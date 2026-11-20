"""No-record path classification and compact output regression tests."""
from __future__ import annotations

import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class UnmanagedListingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.metadata = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        for attribute, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("METADATA_DIRECTORY", self.metadata),
            ("LOG_DIRECTORY", self.home / "Library" / "Logs" / "LaunchAgents"),
            ("DEFAULT_WORKING_DIRECTORY", self.home),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            p = patch.object(la, attribute, value)
            p.start()
            self.addCleanup(p.stop)

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *args]), \
             patch.object(sys, "stdout", output), patch.object(sys, "stderr", error):
            rc = la.main()
        return rc, output.getvalue(), error.getvalue()

    def add(self, name, kind):
        label = la.make_label("echo inspect", name)
        path = self.agents / (label + ".plist")
        if kind == "regular":
            path.write_bytes(b"untrusted plist")
        elif kind == "directory":
            path.mkdir()
        elif kind == "symlink":
            outside = self.home / "outside-important"
            outside.write_text("do not alter", encoding="utf-8")
            path.symlink_to(outside)
        elif kind == "broken-link":
            path.symlink_to(self.home / "missing-target")
        elif kind == "fifo":
            if not hasattr(os, "mkfifo"):
                self.skipTest("FIFO is unavailable")
            os.mkfifo(path)
        return label, path

    def test_regular_entries_are_single_line_each_and_share_one_hint(self):
        entries = [self.add("unknown-" + str(i), "regular") for i in range(4)]
        self.fake.calls.clear()
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        lines = out.splitlines()
        rows = [line for line in lines if "未管理的托管命名样式 plist" in line]
        self.assertEqual(len(rows), 4)
        self.assertEqual(len([line for line in lines if "status --label" in line]), 1)
        self.assertEqual(len(lines), 6)  # four items, summary, shared hint
        for label, path in entries:
            relevant = [row for row in rows if label in row]
            self.assertEqual(len(relevant), 1)
            self.assertIn(str(path), relevant[0])
            self.assertIn("普通文件", relevant[0])
        self.assertNotIn("launchctl bootout", out)
        self.assertNotIn("手动移除该文件", out)
        self.assertEqual(self.fake.calls, [])

    def test_directory_symlinks_and_fifo_are_not_treated_as_plists(self):
        entries = [self.add("folder", "directory"), self.add("linked", "symlink"),
                   self.add("broken", "broken-link"), self.add("pipe", "fifo")]
        self.fake.calls.clear()
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out.count("异常条目（非普通文件："), 4)
        self.assertEqual(out.count("未管理的托管命名样式 plist"), 0)
        self.assertIn("0 个未管理文件", out)
        self.assertIn("4 个非普通文件异常条目", out)
        self.assertIn("0 个异常管理记录", out)
        self.assertNotIn("异常记录已报告", out)
        self.assertNotIn("launchctl bootout", out)
        self.assertNotIn("手动移除", out)
        self.assertNotIn("status --label", out)
        self.assertEqual(self.fake.calls, [])
        self.assertTrue(entries[0][1].is_dir())
        self.assertTrue(entries[1][1].is_symlink())
        self.assertEqual((self.home / "outside-important").read_text(), "do not alter")
        self.assertTrue(entries[2][1].is_symlink())
        self.assertTrue(stat.S_ISFIFO(entries[3][1].lstat().st_mode))

    def test_launch_agents_directory_symlink_refused_with_specific_guidance(self):
        self.agents.rmdir()
        real_directory = self.home / "real-launch-agents"
        real_directory.mkdir(mode=0o700)
        self.agents.symlink_to(real_directory, target_is_directory=True)
        label = la.make_label("/usr/bin/true", "directory-symlink")
        for arguments in (
            ("list",),
            ("status", "--label", label),
            ("install", "--command", "/usr/bin/true", "--name", "directory-symlink",
             "--description", "目录符号链接校验"),
        ):
            with self.subTest(arguments=arguments):
                code, out, err = self.invoke(*arguments)
                self.assertEqual(code, 1)
                self.assertIn("拒绝使用符号链接目录", err)
                self.assertIn("~/Library/LaunchAgents", err)
                self.assertIn("当前用户所有的实体目录", err)
                self.assertIn("建议权限 0700", err)
        self.assertTrue(self.agents.is_symlink())
        self.assertEqual(self.agents.resolve(), real_directory.resolve())
        self.assertEqual(list(real_directory.iterdir()), [])
        self.assertEqual(self.fake.calls, [])

    def test_status_for_no_record_regular_path_has_clean_punctuation(self):
        label, path = self.add("simple", "regular")
        self.fake.calls.clear()
        rc, out, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(len(err.splitlines()), 2)
        self.assertIn("不查询同名未知服务", err)
        self.assertIn("launchctl bootout", err)
        self.assertIn("手动移除该文件", err)
        self.assertNotIn("。；", err)
        self.assertNotIn("；。", err)
        self.assertEqual(self.fake.calls, [])
        self.assertTrue(path.exists())

    def test_status_nonregular_has_no_regular_file_deletion_steps(self):
        for kind in ("directory", "symlink", "broken-link", "fifo"):
            with self.subTest(kind=kind):
                label, path = self.add("status-" + kind, kind)
                self.fake.calls.clear()
                rc, out, err = self.invoke("status", "--label", label)
                self.assertEqual((rc, out), (1, ""))
                self.assertIn("不是可作为 plist", err)
                self.assertIn("不要按普通 plist", err)
                self.assertNotIn("launchctl bootout", err)
                self.assertNotIn("手动移除", err)
                self.assertNotIn("。；", err)
                self.assertEqual(self.fake.calls, [])
                self.assertTrue(path.is_symlink() if kind in ("symlink", "broken-link") else path.exists())

    def test_status_no_record_no_path_is_one_clean_message(self):
        label = la.make_label("echo gone", "no-file")
        rc, out, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(len(err.splitlines()), 1)
        self.assertNotIn("恢复建议：", err)
        self.assertNotIn("。；", err)
        self.assertEqual(self.fake.calls, [])

    def test_list_ignores_file_disappearing_after_scan(self):
        label, path = self.add("removed-during-scan", "regular")
        real = la.path_entry_kind
        def disappear(entry):
            if entry == path:
                path.unlink()
                return None
            return real(entry)
        with patch.object(la, "path_entry_kind", side_effect=disappear):
            rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("未找到本 Skill", out)
        self.assertNotIn(label, out)
        self.assertEqual(self.fake.calls, [])

    def test_list_reports_uninspectable_path_as_abnormal_without_hints(self):
        label, path = self.add("unreadable-during-scan", "regular")
        with patch.object(la, "path_entry_kind", side_effect=PermissionError("denied")):
            rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("类型无法确认", out)
        self.assertIn(label, out)
        self.assertNotIn("launchctl bootout", out)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
