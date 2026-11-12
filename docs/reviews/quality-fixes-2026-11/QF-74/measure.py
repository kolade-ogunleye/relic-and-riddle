"""QF-74 acceptance: the run deadline, the missed-slot notice and the stage budgets, on the code of <repo root>.

    python measure.py <repo root>

Runs the code of <repo root> (the base too: there run_in_worker's timeout escapes, nothing stops at a deadline,
no notice names a missed slot and no stage has a budget). Everything is generated in a temporary folder with a
temporary database, a fake worker process and a fake clock: no network, no provider, no real process is started or
stopped. The real-data part reads only real_data.json beside this file (written by real_data.py from the
development runs, with a read-only database copy, and counts and medians from a production-sample copy) and the
budgets in data/stage_budget.json of this commit's tree. Writes nothing outside the temporary folder; prints JSON.
"""

import inspect
import json
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import main
from src import config, post_schedule, stage_timer
from src.db import PipelineDatabase

try:
    from src import worker_deadline as deadline
except ImportError:
    deadline = None
here = Path(__file__).resolve().parent
SPEC = {"script": 20, "voice": 8, "pictures": 25, "render": 15, "qa": 5, "upload": 5}  # the row's budgets
out = {"root_has_worker_deadline": deadline is not None}


def database(folder):
    bank = folder / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": f"0{i}_topic", "title": f"Topic {i}", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}} for i in range(1, 4)]}),
                    encoding="utf-8")
    return PipelineDatabase(db_path=folder / "measure.db", topic_bank_file=bank)


def needs_notices(db, notes):
    return sum(1 for _, kw in notes if kw.get("group") == "needs")


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    config.OUTPUT_DIR = main.OUTPUT_DIR = tmp
    # 1. A worker that never ends: its run claimed under the supervisor's attempt, a fake clock.
    db = database(tmp)
    db.create_run("late", "01_topic")
    db.record_topic_status("01_topic", "in_production", "late")
    (tmp / "late").mkdir()
    (tmp / "late" / "voiceover.mp3").write_bytes(b"kept")
    clock = [0.0]

    class Worker:
        pid, returncode, dead = 4242, None, False

        def poll(self):
            return self.returncode

        def communicate(self, timeout=None):
            if self.dead:
                return None, ""
            clock[0] += timeout
            raise subprocess.TimeoutExpired("worker", timeout)

    proc = Worker()

    def popen(cmd, **kw):
        attempt = (kw.get("env") or {}).get("RELIC_WORKER_ATTEMPT", "")
        db.set_setting("run_owner:late", json.dumps({"attempt": attempt, "pid": proc.pid}))  # as claim_run does
        return proc

    def escaped(*a, **kw):
        raise subprocess.TimeoutExpired("worker", kw.get("timeout") or 1)

    main.subprocess.Popen, main.subprocess.run = popen, escaped
    notes = []
    if deadline is not None:
        deadline.monotonic = lambda: clock[0]
        deadline.stop_worker_tree = lambda p: setattr(p, "dead", True)
    try:
        # the base takes no database (its run_in_worker has no supervisor): called as the daemon calls it there
        takes_db = "db" in inspect.signature(main.run_in_worker).parameters
        result = main.run_in_worker("reel", True, False, timeout=600, **({"db": db} if takes_db else {}))
        worker = result.get("status")
        if result.get("stopped"):
            deadline.finalize_stopped_worker(db, result["attempt"], lambda t, **kw: notes.append((t, kw)), 8, 2)
    except (subprocess.TimeoutExpired, TypeError) as e:  # the base: subprocess.run's timeout escapes
        worker = "escaped " + type(e).__name__
    out["stopped_worker"] = {"worker_result": worker, "run_status": db.get_run("late")["status"],
                             "topic_status": db.get_topic_by_id("01_topic")["status"],
                             "files_kept": (tmp / "late" / "voiceover.mp3").exists(),
                             "needs_notices": needs_notices(db, notes)}

    # 2. In the worker: a stage boundary past the deadline.
    (tmp / "worker").mkdir()
    db2 = database(tmp / "worker")
    coord = object.__new__(main.PipelineCoordinator)
    coord.db, coord.progress_msg_id = db2, None
    coord.topic_engine = SimpleNamespace(record_status=lambda t, s, run_id=None: db2.record_topic_status(t, s, run_id))
    alerts = []
    coord._alert = lambda text, buttons=None, group=None: alerts.append((text, {"group": group}))
    db2.create_run("slow", "01_topic")
    db2.record_topic_status("01_topic", "in_production", "slow")
    (tmp / "slow").mkdir()
    (tmp / "slow" / "voiceover.mp3").write_bytes(b"kept")
    main.claim_run(db2, "slow")
    if deadline is not None:
        deadline._started["slow"] -= config.RUN_DEADLINE_MINUTES * 60 + 1
    try:
        coord._check_cancel("slow")
        stopped = "carried on"
    except main.JobCancelled as e:
        stopped = type(e).__name__
    db2.create_run("review", "02_topic")
    db2.update_run("review", "pending_approval")
    main.claim_run(db2, "review")
    if deadline is not None:
        deadline._started["review"] -= config.RUN_DEADLINE_MINUTES * 60 + 1
    coord._check_cancel("review")
    out["stage_boundary"] = {"stopped_by": stopped, "run_status": db2.get_run("slow")["status"],
                             "topic_status": db2.get_topic_by_id("01_topic")["status"],
                             "files_kept": (tmp / "slow" / "voiceover.mp3").exists(),
                             "needs_notices": needs_notices(db2, alerts),
                             "review_past_deadline": db2.get_run("review")["status"]}

    # 3. A late approval's booking.
    config.POST_TIMEZONE = config.TIMEZONE = "America/New_York"
    config.REEL_POST_TIMES = ["12:00", "19:00"]
    zone = post_schedule.tz()
    import src.release
    src.release.require_release = lambda *a, **k: {"status": "pass"}
    c = main.PipelineCoordinator(db2, schedule_posts=True)
    run_dir = tmp / "booked"
    run_dir.mkdir()
    (run_dir / "m.mp4").write_bytes(b"0")
    payload = {"topic_id": "03_topic", "caption": "c", "title": "t"}
    db2.create_run("booked", "03_topic")
    db2.update_run("booked", "pending_approval", script_payload=payload, master_video_path=run_dir / "m.mp4",
                   thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
    db2.set_setting("run_started:booked", datetime(2026, 11, 11, 9, 0, tzinfo=zone).isoformat())
    said = []
    c._alert = lambda text, buttons=None, group=None: said.append(text)
    c.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda *a: True)
    approved = datetime(2026, 11, 11, 21, 0, tzinfo=zone)
    c.schedule_run("booked", now=approved)
    out["late_booking"] = {"made_for": post_schedule.fmt(post_schedule.next_reel_slot(datetime(2026, 11, 11, 9, 0,
                                                                                               tzinfo=zone))),
                           "approved": post_schedule.fmt(approved),
                           "booked": post_schedule.fmt(datetime.fromisoformat(
                               db2.list_scheduled_posts(("pending",), run_id="booked")[0]["due_at"])),
                           "missed_slot_notices": sum(1 for s in said if "Missed" in s and "slot" in s)}

    # 4. A render over 150% of its budget, retries included.
    timer = stage_timer.StageTimer(tmp / "measured")
    for name, seconds in (("master_render_attempt1", 900), ("master_render_attempt2", 451)):
        timer.record(name, seconds)
    db2.create_run("measured", "01_topic")
    filed = getattr(stage_timer, "file_findings", lambda *a: 0)(db2, "measured", tmp / "measured")
    out["stage_budget"] = {"render_minutes": round(1351 / 60, 1), "findings": filed,
                           "budget_file": (root / "data" / "stage_budget.json").exists()}

