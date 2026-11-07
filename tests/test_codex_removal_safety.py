from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from agent_profile_manager import manager


class CodexRemovalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root, self.default = self.base / 'manager', self.base / 'default'
        self.default.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]

    def call(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return manager.main([*self.common, *args])

    def test_profile_named_isolated_cannot_authorize_default_deletion(self):
        marker = self.default / 'marker'
        marker.write_text('default data')
        self.assertEqual(self.call('new', 'isolated'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'isolated'), 0)
        self.assertEqual(self.call('remove', 'isolated'), 0)
        self.assertEqual(self.call('remove', 'follower', '--purge', '--remove-store'), 2)
        self.assertEqual(marker.read_text(), 'default data')
        self.assertTrue((self.root / 'profiles' / 'follower').is_dir())

    def test_legacy_ambiguous_metadata_cannot_delete_external_or_default_store(self):
        for index, external in enumerate((self.default, self.base / 'external')):
            with self.subTest(external=external):
                name = f'work-{index}'
                external.mkdir(exist_ok=True)
                marker = external / 'marker'
                marker.write_text('keep')
                self.assertEqual(self.call('new', name, '--isolated'), 0)
                profile = manager.load_profile(self.root, name, self.default)
                profile['conversation_store'] = str(external)
                profile.pop('sharing_mode', None)
                profile['share'] = 'isolated'
                manager.save_profile(self.root, profile)
                self.assertEqual(self.call('remove', name, '--purge', '--remove-store'), 2)
                self.assertEqual(marker.read_text(), 'keep')
                self.assertEqual(self.call('remove', name), 0)

    def test_explicit_isolation_cannot_authorize_external_or_default_store(self):
        for index, external in enumerate((self.default, self.base / 'outside')):
            with self.subTest(external=external):
                external.mkdir(exist_ok=True)
                name = f'explicit-{index}'
                self.assertEqual(self.call('new', name, '--isolated'), 0)
                profile = manager.load_profile(self.root, name, self.default)
                profile.update(conversation_store=str(external), sharing_mode='isolated')
                manager.save_profile(self.root, profile)
                self.assertEqual(self.call('remove', name, '--purge', '--remove-store'), 2)
                self.assertTrue(external.is_dir())
                self.assertTrue(Path(profile['home']).is_dir())

    def test_default_sqlite_use_protects_private_store_and_its_subdirectories(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                self.assertEqual(self.call('new', 'private', '--isolated'), 0)
                profile = manager.load_profile(self.root, 'private', self.default)
                store = Path(profile['conversation_store'])
                sqlite_home = store / 'databases' if nested else store
                sqlite_home.mkdir(exist_ok=True)
                marker = sqlite_home / 'state_5.sqlite'
                marker.write_text('dummy SQLite marker')
                config = self.default / 'config.toml'
                config.write_text('sqlite_home = ' + json.dumps(str(sqlite_home)) + '\n')
                self.assertEqual(self.call('remove', 'private', '--purge', '--remove-store'), 2)
                self.assertTrue((self.root / 'profiles' / 'private').exists())
                self.assertEqual(marker.read_text(), 'dummy SQLite marker')
                config.unlink()
                self.assertEqual(self.call('remove', 'private', '--purge', '--remove-store'), 0)

    def test_another_profiles_saved_sqlite_home_protects_store(self):
        self.call('new', 'private', '--isolated')
        self.call('new', 'other', '--isolated')
        private = manager.load_profile(self.root, 'private', self.default)
        other = manager.load_profile(self.root, 'other', self.default)
        other['sqlite_home'] = private['conversation_store']
        manager.save_profile(self.root, other)
        self.assertEqual(self.call('remove', 'private', '--remove-store'), 2)
        self.assertTrue(Path(private['home']).is_dir())

    def test_rejected_removal_does_not_uninstall_registered_wrapper(self):
        self.call('new', 'private', '--isolated')
        self.call('new', 'follower', '--share', 'private')
        binary = self.base / 'bin'
        self.assertEqual(self.call('install', 'private', '--bin-dir', str(binary)), 0)
        self.assertEqual(self.call('remove', 'private', '--remove-store', '--uninstall'), 2)
        self.assertTrue((binary / 'private').is_file())
        self.assertIn('wrapper', manager.load_profile(self.root, 'private', self.default))

    def test_profile_home_used_as_default_sqlite_is_protected_even_without_remove_store(self):
        self.call('new', 'work')
        profile = manager.load_profile(self.root, 'work', self.default)
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(profile['home']) + '\n')
        self.assertEqual(self.call('remove', 'work', '--purge'), 2)
        self.assertTrue(Path(profile['home']).is_dir())

    def test_symlinked_store_directory_is_not_removed(self):
        self.call('new', 'private', '--isolated')
        profile = manager.load_profile(self.root, 'private', self.default)
        actual = Path(profile['conversation_store'])
        renamed = self.root / 'stores-real'
        (self.root / 'stores').rename(renamed)
        (self.root / 'stores').symlink_to(renamed, target_is_directory=True)
        self.assertEqual(self.call('remove', 'private', '--purge', '--remove-store'), 2)
        self.assertTrue(actual.is_dir())
