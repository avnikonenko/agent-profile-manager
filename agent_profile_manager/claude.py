"""Manage and launch Claude Code accounts with separate configuration directories."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Mapping

from agent_profile_manager import claude_profiles


AUTH_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_PROFILE",
)
PROVIDER_VARIABLES = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_SIMPLE",
)


def resolve_profiles(
    env: Mapping[str, str] | None = None,
    *,
    root: Path | None = None,
    default: Path | None = None,
) -> dict[str, Path]:
    environment = os.environ if env is None else env
    home = Path.home()
    default = default or Path(environment.get("CLAUDE_CONFIG_DIR") or str(home / ".claude"))
    profiles = {
        "default": default.expanduser().resolve(),
    }
    root = root or home / ".claude-manager"
    for profile in claude_profiles.iter_profiles(root, profiles["default"]):
        profiles[profile["name"]] = claude_profiles.home_from_profile(profile)
    return profiles


def authentication_conflicts(env: Mapping[str, str]) -> list[str]:
    conflicts = [name for name in AUTH_VARIABLES if env.get(name)]
    conflicts.extend(
        name for name in PROVIDER_VARIABLES
        if env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
    )
    if env.get("ANTHROPIC_FEDERATION_RULE_ID") and env.get("ANTHROPIC_ORGANIZATION_ID"):
        conflicts.extend(("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID"))
    return conflicts


def profile_collisions(profiles: Mapping[str, Path]) -> list[str]:
    collisions = []
    names = list(profiles)
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            left, right = profiles[name], profiles[other]
            same = left == right
            if not same:
                try:
                    same = left.samefile(right)
                except FileNotFoundError:
                    pass
            if same:
                collisions.append(f"{name} and {other}")
    return collisions


def validate_profile_directory(path: Path) -> bool:
    """Validate an existing directory without modifying it; return existence."""
    try:
        metadata = path.stat()
    except FileNotFoundError:
        # An absent ancestor is legitimate, but a file ancestor is not.
        parent = path.parent
        while True:
            try:
                parent_metadata = parent.stat()
                break
            except FileNotFoundError:
                parent = parent.parent
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise ValueError(f"Profile parent is not a directory: {parent}")
        if not os.access(parent, os.W_OK | os.X_OK):
            raise ValueError(f"Profile parent is not writable/searchable: {parent}")
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"Profile path is not a directory: {path}")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"Profile directory is not owned by this user: {path}")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError(f"Profile directory is not readable/writable/searchable: {path}")
    return True


def validate_managed_projects(root: Path, name: str, default: Path) -> None:
    if name == "default":
        return
    profile = claude_profiles.load_profile(root, name, default)
    home = claude_profiles.home_from_profile(profile)
    store = claude_profiles.store_from_profile(profile)
    projects = home / "projects"
    if not home.is_dir() or not store.is_dir() or not projects.is_symlink() or projects.resolve() != store:
        raise ValueError("Managed projects layout is missing or changed; inspect 'pm claude doctor NAME' and 'pm claude repair NAME'.")


def build_parser(prog: str = "pm claude") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__, allow_abbrev=False)
    parser.add_argument("--manager-root", type=Path, help="Managed profiles root (default: ~/.claude-manager).")
    parser.add_argument("--default-home", type=Path, help="Existing default Claude configuration directory.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List profile directories without reading credentials.", allow_abbrev=False)
    run = commands.add_parser("run", help="Launch a profile; put Claude arguments after --.", allow_abbrev=False)
    run.add_argument("profile", type=claude_profiles.valid_name)
    run.add_argument("--claude-binary", default="claude", help="Claude executable name or absolute path.")
    run.add_argument("--dry-run", action="store_true", help="Validate and display the launch without writing or executing.")
    login = commands.add_parser("login", help="Start Claude's interactive login for one profile.", allow_abbrev=False)
    login.add_argument("profile", type=claude_profiles.valid_name)
    login.add_argument("--claude-binary", default="claude", help="Claude executable name or absolute path.")
    login.set_defaults(func=command_login)

    def command(name: str, help_text: str, function: object, *, aliases: list[str] | None = None) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, aliases=aliases or [], allow_abbrev=False)
        sub.set_defaults(func=function)
        return sub

    def named(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("name", type=claude_profiles.valid_name)

    def wrapper_options(sub: argparse.ArgumentParser, *, binary: bool = False) -> None:
        sub.add_argument("--bin-dir", type=Path, default=Path("~/.local/bin"))
        sub.add_argument("--wrapper", "--command-name", dest="wrapper_name", type=claude_profiles.valid_name)
        if binary:
            sub.add_argument("--claude-binary", default="claude")

    new = command("new", "Create a named account without logging in.", claude_profiles.command_new)
    named(new)
    sharing = new.add_mutually_exclusive_group()
    sharing.add_argument("--share", type=claude_profiles.valid_name, help="Share project history and memory with PROFILE (default: default).")
    sharing.add_argument("--share-history", dest="share", action="store_const", const="default", help="Share default profile project history and memory (same as --share default).")
    sharing.add_argument("--isolated", action="store_true", help="Keep project history private instead of sharing with default.")
    new.add_argument("--force", action="store_true")
    rename = command("rename", "Rename a managed account without moving its login directory.", claude_profiles.command_rename)
    named(rename)
    rename.add_argument("new_name", type=claude_profiles.valid_name)
    rename.add_argument("--bin-dir", type=Path, default=Path("~/.local/bin"))
    install = command("install", "Install a wrapper for a named account.", claude_profiles.command_install)
    named(install)
    wrapper_options(install, binary=True)
    install.add_argument("--force", action="store_true")
    uninstall = command("uninstall", "Remove a registered account wrapper.", claude_profiles.command_uninstall)
    named(uninstall)
    wrapper_options(uninstall)
    uninstall.add_argument("--force", action="store_true")
    remove = command("remove", "Move an account to trash; default account is protected.", claude_profiles.command_remove, aliases=["rm", "delete"])
    named(remove)
    wrapper_options(remove)
    remove.add_argument("--uninstall", action="store_true")
    remove.add_argument("--purge", action="store_true")
    remove.add_argument("--remove-store", action="store_true")
    path = command("path", "Print an account directory or project store.", claude_profiles.command_path)
    named(path)
    path.add_argument("--store", action="store_true")
    doctor = command("doctor", "Inspect account layout without reading credentials.", claude_profiles.command_doctor)
    named(doctor)
    repair = command("repair", "Restore a missing managed projects link.", claude_profiles.command_repair)
    named(repair)
    repair.add_argument("--force", action="store_true", help="Existing history is never replaced.")
    export = command("export", "Export a file-backed login as an encrypted bundle.", _command_export)
    named(export)
    export.add_argument("--output", "-o", type=Path)
    export.add_argument("--include-conversations", action="store_true")
    importing = command("import", "Import an encrypted Claude account bundle.", _command_import)
    importing.add_argument("bundle", type=Path)
    importing.add_argument("--name", type=claude_profiles.valid_name)
    importing.add_argument("--force", action="store_true")
    importing.add_argument("--isolated", action="store_true", help="New imports are always isolated; --force retains the existing store.")
    importing.add_argument("--install", action="store_true")
    wrapper_options(importing, binary=True)
    return parser


def command_login(args: argparse.Namespace) -> int:
    """Run Claude's official interactive login in the selected profile."""
    root = args.manager_root.expanduser().resolve()
    default = args.default_home.expanduser().resolve()
    profiles = resolve_profiles(root=root, default=default)
    if profile_collisions(profiles):
        raise ValueError("Profile directories collide; refusing to authenticate into an ambiguous account.")
    if args.profile not in profiles:
        raise ValueError(f"Unknown Claude profile: {args.profile}. Create it with 'pm claude new NAME'.")
    conflicts = authentication_conflicts(os.environ)
    if conflicts:
        raise ValueError(
            "Inherited authentication/provider overrides can prevent interactive login: "
            + ", ".join(conflicts)
            + ". Unset these variables before logging in."
        )
    path = profiles[args.profile]
    if args.profile != "default":
        validate_managed_projects(root, args.profile, default)
    exists = validate_profile_directory(path)
    executable = shutil.which(args.claude_binary)
    if executable is None:
        print("error: Could not find claude on PATH.", file=sys.stderr)
        return 127
    if not exists:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    validate_profile_directory(path)
    environment = os.environ.copy()
    if args.profile == "default" and args.default_config_external:
        environment.pop("CLAUDE_CONFIG_DIR", None)
    else:
        environment["CLAUDE_CONFIG_DIR"] = str(path)
    print(f"Opening Claude login: profile={args.profile} config_dir={path}", file=sys.stderr, flush=True)
    os.execve(executable, [executable, "auth", "login"], environment)
    return 127


