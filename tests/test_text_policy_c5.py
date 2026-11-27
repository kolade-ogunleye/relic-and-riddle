"""Freeze cycle 5, TEXT stream: the ONE line-breaking policy (src/line_policy.py) and the caption burst policy
(lander ruling 2026-11-26). The same measures the corpus proofs run over every dev run
(docs/reviews/freeze-system-2026-11-15/corpus/text_lines.py, text_captions.py), on committed fixtures, so a class
cannot silently reopen. Invariants, never pinned breaks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import brand, brand_system, heading, line_policy, typography
from src import multi_format as mf
from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
AMBER = json.loads((ROOT / "tests" / "fixtures" / "text_c5" / "amber_caption_words.json").read_text(encoding="utf-8"))

# Real run copy (carousel and Story bodies of development runs; the panel-6 leads among them)
BODIES = (
    "Calculations place its finished weight at up to 1,168 tonnes, still anchored to bedrock.",
    "Carbonatite rocks cooled between 94 and 104 million years ago beneath the surface.",
    "Radiometric dating shows they cooled between 94 and 104 million years ago.",
    "Official records end in August 1944, when Allied bombers heavily fire-bombed the city.",
    "The colossi have stood since 1350 BC, guarding where Amenhotep III built his temple.",
    "Engineers estimate the monument's finished weight at nearly 1,090 tonnes of granite.",
    "Trepanation dates back between 7,000 and 10,000 years, making it the oldest surgery.",
    "The completed room covered more than 590 square feet and held over 13,000 pounds of amber.",
)


# ------------------------------------------------------------------------------------------- one vocabulary
def test_one_function_word_vocabulary_for_every_surface():
    from src import design_cards, graphics
    never, last = brand_system.LINE_END_NEVER, brand_system.LINE_END_LAST_RESORT
    assert not never & last and brand_system.LINE_END_FUNCTION == never | last
    # the conjunctions and subordinators panel 6 found at heading and body line ends
    assert {"and", "or", "but", "when", "while", "than", "that", "which", "because", "if", "as"} <= last
    assert vc.NEVER_END == brand_system.LINE_END_FUNCTION                       # captions
    assert mf._DANGLING == brand_system.LINE_END_FUNCTION                       # carousel / Story body
    assert never <= design_cards.W1_STOPS <= brand_system.LINE_END_FUNCTION     # cards
    assert design_cards.WEAK_LINE_END == brand_system.LINE_END_FUNCTION | brand_system.LINE_END_WEAK
    assert {w.upper() for w in brand_system.LINE_END_FUNCTION} <= graphics.TALL_STOP_WORDS  # graphic labels
    assert graphics.TALL_STOP_WORDS <= graphics.ROUTE_FUNCTION_WORDS            # route names
    assert line_policy.end_class("when") == "last_resort" and line_policy.end_class("of") == "never"
    assert line_policy.end_class("of,") is None and line_policy.end_class("has") == "weak"


@pytest.mark.parametrize("text, pairs", [
    ("a load of 1,090 tonnes of granite", ["1,090 tonnes"]),
    ("between 94 and 104 million years ago", ["104 million", "million years", "years ago", "and 104"]),
    ("more than 590 square feet", ["than 590", "590 square", "square feet"]),
    ("stood since 1350 BC, guarding", ["1350 BC,"]),
    ("end in August 1944, when", ["August 1944,"]),
    ("up to 1,168 tonnes", ["up to", "to 1,168", "1,168 tonnes"]),
    ("about 40 km across", ["about 40", "40 km"]),
    ("TWENTY THOUSAND PEOPLE", ["TWENTY THOUSAND", "THOUSAND PEOPLE"]),
])
def test_keep_together_holds_a_value_to_its_unit_scale_hedge_and_date(text, pairs):
    words = text.split()
    bonds = line_policy.keep_together(words)
    for pair in pairs:
        a, b = pair.split()
        k = next(i for i in range(1, len(words)) if words[i - 1] == a and words[i] == b)
        assert k in bonds, (text, pair, bonds)


def test_keep_together_never_bonds_across_closing_punctuation_or_plain_words():
    assert not line_policy.keep_together("in 1941, 36 soldiers came over the wall".split()) - {3}
    assert line_policy.keep_together("the room was stolen in the night".split()) == set()


def test_the_judge_names_the_panel_six_leads():
    f = lambda lines, **kw: {x["fault"] for x in line_policy.violations(lines, **kw)}   # noqa: E731
    assert f(["ARCHAEOLOGICAL", "EVIDENCE AND", "REFUTED CLAIMS"]) == {"function_word_end"}
    assert f(["IMPACT THEORY", "DISPROVED WHEN", "SUSPECTED COESITE"]) == {"function_word_end"}
    assert f(["weight of nearly 1,090", "tonnes of granite"]) == {"unit_split"}
    assert f(["between 94 and 104 million", "years ago"]) == {"unit_split"}
    assert f(["THE WHISPERING", "VAULT"], heading=True) == {"lone_last_row"}
    assert f(["THE UNFINISHED", "OBELISK"], heading=True, widths=[100, 51]) == {"lone_last_row"}
    assert not f(["CARVED", "FROM SOLID SALT"], heading=True)          # the accepted stagger: a one-word FIRST row
    assert not f(["THE EYE", "OF THE SAHARA"], heading=True)
    assert not f(["IMPACT THEORY", "DISPROVED"], heading=True, widths=[100, 69])   # ruling 4: balance >= 0.55


# ------------------------------------------------------------------------------------------------ body copy
def _draw():
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("width", (700, 760, 840, 912))
def test_body_copy_never_parts_a_value_from_its_unit_and_never_drops_a_word(body, width):
    d = _draw()
    lines, font, short = typography.fit_text(d, body, brand.TEXT_FONT, 36, width, 6, 36, quiet=True)
    line_policy.take_notes()
    even = mf.even_rag(d, lines, font, width, mf.MultiFormatGenerator.BODY_MEASURE)
    for got in (lines, even):
        assert " ".join(got).split() == body.split() and not short            # truth: every word, in order
        faults = line_policy.violations(got)
        # (a range too long for its line may break before its joiner: the recorded last resort, "seam")
        assert not [f for f in faults if f["fault"] in ("unit_split", "never_end") and not f.get("seam")], (got, faults)


def test_a_violation_no_break_removes_is_recorded_not_silent():
    d = _draw()
    font = typography.get_font(brand.TEXT_FONT, 36)
    text = "length between roughly 41.75 metres and 42 metres, with calculations of its weight"
    line_policy.take_notes()
    lines = typography.fit_text(d, text, brand.TEXT_FONT, 36, 760, 6, 36, quiet=True)[0]
    left = line_policy.violations(lines)
    notes = line_policy.take_notes()
    assert not left or any(n["lines"] == lines and n["faults"] for n in notes), (lines, left, notes)
    assert font is not None


def test_policy_wrap_takes_one_more_line_before_a_violation_and_never_drops_a_word():
    m = lambda s: 10.0 * len(s.replace(line_policy.NBSP, " "))   # noqa: E731
    text = "The obelisk would have weighed 1,090 tonnes when it was finished"
    for width in (200, 260, 300, 360, 420):
        lines = line_policy.wrap(text, m, width, max_lines=8)
        assert lines is not None and " ".join(lines).split() == text.split()
        assert not [f for f in line_policy.violations(lines) if f["fault"] == "unit_split"], lines
    assert line_policy.wrap(text, m, 200, max_lines=1) is None      # it refuses; it never truncates


def test_a_fitter_never_drops_a_word_behind_an_ellipsis():
    src = "A concentric formation in Mauritania that has long fascinated observers from orbit."
    assert line_policy.ellipsis_drop("A concentric formation in Mauritania that... long fascinated", src) == ["has"]
    assert line_policy.ellipsis_drop("A concentric formation in Mauritania that has long\u2026", src) == []
    assert line_policy.ellipsis_drop(src, src) == []
    # the system's own fitters refuse instead of cutting (QF-23): no ellipsis comes out of fit_text
    d = _draw()
    with pytest.raises(typography.TextFitError):
        typography.fit_text(d, src, brand.TEXT_FONT, 36, 300, 1, 36, quiet=True)


# ------------------------------------------------------------------------------------------------- headings
def test_headings_keep_dates_eras_and_hedges_whole_and_prefer_no_conjunction_end():
    for text in ("BUILT IN AUGUST 1944 BY HAND", "STANDING SINCE 1350 BC IN THEBES", "MORE THAN 590 SQUARE FEET"):
        words = text.split()
        allowed = set(heading.break_points(words))
        assert not allowed & (line_policy.keep_together(words) - line_policy.range_seams(words)), text
    measure = lambda t, size: len(t) * size * 0.5   # noqa: E731
    form = heading.set_heading("THE THEORY DISPROVED WHEN ROCKS WERE DATED", measure, 26 * 100 * 0.5, (100,), max_lines=2)
    assert form is not None
    ends = [ln.split()[-1].lower() for ln in form.lines[:-1]]
    assert not [w for w in ends if w in brand_system.LINE_END_FUNCTION], form.lines


# ------------------------------------------------------------------------------------------------- captions
def _amber():
    words = [dict(w) for w in AMBER["words"]]
    record: list = []
    phrases = vc.caption_phrases(words, AMBER["emphasis_words"], recut=record)
    return words, phrases, record


def test_caption_bursts_under_the_2026_11_26_ruling():
    words, phrases, record = _amber()
    assert [w for p in phrases for w in p] == words                       # every word, in order, its own time
    timing = vc.burst_timings(phrases, dwell=True)
    kept = {e["kept_short"].replace(" / ", " ") for e in record if e.get("kept_short")}
    for p, (show, hide, _) in zip(phrases, timing):
        text = " ".join(w["word"] for w in p)
        assert hide - show >= vc.CAPTION_SPLIT_MIN_S - 1e-6 or text in kept, (text, hide - show)
    short = [p for p, (s, h, _) in zip(phrases, timing) if h - s < vc.CAPTION_SPLIT_MIN_S - 1e-6]
    assert len(short) <= 2                                                # 14 of 43 before (the panel-6 evidence)
    assert sum(len(p) == 1 for p in phrases) <= 2                         # 9 one-word bursts before
    # no burst cut parts the phrase units the panel named
    texts = [" ".join(re.sub(r"[^\w-]", "", w["word"]).lower() for w in p) for p in phrases]
    for unit in ("heavily fire-bombed", "soviet artillery", "expert supervision", "british aircraft",
                 "eighteenth-century expansions"):
        assert any(unit in t for t in texts), (unit, texts)
    # a split or a re-cut is never recorded without its rule; a legal two-line burst has no fault
    for p in phrases:
        raw = [w["word"] for w in p]
        faults = vc.line_rule_faults(raw)
        assert "one_word_line" not in faults or vc._lone_function_line(vc._phrase_words(raw), vc.reel_caption_lines(raw)[0])


def test_a_one_content_word_line_is_legal_and_a_lone_function_word_line_is_not():
    assert vc.line_rule_faults("under expert supervision.") == []
    assert "one_word_line" not in vc.line_rule_faults("Documentation tracking")
    lay = vc.reel_caption_lines("when K\u00f6nigsberg Castle")
    if lay is not None and lay[0] == 1:
        assert vc.line_rule_faults("when K\u00f6nigsberg Castle")


def test_one_caption_size_per_clip_and_no_empty_first_frame(tmp_path):
    comp = vc.VideoComposer()
    ass = tmp_path / "c.ass"
    comp.build_subtitles([dict(w) for w in AMBER["words"]], ass, emphasis=AMBER["emphasis_words"])
    text = ass.read_text(encoding="utf-8")
    events = [ln for ln in text.splitlines() if ln.startswith("Dialogue:") and ",CapText," in ln]
    sizes = {m.group(1) for ln in events for m in [re.search(r"\\fs(\d+)", ln)] if m}
    plain = [ln for ln in events if not re.search(r"\\fs(\d+)", ln)]
    clip = comp.last_reading_rate["clip_size"]
    # round 2 ruling, freeze cycle 6 (rule 4; 2026-11-27): one size per clip, but for at most
    # CAPTION_FORCED_RUNG_MAX bursts set alone one rung under it (recorded forced_rung, also listed under)
    rung = clip.get("forced_rung") or []
    assert len(rung) <= vc.CAPTION_FORCED_RUNG_MAX and all(r in clip["under"] for r in rung)
    one = sizes - {str(round(r["px"] / 0.5625)) for r in rung}
    assert len(one) <= 1 and not (one and plain), one
    assert clip["px"] in vc.CAPTION_LADDER and clip["px"] >= vc.CAPTION_BOND_MIN_SIZE
    assert not [u for u in clip["under"] if u not in rung]
    assert (clip["px"] == vc.FONT_SIZE) == (not one)
    # the first word of a burst is never fully transparent on the plate's first frame
    shows = {ln.split(",")[1] for ln in text.splitlines() if ln.startswith("Dialogue:") and ",CapBox," in ln}
    firsts = [ln.split(",", 9)[9] for ln in events if ln.split(",")[1] in shows]
    assert firsts
    for body in firsts:   # the event on a plate's first frame: its first drawn word is not at alpha FF
        head = re.sub(r"^(\{\\pos[^}]*\})?(\{\\fad[^}]*\})?(\{\\fs\d+\})?", "", body)
        assert not head.startswith(r"{\alpha&HFF&\t("), body[:160]
    assert vc.CAPTION_FIRST_WORD_HEAD_MS == round(1000 / vc.VIDEO_FPS) < vc.CAPTION_WORD_EASE_MS


def test_caption_clip_size_is_the_largest_rung_every_burst_sets_at():
    top, mid, low = vc.CAPTION_LADDER
    assert vc.caption_clip_size([top, top]) == top and vc.caption_clip_size([top, mid, top]) == mid
    assert vc.caption_clip_size([top, low, mid]) == low
    assert vc.caption_clip_size([top, low - 6]) == top      # a single over-long word's own shrink sets no clip size
    assert vc.caption_clip_size([71]) == top
    assert vc.caption_clip_size([]) == top


def test_phrase_unit_reads_shapes_and_closed_classes_only():
    pu = line_policy.phrase_unit
    assert pu(["bombers"], "heavily", "fire-bombed") == "adverb_participle"
    assert pu(["by"], "Soviet", "artillery") == "modifier_noun"
    assert pu(["under"], "expert", "supervision") == "modifier_noun"
    assert pu(["following"], "eighteenth-century", "expansions") == "modifier_noun"
    assert pu(["in"], "Germany,", "fueling") is None and pu(["it", "was"], "packed", "into") is None
    assert pu(["German"], "soldiers", "disassembled") is None


def test_the_readable_merge_adds_no_function_word_end_and_cuts_no_bond(monkeypatch):
    """Over the committed real caption words: against the phrase builder's bursts, the merge keeps every word, ends
    no further burst on a function word, makes no cut inside a bond, a name or a figure and its unit, and leaves no
    more bursts under 0.8 s."""
    words = [dict(w) for w in AMBER["words"]]
    rec: list = []
    after = vc.caption_phrases(words, AMBER["emphasis_words"], recut=rec)
    monkeypatch.setattr(vc, "_readable_merge", lambda phrases, *a, **k: phrases)
    before = vc.caption_phrases(words, AMBER["emphasis_words"])
    assert [w for p in after for w in p] == [w for p in before for w in p] == words
    # lander ruling, freeze cycle 6 (panel 7 C1, refining R1; 2026-11-27): a burst may end on the stranded function
    # word the strand pass moved to it (remedy 1: GERMAN SOLDIERS OF | ARMY GROUP NORTH), recorded; no other
    strand = {e["into"].split(" | ")[0] for e in rec if e.get("strand_remedy") and e.get("function_end")}
    assert set(vc.never_end_bursts(after)) - strand <= set(vc.never_end_bursts(before))
    assert strand <= set(vc.never_end_bursts(after))
    assert set(vc.figure_split_cuts(after)) <= set(vc.figure_split_cuts(before))
    raw = [w["word"] for w in words]
    bonds = vc.unit_links(words) | vc.name_bonds(raw) | vc.caption_bonds(words)
    pos = {id(w): k for k, w in enumerate(words)}
    moved = {pos[id(p[-1])] for p in after[:-1] if " ".join(w["word"] for w in p) in strand}
    # the moved word's own lead-in bond (OF to ARMY GROUP NORTH) is the one bond remedy 1 parts (same ruling)
    assert not {pos[id(p[-1])] for p in after[:-1]} & bonds - {pos[id(p[-1])] for p in before[:-1]} - (
        moved & (vc.caption_bonds(words) - vc.caption_bonds(words, names=False) - vc.name_bonds(raw)))

    def short(ph):
        return sum(h - s < vc.CAPTION_SPLIT_MIN_S - 1e-6 for s, h, _ in vc.burst_timings(ph, dwell=True))
    assert short(after) < short(before)
    for p in after:                      # a joined burst: two lines, each within one burst's caps
        if vc.over_caps(p) and vc._reel_form_strict(tuple(w["word"] for w in p)) is None:
            lay = vc.reel_caption_lines(p)
            if lay is not None:
                shown = vc._phrase_words([w["word"] for w in p])
                for line in (shown[:lay[0]], shown[lay[0]:]):
                    assert len(line) <= vc.MAX_BURST_WORDS and len(" ".join(line)) <= vc.MAX_BURST_CHARS, shown
