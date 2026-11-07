from contextlib import closing, redirect_stdout, redirect_stderr
from io import StringIO
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from agent_profile_manager import manager, codex_history


class RenameHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.default = self.base / 'manager', self.base / 'default'
        self.default.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]

    def call(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return manager.main([*self.common, *args])

    def make_history(self, shared=False, legacy=False):
        self.assertEqual(self.call('new', 'work', *([] if shared else ['--isolated'])), 0)
        profile = manager.load_profile(self.root, 'work', self.default)
        home, store = Path(profile['home']), Path(profile['conversation_store'])
        database_home = home if legacy else store
        if legacy:
            profile.pop('sqlite_home')
            manager.save_profile(self.root, profile)
        rollout = home / 'sessions' / 'example.jsonl'
        rollout.write_text('dummy conversation')
        db = database_home / 'state_5.sqlite'
        with closing(sqlite3.connect(db)) as connection:
            connection.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)')
            connection.execute('INSERT INTO threads VALUES (?, ?)', ('dummy', str(rollout)))
            connection.commit()
        return profile, db, store

    def test_rename_keeps_shared_private_and_legacy_history_resumable(self):
        for shared, legacy in ((False, False), (True, False), (False, True)):
            with self.subTest(shared=shared, legacy=legacy):
                # Each case gets independent temporary storage.
                with tempfile.TemporaryDirectory() as temporary:
                    previous = self.root, self.default, self.common
                    self.root = Path(temporary) / 'manager'
                    self.default = Path(temporary) / 'default'
                    self.default.mkdir()
                    self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]
                    original, db, store = self.make_history(shared, legacy)
                    with patch.object(codex_history, 'check_stopped') as check:
                        self.assertEqual(self.call('rename', 'work', 'office'), 0)
                    self.assertEqual(check.call_count, 2)
                    renamed = manager.load_profile(self.root, 'office', self.default)
                    if legacy:
                        db = Path(renamed['home']) / 'state_5.sqlite'
                    with closing(sqlite3.connect(db)) as connection:
                        path = Path(connection.execute('SELECT rollout_path FROM threads').fetchone()[0])
                    self.assertEqual(path, store / 'sessions' / 'example.jsonl')
                    self.assertEqual(path.read_text(), 'dummy conversation')
                    self.root, self.default, self.common = previous

    def test_active_database_refuses_rename_before_mutation(self):
        profile, db, store = self.make_history()
        before = db.read_bytes()
        with patch.object(codex_history, 'check_stopped', side_effect=ValueError('database open')):
            self.assertEqual(self.call('rename', 'work', 'office'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), profile)
        self.assertEqual(db.read_bytes(), before)
        self.assertFalse((self.root / 'profiles' / 'office').exists())

    def test_metadata_failure_restores_home_index_and_dependents(self):
        profile, db, store = self.make_history()
        self.assertEqual(self.call('new', 'client', '--share', 'work'), 0)
        client = manager.load_profile(self.root, 'client', self.default)
        real_save = manager.save_profile

        def fail_new_name(root, payload):
            if payload['name'] == 'office':
                raise ValueError('simulated save failure')
            real_save(root, payload)

        with patch.object(codex_history, 'check_stopped'), patch.object(manager, 'save_profile', side_effect=fail_new_name):
            self.assertEqual(self.call('rename', 'work', 'office'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), profile)
        self.assertEqual(manager.load_profile(self.root, 'client', self.default), client)
        with closing(sqlite3.connect(db)) as connection:
            path = Path(connection.execute('SELECT rollout_path FROM threads').fetchone()[0])
        self.assertEqual(path, Path(profile['home']) / 'sessions' / 'example.jsonl')
        self.assertTrue(path.is_file())

    def test_rename_refuses_to_move_a_database_referenced_by_default(self):
        profile, db, store = self.make_history()
        home = Path(profile['home'])
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(home)) + '\n')
        self.assertEqual(self.call('rename', 'work', 'office'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), profile)
        self.assertFalse((self.root / 'profiles' / 'office').exists())

    def test_rename_refuses_an_explicit_sqlite_home_inside_its_moving_home(self):
        profile, db, store = self.make_history()
        profile['sqlite_home'] = profile['home']
        manager.save_profile(self.root, profile)
        self.assertEqual(self.call('rename', 'work', 'office'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), profile)

    def test_rename_normalizes_external_legacy_source_index(self):
        profile, db, store = self.make_history(legacy=True)
        external = self.base / 'legacy-external-db'
        external.mkdir()
        database = external / db.name
        db.rename(database)
        (Path(profile['home']) / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(external)) + '\n')
        with patch.object(codex_history, 'check_stopped') as guard:
            self.assertEqual(self.call('rename', 'work', 'office'), 0)
        self.assertIn(external, guard.call_args.args[0])
        with closing(sqlite3.connect(database)) as connection:
            path = Path(connection.execute('SELECT rollout_path FROM threads').fetchone()[0])
        self.assertEqual(path, store / 'sessions' / 'example.jsonl')
        self.assertTrue(path.is_file())
