"""
scripts/release_gate.py
=======================
Says whether production is idle enough for the release deployer to stop it, from the database
alone (read-only). It runs from the release that is currently installed, under the deployer's own
account, which cannot read .env, so it imports nothing from src.

Busy means any of:
- a run is being made, waits for its review, or is approved and about to publish;
- a review is open (its wait lives in the run's worker process);
- a scheduled post is due within the next hour;
- a daemon job holds a busy lease (fact sheets, the improvement cycle, package updates);
- the daily run starts within 45 minutes or began less than 15 minutes ago.

Prints one JSON object: {"idle": bool, "reasons": [...]}; exit code 0 when idle, 3 when busy,
2 when the state cannot be read (the deployer then waits, never guesses).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

BUSY_RUN_STATUSES = ("producing", "pending_approval", "approved")
POST_HORIZON = timedelta(hours=1)
BEFORE_DAILY = timedelta(minutes=45)
AFTER_DAILY = timedelta(minutes=15)
LEASE_MAX_AGE = timedelta(hours=6)  # a lease older than this belongs to a process that died
SCHEDULE_KEY = "daemon_schedule"
LEASE_PREFIX = "busy:"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _daily_reason(schedule: Optional[str], now: datetime) -> Optional[str]:
    if not schedule:
        return None
    from zoneinfo import ZoneInfo
    data = json.loads(schedule)
    hour, minute = (int(x) for x in data["time"].split(":"))
    local = now.astimezone(ZoneInfo(data["timezone"]))
    for day in (-1, 0, 1):
        start = (local + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if start - BEFORE_DAILY <= local <= start + AFTER_DAILY:
            return f"the daily run is at {data['time']} {data['timezone']}"
    return None


def check(db_path: str, now: Optional[datetime] = None) -> List[str]:
    """The reasons production is busy (empty: idle)."""
    now = now or datetime.now(timezone.utc)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        reasons = []
        marks = ",".join("?" * len(BUSY_RUN_STATUSES))
        for row in db.execute(f"SELECT run_id, status FROM runs WHERE status IN ({marks})", BUSY_RUN_STATUSES):
            reasons.append(f"run {row['run_id']} is {row['status']}")
        open_reviews = db.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'pending'").fetchone()[0]
        if open_reviews:
            reasons.append(f"{open_reviews} review(s) open")
        for row in db.execute("SELECT kind, due_at FROM scheduled_posts WHERE status = 'pending'"):
            due = _parse(row["due_at"])
            if due and due - now <= POST_HORIZON:
                reasons.append(f"a {row['kind']} post is due at {row['due_at']}")
        settings = dict(db.execute("SELECT key, value FROM settings WHERE key = ? OR key LIKE ?",
                                   (SCHEDULE_KEY, LEASE_PREFIX + "%")).fetchall())
        for key, value in settings.items():
            if key.startswith(LEASE_PREFIX) and value:
                started = _parse(json.loads(value).get("started"))
                if started and now - started < LEASE_MAX_AGE:
                    reasons.append(f"the daemon is running {key[len(LEASE_PREFIX):]}")
        daily = _daily_reason(settings.get(SCHEDULE_KEY), now)
        if daily:
            reasons.append(daily)
        return reasons
    finally:
        db.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", required=True, help="Path to the production pipeline.db")
    args = parser.parse_args(argv)
    try:
        reasons = check(args.db)
    except Exception as e:  # unreadable state is never "idle"
        print(json.dumps({"idle": False, "reasons": [f"state unreadable: {type(e).__name__}: {e}"]}))
        return 2
    print(json.dumps({"idle": not reasons, "reasons": reasons}))
    return 0 if not reasons else 3


if __name__ == "__main__":
    sys.exit(main())
