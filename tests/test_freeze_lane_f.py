"""Design freeze lane F, cycle 1 (hook, chapter and myth cards, callouts, badge): structural invariants.

The hook's plate and wash leave with its type (panel 2 MO, clips\\hook__obelisk__916 f037-f039: an empty INK band
stood across the photo for three frames after the type had gone)."""

from __future__ import annotations

import numpy as np
import pytest

from src import brand
from src import design_cards as dc
from src.brand_system import tok
from src.config import VIDEO_FPS

import tests.test_freeze_cards_2026_11_04 as F

FRAMES = range(round(dc.HOOK_SECONDS * VIDEO_FPS) + 1)


def test_hook_motion_reads_the_runtime_tokens():
    """SYS-F-1: the hook's hold and fade are the system's runtime tokens, read now; the ground derives from them."""
    assert dc.HOOK_HOLD == tok("motion.hook_hold") and dc.HOOK_FADE == tok("motion.hook_fade")
    assert dc.HOOK_GROUND_HOLD == dc.HOOK_HOLD and dc.HOOK_GROUND_FADE == pytest.approx(dc.HOOK_FADE + 1 / VIDEO_FPS)
    with brand.use(brand.DEFAULT_BRAND.derive("hook probe", system={"motion.hook_hold": 0.9, "motion.hook_fade": 0.2})):
        assert dc.HOOK_HOLD == 0.9 and dc.HOOK_GROUND_FADE == pytest.approx(0.2 + 1 / VIDEO_FPS)
        assert dc._hook_alpha(0.95) < 1.0 and dc._hook_ground_alpha(0.95) >= dc._hook_alpha(0.95)


def test_lane_f_hook_ground_leaves_with_type():
    """On every frame the ground is at least as present as the type it carries, on a frame without type it is at most
    HOOK_EMPTY_MAX, and it is gone by CAPTION_CLEAR."""
    for i in FRAMES:
        t = i / VIDEO_FPS
        a, g = dc._hook_alpha(t), dc._hook_ground_alpha(t)
        assert g >= a - 1e-9, (i, a, g)
        if a == 0.0:
            assert g <= dc.HOOK_EMPTY_MAX, (i, g)
    assert dc._hook_ground_alpha(dc.CAPTION_CLEAR) == 0.0
    # the type still holds whole through frame 32 (H4), and so does its ground
    assert all(dc._hook_ground_alpha(i / VIDEO_FPS) == 1.0 for i in range(33))


@pytest.mark.parametrize("ground", ["sky", "dark", "warm"])
def test_lane_f_no_empty_plate_on_the_drawn_frames(ground, photos):
    """Measured on the pixels: on a frame whose type is gone, the ground (the plate band, or on a wash ground the whole
    frame) departs from the bare pushed photo by at most HOOK_EMPTY_MAX of its full departure (frame 0), so no empty
    plate or wash is ever on screen. A wash ground is judged on the whole frame, never skipped (CI's skip baseline)."""
    plan = F.hook_plan(photos[ground], "Quartzite ridges form the ring", "f_geo")
    if plan.meta["scrim"]["mode"] == "plate":
        y0, y1 = plan.meta["scrim"]["plate"]
    else:
        y0, y1 = 0, None
    bg = plan.meta["_r"]["bg"]

    def departure(i):
        shown = np.asarray(dc.frame_at(plan, i).convert("L")).astype(float)[y0:y1]
        bare = np.asarray(dc._pushed_in(bg, 1 + dc.LOOP_PUSH * i).convert("L")).astype(float)[y0:y1]
        return float(np.abs(shown - bare).mean())

    full = departure(0)
    assert full > 0
    empty = [i for i in FRAMES if dc._hook_alpha(i / VIDEO_FPS) == 0.0]
    assert empty
    for i in empty:
        assert departure(i) <= dc.HOOK_EMPTY_MAX * full + 1.0, (i, departure(i), full)


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    d = tmp_path_factory.mktemp("lane_f_photos")
    return {k: F.T._photo(d / f"{k}.jpg", k) for k in ("sky", "dark", "warm")}


