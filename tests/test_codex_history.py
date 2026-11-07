from __future__ import annotations

from contextlib import closing, redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_profile_manager import manager


class CodexHistoryTests(unittest.TestCase):
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


    def database(self, home, threads):
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE rollout_migration_state (migration_id TEXT PRIMARY KEY, last_checked_thread_id TEXT)')
            db.execute('CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT)')
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, project_id TEXT REFERENCES projects(id))')
            db.execute('CREATE TABLE thread_attachments (id TEXT PRIMARY KEY, thread_id TEXT REFERENCES threads(id), payload TEXT)')
            db.execute("INSERT INTO projects VALUES ('project', 'project')")
            db.executemany('INSERT INTO threads VALUES (?, ?, ?)', [(name, version, 'project') for name, version in threads])
            db.executemany('INSERT INTO thread_attachments VALUES (?, ?, ?)', [(name + '-attachment', name, 'payload') for name, _ in threads])
            db.commit()
        with closing(sqlite3.connect(home / 'thread_history_1.sqlite')) as db:
            db.execute('CREATE TABLE thread_items (thread_id TEXT, item_id TEXT, item_json TEXT, updated_at_ordinal INTEGER, PRIMARY KEY(thread_id, item_id))')
            db.executemany('INSERT INTO thread_items VALUES (?, ?, ?, ?)', [(name, 'item', str(version), version) for name, version in threads])
            db.commit()


    def legacy(self):
        self.assertEqual(self.call('new', 'work'), 0)
        profile = manager.load_profile(self.root, 'work', self.default)
        profile.pop('sqlite_home')
        manager.save_profile(self.root, profile)
        home = Path(profile['home'])
        self.database(self.default, [('common', 1), ('target-only', 1), ('target-newer', 3), ('tie', 2)])
        self.database(home, [('common', 2), ('source-only', 2), ('target-newer', 1), ('tie', 2)])
        (home / 'auth.json').write_text('dummy-private-login')
        profile['wrapper'] = {'command': 'custom-work'}
        manager.save_profile(self.root, profile)
        return home, profile


    def test_shared_sqlite_stays_live_and_private_profiles_are_separate(self):
        self.assertEqual(self.call('new', 'work'), 0)
        self.assertEqual(self.call('new', 'research'), 0)
        self.assertEqual(self.call('new', 'private', '--isolated'), 0)
        self.assertEqual(self.call('new', 'shared-private', '--share', 'private'), 0)
        envs = {name: manager.profile_environment(name, self.root, self.default, base={}) for name in ('work', 'research', 'private', 'shared-private')}
        self.assertEqual(envs['work']['CODEX_SQLITE_HOME'], str(self.default))
        self.assertEqual(envs['work']['CODEX_SQLITE_HOME'], envs['research']['CODEX_SQLITE_HOME'])
        self.assertNotEqual(envs['private']['CODEX_SQLITE_HOME'], envs['work']['CODEX_SQLITE_HOME'])
        self.assertEqual(envs['private']['CODEX_SQLITE_HOME'], envs['shared-private']['CODEX_SQLITE_HOME'])
        self.database(self.default, [('written-after-creation', 1)])
        for name in ('work', 'research'):
            with closing(sqlite3.connect(Path(envs[name]['CODEX_SQLITE_HOME']) / 'state_5.sqlite')) as db:
                self.assertEqual(db.execute('SELECT id FROM threads').fetchone()[0], 'written-after-creation')
        self.assertNotEqual(envs['work']['CODEX_HOME'], envs['research']['CODEX_HOME'])


    def test_legacy_launch_requires_migration_and_default_is_unchanged(self):
        self.legacy()
        self.assertEqual(self.call('run', 'work'), 2)
        self.assertEqual(manager.profile_environment('default', self.root, self.default, base={'CODEX_SQLITE_HOME': '/custom'})['CODEX_SQLITE_HOME'], '/custom')


    def test_run_enforces_sqlite_config_and_keeps_literal_arguments(self):
        self.call('new', 'work')
        with patch.object(manager.shutil, 'which', return_value='/fake/codex'), patch.object(manager.os, 'execvpe') as execute:
            self.assertEqual(self.call('run', 'work', '--', 'resume', 'literal argument'), 127)
        executable, args, env = execute.call_args.args
        self.assertEqual(args, ['codex', '-c', 'sqlite_home=' + json.dumps(str(self.default)), 'resume', 'literal argument'])
        self.assertEqual(env['CODEX_SQLITE_HOME'], str(self.default))


    def test_run_rejects_explicit_sqlite_redirection(self):
        self.call('new', 'work')
        for arguments in (['-c', 'sqlite_home="/other"'], ['--config=sqlite_home="/other"'], ['-csqlite_home="/other"']):
            with self.subTest(arguments=arguments), patch.object(manager.os, 'execvpe') as execute:
                self.assertEqual(self.call('run', 'work', '--', *arguments), 2)
                execute.assert_not_called()


    def test_legacy_repair_cannot_overwrite_a_private_index(self):
        home, original = self.legacy()
        before = (home / 'state_5.sqlite').read_bytes()
        self.assertEqual(self.call('repair', 'work', '--force'), 2)
        self.assertEqual((home / 'state_5.sqlite').read_bytes(), before)


    def test_shared_sqlite_respects_default_environment_and_config_precedence(self):
        custom = self.base / 'custom-env'
        configured = self.base / 'custom-config'
        with patch.dict(os.environ, {'CODEX_SQLITE_HOME': str(custom)}):
            self.assertEqual(self.call('new', 'work'), 0)
            for name in ('default', 'work'):
                self.assertEqual(manager.profile_environment(name, self.root, self.default)['CODEX_SQLITE_HOME'], str(custom))
            (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(configured)) + '\n')
            for name in ('default', 'work'):
                self.assertEqual(manager.profile_environment(name, self.root, self.default)['CODEX_SQLITE_HOME'], str(configured))
