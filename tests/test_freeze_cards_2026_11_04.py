"""Freeze panel (2026-11-04): the hook frame, chapter card and myth-vs-fact card (src/design_cards.py).

One test (or a few) per spec item: T1-T4 (the Windows failures' root causes), M0-M9 (myth card), W1 (line-end stop
words), H1-H5 (hook), C1-C4 (chapter). Every card is planned through the real path on a checked, signed card field
(tests/test_design_cards_2026_11_03.py helpers)."""
import copy
import math

import numpy as np
import pytest
from PIL import Image

from src import brand
from src import design_cards as dc
from src.graphics import _WideGrid
from src.safe_zones import graphics_box

import tests.test_design_cards_2026_11_03 as T

SHEET = copy.deepcopy(T.SHEET)      # this module's own sheet: the shared one is never extended
SHEET["facts"] += [f for f in (
    T.fact("fz_wall", "Erosion of the tilted quartzite layers left the outer walls standing as hard ridges",
           evidence=[T.ev("https://www.britannica.com/myth/fz_wall", 2, "It is a popular belief that ancient builders "
                          "poured the outer walls out of concrete. Erosion left the walls.")],
           misconception="Ancient builders poured the outer walls out of concrete"),
    T.fact("fz_wide", "Slow erosion of tilted quartzite and sandstone layers left the outer walls as ridges",
           evidence=[T.ev("https://www.britannica.com/myth/fz_wide", 2, "It is a popular belief that ancient builders "
                          "cast the outer walls of the ring from Roman concrete. Erosion left the walls.")],
           misconception="Ancient builders cast the outer walls of the ring from Roman concrete"),
    T.fact("fz_hook", "Sandstone ridges rise from the desert floor around the eroded dome"),
) if not any(x["id"] == f["id"] for x in SHEET["facts"])]

LONG_ET = ("Extraterrestrials constructed the circular rings",
           "Wind and water slowly eroded the dome over millions of years into concentric rings", "f_et")
LONGEST916 = ("Ancient builders poured the outer walls out of concrete",
              "Erosion of the tilted quartzite layers left the outer walls standing as hard ridges", "fz_wall")
LONGEST169 = ("Ancient builders cast the outer walls of the ring from Roman concrete",
              "Slow erosion of tilted quartzite and sandstone layers left the outer walls as ridges", "fz_wide")
SHORT = ("Aliens built the ring", "The ring formed through erosion", "f_myth")
TALL, WIDE = (1080, 1920), (1920, 1080)
GROUNDS = ("warm", "sky", "noise", "dark", "smooth", "busy")


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    d = tmp_path_factory.mktemp("freeze_photos")
    return {k: T._photo(d / f"{k}.jpg", k) for k in GROUNDS}


def myth(m=SHORT, photo=None, verdict="false"):
    return T.myth(sheet=SHEET, myth_text=m[0], fact_text=m[1], fact_id=m[2], photo=photo, verdict=verdict)


def plan_myth(m=SHORT, size=TALL, photo=None, verdict="false"):
    return dc.plan_myth_fact(myth(m, photo, verdict), SHEET, *size)


def els(plan):
    return {e.name: e for e in plan.elements}


def strikes(plan):
    return [e for e in plan.elements if e.name.startswith("strike_")]


def myth_zone(plan):
    """The strike's clamp zone in frame px: the graphics box (9:16), or the myth column to 16 px short of the
    divider (16:9), its left one overhang before the column (freeze panel round 2, M10 (3): symmetric overhang;
    was the column's own left edge)."""
    if plan.width < plan.height:
        z = graphics_box(plan.width, plan.height)
        return z[0], z[2]
    g = _WideGrid(plan.width, plan.height)
    ov = max(1, round(round(dc.STRIKE_OVERHANG * plan.meta["anchors"]["myth_px"]) * g.k))
    return g.x(153) - ov, g.x(dc.MYTH_X[True][1] - dc.MYTH_DIVIDER_AIR) - max(1, round(16 * g.k))


# ------------------------------------------------------------------------------------------------ T1 strike clamp
@pytest.mark.parametrize("size", [TALL, (720, 1280), WIDE, (1280, 720)])
def test_t1_strike_overhang_clamped_to_the_zone(size):
    """T1: a myth line that fills its measure (the short myth, 782 of 788 px on 9:16) keeps every strike inside the
    zone, with round(0.06 x Pm) of overhang on each side wherever the zone allows it."""
    for m in (SHORT, LONG_ET, LONGEST916):
        plan = plan_myth(m, size)
        e = els(plan)
        zl, zr = myth_zone(plan)
        k = plan.width / 1080 if plan.width < plan.height else _WideGrid(plan.width, plan.height).k
        ov = max(1, round(round(dc.STRIKE_OVERHANG * plan.meta["anchors"]["myth_px"]) * k))
        if size == TALL and m is SHORT:
            assert e["myth_0"].box[2] - e["myth_0"].box[0] >= 770      # the line does fill its measure
        for s in strikes(plan):
            line = e["myth_" + s.name.split("_")[1]].box
            assert zl <= s.box[0] and s.box[2] <= zr, (m[0], size, s.name, s.box, (zl, zr))
            assert s.box[0] == max(zl, line[0] - ov), (s.name, s.box, line, ov)
            assert s.box[2] == min(zr, line[2] + ov), (s.name, s.box, line, ov)


def test_t1_myth_wrap_reserves_the_overhang():
    """T1: the myth is broken inside the measure less 2 x ceil(0.06 x Pm) where that costs no line (round 2, W2:
    unless only the full measure's break has no stop-word end; the clamp then trims the overhang)."""
    geo = dc._Geo(*TALL)
    f = dc.get_font("Anton", 60)
    lines = dc._myth_wrap("ALIENS BUILT THE RING FROM THE CONCENTRIC OUTER WALLS", 60, 788, 2, geo)
    assert lines and all(dc._width(ln, f) <= 788 - 2 * math.ceil(0.06 * 60) for ln in lines)
    lines = dc._myth_wrap("ANCIENT BUILDERS POURED THE OUTER WALLS OUT OF CONCRETE", 60, 788, 2, geo)
    assert lines == ["ANCIENT BUILDERS POURED", "THE OUTER WALLS OUT OF CONCRETE"]
    assert all(dc._width(ln, f) <= 788 for ln in lines)


# ------------------------------------------------------------------------------------------------ M0 / M1 9:16 fit
@pytest.mark.parametrize("m", [LONG_ET, LONGEST916])
def test_m0_long_myths_keep_their_source(m):
    """M0 (a): the long 9:16 myths with a host set the fact at >= 76 px and draw their source line inside 1058."""
    plan = plan_myth(m)
    a = plan.meta["anchors"]
    assert a["px"] >= dc.MYTH_FLOOR_SOURCE and plan.meta["source_omitted"] is None
    src = els(plan)["source"]
    assert src.text and src.box[3] <= 1058


def test_m0_card_that_fits_only_bare_is_refused(monkeypatch):
    """M0 (b), merge rule (2026-11-04, step 1 wins, recorded): under MYTH_FLOOR_SOURCE the fit falls through to the
    largest size that still holds the source line instead of refusing; a card is refused only when no size holds
    it with its source (here: bare it fits at 64 with a 56 px myth, under MYTH_FLOOR_MYTH), never drawn without it.
    A zone that fails 76 but holds a smaller size takes that size with the source kept."""
    geo = dc._Geo(*TALL)
    m = "EXTRATERRESTRIAL ARCHITECTS CONSTRUCTED THE CONCENTRIC RINGS"
    f = "WIND AND WATER SLOWLY ERODED THE DOME OVER MILLIONS OF YEARS INTO CONCENTRIC RINGS"
    bare = dc._myth_fit(m, f, None, geo)
    assert bare is not None and bare[0] >= 64
    assert dc._myth_fit(m, f, "BRITANNICA.COM", geo) is None
    # and a domain wider than the fact column refuses rather than dropping the line
    assert dc._myth_fit("ALIENS BUILT THE RING", "THE RING FORMED THROUGH EROSION", "X" * 60 + ".COM", geo) is None
    # the fall-through: the long myth in a zone 98 px shorter does not fit at 76 with its source; it takes 68
    monkeypatch.setitem(dc.MYTH_BOTTOM, False, 960)
    fit = dc._myth_fit(LONG_ET[0].upper(), LONG_ET[1].upper(), "BRITANNICA.COM", geo)
    assert fit is not None and fit[0] < dc.MYTH_FLOOR_SOURCE and fit[3] == "BRITANNICA.COM" and fit[4] is None
    assert fit[5] >= dc.MYTH_FLOOR_MYTH, fit


@pytest.mark.parametrize("m", [SHORT, LONG_ET, LONGEST916])
def test_m0_no_hosted_tall_plan_omits_its_source(m):
    """M0 (c): no 9:16 hosted plan ever has source_omitted set."""
    plan = plan_myth(m)
    assert plan.meta["source_omitted"] is None and "source" in els(plan)


@pytest.mark.parametrize("ground", GROUNDS)
@pytest.mark.parametrize("m", [LONG_ET, LONGEST916])
def test_m1_long_tall_myths_on_every_ground(m, ground, photos):
    """M1: fact >= 76, myth >= 58 in <= 2 lines, source drawn, lowest ink <= 1058 on every ground."""
    plan = plan_myth(m, photo=photos[ground])
    a = plan.meta["anchors"]
    assert a["px"] >= 76 and a["myth_px"] >= 58 and len(a["myth_baselines"]) <= 2, a
    assert "source" in els(plan) and max(e.box[3] for e in plan.elements) <= 1058


def test_m1_short_cards_unchanged():
    """M1 regression: the short and misleading 9:16 cards keep their size and myth share (P 112, myth 96 = 0.86)."""
    for verdict in ("false", "misleading"):
        a = plan_myth(SHORT, verdict=verdict).meta["anchors"]
        assert (a["px"], a["myth_px"], len(a["myth_baselines"]), len(a["fact_baselines"])) == (112, 96, 1, 2)
        assert a["myth_px"] == dc.myth_px(112, False, 0.86) and not a["compact"]


