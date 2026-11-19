"""Design freeze lane F, cycle 1 (hook, chapter and myth cards, callouts, badge): structural invariants.

The hook's plate and wash leave with its type (panel 2 MO, clips\\hook__obelisk__916 f037-f039: an empty INK band
stood across the photo for three frames after the type had gone)."""

from __future__ import annotations

import numpy as np
import pytest

from src import brand
from src import design_cards as dc
from src.brand_system import SPEC
from src.config import VIDEO_FPS

import tests.test_freeze_cards_2026_11_04 as F

FRAMES = range(round(dc.HOOK_SECONDS * VIDEO_FPS) + 1)


def test_hook_motion_equals_the_written_tokens():
    """Until SYS-F-1 makes them runtime tokens, the module values stay equal to the written SPEC ones."""
    assert dc.HOOK_HOLD == SPEC["motion.hook_hold"] and dc.HOOK_FADE == SPEC["motion.hook_fade"]


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
    """Measured on the pixels: on a frame whose type is gone, the plate band departs from the bare pushed photo by at
    most HOOK_EMPTY_MAX of the full plate's departure (frame 0), so no empty band is ever on screen."""
    plan = F.hook_plan(photos[ground], "Quartzite ridges form the ring", "f_geo")
    if plan.meta["scrim"]["mode"] != "plate":
        pytest.skip("wash ground: no plate band")
    y0, y1 = plan.meta["scrim"]["plate"]
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
