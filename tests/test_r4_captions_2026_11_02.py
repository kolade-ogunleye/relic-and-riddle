"""Caption panel round 4 (2026-11-02): a hedge stays in one burst, cue and line with its figure, a range keeps both
ends together, attribution hedges take a key term's gold, a still 16:9 line carries one gold span, a Reel's first
burst is whole from frame 0 when the narration starts early, and 9:16 bursts snap their show and hide to picture
cuts."""
import re
from pathlib import Path

import pytest

from src.config import (
    CAPTION_CUT_MIN_FRAMES,
    CAPTION_CUT_SNAP_FRAMES,
    CAPTION_FIRST_FRAME_WHOLE_S,
    VIDEO_FPS,
)
from src.video_composer import (
    CAPTION_HIDDEN,
    CAPTION_LEAD_FRAMES,
    COL_EMPHASIS,
    VideoComposer,
    _wrap_cue,
    burst_timings,
    caption_bonds,
    caption_cut_times,
    emphasis_indices,
    hedge_figures,
    partition_sidecar_cues,
    partition_words_grammatical,
    reading_need,
    single_gold_span,
    snap_to_cuts,
)

LEAD = CAPTION_LEAD_FRAMES / VIDEO_FPS
FRAME = 1 / VIDEO_FPS


def _timed(text, step=0.36, dur=0.32, t0=0.0, sentence_pause=0.0):
    words, t = [], t0
    for w in text.split():
        words.append({"word": w, "start": round(t, 3), "dur": dur})
        t += step + (sentence_pause if w.endswith((".", "?", "!")) else 0.0)
    return words


def _events(ass_text, style):
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and f",{style}," in line:
            parts = line.split(",", 9)
            h, m, s = parts[1].split(":")
            h2, m2, s2 = parts[2].split(":")
            out.append((int(h) * 3600 + int(m) * 60 + float(s), int(h2) * 3600 + int(m2) * 60 + float(s2), parts[9]))
    return out


def _gold(text):
    """The words drawn gold in an event (a word's first colour; small-caps runs are one word)."""
    out = []
    for token in text.split(CAPTION_HIDDEN)[0].split(" "):
        colours = re.findall(r"\\c(&H[0-9A-F]+&)", token)
        plain = re.sub(r"\{[^}]*\}", "", token).strip()
        if plain and colours and colours[0] == COL_EMPHASIS:
            out.append(plain)
    return out


def _bonded_words(text):
    words = text.split()
    return {(words[i], words[i + 1]) for i in caption_bonds(words)}


# ---------------------------------------------------------------- hedges and ranges are bonds
@pytest.mark.parametrize("text,pairs", [
    ("Derinkuyu reaches about 280 feet deep", {("about", "280")}),
    ("Derinkuyu reaches roughly 280 feet deep", {("roughly", "280")}),
    ("Up to 20,000 people could shelter", {("Up", "to"), ("to", "20,000")}),
    ("more than twenty thousand people", {("more", "than"), ("than", "twenty"), ("twenty", "thousand")}),
    ("It spans 94 to 104 feet", {("94", "to"), ("to", "104")}),
    ("It spans 94 feet to 104 feet", {("94", "feet"), ("feet", "to"), ("to", "104")}),
    ("It was between 94 and 104 metres long", {("between", "94"), ("94", "and"), ("and", "104")}),
    ("Built circa 1200 by masons", {("circa", "1200")}),
    ("It held at least 3,000 people", {("at", "least"), ("least", "3,000")}),
    ("Built c. 1200 by masons", {("c.", "1200")}),
])
def test_a_hedge_and_its_figure_and_both_ends_of_a_range_are_bonded(text, pairs):
    assert pairs <= _bonded_words(text)


@pytest.mark.parametrize("text", [
    "They climbed over the wall at night",
    "and some of them fled the valley",
    "It led up to the gate and beyond",
    "It reaches roughly, 280 feet down",          # never across punctuation
    "It was about. 280 feet below",
])
def test_hedge_words_split_freely_where_no_figure_follows(text):
    words = text.split()
    hedge_at = [i for i, w in enumerate(words) if w.strip(",.").lower() in {"over", "some", "to", "roughly", "about", "up"}]
    assert not set(hedge_at) & caption_bonds(words)


def test_bonds_read_timed_words_and_plain_strings_alike():
    text = "Derinkuyu reaches roughly 280 feet deep"
    assert caption_bonds(text.split()) == caption_bonds(_timed(text))


