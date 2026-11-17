"""QF-70: a review whose window passes undecided stays pending (stale) and its topic queued, never failed; every review
window is at least 12 hours; an open review alone does not hold a release.

The base failed the run ("timeout") and the topic ("failed") when the window passed (1-hour floor), held the topic for
TIMEOUT_HOLD_DAYS and then let it be made again; scripts/release_gate.py called production busy for as long as any
review was open (and the worker waiting on it held the production lease); and a review open longer than
STALE_PRODUCTION_HOURS left its topic "in_production" where get_next_topic picked it a second time.
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from scripts import release_gate
from src import config
from src.telegram_bot import TelegramApprovalBot

ROOT = Path(__file__).resolve().parents[1]


def _review(db, run_id="rv", topic=None):
    topic = topic or db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    db.record_topic_status(topic, "in_production", run_id=run_id)
    db.update_run(run_id, "pending_approval")
    db.record_approval(run_id, topic, 700)
    return topic


def _wait_out(db, run_id="rv"):
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    return bot.wait_for_decision(run_id, timeout_seconds=0.3, poll_interval=0.05)


def test_after_the_window_the_review_stays_pending_and_its_topic_is_queued_not_failed(db):
    topic = _review(db)
    assert _wait_out(db) == "timeout"
    assert db.get_approval("rv")["decision"] == "pending"
    assert db.get_run("rv")["status"] == "pending_approval"
    assert db.get_topic_by_id(topic)["status"] == "queued"
    assert db.review_stale("rv")
    assert [r["run_id"] for r in db.timed_out_reviews()] == ["rv"]          # Needs you: Resend or Reject
    assert "rv" not in [p["run_id"] for p in db.list_pending_approvals()]   # not an open card any more


def test_the_settled_timeout_changes_neither_run_nor_topic(db):
    topic = _review(db)
    statuses = []
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.topic_engine = SimpleNamespace(record_status=lambda tid, status, run_id=None: statuses.append(status))
    c._settle_decision("rv", topic, "timeout")
    assert statuses == [] and db.get_run("rv")["status"] == "pending_approval"
    assert db.get_topic_by_id(topic)["status"] == "queued" and db.get_approval("rv")["decision"] == "pending"


def test_a_stale_reviews_topic_is_held_for_as_long_as_it_waits_never_made_again(db):
    topics = [t["id"] for t in db.list_all_topics()]
    _review(db, topic=topics[0])
    _wait_out(db)
    with db._conn() as conn:   # stale for 8 days: past the old TIMEOUT_HOLD_DAYS, when the base made it again
        conn.execute("UPDATE settings SET value = ? WHERE key = ?",
                     ((datetime.now(UTC) - timedelta(days=8)).isoformat(), "review-stale:rv"))
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = 'rv'",
                     ((datetime.now(UTC) - timedelta(days=8)).isoformat(),))
    for _ in topics[1:]:
        picked = db.get_next_topic()
        assert picked["id"] != topics[0]
        db.record_topic_status(picked["id"], "published")
    with pytest.raises(main.QueueEmpty, match="timed-out review"):   # what is left waits for the stale review
        db.get_next_topic()


def test_an_open_review_older_than_the_production_staleness_does_not_have_its_topic_made_again(db):
    topics = [t["id"] for t in db.list_all_topics()]
    _review(db, topic=topics[0])
    with db._conn() as conn:   # 7 hours into a 12-hour window: past STALE_PRODUCTION_HOURS (6)
        conn.execute("UPDATE topics SET updated_at = ? WHERE id = ?",
                     ((datetime.now(UTC) - timedelta(hours=7)).isoformat(), topics[0]))
    for other in topics[1:]:   # the queued topics come first: only the review's topic is left
        db.record_topic_status(other, "published")
    with pytest.raises(main.QueueEmpty):
        db.get_next_topic()


def test_a_resumed_wait_on_a_stale_review_ends_at_once_and_a_resend_opens_it_again(db):
    _review(db)
    _wait_out(db)
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    started = datetime.now(UTC)
    assert bot.wait_for_decision("rv", timeout_seconds=30, poll_interval=0.05) == "timeout"
    assert (datetime.now(UTC) - started).total_seconds() < 5
    db.record_approval("rv", db.get_run("rv")["topic_id"], 701)              # Resend delivers the card again
    assert not db.review_stale("rv") and "rv" in [p["run_id"] for p in db.list_pending_approvals()]
    db.update_approval_decision("rv", "rejected", decided_by="test")
    assert bot.wait_for_decision("rv", timeout_seconds=30, poll_interval=0.05) == "rejected"


def test_the_sweep_marks_an_old_review_stale_keeping_it_pending(db, monkeypatch):
    _review(db)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at = ? WHERE run_id = 'rv'",
                     ((datetime.now(UTC) - timedelta(hours=27)).isoformat(),))
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    monkeypatch.setattr(bot, "_api", lambda m, **kw: {"message_id": 1})
    assert bot.cleanup_stale_approvals() == 1
    assert db.get_approval("rv")["decision"] == "pending" and db.review_stale("rv")
    assert db.get_run("rv")["status"] == "pending_approval"


def test_every_review_window_is_at_least_twelve_hours(tmp_path, monkeypatch):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RELIC_")}
    env.update(RELIC_APP_ROOT=str(tmp_path), APPROVAL_TIMEOUT_SECONDS="3600", PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", "from src import config; print(config.APPROVAL_TIMEOUT_SECONDS)"],
                         cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout.split()[-1]
    assert int(out) == 12 * 3600
    assert config.APPROVAL_TIMEOUT_SECONDS >= 12 * 3600
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    now = datetime.now(UTC)
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(c, "_booked_reel_days", lambda: set(), raising=False)
    monkeypatch.setattr(main.post_schedule, "next_reel_slot", lambda n, b: now + timedelta(hours=2))
    assert c._approval_window() >= 12 * 3600       # the base closed it 20 minutes before a slot 2 hours away


def _gate(db):
    return release_gate.check(str(db.db_path), now=datetime(2026, 11, 16, 3, 0, tzinfo=UTC))


def test_the_release_gate_counts_an_open_or_stale_review_as_idle(db):
    _review(db, "open")
    _review(db, "stale", topic=db.list_all_topics()[1]["id"])
    db.mark_review_stale("stale")
    assert _gate(db) == []


@pytest.mark.parametrize("state", ["decided", "publishing", "producing", "no card yet"])
def test_the_release_gate_stays_busy_while_a_review_decision_or_run_is_being_acted_on(db, state):
    _review(db)
    if state == "decided":
        db.update_approval_decision("rv", "approved", decided_by="test")
    elif state == "no card yet":
        with db._conn() as conn:
            conn.execute("DELETE FROM approvals WHERE run_id = 'rv'")
    else:
        db.update_run("rv", state)
    assert _gate(db)


def test_the_production_lease_of_a_worker_only_waiting_on_an_undecided_review_is_idle(db, monkeypatch):
    _review(db)
    monkeypatch.setattr(config, "WORKER_ATTEMPT_ID", "attempt-1")
    with main.busy_lease(db, main.PRODUCTION_LEASE):
        assert any("production run" in r for r in _gate(db))         # making the video: busy
        with main.review_wait_lease(db, "rv"):
            assert _gate(db) == []                                     # waiting on the review alone: idle
            db.update_approval_decision("rv", "approved", decided_by="test")
            assert _gate(db)                                           # decided: acting on it is busy
        assert any("production run" in r for r in _gate(db))         # the wait ended: busy again
    lease = json.loads(db.get_setting("busy:" + main.PRODUCTION_LEASE) or "{}")
    assert lease == {}


# ---------------------------------------------------------------------------------------------- round 2
def _card(db, run_id="rv", delivered_ago=0.0, closes_in=None):
    _review(db, run_id)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at = ? WHERE run_id = ?",
                     ((datetime.now(UTC) - timedelta(seconds=delivered_ago)).isoformat(), run_id))
        if closes_in is not None:
            conn.execute("UPDATE approvals SET expires_at = ? WHERE run_id = ?", (time.time() + closes_in, run_id))


def test_a_wait_resumed_after_a_restart_ends_when_the_card_closes_not_a_fresh_window_later(db, monkeypatch):
    """Review 1: a resumed wait sized a fresh window from the restart; between the card's close (taps refused) and the
    wait's end the review was neither open nor stale, with no Resend."""
    _card(db, delivered_ago=5, closes_in=1.0)              # the card delivered before the restart closes in 1 s
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    notices = []
    monkeypatch.setattr(bot, "timeout_notice", lambda run_id: notices.append(run_id))
    started = time.monotonic()
    assert bot.wait_for_decision("rv", timeout_seconds=30, poll_interval=0.05) == "timeout"
    assert time.monotonic() - started < 3                  # at the card's close, not 30 s later
    assert db.review_stale("rv") and notices == ["rv"]      # stale with its Resend notice at the card's expiry
    assert db.get_approval("rv")["decision"] == "pending"