def test_m1_never_under_76_with_a_host():
    """M1, merge rule (2026-11-04, step 1 wins, recorded): with a host a 9:16 fact size under 76 is taken only as
    the fall-through to the largest size that still holds the source (never drawn without it); these fixtures all
    hold it at 76 or more, or are refused."""
    geo = dc._Geo(*TALL)
    texts = [(m[0].upper(), m[1].upper()) for m in (SHORT, LONG_ET, LONGEST916)] + [
        ("EXTRATERRESTRIAL ARCHITECTS CONSTRUCTED THE CONCENTRIC RINGS",
         "WIND AND WATER SLOWLY ERODED THE DOME OVER MILLIONS OF YEARS INTO CONCENTRIC RINGS"),
        ("ANCIENT ARCHITECTS POURED THE OUTERMOST WALLS FROM CONCRETE",
         "EROSION OF THE TILTED QUARTZITE LAYERS LEFT THE OUTER WALLS STANDING AS HARD RIDGES")]
    for mt, ft in texts:
        try:
            fit = dc._myth_fit(mt, ft, "BRITANNICA.COM", geo)
        except dc.CardRefused as e:       # round 2 (W2): it fits only with a stop-word end, so it is refused
            assert e.code == "stop_word_end"
            continue
        assert fit is None or (fit[3] == "BRITANNICA.COM" and fit[4] is None and fit[5] >= 58
                               and len(fit[1]) <= 2), (mt, fit and fit[:2])
        assert fit is None or fit[0] >= 76, (mt, fit[:2])   # measured: no fixture here needs the fall-through


def test_m1_refusal_code(monkeypatch):
    """M1: a hosted 9:16 card that does not fit at 76 with its source is refused (code no_fit), never set smaller
    nor drawn without the source (the zone is shortened here, as no myth within the character limits overruns it)."""
    monkeypatch.setitem(dc.MYTH_BOTTOM, False, 860)
    with pytest.raises(dc.CardRefused) as e:
        plan_myth(LONG_ET)
    assert e.value.code == "no_fit" and "source" in e.value.reason


# ------------------------------------------------------------------------------------------------ M2 / M3 16:9
def test_m2_wide_long_myth_keeps_its_size():
    """M2: the 16:9 long myth sets the fact at >= 88 and the myth at >= 64 in <= 4 lines (it was 64 / 44)."""
    a = plan_myth(LONG_ET, WIDE).meta["anchors"]
    assert a["px"] >= 88 and a["myth_px"] >= 64 and len(a["myth_baselines"]) <= 4, a


def test_m2_wide_no_regression():
    """M2: the 16:9 short and longest916 cards keep at least their sizes (128 and 104)."""
    assert plan_myth(SHORT, WIDE).meta["anchors"]["px"] >= 128
    assert plan_myth(LONGEST916, WIDE).meta["anchors"]["px"] >= 104


def test_m2_long_word_is_not_lone():
    """M2: on 16:9 a one-word myth line of >= 12 letters is a long word filling its line, not a lone word."""
    a = plan_myth(LONG_ET, WIDE)
    assert a.drawn_text["myth_0"] == "EXTRATERRESTRIALS"


@pytest.mark.parametrize("m", [SHORT, LONG_ET, LONGEST916, LONGEST169])
def test_m3_wide_columns_share_their_rules(m):
    """M3: the MYTH and FACT kicker rules stand at one y, the taller column's block is centred on 475 (+/- 2)
    and stays inside 129..816."""
    plan = plan_myth(m, WIDE)
    e, a = els(plan), plan.meta["anchors"]
    assert e["myth_rule"].box[1] == e["fact_rule"].box[1]
    top, bottom = a["block"]
    assert abs((top + bottom) / 2 - 475) <= 2 and top >= 129 and bottom <= 816, a["block"]


# ------------------------------------------------------------------------------------------------ M4 9:16 cohesion
@pytest.mark.parametrize("verdict", ["false", "misleading"])
def test_m4_pair_cohesion(verdict):
    """M4: the myth's last baseline to the FACT rule is at most round(1.4 x P) (157 at 112), the group centred on
    694 (+/- 4), everything inside 288..1058."""
    plan = plan_myth(SHORT, verdict=verdict)
    a = plan.meta["anchors"]
    assert a["F"] - a["myth_baselines"][-1] <= round(1.4 * a["px"]) == 157
    top, bottom = a["block"]
    assert abs((top + bottom) / 2 - 694) <= 4
    assert all(288 <= e.box[1] and e.box[3] <= 1058 for e in plan.elements if e.kind == "text")


# ------------------------------------------------------------------------------------------------ M5 strike
@pytest.mark.parametrize("size", [TALL, WIDE])
@pytest.mark.parametrize("verdict", ["false", "misleading"])
def test_m5_strike_geometry_and_colour(size, verdict):
    """M5 (M11, round 2: thickness clamp(round(0.08 x Pm), 5, 10), was 0.10 / 6..12; a 1 px keyline): its centre 0.55-0.60 of the cap above the baseline; an INK
    keyline above and below; GOLD under the pen, #B18728 on the last frame; the twin still >= PLACE_TARGET."""
    plan = plan_myth(LONG_ET, size, verdict=verdict)
    a = plan.meta["anchors"]
    Pm = a["myth_px"]
    cap = dc._cap(Pm)
    last = np.asarray(dc.frame_at(plan, plan.frames - 1)).astype(int)
    t_gold = plan.meta["t_s"] + plan.meta["pen_total"] + 0.05
    gold = np.asarray(dc.frame_at(plan, math.ceil(t_gold * 30))).astype(int)
    for s in strikes(plan):
        k = int(s.name.split("_")[1])
        base = a["myth_baselines"][k]
        assert s.box[3] - s.box[1] == min(10, max(5, round(0.08 * Pm)))
        centre = (s.box[1] + s.box[3]) / 2
        assert 0.55 <= (base - centre) / cap <= 0.60, (s.name, base, centre, cap)
        x = (s.box[0] + 8) if verdict == "misleading" else (s.box[0] + s.box[2]) // 2
        for y in (s.box[1] - 1, s.box[3]):
            assert np.abs(last[y, x] - np.array(brand.INK)).max() <= 6, (s.name, last[y, x])
        y = (s.box[1] + s.box[3]) // 2
        assert np.abs(last[y, x] - np.array(dc.STRIKE_BACK)).max() <= 4, last[y, x]
        assert np.abs(gold[y, x] - np.array(brand.GOLD)).max() <= 4, gold[y, x]
    assert brand.hex_colour(dc.STRIKE_BACK).upper() == "#B18728"
    for name, p in plan.meta["_contrast"]:
        if name.startswith("myth_") and name[5:].isdigit():
            assert p.contrast >= brand.PLACE_TARGET


# ------------------------------------------------------------------------------------------------ M6 pen
def test_m6_per_line_pen():
    """M6 (M11, round 2: the gap is one frame, 0.033 s, was 0.05; the pen fits 0.95 s for 3 lines or more, was
    0.80): each line its own stroke; the pen is 0.45 s for one line; the fact's kicker lands pen_total + 0.10 after
    the pen starts."""
    one = plan_myth(SHORT)
    assert one.meta["pen_total"] == pytest.approx(0.45)
    assert dc.strike_progress(one, one.meta["t_s"] + 0.45) == 1.0
    four = plan_myth(LONGEST916, WIDE)
    assert len(strikes(four)) == 4 and four.meta["pen_total"] <= 0.95 + 1e-9
    for plan in (one, four, plan_myth(LONG_ET)):
        t_s, pen = plan.meta["t_s"], plan.meta["pen_total"]
        assert plan.meta["t_f"] - t_s == pytest.approx(pen + 0.10, abs=1 / 30)
        bars = strikes(plan)
        for a, b in zip(bars, bars[1:]):
            assert b.t_in == pytest.approx(a.t_full + 0.033, abs=1e-3)
            assert dc.strike_progress(plan, a.t_full + 0.032, b.name) == 0.0
            assert dc.strike_progress(plan, a.t_full + 1e-3, a.name) == 1.0      # t_full is rounded to 4 places
    sched = dc._pen_schedule([800, 800, 800, 800])
    assert sched[2] == pytest.approx(0.95) and len(sched[1]) == 4


# ------------------------------------------------------------------------------------------------ M7 timing
def test_m7_longest_wide_card_plans():
    """M7: the longest 16:9 card plans under MAX_SECONDS (it was refused at 10.43 s); the fact stays fully drawn for
    its read; the short card's timing moves by <= 0.3 s (t_s 1.85, 6.0 s recorded)."""
    plan = plan_myth(LONGEST169, WIDE)
    assert plan.seconds <= dc.MAX_SECONDS
    for p in (plan, plan_myth(SHORT), plan_myth(LONG_ET), plan_myth(LONG_ET, WIDE)):
        f0 = p.meta["t_f"] + 0.10
        fact = " ".join(v for k, v in sorted(p.drawn_text.items()) if k.startswith("fact_") and k[5:].isdigit())
        held = p.seconds - dc.TRANS_DUR - p.meta["rest_f"]
        assert held >= dc._read(fact) - (p.meta["rest_f"] - f0) - 1 / 30
    short = plan_myth(SHORT)
    assert abs(short.meta["t_s"] - 1.85) <= 0.3 and abs(short.seconds - 6.0) <= 0.3


# ------------------------------------------------------------------------------------------------ M8 divider
def test_m8_divider_waits_for_the_pen():
    plan = plan_myth(SHORT, WIDE)
    t_s = plan.meta["t_s"]
    assert dc.alpha_at(plan, "divider", t_s - 0.05) == 0.0
    assert dc.alpha_at(plan, "divider", t_s + dc.STRIKE_PEN + 0.30) == 1.0


