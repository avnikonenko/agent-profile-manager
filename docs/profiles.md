# Profile operations

Use `pm codex COMMAND` or `pm claude COMMAND`. Each provider has its own
profiles. Only `default` is reserved. Existing default configuration and login
are used in place.

| Operation | Codex example | Claude example |
| --- | --- | --- |
| Create | `pm codex new work` | `pm claude new work` |
| List | `pm codex list` | `pm claude list` |
| Sign in again | `pm codex run work -- login` | `pm claude login work` |
| Launch | `pm codex run work` | `pm claude run work` |
| Resume | `pm codex run work -- resume` | `pm claude run work -- --resume` |
| Rename | `pm codex rename work office` | `pm claude rename work office` |
| Locate home | `pm codex path work` | `pm claude path work` |
| Locate history | `pm codex path work --store` | `pm claude path work --store` |
| Diagnose | `pm codex doctor work` | `pm claude doctor work` |
| Install shortcut | `pm codex install work --wrapper codex-work` | `pm claude install work --wrapper claude-work` |
| Remove shortcut | `pm codex uninstall work` | `pm claude uninstall work` |
| Remove profile | `pm codex remove work` | `pm claude remove work` |

Use provider-specific `--help` to see options. Global options precede the
command; launch arguments follow `--`. For example:

```bash
pm claude --manager-root /path/to/manager new work
pm codex --default-home /path/to/existing/codex list
```

## Sharing conversations

Both providers' `new` commands create profiles with private histories by default. The optional
`--isolated` flag remains accepted for compatibility. Existing profiles retain
their sharing configuration. `new NAME --share-history` shares with `default`
for either provider; it is an alias for `--share default`. Use `--share PROFILE`
to choose a different source. The sharing flags and `--isolated` are mutually
exclusive. This option creates a shared profile and does not migrate or merge
existing histories.

To share Codex history, explicitly use
`pm codex new team --share work` (or `--share default`).

Explicit Claude sharing uses
`pm claude new team --share work`. It links `projects/`, sharing sessions and
auto memory while keeping credentials and account configuration separate.
The source projects directory must already exist. Stop work on a conversation
before resuming it under another account: sharing history does not release a
running conversation's writer lock or move a running session.

`repair NAME` repairs missing derived Codex state or a missing managed Claude
projects link. It is provider-specific; it is not a general credential repair.
Claude account directories stay at a stable path across renames for compatibility
with macOS Keychain identity.

## Encrypted account transfers

```bash
pm codex export work --output work.codex-profile
pm codex import work.codex-profile --name transferred --isolated

pm claude export work --output work.claude-profile
pm claude import work.claude-profile --name transferred
```

For Codex imports, keep `--isolated` as shown to select a private destination;
omitting it retains the original import behavior of sharing with `default`.
Claude imports are always private for new profiles.

Enter the passphrase when prompted. Exports contain file-backed credentials and
profile configuration. Add `--include-conversations` to export history too.
Claude history exports also include auto memory under `projects/`.

`import --force` updates an existing account's login and bundled configuration,
preserving history. Claude makes a private backup and explicitly skips bundled
history during a forced update. Default Claude imports are refused: select a
managed name instead. Keychain-only Claude credentials are not exported; sign
in again on the destination. macOS Keychain can take precedence over imported
file-backed credentials.

## Removal

Removal uses a manager trash area by default. A profile's conversation store is
retained unless `--remove-store` is explicitly selected. Shared stores in use
are protected. `--purge` permanently deletes the selected profile data.
Use `--uninstall` when removing a profile with its installed wrapper.

See [security and compatibility](security.md) before transferring accounts.
