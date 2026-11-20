"""单一元数据格式、托管归属与严格权限的防回归测试。"""
from __future__ import annotations

import io
import json
import plistlib
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class SingleSchemaTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.home = Path(self.sandbox.name)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.logs = self.home / "Library" / "Logs" / "LaunchAgents"
        self.metadata = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.logs),
            ("METADATA_DIRECTORY", self.metadata),
            ("DEFAULT_WORKING_DIRECTORY", self.home),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            self.stack.enter_context(patch.object(la, name, value))
        self.label = la.make_label("echo current", "current", "login")
        self.plist_path = self.agents / f"{self.label}.plist"
        self.record_path = self.metadata / f"{self.label}.json"

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            rc = la.main()
        return rc, out.getvalue(), err.getvalue()

    def make_record(self):
        self.agents.mkdir(parents=True, exist_ok=True)
        la.ensure_private_metadata_directory()
        data = la.to_plist_bytes(la.build_plist(self.label, "echo current", self.home, False))
        self.plist_path.write_bytes(data)
        record = la.make_metadata(self.label, self.plist_path, data, "当前任务", "login", None, None, False)
        self.record_path.write_bytes(la.metadata_bytes(record))
        self.record_path.chmod(0o600)
        return record, data

    def test_undefined_record_field_rejected_without_data_loss(self):
        record, data = self.make_record()
        record["unexpected_field"] = "未知字段"
        payload = la.metadata_bytes(record)
        self.record_path.write_bytes(payload)
        for args in (("status", "--label", self.label),
                     ("uninstall", "--label", self.label),
                     ("repair", "--label", self.label, "--action", "rebuild-record",
                      "--trust-current-plist", "--description", "人工确认")):
            with self.subTest(args=args):
                rc, _, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn("未定义字段", err)
                self.assertEqual(self.plist_path.read_bytes(), data)
                self.assertEqual(self.record_path.read_bytes(), payload)
                self.assertEqual(self.fake.calls, [])

    def test_missing_required_current_field_does_not_silently_default(self):
        record, _ = self.make_record()
        record.pop("registration_snapshot_unknown_reason")
        self.record_path.write_bytes(la.metadata_bytes(record))
        rc, _, err = self.invoke("status", "--label", self.label)
        self.assertEqual(rc, 1)
        self.assertIn("缺少当前格式的必需字段", err)

    def test_fixed_plist_cannot_omit_current_umask(self):
        record, _ = self.make_record()
        content = plistlib.loads(self.plist_path.read_bytes())
        content.pop("Umask")
        altered = la.to_plist_bytes(content)
        self.plist_path.write_bytes(altered)
        record["disk_sha256"] = la.sha256_bytes(altered)
        self.record_path.write_bytes(la.metadata_bytes(record))
        rc, _, err = self.invoke("uninstall", "--label", self.label)
        self.assertEqual(rc, 1)
        self.assertIn("不符合本 Skill 的固定模板", err)
        self.assertTrue(self.plist_path.exists())
        self.assertEqual(self.fake.calls, [])

    def test_fixed_record_cannot_claim_custom_plist_source(self):
        record, _ = self.make_record()
        record["source_type"] = "plist"
        self.record_path.write_bytes(la.metadata_bytes(record))
        rc, _, err = self.invoke("status", "--label", self.label)
        self.assertEqual(rc, 1)
        self.assertIn("任务模式与来源类型不一致", err)

    def test_metadata_parent_must_be_private(self):
        self.make_record()
        self.metadata.parent.chmod(0o755)
        rc, _, err = self.invoke("status", "--label", self.label)
        self.assertEqual(rc, 1)
        self.assertIn("0700", err)

    def test_broken_json_value_can_still_be_rebuilt_with_confirmation(self):
        self.make_record()
        self.record_path.write_text("null", encoding="utf-8")
        rc, _, err = self.invoke("repair", "--label", self.label,
                                 "--action", "rebuild-record", "--trust-current-plist",
                                 "--description", "确认后的用途")
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(la.inspect_metadata(self.label)[1]["label"], self.label)


if __name__ == "__main__":
    unittest.main()
