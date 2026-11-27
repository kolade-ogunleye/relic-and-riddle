"""Freeze cycle 5, TEXT stream, after the independent review of the first build (defects D4 to D9): the caption clip
size by majority, one line where one line fits, no clause-straddling join, the 0.8 s floor through the cut snap, the
clip's own forms (the registry reel_caption_lines reads) and the truth of the "no legal break" records.

Invariants over committed real caption words (tests/fixtures/text_c5: word, start, dur of two development runs); the
same measures docs/reviews/freeze-system-2026-11-15/corpus/text_captions.py --production runs over every dev run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "text_c5"
CLIPS = {name: json.loads((FIX / f"{name}_caption_words.json").read_text(encoding="utf-8"))
         for name in ("amber", "richat")}
GRID = 3.1   # s: the review's picture-cut grid (real cut lists are not stored with the runs)


def _words(name: str) -> list[dict]:
    return [dict(w) for w in CLIPS[name]["words"]]


def _raw(p) -> tuple[str, ...]:
    return tuple(str(w["word"]) for w in p)


def _cuts(words: list[dict]) -> list[float]:
    end = max(float(w["start"]) + float(w["dur"]) for w in words)
    out, t = [], GRID
    while t < end:
        out.append(round(t, 3))
        t += GRID
    return out


def _built(name: str, tmp_path, cuts: bool = False):
    """The 9:16 caption file as compose_video builds it: (bursts [(show, hide, size or None, lines)], record)."""
    words = _words(name)
    comp = vc.VideoComposer()
    ass = tmp_path / f"{name}.ass"
    comp.build_subtitles(words, ass, emphasis=CLIPS[name]["emphasis_words"],
                         first_frame_whole=vc.CAPTION_FIRST_FRAME_WHOLE_S, cuts=_cuts(words) if cuts else None)

    def ts(s: str) -> float:
        h, m, r = s.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(r)
    boxes, texts = [], []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        part = line[len("Dialogue:"):].split(",", 9)
        if part[3].strip() == "CapBox":
            boxes.append((ts(part[1]), ts(part[2])))
        elif part[3].strip() == "CapText":
            m = re.search(r"\\pos\(([-\d.]+),([-\d.]+)\)", part[9])
            f = re.search(r"\\fs(\d+)", part[9])
            texts.append((ts(part[1]), float(m.group(2)), int(f.group(1)) if f else None))
    bursts = []
    for show, hide in boxes:
        mine = [x for x in texts if show - 1e-3 <= x[0] < hide - 1e-3]
        bursts.append((show, hide, mine[0][2] if mine else None, len({x[1] for x in mine})))
    return bursts, comp.last_reading_rate


# ------------------------------------------------------------------------------------------ D4: the clip's size
def test_the_clip_size_is_the_largest_rung_the_large_majority_of_bursts_set_at():
    top, mid, low = vc.CAPTION_LADDER
    fits_top = ("the", "old", "dome")
    only_low = ("of", "Army", "Group", "North")
    assert vc._clip_form(fits_top, top) == ("one", 0)
    assert vc._clip_form(only_low, top) is None and vc._clip_form(only_low, mid) is None
    assert vc._clip_form(only_low, low) == ("one", 0)
    # one burst in ten off the size does not pull the clip down a rung; more than one in ten does
    size, shares = vc.caption_clip_rung([fits_top] * 9 + [only_low])
    assert size == top and shares[top] == pytest.approx(0.9) and shares[low] == 1.0
    assert vc.caption_clip_rung([fits_top] * 8 + [only_low] * 2)[0] == low
    assert vc.caption_clip_rung([]) == (top, {top: 1.0, mid: 1.0, low: 1.0})
    # a single word too long for the line sets no clip size (its own recorded shrink)
    assert vc.caption_clip_rung([fits_top, ("Podkamennaya-Tunguskaya-Nizhnyaya",)])[0] == top
    assert 0.5 < vc.CAPTION_CLIP_MAJORITY < 1.0


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_one_size_per_clip_and_every_other_size_is_recorded(name, tmp_path):
    bursts, rate = _built(name, tmp_path)
    clip = rate["clip_size"]
    assert clip["px"] in vc.CAPTION_LADDER and clip["majority"] == vc.CAPTION_CLIP_MAJORITY
    assert clip["shares"][str(clip["px"])] >= vc.CAPTION_CLIP_MAJORITY or clip["px"] == min(vc.CAPTION_LADDER)
    larger = [r for r in vc.CAPTION_LADDER if r > clip["px"]]
    assert all(clip["shares"][str(r)] < vc.CAPTION_CLIP_MAJORITY for r in larger), clip["shares"]
    want = None if clip["px"] >= vc.FONT_SIZE else round(clip["px"] / 0.5625)
    off = [b for b in bursts if b[2] != want]
    # the bursts at another size are exactly the recorded ones: never a silent step, never a whole clip pulled down
    assert len(off) == len(clip["under"]), (off, clip["under"])
    assert all(u["px"] < clip["px"] for u in clip["under"])
    assert len(off) <= (1 - vc.CAPTION_CLIP_MAJORITY) * len(bursts) + 1


def test_a_misfit_never_drags_the_clip_down_and_no_burst_of_several_words_stands_at_another_size(monkeypatch):
    """The Amber clip's one burst that sets only at the bond floor (OF ARMY GROUP NORTH) put the whole clip at 66 px
    under "the largest rung EVERY burst sets at"; under the majority rule the clip is not at the floor for it, and
    the burst is drawn at the clip's size like every other (its break recorded: no legal form)."""
    words = _words("amber")
    phrases = vc.caption_phrases(words, CLIPS["amber"]["emphasis_words"])
    clip, forms = vc._clip_state(), vc.clip_forms()
    assert clip["px"] > min(vc.CAPTION_LADDER)
    assert [w for p in phrases for w in p] == words
    assert set(forms) == {_raw(p) for p in phrases}
    assert {f[2] for raw, f in forms.items() if len(raw) > 1} == {clip["px"]}        # ONE size per clip
    assert not [u for u in clip["under"] if len(u["text"].split()) > 1], clip["under"]
    for row in clip["no_legal_form"]:   # each drawn against the rule is named with its fault
        assert " / " in row["text"] and row["faults"], row
    assert vc.CAPTION_MISFIT_OWN_SIZE is False
    # the measured alternative (not delivered): the misfit on its legal lines one rung down, recorded "under"
    monkeypatch.setattr(vc, "CAPTION_MISFIT_OWN_SIZE", True)
    vc.caption_phrases(words, CLIPS["amber"]["emphasis_words"])
    clip, forms = vc._clip_state(), vc.clip_forms()
    assert {f[2] for f in forms.values()} - {clip["px"]} == {u["px"] for u in clip["under"]}


