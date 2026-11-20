"""Freeze lane B, cycle 3: invariants on synthetic copy (made-up words, no topic), flat grounds.

- S1 truth: the figure hero is the copy's statement of the figure whole: its hedge and both ends of a span are drawn,
  or the figure is declined with a recorded reason that names the rule (YouTube and the Reel cover).
- A figure from outside the headline never leaves one headline line drawn without the other.
- The YouTube figure-to-title gap is measured to the title's top ink (an accent over its caps).
- One YouTube masthead size, whatever the hook.
- The cover and YouTube lockups never stack a one-word row under the balance minimum while another form sets.
- A refused headline names the rule that refused it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import cover_design, figures, masthead
from src import youtube_thumbnail as yt
from src.refusal import LayoutError

FLAT = (96, 92, 88)


def _yt(copy, figure=None):
    meta = {}
    im, plates = yt.render(Image.new("RGB", (1920, 1080), FLAT), copy, meta=meta, figure=figure)
    return im, plates, meta


def _figure_text(plates):
    return " ".join(p.text for n, p in plates if n in ("figure_qualifier", "figure", "figure_unit") and not p.empty)


SPAN_HEDGE = [
    {"eyebrow": "OLD QUARRY", "line1": "GRANITE GIANT", "line2": "NEVER RAISED", "tagline": "LEFT IN STONE",
     "badge": "ROUGHLY 137–138 FT"},
    {"eyebrow": "X", "line1": "THE 2,676–2,954 M", "line2": "RIDGE", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "VAULT", "line2": "", "tagline": "ESTIMATED 2,223 M"},
    {"eyebrow": "X", "line1": "BRONZE", "line2": "", "tagline": "ESTIMATED 3,465 KG"},
    {"eyebrow": "DEEP DUST", "line1": "ETCHED LINES", "line2": "UNCHANGED", "tagline": "WHERE",
     "badge": "EST. 4–6 IN (10–15 CM)"},
]


@pytest.mark.parametrize("copy", SPAN_HEDGE, ids=range(len(SPAN_HEDGE)))
def test_the_youtube_figure_is_a_signed_statement_whole(copy):
    try:
        _, plates, meta = _yt(copy)
    except LayoutError as e:
        assert "refused" in str(e)          # a recorded refusal names its rule
        return
    drawn = _figure_text(plates)
    signed = cover_design.signed_copy_texts(copy)
    if drawn:
        assert figures.truth_faults(drawn, signed) == [], drawn
        for f in figures.parse(drawn):
            twins = [s for s in figures.signed_figures(signed) if set(f.values) & set(s.values)]
            assert any(s.values == f.values for s in twins), drawn         # never one end of a span
    else:
        assert meta.get("figure_declined") or meta.get("figure_withheld") or not any(
            figures.parse(t) for t in signed)


def test_a_hedge_the_figure_line_cannot_draw_declines_the_figure():
    copy = {"eyebrow": "X", "line1": "LOST ROOM", "line2": "", "tagline": "WHERE", "badge": "EST. >13,000 LBS"}
    _, plates, meta = _yt(copy)
    assert "13,000" not in _figure_text(plates)
    assert "truth" in meta["figure_declined"] and "EST. >" in meta["figure_declined"]


def test_a_badge_figure_never_leaves_one_headline_line_alone():
    copy = {"eyebrow": "OLD QUARRY", "line1": "GRANITE GIANT", "line2": "NEVER RAISED", "tagline": "LEFT IN STONE",
            "badge": "ROUGHLY 137–138 FT"}
    _, plates, meta = _yt(copy)
    titles = [p.text for n, p in plates if n == "title" and not p.empty]
    for line in ("GRANITE GIANT", "NEVER RAISED"):
        assert line not in titles                                   # the pair whole or neither
    assert meta.get("headline_pair_kept")


def _mask(plate):
    im = Image.new("L", (yt.W, yt.H), 0)
    ImageDraw.Draw(im).text(plate.xy, plate.text, font=plate.font, fill=255, anchor=plate.anchor)
    return np.asarray(im) > 40


def test_the_title_under_a_figure_clears_its_accented_caps():
    from src.typography import cap_height
    copy = {"eyebrow": "X", "line1": "THE 30-MILE DOMÉ", "line2": "", "tagline": "WHERE"}
    _, plates, _ = _yt(copy)
    up = [p for n, p in plates if n in ("figure", "figure_unit", "figure_dim") and not p.empty]
    low = [p for n, p in plates if n == "title" and not p.empty]
    assert up and low
    u = np.any([_mask(p) for p in up], axis=0)
    lo = np.any([_mask(p) for p in low], axis=0)
    gap = int(np.nonzero(lo.any(1))[0].min()) - int(np.nonzero(u.any(1))[0].max()) - 1
    assert gap >= round(yt.TITLE_LINE_GAP * cap_height(low[0].font)) - 1


@pytest.mark.parametrize("copy", [
    {"eyebrow": "X", "line1": "EYE", "line2": "", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "LOST GEARS", "line2": "OLD TIDE", "tagline": "WHERE"},
    {"eyebrow": "X", "line1": "830 SQUARE MILES", "line2": "FLATTENED FOREST", "tagline": "WHERE"},
], ids=["short", "two lines", "figure"])
def test_one_youtube_masthead_size(copy):
    _, _, meta = _yt(copy)
    rec = meta["masthead"]
    assert rec["shown"]
    assert abs(rec["size_px"] - yt.mast_px()) < 1e-6
    assert any(abs(yt.mast_px() - s) < 1e-6 for s in masthead.fit_ladder(yt.BRAND_PX))   # a declared step


def test_the_cover_lockup_never_stacks_an_unbalanced_lone_row_when_one_heading_sets():
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    out, rec = cover_design._lockup(m, {"line1": "CARVED FROM", "line2": "SOLID SALT"}, 912, 108)
    assert cover_design.lone_balance(m, out) is None or rec.get("balance_below_min")
    rows = [" ".join(t for t, _ in lr) for k in ("line1", "line2") if k in out for lr in out[k][1]]
    assert " ".join(rows) == "CARVED FROM SOLID SALT"                                    # the copy's words, whole
    assert all(r.split()[-1] != "FROM" for r in rows[:-1])


def test_a_refusal_names_the_rule():
    width_of = lambda t: 100.0 * len(t)                       # noqa: E731 (a deterministic measure)
    why = yt.refusal_rule("CARVED FROM SOLID SALT", width_of, 1150, 160)
    assert "line-end rule" in why and "FROM" in why
    why = yt.refusal_rule("UNBREAKABLEWORDS", width_of, 500, 160)
    assert why.startswith("the measure")


# ---------------------------------------------------------------------------------------- QF-101: cover encoding
def _sampling(path):
    from PIL import Image, JpegImagePlugin
    with Image.open(path) as im:
        return JpegImagePlugin.get_sampling(im), im.mode


@pytest.mark.parametrize("surface", ["youtube", "reel", "grid"])
def test_a_cover_jpeg_is_444_ycbcr_at_the_highest_quality_within_its_limit(tmp_path, surface, monkeypatch):
    from src import visual_engine as ve
    rng = np.random.default_rng(7)
    im = Image.fromarray(rng.integers(0, 256, (1080, 1920, 3), dtype=np.uint8))   # incompressible: steps down
    rec = ve.save_cover_jpeg(im, tmp_path / "c.jpg", surface)
    assert _sampling(tmp_path / "c.jpg") == (0, "RGB")                 # 4:4:4, YCbCr (decoded to RGB), no keep_rgb
    assert (tmp_path / "c.jpg").stat().st_size == rec["bytes"] <= ve.COVER_LIMITS[surface]
    higher = [q for q in ve.COVER_QUALITIES if q > rec["quality"]]
    if higher:   # the highest step that fits: one step up would not
        import io
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=min(higher), subsampling=0)
        assert buf.tell() > ve.COVER_LIMITS[surface]
    monkeypatch.setitem(ve.COVER_LIMITS, surface, 1000)
    with pytest.raises(ValueError):                                     # never a subsampled or oversized fallback
        ve.save_cover_jpeg(im, tmp_path / "d.jpg", surface)


def test_every_delivered_cover_is_444_within_its_limit(tmp_path):
    from src import visual_engine as ve
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 1200), (120, 96, 70)).save(hero)
    payload = {"thumbnail": {"eyebrow": "OLD QUARRY", "line1": "LOST GEARS", "line2": "OLD TIDE", "tagline": "WHERE"},
               "title": "Lost gears"}
    cover, grid = VisualEngine().generate_thumbnails(hero, payload, tmp_path / "out", style="minimal", variants=False)
    yt_path = VisualEngine().generate_youtube_thumbnail(hero, dict(payload), tmp_path / "out" / "yt.jpg")
    for path, surface in ((cover, "reel"), (grid, "grid"), (yt_path, "youtube")):
        assert _sampling(path)[0] == 0, path
        assert Path(path).stat().st_size <= ve.COVER_LIMITS[surface]
