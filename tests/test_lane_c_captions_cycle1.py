"""Design-freeze lane C (captions), cycle 1: the carry-in N1 and N2 (queue LANE-C-carry-in-r8i.md, from the final
round-8 adversarial check), closed by class in src/video_composer.py:

- N1: a 9:16 two-line burst's line 1 never ends on the never-end set (never_end_end, the one closed class every burst
  end reads), and a one-word line is set only where no break without one is valid (_two_line_options ranks a line
  defect before the rung, then a one-word line after the name and dangling tiers).
- N2: a figure never leaves its unit across a line or a burst (unit_link: 830 SQUARE, SQUARE MILES, 15 MEGATONS),
  never loosened for the round-4 base nor released for a figure group; a break after a whole hedge phrase keeps the
  quantifier phrase and the value whole (_hedge_break: MORE THAN / 830 SQUARE MILES). A never-end end the repair
  (_never_end_repair) cannot remove stays only where no valid break exists in its window, and every record names it
  (reading_rates "never_end" and "line_defect").

Tests are invariants over the real runs' bursts (the freeze-panel catalogue's caption cases with their own words.json
timings), per burst and per line, and over labelled synthetic narrations: never pinned breaks. The caption words stay
the narration's on every path; pace is recorded, never forced (no voice stretch).
"""
from __future__ import annotations

import itertools
import re
import sys
from pathlib import Path

import pytest

from src import video_composer as vc


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
import fixtures as _fx

REAL = [c for c in _fx.load("captions")]


def _timed(text: str) -> list[dict]:
    words, at = [], 0.0
    for w in text.split():
        dur = round(0.11 + 0.055 * len(re.sub(r"[^\w]", "", w)), 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.49 if re.search(r"[.!?]$", w) else 0.22 if re.search(r"[,;:]$", w) else 0.04)
    return words


# Synthetic narrations (labelled: test texts written for these rules, not scripts), the AD's classes in other words
SYNTH = (
    "The blast flattened more than 830 square miles of remote forest in a single morning. "
    "German soldiers of Army Group North took the panels apart in 36 hours. "
    "Its yield was between 10 and 15 megatons of TNT, by most accounts. "
    "The chamber covered more than 590 square feet and held over 13,000 pounds of amber. "
    "Crews hauled from nearly 1,090 tonnes to 1,168 tonnes of stone each year."
)


def _cases():
    for c in REAL:
        yield f"{c['topic']} {c['format']}", _fx.words(c), list(c.get("emphasis_words") or []), c["format"] == "16:9"
    for wide in (False, True):
        yield f"synthetic {'16:9' if wide else '9:16'}", _timed(SYNTH), [], wide


CASES = list(_cases())
_CACHE: dict = {}


def _phrases(name, words, emph, wide):
    """The delivered phrasing, its clip's forms current (freeze cycle 5 round 4, T2: a 9:16 clip's burst forms are the
    clip's own, video_composer.clip_forms, read by reel_caption_lines until the next clip is built)."""
    if name not in _CACHE:
        phrases = vc.caption_phrases(words, emph or None, wide)
        _CACHE[name] = (phrases, vc.clip_forms())
    phrases, forms = _CACHE[name]
    reg = vc._joined_raws()
    reg.clear()
    reg.update(forms)
    return phrases


def _cuts(phrases):
    out, at = [], -1
    for p in phrases[:-1]:
        at += len(p)
        out.append(at)
    return out


@pytest.mark.parametrize("name,words,emph,wide", CASES, ids=[c[0] for c in CASES])
def test_caption_words_are_the_narration(name, words, emph, wide):
    phrases = _phrases(name, words, emph, wide)
    assert [w for p in phrases for w in p] == words


@pytest.mark.parametrize("name,words,emph,wide", CASES, ids=[c[0] for c in CASES])
def test_no_burst_and_no_line_splits_a_value_from_its_unit(name, words, emph, wide):
    phrases = _phrases(name, words, emph, wide)
    raw = [w["word"] for w in words]
    for k in _cuts(phrases):
        assert not vc.unit_link(raw[k], raw[k + 1]), (name, raw[max(0, k - 3):k + 3])
    if wide:
        return
    for p in phrases:
        lines = vc.reel_caption_lines(p)
        if lines:
            shown = vc._phrase_words(p)
            assert not vc.unit_link(shown[lines[0] - 1], shown[lines[0]]), (name, shown)


