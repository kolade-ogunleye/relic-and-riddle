"""Lane C cycle 4: the caption line rule (lander ruling 2026-11-25, delegated approvals), in src/video_composer.py.

A 9:16 burst whose two-line break is not legal is split into consecutive bursts (_line_split). A legal break never
ends line 1 on an article, a preposition or a conjunction, never parts a qualifier from its figure or a figure from
its unit, and leaves no one-word line unless the burst is one word (line_rule_faults). The two-line layout ranks every
legal break before an illegal one, the rung included (_two_line_options). Each split is timed on its own words (no
voice stretch), adds no burst under CAPTION_MIN_ON_SCREEN_S (D1), drops and rewords nothing, and is recorded
(reading_rates "line_split": the burst, its parts or None, the faults it removed, the rule). A 16:9 burst is one line.

Invariants over the freeze-panel catalogue's real caption cases (their own word timings) and labelled synthetic
narrations; never pinned breaks.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
import fixtures as _fx  # noqa: E402

REAL = list(_fx.load("captions"))
REEL = [c for c in REAL if c["format"] == "9:16"]
WIDE = [c for c in REAL if c["format"] == "16:9"]
FAULTS = {"function_word_line_end", "qualifier_line", "unit_line", "one_word_line"}

# labelled synthetic narrations (not real copy): long words and lead-in prepositions, so two-line bursts are common
SYNTHETIC = [
    "Documentation tracking its whereabouts ends abruptly in the archives. Investigators conclude the collection "
    "was most likely destroyed when the fortress was bombarded.",
    "Under expert supervision, craftsmen disassembled the panels within thirty-six hours. Reconstruction specialists "
    "later recreated approximately four hundred individual components.",
    "Archaeologists estimate the underground labyrinth extends more than 590 square feet beneath the monastery, "
    "throughout the surrounding hillside.",
]


def _timed(text: str) -> list[dict]:
    words, at = [], 0.0
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


def _cases():
    for c in REEL:
        yield c["topic"], _fx.words(c), list(c.get("emphasis_words") or [])
    for i, text in enumerate(SYNTHETIC):
        yield f"synthetic-{i}", _timed(text), None


CASES = list(_cases())


def _pre(words, emphasis=None, wide=False):
    """The bursts before the LINE_RULE pass."""
    orig = vc._line_split
    vc._line_split = lambda phrases, *a, **k: phrases
    try:
        return vc.caption_phrases(words, emphasis, wide)
    finally:
        vc._line_split = orig


def _layout(words, emphasis=None, wide=False):
    rec: list[dict] = []
    return vc.caption_phrases(words, emphasis, wide, line_split=rec), rec


def _key(p) -> tuple:
    return tuple(id(w) for w in p)


def _two_line_text(p) -> str:
    ws = [w["word"] for w in p]
    lay = vc.reel_caption_lines(ws)
    return " ".join(ws[:lay[0]]) + " / " + " ".join(ws[lay[0]:]) if lay else " ".join(ws)


@pytest.mark.parametrize("label,words,emph", CASES, ids=[c[0] for c in CASES])
def test_every_two_line_burst_is_legal_or_recorded_as_having_no_legal_split(label, words, emph):
    phrases, rec = _layout(words, emph)
    kept = {(e["text"], e["at"]) for e in rec if e["into"] is None}
    for p in phrases:
        faults = vc.line_rule_faults(p)
        if faults:
            assert (_two_line_text(p), round(float(p[0]["start"]), 2)) in kept, (label, _two_line_text(p), faults)
    # and every record names its rule and the faults it removed, from the one fault set
    for e in rec:
        assert e["rule"] == vc.LINE_RULE and e["removed"] and set(e["removed"]) <= FAULTS, e


@pytest.mark.parametrize("label,words,emph", CASES, ids=[c[0] for c in CASES])
def test_the_pass_keeps_every_word_its_order_and_its_timing(label, words, emph):
    phrases, _ = _layout(words, emph)
    assert [w for p in phrases for w in p] == words
    # each burst is timed on its own words: it shows a lead before its first word, never earlier than the burst
    # before it hides (no stretch: no word time is changed)
    timing = vc.burst_timings(phrases, dwell=True)
    for p, (show, hide, reveals) in zip(phrases, timing):
        assert show <= float(p[0]["start"]) + 1e-6 and len(reveals) == len(p)
    for (s1, h1, _), (s2, _, _) in zip(timing, timing[1:]):
        assert h1 <= s2 + 1e-6


@pytest.mark.parametrize("label,words,emph", CASES, ids=[c[0] for c in CASES])
def test_the_pass_changes_only_the_bursts_it_records_and_their_sentence(label, words, emph):
    pre = _pre(words, emph)
    phrases, rec = _layout(words, emph)
    split = [e for e in rec if e["into"] is not None]
    pre_keys = {_key(p) for p in pre}
    new = [p for p in phrases if _key(p) not in pre_keys]
    if not split:
        assert not new, label
        return
    # the words a split may move: those of each pre-pass burst with a LINE_RULE fault and the bursts of its
    # sentence on either side (a one-word part joins one; a window re-cuts them)
    pos = {id(w): k for k, w in enumerate(words)}
    movable = set()
    for i, p in enumerate(pre):
        if vc.line_rule_faults(p):
            lo, hi = i, i
            while lo > 0 and not vc._ends_sentence(pre[lo - 1]) and i - lo < 2:
                lo -= 1
            while hi + 1 < len(pre) and not vc._ends_sentence(pre[hi]) and hi - i < 2:
                hi += 1
            movable |= {pos[id(w)] for q in pre[lo:hi + 1] for w in q}
    for p in new:
        assert {pos[id(w)] for w in p} <= movable, (label, [w["word"] for w in p])


@pytest.mark.parametrize("label,words,emph", CASES, ids=[c[0] for c in CASES])
def test_no_new_never_end_burst_end_bond_cut_or_short_burst(label, words, emph):
    pre = _pre(words, emph)
    phrases, _ = _layout(words, emph)
    # no burst ends mid-sentence on an article, preposition or conjunction that the gated phrasing did not
    assert set(vc.never_end_bursts(phrases)) <= set(vc.never_end_bursts(pre)), label
    # no cut inside a figure group, a figure and its unit, or a proper name that the gated phrasing did not make
    assert set(vc.figure_split_cuts(phrases)) <= set(vc.figure_split_cuts(pre)), label
    raw = [w["word"] for w in words]
    bonds = vc.unit_links(words) | vc.name_bonds(raw) | vc.caption_bonds(words)
    pos = {id(w): k for k, w in enumerate(words)}
    pre_cuts = {pos[id(p[-1])] for p in pre[:-1]}
    assert not {pos[id(p[-1])] for p in phrases[:-1]} - pre_cuts & bonds, label
    # D1: no more bursts up for less than CAPTION_MIN_ON_SCREEN_S than before
    def short(ph):
        return sum(h - s < vc.CAPTION_MIN_ON_SCREEN_S - 1e-6 for s, h, _ in vc.burst_timings(ph, dwell=True))
    assert short(phrases) <= short(pre), label


def test_the_class_closes_on_the_real_runs():
    """Over the real 9:16 cases: the pass leaves fewer LINE_RULE faults than the gated phrasing had, and every one
    left is recorded with no legal split (the first test). A lead-in word before a figure group or a name no line
    holds whole has no legal form at the caption sizes (recorded)."""
    before = after = 0
    for c in REEL:
        words, emph = _fx.words(c), list(c.get("emphasis_words") or [])
        before += sum(bool(vc.line_rule_faults(p)) for p in _pre(words, emph))
        after += sum(bool(vc.line_rule_faults(p)) for p in _layout(words, emph)[0])
    assert after < before


@pytest.mark.parametrize("case", WIDE, ids=[c["topic"] for c in WIDE])
def test_a_16_9_burst_is_one_line_and_the_pass_leaves_it(case):
    words, emph = _fx.words(case), list(case.get("emphasis_words") or [])
    phrases, rec = _layout(words, emph, True)
    assert rec == [] and [_key(p) for p in phrases] == [_key(p) for p in _pre(words, emph, True)]


def test_the_record_goes_where_the_caption_records_go(tmp_path):
    c = REEL[0]
    comp = vc.VideoComposer()
    comp.build_subtitles(_fx.words(c), tmp_path / "c.ass", emphasis=list(c.get("emphasis_words") or []))
    rates = comp.last_reading_rate
    rec = _layout(_fx.words(c), list(c.get("emphasis_words") or []))[1]
    assert rates["line_split"] == rec
    for e in rates["line_split"]:
        assert set(e) >= {"at", "text", "removed", "rule", "into"}


def test_a_legal_break_ranks_before_any_illegal_one_whatever_the_rung():
    """_two_line_options: among the breaks with no name split and no line defect, every LINE_RULE-legal one comes
    before every break after a qualifier or leaving a one-word line."""
    for _, words, emph in CASES:
        for p in vc.caption_phrases(words, emph):
            ws = tuple(w["word"] for w in p)
            shown = vc._phrase_words(list(ws))
            opts = [o for o in vc._two_line_options(ws) if not o[0][0] and not o[0][1]]
            ranks = [bool(vc._hedge_break(shown, k) or k == 1 or k == len(shown) - 1) for _, k, _, _ in opts]
            assert ranks == sorted(ranks), (ws, ranks)
