"""Design freeze cycle 5, TEXT stream, review D3: the ONE breaking policy on the surfaces beyond carousel, Story and
cover text, measured through each renderer's own layout call on committed real copy, so the class cannot reopen
unseen: the chapter and myth cards (the committed card fields the board draws), the end card and end screen (the
runs' share lines), the number cards' labels (the runs' cards), and the corpus judge itself
(docs/reviews/freeze-system-2026-11-15/corpus/text_lines.py).

A violation the policy leaves is allowed only when the surface's own PERSISTED record names it (a card's design
record, the end card's layout / stats): a note no file holds is no record."""
import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, end_card, graphics, line_policy
from src import design_cards as dc
from src.typography import get_font

import tests.test_design_cards_2026_11_03 as T
from tests import test_freeze_cards_2026_11_04 as fc

pytestmark = pytest.mark.usefixtures("card_last_guard")

ROOT = Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests" / "fixtures" / "text_c5" / "surfaces_c5b.json").read_text(encoding="utf-8"))
TALL, WIDE = (1080, 1920), (1920, 1080)
HARD = (line_policy.NEVER_END, line_policy.UNIT_SPLIT)


def recorded(record, lines) -> set:
    """The fault names a persisted line_faults record carries for exactly these drawn lines."""
    return {f["fault"] for r in record or [] if r.get("lines") == list(lines) for f in r["faults"]}


def left(lines) -> set:
    return {f["fault"] for f in line_policy.violations(list(lines))}


# ------------------------------------------------------------------------------------------------------ cards
def chapter(n: int, size):
    spec = T.chapter(sheet=fc.SHEET, number=n, total=len(fc.RICHAT_ACTS), title=fc.RICHAT_ACTS[n - 1])
    spec = dataclasses.replace(spec, set_titles=tuple(fc.RICHAT_ACTS))     # as card_composition.make_all passes them
    return dc.plan_chapter_card(spec, fc.SHEET, *size)


def field_lines(plan, field: str) -> list:
    keys = [k for k in plan.drawn_text if k.startswith(field + "_") and k[len(field) + 1:].isdigit()]
    return [plan.drawn_text[k] for k in sorted(keys, key=lambda k: int(k[len(field) + 1:]))]


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_chapter_set_line_ends_are_on_the_design_record(size):
    """Every chapter title of the video's set: a line end the one policy counts is in the card's design record
    (the file beside the clip), and a clean card writes no such key."""
    for n in range(1, len(fc.RICHAT_ACTS) + 1):
        plan = chapter(n, size)
        lines = field_lines(plan, "title")
        rec = dc._design_record(plan, "poster.jpg")
        assert left(lines) <= recorded(rec.get("line_faults"), lines), (n, lines, rec.get("line_faults"))
        assert ("line_faults" in rec) == bool(left(lines)), (n, lines)


def test_chapter_act_4_tall_is_the_recorded_last_resort():
    """Panel 6 TEXT-01: ARCHAEOLOGICAL / EVIDENCE AND / REFUTED CLAIMS has no legal break on the declared ladder; it
    stays at the set's size, and the AND end is on the persisted record (it was an in-memory anchor only)."""
    plan = chapter(4, TALL)
    lines = field_lines(plan, "title")
    assert lines[1].split()[-1] == "AND", lines
    rec = dc._design_record(plan, "poster.jpg")["line_faults"]
    assert [r["field"] for r in rec] == ["title"]
    assert recorded(rec, lines) == {line_policy.FUNCTION_END}
    assert plan.meta["anchors"]["title_px"] >= 84           # never under the chapter ladder's floor to cure it


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_myth_card_line_ends_are_on_the_design_record(size, richat_sheet):
    plan = fc.richat_myth(richat_sheet, size=size)
    rec = dc._design_record(plan, "poster.jpg")
    for field in ("myth", "fact"):
        lines = field_lines(plan, field)
        assert left(lines) <= recorded(rec.get("line_faults"), lines), (field, lines)
    if size == TALL:
        # panel 6 TEXT-01: IMPACT THEORY / DISPROVED WHEN / ...: the 9:16 myth card had no record of it at all
        fact = field_lines(plan, "fact")
        assert any(ln.split()[-1] == "WHEN" for ln in fact[:-1]), fact
        assert line_policy.FUNCTION_END in recorded(rec["line_faults"], fact)
        assert plan.meta["anchors"]["px"] >= 76             # the fact's floor holds: no size under it for a line end


