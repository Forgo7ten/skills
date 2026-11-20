"""Offline regression tests. Simulates launchctl without running any user's command."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_FILE = Path(__file__).resolve().parents[1] / "scripts" / "launch_agent.py"
sys.path.insert(0, str(MODULE_FILE.parent))
spec = importlib.util.spec_from_file_location("launch_agent", MODULE_FILE)
la = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(la)
ORIGINAL_REQUIRE_MACOS = la.require_macos


class FakeLaunchctl:
    def __init__(self):
        self.loaded: dict[str, bytes] = {}
        self.calls: list[tuple[str, ...]] = []
        self.fail_next: str | None = None
        self.gui_available = True

    def __call__(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        verb = args[0]
        if verb == self.fail_next:
            self.fail_next = None
            if check:
                raise RuntimeError("simulated " + verb + " failure")
            return subprocess.CompletedProcess(args, 7, "", "simulated failure")
        if verb == "print":
            if args[1].count("/") == 1:
                return subprocess.CompletedProcess(args, 0 if self.gui_available else 113, "GUI domain", "")
            label = args[1].split("/")[-1]
            if label in self.loaded:
                return subprocess.CompletedProcess(args, 0, "loaded " + label + "\n", "")
            return subprocess.CompletedProcess(args, 113, "", f'Bad request.\nCould not find service "{label}" in domain for user gui: {os.getuid()}')
        if verb == "bootout":
            label = args[1].split("/")[-1]
            self.loaded.pop(label, None)
        if verb == "bootstrap":
            contents = Path(args[2]).read_bytes()
            label = plistlib.loads(contents)["Label"]
            self.loaded[label] = contents
        return subprocess.CompletedProcess(args, 0, "", "")


class LaunchAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.logs = self.root / "Library" / "Logs" / "LaunchAgents"
        self.metadata = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = FakeLaunchctl()
        patches = [
            patch.object(la, "LAUNCH_AGENTS_DIRECTORY", self.agents),
            patch.object(la, "LOG_DIRECTORY", self.logs),
            patch.object(la, "METADATA_DIRECTORY", self.metadata),
            patch.object(la, "DEFAULT_WORKING_DIRECTORY", self.root),
            patch.object(la, "require_macos", lambda: None),
            patch.object(la, "launchctl", self.fake),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def invoke(self, *argv: str, command_input: str | None = None, auto_description: bool = True) -> tuple[int, str, str]:
        if auto_description and argv and argv[0] == "install" and "--description" not in argv:
            argv = (*argv, "--description", "测试任务用途")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(MODULE_FILE), *argv]), \
                patch.object(sys, "stdin", io.StringIO(command_input or "")), \
                patch.object(sys, "stdout", stdout), \
                patch.object(sys, "stderr", stderr):
            result = la.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def path_for(self, command: str, name: str | None = None) -> Path:
        return self.agents / f"{la.make_label(command, name)}.plist"

    def test_label_is_stable_and_safe(self):
        command = '/usr/local/bin/say "你好 hello"'
        label = la.make_label(command, None)
        self.assertEqual(label, la.make_label(command, None))
        la.validate_label(label)
        self.assertRegex(label, r"^local\.my\.agents\.[a-z0-9-]+-[0-9a-f]{8}$")
        self.assertNotEqual(label, la.make_label(command + " -v", None))
        self.assertEqual(la.make_label(command, "My Job"), la.make_label("echo 3", "My Job"))

    def test_command_stdin_exact_quotes_multiline_and_single_newline_strip(self):
        raw = 'echo "$(date)"\nprintf "中文\\n"\n\n'
        parsed = la.make_parser().parse_args(["install", "--command-stdin"])
        with patch.object(sys, "stdin", io.StringIO(raw)):
            self.assertEqual(la.get_command(parsed), raw[:-1])

    def test_script_mode_exact_absolute_path_with_spaces(self):
        script = self.root / "safe script.sh"
        script.write_text("#!/bin/sh\necho 'hello'\n")
        script.chmod(0o700)
        code, _, err = self.invoke("install", "--script", str(script))
        self.assertEqual((code, err), (0, ""))
        import shlex
        resolved_script = script.resolve(strict=True)
        expected = shlex.quote(str(resolved_script))
        payload = plistlib.loads(self.path_for(expected).read_bytes())
        self.assertEqual(payload["ProgramArguments"], ["/bin/zsh", "-lc", expected])

    def test_script_mode_rejects_ambiguous_or_unsafe_script(self):
        non_executable = self.root / "no-exec.sh"
        non_executable.write_text("#!/bin/sh\necho hi\n")
        non_executable.chmod(0o600)
        code, _, err = self.invoke("install", "--script", str(non_executable))
        self.assertEqual(code, 1)
        self.assertIn("没有可执行权限", err)
        non_executable.chmod(0o700)
        non_executable.write_text("echo hi\n")
        code, _, err = self.invoke("install", "--script", str(non_executable))
        self.assertEqual(code, 1)
        self.assertIn("缺少有效的 shebang", err)
        code, _, err = self.invoke("install", "--script", "./relative.sh")
        self.assertEqual(code, 1)
        self.assertIn("绝对路径", err)
        self.assertFalse(self.agents.exists())

    def test_empty_and_nul_commands_rejected(self):
        for command in ["", "   ", "echo hi\0bad"]:
            code, _, err = self.invoke("install", "--command", command, "--dry-run")
            self.assertEqual(code, 1)
            self.assertIn("错误：", err)

    def test_blank_name_and_lossy_xml_are_rejected_without_writing(self):
        code, _, err = self.invoke("install", "--command", "echo hi", "--name", "  ")
        self.assertEqual(code, 1)
        self.assertIn("不能为空", err)
        code, _, err = self.invoke("install", "--command", "echo before\rafter")
        self.assertEqual(code, 1)
        self.assertIn("避免数据丢失", err)
        self.assertFalse(self.agents.exists())

    def test_working_directory_validation(self):
        self.assertEqual(la.get_working_directory(str(self.root)), self.root.resolve())
        with self.assertRaises(ValueError):
            la.get_working_directory("relative/directory")
        with self.assertRaises(ValueError):
            la.get_working_directory(str(self.root / "missing"))

    def test_fixed_plist_keys_and_types(self):
        label = la.make_label("echo hi", None)
        data = la.build_plist(label, "echo hi", self.root, False)
        self.assertEqual(set(data), {"Label", "ProgramArguments", "RunAtLoad", "KeepAlive", "WorkingDirectory", "StandardOutPath", "StandardErrorPath", "Umask"})
        self.assertEqual(data["ProgramArguments"], ["/bin/zsh", "-lc", "echo hi"])
        self.assertIs(data["RunAtLoad"], True)
        self.assertIs(data["KeepAlive"], False)
        self.assertEqual(data["Umask"], 63)
        self.assertEqual(plistlib.loads(la.to_plist_bytes(data)), data)

    def test_dry_run_has_no_files_or_launchctl_calls(self):
        code, out, err = self.invoke("install", "--command-stdin", "--dry-run", command_input='echo "<> && 中国"\n')
        self.assertEqual((code, err), (0, ""))
        self.assertIn("&lt;&gt; &amp;&amp;", out)
        self.assertIn("中国", out)
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.fake.calls, [])

    def test_default_install_does_not_start_now(self):
        cmd = "echo safe"
        code, out, err = self.invoke("install", "--command", cmd)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("本次安装不会注册或执行命令", out)
        self.assertEqual(self.fake.calls, [])
        path = self.path_for(cmd)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        plist = plistlib.loads(path.read_bytes())
        self.assertEqual(plist["ProgramArguments"][-1], cmd)
        self.assertEqual(self.logs.stat().st_mode & 0o777, 0o700)
        for suffix in ["stdout", "stderr"]:
            self.assertEqual((self.logs / f"{path.stem}.{suffix}.log").stat().st_mode & 0o777, 0o600)

    def test_idempotent_install_and_keeps_current_job_running(self):
        cmd = "echo once"
        code, _, _ = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 0)
        label = la.make_label(cmd, None)
        old = self.fake.loaded[label]
        code, out, _ = self.invoke("install", "--command", cmd)
        self.assertEqual(code, 0)
        self.assertIn("plist 内容未变化", out)
        self.assertEqual(old, self.fake.loaded[label])
        self.assertEqual([c[0] for c in self.fake.calls].count("bootout"), 0)

    def test_start_now_registers_service(self):
        cmd = "sleep 30"
        code, out, _ = self.invoke("install", "--name", "Runner", "--command", cmd, "--start-now")
        self.assertEqual(code, 0)
        self.assertIn("已安装并注册登录自启动任务", out)
        self.assertIn("不代表命令仍在运行", out)
        self.assertIn(la.make_label(cmd, "Runner"), self.fake.loaded)
        self.assertIn("bootstrap", [c[0] for c in self.fake.calls])

    def test_keep_alive_and_working_directory_are_explicit_opt_ins(self):
        cmd = "sleep 30"
        target_workdir = self.root / "workspace"
        target_workdir.mkdir()
        code, _, err = self.invoke("install", "--command", cmd, "--keep-alive", "--working-directory", str(target_workdir))
        self.assertEqual((code, err), (0, ""))
        payload = plistlib.loads(self.path_for(cmd).read_bytes())
        self.assertIs(payload["KeepAlive"], True)
        self.assertEqual(payload["WorkingDirectory"], str(target_workdir.resolve()))

    def test_invalid_label_does_not_unload(self):
        for label in ["com.apple.login", "../../Library", "local.my.agents.name-", "local.my.agents.name-abc"]:
            code, _, err = self.invoke("uninstall", "--label", label)
            self.assertEqual(code, 1)
            self.assertIn("标识无效", err)
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_update_without_start_now_only_changes_disk(self):
        cmd1, cmd2, name = "echo OLD", "echo NEW", "Same name"
        self.assertEqual(self.invoke("install", "--command", cmd1, "--name", name, "--start-now")[0], 0)
        label = la.make_label(cmd1, name)
        old_live = self.fake.loaded[label]
        self.assertEqual(self.invoke("install", "--command", cmd2, "--name", name, "--replace")[0], 0)
        self.assertEqual(self.fake.loaded[label], old_live)
        self.assertEqual(plistlib.loads(self.path_for(cmd2, name).read_bytes())["ProgramArguments"][-1], cmd2)

    def test_update_with_start_now_replaces_running_job(self):
        cmd1, cmd2, name = "echo OLD", "echo NEW", "Same name"
        self.assertEqual(self.invoke("install", "--command", cmd1, "--name", name, "--start-now")[0], 0)
        self.assertEqual(self.invoke("install", "--command", cmd2, "--name", name, "--replace", "--start-now")[0], 0)
        label = la.make_label(cmd2, name)
        self.assertEqual(plistlib.loads(self.fake.loaded[label])["ProgramArguments"][-1], cmd2)
        self.assertIn("bootout", [c[0] for c in self.fake.calls])

    def test_different_contents_refuse_silent_overwrite(self):
        first, new, name = "echo first", "echo second", "Safe name"
        self.assertEqual(self.invoke("install", "--command", first, "--name", name)[0], 0)
        path = self.path_for(first, name)
        old_data = path.read_bytes()
        code, _, err = self.invoke("install", "--command", new, "--name", name)
        self.assertEqual(code, 1)
        self.assertIn("拒绝静默覆盖", err)
        self.assertEqual(path.read_bytes(), old_data)
        self.assertEqual(self.fake.calls, [])

    def test_install_without_gui_session_only_writes_plist(self):
        self.fake.gui_available = False
        command = "/usr/bin/say 'later'"
        code, out, err = self.invoke("install", "--command", command)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(self.path_for(command).exists())
        self.assertIn("下次图形用户登录", out)
        self.assertEqual(self.fake.calls, [])

    def test_start_now_without_gui_refuses_before_disk_side_effects(self):
        self.fake.gui_available = False
        cmd = "echo need gui"
        code, _, err = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("launchd 域不可用", err)
        self.assertFalse(self.agents.exists())
        self.assertFalse(any(c[0] == "bootstrap" for c in self.fake.calls))

    def test_bootstrap_failure_on_new_install_restores_missing_file(self):
        cmd = "echo CANNOTSTART"
        self.fake.fail_next = "bootstrap"
        code, _, err = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("simulated bootstrap failure", err)
        self.assertFalse(self.path_for(cmd).exists())
        self.assertNotIn(la.make_label(cmd, None), self.fake.loaded)

    def test_bootstrap_failure_on_update_restores_old_job_and_plist(self):
        oldcmd, newcmd, name = "echo old", "echo new", "My Label"
        self.assertEqual(self.invoke("install", "--command", oldcmd, "--name", name, "--start-now")[0], 0)
        path = self.path_for(oldcmd, name)
        old_bytes = path.read_bytes()
        self.fake.fail_next = "bootstrap"
        code, _, err = self.invoke("install", "--command", newcmd, "--name", name, "--replace", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("已尝试回滚", err)
        self.assertEqual(path.read_bytes(), old_bytes)
        self.assertEqual(self.fake.loaded[path.stem], old_bytes)

    def test_bootout_failure_does_not_interrupt_running_old_job(self):
        oldcmd, newcmd, name = "echo OLD", "echo NEW", "Existing job"
        self.assertEqual(self.invoke("install", "--command", oldcmd, "--name", name, "--start-now")[0], 0)
        path = self.path_for(oldcmd, name)
        old_data = path.read_bytes()
        self.fake.fail_next = "bootout"
        code, _, err = self.invoke("install", "--command", newcmd, "--name", name, "--replace", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("simulated bootout failure", err)
        self.assertEqual(path.read_bytes(), old_data)
        self.assertEqual(self.fake.loaded[path.stem], old_data)

    def test_install_never_modifies_persistent_enable_state(self):
        cmd = "echo enable"
        self.fake.fail_next = "enable"
        code, _, err = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 0)
        self.assertNotIn("enable", [c[0] for c in self.fake.calls])
        self.assertTrue(self.path_for(cmd).exists())

    def test_reject_foreign_plist_same_label_before_touching_service(self):
        cmd = "echo safe"
        label = la.make_label(cmd, None)
        self.agents.mkdir(parents=True)
        target = self.path_for(cmd)
        target.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/bin/bash", "-c", "echo foreign"]}))
        code, _, err = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("拒绝操作无法识别的 plist", err)
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))
        self.assertTrue(target.exists())

    def test_malformed_plist_is_rejected_without_crash_or_service_change(self):
        cmd = "echo corrupt"
        self.agents.mkdir(parents=True)
        target = self.path_for(cmd)
        target.write_bytes(b'<?xml version="1.0"?><plist><dict><key>A</key><string>oops</dict></plist>')
        code, _, err = self.invoke("install", "--command", cmd, "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("拒绝操作无法识别的 plist", err)
        self.assertEqual(target.read_bytes().count(b"oops"), 1)
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_rollback_if_print_fails_after_bootstrap(self):
        cmd1, cmd2, name = "echo OLD", "echo NEW", "Crash test"
        self.assertEqual(self.invoke("install", "--command", cmd1, "--name", name, "--start-now")[0], 0)
        path = self.path_for(cmd1, name)
        old_data = path.read_bytes()
        class FailSecondPrint(FakeLaunchctl):
            pass
        original_call = self.fake.__call__
        do_fail = False
        def mocked_launchctl(*args, check=True):
            nonlocal do_fail
            if args[0] == "bootstrap":
                do_fail = True
            elif args[0] == "print" and do_fail and args[1].count("/") == 2:
                do_fail = False
                raise RuntimeError("simulated post-bootstrap print failure")
            return original_call(*args, check=check)
        with patch.object(la, "launchctl", mocked_launchctl):
            code, _, err = self.invoke("install", "--command", cmd2, "--name", name, "--replace", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("无法确认服务", err)
        self.assertIn("状态查询", err)
        self.assertNotIn("simulated post-bootstrap print failure", err)
        self.assertEqual(path.read_bytes(), old_data)
        self.assertEqual(self.fake.loaded[path.stem], old_data)

    def test_reject_symlink_plist_without_overwrite(self):
        cmd = "echo link"
        self.agents.mkdir(parents=True)
        external = self.root / "external"
        external.write_text("do not touch")
        self.path_for(cmd).symlink_to(external)
        code, _, err = self.invoke("install", "--command", cmd)
        self.assertEqual(code, 1)
        self.assertIn("不安全、或不属于当前用户的 plist", err)
        self.assertEqual(external.read_text(), "do not touch")

    def test_reject_symlink_log(self):
        cmd = "echo log"
        self.logs.mkdir(parents=True)
        external = self.root / "external"
        external.write_text("do not touch")
        path = self.logs / f"{la.make_label(cmd, None)}.stdout.log"
        path.symlink_to(external)
        code, _, err = self.invoke("install", "--command", cmd)
        self.assertEqual(code, 1)
        self.assertIn("现有日志文件不安全", err)
        self.assertEqual(external.read_text(), "do not touch")

    def test_uninstall_boots_out_and_leaves_logs(self):
        cmd = "echo remove"
        self.assertEqual(self.invoke("install", "--command", cmd, "--start-now")[0], 0)
        label = la.make_label(cmd, None)
        code, out, _ = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 0)
        self.assertIn("历史日志会保留", out)
        self.assertFalse(self.path_for(cmd).exists())
        self.assertNotIn(label, self.fake.loaded)
        self.assertTrue((self.logs / f"{label}.stdout.log").exists())
        self.assertFalse(la.metadata_path(label).exists())

    def test_uninstall_missing_does_not_touch_loaded_service(self):
        label = la.make_label("echo anything", None)
        self.fake.loaded[label] = b"other"
        code, _, _ = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 0)
        self.assertIn(label, self.fake.loaded)
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_uninstall_symlink_rejected_before_bootout(self):
        cmd = "echo symlink"
        self.agents.mkdir(parents=True)
        label = la.make_label(cmd, None)
        self.path_for(cmd).symlink_to(self.root / "other")
        self.fake.loaded[label] = b"other"
        code, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("管理记录", err)
        self.assertIn(label, self.fake.loaded)
        self.assertFalse(any(c[0] == "bootout" for c in self.fake.calls))

    def test_status_has_useful_exit_codes(self):
        cmd = "echo status"
        label = la.make_label(cmd, None)
        self.assertEqual(self.invoke("status", "--label", label)[0], 1)
        self.assertEqual(self.invoke("install", "--command", cmd)[0], 0)
        self.assertEqual(self.invoke("status", "--label", label)[0], 0)
        self.assertEqual(self.invoke("install", "--command", cmd, "--start-now")[0], 0)
        code, out, _ = self.invoke("status", "--label", label)
        self.assertEqual(code, 0)
        self.assertIn("当前状态：已加载", out)

    def test_unmanaged_incomplete_plist_not_adopted(self):
        cmd = "echo unmanaged incomplete"
        label = la.make_label(cmd, None)
        payload = la.build_plist(label, cmd, self.root.resolve(), False)
        payload.pop("Umask")
        self.agents.mkdir(parents=True)
        self.path_for(cmd).write_bytes(plistlib.dumps(payload))
        code, _, err = self.invoke("install", "--command", cmd, "--replace")
        self.assertEqual(code, 1)
        self.assertIn("不符合本 Skill 的固定模板", err)
        self.assertNotIn(label, self.fake.loaded)

    def test_out_of_scope_mac_and_root_validation(self):
        with patch.object(la.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "macOS"):
                ORIGINAL_REQUIRE_MACOS()
        with patch.object(la.sys, "platform", "darwin"), patch.object(la.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "sudo 或 root"):
                ORIGINAL_REQUIRE_MACOS()


    def test_help_output_is_chinese(self):
        code, out, err = self.invoke("--help")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("用法：", out)
        self.assertIn("选项：", out)
        self.assertNotIn("usage:", out)
        self.assertNotIn("options:", out)

    def test_new_install_requires_description_without_side_effects(self):
        code, _, err = self.invoke("install", "--command", "echo needs description", auto_description=False)
        self.assertEqual(code, 1)
        self.assertIn("必须提供 --description", err)
        self.assertFalse(self.agents.exists())
        self.assertEqual(self.fake.calls, [])

    def test_metadata_records_description_source_file_and_hashes(self):
        script = self.root / "daily report.sh"
        script.write_text("#!/bin/sh\necho report\n")
        script.chmod(0o700)
        code, out, err = self.invoke("install", "--script", str(script), "--description", "每日生成报告")
        self.assertEqual((code, err), (0, ""))
        label = la.make_label(__import__("shlex").quote(str(script.resolve())), None, "login")
        metadata_file = la.metadata_path(label)
        metadata = json.loads(metadata_file.read_text())
        plist_file = self.agents / f"{label}.plist"
        self.assertEqual(metadata["description"], "每日生成报告")
        self.assertEqual(metadata["source_file"], str(script.resolve()))
        self.assertEqual(metadata["plist_path"], str(plist_file))
        self.assertEqual(metadata["disk_sha256"], hashlib.sha256(plist_file.read_bytes()).hexdigest())
        self.assertIn("源脚本：", out)
        self.assertEqual(metadata_file.stat().st_mode & 0o777, 0o600)

    def test_describe_updates_only_metadata(self):
        command = "echo description"
        self.assertEqual(self.invoke("install", "--command", command, "--description", "旧描述")[0], 0)
        label = la.make_label(command, None, "login")
        plist_before = self.path_for(command).read_bytes()
        self.fake.calls.clear()
        code, out, err = self.invoke("describe", "--label", label, "--description", "更新后的用途说明")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.path_for(command).read_bytes(), plist_before)
        self.assertEqual(self.fake.calls, [])
        self.assertIn("更新后的用途说明", out)
        metadata = json.loads(la.metadata_path(label).read_text())
        self.assertEqual(metadata["description"], "更新后的用途说明")

    def test_login_disk_update_reports_possible_registration_drift(self):
        name = "登录任务漂移"
        old_command, new_command = "echo old", "echo new"
        self.assertEqual(self.invoke("install", "--command", old_command, "--name", name, "--start-now")[0], 0)
        label = la.make_label(old_command, name, "login")
        live_before = self.fake.loaded[label]
        code, _, err = self.invoke("install", "--command", new_command, "--name", name, "--replace")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.fake.loaded[label], live_before)
        code, out, err = self.invoke("status", "--label", label)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        self.assertIn("疑似漂移", out)
        self.assertIn("当前会话可能仍使用旧配置", out)
        list_code, list_out, list_err = self.invoke("list")
        self.assertEqual((list_code, list_err), (0, ""))
        self.assertIn("疑似漂移", list_out)

    def test_login_rollback_warns_runatload_can_execute_old_command_again(self):
        name = "回滚副作用"
        self.assertEqual(self.invoke("install", "--command", "echo old", "--name", name, "--start-now")[0], 0)
        self.fake.fail_next = "bootstrap"
        code, _, err = self.invoke("install", "--command", "echo new", "--name", name, "--replace", "--start-now")
        self.assertEqual(code, 1)
        self.assertIn("RunAtLoad=true", err)
        self.assertIn("可能再次执行旧命令", err)

    def test_status_does_not_query_unknown_loaded_label_without_managed_plist(self):
        label = la.make_label("echo unknown", None, "login")
        self.fake.loaded[label] = b"not owned by this skill"
        code, _, err = self.invoke("status", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("不查询同名未知服务", err)
        self.assertEqual(self.fake.calls, [])

    def test_list_only_includes_fixed_template_managed_plists(self):
        command = "echo managed"
        self.assertEqual(self.invoke("install", "--command", command, "--description", "托管任务说明")[0], 0)
        self.agents.mkdir(parents=True, exist_ok=True)
        foreign = self.agents / "com.example.foreign.plist"
        foreign.write_bytes(plistlib.dumps({"Label": "com.example.foreign", "ProgramArguments": ["/bin/echo"]}))
        code, out, err = self.invoke("list")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("托管任务说明", out)
        self.assertNotIn("com.example.foreign", out)

    def test_named_login_identity_is_stable_and_unknown_existing_plist_is_not_adopted(self):
        command, name = "echo unmanaged named", "Unmanaged Named"
        login_label = la.make_label(command, name, "login")
        calendar_label = la.make_label(command, name, "calendar")
        self.assertNotEqual(login_label, calendar_label)
        # 登录任务使用稳定的 name:<name> 身份；未知 plist 占用该路径时拒绝覆盖，
        # 不会另建一个看似成功但导致重复运行的任务。
        payload = la.build_plist(login_label, command, self.root.resolve(), False)
        self.agents.mkdir(parents=True)
        existing_path = self.agents / f"{login_label}.plist"
        existing_path.write_bytes(la.to_plist_bytes(payload))
        code, _, err = self.invoke("install", "--command", command, "--name", name)
        self.assertEqual(code, 1)
        self.assertIn("没有本 Skill 的管理记录", err)
        self.assertTrue(existing_path.exists())
        self.assertFalse(la.metadata_path(login_label).exists())
        self.assertNotIn("bootout", [c[0] for c in self.fake.calls])
        self.assertNotIn("bootstrap", [c[0] for c in self.fake.calls])

    def test_private_metadata_parent_and_leaf_are_both_0700(self):
        code, _, err = self.invoke("install", "--command", "echo permissions")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.metadata.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.metadata.stat().st_mode & 0o777, 0o700)

    def test_python_version_guard_is_clear_and_runs_before_argparse(self):
        import io
        import sys
        from unittest.mock import patch
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(la.sys, "version_info", (3, 9, 18)), \
                patch.object(sys, "argv", [str(MODULE_FILE), "--help"]), \
                patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            result = la.main()
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Python 3.10 或更高版本", stderr.getvalue())

    def test_argument_errors_keep_specific_chinese_reason(self):
        code, _, err = self.invoke("install", "--bogus-flag")
        self.assertEqual(code, 2)
        self.assertIn("未知参数：--bogus-flag", err)
        code, _, err = self.invoke("install", "--command", "echo x", "--month", "abc")
        self.assertEqual(code, 2)
        self.assertIn("参数 --month 必须是整数", err)
        code, _, err = self.invoke("install", "--command", "echo x", "--month")
        self.assertEqual(code, 2)
        self.assertIn("参数 --month 缺少值", err)

    def test_fresh_login_output_and_fixed_trigger_are_truthful(self):
        code, out, err = self.invoke("install", "--command", "echo new")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("RunAtLoad=true", out)
        self.assertIn("KeepAlive=false", out)
        self.assertNotIn("\u5f53\u524d\u5df2\u52a0\u8f7d", out)
        self.assertNotIn("\u6ce8\u518c\u6210\u529f", out)
        label = la.make_label("echo new", None, "login")
        for action in ("status", "list"):
            argv = (action, "--label", label) if action == "status" else (action,)
            rc, info, error = self.invoke(*argv)
            self.assertEqual((rc, error), (0, ""))
            self.assertIn("RunAtLoad=true", info)
            self.assertIn("KeepAlive=false", info)

    def test_login_update_warns_about_stale_registration_only_when_changed(self):
        cmd1, cmd2, name = "echo original", "echo changed", "named-login"
        self.assertEqual(self.invoke("install", "--command", cmd1, "--name", name, "--start-now")[0], 0)
        rc, again, err = self.invoke("install", "--command", cmd1, "--name", name)
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("\u5f53\u524d\u5df2\u52a0\u8f7d", again)
        rc, changed, err = self.invoke("install", "--command", cmd2, "--name", name, "--replace")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("\u5f53\u524d\u5df2\u52a0\u8f7d", changed)

    def test_adding_name_to_an_unnamed_login_warns_about_duplicate(self):
        cmd = "/usr/bin/true"
        code, out, err = self.invoke("install", "--command", cmd)
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("\u5df2\u6709\u540c\u6a21\u5f0f", out)
        old_label = la.make_label(cmd, None, "login")
        code, out, err = self.invoke("install", "--command", cmd, "--name", "my-task")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("\u5df2\u6709\u540c\u6a21\u5f0f", out)
        self.assertIn(old_label, out)
        self.assertIn("\u672c\u6b21\u5c06\u65b0\u589e", out)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)
        self.assertEqual(self.fake.calls, [])

    def test_duplicate_warning_on_dry_run_does_not_write_extra_task(self):
        cmd = "echo same task"
        self.assertEqual(self.invoke("install", "--command", cmd)[0], 0)
        rc, preview, err = self.invoke("install", "--command", cmd, "--name", "same-task", "--dry-run")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("\u5df2\u6709\u540c\u6a21\u5f0f", preview)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 1)

    def test_different_modes_do_not_warn_as_same_job(self):
        cmd = "echo dual-mode"
        self.assertEqual(self.invoke("install", "--command", cmd, "--name", "login-job")[0], 0)
        rc, out, err = self.invoke("install", "--command", cmd, "--name", "calendar-job", "--time", "11:00")
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("\u5df2\u6709\u540c\u6a21\u5f0f", out)

    def test_chinese_name_still_falls_back_to_job_slug(self):
        name = "\u6bcf\u65e5\u5907\u4efd"
        for mode in ("login", "calendar", "custom"):
            label = la.make_label("echo named", name, mode)
            self.assertIn(".job-", label)
            la.validate_label(label)
        self.assertIn(".daily-backup-", la.make_label("echo named", "daily-backup", "login"))

    def test_new_prefix_is_shared_by_all_fixed_labels_and_lock(self):
        self.assertEqual(la.PREFIX, "local.my.agents.")
        self.assertEqual(la.LOCK_FILENAME, ".local.my.agents.lock")
        self.assertTrue(la.make_label("echo x", None).startswith(la.PREFIX))
        self.assertTrue(la.make_label("echo x", "x", "calendar").startswith(la.PREFIX))
