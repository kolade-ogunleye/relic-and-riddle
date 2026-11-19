"""Design-freeze cycle 0 round 8, lane RULES (operator rulings 1 and 2 of 2026-11-17; official panel 1 consensus 3, 5
and 7). Every test is built from a panel measurement on synthetic inputs (CI has no output/), and every rule has a
guard that fails when a renderer bypasses it."""
from __future__ import annotations

import re

from src import brand_system
from src import video_composer as vc

# ---------------------------------------------------------------- captions (consensus 5)
# The Richat long run's first sentence (run 1792740961_long, words.json): the 16:9 burst ended "is situated near".
RICHAT = ("The Richat Structure is situated near Ouadane on the Adrar Plateau within the Adrar Region of Mauritania. "
          "Visible from space as a massive desert landmark, the concentric formation has served as a visual reference "
          "point for space shuttle crews.")


def _timed(text: str) -> list[dict]:
    words, at = [], 0.25
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


def _mid_sentence_ends(chunks: list[list[str]]) -> list[str]:
    return [c[-1] for c in chunks[:-1] if not re.search(r"[.!?,;:]$", c[-1])]


def test_the_caption_tails_hold_the_heading_engines_never_end_set():
    """One never-end set: every caption path's tail set (DANGLING_ENDS: the freeze-pass builder, the round-4 base, the
    16:9 bursts, the merges, the sidecar cues) holds brand_system.LINE_END_NEVER."""
    assert brand_system.LINE_END_NEVER <= vc.DANGLING_ENDS <= vc.DANGLING_TAILS
    assert all(vc._hard_dangles(w) for w in brand_system.LINE_END_NEVER)


def test_no_caption_path_ends_on_a_never_end_word_on_the_richat_narration():
    words = _timed(RICHAT)
    raw = [w["word"] for w in words]
    paths = {
        "9:16": [[w["word"] for w in p] for p in vc.caption_phrases(words)],
        "16:9": [[w["word"] for w in p] for p in vc.caption_phrases(words, None, True)],
        "cues": [t.split() for _, _, t in vc.partition_sidecar_cues(words)],
    }
    # the bursts' round-4 base never cuts on one (its cues are HEAD's own and may end on THE: the cue builder moves
    # those cuts, test_head_regression_gate's "article" kind)
    for name, cuts in (("base 9:16", vc.round4_burst_cuts(words)), ("base 16:9", vc.round4_burst_cuts(words, None, True))):
        assert not [raw[k] for k in cuts if vc._bare(raw[k]) in brand_system.LINE_END_NEVER], name
    for name, chunks in paths.items():
        assert [w for c in chunks for w in c] == raw, name           # every word, in order
        ends = _mid_sentence_ends(chunks)
        assert not [e for e in ends if vc._bare(e) in brand_system.LINE_END_NEVER], (name, ends)


# ---------------------------------------------------------------- overlay labels on bright grounds (ruling 2)
import ast
import inspect
import textwrap

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, end_card, graphics, multi_format, then_now, typography
from src.brand_system import tok


