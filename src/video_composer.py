"""
src/video_composer.py
=====================
FFmpeg assembly embodying the locked production standards:
1. Anton 72 px running-speech kinetic captions on an INK plate: each burst shown at one size, its words
   appearing as they are spoken (upcoming words are transparent placeholders, so the line never moves), bone
   text, gold for the script's key terms only; zero-overlap ASS events inside the Reel safe box
   (safe_zones.REEL: the plate ends above y 1248). 16:9 burns in one Montserrat Bold line on the same plate.
2. Varied Ken Burns motion (rendered at 2x then downscaled to avoid zoompan jitter)
   with matching forward drift on the opening and closing shots for the loop.
3. Voice cleanup, sidechain ducking of the ambient bed under speech, then measured
   gain + 4x-oversampled brickwall limiting to hit -14 LUFS (EBU R128) with true peak
   below -1.5 dBTP. (Single-filter loudnorm undershoots when the hammer transient makes
   linear mode impossible, and overshoots true peak in its dynamic mode.)
4. H.264 CRF 18 (slow), TV-range yuv420p, AAC 48 kHz, +faststart for streaming.
"""

import logging
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any, Dict, List, Optional, Set, Tuple
from PIL import Image, ImageFilter

from .config import (
    BASE_DIR,
    AUDIO_SAMPLE_RATE,
    FONTS_DIR,
    FONT_NAME,
    FONT_SIZE,
    FONT_SIZE_ASS,
    CAPTION_MARGIN_V,
    MIN_BURST_WORDS,
    TARGET_BURST_WORDS,
    MAX_BURST_WORDS,
    MAX_BURST_CHARS,
    CAPTION_SMALL_CAPS,
    CAPTION_SC_SCALE,
    CAPTION_PAD_X,
    CAPTION_PAD_Y,
    CAPTION_BORDER_RADIUS,
    EMPHASIS_POP,
    COL_BACK,  # the pill; the text colours are the brand's (below)
    CAPTION_LEAD_FRAMES,
    CAPTION_PAUSE_GAP,
    CAPTION_PAUSE_TAIL,
    CAPTION_MIN_CHUNK,
    CAPTION_WIDE_FONT,
    CAPTION_WIDE_SIZE,
    CAPTION_WIDE_MARGIN_V,
    CAPTION_WIDE_MAX_CHARS,
    CAPTION_WIDE_MIN_WORDS,
    CAPTION_WIDE_TARGET_WORDS,
    CAPTION_WIDE_MAX_WORDS,
    SIDECAR_MAX_LINE_CHARS,
    SIDECAR_MAX_LINES,
    SIDECAR_MIN_CUE,
    SIDECAR_MAX_CUE,
    RECON_FONT,
    RECON_SIZE_PORTRAIT,
    RECON_SIZE_WIDE,
    RECON_PLATE_ALPHA,
    TARGET_LUFS,
    TARGET_TRUE_PEAK,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
)
from .typography import is_video
from .parallax import preset_for
from . import brand as _brand

# Caption colours from the brand tokens (visual overhaul, 2026-10-29): spoken words in bone, the script's key words
# in the brand gold (#FFD700 until then, one of three golds on screen), upcoming words in light stone. The style
# stays the pill: measured on graded frames of three topics (docs/reviews/brand-overhaul-2026-10-29), the pill
# kept spoken words at 9.1:1 or better and gold at 6.0:1, where the critic's stroke-and-halo fell to 4.07:1 and
# 2.56:1 on bright desert; the old grey upcoming words read 3.70:1 on the pill, so they are light stone now.
COL_ACTIVE = COL_PAST = _brand.ass_colour(_brand.BONE)
COL_EMPHASIS = _brand.ass_colour(_brand.GOLD)
COL_FUTURE = _brand.ass_colour(_brand.STONE_LT)
# Upcoming words (caption review round 2, 2026-11-02, finding 2): light stone against bone was about 25% apart in
# luminance, so the spoken word barely stood out. They are now drawn fully transparent: they keep their place in
# the line (no reflow as words appear) and show only when spoken. COL_FUTURE stays their (invisible) colour.
CAPTION_HIDDEN = r"{\alpha&HFF&}"

log = logging.getLogger(__name__)

TRANSITIONS = [
    "zoomin", "dissolve", "smoothleft", "fadeblack",
    "zoomin", "dissolve", "smoothright", "fade",
    "zoomin", "dissolve", "smoothleft", "fadeblack",
]
PARALLAX_TO_TRANSITION = {
    "push": "zoomin",
    "drift_left": "smoothleft",
    "drift_right": "smoothright",
    "rise": "smoothup",
    "pull": "dissolve",
}
TRANS_DUR = 0.40
# An AI motion clip keeps a locked camera (motion.build_motion_prompt: camera moves warped the architecture), so
# an opening that is one held still for its first seconds (Meta's Creator assistant, 2026-10-28: "viewers decide
# in under two seconds"). The opening clip gets the stills' push on top, a crop zoom that starts at frame 0 and
# changes nothing in the picture.
HOOK_CLIP_ZOOM = "min(1+0.0016*on,1.24)"


def graphic_vignette_enable(scene_images: List[Path], durations: List[float]) -> str:
    """The vignette filter's `enable` option keeping it off over graphic clips (graphic_*.mp4), "" when none.

    Scene i is on screen from the boundary before it to the boundary after it (cuts are centred on boundaries)."""
    spans, start = [], 0.0
    for item, dur in zip(scene_images, durations):
        if Path(item).name.startswith("graphic_"):
            spans.append((start, start + dur))
        start += dur
    if not spans:
        return ""
    inside = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in spans)
    return f":enable='not({inside})'"


def is_ai_motion_clip(path: Path) -> bool:
    """A clip src/motion.py made from a photo (motion_NN.mp4), as opposed to footage, a graphic or a manual
    hook clip, which move by themselves."""
    return is_video(path) and Path(path).stem.startswith("motion_")


def hook_clip_filter(i: int, width: int, height: int) -> str:
    """The filter for an AI motion clip opening a vertical video: the same push-in as the opening still,
    rendered at twice the size (as the stills are, against zoompan's jitter), from its first frame."""
    return (f"[{i}:v]tpad=stop_mode=clone:stop=-1,scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
            f"crop={width * 2}:{height * 2},zoompan=z='{HOOK_CLIP_ZOOM}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={width}x{height}:fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]")


class RenderError(RuntimeError):
    pass


FFMPEG_TIMEOUT_SECONDS = 3600  # a documentary master takes ~12 min; a hung ffmpeg must not hold a run forever


def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        raise RenderError(f"ffmpeg did not finish within {FFMPEG_TIMEOUT_SECONDS}s; stopped.") from e
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed (exit {res.returncode}):\n{tail}")
    return res


from .encoding import bounded_encode, get_video_encoder as _get_video_encoder, get_intermediate_video_encoder as _get_intermediate_video_encoder