# ------------------------------------------------------------------------------- D5: one line where one line fits
@pytest.mark.parametrize("name", sorted(CLIPS))
def test_a_burst_one_line_holds_at_its_size_is_set_on_one_line(name):
    phrases = vc.caption_phrases(_words(name), CLIPS[name]["emphasis_words"])
    budget = vc._ink_budget()
    for p in phrases:
        raw = _raw(p)
        kind, k, size = vc.clip_forms()[raw]
        one = vc._caption_ink_width(vc._phrase_words(list(raw)), size) <= budget
        assert (kind == "one") == one or len(raw) == 1, (raw, kind, size)
        assert (vc.reel_caption_lines(list(raw)) is None) == (kind == "one")


@pytest.mark.parametrize("text", ["over 590 square feet", "almost 50 kilometres", "at least 30 kilometres",
                                  "between 1404 and 1438", "reconstruction inside"])
def test_the_reviews_wrong_no_legal_break_cases_set_on_one_line_where_the_size_holds_them(text):
    """D5: these drew on two lines with a recorded fault ("no legal break") at a size ONE line holds them at."""
    raw = tuple(text.split())
    budget = vc._ink_budget()
    for size in vc.CAPTION_LADDER:
        form = vc._clip_form(raw, size)
        if vc._caption_ink_width(vc._phrase_words(list(raw)), size) <= budget:
            assert form == ("one", 0), (text, size, form)
        elif form is not None:   # two lines only on a legal break
            assert form[0] == "two" and not vc._break_faults(vc._phrase_words(list(raw)), form[1]), (text, size)
    assert vc._clip_form(raw, min(vc.CAPTION_LADDER)) == ("one", 0), text


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_no_legal_break_is_recorded_only_of_a_burst_drawn_with_the_fault(name, tmp_path):
    _, rate = _built(name, tmp_path)
    phrases = vc.caption_phrases(_words(name), CLIPS[name]["emphasis_words"])   # the same clip: the same forms

    def drawn(p) -> str:
        ws = list(_raw(p))
        lay = vc.reel_caption_lines(ws)
        return " ".join(ws[:lay[0]]) + " / " + " ".join(ws[lay[0]:]) if lay else " ".join(ws)
    faulty = {drawn(p) for p in phrases if vc.line_rule_faults(list(_raw(p)))}
    faulty |= {row["text"] for row in rate["clip_size"]["no_legal_form"] if row["faults"]}   # every break against a rule
    unsplit = {e["text"] for e in rate["line_split"] if e.get("into") is None}
    assert unsplit == faulty, (unsplit ^ faulty)
    budget = vc._ink_budget()
    for p in phrases:   # and no faulted two-line burst is one a single line would hold at its size
        raw = _raw(p)
        if vc.line_rule_faults(list(raw)):
            size = vc.clip_forms()[raw][2]
            assert vc._caption_ink_width(vc._phrase_words(list(raw)), size) > budget, raw


