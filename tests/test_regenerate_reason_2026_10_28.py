"""
tests/test_regenerate_reason_2026_10_28.py
==========================================
Production, 2026-10-26 (Roman concrete): Regenerate at 14:22:32, "Other" at 14:22:41, the new run began at
14:22:45 and the operator's words came at 14:22:52; the script was kept word for word and the same defect
was rebuilt three runs in a row. The regeneration now waits for the words under "Other" and says on the card
what it keeps and redoes. Also: every approval path meets the release gate the card applies (dev, 2026-10-27:
Trepanation approved seven times from the CLI, refused at release each time), and the blocked card lists what
blocks first. Driven through the real bot plumbing (_process_update, drain), as tests/test_archive_film_e2e.py.
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import main
from src import improve

FIXTURE = Path(__file__).parent / "fixtures" / "release_report_trepanation_2026-10-26.json"


def _bot(db, calls):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._api = lambda method, **kw: calls.append((method, kw.get("data") or {})) or {"message_id": 900}
    return bot


def _tap(bot, data, handler, message_id=70, update_id=1):
    bot._process_update({"update_id": update_id, "callback_query": {"id": f"q{update_id}", "data": data, "from": {"id": 555},
                                                                      "message": {"message_id": message_id, "chat": {"id": 555}}}}, handler)
    bot.drain()


def _reply(bot, text, card_text, handler, message_id=301):
    bot._process_update({"update_id": message_id, "message": {
        "message_id": message_id, "text": text, "date": int(time.time()), "from": {"id": 555}, "chat": {"id": 555},
        "reply_to_message": {"message_id": 70, "text": card_text}}}, handler)
    bot.drain()


def _regenerated(db, run_id="run_roman_1"):
    tid = db.list_all_topics()[0]["id"]
    db.create_run(run_id, tid)
    db.record_approval(run_id, tid, 70)
    assert db.update_approval_decision(run_id, "regenerate", decided_by="telegram:555")
    return run_id


def _coordinator(db, bot):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.telegram_bot = db, bot
    return c


class _Clock:
    """main's time, on a fake clock: each sleep moves it on and lets the operator act at a given second."""
    def __init__(self, events):
        self.now, self.events = 0.0, sorted(events.items())

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        while self.events and self.events[0][0] <= self.now:
            self.events.pop(0)[1]()


def _handler(db, tmp_path):
    from src.control import make_command_handler
    return make_command_handler(db, lambda: None, lambda *a, **k: "", assets_dir=tmp_path)


def _texts(calls, method="editMessageText"):
    return [d.get("text") or "" for m, d in calls if m == method]


# ------------------------------------------------------------------ FIX 1: the reason's words are waited for
def test_other_waits_for_the_words_and_they_decide_the_script_is_revised(db, tmp_path, monkeypatch):
    run_id = _regenerated(db)
    calls = []
    bot, handler = _bot(db, calls), _handler(db, tmp_path)
    key = db.run_key(run_id)
    card = f"🎬 Topic 1\n\n✅ Reason: Other. Reply to this card to add details. [run {key}]"
    clock = _Clock({9: lambda: _tap(bot, f"rsn:{key}:other", handler),
                    20: lambda: _reply(bot, "Repeated phrases in the voiceover and a cut-off Story text", card, handler)})
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=time.time))
    fb = _coordinator(db, bot)._await_reason(run_id)
    assert fb["reason"] == "other" and "voiceover" in fb["text"] and 20 <= clock.now < 30   # ended when the words came
    assert main.revises_script(fb)                         # so _produce_and_publish revises, not keeps, the script
    # The tapped chip said it would wait; the decided card then says what the new version redoes.
    assert any("waits for your words" in t for t in _texts(calls))
    plan = [t for t in _texts(calls) if "New version starting" in t][-1]
    assert "Redoes the <b>script</b>" in plan and "Repeated phrases" in plan and f"[run {key}]" in plan
    assert "✅ Reason:" in plan                            # still a reason card: a reply adds details


def test_other_without_words_waits_only_the_bounded_time_then_keeps_the_script(db, tmp_path, monkeypatch):
    from src.telegram_bot import REGEN_DETAIL_SECONDS
    run_id = _regenerated(db)
    calls = []
    bot, handler = _bot(db, calls), _handler(db, tmp_path)
    clock = _Clock({6: lambda: _tap(bot, f"rsn:{db.run_key(run_id)}:other", handler)})
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=time.time))
    fb = _coordinator(db, bot)._await_reason(run_id)
    assert fb["reason"] == "other" and not fb.get("text")
    assert 6 + 10 <= clock.now <= REGEN_DETAIL_SECONDS + 9          # bounded, counted from the Regenerate tap
    plan = [t for t in _texts(calls) if "New version starting" in t][-1]
    assert "Keeps the <b>script word for word</b>" in plan and "needs them to say so" in plan


def test_a_specific_reason_does_not_wait_for_words(db, tmp_path, monkeypatch):
    run_id = _regenerated(db)
    calls = []
    bot, handler = _bot(db, calls), _handler(db, tmp_path)
    clock = _Clock({3: lambda: _tap(bot, f"rsn:{db.run_key(run_id)}:pictures", handler)})
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=time.time))
    fb = _coordinator(db, bot)._await_reason(run_id)
    assert fb["reason"] == "pictures" and clock.now == 3
    assert not any("waits for your words" in t for t in _texts(calls))
    assert "Redoes the <b>pictures</b>" in [t for t in _texts(calls) if "New version starting" in t][-1]