# ---------------------------------------------------------------- 9:16 and 16:9 bursts keep the bonds
@pytest.mark.parametrize("text", [
    "Derinkuyu reaches roughly 280 feet deep below the plain.",
    "Up to 20,000 people could shelter here with their animals.",
    "Its walls measure between 94 and 104 metres around the base.",
    "They removed an estimated 200,000 tons of rock from the cliff.",
])
@pytest.mark.parametrize("max_chars", [26, 32, 42])
def test_no_burst_ends_inside_a_bond_under_tight_widths(text, max_chars):
    from src.video_composer import _relax_figure_bonds
    words = _timed(text)
    for fits in (None, lambda ph: len(" ".join(w["word"] for w in ph)) <= max_chars):
        kw = {"fits": fits} if fits else {}
        phrases = partition_words_grammatical(words, 2, 3, 4, max_chars, **kw)
        # freeze pass C7 (2026-11-04): with the Reel's own widths a figure group no burst can hold at the bond
        # floor is released from its range's BETWEEN first (MEASURE BETWEEN | 94 AND 104 METRES)
        bonds = _relax_figure_bonds(words, caption_bonds(words)) if fits is None else caption_bonds(words)
        ends = []
        n = 0
        for p in phrases[:-1]:
            n += len(p)
            ends.append(n - 1)
        assert not set(ends) & bonds, [" ".join(w["word"] for w in p) for p in phrases]


def test_widescreen_and_reel_bursts_never_leave_a_bare_figure(tmp_path):
    words = _timed("Derinkuyu reaches roughly 280 feet deep. Up to 20,000 people could shelter here.", sentence_pause=0.6)
    for kw in ({}, {"width": 1920, "height": 1080}):
        ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["280 feet"], **kw).read_text("utf-8")
        lines = [re.sub(r"\{[^}]*\}", "", t).split(CAPTION_HIDDEN)[0] for _, _, t in _events(ass, "CapText")]
        full = [re.sub(r"\{[^}]*\}", "", t) for _, _, t in _events(ass, "CapText")]
        for line in full:
            up = line.upper().split()
            assert not (up and up[-1] in {"ROUGHLY", "TO", "UP"}), line
            if "280" in up:
                assert "ROUGHLY" in up
            if "20,000" in up:
                assert "UP" in up and "TO" in up
        assert lines


def test_an_attribution_hedge_stays_in_the_burst_of_its_key_term(tmp_path):
    words = _timed("Its builders were possibly Phrygian settlers from the west.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Phrygian"]).read_text("utf-8")
    for _, _, t in _events(ass, "CapText"):
        plain = re.sub(r"\{[^}]*\}", "", t).replace(CAPTION_HIDDEN, "").split()
        assert ("POSSIBLY" in plain) == ("PHRYGIAN" in plain)


# ---------------------------------------------------------------- sidecar cues keep the bonds
def test_a_sidecar_cue_never_ends_on_a_hedge_before_its_figure():
    # 'roughly' falls at the 42-character line limit: it goes on with 280 feet
    words = _timed("The underground city of Derinkuyu reaches roughly 280 feet deep below the plain of central "
                   "Anatolia, and up to 20,000 people could shelter in it.", step=0.3)
    cues = partition_sidecar_cues(words)
    for _, _, text in cues:
        flat = text.replace("\n", " ").split()
        assert flat[-1].lower() not in {"roughly", "up", "to"}, cues
        if "280" in flat:
            assert "roughly" in flat
        if "20,000" in flat:
            assert "up" in flat and "to" in flat
    assert " ".join(t.replace("\n", " ") for _, _, t in cues).split() == [w["word"] for w in words]


def test_a_cue_line_never_breaks_between_a_hedge_and_its_figure():
    words = ["Derinkuyu", "reaches", "roughly", "280", "feet", "deep", "below", "the", "plain"]
    # freeze pass C12 (2026-11-04): a line never breaks inside an atomic pair either (280 | FEET), so the only
    # break is after FEET and the lines are 36 wide (was 30: ROUGHLY 280 / FEET DEEP ...)
    text = _wrap_cue(words, max_chars=36)
    assert text is not None
    for line in text.split("\n"):
        assert line.split()[-1] != "roughly"
    assert _wrap_cue(words, max_chars=36, bonds=set()) is not None
    assert _wrap_cue(["a", "b", "roughly", "280"], max_chars=10, bonds={2}) is None or "roughly\n" not in _wrap_cue(
        ["a", "b", "roughly", "280"], max_chars=10, bonds={2})