LOADERS = ("get_font", "truetype", "fit_text", "probe_text", "font_variant", "FreeTypeFont", "FeaturedFont")


@pytest.mark.parametrize("module", ["design_cards", "callouts"])
def test_lane_f_faces_come_from_brand_roles(module):
    """Panel 2 lead (second brand: callout labels kept a default face): every font a lane F surface loads names its
    face through a value, never a family literal, so a configured brand's roles reach the hook, chapter, myth and quote
    cards and the callout labels. (The caption and badge roles are read by video_composer, not here.)"""
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / f"{module}.py"
    literal = []
    for n in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and n.args:
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
            if name in LOADERS and isinstance(n.args[0], (ast.Constant, ast.JoinedStr)):
                literal.append((n.lineno, ast.unparse(n)[:80]))
    assert not literal, literal


# ------------------------------------------------------------------------------------------------ cycle 2: myth copy
STRESS_MYTHS = (F.SHORT, F.LONG_ET, F.LONGEST916, F.LONGEST169,
                ("Extraterrestrials made the circular rings",
                 "Wind and water slowly eroded the dome over millions of years into rings", "f_et"))


def test_lane_f_myth_limit_is_the_cards_own():
    """The field gate's and the writer's myth length are the smallest the myth card takes on any format, read from
    the card (never a pinned number of their own)."""
    from src import card_fields, fact_sheet
    n = dc.myth_chars_max()
    assert n == min(dc.MYTH_CHARS[w][0] for w, _ in dc.MYTH_FORMATS)
    assert {w for w, _ in dc.MYTH_FORMATS} == {False, True}
    assert card_fields.myth_max() == n
    prompt = fact_sheet.system_prompt()
    assert "<MYTH_CHARS>" not in prompt and f"at most {n} characters;" in prompt


def _outcome(m, size):
    try:
        F.plan_myth(m, size)
        return "drawn"
    except dc.CardRefused as e:
        return e.code or "refused"


@pytest.mark.parametrize("m", STRESS_MYTHS, ids=lambda m: m[0][:24])
def test_lane_f_a_myth_past_the_gate_sets_on_every_format(m):
    """A myth field the gate takes draws on 9:16 AND 16:9; one that a format's card cannot set is refused at the
    field (code unsupported_field) on every format, never drawn on one and missing on the other."""
    out = {size: _outcome(m, size) for size in (F.TALL, F.WIDE)}
    assert set(out.values()) <= {"drawn", "unsupported_field"}, out
    assert len(set(out.values())) == 1, out


def test_lane_f_gate_matches_the_card():
    """The short myth passes and draws everywhere; LONG_ET, which the 16:9 card cannot set (a lone middle word), is
    refused at the field although 9:16 alone would set it."""
    assert {_outcome(F.SHORT, s) for s in (F.TALL, F.WIDE)} == {"drawn"}
    assert {_outcome(F.LONG_ET, s) for s in (F.TALL, F.WIDE)} == {"unsupported_field"}
    assert "16:9" in dc.myth_fit_problem(*F.LONG_ET[:2], "https://www.britannica.com/x")
    assert dc.myth_fit_problem(*F.SHORT[:2], "https://www.britannica.com/x") == ""


def test_lane_f_refused_cards_are_in_the_runs_record():
    """Refused design cards go to payload["design_card_refusals"] (script_metadata.json), per format: a format's
    later render replaces its own entries and keeps the other format's."""
    from src.card_composition import record_refusals
    payload = {}
    record_refusals(payload, 1080, 1920, [{"kind": "mythfact", "field_id": "M_1", "reason": "a"}])
    record_refusals(payload, 1920, 1080, [{"kind": "mythfact", "field_id": "M_1", "reason": "b"}])
    record_refusals(payload, 1080, 1920, [])
    assert payload["design_card_refusals"] == [{"kind": "mythfact", "field_id": "M_1", "reason": "b",
                                                "format": "1920x1080"}]


