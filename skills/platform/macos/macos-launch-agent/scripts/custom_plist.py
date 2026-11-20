#!/usr/bin/env python3
"""自定义配置适配层：读入、校验、加托管外壳；不写文件、不调用 launchctl。

业务字段保留原样；归属、日志和安装事务交给 launch_agent.py。
本模块不是 launchd 全量 schema，也不会把 XML 语法通过当成任务行为正确。
"""
from __future__ import annotations

import calendar
import copy
import json
import plistlib
from pathlib import Path
from typing import Any

from xml_to_plist import MAX_XML_BYTES, RESERVED_LABEL_PREFIX, convert_xml

CUSTOM_PREFIX = RESERVED_LABEL_PREFIX + "custom."
# 这些键属于系统身份/SMAppService，不在当前用户 GUI LaunchAgent 的管理范围。
FORBIDDEN_KEYS = {"UserName", "GroupName", "RootDirectory", "BundleProgram"}
BOOLEAN_KEYS = {"RunAtLoad", "StartOnMount", "EnableTransactions",
                "EnablePressuredExit", "LowPriorityIO", "AbandonProcessGroup"}
INTEGER_KEYS = {"ThrottleInterval", "ExitTimeOut", "TimeOut", "Umask"}


def _text(value: object, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip()):
        raise ValueError(f"{field} 必须为有效字符串，且不能包含 NUL")
    return value


def _absolute(value: object, field: str) -> str:
    text = _text(value, field)
    if not Path(text).is_absolute():
        raise ValueError(f"{field} 必须使用绝对路径；不会自动展开 ~、$HOME 或 shell 表达式")
    return text


