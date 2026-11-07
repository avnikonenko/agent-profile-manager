# Agent Profile Manager

Manage multiple **Codex and Claude Code accounts** with separate logins and
shared history. `default` uses your existing account.

Based on [Codex Tools](https://github.com/hruskamiro/codex-tools) by Miroslav Hruska.
Original MIT attribution is preserved in [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Install

Linux or macOS; Python **3.11+ recommended** (required for nonempty Codex configs).
Install Codex/Claude separately, then run from this project:

```bash
pipx install .
pm --version
```

`pm` is short for `agent-profile-manager`. From a checkout, use
`./agent-profile-manager` or `python3 -m agent_profile_manager`.

## Three accounts

Keep `default` and add two profiles. Codex and Claude have separate registries.

```bash
pm codex new work
pm codex new research
pm codex run work -- login
pm codex run research -- login
pm codex run work                 # Or: default / research
```

```bash
pm claude new work
pm claude new research
pm claude login work
pm claude login research
pm claude run work                # Or: default / research
```

Use `pm codex list` or `pm claude list` to inspect profiles. Repeat the login
command to sign in again.

## History and shortcuts

New profiles share history with `default`. Use `new NAME --isolated` for private
history or `new NAME --share PROFILE` for another source. Logins stay separate.
Codex also shares SQLite goals/memory; Claude shares project sessions/auto memory.
Older Codex profiles need a [one-time database merge](docs/profiles.md#migrating-older-codex-profiles).

```bash
pm codex install work --wrapper codex-work
pm claude install work --wrapper claude-work
codex-work
claude-work
```

Shortcuts install in `~/.local/bin`; add it to `PATH`. See provider `--help` for
commands. This project manages profiles only; it does not report usage or kill jobs.

## Reference

- [Profile commands, database merge, and transfers](docs/profiles.md)
- [Security and compatibility](docs/security.md)
- [Changelog](CHANGELOG.md) · [Releases](docs/releases.md)

## Development

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests
```

Independent project; not affiliated with OpenAI, Anthropic, or the original author.
