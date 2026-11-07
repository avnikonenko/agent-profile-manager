# Changelog

Git tags identify releases; unreleased entries describe untagged work.

## 0.2.0 — Unreleased

### Added

- Live shared Codex SQLite indexes, goals, and memory; logins remain separate.
- Offline history migration with preview, private backups, record-preserving
  merge, and stopped-client checks.
- Explicit sharing modes independent of profile names.

### Fixed

- Forced creation preserves timestamps/wrappers; rename preserves history paths.
- Source removal/name reuse cannot silently redirect followers.
- Removal protects default/shared data; uninstall verifies untracked wrappers.
- Claude import preflights installation conflicts before replacing login data.
- SQLite resolution handles external legacy databases and quoted TOML keys.

### Compatibility

- Older copied indexes need one-time migration; new profiles share immediately.
- Codex v1 exports refuse SQLite history; login-only exports remain supported.
- Nonempty Codex TOML requires Python 3.11+; config-free Python 3.10 still works.
- Indexed Codex rename requires stopped clients and retains backups.
- Migration retains destination goals/memory; source copies are backed up, not merged.

## 0.1.0 — Initial development baseline

Original package version, without a release tag: separate Codex/Claude profiles,
login, wrappers, shared/private history, lifecycle commands, and encrypted
transfers. Added `pm` shorthand; preserved Miroslav Hruska's MIT attribution.
