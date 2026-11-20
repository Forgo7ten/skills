#!/usr/bin/env python3
"""macOS 用户级 LaunchAgent 统一管理器，仅依赖 Python 标准库。

固定模板和自定义 XML 只负责生成配置，共用归属校验、事务、快照及生命周期。
"""
from __future__ import annotations

import argparse
import calendar as calendar_module
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NamedTuple
from xml.parsers.expat import ExpatError

from custom_plist import (CUSTOM_PREFIX, load_source, prepare_payload,
                          validate_managed_payload, custom_summary)

PREFIX = "local.my.agents."
LAUNCH_AGENTS_DIRECTORY = Path.home() / "Library" / "LaunchAgents"
LOG_DIRECTORY = Path.home() / "Library" / "Logs" / "LaunchAgents"
DEFAULT_WORKING_DIRECTORY = Path.home()
METADATA_DIRECTORY = Path.home() / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
LOCK_FILENAME = ".local.my.agents.lock"
# 只依据当前的字段结构校验管理记录，不使用格式版本号。
METADATA_REQUIRED_FIELDS = frozenset({
    "label", "description", "mode", "source_type", "source_file", "plist_path",
    "created_at", "updated_at", "disk_sha256", "last_registered_sha256",
    "last_registered_at", "registration_snapshot_unknown", "registration_snapshot_unknown_reason",
})
METADATA_ALLOWED_FIELDS = METADATA_REQUIRED_FIELDS | frozenset({
    "source_sha256", "source_label", "task_name",
})
UMASK = 0o077  # plistlib 会将该权限掩码序列化为十进制 63，而不是八进制字符串。


class InvalidMetadataError(RuntimeError):
    """权限检查通过，但 JSON 内容或字段不符合当前记录结构。"""


class UnexpectedMetadataFieldsError(InvalidMetadataError):
    """记录含当前结构不允许的字段，不能借重建动作覆盖。"""


class UnparseablePlistError(RuntimeError):
    """清理候选 plist 已无法解码，不能验证内部 Label。"""


def require_macos() -> None:
    """只允许在目标 macOS 用户身份下管理 LaunchAgent。"""
    if sys.platform != "darwin":
        raise RuntimeError("安装、查询和卸载需要在 macOS 上执行；其他系统仅支持 --dry-run 预览")
    if os.geteuid() == 0:
        raise RuntimeError("请勿使用 sudo 或 root；请以需要运行该任务的 macOS 用户身份执行")

def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return (re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")[:36].strip("-") or "job")


def make_label(command: str, name: str | None, mode: str = "login") -> str:
    """生成稳定且按模式隔离的 Label。"""
    if mode not in {"login", "calendar", "custom"}:
        raise ValueError("任务模式只能是 login、calendar 或 custom")
    if mode == "custom" and (name is None or not name.strip()):
        raise ValueError("自定义托管任务必须提供稳定的 --name")
    if name is not None and name.strip():
        source = name.strip()
        # 登录/日历/自定义模式各自独立，同名不会互相 bootout 或覆盖。
        identity = {"login": "name:", "calendar": "calendar-name:", "custom": "custom-name:"}[mode] + source.casefold()
    else:
        try:
            parts = shlex.split(command, posix=True)
        except ValueError:
            parts = command.strip().split(maxsplit=1)
        source = Path(parts[0]).name if parts else "job"
        identity = ("calendar-command:" if mode == "calendar" else "command:") + command
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"{CUSTOM_PREFIX if mode == 'custom' else PREFIX}{slugify(source)}-{digest}"


def validate_label(label: str) -> None:
    if not re.fullmatch(re.escape(PREFIX) + r"(?:custom\.)?[a-z0-9-]+-[0-9a-f]{8}", label):
        raise ValueError("LaunchAgent 标识无效；请使用安装或 list 命令返回的完整 Label")


def payload_mode(obj: dict[str, Any]) -> str:
    if str(obj.get("Label", "")).startswith(CUSTOM_PREFIX):
        return "custom"
    return "calendar" if "StartCalendarInterval" in obj else "login"


def mode_text(mode: str) -> str:
    return {"login": "登录自启动", "calendar": "日历定时", "custom": "自定义 XML"}[mode]


def restoration_warning(data: bytes) -> str:
    if payload_mode(plistlib.loads(data)) == "custom":
        return "警告：重新 bootstrap 自定义任务可能因 XML 中的启动或触发条件再次执行命令；无法撤销已发生的业务副作用。"
    return "警告：重新 bootstrap 旧登录任务由于 RunAtLoad=true，可能再次执行旧命令。"



def get_command(args: argparse.Namespace) -> str:
    if args.script is not None:
        script = Path(args.script).expanduser()
        if not script.is_absolute():
            raise ValueError("--script 必须使用绝对路径或 ~/ 开头的路径")
        try:
            script = script.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"脚本文件不存在：{script}") from exc
        if not script.is_file():
            raise ValueError(f"--script 必须指向普通文件：{script}")
        if not os.access(script, os.X_OK):
            raise ValueError(f"脚本没有可执行权限；如需解释器，请提供完整解释器命令：{script}")
        with script.open("rb") as stream:
            first_line = stream.readline(4096)
            if not first_line.startswith(b"#!") or not first_line[2:].strip():
                raise ValueError(f"脚本缺少有效的 shebang；如需解释器，请提供完整解释器命令：{script}")
        args.resolved_script = str(script)
        return shlex.quote(str(script))
    if args.command_stdin:
        command = sys.stdin.read()
        # heredoc 通常会附带最后一个换行，只去掉这个分隔符。
        if command.endswith("\n"):
            command = command[:-1]
    else:
        command = args.command
    if not isinstance(command, str) or not command.strip():
        raise ValueError("必须提供非空命令")
    if "\x00" in command:
        raise ValueError("命令不能包含 NUL 空字节")
    return command


def get_working_directory(value: str | None) -> Path:
    if value is None:
        path = DEFAULT_WORKING_DIRECTORY
    else:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("--working-directory 必须使用绝对路径或 ~/ 开头的路径")
    try:
        path = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(f"工作目录不存在：{path}") from exc
    if not path.is_dir():
        raise ValueError(f"工作目录不是目录：{path}")
    return path


def validate_description(value: str | None, *, hint: str = "首次安装") -> str:
    """校验并规范化用户可读的任务用途说明。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{hint}必须提供 --description，简要说明该任务的用途")
    description = " ".join(value.split())
    if len(description) > 500:
        raise ValueError("--description 最长为 500 个字符")
    if "\x00" in description:
        raise ValueError("任务描述不能包含 NUL 空字节")
    return description


def metadata_path(label: str) -> Path:
    """返回本 Skill 专用的任务元数据路径。"""
    validate_label(label)
    return METADATA_DIRECTORY / f"{label}.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _inspect_metadata_checked(label: str) -> tuple[bytes | None, dict[str, Any] | None]:
    """安全读取任务说明和注册快照；不接受符号链接或其他用户的文件。"""
    path = metadata_path(label)
    if METADATA_DIRECTORY.is_symlink():
        raise RuntimeError(f"拒绝使用符号链接元数据目录：{METADATA_DIRECTORY}")
    private_root = METADATA_DIRECTORY.parent
    if private_root.is_symlink():
        raise RuntimeError(f"拒绝使用符号链接管理目录：{private_root}")
    if private_root.exists():
        root_info = private_root.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
                or root_info.st_mode & 0o077):
            raise RuntimeError(f"管理目录所有权或权限不安全（需要 0700）：{private_root}")
    if METADATA_DIRECTORY.exists():
        directory_info = METADATA_DIRECTORY.lstat()
        if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != os.getuid()
                or directory_info.st_mode & 0o077):
            raise RuntimeError(f"元数据目录权限或所有权不安全：{METADATA_DIRECTORY}")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"拒绝读取不安全或不属于当前用户的任务元数据：{path}")
    raw = path.read_bytes()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidMetadataError(f"任务元数据格式损坏（{type(exc).__name__}: {exc}）") from exc
    if not isinstance(obj, dict) or obj.get("label") != label:
        raise InvalidMetadataError(f"任务元数据结构或 Label 不匹配：{path}")
    if missing := METADATA_REQUIRED_FIELDS - obj.keys():
        raise InvalidMetadataError(f"任务元数据缺少当前格式的必需字段：{', '.join(sorted(missing))}（{path}）")
    if extra := obj.keys() - METADATA_ALLOWED_FIELDS:
        raise UnexpectedMetadataFieldsError(f"任务元数据包含未定义字段：{', '.join(sorted(extra))}（{path}）")
    if not isinstance(obj.get("description"), str) or not obj["description"].strip():
        raise InvalidMetadataError(f"任务元数据缺少有效描述：{path}")
    if obj.get("mode") not in {"login", "calendar", "custom"}:
        raise InvalidMetadataError(f"任务元数据中的模式无效：{path}")
    if obj.get("source_type") not in {"command", "script", "plist"}:
        raise InvalidMetadataError(f"任务元数据中的来源类型无效：{path}")
    if type(obj["registration_snapshot_unknown"]) is not bool:
        raise InvalidMetadataError(f"任务元数据中的注册快照状态无效：{path}")
    reason = obj["registration_snapshot_unknown_reason"]
    known_reasons = {"rebuilt", "accepted-plist", "mode-changed", "recovery-failed"}
    if (reason is not None and not isinstance(reason, str)):
        raise InvalidMetadataError(f"任务元数据中的注册快照未知原因无效：{path}")
    if ((obj["registration_snapshot_unknown"] and reason not in known_reasons)
            or (not obj["registration_snapshot_unknown"] and reason is not None)):
        raise InvalidMetadataError(f"任务元数据中的注册快照未知原因与状态不一致：{path}")
    if obj.get("plist_path") != str(LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"):
        raise InvalidMetadataError(f"任务元数据中的 plist 路径不匹配：{path}")
    for key in ("disk_sha256", "last_registered_sha256"):
        value = obj[key]
        if (value is None and key == "disk_sha256") or (value is not None and
                (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value))):
            raise InvalidMetadataError(f"任务元数据中的 {key} 无效：{path}")
    if ((obj["last_registered_sha256"] is None) != (obj["last_registered_at"] is None)):
        raise InvalidMetadataError(f"任务元数据注册哈希与时间必须同时存在或同时为空：{path}")
    for key in ("created_at", "updated_at"):
        if not isinstance(obj[key], str) or not obj[key]:
            raise InvalidMetadataError(f"任务元数据中的 {key} 无效：{path}")
    if obj["last_registered_at"] is not None and not isinstance(obj["last_registered_at"], str):
        raise InvalidMetadataError(f"任务元数据中的 last_registered_at 无效：{path}")
    source_file = obj.get("source_file")
    if source_file is not None and (not isinstance(source_file, str) or not Path(source_file).is_absolute()):
        raise InvalidMetadataError(f"任务元数据中的源文件路径无效：{path}")
    if (label.startswith(CUSTOM_PREFIX)) != (obj["mode"] == "custom"):
        raise InvalidMetadataError(f"管理记录模式与 Label 命名空间不一致：{path}")
    if (obj["mode"] == "custom") != (obj["source_type"] == "plist"):
        raise InvalidMetadataError(f"任务模式与来源类型不一致：{path}")
    source_hash = obj.get("source_sha256")
    if source_hash is not None and (not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash)):
        raise InvalidMetadataError(f"管理记录的来源哈希无效：{path}")
    for key in ("source_label", "task_name"):
        if obj.get(key) is not None and not isinstance(obj[key], str):
            raise InvalidMetadataError(f"管理记录的 {key} 必须是字符串：{path}")
    return raw, obj


def path_entry_kind(path: Path) -> str | None:
    """lstat 判断路径类型，绝不跟随符号链接；缺失时返回 None。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISREG(info.st_mode):
        return "普通文件"
    if stat.S_ISDIR(info.st_mode):
        return "目录"
    if stat.S_ISLNK(info.st_mode):
        return "符号链接"
    return "其他"