# ------------------------------------------------------------------------------------ D6: no clause-straddling join
@pytest.mark.parametrize("name", sorted(CLIPS))
def test_the_merge_joins_nothing_across_a_clause(name, monkeypatch):
    words = _words(name)
    after = [_raw(p) for p in vc.caption_phrases(words, CLIPS[name]["emphasis_words"])]
    monkeypatch.setattr(vc, "_readable_merge", lambda phrases, *a, **k: phrases)
    before = {_raw(p) for p in vc.caption_phrases(words, CLIPS[name]["emphasis_words"])}
    formed = [raw for raw in after if raw not in before]
    assert formed, "the fixture exercises the merge"
    for raw in formed:   # clause punctuation inside a burst the merge formed: only after a one-word lead-in
        inside = [w for w in raw[1:-1] if vc.CLAUSE_PUNCT.search(w) or vc.TERMINAL_PUNCT.search(w)]
        assert not inside, raw
    def straddles(raws) -> int:
        return sum(any(vc.CLAUSE_PUNCT.search(w) for w in raw[1:-1]) for raw in raws)
    assert straddles(after) <= straddles(before)             # the builder's own are cured where a re-cut can, never added
    texts = [" ".join(raw).lower() for raw in after]
    assert not any("expansions, the" in t for t in texts), texts   # the review's join (Amber): EXPANSIONS, THE ROOM


# ----------------------------------------------------------------------- D7: the 0.8 s floor through the cut snap
def test_a_cut_snap_never_takes_a_burst_under_the_floor(monkeypatch):
    phrases = [[{"word": "Seen", "start": 0.10, "dur": 0.30}, {"word": "clearly", "start": 0.45, "dur": 0.40}],
               [{"word": "from", "start": 1.00, "dur": 0.15}, {"word": "space", "start": 1.20, "dur": 0.30}]]
    timings = [(0.03, 0.93, [0.03, 0.38]), (0.93, 1.80, [0.93, 1.13])]
    cuts = [1.03]   # three frames after the hand-over: snapping it leaves the second burst up 0.77 s
    got = vc.snap_to_cuts(phrases, timings, cuts)
    assert all(hide - show >= vc.CAPTION_SPLIT_MIN_S - 1e-6 for show, hide, _ in got), got
    monkeypatch.setattr(vc, "CAPTION_SPLIT_MIN_S", 0.0)   # the guard reverted: the snap goes through
    loose = vc.snap_to_cuts(phrases, timings, cuts)
    assert loose[1][0] == pytest.approx(1.03) and loose[1][1] - loose[1][0] < 0.8


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_captions_after_cut_snapping_hold_the_floor_or_are_recorded(name, tmp_path):
    for cuts in (False, True):
        bursts, rate = _built(name, tmp_path, cuts=cuts)
        short = [b for b in bursts if b[1] - b[0] < vc.CAPTION_SPLIT_MIN_S - 0.011]   # the file's 10 ms clock
        kept = [e for e in rate["recut"] if e.get("kept_short")]
        assert len(kept) >= len(short), (cuts, short, kept)
        assert all(e["on_screen_s"] < vc.CAPTION_SPLIT_MIN_S and e["after_cut_snap"] == cuts for e in kept)
    # the snap itself made no burst short: with the grid no more bursts are under the floor than without it
    words = _words(name)
    phrases = vc.caption_phrases(words, CLIPS[name]["emphasis_words"])
    plain = vc.burst_timings(phrases, first_frame_whole=vc.CAPTION_FIRST_FRAME_WHOLE_S, dwell=True)
    snapped = vc.snap_to_cuts(phrases, plain, _cuts(words))
    assert snapped != plain, "the grid moves at least one boundary of this clip"
    for (s0, h0, _), (s1, h1, _) in zip(plain, snapped):
        assert h1 - s1 >= min(vc.CAPTION_SPLIT_MIN_S, h0 - s0) - 1e-6