# 5. Real runs (committed derived input): what the defaults would have done.
real = json.loads((here / "real_data.json").read_text(encoding="utf-8"))
rows = real["dev"]["rows"]
attempts = sorted(m for r in rows for m in r["attempt_minutes"])
deadline_minutes = getattr(config, "RUN_DEADLINE_MINUTES", None)


def pct(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (position - lo), 1)


out["real_dev"] = {
    "runs": len(rows), "attempts": len(attempts),
    "production_minutes": {"p50": pct(attempts, .5), "p90": pct(attempts, .9), "max": max(attempts)},
    "deadline_minutes": deadline_minutes,
    "attempts_past_deadline": None if deadline_minutes is None else sum(1 for a in attempts if a > deadline_minutes),
    "attempts_past_spec_150": sum(1 for a in attempts if a > 150),
    "longest_step_minutes": max(r["longest_step_minutes"] for r in rows),
    "stages": {group: {"runs": len(v), "p50": pct(v, .5), "p90": pct(v, .9), "max": round(max(v), 1),
                       "budget": SPEC[group], "over_150_percent": sum(1 for x in v if x > SPEC[group] * 1.5)}
               for group in SPEC for v in [[r["stage_minutes"][group] for r in rows if group in r["stage_minutes"]]]
               if v},
    "median_production_minutes": statistics.median(attempts),
}
out["real_prod_counts"] = real.get("prod")
s, b, lb = out["stopped_worker"], out["stage_boundary"], out["late_booking"]
out["acceptance_met"] = bool(
    s["worker_result"] == "timed_out" and s["run_status"] == "timed_out" and s["topic_status"] == "queued"
    and s["files_kept"] and s["needs_notices"] == 1
    and b["stopped_by"] == "RunDeadlineExceeded" and b["run_status"] == "timed_out" and b["topic_status"] == "queued"
    and b["files_kept"] and b["needs_notices"] == 1 and b["review_past_deadline"] == "pending_approval"
    and lb["missed_slot_notices"] == 1 and out["stage_budget"]["findings"] == 1
    and out["real_dev"]["attempts_past_deadline"] == 0
    and (out["real_prod_counts"] or {}).get("attempts_over_minutes", {}).get(str(int(deadline_minutes or 0))) == 0)
print(json.dumps(out, indent=1, ensure_ascii=False))
