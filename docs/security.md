# Security and compatibility

- Credentials and account settings remain in separate profile homes. The
  `default` account uses existing configuration in place.
- Claude uses its official interactive authentication command. Conflicting
  shell authentication variables are reported by name without printing values.
  Verify the account shown in Claude: managed settings, external federation
  profiles, and supplied arguments can affect authentication.
- Profile exports contain usable credentials. Keep both the encrypted files
  and their passphrases private. Passphrases are read interactively.
- No code reads system Keychain credentials. A Keychain-only Claude login
  needs authentication again on the destination.
- Automated tests use dummy accounts, temporary directories, and mocked or
  fake executables. They do not send model prompts or query account limits.

## Existing Codex Tools profiles

This extraction retains the original storage paths and bundle formats:

| Provider | Profile manager | Existing default home |
| --- | --- | --- |
| Codex | `~/.codex-manager` | `~/.codex` |
| Claude | `~/.claude-manager` | `CLAUDE_CONFIG_DIR` or `~/.claude` |

Consequently, Agent Profile Manager and Codex Tools operate on the same profiles
when used under the same home directory. A rename or removal in either tool is
visible to the other. Use `--manager-root` to keep a separate registry and
`--default-home` to choose a different existing default home.

Existing installed wrappers continue to call the package that created them.
Moving to this package does not rewrite those wrappers automatically. Remove
the old wrapper with its original tool before installing a new wrapper here.
Avoid running profile mutations from both packages concurrently.

Neither package is installed or upgraded by creating this source project.
