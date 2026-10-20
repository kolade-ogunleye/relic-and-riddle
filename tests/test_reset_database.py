"""scripts/reset_database.py: a fresh database with a verified backup, keeping this month's
spend (the monthly caps must still count it) and pending topic proposals."""
import sqlite3
from datetime import datetime, timezone

from scripts.reset_database import reset


def _seed(db):
    db.create_run("r1", "01_topic")
    with db._conn() as conn:
        conn.execute("INSERT INTO media_usage (provider, item, cost_usd, created_at) VALUES "
                     "('kling-3.0', 'motion_01.mp4', 0.84, '2026-10-19T20:53:22+00:00'), "
                     "('kling-3.0', 'old.mp4', 0.84, '2026-09-01T00:00:00+00:00')")
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('topic_proposals_pending', '{\"x\": 1}'), "
                     "('scheduler_paused', 'true')")


def test_dry_run_changes_nothing(db, tmp_path):
    _seed(db)
    reset(db.db_path, tmp_path / "archive", apply=False, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert not (tmp_path / "archive").exists()
    assert sqlite3.connect(db.db_path).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_apply_backs_up_rebuilds_and_keeps_this_months_spend(db, tmp_path):
    _seed(db)
    backup = reset(db.db_path, tmp_path / "archive", apply=True, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    old = sqlite3.connect(backup)
    assert old.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1  # the backup is complete
    new = sqlite3.connect(db.db_path)
    assert new.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert new.execute("SELECT item, cost_usd FROM media_usage").fetchall() == [("motion_01.mp4", 0.84)]
    assert new.execute("SELECT id FROM media_usage").fetchone()[0] == 1  # counters start again
    settings = dict(new.execute("SELECT key, value FROM settings").fetchall())
    assert settings.get("topic_proposals_pending") == '{"x": 1}' and "scheduler_paused" not in settings
    assert {s for (s,) in new.execute("SELECT DISTINCT status FROM topics")} == {"queued"}


def test_wipe_keeps_nothing_and_makes_no_backup(db, tmp_path):
    _seed(db)
    assert reset(db.db_path, tmp_path / "archive", apply=True, wipe=True) is None
    assert not (tmp_path / "archive").exists()
    new = sqlite3.connect(db.db_path)
    assert new.execute("SELECT COUNT(*) FROM media_usage").fetchone()[0] == 0
    assert new.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    assert new.execute("SELECT COUNT(*) FROM sqlite_sequence WHERE seq > 0").fetchone()[0] == 0
