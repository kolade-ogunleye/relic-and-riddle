"""QF-72: hourly verified SQLite copies, bounded local retention and the operator's recovery-freshness line.

Everything runs on generated fixtures (a temporary application root, key, spool and mocked OneDrive); no real
database, key, .env or network is touched."""
import json
import os
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_backup_queue import configure
from test_backup_recovery import recovery_root
from test_onedrive_backup import Auth, Cloud

from src import config
from src.backup_crypto import BackupError, encrypt_file, key_id
from src.backup_snapshot import atomic_json, create_snapshot

PRODUCTION_BOT = "8837268021:fixture"  # the identity check reads only the numeric id before the colon


@pytest.fixture
def setup(monkeypatch, tmp_path):
    root, source = recovery_root(tmp_path)
    key = os.urandom(32)
    configure(monkeypatch, tmp_path, key)
    monkeypatch.setattr(config, "BASE_DIR", root)
    monkeypatch.setattr(config, "BACKUP_STATUS_FILE", root / "data" / "backup-status.json", raising=False)
    monkeypatch.setattr(config, "BACKUP_DATABASE_KEEP", 2, raising=False)
    monkeypatch.setattr(config, "BACKUP_SNAPSHOT_KEEP", 2, raising=False)
    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_TRANSPORT", "onedrive-api")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", PRODUCTION_BOT)
    yield root, source, key
    source.close()


def hourly_jobs():
    from src.backup_checkpoints import hourly_spool
    from src.backup_runtime import pending_jobs
    return pending_jobs(hourly_spool())


def cloud_store(key):
    from src.onedrive_backup import OneDriveStore
    return OneDriveStore(Auth(), environment="production", key=key, session=Cloud())


def made_at(monkeypatch, instant):
    from src import backup_snapshot
    monkeypatch.setattr(backup_snapshot, "now", lambda: instant.isoformat())


# --- hourly copies ---------------------------------------------------------------------------------------------------

def test_hourly_copy_holds_uncheckpointed_wal_rows_restores_isolated_and_leaves_no_plaintext(setup, tmp_path):
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool
    from src.backup_runtime import restore_job
    _root, source, _key = setup
    source.execute("INSERT INTO llm_usage VALUES(2,1.25)")
    source.commit()  # still only in the -wal file: the writer's connection stays open
    job = hourly_checkpoint()
    assert job["kind"] == "database" and job["file_count"] == 1 and job["state"] == "local-verified"
    assert job["receipt"] == {"verification": "isolated-restore", "sha256": job["sha256"], "size": job["size"]}
    assert job["database"]["table_rows"] == {"runs": 1, "publications": 1, "llm_usage": 2, "settings": 1}
    assert hourly_checkpoint() is None  # one an hour
    target = tmp_path / "isolated-db"
    report = restore_job(job["snapshot_id"], target, database_only=True, relocate=False)
    assert report["publishing_disabled"] and (target / ".restore-isolated").exists()
    assert not (target / "assets").exists() and not (target / "main.py").exists()
    with sqlite3.connect(target / "data" / "pipeline.db") as copied:
        assert copied.execute("SELECT SUM(cost_usd) FROM llm_usage").fetchone()[0] == 4.0
    leftovers = [p.name for p in hourly_spool().rglob("*") if p.is_file() and p.name.startswith("database.db")]
    assert leftovers == [] and not list(hourly_spool().glob(".verify-*"))


def test_hourly_copy_is_due_again_after_an_hour_and_keeps_the_newest_n(setup, monkeypatch):
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool, prune
    current = datetime.now(UTC)
    made = []
    for age in (4, 3, 2, 0):
        instant = current - timedelta(hours=age)
        made_at(monkeypatch, instant)
        made.append(hourly_checkpoint(now_at=instant + timedelta(seconds=1)))
    assert all(made) and len(hourly_jobs()) == 4
    result = prune(hourly_spool(), 2, hourly=True)
    assert set(result["removed"]) == {made[0]["snapshot_id"], made[1]["snapshot_id"]}
    assert {job["snapshot_id"] for job in hourly_jobs()} == {made[2]["snapshot_id"], made[3]["snapshot_id"]}


