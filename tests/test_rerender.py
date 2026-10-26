"""A run that failed after its video was made (2026-10-24: the release gate blocked the approved Kailasa
master) is re-rendered in place from Telegram, by the daemon's own worker, reusing what it paid for:
before, only `main.py resume` as the pipeline account could do it."""

import json
from types import SimpleNamespace

import main
from src.control import dashboard_text_and_buttons, make_command_handler
from src.telegram_bot import TelegramApprovalBot


def _failed(db, tmp_path, monkeypatch, with_video=True):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    from src import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)  # read by the card and the dashboard
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_k", topic)
    (tmp_path / "run_k").mkdir()
    if with_video:  # a late failure: its script passed the fact check and its video exists (src/resume_points.py)
        (tmp_path / "run_k" / "verified_script.json").write_text("{}", encoding="utf-8")
        (tmp_path / "run_k" / "voiceover.mp3").write_bytes(b"x")
        (tmp_path / "run_k" / "master.mp4").write_bytes(b"x")
        (tmp_path / "run_k" / "checkpoints.json").write_text(json.dumps({"script": {}, "voice": {}}), encoding="utf-8")
    fields = {"master_video_path": tmp_path / "run_k" / "master.mp4"} if with_video else {}
    db.update_run("run_k", "failed", error="ValueError: Release blocked: visual_checks: Beat 2: ...", **fields)
    return "run_k"


def _bot(db, monkeypatch):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300})
    return bot, calls


def _buttons(markup):
    return [b["callback_data"] for row in json.loads(markup)["inline_keyboard"] for b in row]


def test_a_late_failure_card_offers_re_render(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch)
    bot, calls = _bot(db, monkeypatch)
    bot.finish_progress_card(5, "T", run_id, success=False, error="Release blocked: visual_checks: Beat 2")
    data = [d for m, d in calls if m == "editMessageText"][-1]
    assert f"rerender:{db.run_key(run_id)}" in _buttons(data["reply_markup"])
    assert "keeping the fact-checked script, the voiceover and the rendered video" in data["text"]
    assert "<code>run_k</code>" in data["text"]  # a typed /rerender needs it once the review card is gone


def test_an_early_failure_card_does_not(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch, with_video=False)
    bot, calls = _bot(db, monkeypatch)
    bot.finish_progress_card(5, "T", run_id, success=False, error="Script rejected")
    data = [d for m, d in calls if m == "editMessageText"][-1]
    assert not any(b.startswith("rerender:") for b in _buttons(data["reply_markup"]))
    assert "stopped before its script passed the fact check" in data["text"]


def test_dashboard_offers_re_render_for_the_last_late_failure(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch)
    _, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert f"rerender:{db.run_key(run_id)}" in [b["callback_data"] for row in buttons for b in row]


def test_re_render_button_reaches_the_daemon_action(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch)
    seen = []
    h = make_command_handler(db, lambda: None, lambda: "", run_actions={"rerender": lambda r: seen.append(r) or "ok"})
    assert h("rerender", db.run_key(run_id)) == "ok" and seen == [run_id]
    assert "only works while the daemon is running" in make_command_handler(db, lambda: None, lambda: "")("rerender", "1")


def test_only_a_failed_run_with_a_video_is_re_rendered(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch)
    coordinator = SimpleNamespace(db=db)
    refusal = lambda r: main.PipelineCoordinator.rerender_refusal(coordinator, r)  # noqa: E731
    assert refusal(run_id) is None
    db.update_run(run_id, "pending_approval")
    assert "not failed" in refusal(run_id)
    assert "no longer recorded" in refusal("run_missing")


