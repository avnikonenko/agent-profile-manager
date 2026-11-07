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

Both providers' `new` commands share history with `default` unless you supply
`--isolated`. Existing profiles retain their configuration, including when
reused with `new NAME --force` without sharing options.

```bash
pm codex new work                     # Share default history
pm claude new work                    # Share default projects and memory
pm codex new private-work --isolated
pm claude new private-work --isolated
```

Use `--share PROFILE` to choose a different source. `--share` and `--isolated`
are mutually exclusive. These options do not migrate or merge existing histories.

Codex shares session files and a live SQLite directory, so conversation lists,
goals, and memory state are shared too. Credentials and account configuration
stay in separate homes. Managed launches set `CODEX_SQLITE_HOME` and a
`sqlite_home` CLI override to the resolved shared SQLite directory; conflicting `sqlite_home`
settings in a managed profile do not redirect that launch. Sharing with default
resolves the default user config's top-level `sqlite_home` first, then
`CODEX_SQLITE_HOME`, then the default home. Relative environment paths resolve
from the working directory. Use an absolute config path; config-profile-specific
SQLite overrides are rejected with instructions to use a top-level setting.
Storage configuration is parsed as TOML, including quoted keys. Reading any
nonempty Codex configuration requires Python 3.11 or newer; on Python 3.10
the manager refuses to guess SQLite locations from unparsed configuration.
Existing profiles and migration use the same resolver. A source name is followed
only while it still identifies the same conversation store. Removing a source
and reusing its name does not redirect existing followers. Sharing mode is
recorded separately from source names for both providers, so a profile named
`isolated` is valid.
See the
[official SQLite location settings](https://learn.chatgpt.com/docs/config-file/environment-variables#core-locations).

Claude links `projects/`, sharing
sessions and auto memory while keeping credentials and account configuration
separate. If the default Claude projects directory is missing, creation makes
an empty one privately. A different source profile must have an existing store.
Stop work on a conversation before resuming it under another account: sharing
does not release writer locks or move a running session.

`repair NAME` checks the Codex SQLite location or repairs a missing managed
Claude projects link. Codex repair no longer overwrites a copied database.
It is provider-specific; it is not a general credential repair.
Claude account directories stay at a stable path across renames for compatibility
with macOS Keychain identity.

## Migrating older Codex profiles

### Short example: merge `codex2` with default

For an existing `codex2` profile configured to share with `default`:

```bash
# Preview the merge; live databases stay unchanged.
pm codex migrate-history codex2 --output "$HOME/codex-history-preview"

# Close all Codex clients on every host, then merge with fresh backups.
pm codex migrate-history codex2 --output "$HOME/codex-history-merged" --apply --clients-stopped
```

Both output directories must be new. Keep `codex-history-merged` as your backup.
Reopen both profiles and verify that expected conversations can be resumed.
This is a one-time migration for older profiles; new profiles share the database
from creation. Logins stay separate. Existing destination goals/memory are kept;
source copies are backed up, not merged. Existing shortcuts must use Agent Profile
Manager, as explained below.

### Migration details

Older profiles shared rollout files but copied the conversation index only once.
The lists then diverged. Managed launches now refuse a legacy private database
until you migrate it; installing this code does not migrate live data.
The legacy source is resolved from that profile's configuration and inherited
`CODEX_SQLITE_HOME`, separately from the shared destination. Databases outside
the profile home are detected and backed up there; local shadow databases are
also retained under `legacy-home/`. Source config-profile-specific SQLite
overrides are refused as ambiguous: put the source location at the top level
of its `config.toml` before migration. Open-file checks cover the profile home,
source database directory, and shared destination.

If a legacy profile already uses the destination's SQLite directory, migration
still backs it up and records the binding, allowing future launches without
repeated migration warnings.

Prepare a migration without changing live databases:

```bash
pm codex migrate-history work --output /private/path/work-preview
```

The output directory must not exist. It contains private SQLite snapshots,
prepared merged conversation databases, the original profile metadata, and a
`report.json`. It can contain sensitive conversations and logs; keep it private.
Preparation while clients are active is a preview, not an apply-ready snapshot.

Close **all Codex apps, CLIs, app servers, and background jobs** using either
home, on every host. Then create a fresh migration and apply it:

```bash
pm codex migrate-history work --output /private/path/work-migration --apply --clients-stopped
```

Apply checks for open SQLite files on Linux and refuses schema differences.
It reports excluded protected session services (systemd/PAM, SSH agent, and
the speech service); these are not database clients. Other uninspectable
processes cause refusal.
Keep clients stopped until it finishes. The checks cannot prevent another
client starting during migration or detect clients on another host.

Conversation IDs are merged from both indexes; newer thread timestamps select
thread metadata, with destination winning ties. Turns, items and related records
are merged by their individual primary keys, preserving every key from either
database. Per-event ordinals select newer versions of the same record; equal
versions can combine complementary null fields. Ambiguous conflicting content
stops migration before publication. Projection cursors retain the earlier
consistent position so replay cannot skip missing data. Rollout paths through the source profile are rewritten to the shared
store, so migrated entries do not depend on that profile alias. Schemas must
match: upgrade both profiles using the same Codex version before migration if
necessary. Original databases in both homes are backed up;
source files remain in place. Destination goals and memory remain authoritative;
source goals/memory are backed up but **not automatically merged**. If a
non-conversation database does not exist at the destination, the source copy
is preserved there (useful when migrating a private store). Review them
separately if you need their contents. Diagnostic logs are also backed up.

Only state_5.sqlite and thread_history_1.sqlite conversation schemas are
supported. Unknown conversation tables or database versions stop migration.
Do not delete backups until you have verified both profiles can list and resume
the expected conversations. Apply uses backups to roll back database changes
if publishing fails. Each publication and restore uses a unique temporary
directory, and all restores are attempted even if one fails. Any incomplete
rollback reports the affected files and backup location. An interrupted process
may require manual recovery from
those backups; publication across multiple files is not crash-atomic.

Existing shortcuts may still call Codex Tools. After migration, reinstall those
shortcuts using Agent Profile Manager as described in [security.md](security.md).
Using the old launcher again would keep writing the private index.

## Renaming Codex profiles with indexed history

Renaming keeps session-store locations stable. When a SQLite index exists,
close Codex clients using that index before renaming. The command checks for
open database files, refuses to move a home containing another profile's
SQLite directory (or an explicit SQLite home inside itself), backs up the index,
rewrites paths through the old profile
home to the shared session store, and then moves the home. If publication or
metadata saving fails, it attempts to restore the original home, indexes,
dependents, and wrapper. Keep the printed index backups until resume is verified.

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
profile configuration. For Claude, add `--include-conversations` to export sessions and auto memory
under `projects/`. Codex v1 bundles carry legacy rollout files only. Codex
`--include-conversations` now refuses SQLite-backed/paginated history and
unknown database versions rather than creating an incomplete backup. Login-only
Codex exports remain supported. Keep the original SQLite databases; a login-only
bundle is not a conversation backup.

`import --force` updates an existing account's login and bundled configuration,
preserving history. Claude makes a private backup and explicitly skips bundled
history during a forced update. With `--install`, wrapper collisions and other
predictable installation failures are checked before publishing imported login
data. Installation checks run again at publication; concurrent filesystem
changes can still cause an install failure after import. Default Claude imports are refused: select a
managed name instead. Keychain-only Claude credentials are not exported; sign
in again on the destination. macOS Keychain can take precedence over imported
file-backed credentials.

## Removal

Removal uses a manager trash area by default. A profile's conversation store is
retained unless `--remove-store` is explicitly selected. Stores and profile directories used by other profiles
(including default and SQLite-only users) are protected. Store deletion is
restricted to direct managed-store directories; default/external paths and
symlink roots are refused before wrappers or metadata are changed. Claude also
protects resolved default home/projects aliases, including symlinked project
stores. Unregistered Codex shortcuts are removed only when their contents match
a recognized manager wrapper, even when an explicit command name is supplied. `--purge` permanently deletes the selected profile data.
Use `--uninstall` when removing a profile with its installed wrapper.

See [security and compatibility](security.md) before transferring accounts.