def test_a_copy_that_fails_its_test_restore_never_counts_and_failures_cannot_pile_up(setup, monkeypatch):
    from src import backup_checkpoints
    from src.backup_checkpoints import hourly_checkpoint
    real = backup_checkpoints.database_profile
    calls = []

    def wrong_restore(path):  # the restored copy reports a different row count than the staged one
        calls.append(path)
        profile = real(path)
        return profile | {"table_rows": {**profile["table_rows"], "llm_usage": -1}}

    monkeypatch.setattr(backup_checkpoints, "database_profile", wrong_restore)
    current = datetime.now(UTC)
    for age in (3, 2):
        instant = current - timedelta(hours=age)
        made_at(monkeypatch, instant)
        with pytest.raises(BackupError, match="test restore"):
            hourly_checkpoint(now_at=instant + timedelta(seconds=1))
        assert hourly_checkpoint(now_at=instant + timedelta(minutes=10)) is None  # no retry every ten minutes
    failed = hourly_jobs()
    assert len(failed) == 2 and {job["state"] for job in failed} == {"failed"}
    made_at(monkeypatch, current)
    with pytest.raises(BackupError):
        hourly_checkpoint(now_at=current + timedelta(seconds=1))
    assert len(hourly_jobs()) == 2  # the newest earlier failure is kept as evidence, older ones go
    monkeypatch.setattr(backup_checkpoints, "database_profile", real)
    later = current + timedelta(hours=1)
    made_at(monkeypatch, later)
    assert hourly_checkpoint(now_at=later + timedelta(seconds=1))["state"] == "local-verified"


def test_wrong_bot_identity_refuses_the_hourly_copy(setup, monkeypatch):
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "development")
    with pytest.raises(BackupError, match="identity"):
        hourly_checkpoint()
    assert not hourly_spool().exists()


# --- retention ---------------------------------------------------------------------------------------------------------

def daily_job(root, key, *, age, state="verified", environment="production"):
    job = create_snapshot(root, config.BACKUP_SPOOL_DIR, key, environment=environment)
    directory = Path(job.pop("directory"))
    job.update(state=state, created_at=(datetime.now(UTC) - timedelta(days=age)).isoformat(),
               receipt={"verification": "download-sha256", "sha256": job["sha256"], "size": job["size"]})
    atomic_json(directory / "job.json", job)
    return job, directory


def drill_receipt(job, directory, *, age_days=0):
    atomic_json(directory / "restore-test.json", {
        "snapshot_id": job["snapshot_id"], "source": "onedrive-api", "state": "restore-tested",
        "database_integrity": "ok", "publishing_disabled": True, "checksums_verified": 2,
        "tested_at": (datetime.now(UTC) - timedelta(days=age_days)).isoformat()})


def test_daily_retention_keeps_newest_n_the_drilled_one_and_everything_unproven(setup):
    from src.backup_checkpoints import prune
    root, _, key = setup
    jobs = [daily_job(root, key, age=age) for age in (5, 3, 1, 0)]
    drill_receipt(*jobs[0])
    pending = daily_job(root, key, age=9, state="uploaded")[1]
    other = daily_job(root, key, age=9, environment="development")[1]
    result = prune(config.BACKUP_SPOOL_DIR, 2, hourly=False)
    assert result["removed"] == [jobs[1][0]["snapshot_id"]]
    assert jobs[0][1].exists() and jobs[2][1].exists() and jobs[3][1].exists()
    assert pending.exists() and other.exists()


def test_daily_retention_reads_no_archive_bytes(setup, monkeypatch):
    """The cloud holds each verified daily archive: pruning their local copies must not re-hash gigabytes."""
    from src import backup_checkpoints
    root, _, key = setup
    jobs = [daily_job(root, key, age=age) for age in (3, 2, 1, 0)]
    monkeypatch.setattr(backup_checkpoints, "sha256_file", lambda path: pytest.fail("hashed a daily archive"))
    assert len(backup_checkpoints.prune(config.BACKUP_SPOOL_DIR, 2, hourly=False)["removed"]) == 2
    assert [directory.exists() for _, directory in jobs] == [False, False, True, True]


@pytest.mark.parametrize("damage", ["unknown", "linked", "journal"])
def test_retention_keeps_a_directory_with_unexplained_or_linked_content(setup, monkeypatch, damage):
    from src import backup_checkpoints
    root, _, key = setup
    jobs = [daily_job(root, key, age=age) for age in (3, 2, 0)]
    oldest = jobs[0][1]
    if damage == "unknown":
        (oldest / "keep-evidence.txt").write_text("do not remove")
    elif damage == "journal":
        (oldest / "database.db-wal").write_bytes(b"unexplained pages")
    else:
        original = backup_checkpoints.plain
        monkeypatch.setattr(backup_checkpoints, "plain", lambda path: False if Path(path) == oldest else original(path))
    result = backup_checkpoints.prune(config.BACKUP_SPOOL_DIR, 2, hourly=False)
    assert result == {"removed": [], "retained": 1, "damaged": 0}
    assert all(directory.exists() for _, directory in jobs)