# ------------------------------------------------------------------------------------------------ cycle 2: chapter
def _chapter(size, number=2, total=5):
    return dc.plan_chapter_card(F.T.chapter(number=number, total=total), F.T.SHEET, *size)


@pytest.mark.parametrize("number,total", [(1, 5), (2, 5), (5, 5), (10, 12)])
def test_lane_f_chapter_elements_one_treatment(number, total):
    """Lander ruling (C5/C6, lane F cycle 2): both formats draw the chapter's count with its numeral, in the numeral's
    face and GOLD, on its baseline, after its ink, at the same share of the numeral's size, revealed after it; the
    kicker reads CHAPTER alone; the drawn text says the structural number and total."""
    by = {}
    for size in (F.TALL, F.WIDE):
        plan = _chapter(size, number, total)
        e = {x.name: x for x in plan.elements}
        num, tot = e["numeral"], e["total"]
        assert plan.drawn_text["numeral"] == str(number) and plan.drawn_text["total"] == f"/ {total}"
        assert plan.meta["anchors"]["total_placement"] == "after_numeral"
        assert tot.face == num.face and tot.fill == num.fill == brand.GOLD and tot.kind == num.kind == "text"
        assert tot.xy[1] == num.xy[1] and tot.box[0] > num.box[2]
        assert tot.t_in > num.t_in
        assert abs(tot.px / num.px - dc.CHAPTER_TOTAL_SHARE) < 0.01
        assert plan.drawn_text["kicker_label"] == "CHAPTER"
        assert not any(t.startswith("/") for k, t in plan.drawn_text.items() if k != "total")
        by[size] = {(x.name, x.kind, x.face) for x in plan.elements
                    if x.name in ("numeral", "total", "kicker_label") or x.name.startswith("title_")}
    names = [{n for n, _, _ in v if not n.startswith("title_")} for v in by.values()]
    assert names[0] == names[1] and {"numeral", "total"} <= names[0]


def test_lane_f_chapter_total_one_column_per_video():
    """16:9: the numeral and its "/ N" end on one right edge for every chapter of the video (C2's column), so the
    divider and the title stand at one x."""
    rights = {(_chapter(F.WIDE, n, 5).meta["anchors"]["divider_x"]) for n in range(1, 6)}
    assert len(rights) == 1


# ------------------------------------------------------------------------------------------------ cycle 3
def test_lane_f_myth_copy_refusal_names_rule_format_and_card():
    """Lane F cycle 3 (every panel-4 lens: the 9:16 card's reason named only the 16:9 card): a field one format cannot
    set is refused on every format, and each card's reason names the rule, the format that could not set the copy,
    and the card it refuses."""
    reasons = {}
    for size in (F.TALL, F.WIDE):
        with pytest.raises(dc.CardRefused) as e:
            F.plan_myth(F.LONG_ET, size)
        reasons[size] = e.value.reason
        assert e.value.code == "unsupported_field"
        assert dc.MYTH_COPY_RULE in e.value.reason
        assert f"this {'16:9' if size == F.WIDE else '9:16'} myth card is refused" in e.value.reason
        assert "the 16:9 myth card cannot set it" in e.value.reason
    assert reasons[F.TALL] != reasons[F.WIDE]


def _title_lines(plan):
    return [e for e in plan.elements if e.name.startswith("title_")]