def test_sidecar_cues_without_hedges_are_unchanged():
    from src.video_composer import SIDECAR_MAX_LINE_CHARS
    words = _timed("Beneath Cappadocia lies a city carved by hand. It was forgotten for centuries, then found again "
                   "by a man knocking down a wall.", step=0.33)
    assert caption_bonds(words) == set()
    cues = partition_sidecar_cues(words)
    assert all(len(line) <= SIDECAR_MAX_LINE_CHARS for _, _, t in cues for line in t.split("\n"))


# ---------------------------------------------------------------- gold: hedges, ranges, attribution
def _emph(text, terms):
    words = [{"word": w} for w in text.split()]
    return [words[i]["word"].upper().strip(".,") for i in sorted(emphasis_indices(words, terms))]


def test_gold_carries_about_with_280_feet():
    assert _emph("Derinkuyu reaches about 280 feet deep.", ["280 feet"]) == ["ABOUT", "280", "FEET"]


def test_gold_takes_both_ends_of_a_range():
    assert _emph("The walls run 94 to 104 feet.", ["94"]) == ["94", "TO", "104", "FEET"]
    assert _emph("The walls run 94 to 104 feet.", ["104 feet"]) == ["94", "TO", "104", "FEET"]


def test_gold_takes_between_and_both_ends():
    assert _emph("It was between 94 and 104 metres long.", ["94"]) == ["BETWEEN", "94", "AND", "104", "METRES"]


def test_gold_takes_an_attribution_hedge_with_a_key_term():
    assert _emph("Its builders were possibly Phrygian.", ["Phrygian"]) == ["POSSIBLY", "PHRYGIAN"]
    assert _emph("It was built by possibly the Phrygians.", ["Phrygians"]) == ["POSSIBLY", "THE", "PHRYGIANS"]
    assert _emph("They may have been Phrygian.", ["Phrygian"]) == ["MAY", "HAVE", "BEEN", "PHRYGIAN"]
    assert _emph("A Phrygian city, possibly.", ["Phrygian"]) == ["PHRYGIAN"]      # a hedge after it is not taken


def test_figure_hedges_do_not_attach_to_names():
    assert _emph("They spoke about Derinkuyu often.", ["Derinkuyu"]) == ["DERINKUYU"]
    assert _emph("It was over Derinkuyu.", ["Derinkuyu"]) == ["DERINKUYU"]


def test_an_attribution_hedge_waits_for_its_term_in_the_reel(tmp_path):
    words = _timed("Its builders were possibly Phrygian.")
    assert hedge_figures(words, ["Phrygian"]) == {3: 4}
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Phrygian"]).read_text("utf-8")
    seen = 0
    for _, _, t in _events(ass, "CapText"):
        shown = re.sub(r"\{[^}]*\}", "", t.split(CAPTION_HIDDEN)[0]).split()
        if "POSSIBLY" in shown:
            seen += 1
            assert ("POSSIBLY" in _gold(t)) == ("PHRYGIAN" in shown)
    assert seen >= 2


# ---------------------------------------------------------------- 16:9: one gold span a line
def test_single_gold_span_keeps_the_later_figure_with_its_hedge():
    shown = ["Derinkuyu", "reaches", "roughly", "280", "feet", "deep"]
    assert single_gold_span(shown, [0, 2, 3, 4]) == [2, 3, 4]
    assert single_gold_span(shown, [0]) == [0]
    assert single_gold_span(shown, []) == []
    assert single_gold_span(["Kailasa", "and", "Ellora"], [0, 2]) == [0]          # no figure: the first
    assert single_gold_span(["280", "feet", "and", "20,000", "people"], [0, 1, 3]) == [3]  # the later figure


def test_a_widescreen_line_draws_one_gold_span(tmp_path):
    words = _timed("Beneath Cappadocia lies a city carved by hand. Derinkuyu reaches roughly 280 feet deep.",
                   sentence_pause=0.5)
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass", emphasis=["Derinkuyu", "280 feet"],
                                          width=1920, height=1080).read_text("utf-8")
    golds = [_gold(t) for _, _, t in _events(ass, "CapText")]
    assert ["roughly", "280", "feet"] in golds
    assert all("Derinkuyu" not in g for g in golds if "280" in g)
    # the Reel keeps both key terms (its gold arrives word by word, one at a time)
    reel = VideoComposer().build_subtitles(words, tmp_path / "r.ass", emphasis=["Derinkuyu", "280 feet"]).read_text("utf-8")
    assert any("DERINKUYU" in _gold(t) for _, _, t in _events(reel, "CapText"))


