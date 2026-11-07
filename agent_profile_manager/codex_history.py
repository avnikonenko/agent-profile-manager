"""Offline migration of legacy per-profile Codex conversation indexes.

Original databases are only read during preparation. Applying requires stopped
clients; unknown schemas fail before publishing any database changes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

DATABASES = ("state_5.sqlite", "thread_history_1.sqlite")
STATE_TABLES = {
    "projects", "project_roots", "thread_sections", "threads",
    "thread_attachments", "thread_dynamic_tools", "thread_read_receipts",
    "thread_spawn_edges", "guardian_review_feedback",
}
HISTORY_TABLES = {
    "thread_history_projection_state", "thread_turns", "thread_items", "thread_realtime_items",
}
LOCAL_STATE_TABLES = {
    "_sqlx_migrations", "backfill_state", "external_agent_config_imports",
    "project_idempotency_keys", "remote_control_enrollments",
    "rollout_migration_skipped_rollouts", "rollout_migration_state",
}
# These per-user system services protect /proc/fd even from their owner. They
# are not Codex/database clients. Every other inaccessible process fails closed.
PROTECTED_USER_SERVICES = {"systemd", "(sd-pam)", "ssh-agent", "sd_espeak-ng-mb"}


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def snapshot(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"refusing non-regular SQLite file: {source}")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(connect(source)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {source.name}")
    except Exception:
        target.unlink(missing_ok=True)
        raise


def check_stopped(homes: list[Path]) -> None:
    if not sys.platform.startswith("linux"):
        raise ValueError("automatic open-file checks currently require Linux; preparation is still available")
    inodes = {
        (p.stat().st_dev, p.stat().st_ino)
        for home in homes for p in home.glob("*.sqlite*") if p.is_file()
    }
    excluded_services = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            for fd in (process / "fd").iterdir():
                try:
                    info = fd.stat()
                except FileNotFoundError:
                    continue
                if (info.st_dev, info.st_ino) in inodes:
                    raise ValueError(f"SQLite files are open in process {process.name}; stop Codex before applying")
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            try:
                service = (process / "comm").read_text().strip()
            except OSError:
                service = "unknown"
            if service in PROTECTED_USER_SERVICES:
                excluded_services.add(service)
                continue
            raise ValueError("cannot verify that all SQLite clients are stopped") from exc
    if excluded_services:
        print("Open-file scan excluded protected session services: " + ", ".join(sorted(excluded_services)))


def tables(db: sqlite3.Connection) -> dict[str, str]:
    return dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]


def thread_versions(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    with closing(connect(path)) as db:
        if "threads" not in tables(db):
            raise ValueError("state database has no threads table")
        fields = columns(db, "threads")
        if "updated_at_ms" in fields and "updated_at" in fields:
            version = "COALESCE(updated_at_ms, updated_at * 1000)"
        elif "updated_at" in fields:
            version = "updated_at * 1000"
        else:
            raise ValueError("unsupported threads schema: missing update timestamp")
        return dict(db.execute(f"SELECT id, {version} FROM threads"))


def merge_record(table: str, fields: list[str], old: tuple, new: tuple,
                 prefer_source: bool) -> tuple:
    if old == new:
        return old
    if table in {"projects", "project_roots", "thread_sections"}:
        return old
    if table in {"threads", "thread_read_receipts", "thread_spawn_edges"}:
        return new if prefer_source else old
    if table == "thread_history_projection_state":
        # Replay from the earlier complete cursor; never skip unprojected data.
        positions = [fields.index(name) for name in ("next_rollout_byte_offset", "next_rollout_ordinal")]
        a, b = tuple(old[i] for i in positions), tuple(new[i] for i in positions)
        if all(x <= y for x, y in zip(a, b)):
            return old
        if all(y <= x for x, y in zip(a, b)):
            return new
        raise ValueError("incompatible history projection cursors")
    # These ordinals describe the version of an individual event, independently
    # of the thread timestamp (which can change for an unrelated title edit).
    version = next((name for name in ("updated_at_ordinal", "rollout_end_ordinal") if name in fields), None)
    if version:
        index = fields.index(version)
        a, b = old[index], new[index]
        if a is not None and b is not None and a != b:
            return new if b > a else old
    # Identical events can have complementary nullable projection metadata.
    if all(a == b or a is None or b is None for a, b in zip(old, new)):
        return tuple(a if a is not None else b for a, b in zip(old, new))
    raise ValueError(f"ambiguous record conflict in {table}; originals retained, no migration applied")


def merge_database(source: Path, target: Path, selected: set[str], history: bool) -> None:
    allowed = HISTORY_TABLES if history else STATE_TABLES
    with closing(connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        source_tables, target_tables = tables(src), tables(dst)
        if source_tables != target_tables:
            raise ValueError(f"database schemas differ: {source.name}; upgrade both profiles with the same Codex version first")
        for table in sorted(source_tables):
            fields = columns(src, table)
            if table not in allowed and table not in LOCAL_STATE_TABLES:
                if table.startswith("thread_") or any(name == "thread_id" or name.endswith("_thread_id") for name in fields):
                    raise ValueError(f"unsupported conversation table: {table}")
        dst.execute("PRAGMA foreign_keys=OFF")
        ordered = [t for t in ("projects", "project_roots", "thread_sections", "threads") if t in allowed and t in source_tables]
        ordered += sorted(allowed.intersection(source_tables).difference(ordered))
        for table in ordered:
            info = list(src.execute(f'PRAGMA table_info("{table}")'))
            fields = [row[1] for row in info]
            key_positions = [fields.index(row[1]) for row in sorted(info, key=lambda row: row[5]) if row[5]]
            if not key_positions:
                raise ValueError(f"unsupported conversation table without a primary key: {table}")
            field_names = ",".join('"' + name.replace('"', '""') + '"' for name in fields)
            quoted = '"' + table + '"'
            # Index by the row's actual primary key, never by the entire thread.
            rows = {tuple(row[i] for i in key_positions): row for row in dst.execute(f"SELECT {field_names} FROM {quoted}")}
            expected_keys = set(rows)
            upserts = []
            for new in src.execute(f"SELECT {field_names} FROM {quoted}"):
                key = tuple(new[i] for i in key_positions)
                expected_keys.add(key)
                old = rows.get(key)
                thread_id = new[fields.index("id")] if table == "threads" else new[fields.index("thread_id")] if "thread_id" in fields else new[fields.index("child_thread_id")] if "child_thread_id" in fields else None
                merged = new if old is None else merge_record(table, fields, old, new, thread_id in selected)
                if old != merged:
                    upserts.append(merged)
            placeholders = ",".join("?" for _ in fields)
            keys = ",".join('"' + fields[i] + '"' for i in key_positions)
            updates = ",".join('"' + name + '"=excluded."' + name + '"' for name in fields if fields.index(name) not in key_positions)
            conflict = "DO UPDATE SET " + updates if updates else "DO NOTHING"
            dst.executemany(f"INSERT INTO {quoted} ({field_names}) VALUES ({placeholders}) ON CONFLICT ({keys}) {conflict}", upserts)
            actual_keys = {tuple(row) for row in dst.execute(f"SELECT {keys} FROM {quoted}")}
            if actual_keys != expected_keys:
                raise ValueError(f"record preservation check failed: {table}")
        if dst.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError(f"foreign-key validation failed: {source.name}")
        dst.commit()
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"merged database integrity check failed: {source.name}")


def publish(source: Path, target: Path) -> None:
    # All originals are backed up and clients must remain stopped until done.
    with tempfile.TemporaryDirectory(prefix=".pm-migration-", dir=target.parent) as directory:
        temporary = Path(directory) / target.name
        snapshot(source, temporary)
        os.replace(temporary, target)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(target) + suffix).unlink(missing_ok=True)


def normalize_rollout_paths(database: Path, source: Path, target: Path) -> int:
    changed = 0
    with closing(sqlite3.connect(database)) as db:
        if "rollout_path" not in columns(db, "threads"):
            return 0
        for thread_id, path in db.execute("SELECT id, rollout_path FROM threads").fetchall():
            try:
                relative = Path(path).relative_to(source)
            except ValueError:
                continue
            if relative.parts and relative.parts[0] in {"sessions", "archived_sessions"}:
                if (source / relative.parts[0]).resolve() != (target / relative.parts[0]).resolve():
                    raise ValueError("profile rollout directory does not point at the shared store")
                db.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(target / relative), thread_id))
                changed += 1
        db.commit()
    return changed


def command_migrate(args: argparse.Namespace) -> int:
    from agent_profile_manager import manager

    root, default = manager.manager_root(args), manager.default_home(args)
    if args.name == "default":
        raise ValueError("select the managed profile whose private index needs migration")
    profile = manager.load_profile(root, args.name, default)
    home, store = manager.home_from_profile(profile), manager.store_from_profile(profile)
    source = manager.legacy_sqlite_home(profile)
    target = manager.profile_sqlite_home(profile, root, default, check_legacy=False)
    if profile.get("sqlite_home"):
        raise ValueError("profile already uses its shared SQLite home")
    if not store.is_dir() or (target.exists() and not target.is_dir()):
        raise ValueError("migration requires valid SQLite directories and an existing conversation store")
    if args.apply:
        if not args.clients_stopped:
            raise ValueError("--apply requires --clients-stopped; close every Codex client first")
        check_stopped([home, source, target])
    output = args.output.expanduser().absolute() if args.output else root / "history-migrations" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(4).hex())
    # A new destination prevents accidentally replacing an earlier backup.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for label in ("source", "target", "prepared"):
        (output / label).mkdir(mode=0o700)
    (output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    (output / "profile.json").chmod(0o600)
    try:
        if source != home:
            (output / "legacy-home").mkdir(mode=0o700)
            for database in sorted(home.glob("*.sqlite")):
                snapshot(database, output / "legacy-home" / database.name)
        for label, directory in (("source", source), ("target", target)):
            for database in sorted(directory.glob("*.sqlite")):
                snapshot(database, output / label / database.name)
                if database.name.startswith(("state_", "thread_history_")) and database.name not in DATABASES:
                    raise ValueError(f"unsupported conversation database version: {database.name}")
        source_state = output / "source" / DATABASES[0]
        if not source_state.exists():
            raise ValueError("no legacy state_5.sqlite found; refusing an empty migration")
        src_versions = thread_versions(source_state)
        dst_versions = thread_versions(output / "target" / DATABASES[0])
        selected = {key for key, version in src_versions.items() if key not in dst_versions or version > dst_versions[key]}
        if selected and (output / "target" / DATABASES[1]).exists() and not (output / "source" / DATABASES[1]).exists():
            raise ValueError("source conversation-history database is missing; cannot safely replace indexed conversations")
        for filename in DATABASES:
            src, dst, prepared = (output / label / filename for label in ("source", "target", "prepared"))
            if dst.exists():
                snapshot(dst, prepared)
                if src.exists():
                    merge_database(src, prepared, selected, filename == DATABASES[1])
            elif src.exists():
                snapshot(src, prepared)
        seeded_databases = []
        for src in sorted((output / "source").glob("*.sqlite")):
            if src.name not in DATABASES and not (output / "target" / src.name).exists():
                snapshot(src, output / "prepared" / src.name)
                seeded_databases.append(src.name)
        profile_home = manager.home_from_profile(profile)
        normalized_paths = normalize_rollout_paths(output / "prepared" / DATABASES[0], profile_home, store)
        report = {
            "profile": args.name, "home": str(profile_home), "source": str(source), "target": str(target),
            "source_only_threads": len(src_versions.keys() - dst_versions.keys()),
            "newer_source_threads": len(selected.intersection(dst_versions)),
            "seeded_databases": seeded_databases,
            "normalized_rollout_paths": normalized_paths,
            "policy": "all history primary keys retained; per-record ordinals resolve updates; complementary null fields combined; ambiguous conflicts rejected; newer thread metadata wins; other destination databases retained",
            "applied": False,
        }
        report_path = output / "report.json"
        manager.atomic_write_json(report_path, report)
        if args.apply:
            check_stopped([profile_home, source, target])
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            published = []
            try:
                for prepared in sorted((output / "prepared").glob("*.sqlite")):
                    published.append(prepared.name)
                    publish(prepared, target / prepared.name)
                profile["sqlite_home"] = str(target)
                manager.save_profile(root, profile)
            except Exception as apply_error:
                rollback_errors = []
                for filename in reversed(published):
                    try:
                        backup = output / "target" / filename
                        if backup.exists():
                            publish(backup, target / filename)
                        else:
                            (target / filename).unlink(missing_ok=True)
                    except Exception:
                        rollback_errors.append(filename)
                original = json.loads((output / "profile.json").read_text())
                try:
                    if manager.load_profile(root, args.name, default) != original:
                        manager.save_profile(root, original)
                except Exception:
                    rollback_errors.append("profile.json")
                if rollback_errors:
                    raise ValueError(
                        "migration failed and rollback needs manual recovery for "
                        + ", ".join(rollback_errors) + f"; backups: {output}"
                    ) from apply_error
                raise
            report["applied"] = True
            try:
                manager.atomic_write_json(report_path, report)
            except OSError:
                print("Migration applied, but the report could not be updated; backups are retained.", file=sys.stderr)
    except Exception:
        print(f"Migration failed; originals/backups retained. Inspect: {output}", file=sys.stderr)
        raise
    print(f"{'Applied' if args.apply else 'Prepared only'}: {output}")
    print(f"Source-only conversations: {report['source_only_threads']}; newer source conversations: {report['newer_source_threads']}")
    print("Existing destination goals/memory retained; source goals/memory backed up, not merged.")
    if seeded_databases:
        print("Missing destination databases seeded from source: " + ", ".join(seeded_databases))
    if not args.apply:
        print("Live files unchanged. Close all Codex clients, then rerun with --apply --clients-stopped (use a new output directory).")
    return 0
