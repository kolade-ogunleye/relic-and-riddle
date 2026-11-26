"""Design-freeze lane B, cycle 4: Reel cover / grid tile / YouTube thumbnail invariants (panel 5 carry-in)."""

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand
from src.cover_design import render_cover
from src.still_layout import Layout

COPY = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE DOME", "line2": "THAT WHISPERS", "tagline": "BUILT WITHOUT RINGS"}


def _ground(kind):
    """Stress grounds: bright (near-white), dark, busy (noise)."""
    if kind == "busy":
        return Image.fromarray(np.random.default_rng(5).integers(20, 235, (1000, 1500, 3), dtype=np.uint8))
    im = Image.new("RGB", (1500, 1000), {"bright": (240, 238, 232), "dark": (40, 34, 30)}[kind])
    ImageDraw.Draw(im).ellipse((500, 250, 1000, 750), fill=(120, 100, 80))
    return im


@pytest.mark.parametrize("kind", ["bright", "dark", "busy"])
@pytest.mark.parametrize("style", ["badge", "lower_third", "minimal"])
def test_a_framed_window_stands_on_one_ink_field_no_gutter_no_band(kind, style) -> None:
    """Panel 5 (CD r1, PL r1/r2): the photo bands above and below the INK plate faded to black, then stepped UP to the
    plate at y 420 and 1500: a dark gutter line, and a lid on bright heroes. Lane B cycle 4 ended the bands in the
    plate's value; freeze cycle 5 (PHOTO-07: one photograph three times in a tile) drew the bands out: a contained
    window stands on ONE INK field, and a photograph that fills the cover has no window at all."""
    photo = _ground(kind)
    lay = Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    im = render_cover(photo.resize((1080, 1920)).convert("RGBA"), COPY, style, lay=lay, source=photo.convert("RGBA"))
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    plate = np.array(brand.INK, dtype=np.float32)
    comp = lay.meta["composition"]
    if comp["mode"] == "contain":
        x0, y0, x1, y1 = comp["image_box"]
        # INK from the canvas top to the window, read beside the type's column (minimal sets its title above it)
        assert np.abs(rgb[:y0 - 1, :40] - plate).max() <= 1.5 and np.abs(rgb[:y0 - 1, 1040:] - plate).max() <= 1.5
        assert np.abs(rgb[:400] - plate).max() <= 1.5              # and above the profile square: one field
        assert np.abs(rgb[1900:] - plate).max() <= 1.5
    else:
        assert style == "badge" and comp["mode"] in ("full_bleed", "photo_over_ink")
        assert comp["image_box"][:3] == [0, 0, 1080] and comp["source_coverage"] == 1.0


@pytest.mark.parametrize("copy", [{"title": "THE EYE OF THE SAHARA"}, {"line1": "A CITY", "line2": "UNDER A FIELD"}])
@pytest.mark.parametrize("kind", ["bright", "dark"])
def test_youtube_masthead_size_is_the_ruled_step_and_says_so(copy, kind) -> None:
    """Panel 5 (BC MG-02, PL r1 gain 3): the YouTube masthead draws at 48 px with "size_asked 64, room 488.5" on its
    record, which reads as a fit to each hook. It is the token masthead.youtube_px, a declared step of the masthead's
    fit ladder (lane B cycle 3 ruling B-S2-1, SYS-B-5): the record names that rule, and the drawn size is it."""
    from src import masthead, youtube_thumbnail
    meta: dict = {}
    youtube_thumbnail.render(_ground(kind).resize((1920, 1080)), dict(copy), meta=meta)
    rec = meta["masthead"]
    rule = rec["size_rule"]
    ladder = masthead.fit_ladder(youtube_thumbnail.BRAND_PX)
    assert rule["token"] == "masthead.youtube_px" and rule["ladder"] == ladder
    assert rule["step"] is not None and ladder[rule["step"]] == rule["px"] >= ladder[-1]
    assert rule["px"] == youtube_thumbnail.measured_mast_px()   # the token is the measurement's step
    if rec.get("shown"):
        assert rec["size_px"] == rule["px"]                      # drawn at the ruled step, never another