def state_recovery_hint(label: str, state: str, *, path: Path | None = None) -> str:
    """统一提供按当前状态可行的恢复出口；不修改配置或服务。

    没有可验证归属的文件只允许人工核对；绝不能凭前缀自动接管。
    """
    label_arg = shlex.quote(label)
    plist_path = path or (LAUNCH_AGENTS_DIRECTORY / f"{label}.plist")
    if state in {"record_ok_plist_missing", "record_corrupt_plist_missing"}:
        if state == "record_corrupt_plist_missing":
            return ("管理记录内容已损坏，且 plist 缺失。请先人工核对该 Label 的来源；"
                    "若仍有同 Label 服务加载，损坏记录不能证明其归属，必须先人工核实并停止。"
                    "确认已停止后，执行 "
                    f"repair --label {label_arg} --action remove-orphan --confirm-stop "
                    "只清理安全的当前用户管理记录，不接管同名服务。")
        return ("管理记录仍在，但 plist 缺失。确认不再需要此 Label 的服务后，执行 "
                f"repair --label {label_arg} --action remove-orphan --confirm-stop "
                "清理孤立记录；操作前将确认加载状态，必要时停止已确认的托管服务。")
    if state == "record_corrupt_plist_ok":
        return ("管理记录内容损坏但磁盘 plist 仍符合当前托管结构。"
                "核对来源与用途后，执行 "
                f"repair --label {label_arg} --action rebuild-record "
                "--trust-current-plist --description '任务用途说明'；"
                "重建不会自动注册或执行任务。")
    if state == "record_corrupt_plist_unusable":
        return ("管理记录与 plist 均不能用于安全重建；禁止自动接管。"
                "先人工核对来源、所有权及服务状态；若确认应清理，按需人工停止同名服务，"
                f"确认停止后仅手动删除已核实的 {shlex.quote(str(plist_path))}，"
                f"再执行 repair --label {label_arg} --action remove-orphan --confirm-stop "
                "清理损坏的孤立记录；不要只删除 JSON 或让本工具接管未知 plist。")
    if state == "plist_unparseable":
        return ("plist 已无法解析，不能校验内部 Label，禁止自动删除。"
                "请在目标 Mac 先人工确认文件归属并核对 "
                f"launchctl print '{service_target(label)}'；若仍加载且确认可停止，再人工执行 "
                f"launchctl bootout '{service_target(label)}'。确认服务已经停止后，"
                f"仅手动删除已核实的 plist 文件 {shlex.quote(str(plist_path))}，"
                f"随后执行 repair --label {label_arg} --action remove-orphan --confirm-stop "
                "清理剩余管理记录。")
    if state == "plist_without_record":
        kind = path_entry_kind(plist_path)
        if kind is None:
            return ("该路径已经不存在；无法凭文件名确认任何服务归属，请重新查询并核对。")
        if kind != "普通文件":
            return (f"这不是可作为 plist 读取的普通文件，而是{kind}；"
                    "本工具不会读取链接目标、查询或操作同名服务。"
                    "请人工确认该路径的类型和来源，"
                    "不要按普通 plist 的清理步骤操作。")
        return ("这个文件只有托管命名样式，缺少有效管理记录；本工具不会接管、停止或删除它。"
                f"请人工核实 {shlex.quote(str(plist_path))} 的来源，"
                f"查看 launchctl print '{service_target(label)}'；"
                "确认确属废弃服务且仍加载时，先人工执行 "
                f"launchctl bootout '{service_target(label)}' 并确认已停止，再手动移除该文件，"
                "之后才能以同一 Label 安装新任务。")
    raise ValueError("未知管理恢复状态")


def metadata_corruption_hint(label: str, *, can_rebuild: bool = True) -> str:
    """基于磁盘 plist 现状区分损坏记录可重建还是应清理。"""
    path = LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"
    if not path.exists() and not path.is_symlink():
        return state_recovery_hint(label, "record_corrupt_plist_missing")
    if not can_rebuild:
        return ("记录包含不被当前结构允许的字段，拒绝自动重建或迁移；"
                "请人工核实来源与字段内容。")
    try:
        data = inspect_plist(path, label)
    except (RuntimeError, OSError):
        return state_recovery_hint(label, "record_corrupt_plist_unusable")
    return state_recovery_hint(label, "record_corrupt_plist_ok" if data is not None
                               else "record_corrupt_plist_missing")


def inspect_metadata(label: str) -> tuple[bytes | None, dict[str, Any] | None]:
    """单一读取入口：权限错误不建议修复，内容损坏按磁盘状态给出口。"""
    try:
        return _inspect_metadata_checked(label)
    except InvalidMetadataError as exc:
        path = metadata_path(label)
        prefix = f"记录路径：{path}；" if str(path) not in str(exc) else ""
        hint = metadata_corruption_hint(label, can_rebuild=not isinstance(exc, UnexpectedMetadataFieldsError))
        raise InvalidMetadataError(f"{prefix}{exc}；恢复建议：{hint}") from exc


def metadata_bytes(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_metadata(label: str, obj: dict[str, Any]) -> None:
    ensure_private_metadata_directory()
    atomic_write(metadata_path(label), metadata_bytes(obj))


def ensure_private_metadata_directory() -> None:
    """将本工具专属目录及其父目录都限制为当前用户可访问。"""
    # 不更改通用的 ~/Library/Application Support 权限，只保护本工具创建的目录。
    ensure_directory(METADATA_DIRECTORY.parent, private=True)
    ensure_directory(METADATA_DIRECTORY, private=True)


def make_metadata(
    label: str,
    plist_path: Path,
    data: bytes,
    description: str,
    mode: str,
    source_file: str | None,
    old: dict[str, Any] | None,
    registered_now: bool,
) -> dict[str, Any]:
    """建立元数据，并保留尚未重新注册的旧快照以便检测潜在漂移。"""
    now = timestamp_now()
    previous = old or {}
    last_registered_hash = previous.get("last_registered_sha256")
    last_registered_at = previous.get("last_registered_at")
    if registered_now:
        last_registered_hash = sha256_bytes(data)
        last_registered_at = now
    return {
        "label": label,
        "description": description,
        "mode": mode,
        "source_type": "plist" if mode == "custom" else "script" if source_file else "command",
        "source_file": source_file,
        "plist_path": str(plist_path),
        "created_at": previous.get("created_at", now),
        "updated_at": now,
        "disk_sha256": sha256_bytes(data),
        "last_registered_sha256": last_registered_hash,
        "last_registered_at": last_registered_at,
        # 重建损坏记录时无法证明当前 launchd 内存状态；直到显式重新注册前保持未知。
        "registration_snapshot_unknown": False if registered_now else bool(previous.get("registration_snapshot_unknown", False)),
        "registration_snapshot_unknown_reason": None if registered_now else previous.get("registration_snapshot_unknown_reason"),
    }


def registration_unknown_text(metadata: dict[str, Any]) -> str:
    """使用当前格式记录的明确原因解释未知快照。"""
    reason = metadata.get("registration_snapshot_unknown_reason")
    messages = {
        "rebuilt": "注册快照未知：管理记录已重建但尚未重新注册",
        "accepted-plist": "注册快照未知：已接受外部改动但尚未重新注册",
        "mode-changed": "注册快照未知：任务模式已改变但尚未重新注册",
        "recovery-failed": "注册快照未知：回滚无法确认原服务状态，请核实后重新注册或卸载",
    }
    return messages[reason]


def registration_sync_text(data: bytes, metadata: dict[str, Any], state: str) -> str:
    """根据本地哈希快照提示同步情况；不能直接读取 launchd 内存中的配置。"""
    disk_hash = sha256_bytes(data)
    baseline_hash = metadata.get("disk_sha256")
    registered_hash = metadata.get("last_registered_sha256")
    if disk_hash != baseline_hash:
        disk_note = "磁盘 plist 与本工具管理记录不符；禁止修改、执行或卸载"
    else:
        disk_note = "磁盘 plist 与本工具记录一致"
    if metadata.get("registration_snapshot_unknown"):
        registered_note = registration_unknown_text(metadata) + "；需确认后重新注册或显式复位为未注册基线"
    elif registered_hash is None:
        registered_note = "没有由本工具维护的注册快照"
    elif disk_hash != registered_hash:
        registered_note = "疑似漂移：磁盘配置与上次注册快照不同；当前会话可能仍使用旧配置"
    else:
        registered_note = "磁盘配置与上次注册快照一致"
    state_notes = {"loaded": "已加载", "absent": "未加载", "unknown": "无法确认加载状态"}
    return f"{state_notes[state]}；{disk_note}；{registered_note}；快照不等于 launchd 内存的实际配置"

# launchd 星期编号：0 表示周日，1–6 表示周一至周六。
WEEKDAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
CALENDAR_KEYS = {"Hour", "Minute", "Weekday", "Day", "Month"}


def parse_times(raw_values: list[str] | None) -> list[tuple[int, int]]:
    """解析可重复或逗号分隔的 HH:MM，规范化并去重。"""
    if not raw_values:
        return [(9, 0)]  # 显式选择日历定时但未指定时间时，默认每天 09:00。
    times: set[tuple[int, int]] = set()
    for raw in raw_values:
        for index, part in enumerate(raw.split(","), 1):
            value = part.strip()
            if not value:
                raise ValueError(f"--time 的逗号列表中存在空项（第 {index} 项，原始值 {raw!r}）")
            match = re.fullmatch(r"([0-9]{1,2}):([0-9]{1,2})", value)
            if match is None:
                raise ValueError(f"--time 时间格式无效：{value}（应为 HH:MM，范围 00:00–23:59）")
            hour, minute = int(match.group(1)), int(match.group(2))
            if hour > 23 or minute > 59:
                raise ValueError(f"--time 超出有效范围：{value}（00:00–23:59）")
            times.add((hour, minute))
    return sorted(times)


def parse_weekdays(raw_values: list[str] | None) -> list[int]:
    weekdays: set[int] = set()
    for raw in raw_values or []:
        for index, part in enumerate(raw.split(","), 1):
            value = part.strip().lower()
            if not value:
                raise ValueError(f"--weekday 的逗号列表中存在空项（第 {index} 项，原始值 {raw!r}）")
            if value in WEEKDAY_NAMES:
                day = WEEKDAY_NAMES[value]
            elif value in {str(n) for n in range(8)}:
                day = int(value) % 7
            else:
                raise ValueError(f"--weekday 无效：{part.strip()}（请使用 sun..sat 或 0..7）")
            weekdays.add(day)
    return sorted(weekdays)


def validate_calendar_item(item: Any) -> dict[str, int]:
    """验证固定日历字段；不接受模板以外的 plist 字段。"""
    if not isinstance(item, dict) or not {"Hour", "Minute"}.issubset(item) or set(item) - CALENDAR_KEYS:
        raise ValueError("日历条目必须包含 Hour/Minute，且只能使用支持的字段")
    bounds = {"Hour": (0, 23), "Minute": (0, 59), "Weekday": (0, 6), "Day": (1, 31), "Month": (1, 12)}
    for key, value in item.items():
        lo, hi = bounds[key]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"日历字段 {key} 的值无效：{value!r}")
    if "Day" in item and "Weekday" in item:
        raise ValueError("不能同时设置 Day 和 Weekday（launchd 会按 OR 语义匹配）")
    if "Month" in item and "Day" not in item:
        raise ValueError("本固定模板要求 Month 必须与 Day 同时设置")
    if "Month" in item:
        # 以闰年 2024 验证月日组合：允许 2 月 29 日，拒绝日历上不存在的日期。
        max_day = calendar_module.monthrange(2024, item["Month"])[1]
        if item["Day"] > max_day:
            raise ValueError("所选月份不存在该日期：" +
                             f"{item['Month']:02d}-{item['Day']:02d}")
    return {key: item[key] for key in ("Month", "Day", "Weekday", "Hour", "Minute") if key in item}


def canonical_calendar(items: list[dict[str, int]]) -> list[dict[str, int]]:
    if not items:
        raise ValueError("定时计划不能为空")
    valid = [validate_calendar_item(item) for item in items]
    result = sorted(valid, key=lambda d: (d.get("Month", 0), d.get("Day", 0), d.get("Weekday", -1), d["Hour"], d["Minute"]))
    if any(a == b for a, b in zip(result, result[1:])):
        raise ValueError("日历计划中存在重复条目")
    return result


def make_calendar(args: argparse.Namespace) -> list[dict[str, int]] | None:
    options_used = bool(args.time or args.weekday or args.month_day is not None or args.month is not None)
    mode = args.mode or ("calendar" if options_used else "login")
    if mode == "login":
        if options_used:
            raise ValueError("日历参数必须配合 --mode calendar 使用")
        if args.defer:
            raise ValueError("--defer 仅适用于日历定时任务")
        return None
    if args.keep_alive:
        raise ValueError("定时任务不能使用 --keep-alive")
    if args.month is not None and args.month_day is None:
        raise ValueError("--month 必须同时提供 --month-day")
    if args.month_day is not None and args.weekday:
        raise ValueError("--month-day 与 --weekday 不能同时使用")
    if args.month_day is not None and not 1 <= args.month_day <= 31:
        raise ValueError("--month-day 必须为 1..31")
    if args.month is not None and not 1 <= args.month <= 12:
        raise ValueError("--month 必须为 1..12")
    entries = []
    for hour, minute in parse_times(args.time):
        extras: list[dict[str, int]]
        if args.weekday:
            extras = [{"Weekday": day} for day in parse_weekdays(args.weekday)]
        elif args.month_day is not None:
            extras = [{**({"Month": args.month} if args.month is not None else {}), "Day": args.month_day}]
        else:
            extras = [{}]
        for extra in extras:
            entries.append({**extra, "Hour": hour, "Minute": minute})
    return canonical_calendar(entries)


