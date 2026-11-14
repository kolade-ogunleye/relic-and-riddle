"""Freeze pass on the burned-in captions (design panel, 2026-11-04): the 9:16 Reel bursts and the 16:9 documentary
line, and the .srt/.vtt sidecar cues (src/video_composer.py caption code). One test group per spec item:

C1  16:9 font (BLOCKED on the operator: a static Montserrat Bold); the 16:9 plate is capped at CAPTION_WIDE_MAX_PLATE
C2  proper names carry connectors (VALLEY OF THE KINGS), an acronym (UNESCO) and a year or ordinal (THE 1941 SIEGE)
C3  the prepositions and articles leading into a name or figure group go with it; never a function-word burst
C4  the 9:16 shrink ladder 80 -> 74 -> 66 px (only bonded bursts at 66)
C5  the 9:16 measure is what libass draws; the ink sits CAPTION_INK_PAD_MIN inside the plate
C6  sentence ends: no burst runs past . ? !; a one- or two-word sentence stands alone (or joins the following burst)
C7  figure units: dates, units, conversions in brackets, ranges, key terms
C8  cohesion: no burst ends on a dangling word (articles, prepositions, pre-modifiers, possessives) where avoidable
C9  cue-to-cut: no boundary one to three frames off a picture cut
C10 dwell: a burst stays whole CAPTION_DWELL_AFTER_LAST_S after its last word; a figure burst CAPTION_DIGIT_MIN_S
C11 gold on letters and figures, the gold word's trailing punctuation in bone
C12 sidecar cues: break priority, no break inside a bond or after a dangling word, balanced lines

The six narrations are the panel's: two synthetic test texts (labelled as such) and four real verified scripts, with
the review harness's deterministic word timings (docs/reviews/freeze-panel-2026-11-04/captions/render_evidence.py).

Round 2 (operator decisions, 2026-11-04; tests at the end of the file use the five REAL runs of the freeze-panel
catalogue, docs/reviews/freeze-panel-2026-11-04/fixtures, with their own words.json timings):
V1 16:9 vignette at the SAR; N1 long-name ladder and two-line names; N2 name runs past one burst; N3 number words in
names; A1 no burst ends on an article; P1 pause breaks; U1 units and hedge phrases; M1 modifier cohesion (and a
regression gate against HEAD 5fdb5dc); D1 half a second on screen; G1 caption plate over a graphic's text; W1 the 16:9
plate measure; K1 the 28 px pad, IT WAS and quote marks; S1 sidecar cue ends.

Fix round 1 (operator decisions 2026-11-04): HEAD 0036b18's phrasing is the floor. Every pair of adjacent words it
keeps in one 9:16 burst, 16:9 burst or sidecar cue stays together unless a name, a figure link, the article rule or a
width or floor limit forces the cut (test_head_regression_gate, GATE_FORCED; test_lone_word_bursts_are_head_or_forced);
names are HEAD's name_bonds (merge note); no two-line plate rises above y 1058. Tests of the round-2 rules that HEAD's
own phrasing breaks read HEAD's there, each marked "fix round 1 (gate)" with the value it had.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import config
from src import video_composer as vc
from src.video_composer import (
    CAPTION_BOND_MIN_SIZE,
    CAPTION_BURST_MIN_SIZE,
    CAPTION_DIGIT_MIN_S,
    CAPTION_DWELL_AFTER_LAST_S,
    CAPTION_INK_PAD_MIN,
    FONT_SIZE,
    FUNCTION_WORDS,
    MAX_BURST_CHARS,
    MAX_BURST_WORDS,
    VideoComposer,
    caption_bonds,
    caption_phrases,
    name_bonds,
    partition_sidecar_cues,
    partition_words_grammatical,
)

ROOT = Path(__file__).resolve().parents[1]
FPS = 30
HAS_FFMPEG = shutil.which("ffmpeg") is not None

# Synthetic test narrations (labelled as such: the panel's edge cases), and the real scripts.
SYNTH = {
    "names": ("Under the Rashtrakuta dynasty, sculptors cut the Temple of Kailasa from a single hill of basalt. "
              "Far to the west, the Hal Saflieni Hypogeum held the bones of some 7,000 people. "
              "In 1941, Army Group North pushed toward Leningrad. "
              "Pilgrims still walk the Ellora Caves at dawn, and the Great Wall of Gorgan runs for about "
              "195 kilometres across northern Iran.",
              ["Rashtrakuta dynasty", "Hal Saflieni Hypogeum", "7,000 people"]),
    "edge": ("Look. Nobody knows why. "
             "It sank roughly 1,200 years ago, between 287 and 300 kilometres below the sea. "
             "Incomprehensibilities remain. "
             "Counterrevolutionaries dismantled the Colossi of Memnon? No. "
             "It was hidden underground for centuries, beneath the Valley of the Kings.",
             ["1,200 years", "Colossi of Memnon"]),
}
REAL = ["odessa_catacombs", "singing_colossi_of_memnon", "unfinished_obelisk_aswan", "wieliczka_salt_mine"]


def _narrations() -> dict[str, tuple[str, list[str]]]:
    out = dict(SYNTH)
    for topic in REAL:
        d = json.loads((ROOT / "data" / "script_library" / topic / "verified_script.json").read_text("utf-8"))
        out[topic] = (d["narration_script"], list(d.get("emphasis_words") or []))
    return out


NARRATIONS = _narrations()


def timed(text: str) -> list[dict]:
    """The review harness's deterministic timings: 0.11 s + 55 ms a letter, a 40 ms gap, longer after punctuation."""
    words, at = [], 0.25
    for w in text.split():
        letters = len(re.sub(r"[^\w]", "", w))
        dur = round(0.11 + 0.055 * letters, 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + 0.04
        if re.search(r"[.!?…]$", w):
            at += 0.45
        elif re.search(r"[,;:]$", w):
            at += 0.18
    return words


def _even(text: str, step: float = 0.36, dur: float = 0.32) -> list[dict]:
    return [{"word": w, "start": round(k * step, 3), "dur": dur} for k, w in enumerate(text.split())]


def _texts(phrases) -> list[str]:
    return [" ".join(vc._phrase_words(p)).upper() for p in phrases]


def _raw_texts(phrases) -> list[list[str]]:
    return [[w["word"] for w in p] for p in phrases]


_CACHE: dict = {}


def layout(nid: str, wide: bool):
    """(words, phrases) of a narration as build_subtitles forms them."""
    key = (nid, wide)
    if key not in _CACHE:
        text, emphasis = NARRATIONS[nid]
        words = timed(text)
        _CACHE[key] = (words, caption_phrases(words, emphasis, wide))
    return _CACHE[key]


def reel_timings(nid: str):
    words, phrases = layout(nid, False)
    tim = vc.burst_timings(phrases, first_frame_whole=config.CAPTION_FIRST_FRAME_WHOLE_S, dwell=True)
    end = words[-1]["start"] + words[-1]["dur"]
    cuts = vc.caption_cut_times([4.0] * (int(end // 4) + 1))
    return phrases, vc.snap_to_cuts(phrases, tim, cuts), cuts


def _bonded_all(phrase, bonds_of) -> bool:
    raw = [w["word"] for w in phrase]
    return len(raw) > 1 and all(k in bonds_of or vc._is_atomic_boundary(raw[k], raw[k + 1]) for k in range(len(raw) - 1))


# ---------------------------------------------------------------- fix round 1: the HEAD-regression gate
# Operator decision 2026-11-04: every pair of adjacent words HEAD 0036b18 keeps in one 9:16 burst, one 16:9 burst or
# one sidecar cue stays together unless a proper name, a figure link, the article rule or a width or floor limit
# forces the cut. round4_burst_cuts / round4_cue_ends are HEAD 0036b18's phrasing (test_round4_base_is_head_0036b18).
# A freeze-pass rule tested below holds where it costs no such pair; where HEAD's own phrasing cuts (or joins) there,
# the test reads HEAD's, marked "gate" (the change list is in the fix-round-1 build report).
_HEAD_CACHE: dict = {}


def _head_cuts(words, emphasis=None, wide=False) -> set:
    """HEAD 0036b18's burst ends (word positions, the last left out) for these words."""
    key = (id(words), wide)
    if key not in _HEAD_CACHE:
        _HEAD_CACHE[key] = (words, vc.round4_burst_cuts(words, emphasis, wide, 1440 if wide else None))
    return _HEAD_CACHE[key][1]


def _head_cue_ends(words) -> set:
    key = (id(words), "cues")
    if key not in _HEAD_CACHE:
        _HEAD_CACHE[key] = (words, vc.round4_cue_ends(words))
    return _HEAD_CACHE[key][1]


def _ends(phrases) -> list:
    out, at = [], -1
    for p in phrases:
        at += len(p)
        out.append(at)
    return out


# ---------------------------------------------------------------- C0 / C2: names stay whole
@pytest.mark.parametrize("wide", [False, True])
# fix round 1 (merge note 2026-11-04: step 1's name_bonds, HEAD 0036b18's, is the code): VALLEY OF THE KINGS (a lower-case
# THE after OF) and UNESCO WORLD HERITAGE SITES (wider than one burst at 66 px) are no name bond and left the list
@pytest.mark.parametrize("name", ["RASHTRAKUTA DYNASTY", "TEMPLE OF KAILASA", "GREAT WALL OF GORGAN",
                                  "HAL SAFLIENI HYPOGEUM", "COLOSSI OF MEMNON", "AMENHOTEP III", "WORLD WAR II",
                                  "BLACK SEA", "ARMY GROUP NORTH", "ELLORA CAVES"])
def test_listed_names_sit_in_one_burst(name, wide):
    hits = 0
    for nid in NARRATIONS:
        texts = [re.sub(r"[^\w ]", "", t) for t in _texts(layout(nid, wide)[1])]
        if name not in " ".join(texts):
            continue
        hits += 1
        assert any(name in t for t in texts), (nid, texts)
    assert hits, name


# Fix round 1 (merge note 2026-11-04): names HEAD 0036b18's name_bonds does not bond (was: every listed name whole)
UNBONDED_NAMES = {"Valley of the Kings", "UNESCO World Heritage Sites", "Bridge of the Moors", "Abbey de la Trappe"}


@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("sentence,name", [
    ("Far to the south lies the Valley of the Kings, where the pharaohs rest.", "Valley of the Kings"),
    ("Pilgrims crossed the hills to see the Temple of Kailasa at last.", "Temple of Kailasa"),
    ("Soldiers once guarded the Great Wall of Gorgan against raiders.", "Great Wall of Gorgan"),
    ("Travellers heard the Colossi of Memnon sing at dawn.", "Colossi of Memnon"),
    ("Divers found the Hal Saflieni Hypogeum beneath a house.", "Hal Saflieni Hypogeum"),
    ("Both mines are now UNESCO World Heritage Sites with guided tours.", "UNESCO World Heritage Sites"),
    ("Engineers rebuilt the Bridge of the Moors after the flood.", "Bridge of the Moors"),
    ("Monks copied texts at the Abbey de la Trappe for centuries.", "Abbey de la Trappe"),
])
def test_synthetic_names_with_connectors_stay_whole(sentence, name, wide):
    """A name name_bonds bonds sits in one burst; one it does not bond (UNBONDED_NAMES: a lower-case THE or DE LA
    after the particle, or wider than one burst at 66 px) keeps HEAD's phrasing; no burst ends on an article."""
    words = _even(sentence)
    phrases = caption_phrases(words, None, wide)
    assert [w for p in phrases for w in p] == words
    raw = sentence.split()
    n = name.split()
    i = next(k for k in range(len(raw)) if [re.sub(r"[,.]$", "", x) for x in raw[k:k + len(n)]] == n)
    bonded = all(k in name_bonds(raw) for k in range(i, i + len(n) - 1))
    assert bonded == (name not in UNBONDED_NAMES), name
    if bonded:
        assert any(name in " ".join(p) for p in _raw_texts(phrases)), _texts(phrases)
    assert not any(vc._bare(p[-1]["word"]) in ("the", "a", "an") for p in phrases[:-1]), _texts(phrases)


def test_name_runs_take_connectors_acronyms_and_years():
    """Fix round 1 (merge note 2026-11-04: step 1's name_bonds, HEAD 0036b18's, is the code; the rework's N2 name
    variant was dropped): the name bonds as HEAD 0036b18 forms them (tests/test_caption_names_2026_11_04.py)."""
    def pairs(text):
        raw = text.split()
        return {(raw[i], raw[i + 1]) for i in name_bonds(raw)}
    assert pairs("beneath the Valley of the Kings.") == set()        # a lower-case THE after OF: no connector run
    assert pairs("one of the UNESCO World Heritage Sites in 1978.") == set()   # wider than one burst at 66 px
    assert pairs("By one estimate, the 1941 Siege of Odesa began") == {("Siege", "of"), ("of", "Odesa")}
    assert pairs("the 18th Dynasty ruled") == set()
    assert pairs("Look at the Kings") == set()                       # a lower-case verb before: no name
    assert pairs("In 1941 Leningrad fell") == set()                  # a year with no determiner before it
    assert pairs("under King Krishna I of the Rashtrakuta dynasty.") == {
        ("King", "Krishna"), ("Krishna", "I"), ("the", "Rashtrakuta"), ("Rashtrakuta", "dynasty.")}
    assert pairs("ruled by Philip II of Macedon then") == {("Philip", "II"), ("II", "of"), ("of", "Macedon")}
    assert pairs("upon Mohammad Adil Shah's death, leaving") == {("Mohammad", "Adil"), ("Adil", "Shah's")}
    assert pairs("left Egypt's greatest monument") == set()       # an adjective is no owned noun


def test_odessa_siege_is_one_burst():
    for wide in (False, True):
        texts = _texts(layout("odessa_catacombs", wide)[1])
        # fix round 1 (merge note: HEAD 0036b18's name_bonds): the name is SIEGE OF ODESA (was THE 1941 SIEGE OF ODESA)
        assert any("SIEGE OF ODESA" in t for t in texts), texts


# ---------------------------------------------------------------- C3: lead-ins and function-word bursts
def test_rashtrakuta_lead_in_is_one_burst():
    """Never UNDER THE alone. N1 (operator decision, round 2): no burst shrinks below the 66 px bond floor to hold a
    name, so at 9:16 the four words (724 px at 66) break as UNDER | THE RASHTRAKUTA DYNASTY (a one-word lead-in
    burst); 16:9 keeps the four words on its one line."""
    assert _texts(layout("names", False)[1])[:2] == ["UNDER", "THE RASHTRAKUTA DYNASTY"]
    texts = _texts(layout("names", True)[1])
    assert texts[0] == "UNDER THE RASHTRAKUTA DYNASTY", texts
    for wide in (False, True):
        assert "UNDER THE" not in _texts(layout("names", wide)[1])


def test_valley_of_the_kings_and_the_burst_before_it():
    """Fix round 1 (merge note: HEAD 0036b18's name_bonds; gate): VALLEY OF THE KINGS is no name bond, so the burst
    keeps HEAD's BENEATH THE VALLEY | OF THE KINGS. (was: THE VALLEY OF THE KINGS whole); no burst ends on THE."""
    texts = _texts(layout("edge", False)[1])
    k = next(i for i, t in enumerate(texts) if t.endswith("OF THE KINGS"))
    assert texts[k - 1:k + 1] == ["BENEATH THE VALLEY", "OF THE KINGS"], texts
    assert not any(t.endswith(" THE") for t in texts)


def test_to_the_year_196_ad_is_one_burst():
    for wide in (False, True):
        texts = _texts(layout("singing_colossi_of_memnon", wide)[1])
        assert any("TO THE YEAR 196 AD" in t for t in texts), texts
    assert "TO THE YEAR 196 AD" in _texts(layout("singing_colossi_of_memnon", False)[1])


def _lead_in_before_name(phrases, k) -> bool:
    """N1: burst k is a one-word lead-in (UNDER, BENEATH) right before a burst that opens with a name or its article."""
    p = phrases[k]
    if len(p) != 1 or k + 1 >= len(phrases) or vc._bare(p[0]["word"]) not in vc.LEAD_IN_WORDS:
        return False
    nxt = [w["word"] for w in phrases[k + 1]]
    return bool(vc.name_bonds(nxt))


def _name_burst(p) -> bool:
    """A burst holding a whole bonded name with at most an article and a lead-in before it (N1/N2)."""
    raw = [w["word"] for w in p]
    named = vc.name_bonds(raw)
    return bool(named) and vc._name_end_at(named, len(raw) - 1) and vc._name_start_of(named, len(raw) - 1) <= 2


@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_no_function_word_burst_and_reel_limits(nid):
    for wide in (False, True):
        phrases = layout(nid, wide)[1]
        for k, p in enumerate(phrases):
            bare = [vc._bare(w["word"]) for w in p]
            assert not all(b in FUNCTION_WORDS for b in bare) or _lead_in_before_name(phrases, k), \
                (nid, wide, _texts(phrases))
    for p in layout(nid, False)[1]:
        t = " ".join(vc._phrase_words(p)).upper()
        if _name_burst(p):   # N1/N2: a whole name (and its article) passes the burst limits, on one line or two
            assert len(t.split()) <= vc.NAME_MAX_WORDS + 2, t
            continue
        assert len(t.split()) <= MAX_BURST_WORDS + config.CAPTION_BOND_EXTRA_WORDS, t
        assert len(t) <= MAX_BURST_CHARS + config.CAPTION_BOND_EXTRA_CHARS, t


def test_a_fifth_word_needs_two_short_function_words_or_a_figure():
    assert vc.bonded_burst_allowed("the 1941 Siege of Odesa".split())
    assert vc.bonded_burst_allowed("to the year 196 AD".split())
    assert not vc.bonded_burst_allowed("United Nations Educational Scientific Council".split())
    assert not vc.bonded_burst_allowed("one two three four five six".split())


# ---------------------------------------------------------------- C4: the shrink ladder
@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_reel_sizes_keep_to_the_ladder(nid):
    words, phrases = layout(nid, False)
    bonds = caption_bonds(words) | vc.emphasis_bonds(words, NARRATIONS[nid][1])
    base, off_ladder = 0, []
    for p in phrases:
        size = vc._caption_fit_size(p)
        local = {k - base for k in bonds if base <= k < base + len(p) - 1}
        if len(p) > 1:
            if size not in (FONT_SIZE, CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE):
                off_ladder.append((" ".join(w["word"] for w in p), size))
            if size == CAPTION_BOND_MIN_SIZE:
                assert _bonded_all(p, local) or any(k in local for k in range(len(p) - 1)) or \
                    vc.reel_caption_lines(p), (nid, [w["word"] for w in p])
        base += len(p)
    # N1 (operator decision, round 2): the 58/60 px whole-burst shrink is withdrawn, so no multi-word burst is off the
    # ladder (was: UNDER THE RASHTRAKUTA DYNASTY and UNESCO WORLD HERITAGE SITES); the two draw as UNDER | THE
    # RASHTRAKUTA DYNASTY and UNESCO WORLD / HERITAGE SITES (two lines at 80)
    assert off_ladder == [], off_ladder


def test_fit_size_returns_a_ladder_rung():
    assert vc._caption_fit_size("85 METRES") == FONT_SIZE
    for phrase in ("of subterranean tunnels", "the Hal Saflieni Hypogeum", "a natural underground"):
        assert vc._caption_fit_size(phrase) in (FONT_SIZE, CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE)
    assert vc._caption_fit_size("Pseudoarchaeoastronomical") < FONT_SIZE     # a single word keeps its own path


def test_incomprehensibilities_remain_splits_or_fits_at_74():
    texts = _texts(layout("edge", False)[1])
    if "INCOMPREHENSIBILITIES REMAIN" in texts:
        assert vc._caption_fit_size("Incomprehensibilities remain") >= CAPTION_BURST_MIN_SIZE
    else:
        k = texts.index("INCOMPREHENSIBILITIES")
        assert texts[k + 1] == "REMAIN"


def test_every_reel_plate_is_one_height(tmp_path):
    text, emphasis = NARRATIONS["names"]
    ass = VideoComposer().build_subtitles(timed(text), tmp_path / "c.ass", emphasis=emphasis)
    plates = [l for l in ass.read_text("utf-8").splitlines() if ",CapBox," in l]
    heights = set()
    for line in plates:
        path = line.split("\\p1}")[1].split("{")[0]
        numbers = [int(x) for x in re.findall(r"-?\d+", path)]
        heights.add(max(numbers[1::2]))   # the path's y coordinates (x y pairs): its bottom is the plate's height
    assert heights == {vc.REEL_PLATE_H}, heights


# ---------------------------------------------------------------- C5: the drawn measure
INK = ["COUNTERREVOLUTIONARIES", "Incomprehensibilities", "the Hal Saflieni Hypogeum"]


def _render_reel(words: list[dict], tmp: Path, at: float) -> "np.ndarray":
    import numpy as np
    from PIL import Image
    ass = VideoComposer().build_subtitles(words, tmp / "c.ass")
    png = tmp / "f.png"

    def esc(p: Path) -> str:
        return str(p.resolve()).replace("\\", "/").replace(":", "\\:")
    vf = f"setpts=PTS+{at:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(ROOT / 'fonts')}'"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xF2EEE6:s=1080x1920:r=30",
                    "-vf", vf, "-frames:v", "1", str(png)], check=True, timeout=120)
    return np.asarray(Image.open(png).convert("RGB")).astype(int)


def _plate_and_ink(a) -> tuple[int, int, int, int]:
    """(plate left, plate right, ink left, ink right) in px, from the plate's middle rows (clear of its corners)."""
    import numpy as np
    ys, _ = np.nonzero(a.sum(-1) < 200)
    band = a[ys.min() + 16:ys.max() - 15]
    plate_cols = np.nonzero((band.sum(-1) < 200).any(0))[0]
    pl, pr = int(plate_cols.min()), int(plate_cols.max()) + 1
    ink = band.sum(-1) > 300
    ink[:, :pl + 2] = False
    ink[:, pr - 2:] = False
    cols = np.nonzero(ink.any(0))[0]
    return pl, pr, int(cols.min()), int(cols.max()) + 1


def _reel_gaps(text: str, tmp: Path) -> tuple[int, int]:
    words = [{"word": w, "start": 0.1 + 0.3 * k, "dur": 0.25} for k, w in enumerate(text.split())]
    assert len(caption_phrases(words)) == 1
    a = _render_reel(words, tmp, words[-1]["start"] + 0.2)
    pl, pr, il, ir = _plate_and_ink(a)
    return il - pl, pr - ir


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
@pytest.mark.parametrize("text", INK + ["Beneath Odesa", "an estimated 720 tons"])
def test_reel_ink_sits_inside_its_plate(text, tmp_path):
    """The plate is centred on the drawn ink and pads it by CAPTION_INK_FIT_PAD (K1, operator decision: the
    compliant 28 px everywhere; CAPTION_INK_PAD_MIN 40 is withdrawn), less libass's 2 px."""
    left, right = _reel_gaps(text, tmp_path)
    want = config.CAPTION_INK_FIT_PAD
    assert left >= want - 2 and right >= want - 2, (left, right, want)
    assert abs(left - right) <= 4, (left, right)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
@pytest.mark.parametrize("text", INK)
def test_reel_ink_pad_is_28_px_on_the_widest_bursts(text, tmp_path):
    """K1 (was the strict xfail test_reel_ink_pad_is_36_px_on_the_widest_bursts): the widest bursts keep 26 px or
    more of plate each side (28 less libass's 2 px), the sides within 4 px, and no plate passes 696 px."""
    left, right = _reel_gaps(text, tmp_path)
    assert left >= 26 and right >= 26, (left, right)
    assert abs(left - right) <= 4, (left, right)
    words = [{"word": w, "start": 0.1 + 0.3 * k, "dur": 0.25} for k, w in enumerate(text.split())]
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass")
    for line in ass.read_text("utf-8").splitlines():
        if ",CapBox," in line:
            path = line.split("\\p1}")[1].split("{")[0]
            assert max(int(x) for x in re.findall(r"-?\d+", path)[0::2]) <= 696, line


def require_raqm():
    """Measured widths are pinned to Pillow's raqm layout (operator decision 2026-11-05: raqm everywhere; on Windows
    FriBiDi's DLL must be on PATH, see docs/HANDOVER-design.md). A build without it fails here, by name."""
    from PIL import features
    assert features.check("raqm"), "Pillow raqm layout is unavailable (FriBiDi missing): measured pins are raqm's"


# Pinned to raqm (operator decision 2026-11-05): text -> (fit size, Pillow measure, libass ink width), measured with
# Pillow 12.3.0 / raqm 0.10.5 / FriBiDi 1.0.18 and ffmpeg's libass (CI's WAVY ink, 552, is the same). Operator
# decision 2026-11-05, "measure without kerning": libass draws the captions unkerned (the ASS header has no
# "Kerning: yes") while raqm kerns, so the measure sums single-letter advances (vc.unkerned_length). WAVY Vault (WA,
# AV) measured 545 kerned against libass's 552 and measures 550 now; Beneath, It was, an estimated, Amenhotep gain
# 1 px each. Every sample is now 1 to 2 px under the ink (REEL_INK_OVER_MEASURE).
REEL_MEASURE_RAQM = {
    "Wait, look here!": (80, 443, 444), "Beneath Odesa": (80, 402, 403), "It was": (80, 171, 172),
    "an estimated 720 tons": (80, 639, 640), "shore of the Black Sea": (80, 634, 635),
    "Amenhotep III was": (80, 510, 511), "WAVY Vault 7,000": (80, 550, 552), "COUNTERREVOLUTIONARIES": (77, 634, 635),
    "Incomprehensibilities": (80, 593, 594), "the mine's rock salt": (80, 558, 560),
}
# libass's ink less the unkerned measure, in px: measured 0 to 2 on all 329 one-line 9:16 bursts of the five fixture
# runs (8 at 0, 224 at 1, 97 at 2; the kerned measure was off by up to 5, 55 bursts over 2) and on the ten samples
# (docs/reviews/freeze-panel-2026-11-04/captions/decision-nokern-measure-2026-11-05/README.md).
REEL_INK_OVER_MEASURE = (0, 2)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
def test_reel_measure_matches_libass_ink(tmp_path):
    require_raqm()
    for text, (size_pin, measure_pin, ink_pin) in REEL_MEASURE_RAQM.items():
        words = [{"word": w, "start": 0.1 + 0.3 * k, "dur": 0.25} for k, w in enumerate(text.split())]
        phrases = caption_phrases(words)
        assert len(phrases) == 1, text
        a = _render_reel(words, tmp_path, words[-1]["start"] + 0.2)
        _, _, il, ir = _plate_and_ink(a)
        size = vc._caption_fit_size(phrases[0])
        measured = vc.measure_caption_phrase(phrases[0], size)[0]
        assert (size, measured, ir - il) == (size_pin, measure_pin, ink_pin), (text, size, measured, ir - il)
        lo, hi = REEL_INK_OVER_MEASURE
        assert lo <= (ir - il) - measured <= hi, (text, measured, ir - il)


def test_caption_measure_is_unkerned(tmp_path):
    """Operator decision 2026-11-05: every caption width measure is the sum of single-letter advances (libass draws
    the captions unkerned), not raqm's kerned layout. WAVY kerns under raqm (WA, AV), so the two differ there."""
    require_raqm()
    from src.typography import get_font
    anton = get_font(vc.FONT_NAME, vc._LIBASS_REF_PX)
    wide = get_font(config.CAPTION_WIDE_FONT, config.CAPTION_WIDE_SIZE, weight=700)
    for font in (anton, wide):
        for text in ("WAVY VAULT 7,000", "Wavy Vault", "AVOWAL TYPE", " "):
            assert vc.unkerned_length(font, text) == sum(font.getlength(ch) for ch in text), text
    assert anton.getlength("WAVY") < vc.unkerned_length(anton, "WAVY") - 10   # raqm kerns: the reason for the decision
    for w, h in ((1080, 1920), (1920, 1080)):   # libass draws unkerned: no "Kerning: yes" in either header
        ass = VideoComposer().build_subtitles(_even("Wavy Vault"), tmp_path / f"k{w}.ass", width=w, height=h)
        assert "kerning" not in ass.read_text("utf-8").lower()
    # a 9:16 run's advance is its letters' advances (plus the CapText Spacing after each), at the drawn em
    upm, asc, desc = vc._font_vmetrics(vc.FONT_NAME)
    k = vc.FONT_SIZE_ASS * upm / (asc + desc) / vc._LIBASS_REF_PX
    adv, _, _ = vc._libass_line(("WAVY",), float(FONT_SIZE))
    assert adv == pytest.approx(sum(anton.getlength(ch) * k + vc.REEL_LETTER_SPACING for ch in "WAVY"))
    # the round-2 type measure and the 16:9 fit measure sum the letters too
    f80 = get_font("Anton", FONT_SIZE)
    assert vc._caption_text_width(["WAVY"], FONT_SIZE) == pytest.approx(sum(f80.getlength(ch) for ch in "WAVY"))
    text = "Wavy Vault"
    width = vc.unkerned_length(wide, text) + 2 * config.CAPTION_PAD_X
    assert vc._fits_wide(text.split(), int(width) + 1) and not vc._fits_wide(text.split(), int(width) - 1)


# Real one-line 9:16 bursts of the fixture runs where the kerned measure fell 3 to 5 px short of libass's ink (the
# worst five of 329; decision-nokern-measure-2026-11-05/README.md): (run, burst) -> libass ink width at its fit.
KERN_WORST = {
    ("richat_structure_sahara_eye", "WAS INITIALLY CONSIDERED"): 624,
    ("richat_structure_sahara_eye", "BUILT IN VARIOUS"): 423,
    ("richat_structure_sahara_eye", "ALTERNATIVE SPECULATION"): 611,
    ("gol_gumbaz_whispering_gallery", "GRAY BASALT MAUSOLEUM"): 614,
    ("tunguska_event_1908", "A CATACLYSM STRUCK"): 550,
}


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
@pytest.mark.parametrize("run,text", list(KERN_WORST))
def test_unkerned_measure_matches_libass_ink_on_real_bursts(run, text, tmp_path, monkeypatch):
    """The unkerned measure is within REEL_INK_OVER_MEASURE of the ink libass draws on the real bursts the kerned
    measure got most wrong; the kerned measure (raqm's layout, as before the decision) is more than 2 px short."""
    require_raqm()
    _, phrases = run_layout(run, False)
    phrase = next(p for p in phrases if " ".join(vc._phrase_words(p)).upper() == text)
    words = [{"word": w["word"], "start": 0.1 + 0.3 * k, "dur": 0.25} for k, w in enumerate(phrase)]
    assert len(caption_phrases(words)) == 1, text
    size = vc._caption_fit_size(phrase)
    a = _render_reel(words, tmp_path, words[-1]["start"] + 0.2)
    _, _, il, ir = _plate_and_ink(a)
    ink, measured = ir - il, vc.measure_caption_phrase(phrase, size)[0]
    assert ink == KERN_WORST[(run, text)], (text, ink)
    lo, hi = REEL_INK_OVER_MEASURE
    assert lo <= ink - measured <= hi, (text, measured, ink)
    vc._libass_line.cache_clear()
    monkeypatch.setattr(vc, "unkerned_length", lambda font, t: font.getlength(t))
    try:
        assert ink - vc.measure_caption_phrase(phrase, size)[0] > 2, text
    finally:
        vc._libass_line.cache_clear()


def test_ink_budget_is_the_plate_less_the_pad():
    from src.safe_zones import REEL
    # QF-24 (decision QF-24/QF-87 layout, deliberate change): the reviewed 640 px is kept, its plate centred in the
    # organic box (centred on the frame that box leaves 616 px; around its centre, x 480, 734); it was the ads box's
    # centred width (696) less the pad
    assert vc._ink_budget() == vc.MAX_CAPTION_WIDTH + 2 * config.CAPTION_PAD_X - 2 * config.CAPTION_INK_FIT_PAD == 640
    assert REEL.centred_width() == 616 and vc._ink_budget() + 2 * config.CAPTION_INK_FIT_PAD <= vc.reel_caption_span()
    assert CAPTION_INK_PAD_MIN == 40
    assert vc.MAX_CAPTION_WIDTH == 640 and config.CAPTION_PAD_X == 28   # unchanged


# ---------------------------------------------------------------- C6: sentence ends
def _sentence_tail_ok(text: str) -> bool:
    """No sentence terminator followed by exactly one or two words inside the burst."""
    words = text.split()
    for k, w in enumerate(words[:-1]):
        if re.search(r"[.?!]$", w) and len(words) - k - 1 in (1, 2):
            return False
    return True


@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_no_burst_runs_past_a_sentence_end(nid):
    for wide in (False, True):
        words, phrases = layout(nid, wide)
        head = _head_cuts(words, NARRATIONS[nid][1], wide)
        at = -1
        for p in phrases:
            raw = [w["word"] for w in p]
            start, at = at + 1, at + len(p)
            inner = [start + j for j, w in enumerate(raw[:-1]) if re.search(r"[.?!]$", w)]
            if inner and not any(k in head for k in inner):
                continue   # gate: HEAD 0036b18 joins this sentence end too (fix round 1)
            assert _sentence_tail_ok(" ".join(raw)), (nid, wide, raw)
            for w in raw[:-1]:   # nor any terminator inside a burst but a joined short sentence's
                if re.search(r"[.?!]$", w):
                    assert len(raw) - raw.index(w) - 1 >= 3, raw


def test_short_sentences_stand_alone_for_half_a_second():
    texts = _texts(layout("edge", False)[1])
    # fix round 1 (gate): HEAD 0036b18's NO. IT WAS (was: NO alone or NO. IT WAS HIDDEN ...)
    assert "NO. IT WAS" in texts, texts
    assert "LOOK" in texts
    for nid in NARRATIONS:   # every standalone sentence of one or two words is up at least half a second
        phrases, tim, _ = reel_timings(nid)
        for k, (p, (show, hide, _)) in enumerate(zip(phrases, tim)):
            opens = k == 0 or re.search(r"[.?!]$", phrases[k - 1][-1]["word"])
            if opens and len(p) <= 2 and re.search(r"[.?!]$", p[-1]["word"]):
                assert hide - show >= config.CAPTION_SHORT_SENTENCE_MIN_S - 1e-6, (nid, _texts([p]), hide - show)


def test_wide_question_ends_its_burst():
    texts = _texts(layout("edge", True)[1])
    # fix round 1 (gate): HEAD 0036b18 joins NO. to the question's line (was: the line ends on MEMNON?)
    assert "DISMANTLED THE COLOSSI OF MEMNON? NO" in texts, texts


def test_a_short_sentence_under_half_a_second_joins_the_following_burst():
    words = [{"word": "No.", "start": 0.0, "dur": 0.15}, {"word": "It", "start": 0.2, "dur": 0.1},
             {"word": "was", "start": 0.33, "dur": 0.12}, {"word": "hidden.", "start": 0.48, "dur": 0.3},
             {"word": "Stone", "start": 1.6, "dur": 0.3}, {"word": "walls.", "start": 1.95, "dur": 0.4}]
    texts = _texts(caption_phrases(words))
    assert texts[0] == "NO. IT WAS HIDDEN", texts
    # never onto the burst before
    words2 = [{"word": "Stone", "start": 0.0, "dur": 0.3}, {"word": "walls.", "start": 0.34, "dur": 0.3},
              {"word": "No.", "start": 0.7, "dur": 0.12}, {"word": "Gold.", "start": 0.9, "dur": 0.3}]
    phrases2 = caption_phrases(words2)
    # fix round 1 (gate): the bursts are HEAD 0036b18's, which joins NO. GOLD. onto STONE WALLS. (was: never onto the
    # burst before)
    assert set(_ends(phrases2)[:-1]) == _head_cuts(words2), _texts(phrases2)


# ---------------------------------------------------------------- C7: figure units
def test_figure_units_exact():
    odessa = _texts(layout("odessa_catacombs", False)[1])
    k = odessa.index("BEGAN ON AUGUST 5")
    assert odessa[k + 1].startswith("AND LASTED"), odessa
    edge = _texts(layout("edge", False)[1])
    # fix round 1 (gate): BETWEEN 287 AND 300 KILOMETRES cannot hold at 66 px; HEAD 0036b18 cuts the range after AND
    # and the tree takes HEAD's cut (was: no burst ends on 287 AND)
    assert "AGO, BETWEEN 287 AND" in edge and "300 KILOMETRES BELOW" in edge, edge
    aswan = _texts(layout("unfinished_obelisk_aswan", False)[1])
    # fix round 2 (review blocker 1, operator decision 2026-11-04 as written: only a name bond or a width or floor
    # limit forces a cut HEAD 0036b18 lacks): a figure link HEAD cut holds only where that costs no such cut. Aswan keeps
    # HEAD's FROM NEARLY 1,090 | TONNES TO 1,168 TONNES. and (42 M), WITH WEIGHT, and colossi HEAD's (18 M) INCLUDING
    # (was: no burst ends on 1,090 or opens on TONNES or a bracket); ROUGHLY 137 FEET (41.75 M) is held (HEAD cut it)
    assert "ROUGHLY 137 FEET (41.75 M)" in aswan, aswan
    assert "FROM NEARLY 1,090" in aswan and "TONNES TO 1,168 TONNES" in aswan, aswan
    colossi = _texts(layout("singing_colossi_of_memnon", False)[1])
    assert "(18 M) INCLUDING" in colossi and "THEIR 13-FOOT (4 M)" in colossi, colossi
    # fix round 1 (gate): ROCK SALT is a key term, no figure link: at 9:16 it keeps HEAD's THE MINE'S ROCK | SALT
    # NATURALLY (was: THE MINE'S ROCK SALT in both formats)
    assert "THE MINE'S ROCK" in _texts(layout("wieliczka_salt_mine", False)[1])
    wiel = _texts(layout("wieliczka_salt_mine", True)[1])
    assert any("THE MINE'S ROCK SALT" in t for t in wiel), wiel


_UNITS = vc.MEASURE_UNITS | vc.ERA_DESIGNATORS


@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_no_figure_split_from_its_unit(nid):
    # fix round 2 (review blocker 1, the decision as written): a figure, its unit or its bracketed conversion is split
    # only where HEAD 0036b18 splits it too (was: never)
    for wide in (False, True):
        words, phrases = layout(nid, wide)
        head = _head_cuts(words, NARRATIONS[nid][1], wide)
        texts = [[w["word"] for w in p] for p in phrases]
        ends = _ends(phrases)
        for k, raw in enumerate(texts):
            first = raw[0]
            at_head = k > 0 and ends[k - 1] in head
            assert not first.startswith("(") or at_head, (nid, wide, raw)
            assert (vc._norm(first) not in _UNITS or k == 0 or not any(c.isdigit() for c in texts[k - 1][-1])
                    or at_head), (nid, wide, texts[k - 1], raw)
            if k + 1 < len(texts) and any(c.isdigit() for c in raw[-1]):
                nxt = texts[k + 1][0]
                assert (not nxt.startswith("(") and vc._norm(nxt) not in _UNITS) or ends[k] in head, \
                    (nid, wide, raw, texts[k + 1])


def test_atomic_dates_conversions_and_units():
    atomic = vc._is_atomic_boundary
    assert atomic("August", "5") and atomic("5", "August") and atomic("May", "1941")
    assert not atomic("may", "1941")                      # the verb, not the month
    assert atomic("feet", "(18") and atomic("(18", "m)") and atomic("13-foot", "(4")
    assert atomic("1,090", "tonnes") and atomic("300", "kilometers") and atomic("metric", "tons")
    assert atomic("year", "196")
    assert not atomic("m)", "including")


# ---------------------------------------------------------------- C8: cohesion
DANGLE_WORDS = {"the", "a", "an", "of", "to", "and", "or", "with"}


def _dangling(raw_last: str) -> bool:
    """The C8 acceptance list: the, a, an, of, to, and, or, with, a pre-modifier, a possessive, a hyphenated measure."""
    bare = vc._bare(raw_last)
    core = raw_last.strip("\"'“”‘’()[]")
    if re.search(r"[,;:.?!]$", core):
        return False
    return (bare in DANGLE_WORDS or bare in vc.PRE_MODIFIERS or bool(re.search(r"['’]s$", core, re.I))
            or bool(vc.HYPHEN_MEASURE.match(core)))


def _sentences(words):
    out, cur, off = [], [], 0
    for i, w in enumerate(words):
        if not cur:
            off = i
        cur.append(w)
        if vc.TERMINAL_PUNCT.search(w["word"]) and not vc._is_abbreviation(w["word"]):
            out.append((off, cur))
            cur = []
    if cur:
        out.append((off, cur))
    return out


def _chunk_cost(sent, off, bonds, i, j, named=frozenset()):
    """(allowed, over, hard, soft) of the 9:16 burst sent[i:j] under the round-2 rules, as the partition reads them:
    no cut inside a bond or an atomic pair; A1 no end on an article but before a hedge and figure group that cannot
    hold it; never a function-word burst but a one-word lead-in before a name (N1); the word and character limits
    unless bonded (bonded_burst_allowed, a whole figure group within the extra words and characters, a whole name on
    one line at the bond floor, or a two-line name: reel_caption_lines); a burst of 2+ words fits at 74 px, at 66 px
    when bonded, or is a two-line name or a whole figure group. Review round 4 tiers: hard defects (a function-word
    burst 2, a mid-sentence end on a function-word tail 1), then the soft cost (a lone content word
    LONE_WORD_COST; a mid-sentence pre-modifier, possessive, hyphenated-measure or attributive end SOFT_END_COST;
    clause punctuation then two words or fewer mid-sentence SOFT_END_COST (P1); a one-word lead-in LEAD_IN_COST;
    a capitalised word, not a name's end, before a lower-case content word 1 and the open noun phrase end 1, M1)."""
    n = len(sent)
    raw = [w["word"] for w in sent]

    def linked(k):
        return off + k in bonds or vc._is_atomic_boundary(raw[k], raw[k + 1])
    size = j - i
    chunk = sent[i:j]
    if j < n and linked(j - 1):
        return False, 0, 0, 0
    if j < n and vc._bare(raw[j - 1]) in ("the", "a", "an"):
        e = j
        while e < n - 1 and linked(e):
            e += 1
        group = " ".join(raw[j:e + 1])
        if not (any(c.isdigit() for c in group) and not vc._fits_caption_bonded(raw[j - 1:e + 1])):
            return False, 0, 0, 0
    rest_names = vc.name_bonds(raw[j:]) if j < n else set()
    lead = (size == 1 and j < n and vc._bare(raw[i]) in vc.LEAD_IN_WORDS and vc._bare(raw[i]) not in vc._ARTICLE_SLOT
            and (0 in rest_names or (vc._bare(raw[j]) in vc._ARTICLE_SLOT and 1 in rest_names))
            and float(sent[j]["start"]) - float(sent[i]["start"]) >= config.CAPTION_DWELL_AFTER_LAST_S - 1e-9)
    if size < n and vc._function_only(chunk) and not lead:
        return False, 0, 0, 0
    txt = " ".join(vc._phrase_words(chunk))
    inner = all(linked(i + k) for k in range(1, size - 1))
    whole = size > 1 and all(linked(i + k) for k in range(size - 1))
    two = size > 1 and vc.reel_caption_lines(chunk) is not None
    figure = whole and any(c.isdigit() for c in txt)
    name_group = whole and bool(vc.name_bonds(raw[i:j])) and vc._fits_caption_bonded(chunk)
    if (size > MAX_BURST_WORDS or len(txt) > MAX_BURST_CHARS) and not two:
        if not (inner and (vc.bonded_burst_allowed(raw[i:j]) or name_group or (
                figure and size <= MAX_BURST_WORDS + config.CAPTION_BOND_EXTRA_WORDS
                and len(txt) <= MAX_BURST_CHARS + config.CAPTION_BOND_EXTRA_CHARS))):
            return False, 0, 0, 0
    if size > 1 and not vc._fits_caption_burst(chunk) and not (inner and vc._fits_caption_bonded(chunk)) \
            and not two and not figure:
        return False, 0, 0, 0
    over = 1 if size > 1 and not two and not vc._fits_caption_bonded(chunk) else 0
    hard = soft = 0
    if vc._function_only(chunk) and not lead and size < n:
        hard = 2
    elif j < n and not lead and vc._hard_dangles(raw[j - 1]):
        hard = 1
    elif j < n and not lead and (vc._dangles(raw[j - 1]) or vc._attributive_end(raw, j - 1)):
        soft += vc.SOFT_END_COST
    if j < n and any(vc.CLAUSE_PUNCT.search(raw[k]) and j - 1 - k <= 2 for k in range(i, j - 1)):
        soft += vc.SOFT_END_COST
    if size == 1 and n > 1:
        soft += vc.LEAD_IN_COST if lead else vc.LONE_WORD_COST
    if j < n and j - 1 > 0:
        core, nxt = raw[j - 1].strip("\"'“”‘’()[]"), raw[j]
        is_name_end = off + j - 2 in named and off + j - 1 not in named
        soft += int(core[:1].isupper() and not vc.CLAUSE_PUNCT.search(raw[j - 1]) and nxt[:1].islower()
                    and vc._bare(nxt) not in FUNCTION_WORDS and not is_name_end)
    soft += vc._open_phrase_end(raw, i, j - 1, j >= n)
    return True, over, hard, soft


def _fewest(sent, off, bonds, named=frozenset()):
    """The least (bursts over the bond floor, hard defects, soft cost) any 9:16 split the round-2 rules allow has
    (_chunk_cost), by brute force over every split."""
    from functools import lru_cache
    n = len(sent)
    top = MAX_BURST_WORDS + vc.NAME_MAX_WORDS + 2

    @lru_cache(maxsize=None)
    def best(i):
        if i == n:
            return (0, 0, 0)
        found = None
        for size in range(1, min(n - i, top) + 1):
            ok, over, hard, soft = _chunk_cost(sent, off, bonds, i, i + size, named)
            if not ok:
                continue
            rest = best(i + size)
            if rest is None:
                continue
            cand = (rest[0] + over, rest[1] + hard, rest[2] + soft)
            found = cand if found is None else min(found, cand)
        return found
    return best(0)


@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_reel_split_has_the_fewest_dangling_ends_the_rules_allow(nid):
    """C8 (round-2 rules, review round 4 tiers): a 9:16 burst ends on a dangling word, or stands as a lone word, only
    where every split the rules allow (the ink budget, the bonds, the sentence ends, A1, N1, P1) costs as much: the
    partition's own split has the least (over, hard, soft) of any (brute force)."""
    text, emphasis = NARRATIONS[nid]
    words = timed(text)
    bonds = vc._relax_figure_bonds(words, caption_bonds(words) | vc.emphasis_bonds(words, emphasis))
    phrases = partition_words_grammatical(words, bonds=bonds)
    named = frozenset(name_bonds([w["word"] for w in words]) & bonds)
    k = 0
    for off, sent in _sentences(words):
        split = []
        while k < len(phrases) and sum(map(len, split)) < len(sent):
            split.append(phrases[k])
            k += 1
        own, at = [0, 0, 0], 0
        for c in split:
            ok, over, hard, soft = _chunk_cost(sent, off, bonds, at, at + len(c), named)
            assert ok, (nid, _texts(split), _texts([c]))
            own[0] += over
            own[1] += hard
            own[2] += soft
            at += len(c)
        fewest = _fewest(sent, off, bonds, named)
        assert fewest is not None
        assert tuple(own) == fewest, (_texts(split), own, fewest)


@pytest.mark.parametrize("nid", REAL)
def test_dangling_ends_on_the_real_scripts(nid):
    """Round 2 (A1): the C8 list of width-forced article endings (PARTISANS USED THE, TODAY, THE, INTRODUCED IN THE,
    16:9 AS ONE OF ...) is deleted; no burst of these scripts ends on THE, A or AN in either format."""
    for wide in (False, True):
        found = {" ".join(vc._phrase_words(p)).upper() for p in layout(nid, wide)[1][:-1]
                 if vc._bare(p[-1]["word"]) in ("the", "a", "an")}
        assert found == set(), (nid, wide, sorted(found))


def test_cohesion_exact():
    names = _texts(layout("names", False)[1])
    assert ("AT DAWN," in names and "AND THE GREAT WALL OF GORGAN" in names) or (
        "AT DAWN, AND" in names and "THE GREAT WALL OF GORGAN" in names), names
    wide = _texts(layout("singing_colossi_of_memnon", True)[1])
    # fix round 1 (gate): HEAD 0036b18's 16:9 ORIGINALLY CARVED TO GUARD THE MEMORIAL | TEMPLE WHERE ... (was: THE
    # MEMORIAL TEMPLE in one line); its 9:16 burst keeps THE MEMORIAL TEMPLE
    assert "TEMPLE WHERE AMENHOTEP III WAS WORSHIPPED" in wide, wide
    assert "THE MEMORIAL TEMPLE" in _texts(layout("singing_colossi_of_memnon", False)[1])


def test_dangles_reads_possessives_measures_and_pre_modifiers():
    assert vc._dangles("mine's") and vc._dangles("granite’s") and vc._dangles("13-foot")
    assert vc._dangles("memorial") and vc._dangles("the")
    assert not vc._dangles("late,") and not vc._dangles("tunnels") and not vc._dangles("was")


# ---------------------------------------------------------------- C9: cue-to-cut
# Boundaries within six frames of a cut that snap_to_cuts may not move onto it, each with the _valid rule a move
# would break (measured on the harness timings): (narration, the burst whose hide it is). The 0.8 ratio below is
# HEAD's and applies to every other near boundary (review round 3: the ratio is not loosened).
# Fix round 1 (gate): STILL, THE COLOSSI HAVE is HEAD 0036b18's STILL, THE COLOSSI | HAVE STOOD again and left the
# list; the ratio stays 0.8 x near (operator decision 2026-11-04).
# Fix round 2 (the decision as written): aswan WITH WEIGHT is HEAD 0036b18's (42 M), WITH WEIGHT again (its figure link
# no longer moves the cut), its hide is no longer near a cut, and it left the list; none is blocked now.
SNAP_BLOCKED: dict = {}


def test_no_reel_boundary_one_to_three_frames_off_a_cut():
    frame = 1.0 / FPS
    near, on, blocked = 0, 0, set()
    for nid in REAL:
        phrases, tim, cuts = reel_timings(nid)
        for k, (show, hide, _) in enumerate(tim):
            for t, side in ((show, "show"), (hide, "hide")):
                d = min(abs(t - c) for c in cuts) / frame
                assert not (0.5 < d < 3.5), (nid, t, d)
                if d < 5.5:   # within six frames, the fallback's own six-frame move apart (snap_to_cuts)
                    owner = _upper(phrases[k] if side == "hide" else phrases[k - 1]) if (side == "hide" or k) else ""
                    if d >= 0.5 and (nid, owner) in SNAP_BLOCKED:
                        blocked.add((nid, owner))
                        continue
                    near += 1
                    on += d < 0.5
        for p, (show, _, _) in zip(phrases, tim):
            assert show <= float(p[0]["start"]) + frame + 1e-6
    assert blocked == set(SNAP_BLOCKED), sorted(blocked ^ set(SNAP_BLOCKED))
    assert on >= 0.8 * near, (on, near)


# ---------------------------------------------------------------- C10: dwell
# Bursts the speech leaves less than the dwell after their last word (the next word follows at once) and no
# neighbour in the clause can take: (narration, burst).
# Fix round 1 (gate): FROM THE WAIST UP is HEAD 0036b18's FROM THE WAIST | UP AND CRACKING again and left the list.
DWELL_EXCEPTIONS = {
    ("odessa_catacombs", "BEGAN ON AUGUST 5"),
    ("singing_colossi_of_memnon", "THEIR 13-FOOT (4 M)"),
    # fix round 2 (the decision as written): OF 60 FEET (18 M) is HEAD 0036b18's HEIGHT OF 60 FEET | (18 M) INCLUDING
    # again and left the list
    ("unfinished_obelisk_aswan", "ROUGHLY 137 FEET (41.75 M)"),
}


# Fix round 1 (gate): figure bursts the speech leaves under CAPTION_DIGIT_MIN_S that HEAD 0036b18 forms too and no
# neighbour can take.
DIGIT_SHORT = {
    ("odessa_catacombs", "THE 1941"): "HEAD's THE 1941 | SIEGE OF ODESA: BY ONE ESTIMATE, THE 1941 crosses the comma and "
                                      "THE 1941 SIEGE OF ODESA is 5 words, no bonded run",
}


@pytest.mark.parametrize("nid", REAL)
def test_reel_dwell(nid):
    phrases, tim, _ = reel_timings(nid)
    short = set()
    for p, (show, hide, rev) in zip(phrases, tim):
        text = " ".join(vc._phrase_words(p)).upper()
        if any(c.isdigit() for c in text) and (nid, text) not in DIGIT_SHORT:
            assert hide - show >= CAPTION_DIGIT_MIN_S - 1e-6, (nid, text, hide - show)
        if hide - rev[-1] < CAPTION_DWELL_AFTER_LAST_S - 1e-6:
            short.add((nid, text))
    assert short == {e for e in DWELL_EXCEPTIONS if e[0] == nid}, sorted(short ^ {e for e in DWELL_EXCEPTIONS if e[0] == nid})


# ---------------------------------------------------------------- C11: gold punctuation
BONE, GOLD = "&H00E2EDF3&", "&H0030B6F2&"


def test_gold_word_punctuation_is_bone(tmp_path):
    assert vc.COL_PAST == BONE and vc.COL_EMPHASIS == GOLD
    words = _even("Who dismantled the Colossi of Memnon? Nobody knows.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["Colossi of Memnon"])
    # the burst's last event: every word shown, MEMNON gold (a full-size M, small-capital EMNON), then a bone ?
    last = [l for l in ass.read_text("utf-8").splitlines() if ",CapText," in l and "EMNON" in l][-1]
    tags = re.findall(r"\{([^}]*)\}([^{]*)", last.split(",,", 1)[1])
    memnon = [c for tag, text in tags for c in [re.search(r"\\c(&H[0-9A-F]+&)", tag)] if "EMNON" in text or text == "M"]
    assert all(m and m.group(1) == GOLD for m in memnon), tags
    question = [tag for tag, text in tags if text.strip() == "?"]
    assert question and BONE in question[0], tags


def test_wide_gold_comma_is_bone_and_figures_stay_gold(tmp_path):
    words = _even("Beneath Odesa, the tunnels sheltered 7,000 people.")
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass", emphasis=["Odesa", "7,000 people"],
                                          width=1920, height=1080)
    text = "\n".join(l for l in ass.read_text("utf-8").splitlines() if ",CapText," in l)
    assert f"{{\\c{GOLD}}}Odesa{{\\c{BONE}}}," in text, text
    assert f"{{\\c{GOLD}}}7,000" in text


def test_format_word_caption_gold_tail():
    out = vc.format_word_caption("Memnon?", GOLD, True, False, True)
    assert out.endswith(f"{{\\c{BONE}\\fscx{config.CAPTION_SC_SCALE}\\fscy{config.CAPTION_SC_SCALE}}}?"), out
    assert "?" not in out.split(BONE)[0]
    assert vc.gold_tail("7,000") == ("7,000", "") and vc.gold_tail("13-FOOT,") == ("13-FOOT", ",")
    plain = vc.format_word_caption("Memnon?", BONE, False, False, False)
    assert plain.endswith("?") and BONE in plain


# ---------------------------------------------------------------- C12: sidecar cues
def _cues(nid):
    return partition_sidecar_cues(timed(NARRATIONS[nid][0]))


def _head_cue(words, pos: int, length: int) -> bool:
    """Fix round 1 (gate): whether the cue of `length` words from `pos` opens and ends where a HEAD 0036b18 cue does."""
    heads = _head_cue_ends(words)
    last = pos + length - 1
    return (pos == 0 or pos - 1 in heads) and (last == len(words) - 1 or last in heads)


@pytest.mark.parametrize("nid", list(NARRATIONS))
def test_sidecar_rules(nid):
    cues = _cues(nid)
    words = timed(NARRATIONS[nid][0])
    assert [w for c in cues for w in c[2].split()] == [w["word"] for w in words]
    terms = [w["word"] for w in words]
    pos = 0
    for start, end, text in cues:
        ws = text.split()
        lines = text.split("\n")
        assert len(lines) <= config.SIDECAR_MAX_LINES and all(len(l) <= config.SIDECAR_MAX_LINE_CHARS for l in lines)
        assert end - start <= config.SIDECAR_MAX_CUE + 1e-6
        if _head_cue(words, pos, len(ws)):
            # fix round 1 (gate): HEAD 0036b18's own cue; it never ends on an article (the operator's rule), its
            # other ends and its round-4 line break are HEAD's
            assert vc._bare(ws[-1]) not in ("the", "a", "an") or pos + len(ws) == len(words), (nid, text)
            pos += len(ws)
            continue
        assert end - start >= config.SIDECAR_MIN_CUE_HARD - 1e-6, (nid, text, end - start)
        sentence_start = pos == 0 or re.search(r"[.?!]$", terms[pos - 1])
        if len(ws) == 1 or (len(ws) == 2 and not sentence_start):
            raise AssertionError((nid, text))
        if pos > 0 and re.search(r"[.?!]$", ws[0]) and not re.search(r"[.?!]$", terms[pos - 1]):
            raise AssertionError(("a cue opens on a sentence's last word", nid, text))
        for line in lines:
            assert vc._bare(line.split()[-1]) not in {"the", "a", "an", "of", "to", "and"}, (nid, text)
        pos += len(ws)


def test_sidecar_keeps_groups_in_one_cue_and_line():
    # fix round 1: a figure link holds in the cues as in the bursts (HEAD 0036b18 split 196 | AD. across two cues),
    # and a base cue no freeze-pass wrap fits keeps its line break outside a bond or an atomic pair
    # fix round 2 (review blocker 1, the decision as written): a figure link no longer forces a cue end HEAD lacks, so
    # the colossi keep HEAD's cue end 196 | AD. (STATUE VOCALIZING DATES TO THE YEAR 196 AD. is 43 characters, over the
    # line; was: 196 AD in one line, with two cue ends HEAD lacks, RECORD | OF and AD. | LOCAL)
    words = timed(NARRATIONS["singing_colossi_of_memnon"][0])
    ends = set(_ends([t.split() for _, _, t in _cues("singing_colossi_of_memnon")])[:-1])
    assert ends <= _head_cue_ends(words), sorted(ends - _head_cue_ends(words))   # no cue end HEAD lacks
    assert [w["word"] for w in words].index("196") in ends
    for nid, group in (("names", "Hal Saflieni Hypogeum"),):
        cues = _cues(nid)
        assert any(group in line for _, _, t in cues for line in t.split("\n")), (nid, [c[2] for c in cues])
    # fix round 1 (gate): wieliczka keeps HEAD 0036b18's cues, ... SITES IN | 1978. ... and the one-word cue SALT.
    # (no name, figure link or article ends there; was: SITES IN 1978 in one line and no SALT. cue)
    words = timed(NARRATIONS["wieliczka_salt_mine"][0])
    assert set(_ends([t.split() for _, _, t in _cues("wieliczka_salt_mine")])[:-1]) == _head_cue_ends(words)


def test_sidecar_lines_are_balanced():
    for nid in NARRATIONS:
        for _, _, text in _cues(nid):
            lines = text.split("\n")
            if len(lines) == 2:
                short, long_ = sorted(map(len, lines))
                assert short >= config.SIDECAR_LINE_BALANCE * long_ or (nid, text) in {
                    ("unfinished_obelisk_aswan", "Surveys place its length\nbetween roughly 137 feet (41.75 m)"),
                    # S1 (round 2): no single-word line in a cue of four words or more, so the one-word sentence
                    # NO., which must join a cue (it is up under SIDECAR_MIN_CUE_HARD alone), shares line 1 with IT
                    ("edge", "No. It\nwas hidden underground for centuries,"),
                    # S1 (1): a sentence's last words stay in its cue (no cue opens on BELOW THE SEA.)
                    ("edge", "between 287 and 300 kilometres\nbelow the sea."),
                }, (nid, text)


# ---------------------------------------------------------------- C1: the 16:9 font (operator decision pending)
STATIC_BOLD = ROOT / "fonts" / "Montserrat-Bold.ttf"
AWAITING = "awaiting operator approval of static Montserrat Bold"


def test_wide_font_resolves():
    assert STATIC_BOLD.exists()   # W1: the static face is bundled (operator decision), so the test runs
    assert vc.libass_font_problems(Path("fonts"), [(config.CAPTION_WIDE_FONT, 700)]) == []


def _wide_plate_gaps(text, tmp_path, shrink_plate: int = 0):
    """(ink left - plate left, plate right - ink right, top gap, bottom gap) of a 16:9 burst on flat #F2EEE6. The
    vertical gaps are measured clear of the plate's rounded corners: CAPTION_BORDER_RADIUS + 2 columns each end (W1;
    the corners' background read as ink). `shrink_plate` narrows the drawn plate by that many px a side and lowers
    its height as much (the negative check)."""
    import numpy as np
    from PIL import Image
    words = [{"word": w, "start": 0.1 + 0.3 * k, "dur": 0.25} for k, w in enumerate(text.split())]
    ass = VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080)
    if shrink_plate:
        lines = ass.read_text("utf-8").splitlines()
        for i, line in enumerate(lines):
            if ",CapBox," in line:
                x, y = map(int, re.search(r"\\pos\((-?\d+),(-?\d+)\)", line).groups())
                path = line.split("\\p1}")[1].split("{")[0]
                nums = [int(v) for v in re.findall(r"-?\d+", path)]
                w, h = max(nums[0::2]), max(nums[1::2])
                new = vc.rounded_rect_path(w - 2 * shrink_plate, h - shrink_plate, config.CAPTION_BORDER_RADIUS)
                lines[i] = line.replace(f"\\pos({x},{y})", f"\\pos({x + shrink_plate},{y + shrink_plate})").replace(
                    path, new)
        ass.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def esc(p: Path) -> str:
        return str(p.resolve()).replace("\\", "/").replace(":", "\\:")
    png = tmp_path / "w.png"
    vf = f"setpts=PTS+{words[-1]['start']:.3f}/TB,subtitles='{esc(ass)}':fontsdir='{esc(ROOT / 'fonts')}'"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xF2EEE6:s=1920x1080:r=30",
                    "-vf", vf, "-frames:v", "1", str(png)], check=True, timeout=120)
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    pl, pr, il, ir = _plate_and_ink(a)
    plate_rows = np.nonzero((a.sum(-1) < 200).any(1))[0]
    edge = config.CAPTION_BORDER_RADIUS + 2
    cols = (np.arange(a.shape[1]) > pl + edge) & (np.arange(a.shape[1]) < pr - edge)
    ink_rows = np.nonzero(((a.sum(-1) > 300) & cols)[plate_rows.min():plate_rows.max()].any(1))[0]
    return il - pl, pr - ir, int(ink_rows.min()), int((plate_rows.max() - plate_rows.min()) - ink_rows.max())


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
@pytest.mark.parametrize("text", ["Under the Rashtrakuta dynasty", "temple where Amenhotep III was worshipped",
                                  "MAKING THE CATACOMBS A NATURAL UNDERGROUND"])