@pytest.mark.parametrize("title", F.RICHAT_ACTS)
def test_lane_f_wide_chapter_title_spans_the_numeral_or_has_no_clean_three_lines(title):
    """Lane F cycle 3 (16:9 title block 46-63 px above the numeral foot; "A DESERT / LANDMARK" parted a phrase where
    the copy breaks itself at its colon): a 16:9 title either ends on the numeral's baseline or is the two-line,
    top-aligned form because no clean three-line break spans the numeral; a three-line title is heading-valid, ends
    on no stop word, parts fewer phrases than the two lines would, and stands no taller than the numeral column."""
    n = F.RICHAT_ACTS.index(title) + 1
    plan = F.chapter_plan(F.WIDE, n, F.ACTS, title)
    lines = [e.text for e in _title_lines(plan)]
    a = plan.meta["anchors"]
    num = {e.name: e for e in plan.elements}["numeral"]
    from src.heading import valid
    if len(lines) == 3:
        assert valid(lines) and not dc._stop_end(lines)
        assert a["title_baselines"][-1] == a["Bn"]
        assert min(e.box[1] for e in _title_lines(plan)) >= num.box[1] - dc.CHAPTER_TALL_SLACK
    if len(lines) == 2 and a["title_baselines"][-1] != a["Bn"]:
        col = dc._numeral_tall(F.ACTS)
        P = _title_lines(plan)[0].px
        assert dc._chapter_wide_three(title.upper(), (P, lines), col, 1766 - a["title_x"], dc._Geo(*F.WIDE)) == (P, lines)


def test_lane_f_wide_chapter_breaks_where_the_tall_card_does():
    """The act 1 title the copy breaks at its colon takes one form on both formats (p3 TY: form mismatch)."""
    forms = [[e.text for e in _title_lines(F.chapter_plan(s, 1, F.ACTS, F.RICHAT_ACTS[0]))] for s in (F.TALL, F.WIDE)]
    assert forms[0] == forms[1] == ["INTRODUCTION:", "A DESERT LANDMARK", "FROM SPACE"]


@pytest.mark.parametrize("size", [F.TALL, (720, 1280)])
@pytest.mark.parametrize("title", ["The Ancient Quarry Canal", "Jars of Quartz", "Granite Quarry",
                                   "Cutting the Quarry Canal"])
def test_lane_f_tall_chapter_descender_stays_above_the_foot(size, title):
    """Stress OUT_OF_SAFE (12 of 12 on real heroes): a descender on the last title line (the tail of a Q) stays inside
    the 9:16 graphics box at every render size."""
    from src.safe_zones import graphics_box
    plan = F.chapter_plan(size, 2, 5, title)
    assert max(e.box[3] for e in _title_lines(plan)) <= graphics_box(*size)[3]


@pytest.mark.parametrize("lines,split", [
    (["MORE", "THAN 129 FT"], True), (["MORE THAN", "129 FT"], True), (["AT", "LEAST 2,000 YEARS"], True),
    (["ABOUT", "40 KM"], True), (["MORE THAN 129 FT", "ANCIENT"], False), (["ABOUT 40 KM", "ACROSS"], False),
    (["THE RING", "IS 40 KM"], False)])
def test_lane_f_a_hedge_stays_on_its_figures_line(lines, split):
    """Stress FIGURE_GATE (hook "MORE / THAN 129 FT", reachable with lowercase "more than"): no break runs through a
    hedge phrase or between it and its figure."""
    assert dc._qualifier_split(lines) is split


@pytest.mark.parametrize("claim", ["More than 129 ft", "More than 603 kg", "At least 2,000 years old",
                                   "About 40 km across"])
def test_lane_f_hook_draws_the_hedge_with_its_figure_or_refuses(claim, photos):
    """A hedged hook claim is drawn with the hedge on its figure's line, or refused (recorded), never drawn with a
    line that carries the figure without its hedge."""
    sheet = {"topic": "t", "facts": [F.T.fact("s_hook", claim)]}
    spec = F.T.hook(photos["dark"], sheet=sheet, claim=claim, fact_ids=("s_hook",))
    try:
        plan = dc.plan_hook_frame(spec, sheet)
    except dc.CardRefused as e:
        assert e.code in ("no_fit", "stop_word_end", "") and e.reason
        return
    lines = F.claim_lines(plan)
    assert not dc._qualifier_split(lines), lines
    hedge = dc._hedged_run(claim.upper())
    if hedge:
        assert any(hedge in ln for ln in lines), (hedge, lines)