def calendar_text(items: list[dict[str, int]]) -> str:
    """将固定的日历条目转换为中文摘要。"""
    output: list[str] = []
    day_names = {v: k for k, v in WEEKDAY_NAMES.items()}
    zh_days = {"sun": "日", "mon": "一", "tue": "二", "wed": "三", "thu": "四", "fri": "五", "sat": "六"}
    for item in items:
        when = f"{item['Hour']:02d}:{item['Minute']:02d}"
        if "Weekday" in item:
            when = f"每周{zh_days[day_names[item['Weekday']]]} {when}"
        elif "Day" in item:
            when = f"每月 {item['Day']} 日 {when}" if "Month" not in item else f"每年 {item['Month']} 月 {item['Day']} 日 {when}"
        else:
            when = f"每天 {when}"
        output.append(when)
    return "、".join(output)

def build_plist(
    label: str, command: str, working_directory: Path, keep_alive: bool,
    calendar: list[dict[str, int]] | None = None,
) -> dict[str, Any]:
    """唯一支持的 plist 模板：登录自启动或日历定时；不接受任意字段。"""
    if calendar is not None:
        if keep_alive:
            raise ValueError("定时任务不能启用 KeepAlive")
        calendar = canonical_calendar(calendar)
    result = {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-lc", command],
        "RunAtLoad": calendar is None,
        "KeepAlive": keep_alive,
        "WorkingDirectory": str(working_directory),
        "StandardOutPath": str(LOG_DIRECTORY / f"{label}.stdout.log"),
        "StandardErrorPath": str(LOG_DIRECTORY / f"{label}.stderr.log"),
        "Umask": UMASK,
    }
    if calendar is not None:
        result["StartCalendarInterval"] = calendar[0] if len(calendar) == 1 else calendar
    return result


def to_plist_bytes(payload: dict[str, Any]) -> bytes:
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    # XML 1.0 会规范化原始回车符；不能静默改变用户命令。
    if plistlib.loads(data) != payload:
        raise ValueError("plist XML 序列化会改变用户提供的命令或路径；为避免数据丢失，已拒绝写入")
    return data


def plutil_lint(path: Path) -> None:
    """在修改 launchctl 状态前，先确认 macOS 接受磁盘上的 plist。"""
    if sys.platform != "darwin":  # 非 macOS 离线测试使用 plistlib 与模拟 launchctl。
        return
    result = subprocess.run(["/usr/bin/plutil", "-lint", str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError("macOS plist 格式检查失败：" + translate_system_detail(details))


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["/bin/launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"launchctl {' '.join(args)} 执行失败（退出码 {result.returncode}）"
            + (f"：{translate_system_detail(details)}" if details else "")
        )
    return result


def translate_system_detail(details: str) -> str:
    """不要把 launchctl/plutil 的英文 stderr 原样暴露给用户。"""
    text = details.strip()
    lower = text.casefold()
    if not text:
        return "系统命令未提供详细原因"
    if "could not find service" in lower or "no such process" in lower:
        return "未找到指定服务或进程"
    if "operation not permitted" in lower or "not permitted" in lower:
        return "操作未获系统授权；请确认当前用户与图形登录会话"
    if "permission denied" in lower or "not authorized" in lower:
        return "权限不足；请确认当前用户是否有权管理此用户级任务"
    if "already loaded" in lower or "service is already loaded" in lower:
        return "服务已经加载"
    if "input/output error" in lower:
        return "系统返回输入/输出错误"
    # launchctl 的文本不是稳定 API，未知英文内容不原样转发。
    return "系统命令返回了未识别的诊断信息（原始输出已省略）"


def domain_target() -> str:
    return f"gui/{os.getuid()}"


def service_target(label: str) -> str:
    return f"{domain_target()}/{label}"


def disabled_override_status(label: str) -> tuple[str, str]:
    """只保守解析 launchctl print-disabled 中对应 Label 的显式覆盖。

    print-disabled 是诊断性文本而非稳定 API；无法识别时不猜测为 enabled。
    """
    try:
        result = launchctl("print-disabled", domain_target(), check=False)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return "unknown", "无法执行系统禁用覆盖查询"
    if result.returncode != 0:
        return "unknown", f"系统禁用覆盖查询失败（退出码 {result.returncode}）"
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    escaped = re.escape(label)
    match = re.search(r"^\s*[\"']?" + escaped + r"[\"']?\s*=>\s*(enabled|disabled)\s*$",
                      output, re.MULTILINE | re.IGNORECASE)
    if match:
        return ("disabled" if match.group(1).lower() == "disabled" else "enabled"), ""
    if re.search(r"\bdisabled services\s*=\s*\{", output, re.IGNORECASE):
        return "not-listed", ""
    return "unknown", "系统返回的禁用列表格式无法可靠识别"


def disabled_service_guidance(label: str, *, mode: str | None = None,
                              operation: str = "status") -> str:
    """按当前调用动作给出建议；持久覆盖属于 Label，不证明 plist 存在。"""
    message = (
        f"Label {label} 存在跨重启的持久禁用覆盖（即使任务已卸载也可能保留）；本工具不会自行 enable。用户明确同意后，可在目标 Mac 执行 "
        f"launchctl enable '{service_target(label)}'"
        "，或在系统设置 → 通用 → 登录项与扩展中打开对应项目，再重新安装或注册。enable 会跨重启改变持久启用状态。"
    )
    if operation == "register":
        message += "当前命令 register 必然尝试注册，没有只写盘选项；"
        if mode == "calendar":
            message += "若只需更新日历配置，请另用 install --replace --defer；--defer 不适用于 register。"
        elif mode == "login":
            message += "若只需更新登录任务的磁盘配置，请另用 install（不加 --start-now）。"
        elif mode == "custom":
            message += "若只需更新自定义任务的磁盘配置，请另用 install-plist --replace（不加 --activate）。"
    elif operation in {"install", "install-plist", "status"}:
        if mode == "calendar":
            message += "日历任务仅写盘可用 install --defer（更新时还须 --replace）；register 不支持 --defer。"
        elif mode == "login":
            message += "登录任务用 install（不加 --start-now）仅写盘；register 不支持 --start-now。"
        elif mode == "custom":
            message += "自定义任务用 install-plist（不加 --activate）仅写盘；register 不支持 --activate。"
    else:
        raise ValueError("禁用状态指引的调用场景无效")
    return message


def bootstrap_failure_guidance(label: str, error: Exception, *, mode: str | None = None,
                               operation: str = "install") -> str:
    """仅当 bootstrap 报泛用 EIO 时检查持久覆盖，避免猜测是 XML 错误。"""
    message = str(error)
    if "退出码 5" not in message and "输入/输出错误" not in message:
        return ""
    state, detail = disabled_override_status(label)
    if state == "disabled":
        return "；诊断：" + disabled_service_guidance(label, mode=mode, operation=operation)
    if state == "unknown":
        return ("；诊断：系统禁用覆盖状态无法确认（" + detail + "）；"
                f"可手动执行 launchctl print-disabled '{domain_target()}' 核对，"
                "并检查 plist 字段和系统日志；EIO 本身不能证明任务已禁用")
    return ("；诊断：未发现此 Label 的 disabled 覆盖；"
            "EIO 也可能由系统拒绝配置或其他原因引起，请检查系统日志和 launchd 配置")


def disabled_uninstall_notice(label: str, mode: str) -> str:
    """卸载后只说明覆盖残留和正确的同名重装选项，不误报任务仍存在。"""
    methods = {
        "calendar": "若同名日历任务重装时只想写盘，请在 install 中加 --defer；默认安装会尝试注册。",
        "login": "同名登录任务用 install 重装时不加 --start-now 即只写盘。",
        "custom": "同名自定义任务用 install-plist 重装时不加 --activate 即只写盘。",
    }
    return (
        "注意：该 Label 仍存在持久禁用覆盖（不代表 plist 或管理记录残留），"
        "卸载不自动清除此跨重启状态。" + methods[mode] +
        "再次注册或激活前，须经用户明确同意后执行 "
        f"launchctl enable '{service_target(label)}'，或在系统设置中重新打开对应项目。"
    )

def probe_service(label: str) -> tuple[str, str]:
    """保守判断服务状态：已加载、明确未加载或无法确认。"""
    try:
        result = launchctl("print", service_target(label), check=False)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return "unknown", "无法执行系统服务状态查询"
    if result.returncode == 0:
        return "loaded", ""
    details = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
    # 必须匹配明确的“服务不存在”响应；任意非零返回码都不能当成“未加载”。
    # launchctl 文本不是稳定 API，无法识别时必须保守返回“无法确认”。
    absent_pattern = r"Could not find service\s+[\"\']?" + re.escape(label) + r"[\"\']?\s+in domain"
    if result.returncode == 113 and re.search(absent_pattern, details, re.IGNORECASE):
        try:
            gui = launchctl("print", domain_target(), check=False)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return "unknown", "无法查询当前图形登录域"
        if gui.returncode == 0:
            return "absent", ""
        return "unknown", f"图形登录域查询失败（退出码 {gui.returncode}）：{translate_system_detail(gui.stderr or gui.stdout or '')}"
    return "unknown", f"launchctl 状态查询返回退出码 {result.returncode}：{translate_system_detail(details)}"


def require_known_service_state(label: str) -> bool:
    """在停止、重载或触发任务前，拒绝无法确认的服务状态。"""
    state, details = probe_service(label)
    if state == "unknown":
        raise RuntimeError(f"无法确认服务 {label} 的加载状态；已拒绝操作。{details}")
    return state == "loaded"

def unsafe_directory_link_message(path: Path) -> str:
    if path == LAUNCH_AGENTS_DIRECTORY:
        return (f"拒绝使用符号链接目录：{path}；本 Skill 不支持 "
                "~/Library/LaunchAgents 为符号链接。请人工核对原目录和链接目标，"
                "改用当前用户所有的实体目录（建议权限 0700，不允许群组或其他用户写入）后重试；"
                "本工具不会自动修改此路径。")
    return f"拒绝使用符号链接目录：{path}"


def ensure_directory(path: Path, private: bool = False) -> None:
    if path.is_symlink():
        raise RuntimeError(unsafe_directory_link_message(path))
    path.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if path.is_symlink():
        raise RuntimeError(unsafe_directory_link_message(path))
    if not path.is_dir():
        raise RuntimeError(f"目录不安全：{path}")
    info = path.stat()
    if info.st_uid != os.getuid():
        raise RuntimeError(f"目录不属于当前用户：{path}")
    if info.st_mode & 0o022:
        raise RuntimeError(f"目录允许群组或其他用户写入：{path}")
    if private:
        path.chmod(0o700)


def inspect_plist(path: Path, label: str) -> bytes | None:
    """只读取可识别的托管普通文件；拒绝操作无关文件。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError(f"拒绝覆盖或删除不安全、或不属于当前用户的 plist：{path}")
    data = path.read_bytes()
    try:
        obj = plistlib.loads(data)
        if not isinstance(obj, dict) or obj.get("Label") != label:
            raise ValueError("Label 不匹配")
        if label.startswith(CUSTOM_PREFIX):
            validate_managed_payload(obj, label, LOG_DIRECTORY)
            return data
        args = obj.get("ProgramArguments")
        if not isinstance(args, list) or len(args) != 3 or args[:2] != ["/bin/zsh", "-lc"]:
            raise ValueError("不是本 Skill 支持的固定命令模板")
        if not isinstance(args[2], str) or not args[2].strip():
            raise ValueError("命令无效")
        if type(obj.get("KeepAlive")) is not bool:
            raise ValueError("KeepAlive 字段类型不符合固定模板")
        working_dir = obj.get("WorkingDirectory")
        if not isinstance(working_dir, str) or not Path(working_dir).is_absolute():
            raise ValueError("工作目录字段不符合固定模板")
        if type(obj.get("RunAtLoad")) is not bool:
            raise ValueError("RunAtLoad 字段类型不符合固定模板")
        if "StartCalendarInterval" in obj:
            if obj["RunAtLoad"] is not False or obj["KeepAlive"] is not False:
                raise ValueError("定时任务必须关闭 RunAtLoad 和 KeepAlive")
            raw = obj["StartCalendarInterval"]
            entries = canonical_calendar(raw if isinstance(raw, list) else [raw])
            expected = build_plist(label, args[2], Path(working_dir), False, entries)
        else:
            if obj["RunAtLoad"] is not True:
                raise ValueError("登录自启动任务必须设置 RunAtLoad=true；" + unrecognized_login_autostart_hint())
            expected = build_plist(label, args[2], Path(working_dir), obj["KeepAlive"])
        if obj != expected:
            missing = sorted(expected.keys() - obj.keys())
            extra = sorted(obj.keys() - expected.keys())
            changed = sorted(key for key in obj.keys() & expected.keys() if obj[key] != expected[key])
            differences = []
            if extra:
                differences.append("多出键：" + ", ".join(extra))
            if missing:
                differences.append("缺失键：" + ", ".join(missing))
            if changed:
                differences.append("值不同的键：" + ", ".join(changed))
            raise ValueError("plist 的字段或值不符合本 Skill 的固定模板（" + "；".join(differences) + "）")
    except (ValueError, TypeError, OverflowError, plistlib.InvalidFileException, ExpatError) as exc:
        raise RuntimeError(f"拒绝操作无法识别的 plist {path}：{exc}") from exc
    return data



def inspect_explicit_discard_candidate(path: Path, label: str) -> bytes:
    """清理异常文件时只认证所属 Label，不将该配置视为可注册模板。"""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("plist 不存在；若仅剩管理记录，请使用 remove-orphan") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError(f"异常 plist 的文件类型、所有权或权限不安全；禁止清理：{path}")
    data = path.read_bytes()
    try:
        payload = plistlib.loads(data)
    except (ValueError, TypeError, OverflowError, plistlib.InvalidFileException, ExpatError) as exc:
        raise UnparseablePlistError("plist 无法解析，不能核对其内部 Label；已拒绝自动清理") from exc
    if not isinstance(payload, dict) or payload.get("Label") != label:
        raise RuntimeError("plist 内部 Label 与管理记录不一致；拒绝清理未知任务")
    return data


def managed_file_recovery_hint(label: str, *, recognizable: bool, path: Path | None = None) -> str:
    """只为本工具已验证归属的文件提出可执行的恢复路径。

    仅哈希漂移且仍符合模板时优先接受；模板校验失败时只在清理候选
    的所有权、普通文件类型、内部 Label 均可验证时建议显式丢弃。
    此函数只读，不修改磁盘或调用 launchctl。
    """
    quoted_label = shlex.quote(label)
    if recognizable:
        return (
            "当前 plist 仍符合托管模板。若确认外部修改可信，优先执行 "
            f"repair --label {quoted_label} --action accept-plist --trust-current-plist "
            "接受当前磁盘配置（不会自动注册）；若需恢复可信注册快照，再明确执行 "
            f"register --label {quoted_label} --allow-execution（注册可能触发执行）。"
            "仅在确定丢弃该任务时，才执行 "
            f"repair --label {quoted_label} --action drop-unrecognized --confirm-delete。"
        )
    if path is None:
        return "当前 plist 无法识别；请先核对来源和文件安全性，不能直接接受为托管配置。"
    try:
        inspect_explicit_discard_candidate(path, label)
    except UnparseablePlistError:
        return state_recovery_hint(label, "plist_unparseable", path=path)
    except RuntimeError:
        return "当前 plist 无法识别，且清理候选的安全性或内部 Label 无法确认；不能自动接管或清理，请人工核对。"
    except OSError:
        return "当前 plist 无法识别，且清理候选的安全性或内部 Label 无法确认；不能自动接管或清理，请人工核对。"
    return (
        "当前 plist 不再符合托管模板，不能执行 accept-plist。"
        "若已确认要丢弃该异常任务并允许停止服务，才执行 "
        f"repair --label {quoted_label} --action drop-unrecognized --confirm-delete。"
    )


def unrecognized_login_autostart_hint() -> str:
    return ("如需关闭登录自启动，应使用 uninstall，而不是直接修改 RunAtLoad；"
            "若 plist 已被改坏，请先恢复原模板再正常卸载，或在确认归属后显式清理。")


def inspect_managed_task(label: str, *, require_disk: bool = True,
                         require_hash: bool = True) -> tuple[bytes | None, dict[str, Any]]:
    """只有管理记录、固定模板和哈希都匹配时才允许操作任务。"""
    _, metadata = inspect_metadata(label)
    if metadata is None:
        path = LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"
        hint = ("；" + state_recovery_hint(label, "plist_without_record")) if (
            path.exists() or path.is_symlink()) else ""
        raise RuntimeError("缺少本 Skill 的管理记录；即使 Label 或 plist 格式相似，也不会接管未知任务" + hint)
    path = LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"
    try:
        data = inspect_plist(path, label)
    except RuntimeError as exc:
        raise RuntimeError(str(exc) + "；" + managed_file_recovery_hint(
            label, recognizable=False, path=path)) from exc
    if data is None:
        if require_disk:
            raise RuntimeError("管理记录存在，但 plist 缺失；已拒绝常规管理操作；"
                               + state_recovery_hint(label, "record_ok_plist_missing"))
        return None, metadata
    obj = plistlib.loads(data)
    if metadata.get("mode") != payload_mode(obj):
        raise RuntimeError("管理记录与 plist 模式不匹配，已拒绝操作")
    if require_hash and metadata.get("disk_sha256") != sha256_bytes(data):
        raise RuntimeError("管理记录与 plist 内容不一致；可能被外部更改，已拒绝操作；"
                           + managed_file_recovery_hint(label, recognizable=True))
    return data, metadata


def atomic_write(path: Path, data: bytes) -> None:
    """在目标目录创建唯一的 0600 临时文件，再原子替换。"""
    if path.is_symlink():
        raise RuntimeError(f"拒绝覆盖符号链接：{path}")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def management_lock() -> Iterator[None]:
    """串行化多个安装器实例的并发修改。"""
    lock_path = LAUNCH_AGENTS_DIRECTORY / LOCK_FILENAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(f"锁文件不安全：{lock_path}")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def protect_log_file(path: Path) -> None:
    """以私有权限预建日志，不截断旧日志，也不跟随符号链接。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        os.close(fd)
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"现有日志文件不安全：{path}")
    path.chmod(0o600)


def ensure_log_files(label: str) -> None:
    ensure_directory(LOG_DIRECTORY, private=True)
    for suffix in ("stdout", "stderr"):
        protect_log_file(LOG_DIRECTORY / f"{label}.{suffix}.log")


def record_restored_registration(label: str, data: bytes) -> None:
    """回滚重注册也是真实注册；不能继续声称还在使用更早的快照。"""
    if not require_known_service_state(label):
        raise RuntimeError("恢复注册后仍未确认服务已加载")
    _, record = inspect_metadata(label)
    if record is None:
        raise RuntimeError("恢复注册后缺少管理记录，无法更新快照")
    digest = sha256_bytes(data)
    if record.get("last_registered_sha256") != digest or record.get("registration_snapshot_unknown"):
        restored = dict(record)
        restored.update(last_registered_sha256=digest, last_registered_at=timestamp_now(),
                        registration_snapshot_unknown=False, registration_snapshot_unknown_reason=None)
        atomic_write(metadata_path(label), metadata_bytes(restored))


def rollback(
    path: Path,
    old: bytes | None,
    label: str,
    live_touched: bool,
    old_loaded: bool,
    old_metadata_bytes: bytes | None,
    pending_data: bytes,
    pending_metadata: dict[str, Any],
) -> tuple[list[str], bool]:
    """尽力恢复；无法停止的新任务保留记录，绝不把活服务变成不可见孤儿。"""
    errors: list[str] = []
    relaunched = False
    runtime_uncertain = False
    if live_touched:
        try:
            if require_known_service_state(label):
                launchctl("bootout", service_target(label))
                if require_known_service_state(label):
                    raise RuntimeError("回滚停止命令返回后服务仍在加载")
        except (OSError, RuntimeError) as exc:
            runtime_uncertain = True
            errors.append(f"无法确认或停止部分加载的任务：{exc}")
    # 若是新任务且服务可能仍活着，就保留已确认由本次安装产生的配置/记录。
    # 它们仍有源配置和描述可供查询，unknown 快照会阻止 run，不冒充安装成功。
    retain_new = old is None and runtime_uncertain
    restore_data = pending_data if retain_new else old
    restore_record = metadata_bytes(pending_metadata) if retain_new else old_metadata_bytes
    try:
        if restore_data is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, restore_data)
    except (OSError, RuntimeError) as exc:
        errors.append(f"无法恢复原 plist：{exc}")
    try:
        if restore_record is None:
            metadata_path(label).unlink(missing_ok=True)
        else:
            ensure_private_metadata_directory()
            atomic_write(metadata_path(label), restore_record)
    except (OSError, RuntimeError) as exc:
        errors.append(f"无法恢复原任务记录：{exc}")
    if live_touched and old_loaded and old is not None and not runtime_uncertain:
        try:
            if inspect_plist(path, label) != old:
                raise RuntimeError("原 plist 尚未完整恢复，拒绝重载")
            if require_known_service_state(label):
                raise RuntimeError("恢复过程中发现未预期的已加载服务，拒绝冒充恢复成功")
            launchctl("bootstrap", domain_target(), str(path))
            relaunched = payload_mode(plistlib.loads(old)) in {"login", "custom"}
            record_restored_registration(label, old)
        except (OSError, RuntimeError) as exc:
            runtime_uncertain = True
            errors.append(f"无法重新注册旧任务：{exc}")
    if runtime_uncertain:
        try:
            _, record = inspect_metadata(label)
            if record is None:
                raise RuntimeError("缺少恢复记录")
            recovery = dict(record)
            recovery.update(registration_snapshot_unknown=True,
                            registration_snapshot_unknown_reason="recovery-failed", updated_at=timestamp_now())
            atomic_write(metadata_path(label), metadata_bytes(recovery))
            errors.append(f"服务状态尚未恢复确认；已保留任务记录并阻止 run，请查询 Label：{label}")
        except (OSError, RuntimeError) as exc:
            errors.append(f"无法标记未知快照或保留完整恢复记录：{exc}；请人工核对服务和磁盘")
    return errors, relaunched

