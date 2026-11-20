"""恢复矩阵：区分有效/损坏/缺失记录，以及有效/缺失/无法解析的 plist。"""
from __future__ import annotations

import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_launch_agent as fixtures

la = fixtures.la


class RecoveryMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.agents = self.home / "Library" / "LaunchAgents"
        self.logs = self.home / "Library" / "Logs" / "LaunchAgents"
        self.meta = self.home / "Library" / "Application Support" / "macOSLaunchAgentSkill" / "Agents"
        self.fake = fixtures.FakeLaunchctl()
        for target, value in (
            ("LAUNCH_AGENTS_DIRECTORY", self.agents),
            ("LOG_DIRECTORY", self.logs),
            ("METADATA_DIRECTORY", self.meta),
            ("DEFAULT_WORKING_DIRECTORY", self.home),
            ("require_macos", lambda: None),
            ("launchctl", self.fake),
        ):
            patched = patch.object(la, target, value)
            patched.start()
            self.addCleanup(patched.stop)

    def invoke(self, *argv):
        if argv and argv[0] == "install" and "--description" not in argv:
            argv = (*argv, "--description", "状态恢复矩阵测试")
        output, error = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(fixtures.MODULE_FILE), *argv]), \
             patch.object(sys, "stdout", output), patch.object(sys, "stderr", error):
            result = la.main()
        return result, output.getvalue(), error.getvalue()

    def install_job(self, name="matrix", mode="login", start=False):
        args = ["install", "--name", name, "--command", "echo matrix"]
        if mode == "calendar":
            args += ["--mode", "calendar", "--time", "13:00", "--defer"]
        if start:
            args += ["--start-now"]
        rc, _, err = self.invoke(*args)
        self.assertEqual((rc, err), (0, ""), err)
        label = la.make_label("echo matrix", name, mode)
        return label, self.agents / (label + ".plist"), la.metadata_path(label)

    def damage_json(self, meta):
        meta.write_text('{"description":', encoding="utf-8")
        self.assertEqual(meta.stat().st_mode & 0o777, 0o600)

    def test_valid_orphan_status_list_uninstall_register_describe_and_install_have_exit(self):
        label, plist, meta = self.install_job(mode="calendar")
        plist.unlink()
        for args in (("uninstall", "--label", label),
                     ("register", "--label", label, "--allow-execution"),
                     ("describe", "--label", label, "--description", "修订"),
                     ("install", "--name", "matrix", "--command", "echo matrix", "--mode", "calendar", "--time", "13:00", "--defer"),
                     ("install", "--name", "matrix", "--command", "echo matrix", "--mode", "calendar", "--time", "13:00", "--defer", "--dry-run")):
            with self.subTest(args=args):
                rc, out, err = self.invoke(*args)
                self.assertNotEqual(rc, 0)
                self.assertIn("--action remove-orphan --confirm-stop", err)
        for args in (("status", "--label", label), ("list",)):
            with self.subTest(args=args):
                rc, out, err = self.invoke(*args)
                self.assertEqual(rc, 0)
                self.assertIn("remove-orphan", out)
        self.assertTrue(meta.exists())
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")[0], 0)
        self.assertFalse(meta.exists())

    def test_whole_launchagents_directory_missing_has_orphan_hint_and_is_recoverable(self):
        label, plist, meta = self.install_job()
        shutil.rmtree(self.agents)
        rc, _, err = self.invoke("uninstall", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("remove-orphan", err)
        self.assertEqual(self.invoke("status", "--label", label)[0], 0)
        self.assertIn("remove-orphan", self.invoke("status", "--label", label)[1])
        rc, out, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual((rc, err), (0, ""), err)
        self.assertFalse(meta.exists())

    def test_corrupt_json_valid_plist_rebuild_is_advertised_everywhere(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        actions = (("status", "--label", label),
                   ("uninstall", "--label", label),
                   ("describe", "--label", label, "--description", "updated"),
                   ("register", "--label", label, "--allow-execution"),
                   ("repair", "--label", label, "--action", "accept-plist", "--trust-current-plist"),
                   ("install", "--name", "matrix", "--command", "echo matrix", "--replace"))
        for args in actions:
            with self.subTest(args=args):
                rc, out, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn("--action rebuild-record", err)
                self.assertIn("--trust-current-plist", err)
                self.assertIn("--description", err)
                self.assertEqual(err.count(str(meta)), 1)
        rc, _, err = self.invoke("list")
        self.assertEqual(rc, 0)
        self.assertIn("--action rebuild-record", err)
        self.assertNotIn("--action remove-orphan", err)
        rc, out, err = self.invoke("repair", "--label", label, "--action", "rebuild-record",
                                    "--trust-current-plist", "--description", "可信重建记录")
        self.assertEqual((rc, err), (0, ""), err)
        self.assertEqual(la.inspect_metadata(label)[1]["description"], "可信重建记录")

    def test_corrupt_json_missing_plist_repair_no_longer_dead_ends(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        plist.unlink()
        for args in (("status", "--label", label),
                     ("install", "--name", "matrix", "--command", "echo matrix", "--dry-run"),
                     ("uninstall", "--label", label)):
            with self.subTest(args=args):
                rc, _, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn("--action remove-orphan --confirm-stop", err)
                self.assertNotIn("--action rebuild-record", err)
        rc, _, err = self.invoke("list")
        self.assertEqual(rc, 0)
        self.assertIn("--action remove-orphan --confirm-stop", err)
        self.assertNotIn("--action rebuild-record", err)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "rebuild-record", "--trust-current-plist")
        self.assertEqual(rc, 1)
        self.assertIn("remove-orphan", err)
        rc, out, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual((rc, err), (0, ""), err)
        self.assertIn("JSON 已损坏", out)
        self.assertFalse(meta.exists())

    def test_corrupt_json_loaded_service_needs_manual_identity_check(self):
        label, plist, meta = self.install_job(start=True)
        self.damage_json(meta)
        plist.unlink()
        self.fake.calls.clear()
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("无法证明当前加载服务的来源", err)
        self.assertTrue(meta.exists())
        self.assertNotIn("bootout", [x[0] for x in self.fake.calls])
        self.fake.loaded.pop(label)
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")[0], 0)

    def test_corrupt_json_missing_plist_rejects_insecure_record(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        plist.unlink()
        meta.chmod(0o644)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("不安全", err)
        self.assertTrue(meta.exists())

    def test_corrupt_json_missing_plist_rejects_symlink_record(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        plist.unlink()
        dest = self.home / "outside.json"
        dest.write_text("important", encoding="utf-8")
        meta.unlink()
        meta.symlink_to(dest)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("不安全", err)
        self.assertEqual(dest.read_text(encoding="utf-8"), "important")

    def test_orphan_probe_unknown_does_not_delete(self):
        label, plist, meta = self.install_job()
        plist.unlink()
        self.fake.fail_next = "print"
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("无法确认", err)
        self.assertTrue(meta.exists())

    def test_record_modified_during_orphan_cleanup_is_retained(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        plist.unlink()
        original = self.fake.__call__
        def write_during_probe(*args, check=True):
            result = original(*args, check=check)
            if args[0] == "print" and args[1].count("/") == 2:
                meta.write_text('{"new": "other"}', encoding="utf-8")
            return result
        with patch.object(la, "launchctl", write_during_probe):
            rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("外部修改", err)
        self.assertTrue(meta.exists())

    def test_plist_reappears_during_orphan_cleanup_is_retained(self):
        label, plist, meta = self.install_job()
        plist.unlink()
        original = self.fake.__call__
        def resurrect(*args, check=True):
            result = original(*args, check=check)
            if args[0] == "print" and args[1].count("/") == 2:
                plist.write_bytes(b"external")
            return result
        with patch.object(la, "launchctl", resurrect):
            rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("重新出现", err)
        self.assertTrue(meta.exists())
        self.assertEqual(plist.read_bytes(), b"external")

    def test_unparseable_plist_valid_record_explains_manual_steps(self):
        label, plist, meta = self.install_job()
        plist.write_bytes(b"not a plist")
        for args in (("uninstall", "--label", label),
                     ("repair", "--label", label, "--action", "accept-plist", "--trust-current-plist"),
                     ("repair", "--label", label, "--action", "drop-unrecognized", "--confirm-delete"),
                     ("install", "--name", "matrix", "--command", "echo matrix", "--dry-run")):
            with self.subTest(args=args):
                rc, _, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn("remove-orphan", err)
                self.assertIn("手动删除", err)
        for args in (("status", "--label", label), ("list",)):
            rc, out, err = self.invoke(*args)
            self.assertEqual(rc, 0)
            self.assertIn("手动删除", out)
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("plist 仍存在", err)
        plist.unlink()  # 已人工核实并清理当前托管路径；此处不模拟真实 Mac 停服。
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")[0], 0)

    def test_unmanaged_plist_is_visible_without_claiming_ownership(self):
        label = la.make_label("echo not-owned", "orphan-name")
        self.agents.mkdir(parents=True)
        path = self.agents / (label + ".plist")
        path.write_bytes(la.to_plist_bytes(la.build_plist(label, "echo not-owned", self.home, False)))
        self.fake.loaded[label] = path.read_bytes()
        for args in (("status", "--label", label),
                     ("install", "--name", "orphan-name", "--command", "echo not-owned", "--dry-run"),
                     ("install", "--name", "orphan-name", "--command", "echo not-owned"),
                     ("uninstall", "--label", label)):
            with self.subTest(args=args):
                rc, _, err = self.invoke(*args)
                self.assertEqual(rc, 1)
                self.assertIn("不会接管", err)
                self.assertIn("人工", err)
        self.fake.calls.clear()
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("未管理的托管命名样式 plist", out)
        self.assertIn(str(path), out)
        self.assertIn("0 个有效管理记录、1 个未管理文件", out)
        self.assertEqual(self.fake.calls, [])
        self.assertTrue(path.exists())
        self.assertIn(label, self.fake.loaded)

    def test_unmanaged_symlink_is_only_reported_and_never_followed(self):
        label = la.make_label("echo not-owned", "link-name")
        self.agents.mkdir(parents=True)
        target = self.home / "outside.dat"
        target.write_text("keep", encoding="utf-8")
        (self.agents / (label + ".plist")).symlink_to(target)
        rc, out, err = self.invoke("list")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("未管理", out)
        self.assertIn(label, out)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_mixed_managed_orphan_corrupt_and_unmanaged_listing(self):
        label, plist, meta = self.install_job("healthy")
        _, orphan_plist, orphan_meta = self.install_job("missing")
        orphan_plist.unlink()
        _, broken_plist, broken_meta = self.install_job("badjson")
        self.damage_json(broken_meta)
        unknown = la.make_label("echo foreign", "unknown")
        (self.agents / (unknown + ".plist")).write_bytes(
            la.to_plist_bytes(la.build_plist(unknown, "echo foreign", self.home, False)))
        rc, out, err = self.invoke("list")
        self.assertEqual(rc, 0)
        self.assertIn("共列出 2 个有效管理记录、1 个未管理文件；1 个异常管理记录", out)
        self.assertIn("--action remove-orphan", out)
        self.assertIn("--action rebuild-record", err)
        self.assertIn(unknown, out)

    def test_record_extra_fields_does_not_suggest_automatic_migration(self):
        label, plist, meta = self.install_job()
        obj = la.inspect_metadata(label)[1]
        obj["some_other_format"] = {"a": 1}
        meta.write_text(json.dumps(obj), encoding="utf-8")
        rc, _, err = self.invoke("status", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("未定义字段", err)
        self.assertIn("拒绝自动重建或迁移", err)
        self.assertNotIn("--action rebuild-record", err)
        self.assertTrue(meta.exists())

    def test_corrupt_record_and_unparseable_plist_have_manual_cleanup_then_orphan_exit(self):
        label, plist, meta = self.install_job()
        self.damage_json(meta)
        plist.write_bytes(b"invalid bytes")
        rc, _, err = self.invoke("status", "--label", label)
        self.assertEqual(rc, 1)
        self.assertIn("手动删除", err)
        self.assertIn("--action remove-orphan", err)
        self.assertNotIn("--action rebuild-record", err)
        # 文件在场时拒绝清理记录；由人核对并删除损坏文件后才继续。
        rc, _, err = self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")
        self.assertEqual(rc, 1)
        self.assertIn("plist 仍存在", err)
        plist.unlink()
        self.assertEqual(self.invoke("repair", "--label", label, "--action", "remove-orphan", "--confirm-stop")[0], 0)

    def test_list_refuses_symlink_metadata_directory_without_traversal(self):
        self.meta.parent.mkdir(parents=True)
        foreign = self.home / "foreign-directory"
        foreign.mkdir()
        (foreign / "secret.json").write_text("external", encoding="utf-8")
        self.meta.symlink_to(foreign, target_is_directory=True)
        rc, _, err = self.invoke("list")
        self.assertEqual(rc, 1)
        self.assertIn("拒绝枚举符号链接管理目录", err)
        self.assertEqual((foreign / "secret.json").read_text(encoding="utf-8"), "external")


if __name__ == "__main__":
    unittest.main()
