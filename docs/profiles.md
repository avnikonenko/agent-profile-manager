# Profile operations

Use `apm codex COMMAND` or `apm claude COMMAND`. Each provider has its own
profiles. Only `default` is reserved. Existing default configuration and login
are used in place.

| Operation | Codex example | Claude example |
| --- | --- | --- |
| Create | `apm codex new work --isolated` | `apm claude new work` |
| List | `apm codex list` | `apm claude list` |
| Sign in again | `apm codex run work -- login` | `apm claude login work` |
| Launch | `apm codex run work` | `apm claude run work` |
| Resume | `apm codex run work -- resume` | `apm claude run work -- --resume` |
| Rename | `apm codex rename work office` | `apm claude rename work office` |
| Locate home | `apm codex path work` | `apm claude path work` |
| Locate history | `apm codex path work --store` | `apm claude path work --store` |
| Diagnose | `apm codex doctor work` | `apm claude doctor work` |
| Install shortcut | `apm codex install work --wrapper codex-work` | `apm claude install work --wrapper claude-work` |
| Remove shortcut | `apm codex uninstall work` | `apm claude uninstall work` |
| Remove profile | `apm codex remove work` | `apm claude remove work` |

Use provider-specific `--help` to see options. Global options precede the
command; launch arguments follow `--`. For example:

```bash
apm claude --manager-root /path/to/manager new work
apm codex --default-home /path/to/existing/codex list
```

## Sharing conversations

Codex shares conversations with `default` unless `new` receives `--isolated`.
To share with another profile, use `apm codex new team --share work`.

Claude is isolated by default. Explicit sharing uses
`apm claude new team --share work`. It links `projects/`, sharing sessions and
auto memory while keeping credentials and account configuration separate.
The source projects directory must already exist.

`repair NAME` repairs missing derived Codex state or a missing managed Claude
projects link. It is provider-specific; it is not a general credential repair.
Claude account directories stay at a stable path across renames for compatibility
with macOS Keychain identity.

## Encrypted account transfers

```bash
apm codex export work --output work.codex-profile
apm codex import work.codex-profile --name transferred --isolated

apm claude export work --output work.claude-profile
apm claude import work.claude-profile --name transferred
```

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
