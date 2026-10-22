import json
import sqlite3

import pytest

from scripts.archive_validation_runs import archive_runs
from src.db import PipelineDatabase


def fixture_root(tmp_path):
    (tmp_path / "data").mkdir()
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "test_topic", "title": "Test"}]}))
    db = PipelineDatabase(tmp_path / "data" / "pipeline.db", bank)
    db.create_run("validation_1", "test_topic")
    db.update_run("validation_1", "simulated")
    db.create_run("live_1", "test_topic")
    db.update_run("live_1", "published")
    with db._conn() as conn:
        conn.execute("INSERT INTO llm_usage(model,cost_usd,created_at) VALUES('test',1.25,'2026-10-21')")
        conn.execute("INSERT INTO settings(key,value) VALUES('telegram_offset','123')")
        conn.execute("UPDATE topics SET last_run_id='validation_1',status='failed'")
    folder = tmp_path / "output" / "validation_1"
    folder.mkdir(parents=True)
    (folder / "release_report.json").write_text('{"dry_run":true}')
    return db


def test_preview_changes_nothing_and_apply_preserves_live_state_and_spend(tmp_path):
    db = fixture_root(tmp_path)
    preview = archive_runs(tmp_path, ["validation_1"])
    assert preview["applied"] is False
    assert db.get_run("validation_1")
    result = archive_runs(tmp_path, ["validation_1"], apply=True, workers_stopped=True)
    with db._conn() as conn:
        assert conn.execute("SELECT run_id FROM runs").fetchall()[0][0] == "live_1"
        assert conn.execute("SELECT SUM(cost_usd) FROM llm_usage").fetchone()[0] == 1.25
        assert conn.execute("SELECT value FROM settings WHERE key='telegram_offset'").fetchone()[0] == "123"
        topic = conn.execute("SELECT status,last_run_id FROM topics").fetchone()
        assert tuple(topic) == ("queued", None)
    archive = __import__("pathlib").Path(result["archive"])
    assert (archive / "validation_1" / "release_report.json").is_file()
    with sqlite3.connect(archive / "pipeline.db") as backup:
        assert backup.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    assert not (tmp_path / "output" / "validation_1").exists()


def test_refuses_real_or_uncertain_publication_and_live_runs(tmp_path):
    db = fixture_root(tmp_path)
    with pytest.raises(ValueError, match="Only simulated"):
        archive_runs(tmp_path, ["live_1"], apply=True, workers_stopped=True)
    with db._conn() as conn:
        conn.execute("INSERT INTO publications(topic_id,run_id,channel,status,created_at) "
                     "VALUES('test_topic','validation_1','meta','publish_unknown','2026-10-21')")
    with pytest.raises(ValueError, match="publication evidence"):
        archive_runs(tmp_path, ["validation_1"], apply=True, workers_stopped=True)
    assert (tmp_path / "output" / "validation_1").is_dir()


def test_requires_stopped_workers_and_explicit_failed_selection(tmp_path):
    db = fixture_root(tmp_path)
    with pytest.raises(ValueError, match="Stop workers"):
        archive_runs(tmp_path, ["validation_1"], apply=True)
    db.update_run("validation_1", "failed")
    with pytest.raises(ValueError, match="Only simulated"):
        archive_runs(tmp_path, ["validation_1"])
    assert archive_runs(tmp_path, ["validation_1"], include_failed=True)["rows"]["runs"] == 1


def test_failed_archive_restores_database_and_moved_output(tmp_path):
    db = fixture_root(tmp_path)
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT,'disk error'); END")
    # The files have already moved when this transaction fails. Both must recover.
    with pytest.raises(sqlite3.IntegrityError, match="disk error"):
        archive_runs(tmp_path, ["validation_1"], apply=True, workers_stopped=True)
    assert db.get_run("validation_1")["status"] == "simulated"
    assert (tmp_path / "output" / "validation_1").is_dir()
