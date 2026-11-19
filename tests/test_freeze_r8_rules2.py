"""Cycle 0 round 8, pre-lane system batch SYS-R8-2 (queue\\CYCLE0.r8-AD-regression.md R1, R2, F4, F5), closed by class:

- R1: one overlay-label treatment per SET (typography.plan_label_set / set_step): a deck's kickers, a Story's, a
  then-and-now pair, an end screen's labels take the least intrusive step that holds the gate on every member's
  ground; the gates are never traded for consistency.
- R2: the YouTube lockup's stacked rows obey the heading rules (heading.lockup_valid): no one-word middle row
  (THE 1,200-TON / BEDROCK / MONSTER); the larger size where a legal form sets, else the largest legal size, never
  under the feed floor.
- F4: one never-end set (brand_system.NEVER_END_WORDS, the derived closed class) read by both burst paths and every
  caption record (video_composer.never_end_end).
- F5: every renderer that colours a figure draws the one gold scope (figures.gold_runs / gold_word_indices).
"""
import ast
import inspect
import json
import textwrap
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, end_card, heading, multi_format, then_now, typography
from src import video_composer as vc
from src.brand_system import GATES, tok

ROOT = Path(__file__).resolve().parents[1]


def _stripes(size=(900, 300), period=6):
    arr = np.zeros((size[1], size[0], 3), np.uint8)
    for k in range(period // 2):
        arr[k::period] = 255
    return Image.fromarray(arr)


DARK = Image.new("RGB", (900, 300), (22, 24, 28))
BRIGHT = Image.new("RGB", (900, 300), (250, 250, 246))


def _kicker_member(ground, text="THE UNFINISHED OBELISK"):
    font = typography.get_font(brand.TEXT_FONT, 40, weight=tok("kicker.weight"))
    return multi_format.kicker_label(ground, (150, 160), text, font, brand.GOLD, "ls", 6, (100, 140, 127, 145),
                                     GATES["contrast.kicker"])


def _measured(plates, ground):
    out = ground
    for p in plates:
        out = p.draw(out)
    return min(p.measure(out) for p in plates if not p.empty)


# ------------------------------------------------------------------------------------------------ R1: one step a set
@pytest.mark.parametrize("grounds", [(DARK, BRIGHT), (DARK, _stripes()), (BRIGHT, _stripes()), (DARK, BRIGHT, _stripes())])
def test_a_set_of_labels_takes_one_step_that_holds_on_every_members_ground(grounds):
    members = [_kicker_member(g) for g in grounds]
    own = [typography.plan_label(**m)[1]["step"] for m in members]
    results, rec = typography.plan_label_set(members)
    steps = {tuple(r["step"]) for _, r in results}
    assert rec["uniform"] and len(steps) == 1, (own, [r["step"] for _, r in results])
    ladder = typography.label_ladder((0, 1))
    assert ladder.index(next(iter(steps))) >= max(ladder.index(tuple(s)) for s in own)   # never below a member's own
    for (plates, r), g in zip(results, grounds):   # the gate on every member's own ground, drawn
        assert r["shown"] and _measured(plates, g) >= GATES["contrast.kicker"] - 1e-6


def test_the_set_keeps_colour_where_it_holds_for_every_member():
    results, rec = typography.plan_label_set([_kicker_member(DARK), _kicker_member(DARK.copy())])
    assert rec["step"] == ["halo", 0] and all(tuple(p.fill) == tuple(brand.GOLD) for ps, _ in results for p in ps)


def test_a_member_that_may_not_take_ink_type_keeps_the_set_off_it():
    a, b = _kicker_member(BRIGHT), _kicker_member(BRIGHT.copy())
    b["ink"] = False
    results, rec = typography.plan_label_set([a, b])
    assert rec["uniform"] and rec["step"][0] != "ink"


def test_set_step_is_the_least_intrusive_common_step_or_none():
    ladder = typography.label_ladder((0, 1))
    assert typography.set_step(ladder, [[("halo", 0), ("plate", 0)], [("ink", -1), ("plate", 0)]]) == ("plate", 0)
    assert typography.set_step(ladder, [[("halo", 1)], [("ink", -1)]]) is None


def test_plan_label_only_tries_the_one_step():
    m = _kicker_member(DARK)
    _, rec = typography.plan_label(**m, only=("plate", 0))
    assert rec["shown"] and rec["step"] == ["plate", 0] and rec["variant"] == "plate"


def test_a_deck_draws_every_kicker_in_one_treatment(tmp_path):
    """The Aswan deck drew its kicker gold on a plate (s4), bone (s6) and bare gold elsewhere: one deck, three
    treatments. A deck of a near-white, a dark and a striped photograph now draws one."""
    photos = []
    for i, kind in enumerate(("bright", "dark", "stripes", "dark")):
        p = tmp_path / f"0{i}.jpg"
        im = {"bright": Image.new("RGB", (1200, 1500), (250, 250, 246)), "dark": Image.new("RGB", (1200, 1500), (20, 22, 26)),
              "stripes": _stripes((1200, 1500), 4)}[kind]
        im.save(p)
        photos.append(p)
    cards = [{"heading": "The dome", "body": "Built in concrete."}, {"heading": "The mix", "body": "Lime clasts."},
             {"heading": "The pour", "body": "Layer by layer."}, {"heading": "Your view", "body": "Which mattered more?"}]
    slides = multi_format.MultiFormatGenerator().generate_carousel_sequence(
        photos, {"carousel_slides": cards, "title": "T", "poll": {}}, tmp_path)
    rec = json.loads((tmp_path / "carousel" / "still_layout.json").read_text(encoding="utf-8"))
    kickers = [e for s in slides for e in rec[s.name]["elements"] if e["name"] == "kicker"]
    assert len(kickers) >= 3
    assert len({(k["variant"], tuple(k["step"]), tuple(k["fill"])) for k in kickers}) == 1, kickers
    assert all(k["contrast"] >= GATES["contrast.kicker"] - 1e-6 for k in kickers)
    steps = {tuple(rec[s.name]["recipe"].get("kicker_step") or ()) for s in slides}
    assert len(steps) == 1 and steps != {()}                  # a redraw keeps the deck's step (redraw_carousel_slide)


def test_a_kicker_forced_off_its_ground_falls_back_and_says_so():
    font = typography.get_font(brand.TEXT_FONT, 40, weight=tok("kicker.weight"))
    with multi_format.kicker_set(("halo", 0)):
        plate, rec = multi_format.plan_kicker(BRIGHT, (150, 160), "WATCH NEXT", font, brand.GOLD, "ls", 6,
                                              rule=(100, 140, 127, 145))
    assert rec["shown"] and rec["set_broken"] == ["halo", 0] and rec["step"] != ["halo", 0]
    assert plate.measure(plate.draw(BRIGHT)) >= GATES["contrast.kicker"] - 1e-6
    assert getattr(multi_format._render_state, "kicker_step", None) is None   # restored on exit


# The guard: every renderer that draws a SET of overlay labels plans it through the set planner.
SET_RENDERERS = [(then_now.render, "plan_label_set"), (end_card.frames, "plan_label_set"),
                 (multi_format.MultiFormatGenerator.generate_carousel_sequence, "deck_kicker_step"),
                 (multi_format.MultiFormatGenerator.generate_story_sequence, "deck_kicker_step"),
                 (multi_format.redraw_carousel_slide, "kicker_set"), (multi_format.redraw_story_frame, "kicker_set")]


@pytest.mark.parametrize("fn, planner", SET_RENDERERS, ids=lambda x: getattr(x, "__qualname__", x))
def test_every_set_renderer_goes_through_the_set_planner(fn, planner):
    src = textwrap.dedent(inspect.getsource(fn))
    names = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(ast.parse(src))
             if isinstance(c, ast.Call)}
    assert planner in names
    if planner == "plan_label_set":
        assert "plan_label" not in names and "plan_kicker" not in names   # no member planned on its own


