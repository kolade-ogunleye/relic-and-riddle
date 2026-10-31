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
    assert "<b>Added to .env</b> (3)" in note and "• <code>PICTURE_VISION_CHECK = true</code>" in note
    assert "• <code>CLAUDE_MONTHLY_BUDGET_USD: template 10 · now 30.0</code>" in note   # yours to decide
    assert "META_PAGE_ACCESS_TOKEN" in note and "sk-real-secret" not in note
    assert "PICTURE_VISION_CHECK=true" in env.read_text(encoding="utf-8")
    again = env_reconcile.sync_on_start(env, example)
    assert "Added" not in again and "CLAUDE_MONTHLY_BUDGET_USD" in again          # nothing added twice


def test_a_read_only_env_is_reported_not_changed(tmp_path, monkeypatch):
    env, example = _files(tmp_path)
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (running,))
    monkeypatch.setattr(env_reconcile, "apply", lambda *a: (_ for _ in ()).throw(PermissionError(
        13, "Permission denied", r"C:pp\.env.reconcile-20261027T221500Z.bak")))
    note = env_reconcile.sync_on_start(env, example)
    # Production's pipeline account may not write the app folder (2026-10-27): said plainly, one setting a line
    assert "This account cannot write .env, so nothing was changed" in note
    assert "<b>Not in .env, already in effect</b> (3)" in note and "nothing needs doing" in note  # outcome first
    assert "Missing from .env" not in note                                        # read as "added" (2026-10-28)
    assert "• <code>PICTURE_VISION_CHECK = true</code>" in note
    assert "Errno" not in note and "reconcile-2026" not in note                   # no raw error or timestamped path
    assert env_reconcile.sync_on_start(env, example) == note                      # the same each start: told once


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


def test_apply_writes_empty_lines_for_credentials_that_run_empty_never_over_a_default(tmp_path, monkeypatch):
    """Operator, 2026-10-28: "reconcile the file", the key's line there to fill in; a default is never blanked."""
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    env.write_text("A=1\n", encoding="utf-8")
    example.write_text("EUROPEANA_API_KEY=\nELEVENLABS_VOICE_ID=pNInz6obpgDQGcFmaJgB\n", encoding="utf-8")
    now = {"EUROPEANA_API_KEY": "", "ELEVENLABS_VOICE_ID": "pNInz6obpgDQGcFmaJgB"}
    monkeypatch.setattr(env_reconcile, "running_value", lambda name: now.get(name, env_reconcile.UNKNOWN))
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (lambda name: now.get(name, env_reconcile.UNKNOWN),))
    env_reconcile.main(["--env", str(env), "--example", str(example), "--apply"])
    text = env.read_text(encoding="utf-8")
    assert "\nEUROPEANA_API_KEY=\n" in text and "ELEVENLABS_VOICE_ID" not in text
    assert text.startswith("A=1\n")


def test_a_photo_key_added_while_the_daemon_runs_is_taken_in_and_masked(tmp_path, monkeypatch):
    """Operator, 2026-10-28: "how do I make it start working right away?" (production's restart needs an admin)."""
    from src import config, ops
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "EUROPEANA_API_KEY", "")
    monkeypatch.setattr(config, "SMITHSONIAN_API_KEY", "already-set-key")
    monkeypatch.delenv("EUROPEANA_API_KEY", raising=False)
    masker = ops.SecretMasker()                                                   # made before the key arrives
    env.write_text("A=1\nEUROPEANA_API_KEY=\n", encoding="utf-8")
    assert env_reconcile.pick_up_credentials(env) == []                           # still empty: nothing
    env.write_text('A=1\nEUROPEANA_API_KEY="eu-live-key-123"\nSMITHSONIAN_API_KEY=other-key\n', encoding="utf-8")
    assert env_reconcile.pick_up_credentials(env) == ["EUROPEANA_API_KEY"]
    assert config.EUROPEANA_API_KEY == "eu-live-key-123"
    assert config.SMITHSONIAN_API_KEY == "already-set-key"                        # a set key waits for a restart
    assert "eu-live-key-123" not in masker.mask("GET failed with eu-live-key-123")
    assert env_reconcile.pick_up_credentials(env) == []                           # said once
    assert env_reconcile.pick_up_credentials(tmp_path / "missing.env") == []      # never raises


def test_live_credentials_are_read_at_each_use_never_copied_at_import():
    """Taking a key in at run time only works where the code reads config.<KEY> when it calls."""
    import re
    src = Path(env_reconcile.__file__).parent
    for path in src.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in env_reconcile.LIVE_CREDENTIALS:
            assert not re.search(rf"from \.?config import[^\n]*\b{name}\b", text), (path.name, name)
            assert not re.search(rf"^\w+\s*=\s*config\.{name}\b", text, re.M), (path.name, name)


def test_the_card_says_which_credentials_are_taken_in_without_a_restart(tmp_path, monkeypatch):
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    env.write_text("A=1\n", encoding="utf-8")
    example.write_text("EUROPEANA_API_KEY=\nKLING_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(env_reconcile.plan, "__defaults__", (lambda name: "",))
    note = env_reconcile.sync_on_start(env, example)
    assert "Add the line to .env yourself" in note
    assert "no restart: EUROPEANA_API_KEY. The others take effect at the next restart." in note


def test_the_daemon_checks_for_new_credentials_every_two_minutes():
    import main
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert 'scheduler.add_job(pick_up_credentials, "interval", minutes=2' in source