def _stripes(size=(900, 300), period=6):
    """A busy ground neither bone (halo <= 0.16) nor INK type carries: black and white rows."""
    arr = np.zeros((size[1], size[0], 3), np.uint8)
    for k in range(period // 2):
        arr[k::period] = 255
    return Image.fromarray(arr)


def _label(ground, text="CATHERINE PALACE", px=34, colours=None, **kw):
    font = graphics.place_font(px)
    return typography.plan_label(ground, [((100, 160), text, font, "ls", 0)], colours or (brand.BONE,),
                                 target=4.8, **kw)


@pytest.mark.parametrize("ground, variant", [
    (Image.new("RGB", (900, 300), (24, 26, 30)), "bone"),       # a dark ground: bone, no halo needed
    (Image.new("RGB", (900, 300), (236, 236, 232)), "ink"),     # a bright ground: dark type first where it fits
    (None, "plate"),                                            # a busy ground: the held INK plate
])
def test_the_overlay_label_rule_runs_its_three_variants_in_order(ground, variant):
    ground = ground or _stripes()
    plates, rec = _label(ground)
    assert rec["shown"] and rec["variant"] == variant
    assert all(p.halo <= tok("halo.label_max") + 1e-9 for p in plates)     # the halo exception is retired
    drawn = plates[0].draw(ground)
    assert plates[0].measure(drawn) >= 4.8 - 1e-6
    if variant == "plate":
        box = rec["plate"]["box"]
        ink = plates[0].box
        pad = tok("plate.label_pad") * 34
        assert box[0] <= ink[0] - pad + 1 and box[2] >= ink[2] + pad - 1      # the plate holds the ink, padded
        assert rec["plate"]["alpha"] == tok("plate.held")
    else:
        assert getattr(plates[0], "backing", None) is None


def test_the_held_plate_fades_and_rises_with_its_label():
    ground = _stripes()
    plates, rec = _label(ground)
    assert rec["variant"] == "plate"
    p = plates[0]
    x0, y0, _x1, y1 = rec["plate"]["box"]
    probe = (x0 + 3, y0 + 4, x0 + 9, y1 - 4)     # plate, clear of the glyphs (its left padding)

    def dark(im):
        return float(np.asarray(im.convert("L").crop(probe), np.float32).mean())
    bare, half, full = dark(ground), dark(p.draw(ground, 0.5)), dark(p.draw(ground, 1.0))
    assert full < half < bare
    assert abs((bare - half) - 0.5 * (bare - full)) <= 0.08 * (bare - full) + 1   # the plate's alpha is the label's
    moved = p.draw(ground, 1.0, 40)
    assert dark(moved) > full + 5                                       # it rises with the label (dy)


def test_a_multi_row_label_lays_its_plate_once():
    ground = _stripes((900, 400))
    font = graphics.place_font(34)
    rows = [((100, 160), "CATHERINE", font, "ls", 0), ((100, 205), "PALACE", font, "ls", 0)]
    plates, rec = typography.plan_label(ground, rows, (brand.BONE,), target=4.8)
    assert rec["variant"] == "plate" and len(plates) == 2
    out = ground
    for q in plates:
        out = q.draw(out)
    a, b = plates[0].backing.parent.box, plates[1].backing.parent.box
    assert a[3] == b[1]                                                  # the rows' plates meet, no gap, no overlap
    assert a[0] < plates[0].box[0] and b[2] > plates[1].box[2]           # each hugs its own row (stepped)
    assert b[0] > a[0] and b[2] < a[2]                                   # PALACE's plate is narrower than CATHERINE's
    x = max(a[0], b[0]) + 4                                              # a column inside both, clear of the glyphs
    col = np.asarray(out.convert("L"), np.float32)[a[1] + 4:b[3] - 4, x]
    assert col.max() - col.min() < 40                                    # one even plate, no doubled band at the seam


def test_the_loop_bridge_follow_never_takes_a_halo_past_the_cap():
    grey = Image.new("RGB", (1080, 1920), (128, 128, 128))
    plate, why = end_card._follow_plate(grey, grey, 0.0, 0.0, 30)
    assert why is None and plate.variant == "plate" and plate.halo == 0.0
    for col in ((255, 255, 255), (200, 215, 230), (0, 0, 0)):
        im = Image.new("RGB", (1080, 1920), col)
        p, _ = end_card._follow_plate(im, im, 0.0, 0.0, 30)
        assert p.variant in ("bone", "ink", "plate") and p.halo <= tok("halo.label_max") + 1e-9


@pytest.mark.parametrize("bright, variant", [(True, "ink"), (False, "plate")])
def test_a_kicker_on_a_bright_ground_takes_ink_type_or_the_plate_with_its_rule(bright, variant):
    ground = Image.new("RGB", (900, 300), (250, 250, 246)) if bright else _stripes()
    font = typography.get_font(brand.TEXT_FONT, 40, weight=tok("kicker.weight"))
    rule = (100, 140, 127, 145)
    plate, rec = multi_format.plan_kicker(ground, (150, 160), "WATCH NEXT", font, brand.GOLD, "ls", 6, rule=rule)
    assert rec["shown"] and rec["variant"] == variant and plate.halo <= tok("halo.label_max") + 1e-9
    if variant == "plate":
        box = rec["plate"]["box"]
        assert box[0] <= rule[0] and box[1] <= rule[1] and box[2] >= plate.box[2]   # the plate holds the rule
        drawn = np.asarray(plate.draw(ground), np.int16)
        assert tuple(drawn[142, 112]) == tuple(plate.fill)                           # the rule drawn on it


# The guard: every overlay-label renderer plans through typography.plan_label and none keeps a halo exception.
OVERLAY_LABEL_RENDERERS = [end_card._follow_plate, end_card.frames, multi_format.plan_kicker, graphics.place_plate_set,
                           then_now.render]
RETIRED = ("LOOP_FOLLOW_HALO", "HALO_EXCEPTION_CAP", "KICKER_HALO", "GLOW_HALO_CAP", "band_mask(", "_marker_plate(")


@pytest.mark.parametrize("fn", OVERLAY_LABEL_RENDERERS, ids=lambda f: f.__qualname__)
def test_every_overlay_label_renderer_goes_through_the_one_rule(fn):
    src = textwrap.dedent(inspect.getsource(fn))
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)]
    names = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in calls}
    # round 8 R1 (rules2): a renderer drawing a SET of labels plans them through plan_label_set (one rule, per set)
    assert "plan_label" in names or "plan_kicker" in names or "plan_label_set" in names
    assert not [r for r in RETIRED if r in src], fn.__qualname__
    for c in calls:     # no plan_text past the cap in an overlay-label renderer
        for kw in c.keywords:
            if kw.arg == "max_halo" and isinstance(kw.value, ast.Constant):
                assert float(kw.value.value) <= 0.16, fn.__qualname__


