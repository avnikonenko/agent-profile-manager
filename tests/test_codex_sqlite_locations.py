from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
import builtins
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_profile_manager import codex_history, manager


class SqliteLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.default = self.base / 'manager', self.base / 'default'
        self.default.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]

    def call(self, *arguments):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return manager.main([*self.common, *arguments])

    def database(self, directory):
        directory.mkdir(exist_ok=True)
        with closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, updated_at INTEGER, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('dummy', 1, str(directory / 'dummy.jsonl')))
            db.commit()

    def legacy(self, source):
        self.assertEqual(self.call('new', 'work'), 0)
        profile = manager.load_profile(self.root, 'work', self.default)
        profile.pop('sqlite_home')
        manager.save_profile(self.root, profile)
        home = Path(profile['home'])
        (home / 'config.toml').write_text('"sqlite_home" = ' + json.dumps(str(source)) + '\n')
        self.database(source)
        return home, profile

    def test_legacy_external_database_is_detected_instead_of_silently_redirected(self):
        source = self.base / 'external-source'
        home, original = self.legacy(source)
        with self.assertRaisesRegex(ValueError, 'migration'):
            manager.profile_environment('work', self.root, self.default, base={})
        self.assertEqual(self.call('run', 'work'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)

    def test_migration_reads_external_source_and_checks_it_for_open_clients(self):
        source = self.base / 'external-source'
        home, original = self.legacy(source)
        output = self.base / 'preview'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 0)
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(report['source'], str(source))
        self.assertTrue((output / 'source' / 'state_5.sqlite').exists())
        self.assertTrue((output / 'legacy-home').is_dir())
        with patch.object(codex_history, 'check_stopped') as stopped:
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        self.assertEqual(stopped.call_count, 2)
        for arguments in stopped.call_args_list:
            self.assertIn(source, arguments.args[0])
            self.assertIn(home, arguments.args[0])
        with closing(sqlite3.connect(self.default / 'state_5.sqlite')) as db:
            self.assertEqual(db.execute('SELECT id FROM threads').fetchall(), [('dummy',)])
        self.assertEqual(manager.profile_environment('work', self.root, self.default, base={})['CODEX_SQLITE_HOME'], str(self.default))

    def test_legacy_database_already_at_shared_target_can_be_adopted(self):
        home, original = self.legacy(self.default)
        with patch.object(codex_history, 'check_stopped'):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default)['sqlite_home'], str(self.default))

    def test_quoted_top_level_sqlite_key_is_resolved(self):
        custom = self.base / 'custom'
        (self.default / 'config.toml').write_text('"sqlite_home" = ' + json.dumps(str(custom)) + '\n')
        self.assertEqual(manager.default_sqlite_home(self.default, base={}), custom)

    def test_quoted_keys_in_selected_profile_configuration_are_rejected(self):
        for setting in ('"sqlite_home"', "'sqlite_home'", '"\\u0073qlite_home"'):
            with self.subTest(setting=setting):
                (self.default / 'config.toml').write_text('profile = "work"\n')
                (self.default / 'work.config.toml').write_text(setting + ' = "/different"\n')
                with self.assertRaisesRegex(ValueError, 'config-profile'):
                    manager.default_sqlite_home(self.default, base={})
        (self.default / 'work.config.toml').unlink()
        (self.default / 'config.toml').write_text('profile = "work"\n[profiles.work]\n"sqlite_home" = "/different"\n')
        with self.assertRaisesRegex(ValueError, 'config profile'):
            manager.default_sqlite_home(self.default, base={})

    def test_python310_cannot_silently_ignore_any_nonempty_toml(self):
        real_import = builtins.__import__

        def no_tomllib(name, *arguments, **keywords):
            if name == 'tomllib':
                raise ImportError('simulated Python 3.10')
            return real_import(name, *arguments, **keywords)

        for text in ('"sqlite_home" = "/custom"\n', "'sqlite_home' = '/custom'\n", '"\\u0073qlite_home" = "/custom"\n', 'profile="work"\n'):
            with self.subTest(text=text):
                (self.default / 'config.toml').write_text(text)
                with patch('builtins.__import__', side_effect=no_tomllib):
                    with self.assertRaisesRegex(ValueError, 'Python 3.11'):
                        manager.default_sqlite_home(self.default, base={})
        (self.default / 'config.toml').unlink()
        with patch('builtins.__import__', side_effect=no_tomllib):
            self.assertEqual(manager.default_sqlite_home(self.default, base={}), self.default)

    def test_ambiguous_legacy_profile_override_fails_before_backup_or_redirect(self):
        source = self.base / 'external'
        home, original = self.legacy(source)
        (home / 'config.toml').write_text('profile="work"\n[profiles.work]\n"sqlite_home"=' + json.dumps(str(source)) + '\n')
        output = self.base / 'preview'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 2)
        self.assertFalse(output.exists())
        with self.assertRaises(ValueError):
            manager.profile_environment('work', self.root, self.default, base={})