def _ass_escape(text: str) -> str:
    """Neutralizes ASS override syntax so caption text can't inject styling tags."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


from .safe_zones import CAPTION_CLEARANCE, REEL as _REEL_UI

# The widest caption text, centred: its pill (text plus padding) fills at most the width every Reel platform
# leaves free (safe_zones.REEL.centred_width, 696 px: Shorts' icon column takes the right 192 px). 640 px.
MAX_CAPTION_WIDTH = _REEL_UI.centred_width() - 2 * CAPTION_PAD_X


TERMINAL_PUNCT = re.compile(r"[\.\?!…]+[\"\'”’\)]*$")
CLAUSE_PUNCT = re.compile(r"[,;:\-—–]+[\"\'”’\)]*$")


def rounded_rect_path(w: int, h: int, r: int) -> str:
    """Vector path in ASS drawing commands for a rectangle with rounded corners."""
    c = round(r * 0.552285)
    return (
        f"m {r} 0 "
        f"l {w - r} 0 "
        f"b {w - r + c} 0 {w} {r - c} {w} {r} "
        f"l {w} {h - r} "
        f"b {w} {h - r + c} {w - r + c} {h} {w - r} {h} "
        f"l {r} {h} "
        f"b {r - c} {h} 0 {h - r + c} 0 {h - r} "
        f"l 0 {r} "
        f"b 0 {r - c} {r - c} 0 {r} 0"
    )


ROMAN_NUMERALS = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX"
}
ERA_DESIGNATORS = {
    "bc", "bce", "ad", "ce", "bp", "mya", "kya", "ya"
}
ORDINAL_WORDS = {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
    "eighteenth", "nineteenth", "twentieth", "twenty-first"
}
SPELLED_NUMBERS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion"
}
MEASURE_UNITS = {
    # Distance / Dimension
    "feet", "ft", "meters", "meter", "m", "kilometres", "kilometre", "km", "miles", "mile",
    "inches", "inch", "in", "cm", "centimeters", "centimeter", "centimetres", "centimetre",
    "mm", "millimeters", "millimeter", "yards", "yard", "yd",
    # Weight / Mass
    "tons", "ton", "kg", "lbs", "pounds", "pound", "grams", "gram", "g", "ounces", "ounce", "oz",
    # Time / Chronology
    "years", "year", "centuries", "century", "decades", "decade", "millennia", "millennium",
    "months", "month", "weeks", "week", "days", "day", "hours", "hour", "hr", "hrs",
    "minutes", "minute", "min", "mins", "seconds", "second", "sec", "secs",
    # Architecture / Archaeology Specifics
    "stories", "story", "levels", "level", "shafts", "shaft", "chambers", "chamber",
    "rooms", "room", "monasteries", "monastery", "temples", "temple", "gates", "gate",
    "steps", "step", "tiers", "tier", "pillars", "pillar", "columns", "column",
    "gears", "gear", "teeth", "tooth", "fragments", "fragment", "pieces", "piece",
    "monoliths", "monolith", "blocks", "block", "slabs", "slab",
    # Percentages & Angles
    "percent", "pct", "degrees", "deg",
}
DESIGNATORS = {
    "cave", "caves", "level", "levels", "chamber", "chambers", "room", "rooms", "phase", "phases",
    "shaft", "shafts", "tier", "tiers", "gate", "gates", "tomb", "tombs", "site", "sites",
    "trench", "trenches", "layer", "layers", "step", "steps", "dynasty", "century", "centuries",
    "chapter", "part", "no.", "number", "fragment", "fragments", "piece", "pieces"
}
TITLES = {"king", "queen", "emperor", "pope", "saint", "st.", "dr.", "mr.", "mrs.", "lord", "sir"}
DANGLING_TAILS = {
    # Determiners & Articles
    "the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "my", "your", "his", "her",
    # Prepositions
    "of", "at", "to", "in", "for", "on", "with", "by", "from", "into", "over", "under", "about",
    "across", "through", "between", "during", "before", "after", "upon", "within", "without",
    "toward", "towards", "against", "behind", "beneath", "beside",
    # Conjunctions (coordinating & subordinating)
    "and", "or", "but", "nor", "so", "yet", "though", "although", "while", "whereas",
    "because", "since", "until", "unless", "if", "as", "than", "whether",
    # Relative Pronouns
    "which", "who", "whom", "whose", "where", "when",
    # Copulas & Auxiliaries (dangling verb tails before predicates)
    "is", "was", "are", "were", "been", "being",
}

CLAUSE_STARTERS = {
    "though", "although", "while", "whereas", "because", "since", "until", "unless",
    "if", "when", "where", "however", "yet", "which", "who", "whom", "whose"
}


def _is_atomic_boundary(w1_raw: str, w2_raw: str) -> bool:
    """True if w1 and w2 must NOT be split across burst boundaries."""
    w1 = re.sub(r"[^\w]", "", w1_raw).lower()
    w2 = re.sub(r"[^\w]", "", w2_raw).lower()
    if not w1 or not w2:
        return False
    # Number / Spelled Number + Era Designator (e.g. '205' + 'BC', '87' + 'BC', '300' + 'BCE', '10,000' + 'BP')
    if (any(c.isdigit() for c in w1) or w1 in SPELLED_NUMBERS) and w2 in ERA_DESIGNATORS:
        return True
    # Prefix Era / Circa + Number (e.g. 'AD' + '79', 'circa' + '1900', 'c.' + '200')
    if w1 in {"ad", "circa", "c", "ca"} and any(c.isdigit() for c in w2):
        return True
    # Number / Spelled Number / Ordinal + Measurement Unit (e.g. '200,000' + 'tons', '100' + 'feet', '14th' + 'century', 'five' + 'inches')
    if (any(c.isdigit() for c in w1) or w1 in SPELLED_NUMBERS or w1 in ORDINAL_WORDS) and (w2 in MEASURE_UNITS or w2 in {"century", "centuries", "millennium", "millennia", "era", "dynasty"}):
        return True
    # Entity Designator + Number / Single-letter Code / Roman Numeral / Ordinal (e.g. 'Cave' + '16', 'Fragment' + 'A', 'Dynasty' + 'IV')
    # Note: Prepositions like 'in', 'at', 'of' are NOT codes and must not be bound here.
    if w1 in DESIGNATORS and (any(c.isdigit() for c in w2) or (len(w2) == 1 and w2.isalpha()) or w2.upper() in ROMAN_NUMERALS or w2 in {"first", "second", "third"}):
        return True
    # Honorific/Title + Name (e.g. 'King' + 'Krishna')
    if w1 in TITLES:
        return True
    # Determiner + Ordinal / Regal (e.g. 'the' + 'first')
    if w1 == "the" and w2 in ORDINAL_WORDS:
        return True
    # Name + Roman Numeral (e.g. 'Krishna' + 'I')
    if w2.upper() in ROMAN_NUMERALS:
        return True
    return False



def partition_words_grammatical(
    words: List[Dict[str, Any]],
    min_words: int = MIN_BURST_WORDS,
    target_words: int = TARGET_BURST_WORDS,
    max_words: int = MAX_BURST_WORDS,
    max_chars: int = MAX_BURST_CHARS,
    fits: Callable[[list[dict[str, Any]]], bool] | None = None,
    glance: tuple[int, int] = (14, 24),
    prefer: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> List[List[Dict[str, Any]]]:
    """
    Groups spoken words into readable phrases respecting grammatical boundaries:
    1. Splits strictly at terminal punctuation (. ? ! …) so every new sentence starts fresh.
    2. Dynamic partition ensures phrases use at least min_words (default 2) where possible.
    3. Heavily penalizes ending phrases on orphan prepositions/determiners/conjunctions ('at', 'of', 'though').
    4. Strongly rewards starting new caption blocks on subordinating conjunctions ('though', 'while', 'because').
    5. Respects natural clause punctuation (, ; : - —) and limits line length to avoid margin overflow.
    6. Enforces atomic boundaries to prevent breaking [Name + Roman Numeral] or [Designator + Number].
    `fits` says whether a multi-word burst may be formed at all and `prefer` whether it fits as wanted (a burst
    that does not is penalised, and the whole-remainder shortcut needs it). The Reel defaults: a burst must fit at
    CAPTION_BURST_MIN_SIZE and should fit at the full caption size, so bursts keep one size unless every full-size
    split would end on a dangling word (caption review round 2, finding 1). The 16:9 burn-in passes its own `fits`.
    `glance` is the burst length (characters) rewarded as readable at a glance.
    """
    if fits is None:
        fits = _fits_caption_burst
        prefer = prefer or _fits_caption_width
    prefer = prefer or fits
    sentences: List[List[Dict[str, Any]]] = []
    curr: List[Dict[str, Any]] = []
    for w in words:
        curr.append(w)
        if TERMINAL_PUNCT.search(w.get("word", "")):
            sentences.append(curr)
            curr = []
    if curr:
        sentences.append(curr)

    def _partition_sentence(sent: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        n = len(sent)
        if n == 0:
            return []
        if n <= min_words and (n == 1 or fits(sent)):
            return [sent]
        memo: Dict[int, Optional[List[List[Dict[str, Any]]]]] = {}

        def _solve(i: int) -> Optional[List[List[Dict[str, Any]]]]:
            if i == n:
                return []
            if i in memo:
                return memo[i]
            rem = n - i
            rem_clean = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in sent[i:]]
            rem_txt = " ".join(rem_clean)
            if min_words <= rem <= max_words and len(rem_txt) <= max_chars and prefer(sent[i:]):
                memo[i] = [sent[i:]]
                return memo[i]

            best: Optional[List[List[Dict[str, Any]]]] = None
            best_score = -999999
            candidate_sizes = [target_words, min_words, 4, 5, 6] + list(range(7, max_words + 1))
            for size in candidate_sizes:
                if i + size <= n:
                    if i + size < n and _is_atomic_boundary(sent[i + size - 1].get("word", ""), sent[i + size].get("word", "")):
                        continue
                    rem_after = n - (i + size)
                    if rem_after > 0 and rem_after < min_words:
                        continue
                    after = _solve(i + size)
                    if after is not None:
                        chunk = sent[i:i + size]
                        if len(chunk) > 1 and not fits(chunk):
                            continue  # a burst its pill cannot hold inside the platforms' safe width is never a choice
                        clean_chunk = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in chunk]
                        txt = " ".join(clean_chunk)
                        first_w = re.sub(r"[^\w]", "", clean_chunk[0]).lower()
                        last_w = re.sub(r"[^\w]", "", clean_chunk[-1]).lower()
                        score = 100
                        if len(txt) <= max_chars:
                            score += 50
                        else:
                            score -= (len(txt) - max_chars) * 20
                        if last_w in DANGLING_TAILS:
                            score -= 200  # Strictly penalize dangling prepositions/determiners/conjunctions at block ends
                        if last_w in CLAUSE_STARTERS:
                            score -= 300  # Conjunctions/clause starters must start a new block, never end one
                        if first_w in ERA_DESIGNATORS:
                            score -= 500  # Never start a block with an orphaned era designator (e.g. 'BC')
                        if first_w in MEASURE_UNITS:
                            score -= 400  # Never start a block with an orphaned measurement unit (e.g. 'feet')
                        if re.search(r"[\,\;]$", chunk[-1].get("word", "")):
                            score += 70   # Reward breaking at clause commas
                        # If next word begins a new subordinate clause, strongly reward cutting right before it!
                        if i + size < n:
                            next_w = re.sub(r"[^\w]", "", sent[i + size].get("word", "")).lower()
                            if next_w in CLAUSE_STARTERS:
                                if re.search(r"[\,\;]$", chunk[-1].get("word", "")):
                                    score += 160  # Large reward for breaking before 'though', 'although', etc. after comma
                                else:
                                    score += 60
                        if len(chunk) > 1 and not prefer(chunk):
                            score -= SHRUNK_BURST_PENALTY  # shown below the full size: only to avoid a dangling end
                        if glance[0] <= len(txt) <= glance[1]:
                            score += 40   # Reward optimal glanceable width
                        if score > best_score:
                            best_score = score
                            best = [chunk] + after
            if best is None:
                cut = min(rem, target_words)
                while cut > 1 and not fits(sent[i:i + cut]):
                    cut -= 1  # shorter bursts rather than a pill wider than the safe width
                best = [sent[i:i + cut]] + (_solve(i + cut) or [sent[i + cut:]])
            memo[i] = best
            return best

        res = _solve(0)
        return res if res is not None else [sent]

    all_phrases: List[List[Dict[str, Any]]] = []
    for s in sentences:
        for p in _partition_sentence(s):
            if p:
                all_phrases.append(p)
    return all_phrases


CAPTION_ACRONYMS = {
    "BC", "AD", "BCE", "CE", "BP", "MYA", "KYA", "US", "USA", "UK", "DNA", "GPS",
    "UNESCO", "CIA", "FBI", "NASA", "USSR", "WWI", "WWII", "AM", "PM", "FT", "KM", "M", "KG"
}


def caption_runs(word: str, sc_scale: int = CAPTION_SC_SCALE) -> list[tuple[str, bool]]:
    """The word as the caption shows it: (UPPER-CASED text, full size?) runs, adjacent runs of one size merged.

    Casing follows the narration (caption review round 2, 2026-11-02, finding 3): a full-size initial only where
    the narration's word itself starts with a capital (a name, a sentence's first word, "I"); a lower-case word is
    small capitals throughout, so the line no longer reads as Title Case. Acronyms and Roman numerals the narration
    writes in capitals stay full size. A possessive or contraction is one word ("Kailasa's": one initial, the s
    after the apostrophe small; the first posted Reel showed KAILASA'S with a full-size S, 2026-10-24). Figures are
    full size; other punctuation takes the size of the letters before it."""
    tokens = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[^A-Za-z]+", word)
    runs: list[tuple[str, bool]] = []
    for tok in tokens:
        if tok[:1].isalpha():
            upper = tok.upper()
            letters = re.sub(r"[^A-Za-z]", "", tok)
            if letters.isupper() and (upper in CAPTION_ACRONYMS or upper in ROMAN_NUMERALS or 2 <= len(letters) <= 4):
                runs.append((upper, True))
            elif tok[0].isupper():
                runs.append((upper[0], True))
                if len(upper) > 1:
                    runs.append((upper[1:], False))
            else:
                runs.append((upper, False))
        elif any(c.isdigit() for c in tok):
            runs.append((tok, True))
        else:
            runs.append((tok, runs[-1][1] if runs else True))
    merged: list[tuple[str, bool]] = []
    for text, full in runs:
        if merged and merged[-1][1] == full:
            merged[-1] = (merged[-1][0] + text, full)
        else:
            merged.append((text, full))
    return merged


def _phrase_words(phrase: Any) -> list[str]:
    """A burst's words as shown: the last one without its trailing comma, semicolon or full stop."""
    if isinstance(phrase, str):
        words = phrase.split()
    else:
        words = [w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w)) for w in phrase]
    if words:
        words[-1] = re.sub(r"[\,\;\.]+$", "", words[-1])
    return words


