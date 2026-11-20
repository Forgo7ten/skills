"""Calendar-mode tests: fixed schemas, install semantics, failures and safe management."""
from __future__ import annotations

import io
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_launch_agent import FakeLaunchctl, la, MODULE_FILE


class CalendarAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.logs = self.root / "Library" / "Logs" / "LaunchAgents"
        self.metadata = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = FakeLaunchctl()
        for obj, name, value in (
            (la, "LAUNCH_AGENTS_DIRECTORY", self.agents),
            (la, "LOG_DIRECTORY", self.logs),
            (la, "METADATA_DIRECTORY", self.metadata),
            (la, "DEFAULT_WORKING_DIRECTORY", self.root),
            (la, "require_macos", lambda: None),
            (la, "launchctl", self.fake),
        ):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def invoke(self, *argv: str, command_input: str | None = None, auto_description: bool = True) -> tuple[int, str, str]:
        if auto_description and argv and argv[0] == "install" and "--description" not in argv:
            argv = (*argv, "--description", "测试任务用途")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(MODULE_FILE), *argv]), \
                patch.object(sys, "stdin", io.StringIO(command_input or "")), \
                patch.object(sys, "stdout", out), \
                patch.object(sys, "stderr", err):
            code = la.main()
        return code, out.getvalue(), err.getvalue()

    def plist_for(self, command: str, name: str | None = None) -> tuple[Path, dict]:
        label = la.make_label(command, name, "calendar")
        path = self.agents / (label + ".plist")
        return path, plistlib.loads(path.read_bytes())

    def test_default_calendar_nine_am_and_bootstrap_without_execution(self):
        command = "echo scheduled"
        code, out, err = self.invoke("install", "--command", command, "--mode", "calendar")
        self.assertEqual((code, err), (0, ""))
        path, p = self.plist_for(command)
        self.assertEqual(p["StartCalendarInterval"], {"Hour": 9, "Minute": 0})
        self.assertIs(p["RunAtLoad"], False)
        self.assertIs(p["KeepAlive"], False)
        self.assertEqual(p["ProgramArguments"], ["/bin/zsh", "-lc", command])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn("每天 09:00", out)
        self.assertIn("注册本身不会因 RunAtLoad 执行命令", out)
        self.assertIn("bootstrap", [c[0] for c in self.fake.calls])
        self.assertNotIn("kickstart", [c[0] for c in self.fake.calls])

    def test_time_implies_calendar_without_explicit_mode(self):
        code, _, err = self.invoke("install", "--command", "echo ok", "--time", "13:00")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.plist_for("echo ok")[1]["StartCalendarInterval"], {"Hour": 13, "Minute": 0})

    def test_multi_time_repeat_comma_sort_dedupe_and_xml_int(self):
        cmd = "echo 'Hello <world> & good day'"
        code, out, err = self.invoke("install", "--command-stdin", "--time", "13:00, 9:05", "--time", "09:05", "--time", "00:00,23:59", command_input=cmd + "\n")
        self.assertEqual((code, err), (0, ""))
        plist = self.plist_for(cmd)[1]
        self.assertEqual(plist["ProgramArguments"][-1], cmd)
        self.assertEqual(plist["StartCalendarInterval"], [
            {"Hour": 0, "Minute": 0}, {"Hour": 9, "Minute": 5},
            {"Hour": 13, "Minute": 0}, {"Hour": 23, "Minute": 59},
        ])
        self.assertIn("每天 00:00、每天 09:05、每天 13:00、每天 23:59", out)
        self.assertEqual(plist["Umask"], 63)

    def test_time_default_and_renamed_installer_are_deterministic(self):
        cmd = "echo once"
        c1 = self.invoke("install", "--command", cmd, "--mode", "calendar", "--defer")[0]
        c2 = self.invoke("install", "--command", cmd, "--time", "09:00", "--defer")[0]
        self.assertEqual((c1, c2), (0, 0))
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 1)

    def test_invalid_times_rejected_before_file_changes(self):
        for value in ["24:00", "9:60", "-1:00", "9", "9:0:0", "", "9:00,", "9:00,,10:00", "9:00 10:00", "09:０0", "9:00\n10:00"]:
            with self.subTest(value=value):
                code, _, err = self.invoke("install", "--command", "echo reject", f"--time={value}")
                self.assertNotEqual(code, 0)
                if not value or ",," in value or value.endswith(","):
                    self.assertIn("--time 的逗号列表中存在空项", err)
                else:
                    self.assertIn("--time 时间格式无效" if "24" not in value and "9:60" not in value else "--time 超出有效范围", err)
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.fake.calls, [])

    def test_weekday_cross_product_multiple_clocks_and_dedup(self):
        cmd = "echo weekday"
        code, out, err = self.invoke("install", "--command", cmd, "--weekday", "mon,fri", "--weekday", "Mon,7", "--time", "08:05,17:30")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.plist_for(cmd)[1]["StartCalendarInterval"], [
            {"Weekday": 0, "Hour": 8, "Minute": 5}, {"Weekday": 0, "Hour": 17, "Minute": 30},
            {"Weekday": 1, "Hour": 8, "Minute": 5}, {"Weekday": 1, "Hour": 17, "Minute": 30},
            {"Weekday": 5, "Hour": 8, "Minute": 5}, {"Weekday": 5, "Hour": 17, "Minute": 30},
        ])
        self.assertIn("每周日 08:05", out)

    def test_monthly_and_exact_date_calendar(self):
        c = "echo month"
        self.assertEqual(self.invoke("install", "--command", c, "--time", "4:5", "--month-day", "31")[0], 0)
        self.assertEqual(self.plist_for(c)[1]["StartCalendarInterval"], {"Day": 31, "Hour": 4, "Minute": 5})
        name = "Monthly Task"
        self.assertEqual(self.invoke("install", "--command", "echo exact", "--name", name, "--time", "02:04", "--month-day", "29", "--month", "2")[0], 0)
        self.assertEqual(self.plist_for("echo exact", name)[1]["StartCalendarInterval"], {"Month": 2, "Day": 29, "Hour": 2, "Minute": 4})

    def test_invalid_calendar_combinations_and_ranges(self):
        invalid_opts = [
            ("--mode", "login", "--time", "10:00"),
            ("--mode", "login", "--defer"),
            ("--mode", "calendar", "--keep-alive"),
            ("--time", "01:00", "--weekday", "whatever"),
            ("--time", "01:00", "--weekday", "mon,"),
            ("--time", "01:00", "--weekday", "-1"),
            ("--time", "01:00", "--weekday", "8"),
            ("--time", "01:00", "--month", "4"),
            ("--time", "01:00", "--month-day", "0"),
            ("--time", "01:00", "--month-day", "32"),
            ("--time", "01:00", "--month-day", "1", "--weekday", "fri"),
            ("--time", "01:00", "--month", "0", "--month-day", "2"),
            ("--time", "01:00", "--month", "13", "--month-day", "2"),
        ]
        for opts in invalid_opts:
            with self.subTest(opts=opts):
                code, _, err = self.invoke("install", "--command", "echo x", *opts)
                self.assertEqual(code, 1, (opts, err))
                self.assertIn("错误：", err)
        self.assertFalse(self.agents.exists())

    def test_scheduled_defer_only_writes_and_gui_missing_is_allowed(self):
        self.fake.gui_available = False
        command = "echo tomorrow"
        code, out, err = self.invoke("install", "--command", command, "--mode", "calendar", "--time", "06:00", "--defer")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(self.plist_for(command)[0].exists())
        self.assertIn("下次图形用户登录", out)
        self.assertNotIn("旧日程", out)
        self.assertEqual(self.fake.calls, [])

    def test_scheduled_missing_gui_rejected_before_side_effects(self):
        self.fake.gui_available = False
        code, _, err = self.invoke("install", "--command", "echo notnow", "--time", "06:00")
        self.assertEqual(code, 1)
        self.assertIn("图形登录 launchd 域", err)
        self.assertFalse(self.agents.exists())

    def test_start_now_for_calendar_explicitly_kickstarts(self):
        cmd = "echo kick"
        code, out, err = self.invoke("install", "--command", cmd, "--time", "12:00", "--start-now")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("额外触发一次任务", out)
        self.assertEqual([c[0] for c in self.fake.calls].count("kickstart"), 1)

    def test_same_schedule_repeated_install_reconciles_live_state(self):
        cmd = "echo stable"
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "9:00")[0], 0)
        loaded_before = self.fake.loaded[self.plist_for(cmd)[0].stem]
        self.fake.calls.clear()
        code, out, err = self.invoke("install", "--command", cmd, "--time", "09:00", "--mode", "calendar")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("plist 内容未变化", out)
        # 同一配置已有匹配的注册快照时，不应无谓中断当前任务。
        self.assertNotIn("bootout", [c[0] for c in self.fake.calls])
        self.assertNotIn("bootstrap", [c[0] for c in self.fake.calls])
        self.assertEqual(self.fake.loaded[self.plist_for(cmd)[0].stem], loaded_before)

    def test_deferred_update_reconciles_active_schedule_later(self):
        cmd, name = "echo again", "Deferred Update"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "9:00")[0], 0)
        path, _ = self.plist_for(cmd, name)
        self.assertEqual(plistlib.loads(self.fake.loaded[path.stem])["StartCalendarInterval"]["Hour"], 9)
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "10:00", "--replace", "--defer")[0], 0)
        self.assertEqual(plistlib.loads(path.read_bytes())["StartCalendarInterval"]["Hour"], 10)
        self.assertEqual(plistlib.loads(self.fake.loaded[path.stem])["StartCalendarInterval"]["Hour"], 9)
        code, status_out, status_err = self.invoke("status", "--label", path.stem)
        self.assertEqual(status_err, "")
        self.assertEqual(code, 0)
        self.assertIn("疑似漂移", status_out)
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "10:00")[0], 0)
        self.assertEqual(plistlib.loads(self.fake.loaded[path.stem])["StartCalendarInterval"]["Hour"], 10)

    def test_calendar_change_requires_replace_and_live_reloads(self):
        cmd, name = "echo changed", "Scheduled Unique"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "9:00")[0], 0)
        path, original = self.plist_for(cmd, name)
        self.fake.calls.clear()
        code, _, err = self.invoke("install", "--command", cmd, "--name", name, "--time", "10:00")
        self.assertEqual(code, 1)
        self.assertIn("--replace", err)
        self.assertEqual(plistlib.loads(path.read_bytes()), original)
        self.assertFalse(any(x[0] in {"bootout", "bootstrap", "enable", "kickstart"} for x in self.fake.calls))
        code, _, err = self.invoke("install", "--command", cmd, "--name", name, "--time", "10:00", "--replace")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("bootout", [x[0] for x in self.fake.calls])
        self.assertEqual(plistlib.loads(self.fake.loaded[path.stem])["StartCalendarInterval"], {"Hour": 10, "Minute": 0})

    def test_same_name_login_and_calendar_use_separate_labels(self):
        cmd, name = "echo updated", "Swap Modes"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--start-now")[0], 0)
        login_label = la.make_label(cmd, name, "login")
        login_path = self.agents / f"{login_label}.plist"
        self.assertIs(plistlib.loads(login_path.read_bytes())["RunAtLoad"], True)
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "14:00")[0], 0)
        calendar_label = la.make_label(cmd, name, "calendar")
        calendar_path = self.agents / f"{calendar_label}.plist"
        self.assertNotEqual(login_label, calendar_label)
        self.assertIs(plistlib.loads(calendar_path.read_bytes())["RunAtLoad"], False)
        self.assertIn(login_label, self.fake.loaded)
        self.assertIn(calendar_label, self.fake.loaded)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)

    def test_rollback_update_on_bootstrap_failure(self):
        cmd, name = "echo orig", "Rollback Calendar"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "07:00")[0], 0)
        path, _ = self.plist_for(cmd, name)
        old = path.read_bytes()
        self.fake.fail_next = "bootstrap"
        code, _, err = self.invoke("install", "--command", cmd, "--name", name, "--time", "08:00", "--replace")
        self.assertEqual(code, 1)
        self.assertIn("已尝试回滚", err)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(self.fake.loaded[path.stem], old)

    def test_rollback_on_calendar_bootstrap_failure_new_install(self):
        cmd = "echo new fail"
        self.fake.fail_next = "bootstrap"
        code, _, err = self.invoke("install", "--command", cmd, "--time", "18:00")
        self.assertEqual(code, 1)
        self.assertIn("已尝试回滚", err)
        self.assertFalse((self.agents / (la.make_label(cmd, None, "calendar") + ".plist")).exists())

    def test_plutil_lint_failure_is_rolled_back_before_bootstrap(self):
        cmd = "echo plutil_fail"
        with patch.object(la, "plutil_lint", side_effect=RuntimeError("invalid plist by plutil")):
            code, _, err = self.invoke("install", "--command", cmd, "--time", "10:00")
        self.assertEqual(code, 1)
        self.assertIn("invalid plist by plutil", err)
        self.assertNotIn("bootstrap", [c[0] for c in self.fake.calls])
        self.assertFalse(self.plist_path(cmd).exists())

    def plist_path(self, cmd: str) -> Path:
        return self.agents / (la.make_label(cmd, None, "calendar") + ".plist")

    def test_failed_kickstart_restores_new_install(self):
        cmd = "echo failskick"
        self.fake.fail_next = "kickstart"
        code, _, err = self.invoke("install", "--command", cmd, "--time", "16:00", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("simulated kickstart failure", err)
        self.assertFalse(self.plist_path(cmd).exists())
        self.assertNotIn(la.make_label(cmd, None, "calendar"), self.fake.loaded)

    def test_run_requires_owned_installed_loaded_job_and_uses_kickstart(self):
        cmd = "echo manual"
        label = la.make_label(cmd, None, "calendar")
        code, _, err = self.invoke("run", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("管理记录", err)
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "9:00", "--defer")[0], 0)
        code, _, err = self.invoke("run", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("未加载", err)
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "9:00")[0], 0)
        code, out, err = self.invoke("run", "--label", label)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("已请求任务", out)
        self.assertIn(("kickstart", la.service_target(label)), self.fake.calls)

    def test_schedule_status_and_list_are_concise_and_manageable(self):
        cmd = "echo query"
        self.assertEqual(self.invoke("install", "--command", cmd, "--weekday", "tue", "--time", "9:15,14:40")[0], 0)
        path, _ = self.plist_for(cmd)
        c1, out, err = self.invoke("status", "--label", path.stem)
        self.assertEqual((c1, err), (0, ""))
        self.assertIn("每周二 09:15", out)
        self.assertIn("当前状态：已加载", out)
        self.assertNotIn("loaded " + path.stem, out)
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("模式：日历定时", out)
        self.assertIn("每周二 14:40", out)

    def test_uninstall_calendar_removes_live_job_and_retains_logs(self):
        cmd = "echo delete"
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "01:30")[0], 0)
        path, _ = self.plist_for(cmd)
        self.assertEqual(self.invoke("uninstall", "--label", path.stem)[0], 0)
        self.assertFalse(path.exists())
        self.assertNotIn(path.stem, self.fake.loaded)
        self.assertTrue((self.logs / (path.stem + ".stdout.log")).exists())

    def test_reject_modified_foreign_calendar_plist_before_bootout(self):
        cmd, name = "echo guard", "guard"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "08:00")[0], 0)
        path, payload = self.plist_for(cmd, name)
        variants = [
            {**payload, "RunAtLoad": True},
            {**payload, "KeepAlive": True},
            {**payload, "StartCalendarInterval": {"Hour": 25, "Minute": 0}},
            {**payload, "StartCalendarInterval": {"Hour": True, "Minute": 0}},
            {**payload, "StartCalendarInterval": {"Hour": 8, "Minute": 0, "UserName": "root"}},
            {**payload, "StartCalendarInterval": [{"Hour": 8, "Minute": 0}, {"Hour": 8, "Minute": 0}]},
            {**payload, "StartCalendarInterval": {"Day": 4, "Weekday": 5, "Hour": 8, "Minute": 0}},
        ]
        original = path.read_bytes()
        for altered in variants:
            with self.subTest(calendar=altered["StartCalendarInterval"]):
                path.write_bytes(plistlib.dumps(altered))
                self.fake.calls.clear()
                code, _, err = self.invoke("uninstall", "--label", path.stem)
                self.assertEqual(code, 1)
                self.assertIn("拒绝操作无法识别的 plist", err)
                self.assertFalse(any(x[0] == "bootout" for x in self.fake.calls))
                self.assertTrue(path.exists())
        path.write_bytes(original)
        self.assertEqual(self.invoke("uninstall", "--label", path.stem)[0], 0)

    def test_calendar_script_path_with_shebang_is_frozen(self):
        script = self.root / "scheduled script.sh"
        script.write_text("#!/bin/bash\nprintf '%s\\n' 'scheduled'\n")
        script.chmod(0o700)
        code, _, err = self.invoke("install", "--script", str(script), "--time", "05:10")
        self.assertEqual((code, err), (0, ""))
        import shlex
        expected = shlex.quote(str(script.resolve()))
        self.assertEqual(self.plist_for(expected)[1]["ProgramArguments"], ["/bin/zsh", "-lc", expected])

    def test_calendar_update_bootout_failure_restores_old_plist_and_job(self):
        cmd, name = "echo old", "Calendar Bootout Failure"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "05:00")[0], 0)
        path, _ = self.plist_for(cmd, name)
        old = path.read_bytes()
        self.fake.fail_next = "bootout"
        code, _, err = self.invoke("install", "--command", cmd, "--name", name, "--time", "06:00", "--replace")
        self.assertEqual(code, 1)
        self.assertIn("simulated bootout failure", err)
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(self.fake.loaded[path.stem], old)

    def test_macos_plutil_lint_invoked_with_disk_file(self):
        from subprocess import CompletedProcess
        candidate = self.root / "name.plist"
        candidate.write_bytes(plistlib.dumps({"Label": "demo"}))
        with patch.object(la.sys, "platform", "darwin"), patch.object(la.subprocess, "run", return_value=CompletedProcess([], 0, "OK", "")) as call:
            la.plutil_lint(candidate)
            call.assert_called_once_with(
                ["/usr/bin/plutil", "-lint", str(candidate)],
                capture_output=True, text=True, check=False,
            )

    def test_unmanaged_incomplete_calendar_not_overwritten(self):
        cmd, name = "echo unmanagedcalendar", "unmanagedcalendar"
        label = la.make_label(cmd, name, "calendar")
        payload = la.build_plist(label, cmd, self.root.resolve(), False,
                                 [{"Hour": 6, "Minute": 45}])
        payload.pop("Umask")
        self.agents.mkdir(parents=True)
        path = self.agents / (label + ".plist")
        path.write_bytes(plistlib.dumps(payload))
        code, _, err = self.invoke("install", "--command", cmd, "--name", name,
                                    "--time", "06:45", "--replace")
        self.assertEqual(code, 1)
        self.assertIn("不符合本 Skill 的固定模板", err)
        self.assertNotIn("Umask", plistlib.loads(path.read_bytes()))

    def test_login_auto_and_calendar_can_coexist_for_unamed_command(self):
        cmd = "echo both"
        self.assertEqual(self.invoke("install", "--command", cmd)[0], 0)
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "10:00")[0], 0)
        self.assertNotEqual(la.make_label(cmd, None), la.make_label(cmd, None, "calendar"))
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)



    def test_calendar_changed_with_prior_registration_warns_only_on_defer(self):
        cmd, name = "echo daily", "daily-job"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name, "--time", "08:00")[0], 0)
        label = la.make_label(cmd, name, "calendar")
        original = self.fake.loaded[label]
        rc, out, err = self.invoke("install", "--command", cmd, "--name", name,
                                   "--time", "09:00", "--defer", "--replace")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("\u65e7\u65e5\u7a0b", out)
        self.assertEqual(self.fake.loaded[label], original)
        self.assertEqual(self.plist_for(cmd, name)[1]["StartCalendarInterval"], {"Hour":9, "Minute":0})

    def test_calendar_never_registered_does_not_warn_about_old_schedule(self):
        cmd, name = "echo later", "later-job"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", name,
                                     "--time", "09:00", "--defer")[0], 0)
        rc, out, err = self.invoke("install", "--command", cmd, "--name", name,
                                   "--time", "10:00", "--defer", "--replace")
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("\u65e7\u65e5\u7a0b", out)
        self.assertIn("RunAtLoad=false", out)
        self.assertIn("KeepAlive=false", out)
        self.assertEqual(self.fake.calls, [])

    def test_same_calendar_command_other_name_warns_without_blocking(self):
        cmd = "echo identical"
        self.assertEqual(self.invoke("install", "--command", cmd, "--time", "09:00")[0], 0)
        original = la.make_label(cmd, None, "calendar")
        rc, out, err = self.invoke("install", "--name", "daily-job", "--command", cmd, "--time", "10:00")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn(original, out)
        self.assertIn("\u5df2\u6709\u540c\u6a21\u5f0f", out)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)
        status, info, msg = self.invoke("status", "--label", la.make_label(cmd, "daily-job", "calendar"))
        self.assertEqual((status, msg), (0, ""))
        self.assertIn("KeepAlive=false", info)

if __name__ == "__main__":
    unittest.main(verbosity=2)
