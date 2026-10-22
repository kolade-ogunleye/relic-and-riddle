import base64
import json
import os
from pathlib import Path

import pytest

from src.backup_crypto import BackupError
from src.backup_runtime import sync_pending, restore_job
from src.backup_snapshot import create_snapshot
from src.onedrive_backup import OneDriveStore
from test_backup_recovery import recovery_root
from test_onedrive_backup import Auth, Cloud


def configure(monkeypatch, tmp_path, key):
    from src import config
    key_file = tmp_path / "outside-secrets" / "recovery.key"
    key_file.parent.mkdir()
    key_file.write_bytes(base64.urlsafe_b64encode(key))
    monkeypatch.setattr(config, "BACKUP_KEY_FILE", key_file)
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "production")
    monkeypatch.setattr(config, "BACKUP_SPOOL_DIR", tmp_path / "spool")
    monkeypatch.setattr(config, "BACKUP_SYNC_FOLDER_FALLBACK", False)


def test_queue_only_marks_verified_after_readback_and_restore_really_downloads(monkeypatch, tmp_path):
    root, source_db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    try:
        job = create_snapshot(root, tmp_path / "spool", key, environment="production")
        cloud = Cloud()
        store = OneDriveStore(Auth(), environment="production", key=key, session=cloud)
        result = sync_pending(store=store, spool=tmp_path / "spool", force=True)
        assert result[0]["state"] == "verified"
        assert result[0]["receipt"]["verification"] == "download-sha256"
        # Prove the restore cannot quietly reuse the staged local archive.
        (Path(job["directory"]) / "snapshot.enc").unlink()
        before = sum(url == cloud.download_url for _, url, _ in cloud.calls)
        report = restore_job(job["snapshot_id"], tmp_path / "restored-from-cloud", cloud=True, store=store)
        assert report["source"] == "onedrive-api"
        assert report["database_integrity"] == "ok"
        assert sum(url == cloud.download_url for _, url, _ in cloud.calls) == before + 1
        record = json.loads((Path(job["directory"]) / "restore-test.json").read_text())
        assert record["source"] == "onedrive-api"
        from src.backup_runtime import check_readiness
        assert check_readiness(store=store)[0]
    finally:
        source_db.close()


def test_failed_verification_stays_uploaded_and_retryable(monkeypatch, tmp_path):
    root, source_db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    try:
        job = create_snapshot(root, tmp_path / "spool", key, environment="production")
        cloud = Cloud(corrupt=True)
        store = OneDriveStore(Auth(), environment="production", key=key, session=cloud)
        first = sync_pending(store=store, spool=tmp_path / "spool", force=True)[0]
        assert first["state"] == "uploaded" and "receipt" not in first
        assert "checksum" in first["last_error"] and first["retry_at"]
        assert (Path(job["directory"]) / "snapshot.enc").exists()
        assert sync_pending(store=store, spool=tmp_path / "spool") == []  # durable backoff
        cloud.corrupt = False
        second = sync_pending(store=store, spool=tmp_path / "spool", force=True)[0]
        assert second["state"] == "verified" and second["attempts"] == 2
        assert cloud.session_creations == 1
        from src.backup_runtime import check_readiness
        assert check_readiness(store=store)[0] is False  # no cloud restore drill yet
    finally:
        source_db.close()


def test_cloud_restore_on_replacement_host_needs_no_local_backup_history(monkeypatch, tmp_path):
    import shutil
    from src import config
    root, source_db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    try:
        job = create_snapshot(root, tmp_path / "spool", key, environment="production")
        cloud = Cloud()
        store = OneDriveStore(Auth(), environment="production", key=key, session=cloud)
        assert sync_pending(store=store, force=True)[0]["state"] == "verified"
        # A replacement machine has only the separately recovered key and a fresh
        # authorization. The old staging disk and job receipts are unavailable.
        shutil.rmtree(tmp_path / "spool")
        monkeypatch.setattr(config, "BACKUP_SPOOL_DIR", tmp_path / "replacement-spool")
        report = restore_job(job["snapshot_id"], tmp_path / "replacement-host", cloud=True, store=store)
        assert report["source"] == "onedrive-api"
        assert report["database_integrity"] == "ok"
        assert (tmp_path / "replacement-host" / ".restore-isolated").is_file()
        assert not config.BACKUP_SPOOL_DIR.exists()
    finally:
        source_db.close()


def test_backup_worker_does_not_upload_another_environments_data(monkeypatch, tmp_path):
    from src import config
    root, db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "development")
    try:
        create_snapshot(root, tmp_path / "spool", key, environment="production")
        class MustNotUpload:
            def upload(self, *args, **kwargs):
                pytest.fail("Production backup leaked into development's backup provider")
        assert sync_pending(store=MustNotUpload(), spool=tmp_path / "spool", force=True) == []
    finally:
        db.close()


def test_desktop_fallback_is_explicit_and_does_not_claim_cloud_completion(monkeypatch, tmp_path):
    from src import config
    root, source_db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    monkeypatch.setattr(config, "BACKUP_SYNC_FOLDER_FALLBACK", True)
    monkeypatch.setattr(config, "BACKUP_ONEDRIVE_DIR", tmp_path / "desktop-onedrive")
    class Offline:
        def upload(self, *args, **kwargs):
            raise BackupError("API temporarily offline")
    try:
        job = create_snapshot(root, tmp_path / "spool", key, environment="production")
        result = sync_pending(store=Offline(), spool=tmp_path / "spool", force=True)[0]
        assert result["state"] == "staged" and "receipt" not in result
        assert result["sync_folder_fallback"]["cloud_status"] == "unverified"
        mirror = Path(result["sync_folder_fallback"]["directory"]) / "snapshot.enc"
        assert mirror.read_bytes() == (Path(job["directory"]) / "snapshot.enc").read_bytes()
    finally:
        source_db.close()


def test_recurring_cycle_captures_daily_state_without_duplicate_daily_uploads(monkeypatch, tmp_path):
    from src import config
    from src.backup_runtime import run_cycle, pending_jobs
    root, source_db = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    monkeypatch.setattr(config, "BASE_DIR", root)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "8837268021:test")
    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_TRANSPORT", "onedrive-api")
    try:
        cloud = Cloud()
        store = OneDriveStore(Auth(), environment="production", key=key, session=cloud)
        assert run_cycle(store=store)[0]["state"] == "verified"
        first = pending_jobs()[0]
        assert run_cycle(store=store) == []
        assert len(pending_jobs()) == 1
        assert pending_jobs()[0]["snapshot_id"] == first["snapshot_id"]
        assert cloud.session_creations == 1
        monkeypatch.setattr(config, "BACKUP_TRANSPORT", "sync-folder")
        with pytest.raises(BackupError, match="enabled onedrive-api"):
            run_cycle(store=store)
    finally:
        source_db.close()
