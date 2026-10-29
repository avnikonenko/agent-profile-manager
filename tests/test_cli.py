from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from agent_profile_manager import claude, cli, manager


class CliTests(unittest.TestCase):
    def test_providers_receive_literal_arguments(self) -> None:
        for provider, module in (("codex", manager), ("claude", claude)):
            with self.subTest(provider=provider), patch.object(module, "main", return_value=7) as call:
                self.assertEqual(cli.main([provider, "run", "work", "--", "literal argument"]), 7)
            call.assert_called_once_with(["run", "work", "--", "literal argument"], prog=f"apm {provider}")

    def test_no_unrelated_tools_or_implicit_default_provider(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["viewer"]), 2)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 0)

    def test_usage_is_not_part_of_profile_management(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                manager.main(["usage"])
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(claude.main(["usage"]), 2)


if __name__ == "__main__":
    unittest.main()