# ------------------------------------------------------------------------------------------------ M9 source
@pytest.mark.parametrize("size", [TALL, WIDE])
def test_m9_source_line(size, photos):
    """M9: the domain at 34 / 36 px, weight 600, its text the signed host exactly; the SOURCE chrome label before it
    (not a fact role); drawn contrast >= 7.0 and >= 4.5; the 16:9 kicker labels at 40 px."""
    for ground in GROUNDS:
        plan = plan_myth(SHORT, size, photo=photos[ground])
        e, roles = els(plan), plan.meta["roles"]
        src, lab = e["source"], e["source_label"]
        assert src.px == 38 and src.weight == 600       # M12 (round 2): 38 on both formats (was 34 / 36)
        assert src.text == "BRITANNICA.COM" and src.fill == dc.SOURCE_FILL
        assert lab.text == "SOURCE" and roles["source_label"] == "chrome" and roles["source"] == "data"
        assert src.box[0] - lab.box[2] == 14
        last = dc.frame_at(plan, plan.frames - 1)
        c = dict(plan.meta["_contrast"])
        assert c["source"].measure(last) >= 7.0 and c["source_label"].measure(last) >= 4.5, ground
    wide = els(plan_myth(SHORT, WIDE))
    assert wide["myth_label"].px == wide["fact_label"].px == 40


# ------------------------------------------------------------------------------------------------ W1 stop words
def test_w1_stop_ends():
    assert dc._stop_end(["THE EYE OF THE", "GREAT SAHARA"]) and not dc._stop_end(["THE EYE", "OF THE GREAT"])
    calls = []

    def wrap(P):
        calls.append(P)
        return {176: ["THE EYE OF THE", "GREAT SAHARA"], 168: ["THE EYE", "OF THE GREAT SAHARA"]}.get(P)
    assert dc._w1_ladder((176, 168, 160), wrap) == (168, ["THE EYE", "OF THE GREAT SAHARA"])
    # W2 (round 2, operator: no line ends on THE or OF): a ladder with only stop-word ends is refused (was: the
    # first size's break kept)
    with pytest.raises(dc.CardRefused) as e:
        dc._w1_ladder((176, 160), lambda P: ["THE EYE OF", "THE GREAT"])
    assert e.value.code == "stop_word_end"


def test_w1_breaks():
    """W1 (W2, round 2: the 16:9 chapter breaks 'THE EYE / OF THE GREAT SAHARA' at 160, was 'THE EYE OF / THE
    GREAT SAHARA' at 168); the 16:9 longest916 myth has no line ending in THE; the 9:16 longest916 myth's lines are
    even (shortest / longest >= 0.85)."""
    p = chapter_plan(WIDE)
    assert [p.drawn_text["title_0"], p.drawn_text["title_1"]] == ["THE EYE", "OF THE GREAT SAHARA"]
    wide = plan_myth(LONGEST916, WIDE)
    assert not any(v.split()[-1] == "THE" for k, v in wide.drawn_text.items() if k.startswith("myth_") and k[5:].isdigit())
    tall = plan_myth(LONGEST916)
    # round 2 (W2, deliberate change): no stop-word end beats balance, so the 9:16 longest916 myth breaks
    # "ANCIENT BUILDERS POURED / THE OUTER WALLS OUT OF CONCRETE" (was even to >= 0.85 with "... POURED THE / ...")
    assert no_stop_end([tall.drawn_text[n] for n in ("myth_0", "myth_1")])


# ------------------------------------------------------------------------------------------------ hook
def hook_plan(photo, claim="About 40 km across", fid="f_len", eyebrow=""):
    return dc.plan_hook_frame(T.hook(photo, sheet=SHEET, claim=claim, fact_ids=(fid,), eyebrow=eyebrow), SHEET)


def claim_lines(plan):
    return [plan.drawn_text[k] for k in sorted(plan.drawn_text) if k.startswith("title_")]


@pytest.mark.parametrize("ground", GROUNDS)
def test_h1_claim_size_by_line_count(ground, photos):
    """H1: one line at 192, two at 152, three at 128 on the wash; the right ink inside 888 and the top ink 56 px
    under the masthead's foot on every ground."""
    expect = [("The ring", "f_len", 1, 192), ("About 40 km across", "f_len", 2, 152),
              ("Concentric sandstone quartzite", "f_geo", 3, 128), ("Ridges rise from the desert", "fz_hook", None, 152)]
    for claim, fid, n, px in expect:
        plan = hook_plan(photos[ground], claim, fid)
        a = plan.meta["anchors"]
        if n is not None and a["position"] == "normal":
            assert (a["lines"], a["P"]) == (n, px), (claim, a)
        assert a["P"] <= px
        e = els(plan)
        titles = [x for x in plan.elements if x.name.startswith("title_")]
        assert max(x.box[2] for x in titles) <= 888
        top = min(x.box[1] for x in plan.elements if x.name.startswith(("title_", "eyebrow")))
        assert top >= e["brand"].box[3] + 56 - 1, (claim, top, e["brand"].box)


def test_h2_gold_rule(photos):
    """H2: the first line with a figure is GOLD, else a 2-3 line claim's last line, else a one-line claim's last
    word (a second run on its baseline); the eyebrow is BONE at 80 % and the claim holds the only gold type."""
    assert dc._hook_gold(["ABOUT 40 KM", "ACROSS"]) == (0, None)
    assert dc._hook_gold(["RIDGES RISE", "FROM THE DESERT"]) == (1, None)
    assert dc._hook_gold(["THE RING"]) == (0, 4)
    plan = hook_plan(photos["dark"], "The ring")
    golds = [p.text for p in plan.meta["_r"]["plates"] if p.fill == brand.GOLD]
    assert golds == ["RING"]
    plan = hook_plan(photos["dark"], "About 40 km across", eyebrow="The Richat ring")
    e = els(plan)
    assert e["title_0"].fill == brand.GOLD and e["title_1"].fill == brand.BONE
    assert e["eyebrow"].fill == brand.BONE and plan.meta["eyebrow_alpha"] == 0.8
    eb = [p for p in plan.meta["_r"]["plates"] if p.text == plan.drawn_text["eyebrow"]]
    assert eb and all(p.alpha_scale == 0.8 for p in eb)
    assert e["eyebrow_rule"].fill == brand.GOLD
    for name, v in plan.meta["drawn_contrast"].items():
        assert v >= 4.5, (name, v)


def test_h3_one_axis_per_position(photos):
    """H3: one axis per position: x 100 on the wash; on the plate all centred on 540 (fix round 1, operator
    2026-11-04: the rework's flush-left plate, round 2's H6 (4), stood 48 px off the masthead's axis and is undone),
    refit inside HOOK_MEASURE_CENTRED; the plate's first ink 56 px under the masthead's box."""
    plan = hook_plan(photos["dark"], eyebrow="The Richat ring")
    assert plan.meta["anchors"]["position"] == "normal"
    e = els(plan)
    assert abs(e["brand_mark"].box[0] - 100) <= 2 and abs(e["eyebrow_rule"].box[0] - 100) <= 2
    assert abs(e["eyebrow"].box[0] - 100) <= 2 and all(abs(e[n].box[0] - 100) <= 2 for n in ("title_0", "title_1"))
    normal_p = plan.meta["anchors"]["P"]
    plan = hook_plan(photos["sky"], eyebrow="The Richat ring")
    assert plan.meta["anchors"]["position"] == "plate" and plan.meta["anchors"]["P"] <= normal_p
    e = els(plan)
    for n in ("eyebrow", "title_0", "title_1", "eyebrow_rule"):
        assert abs((e[n].box[0] + e[n].box[2]) / 2 - 540) <= 3 and 192 <= e[n].box[0] and e[n].box[2] <= 888, (
            n, e[n].box)
    mast = _union_box([e["brand"].box, e["brand_mark"].box])
    assert abs((mast[0] + mast[2]) / 2 - 540) <= 3, mast
    first = min(x.box[1] for x in plan.elements if x.name.startswith(("title_", "eyebrow")))
    assert first - e["brand"].box[3] >= 56


def test_h4_dissolve(photos):
    """H4: a smoothstep dissolve: the plate band's mean luma moves <= 70 per frame over frames 33-42; everything is
    gone by 1.40 s; frames 0-32 at full alpha."""
    plan = hook_plan(photos["sky"], "Concentric sandstone quartzite", "f_geo")
    assert plan.meta["scrim"]["mode"] == "plate"
    y0, y1 = plan.meta["scrim"]["plate"]
    lum = [float(np.asarray(dc.frame_at(plan, i).convert("L"))[y0:y1].mean()) for i in range(32, 44)]
    assert max(abs(b - a) for a, b in zip(lum, lum[1:])) <= 70, lum
    for e in plan.elements:
        assert all(dc.alpha_at(plan, e.name, i / 30) == 1.0 for i in range(33))
        assert dc.alpha_at(plan, e.name, 1.40) == 0.0
    # H6 (5), round 2: the ground holds to 1.22 s (was 1.16) and fades over 0.18 s to CAPTION_CLEAR
    assert dc._hook_alpha(1.40, dc.HOOK_GROUND_HOLD) == 0.0 and dc._hook_alpha(1.22, dc.HOOK_GROUND_HOLD) == 1.0
    bg = plan.meta["_r"]["bg"]
    assert T.mad(dc.frame_at(plan, 42), dc._pushed_in(bg, 1 + 0.0016 * 42)) <= 1


@pytest.mark.parametrize("ground", GROUNDS)
def test_h5_drawn_contrast(ground, photos):
    """H5: the drawn contrast is measured on frames 0 and 32 and recorded; >= 4.5 for cream and gold on every
    ground (the warm amber ground takes the radial scrim on the wash)."""
    for claim, fid, eb in (("About 40 km across", "f_len", ""), ("The ring", "f_len", "The Richat ring")):
        plan = hook_plan(photos[ground], claim, fid, eb)
        assert plan.meta["drawn_contrast_min"] is not None and plan.meta["drawn_contrast_min"] >= 4.5, plan.meta[
            "drawn_contrast"]
        rad = plan.meta["scrim"]["radial"]
        assert rad["applied"] == (plan.meta["scrim"]["mode"] == "wash" and 15 <= rad["hue"] <= 55
                                  and rad["saturation"] > 90)


