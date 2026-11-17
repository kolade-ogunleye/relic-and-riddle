"""QF-48: in production maintenance.apply() raises before any git or pip command, the weekly
report offers no upd:apply button, and check() stays report-only. Development keeps apply()."""

from types import SimpleNamespace

import pytest

from src import config, maintenance
from src.maintenance import Update, UpdatePlan

PLAN = UpdatePlan([Update("pillow", "12.2.0", "12.3.0", "security", ["A"])])


def _recording_run(monkeypatch):
    commands = []

    def run(args, timeout=900):
        commands.append(list(args))
        return SimpleNamespace(returncode=0, stdout="main" if args[:2] == ["git", "rev-parse"] else "", stderr="")
    monkeypatch.setattr(maintenance, "_run", run)
    writes = []
    monkeypatch.setattr(maintenance, "_write_pins", lambda updates: writes.append(updates))
    return commands, writes


def _buttons(plan):
    """The weekly report's buttons, through the module function main.py's daemon uses."""
    fn = getattr(maintenance, "apply_buttons", None)
    if fn is None:  # start commit: main.py built the button whenever the plan had applicable updates
        return [{"text": "🧰 Apply updates", "callback_data": "upd:apply"}] if plan.applicable else None
    return fn(plan)


def test_apply_raises_in_production_before_git_or_pip(monkeypatch):
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "production")
    commands, writes = _recording_run(monkeypatch)
    with pytest.raises(RuntimeError, match="production"):
        maintenance.apply(PLAN)
    assert commands == [] and writes == []


def test_no_apply_button_in_production(monkeypatch):
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "production")
    assert _buttons(PLAN) is None
    assert "Approve to apply" not in maintenance.report_text(PLAN)


def test_development_keeps_apply_and_button(monkeypatch):
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "development")
    commands, writes = _recording_run(monkeypatch)
    maintenance.apply(PLAN)
    assert any(c[:2] == ["git", "commit"] for c in commands) and writes
    assert _buttons(PLAN) == [{"text": "🧰 Apply updates", "callback_data": "upd:apply"}]
    assert "Approve to apply" in maintenance.report_text(PLAN)


def test_main_daemon_uses_the_gated_button():
    # main.py must not build the upd:apply button itself, or production would offer it again.
    source = (config.BASE_DIR / "main.py").read_text(encoding="utf-8")
    assert source.count('"callback_data": "upd:apply"') == 0
    assert "maintenance.apply_buttons(plan)" in source