def test_the_worker_resumes_the_run_under_the_worker_contract(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        out = cmd[cmd.index("--result-file") + 1]
        open(out, "w", encoding="utf-8").write(json.dumps({"status": "simulated", "run_id": "run_k"}))
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    result = main.run_in_worker("reel", dry_run=True, schedule_posts=False, resume="run_k")
    assert seen["cmd"][2:5] == ["main.py", "resume", "run_k"] and "--background-polling" in seen["cmd"]
    assert "--dry-run" in seen["cmd"] and "--format" not in seen["cmd"] and result["status"] == "simulated"


def test_resume_writes_the_worker_result(monkeypatch, tmp_path):
    out = tmp_path / "result.json"

    class Coordinator:
        def __init__(self, schedule_posts=False):
            self.telegram_bot = SimpleNamespace(background_polling=False)

        def resume_run(self, run_id, auto_approve, dry_run, require_live=False):
            raise ValueError("Run status 'published' can't be resumed.")
    monkeypatch.setattr(main, "PipelineCoordinator", Coordinator)
    assert main.main(["resume", "run_k", "--background-polling", "--result-file", str(out)]) == 1
    assert json.loads(out.read_text())["status"] == "refused"


def test_a_re_render_waits_for_a_new_decision(db, tmp_path, monkeypatch):
    # Production 2026-10-24: the resumed run read the earlier Approve 8 ms after its new card was sent.
    run_id = _failed(db, tmp_path, monkeypatch)
    db.record_approval(run_id, db.get_run(run_id)["topic_id"], 891)
    db.update_approval_decision(run_id, "approved", decided_by="telegram:555")
    db.reset_approval(run_id)
    approval = db.get_approval(run_id)
    assert approval["decision"] == "pending" and approval["decided_by"] is None
    import inspect
    assert "reset_approval(run_id)" in inspect.getsource(main.PipelineCoordinator.resume_run)


def test_dashboard_still_offers_re_render_after_a_newer_run_was_cancelled(db, tmp_path, monkeypatch):
    # Production 2026-10-24: the 07:00 scheduled run, cancelled, became the latest run and the Kailasa
    # Re-render button disappeared from the dashboard.
    run_id = _failed(db, tmp_path, monkeypatch)
    topic2 = db.list_all_topics()[1]["id"]
    db.create_run("run_newer", topic2)
    db.update_run("run_newer", "cancelled")
    _, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    found = [b for row in buttons for b in row if b["callback_data"] == f"rerender:{db.run_key(run_id)}"]
    assert found and found[0]["text"] == "🔁 Re-render"   # the run is named in the text (2026-10-25)
    # Once the topic has been made again and reached review, the old failure is no longer offered.
    db.create_run("run_again", db.get_run(run_id)["topic_id"])
    db.update_run("run_again", "simulated")
    _, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert not any(b["callback_data"].startswith("rerender:") for row in buttons for b in row)


def test_dashboard_says_a_late_failure_needs_you_even_when_a_newer_run_is_the_latest(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch)
    db.create_run("run_newer", db.list_all_topics()[1]["id"])
    db.update_run("run_newer", "cancelled")
    text, _ = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert "Nothing needs you" not in text and "🔁 <b>Failed after rendering</b>" in text and f"<code>{run_id}</code>" in text


def test_a_rejected_run_with_a_video_is_made_again_as_a_regeneration(db, tmp_path, monkeypatch):
    # Production 2026-10-24: Regenerate on the card would have re-voiced and re-bought the clip at once on
    # the running release; Reject, then Re-render after the fix, regenerates with the reason and reuses both.
    run_id = _failed(db, tmp_path, monkeypatch)
    db.update_run(run_id, "rejected")
    coordinator = SimpleNamespace(db=db)
    assert main.PipelineCoordinator.rerender_refusal(coordinator, run_id) is None
    topic = db.get_run(run_id)["topic_id"]
    assert main.PipelineCoordinator.remake_args(coordinator, run_id) == ["--regenerate-from", run_id, "--topic-id", topic]
    assert main.regeneration_topic(db, run_id) == topic and main.regeneration_topic(db, "run_missing") is None
    db.update_run(run_id, "failed")
    assert main.PipelineCoordinator.remake_args(coordinator, run_id) == []       # failed: resumed in place
    db.update_run(run_id, "rejected")
    _, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    found = [b for row in buttons for b in row if b["callback_data"] == f"rerender:{db.run_key(run_id)}"]
    assert found and found[0]["text"].startswith("🔁 Make again")
    assert "made again with the reason you gave" in main.rerender_started(db, run_id)


def test_a_regeneration_from_the_command_line_keeps_the_previous_runs_topic(db, tmp_path, monkeypatch):
    # Production 2026-10-24: `run --regenerate-from <kailasa run>` with no --topic-id took the next queued
    # topic (Greek Fire) and attached Kailasa's feedback to it.
    run_id = _failed(db, tmp_path, monkeypatch)
    db.update_run(run_id, "rejected")
    seen = {}

    class Coordinator:
        def __init__(self, schedule_posts=False):
            self.db = db
            self.telegram_bot = SimpleNamespace(background_polling=False, notify=lambda *a, **k: None)
            self.paid_voice_in_dry_run = self.paid_motion_in_dry_run = False

        def _await_reason(self, previous, wait_seconds=10):
            return {"reason": "pictures", "from_run": previous}

        def run_cycle(self, topic_id, *a, **k):
            seen["topic"] = topic_id
            return {"status": "simulated", "run_id": "run_new"}
    monkeypatch.setattr(main, "PipelineCoordinator", Coordinator)
    assert main.main(["run", "--regenerate-from", run_id, "--dry-run"]) == 0
    assert seen["topic"] == db.get_run(run_id)["topic_id"]


def test_a_re_render_keeps_one_message_for_the_run(db, tmp_path, monkeypatch):
    # Operator, 2026-10-25: a Re-render left three messages (the dashboard, a notice and a new failure card).
    from src import card_ledger
    run_id = _failed(db, tmp_path, monkeypatch)
    card_ledger.record(run_id, "failure", "❌ Production failed", [], 77)
    bot, calls = _bot(db, monkeypatch)
    db.set_setting("telegram_dashboard_id", "10")
    monkeypatch.setattr(bot, "show_dashboard", lambda *a, **k: calls.append(("dashboard", {})))
    monkeypatch.setattr(bot, "_answer_callback", lambda *a, **k: None)
    handler = lambda action, target: f"🔁 {run_id} is being re-rendered, keeping the fact-checked script."  # noqa: E731
    bot._process_update({"update_id": 1, "callback_query": {"id": "q", "data": f"rerender:{db.run_key(run_id)}",
                         "from": {"id": 555}, "message": {"message_id": 10, "chat": {"id": 555}}}}, handler)
    edits = [d for m, d in calls if m == "editMessageText"]
    assert edits and edits[-1]["message_id"] == 77                                   # the run's failure card, not the dashboard
    assert not [d for m, d in calls if m == "sendMessage"]                           # no new message
    from src.telegram_state import get
    assert get(db, "progress-reuse", run_id) == "77"                                 # its progress card will reuse it