# ------------------------------------------------------------------------------------------------ R2: YouTube rows
def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def test_a_stacked_lockup_never_leaves_a_one_word_middle_row():
    assert not heading.lockup_valid(["THE 1,200-TON", "BEDROCK", "MONSTER"], seam=2, fields=True)
    assert not heading.lockup_valid(["DRAWN", "WITHOUT", "EVER FLYING"], seam=2, fields=True)
    assert heading.lockup_valid(["THE 1,200-TON", "BEDROCK MONSTER"], seam=2, fields=True)
    # the authored field boundary frees a row of the bonded-unit reading, never of the never-end rule (SYS-B-1:
    # CARVED FROM / SOLID SALT, a row ending on FROM, is invalid as fields and as one heading)
    assert not heading.lockup_valid(["CARVED FROM", "SOLID SALT"], seam=2, fields=True)
    assert not heading.lockup_valid(["CARVED FROM", "SOLID SALT"])
    assert heading.lockup_valid(["CARVED", "FROM SOLID SALT"], seam=1, fields=True)   # the ruled seam (lockup_fields)


def test_the_aswan_headline_holds_no_form_at_its_floor_and_is_refused():
    """AD R2: THE 1,200-TON / BEDROCK / MONSTER (two lone rows). At the hero's feed floor (160) no legal form of
    904 px sets: a recorded refusal (cover_fit routes the copy to its edit), never a lone middle row."""
    from src import youtube_thumbnail as yt
    from src.refusal import LayoutError
    for kickerless in (True, False):
        with pytest.raises(LayoutError):
            yt._lockup(_draw(), "THE 1,200-TON", "BEDROCK MONSTER", 904, kickerless=kickerless)