@pytest.mark.parametrize("size", [(3024, 4032), (3000, 3330), (2000, 2000), (1200, 3000)])
def test_a_tall_photograph_shows_its_form_on_the_youtube_frame(size) -> None:
    """Panel 5 (CD r1 gain 5, CD r2 near-white crop): filled across the whole width, a tall photograph kept a thin
    slice (the Aswan obelisk's top surface only; the stand-in's flat vitrine wall, fragments cut). It now covers only
    the photo side at full height and shows at least SHOW_MIN of its height where covering that side allows; the field
    left of it joins without an edge."""
    from src import youtube_thumbnail as yt
    sw, sh = size
    rng = np.random.default_rng(2)
    src = Image.fromarray(rng.integers(60, 200, (sh // 10, sw // 10, 3), dtype=np.uint8)).resize(size)
    meta: dict = {}
    out = np.asarray(yt._photo(src, None, meta).convert("L"), dtype=np.float32)
    side = meta["photo_side"]
    cover_side = max((yt.W - yt.PHOTO_SIDE_LEFT) / sw, yt.H / sh)
    assert side["left"] <= yt.PHOTO_SIDE_LEFT                                   # the photo side is covered
    assert side["shown_height"] >= min(yt.SHOW_MIN, yt.H / (sh * cover_side)) - 0.005
    assert side["shown_height"] > side["full_width_shown"]                      # never less than before
    cols = out.mean(axis=0)
    x = side["left"]
    assert np.abs(np.diff(cols[max(0, x - 40):x + yt.SIDE_FEATHER + 40])).max() < 4   # no edge where it starts


@pytest.mark.parametrize("size", [(6000, 4000), (3840, 2392), (7500, 3841)])
def test_a_wide_photograph_keeps_the_full_width_fill(size) -> None:
    from src import youtube_thumbnail as yt
    sw, sh = size
    assert yt.photo_side_scale(sw, sh) == max(yt.W / sw, yt.H / sh)
    meta: dict = {}
    yt._photo(Image.new("RGB", size, (90, 80, 70)), None, meta)
    assert "photo_side" not in meta


# ----------------------------------------------------------------- a hedged hyphenated adjective (ruling 2026-11-25, 2)
NEARLY_SYN = {"narration_script": "Each statue weighs nearly 720 tons. The pit is about 45 metres deep."}
# the Memnon run's own signed sentence (run_singing_colossi_of_memnon_1792952878 narration): "an estimated" -> ABOUT
MEMNON = {"narration_script": "Standing 60 feet (18 m) tall, each colossus weighs an estimated 720 tons."}


@pytest.mark.parametrize("payload, line, drawn, why", [
    (NEARLY_SYN, "720-TON TITAN", "NEARLY 720-TON TITAN", None),          # the ruling's form: drawn
    (NEARLY_SYN, "A SINGING 720-TON TITAN", None, "does not open its line"),  # only as the phrase's first word
    (NEARLY_SYN, "THE 720-TON TITAN", None, "does not open its line"),     # the article rule still holds
    (NEARLY_SYN, "THE TITAN, 720-TON", None, "modifies no word"),         # the adjective must modify a word after it
    (NEARLY_SYN, "THE 45-METRE PIT", None, "ABOUT does not read before an adjective"),
    (NEARLY_SYN, "45-METRE PIT", None, "ABOUT does not read before an adjective"),
    (MEMNON, "720-TON TITAN", None, "ABOUT does not read before an adjective"),   # the real case stays withheld
])
def test_a_hyphenated_figure_draws_only_with_an_adverb_hedge(payload, line, drawn, why):
    from src.visual_engine import gate_drawn, evidence_faults
    hedged: list = []
    gated, rows = gate_drawn({"t": line}, payload, "x", hedged=hedged)
    if drawn is not None:
        assert gated["t"] == drawn and not rows and hedged and hedged[0]["hedge"] == "NEARLY"
        assert evidence_faults(drawn, payload) == []                     # re-passes the gate as drawn
    else:
        assert not hedged and rows and why in rows[0]["hedge_declined"]


def test_an_unhedged_hyphenated_adjective_stays_withheld():
    from src.visual_engine import gate_drawn
    gated, rows = gate_drawn({"t": "720-TON TITAN"}, NEARLY_SYN, "x")   # the plain gate (no hedge offered)
    assert rows and "720" not in gated["t"]