def test_a_damaged_hourly_copy_neither_counts_nor_is_deleted(setup, monkeypatch):
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool, prune
    current = datetime.now(UTC)
    made = []
    for age in (3, 2, 0):
        instant = current - timedelta(hours=age)
        made_at(monkeypatch, instant)
        made.append(hourly_checkpoint(now_at=instant + timedelta(seconds=1)))
    newest = hourly_spool() / made[-1]["snapshot_id"] / "snapshot.enc"
    newest.write_bytes(b"damaged")
    result = prune(hourly_spool(), 2, hourly=True)
    assert result["removed"] == [] and result["damaged"] == 1  # the two intact older copies are the kept two
    assert newest.exists()


def test_restore_and_retention_share_one_lock(setup):
    from src.backup_checkpoints import prune
    from src.backup_runtime import restore_job
    from src.publication_lock import exclusive
    root, _, key = setup
    job, _ = daily_job(root, key, age=0)
    with exclusive(config.BACKUP_SPOOL_DIR / "jobs.db", "backup-retention") as held:
        assert held
        assert prune(config.BACKUP_SPOOL_DIR, 2, hourly=False)["busy"] is True
        with pytest.raises(BackupError, match="retention"):
            restore_job(job["snapshot_id"], root.parent / "restored")


# --- the worker cycle and its receipt -------------------------------------------------------------------------------

def test_cycle_takes_the_hourly_copy_prunes_and_writes_a_redacted_receipt(setup):
    from src.backup_runtime import pending_jobs, run_cycle
    root, _, key = setup
    old = [daily_job(root, key, age=age) for age in (4, 3, 2)]
    assert run_cycle(store=cloud_store(key))[0]["state"] == "verified"
    assert len(hourly_jobs()) == 1
    assert len(pending_jobs()) == 2 and not old[0][1].exists()  # today's archive + newest old one: keep 2
    receipt = json.loads(config.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    assert set(receipt) == {"schema", "environment", "updated_at", "snapshot_at", "database_at", "drill_at",
                            "local_snapshots", "hourly_copies", "errors"}
    assert receipt["errors"] == [] and receipt["hourly_copies"] == 1 and receipt["drill_at"] is None
    text = config.BACKUP_STATUS_FILE.read_text(encoding="utf-8")
    assert "production-" not in text and str(root) not in text and key_id(key) not in text


def test_an_hourly_fault_or_an_unwritable_receipt_never_stops_the_upload(setup, monkeypatch):
    from src import backup_checkpoints
    from src.backup_runtime import run_cycle

    def broken(*a, **k):
        raise BackupError("fixture fault")

    monkeypatch.setattr(backup_checkpoints, "hourly_checkpoint", broken)
    assert run_cycle(store=cloud_store(setup[2]))[0]["state"] == "verified"
    assert json.loads(config.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))["errors"] == ["hourly"]
    monkeypatch.setattr(backup_checkpoints, "atomic_json", broken)
    assert run_cycle(store=cloud_store(setup[2])) == []  # nothing left to upload, and no exception


