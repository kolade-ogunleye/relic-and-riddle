"""Every card a run sends is kept with the run and checked against its records when the run ends
(2026-10-24: Approve on a release-blocked version, "(by -)", a reason cut mid-word and a 79-minute
estimate were all found by the operator, not by the pipeline)."""

import json

from PIL import Image

from src import card_ledger, config, improve
from src.telegram_bot import TelegramApprovalBot
from src.telegram_state import initialize, put, review_fingerprint


def _run_dir(tmp_path, monkeypatch, run_id="run_a"):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    d = tmp_path / run_id
    d.mkdir()
    return d


def _timings(d, minutes=20):
    rows = [{"stage": "script_and_fact_check", "seconds": 300, "at": "2026-10-24T10:05:00Z"},
            {"stage": "review_proxy", "seconds": 15, "at": f"2026-10-24T10:{minutes:02d}:00Z"}]
    (d / "timings.json").write_text(json.dumps(rows), encoding="utf-8")


def test_a_clean_run_has_no_findings(tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch)
    _timings(d)
    (d / "release_report.json").write_text(json.dumps({"status": "pass", "dry_run": True, "checks": [
        {"name": "duration", "status": "pass", "detail": "ok"}]}), encoding="utf-8")
    card_ledger.record("run_a", "progress", "⏳ ... 🔄 <b>Step 1/5:</b> x\n⏱ <b>Elapsed:</b> 00:00 · <b>Time left:</b> ~19 min",
                       [{"text": "Refresh", "callback_data": "cprog:1"}], 5)
    card_ledger.record("run_a", "progress", "✅ <b>Step 4/5:</b> x\n🔄 <b>Step 5/5:</b> y\n<b>Time left:</b> under a minute", None, 5)
    card_ledger.record("run_a", "review", "🎬 review", [[{"text": "Approve", "callback_data": "approve:1"}]], 6)
    card_ledger.record("run_a", "decision", "✅ <b>Approved</b> by Kolade", None, 6)
    assert card_ledger.audit(d) == []


def test_each_mismatch_between_a_card_and_the_record_is_a_finding(tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch)
    _timings(d, minutes=20)
    (d / "release_report.json").write_text(json.dumps({"status": "fail", "dry_run": True, "checks": [
        {"name": "visual_checks", "status": "fail", "detail": [{"issue": "Beat 2: late"}]}]}), encoding="utf-8")
    card_ledger.record("run_a", "progress", "🔄 <b>Step 1/5:</b> x\n<b>Time left:</b> ~79 min", None, 5)
    card_ledger.record("run_a", "progress", "🔄 <b>Step 2/5:</b> x\n<b>Time left:</b> ~60 min", None, 5)
    card_ledger.record("run_a", "review", "🎬 review", [[{"text": "Approve", "callback_data": "approve:1"}]], 6)
    card_ledger.record("run_a", "decision", "🎬 <b>APPROVAL RECORDED</b> (by -)", None, 6)
    card_ledger.record("run_a", "failure", "❌ failed\n\nThe script writer made 3 attempts for 'kai…\n\nNothing was posted.", None, 5)
    found = dict(card_ledger.audit(d))
    assert set(found) == {"ui.approve_on_blocked", "ui.placeholder_name", "ui.cut_short", "ui.estimate", "ui.progress_stale"}
    assert "visual_checks" in found["ui.approve_on_blocked"]
    assert "'by -'" in found["ui.placeholder_name"]
    assert "'kai" in found["ui.cut_short"]
    assert "promised ~79 min; the run took 20 min" in found["ui.estimate"]  # 10:00 (first stage start) to 10:20
    assert "step 2 of 5" in found["ui.progress_stale"]


def test_an_unfinished_run_is_not_judged_on_time_or_steps(tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch)
    (d / "timings.json").write_text(json.dumps([{"stage": "script_and_fact_check", "seconds": 300, "at": "2026-10-24T10:05:00Z"}]),
                                    encoding="utf-8")
    card_ledger.record("run_a", "progress", "🔄 <b>Step 1/5:</b> x\n<b>Time left:</b> ~79 min", None, 5)
    assert card_ledger.audit(d) == []


