from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_profile_manager import claude, claude_profiles


class ClaudeTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = self.home / ".claude-manager"
        self.default = self.home / ".claude"
        stack.enter_context(patch.dict(os.environ, {"HOME": str(self.home)}, clear=True))
        self.output = io.StringIO()
        self.error = io.StringIO()
        stack.enter_context(redirect_stdout(self.output))
        stack.enter_context(redirect_stderr(self.error))

    def new_profile(self, name: str = "work") -> Path:
        profile = claude_profiles.create_profile(self.root, name, self.default)
        return Path(profile["home"])

    def snapshot(self) -> list[tuple[str, int, str | bytes | None]]:
        result = []
        for path in sorted(self.home.rglob("*")):
            content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
            result.append((str(path.relative_to(self.home)), path.lstat().st_mode, content))
        return result

    def invoke(self, args: list[str]) -> int:
        return claude.main(args)

    def test_list_contains_only_default_until_named_profiles_are_created(self) -> None:
        with patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["list"]), 0)
        rows = self.output.getvalue().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].split("\t")[0], "default")
        self.assertIn(str(self.default), rows[1])
        self.assertEqual(self.snapshot(), [])
        execute.assert_not_called()

    def test_list_has_stable_named_profile_order_and_does_not_mutate_files(self) -> None:
        zeta = self.new_profile("zeta")
        alpha = self.new_profile("alpha")
        before = self.snapshot()
        self.assertEqual(self.invoke(["list"]), 0)
        rows = self.output.getvalue().splitlines()[1:]
        self.assertEqual([row.split("\t")[0] for row in rows], ["default", "alpha", "zeta"])
        self.assertIn(str(alpha), rows[1])
        self.assertIn(str(zeta), rows[2])
        self.assertEqual(self.snapshot(), before)

    def test_numbered_account_names_are_regular_managed_profiles(self) -> None:
        for name in ("account2", "account3"):
            path = self.new_profile(name)
            self.assertEqual(path.parent, self.root / "homes")
            with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.invoke(["run", name])
            self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], str(path))

    def test_dry_run_on_either_side_of_profile_never_mutates_or_executes(self) -> None:
        self.new_profile()
        before = self.snapshot()
        for args in (["run", "--dry-run", "work"], ["run", "work", "--dry-run"]):
            with self.subTest(args=args), patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(args), 0)
                self.assertEqual(self.snapshot(), before)
                execute.assert_not_called()

    def test_default_environment_is_resolved_at_invocation_time(self) -> None:
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            for directory in (self.home / "first", self.home / "second"):
                os.environ["CLAUDE_CONFIG_DIR"] = str(directory)
                self.invoke(["run", "default"])
                self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], str(directory))
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_explicit_manager_root_selects_profile_from_that_manager(self) -> None:
        self.new_profile("work")
        other_root = self.home / "other-manager"
        other = claude_profiles.create_profile(other_root, "work", self.default)
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.invoke(["--manager-root", str(other_root), "run", "work"])
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], other["home"])

    def test_explicit_default_home_sets_child_config_directory(self) -> None:
        custom = self.home / "custom-default"
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.invoke(["--default-home", str(custom), "run", "default"])
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], str(custom))

    def test_named_profiles_use_stable_distinct_homes_without_changing_parent_environment(self) -> None:
        work = self.new_profile("work")
        personal = self.new_profile("personal")
        self.assertNotEqual(work, personal)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / "existing-default")
        os.environ["TEST_KEEP"] = "keep"
        original_env = dict(os.environ)
        before = self.snapshot()
        for name, directory in (("work", work), ("personal", personal)):
            with self.subTest(name=name), patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.invoke(["run", name])
                args = execute.call_args.args
                self.assertEqual(args[0], "/fake/claude")
                self.assertEqual(args[1][1:], [])
                self.assertEqual(args[2]["CLAUDE_CONFIG_DIR"], str(directory))
                self.assertEqual(args[2]["TEST_KEEP"], "keep")
                self.assertEqual(dict(os.environ), original_env)
        self.assertEqual(self.snapshot(), before)

    def test_existing_mode_contents_and_nested_markers_are_preserved(self) -> None:
        directory = self.new_profile()
        directory.chmod(0o750)
        (directory / "settings.json").write_text('{"sentinel": true}\n', encoding="utf-8")
        before = self.snapshot()
        os.environ["CLAUDECODE"] = "1"
        os.environ["CLAUDE_CODE_CHILD_SESSION"] = "1"
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.invoke(["run", "work"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(execute.call_args.args[2]["CLAUDECODE"], "1")
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CODE_CHILD_SESSION"], "1")

    def test_forwarded_arguments_are_literal_and_delimiter_ends_launcher_options(self) -> None:
        self.new_profile()
        forwarded = ["--dry-run", "--resume", "space value", "$(touch unwanted)", "quote'\"", "žluťoučký"]
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.invoke(["run", "work", "--", *forwarded])
        self.assertEqual(execute.call_args.args[1][1:], forwarded)
        self.assertFalse((self.home / "unwanted").exists())

    def test_dry_run_does_not_print_forwarded_secret_arguments(self) -> None:
        self.new_profile()
        before = self.snapshot()
        secret = "dummy-secret-do-not-log"
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "work", "--dry-run", "--", "--settings", secret]), 0)
        self.assertNotIn(secret, self.output.getvalue() + self.error.getvalue())
        self.assertEqual(self.snapshot(), before)
        execute.assert_not_called()

    def test_invalid_cli_or_unknown_account_never_launches_or_creates_directories(self) -> None:
        cases = [[], ["run"], ["run", "unknown"], ["run", "default", "--dry-r"],
                 ["run", "default", "--resume"], ["list", "--", "forwarded"]]
        for args in cases:
            with self.subTest(args=args), patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(args), 2)
                execute.assert_not_called()
                self.assertEqual(self.snapshot(), [])

    def test_missing_executable_does_not_mutate_profile(self) -> None:
        self.new_profile()
        before = self.snapshot()
        with patch.object(claude.shutil, "which", return_value=None), patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "work"]), 127)
        execute.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_regular_file_as_default_directory_is_rejected(self) -> None:
        file = self.home / "not-a-directory"
        file.write_text("keep", encoding="utf-8")
        os.environ["CLAUDE_CONFIG_DIR"] = str(file)
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "default"]), 2)
        self.assertEqual(file.read_text(), "keep")
        execute.assert_not_called()

    def test_default_collision_with_managed_home_is_rejected_for_list_and_run(self) -> None:
        directory = self.new_profile()
        os.environ["CLAUDE_CONFIG_DIR"] = str(directory)
        before = self.snapshot()
        for args in (["list"], ["run", "work"], ["run", "default", "--dry-run"]):
            with self.subTest(args=args), patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(args), 2)
                self.assertEqual(self.snapshot(), before)
                execute.assert_not_called()

    def test_default_symlink_alias_cannot_hide_managed_home_collision(self) -> None:
        directory = self.new_profile()
        link = self.home / "default-alias"
        link.symlink_to(directory, target_is_directory=True)
        os.environ["CLAUDE_CONFIG_DIR"] = str(link)
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "work"]), 2)
        execute.assert_not_called()

    def test_default_symlink_to_distinct_owned_directory_is_allowed(self) -> None:
        target = self.home / "private-target"
        target.mkdir(mode=0o700)
        link = self.home / "default-alias"
        link.symlink_to(target, target_is_directory=True)
        os.environ["CLAUDE_CONFIG_DIR"] = str(link)
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
            self.invoke(["run", "default"])
        self.assertEqual(execute.call_args.args[2]["CLAUDE_CONFIG_DIR"], str(target))
        self.assertTrue(link.is_symlink())

    def test_authentication_conflicts_report_names_without_values_or_mutations(self) -> None:
        self.new_profile()
        before = self.snapshot()
        for variable in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                         "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_PROFILE"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "dummy-never-log-secret"}), patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(["run", "work"]), 2)
                self.assertIn(variable, self.error.getvalue())
                self.assertNotIn("dummy-never-log-secret", self.output.getvalue() + self.error.getvalue())
                self.assertEqual(self.snapshot(), before)
                execute.assert_not_called()

    def test_authentication_conflicts_also_block_dry_run(self) -> None:
        self.new_profile()
        before = self.snapshot()
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "dummy-never-log-secret"
        with patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "work", "--dry-run"]), 2)
        self.assertNotIn("dummy-never-log-secret", self.output.getvalue() + self.error.getvalue())
        self.assertEqual(self.snapshot(), before)
        execute.assert_not_called()

    def test_active_provider_switches_block_launch(self) -> None:
        self.new_profile()
        before = self.snapshot()
        for variable in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                         "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_SIMPLE"):
            for value in ("1", "true", " TRUE ", "yes", "on"):
                with self.subTest(variable=variable, value=value), patch.dict(os.environ, {variable: value}), patch.object(os, "execve") as execute:
                    self.assertEqual(self.invoke(["run", "work"]), 2)
                    self.assertIn(variable, self.error.getvalue())
                    self.assertEqual(self.snapshot(), before)
                    execute.assert_not_called()

    def test_disabled_provider_switches_do_not_block_dry_run(self) -> None:
        self.new_profile()
        before = self.snapshot()
        for value in ("0", "false", ""):
            overrides = {name: value for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                         "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_SIMPLE")}
            with self.subTest(value=value), patch.dict(os.environ, overrides), patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(["run", "work", "--dry-run"]), 0)
                execute.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_empty_or_unset_default_override_preserves_legacy_global_config_location(self) -> None:
        for value in (None, ""):
            if value is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = value
            with self.subTest(value=value), patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve") as execute:
                self.invoke(["run", "default"])
                self.assertNotIn("CLAUDE_CONFIG_DIR", execute.call_args.args[2])
                self.assertTrue(self.default.is_dir())

    def test_file_ancestor_of_default_path_reports_operational_error_without_launch(self) -> None:
        parent = self.home / "not-a-directory"
        parent.write_text("keep", encoding="utf-8")
        os.environ["CLAUDE_CONFIG_DIR"] = str(parent / "profile")
        with patch.object(os, "execve") as execute:
            self.assertEqual(self.invoke(["run", "default"]), 1)
        self.assertIn("NotADirectoryError", self.error.getvalue())
        self.assertEqual(parent.read_text(), "keep")
        execute.assert_not_called()

    def test_foreign_owned_or_inaccessible_managed_directory_is_rejected(self) -> None:
        directory = self.new_profile()
        directory.chmod(0o750)
        before = self.snapshot()
        for context in (patch.object(os, "getuid", return_value=os.getuid() + 1), patch.object(os, "access", return_value=False)):
            with context, patch.object(os, "execve") as execute:
                self.assertEqual(self.invoke(["run", "work"]), 2)
                execute.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_list_remains_available_with_authentication_conflicts(self) -> None:
        os.environ["ANTHROPIC_API_KEY"] = "dummy-never-log-secret"
        self.assertEqual(self.invoke(["list"]), 0)
        self.assertNotIn("dummy-never-log-secret", self.output.getvalue() + self.error.getvalue())

    def test_os_execution_error_is_sanitized(self) -> None:
        self.new_profile()
        with patch.object(claude.shutil, "which", return_value="/fake/claude"), patch.object(os, "execve", side_effect=PermissionError("dummy-secret-from-exception")):
            self.assertEqual(self.invoke(["run", "work"]), 1)
        self.assertIn("PermissionError", self.error.getvalue())
        self.assertNotIn("dummy-secret-from-exception", self.error.getvalue())

    @unittest.skipUnless(os.name == "posix", "executable fixture uses a POSIX shebang")
    def test_fake_executable_receives_managed_home_arguments_and_exit_status(self) -> None:
        managed_home = self.new_profile()
        bin_directory = self.home / "bin"
        bin_directory.mkdir()
        executable = bin_directory / "claude"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['TEST_RECORD']).write_text(json.dumps({\n"
            "    'config': os.environ['CLAUDE_CONFIG_DIR'],\n"
            "    'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
            "    'keep': os.environ['TEST_KEEP'],\n"
            "}))\n"
            "sys.exit(23)\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        record = self.home / "record.json"
        forwarded = ["--resume", "literal value", "$(touch SHOULD_NOT_EXIST)", "--dry-run"]
        environment = {
            "HOME": str(self.home), "PATH": str(bin_directory),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "TEST_RECORD": str(record), "TEST_KEEP": "keep",
        }
        result = subprocess.run(
            [sys.executable, "-c", "import sys; from agent_profile_manager.claude import main; sys.exit(main(sys.argv[1:]))",
             "run", "work", "--", *forwarded],
            env=environment, cwd=self.home, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(record.read_text(encoding="utf-8")), {
            "config": str(managed_home), "argv": forwarded,
            "cwd": str(self.home), "keep": "keep",
        })
        self.assertFalse((self.home / "SHOULD_NOT_EXIST").exists())
        self.assertFalse(self.default.exists())


if __name__ == "__main__":
    unittest.main()
