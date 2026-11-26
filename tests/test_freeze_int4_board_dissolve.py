"""Integration 4 (lander): the board's act-dissolve evidence is built from the composer's own xfade option, so a
change to how the composer writes its dissolve can never drop the row-12 dissolve evidence silently (panel-6 render
at ec5a7c26 refused by the gate: the board looked for a literal `xfade=transition=<word>` that SYS-E-c4-1 replaced)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"


def _bm():
    sys.path.insert(0, str(BOARD))
    import board_motion
    return board_motion


def test_the_board_dissolve_spec_is_the_composers_own_xfade_option():
    from src import video_composer
    spec = _bm().act_dissolve_spec()
    assert spec["seconds"] == video_composer.act_dissolve_s()
    assert spec["xfade"] == video_composer.act_dissolve_transition()
    assert spec["transition"] and spec["xfade"].startswith("transition=" + spec["transition"])


def test_a_linear_ease_gives_the_plain_fade(monkeypatch):
    from src import video_composer
    monkeypatch.setattr(video_composer, "act_dissolve_transition", lambda: "transition=fade")
    spec = _bm().act_dissolve_spec()
    assert spec["transition"] == "fade" and spec["xfade"] == "transition=fade"
