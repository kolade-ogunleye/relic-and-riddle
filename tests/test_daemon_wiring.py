"""
tests/test_daemon_wiring.py
===========================
The daemon must register every job it relies on, and other commands must not.
"""

from types import SimpleNamespace

import pytest

import main


class FakeScheduler:
    instances = []

    def __init__(self, *args, **kwargs):
        self.jobs = []
        FakeScheduler.instances.append(self)

    def add_job(self, func, trigger=None, **kwargs):
        self.jobs.append((func, trigger, kwargs))
        return SimpleNamespace(next_run_time=None)

    def start(self):
        raise KeyboardInterrupt  # stop at once; the daemon treats this as a clean shutdown


@pytest.fixture
def daemon_env(monkeypatch):
    FakeScheduler.instances.clear()
    import apscheduler.schedulers.blocking as blocking
    monkeypatch.setattr(blocking, "BlockingScheduler", FakeScheduler)
    posted = []
    bot = SimpleNamespace(register_commands=lambda: None, is_configured=lambda: False,
                          notify=lambda *a, **k: None, show_dashboard=lambda *a, **k: None, warm_up=lambda: None, _api=lambda *a, **k: {},
                          send_photo=None, send_audio=None, delete_message=None, chat_id="1")
    coordinator = SimpleNamespace(telegram_bot=bot, db=SimpleNamespace(), topic_engine=None,
                                  audio_engine=SimpleNamespace(voice_quota=lambda: None),
                                  post_due=lambda: posted.append(1),
                                  confirm_posted=None, confirm_not_posted=None, simulate_publish=None)
    monkeypatch.setattr(main, "PipelineCoordinator", lambda **kw: coordinator)
    monkeypatch.setattr(main, "make_command_handler", lambda *a, **k: (lambda c, a: ""))
    monkeypatch.setattr(main, "is_paused", lambda db: False)
    monkeypatch.setattr("src.control.startup_dashboard_card", lambda *a, **k: ("", []))
    return posted


