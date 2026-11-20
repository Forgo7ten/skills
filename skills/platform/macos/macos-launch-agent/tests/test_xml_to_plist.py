"""独立 XML→plist 扩展入口的离线测试，不调用用户真实 launchctl。"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_FILE = Path(__file__).resolve().parents[1] / "scripts" / "xml_to_plist.py"
spec = importlib.util.spec_from_file_location("xml_to_plist_extension", MODULE_FILE)
assert spec and spec.loader
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


class XmlToPlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.output = self.root / "converted.plist"
        self.content = {
            "Label": "com.example.custom",
            "ProgramArguments": ["/bin/echo", "hello 世界"],
            "StartInterval": 65,
            "WatchPaths": ["/tmp/example", "/Users/example/Documents"],
            "RunAtLoad": False,
            "CustomUnknownFutureKey": {"Enabled": True, "Attempts": 4},
        }
        self.xml = plistlib.dumps(self.content, fmt=plistlib.FMT_XML, sort_keys=False)

    def invoke(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        return subprocess.run([sys.executable, str(MODULE_FILE), *args],
                              input=input, capture_output=True, env=env, check=False)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn("转换成功".encode(), result.stdout)

    def test_xml_inline_preserves_all_custom_launchd_keys(self):
        result = self.invoke("--xml", self.xml.decode(), "--output", str(self.output))
        self.assert_success(result)
        self.assertEqual(plistlib.loads(self.output.read_bytes()), self.content)
        self.assertTrue(self.output.read_bytes().startswith(b"<?xml"))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "conversions.log").is_file())
        self.assertFalse((self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents").exists())

    def test_stdin_supports_heredoc_and_unicode(self):
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assert_success(result)
        self.assertEqual(plistlib.loads(self.output.read_bytes()), self.content)

    def test_input_file_to_binary_format(self):
        source = self.root / "source.xml"
        source.write_bytes(self.xml)
        result = self.invoke("--input", str(source), "--output", str(self.output),
                             "--format", "binary")
        self.assert_success(result)
        self.assertTrue(self.output.read_bytes().startswith(b"bplist00"))
        self.assertEqual(plistlib.loads(self.output.read_bytes()), self.content)

    def test_unrecognized_root_rejected_without_output(self):
        value = self.xml.replace(b"<plist version=\"1.0\">", b"<custom>").replace(b"</plist>", b"</custom>")
        result = self.invoke("--input", "-", "--output", str(self.output), input=value)
        self.assertEqual(result.returncode, 1)
        self.assertIn("根节点".encode(), result.stderr)
        self.assertFalse(self.output.exists())

    def test_multiple_root_values_rejected(self):
        value = b'<plist version="1.0"><string>a</string><string>b</string></plist>'
        result = self.invoke("--input", "-", "--output", str(self.output), input=value)
        self.assertEqual(result.returncode, 1)
        self.assertIn("恰好".encode(), result.stderr)

    def test_duplicate_keys_rejected_to_prevent_silent_loss(self):
        value = b'<plist version="1.0"><dict><key>A</key><integer>1</integer><key>A</key><integer>2</integer></dict></plist>'
        result = self.invoke("--input", "-", "--output", str(self.output), input=value)
        self.assertEqual(result.returncode, 1)
        self.assertIn("重复 key".encode(), result.stderr)

    def test_arbitrary_nested_xml_rejected(self):
        value = b'<plist version="1.0"><dict><key>A</key><unknown>val</unknown></dict></plist>'
        result = self.invoke("--input", "-", "--output", str(self.output), input=value)
        self.assertEqual(result.returncode, 1)
        self.assertIn("XML 字典键缺少有效的值节点".encode(), result.stderr)

    def test_entity_declaration_rejected(self):
        value = b'<!DOCTYPE plist [<!ENTITY secret "value">]><plist version="1.0"><string>&secret;</string></plist>'
        result = self.invoke("--input", "-", "--output", str(self.output), input=value)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())

    def test_oversized_source_rejected_without_output(self):
        source = self.root / "huge.xml"
        source.write_bytes(b"A" * (converter.MAX_XML_BYTES + 1))
        result = self.invoke("--input", str(source), "--output", str(self.output))
        self.assertEqual(result.returncode, 1)
        self.assertIn("不能超过".encode(), result.stderr)
        self.assertFalse(self.output.exists())

    def test_missing_or_empty_input_rejected(self):
        result = self.invoke("--input", "-", "--output", str(self.output), input=b"  ")
        self.assertEqual(result.returncode, 1)
        self.assertIn("不能为空".encode(), result.stderr)

    def test_existing_file_not_overwritten_by_default(self):
        self.output.write_text("original", encoding="utf-8")
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("--overwrite".encode(), result.stderr)
        self.assertEqual(self.output.read_text(), "original")

    def test_explicit_overwrite_replaces_only_selected_output(self):
        self.output.write_text("old", encoding="utf-8")
        result = self.invoke("--input", "-", "--output", str(self.output), "--overwrite", input=self.xml)
        self.assert_success(result)
        self.assertEqual(plistlib.loads(self.output.read_bytes()), self.content)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_existing_symlink_never_overwritten(self):
        other = self.root / "other.plist"
        other.write_text("do not touch", encoding="utf-8")
        self.output.symlink_to(other)
        result = self.invoke("--input", "-", "--output", str(self.output), "--overwrite", input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(other.read_text(), "do not touch")

    def test_dangling_symlink_never_overwritten(self):
        self.output.symlink_to(self.root / "missing.plist")
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.output.is_symlink())

    def test_invalid_output_extension_rejected(self):
        result = self.invoke("--input", "-", "--output", str(self.root / "wrong.txt"), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn(".plist".encode(), result.stderr)

    def test_missing_parent_dir_not_automatically_created(self):
        output = self.root / "missing" / "job.plist"
        result = self.invoke("--input", "-", "--output", str(output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())
        self.assertFalse(output.parent.exists())

    def test_reserved_managed_label_filename_cannot_be_written(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        path = agents / "local.my.agents.custom-1234abcd.plist"
        result = self.invoke("--input", "-", "--output", str(path), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("保留".encode(), result.stderr)
        self.assertFalse(path.exists())

    def test_reserved_managed_prefix_is_case_insensitive_on_macos(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        path = agents / "LOCAL.MY.AGENTS.custom-1234abcd.plist"
        result = self.invoke("--input", "-", "--output", str(path), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(path.exists())
        content = dict(self.content)
        content["Label"] = "LOCAL.MY.AGENTS.hidden-1234abcd"
        result = self.invoke("--input", "-", "--output", str(agents / "com.example.plist"),
                             input=plistlib.dumps(content, fmt=plistlib.FMT_XML))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((agents / "com.example.plist").exists())

    def test_reserved_managed_label_cannot_be_hidden_under_custom_filename(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        content = dict(self.content)
        content["Label"] = "local.my.agents.other-1234abcd"
        xml = plistlib.dumps(content, fmt=plistlib.FMT_XML)
        path = agents / "com.example.fake-managed.plist"
        result = self.invoke("--input", "-", "--output", str(path), input=xml)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(path.exists())

    def test_unknown_argument_cannot_bypass_directory_protection(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        path = agents / "com.example.custom.plist"
        result = self.invoke("--input", "-", "--output", str(path), "--unsupported-option", input=self.xml)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未知参数".encode(), result.stderr)
        self.assertIn("--unsupported-option".encode(), result.stderr)
        self.assertFalse(path.exists())
        self.assertFalse((self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents").exists())

    def test_help_and_bad_arg_errors_are_chinese(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("选项".encode(), result.stdout)
        result = self.invoke("--input", "-", "--output", str(self.output), "--bogus", input=self.xml)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未知参数".encode(), result.stderr)
        self.assertIn("--bogus".encode(), result.stderr)

    def test_plutil_failure_does_not_write_file(self):
        class FakeResult:
            returncode = 1
        with patch.object(converter.sys, "platform", "darwin"), \
                patch.object(converter.subprocess, "run", return_value=FakeResult()) as run:
            with self.assertRaisesRegex(ValueError, "plutil 检查未通过"):
                converter.write_atomic(self.output, self.xml, overwrite=False)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".xml-to-plist-*")), [])
        self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/plutil", "-lint"])

    def test_python_below_310_is_blocked(self):
        with patch.object(converter.sys, "version_info", (3, 9, 21)), \
                patch.object(converter.sys, "stderr", io.StringIO()) as err:
            code = converter.main()
            self.assertEqual(code, 2)
            self.assertIn("Python 3.10+", err.getvalue())
        self.assertFalse(self.output.exists())

    def test_concurrent_creation_is_refused_without_clobber(self):
        with patch.object(converter.os, "link", side_effect=FileExistsError("already exists")):
            with self.assertRaisesRegex(ValueError, "刚被其他进程创建"):
                converter.write_atomic(self.output, self.xml, overwrite=False)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".xml-to-plist-*")), [])

    def test_nested_above_64_levels_rejected(self):
        xml = b'<plist version="1.0">' + b'<array>' * 65 + b'<string>hi</string>' + b'</array>' * 65 + b'</plist>'
        result = self.invoke("--input", "-", "--output", str(self.output), input=xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("超过 64 层".encode(), result.stderr)


    def audit_entries(self):
        path = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "conversions.log"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_converter_refuses_automatic_launch_agents_directory(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        target = agents / "com.example.custom.plist"
        result = self.invoke("--input", "-", "--output", str(target), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("install-plist".encode(), result.stderr)
        self.assertFalse(target.exists())
        self.assertFalse((self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill").exists())

    def test_export_creates_private_append_only_conversion_audit(self):
        target = self.root / "com.example.custom.plist"
        self.assert_success(self.invoke("--input", "-", "--output", str(target), input=self.xml))
        events = self.audit_entries()
        self.assertEqual([item["phase"] for item in events], ["prepared", "written"])
        self.assertEqual(events[1]["source_label"], "com.example.custom")
        self.assertNotIn("label", events[1])  # 未导入托管，不能冒充已分配管理 Label。
        self.assertFalse(events[1]["in_launch_agents"])
        self.assertEqual(events[1]["command_summary"], "echo")
        self.assertTrue(events[1]["command_sha256_prefix"])
        self.assertEqual(events[1]["output"], str(target.resolve()))
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((root / "conversions.log").stat().st_mode & 0o777, 0o600)
        self.assertFalse((root / "Agents").exists())

    def test_export_without_source_label_reports_none_not_managed_identity(self):
        content = {"ProgramArguments": ["/bin/true"], "RunAtLoad": False}
        target = self.root / "no-label.plist"
        self.assert_success(self.invoke("--input", "-", "--output", str(target),
                                        input=plistlib.dumps(content)))
        event = self.audit_entries()[-1]
        self.assertIn("source_label", event)
        self.assertIsNone(event["source_label"])
        self.assertNotIn("label", event)

    def test_missing_xml_file_has_chinese_error_and_no_audit(self):
        missing = self.root / "missing.xml"
        result = self.invoke("--input", str(missing), "--output", str(self.output))
        self.assertEqual(result.returncode, 1)
        self.assertIn("无法读取 XML 输入文件".encode(), result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill").exists())

    def test_unsafe_audit_file_permissions_are_refused(self):
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        path = root / "conversions.log"
        path.write_text("original\n", encoding="utf-8")
        path.chmod(0o644)
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("日志不是当前用户的私有普通文件".encode(), result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "original\n")

    def test_unknown_option_outside_agents_is_rejected(self):
        result = self.invoke("--input", "-", "--output", str(self.output),
                             "--unsupported-option", input=self.xml)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未知参数".encode(), result.stderr)
        self.assertFalse(self.output.exists())

    def test_reserved_label_blocked_even_outside_agents(self):
        content = dict(self.content)
        content["Label"] = "LOCAL.MY.AGENTS.invalid-12345678"
        result = self.invoke("--input", "-", "--output", str(self.output),
                             input=plistlib.dumps(content, fmt=plistlib.FMT_XML))
        self.assertEqual(result.returncode, 1)
        self.assertIn("托管 Label".encode(), result.stderr)
        self.assertFalse(self.output.exists())

    def test_reserved_filename_only_checked_inside_agents(self):
        output = self.root / "local.my.agents.safe-file.plist"
        self.assert_success(self.invoke("--input", "-", "--output", str(output), input=self.xml))
        self.assertTrue(output.exists())

    def test_exactly_64_nested_containers_ok_but_65_rejected(self):
        for depth in (64, 65):
            target = self.root / f"depth-{depth}.plist"
            xml = (b'<plist version="1.0">' + b'<array>' * depth + b'<string>ok</string>'
                   + b'</array>' * depth + b'</plist>')
            result = self.invoke("--input", "-", "--output", str(target), input=xml)
            if depth == 64:
                self.assert_success(result)
                self.assertTrue(target.exists())
            else:
                self.assertEqual(result.returncode, 1)
                self.assertIn("超过 64 层".encode(), result.stderr)
                self.assertFalse(target.exists())

    def test_successive_conversions_only_append_to_audit(self):
        self.assert_success(self.invoke("--input", "-", "--output", str(self.output), input=self.xml))
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        first = (root / "conversions.log").read_bytes()
        target2 = self.root / "converted-two.plist"
        self.assert_success(self.invoke("--input", "-", "--output", str(target2), input=self.xml))
        second = (root / "conversions.log").read_bytes()
        self.assertTrue(second.startswith(first))
        self.assertEqual(len(self.audit_entries()), 4)
        self.assertFalse(self.audit_entries()[0]["in_launch_agents"])

    def test_audit_path_is_symlink_rejected_without_writing_plist(self):
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        root.mkdir(parents=True, mode=0o700)
        root.chmod(0o700)
        other = self.root / "other.log"
        other.write_text("untouched", encoding="utf-8")
        (root / "conversions.log").symlink_to(other)
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(other.read_text(encoding="utf-8"), "untouched")

    def test_world_readable_audit_directory_rejected_without_writing(self):
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        root.mkdir(parents=True)
        root.chmod(0o755)
        result = self.invoke("--input", "-", "--output", str(self.output), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("0700".encode(), result.stderr)
        self.assertFalse(self.output.exists())

    def test_command_secrets_are_not_logged_in_plaintext(self):
        content = dict(self.content)
        content["ProgramArguments"] = ["/bin/sh", "-c", "echo token=do_not_log_me"]
        result = self.invoke("--input", "-", "--output", str(self.output),
                             input=plistlib.dumps(content, fmt=plistlib.FMT_XML))
        self.assert_success(result)
        text = json.dumps(self.audit_entries(), ensure_ascii=False)
        self.assertNotIn("do_not_log_me", text)
        self.assertIn('"command_summary": "sh"', text)

    def test_legal_plist_arguments_with_data_do_not_crash_audit_digest(self):
        # 转换器接受任意合法 plist 键值；审计模块不得假定 argv 全为字符串。
        content = dict(self.content)
        content["ProgramArguments"] = ["/bin/example", b"some bytes", {"option": "value"}]
        result = self.invoke("--input", "-", "--output", str(self.output),
                             input=plistlib.dumps(content, fmt=plistlib.FMT_XML))
        self.assert_success(result)
        self.assertEqual(plistlib.loads(self.output.read_bytes()), content)
        events = self.audit_entries()
        self.assertEqual(events[-1]["command_summary"], "example")
        self.assertTrue(events[-1]["command_sha256_prefix"])

    def test_postwrite_audit_failure_reports_existing_file_and_keeps_prepared_event(self):
        root = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        real_append = converter.append_audit

        def failing_append(fd, context, phase):
            if phase == "written":
                raise OSError("simulated audit commit failure")
            return real_append(fd, context, phase)

        with patch.object(converter.Path, "home", return_value=self.home), \
                patch.object(converter.sys, "argv", [str(MODULE_FILE), "--input", "-", "--output", str(self.output)]), \
                patch.object(converter.sys, "stdin", io.TextIOWrapper(io.BytesIO(self.xml))), \
                patch.object(converter.sys, "stdout", io.StringIO()), \
                patch.object(converter.sys, "stderr", io.StringIO()) as err, \
                patch.object(converter, "append_audit", side_effect=failing_append):
            self.assertEqual(converter.main(), 1)
            self.assertIn("文件已写入", err.getvalue())
        self.assertTrue(self.output.exists())
        events = [json.loads(line) for line in (root / "conversions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["phase"] for item in events], ["prepared"])

    def test_alias_path_to_agents_still_requires_flag(self):
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        alias = self.root / "launchagents_alias"
        alias.symlink_to(agents, target_is_directory=True)
        target = alias / "alias.plist"
        result = self.invoke("--input", "-", "--output", str(target), input=self.xml)
        self.assertEqual(result.returncode, 1)
        self.assertIn("install-plist".encode(), result.stderr)
        self.assertFalse((agents / "alias.plist").exists())

    def test_written_failure_does_not_erase_prepared_audit_event(self):
        envroot = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill"
        with patch.object(converter.Path, "home", return_value=self.home), \
                patch.object(converter.sys, "argv", [str(MODULE_FILE), "--input", "-", "--output", str(self.output)]), \
                patch.object(converter.sys, "stdin", io.TextIOWrapper(io.BytesIO(self.xml))), \
                patch.object(converter.sys, "stdout", io.StringIO()), \
                patch.object(converter.sys, "stderr", io.StringIO()), \
                patch.object(converter, "lint_with_plutil", side_effect=ValueError("simulated lint failed")):
            self.assertEqual(converter.main(), 1)
        self.assertFalse(self.output.exists())
        events = [json.loads(line) for line in (envroot / "conversions.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["phase"] for event in events], ["prepared", "failed"])


if __name__ == "__main__":
    unittest.main()