def test_a_resumed_wait_keeps_the_cards_reminder_time(db, monkeypatch):
    sent = []
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    monkeypatch.setattr(bot, "notify", lambda text, *a, **k: sent.append(text) or True)
    monkeypatch.setattr(bot, "timeout_notice", lambda run_id: None)
    _card(db, delivered_ago=120, closes_in=1.0)            # its reminder (60 s after delivery) came before the restart
    bot.wait_for_decision("rv", timeout_seconds=3000, poll_interval=0.05, reminder_after=60,
                          hand_off_at=time.time() + 3600)   # bounded on any code: the card closes in 1 s
    assert not [s for s in sent if "Still waiting" in s]   # not said a second time by the resumed wait


def _worker(db, monkeypatch, now=None):
    monkeypatch.setattr(config, "WORKER_ATTEMPT_ID", "attempt-1")
    db.set_setting(main.DAEMON_SCHEDULE_KEY, json.dumps({"time": "07:00", "timezone": "UTC"}))
    db.set_setting("busy:" + main.PRODUCTION_LEASE, json.dumps({"pid": 1, "started": datetime.now(UTC).isoformat()}))


def test_the_daemons_worker_hands_the_wait_over_fifteen_minutes_before_the_daily_run(db, monkeypatch):
    _worker(db, monkeypatch)
    at = main.daily_hand_off_at(db, now=datetime(2026, 11, 16, 20, 0, tzinfo=UTC))
    assert at == datetime(2026, 11, 17, 6, 45, tzinfo=UTC).timestamp()
    monkeypatch.setattr(config, "WORKER_ATTEMPT_ID", "")
    assert main.daily_hand_off_at(db) is None               # the command line, the bot's own workers: never


