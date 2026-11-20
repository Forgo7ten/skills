"""注册快照恢复、诊断原因及受控修复的补充回归测试。"""
from __future__ import annotations

import io
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class SnapshotRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.logs = self.home / "Library" / "Logs" / "LaunchAgents"
        self.meta = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        for patcher in (
            patch.object(la, "LAUNCH_AGENTS_DIRECTORY", self.agents),
            patch.object(la, "LOG_DIRECTORY", self.logs),
            patch.object(la, "METADATA_DIRECTORY", self.meta),
            patch.object(la, "DEFAULT_WORKING_DIRECTORY", self.home),
            patch.object(la, "require_macos", lambda: None),
            patch.object(la, "launchctl", self.fake),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def invoke(self, *argv):
        if argv and argv[0] == "install" and "--description" not in argv:
            argv = (*argv, "--description", "补充测试的业务用途")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            rc = la.main()
        return rc, out.getvalue(), err.getvalue()

    def create_job(self, *, start=False, name="snapshot"):
        arguments = ("install", "--command", "echo original", "--name", name)
        if start:
            arguments += ("--start-now",)
        code, out, err = self.invoke(*arguments)
        self.assertEqual((code, err), (0, ""))
        label = la.make_label("echo original", name, "login")
        return label, self.agents / f"{label}.plist"

    def edit_command(self, path, command="echo externally modified"):
        content = plistlib.loads(path.read_bytes())
        content["ProgramArguments"][2] = command
        path.write_bytes(la.to_plist_bytes(content))

    def test_never_registered_login_accept_does_not_invent_unknown_snapshot(self):
        label, path = self.create_job()
        self.edit_command(path)
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "accept-plist", "--trust-current-plist")[0], 0)
        _, record = la.inspect_metadata(label)
        self.assertFalse(record["registration_snapshot_unknown"])
        self.assertIsNone(record["last_registered_sha256"])
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("没有由本工具维护的注册快照", out)
        self.assertNotIn("管理记录已重建", out)
        self.assertEqual(self.invoke("describe", "--label", label, "--description", "新用途")[0], 0)
        # “install --replace”不启动旧命令，也不会凭空造出注册快照。
        before = list(self.fake.calls)
        self.assertEqual(self.invoke("install", "--command", "echo externally modified",
                                     "--name", "snapshot", "--replace")[0], 0)
        self.assertEqual(self.fake.calls, before)
        _, updated = la.inspect_metadata(label)
        self.assertFalse(updated["registration_snapshot_unknown"])
        self.assertIsNone(updated["last_registered_sha256"])
        # 模拟由系统登录时加载、但工具从未确认过配置的服务；run 必须拒绝。
        self.fake.loaded[label] = path.read_bytes()
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("没有由本工具维护的可信注册快照", err)

    def test_accept_reason_remains_until_explicit_registration_or_reset(self):
        label, path = self.create_job(start=True)
        self.edit_command(path)
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "accept-plist", "--trust-current-plist")[0], 0)
        _, record = la.inspect_metadata(label)
        self.assertTrue(record["registration_snapshot_unknown"])
        self.assertEqual(record["registration_snapshot_unknown_reason"], "accepted-plist")
        self.assertIn("已接受外部改动", self.invoke("status", "--label", label)[1])
        self.assertIn("已接受外部改动", self.invoke("run", "--label", label)[2])
        # 不显式启动的登录更新不重新注册，未知原因仍保持。
        self.assertEqual(self.invoke("install", "--command", "echo externally modified", "--name", "snapshot", "--replace")[0], 0)
        self.assertTrue(la.inspect_metadata(label)[1]["registration_snapshot_unknown"])
        before = list(self.fake.calls)
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "reset-snapshot", "--trust-current-plist")[0], 0)
        self.assertEqual(self.fake.calls, before)
        _, record = la.inspect_metadata(label)
        self.assertFalse(record["registration_snapshot_unknown"])
        self.assertIsNone(record["registration_snapshot_unknown_reason"])
        self.assertIsNone(record["last_registered_sha256"])
        self.assertIn("没有由本工具维护的注册快照", self.invoke("status", "--label", label)[1])
        self.assertIn("没有由本工具维护的可信注册快照", self.invoke("run", "--label", label)[2])
        self.assertEqual(self.invoke("describe", "--label", label, "--description", "恢复用途")[0], 0)
        self.assertEqual(self.invoke("install", "--command", "echo externally modified", "--name", "snapshot", "--replace")[0], 0)
        self.assertIsNone(la.inspect_metadata(label)[1]["last_registered_sha256"])
        # 显式 --start-now 才能得到新的可信快照；可能运行一次。
        self.assertEqual(self.invoke("install", "--command", "echo externally modified", "--name", "snapshot", "--start-now")[0], 0)
        _, record = la.inspect_metadata(label)
        self.assertFalse(record["registration_snapshot_unknown"])
        self.assertIsNotNone(record["last_registered_sha256"])
        self.assertEqual(self.invoke("run", "--label", label)[0], 0)

    def test_mode_changed_reason_is_not_rebuild(self):
        label, path = self.create_job(start=True)
        other = la.build_plist(label, "echo original", self.home, False, [{"Hour": 10, "Minute": 0}])
        path.write_bytes(la.to_plist_bytes(other))
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "accept-plist", "--trust-current-plist")[0], 0)
        self.assertEqual(la.inspect_metadata(label)[1]["registration_snapshot_unknown_reason"], "mode-changed")
        out = self.invoke("status", "--label", label)[1]
        self.assertIn("任务模式已改变", out)
        self.assertNotIn("管理记录已重建", out)
        self.assertIn("任务模式已改变", self.invoke("run", "--label", label)[2])

    def test_rebuilt_reason_field_is_mandatory(self):
        label, path = self.create_job(start=True)
        meta_path = la.metadata_path(label)
        meta_path.write_text("{ invalid JSON", encoding="utf-8")
        rc, _, err = self.invoke("repair", "--label", label, "--action", "rebuild-record", "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("重建管理记录必须提供", err)
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                     "--description", "可信的重建用途", "--trust-current-plist")[0], 0)
        record = la.inspect_metadata(label)[1]
        self.assertEqual(record["registration_snapshot_unknown_reason"], "rebuilt")
        self.assertIn("管理记录已重建", self.invoke("status", "--label", label)[1])
        self.assertIn("管理记录已重建", self.invoke("run", "--label", label)[2])
        record.pop("registration_snapshot_unknown_reason")
        la.write_metadata(label, record)
        rc, _, err = self.invoke("status", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("缺少当前格式的必需字段", err)
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("缺少当前格式的必需字段", err)

    def test_reset_requires_trust_and_current_owned_hash(self):
        label, path = self.create_job(start=True)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "reset-snapshot")
        self.assertEqual(rc, 1)
        self.assertIn("--trust-current-plist", err)
        self.edit_command(path)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "reset-snapshot", "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("内容不一致", err)
        self.assertIsNotNone(la.inspect_metadata(label)[1]["last_registered_sha256"])

    def test_list_reports_json_parser_cause_and_path_once(self):
        label, path = self.create_job()
        record_path = la.metadata_path(label)
        record_path.write_text('{"description":', encoding="utf-8")
        code, out, err = self.invoke("list")
        self.assertEqual(code, 0)
        self.assertEqual(err.count(str(record_path)), 1)
        self.assertIn("JSONDecodeError", err)
        self.assertIn("Expecting value", err)
        self.assertIn("--action rebuild-record", err)
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual(code, 1)
        self.assertEqual(err.count(str(record_path)), 1)
        self.assertIn("JSONDecodeError", err)

    def test_invalid_snapshot_reason_type_is_rejected_without_traceback(self):
        label, path = self.create_job()
        record = la.inspect_metadata(label)[1]
        record["registration_snapshot_unknown_reason"] = {"bad": True}
        la.write_metadata(label, record)
        rc, _, err = self.invoke("status", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("原因无效", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
