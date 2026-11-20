#!/usr/bin/env python3
"""将用户提供的 Apple plist XML 转换为独立 .plist 文件，不注册或管理任务。

此脚本只负责转换和安全写入；不调用 launchctl，也不创建本 Skill 的管理记录。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from pathlib import Path
from xml.parsers.expat import ExpatError
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 2 * 1024 * 1024
RESERVED_LABEL_PREFIX = "local.my.agents."


class ChineseParser(argparse.ArgumentParser):
    """固定中文帮助标题，并保留有针对性的参数错误信息。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._optionals.title = "选项"
        self._positionals.title = "位置参数"

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：").replace("用法:", "用法：")

    def format_help(self) -> str:
        return (super().format_help()
                .replace("usage:", "用法：")
                .replace("options:", "选项：")
                .replace("positional arguments:", "位置参数："))

    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:"):
            detail = "未知参数：" + message.partition(":")[2].strip()
        elif message.startswith("the following arguments are required:"):
            detail = "缺少必需参数：" + message.partition(":")[2].strip()
        elif re.search(r"argument (.+?): expected one argument", message):
            match = re.search(r"argument (.+?): expected one argument", message)
            detail = f"参数 {match.group(1)} 缺少值" if match else "参数缺少值"
        elif "not allowed with argument" in message:
            detail = "参数互斥：--xml 与 --input 只能选择一种来源"
        elif "invalid choice" in message:
            detail = "参数值无效：" + ("输出格式必须为 xml 或 binary" if "--format" in message else "请查看 --help 中的可选值")
        else:
            detail = "参数无效或组合不正确；请检查 --xml、--input、--output 和 --format"
        self.print_usage(sys.stderr)
        self.exit(2, f"错误：{detail}\n")