# ---------------------------------------------------------------- 9:16 first frame
def test_an_early_first_burst_is_whole_from_zero():
    phrases = [[{"word": "Beneath", "start": 0.3, "dur": 0.3}, {"word": "Cappadocia", "start": 0.7, "dur": 0.4}],
               [{"word": "lies", "start": 1.2, "dur": 0.2}, {"word": "a", "start": 1.45, "dur": 0.1}]]
    (show, hide, rev), second = burst_timings(phrases, first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S)
    assert show == 0.0 and rev == [0.0, 0.0] and hide == pytest.approx(1.2 - LEAD)
    assert second == burst_timings(phrases)[1]                            # later bursts are unchanged
    assert burst_timings(phrases)[0][2][1] == pytest.approx(0.7 - LEAD)    # off by default
    late = [[dict(w, start=w["start"] + 0.7) for w in phrases[0]]]
    assert burst_timings(late, first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S)[0][0] == pytest.approx(1.0 - LEAD)
    edge = [[dict(w, start=w["start"] + 0.3 + LEAD) for w in phrases[0]]]  # first - lead == 0.6: still whole
    assert burst_timings(edge, first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S)[0][0] == 0.0


def test_the_whole_first_burst_is_one_settled_event_in_bone_and_gold(tmp_path):
    words = _timed("Derinkuyu reaches roughly 280 feet deep. Then it was forgotten.", t0=0.25, sentence_pause=0.8)
    path = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Derinkuyu"],
                                           first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S)
    texts, boxes = _events(path.read_text("utf-8"), "CapText"), _events(path.read_text("utf-8"), "CapBox")
    first = [e for e in texts if e[0] == 0.0]
    assert len(first) == 1 and boxes[0][0] == 0.0
    t = first[0][2]
    assert CAPTION_HIDDEN not in t and "\\t(" not in t and "\\fad" not in t
    assert "\\fad" not in boxes[0][2]
    assert "DERINKUYU" in _gold(t)
    # the rest of the Reel still reveals word by word
    assert any(CAPTION_HIDDEN in e[2] for e in texts if e[0] > 1.0)
    # without the switch the first burst reveals as before
    plain = VideoComposer().build_subtitles(words, tmp_path / "p.ass", emphasis=["Derinkuyu"]).read_text("utf-8")
    assert any(CAPTION_HIDDEN in e[2] for e in _events(plain, "CapText") if e[0] < 0.3)


# ---------------------------------------------------------------- 9:16 cue-to-cut
def _ph(start, text="Ancient stone", dur=0.3):
    return [{"word": w, "start": start + i * 0.35, "dur": dur} for i, w in enumerate(text.split())]


def test_caption_cut_times_are_the_scene_boundaries():
    assert caption_cut_times([2.0, 3.0, 1.5]) == pytest.approx([2.0, 5.0])
    assert caption_cut_times([4.0]) == []


def test_a_show_a_few_frames_after_a_cut_moves_onto_it():
    phrases = [_ph(1.0), _ph(4.0)]
    timings = burst_timings(phrases)
    cut = timings[1][0] - 3 * FRAME
    snapped = snap_to_cuts(phrases, timings, [cut])
    assert snapped[1][0] == pytest.approx(cut) and snapped[1][2][0] == pytest.approx(cut)
    assert snapped[0] == timings[0]


def test_a_hide_a_few_frames_before_a_cut_moves_onto_it():
    phrases = [_ph(1.0), _ph(4.0)]
    timings = burst_timings(phrases)
    cut = timings[0][1] + 4 * FRAME
    snapped = snap_to_cuts(phrases, timings, [cut])
    assert snapped[0][1] == pytest.approx(cut)


def test_back_to_back_bursts_move_their_shared_boundary_as_one():
    phrases = [_ph(1.0), _ph(2.2)]
    timings = burst_timings(phrases)
    assert timings[0][1] == pytest.approx(timings[1][0])
    cut = timings[0][1] - 2 * FRAME
    snapped = snap_to_cuts(phrases, timings, [cut])
    assert snapped[0][1] == pytest.approx(cut) and snapped[1][0] == pytest.approx(cut)


def test_far_cuts_and_no_cuts_leave_the_timing_alone():
    phrases = [_ph(1.0), _ph(4.0)]
    timings = burst_timings(phrases)
    assert snap_to_cuts(phrases, timings, None) == timings
    assert snap_to_cuts(phrases, timings, [timings[1][0] - (CAPTION_CUT_SNAP_FRAMES + 1) * FRAME]) == timings