def test_the_largest_legal_size_wins_over_a_larger_illegal_form():
    from src import youtube_thumbnail as yt
    rows, f1, f2, rec = yt._lockup(_draw(), "THE SINGING", "720-TON TITAN", 904, kickerless=True)
    assert [" ".join(t for _, t, _ in r) for _, r in rows] == ["THE SINGING", "720-TON TITAN"]
    assert rec["sizes"] == [160, 160]    # 190 set it only as THE SINGING / 720-TON / TITAN


REAL_COPIES = [("830 SQUARE MILES", "FLATTENED FOREST"), ("CARVED DEEP", "UNDERGROUND"), ("CARVED FROM", "SOLID SALT"),
               ("CARVED FROM", "THE TOP DOWN"), ("DRAWN WITHOUT", "EVER FLYING"), ("EGYPT'S LARGEST", "CRACKED OBELISK"),
               ("ESTIMATED 6 TONNES", "GONE IN 36 HOURS"), ("ETCHED IN DUST", "UNCHANGED FOR AGES"),
               ("MASSIVE DESERT", "SAHARA BULLSEYE"), ("PREHISTORIC SKULL", "SURGERY SURVIVORS"),
               ("THE 1,200-TON", "BEDROCK MONSTER"), ("THE 144-FOOT", "WHISPERING DOME"), ("THE 25-MILE", "SAHARA EYE"),
               ("THE 25-MILE EYE", "SEEN FROM SPACE"), ("THE 6-TON ROOM", "STOLEN IN 36 HOURS"), ("THE EYE OF", "THE SAHARA"),
               ("THE LOST ROOM", "OF CARVED AMBER"), ("THE SECRET LABYRINTH", "DEEP BENEATH ODESA"),
               ("THE SECRET OF", "DAMASCUS STEEL"), ("THE SECRET WEAPON", "WATER COULD NOT STOP"),
               ("THE SINGING", "720-TON TITAN"), ("THE UNBROKEN", "VOYNICH CODE"), ("THE VANISHED", "AMBER ROOM"),
               ("TRAPPED IN BEDROCK", "NEVER COMPLETED"), ("WHY ROMAN CONCRETE", "HEALS ITSELF")]


