"""Adversarial tests for ownership, launchctl uncertainty and rollback."""
from __future__ import annotations

import argparse
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures
la = fixtures.la
ORIGINAL_LAUNCHCTL = la.launchctl


class SafetyTests(unittest.TestCase):
    def setUp(self):
        # 独立建立夹具，不实例化另一个 TestCase 并手动调用其生命周期方法。
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.logs = self.root / "Library" / "Logs" / "LaunchAgents"
        self.metadata = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        patches = [
            patch.object(la, "LAUNCH_AGENTS_DIRECTORY", self.agents),
            patch.object(la, "LOG_DIRECTORY", self.logs),
            patch.object(la, "METADATA_DIRECTORY", self.metadata),
            patch.object(la, "DEFAULT_WORKING_DIRECTORY", self.root),
            patch.object(la, "require_macos", lambda: None),
            patch.object(la, "launchctl", self.fake),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.t = self

    def invoke(self, *args: str):
        if args and args[0] == "install" and "--description" not in args:
            args = (*args, "--description", "测试任务用途")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *args]), \
                patch.object(sys, "stdin", io.StringIO("")), \
                patch.object(sys, "stdout", stdout), \
                patch.object(sys, "stderr", stderr):
            result = la.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def create_job(self, command="echo owned", start=False):
        argv = ("install", "--command", command)
        if start:
            argv += ("--start-now",)
        code, out, err = self.invoke(*argv)
        self.assertEqual((code, err), (0, ""), err)
        return la.make_label(command, None, "login")

    def path_for(self, command, name=None, mode="login"):
        return self.agents / f"{la.make_label(command, name, mode)}.plist"

    def test_foreign_fixed_template_without_metadata_is_never_managed(self):
        cmd = "echo forged"
        label = la.make_label(cmd, None)
        path = self.t.agents / f"{label}.plist"
        self.t.agents.mkdir(parents=True)
        raw = la.to_plist_bytes(la.build_plist(label, cmd, self.t.root, False))
        path.write_bytes(raw)
        self.t.fake.loaded[label] = raw
        for argv in [
            ("status", "--label", label),
            ("run", "--label", label),
            ("uninstall", "--label", label),
            ("describe", "--label", label, "--description", "new"),
            ("install", "--command", cmd, "--replace", "--start-now"),
        ]:
            with self.subTest(argv=argv):
                code, _, _ = self.invoke(*argv)
                self.assertNotEqual(code, 0)
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        # 仅展示无法验证的文件，不把它纳入有效托管任务或操作服务。
        self.assertIn(label, out)
        self.assertIn("未管理的托管命名样式 plist", out)
        self.assertIn("共列出 0 个有效管理记录", out)
        self.assertTrue(path.exists())
        self.assertIn(label, self.t.fake.loaded)
        self.assertNotIn("bootout", [call[0] for call in self.t.fake.calls])

    def test_hash_mismatch_blocks_all_mutations_and_remains_readable(self):
        label = self.create_job(start=True)
        path = self.t.agents / f"{label}.plist"
        payload = plistlib.loads(path.read_bytes())
        payload["ProgramArguments"][-1] = "echo replaced by outsider"
        path.write_bytes(la.to_plist_bytes(payload))
        commands = [
            ("uninstall", "--label", label),
            ("run", "--label", label),
            ("describe", "--label", label, "--description", "external"),
            ("install", "--command", "echo owned", "--replace", "--start-now"),
        ]
        self.t.fake.calls.clear()
        for args in commands:
            with self.subTest(args=args):
                code, _, err = self.invoke(*args)
                self.assertEqual(code, 1)
                if args[0] == "install":
                    self.assertIn("plist 与管理记录不一致", err)
                else:
                    self.assertIn("管理记录与 plist 内容不一致", err)
        code, out, _ = self.invoke("status", "--label", label)
        self.assertEqual(code, 0)
        self.assertIn("禁止管理操作", out)
        self.assertNotIn("bootout", [call[0] for call in self.t.fake.calls])

    def test_status_is_successful_for_installed_unloaded(self):
        label = self.create_job()
        rc, out, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("\u672a\u52a0\u8f7d", out)
        self.assertTrue((self.t.agents / f"{label}.plist").exists())

    def test_unknown_probe_with_nonzero_exit_fails_closed(self):
        label = self.create_job(start=True)
        real = self.t.fake.__call__
        def fake(*args, check=True):
            if args[0] == "print" and args[1].count("/") == 2:
                return subprocess.CompletedProcess(args, 113, "", "permission denied")
            return real(*args, check=check)
        with patch.object(la, "launchctl", fake):
            code, out, err = self.invoke("status", "--label", label)
            self.assertEqual((code, err), (0, ""))
            self.assertIn("\u65e0\u6cd5\u786e\u8ba4", out)
            code, _, err = self.invoke("uninstall", "--label", label)
            self.assertEqual(code, 1)
            self.assertIn("\u65e0\u6cd5\u786e\u8ba4", err)
        self.assertIn(label, self.t.fake.loaded)
        self.assertTrue((self.t.agents / f"{label}.plist").exists())

    def test_transient_print_failure_leaves_live_service_intact(self):
        label = self.create_job(start=True)
        self.t.fake.fail_next = "print"
        rc, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("\u65e0\u6cd5\u786e\u8ba4", err)
        self.assertIn(label, self.t.fake.loaded)
        self.assertTrue((self.t.agents / f"{label}.plist").exists())

    def test_orphan_metadata_is_listed_and_status_does_not_probe_unknown(self):
        label = self.create_job(start=True)
        (self.t.agents / f"{label}.plist").unlink()
        self.t.fake.calls.clear()
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn(label, out)
        self.assertIn("\u5b64\u7acb\u7ba1\u7406\u8bb0\u5f55", out)
        rc, out, err = self.invoke("status", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("plist \u4e22\u5931", out)
        self.assertEqual(self.t.fake.calls, [])
        rc, _, _ = self.invoke("uninstall", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn(label, self.t.fake.loaded)

    def test_invalid_dates_refused_before_side_effects(self):
        for month, day in [(2, 30), (2, 31), (4, 31), (11, 31)]:
            with self.subTest(month=month, day=day):
                rc, _, err = self.invoke("install", "--command", "echo impossible",
                                          "--month", str(month), "--month-day", str(day), "--defer")
                self.assertEqual(rc, 1)
                self.assertIn("\u4e0d\u5b58\u5728", err)
        self.assertFalse(self.t.agents.exists())
        rc, _, err = self.invoke("install", "--command", "echo leap", "--month", "2",
                                 "--month-day", "29", "--defer")
        self.assertEqual((rc, err), (0, ""))

    def test_repair_accepts_external_plist_only_with_explicit_trust(self):
        label = self.create_job(start=True)
        path = self.agents / f"{label}.plist"
        original = plistlib.loads(path.read_bytes())
        original_hash = la.sha256_bytes(path.read_bytes())
        changed = dict(original)
        changed["ProgramArguments"] = list(original["ProgramArguments"])
        changed["ProgramArguments"][2] = "echo externally edited"
        path.write_bytes(la.to_plist_bytes(changed))
        rc, _, err = self.invoke("repair", "--label", label, "--action", "accept-plist")
        self.assertEqual(rc, 1)
        self.assertIn("--trust-current-plist", err)
        rc, out, err = self.invoke("repair", "--label", label, "--action", "accept-plist",
                                   "--trust-current-plist")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("未重载 launchd", out)
        _, metadata = la.inspect_metadata(label)
        self.assertEqual(metadata["disk_sha256"], la.sha256_bytes(path.read_bytes()))
        self.assertEqual(metadata["last_registered_sha256"], original_hash)
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("已接受外部改动", err)

    def test_repair_accepts_explicit_mode_change_so_task_can_be_managed_again(self):
        label = self.create_job(start=True)
        path = self.agents / f"{label}.plist"
        command = "echo owned"
        calendar_payload = la.build_plist(label, command, self.root, False,
                                          [{"Hour": 9, "Minute": 0}])
        path.write_bytes(la.to_plist_bytes(calendar_payload))
        rc, out, err = self.invoke("repair", "--label", label, "--action", "accept-plist",
                                   "--trust-current-plist")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("模式已从 登录自启动 更新为 日历定时", out)
        _, metadata = la.inspect_metadata(label)
        self.assertEqual(metadata["mode"], "calendar")
        self.assertIs(metadata["registration_snapshot_unknown"], True)
        # 接受模式变化只修复归属元数据，不会擅自重载；显式卸载仍能安全清理该 Label。
        rc, out, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertFalse(path.exists())

    def test_calendar_named_install_does_not_touch_separate_unmanaged_login_identity(self):
        command, name = "echo separate tasks", "Shared Friendly Name"
        other_label = la.make_label(command, name, "login")
        other_path = self.agents / f"{other_label}.plist"
        self.agents.mkdir(parents=True)
        other_payload = la.build_plist(other_label, command, self.root, False)
        other_data = la.to_plist_bytes(other_payload)
        other_path.write_bytes(other_data)
        rc, out, err = self.invoke("install", "--command", command, "--name", name,
                                   "--mode", "calendar", "--time", "10:00")
        self.assertEqual((rc, err), (0, ""))
        scheduled_label = la.make_label(command, name, "calendar")
        self.assertNotEqual(other_label, scheduled_label)
        self.assertTrue((self.agents / f"{scheduled_label}.plist").exists())
        self.assertEqual(other_path.read_bytes(), other_data)
        self.assertFalse(la.metadata_path(other_label).exists())
        self.assertNotIn(other_label, [call[1] for call in self.fake.calls if call[0] == "bootout"])

    def test_repair_remove_orphan_stops_loaded_service_before_forgetting_record(self):
        label = self.create_job(start=True)
        (self.agents / f"{label}.plist").unlink()
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan")
        self.assertEqual(rc, 1)
        self.assertIn("--confirm-stop", err)
        rc, out, err = self.invoke("repair", "--label", label, "--action", "remove-orphan",
                                   "--confirm-stop")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("已清理孤立管理记录", out)
        self.assertNotIn(label, self.fake.loaded)
        self.assertFalse(la.metadata_path(label).exists())

    def test_repair_rebuilds_corrupt_metadata_but_marks_registration_unknown(self):
        label = self.create_job(start=True)
        metadata_path = la.metadata_path(label)
        metadata_path.write_text("{ broken json", encoding="utf-8")
        rc, _, err = self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                 "--description", "恢复的任务用途")
        self.assertEqual(rc, 1)
        self.assertIn("--trust-current-plist", err)
        before_calls = list(self.fake.calls)
        rc, out, err = self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                   "--description", "恢复的任务用途", "--trust-current-plist")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("注册快照无法恢复", out)
        self.assertEqual(self.fake.calls, before_calls)
        _, metadata = la.inspect_metadata(label)
        self.assertIs(metadata["registration_snapshot_unknown"], True)
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("注册快照未知", err)
        # 显式重新注册后，新的注册快照可信，未知标志应清除。
        rc, _, err = self.invoke("install", "--command", "echo owned", "--start-now", "--replace")
        self.assertEqual((rc, err), (0, ""))
        _, metadata = la.inspect_metadata(label)
        self.assertIs(metadata["registration_snapshot_unknown"], False)

    def test_repair_does_not_rebuild_valid_metadata_or_adopt_missing_record(self):
        label = self.create_job()
        rc, _, err = self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                 "--description", "不应重建", "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("当前有效", err)
        metadata_path = la.metadata_path(label)
        metadata_path.unlink()
        rc, _, err = self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                 "--description", "不应接管", "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("管理记录文件不存在", err)

    def test_argparse_help_titles_and_error_details_are_stable(self):
        rc, out, err = self.invoke("--help")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("选项：", out)
        self.assertNotIn("可选参数：", out)
        rc, child_help, err = self.invoke("install", "--help")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("选项：", child_help)
        self.assertNotIn("可选参数：", child_help)
        rc, _, err = self.invoke("install", "--bogus-flag")
        self.assertEqual(rc, 2)
        self.assertIn("未知参数：--bogus-flag", err)
        rc, _, err = self.invoke("install")
        self.assertEqual(rc, 2)
        self.assertIn("必须提供以下参数之一", err)
        rc, _, err = self.invoke("install", "--command", "echo x", "--month", "not-an-int")
        self.assertEqual(rc, 2)
        self.assertIn("参数 --month 必须是整数", err)
        rc, _, err = self.invoke("install", "--command", "echo x", "--mode", "unknown")
        self.assertEqual(rc, 2)
        self.assertIn("参数 --mode 的值无效", err)

    def test_launchctl_english_diagnostics_are_not_leaked(self):
        real_run = subprocess.run
        def failing_run(command, *args, **kwargs):
            if command[:2] == ["/bin/launchctl", "bootstrap"]:
                return subprocess.CompletedProcess(command, 5, "", "Operation not permitted")
            if command[:2] == ["/bin/launchctl", "print"]:
                target = command[2]
                if target.count("/") == 1:
                    return subprocess.CompletedProcess(command, 0, "GUI domain", "")
                label = target.split("/")[-1]
                return subprocess.CompletedProcess(command, 113, "", f'Could not find service "{label}" in domain')
            return real_run(command, *args, **kwargs)
        with patch.object(la, "launchctl", ORIGINAL_LAUNCHCTL), \
                patch.object(la.subprocess, "run", failing_run):
            rc, _, err = self.invoke("install", "--command", "echo denied", "--start-now")
        self.assertEqual(rc, 1)
        self.assertNotIn("Operation not permitted", err)
        self.assertIn("操作未获系统授权", err)

    def test_plutil_english_diagnostics_are_not_leaked(self):
        path = self.root / "sample.plist"
        path.write_text("not a plist", encoding="utf-8")
        def bad_plutil(command, *args, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "Unexpected character < at line 1")
        with patch.object(la.sys, "platform", "darwin"), \
                patch.object(la.subprocess, "run", bad_plutil):
            with self.assertRaises(RuntimeError) as captured:
                la.plutil_lint(path)
        message = str(captured.exception)
        self.assertIn("原始输出已省略", message)
        self.assertNotIn("Unexpected character", message)

    def test_failed_bootstrap_does_not_change_persistent_enable_state(self):
        command = "echo persistent"
        self.t.fake.fail_next = "bootstrap"
        rc, _, err = self.invoke("install", "--command", command, "--start-now")
        self.assertEqual(rc, 1)
        self.assertIn("\u56de\u6eda", err)
        self.assertNotIn("enable", [call[0] for call in self.t.fake.calls])
        self.assertNotIn("disable", [call[0] for call in self.t.fake.calls])
        self.assertFalse(self.t.path_for(command).exists())
        self.assertFalse(la.metadata_path(la.make_label(command, None)).exists())

    def test_describe_is_serialized_against_uninstall(self):
        label = self.create_job()
        actual_write = la.write_metadata
        started = threading.Event()
        finished = threading.Event()
        errors = []
        threads = []
        def remover():
            started.set()
            try:
                la.uninstall(argparse.Namespace(label=label))
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()
        def concurrent_write(*args):
            thread = threading.Thread(target=remover)
            threads.append(thread)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(finished.wait(0.08), "uninstall must be blocked by describe's lock")
            return actual_write(*args)
        with patch.object(la, "write_metadata", side_effect=concurrent_write):
            rc, _, err = self.invoke("describe", "--label", label, "--description", "updated")
        self.assertEqual((rc, err), (0, ""))
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse((self.t.agents / f"{label}.plist").exists())
        self.assertFalse(la.metadata_path(label).exists())

    def test_uninstall_partial_bootout_failure_restores_old_job(self):
        label = self.create_job(start=True)
        path = self.t.agents / f"{label}.plist"
        previous = path.read_bytes()
        real = self.t.fake.__call__
        fired = False
        def fail_after_bootout(*args, check=True):
            nonlocal fired
            if args[0] == "bootout" and not fired:
                fired = True
                real(*args, check=check)
                raise RuntimeError("simulated partial bootout")
            return real(*args, check=check)
        with patch.object(la, "launchctl", fail_after_bootout):
            code, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("simulated partial bootout", err)
        self.assertIn("RunAtLoad", err)
        self.assertEqual(self.t.fake.loaded[label], previous)
        self.assertEqual(path.read_bytes(), previous)

    def test_uninstall_multiple_recovery_errors_are_aggregated(self):
        label = self.create_job(start=True)
        path = self.t.agents / f"{label}.plist"
        meta = la.metadata_path(label)
        old_unlink = Path.unlink
        real_atomic = la.atomic_write
        def broken_unlink(target, *args, **kwargs):
            if target == path:
                old_unlink(target, *args, **kwargs)
                raise PermissionError("simulated unlink after deletion")
            return old_unlink(target, *args, **kwargs)
        def broken_atomic(target, data):
            if target == path or target == meta:
                raise PermissionError("simulated recovery write failure")
            return real_atomic(target, data)
        with patch.object(Path, "unlink", broken_unlink), patch.object(la, "atomic_write", broken_atomic):
            code, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("simulated unlink after deletion", err)
        self.assertGreaterEqual(err.count("simulated recovery write failure"), 2)
        self.assertIn("\u91cd\u65b0\u6ce8\u518c\u5931\u8d25", err)

    def test_install_partial_bootout_failure_restores_live_state(self):
        cmd, name = "echo old", "Partial Bootout"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--start-now")[0], 0)
        label = la.make_label(cmd, name)
        path = self.t.agents / f"{label}.plist"
        previous = path.read_bytes()
        real = self.t.fake.__call__
        fired = False
        def fail_after_bootout(*args, check=True):
            nonlocal fired
            if args[0] == "bootout" and not fired:
                fired = True
                real(*args, check=check)
                raise RuntimeError("partial bootout install")
            return real(*args, check=check)
        with patch.object(la, "launchctl", fail_after_bootout):
            code, _, err = self.invoke("install", "--command", "echo newer", "--name", name,
                                        "--replace", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("partial bootout install", err)
        self.assertIn("RunAtLoad", err)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(self.t.fake.loaded[label], previous)

    def test_run_refuses_known_login_registration_drift(self):
        name = "DriftedJob"
        self.assertEqual(self.invoke("install", "--name", name, "--command", "echo old",
                                     "--start-now")[0], 0)
        label = la.make_label("echo old", name, "login")
        self.assertEqual(self.invoke("install", "--name", name, "--command", "echo new",
                                     "--replace")[0], 0)
        self.t.fake.calls.clear()
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("\u53ef\u80fd\u6267\u884c\u65e7\u547d\u4ee4", err)
        self.assertNotIn("kickstart", [c[0] for c in self.t.fake.calls])
        self.assertIn(label, self.t.fake.loaded)

    def test_run_refuses_deferred_calendar_drift(self):
        name = "ScheduledDrift"
        self.assertEqual(self.invoke("install", "--name", name, "--command", "echo old",
                                     "--time", "09:00")[0], 0)
        label = la.make_label("echo old", name, "calendar")
        self.assertEqual(self.invoke("install", "--name", name, "--command", "echo old",
                                     "--time", "10:00", "--replace", "--defer")[0], 0)
        self.t.fake.calls.clear()
        rc, _, err = self.invoke("run", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("\u53ef\u80fd\u6267\u884c\u65e7\u547d\u4ee4", err)
        self.assertNotIn("kickstart", [c[0] for c in self.t.fake.calls])

    def test_list_only_foreign_files_shows_empty_managed_collection(self):
        self.t.agents.mkdir(parents=True)
        self.t.metadata.mkdir(parents=True)
        label = la.make_label("echo unknown", None)
        (self.t.agents / f"{label}.plist").write_bytes(
            la.to_plist_bytes(la.build_plist(label, "echo unknown", self.t.root, False)))
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn(label, out)
        self.assertIn("未管理的托管命名样式 plist", out)
        self.assertIn("共列出 0 个有效管理记录", out)


if __name__ == "__main__":
    unittest.main()
