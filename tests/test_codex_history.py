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

from agent_profile_manager import manager, codex_history


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
            with closing(codex_history.connect(Path(envs[name]['CODEX_SQLITE_HOME']) / 'state_5.sqlite')) as db:
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

    def test_prepare_preserves_originals_and_merges_newer_threads(self):
        home, profile = self.legacy()
        originals = {p: p.read_bytes() for directory in (home, self.default) for p in directory.glob('*.sqlite')}
        output = self.base / 'prepared'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 0)
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), profile)
        self.check_merged(output / 'prepared')
        self.assertEqual(json.loads((output / 'report.json').read_text())['source_only_threads'], 1)
        self.assertEqual((output.stat().st_mode & 0o777), 0o700)
        for path in output.rglob('*.sqlite'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def check_merged(self, directory):
        with closing(codex_history.connect(directory / 'state_5.sqlite')) as db:
            self.assertEqual(dict(db.execute('SELECT id, updated_at FROM threads')), {'common': 2, 'source-only': 2, 'target-only': 1, 'target-newer': 3, 'tie': 2})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM thread_attachments').fetchone()[0], 5)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        with closing(codex_history.connect(directory / 'thread_history_1.sqlite')) as db:
            self.assertEqual(dict(db.execute('SELECT thread_id, item_json FROM thread_items')), {'common': '2', 'source-only': '2', 'target-only': '1', 'target-newer': '3', 'tie': '2'})

    def test_apply_requires_stopped_clients_and_preserves_login_metadata(self):
        home, original = self.legacy()
        self.assertEqual(self.call('migrate-history', 'work', '--apply'), 2)
        with patch.object(codex_history, 'check_stopped') as stopped:
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        self.assertEqual(stopped.call_count, 2)
        self.check_merged(self.default)
        after = manager.load_profile(self.root, 'work', self.default)
        self.assertEqual(after, {**original, 'sqlite_home': str(self.default)})
        self.assertEqual((home / 'auth.json').read_text(), 'dummy-private-login')
        self.assertEqual(manager.profile_environment('work', self.root, self.default, base={})['CODEX_SQLITE_HOME'], str(self.default))

    def test_schema_mismatch_and_open_clients_are_rejected(self):
        home, original = self.legacy()
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as db:
            db.execute('ALTER TABLE threads ADD COLUMN new_column TEXT')
        self.assertEqual(self.call('migrate-history', 'work'), 2)
        with patch.object(codex_history, 'check_stopped', side_effect=ValueError('clients open')):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)

    def test_apply_metadata_failure_rolls_back_databases(self):
        home, original = self.legacy()
        with closing(codex_history.connect(self.default / 'state_5.sqlite')) as db:
            before = db.execute('SELECT * FROM threads ORDER BY id').fetchall()
        with patch.object(codex_history, 'check_stopped'), patch.object(manager, 'save_profile', side_effect=ValueError('simulated metadata failure')):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 2)
        with closing(codex_history.connect(self.default / 'state_5.sqlite')) as db:
            self.assertEqual(db.execute('SELECT * FROM threads ORDER BY id').fetchall(), before)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)

    def test_partial_metadata_write_is_also_rolled_back(self):
        home, original = self.legacy()
        real_save = manager.save_profile

        def failing_save(root, payload):
            real_save(root, payload)
            if 'sqlite_home' in payload:
                raise ValueError('failure after replacing metadata')

        with patch.object(codex_history, 'check_stopped'), patch.object(manager, 'save_profile', side_effect=failing_save):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)
        with closing(codex_history.connect(self.default / 'state_5.sqlite')) as db:
            self.assertNotIn('source-only', {row[0] for row in db.execute('SELECT id FROM threads')})

    def test_missing_destination_goals_are_seeded_without_overwriting_existing_state(self):
        home, original = self.legacy()
        with closing(sqlite3.connect(home / 'goals_1.sqlite')) as db:
            db.execute('CREATE TABLE goals (id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO goals VALUES ('source-goal')")
            db.commit()
        output = self.base / 'goals-preview'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 0)
        with closing(codex_history.connect(output / 'prepared' / 'goals_1.sqlite')) as db:
            self.assertEqual(db.execute('SELECT id FROM goals').fetchone()[0], 'source-goal')
        with closing(sqlite3.connect(self.default / 'goals_1.sqlite')) as db:
            db.execute('CREATE TABLE goals (id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO goals VALUES ('target-goal')")
            db.commit()
        with patch.object(codex_history, 'check_stopped'):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        with closing(codex_history.connect(self.default / 'goals_1.sqlite')) as db:
            self.assertEqual(db.execute('SELECT id FROM goals').fetchall(), [('target-goal',)])

    def test_missing_source_history_and_unknown_versions_are_rejected(self):
        home, original = self.legacy()
        (home / 'thread_history_1.sqlite').unlink()
        self.assertEqual(self.call('migrate-history', 'work'), 2)
        with closing(sqlite3.connect(home / 'state_6.sqlite')) as db:
            db.execute('CREATE TABLE placeholder (id TEXT)')
        self.assertEqual(self.call('migrate-history', 'work'), 2)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)

    def test_migration_normalizes_rollout_aliases_without_touching_originals(self):
        home, original = self.legacy()
        for directory in (home, self.default):
            with closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
                db.execute('ALTER TABLE threads ADD COLUMN rollout_path TEXT')
                db.execute('UPDATE threads SET rollout_path=?', (str(directory / 'sessions' / 'dummy.jsonl'),))
                db.commit()
        output = self.base / 'paths-preview'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 0)
        with closing(codex_history.connect(output / 'prepared' / 'state_5.sqlite')) as db:
            paths = {row[0] for row in db.execute('SELECT rollout_path FROM threads')}
            self.assertEqual(paths, {str(self.default / 'sessions' / 'dummy.jsonl')})
        with closing(codex_history.connect(home / 'state_5.sqlite')) as db:
            self.assertEqual(db.execute('SELECT rollout_path FROM threads LIMIT 1').fetchone()[0], str(home / 'sessions' / 'dummy.jsonl'))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux open-file guard')
    def test_open_file_guard_detects_another_process(self):
        self.database(self.default, [('dummy', 1)])
        process = subprocess.Popen(
            [sys.executable, '-c', 'import sqlite3, sys; db=sqlite3.connect(sys.argv[1]); print("ready", flush=True); input()', str(self.default / 'state_5.sqlite')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertRaises(ValueError):
                codex_history.check_stopped([self.default])
        finally:
            process.communicate('\n', timeout=5)

    def test_legacy_repair_cannot_overwrite_a_private_index(self):
        home, original = self.legacy()
        before = (home / 'state_5.sqlite').read_bytes()
        self.assertEqual(self.call('repair', 'work', '--force'), 2)
        self.assertEqual((home / 'state_5.sqlite').read_bytes(), before)

    def test_all_item_keys_survive_even_when_thread_timestamp_favors_other_copy(self):
        home, _ = self.legacy()
        for directory, label in ((home, 'source-only-item'), (self.default, 'destination-only-item')):
            with closing(sqlite3.connect(directory / 'thread_history_1.sqlite')) as db:
                db.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?)', ('common', label, 'payload', 1))
                db.commit()
        output = self.base / 'all-records'
        self.assertEqual(self.call('migrate-history', 'work', '--output', str(output)), 0)
        with closing(codex_history.connect(output / 'prepared' / 'thread_history_1.sqlite')) as db:
            keys = {row[0] for row in db.execute("SELECT item_id FROM thread_items WHERE thread_id='common'")}
            self.assertEqual(keys, {'item', 'source-only-item', 'destination-only-item'})

    def test_ambiguous_item_conflict_stops_before_apply(self):
        home, original = self.legacy()
        with closing(sqlite3.connect(home / 'thread_history_1.sqlite')) as db:
            db.execute("UPDATE thread_items SET updated_at_ordinal=1, item_json='conflicting' WHERE thread_id='common'")
            db.commit()
        before = (self.default / 'state_5.sqlite').read_bytes()
        with patch.object(codex_history, 'check_stopped'):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 2)
        self.assertEqual((self.default / 'state_5.sqlite').read_bytes(), before)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default), original)

    def test_projection_metadata_enrichment_and_cursor_merge(self):
        self.assertEqual(
            codex_history.merge_record('thread_items', ['item_json', 'started_at_ms', 'completed_at_ms'], ('payload', None, 5), ('payload', 1, None), False),
            ('payload', 1, 5),
        )
        fields = ['thread_id', 'next_rollout_byte_offset', 'next_rollout_ordinal']
        self.assertEqual(codex_history.merge_record('thread_history_projection_state', fields, ('id', 100, 10), ('id', 200, 20), True), ('id', 100, 10))
        with self.assertRaises(ValueError):
            codex_history.merge_record('thread_history_projection_state', fields, ('id', 100, 20), ('id', 200, 10), True)

    def test_stale_old_temporary_filename_cannot_break_publication(self):
        self.legacy()
        leftover = self.default / 'thread_history_1.sqlite.pm-migration'
        leftover.write_text('leave this unrelated file alone')
        with patch.object(codex_history, 'check_stopped'):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        self.check_merged(self.default)
        self.assertEqual(leftover.read_text(), 'leave this unrelated file alone')

    def test_failed_restore_does_not_skip_other_database_restores(self):
        home, original = self.legacy()
        with closing(codex_history.connect(self.default / 'state_5.sqlite')) as db:
            before = db.execute('SELECT * FROM threads ORDER BY id').fetchall()
        real_publish = codex_history.publish
        restored = []

        def failing_publish(source, target):
            if source.parent.name == 'target':
                restored.append(target.name)
                if target.name == 'thread_history_1.sqlite':
                    raise OSError('simulated failure restoring second database')
            if source.parent.name == 'prepared' and target.name == 'thread_history_1.sqlite':
                raise OSError('simulated second publication failure')
            real_publish(source, target)

        with patch.object(codex_history, 'check_stopped'), patch.object(codex_history, 'publish', side_effect=failing_publish):
            with redirect_stderr(StringIO()) as stderr:
                result = manager.main([*self.common, 'migrate-history', 'work', '--apply', '--clients-stopped'])
        self.assertEqual(result, 2)
        self.assertIn('manual recovery', stderr.getvalue())
        self.assertEqual(restored, ['thread_history_1.sqlite', 'state_5.sqlite'])
        with closing(codex_history.connect(self.default / 'state_5.sqlite')) as db:
            self.assertEqual(db.execute('SELECT * FROM threads ORDER BY id').fetchall(), before)

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

    def test_migration_uses_custom_sqlite_target_and_original_session_store(self):
        home, _ = self.legacy()
        custom = self.base / 'custom-db'
        custom.mkdir()
        for name in codex_history.DATABASES:
            shutil.copy2(self.default / name, custom / name)
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        before = (self.default / 'state_5.sqlite').read_bytes()
        with patch.object(codex_history, 'check_stopped'):
            self.assertEqual(self.call('migrate-history', 'work', '--apply', '--clients-stopped'), 0)
        self.check_merged(custom)
        self.assertEqual((self.default / 'state_5.sqlite').read_bytes(), before)
        self.assertEqual(manager.load_profile(self.root, 'work', self.default)['sqlite_home'], str(custom))