def _caption_text_width(words: list[str], size: int, sc_scale: int = CAPTION_SC_SCALE) -> float:
    """Rendered width in px of these words at caption size `size`, with the caption's small capitals."""
    from .typography import get_font
    f_base = get_font("Anton", size)
    f_sc = get_font("Anton", max(16, round(size * (sc_scale / 100.0))))
    total = 0.0
    for wi, word in enumerate(words):
        if wi > 0:
            total += f_base.getlength(" ")
        if not CAPTION_SMALL_CAPS:
            total += f_base.getlength(_ass_escape(word).upper())
            continue
        for text, full in caption_runs(_ass_escape(word), sc_scale):
            total += (f_base if full else f_sc).getlength(text)
    return total


def measure_caption_phrase(phrase: Any, fit_rendered: int, sc_scale: int = CAPTION_SC_SCALE) -> tuple[int, int]:
    """Measures rendered width and height of a caption phrase in pixels."""
    total_w = _caption_text_width(_phrase_words(phrase), fit_rendered, sc_scale)
    # 75px accounts for ascenders, full caps, and descenders like commas at base FONT_SIZE 76
    text_h = max(30, round(75 * (fit_rendered / FONT_SIZE)))
    return round(total_w), text_h


CAPTION_MIN_SIZE = 60  # px: the smallest a single over-long word shrinks to (round 2: 48 made bursts jump 33%)
CAPTION_HARD_MIN = 36  # px: below CAPTION_MIN_SIZE only for a word that would otherwise leave the safe width


def _caption_fit_size(phrase: Any, scale: float = 1.0, max_width: int = MAX_CAPTION_WIDTH) -> int:
    """Largest font size <= FONT_SIZE at which `phrase` (shown at up to `scale`) fits max_width.

    Since the caption review of 2026-11-02 (round 2, finding 1) the phrase builder forms bursts that fit at
    FONT_SIZE, so a Reel's bursts keep one size; it accepts one down to CAPTION_BURST_MIN_SIZE only where every
    full-size split would end on a dangling word. A single word too long for the line shrinks down to
    CAPTION_MIN_SIZE (and below it only as far as the safe width requires)."""
    words = _phrase_words(phrase)
    width = _caption_text_width(words, FONT_SIZE) * scale
    if width <= max_width:
        return FONT_SIZE
    floor = CAPTION_MIN_SIZE if len(words) == 1 else CAPTION_BURST_MIN_SIZE
    size = max(floor, int(FONT_SIZE * max_width / width))
    while size > CAPTION_HARD_MIN and _caption_text_width(words, size) * scale > max_width:
        size -= 1
    return size


CAPTION_BURST_MIN_SIZE = 66  # px: a multi-word burst never shows below this (8% under FONT_SIZE, was 33%)
SHRUNK_BURST_PENALTY = 150  # the phrase builder's cost of a burst below full size: more than a short burst,
                            # less than one ending on a dangling article or preposition (200)
END_CARD_BELOW_BODY_LU = 6.0  # the end card sits this far under the narrated body (the approved prototype: about 6)


