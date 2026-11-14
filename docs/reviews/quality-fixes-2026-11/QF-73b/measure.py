"""QF-73b measurement: three weeks of the daemon's carousel slots, twelve scenarios, plan against action.

    python docs/reviews/quality-fixes-2026-11/QF-73b/measure.py <repo root>

Prints JSON and writes nothing in the repository. <repo root> is the code measured (the parent or this row). Every
database is a fresh one in a temporary folder, built from synthetic topics (4 sensitive, 5 not); sockets are refused
for the whole run (no network, no Bot API, no provider call: spend $0); a carousel "posts" by recording its booking
as published, as the publisher would.

Each scenario books one Reel a day for 21 days (production defaults: Reels 12:00/19:00, carousels Tue-Thu at the
other time, 48 h after the Reel to 7 days, one clear audience day between sensitive topics, US Eastern), two days
ahead, a sensitive topic about half the time where spacing allows; every booking has a waiting carousel. The real
PipelineCoordinator._post_carousel_if_due (the spacing watch and the slot loop) runs at every event, one minute
before every carousel slot, at the slot (the daemon's first pass in its window), and at 05:00 and 17:00 every
day, for 31 days, on a frozen clock. The run shares one SQLite connection (the same SQL, far faster on Windows). Modes:
  rolling  nothing is tapped;
  taps     every Post anyway a notice offers is tapped at once;
  held     as taps, and every fourth plain Reel is held 60 h past its slot (a QF-110 hold) before it posts.
Seeds 0-3 x 3 modes = 12 scenarios.

Measured, summed over the scenarios:
  plan_vs_action      slots where the carousel the plan (posting_view.carousel_outlook, what every screen shows)
                      named one minute before the slot is not the one the slot loop posted, or the plan at the slot
                      itself differs from the action
  replies_not_kept    Post anyway replies ("posts at <slot>") whose carousel did not post at that slot
  silent_expiries     carousels that expired with no Needs-you notice about them
  notices_max         the most notices one carousel got (a second one only when its reason changed: in the clean
                      chat both are the one item of its booking's key, replaced)
  same_reason_max     the most notices one carousel got with the same heading (acceptance: at most 1)
  waiting_at_end      carousels still waiting after every deadline passed
"""

import json
import random
import socket
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEEDS = (0, 1, 2, 3)
MODES = ("rolling", "taps", "held")
DAYS = 21
HORIZON_DAYS = 31
SENSITIVE = ("s0", "s1", "s2", "s3")
PLAIN = ("p0", "p1", "p2", "p3", "p4")
SETTINGS = {"CAROUSEL_DAYS": ["tue", "wed", "thu"], "CAROUSEL_GAP_HOURS": 48, "CAROUSEL_MAX_AGE_DAYS": 7,
            "REEL_POST_TIMES": ["12:00", "19:00"], "SENSITIVE_TOPIC_GAP_DAYS": 1,
            "POST_TIMEZONE": "America/New_York", "TIMEZONE": "America/New_York", "TELEGRAM_CLEAN_CHAT": False}


def _no_network(*args, **kwargs):
    raise OSError("QF-73b measure: no network")


class _Patched:
    """Sets module attributes for the run and puts them back after it (the measure runs outside pytest)."""

    def __init__(self):
        self.saved = []

    def set(self, owner, name, value):
        self.saved.append((owner, name, getattr(owner, name, None), hasattr(owner, name)))
        setattr(owner, name, value)

    def restore(self):
        for owner, name, value, had in reversed(self.saved):
            if had:
                setattr(owner, name, value)
            else:
                delattr(owner, name)


def _shared_connection(path):
    import sqlite3
    from contextlib import contextmanager
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    @contextmanager
    def shared():
        with conn:   # commits on success, rolls back on exception, as PipelineDatabase._conn
            yield conn
    shared.close = conn.close
    return shared


def _bank(path):
    topics = [{"id": tid, "title": f"Subject {tid}", "hook": "Why build 40 rooms underground?", "location": {},
               "sources": [{"url": "https://source.example/record"}], "region_group": "sample",
               "sensitivity": ["Human remains"] if tid in SENSITIVE else []} for tid in SENSITIVE + PLAIN]
    path.write_text(json.dumps({"total": len(topics), "topics": topics}), encoding="utf-8")
    return path