def validate_business(payload: dict[str, Any]) -> None:
    """只验证常见字段和用户级边界；未覆盖的合法键保留，需核对目标 Mac 手册。"""
    if "Disabled" in payload:
        raise ValueError("自定义托管任务不得包含 Disabled（无论 true/false）；它属于系统启用状态而不是触发条件。请从 XML 删除此键；禁用或启用请在系统设置或经用户明确同意后使用 launchctl 管理。")
    forbidden = FORBIDDEN_KEYS.intersection(payload)
    if forbidden:
        raise ValueError("当前用户 LaunchAgent 不支持这些身份/系统服务字段：" + ", ".join(sorted(forbidden)))
    program = payload.get("Program")
    argv = payload.get("ProgramArguments")
    if program is None and argv is None:
        raise ValueError("自定义任务必须提供 Program 或非空 ProgramArguments")
    if program is not None:
        _absolute(program, "Program")
    if argv is not None:
        if not isinstance(argv, list) or not argv:
            raise ValueError("ProgramArguments 必须是非空字符串数组")
        for arg in argv:
            _text(arg, "ProgramArguments 参数", empty=True)
        if program is None:
            _absolute(argv[0], "ProgramArguments[0]")
    for key in BOOLEAN_KEYS.intersection(payload):
        if type(payload[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
    for key in INTEGER_KEYS.intersection(payload):
        if type(payload[key]) is not int or payload[key] < 0:
            raise ValueError(f"{key} 必须是非负整数")
    if "StartInterval" in payload:
        value = payload["StartInterval"]
        if type(value) is not int or value <= 0:
            raise ValueError("StartInterval 必须是大于 0 的整数秒数")
    for key in ("WatchPaths", "QueueDirectories"):
        if key in payload:
            if not isinstance(payload[key], list) or not payload[key]:
                raise ValueError(f"{key} 必须是非空路径数组")
            for value in payload[key]:
                _absolute(value, key)
    if "EnvironmentVariables" in payload:
        env = payload["EnvironmentVariables"]
        if not isinstance(env, dict):
            raise ValueError("EnvironmentVariables 必须是字符串字典")
        for key, value in env.items():
            _text(key, "环境变量名")
            if "=" in key:
                raise ValueError("环境变量名不能含等号")
            _text(value, f"环境变量 {key}", empty=True)
    if "WorkingDirectory" in payload:
        _absolute(payload["WorkingDirectory"], "WorkingDirectory")
    keep_alive = payload.get("KeepAlive")
    if "KeepAlive" in payload and type(keep_alive) is not bool and not isinstance(keep_alive, dict):
        raise ValueError("KeepAlive 必须是布尔值或条件字典")
    if isinstance(keep_alive, dict):
        for key, value in keep_alive.items():
            if key in {"SuccessfulExit", "Crashed", "NetworkState"} and type(value) is not bool:
                raise ValueError(f"KeepAlive.{key} 必须是布尔值")
            if key in {"PathState", "OtherJobEnabled", "OtherJobActive"}:
                if not isinstance(value, dict) or any(type(v) is not bool for v in value.values()):
                    raise ValueError(f"KeepAlive.{key} 必须是布尔字典")
                if key == "PathState":
                    for path in value:
                        _absolute(path, "KeepAlive.PathState")
    if "StartCalendarInterval" in payload:
        raw = payload["StartCalendarInterval"]
        items = raw if isinstance(raw, list) else [raw]
        if not items:
            raise ValueError("StartCalendarInterval 不能是空数组")
        limits = {"Minute": (0, 59), "Hour": (0, 23), "Day": (1, 31),
                  "Weekday": (0, 7), "Month": (1, 12)}
        for item in items:
            if not isinstance(item, dict) or set(item) - set(limits):
                raise ValueError("StartCalendarInterval 每一项必须是合法日历字典，不支持 Year/Second")
            for key, value in item.items():
                low, high = limits[key]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"StartCalendarInterval.{key} 必须在 {low}–{high} 范围内")
            if "Month" in item and "Day" in item and item["Day"] > calendar.monthrange(2000, item["Month"])[1]:
                raise ValueError("StartCalendarInterval 包含不存在的月/日组合")
    for key in ("Sockets", "MachServices", "SoftResourceLimits", "HardResourceLimits"):
        if key in payload and not isinstance(payload[key], dict):
            raise ValueError(f"{key} 必须是字典")


def load_source(raw_path: str) -> tuple[dict[str, Any], Path, bytes]:
    """读取导出的 XML/binary plist；拒绝从系统自动加载目录直接接管任务。"""
    path = Path(raw_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("--input 必须是普通 plist 文件")
    if any(parent.name.casefold() in {"launchagents", "launchdaemons"} for parent in path.parents):
        raise ValueError("不能直接导入自动加载目录中的任务；请在普通工作目录准备新配置，不会自动迁移其他工具的任务")
    with path.open("rb") as stream:
        raw = stream.read(MAX_XML_BYTES + 1)
    if not raw or len(raw) > MAX_XML_BYTES:
        raise ValueError("自定义输入不能为空或超过 2 MiB")
    if raw.startswith(b"bplist00"):
        try:
            obj = plistlib.loads(raw)
            # 二进制也走统一 XML 校验（深度、标准类型），不能绕过 XML 入口限制。
            _, obj = convert_xml(plistlib.dumps(obj, sort_keys=False), "xml")
        except (ValueError, TypeError, OverflowError, RecursionError, plistlib.InvalidFileException) as exc:
            raise ValueError("二进制 plist 无法转换为受支持的标准属性列表") from exc
    else:
        _, obj = convert_xml(raw, "xml")
    if not isinstance(obj, dict):
        raise ValueError("自定义 LaunchAgent 必须是字典；普通数组/标量 plist 只能导出，不能安装")
    if "Label" in obj:
        label = _text(obj["Label"], "来源 Label")
        if label.casefold().startswith(RESERVED_LABEL_PREFIX.casefold()):
            raise ValueError("来源 XML 不得使用托管 Label 前缀；请省略 Label，由安装器分配")
    validate_business(obj)
    return obj, path, raw


def prepare_payload(source: dict[str, Any], label: str, logs: Path, default_workdir: Path) -> dict[str, Any]:
    """只补管理外壳，不改用户参数、触发条件和环境变量；冲突日志/掩码显式报错。"""
    obj = copy.deepcopy(source)
    obj["Label"] = label
    for key, suffix in (("StandardOutPath", "stdout"), ("StandardErrorPath", "stderr")):
        expected = str(logs / f"{label}.{suffix}.log")
        if key in obj and obj[key] != expected:
            raise ValueError(f"{key} 由管理器统一分配；请从源 XML 删除该键，应用自身日志可由业务参数配置")
        obj[key] = expected
    if "Umask" in obj and obj["Umask"] != 0o077:
        raise ValueError("托管任务固定使用 Umask=63（八进制 077）；请删除冲突值")
    obj["Umask"] = 0o077
    working = Path(obj.get("WorkingDirectory", str(default_workdir)))
    if not working.is_absolute():
        raise ValueError("WorkingDirectory 必须是绝对路径")
    working = working.resolve(strict=True)
    if not working.is_dir():
        raise ValueError("WorkingDirectory 必须是已存在的目录")
    obj["WorkingDirectory"] = str(working)
    validate_managed_payload(obj, label, logs)
    return obj


def validate_managed_payload(obj: dict[str, Any], label: str, logs: Path) -> None:
    """用于后续查询、卸载、修复：目录可已被业务删除，但管理外壳仍须一致。"""
    if not label.startswith(CUSTOM_PREFIX) or obj.get("Label") != label:
        raise ValueError("自定义托管 Label 不匹配")
    validate_business(obj)
    _absolute(obj.get("WorkingDirectory"), "WorkingDirectory")
    if type(obj.get("Umask")) is not int or obj["Umask"] != 0o077:
        raise ValueError("托管自定义配置的 Umask 外壳不一致")
    for key, suffix in (("StandardOutPath", "stdout"), ("StandardErrorPath", "stderr")):
        if obj.get(key) != str(logs / f"{label}.{suffix}.log"):
            raise ValueError(f"托管自定义配置的 {key} 外壳不一致")


def custom_summary(obj: dict[str, Any]) -> str:
    """展示真实触发键，不把任意 XML 错译成固定日历任务。"""
    keys = ("RunAtLoad", "KeepAlive", "StartInterval", "StartCalendarInterval",
            "WatchPaths", "QueueDirectories", "StartOnMount", "Sockets", "MachServices")
    selected = {key: obj[key] for key in keys if key in obj}
    return json.dumps(selected, ensure_ascii=False, sort_keys=True, default=lambda value: f"<{type(value).__name__}>") if selected else "未设置自动触发键；需显式运行或按需触发"