def test_a_wait_handed_over_leaves_the_review_open_not_stale(db):
    from src.telegram_bot import HANDED_OFF
    _card(db, closes_in=60)
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    assert bot.wait_for_decision("rv", timeout_seconds=60, poll_interval=0.05, hand_off_at=time.time()) == HANDED_OFF
    assert db.get_approval("rv")["decision"] == "pending" and not db.review_stale("rv")
    assert db.get_run("rv")["status"] == "pending_approval"


def test_the_worker_returns_a_handed_over_review_without_acting(db, monkeypatch):
    from src.telegram_bot import HANDED_OFF
    _review(db)
    _worker(db, monkeypatch)
    acted = []
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c._approval_window = lambda: 60
    c.telegram_bot = SimpleNamespace(wait_for_decision=lambda run_id, window, hand_off_at=None: HANDED_OFF)
    assert c._decide_review("rv", lambda d: acted.append(d)) == (HANDED_OFF, None) and acted == []


def _daily(monkeypatch, result, worker=None, decided=None, settings=None):
    """The daemon's daily job (test_daemon_wiring's stand-ins), its worker returning `result`; the daemon's review
    wait returns `decided`."""
    from apscheduler.schedulers import blocking
    from test_daemon_wiring import FakeScheduler
    FakeScheduler.instances.clear()
    monkeypatch.setattr(blocking, "BlockingScheduler", FakeScheduler)
    notes, followed = [], []
    settings = {} if settings is None else settings
    bot = SimpleNamespace(register_commands=lambda: None, is_configured=lambda: False,
                          notify=lambda text, *a, **k: notes.append((text, k.get("group"))),
                          show_dashboard=lambda *a, **k: None, warm_up=lambda: None, _api=lambda *a, **k: {},
                          send_photo=None, send_audio=None, delete_message=None, chat_id="1")
    db = SimpleNamespace(get_setting=lambda k, d=None: settings.get(k, d), set_setting=settings.__setitem__,
                         run_label=lambda run_id: "Petra", get_run=lambda run_id: {"content_format": "reel"})
    coordinator = SimpleNamespace(telegram_bot=bot, db=db, topic_engine=None,
                                  audio_engine=SimpleNamespace(voice_quota=lambda: None), post_due=lambda: None,
                                  confirm_posted=None, confirm_not_posted=None, simulate_publish=None,
                                  request_approval=lambda run_id, **k: followed.append((run_id, k)) or dict(
                                      decided or {"status": "rejected", "decision": "rejected"}))
    monkeypatch.setattr(main, "PipelineCoordinator", lambda **kw: coordinator)
    monkeypatch.setattr(main, "make_command_handler", lambda *a, **k: (lambda c, a: ""))
    monkeypatch.setattr(main, "is_paused", lambda db: False)
    monkeypatch.setattr("src.control.startup_dashboard_card", lambda *a, **k: ("", []))
    monkeypatch.setattr(main, "ping_healthcheck", lambda: None)
    monkeypatch.setattr(main, "reconcile_interrupted_runs", lambda db, te=None: [])
    monkeypatch.setattr(main, "busy_lease", lambda db, job: __import__("contextlib").nullcontext())
    monkeypatch.setattr(main, "run_in_worker", worker or (lambda fmt, **kw: dict(result)))
    main.main(["daemon", "--time", "07:00", "--dry-run"])
    daily = next(f for f, t, _ in FakeScheduler.instances[-1].jobs
                 if t not in ("interval", None) and getattr(f, "__name__", "") == "production_run")
    return daily, notes, followed