def test_a_cloud_drill_moves_last_drill_at_once(setup):
    from src.backup_runtime import restore_job, sync_pending
    root, _, key = setup
    job = create_snapshot(root, config.BACKUP_SPOOL_DIR, key, environment="production")
    store = cloud_store(key)
    assert sync_pending(store=store, force=True)[0]["state"] == "verified"
    restore_job(job["snapshot_id"], root.parent / "drill", cloud=True, store=store)
    receipt = json.loads(config.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    assert receipt["drill_at"] is not None and receipt["snapshot_at"] is not None


def test_an_old_overlay_that_cannot_load_the_hourly_module_never_stops_the_daily_backup(setup, monkeypatch, tmp_path):
    """The old launcher puts a maintenance overlay first on src.__path__; with a pre-QF-72 backup_snapshot there,
    `src.backup_checkpoints` cannot import. The daily archive, the upload and the cloud drill must still run."""
    import sys

    from src.backup_runtime import pending_jobs, restore_job, run_cycle
    _root, _, key = setup
    monkeypatch.setitem(sys.modules, "src.backup_checkpoints", None)  # any import of it raises ImportError
    store = cloud_store(key)
    assert run_cycle(store=store)[0]["state"] == "verified"
    job = pending_jobs()[0]
    report = restore_job(job["snapshot_id"], tmp_path / "drill-without-module", cloud=True, store=store)
    assert report["state"] == "restore-tested" and report["source"] == "onedrive-api"
    report = restore_job(job["snapshot_id"], tmp_path / "local-without-module")
    assert report["state"] == "restore-tested"
    assert not config.BACKUP_STATUS_FILE.exists()  # no receipt: the pipeline alerts
    assert not (config.BACKUP_SPOOL_DIR / "sqlite-hourly").exists()


def test_failed_copies_are_not_tidied_while_a_restore_holds_the_hourly_spool(setup, monkeypatch):
    from src import backup_checkpoints
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool
    from src.publication_lock import exclusive
    real = backup_checkpoints.database_profile
    monkeypatch.setattr(backup_checkpoints, "database_profile",
                        lambda path: real(path) | {"schema_sha256": "differs"} if "isolated" in str(path) else real(path))
    current = datetime.now(UTC)
    for age in (3, 2, 1):
        instant = current - timedelta(hours=age)
        made_at(monkeypatch, instant)
        hourly_spool().mkdir(parents=True, exist_ok=True)
        with exclusive(hourly_spool() / "jobs.db", "backup-retention") as held:
            assert held
            with pytest.raises(BackupError):
                hourly_checkpoint(now_at=instant + timedelta(seconds=1))
    assert len(hourly_jobs()) == 3  # nothing deleted under the lock
    made_at(monkeypatch, current)
    with pytest.raises(BackupError):
        hourly_checkpoint(now_at=current + timedelta(seconds=1))
    assert len(hourly_jobs()) == 2  # once free: the newest earlier failure and the new one


# --- compatibility ---------------------------------------------------------------------------------------------------

def test_an_archive_from_the_previous_writer_still_restores_and_counts(setup, tmp_path):
    """Schema-1 archive exactly as the writer before QF-72 made it: no "kind" anywhere, database still in WAL mode."""
    from src.backup_checkpoints import cloud_verified
    from src.backup_runtime import pending_jobs, restore_job
    from src.backup_snapshot import sha256_file
    root, source, key = setup
    snapshot_id = "production-20261020T000000Z-0123456789ab"
    directory = config.BACKUP_SPOOL_DIR / snapshot_id
    directory.mkdir(parents=True)
    work = tmp_path / "old-writer"
    work.mkdir()
    with sqlite3.connect(work / "database.db") as copy:
        source.backup(copy)
    assert sqlite3.connect(work / "database.db").execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    files = [("main.py", root / "main.py"), ("data/pipeline.db", work / "database.db")]
    manifest = {"schema": 1, "snapshot_id": snapshot_id, "environment": "production", "source_root": str(root),
                "created_at": "2026-10-20T00:00:00+00:00", "key_id": key_id(key),
                "files": [{"path": name, "size": path.stat().st_size, "sha256": sha256_file(path)} for name, path in files],
                "captured_at": "2026-10-20T00:00:01+00:00"}
    with zipfile.ZipFile(work / "snapshot.zip", "x") as bundle:
        for name, path in files:
            bundle.write(path, name)
        bundle.writestr("backup-manifest.json", json.dumps(manifest))
    encrypt_file(work / "snapshot.zip", directory / "snapshot.enc", key, context=snapshot_id)
    digest, size = sha256_file(directory / "snapshot.enc"), (directory / "snapshot.enc").stat().st_size
    atomic_json(directory / "job.json", {"schema": 1, "snapshot_id": snapshot_id, "environment": "production",
                "created_at": manifest["created_at"], "state": "verified", "key_id": key_id(key), "sha256": digest,
                "size": size, "file_count": 2, "attempts": 1,
                "receipt": {"verification": "download-sha256", "sha256": digest, "size": size}})
    report = restore_job(snapshot_id, tmp_path / "restored-old")
    assert report["database_integrity"] == "ok" and report["checksums_verified"] == 2
    assert cloud_verified(pending_jobs()[0])  # an old record still counts as a daily recovery point


def test_new_archives_keep_every_field_the_previous_reader_requires(setup, tmp_path):
    from src.backup_checkpoints import hourly_checkpoint, hourly_spool
    from src.backup_crypto import decrypt_file
    _root, _, key = setup
    for job, directory in ((hourly_checkpoint(), None), daily_job(setup[0], key, age=0)):
        directory = directory or hourly_spool() / job["snapshot_id"]
        plain = tmp_path / (job["snapshot_id"] + ".zip")
        decrypt_file(directory / "snapshot.enc", plain, key, context=job["snapshot_id"])
        with zipfile.ZipFile(plain) as bundle:
            manifest = json.loads(bundle.read("backup-manifest.json"))
            assert manifest["schema"] == 1 and manifest["snapshot_id"] == job["snapshot_id"]
            assert {"source_root", "created_at", "key_id", "files"} <= set(manifest)
            assert "data/pipeline.db" in {entry["path"] for entry in manifest["files"]}
            assert set(bundle.namelist()) == {entry["path"] for entry in manifest["files"]} | {"backup-manifest.json"}


# --- what the operator sees -------------------------------------------------------------------------------------------

def receipt(**ages):
    current = datetime.now(UTC)
    value = {"schema": 1, "environment": config.BACKUP_ENVIRONMENT, "updated_at": current.isoformat(),
             "local_snapshots": 1, "hourly_copies": 1, "errors": []}
    for key, hours in ages.items():
        value[key] = None if hours is None else (current - timedelta(hours=hours)).isoformat()
    atomic_json(config.BACKUP_STATUS_FILE, value)


class Db:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


class Bot:
    def __init__(self, result=True):
        self.sent, self.result = [], result

    def notify(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self.result


def test_a_27_hour_old_snapshot_is_backup_stale(setup):
    from src.backup_health import health, status_lines
    receipt(snapshot_at=27, drill_at=24 * 3, database_at=0.4)
    result = health()
    assert result["alerts"] == ["backup stale"]
    assert result["line"] == "Last snapshot 27 h / last drill 3 d / hourly copy 24 min"
    assert status_lines() == ["", "<b>Recovery</b>",
                              "🗄 Last snapshot 27 h / last drill 3 d / hourly copy 24 min\n⚠️ backup stale"]


def test_heartbeat_and_status_text_carry_the_line(setup, monkeypatch, tmp_path):
    from src import control
    from src.db import PipelineDatabase
    receipt(snapshot_at=27, drill_at=24 * 3, database_at=0.4)
    db = PipelineDatabase(tmp_path / "fixture.db")
    text = control.heartbeat_text(db, None)
    assert "<b>Recovery</b>" in text and "backup stale" in text and "Last snapshot 27 h / last drill 3 d" in text
    receipt(snapshot_at=2, drill_at=24 * 3, database_at=0.4)
    healthy = control.status_text(db, None)
    assert "Last snapshot 2 h / last drill 3 d / hourly copy 24 min" in healthy and "⚠️ backup" not in healthy


def test_dashboard_shows_a_line_only_while_something_is_overdue(setup, monkeypatch, tmp_path):
    from src import control
    from src.db import PipelineDatabase
    db = PipelineDatabase(tmp_path / "fixture.db")
    receipt(snapshot_at=2, drill_at=24, database_at=0.5)
    assert "Last snapshot" not in control.dashboard_text_and_buttons(db)[0]
    receipt(snapshot_at=27, drill_at=24 * 31, database_at=0.5)
    text = control.dashboard_text_and_buttons(db)[0]
    assert "backup stale; restore drill overdue · Last snapshot 27 h / last drill 31 d" in text


@pytest.mark.parametrize("ages, alerts", [
    ({"snapshot_at": 25, "drill_at": 24 * 29, "database_at": 2.5}, []),
    ({"snapshot_at": 27, "drill_at": 24 * 29, "database_at": 2.5}, ["backup stale"]),
    ({"snapshot_at": 25, "drill_at": 24 * 31, "database_at": 2.5}, ["restore drill overdue"]),
    ({"snapshot_at": 25, "drill_at": 24 * 29, "database_at": 3.5}, ["hourly copy stale"]),
    ({"snapshot_at": 25, "drill_at": None, "database_at": 0.1}, ["restore drill overdue"]),
    ({"snapshot_at": -2, "drill_at": 1, "database_at": 0.1}, ["backup stale"]),  # a future time is no evidence
])
def test_alert_thresholds(setup, ages, alerts):
    from src.backup_health import health
    receipt(**ages)
    assert health()["alerts"] == alerts


def test_a_stage_the_worker_reports_failing_reaches_needs_you_in_production_only(setup, monkeypatch):
    from src.backup_health import health, notify_stale
    receipt(snapshot_at=1, drill_at=24, database_at=0.2)  # every age fresh
    value = json.loads(config.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    atomic_json(config.BACKUP_STATUS_FILE, value | {"errors": ["retention", "unknown-stage", {"x": 1}]})
    assert health()["alerts"] == ["backup worker fault (retention)"]
    bot = Bot()
    assert notify_stale(Db(), bot) is True
    assert "backup worker fault (retention)" in bot.sent[0][0] and bot.sent[0][1] == {"group": "needs"}
    atomic_json(config.BACKUP_STATUS_FILE, value | {"errors": ["hourly", "retention"]})
    assert health()["alerts"] == ["backup worker fault (hourly copy, retention)"]
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "development")
    monkeypatch.setattr(config, "BACKUP_TRANSPORT", "sync-folder")
    quiet = Bot()
    assert health()["alerts"] == [] and notify_stale(Db(), quiet) is False and quiet.sent == []


@pytest.mark.parametrize("problem", ["missing", "foreign", "garbage", "oversized"])
def test_no_trustworthy_receipt_alerts_rather_than_looking_healthy(setup, problem):
    from src.backup_health import health
    receipt(snapshot_at=1, drill_at=1, database_at=0.1)
    path = config.BACKUP_STATUS_FILE
    if problem == "missing":
        path.unlink()
    elif problem == "foreign":
        path.write_text(json.dumps(json.loads(path.read_text()) | {"environment": "development"}))
    elif problem == "garbage":
        path.write_text("{not json")
    else:
        path.write_text(json.dumps(json.loads(path.read_text()) | {"padding": "x" * 9000}))
    result = health()
    assert result["alerts"] == ["backup stale", "restore drill overdue", "hourly copy stale"]
    assert result["line"].endswith("(no status from the backup worker)")


def test_development_without_a_worker_shows_nothing_and_never_notifies(setup, monkeypatch, tmp_path):
    from src import control
    from src.backup_health import notify_stale
    from src.db import PipelineDatabase
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "development")
    monkeypatch.setattr(config, "BACKUP_TRANSPORT", "sync-folder")
    config.BACKUP_STATUS_FILE.unlink(missing_ok=True)
    db = PipelineDatabase(tmp_path / "fixture.db")
    assert "Recovery" not in control.status_text(db, None)
    assert "Last snapshot" not in control.dashboard_text_and_buttons(db)[0]
    bot = Bot()
    assert notify_stale(Db(), bot) is False and bot.sent == []


def test_needs_notice_once_a_day_per_alert_set_and_retried_after_a_failed_send(setup):
    from src.backup_health import notify_stale
    db, current = Db(), datetime.now(UTC)
    receipt(snapshot_at=27, drill_at=24, database_at=0.2)
    failing = Bot(result=False)
    assert notify_stale(db, failing, now_at=current) is False and len(failing.sent) == 1
    bot = Bot()
    assert notify_stale(db, bot, now_at=current) is True
    text, kwargs = bot.sent[0]
    assert kwargs == {"group": "needs"} and "backup stale" in text and "Last snapshot 27 h" in text
    assert notify_stale(db, bot, now_at=current + timedelta(minutes=5)) is False  # same day, same alerts
    receipt(snapshot_at=27, drill_at=24 * 31, database_at=0.2)
    assert notify_stale(db, bot, now_at=current + timedelta(minutes=10)) is True  # a new alert joins
    receipt(snapshot_at=1, drill_at=1, database_at=0.2)
    assert notify_stale(db, bot, now_at=current + timedelta(minutes=15)) is False and db.settings[
        "backup_health_notice"] == ""
    assert len(bot.sent) == 2


def test_daemon_checks_backup_freshness_hourly_and_at_the_heartbeat():
    source = Path(__file__).resolve().parent.parent.joinpath("main.py").read_text(encoding="utf-8")
    assert 'scheduler.add_job(backup_check, "interval", hours=1' in source
    heartbeat = source[source.index("def heartbeat():"):source.index("def backup_check():")]
    assert "backup_check()" in heartbeat