@pytest.fixture(scope="module")
def richat_sheet():
    return fc.real_sheet("richat_structure_sahara_eye")


def test_line_faults_reads_the_drawn_fields_only():
    """The record is computed from the plan's drawn rows, field by field: labels and one-row fields name nothing."""
    plan = dataclasses.make_dataclass("P", ["drawn_text"])(
        {"title_0": "EVIDENCE AND", "title_1": "REFUTED CLAIMS", "kicker_label": "CHAPTER", "numeral": "4",
         "fact_0": "ONE ROW ONLY AND"})
    got = dc.line_faults(plan)
    assert [(r["field"], r["lines"]) for r in got] == [("title", ["EVIDENCE AND", "REFUTED CLAIMS"])]
    assert [f["fault"] for f in got[0]["faults"]] == [line_policy.FUNCTION_END]
    clean = dataclasses.make_dataclass("P", ["drawn_text"])({"title_0": "ORIGIN DEBATES", "title_1": "AND ROCK DATING"})
    assert dc.line_faults(clean) == []


# --------------------------------------------------------------------------------------------------- end card
def share_rows(lay) -> list:
    return [" ".join(ln) for ln in lay["lines"]]


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_share_lines_hold_the_policy_or_record_it(size):
    """Every real share line on the end card (9:16) and the end screen (16:9): what the policy still counts is in the
    layout's own record, and only where no declared size of the line's ladder sets the same number of rows clean
    without a one-word row."""
    sizes = end_card.LANDSCAPE_SHARE_SIZES if size[0] > size[1] else end_card.SHARE_SIZES
    for share in FIX["share_lines"]:
        lay = end_card.layout(share, *size)
        rows = share_rows(lay)
        assert " ".join(rows) == share
        assert left(rows) <= recorded(lay["line_faults"], rows), (share, rows)
        assert bool(lay["line_faults"]) == bool(left(rows)), (share, rows)
        if not left(rows) or lay["share_font"].size not in sizes:
            continue
        for px in sizes:
            font = get_font(brand.TEXT_FONT, px, weight=end_card.SHARE_WEIGHT)
            got = end_card._balanced(share.split(), font, lay["max_width"], len(rows))
            if got and len(got) == len(rows):
                assert left(" ".join(ln) for ln in got) or any(len(ln) == 1 for ln in got), (share, px, got)


def test_share_line_takes_a_smaller_ladder_size_before_a_function_word_end():
    """"Share this / with a friend who / loves wartime history." (58 px, two last-resort ends) sets clean two px
    down its own ladder; never under the ladder's last size."""
    lay = end_card.layout("Share this with a friend who loves wartime history.", *TALL)
    assert share_rows(lay) == ["Share this with a friend", "who loves", "wartime history."]
    assert lay["share_font"].size in end_card.SHARE_SIZES and lay["line_faults"] == []


@pytest.mark.parametrize("size", [TALL, WIDE])
def test_share_line_never_buys_a_line_end_with_a_one_word_row(size):
    """With the wider vocabulary the engine's own order set "Send / this to a friend fascinated / ...": a one-word
    row is weighed before a last-resort end; the end stays and is recorded."""
    lay = end_card.layout("Send this to a friend fascinated by ancient engineering.", *size)
    rows = share_rows(lay)
    assert rows == ["Send this", "to a friend fascinated", "by ancient engineering."]
    assert recorded(lay["line_faults"], rows) == {line_policy.FUNCTION_END}


def test_share_line_faults_names_the_drawn_rows():
    got = end_card.share_line_faults([["Send", "this", "to", "a", "friend", "who"], ["loves", "geology."]])
    assert got[0]["lines"] == ["Send this to a friend who", "loves geology."]
    assert [f["word"] for f in got[0]["faults"]] == ["who"]
    assert end_card.share_line_faults([["Send", "this", "to", "a", "friend"], ["who", "loves", "geology."]]) == []
    assert end_card.share_line_faults([["One", "row."]]) == []