def test_lane_f_hook_one_alignment_on_every_ground(photos):
    """Panel 4 BC r1 / CD r2 (the amber hook flush left on a wash, the obelisk hook centred on a plate): one rule.
    On every ground the claim stands flush left on one column edge, in the same position where the subject allows;
    only the ground (wash or plate) changes."""
    lefts, tops = set(), set()
    modes = set()
    for g in ("sky", "dark", "warm"):
        plan = F.hook_plan(photos[g])
        lefts |= {e.box[0] for e in plan.elements if e.name.startswith("title_")
                  and plan.drawn_text.get(e.name, "")[:1] not in "0123456789"}
        if plan.meta["anchors"]["position"] == "normal":
            tops.add(plan.meta["anchors"]["block_top"])
        modes.add(plan.meta["scrim"]["mode"])
        assert plan.meta["anchors"]["position"] in ("normal", "top")
    assert len(tops) <= 1, tops
    assert max(lefts) - min(lefts) <= 12, lefts      # one edge, less the glyphs' own side bearings


def test_lane_f_callout_low_contrast_is_the_refusal_type():
    """Stress (realistic callout_916-R1-0000): LowContrast was a bare ValueError, so the surface failed instead of
    being refused; it is the one refusal type now, with a reason and a code."""
    from src.callouts import LowContrast
    from src.refusal import LayoutError, record
    e = LowContrast("the 9:16 photo callout is refused under the non-text contrast gate")
    assert isinstance(e, LayoutError) and isinstance(e, ValueError)
    assert record(e, "callout")["code"] == "low_contrast" and "refused" in record(e, "callout")["reason"]


@pytest.mark.parametrize("ox,dx", [(1151, 3.5), (1150, 3.5), (871, -2.5), (40, 0.49), (41, 0.51)])
def test_lane_f_callout_text_registers_with_its_measure(ox, dx):
    """Stress LOW_CONTRAST (real Richat labels, planned 16.60, delivered 3.74-4.10): the label text is drawn on
    whole pixels at exactly the place its record measures the plate (_placed at a.ox + round(dx)), never a
    bilinear sub-pixel shift, and never one pixel off where round(ox + dx) and ox + round(dx) part (an odd ox)."""
    from types import SimpleNamespace
    from src import callouts
    a = SimpleNamespace(ox=ox, oy=ox + 7)
    tx, ty = callouts._text_at(a, dx, dx)
    assert isinstance(tx, int) and isinstance(ty, int)
    q = SimpleNamespace(empty=False, origin=(0, 0), box=(0, 0, 1, 1), xy=(0, 0))
    placed = callouts._placed(q, a.ox + round(dx), a.oy + round(dx))
    assert (tx, ty) == placed.origin


# ------------------------------------------------------------------------------------------------ cycle 3, rulings
@pytest.mark.parametrize("ground", ["sky", "dark", "warm"])
def test_lane_f_hook_wash_keeps_the_subject(ground, photos):
    """Lander ruling (the hook's frame 0 is a feed tile; subject visibility outranks ornament): a wash keeps at least
    HOOK_ABOVE_KEPT of the photo's brightness above the claim with the contrast gate met; where no wash does, the
    plate, its reason recorded."""
    plan = F.hook_plan(photos[ground], "Quartzite ridges form the ring", "f_geo")
    sc = plan.meta["scrim"]
    assert sc["above_kept"] >= dc.HOOK_ABOVE_KEPT or sc["mode"] == "plate", sc
    if sc["mode"] == "wash":
        assert sc["halo"] is not None and sc["above_kept"] >= dc.HOOK_ABOVE_KEPT
    else:
        assert "plate_reason" not in sc or "70%" in sc["plate_reason"]
    assert all(p.contrast is None or p.contrast >= 4.5 for _, p in plan.meta["_contrast"])