def test_the_bot_keeps_each_card_it_sends_with_the_run(db, tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch, "review")
    initialize(db)
    topic = db.list_all_topics()[0]["id"]
    (d / "master.mp4").write_bytes(b"video")
    Image.new("RGB", (20, 20)).save(d / "thumbnail_cover.jpg")
    db.create_run("review", topic)
    db.update_run("review", "pending_approval", script_payload={"caption": "c", "title": "T"}, master_video_path=d / "master.mp4")
    db.record_approval("review", topic, None)
    put(db, "review-media", "review", review_fingerprint(db, "review"))
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda method, **kw: {"message_id": 300})
    bot.send_review_card("review", {"title": "T", "caption": "c"}, {"dry_run": True, "checks": [{"name": "duration", "status": "pass", "detail": "ok"}]})
    bot._render_decision({"from": {"id": 555, "first_name": "Kolade"}, "message": {"message_id": 300}}, "review", "approved", 555)
    db.update_run("review", "producing")
    bot.send_progress_card("review", "T")
    db.update_run("review", "failed", error="x")
    bot.finish_progress_card(300, "T", "review", success=False, error="Release blocked: visual_checks: Beat 2")
    kinds = [c["kind"] for c in card_ledger.cards(d)]
    assert kinds == ["review", "decision", "progress", "failure"]
    review = card_ledger.cards(d)[0]
    assert any(b.startswith("approve:") for b in review["buttons"]) and review["message_id"] == 300


def test_run_end_files_the_self_audit_and_long_script_rounds(db, tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch)
    card_ledger.record("run_a", "decision", "❌ <b>Rejected</b> by -", None, 6)
    payload = {"script_rounds": 4, "script_round_reasons": ["REJECTED: too short", "REJECTED by fact checker: 'x'", "REJECTED: y"]}
    improve.collect_run(db, "run_a", d, payload, topic_id="t")
    with db._conn() as conn:
        rows = {r["klass"]: r["detail"] for r in conn.execute("select klass, detail from findings where run_id='run_a'")}
    assert rows["ui.placeholder_name"].endswith("where a name should be")
    assert rows["script.rounds"].startswith("4 drafts: REJECTED: too short | REJECTED by fact checker")


def test_a_line_said_twice_on_one_card_is_a_finding(tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch)
    label = '"The Unsolved 1,070 MPa Steel of Damascus" (Damascus Old City) · 24 Oct 07:00'
    card_ledger.record("run_a", "cancelled", f"⏹ <b>Render cancelled:</b> {label}\n<code>[{label}]</code>\n\nStopped.", None, 5)
    found = dict(card_ledger.audit(d))
    assert found["ui.repeated_line"].startswith("A cancelled card says '\"The Unsolved 1,070 MPa Steel of Damascus\"")


def test_the_cancelled_card_names_the_run_once(db, tmp_path, monkeypatch):
    d = _run_dir(tmp_path, monkeypatch, "run_c")
    db.create_run("run_c", db.list_all_topics()[0]["id"])
    db.update_run("run_c", "cancelled")
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    sent = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: sent.append(kw.get("data", {})) or {"message_id": 300})
    bot.finish_progress_card(5, db.run_label("run_c"), "run_c", success=False, error="")
    text = sent[-1]["text"]
    import html
    assert text.count(html.escape(db.run_label("run_c"))) == 1 and "Run ID: run_c" in text
    assert card_ledger.audit(d) == [] and card_ledger.cards(d)[0]["kind"] == "cancelled"


def test_a_re_rendered_runs_length_is_its_last_session(tmp_path, monkeypatch):
    rows = [{"stage": "script_and_fact_check", "seconds": 300, "at": "2026-10-24T09:17:00Z"},
            {"stage": "review_proxy", "seconds": 15, "at": "2026-10-24T09:43:00Z"},          # the first session: 31 min
            {"stage": "voiceover", "seconds": 0.1, "at": "2026-10-24T11:28:22Z"},            # hours later: the re-render
            {"stage": "review_proxy", "seconds": 17, "at": "2026-10-24T11:49:44Z"}]
    assert round(card_ledger.run_seconds(rows) / 60) == 21                                  # not 2 h 37
    assert card_ledger.run_seconds(rows[:3]) is None                                         # the last session unfinished