# ------------------------------------------------------------------------------------------- number card labels
def tall_stat_plan(card):
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    w, h = TALL
    text_w = graphics.get_text_box(w, h)[2] - graphics.inset_x0(w, h) - 4
    floor = graphics.label_floor(w, h)
    return graphics._stat_layout(probe, dict(card), 230, text_w, 1.0, word_floor=floor), floor


def test_number_card_labels_hold_the_hard_rules():
    """Every real number card of the runs, as the 9:16 renderer plans it: no label row ends on a never-end word or
    cuts a keep-together group, and no label is under its floor for it."""
    for card in FIX["stat_cards"]:
        plan, floor = tall_stat_plan(card)
        rows = [r for r in plan["rows"] if r["name"] == "label"]
        lines = [r["text"] for r in rows]
        assert " ".join(lines) == " ".join(card["label"].upper().split()), card
        assert not (left(lines) & set(HARD)), (card, lines)
        assert all(r["font"].size >= floor for r in rows), (card, lines)


def test_number_card_label_steps_down_only_for_a_hard_fault():
    font = get_font(brand.TEXT_FONT, 46, weight=800)
    clean = ["WEIGHT OF AMBER"], ["DISASSEMBLY", "TIME IN 1941"]
    for lines in clean:
        assert graphics.stat_label_policy(" ".join(lines), list(lines), font, 600, 3, 34) == (list(lines), font)
    # a last-resort end is no reason for a smaller size
    soft = ["CHAMBER DISASSEMBLED AND", "SHIPPED"]
    assert graphics.stat_label_policy(" ".join(soft), list(soft), font, 900, 3, 34) == (soft, font)
    # the real case: COOLING OF / CARBONATITE ROCKS on a measure where the clean break needs a smaller size
    text = "COOLING OF CARBONATITE ROCKS"
    need = graphics._tracked_w("OF CARBONATITE ROCKS", font, 3)
    steps = [get_font(brand.TEXT_FONT, px, weight=800) for px in range(44, 33, -graphics.STAT_LABEL_STEP)]
    got, f2 = graphics.stat_label_policy(text, ["COOLING OF", "CARBONATITE ROCKS"], font, need - 30, 3, 34, steps)
    assert got == ["COOLING", "OF CARBONATITE ROCKS"] and 34 <= f2.size < 46
    assert graphics._tracked_w(got[1], f2, 3) <= need - 30
    # and where no size down to the floor clears it, the rows stay as fitted (logged), never under the floor
    kept, f3 = graphics.stat_label_policy(text, ["COOLING OF", "CARBONATITE ROCKS"], font, need - 30, 3, 46, steps)
    assert (kept, f3) == (["COOLING OF", "CARBONATITE ROCKS"], font)


