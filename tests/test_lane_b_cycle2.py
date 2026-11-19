"""Freeze lane B, cycle 2 (panel 2 leads): invariants on synthetic copy, flat grounds.

- PL: the YouTube masthead never competes with the hook: its drawn group is at most MARK_RATIO_MAX of the hook's first
  row (a step of the masthead's own fit ladder, never under its floor, never refused for this).
- TY: the title under a figure clears the figure row's lowest ink (a Q tail, a descender) by one heading line gap.
- BC/TY: every drawn Reel cover / grid title row but the last obeys the heading engine's never-end rule.
- The deep dive's thumbnail is required at release and a title the thumbnail refuses goes to the draft retry.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import heading, masthead
from src import youtube_thumbnail as yt
from src.refusal import LayoutError

FLAT = (96, 92, 88)
COPIES = [
    {"eyebrow": "SIBERIA 1908", "line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST", "tagline": "WHAT HIT"},
    {"eyebrow": "HISTORICAL HEIST", "line1": "THE 6-TON", "line2": "ROOM", "tagline": "WHERE"},
    {"eyebrow": "THE WEAPON", "line1": "THE SECRET WEAPON", "line2": "", "tagline": "WHERE"},
    {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA EYE", "line2": "", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "EYE", "line2": "", "tagline": "WHERE"},          # a short hook: the mark at its floor
    {"eyebrow": "X", "line1": "ABOUT 42 M", "line2": "QUARRY GIANT", "tagline": "WHERE"},
]


def _render(copy):
    meta = {}
    im, plates = yt.render(Image.new("RGB", (1920, 1080), FLAT), copy, meta=meta)
    return im, plates, meta


@pytest.mark.parametrize("copy", COPIES, ids=[c["line1"] for c in COPIES])
def test_the_masthead_never_competes_with_the_hook(copy):
    _, plates, meta = _render(copy)
    rec = meta["masthead"]
    assert rec["shown"]                                      # stepped down, never refused for this
    ladder = masthead.fit_ladder(yt.BRAND_PX)
    size = rec["size_px"]
    assert any(abs(size - s) < 1e-6 for s in ladder)         # a declared step of its own fit ladder
    row = yt.hook_row(plates)
    width = masthead.group_width(size, yt.LEFT)
    assert width <= yt.MARK_RATIO_MAX * row + 1.0 or abs(size - ladder[-1]) < 1e-6, (width, row, size)
    # the largest step that holds it: one step up would not
    bigger = [s for s in ladder if s > size + 1e-6]
    if bigger:
        assert masthead.group_width(min(bigger), yt.LEFT) > yt.MARK_RATIO_MAX * row


def _mask(plate):
    from src.typography import _draw_tracked
    im = Image.new("L", (yt.W, yt.H), 0)
    _draw_tracked(ImageDraw.Draw(im), plate.xy, plate.text, plate.font, 255, plate.anchor, plate.track or 0, 0, None,
                  plate.features)
    return np.asarray(im) > 40


def test_the_title_under_a_figure_clears_the_figure_rows_lowest_ink():
    """Panel 2 TY (Tunguska): the Q tail of SQ MI reached the cap top of FLATTENED (the gap was measured from the
    baseline). Measured on ink, in every shared column: at least one heading line gap of the title's cap."""
    from src.typography import cap_height
    for copy in (COPIES[0], COPIES[5],
                 {"eyebrow": "X", "line1": "THE 25 MI RING", "line2": "SEEN FROM ORBIT", "tagline": "WHERE"}):
        _, plates, _ = _render(copy)
        d = [(n, p) for n, p in plates if not p.empty]
        up = [p for n, p in d if n in ("figure", "figure_unit", "figure_dim")]
        low = [p for n, p in d if n == "title"]
        if not (up and low):
            continue
        u = np.any([_mask(p) for p in up], axis=0)
        lo = np.any([_mask(p) for p in low], axis=0)
        cols = np.nonzero(u.any(0) & lo.any(0))[0]
        gap = min(np.nonzero(lo[:, x])[0].min() - np.nonzero(u[:, x])[0].max() - 1 for x in cols)
        need = round(yt.TITLE_LINE_GAP * cap_height(low[0].font))
        assert gap >= need - 1, (copy["line1"], gap, need)


def test_every_drawn_reel_cover_title_row_obeys_the_never_end_rule(tmp_path):
    """Panel 2 BC/TY: the Reel cover and the grid tile set their title through the heading engine (cover_design._lockup:
    lockup_fields, set_field / set_heading, _stack_valid); its off-ladder last resort is refused by the grid proof,
    never delivered. Checked on the delivered layout record of every style that draws."""
    import json

    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1600, 2400), (90, 80, 70)).save(hero)
    for i, (l1, l2) in enumerate((("CARVED FROM", "SOLID SALT"), ("THE EYE OF", "THE SAHARA"),
                                  ("LOST IN", "THE DESERT SAND"), ("THE 1,200-TON", "BEDROCK MONSTER"))):
        payload = {"thumbnail": {"eyebrow": "THE EVIDENCE", "line1": l1, "line2": l2, "tagline": "WHERE"},
                   "title": "T", "narration_script": "x."}
        for style in ("badge", "minimal", "depth_sandwich"):
            out = tmp_path / f"c{i}{style}"
            try:
                VisualEngine().generate_thumbnails(hero, payload, out, variants=False, style=style)
            except LayoutError:
                continue   # refused (recorded), never delivered
            rec = json.loads((out / "still_layout.json").read_text(encoding="utf-8"))
            for name, lay in rec.items():
                rows = [e["text"] for e in lay["elements"] if e.get("name") in ("title_1", "title_2") and e.get("text")]
                assert all(heading.end_ok(r.split()[-1]) for r in rows[:-1]), (name, rows)


def test_a_deep_dive_title_the_thumbnail_refuses_goes_back_and_the_release_requires_the_thumbnail(tmp_path):
    """The deep dive is the only delivery that publishes the 16:9 thumbnail (deep_dive_16x9); its release gate
    requires it, and a title the thumbnail cannot set is a cover-fit problem for the draft retry (main)."""
    from main import documentary_cover
    from src import cover_fit, delivery_profiles
    from src.visual_engine import youtube_cover_copy
    assert delivery_profiles.get_delivery_profile("deep_dive").requires_youtube_thumbnail is True
    assert not any(p.requires_youtube_thumbnail for k, p in delivery_profiles.PROFILES.items()
                   if not k.startswith("deep_dive"))
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (90, 80, 70)).save(hero)
    title = "The 1,200-Ton Bedrock Monster"
    ctx = {"title": title, "narration_script": "x."}
    ctx["thumbnail"] = documentary_cover(title, payload=ctx, hero=hero)
    assert cover_fit.fit_problems(youtube_cover_copy(ctx), payload=ctx, hero=hero, reel=False)
