"""QF-137: tests that tick the layout do not race the background photo-set counter.

control.ready_pools(wait=0) (a layout tick's notice conditions, QF-115) answers None while the queued topics' photo
sets are being counted and starts a daemon counter; the answers, the pending sets, the counter and the bot listening
for it are module state. On a pytest-xdist worker that state outlived its test, so a test saw what an earlier test
on the worker had left: on QF-135's CI shard 6 the counter finished between the two ticks of
test_the_weekly_digest_arrives_silently every run and "🔔 Photo sets not ready" was sent where nothing may be.

Both ways it can go are held here deterministically, each test in a new process (no earlier test counted the sets,
as on that worker): control's counter is made to finish the moment it is started (the race at its worst: the tick
that started it still answers None, the next tick finds the count done), or never to run within the test. And a
test that leaves the count's state behind is followed by one that must find it as a new process has it."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# the tests that tick the layout (or sync the conditions) more than once and count what was sent or changed
RACED = [
    "tests/test_ui01b_dashboard_in_place.py::test_the_weekly_digest_arrives_silently",
    "tests/test_ui01b_dashboard_in_place.py::test_an_unchanged_condition_writes_nothing_and_a_new_review_rings_again",
    "tests/test_ui01e_review_panel.py::test_the_thirty_minute_reminder_alert_starts_with_its_alarm_clock",
]

HELD_COUNTER = '''
import os
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def qf137_held_counter(monkeypatch):
    """control's background counter, held: "finishes" counts every pending set the moment it is started (in the
    caller's thread; the count's lock is re-entrant); "never" does not run it during the test."""
    mode = os.environ["QF137_MODE"]
    from src import control

    class Counter:
        def __init__(self, target=None, name=None, daemon=None):
            self.target = target

        def start(self):
            if mode == "finishes":
                self.target()
    monkeypatch.setattr(control, "threading", SimpleNamespace(Thread=Counter, Condition=control.threading.Condition))
    yield
'''

LEAVES_AND_FINDS = '''
from src import control


def test_1_leaves_the_count_behind(db):
    queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
    for t in queued:   # every set "known ready", as a test that stubbed pool_ready to True left them
        key, stamp = control._ready_key(t)
        control._READY_CACHE[key] = (stamp, True)
    control._count_pending["stale"] = {"id": "stale"}
    control._count_shown = True
    control.when_pools_counted(lambda: None)   # an earlier test's bot, still listening


def test_2_finds_a_new_process_count(db):
    queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
    assert control._READY_CACHE == {} and control._count_pending == {}, "an earlier test's count was left"
    assert control._counter is None and control._count_shown is False and control._count_listener is None
    assert control.ready_pools(db, queued) == 0   # counted here, not taken from what the earlier test left
'''


def pytest_in_a_new_process(tmp_path, args, mode=""):
    (tmp_path / "qf137_held_counter.py").write_text(HELD_COUNTER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "QF137_"))}
    env.update(PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]), QF137_MODE=mode)
    plugin = ["-p", "qf137_held_counter"] if mode else []
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *plugin, *args],
                          check=False, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=600)


@pytest.mark.parametrize("mode", ["finishes", "never"], ids=["count-finishes-between-ticks", "count-still-running"])
def test_the_layout_tests_hold_whenever_the_count_finishes(tmp_path, mode):
    result = pytest_in_a_new_process(tmp_path, RACED, mode)
    assert result.returncode == 0 and f"{len(RACED)} passed" in result.stdout, result.stdout[-4000:]


def test_a_test_starts_with_the_count_a_new_process_has(tmp_path):
    """The fixture: whatever count state one test leaves, the next test on the same worker starts without it. The
    two tests run in order, in one process, under the suite's conftest."""
    inner = tmp_path / "test_count_left_behind.py"
    inner.write_text(textwrap.dedent(LEAVES_AND_FINDS), encoding="utf-8")
    result = pytest_in_a_new_process(tmp_path, ["-p", "tests.conftest", "--rootdir", str(tmp_path), "-c", os.devnull,
                                                str(inner)])
    assert result.returncode == 0 and "2 passed" in result.stdout, result.stdout[-3000:] + result.stderr[-2000:]