def _command_export(args: argparse.Namespace) -> int:
    from agent_profile_manager import claude_bundle

    return claude_bundle.command_export(args)


def _command_import(args: argparse.Namespace) -> int:
    from agent_profile_manager import claude_bundle

    return claude_bundle.command_import(args)


def main(argv: list[str] | None = None, *, prog: str = "pm claude") -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    forwarded: list[str] = []
    has_separator = "--" in arguments
    if has_separator:
        separator = arguments.index("--")
        forwarded = arguments[separator + 1:]
        arguments = arguments[:separator]
    parser = build_parser(prog)
    try:
        args = parser.parse_args(arguments)
        if args.command != "run" and has_separator:
            parser.error("Only run accepts Claude arguments after --.")
    except SystemExit as exc:
        return int(exc.code)

    try:
        args.default_config_external = args.default_home is None and not os.environ.get("CLAUDE_CONFIG_DIR")
        args.manager_root = (args.manager_root or Path.home() / ".claude-manager").expanduser().resolve()
        args.default_home = (args.default_home or Path(os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))).expanduser().resolve()
        if hasattr(args, "func"):
            return int(args.func(args))
        profiles = resolve_profiles(root=args.manager_root, default=args.default_home)
        collisions = profile_collisions(profiles)
        if args.command == "list":
            print("PROFILE\tCONFIG_DIR\tDIRECTORY\tSHARING\tWRAPPER")
            for name, path in profiles.items():
                try:
                    metadata = path.stat()
                except FileNotFoundError:
                    state = "not-created"
                else:
                    state = "existing" if stat.S_ISDIR(metadata.st_mode) else "not-directory"
                profile = claude_profiles.load_profile(args.manager_root, name, args.default_home)
                wrapper = profile.get("wrapper") or {}
                print(f"{name}\t{path}\t{state}\t{profile['share']}\t{wrapper.get('command', '-')}")
            if collisions:
                print("error: Profile directories collide: " + "; ".join(collisions), file=sys.stderr)
                return 2
            return 0
        if collisions:
            raise ValueError("Profile directories collide: " + "; ".join(collisions))
        if args.profile not in profiles:
            raise ValueError(f"Unknown Claude profile: {args.profile}. Create it with 'pm claude new NAME'.")
        conflicts = authentication_conflicts(os.environ)
        if conflicts:
            raise ValueError(
                "Inherited authentication/provider overrides conflict with profile selection: "
                + ", ".join(conflicts)
                + ". Unset these variables before launching a saved account."
            )
        path = profiles[args.profile]
        validate_managed_projects(args.manager_root, args.profile, args.default_home)
        exists = validate_profile_directory(path)
        executable = shutil.which(args.claude_binary)
        if executable is None:
            print("error: Could not find claude on PATH.", file=sys.stderr)
            return 127
        print(
            f"{'Dry run' if args.dry_run else 'Launching'}: profile={args.profile} "
            f"config_dir={path} executable={executable} argument_count={len(forwarded)}",
            file=sys.stdout if args.dry_run else sys.stderr,
            flush=True,
        )
        if args.dry_run:
            return 0
        if not exists:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        # Recheck after creation to reject races without chmodding existing paths.
        validate_profile_directory(path)
        validate_managed_projects(args.manager_root, args.profile, args.default_home)
        if profile_collisions(resolve_profiles(root=args.manager_root, default=args.default_home)):
            raise ValueError("Profile directories changed and now collide; launch aborted.")
        environment = os.environ.copy()
        if args.profile == "default" and args.default_config_external:
            # Setting even the standard path relocates ~/.claude.json into it.
            environment.pop("CLAUDE_CONFIG_DIR", None)
        else:
            environment["CLAUDE_CONFIG_DIR"] = str(path)
        os.execve(executable, [executable, *forwarded], environment)
        return 127
    except (ValueError, argparse.ArgumentTypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        # Do not echo arbitrary exception text: it can contain forwarded arguments.
        print(f"error: Local profile/launch operation failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