def test_a_regeneration_started_long_after_the_tap_waits_only_briefly(db):
    run_id = _regenerated(db)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET decision_timestamp = ? WHERE run_id = ?", ("2026-10-26T14:22:32+00:00", run_id))
    assert main.reason_window(db, run_id, 120) == 10
    assert main.reason_window(db, "unknown", 120) == 10


def test_the_plan_says_what_each_reason_keeps_and_redoes():
    assert "new script" in main.regeneration_plan({})
    assert "Redoes the <b>script</b>" in main.regeneration_plan({"reason": "facts"})
    assert "Redoes the <b>script</b>" in main.regeneration_plan({"reason": "other", "text": "the narration repeats"})
    assert "Keeps the <b>script word for word</b>" in main.regeneration_plan({"reason": "other", "text": "too dark"})
    sound = main.regeneration_plan({"reason": "sound"})
    assert "different track" in sound and "music bed carry" not in sound


# ------------------------------------------------------------------ FIX 2: every approval meets the gate
def _blocked_run(db, tmp_path, run_id="run_trep_1"):
    tid = db.list_all_topics()[0]["id"]
    db.create_run(run_id, tid)
    master = tmp_path / run_id / "master.mp4"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"\0")
    (master.parent / "release_report.json").write_bytes(FIXTURE.read_bytes())
    db.update_run(run_id, "pending_approval", master_video_path=str(master))
    db.set_setting("run_mode:" + run_id, "dry_run")
    db.record_approval(run_id, tid, 71)
    return run_id


def test_cli_approve_refuses_a_version_the_gate_blocks_and_says_why(db, tmp_path, monkeypatch, capsys):
    run_id = _blocked_run(db, tmp_path)
    monkeypatch.setattr(main, "PipelineDatabase", lambda *a, **k: db)
    monkeypatch.setattr("src.release.write_release_report", lambda run, dry_run=False: json.loads(FIXTURE.read_text(encoding="utf-8")))
    assert main.main(["review", "approve", run_id]) == 1
    out = capsys.readouterr().out
    assert "blocked at the release gate" in out and "visual_checks" in out and "accession" in out
    assert db.get_approval(run_id)["decision"] == "pending"          # never recorded as approved; still open
    assert main.main(["review", "publish", run_id]) == 1
    assert db.get_approval(run_id)["decision"] == "pending"


def test_cli_approve_of_a_passing_version_still_approves(db, tmp_path, monkeypatch):
    run_id = _blocked_run(db, tmp_path)
    monkeypatch.setattr(main, "PipelineDatabase", lambda *a, **k: db)
    monkeypatch.setattr("src.release.write_release_report", lambda run, dry_run=False: {"checks": [{"name": "duration", "status": "pass"}]})
    monkeypatch.setattr("src.telegram_bot.TelegramApprovalBot.dismiss_approval_buttons", lambda self, r: None)
    assert main.main(["review", "approve", run_id]) == 0
    assert db.get_approval(run_id)["decision"] == "approved"


def test_the_bots_local_decision_refuses_a_blocked_version(db, tmp_path):
    import pytest
    run_id = _blocked_run(db, tmp_path)
    bot = _bot(db, [])
    with pytest.raises(ValueError, match="visual_checks"):
        bot.record_local_decision(run_id, "approved")
    assert db.get_approval(run_id)["decision"] == "pending"
    assert bot.record_local_decision(run_id, "rejected")             # the other decisions stay open


def test_the_auto_approve_flag_records_a_blocked_run_as_blocked_at_the_gate(db, tmp_path):
    run_id = _blocked_run(db, tmp_path)
    alerts, topics = [], []
    c = _coordinator(db, SimpleNamespace(notify=lambda text, buttons=None: alerts.append(text)))
    c.topic_engine = SimpleNamespace(record_status=lambda tid, status, run_id=None: topics.append(status))
    why = c._blocked_at_gate(run_id, json.loads(FIXTURE.read_text(encoding="utf-8")), True, "auto-approve flag")
    run = db.get_run(run_id)
    assert why and run["status"] == "failed" and run["error"].startswith("Blocked at the release gate, not approved")
    assert "visual_checks" in run["error"] and db.get_approval(run_id)["decision"] == "pending" and topics == ["failed"]
    assert "Blocked at the release gate" in alerts[0]
    assert c._blocked_at_gate(run_id, {"checks": [{"name": "x", "status": "pass"}]}, True, "flag") is None


# ------------------------------------------------------------------ the blocked card shows what blocks
def test_the_blocked_card_lists_the_blocking_check_first(db):
    from src.telegram_bot import TelegramApprovalBot
    report = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for cap in (1, 2, 4):
        lines = TelegramApprovalBot.release_lines(report, cap=cap)
        assert "blocked" in lines and "visual_checks" in lines and "accession" in lines
    full = TelegramApprovalBot.release_lines(report)
    assert full.index("visual_checks") < full.index("asset_rights") and "asset_rights (not blocking)" in full
    card = TelegramApprovalBot.format_card_text({"title": "Trepanation", "narration_script": "Words. " * 200,
                                                 "caption": "c " * 400}, "run_trep_1", release=report)
    assert "visual_checks" in card