def test_daemon_registers_scheduled_posting(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    assert main.main(["daemon", "--time", "07:00"]) == 0
    jobs = FakeScheduler.instances[-1].jobs
    interval_jobs = [(f, t, k) for f, t, k in jobs if t == "interval" and k.get("minutes") == 1]
    assert len(interval_jobs) == 1
    interval_jobs[0][0]()  # the registered job publishes due posts
    assert daemon_env == [1]


def test_daemon_skips_posting_job_when_scheduling_is_off(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", False)
    assert main.main(["daemon", "--time", "07:00"]) == 0
    assert not [t for _, t, k in FakeScheduler.instances[-1].jobs if t == "interval" and k.get("minutes") == 1]


def test_scheduled_posting_holds_while_paused(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(main, "is_paused", lambda db: True)
    main.main(["daemon", "--time", "07:00"])
    job = next(f for f, t, k in FakeScheduler.instances[-1].jobs if t == "interval" and k.get("minutes") == 1)
    job()
    assert daemon_env == []


def test_update_command_does_not_touch_a_scheduler(monkeypatch):
    from src import maintenance
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(maintenance, "check", lambda: SimpleNamespace(applicable=[], updates=[]))
    monkeypatch.setattr(maintenance, "report_text", lambda plan: "no updates")
    assert main.main(["update"]) == 0


@pytest.mark.parametrize("status, code", [("simulated", 0), ("failed", 1)])
def test_test_command_reports_its_own_outcome(monkeypatch, capsys, status, code):
    class FakeCoordinator:
        def run_cycle(self, *a, **k):
            return {"status": status, "master_video": "m.mp4", "thumbnails": [], "stories": [], "carousel": []}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    assert main.main(["test"]) == code
    assert "usage:" not in capsys.readouterr().out  # no help text after a run


def test_test_suite_cannot_write_repository_data(db):
    from src import config, music
    from src.control import make_command_handler
    assert config.BASE_DIR not in config.ISSUES_DIR.parents
    assert config.BASE_DIR not in music.LIBRARY.parents
    make_command_handler(db, lambda: None, lambda: "")("issue", "sandbox check")
    assert list(config.ISSUES_DIR.glob("iss_*.md"))


def _bot_mode(monkeypatch, daemon_env, scheduling):
    """Starts `main.py bot` until its first poll; returns (trigger_run, ticker calls)."""
    monkeypatch.setattr(main, "POST_SCHEDULING", scheduling)
    captured, tickers = {}, []

    def handler(db, schedule_text, trigger_run, **kwargs):
        captured["trigger"] = trigger_run
        return lambda c, a: ""

    def stop(handler):
        raise KeyboardInterrupt

    coordinator = main.PipelineCoordinator()
    coordinator.telegram_bot.is_configured = lambda: True
    coordinator.telegram_bot.poll_once = stop
    monkeypatch.setattr(main, "make_command_handler", handler)
    monkeypatch.setattr(main, "PipelineDatabase", lambda: SimpleNamespace())
    monkeypatch.setattr(main, "start_post_ticker", lambda coord, bot: tickers.append(coord))
    assert main.main(["bot"]) == 0
    return captured["trigger"], tickers


@pytest.mark.parametrize("scheduling", [True, False])
def test_bot_run_books_slots_when_scheduling_is_on(daemon_env, monkeypatch, scheduling):
    launched = []
    trigger, tickers = _bot_mode(monkeypatch, daemon_env, scheduling)
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: launched.append(cmd) or SimpleNamespace(
        pid=1, poll=lambda: None, communicate=lambda: ("", ""), returncode=0))
    trigger()
    assert ("--schedule-posts" in launched[0]) is scheduling
    assert len(tickers) == (1 if scheduling else 0)  # bot mode posts what it books


def test_run_command_schedules_only_when_asked(monkeypatch):
    made = []

    class FakeCoordinator:
        def __init__(self, **kw):
            made.append(kw)
            self.telegram_bot = SimpleNamespace()

        def run_cycle(self, *a, **k):
            return {"status": "simulated"}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    main.main(["run", "--dry-run"])
    main.main(["run", "--dry-run", "--schedule-posts"])
    assert made == [{}, {"schedule_posts": True}]


def test_daemon_dry_run_never_books_or_posts(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    assert main.main(["daemon", "--time", "07:00", "--dry-run"]) == 0
    assert not [t for _, t, k in FakeScheduler.instances[-1].jobs if t == "interval" and k.get("minutes") == 1]  # no posting job


@pytest.mark.parametrize("dry", [True, False])
def test_a_run_started_from_the_bot_is_a_dry_run_unless_switched_off(daemon_env, monkeypatch, dry):
    # A Render in the development bot started a live run: Approve & Post would have published.
    from src import config
    monkeypatch.setattr(config, "BOT_RUNS_DRY", dry)
    launched = []
    trigger, _ = _bot_mode(monkeypatch, daemon_env, False)
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: launched.append(cmd) or SimpleNamespace(
        pid=1, poll=lambda: None, communicate=lambda: ("", ""), returncode=0))
    trigger()
    assert ("--dry-run" in launched[0]) is dry


def _daemon_run(monkeypatch, daemon_env, result, dry_run=True):
    """Starts the daemon, then fires its daily job with the worker returning `result`."""
    calls, notes, cleared = [], [], []
    monkeypatch.setattr(main, "run_in_worker", lambda fmt, **kw: calls.append((fmt, kw)) or dict(result))
    monkeypatch.setattr(main, "ping_healthcheck", lambda: notes.append("ping"))
    monkeypatch.setattr(main, "reconcile_interrupted_runs", lambda db, te=None: [])  # startup: nothing orphaned
    main.main(["daemon", "--time", "07:00"] + (["--dry-run"] if dry_run else []))
    monkeypatch.setattr(main, "reconcile_interrupted_runs", lambda db, te=None: cleared.append(db) or ["run_x"])
    coordinator = main.PipelineCoordinator()
    coordinator.telegram_bot.notify = lambda text, *a, **k: notes.append(text)
    daily = next(f for f, t, _ in FakeScheduler.instances[-1].jobs if t not in ("interval", None) and getattr(f, "__name__", "") == "production_run")
    daily(content_format="dual")
    return calls, notes, cleared


def test_daemon_runs_production_in_a_worker_process(daemon_env, monkeypatch):
    # 2026-10-23: a native crash in a run would have taken the daemon (its bot and schedule) down with it.
    calls, notes, cleared = _daemon_run(monkeypatch, daemon_env, {"status": "simulated", "run_id": "r1"})
    assert calls == [("dual", {"dry_run": True, "schedule_posts": main.POST_SCHEDULING, "extra": None})]
    assert notes == ["ping"] and not cleared


def test_a_crashed_worker_is_reported_and_its_run_cleared(daemon_env, monkeypatch):
    calls, notes, cleared = _daemon_run(monkeypatch, daemon_env, {"status": "crashed", "exit_code": 3221225477})
    assert cleared  # the orphaned run is failed and its topic requeued at once, not at the next restart
    assert any("crashed" in n and "0xC0000005" in n for n in notes)
    assert "ping" not in notes


def test_an_empty_queue_from_the_worker_is_reported(daemon_env, monkeypatch):
    _, notes, _ = _daemon_run(monkeypatch, daemon_env, {"status": "queue_empty"})
    assert notes[0] == "ping" and "queue is empty" in notes[1]


def test_run_in_worker_passes_the_run_settings_and_reads_its_result(monkeypatch):
    import json
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        path = cmd[cmd.index("--result-file") + 1]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"status": "simulated", "run_id": "r9"}, f)
        return SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(main.subprocess, "run", fake_run)
    result = main.run_in_worker("reel", dry_run=True, schedule_posts=False)
    assert result == {"status": "simulated", "run_id": "r9", "exit_code": 0}
    cmd = seen["cmd"]
    assert cmd[2:4] == ["main.py", "run"] and "--background-polling" in cmd and "--require-live" in cmd
    assert "--dry-run" in cmd and "--schedule-posts" not in cmd
    assert not __import__("os").path.exists(cmd[cmd.index("--result-file") + 1])  # cleaned up