def test_the_place_label_never_takes_the_retired_halo_exception():
    p = graphics.place_plate(_stripes(), (100, 150), "CATHERINE PALACE", graphics.place_font(34), "ls")
    assert p.variant == "plate" and p.halo == 0.0 and getattr(p, "halo_exception", False) is False


# ---------------------------------------------------------------- the site's own place-label step (ruling 1)
def test_the_story_site_is_one_place_label_step_up_in_the_same_face():
    for size in ((1080, 1920), (1920, 1080)):
        site, place = graphics.site_steps(*size), graphics.place_steps(*size)
        assert site[0] == round(place[0] * tok("place.site_step"))
        assert site[0] >= 1.15 * place[0] and site[-1] == place[-1]
        _lines, font = graphics.marker_label_lines("Catherine Palace", *size)
        assert font.size == site[0]
        assert graphics.place_font(font.size).path == font.path        # the place-label face, never the display face


# ---------------------------------------------------------------- ruling-3 / kicker ports (consensus 7)
def test_then_and_now_uses_the_system_kicker_and_a_gold_figure_year():
    from src.brand_system import FACE, type_px
    for key, canvas in (("portrait", "tall"), ("landscape", "wide")):
        kf, yf = then_now.kicker_font(key), then_now.year_font(key)
        assert kf.size == round(type_px("kicker", canvas))
        assert kf.path == typography.get_font(brand.active().font(FACE["kicker"]), kf.size, weight=800).path
        assert yf.path == graphics.figure_font(yf.size).path
        size = (1080, 1920) if key == "portrait" else (1920, 1080)
        for which in ("then", "now"):
            word = which.upper()
            kxy, _fxy, anchor = then_now.marker_lines(which, 1934, *size)
            rule = then_now.marker_rule(kxy, word, key, anchor)
            words = typography.TextPlate(word, kf, brand.BONE, kxy, anchor, then_now.kicker_track(key), 0,
                                         brand.INK, 0, False, size).box
            _, gap, _ = then_now.kicker_lead(kf)
            assert rule[2] < words[0] and abs((words[0] - rule[2] - 1) - gap) <= 2      # the dash leads its words
            assert words[1] <= (rule[1] + rule[3]) / 2 <= words[3]                       # at mid-cap
    src = inspect.getsource(then_now.render)
    assert 'colour_of("figure.colour")' in src and 'colour_of("kicker.colour")' in src


