"""
src/video_composer.py
=====================
FFmpeg assembly embodying the locked production standards:
1. Anton 80 px (72 until 2026-11-03) running-speech kinetic captions on an INK plate: each burst shown at one size, its words
   easing in as they are spoken (upcoming words are transparent placeholders, so the line never moves), bone
   text, gold for the script's key terms only; zero-overlap ASS events inside the Reel safe box
   (safe_zones.REEL: the plate ends above y 1248). 16:9 burns in one still Montserrat Bold line per burst on the
   same plate (shown whole, not word by word).
2. Varied Ken Burns motion (rendered at 2x then downscaled to avoid zoompan jitter)
   with matching forward drift on the opening and closing shots for the loop.
3. Voice cleanup, sidechain ducking of the ambient bed under speech, then measured
   gain + 4x-oversampled brickwall limiting to hit -14 LUFS (EBU R128) with true peak
   below -1.5 dBTP. (Single-filter loudnorm undershoots when the hammer transient makes
   linear mode impossible, and overshoots true peak in its dynamic mode.)
4. H.264 CRF 18 (slow), TV-range yuv420p, AAC 48 kHz, +faststart for streaming.
"""

import json
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
    CAPTION_REEL_PAD_Y_EM,
    CAPTION_REEL_CLEARANCE,
    CAPTION_WORD_EASE_MS,
    CAPTION_PLATE_FADE_IN_MS,
    CAPTION_PLATE_FADE_OUT_MS,
    CAPTION_PLATE_STEPS,
    CAPTION_PLATE_SLACK,
    CAPTION_READ_PER_CHAR,
    CAPTION_READ_BASE,
    CAPTION_MAX_CPS,
    CAPTION_FIRST_FRAME_WHOLE_S,
    CAPTION_CUT_SNAP_FRAMES,
    CAPTION_CUT_MIN_FRAMES,
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
# The on-screen disclosure over a generated illustration, in one place (2026-11-02): it says plainly what the picture
# is (operator decision 2026-11-03; "[ HISTORICAL RECONSTRUCTION ]" until then, the same 29 monospaced characters, so
# the plates keep their width). The covers and the YouTube thumbnail draw this same constant.
RECON_BADGE_LABEL = "AI ILLUSTRATION · NOT A PHOTO"


def _is_reconstruction_scene(item) -> bool:
    p = Path(item)
    if p.stem.startswith(("gap_gen_", "gen_")):
        return True
    try:
        from .lineage import source_chain
        return any(Path(s.get("path", "")).stem.startswith(("gap_gen_", "gen_")) for s in source_chain(p))
    except Exception:  # noqa: BLE001 - an unreadable lineage is no reconstruction claim
        return False


def reconstruction_badge_intervals(scene_images, durations, vo_dur: float) -> list[tuple[float, float]]:
    """(start, end) seconds the reconstruction badge is on screen: each generated illustration's beat, widened by
    TRANS_DUR / 2 on each side so the crossfades into and out of it are covered (2026-11-02, the clip is visible
    from TRANS_DUR / 2 before its cut), clamped at 0 and merged where they touch."""
    out: list[tuple[float, float]] = []
    cur_t = 0.0
    n = max(1, len(scene_images))
    for i, item in enumerate(scene_images):
        dur = durations[i] if durations and i < len(durations) else (vo_dur / n)
        if _is_reconstruction_scene(item):
            start, end = max(0.0, cur_t - TRANS_DUR / 2), cur_t + dur + TRANS_DUR / 2
            if out and start <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], end))
            else:
                out.append((start, end))
        cur_t += dur
    return out
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