class InstallPlan(NamedTuple):
    """磁盘只读预检的结果；预览与实际提交使用相同的拒绝条件。"""
    path: Path
    meta_path: Path
    old: bytes | None
    old_metadata_bytes: bytes | None
    old_metadata: dict[str, Any] | None
    description: str
    updated: bool
    description_changed: bool
    existed_before: bool
    had_registered_snapshot: bool


def inspect_install_plan(*, args: argparse.Namespace, label: str, data: bytes,
                         mode: str, new_description: str | None,
                         expected_disk: bytes | None = None) -> InstallPlan:
    """不创建目录、文件或调用 launchctl；与提交共享磁盘/归属/替换规则。

    该检查只能反映检查瞬间的磁盘状态，不能保证 GUI 会话或后续 bootstrap 成功。
    """
    path = LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"
    meta_path = metadata_path(label)
    old_metadata_bytes, old_metadata = inspect_metadata(label)
    try:
        old = inspect_plist(path, label)
    except RuntimeError as exc:
        if old_metadata is not None:
            raise RuntimeError(str(exc) + "；" + managed_file_recovery_hint(
                label, recognizable=False, path=path)) from exc
        raise RuntimeError(str(exc) + "；" + state_recovery_hint(
            label, "plist_without_record", path=path)) from exc
    if expected_disk is not None and old != expected_disk:
        raise RuntimeError("任务在准备注册期间发生变化；未覆盖，请重新查询再操作")
    if old is None and old_metadata is not None:
        raise RuntimeError(f"发现孤立管理记录：{meta_path}；拒绝安装以防误关联；"
                           + state_recovery_hint(label, "record_ok_plist_missing"))
    if old is not None:
        if old_metadata is None:
            raise RuntimeError("既有 plist 没有本 Skill 的管理记录；拒绝接管或覆盖未知任务；"
                               + state_recovery_hint(label, "plist_without_record", path=path))
        if old_metadata.get("mode") != payload_mode(plistlib.loads(old)):
            raise RuntimeError("管理记录的模式与 plist 不一致")
        if old_metadata.get("disk_sha256") != sha256_bytes(old):
            raise RuntimeError("plist 与管理记录不一致；拒绝覆盖外部更改的任务；"
                               + managed_file_recovery_hint(label, recognizable=True))
        if payload_mode(plistlib.loads(old)) != mode:
            raise RuntimeError("相同 Label 已属于另一种任务模式，拒绝跨模式覆盖")
    description = new_description or (validate_description(old_metadata.get("description")) if old_metadata else None)
    if description is None:
        raise ValueError("首次安装必须提供 --description，简要说明任务用途")
    updated = old != data
    if updated and old is not None and not getattr(args, "replace", False):
        raise RuntimeError(f"现有配置不同：{path}；拒绝静默覆盖。确认要更新时请用 --replace。")
    return InstallPlan(path, meta_path, old, old_metadata_bytes, old_metadata,
                       description, updated,
                       old_metadata is None or old_metadata.get("description") != description,
                       old is not None,
                       bool(old_metadata and old_metadata.get("last_registered_sha256")))


