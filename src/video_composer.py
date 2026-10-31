"""
src/video_composer.py
=====================
FFmpeg assembly embodying the locked production standards:
1. Anton 92pt running-speech kinetic captions (gold active, white past, gray upcoming),
   zero-overlap ASS events, Meta UI safe zone (MarginV = 540px / y = 1380px).
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from PIL import Image, ImageDraw, ImageFilter

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
) -> List[List[Dict[str, Any]]]:
    """
    Groups spoken words into readable phrases respecting grammatical boundaries:
    1. Splits strictly at terminal punctuation (. ? ! …) so every new sentence starts fresh.
    2. Dynamic partition ensures phrases use at least min_words (default 2) where possible.
    3. Heavily penalizes ending phrases on orphan prepositions/determiners/conjunctions ('at', 'of', 'though').
    4. Strongly rewards starting new caption blocks on subordinating conjunctions ('though', 'while', 'because').
    5. Respects natural clause punctuation (, ; : - —) and limits line length to avoid margin overflow.
    6. Enforces atomic boundaries to prevent breaking [Name + Roman Numeral] or [Designator + Number].
    """
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
        if n <= min_words:
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
            if min_words <= rem <= max_words and len(rem_txt) <= max_chars and _fits_caption_width(sent[i:]):
                memo[i] = [sent[i:]]
                return memo[i]

            best: Optional[List[List[Dict[str, Any]]]] = None
            best_score = -999999
            candidate_sizes = [target_words, min_words, 4, 5, 6]
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
                        if len(chunk) > 1 and not _fits_caption_width(chunk):
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
                        if 14 <= len(txt) <= 24:
                            score += 40   # Reward optimal glanceable width
                        if score > best_score:
                            best_score = score
                            best = [chunk] + after
            if best is None:
                cut = min(rem, target_words)
                while cut > 1 and not _fits_caption_width(sent[i:i + cut]):
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


def measure_caption_phrase(phrase: Any, fit_rendered: int, sc_scale: int = CAPTION_SC_SCALE) -> Tuple[int, int]:
    """Measures rendered width and height of a caption phrase in pixels."""
    from .typography import get_font
    if isinstance(phrase, str):
        words_list = [{"word": w} for w in phrase.split()]
    else:
        words_list = phrase
    f_base = get_font("Anton", fit_rendered)
    f_sc = get_font("Anton", max(16, int(round(fit_rendered * (sc_scale / 100.0)))))
    space_w = f_base.getlength(" ")
    total_w = 0.0
    for wi, w in enumerate(words_list):
        if wi > 0:
            total_w += space_w
        raw = w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w))
        if wi == len(words_list) - 1:
            raw = re.sub(r"[\,\;\.]+$", "", raw)
        tokens = re.findall(r"[A-Za-z]+|[^A-Za-z]+", raw)
        for tok in tokens:
            if tok.isalpha():
                total_w += f_base.getlength(tok[0].upper())
                if len(tok) > 1:
                    total_w += f_sc.getlength(tok[1:].upper())
            else:
                total_w += f_base.getlength(tok)
    # 75px accounts for ascenders, full caps, and descenders like commas at base FONT_SIZE 76
    text_h = max(30, int(round(75 * (fit_rendered / FONT_SIZE))))
    return int(round(total_w)), text_h


def _caption_fit_size(phrase: Any, scale: float = 1.0, max_width: int = MAX_CAPTION_WIDTH) -> int:
    """Largest font size <= FONT_SIZE at which `phrase` (shown at up to `scale`) fits max_width."""
    from .typography import get_font
    from PIL import Image, ImageDraw
    if isinstance(phrase, str):
        words = phrase.split()
    else:
        words = [w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w)) for w in phrase]
    if words:
        words[-1] = re.sub(r"[\,\;\.]+$", "", words[-1])
    plain = " ".join(words).upper()
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    width = draw.textlength(plain, font=get_font("Anton", FONT_SIZE)) * scale
    if width <= max_width:
        return FONT_SIZE
    return max(CAPTION_MIN_SIZE, int(FONT_SIZE * max_width / width))


CAPTION_MIN_SIZE = 48  # px: the smallest a caption shrinks to; below this it is not readable on a phone
END_CARD_BELOW_BODY_LU = 6.0  # the end card sits this far under the narrated body (the approved prototype: about 6)


def _fits_caption_width(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a burst can be shown inside the caption width at the smallest readable size. A burst that
    cannot would get a pill wider than the area the platforms leave free (safe_zones.REEL), so the phrase
    builder never forms one from more than one word (measured 2026-10-24)."""
    from .typography import get_font
    from PIL import Image, ImageDraw
    words = [w if isinstance(w, str) else (w.get("word", "") if isinstance(w, dict) else str(w)) for w in phrase]
    if words:
        words[-1] = re.sub(r"[\,\;\.]+$", "", words[-1])
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    return draw.textlength(" ".join(words).upper(), font=get_font("Anton", CAPTION_MIN_SIZE)) <= max_width



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
    When enable_small_caps is True, initial letter of alphabetic segments is full size,
    and remaining letters are small capitals (sc_scale%).
    """
    clean = _ass_escape(word)
    if not enable_small_caps:
        return f"{{\\c{colour}\\fscx100\\fscy100}}{clean.upper()}"

    # A possessive or contraction is one word: "Kailasa's" keeps one full-size initial, and the s after the
    # apostrophe is small (the first posted Reel showed KAILASA'S with a full-size S, 2026-10-24).
    tokens = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[^A-Za-z]+", clean)
    out = []
    sc_base = sc_scale
    init_tag = f"\\c{colour}\\fscx100\\fscy100"
    sc_tag = f"\\c{colour}\\fscx{sc_base}\\fscy{sc_base}"

    ACRONYMS = {
        "BC", "AD", "BCE", "CE", "BP", "MYA", "KYA", "US", "USA", "UK", "DNA", "GPS",
        "UNESCO", "CIA", "FBI", "NASA", "USSR", "WWI", "WWII", "AM", "PM", "FT", "KM", "M", "KG"
    }
    ROMAN_NUMERALS = {
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
        "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
        "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX"
    }

    for tok in tokens:
        if tok[:1].isalpha():  # a word, possibly with an apostrophe inside it
            upper = tok.upper()
            if upper in ACRONYMS or upper in ROMAN_NUMERALS or (tok.isupper() and 2 <= len(tok) <= 4):
                out.append(f"{{{init_tag}}}{upper}")
            else:
                first = tok[0].upper()
                rest = tok[1:].upper()
                if rest:
                    out.append(f"{{{init_tag}}}{first}{{{sc_tag}}}{rest}")
                else:
                    out.append(f"{{{init_tag}}}{first}")
        else:
            out.append(f"{{{init_tag}}}{tok}")
    return "".join(out)


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

    def export_subtitles_srt(self, words: List[Dict[str, Any]], srt_path: Path) -> Path:
        """Export standardized SubRip (.srt) sidecar file for YouTube closed caption indexing."""
        phrases = partition_words_grammatical(words)
        lines = []
        for i, phrase in enumerate(phrases, start=1):
            t_start = phrase[0]["start"]
            t_end = phrase[-1]["start"] + phrase[-1].get("dur", 0.3) + 0.1
            text = " ".join(w["word"] for w in phrase).strip()
            lines.append(f"{i}\n{self._format_srt_time(t_start)} --> {self._format_srt_time(t_end)}\n{text}\n")
        srt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported SRT captions (%d cues) -> %s", len(phrases), srt_path.name)
        return srt_path

    def export_subtitles_vtt(self, words: List[Dict[str, Any]], vtt_path: Path) -> Path:
        """Export standardized WebVTT (.vtt) sidecar file for web player closed captions."""
        phrases = partition_words_grammatical(words)
        lines = ["WEBVTT\n"]
        for i, phrase in enumerate(phrases, start=1):
            t_start = phrase[0]["start"]
            t_end = phrase[-1]["start"] + phrase[-1].get("dur", 0.3) + 0.1
            text = " ".join(w["word"] for w in phrase).strip()
            lines.append(f"{i}\n{self._format_vtt_time(t_start)} --> {self._format_vtt_time(t_end)}\n{text}\n")
        vtt_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Exported VTT captions (%d cues) -> %s", len(phrases), vtt_path.name)
        return vtt_path

    def build_subtitles(
        self,
        words: List[Dict[str, Any]],
        ass_path: Path,
        emphasis: Optional[List[str]] = None,
        width: int = VIDEO_WIDTH,
        height: int = VIDEO_HEIGHT,
        margin_v: Optional[int] = None,
        reconstruction_intervals: Optional[List[Tuple[float, float]]] = None,
    ) -> Path:
        """Dynamic running-speech kinetic captions:
        1. Grammatical phrasing: sensitive to punctuation (. ? ! …) so every new sentence starts fresh.
        2. At least three words per caption burst (wherever feasible) with dynamic resolution protection.
        3. Equal margin translucent pill background with subtle rounded corners (vector Bézier curve).
        4. Word-by-word karaoke highlighting (gold key terms with pop, active white, upcoming grey).
        5. Small Caps typography: initial capital anchors with proportioned small capitals for peak legibility.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries).
        """
        is_widescreen = width > height
        eff_margin_v = margin_v if margin_v is not None else (70 if is_widescreen else CAPTION_MARGIN_V)
        max_caption_width = int(width * 0.75) if is_widescreen else MAX_CAPTION_WIDTH

        if is_widescreen:
            header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,Montserrat,38,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0.5,0,1,1.8,1.2,2,0,0,{eff_margin_v},1
Style: CapBox,Montserrat,38,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1
Style: ReconBadge,Montserrat,22,&H40E2DAC8,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,1.5,0,1,0,0,8,0,0,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        else:
            header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,{FONT_NAME},{FONT_SIZE_ASS},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,0,0,5,0,0,0,1
Style: CapBox,{FONT_NAME},{FONT_SIZE_ASS},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: ReconBadge,Montserrat,22,&H40E2DAC8,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,1.5,0,1,0,0,8,0,0,310,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        phrases = partition_words_grammatical(words)
        key = emphasis_indices(words, emphasis)
        raw_events: List[Tuple[int, float, float, str, str]] = []  # (layer, start, end, style, text)

        base = 0

        for pi, phrase in enumerate(phrases):
            if pi < len(phrases) - 1:
                next_start = phrases[pi + 1][0]["start"]
            else:
                dur = phrase[-1].get("dur", phrase[-1].get("end", phrase[-1]["start"] + 0.5) - phrase[-1]["start"])
                next_start = phrase[-1]["start"] + dur + 0.10

            phrase_start = phrase[0]["start"]
            phrase_end = next_start

            # Fit font size to ensure the caption comfortably clears side margins and buttons
            has_key = any(base + j in key for j in range(len(phrase)))
            fit = _caption_fit_size(phrase, EMPHASIS_POP / 100 if has_key else 1.0, max_width=max_caption_width)
            if is_widescreen:
                from .typography import fit_font
                measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
                caption_text = " ".join(w["word"] for w in phrase)
                fit_wide = fit_font(measure_draw, caption_text, "Montserrat", 38, max_caption_width, 28).size
                size_tag = f"{{\\fs{fit_wide}}}" if fit_wide < 38 else ""
            elif fit < FONT_SIZE:
                fit_ass = int(round(fit / 0.5625))
                size_tag = f"{{\\fs{fit_ass}}}"
            else:
                size_tag = ""

            center_x = width // 2
            center_y = height - eff_margin_v

            if not is_widescreen:
                # Calculate exact bounding dimensions to render perfectly centered pill background
                text_w, text_h = measure_caption_phrase(phrase, fit, CAPTION_SC_SCALE)

                box_w = int(round(text_w + CAPTION_PAD_X * 2))
                box_h = int(round(text_h + CAPTION_PAD_Y * 2))
                box_x = int(round(center_x - box_w / 2))
                # The pill ends above the band the platforms draw over (eff_margin_v = safe_zones.REEL.bottom):
                # its centre used to sit on that line, so its lower half was under Meta's caption and audio
                # row (measured on the first publication: pills at y 1202-1309 against the line at 1248).
                shift = int(round(7.5 * (fit / FONT_SIZE)))  # Anton's visual centre sits below the geometric one
                center_y = height - eff_margin_v - CAPTION_CLEARANCE - box_h // 2 - shift
                text_visual_center_y = center_y + shift
                box_y = int(round(text_visual_center_y - box_h / 2))
                path = rounded_rect_path(box_w, box_h, CAPTION_BORDER_RADIUS)

                # Layer 0: Background pill box with subtle rounded corners and exact equal margins
                box_text = f"{{\\pos({box_x},{box_y})\\p1}}{path}{{\\p0}}"
                raw_events.append((0, phrase_start, phrase_end, "CapBox", box_text))

            # Layer 1: Text events with word-by-word karaoke progression and no-wrap protection
            for wi, target in enumerate(phrase):
                t_end = phrase[wi + 1]["start"] if wi < len(phrase) - 1 else next_start
                parts = []
                for ji, w in enumerate(phrase):
                    is_key = base + ji in key
                    disp_word = w["word"]
                    if ji == len(phrase) - 1:
                        disp_word = re.sub(r"[\,\;\.]+$", "", disp_word)

                    if is_widescreen:
                        if ji > wi:
                            c_tag = r"{\c&HCCCCCC&}"
                        elif is_key:
                            c_tag = r"{\c&H00D7D7&}"
                        elif ji == wi:
                            c_tag = r"{\c&HFFFFFF&}"
                        else:
                            c_tag = r"{\c&H999999&}"
                        parts.append(f"{c_tag}{disp_word}")
                    else:
                        if ji > wi:
                            colour = COL_FUTURE
                        else:
                            colour = COL_EMPHASIS if is_key else COL_PAST if ji < wi else COL_ACTIVE
                        word_ass = format_word_caption(
                            disp_word,
                            colour=colour,
                            is_key=is_key,
                            is_spoken_now=(ji == wi),
                            has_key=has_key,
                        )
                        parts.append(word_ass)
                text_content = f"{{\\pos({center_x},{center_y})\\q2}}{size_tag}" + " ".join(parts)
                raw_events.append((1, target["start"], t_end, "CapText", text_content))

            base += len(phrase)

        # Append historical reconstruction badges on layer 2 if present
        if reconstruction_intervals:
            for start_t, end_t in reconstruction_intervals:
                badge_text = r"{\an8\fs22\c&HE2DAC8&\alpha&H40&\b1}[ HISTORICAL RECONSTRUCTION ]"
                raw_events.append((2, start_t, end_t, "ReconBadge", badge_text))

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
        # The Kodak 2383 print's tone with the picture's own hue (2026-10-29: the full LUT turned a balanced
        # lamp-lit door neon lime); film_grade_filter, above, has the why and the measurements.
        film_grade = film_grade_filter(grade_bias)
        # The graded picture before the captions, split off for the end card's background (its last frame).
        card_split = "split[v_graded][v_card_src];[v_graded]" if card else ""
        filter_parts.append(
            f"[video_raw]format=yuv420p,{film_grade},format=yuv420p,"
            f"noise=alls=8:allf=t+u,vignette=PI/4.2:aspect={vignette_aspect},{card_split}"
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