def test_wide_ink_sits_inside_its_plate(text, tmp_path):
    left, right, top, bottom = _wide_plate_gaps(text, tmp_path)
    assert left >= 24 and right >= 24, (left, right)
    assert top >= 8 and bottom >= 8, (top, bottom)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
def test_wide_ink_measure_catches_a_narrow_plate(tmp_path):
    """W1 (c): the same measure fails a plate 20 px narrower each side and 20 px lower."""
    left, right, top, bottom = _wide_plate_gaps("Under the Rashtrakuta dynasty", tmp_path, shrink_plate=20)
    assert not (left >= 24 and right >= 24 and top >= 8 and bottom >= 8), (left, right, top, bottom)


def test_no_wide_plate_over_1500(tmp_path):
    assert config.CAPTION_WIDE_MAX_PLATE == 1500
    for nid in NARRATIONS:
        text, emphasis = NARRATIONS[nid]
        ass = VideoComposer().build_subtitles(timed(text), tmp_path / f"{nid}.ass", emphasis=emphasis,
                                              width=1920, height=1080)
        for line in ass.read_text("utf-8").splitlines():
            if ",CapBox," in line:
                x = int(re.search(r"\\pos\((-?\d+),", line).group(1))
                assert 2 * (960 - x) <= 1500, (nid, line[:80])


