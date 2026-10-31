"""Every topic says where it came from: the bank keeps the record and the review card shows it."""
import json

from src.db import PipelineDatabase
from src.topic_proposer import added_record, origin_note


def test_the_origin_is_kept_through_the_bank_sync_and_shown(tmp_path):
    bank = tmp_path / "topic_bank.json"
    topics = [{"id": "a_topic", "title": "A", "hook": "h", "location": {}},
              {"id": "b_topic", "title": "B", "hook": "h", "location": {},
               "added": added_record("research proposal", "approved in Telegram (3 sources)")}]
    bank.write_text(json.dumps({"topics": topics}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp_path / "t.db", topic_bank_file=bank)
    a, b = db.get_topic_by_id("a_topic"), db.get_topic_by_id("b_topic")
    assert origin_note(a).startswith("🗂 Topic origin: the original topic bank")
    assert "research proposal, approved in Telegram (3 sources), 20" in origin_note(b)
