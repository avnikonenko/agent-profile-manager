# Versioning and releases

Use `MAJOR.MINOR.PATCH` and annotated tags such as `v0.2.0`. Before 1.0, minor
versions may change behavior; patch versions contain compatible fixes.

`agent_profile_manager/__init__.py` is the only version source. Setuptools reads
`__version__` from it. Check with `python3 -m agent_profile_manager --version`
(or `pm --version` for the installed copy, which needs upgrading separately).

## Release

1. Update `__version__` and its `CHANGELOG.md` entry; replace `Unreleased` with
   the release date and note compatibility/migration requirements.
2. Test and build:

   ```bash
   python3 -m unittest discover -s tests
   python3 -m pip wheel --no-deps . --wheel-dir dist
   ```

3. Review and commit. From a clean working tree, tag the release (example 0.2.0):

   ```bash
   git tag -a v0.2.0 -m "Agent Profile Manager 0.2.0"
   git push origin main
   git push origin v0.2.0
   ```

Tags/publication are never automatic. Never move a published tag; start a new
unreleased changelog entry for future work. Exclude credentials, migration
backups, and resume notes from commits and build artifacts.