def test_the_daemon_takes_on_a_handed_over_review_and_says_so(monkeypatch):
    daily, notes, followed = _daily(monkeypatch, {"status": "pending_approval", "handed_off": True, "run_id": "r1"})
    daily(content_format="dual")
    for _ in range(100):   # the daemon's wait runs in its own thread
        if followed:
            break
        time.sleep(0.02)
    assert followed and followed[0][0] == "r1" and followed[0][1].get("resend") is False
    assert any("still waits for your review" in text for text, _ in notes)


def test_a_daily_run_that_cannot_start_is_never_skipped_silently(monkeypatch):
    inner = {}

    def worker(fmt, **kw):   # the daily run comes while another run holds the run lock
        inner["notes_before"] = len(notes)
        daily(content_format="dual")
        return {"status": "simulated", "run_id": "r1"}
    daily, notes, _ = _daily(monkeypatch, {}, worker=worker)
    daily(manual=True)
    said = notes[inner["notes_before"]:]
    assert any("Today's run was skipped" in text and group == "needs" for text, group in said)


# ---------------------------------------------------------------------------------------------- round 3
def _jobs():
    from test_daemon_wiring import FakeScheduler
    return [k.get("kwargs") or {} for f, trig, k in FakeScheduler.instances[-1].jobs
            if trig is None and getattr(f, "__name__", "") == "production_run"]


def _run_job(kwargs):
    from test_daemon_wiring import FakeScheduler
    job = next(f for f, trig, k in FakeScheduler.instances[-1].jobs if getattr(f, "__name__", "") == "production_run")
    job(**kwargs)


