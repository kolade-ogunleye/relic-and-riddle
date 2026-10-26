"""Every panel the operator was told to type for is a public command (operator, 2026-10-25: "bot says I don't know
/produce"), and /produce works in the daemon, which is what production runs, not only in bot mode."""
import contextlib
from types import SimpleNamespace

import main
from src.control import make_command_handler
from src.telegram_commands import menu


def test_typed_panels_are_public_commands(db):
    from src.telegram_commands import ALIASES
    public = {entry["command"] for entry in menu()}
    assert {"produce", "posts", "reviews", "queue", "render", "status", "help"} <= public
    assert ALIASES == {"start": "dashboard"}          # the older names are removed, not half-kept (operator, 2026-10-25)
    h = make_command_handler(db, lambda: None, lambda: "")
    for command in ("posts", "reviews", "history"):
        reply = h(command, "")
        text = reply[0] if isinstance(reply, tuple) else reply
        assert "Unknown command" not in text, command
    assert h("produce", "3") == "Batch production works while the bot is running."  # wired by bot and daemon


def test_every_public_command_is_handled_or_a_bot_builtin(db):
    # A menu entry the handler does not know would read "Unknown command" after Telegram offered it.
    builtins = {"start", "dashboard", "clear", "jobs", "progress", "help", "ask", "issue", "images", "music", "covers"}
    h = make_command_handler(db, lambda: None, lambda: "")
    unknown = []
    for entry in menu():
        if entry["command"] in builtins:
            continue
        reply = h(entry["command"], "")
        text = reply[0] if isinstance(reply, tuple) else (reply or "")
        if "Unknown command" in text:
            unknown.append(entry["command"])
    assert unknown == []


class _Scheduler:
    instances = []

    def __init__(self, *a, **k):
        self.jobs = []
        _Scheduler.instances.append(self)

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append((func, trigger, kwargs))
        return SimpleNamespace(next_run_time=None)

    def start(self):
        raise KeyboardInterrupt


def test_the_daemon_chains_a_produce_batch_after_each_clean_run(monkeypatch):
    import apscheduler.schedulers.blocking as blocking
    _Scheduler.instances.clear()
    monkeypatch.setattr(blocking, "BlockingScheduler", _Scheduler)
    settings, notices, worker_calls = {}, [], []
    db = SimpleNamespace(get_setting=settings.get, set_setting=settings.__setitem__)
    bot = SimpleNamespace(register_commands=lambda: None, is_configured=lambda: False, notify=lambda text, *a, **k: notices.append(text),
                          show_dashboard=lambda *a, **k: None, warm_up=lambda: None, _api=lambda *a, **k: {},
                          send_photo=None, send_audio=None, delete_message=None, chat_id="1")
    coordinator = SimpleNamespace(telegram_bot=bot, db=db, topic_engine=None, audio_engine=SimpleNamespace(voice_quota=lambda: None),
                                  post_due=lambda: None, confirm_posted=None, confirm_not_posted=None, simulate_publish=None)
    captured = {}

    def handler(db_, schedule_text, trigger_run, **kwargs):
        captured["produce"] = kwargs.get("produce_batch")
        return lambda c, a: ""
    monkeypatch.setattr(main, "PipelineCoordinator", lambda **kw: coordinator)
    monkeypatch.setattr(main, "make_command_handler", handler)
    monkeypatch.setattr(main, "is_paused", lambda db: False)
    monkeypatch.setattr(main, "busy_lease", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(main, "ping_healthcheck", lambda: None)
    monkeypatch.setattr(main, "run_in_worker", lambda *a, **k: worker_calls.append(a) or {"status": "simulated"})
    monkeypatch.setattr("src.control.startup_dashboard_card", lambda *a, **k: ("", []))
    assert main.main(["daemon", "--time", "07:00", "--dry-run"]) == 0
    scheduler = _Scheduler.instances[-1]
    produce = captured["produce"]
    assert produce is not None

    def run_pending():  # the jobs trigger_run and the batch add: run each production_run once
        pending = [f for f, t, k in scheduler.jobs if t is None and f.__name__ == "production_run"]
        scheduler.jobs = [j for j in scheduler.jobs if not (j[1] is None and j[0].__name__ == "production_run")]
        for f in pending:
            f(manual=True)
        return len(pending)

    assert "Producing 3 episode(s)" in produce(3) and settings["produce_remaining"] == "2"
    assert run_pending() == 1 and len(worker_calls) == 1               # the first episode ran
    assert settings["produce_remaining"] == "1" and any("Next episode" in n for n in notices)
    assert run_pending() == 1 and settings["produce_remaining"] == "0"  # the second, chained after the first
    assert run_pending() == 1 and run_pending() == 0                   # the third; nothing after it
    assert len(worker_calls) == 3
    # a crash ends the batch
    monkeypatch.setattr(main, "run_in_worker", lambda *a, **k: {"status": "crashed", "exit_code": 1})
    monkeypatch.setattr(main, "reconcile_interrupted_runs", lambda *a, **k: [])
    produce(2)
    run_pending()
    assert settings["produce_remaining"] == "0" and any("Batch production stopped" in n for n in notices)
