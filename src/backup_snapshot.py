"""Portable, encrypted application snapshots and isolated, authenticated restores.

Snapshots include code, persistent state, media and rights evidence. Credentials,
downloaded tools, caches and logs are deliberately not recovery payloads.
"""
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from .backup_crypto import BackupError, BLOCK, encrypt_file, decrypt_file, key_id, sha256_file

DIRECTORIES = ("src", "scripts", "data", "assets", "fonts", "output")
ROOT_FILES = ("main.py", "requirements.txt", ".env.example", "README.md")
SKIP_PARTS = {"__pycache__", ".git", ".venv", ".pytest_cache", "publication_locks", "stage_cache"}


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=".record-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def safe_name(value):
    path = PurePosixPath(value)
    devices = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
    if (not value or path.is_absolute() or path.as_posix() != value or
            any(part in (".", "..") or any(char in part for char in ':\\<>"|?*') or
                part.endswith((".", " ")) or part.split(".")[0].upper() in devices for part in path.parts)):
        raise BackupError("Unsafe path in backup manifest")
    return path


def snapshot_files(root, *, excluded=()):
    root = Path(root).resolve()
    excluded = {Path(path).resolve() for path in excluded}
    candidates = [root / name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        base = root / directory
        if base.exists():
            candidates.extend(base.rglob("*"))
    seen = set()
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".tmp", ".key", ".pem", ".pfx"}:
            continue
        if path.name in {"pipeline.db", "pipeline.db-wal", "pipeline.db-shm"}:
            continue
        if path.is_symlink() or (path.exists() and root not in path.resolve().parents):
            raise BackupError("Snapshot source resolves outside the application root")
        if not path.is_file() or path.resolve() in excluded:
            continue
        name = relative.as_posix()
        safe_name(name)
        if name.casefold() in seen:
            raise BackupError("Case-colliding paths are not portable")
        seen.add(name.casefold())
        yield name, path


def check_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as database:
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("SQLite snapshot failed its integrity check")
        if database.execute("PRAGMA foreign_key_check").fetchone():
            raise BackupError("SQLite snapshot has broken foreign keys")


def create_snapshot(root, spool, key, *, environment, excluded=()):
    import hashlib
    root, spool = Path(root).resolve(), Path(spool).resolve()
    if environment not in {"development", "production"}:
        raise BackupError("Backup environment must be development or production")
    db_path = root / "data" / "pipeline.db"
    if not db_path.is_file():
        raise BackupError("Cannot make a recovery snapshot without the production database")
    # A spool inside a captured directory would recursively back up old backups.
    for directory in DIRECTORIES:
        captured = (root / directory).resolve()
        if spool == captured or captured in spool.parents:
            raise BackupError("Backup spool must be outside captured data/media/code directories")
    spool.mkdir(parents=True, exist_ok=True)
    snapshot_id = environment + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    staging = Path(tempfile.mkdtemp(dir=spool, prefix=".staging-"))
    final = spool / snapshot_id
    manifest = {"schema": 1, "snapshot_id": snapshot_id, "environment": environment,
                "source_root": str(root), "created_at": now(), "key_id": key_id(key), "files": []}
    try:
        # SQLite's online backup includes committed WAL state without copying a live .db.
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(staging / "database.db")) as backup:
            source.backup(backup)
        check_database(staging / "database.db")
        files = list(snapshot_files(root, excluded=excluded)) + [("data/pipeline.db", staging / "database.db")]
        archive = staging / "snapshot.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            for name, source in files:
                before = source.stat()
                digest, size = hashlib.sha256(), 0
                with source.open("rb") as src, bundle.open(name, "w", force_zip64=True) as dest:
                    for block in iter(lambda: src.read(BLOCK), b""):
                        digest.update(block)
                        size += len(block)
                        dest.write(block)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise BackupError("A recovery file changed while being captured; retry the snapshot")
                manifest["files"].append({"path": name, "size": size, "sha256": digest.hexdigest()})
            manifest["captured_at"] = now()
            bundle.writestr("backup-manifest.json", json.dumps(manifest, indent=2))
        verify_archive(archive, expected_snapshot=snapshot_id)
        encrypted = staging / "snapshot.enc"
        encrypt_file(archive, encrypted, key, context=snapshot_id)
        archive.unlink()
        (staging / "database.db").unlink()
        record = {"schema": 1, "snapshot_id": snapshot_id, "environment": environment,
                  "created_at": manifest["created_at"], "state": "staged", "key_id": key_id(key),
                  "sha256": sha256_file(encrypted), "size": encrypted.stat().st_size,
                  "file_count": len(manifest["files"]), "attempts": 0}
        atomic_json(staging / "job.json", record)
        os.replace(staging, final)
        return record | {"directory": str(final)}
    except Exception:
        shutil.rmtree(staging)
        raise