@pytest.mark.parametrize("l1, l2", REAL_COPIES)
def test_a_field_boundary_never_exempts_a_row_from_the_never_end_rule(l1, l2):
    """SYS-B-1: every stacking of the copy's words in two or three rows, at either seam (authored or ruled), that
    lockup_valid(fields=True) accepts ends every row but the last on a word that may end a line."""
    import itertools
    r1, _, _ = heading.lockup_fields(l1, l2)
    words = (l1 + " " + l2).split()
    for seam in {len(l1.split()), len(r1.split())}:
        for n in (1, 2):
            for cuts in itertools.combinations(range(1, len(words)), n):
                rows = heading.lines_of(words, cuts)
                if heading.lockup_valid(rows, seam=seam, fields=True):
                    assert all(heading.end_ok(r.split()[-1]) for r in rows[:-1]), (rows, seam)


@pytest.mark.parametrize("l1, l2", REAL_COPIES)
def test_every_real_youtube_headline_sets_a_legal_form_at_or_above_its_floor_or_is_refused(l1, l2):
    """The stress copy set: every thumbnail line pair the dev runs signed, with its line 2 and without (the budget
    drops it), kickered and kickerless."""
    from src import youtube_thumbnail as yt
    from src.refusal import LayoutError
    floor = yt.feed_floor_px(yt.HERO_FLOOR)
    for second in (l2, ""):
        for kickerless in (True, False):
            try:
                rows, f1, f2, rec = yt._lockup(_draw(), l1, second, 904, kickerless=kickerless)
            except LayoutError:
                continue
            lines = [" ".join(t for _, t, _ in r) for _, r in rows]
            sizes = [f1.size if k == "title_1" else f2.size for k, _ in rows]
            widths = [_draw().textlength(t, font=typography.get_font(brand.DISPLAY_FONT, s)) for t, s in zip(lines, sizes)]
            seam = len(rec["line1"].split()) if rec["line1"] and rec["line2"] else None
            assert min(rec["sizes"]) >= floor and len(lines) <= 3 and max(widths) <= 904 + 1e-6
            if seam is not None:
                assert heading.lockup_valid(lines, widths, seam, fields=True), lines
            else:
                assert heading.lockup_valid(lines, widths, heading.ideal_seam(tuple(" ".join(lines).split()))), lines


# ------------------------------------------------------------------------------------------------ F4: one never-end set
def test_the_never_end_set_is_the_derived_closed_class_and_every_caption_set_holds_it():
    data = json.loads((ROOT / "data" / "never_end_words.json").read_text(encoding="utf-8"))
    assert set(data["classes"]) == {"preposition", "determiner", "quantifier", "conjunction", "ruled"}
    assert brand_system.NEVER_END_WORDS == frozenset(data["words"]) | brand_system.LINE_END_NEVER
    assert {"across", "along", "upon", "alongside", "most", "both"} <= brand_system.NEVER_END_WORDS
    assert brand_system.LINE_END_NEVER <= brand_system.NEVER_END_WORDS <= vc.NEVER_END and vc.DANGLING_ENDS <= vc.NEVER_END
    assert all(vc.never_end_end(w) for w in brand_system.NEVER_END_WORDS)
    assert not vc.never_end_end("across,") and not vc.never_end_end("both.")   # punctuation closes it


