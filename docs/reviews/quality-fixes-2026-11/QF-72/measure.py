"""QF-72 measurement: hourly verified SQLite copies, bounded local retention, and the backup line the operator sees.

    python docs/reviews/quality-fixes-2026-11/QF-72/measure.py <repo root> [--db FILE]

<repo root> is the code measured (the parent or this row). Prints JSON; writes nothing in either tree: everything runs
in a temporary folder that is removed. No network (sockets refused; OneDrive is the test suite's in-memory double).

- "fixture": a generated application root whose database has a commit still only in its -wal file, a generated key,
  and ten cloud-verified daily archives already in the spool. One worker cycle (backup_runtime.run_cycle, as the
  backup task runs it) is measured: hourly copies made, whether the -wal row is in one, daily archives left, and
  /status's heartbeat text with the worker's receipt aged 27 h (the row's acceptance: "backup stale").
- "old_archive": an archive exactly as the writer before this row made it (no "kind", database in WAL mode),
  restored by the measured code.
- "real_db" (--db, a pipeline database): a copy is made with SQLite's online backup API (read-only open, so the file
  is never written), then per-table row counts and a SHA-256 over each table's ordered rows are taken from that copy,
  an hourly copy is made of it by the measured code and restored isolated, and the same counts and hashes are taken
  from the restored file. Prints counts and mismatches only.
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import sqlite3
import sys
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path


def no_network(*_a, **_k):
    raise OSError("network disabled in the QF-72 measurement")


def table_digest(path):
    """{table: [rows, sha256 over its rows in rowid/primary order]} from a database file (opened read-only)."""
    out = {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                           "'sqlite_%' ORDER BY name")]
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            digest, rows = hashlib.sha256(), 0
            for row in db.execute(f"SELECT * FROM {quoted} ORDER BY 1"):
                digest.update(repr(row).encode("utf-8"))
                rows += 1
            out[name] = [rows, digest.hexdigest()]
    return out


def generated_root(base):
    root = base / "app"
    (root / "data").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "main.py").write_text("# fixture application\n", encoding="utf-8")
    db = sqlite3.connect(root / "data" / "pipeline.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("CREATE TABLE runs(run_id TEXT PRIMARY KEY, status TEXT);"
                     "CREATE TABLE llm_usage(id INTEGER PRIMARY KEY, cost_usd REAL);"
                     "INSERT INTO runs VALUES('run_a','published'); INSERT INTO llm_usage VALUES(1, 2.75);")
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("INSERT INTO llm_usage VALUES(2, 1.25)")  # committed, but only in the -wal file
    db.commit()
    return root, db


def configure(config, base, root, key):
    key_file = base / "custody" / "recovery.key"
    key_file.parent.mkdir()
    key_file.write_bytes(base64.urlsafe_b64encode(key))
    for name, value in {"BASE_DIR": root, "BACKUP_KEY_FILE": key_file, "BACKUP_ENVIRONMENT": "production",
                        "BACKUP_SPOOL_DIR": base / "spool", "BACKUP_SYNC_FOLDER_FALLBACK": False,
                        "BACKUP_ENABLED": True, "BACKUP_TRANSPORT": "onedrive-api",
                        "BACKUP_STATUS_FILE": root / "data" / "backup-status.json",
                        "TELEGRAM_BOT_TOKEN": "8837268021:fixture",  # the identity check reads the id only
                        "ISSUES_DIR": base / "issues", "LOG_DIR": base / "logs"}.items():
        setattr(config, name, value)


def daily(config, root, key, age_days):
    from src.backup_snapshot import atomic_json, create_snapshot
    job = create_snapshot(root, config.BACKUP_SPOOL_DIR, key, environment="production")
    directory = Path(job.pop("directory"))
    job.update(state="verified", created_at=(datetime.now(UTC) - timedelta(days=age_days)).isoformat(),
               receipt={"verification": "download-sha256", "sha256": job["sha256"], "size": job["size"]})
    atomic_json(directory / "job.json", job)


def hourly_records(config):
    spool = Path(config.BACKUP_SPOOL_DIR) / "sqlite-hourly"
    if not spool.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(spool.glob("*/job.json"))]


def restored_hourly_rows(config, key, record, target):
    """The fixture's llm_usage rows in an isolated restore of the hourly copy (the row only in -wal must be there)."""
    from src.backup_snapshot import restore_snapshot
    spool = Path(config.BACKUP_SPOOL_DIR) / "sqlite-hourly"
    restore_snapshot(spool / record["snapshot_id"] / "snapshot.enc", target, key,
                     snapshot_id=record["snapshot_id"], relocate=False)
    with closing(sqlite3.connect(target / "data" / "pipeline.db")) as db:
        return db.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]