def parser_factory() -> argparse.ArgumentParser:
    parser = ChineseParser(
        description="将 Apple 属性列表 XML 校验并序列化为 .plist 文件；只转换，不安装或加载 LaunchAgent。",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--xml", help="直接传入 plist XML 文本（复杂内容建议改用 --input -）")
    source.add_argument("--input", help="读取 XML 文件路径；使用 - 从标准输入读取 XML")
    parser.add_argument("--output", required=True, help="目标 .plist 文件路径，父目录必须已存在")
    parser.add_argument("--format", choices=("xml", "binary"), default="xml",
                        help="输出格式：xml（默认）或 binary（二进制 plist）")
    parser.add_argument("--overwrite", action="store_true",
                        help="明确允许替换已存在的普通 plist 文件；不会允许覆盖本 Skill 托管命名空间")
    return parser


def read_xml(args: argparse.Namespace) -> bytes:
    """有界读取 XML，阻止把超大输入直接载入内存。"""
    if args.xml is not None:
        data = args.xml.encode("utf-8")
    elif args.input == "-":
        data = sys.stdin.buffer.read(MAX_XML_BYTES + 1)
    else:
        input_path = Path(args.input).expanduser()
        try:
            with input_path.open("rb") as stream:
                data = stream.read(MAX_XML_BYTES + 1)
        except OSError as exc:
            raise ValueError(f"无法读取 XML 输入文件：{input_path}（系统错误码 {exc.errno}）") from exc
    if not data or not data.strip():
        raise ValueError("输入的 XML 不能为空")
    if len(data) > MAX_XML_BYTES:
        raise ValueError(f"XML 输入不能超过 {MAX_XML_BYTES // 1024 // 1024} MiB")
    return data


def validate_structure(raw: bytes) -> None:
    """检查真实 plist XML 文档结构，避免 plistlib 宽松解析时丢失 XML 内容。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("XML 文档结构不完整或格式有误") from exc
    if root.tag != "plist" or len(root) != 1:
        raise ValueError("Apple plist XML 必须以 <plist> 为根节点，且恰好包含一个属性列表对象")
    if (root.text or "").strip() or (root[0].tail or "").strip():
        raise ValueError("plist 根节点中存在不属于属性列表的数据")

    def validate_value(node: ET.Element, depth: int = 0) -> None:
        # 以 dict/array 容器为一层；恰好 64 层容器允许，65 层拒绝。
        # 叶子值不增加容器层数，否则 64 层 array + string 会被误拒。
        if depth >= 64 and node.tag in {"dict", "array"}:
            raise ValueError("XML 嵌套超过 64 层，已拒绝解析")
        if node.attrib:
            raise ValueError(f"属性列表值 <{node.tag}> 不允许额外 XML 属性")
        if node.tag == "dict":
            if (node.text or "").strip() or len(node) % 2:
                raise ValueError("dict 必须仅包含成对的 key 与值节点")
            seen: set[str] = set()
            for index in range(0, len(node), 2):
                key, value = node[index], node[index + 1]
                if key.tag != "key" or len(key) or key.attrib:
                    raise ValueError("dict 中必须以 <key> 和属性列表值成对出现")
                name = key.text or ""
                if name in seen:
                    raise ValueError(f"dict 存在重复 key：{name}")
                seen.add(name)
                if (key.tail or "").strip():
                    raise ValueError("dict 的 key 节点后存在多余文本")
                validate_value(value, depth + 1)
                if (value.tail or "").strip():
                    raise ValueError("dict 的值节点后存在多余文本")
        elif node.tag == "array":
            if (node.text or "").strip():
                raise ValueError("array 节点中存在不属于属性列表的数据")
            for value in node:
                validate_value(value, depth + 1)
                if (value.tail or "").strip():
                    raise ValueError("array 的值节点后存在多余文本")
        elif node.tag in {"string", "integer", "real", "date", "data", "true", "false"}:
            if len(node):
                raise ValueError(f"标量节点 <{node.tag}> 不允许包含嵌套 XML 元素")
            if node.tag in {"true", "false"} and (node.text or "").strip():
                raise ValueError(f"布尔节点 <{node.tag}> 不允许包含文本")
        else:
            raise ValueError(f"不支持的 plist XML 元素：<{node.tag}>")

    validate_value(root[0])


def convert_xml(raw: bytes, fmt: str) -> tuple[bytes, object]:
    """保留任意合法 plist 键，将 plist XML 转成规范 XML 或二进制 plist。"""
    # 先由 plistlib 拒绝 XML 自定义实体，再使用 ElementTree 严格验证语法结构。
    try:
        value = plistlib.loads(raw, fmt=plistlib.FMT_XML)
    except (plistlib.InvalidFileException, ExpatError, ValueError, TypeError,
            OverflowError, IndexError, KeyError) as exc:
        if isinstance(exc, ExpatError):
            detail = f"XML 语法错误（第 {exc.lineno} 行，第 {exc.offset} 列）"
        elif "missing value for key" in str(exc):
            detail = "XML 字典键缺少有效的值节点"
        else:
            detail = "XML 元素、数据类型或实体声明不符合 Apple plist 规范"
        raise ValueError(detail) from exc
    if value is None:
        raise ValueError("XML 未包含有效的属性列表根对象")
    validate_structure(raw)
    try:
        result = plistlib.dumps(
            value,
            fmt=plistlib.FMT_XML if fmt == "xml" else plistlib.FMT_BINARY,
            sort_keys=False,
        )
        if plistlib.loads(result) != value:
            raise ValueError("序列化校验不一致，已拒绝写入文件")
    except (plistlib.InvalidFileException, ExpatError, TypeError,
            OverflowError, IndexError, KeyError) as exc:
        raise ValueError("XML 数据无法无损转换为 plist") from exc
    return result, value


def output_path(raw: str, parsed: object) -> tuple[Path, bool]:
    """规范化输出目录，并隔离固定模板管理器的命名空间。"""
    proposed = Path(raw).expanduser()
    if proposed.suffix.lower() != ".plist":
        raise ValueError("输出文件必须使用 .plist 扩展名")
    try:
        parent = proposed.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"目标父目录不存在或无法访问：{proposed.parent}") from exc
    if not parent.is_dir():
        raise ValueError(f"目标父路径不是目录：{parent}")
    target = parent / proposed.name
    launch_agents = (Path.home() / "Library" / "LaunchAgents").resolve(strict=False)
    in_launch_agents = parent == launch_agents
    if (isinstance(parsed, dict) and isinstance(parsed.get("Label"), str)
            and parsed["Label"].casefold().startswith(RESERVED_LABEL_PREFIX.casefold())):
        raise ValueError("拒绝使用本 Skill 独占的托管 Label 前缀；自定义任务请使用其他 Label")
    # 文件名限制只适用于系统自动扫描的 LaunchAgents 目录；其他目录不受此规则影响。
    if in_launch_agents and target.name.casefold().startswith(RESERVED_LABEL_PREFIX.casefold()):
        raise ValueError("拒绝在 LaunchAgents 中使用本 Skill 保留的托管 plist 文件名前缀")
    return target, in_launch_agents


def check_existing(target: Path, overwrite: bool) -> None:
    """拒绝跟随符号链接或覆盖其他用户文件；默认拒绝一切覆盖。"""
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"目标不是普通文件（可能是符号链接）：{target}")
    if info.st_uid != os.getuid():
        raise ValueError(f"目标文件不属于当前用户，拒绝覆盖：{target}")
    if not overwrite:
        raise ValueError(f"目标文件已经存在；确认需要替换时才使用 --overwrite：{target}")


def lint_with_plutil(temp_path: Path) -> None:
    """macOS 使用系统 plutil 再校验一次，成功后才提交目标文件。"""
    if sys.platform != "darwin":
        return
    result = subprocess.run(
        ["/usr/bin/plutil", "-lint", str(temp_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"macOS 的 plutil 检查未通过（退出码 {result.returncode}）；未写入目标文件")


def write_atomic(target: Path, content: bytes, overwrite: bool) -> None:
    """原子提交，默认用 hard link 的 O_EXCL 类语义避免并发误覆盖。"""
    check_existing(target, overwrite)
    temp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".xml-to-plist-", suffix=".plist", dir=str(target.parent))
        temp = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        lint_with_plutil(temp)
        # 二次检查避免写入期间目标变成链接或其他用户拥有的文件。
        check_existing(target, overwrite)
        if overwrite:
            os.replace(temp, target)
        else:
            # 只要目标在提交前出现，os.link 会原子报 FileExistsError，而不是覆盖。
            try:
                os.link(temp, target)
            except FileExistsError as exc:
                raise ValueError(f"目标文件刚被其他进程创建，已拒绝覆盖：{target}") from exc
        temp.unlink(missing_ok=True)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


@contextmanager
def open_audit_log() -> Iterator[int]:
    """打开仅追加的 0600 转换审计日志，不修改任何已有记录。

    使用 O_NOFOLLOW 和私有目录防止误向符号链接写入审计日志。
    文件所有者仍可以手动修改；这是意外操作审计，不是防篡改安全日志。
    """
    root = Path.home() / "Library" / "Application Support" / "macOSLaunchAgentSkill"
    if root.is_symlink():
        raise ValueError(f"拒绝写入符号链接审计目录：{root}")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"无法安全创建转换审计目录：{root}（系统错误码 {exc.errno}）") from exc
    root_info = root.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
            or root_info.st_mode & 0o077):
        raise ValueError(f"审计目录权限不安全，需为当前用户所有且权限为 0700：{root}")
    path = root / "conversions.log"
    if path.is_symlink():
        raise ValueError(f"转换审计日志是符号链接，拒绝写入：{path}")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"无法安全打开转换审计日志：{path}（系统错误码 {exc.errno}）") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError(f"转换审计日志不是当前用户的私有普通文件：{path}")
        # 整次转换持有日志锁，防止并发进程的多段追加相互交错。
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def audit_context(target: Path, parsed: object, in_agents: bool, fmt: str) -> dict[str, object]:
    """只记录命令程序名和参数摘要，不把完整命令/令牌泄露到日志中。"""
    label = parsed.get("Label") if isinstance(parsed, dict) else None
    if not isinstance(label, str):
        label = None
    program = "未提供"
    command: object = None
    if isinstance(parsed, dict):
        argv = parsed.get("ProgramArguments")
        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            command = argv
            program = Path(argv[0]).name or argv[0]
        elif isinstance(parsed.get("Program"), str):
            command = parsed["Program"]
            program = Path(command).name or command
    # 对任意合法 plist 类型都可序列化摘要（包括 data/date、嵌套字典）；
    # 避免 JSON 对 bytes/datetime 抛 TypeError，也不记录可能含秘密的完整参数。
    command_digest = (hashlib.sha256(plistlib.dumps(command, fmt=plistlib.FMT_BINARY,
                                                   sort_keys=False)).hexdigest()[:16]
                      if command is not None else None)
    return {
        "output": str(target), "source_label": label, "command_summary": program,
        "command_sha256_prefix": command_digest, "in_launch_agents": in_agents,
        "format": fmt,
    }


def append_audit(fd: int, context: dict[str, object], phase: str) -> None:
    """append-only JSONL；准备阶段先入日志，防止结果文件无记录。"""
    event = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
             "phase": phase, **context}
    data = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    offset = 0
    while offset < len(data):
        count = os.write(fd, data[offset:])
        if count <= 0:
            raise OSError("转换审计日志写入不完整")
        offset += count
    os.fsync(fd)


def main() -> int:
    if sys.version_info < (3, 10):
        current = ".".join(str(x) for x in sys.version_info[:3])
        print(f"错误：需要 Python 3.10+；当前 Python 为 {current}。", file=sys.stderr)
        return 2
    try:
        args = parser_factory().parse_args()
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        raw = read_xml(args)
        data, parsed = convert_xml(raw, args.format)
        target, in_agents = output_path(args.output, parsed)
        if in_agents or any(part.name.casefold() in {"launchagents", "launchdaemons"} for part in target.parents):
            raise ValueError("转换器拒绝写入 LaunchAgents/LaunchDaemons 自动加载目录；请先导出到普通目录，再用 launch_agent.py install-plist 托管安装")
        # 先确认目标可写及审计记录可安全追加；异常输入不应产生记录或文件。
        check_existing(target, args.overwrite)
        context = audit_context(target, parsed, in_agents, args.format)
        with open_audit_log() as fd:
            append_audit(fd, context, "prepared")
            try:
                write_atomic(target, data, args.overwrite)
            except (ValueError, OSError):
                append_audit(fd, context, "failed")
                raise
            try:
                append_audit(fd, context, "written")
            except OSError as exc:
                raise RuntimeError(f"plist 文件已写入 {target}，但审计完成记录失败；日志仍保留 prepared 条目，请人工核对") from exc
        print(f"plist 转换成功：{target}")
        print(f"输出格式：{'XML' if args.format == 'xml' else '二进制'}；未调用 launchctl，也未创建本 Skill 的管理记录。")
        print("需要安装为托管任务时，使用 launch_agent.py install-plist --input 此文件 --name 稳定名称 --description 用途。")
        return 0
    except (ValueError, RuntimeError, OSError, UnicodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
