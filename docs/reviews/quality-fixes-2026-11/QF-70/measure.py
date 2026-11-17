"""QF-70 measurements: what a review window that passes undecided does to the run and its topic, how long a window is
at least, and whether an open review holds a release.

    python measure.py <repo root>

Runs on the base (QF-69's commit) and on this commit. Generated inputs only: a throwaway database and app root in the
system temp folder (no .env: that app root has none); the measured root's src/, main.py and scripts/release_gate.py.
Nothing is sent (the bot has no token and polls nothing).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="qf70-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)   # its .env is the app root's, and this one has none
os.environ.pop("APPROVAL_TIMEOUT_SECONDS", None)
for tracked in subprocess.run(["git", "-C", str(ROOT), "ls-files", "data", "fonts"], capture_output=True, text=True,
                              check=True).stdout.splitlines():
    (TMP / tracked).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / tracked, TMP / tracked)
sys.path.insert(0, str(ROOT))
_N = [0]
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)   # clear of any daily run the gate checks (none is set)


def database():
    from src.db import PipelineDatabase
    _N[0] += 1
    bank = TMP / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": f"0{i}_topic", "title": f"Topic {i}", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}} for i in (1, 2, 3)]}),
                    encoding="utf-8")
    return PipelineDatabase(db_path=TMP / f"m{_N[0]}.db", topic_bank_file=bank)


def review(db, run_id="rv", topic="01_topic"):
    db.create_run(run_id, topic)
    db.record_topic_status(topic, "in_production", run_id=run_id)
    db.update_run(run_id, "pending_approval")
    db.record_approval(run_id, topic, 700)


def wait_out(db, run_id="rv"):
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    outcome = bot.wait_for_decision(run_id, timeout_seconds=0.3, poll_interval=0.05)
    run = db.get_run(run_id)
    c = coordinator(db)
    c._settle_decision(run_id, run["topic_id"], outcome)   # what every wait's caller does with it
    return outcome


def coordinator(db):
    import main
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.topic_engine = SimpleNamespace(record_status=lambda tid, status, run_id=None: db.record_topic_status(
        tid, status, run_id=run_id))
    c._alert = lambda *a, **k: None
    return c


def picks_topic_again(db, topic="01_topic", days=0.0, hours=0.0) -> bool:
    """Whether the topic of the run awaiting review is made again: every other topic published first."""
    from src.db import QueueEmpty
    ago = (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()
    with db._conn() as conn:
        conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = 'rv'", (ago,))
        conn.execute("UPDATE topics SET updated_at = ? WHERE id = ?", (ago, topic))
        conn.execute("UPDATE settings SET value = ? WHERE key LIKE 'review-stale:%'", (ago,))
    for _ in range(3):
        try:
            picked = db.get_next_topic()
        except QueueEmpty:
            return False
        if picked["id"] == topic:
            return True
        db.record_topic_status(picked["id"], "published")
    return False


def floor() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "APPROVAL_TIMEOUT_SECONDS"}
    env.update(APPROVAL_TIMEOUT_SECONDS="3600", PYTHONDONTWRITEBYTECODE="1")
    configured = int(subprocess.run([sys.executable, "-c", "from src import config; print(config.APPROVAL_TIMEOUT_SECONDS)"],
                                    cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout.split()[-1])
    import main
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c._booked_reel_days = lambda: set()
    main.POST_SCHEDULING = True
    real = main.post_schedule.next_reel_slot
    main.post_schedule.next_reel_slot = lambda n, b: n + timedelta(hours=2)
    try:
        window = c._approval_window()
    finally:
        main.post_schedule.next_reel_slot = real
    return {"timeout_with_APPROVAL_TIMEOUT_SECONDS_3600_s": configured, "window_with_a_slot_2_h_away_s": window}


def gate(db) -> list:
    sys.path.insert(0, str(ROOT / "scripts"))
    import release_gate
    return release_gate.check(str(db.db_path), now=NOW)


def gate_cases() -> dict:
    import main
    from src import config
    out = {}
    db = database()
    review(db)
    out["open undecided review"] = gate(db)
    db = database()
    review(db)
    wait_out(db)
    out["review past its window"] = gate(db)
    db = database()
    review(db)
    config.WORKER_ATTEMPT_ID = "measure"
    with main.busy_lease(db, "the production run"):
        marker = getattr(main, "review_wait_lease", None)
        if marker is None:   # the base: the worker holds the lease while it waits
            out["production worker waiting on that review"] = gate(db)
        else:
            with marker(db, "rv"):
                out["production worker waiting on that review"] = gate(db)
    db = database()
    review(db)
    db.update_approval_decision("rv", "approved", decided_by="measure")
    out["control: decided review, not acted on yet"] = gate(db)
    db = database()
    review(db)
    db.update_run("rv", "publishing")
    out["control: publishing"] = gate(db)
    return {k: {"idle": not v, "reasons": len(v)} for k, v in out.items()}


def resumed_wait() -> dict:
    """A wait resumed after a restart on a card delivered 5 s before, which closes 1 s from now (a 4 s wait)."""
    import time

    from src.telegram_bot import TelegramApprovalBot
    db = database()
    review(db)
    with db._conn() as conn:
        conn.execute("UPDATE approvals SET created_at = ?, expires_at = ? WHERE run_id = 'rv'",
                     ((datetime.now(UTC) - timedelta(seconds=5)).isoformat(), time.time() + 1.0))
    closes = time.monotonic() + 1.0
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    bot.timeout_notice = lambda run_id: None
    bot.wait_for_decision("rv", timeout_seconds=4, poll_interval=0.05)
    ended_after_close = round(max(0.0, time.monotonic() - closes))   # whole seconds: timing jitter is not a result
    stale = getattr(db, "review_stale", lambda r: False)("rv")
    return {"wait_ends_after_the_card_closed_s": ended_after_close, "stale_when_the_wait_ends": bool(stale)}


def hand_off() -> dict:
    """The daemon's worker waiting on an undecided review when the daily run nears: does the wait end (the worker
    then ends and frees the run lock the daily run needs), leaving the review open?"""
    import inspect
    import time

    from src.telegram_bot import TelegramApprovalBot
    db = database()
    review(db)
    bot = TelegramApprovalBot(db=db, bot_token="", chat_id="")
    bot.timeout_notice = lambda run_id: None
    if "hand_off_at" not in inspect.signature(bot.wait_for_decision).parameters:
        return {"worker_wait_ends_before_the_daily_run": False, "review_still_open": True}
    started = time.monotonic()
    bot.wait_for_decision("rv", timeout_seconds=3, poll_interval=0.05, hand_off_at=time.time())
    return {"worker_wait_ends_before_the_daily_run": time.monotonic() - started < 2,
            "review_still_open": db.get_approval("rv")["decision"] == "pending"
                                 and not getattr(db, "review_stale", lambda r: False)("rv")}


def main_() -> None:
    try:
        db = database()
        review(db)
        outcome = wait_out(db)
        after_window = {"wait_outcome": outcome, "decision": db.get_approval("rv")["decision"],
                        "run": db.get_run("rv")["status"], "topic": db.get_topic_by_id("01_topic")["status"],
                        "listed_under_timed_out_reviews": [r["run_id"] for r in db.timed_out_reviews()] == ["rv"]}
        made_again_after_8_days = picks_topic_again(db, days=8)
        db = database()
        review(db)
        made_again_7_h_into_an_open_review = picks_topic_again(db, hours=7)
        gates = gate_cases()
        resumed = resumed_wait()
        handed = hand_off()
        result = {"after_the_window": after_window, "resumed_wait": resumed, "daily_run_hand_off": handed,
                  "topic_made_again_8_days_after_a_missed_window": made_again_after_8_days,
                  "topic_made_again_7_h_into_an_open_review": made_again_7_h_into_an_open_review,
                  "floor": floor(), "release_gate": gates}
        result["acceptance_met"] = (after_window["topic"] == "queued" and after_window["decision"] == "pending"
                                    and not made_again_after_8_days and not made_again_7_h_into_an_open_review
                                    and result["floor"]["timeout_with_APPROVAL_TIMEOUT_SECONDS_3600_s"] >= 43200
                                    and result["floor"]["window_with_a_slot_2_h_away_s"] >= 43200
                                    and gates["open undecided review"]["idle"]
                                    and gates["review past its window"]["idle"]
                                    and gates["production worker waiting on that review"]["idle"]
                                    and not gates["control: decided review, not acted on yet"]["idle"]
                                    and not gates["control: publishing"]["idle"]
                                    and resumed["wait_ends_after_the_card_closed_s"] <= 0.5
                                    and resumed["stale_when_the_wait_ends"]
                                    and handed["worker_wait_ends_before_the_daily_run"] and handed["review_still_open"])
        print(json.dumps(result, indent=1))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main_()
