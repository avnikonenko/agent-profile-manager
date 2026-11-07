from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_profile_manager import manager


class CodexUninstallAndFallbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.default, self.bin = self.base / 'manager', self.base / 'default', self.base / 'bin'
        self.default.mkdir()
        self.bin.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]

    def call(self, *arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return manager.main([*self.common, *arguments])

    def environment(self, name):
        return manager.profile_environment(name, self.root, self.default)

    def test_explicit_untracked_uninstall_preserves_unrelated_commands_even_with_force(self):
        self.assertEqual(self.call('new', 'work'), 0)
        unrelated = self.bin / 'important-tool'
        unrelated.write_bytes(b'unrelated executable\x00contents')
        for force in ([], ['--force']):
            self.assertEqual(self.call('uninstall', 'work', '--wrapper', unrelated.name, '--bin-dir', str(self.bin), *force), 2)
            self.assertEqual(unrelated.read_bytes(), b'unrelated executable\x00contents')

    def test_explicit_custom_untracked_wrapper_is_recognized_by_contents(self):
        self.assertEqual(self.call('new', 'work'), 0)
        wrapper = self.bin / 'custom-work'
        wrapper.write_text(manager.wrapper_script('work', 'codex', self.root, self.default))
        self.assertEqual(self.call('uninstall', 'work', '--wrapper', wrapper.name, '--bin-dir', str(self.bin)), 0)
        self.assertFalse(wrapper.exists())
        # A manager wrapper for another profile is not this profile's command.
        wrapper.write_text(manager.wrapper_script('other', 'codex', self.root, self.default))
        self.assertEqual(self.call('uninstall', 'work', '--wrapper', wrapper.name, '--bin-dir', str(self.bin)), 2)
        self.assertTrue(wrapper.is_file())

    def test_untracked_symlink_is_never_followed_or_removed(self):
        self.call('new', 'work')
        unrelated = self.base / 'outside-command'
        unrelated.write_text(manager.wrapper_script('work', 'codex', self.root, self.default))
        link = self.bin / 'custom-work'
        link.symlink_to(unrelated)
        self.assertEqual(self.call('uninstall', 'work', '--wrapper', link.name, '--bin-dir', str(self.bin), '--force'), 2)
        self.assertTrue(link.is_symlink())
        self.assertTrue(unrelated.is_file())

    def test_registered_forced_uninstall_removes_link_not_its_target(self):
        self.call('new', 'work')
        self.call('install', 'work', '--wrapper', 'custom-work', '--bin-dir', str(self.bin))
        wrapper = self.bin / 'custom-work'
        outside = self.base / 'outside-command'
        outside.write_text(wrapper.read_text())
        wrapper.unlink()
        wrapper.symlink_to(outside)
        self.assertEqual(self.call('uninstall', 'work'), 2)
        self.assertEqual(self.call('uninstall', 'work', '--force'), 0)
        self.assertFalse(wrapper.is_symlink())
        self.assertTrue(outside.is_file())

    def test_missing_source_keeps_default_mapping_current_after_config_changes(self):
        self.call('new', 'source')
        self.call('new', 'follower', '--share', 'source')
        self.call('new', 'indirect', '--share', 'follower')
        custom = self.base / 'custom-sqlite'
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        for name in ('source', 'follower', 'indirect'):
            self.assertEqual(self.environment(name)['CODEX_SQLITE_HOME'], str(custom))
        self.assertEqual(self.call('remove', 'source'), 0)
        for name in ('follower', 'indirect'):
            self.assertEqual(self.environment(name)['CODEX_SQLITE_HOME'], str(custom))
        self.assertEqual(self.call('new', 'source', '--isolated'), 0)
        for name in ('follower', 'indirect'):
            self.assertEqual(self.environment(name)['CODEX_SQLITE_HOME'], str(custom))
        newer = self.base / 'newer-sqlite'
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(newer)) + '\n')
        self.assertEqual(self.environment('follower')['CODEX_SQLITE_HOME'], str(newer))
        self.assertEqual(self.call('new', 'follower', '--force'), 0)
        self.assertEqual(self.environment('follower')['CODEX_SQLITE_HOME'], str(newer))

    def test_missing_source_keeps_default_mapping_current_after_environment_changes(self):
        with patch.dict(os.environ, {'CODEX_SQLITE_HOME': str(self.base / 'first-sqlite')}):
            self.call('new', 'source')
            self.call('new', 'follower', '--share', 'source')
        with patch.dict(os.environ, {'CODEX_SQLITE_HOME': str(self.base / 'second-sqlite')}):
            self.assertEqual(self.call('remove', 'source'), 0)
            self.assertEqual(self.environment('follower')['CODEX_SQLITE_HOME'], str(self.base / 'second-sqlite'))