# ================================================================ round 2 (operator decisions, 2026-11-04)
# Real inputs: the five runs of the freeze-panel catalogue (docs/reviews/freeze-panel-2026-11-04/fixtures), each with
# its own words.json timings and emphasis_words, in its own format (Richat 1792740961_long is the 16:9 run).
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
import fixtures as _fx  # noqa: E402

RUNS = {c["topic"]: c for c in _fx.load("captions")}
RUN_WIDE = {t: c["format"] == "16:9" for t, c in RUNS.items()}
_RUN_CACHE: dict = {}


def run_layout(topic: str, wide: bool | None = None):
    """(words, phrases) of a real run as build_subtitles forms them (its own format unless `wide` says)."""
    wide = RUN_WIDE[topic] if wide is None else wide
    key = (topic, wide)
    if key not in _RUN_CACHE:
        c = RUNS[topic]
        words = _fx.words(c)
        _RUN_CACHE[key] = (words, caption_phrases(words, list(c.get("emphasis_words") or []), wide))
    return _RUN_CACHE[key]


def run_timings(topic: str):
    words, phrases = run_layout(topic)
    wide = RUN_WIDE[topic]
    return phrases, vc.burst_timings(phrases, first_frame_whole=None if wide else config.CAPTION_FIRST_FRAME_WHOLE_S,
                                     dwell=not wide)


