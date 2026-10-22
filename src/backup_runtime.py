"""Durable backup jobs: staging is local, cloud completion requires read-back proof."""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .backup_crypto import BackupError, load_key, sha256_file
from .backup_snapshot import atomic_json, create_snapshot, now, restore_snapshot
from .publication_lock import exclusive

log = logging.getLogger(__name__)


def connection():
    from .onedrive_backup import OneDriveAuth, OneDriveStore
    key = load_key(config.BACKUP_KEY_FILE)
    auth = OneDriveAuth(config.ONEDRIVE_CLIENT_ID, config.ONEDRIVE_TOKEN_CACHE, key,
                        environment=config.BACKUP_ENVIRONMENT, scope=config.ONEDRIVE_SCOPE)
    return OneDriveStore(auth, environment=config.BACKUP_ENVIRONMENT, key=key,
                         backup_path=config.ONEDRIVE_BACKUP_PATH)


def stage_current_snapshot():
    if config.BACKUP_TRANSPORT != "onedrive-api":
        raise BackupError("API snapshots require BACKUP_TRANSPORT=onedrive-api; current sync-folder configuration is unchanged")
    expected_bot = "8837268021" if config.BACKUP_ENVIRONMENT == "production" else "8701645452"
    if config.TELEGRAM_BOT_TOKEN.split(":")[0] != expected_bot:
        raise BackupError("Effective bot identity does not match the selected backup environment; snapshot refused")
    return create_snapshot(config.BASE_DIR, config.BACKUP_SPOOL_DIR, load_key(config.BACKUP_KEY_FILE),
                           environment=config.BACKUP_ENVIRONMENT,
                           excluded=(config.BACKUP_KEY_FILE, config.ONEDRIVE_TOKEN_CACHE))


def mirror_failed_upload(directory, record):
    """Optional legacy OneDrive desktop fallback; it never clears the API queue."""
    import shutil
    if not config.BACKUP_SYNC_FOLDER_FALLBACK:
        return
    destination = Path(config.BACKUP_ONEDRIVE_DIR) / "api-fallback" / record["snapshot_id"]
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(directory) / "snapshot.enc"
    temporary = destination / "snapshot.enc.tmp"
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != record["sha256"]:
        temporary.unlink(missing_ok=True)
        raise BackupError("Desktop fallback copy failed its local checksum")
    temporary.replace(destination / "snapshot.enc")
    record["sync_folder_fallback"] = {"state": "locally-copied", "cloud_status": "unverified",
                                      "directory": str(destination)}
    atomic_json(destination / "job.json", record)


def job_directory(snapshot_id, spool=None):
    import re
    if not re.fullmatch(r"(development|production)-\d{8}T\d{6}Z-[a-f0-9]{12}", snapshot_id):
        raise BackupError("Invalid backup snapshot ID")
    spool = Path(spool or config.BACKUP_SPOOL_DIR).resolve()
    directory = spool / snapshot_id
    if directory.is_symlink() or directory.resolve().parent != spool:
        raise BackupError("Snapshot resolves outside the backup spool")
    return directory


def pending_jobs(spool=None):
    spool = Path(spool or config.BACKUP_SPOOL_DIR)
    if not spool.exists():
        return []
    jobs = []
    for path in sorted(spool.glob("*/job.json")):
        if path.parent.name.startswith("."):
            continue
        directory = job_directory(path.parent.name, spool)
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("snapshot_id") != directory.name:
            raise BackupError("Backup job identity mismatch")
        if not directory.name.startswith(record.get("environment", "invalid") + "-"):
            raise BackupError("Backup job environment mismatch")
        restore_receipt = directory / "restore-test.json"
        if restore_receipt.exists():
            receipt = json.loads(restore_receipt.read_text(encoding="utf-8"))
            if receipt.get("snapshot_id") != directory.name:
                raise BackupError("Restore receipt identity mismatch")
            record["restore_test"] = receipt
        jobs.append(record | {"directory": str(directory)})
    return jobs


