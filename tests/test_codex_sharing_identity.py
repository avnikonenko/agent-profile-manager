from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from agent_profile_manager import manager


class CodexSharingIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root, self.default = base / 'manager', base / 'default'
        self.default.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]

    def call(self, *arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return manager.main([*self.common, *arguments])

    def sqlite_home(self, name):
        return manager.profile_environment(name, self.root, self.default, base={})['CODEX_SQLITE_HOME']

    def test_deleted_source_name_reuse_does_not_redirect_followers(self):
        self.assertEqual(self.call('new', 'source', '--isolated'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'source'), 0)
        self.assertEqual(self.call('new', 'indirect', '--share', 'follower'), 0)
        original = self.sqlite_home('follower')
        self.assertEqual(self.call('remove', 'source'), 0)
        self.assertEqual(self.sqlite_home('follower'), original)
        self.assertEqual(self.call('new', 'follower', '--force'), 0)
        self.assertEqual(self.call('new', 'source'), 0)
        self.assertEqual(self.sqlite_home('source'), str(self.default))
        for name in ('follower', 'indirect'):
            self.assertEqual(self.sqlite_home(name), original)
            self.assertEqual(self.call('new', name, '--force'), 0)
            self.assertEqual(self.sqlite_home(name), original)

    def test_valid_shared_chain_still_tracks_default_sqlite_config(self):
        self.assertEqual(self.call('new', 'source'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'source'), 0)
        custom = self.default.parent / 'custom-sqlite'
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        self.assertEqual(self.sqlite_home('follower'), str(custom))

    def test_profile_named_isolated_can_be_an_explicit_shared_source(self):
        self.assertEqual(self.call('new', 'isolated'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'isolated'), 0)
        profile = manager.load_profile(self.root, 'follower', self.default)
        self.assertEqual(profile['share'], 'isolated')
        self.assertEqual(profile['sharing_mode'], 'shared')
        self.assertFalse(manager.profile_is_isolated(profile))
        self.assertEqual(self.call('new', 'follower', '--force'), 0)
        self.assertEqual(manager.load_profile(self.root, 'follower', self.default)['sharing_mode'], 'shared')
        custom = self.default.parent / 'custom-sqlite'
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        self.assertEqual(self.sqlite_home('follower'), str(custom))

    def test_legacy_private_sentinel_is_preserved_on_force(self):
        self.assertEqual(self.call('new', 'private', '--isolated'), 0)
        profile = manager.load_profile(self.root, 'private', self.default)
        profile.pop('sharing_mode')
        manager.save_profile(self.root, profile)
        self.assertTrue(manager.profile_is_isolated(profile))
        self.assertEqual(self.call('new', 'private', '--force'), 0)
        updated = manager.load_profile(self.root, 'private', self.default)
        self.assertEqual(updated['sharing_mode'], 'isolated')
        self.assertEqual(updated['conversation_store'], profile['conversation_store'])

    def test_legacy_isolated_source_name_cannot_mark_default_store_private(self):
        self.assertEqual(self.call('new', 'isolated'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'isolated'), 0)
        profile = manager.load_profile(self.root, 'follower', self.default)
        profile.pop('sharing_mode')
        manager.save_profile(self.root, profile)
        self.assertFalse(manager.profile_is_isolated(profile))
        self.assertEqual(self.call('new', 'follower', '--force'), 0)
        self.assertEqual(manager.load_profile(self.root, 'follower', self.default)['sharing_mode'], 'shared')

    def test_renaming_isolated_source_does_not_rewrite_private_sentinels(self):
        self.assertEqual(self.call('new', 'isolated'), 0)
        self.assertEqual(self.call('new', 'private', '--isolated'), 0)
        self.assertEqual(self.call('new', 'follower', '--share', 'isolated'), 0)
        original = manager.load_profile(self.root, 'private', self.default)
        self.assertEqual(self.call('rename', 'isolated', 'renamed-source'), 0)
        self.assertEqual(manager.load_profile(self.root, 'private', self.default), original)
        follower = manager.load_profile(self.root, 'follower', self.default)
        self.assertEqual(follower['share'], 'renamed-source')
        self.assertFalse(manager.profile_is_isolated(follower))
