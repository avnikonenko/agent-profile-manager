from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_profile_manager import claude, claude_profiles, cli, manager


class SharedHistoryTests(unittest.TestCase):
    def test_share_history_links_default_without_copying_login(self) -> None:
        for provider in ('codex', 'claude'):
            with self.subTest(provider=provider), ExitStack() as stack:
                base = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                stack.enter_context(patch.dict(os.environ, {'HOME': str(base)}, clear=True))
                stack.enter_context(redirect_stdout(io.StringIO()))
                root, default = base / 'manager', base / 'default'
                history_dir = 'sessions' if provider == 'codex' else 'projects'
                history = default / history_dir
                history.mkdir(parents=True)
                conversation = history / 'example.jsonl'
                conversation.write_text('old conversation\n')
                credential = 'auth.json' if provider == 'codex' else '.credentials.json'
                (default / credential).write_text('dummy-login-not-for-copying')
                homes = []
                for name in ('work', 'research'):
                    self.assertEqual(cli.main([
                        provider, '--manager-root', str(root), '--default-home', str(default),
                        'new', name, '--share-history',
                    ]), 0)
                    backend = manager if provider == 'codex' else claude_profiles
                    profile = backend.load_profile(root, name, default)
                    self.assertEqual(profile['share'], 'default')
                    home = Path(profile['home'])
                    homes.append(home)
                    self.assertEqual((home / history_dir).resolve(), history)
                    self.assertFalse((home / credential).exists())
                (homes[0] / history_dir / 'example.jsonl').write_text('shared update\n')
                self.assertEqual((homes[1] / history_dir / 'example.jsonl').read_text(), 'shared update\n')
                self.assertEqual((default / credential).read_text(), 'dummy-login-not-for-copying')

    def test_sharing_options_cannot_be_combined(self) -> None:
        for provider, backend in (('codex', manager), ('claude', claude)):
            for extra in (['--isolated'], ['--share', 'work']):
                with self.subTest(provider=provider, extra=extra), redirect_stderr(io.StringIO()):
                    parser = backend.build_parser()
                    with self.assertRaises(SystemExit) as raised:
                        parser.parse_args(['new', 'research', '--share-history', *extra])
                    self.assertEqual(raised.exception.code, 2)

    def test_private_default_and_explicit_share_remain_available(self) -> None:
        for backend in (manager, claude):
            parser = backend.build_parser()
            self.assertIsNone(parser.parse_args(['new', 'work']).share)
            self.assertEqual(parser.parse_args(['new', 'work', '--share', 'research']).share, 'research')
            self.assertEqual(parser.parse_args(['new', 'work', '--share-history']).share, 'default')


if __name__ == '__main__':
    unittest.main()