def _fits_caption_width(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a burst fits the caption width at the full caption size, as rendered (small capitals included).
    A burst that does not is never formed from more than one word: until 2026-11-02 this tested the smallest
    size (48 px), so a wide burst shrank and adjacent bursts differed in size by up to a third (caption review
    round 2, finding 1); the pill must also stay inside the area the platforms leave free (safe_zones.REEL)."""
    return _caption_text_width(_phrase_words(phrase), FONT_SIZE) <= max_width


def _fits_caption_burst(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a multi-word burst fits the caption width at CAPTION_BURST_MIN_SIZE, the most a burst may shrink."""
    return _caption_text_width(_phrase_words(phrase), CAPTION_BURST_MIN_SIZE) <= max_width


def format_word_caption(
    word: str,
    colour: str,
    is_key: bool,
    is_spoken_now: bool,
    has_key: bool,
    pop_val: int = EMPHASIS_POP,
    sc_scale: int = CAPTION_SC_SCALE,
    enable_small_caps: bool = CAPTION_SMALL_CAPS,
) -> str:
    """Formats a word for ASS kinetic subtitles.
    When enable_small_caps is True, the word is upper-cased in runs (caption_runs): full size where the narration
    has a capital (the initial of a capitalised word, an acronym), small capitals (sc_scale%) elsewhere. Stripping
    the override tags leaves the narration's word, upper-cased.
    """
    clean = _ass_escape(word)
    if not enable_small_caps:
        return f"{{\\c{colour}\\fscx100\\fscy100}}{clean.upper()}"
    init_tag = f"\\c{colour}\\fscx100\\fscy100"
    sc_tag = f"\\c{colour}\\fscx{sc_scale}\\fscy{sc_scale}"
    return "".join(f"{{{init_tag if full else sc_tag}}}{text}" for text, full in caption_runs(clean, sc_scale))


def _word_end(w: dict[str, Any]) -> float:
    """When a timed word stops being heard: its duration if given, else its end, else 0.3 s after its start."""
    start = float(w["start"])
    if w.get("dur") is not None:
        return start + float(w["dur"])
    if w.get("end") is not None:
        return float(w["end"])
    return start + 0.3


def merge_short_bursts(
    phrases: list[list[dict[str, Any]]],
    fits: Callable[[list[dict[str, Any]]], bool],
    max_words: int,
    min_dur: float = CAPTION_MIN_CHUNK,
    pause_gap: float = CAPTION_PAUSE_GAP,
) -> list[list[dict[str, Any]]]:
    """Joins each burst heard for less than `min_dur` to a neighbour (round 2, finding 4: a flash is not read),
    preferring the neighbour in the same sentence, then the one before. A join must still fit the line, keep to
    `max_words` and not bridge a pause longer than `pause_gap`; a burst with no such neighbour stays as it is."""
    out = [list(p) for p in phrases if p]
    i = 0
    while i < len(out):
        p = out[i]
        if len(out) == 1 or _word_end(p[-1]) - float(p[0]["start"]) >= min_dur:
            i += 1
            continue
        cands: list[tuple[int, int]] = []  # (rank, neighbour index); rank 0 = same sentence
        if i > 0 and float(p[0]["start"]) - _word_end(out[i - 1][-1]) <= pause_gap:
            cands.append((0 if not TERMINAL_PUNCT.search(out[i - 1][-1].get("word", "")) else 1, i - 1))
        if i + 1 < len(out) and float(out[i + 1][0]["start"]) - _word_end(p[-1]) <= pause_gap:
            cands.append((0 if not TERMINAL_PUNCT.search(p[-1].get("word", "")) else 1, i + 1))
        joined = None
        for _, j in sorted(cands):
            combo = out[j] + p if j < i else p + out[j]
            if len(combo) <= max_words and fits(combo):
                joined = j
                out[min(i, j)] = combo
                del out[max(i, j)]
                break
        i = min(i, joined) if joined is not None else i + 1
    return out


def burst_timings(
    phrases: list[list[dict[str, Any]]],
    lead: float = CAPTION_LEAD_FRAMES / VIDEO_FPS,
    pause_gap: float = CAPTION_PAUSE_GAP,
    pause_tail: float = CAPTION_PAUSE_TAIL,
) -> list[tuple[float, float, list[float]]]:
    """(show, hide, word reveal times) for each burst (round 2, finding 4). A burst appears `lead` (two frames)
    before its first word is heard; each word appears the same lead before its own onset. It stays until the next
    burst appears, except across a pause longer than `pause_gap`, where it leaves `pause_tail` after its last word.
    The last burst leaves 0.10 s after its last word. No two bursts overlap."""
    out: list[tuple[float, float, list[float]]] = []
    prev_hide = 0.0
    for pi, phrase in enumerate(phrases):
        first = float(phrase[0]["start"])
        last_end = _word_end(phrase[-1])
        show = max(0.0, first - lead, prev_hide)
        reveals = [show]
        for w in phrase[1:]:
            reveals.append(max(reveals[-1], float(w["start"]) - lead))
        if pi + 1 < len(phrases):
            next_first = float(phrases[pi + 1][0]["start"])
            next_show = max(0.0, next_first - lead)
            hide = last_end + pause_tail if next_first - last_end > pause_gap else next_show
            hide = min(hide, next_show)
        else:
            hide = last_end + 0.10
        hide = max(hide, reveals[-1] + 0.02)
        out.append((show, hide, reveals))
        prev_hide = hide
    return out


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def emphasis_indices(words: List[Dict[str, Any]], terms: Optional[List[str]]) -> Set[int]:
    """Word positions to emphasise: the first occurrence of each key term (multi-word terms
    match as a sequence). Without terms, the first two words containing a digit."""
    tokens = [_norm(w["word"]) for w in words]
    picked: Set[int] = set()
    for term in (terms or [])[:3]:
        seq = [t for t in (_norm(x) for x in term.split()) if t]
        for i in range(len(tokens) - len(seq) + 1):
            if seq and tokens[i:i + len(seq)] == seq:
                picked.update(range(i, i + len(seq)))
                break
    if not terms:
        picked = set([i for i, w in enumerate(words) if any(c.isdigit() for c in w["word"])][:2])
    return picked


def _audio_chain(vo_idx: int, bg_idx: int, pad: float = 0.0, card_at: Optional[float] = None, card_db: float = 0.0) -> str:
    """Voice cleanup + optical vocal leveling + center spectral pocket carving + ducked stereo bed.
    pad: seconds of silence after the voice (the end card), so the mix runs on with the bed alone.
    card_at/card_db: the end card's level set from card_at on (a 0.3 s ramp), measured per run."""
    pad_filter = f"apad=pad_dur={pad:.3f}," if pad > 0 else ""
    card_filter = ""
    if card_at is not None and card_db:
        lin = 10 ** (card_db / 20)
        card_filter = f",volume='1+({lin:.5f}-1)*min(max((t-{card_at:.3f})/0.3,0),1)':eval=frame"
    return (
        f"[{vo_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,{pad_filter}"
        f"highpass=f=85,compand=attacks=0.02:decays=0.15:points=-80/-80|-40/-35|-20/-16|0/-10,"
        f"equalizer=f=3500:width_type=q:w=1.2:g=1.8,equalizer=f=7000:width_type=q:w=2.0:g=-2.2,"
        f"asplit[v_main][v_sc];"
        f"[{bg_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"equalizer=f=3200:width_type=q:w=1.5:g=-3.0[bg_carved];"
        f"[bg_carved][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,aformat=channel_layouts=stereo{card_filter}"
    )


def _master_chain(gain_db: float) -> str:
    """Gain + true-peak-safe limiter (limiting at 4x rate catches inter-sample peaks) with dynamic range breathing room.
    A 16 kHz low-pass ahead of the limiter: AAC at 192 kb/s drops that band, and dropping it after the limiter made a
    -1.9 dBTP programme encode at +1.0 dBTP (Roman concrete, 2026-10-28; docs/reviews/aac-overshoot-2026-10-28).
    The ceiling and the band are shared with finalize_encoded_audio, which limits once more if the encode still
    overshoots."""
    from .encoded_audio import LOWPASS_HZ, MASTER_LIMIT
    return (
        f",volume={gain_db:.2f}dB,lowpass=f={LOWPASS_HZ}:poles=2,aresample={AUDIO_SAMPLE_RATE * 4},"
        f"alimiter=limit={MASTER_LIMIT:.2f}:attack=2.5:release=80:level=disabled:asc=1,"
        f"aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo"
    )


# The film grade (2026-10-29). The Kodak 2383 print LUT expects log film scans; on our display-referred stills it
# also pushed every colour warm/yellow and up to 2.5x more saturated, so a lamp-lit stone door that colour_balance
# had taken from 70/71/31 to 85/84/45 came out 79/76/26 and read neon lime. The grade now keeps only the print's
# tone (its grey response: the contrast, black density and highlight roll-off) and takes the hue from the picture:
# each colour is scaled as a whole by the print's curve on a norm halfway between its luminance and its brightest
# channel (hue and saturation kept, no channel clipped), then given a small, bounded chroma lift.
# Measured on 40 stills of 10 topics: docs/reviews/film-grade-2026-10-29.
FILM_PRINT_LUT = BASE_DIR / "assets" / "luts" / "kodak_2383.cube"
FILM_LUT_DIR = BASE_DIR / "cache" / "luts"
FILM_TONE_VERSION = 1
FILM_TONE_NORM_MIX = 0.5     # 0 = luminance (washes bright warm colours out), 1 = brightest channel (flatter)
FILM_TONE_CHROMA = 1.03      # the print's richness, bounded: more than this grows a warm material into a cast
FILM_PRE_CURVE = "curves=master='0/0.02 0.5/0.50 1/0.98'"   # the pre-LUT black lift and white headroom (unchanged)
_LUMA_709 = (0.2126, 0.7152, 0.0722)


def _read_cube(path: Path):
    """A .cube 3D LUT as an array [b][g][r] -> rgb (red runs fastest in the file)."""
    import numpy as np
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and (s[0].isdigit() or s[0] in ".-"):
            rows.append([float(v) for v in s.split()[:3]])
    a = np.asarray(rows, dtype=np.float64)
    n = round(len(a) ** (1 / 3))
    if n < 2 or n ** 3 != len(a):
        raise ValueError(f"{Path(path).name} is not a 3D LUT")
    return a.reshape(n, n, n, 3)


def hue_safe_print_table(lut, mix: float = FILM_TONE_NORM_MIX, chroma: float = FILM_TONE_CHROMA):
    """The print LUT's tone without its colour: a table of the same size whose greys stay grey (at the print's
    mean grey response) and whose colours keep their hue, with relative chroma changed by at most `chroma`."""
    import numpy as np
    n = lut.shape[0]
    v = np.linspace(0.0, 1.0, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    x = np.stack([r, g, b], axis=-1)
    tone = np.array([lut[i, i, i] for i in range(n)]).mean(axis=1)   # the grey axis, neutralised
    w = np.asarray(_LUMA_709)
    norm = mix * x.max(axis=-1) + (1 - mix) * (x @ w)
    gain = np.where(norm > 1e-6, np.interp(norm, v, tone) / np.maximum(norm, 1e-6), 1.0)
    out = x * gain[..., None]
    out = out / np.maximum(out.max(axis=-1), 1.0)[..., None]   # over white: the whole colour scaled, hue kept
    out[norm <= 1e-6] = tone[0]                                 # black is the print's black, neutral
    y = out @ w
    c = (out - y[..., None]) * chroma
    with np.errstate(divide="ignore", invalid="ignore"):         # the chroma lift shrinks to stay in gamut
        room = np.minimum(np.where(c > 0, (1 - y[..., None]) / c, np.inf),
                          np.where(c < 0, -y[..., None] / c, np.inf)).min(axis=-1)
    out = y[..., None] + c * np.clip(room, 0.0, 1.0)[..., None]
    return np.clip(out, 0.0, 1.0)


def hue_safe_print_lut(source: Path = FILM_PRINT_LUT, out_dir: Optional[Path] = None) -> Path:
    """The hue-safe version of `source` as a .cube file (cached by the source's content and the settings)."""
    import hashlib
    data = Path(source).read_bytes()
    key = hashlib.sha256(data + f"|v{FILM_TONE_VERSION}|{FILM_TONE_NORM_MIX}|{FILM_TONE_CHROMA}".encode()).hexdigest()[:12]
    out = Path(out_dir or FILM_LUT_DIR) / f"{Path(source).stem}_tone_{key}.cube"
    if out.is_file():
        return out
    table = hue_safe_print_table(_read_cube(source))
    n = table.shape[0]
    body = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table.reshape(-1, 3))
    text = (f'TITLE "{Path(source).stem} tone only (hue kept)"\nLUT_3D_SIZE {n}\n'
            f"DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n{body}\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")       # concurrent renders never read half a table
    tmp.write_bytes(text.encode("ascii"))
    os.replace(tmp, out)
    return out


def film_grade_filter(grade_bias: float = 0.0, source: Path = FILM_PRINT_LUT, out_dir: Optional[Path] = None) -> str:
    """The master's film grade: the pre-curve and the hue-safe print LUT (a gentle neutral contrast when the LUT
    is missing or unreadable), then the topic's bounded warm/cool bias."""
    grade = "eq=contrast=1.07:brightness=0.008:saturation=1.03,curves=master='0/0.02 0.2/0.19 0.5/0.51 0.8/0.82 1/0.98'"
    if Path(source).exists():
        try:
            lut = hue_safe_print_lut(source, out_dir)
            lut_str = str(lut.resolve()).replace("\\", "/").replace(":", "\\:")
            grade = f"{FILM_PRE_CURVE},lut3d='{lut_str}'"
        except (OSError, ValueError) as e:
            log.warning("Film print LUT not usable (%s); the neutral contrast grade is used.", e)
    bias = max(-0.05, min(0.05, float(grade_bias or 0.0)))
    if bias:  # the topic's own warmth or coolness, bounded, after the film print
        grade += f",colorbalance=rs={bias:.3f}:bs={-bias:.3f}:rm={bias / 2:.3f}:bm={-bias / 2:.3f}"
    return grade


@lru_cache(maxsize=16)
def _font_vmetrics(name: str, weight: int | None = None) -> tuple[int, int, int]:
    """(unitsPerEm, usWinAscent, usWinDescent) of a bundled font, read from its head and OS/2 tables. libass sizes
    a font so that its Windows ascent plus descent equal the ASS Fontsize (Anton: Fontsize 128 is a 73.8 px em,
    which is why FONT_SIZE_ASS 128 renders the 72 px caption)."""
    import struct
    from .typography import font_file
    data = (FONTS_DIR / font_file(name, weight)).read_bytes()
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        tables[tag] = offset
    upm = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
    win_ascent, win_descent = struct.unpack(">HH", data[tables[b"OS/2"] + 74:tables[b"OS/2"] + 78])
    return upm, win_ascent, win_descent


def ass_font_size(name: str, px: float, weight: int | None = None) -> int:
    """The ASS Fontsize that renders a `px` em of this bundled font under libass."""
    upm, asc, desc = _font_vmetrics(name, weight)
    return round(px * (asc + desc) / upm)


def _ink_band(name: str, px: int, ass_size: int, weight: int | None = None) -> tuple[float, float]:
    """(cap top, descender bottom) of a line of this font, in px from the top of its libass line box."""
    from .typography import get_font
    _, asc, desc = _font_vmetrics(name, weight)
    baseline = asc * ass_size / (asc + desc)
    _, top, _, bottom = get_font(name, px, weight=weight).getbbox("Hgjpy", anchor="ls")
    return baseline + top, baseline + bottom


def _fits_wide(phrase: Any, max_width: int) -> bool:
    """A 16:9 burst fits one line: at most CAPTION_WIDE_MAX_CHARS characters and inside `max_width` (its plate
    included) in Montserrat Bold at CAPTION_WIDE_SIZE."""
    from .typography import get_font
    text = " ".join(_phrase_words(phrase))
    if len(text) > CAPTION_WIDE_MAX_CHARS:
        return False
    return get_font(CAPTION_WIDE_FONT, CAPTION_WIDE_SIZE, weight=700).getlength(text) + 2 * CAPTION_PAD_X <= max_width


def _wrap_cue(words: list[str], max_chars: int = SIDECAR_MAX_LINE_CHARS, max_lines: int = SIDECAR_MAX_LINES) -> str | None:
    """The cue's text in at most `max_lines` lines of at most `max_chars` (two lines balanced, preferring a break
    after punctuation), or None when it does not fit."""
    text = " ".join(words)
    if len(text) <= max_chars:
        return text
    if max_lines < 2:
        return None
    best: tuple[float, str] | None = None
    for k in range(1, len(words)):
        top, bottom = " ".join(words[:k]), " ".join(words[k:])
        if len(top) <= max_chars and len(bottom) <= max_chars:
            score = max(len(top), len(bottom)) - (8 if re.search(r"[\,\;\:\.\?!…—]$", top) else 0)
            if best is None or score < best[0]:
                best = (score, f"{top}\n{bottom}")
    return best[1] if best else None


def partition_sidecar_cues(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """Closed-caption cues for the .srt/.vtt sidecars, (start, end, text): caption review round 2, finding 7. The
    Reel's 2-5 word bursts made staccato YouTube captions; a sidecar cue holds up to SIDECAR_MAX_LINES lines of
    SIDECAR_MAX_LINE_CHARS, is on screen SIDECAR_MIN_CUE to SIDECAR_MAX_CUE seconds, and ends at a sentence (or,
    once it holds a full line, at a clause) where the text allows. Text is the narration's, word for word."""
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        word = str(w.get("word", "")).strip()
        if not word:
            continue
        if cur:
            candidate = [str(x["word"]).strip() for x in cur] + [word]
            if _wrap_cue(candidate) is None or _word_end(w) - float(cur[0]["start"]) > SIDECAR_MAX_CUE:
                groups.append(cur)
                cur = []
        cur.append(w)
        held = _word_end(w) - float(cur[0]["start"])
        text_len = len(" ".join(str(x["word"]).strip() for x in cur))
        if held >= SIDECAR_MIN_CUE and (TERMINAL_PUNCT.search(word) or (CLAUSE_PUNCT.search(word) and text_len >= SIDECAR_MAX_LINE_CHARS)):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    cues: list[tuple[float, float, str]] = []
    for gi, g in enumerate(groups):
        start = float(g[0]["start"])
        end = max(_word_end(g[-1]) + 0.1, start + SIDECAR_MIN_CUE)
        if gi + 1 < len(groups):
            end = min(end, float(groups[gi + 1][0]["start"]))
        end = max(min(end, start + SIDECAR_MAX_CUE), start + 0.02)
        text = _wrap_cue([str(x["word"]).strip() for x in g]) or " ".join(str(x["word"]).strip() for x in g)
        cues.append((start, end, text))
    return cues


class VideoComposer:
    def __init__(self, fonts_dir: Path = FONTS_DIR):
        self.fonts_dir = fonts_dir

    @staticmethod
    def _format_ass_time(sec: float) -> str:
        cs_total = int(round(max(0.0, sec) * 100))
        h, rem = divmod(cs_total, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def _format_srt_time(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _format_vtt_time(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    def export_subtitles_srt(self, words: list[dict[str, Any]], srt_path: Path) -> Path:
        """Export the SubRip (.srt) sidecar for YouTube and Facebook closed captions: its own cues
        (partition_sidecar_cues: up to two 42-character lines, 1.2-7 s each), not the Reel's bursts."""
        cues = partition_sidecar_cues(words)
        lines = [f"{i}\n{self._format_srt_time(s)} --> {self._format_srt_time(e)}\n{t}\n"
                 for i, (s, e, t) in enumerate(cues, start=1)]
        srt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported SRT captions (%d cues) -> %s", len(cues), srt_path.name)
        return srt_path

    def export_subtitles_vtt(self, words: list[dict[str, Any]], vtt_path: Path) -> Path:
        """Export the WebVTT (.vtt) sidecar for web player closed captions: the same cues as the .srt."""
        cues = partition_sidecar_cues(words)
        lines = ["WEBVTT\n"] + [f"{i}\n{self._format_vtt_time(s)} --> {self._format_vtt_time(e)}\n{t}\n"
                                for i, (s, e, t) in enumerate(cues, start=1)]
        vtt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported VTT captions (%d cues) -> %s", len(cues), vtt_path.name)
        return vtt_path

    def build_subtitles(
        self,
        words: list[dict[str, Any]],
        ass_path: Path,
        emphasis: list[str] | None = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        margin_v: int | None = None,
        reconstruction_intervals: list[tuple[float, float]] | None = None,
    ) -> Path:
        """Dynamic running-speech kinetic captions:
        1. Grammatical phrasing: sensitive to punctuation (. ? ! …) so every new sentence starts fresh.
        2. Bursts formed to fit the line at the full caption size, so a Reel's captions keep one size; a burst
           heard for under CAPTION_MIN_CHUNK joins a neighbour (merge_short_bursts).
        3. A plate (the brand's INK, rounded corners, equal margins) behind each burst, fixed for its duration.
        4. Words appear as they are spoken: upcoming words are transparent placeholders holding their place,
           spoken words bone, the script's key terms gold. A burst shows two frames before its first word and
           leaves soon after its last across a pause (burst_timings).
        5. Small Caps typography following the narration's capitals (caption_runs).
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries: one Montserrat Bold line).
        """
        from .typography import get_font
        is_widescreen = width > height
        if is_widescreen:
            eff_margin_v = margin_v if margin_v is not None else round(CAPTION_WIDE_MARGIN_V * height / 1080)
        else:
            eff_margin_v = margin_v if margin_v is not None else CAPTION_MARGIN_V
        max_caption_width = int(width * 0.75) if is_widescreen else MAX_CAPTION_WIDTH
        recon_plate = _brand.ass_colour(_brand.INK, RECON_PLATE_ALPHA)

        if is_widescreen:
            wide_px = round(CAPTION_WIDE_SIZE * height / 1080)
            wide_fs = ass_font_size(CAPTION_WIDE_FONT, wide_px, 700)
            badge_px = round(RECON_SIZE_WIDE * height / 1080)
            badge_fs = ass_font_size(RECON_FONT, badge_px, 600)
            styles = (
                f"Style: CapText,{CAPTION_WIDE_FONT},{wide_fs},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
                f"Style: CapBox,{CAPTION_WIDE_FONT},{wide_fs},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconBadge,{RECON_FONT},{badge_fs},{COL_ACTIVE},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconPlate,{RECON_FONT},{badge_fs},{recon_plate},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            )
        else:
            badge_px = RECON_SIZE_PORTRAIT
            badge_fs = ass_font_size(RECON_FONT, badge_px, 600)
            styles = (
                f"Style: CapText,{FONT_NAME},{FONT_SIZE_ASS},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,0,0,5,0,0,0,1\n"
                f"Style: CapBox,{FONT_NAME},{FONT_SIZE_ASS},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconBadge,{RECON_FONT},{badge_fs},{COL_ACTIVE},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconPlate,{RECON_FONT},{badge_fs},{recon_plate},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            )
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        if is_widescreen:
            def fits(ph: list[dict[str, Any]]) -> bool:
                return _fits_wide(ph, max_caption_width)
            phrases = partition_words_grammatical(words, CAPTION_WIDE_MIN_WORDS, CAPTION_WIDE_TARGET_WORDS,
                                                  CAPTION_WIDE_MAX_WORDS, CAPTION_WIDE_MAX_CHARS, fits=fits,
                                                  glance=(24, CAPTION_WIDE_MAX_CHARS))
            phrases = merge_short_bursts(phrases, fits, CAPTION_WIDE_MAX_WORDS)
        else:
            phrases = merge_short_bursts(partition_words_grammatical(words), _fits_caption_width, MAX_BURST_WORDS)
        timings = burst_timings(phrases)
        key = emphasis_indices(words, emphasis)
        raw_events: list[tuple[int, float, float, str, str]] = []  # (layer, start, end, style, text)

        base = 0
        center_x = width // 2
        for pi, phrase in enumerate(phrases):
            show, hide, reveals = timings[pi]
            has_key = any(base + j in key for j in range(len(phrase)))
            shown = _phrase_words(phrase)

            if is_widescreen:
                font = get_font(CAPTION_WIDE_FONT, wide_px, weight=700)
                text_w = font.getlength(" ".join(_ass_escape(w) for w in shown))
                shrink = min(1.0, (max_caption_width - 2 * CAPTION_PAD_X) / max(1.0, text_w))
                size_tag = f"{{\\fs{int(wide_fs * shrink)}}}" if shrink < 1.0 else ""
                cap_top, desc_bottom = (v * shrink for v in _ink_band(CAPTION_WIDE_FONT, wide_px, wide_fs, 700))
                box_w = round(text_w * shrink + CAPTION_PAD_X * 2)
                box_h = round(desc_bottom - cap_top + CAPTION_PAD_Y * 2)
                box_x = round(center_x - box_w / 2)
                box_y = height - eff_margin_v - box_h
                # \an5 centres the line box (wide_fs tall) on \pos; the letters' ink sits lower in it than its middle
                ink_mid = (cap_top + desc_bottom) / 2 - wide_fs * shrink / 2
                center_y = round(box_y + box_h / 2 - ink_mid)
            else:
                # One size per Reel: bursts are formed to fit at FONT_SIZE; only a single over-long word shrinks.
                fit = _caption_fit_size(phrase, EMPHASIS_POP / 100 if has_key else 1.0, max_width=max_caption_width)
                size_tag = f"{{\\fs{round(fit / 0.5625)}}}" if fit < FONT_SIZE else ""
                # Calculate exact bounding dimensions to render perfectly centered pill background
                text_w, text_h = measure_caption_phrase(phrase, fit, CAPTION_SC_SCALE)
                box_w = round(text_w + CAPTION_PAD_X * 2)
                box_h = round(text_h + CAPTION_PAD_Y * 2)
                box_x = round(center_x - box_w / 2)
                # The pill ends above the band the platforms draw over (eff_margin_v = safe_zones.REEL.bottom):
                # its centre used to sit on that line, so its lower half was under Meta's caption and audio
                # row (measured on the first publication: pills at y 1202-1309 against the line at 1248).
                shift = round(7.5 * (fit / FONT_SIZE))  # Anton's visual centre sits below the geometric one
                center_y = height - eff_margin_v - CAPTION_CLEARANCE - box_h // 2 - shift
                box_y = round(center_y + shift - box_h / 2)

            # Layer 0: the plate, fixed for the whole burst (sized to every word of it, spoken or not)
            path = rounded_rect_path(box_w, box_h, CAPTION_BORDER_RADIUS)
            raw_events.append((0, show, hide, "CapBox", f"{{\\pos({box_x},{box_y})\\p1}}{path}{{\\p0}}"))

            # Layer 1: one event per spoken word; the words still to come are transparent placeholders
            for wi in range(len(phrase)):
                t_start = reveals[wi]
                t_end = reveals[wi + 1] if wi < len(phrase) - 1 else hide
                visible, hidden = [], []
                for ji, disp_word in enumerate(shown):
                    is_key = base + ji in key
                    if ji > wi:
                        colour = COL_FUTURE
                    else:
                        colour = COL_EMPHASIS if is_key else COL_PAST if ji < wi else COL_ACTIVE
                    if is_widescreen:
                        word_ass = f"{{\\c{colour}}}{_ass_escape(disp_word)}"
                    else:
                        word_ass = format_word_caption(disp_word, colour=colour, is_key=is_key,
                                                       is_spoken_now=(ji == wi), has_key=has_key)
                    (hidden if ji > wi else visible).append(word_ass)
                line = " ".join(visible)
                if hidden:
                    line += " " + CAPTION_HIDDEN + " ".join(hidden)
                raw_events.append((1, t_start, t_end, "CapText", f"{{\\pos({center_x},{center_y})\\q2}}{size_tag}{line}"))

            base += len(phrase)

        # The AI-reconstruction disclosure (layer 2 plate, layer 3 text) while a reconstruction is on screen
        if reconstruction_intervals:
            label = "[ HISTORICAL RECONSTRUCTION ]"
            # Bone IBM Plex Mono on an INK plate, top-left: the 5% title-safe corner on 16:9; on 9:16 inside the Meta
            # inset below the Reel's top bar (x 84, y 300). The 9:16 badge was 22px, centred and unplated until the
            # caption review of 2026-11-02 (operator approved moving it).
            scale = height / 1080 if is_widescreen else 1
            pad_x, pad_y = round(14 * scale), round(8 * scale)
            b_top, b_bottom = _ink_band(RECON_FONT, badge_px, badge_fs, 600)
            tw = get_font(RECON_FONT, badge_px, weight=600).getlength(label) + 1.5 * len(label)
            x0, y0 = (round(0.05 * width), round(0.05 * height)) if is_widescreen else (84, 300)
            text_pos = (x0 + pad_x, round(y0 + pad_y - b_top))
            badge_text = f"{{\\an7\\pos({text_pos[0]},{text_pos[1]})}}{label}"
            plate_w, plate_h = round(tw + 2 * pad_x), round(b_bottom - b_top + 2 * pad_y)
            plate_text = f"{{\\pos({x0},{y0})\\p1}}{rounded_rect_path(plate_w, plate_h, 8)}{{\\p0}}"
            for start_t, end_t in reconstruction_intervals:
                raw_events.append((2, start_t, end_t, "ReconPlate", plate_text))
                raw_events.append((3, start_t, end_t, "ReconBadge", badge_text))

        # Sort all events chronologically (layer 0 background pill always appears under layer 1 text)
        raw_events.sort(key=lambda e: (e[1], e[0]))

        lines = [header]
        for layer, ts, te, style, txt in raw_events:
            te_clamped = max(te, ts + 0.02)
            lines.append(
                f"Dialogue: {layer},{self._format_ass_time(ts)},{self._format_ass_time(te_clamped)},{style},,0,0,0,,{txt}"
            )

        ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Wrote %d caption events -> %s", len(raw_events), ass_path.name)
        return ass_path

    @staticmethod
    def _measure(vo_path: Path, bed_path: Path, chain: str) -> Tuple[float, float]:
        """Integrated loudness (LUFS) and true peak (dBTP) of the mixed program."""
        res = _run_ffmpeg(["ffmpeg", "-hide_banner", "-nostats", "-i", str(vo_path), "-i", str(bed_path),
                           "-filter_complex", chain + ",ebur128=peak=true", "-f", "null", "-"])
        summary = res.stderr[res.stderr.rfind("Summary:"):]
        i_match = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
        p_match = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
        if not i_match or not p_match or "inf" in i_match.group(1):
            raise RenderError("Could not measure program loudness (silent audio?).")
        return float(i_match.group(1)), float(p_match.group(1))

    def calibrate_gain(self, vo_path: Path, bed_path: Path, tolerance: float = 0.5, accept: float = 1.0,
                       max_iter: int = 6, target_lufs: float = TARGET_LUFS,
                       target_true_peak: float = TARGET_TRUE_PEAK, pad: float = 0.0,
                       card_at: Optional[float] = None, card_db: float = 0.0) -> float:
        """
        Finds the gain that lands the limited mix on target_lufs (within `tolerance` LU,
        the usual delivery tolerance). The limiter absorbs part of each gain step, so steps
        are scaled up. If it never gets that close, the best result within `accept` LU is
        used with a warning; platforms re-normalize, so that is not worth failing a run.
        """
        from .credits import sha256_file
        # Same voice + bed + targets always converge to the same gain (each pass decodes the full
        # mix), so a re-render (QA re-plan, deep-dive) reuses it instead of re-measuring.
        key = (sha256_file(vo_path), sha256_file(bed_path), tolerance, accept, max_iter, target_lufs, target_true_peak, round(pad, 3),
               card_at, round(card_db, 2))
        cache = self.__dict__.setdefault("_gain_cache", {})
        if all(key[:2]) and key in cache:
            log.info("Mastering: reusing calibrated gain %+.2f dB for unchanged audio", cache[key])
            return cache[key]
        gain = self._calibrate_gain(vo_path, bed_path, tolerance, accept, max_iter, target_lufs, target_true_peak, pad,
                                    card_at, card_db)
        if all(key[:2]):
            cache[key] = gain
        return gain

    def end_card_level(self, vo_path: Path, bed_path: Path, card_at: float, pad: float, gain: float) -> Dict[str, float]:
        """The end card's loudness against the narrated body in the mastered programme, and the correction (dB)
        that sets it END_CARD_BELOW_BODY_LU under the body: the bed's music level differs from run to run, so the
        card's level is measured, not assumed (6.3 to 8.7 LU under the body on three masters, 2026-10-25)."""
        chain = _audio_chain(0, 1, pad) + _master_chain(gain)
        body, _ = self._measure(vo_path, bed_path, chain + f",atrim=end={card_at:.3f}")
        card, _ = self._measure(vo_path, bed_path, chain + f",atrim=start={card_at:.3f},asetpts=PTS-STARTPTS")
        correction = max(-12.0, min(12.0, (body - END_CARD_BELOW_BODY_LU) - card))
        log.info("End card: %.1f LUFS against the body's %.1f; set %+.1f dB", card, body, correction)
        return {"body_lufs": round(body, 1), "card_lufs": round(card, 1), "correction_db": round(correction, 1)}

    def _calibrate_gain(self, vo_path: Path, bed_path: Path, tolerance: float, accept: float,
                        max_iter: int, target_lufs: float, target_true_peak: float, pad: float = 0.0,
                        card_at: Optional[float] = None, card_db: float = 0.0) -> float:
        base = _audio_chain(0, 1, pad, card_at, card_db)
        integrated, _ = self._measure(vo_path, bed_path, base)
        gain = target_lufs - integrated
        best_gain, best_err = gain, float("inf")
        for _ in range(max_iter):
            integrated, peak = self._measure(vo_path, bed_path, base + _master_chain(gain))
            log.info("Mastering: gain %+.2f dB -> %.1f LUFS, %.1f dBTP", gain, integrated, peak)
            if peak > target_true_peak:
                raise RenderError(f"True peak {peak} dBTP exceeds {target_true_peak} after limiting.")
            err = abs(integrated - target_lufs)
            if err < best_err:
                best_gain, best_err = gain, err
            if err <= tolerance + 1e-9:
                return gain
            gain += (target_lufs - integrated) * 1.5
        if best_err <= accept:
            log.warning("Loudness within %.1f LU of %.1f LUFS (not %.1f); using best gain.", best_err, target_lufs, tolerance)
            return best_gain
        raise RenderError(f"Loudness did not converge to {target_lufs} LUFS (best was {best_err:.1f} LU off).")

    @staticmethod
    def _is_portrait_video(path: Path) -> bool:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
                capture_output=True, text=True, timeout=10
            )
            if out.returncode == 0 and out.stdout.strip():
                parts = out.stdout.strip().split("x")
                if len(parts) == 2:
                    w, h = int(parts[0]), int(parts[1])
                    return (w / h) < 1.3
        except Exception:
            pass
        return False


    @staticmethod
    def compute_focal_point(image_path: Path) -> Tuple[float, float]:
        """
        Computes normalized (cx, cy) focal center of mass in [0.15, 0.85] using edge-energy gradient.
        Prevents off-center relics/monuments in wide 16:9 or 3:1 panoramas from getting cropped off
        during 9:16 vertical crop and zoompan moves.
        """
        try:
            with Image.open(image_path) as im:
                small = im.convert("L").resize((128, 128))
                edges = small.filter(ImageFilter.FIND_EDGES)
                pixels = edges.tobytes()
                total_mass = sum(pixels)
                if total_mass == 0:
                    return (0.5, 0.5)

                x_mass = sum((idx % 128 + 0.5) * val for idx, val in enumerate(pixels))
                y_mass = sum((idx // 128 + 0.5) * val for idx, val in enumerate(pixels))

                cx = (x_mass / total_mass) / 128.0
                cy = (y_mass / total_mass) / 128.0
                cx = max(0.15, min(0.85, cx))
                cy = max(0.15, min(0.85, cy))
                return (cx, cy)
        except Exception as e:
            log.debug("Focal point computation fallback for %s: %s", image_path, e)
            return (0.5, 0.5)

    @bounded_encode
    def compose_video(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
        durations: Optional[List[float]] = None,
        emphasis: Optional[List[str]] = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        aspect_ratio: str = "9:16",
        delivery_profile=None,
        grade_bias: float = 0.0,
        end_card: str = "",
        macro_drifts: Optional[List[int]] = None,
    ) -> Path:
        """
        end_card: the share line of the end card appended after the narration (Reels only, src/end_card.py); the
        bed must run on for end_card.SECONDS more (generate_ambient_bed(end_card_at=vo_dur)). Empty: no card.
        grade_bias: a bounded warm (+) / cool (-) shift measured from the topic's hero photograph
        (art direction, 2026-10-24), applied after the film LUT; 0 leaves the grade as it was.
        durations: seconds on screen per scene (e.g. from scene_planner beats). Each cut is
        a short cross-fade centred on the beat boundary. Without durations, scenes share
        the narration equally.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries) with BBC intelligent compositing.
        """
        from .delivery_profiles import get_delivery_profile
        profile = delivery_profile or get_delivery_profile("deep_dive" if width > height else "reel")
        width, height = profile.width, profile.height
        from .checkpoints import file_inputs, fingerprint
        from .credits import sha256_file
        from .typography import FONT_FILES
        source_inputs = file_inputs([*scene_images, vo_path, bed_path])
        font_inputs = file_inputs([self.fonts_dir / filename for filename in FONT_FILES.values()])
        if not all(source_inputs.values()) or not all(font_inputs.values()):
            raise RenderError("Render sources and bundled fonts must exist")

        n = len(scene_images)
        if not durations or len(durations) != n:
            durations = [(vo_dur + TRANS_DUR) / n] * n

        # Historical reconstruction badge intervals
        recon_intervals = []
        cur_t = 0.0
        for i, item in enumerate(scene_images):
            dur = durations[i] if i < len(durations) else (vo_dur / max(1, len(scene_images)))
            p = Path(item)
            is_recon = p.stem.startswith(("gap_gen_", "gen_"))
            if not is_recon:
                try:
                    from .lineage import source_chain
                    chain = source_chain(p)
                    is_recon = any(Path(s.get("path", "")).stem.startswith(("gap_gen_", "gen_")) for s in chain)
                except Exception:
                    pass
            if is_recon:
                recon_intervals.append((cur_t, cur_t + dur))
            cur_t += dur

        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(words, ass_path, emphasis=emphasis, width=width, height=height,
                             reconstruction_intervals=recon_intervals)
        from . import end_card as card_mod
        card = bool((end_card or "").strip()) and width < height
        card_seconds = card_mod.SECONDS if card else 0.0
        self.last_end_card = None

        # Export sidecar SRT and VTT closed captions for YouTube / platform transcript indexing
        srt_path = output_mp4_path.parent / (output_mp4_path.stem + ".srt")
        vtt_path = output_mp4_path.parent / (output_mp4_path.stem + ".vtt")
        self.export_subtitles_srt(words, srt_path)
        self.export_subtitles_vtt(words, vtt_path)

        # Transition k starts half a fade before boundary k so the cut lands on the beat.
        boundaries = [sum(durations[:k + 1]) for k in range(n - 1)]
        offsets = [max(0.0, b - TRANS_DUR / 2) for b in boundaries]
        clip_durs = [d + TRANS_DUR for d in durations]
        clip_durs[-1] += TRANS_DUR  # tail headroom; the output is cut to the narration length
        w2, h2 = width * 2, height * 2
        macro_indices = set(macro_drifts or [])

        input_args: List[str] = []
        filter_parts: List[str] = []
        for i, item in enumerate(scene_images):
            item = Path(item)
            clip_dur = clip_durs[i]
            frames = int(clip_dur * VIDEO_FPS) + 1
            if is_video(item):
                input_args += ["-t", f"{clip_dur:.3f}", "-i", str(item)]
                if width > height and self._is_portrait_video(item):
                    fg_h = int(round(height * 0.94)) // 2 * 2
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,split[bg_in{i}][fg_in{i}];"
                        f"[bg_in{i}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=25:5,eq=brightness=-0.15[bg{i}];"
                        f"[fg_in{i}]scale=-2:{fg_h}[fg{i}];"
                        f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                    )
                elif i == 0 and width < height and is_ai_motion_clip(item):
                    filter_parts.append(hook_clip_filter(i, width, height))
                else:
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                    )
                continue

            input_args += ["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", str(item)]
            if i in macro_indices:
                # Tier 3 Intentional Relic Macro Drift: intimate optical push-in (1.00x -> 1.35x dynamic zoom)
                # into the microscopic craftsmanship and material texture of the relic over an unphotographed beat.
                z, x, y = "min(1.0+0.0025*on,1.35)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif i == 0 or i == n - 1:  # matching forward drift for the loop seam
                z, x, y = "min(pzoom+0.0016,1.24)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif i % 2 == 1:
                z, x, y = "max(1.20-0.0014*on,1.0)", "iw/2-(iw/zoom/2)+sin(on/35)*32", "ih/2-(ih/zoom/2)"
            else:
                z, x, y = "min(pzoom+0.0013,1.18)", "iw/2-(iw/zoom/2)-sin(on/35)*28", "ih/2-(ih/zoom/2)+sin(on/45)*16"

            is_portrait_on_widescreen = False
            if width > height:
                try:
                    with Image.open(item) as im:
                        img_w, img_h = im.size
                        if (img_w / img_h) < 1.3:
                            is_portrait_on_widescreen = True
                except Exception:
                    pass

            if is_portrait_on_widescreen:
                # Cinematic vertical tilt/pan: scale width to 1920 so the tall image fills the screen horizontally,
                # then smoothly pan vertically across the full height with a cinematic cosine ease, revealing 100% of the asset.
                tilt_dir = f"(0.5-0.5*cos(PI*t/{clip_dur:.3f}))" if (i % 2 == 0) else f"(0.5+0.5*cos(PI*t/{clip_dur:.3f}))"
                filter_parts.append(
                    f"[{i}:v]scale={width}:-2,"
                    f"crop={width}:{height}:0:'max(0,min(in_h-out_h,(in_h-out_h)*{tilt_dir}))',"
                    f"fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                )
            else:
                cx, cy = self.compute_focal_point(item)
                crop_x = f"(in_w-out_w)*{cx:.3f}"
                crop_y = f"(in_h-out_h)*{cy:.3f}"
                filter_parts.append(
                    f"[{i}:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2}:{crop_x}:{crop_y},"
                    f"curves=master='0/0.02 0.1/0.11 0.5/0.50 0.9/0.89 1/0.97',"
                    f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={VIDEO_FPS},"
                    f"setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                )

        curr = "[v0]"
        for k in range(n - 1):
            out = f"[x{k}]" if k < n - 2 else "[video_raw]"
            off = round(offsets[k], 3)
            preset_k = preset_for(k, n)
            trans_k = PARALLAX_TO_TRANSITION.get(preset_k, "dissolve")
            # Narrative punctuation: elevate the primary revelation cut (beat 2) to a dramatic punch
            if k == 1 and n >= 4:
                trans_k = "zoomin"
            elif (k + 1) % 4 == 0:
                trans_k = "fadeblack"
            filter_parts.append(f"{curr}[v{k + 1}]xfade=transition={trans_k}:duration={TRANS_DUR}:offset={off}{out}")
            curr = out
        if n == 1:
            filter_parts.append("[v0]null[video_raw]")

        input_args += ["-i", str(vo_path), "-i", str(bed_path)]
        if card:  # the card's sound is the bed running on: a bed made for the narration alone would cut out
            import soundfile as sf
            bed_seconds = sf.info(str(bed_path)).duration
            if bed_seconds < vo_dur + card_seconds - 0.02:
                raise RenderError(f"The ambient bed runs {bed_seconds:.2f}s; the end card needs "
                                  f"{vo_dur + card_seconds:.2f}s (make it with end_card_at).")
        # The whole programme is mastered together: narration, and the card after it with the bed alone.
        gain = self.calibrate_gain(vo_path, bed_path, target_lufs=profile.target_lufs,
                                   target_true_peak=profile.target_true_peak, pad=card_seconds)
        card_at, card_db, card_level = (vo_dur if card else None), 0.0, None
        if card:  # the card's own level, measured in the mastered programme, then the whole mastered again
            card_level = self.end_card_level(vo_path, bed_path, vo_dur, card_seconds, gain)
            card_db = card_level["correction_db"] if abs(card_level["correction_db"]) >= 0.5 else 0.0
            if card_db:
                gain = self.calibrate_gain(vo_path, bed_path, target_lufs=profile.target_lufs,
                                           target_true_peak=profile.target_true_peak, pad=card_seconds,
                                           card_at=card_at, card_db=card_db)
        filter_parts.append(_audio_chain(n, n + 1, card_seconds, card_at, card_db) + _master_chain(gain) + "[a_final]")

        ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts_str = str(self.fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")
        vignette_aspect = "16/9" if width > height else "9/16"
        # Graphics are drawn to their own measured contrast: the lens vignette is lifted while one is on screen,
        # so a graphic can use the frame's full width (2026-11-02; at the edges it read 2.7:1 after grading).
        vignette_enable = graphic_vignette_enable(scene_images, durations)
        # The Kodak 2383 print's tone with the picture's own hue (2026-10-29: the full LUT turned a balanced
        # lamp-lit door neon lime); film_grade_filter, above, has the why and the measurements.
        film_grade = film_grade_filter(grade_bias)
        # The graded picture before the captions, split off for the end card's background (its last frame).
        card_split = "split[v_graded][v_card_src];[v_graded]" if card else ""
        filter_parts.append(
            f"[video_raw]format=yuv420p,{film_grade},format=yuv420p,"
            f"noise=alls=8:allf=t+u,vignette=PI/4.2:aspect={vignette_aspect}{vignette_enable},{card_split}"
            f"subtitles='{ass_str}':fontsdir='{fonts_str}',scale=out_range=tv,format=yuv420p[v_final]"
        )
        if card:
            filter_parts.append(f"[v_card_src]trim=start={max(0.0, vo_dur - 0.2):.3f}:end={vo_dur:.3f}[v_last]")

        encoder_args = _get_video_encoder()
        delivery_args = [
            *encoder_args,
            "-threads", str(os.cpu_count() or 0),
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-r", str(VIDEO_FPS),
            # Meta Reels spec: closed GOP of 2-5 s. Fixed 2 s keyframe interval, no scene-cut keyframes.
            "-g", str(VIDEO_FPS * 2), "-keyint_min", str(VIDEO_FPS * 2), "-sc_threshold", "0", "-flags", "+cgop",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
            "-max_muxing_queue_size", "8192",
            "-movflags", "+faststart",
        ]
        head = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-threads", str(os.cpu_count() or 0),
                *input_args, "-filter_complex", ";".join(filter_parts)]
        log.info("Rendering master -> %s", output_mp4_path.name)
        if not card:
            _run_ffmpeg([*head, "-map", "[v_final]", "-map", "[a_final]", *delivery_args,
                         "-t", f"{vo_dur:.2f}", str(output_mp4_path)])
        else:
            self.last_end_card = self._render_with_end_card(head, delivery_args, end_card, vo_dur, output_mp4_path)
            self.last_end_card["level"] = card_level
        from .encoded_audio import finalize_encoded_audio
        encoded_audio = finalize_encoded_audio(output_mp4_path, profile.target_lufs, profile.target_true_peak)
        if source_inputs != file_inputs([*scene_images, vo_path, bed_path]):
            raise RenderError("Source files changed while rendering")
        self.export_subtitles_srt(words, output_mp4_path.with_suffix(".srt"))
        self.export_subtitles_vtt(words, output_mp4_path.with_suffix(".vtt"))
        import json
        manifest = {"master_sha256": sha256_file(output_mp4_path),
                    "encoded_audio": encoded_audio,
                    "scenes": file_inputs(scene_images), "voiceover": file_inputs([vo_path]),
                    "ambient_bed": file_inputs([bed_path]), "fonts": font_inputs,
                    "captions": file_inputs([ass_path, output_mp4_path.with_suffix(".srt"), output_mp4_path.with_suffix(".vtt")]), "words_fingerprint": fingerprint(words),
                    "width": width, "height": height, "renderer_sha256": sha256_file(Path(__file__)),
                    "end_card": self.last_end_card}
        manifest_path = output_mp4_path.with_suffix(".composition.json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
        log.info("Master rendered: %s (%.2f MB)", output_mp4_path.name, size_mb)
        return output_mp4_path

    @staticmethod
    def _render_with_end_card(head: List[str], delivery_args: List[str], share: str, vo_dur: float,
                              output_mp4_path: Path) -> Dict[str, Any]:
        """The master with the end card after the narration, in two passes. The first renders the narrated body
        (visually lossless), the whole mastered programme (narration, then the bed alone under the card) and the
        graded last frame without captions; the second draws the card over that frame and encodes body and card
        as one delivery file. Returns what the card measured, for the record."""
        from . import end_card as card_mod
        stem = output_mp4_path.parent / output_mp4_path.stem
        body, audio, last = Path(f"{stem}_body.mkv"), Path(f"{stem}_programme.wav"), Path(f"{stem}_card_bg.png")
        total = vo_dur + card_mod.SECONDS
        inter_encoder = _get_intermediate_video_encoder()
        try:
            _run_ffmpeg([*head,
                         "-map", "[v_final]", *inter_encoder, "-pix_fmt", "yuv420p",
                         "-r", str(VIDEO_FPS), "-t", f"{vo_dur:.3f}", str(body),
                         "-map", "[v_last]", "-update", "1", "-frames:v", "1000", str(last),
                         "-map", "[a_final]", "-c:a", "pcm_s24le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                         "-t", f"{total:.3f}", str(audio)])
            stats: Dict[str, Any] = {}
            with Image.open(last) as im:
                background = im.convert("RGB")
            card_frames = card_mod.frames(background, share, stats)
            w, h = card_mod.WIDTH, card_mod.HEIGHT
            cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error",
                   "-i", str(body), "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(card_mod.FPS),
                   "-i", "-", "-i", str(audio),
                   "-filter_complex", "[0:v]setsar=1[b];[1:v]scale=out_range=tv,format=yuv420p,setsar=1[c];"
                                      "[b][c]concat=n=2:v=1:a=0[v]",
                   "-map", "[v]", "-map", "2:a", *delivery_args, "-t", f"{total:.2f}", str(output_mp4_path)]
            errors = output_mp4_path.parent / f"{output_mp4_path.stem}_card_ffmpeg.log"
            with open(errors, "wb") as err:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
                try:
                    for frame in card_frames:
                        proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    pass
                finally:
                    proc.stdin.close()
                try:
                    code = proc.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired as e:
                    proc.kill()
                    raise RenderError(f"ffmpeg did not finish the end card within {FFMPEG_TIMEOUT_SECONDS}s; stopped.") from e
            if code != 0:
                raise RenderError("End card encode failed: " + errors.read_text(encoding="utf-8", errors="replace")[-600:])
            errors.unlink(missing_ok=True)
        finally:
            for path in (body, audio, last):
                path.unlink(missing_ok=True)
        return {"variant": card_mod.VARIANT, "start_s": round(vo_dur, 3), "duration_s": card_mod.SECONDS,
                "text": share, **stats}

    def compose_deep_dive(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
        durations: Optional[List[float]] = None,
        emphasis: Optional[List[str]] = None,
    ) -> Path:
        """Compose a 16:9 widescreen broadcast documentary with BBC blurred-backdrop compositing."""
        return self.compose_video(
            scene_images=scene_images,
            vo_path=vo_path,
            bed_path=bed_path,
            words=words,
            vo_dur=vo_dur,
            output_mp4_path=output_mp4_path,
            durations=durations,
            emphasis=emphasis,
            width=1920,
            height=1080,
            aspect_ratio="16:9",
        )

    @staticmethod
    def splice_single_beat(
        master_mp4: Path,
        beat_index: int,
        replacement_asset: Path,
        durations: List[float],
        output_mp4_path: Path,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
    ) -> Path:
        """
        Micro-patching engine: replace a single beat's visual clip without re-rendering the whole master.
        Keeps the mastered audio stream untouched via copy (-c:a copy).
        Uses GPU hardware acceleration (h264_nvenc) when available to encode the spliced stream in ~2-4s.
        """
        if not master_mp4.is_file():
            raise FileNotFoundError(f"Master video not found: {master_mp4}")
        if beat_index < 0 or beat_index >= len(durations):
            raise ValueError(f"Invalid beat index {beat_index} for {len(durations)} durations")

        t_start = sum(durations[:beat_index])
        beat_dur = durations[beat_index]
        t_end = t_start + beat_dur
        n_beats = len(durations)

        encoder_args = _get_video_encoder()
        delivery_args = [
            *encoder_args,
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            "-c:a", "copy",
            "-movflags", "+faststart",
        ]

        replacement_asset = Path(replacement_asset)
        input_args = ["-i", str(master_mp4)]
        filter_parts = []

        if is_video(replacement_asset):
            input_args += ["-t", f"{beat_dur:.3f}", "-i", str(replacement_asset)]
            filter_parts.append(
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v_mid]"
            )
        else:
            w2, h2 = width * 2, height * 2
            frames = int(beat_dur * VIDEO_FPS) + 1
            z, x, y = "min(pzoom+0.0014,1.20)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            input_args += ["-loop", "1", "-t", f"{beat_dur:.3f}", "-i", str(replacement_asset)]
            filter_parts.append(
                f"[1:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={VIDEO_FPS},"
                f"setsar=1,scale=out_range=tv,format=yuv420p[v_mid]"
            )

        if beat_index == 0:
            filter_parts.append(f"[0:v]trim=start={t_end:.3f},setpts=PTS-STARTPTS[v_post]")
            filter_parts.append("[v_mid][v_post]concat=n=2:v=1:a=0[v_out]")
        elif beat_index == n_beats - 1:
            filter_parts.append(f"[0:v]trim=end={t_start:.3f},setpts=PTS-STARTPTS[v_pre]")
            filter_parts.append("[v_pre][v_mid]concat=n=2:v=1:a=0[v_out]")
        else:
            filter_parts.append(f"[0:v]trim=end={t_start:.3f},setpts=PTS-STARTPTS[v_pre]")
            filter_parts.append(f"[0:v]trim=start={t_end:.3f},setpts=PTS-STARTPTS[v_post]")
            filter_parts.append("[v_pre][v_mid][v_post]concat=n=3:v=1:a=0[v_out]")

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-threads", str(os.cpu_count() or 0),
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[v_out]",
            "-map", "0:a",
            *delivery_args,
            str(output_mp4_path),
        ]
        log.info("Splicing beat %d into master -> %s", beat_index + 1, output_mp4_path.name)
        _run_ffmpeg(cmd)
        return output_mp4_path

    @staticmethod
    def make_review_proxy(master_mp4: Path, proxy_path: Path) -> Path:
        """Small 720p copy for phone review over Telegram (bot uploads are capped near 50 MB). The video
        bitrate is sized to the duration so a 5-minute documentary fits too (1.5 Mbit/s would not)."""
        try:
            seconds = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                                     "-of", "default=noprint_wrappers=1:nokey=1", str(master_mp4)],
                                                    timeout=60).decode().strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            seconds = 60.0
        video_kbps = max(400, min(1500, int(44 * 8 * 1024 / max(seconds, 1)) - 128))
        _run_ffmpeg([
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-threads", str(os.cpu_count() or 0),
            "-i", str(master_mp4),
            "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "28",
            "-threads", str(os.cpu_count() or 0),
            "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy_path),
        ])
        log.info("Review proxy: %s (%.1f MB)", proxy_path.name, os.path.getsize(proxy_path) / (1024 * 1024))
        return proxy_path