def test_a_snap_never_shows_a_burst_after_its_first_word_is_heard():
    phrases = [_ph(1.0), _ph(4.0)]
    timings = burst_timings(phrases)
    cut = timings[1][0] + 4 * FRAME          # would land 2 frames after the first word
    snapped = snap_to_cuts(phrases, timings, [cut])
    assert snapped[1][0] <= phrases[1][0]["start"] + 1e-9


def test_a_snap_that_would_cut_the_reading_time_moves_the_boundary_two_frames_off_the_cut():
    # one-word burst held just long enough to be read: pulling its hide onto an earlier cut would shorten it
    phrases = [[{"word": "Extraordinarily", "start": 1.0, "dur": 0.2}], _ph(5.0)]
    timings = burst_timings(phrases)
    show, hide, _ = timings[0]
    assert hide - show == pytest.approx(reading_need(phrases[0]))
    cut = hide - FRAME
    snapped = snap_to_cuts(phrases, timings, [cut])
    # freeze pass C9 (2026-11-04): out to CAPTION_CUT_CLEAR_FRAMES (4) off the cut, no longer CAPTION_CUT_MIN_FRAMES
    # (2): a plate change one to three frames off a cut reads as a mistake
    from src.config import CAPTION_CUT_CLEAR_FRAMES
    assert snapped[0][1] == pytest.approx(cut + CAPTION_CUT_CLEAR_FRAMES * FRAME)


def test_build_subtitles_snaps_only_the_reel(tmp_path):
    words = _timed("Ancient stone. Then silence fell.", sentence_pause=1.5)
    plain = _events(VideoComposer().build_subtitles(words, tmp_path / "a.ass").read_text("utf-8"), "CapBox")
    cut = plain[1][0] - 3 * FRAME
    snapped = _events(VideoComposer().build_subtitles(words, tmp_path / "b.ass", cuts=[cut]).read_text("utf-8"), "CapBox")
    assert snapped[1][0] == pytest.approx(round(cut, 2), abs=0.006)
    wide_a = _events(VideoComposer().build_subtitles(words, tmp_path / "c.ass", width=1920, height=1080).read_text("utf-8"), "CapBox")
    wide_b = _events(VideoComposer().build_subtitles(words, tmp_path / "d.ass", width=1920, height=1080,
                                                     cuts=[cut]).read_text("utf-8"), "CapBox")
    assert wide_a == wide_b


# ---------------------------------------------------------------- compose turns both on
@pytest.mark.parametrize("size,expect_cuts", [((1080, 1920), True), ((1920, 1080), False)])
def test_compose_video_passes_the_first_frame_and_cut_switches(tmp_path, monkeypatch, size, expect_cuts):
    from PIL import Image
    seen = {}

    class Stop(Exception):
        pass

    def fake(self, words, ass_path, **kw):
        seen.update(kw)
        raise Stop

    monkeypatch.setattr(VideoComposer, "build_subtitles", fake)
    imgs = []
    for i in range(2):
        p = tmp_path / f"scene{i}.jpg"
        Image.new("RGB", (64, 64), (120, 110, 90)).save(p)
        imgs.append(p)
    vo, bed = tmp_path / "vo.wav", tmp_path / "bed.wav"
    vo.write_bytes(b"RIFF"), bed.write_bytes(b"RIFF")
    with pytest.raises(Stop):
        VideoComposer().compose_video(imgs, vo, bed, _timed("Derinkuyu reaches roughly 280 feet deep."), 4.0,
                                      tmp_path / "out.mp4", width=size[0], height=size[1],
                                      aspect_ratio="9:16" if size[1] > size[0] else "16:9", durations=[2.0, 2.4])
    # both switches are 9:16 only (review fix: a 16:9 first line no longer jumps to 0.0 ahead of its narration)
    assert seen["first_frame_whole"] == (CAPTION_FIRST_FRAME_WHOLE_S if expect_cuts else None)
    assert (seen["cuts"] == pytest.approx([2.0])) if expect_cuts else seen["cuts"] is None


