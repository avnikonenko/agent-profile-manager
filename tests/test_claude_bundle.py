from __future__ import annotations

import argparse
import io
import json
import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_profile_manager import claude_bundle as bundle, claude_profiles as profiles, profile_bundle


class ClaudeBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"HOME": str(self.directory)}, clear=True))
        stack.enter_context(patch.object(bundle, "prompt_export_passphrase", return_value="dummy-test-passphrase"))
        stack.enter_context(patch.object(bundle, "prompt_import_passphrase", return_value="dummy-test-passphrase"))
        self.output = io.StringIO()
        stack.enter_context(redirect_stdout(self.output))
        self.default = self.directory / ".claude"
        self.default.mkdir(mode=0o700)
        self.root = self.directory / "manager"
        self.args = argparse.Namespace(
            manager_root=self.root, default_home=self.default, name="source", output=self.directory / "export.claude-profile",
            include_conversations=False, bundle=self.directory / "export.claude-profile", force=False,
            isolated=False, install=False, wrapper_name=None, bin_dir=self.directory / "bin", claude_binary="claude",
        )

    def new_profile(self, name="source", credential="dummy-secret-source"):
        profile = profiles.create_profile(self.root, name, self.default)
        home = Path(profile["home"])
        (home / ".credentials.json").write_text(json.dumps({"token": credential}), encoding="utf-8")
        (home / "settings.json").write_text('{"model":"test"}', encoding="utf-8")
        (home / ".claude.json").write_text('{"account":"dummy"}', encoding="utf-8")
        (home / "CLAUDE.md").write_text("Test instructions", encoding="utf-8")
        return profile

    def roundtrip_export(self, history=False):
        self.args.include_conversations = history
        self.assertEqual(bundle.command_export(self.args), 0)
        self.args.name = "imported"
        self.assertEqual(bundle.command_import(self.args), 0)
        return profiles.load_profile(self.root, "imported", self.default)

    def malformed_bundle(self, extra=None, manifest=None):
        source = self.directory / "malformed.zip"
        if manifest is None:
            manifest = {"format": bundle.FORMAT, "version": 1, "name": "imported", "includes": {"conversations": False}}
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("profile/.credentials.json", '{"token":"dummy"}')
            if extra:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    for name, data in extra:
                        archive.writestr(name, data)
        profile_bundle.encrypt_file(source, self.args.bundle, "dummy-test-passphrase")
        self.args.name = "imported"

    def test_encrypted_roundtrip_restores_allowlisted_files_privately(self):
        source = self.new_profile()
        imported = self.roundtrip_export()
        self.assertNotEqual(source["home"], imported["home"])
        self.assertEqual(imported["share"], "isolated")
        home = Path(imported["home"])
        for name in bundle.PROFILE_FILES:
            self.assertEqual((home / name).read_bytes(), (Path(source["home"]) / name).read_bytes())
            self.assertEqual((home / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.args.bundle.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b"dummy-secret-source", self.args.bundle.read_bytes())
        self.assertNotIn("dummy-secret-source", self.output.getvalue())
        self.assertEqual((home / "projects").resolve(), Path(imported["conversation_store"]))

    def test_history_and_memory_are_opt_in(self):
        source = self.new_profile()
        store = Path(source["conversation_store"])
        memory = store / "project" / "memory"
        memory.mkdir(parents=True)
        (memory / "MEMORY.md").write_text("dummy memory", encoding="utf-8")
        (store / "session.jsonl").write_text("dummy conversation", encoding="utf-8")
        imported = self.roundtrip_export(history=True)
        destination = Path(imported["conversation_store"])
        self.assertEqual((destination / "project/memory/MEMORY.md").read_text(), "dummy memory")
        self.assertEqual((destination / "session.jsonl").read_text(), "dummy conversation")

    def test_default_external_account_config_moves_into_managed_home(self):
        (self.default / ".credentials.json").write_text('{"token":"dummy-default"}')
        external = self.directory / ".claude.json"
        external.write_text('{"account":"dummy-default"}')
        self.args.name = "default"
        bundle.command_export(self.args)
        self.args.name = "imported"
        bundle.command_import(self.args)
        imported = profiles.load_profile(self.root, "imported", self.default)
        self.assertEqual((Path(imported["home"]) / ".claude.json").read_bytes(), external.read_bytes())
        self.assertEqual(external.read_text(), '{"account":"dummy-default"}')

    def test_explicit_default_config_directory_exports_internal_account_config(self):
        (self.default / ".credentials.json").write_text('{"token":"dummy-default"}')
        (self.default / ".claude.json").write_text('{"account":"internal"}')
        (self.directory / ".claude.json").write_text('{"account":"external-do-not-copy"}')
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.default)
        self.args.name = "default"
        self.args.default_config_external = False
        bundle.command_export(self.args)
        self.args.name = "imported"
        bundle.command_import(self.args)
        imported = profiles.load_profile(self.root, "imported", self.default)
        self.assertEqual((Path(imported["home"]) / ".claude.json").read_text(), '{"account":"internal"}')

    def test_export_profile_size_limit_matches_import_limit(self):
        self.new_profile()
        with patch.object(bundle, "MAX_PROFILE_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "size limit"):
                bundle.command_export(self.args)
        self.assertFalse(self.args.output.exists())

    def test_existing_manager_directory_permissions_are_preserved(self):
        self.new_profile()
        self.root.chmod(0o750)
        self.roundtrip_export()
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o750)

    def test_missing_file_credentials_is_actionable_and_never_reads_keychain(self):
        self.args.name = "default"
        with self.assertRaisesRegex(ValueError, "Keychain"):
            bundle.command_export(self.args)
        self.assertFalse(self.args.output.exists())

    def test_authenticated_non_zip_payload_is_reported_without_destination_changes(self):
        source = self.directory / "not-a-zip"
        source.write_bytes(b"authenticated dummy malformed payload")
        profile_bundle.encrypt_file(source, self.args.bundle, "dummy-test-passphrase")
        self.args.name = "imported"
        with self.assertRaisesRegex(ValueError, "archive is invalid"):
            bundle.command_import(self.args)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.default.iterdir()), [])

    def test_wrong_passphrase_creates_no_profile(self):
        self.new_profile()
        bundle.command_export(self.args)
        self.args.name = "imported"
        with patch.object(bundle, "prompt_import_passphrase", return_value="wrong-passphrase"):
            with self.assertRaises(profile_bundle.BundleError):
                bundle.command_import(self.args)
        self.assertFalse(profiles.profile_dir(self.root, "imported").exists())

    def test_foreign_provider_is_rejected_before_manager_creation(self):
        self.malformed_bundle(manifest={"format": "codex-manager-profile", "version": 1, "name": "imported", "includes": {"conversations": False}})
        with self.assertRaisesRegex(ValueError, "Claude"):
            bundle.command_import(self.args)
        self.assertFalse(self.root.exists())

    def test_unsafe_duplicate_unexpected_and_symlink_members_are_rejected(self):
        symlink = zipfile.ZipInfo("profile/settings.json")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [[("../escape", "bad")], [("/absolute", "bad")], [("profile\\settings.json", "bad")],
                 [("profile/.credentials.json", "duplicate")], [("profile/auth.json", "unexpected")], [(symlink, "target")],
                 [("conversations/projects", "not-directory")]]
        for extra in cases:
            with self.subTest(extra=extra):
                self.malformed_bundle(extra=extra)
                with self.assertRaises(ValueError):
                    bundle.command_import(self.args)
                self.assertFalse(self.root.exists())
                self.args.bundle.unlink()

    def test_default_import_refused(self):
        self.malformed_bundle()
        self.args.name = "default"
        with self.assertRaisesRegex(ValueError, "Default"):
            bundle.command_import(self.args)
        self.assertEqual(list(self.default.iterdir()), [])
        self.assertFalse(self.root.exists())

    def test_export_never_clobbers_and_rejects_linked_credentials(self):
        source = self.new_profile()
        self.args.output.write_text("keep")
        with self.assertRaisesRegex(ValueError, "existing"):
            bundle.command_export(self.args)
        self.assertEqual(self.args.output.read_text(), "keep")
        self.args.output.unlink()
        credential = Path(source["home"]) / ".credentials.json"
        linked = self.directory / "linked-secret"
        credential.rename(linked)
        credential.symlink_to(linked)
        with self.assertRaisesRegex(ValueError, "symlink"):
            bundle.command_export(self.args)
        self.assertFalse(self.args.output.exists())

    def test_export_rejects_history_symlinks(self):
        source = self.new_profile()
        (Path(source["conversation_store"]) / "linked").symlink_to(self.directory)
        self.args.include_conversations = True
        with self.assertRaisesRegex(ValueError, "symlink"):
            bundle.command_export(self.args)
        self.assertFalse(self.args.output.exists())

    def test_force_import_preserves_history_and_keeps_private_backups(self):
        source = self.new_profile()
        (Path(source["conversation_store"]) / "new.jsonl").write_text("do not copy")
        self.args.include_conversations = True
        bundle.command_export(self.args)
        target = self.new_profile("target", "dummy-old-secret")
        store = Path(target["conversation_store"])
        (store / "existing.jsonl").write_text("keep existing")
        self.args.name = "target"
        self.args.force = True
        bundle.command_import(self.args)
        self.assertEqual((store / "existing.jsonl").read_text(), "keep existing")
        self.assertFalse((store / "new.jsonl").exists())
        self.assertIn("skipped", self.output.getvalue())
        self.assertEqual(json.loads((Path(target["home"]) / ".credentials.json").read_text())["token"], "dummy-secret-source")
        backups = list((self.root / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o700)
        credential = backups[0] / ".credentials.json"
        self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(credential.read_text())["token"], "dummy-old-secret")
        self.assertNotIn("dummy-old-secret", self.output.getvalue())

    def test_force_failure_rolls_back_credentials_and_settings(self):
        self.new_profile()
        bundle.command_export(self.args)
        target = self.new_profile("target", "dummy-old-secret")
        self.args.name = "target"
        self.args.force = True
        original_replace = os.replace
        failed = False
        def replace(source, destination):
            nonlocal failed
            if Path(destination).name == "settings.json" and not failed:
                failed = True
                raise OSError("simulated failure")
            return original_replace(source, destination)
        with patch.object(bundle.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                bundle.command_import(self.args)
        self.assertEqual(json.loads((Path(target["home"]) / ".credentials.json").read_text())["token"], "dummy-old-secret")
        self.assertEqual((Path(target["home"]) / "settings.json").read_text(), '{"model":"test"}')

    def test_archive_limits_and_special_files_are_rejected(self):
        self.malformed_bundle()
        with patch.object(bundle, "MAX_TOTAL_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                bundle.command_import(self.args)
        self.assertFalse(self.root.exists())
        special = zipfile.ZipInfo("profile/settings.json")
        special.create_system = 3
        special.external_attr = (stat.S_IFIFO | 0o600) << 16
        self.args.bundle.unlink()
        self.malformed_bundle(extra=[(special, "bad")])
        with self.assertRaisesRegex(ValueError, "special"):
            bundle.command_import(self.args)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