def fixture(config, base):
    from test_onedrive_backup import Auth, Cloud

    from src.backup_runtime import pending_jobs, run_cycle
    from src.onedrive_backup import OneDriveStore
    root, writer = generated_root(base)
    key = os.urandom(32)
    configure(config, base, root, key)
    try:
        for age in range(10, 0, -1):
            daily(config, root, key, age)
        before = len(pending_jobs())
        started = time.perf_counter()
        results = run_cycle(store=OneDriveStore(Auth(), environment="production", key=key, session=Cloud()))
        seconds = round(time.perf_counter() - started, 2)
        hourly = [r for r in hourly_records(config) if r.get("state") == "local-verified"]
        rows = restored_hourly_rows(config, key, hourly[0], base / "restored-hourly") if hourly else None
        receipt_path = config.BACKUP_STATUS_FILE
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None
        heartbeat = {}
        from src.control import heartbeat_text
        from src.db import PipelineDatabase
        db = PipelineDatabase(base / "status.db")
        for label, hours in (("fresh", 0), ("27h", 27)):
            if receipt:
                aged = dict(receipt, snapshot_at=(datetime.now(UTC) - timedelta(hours=hours)).isoformat())
                receipt_path.write_text(json.dumps(aged), encoding="utf-8")
            text = heartbeat_text(db, None)
            heartbeat[label] = {"backup_stale": "backup stale" in text, "has_line": "Last snapshot" in text,
                                "drill_age_shown": "last drill" in text}
        return {"cycle_upload_state": results[0]["state"] if results else None, "cycle_seconds": seconds,
                "daily_archives_before": before, "daily_archives_after": len(pending_jobs()),
                "hourly_verified_copies": len(hourly), "hourly_copy_has_wal_only_row": rows == 2 if hourly else None,
                "receipt_fields": sorted(receipt) if receipt else None, "heartbeat": heartbeat}
    finally:
        writer.close()


def old_archive(config, base):
    """Restore, by the measured code, of an archive written the way the writer before QF-72 wrote it."""
    from src.backup_crypto import encrypt_file, key_id, sha256_file
    from src.backup_runtime import restore_job
    from src.backup_snapshot import atomic_json
    root, writer = generated_root(base)
    key = os.urandom(32)
    configure(config, base, root, key)
    snapshot_id = "production-20261020T000000Z-0123456789ab"
    directory = Path(config.BACKUP_SPOOL_DIR) / snapshot_id
    directory.mkdir(parents=True)
    work = base / "old-writer"
    work.mkdir()
    with closing(sqlite3.connect(work / "database.db")) as copy:
        writer.backup(copy)
    writer.close()
    files = [("main.py", root / "main.py"), ("data/pipeline.db", work / "database.db")]
    manifest = {"schema": 1, "snapshot_id": snapshot_id, "environment": "production", "source_root": str(root),
                "created_at": "2026-10-20T00:00:00+00:00", "key_id": key_id(key),
                "files": [{"path": n, "size": p.stat().st_size, "sha256": sha256_file(p)} for n, p in files]}
    with zipfile.ZipFile(work / "snapshot.zip", "x") as bundle:
        for name, path in files:
            bundle.write(path, name)
        bundle.writestr("backup-manifest.json", json.dumps(manifest))
    encrypt_file(work / "snapshot.zip", directory / "snapshot.enc", key, context=snapshot_id)
    digest = sha256_file(directory / "snapshot.enc")
    atomic_json(directory / "job.json", {"schema": 1, "snapshot_id": snapshot_id, "environment": "production",
                "created_at": manifest["created_at"], "state": "verified", "key_id": key_id(key), "sha256": digest,
                "size": (directory / "snapshot.enc").stat().st_size, "file_count": 2, "attempts": 1})
    report = restore_job(snapshot_id, base / "restored-old")
    return {"restored": report.get("state") == "restore-tested", "database_integrity": report.get("database_integrity"),
            "checksums_verified": report.get("checksums_verified")}


def real_db(config, base, source_path):
    """A read-only online copy of a real database through the measured hourly path; counts and mismatches only."""
    root = base / "app"
    (root / "data").mkdir(parents=True)
    with closing(sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)) as source, \
            closing(sqlite3.connect(root / "data" / "pipeline.db")) as copy:
        source.backup(copy)
    key = os.urandom(32)
    configure(config, base, root, key)
    before = table_digest(root / "data" / "pipeline.db")
    try:
        from src.backup_checkpoints import hourly_checkpoint
    except ImportError:
        return {"tables": len(before), "rows": sum(v[0] for v in before.values()), "hourly_copy": None}
    started = time.perf_counter()
    record = hourly_checkpoint()
    seconds = round(time.perf_counter() - started, 2)
    from src.backup_runtime import restore_job
    target = base / "restored-real"
    restore_job(record["snapshot_id"], target, database_only=True, relocate=False)
    after = table_digest(target / "data" / "pipeline.db")
    mismatched = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
    return {"tables": len(before), "rows": sum(v[0] for v in before.values()),
            "rows_restored": sum(v[0] for v in after.values()), "tables_mismatched": len(mismatched),
            "database_mb": round((root / "data" / "pipeline.db").stat().st_size / 2**20, 2),
            "encrypted_copy_mb": round(record["size"] / 2**20, 2), "hourly_copy_seconds": seconds,
            "copies_kept_default": 48, "spool_mb_at_default_retention": round(48 * record["size"] / 2**20, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path[:0] = [str(root), str(root / "tests")]
    socket.socket.connect = no_network
    socket.create_connection = no_network
    from src import config
    out = {}
    for name, job in (("fixture", fixture), ("old_archive", old_archive)):
        with tempfile.TemporaryDirectory(prefix=f"qf72-{name}-") as base:
            out[name] = job(config, Path(base))
    if args.db:
        with tempfile.TemporaryDirectory(prefix="qf72-real-") as base:
            out["real_db"] = real_db(config, Path(base), args.db)
    beat = out["fixture"]["heartbeat"]
    out["acceptance_met"] = bool(beat["27h"]["backup_stale"] and not beat["fresh"]["backup_stale"]
                                 and out["fixture"]["hourly_verified_copies"] == 1
                                 and out["fixture"]["hourly_copy_has_wal_only_row"]
                                 and out["fixture"]["daily_archives_after"] <= 7 and out["old_archive"]["restored"])
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