def loop_motion(scene_images: List[Any], durations: List[float], vo_dur: float, macro_indices: Set[int],
                width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> Dict[str, float]:
    """The push the loop bridge carries through the seam (end_card.loop_bridge's close_zoom, close_push and
    open_push), read from how compose_video moves the closing and opening shots: a still's capped forward drift
    (end_card.LOOP_PUSH to LOOP_PUSH_CAP, end_card.shot_drift), a macro drift (0.0025 a frame to 1.35), the AI
    hook clip's push (LOOP_PUSH), or none for a video (a parallax or footage clip moves by itself). The closing
    shot's zoom is taken at the narration's end, its frame count from where its clip starts (its crossfade)."""
    from . import end_card as card_mod
    n = len(scene_images)
    if not n:
        return {"close_zoom": 1.0, "close_push": 0.0, "open_push": 0.0}
    boundaries = [sum(durations[:k + 1]) for k in range(n - 1)]
    start = max(0.0, boundaries[-1] - TRANS_DUR / 2) if n > 1 else 0.0
    frames = max(1, round((vo_dur - start) * VIDEO_FPS))

    def rate(i: int) -> tuple[float, float]:
        item = Path(scene_images[i])
        if is_video(item):
            hook = i == 0 and width < height and is_ai_motion_clip(item)
            return (card_mod.LOOP_PUSH, card_mod.LOOP_PUSH_CAP) if hook else (0.0, 1.0)
        if i in macro_indices:
            return 0.0025, 1.35
        return card_mod.LOOP_PUSH, card_mod.LOOP_PUSH_CAP

    close_rate, close_cap = rate(n - 1)
    close_zoom, close_push = (card_mod.shot_drift(frames, close_rate, close_cap) if close_rate else (1.0, 0.0))
    return {"close_zoom": round(close_zoom, 6), "close_push": round(close_push, 6), "open_push": rate(0)[0]}


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


from .safe_zones import REEL as _REEL_UI


@lru_cache(maxsize=16)
def _font_vmetrics(name: str, weight: int | None = None) -> tuple[int, int, int]:
    """(unitsPerEm, usWinAscent, usWinDescent) of a bundled font, read from its head and OS/2 tables. libass sizes
    a font so that its Windows ascent plus descent equal the ASS Fontsize (Anton: Fontsize 128 is a 73.8 px em,
    which is why FONT_SIZE_ASS 128 rendered the 72 px caption and 142 renders the 80 px one)."""
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


@lru_cache(maxsize=32)
def _libass_face(path: Path) -> tuple[frozenset[str], int] | None:
    """What libass (0.17) sees of a bundled font file: the names it matches an ASS Fontname against (name ID 1, the
    legacy family, plus ID 4, the full name, and ID 6, the PostScript name; lower-cased) and the OS/2 weight of face
    0. libass reads only face 0 of a file and sets no variation axes, so a variable font is its default instance:
    Montserrat-Variable.ttf is "Montserrat Thin" at weight 100, and "Montserrat" is not one of its names (round 3:
    the 16:9 captions drew in the system fallback, Inter). None for a file that is not a font."""
    import struct
    try:
        data = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            tables[tag] = offset
        name_at, os2_at = tables[b"name"], tables[b"OS/2"]
    except (OSError, struct.error, KeyError):
        return None
    count, strings = struct.unpack(">HH", data[name_at + 2:name_at + 6])
    names: set[str] = set()
    for r in range(count):
        platform, _, _, name_id, length, offset = struct.unpack(
            ">HHHHHH", data[name_at + 6 + 12 * r:name_at + 18 + 12 * r])
        if name_id not in (1, 4, 6):
            continue
        raw = data[name_at + strings + offset:name_at + strings + offset + length]
        text = raw.decode("utf-16-be", "replace") if platform in (0, 3) else raw.decode("latin-1")
        names.add(text.strip().lower())
    weight = struct.unpack(">H", data[os2_at + 4:os2_at + 6])[0]
    return frozenset(names), weight


def libass_font_problems(fonts_dir: Path, requests: list[tuple[str, int]]) -> list[str]:
    """For each (ASS Fontname, weight) the captions draw, why libass would not draw it from the bundled fonts: no
    bundled face carries that name (libass then takes a system font: the line no longer matches the plate measured
    in the bundled font), or the nearest bundled face of that name is more than 150 lighter than asked (libass
    thickens it synthetically: not the designed weight). Empty when every request resolves."""
    faces = [face for p in sorted(Path(fonts_dir).glob("*")) if p.suffix.lower() in (".ttf", ".otf")
             and (face := _libass_face(p)) is not None]
    problems: list[str] = []
    for fontname, weight in requests:
        weights = [w for names, w in faces if fontname.strip().lower() in names]
        if not weights:
            problems.append(f"no bundled font is named {fontname!r} for libass (it falls back to a system font)")
        elif min(abs(w - weight) for w in weights) > 150:
            problems.append(f"the bundled {fontname!r} faces weigh {sorted(weights)}, not {weight} (libass draws a "
                            "synthetic weight)")
    return problems


def reel_render_px(fit: float) -> float:
    """The Anton em libass draws for a 9:16 caption of nominal size `fit`: its ASS size (FONT_SIZE_ASS, or
    fit / 0.5625 for a shrunk word) times unitsPerEm / (winAscent + winDescent). About 2.6% over `fit`; until the
    round-3 captions change (2026-11-02) the plate was measured at `fit`, a little narrower than the drawn line."""
    upm, asc, desc = _font_vmetrics(FONT_NAME)
    ass_size = FONT_SIZE_ASS if fit >= FONT_SIZE else fit / 0.5625
    return ass_size * upm / (asc + desc)


def reel_cap_height(fit: float) -> float:
    """The drawn cap height in px of a 9:16 caption at nominal size `fit`."""
    from .typography import get_font
    return float(-get_font(FONT_NAME, max(8, round(reel_render_px(fit)))).getbbox("H", anchor="ls")[1])


# The 9:16 plate's height follows the type (round 3: CAPTION_REEL_PAD_Y_EM of the cap height above and below the
# capitals); its sides keep CAPTION_PAD_X, as does the 16:9 plate.
REEL_LETTER_SPACING = 0.5   # the 9:16 CapText style's Spacing (px at PlayRes)
REEL_CAP_H = reel_cap_height(FONT_SIZE)
REEL_PAD_X = CAPTION_PAD_X
REEL_PAD_Y = round(CAPTION_REEL_PAD_Y_EM * REEL_CAP_H)
REEL_PLATE_H = round(REEL_CAP_H + 2 * REEL_PAD_Y)   # one plate height per Reel

# The widest caption text, centred: its pill (text plus padding) fills at most the width every Reel platform
# leaves free (safe_zones.REEL.centred_width, 696 px: Shorts' icon column takes the right 192 px). 640 px.
MAX_CAPTION_WIDTH = _REEL_UI.centred_width() - 2 * REEL_PAD_X


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
    "feet", "ft", "meters", "meter", "metres", "metre", "m", "kilometres", "kilometre", "km", "miles", "mile",
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
    # Relative Pronouns (and the wh- words that open an indirect question: OR WHY | THEY STOPPED, review 2026-11-03)
    "which", "who", "whom", "whose", "where", "when", "why", "how",
    # Copulas & Auxiliaries (dangling verb tails before predicates)
    "is", "was", "are", "were", "been", "being",
}
# Words that carry no content of their own (review, 2026-11-03: OF THE was a burst at 80 px): a burst made of these
# alone is never formed where another split fits. The dangling tails (articles, determiners, prepositions,
# conjunctions, relative words, copulas) plus the auxiliaries. Personal pronouns are left out: a subject and its
# verb (IT WAS, THEY HAD) reads as a burst, and tests/test_caption_emphasis.py pins IT WAS | HIDDEN UNDERGROUND.
# The tails no burst ends on mid-sentence where another split fits (review, 2026-11-03): articles and determiners,
# prepositions, conjunctions and the relative and wh- words that open a clause. A copula is no such tail (IT WAS |
# HIDDEN UNDERGROUND reads; it keeps DANGLING_PENALTY in the burst's own score).
DANGLING_ENDS = DANGLING_TAILS - {"is", "was", "are", "were", "been", "being"}
FUNCTION_WORDS = DANGLING_TAILS | {
    "be", "has", "had", "have", "do", "did", "does", "will", "would", "can", "could", "may", "might", "must",
    "shall", "should", "not",
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



def _bare(word: str) -> str:
    return re.sub(r"[^\w]", "", word).lower()


def _function_only(chunk: List[Dict[str, Any]]) -> bool:
    """A burst made only of FUNCTION_WORDS (OF THE, AND THEY)."""
    words = [_bare(w.get("word", "")) for w in chunk]
    return bool(words) and all(w in FUNCTION_WORDS for w in words)


def _split_defects(split: List[List[Dict[str, Any]]]) -> int:
    """How badly a sentence's split (its bursts in order, the last ending the sentence) reads: 2 for each burst
    of function words only, 1 for each burst but the last ending on a DANGLING_ENDS word (an article, determiner,
    preposition, conjunction or relative word). A sentence's own last word is no dangling end: nothing follows it."""
    defects = 0
    for k, chunk in enumerate(split):
        if _function_only(chunk):
            defects += 2
        elif k < len(split) - 1 and _bare(chunk[-1].get("word", "")) in DANGLING_ENDS:
            defects += 1
    return defects


def _split_lones(split: List[List[Dict[str, Any]]]) -> int:
    """Lone-word bursts in a sentence's split of more than one burst."""
    return sum(len(chunk) == 1 for chunk in split) if len(split) > 1 else 0


def partition_words_grammatical(
    words: List[Dict[str, Any]],
    min_words: int = MIN_BURST_WORDS,
    target_words: int = TARGET_BURST_WORDS,
    max_words: int = MAX_BURST_WORDS,
    max_chars: int = MAX_BURST_CHARS,
    fits: Callable[[list[dict[str, Any]]], bool] | None = None,
    glance: tuple[int, int] = (14, 24),
    prefer: Callable[[list[dict[str, Any]]], bool] | None = None,
    bonds: set[int] | None = None,
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
    `bonds` (round 4) are word positions i whose boundary with word i + 1 no burst may split: by default
    caption_bonds(words), a hedge with its figure (ROUGHLY | 280) and both ends of a range (94 | TO | 104).
    """
    if bonds is None:
        bonds = caption_bonds(words)
    # A burst that cannot be cut into bursts of min_words or more without splitting a bond (BETWEEN 94 AND 104
    # METRES) may shrink further with the Reel defaults, to CAPTION_BOND_MIN_SIZE (item 2, 2026-11-03: at 80 px it
    # no longer fits at CAPTION_BURST_MIN_SIZE, so the figure was split from its range). A caller's own `fits` is used
    # as is.
    fits_bonded = fits
    if fits is None:
        fits = _fits_caption_burst
        fits_bonded = _fits_caption_bonded
        prefer = prefer or _fits_caption_width
    prefer = prefer or fits
    sentences: List[List[Dict[str, Any]]] = []
    offsets: list[int] = []
    curr: List[Dict[str, Any]] = []
    for wi, w in enumerate(words):
        if not curr:
            offsets.append(wi)
        curr.append(w)
        if TERMINAL_PUNCT.search(w.get("word", "")) and not _is_abbreviation(w.get("word", "")):
            sentences.append(curr)
            curr = []
    if curr:
        sentences.append(curr)

    def _partition_sentence(sent: List[Dict[str, Any]], off: int) -> List[List[Dict[str, Any]]]:
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
            best_score: tuple[int, int, int] = (-999999, -999999, -999999)
            candidate_sizes = [target_words, min_words, 4, 5, 6] + list(range(7, max_words + 1))
            # never past max_words (round 3: sizes 4-6 were tried whatever the cap, so a 4-word cap formed 5)
            candidate_sizes = [c for c in candidate_sizes if c <= max_words] or [min(target_words, rem)]
            # round 4: past max_words only through bonded words (BETWEEN 94 AND 104 METRES is one burst), at most 2
            candidate_sizes += [c for c in (max_words + 1, max_words + 2)
                                if all(off + i + k - 1 in bonds for k in range(max_words, c))]
            # A lone content word is a candidate too (review, 2026-11-03): it costs LONE_WORD_PENALTY, so it is
            # only formed where every split of min_words or more has more defects (_split_defects), e.g.
            # BUILT | UNDER KING KRISHNA I rather than BUILT UNDER | KING KRISHNA I.
            if min_words > 1 and not _function_only(sent[i:i + 1]):
                candidate_sizes.append(1)
            for size in candidate_sizes:
                if i + size <= n:
                    if i + size < n and (off + i + size - 1 in bonds or _is_atomic_boundary(
                            sent[i + size - 1].get("word", ""), sent[i + size].get("word", ""))):
                        continue
                    rem_after = n - (i + size)
                    if rem_after > 0 and rem_after < min_words and (
                            rem_after > 1 or _function_only(sent[n - 1:])):
                        continue
                    after = _solve(i + size)
                    if after is not None:
                        chunk = sent[i:i + size]
                        if len(chunk) > 1 and not fits(chunk) and not (
                                all(off + i + k in bonds for k in range(min_words - 1, size - 1))
                                and fits_bonded(chunk)):
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
                            score -= DANGLING_PENALTY  # dangling prepositions/determiners/conjunctions at block ends
                        if _function_only(chunk):
                            score -= FUNCTION_ONLY_PENALTY
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
                        if after and len(after[0]) == 1 and len(after) > 1:
                            # the rest could only start on a lone word (2026-11-03, 80 px: THEY REMOVED | AN |
                            # ESTIMATED 200,000 TONS); less than ending this burst on a dangling word
                            # (DANGLING_PENALTY; review 2026-11-03: at 250 against 200 it formed THEY REMOVED AN)
                            score -= LONE_WORD_PENALTY
                        if size == 1 and n > 1:
                            score -= LONE_WORD_PENALTY   # this burst is the lone word
                        elif rem_after == 1 and min_words > 1:
                            score -= LONE_WORD_PENALTY   # the sentence would end on a lone word
                        # The split's defects come first (review, 2026-11-03): a burst of function words only, or
                        # one ending on an article, preposition or conjunction mid-sentence, anywhere in the
                        # sentence's rest, so no split that has fewer is passed over for this burst's own score
                        # (BUILT UNDER | KING KRISHNA I | OF THE | ..., WHO CARVED | THE STATUES OR WHY | ...).
                        # Then the fewest lone words: a lone content word only where it saves a defect.
                        split = [chunk] + after
                        key = (-_split_defects(split), -_split_lones(split), score)
                        if key > best_score:
                            best_score = key
                            best = split
            if best is None:
                def _held(c: int) -> bool:   # the cut after c words splits a bond or an atomic pair (KRISHNA | I)
                    return i + c < n and (off + i + c - 1 in bonds or _is_atomic_boundary(
                        sent[i + c - 1].get("word", ""), sent[i + c].get("word", "")))

                def _cut(keep_bonds: bool) -> int:
                    c = min(rem, target_words)
                    # shorter bursts rather than a pill wider than the safe width (or, where it can, a split bond or
                    # atomic pair: at 80 px, 2026-11-03, BUILT UNDER KING KRISHNA I no longer fits one line)
                    while c > 1 and (not fits(sent[i:i + c]) or (keep_bonds and _held(c))):
                        c -= 1
                    return c
                cut = _cut(True)
                if _held(cut):
                    cut = _cut(False)
                best = [sent[i:i + cut]] + (_solve(i + cut) or [sent[i + cut:]])
            memo[i] = best
            return best

        res = _solve(0)
        return res if res is not None else [sent]

    all_phrases: List[List[Dict[str, Any]]] = []
    for s, off in zip(sentences, offsets):
        for p in _partition_sentence(s, off):
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


def _caption_text_width(words: list[str], size: int, sc_scale: int = CAPTION_SC_SCALE, drawn: bool = False) -> float:
    """Width in px of these words at caption size `size`, with the caption's small capitals.

    The burst builder measures at `size` (the baseline tests on origin/main pin the bursts that measure forms).
    With `drawn` it is the width libass actually draws: at its em (reel_render_px, about 2.6% over `size`) plus the
    CapText style's letter spacing; the plate is sized from that (round 3: it was the narrower measure, so the
    plate's side padding shrank to about 11 px on a full line)."""
    from .typography import get_font
    px = reel_render_px(size) if drawn else size
    f_base = get_font("Anton", max(8, round(px)))
    f_sc = get_font("Anton", max(16, round(px * (sc_scale / 100.0))))
    total = REEL_LETTER_SPACING * len(" ".join(words)) if drawn else 0.0
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
    total_w = _caption_text_width(_phrase_words(phrase), fit_rendered, sc_scale, drawn=True)
    # The capitals' drawn height: the line is upper case (full and small capitals), so the plate is padded around
    # the cap band and the letters sit optically centred (round 3; was a fixed 75 px whatever the size).
    text_h = max(30, round(reel_cap_height(fit_rendered)))
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


CAPTION_BURST_MIN_SIZE = round(0.92 * FONT_SIZE)  # px: a multi-word burst never shows below this (74 at 80 px)
# px: a burst no cut can divide without splitting a bond (partition_words_grammatical) may shrink to this instead, rather than split a figure from its hedge or range (2026-11-03): 66 at 80 px, the floor every burst had at 72 px, so no caption draws
# smaller than it could before the size change. _caption_fit_size shrinks such a burst as far as it must.
CAPTION_BOND_MIN_SIZE = round(0.825 * FONT_SIZE)
LONE_WORD_PENALTY = 250     # the phrase builder's cost of a split that leaves the next burst a lone word mid-sentence
SHRUNK_BURST_PENALTY = 150  # the phrase builder's cost of a burst below full size: more than a short burst,
                            # less than one ending on a dangling article or preposition (DANGLING_PENALTY)
# Review, 2026-11-03: a dangling end costs more than a lone content word (it was 200 against LONE_WORD_PENALTY's
# 250, so THEY REMOVED AN | ESTIMATED 200,000 TONS won over THEY REMOVED | AN ESTIMATED 200,000 TONS), and more
# than a lone word and a shrunk burst together. A burst of function words only (OF THE) costs more still.
DANGLING_PENALTY = 450
FUNCTION_ONLY_PENALTY = 600
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


def _fits_caption_bonded(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a burst no cut can divide without splitting a bond (a hedge or range with its figure) fits at
    CAPTION_BOND_MIN_SIZE (partition_words_grammatical)."""
    return _caption_text_width(_phrase_words(phrase), CAPTION_BOND_MIN_SIZE) <= max_width


def format_word_caption(
    word: str,
    colour: str,
    is_key: bool,
    is_spoken_now: bool,
    has_key: bool,
    pop_val: int = EMPHASIS_POP,
    sc_scale: int = CAPTION_SC_SCALE,
    enable_small_caps: bool = CAPTION_SMALL_CAPS,
    ease_ms: int = 0,
) -> str:
    """Formats a word for ASS kinetic subtitles.
    When enable_small_caps is True, the word is upper-cased in runs (caption_runs): full size where the narration
    has a capital (the initial of a capitalised word, an acronym), small capitals (sc_scale%) elsewhere. Stripping
    the override tags leaves the narration's word, upper-cased.
    With `ease_ms` > 0 the word being spoken now eases in from transparent over that many ms from the start of its
    event (round 3: it hard-cut on). Only that word: words already on the line never fade again, and no size pop
    (a \\fscx pop on one word of a centred line reflows the whole line).
    """
    clean = _ass_escape(word)
    ease = f"{{\\alpha&HFF&\\t(0,{int(ease_ms)},\\alpha&H00&)}}" if is_spoken_now and ease_ms > 0 else ""
    if not enable_small_caps:
        return f"{ease}{{\\c{colour}\\fscx100\\fscy100}}{clean.upper()}"
    init_tag = f"\\c{colour}\\fscx100\\fscy100"
    sc_tag = f"\\c{colour}\\fscx{sc_scale}\\fscy{sc_scale}"
    return ease + "".join(f"{{{init_tag if full else sc_tag}}}{text}" for text, full in caption_runs(clean, sc_scale))


def _word_end(w: dict[str, Any]) -> float:
    """When a timed word stops being heard: its duration if given, else its end, else 0.3 s after its start."""
    start = float(w["start"])
    if w.get("dur") is not None:
        return start + float(w["dur"])
    if w.get("end") is not None:
        return float(w["end"])
    return start + 0.3


def reading_need(phrase: Any) -> float:
    """Seconds a burst wants on screen to be read (round 3 reading-rate guard): CAPTION_READ_PER_CHAR a shown
    character plus CAPTION_READ_BASE."""
    return len(" ".join(_phrase_words(phrase))) * CAPTION_READ_PER_CHAR + CAPTION_READ_BASE


def _on_screen_estimate(out: list[list[dict[str, Any]]], i: int, pause_gap: float, pause_tail: float) -> float:
    """How long burst `i` would stay up (burst_timings, without the lead): to the next burst's first word when the
    next follows within `pause_gap`, else `pause_tail` after its own last word (0.10 s for the last burst)."""
    p = out[i]
    first, last_end = float(p[0]["start"]), _word_end(p[-1])
    if i + 1 < len(out):
        next_first = float(out[i + 1][0]["start"])
        return next_first - first if next_first - last_end <= pause_gap else last_end + pause_tail - first
    return last_end + 0.10 - first


def merge_short_bursts(
    phrases: list[list[dict[str, Any]]],
    fits: Callable[[list[dict[str, Any]]], bool],
    max_words: int,
    min_dur: float = CAPTION_MIN_CHUNK,
    pause_gap: float = CAPTION_PAUSE_GAP,
    reading_rate: bool = True,
    pause_tail: float = CAPTION_PAUSE_TAIL,
) -> list[list[dict[str, Any]]]:
    """Joins each burst heard for less than `min_dur` to a neighbour (round 2, finding 4: a flash is not read),
    preferring the neighbour in the same sentence, then the one before. A join must still fit the line, keep to
    `max_words` and not bridge a pause longer than `pause_gap`; a burst with no such neighbour stays as it is.
    With `reading_rate` (round 3), a burst whose time on screen is under reading_need joins a neighbour the same
    way (burst_timings then holds one that cannot into the following pause)."""
    out = [list(p) for p in phrases if p]
    i = 0
    while i < len(out):
        p = out[i]
        heard_ok = _word_end(p[-1]) - float(p[0]["start"]) >= min_dur
        read_ok = not reading_rate or _on_screen_estimate(out, i, pause_gap, pause_tail) >= reading_need(p)
        if len(out) == 1 or (heard_ok and read_ok):
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
    first_frame_whole: float | None = None,
) -> list[tuple[float, float, list[float]]]:
    """(show, hide, word reveal times) for each burst (round 2, finding 4). A burst appears `lead` (two frames)
    before its first word is heard; each word appears the same lead before its own onset. It stays until the next
    burst appears, except across a pause longer than `pause_gap`, where it leaves `pause_tail` after its last word.
    The last burst leaves 0.10 s after its last word. No two bursts overlap. A burst that would be up for less
    than reading_need holds on into the following pause, never past the next burst's appearance (round 3); one
    still faster than CAPTION_MAX_CPS characters a second is logged.
    With `first_frame_whole` (round 4; compose_video passes CAPTION_FIRST_FRAME_WHOLE_S for 9:16), a first burst that would
    appear that soon or sooner is shown whole from 0.0 (every reveal at 0.0): frame 0 carries the hook line."""
    out: list[tuple[float, float, list[float]]] = []
    prev_hide = 0.0
    for pi, phrase in enumerate(phrases):
        first = float(phrase[0]["start"])
        last_end = _word_end(phrase[-1])
        show = max(0.0, first - lead, prev_hide)
        reveals = [show]
        for w in phrase[1:]:
            reveals.append(max(reveals[-1], float(w["start"]) - lead))
        if pi == 0 and first_frame_whole is not None and first - lead <= first_frame_whole + 1e-9:
            show, reveals = 0.0, [0.0] * len(phrase)
        if pi + 1 < len(phrases):
            next_first = float(phrases[pi + 1][0]["start"])
            next_show = max(0.0, next_first - lead)
            hide = last_end + pause_tail if next_first - last_end > pause_gap else next_show
            hide = min(hide, next_show)
        else:
            next_show = None
            hide = last_end + 0.10
        need = reading_need(phrase)
        if hide - show < need:
            hide = show + need if next_show is None else max(hide, min(show + need, next_show))
        hide = max(hide, reveals[-1] + 0.02)
        chars = len(" ".join(_phrase_words(phrase)))
        if hide > show and chars / (hide - show) > CAPTION_MAX_CPS:
            log.info("Caption burst at %.2f s reads at %.1f characters a second (over %.0f): %s", show,
                     chars / (hide - show), CAPTION_MAX_CPS, " ".join(_phrase_words(phrase)))
        out.append((show, hide, reveals))
        prev_hide = hide
    return out


def caption_cut_times(durations: list[float]) -> list[float]:
    """The picture cuts (s): the boundaries between scenes, as caption_graphic_intervals and graphic_vignette_enable
    read them (a crossfade is centred on its boundary)."""
    cuts, t = [], 0.0
    for dur in durations[:-1]:
        t += dur
        cuts.append(t)
    return [c for c in cuts if c > 0]


def snap_to_cuts(
    phrases: list[list[dict[str, Any]]],
    timings: list[tuple[float, float, list[float]]],
    cuts: list[float] | None,
    fps: float = VIDEO_FPS,
    snap_frames: int = CAPTION_CUT_SNAP_FRAMES,
    min_frames: int = CAPTION_CUT_MIN_FRAMES,
) -> list[tuple[float, float, list[float]]]:
    """burst_timings with each burst's show and hide moved onto a picture cut 1 to `snap_frames` frames away (round
    4, cue-to-cut: no plate change a frame or two off a cut). Two bursts heard back to back share one boundary, and
    it moves as one. A move must leave every burst it touches on screen for its reading_need (or no less than it
    had), keep the bursts in order, never overlap and keep each burst's last word up; where snapping would not, a
    boundary nearer than `min_frames` frames moves out to `min_frames` from the cut instead, else stays. Without
    cuts the timings come back unchanged."""
    if not cuts or not timings:
        return timings
    frame = 1.0 / fps
    rows = [[show, hide, list(rev)] for show, hide, rev in timings]
    n = len(rows)
    first_words = [float(p[0]["start"]) for p in phrases]

    def _valid(cand: list[list[Any]], touched: list[int]) -> bool:
        for j in touched:
            show, hide, rev = cand[j]
            old = timings[j][1] - timings[j][0]
            if show < 0 or hide - show < min(reading_need(phrases[j]), old) - 1e-9 or hide < rev[-1] + 0.02 - 1e-9:
                return False
            if show > first_words[j] + 1e-9:   # never after its first word is heard
                return False
            if j > 0 and show < cand[j - 1][1] - 1e-9:
                return False
            if j + 1 < n and hide > cand[j + 1][0] + 1e-9:
                return False
        return True

    def _moved(cand: list[list[Any]], j: int, show: float | None = None, hide: float | None = None) -> None:
        if show is not None:
            # the first word appears with the plate; later words keep their own times, never before it
            cand[j][0] = show
            cand[j][2] = [show] + [max(show, r) for r in cand[j][2][1:]]
        if hide is not None:
            cand[j][1] = hide

    # the boundaries: (time, burst whose show it is or None, burst whose hide it is or None)
    bounds: list[tuple[int | None, int | None]] = []
    for j in range(n):
        joined = j > 0 and abs(rows[j - 1][1] - rows[j][0]) < 1e-6
        if not joined:
            bounds.append((j, None))
        bounds.append((j + 1 if j + 1 < n and abs(rows[j][1] - rows[j + 1][0]) < 1e-6 else None, j))
    for show_of, hide_of in bounds:
        t = rows[show_of][0] if show_of is not None else rows[hide_of][1]
        cut = min(cuts, key=lambda c: abs(c - t))
        dist = abs(cut - t)
        if dist < 1e-6 or dist > snap_frames * frame + 1e-6:
            continue
        touched = [j for j in (hide_of, show_of) if j is not None]
        targets = [cut]
        if dist < min_frames * frame - 1e-6:
            targets.append(cut + (min_frames * frame if t > cut else -min_frames * frame))
        for target in targets:
            cand = [[r[0], r[1], list(r[2])] for r in rows]
            if show_of is not None:
                _moved(cand, show_of, show=target)
            if hide_of is not None:
                _moved(cand, hide_of, hide=target)
            if _valid(cand, touched):
                rows = cand
                break
    return [(r[0], r[1], r[2]) for r in rows]


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def emphasis_indices(words: List[Dict[str, Any]], terms: Optional[List[str]]) -> Set[int]:
    """Word positions to emphasise: the first occurrence of each key term (multi-word terms
    match as a sequence). Without terms, the first two words containing a digit. A figure's hedge is emphasised
    with it (hedge_figures)."""
    picked, hedges = _emphasis_plan(words, terms)
    return picked | set(hedges)


def hedge_figures(words: list[dict[str, Any]], terms: list[str] | None) -> dict[int, int]:
    """{hedge word position: position of the first word of the figure it qualifies} for the hedges
    emphasis_indices golds with a figure (ROUGHLY with 280 FEET). The 9:16 reveal holds a hedge's gold until that
    figure word is shown (round 3: ROUGHLY alone in gold put the emphasis on the hedge before 280 FEET was heard)."""
    return _emphasis_plan(words, terms)[1]


def _emphasis_plan(words: list[dict[str, Any]], terms: list[str] | None) -> tuple[set[int], dict[int, int]]:
    """(key-term positions, {hedge position: start of the term it qualifies}) for emphasis_indices and
    hedge_figures. A figure term takes its range with it (round 4: BETWEEN 94 AND 104, never a gold 94 beside a bone
    104) and its hedge (HEDGE_PHRASES); every key term takes an attribution hedge (ATTRIBUTION_HEDGES: POSSIBLY
    PHRYGIAN, never a bare gold PHRYGIAN)."""
    tokens = [_norm(w["word"]) for w in words]
    raw = [str(w.get("word", "")) for w in words]
    picked: set[int] = set()
    starts: list[tuple[int, bool]] = []   # (first position of a key term, whether it is a figure)
    for term in (terms or [])[:3]:
        seq = [t for t in (_norm(x) for x in term.split()) if t]
        for i in range(len(tokens) - len(seq) + 1):
            if seq and tokens[i:i + len(seq)] == seq:
                is_figure = any(c.isdigit() for c in term) or any(t in SPELLED_NUMBERS for t in seq)
                lo, hi = (_range_span(tokens, raw, i, i + len(seq) - 1) if is_figure else (i, i + len(seq) - 1))
                picked.update(range(lo, hi + 1))
                starts.append((lo, is_figure))
                break
    if not terms:
        digits = [i for i, w in enumerate(words) if any(c.isdigit() for c in w["word"])][:2]
        picked = set(digits)
        starts = [(i, True) for i in sorted(digits)]
    # Gold carries the hedge with the figure (round 3, standards editor): ROUGHLY 280 FEET, never a bare 280 FEET.
    hedges: dict[int, int] = {}

    def _take(target: int, start: int, phrases: tuple[tuple[str, ...], ...], skip_det: bool) -> int:
        """The position a hedge in `phrases` right before `start` begins at (after one article if `skip_det`),
        its words added to `hedges` as qualifying the term at `target`; `start` when there is none."""
        for lead_in in ((), ("the",), ("a",), ("an",)) if skip_det else ((),):
            j = start - len(lead_in)
            if j < 0 or tuple(tokens[j:start]) != lead_in or any(CLAUSE_PUNCT.search(raw[x]) for x in range(j, start)):
                continue
            for hedge in phrases:
                k = len(hedge)
                if j - k >= 0 and tuple(tokens[j - k:j]) == hedge and not any(
                        CLAUSE_PUNCT.search(raw[x]) or TERMINAL_PUNCT.search(raw[x]) for x in range(j - k, j)):
                    for h in range(j - k, start):
                        if h not in picked:
                            hedges[h] = target
                    return j - k
        return start

    for target, is_figure in starts:
        at = _take(target, target, HEDGE_PHRASES, False) if is_figure else target
        _take(target, at, ATTRIBUTION_HEDGES, True)
    return picked, hedges


# The hedges a figure's gold takes with it (emphasis_indices) and that no burst or cue splits from it
# (caption_bonds), longest first. Round 4: circa / c. / ca., at least and between.
HEDGE_PHRASES: tuple[tuple[str, ...], ...] = (
    ("more", "than"), ("fewer", "than"), ("less", "than"), ("as", "many", "as"), ("as", "much", "as"),
    ("up", "to"), ("about",), ("roughly",), ("nearly",), ("almost",), ("estimated",),
    ("some",), ("around",), ("approximately",), ("perhaps",), ("over",),
    ("at", "least"), ("circa",), ("c",), ("ca",), ("between",),
)
HEDGE_PHRASES = tuple(sorted(HEDGE_PHRASES, key=len, reverse=True))
# Attribution hedges (round 4): any key term's gold takes one with it, after at most one article (POSSIBLY THE
# PHRYGIANS), so the emphasis never states as fact what the narration hedges.
ATTRIBUTION_HEDGES: tuple[tuple[str, ...], ...] = tuple(sorted((
    ("may", "have", "been"), ("might", "have", "been"), ("may", "have"), ("might", "have"), ("possibly",),
    ("probably",), ("likely",), ("perhaps",), ("reportedly",), ("supposedly",), ("allegedly",), ("presumably",),
), key=len, reverse=True))
# Words joining the two ends of a range (94 TO 104, BETWEEN 94 AND 104, EIGHT OR NINE).
RANGE_JOINERS = frozenset({"to", "and", "or"})
_COUNT_NOUNS = frozenset({"hundred", "thousand", "million", "billion", "dozen"})


def _is_figure_at(tokens: list[str], i: int) -> bool:
    """Whether tokens[i] starts a figure: a numeral, a spelled number, or 'a' before hundred/thousand/dozen."""
    if not 0 <= i < len(tokens) or not tokens[i]:
        return False
    t = tokens[i]
    return (any(c.isdigit() for c in t) or t in SPELLED_NUMBERS
            or (t == "a" and i + 1 < len(tokens) and tokens[i + 1] in _COUNT_NOUNS))


def _figure_end(tokens: list[str], raw: list[str], i: int) -> int:
    """The last position of the figure starting at i (TWENTY THOUSAND, A HUNDRED), before any punctuation."""
    j = i
    while (j + 1 < len(tokens) and not _ends_clause(raw[j])
           and (tokens[j + 1] in SPELLED_NUMBERS or tokens[j + 1] in _COUNT_NOUNS
                or (tokens[j] == "a" and tokens[j + 1] in _COUNT_NOUNS))):
        j += 1
    return j


def _ends_clause(word: str) -> bool:
    return bool(CLAUSE_PUNCT.search(word) or (TERMINAL_PUNCT.search(word) and not _is_abbreviation(word)))


# Abbreviations whose full stop ends no sentence (round 4: 'c. 1200' split into two sentences)
SENTENCE_ABBREVIATIONS = frozenset({"c.", "ca.", "approx."})


def _is_abbreviation(word: str) -> bool:
    return word.strip().lower().rstrip("\"'”’)") in SENTENCE_ABBREVIATIONS


# Words that may follow a range's second end without naming what it counts (94 TO 104 FEET HIGH, 20 OR 30 YEARS
# LATER): a function word, not a different noun (round 4 fix: 20 YEARS OR 30 WORKERS is no range).
_RANGE_TAIL_WORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "by", "for", "from", "with", "into", "across", "and", "or", "but",
    "before", "after", "ago", "later", "earlier", "long", "high", "tall", "wide", "deep", "old", "each",
    "apiece", "per", "away", "around", "below", "above", "beneath", "underground", "down", "up", "back", "then",
    "were", "was", "are", "is", "had", "have", "could", "would", "might", "may", "will", "to", "than", "that",
    "which", "who", "when", "where", "while", "as",
})


def _unit_key(word: str) -> str:
    """One spelling for a counted word, so FOOT, FEET and FT, or YEAR and YEARS, compare equal."""
    if word in ("feet", "foot", "ft"):
        return "ft"
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _counted_after(tokens: list[str], raw: list[str], e: int) -> str | None:
    """The word after the figure ending at e that names what it counts (FEET, WORKERS); None at a clause end, the
    end of the words, a range joiner or a function word (_RANGE_TAIL_WORDS)."""
    if e + 1 >= len(tokens) or _ends_clause(raw[e]):
        return None
    t = tokens[e + 1]
    return None if not t or t in RANGE_JOINERS or t in _RANGE_TAIL_WORDS else t


def _same_count(first: str | None, second: str | None) -> bool:
    """Whether two range ends count one thing: either end names nothing, or both name the same unit or noun."""
    return first is None or second is None or _unit_key(first) == _unit_key(second)


def _range_span(tokens: list[str], raw: list[str], lo: int, hi: int) -> tuple[int, int]:
    """(lo, hi) widened over a range the figure at lo..hi is one end of: '94 to [104 feet]' or '[94] to 104
    (feet)'. A unit may follow either end. Round 4 fix (independent review): AND joins a range only after BETWEEN
    (20,000 PEOPLE AND 50 CATTLE is two counts); TO / OR / BETWEEN..AND only when the second end names nothing it
    counts or the same unit or noun as the first (20 YEARS OR 30 WORKERS is no range)."""
    n = len(tokens)
    fs = next((x for x in range(lo, hi + 1) if _is_figure_at(tokens, x)), None)
    if fs is None:
        return lo, hi
    fe = min(_figure_end(tokens, raw, fs), hi) if hi >= fs else fs
    # the term's own count: its last word after the figure, else a unit right after the term
    j = hi
    if hi > fe:
        own: str | None = tokens[hi]
    elif j + 1 < n and tokens[j + 1] in MEASURE_UNITS and tokens[j + 1] not in _RANGE_TAIL_WORDS \
            and not _ends_clause(raw[j]):
        j += 1
        own = tokens[j]
    else:
        own = _counted_after(tokens, raw, hi)

    def _joins(joiner: int, first_start: int) -> bool:
        if tokens[joiner] not in RANGE_JOINERS or _ends_clause(raw[joiner]) or _ends_clause(raw[joiner - 1]):
            return False
        return tokens[joiner] != "and" or (first_start >= 1 and tokens[first_start - 1] == "between"
                                           and not _ends_clause(raw[first_start - 1]))

    # forward: [figure] (unit) joiner figure (unit)
    new_hi = hi
    if j + 2 < n and _joins(j + 1, fs) and _is_figure_at(tokens, j + 2):
        e2 = _figure_end(tokens, raw, j + 2)
        other = _counted_after(tokens, raw, e2)
        first = tokens[j] if j > fe else None
        if _same_count(first, other):
            new_hi = e2
            if other is not None and (other in MEASURE_UNITS or (first is not None and _unit_key(first) == _unit_key(other))):
                new_hi = e2 + 1
    # backward: figure (unit) joiner [figure]
    new_lo = lo
    k = fs - 1
    if k >= 1 and tokens[k] in RANGE_JOINERS:
        m = k - 1
        earlier: str | None = None
        if tokens[m] in MEASURE_UNITS and m >= 1 and not _ends_clause(raw[m - 1]):
            earlier = tokens[m]
            m -= 1
        while m >= 1 and (tokens[m] in SPELLED_NUMBERS or tokens[m] in _COUNT_NOUNS) and (
                tokens[m - 1] in SPELLED_NUMBERS or tokens[m - 1] == "a") and not _ends_clause(raw[m - 1]):
            m -= 1
        if _is_figure_at(tokens, m) and _joins(k, m) and _same_count(earlier, own):
            new_lo = m
    return new_lo, new_hi


def caption_bonds(words: list[Any]) -> set[int]:
    """Word positions i whose boundary with word i + 1 no caption burst, sidecar cue or cue line may split
    (round 4, standards editor): a hedge (HEDGE_PHRASES) and the figure it qualifies, inside a multi-word hedge
    (UP TO, MORE THAN), the words of one figure (TWENTY THOUSAND), and both ends of a range (94 TO 104, 94 FEET TO
    104 FEET, BETWEEN 94 AND 104). Only before a figure: 'over the wall', 'some of' and 'up to the gate' split
    freely. Never across punctuation. `words` are timed word dicts or plain strings."""
    raw = [str(w.get("word", "")) if isinstance(w, dict) else str(w) for w in words]
    tokens = [_norm(r) for r in raw]
    n = len(tokens)
    bonds: set[int] = set()

    def _bond(a: int, b: int) -> None:   # every boundary from a to b - 1, unless a word there ends a clause
        span = range(a, b)
        if all(not _ends_clause(raw[x]) for x in span):
            bonds.update(span)

    for i in range(n):
        if not tokens[i]:
            continue
        for hedge in HEDGE_PHRASES:
            k = len(hedge)
            if tuple(tokens[i:i + k]) == hedge and _is_figure_at(tokens, i + k):
                _bond(i, i + k)
                break
        if _is_figure_at(tokens, i):
            end = _figure_end(tokens, raw, i)
            _bond(i, end)
            _, hi = _range_span(tokens, raw, i, end)
            if hi > end:
                _bond(i, hi)
    return bonds


SEAM_RETURN_S = 0.3   # s: the loop bridge's level returns to the bed's own over its last 0.3 s (_audio_chain)


def _audio_chain(vo_idx: int, bg_idx: int, pad: float = 0.0, card_at: Optional[float] = None, card_db: float = 0.0,
                 end: float | None = None) -> str:
    """Voice cleanup + optical vocal leveling + center spectral pocket carving + ducked stereo bed.
    pad: seconds of silence after the voice (the end card), so the mix runs on with the bed alone.
    card_at/card_db: the end card's level set from card_at on (a 0.3 s ramp), measured per run. With a pad (the
    Reel's loop bridge, which loops into the programme's first frame) the level returns to the bed's own over the
    pad's last SEAM_RETURN_S, so the loop restarts at the level it ends on: no step at the seam of card_db
    (review, 2026-11-03).
    end: the programme's cut (s; the Reel's narration + loop bridge). The mix is then exactly round(end * rate)
    samples of voice and bed, whatever order ffmpeg feeds the two inputs in (CI, 2026-11-04: sidechaincompress
    forwards the first EOF it sees and drops what the other input had queued, and amix=duration=first then ran on
    with the voice's silent pad alone, so the master's last 70 ms were digital zeros on a loaded runner). Both
    branches are padded without end, so no EOF reaches either filter, and the mix is cut by sample count."""
    exact = None if end is None else round(end * AUDIO_SAMPLE_RATE)
    pad_filter = "apad," if exact is not None else (f"apad=pad_dur={pad:.3f}," if pad > 0 else "")
    bed_pad = ",apad" if exact is not None else ""
    cut = f",atrim=end_sample={exact}" if exact is not None else ""
    card_filter = ""
    if card_at is not None and card_db:
        lin = 10 ** (card_db / 20)
        ramp = f"min(max((t-{card_at:.3f})/0.3,0),1)"
        if pad > 0:
            seam = card_at + pad
            back = min(SEAM_RETURN_S, pad / 2)
            ramp = f"min({ramp},min(max(({seam:.4f}-t)/{back:.3f},0),1))"
        card_filter = f",volume='1+({lin:.5f}-1)*{ramp}':eval=frame"
    return (
        f"[{vo_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,{pad_filter}"
        f"highpass=f=85,compand=attacks=0.02:decays=0.15:points=-80/-80|-40/-35|-20/-16|0/-10,"
        f"equalizer=f=3500:width_type=q:w=1.2:g=1.8,equalizer=f=7000:width_type=q:w=2.0:g=-2.2,"
        f"asplit[v_main][v_sc];"
        f"[{bg_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"equalizer=f=3200:width_type=q:w=1.5:g=-3.0{bed_pad}[bg_carved];"
        f"[bg_carved][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0{cut},aformat=channel_layouts=stereo{card_filter}"
    )


def _seam_tail(end: float, edge: float) -> str:
    """The Reel programme's last filters (prefixed with a comma): the loop seam's fade-out over the last `edge`
    seconds, by sample count, and the programme cut to exactly round(end * rate) samples. The timestamps are
    renumbered from the sample count first, so the fade cannot land off the cut by any timestamp offset the
    resampler or limiter carry, and apad/atrim make the length exact whatever those filters flush at the end."""
    rate = AUDIO_SAMPLE_RATE
    total = round(end * rate)
    fade = min(round(edge * rate), total)
    return (f",asetpts=N/SR/TB,afade=t=out:ss={total - fade}:ns={fade}"
            f",apad=whole_len={total},atrim=end_sample={total}")


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


def quantise_plate_widths(widths: list[int], steps: int = CAPTION_PLATE_STEPS,
                          slack: int = CAPTION_PLATE_SLACK) -> list[int]:
    """Plate widths for a run of bursts heard back to back (round 3: the plate no longer changes width every burst).
    From the widest down, each width takes every burst at most `slack` px narrower (at most slack / 2 more padding a
    side); at most `steps` widths in all, the last taking whatever remains. No plate grows past the run's widest."""
    if not widths:
        return []
    remaining = sorted(set(widths), reverse=True)
    levels: list[int] = []
    while remaining:
        top = remaining[0]
        levels.append(top)
        if len(levels) == steps:
            break
        remaining = [w for w in remaining if w < top - slack]
    return [min(lv for lv in levels if lv >= w) if any(lv >= w for lv in levels) else w for w in widths]