def sync_pending(*, store=None, spool=None, force=False):
    spool = Path(spool or config.BACKUP_SPOOL_DIR)
    spool.mkdir(parents=True, exist_ok=True)
    results = []
    with exclusive(spool / "jobs.db", "backup-sync") as acquired:
        if not acquired:
            return [{"state": "busy", "reason": "Another backup worker owns the upload lock"}]
        for record in pending_jobs(spool):
            if record.get("environment") != config.BACKUP_ENVIRONMENT:
                continue
            if record.get("state") == "verified":
                continue
            if not force and record.get("retry_at", "") > now():
                continue
            directory = job_directory(record["snapshot_id"], spool)
            record.pop("directory", None)
            record["attempts"] += 1
            try:
                encrypted = directory / "snapshot.enc"
                if sha256_file(encrypted) != record["sha256"] or encrypted.stat().st_size != record["size"]:
                    raise BackupError("Staged encrypted backup changed; upload refused")
                if store is None:
                    store = connection()

                def uploaded(item):
                    record.update(state="uploaded", remote_item_id=item["id"], uploaded_at=now())
                    atomic_json(directory / "job.json", record)

                receipt = store.upload(encrypted, record["snapshot_id"],
                                       session_path=directory / "upload-session.enc", on_uploaded=uploaded)
                if receipt.get("sha256") != record["sha256"] or receipt.get("size") != record["size"]:
                    raise BackupError("Cloud receipt does not match the staged encrypted backup")
                record.update(state="verified", verified_at=now(), receipt=receipt)
                record.pop("last_error", None)
                record.pop("retry_at", None)
                log.info("Cloud backup verified by download SHA-256: %s", record["snapshot_id"])
            except Exception as exc:
                # Network exceptions can contain signed URLs/tokens. Only our
                # explicit operator-safe exceptions may appear in job records.
                record["last_error"] = str(exc) if isinstance(exc, BackupError) else type(exc).__name__
                delay = min(3600, 60 * (2 ** min(record["attempts"] - 1, 6)))
                record["retry_at"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                try:
                    mirror_failed_upload(directory, record)
                except Exception:
                    record["fallback_error"] = "Desktop fallback unavailable; primary API job retained"
                log.warning("Cloud backup pending (%s): %s", record["snapshot_id"], record["last_error"])
            atomic_json(directory / "job.json", record)
            results.append(record)
    return results


def restore_job(snapshot_id, destination, *, cloud=False, store=None, relocate=True):
    import tempfile
    key = load_key(config.BACKUP_KEY_FILE)
    directory = job_directory(snapshot_id)
    if not snapshot_id.startswith(config.BACKUP_ENVIRONMENT + "-"):
        raise BackupError("Choose the matching backup environment explicitly; cross-environment restore refused")
    if cloud:
        store = store or connection()
        # Always fetch from Graph for a cloud restore drill; never substitute a
        # local archive when the cloud request fails.
        with tempfile.TemporaryDirectory(prefix="relic-cloud-restore-") as temporary:
            encrypted = Path(temporary) / "snapshot.enc"
            store.download(snapshot_id, encrypted)
            report = restore_snapshot(encrypted, destination, key, snapshot_id=snapshot_id, relocate=relocate)
        report["source"] = "onedrive-api"
    else:
        encrypted = directory / "snapshot.enc"
        record = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        if sha256_file(encrypted) != record["sha256"]:
            raise BackupError("Local encrypted snapshot checksum failed")
        report = restore_snapshot(encrypted, destination, key, snapshot_id=snapshot_id, relocate=relocate)
        report["source"] = "local-encrypted-snapshot"
    atomic_json(Path(destination) / ".recovery" / "restore-report.json", report)
    if (directory / "job.json").exists():
        atomic_json(directory / "restore-test.json", report)
    return report


def run_cycle(*, store=None):
    """Daily recovery snapshot plus durable retries; suitable for a recurring task."""
    if not config.BACKUP_ENABLED or config.BACKUP_TRANSPORT != "onedrive-api":
        raise BackupError("Backup cycle requires enabled onedrive-api transport")
    spool = config.BACKUP_SPOOL_DIR
    spool.mkdir(parents=True, exist_ok=True)
    with exclusive(spool / "jobs.db", "backup-cycle") as acquired:
        if not acquired:
            return [{"state": "busy", "reason": "Another backup cycle owns the snapshot lock"}]
        today = now()[:10]
        jobs = [job for job in pending_jobs() if job.get("environment") == config.BACKUP_ENVIRONMENT]
        if not any(job.get("created_at", "")[:10] == today for job in jobs):
            stage_current_snapshot()
        return sync_pending(store=store)


def check_readiness(*, store=None):
    """Require real cloud verification and a recent cloud-sourced restore drill."""
    try:
        jobs = [job for job in pending_jobs() if job.get("environment") == config.BACKUP_ENVIRONMENT]
        pending = [job for job in jobs if job.get("state") != "verified"]
        if pending:
            return False, f"{len(pending)} backup(s) still pending API verification; inspect backupctl status"
        verified = [job for job in jobs if job.get("state") == "verified" and
                    job.get("receipt", {}).get("sha256") == job.get("sha256") and
                    job.get("receipt", {}).get("verification") == "download-sha256"]
        if not verified:
            return False, "No API backup has been verified by downloaded SHA-256 yet"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        tested = [job for job in verified if job.get("restore_test", {}).get("source") == "onedrive-api" and
                  job["restore_test"].get("state") == "restore-tested" and
                  job["restore_test"].get("tested_at", "") >= cutoff]
        if not tested:
            return False, "A cloud-sourced isolated restore drill is required (within 30 days)"
        (store or connection()).connect()
        return True, "OneDrive API reachable; uploads verified; recent cloud restore drill passed"
    except Exception as exc:
        return False, str(exc) if isinstance(exc, BackupError) else type(exc).__name__


def cycle_summary(results=(), *, now_at=None):
    """Versioned service result; freshness policy belongs to the backup application."""
    current = now_at or datetime.now(timezone.utc)
    jobs = [job for job in pending_jobs() if job.get("environment") == config.BACKUP_ENVIRONMENT]
    pending = sum(job.get("state") != "verified" for job in jobs)
    proven = [job for job in jobs if job.get("state") == "verified" and
              job.get("receipt", {}).get("verification") == "download-sha256" and
              job["receipt"].get("sha256") == job.get("sha256") and
              job["receipt"].get("size") == job.get("size")]
    latest = max(proven, key=lambda job: job["snapshot_id"], default=None)
    fresh = False
    if latest:
        stamp = latest["snapshot_id"][len(config.BACKUP_ENVIRONMENT) + 1:][:16]
        captured = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        fresh = current - timedelta(hours=26) <= captured <= current + timedelta(minutes=5)
    busy = any(result.get("state") == "busy" for result in results)
    ready = bool(latest and fresh and not pending and not busy)
    return {"contract_version": 1, "cycle_verified": ready,
            "state": "verified" if ready else "busy" if busy else "pending" if pending else "stale",
            "environment": config.BACKUP_ENVIRONMENT, "pending_jobs": pending,
            "latest_snapshot_id": latest["snapshot_id"] if latest else None,
            "verification": "download-sha256" if latest else None}