def commit_install(args: argparse.Namespace, *, label: str, data: bytes, mode: str,
                   source_file: str | None, new_description: str | None,
                   activate: bool, source_info: dict[str, Any] | None = None,
                   force_reload: bool = False, expected_disk: bytes | None = None,
                   preserve_source: bool = False,
                   operation: str = "install") -> tuple[Path, Path, str, bool, bool, bool, bool]:
    """统一提交入口：所有配置类型共用锁、归属检查、落盘、注册和回滚。"""
    require_macos()
    if activate and launchctl("print", domain_target(), check=False).returncode != 0:
        raise RuntimeError("图形登录 launchd 域不可用；本次未写 plist。请先只写盘，或在目标图形会话中注册。")
    if activate:
        override, _ = disabled_override_status(label)
        if override == "disabled":
            raise RuntimeError(disabled_service_guidance(label, mode=mode, operation=operation) + "本次未写入配置。")
    if new_description is None:
        _, prior = inspect_metadata(label)
        if prior is None:
            raise ValueError("首次安装必须提供 --description，简要说明任务用途")
    ensure_directory(LAUNCH_AGENTS_DIRECTORY)
    with management_lock():
        plan = inspect_install_plan(args=args, label=label, data=data, mode=mode,
                                    new_description=new_description, expected_disk=expected_disk)
        (path, meta_path, old, old_metadata_bytes, old_metadata,
         description, updated, description_changed,
         existed_before, had_registered_snapshot) = (
            plan.path, plan.meta_path, plan.old, plan.old_metadata_bytes, plan.old_metadata,
            plan.description, plan.updated, plan.description_changed,
            plan.existed_before, plan.had_registered_snapshot)
        old_loaded = require_known_service_state(label) if activate else False
        if activate and old is None and old_loaded:
            raise RuntimeError("同 Label 服务已从未知 plist 加载，拒绝替换")
        ensure_log_files(label)
        pending_metadata = make_metadata(label, path, data, description, mode,
                                         source_file, old_metadata, registered_now=False)
        if source_info:
            pending_metadata.update(source_info)
        live_touched = False
        registered_now = False
        try:
            if updated:
                atomic_write(path, data)
            plutil_lint(path)
            if activate:
                reload_needed = (updated or force_reload or getattr(args, "start_now", False)
                    or old_metadata is None
                    or bool(old_metadata.get("registration_snapshot_unknown"))
                    or old_metadata.get("last_registered_sha256") != sha256_bytes(data))
                if old_loaded and reload_needed:
                    live_touched = True
                    launchctl("bootout", service_target(label))
                    if require_known_service_state(label):
                        raise RuntimeError("bootout 返回后任务仍加载，拒绝继续替换")
                if not old_loaded or reload_needed:
                    live_touched = True
                    launchctl("bootstrap", domain_target(), str(path))
                    registered_now = True
                if not require_known_service_state(label):
                    raise RuntimeError("注册命令返回后仍未确认任务已加载")
                if mode == "calendar" and getattr(args, "start_now", False):
                    launchctl("kickstart", service_target(label))
            if preserve_source and old_metadata:
                source_file = old_metadata.get("source_file")
            new_metadata = make_metadata(label, path, data, description, mode,
                                         source_file, old_metadata, registered_now)
            if preserve_source and old_metadata:
                for key in ("source_type", "source_sha256", "source_label", "task_name"):
                    if key in old_metadata:
                        new_metadata[key] = old_metadata[key]
            elif source_info:
                new_metadata.update(source_info)
            write_metadata(label, new_metadata)
        except (RuntimeError, OSError, ValueError) as exc:
            bootstrap_diagnosis = bootstrap_failure_guidance(label, exc, mode=mode, operation=operation) if live_touched else ""
            recovery_errors, relaunched = rollback(path, old, label, live_touched, old_loaded, old_metadata_bytes, data, pending_metadata)
            message = f"安装失败：{exc}{bootstrap_diagnosis}；已尝试回滚"
            if recovery_errors:
                message += "；回滚未完成：" + "；".join(recovery_errors)
            if old is not None and (relaunched or (live_touched and old_loaded
                    and payload_mode(plistlib.loads(old)) in {"login", "custom"})):
                message += "。" + restoration_warning(old)
            if live_touched:
                message += " 恢复以操作前磁盘版本为准；不保证还原之前实际运行的配置或进程，注册/触发中已发生的业务动作不可撤销。"
            raise RuntimeError(message) from exc
    return path, meta_path, description, updated, description_changed, existed_before, had_registered_snapshot


def find_duplicate_fixed_tasks(label: str, mode: str, command: str) -> list[str]:
    """查找其他有效托管记录，判断是否存在同模式、同命令的任务。"""
    if mode not in {"login", "calendar"} or not METADATA_DIRECTORY.exists():
        return []
    matched = []
    for path in sorted(METADATA_DIRECTORY.glob(f"{PREFIX}*.json")):
        other_label = path.stem
        if other_label == label:
            continue
        try:
            validate_label(other_label)
            data, record = inspect_managed_task(other_label)
            if record["mode"] != mode or data is None:
                continue
            arguments = plistlib.loads(data).get("ProgramArguments")
            if isinstance(arguments, list) and len(arguments) == 3 and arguments[2] == command:
                matched.append(other_label)
        except (RuntimeError, OSError, ValueError, TypeError, plistlib.InvalidFileException):
            # 只提示归属可验证的任务；不得凭相似 Label 接管未知 plist。
            continue
    return matched


def duplicate_fixed_task_notice(labels: list[str]) -> str:
    return '提醒：已有同模式、同命令的任务 {labels}；本次将新增另一条任务，可能重复执行。若不需要并存，请先核对并卸载旧任务。'.format(labels=", ".join(labels))


def fixed_trigger_summary(payload: dict[str, Any]) -> str:
    """固定模板登录/日历任务的真实触发配置。"""
    run_at_load = str(payload["RunAtLoad"]).lower()
    keep_alive = str(payload["KeepAlive"]).lower()
    return f"RunAtLoad={run_at_load}、KeepAlive={keep_alive}"


def print_install_summary(*, data: bytes, label: str, description: str,
                          mode: str, source_file: str | None, source_label: str | None = None) -> None:
    """预览和成功安装共用同一份配置摘要，不依赖运行态推测。"""
    payload = plistlib.loads(data)
    print(f"任务描述：{description}")
    print(f"模式：{mode_text(mode)}")
    print("触发配置：" + (custom_summary(payload) if mode == "custom"
                        else fixed_trigger_summary(payload)))
    if mode == "calendar":
        schedule = payload["StartCalendarInterval"]
        print(f"计划时间（Mac 本地时区）：{calendar_text(schedule if isinstance(schedule, list) else [schedule])}")
    print(f"Label：{label}")
    print(f"plist 文件：{LAUNCH_AGENTS_DIRECTORY / (label + '.plist')}")
    print(f"工作目录：{payload['WorkingDirectory']}")
    print(f"元数据文件：{metadata_path(label)}")
    if mode == "custom":
        print(f"来源文件：{source_file}（仅作来源记录；运行不依赖此文件）")
        source_name = source_label or "未提供"
        print(f"来源 Label：{source_name}（仅作来源信息，不按此 Label 操作服务）")
    else:
        print(f"源脚本：{source_file if source_file else '无（直接执行命令）'}")
        print(f"执行命令：{payload['ProgramArguments'][2]}")
    print(f"标准输出日志：{payload['StandardOutPath']}")
    print(f"错误日志：{payload['StandardErrorPath']}")


def install(args: argparse.Namespace) -> int:
    """常规配置生成器；生命周期由 commit_install 负责。"""
    if args.name is not None and not args.name.strip():
        raise ValueError("--name 不能为空")
    calendar = make_calendar(args)
    mode = "calendar" if calendar is not None else "login"
    command = get_command(args)
    source_file = getattr(args, "resolved_script", None)
    working_directory = get_working_directory(args.working_directory)
    new_description = validate_description(args.description) if args.description is not None else None
    label = make_label(command, args.name, mode)
    data = to_plist_bytes(build_plist(label, command, working_directory, args.keep_alive, calendar))
    possible_duplicates = find_duplicate_fixed_tasks(label, mode, command)
    if args.dry_run:
        plan = inspect_install_plan(args=args, label=label, data=data,
                                    mode=mode, new_description=new_description)
        print("只预览；只读磁盘预检通过；未写盘、未调用 launchctl。注册是否成功仍需在 Mac GUI 会话中验证。")
        print_install_summary(data=data, label=label, description=plan.description,
                              mode=mode, source_file=source_file)
        if possible_duplicates and not plan.existed_before:
            print(duplicate_fixed_task_notice(possible_duplicates))
        print(data.decode("utf-8"))
        return 0
    path, meta_path, description, updated, description_changed, existed_before, had_registered_snapshot = commit_install(
        args, label=label, data=data, mode=mode, source_file=source_file,
        new_description=new_description,
        activate=args.start_now or (mode == "calendar" and not args.defer))
    if mode == "login":
        if args.start_now:
            print("已安装并注册登录自启动任务；RunAtLoad 已触发一次启动请求。注册成功不代表命令仍在运行或已成功完成，请查看日志。")
        else:
            print("已写入登录自启动 plist；本次安装不会注册或执行命令。通常会在下次图形用户登录时加载。")
            if existed_before and updated:
                print("当前已加载的同 Label 任务可能继续使用旧配置。")
    elif args.defer:
        print('已写入定时任务 plist；本次未注册或执行，将等待下次图形用户登录加载。')
        if had_registered_snapshot and updated:
            print('旧日程可能依然加载。')
    else:
        print("已安装并注册日历定时任务；注册本身不会因 RunAtLoad 执行命令。")
        if args.start_now:
            print("已按明确请求额外触发一次任务；实际执行结果请查看日志。")
    if possible_duplicates and not existed_before:
        print(duplicate_fixed_task_notice(possible_duplicates))
    if not updated:
        print("plist 内容未变化。")
    if not description_changed:
        print("任务描述未变化。")
    print_install_summary(data=data, label=label, description=description,
                          mode=mode, source_file=source_file)
    return 0


def install_plist(args: argparse.Namespace) -> int:
    """将经过校验的扩展配置接入统一托管；不接管来源 Label 的旧服务。"""
    label = make_label("", args.name, "custom")
    source, source_path, raw = load_source(args.input)
    payload = prepare_payload(source, label, LOG_DIRECTORY, DEFAULT_WORKING_DIRECTORY)
    data = to_plist_bytes(payload)
    description = validate_description(args.description) if args.description is not None else None
    if args.dry_run:
        plan = inspect_install_plan(args=args, label=label, data=data,
                                    mode="custom", new_description=description)
        print("只预览；只读磁盘预检通过；未写盘、未调用 launchctl。注册是否成功仍需在 Mac GUI 会话中验证。")
        print_install_summary(data=data, label=label, description=plan.description,
                              mode="custom", source_file=str(source_path), source_label=source.get("Label"))
        print("注册后 XML 条件可能立即触发任务。")
        print(data.decode("utf-8"))
        return 0
    path, meta_path, description, updated, _, _, _ = commit_install(
        args, label=label, data=data, mode="custom", source_file=str(source_path),
        new_description=description, activate=args.activate, operation="install-plist",
        source_info={"source_type": "plist", "source_sha256": sha256_bytes(raw),
                     "source_label": source.get("Label"), "task_name": args.name.strip()})
    print("已安装并注册自定义托管任务；XML 条件可能已触发执行。" if args.activate
          else "已写入自定义托管任务；未调用 launchctl。下次图形登录仍可能自动加载并按 XML 条件运行。")
    if args.activate:
        print("本次没有额外 kickstart；注册成功不代表业务执行成功。")
    else:
        print('本次未注册，仅写盘。')
    if not updated:
        print("plist 内容未变化。")
    print_install_summary(data=data, label=label, description=description,
                          mode="custom", source_file=str(source_path), source_label=source.get("Label"))
    return 0