# The designed contrast record beside each caption file (round 3, colour_accessibility), in the layout of the
# graphics' <clip>.contrast.json records. No QA path reads it yet: video_qa.text_contrast_audit runs
# graphic_contrast_problems on run_dir / "graphics" only, and the caption file is written beside the master. The
# build-time check in build_subtitles is its only enforcement today.
CAPTION_CONTRAST_SIDECAR = ".contrast.json"


def _ass_rgb(colour: str) -> tuple[int, int, int]:
    """(r, g, b) of an ASS colour &HAABBGGRR& (or &HBBGGRR&)."""
    hexes = colour.strip("&Hh")[-6:]
    return int(hexes[4:6], 16), int(hexes[2:4], 16), int(hexes[0:2], 16)


def _ass_alpha(colour: str) -> float:
    """Transparency 0-1 of an ASS colour &HAABBGGRR& (0 opaque; a 6-digit colour is opaque)."""
    digits = colour.strip("&Hh")
    return int(digits[:2], 16) / 255.0 if len(digits) == 8 else 0.0


def caption_contrast_record(bursts: list[dict[str, Any]], plate: str = COL_BACK, text: str = COL_PAST,
                            emphasis: str = COL_EMPHASIS) -> dict[str, Any]:
    """WCAG ratios of the colours the caption file draws (its text, emphasis and plate ASS colours, so a change
    to any of them is measured) on the plate, the plate's alpha composited over the worst-case ground: white,
    unless the plate is lighter than mid-grey, then black. One text element per burst, and a gold one where the
    burst shows gold. `bursts`: {"burst", "text", "gold"} rows."""
    from .typography import contrast_ratio, rel_luminance
    alpha = _ass_alpha(plate)
    fill = _ass_rgb(plate)
    ground_name, ground = ("white", (255, 255, 255)) if rel_luminance(fill) < 0.18 else ("black", (0, 0, 0))
    composite = tuple(round(c * (1 - alpha) + g * alpha) for c, g in zip(fill, ground))
    bone = round(contrast_ratio(_ass_rgb(text), composite), 2)
    gold = round(contrast_ratio(_ass_rgb(emphasis), composite), 2)
    elements: list[dict[str, Any]] = []
    for b in bursts:
        elements.append({"name": f"caption {b['burst']} bone", "text": b["text"], "drawn": bone})
        if b.get("gold"):
            elements.append({"name": f"caption {b['burst']} gold", "text": b["text"], "drawn": gold})
    return {"clip": "captions", "ground": ground_name, "plate": list(composite), "bone": bone, "gold": gold,
            "elements": elements}