def test_lane_f_above_kept_reads_the_dimming():
    from PIL import Image
    base = Image.new("RGBA", (40, 100), (200, 200, 200, 255))
    none = Image.new("RGBA", (40, 100), (0, 0, 0, 0))
    half = Image.new("RGBA", (40, 100), brand.INK + (128,))
    assert dc._above_kept(base, none, 50) == 1.0
    assert 0.4 < dc._above_kept(base, half, 50) < 0.7


def _covers():
    import tests.test_r3_covers_2026_11_02 as R
    return R


@pytest.mark.parametrize("illustration", [True, False])
def test_lane_f_feed_badge_on_the_cover_is_legible_at_the_grid_and_only_on_illustrations(illustration):
    """Lander ruling (feed-size AI badge): on the Reel cover the disclosure is RECON_BADGE_FEED_LABEL, its cap at
    least the grid floor on the 124 px tile, inside the 3:4 crop, at the tile's contrast gate, and drawn when and
    only when the hero is an AI illustration."""
    from src.cover_design import GRID_CONTRAST, GRID_HOOK
    from src.video_composer import RECON_BADGE_FEED_LABEL
    R = _covers()
    _, lay = R._cover(R.GUMBAZ, "depth_sandwich", illustration=illustration)
    if not illustration:
        assert "provenance" not in lay.meta and "provenance" not in lay.meta.get("plates", {})
        return
    tag = lay.meta["provenance"]
    assert " ".join(tag["lines"]) == tag["label"] == RECON_BADGE_FEED_LABEL
    el = lay.meta["grid_proof"]["elements"]["provenance"]
    assert el["class"] == "hook" and el["cap_124"] >= GRID_HOOK["provenance"] and el["inside_34"]
    assert el["contrast_124"] is not None and el["contrast_124"] >= GRID_CONTRAST
    assert not [f for f in lay.meta["grid_proof"]["fails"] if f.startswith("provenance")]
    x0, y0, x1, y1 = tag["plate"]
    win = lay.meta["composition"]["image_box"]
    assert 84 <= x0 and x1 <= 1080 - 84 and 240 <= y0 and y1 <= 1680     # inside the margins and the 3:4 crop
    assert win[0] <= x0 and win[1] <= y0 and y1 <= win[3], (tag["plate"], win)
    # inside the window wherever the window is as wide as the badge at the grid floor; else recorded
    assert (x1 <= win[2]) != tag["straddles_window"], (tag["plate"], win)


def test_lane_f_feed_badge_stays_inside_a_wide_enough_window():
    """The cover badge never straddles the photo window's edge when the window holds it (panel 4 PL r1)."""
    R = _covers()
    _, lay = R._cover(R.GUMBAZ, "badge", illustration=True)
    tag, win = lay.meta["provenance"], lay.meta["composition"]["image_box"]
    if win[2] - win[0] >= tag["plate"][2] - tag["plate"][0]:
        assert not tag["straddles_window"] and tag["plate"][2] <= win[2]


@pytest.mark.parametrize("illustration", [True, False])
def test_lane_f_feed_badge_on_the_thumbnail_is_legible_at_168_and_only_on_illustrations(illustration):
    """Lander ruling: on the YouTube thumbnail the disclosure is RECON_BADGE_FEED_LABEL, its cap at least the line
    floor at 168 px wide and its contrast at least 4.5 at 246 px, clear of every drawn line, and drawn when and only
    when the hero is an AI illustration."""
    from src import youtube_thumbnail as yt
    from src.video_composer import RECON_BADGE_FEED_LABEL
    R = _covers()
    meta = {}
    _, plates = yt.render(R._photo(), R.RICHAT, meta=meta, illustration=illustration)
    names = {n: p for n, p in plates}
    if not illustration:
        assert "provenance" not in names
        return
    p = names["provenance"]
    assert p.text == RECON_BADGE_FEED_LABEL and not p.collides
    row = meta["downscale"]["provenance"]
    assert row["cap_168"] >= yt.LINE_FLOOR and row["contrast_246"] >= 4.5
    assert not [f for f in meta["downscale_fails"] if f.startswith("provenance")]
