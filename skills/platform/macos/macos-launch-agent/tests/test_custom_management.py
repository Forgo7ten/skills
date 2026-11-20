"""配置生成与统一生命周期之间的集成、故障注入和边界回归测试。"""
from __future__ import annotations

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

import test_launch_agent as fixtures
from custom_plist import prepare_payload

la = fixtures.la


class CustomManagementTests(unittest.TestCase):
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
        self.source = self.home / "source.plist"
        self.payload = {"ProgramArguments": ["/bin/echo", "空格 ' 引号 & $HOME"],
                        "StartInterval": 300, "RunAtLoad": False,
                        "EnvironmentVariables": {"TEST_VAR": "a b"}}
        self.write_source()

    def write_source(self, payload=None, fmt=plistlib.FMT_XML):
        self.source.write_bytes(plistlib.dumps(self.payload if payload is None else payload, fmt=fmt, sort_keys=False))

    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = la.main()
        return code, out.getvalue(), err.getvalue()

    def install(self, *flags, name="demo"):
        return self.invoke("install-plist", "--input", str(self.source), "--name", name,
                           "--description", "测试目录维护任务", *flags)

    def label(self, name="demo"):
        return la.make_label("", name, "custom")

    def path(self, name="demo"):
        return self.agents / (self.label(name) + ".plist")

    def ok(self, result):
        self.assertEqual((result[0], result[2]), (0, ""), result)
        return result[1]

    def test_converter_export_then_managed_import_complete_lifecycle(self):
        env = dict(os.environ, HOME=str(self.home))
        converter = fixtures.MODULE_FILE.with_name("xml_to_plist.py")
        exported = self.home / "export.plist"
        result = subprocess.run([sys.executable, str(converter), "--input", str(self.source),
                                 "--output", str(exported)], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.agents.exists())
        self.ok(self.invoke("install-plist", "--input", str(exported), "--name", "demo",
                            "--description", "转换导入集成测试", "--activate"))
        self.assertTrue(self.path().exists())
        self.assertIn(self.label(), self.fake.loaded)
        self.assertIn("自定义 XML", self.ok(self.invoke("list")))
        self.assertIn("StartInterval", self.ok(self.invoke("status", "--label", self.label())))
        self.ok(self.invoke("describe", "--label", self.label(), "--description", "新说明"))
        self.ok(self.invoke("run", "--label", self.label()))
        self.ok(self.invoke("uninstall", "--label", self.label()))
        self.assertFalse(self.path().exists())
        self.assertFalse(la.metadata_path(self.label()).exists())
        self.assertNotIn(self.label(), self.fake.loaded)
        self.assertTrue(exported.exists())
        self.assertTrue((self.logs / (self.label() + ".stdout.log")).exists())

    def test_dry_run_has_no_files_or_launchctl(self):
        out = self.ok(self.install("--dry-run", "--activate"))
        self.assertIn("只预览", out)
        self.assertIn(self.label(), out)
        self.assertFalse(self.agents.exists())
        self.assertFalse(self.meta.exists())
        self.assertEqual(self.fake.calls, [])

    def test_default_custom_install_only_writes_and_preserves_arguments(self):
        self.ok(self.install())
        obj = plistlib.loads(self.path().read_bytes())
        for key, value in self.payload.items():
            self.assertEqual(obj[key], value)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(obj["Label"], self.label())
        self.assertEqual(obj["WorkingDirectory"], str(self.home.resolve()))
        self.assertEqual(obj["Umask"], 63)
        self.assertEqual(obj["StandardOutPath"], str(self.logs / (self.label() + ".stdout.log")))
        record = la.inspect_metadata(self.label())[1]
        self.assertEqual(record["source_type"], "plist")
        self.assertEqual(record["source_file"], str(self.source.resolve()))
        self.assertEqual(record["source_sha256"], la.sha256_bytes(self.source.read_bytes()))
        self.assertIsNone(record["last_registered_sha256"])

    def test_input_label_is_provenance_never_a_service_target(self):
        self.payload["Label"] = "com.example.previous"
        self.write_source()
        self.ok(self.install("--activate"))
        record = la.inspect_metadata(self.label())[1]
        self.assertEqual(record["source_label"], "com.example.previous")
        self.assertNotIn("com.example.previous", repr(self.fake.calls))
        self.assertEqual(plistlib.loads(self.source.read_bytes())["Label"], "com.example.previous")

    def test_identity_same_name_all_three_modes_are_independent(self):
        login = la.make_label("echo hi", "same", "login")
        calendar = la.make_label("echo hi", "same", "calendar")
        custom = la.make_label("", "same", "custom")
        self.assertEqual(len({login, calendar, custom}), 3)
        for label in (login, calendar, custom):
            la.validate_label(label)
        self.ok(self.invoke("install", "--command", "echo hi", "--name", "same",
                            "--description", "登录任务", "--start-now"))
        self.ok(self.invoke("install", "--command", "echo hi", "--name", "same",
                            "--mode", "calendar", "--time", "09:00", "--description", "日历任务"))
        self.ok(self.install("--activate", name="same"))
        self.assertEqual(set(self.fake.loaded), {login, calendar, custom})
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_multiple_independent_calendar_dicts_are_preserved_not_cross_product(self):
        self.payload.pop("StartInterval")
        calendar = [{"Weekday": 1, "Hour": 9, "Minute": 0},
                    {"Weekday": 5, "Hour": 18, "Minute": 30}]
        self.payload["StartCalendarInterval"] = calendar
        self.write_source()
        self.ok(self.install())
        self.assertEqual(plistlib.loads(self.path().read_bytes())["StartCalendarInterval"], calendar)
        out = self.ok(self.invoke("status", "--label", self.label()))
        self.assertIn("自定义 XML", out)
        self.assertEqual(la.inspect_metadata(self.label())[1]["mode"], "custom")

    def test_keepalive_watchpaths_sockets_future_keys_remain(self):
        self.payload.update(KeepAlive={"SuccessfulExit": False}, WatchPaths=["/tmp/watched"],
                            QueueDirectories=["/tmp/inbox"], Sockets={"Listener": {"SockServiceName": "9000"}},
                            FutureKey={"type": b"raw", "flag": True})
        self.write_source()
        self.ok(self.install("--activate"))
        obj = plistlib.loads(self.path().read_bytes())
        for key, value in self.payload.items():
            self.assertEqual(obj[key], value)
        self.assertFalse(any(c[0] == "kickstart" for c in self.fake.calls))

    def test_missing_name_description_and_wrong_xml_do_not_install(self):
        self.assertEqual(self.invoke("install-plist", "--input", str(self.source))[0], 2)
        self.assertEqual(self.invoke("install-plist", "--input", str(self.source), "--name", "x")[0], 1)
        self.write_source(["not a job"])
        rc, _, err = self.install()
        self.assertEqual(rc, 1)
        self.assertIn("必须是字典", err)
        self.assertFalse(self.agents.exists())

    def test_invalid_field_types_rejected_before_any_state_change(self):
        invalid = [{"StartInterval": True}, {"StartInterval": 0}, {"RunAtLoad": "false"},
                   {"ProgramArguments": "echo hi"}, {"ProgramArguments": ["relative"]},
                   {"EnvironmentVariables": {"X": 3}}, {"WatchPaths": ["~/x"]},
                   {"KeepAlive": []}, {"StartCalendarInterval": {"Month": 2, "Day": 30}},
                   {"UserName": "root"}, {"RootDirectory": "/"}, {"BundleProgram": "app"}]
        for change in invalid:
            with self.subTest(change=change):
                self.write_source(dict(self.payload, **change))
                self.assertEqual(self.install()[0], 1)
                self.assertFalse(self.agents.exists())
        self.assertEqual(self.fake.calls, [])

    def test_conflicting_managed_logs_and_umask_are_not_silently_overwritten(self):
        for key, value in (("StandardOutPath", "/tmp/log"), ("StandardErrorPath", "/tmp/err"), ("Umask", 18)):
            with self.subTest(key=key):
                self.write_source(dict(self.payload, **{key: value}))
                self.assertIn(key, self.install()[2])
                self.assertFalse(self.agents.exists())

    def test_program_only_and_explicit_argv0_work(self):
        for payload in ({"Program": "/bin/echo", "StartInterval": 80},
                        {"Program": "/bin/echo", "ProgramArguments": ["custom-argv0", "one"], "StartInterval": 90}):
            with self.subTest(payload=payload):
                self.write_source(payload)
                self.ok(self.install("--replace"))
                self.ok(self.invoke("status", "--label", self.label()))

    def test_binary_input_uses_same_boundary_and_gets_owned_xml(self):
        self.write_source(fmt=plistlib.FMT_BINARY)
        self.ok(self.install())
        self.assertTrue(self.path().read_bytes().startswith(b"<?xml"))
        self.assertEqual(plistlib.loads(self.path().read_bytes())["ProgramArguments"], self.payload["ProgramArguments"])

    def test_duplicate_xml_keys_cannot_bypass_converter_by_importing_directly(self):
        self.source.write_text('<plist><dict><key>Program</key><string>/bin/true</string><key>Program</key><string>/bin/false</string></dict></plist>')
        self.assertIn("重复", self.install()[2])
        self.assertFalse(self.agents.exists())

    def test_reserved_source_label_and_automatic_directory_not_adopted(self):
        self.write_source(dict(self.payload, Label=self.label()))
        self.assertIn("托管 Label", self.install()[2])
        self.write_source()
        self.agents.mkdir(parents=True)
        active = self.agents / "other.plist"
        active.write_bytes(self.source.read_bytes())
        rc, _, err = self.invoke("install-plist", "--input", str(active), "--name", "x", "--description", "不接管")
        self.assertEqual(rc, 1)
        self.assertIn("自动加载目录", err)
        self.assertTrue(active.exists())
        self.assertEqual(self.fake.calls, [])

    def test_unchanged_activation_is_idempotent_and_changes_require_replace(self):
        self.ok(self.install("--activate"))
        before = len([c for c in self.fake.calls if c[0] == "bootstrap"])
        self.ok(self.install("--activate"))
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "bootstrap"]), before)
        self.payload["StartInterval"] = 600
        self.write_source()
        self.assertIn("--replace", self.install("--activate")[2])
        self.ok(self.install("--replace"))
        self.assertIn("疑似漂移", self.ok(self.invoke("status", "--label", self.label())))
        self.assertIn("可能执行旧命令", self.invoke("run", "--label", self.label())[2])
        self.ok(self.invoke("register", "--label", self.label(), "--allow-execution"))
        self.assertEqual(self.fake.loaded[self.label()], self.path().read_bytes())
        self.ok(self.invoke("run", "--label", self.label()))

    def test_source_deletion_does_not_break_management_or_registration(self):
        self.ok(self.install())
        self.source.unlink()
        self.ok(self.invoke("register", "--label", self.label(), "--allow-execution"))
        self.ok(self.invoke("status", "--label", self.label()))
        self.ok(self.invoke("uninstall", "--label", self.label()))

    def test_register_requires_explicit_execution_risk_consent(self):
        self.ok(self.install())
        before = list(self.fake.calls)
        self.assertIn("--allow-execution", self.invoke("register", "--label", self.label())[2])
        self.assertEqual(self.fake.calls, before)

    def test_register_repairs_snapshot_for_existing_fixed_login_and_calendar(self):
        for mode in ("login", "calendar"):
            with self.subTest(mode=mode):
                arguments = ("install", "--command", "echo hello", "--name", "fixed",
                             "--description", "既有任务", "--mode", mode)
                if mode == "calendar":
                    arguments += ("--defer", "--time", "09:00")
                self.ok(self.invoke(*arguments))
                label = la.make_label("echo hello", "fixed", mode)
                self.ok(self.invoke("register", "--label", label, "--allow-execution"))
                record = la.inspect_metadata(label)[1]
                self.assertIsNotNone(record["last_registered_sha256"])
                self.ok(self.invoke("run", "--label", label))

    def test_foreign_plist_and_unrecorded_live_label_remain_untouched(self):
        self.agents.mkdir(parents=True)
        payload = prepare_payload(self.payload, self.label(), self.logs, self.home)
        self.path().write_bytes(la.to_plist_bytes(payload))
        self.assertIn("管理记录", self.install("--activate", "--replace")[2])
        self.path().unlink()
        self.fake.loaded[self.label()] = la.to_plist_bytes(payload)
        self.assertIn("未知 plist", self.install("--activate")[2])
        self.assertIn(self.label(), self.fake.loaded)
        self.assertFalse(self.path().exists())
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_gui_unavailable_blocks_activation_but_allows_disk_only(self):
        self.fake.gui_available = False
        self.assertEqual(self.install("--activate")[0], 1)
        self.assertFalse(self.agents.exists())
        before = list(self.fake.calls)
        self.ok(self.install())
        self.assertEqual(self.fake.calls, before)

    def test_new_bootstrap_failure_removes_owned_outputs_not_source(self):
        self.fake.fail_next = "bootstrap"
        self.assertEqual(self.install("--activate")[0], 1)
        self.assertFalse(self.path().exists())
        self.assertFalse(la.metadata_path(self.label()).exists())
        self.assertNotIn(self.label(), self.fake.loaded)
        self.assertTrue(self.source.exists())

    def test_existing_custom_bootstrap_failure_restores_disk_live_metadata(self):
        self.ok(self.install("--activate"))
        old_data = self.path().read_bytes()
        old_record = la.metadata_path(self.label()).read_bytes()
        self.payload["StartInterval"] = 650
        self.write_source()
        self.fake.fail_next = "bootstrap"
        rc, _, err = self.install("--replace", "--activate")
        self.assertEqual(rc, 1)
        self.assertIn("自定义任务", err)
        self.assertIn("无法撤销", err)
        self.assertEqual(self.path().read_bytes(), old_data)
        self.assertEqual(self.fake.loaded[self.label()], old_data)
        self.assertEqual(la.metadata_path(self.label()).read_bytes(), old_record)

    def test_metadata_commit_failure_rolls_back_custom_registration(self):
        self.ok(self.install("--activate"))
        old = self.path().read_bytes()
        self.payload["StartInterval"] = 123
        self.write_source()
        with patch.object(la, "write_metadata", side_effect=OSError("disk full")):
            self.assertEqual(self.install("--replace", "--activate")[0], 1)
        self.assertEqual(self.path().read_bytes(), old)
        self.assertEqual(self.fake.loaded[self.label()], old)
        self.assertEqual(la.inspect_metadata(self.label())[1]["disk_sha256"], la.sha256_bytes(old))

    def test_custom_accept_reset_rebuild_orphan_recovery(self):
        self.ok(self.install("--activate"))
        payload = plistlib.loads(self.path().read_bytes())
        payload["ProgramArguments"] = ["/bin/echo", "changed"]
        self.path().write_bytes(la.to_plist_bytes(payload))
        for argv in (("register", "--allow-execution"), ("run",), ("uninstall",)):
            self.assertEqual(self.invoke(*argv, "--label", self.label())[0], 1)
        self.ok(self.invoke("repair", "--label", self.label(), "--action", "accept-plist", "--trust-current-plist"))
        record = la.inspect_metadata(self.label())[1]
        self.assertEqual(record["source_type"], "plist")
        self.assertIsNone(record["source_file"])
        self.assertIn("已接受外部改动", self.invoke("run", "--label", self.label())[2])
        self.ok(self.invoke("repair", "--label", self.label(), "--action", "reset-snapshot", "--trust-current-plist"))
        self.ok(self.invoke("register", "--label", self.label(), "--allow-execution"))
        la.metadata_path(self.label()).write_text("{bad")
        self.ok(self.invoke("repair", "--label", self.label(), "--action", "rebuild-record",
                            "--trust-current-plist", "--description", "重建自定义配置"))
        self.assertEqual(la.inspect_metadata(self.label())[1]["mode"], "custom")
        self.assertIn("管理记录已重建", self.invoke("run", "--label", self.label())[2])
        self.path().unlink()
        self.assertIn("孤立管理记录", self.ok(self.invoke("list")))
        self.ok(self.invoke("repair", "--label", self.label(), "--action", "remove-orphan", "--confirm-stop"))
        self.assertNotIn(self.label(), self.fake.loaded)

    def test_unknown_snapshot_with_matching_hash_forces_new_registration(self):
        self.ok(self.install("--activate"))
        record = la.inspect_metadata(self.label())[1]
        record.update(registration_snapshot_unknown=True, registration_snapshot_unknown_reason="rebuilt")
        la.write_metadata(self.label(), record)
        count = len([c for c in self.fake.calls if c[0] == "bootstrap"])
        self.ok(self.install("--activate"))
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "bootstrap"]), count + 1)
        self.assertFalse(la.inspect_metadata(self.label())[1]["registration_snapshot_unknown"])

    def test_register_race_does_not_overwrite_new_disk_configuration(self):
        self.ok(self.install())
        original = la.commit_install
        def race(args, **kwargs):
            obj = plistlib.loads(self.path().read_bytes())
            obj["StartInterval"] = 601
            self.path().write_bytes(la.to_plist_bytes(obj))
            return original(args, **kwargs)
        with patch.object(la, "commit_install", side_effect=race):
            rc, _, err = self.invoke("register", "--label", self.label(), "--allow-execution")
        self.assertEqual(rc, 1)
        self.assertIn("发生变化", err)
        self.assertEqual(plistlib.loads(self.path().read_bytes())["StartInterval"], 601)
        self.assertFalse(any(c[0] == "bootstrap" for c in self.fake.calls))

    def test_uninstall_must_confirm_service_absent_after_successful_bootout(self):
        self.ok(self.install("--activate"))
        fake = self.fake
        def ignores_bootout(*args, **kwargs):
            if args[0] == "bootout":
                return subprocess.CompletedProcess(args, 0, "", "")
            return fake(*args, **kwargs)
        with patch.object(la, "launchctl", side_effect=ignores_bootout):
            rc, _, err = self.invoke("uninstall", "--label", self.label())
        self.assertEqual(rc, 1)
        self.assertTrue(self.path().exists())
        self.assertTrue(la.metadata_path(self.label()).exists())
        self.assertIn(self.label(), self.fake.loaded)

    def test_rollback_updates_snapshot_when_preexisting_disk_was_deferred(self):
        self.ok(self.install("--activate"))
        self.payload["StartInterval"] = 600
        self.write_source()
        self.ok(self.install("--replace"))
        deferred_data = self.path().read_bytes()
        self.assertNotEqual(self.fake.loaded[self.label()], deferred_data)
        self.payload["StartInterval"] = 900
        self.write_source()
        self.fake.fail_next = "bootstrap"
        self.assertEqual(self.install("--replace", "--activate")[0], 1)
        self.assertEqual(self.path().read_bytes(), deferred_data)
        self.assertEqual(self.fake.loaded[self.label()], deferred_data)
        record = la.inspect_metadata(self.label())[1]
        self.assertEqual(record["last_registered_sha256"], la.sha256_bytes(deferred_data))
        self.assertFalse(record["registration_snapshot_unknown"])

    def test_custom_summary_does_not_crash_on_unvalidated_future_nested_type(self):
        # 语法上合法，但深层 Sockets 语义尚未验证；预览/状态应展示而非因 JSON 序列化崩溃。
        self.payload["Sockets"] = {"Future": {"Opaque": b"abc"}}
        self.write_source()
        self.ok(self.install())
        self.ok(self.invoke("status", "--label", self.label()))

    def test_case_insensitive_automatic_directory_source_is_rejected(self):
        folder = self.home / "Library" / "launchagents"
        folder.mkdir(parents=True)
        source = folder / "another.plist"
        source.write_bytes(self.source.read_bytes())
        result = self.invoke("install-plist", "--input", str(source), "--name", "new", "--description", "不接管")
        self.assertEqual(result[0], 1)
        self.assertIn("自动加载目录", result[2])
        self.assertEqual(self.fake.calls, [])
    def test_uncertain_rollback_keeps_existing_snapshot_blocked(self):
        self.ok(self.install("--activate"))
        self.payload["StartInterval"] = 120
        self.write_source()
        fake, stopped = self.fake, 0
        def broken_rollback_stop(*args, **kwargs):
            nonlocal stopped
            if args[0] == "bootout":
                stopped += 1
                if stopped == 2:
                    return subprocess.CompletedProcess(args, 0, "", "")
            return fake(*args, **kwargs)
        with patch.object(la, "launchctl", side_effect=broken_rollback_stop), \
             patch.object(la, "write_metadata", side_effect=OSError("cannot commit metadata")):
            rc, _, err = self.install("--replace", "--activate")
        self.assertEqual(rc, 1)
        self.assertIn("回滚未完成", err)
        record = la.inspect_metadata(self.label())[1]
        self.assertTrue(record["registration_snapshot_unknown"])
        self.assertEqual(record["registration_snapshot_unknown_reason"], "recovery-failed")
        self.assertEqual(self.invoke("run", "--label", self.label())[0], 1)

    def test_partial_new_install_that_cannot_stop_stays_visible_and_manageable(self):
        fake = self.fake
        def cannot_stop(*args, **kwargs):
            if args[0] == "bootout":
                return subprocess.CompletedProcess(args, 0, "", "")
            return fake(*args, **kwargs)
        with patch.object(la, "launchctl", side_effect=cannot_stop), \
             patch.object(la, "write_metadata", side_effect=OSError("cannot commit metadata")):
            rc, _, err = self.install("--activate")
        self.assertEqual(rc, 1)
        self.assertTrue(self.path().exists())
        record = la.inspect_metadata(self.label())[1]
        self.assertIsNotNone(record)
        self.assertTrue(record["registration_snapshot_unknown"])
        self.assertIn(self.label(), self.ok(self.invoke("list")))
        self.assertIn("回滚", self.invoke("run", "--label", self.label())[2])
        self.ok(self.invoke("uninstall", "--label", self.label()))

    def test_unactivated_custom_never_claims_registration(self):
        result = self.install()
        out = self.ok(result)
        self.assertIn("\u672c\u6b21\u672a\u6ce8\u518c", out)
        self.assertNotIn("\u6ce8\u518c\u6210\u529f", out)
        self.assertNotIn("kickstart", out)
        self.assertEqual(self.fake.calls, [])

    def test_activated_custom_reports_registration_cautiously(self):
        out = self.ok(self.install("--activate"))
        self.assertIn("\u6ce8\u518c\u6210\u529f\u4e0d\u4ee3\u8868", out)
        self.assertIn("kickstart", out)
        self.assertIn(self.label(), self.fake.loaded)

    def test_new_prefix_flows_to_custom_and_converter(self):
        self.assertTrue(self.label().startswith("local.my.agents.custom."))
        from xml_to_plist import RESERVED_LABEL_PREFIX
        self.assertEqual(RESERVED_LABEL_PREFIX, "local.my.agents.")

if __name__ == "__main__":
    unittest.main()