# The review sentences (tests/test_loop_seam_and_captions_2026_11_03.py) and, for N3, the real Voynich run's last
# sentence (run_voynich_manuscript_1792965308 words.json), with the harness timings.
REVIEW_SENTENCES = [
    "Built under King Krishna I of the Rashtrakuta dynasty.",
    "They removed an estimated 200,000 tons of rock.",
    "No one knows who carved the statues or why they stopped.",
    "American and British military cryptographers from both World War One and World War Two attempted to decipher "
    "the codex without success.",
]


def _all_layouts():
    """Every (label, words, phrases, wide) of NARRATIONS, the review sentences and the five real runs, both formats."""
    out = []
    for nid in NARRATIONS:
        for wide in (False, True):
            words, phrases = layout(nid, wide)
            out.append((nid, words, phrases, wide))
    for k, text in enumerate(REVIEW_SENTENCES):
        words = timed(text)
        for wide in (False, True):
            out.append((f"review{k}", words, caption_phrases(words, None, wide), wide))
    for topic in RUNS:
        for wide in (False, True):
            words, phrases = run_layout(topic, wide)
            out.append((topic, words, phrases, wide))
    return out


def _upper(p) -> str:
    return " ".join(vc._phrase_words(p)).upper()


def _plain(text: str) -> str:
    return re.sub(r"[^\w' ]", "", text.upper())