def verify_archive(path, *, expected_snapshot=None):
    import hashlib
    with zipfile.ZipFile(path) as bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(name.casefold() for name in names)):
            raise BackupError("Duplicate paths in recovery archive")
        try:
            manifest_info = bundle.getinfo("backup-manifest.json")
            if manifest_info.file_size > 32 * 1024 * 1024:
                raise BackupError("Recovery manifest is too large")
            manifest = json.loads(bundle.read(manifest_info))
        except (KeyError, ValueError):
            raise BackupError("Recovery manifest is missing or invalid") from None
        if manifest.get("schema") != 1 or (expected_snapshot and manifest.get("snapshot_id") != expected_snapshot):
            raise BackupError("Unexpected recovery snapshot identity")
        entries = manifest.get("files", [])
        if not isinstance(entries, list) or not entries:
            raise BackupError("Recovery manifest has no files")
        expected = {entry["path"]: entry for entry in entries}
        if len(expected) != len(entries) or set(names) != set(expected) | {"backup-manifest.json"}:
            raise BackupError("Recovery archive differs from its manifest")
        for info in infos:
            safe_name(info.filename)
            if PurePosixPath(info.filename).name.startswith(".env") and PurePosixPath(info.filename).name != ".env.example":
                raise BackupError("Credential .env files are not accepted in recovery payloads")
            if stat.S_ISLNK(info.external_attr >> 16) or info.is_dir():
                raise BackupError("Linked or directory entries are not accepted")
            if info.filename == "backup-manifest.json":
                continue
            entry = expected[info.filename]
            if info.file_size != entry["size"]:
                raise BackupError("Recovery size does not match manifest")
            digest = hashlib.sha256()
            with bundle.open(info) as stream:
                for block in iter(lambda: stream.read(BLOCK), b""):
                    digest.update(block)
            if digest.hexdigest() != entry["sha256"]:
                raise BackupError("Recovery checksum does not match manifest")
        if "data/pipeline.db" not in expected:
            raise BackupError("Recovery database is missing")
        return manifest


def relocated(value, source_root, target):
    """Rewrite only source-root-prefixed absolute paths, including JSON map keys."""
    if isinstance(value, str):
        windows = bool(PureWindowsPath(source_root).drive)
        source = PureWindowsPath(source_root) if windows else PurePosixPath(source_root)
        candidate = PureWindowsPath(value) if windows else PurePosixPath(value)
        try:
            if candidate.is_absolute() and source != candidate:
                return str(Path(target).joinpath(*candidate.relative_to(source).parts))
            if candidate == source:
                return str(target)
        except ValueError:
            pass
        # Some database columns contain JSON text rather than plain paths.
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                updated = relocated(parsed, source_root, target)
                if updated != parsed:
                    return json.dumps(updated, ensure_ascii=False)
            except ValueError:
                pass
        return value
    if isinstance(value, dict):
        return {relocated(k, source_root, target): relocated(v, source_root, target) for k, v in value.items()}
    if isinstance(value, list):
        return [relocated(item, source_root, target) for item in value]
    return value


