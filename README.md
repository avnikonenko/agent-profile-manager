# Agent Profile Manager

Manage multiple **Codex and Claude Code accounts** from one small CLI. Create a
profile, sign in to its account, and launch it by name. The original account is
called `default`; additional profiles can have any valid name.

**Based on [Codex Tools](https://github.com/hruskamiro/codex-tools) by Miroslav
Hruska.** The original MIT license and copyright are preserved in [LICENSE](LICENSE).
See [NOTICE](NOTICE) for source attribution and the origin of the Claude extensions.

## Install

Linux or macOS, Python 3.10+, and `pipx` are required. Install Codex and/or Claude
Code separately. From this project's directory:

```bash
pipx install .
apm --help
```

`agent-profile-manager` is the full command name; `apm` is its short form.
From a source checkout, use `./agent-profile-manager` after installing the
dependency, or run `python3 -m agent_profile_manager`.

## Three Codex accounts

Keep your original `default` account and add two more with private histories:

```bash
# Create profiles
apm codex new work --isolated
apm codex new research --isolated

# Sign in to the intended account for each profile
apm codex run work -- login
apm codex run research -- login

# Launch one account
apm codex run default
apm codex run work
apm codex run research
```

Run `apm codex list` to see profiles. Repeat `run NAME -- login` to sign in again.
Omitting `--isolated` shares conversations with `default`, matching the original
Codex profile manager's behavior.

## Three Claude accounts

Claude profiles are independent of Codex profiles, so you can reuse names:

```bash
apm claude new work
apm claude new research

apm claude login work
apm claude login research

apm claude run default
apm claude run work
apm claude run research
```

Run `apm claude list` to see profiles. Repeat `login NAME` to sign in again.
Claude histories are private by default.

## Shortcuts

```bash
apm codex install work --wrapper codex-work
apm claude install work --wrapper claude-work

codex-work
claude-work
```

Wrappers are installed in `~/.local/bin` by default; it must be on `PATH`.

## More

- [Profile operations, sharing, and account transfers](docs/profiles.md)
- [Security and data compatibility](docs/security.md)

This project contains profile management only. It does not include conversation
viewers, summaries, relay services, usage reporting, or process termination.
No accounts are created or logged into during installation.

Independent project; not affiliated with OpenAI, Anthropic, or the original author.

## Development

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests
```

[MIT license](LICENSE)