def test_round4_caption_constants_live_in_one_marked_config_block():
    text = Path("src/config.py").read_text(encoding="utf-8")
    block = text[text.index("# >>> Captions round 4"):text.index("# <<< Captions round 4")]
    for name in ("CAPTION_FIRST_FRAME_WHOLE_S", "CAPTION_CUT_SNAP_FRAMES", "CAPTION_CUT_MIN_FRAMES"):
        assert f"\n{name} = " in block and text.count(f"\n{name} = ") == 1
    assert CAPTION_FIRST_FRAME_WHOLE_S < 1.0 - LEAD     # a first word at 1.0 s keeps its word-by-word reveal


# ---------------------------------------------------------------- review fixes: ranges count one thing; 9:16 only
@pytest.mark.parametrize("text,terms,expect", [
    ("It housed 20,000 people and 50 cattle", ["20,000 people"], ["20,000", "PEOPLE"]),
    ("It housed 20,000 people and 50 cattle", ["50 cattle"], ["50", "CATTLE"]),
    ("Kings built 3 temples and 2 towers here", ["3 temples"], ["3", "TEMPLES"]),
    ("It took 20 years or 30 workers", ["20 years"], ["20", "YEARS"]),
    ("It took 20 years or 30 workers", ["30 workers"], ["30", "WORKERS"]),
    ("It took 20 years to 30 workers", ["20 years"], ["20", "YEARS"]),
    ("They counted 94 and 104 feet", ["94"], ["94"]),                      # AND without BETWEEN: two figures
])
def test_gold_does_not_spread_onto_a_second_count_of_a_different_thing(text, terms, expect):
    assert _emph(text, terms) == expect


@pytest.mark.parametrize("text,terms,expect", [
    ("It took 20 years or 30 years", ["20 years"], ["20", "YEARS", "OR", "30", "YEARS"]),
    ("It took 20 to 30 years later", ["20"], ["20", "TO", "30", "YEARS"]),
    ("It took 20 years or 30 later", ["20 years"], ["20", "YEARS", "OR", "30"]),
    ("It spans 94 feet to 104 feet", ["104 feet"], ["94", "FEET", "TO", "104", "FEET"]),
    ("It took 1 year to 2 years", ["1"], ["1", "YEAR", "TO", "2", "YEARS"]),
    ("Eight or nine kings ruled", ["eight"], ["EIGHT", "OR", "NINE"]),
    ("It was between 94 and 104 metres long.", ["104 metres"], ["BETWEEN", "94", "AND", "104", "METRES"]),
])
def test_gold_still_takes_a_range_of_one_thing(text, terms, expect):
    assert _emph(text, terms) == expect


@pytest.mark.parametrize("text,unbonded", [
    ("It took 20 years or 30 workers", ("years", "or")),
    ("It housed 20,000 people and 50 cattle", ("people", "and")),
    ("They counted 94 feet and 104 feet", ("feet", "and")),
])
def test_two_counts_of_different_things_are_not_bonded(text, unbonded):
    bonded = _bonded_words(text)
    assert unbonded not in bonded and (unbonded[1], text.split()[text.split().index(unbonded[1]) + 1]) not in bonded


def test_a_mixed_count_sentence_never_forces_a_burst_over_the_word_cap(tmp_path):
    from src.config import MAX_BURST_WORDS
    words = _timed("It took 20 years or 30 workers to cut the city from the rock.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "m.ass", emphasis=["20 years"]).read_text("utf-8")
    for _, _, t in _events(ass, "CapText"):
        assert len(re.sub(r"\{[^}]*\}", "", t).replace(CAPTION_HIDDEN, " ").split()) <= MAX_BURST_WORDS
    golds = {w.strip(".,") for _, _, t in _events(ass, "CapText") for w in _gold(t)}
    assert golds <= {"20", "YEARS"}


def test_a_widescreen_first_line_waits_for_its_narration(tmp_path):
    words = _timed("Derinkuyu reaches roughly 280 feet deep. Then it was forgotten.", t0=0.25, sentence_pause=0.8)
    kw = {"emphasis": ["Derinkuyu"], "width": 1920, "height": 1080}
    plain = _events(VideoComposer().build_subtitles(words, tmp_path / "a.ass", **kw).read_text("utf-8"), "CapText")
    asked = _events(VideoComposer().build_subtitles(words, tmp_path / "b.ass", first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S,
                                                    **kw).read_text("utf-8"), "CapText")
    assert asked == plain
    assert asked[0][0] == pytest.approx(0.25 - LEAD, abs=0.006) and asked[0][0] > 0.0
    # the Reel still opens whole on frame 0
    reel = _events(VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Derinkuyu"],
                                                   first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S).read_text("utf-8"),
                   "CapText")
    assert reel[0][0] == 0.0