# -------------------------------------------------------------------------------- D9: the hook burst stays its own
@pytest.mark.parametrize("name", sorted(CLIPS))
def test_the_merge_never_joins_into_the_burst_shown_whole_on_frame_zero(name, monkeypatch):
    words = _words(name)
    after = vc.caption_phrases(words, CLIPS[name]["emphasis_words"])
    monkeypatch.setattr(vc, "_readable_merge", lambda phrases, *a, **k: phrases)
    before = vc.caption_phrases(words, CLIPS[name]["emphasis_words"])
    if float(words[0]["start"]) - vc.CAPTION_LEAD_FRAMES / vc.VIDEO_FPS <= vc.CAPTION_FIRST_FRAME_WHOLE_S:
        assert len(after[0]) <= len(before[0]), (_raw(after[0]), _raw(before[0]))


# ------------------------------------------------------------------------------ the clip's forms (the registry)
def test_the_clips_forms_are_its_own_and_never_reach_the_builder_or_the_next_clip():
    probe = ("heavily", "fire-bombed", "Königsberg.")
    vc._reel_caches_clear()
    assert vc.clip_forms() == {} and vc._clip_state() is None
    pure = vc._reel_form_pure(probe)
    amber = vc.caption_phrases(_words("amber"), CLIPS["amber"]["emphasis_words"])
    forms = vc.clip_forms()
    assert set(forms) == {_raw(p) for p in amber} and vc._clip_state()["px"] in vc.CAPTION_LADDER
    for p in amber:   # reel_caption_lines answers with the clip's form for the clip's own bursts
        kind, k, size = forms[_raw(p)]
        assert vc.reel_caption_lines(p) == ((k, size) if kind == "two" else None)
    assert vc._reel_form_pure(probe) == pure                 # the pure form is no function of the clip
    again = vc.caption_phrases(_words("amber"), CLIPS["amber"]["emphasis_words"])
    assert [_raw(p) for p in again] == [_raw(p) for p in amber] and vc.clip_forms() == forms   # deterministic
    richat = vc.caption_phrases(_words("richat"), CLIPS["richat"]["emphasis_words"])
    assert set(vc.clip_forms()) == {_raw(p) for p in richat}                                   # replaced, not merged
    wide = vc.caption_phrases(_words("amber"), CLIPS["amber"]["emphasis_words"], True)
    assert wide and set(vc.clip_forms()) == {_raw(p) for p in richat}     # a 16:9 build neither reads nor clears it
    vc._reel_caches_clear()
    assert vc.clip_forms() == {} and vc._clip_state() is None
    assert vc.reel_caption_lines(list(probe)) == ((pure[1], pure[2]) if pure and pure[0] == "two" else None)


def test_without_the_merge_there_is_no_clip_fit_and_the_builder_forms_stand(monkeypatch):
    monkeypatch.setattr(vc, "_readable_merge", lambda phrases, *a, **k: phrases)
    phrases = vc.caption_phrases(_words("amber"), CLIPS["amber"]["emphasis_words"])
    assert vc._clip_state() is None and vc.clip_forms() == {}
    for p in phrases:
        pure = vc._reel_form_pure(_raw(p))
        assert vc.reel_caption_lines(p) == ((pure[1], pure[2]) if pure and pure[0] == "two" else None)


# -------------------------------------------- the delivered phrasing against the phrase builder's (the HEAD gate's)
def _gate_runs() -> dict:
    """The real runs of the HEAD 0036b18 regression gate (tests/test_freeze_captions_2026_11_04.py): their words."""
    import sys
    sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import fixtures as _fx
    return {c["topic"]: ([dict(w) for w in _fx.words(c)], list(c.get("emphasis_words") or []))
            for c in _fx.load("captions")}


