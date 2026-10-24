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
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_k", topic)
    (tmp_path / "run_k").mkdir()
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
    assert "reusing its script, voiceover and accepted motion clips" in data["text"]
    assert "<i>Run ID: run_k</i>" in data["text"]  # a typed /rerender needs it once the review card is gone


def test_an_early_failure_card_does_not(db, tmp_path, monkeypatch):
    run_id = _failed(db, tmp_path, monkeypatch, with_video=False)
    bot, calls = _bot(db, monkeypatch)
    bot.finish_progress_card(5, "T", run_id, success=False, error="Script rejected")
    data = [d for m, d in calls if m == "editMessageText"][-1]
    assert not any(b.startswith("rerender:") for b in _buttons(data["reply_markup"]))
    assert "will be retried on a later run" in data["text"]


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

        def resume_run(self, run_id, auto_approve, dry_run):
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
    assert found and found[0]["text"].startswith("🔁 Re-render: ")
    # Once the topic has been made again and reached review, the old failure is no longer offered.
    db.create_run("run_again", db.get_run(run_id)["topic_id"])
    db.update_run("run_again", "simulated")
    _, buttons = dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert not any(b["callback_data"].startswith("rerender:") for row in buttons for b in row)
