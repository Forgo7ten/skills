"""模板内哈希漂移的可执行恢复指引与不可识别文件的安全清理边界。"""
from __future__ import annotations

import io
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class DriftRemediationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.logs = self.home / "Library" / "Logs" / "LaunchAgents"
        self.records = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        for key, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.logs),
            ("METADATA_DIRECTORY", self.records),
            ("DEFAULT_WORKING_DIRECTORY", self.home),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            patcher = patch.object(la, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            return la.main(), out.getvalue(), err.getvalue()

    def fixed(self, mode="login", name="drift-guide", command="echo old"):
        argv = ["install", "--name", name, "--description", "漂移修复指引测试",
                "--command", command]
        if mode == "calendar":
            argv += ["--mode", "calendar", "--time", "13:00", "--defer"]
        rc, _, err = self.invoke(*argv)
        self.assertEqual((rc, err), (0, ""), err)
        label = la.make_label(command, name, mode)
        return label, self.agents / (label + ".plist")

    @staticmethod
    def modify(path, *, key="ProgramArguments", value="echo changed"):
        obj = plistlib.loads(path.read_bytes())
        if key == "ProgramArguments":
            obj["ProgramArguments"][2] = value
        else:
            obj[key] = value
        path.write_bytes(plistlib.dumps(obj, sort_keys=False))

    def assert_recognized_hint(self, text):
        self.assertIn("仍符合托管模板", text)
        self.assertIn("--action accept-plist --trust-current-plist", text)
        self.assertIn("--action drop-unrecognized --confirm-delete", text)
        self.assertLess(text.index("accept-plist"), text.index("drop-unrecognized"))
        self.assertIn("register --label", text)

    def test_calendar_same_template_command_drift_all_paths_offer_accept_first(self):
        label, path = self.fixed(mode="calendar")
        self.modify(path)
        raw = path.read_bytes()
        operations = (
            ("status", "--label", label),
            ("uninstall", "--label", label),
            ("describe", "--label", label, "--description", "保持"),
            ("run", "--label", label),
            ("install", "--mode", "calendar", "--time", "13:00", "--defer",
             "--name", "drift-guide", "--description", "保持", "--command", "echo old",
             "--replace", "--dry-run"),
            ("install", "--mode", "calendar", "--time", "13:00", "--defer",
             "--name", "drift-guide", "--description", "保持", "--command", "echo old", "--replace"),
        )
        for cmd in operations:
            with self.subTest(cmd=cmd):
                code, out, err = self.invoke(*cmd)
                self.assertEqual(code, 0 if cmd[0] == "status" else 1, (cmd, err))
                self.assert_recognized_hint(out if cmd[0] == "status" else err)
                self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(any(call[0] in ("bootstrap", "bootout", "kickstart", "enable", "disable")
                             for call in self.fake.calls))
        code, _, err = self.invoke("repair", "--label", label, "--action", "accept-plist",
                                   "--trust-current-plist")
        self.assertEqual((code, err), (0, ""), err)
        self.assertNotIn("磁盘配置与管理记录不同", self.invoke("status", "--label", label)[1])
        code, _, err = self.invoke("register", "--label", label, "--allow-execution")
        self.assertEqual((code, err), (0, ""), err)
        self.assertEqual(self.invoke("run", "--label", label)[0], 0)

    def test_plist_binary_reencoding_is_recoverable_not_deleted(self):
        label, path = self.fixed()
        original = plistlib.loads(path.read_bytes())
        path.write_bytes(plistlib.dumps(original, fmt=plistlib.FMT_BINARY))
        self.assertEqual(plistlib.loads(path.read_bytes()), original)
        self.assert_recognized_hint(self.invoke("status", "--label", label)[1])
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "accept-plist",
                                     "--trust-current-plist")[0], 0)
        self.assertNotIn("磁盘配置与管理记录不同", self.invoke("status", "--label", label)[1])

    def test_list_briefly_names_preserve_and_discard(self):
        label, path = self.fixed()
        self.modify(path)
        rc, output, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""), err)
        self.assertIn("accept-plist", output)
        self.assertIn("drop-unrecognized", output)

    def test_extra_field_breaks_template_and_does_not_recommend_accept(self):
        label, path = self.fixed()
        self.modify(path, key="ProcessType", value="Background")
        for cmd in (("status", "--label", label), ("uninstall", "--label", label),
                    ("install", "--name", "drift-guide", "--description", "测试",
                     "--command", "echo old", "--replace", "--dry-run")):
            with self.subTest(cmd=cmd):
                rc, out, err = self.invoke(*cmd)
                self.assertEqual(rc, 0 if cmd[0] == "status" else 1)
                msg = out if cmd[0] == "status" else err
                self.assertIn("ProcessType", msg)
                self.assertIn("--action drop-unrecognized --confirm-delete", msg)
                self.assertNotIn("--action accept-plist --trust-current-plist", msg)
        self.assertTrue(path.exists())
        self.assertEqual(self.fake.calls, [])

    def test_login_run_at_load_changed_means_disable_via_uninstall_not_accept(self):
        label, path = self.fixed()
        old = path.read_bytes()
        self.modify(path, key="RunAtLoad", value=False)
        rc, out, _ = self.invoke("status", "--label", label)
        self.assertEqual(rc, 0)
        self.assertIn("登录自启动任务必须设置 RunAtLoad=true", out)
        self.assertIn("应使用 uninstall，而不是直接修改 RunAtLoad", out)
        self.assertIn("--action drop-unrecognized", out)
        self.assertNotIn("--action accept-plist --trust-current-plist", out)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "accept-plist",
                                 "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("应使用 uninstall", err)
        path.write_bytes(old)
        rc, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, err), (0, ""), err)

    def test_symlink_target_cannot_be_advised_to_drop_or_accept(self):
        label, path = self.fixed()
        stored = self.home / "stored.plist"
        path.rename(stored)
        path.symlink_to(stored)
        rc, output, _ = self.invoke("status", "--label", label)
        self.assertEqual(rc, 0)
        self.assertIn("无法确认", output)
        self.assertNotIn("--action drop-unrecognized --confirm-delete", output)
        self.assertNotIn("--action accept-plist --trust-current-plist", output)
        rc, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(rc, 1)
        self.assertNotIn("--action drop-unrecognized --confirm-delete", err)
        self.assertTrue(path.is_symlink())

    def test_missing_metadata_does_not_offer_repair_for_unknown_file(self):
        label, path = self.fixed()
        la.metadata_path(label).unlink()
        self.modify(path)
        rc, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("缺少本 Skill 的管理记录", err)
        self.assertNotIn("--action accept-plist", err)
        self.assertNotIn("--action drop-unrecognized", err)
        self.assertTrue(path.exists())

    def test_custom_payload_drift_follows_same_preserve_first_rule(self):
        source = self.home / "source.plist"
        source.write_bytes(plistlib.dumps({"ProgramArguments": ["/bin/true"], "RunAtLoad": False,
                                          "StartInterval": 180}, sort_keys=False))
        code, _, error = self.invoke("install-plist", "--name", "custom-drift", "--description", "自定义任务",
                                     "--input", str(source))
        self.assertEqual((code, error), (0, ""), error)
        label = la.make_label("", "custom-drift", "custom")
        path = self.agents / (label + ".plist")
        self.modify(path, key="StartInterval", value=600)
        self.assert_recognized_hint(self.invoke("status", "--label", label)[1])
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "accept-plist",
                                     "--trust-current-plist")[0], 0)
        self.assertEqual(self.invoke("describe", "--label", label,
                                     "--description", "已接受调度变更")[0], 0)


if __name__ == "__main__":
    unittest.main()