# ---------------------------------------------------------------- V1: the 16:9 picture-chain vignette
def test_picture_vignette_is_the_sar_on_16x9_and_unchanged_on_9x16():
    assert vc.picture_vignette(1080, 1920) == "vignette=PI/4.2:aspect=9/16"
    assert vc.picture_vignette(1920, 1080) == "vignette=PI/6.4:aspect=1"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
def test_compose_video_draws_the_helper_vignette(tmp_path, monkeypatch):
    """compose_video's own ffmpeg filter graph (captured, not run) holds picture_vignette's output for the 16:9
    master and no 16/9 aspect."""
    from src import delivery_profiles
    captured: list[list[str]] = []

    class Stop(Exception):
        pass

    def fake_ffmpeg(cmd):
        if "-filter_complex" in cmd and any("[v_final]" in str(c) for c in cmd):
            captured.append([str(c) for c in cmd])
            raise Stop()
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(vc, "_run_ffmpeg", fake_ffmpeg)
    comp = VideoComposer()
    monkeypatch.setattr(comp, "calibrate_gain", lambda *a, **k: 0.0)
    still = _fx.still("richat_structure_sahara_eye_01_commons_960.jpg")   # a real Richat photo, 960 px (committed)
    audio = tmp_path / "vo.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2",
                    str(audio)], check=True, timeout=60)
    words = [{"word": "Sand.", "start": 0.2, "dur": 0.4}]
    with pytest.raises(Stop):
        comp.compose_video([still], audio, audio, words, 2.0, tmp_path / "m.mp4",
                           delivery_profile=delivery_profiles.get_delivery_profile("deep_dive"))
    graph = captured[0][captured[0].index("-filter_complex") + 1]
    assert vc.picture_vignette(1920, 1080) in graph and "aspect=16/9" not in graph


def _vignette_luma(width: int, height: int, chain: str):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x808080:s={width}x{height}:r=30",
                          "-vf", f"format=yuv420p,{chain}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
                          "-"], capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width).astype(float)