# ------------------------------------------------------------------------------------------------ the corpus judge
@pytest.fixture(scope="module")
def judge():
    path = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus" / "text_lines.py"
    spec = importlib.util.spec_from_file_location("text_lines_c5b", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_judge_passes_only_what_the_surface_recorded(judge):
    lines = ["IMPACT THEORY", "DISPROVED WHEN", "SUSPECTED COESITE"]
    fault = [{"fault": "function_word_end", "line": 1, "word": "when"}]
    rows = [
        {"surface": "card_myth_richat_long_916_fact", "text": " ".join(lines), "lines": lines, "heading": True,
         "record": {}},                                                               # nothing recorded: fails
        {"surface": "card_myth_richat_long_916_fact", "text": " ".join(lines), "lines": lines, "heading": True,
         "record": {"line_faults": [{"field": "fact", "lines": lines, "faults": fault}]}},      # recorded: passes
        {"surface": "card_myth_richat_long_916_fact", "text": " ".join(lines), "lines": lines, "heading": True,
         "record": {"line_faults": [{"field": "fact", "lines": ["OTHER", "LINES"], "faults": fault}]}},   # a trial's
        {"surface": "card_myth_richat_long_916_fact", "text": " ".join(lines), "lines": lines, "heading": True,
         "record": {"in_memory_only": ["conjunction_end_last_resort"]}},              # a note no file holds
        {"surface": "endcard_916_share", "error": "RuntimeError: the layout call failed"},
        {"surface": "graphic_scale_916", "text": "scale", "refused": True},
    ]
    table, tot, fam = judge.judge({"runs": {"run_x": {"lines": rows}}})
    assert [r[3] for r in table if "function_word_end" in r[2]] == ["FAIL", "pass (recorded)", "FAIL", "FAIL"]
    assert tot["TEXT-01 UNRECORDED"] == 3 and tot["TEXT-01 recorded"] == 1
    assert tot["collector errors"] == 1 and tot["refused (recorded by the renderer)"] == 1
    assert fam["card_myth_916"]["function_word_end"] == 4 and fam["endcard_916_share"]["errors"] == 1


def test_judge_story_title_is_two_authored_fields(judge):
    """The field seam is a line break like any other (a function word before it counts); a one-word second FIELD is
    the copy's payoff field, not a lone last row the engine chose."""
    seam = {"surface": "story_03_title", "lines": ["CONTAIN OR", "COUNTERACT?"], "fields": [1, 1], "heading": True}
    assert [f["fault"] for f in judge.row_faults(seam)] == ["function_word_end"]
    payoff = {"surface": "story_02_title", "lines": ["WOODEN STAKES", "FOUND"], "fields": [1, 1], "heading": True}
    assert judge.row_faults(payoff) == []
    broken = {"surface": "story_01_title", "lines": ["A VERY LONG FIRST FIELD THAT", "BREAKS", "PAYOFF"],
              "fields": [2, 1], "heading": True}
    assert "lone_last_row" in [f["fault"] for f in judge.row_faults(broken)]


def test_judge_families(judge):
    assert judge.family("carousel_s05_body") == "carousel_body"
    assert judge.family("story_02_title") == "story_title"
    assert judge.family("card_chapter_act4_916_title") == "card_chapter_916"
    assert judge.family("card_myth_richat_long_169_fact") == "card_myth_169"
    assert judge.family("graphic_timeline_916_label2") == "graphic_timeline_916"
    assert judge.family("graphic_route_stop2_169") == "graphic_route_169"
    assert judge.family("endcard_169_share") == "endcard_169_share"


# ---------------------------------------------------------------------------------------------------- Reel cover
def test_cover_lockup_line_faults_are_the_drawn_rows_only():
    """The Reel cover's lockup (real covers: DRAWN WITHOUT / EVER FLYING, an authored field seam after a last-resort
    word): the fault is written under "line_faults", the key still_layout.json keeps; a later fit of the same layout
    replaces it (never a trial fit's rows on the record), other roles' records stay."""
    from src import cover_design, still_layout
    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = get_font(brand.DISPLAY_FONT, 104)
    faulty = {"line1": (font, [[("DRAWN WITHOUT", "bone")]]), "line2": (font, [[("EVER FLYING", "gold")]])}
    clean = {"line1": (font, [[("DRAWN", "bone")]]), "line2": (font, [[("WITHOUT EVER FLYING", "gold")]])}
    body = {"fault": "measure_over", "line": 0, "text": "x", "role": "body"}
    record = {"line_faults": [dict(body)]}
    cover_design.lockup_line_faults(record, measure, faulty)
    title = [f for f in record["line_faults"] if f.get("role") == "title"]
    assert [(f["fault"], f["word"], f["text"]) for f in title] == [("function_word_end", "without", "DRAWN WITHOUT")]
    assert body in record["line_faults"]
    lay = still_layout.Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    lay.meta.update(record)
    assert still_layout.text_records(lay)["line_faults"] == record["line_faults"]     # what the sidecar keeps
    cover_design.lockup_line_faults(record, measure, clean)
    assert record["line_faults"] == [body]
    only = {}
    cover_design.lockup_line_faults(only, measure, faulty)
    cover_design.lockup_line_faults(only, measure, clean)
    assert "line_faults" not in only
