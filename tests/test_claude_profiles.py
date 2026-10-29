from __future__ import annotations

import argparse
import contextlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_profile_manager import claude_profiles as profiles


class ClaudeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "manager"
        self.default = self.base / "existing"
        (self.default / "projects").mkdir(parents=True)
        self.bin = self.base / "bin"

    def args(self, name="work", **kwargs):
        values = dict(name=name, manager_root=self.root, default_home=self.default,
                      bin_dir=self.bin, wrapper_name=None, claude_binary="claude", force=False,
                      share=None, install=False, uninstall=False, purge=False, remove_store=False)
        values.update(kwargs)
        return argparse.Namespace(**values)

    def create(self, name="work", **kwargs):
        return profiles.create_profile(self.root, name, self.default, **kwargs)

    def command(self, function, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(self.args(**kwargs))

    def test_create_private_stable_home_and_store(self):
        profile = self.create()
        home = profiles.home_from_profile(profile)
        store = profiles.store_from_profile(profile)
        self.assertEqual(home.parent, self.root / "homes")
        self.assertEqual(store.parent, self.root / "stores")
        self.assertEqual((home / "projects").resolve(), store)
        for path in (home, store, profiles.profile_dir(self.root, "work")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(profiles.profile_file(self.root, "work").stat().st_mode), 0o600)
        self.assertEqual(profiles.load_profile(self.root, "work", self.default), profile)

    def test_default_config_is_never_changed(self):
        secret = self.default / ".credentials.json"
        secret.write_text("existing login")
        before = self.default.stat().st_mode
        self.create()
        self.assertEqual(secret.read_text(), "existing login")
        self.assertEqual(self.default.stat().st_mode, before)
        self.assertEqual(profiles.load_profile(self.root, "default", self.default)["home"], str(self.default))

    def test_arbitrary_names_but_reserved_and_traversal_rejected(self):
        for name in ("default", "../bad", "", "a/b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.create(name)
        self.create("research-team_4")
        self.create("account2")
        self.create("account3")
        self.assertEqual([p["name"] for p in profiles.iter_profiles(self.root, self.default)], ["account2", "account3", "research-team_4"])

    def test_force_reuses_without_changing_sharing(self):
        first = self.create()
        self.assertEqual(self.create(force=True), first)
        with self.assertRaises(ValueError):
            self.create(force=True, share="default")

    def test_projects_sharing_only_and_source_rename_stable(self):
        source = self.create()
        dependent = self.create("other", share="work")
        home = profiles.home_from_profile(source)
        (home / ".credentials.json").write_text("private source credential")
        other_home = profiles.home_from_profile(dependent)
        self.assertFalse((other_home / ".credentials.json").exists())
        self.assertEqual((other_home / "projects").resolve(), profiles.store_from_profile(source))
        self.command(profiles.command_rename, new_name="office")
        renamed = profiles.load_profile(self.root, "office", self.default)
        self.assertEqual(renamed["home"], source["home"])
        self.assertEqual(profiles.load_profile(self.root, "other", self.default)["share"], "office")
        self.assertEqual((home / ".credentials.json").read_text(), "private source credential")

    def test_share_default_does_not_modify_existing_config(self):
        profile = self.create(share="default")
        self.assertEqual(profile["conversation_store"], str(self.default / "projects"))
        with self.assertRaises(ValueError):
            self.command(profiles.command_remove, remove_store=True)

    def test_remove_to_trash_retains_shared_store(self):
        source = self.create()
        dependent = self.create("other", share="work")
        for option in ({"purge": True}, {"remove_store": True}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.command(profiles.command_remove, **option)
        self.command(profiles.command_remove)
        self.assertFalse(profiles.profile_dir(self.root, "work").exists())
        self.assertTrue(Path(source["conversation_store"]).is_dir())
        self.assertEqual((Path(dependent["home"]) / "projects").resolve(), Path(source["conversation_store"]))
        self.assertEqual(len(list((self.root / "trash").glob("*/home"))), 1)

    def test_explicit_purge_and_store_removal(self):
        source = self.create()
        self.command(profiles.command_remove, purge=True, remove_store=True)
        self.assertFalse(Path(source["home"]).exists())
        self.assertFalse(Path(source["conversation_store"]).exists())
        self.assertEqual(list((self.root / "trash").iterdir()), [])

    def test_metadata_cannot_redirect_home_or_store(self):
        original = self.create()
        file = profiles.profile_file(self.root, "work")
        for field in ("home", "conversation_store"):
            changed = dict(original)
            changed[field] = str(self.default)
            file.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaises(ValueError):
                profiles.load_profile(self.root, "work", self.default)
        file.write_text(json.dumps({**original, "provider": "codex"}))
        with self.assertRaises(ValueError):
            profiles.load_profile(self.root, "work", self.default)

    def test_symlink_metadata_and_managed_home_rejected(self):
        original = self.create()
        file = profiles.profile_file(self.root, "work")
        backup = self.base / "external.json"
        file.rename(backup)
        file.symlink_to(backup)
        with self.assertRaises(ValueError):
            profiles.load_profile(self.root, "work", self.default)
        file.unlink()
        backup.rename(file)
        home = Path(original["home"])
        renamed = self.base / "external-home"
        home.rename(renamed)
        home.symlink_to(renamed, target_is_directory=True)
        with self.assertRaises(ValueError):
            profiles.load_profile(self.root, "work", self.default)

    def test_invalid_wrapper_metadata_types_are_validation_errors(self):
        self.create()
        self.command(profiles.command_install)
        registered = profiles.load_profile(self.root, "work", self.default)
        for field, value in (("command", []), ("command", 123), ("naming", [])):
            malformed = dict(registered)
            malformed["wrapper"] = {**registered["wrapper"], field: value}
            profiles.profile_file(self.root, "work").write_text(json.dumps(malformed))
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                profiles.load_profile(self.root, "work", self.default)

    def test_duplicate_home_reference_cannot_remove_other_account(self):
        original = self.create()
        other = self.create("other")
        other["home"] = original["home"]
        profiles.save_profile(self.root, other, self.default)
        with self.assertRaises(ValueError):
            self.command(profiles.command_remove, name="other")
        self.assertTrue(Path(original["home"]).is_dir())

    def test_install_metadata_failure_removes_only_new_wrapper(self):
        self.create()
        with patch.object(profiles, "save_profile", side_effect=OSError("metadata failure")):
            with self.assertRaises(OSError):
                self.command(profiles.command_install)
        self.assertFalse((self.bin / "work").exists())
        self.assertNotIn("wrapper", profiles.load_profile(self.root, "work", self.default))

    def test_trash_symlink_cannot_redirect_remove(self):
        original = self.create()
        external = self.base / "external-trash"
        external.mkdir()
        (self.root / "trash").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.command(profiles.command_remove)
        self.assertTrue(Path(original["home"]).is_dir())
        self.assertEqual(list(external.iterdir()), [])

    def test_wrapper_install_collision_and_modified_refusal(self):
        self.create()
        self.bin.mkdir()
        occupied = self.bin / "work"
        occupied.write_text("unrelated")
        with self.assertRaises(ValueError):
            self.command(profiles.command_install, force=True)
        self.assertEqual(occupied.read_text(), "unrelated")
        occupied.unlink()
        self.command(profiles.command_install)
        self.assertIn("agent_profile_manager.claude", occupied.read_text())
        self.assertIn('"$@"', occupied.read_text())
        occupied.write_text("modified")
        for function in (profiles.command_install, profiles.command_uninstall):
            with self.subTest(function=function), self.assertRaises(ValueError):
                self.command(function, force=True)
        self.assertEqual(occupied.read_text(), "modified")

    def test_default_wrapper_rename_and_custom_wrapper_retained(self):
        for custom in (None, "cc-work"):
            with self.subTest(custom=custom):
                name = "work" if custom is None else "personal"
                profile = self.create(name)
                self.command(profiles.command_install, name=name, wrapper_name=custom)
                old = self.bin / (custom or name)
                self.command(profiles.command_rename, name=name, new_name=name + "2")
                target = self.bin / (custom or name + "2")
                self.assertTrue(target.exists())
                if custom is None:
                    self.assertFalse(old.exists())
                renamed = profiles.load_profile(self.root, name + "2", self.default)
                self.assertEqual(renamed["home"], profile["home"])
                self.assertIn(name + "2", target.read_text())
                self.command(profiles.command_uninstall, name=name + "2")
                self.assertFalse(target.exists())

    def test_wrapper_self_recursion_rejected(self):
        self.create("claude")
        with self.assertRaises(ValueError):
            self.command(profiles.command_install, name="claude")
        self.create()
        with self.assertRaises(ValueError):
            self.command(profiles.command_install, claude_binary=str(self.bin / "work"))

    def test_wrapper_launches_fake_binary_with_stable_home_and_literal_args(self):
        profile = self.create()
        fake = self.base / "fake-claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json,os,sys\n"
            "print(json.dumps({'home':os.environ['CLAUDE_CONFIG_DIR'],'args':sys.argv[1:],'cwd':os.getcwd()}))\n"
            "sys.exit(23)\n"
        )
        fake.chmod(0o755)
        self.command(profiles.command_install, claude_binary=str(fake))
        forwarded = ["argument with spaces", "$(touch must-not-exist)", "--dry-run", "--"]
        result = subprocess.run(
            [str(self.bin / "work"), *forwarded], cwd=self.base,
            env={"HOME": str(self.base), "PATH": "/usr/bin:/bin"},
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "home": profile["home"], "args": forwarded, "cwd": str(self.base),
        })
        self.assertFalse((self.base / "must-not-exist").exists())

    def test_rename_publication_race_preserves_unrelated_command(self):
        self.create()
        self.command(profiles.command_install)
        target = self.bin / "office"
        def race(path, content, **kwargs):
            path.write_text("unrelated raced command")
            raise FileExistsError("raced")
        with patch.object(profiles, "_write_wrapper", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.command(profiles.command_rename, new_name="office")
        self.assertEqual(target.read_text(), "unrelated raced command")
        self.assertTrue(profiles.profile_dir(self.root, "work").is_dir())
        self.assertFalse(profiles.profile_dir(self.root, "office").exists())

    def test_repair_only_missing_projects_link(self):
        profile = self.create()
        projects = Path(profile["home"]) / "projects"
        projects.unlink()
        self.command(profiles.command_repair)
        self.assertEqual(projects.resolve(), Path(profile["conversation_store"]))
        projects.unlink()
        projects.mkdir()
        with self.assertRaises(ValueError):
            self.command(profiles.command_repair, force=True)

    def test_remove_tracked_wrapper_requires_explicit_uninstall(self):
        self.create()
        self.command(profiles.command_install)
        with self.assertRaises(ValueError):
            self.command(profiles.command_remove)
        self.command(profiles.command_remove, uninstall=True)
        self.assertFalse((self.bin / "work").exists())


if __name__ == "__main__":
    unittest.main()