def register_task(args: argparse.Namespace) -> int:
    """按实际 Label 重注册当前托管磁盘配置，避免重填命令生成新身份。"""
    require_macos()
    validate_label(args.label)
    if not args.allow_execution:
        raise ValueError("注册/重新注册可能停止旧实例并触发执行；确认后必须提供 --allow-execution")
    data, record = inspect_managed_task(args.label)
    assert data is not None
    commit_install(args, label=args.label, data=data, mode=record["mode"],
                   source_file=None, new_description=None, activate=True,
                   force_reload=True, expected_disk=data, preserve_source=True,
                   operation="register")
    print(f"已按磁盘配置重新注册：{args.label}")
    print("已更新本工具注册快照；没有额外 kickstart，但配置条件可能立即触发执行。")
    return 0

def _present_task(label: str, metadata: dict[str, Any], *, verbose: bool = True) -> None:
    """只读展示任务；文件不一致时绝不假定它仍属于本工具。"""
    print("---")
    print(f"任务描述：{metadata['description']}")
    print(f"Label：{label}")
    print(f"管理记录：{metadata_path(label)}")
    path = LAUNCH_AGENTS_DIRECTORY / f"{label}.plist"
    print(f"plist 文件：{path}")
    try:
        data = inspect_plist(path, label)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"文件状态：异常（{exc}）；已禁止管理操作")
        # 故障记录即使在 list 中也必须指出正确出口；正常任务的简洁性不受影响。
        print("恢复建议：" + managed_file_recovery_hint(label, recognizable=False, path=path))
        return
    if data is None:
        print("文件状态：plist 丢失（孤立管理记录）；不会触碰同名未知服务")
        if verbose:
            print("恢复建议：" + state_recovery_hint(label, "record_ok_plist_missing"))
        else:
            print("恢复入口：repair --action remove-orphan --confirm-stop（核实 Label 后使用）。")
        return
    obj = plistlib.loads(data)
    if metadata.get("mode") != payload_mode(obj):
        print("文件状态：模式不匹配；已禁止管理操作")
        return
    tampered = sha256_bytes(data) != metadata.get("disk_sha256")
    if tampered:
        print("文件状态：磁盘配置与管理记录不同；已禁止管理操作")
        if verbose:
            print("恢复建议：" + managed_file_recovery_hint(label, recognizable=True))
        else:
            print("恢复入口：确认可信后使用 accept-plist；决定丢弃时才使用 drop-unrecognized。")
    schedule = obj.get("StartCalendarInterval")
    print(f"模式：{mode_text(metadata['mode'])}")
    if metadata["mode"] == "custom":
        print(f"触发配置：{custom_summary(obj)}")
    elif schedule is not None:
        print(f"计划时间：{calendar_text(schedule if isinstance(schedule, list) else [schedule])}")
    if metadata["mode"] != "custom":
        print('触发配置：' + fixed_trigger_summary(obj))
    if metadata.get("source_file"):
        print(f"来源文件：{metadata['source_file']}")
    if metadata["mode"] == "custom":
        print("执行配置：" + json.dumps({k: obj[k] for k in ("Program", "ProgramArguments") if k in obj}, ensure_ascii=False))
        print(f"来源 Label：{metadata.get('source_label') or '未记录'}（不是托管身份）")
    else:
        print(f"执行命令：{obj['ProgramArguments'][2]}")
    print(f"标准输出日志：{obj['StandardOutPath']}")
    print(f"错误日志：{obj['StandardErrorPath']}")
    state, details = probe_service(label)
    disabled_state, disabled_detail = disabled_override_status(label)
    if disabled_state == "disabled" and state == "absent":
        print("当前状态：已禁用（当前未加载）")
    elif disabled_state == "disabled" and state == "unknown":
        print("当前状态：无法确认（另检测到持久禁用覆盖）")
    else:
        print(f"当前状态：{'已加载' if state == 'loaded' else '未加载' if state == 'absent' else '无法确认'}")
    if disabled_state == "disabled":
        print("系统禁用覆盖：已禁用" +
              ("；" + disabled_service_guidance(label, mode=metadata["mode"], operation="status") if verbose else ""))
    elif disabled_state == "enabled":
        print("系统禁用覆盖：已明确启用")
    elif disabled_state == "not-listed":
        print("系统禁用覆盖：无显式条目（不代表当前已加载）")
    else:
        print("系统禁用覆盖：无法确认；" + disabled_detail)
    if details:
        print(f"查询警告：{details}")
    print(f"配置同步：{registration_sync_text(data, metadata, state)}")


def list_agents(_args: argparse.Namespace) -> int:
    """列出有管理记录的任务，并以一行摘要报告无记录的同前缀路径。"""
    require_macos()
    for directory in (METADATA_DIRECTORY.parent, METADATA_DIRECTORY):
        if directory.is_symlink():
            raise RuntimeError(f"拒绝枚举符号链接管理目录：{directory}")
    with management_lock_if_agents_exist():
        count = 0
        errors = 0
        paths = (sorted(METADATA_DIRECTORY.glob(f"{PREFIX}*.json"))
                 if METADATA_DIRECTORY.exists() else [])
        for path in paths:
            label = path.stem
            try:
                validate_label(label)
                _, metadata = inspect_metadata(label)
                if metadata is not None:
                    count += 1
                    _present_task(label, metadata, verbose=False)
            except InvalidMetadataError as exc:
                errors += 1
                print(f"已跳过损坏的管理记录：{exc}", file=sys.stderr)
            except (RuntimeError, OSError, ValueError) as exc:
                errors += 1
                print(f"已跳过不安全或无效的管理记录：{path}（{exc}）；"
                      "禁止自动接管或修复。", file=sys.stderr)
        unmanaged = 0
        abnormal = 0
        if LAUNCH_AGENTS_DIRECTORY.exists():
            for path in sorted(LAUNCH_AGENTS_DIRECTORY.glob(f"{PREFIX}*.plist")):
                label = path.stem
                try:
                    validate_label(label)
                except ValueError:
                    continue
                record_path = metadata_path(label)
                if record_path.exists() or record_path.is_symlink():
                    continue
                try:
                    kind = path_entry_kind(path)
                except OSError as exc:
                    abnormal += 1
                    print(f"异常条目（类型无法确认）：{path}；Label：{label}；"
                          f"无法安全检查（{exc}）；不按 plist 处理。")
                    continue
                if kind is None:
                    continue
                if kind == "普通文件":
                    unmanaged += 1
                    print(f"未管理的托管命名样式 plist：{path}；Label：{label}；"
                          "类型：普通文件；归属未知，未管理。")
                else:
                    abnormal += 1
                    print(f"异常条目（非普通文件：{kind}）：{path}；"
                          f"Label：{label}；不按 plist 处理，不查询或操作同名服务。")
        if count == 0 and unmanaged == 0 and abnormal == 0 and errors == 0:
            print("未找到本 Skill 的管理记录或托管命名样式 plist。")
        else:
            summary = (f"共列出 {count} 个有效管理记录、{unmanaged} 个未管理文件；"
                       f"{errors} 个异常管理记录")
            if abnormal:
                summary += f"、{abnormal} 个非普通文件异常条目"
            print(summary + "。异常/孤立记录不得执行常规管理操作；只对可验证归属的记录使用相应 repair。")
            if unmanaged:
                print("提示：以上未管理普通文件均不属于本 Skill 的托管任务，"
                      "不能自动停服或删除；完整人工核对步骤请用 "
                      "status --label '<对应 Label>' 查看，或阅读 references/operations.md。")
            if abnormal:
                print("提示：非普通文件异常条目不按 plist 处理，"
                      "也不会据此建议删除；请人工核对路径类型与来源。")
    return 0


@contextmanager
def management_lock_if_agents_exist() -> Iterator[None]:
    """仅当 LaunchAgents 目录已存在时才建立读取锁。"""
    if LAUNCH_AGENTS_DIRECTORY.exists():
        ensure_directory(LAUNCH_AGENTS_DIRECTORY)
        with management_lock():
            yield
    else:
        yield


def status(args: argparse.Namespace) -> int:
    require_macos()
    validate_label(args.label)
    with management_lock_if_agents_exist():
        try:
            _, metadata = inspect_metadata(args.label)
        except RuntimeError as exc:
            # inspect_metadata 的权限类错误可能已包含路径；不要重复拼接。
            location = str(metadata_path(args.label))
            if location in str(exc):
                raise
            raise RuntimeError(f"记录路径：{location}；{exc}") from exc
        if metadata is None:
            path = LAUNCH_AGENTS_DIRECTORY / f"{args.label}.plist"
            kind = path_entry_kind(path)
            print("未找到本 Skill 的管理记录；不查询同名未知服务。", file=sys.stderr)
            if kind is not None:
                print("恢复建议：" + state_recovery_hint(
                    args.label, "plist_without_record", path=path), file=sys.stderr)
            return 1
        _present_task(args.label, metadata)
    return 0


def _validate_repair_metadata_storage(label: str) -> Path:
    """修复损坏记录前先确认目录和记录本身没有权限/符号链接问题。"""
    root = METADATA_DIRECTORY.parent
    path = metadata_path(label)
    for directory in (root, METADATA_DIRECTORY):
        if directory.is_symlink():
            raise RuntimeError(f"拒绝修复符号链接管理目录：{directory}")
        if directory.exists():
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise RuntimeError(f"管理目录权限或所有权不安全（需要 0700），拒绝自动修复：{directory}")
    if path.is_symlink():
        raise RuntimeError(f"拒绝修复符号链接管理记录：{path}")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"管理记录文件不存在：{path}；不会自动接管未知任务") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"管理记录权限或所有权不安全，拒绝自动修复：{path}")
    return path


def ensure_repairable_record(path: Path) -> None:
    """损坏的记录可在明确确认后重建，但不覆盖含未定义字段的记录。"""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return  # 文件内容损坏，允许用户明确确认后重建管理记录。
    if isinstance(record, dict):
        if extra := record.keys() - METADATA_ALLOWED_FIELDS:
            raise RuntimeError(f"管理记录包含未定义字段，拒绝用重建命令覆盖：{', '.join(sorted(extra))}（{path}）")