GATE = _gate_runs()
DELIVERED = {**{name: (_words(name), CLIPS[name]["emphasis_words"]) for name in CLIPS},
             **{f"gate:{topic}": v for topic, v in GATE.items()}}


@pytest.mark.parametrize("name", sorted(DELIVERED))
def test_the_delivered_phrasing_adds_only_legal_recorded_cuts_to_the_builders(name, monkeypatch):
    """The older suites hold the PHRASE BUILDER's bursts (the HEAD 0036b18 gate, the caps): they switch the merge off.
    This holds the DELIVERED phrasing against the builder's, on the two clips above and every real run of the HEAD
    gate: a cut the delivered bursts have and the builder's do not parts no bond, name or unit, ends no burst on a
    never-end word, and comes from a recorded re-cut or split; every word keeps its place."""
    words, emphasis = [dict(w) for w in DELIVERED[name][0]], DELIVERED[name][1]
    recut: list = []
    after = vc.caption_phrases(words, emphasis, recut=recut)
    assert [w for p in after for w in p] == words
    monkeypatch.setattr(vc, "_readable_merge", lambda phrases, *a, **k: phrases)
    before = vc.caption_phrases(words, emphasis)

    def ends(phrases) -> set[int]:
        out, at = set(), -1
        for p in phrases[:-1]:
            at += len(p)
            out.add(at)
        return out
    raw = [w["word"] for w in words]
    new = ends(after) - ends(before)
    bonds = vc.unit_links(words) | vc.name_bonds(raw) | vc.caption_bonds(words)
    assert not new & bonds, [(raw[k], raw[k + 1]) for k in sorted(new & bonds)]
    assert not [raw[k] for k in new if vc.never_end_end(raw[k])]
    assert not new or [e for e in recut if e.get("recut") or e.get("into")]
    assert len(ends(before) - ends(after)) >= len(new)       # the merge joins more than it cuts


# ------------------------------------------------------ round 3: capacity-aware bursts, no plate-height pumping
def _heights(name: str, capacity: bool, monkeypatch) -> tuple[list, list, dict, list]:
    """The delivered bursts of a fixture clip with the capacity pass on or off: (bursts, their line counts at the clip's
    size, the clip record, the recut record)."""
    monkeypatch.setattr(vc, "CAPTION_CAPACITY_AWARE", capacity)
    words = _words(name)
    recut: list = []
    phrases = vc.caption_phrases(words, CLIPS[name]["emphasis_words"], recut=recut)
    clip = dict(vc._clip_state() or {})
    lines = [2 if vc.reel_caption_lines([w["word"] for w in p]) else 1 for p in phrases]
    return phrases, lines, clip, recut


def _height_changes(phrases, lines) -> int:
    timing = vc.burst_timings(phrases, dwell=True)
    return sum(a != b for (a, b), (t0, t1) in zip(zip(lines, lines[1:]), zip(timing, timing[1:]))
               if abs(t0[1] - t1[0]) < 1e-3)


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_capacity_aware_bursts_pump_the_plate_less_and_break_no_rule(name, monkeypatch):
    before, lines0, clip0, _ = _heights(name, False, monkeypatch)
    after, lines1, clip1, recut = _heights(name, True, monkeypatch)
    words = _words(name)
    assert [w["word"] for p in after for w in p] == [w["word"] for w in words]      # every word, in order
    assert clip1["px"] >= clip0["px"]                                                # never a smaller size for it
    assert _height_changes(after, lines1) < _height_changes(before, lines0)          # the plate pumps less
    raw = [w["word"] for w in words]

    def ends(phrases) -> set[int]:
        out, at = set(), -1
        for p in phrases[:-1]:
            at += len(p)
            out.add(at)
        return out
    new = ends(after) - ends(before)
    assert not [(raw[k], raw[k + 1]) for k in new if vc.noun_phrase_cut(raw, k)]      # no phrase unit parted anew
    assert not [raw[k] for k in new if vc.never_end_end(raw[k])]
    olds = {tuple(w["word"] for w in p) for p in before}
    timing = vc.burst_timings(after, dwell=True)
    for p, (show, hide, _) in zip(after, timing):
        key = tuple(w["word"] for w in p)
        if key not in olds:                                                          # a burst the pass made
            assert len(p) > 1 and hide - show >= vc.CAPTION_SPLIT_MIN_S - 1e-6, (key, hide - show)
    assert not new or [e for e in recut if e.get("height_run")]                      # every re-cut run recorded


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_every_two_line_burst_names_the_rule_that_keeps_it_on_two_lines(name, monkeypatch):
    after, lines, clip, _ = _heights(name, True, monkeypatch)
    named = {e["text"] for e in clip.get("two_line") or []} | {
        e["text"].replace(" / ", " ") for e in clip.get("no_legal_form") or []}
    for p, n in zip(after, lines):
        if n == 2:
            assert " ".join(w["word"] for w in p) in named, [w["word"] for w in p]
    for e in clip.get("two_line") or []:
        assert e["why"] and set(e["why"]) <= {"one_word_part", "cut_parts_a_bond_or_ends_on_a_never_end_word",
                                              "under_min_on_screen", "run_height", "phrase_unit",
                                              "part_not_one_line"}, e


