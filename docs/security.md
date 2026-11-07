# Security and compatibility

- Logins/configuration are separate; default uses existing files in place.
- Claude uses official interactive login and reports conflicting authentication
  environment variables by name, without values. Verify the active account:
  managed policy and supplied arguments can affect authentication.
- Exports contain usable credentials. Keep bundles/passphrases private.
  Keychain credentials are never read; Keychain-only accounts need a new login.
- Tests use temporary profiles, dummy accounts, and fake/mocked executables;
  they do not query models or account limits.

## Existing Codex Tools profiles

Both packages use the same paths and supported bundle formats:

| Provider | Registry | Default home |
| --- | --- | --- |
| Codex | `~/.codex-manager` | `~/.codex` |
| Claude | `~/.claude-manager` | `CLAUDE_CONFIG_DIR` or `~/.claude` |

Changes are visible to both tools. Use `--manager-root` for a separate registry
and `--default-home` for another default account. Avoid concurrent mutations.

Existing shortcuts still call their original package. Remove an old shortcut
with its original tool, then reinstall it here. After SQLite migration, old
Codex Tools launchers can write a separate private index again; use this package.
Creating this source checkout does not install or upgrade either package.