def repair(args: argparse.Namespace) -> int:
    """提供显式、可审计的修复出口，不自动接管未知任务。"""
    require_macos()
    validate_label(args.label)
    ensure_directory(LAUNCH_AGENTS_DIRECTORY)
    with management_lock():
        plist_path = LAUNCH_AGENTS_DIRECTORY / f"{args.label}.plist"
        meta_path = metadata_path(args.label)

        if args.repair_action == "drop-unrecognized":
            if not args.confirm_delete:
                raise ValueError("清理被外部修改的托管任务会停止服务并删除 plist/记录；请显式添加 --confirm-delete")
            old_meta_bytes, record = inspect_metadata(args.label)
            if record is None or old_meta_bytes is None:
                raise RuntimeError("没有有效管理记录；拒绝清理未知服务或任意同名 plist")
            try:
                original = inspect_explicit_discard_candidate(plist_path, args.label)
            except UnparseablePlistError as exc:
                raise RuntimeError(str(exc) + "；" + state_recovery_hint(
                    args.label, "plist_unparseable", path=plist_path)) from exc
            try:
                recognized = inspect_plist(plist_path, args.label)
            except RuntimeError:
                recognized = None
            if recognized is not None and record["disk_sha256"] == sha256_bytes(original):
                raise RuntimeError("当前 plist 与管理记录一致且符合模板；请使用 uninstall 正常卸载")
            state, details = probe_service(args.label)
            if state == "unknown":
                raise RuntimeError(f"无法确认服务状态；已保留 plist 与记录：{details}")
            stopped = False
            stop_warning = ""
            if state == "loaded":
                try:
                    launchctl("bootout", service_target(args.label))
                except (RuntimeError, OSError):
                    # bootout 可能在成功停止服务之后才报告错误，必须再次探测。
                    stop_warning = "停止命令返回错误，但会再次确认运行态。"
                after, detail = probe_service(args.label)
                if after != "absent":
                    raise RuntimeError(f"未确认任务已停止（{detail or after}）；已保留文件和记录，禁止删除")
                stopped = True
            # 外部程序可能不遵守本工具的锁；真正删除前再次核对磁盘字节。
            try:
                latest = inspect_explicit_discard_candidate(plist_path, args.label)
            except (RuntimeError, OSError) as exc:
                stopped_note = "；旧服务已停止且不会自动重载" if stopped else ""
                raise RuntimeError(f"清理前无法重新确认 plist：{exc}；已保留文件与记录{stopped_note}") from exc
            if latest != original:
                stopped_note = "；旧服务已停止且不会自动重载" if stopped else ""
                raise RuntimeError("清理前 plist 又被修改；已保留文件与记录，禁止删除" + stopped_note)
            try:
                meta_path.unlink()
                plist_path.unlink()
            except (OSError, RuntimeError) as exc:
                recovery_errors = []
                try:
                    if not plist_path.exists():
                        atomic_write(plist_path, original)
                except (OSError, RuntimeError) as recover:
                    recovery_errors.append(f"恢复 plist 失败：{recover}")
                try:
                    if not meta_path.exists():
                        ensure_private_metadata_directory()
                        atomic_write(meta_path, old_meta_bytes)
                except (OSError, RuntimeError) as recover:
                    recovery_errors.append(f"恢复管理记录失败：{recover}")
                suffix = ("；" + "；".join(recovery_errors)) if recovery_errors else ""
                if stopped:
                    suffix += "；原服务已停止，不会自动重载被外部修改的配置；请核对后处理"
                raise RuntimeError(f"清理异常任务时删除文件失败：{exc}{suffix}") from exc
            print(f"已明确清理被外部修改的托管任务：{args.label}")
            print("已删除 plist 与管理记录，保留日志；未采用修改后的配置作为可信基线。")
            if stopped:
                print("已确认停止旧服务；未重新 bootstrap 或执行被修改的配置。")
            if stop_warning:
                print("警告：" + stop_warning)
            state, _ = disabled_override_status(args.label)
            if state == "disabled":
                print(disabled_uninstall_notice(args.label, record["mode"]))
            return 0

        if args.repair_action == "accept-plist":
            if not args.trust_current_plist:
                raise ValueError("此操作会信任当前磁盘 plist；请显式添加 --trust-current-plist")
            _, metadata = inspect_metadata(args.label)
            if metadata is None:
                raise RuntimeError("没有有效管理记录；请勿用 accept-plist 接管未知任务")
            try:
                data = inspect_plist(plist_path, args.label)
            except RuntimeError as exc:
                raise RuntimeError(str(exc) + "；" + managed_file_recovery_hint(
                    args.label, recognizable=False, path=plist_path)) from exc
            if data is None:
                raise RuntimeError("plist 已丢失；请使用 repair --action remove-orphan 清理孤立记录")
            payload = plistlib.loads(data)
            new_mode = payload_mode(payload)
            current_hash = sha256_bytes(data)
            if metadata.get("disk_sha256") == current_hash and metadata.get("mode") == new_mode:
                raise RuntimeError("磁盘 plist 与管理记录已经一致，无需修复")
            metadata = dict(metadata)
            old_mode = metadata.get("mode")
            old_disk_hash = metadata.get("disk_sha256")
            old_source_file = metadata.get("source_file")
            if metadata.get("source_type") == "plist":
                metadata["source_file"] = None
                metadata["source_sha256"] = None
            elif old_source_file and payload["ProgramArguments"][2] != shlex.quote(old_source_file):
                # plist 命令已改变时，不再声称它仍对应原来的源脚本。
                metadata["source_file"] = None
                metadata["source_type"] = "command"
            metadata.update(disk_sha256=current_hash, mode=new_mode, updated_at=timestamp_now())
            # 只有原来有可信注册快照却被更改，或模式发生变化时才新增未知状态。
            # 从未由本工具注册过的登录任务不应因为接受外部改动而永久卡死。
            if old_mode != new_mode:
                metadata["registration_snapshot_unknown"] = True
                metadata["registration_snapshot_unknown_reason"] = "mode-changed"
            elif (metadata.get("last_registered_sha256") is not None
                  and current_hash != old_disk_hash):
                metadata["registration_snapshot_unknown"] = True
                metadata["registration_snapshot_unknown_reason"] = "accepted-plist"
            write_metadata(args.label, metadata)
            print(f"已接受当前磁盘 plist 并更新管理记录：{args.label}")
            if old_mode != new_mode:
                print(f"任务模式已从 {mode_text(old_mode)} 更新为 {mode_text(new_mode)}；Label 不会自动改名。")
            print("未重载 launchd；若注册快照与磁盘内容不同或状态未知，run 仍会拒绝触发。请先核实并重新注册。")
            return 0

        if args.repair_action == "reset-snapshot":
            # 这是显式的“放弃可信注册快照”，而不是替 launchctl 重新注册。
            # 不触发用户命令，也不允许用它接管外部修改/无记录的任务。
            if not args.trust_current_plist:
                raise ValueError("复位注册快照须显式添加 --trust-current-plist，并先核对磁盘 plist")
            _, record = inspect_managed_task(args.label)
            if (not record.get("registration_snapshot_unknown")
                    and record.get("last_registered_sha256") is None):
                print("当前任务已经没有本工具维护的注册快照；无需复位。")
                return 0
            updated = dict(record)
            updated.update(last_registered_sha256=None, last_registered_at=None,
                           registration_snapshot_unknown=False,
                           registration_snapshot_unknown_reason=None,
                           updated_at=timestamp_now())
            write_metadata(args.label, updated)
            print(f"已将任务注册快照复位为未注册基线：{args.label}")
            print("未调用 launchctl；当前会话中任务可能仍被加载，但无法证明其配置与磁盘一致。")
            print("手动 run 将继续拒绝没有可信注册快照的任务；需显式重新注册才可恢复手动运行。")
            return 0

        if args.repair_action == "remove-orphan":
            if not args.confirm_stop:
                raise ValueError("清理孤立记录可能停止仍在运行的服务；请显式添加 --confirm-stop")
            # 内容损坏不妨碍用户删除自己的孤立记录，但必须先独立验证
            # 目录、普通文件、所有权与权限。绝不借此接管/重建未知记录。
            corrupt_record = False
            try:
                _, metadata = inspect_metadata(args.label)
            except InvalidMetadataError:
                _validate_repair_metadata_storage(args.label)
                corrupt_record = True
                metadata = None
            if metadata is None and not corrupt_record:
                raise RuntimeError("没有管理记录；不会操作未知服务")
            if plist_path.exists() or plist_path.is_symlink():
                raise RuntimeError("plist 仍存在；remove-orphan 仅用于 plist 已丢失的孤立记录")
            # 捕获文件身份与字节：外部改动不遵守管理锁，删除前必须再次检查。
            _validate_repair_metadata_storage(args.label)
            before_info = meta_path.lstat()
            before_data = meta_path.read_bytes()
            state, details = probe_service(args.label)
            if state == "unknown":
                raise RuntimeError(f"无法确认服务状态，未删除记录：{details}")
            warning = ""
            if state == "loaded":
                if corrupt_record:
                    raise RuntimeError(
                        "管理记录损坏且 plist 已缺失，无法证明当前加载服务的来源；"
                        f"请先人工核实 launchctl print '{service_target(args.label)}'，"
                        f"确认后手动 bootout '{service_target(args.label)}'，"
                        "确认未加载后再执行 remove-orphan --confirm-stop；管理记录已保留。")
                try:
                    launchctl("bootout", service_target(args.label))
                except (RuntimeError, OSError) as exc:
                    after, after_details = probe_service(args.label)
                    if after != "absent":
                        raise RuntimeError(f"停止孤立服务失败，管理记录已保留：{translate_system_detail(str(exc))}；{after_details}") from exc
                    warning = "停止命令返回错误，但已确认服务不再加载。"
                after, after_details = probe_service(args.label)
                if after != "absent":
                    raise RuntimeError(f"无法确认服务已停止，管理记录已保留：{after_details}")
            if plist_path.exists() or plist_path.is_symlink():
                raise RuntimeError("清理前发现 plist 已重新出现；保留管理记录，拒绝误关联")
            latest_info = meta_path.lstat()
            if ((latest_info.st_dev, latest_info.st_ino, latest_info.st_uid, latest_info.st_mode)
                    != (before_info.st_dev, before_info.st_ino, before_info.st_uid, before_info.st_mode)
                    or meta_path.read_bytes() != before_data):
                raise RuntimeError("清理期间管理记录被外部修改；拒绝删除，请重新核对")
            meta_path.unlink()
            print(f"已清理孤立管理记录：{args.label}")
            if corrupt_record:
                print("原记录 JSON 已损坏；已确认不存在可识别的加载服务，没有重建或接管同名任务。")
            print("没有删除任何 plist 文件；日志也予以保留。")
            if warning:
                print(f"警告：{warning}")
            return 0

        if args.repair_action == "rebuild-record":
            if not args.trust_current_plist:
                raise ValueError("重建记录会把当前磁盘 plist 作为可信来源；请显式添加 --trust-current-plist")
            if not plist_path.exists() and not plist_path.is_symlink():
                raise RuntimeError("plist 缺失，无法重建管理记录；" + state_recovery_hint(
                    args.label, "record_corrupt_plist_missing"))
            description = validate_description(args.description, hint="重建管理记录")
            _validate_repair_metadata_storage(args.label)
            ensure_repairable_record(meta_path)
            try:
                _, existing_metadata = inspect_metadata(args.label)
            except RuntimeError:
                # 只有安全的普通文件在解析/结构校验失败时才允许显式重建。
                existing_metadata = None
            else:
                if existing_metadata is not None:
                    raise RuntimeError("管理记录当前有效；rebuild-record 只适用于损坏的管理记录")
            try:
                data = inspect_plist(plist_path, args.label)
            except RuntimeError as exc:
                raise RuntimeError(str(exc) + "；" + managed_file_recovery_hint(
                    args.label, recognizable=False, path=plist_path)) from exc
            if data is None:
                raise RuntimeError("plist 缺失；无法从磁盘重建管理记录")
            payload = plistlib.loads(data)
            mode = payload_mode(payload)
            rebuilt = make_metadata(args.label, plist_path, data, description, mode,
                                     source_file=None, old=None, registered_now=False)
            rebuilt["registration_snapshot_unknown"] = True
            rebuilt["registration_snapshot_unknown_reason"] = "rebuilt"
            write_metadata(args.label, rebuilt)
            print(f"已根据当前托管 plist 重建管理记录：{args.label}")
            print("原源脚本关联和注册快照无法恢复；当前任务未被重载，手动 run 会被拒绝，需核实后重新注册。")
            return 0

    raise ValueError("未知修复操作")


def set_description(args: argparse.Namespace) -> int:
    require_macos()
    validate_label(args.label)
    description = validate_description(args.description)
    ensure_directory(LAUNCH_AGENTS_DIRECTORY)
    with management_lock():
        _, old_meta = inspect_managed_task(args.label)
        new_meta = dict(old_meta)
        new_meta.update(description=description, updated_at=timestamp_now())
        write_metadata(args.label, new_meta)
    print(f"已更新任务描述：{description}")
    print(f"Label：{args.label}")
    print("未改变 plist 或 launchd 注册状态。")
    return 0


def run(args: argparse.Namespace) -> int:
    require_macos()
    validate_label(args.label)
    ensure_directory(LAUNCH_AGENTS_DIRECTORY)
    with management_lock():
        data, record = inspect_managed_task(args.label)
        if not require_known_service_state(args.label):
            raise RuntimeError("任务未加载；请先在图形用户会话中注册。")
        if record.get("registration_snapshot_unknown"):
            raise RuntimeError(registration_unknown_text(record) + "；为防止执行旧命令，请核对后显式重新注册。")
        registered = record.get("last_registered_sha256")
        # “复位为未注册”不能推导出内存配置可信，也不能绕过运行保护。
        if registered is None:
            raise RuntimeError("没有由本工具维护的可信注册快照；无法保证已加载任务的命令是当前磁盘配置，请先显式重新注册。")
        if registered != sha256_bytes(data):
            raise RuntimeError("检测到磁盘配置与最近注册快照不同；可能执行旧命令。请先核实并重新注册，再手动运行")
        launchctl("kickstart", service_target(args.label))
    print(f"已请求任务 {args.label} 执行一次；若已运行，不保证启动新实例。")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    """先移除管理记录再删除 plist；任何一步失败都分别尝试恢复。"""
    require_macos()
    validate_label(args.label)
    if not LAUNCH_AGENTS_DIRECTORY.exists():
        _, record = inspect_metadata(args.label)
        if record is not None:
            raise RuntimeError("存在孤立管理记录，但 plist 目录缺失；拒绝卸载任何未知服务；"
                               + state_recovery_hint(args.label, "record_ok_plist_missing"))
        print("未找到 plist 目录；不会操作未知服务。")
        return 0
    ensure_directory(LAUNCH_AGENTS_DIRECTORY)
    with management_lock():
        # 归属检查负责区分“有效模板但哈希漂移”和“模板无法识别”。
        # 不再对所有异常无条件追加删除指引，避免诱导用户丢弃可恢复任务。
        old, _ = inspect_managed_task(args.label)
        assert old is not None
        old_meta_bytes, old_record = inspect_metadata(args.label)
        assert old_meta_bytes is not None and old_record is not None
        path = LAUNCH_AGENTS_DIRECTORY / f"{args.label}.plist"
        meta = metadata_path(args.label)
        was_loaded = require_known_service_state(args.label)
        if was_loaded:
            try:
                launchctl("bootout", service_target(args.label))
                if require_known_service_state(args.label):
                    raise RuntimeError("停止命令返回后服务仍然加载，拒绝删除文件")
            except (RuntimeError, OSError) as exc:
                # bootout 可能部分成功；此时磁盘 plist 尚未动过，可据此尝试恢复。
                message = f"停止任务失败：{exc}"
                try:
                    if not require_known_service_state(args.label):
                        launchctl("bootstrap", domain_target(), str(path))
                        record_restored_registration(args.label, old)
                        if payload_mode(plistlib.loads(old)) in {"login", "custom"}:
                            message += "；" + restoration_warning(old)
                except (RuntimeError, OSError) as restore_exc:
                    message += f"；重新注册失败：{restore_exc}"
                raise RuntimeError(message) from exc
        errors: list[str] = []
        needs_restore_live = was_loaded
        try:
            meta.unlink()
            path.unlink()
        except (OSError, RuntimeError) as exc:
            errors.append(f"卸载文件失败：{exc}")
            try:
                if inspect_plist(path, args.label) != old:
                    atomic_write(path, old)
            except (OSError, RuntimeError) as recovery_exc:
                errors.append(f"恢复 plist 失败：{recovery_exc}")
            try:
                if not meta.exists():
                    ensure_private_metadata_directory()
                    atomic_write(meta, old_meta_bytes)
            except (OSError, RuntimeError) as recovery_exc:
                errors.append(f"恢复管理记录失败：{recovery_exc}")
            if needs_restore_live:
                try:
                    if inspect_plist(path, args.label) != old:
                        raise RuntimeError("plist 未完整恢复，不能重新注册")
                    if not require_known_service_state(args.label):
                        launchctl("bootstrap", domain_target(), str(path))
                        record_restored_registration(args.label, old)
                        if payload_mode(plistlib.loads(old)) in {"login", "custom"}:
                            errors.append(restoration_warning(old))
                except (OSError, RuntimeError) as recovery_exc:
                    errors.append(f"重新注册失败：{recovery_exc}")
            raise RuntimeError("；".join(errors)) from exc
    print(f"已卸载任务：{args.label}")
    print("已删除 plist 与管理记录；历史日志会保留。")
    # 卸载不会清理 launchctl 的持久 Label 覆盖；仅在明确识别到 disabled 时提示。
    state, _ = disabled_override_status(args.label)
    if state == "disabled":
        print(disabled_uninstall_notice(args.label, old_record["mode"]))
    return 0