def test_the_number_cards_prefix_is_the_system_kicker_led_by_its_dash():
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lay = graphics._stat_layout(d, {"value": "Est. over 13,000 lbs", "label": "the room's amber"}, 230, 700)
    pre = next(r for r in lay["rows"] if r["kind"] == "kicker")
    assert pre["text"].startswith("EST")
    x0, y0, x1, y1 = pre["bar"]
    ch = typography.cap_height(pre["font"])
    assert x0 == 0 and x1 < pre["x"] and y1 < pre["y"] and y0 > pre["y"] - ch     # before the words, inside the cap
    assert abs((y0 + y1) / 2 - (pre["y"] - ch / 2)) <= 1.5                         # at mid-cap (never above them)
    assert pre["track"] == pytest.approx(tok("kicker.track") * pre["font"].size)


def test_the_9x16_number_card_shares_the_caption_axis():
    from src.video_composer import reel_caption_centre_x
    assert graphics.tall_axis_x(1080, 1920) == reel_caption_centre_x(1080, 1920) == 480
    assert "tall_axis_x(width, height)" in inspect.getsource(graphics.render_stat_cards)



# ---------------------------------------------------------------- motion: the act dissolve and the caption cps rule
def test_the_act_dissolve_reads_its_token_and_is_a_true_crossfade():
    from src.video_composer import VIDEO_FPS, VideoComposer, act_dissolve_s
    assert act_dissolve_s() == round(tok("motion.act_dissolve") * VIDEO_FPS) / VIDEO_FPS == 0.5
    src = inspect.getsource(VideoComposer.compose_video)
    assert "act_dissolve_s()" in src and "transition=fade:" in src and "transition=dissolve" not in src
    with brand.use(brand.DEFAULT_BRAND.derive("dissolve probe", system={"motion.act_dissolve": 0.8})):
        assert act_dissolve_s() == pytest.approx(0.8)


def test_the_caption_cps_cap_has_one_source_and_holds_a_burst_into_its_pause():
    from src import config
    from src.brand_system import GATES
    assert vc.CAPTION_MAX_CPS == GATES["motion.caption_cps"] == config.CAPTION_MAX_CPS == 17.0
    # 46 characters spoken in 1.6 s (28.8 cps), then a 2 s pause: the burst holds into the pause to 46 / 17 s
    words = [{"word": w, "start": 0.2 + 0.32 * k, "dur": 0.3} for k, w in enumerate(
        ["Visible", "from", "space", "as", "a", "massive", "desert", "landmark,"])]
    nxt = [{"word": "Then", "start": 5.0, "dur": 0.3}, {"word": "more.", "start": 5.35, "dur": 0.3}]
    show, hide, _ = vc.burst_timings([words, nxt])[0]
    chars = len(" ".join(w["word"] for w in words))
    assert chars / (hide - show) <= vc.CAPTION_MAX_CPS + 1e-6
    # back to back with the next burst (no pause) it cannot be held: recorded as the narration's pace, not a defect
    tight = [dict(w, start=w["start"]) for w in words]
    after = [{"word": "Then", "start": tight[-1]["start"] + 0.31, "dur": 0.3}]
    timings = vc.burst_timings([tight, after])
    rec = vc.reading_rates([tight, after], timings)
    assert rec["over_max"] >= 1 and rec["over_max_pace"] == rec["over_max"] and not rec["timing_defects"]


# ---------------------------------------------------------------- hero window (consensus 3)
from src import hero_window


