from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_profile_manager import claude, claude_profiles, manager


class ClaudeManagerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"HOME": str(self.home)}, clear=True))
        self.output, self.error = io.StringIO(), io.StringIO()
        stack.enter_context(redirect_stdout(self.output))
        stack.enter_context(redirect_stderr(self.error))
        self.root = self.home / ".claude-manager"

    def test_named_accounts_create_list_rename_and_run_through_existing_manager(self) -> None:
        self.assertEqual(manager.main(["--provider", "claude", "new", "work"]), 0)
        original = claude_profiles.load_profile(self.root, "work", self.home / ".claude")
        self.assertEqual(claude.main(["rename", "work", "office"]), 0)
        self.assertEqual(claude.main(["list"]), 0)
        self.assertIn("office", self.output.getvalue())
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            claude.main(["run", "office", "--", "--resume", "literal argument"])
        self.assertEqual(execute.call_args.args[1][1:], ["--resume", "literal argument"])
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], original["home"])

    def test_unknown_named_account_does_not_create_anything(self) -> None:
        with patch.object(os, "execve") as execute:
            self.assertEqual(claude.main(["run", "unregistered"]), 2)
        execute.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_account_numbers_are_ordinary_explicitly_created_names(self) -> None:
        self.assertEqual(claude.main(["list"]), 0)
        self.assertNotIn("account2", self.output.getvalue())
        self.assertNotIn("account3", self.output.getvalue())
        self.assertEqual(list(self.home.iterdir()), [])
        for name in ("account2", "account3"):
            with self.subTest(name=name):
                self.assertEqual(claude.main(["new", name]), 0)
                profile = claude_profiles.load_profile(self.root, name, self.home / ".claude")
                self.assertEqual(Path(profile["home"]).parent, self.root / "homes")
                self.assertFalse((self.home / f".claude-{name}").exists())
        self.assertEqual(claude.main(["rename", "account2", "secondary"]), 0)
        self.assertEqual(claude.main(["remove", "account3"]), 0)

    def test_force_new_does_not_silently_change_requested_sharing(self) -> None:
        self.assertEqual(claude.main(["new", "source"]), 0)
        self.assertEqual(claude.main(["new", "shared", "--share", "source"]), 0)
        before = claude_profiles.load_profile(self.root, "shared", self.home / ".claude")
        self.assertEqual(claude.main(["new", "shared", "--force", "--isolated"]), 2)
        self.assertEqual(claude_profiles.load_profile(self.root, "shared", self.home / ".claude"), before)
        self.assertEqual(claude.main(["new", "shared", "--force"]), 0)
        self.assertIn("Reusing Claude profile shared", self.output.getvalue())

    def test_missing_home_or_changed_projects_link_blocks_named_launch(self) -> None:
        self.assertEqual(claude.main(["new", "work"]), 0)
        profile = claude_profiles.load_profile(self.root, "work", self.home / ".claude")
        home = Path(profile["home"])
        projects = home / "projects"
        projects.unlink()
        projects.mkdir()
        for dry_run in ([], ["--dry-run"]):
            with self.subTest(dry_run=dry_run), patch.object(os, "execve") as execute:
                self.assertEqual(claude.main(["run", "work", *dry_run]), 2)
                execute.assert_not_called()
        shutil.rmtree(home)
        with patch.object(os, "execve") as execute:
            self.assertEqual(claude.main(["run", "work"]), 2)
            execute.assert_not_called()
        self.assertFalse(home.exists())

    def test_default_launch_keeps_global_configuration_location(self) -> None:
        config = self.home / ".claude.json"
        config.write_text('{"sentinel": true}', encoding="utf-8")
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            claude.main(["run", "default"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", execute.call_args.args[2])
        self.assertEqual(config.read_text(encoding="utf-8"), '{"sentinel": true}')
        self.assertFalse((self.home / ".claude" / ".claude.json").exists())

    def test_explicit_default_override_remains_explicit(self) -> None:
        directory = self.home / ".claude"
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            claude.main(["--default-home", str(directory), "run", "default"])
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], str(directory))

    def test_named_and_federation_auth_conflicts_are_reported_without_values(self) -> None:
        cases = [
            {"ANTHROPIC_PROFILE": "dummy-profile-secret"},
            {"ANTHROPIC_FEDERATION_RULE_ID": "dummy-rule-secret", "ANTHROPIC_ORGANIZATION_ID": "dummy-org-secret"},
        ]
        for environment in cases:
            with self.subTest(variables=list(environment)), patch.dict(os.environ, environment), patch.object(os, "execve") as execute:
                self.assertEqual(claude.main(["run", "default"]), 2)
            execute.assert_not_called()
            for name, value in environment.items():
                self.assertIn(name, self.error.getvalue())
                self.assertNotIn(value, self.error.getvalue())
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
