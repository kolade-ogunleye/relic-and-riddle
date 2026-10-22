import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.service_runtime import ServiceSettings, configure_backup, execute


def manifest(tmp_path):
    root = tmp_path / "production"
    runtime = tmp_path / "runtime"
    return {"app_root": str(root), "maintenance_root": str(tmp_path / "maintenance"),
            "runtime_root": str(runtime), "backup_key_file": str(tmp_path / "custody/key"),
            "source_token_cache": str(tmp_path / "custody/source.enc"),
            "token_cache": str(runtime / "secrets/tokens.enc"), "legacy_archive": str(tmp_path / "local-archive"),
            "client_id": "11111111-1111-4111-8111-111111111111", "expected_bot_id": "8837268021"}


def load(tmp_path, value=None):
    p = tmp_path / "launch.json"
    p.write_text(json.dumps(value or manifest(tmp_path)))
    return ServiceSettings.load(p)


def test_service_contract_accepts_existing_lab_manifest_without_private_values(tmp_path):
    settings = load(tmp_path)
    config = SimpleNamespace()
    configure_backup(settings, config)
    assert config.BACKUP_SPOOL_DIR == settings.runtime_root / "spool/Backup"
    assert config.ONEDRIVE_TOKEN_CACHE == settings.token_cache
    assert config.BACKUP_SYNC_FOLDER_FALLBACK is False


@pytest.mark.parametrize("change", ["version", "boolean-version", "relative-path", "shared-cache", "outside-cache", "unknown-field"])
def test_service_settings_reject_unsafe_or_incompatible_manifests(tmp_path, change):
    value = manifest(tmp_path)
    if change == "version": value["contract_version"] = 2
    if change == "boolean-version": value["contract_version"] = True
    if change == "relative-path": value["app_root"] = "relative"
    if change == "shared-cache": value["token_cache"] = value["source_token_cache"]
    if change == "outside-cache": value["token_cache"] = str(tmp_path / "other/tokens.enc")
    if change == "unknown-field": value["client_secret"] = "must-not-be-accepted"
    with pytest.raises(ValueError): load(tmp_path, value)


def test_pipeline_entrypoint_never_configures_api_worker_or_reads_backup_custody(monkeypatch, tmp_path):
    settings = load(tmp_path)
    config = SimpleNamespace(BACKUP_TRANSPORT="onedrive-api")
    monkeypatch.setattr(ServiceSettings, "configure", lambda _: config)
    import src.service_runtime as runtime
    monkeypatch.setattr(runtime.os, "chdir", lambda _: None)
    monkeypatch.setattr(sys, "argv", [])
    calls = []
    monkeypatch.setattr(runtime.runpy, "run_path", lambda path, **kw: calls.append((path, list(sys.argv))))
    assert execute("pipeline", settings)[1] == 0
    assert calls[0][1][-2:] == ["daemon", "--dry-run"]
    assert config.BACKUP_TRANSPORT == "sync-folder"
    assert not settings.backup_key_file.exists()
    assert not settings.token_cache.exists()


def test_contract_discovery_has_no_configuration_or_credential_dependency():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, str(root / "scripts/service_runtime.py"), "contract"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["contract_version"] == 1


@pytest.mark.parametrize("age_hours,verified,pending,expected", [(1, True, False, True), (27, True, False, False),
                                                              (-1, True, False, False), (1, False, False, False),
                                                              (1, True, True, False)])
def test_cycle_summary_requires_current_verified_backup_and_no_pending_jobs(monkeypatch, age_hours, verified, pending, expected):
    from src import backup_runtime as runtime, config
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "production")
    current = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    stamp = (current - timedelta(hours=age_hours)).strftime("%Y%m%dT%H%M%SZ")
    job = {"snapshot_id": "production-" + stamp + "-0123456789ab", "environment": "production",
           "state": "verified", "sha256": "expected", "size": 12,
           "receipt": {"verification": "download-sha256", "sha256": "expected" if verified else "wrong", "size": 12}}
    jobs = [job]
    if pending: jobs.append({"environment": "production", "state": "staged"})
    monkeypatch.setattr(runtime, "pending_jobs", lambda: jobs)
    assert runtime.cycle_summary(now_at=current)["cycle_verified"] is expected
    assert runtime.cycle_summary([{"state": "busy"}], now_at=current)["cycle_verified"] is False