def simulate(seed, mode, folder):
    """One scenario; returns its counts and its events (for the regression test's messages)."""
    import main
    from src import config, post_schedule, posting_view
    from src.db import PipelineDatabase

    patched = _Patched()
    db = None

    class Clock(datetime):
        at = None

        @classmethod
        def now(cls, tz=None):
            return cls.at.astimezone(tz) if tz else cls.at.astimezone().replace(tzinfo=None)

    try:
        for name, value in SETTINGS.items():
            patched.set(config, name, value)
        patched.set(posting_view, "datetime", Clock)
        patched.set(main, "datetime", Clock)
        folder.mkdir(parents=True, exist_ok=True)
        db = PipelineDatabase(folder / "sim.db", _bank(folder / "topics.json"))
        db._conn = _shared_connection(db.db_path)   # one connection for the run: the same SQL, far faster
        coord = main.PipelineCoordinator(db, schedule_posts=True)
        rng = random.Random(seed)
        start = datetime(2030, 5, 6, tzinfo=post_schedule.tz()).date()   # a Monday
        alerts, taps, posted = [], [], {}

        def alert(text, buttons=None, group=None):
            alerts.append((Clock.at, text, buttons, group))
        coord._alert = alert

        def shown(run_id, kind, when):
            db.record_publication(db.get_run(run_id)["topic_id"], run_id, f"upload-post:{kind}", "published", {})
            with db._conn() as conn:
                conn.execute("UPDATE publications SET created_at = ? WHERE id = (SELECT MAX(id) FROM publications)",
                             (when.astimezone(UTC).isoformat(),))

        def post_photos(post, run, kind):
            slot = post_schedule.carousel_time(Clock.at.astimezone(post_schedule.tz()).date())
            posted[post["id"]] = slot
            db.update_scheduled_post(post["id"], "published")
            shown(post["run_id"], kind, Clock.at)
            return "published"
        coord._post_photos = post_photos

        pending = []   # (when, order, action): bookings, then the Reels they book
        bookings = {}

        def book(day):
            run_id = f"r{day:02d}"
            due = post_schedule.reel_time(start + timedelta(days=day))
            late = mode == "held" and day % 3 == 1   # a plain Reel held 60 h past its slot (a QF-110 hold)
            sensitive = rng.random() < 0.55 and not late
            order = list(SENSITIVE if sensitive else PLAIN)
            rng.shuffle(order)
            topic = next((t for t in order if not db.topic_problem(t, due, spacing_only=True)), None)
            if topic is None:
                topic = rng.choice(PLAIN)
            db.create_run(run_id, topic)
            run_dir = folder / run_id
            run_dir.mkdir()
            (run_dir / "slide.jpg").write_bytes(b"x")
            db.update_run(run_id, "approved", master_video_path=run_dir / "m.mp4",
                          script_payload={"title": f"Episode {run_id}"})
            reel = db.add_scheduled_post(run_id, "reel", due.astimezone(UTC).isoformat(), [])
            car = db.add_scheduled_post(run_id, "carousel", None, [str(run_dir / "slide.jpg")], status="waiting")
            bookings[run_id] = car
            at = due + (timedelta(hours=60) if late else timedelta(minutes=1))
            pending.append((at, 0, lambda: reel_posted(run_id, reel, at)))

        def reel_posted(run_id, reel, when):
            db.update_run(run_id, "published")
            db.update_scheduled_post(reel, "published", detail={"posted_at": when.astimezone(UTC).isoformat()})
            shown(run_id, "reel", when)

        first = post_schedule.reel_time(start) - timedelta(days=2)
        for day in range(DAYS):
            due = post_schedule.reel_time(start + timedelta(days=day))
            pending.append((due - timedelta(days=2), 1, lambda day=day: book(day)))
        checks = {}   # slot -> {"before": the plan's pick a minute before, "at": the plan's pick at the slot}
        times = set()
        for n in range(-2, HORIZON_DAYS + 3):
            day = start + timedelta(days=n)
            times |= {datetime.combine(day, datetime.min.time().replace(hour=h), post_schedule.tz()) for h in (5, 17)}
            slot = post_schedule.carousel_time(day)
            if slot:   # the daemon's pass a minute before the slot, and its first pass inside the slot's window
                checks[slot] = {}
                times |= {slot - timedelta(minutes=1), slot}
        end = first + timedelta(days=HORIZON_DAYS + 3)
        said = 0
        while True:
            # the next moment: a scheduled pass or an event (events added while running are taken in turn)
            pending.sort(key=lambda e: (e[0], e[1]))
            nxt = min([t for t in times if t <= end] + [e[0] for e in pending], default=None)
            if nxt is None:
                break
            times.discard(nxt)
            while pending and pending[0][0] <= nxt:
                pending.pop(0)[2]()
            Clock.at = nxt
            slot = post_schedule.carousel_time(nxt.astimezone(post_schedule.tz()).date())
            plan = posting_view.carousel_outlook(db, nxt)["plan"]
            if slot in checks and nxt == slot - timedelta(minutes=1):
                checks[slot]["before"] = next((pid for pid, s in plan.items() if s == slot), None)
            if slot in checks and nxt == slot:
                checks[slot]["at"] = next((pid for pid, s in plan.items() if s == slot), None)
            coord._post_carousel_if_due(nxt)
            if mode != "rolling":   # every Post anyway a new notice offers is tapped at once
                for _when, _text, buttons, _group in alerts[said:]:
                    for row in buttons or []:
                        for button in row:
                            if button.get("callback_data", "").startswith("carspace:"):
                                pid = int(button["callback_data"].split(":")[1])
                                reply = coord.post_carousel_anyway(pid)
                                taps.append((nxt, pid, str(reply), bool(getattr(reply, "done", True))))
                said = len(alerts)
            if nxt >= end and not pending:
                break
        cars = {p["id"]: p for p in db.list_scheduled_posts(
            ("waiting", "published", "expired", "failed", "cancelled")) if p["kind"] == "carousel"}
        notices = {pid: sum(1 for _w, _t, b, g in alerts if g == "needs" and any(
            x.get("callback_data") == f"carexpire:{pid}" for row in b or [] for x in row)) for pid in cars}
        for slot, check in checks.items():
            check["posted"] = next((pid for pid, s in posted.items() if s == slot), None)
        mismatches = [{"slot": s.isoformat(), **c} for s, c in sorted(checks.items())
                      if c.get("before") != c["posted"] or c.get("at") != c["posted"]]
        broken = []
        for at, pid, reply, done in taps:
            if not done or "posts at" not in reply:
                continue
            slot = posted.get(pid)
            if slot is None or post_schedule.fmt(slot) not in reply:
                broken.append({"tapped": at.isoformat(), "carousel": pid, "posted": slot.isoformat() if slot else None,
                               "reply": reply})
        expired = [pid for pid, p in cars.items() if p["status"] == "expired"]
        heads = {}   # (carousel, heading) -> notices: a notice is said again only when its reason changed
        for _w, text, buttons, group in alerts:
            for row in buttons or []:
                for x in row:
                    if group == "needs" and str(x.get("callback_data", "")).startswith("carexpire:"):
                        key = (x["callback_data"], text.split(chr(10))[0])
                        heads[key] = heads.get(key, 0) + 1
        return {
            "carousels": len(cars), "posted": len(posted), "expired": len(expired),
            "slots": len(checks), "plan_vs_action": len(mismatches),
            "taps": len(taps), "replies_done": sum(1 for t in taps if t[3] and "posts at" in t[2]),
            "replies_not_kept": len(broken),
            "silent_expiries": sum(1 for pid in expired if notices[pid] == 0),
            "notices_max": max(notices.values(), default=0),
            "same_reason_max": max(heads.values(), default=0),
            "waiting_at_end": sum(1 for p in cars.values() if p["status"] == "waiting"),
            "detail": {"mismatches": mismatches, "replies_not_kept": broken,
                       "silent": [pid for pid in expired if notices[pid] == 0],
                       "repeated": {pid: [(w.isoformat(), t.split(chr(10))[0]) for w, t, bt, g in alerts if g == "needs"
                                          and any(x.get("callback_data") == f"carexpire:{pid}"
                                                  for row in bt or [] for x in row)]
                                    for pid, n in notices.items() if n > 1}},
        }
    finally:
        patched.restore()
        if db is not None and hasattr(db._conn, "close"):
            db._conn.close()