def test_run_in_worker_reports_a_crash_when_no_result_was_written(monkeypatch):
    monkeypatch.setattr(main.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=3221225477, stderr="loading model\nWindows fatal exception: access violation\n"))
    result = main.run_in_worker("reel", dry_run=False, schedule_posts=True)
    assert result["status"] == "crashed" and result["exit_code"] == 3221225477
    assert "access violation" in result["stderr_tail"]


@pytest.mark.parametrize("outcome, expected", [
    ("simulated", {"status": "simulated", "run_id": "r1"}),
    ("empty", {"status": "queue_empty"}),
    ("boom", {"status": "failed"}),
])
def test_the_run_command_writes_its_outcome_for_the_daemon(monkeypatch, tmp_path, outcome, expected):
    import json

    class FakeCoordinator:
        def __init__(self, **kw):
            self.telegram_bot = SimpleNamespace()

        def run_cycle(self, *a, **k):
            if outcome == "empty":
                raise main.QueueEmpty("nothing left")
            if outcome == "boom":
                raise RuntimeError("stage failed")
            return {"status": "simulated", "run_id": "r1", "master_video": "m.mp4"}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    out = tmp_path / "result.json"
    main.main(["run", "--dry-run", "--result-file", str(out)])
    written = json.loads(out.read_text(encoding="utf-8"))
    assert {k: written[k] for k in expected} == expected


def test_the_daemon_reports_a_refused_release_without_restarting(daemon_env, monkeypatch):
    # A release refused before the stop window causes no restart, so startup alone would never say so.
    reported = []
    monkeypatch.setattr(main, "announce_release", lambda db, bot: reported.append(db))
    main.main(["daemon", "--time", "07:00", "--dry-run"])
    poll = [f for f, t, k in FakeScheduler.instances[-1].jobs if t == "interval" and k.get("minutes") == 5]
    assert len(poll) == 1
    before = len(reported)
    poll[0]()
    assert len(reported) == before + 1


