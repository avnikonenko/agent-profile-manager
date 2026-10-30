"""Local Claude profile lifecycle, stable homes, and tracked launch wrappers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_profile_manager.manager import valid_command_name as _valid_command_name
from agent_profile_manager.manager import valid_name as _valid_name


DEFAULT_MANAGER_ROOT = Path("~/.claude-manager").expanduser()
PROFILE_FILE = "profile.json"
RESERVED_NAMES = {"default"}


def valid_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid profile name")
    try:
        return _valid_name(value)
    except (argparse.ArgumentTypeError, TypeError) as exc:
        raise ValueError("Invalid profile name") from exc


def valid_command_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid command name")
    try:
        return _valid_command_name(value)
    except (argparse.ArgumentTypeError, TypeError) as exc:
        raise ValueError("Invalid command name") from exc


def _root(root: Path) -> Path:
    return root.expanduser().resolve()


def profile_dir(root: Path, name: str) -> Path:
    valid_name(name)
    return _root(root) / "profiles" / name


def profile_file(root: Path, name: str) -> Path:
    return profile_dir(root, name) / PROFILE_FILE


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symlink manager directory: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError(f"Manager directory is not owned by this user: {path}")


def _managed_path(root: Path, path: str, area: str) -> Path:
    candidate = Path(path)
    base = _root(root) / area
    try:
        canonical_id = str(uuid.UUID(candidate.name))
    except ValueError as exc:
        raise ValueError("Invalid managed directory identifier") from exc
    if (not candidate.is_absolute() or candidate.name != canonical_id
            or candidate.parent != base or candidate.resolve() != candidate
            or base.resolve() != base):
        raise ValueError("Managed directory escapes its storage root")
    return candidate


def _validate_profile(root: Path, payload: dict[str, Any], default: Path) -> None:
    name = payload.get("name")
    if not isinstance(name, str) or name in RESERVED_NAMES:
        raise ValueError("Invalid managed profile name")
    valid_name(name)
    if payload.get("provider") != "claude":
        raise ValueError("Profile is not a Claude profile")
    home = _managed_path(root, str(payload.get("home", "")), "homes")
    store = Path(str(payload.get("conversation_store", "")))
    if store != (default.expanduser().resolve() / "projects").resolve():
        _managed_path(root, str(store), "stores")
    if home == store:
        raise ValueError("Profile home and projects store must differ")
    share = payload.get("share")
    if not isinstance(share, str):
        raise ValueError("Invalid profile sharing record")
    if share != "isolated":
        valid_name(share)
    wrapper = payload.get("wrapper")
    if wrapper is not None:
        _validate_wrapper_record(wrapper)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".profile-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_profile(root: Path, payload: dict[str, Any], default: Path | None = None) -> None:
    _validate_profile(root, payload, default or Path.home() / ".claude")
    directory = profile_dir(root, payload["name"])
    if directory.resolve() != directory or directory.parent.resolve() != directory.parent:
        raise ValueError("Profile metadata directory escapes manager root")
    _write_json(directory / PROFILE_FILE, payload)


def load_profile(root: Path, name: str, default: Path) -> dict[str, Any]:
    valid_name(name)
    if name in RESERVED_NAMES:
        home = default.expanduser().resolve()
        return {"provider": "claude", "name": name, "home": str(home),
                "conversation_store": str((home / "projects").resolve()),
                "share": "self", "created_at": ""}
    directory = profile_dir(root, name)
    if directory.resolve() != directory or directory.parent.resolve() != directory.parent:
        raise ValueError("Profile metadata directory escapes manager root")
    file = directory / PROFILE_FILE
    if file.is_symlink():
        raise ValueError("Refusing symlink profile metadata")
    payload = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("name") != name:
        raise ValueError("Profile metadata name does not match directory")
    _validate_profile(root, payload, default)
    return payload


def iter_profiles(root: Path, default: Path) -> list[dict[str, Any]]:
    base = _root(root) / "profiles"
    if not base.exists():
        return []
    if base.is_symlink():
        raise ValueError("Refusing symlink profiles directory")
    return [load_profile(root, path.name, default) for path in sorted(base.iterdir())
            if path.is_dir() and (path / PROFILE_FILE).exists()]


def home_from_profile(profile: dict[str, Any]) -> Path:
    return Path(profile["home"])


def store_from_profile(profile: dict[str, Any]) -> Path:
    return Path(profile["conversation_store"])


def profile_home(root: Path, name: str, default: Path | None = None) -> Path:
    return home_from_profile(load_profile(root, name, default or Path.home() / ".claude"))


def create_profile(root: Path, name: str, default: Path, share: str | None = None,
                   force: bool = False) -> dict[str, Any]:
    valid_name(name)
    if name in RESERVED_NAMES:
        raise ValueError("default is reserved for the existing Claude account")
    root = _root(root)
    destination = profile_dir(root, name)
    if destination.exists() or destination.is_symlink():
        if force:
            existing = load_profile(root, name, default)
            if share is not None and existing["share"] != share:
                raise ValueError("--force cannot change an existing profile's sharing")
            return existing
        raise ValueError(f"Profile already exists: {name}")
    source = load_profile(root, share, default) if share else None
    if source and share == "default":
        projects = home_from_profile(source) / "projects"
        if not projects.exists() and not projects.is_symlink():
            _private_dir(home_from_profile(source))
            _private_dir(projects)
    for directory in (root, root / "profiles", root / "homes", root / "stores"):
        _private_dir(directory)
    identifier = str(uuid.uuid4())
    home = root / "homes" / identifier
    store = store_from_profile(source) if source else root / "stores" / identifier
    home.mkdir(mode=0o700)
    created_store = False
    try:
        if source:
            # Existing shared stores remain untouched.
            if not store.is_dir():
                raise ValueError("Shared projects directory does not exist")
        else:
            store.mkdir(mode=0o700)
            created_store = True
        (home / "projects").symlink_to(store, target_is_directory=True)
        payload = {"provider": "claude", "name": name, "home": str(home),
                   "conversation_store": str(store), "share": share or "isolated",
                   "created_at": datetime.now(timezone.utc).isoformat()}
        destination.mkdir(mode=0o700)
        save_profile(root, payload, default)
        return payload
    except Exception:
        shutil.rmtree(home)
        if created_store:
            shutil.rmtree(store)
        if destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        raise


def discard_new_profile(root: Path, profile: dict[str, Any], default: Path) -> None:
    """Rollback only a newly created, unshared managed profile after failed import."""
    _validate_profile(root, profile, default)
    if profile.get("wrapper") or any(
            other["name"] != profile["name"] and (
                other["conversation_store"] == profile["conversation_store"] or other["home"] == profile["home"])
            for other in iter_profiles(root, default)):
        raise ValueError("Cannot discard a registered or shared profile")
    shutil.rmtree(profile_dir(root, profile["name"]))
    shutil.rmtree(home_from_profile(profile))
    if profile["share"] == "isolated":
        shutil.rmtree(_managed_path(root, profile["conversation_store"], "stores"))


def _args_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    return _root(args.manager_root), args.default_home.expanduser().resolve()


def command_new(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    reused = profile_dir(root, args.name).exists()
    if reused and getattr(args, "force", False) and getattr(args, "isolated", False):
        if load_profile(root, args.name, default)["share"] != "isolated":
            raise ValueError("--force cannot change an existing profile's sharing")
    share = getattr(args, "share", None)
    if getattr(args, "isolated", False):
        share = None
    elif share is None and not reused:
        share = "default"
    profile = create_profile(root, args.name, default, share, getattr(args, "force", False))
    print(f"{'Reusing' if reused else 'Created'} Claude profile {profile['name']}: {profile['home']}")
    print("History: isolated" if profile["share"] == "isolated"
          else f"History and memory: shared with {profile['share']}")
    if getattr(args, "install", False):
        return command_install(args)
    return 0


def command_path(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    profile = load_profile(root, args.name, default)
    print(profile["conversation_store"] if getattr(args, "store", False) else profile["home"])
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    profile = load_profile(root, args.name, default)
    home, store = home_from_profile(profile), store_from_profile(profile)
    projects = home / "projects"
    print(f"Profile: {args.name}\nHome: {home}\nProjects: {store}\nSharing: {profile['share']}")
    healthy = home.is_dir() and store.is_dir() and projects.resolve() == store
    print("Status: healthy" if healthy else "Status: missing home/store or incorrect projects link")
    if profile.get("wrapper"):
        match = _wrapper_matches(root, default, profile)
        print("Wrapper: verified" if match else "Wrapper: missing or modified")
        healthy = healthy and match
    return 0 if healthy else 1


def command_repair(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    profile = load_profile(root, args.name, default)
    if args.name == "default":
        raise ValueError("Repair does not modify the existing default configuration")
    home, store = home_from_profile(profile), store_from_profile(profile)
    if not home.is_dir() or not store.is_dir():
        raise ValueError("Home/store missing; restore it from backup")
    projects = home / "projects"
    if projects.exists() or projects.is_symlink():
        if projects.is_symlink() and projects.resolve() == store:
            print("Projects link is already correct")
            return 0
        raise ValueError("Refusing to replace an existing projects path")
    projects.symlink_to(store, target_is_directory=True)
    print("Restored missing projects link")
    return 0


def wrapper_script(name: str, claude_binary: str, root: Path, default: Path) -> str:
    arguments = [sys.executable, "-m", "agent_profile_manager.claude", "--manager-root", str(_root(root)),
                 "--default-home", str(default.expanduser().resolve()), "run", "--claude-binary", claude_binary, name, "--"]
    source = shlex.quote(str(Path(__file__).resolve().parent.parent))
    command = " ".join(shlex.quote(value) for value in arguments)
    return f'#!/usr/bin/env bash\nset -euo pipefail\nexport PYTHONPATH={source}${{PYTHONPATH:+:${{PYTHONPATH}}}}\nexec {command} "$@"\n'


def _validate_wrapper_record(wrapper: Any) -> None:
    if not isinstance(wrapper, dict):
        raise ValueError("Invalid wrapper record")
    command = valid_command_name(wrapper.get("command", ""))
    path = Path(str(wrapper.get("path", "")))
    if (not path.is_absolute() or path.name != command or path.parent.resolve() != path.parent
            or not isinstance(wrapper.get("naming"), str)
            or wrapper["naming"] not in {"default", "custom"}
            or not isinstance(wrapper.get("claude_binary"), str)
            or not wrapper["claude_binary"] or "\0" in wrapper["claude_binary"]):
        raise ValueError("Invalid wrapper record")
    digest = wrapper.get("sha256", "")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid wrapper digest")


def _wrapper_matches(root: Path, default: Path, profile: dict[str, Any]) -> bool:
    wrapper = profile["wrapper"]
    _validate_wrapper_record(wrapper)
    path = Path(wrapper["path"])
    if path.is_symlink() or not path.is_file():
        return False
    content = path.read_bytes()
    expected = wrapper_script(profile["name"], wrapper["claude_binary"], root, default).encode()
    return content == expected and hashlib.sha256(content).hexdigest() == wrapper["sha256"]


def _wrapper_record(name: str, binary: str, root: Path, default: Path, path: Path, custom: bool) -> tuple[dict[str, str], str]:
    if path.name == binary or Path(binary).expanduser().resolve() == path.resolve():
        raise ValueError("Wrapper would call itself")
    found = shutil.which(binary)
    if found and Path(found).resolve() == path.resolve():
        raise ValueError("Wrapper would call itself")
    content = wrapper_script(name, binary, root, default)
    return {"command": path.name, "path": str(path), "claude_binary": binary,
            "naming": "custom" if custom else "default",
            "sha256": hashlib.sha256(content.encode()).hexdigest()}, content


def _write_wrapper(path: Path, content: str, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".claude-wrapper-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, 0o755)
        if replace:
            os.replace(temporary, path)
        else:
            # Hard-link publication cannot overwrite an unrelated command in a race.
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _rollback_wrapper(path: Path, published: str, previous: str | None = None) -> None:
    if path.is_symlink() or not path.is_file() or path.read_bytes() != published.encode():
        print(f"WARNING: wrapper changed during rollback; preserved: {path}", file=sys.stderr)
        return
    if previous is None:
        path.unlink()
    else:
        _write_wrapper(path, previous, replace=True)


def command_install(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    profile = load_profile(root, args.name, default)
    if args.name in RESERVED_NAMES:
        raise ValueError("Install wrappers only for managed named profiles")
    custom = getattr(args, "wrapper_name", None)
    command = valid_command_name(custom or args.name)
    binary = getattr(args, "claude_binary", "claude")
    target = args.bin_dir.expanduser().resolve() / command
    record, content = _wrapper_record(args.name, binary, root, default, target, custom is not None)
    existing = profile.get("wrapper")
    if existing and (existing["path"] != str(target) or not getattr(args, "force", False)):
        raise ValueError("Profile already has a registered wrapper")
    occupied = target.exists() or target.is_symlink()
    if occupied and (not existing or not _wrapper_matches(root, default, profile)):
        raise ValueError("Refusing to replace an unrelated or modified wrapper")
    original = dict(profile)
    profile["wrapper"] = record
    _write_wrapper(target, content, replace=occupied)
    try:
        save_profile(root, profile, default)
    except Exception:
        if not occupied:
            _rollback_wrapper(target, content)
        else:
            _rollback_wrapper(target, content, wrapper_script(args.name, existing["claude_binary"], root, default))
        profile.clear()
        profile.update(original)
        raise
    print(f"Installed {command}: {target}")
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    profile = load_profile(root, args.name, default)
    wrapper = profile.get("wrapper")
    if not wrapper:
        raise ValueError("No registered wrapper; untracked commands are never removed")
    if getattr(args, "wrapper_name", None) not in {None, wrapper["command"]}:
        raise ValueError("Requested wrapper does not match registered wrapper")
    path = Path(wrapper["path"])
    occupied = path.exists() or path.is_symlink()
    if occupied and not _wrapper_matches(root, default, profile):
        raise ValueError("Refusing to remove a modified wrapper")
    profile.pop("wrapper")
    save_profile(root, profile, default)
    try:
        if occupied:
            path.unlink()
    except Exception:
        profile["wrapper"] = wrapper
        save_profile(root, profile, default)
        raise
    print(f"Uninstalled {wrapper['command']}")
    return 0


def command_rename(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    valid_name(args.new_name)
    if args.name in RESERVED_NAMES or args.new_name in RESERVED_NAMES:
        raise ValueError("Reserved profiles cannot be renamed or replaced")
    profile = load_profile(root, args.name, default)
    if args.name == args.new_name:
        return 0
    old_dir, new_dir = profile_dir(root, args.name), profile_dir(root, args.new_name)
    if new_dir.exists() or new_dir.is_symlink():
        raise ValueError("Destination profile already exists")
    dependents = [other for other in iter_profiles(root, default) if other["share"] == args.name]
    wrapper = profile.get("wrapper")
    target = None
    if wrapper:
        if not _wrapper_matches(root, default, profile):
            raise ValueError("Refusing to rename a missing or modified wrapper")
        old_wrapper = Path(wrapper["path"])
        target = old_wrapper.with_name(args.new_name) if wrapper["naming"] == "default" else old_wrapper
        if target != old_wrapper and (target.exists() or target.is_symlink()):
            raise ValueError("Destination wrapper already exists")
        record, content = _wrapper_record(args.new_name, wrapper["claude_binary"], root, default, target, wrapper["naming"] == "custom")
    original = dict(profile)
    old_dir.rename(new_dir)
    changed = []
    wrapper_published = False
    try:
        profile["name"] = args.new_name
        if wrapper:
            profile["wrapper"] = record
            _write_wrapper(target, content, replace=target == old_wrapper)
            wrapper_published = True
        save_profile(root, profile, default)
        for other in dependents:
            changed.append(dict(other))
            other["share"] = args.new_name
            save_profile(root, other, default)
        if wrapper and target != old_wrapper:
            old_wrapper.unlink()
    except Exception:
        for other in changed:
            save_profile(root, other, default)
        if wrapper and wrapper_published:
            if target != old_wrapper:
                _rollback_wrapper(target, content)
            else:
                _rollback_wrapper(old_wrapper, content, wrapper_script(args.name, wrapper["claude_binary"], root, default))
        new_dir.rename(old_dir)
        save_profile(root, original, default)
        raise
    print(f"Renamed {args.name} -> {args.new_name}; home remains {profile['home']}")
    return 0


def command_remove(args: argparse.Namespace) -> int:
    root, default = _args_paths(args)
    if args.name in RESERVED_NAMES:
        raise ValueError("Reserved profiles cannot be removed")
    profile = load_profile(root, args.name, default)
    if any(other["name"] != args.name and other["home"] == profile["home"]
           for other in iter_profiles(root, default)):
        raise ValueError("Profile home is also referenced by another profile")
    store = store_from_profile(profile)
    users = [other for other in iter_profiles(root, default)
             if other["name"] != args.name and other["conversation_store"] == str(store)]
    purge = getattr(args, "purge", False)
    remove_store = getattr(args, "remove_store", False)
    if users and (purge or remove_store):
        raise ValueError("Conversation store is used by other profiles")
    if remove_store:
        _managed_path(root, str(store), "stores")
    if profile.get("wrapper"):
        if not getattr(args, "uninstall", False):
            raise ValueError("Registered wrapper exists; specify --uninstall first")
        command_uninstall(args)
    home = home_from_profile(profile)
    directory = profile_dir(root, args.name)
    _private_dir(root / "trash")
    trash = root / "trash" / str(uuid.uuid4())
    _private_dir(trash)
    moved = []
    try:
        for source, label in ((directory, "metadata"), (home, "home")):
            source.rename(trash / label)
            moved.append((source, trash / label))
        if remove_store:
            store.rename(trash / "projects")
            moved.append((store, trash / "projects"))
    except Exception:
        for source, destination in reversed(moved):
            destination.rename(source)
        raise
    if purge:
        shutil.rmtree(trash)
    print(f"Removed profile {args.name}" + (" permanently" if purge else f"; backup: {trash}"))
    if not remove_store:
        print(f"Projects retained: {store}")
    return 0
