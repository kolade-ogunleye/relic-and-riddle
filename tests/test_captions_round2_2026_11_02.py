"""Caption review round 2 (2026-11-02): one caption size per Reel, words that appear as they are spoken, casing
that follows the narration, timing around pauses, the brand's INK plate, the 16:9 burn-in, closed-caption
sidecars with their own cues, and the AI-reconstruction badge. ffmpeg here has no libass, so these read the
generated ASS text and measure the bundled fonts with PIL."""
import re
from itertools import pairwise
from pathlib import Path

import pytest

from src import brand, safe_zones
from src.config import (
    CAPTION_LEAD_FRAMES,
    CAPTION_PAUSE_GAP,
    CAPTION_PAUSE_TAIL,
    CAPTION_WIDE_MAX_CHARS,
    CAPTION_WIDE_MAX_WORDS,
    COL_BACK,
    SIDECAR_MAX_CUE,
    SIDECAR_MAX_LINE_CHARS,
    SIDECAR_MAX_LINES,
    SIDECAR_MIN_CUE,
    VIDEO_FPS,
)
from src.typography import get_font
from src.video_composer import (
    CAPTION_BURST_MIN_SIZE,
    CAPTION_HIDDEN,
    CAPTION_MIN_SIZE,
    COL_EMPHASIS,
    COL_FUTURE,
    FONT_SIZE,
    MAX_CAPTION_WIDTH,
    VideoComposer,
    _caption_fit_size,
    _caption_text_width,
    _fits_caption_width,
    _phrase_words,
    ass_font_size,
    burst_timings,
    format_word_caption,
    merge_short_bursts,
    partition_sidecar_cues,
    partition_words_grammatical,
)
from src.video_qa import caption_pills

NARRATION = (
    "Can a massive temple rise without assembling a single stone? In the 8th century, under King Krishna I of the "
    "Rashtrakuta dynasty, workers began cutting downward into a basalt cliff at Ellora. They removed an estimated "
    "200,000 tons of rock, carving the Kailasa temple, designated as Cave 16, from a single monolith. "
    "Archaeologists still debate how long it took. Some say twenty years. Others suspect far longer, though no "
    "inscription survives to settle it."
)
LEAD = CAPTION_LEAD_FRAMES / VIDEO_FPS


def _timed(text, step=0.36, dur=0.32, sentence_pause=0.0, t0=0.0):
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
            out.append((_t(parts[1]), _t(parts[2]), parts[9]))
    return out


def _burst_events(ass_text, style):
    """_events with a two-line burst's lines (freeze pass N1, 2026-11-04: one event a line, same start and end) joined
    into one, line 1 first."""
    out = []
    for s, e, t in _events(ass_text, style):
        if out and abs(out[-1][0] - s) < 1e-9 and abs(out[-1][1] - e) < 1e-9:
            out[-1] = (s, e, out[-1][2] + " " + t)
        else:
            out.append((s, e, t))
    return out


def _t(stamp):
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _plain(tagged):
    return re.sub(r"\{[^}]*\}", "", tagged)


# ---------------------------------------------------------------- 1. one size per Reel
def test_the_phrase_builder_tests_the_full_caption_size_not_the_smallest():
    phrase = "underground for thousands"  # fits at 48 px (the old test size), not at 72 px
    assert _caption_text_width(phrase.split(), 48) <= MAX_CAPTION_WIDTH
    assert not _fits_caption_width(phrase)
    assert _fits_caption_width("a single stone")