def test_bot_mode_owns_polling_so_its_review_waits_read_the_database(daemon_env, monkeypatch):
    # A Resend in the dev bot started a review wait that polled getUpdates beside the bot's own loop:
    # Telegram answered 409 Conflict every few seconds for as long as the review was open.
    seen = {}
    monkeypatch.setattr(main, "POST_SCHEDULING", False)
    coordinator = main.PipelineCoordinator()
    coordinator.telegram_bot.is_configured = lambda: True

    def stop(handler):
        seen["background_polling"] = getattr(coordinator.telegram_bot, "background_polling", False)
        raise KeyboardInterrupt
    coordinator.telegram_bot.poll_once = stop
    monkeypatch.setattr(main, "make_command_handler", lambda *a, **k: (lambda c, a: ""))
    monkeypatch.setattr(main, "PipelineDatabase", lambda: SimpleNamespace())
    assert main.main(["bot"]) == 0
    assert seen["background_polling"] is True


def _awaiting_review(db, run_id="run_r", decision=None):
    db.create_run(run_id, db.list_all_topics()[0]["id"])
    db.update_run(run_id, "pending_approval")
    db.record_approval(run_id, db.list_all_topics()[0]["id"], 700)
    if decision:
        db.update_approval_decision(run_id, decision, decided_by="test")


def test_startup_resumes_every_review_still_awaiting_its_decision(db):
    # A restart ended the review's wait; a Regenerate tapped afterwards was recorded and never acted on.
    _awaiting_review(db, "run_a")
    _awaiting_review(db, "run_b", decision="regenerate")
    db.create_run("run_c", db.list_all_topics()[0]["id"])   # producing: not a review
    followed = []
    main.resume_reviews(SimpleNamespace(db=db), lambda run_id, resend: followed.append((run_id, resend)))
    assert sorted(followed) == [("run_a", False), ("run_b", False)]   # waits resume; cards are not re-sent


def test_a_resumed_review_acts_on_a_decision_made_while_nothing_waited(db):
    _awaiting_review(db, decision="regenerate")
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coordinator.db = db
    coordinator.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    sent = []
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="", approver_ids=set())
    bot.send_approval_request = lambda *a, **k: sent.append(1)
    coordinator.telegram_bot = bot
    result = coordinator.request_approval("run_r", resend=False)
    assert result["decision"] == "regenerate" and result["topic_id"] == db.list_all_topics()[0]["id"]
    assert sent == []   # the card already in the chat is the one tapped


def test_a_regenerate_in_bot_mode_launches_the_regeneration_run(daemon_env, monkeypatch):
    launched = []
    trigger, _ = _bot_mode(monkeypatch, daemon_env, False)
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: launched.append(cmd) or SimpleNamespace(
        pid=1, poll=lambda: None, communicate=lambda: ("", ""), returncode=0))
    assert trigger() and "--regenerate-from" not in launched[-1]


def test_the_run_command_regenerates_with_the_earlier_runs_feedback(monkeypatch):
    seen = {}

    class FakeCoordinator:
        def __init__(self, **kw):
            self.telegram_bot = SimpleNamespace()
            self.db = SimpleNamespace(get_setting=lambda key: None)

        def _await_reason(self, run_id, wait_seconds=120):
            seen["from"] = run_id
            return {"reason": "sound"}

        def run_cycle(self, topic_id, auto_approve, dry_run, require_live=False, content_format="reel", _regenerations=0, run_id=None):
            seen.update(topic=topic_id, regenerations=_regenerations, feedback=self._operator_feedback)
            return {"status": "simulated"}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    main.main(["run", "--dry-run", "--topic-id", "amber_room_heist", "--regenerate-from", "run_old"])
    assert seen == {"from": "run_old", "topic": "amber_room_heist", "regenerations": 1, "feedback": {"reason": "sound"}}


