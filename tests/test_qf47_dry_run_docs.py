"""QF-47: the docs said production runs `daemon --dry-run` while the launcher runs `daemon` live (operator
activation, 2026-10-25), and CI tested an obsolete handoff copy of the launcher that still said --dry-run.

The four docs must not claim a dry-run production; the handoff launcher copies are gone and CI runs the
maintained `deployment/windows/runtime/tests`; the dashboard masthead names the mode the process recorded."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("docs/deployment.md", "docs/runtime-contract.md", "docs/unattended-startup.md",
        "docs/production-probe-handoff.md")
# Every phrasing the four docs used for "production is a dry run".
DRY_PRODUCTION = re.compile(r"daemon\s+--dry-run|(?:remains|continues|stays|keeps)\s+(?:in\s+)?`?(?:--)?dry-run`?"
                            r"(?:\s+mode)?", re.IGNORECASE)


@pytest.mark.parametrize("doc", DOCS)
def test_no_doc_says_production_is_a_dry_run(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    found = [m.group(0) for m in DRY_PRODUCTION.finditer(text)]
    assert not found, f"{doc} still says production is a dry run: {found}"


@pytest.mark.parametrize("doc", DOCS)
def test_each_doc_says_production_posts_live(doc):
    text = re.sub(r"\s+", " ", (ROOT / doc).read_text(encoding="utf-8"))
    assert re.search(r"\blive\b", text, re.IGNORECASE), f"{doc} does not say production posts live"


def test_the_launcher_the_docs_describe_runs_the_daemon_live():
    source = (ROOT / "deployment/windows/runtime/content_runtime.py").read_text(encoding="utf-8")
    argv = re.search(r'sys\.argv = \[str\(settings\.app_root / "main\.py"\), ([^\]]*)\]', source)
    assert argv and argv.group(1).strip() == '"daemon"'


def test_the_obsolete_handoff_launcher_copies_are_gone():
    handoff = ROOT / "deployment/windows/handoff"
    assert not (handoff / "tools").exists() and not (handoff / "tests").exists()


def test_ci_tests_the_installed_runtime_copy():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert "handoff" not in workflow
    assert re.search(r"working-directory:\s*deployment/windows/runtime\s*\n\s*run:\s*python -m unittest discover -s tests",
                     workflow)
    tests = ROOT / "deployment/windows/runtime/tests/test_content_runtime.py"
    assert tests.exists() and "import content_runtime" in tests.read_text(encoding="utf-8")


@pytest.mark.parametrize("dry_run, label, other", [(False, "📡 LIVE posting", "🧪 dry run"),
                                                   (True, "🧪 dry run", "📡 LIVE posting")])
def test_the_dashboard_masthead_names_the_mode(db, monkeypatch, dry_run, label, other):
    from src import config, control
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1:test")
    monkeypatch.setattr("src.release_info.release_label", lambda root=None: None)
    control.record_run_mode(db, dry_run)
    text, _ = control.dashboard_text_and_buttons(db, None)
    masthead = text.split("\n", 1)[0]
    assert masthead.startswith("🏛️") and label in masthead and other not in text


def test_an_unrecorded_mode_claims_neither(db, monkeypatch):
    from src import config, control
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1:test")
    monkeypatch.setattr("src.release_info.release_label", lambda root=None: None)
    masthead = control.dashboard_text_and_buttons(db, None)[0].split("\n", 1)[0]
    assert masthead.startswith("🏛️") and "LIVE posting" not in masthead and "dry run" not in masthead


def test_the_bot_and_the_daemon_record_their_mode():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "record_run_mode(db, config.BOT_RUNS_DRY)" in main
    assert "record_run_mode(coordinator.db, bool(args.dry_run))" in main