def test_unit_link_is_the_figure_and_its_unit():
    assert vc.unit_link("830", "square") and vc.unit_link("square", "miles") and vc.unit_link("15", "megatons")
    assert vc.unit_link("36", "hours") and vc.unit_link("590", "square")
    assert not vc.unit_link("1941,", "German") and not vc.unit_link("1941", "in") and not vc.unit_link("miles", "of")
    assert not vc.unit_link("830,", "square")   # punctuation ends the figure's phrase


def _record(phrases, wide):
    return vc.reading_rates(phrases, vc.burst_timings(phrases, dwell=not wide), widescreen=wide)


def _line_text(p, k):
    ws = [w["word"] for w in p]
    return " ".join(ws[:k]) + " / " + " ".join(ws[k:])


@pytest.mark.parametrize("name,words,emph,wide", [c for c in CASES if not c[3]], ids=[c[0] for c in CASES if not c[3]])
def test_every_line_defect_is_recorded(name, words, emph, wide):
    """Each 9:16 two-line burst whose line 1 ends on a never-end word or splits a figure from its unit (no hedge break)
    is in the composer's record, and the record names nothing else."""
    phrases = _phrases(name, words, emph, wide)
    found = []
    for p in phrases:
        lines = vc.reel_caption_lines(p)
        if lines and vc._line_break_defect(vc._phrase_words(p), lines[0]):
            found.append(_line_text(p, lines[0]))
    assert _record(phrases, wide)["line_defect"] == found


def _relaxed(ph) -> bool:
    shown = vc._phrase_words(ph)
    return (2 <= len(ph) <= vc.MAX_BURST_WORDS + vc.CAPTION_BOND_EXTRA_WORDS
            and len(" ".join(shown)) <= vc.MAX_BURST_CHARS + vc.CAPTION_BOND_EXTRA_CHARS and not vc._function_only(ph)
            and not vc.reel_line_defect(ph)
            and (vc._fits_caption_burst(ph) or vc._caption_ink_width(shown, vc.CAPTION_BOND_MIN_SIZE) <= vc._ink_budget()))


def _valid_window(phrases, i, words) -> list | None:
    """A re-cut of burst i with its neighbours in its sentence (any number of bursts) whose every burst holds the
    repair's widest caps (_relaxed: no line defect) and whose every cut is valid (no bond, unit or atomic pair, no
    never-end end, the window's last cut too): written here from the rules, not from the repair's own search."""
    raw = [w["word"] for w in words]
    protected = vc.name_bonds(raw) | vc.caption_bonds(words) | vc.unit_links(words)
    pos = {id(w): k for k, w in enumerate(words)}
    lo = i - 1 if i > 0 and not vc._ends_sentence(phrases[i - 1]) else i
    hi = i + 1 if i + 1 < len(phrases) and not vc._ends_sentence(phrases[i]) else i
    flat = [w for p in phrases[lo:hi + 1] for w in p]
    after = phrases[hi + 1] if hi + 1 < len(phrases) and not vc._ends_sentence(phrases[hi]) else None

    def cut_ok(a, b):
        return (pos[id(a[-1])] not in protected and not vc.never_end_end(a[-1]["word"])
                and not vc._is_atomic_boundary(a[-1]["word"], b[0]["word"]))
    for m in range(1, len(flat) + 1):
        for cuts in itertools.combinations(range(1, len(flat)), m - 1):
            bounds = (0, *cuts, len(flat))
            parts = [flat[a:b] for a, b in itertools.pairwise(bounds)]
            if all(_relaxed(p) for p in parts) and all(cut_ok(a, b) for a, b in itertools.pairwise(parts)) and (
                    after is None or cut_ok(parts[-1], after)):
                return [" ".join(w["word"] for w in p) for p in parts]
    return None


@pytest.mark.parametrize("name,words,emph,wide", CASES, ids=[c[0] for c in CASES])
def test_a_never_end_end_or_line_stays_only_where_no_valid_break_exists(name, words, emph, wide):
    forced: list = []
    if not wide:   # the delivered clip's record of bursts drawn on a forced break (no legal form, no legal split)
        vc.caption_phrases([dict(w) for w in words], emph or None, wide, line_split=forced)
    phrases = _phrases(name, words, emph, wide)
    recorded = {" ".join(e["text"].replace(" / ", " ").split()) for e in forced if e.get("into") is None}
    rec = _record(phrases, wide)
    assert rec["never_end"] == vc.never_end_bursts(phrases)
    for i, p in enumerate(phrases):
        burst_end = i + 1 < len(phrases) and not vc._ends_sentence(p) and vc.never_end_end(p[-1]["word"])
        line = not wide and vc.reel_line_defect(p)
        if line and not burst_end and " ".join(w["word"] for w in p) in recorded:
            # lander ruling 2026-11-26 (round 4, delivered phrasing): a line defect the clip draws on a forced break
            # is recorded there; the relaxed window below predates the clause rule (D6: no burst straddles a
            # clause) and the phrase units, which its windows break (BURST, THOUGH NO CELESTIAL | METEOR AIR)
            continue
        if burst_end or line:
            assert _valid_window(phrases, i, words) is None, (name, [w["word"] for w in p], _valid_window(
                phrases, i, words))