def run_all():
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in SEEDS:
            for mode in MODES:
                results[f"{mode}-{seed}"] = simulate(seed, mode, Path(tmp) / f"{mode}-{seed}")
    keys = ("carousels", "posted", "expired", "slots", "plan_vs_action", "taps", "replies_done", "replies_not_kept",
            "silent_expiries", "waiting_at_end")
    total = {k: sum(r[k] for r in results.values()) for k in keys}
    total["notices_max"] = max(r["notices_max"] for r in results.values())
    total["same_reason_max"] = max(r["same_reason_max"] for r in results.values())
    total["scenarios"] = len(results)
    total["acceptance_met"] = (total["plan_vs_action"] == 0 and total["replies_not_kept"] == 0
                               and total["silent_expiries"] == 0 and total["same_reason_max"] <= 1
                               and total["waiting_at_end"] == 0)
    return total, results


def main_cli():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    import os
    os.chdir(root)
    socket.socket.connect = _no_network
    socket.create_connection = _no_network
    started = time.perf_counter()
    import logging
    logging.disable(logging.CRITICAL)
    total, results = run_all()
    out = {"total": total,
           "scenarios": {name: {k: v for k, v in r.items() if k != "detail"} for name, r in results.items()},
           "runtime_seconds": round(time.perf_counter() - started, 1)}
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main_cli()