# ------------------------------------------------------------------------------------------------ chapter
def chapter_plan(size, number=2, total=5, title="The Eye of the Great Sahara", photo=None):
    return dc.plan_chapter_card(T.chapter(sheet=SHEET, number=number, total=total, title=title, photo=photo), SHEET,
                                *size)


@pytest.mark.parametrize("size", [TALL, WIDE, (720, 1280), (1280, 720)])
def test_c1_chapter_total(size):
    """C1 (round 2: C5 / C6): 9:16 '/ N' after the numeral in GOLD at 72 px (was 90 GOLD_DIM), on its baseline, 16
    px after its ink; 16:9 '/ N' on the kicker line in the kicker's face (was 93 px Anton after the numeral, C5); >=
    4.5; none for a one-chapter video; inside the zone."""
    plan = chapter_plan(size, 3, 4)
    e = els(plan)
    wide = size[0] > size[1]
    k = _WideGrid(*size).k if wide else size[0] / 1080
    tot, num = e["total"], e["numeral"]
    ref = e["kicker_label"] if wide else num
    assert tot.text == "/ 4" and tot.fill == brand.GOLD
    assert tot.px == max(1, round((dc.KICKER_WIDE_PX if wide else 72) * k))
    assert abs(tot.xy[1] - ref.xy[1]) <= 1
    assert abs((tot.box[0] - ref.box[2]) - round((24 if wide else 16) * k)) <= 1 + k, (tot.box, ref.box)
    assert dict(plan.meta["_contrast"])["total"].contrast >= 4.5
    if wide:
        g = _WideGrid(*size)
        assert e["divider"].box[2] < tot.box[0] and tot.box[2] <= g.x(1766) + 1
    else:
        z = graphics_box(*size)
        assert z[0] <= tot.box[0] and tot.box[2] <= z[2] and z[1] <= tot.box[1] and tot.box[3] <= z[3]
    assert "total" not in els(chapter_plan(size, 1, 1))


def test_c2_numeral_column_fixed_per_video():
    """C2: on 16:9 the title stands at one x for chapters 1, 9, 10 and 12 of a 12-chapter video; the 2-line title's
    cap top stands no higher than CHAPTER_TALL_SLACK (12) above the numeral's ink top (C5, round 2: it met it within
    2 px at 168; W2 sets this title at 160, 16 px lower), with the baselines shared."""
    xs = {chapter_plan(WIDE, n, 12, "The Ring").meta["anchors"]["title_x"] for n in (1, 9, 10, 12)}
    assert len(xs) == 1
    rights = {els(chapter_plan(WIDE, n, 12, "The Ring"))["numeral"].box[2] for n in (1, 9, 10, 12)}
    assert max(rights) - min(rights) <= 2
    plan = chapter_plan(WIDE)
    e, a = els(plan), plan.meta["anchors"]
    assert len(a["title_baselines"]) == 2
    cap_top = a["title_baselines"][0] - round(0.86 * a["title_px"])
    assert cap_top - e["numeral"].box[1] >= -dc.CHAPTER_TALL_SLACK, (cap_top, e["numeral"].box)
    assert abs(e["title_1"].xy[1] - e["numeral"].xy[1]) <= 1


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_c3_saturated_ground(size, photos):
    """C3: on a saturated (amber) ground the chapter is darkened until its drawn minimum reaches 7.0; a grey
    ground is untouched."""
    plan = chapter_plan(size, photo=photos["warm"])
    a = plan.meta["anchors"]
    assert a["ground_saturation"] > 90 and a["drawn_contrast_min"] >= 7.0, a
    grey = chapter_plan(size, photo=photos["noise"]).meta["anchors"]
    assert grey["darken_steps"] == 0 or grey["ground_saturation"] > 90


def test_c4_chapter_motion():
    """C4: the 16:9 reveal paced to 3.5 s (rest 1.30-1.60 s); a title line's alpha >= 0.85 at its start + 0.22 s on
    both formats; the 9:16 kicker, numeral and divider timings unchanged."""
    wide = chapter_plan(WIDE)
    assert 1.30 <= wide.meta["rest_s"] <= 1.60
    m = wide.meta["_motion"]
    assert m["kicker_rule"][1:3] == (0.20, 0.40) and m["numeral"][1] == 0.30 and m["divider"][1:3] == (0.55, 0.45)
    assert [m[f"title_{k}"][1] for k in range(2)] == pytest.approx([0.80, 0.98])
    tall = chapter_plan(TALL)
    mt = tall.meta["_motion"]
    assert mt["kicker_rule"][1:3] == (0.10, 0.30) and mt["kicker_label"][1:3] == (0.15, dc.REVEAL)
    assert mt["numeral"][1] == 0.25
    assert [mt[f"title_{k}"][1] for k in range(2)] == pytest.approx([0.62, 0.80])     # C6 (was 0.55, 0.67)
    for plan in (wide, tall):
        for k in range(2):
            start = plan.meta["_motion"][f"title_{k}"][1]
            assert dc.alpha_at(plan, f"title_{k}", start + 0.22) >= 0.85
    # the rise keeps its own motion: 14 px over the line's motion, the alpha over 0.28 s
    it = next(i for i in wide.meta["_r"]["items"] if i.get("name") == "title_0")
    assert it["rise"] and dc.RISE == 14


# ================================================================================================ round 2 (2026-11-04)
# T0, C5, W2, C6, A1, H6, M10, M11, M12. Content comes from real runs: the Richat deep dive's act titles and act
# count (output/run_richat_structure_sahara_eye_1792740961_long/verified_deep_dive.json), the real facts of the
# tracked fact sheets (data/fact_sheets/*.json, copied into a test-local sheet; card fields built from their own words
# through card_fields.check_field and signed under conftest's test key). "The Eye of the Great Sahara" stays a test
# fixture title only.
import json
import re
from pathlib import Path

from PIL import ImageFont, features

ROOT = Path(__file__).resolve().parent.parent
ACT2, ACT3, ACTS = "Documented Physical Architecture", "Origin Debates and Rock Dating", 5   # real act titles, 5 acts
RICHAT_MYTH = "The Richat Structure is an impact crater"          # fact-1's subject + fact-9's own words
RICHAT_SHORT = "The impact theory was disproved"                   # fact-9 verbatim
RICHAT_1969 = "The impact theory was disproved in 1969"            # fact-9, clause elided
# round 2 (review, fact meaning): fact-9's 'suspected' is kept, so the suspected coesite proves to be barite as the source
# states (round 1's 'coesite was identified ... as barite' reversed the finding); the 1969 variant that keeps that
# meaning is 83 characters and does not fit 9:16 with its source line, so it is the longest field
RICHAT_LONG_NODATE = "The impact theory was disproved when suspected coesite was identified as barite"  # fact-9
# round 4 (review: richat_long must draw 1969): fact-9's own words in order, elisions only ('the', both 'was' of the
# main clause, 'shock-metamorphism', 'mislabeled'), 'suspected' kept, 79 characters, so it fits 9:16 with its source
RICHAT_LONG = "Impact theory disproved when suspected coesite was identified in 1969 as barite"
# round 4: the longest myth inside the 16:9 limits with the 1969 field (9:16 refuses the 57-character myth: limit 56)
RICHAT_LONGEST = ("The Richat Structure is an extraterrestrial impact crater", RICHAT_LONG)


def real_sheet(topic):
    """A test-local copy of a tracked, real fact sheet."""
    sh = json.loads((ROOT / "data" / "fact_sheets" / f"{topic}.json").read_text(encoding="utf-8"))
    sh.setdefault("topic", topic)
    return copy.deepcopy(sh)


@pytest.fixture(scope="module")
def richat():
    return real_sheet("richat_structure_sahara_eye")


def richat_myth(sheet, correction=RICHAT_LONG, myth_text=RICHAT_MYTH, size=TALL, photo=None):
    spec = T.myth(sheet=sheet, myth_text=myth_text, fact_text=correction, fact_id="fact-9", photo=photo)
    return dc.plan_myth_fact(spec, sheet, *size)


def lines_of(plan, group):
    return [plan.drawn_text[k] for k in sorted(plan.drawn_text, key=lambda n: (len(n), n))
            if k.startswith(group + "_") and k[len(group) + 1:].isdigit()]


def no_stop_end(lines):
    return not any(ln.split()[-1].lower().strip(".,;:") in dc.W1_STOPS for ln in lines[:-1])


# ------------------------------------------------------------------------------------------------ T0
def test_t0_json_records_read_as_utf8():
    """T0 (3): no bare read_text() on a .json record in the design-card tests (cp1252 on Windows)."""
    src = (ROOT / "tests" / "test_design_cards_2026_11_03.py").read_text(encoding="utf-8")
    assert not re.search(r"json\.loads\([^)]*\.read_text\(\)\)", src)
    assert not re.search(r"\.json\"?\)?\.read_text\(\)", src)


# ------------------------------------------------------------------------------------------------ C5
def test_c5_wide_title_keeps_its_measure():
    """C5 (operator decision): 1920x1080, total 5: numeral column 325, divider 391, title 459 (measure 1307); the real
    act 2 sets 'DOCUMENTED PHYSICAL / ARCHITECTURE' at 152 (was 136), act 3 at 176 (was 168)."""
    p2 = chapter_plan(WIDE, 2, ACTS, ACT2)
    a = p2.meta["anchors"]
    assert (a["title_x"], a["divider_x"], a["numeral_right"]) == (459, 391, 325)
    assert a["divider_x"] - a["numeral_right"] == 66
    assert (a["title_px"], lines_of(p2, "title")) == (152, ["DOCUMENTED PHYSICAL", "ARCHITECTURE"])
    p3 = chapter_plan(WIDE, 3, ACTS, ACT3)
    assert p3.meta["anchors"]["title_px"] == 176 and no_stop_end(lines_of(p3, "title"))