def caption_graphic_intervals(scene_images: list[Path], durations: list[float]) -> list[tuple[float, float]]:
    """(start, end) seconds of each graphic clip (graphic_*.mp4) on screen, as graphic_vignette_enable reads them:
    the captions carry no gold while one is up (round 3: one gold event at a time)."""
    spans, start = [], 0.0
    for item, dur in zip(scene_images, durations):
        if Path(item).name.startswith("graphic_"):
            spans.append((start, start + dur))
        start += dur
    return spans


def _fits_wide(phrase: Any, max_width: int) -> bool:
    """A 16:9 burst fits one line: at most CAPTION_WIDE_MAX_CHARS characters and inside `max_width` (its plate
    included) in Montserrat Bold at CAPTION_WIDE_SIZE."""
    from .typography import get_font
    text = " ".join(_phrase_words(phrase))
    if len(text) > CAPTION_WIDE_MAX_CHARS:
        return False
    return get_font(CAPTION_WIDE_FONT, CAPTION_WIDE_SIZE, weight=700).getlength(text) + 2 * CAPTION_PAD_X <= max_width


def _wrap_cue(words: list[str], max_chars: int = SIDECAR_MAX_LINE_CHARS, max_lines: int = SIDECAR_MAX_LINES,
              bonds: set[int] | None = None) -> str | None:
    """The cue's text in at most `max_lines` lines of at most `max_chars` (two lines balanced, preferring a break
    after punctuation), or None when it does not fit. Never breaks a line after a position in `bonds` (default
    caption_bonds(words), round 4: ROUGHLY / 280 FEET is never split across the lines)."""
    text = " ".join(words)
    if len(text) <= max_chars:
        return text
    if max_lines < 2:
        return None
    if bonds is None:
        bonds = caption_bonds(words)
    best: tuple[float, str] | None = None
    for k in range(1, len(words)):
        if k - 1 in bonds:
            continue
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
    once it holds a full line, at a clause) where the text allows. Text is the narration's, word for word.
    No cue ends inside a caption_bonds bond (round 4): a hedge whose figure would open the next cue goes with it."""
    timed = [w for w in words if str(w.get("word", "")).strip()]
    bonds = caption_bonds(timed)
    pos = {id(w): i for i, w in enumerate(timed)}
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in timed:
        word = str(w.get("word", "")).strip()
        if cur:
            candidate = [str(x["word"]).strip() for x in cur] + [word]
            local = {pos[id(x)] - pos[id(cur[0])] for x in cur if pos[id(x)] in bonds}
            if _wrap_cue(candidate, bonds=local) is None or _word_end(w) - float(cur[0]["start"]) > SIDECAR_MAX_CUE:
                carry: list[dict[str, Any]] = []
                while len(cur) > 1 and pos[id(cur[-1])] in bonds:
                    carry.insert(0, cur.pop())   # the hedge goes on with its figure
                groups.append(cur)
                cur = carry
        cur.append(w)
        held = _word_end(w) - float(cur[0]["start"])
        text_len = len(" ".join(str(x["word"]).strip() for x in cur))
        if held >= SIDECAR_MIN_CUE and ((TERMINAL_PUNCT.search(word) and not _is_abbreviation(word)) or (CLAUSE_PUNCT.search(word) and text_len >= SIDECAR_MAX_LINE_CHARS)):
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
        g_words = [str(x["word"]).strip() for x in g]
        local = {pos[id(x)] - pos[id(g[0])] for x in g if pos[id(x)] in bonds}
        text = _wrap_cue(g_words, bonds=local) or _wrap_cue(g_words, bonds=set()) or " ".join(g_words)
        cues.append((start, end, text))
    return cues


def single_gold_span(shown: list[str], gold: list[int]) -> list[int]:
    """The gold positions of a still 16:9 line kept to one span (round 4: DERINKUYU ... ROUGHLY 280 FEET drew two
    gold events in one line): of several runs of adjacent gold words, the last that holds a figure (its hedge and
    range are in the run), else the first."""
    runs: list[list[int]] = []
    for ji in sorted(gold):
        if runs and ji == runs[-1][-1] + 1:
            runs[-1].append(ji)
        else:
            runs.append([ji])
    if len(runs) <= 1:
        return sorted(gold)
    tokens = [_norm(w) for w in shown]
    figures = [r for r in runs if any(_is_figure_at(tokens, j) for j in r)]
    return figures[-1] if figures else runs[0]


RELEVEL_MAX_DB = 6.0   # a dropped loop bridge's cut programme is re-levelled by at most this much


def _relevel_filter(audio: Path, seconds: float, target_lufs: float) -> str:
    """The audio filter prefix (ending in a comma) that brings the first `seconds` of `audio` to `target_lufs`
    integrated, limited at the master chain's ceiling; "" when it is already within 0.2 LU or cannot be measured."""
    import math
    import tempfile
    from .encoded_audio import LOWPASS_HZ, MASTER_LIMIT
    from .video_qa import measure_program_loudness
    handle, cut = tempfile.mkstemp(dir=audio.parent, suffix=".wav")
    os.close(handle)
    try:
        _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(audio),
                     "-t", f"{seconds:.3f}", "-c:a", "pcm_s24le", cut])
        measured = measure_program_loudness(Path(cut))["integrated_lufs"]
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        log.warning("Could not measure the cut programme (%s); left as mastered.", e)
        return ""
    finally:
        if os.path.exists(cut):
            os.unlink(cut)
    gain = max(-RELEVEL_MAX_DB, min(RELEVEL_MAX_DB, target_lufs - measured))
    if abs(gain) < 0.2:
        return ""
    log.info("Cut programme at %.1f LUFS; re-levelled by %+.1f dB to %.1f.", measured, gain, target_lufs)
    rate = AUDIO_SAMPLE_RATE
    return (f"volume={gain:.2f}dB,lowpass=f={LOWPASS_HZ}:poles=2,aresample={rate * 4},"
            f"alimiter=limit={MASTER_LIMIT:.4f}:attack=2.5:release=80:level=disabled:asc=1,aresample={rate},")


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
        graphic_intervals: list[tuple[float, float]] | None = None,
        first_frame_whole: float | None = None,
        cuts: list[float] | None = None,
    ) -> Path:
        """Dynamic running-speech kinetic captions:
        1. Grammatical phrasing: sensitive to punctuation (. ? ! …) so every new sentence starts fresh.
        2. Bursts formed to fit the line at the full caption size, so a Reel's captions keep one size; a burst
           heard for under CAPTION_MIN_CHUNK, or up for less than its reading time, joins a neighbour
           (merge_short_bursts).
        3. A plate (the brand's INK, rounded corners, padding from the cap height) behind each burst, fixed for its
           duration. 9:16: between bursts heard back to back in a sentence the plate keeps to CAPTION_PLATE_STEPS
           widths, and it fades only where it appears after or leaves before a pause.
        4. 9:16: words appear as they are spoken, each easing in over CAPTION_WORD_EASE_MS: upcoming words are
           transparent placeholders holding their place, spoken words bone, the script's key terms gold (a figure's
           hedge with it). 16:9: each burst is shown whole and still, bone with its key term gold (round 3: a
           documentary is read in sentences, not word by word). A burst shows two frames before its first word and
           leaves soon after its last across a pause (burst_timings).
        5. 9:16: Small Caps typography following the narration's capitals (caption_runs).
        While a graphic is on screen (`graphic_intervals`) the captions carry no gold: one gold event at a time.
        Round 4: no burst splits a hedge from its figure or term, or a range (caption_bonds); a 16:9 burst carries
        one gold span (single_gold_span); with `first_frame_whole` a Reel's first burst is whole from frame 0 when
        its narration starts that soon (burst_timings); with `cuts` a 9:16 burst's show and hide snap to picture cuts
        (snap_to_cuts). Both are 9:16 only: a 16:9 call ignores them. compose_video passes both for a 9:16 render;
        without them the timing is as in round 3.
        The designed contrast of bone and gold on the plate over a white ground is written beside the file
        (<stem>.contrast.json, caption_contrast_record); under brand.TEXT_CONTRAST the build fails.
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
                f"Style: CapText,{FONT_NAME},{FONT_SIZE_ASS},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,{REEL_LETTER_SPACING:g},0,1,0,0,5,0,0,0,1\n"
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

        key = emphasis_indices(words, emphasis)
        hedge_of = hedge_figures(words, emphasis)  # a hedge's gold waits for its figure (round 3)
        # No burst splits a hedge from its figure or term, or the ends of a range (round 4)
        bonds = caption_bonds(words) | {b for h, f in hedge_of.items() for b in range(h, f)}
        if is_widescreen:
            def fits(ph: list[dict[str, Any]]) -> bool:
                return _fits_wide(ph, max_caption_width)
            phrases = partition_words_grammatical(words, CAPTION_WIDE_MIN_WORDS, CAPTION_WIDE_TARGET_WORDS,
                                                  CAPTION_WIDE_MAX_WORDS, CAPTION_WIDE_MAX_CHARS, fits=fits,
                                                  glance=(24, CAPTION_WIDE_MAX_CHARS), bonds=bonds)
            phrases = merge_short_bursts(phrases, fits, CAPTION_WIDE_MAX_WORDS)
        else:
            phrases = merge_short_bursts(partition_words_grammatical(words, bonds=bonds), _fits_caption_width,
                                         MAX_BURST_WORDS)
        # The whole first frame is a Reel (9:16) feature only: a 16:9 documentary's first line appears with its
        # narration, as in round 3 (round 4 fix: it jumped to 0.0, up to 0.6 s before the voice).
        if is_widescreen:
            first_frame_whole = None
        timings = burst_timings(phrases, first_frame_whole=first_frame_whole)
        whole_first = bool(phrases) and timings[0][0] == 0.0 and first_frame_whole is not None and all(
            r == 0.0 for r in timings[0][2])
        if not is_widescreen:
            timings = snap_to_cuts(phrases, timings, cuts)
        raw_events: list[tuple[int, float, float, str, str]] = []  # (layer, start, end, style, text)

        center_x = width // 2
        graphic_spans = list(graphic_intervals or [])

        # Pass 1: each burst's geometry
        geo: list[dict[str, Any]] = []
        base = 0
        for pi, phrase in enumerate(phrases):
            show, hide, _ = timings[pi]
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
                box_y = height - eff_margin_v - box_h
                # \an5 centres the line box (wide_fs tall) on \pos; the letters' ink sits lower in it than its middle
                ink_mid = (cap_top + desc_bottom) / 2 - wide_fs * shrink / 2
                center_y = round(box_y + box_h / 2 - ink_mid)
            else:
                # One size per Reel: bursts are formed to fit at FONT_SIZE; only a single over-long word shrinks.
                fit = _caption_fit_size(phrase, EMPHASIS_POP / 100 if has_key else 1.0, max_width=max_caption_width)
                ass_size = FONT_SIZE_ASS if fit >= FONT_SIZE else round(fit / 0.5625)
                size_tag = f"{{\\fs{ass_size}}}" if fit < FONT_SIZE else ""
                text_w, cap_h = measure_caption_phrase(phrase, fit, CAPTION_SC_SCALE)
                # The drawn line plus its padding, never wider than the width every Reel platform leaves free
                box_w = min(round(text_w + REEL_PAD_X * 2), _REEL_UI.centred_width())
                box_h = REEL_PLATE_H
                # The pill ends CAPTION_REEL_CLEARANCE above the band the platforms draw over (eff_margin_v =
                # safe_zones.REEL.bottom; on the first publication its lower half sat under Meta's caption row).
                box_y = height - eff_margin_v - CAPTION_REEL_CLEARANCE - box_h
                # \an5 centres the line box (ass_size tall) on \pos; the line is capitals, so the cap band (cap top to
                # baseline) is centred in the plate (round 3: optically centred, was a fixed 7.5 px nudge).
                _, asc, desc = _font_vmetrics(FONT_NAME)
                baseline = asc * ass_size / (asc + desc)
                ink_mid = (baseline - cap_h / 2) - ass_size / 2
                center_y = round(box_y + box_h / 2 - ink_mid)
            in_graphic = any(show < b and hide > a for a, b in graphic_spans)
            geo.append({"show": show, "hide": hide, "box_w": box_w, "box_h": box_h, "box_y": box_y,
                        "center_y": center_y, "size_tag": size_tag, "base": base, "gold": not in_graphic})
            base += len(phrase)

        # 9:16 plate continuity (round 3): bursts that follow on with no gap form a run until a sentence ends; within
        # a run the plate keeps to CAPTION_PLATE_STEPS widths, and it fades only at a run's pause boundaries.
        joined_next = [pi + 1 < len(phrases) and abs(geo[pi]["hide"] - geo[pi + 1]["show"]) < 1e-6
                       for pi in range(len(phrases))]
        if not is_widescreen:
            run: list[int] = []
            for pi in range(len(phrases)):
                run.append(pi)
                ends_sentence = bool(TERMINAL_PUNCT.search(str(phrases[pi][-1].get("word", ""))))
                if not joined_next[pi] or ends_sentence or pi == len(phrases) - 1:
                    for idx, w in zip(run, quantise_plate_widths([geo[i]["box_w"] for i in run], CAPTION_PLATE_STEPS)):
                        geo[idx]["box_w"] = w
                    run = []

        contrast_rows: list[dict[str, Any]] = []
        for pi, phrase in enumerate(phrases):
            g = geo[pi]
            show, hide, reveals = timings[pi]
            shown = _phrase_words(phrase)
            base = g["base"]
            box_w, box_h = g["box_w"], g["box_h"]
            box_x = round(center_x - box_w / 2)
            whole = pi == 0 and whole_first   # round 4: the first burst whole and settled on frame 0
            fade_in = 0 if is_widescreen or whole or (pi > 0 and joined_next[pi - 1]) else CAPTION_PLATE_FADE_IN_MS
            fade_out = 0 if is_widescreen or joined_next[pi] else CAPTION_PLATE_FADE_OUT_MS
            fad = f"\\fad({fade_in},{fade_out})" if fade_in or fade_out else ""

            # Layer 0: the plate, fixed for the whole burst (sized to every word of it, spoken or not)
            path = rounded_rect_path(box_w, box_h, CAPTION_BORDER_RADIUS)
            raw_events.append((0, show, hide, "CapBox", f"{{\\pos({box_x},{g['box_y']}){fad}\\p1}}{path}{{\\p0}}"))
            pos = f"{{\\pos({center_x},{g['center_y']})\\q2}}"
            # A hedge is gold only beside its figure: never on a plate the figure is not on
            gold_words = [ji for ji in range(len(shown)) if base + ji in key and g["gold"]
                          and (base + ji not in hedge_of or hedge_of[base + ji] - base < len(shown))]
            # 9:16: the reveal index from which each gold word shows gold (a hedge's: its figure's first word)
            if is_widescreen:   # round 4: one gold span a still 16:9 line (the later figure, with its hedge)
                gold_words = single_gold_span(shown, gold_words)
            gold_from = {ji: (hedge_of[base + ji] - base if base + ji in hedge_of else ji) for ji in gold_words}
            contrast_rows.append({"burst": pi, "text": " ".join(shown), "gold": bool(gold_words)})

            if is_widescreen:
                # Layer 1, 16:9: the whole burst at once, still, from show to hide
                line = " ".join(f"{{\\c{COL_EMPHASIS if ji in gold_words else COL_PAST}}}{_ass_escape(w)}"
                                for ji, w in enumerate(shown))
                raw_events.append((1, show, hide, "CapText", f"{pos}{g['size_tag']}{line}"))
                continue

            # Layer 1, 9:16: one event per spoken word; the words still to come are transparent placeholders
            for wi in range(len(phrase)):
                if whole and wi < len(phrase) - 1:
                    continue   # shown whole: only the full line's event
                t_start = reveals[wi]
                t_end = reveals[wi + 1] if wi < len(phrase) - 1 else hide
                visible, hidden = [], []
                for ji, disp_word in enumerate(shown):
                    is_key = ji in gold_words and wi >= gold_from[ji]
                    if ji > wi:
                        colour = COL_FUTURE
                    else:
                        colour = COL_EMPHASIS if is_key else COL_PAST if ji < wi else COL_ACTIVE
                    word_ass = format_word_caption(disp_word, colour=colour, is_key=is_key,
                                                   is_spoken_now=(ji == wi), has_key=bool(gold_words),
                                                   ease_ms=0 if whole else CAPTION_WORD_EASE_MS)
                    (hidden if ji > wi else visible).append(word_ass)
                line = " ".join(visible)
                if hidden:
                    line += " " + CAPTION_HIDDEN + " ".join(hidden)
                out_fad = f"{{\\fad(0,{fade_out})}}" if fade_out and wi == len(phrase) - 1 else ""
                raw_events.append((1, t_start, t_end, "CapText", f"{pos}{out_fad}{g['size_tag']}{line}"))

        record = caption_contrast_record(contrast_rows)
        record_path = ass_path.with_name(ass_path.stem + CAPTION_CONTRAST_SIDECAR)
        record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        low = [e for e in record["elements"] if e["drawn"] < _brand.TEXT_CONTRAST]
        if low:
            raise RenderError(f"Caption contrast under {_brand.TEXT_CONTRAST}:1 on the plate: {low[:3]}")

        # The AI-reconstruction disclosure (layer 2 plate, layer 3 text) while a reconstruction is on screen
        if reconstruction_intervals:
            label = RECON_BADGE_LABEL
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
        chain = _audio_chain(0, 1, pad, end=card_at + pad if pad > 0 else None) + _master_chain(gain)
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
        end_card: non-empty ends a Reel on the loop bridge after the narration (src/end_card.py loop_bridge,
        operator decision 2026-11-03: in place of the full end card, which stays the 16:9 YouTube end screen); the
        value is the share line, drawn only when end_card.LOOP_LINE is on (off: the bridge has no line). The bed must
        run on for end_card.LOOP_SECONDS more (generate_ambient_bed(end_card_at=vo_dur)). Empty, or 16:9: no tail.
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

        # Historical reconstruction badge intervals (crossfades included: reconstruction_badge_intervals)
        recon_intervals = reconstruction_badge_intervals(scene_images, durations, vo_dur)

        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(words, ass_path, emphasis=emphasis, width=width, height=height,
                             reconstruction_intervals=recon_intervals,
                             graphic_intervals=caption_graphic_intervals(scene_images, durations),
                             first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S if height > width else None,
                             cuts=caption_cut_times(durations) if height > width else None)
        # The caption plates are measured in the bundled font; burn them in only if libass draws that font
        # (round 3: with only Montserrat-Variable.ttf bundled, the 16:9 line drew in Inter and ran off its plate).
        caption_font = (CAPTION_WIDE_FONT, 700) if width > height else (FONT_NAME, 400)
        font_problems = libass_font_problems(self.fonts_dir, [caption_font])
        if font_problems:
            raise RenderError("Captions would not draw in the bundled font: " + "; ".join(font_problems))
        from . import end_card as card_mod
        card = bool((end_card or "").strip()) and width < height
        card_seconds = card_mod.LOOP_SECONDS if card else 0.0   # the loop bridge (card_mod.SECONDS until 2026-11-03)
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
        if card:  # the bridge's sound is the bed running on: a bed made for the narration alone would cut out
            import soundfile as sf
            bed_seconds = sf.info(str(bed_path)).duration
            if bed_seconds < vo_dur + card_seconds - 0.02:
                raise RenderError(f"The ambient bed runs {bed_seconds:.2f}s; the loop bridge needs "
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
        # The loop seam (review, 2026-11-03): the programme is cut at the bridge's end (vo_dur + card_seconds), and
        # the bed's LOOP_BRIDGE_EDGE fade-out ends exactly there. The bed file keeps 50 ms of headroom past the cut
        # (end_card.bed_tail), where its own fade used to fall: the delivered file then ended at the bridge's held
        # level, and every loop restarted on a level step and a click. The fade is the programme's last filter, so
        # nothing after it (limiter latency, low-pass) moves it off the cut.
        seam_fade = ""
        if card:
            from .audio_engine import AudioEngine
            edge = AudioEngine.LOOP_BRIDGE_EDGE
            seam_fade = _seam_tail(vo_dur + card_seconds, edge)
        filter_parts.append(_audio_chain(n, n + 1, card_seconds, card_at, card_db,
                                         end=vo_dur + card_seconds if card else None)
                            + _master_chain(gain) + seam_fade + "[a_final]")

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
            motion = loop_motion(scene_images, durations, vo_dur, macro_indices, width, height)
            self.last_end_card = self._render_with_loop_bridge(head, delivery_args, end_card, vo_dur, output_mp4_path,
                                                               motion, target_lufs=profile.target_lufs)
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
    def _render_with_loop_bridge(head: List[str], delivery_args: List[str], share: str, vo_dur: float,
                                 output_mp4_path: Path, motion: Optional[Dict[str, float]] = None,
                                 target_lufs: Optional[float] = None) -> Dict[str, Any]:
        """The Reel master ending on the loop bridge (src/end_card.py loop_bridge; operator decision 2026-11-03, in
        place of the full end card), in two passes. The first renders the narrated body (visually lossless), the
        whole mastered programme (narration, then the bed alone under the bridge) and the graded last frame without
        captions; the Reel's real frame 0 is then read back from the body. The bridge is gated first
        (check_loop_bridge: a seam that would hitch is not shipped); the second pass encodes body and bridge as one
        delivery file, or, when the gate fails, the body alone with the programme cut at the narration's end (the
        record then has no variant and carries the gate's QA note). A failed gate is first retried without the push
        and on the longest crossfade (record: still_fallback, the first gate's problems); only if that fails too does
        the Reel end at the narration. `motion`: loop_motion's push through the seam.
        Returns what the bridge measured, for the record."""
        from . import end_card as card_mod
        stem = output_mp4_path.parent / output_mp4_path.stem
        body, audio = Path(f"{stem}_body.mkv"), Path(f"{stem}_programme.wav")
        last, first = Path(f"{stem}_card_bg.png"), Path(f"{stem}_frame0.png")
        total = vo_dur + card_mod.LOOP_SECONDS
        inter_encoder = _get_intermediate_video_encoder()
        kw: Dict[str, Any] = {"line": card_mod.LOOP_LINE, **(motion or {})}
        fallback: Optional[List[str]] = None
        try:
            _run_ffmpeg([*head,
                         "-map", "[v_final]", *inter_encoder, "-pix_fmt", "yuv420p",
                         "-r", str(VIDEO_FPS), "-t", f"{vo_dur:.3f}", str(body),
                         "-map", "[v_last]", "-update", "1", "-frames:v", "1000", str(last),
                         "-map", "[a_final]", "-c:a", "pcm_s24le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", "2",
                         "-t", f"{total:.3f}", str(audio)])
            # The frame the loop lands on: the body's first, as delivered (graded, captioned, at its push-in start).
            _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(body),
                         "-frames:v", "1", str(first)])
            with Image.open(last) as im:
                background = im.convert("RGB")
            with Image.open(first) as im:
                frame0 = im.convert("RGB")
            gate = card_mod.check_loop_bridge(background, frame0, share, **kw)
            if not gate["ok"]:
                # Before dropping the ending: the same bridge without the push through the seam and on its longest
                # crossfade (the gentlest it can be). Most seam problems come from the motion; this keeps the Reel's
                # ending and its length, and the record says which bridge shipped.
                still = {**kw, "close_push": 0.0, "open_push": 0.0, "crossfade": card_mod.LOOP_CROSSFADE[1]}
                retry = card_mod.check_loop_bridge(background, frame0, share, **still)
                if retry["ok"]:
                    log.warning("Loop bridge without its push: %s", "; ".join(gate["problems"]))
                    fallback, kw, gate = gate["problems"], still, retry
            errors = output_mp4_path.parent / f"{output_mp4_path.stem}_card_ffmpeg.log"
            if not gate["ok"]:
                log.warning("Loop bridge dropped: %s", "; ".join(gate["problems"]))
                fade = max(0.0, vo_dur - 0.05)
                # The programme was mastered to target with the bridge's quieter tail in it: cut at the narration
                # it reads louder (CI, 2026-11-03: -13.1 LUFS against -14 locally, past the 1 LU tolerance on the
                # runner). Re-level the cut programme to target, under the master chain's limiter ceiling.
                level = ""
                if target_lufs is not None:
                    level = _relevel_filter(audio, vo_dur, target_lufs)
                _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error",
                             "-i", str(body), "-i", str(audio), "-map", "0:v", "-map", "1:a",
                             "-af", f"{level}afade=t=out:st={fade:.3f}:d=0.05", *delivery_args,
                             "-t", f"{vo_dur:.2f}", str(output_mp4_path)])
                return {"variant": None, "start_s": round(vo_dur, 3), "duration_s": 0.0, "text": None,
                        "problems": gate["problems"], "note": gate["note"]}
            stats: Dict[str, Any] = {}
            bridge_frames = card_mod.loop_bridge(background, frame0, share, stats, **kw)
            w, h = card_mod.WIDTH, card_mod.HEIGHT
            cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error",
                   "-i", str(body), "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(card_mod.FPS),
                   "-i", "-", "-i", str(audio),
                   "-filter_complex", "[0:v]setsar=1[b];[1:v]scale=out_range=tv,format=yuv420p,setsar=1[c];"
                                      "[b][c]concat=n=2:v=1:a=0[v]",
                   "-map", "[v]", "-map", "2:a", *delivery_args, "-t", f"{total:.3f}", str(output_mp4_path)]
            with open(errors, "wb") as err:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=err)
                try:
                    for frame in bridge_frames:
                        proc.stdin.write(frame.tobytes())
                except BrokenPipeError:
                    pass
                finally:
                    proc.stdin.close()
                try:
                    code = proc.wait(timeout=FFMPEG_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired as e:
                    proc.kill()
                    raise RenderError(f"ffmpeg did not finish the loop bridge within {FFMPEG_TIMEOUT_SECONDS}s; stopped.") from e
            if code != 0:
                raise RenderError("Loop bridge encode failed: " + errors.read_text(encoding="utf-8", errors="replace")[-600:])
            errors.unlink(missing_ok=True)
        finally:
            for path in (body, audio, last, first):
                path.unlink(missing_ok=True)
        out = {**stats, "variant": card_mod.LOOP_VARIANT, "start_s": round(vo_dur, 3),
               "duration_s": card_mod.LOOP_SECONDS, "text": stats.get("text")}
        if fallback:
            out["still_fallback"] = fallback   # why the bridge with the push was not used
        return out

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
