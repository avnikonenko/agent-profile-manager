# Changelog

Changes are grouped by version. Git tags identify released commits; an
unreleased entry describes work that has not yet been tagged.

## 0.2.0 — Unreleased

### Added

- Live shared Codex SQLite history, including conversation indexes, goals, and
  memory, with separate account logins.
- Offline `pm codex migrate-history NAME` with preview, private backups,
  record-preserving merge, and stopped-client checks.
- Explicit sharing modes for Codex and Claude, independent of profile names.

### Fixed

- Forced profile creation preserves creation timestamps and registered wrappers.
- Renamed profiles retain history stores and usable indexed conversation paths.
- Reusing or removing a source name does not silently redirect followers.
- Removal protects default data, shared stores, and SQLite-only users.
- Uninstall verifies unregistered wrappers and avoids deleting unrelated files.
- Claude import checks predictable wrapper installation failures before replacing
  login data.
- SQLite location resolution handles external legacy databases and quoted TOML
  keys consistently.

### Compatibility and safety

- Older Codex profiles with copied indexes require a one-time offline migration.
  New profiles use their selected SQLite directory immediately.
- Codex v1 conversation exports refuse SQLite-backed history rather than produce
  incomplete backups. Login/configuration-only exports remain supported.
- Nonempty Codex TOML configuration requires Python 3.11 or newer for reliable
  storage resolution. Python 3.10 without configuration remains supported.
- Renaming indexed Codex profiles requires stopped clients and retains backups.
- Existing destination goals and memory are retained during migration; source
  copies are backed up rather than automatically merged.

## 0.1.0 — Initial development baseline

The original package version was 0.1.0; no release tag was created.

- Separate Codex and Claude profiles, interactive login, and launch wrappers.
- Shared history by default, with `--isolated` and `--share PROFILE` options.
- Profile rename, inspection, repair, removal, and encrypted account transfers.
- `pm` as the short command, with `agent-profile-manager` as the full command.
- MIT license and attribution to Codex Tools by Miroslav Hruska.