@pytest.mark.parametrize("name,words,emph,wide", [c for c in CASES if not c[3]], ids=[c[0] for c in CASES if not c[3]])
def test_a_one_word_line_only_where_no_break_without_one_is_valid(name, words, emph, wide):
    """A two-line burst sets a one-word line only where every break at its rung without one ends line 1 on a never-end
    word, splits a name or a unit, or dangles (N1: DISASSEMBLED / THE AMBER ROOM has no other break)."""
    for p in _phrases(name, words, emph, wide):
        lines = vc.reel_caption_lines(p)
        if not lines:
            continue
        shown = vc._phrase_words(p)
        k, rung = lines
        if k != 1 and k != len(shown) - 1:
            continue
        for rank, kk, rr, _ in vc._two_line_options(tuple(w["word"] for w in p)):
            if rr != rung or kk in (1, len(shown) - 1):
                continue
            assert rank[0] or rank[1] or rank[3], (name, shown, k, kk)   # a name split, a line defect, a dangle


def test_a_name_is_a_bond_before_the_rung_and_the_line_defect():
    """Cycle 2 (lander ruling 2): the lead-in and the whole name on one line down to the bond floor (OF ARMY GROUP
    NORTH at 66) before a name splits; where no line holds them (WHEN KÖNIGSBERG CASTLE: 663 px at 66) the name stays
    whole on line 2 and the lead-in line is a recorded line defect."""
    assert vc.reel_floor_line(["of", "Army", "Group", "North"]) and not vc.reel_name_split(["of", "Army", "Group", "North"])
    lines = vc.reel_caption_lines(["when", "Königsberg", "Castle"])
    assert lines == (1, vc.FONT_SIZE) and not vc.reel_name_split(["when", "Königsberg", "Castle"])


@pytest.mark.parametrize("name,words,emph,wide", CASES, ids=[c[0] for c in CASES])
def test_no_burst_and_no_line_splits_a_proper_name_unless_recorded(name, words, emph, wide):
    """A proper name (name_bonds: the narration's own capitalised runs, not sentence starts; no topic list) is never
    cut between bursts on either path, and a 9:16 line splits one only where the record names it."""
    phrases = _phrases(name, words, emph, wide)
    named = vc.name_bonds([w["word"] for w in words])
    raw = [w["word"] for w in words]
    for k in _cuts(phrases):
        assert k not in named, (name, raw[max(0, k - 3):k + 3])
    if wide:
        return
    split = [_line_text(p, vc.reel_caption_lines(p)[0]) for p in phrases if vc.reel_name_split(p)]
    assert _record(phrases, wide)["name_split"] == split


def test_a_break_after_a_whole_hedge_keeps_the_value_whole():
    assert not vc._line_break_defect(["more", "than", "830", "square", "miles"], 2)
    assert not vc._line_break_defect(["from", "nearly", "1,090", "tonnes"], 2)
    assert vc._line_break_defect(["more", "than", "830", "square", "miles"], 3)        # 830 / SQUARE
    assert vc._line_break_defect(["about", "40", "km", "across"], 1)                # a one-word never-end hedge
    lines = vc.reel_caption_lines(["more", "than", "830", "square", "miles"])
    assert lines is not None and lines[0] == 2, lines


