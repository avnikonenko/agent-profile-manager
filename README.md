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
pm --help
```

`agent-profile-manager` is the full command name; `pm` is its short form.
From a source checkout, use `./agent-profile-manager` after installing the
dependency, or run `python3 -m agent_profile_manager`.

## Three Codex accounts

Keep your original `default` account and add two more that share its conversation history:

```bash
# Create profiles
pm codex new work
pm codex new research

# Sign in to the intended account for each profile
pm codex run work -- login
pm codex run research -- login

# Launch one account
pm codex run default
pm codex run work
pm codex run research
```

Run `pm codex list` to see profiles. Repeat `run NAME -- login` to sign in again.
New profiles share history with `default` for both providers. Use `--isolated`
when creating a profile to keep its history private.

## Three Claude accounts

Claude profiles are independent of Codex profiles, so you can reuse names:

```bash
pm claude new work
pm claude new research

pm claude login work
pm claude login research

pm claude run default
pm claude run work
pm claude run research
```

Run `pm claude list` to see profiles. Repeat `login NAME` to sign in again.
New Claude profiles share project history and auto memory with `default`.

## Shared or private history

History is shared with `default` unless you request isolation:

```bash
pm codex new work                     # Shared history (default)
pm claude new work                    # Shared history and auto memory (default)
pm codex new private-work --isolated  # Private history
pm claude new private-work --isolated # Private history and memory
```

Use `--share work` to share another profile instead. Logins stay separate in
every mode. These options apply when creating profiles; existing histories are not merged
or moved.

## Shortcuts

```bash
pm codex install work --wrapper codex-work
pm claude install work --wrapper claude-work

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
