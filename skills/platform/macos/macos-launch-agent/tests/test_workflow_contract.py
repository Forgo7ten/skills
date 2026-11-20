"""Skill 路由说明与 CLI 的静态契约检查，仅依赖标准库。"""
from __future__ import annotations
import ast
import argparse
import importlib.util
import json
import re
import unittest
from pathlib import Path

import test_launch_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_description_and_markdown_references(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        desc = json.loads(re.search(r"^description: (.+)$", text, re.M).group(1))
        self.assertLessEqual(len(desc), 60)
        self.assertIn("XML", desc)
        self.assertIn("统一", desc)
        self.assertLess(len(text.splitlines()), 500)
        for ref in re.findall(r"\]\((references/[^)]+)\)", text):
            self.assertTrue((ROOT / ref).is_file(), ref)

    def test_documented_entrypoints_and_confirmation_flags_exist(self):
        parser = fixtures.la.make_parser()
        examples = [
            ["install-plist", "--input", "/tmp/source.plist", "--name", "demo", "--description", "用途", "--dry-run"],
            ["install-plist", "--input", "/tmp/source.plist", "--name", "demo", "--activate", "--replace"],
            ["register", "--label", "demo", "--allow-execution"],
            ["repair", "--label", "demo", "--action", "reset-snapshot", "--trust-current-plist"],
        ]
        for argv in examples:
            with self.subTest(argv=argv):
                parsed = parser.parse_args(argv)
                self.assertTrue(callable(parsed.handler))
        custom = parser.parse_args(examples[0])
        self.assertFalse(custom.activate)
        self.assertFalse(parser.parse_args(["register", "--label", "demo"]).allow_execution)

    def test_no_hidden_compatibility_entrypoints(self):
        # 每个入口只接受本版本正式对外公布的操作与参数。
        manager = fixtures.la.make_parser()
        command_options = [action for action in manager._actions if isinstance(action, argparse._SubParsersAction)]
        self.assertEqual(len(command_options), 1)
        self.assertEqual(set(command_options[0].choices), {
            'install', 'install-plist', 'register', 'list', 'status',
            'describe', 'run', 'uninstall', 'repair',
        })
        converter_file = ROOT / 'scripts' / 'xml_to_plist.py'
        spec = importlib.util.spec_from_file_location('xml_to_plist_for_contract', converter_file)
        converter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(converter)
        self.assertEqual(set(converter.parser_factory()._option_string_actions), {
            '-h', '--help', '--xml', '--input', '--output', '--format', '--overwrite',
        })
        self.assertIsInstance(ast.parse(converter_file.read_text(encoding='utf-8')), ast.Module)

    def test_metadata_contains_only_defined_fields(self):
        src = fixtures.la.make_metadata(
            'local.my.agents.example-abcdef12',
            fixtures.la.LAUNCH_AGENTS_DIRECTORY / 'local.my.agents.example-abcdef12.plist',
            b'dummy', '测试用途', 'login', None, None, False,
        )
        self.assertEqual(set(src), fixtures.la.METADATA_REQUIRED_FIELDS)
        self.assertIn('registration_snapshot_unknown_reason', src)

    def test_installation_entrypoints_use_one_commit_function(self):
        tree = ast.parse(fixtures.MODULE_FILE.read_text(encoding="utf-8"))
        functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("install", "install_plist", "register_task"):
            calls = [n.func.id for n in ast.walk(functions[name])
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
            self.assertIn("commit_install", calls, name)
            self.assertNotIn("launchctl", calls, name)
            self.assertNotIn("atomic_write", calls, name)


    def test_all_packaged_sources_use_only_current_namespace(self):
        for item in ROOT.rglob("*"):
            if item.is_file() and item.suffix in {".py", ".md", ".yaml"}:
                text = item.read_text(encoding="utf-8")
                # Build the deprecated dotted name so source grep stays clean.
                previous = ".".join(("com", "local", "autostart"))
                self.assertNotIn(previous, text.lower(), str(item))
                escaped = r"\.".join(("com", "local", "autostart"))
                self.assertNotIn(escaped, text.lower(), str(item))
        self.assertEqual(fixtures.la.PREFIX, "local.my.agents.")
        self.assertEqual(fixtures.la.LOCK_FILENAME, ".local.my.agents.lock")

    def test_skill_examples_and_naming_guidance_are_consistent(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("daily-backup", skill)
        self.assertIn("job-<hash>", skill)
        self.assertIn("\u4e2d\u6587\u540d", skill)
        self.assertIn("\u672a\u7ed9\u540d\u79f0", skill)
        self.assertIn("--name 'login-service'", skill)
        self.assertIn("--name 'twice-daily-maintenance'", skill)
        self.assertIn("--name 'login-service'", readme)
        self.assertIn("--name 'twice-daily-maintenance'", readme)
        self.assertNotIn("--name 'login-service' --name", skill)
        self.assertNotIn("--name 'login-service' --name", readme)

if __name__ == "__main__":
    unittest.main()