def test_bursts_keep_one_size_across_a_realistic_narration(tmp_path):
    words = _timed(NARRATION, sentence_pause=0.5)
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Kailasa"]).read_text(encoding="utf-8")
    sizes = [re.search(r"\\fs(\d+)", e[2]) for e in _events(ass, "CapText")]
    shrunk = [int(m.group(1)) for m in sizes if m]
    # Round 4 (caption panel, standards editor): a hedge never leaves its figure (caption_bonds), so this narration's
    # "an estimated | 200,000 tons" split is gone and AN ESTIMATED 200,000 TONS is one burst a little under the full
    # size (still above CAPTION_BURST_MIN_SIZE, asserted below). Only bursts holding no such bond count here.
    from src.video_composer import caption_bonds
    unbonded = [int(m.group(1)) for e, m in zip(_events(ass, "CapText"), sizes)
                if m and not caption_bonds(_plain(e[2]).split())]
    assert len(unbonded) <= 0.1 * len(sizes), unbonded                  # nearly every word event at the full size
    # freeze pass C4 (2026-11-04): the ladder 80 -> 74 -> 66 px, 66 only for a bonded burst (a name with its lead-in,
    # a hedge with its figure); was every shrunk event at 74 or more
    from src.video_composer import CAPTION_BOND_MIN_SIZE
    assert set(shrunk) <= {round(CAPTION_BURST_MIN_SIZE / 0.5625), round(CAPTION_BOND_MIN_SIZE / 0.5625)}
    phrases = merge_short_bursts(partition_words_grammatical(words), _fits_caption_width, 5)
    fits = [_caption_fit_size(p) for p in phrases]
    assert set(fits) <= {FONT_SIZE, CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE} and max(fits) == FONT_SIZE
    for p, fit in zip(phrases, fits):
        if fit < CAPTION_BURST_MIN_SIZE:
            assert caption_bonds(p), [w["word"] for w in p]
    # adjacent bursts differ by one ladder step at most where neither is bonded (was <= 9% between any two)
    assert max(abs(a - b) / max(a, b) for a, b in pairwise(fits)) <= (FONT_SIZE - CAPTION_BOND_MIN_SIZE) / FONT_SIZE


def test_only_a_single_over_long_word_shrinks_and_not_below_60():
    assert CAPTION_MIN_SIZE >= 60
    assert CAPTION_MIN_SIZE <= _caption_fit_size("Pseudoarchaeoastronomical") < FONT_SIZE
    words = _timed("It was hidden underground for thousands of years.")
    for p in partition_words_grammatical(words):
        if len(p) > 1:
            assert _caption_fit_size(p) >= CAPTION_BURST_MIN_SIZE


# ---------------------------------------------------------------- 2. words appear as spoken
def test_upcoming_words_are_transparent_placeholders_and_the_plate_is_fixed(tmp_path):
    words = _timed("Roman concrete heals itself inside ocean water.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    texts = _events(ass, "CapText")
    boxes = _events(ass, "CapBox")
    assert len(texts) == len(words)
    phrases = merge_short_bursts(partition_words_grammatical(words), _fits_caption_width, 5)
    k = 0
    for bi, phrase in enumerate(phrases):
        shown = " ".join(_phrase_words(phrase)).upper()
        box_start, box_end, _ = boxes[bi]
        for wi in range(len(phrase)):
            start, end, text = texts[k]
            k += 1
            assert box_start <= start and end <= box_end + 1e-6             # the plate holds through the burst
            assert _plain(text) == shown                                     # same layout every event
            visible, _, hidden = text.partition(CAPTION_HIDDEN)
            assert len(_plain(visible).split()) == wi + 1
            assert COL_FUTURE not in visible
            assert (CAPTION_HIDDEN in text) == (wi < len(phrase) - 1)
            assert len(_plain(hidden).split()) == len(phrase) - wi - 1


def test_gold_marks_key_terms_only(tmp_path):
    words = _timed("In 1963, a man found Derinkuyu, a city 280 feet deep.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Derinkuyu"]).read_text(encoding="utf-8")
    for _, _, text in _events(ass, "CapText"):
        for colour, word in re.findall(r"\\c(&H[0-9A-F]+&)[^}]*\}([^{]*)", text):
            if colour == COL_EMPHASIS:
                assert "DERINKUYU".startswith(word.strip()) or word.strip() in "DERINKUYU"


# ---------------------------------------------------------------- 3. casing follows the narration
@pytest.mark.parametrize("word, runs", [
    ("concrete", [("82", "CONCRETE")]),
    ("Roman", [("100", "R"), ("82", "OMAN")]),
    ("stone?", [("82", "STONE?")]),
    ("1963,", [("100", "1963,")]),
    ("BC", [("100", "BC")]),
    ("us", [("82", "US")]),
    ("I", [("100", "I")]),
    ("self-healing", [("82", "SELF-HEALING")]),
])
def test_full_size_initials_only_where_the_narration_has_a_capital(word, runs):
    tag = format_word_caption(word, "&H00FFFFFF&", False, False, False)
    assert re.findall(r"fscx(\d+)\\fscy\d+\}([^{]*)", tag) == runs
    assert _plain(tag) == word.upper()


