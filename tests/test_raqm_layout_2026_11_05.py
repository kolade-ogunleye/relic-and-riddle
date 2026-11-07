"""Operator decision 2026-11-05: Pillow's raqm layout everywhere. A run warns at start when raqm is unavailable,
and the build that runs this suite has it (CI installs libfribidi0; Windows needs libfribidi-0.dll on PATH)."""
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parents[1]


def test_this_build_lays_text_out_with_raqm():
    from PIL import features
    assert features.check("raqm"), "Pillow raqm layout is unavailable: install FriBiDi (docs/HANDOVER-design.md)"


def test_no_warning_with_raqm(monkeypatch):
    from PIL import features
    monkeypatch.setattr(features, "check", lambda name: True)
    assert main.warn_missing_raqm() == []


def test_a_warning_without_raqm_names_the_fix(monkeypatch, caplog):
    from PIL import features
    monkeypatch.setattr(features, "check", lambda name: False)
    lines = main.warn_missing_raqm()
    assert len(lines) == 1 and "raqm" in lines[0] and "libfribidi-0.dll" in lines[0] and "libfribidi0" in lines[0]
    assert lines[0] in caplog.text


def test_the_run_start_calls_it_before_the_health_gate():
    src = (ROOT / "main.py").read_text("utf-8")
    assert "warn_missing_raqm()" in src
    assert src.index("        warn_missing_raqm()") < src.index('if not health["all_passed"]:')


def test_ci_installs_fribidi():
    assert "libfribidi0" in (ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
