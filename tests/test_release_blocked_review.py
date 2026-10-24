"""A version the release gate blocks says why on its review card and is not offered for approval; the
timing finding that blocked one is repaired by re-planning before review; decision and failure texts
name what happened in full (production, 2026-10-24: Approve led to "Release blocked (visual_checks)",
the stamp read "(by -)" and the proposal's examples were cut mid-word)."""

import json

from PIL import Image

from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import initialize, put, review_fingerprint

DESYNC = {"type": "temporal_desync", "severity": "minor", "scene_index": 1,
          "issue": "Beat 2: the graphic graphic_map.mp4 is on screen 2.4 s after the narration names it (allowed: 1.2 s)",
          "asset": "C:/run/graphics/graphic_map.mp4"}


def _report(visual_status="fail"):
    return {"status": visual_status, "checks": [
        {"name": "duration", "status": "pass", "detail": "Measured 46.567s; expected 42.0-58.0s."},
        {"name": "visual_checks", "status": visual_status, "detail": [DESYNC] if visual_status != "pass" else []},
    ]}


def _review(db, tmp_path):
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    (tmp_path / "master.mp4").write_bytes(b"offline review video")
    Image.new("RGB", (20, 20)).save(tmp_path / "thumbnail_cover.jpg")
    db.create_run("review", topic)
    db.update_run("review", "pending_approval", script_payload={"caption": "Original", "title": "T"},
                  master_video_path=tmp_path / "master.mp4")
    db.record_approval("review", topic, None)
    put(db, "review-media", "review", review_fingerprint(db, "review"))


def _bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300})
    return bot, calls


def _card(calls):
    data = [d for m, d in calls if m == "sendMessage"][-1]
    buttons = [b["callback_data"] for row in json.loads(data["reply_markup"])["inline_keyboard"] for b in row]
    return data["text"], buttons


def test_require_release_names_what_was_found(monkeypatch):
    import pytest
    from src import release
    monkeypatch.setattr(release, "write_release_report", lambda run, dry_run=False: _report())
    with pytest.raises(ValueError) as err:
        release.require_release({}, dry_run=True)
    assert str(err.value) == "Release blocked: visual_checks: " + DESYNC["issue"]
    monkeypatch.setattr(release, "write_release_report", lambda run, dry_run=False: _report("pass"))
    assert release.require_release({}, dry_run=True)["status"] == "pass"


def test_blocked_version_has_no_approve_and_says_why(db, tmp_path, monkeypatch):
    _review(db, tmp_path)
    bot, calls = _bot(db, monkeypatch)
    bot.send_review_card("review", {"title": "T", "caption": "Original"}, _report())
    text, buttons = _card(calls)
    assert not any(b.startswith("approve:") for b in buttons)
    assert any(b.startswith(p) for b in buttons for p in ("edit:",)) and any(b.startswith("reject:") for b in buttons)
    assert "⛔ <b>Release gate:</b> blocked" in text
    assert "is on screen 2.4 s after the narration names it" in text and "'}" not in text  # the sentence, not the dict


def test_passing_version_keeps_approve(db, tmp_path, monkeypatch):
    _review(db, tmp_path)
    bot, calls = _bot(db, monkeypatch)
    bot.send_review_card("review", {"title": "T", "caption": "Original"}, _report("pass"))
    text, buttons = _card(calls)
    assert any(b.startswith("approve:") for b in buttons) and "✅ all checks pass" in text


def test_timing_finding_is_repaired_by_replanning():
    from src.video_qa import audit_scene_feedback
    line = audit_scene_feedback({"issues": [DESYNC]})
    assert line.startswith("Beat 2:") and "graphic_map.mp4" in line
    assert "beat where those words are spoken" in line


def test_decision_stamp_is_sentence_case_and_never_a_placeholder_name(db, tmp_path, monkeypatch):
    _review(db, tmp_path)
    bot, calls = _bot(db, monkeypatch)
    cq = {"from": {"id": 555, "first_name": "-"}, "message": {"message_id": 9, "text": "card"}}
    bot._render_decision(cq, "review", "approved", 555)
    edited = [d["text"] for m, d in calls if m == "editMessageText"][-1]
    assert "✅ <b>Approved</b> by you" in edited and "(by -)" not in edited and "APPROVAL RECORDED" not in edited
    calls.clear()
    bot._render_decision({"from": {"id": 555, "first_name": "-", "username": "kolade"}, "message": {"message_id": 9}},
                         "review", "approved", 555)
    assert "by kolade" in [d["text"] for m, d in calls if m == "editMessageText"][-1]


def test_failure_card_folds_the_whole_reason(db, tmp_path, monkeypatch):
    _review(db, tmp_path)
    bot, calls = _bot(db, monkeypatch)
    reason = "Release blocked: visual_checks: " + DESYNC["issue"] + " " + "detail " * 120
    bot.finish_progress_card(5, "T", "review", success=False, error=reason)
    text = [d["text"] for m, d in calls if m == "editMessageText"][-1]
    assert "<blockquote expandable>Release blocked: visual_checks: Beat 2" in text and "detail detail" in text


def test_proposal_examples_are_whole_and_folded():
    from src.improve import card
    long = "The script writer made 3 attempts and each failed its checks (last: Narration runs 35.8s when spoken " \
           "(minimum 42s); add about 18 words) " + "x" * 150 + " END"
    text, _ = card({"id": "imp_1", "title": "Recurring failure", "tier": "code", "evidence": {"examples": [long]}})
    assert "<blockquote expandable>• " in text and text.count("END") == 1


def test_minor_findings_block_release_unless_the_operator_switches_that_off(tmp_path, monkeypatch):
    # The operator's choice (2026-10-24): by default any finding blocks; with MINOR_FINDINGS_BLOCK_RELEASE
    # off, a master whose only findings are minor passes the gate with them shown on the card.
    from src import config, release
    from src.credits import sha256_file
    master = tmp_path / "master.mp4"
    master.write_bytes(b"video")
    minor = {"type": "temporal_desync", "severity": "minor", "issue": "Beat 3: the graphic graphic_map.mp4 is on screen 1.5 s after the narration names it"}
    checks = []
    captured = {}

    def fake_write(run, dry_run=False):  # only the visual_checks entry is under test
        quality = {"certified": False, "master_sha256": sha256_file(master), "issues": run["issues"]}
        issues = quality["issues"]
        audited = quality["master_sha256"] == sha256_file(master)
        minor_only = bool(issues) and all(i.get("severity") == "minor" for i in issues)
        if audited and minor_only and not config.MINOR_FINDINGS_BLOCK_RELEASE:
            entry = {"name": "visual_checks", "status": "pass", "detail": issues, "minor": True}
        else:
            entry = {"name": "visual_checks", "status": "fail", "detail": issues}
        return {"status": entry["status"], "dry_run": dry_run, "checks": [entry]}
    monkeypatch.setattr(release, "write_release_report", fake_write)
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", True)
    import pytest
    with pytest.raises(ValueError, match="visual_checks"):
        release.require_release({"issues": [minor]}, dry_run=True)
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", False)
    report = release.require_release({"issues": [minor]}, dry_run=True)
    assert report["checks"][0]["status"] == "pass" and report["checks"][0]["minor"]
    line = TelegramApprovalBot.release_lines(report)
    assert "all checks pass · minor findings, not blocking" in line and "1.5 s after the narration" in line
    with pytest.raises(ValueError):                              # a major finding still blocks
        release.require_release({"issues": [dict(minor, severity="major")]}, dry_run=True)
