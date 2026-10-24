"""📚 Review history (operator, 2026-10-24): past blocked reviews with their reasons, to address whenever it
suits; past approved reviews with their self-audit and audience numbers, to see what did well. Built from
the runs' own records, nothing new is stored."""

import html
import json

from src import config
from src.control import make_command_handler, review_history


def _three_runs(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    topics = [t["id"] for t in db.list_all_topics()][:3]
    for run_id, topic in zip(("run_a", "run_b", "run_c"), topics):
        db.create_run(run_id, topic)
        (tmp_path / run_id).mkdir()
    db.update_run("run_a", "rejected", master_video_path=str(tmp_path / "run_a" / "m.mp4"))
    db.set_setting("decision_reason:run_a", json.dumps({"reason": "pictures", "text": "wrong temple on beat 5"}))
    db.update_run("run_b", "failed", error="release gate", master_video_path=str(tmp_path / "run_b" / "m.mp4"))
    (tmp_path / "run_b" / "release_report.json").write_text(json.dumps({"checks": [
        {"name": "visual_checks", "status": "fail", "detail": [{"issue": "beat 2: caption clipped at the edge", "severity": "major"}]}]}),
        encoding="utf-8")
    db.update_run("run_c", "simulated", master_video_path=str(tmp_path / "run_c" / "m.mp4"))
    return topics


def test_history_lists_blocked_runs_with_their_reasons_and_approved_runs_with_their_audit(db, tmp_path, monkeypatch):
    _three_runs(db, tmp_path, monkeypatch)
    text, buttons = review_history(db)
    blocked, approved = text.split("✅ <b>Approved</b>")
    assert "you: pictures · wrong temple on beat 5" in blocked                 # the operator's own reason
    assert "visual_checks: beat 2: caption clipped at the edge" in blocked     # the release gate's line
    assert html.escape(db.run_label("run_c")) in approved and "self-audit clean" in approved
    callbacks = [b["callback_data"] for row in buttons for b in row]
    # the newest late failure also gets Make again (the dashboard's Re-render), as the dashboard offers it
    assert callbacks == [f"chist:{db.run_key('run_b')}", f"rerender:{db.run_key('run_b')}",
                         f"chist:{db.run_key('run_a')}", f"chist:{db.run_key('run_c')}"]


def test_the_reviews_panel_offers_the_history_whether_or_not_anything_waits(db, tmp_path, monkeypatch):
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("creviews", "")                                          # an empty history still opens
    assert text == "No reviews are waiting." and buttons[0][0]["callback_data"] == "chistory:"
    _three_runs(db, tmp_path, monkeypatch)
    text, buttons = h("creviews", "")                                          # the simulated run is a post candidate
    assert "Post candidates" in text and "chistory:" in [b["callback_data"] for row in buttons for b in row]
    text, buttons = h("chistory", "")
    assert text.startswith("📚 <b>Review history</b>") and buttons[-2][0]["callback_data"] == "creviews:"


def test_details_of_a_blocked_run_name_every_reason_and_the_run_id(db, tmp_path, monkeypatch):
    _three_runs(db, tmp_path, monkeypatch)
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("chist", db.run_key("run_b"))
    assert "Why it was blocked" in text and "visual_checks: beat 2: caption clipped at the edge" in text
    assert "<code>run_b</code>" in text and buttons[0][0]["callback_data"] == "chistory:"
    text, _ = h("chist", db.run_key("run_c"))
    assert "How it did" in text and "self-audit: clean" in text and "no numbers yet" in text
