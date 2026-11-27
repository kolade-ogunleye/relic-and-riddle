"""Design-freeze lane C (captions), cycle 3 (queue LANE-C-cycle3.md and the stress realism run), closed by class in
src/video_composer.py:

- MO-cap-size: two 9:16 bursts shown back to back differ by at most one CAPTION_LADDER rung (bounded_caption_sizes);
  no burst draws larger than its own fit, and only a burst beside a shrunk one gives way.
- MO-C2: the 16:9 plate and its line fade in and out at a pause with the 60/80 ms plate-fade token, as 9:16 does.
- Stress LINE_END (all caps): a hedge phrase before its figure is no part of a proper name whatever its case, so the
  bursts never cut it from its figure (name_bonds).
- Stress realism LINE_END_BREAK: the composer's record lists every line inside a 9:16 burst that ends on a never-end
  word (never_end_lines), the ruled hedge break marked, and one total with the burst ends (never_end_total).

Invariants over the real runs' bursts (the freeze-panel catalogue's caption cases with their own word timings) and
labelled synthetic narrations; never pinned sizes or breaks.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
import fixtures as _fx

REAL = [c for c in _fx.load("captions")]
REEL = [c for c in REAL if c["format"] == "9:16"]


def _timed(text: str) -> list[dict]:
    words, at = [], 0.0
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


def _events(ass: Path) -> list[tuple[str, str, str, str]]:
    out = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:"):
            parts = line[len("Dialogue:"):].split(",", 9)
            out.append((parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[9]))
    return out


def _secs(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _drawn_sizes(ass: Path) -> list[tuple[float, float, float]]:
    """(show, hide, px) per 9:16 burst as drawn: its plate's interval and its CapText \\fs (the style's size when
    none), in frame pixels."""
    ev = _events(ass)
    plates = [(_secs(s), _secs(e)) for s, e, style, _ in ev if style == "CapBox"]
    out = []
    for s, e in plates:
        fs = {int(m.group(1)) if (m := re.search(r"\\fs(\d+)", t)) else vc.FONT_SIZE_ASS
              for ts, _, style, t in ev if style == "CapText" and s <= _secs(ts) < e}
        px = [vc.FONT_SIZE if f >= vc.FONT_SIZE_ASS else f * 0.5625 for f in fs]
        out.append((s, e, min(px)))
    return out


def _rung_index(px: float) -> int:
    """The ladder position a drawn size sits at or under (0 = FONT_SIZE): sizes are compared by rung, not by pixel."""
    ladder = sorted(vc.CAPTION_LADDER, reverse=True)
    return next((k for k, r in enumerate(ladder) if px >= r - 1), len(ladder))


@pytest.mark.parametrize("case", REEL, ids=[c["topic"] for c in REEL])
def test_back_to_back_bursts_differ_by_at_most_one_rung(case, tmp_path):
    ass = vc.VideoComposer().build_subtitles(_fx.words(case), tmp_path / "c.ass",
                                             emphasis=list(case.get("emphasis_words") or []) or None)
    sizes = _drawn_sizes(ass)
    for (s1, e1, a), (s2, _, b) in zip(sizes, sizes[1:]):
        if abs(e1 - s2) < 0.011:   # handed over with no pause
            assert abs(_rung_index(a) - _rung_index(b)) <= 1, (s1, a, s2, b)


def test_bounded_sizes_never_grow_a_burst_and_bound_each_handover():
    fits = [80, 80, 66, 80, 80, 74, 80, 58, 80]
    joined = [True] * (len(fits) - 1) + [False]
    out = vc.bounded_caption_sizes(fits, joined)
    assert all(o <= f for o, f in zip(out, fits))
    for i in range(len(out) - 1):
        lo, hi = sorted((out[i], out[i + 1]))
        assert hi <= vc._rung_above(lo) or hi == lo
    # across a pause nothing is bounded: the plate fades out and in
    assert vc.bounded_caption_sizes([80, 66], [False, False]) == [80, 66]
    # only what the bound needs: the burst two away from a floor burst keeps its size
    assert vc.bounded_caption_sizes([80, 80, 66, 80, 80], [True] * 4 + [False]) == [80, 74, 66, 74, 80]


def test_the_wide_plate_and_line_fade_at_a_pause(tmp_path):
    text = ("The ring is forty kilometres wide. It formed slowly over millions of years. "
            "Wind and sand shaped every ridge.")   # labelled synthetic narration (a test text, not a script)
    words = _timed(text)
    words[6]["start"] += 1.5   # a real pause before the second sentence
    for w in words[7:]:
        w["start"] += 1.5
    ass = vc.VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080)
    ev = _events(ass)
    plates = [(s, e, t) for s, e, style, t in ev if style == "CapBox"]
    lines = {s: t for s, e, style, t in ev if style == "CapText"}
    assert plates
    ends = [_secs(e) for _, e, _ in plates]
    starts = [_secs(s) for s, _, _ in plates]
    for i, (s, e, t) in enumerate(plates):
        after_pause = i == 0 or abs(starts[i] - ends[i - 1]) > 1e-6
        before_pause = i == len(plates) - 1 or abs(ends[i] - starts[i + 1]) > 1e-6
        fad = re.search(r"\\fad\((\d+),(\d+)\)", t)
        got = (int(fad.group(1)), int(fad.group(2))) if fad else (0, 0)
        want = (vc.CAPTION_PLATE_FADE_IN_MS if after_pause else 0, vc.CAPTION_PLATE_FADE_OUT_MS if before_pause else 0)
        assert got == want, (i, got, want)
        line_fad = re.search(r"\\fad\((\d+),(\d+)\)", lines[s])
        assert ((int(line_fad.group(1)), int(line_fad.group(2))) if line_fad else (0, 0)) == want


@pytest.mark.parametrize("text", [
    "Altar copper salt 2560-2540 BC MORE THAN 535 meters of wall.",
    "THE ROMAN ARMY HAULED NEARLY 1,090 TONNES OF STONE.",
    "Altar copper salt 2560-2540 BC more than 535 meters of wall.",
])   # labelled synthetic narrations: upper-case copy (0 in real narration) and its lower-case control
def test_a_hedge_never_leaves_its_figure_whatever_its_case(text):
    words = _timed(text)
    bonds = vc.caption_bonds(words, names=False)
    at = -1
    for p in vc.caption_phrases(words, None, False)[:-1]:
        at += len(p)
        assert at not in bonds, [" ".join(w["word"] for w in q) for q in vc.caption_phrases(words, None, False)]
    raw = [w["word"] for w in words]
    hedge_at = next(i for i, w in enumerate(raw) if w.lower() in ("more", "nearly"))
    assert hedge_at - 1 not in vc.name_bonds(raw)


@pytest.mark.parametrize("case", REEL, ids=[c["topic"] for c in REEL])
def test_the_record_counts_every_never_end_line_inside_a_burst(case):
    words = _fx.words(case)
    phrases = vc.caption_phrases(words, list(case.get("emphasis_words") or []) or None, False)
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases))
    drawn = []
    for p in phrases:
        ws = [w["word"] for w in p]
        lay = vc.reel_caption_lines(ws)
        if lay and vc.never_end_end(ws[lay[0] - 1]):
            drawn.append(" ".join(ws[:lay[0]]) + " / " + " ".join(ws[lay[0]:]))
    assert [r["text"] for r in rec["never_end_lines"]] == drawn
    assert rec["never_end_total"] == len(rec["never_end"]) + sum(not r["hedge_break"] for r in rec["never_end_lines"])
    # every never-end line outside the ruled hedge break is also a recorded line defect
    defects = set(rec["line_defect"])
    assert all(r["text"] in defects for r in rec["never_end_lines"] if not r["hedge_break"])


# ---------------------------------------------------------------- lander cycle-3 rulings 2 and 5
# Labelled synthetic narrations (test texts written for the rules, not scripts), the real runs' classes in other words
GROUPS = (
    "The rocks cooled between 94 and 104 million years ago, and the dome rose.",
    "Divers dated the wreck from roughly 205 BC to 87 BC. The ship was lost.",
    "Engineers moved from nearly 1,090 tonnes to 1,168 tonnes. Work stopped.",
    "The trenches, by one estimate, cut only shallow ground.",
    "Survival rose to an estimated eighty percent. Then it fell.",
    "The text dates to between 1404 and 1438. Nobody knows more.",
)


def test_an_era_range_is_one_figure_group():
    raw = "from roughly 205 BC to 87 BC.".split()
    bonds = vc.caption_bonds(raw, names=False)
    assert all(k in bonds for k in range(1, len(raw) - 1))


@pytest.mark.parametrize("text", GROUPS)
def test_no_burst_cut_falls_inside_a_settable_figure_group(text):
    words = _timed(text)
    phrases = vc.caption_phrases(words, None, False)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    # lander ruling on 2396b8df: a settable RANGE group whose whole form leaves a never-end burst end (ENGINEERS MOVED
    # FROM |) cuts at its joiner, and only there; every other settable group is never cut
    cuts = vc._cuts_of(phrases)
    assert cuts & (vc._figure_bonds([w["word"] for w in words]) | vc.unit_links(words)) <= vc.range_joiner_cuts(words), texts
    assert not vc.figure_split_cuts(phrases) or vc.never_end_bursts(phrases) == [], texts
    for p in phrases:   # a group past the caps sets on two lines, each line within one burst's caps
        ws = [w["word"] for w in p]
        lay = vc.reel_caption_lines(ws)
        if vc._figure_group_over_caps(ws):
            assert lay is not None
            for line in (ws[:lay[0]], ws[lay[0]:]):
                assert len(line) <= vc.MAX_BURST_WORDS and len(" ".join(line)) <= vc.MAX_BURST_CHARS
            assert not vc.unit_link(ws[lay[0] - 1], ws[lay[0]])


@pytest.mark.parametrize("case", REAL, ids=[f"{c['topic']} {c['format']}" for c in REAL])
def test_every_figure_group_cut_left_is_one_no_burst_can_set_and_is_recorded(case):
    words = _fx.words(case)
    wide = case["format"] == "16:9"
    phrases = vc.caption_phrases(words, list(case.get("emphasis_words") or []) or None, wide)
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases), widescreen=wide)
    assert rec["figure_split"] == vc.figure_split_cuts(phrases)
    if wide:
        return
    raw = [w["word"] for w in words]
    links = vc._figure_bonds(raw) | vc.unit_links(raw)
    at = -1
    for p in phrases[:-1]:
        at += len(p)
        if at in links:   # the cut's whole group: no burst can set it (too wide on each line, or no seam)
            a, b = at, at + 1
            while a - 1 in links:
                a -= 1
            while b in links:
                b += 1
            assert not vc._reel_figure_group_fits(raw[a:b + 1]), raw[a:b + 1]


def test_a_group_no_burst_sets_never_yields_after_its_joiner():
    words = _timed("The wreck sank between approximately 70 and 60 BC. Divers found it.")
    for p in vc.caption_phrases(words, None, False)[:-1]:
        assert vc._norm(p[-1]["word"]) not in vc.RANGE_JOINERS


def test_the_wide_plate_keeps_to_the_plate_steps_within_a_run(tmp_path):
    text = ("The ring is forty kilometres wide and it formed slowly over millions of years while wind and sand "
            "shaped every single ridge of its rim.")   # labelled synthetic narration
    ass = vc.VideoComposer().build_subtitles(_timed(text), tmp_path / "w.ass", width=1920, height=1080)
    ev = [(s, e, t) for s, e, style, t in _events(ass) if style == "CapBox"]
    widths = [int(m.group(1)) for _, _, t in ev if (m := re.search(r"l (\d+) 0 b", t))]
    assert len(ev) > vc.CAPTION_PLATE_STEPS and len(set(widths)) <= vc.CAPTION_PLATE_STEPS
    xs = [(int(m.group(1)), w) for (_, _, t), w in zip(ev, widths) if (m := re.search(r"pos\((\d+),", t))]
    for x, w in xs:   # centred on the line
        assert abs(x + (w + 14) / 2 - 960) <= 1


# ---------------------------------------------------------------- lander rulings on 8f00b711
def test_a_group_closing_word_rides_line_two_with_the_bond_extra_and_is_recorded():
    # labelled synthetic narration: line 2 (AND 2 MILLION KM AWAY,) needs the +1 word to hold the closing word
    words = _timed("The probe drifted between 1 and 2 million km away, and the signal faded slowly.")
    extra: list = []
    phrases = vc.caption_phrases(words, None, False, bond_extra=extra)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    assert "between 1 and 2 million km away," in texts, texts
    assert any(e["removed"] == "lone_group_tail" and e["text"] == "between 1 and 2 million km away," for e in extra)
    k = vc.reel_caption_lines("between 1 and 2 million km away,".split())[0]
    line2 = "between 1 and 2 million km away,".split()[k:]
    assert len(line2) <= vc.MAX_BURST_WORDS + vc.CAPTION_BOND_EXTRA_WORDS
    assert len(" ".join(line2)) <= vc.MAX_BURST_CHARS + vc.CAPTION_BOND_EXTRA_CHARS


@pytest.mark.parametrize("case", REEL, ids=[c["topic"] for c in REEL])
def test_a_lone_group_closing_word_is_one_no_two_line_form_holds_and_is_recorded(case):
    words = _fx.words(case)
    phrases = vc.caption_phrases(words, list(case.get("emphasis_words") or []) or None, False)
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases))
    assert rec["lone_group_tail"] == vc.lone_group_tails(phrases)
    for row in rec["lone_group_tail"]:
        group = row.replace(" | ", " ").split()
        assert vc._figure_group_split(group) is None, row


def _cue_runs():
    import glob
    paths = sorted(glob.glob(str(ROOT / "output" / "run_*" / "words.json")))
    paths += sorted(glob.glob("C:/tmp/prod-export-data/output/run_*/words.json"))   # the read-only production sample
    cases = [(Path(p).parent.name, p) for p in paths]
    cases += [(f"fixture {c['topic']}", str(c["words"])) for c in REAL]
    return cases


CUE_RUNS = _cue_runs()


@pytest.mark.parametrize("name,path", CUE_RUNS, ids=[c[0] for c in CUE_RUNS])
def test_sidecar_cues_follow_the_burst_bonds_and_never_end_rules(name, path):
    """Lander ruling on 8f00b711: an SRT/VTT cue never ends on a never-end word, never cuts a figure group, and never
    runs across a sentence end where a cue boundary is available there (the real runs: dev output, the production
    sample and the catalogue's fixtures). A cue line breaks inside a figure group only at its seam before a range
    joiner."""
    import json
    words = json.loads(Path(path).read_text(encoding="utf-8"))
    timed = [w for w in words if str(w.get("word", "")).strip()]
    raw = [str(w["word"]).strip() for w in timed]
    links = vc._figure_bonds(raw) | vc.unit_links(raw)
    cues = vc.partition_sidecar_cues(words)
    assert " ".join(t.replace("\n", " ") for _, _, t in cues).split() == raw   # the narration, word for word
    at = -1
    for i, (_, _, text) in enumerate(cues):
        ws = text.split()
        start, at = at + 1, at + len(ws)
        if i < len(cues) - 1:
            assert not vc.never_end_end(ws[-1]), text
            assert at not in links, text
        assert not any(vc.TERMINAL_PUNCT.search(w) and not vc._is_abbreviation(w) for w in ws[:-1]), text
        lines = text.split("\n")
        if len(lines) == 2:
            k = start + len(lines[0].split()) - 1
            assert k not in links or vc._norm(raw[k + 1]) in vc.RANGE_JOINERS, text


# ---------------------------------------------------------------- int-3: a figure group bond and the never-end rule
# The two real narrations the int-3 integration caught (QF-150's lint on the bond of 8f00b711): the group held whole
# left its never-end lead-in or the word after its closing word ending a burst (STANDS AFTER |, OLD, AND |).
NEVER_END_BEFORE_GROUP = [
    ("The old dome still stands after nearly two thousand years.", "after nearly two thousand years."),
    ("Its earliest tunnels may be nearly three thousand years old, and Byzantine-era Christians later expanded it "
     "to hide from raids.", "old,"),
]


def _paced(text: str) -> list[dict]:
    """The narrator's fitted pace (QF-150's nominal timing on the 69 real runs' words: 0.053 s a letter plus 0.09 s,
    a clause mark 0.47 s more, a sentence end 0.55 s), the timing the script-stage lint plans these narrations at."""
    words, at = [], 0.0
    for w in text.split():
        span = 0.053 * len(re.sub(r"\W", "", w)) + 0.09
        words.append({"word": w, "start": round(at, 3), "dur": round(span * 0.85, 3)})
        at += span + (0.55 if re.search(r"[.!?]$", w) else 0.47 if re.search(r"[,;:]$", w) else 0.0)
    return words


@pytest.mark.parametrize("text,burst", NEVER_END_BEFORE_GROUP)
def test_a_figure_group_held_whole_leaves_no_never_end_burst_end_before_or_after_it(text, burst):
    phrases = vc.caption_phrases(_paced(text), None, False)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    assert vc.never_end_bursts(phrases) == [], texts          # the never-end rule holds
    assert vc.figure_split_cuts(phrases) == [], texts         # and the group is never cut
    assert burst in texts, texts
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases))
    assert rec["never_end"] == [] and rec["figure_split"] == []
    for p in phrases:   # the lead-in rides the group's own two-line form, each line within one burst's caps
        ws = [w["word"] for w in p]
        if vc._figure_group_over_caps(ws):
            k = vc.reel_caption_lines(ws)[0]
            assert not vc.reel_line_defect(ws)
            for line in (ws[:k], ws[k:]):
                assert len(line) <= vc.MAX_BURST_WORDS and len(" ".join(line)) <= vc.MAX_BURST_CHARS
    if burst == "old,":   # a closing word no form holds with its group stands alone after it, recorded
        assert rec["lone_group_tail"] == ["nearly three thousand years | old,"]


# ---------------------------------------------------------------- lander ruling on 2396b8df: a range splits at its joiner
# Order: the figure group whole (one burst, or its own two lines); where that leaves a never-end burst end, a RANGE group
# may cut at its joiner and only there, each side with its own number, unit and hedge; only then does a never-end end
# stay (recorded, and QF-150's lint flags it). The real narrations (unfinished obelisk, dev and production; Antikythera
# closing sentence, production).
OBELISK = ("Calculations of the finished obelisk's weight range from nearly 1,090 tonnes to 1,168 tonnes, possibly "
           "ordered to complement the Lateran Obelisk at Karnak.")
ANTIKYTHERA = "That astonishing scientific mastery vanished for over a thousand years when the cargo ship was lost."


@pytest.mark.parametrize("timing", [_paced, _timed])
def test_a_range_left_with_a_never_end_lead_in_splits_at_its_joiner_only(timing):
    recut: list = []
    phrases = vc.caption_phrases(timing(OBELISK), None, False, recut=recut)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    # lander ruling, freeze cycle 6 (panel 7 C1, refining R1; 2026-11-27): FROM NEARLY / 1,090 TONNES (a hedge parted from its figure on a forced break at the clip's
    # size) gives way to the previous burst taking the stranded FROM (remedy 1), recorded; was: never RANGE FROM |,
    # FROM NEARLY 1,090 TONNES one burst
    moved = {e["into"].split(" | ")[0] for e in recut if e.get("strand_remedy") and e.get("function_end")}
    assert set(vc.never_end_bursts(phrases)) == moved == {"obelisk's weight range from"}, texts
    assert "nearly 1,090 tonnes" in texts and "to 1,168 tonnes," in texts, texts   # the hedge with the first end
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases))
    assert rec["figure_split"] == ["nearly 1,090 tonnes | to 1,168 tonnes,"]           # the cut is recorded
    raw = OBELISK.split()
    assert vc.range_joiner_cuts(raw) == {raw.index("tonnes")}           # only before the joiner, never inside a unit


def test_a_range_one_burst_holds_whole_is_not_split():
    phrases = vc.caption_phrases(_paced("The weight calculations ranged from nearly 1,090 to 1,168 tonnes. Work "
                                        "stopped."), None, False)
    assert vc.figure_split_cuts(phrases) == [] and vc.never_end_bursts(phrases) == []


def test_a_group_that_is_no_range_stays_whole_and_its_never_end_lead_in_is_recorded():
    raw = ANTIKYTHERA.split()
    assert vc.range_joiner_cuts(raw) == set()
    phrases = vc.caption_phrases(_paced(ANTIKYTHERA), None, False)
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    assert "over a thousand years" in texts and vc.figure_split_cuts(phrases) == [], texts
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases))
    assert rec["never_end"] == vc.never_end_bursts(phrases) and rec["never_end"], texts
