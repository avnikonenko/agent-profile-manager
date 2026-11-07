# Versioning and releases

The project uses `MAJOR.MINOR.PATCH` versions and annotated Git tags such as
`v0.2.0`. Before 1.0, minor versions may change behavior; patch versions contain
compatible fixes. Record any migration or compatibility requirement in the
changelog.

## One version source

Edit `agent_profile_manager/__init__.py` to change `__version__`. Package metadata
reads that value through setuptools; do not add a separate version to
`pyproject.toml`.

Check the source version:

```bash
python3 -m agent_profile_manager --version
```

For an installed copy, use `pm --version`. An older installed copy keeps its old
version until it is upgraded.

## Release checklist

1. Update `__version__` and the matching entry in `CHANGELOG.md`.
2. Replace the entry's `Unreleased` marker with its actual release date.
3. Run the tests and build a wheel:

   ```bash
   python3 -m unittest discover -s tests
   python3 -m pip wheel --no-deps . --wheel-dir dist
   ```

4. Review and commit the version and changelog changes. Confirm the working tree
   is clean, then tag that commit. For version 0.2.0:

   ```bash
   git tag -a v0.2.0 -m "Agent Profile Manager 0.2.0"
   git push origin main
   git push origin v0.2.0
   ```

A tag publishes the reviewed snapshot; never move an already published release
tag. Add future work under a new unreleased version entry.

No release tag or package publication is created automatically by changing the
version file. Keep profile credentials, migration backups, and local resume
notes out of release commits and build artifacts.
