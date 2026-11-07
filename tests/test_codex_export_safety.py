from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent_profile_manager import manager


class CodexExportSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.default = self.base / 'manager', self.base / 'default'
        self.default.mkdir()
        self.common = ['--manager-root', str(self.root), '--default-home', str(self.default)]
        with redirect_stdout(StringIO()):
            self.assertEqual(manager.main([*self.common, 'new', 'work']), 0)
        self.profile = manager.load_profile(self.root, 'work', self.default)
        self.home = Path(self.profile['home'])
        (self.home / 'auth.json').write_text('dummy-login')

    def state(self, directory, mode):
        with closing(sqlite3.connect(directory / 'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, history_mode TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?)', ('dummy', mode))
            db.commit()

    def test_paginated_export_fails_before_creating_archive_or_prompting(self):
        self.state(self.default, 'paginated')
        output = self.base / 'export.codex-profile'
        with redirect_stderr(StringIO()) as stderr, patch.object(manager, 'prompt_export_passphrase') as prompt:
            result = manager.main([*self.common, 'export', 'work', '--include-conversations', '--output', str(output)])
        self.assertEqual(result, 2)
        self.assertIn('cannot preserve SQLite-backed history', stderr.getvalue())
        prompt.assert_not_called()
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.*.tmp-*')))

    def test_direct_archive_api_refuses_to_claim_paginated_history_included(self):
        self.state(self.default, 'paginated')
        archive = self.base / 'plain.zip'
        with self.assertRaises(ValueError):
            manager.create_profile_archive(archive, self.profile, True)
        self.assertFalse(archive.exists())

    def test_history_items_without_state_database_are_also_protected(self):
        with closing(sqlite3.connect(self.default / 'thread_history_1.sqlite')) as db:
            db.execute('CREATE TABLE thread_items (item_json TEXT)')
            db.execute("INSERT INTO thread_items VALUES ('dummy unique content')")
            db.commit()
        with self.assertRaises(ValueError):
            manager.create_profile_archive(self.base / 'plain.zip', self.profile, True)

    def test_custom_effective_default_sqlite_path_is_checked(self):
        custom = self.base / 'custom-db'
        custom.mkdir()
        self.state(custom, 'paginated')
        (self.default / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        with redirect_stderr(StringIO()), patch.object(manager, 'prompt_export_passphrase') as prompt:
            self.assertEqual(manager.main([*self.common, 'export', 'work', '--include-conversations', '--output', str(self.base / 'bundle')]), 2)
        prompt.assert_not_called()

    def test_credentials_and_legacy_rollouts_remain_exportable(self):
        self.state(self.default, 'legacy')
        rollout = self.default / 'sessions' / 'legacy.jsonl'
        rollout.write_text('dummy file-backed history')
        legacy = self.base / 'legacy.zip'
        manager.create_profile_archive(legacy, self.profile, True)
        with zipfile.ZipFile(legacy) as archive:
            self.assertEqual(archive.read('conversations/sessions/legacy.jsonl'), b'dummy file-backed history')
        with closing(sqlite3.connect(self.default / 'state_5.sqlite')) as db:
            db.execute("UPDATE threads SET history_mode='paginated'")
            db.commit()
        credentials = self.base / 'credentials.zip'
        manager.create_profile_archive(credentials, self.profile, False)
        with zipfile.ZipFile(credentials) as archive:
            self.assertEqual(archive.read('profile/auth.json'), b'dummy-login')
            self.assertFalse(json.loads(archive.read('manifest.json'))['includes']['conversations'])

    def test_unknown_conversation_database_version_is_rejected(self):
        with closing(sqlite3.connect(self.default / 'state_6.sqlite')) as db:
            db.execute('CREATE TABLE unknown (id TEXT)')
        with self.assertRaises(ValueError):
            manager.create_profile_archive(self.base / 'future.zip', self.profile, True)

    def test_legacy_external_source_database_is_not_omitted(self):
        custom = self.base / 'legacy-external-db'
        custom.mkdir()
        self.state(custom, 'paginated')
        self.profile.pop('sqlite_home')
        manager.save_profile(self.root, self.profile)
        (self.home / 'config.toml').write_text('sqlite_home = ' + json.dumps(str(custom)) + '\n')
        with redirect_stderr(StringIO()), patch.object(manager, 'prompt_export_passphrase') as prompt:
            self.assertEqual(manager.main([*self.common, 'export', 'work', '--include-conversations', '--output', str(self.base / 'bundle')]), 2)
        prompt.assert_not_called()
