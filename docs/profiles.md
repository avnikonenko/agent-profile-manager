# Profile operations

Use `pm codex COMMAND` or `pm claude COMMAND`; each has its own profiles.
`default` is reserved. Global options go before the command; agent arguments
follow `--`.

| Operation | Codex | Claude |
| --- | --- | --- |
| Create | `pm codex new work` | `pm claude new work` |
| List | `pm codex list` | `pm claude list` |
| Login again | `pm codex run work -- login` | `pm claude login work` |
| Launch | `pm codex run work` | `pm claude run work` |
| Resume | `pm codex run work -- resume` | `pm claude run work -- --resume` |
| Rename | `pm codex rename work office` | `pm claude rename work office` |
| Home / store | `pm codex path work [--store]` | `pm claude path work [--store]` |
| Diagnose | `pm codex doctor work` | `pm claude doctor work` |
| Shortcut | `pm codex install work --wrapper codex-work` | `pm claude install work --wrapper claude-work` |
| Uninstall | `pm codex uninstall work` | `pm claude uninstall work` |
| Remove | `pm codex remove work` | `pm claude remove work` |

For custom locations: `pm codex --manager-root /path/to/manager --default-home /path/to/codex list`.
Square brackets in the table mean an optional argument; omit the brackets.

## Sharing

`new NAME` shares with `default`; use `--isolated` or `--share PROFILE` instead.
These options do not merge existing histories. `new NAME --force` preserves an
existing profile's setup when no sharing option is supplied. Names, including
`isolated`, are distinct from sharing modes; reusing a source name cannot redirect
its followers.

- **Codex:** shares sessions and live SQLite indexes, goals, and memory.
- **Claude:** shares `projects/` sessions and auto memory. A missing default
  projects directory is created privately; another source must already exist.

Credentials/configuration stay separate. Stop working on a conversation before
resuming it under another account; sharing does not release writer locks.
`repair NAME` checks Codex's SQLite location or repairs a missing Claude projects
link. Claude homes stay stable across renames for Keychain compatibility.

Codex resolves default SQLite storage from top-level `sqlite_home` in default's
config, then `CODEX_SQLITE_HOME`, then the default home. Managed launches enforce
the resolved directory. Use absolute config paths; config-profile SQLite overrides
are refused. Nonempty TOML requires Python 3.11+; Python 3.10 refuses to guess.
See [official location settings](https://learn.chatgpt.com/docs/config-file/environment-variables#core-locations).

## Migrating older Codex profiles

Older profiles copied indexes once, causing history lists to diverge. Migration
is a one-time step; new profiles share SQLite immediately. For an existing
`codex2` configured to share with default:

```bash
# Preview only; output directory must not exist.
pm codex migrate-history codex2 --output "$HOME/codex-history-preview"

# Close ALL Codex apps, CLIs, servers, and jobs on EVERY host first.
pm codex migrate-history codex2 --output "$HOME/codex-history-merged" --apply --clients-stopped
```

Keep clients stopped until completion. Apply takes fresh backups into a new
output directory; a running-client preview is not an apply-ready snapshot.
Reopen both profiles and verify listing/resume before deleting backups. Update
old shortcuts to use this package; see [compatibility](security.md).

Migration backs up source/destination databases, metadata, and logs, writes a
`report.json`, and preserves conversation keys from both copies. Newer thread
metadata wins (destination on ties); per-record ordinals resolve item updates.
Complementary null fields can combine; ambiguous conflicts stop migration.
Earlier consistent projection cursors are retained, and rollout paths are normalized.

Existing destination goals/memory are retained; source copies are backed up,
**not merged**. Missing destination databases are seeded from source. External
legacy databases are resolved from source configuration/environment; home-local
shadow copies are also saved under `legacy-home/`. Migration can adopt a legacy
profile already using the shared SQLite directory.

Limits: Linux apply only; matching supported `state_5.sqlite` and
`thread_history_1.sqlite` schemas required. Unknown schemas/versions are refused.
Open-file checks exclude reported protected session services; they cannot detect
remote-host clients or prevent new clients starting. Backups can contain sensitive
data. Publishing failures trigger rollback attempts, but crashes or failed restores
may require manual recovery: publication across files is not crash-atomic.

## Rename and removal

Codex rename requires stopped clients when indexes exist. It backs up indexes,
normalizes old-home rollout paths, and attempts rollback on failure. It refuses
to move a home containing an explicit SQLite location or another profile's data.
Keep printed index backups until resume is verified.

Removal moves profile data to trash and retains the conversation store by default.
`--remove-store` also removes an eligible store; `--purge` deletes permanently.
Use `--uninstall` to remove a registered shortcut. Default data, external/symlinked
stores, and directories used by other profiles (including SQLite-only users) are
protected. Untracked Codex wrappers must match recognized manager contents.

## Encrypted transfers

```bash
pm codex export work --output work.codex-profile
pm codex import work.codex-profile --name transferred --isolated
pm claude export work --output work.claude-profile
pm claude import work.claude-profile --name transferred
```

Enter a passphrase when prompted. Bundles contain usable file-backed credentials
and configuration; keep them private. Codex import shares with default unless
`--isolated` is given; new Claude imports are always private. Default Claude
imports are refused. Keychain-only credentials require login on the destination;
macOS Keychain can override imported files.

Claude `--include-conversations` includes sessions/auto memory. Codex v1 bundles
support legacy rollout files only and refuse SQLite-backed history or unknown
versions. A login-only export is **not a conversation backup**.

`import --force` updates login/configuration and preserves the existing store;
Claude backs up login data and skips bundled history. Claude preflights predictable
`--install` failures, but a concurrent filesystem change can still fail installation
after import. See [security](security.md).
