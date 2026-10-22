"""Operator-facing times use DEFAULT_TIMEZONE; stored times stay UTC; post slots keep the audience zone."""
from datetime import datetime, timezone

from src import config, local_time, post_schedule


def test_stored_utc_is_shown_in_the_operator_zone(monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "Europe/London")
    assert local_time.show("2026-10-22T22:16:46+00:00") == "22 Oct 23:16 BST"
    assert local_time.show("2026-10-22 22:16:46") == "22 Oct 23:16 BST"        # naive text is UTC
    assert local_time.show(1793052000, "%d %b") == local_time.local(1793052000).strftime("%d %b")
    assert local_time.show("not a time") == "not a time"


def test_post_slot_adds_operator_time_only_when_zones_differ(monkeypatch):
    slot = datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)                  # 12:00 in New York
    monkeypatch.setattr(config, "POST_TIMEZONE", "America/New_York")
    monkeypatch.setattr(config, "TIMEZONE", "America/New_York")
    assert post_schedule.fmt(slot) == "Fri 23 Oct 12:00 EDT"
    monkeypatch.setattr(config, "TIMEZONE", "Europe/London")
    assert post_schedule.fmt(slot) == "Fri 23 Oct 12:00 EDT (17:00 BST your time)"


def test_run_label_and_jobs_use_the_operator_zone(db, monkeypatch):
    monkeypatch.setattr(config, "TIMEZONE", "Asia/Tokyo")
    db.create_run("r1", "01_topic")
    with db._conn() as conn:
        conn.execute("UPDATE runs SET created_at='2026-10-22T22:00:00+00:00' WHERE run_id='r1'")
    assert db.run_label("r1").endswith("23 Oct 07:00")
