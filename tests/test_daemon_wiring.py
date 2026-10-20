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
                          notify=lambda *a, **k: None, _api=lambda *a, **k: {},
                          send_photo=None, send_audio=None, delete_message=None, chat_id="1")
    coordinator = SimpleNamespace(telegram_bot=bot, db=SimpleNamespace(),
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
    interval_jobs = [(f, t, k) for f, t, k in jobs if t == "interval"]
    assert len(interval_jobs) == 1 and interval_jobs[0][2].get("minutes") == 1
    interval_jobs[0][0]()  # the registered job publishes due posts
    assert daemon_env == [1]


def test_daemon_skips_posting_job_when_scheduling_is_off(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", False)
    assert main.main(["daemon", "--time", "07:00"]) == 0
    assert not [t for _, t, _ in FakeScheduler.instances[-1].jobs if t == "interval"]


def test_scheduled_posting_holds_while_paused(daemon_env, monkeypatch):
    monkeypatch.setattr(main, "POST_SCHEDULING", True)
    monkeypatch.setattr(main, "is_paused", lambda db: True)
    main.main(["daemon", "--time", "07:00"])
    job = next(f for f, t, _ in FakeScheduler.instances[-1].jobs if t == "interval")
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