def _museum(label_rows=("THE ANTIKYTHERA MECHANISM", "BRONZE GEARING, 2ND CENTURY BC"), at=0.86, size=(1500, 2000)):
    """A synthetic exhibit: a mottled bronze object up top, a white plinth label with printed lines at `at` of the
    height (the Antikythera stand-in's museum label, panel CD-5 / AD S2-2)."""
    rng = np.random.default_rng(7)
    arr = (rng.normal(90, 25, (size[1] // 20, size[0] // 20, 3)).clip(0, 255)).astype(np.uint8)
    im = Image.fromarray(arr).resize(size, Image.BILINEAR)
    d = ImageDraw.Draw(im)
    font = typography.get_font(brand.TEXT_FONT, 60, weight=700)
    top = round(at * size[1])
    d.rectangle([60, top - 40, size[0] - 60, top + 90 * len(label_rows)], fill=(236, 234, 228))
    for k, row in enumerate(label_rows):
        d.text((110, top + 80 * k), row, font=font, fill=(150, 70, 30))
    return im


def test_photographed_text_at_the_edge_is_kept_out_of_every_window():
    im = _museum()
    lines = hero_window.text_lines(im)
    assert len(lines) >= 2                                        # both label lines found, OCR-free
    bands = hero_window.photo_text(im)
    assert bands and bands[0][0] == "row" and bands[0][1] < 0.86 * 2000 < bands[0][2]
    _x0, y0, _x1, y1 = hero_window.clean(im)[1]
    assert y1 <= bands[0][1] and y0 == 0                          # the clean region (every crop) stops above it


def test_text_across_the_middle_is_the_subject_and_stays():
    rows = tuple(f"LINE {k} OF THE DOCUMENT TEXT" for k in range(6))
    im = _museum(rows, at=0.30, size=(1500, 2000))               # a document: its lines reach the middle
    assert hero_window.text_lines(im) and hero_window.photo_text(im) == []


def test_solid_shapes_in_a_row_are_not_text():
    im = Image.new("RGB", (1600, 1200), (180, 170, 150))
    d = ImageDraw.Draw(im)
    for k in range(10):                                           # a row of windows / posts near the foot
        d.rectangle([100 + 140 * k, 1000, 160 + 140 * k, 1060], fill=(40, 40, 40))
    assert hero_window.photo_text(im) == []


def test_a_deck_shows_a_photograph_again_in_another_window_keeping_its_centre():
    src, out = (3000, 2000), (1440, 1800)            # a landscape photo on a 4:5 slide: horizontal slack
    first = hero_window.deck_window(src, out, 0)
    second = hero_window.deck_window(src, out, 1)
    assert first["centering"] == (0.5, 0.5) and first["overlap"] == 0.0
    assert second["centering"][0] != 0.5 and second["overlap"] < 1.0
    box = hero_window._fit_box(src, out, second["centering"])
    assert box[0] <= src[0] / 2 <= box[2]                          # the centre (the subject) stays in view
    wide = hero_window.deck_window((6000, 2000), out, 1)            # room to spare: under DECK_OVERLAP_MAX
    assert wide["overlap"] <= hero_window.DECK_OVERLAP_MAX + 1e-3


def test_the_masthead_box_is_kept_off_a_foreign_object_where_the_window_allows():
    im = Image.new("RGB", (3000, 2000), (196, 170, 120))           # desert
    d = ImageDraw.Draw(im)
    d.rectangle([700, 0, 1800, 120], fill=(250, 250, 250))         # a wing's white skin with a red trim
    d.rectangle([700, 120, 1800, 160], fill=(200, 20, 30))
    mbox = (80, 54, 700, 160)                                      # the carousel's masthead box (1440 x 1800 px)
    win = hero_window.deck_window(im.size, (1440, 1800), 0, mbox, im)
    assert win["masthead_clear"] is True and win["centering"][0] != 0.5
    centred = hero_window._fit_box(im.size, (1440, 1800), (0.5, 0.5))
    k = (centred[2] - centred[0]) / 1440
    crop = im.crop((round(centred[0] + mbox[0] * k), round(mbox[1] * k), round(centred[0] + mbox[2] * k),
                    round(mbox[3] * k)))
    assert hero_window.foreign_object(crop)                         # the centred window had it under the masthead


def test_the_routes_fast_plate_draw_lays_the_held_plate_too():
    """graphics.draw_plate (the route's per-frame draw in the plate's own box) draws a label's held plate as
    TextPlate.draw does: the same pixels."""
    ground = _stripes((900, 300))
    p = graphics.place_plate(ground, (100, 150), "CATHERINE PALACE", graphics.place_font(34), "ls")
    assert p.variant == "plate"
    fast = graphics.draw_plate(ground.copy(), p, 0.7, 3)
    assert np.array_equal(np.asarray(fast), np.asarray(p.draw(ground, 0.7, 3).convert("RGB")))
