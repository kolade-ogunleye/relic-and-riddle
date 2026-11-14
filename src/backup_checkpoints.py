"""Hourly SQLite recovery points, local spool retention and the worker's redacted status receipt.

Only the backup worker (``backup_runtime.run_cycle``) calls this module. An hourly copy is an archive of the same
format as the daily one (schema 1, encrypted with the same key) holding only ``data/pipeline.db``; it lives in
``<spool>/sqlite-hourly`` and never goes to the cloud, so it narrows the recovery point for the database on this host
while the daily archive stays the off-machine recovery point.
"""
import logging
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config
from .backup_crypto import BackupError, load_key, sha256_file
from .backup_snapshot import (
    atomic_json,
    create_snapshot,
    database_profile,
    restore_snapshot,
)

log = logging.getLogger(__name__)

HOURLY_DIR = "sqlite-hourly"
HOURLY_DUE = timedelta(minutes=55)  # the worker runs every 10 minutes: one copy an hour, not one every 50 minutes
# Files a verified snapshot directory may hold. Writers before 2026-11 left empty -wal/-shm sidecars of the staged
# database (the copy was in WAL mode when it was checked); anything else is unexplained and keeps the directory.
OWNED_FILES = {"snapshot.enc", "job.json", "restore-test.json", "upload-session.enc", "database.db-wal", "database.db-shm"}


def stamp(value):
    """An aware UTC datetime from an ISO string, or None."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def hourly_spool():
    return Path(config.BACKUP_SPOOL_DIR) / HOURLY_DIR


def plain(path):
    """Neither a symbolic link nor a Windows junction/reparse point."""
    path = Path(path)
    return not path.is_symlink() and not (getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def cloud_verified(job):
    """A full archive whose upload was proven by downloading it again (the daily recovery point)."""
    receipt = job.get("receipt") or {}
    return (job.get("environment") == config.BACKUP_ENVIRONMENT and job.get("kind", "full") == "full" and
            job.get("state") == "verified" and receipt.get("verification") == "download-sha256" and
            receipt.get("sha256") == job.get("sha256") and receipt.get("size") == job.get("size"))


def locally_verified(job):
    """An hourly copy that was decrypted, checked and restored when it was made."""
    receipt = job.get("receipt") or {}
    return (job.get("environment") == config.BACKUP_ENVIRONMENT and job.get("kind") == "database" and
            job.get("state") == "local-verified" and receipt.get("verification") == "isolated-restore" and
            receipt.get("sha256") == job.get("sha256") and receipt.get("size") == job.get("size"))


def valid_drill(job):
    """The job carries the receipt of a cloud-sourced isolated restore (the drill `backupctl restore --cloud` runs)."""
    report = job.get("restore_test") or {}
    return (report.get("snapshot_id") == job.get("snapshot_id") and report.get("source") == "onedrive-api" and
            report.get("state") == "restore-tested" and report.get("database_integrity") == "ok" and
            report.get("publishing_disabled") is True and report.get("checksums_verified", 0) > 0 and
            stamp(report.get("tested_at")) is not None)


def archive_intact(job, spool):
    from .backup_runtime import job_directory
    try:
        directory = job_directory(job["snapshot_id"], spool)
        archive = directory / "snapshot.enc"
        return (plain(directory) and plain(archive) and archive.stat().st_size == job.get("size") and
                sha256_file(archive) == job.get("sha256"))
    except (OSError, BackupError):
        return False


def _jobs(spool):
    from .backup_runtime import pending_jobs
    return pending_jobs(spool) if Path(spool).exists() else []


def hourly_checkpoint(*, now_at=None):
    """Takes an hourly copy when the newest verified one is 55 minutes old or more; returns its record or None.

    The copy counts only after a real test restore: decrypt, every manifest hash, SQLite integrity and foreign keys
    (restore_snapshot), then the restored database's schema digest and per-table row counts must equal the staged
    copy's. A copy that fails stays in the spool unverified: it never suppresses the next attempt or counts as kept."""
    expected_bot = "8837268021" if config.BACKUP_ENVIRONMENT == "production" else "8701645452"
    if (config.BACKUP_ENVIRONMENT not in {"production", "development"} or
            config.TELEGRAM_BOT_TOKEN.split(":")[0] != expected_bot):
        raise BackupError("Effective bot identity does not match the checkpoint environment")
    current = now_at or datetime.now(UTC)
    spool = hourly_spool()
    spool.mkdir(parents=True, exist_ok=True)
    if not plain(spool):
        raise BackupError("Linked checkpoint spool refused")
    jobs = _jobs(spool)
    for job in jobs:
        made = stamp(job.get("created_at"))
        recent = made is not None and current - HOURLY_DUE < made <= current + timedelta(minutes=5)
        # A failed attempt waits for the next hour too: retrying every ten minutes would only add failed copies.
        if recent and ((locally_verified(job) and archive_intact(job, spool)) or job.get("state") == "failed"):
            return None
    _drop_old_failures(spool, jobs)
    key = load_key(config.BACKUP_KEY_FILE)
    record = create_snapshot(config.BASE_DIR, spool, key, environment=config.BACKUP_ENVIRONMENT, database_only=True)
    directory = Path(record.pop("directory"))
    try:
        with tempfile.TemporaryDirectory(dir=spool, prefix=".verify-") as temporary:
            report = restore_snapshot(directory / "snapshot.enc", Path(temporary) / "isolated", key,
                                      snapshot_id=record["snapshot_id"], relocate=False)
            restored = database_profile(Path(temporary) / "isolated" / "data" / "pipeline.db")
        if report.get("database_integrity") != "ok" or restored != record.get("database"):
            raise BackupError("Hourly copy did not restore to the same schema and rows")
    except Exception as exc:  # noqa: BLE001 - any fault in the test restore leaves the copy unverified
        record.update(state="failed", failed_at=datetime.now(UTC).isoformat(),
                      last_error=str(exc) if isinstance(exc, BackupError) else type(exc).__name__)
        atomic_json(directory / "job.json", record)  # kept for inspection; never counted as a recovery point
        raise BackupError("Hourly copy failed its test restore; kept unverified for inspection") from None
    record.update(state="local-verified", verified_at=datetime.now(UTC).isoformat(),
                  receipt={"verification": "isolated-restore", "sha256": record["sha256"], "size": record["size"]})
    atomic_json(directory / "job.json", record)
    return record