def test_the_sweep_resumes_only_decided_reviews_nothing_waits_on(db):
    _awaiting_review(db, "run_open")                                   # undecided: its card waits for you
    _awaiting_review(db, "run_cli", decision="rejected")              # decided from the command line
    _awaiting_review(db, "run_mine", decision="approved")             # this process already waits on it
    _awaiting_review(db, "run_late", decision="timeout")              # timed out: Resend reopens it
    followed = []
    main.resume_reviews(SimpleNamespace(db=db), lambda run_id, resend: followed.append(run_id),
                        waiting={"run_mine"}, decided_only=True)
    assert followed == ["run_cli"]


def test_the_daemon_sweeps_decided_reviews_but_not_during_a_production_run(daemon_env, monkeypatch):
    swept = []
    monkeypatch.setattr(main, "resume_reviews", lambda coordinator, follow, waiting=None, decided_only=False: swept.append(decided_only) or [])
    main.main(["daemon", "--time", "07:00", "--dry-run"])
    sweep = [f for f, t, k in FakeScheduler.instances[-1].jobs if t == "interval" and k.get("minutes") == 2]
    assert len(sweep) == 1
    swept.clear()
    sweep[0]()
    assert swept == [True]


def test_both_decision_paths_settle_a_timeout_the_same_way(db):
    # One failed the topic, the other left it in production, and one alerted a second time.
    _awaiting_review(db, decision="timeout")
    statuses, alerts = [], []
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.topic_engine = SimpleNamespace(record_status=lambda tid, status, run_id=None: statuses.append(status))
    c._alert = lambda *a, **k: alerts.append(a)
    c._settle_decision("run_r", db.list_all_topics()[0]["id"], "timeout")
    assert db.get_run("run_r")["status"] == "timeout" and statuses == ["failed"] and alerts == []


def test_the_regeneration_limit_holds_across_restarts(db, monkeypatch):
    # A regeneration started by a resumed wait always counted as the first, so the limit never applied.
    notes, made = [], []

    class FakeCoordinator:
        def __init__(self, **kw):
            self.db = db
            self.telegram_bot = SimpleNamespace(notify=lambda text, *a, **k: notes.append(text))

        def _await_reason(self, run_id, wait_seconds=120):
            return {}

        def run_cycle(self, *a, **k):
            made.append(k.get("_regenerations"))
            return {"status": "simulated"}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    db.create_run("run_second", db.list_all_topics()[0]["id"])
    db.set_setting("regenerations:run_second", str(main.MAX_REGENERATIONS))
    assert main.main(["run", "--dry-run", "--topic-id", "t", "--regenerate-from", "run_second"]) == 0
    assert made == [] and "limit reached" in notes[0]
    db.set_setting("regenerations:run_second", "1")
    main.main(["run", "--dry-run", "--topic-id", "t", "--regenerate-from", "run_second"])
    assert made == [2]


def test_a_bare_ask_survives_a_restart(db):
    # /ask without text waits for the next message; that wait lived only in memory.
    import time as clock
    from src.telegram_bot import TelegramApprovalBot
    first = TelegramApprovalBot(db=db, bot_token="", chat_id="", approver_ids=set())
    first._awaiting = ("ask", clock.time() + 600)
    restarted = TelegramApprovalBot(db=db, bot_token="", chat_id="", approver_ids=set())
    assert restarted._awaiting[0] == "ask"
    restarted._awaiting = None
    assert TelegramApprovalBot(db=db, bot_token="", chat_id="", approver_ids=set())._awaiting is None


@pytest.mark.parametrize("status, code", [("rejected", 0), ("regenerate", 0), ("scheduled", 0), ("timeout", 0),
                                          ("simulated", 0), ("failed", 1)])
def test_a_decided_run_is_not_reported_as_a_failed_worker(monkeypatch, status, code):
    # A Reject made bot mode log "Production worker failed with code 1".
    class FakeCoordinator:
        def __init__(self, **kw):
            self.telegram_bot = SimpleNamespace()

        def run_cycle(self, *a, **k):
            return {"status": status}
    monkeypatch.setattr(main, "PipelineCoordinator", FakeCoordinator)
    assert main.main(["run", "--dry-run"]) == code
