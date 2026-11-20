"""注册场景指引与显式清理无法识别的托管文件：故障注入回归。"""
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


class DiscardAndGuidanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.agents = self.root / "Library" / "LaunchAgents"
        self.logs = self.root / "Library" / "Logs" / "LaunchAgents"
        self.records = self.root / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = DisabledLaunchctl()
        for key, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.logs),
            ("METADATA_DIRECTORY", self.records),
            ("DEFAULT_WORKING_DIRECTORY", self.root),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            p = patch.object(la, key, value)
            p.start()
            self.addCleanup(p.stop)

    def invoke(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", out), \
             patch.object(sys, "stderr", err):
            result = la.main()
        return result, out.getvalue(), err.getvalue()

    def fixed(self, mode="login", name="disposable", start=False):
        args = ["install", "--name", name, "--description", "测试显式清理", "--command", "/bin/true"]
        if mode == "calendar":
            args += ["--mode", "calendar", "--time", "13:00"]
        elif start:
            args.append("--start-now")
        return self.invoke(*args)

    def label(self, mode="login", name="disposable"):
        return la.make_label("/bin/true", name, mode)

    def corrupt_fixed(self, *, loaded=False):
        self.assertEqual(self.fixed(start=loaded)[0], 0)
        label = self.label()
        path = self.agents / (label + ".plist")
        obj = plistlib.loads(path.read_bytes())
        obj["ProcessType"] = "Background"
        path.write_bytes(plistlib.dumps(obj, sort_keys=False))
        return label, path, self.records / (label + ".json")

    def test_register_login_guidance_is_about_register_not_only_install(self):
        self.assertEqual(self.fixed(start=True)[0], 0)
        label = self.label()
        self.fake.overrides[label] = True
        rc, _, error = self.invoke("register", "--label", label, "--allow-execution")
        self.assertEqual(rc, 1, error)
        self.assertIn("当前命令 register 必然尝试注册", error)
        self.assertIn("install（不加 --start-now）", error)
        self.assertNotIn("register --start-now", error)
        self.assertNotIn("register --defer", error)
        self.assertIn("本次未写入配置", error)

    def test_uninstall_calendar_guidance_explains_defer(self):
        self.assertEqual(self.invoke("install", "--mode", "calendar", "--time", "13:00",
                                     "--defer", "--name", "day-sticky", "--command", "/bin/true",
                                     "--description", "测试")[0], 0)
        label = self.label("calendar", "day-sticky")
        self.fake.overrides[label] = True
        rc, text, error = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, error), (0, ""))
        self.assertIn("--defer", text)
        self.assertIn("默认安装会尝试注册", text)
        self.assertNotIn("--start-now", text)
        self.assertFalse((self.agents / (label + ".plist")).exists())
        self.assertTrue(self.fake.overrides[label])
        self.assertEqual(self.invoke("install", "--mode", "calendar", "--time", "13:00",
                                     "--name", "day-sticky", "--command", "/bin/true",
                                     "--description", "测试", "--defer")[0], 0)

    def test_uninstall_login_guidance_uses_start_now_not_defer(self):
        self.assertEqual(self.fixed()[0], 0)
        label = self.label()
        self.fake.overrides[label] = True
        rc, out, err = self.invoke("uninstall", "--label", label)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("--start-now", out)
        self.assertNotIn("--defer", out)

    def test_corrupt_fixed_status_shows_exact_changed_keys_and_normal_uninstall_explains_outlet(self):
        label, path, record = self.corrupt_fixed()
        rc, status, err = self.invoke("status", "--label", label)
        self.assertEqual(rc, 0, (status, err))
        self.assertIn("多出键：ProcessType", status)
        code, _, error = self.invoke("uninstall", "--label", label)
        self.assertEqual(code, 1)
        self.assertIn("ProcessType", error)
        self.assertIn("drop-unrecognized", error)
        self.assertTrue(path.exists())
        self.assertTrue(record.exists())

    def test_explicit_drop_unrecognized_stops_loaded_and_clears_only_managed_files(self):
        label, path, record = self.corrupt_fixed(loaded=True)
        self.assertIn(label, self.fake.loaded)
        self.assertTrue((self.logs / (label + ".stdout.log")).exists())
        code, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized")
        self.assertEqual(code, 1)
        self.assertIn("--confirm-delete", error)
        code, _, error = self.invoke("repair", "--label", label, "--action", "accept-plist",
                                     "--trust-current-plist")
        self.assertEqual(code, 1)
        self.assertIn("ProcessType", error)
        self.fake.calls.clear()
        code, out, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                       "--confirm-delete")
        self.assertEqual((code, error), (0, ""), error)
        self.assertIn("已明确清理", out)
        self.assertIn("未重新 bootstrap", out)
        self.assertFalse(path.exists())
        self.assertFalse(record.exists())
        self.assertNotIn(label, self.fake.loaded)
        self.assertTrue((self.logs / (label + ".stdout.log")).exists())
        self.assertIn("bootout", [args[0] for args in self.fake.calls])
        self.assertNotIn("bootstrap", [args[0] for args in self.fake.calls])

    def test_explicit_drop_rejects_unknown_record_and_foreign_or_different_label(self):
        label, path, record = self.corrupt_fixed()
        record.unlink()
        code, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                     "--confirm-delete")
        self.assertEqual(code, 1)
        self.assertIn("没有有效管理记录", error)
        self.assertTrue(path.exists())
        self.assertEqual(self.fixed()[0], 1)

        # 恢复真实记录后变更内部 Label：仍不能破坏另一服务。
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                     "--trust-current-plist", "--description", "rebuild")[0], 1)
        # 独立的有效任务模拟。
        label2 = self.label(name="different")
        self.assertEqual(self.fixed(name="different")[0], 0)
        path2 = self.agents / (label2 + ".plist")
        obj = plistlib.loads(path2.read_bytes())
        obj["Label"] = "example.foreign"
        path2.write_bytes(plistlib.dumps(obj))
        code, _, error = self.invoke("repair", "--label", label2, "--action", "drop-unrecognized",
                                     "--confirm-delete")
        self.assertEqual(code, 1)
        self.assertIn("内部 Label", error)
        self.assertTrue(path2.exists())
        self.assertTrue((self.records / (label2 + ".json")).exists())

    def test_explicit_drop_does_not_apply_to_healthy_task(self):
        self.assertEqual(self.fixed()[0], 0)
        label = self.label()
        code, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                     "--confirm-delete")
        self.assertEqual(code, 1)
        self.assertIn("请使用 uninstall", error)
        self.assertTrue((self.agents / (label + ".plist")).exists())

    def test_explicit_drop_rejects_unparseable_file(self):
        self.assertEqual(self.fixed()[0], 0)
        label = self.label()
        target = self.agents / (label + ".plist")
        target.write_bytes(b"not a valid plist")
        code, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                     "--confirm-delete")
        self.assertEqual(code, 1)
        self.assertIn("无法解析", error)
        self.assertTrue(target.exists())

    def test_explicit_drop_rejects_unknown_service_state_without_file_changes(self):
        label, path, record = self.corrupt_fixed()
        self.fake.fail_next = "print"
        rc, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                    "--confirm-delete")
        self.assertEqual(rc, 1)
        self.assertIn("无法确认服务状态", error)
        self.assertTrue(path.exists())
        self.assertTrue(record.exists())

    def test_explicit_drop_file_delete_failure_restores_metadata_without_reloading_edited_plist(self):
        label, path, record = self.corrupt_fixed(loaded=True)
        real_unlink = Path.unlink
        def fail_path_unlink(obj, *a, **kw):
            if obj == path:
                raise OSError("模拟 plist 删除失败")
            return real_unlink(obj, *a, **kw)
        with patch.object(Path, "unlink", fail_path_unlink):
            rc, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                        "--confirm-delete")
        self.assertEqual(rc, 1)
        self.assertIn("清理异常任务时删除文件失败", error)
        self.assertIn("不会自动重载", error)
        self.assertTrue(path.exists())
        self.assertTrue(record.exists())
        self.assertNotIn(label, self.fake.loaded)

    def test_explicit_drop_rejects_symlink_even_with_record(self):
        label, path, record = self.corrupt_fixed()
        real = self.root / "saved.plist"
        path.rename(real)
        path.symlink_to(real)
        code, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                     "--confirm-delete")
        self.assertEqual(code, 1)
        self.assertIn("不安全", error)
        self.assertTrue(path.is_symlink())
        self.assertTrue(record.exists())
    def test_drop_with_bootout_error_after_actual_stop_safely_finishes(self):
        label, path, record = self.corrupt_fixed(loaded=True)
        original_launchctl = self.fake

        def partial_bootout(*args, **kwargs):
            if args[0] == "bootout":
                original_launchctl(*args, **kwargs)
                raise RuntimeError("模拟 bootout 停止后报错")
            return original_launchctl(*args, **kwargs)

        with patch.object(la, "launchctl", partial_bootout):
            rc, out, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                         "--confirm-delete")
        self.assertEqual((rc, error), (0, ""), error)
        self.assertIn("停止命令返回错误", out)
        self.assertFalse(path.exists())
        self.assertFalse(record.exists())
        self.assertNotIn(label, original_launchctl.loaded)

    def test_drop_with_bootout_failure_while_still_loaded_preserves_both_files(self):
        label, path, record = self.corrupt_fixed(loaded=True)
        original_launchctl = self.fake

        def fail_before_stop(*args, **kwargs):
            if args[0] == "bootout":
                raise RuntimeError("模拟停止请求完全失败")
            return original_launchctl(*args, **kwargs)

        with patch.object(la, "launchctl", fail_before_stop):
            rc, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                        "--confirm-delete")
        self.assertEqual(rc, 1)
        self.assertIn("未确认任务已停止", error)
        self.assertTrue(path.exists())
        self.assertTrue(record.exists())
        self.assertIn(label, original_launchctl.loaded)

    def test_drop_rechecks_plist_after_stop_before_deletion(self):
        label, path, record = self.corrupt_fixed(loaded=True)
        original_launchctl = self.fake

        def mutate_after_stop(*args, **kwargs):
            result = original_launchctl(*args, **kwargs)
            if args[0] == "bootout":
                obj = plistlib.loads(path.read_bytes())
                obj["ProcessType"] = "Interactive"
                path.write_bytes(plistlib.dumps(obj, sort_keys=False))
            return result

        with patch.object(la, "launchctl", mutate_after_stop):
            rc, _, error = self.invoke("repair", "--label", label, "--action", "drop-unrecognized",
                                        "--confirm-delete")
        self.assertEqual(rc, 1)
        self.assertIn("清理前 plist 又被修改", error)
        self.assertIn("旧服务已停止", error)
        self.assertTrue(path.exists())
        self.assertTrue(record.exists())
        self.assertNotIn(label, original_launchctl.loaded)

    def test_register_calendar_guidance_is_not_install_arguments_on_register(self):
        self.assertEqual(self.invoke("install", "--name", "calendar-registered", "--mode", "calendar",
                                     "--time", "13:00", "--command", "/bin/true",
                                     "--description", "cal", "--defer")[0], 0)
        label = self.label("calendar", "calendar-registered")
        self.fake.overrides[label] = True
        rc, _, error = self.invoke("register", "--label", label, "--allow-execution")
        self.assertEqual(rc, 1)
        self.assertIn("当前命令 register 必然尝试注册", error)
        self.assertIn("--replace --defer", error)
        self.assertIn("不适用于 register", error)


if __name__ == "__main__":
    unittest.main()
