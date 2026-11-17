"""Per-stage wall-clock timings for a run, logged and kept in <run_dir>/timings.json.

Timing is observational only: a failure to record never affects the run.

Stage budgets (QF-74): data/stage_budget.json (config.STAGE_BUDGET_FILE) gives each budgeted stage (script, voice,
pictures, render, QA, upload) its minutes and names the timed steps that belong to it. A run's total for a stage,
retries and resumed attempts included, above warning_multiplier times its budget logs a WARNING when it crosses
the line and files one finding per run and stage (file_findings). Steps outside the budgets (music_and_bed,
review_proxy, image_prep_wait, which overlaps image_prep) are timed but not budgeted.
"""
import json
import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()  # background stages and the main thread share one timings file
FINDING = "operations.stage_budget"


def budgets() -> dict[str, Any]:
    return json.loads(config.STAGE_BUDGET_FILE.read_text(encoding="utf-8"))


def category(stage: str, policy: dict[str, Any]) -> str | None:
    """The budgeted stage a timed step belongs to ("master_render_attempt2" -> "render"), or None."""
    return policy["stages"].get(re.sub(r"\d+$", "", stage))


def totals(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, float]:
    """Seconds per budgeted stage over every attempt in a timings file."""
    out: dict[str, float] = {}
    for row in rows:
        group = category(str(row.get("stage", "")), policy)
        if group:
            out[group] = out.get(group, 0.0) + float(row.get("seconds") or 0)
    return out


def over_budget(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[tuple[str, float, float]]:
    """(stage, seconds, budget seconds) for each budgeted stage above warning_multiplier times its budget."""
    limit = float(policy["warning_multiplier"])
    return [(group, seconds, policy["minutes"][group] * 60.0) for group, seconds in sorted(totals(rows, policy).items())
            if seconds > policy["minutes"][group] * 60.0 * limit]


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _detail(group: str, seconds: float, budget: float, multiplier: float) -> str:
    return (f"{group.capitalize()} took {seconds / 60:.1f} minutes (retries included), more than "
            f"{multiplier * 100:g}% of its {budget / 60:g}-minute budget.")


def file_findings(db, run_id: str, run_dir: Path) -> int:
    """One finding per budgeted stage this run overran (never a second for the same run and stage)."""
    from . import improve
    try:
        policy = budgets()
        over = over_budget(_read(Path(run_dir) / "timings.json"), policy)
        if not over:
            return 0
        improve.ensure_schema(db)
        filed = 0
        for group, seconds, budget in over:
            klass = f"{FINDING}.{group}"
            with db._conn() as conn:
                if conn.execute("SELECT 1 FROM findings WHERE run_id = ? AND klass = ? LIMIT 1",
                                (run_id, klass)).fetchone():
                    continue
            run = db.get_run(run_id) or {}
            filed += improve.record(db, klass, _detail(group, seconds, budget, float(policy["warning_multiplier"])),
                                    "stage_timer", run_id=run_id, topic_id=run.get("topic_id"))
        return filed
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Could not check the stage budgets of %s: %s", run_id, e)
        return 0


def weekly_lines(db, since: str) -> list[str]:
    """For the weekly digest: each budgeted stage's per-run total (retries included) at p50 / p90 over the runs
    made since `since`, against its budget; and how many runs stopped at the run deadline."""
    with db._conn() as conn:
        rows = conn.execute("SELECT run_id, status FROM runs WHERE COALESCE(updated_at, created_at) >= ?",
                            (since,)).fetchall()
    try:
        policy = budgets()
    except (OSError, ValueError) as e:
        log.warning("Stage budgets unreadable: %s", e)
        return []
    per_stage: dict[str, list[float]] = {}
    for row in rows:
        for group, seconds in totals(_read(config.OUTPUT_DIR / row["run_id"] / "timings.json"), policy).items():
            per_stage.setdefault(group, []).append(seconds / 60)
    stopped = sum(1 for row in rows if row["status"] == "timed_out")
    if not per_stage and not stopped:
        return []

    def percentile(values: list[float], fraction: float) -> float:
        values = sorted(values)
        position = (len(values) - 1) * fraction
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    lines = ["\n<b>Stage time</b> (minutes per run, p50 / p90, budget)"]
    lines += [f"• {group.capitalize()}: {percentile(v, .5):.1f} / {percentile(v, .9):.1f} of "
              f"{policy['minutes'][group]:g} ({len(v)} run{'s' if len(v) != 1 else ''})"
              for group in policy["minutes"] for v in [per_stage.get(group)] if v]
    if stopped:
        lines.append(f"• Stopped at the {config.RUN_DEADLINE_MINUTES:g}-minute run deadline: {stopped}")
    return lines


class StageTimer:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "timings.json"

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        finally:
            self.record(name, time.perf_counter() - started, ok)

    def record(self, name: str, seconds: float, ok: bool = True) -> None:
        if not (isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0):
            log.warning("Stage %s: duration %r not recorded (not a finite, non-negative number).", name, seconds)
            return
        log.info("Stage %-24s %7.2fs%s", name, seconds, "" if ok else " (failed)")
        try:
            with _WRITE_LOCK:
                data = _read(self.path)
                before = data[:]
                data.append({"stage": name, "seconds": round(seconds, 3), "ok": ok,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError as e:
            log.debug("Could not record stage timing: %s", e)
            return
        try:
            policy = budgets()
            group = category(name, policy)
            if group and group in {g for g, _, _ in over_budget(data, policy)} - {g for g, _, _ in over_budget(before, policy)}:
                total = totals(data, policy)[group]
                log.warning("Stage budget: %s", _detail(group, total, policy["minutes"][group] * 60.0,
                                                        float(policy["warning_multiplier"])))
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.debug("Stage budget not checked: %s", e)