@pytest.mark.parametrize("ground", ["dark", "noise", "warm"])
def test_c5_total_on_the_kicker(ground, photos):
    """C5: '/ 5' on the kicker baseline, 24 design px after the CHAPTER label's ink, in its GOLD, revealed with it;
    nothing between the numeral and the divider; contrast within 0.1 of the label's."""
    plan = chapter_plan(WIDE, 2, ACTS, ACT2, photo=photos[ground])
    e = els(plan)
    tot, lab = e["total"], e["kicker_label"]
    assert tot.text == "/ 5" and tot.fill == lab.fill == brand.GOLD and tot.face == "Montserrat" and tot.weight == 800
    assert abs(tot.xy[1] - lab.xy[1]) <= 1
    assert abs((tot.box[0] - lab.box[2]) - 24) <= 1
    assert tot.box[0] > e["divider"].box[2]
    for t in [i / 30 for i in range(0, 106)]:
        assert dc.alpha_at(plan, "total", t) == dc.alpha_at(plan, "kicker_label", t)
    last = dc.frame_at(plan, plan.frames - 1)
    c = dict(plan.meta["_contrast"])
    assert abs(c["total"].measure(last) - c["kicker_label"].measure(last)) <= 0.1


def test_c5_total_and_column():
    """C5: no total for a one-chapter video; with 12 chapters the title x is one for chapters 1, 9, 10, 12; two
    title lines share the numeral's baseline and their cap top stays within 12 of its ink top where the size
    allows (176)."""
    assert "total" not in els(chapter_plan(WIDE, 1, 1, ACT3))
    assert len({chapter_plan(WIDE, n, 12, ACT3).meta["anchors"]["title_x"] for n in (1, 9, 10, 12)}) == 1
    p = chapter_plan(WIDE, 3, ACTS, ACT3)
    e, a = els(p), p.meta["anchors"]
    assert abs(a["title_baselines"][-1] - e["numeral"].xy[1]) <= 1
    # the cap is one per video (the tallest numeral, '2' at 311 px ink); this chapter's '3' inks 310, hence + 1
    assert abs((a["title_baselines"][0] - round(0.86 * a["title_px"])) - e["numeral"].box[1]) <= 12 + 1
    # round 4 (review: act 2's two lines at 152 stood 30 px under the numeral's ink top): the step grows (at most
    # CHAPTER_WIDE_LEAD_MAX x P) so the first cap top meets the numeral's ink top; the baselines stay shared
    for title, num in ((ACT2, 2), (ACT3, 3), ("The Eye of the Great Sahara", 2)):
        p = chapter_plan(WIDE, num, ACTS, title)
        e, a = els(p), p.meta["anchors"]
        assert len(a["title_baselines"]) == 2
        # a numeral inking under the video's tallest (act 3's '3': 310 against 311) adds its own difference
        own = dc._numeral_tall(ACTS) + (e["numeral"].box[1] - e["numeral"].xy[1])
        assert abs((a["title_baselines"][0] - round(0.86 * a["title_px"])) - e["numeral"].box[1]) <= 12 + own, (
            title, a)
        assert abs(a["title_baselines"][-1] - e["numeral"].xy[1]) <= 1
        step = a["title_baselines"][1] - a["title_baselines"][0]
        assert round(0.98 * a["title_px"]) <= step <= round(dc.CHAPTER_WIDE_LEAD_MAX * a["title_px"]), (title, step)


def test_fr1_c5_cap_top_meets_the_numeral_at_every_two_line_size(monkeypatch):
    """Fix round 1 (review: act 2's cap top within 12 of the numeral's ink top at every title size, 152 included):
    the 16:9 two-line ladder holds only sizes whose first cap top can stand within CHAPTER_TALL_SLACK of the numeral
    column's ink top (176..152 for a 5-act video); the real act 2 meets it on its drawn ink at every 16:9 frame size;
    a title that would need a smaller two-line size is refused (no_fit), never drawn under the numeral's top."""
    col = dc._numeral_tall(ACTS)
    allowed = [P for P in dc.CHAPTER_WIDE_TWO if dc._chapter_wide_two_ok(P, col)]
    assert allowed == [176, 168, 160, 152], allowed
    for P in allowed:
        off = dc._chapter_wide_off(["A B", "C D"], P, col)
        assert abs(round(0.86 * P) + off[1] - col) <= dc.CHAPTER_TALL_SLACK, (P, off)
    for size in (WIDE, (1280, 720), (2560, 1440)):
        k = _WideGrid(*size).k
        p = chapter_plan(size, 2, ACTS, ACT2)
        e = els(p)
        assert p.meta["anchors"]["title_px"] == 152
        assert abs(e["title_0"].box[1] - e["numeral"].box[1]) / k <= dc.CHAPTER_TALL_SLACK, (size, e["title_0"].box)
    real = dc._design_wrap
    monkeypatch.setattr(dc, "_design_wrap", lambda text, face, w, px, *a, **kw: (
        None if px >= 152 and kw.get("even") is not None else real(text, face, w, px, *a, **kw)))
    monkeypatch.setattr(dc, "CHAPTER_WIDE_ONE", ())
    with pytest.raises(dc.CardRefused) as err:
        chapter_plan(WIDE, 2, ACTS, ACT2)
    assert err.value.code == "no_fit"


# ------------------------------------------------------------------------------------------------ W2
def test_w2_ladder_bounds():
    """W2: up to 3 further steps and never more than 24 px below the first size; else refused (stop_word_end)."""
    bad, good = ["THE EYE OF", "THE GREAT"], ["THE EYE", "OF THE GREAT"]
    ladder = (176, 168, 160, 152, 144)
    assert dc._w1_ladder(ladder, lambda P: good if P == 152 else bad) == (152, good)
    with pytest.raises(dc.CardRefused) as e:
        dc._w1_ladder(ladder, lambda P: good if P == 144 else bad)
    assert e.value.code == "stop_word_end"
    with pytest.raises(dc.CardRefused):
        dc._w1_ladder((176, 168, 160, 152), lambda P: bad)


def test_w2_within_one_size_compliance_wins():
    """W2: within one size a break with no stop-word end beats a better-balanced one with."""
    f = dc.get_font("Anton", 176)
    lines = dc._wrap("THE EYE OF THE GREAT SAHARA", f, 1406, 2, even=True)
    assert lines and no_stop_end(lines)


def test_w2_sahara_fixture_titles():
    """W2, product choice P2: 16:9 'THE EYE / OF THE GREAT SAHARA' at 160; 9:16 in 3 lines at >= 120."""
    wide = chapter_plan(WIDE)
    assert (wide.meta["anchors"]["title_px"], lines_of(wide, "title")) == (160, ["THE EYE", "OF THE GREAT SAHARA"])
    tall = chapter_plan(TALL)
    assert lines_of(tall, "title") == ["THE EYE", "OF THE GREAT", "SAHARA"] and tall.meta["anchors"]["title_px"] >= 120


@pytest.mark.parametrize("field", ["short", "long", "nodate", "longest"])
def test_w2_richat_myth_breaks(field, richat):
    """W2: the 16:9 Richat myth sets 'THE RICHAT / STRUCTURE IS / AN IMPACT CRATER' (no line ends on AN) for the
    short, long and longest fields; the long fact has no line ending on WAS or a stop word."""
    m, c = {"short": (RICHAT_MYTH, RICHAT_SHORT), "long": (RICHAT_MYTH, RICHAT_LONG),
            "nodate": (RICHAT_MYTH, RICHAT_LONG_NODATE), "longest": RICHAT_LONGEST}[field]
    plan = richat_myth(richat, c, m, WIDE)
    ml, fl = lines_of(plan, "myth"), lines_of(plan, "fact")
    if field != "longest":
        assert ml == ["THE RICHAT", "STRUCTURE IS", "AN IMPACT CRATER"], ml
    assert no_stop_end(ml) and no_stop_end(fl), (ml, fl)
    assert not any(ln.split()[-1] == "WAS" for ln in fl[:-1]), fl
    # round 4 (review: the 16:9 longest fact read 'WHEN SUSPECTED / COESITE WAS IDENTIFIED'): no field splits it
    assert not any(ln.split()[-1] == "SUSPECTED" for ln in fl[:-1]), fl


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_w2_every_fixture_line_end(size, photos, richat):
    """W2: no planned chapter, myth, fact or hook line other than the last ends on a W1_STOPS word."""
    plans = [chapter_plan(size, 2, ACTS, t) for t in (ACT2, ACT3, "The Eye of the Great Sahara")]
    plans += [plan_myth(m, size) for m in (SHORT, LONG_ET, LONGEST916) + ((LONGEST169,) if size == WIDE else ())]
    plans += [richat_myth(richat, c, size=size) for c in (RICHAT_SHORT, RICHAT_LONG)]
    if size == TALL:
        plans += [hook_plan(photos["dark"], c, f) for c, f in (("About 40 km across", "f_len"),
                                                                ("Ridges rise from the desert", "fz_hook"))]
    for p in plans:
        for g in ("title", "myth", "fact"):
            assert no_stop_end(lines_of(p, g)) if lines_of(p, g) else True, (g, lines_of(p, g))


# ------------------------------------------------------------------------------------------------ C6
def test_c6_tall_chapter_fill():
    """C6: real act 2 on 9:16 plans 3 lines at >= 128 (was 88); act 3 2 lines at >= 112, right ink <= 888; every
    block inside 288..1058, centred on min(864, 1058 - H / 2) (act 3 at >= 734)."""
    p2 = chapter_plan(TALL, 2, ACTS, ACT2)
    assert lines_of(p2, "title") == ["DOCUMENTED", "PHYSICAL", "ARCHITECTURE"] and p2.meta["anchors"]["title_px"] >= 128
    p3 = chapter_plan(TALL, 3, ACTS, ACT3)
    assert len(lines_of(p3, "title")) == 2 and p3.meta["anchors"]["title_px"] >= 112
    assert max(e.box[2] for e in p3.elements if e.name.startswith("title_")) <= 888
    for p in (p2, p3, chapter_plan(TALL)):
        a = p.meta["anchors"]
        assert max(e.box[3] for e in p.elements) <= 1059 and min(e.box[1] for e in p.elements) >= 288
        assert abs(a["block_centre"] - min(864, 1058 - a["block_h"] / 2)) <= 2
    assert p3.meta["anchors"]["block_centre"] >= 694 + 40