class ChineseArgumentParser(argparse.ArgumentParser):
    """将 argparse 的帮助与参数错误统一为中文。"""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # 固定中文帮助分组标题，减少不同 locale 的显示差异。
        self._optionals.title = "选项"
        self._positionals.title = "位置参数"

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：").replace("用法:", "用法：")

    def format_help(self) -> str:
        text = super().format_help()
        return (text.replace("usage:", "用法：")
                    .replace("用法:", "用法：")
                    .replace("options:", "选项：")
                    .replace("positional arguments:", "位置参数：")
                    .replace("选项:", "选项：")
                    .replace("位置参数:", "位置参数："))

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        # argparse 会优先报告缺少必需参数，可能遮蔽同一命令中的未知选项。
        # 在这种情况下优先指出未知的 --flag，避免用户盲试。
        if "required" in message.casefold():
            unknown = self._unknown_option_from_argv()
            if unknown:
                message = f"unrecognized arguments: {unknown}"
        detail = translate_argparse_error(message)
        self.exit(2, f"错误：{detail}。请运行 --help 查看说明。\n")

    def _unknown_option_from_argv(self) -> str | None:
        known = self._option_string_actions
        args = list(sys.argv[1:])
        # 子命令解析器的 sys.argv 中仍有子命令名称；只检查选项 token。
        skip_values = 0
        for token in args:
            if skip_values:
                skip_values -= 1
                continue
            if token == "--":
                break
            option = token.split("=", 1)[0]
            action = known.get(option)
            if action is not None:
                if "=" not in token:
                    nargs = action.nargs
                    if nargs is None:
                        skip_values = 1
                    elif isinstance(nargs, int) and nargs > 0:
                        skip_values = nargs
                continue
            if token.startswith("-") and token != "-" and option not in known:
                # 负数作为已知参数值时会被前一个选项的 skip_values 跳过。
                return token
        return None


def translate_argparse_error(message: str) -> str:
    """把 argparse 常见英文错误转换成具体、可纠正的中文提示。"""
    value = message.strip()
    match = re.fullmatch(r"unrecognized arguments?:\s*(.+)", value)
    if match:
        return f"未知参数：{match.group(1)}"
    match = re.fullmatch(r"the following arguments are required:\s*(.+)", value)
    if match:
        return f"缺少必需参数：{match.group(1)}"
    match = re.fullmatch(r"one of the arguments (.+) is required", value)
    if match:
        return f"必须提供以下参数之一：{match.group(1)}"
    match = re.fullmatch(r"argument (.+?): invalid int value: (.+)", value)
    if match:
        return f"参数 {match.group(1)} 必须是整数，收到：{match.group(2)}"
    match = re.fullmatch(r"argument (.+?): invalid choice: (.+?) \(choose from (.+)\)", value)
    if match:
        return f"参数 {match.group(1)} 的值无效：{match.group(2)}（可选值：{match.group(3)}）"
    match = re.fullmatch(r"argument (.+?): expected one argument", value)
    if match:
        return f"参数 {match.group(1)} 缺少值"
    match = re.fullmatch(r"argument (.+?): not allowed with argument (.+)", value)
    if match:
        return f"参数 {match.group(1)} 不能与 {match.group(2)} 同时使用"
    match = re.fullmatch(r"ambiguous option: (.+?) could match (.+)", value)
    if match:
        return f"参数 {match.group(1)} 不明确，可能匹配：{match.group(2)}"
    # 不将未知的 argparse 英文原文泄露到中文 CLI；常见参数错误已在上面提供细节。
    return "参数无效或组合不正确；请检查参数名称、必需参数、参数值和互斥关系"


def add_help(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出")


def make_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        description="统一管理本 Skill 的固定模板与自定义 XML LaunchAgent。",
        add_help=False,
    )
    add_help(parser)
    sub = parser.add_subparsers(dest="action", required=True, parser_class=ChineseArgumentParser)

    p = sub.add_parser("install", help="安装或更新登录自启动、日历定时任务", add_help=False,
                       description="安装固定模板 LaunchAgent，并记录任务描述、源脚本和配置快照。")
    add_help(p)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--command", help="完整命令；复杂引号建议使用 --command-stdin")
    source.add_argument("--command-stdin", action="store_true", help="从标准输入原样读取完整命令")
    source.add_argument("--script", help="可执行脚本的绝对路径，要求有效 shebang")
    p.add_argument("--description", help="必填：说明任务用途，最多 500 字；更新时可省略以保留旧描述")
    p.add_argument("--name", help="用户指定的任务名称；相同名称在登录与定时模式下使用不同 Label")
    p.add_argument("--working-directory", help="绝对工作目录；默认用户主目录")
    p.add_argument("--mode", choices=["login", "calendar"], help="任务模式：login 登录自启；calendar 日历定时")
    p.add_argument("--time", action="append", help="定时 HH:MM；可重复或逗号分隔，定时模式默认 09:00")
    p.add_argument("--weekday", action="append", help="可选星期 sun..sat 或 0..7；可重复或逗号分隔")
    p.add_argument("--month-day", type=int, help="可选每月日期 1..31，不能与 --weekday 同用")
    p.add_argument("--month", type=int, help="可选月份 1..12，必须同时提供 --month-day")
    p.add_argument("--keep-alive", action="store_true", help="仅登录模式：任务退出后自动重启；必须明确要求")
    timing = p.add_mutually_exclusive_group()
    timing.add_argument("--start-now", action="store_true", help="明确要求现在注册/启动；登录模式会触发 RunAtLoad")
    timing.add_argument("--defer", action="store_true", help="仅定时模式：只写文件，等待下次图形登录注册")
    p.add_argument("--replace", action="store_true", help="明确更新已有同模式任务时允许替换 plist")
    p.add_argument("--dry-run", action="store_true", help="只预览 XML 和目标路径，不写文件、不调用 launchctl")
    p.set_defaults(handler=install)

    p = sub.add_parser("install-plist", help="把扩展 XML/binary plist 导入统一托管", add_help=False,
                       description="自动分配独立托管 Label、日志和管理记录；默认只写盘，不改变 XML 触发条件。")
    add_help(p)
    p.add_argument("--input", required=True, help="普通工作目录中的 XML/binary plist 文件；不会直接接管自动加载目录中的任务")
    p.add_argument("--name", required=True, help="稳定任务名称；同名 XML 更新使用相同自定义 Label")
    p.add_argument("--description", help="新任务必须提供用途说明；更新时可省略")
    p.add_argument("--replace", action="store_true", help="明确更新此名称对应的自定义任务")
    p.add_argument("--activate", action="store_true", help="明确同意注册；XML 触发条件可能立即执行命令，不额外 kickstart")
    p.add_argument("--dry-run", action="store_true", help="预览最终托管 plist，不写文件、不调用 launchctl")
    p.set_defaults(handler=install_plist)

    p = sub.add_parser("register", help="按 Label 注册/重注册磁盘配置，并刷新快照", add_help=False)
    add_help(p)
    p.add_argument("--label", required=True, help="已有有效管理记录的 Label")
    p.add_argument("--allow-execution", action="store_true", help="确认接受停止旧实例、注册可能触发执行的副作用")
    p.set_defaults(handler=register_task)

    p = sub.add_parser("list", help="列出本 Skill 管理的任务及描述、状态和配置漂移", add_help=False,
                       description="从本 Skill 管理记录枚举固定与自定义任务，不接管其他 LaunchAgent。")
    add_help(p)
    p.set_defaults(handler=list_agents)

    p = sub.add_parser("status", help="查询一个托管任务的状态和配置同步情况", add_help=False)
    add_help(p)
    p.add_argument("--label", required=True, help="完整 Label，必须来自本 Skill 的安装或 list 输出")
    p.set_defaults(handler=status)

    p = sub.add_parser("describe", help="更新已有托管任务的用途描述", add_help=False)
    add_help(p)
    p.add_argument("--label", required=True, help="完整 Label，必须来自本 Skill 的安装或 list 输出")
    p.add_argument("--description", required=True, help="新的任务用途说明，最多 500 字")
    p.set_defaults(handler=set_description)

    p = sub.add_parser("run", help="明确请求一个已加载的托管任务执行一次", add_help=False)
    add_help(p)
    p.add_argument("--label", required=True, help="完整 Label，必须来自本 Skill 的安装或 list 输出")
    p.set_defaults(handler=run)

    p = sub.add_parser("uninstall", help="卸载一个托管任务，保留日志", add_help=False)
    add_help(p)
    p.add_argument("--label", required=True, help="完整 Label，必须来自本 Skill 的安装或 list 输出")
    p.set_defaults(handler=uninstall)

    p = sub.add_parser("repair", help="显式修复外部改动、孤立或损坏的管理记录", add_help=False,
                       description="修复前必须核对 Label 和当前 plist。该命令不会自动接管未知任务。")
    add_help(p)
    p.add_argument("--label", required=True, help="完整 Label；损坏记录可从文件名或 list 错误中取得")
    p.add_argument("--action", dest="repair_action", required=True,
                   choices=["accept-plist", "remove-orphan", "rebuild-record", "reset-snapshot", "drop-unrecognized"],
                   help="accept-plist 接受有效模板改动；remove-orphan 清理孤立记录；rebuild-record 重建 JSON；reset-snapshot 放弃快照；drop-unrecognized 清理不再符合模板的托管任务")
    p.add_argument("--trust-current-plist", action="store_true",
                   help="明确确认当前磁盘 plist 是可信来源（accept-plist/rebuild-record/reset-snapshot 必需）")
    p.add_argument("--confirm-stop", action="store_true",
                   help="明确允许停止仍加载的孤立服务并删除管理记录（remove-orphan 必需）")
    p.add_argument("--confirm-delete", action="store_true",
                   help="确认停止服务并删除变更后的托管 plist 与记录（drop-unrecognized 必需）")
    p.add_argument("--description", help="重建 JSON 记录时必需的新用途描述")
    p.set_defaults(handler=repair)
    return parser


def main() -> int:
    if sys.version_info < (3, 10):
        current = ".".join(str(part) for part in sys.version_info[:3])
        print(f"错误：本工具需要 Python 3.10 或更高版本；当前版本为 {current}。请使用 python3.10+ 重新执行。", file=sys.stderr)
        return 2
    try:
        args = make_parser().parse_args()
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return args.handler(args)
    except (ValueError, RuntimeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