def test_a_new_cut_never_parts_a_noun_phrase_over_two_modifiers():
    raw = "Near Ouadane a subsurface igneous intrusion deformed overlying rock".split()
    assert vc.noun_phrase_cut(raw, raw.index("igneous")) is not None
    assert vc.noun_phrase_cut(raw, raw.index("intrusion")) is None                    # subject | verb is a boundary


# ------------------------------------------------------------------- round 4: the shipped defaults; D9 on record
def test_the_shipped_caption_defaults_are_pinned():
    """Second review T1: the capacity pass and the clip's majority are the shipped behaviour (lander rulings R1 / R2,
    2026-11-26); turning either off fails here, whatever the other tests set for themselves."""
    assert vc.CAPTION_CAPACITY_AWARE is True
    assert vc.CAPTION_CLIP_MAJORITY == 0.9
    assert vc.CAPTION_RUN_ONE_WORD is False and vc.CAPTION_SIZE_AFTER_RUNS is False
    # and the delivered phrasing is the capacity pass's (the default call, no flag set by the test)
    words = _words("amber")
    phrases = vc.caption_phrases(words, CLIPS["amber"]["emphasis_words"])
    lines = [2 if vc.reel_caption_lines([w["word"] for w in p]) else 1 for p in phrases]
    orig = vc._capacity_pass
    try:
        vc._capacity_pass = lambda phrases, *a, **k: [list(p) for p in phrases]
        off = vc.caption_phrases(_words("amber"), CLIPS["amber"]["emphasis_words"])
    finally:
        vc._capacity_pass = orig
    off_lines = [2 if vc.reel_caption_lines([w["word"] for w in p]) else 1 for p in off]
    assert _height_changes(phrases, lines) < _height_changes(off, off_lines)


def _hook_words() -> list[dict]:
    # labelled synthetic narration, timed like a real hook whose fourth word is spoken at 0.6 s (Nazca: HOW DO YOU ETCH)
    text = "How do you etch massive desert lines without flying above them? Archaeologists still debate the answer."
    out, at = [], 0.0
    for w in text.split():
        dur = 0.18 if len(w) < 5 else 0.34
        out.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + (0.45 if w.endswith("?") or w.endswith(".") else 0.02)
    return out


def test_a_hook_shown_whole_records_every_word_shown_early(tmp_path):
    """Second review D9 / M1: the hook burst shown whole on frame 0 shows a later word before it is heard; every word
    on screen more than CAPTION_EARLY_RECORD_S early is in reading_rates["hook_whole_early"]."""
    words = _hook_words()
    comp = vc.VideoComposer()
    comp.build_subtitles([dict(w) for w in words], tmp_path / "h.ass", first_frame_whole=vc.CAPTION_FIRST_FRAME_WHOLE_S)
    rec = comp.last_reading_rate.get("hook_whole_early")
    assert rec is not None
    first = vc.caption_phrases([dict(w) for w in words], None)[0]
    late = [w["word"] for w in first if float(w["start"]) > vc.CAPTION_EARLY_RECORD_S]
    assert late and [e["word"] for e in rec] == late
    assert all(e["early_ms"] > 250 for e in rec)