def test_caption_characters_still_equal_the_narration(tmp_path):
    words = _timed(NARRATION)
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    # fix round 1 (2026-11-04): build_subtitles forms its bursts with caption_phrases (the freeze-pass phrasing on the
    # round-4 base, operator decision: the HEAD-regression gate); was the bare partition and merge
    from src.video_composer import caption_phrases
    phrases = caption_phrases(words)
    finals = [e[2] for e in _burst_events(ass, "CapText") if CAPTION_HIDDEN not in e[2]]
    assert [_plain(t) for t in finals] == [" ".join(_phrase_words(p)).upper() for p in phrases]
    lower_words = [w["word"] for w in words if w["word"][0].islower()]
    first_lower = format_word_caption(lower_words[0], "&H00FFFFFF&", False, False, False)
    assert "fscx100" not in first_lower


# ---------------------------------------------------------------- 4. timing
def test_a_burst_appears_two_frames_before_its_first_word():
    phrases = [[{"word": "One", "start": 1.0, "dur": 0.3}, {"word": "two", "start": 1.4, "dur": 0.3}],
               [{"word": "three", "start": 1.8, "dur": 0.3}, {"word": "four.", "start": 2.2, "dur": 0.3}]]
    (show1, hide1, rev1), (show2, _, _) = burst_timings(phrases)
    assert show1 == pytest.approx(1.0 - LEAD) and rev1[1] == pytest.approx(1.4 - LEAD)
    assert LEAD == pytest.approx(0.0667, abs=0.001)
    assert hide1 == pytest.approx(show2) and show2 == pytest.approx(1.8 - LEAD)


def test_across_a_long_pause_the_burst_leaves_after_its_last_word():
    phrases = [[{"word": "It", "start": 0.0, "dur": 0.3}, {"word": "sank.", "start": 0.4, "dur": 0.4}],
               [{"word": "Then", "start": 2.5, "dur": 0.3}, {"word": "silence.", "start": 2.9, "dur": 0.5}]]
    (_, hide1, _), (show2, _, _) = burst_timings(phrases)
    assert 2.5 - 0.8 > CAPTION_PAUSE_GAP
    assert hide1 == pytest.approx(0.8 + CAPTION_PAUSE_TAIL) and hide1 < show2
    near = [[{"word": "It", "start": 0.0, "dur": 0.3}, {"word": "sank,", "start": 0.4, "dur": 0.4}],
            [{"word": "then", "start": 1.3, "dur": 0.3}, {"word": "rose.", "start": 1.7, "dur": 0.5}]]
    (_, hide_near, _), (show_near, _, _) = burst_timings(near)
    assert hide_near == pytest.approx(show_near)                         # a short gap is held


def test_a_burst_shorter_than_0_6_s_joins_a_neighbour(tmp_path):
    words = [{"word": "Visible", "start": 0.0, "dur": 0.3}, {"word": "from", "start": 0.35, "dur": 0.2},
             {"word": "space.", "start": 0.6, "dur": 0.4}, {"word": "Ok.", "start": 1.05, "dur": 0.25}]
    merged = merge_short_bursts(partition_words_grammatical(words), _fits_caption_width, 5)
    # freeze pass C6 (2026-11-04): no join crosses a sentence end, and a one-word sentence never joins the burst
    # before it (was [4]: VISIBLE FROM SPACE. OK), so OK. stays its own burst
    assert [len(p) for p in merged] == [3, 1]
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    # fix round 1 (operator decision 2026-11-04, the HEAD-regression gate): the drawn bursts keep HEAD 0036b18's join
    # across the sentence end (SPACE. | OK. was a cut HEAD does not make), so one plate, as at HEAD (was 2 in round 2)
    assert len(_events(ass, "CapBox")) == 1 and len(_events(ass, "CapText")) == 4
    paused = [dict(w) for w in words]
    paused[3]["start"] = 2.5                                             # after a long pause it stays alone
    assert len(merge_short_bursts(partition_words_grammatical(paused), _fits_caption_width, 5)) == 2


