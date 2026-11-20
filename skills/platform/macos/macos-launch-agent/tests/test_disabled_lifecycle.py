"""Regression coverage for persistent disabled overrides and summary consistency."""
from __future__ import annotations

import io
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures
from test_preview_disable import DisabledLaunchctl

la = fixtures.la


class DisabledLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.records = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = DisabledLaunchctl()
        for key, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.root / "Library" / "Logs" / "LaunchAgents"),
            ("METADATA_DIRECTORY", self.records),
            ("DEFAULT_WORKING_DIRECTORY", self.root),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            p = patch.object(la, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.source = self.root / "example.plist"

    def invoke(self, *args: str):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *args]), \
             patch.object(sys, "stdout", out), \
             patch.object(sys, "stderr", err):
            code = la.main()
        return code, out.getvalue(), err.getvalue()

    def calendar(self, time: str = "13:00", *extras: str):
        return self.invoke("install", "--mode", "calendar", "--name", "sticky", "--time", time,
                           "--description", "test schedule", "--command", "/bin/true", *extras)

    def login(self, *extras: str):
        return self.invoke("install", "--name", "login-sticky", "--description", "login task",
                           "--command", "/bin/true", *extras)

    def label(self, mode="calendar"):
        return la.make_label("/bin/true", "sticky" if mode == "calendar" else "login-sticky", mode)

    def test_uninstall_warns_about_retained_disabled_override_then_reinstall(self):
        self.assertEqual(self.calendar("13:00", "--defer")[0], 0)
        label = self.label()
        self.fake.overrides[label] = True
        rc, out, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("launchctl enable", out)
        self.assertIn("Label", out)
        self.assertIn("持久禁用覆盖", out)
        self.assertFalse((self.agents / (label + ".plist")).exists())
        self.assertFalse((self.records / (label + ".json")).exists())
        self.assertTrue(self.fake.overrides[label])
        self.assertNotIn("enable", [c[0] for c in self.fake.calls])
        rc, _, err = self.calendar("13:00")
        self.assertEqual(rc, 1)
        self.assertIn("Label", err)
        self.assertIn("重新安装", err)
        self.assertIn("--defer", err)
        self.assertFalse((self.agents / (label + ".plist")).exists())
        self.assertEqual(self.calendar("13:00", "--defer")[0], 0)
        self.assertTrue(self.fake.overrides[label])

    def test_disabled_calendar_replace_can_update_disk_using_defer(self):
        self.assertEqual(self.calendar("13:00", "--defer")[0], 0)
        label = self.label()
        path = self.agents / (label + ".plist")
        original = path.read_bytes()
        self.fake.overrides[label] = True
        rc, out, err = self.calendar("17:30", "--replace")
        self.assertEqual(rc, 1)
        self.assertIn("--defer", err)
        self.assertIn("install", err)
        self.assertEqual(path.read_bytes(), original)
        rc, out, err = self.calendar("17:30", "--replace", "--defer")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("17:30", out)
        self.assertEqual(plistlib.loads(path.read_bytes())["StartCalendarInterval"], {"Hour": 17, "Minute": 30})
        self.assertTrue(self.fake.overrides[label])
        self.assertNotIn(label, self.fake.loaded)

    def test_disabled_calendar_register_guidance_does_not_suggest_defer_on_register(self):
        self.assertEqual(self.calendar("13:00", "--defer")[0], 0)
        label = self.label()
        self.fake.overrides[label] = True
        code, _, err = self.invoke("register", "--label", label, "--allow-execution")
        self.assertEqual(code, 1)
        self.assertIn("--defer", err)
        self.assertIn("register", err)
        self.assertIn("不适用于 register", err)

    def test_disabled_status_verbose_but_list_compact(self):
        self.assertEqual(self.calendar("13:00", "--defer")[0], 0)
        label = self.label()
        self.fake.overrides[label] = True
        rc, listing, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("系统禁用覆盖：已禁用", listing)
        self.assertNotIn("launchctl enable", listing)
        self.assertNotIn("--defer", listing)
        rc, status, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("launchctl enable", status)
        self.assertIn("--defer", status)
        self.assertIn("已禁用", status)

    def test_disabled_override_can_coexist_with_loaded_service_in_list(self):
        self.assertEqual(self.login("--start-now")[0], 0)
        label = self.label("login")
        self.fake.overrides[label] = True
        rc, listing, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("当前状态：已加载", listing)
        self.assertIn("系统禁用覆盖：已禁用", listing)
        self.assertNotIn("launchctl enable", listing)
        rc, status, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("launchctl enable", status)
        self.assertNotIn("--defer", status)

    def test_uninstall_loaded_then_disabled_does_not_clear_override(self):
        self.assertEqual(self.login("--start-now")[0], 0)
        label = self.label("login")
        self.assertIn(label, self.fake.loaded)
        self.fake.overrides[label] = True
        rc, out, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn(label, self.fake.loaded)
        self.assertTrue(self.fake.overrides[label])
        self.assertIn("launchctl enable", out)
        self.assertNotIn("enable", [call[0] for call in self.fake.calls])

    def test_custom_activated_summary_includes_source_label(self):
        source = {"ProgramArguments": ["/bin/true"], "StartInterval": 300, "Label": "example.source"}
        self.source.write_bytes(plistlib.dumps(source))
        args = ("install-plist", "--name", "activated-custom", "--description", "test",
                "--input", str(self.source), "--activate")
        pre = self.invoke(*args, "--dry-run")
        post = self.invoke(*args)
        self.assertEqual((pre[0], post[0]), (0, 0), (pre, post))
        key = "\u6765\u6e90 Label\uff1a"
        self.assertEqual([line for line in pre[1].splitlines() if line.startswith(key)],
                         [line for line in post[1].splitlines() if line.startswith(key)])
        label = la.make_label("", "activated-custom", "custom")
        self.assertIn(label, self.fake.loaded)

    def test_uninstall_without_explicit_disabled_override_has_no_false_warning(self):
        self.assertEqual(self.login()[0], 0)
        label = self.label("login")
        self.fake.gibberish_disabled_output = True
        code, out, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("launchctl enable", out)
        self.assertNotIn("注意：该 Label", out)

    def test_custom_source_label_is_identical_in_preview_and_install(self):
        for source_label in ("com.example.original", None):
            with self.subTest(source_label=source_label):
                body = {"ProgramArguments": ["/bin/true"], "StartInterval": 3600, "RunAtLoad": False}
                if source_label is not None:
                    body["Label"] = source_label
                self.source.write_bytes(plistlib.dumps(body, sort_keys=False))
                name = "custom-with-label" if source_label else "custom-without-label"
                args = ("install-plist", "--name", name, "--description", "test XML",
                        "--input", str(self.source))
                before = self.invoke(*args, "--dry-run")
                after = self.invoke(*args)
                self.assertEqual((before[0], after[0]), (0, 0), (before, after))
                key = "来源 Label："
                expected = source_label or "未提供"
                preview_lines = [x for x in before[1].splitlines() if x.startswith(key)]
                actual_lines = [x for x in after[1].splitlines() if x.startswith(key)]
                self.assertEqual(len(preview_lines), 1)
                self.assertEqual(preview_lines, actual_lines)
                self.assertIn(expected, preview_lines[0])
                self.assertIn("未注册", after[1])
                self.assertNotIn("注册成功不代表", after[1])

    def test_disabled_custom_can_only_write_disk_without_activate(self):
        body = {"ProgramArguments": ["/bin/true"], "StartInterval": 3600, "Label": "example.original"}
        self.source.write_bytes(plistlib.dumps(body))
        label = la.make_label("", "custom-task", "custom")
        self.fake.overrides[label] = True
        args = ("install-plist", "--name", "custom-task", "--description", "test", "--input", str(self.source))
        code, _, err = self.invoke(*args, "--activate")
        self.assertEqual(code, 1)
        self.assertIn("--activate", err)
        self.assertEqual(self.invoke(*args)[0], 0)
        self.assertNotIn("enable", [c[0] for c in self.fake.calls])


if __name__ == "__main__":
    unittest.main()
