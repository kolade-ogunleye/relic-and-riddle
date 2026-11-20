"""Freeze lane B, cycle 1: the YouTube thumbnail's copy-stage fit check (src/cover_fit.py) and its render are one
decision (LANE-AB carry-in r8i, board fix 72ac97a8 finding: the check rendered without the figure check and the badge,
at the full column only, and passed copy the real thumbnail refuses).

Invariants, not pinned measurements:
- one input builder: the render (generate_youtube_thumbnail) and the check read visual_engine.youtube_render_inputs;
- the check never passes what the render refuses: copy the check passes without a hero sets at the standard type
  column (lane B cycle 3 ruling; a narrower column is met at render by the headline chain), the figure's qualifier,
  hedge and badge included; with the hero, the render on that hero sets it;
- the authored YouTube form is gone: no drawn row but the last ends on a never-end word (CARVED FROM /).
"""
from __future__ import annotations

import pytest
from PIL import Image

from src import cover_fit, heading, visual_engine
from src import youtube_thumbnail as yt
from src.refusal import LayoutError

FULL = yt.TEXT_RIGHT - yt.LEFT

# synthetic copies of the classes that decide by width: a two-field headline near the column, a figure with a
# qualifier whose kicker may or may not fit (the branch flips by width), a hedge line, a one-field headline
COPIES = [
    {"eyebrow": "THE EVIDENCE", "line1": "LOST CITY", "line2": "UNDER SAND", "tagline": "WHERE"},
    {"eyebrow": "THE EVIDENCE", "line1": "STONE RINGS", "line2": "OF THE NORTH", "tagline": "HOW"},
    {"eyebrow": "ANCIENT QUARRY", "line1": "ABOUT 144 FT", "line2": "OF SOLID STONE", "tagline": "NEVER RAISED"},
    {"eyebrow": "ANCIENT QUARRY", "line1": "THE 42-METRE", "line2": "GIANT", "tagline": "NEVER RAISED",
     "badge": "APPROXIMATELY 42 M"},
    {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA EYE", "line2": "", "tagline": "CARVED BY NATURE"},
    {"eyebrow": "SUBTERRANEAN MARVEL", "line1": "CARVED FROM", "line2": "SOLID SALT", "tagline": "DOWN"},
]


def _flat():
    return Image.new("RGB", (1920, 1080), (96, 92, 88))


def _render_at(copy, width, payload=None):
    inputs = visual_engine.youtube_render_inputs(dict(payload or {}), None, copy=copy)
    yt.render(meta={}, column=float(width), **dict(inputs, source=_flat()))


@pytest.mark.parametrize("copy", COPIES, ids=[c["line1"] for c in COPIES])
def test_copy_the_check_passes_sets_at_the_standard_column(copy):
    """Lane B cycle 3 (lander ruling): the copy stage holds the design's standard text column; a hero that narrows it
    is met at render by the headline chain (tests/test_lane_b_cycle3.py)."""
    cover_fit.clear()
    problems = cover_fit.fit_problems(copy, reel=False)
    if problems:
        assert _refuses(copy, FULL), problems
        return
    _render_at(copy, FULL)   # raises LayoutError if the check passed what the render refuses


def _refuses(copy, w):
    try:
        _render_at(copy, w)
    except LayoutError:
        return True
    return False


def _refused_at_reported(copy, problems):
    import re
    m = re.search(r"(\d+) px", problems[0])
    return bool(m) and _refuses(copy, int(m.group(1)))


def test_the_check_reads_the_renders_inputs_the_badge_and_the_figure_check(monkeypatch):
    """The figure check and the badge reach the check as they reach the render: a figure the sheet hedges with no
    qualifier the copy can show is declined in both (the class the old check missed: it rendered with figure=None)."""
    seen = []
    real = visual_engine.youtube_render_inputs

    def spy(payload, hero=None, copy=None):
        got = real(payload, hero, copy=copy)
        seen.append(got)
        return got
    monkeypatch.setattr(visual_engine, "youtube_render_inputs", spy)
    fig = {"number": "42", "unit": "M", "hedged": True, "reason": "the sheet hedges it"}
    monkeypatch.setattr(visual_engine, "cover_figure", lambda copy, payload: dict(fig))
    copy = {"eyebrow": "ANCIENT QUARRY", "line1": "THE 42 M", "line2": "GIANT", "tagline": "NEVER RAISED"}
    payload = {"thumbnail": copy, "stat_cards": [{"value": "42 M", "label": "LONG"}]}
    cover_fit.clear()
    cover_fit.fit_problems(copy, reel=False, payload=payload)
    assert seen and all(s["figure"] == fig for s in seen)
    assert all(s["copy"].get("badge") == "42 M • LONG" for s in seen)


def test_the_render_and_the_check_build_their_inputs_in_one_place(monkeypatch, tmp_path):
    calls = []
    real = visual_engine.youtube_render_inputs

    def spy(payload, hero=None, copy=None):
        calls.append(hero)
        return real(payload, hero, copy=copy)
    monkeypatch.setattr(visual_engine, "youtube_render_inputs", spy)
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (90, 80, 70)).save(hero)
    payload = {"thumbnail": COPIES[4], "title": "T", "narration_script": "x."}
    visual_engine.VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    assert calls == [hero]
    cover_fit.clear()
    assert cover_fit.fit_problems(visual_engine.youtube_cover_copy(payload), reel=False, payload=payload,
                                  hero=hero) == []
    assert hero in calls[1:]


def test_a_copy_only_a_narrowed_column_refuses_passes_the_copy_stage():
    """Lane B cycle 3 (lander ruling): a headline that sets in the standard column but not in a column a hero could
    narrow it to passes the copy stage (the thumbnail controls its column); at render, the chain meets the narrower
    column."""
    copy = next((c for c in COPIES if not _refuses(c, FULL) and _refuses(c, yt.COLUMN_MIN)), None)
    assert copy is not None   # the set holds a member of the class (else this test proves nothing)
    cover_fit.clear()
    assert cover_fit.fit_problems(copy, reel=False) == []


def test_no_youtube_row_but_the_last_ends_on_a_never_end_word():
    """r8i AD final (lane B): the authored form (CARVED FROM / SOLID SALT) bypassed the never-end check through the
    field boundary; every drawn form now obeys it, else the headline is refused (copy edit)."""
    from PIL import ImageDraw
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for l1, l2 in (("CARVED FROM", "SOLID SALT"), ("THE EYE OF", "THE SAHARA"), ("BURIED UNDER", "THE ICE"),
                   ("LOST IN", "THE DESERT"), ("ROMAN CONCRETE", "HEALS ITSELF")):
        for width in (FULL, 1400):
            try:
                _, _, _, rec = yt._lockup(m, l1, l2, width)
            except LayoutError:
                continue
            assert rec["form"] in ("ruled", "one heading")
            assert all(heading.end_ok(t.split()[-1]) for t in rec["drawn_rows"][:-1]), rec["drawn_rows"]