def test_the_committed_never_end_set_is_the_derivation():
    import subprocess
    import sys
    got = subprocess.run([sys.executable, str(ROOT / "scripts" / "derive_never_end.py"), "--check"], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    assert got.returncode == 0, got.stdout + got.stderr


def _timed(text: str):
    import re
    words, at = [], 0.0
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


# the AD's real endings (Gol 9:16, Richat 16:9, Amber, Voynich), each in its own sentence
NARRATION = ("Echoes travel across the vast stone hall without fading. The sound moves continuously along the curved "
             "inner wall of the gallery. Construction ceased in 1656 upon the death of the sultan who ordered it. "
             "Researchers found stone tools of Acheulean artifacts alongside burned bones near the ancient rings. "
             "An inquiry concluded the room was most likely burned during the bombing of the city. Military "
             "cryptographers from both great wars studied the strange pages for years.")


@pytest.mark.parametrize("widescreen", [False, True])
def test_no_burst_ends_on_a_never_end_word_on_either_path(widescreen):
    words = _timed(NARRATION)
    phrases = vc.caption_phrases(words, None, widescreen)
    assert [w for p in phrases for w in p] == words
    assert vc.never_end_bursts(phrases) == [], [" ".join(w["word"] for w in p) for p in phrases]


def test_the_record_counts_what_the_breaker_sees():
    words = _timed(NARRATION)
    phrases = [words[0:2], words[2:4], words[4:]]   # "Echoes travel | across the | ...": one never-end end
    assert vc.never_end_bursts(phrases) == ["across the"]
    rates = vc.reading_rates(phrases, vc.burst_timings(phrases))
    assert rates["never_end"] == vc.never_end_bursts(phrases)
    for name in ("travel along", "the most", "from both", "artifacts alongside", "in 1656 upon"):
        assert vc.never_end_bursts([[{"word": w} for w in name.split()], [{"word": "x."}]]) == [name]
    # the board's caption rows read the same predicate (never a second list)
    src = (ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "board_evidence.py").read_text(encoding="utf-8")
    body = src[src.index("def caption_rates"):src.index("# ---", src.index("def caption_rates"))]
    assert "never_end_end" in body and "bs.LINE_END_NEVER" not in body


# ------------------------------------------------------------------------------------------------ F5: one gold scope
def test_the_hook_claim_draws_only_the_number_and_unit_gold(fact_record_key, tmp_path):
    import tests.test_design_cards_2026_11_03 as T
    import tests.test_freeze_cards_2026_11_04 as FC
    from src.figures import gold_runs
    plan = FC.hook_plan(T._photo(tmp_path / "dark.jpg", "dark"))
    claim = " ".join(FC.claim_lines(plan))
    parts = [pl for nm, p in plan.meta["_contrast"] if nm.startswith("title")
             for pl in (getattr(p, "parts", None) or [p])]
    gold_drawn = [pl.text.strip() for pl in parts if tuple(pl.fill[:3]) == tuple(brand.GOLD)]
    assert parts and "ABOUT" in " ".join(pl.text for pl in parts if tuple(pl.fill[:3]) == tuple(brand.BONE))
    assert gold_drawn == [t.strip() for t, g in gold_runs(claim.upper()) if g] == ["40 KM"], gold_drawn


def test_the_story_hero_unit_takes_the_one_gold_scope():
    from src.figures import gold_word_indices
    for qualifier, number, unit in (("ABOUT", "280", "FT DEEP"), ("", "94–104", "MILLION YEARS OLD"),
                                    ("UP TO", "20,000", "PEOPLE")):
        lead = qualifier.split() + number.split()
        gold = {i - len(lead) for i in gold_word_indices(lead + unit.split()) if i >= len(lead)}
        runs = multi_format.unit_runs(unit, gold)
        drawn_gold = [i for i, w in enumerate(unit.split()) if any(g and w in t.split() for t, g in runs)]
        assert set(drawn_gold) == gold


def test_every_figure_colouring_renderer_reads_the_one_gold_source():
    """The renderers that colour a stated quantity in this tree's scope read src/figures.py's gold spans."""
    import src.design_cards as dc
    checks = [(dc.plan_hook_frame, "runs=figure_line"), (dc._Card.text, "gold_runs")]
    for fn, needle in checks:
        assert needle in inspect.getsource(fn), fn.__qualname__
    story = inspect.getsource(multi_format.MultiFormatGenerator._render_story_frame)
    assert "gold_word_indices" in story and "DIMENSION_WORDS" not in story