@pytest.mark.parametrize("ground", GROUNDS)
def test_c6_total_and_motion(ground, photos):
    """C6: the total GOLD at 72 px (drawn >= 4.5 on every test ground), hidden until 0.55 s; titles from 0.62 s, 0.18
    s apart, >= 0.85 at start + 0.22 and whole by 0.62 + 0.18 (n - 1) + 0.40; the card stays 2.5 s."""
    plan = chapter_plan(TALL, 2, ACTS, ACT2, photo=photos[ground])
    e = els(plan)
    assert e["total"].fill == brand.GOLD and e["total"].px == 72
    last = dc.frame_at(plan, plan.frames - 1)
    assert dict(plan.meta["_contrast"])["total"].measure(last) >= 4.5
    assert dc.alpha_at(plan, "total", 0.54) == 0.0
    n = len(lines_of(plan, "title"))
    for k in range(n):
        assert dc.alpha_at(plan, f"title_{k}", 0.62 + 0.18 * k + 0.22) >= 0.85
    assert dc.alpha_at(plan, f"title_{n - 1}", 0.62 + 0.18 * (n - 1) + 0.40) == 1.0
    assert plan.seconds == 2.5


# ------------------------------------------------------------------------------------------------ A1
def _ink_gaps(plan, group):
    ls = sorted((e for e in plan.elements if e.name.startswith(group + "_") and e.name[len(group) + 1:].isdigit()),
                key=lambda e: int(e.name.split("_")[1]))
    k = plan.width / 1080 if plan.width < plan.height else _WideGrid(plan.width, plan.height).k
    return [(b.text, (b.box[1] - a.xy[1]) / k) for a, b in zip(ls, ls[1:])]


ACC_SHEET = copy.deepcopy(SHEET)
ACC_SHEET["facts"] += [
    T.fact("fz_acc", "Érosion of the Åland ridges left Örebro walls standing"),
    T.fact("fz_acc_m", "Érosion of the Åland ridges left Örebro walls standing",
           evidence=[T.ev("https://www.britannica.com/myth/fz_acc", 2, "It is a popular belief that ancient builders "
                          "poured the Åland walls. Érosion of the Åland ridges left Örebro walls standing.")],
           misconception="Ancient builders poured the Åland walls")]


def test_a1_lead_unchanged_without_marks():
    """A1: lines without diacritics keep 0.98 x P exactly."""
    assert dc._lead(["ABOUT 40 KM", "ACROSS", "THE RING"], 152, 149, 131) == [0, 149, 298]


def test_a1_konigsberg_hook(photos):
    """A1 on the real 'Shipped to Königsberg Castle' (amber-room-nazi-looting-1941): L stays 1010 and the accented
    line's ink top clears the line above's baseline by >= 16, on three grounds."""
    sh = real_sheet("amber_room_heist")
    for g in ("dark", "sky", "busy"):
        spec = T.hook(photos[g], sheet=sh, claim="Shipped to Königsberg Castle",
                      fact_ids=("amber-room-nazi-looting-1941",), first_burst="In 1941 the room vanished")
        plan = dc.plan_hook_frame(spec, sh)
        a = plan.meta["anchors"]
        if a["position"] == "normal":
            assert a["L"] == 1010
        for text, gap in _ink_gaps(plan, "title"):
            if dc._accented(text):
                assert gap >= 16, (g, text, gap)


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_a1_accented_chapter_and_myth(size):
    """A1: synthetic chapter, myth and fact lines with Ö, É and Å clear the line above by >= 16 design px."""
    ch = dc.plan_chapter_card(T.chapter(sheet=ACC_SHEET, number=2, total=5, title="Över Åland Érosion Ridges Örebro"),
                              ACC_SHEET, *size)
    for text, gap in _ink_gaps(ch, "title"):
        assert not dc._accented(text) or gap >= 16, (text, gap)
    m = T.myth(sheet=ACC_SHEET, myth_text="Ancient builders poured the Åland walls",
               fact_text="Érosion of the Åland ridges left Örebro walls standing", fact_id="fz_acc_m")
    plan = dc.plan_myth_fact(m, ACC_SHEET, *size)
    for g in ("myth", "fact"):
        for text, gap in _ink_gaps(plan, g):
            assert not dc._accented(text) or gap >= 16, (g, text, gap)


# ------------------------------------------------------------------------------------------------ H6
def test_h6_hook_ladder_and_gold(photos):
    """H6 (1), (3): 'Inscribed in 1994' (nazca-unesco-listing) at 176 in 2 lines inside 888, its figure line GOLD;
    'Shipped to Königsberg Castle' golds the lines holding KÖNIGSBERG and CASTLE, SHIPPED stays BONE."""
    sh = real_sheet("nazca_lines_runway_paradox")
    plan = dc.plan_hook_frame(T.hook(photos["dark"], sheet=sh, claim="Inscribed in 1994",
                                     fact_ids=("nazca-unesco-listing",), first_burst="Lines drawn without wings"), sh)
    a = plan.meta["anchors"]
    assert (a["P"], a["lines"]) == (176, 2)
    assert max(e.box[2] for e in plan.elements if e.name.startswith("title_")) <= 888
    assert dc._hook_gold_lines(["SHIPPED TO", "KÖNIGSBERG CASTLE"], "Shipped to Königsberg Castle") == ((1,), None)
    assert dc._hook_gold_lines(["SHIPPED TO KÖNIGSBERG", "CASTLE"], "Shipped to Königsberg Castle") == ((0, 1), None)
    assert dc._hook_gold_lines(["DISASSEMBLED", "IN 36 HOURS"], "Disassembled in 36 hours") == ((1,), None)
    assert dc._hook_gold_lines(["RIPPLED ACROSS", "THE GLOBE"], "Rippled across the globe") == ((1,), None)
    # H2's cases keep their gold
    assert dc._hook_gold_lines(["ABOUT 40 KM", "ACROSS"], "About 40 km across") == ((0,), None)
    assert dc._hook_gold_lines(["THE RING"], "The ring") == ((0,), 4)
    assert dc._hook_gold_lines(["RIDGES RISE", "FROM THE DESERT"], "Ridges rise from the desert") == ((1,), None)
    sh = real_sheet("amber_room_heist")
    plan = dc.plan_hook_frame(T.hook(photos["dark"], sheet=sh, claim="Shipped to Königsberg Castle",
                                     fact_ids=("amber-room-nazi-looting-1941",),
                                     first_burst="In 1941 the room vanished"), sh)
    for e in plan.elements:
        if e.name.startswith("title_"):
            assert e.fill == (brand.GOLD if ("KÖNIGSBERG" in e.text or "CASTLE" in e.text) else brand.BONE), e.text


