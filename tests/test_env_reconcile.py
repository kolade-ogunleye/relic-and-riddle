"""
tests/test_env_reconcile.py
===========================
An environment's .env is brought up to date with the release's template without a secret being shown, copied
or invented, and without changing what the environment runs with (operator, 2026-10-27: production's .env
lagged behind; one by one was too manual; each environment keeps its own providers and caps).
"""

from pathlib import Path

import src.env_sync as env_reconcile

RUNNING = {"PICTURE_VISION_CHECK": True, "PICTURE_CHECK_RETRY_SECONDS": 15.0, "CLAUDE_MONTHLY_BUDGET_USD": 30.0,
           "LIGHT_TASK_MODELS": ["claude-sonnet-5-5", "claude-opus-5-5"]}


def running(name):
    return RUNNING.get(name, env_reconcile.UNKNOWN)


def _files(tmp_path):
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    env.write_bytes(b"ANTHROPIC_API_KEY=sk-real-secret\r\nYOUTUBE_SHORTS=true\r\nOLD_FLAG=1\r\n")
    example.write_text("# a comment\nANTHROPIC_API_KEY=your-key\nYOUTUBE_SHORTS=false\n"
                       "PICTURE_VISION_CHECK=true\nPICTURE_CHECK_RETRY_SECONDS=15\nMETA_PAGE_ACCESS_TOKEN=\n"
                       "TELEGRAM_CHAT_ID=\nCLAUDE_MONTHLY_BUDGET_USD=10\nLIGHT_TASK_MODELS=claude-sonnet-5-5,claude-opus-5-5\n"
                       "COMMONS_CONTACT=you@example.com\n", encoding="utf-8")
    return env, example


def test_the_plan_sorts_what_is_safe_from_what_would_change_behaviour(tmp_path):
    env, example = _files(tmp_path)
    safe, differ, secrets, extra = env_reconcile.plan(env, example, running)
    assert safe == [("PICTURE_VISION_CHECK", "true"), ("PICTURE_CHECK_RETRY_SECONDS", "15"),
                    ("LIGHT_TASK_MODELS", "claude-sonnet-5-5,claude-opus-5-5")]
    assert differ == [("CLAUDE_MONTHLY_BUDGET_USD", "10", 30.0)]                   # a cap is never changed on its own
    assert secrets == ["META_PAGE_ACCESS_TOKEN", "TELEGRAM_CHAT_ID", "COMMONS_CONTACT"]
    assert extra == ["OLD_FLAG"]


def test_the_report_never_shows_a_value_from_env(tmp_path, capsys, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile, "running_value", running)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    assert env_reconcile.main(["--env", str(env), "--example", str(example)]) == 0
    out = capsys.readouterr().out
    assert "PICTURE_VISION_CHECK=true" in out and "CLAUDE_MONTHLY_BUDGET_USD: template '10', runs now with 30.0" in out
    assert "META_PAGE_ACCESS_TOKEN" in out and "OLD_FLAG" in out                   # by name only
    assert "sk-real-secret" not in out and "YOUTUBE_SHORTS=true" not in out
    assert env.read_bytes() == b"ANTHROPIC_API_KEY=sk-real-secret\r\nYOUTUBE_SHORTS=true\r\nOLD_FLAG=1\r\n"   # untouched


def test_apply_backs_up_then_adds_only_what_changes_nothing(tmp_path, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    before = env.read_bytes()
    env_reconcile.main(["--env", str(env), "--example", str(example), "--apply"])
    after = env.read_bytes().decode("utf-8")
    assert after.startswith(before.decode("utf-8"))                                 # every existing line as it was
    assert "PICTURE_VISION_CHECK=true\r\n" in after and "PICTURE_CHECK_RETRY_SECONDS=15\r\n" in after
    assert "CLAUDE_MONTHLY_BUDGET_USD" not in after                                 # would change the cap: not added
    assert "META_PAGE_ACCESS_TOKEN" not in after and "COMMONS_CONTACT" not in after  # nothing invented
    assert after.count("YOUTUBE_SHORTS=") == 1                                      # an existing value never overridden
    backups = list(tmp_path.glob(".env.reconcile-*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert env_reconcile.plan(env, example, running)[0] == []                      # a second run adds nothing


def test_credentials_and_personal_details_are_recognised_by_name():
    for name in ("ANTHROPIC_API_KEY", "META_PAGE_ACCESS_TOKEN", "TELEGRAM_CHAT_ID", "UPLOAD_POST_USER",
                 "TELEGRAM_APPROVER_IDS", "HEALTHCHECK_PING_URL", "ONEDRIVE_CLIENT_SECRET", "COMMONS_CONTACT"):
        assert env_reconcile.is_secret(name), name
    for name in ("PICTURE_VISION_CHECK", "YOUTUBE_SHORTS_PRIVACY", "VIDEO_FORMATS", "SCRIPT_ENGINE",
                 "GEMINI_SPARE_KEY_PERMITTED"):
        assert not env_reconcile.is_secret(name), name


def test_each_daemon_start_writes_in_what_changes_nothing_and_says_the_rest(tmp_path, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    note = env_reconcile.sync_on_start(env, example)
    assert "Added to .env from the template (3)" in note and "PICTURE_VISION_CHECK" in note
    assert "CLAUDE_MONTHLY_BUDGET_USD: template 10, now 30.0" in note             # yours to decide
    assert "META_PAGE_ACCESS_TOKEN" in note and "sk-real-secret" not in note
    assert "PICTURE_VISION_CHECK=true" in env.read_text(encoding="utf-8")
    again = env_reconcile.sync_on_start(env, example)
    assert "Added" not in again and "CLAUDE_MONTHLY_BUDGET_USD" in again          # nothing added twice


def test_a_read_only_env_is_reported_not_changed(tmp_path, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    monkeypatch.setattr(env_reconcile, "apply", lambda *a: (_ for _ in ()).throw(PermissionError("access denied")))
    note = env_reconcile.sync_on_start(env, example)
    assert "could not be written in (access denied)" in note and "PICTURE_VISION_CHECK=true" in note


def test_include_changes_adds_the_differing_ones_only_when_asked(tmp_path, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    env_reconcile.main(["--env", str(env), "--example", str(example), "--apply", "--include-changes"])
    assert "CLAUDE_MONTHLY_BUDGET_USD=10" in env.read_text(encoding="utf-8")


def test_the_daemon_runs_the_check_at_start_and_never_on_the_real_env():
    import main
    import src.env_sync as env_sync
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "sync_on_start()" in source.split('if args.command == "daemon":')[1].split("bot.register_commands()")[0]
    assert env_sync.ROOT != Path(main.config.BASE_DIR)                            # the sandbox, in every test
