"""Command-line entry point for managing agent account profiles."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("""usage: pm {codex,claude} COMMAND [OPTIONS]

Manage multiple Codex and Claude Code accounts.

Examples:
  pm codex new work
  pm codex run work -- login
  pm codex run work
  pm claude new work
  pm claude login work
  pm claude run work

Commands: new, list, run, rename, path, doctor, repair, install,
          uninstall, export, import, remove
Claude also supports login. Use 'pm codex --help' or 'pm claude --help'
for provider-specific options.

Derived from Codex Tools by Miroslav Hruska (MIT); see NOTICE and LICENSE.
""")
        return 0
    if args[0] == "--version":
        from agent_profile_manager import __version__

        print(__version__)
        return 0
    if args[0] == "codex":
        from agent_profile_manager import manager

        return manager.main(args[1:], prog="pm codex")
    if args[0] == "claude":
        from agent_profile_manager import claude

        return claude.main(args[1:], prog="pm claude")
    print(f"error: unknown provider: {args[0]}. Choose codex or claude.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