def restore_snapshot(encrypted, destination, key, *, snapshot_id, relocate=True):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise BackupError("Restore destination must not exist; never restore over live production")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".restore-"))
    try:
        archive = staging / "snapshot.zip"
        decrypt_file(encrypted, archive, key, context=snapshot_id)
        manifest = verify_archive(archive, expected_snapshot=snapshot_id)
        restored = staging / "application"
        restored.mkdir()
        # All members were validated before any extraction. No extractall or links.
        with zipfile.ZipFile(archive) as bundle:
            for entry in manifest["files"]:
                target = restored.joinpath(*safe_name(entry["path"]).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry["path"]) as src, target.open("xb") as dest:
                    shutil.copyfileobj(src, dest, BLOCK)
        check_database(restored / "data" / "pipeline.db")
        recovery = restored / ".recovery"
        recovery.mkdir()
        atomic_json(recovery / "backup-manifest.json", manifest)
        report = {"snapshot_id": snapshot_id, "state": "restore-tested", "tested_at": now(),
                  "checksums_verified": len(manifest["files"]), "database_integrity": "ok",
                  "source_root": manifest["source_root"], "destination": str(destination),
                  "publishing_disabled": True, "revalidation_required": True, "relocated": []}
        if relocate:
            for path in restored.rglob("*.json"):
                if recovery in path.parents:
                    continue
                try:
                    old = json.loads(path.read_text(encoding="utf-8-sig"))
                except (ValueError, UnicodeError):
                    continue
                updated = relocated(old, manifest["source_root"], destination)
                if updated != old:
                    saved = recovery / "original-evidence" / path.relative_to(restored)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, saved)
                    atomic_json(path, updated)
                    report["relocated"].append(path.relative_to(restored).as_posix())
            shutil.copy2(restored / "data" / "pipeline.db", recovery / "pipeline.original.db")
            with closing(sqlite3.connect(restored / "data" / "pipeline.db")) as db, db:
                # Preserve rows, statuses, approvers, usage and publication IDs.
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    if table.startswith("sqlite_"):
                        continue
                    quoted = '"' + table.replace('"', '""') + '"'
                    columns = db.execute(f"PRAGMA table_info({quoted})").fetchall()
                    for row in db.execute(f"SELECT rowid,* FROM {quoted}").fetchall():
                        assignments, values = [], []
                        for column, value in zip(columns, row[1:]):
                            updated = relocated(value, manifest["source_root"], destination)
                            if updated != value:
                                assignments.append('"' + column[1].replace('"', '""') + '"=?')
                                values.append(updated)
                        if assignments:
                            db.execute(f"UPDATE {quoted} SET {','.join(assignments)} WHERE rowid=?", values + [row[0]])
            check_database(restored / "data" / "pipeline.db")
        # CLI guard blocks operation of this restored copy until explicit recovery review.
        (restored / ".restore-isolated").write_text("Recovery validation only; publishing is disabled.\n", encoding="utf-8")
        # Older releases may not understand the isolation marker. Preserve their
        # exact entry point and install a guard before allowing any of its imports.
        entrypoint = restored / "main.py"
        if entrypoint.exists():
            shutil.copy2(entrypoint, recovery / "main.original.py")
            entrypoint.write_text(
                'from pathlib import Path\n'
                '_root = Path(__file__).resolve().parent\n'
                'if (_root / ".restore-isolated").exists():\n'
                '    raise SystemExit("Isolated recovery copy: runtime is disabled pending recovery review.")\n'
                'exec(compile((_root / ".recovery" / "main.original.py").read_bytes(), __file__, "exec"), globals())\n',
                encoding="utf-8")
            report["entrypoint_guard_installed"] = True
        atomic_json(recovery / "restore-report.json", report)
        # An exclusive directory rename must not overwrite a destination created meanwhile.
        if destination.exists():
            raise BackupError("Restore destination appeared while restoring")
        restored.rename(destination)
        return report
    finally:
        shutil.rmtree(staging)