def test_caption_events_never_overlap_on_a_realistic_narration(tmp_path):
    words = _timed(NARRATION, sentence_pause=1.0)
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass").read_text(encoding="utf-8")
    for style in ("CapText", "CapBox"):
        spans = [(s, e) for s, e, _ in _burst_events(ass, style)]   # a two-line burst's lines share one span (N1)
        assert all(a[1] <= b[0] + 1e-6 for a, b in pairwise(spans)), style


# ---------------------------------------------------------------- 5. the brand's plate, the docstring
def test_the_plate_is_the_brand_ink():
    assert COL_BACK == brand.ass_colour(brand.INK, 0x20) == "&H20120E0B&"
    import src.video_composer as vc
    assert "92pt" not in (vc.__doc__ or "")


# ---------------------------------------------------------------- 6. 16:9 burn-in
def test_widescreen_captions_use_montserrat_bold_brand_colours_and_a_plate(tmp_path):
    words = _timed(NARRATION, sentence_pause=0.5)
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080,
                                          emphasis=["Kailasa"]).read_text(encoding="utf-8")
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: CapText,")).split(",")
    assert style[1] == "Montserrat" and style[7] == "-1"                  # Bold, not the variable font's Thin
    assert int(style[2]) == ass_font_size("Montserrat", 52, 700)
    for stale in ("&HCCCCCC&", "&H999999&", "&H00D7D7&"):
        assert stale not in ass
    assert COL_EMPHASIS in ass
    assert next(ln for ln in ass.splitlines() if ln.startswith("Style: CapBox,")).split(",")[3] == COL_BACK
    texts = _events(ass, "CapText")
    assert not any(CAPTION_HIDDEN in t for _, _, t in texts)            # round 3: each 16:9 burst shown whole
    pills = caption_pills(tmp_path / "w.ass")
    assert pills and all(p[3] == 950 and p[1] >= 855 for p in pills)      # round 3: lower edge 130 px up (was 80)
    font = get_font("Montserrat", 52, weight=700)
    finals = [_plain(t) for _, _, t in texts if CAPTION_HIDDEN not in t]
    from src.video_composer import name_bonds
    for line in finals:
        # freeze pass N2 (2026-11-04): a burst holding a whole bonded name may pass the 42 characters while its
        # plate stays inside the 1440 px line (UNDER KING KRISHNA I OF THE RASHTRAKUTA DYNASTY)
        named = len(name_bonds(line.split())) >= 3
        assert (len(line) <= CAPTION_WIDE_MAX_CHARS or named) and len(line.split()) <= CAPTION_WIDE_MAX_WORDS
        assert font.getlength(line) + 56 <= 1440
    counts = [len(line.split()) for line in finals]
    assert sum(4 <= c <= 8 for c in counts) >= 0.7 * len(counts)
    assert all(p[0] >= 0.05 * 1920 and p[2] <= 0.95 * 1920 for p in pills)


# ---------------------------------------------------------------- 7. closed-caption sidecars
def test_sidecar_cues_are_their_own_lines_not_reel_bursts(tmp_path):
    words = _timed(NARRATION, sentence_pause=0.4)
    cues = partition_sidecar_cues(words)
    assert len(cues) < len(partition_words_grammatical(words)) / 2
    for start, end, text in cues:
        lines = text.split("\n")
        assert len(lines) <= SIDECAR_MAX_LINES and all(len(ln) <= SIDECAR_MAX_LINE_CHARS for ln in lines)
        assert SIDECAR_MIN_CUE - 1e-6 <= end - start <= SIDECAR_MAX_CUE + 1e-6
    assert " ".join(t.replace("\n", " ") for _, _, t in cues) == NARRATION
    assert all(a[1] <= b[0] for a, b in pairwise(cues))
    srt = VideoComposer().export_subtitles_srt(words, tmp_path / "c.srt").read_text(encoding="utf-8")
    vtt = VideoComposer().export_subtitles_vtt(words, tmp_path / "c.vtt").read_text(encoding="utf-8")
    assert srt.count(" --> ") == vtt.count(" --> ") == len(cues)
    assert vtt.startswith("WEBVTT")