def _drop_old_failures(spool, jobs):
    """Keeps only the newest failed hourly copy as evidence, so a lasting fault cannot fill the disk. Holds the
    retention lock, as pruning does, so a copy someone is restoring is never deleted under them."""
    from .backup_runtime import job_directory
    from .publication_lock import exclusive
    failed = sorted((job for job in jobs if job.get("kind") == "database" and job.get("state") == "failed"),
                    key=lambda job: job.get("created_at", ""), reverse=True)
    if len(failed) < 2:
        return
    with exclusive(Path(spool) / "jobs.db", "backup-retention") as acquired:
        if not acquired:
            return  # a restore holds the spool; the next attempt tidies
        for job in failed[1:]:
            directory = job_directory(job["snapshot_id"], spool)
            children = list(directory.iterdir()) if plain(directory) else [None]
            if all(child is not None and child.name in OWNED_FILES and child.is_file() and plain(child)
                   for child in children):
                for child in children:
                    child.unlink()
                directory.rmdir()


def prune(spool, keep, *, hourly):
    """Deletes local verified archives beyond the newest ``keep``; returns {"removed", "retained", "damaged"}.

    Daily archives: only cloud-verified ones are candidates (their downloaded-SHA-256 copy stays in OneDrive; the
    cloud is never touched) and the one holding the latest drill receipt is kept besides the newest ``keep``. Hourly
    copies are this host's only copies, so each kept one is re-hashed first and a damaged one neither counts nor is
    deleted. Pending, other-environment, unknown or linked content is never deleted. Restores hold the same lock."""
    from .publication_lock import exclusive
    spool = Path(spool)
    if not spool.exists():
        return {"removed": [], "retained": 0, "damaged": 0}
    if not plain(spool):
        raise BackupError("Linked retention spool refused")
    with exclusive(spool / "jobs.db", "backup-retention") as acquired:
        if not acquired:
            return {"removed": [], "retained": 0, "damaged": 0, "busy": True}  # a restore holds them; next cycle
        return _prune(spool.resolve(), max(2, int(keep)), hourly=hourly)


