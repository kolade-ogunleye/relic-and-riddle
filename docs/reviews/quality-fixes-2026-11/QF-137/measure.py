"""QF-137 measurement: do the layout tests hold whenever the background photo-set count
finishes, and does a test start with the count a new process has?

    python measure.py <repo root>

Every case runs pytest in a fresh process on the given checkout (no network, no .env, nothing written to the
checkout; the helper plugin and the inner test go to a throwaway temp folder):

- "alone": each of the three tests that tick the layout (or sync the conditions) more than once and count what
  was sent or changed, alone in its process (the count starts cold, as on a worker where no earlier test counted
  the sets: QF-135's CI shard 6).
- "count finishes between ticks" / "count still running": the same tests with control's counter held by a plugin:
  it counts every pending set the moment it is started, or never runs during the test.
- "left behind": two tests in order in one process; the first leaves the count's state (sets known ready, a pending
  set, a listening bot), the second checks it finds none of it.

Prints one JSON document: per case, the runs and how many passed. Acceptance: every case passes every run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
RACED = [   # the tests that tick the layout (or sync the conditions) more than once and count what was sent or changed
    "tests/test_ui01b_dashboard_in_place.py::test_the_weekly_digest_arrives_silently",
    "tests/test_ui01b_dashboard_in_place.py::test_an_unchanged_condition_writes_nothing_and_a_new_review_rings_again",
    "tests/test_ui01e_review_panel.py::test_the_thirty_minute_reminder_alert_starts_with_its_alarm_clock",
]

PLUGIN = '''
import os
from types import SimpleNamespace

import pytest

MODE = os.environ.get("QF137_MODE", "")


@pytest.fixture(autouse=True)
def qf137_held_counter(monkeypatch):
    if MODE in ("finishes", "never"):
        from src import control

        class Counter:
            def __init__(self, target=None, name=None, daemon=None):
                self.target = target

            def start(self):
                if MODE == "finishes":
                    self.target()   # in the caller's thread: the count's lock is re-entrant
        monkeypatch.setattr(control, "threading", SimpleNamespace(Thread=Counter, Condition=control.threading.Condition))
    yield
'''

LEFT_BEHIND = '''
from src import control


def test_1_leaves_the_count_behind(db):
    for t in [t for t in db.list_all_topics() if t.get("status") == "queued"]:
        key, stamp = control._ready_key(t)
        control._READY_CACHE[key] = (stamp, True)
    control._count_pending["stale"] = {"id": "stale"}
    control._count_shown = True
    control.when_pools_counted(lambda: None)


def test_2_finds_a_new_process_count(db):
    assert control._READY_CACHE == {} and control._count_pending == {}
    assert control._counter is None and control._count_shown is False and control._count_listener is None
'''


def pytest_run(args: list[str], mode: str, helper: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "QF137_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(helper), str(ROOT)])
    env["QF137_MODE"] = mode
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "qf137_held_counter",
                        *args], check=False, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600)
    if r.returncode:   # the failure itself, for a reader (stderr; the JSON keeps the outcome)
        print(f"--- {' '.join(args)} ({mode or 'natural'}): exit {r.returncode}", r.stdout[-3000:], r.stderr[-2000:],
              sep="\n", file=sys.stderr)
    last = [ln for ln in r.stdout.splitlines() if ln.strip()][-1:] or [""]
    sent = "Photo sets not ready" in r.stdout
    return {"test": args[-1].rpartition("::")[2] if "::" in args[-1] else Path(args[-1]).name, "returncode": r.returncode,
            "summary": last[0], "photo_notice_in_failure": sent}


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # the long form: given a short (8.3) temp path, pytest walked the whole temp folder, where other processes'
        # folders come and go mid-collection
        helper = Path(tmp).resolve()
        (helper / "qf137_held_counter.py").write_text(PLUGIN, encoding="utf-8")
        inner = helper / "test_count_left_behind.py"
        inner.write_text(textwrap.dedent(LEFT_BEHIND), encoding="utf-8")
        cases = {
            "alone": [pytest_run([t], "", helper) for t in RACED],
            "count finishes between ticks": [pytest_run([t], "finishes", helper) for t in RACED],
            "count still running": [pytest_run([t], "never", helper) for t in RACED],
            "left behind": [pytest_run(["-p", "tests.conftest", "--rootdir", str(helper), "-c", os.devnull,
                                        str(inner)], "", helper)],
        }
    out = {"cases": {name: {"runs": len(runs), "passed": sum(r["returncode"] == 0 for r in runs),
                                               "detail": runs} for name, runs in cases.items()}}
    out["all_pass"] = all(c["runs"] == c["passed"] for c in out["cases"].values())
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
