"""Encrypted, allowlisted Claude profile transfer without reading system keychains."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from agent_profile_manager import manager, profile_bundle


FORMAT = "claude-manager-profile"
VERSION = 1
MANIFEST = "manifest.json"
PROFILE_FILES = (".credentials.json", "settings.json", ".claude.json", "CLAUDE.md")
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PROFILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 10000


def prompt_export_passphrase() -> str:
    return manager.prompt_export_passphrase()


def prompt_import_passphrase() -> str:
    return manager.prompt_import_passphrase()


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Refusing a non-directory or symlink staging destination")


def _regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Refusing to transfer a symlink or special file")


def _copy_private(source: Path, target: Path) -> None:
    _regular(source)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as outgoing, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, outgoing)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _export_sources(profile: dict[str, Any], include_conversations: bool, default_config_external: bool | None = None) -> list[tuple[Path, str]]:
    home = Path(profile["home"])
    credentials = home / ".credentials.json"
    if not credentials.exists():
        raise ValueError(
            "File-backed Claude credentials are missing. Keychain-only logins cannot be exported; "
            "sign in again on the destination. No system Keychain is read."
        )
    external = (home.resolve() == (Path.home() / ".claude").resolve()
                and not os.environ.get("CLAUDE_CONFIG_DIR")) if default_config_external is None else default_config_external
    sources = []
    for name in PROFILE_FILES:
        source = home / name
        if name == ".claude.json" and profile["name"] == "default" and external:
            source = Path.home() / ".claude.json"
        if source.exists() or source.is_symlink():
            _regular(source)
            sources.append((source, f"profile/{name}"))
    if include_conversations:
        projects = home / "projects"
        store = Path(profile["conversation_store"]).resolve()
        if projects.is_symlink():
            if projects.resolve(strict=True) != store:
                raise ValueError("Project symlink does not match the profile conversation store")
            projects = store
        if projects.exists():
            if not projects.is_dir() or projects.stat().st_uid != os.getuid():
                raise ValueError("Projects path is not an owned directory")
            def scan_error(error: OSError) -> None:
                raise error
            for current, directories, files in os.walk(projects, followlinks=False, onerror=scan_error):
                parent = Path(current)
                directories.sort()
                for name in directories:
                    if not stat.S_ISDIR((parent / name).lstat().st_mode):
                        raise ValueError("Refusing to transfer a symlink or special directory")
                for name in sorted(files):
                    source = parent / name
                    _regular(source)
                    sources.append((source, "conversations/projects/" + source.relative_to(projects).as_posix()))
    return sources


def create_archive(path: Path, profile: dict[str, Any], include_conversations: bool, default_config_external: bool | None = None) -> None:
    sources = _export_sources(profile, include_conversations, default_config_external)
    if len(sources) + 1 > MAX_MEMBERS:
        raise ValueError("Too many files in profile export")
    manifest = {"format": FORMAT, "version": VERSION, "name": profile["name"],
                "includes": {"conversations": include_conversations}}
    encoded_manifest = json.dumps(manifest, sort_keys=True)
    total = len(encoded_manifest.encode("utf-8"))
    for source, name in sources:
        size = source.stat().st_size
        limit = MAX_PROFILE_BYTES if name.startswith("profile/") else MAX_FILE_BYTES
        if size > limit:
            raise ValueError("A profile export file exceeds the size limit")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Profile export exceeds the total size limit")
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, encoded_manifest)
        for source, name in sources:
            _regular(source)
            archive.write(source, name)


def validate_archive(archive: zipfile.ZipFile) -> dict[str, Any]:
    records = archive.infolist()
    if len(records) > MAX_MEMBERS:
        raise ValueError("Too many files in profile bundle")
    names = [record.filename for record in records]
    if len(names) != len(set(names)):
        raise ValueError("Bundle contains duplicate paths")
    if MANIFEST not in names or archive.getinfo(MANIFEST).file_size > 65536:
        raise ValueError("Bundle manifest is missing or too large")
    total = 0
    for record in records:
        name = record.filename
        if record.flag_bits & 1 or record.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("Unsupported ZIP encryption or compression")
        path = PurePosixPath(name)
        if ("\\" in name or "\x00" in name or not path.parts or path.is_absolute()
                or ".." in path.parts or path.as_posix() != name.rstrip("/")):
            raise ValueError("Unsafe path in profile bundle")
        mode = record.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if (kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or (record.is_dir() and kind == stat.S_IFREG)
                or (not record.is_dir() and kind == stat.S_IFDIR)):
            raise ValueError("Bundle contains a symlink or special file")
        allowed = name == MANIFEST or name in {f"profile/{item}" for item in PROFILE_FILES}
        history = len(path.parts) >= 2 and path.parts[:2] == ("conversations", "projects")
        structural = record.is_dir() and name in {"profile/", "conversations/"}
        if history and len(path.parts) == 2 and not record.is_dir():
            raise ValueError("Projects root in bundle is not a directory")
        if not (allowed or history or structural):
            raise ValueError("Unexpected path in Claude profile bundle")
        if allowed and record.is_dir():
            raise ValueError("Profile file is a directory")
        limit = MAX_PROFILE_BYTES if name.startswith("profile/") else MAX_FILE_BYTES
        if record.file_size > limit or record.file_size < 0:
            raise ValueError("Bundle member exceeds the size limit")
        total += record.file_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Bundle exceeds the total size limit")
    files = {r.filename for r in records if not r.is_dir()}
    for name in names:
        if any(parent.as_posix() in files for parent in PurePosixPath(name).parents):
            raise ValueError("Bundle file/directory paths conflict")
    try:
        manifest = json.loads(archive.read(MANIFEST))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid profile bundle manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("Not a supported Claude profile bundle")
    name = manifest.get("name")
    if not isinstance(name, str):
        raise ValueError("Invalid profile bundle name")
    manager.valid_name(name)
    includes = manifest.get("includes")
    if not isinstance(includes, dict) or not isinstance(includes.get("conversations"), bool):
        raise ValueError("Invalid profile bundle metadata")
    if "profile/.credentials.json" not in files:
        raise ValueError("Bundle file-backed credentials are missing")
    if not includes["conversations"] and any(name.startswith("conversations/") for name in names):
        raise ValueError("Bundle conversation metadata disagrees with contents")
    for name in files:
        # Read and verify every CRC before any destination profile is created.
        with archive.open(name) as source:
            remaining = archive.getinfo(name).file_size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("Truncated profile bundle member")
                remaining -= len(chunk)
            if source.read(1):
                raise ValueError("Bundle member exceeds declared size")
    return manifest


def _stage_archive(archive: zipfile.ZipFile, stage: Path) -> None:
    for record in archive.infolist():
        if record.filename == MANIFEST:
            continue
        target = stage / record.filename
        _private_directory(target if record.is_dir() else target.parent)
        if not record.is_dir():
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as destination, archive.open(record) as source:
                shutil.copyfileobj(source, destination)


def command_export(args: argparse.Namespace) -> int:
    from agent_profile_manager import claude_profiles as profiles

    root = Path(args.manager_root).expanduser().resolve()
    default = Path(args.default_home).expanduser().resolve()
    profile = profiles.load_profile(root, args.name, default)
    output = Path(args.output or f"{args.name}.claude-profile").expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing to replace an existing export")
    passphrase = prompt_export_passphrase()
    with tempfile.TemporaryDirectory(prefix="claude-profile-export-") as temporary:
        source = Path(temporary) / "profile.zip"
        create_archive(source, profile, args.include_conversations, getattr(args, "default_config_external", None))
        source.chmod(0o600)
        with zipfile.ZipFile(source) as archive:
            included = {name.removeprefix("profile/") for name in archive.namelist() if name.startswith("profile/")}
        _private_directory(output.parent) if not output.parent.exists() else None
        profile_bundle.encrypt_file(source, output, passphrase)
    print(f"Exported Claude profile {args.name}: {output}")
    print("Conversations: included" if args.include_conversations else "Conversations: not included")
    optional = PROFILE_FILES[1:]
    print("Configuration included: " + (", ".join(name for name in optional if name in included) or "none"))
    missing = [name for name in optional if name not in included]
    if missing:
        print("Optional configuration absent and skipped: " + ", ".join(missing))
    return 0


def _replace_login(root: Path, home: Path, stage: Path) -> Path:
    candidates = [name for name in PROFILE_FILES if (stage / "profile" / name).is_file()]
    for name in candidates:
        target = home / name
        if target.exists() or target.is_symlink():
            _regular(target)
    backup_root = root / "backups"
    _private_directory(backup_root)
    backup = backup_root / ("import-" + secrets.token_hex(12))
    _private_directory(backup)
    existed = set()
    for name in candidates:
        target = home / name
        if target.exists():
            _copy_private(target, backup / name)
            existed.add(name)
    # Stage replacements on the destination filesystem for atomic per-file replace.
    local_stage = Path(tempfile.mkdtemp(prefix=".login-import-", dir=home))
    replaced = []
    try:
        for name in candidates:
            _copy_private(stage / "profile" / name, local_stage / name)
        for name in candidates:
            os.replace(local_stage / name, home / name)
            replaced.append(name)
    except Exception:
        for name in reversed(replaced):
            if name in existed:
                restore = local_stage / ("restore-" + secrets.token_hex(8))
                _copy_private(backup / name, restore)
                os.replace(restore, home / name)
            else:
                (home / name).unlink()
        raise
    finally:
        shutil.rmtree(local_stage)
    return backup


def _publish_profile(root: Path, default: Path, name: str, stage: Path) -> dict[str, Any]:
    from agent_profile_manager import claude_profiles as profiles

    _private_directory(root)
    with tempfile.TemporaryDirectory(prefix=".profile-import-", dir=root) as temporary:
        return _publish_staged(root, default, name, stage, Path(temporary))


def _publish_staged(root: Path, default: Path, name: str, stage: Path, staged_root: Path) -> dict[str, Any]:
    from agent_profile_manager import claude_profiles as profiles

    profile = profiles.create_profile(staged_root, name, default, share=None)
    home = Path(profile["home"])
    store = Path(profile["conversation_store"])
    for source in (stage / "profile").iterdir():
        _copy_private(source, home / source.name)
    projects = stage / "conversations" / "projects"
    if projects.exists():
        shutil.copytree(projects, store, dirs_exist_ok=True)
    destination_home = root / "homes" / home.name
    destination_store = root / "stores" / store.name
    _private_directory(destination_home.parent)
    _private_directory(destination_store.parent)
    if any(path.exists() or path.is_symlink() for path in (destination_home, destination_store)):
        raise ValueError("Import destination already exists")
    _private_directory(root / "profiles")
    destination_profile = profiles.profile_dir(root, name)
    destination_profile.mkdir(mode=0o700, exist_ok=False)
    moved = []
    try:
        os.rename(home, destination_home)
        moved.append(destination_home)
        os.rename(store, destination_store)
        moved.append(destination_store)
        (destination_home / "projects").unlink()
        (destination_home / "projects").symlink_to(destination_store, target_is_directory=True)
        profile["home"] = str(destination_home)
        profile["conversation_store"] = str(destination_store)
        profiles.save_profile(root, profile, default)
    except Exception:
        for path in reversed(moved):
            shutil.rmtree(path)
        shutil.rmtree(destination_profile)
        raise
    return profile


def command_import(args: argparse.Namespace) -> int:
    from agent_profile_manager import claude_profiles as profiles

    source = Path(args.bundle).expanduser().resolve()
    _regular(source)
    if source.stat().st_size > MAX_TOTAL_BYTES + 16 * 1024 * 1024:
        raise ValueError("Encrypted profile bundle exceeds the size limit")
    passphrase = prompt_import_passphrase()
    root = Path(args.manager_root).expanduser().resolve()
    default = Path(args.default_home).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="claude-profile-import-") as temporary:
        stage = Path(temporary)
        archive_path = stage / "profile.zip"
        profile_bundle.decrypt_file(source, archive_path, passphrase)
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile as exc:
            raise ValueError("Decrypted Claude profile archive is invalid") from exc
        with archive:
            try:
                manifest = validate_archive(archive)
            except zipfile.BadZipFile as exc:
                raise ValueError("Claude profile archive integrity check failed") from exc
            name = args.name or manifest["name"]
            manager.valid_name(name)
            if name == "default":
                raise ValueError("Default import is refused; use --name for a managed profile")
            existing = profiles.profile_dir(root, name)
            if existing.exists() or existing.is_symlink():
                if not args.force:
                    raise ValueError("Profile already exists; use --force to update login/configuration only")
                profile = profiles.load_profile(root, name, default)
            else:
                profile = None
            _stage_archive(archive, stage)
        if profile is None:
            _publish_profile(root, default, name, stage)
            print(f"Imported Claude profile {name} with an isolated conversation store")
        else:
            backup = _replace_login(root, Path(profile["home"]), stage)
            print(f"Updated Claude profile {name}; private backup: {backup}")
            if manifest["includes"]["conversations"]:
                print("Conversations: bundled history skipped; existing store preserved")
            else:
                print("Conversations: existing store preserved")
        if args.install:
            install_args = argparse.Namespace(**vars(args))
            install_args.name = name
            install_args.force = False
            return profiles.command_install(install_args)
    return 0
