import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def test_next_topic_prefers_queued_then_failed_and_never_repeats_published(db):
    from src.db import QueueEmpty
    db.record_topic_status("01_topic", "published")
    db.record_topic_status("02_topic", "failed")
    assert db.get_next_topic()["id"] == "03_topic"
    db.record_topic_status("03_topic", "rejected")
    assert db.get_next_topic()["id"] == "02_topic"
    db.record_topic_status("02_topic", "in_production")
    with pytest.raises(QueueEmpty):          # 01 is published: no automatic repeat
        db.get_next_topic()
    db.requeue_topic("01_topic")             # an explicit requeue brings it back
    assert db.get_next_topic()["id"] == "01_topic"


def test_stale_in_production_topic_is_recovered(db):
    for tid in ("01_topic", "02_topic", "03_topic"):
        db.record_topic_status(tid, "rejected")
    db.record_topic_status("02_topic", "in_production")
    with pytest.raises(RuntimeError):
        db.get_next_topic()
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE topics SET updated_at = ? WHERE id = '02_topic'", (old,))
    assert db.get_next_topic()["id"] == "02_topic"


def test_publish_count_only_increments_on_published(db):
    db.record_topic_status("01_topic", "in_production")
    db.record_topic_status("01_topic", "failed")
    db.record_topic_status("01_topic", "published")
    assert db.get_topic_by_id("01_topic")["times_published"] == 1


def test_repair_requeues_topics_without_live_publication(db):
    db.create_run("r1", "01_topic")
    db.record_publication("01_topic", "r1", "dry-run", "simulated", {})
    db.record_topic_status("01_topic", "published")
    db.create_run("r2", "02_topic")
    db.record_publication("02_topic", "r2", "upload-post", "published", {})
    db.record_topic_status("02_topic", "published")
    assert db.repair_simulated_publications() == ["01_topic"]
    assert db.get_topic_by_id("01_topic")["times_published"] == 0
    assert db.get_topic_by_id("02_topic")["status"] == "published"


def test_first_decision_wins(db):
    db.create_run("r1", "01_topic")
    db.record_approval("r1", "01_topic")
    assert db.update_approval_decision("r1", "rejected", decided_by="telegram:1")
    assert not db.update_approval_decision("r1", "approved", decided_by="cli")
    assert db.get_approval("r1")["decision"] == "rejected"


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_run("r1", "no_such_topic")


def test_reseed_does_not_reset_state(db):
    db.record_topic_status("01_topic", "published")
    db._seed_topics_from_json()
    assert db.get_topic_by_id("01_topic")["status"] == "published"


def test_bank_edits_sync_content_but_not_state(db):
    import json
    db.record_topic_status("01_topic", "published")
    bank = json.loads(db.topic_bank_file.read_text(encoding="utf-8"))
    bank["topics"][0]["hook"] = "A corrected hook"
    db.topic_bank_file.write_text(json.dumps(bank), encoding="utf-8")
    db._seed_topics_from_json()
    t = db.get_topic_by_id("01_topic")
    assert t["hook"] == "A corrected hook"
    assert (t["status"], t["times_published"]) == ("published", 1)
def test_only_one_caller_can_claim_publication(db):
    from concurrent.futures import ThreadPoolExecutor
    db.create_run("claim", "01_topic")
    db.update_run("claim", "approved")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: db.claim_publication("claim"), range(4)))
    assert claims.count(True) == 1
    assert db.get_run("claim")["status"] == "publishing"