def test_a_short_sentence_does_not_flash_as_its_own_cue():
    words = [{"word": "Ok.", "start": 0.0, "dur": 0.3}, {"word": "Then", "start": 0.4, "dur": 0.3},
             {"word": "it", "start": 0.75, "dur": 0.2}, {"word": "sank.", "start": 1.0, "dur": 0.5}]
    cues = partition_sidecar_cues(words)
    assert len(cues) == 1 and cues[0][2] == "Ok. Then it sank."


# ---------------------------------------------------------------- 8. the reconstruction badge
def test_widescreen_reconstruction_badge_is_bone_plex_mono_on_an_ink_plate(tmp_path):
    words = _timed("Before the ship sank.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080,
                                          reconstruction_intervals=[(0.0, 2.0)]).read_text(encoding="utf-8")
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: ReconBadge,")).split(",")
    assert style[1] == "IBM Plex Mono" and int(style[2]) == ass_font_size("IBM Plex Mono", 26, 600)
    assert style[3] == brand.ass_colour(brand.BONE) and style[7] == "-1"
    plate_style = next(ln for ln in ass.splitlines() if ln.startswith("Style: ReconPlate,")).split(",")
    assert plate_style[3] == brand.ass_colour(brand.INK, 0x26)
    (_, _, badge), = _events(ass, "ReconBadge")
    assert _plain(badge) == "AI ILLUSTRATION · NOT A PHOTO"   # item 4 (2026-11-03): was "[ HISTORICAL RECONSTRUCTION ]"
    x, y = map(int, re.search(r"\\an7\\pos\((\d+),(\d+)\)", badge).groups())
    assert x >= 96 and y >= 54
    (_, _, plate), = _events(ass, "ReconPlate")
    px, py = map(int, re.search(r"\\pos\((\d+),(\d+)\)", plate).groups())
    assert (px, py) == (96, 54) and px < x


def test_portrait_reconstruction_badge_gets_a_plate_and_keeps_its_wording(tmp_path):
    words = _timed("Ancient craft.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "p.ass", reconstruction_intervals=[(0.0, 2.0)]).read_text(encoding="utf-8")
    assert next(ln for ln in ass.splitlines() if ln.startswith("Style: ReconBadge,")).split(",")[1] == "IBM Plex Mono"
    (_, _, plate), = _events(ass, "ReconPlate")
    px, py = map(int, re.search(r"\\pos\((\d+),(\d+)\)", plate).groups())
    box = safe_zones.safe_box("reel")
    assert box[0] <= px and box[1] <= py <= 320
    assert "AI ILLUSTRATION · NOT A PHOTO" in ass   # item 4 (2026-11-03): was "[ HISTORICAL RECONSTRUCTION ]"


# ---------------------------------------------------------------- safe zones
def test_reel_plates_stay_in_the_reel_box_and_below_the_graphics(tmp_path):
    words = _timed(NARRATION + " Pseudoarchaeoastronomical claims followed.")
    path = VideoComposer().build_subtitles(words, tmp_path / "c.ass")
    pills = caption_pills(Path(path))
    box = safe_zones.safe_box("reel")
    graphics_bottom = safe_zones.graphics_box()[3]
    assert pills and all(safe_zones.inside(p, box) for p in pills)
    assert all(p[0] >= 65 and p[2] <= 888 and p[1] > graphics_bottom and p[3] <= 1248 for p in pills)


def test_portrait_reconstruction_badge_is_readable_top_left(tmp_path):
    """Operator-approved move (2026-11-02): 30px bone Plex Mono on an INK plate at x 84, y 300, inside the Reel box."""
    from src.config import RECON_SIZE_PORTRAIT

    words = _timed("Ancient craft.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "p.ass", reconstruction_intervals=[(0.0, 2.0)]).read_text(encoding="utf-8")
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: ReconBadge,")).split(",")
    assert style[18] == "7"  # top-left alignment
    (_, _, plate), = _events(ass, "ReconPlate")
    assert r"\pos(84,300)" in plate
    width = get_font("IBM Plex Mono", RECON_SIZE_PORTRAIT, weight=600).getlength("AI ILLUSTRATION · NOT A PHOTO")
    assert 84 + width + 60 <= 888  # plate stays clear of the right rail