def test_the_composer_record_names_the_drawn_line_defects(tmp_path):
    """The ASS build_subtitles writes: every 9:16 burst whose line 1 (its own CapText event at its own y) ends on a
    never-end word outside a hedge break is in last_reading_rate["line_defect"] (the record reads what is drawn)."""
    case = next(c for c in REAL if c["format"] == "9:16" and c["topic"] == "tunguska_event_1908")
    comp = vc.VideoComposer()
    ass = comp.build_subtitles(_fx.words(case), tmp_path / "t.ass", emphasis=list(case.get("emphasis_words") or []))
    boxes, texts = [], []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line[len("Dialogue:"):].split(",", 9)
        start, end, style, text = parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[9]
        y = re.search(r"\\pos\(\s*[-\d.]+\s*,\s*([-\d.]+)\s*\)", text)
        if style == "CapBox":
            boxes.append((start, end))
        elif style == "CapText":
            texts.append((start, float(y.group(1)) if y else 0.0, " ".join(re.sub(r"\{[^}]*\}", "", text).split())))
    drawn = []
    for s, e in boxes:
        at_y = {}
        for st, y, t in texts:
            if s <= st < e:
                at_y[y] = t
        lines = [at_y[y] for y in sorted(at_y) if at_y[y]]
        if len(lines) == 2:
            l1, l2 = lines[0].split(), lines[1].split()
            if vc._line_break_defect(l1 + l2, len(l1)):
                drawn.append(lines[0] + " / " + lines[1])
    rec = comp.last_reading_rate["line_defect"]

    def norm(t: str) -> str:
        return " ".join(re.sub(r"[^\w/ ]", "", t).lower().split())
    assert [norm(d) for d in drawn] == [norm(r) for r in rec], (drawn, rec)


@pytest.mark.parametrize("name,words,emph,wide", [c for c in CASES if not c[3]], ids=[c[0] for c in CASES if not c[3]])
def test_every_burst_over_the_caps_is_a_bond_or_in_the_bond_extra_record(name, words, emph, wide):
    """Ruling 5 (lander, 2026-11-20): the bond extra (+1 word / +2 characters) is used on a 9:16 burst only where it
    removes a defect, and is recorded. Every burst past MAX_BURST_WORDS or MAX_BURST_CHARS is either one bonded group
    (every boundary inside it a bond: a name with its lead-in, a figure with its hedge, range or unit) or in the
    record, with the defect it removed; the record names nothing within the caps."""
    log: list = []
    phrases = vc.caption_phrases(words, emph or None, wide, bond_extra=log)
    assert phrases == _phrases(name, words, emph, wide)
    pos = {id(w): k for k, w in enumerate(words)}
    held = vc.caption_bonds(words) | vc.unit_links(words)
    logged = {e["text"] for e in log}
    for p in phrases:
        if not vc.over_caps(p):
            continue
        bonded = all(pos[id(p[j])] in held for j in range(len(p) - 1))
        # lane C cycle 3 (lander ruling: a figure group is a bond): a whole figure group set on two lines
        bonded = bonded or vc._figure_group([w["word"] for w in p])
        # lander ruling 2026-11-26 (round 4, delivered phrasing): or a joined two-line burst of the readable merge / capacity pass,
        # each line within one burst's caps (the merge's records are held by tests/test_text_captions_c5b.py)
        raw = [w["word"] for w in p]
        lay = vc.reel_caption_lines(raw)
        joined = bool(lay) and all(
            len(x) <= vc.MAX_BURST_WORDS and len(" ".join(x)) <= vc.MAX_BURST_CHARS
            for x in (vc._phrase_words(raw[:lay[0]]), vc._phrase_words(raw[lay[0]:])))
        assert bonded or joined or " ".join(w["word"] for w in p) in logged, (name, raw)
    for e in log:
        assert e["removed"] in ("never_end_end", "line_defect", "lone_group_tail") and e["was"]
        assert any(" ".join(w["word"] for w in p) == e["text"] and vc.over_caps(p) for p in phrases), e
    assert _record(phrases, wide)["bond_extra"] == []   # reading_rates names only what it is given
    rec = vc.reading_rates(phrases, vc.burst_timings(phrases, dwell=True), bond_extra=log)
    assert rec["bond_extra"] == log


def test_the_repair_records_a_burst_it_forms_with_the_bond_extra():
    """The relaxed tier's record, driven directly: a never-end end only a burst past the caps removes is joined and
    named with the defect it removed (the real runs need none today: their records are empty)."""
    words = _timed("Echoes travel across the hall.")
    log: list = []
    out = vc._never_end_repair([words[:3], words[3:]], words, lambda p: False,
                               relaxed=lambda p: len(p) == vc.MAX_BURST_WORDS + vc.CAPTION_BOND_EXTRA_WORDS,
                               bond_extra=log)
    assert out == [words] and vc.over_caps(words)
    assert log == [{"at": 0.0, "text": "Echoes travel across the hall.", "removed": "never_end_end",
                    "was": "Echoes travel across"}]