def _corner_ratios(a, n: int) -> float:
    h, w = a.shape
    centre = a[h // 2 - 32:h // 2 + 32, w // 2 - 32:w // 2 + 32].mean()
    return min(a[y:y + n, x:x + n].mean() for y in (0, h - n) for x in (0, w - n)) / centre


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
def test_16x9_vignette_corners_are_shaped_not_black():
    a = _vignette_luma(1920, 1080, vc.picture_vignette(1920, 1080))
    centre = a[540 - 32:540 + 32, 960 - 32:960 + 32].mean()
    assert _corner_ratios(a, 64) >= 0.55 and _corner_ratios(a, 32) >= 0.53
    assert a.min() >= 0.45 * centre
    old = _vignette_luma(1920, 1080, "vignette=PI/4.2:aspect=16/9")   # the chain before V1
    assert old.min() == 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")
def test_9x16_vignette_is_unchanged():
    new = _vignette_luma(1080, 1920, vc.picture_vignette(1080, 1920))
    old = _vignette_luma(1080, 1920, "vignette=PI/4.2:aspect=9/16")
    assert abs(new - old).max() <= 1
    assert abs(_corner_ratios(new, 64) - 0.535) <= 0.02


def test_16x9_evidence_corners_are_not_black():
    """V1 (d), as measured: the re-rendered 16:9 evidence (real Richat run 1792740961_long) keeps a quarter or more
    of each corner's own mean luma (with the vignette over the same frame without it; the chain before V1 took the
    corners to 0). The literal criterion, a corner minimum above 0, cannot hold on these photographs: several hold
    pure-black pixels of their own before any vignette (02_commons.jpg's top-left corner, a night sky)."""
    summary = json.loads((ROOT / "docs/reviews/freeze-panel-2026-11-04/captions/after/summary.json").read_text("utf-8"))
    samples = [s for s in summary["cases"]["richat_structure_sahara_eye_16x9"]["samples"] if s.get("corner_mean_ratio")]
    ratios = [s["corner_mean_ratio"] for s in samples]
    assert samples and all(min(r) >= 0.25 for r in ratios), ratios
    assert summary["cases"]["richat_structure_sahara_eye_16x9"]["vignette_aspect"] == vc.picture_vignette(1920, 1080)


# ---------------------------------------------------------------- N1: long names
LADDER = {FONT_SIZE, CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE}


def test_every_multi_word_reel_burst_is_on_the_ladder():
    for label, words, phrases, wide in _all_layouts():
        if wide:
            continue
        for p in phrases:
            if len(p) > 1:
                lines = vc.reel_caption_lines(p)
                size = lines[1] if lines else vc._caption_fit_size(p)
                assert size in LADDER, (label, _upper(p), size)


def test_no_multi_word_captext_below_the_bond_floor_in_the_real_reels(tmp_path):
    floor = round(CAPTION_BOND_MIN_SIZE / 0.5625)
    for topic, c in RUNS.items():
        if RUN_WIDE[topic]:
            continue
        ass = VideoComposer().build_subtitles(_fx.words(c), tmp_path / f"{topic}.ass",
                                              emphasis=list(c.get("emphasis_words") or []))
        for line in ass.read_text("utf-8").splitlines():
            if ",CapText," in line:
                fs = re.search(r"\\fs(\d+)", line)
                shown = re.sub(r"\{[^}]*\}", "", line.split(",,", 1)[1])
                if fs and len(shown.split()) > 1:
                    assert int(fs.group(1)) >= floor, (topic, line[-120:])


def test_under_the_rashtrakuta_dynasty_breaks_before_the_article():
    words = _even("Under the Rashtrakuta dynasty", step=0.3, dur=0.28)
    phrases = caption_phrases(words)
    assert _texts(phrases) == ["UNDER", "THE RASHTRAKUTA DYNASTY"]
    assert vc._caption_fit_size(phrases[1]) == CAPTION_BOND_MIN_SIZE and vc.reel_caption_lines(phrases[1]) is None
    assert round(vc._caption_ink_width(vc._phrase_words(phrases[1]), CAPTION_BOND_MIN_SIZE)) <= 640


def test_tunguska_long_name():
    """Real run 1792930600. Fix round 1 (operator decisions 2026-11-04): PODKAMENNAYA TUNGUSKA RIVER is no name bond
    (HEAD 0036b18's name_bonds: THE PODKAMENNAYA TUNGUSKA RIVER is wider than one burst at 66 px), a two-line plate
    would rise over the graphics box, and its key-term bond (702 px at 66) cannot be held, so the bursts are HEAD's
    NEAR THE PODKAMENNAYA | TUNGUSKA RIVER: no burst is THE alone or ends on THE. Was, after the merge: NEAR | THE |
    PODKAMENNAYA TUNGUSKA | RIVER IN CENTRAL SIBERIA, (and the round-2 two-line name before it)."""
    words, phrases = run_layout("tunguska_event_1908")
    texts = _texts(phrases)
    assert "THE" not in texts and all(not t.endswith(" THE") for t in texts)
    k = texts.index("NEAR THE PODKAMENNAYA")
    assert texts[k - 1:k + 3] == ["LOCAL TIME", "NEAR THE PODKAMENNAYA", "TUNGUSKA RIVER", "IN CENTRAL SIBERIA"], texts
    assert all(vc.reel_caption_lines(p) is None for p in phrases)
    assert all(vc._caption_fit_size(p) in LADDER for p in phrases if len(p) > 1)


def _reel_plates(ass_text: str) -> list[tuple[int, int, int, int]]:
    """(left, top, right, bottom) of every 9:16 CapBox plate in an .ass file."""
    out = []
    for line in ass_text.splitlines():
        if ",CapBox," in line:
            x, y = map(int, re.search(r"\\pos\((-?\d+),(-?\d+)\)", line).groups())
            nums = [int(v) for v in re.findall(r"-?\d+", line.split("\\p1}")[1].split("{")[0])]
            out.append((x, y, x + max(nums[0::2]), y + max(nums[1::2])))
    return out


def test_two_line_plates_never_rise_over_the_graphics(tmp_path):
    """Fix round 1 (operator decision 2026-11-04: a two-line plate never rises above y 1058, the graphics box's
    bottom; cap the rise or split instead). A two-line plate is REEL_PLATE_H plus one baseline pitch above the one-line
    plate's bottom (y 1228): at 66 px its top is y 1032, so no rung clears the box and a burst that would need two
    lines is split. Every plate of the four real Reels stays inside the Reel box and below the graphics box. Was
    test_two_line_burst_geometry (the two-line tunguska plate, top y 1015)."""
    assert vc.REEL_PLATE_H == 121 and round(config.CAPTION_TWO_LINE_PITCH_EM * vc.reel_cap_height(66)) == 75
    # QF-24 (decision QF-24/QF-87 layout, 2026-11-09, deliberate change): the 240 px band (top y 1130, 40 px under
    # the graphics' foot at 1090) holds a two-line plate at every rung (none did under the ads profile, so this
    # tunguska burst had no two-line layout); every plate still stays in the band, under the graphics
    assert all(vc._two_line_clears_graphics(r) for r in vc.CAPTION_LADDER)
    assert vc._two_line_layout("near the Podkamennaya Tunguska River".split()) == (3, 74)
    seen = 0
    for topic, c in RUNS.items():
        if RUN_WIDE[topic]:
            continue
        ass = VideoComposer().build_subtitles(_fx.words(c), tmp_path / f"{topic}.ass",
                                              emphasis=list(c.get("emphasis_words") or []))
        for left, top, right, bottom in _reel_plates(ass.read_text("utf-8")):
            seen += 1
            # QF-24: the organic Reel box (113, 288)-(848, 1370) and its band from y 1130 (was 65-888, foot 1248)
            assert top >= 1130 > vc.CAPTION_GUARD_GRAPHICS_BOTTOM and bottom <= 1370 and left >= 113 \
                and right <= 848, (topic, top, bottom)
            assert bottom - top == vc.REEL_PLATE_H, (topic, top, bottom)
    assert seen > 100


@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("text,name", [
    ("Pilgrims crowd the Basilica of Santa Maria della Salute every November.", "BASILICA OF SANTA MARIA DELLA SALUTE"),
    ("Inside the Mausoleum of the First Qin Emperor, mercury rivers may still flow.",
     "MAUSOLEUM OF THE FIRST QIN EMPEROR"),
])
def test_adversarial_long_names_stay_whole(text, name, wide):
    """Synthetic adversarial texts (labelled as such). Fix round 1 (merge note 2026-11-04: HEAD 0036b18's name_bonds):
    both names run past NAME_MAX_WORDS, so they are no name bond and keep HEAD's phrasing (was: the name in one burst,
    MERCURY never with it). No burst ends on OF or THE, and none is under 66 px."""
    words = _even(text, step=0.3, dur=0.28)
    phrases = caption_phrases(words, None, wide)
    assert [w for p in phrases for w in p] == words
    assert len(name.split()) > vc.NAME_MAX_WORDS
    texts = [_plain(t) for t in _texts(phrases)]
    assert not any(t.split()[-1] in ("OF", "THE") for t in texts[:-1]), texts
    assert set(_ends(phrases)[:-1]) == _head_cuts(words, None, wide), texts   # gate: HEAD's phrasing
    if not wide:
        for p in phrases:
            if len(p) > 1:
                assert vc._caption_fit_size(p) >= CAPTION_BOND_MIN_SIZE, _upper(p)


def test_bursts_keep_every_word_in_order():
    for label, words, phrases, wide in _all_layouts():
        assert [w for p in phrases for w in p] == words, (label, wide)


# ---------------------------------------------------------------- N2: name runs past one burst
def test_richat_16x9_bursts_are_never_shrunk(tmp_path):
    """Was test_richat_international_union_is_one_16x9_burst. Fix round 1 (merge note 2026-11-04: HEAD 0036b18's
    name_bonds; the N2 name runs were dropped): THE INTERNATIONAL UNION OF GEOLOGICAL SCIENCES is no name bond (six
    words), so it keeps HEAD's lines. Every 16:9 burst draws at wide_fs, unshrunk, on a plate of 1500 px or less."""
    words, phrases = run_layout("richat_structure_sahara_eye")
    raw = "the International Union of Geological Sciences".split()
    assert not name_bonds(raw)
    c = RUNS["richat_structure_sahara_eye"]
    ass = VideoComposer().build_subtitles(words, tmp_path / "r.ass", width=1920, height=1080)
    for line in ass.read_text("utf-8").splitlines():
        if ",CapText," in line:
            assert "\\fs" not in line, line[-100:]       # never shrunk: every 16:9 burst at wide_fs
        if ",CapBox," in line:
            path = line.split("\\p1}")[1].split("{")[0]
            assert max(int(x) for x in re.findall(r"-?\d+", path)[0::2]) <= config.CAPTION_WIDE_MAX_PLATE
    assert c["format"] == "16:9"


def test_a_long_name_run_is_no_bond_and_no_burst_ends_on_a_connector():
    """Was test_a_name_past_eight_words_splits_once_before_a_connector (N2, dropped by the merge note 2026-11-04):
    HEAD 0036b18's name_bonds bonds a run of NAME_MAX_WORDS or fewer only (THE GRAND HALL); no burst ends on a
    connector or THE in either format."""
    text = "Delegates met at the Grand Hall of the Union of Soviet Socialist Republics of Asia yesterday."
    raw = text.split()
    assert {(raw[i], raw[i + 1]) for i in name_bonds(raw)} == {("the", "Grand"), ("Grand", "Hall")}
    for wide in (False, True):
        texts = [_plain(t) for t in _texts(caption_phrases(_even(text, step=0.3, dur=0.28), None, wide))]
        assert any("GRAND HALL" in t for t in texts), texts
        assert not any(t.split()[-1].lower() in vc.NAME_PARTICLES | {"the"} for t in texts[:-1]), texts


def test_sidecar_keeps_each_name_bond_in_one_cue():
    """Was test_sidecar_keeps_a_long_name_in_one_cue_breaking_before_of (N2, dropped by the merge note): no cue ends
    inside a name_bonds name, on the five real runs and the test narrations."""
    for case, words in _cue_cases():
        raw = [w["word"] for w in words]
        named = name_bonds(raw)
        ends = set(_ends([t.split() for _, _, t in partition_sidecar_cues(words)])[:-1])
        assert not ends & named, (case, [(raw[k], raw[k + 1]) for k in sorted(ends & named)])


# ---------------------------------------------------------------- N3: number words in names
def test_world_war_one_and_two_stay_whole():
    words, phrases = run_layout("voynich_manuscript")
    texts = _texts(phrases)
    for name in ("WORLD WAR ONE", "WORLD WAR TWO"):
        assert any(name in t for t in texts), texts
    assert not any(t.endswith("WORLD WAR") for t in texts)
    k = texts.index("WORLD WAR ONE")
    assert texts[k - 1:k + 2] == ["FROM BOTH", "WORLD WAR ONE", "AND WORLD WAR TWO"], texts[k - 2:k + 3]
    assert round(vc._caption_ink_width(vc._phrase_words(phrases[k + 1]), FONT_SIZE)) <= 640
    for p in phrases:
        if len(p) > 1:
            assert vc._caption_fit_size(p) >= CAPTION_BOND_MIN_SIZE


def test_name_number_tails():
    def pairs(text):
        raw = text.split()
        return {(raw[i], raw[i + 1]) for i in name_bonds(raw)}
    # fix round 1 (merge note: HEAD 0036b18's name_bonds): a lone name before I is no run (CARTER I BELIEVE), and a
    # numbered name takes its number (APOLLO 11: NAME_NUMBERED); was ("Krishna", "I") bonded and APOLLO 11 no bond
    assert pairs("ruled by Krishna I of old") == set()
    assert pairs("in World War One today") == {("World", "War"), ("War", "One")}
    assert ("Louis", "XIV") in pairs("under King Louis XIV the court")
    assert pairs("In 1941 Leningrad fell") == set()
    assert pairs("the Apollo 11 crew") == {("the", "Apollo"), ("Apollo", "11")}
    assert pairs("named for Voynich. I think") == set()


def test_review_sentence_voynich():
    """N3 (c): the real Voynich sentence among the review sentences: the two names whole, every word kept."""
    words = timed(REVIEW_SENTENCES[3])
    for wide in (False, True):
        texts = _texts(caption_phrases(words, None, wide))
        assert any("WORLD WAR ONE" in t for t in texts) and any("WORLD WAR TWO" in t for t in texts), texts


# ---------------------------------------------------------------- A1: no burst ends on an article
# The one exception class: an article right before a hedge and figure group that cannot hold it at the bond floor.
A1_EXCEPTIONS = {"THEY REMOVED AN"}


def test_no_burst_ends_on_an_article():
    seen = set()
    for label, words, phrases, wide in _all_layouts():
        for k, p in enumerate(phrases[:-1]):
            if vc._bare(p[-1]["word"]) not in ("the", "a", "an"):
                continue
            text = _upper(p)
            assert text in A1_EXCEPTIONS, (label, wide, text)
            seen.add(text)
            nxt = [w["word"] for w in phrases[k + 1]]
            assert any(c.isdigit() for c in " ".join(nxt)) and not vc._fits_caption_bonded([p[-1]["word"]] + nxt)
    assert seen == A1_EXCEPTIONS


def test_the_c8_article_endings_re_split():
    odessa = _texts(layout("odessa_catacombs", False)[1])
    assert "PARTISANS USED" in odessa
    wiel = _texts(layout("wieliczka_salt_mine", False)[1])
    assert "TODAY, THE" not in wiel and "INTRODUCED IN THE" not in wiel


# ---------------------------------------------------------------- P1: pause breaks
def test_no_real_burst_holds_a_pause_after_punctuation():
    """Fix round 1 (gate): a burst holds a pause after punctuation only where HEAD 0036b18 keeps the pair too (was:
    never)."""
    for topic in RUNS:
        words, phrases = run_layout(topic)
        head = _head_cuts(words, list(RUNS[topic].get("emphasis_words") or []), RUN_WIDE[topic])
        at = -1
        for p in phrases:
            start, at = at + 1, at + len(p)
            for j in range(len(p) - 1):
                assert not (vc._pause_break(p, j) and start + j in head), (topic, _upper(p))


def test_pause_breaks_exact():
    tung = [" ".join(w["word"] for w in p) for p in run_layout("tunguska_event_1908")[1]]
    assert "local time," in tung
    voy = [" ".join(w["word"] for w in p) for p in run_layout("voynich_manuscript")[1]]
    # fix round 1 (gate): HEAD 0036b18's PLANTS, THE TEXT and 16:9 TIMELINE: DATING ... (was: a burst ends on
    # PLANTS, and on TIMELINE:)
    assert "plants, the text" in voy, voy
    richat = [" ".join(w["word"] for w in p) for p in run_layout("richat_structure_sahara_eye")[1]]
    assert any(t.endswith("site;") for t in richat) and any(t.startswith("timeline: dating") for t in richat)


def test_few_16x9_bursts_span_a_pause():
    words, phrases = run_layout("richat_structure_sahara_eye")
    head = _head_cuts(words, list(RUNS["richat_structure_sahara_eye"].get("emphasis_words") or []), True)
    spanning, at = 0, -1
    for p in phrases:
        start, at = at + 1, at + len(p)
        spanning += any(vc._gap_after(p, j) >= 0.30 and start + j in head for j in range(len(p) - 1))
    # fix round 1 (gate): no 16:9 burst spans a pause HEAD 0036b18 breaks at (was: at most 2 span any pause)
    assert spanning == 0, spanning


# ---------------------------------------------------------------- U1: units and hedge phrases
def test_units_and_hedges_on_the_real_runs():
    tung = _texts(run_layout("tunguska_event_1908")[1])
    # fix round 2 (review blocker 1, operator decision 2026-11-04 as written: a figure link forces no cut HEAD 0036b18
    # lacks): tunguska keeps HEAD's MORE THAN 830 SQUARE | MILES OF TAIGA FOREST and BETWEEN 10 AND 15 | MEGATONS OF
    # TNT (was: no burst ends on SQUARE or 10 AND 15, which cost BETWEEN 10 | AND 15 MEGATONS | OF TNT)
    assert "MORE THAN 830 SQUARE" in tung and "MILES OF TAIGA FOREST" in tung, tung
    assert "BETWEEN 10 AND 15" in tung and "MEGATONS OF TNT" in tung, tung
    assert not any(t.endswith("BETWEEN 10") for t in tung), tung
    amber = _texts(run_layout("amber_room_heist")[1])
    # fix round 1 (gate): a fixed hedge phrase is no figure link, so amber keeps HEAD 0036b18's BY ONE | ESTIMATE,
    # FOLLOWING and THE ROOM WAS MOST | LIKELY INCINERATED (was: no burst ends on BY ONE or opens on LIKELY)
    # fix round 2 (as above): amber keeps HEAD's COVERED MORE THAN 590 | SQUARE FEET, no lone COVERED (was: no burst
    # ends on MORE THAN 590 or opens on SQUARE FEET)
    assert "COVERED MORE THAN 590" in amber and "SQUARE FEET" in amber and "COVERED" not in amber, amber
    for topic in RUNS:
        words, phrases = run_layout(topic)
        head = _head_cuts(words, list(RUNS[topic].get("emphasis_words") or []), RUN_WIDE[topic])
        raw = [w["word"] for w in words]
        for k in range(len(raw) - 2):
            if [vc._norm(x) for x in raw[k:k + 3]] == ["by", "one", "estimate"] and not {k, k + 1} & head:
                assert not {k, k + 1} & set(_ends(phrases)), (topic, k)
        for p in run_layout(topic)[1]:
            if not RUN_WIDE[topic] and len(p) > 1:
                lines = vc.reel_caption_lines(p)
                assert (lines[1] if lines else vc._caption_fit_size(p)) >= CAPTION_BOND_MIN_SIZE, (topic, _upper(p))


def test_square_units_and_hedge_groups_are_bonds():
    assert vc._is_atomic_boundary("square", "miles") and vc._is_atomic_boundary("cubic", "feet")
    raw = "covered more than 590 square feet and".split()
    assert {1, 2, 3} <= caption_bonds(raw) and vc._is_atomic_boundary(raw[4], raw[5])   # 590-SQUARE a bond
    raw = "By one estimate, the room was most likely burned".split()
    bonds = caption_bonds(raw)
    assert {0, 1} <= bonds and 6 in bonds and 2 not in bonds
    assert "megatons" in vc.MEASURE_UNITS and "kilotons" in vc.MEASURE_UNITS


# ---------------------------------------------------------------- fix round 1: the HEAD-regression gate (M1)
# Operator decision 2026-11-04: every adjacent pair HEAD 0036b18 keeps in one 9:16 burst, one 16:9 burst or one sidecar
# cue stays together, unless a name bond or a hard width or floor limit forces the cut. Replaces the round-3/4 gate
# against HEAD 5fdb5dc (GATE_ALLOWED, REVIEW_SPLITS), which read only the _dangles ends. The fixture was written from
# the HEAD 0036b18 worktree (regression/make_head_0036b18_cuts.py), under raqm (operator decision 2026-11-05).
HEAD_CUTS_FILE = ROOT / "docs/reviews/freeze-panel-2026-11-04/captions/regression/head_0036b18_cuts.json"
# Every cut the tree makes that HEAD 0036b18 does not, (case, format, left word, right word): (kind, reason). Kinds:
# "width" (HEAD's burst draws more than 640 px of ink at 74 px and is no bond, or passes the 28 characters: measured
# below), "article" (HEAD's burst or cue ends on THE, A or AN: the operator's rule). Fix round 2 (review blocker 1, the
# decision as written): the "figure" kind is withdrawn. A figure link HEAD cut no longer forces a cut (its 22 entries
# left: amber COVERED | MORE, tunguska 830 | SQUARE, 10 | AND, MEGATONS | OF, the 16:9 DEVASTATED | MORE, the colossi
# and aswan conversions, wieliczka 287 | AND and the cue entries); HEAD's own cut stands there.
# Pinned to raqm (operator decision 2026-11-05; head_0036b18_cuts.json regenerated under raqm, which changes HEAD's
# 9:16 cuts in two cases: edge gains INCOMPREHENSIBILITIES | REMAIN. (index 18); singing_colossi_of_memnon's one cut
# REPORTED | CRY (38) becomes EMITTING | A (36) and CRY | AT (39)). So edge's INCOMPREHENSIBILITIES | REMAIN. is
# HEAD's own cut, no longer a forced one, and its two lone words are HEAD's. Richat's HERITAGE | SITES. went under
# raqm's kerned measure (GEOLOGICAL HERITAGE SITES. measured 639.9 px at 74) and is back with the unkerned measure
# (operator decision 2026-11-05, "measure without kerning"): 640.6 px at 74, and libass draws 642 px of ink there, so
# the burst passes the 640 px ink budget (measured in test_head_regression_gate). HEAD never cut there.
GATE_FORCED = {
    ("names", "9:16", "pushed", "toward"): ("width", "PUSHED TOWARD LENINGRAD. is 653 px at 74"),
    ("edge", "9:16", "years", "ago,"): ("width", "ROUGHLY 1,200 YEARS AGO, is 646 px at 74; AGO is no bond"),
    ("wieliczka_salt_mine", "9:16", "underground", "galleries"): ("width", "OF UNDERGROUND GALLERIES is 654 px at 74"),
    ("run:richat_structure_sahara_eye", "9:16", "dating", "of"): ("width", "DATING OF THE CARBONATITE is 649 px at 74"),
    ("run:richat_structure_sahara_eye", "9:16", "thousands", "of"):
        ("width", "THOUSANDS OF PREHISTORIC is 646 px at 74"),
    ("run:richat_structure_sahara_eye", "9:16", "recognized", "this"):
        ("width", "RECOGNIZED THIS SCIENTIFIC is 645 px at 74"),
    # operator decision 2026-11-05 (measure without kerning): libass draws 642 px of ink, the kerned measure said 639.9
    ("run:richat_structure_sahara_eye", "9:16", "heritage", "sites."):
        ("width", "GEOLOGICAL HERITAGE SITES. is 640.6 px at 74 unkerned (libass draws 642)"),
    ("run:gol_gumbaz_whispering_gallery", "9:16", "across", "an"): ("article", "HEAD's TRAVEL ACROSS AN"),
    ("run:voynich_manuscript", "9:16", "script", "where"): ("width", "SCRIPT WHERE AN ALPHABET is 653 px at 74"),
    ("edge", "cues", "ago,", "between"): ("article", "HEAD's cue ended on BELOW THE (the next opened on SEA.)"),
    # fix round 3 (review: a new cue end goes before a determiner, not mid-phrase): one word back from HEAD's THE
    ("odessa_catacombs", "cues", "used", "the"): ("article", "HEAD's cue ended on PARTISANS USED THE"),
    ("run:richat_structure_sahara_eye", "cues", "Plateau", "within"): ("article", "HEAD's cue ended on WITHIN THE"),
    # fix round 3 (review blocker: THIS CIRCULAR || GEOLOGICAL FORMATION split the noun phrase; was circular|geological
    # and structure|as): the forced end goes before the determiner, and the next cue ends before THE STRUCTURE
    ("run:richat_structure_sahara_eye", "cues", "formed", "this"):
        ("article", "HEAD's cue ended on FORMATION IN THE; FORMATION || IN is in the S1 tail, so the cut goes before THIS"),
    ("run:richat_structure_sahara_eye", "cues", "identify", "the"): ("article", "HEAD's cue ended on WHEN A"),
    ("run:richat_structure_sahara_eye", "cues", "intrusion", "deformed"):
        ("article", "the cue after WHEN A: its end moves with it (SIDECAR_MAX_CUE)"),
}
# One-word 9:16 bursts HEAD 0036b18 does not form, each where a GATE_FORCED cut leaves the word alone: the other
# split ends on a function word (a hard defect, as at HEAD) or passes the width.
LONE_FORCED = {
    ("names", "pushed"): "width: PUSHED TOWARD is a dangling end; PUSHED TOWARD LENINGRAD. 653 px at 74",
    ("run:richat_structure_sahara_eye", "dating"): "width: DATING OF | THE CARBONATITE ends on OF",
    ("run:richat_structure_sahara_eye", "thousands"): "width: THOUSANDS OF | PREHISTORIC ends on OF",
    ("run:richat_structure_sahara_eye", "recognized"): "width: RECOGNIZED THIS | SCIENTIFIC ends on THIS",
    # fix round 2 (the decision as written): amber COVERED and colossi INCLUDING were figure-forced and are HEAD's
    # bursts again; wieliczka GALLERIES takes HEAD's lone KILOMETERS. back with it
    ("wieliczka_salt_mine", "galleries"):
        "width: OF UNDERGROUND GALLERIES 654 px at 74; OF UNDERGROUND | GALLERIES BETWEEN ... passes the width too",
    # operator decision 2026-11-05 (measure without kerning): HEAD's OFFICIAL | GEOLOGICAL HERITAGE SITES. stands, and
    # its last burst passes the width, so one word of it goes alone (GEOLOGICAL | HERITAGE SITES. would leave GEOLOGICAL)
    ("run:richat_structure_sahara_eye", "sites."):
        "width: GEOLOGICAL HERITAGE SITES. 640.6 px at 74 (libass draws 642); either cut leaves one word alone",
}


def _gate_cases():
    """(case, words, emphasis, HEAD cuts) for every case of head_0036b18_cuts.json."""
    head = json.loads(HEAD_CUTS_FILE.read_text("utf-8"))
    for case, h in head.items():
        if case.startswith("run:"):
            c = RUNS[case[4:]]
            words, emph = _fx.words(c), list(c.get("emphasis_words") or [])
        else:
            words, emph = timed(NARRATIONS[case][0]), NARRATIONS[case][1]
        assert [w["word"] for w in words] == h["words"], case
        yield case, words, emph, h


def test_round4_base_is_head_0036b18():
    """round4_burst_cuts and round4_cue_ends, the gate's base, reproduce HEAD 0036b18's bursts and cues exactly
    (pinned to raqm, operator decision 2026-11-05: head_0036b18_cuts.json is HEAD 0036b18's own phrasing under raqm)."""
    require_raqm()
    for case, words, emph, h in _gate_cases():
        assert sorted(vc.round4_burst_cuts(words, emph)) == h["9:16"], case
        assert sorted(vc.round4_burst_cuts(words, emph, True, 1440)) == h["16:9"], case
        assert sorted(vc.round4_cue_ends(words)) == h["cues"], case


def test_head_regression_gate():
    require_raqm()   # the HEAD cuts are raqm's (operator decision 2026-11-05)
    budget = vc._ink_budget()
    new = {}
    for case, words, emph, h in _gate_cases():
        raw = [w["word"] for w in words]
        now = {"9:16": _ends(caption_phrases(words, emph))[:-1], "16:9": _ends(caption_phrases(words, emph, True))[:-1],
               "cues": _ends([t.split() for _, _, t in partition_sidecar_cues(words)])[:-1]}
        for fmt, cuts in now.items():
            spans, a = [], 0
            for e in h[fmt] + [len(raw) - 1]:
                spans.append((a, e))
                a = e + 1
            for k in sorted(set(cuts) - set(h[fmt])):
                key = (case, fmt, raw[k], raw[k + 1])
                new[key] = k
                kind = GATE_FORCED.get(key, ("?",))[0]
                if kind == "width":   # measured: HEAD's burst passes the ink budget at 74 px or the 28 characters
                    a, e = next((a, e) for a, e in spans if a <= k < e)
                    shown = vc._phrase_words(raw[a:e + 1])
                    assert (vc._caption_ink_width(shown, CAPTION_BURST_MIN_SIZE) > budget
                            or len(" ".join(shown)) > MAX_BURST_CHARS), (key, shown)
    assert set(new) == set(GATE_FORCED), sorted(set(new) ^ set(GATE_FORCED))
    # kept from review round 4: AS THE SECOND | MOST COMMON. and HIDING PLACE are HEAD's and stay
    voy = _texts(run_layout("voynich_manuscript")[1])
    assert "AS THE SECOND" in voy and "MOST COMMON" in voy, voy
    odessa = _texts(layout("odessa_catacombs", False)[1])
    assert "HIDING PLACE" in odessa and not any(t.endswith(" HIDING") for t in odessa), odessa


def test_lone_word_bursts_are_head_or_forced():
    """Was test_lone_word_bursts_are_each_forced (against HEAD 5fdb5dc). Every one-word 9:16 burst is one HEAD 0036b18
    forms, or listed in LONE_FORCED with the cut that forces it (operator decision 2026-11-04)."""
    require_raqm()   # the HEAD cuts are raqm's (operator decision 2026-11-05)
    found = set()
    for case, words, emph, h in _gate_cases():
        raw = [w["word"] for w in words]
        spans, a = [], 0
        for e in h["9:16"] + [len(raw) - 1]:
            spans.append((a, e))
            a = e + 1
        head_lone = {a for a, e in spans if a == e}
        at = -1
        for p in caption_phrases(words, emph):
            at += len(p)
            if len(p) == 1 and at not in head_lone:
                found.add((case, p[0]["word"]))
    assert found == set(LONE_FORCED), sorted(found ^ set(LONE_FORCED))


def _mid_dangles(phrases) -> list[str]:
    return [_upper(p) for p in phrases[:-1]
            if not re.search(r"[.?!]$", p[-1]["word"]) and vc._dangles(p[-1]["word"])]


def test_fewer_dangling_ends_on_the_real_runs():
    """M1 (b): on the five real runs the bursts ending mid-sentence on a _dangles word are at most half of the round-1
    evidence's 29 (current/summary.json, measured with the same _dangles), and none of the panel's cases is left but
    OF A PROMINENT (forced: A PROMINENT WHISPERING GALLERY is 30 characters, PROMINENT WHISPERING GALLERY needs 66 px,
    and every other split of the clause ends on OF, A or the article)."""
    # fix round 1 (gate): no real run has more such ends than HEAD 0036b18's phrasing of it (was: at most 14 in all,
    # and none of INSIDE THE DARK, WHEN ALLIED, IS NAMED AFTER POLISH, THE CONCENTRIC, AS A VISUAL, DOCUMENT HUMAN,
    # which are HEAD's own ends)
    ends = []
    for topic in RUNS:
        words, phrases = run_layout(topic)
        found = _mid_dangles(phrases)
        head, spans, a = sorted(_head_cuts(words, list(RUNS[topic].get("emphasis_words") or []), RUN_WIDE[topic])), [], 0
        for e in head + [len(words) - 1]:
            spans.append(words[a:e + 1])
            a = e + 1
        assert len(found) <= len(_mid_dangles(spans)), (topic, found, _mid_dangles(spans))
        ends += found
    # OPEN (review round 3): OF A PROMINENT still ends a burst (gol): A PROMINENT WHISPERING GALLERY is 30 characters
    # and PROMINENT WHISPERING GALLERY needs 66 px, so every split ends on OF, A, PROMINENT or the attributive
    # WHISPERING; M1 (b) asks for 0, so this needs the operator (accept, or narrow M1). Not pinned: a fix may remove it.
    assert len([e for e in ends if e.endswith("OF A PROMINENT")]) <= 1


def test_attributive_ing_end():
    """M1 (review round 3): an -ing word after a PRE_MODIFIERS adjective and before a lower-case content word is an
    attributive end (AN ACTIVE HIDING | PLACE); a noun after an article, or punctuation, is not."""
    assert vc._attributive_end("as an active hiding place".split(), 3)
    assert vc._attributive_end("with aerial bombing raids,".split(), 2)
    assert not vc._attributive_end("the building collapsed".split(), 1)
    assert not vc._attributive_end("an active hiding, place".split(), 2)
    assert not vc._attributive_end("an active hiding and".split(), 2)
    assert not vc._attributive_end("a prominent Whispering Gallery".split(), 2)


def test_m1_pre_modifiers_and_proper_modifiers():
    for word in ("native", "dark", "prominent", "allied", "concentric", "visual", "human", "first"):
        assert vc._dangles(word) and word in vc.PRE_MODIFIERS
    assert not vc._dangles("across,")   # punctuation closes a word (40 KILOMETRES ACROSS, | THOUGH ...)
    voy = _texts(run_layout("voynich_manuscript")[1])
    # fix round 1 (gate): IS NAMED AFTER POLISH | BOOK DEALER and WAS BOMBED BY BRITISH | AIRCRAFT are HEAD 0036b18's
    # (was: no burst ends on POLISH or BRITISH)
    assert "IS NAMED AFTER POLISH" in voy, voy
    # soft (M1): AMERICAN AND BRITISH | MILITARY CRYPTOGRAPHERS stays, every split avoiding it leaves a lone word
    assert "AMERICAN AND BRITISH" in voy


# ---------------------------------------------------------------- D1: half a second on screen
# Bursts the speech leaves under CAPTION_MIN_ON_SCREEN_S: each is held to the next burst's appearance (no silence to
# extend into) and no same-clause neighbour can take it (the reason given).
# Fix round 1 (gate): RUNS, THOUGH NO and AND SHELLED are HEAD 0036b18's longer bursts again and left the list.
SPEECH_LIMITED = {
    ("names", "PUSHED"): "PUSHED TOWARD LENINGRAD. is 653 px at 74 (over 640, no bond); ARMY GROUP NORTH is a whole name",
    ("amber_room_heist", "BY ONE"): "gate: HEAD 0036b18's BY ONE | ESTIMATE, FOLLOWING (a hedge phrase is no figure "
                                    "link); BY ONE ESTIMATE, FOLLOWING is 5 words",
    # fix round 2 (the decision as written): amber COVERED is HEAD 0036b18's COVERED MORE THAN 590 | SQUARE FEET again
}


def _short_bursts():
    out = {}
    for nid in NARRATIONS:
        for wide in (False, True):
            words, phrases = layout(nid, wide)
            tim = vc.burst_timings(phrases, first_frame_whole=None if wide else config.CAPTION_FIRST_FRAME_WHOLE_S,
                                   dwell=not wide)
            for k, (p, (s, h, _)) in enumerate(zip(phrases, tim)):
                if h - s < config.CAPTION_MIN_ON_SCREEN_S - 1e-6:
                    out[(nid, _upper(p))] = (k, tim)
    for topic in RUNS:
        phrases, tim = run_timings(topic)
        for k, (p, (s, h, _)) in enumerate(zip(phrases, tim)):
            if h - s < config.CAPTION_MIN_ON_SCREEN_S - 1e-6:
                out[(topic, _upper(p))] = (k, tim)
    return out


def test_every_burst_is_up_half_a_second_or_speech_limited():
    short = _short_bursts()
    assert set(short) == set(SPEECH_LIMITED), sorted(set(short) ^ set(SPEECH_LIMITED))
    for (_, text), (k, tim) in short.items():
        assert k + 1 < len(tim) and abs(tim[k][1] - tim[k + 1][0]) < 1e-6, text   # held to the next burst


def test_gol_merges_and_digit_bursts():
    phrases, tim = run_timings("gol_gumbaz_whispering_gallery")
    for p, (s, h, _) in zip(phrases, tim):
        assert not (_upper(p) in ("WITH LIME", "ARE GUIDED") and h - s < 0.5), _upper(p)
    for topic in RUNS:
        if RUN_WIDE[topic]:
            continue
        phrases, tim = run_timings(topic)
        for p, (s, h, _) in zip(phrases, tim):
            if any(c.isdigit() for c in _upper(p)):
                assert h - s >= CAPTION_DIGIT_MIN_S - 1e-6, (topic, _upper(p), h - s)


# ---------------------------------------------------------------- G1: a caption plate over a graphic's text
def _scene_clips(case):
    """(clips, durations) from a run's scene timeline, starts estimated as fixtures.background_at estimates them."""
    tl, dur = case["timeline"], float(case["duration_s"])
    known = [s for s in tl if s.get("start_s_est") is not None]
    rest = tl[len(known):]
    starts = [float(s["start_s_est"]) for s in known]
    if rest:
        t0 = starts[-1] + float(known[-1].get("duration_s") or 0)
        starts += [t0 + i * max(dur - t0, 0) / len(rest) for i in range(len(rest))]
    clips = [_fx.run_path(s["clip"]) for s in tl]   # graphic posters are committed copies (fixtures/runs)
    return clips, [b - a for a, b in zip(starts, starts[1:])] + [dur - starts[-1]]


def test_amber_bursts_over_the_timeline_graphic_get_an_opaque_plate(tmp_path):
    c = RUNS["amber_room_heist"]
    clips, durations = _scene_clips(c)
    gclips = vc.caption_graphic_clips(clips, durations)
    comp = VideoComposer()
    ass = comp.build_subtitles(_fx.words(c), tmp_path / "a.ass", emphasis=list(c["emphasis_words"]),
                               graphic_intervals=vc.caption_graphic_intervals(clips, durations), graphic_clips=gclips)
    flagged = comp.last_caption_graphic_overlap
    # QF-24 (decision QF-24/QF-87 layout, 2026-11-09, deliberate change): the plates sit in the 240 px band (y 1130 and
    # below), 40 px under the graphics box, so no burst over the timeline meets its text and none takes the opaque
    # plate (on the ads profile the bursts over graphic_timeline.mp4 were flagged and drawn at &H08&)
    assert flagged == []
    span = next((a, b) for a, b, clip in gclips if clip.name == "graphic_timeline.mp4")
    plates = [l for l in ass.read_text("utf-8").splitlines() if ",CapBox," in l]
    assert sum("\\1a&H08&" in l for l in plates) == len(flagged) == 0
    phrases = caption_phrases(_fx.words(c), list(c["emphasis_words"]))
    tim = vc.burst_timings(phrases, first_frame_whole=config.CAPTION_FIRST_FRAME_WHOLE_S, dwell=True)
    for f in flagged:
        s, h, _ = tim[f["burst"]]
        assert s < span[1] and h > span[0]


def test_richat_reel_graphics_and_the_two_line_plate(tmp_path):
    """Real run 1793474843 (its graphics carry element boxes; scene durations as compose_video takes them without
    any: equal shares). Fix round 1 (operator decision 2026-11-04: no plate rises over the graphics box): there is no
    two-line plate, the one-line plates stay clear of every graphic's text, nothing is flagged and the file is
    byte-identical to the build without the guard (was: one flag, the two-line ON THE / ADRAR PLATEAU OF
    MAURITANIA reaching y 991 over the map's 300 KM scale box)."""
    run = Path("output") / "run_richat_structure_sahara_eye_1793474843"   # its committed copy (fixtures/runs)
    words = json.loads(_fx.run_file(run, "words.json").read_text("utf-8"))
    scenes = [_fx.run_path(x) for x in json.loads(
        _fx.run_file(run, "richat_structure_sahara_eye_master.composition.json").read_text("utf-8"))["scenes"]]
    vo = words[-1]["start"] + words[-1]["dur"]
    gclips = vc.caption_graphic_clips(scenes, [(vo + vc.TRANS_DUR) / len(scenes)] * len(scenes))
    comp = VideoComposer()
    a = comp.build_subtitles(words, tmp_path / "a.ass", graphic_clips=gclips).read_text("utf-8")
    flagged = comp.last_caption_graphic_overlap
    # the committed graphic records are what the guard read (a missing sidecar would also give no flag)
    assert len(gclips) == 2 and all(Path(str(c) + vc.CAPTION_CONTRAST_SIDECAR).is_file() for _, _, c in gclips), gclips
    assert flagged == []
    assert all(vc.reel_caption_lines(p) is None for p in caption_phrases(words))
    b = VideoComposer().build_subtitles(words, tmp_path / "b.ass").read_text("utf-8")
    assert a.replace("\\1a&H08&", "") == b


def test_graphic_intrusion_reads_the_sidecar_boxes(tmp_path):
    clip = tmp_path / "graphic_x.mp4"
    clip.write_bytes(b"")
    rect = (200, 1083, 880, 1252)
    Path(str(clip) + ".contrast.json").write_text(json.dumps({"elements": [{"box": [300, 1100, 600, 1150]}]}))
    assert vc.graphic_caption_intrusion(clip, rect)
    Path(str(clip) + ".contrast.json").write_text(json.dumps({"elements": [{"box": [300, 900, 600, 1050]}]}))
    assert not vc.graphic_caption_intrusion(clip, rect)


# ---------------------------------------------------------------- K1: quote marks next to a gold word
def test_quote_marks_next_to_a_gold_word_are_bone(tmp_path):
    words = _even('The alchemists called it "Mercury" and kept it sealed.')
    ass = VideoComposer().build_subtitles(words, tmp_path / "q.ass", emphasis=["Mercury"])
    last = [l for l in ass.read_text("utf-8").splitlines() if ",CapText," in l and "ERCURY" in l][-1]
    tags = re.findall(r"\{([^}]*)\}([^{]*)", last.split(",,", 1)[1])
    quotes = [tag for tag, text in tags if text.strip() == '"']
    assert len(quotes) == 2 and all(BONE in q for q in quotes), tags
    gold = [tag for tag, text in tags if "ERCURY" in text]
    assert gold and all(GOLD in g for g in gold)


def test_no_xfail_is_left_in_this_file():
    src = Path(__file__).read_text("utf-8")
    assert "mark." + "xfail" not in src


# ---------------------------------------------------------------- S1: sidecar cue ends
def _cue_cases():
    for nid, (text, _) in NARRATIONS.items():
        yield nid, timed(text)
    for topic in RUNS:
        yield topic, _fx.words(RUNS[topic])


# Cue or line ends on a dangling word where every wrap of the cue has one (relaxed, at a cost): (case, cue text).
S1_DANGLING_LINE_ENDS = {
    ("wieliczka_salt_mine", "Wieliczka was designated\nas one of the original"):
        "THE ORIGINAL UNESCO WORLD HERITAGE SITES (40 chars) cannot follow OF on one line with AS ONE",
    # fix round 1 (gate and the article rule): HEAD 0036b18's cue ended on PARTISANS USED THE, so the next cue opens on
    # THE (fix round 3: the forced end goes before the determiner; was: on USED) and ends where HEAD's does, at COMBAT;
    # no other line break keeps both lines within 42 characters
    ("odessa_catacombs", "the subterranean tunnels as an active\nhiding place and continued active combat"):
        "AS AN ACTIVE HIDING PLACE AND CONTINUED ACTIVE COMBAT is 52 characters; every other break ends on AS or AN",
    # fix round 1 (gate and the article rule): HEAD 0036b18's cue ended on WHEN A, so this cue opens on DEFORMED and
    # ends where HEAD's does; its only balanced line break ends on OVERLYING (the other: 39 / 15 characters)
    ("richat_structure_sahara_eye", "deformed the overlying\nsedimentary rock into concentric"):
        "DEFORMED THE OVERLYING SEDIMENTARY ROCK / INTO CONCENTRIC is 39 / 15 characters, under SIDECAR_LINE_BALANCE",
}


def test_sidecar_cue_ends():
    for case, words in _cue_cases():
        cues = partition_sidecar_cues(words)
        raw = [w["word"] for w in words]
        assert [w for c in cues for w in c[2].split()] == raw, case
        pos = 0
        for start, end, text in cues:
            ws = text.split()
            lines = text.split("\n")
            assert len(lines) <= config.SIDECAR_MAX_LINES and all(len(l) <= config.SIDECAR_MAX_LINE_CHARS
                                                                  for l in lines)
            if _head_cue(words, pos, len(ws)):   # fix round 1 (gate): HEAD 0036b18's own cue
                assert vc._bare(ws[-1]) not in ("the", "a", "an") or pos + len(ws) == len(raw), (case, text)
                pos += len(ws)
                continue
            assert 1.0 - 1e-6 <= end - start <= config.SIDECAR_MAX_CUE + 1e-6, (case, text)
            if pos > 0 and not re.search(r"[.?!]$", raw[pos - 1]):
                assert not any(re.search(r"[.?!]$", w) for w in ws[:3]), (case, text)   # no sentence tail
            for li, line in enumerate(lines):
                last = line.split()[-1]
                if re.search(r"[.?!]$", last):
                    continue
                if li == len(lines) - 1 and pos + len(ws) - 1 in _head_cue_ends(words):
                    continue   # fix round 2 (the decision as written): the cue ends where HEAD 0036b18's does
                assert vc._bare(last) not in {"the", "a", "an", "of", "has"}, (case, text)
                assert not vc._dangles(last) or (case, text) in S1_DANGLING_LINE_ENDS, (case, text)
            if len(ws) >= 4:
                assert all(len(l.split()) > 1 for l in lines) or (case, text) in {
                    ("edge", "No. It\nwas hidden underground for centuries,")}, (case, text)
            pos += len(ws)


def test_richat_question_does_not_share_a_cue_unless_both_parts_are_long():
    words = _fx.words(RUNS["richat_structure_sahara_eye"])
    starts = {w["word"]: w for w in words}
    cues = partition_sidecar_cues(words)
    pos = 0
    for start, end, text in cues:
        pos += len(text.split())
        if _head_cue(words, pos - len(text.split()), len(text.split())):
            continue   # fix round 1 (gate): HEAD 0036b18's own cue
        if "Sahara? Geologists identify" in text.replace("\n", " "):
            k = text.replace("\n", " ").split().index("Sahara?")
            assert k >= 2 and end - start >= 2.0, text
    assert starts


# ---------------------------------------------------------------- review round 4 (2026-11-04)
def test_a_figure_unit_is_a_bond():
    """THEY REMOVED AN | ESTIMATED 200,000 TONS (tests/test_loop_seam_and_captions_2026_11_03.py, committed at HEAD,
    passes unchanged): the figure's unit is a bond of the figure, as U1 bonds 830 | SQUARE."""
    raw = "They removed an estimated 200,000 tons of rock.".split()
    assert {3, 4} <= caption_bonds(raw) and 2 not in caption_bonds(raw)
    texts = _texts(partition_words_grammatical(_even(" ".join(raw), 0.3, 0.25)))
    assert texts == ["THEY REMOVED AN", "ESTIMATED 200,000 TONS", "OF ROCK"], texts


def test_king_krishna_and_the_rashtrakuta_dynasty_are_two_names():
    texts = _texts(partition_words_grammatical(_even("Built under King Krishna I of the Rashtrakuta dynasty.", 0.3,
                                                     0.25)))
    assert texts[0] == "BUILT" and any(t.startswith("UNDER KING KRISHNA I") for t in texts), texts
    assert "OF THE RASHTRAKUTA DYNASTY" in texts, texts


def test_sidecar_cue_keeps_a_new_clause_and_ago():
    """Review round 4 (non-blocking sidecar item): no cue ends one word into the next clause (ESTABLISHED A
    TERRESTRIAL TIMELINE: DATING), and a span of time keeps its AGO in the cue and line."""
    for case, words in _cue_cases():
        raw = [w["word"] for w in words]
        cues = partition_sidecar_cues(words)
        heads = _head_cue_ends(words)
        pos = -1
        for _, _, text in cues[:-1]:
            ws = text.split()
            pos += len(ws)
            if pos in heads:
                continue   # fix round 1 (gate): a cue end HEAD 0036b18 has
            assert not (len(ws) > 1 and vc.CLAUSE_PUNCT.search(ws[-2]) and not re.search(r"[,;:.?!]$", ws[-1])), \
                (case, text)
        pos = 0
        for _, _, text in cues:
            if not _head_cue(words, pos, len(text.split())):
                for line in text.split("\n"):
                    assert not (line.split()[0].strip(",.") == "ago" and len(cues) > 1), (case, text)
            pos += len(text.split())
        assert [w for c in cues for w in c[2].split()] == raw


# ---------------------------------------------------------------- fix round 3 (2026-11-04): sidecar lines and cue cuts
def _aswan_words():
    """The real unfinished-obelisk run 1792985912 (its fixture case in the social section)."""
    case = next(c for c in _fx.load("social") if str(c["run"]).endswith("run_unfinished_obelisk_aswan_1792985912"))
    return _fx.json_of(_fx.run_file(case["run"], "words.json"))


def _nazca_words():
    """The real nazca run 1792961797: not a fixture case (the reviewer's 35-run corpus case), read from its run."""
    return json.loads(_fx.run_file("output/run_nazca_lines_runway_paradox_1792961797", "words.json").read_text("utf-8"))


def _line_and_cue_breaks(cues):
    """The word positions after which a cue line or a cue ends."""
    out, at = set(), -1
    for _, _, text in cues:
        for line in text.split("\n"):
            at += len(line.split())
            out.add(at)
    return out


@pytest.mark.parametrize("which,cue,held", [
    # review blocker: HEAD 0036b18's NAMED AFTER / POLISH BOOK DEALER (was: NAMED AFTER POLISH / BOOK DEALER)
    ("voynich", "The manuscript is named after\nPolish book dealer Wilfrid Voynich,", ("Polish", "book")),
    # review blocker: HEAD's SHIELDING THESE / SHALLOW TRENCHES (was: SHIELDING THESE SHALLOW / TRENCHES)
    ("nazca", "layer, shielding these\nshallow trenches from wind.", ("shallow", "trenches")),
    # review blocker: HEAD's cue again, its phrasal verb whole (was: BURNED AND WEDGED / AWAY UPPER ROCK, CARVING
    # DIRECTLY); the line breaks before the conjunction, where HEAD broke after it
    ("aswan", "Ancient workers burned\nand wedged away upper rock,", ("wedged", "away")),
])
def test_sidecar_lines_keep_heads_phrases(which, cue, held):
    words = {"voynich": lambda: _fx.words(RUNS["voynich_manuscript"]), "nazca": _nazca_words,
             "aswan": _aswan_words}[which]()
    words = [w for w in words if str(w.get("word", "")).strip()]
    cues = partition_sidecar_cues(words)
    texts = [t for _, _, t in cues]
    assert cue in texts, texts
    raw = [str(w["word"]).strip() for w in words]
    breaks = _line_and_cue_breaks(cues)
    pairs = [k for k in range(len(raw) - 1) if (raw[k], raw[k + 1]) == held]
    assert pairs and not set(pairs) & breaks, held
    pos = sum(len(t.split()) for t in texts[:texts.index(cue)])
    assert _head_cue(words, pos, len(cue.split())), cue   # HEAD 0036b18's own cue, start and end


def test_aswan_cues_change_only_where_the_article_rule_forces():
    """HEAD 0036b18 ended a cue on STILL MARK THE (the article rule moves it): the one new cue end goes before WHERE,
    and the cue before it stays HEAD's (was: CARVING DIRECTLY || INTO BEDROCK, rebuilding HEAD's cue)."""
    words = [w for w in _aswan_words() if str(w.get("word", "")).strip()]
    raw = [str(w["word"]).strip() for w in words]
    ends = set(_ends([t.split() for _, _, t in partition_sidecar_cues(words)])[:-1])
    new = sorted((raw[k], raw[k + 1]) for k in ends - _head_cue_ends(words))
    assert new == sorted([("bedrock", "where"), ("trench.", "Surveys")]), new


def test_richat_forced_cue_end_keeps_the_noun_phrase_whole():
    """Review blocker: the article-rule cut landed as THIS CIRCULAR || GEOLOGICAL FORMATION. It now goes before the
    determiner (FORMED || THIS); THIS CIRCULAR GEOLOGICAL FORMATION is one cue line."""
    words = _fx.words(RUNS["richat_structure_sahara_eye"])
    raw = [w["word"] for w in words]
    cues = partition_sidecar_cues(words)
    k = raw.index("circular")
    assert raw[k - 1:k + 3] == ["this", "circular", "geological", "formation"]
    assert not {k - 1, k, k + 1} & _line_and_cue_breaks(cues)
    assert k - 2 in set(_ends([t.split() for _, _, t in cues])[:-1])   # the cue ends on FORMED
    assert vc._inside_noun_phrase(raw, k) and vc._inside_noun_phrase(raw, k + 1)
    assert not vc._inside_noun_phrase(raw, k + 2) and not vc._inside_noun_phrase(raw, k - 2)


def test_a_heads_line_break_is_not_restored_before_a_function_word():
    """Fix round 3: HEAD's line break wins only over a freeze-pass break mid-phrase. Where the freeze-pass break opens
    the bottom line on a function word (TRACKING / ITS LOCATION, CONCLUDED IT / WAS INCINERATED), it stays, so no
    line ends on ITS or IT WAS (real amber runs 1792790550 and 1792805842)."""
    words = _fx.words(RUNS["amber_room_heist"])
    texts = [t for _, _, t in partition_sidecar_cues(words)]
    assert "Documentation tracking\nits location ends in August 1944," in texts, texts
    assert not any(line.split()[-1] in ("its", "their") for t in texts for line in t.split("\n")[:-1]), texts
