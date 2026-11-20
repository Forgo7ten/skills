"""预览与提交一致性、Disabled 禁用覆盖、转换日志语义的回归测试。"""
from __future__ import annotations

import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class DisabledLaunchctl(fixtures.FakeLaunchctl):
    """模拟 print-disabled 的持久记录以及 bootstrap 的泛用 EIO。"""
    def __init__(self):
        super().__init__()
        self.overrides: dict[str, bool] = {}
        self.eio_at_bootstrap = False
        self.gibberish_disabled_output = False

    def __call__(self, *args: str, check: bool = True):
        if args[0] == "print-disabled":
            self.calls.append(args)
            if self.gibberish_disabled_output:
                return subprocess.CompletedProcess(args, 0, "unrecognized future response", "")
            entries = "\n".join(f'  "{name}" => {"disabled" if flag else "enabled"}'
                                for name, flag in sorted(self.overrides.items()))
            return subprocess.CompletedProcess(args, 0, "disabled services = {\n" + entries + "\n}\n", "")
        if args[0] == "bootstrap" and self.eio_at_bootstrap:
            self.calls.append(args)
            raise RuntimeError("launchctl bootstrap 执行失败（退出码 5）：系统返回输入/输出错误")
        return super().__call__(*args, check=check)


class PreviewDisabledTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.logs = self.root / "Library" / "Logs" / "LaunchAgents"
        self.records = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = DisabledLaunchctl()
        for name, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.logs),
            ("METADATA_DIRECTORY", self.records),
            ("DEFAULT_WORKING_DIRECTORY", self.root),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            p = patch.object(la, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.source = self.root / "source.plist"

    def invoke(self, *args: str):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *args]), \
                patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = la.main()
        return code, out.getvalue(), err.getvalue()

    def fixed(self, cmd: str, *args: str):
        return self.invoke("install", "--name", "sample", "--description", "测试任务", "--command", cmd, *args)

    def custom(self, *args: str):
        return self.invoke("install-plist", "--name", "custom-task", "--description", "测试自定义任务",
                           "--input", str(self.source), *args)

    def write_source(self, *, command: str = "/bin/true", disabled=None):
        data = {"ProgramArguments": [command], "StartInterval": 120, "RunAtLoad": False,
                "Label": "example.source"}
        if disabled is not None:
            data["Disabled"] = disabled
        self.source.write_bytes(plistlib.dumps(data, sort_keys=False))
        return data

    def test_dry_run_fixed_matches_replace_check_and_is_read_only(self):
        self.assertEqual(self.fixed("echo one")[0], 0)
        path = self.agents / (la.make_label("echo one", "sample") + ".plist")
        original = path.read_bytes()
        self.fake.calls.clear()
        preview = self.fixed("echo two", "--dry-run")
        actual = self.fixed("echo two")
        self.assertEqual(preview[0], actual[0], (preview, actual))
        self.assertEqual(preview[0], 1)
        self.assertIn("--replace", preview[2])
        self.assertIn("--replace", actual[2])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.fake.calls, [])
        accepted = self.fixed("echo two", "--replace", "--dry-run")
        self.assertEqual(accepted[0], 0, accepted)
        self.assertIn("工作目录：", accepted[1])
        self.assertIn("触发配置：", accepted[1])
        self.assertEqual(path.read_bytes(), original)

    def test_fixed_dry_run_preflight_detects_unowned_and_orphan(self):
        label = la.make_label("echo one", "sample")
        self.agents.mkdir(parents=True, exist_ok=True)
        path = self.agents / f"{label}.plist"
        path.write_bytes(la.to_plist_bytes(la.build_plist(label, "echo one", self.root, False)))
        code, _, err = self.fixed("echo one", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("没有本 Skill 的管理记录", err)
        path.unlink()
        self.assertEqual(self.fixed("echo one")[0], 0)
        path.unlink()
        code, _, err = self.fixed("echo one", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("孤立管理记录", err)

    def test_calendar_preview_shares_detailed_schedule(self):
        code, out, err = self.invoke("install", "--mode", "calendar", "--name", "hourly-test",
                                     "--description", "每天维护", "--command", "/bin/true",
                                     "--time", "08:30,19:05", "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("计划时间（Mac 本地时区）：每天 08:30、每天 19:05", out)
        self.assertIn("工作目录：", out)
        self.assertIn("元数据文件：", out)
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.fake.calls, [])

    def test_custom_preview_conflicts_and_trigger(self):
        self.write_source()
        self.assertEqual(self.custom()[0], 0)
        self.write_source(command="/bin/echo")
        preview, actual = self.custom("--dry-run"), self.custom()
        self.assertEqual(preview[0], actual[0])
        self.assertEqual(preview[0], 1)
        self.assertIn("--replace", preview[2])
        self.assertEqual(self.custom("--replace", "--dry-run")[0], 0)
        check = self.custom("--replace", "--dry-run")
        self.assertIn("触发配置：", check[1])
        self.assertIn("工作目录：", check[1])
        self.assertIn("StartInterval", check[1])
        self.assertEqual(self.custom("--replace")[0], 0)

    def test_custom_preview_unowned_target_refused(self):
        self.write_source()
        label = la.make_label("", "custom-task", "custom")
        self.agents.mkdir(parents=True)
        (self.agents / (label + ".plist")).write_bytes(plistlib.dumps(
            {"Label": label, "ProgramArguments": ["/bin/true"]}))
        code, _, err = self.custom("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("无法识别", err)
        self.assertEqual(self.custom()[0], 1)

    def test_comma_items_report_actual_position(self):
        for option, value in (("--time", "09:00,"), ("--time", "09:00,,10:00"),
                              ("--weekday", "mon,"), ("--weekday", ",mon")):
            with self.subTest(option=option, value=value):
                result = self.invoke("install", "--mode", "calendar", "--command", "/bin/true",
                                     "--description", "无害测试", option, value)
                self.assertEqual(result[0], 1)
                self.assertIn("逗号列表中存在空项", result[2])
                self.assertIn(repr(value), result[2])

    def test_disabled_true_and_false_rejected_in_custom_dry_run_and_commit(self):
        for value in (True, False):
            with self.subTest(value=value):
                self.write_source(disabled=value)
                for extra in ((), ("--dry-run",), ("--activate",)):
                    result = self.custom(*extra)
                    self.assertEqual(result[0], 1, result)
                    self.assertIn("Disabled", result[2])
                self.assertFalse(self.agents.exists())
                self.assertEqual(self.fake.calls, [])

    def test_override_prevents_activation_without_writing(self):
        label = la.make_label("echo one", "sample")
        self.fake.overrides[label] = True
        code, _, err = self.fixed("echo one", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("持久禁用", err)
        self.assertIn("launchctl enable", err)
        self.assertNotIn(label, self.fake.loaded)
        self.assertFalse(self.agents.exists())
        self.assertNotIn("enable", [c[0] for c in self.fake.calls])

    def test_disabled_is_visible_in_status_list_and_register(self):
        self.assertEqual(self.fixed("echo one")[0], 0)
        label = la.make_label("echo one", "sample")
        self.fake.overrides[label] = True
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("当前状态：已禁用", out)
        self.assertIn("系统禁用覆盖：已禁用", out)
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("已禁用", out)
        code, out, err = self.invoke("register", "--label", label, "--allow-execution")
        self.assertEqual(code, 1)
        self.assertIn("持久禁用", err)
        self.assertNotIn("enable", [c[0] for c in self.fake.calls])
        self.fake.overrides[label] = False
        self.assertEqual(self.invoke("register", "--label", label, "--allow-execution")[0], 0)

    def test_bootstrap_eio_has_conditionally_diagnostic_and_rolls_back(self):
        self.fake.eio_at_bootstrap = True
        code, _, err = self.fixed("echo one", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("EIO 也可能", err)
        self.assertIn("已尝试回滚", err)
        self.assertFalse(list(self.agents.glob("*.plist")))
        self.assertFalse(list(self.records.glob("*.json")))

    def test_bootstrap_eio_diagnoses_newly_disabled_override_after_precheck(self):
        """TOCTOU：检查后、bootstrap 前系统被用户禁用，不能只报泛用 EIO。"""
        label = la.make_label("echo one", "sample")
        original = self.fake.__call__
        queries = [0]

        def toggle(*args, **kwargs):
            if args[0] == "print-disabled":
                queries[0] += 1
                if queries[0] == 2:
                    self.fake.overrides[label] = True
            if args[0] == "bootstrap":
                self.fake.eio_at_bootstrap = True
            return original(*args, **kwargs)

        with patch.object(la, "launchctl", toggle):
            code, _, err = self.fixed("echo one", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("诊断：Label", err)
        self.assertIn("存在跨重启的持久禁用覆盖", err)
        self.assertIn("已尝试回滚", err)
        self.assertFalse(list(self.agents.glob("*.plist")))

    def test_loaded_service_with_disabled_override_is_not_misreported_stopped(self):
        self.assertEqual(self.fixed("echo one", "--start-now")[0], 0)
        label = la.make_label("echo one", "sample")
        self.fake.overrides[label] = True
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("当前状态：已加载", out)
        self.assertIn("系统禁用覆盖：已禁用", out)

    def test_unknown_runtime_is_not_silently_overridden_by_disabled_flag(self):
        self.assertEqual(self.fixed("echo one")[0], 0)
        label = la.make_label("echo one", "sample")
        self.fake.overrides[label] = True
        original = self.fake.__call__

        def uncertain(*args, **kwargs):
            if args[0] == "print" and args[1].endswith("/" + label):
                return subprocess.CompletedProcess(args, 5, "", "I/O error")
            return original(*args, **kwargs)

        with patch.object(la, "launchctl", uncertain):
            code, out, err = self.invoke("status", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("当前状态：无法确认", out)
        self.assertIn("系统禁用覆盖：已禁用", out)

    def test_dry_run_skips_launchctl_even_if_disabled_override_present(self):
        label = la.make_label("echo one", "sample")
        self.fake.overrides[label] = True
        code, out, err = self.fixed("echo one", "--start-now", "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("注册是否成功仍需", out)
        self.assertFalse(self.fake.calls)
        self.assertFalse(self.agents.exists())

    def test_unknown_print_disabled_format_not_assumed_enabled(self):
        self.assertEqual(self.fixed("echo one")[0], 0)
        label = la.make_label("echo one", "sample")
        self.fake.gibberish_disabled_output = True
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("系统禁用覆盖：无法确认", out)
        self.assertNotIn("已明确启用", out)


if __name__ == "__main__":
    unittest.main()