def _prune(spool, keep, *, hourly):
    from .backup_runtime import job_directory
    proven = locally_verified if hourly else cloud_verified
    jobs = sorted((job for job in _jobs(spool) if proven(job)), key=lambda job: job.get("created_at", ""), reverse=True)
    kept, damaged, candidates = [], 0, []
    for job in jobs:
        if len(kept) >= keep:
            candidates.append(job)
        elif not hourly or archive_intact(job, spool):
            kept.append(job)
        else:
            damaged += 1
    if not hourly:
        drills = [job for job in jobs if valid_drill(job)]
        if drills:  # its restore-test.json is the drill evidence the status line and readiness read
            latest = max(drills, key=lambda job: stamp(job["restore_test"]["tested_at"]))
            candidates = [job for job in candidates if job["snapshot_id"] != latest["snapshot_id"]]
    removed, retained = [], 0
    for job in candidates:
        directory = job_directory(job["snapshot_id"], spool)
        children = list(directory.iterdir()) if directory.is_dir() and plain(directory) else None
        if (children is None or directory.resolve().parent != spool or
                any(child.name not in OWNED_FILES or not child.is_file() or not plain(child) for child in children) or
                any(child.name == "database.db-wal" and child.stat().st_size for child in children)):
            retained += 1
            log.warning("Backup retention kept %s: unexplained or linked content", job["snapshot_id"])
            continue
        for child in children:
            child.unlink()
        directory.rmdir()
        removed.append(job["snapshot_id"])
    if removed:
        log.info("Backup retention removed %d local %s archive(s)", len(removed), "hourly" if hourly else "daily")
    return {"removed": removed, "retained": retained, "damaged": damaged}


def status_receipt(errors=(), *, now_at=None):
    """Timestamps and counts only: no paths, identifiers, hashes or error text."""
    full = [job for job in _jobs(config.BACKUP_SPOOL_DIR) if cloud_verified(job)]
    hourly = [job for job in _jobs(hourly_spool()) if locally_verified(job)]
    drills = [stamp(job["restore_test"]["tested_at"]) for job in full if valid_drill(job)]

    def newest(values):
        values = [value for value in values if value is not None]
        return max(values).isoformat() if values else None

    return {"schema": 1, "environment": config.BACKUP_ENVIRONMENT,
            "updated_at": (now_at or datetime.now(UTC)).isoformat(),
            "snapshot_at": newest(stamp(job.get("created_at")) for job in full),
            "database_at": newest(stamp(job.get("created_at")) for job in hourly),
            "drill_at": newest(drills), "local_snapshots": len(full), "hourly_copies": len(hourly),
            "errors": sorted(set(errors))}


def write_status(errors=()):
    """Publishes the receipt the pipeline reads (``BACKUP_STATUS_FILE``). Never raises: a status the worker cannot
    write shows as unknown and alerts in the pipeline, and must not fail the backup itself."""
    try:
        receipt = status_receipt(errors)
        atomic_json(config.BACKUP_STATUS_FILE, receipt)
        return receipt
    except Exception as exc:  # noqa: BLE001 - the receipt must never fail the backup it reports on
        log.warning("Backup status receipt not written: %s", type(exc).__name__)
        return None