def _wait_for(check):
    for _ in range(200):
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize("decided, expected", [
    ({"status": "rejected", "decision": "rejected"}, None),                       # the batch's next episode
    ({"decision": "regenerate", "topic_id": "01_topic"}, ["--topic-id", "01_topic", "--regenerate-from", "r1"]),
])
def test_work_a_handed_over_review_starts_while_the_daily_run_holds_the_lock_waits_and_then_starts(
        monkeypatch, decided, expected):
    """Review 2: after a hand-off the decision queued a manual run (the batch's next episode, or a regeneration)
    while the daily run held the run lock; it returned with a log line and was never started, after "queued" /
    "Regenerating". It now waits behind the run in progress (persisted) and starts when that run ends."""
    from src import config as cfg
    settings = {"produce_remaining": "2", "produce_started_at": datetime.now(UTC).isoformat()}
    monkeypatch.setattr(cfg, "BOT_RUNS_DRY", False, raising=False)
    calls, gate = [], {}

    def worker(fmt, **kw):
        calls.append(kw.get("extra"))
        if len(calls) == 1:   # the batch episode: its review goes to the daemon before the daily run
            return {"status": "pending_approval", "handed_off": True, "run_id": "r1"}
        if len(calls) == 2:   # the daily run, holding the lock while the decision comes and queues its work
            gate["while_daily"] = True
            assert _wait_for(lambda: len(_jobs()) >= 1)
            _run_job(_jobs()[0])                  # the queued manual run fires while the daily holds the lock
            assert _wait_for(lambda: settings.get(main.MANUAL_QUEUE_KEY, "[]") != "[]")
        return {"status": "simulated", "run_id": f"r{len(calls)}"}
    daily, _notes, followed = _daily(monkeypatch, {}, worker=worker, decided=decided, settings=settings)
    daily(manual=True)                              # the batch episode, handed over
    assert _wait_for(lambda: followed)              # the daemon followed the review and it was decided
    assert _wait_for(lambda: len(_jobs()) >= 1)     # decision -> a manual run asked for
    daily(content_format="dual")                    # the daily run: the manual run found the lock held and queued
    assert gate.get("while_daily")
    after = _jobs()
    assert len(after) == 2 and after[-1].get("manual") is True     # the daily run ended: the queued run starts
    assert json.loads(settings[main.MANUAL_QUEUE_KEY]) == []
    _run_job(after[-1])
    assert calls[-1] == expected                    # it ran: the episode / the regeneration was not dropped


def test_queued_manual_runs_survive_a_restart(monkeypatch):
    settings = {}
    db = SimpleNamespace(get_setting=lambda k, d=None: settings.get(k, d), set_setting=settings.__setitem__)
    main.queue_manual_run(db, {"content_format": "reel", "extra": ["--topic-id", "t"], "resume": None})
    _daily(monkeypatch, {}, settings=settings)      # the daemon starts again
    assert _jobs() == [{"manual": True, "content_format": "reel", "extra": ["--topic-id", "t"], "resume": None}]
    assert json.loads(settings[main.MANUAL_QUEUE_KEY]) == []


def test_a_review_decision_is_not_acted_on_while_a_release_hold_is_pending(tmp_path, monkeypatch):
    """Review 2: a V1 card or command-line decision is acted on at the next poll, so one recorded after the launcher's
    last gate check could be acted on and killed mid-act. While control/Pipeline.hold exists nothing is acted on."""
    hold = tmp_path / "Pipeline.hold"
    hold.write_text("v1 abc", encoding="ascii")
    monkeypatch.setenv(main.RELEASE_RECEIPT_ENV, str(tmp_path / "release.json"))
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:
            hold.unlink()                           # the install is done (or the hold was declined)
    main.wait_while_release_held(sleep=sleep)
    assert len(slept) == 3 and not hold.exists()
    monkeypatch.delenv(main.RELEASE_RECEIPT_ENV)
    main.wait_while_release_held(sleep=lambda s: pytest.fail("no launcher, no hold: never waits"))


def test_the_review_wait_holds_its_act_until_the_release_hold_clears(db, monkeypatch):
    _review(db)
    db.update_approval_decision("rv", "rejected", decided_by="test")
    order = []
    monkeypatch.setattr(main, "wait_while_release_held", lambda: order.append("held-check"))
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c._approval_window = lambda: 60
    c.telegram_bot = SimpleNamespace(wait_for_decision=lambda run_id, window, **k: "rejected")
    c._decide_review("rv", lambda d: order.append(("act", d)))
    assert order == ["held-check", ("act", "rejected")]