def _union_box(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def test_h6_plate_flush_left_and_feathered(photos):
    """H6 (4), fix round 1 (operator 2026-11-04: the rework's flush-left panel stood 48 px off the masthead's axis):
    on the bright plate the claim is centred under the centred masthead, refit inside HOOK_MEASURE_CENTRED (192..888),
    while the wash keeps flush left at x 100; the plate's luma ramps over >= 60 px at its top and bottom edges with
    no row step over 20."""
    normal = hook_plan(photos["dark"], "Concentric sandstone quartzite", "f_geo")
    plan = hook_plan(photos["sky"], "Concentric sandstone quartzite", "f_geo")
    a = plan.meta["anchors"]
    assert a["position"] == "plate" and a["P"] <= normal.meta["anchors"]["P"]
    e = els(plan)
    assert abs(els(normal)["brand_mark"].box[0] - 100) <= 2 and abs(els(normal)["title_0"].box[0] - 100) <= 2
    titles = [x.box for n, x in e.items() if n.startswith("title_")]
    for b in titles:
        assert abs((b[0] + b[2]) / 2 - 540) <= 3 and b[0] >= 192 and b[2] <= 888, b
    brand_box = _union_box([e["brand"].box, e["brand_mark"].box])
    assert abs((brand_box[0] + brand_box[2]) / 2 - 540) <= 3, brand_box
    y0, y1 = plan.meta["scrim"]["plate"]
    lum = np.asarray(dc.frame_at(plan, 0).convert("L"), np.float32)[:, 900:1060].mean(axis=1)
    for a_, b_ in ((y0 - 70, y0 + 2), (y1 - 2, y1 + 70)):
        seg = lum[a_:b_]
        assert np.abs(np.diff(seg)).max() <= 20, np.abs(np.diff(seg)).max()
    top = lum[y0 - 64:y0]
    assert (top[0] - top[-1]) > 0 and np.count_nonzero(np.abs(np.diff(top)) > 0.5) >= 40


def test_h6_dissolve(photos):
    """H6 (5): every type element's alpha is <= 0.10 at t = 1.22 s (round 4: measured at 1.22 itself, not at the first
    frame after it; fix round 1: HOOK_HOLD 1.07 with HOOK_FADE 0.16, so the frame on screen at 1.22 s, frame 36, is
    too: test_fr1_h6_dissolve_measured_on_the_frame_at_the_spec_time) and every hook alpha is 0 from 1.40 s; frames
    0..32 at full
    alpha; the plate band moves <= 70 luma per frame over frames 33..42."""
    plan = hook_plan(photos["sky"], "Concentric sandstone quartzite", "f_geo")
    assert dc.HOOK_FADE == 0.16 and dc.HOOK_GROUND_HOLD == 1.22
    for e in plan.elements:
        assert all(dc.alpha_at(plan, e.name, i / 30) == 1.0 for i in range(33))
        assert dc.alpha_at(plan, e.name, 1.22) <= 0.10, (e.name, dc.alpha_at(plan, e.name, 1.22))
        assert dc.alpha_at(plan, e.name, 1.40) == 0.0
    assert dc._hook_alpha(1.40, dc.HOOK_GROUND_HOLD) == 0.0
    y0, y1 = plan.meta["scrim"]["plate"]
    lum = [float(np.asarray(dc.frame_at(plan, i).convert("L"))[y0:y1].mean()) for i in range(33, 43)]
    assert max(abs(b - a) for a, b in zip(lum, lum[1:])) <= 70, lum


@pytest.mark.parametrize("ground", ["sky", "dark"])
def test_fr1_h6_dissolve_measured_on_the_frame_at_the_spec_time(ground, photos, monkeypatch):
    """Fix round 1 (review: test at the spec time, not a chosen frame): the frame on screen at t = 1.22 s (frame
    floor(1.22 x 30) = 36, shown 1.200-1.233 s) has every type element at alpha <= 0.10, measured on its pixels: the
    type's share of the difference between the same frame composed with the type whole and with it gone."""
    plan = hook_plan(photos[ground], "Concentric sandstone quartzite", "f_geo")
    i = math.floor(1.22 * 30 + 1e-9)
    assert i == 36
    assert all(dc.alpha_at(plan, e.name, i / 30) <= 0.10 for e in plan.elements)
    assert all(dc.alpha_at(plan, e.name, k / 30) == 1.0 for e in plan.elements for k in range(33))
    shown = np.asarray(dc.frame_at(plan, i)).astype(float)
    real = dc._hook_alpha

    def forced(a):
        def f(t, hold=dc.HOOK_HOLD, fade=None):
            return a if hold == dc.HOOK_HOLD and fade is None else real(t, hold, fade)
        return f
    monkeypatch.setattr(dc, "_hook_alpha", forced(1.0))
    whole = np.asarray(dc.frame_at(plan, i)).astype(float)
    monkeypatch.setattr(dc, "_hook_alpha", forced(0.0))
    gone = np.asarray(dc.frame_at(plan, i)).astype(float)
    box = _union_box([e.box for e in plan.elements if e.name.startswith("title_")])
    sl = (slice(box[1], box[3]), slice(box[0], box[2]))
    span = np.abs(whole[sl] - gone[sl]).sum()
    assert span > 0
    measured = np.abs(shown[sl] - gone[sl]).sum() / span
    assert measured <= 0.10, measured


# ------------------------------------------------------------------------------------------------ M10
def test_m10_wide_columns(richat):
    """M10: divider 786, the fact column to 1766; the long field sets its fact in 4 lines at >= 96 with no weak line
    end ('THE IMPACT THEORY / WAS DISPROVED / WHEN SUSPECTED COESITE / WAS IDENTIFIED AS BARITE'; four lines one step
    smaller beat five with as many weak ends) and the myth at >= 84 (round 4: the 16:9 myth share starts at 0.88, so
    myth_px(96) = 84); richat_short's myth at >= 84 and fact >= 120; the source drop max(68, round(0.58 P)). The 1969
    field (RICHAT_LONG, round 4) sets 4 lines at 92: its first line 'IMPACT THEORY DISPROVED' inks 961 px at 96
    against the 926 column."""
    assert dc.MYTH_X[True] == (153, 840) and dc.MYTH_MEASURE[True] == (617, 926)
    long_ = richat_myth(richat, RICHAT_LONG_NODATE, size=WIDE)
    a = long_.meta["anchors"]
    assert lines_of(long_, "fact") == ["THE IMPACT THEORY", "WAS DISPROVED", "WHEN SUSPECTED COESITE",
                                       "WAS IDENTIFIED AS BARITE"], lines_of(long_, "fact")
    assert a["px"] >= 96, a["px"]
    assert a["myth_px"] >= 84, (a["px"], a["myth_px"])
    dated = richat_myth(richat, RICHAT_LONG, size=WIDE)
    assert lines_of(dated, "fact") == ["IMPACT THEORY DISPROVED", "WHEN SUSPECTED COESITE", "WAS IDENTIFIED",
                                       "IN 1969 AS BARITE"], lines_of(dated, "fact")
    assert dated.meta["anchors"]["px"] == 92 and dated.meta["anchors"]["myth_px"] == 80
    fl = lines_of(long_, "fact")
    assert not any(ln.split()[-1].lower() in dc.WEAK_LINE_END for ln in fl[:-1]), fl
    e = els(long_)
    assert e["divider"].box[0] == 786
    assert e["source"].xy[1] - a["fact_baselines"][-1] >= max(68, round(0.58 * a["px"]))
    short = richat_myth(richat, RICHAT_SHORT, size=WIDE).meta["anchors"]
    assert short["myth_px"] >= 84 and short["px"] >= 120, short
    top, bottom = a["block"]
    assert abs((top + bottom) / 2 - 475) <= 2


@pytest.mark.parametrize("m", [SHORT, LONG_ET, LONGEST169])
def test_m10_symmetric_overhang(m):
    """M10 (3): on 16:9 both ends of every strike overhang round(0.06 x Pm)."""
    plan = plan_myth(m, WIDE)
    e = els(plan)
    ov = round(0.06 * plan.meta["anchors"]["myth_px"])
    zl, zr = myth_zone(plan)
    for s in strikes(plan):
        line = e["myth_" + s.name.split("_")[1]].box
        assert line[0] - s.box[0] == ov
        if s.box[2] < zr:
            assert s.box[2] - line[2] == ov


# ------------------------------------------------------------------------------------------------ M11
@pytest.mark.parametrize("size", [TALL, WIDE])
def test_m11_strike_bar(size, richat):
    """M11 (1): clamp(round(0.08 Pm), 5, 10) thick, a 1 px INK keyline, centre 0.55..0.60 of the cap."""
    plan = richat_myth(richat, RICHAT_LONG, size=size)
    a = plan.meta["anchors"]
    Pm, cap = a["myth_px"], dc._cap(a["myth_px"])
    last = np.asarray(dc.frame_at(plan, plan.frames - 1)).astype(int)
    for s in strikes(plan):
        k = int(s.name.split("_")[1])
        assert s.box[3] - s.box[1] == min(10, max(5, round(0.08 * Pm)))
        assert 0.55 <= (a["myth_baselines"][k] - (s.box[1] + s.box[3]) / 2) / cap <= 0.60
        x = (s.box[0] + s.box[2]) // 2
        for y in (s.box[1] - 1, s.box[3]):
            assert np.abs(last[y, x] - np.array(brand.INK)).max() <= 6
    assert dc.STRIKE_KEYLINE == 1


def test_m11_pen_and_read(richat):
    """M11 (2), (3): 4-line strokes >= 0.20 s one frame apart; pen <= 0.95 s for >= 3 lines, <= 0.80 for 1-2; the
    16:9 richat_longest strike at <= 2.95 s and the card <= 8.3 s."""
    four = plan_myth(LONGEST169, WIDE)
    bars = strikes(four)
    assert len(bars) == 4 and all(b.t_full - b.t_in >= 0.20 - 1e-3 for b in bars)
    assert all(abs(b.t_in - a.t_full - 0.033) <= 1e-3 for a, b in zip(bars, bars[1:]))
    for p in (four, plan_myth(SHORT), plan_myth(LONG_ET), plan_myth(SHORT, WIDE)):
        n = len(strikes(p))
        assert p.meta["pen_total"] <= (0.95 if n >= 3 else 0.80) + 1e-9
    longest = richat_myth(richat, RICHAT_LONGEST[1], RICHAT_LONGEST[0], WIDE)
    assert longest.meta["t_s"] <= 2.95 and longest.seconds <= 8.3, (longest.meta["t_s"], longest.seconds)


def test_m11_panel_foot_opens(photos, richat):
    """M11 (4), 9:16: the INK panel's foot stands at the last myth baseline + 120 before t_f, reaches 1098 by
    t_f + 0.40, and every fact element is hidden while the foot is above its box bottom + 24."""
    plan = richat_myth(richat, RICHAT_LONG, photo=photos["sky"])
    drift = plan.meta["_r"]["drift"]
    a = plan.meta["anchors"]
    t_f = plan.meta["t_f"]
    b0 = a["myth_baselines"][-1] + 120
    for t in (0.0, 1.0, t_f - 0.01):
        assert abs(drift.foot_at(t) - b0) <= 2
    assert abs(drift.foot_at(t_f + 0.40) - 1098) <= 1
    facts = [e for e in plan.elements if e.name.startswith(("fact_", "source"))]
    for i in range(round(t_f * 30) - 2, round((t_f + 0.45) * 30)):
        t = i / 30
        for e in facts:
            if drift.foot_at(t) < e.box[3] + 24:
                assert dc.alpha_at(plan, e.name, t) == 0.0, (e.name, t)


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_m11_kicker_rules(size):
    """M11 (5): the rule's foot to the label's cap top is >= 18 design px on MYTH, FACT and CHAPTER."""
    k = 1.0 if size == TALL else _WideGrid(*size).k
    for plan, names in ((plan_myth(SHORT, size), ("myth", "fact")), (chapter_plan(size), ("kicker",))):
        e = els(plan)
        for n in names:
            assert (e[n + "_label"].box[1] - e[n + "_rule"].box[3]) / k >= 18, (n, e[n + "_label"].box)


# ------------------------------------------------------------------------------------------------ M12
def test_m12_display_host():
    """M12 (1): the display host strips www., m. and a language label, never down to a public suffix."""
    assert dc.display_host("en.wikipedia.org") == "wikipedia.org"
    assert dc.display_host("www.britannica.com") == "britannica.com"
    assert dc.display_host("fr.m.wikipedia.org") == "wikipedia.org"
    assert dc.display_host("bbc.co.uk") == "bbc.co.uk" and dc.display_host("pbs.org") == "pbs.org"
    assert dc.display_host("nasa.gov") == "nasa.gov"


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_m12_source_line(size, photos, richat):
    """M12 (1), (2): 'WIKIPEDIA.ORG' drawn for the signed en.wikipedia.org host (the record keeps the raw host); the
    domain at 38 px and the label at 28; both drawn >= 7.0 on every ground; the long 9:16 card keeps fact >= 76 and
    its lowest ink inside 1058."""
    for g in GROUNDS:
        plan = richat_myth(richat, RICHAT_LONG, size=size, photo=photos[g])
        e = els(plan)
        assert e["source"].text == "WIKIPEDIA.ORG" and plan.meta["source_host_raw"] == "en.wikipedia.org"
        assert e["source"].px == 38 and e["source_label"].px == 28
        last = dc.frame_at(plan, plan.frames - 1)
        c = dict(plan.meta["_contrast"])
        assert c["source"].measure(last) >= 7.0 and c["source_label"].measure(last) >= 7.0, g
        if size == TALL:
            assert plan.meta["anchors"]["px"] >= 76 and max(x.box[3] for x in plan.elements) <= 1058


def test_m12_correction_note(richat, tmp_path):
    """M12 (3): a correction that keeps none of its fact's figures or names gets correction_drops_specifics (in the
    plan and the .design.json); one that keeps '1969' does not."""
    plain = richat_myth(richat, RICHAT_SHORT)
    assert plain.meta["quality_notes"] == ["correction_drops_specifics"]
    # round 4: the long field keeps '1969' (no note); the field that elides it carries the note
    assert richat_myth(richat, RICHAT_LONG_NODATE).meta["quality_notes"] == ["correction_drops_specifics"]
    for dated in (richat_myth(richat, RICHAT_LONG), richat_myth(richat, RICHAT_LONG, size=WIDE)):
        assert dated.meta["quality_notes"] == [] and "1969" in " ".join(lines_of(dated, "fact"))
    dated = richat_myth(richat, RICHAT_1969)
    assert dated.meta["quality_notes"] == []
    assert dc._design_record(plain, "x.jpg")["quality_notes"] == ["correction_drops_specifics"]
    assert "1969" in " ".join(lines_of(dated, "fact"))


def test_m12_evidence_fields_use_only_fact_words(richat):
    """M12 (4): every richat evidence field's words appear in fact-9's statement or evidence quotes (fact-1 gives the
    subject 'The Richat Structure is')."""
    f9 = dc.fact_by_id(richat, "fact-9")
    f1 = dc.fact_by_id(richat, "fact-1")
    pool = " ".join([f9["statement"], f1["statement"]] + [e["quote"] for e in f9["evidence"] + f1["evidence"]])
    words = set(re.findall(r"[a-z0-9]+", pool.casefold()))
    for t in (RICHAT_MYTH, RICHAT_SHORT, RICHAT_1969, RICHAT_LONG, RICHAT_LONG_NODATE) + RICHAT_LONGEST:
        assert set(re.findall(r"[a-z0-9]+", t.casefold())) <= words, t


# ------------------------------------------------------------------------------------------------ round 2
def weak_end(lines):
    return any(ln.split()[-1].lower().strip(".,;:") in dc.WEAK_LINE_END for ln in lines[:-1])


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_r2_myth_fit_steps_down_for_a_weak_end(size, richat):
    """Round 2 (review): a myth fit with a weak line end takes the first smaller size, at most MYTH_WEAK_STEP down,
    whose lines have fewer: the 16:9 1969 fact no longer reads 'THE IMPACT / THEORY WAS / DISPROVED IN 1969', and the
    9:16 long fact no longer ends a line on WHEN."""
    dated = richat_myth(richat, RICHAT_1969, size=size)
    fl = lines_of(dated, "fact")
    assert fl == ["THE IMPACT THEORY", "WAS DISPROVED", "IN 1969"], fl
    assert dated.meta["anchors"]["px"] >= (120 if size == WIDE else 96)
    long_ = richat_myth(richat, RICHAT_LONG, size=size)
    for p in (dated, long_):     # the fact; the 16:9 myth keeps W2's operator-chosen 'STRUCTURE IS' break
        assert not weak_end(lines_of(p, "fact")), lines_of(p, "fact")
    assert dc.MYTH_WEAK_STEP == 24


def test_r2_evidence_corrections_keep_the_finding(richat):
    """Round 2 (review, fact meaning): fact-9 says barite had been misidentified as coesite. A field that keeps
    'coesite ... identified as ... barite' keeps 'suspected' before 'coesite' (or 'mislabeled' before 'barite'), so the
    card never says coesite proved to be barite; each is an elision of fact-9 ground_text accepts."""
    for c in (RICHAT_LONG, RICHAT_LONG_NODATE, RICHAT_LONGEST[1]):
        low = c.casefold()
        if "coesite" in low and "barite" in low:
            assert "suspected coesite" in low or "mislabeled barite" in low, c
        assert dc.ground_text(c, copy.deepcopy(richat), ["fact-9"], role="fact")
        assert len(c) <= 84


def test_r2_c5_total_reads_as_its_label_on_the_real_ground(richat):
    """Round 2 (review: 9.68 against 9.85 on act 2 busy): on the Richat run's busiest photo (05_commons.jpg) the
    16:9 total's contrast is within 0.1 of the CHAPTER label's, planned and drawn on the last frame."""
    photo = ROOT / "assets" / "richat_structure_sahara_eye" / "05_commons.jpg"
    if not photo.is_file():
        pytest.skip("the run's photo is not in this checkout")
    sh = copy.deepcopy(richat)
    plan = dc.plan_chapter_card(dc.ChapterCard(number=2, total=ACTS, title=ACT2, photo=photo,
                                               title_src=T.gate(ACT2, sh)), sh, *WIDE)
    c = dict(plan.meta["_contrast"])
    assert abs(c["total"].contrast - c["kicker_label"].contrast) <= 0.1
    last = dc.frame_at(plan, plan.frames - 1)
    assert abs(c["total"].measure(last) - c["kicker_label"].measure(last)) <= 0.1
    assert min(c["total"].contrast, c["kicker_label"].contrast) >= 7.0


# ------------------------------------------------------------------------------------------------ round 4
def test_r4_cohesive_fact_wrap(richat):
    """Round 4 (review: 'WHEN SUSPECTED / COESITE WAS IDENTIFIED'): the myth card's fact breaks before a function word
    rather than between two content words where the measure allows; the other wrappers are unchanged."""
    assert dc._parts_phrase("SUSPECTED", "COESITE") and not dc._parts_phrase("COESITE", "WAS")
    assert not dc._parts_phrase("DISPROVED:", "SUSPECTED") and not dc._parts_phrase("WHEN", "SUSPECTED")
    for size in (TALL, WIDE):
        for c in (RICHAT_LONG, RICHAT_LONG_NODATE):
            fl = lines_of(richat_myth(richat, c, size=size), "fact")
            assert not any(dc._parts_phrase(a.split()[-1], b.split()[0]) for a, b in zip(fl, fl[1:])), (size, fl)


def test_r4_tall_long_myth_draws_1969(richat):
    """Round 4 (review: no 9:16 long card with 1969 had a render): the 9:16 richat_long card plans with its source
    kept, fact >= 76, lowest ink <= 1058, and draws 1969; the 57-character myth stays refused on 9:16 (limit 56)."""
    plan = richat_myth(richat, RICHAT_LONG)
    fl = lines_of(plan, "fact")
    assert "1969" in " ".join(fl) and plan.meta["anchors"]["px"] >= 76
    assert els(plan)["source"].text == "WIKIPEDIA.ORG"
    assert max(e.box[3] for e in plan.elements) <= 1058
    with pytest.raises(dc.CardRefused):
        richat_myth(richat, RICHAT_LONGEST[1], RICHAT_LONGEST[0])


def test_r4_chapter_wide_lead_bounds():
    """Round 4 (C5): the 16:9 two-line step is 0.98 P unless the numeral needs more, never past 1.20 P; one-line and
    three-line titles and 9:16 titles keep _lead's step."""
    assert dc.CHAPTER_WIDE_LEAD_MAX == 1.20
    assert dc._chapter_wide_off(["DOCUMENTED PHYSICAL", "ARCHITECTURE"], 152, 311) == [0, 311 - round(0.86 * 152)]
    assert dc._chapter_wide_off(["ORIGIN DEBATES", "AND ROCK DATING"], 176, 311) == [0, round(0.98 * 176)]
    assert dc._chapter_wide_off(["A B", "C D"], 96, 311) == [0, round(1.20 * 96)]
    assert dc._chapter_wide_off(["ONE"], 152, 311) == [0]


# ------------------------------------------------------------------------------------------------ fix round 1
def test_fr1_correction_note_reaches_the_review_card(richat, tmp_path, monkeypatch):
    """Fix round 1 (operator 2026-11-04, M12 (3)): a myth card that drops every specific (correction_drops_specifics,
    in its .design.json) shows that note on the Telegram review card; a card that keeps '1969' adds none. Real run:
    the Richat short run's own verified script and fact sheet (new: real fact, test-signed field)."""
    from src import config
    from src.telegram_bot import TelegramApprovalBot, design_card_notes
    run = "run_richat_structure_sahara_eye_1792737039_short"
    vs = ROOT / "output" / run / "verified_script.json"
    if not vs.is_file():
        pytest.skip("the run's verified script is not in this checkout")
    payload = json.loads(vs.read_text(encoding="utf-8"))
    out = tmp_path / "output"
    (out / run / "graphics").mkdir(parents=True)
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    dated = richat_myth(richat, RICHAT_LONG)
    (out / run / "graphics" / "graphic_mythfact_01.mp4.design.json").write_text(
        json.dumps(dc._design_record(dated, "last")), encoding="utf-8")
    assert design_card_notes(out / run) == []
    assert "Design cards" not in TelegramApprovalBot.format_card_text(payload, run)
    plain = richat_myth(richat, RICHAT_LONG_NODATE)
    (out / run / "graphics" / "graphic_mythfact_02.mp4.design.json").write_text(
        json.dumps(dc._design_record(plain, "last")), encoding="utf-8")
    (out / run / "graphics" / "graphic_mythfact_03.mp4.design.json").write_text(
        json.dumps({"kind": "mythfact", "refused": "no_fit", "quality_notes": ["correction_drops_specifics"]}),
        encoding="utf-8")
    notes = design_card_notes(out / run)
    assert notes == ["graphic_mythfact_02: the correction keeps none of its fact's figures, dates or names"]
    card = TelegramApprovalBot.format_card_text(payload, run)
    assert "Design cards: 1 note(s)" in card and "graphic_mythfact_02" in card
    assert "figures, dates or names" in card and "graphic_mythfact_03" not in card
    # a run id that leaves the output folder reads nothing
    assert "Design cards" not in TelegramApprovalBot.format_card_text(payload, "../" + run)
