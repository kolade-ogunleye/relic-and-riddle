"""
src/video_composer.py
=====================
FFmpeg assembly embodying the locked production standards:
1. Anton 80 px (72 until 2026-11-03) running-speech kinetic captions on an INK plate: each burst shown at one size, its words
   easing in as they are spoken (upcoming words are transparent placeholders, so the line never moves), bone
   text, gold for the script's key terms only; zero-overlap ASS events inside the Reel safe box
   (safe_zones.REEL: the plate ends above y 1370, centred in the box at x 480). 16:9 burns in one still Montserrat Bold line per burst on the
   same plate (shown whole, not word by word).
2. Varied Ken Burns motion sampled at fractional source positions (one shared camera,
   src/camera_motion.py) on eased curves, through lossless intermediates (src/still_camera.py).
3. Voice cleanup, sidechain ducking of the ambient bed under speech, then two-pass mastering to -14 LUFS
   (ITU-R BS.1770) with true peak below -1.5 dBTP: one measurement of the unlimited mix, then its measured gain
   applied linearly ahead of a 4x-oversampled brickwall limiter (QF-11). loudnorm's own second pass is not used:
   its linear mode needs the whole gain to fit under the true-peak target, which no real mix does, so it falls
   back to its dynamic mode (time-varying gain; QF-11 review 3: 34 of 34 real mixes).
4. H.264 CRF 18 (slow), TV-range yuv420p, AAC 48 kHz, +faststart for streaming.
"""

import json
import logging
import math
import os
import re
import shutil
import subprocess
import unicodedata
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any, Dict, List, Optional, Set, Tuple
from PIL import Image, ImageFilter

from .config import (
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
    CAPTION_REEL_TEXT_WIDTH,
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
    CAPTION_INK_PAD_MIN,  # noqa: F401 (withdrawn by the operator, 2026-11-04: no plate uses it; kept for imports)
    CAPTION_INK_FIT_PAD,
    CAPTION_BOND_EXTRA_WORDS,
    CAPTION_BOND_EXTRA_CHARS,
    CAPTION_SHORT_SENTENCE_MIN_S,
    CAPTION_COMMA_PAUSE_S,
    CAPTION_DWELL_AFTER_LAST_S,
    CAPTION_DIGIT_MIN_S,
    CAPTION_WIDE_MAX_PLATE,
    CAPTION_CUT_CLEAR_FRAMES,
    CAPTION_TWO_LINE_INK_GAP,
    CAPTION_TWO_LINE_PITCH_EM,
    CAPTION_PAUSE_BREAK_S,
    CAPTION_PAUSE_SOFT_S,
    CAPTION_MIN_ON_SCREEN_S,
    CAPTION_SHORT_BURST_S,
    SIDECAR_MIN_CUE_HARD,
    SIDECAR_SENTENCE_TAIL,
    SIDECAR_LINE_BALANCE,
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


# Clips that are a photograph in motion (QF-86): a parallax move (src/parallax.py render_clip) and an AI motion
# clip animated from a photo's anchor frame (src/motion.py). They take the photo curve as the stills do.
PHOTO_CLIP_OPERATIONS = ("parallax", "ai_motion")
_NOT_PHOTO_STEMS = ("gap_gen_", "gen_", "graphic_", "hook_frame", "footage_")


def is_photo_clip(item) -> bool:
    """True for a clip that is a photograph in motion: its own lineage record names a PHOTO_CLIP_OPERATIONS
    operation, its whole source chain verifies (lineage.source_chain: every hash intact, every source present),
    and neither the record's own sources nor anything in the chain is a generated illustration, a graphic or
    footage. Fails closed: an unreadable record, a broken or stale chain, or a missing source is False (the clip
    keeps its supplied colour)."""
    p = Path(item)
    if p.stem.startswith(_NOT_PHOTO_STEMS):
        return False
    try:
        from .lineage import sidecar, source_chain
        record = json.loads(sidecar(p).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("operation") not in PHOTO_CLIP_OPERATIONS:
            return False
        own = [str(s.get("path") or "") for s in record.get("sources") or [] if isinstance(s, dict)]
        if not own or not all(own):
            return False
        chain = [str(s.get("path") or "") for s in source_chain(p) if s.get("kind") != "lineage"]
    except Exception:  # noqa: BLE001 - any unreadable or broken lineage: not shown to be a photograph
        return False
    return not any(Path(s).stem.startswith(_NOT_PHOTO_STEMS) for s in own + chain)


def is_photo_still(item) -> bool:
    """True for a still that is a photograph, by the same rule as is_photo_clip: no generated-illustration,
    graphic, hook-frame or footage stem in its own name or anywhere in its lineage, and, when it has a lineage
    record (an upscale, a privacy edit), a chain that verifies. Fails closed: a broken or stale chain is False (the
    still keeps its supplied colour). A still without a record (a photo as downloaded) is judged by its name."""
    p = Path(item)
    if p.stem.startswith(_NOT_PHOTO_STEMS):
        return False
    try:
        from .lineage import source_chain
        chain = [str(s.get("path") or "") for s in source_chain(p) if s.get("kind") != "lineage"]
    except Exception:  # noqa: BLE001 - any unreadable or broken lineage: not shown to be a photograph
        return False
    return not any(Path(s).stem.startswith(_NOT_PHOTO_STEMS) for s in chain)


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
# in under two seconds"). The opening clip gets the stills' push on top (still_camera's "hook" preset, moving from
# frame 0: lead), a crop zoom that changes nothing in the picture.


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


def picture_vignette(width: int, height: int) -> str:
    """The lens vignette compose_video draws (freeze pass, 2026-11-04). ffmpeg's vignette `aspect` is the pixel
    aspect (SAR), not the frame's: 16/9 on a square-pixel 1920x1080 frame crushed its corners to black. 9:16 keeps
    its approved chain byte for byte; 16:9 uses aspect 1 (square pixels) at a wider angle, PI/6.4, because aspect 1
    at PI/4.2 still takes the corners to about 0.21 of the centre (measured)."""
    if width <= height:
        return "vignette=PI/4.2:aspect=9/16"
    return "vignette=PI/6.4:aspect=1"


def is_ai_motion_clip(path: Path) -> bool:
    """A clip src/motion.py made from a photo (motion_NN.mp4), as opposed to footage, a graphic or a manual
    hook clip, which move by themselves."""
    return is_video(path) and Path(path).stem.startswith("motion_")


def video_colorspace(path: Path) -> str:
    """A clip's YCbCr matrix tag as ffprobe reads it ("" untagged or unreadable: encoding.source_matrix then
    takes BT.709, as players do for HD)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=color_space", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                             capture_output=True, text=True, timeout=10, check=False)
        tag = out.stdout.strip() if out.returncode == 0 else ""
        return "" if tag in ("unknown", "N/A") else tag
    except Exception:  # noqa: BLE001  a missing tag is read as BT.709
        return ""


def _camera_kind(item: Any, i: int, n: int, macro_indices: set[int], width: int, height: int) -> str | None:
    """The still_camera preset compose_video moves scene `i` with, None when it is not camera-moved (footage, a
    parallax or graphic clip, an essential full image)."""
    item = Path(item)
    from .image_presentation import full_image
    from .still_camera import preset_for
    if is_video(item):
        return "hook" if i == 0 and width < height and is_ai_motion_clip(item) else None
    if full_image(item):
        return None
    return preset_for(i, n, i in macro_indices)


def loop_motion(scene_images: List[Any], durations: List[float], vo_dur: float, macro_indices: Set[int],
                width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                closing_transition: float = TRANS_DUR, opening_transition: float | None = None) -> Dict[str, float]:
    """The motion the loop bridge carries through the seam (end_card.loop_bridge's close_zoom, close_push and
    open_push), read from how compose_video moves the closing and opening shots (QF-22, still_camera): a still eases
    in and out of its push (at rest at both ends: no push through the seam, close_zoom its zoom on the last frame);
    the AI hook clip's push starts at the old rate from its first frame (`lead`), so the bridge pre-rolls frame 0 at
    it; a video that moves by itself, or an essential full image, has none. The pushes are zoom per frame. Frame
    counts are compose_video's: `durations` end on its frame-rounded boundaries, each clip gets half its
    transition's overlap, and the closing clip runs to the narration's last frame."""
    from .still_camera import frame_count, motion_for
    n = len(scene_images)
    if not n:
        return {"close_zoom": 1.0, "close_push": 0.0, "open_push": 0.0}
    opening_transition = closing_transition if opening_transition is None else opening_transition
    boundaries = [sum(durations[:k + 1]) for k in range(n - 1)]
    end = math.ceil(vo_dur * VIDEO_FPS) / VIDEO_FPS
    close_frames = frame_count(end - (boundaries[-1] - closing_transition / 2 if n > 1 else 0.0), VIDEO_FPS)
    open_frames = frame_count(boundaries[0] + opening_transition / 2 if n > 1 else end, VIDEO_FPS)
    out = {"close_zoom": 1.0, "close_push": 0.0, "open_push": 0.0}
    close_kind = _camera_kind(scene_images[-1], n - 1, n, macro_indices, width, height)
    if close_kind:
        m = motion_for(close_kind, close_frames, lead=close_kind == "hook")
        out.update(close_zoom=m.end_zoom, close_push=m.end_velocity)
    open_kind = _camera_kind(scene_images[0], 0, n, macro_indices, width, height)
    if open_kind:
        out["open_push"] = motion_for(open_kind, open_frames, lead=open_kind == "hook").start_velocity
    return {key: round(value, 6) for key, value in out.items()}


def render_cameras(cameras: list[tuple[int, Path, Path, float, dict[str, Any]]],
                   size: tuple[int, int]) -> dict[int, Any]:
    """Render compose_video's camera clips ((scene, source, clip, seconds, still_camera options)); {scene: Motion}.
    Refused (RenderError) when the clips would not fit on the disk."""
    from .still_camera import ensure_disk, frame_count, render_clip
    if cameras:
        try:
            ensure_disk(cameras[0][2].parent, sum(frame_count(c[3], VIDEO_FPS) for c in cameras), size)
        except RuntimeError as exc:
            raise RenderError(str(exc)) from exc
    return {i: render_clip(source, clip, seconds, size, VIDEO_FPS, **options)
            for i, source, clip, seconds, options in cameras}


def seam_from_cameras(motions: dict[int, Any], n: int) -> dict[str, float]:
    """The seam motion the rendered cameras really have (loop_motion predicts the same from the frame counts)."""
    out: dict[str, float] = {}
    if n - 1 in motions:
        out.update(close_zoom=round(motions[n - 1].end_zoom, 6), close_push=round(motions[n - 1].end_velocity, 6))
    if 0 in motions:
        out["open_push"] = round(motions[0].start_velocity, 6)
    return out


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
from .encoding import BT709_FLAGS, BT709_TAGS, BT709_TO_RGB, RGB_TO_BT709, source_matrix, to_bt709


def _ass_escape(text: str) -> str:
    """Neutralizes ASS override syntax so caption text can't inject styling tags."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


from .safe_zones import REEL as _REEL_UI
from .safe_zones import caption_band_top as _caption_band_top
from .safe_zones import graphics_box as _graphics_box


def _caption_family() -> str:
    """The family a 9:16 caption is drawn and measured in: with CAPTION_SMALL_CAPS the derived caption face
    (font_assets.CAPTION_FAMILY, Anton with its smcp glyphs as the default lower case: libass applies no OpenType
    feature a script asks for), else FONT_NAME. The small capitals exist for Anton only."""
    from .font_assets import CAPTION_FAMILY
    if CAPTION_SMALL_CAPS:
        if FONT_NAME != "Anton":
            raise ValueError("True caption small caps require the approved Anton-derived face")
        return CAPTION_FAMILY
    return FONT_NAME


@lru_cache(maxsize=16)
def _font_vmetrics(name: str, weight: int | None = None) -> tuple[int, int, int]:
    """(unitsPerEm, usWinAscent, usWinDescent) of a bundled font, read from its head and OS/2 tables. libass sizes
    a font so that its Windows ascent plus descent equal the ASS Fontsize (Anton: Fontsize 128 is a 73.8 px em,
    which is why FONT_SIZE_ASS 128 rendered the 72 px caption and 142 renders the 80 px one)."""
    from .font_engine import inspect_font
    from .typography import font_file

    return inspect_font(str(FONTS_DIR / font_file(name, weight))).metrics


@lru_cache(maxsize=32)
def _libass_face(path: Path) -> tuple[frozenset[str], int] | None:
    """What libass (0.17) sees of a bundled font file: the names it matches an ASS Fontname against (name ID 1, the
    legacy family, plus ID 4, the full name, and ID 6, the PostScript name; lower-cased) and the OS/2 weight of face
    0. libass reads only face 0 of a file and sets no variation axes, so a variable font is its default instance:
    Montserrat-Variable.ttf is "Montserrat Thin" at weight 100, and "Montserrat" is not one of its names (round 3:
    the 16:9 captions drew in the system fallback, Inter). None for a file that is not a font."""
    from fontTools.ttLib import TTLibError

    from .font_engine import inspect_font

    try:
        info = inspect_font(str(path))
    except (OSError, TTLibError, KeyError):
        return None
    return info.names, info.weight


def libass_font_problems(fonts_dir: Path, requests: list[tuple[str, int]], extra_fonts=()) -> list[str]:
    """For each (ASS Fontname, weight) the captions draw, why libass would not draw it from the bundled fonts: no
    bundled face carries that name (libass then takes a system font: the line no longer matches the plate measured
    in the bundled font), or the nearest bundled face of that name is more than 150 lighter than asked (libass
    thickens it synthetically: not the designed weight). Empty when every request resolves."""
    faces = [face for p in [*sorted(Path(fonts_dir).glob("*")), *extra_fonts] if p.suffix.lower() in (".ttf", ".otf")
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
    upm, asc, desc = _font_vmetrics(_caption_family())
    ass_size = FONT_SIZE_ASS if fit >= FONT_SIZE else fit / 0.5625
    return ass_size * upm / (asc + desc)


def reel_cap_height(fit: float) -> float:
    """The drawn cap height in px of a 9:16 caption at nominal size `fit`."""
    from .typography import get_font
    return float(-get_font(_caption_family(), max(8, round(reel_render_px(fit)))).getbbox("H", anchor="ls")[1])


# The 9:16 plate's height follows the type (round 3: CAPTION_REEL_PAD_Y_EM of the cap height above and below the
# capitals); its sides keep CAPTION_PAD_X, as does the 16:9 plate.
REEL_LETTER_SPACING = 0.5   # the 9:16 CapText style's Spacing (px at PlayRes)
REEL_CAP_H = reel_cap_height(FONT_SIZE)
REEL_PAD_X = CAPTION_PAD_X
REEL_PAD_Y = round(CAPTION_REEL_PAD_Y_EM * REEL_CAP_H)
REEL_PLATE_H = round(REEL_CAP_H + 2 * REEL_PAD_Y)   # one plate height per Reel

# The widest caption text: the reviewed CAPTION_REEL_TEXT_WIDTH (640 px), its pill (text plus padding, 696 px) inside
# the width every Reel platform leaves free (safe_zones.REEL, 735 px). The pill is centred in that box, not on the
# frame (reel_caption_centre_x): centred on the frame the organic box leaves 616 px (its right rail, 232 px, is wider
# than its left, 113 px), which would cut every line to 560 px and form new bursts (QF-24).
MAX_CAPTION_WIDTH = min(CAPTION_REEL_TEXT_WIDTH, _REEL_UI.box()[2] - _REEL_UI.box()[0] - 2 * REEL_PAD_X)


def reel_caption_centre_x(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The x a 9:16 caption is centred on: the frame's centre when the widest pill fits around it inside the Reel
    box, else the box's centre (x 480 at 1080 x 1920: the shared organic Reel box is x 113-848)."""
    x0, _, x1, _ = _REEL_UI.box(width, height)
    half = (MAX_CAPTION_WIDTH + 2 * REEL_PAD_X) * width / VIDEO_WIDTH / 2
    centre = width // 2
    return centre if x0 <= centre - half and centre + half <= x1 else round((x0 + x1) / 2)


def reel_caption_span(width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT) -> int:
    """The widest 9:16 plate centred on reel_caption_centre_x that stays inside the Reel box."""
    x0, _, x1, _ = _REEL_UI.box(width, height)
    cx = reel_caption_centre_x(width, height)
    return int(2 * min(cx - x0, x1 - cx))


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
    # freeze pass C7 (2026-11-04): the metric spellings the scripts use, and areas
    "tonne", "tonnes", "kilometer", "kilometers", "metric", "acres", "acre", "hectares", "hectare",
    # freeze pass U1 (2026-11-04): yields and the metric ton
    "megaton", "megatons", "kiloton", "kilotons", "gigaton", "gigatons", "metric-ton", "metric-tons",
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
# Freeze pass C8 (2026-11-04): a closed, topic-agnostic list of pre-modifiers that read as cut off at a burst's end
# mid-sentence (THE MEMORIAL | TEMPLE, A TOTAL | HEIGHT), unless a comma ends them; a possessive (THE MINE'S |
# ROCK SALT) and a hyphenated measure (THEIR 13-FOOT | (4 M)) are dangling ends too (_dangles).
PRE_MODIFIERS = {
    "rather", "late", "early", "upper", "lower", "northern", "southern", "eastern", "western", "ancient", "various",
    "multiple", "total", "single", "major", "natural", "reported", "original", "severe", "active", "structural",
    "subterranean", "deep", "aerial", "memorial", "carved",
    # freeze pass M1 (2026-11-04): attributive adjectives the panel and the reviewer listed, and the ordinals
    "native", "northwestern", "extensive", "greatest", "underground", "dark", "curved", "prominent", "varying",
    "central", "distinctive", "radial", "atmospheric", "celestial", "sudden", "unidentified", "botanical", "medieval",
    "unread", "expert", "allied", "massive", "concentric", "visual", "subsurface", "overlying", "outer", "inner",
    "terrestrial", "geological", "prehistoric", "distinct", "protruding", "mortuary", "human", "siliceous",
    "catastrophic", "hidden", "sacred", "royal", "giant", "vast", "remote", "famous", "mysterious",
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    # review round 3 (adversarial Richat copy): RATHER THAN A SUNKEN | ISLAND, SUBSEQUENT GEOCHEMICAL | ANALYSES
    "sunken", "exceptional", "geochemical", "subsequent",
    # review round 4: the degree words before an adjective (AS THE SECOND MOST | COMMON read as cut off)
    "most", "very", "least",
}
# Review round 4 (2026-11-04): DANGLING_ENDS is the function-word tails again (HEAD 5fdb5dc's set, which
# tests/test_loop_seam_and_captions_2026_11_03.py reads): a burst ending on one is a hard defect. A pre-modifier, a
# possessive or a hyphenated measure (_dangles) is a soft cost the phrase builder weighs against a lone word.
FUNCTION_WORDS = DANGLING_TAILS | {
    "be", "has", "had", "have", "do", "did", "does", "will", "would", "can", "could", "may", "might", "must",
    "shall", "should", "not",
}

CLAUSE_STARTERS = {
    "though", "although", "while", "whereas", "because", "since", "until", "unless",
    "if", "when", "where", "however", "yet", "which", "who", "whom", "whose"
}


# Review round 4: the time units a sidecar cue or line never breaks before AGO after (29,000 YEARS AGO)
TIME_UNITS = {"years", "year", "centuries", "century", "decades", "decade", "millennia", "millennium", "months",
              "month", "weeks", "week", "days", "day", "hours", "hour", "minutes", "minute", "seconds", "second"}
AREA_QUALIFIERS = {"square", "cubic", "sq"}   # freeze pass U1: 830 SQUARE MILES is one unit
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}
# A measure written as one hyphenated word (13-FOOT, 40-METRE): a modifier, never a burst's last word mid-sentence.
HYPHEN_MEASURE = re.compile(r"^\(?\d[\d,.]*-[a-z]+$", re.IGNORECASE)


def _month(raw: str) -> bool:
    core = raw.strip("\"'“”‘’()[],;:").lower()
    return core.rstrip(".") in MONTHS and (core.rstrip(".") != "may" or raw[:1].isupper())


def _is_atomic_boundary(w1_raw: str, w2_raw: str) -> bool:
    """True if w1 and w2 must NOT be split across burst boundaries."""
    w1 = re.sub(r"[^\w]", "", w1_raw).lower()
    w2 = re.sub(r"[^\w]", "", w2_raw).lower()
    if not w1 or not w2:
        return False
    # Freeze pass C7 (2026-11-04): dates (AUGUST 5, 5 AUGUST, MAY 1941), a conversion in brackets with the measure
    # it converts (60 FEET (18 M), 13-FOOT (4 M)) and its own words, the metric qualifier with its unit (METRIC TONS),
    # and THE YEAR with its figure (THE YEAR 196 AD).
    if _month(w1_raw) and re.fullmatch(r"\d{1,2}(st|nd|rd|th)?|\d{4}", w2):
        return True
    if re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", w1) and _month(w2_raw):
        return True
    if w2_raw.lstrip()[:1] == "(" and any(c.isdigit() for c in w2_raw) and (
            any(c.isdigit() for c in w1_raw) or w1 in MEASURE_UNITS or HYPHEN_MEASURE.match(w1_raw.strip())):
        return True
    if w1_raw.lstrip()[:1] == "(" and ")" not in w1_raw:
        return True
    if w1 == "metric" and w2 in MEASURE_UNITS:
        return True
    # Freeze pass U1: SQUARE / CUBIC with the unit after it (SQUARE MILES); the figure before it is a bond
    # (caption_bonds), released only where the whole group cannot be held at the bond floor (_relax_figure_bonds)
    if w1 in AREA_QUALIFIERS and w2 in MEASURE_UNITS:
        return True
    if w1 == "year" and any(c.isdigit() for c in w2):
        return True
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
    if w1 in TITLES and not _ends_clause(w1_raw):   # round 2: never across punctuation (QIN EMPEROR, MERCURY)
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


# Subject pronouns: a burst of one with only function words (IT WAS, THEY HAD) says nothing on its own (freeze pass
# C6 review: IT WAS | HIDDEN UNDERGROUND); a defect, not a ban.
SUBJECT_PRONOUNS = {"i", "it", "he", "she", "we", "they", "you", "there"}
SUBJECT_ONLY_PENALTY = 100   # less than a lone word: tests/test_caption_emphasis.py (origin/main) keeps no lone word


def _subject_only(chunk: List[Dict[str, Any]]) -> bool:
    """A burst of a subject pronoun and function words only (IT WAS), not a whole sentence."""
    words = [_bare(w.get("word", "")) for w in chunk]
    return (bool(words) and any(w in SUBJECT_PRONOUNS for w in words)
            and all(w in FUNCTION_WORDS or w in SUBJECT_PRONOUNS for w in words))


def _relax_figure_bonds(words: List[Dict[str, Any]], bonds: set[int], prefer: set[int] | None = None,
                        loose: set[int] | None = None) -> set[int]:
    """The Reel's bonds with each figure group that no burst can hold (wider than the ink budget at
    CAPTION_BOND_MIN_SIZE, or past the extra words and characters) shortened until one can (freeze pass C7): first
    a range's BETWEEN or FROM, or a preposition or article leading in (BETWEEN | 287 AND 300 KILOMETRES), then
    between a range's two ends, before the joiner (1,090 TONNES | TO 1,168 TONNES), so a burst breaks before a
    figure's core, never inside it. A hedge of uncertainty (ROUGHLY, NEARLY, AN ESTIMATED) is never released from its
    figure: such a group, like an atomic one (60 FEET (18 M)), shrinks whole instead. A group runs over bonds and
    atomic pairs; only bonds are released. Names keep their bonds: a whole name shrinks instead (the operator's
    handover item). Fix round 1 (the HEAD-regression gate): with `prefer` (the round-4 base cuts) the lead-in, the
    joiners and the SQUARE unit are released together, and the phrase builder takes the fewest cuts the base lacks
    (FROM NEARLY 1,090 | TO 1,168 TONNES rather than FROM | NEARLY 1,090 TO 1,168 TONNES). An atomic pair at a
    position in `loose` (_base_yields) does not hold."""
    raw = [str(w.get("word", "")) for w in words]
    n = len(raw)
    out = set(bonds)
    atomic = {k for k in range(n - 1) if _is_atomic_boundary(raw[k], raw[k + 1])} - (loose or set())

    def holdable(lo: int, hi: int) -> bool:
        part = raw[lo:hi + 1]
        return lo >= hi or bonded_burst_allowed(part) or (
            hi - lo + 1 <= MAX_BURST_WORDS and len(" ".join(_phrase_words(part))) <= MAX_BURST_CHARS
            and _fits_caption_bonded(part))

    i = 0
    while i < n:
        if i not in out and i not in atomic:
            i += 1
            continue
        a = i
        while i in out or i in atomic:
            i += 1
        b = i
        if any(c.isdigit() for w in raw[a:b + 1] for c in w):
            spans = [(a, b)]
            while spans:
                lo, hi = spans.pop()
                # the span's own groups: split where no link holds it any longer
                cut = next((k for k in range(lo, hi) if k not in out and k not in atomic), None)
                if cut is not None:
                    spans += [(lo, cut), (cut + 1, hi)]
                    continue
                # fix round 1: with a base, a lead-in word the base burst holds with the group counts with it (FROM
                # NEARLY 1,090 TO 1,168 is 644 px at 66: its joiners give way, never a lone FROM)
                ext = lo - 1 if (prefer is not None and lo >= 1 and lo - 1 not in prefer and lo - 1 not in out
                                 and _bare(raw[lo - 1]) in LEAD_IN_WORDS and not _ends_clause(raw[lo - 1])) else lo
                if holdable(lo, hi) and holdable(ext, hi):
                    continue
                joiners = [j for j in range(lo + 1, hi) if _norm(raw[j]) in RANGE_JOINERS and j - 1 in out]
                area = [j for j in range(lo, hi) if j in out and _norm(raw[j + 1]) in AREA_QUALIFIERS]
                lead = lo in out and lo not in atomic and _norm(raw[lo]) in LEAD_IN_WORDS
                if prefer is not None and (lead or joiners or area):
                    # fix round 1: with a base, every such link at once (after a joiner only where the base cuts
                    # there: BETWEEN 94 AND | 104 MILLION YEARS AGO); the phrase builder picks among them
                    out -= ({lo} if lead else set()) | {j - 1 for j in joiners} | (set(joiners) & prefer) | set(area)
                    spans.append((lo, hi))
                elif lead:
                    out.discard(lo)          # a range's BETWEEN or FROM, or a lead-in, first: BETWEEN | 287 AND 300
                    spans.append((lo + 1, hi))
                elif joiners:
                    out.discard(joiners[-1] - 1)   # then between the range's ends, before the joiner
                    spans.append((lo, hi))
                elif area:
                    out.discard(area[-1])   # U1: then a figure from its SQUARE unit (MORE THAN 830 | SQUARE MILES)
                    spans.append((lo, hi))
                # else a hedge of uncertainty stays with its figure (ROUGHLY 137 FEET (41.75 M)): it shrinks whole
        i = b + 1
    return out


def _dangles(word: str) -> bool:
    """Whether a burst ending mid-sentence on this word leaves it hanging: a DANGLING_ENDS word (a pre-modifier only
    without a comma after it), a possessive, or a hyphenated measure (freeze pass C8)."""
    bare = _bare(word)
    core = word.strip("\"'“”‘’()[]")
    if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word):
        return False   # round 2: punctuation closes it (40 KILOMETRES ACROSS, | THOUGH ...)
    if bare in DANGLING_TAILS and bare in DANGLING_ENDS:
        return True
    return bare in PRE_MODIFIERS or bool(re.search(r"['’]s$", core, re.IGNORECASE)) or bool(HYPHEN_MEASURE.match(core))


def _attributive_end(raw: List[str], q: int) -> bool:
    """Whether a burst ending on word q mid-noun-phrase leaves an attributive -ing word hanging (freeze pass M1,
    review round 3): the word ends in -ing, a PRE_MODIFIERS adjective comes right before it and a lower-case content
    word right after it, with no punctuation between (AN ACTIVE HIDING | PLACE, A PROMINENT WHISPERING | GALLERY).
    Topic-agnostic: it reads only the closed PRE_MODIFIERS list and the word shapes."""
    if q < 1 or q + 1 >= len(raw):
        return False
    word, before, after = raw[q], raw[q - 1], raw[q + 1]
    if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word) or CLAUSE_PUNCT.search(before):
        return False
    bare = _bare(word)
    return (len(bare) > 4 and bare.endswith("ing") and word[:1].islower() and _bare(before) in PRE_MODIFIERS
            and after[:1].islower() and _bare(after) not in FUNCTION_WORDS)


def _hard_dangles(word: str) -> bool:
    """A burst's end on a function-word tail (DANGLING_ENDS: an article, determiner, preposition, conjunction or
    relative word) with no punctuation closing it: the phrase builder's hard defect (review round 4)."""
    if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word):
        return False
    return _bare(word) in DANGLING_ENDS


# Review round 4 (2026-11-04): the phrase builder's soft costs, summed per split after the hard defects. A lone
# content word mid-sentence costs more than one soft end, so a lone word is formed only to save a hard defect or two
# soft ends (HEAD 5fdb5dc formed one only to save a hard defect; round 3's single tier let every new M1 pre-modifier
# end buy a lone word: 9 lone bursts became 38).
LONE_WORD_COST = 3
SOFT_END_COST = 2
LEAD_IN_COST = 2   # a one-word lead-in burst before a name (N1): allowed, but a two-line name takes it on line 1


def _open_phrase_end(raw: List[str], p: int, q: int, last: bool) -> int:
    """M1 (review round 4, soft, 1): a mid-sentence burst p..q ending on the one word after an article inside it, with
    a lower-case content word right after and no punctuation between (THE COMPLETED | ROOM COVERED): the noun phrase
    the article opened reads as cut off. Topic-agnostic: word shapes and FUNCTION_WORDS only."""
    if last or q < p + 1 or q + 1 >= len(raw) or _bare(raw[q - 1]) not in _ARTICLES:
        return 0
    word, nxt = raw[q], raw[q + 1]
    if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word) or not word[:1].islower() or _dangles(word):
        return 0   # a pre-modifier end is already a soft end (SOFT_END_COST)
    return int(_bare(word) not in FUNCTION_WORDS and nxt[:1].islower() and _bare(nxt) not in FUNCTION_WORDS)


def _split_defects(split: List[List[Dict[str, Any]]]) -> int:
    """How badly a sentence's split (its bursts in order, the last ending the sentence) reads: 2 for each burst
    of function words only, 1 for each burst but the last ending on a function-word tail (_hard_dangles: an
    article, determiner, preposition, conjunction or relative word). A sentence's own last word is no dangling end:
    nothing follows it. Pre-modifier, possessive and hyphenated-measure ends are soft costs (review round 4)."""
    defects = 0
    for k, chunk in enumerate(split):
        if _function_only(chunk):
            defects += 2
        elif k < len(split) - 1 and _hard_dangles(chunk[-1].get("word", "")):
            defects += 1
    return defects


def _split_lones(split: List[List[Dict[str, Any]]]) -> int:
    """Lone-word bursts in a sentence's split of more than one burst."""
    return sum(len(chunk) == 1 for chunk in split) if len(split) > 1 else 0


def _gap_after(words: List[Dict[str, Any]], k: int) -> float:
    """The silence (s) after word k: the next word's start less this word's end (words.json timings); 0 without."""
    if k + 1 >= len(words) or "start" not in words[k] or "start" not in words[k + 1]:
        return 0.0
    return float(words[k + 1]["start"]) - _word_end(words[k])


def _pause_break(words: List[Dict[str, Any]], k: int) -> bool:
    """Freeze pass P1: word k ends in clause or sentence punctuation and the voice then pauses CAPTION_PAUSE_BREAK_S or
    more, so a burst ends there (a hard break, partition_words_grammatical)."""
    word = str(words[k].get("word", ""))
    return bool(re.search(r"[,;:.?!]+[\"'”’)]*$", word)) and _gap_after(words, k) >= CAPTION_PAUSE_BREAK_S - 1e-9


def _name_end_at(named: set[int], pos: int) -> bool:
    """Whether word `pos` is the last word of a bonded proper name (name_bonds)."""
    return pos - 1 in named and pos not in named


def _name_start_of(named: set[int], end: int) -> int:
    k = end
    while k - 1 in named:
        k -= 1
    return k


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
    fits_bonded: Callable[[list[dict[str, Any]]], bool] | None = None,
    base_cuts: set[int] | None = None,
    loose: set[int] | None = None,
    held_soft: set[int] | None = None,
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
    split would end on a dangling word (caption review round 2, finding 1). The 16:9 burn-in passes its own `fits`
    (and `fits_bonded`, the test for a burst every boundary of which is bonded: a name may pass its 42 characters).
    `glance` is the burst length (characters) rewarded as readable at a glance.
    `bonds` (round 4) are word positions i whose boundary with word i + 1 no burst may split: by default
    caption_bonds(words), a hedge with its figure (ROUGHLY | 280) and both ends of a range (94 | TO | 104).

    Freeze pass round 2 (2026-11-04, operator decisions):
    N1  no multi-word 9:16 burst draws below CAPTION_BOND_MIN_SIZE to hold a name: a name too wide for one line at
        that size draws on two lines (_two_line_layout) with its article, and the words leading into it end the
        burst before or form a one-word lead-in burst (UNDER | THE RASHTRAKUTA DYNASTY), which needs only
        CAPTION_DWELL_AFTER_LAST_S on screen and costs no lone word. Only an unsplittable hedge and figure group
        still shrinks whole (fact safety).
    A1  no burst ends on THE, A or AN, save an article right before a hedge and figure group that cannot hold it
        (THEY REMOVED AN | ESTIMATED 200,000 TONS).
    P1  after clause or sentence punctuation a pause of CAPTION_PAUSE_BREAK_S or more is a hard break, unless the
        break would leave a lone function word or a burst under CAPTION_DWELL_AFTER_LAST_S on either side (then a
        defect); a break at a pause of CAPTION_PAUSE_SOFT_S or more is rewarded; a burst whose clause punctuation is
        followed by two words or fewer of the next clause mid-sentence is a defect (PLANTS, THE TEXT).
    M1  a mid-sentence burst ending on a capitalised word followed by a lower-case content word is a defect
        (POLISH | BOOK DEALER).
    D1  a burst under CAPTION_SHORT_BURST_S on screen is avoided after the defects and lone words.

    `base_cuts` (fix round 1, operator decision 2026-11-04, the HEAD-regression gate): the word positions after which
    the round-4 phrasing ends a burst (round4_burst_cuts). A split's cuts outside it are counted right after the bursts
    over the bond floor and before every other cost, so a cut the base does not have is made only where the base's
    burst is not allowed (a bond, an article end, the floor, the width and character limits); a P1 pause break holds
    only where the base breaks too. `loose`: positions where an atomic pair does not hold (_base_yields).
    `held_soft` (fix round 2, the operator decision as written): base cuts at a figure link (a figure with its unit,
    SQUARE with its unit, a bracketed conversion, a date: _base_figure_links). They are no bonds, but a split's cuts
    there are counted right after its new cuts, so a link HEAD 0036b18 cut is held wherever that adds no cut the base
    lacks (TO THE YEAR 196 AD) and keeps HEAD's cut where holding it would force one (MORE THAN 830 SQUARE | MILES).
    """
    if bonds is None:
        bonds = caption_bonds(words)
    reel = fits is None   # the Reel's own limits (freeze pass C3/C4): a burst past them only when bonded
    if fits is None:
        fits = _fits_caption_burst
        fits_bonded = _fits_caption_bonded
        prefer = prefer or _fits_caption_width
    fits_bonded = fits_bonded or fits
    prefer = prefer or fits
    loose = loose or set()
    held_soft = held_soft or set()
    if reel:
        bonds = _relax_figure_bonds(words, bonds, base_cuts, loose)
    raw_all = [str(w.get("word", "")) for w in words]
    named = name_bonds(raw_all) & bonds
    timed_words = all("start" in w for w in words)
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
        raw = [str(x.get("word", "")) for x in sent]
        next_start = (float(words[off + n]["start"]) if off + n < len(words) and timed_words else None)
        memo: Dict[int, Optional[List[List[Dict[str, Any]]]]] = {}

        def linked(pos: int) -> bool:   # a bond or an atomic pair at the boundary after word pos (freeze pass C3)
            k = pos - off
            return pos in bonds or (0 <= k < n - 1 and pos not in loose and _is_atomic_boundary(raw[k], raw[k + 1]))

        def joined(pos: int) -> bool:
            """linked, or a held_soft figure link (fix round 2): a burst holding it whole is a bonded group (past the
            word limit, at the bond floor) though a cut there is allowed."""
            return linked(pos) or pos in held_soft

        def on_screen(p: int, q: int) -> float:
            """How long the burst of local words p..q stays up (burst_timings, without the lead)."""
            if not timed_words:
                return 99.0
            first, last_end = float(sent[p]["start"]), _word_end(sent[q])
            nxt = float(sent[q + 1]["start"]) if q + 1 < n else next_start
            if nxt is None:
                return last_end + 0.10 - first
            return nxt - first if nxt - last_end <= CAPTION_PAUSE_GAP else last_end + CAPTION_PAUSE_TAIL - first

        def lead_in_burst(p: int, q: int) -> bool:
            """N1: a one-word lead-in burst (UNDER) right before a name burst, up long enough to be read."""
            if q != p or q + 1 >= n or _bare(raw[p]) not in LEAD_IN_WORDS or _bare(raw[p]) in _ARTICLE_SLOT:
                return False
            g = off + q + 1   # the name burst opens with the name or its article
            opens_name = (g in named) or (g in bonds and g + 1 in named and _bare(raw[q + 1]) in _ARTICLE_SLOT)
            return opens_name and on_screen(p, q) >= CAPTION_DWELL_AFTER_LAST_S - 1e-9

        def two_line(p: int, q: int) -> bool:
            """N1 (c): local words p..q are a 9:16 name burst too wide for one line at CAPTION_BOND_MIN_SIZE that
            draws on two lines: [a lead-in] [an article] and a whole name, ending at the name's end."""
            if not reel or q <= p or not _name_end_at(named, off + q):
                return False
            s = _name_start_of(named, off + q) - off
            if s < p or s - p > 2:
                return False
            prefix = [_bare(r) for r in raw[p:s]]
            if any(x not in LEAD_IN_WORDS for x in prefix) or (len(prefix) == 2 and prefix[1] not in _ARTICLE_SLOT):
                return False
            # a lead-in on line 1 costs LONE_WORD_PENALTY (below): it goes there where on its own it would be up under
            # CAPTION_DWELL_AFTER_LAST_S (no lead-in burst then: lead_in_burst), or a flash under CAPTION_SHORT_BURST_S
            core = p + 1 if prefix and prefix[0] not in _ARTICLE_SLOT else p   # the article and the name
            return not fits_bonded(sent[core:q + 1]) and _two_line_layout(sent[p:q + 1]) is not None

        def hard_inside(p: int, q: int) -> tuple[bool, int]:
            """(forbidden, defects) for the P1 pause breaks inside local words p..q."""
            defects = 0
            if not timed_words:
                return False, 0
            for k in range(p, q):
                if not _pause_break(sent, k) or (base_cuts is not None and off + k not in base_cuts):
                    continue
                left_lone = k == p and _function_only(sent[p:p + 1])
                right_lone = k + 1 == q and _function_only(sent[q:q + 1])
                if left_lone or right_lone or on_screen(k + 1, q) < CAPTION_DWELL_AFTER_LAST_S - 1e-9:
                    defects += 1   # the break would leave a lone function word or a flash: kept, as a defect
                    continue
                return True, 0
            return False, defects

        def article_end_ok(q: int) -> bool:
            """A1: a burst may end on an article only right before a hedge and figure group that cannot hold it."""
            if q + 1 >= n or _bare(raw[q]) not in _ARTICLES or not joined(off + q + 1):
                return q + 1 >= n or _bare(raw[q]) not in _ARTICLES
            e = q + 1
            while e < n - 1 and joined(off + e):
                e += 1
            group = sent[q + 1:e + 1]
            if not any(c.isdigit() for x in group for c in str(x.get("word", ""))):
                return False
            return not fits_bonded(sent[q:e + 1])

        def chunk_defects(p: int, q: int, last: bool) -> int:
            """The defects of the burst p..q beyond _split_defects' (freeze pass round 2)."""
            d = hard_inside(p, q)[1]
            if not last:
                # P1 (3): clause punctuation inside, then two words or fewer of the next clause
                for k in range(p, q):
                    if CLAUSE_PUNCT.search(raw[k]) and q - k <= 2:
                        d += 1
                        break
            return d

        def proper_modifier_end(p: int, q: int, last: bool) -> bool:
            """M1 (soft): a mid-sentence burst ending on a capitalised word with a lower-case content word right
            after it (POLISH | BOOK DEALER, BRITISH | AIRCRAFT)."""
            if last or q + 1 >= n or q == 0:
                return False
            core = raw[q].strip("\"'“”‘’()[]")
            nxt = raw[q + 1]
            return (core[:1].isupper() and not CLAUSE_PUNCT.search(raw[q]) and nxt[:1].islower()
                    and _bare(nxt) not in FUNCTION_WORDS and not _name_end_at(named, off + q))

        def split_key(split: List[List[Dict[str, Any]]], i: int) -> tuple[int, int, int, int, int, int, int]:
            """(over, hard, soft, short, small) of a split from local word i (review round 4 tiers): bursts over the
            bond floor; the hard defects (_split_defects: a function-word burst 2, a mid-sentence end on a
            function word 1); the soft cost (split_soft_cost: a lone content word, a pre-modifier or possessive
            end, the P1 clause tail, the M1 proper-modifier and open noun phrase ends); bursts under
            CAPTION_SHORT_BURST_S; bursts at the bond floor. `new` (fix round 1): the cuts not in base_cuts; `cut_soft`
            (fix round 2): the cuts at a held_soft figure link."""
            over = new = cut_soft = hard = soft = short = small = 0
            p = i
            for k, chunk in enumerate(split):
                if not chunk:
                    continue
                q = p + len(chunk) - 1
                last = k == len(split) - 1
                lead = lead_in_burst(p, q)
                tl = two_line(p, q)
                if len(chunk) > 1 and reel and not tl and not fits_bonded(chunk):
                    over += 1
                if not last and base_cuts is not None and off + q not in base_cuts:
                    new += 1
                if not last and off + q in held_soft:
                    cut_soft += 1
                if last and base_cuts is not None and off + q not in base_cuts and off + n < len(words):
                    # fix round 2: the base joins this sentence's end onto the next sentence's first burst
                    # (_rejoin_base): a last burst too long to join it again leaves a cut the base lacks (MORE THAN
                    # 590 SQUARE FEET. | SHIPPED against HEAD's MORE THAN 590 | SQUARE FEET. SHIPPED)
                    c = min((b for b in base_cuts if b >= off + n), default=len(words) - 1)
                    tail = list(chunk) + words[off + n:c + 1]
                    if not (len(tail) <= max_words and len(" ".join(_phrase_words(tail))) <= max_chars
                            and fits(tail)):
                        new += 1
                if _function_only(chunk) and not lead and len(split) > 1:
                    # fix round 1: a lone function word (FROM, THE) only where nothing else is left
                    over += len(chunk) == 1
                    hard += 2 * (len(chunk) > 1)
                elif not last and not lead and _hard_dangles(raw[q]):
                    hard += 1
                elif not last and not lead and (_dangles(raw[q]) or _attributive_end(raw, q)):
                    soft += SOFT_END_COST
                soft += chunk_defects(p, q, last) * SOFT_END_COST
                soft += proper_modifier_end(p, q, last) + _open_phrase_end(raw, p, q, last)
                if len(chunk) == 1 and len(split) > 1 and not lead:
                    soft += LONE_WORD_COST
                elif lead:
                    soft += LEAD_IN_COST   # a one-word lead-in flash only where the name has no line for it
                if on_screen(p, q) < CAPTION_SHORT_BURST_S - 1e-9 and len(split) > 1:
                    short += 1
                if reel and len(chunk) > 1 and not fits(chunk):
                    small += 1
                p = q + 1
            return over, new, cut_soft, hard, soft, short, small

        def _solve(i: int) -> Optional[List[List[Dict[str, Any]]]]:
            if i == n:
                return []
            if i in memo:
                return memo[i]
            rem = n - i
            rem_clean = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in sent[i:]]
            rem_txt = " ".join(rem_clean)
            if (min_words <= rem <= max_words and len(rem_txt) <= max_chars and prefer(sent[i:])
                    and not hard_inside(i, n - 1)[0] and not hard_inside(i, n - 1)[1]
                    and not chunk_defects(i, n - 1, True)):
                memo[i] = [sent[i:]]
                return memo[i]

            best: Optional[List[List[Dict[str, Any]]]] = None
            best_score: tuple[int, ...] = (-999999,) * 8
            candidate_sizes = [target_words, min_words, 4, 5, 6] + list(range(7, max_words + 1))
            # never past max_words (round 3: sizes 4-6 were tried whatever the cap, so a 4-word cap formed 5)
            candidate_sizes = [c for c in candidate_sizes if c <= max_words] or [min(target_words, rem)]
            # round 4: past max_words only through bonded words (BETWEEN 94 AND 104 METRES is one burst); freeze pass
            # round 2 (N2): a whole name may run to NAME_MAX_WORDS with its article and a lead-in
            candidate_sizes += [c for c in range(max_words + 1, min(rem, max_words + NAME_MAX_WORDS + 2) + 1)
                                if all(joined(off + i + k - 1) for k in range(max_words, c))
                                or two_line(i, i + c - 1)]
            # A lone content word is a candidate too (review, 2026-11-03): it costs LONE_WORD_PENALTY, so it is
            # only formed where every split of min_words or more has more defects (_split_defects), e.g.
            # BUILT | UNDER KING KRISHNA I rather than BUILT UNDER | KING KRISHNA I. N1: a one-word lead-in too.
            if min_words > 1 and (not _function_only(sent[i:i + 1]) or lead_in_burst(i, i)):
                candidate_sizes.append(1)
            if timed_words:   # P1: a burst may end at a pause after punctuation whatever its length (IN 1941, | ...)
                candidate_sizes += [k - i + 1 for k in range(i, min(n - 1, i + max_words)) if _pause_break(sent, k)]
            # a second pass, every size, where the usual ones leave no split (PILGRIMS CROWD | THE BASILICA ...)
            for sizes in (candidate_sizes, list(range(1, min(rem, max_words + NAME_MAX_WORDS + 2) + 1))):
                if best is not None:
                    break
                for size in dict.fromkeys(sizes):
                    if i + size > n:
                        continue
                    q = i + size - 1
                    if i + size < n and linked(off + q):
                        continue
                    if not article_end_ok(q):
                        continue   # A1: never a burst ending on THE, A or AN
                    rem_after = n - (i + size)
                    if sizes is candidate_sizes and rem_after > 0 and rem_after < min_words and (
                            rem_after > 1 or (_function_only(sent[n - 1:]) and not lead_in_burst(n - 1, n - 1))):
                        continue
                    if hard_inside(i, q)[0]:
                        continue   # P1: a pause after punctuation inside the burst
                    lead = lead_in_burst(i, q)
                    if size == 1 and size < n and _function_only(sent[i:i + 1]) and not lead:
                        continue
                    after = _solve(i + size)
                    if after is None:
                        continue
                    chunk = sent[i:i + size]
                    if size < n and _function_only(chunk) and not lead:
                        continue  # freeze pass C3: never a burst of function words only (OF THE, UNDER THE)
                    clean_chunk = [re.sub(r"[\,\;\.]+$", "", x.get("word", "")) for x in chunk]
                    txt = " ".join(clean_chunk)
                    # every boundary inside the chunk is a bond or an atomic pair: one name or figure group
                    whole_bond = size > 1 and all(joined(off + i + k) for k in range(size - 1))
                    tl = two_line(i, q)
                    figure_group = whole_bond and any(c.isdigit() for c in txt)
                    # N1/N2: a whole name with its article (and a lead-in) on one line at the bond floor, past the
                    # word and character limits (THE GRAND HALL OF THE UNION)
                    name_group = whole_bond and any(off + i + k in named for k in range(size - 1))
                    if reel and (size > max_words or len(txt) > max_chars) and not tl:
                        # past the Reel's limits only as a bonded burst bonded_burst_allowed admits, or a whole
                        # figure group within the extra words and characters (a hedge never leaves its figure)
                        raw_chunk = [x.get("word", "") for x in chunk]
                        if not (all(joined(off + i + k) for k in range(min_words - 1, size - 1))
                                and (bonded_burst_allowed(raw_chunk) or (name_group and fits_bonded(chunk)) or (
                                    figure_group and size <= max_words + CAPTION_BOND_EXTRA_WORDS
                                    and len(txt) <= max_chars + CAPTION_BOND_EXTRA_CHARS))):
                            continue
                    if len(chunk) > 1 and not fits(chunk) and not (
                            all(joined(off + i + k) for k in range(min_words - 1, size - 1))
                            and fits_bonded(chunk)) and not tl and not (reel and figure_group):
                        continue  # a burst its pill cannot hold inside the platforms' safe width is never a choice
                    first_w = re.sub(r"[^\w]", "", clean_chunk[0]).lower()
                    last_w = re.sub(r"[^\w]", "", clean_chunk[-1]).lower()
                    score = 100
                    if len(txt) <= max_chars:
                        score += 50
                    elif not tl:
                        score -= (len(txt) - max_chars) * 20
                    if not lead and (last_w in DANGLING_TAILS
                                     or (i + size < n and (_dangles(chunk[-1].get("word", ""))
                                                           or _attributive_end(raw, q)))):
                        score -= DANGLING_PENALTY  # dangling prepositions/determiners/conjunctions at block ends
                    if _function_only(chunk) and not lead:
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
                    if i + size < n:
                        # freeze pass C8 (2026-11-04): a break before a phrase opener (a preposition, article,
                        # conjunction or relative word) by one unit; one after a comma the voice pauses on
                        # (CAPTION_COMMA_PAUSE_S) as much as a shrunk burst costs, unless the burst would be up
                        # under CAPTION_SHORT_SENTENCE_MIN_S or the next one a lone function word
                        nxt = sent[i + size]
                        if _bare(nxt.get("word", "")) in DANGLING_TAILS:
                            score += 1
                        if "start" in nxt and "start" in chunk[0]:
                            gap = float(nxt["start"]) - _word_end(chunk[-1])
                            heard = _word_end(chunk[-1]) - float(chunk[0]["start"])
                            lone_fn = bool(after) and len(after[0]) == 1 and _function_only(after[0])
                            if re.search(r"[,;]$", chunk[-1].get("word", "")):
                                if (gap >= CAPTION_COMMA_PAUSE_S - 1e-9 and heard >= CAPTION_SHORT_SENTENCE_MIN_S
                                        and not lone_fn):
                                    score += SHRUNK_BURST_PENALTY
                            elif gap >= CAPTION_PAUSE_SOFT_S - 1e-9 and not lone_fn:
                                score += SHRUNK_BURST_PENALTY   # P1 (2): the voice pauses without punctuation
                    if len(chunk) > 1 and not prefer(chunk):
                        score -= SHRUNK_BURST_PENALTY  # shown below the full size: only to avoid a dangling end
                    if size < n and _subject_only(chunk):
                        score -= SUBJECT_ONLY_PENALTY   # IT WAS on its own, where the split's defects tie
                    if glance[0] <= len(txt) <= glance[1]:
                        score += 40   # Reward optimal glanceable width
                    if after and len(after[0]) == 1 and len(after) > 1 and not lead_in_burst(i + size, i + size):
                        # the rest could only start on a lone word (2026-11-03, 80 px: THEY REMOVED | AN |
                        # ESTIMATED 200,000 TONS); less than ending this burst on a dangling word
                        # (DANGLING_PENALTY; review 2026-11-03: at 250 against 200 it formed THEY REMOVED AN)
                        score -= LONE_WORD_PENALTY
                    if size == 1 and n > 1 and not lead:
                        score -= LONE_WORD_PENALTY   # this burst is the lone word
                    elif rem_after == 1 and min_words > 1:
                        score -= LONE_WORD_PENALTY   # the sentence would end on a lone word
                    if tl and _bare(raw[i]) not in _ARTICLE_SLOT and not (off + i in named):
                        score -= LONE_WORD_PENALTY   # N1: a lead-in on line 1 only where nothing else fits
                    # The split's defects come first (review, 2026-11-03): a burst of function words only, or
                    # one ending on an article, preposition or conjunction mid-sentence, anywhere in the
                    # sentence's rest, so no split that has fewer is passed over for this burst's own score
                    # (BUILT UNDER | KING KRISHNA I | OF THE | ..., WHO CARVED | THE STATUES OR WHY | ...).
                    # Then the fewest lone words: a lone content word only where it saves a defect. Then the fewest
                    # bursts on screen under CAPTION_SHORT_BURST_S (D1), then at the bond floor (C4).
                    split = [chunk] + after
                    over, new, cut_soft, hard, soft, short, small = split_key(split, i)
                    key = (-over, -new, -cut_soft, -hard, -soft, -short, -small, score)
                    if key > best_score:
                        best_score = key
                        best = split
            if best is None:
                def _held(c: int) -> bool:   # the cut after c words splits a bond or an atomic pair (KRISHNA | I)
                    return i + c < n and linked(off + i + c - 1)

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
                rest = _solve(i + cut) if i + cut < n else []
                best = [sent[i:i + cut]] + (rest if rest is not None else [sent[i + cut:]])
            memo[i] = best
            return best

        if n <= min_words and (n == 1 or fits(sent)) and not hard_inside(0, n - 1)[0]:
            return [sent]
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


_LIBASS_REF_PX = 1000   # the em the drawn measure reads glyph metrics at, scaled linearly as libass scales outlines


def unkerned_length(font: Any, text: str) -> float:
    """The advance of `text` as libass draws the captions (operator decision 2026-11-05, "measure without
    kerning"): the ASS header sets no "Kerning: yes", so libass shapes the captions unkerned, while Pillow's raqm
    layout applies the font's GPOS kerning (WAVY VAULT 7,000 measured 545 px kerned against the 552 px libass
    draws). QF-21: the whole run is shaped once with the features libass draws it with (font_engine.LIBASS_FEATURES:
    no kern, no optional ligatures, no case forms) instead of summing single letters, so a combining mark stays on
    its letter."""
    from .font_engine import LIBASS_FEATURES, choose_face, shape, shape_in_face
    face = choose_face(text, font)
    if face is None:
        # QF-94: a caption word no bundled face draws (an Arabic name in the narration) never stops the render.
        # libass draws the captions and takes a system face for such characters, as before QF-94; the word is
        # measured in the caption face as it stands (missing-glyph advances). A measure records nothing (the
        # caption measures are cached across runs): build_subtitles records the word (_record_caption_faces).
        return shape_in_face(text, font, features=LIBASS_FEATURES).advance
    return shape(text, face, features=LIBASS_FEATURES).advance


def _record_caption_faces(words: list[Any], font_name: str, upper: bool) -> None:
    """Each caption word no bundled face draws, recorded once per run on the surface "captions" (QF-94) for the
    review card: libass draws it in a system face, and the measures (unkerned_length, _glyph_box) take it in the
    caption face itself, never refusing it. Recorded here, once per build, because those measures are cached."""
    from .font_engine import choose_face, missing_glyphs, record_glyph_event, surface
    from .typography import get_font
    font = get_font(font_name, _LIBASS_REF_PX)
    with surface("captions"):
        for w in words:
            word = w.get("word", "") if isinstance(w, dict) else str(w)
            text = _ass_escape(str(word)).strip()
            text = text.upper() if upper else text
            if text and choose_face(text, font) is None:
                record_glyph_event("caption_system_face", text, Path(str(font.path)).name, missing_glyphs(text, font))


def _reel_ass_size(size: float) -> float:
    """The ASS Fontsize build_subtitles writes for a 9:16 caption at nominal size `size`."""
    return FONT_SIZE_ASS if size >= FONT_SIZE else round(size / 0.5625)


@lru_cache(maxsize=512)
def _glyph_box(ch: str) -> tuple[int, int, int, int] | None:
    """The ink box of one caption glyph at _LIBASS_REF_PX (cached: rendering it is the measure's slow part).
    Boxed in the face unkerned_length measures it in (QF-94): the caption face, or the bundled fallback face that
    draws it; a glyph no bundled face draws in the caption face itself (its missing-glyph box, as before QF-94),
    never refused: libass draws it in a system face (recorded by _record_caption_faces)."""
    from .font_engine import LIBASS_FEATURES, choose_face, plain_face
    from .typography import get_font
    font = get_font(_caption_family(), _LIBASS_REF_PX)
    face = choose_face(ch, font)
    if face is None:
        return plain_face(font).getmask(ch, features=list(LIBASS_FEATURES)).getbbox()
    return face.getmask(ch, features=list(LIBASS_FEATURES)).getbbox()


@lru_cache(maxsize=8192)
def _libass_line(words: tuple[str, ...], size: float, sc_scale: int = CAPTION_SC_SCALE) -> tuple[float, float, float]:
    """(advance, ink left, ink right) in px of a 9:16 caption line as libass draws it (freeze pass C5, 2026-11-04):
    the em of its ASS size, the CapText Spacing after every glyph, and the ink of the first and last glyphs.
    QF-87: the line is one run in the caption face (_caption_family: Anton's own small-capital glyphs as the
    default forms of its lower case), shaped once with the features libass draws it with (LIBASS_FEATURES: no kern,
    operator decision 2026-11-05; no optional ligatures, no contextual or case forms), and its ink is the chosen
    glyphs' own rasters at their shaped positions (font_engine.run_ink_bounds). No capital is scaled down (the
    round-2 82% fscx runs are gone). A line the caption face cannot map whole is shaped in the bundled face that
    can (QF-94, choose_face), or in the caption face itself when none can (libass takes a system face for it,
    recorded by _record_caption_faces). Unhinted: glyph metrics at a large em, scaled linearly."""
    from .font_engine import run_ink_bounds
    k = _libass_scale(size)
    run, face = _caption_line_run(words)
    box = run_ink_bounds(run, face)
    if not box:
        return run.advance * k, 0.0, 0.0
    return (run.advance * k + REEL_LETTER_SPACING * len(run.glyphs),
            box[0] * k,
            box[2] * k + REEL_LETTER_SPACING * max(0, len(run.glyphs) - 1))


def _libass_scale(size: float) -> float:
    """px at a 9:16 caption of nominal size `size` per px of the _LIBASS_REF_PX reference measure."""
    upm, asc, desc = _font_vmetrics(_caption_family())
    return _reel_ass_size(size) * upm / (asc + desc) / _LIBASS_REF_PX


@lru_cache(maxsize=8192)
def _caption_line_run(words: tuple[str, ...]):
    """(GlyphRun, face) of one 9:16 caption line at _LIBASS_REF_PX, as _libass_line describes it: one run in the
    caption face (or the bundled face that maps it whole, QF-94; else the caption face itself), LIBASS_FEATURES."""
    from .font_engine import LIBASS_FEATURES, choose_face, shape, shape_in_face
    from .typography import get_font
    font = get_font(_caption_family(), _LIBASS_REF_PX)
    text = " ".join(_caption_display_text(word) for word in words)
    text = text if CAPTION_SMALL_CAPS else text.upper()
    face = choose_face(text, font)
    if face is None:
        return shape_in_face(text, font, features=LIBASS_FEATURES), font
    return shape(text, face, features=LIBASS_FEATURES), face


@lru_cache(maxsize=8192)
def _caption_line_ink(words: tuple[str, ...], size: float) -> tuple[float, float]:
    """(ink top, ink bottom) in px of one 9:16 caption line at caption size `size`, from its baseline (up is
    negative): the shaped glyphs' own ink, so a line of small capitals is shorter than a line with an accented
    capital (QF-87)."""
    from .font_engine import run_ink_bounds
    run, face = _caption_line_run(words)
    box = run_ink_bounds(run, face)
    if box is None:
        raise ValueError(f"A caption line has no ink: {' '.join(words)!r}")
    k = _libass_scale(size)
    return box[1] * k, box[3] * k


def _caption_two_line_geometry(parts: list[list[str]], size: float) -> tuple[int, int, float]:
    """(pitch, plate height, lower-line offset) of a two-line 9:16 burst at caption size `size` (QF-24/QF-87
    layout decision 2026-11-09). The baselines are CAPTION_TWO_LINE_PITCH_EM x the cap height apart, or further
    when the measured ink of the two lines (an accented capital under a descender) would come closer than
    CAPTION_TWO_LINE_INK_GAP px. The plate is the one-line plate plus that minimum pitch, or taller when the ink
    block needs it with REEL_PAD_Y above and below; the ink block is centred in it. The offset is from the plate's
    bottom edge to the lower line's an5 anchor (its line box's centre)."""
    upper, lower = (_caption_line_ink(tuple(part), float(size)) for part in parts)
    min_pitch = round(CAPTION_TWO_LINE_PITCH_EM * reel_cap_height(size))
    pitch = max(min_pitch, math.ceil(upper[1] - lower[0] + CAPTION_TWO_LINE_INK_GAP))
    ink_h = pitch + lower[1] - upper[0]
    box_h = max(REEL_PLATE_H + min_pitch, math.ceil(ink_h + 2 * REEL_PAD_Y))
    pad = (box_h - ink_h) / 2
    _, asc, desc = _font_vmetrics(_caption_family())
    ass_size = _reel_ass_size(size)
    baseline = asc * ass_size / (asc + desc) - ass_size / 2   # the baseline below the line box's centre
    return pitch, box_h, -pad - lower[1] - baseline


def _caption_ink_width(words: list[str], size: float, sc_scale: int = CAPTION_SC_SCALE) -> float:
    """The ink width in px libass draws for these words at caption size `size` (_libass_line)."""
    _, left, right = _libass_line(tuple(words), float(size), sc_scale)
    return right - left


def _caption_ink_offset(words: list[str], size: float) -> float:
    """How far right of the an5 anchor the drawn ink's centre falls (px): the plate is centred on the ink."""
    adv, left, right = _libass_line(tuple(words), float(size))
    return (left + right) / 2 - adv / 2


def _ink_budget(max_width: int = MAX_CAPTION_WIDTH) -> float:
    """The widest ink a 9:16 burst may draw: its plate (max_width plus REEL_PAD_X a side, the platforms' free
    width) less CAPTION_INK_FIT_PAD a side (640 px). The plate pads the ink by CAPTION_INK_FIT_PAD (28) everywhere
    (operator decision K1, 2026-11-04; the 40 px CAPTION_INK_PAD_MIN is withdrawn and no plate uses it)."""
    return max_width + 2 * REEL_PAD_X - 2 * CAPTION_INK_FIT_PAD


def _caption_text_width(words: list[str], size: int, sc_scale: int = CAPTION_SC_SCALE, drawn: bool = False) -> float:
    """Width in px of these words at caption size `size`.

    Without `drawn`, the round-2 reference measure (Anton capitals, the narration's lower case as 82% capitals,
    PIL at that size): the word bonds and baseline cuts the burst builder keeps (the HEAD 0036b18 gate, operator
    decision 2026-11-04) are defined on it, so a change of the drawn face (QF-87) moves no cut. No renderer draws
    it. With `drawn` it is the ink width libass draws in the caption face (_libass_line: the genuine small-capital
    glyphs), which the burst builder fits and the plate is sized from."""
    if drawn:
        return _caption_ink_width(list(words), size, sc_scale)
    from .typography import get_font
    f_base = get_font("Anton", max(8, round(size)))
    f_sc = get_font("Anton", max(16, round(size * (sc_scale / 100.0))))
    total = 0.0
    for wi, word in enumerate(words):
        if wi > 0:
            total += unkerned_length(f_base, " ")
        if not CAPTION_SMALL_CAPS:
            total += unkerned_length(f_base, _ass_escape(word).upper())
            continue
        for text, full in caption_runs(_ass_escape(word), sc_scale):
            total += unkerned_length(f_base if full else f_sc, text)
    return total


def measure_caption_phrase(phrase: Any, fit_rendered: int, sc_scale: int = CAPTION_SC_SCALE) -> tuple[int, int]:
    """(ink width, cap height) in px of a caption phrase as libass draws it at `fit_rendered` (freeze pass C5). QF-87
    review 1: the shaped glyphs' outline extent, in whole pixels rounded down: libass's visible ink (its edge pixels
    under about 40% coverage do not show) is 0 to 0.8 px wider than the outline extent on the ten pinned samples and
    0.6 px narrower on WAVY Vault 7,000, so the extent rounded down is never wider than the drawn ink."""
    total_w = _caption_text_width(_phrase_words(phrase), fit_rendered, sc_scale, drawn=True)
    # The capitals' drawn height: the line is upper case (full and small capitals), so the plate is padded around
    # the cap band and the letters sit optically centred (round 3; was a fixed 75 px whatever the size).
    text_h = max(30, round(reel_cap_height(fit_rendered)))
    return math.floor(total_w), text_h


CAPTION_MIN_SIZE = 60  # px: the smallest a single over-long word shrinks to (round 2: 48 made bursts jump 33%)
CAPTION_HARD_MIN = 36  # px: below CAPTION_MIN_SIZE only for a word that would otherwise leave the safe width


def _caption_fit_size(phrase: Any, scale: float = 1.0, max_width: int = MAX_CAPTION_WIDTH) -> int:
    """Largest caption size <= FONT_SIZE at which `phrase` (shown at up to `scale`) draws inside the ink budget
    (_ink_budget: CAPTION_INK_FIT_PAD clear of each side of the widest plate).

    Freeze pass C4 (2026-11-04): a burst of several words takes the largest rung of the ladder FONT_SIZE (80) ->
    CAPTION_BURST_MIN_SIZE (74) -> CAPTION_BOND_MIN_SIZE (66) that fits; the phrase builder forms a burst that is
    no bond only where it fits at 74, so only bonded bursts draw at 66. A single word too long for the line shrinks
    down to CAPTION_MIN_SIZE (and below it only as far as the safe width requires).
    QF-87: a burst reel_caption_lines draws on two lines takes that layout's rung."""
    words = _phrase_words(phrase)
    budget = _ink_budget(max_width)
    if _caption_ink_width(words, FONT_SIZE) * scale <= budget:
        return FONT_SIZE
    layout = reel_caption_lines(phrase) if max_width == MAX_CAPTION_WIDTH and len(words) > 1 else None
    if layout is not None:
        return layout[1]
    if len(words) > 1:
        for rung in (CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE):
            if _caption_ink_width(words, rung) * scale <= budget:
                return rung
        lines = reel_caption_lines(phrase) if max_width == MAX_CAPTION_WIDTH else None
        if lines is not None:   # freeze pass N1: a name too wide for one line draws on two at a ladder rung
            return lines[1]
        floor = CAPTION_BOND_MIN_SIZE   # only an unsplittable hedge and figure group still shrinks whole
    else:
        floor = CAPTION_MIN_SIZE
    size = min(FONT_SIZE - 1, max(floor, int(FONT_SIZE * budget / (_caption_ink_width(words, FONT_SIZE) * scale))))
    while size > CAPTION_HARD_MIN and _caption_ink_width(words, size) * scale > budget:
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
    round 2, finding 1); the pill must also stay inside the area the platforms leave free (safe_zones.REEL).
    QF-87: or it draws on two lines at FONT_SIZE (reel_caption_lines)."""
    if _caption_ink_width(_phrase_words(phrase), FONT_SIZE) <= _ink_budget(max_width):
        return True
    layout = reel_caption_lines(phrase) if max_width == MAX_CAPTION_WIDTH else None
    return bool(layout and layout[1] == FONT_SIZE)


def _fits_caption_burst(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a multi-word burst fits the caption width at CAPTION_BURST_MIN_SIZE, the most a burst may shrink, or
    draws on two lines (reel_caption_lines, QF-87)."""
    if _caption_ink_width(_phrase_words(phrase), CAPTION_BURST_MIN_SIZE) <= _ink_budget(max_width):
        return True
    return max_width == MAX_CAPTION_WIDTH and reel_caption_lines(phrase) is not None


def _fits_caption_bonded(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """Whether a burst no cut can divide without splitting a bond (a hedge or range with its figure) fits at
    CAPTION_BOND_MIN_SIZE (partition_words_grammatical), or draws on two lines (reel_caption_lines, QF-87)."""
    if _caption_ink_width(_phrase_words(phrase), CAPTION_BOND_MIN_SIZE) <= _ink_budget(max_width):
        return True
    return max_width == MAX_CAPTION_WIDTH and reel_caption_lines(phrase) is not None


CAPTION_LADDER = (FONT_SIZE, CAPTION_BURST_MIN_SIZE, CAPTION_BOND_MIN_SIZE)   # px: the 9:16 sizes a burst draws at


def _two_line_clears_graphics(size: float, height: int = VIDEO_HEIGHT) -> bool:
    """Whether a two-line 9:16 plate at caption size `size` (REEL_PLATE_H plus one baseline pitch, its bottom on the
    one-line plate's) stays inside the band reserved for captions (safe_zones.caption_band_top, which stands the
    40 px gap below safe_zones.graphics_box): operator decision 2026-11-04, a caption plate never rises over the
    graphics. Under the ads profile no rung cleared it (66 px: top y 1032 over a graphics floor at 1058); the
    organic profile's 240 px band (QF-24/QF-87 decision, 2026-11-09) holds a two-line plate at every rung (80 px:
    top y 1137 under a band top of 1130). A burst that would cross it is split instead."""
    one_line_top = height - CAPTION_MARGIN_V - CAPTION_REEL_CLEARANCE - REEL_PLATE_H
    two_line_top = one_line_top - round(CAPTION_TWO_LINE_PITCH_EM * reel_cap_height(size))
    return two_line_top >= _caption_band_top(VIDEO_WIDTH, height)


def _two_line_structure(shown: list[str], k: int, named: set[int]) -> tuple[int, int, int]:
    """How a break before word k reads (lower is better; QF-87 review 1): (it splits a proper name (name_bonds), line
    1 ends on a word that dangles (a preposition, a pre-modifier, a possessive: _dangles), line 1 is one function
    word alone)."""
    return ((k - 1) in named, _dangles(shown[k - 1]), k == 1 and _bare(shown[0]) in FUNCTION_WORDS)


@lru_cache(maxsize=4096)
def _two_line_options(words: tuple[str, ...]) -> tuple[tuple[tuple, int, int, float], ...]:
    """Every (rank, k, rung, wider line) where a 9:16 burst may break onto two lines: line 1 the first k words, both
    lines inside the ink budget at a CAPTION_LADDER rung whose two-line plate clears the graphics, line 1 never ending
    on an article nor between a hedge and its figure or an atomic pair. Sorted best first: the larger rung (a caption
    never shrinks to keep a name whole: lander ruling 2026-11-13, QF-87 round 3), then at that rung
    (_two_line_structure) a break that keeps a proper name whole, then a line 1 that does not end on a dangling word
    (a preposition, a pre-modifier) and is not one function word alone, then the narrower wider line, then a break
    before a connector (OF, DE)."""
    shown = _phrase_words(list(words))
    if len(shown) < 2:
        return ()
    named = name_bonds(list(shown))
    links = _figure_bonds(list(shown))
    budget = _ink_budget()
    out = []
    for rung in CAPTION_LADDER:
        if not _two_line_clears_graphics(rung):
            continue   # fix round 1: never above the caption band (under the graphics box); split instead
        for k in range(1, len(shown)):
            if _bare(shown[k - 1]) in _ARTICLES or _is_atomic_boundary(shown[k - 1], shown[k]) or (k - 1) in links:
                continue
            wide = max(_caption_ink_width(shown[:k], rung), _caption_ink_width(shown[k:], rung))
            if wide > budget:
                continue
            name, dangles, lone = _two_line_structure(shown, k, named)
            rank = (-rung, name, dangles, lone, round(wide), 0 if _bare(shown[k]) in NAME_PARTICLES else 1)
            out.append((rank, k, rung, wide))
    return tuple(sorted(out))


@lru_cache(maxsize=4096)
def _two_line_cached(words: tuple[str, ...]) -> tuple[int, int] | None:
    options = _two_line_options(words)
    return (options[0][1], options[0][2]) if options else None


def _two_line_layout(words: list[Any]) -> tuple[int, int] | None:
    """(k, size) for a 9:16 name burst drawn as two lines (freeze pass N1, operator decision 2026-11-04): line 1 is
    the first k words, both lines' ink within the ink budget (_ink_budget) at `size`, the largest rung of
    CAPTION_LADDER where some word-boundary break fits; the break minimises the wider line. Line 1 never ends on an
    article and the break never falls inside a hedge and its figure or an atomic pair; on a tie it goes before a
    connector (OF, DE, DELLA). None where no break fits at CAPTION_BOND_MIN_SIZE. QF-87 rounds 2-3: the break is
    ranked by size first, then phrase structure (_two_line_options): at the largest rung where any break fits, a name,
    a modifier and its noun, a function word and what follows stay on one line where a break there allows it."""
    raw = tuple(w if isinstance(w, str) else str(w.get("word", "")) for w in words)
    return _two_line_cached(raw)


def reel_caption_lines(phrase: Any) -> tuple[int, int] | None:
    """(k, size) where a 9:16 burst draws on two lines (line 1 its first k words), else None.

    Freeze pass N1 drew only a whole proper name this way. QF-87 (the QF-24/QF-87 layout, operator decision
    2026-11-09): the genuine small capitals are wider than the 82% capitals they replace, so a complete bounded
    burst too wide for one line at CAPTION_BURST_MIN_SIZE draws on two lines instead of splitting: at most
    MAX_BURST_WORDS words and MAX_BURST_CHARS characters (a figure group CAPTION_BOND_EXTRA_WORDS/CHARS more; a whole
    name with at most an article and one lead-in, any length), on the 80/74/66 ladder (66 only for a burst with a
    name or a figure). The break is _two_line_layout's; a figure group whose bonds leave no such break may break
    only before a range joiner (TO, AND, OR), a bracketed conversion or after its hedge, never between a number and
    its unit. The rung is the largest at which both lines fit the ink budget and the plate, at its measured
    two-line height (_caption_two_line_geometry), stays inside the caption band. Every other burst is one line."""
    raw = tuple(w if isinstance(w, str) else str(w.get("word", "")) for w in (
        phrase.split() if isinstance(phrase, str) else phrase))
    return _reel_caption_lines(raw)


@lru_cache(maxsize=8192)
def _reel_caption_lines(raw: tuple[str, ...]) -> tuple[int, int] | None:
    words = list(raw)
    if len(words) < 2:
        return None
    budget = _ink_budget()
    if _caption_ink_width(_phrase_words(words), CAPTION_BURST_MIN_SIZE) <= budget:
        return None
    named = name_bonds(words)
    end = len(words) - 1
    start = _name_start_of(named, end) if _name_end_at(named, end) else len(words)
    prefix = [_bare(w) for w in words[:start]]
    whole_name = (start < len(words) and len(prefix) <= 2 and all(w in LEAD_IN_WORDS for w in prefix)
                  and (len(prefix) < 2 or prefix[1] in _ARTICLE_SLOT))
    links = _figure_bonds(words)
    figure = any(c.isdigit() for w in words for c in w)
    whole_figure = figure and all(k in links or _is_atomic_boundary(words[k], words[k + 1]) for k in range(end))
    minimum = CAPTION_BOND_MIN_SIZE if whole_figure or whole_name else CAPTION_BURST_MIN_SIZE
    extra, extra_chars = (CAPTION_BOND_EXTRA_WORDS, CAPTION_BOND_EXTRA_CHARS) if whole_figure else (0, 0)
    if not whole_name and (len(words) > MAX_BURST_WORDS + extra
                           or len(" ".join(words)) > MAX_BURST_CHARS + extra_chars):
        return None
    shown = _phrase_words(words)
    floor = CAPTION_BOND_MIN_SIZE if named or figure else CAPTION_BURST_MIN_SIZE

    def plate_fits(k: int, rung: int) -> bool:
        return _caption_two_line_geometry([shown[:k], shown[k:]], rung)[1] <= _caption_band_height()

    # QF-87 rounds 2-3: the best-ranked break (the larger rung, then phrase structure, then width) whose plate the band
    # holds, at a rung no lower than the burst's floor; a burst whose breaks all need a rung under `minimum` is no
    # two-line burst (a whole figure group then tries its range or bracket break)
    options = [(k, rung) for _, k, rung, _ in _two_line_options(tuple(words)) if rung >= floor and plate_fits(k, rung)]
    if options and max(rung for _, rung in options) >= minimum:
        return options[0]
    split = _figure_two_line(words, minimum) if whole_figure else None
    if split is None:
        return None
    for rung in CAPTION_LADDER:
        if rung > split[1] or rung < floor:
            continue
        parts = [shown[:split[0]], shown[split[0]:]]
        if max(_caption_ink_width(part, rung) for part in parts) <= budget and plate_fits(split[0], rung):
            return split[0], rung
    return None


def _caption_band_height(height: int = VIDEO_HEIGHT) -> int:
    """The tallest 9:16 plate the caption band holds: from its top (safe_zones.caption_band_top) to the plate's
    bottom edge, CAPTION_REEL_CLEARANCE above the foot of the safe box (CAPTION_MARGIN_V)."""
    return height - CAPTION_MARGIN_V - CAPTION_REEL_CLEARANCE - _caption_band_top(VIDEO_WIDTH, height)


def _figure_two_line(words: list[str], minimum: int) -> tuple[int, int] | None:
    """(k, size) for a figure group no _two_line_layout break may divide (QF-87): line 2 starts at a range joiner
    (TO, AND, OR), a bracketed conversion or the figure after its hedge, at the largest CAPTION_LADDER rung from
    `minimum` whose two lines both fit the ink budget and clear the graphics; the break with the narrower wider
    line wins. A number never leaves its unit."""
    budget = _ink_budget()
    for rung in CAPTION_LADDER:
        if rung < minimum or not _two_line_clears_graphics(rung):
            continue
        choices = []
        for k in range(1, len(words)):
            after_hedge = (tuple(_norm(w) for w in words[:k]) in HEDGE_PHRASES
                           and any(c.isdigit() for c in words[k]))
            if _norm(words[k]) not in RANGE_JOINERS and not words[k].startswith("(") and not after_hedge:
                continue
            wide = max(_caption_ink_width(_phrase_words(words[:k]), rung),
                       _caption_ink_width(_phrase_words(words[k:]), rung))
            if wide <= budget:
                choices.append((wide, k))
        if choices:
            return min(choices)[1], rung
    return None


def _caption_display_text(word: str) -> str:
    """A caption word as the ASS file draws it: escaped and in Unicode NFC. Display only: the transcript's words,
    their times and the SRT/VTT sidecars keep the narration's own text. libass places a decomposed accent (E +
    U+0301) differently from its precomposed letter (É); NFC selects the face's own accented glyph, so both
    spellings draw the same ink, in the face's small capitals for a lower-case letter (QF-87)."""
    return _ass_escape(unicodedata.normalize("NFC", word))


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
    When enable_small_caps is True the word keeps the narration's case and accents (NFC, _caption_display_text):
    the caption face (font_assets.CAPTION_FAMILY) draws its lower case as Anton's own small-capital glyphs and its
    capitals full size, so no capital outline is scaled down (QF-87; it was 82% fscx runs). Stripping the override
    tags leaves the narration's word. Without small caps the word is upper-cased.
    With `ease_ms` > 0 the word being spoken now eases in from transparent over that many ms from the start of its
    event (round 3: it hard-cut on). Only that word: words already on the line never fade again, and no size pop
    (a \\fscx pop on one word of a centred line reflows the whole line).
    """
    clean = _caption_display_text(word)
    ease = f"{{\\alpha&HFF&\\t(0,{int(ease_ms)},\\alpha&H00&)}}" if is_spoken_now and ease_ms > 0 else ""
    # Freeze pass C11 (2026-11-04): gold is on the key term's letters and figures; its trailing punctuation is bone
    # K1: a quote mark before a gold word is bone too
    lead, core, tail = gold_parts(clean) if is_key and colour == COL_EMPHASIS else ("", clean, "")
    if not enable_small_caps:
        out = ease + (f"{{\\c{COL_PAST}}}{lead}" if lead else "") + f"{{\\c{colour}\\fscx100\\fscy100}}{core.upper()}"
        return out + (f"{{\\c{COL_PAST}}}{tail}" if tail else "")
    out = ease + (f"{{\\c{COL_PAST}\\fscx100\\fscy100}}{lead}" if lead else "")
    out += f"{{\\c{colour}\\fscx100\\fscy100}}{core}"
    if tail:
        out += f"{{\\c{COL_PAST}\\fscx100\\fscy100}}{tail}"
    return out


def gold_tail(word: str) -> tuple[str, str]:
    """(the word, its trailing punctuation) for a gold word: MEMNON? is gold MEMNON and a bone ?, while a comma
    or hyphen inside a figure (7,000, 13-FOOT) stays gold (freeze pass C11). A closing quote mark is punctuation
    too (K1: MERCURY" is gold MERCURY and a bone ")."""
    m = re.search(r"[,.;:?!\"'”’»)]+$", word)
    if not m or m.start() == 0:
        return word, ""
    return word[:m.start()], m.group()


def gold_parts(word: str) -> tuple[str, str, str]:
    """(opening quote marks, the gold core, trailing punctuation) of a gold word (freeze pass K1): straight and
    curly quote marks next to a gold word are bone, like its trailing punctuation."""
    m = re.match(r"^[\"'“‘«(]+", word)
    lead = m.group() if m and m.end() < len(word) else ""
    core, tail = gold_tail(word[len(lead):])
    return lead, core, tail


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


def _ends_sentence(phrase: list[dict[str, Any]]) -> bool:
    word = str(phrase[-1].get("word", ""))
    return bool(TERMINAL_PUNCT.search(word)) and not _is_abbreviation(word)


def _dwell_after_last(out: list[list[dict[str, Any]]], i: int, pause_gap: float, pause_tail: float,
                      lead: float) -> float:
    """How long burst `i` would stay whole after its last word appears (burst_timings, before any hold)."""
    p = out[i]
    last_start, last_end = float(p[-1]["start"]), _word_end(p[-1])
    if i + 1 < len(out):
        next_first = float(out[i + 1][0]["start"])
        if next_first - last_end <= pause_gap:
            return next_first - last_start
        return last_end + pause_tail - last_start + lead
    return last_end + 0.10 - last_start + lead


def _has_figure(phrase: list[dict[str, Any]]) -> bool:
    return any(c.isdigit() for w in phrase for c in str(w.get("word", "")))


def _join_breaks_rules(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """Whether joining two adjacent bursts of a sentence breaks a freeze-pass rule (round 2): a pause after
    punctuation between them (P1), clause punctuation followed by two words or fewer mid-sentence (P1), or an end
    mid-sentence on an article or a dangling word (A1, M1; a one-word lead-in is no such end on its own)."""
    combo = left + right
    if _pause_break(combo, len(left) - 1):
        return True
    raw = [str(w.get("word", "")) for w in combo]
    if _ends_sentence(right):
        return False
    if any(CLAUSE_PUNCT.search(raw[k]) and len(raw) - 1 - k <= 2 for k in range(len(raw) - 1)):
        return True
    return _bare(raw[-1]) in _ARTICLES or _dangles(raw[-1])


def merge_short_bursts(
    phrases: list[list[dict[str, Any]]],
    fits: Callable[[list[dict[str, Any]]], bool],
    max_words: int,
    min_dur: float = CAPTION_MIN_CHUNK,
    pause_gap: float = CAPTION_PAUSE_GAP,
    reading_rate: bool = True,
    pause_tail: float = CAPTION_PAUSE_TAIL,
    dwell: bool = False,
    dwell_fits: Callable[[list[dict[str, Any]]], bool] | None = None,
    max_chars: int | None = None,
    lead: float = CAPTION_LEAD_FRAMES / VIDEO_FPS,
) -> list[list[dict[str, Any]]]:
    """Joins each burst heard for less than `min_dur` to a neighbour in its sentence (round 2, finding 4: a flash is
    not read), the one before first. A join must still fit the line, keep to `max_words` (and `max_chars`) and not
    bridge a pause longer than `pause_gap`; a burst with no such neighbour stays as it is. With `reading_rate`
    (round 3), a burst whose time on screen is under reading_need joins a neighbour the same way (burst_timings
    then holds one that cannot into the following pause).

    Freeze pass (2026-11-04). C6: no join crosses a sentence end. A sentence of one or two words (NO. LOOK.) is its
    own burst when it is up CAPTION_SHORT_SENTENCE_MIN_S or more; under that it joins the following burst, never the
    one before, and only one of three words or more (no burst ends a sentence and then holds one or two words).
    C10 (`dwell`, 9:16): a burst up for less than CAPTION_DWELL_AFTER_LAST_S after its last word appears, or one
    holding a figure up for less than CAPTION_DIGIT_MIN_S, joins a neighbour in its clause (no clause punctuation
    between) where the join fits `dwell_fits` (default `fits`)."""
    out = [list(p) for p in phrases if p]
    join_fits = dwell_fits or fits
    i = 0
    while i < len(out):
        p = out[i]
        on_screen = _on_screen_estimate(out, i, pause_gap, pause_tail)
        sentence_start = i == 0 or _ends_sentence(out[i - 1])
        short_sentence = sentence_start and _ends_sentence(p) and len(p) <= 2
        if short_sentence:
            if len(out) == 1 or on_screen >= CAPTION_SHORT_SENTENCE_MIN_S - 1e-9:
                i += 1
                continue
            j = i + 1
            if (j < len(out) and len(out[j]) >= 3 and float(out[j][0]["start"]) - _word_end(p[-1]) <= pause_gap
                    and len(p + out[j]) <= max_words and fits(p + out[j])
                    and (max_chars is None or len(" ".join(_phrase_words(p + out[j]))) <= max_chars)):
                out[i] = p + out[j]
                del out[j]
            i += 1
            continue
        heard_ok = _word_end(p[-1]) - float(p[0]["start"]) >= min_dur
        read_ok = not reading_rate or on_screen >= max(reading_need(p), CAPTION_MIN_ON_SCREEN_S - 1e-9)
        dwell_ok = not dwell or (
            _dwell_after_last(out, i, pause_gap, pause_tail, lead) >= CAPTION_DWELL_AFTER_LAST_S - 1e-9
            and (not _has_figure(p) or on_screen >= CAPTION_DIGIT_MIN_S - 1e-9))
        if len(out) == 1 or (heard_ok and read_ok and dwell_ok):
            i += 1
            continue
        only_dwell = heard_ok and read_ok
        cands: list[int] = []   # neighbours in the same sentence, the one before first
        if i > 0 and not _ends_sentence(out[i - 1]) and float(p[0]["start"]) - _word_end(out[i - 1][-1]) <= pause_gap \
                and not (only_dwell and _ends_clause(str(out[i - 1][-1].get("word", "")))) \
                and not _join_breaks_rules(out[i - 1], p):   # freeze pass round 2: P1, A1, M1
            cands.append(i - 1)
        if i + 1 < len(out) and not _ends_sentence(p) and float(out[i + 1][0]["start"]) - _word_end(p[-1]) <= pause_gap \
                and not (only_dwell and _ends_clause(str(p[-1].get("word", "")))) \
                and not _join_breaks_rules(p, out[i + 1]):
            cands.append(i + 1)
        joined = None
        for j in cands:
            combo = out[j] + p if j < i else p + out[j]
            ok = join_fits(combo) if only_dwell else fits(combo)
            if len(combo) <= max_words and ok and (
                    max_chars is None or len(" ".join(_phrase_words(combo))) <= max_chars):
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
    dwell: bool = False,
) -> list[tuple[float, float, list[float]]]:
    """(show, hide, word reveal times) for each burst (round 2, finding 4). A burst appears `lead` (two frames)
    before its first word is heard; each word appears the same lead before its own onset. It stays until the next
    burst appears, except across a pause longer than `pause_gap`, where it leaves `pause_tail` after its last word.
    The last burst leaves 0.10 s after its last word. No two bursts overlap. A burst that would be up for less
    than reading_need holds on into the following pause, never past the next burst's appearance (round 3); one
    still faster than CAPTION_MAX_CPS characters a second is logged.
    With `first_frame_whole` (round 4; compose_video passes CAPTION_FIRST_FRAME_WHOLE_S for 9:16), a first burst that would
    appear that soon or sooner is shown whole from 0.0 (every reveal at 0.0): frame 0 carries the hook line.
    With `dwell` (freeze pass C10, 9:16) a burst holding a figure wants CAPTION_DIGIT_MIN_S on screen and every
    burst CAPTION_DWELL_AFTER_LAST_S after its last word appears, held into a following pause, never past the next
    burst's appearance (merge_short_bursts joins a burst the speech leaves too little)."""
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
        need = max(reading_need(phrase), CAPTION_MIN_ON_SCREEN_S)   # D1: every burst up half a second
        if dwell:   # freeze pass C10 (9:16): a figure burst up CAPTION_DIGIT_MIN_S, every burst whole for a beat
            need = max(need, CAPTION_DIGIT_MIN_S if _has_figure(phrase) else 0.0)
        if hide - show < need:
            hide = show + need if next_show is None else max(hide, min(show + need, next_show))
        if dwell and hide < reveals[-1] + CAPTION_DWELL_AFTER_LAST_S:
            want = reveals[-1] + CAPTION_DWELL_AFTER_LAST_S
            hide = want if next_show is None else max(hide, min(want, next_show))
        hide = max(hide, reveals[-1] + 0.02)
        chars = len(" ".join(_phrase_words(phrase)))
        if hide > show and chars / (hide - show) > CAPTION_MAX_CPS:
            log.info("Caption burst at %.2f s reads at %.1f characters a second (over %.0f): %s", show,
                     chars / (hide - show), CAPTION_MAX_CPS, " ".join(_phrase_words(phrase)))
        out.append((show, hide, reveals))
        prev_hide = hide
    return out


def emphasis_bonds(words: list[dict[str, Any]], emphasis: list[str] | None) -> set[int]:
    """Bonds inside each gold run (emphasis_indices with its hedges: ROCK SALT, ROUGHLY 280 FEET): no burst splits
    a key term (freeze pass C7, 2026-11-04). Never across clause or sentence punctuation."""
    key = emphasis_indices(words, emphasis)
    raw = [str(w.get("word", "")) for w in words]
    hedge_of = hedge_figures(words, emphasis)
    out = {k for k in key if k + 1 in key and not _ends_clause(raw[k])}
    return out | {b for h, f in hedge_of.items() for b in range(h, f)}


# ---------------------------------------------------------------------------------------------------------------------
# The round-4 phrasing (HEAD 0036b18) as the base the freeze-pass rules refine (operator decision 2026-11-04, the
# HEAD-regression gate): every pair of adjacent words the round-4 builders keep in one 9:16 burst, one 16:9 burst or
# one sidecar cue stays together, unless a bond (a proper name, a figure group), the article rule (no burst ends on
# THE, A or AN), the 66 px floor or the width and character limits force the cut. The freeze-pass builders take the
# fewest cuts the base does not have (partition_words_grammatical, partition_sidecar_cues) and rejoin what the base
# kept (_rejoin_base). These copies measure as round 4 measured, but unkerned (operator decision 2026-11-05: HEAD's
# raqm measure kerned; the unkerned measure reproduces HEAD's own phrasing on every gate case, so the base is
# unchanged: test_round4_base_is_head_0036b18); they decide no burst on their own.
_R4_MEASURE_UNITS = frozenset(MEASURE_UNITS - {
    "tonne", "tonnes", "kilometer", "kilometers", "metric", "acres", "acre", "hectares", "hectare",
    "megaton", "megatons", "kiloton", "kilotons", "gigaton", "gigatons", "metric-ton", "metric-tons",
})


def _r4_fits_width(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """_fits_caption_width as at HEAD 0036b18 (the type measure at FONT_SIZE)."""
    return _caption_text_width(_phrase_words(phrase), FONT_SIZE) <= max_width


def _r4_fits_burst(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """_fits_caption_burst as at HEAD 0036b18."""
    return _caption_text_width(_phrase_words(phrase), CAPTION_BURST_MIN_SIZE) <= max_width


def _r4_fits_bonded(phrase: Any, max_width: int = MAX_CAPTION_WIDTH) -> bool:
    """_fits_caption_bonded as at HEAD 0036b18."""
    return _caption_text_width(_phrase_words(phrase), CAPTION_BOND_MIN_SIZE) <= max_width


def _r4_atomic_boundary(w1_raw: str, w2_raw: str) -> bool:
    """_is_atomic_boundary as at HEAD 0036b18 (round 4), for the round-4 base phrasing."""
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
    if (any(c.isdigit() for c in w1) or w1 in SPELLED_NUMBERS or w1 in ORDINAL_WORDS) and (w2 in _R4_MEASURE_UNITS or w2 in {"century", "centuries", "millennium", "millennia", "era", "dynasty"}):
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


def _r4_split_defects(split: List[List[Dict[str, Any]]]) -> int:
    """_split_defects as at HEAD 0036b18: every DANGLING_ENDS end mid-sentence is a defect."""
    defects = 0
    for k, chunk in enumerate(split):
        if _function_only(chunk):
            defects += 2
        elif k < len(split) - 1 and _bare(chunk[-1].get("word", "")) in DANGLING_ENDS:
            defects += 1
    return defects


def _r4_caption_bonds(words: list[Any]) -> set[int]:
    """caption_bonds as at HEAD 0036b18 (no U1 bonds, the round-4 units)."""
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
            _, hi = _range_span(tokens, raw, i, end, _R4_MEASURE_UNITS)
            if hi > end:
                _bond(i, hi)
    return bonds | name_bonds(raw)


def _r4_partition(
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
    """partition_words_grammatical as at HEAD 0036b18 (round 4), measured as it measured."""
    if bonds is None:
        bonds = _r4_caption_bonds(words)
    # A burst that cannot be cut into bursts of min_words or more without splitting a bond (BETWEEN 94 AND 104
    # METRES) may shrink further with the Reel defaults, to CAPTION_BOND_MIN_SIZE (item 2, 2026-11-03: at 80 px it
    # no longer fits at CAPTION_BURST_MIN_SIZE, so the figure was split from its range). A caller's own `fits` is used
    # as is.
    fits_bonded = fits
    if fits is None:
        fits = _r4_fits_burst
        fits_bonded = _r4_fits_bonded
        prefer = prefer or _r4_fits_width
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
                    if i + size < n and (off + i + size - 1 in bonds or _r4_atomic_boundary(
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
                        if first_w in _R4_MEASURE_UNITS:
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
                        key = (-_r4_split_defects(split), -_split_lones(split), score)
                        if key > best_score:
                            best_score = key
                            best = split
            if best is None:
                def _held(c: int) -> bool:   # the cut after c words splits a bond or an atomic pair (KRISHNA | I)
                    return i + c < n and (off + i + c - 1 in bonds or _r4_atomic_boundary(
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


def _r4_merge(
    phrases: list[list[dict[str, Any]]],
    fits: Callable[[list[dict[str, Any]]], bool],
    max_words: int,
    min_dur: float = CAPTION_MIN_CHUNK,
    pause_gap: float = CAPTION_PAUSE_GAP,
    reading_rate: bool = True,
    pause_tail: float = CAPTION_PAUSE_TAIL,
) -> list[list[dict[str, Any]]]:
    """merge_short_bursts as at HEAD 0036b18."""
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


def _r4_wrap_cue(words: list[str], max_chars: int = SIDECAR_MAX_LINE_CHARS, max_lines: int = SIDECAR_MAX_LINES,
              bonds: set[int] | None = None) -> str | None:
    """_wrap_cue as at HEAD 0036b18."""
    text = " ".join(words)
    if len(text) <= max_chars:
        return text
    if max_lines < 2:
        return None
    if bonds is None:
        bonds = _r4_caption_bonds(words)
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


def _r4_sidecar_cues(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """partition_sidecar_cues as at HEAD 0036b18."""
    timed = [w for w in words if str(w.get("word", "")).strip()]
    bonds = _r4_caption_bonds(timed)
    pos = {id(w): i for i, w in enumerate(timed)}
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for w in timed:
        word = str(w.get("word", "")).strip()
        if cur:
            candidate = [str(x["word"]).strip() for x in cur] + [word]
            local = {pos[id(x)] - pos[id(cur[0])] for x in cur if pos[id(x)] in bonds}
            if _r4_wrap_cue(candidate, bonds=local) is None or _word_end(w) - float(cur[0]["start"]) > SIDECAR_MAX_CUE:
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
        text = _r4_wrap_cue(g_words, bonds=local) or _r4_wrap_cue(g_words, bonds=set()) or " ".join(g_words)
        cues.append((start, end, text))
    return cues


def _cuts_of(phrases: list[list[Any]]) -> set[int]:
    """The word positions after which a burst or cue ends, the last one left out."""
    cuts, at = set(), -1
    for p in phrases:
        at += len(p)
        cuts.add(at)
    return cuts - {at}


def round4_burst_cuts(words: list[dict[str, Any]], emphasis: list[str] | None = None, widescreen: bool = False,
                      max_width: int | None = None) -> set[int]:
    """Where HEAD 0036b18's build_subtitles ends its 9:16 (or 16:9) bursts: the base the freeze-pass phrasing keeps
    (operator decision 2026-11-04, the HEAD-regression gate)."""
    hedge_of = hedge_figures(words, emphasis)
    bonds = _r4_caption_bonds(words) | {b for h, f in hedge_of.items() for b in range(h, f)}
    if widescreen:
        wide_max = max_width if max_width is not None else int(1920 * 0.75)

        def fits(ph: list[dict[str, Any]]) -> bool:
            return _fits_wide(ph, wide_max)
        phrases = _r4_partition(words, CAPTION_WIDE_MIN_WORDS, CAPTION_WIDE_TARGET_WORDS, CAPTION_WIDE_MAX_WORDS,
                                CAPTION_WIDE_MAX_CHARS, fits=fits, glance=(24, CAPTION_WIDE_MAX_CHARS), bonds=bonds)
        phrases = _r4_merge(phrases, fits, CAPTION_WIDE_MAX_WORDS)
    else:
        phrases = _r4_merge(_r4_partition(words, bonds=bonds), _r4_fits_width, MAX_BURST_WORDS)
    return _cuts_of(phrases)


def round4_cue_ends(words: list[dict[str, Any]]) -> set[int]:
    """Where HEAD 0036b18's sidecar cues end, as positions in the non-empty words (the last one left out)."""
    return _cuts_of([text.split() for _, _, text in _r4_sidecar_cues(words)])


def _base_yields(words: list[dict[str, Any]], emphasis: list[str] | None, base_cuts: set[int]) -> set[int]:
    """Fix round 1 (operator decision 2026-11-04, the HEAD-regression gate: a cut the round-4 phrasing does not have
    only where a name or a width or floor limit forces it): the base cuts where a freeze-pass phrase link gives way,
    so it adds no cut there: a fixed hedge phrase (BY ONE | ESTIMATE), the lead-in to a name or figure (HOUSED INSIDE |
    THE CATHERINE PALACE) and a key term (WOODEN | STAKES). A proper name (name_bonds) and round 4's own bonds and
    atomic pairs never give way. Fix round 2 (the decision as written, review blocker 1): a freeze-pass figure link
    (a figure with its unit, SQUARE with its unit, a bracketed conversion, a date) gives way too, so it never forces a
    cut the base lacks (BETWEEN 10 | AND 15 MEGATONS | OF TNT); it is held only where that costs no such cut
    (_base_figure_links)."""
    raw = [str(w.get("word", "")) for w in words]
    hedge_of = hedge_figures(words, emphasis)
    held = _r4_caption_bonds(words) | {b for h, f in hedge_of.items() for b in range(h, f)} | name_bonds(raw)
    return {k for k in base_cuts if k + 1 < len(raw) and k not in held and not _r4_atomic_boundary(raw[k], raw[k + 1])}


def _base_figure_links(words: list[dict[str, Any]], base_cuts: set[int]) -> set[int]:
    """Fix round 2: the base cuts at a freeze-pass figure link (caption_bonds' figure part beyond a fixed hedge phrase,
    and the atomic pairs: a figure with its unit, SQUARE with its unit, a bracketed conversion, a date). The operator
    decision as written lets only a name or a width or floor limit force a cut the base lacks, so these are no bonds;
    the phrase builder holds them only where that adds no such cut (partition_words_grammatical's `held_soft`)."""
    raw = [str(w.get("word", "")) for w in words]
    tokens = [_norm(r) for r in raw]
    groups = {i + j for i in range(len(raw)) for g in HEDGE_GROUPS if tuple(tokens[i:i + len(g)]) == g
              for j in range(len(g) - 1)}
    links = _figure_bonds(raw) - groups
    return {k for k in base_cuts if k + 1 < len(raw) and (k in links or _is_atomic_boundary(raw[k], raw[k + 1]))}


def _held_runs(words: list[dict[str, Any]], extra: set[int], fits: Callable[[list[dict[str, Any]]], bool]) -> set[int]:
    """The key-term bonds (emphasis_bonds beyond caption_bonds) of each run one burst can hold (`fits`, at the bond
    floor): a key term too wide for any burst (PODKAMENNAYA TUNGUSKA RIVER, 702 px at 66) is no bond (fix round 1,
    operator decision 2026-11-04: it split as NEAR | THE | PODKAMENNAYA TUNGUSKA | RIVER ...)."""
    out: set[int] = set()
    k = 0
    while k < len(words):
        if k not in extra:
            k += 1
            continue
        a = k
        while k in extra:
            k += 1
        if fits(words[a:k + 1]):
            out.update(range(a, k))
        k += 1
    return out


def _rejoin_base(phrases: list[list[dict[str, Any]]], words: list[dict[str, Any]], base_cuts: set[int],
                 can_join: Callable[[list[dict[str, Any]]], bool]) -> list[list[dict[str, Any]]]:
    """Fix round 1 (the HEAD-regression gate): two adjacent bursts whose boundary the round-4 phrasing does not cut
    (a sentence end it joined, a merge it made) are one burst again wherever the join is allowed (`can_join`)."""
    pos = {id(w): k for k, w in enumerate(words)}
    out = [list(p) for p in phrases if p]
    i = 0
    while i + 1 < len(out):
        if pos[id(out[i][-1])] not in base_cuts and can_join(out[i] + out[i + 1]):
            out[i] = out[i] + out[i + 1]
            del out[i + 1]
            continue
        i += 1
    return out


def caption_phrases(words: list[dict[str, Any]], emphasis: list[str] | None = None, widescreen: bool = False,
                    max_width: int | None = None) -> list[list[dict[str, Any]]]:
    """The bursts build_subtitles draws (9:16 Reel bursts, or 16:9 one-line bursts): the grammatical partition
    with every bond (caption_bonds, and the key terms with their hedges: emphasis_bonds), then merge_short_bursts.
    9:16 joins also keep each burst on screen for its dwell (freeze pass C10). Fix round 1 (operator decision
    2026-11-04): the round-4 phrasing is the base (round4_burst_cuts), a cut it does not have is made only where a
    rule forces it, and a join it made is made again (_rejoin_base)."""
    if widescreen:
        wide_max = max_width if max_width is not None else min(int(1920 * 0.75), CAPTION_WIDE_MAX_PLATE)
        base = round4_burst_cuts(words, emphasis, True, wide_max)
        loose = _base_yields(words, emphasis, base)
        bonds = (caption_bonds(words) | _held_runs(words, emphasis_bonds(words, emphasis) - caption_bonds(words),
                                                   lambda ph: _fits_wide(ph, wide_max, max_chars=None))) - loose

        def fits(ph: list[dict[str, Any]]) -> bool:
            return _fits_wide(ph, wide_max)

        def fits_bonded(ph: list[dict[str, Any]]) -> bool:   # N2: a bonded name may pass the 42 characters
            return _fits_wide(ph, wide_max, max_chars=None)
        phrases = partition_words_grammatical(words, CAPTION_WIDE_MIN_WORDS, CAPTION_WIDE_TARGET_WORDS,
                                              CAPTION_WIDE_MAX_WORDS, CAPTION_WIDE_MAX_CHARS, fits=fits,
                                              glance=(24, CAPTION_WIDE_MAX_CHARS), bonds=bonds,
                                              fits_bonded=fits_bonded, base_cuts=base, loose=loose,
                                              held_soft=_base_figure_links(words, base) & loose)
        phrases = merge_short_bursts(phrases, fits, CAPTION_WIDE_MAX_WORDS, max_chars=CAPTION_WIDE_MAX_CHARS)
        return _rejoin_base(phrases, words, base, lambda ph: len(ph) <= CAPTION_WIDE_MAX_WORDS and fits(ph))
    base = round4_burst_cuts(words, emphasis)
    loose = _base_yields(words, emphasis, base)
    bonds = (caption_bonds(words) | _held_runs(words, emphasis_bonds(words, emphasis) - caption_bonds(words),
                                               _fits_caption_bonded)) - loose
    phrases = merge_short_bursts(partition_words_grammatical(words, bonds=bonds, base_cuts=base, loose=loose,
                                                             held_soft=_base_figure_links(words, base) & loose),
                                 _fits_caption_width, MAX_BURST_WORDS, dwell=True, dwell_fits=_fits_caption_burst, max_chars=MAX_BURST_CHARS)
    phrases = _rejoin_base(phrases, words, base, lambda ph: (
        len(ph) <= MAX_BURST_WORDS and len(" ".join(_phrase_words(ph))) <= MAX_BURST_CHARS
        and _fits_caption_burst(ph)))
    return _two_line_phrasing(phrases, words, base)


def _two_line_phrasing(phrases: list[list[dict[str, Any]]], words: list[dict[str, Any]],
                       base: set[int]) -> list[list[dict[str, Any]]]:
    """QF-87 (the QF-24/QF-87 layout, operator decision 2026-11-09): the 9:16 bursts once a burst may draw on two
    lines (reel_caption_lines). Each change keeps every word, its time and every bond, and holds the burst caps:
    two to MAX_BURST_WORDS words, MAX_BURST_CHARS characters, not function words only, fitting at
    CAPTION_BURST_MIN_SIZE on one line or two.
    1. A short sentence the round-4 phrasing (`base`) joined to what follows (NO. IT WAS) is not stranded by a
       wider two-line neighbour: the pair is re-cut at the round-4 boundary after the sentence end instead.
    2. Two bursts the round-4 phrasing did not cut are one again where the join holds (_rejoin_base).
    3. A burst ending on a dangling function word (an article, a preposition: _dangles, FUNCTION_WORDS; never a
       pre-modifier, whose noun phrase stays whole: QF-87 review 1) that is on screen for less than its
       reading_need takes the first words of a two-line successor, where both bursts hold the caps, no bond or
       atomic pair is split, the new end does not dangle and neither burst reads faster than
       1 / CAPTION_READ_PER_CHAR characters a second."""
    out = [list(p) for p in phrases if p]
    pos = {id(w): k for k, w in enumerate(words)}
    raw = [w["word"] for w in words]
    protected = name_bonds(raw) | caption_bonds(words)

    def allowed(chunk: list[dict[str, Any]]) -> bool:
        return (2 <= len(chunk) <= MAX_BURST_WORDS and len(" ".join(_phrase_words(chunk))) <= MAX_BURST_CHARS
                and not _function_only(chunk) and _fits_caption_burst(chunk))

    for i in range(len(out) - 1):
        left, right = out[i], out[i + 1]
        cut = pos[id(left[-1])]
        if cut in base or not _ends_sentence(left):
            continue
        end = min((b for b in base if b > cut), default=len(words) - 1)
        combo = left + right
        k = end - pos[id(combo[0])] + 1
        if not 0 < k < len(combo) or end in protected:
            continue
        a, b = combo[:k], combo[k:]
        if allowed(a) and allowed(b):
            out[i:i + 2] = [a, b]
    out = _rejoin_base(out, words, base, allowed)
    max_cps = 1 / CAPTION_READ_PER_CHAR
    for i in range(len(out) - 1):
        left, right = out[i], out[i + 1]
        if (not _dangles(left[-1]["word"]) or _ends_clause(left[-1]["word"])
                or _bare(left[-1]["word"]) not in FUNCTION_WORDS):
            continue   # review 1: only a function word end moves; a pre-modifier keeps its noun phrase (DARK GRAY)
        show, hide, _ = burst_timings(out)[i]
        if hide - show >= reading_need(left) or reel_caption_lines(right) is None:
            continue
        for take in range(1, len(right) - 1):
            a, b = left + right[:take], right[take:]
            cut = pos[id(a[-1])]
            if (not allowed(a) or not allowed(b) or cut in protected or _dangles(a[-1]["word"])
                    or _is_atomic_boundary(a[-1]["word"], b[0]["word"])):
                continue
            trial = out[:i] + [a, b] + out[i + 2:]
            timing = burst_timings(trial)
            cps = [len(" ".join(_phrase_words(trial[q]))) / max(0.01, timing[q][1] - timing[q][0])
                   for q in (i, i + 1)]
            if max(cps) > max_cps:
                continue
            out = trial
            break
    if [w for p in out for w in p] != words:
        raise RuntimeError("caption phrasing lost or reordered a word")
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
    cuts the timings come back unchanged.
    Freeze pass C9 (2026-11-04): a snap may put a burst's show up to one frame after its first word is heard; where
    the snap onto the cut is not valid, a boundary before the cut moves back to `snap_frames` frames before it (else
    CAPTION_CUT_CLEAR_FRAMES before it), one after it out to CAPTION_CUT_CLEAR_FRAMES after it, so no boundary is
    left one to three frames off a cut where a move is valid. A move also keeps each burst whole for
    CAPTION_DWELL_AFTER_LAST_S after its last word appears (or no less than it had)."""
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
            old_dwell = timings[j][1] - timings[j][2][-1]
            if hide - rev[-1] < min(CAPTION_DWELL_AFTER_LAST_S, old_dwell) - 1e-9:
                return False
            if show > first_words[j] + frame + 1e-9:   # at most a frame after its first word is heard
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
        clear = max(min_frames, CAPTION_CUT_CLEAR_FRAMES) * frame
        if t < cut:
            targets += [cut - snap_frames * frame, cut - clear]
        elif t < cut + clear - 1e-6:
            targets.append(cut + clear)
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
# Freeze pass U1 (2026-11-04): fixed, topic-agnostic hedge phrases kept whole in a burst, cue and cue line.
HEDGE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("by", "one", "estimate"), ("by", "one", "account"), ("by", "some", "accounts"), ("by", "some", "estimates"),
    ("most", "likely"),
)
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


def _range_span(tokens: list[str], raw: list[str], lo: int, hi: int,
                units: set[str] | frozenset[str] | None = None) -> tuple[int, int]:
    """(lo, hi) widened over a range the figure at lo..hi is one end of: '94 to [104 feet]' or '[94] to 104
    (feet)'. A unit may follow either end. Round 4 fix (independent review): AND joins a range only after BETWEEN
    (20,000 PEOPLE AND 50 CATTLE is two counts); TO / OR / BETWEEN..AND only when the second end names nothing it
    counts or the same unit or noun as the first (20 YEARS OR 30 WORKERS is no range). `units` defaults to
    MEASURE_UNITS (the round-4 base phrasing passes the round-4 set, _R4_MEASURE_UNITS)."""
    n = len(tokens)
    units = MEASURE_UNITS if units is None else units
    fs = next((x for x in range(lo, hi + 1) if _is_figure_at(tokens, x)), None)
    if fs is None:
        return lo, hi
    fe = min(_figure_end(tokens, raw, fs), hi) if hi >= fs else fs
    # the term's own count: its last word after the figure, else a unit right after the term
    j = hi
    if hi > fe:
        own: str | None = tokens[hi]
    elif j + 1 < n and tokens[j + 1] in units and tokens[j + 1] not in _RANGE_TAIL_WORDS \
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
            if other is not None and (other in units or (first is not None and _unit_key(first) == _unit_key(other))):
                new_hi = e2 + 1
    # backward: figure (unit) joiner [figure]
    new_lo = lo
    k = fs - 1
    if k >= 1 and tokens[k] in RANGE_JOINERS:
        m = k - 1
        earlier: str | None = None
        if tokens[m] in units and m >= 1 and not _ends_clause(raw[m - 1]):
            earlier = tokens[m]
            m -= 1
        while m >= 1 and (tokens[m] in SPELLED_NUMBERS or tokens[m] in _COUNT_NOUNS) and (
                tokens[m - 1] in SPELLED_NUMBERS or tokens[m - 1] == "a") and not _ends_clause(raw[m - 1]):
            m -= 1
        if _is_figure_at(tokens, m) and _joins(k, m) and _same_count(earlier, own):
            new_lo = m
    return new_lo, new_hi


def _figure_bonds(raw: list[str]) -> set[int]:
    """caption_bonds' figure part: a hedge with its figure, the words of one figure and both ends of a range, and the
    fixed hedge phrases (HEDGE_GROUPS)."""
    return caption_bonds(raw, names=False)


def caption_bonds(words: list[Any], names: bool = True) -> set[int]:
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
            if end + 2 < n and tokens[end + 1] in AREA_QUALIFIERS and tokens[end + 2] in MEASURE_UNITS:
                _bond(end, end + 1)   # U1: 830 SQUARE MILES
            elif end + 1 < n and tokens[end + 1] in MEASURE_UNITS and _is_atomic_boundary(raw[end], raw[end + 1]):
                _bond(end, end + 1)   # review round 4: the figure's unit is part of the figure (200,000 TONS)
    for i in range(n):   # freeze pass U1: a fixed hedge phrase never splits (BY ONE ESTIMATE, MOST LIKELY)
        for group in HEDGE_GROUPS:
            k = len(group)
            if tuple(tokens[i:i + k]) == group:
                _bond(i, i + k - 1)
    if not names:
        return bonds
    named = name_bonds(raw)
    return bonds | named | lead_in_bonds(raw, bonds | named)


# The words that lead into a bonded name or figure group and go with it (freeze pass C3, 2026-11-04: UNDER THE |
# RASHTRAKUTA DYNASTY read as two bursts): the articles and determiners, and the prepositions.
LEAD_IN_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "his", "her",
    "of", "at", "to", "in", "for", "on", "with", "by", "from", "into", "over", "under", "about", "across", "through",
    "between", "during", "before", "after", "upon", "within", "without", "toward", "towards", "against", "behind",
    "beneath", "beside", "near", "since", "until", "around", "beyond", "below", "above", "inside", "outside",
}
LEAD_IN_MAX = 2   # at most a preposition and an article (UNDER THE, TO THE)


def _short_function_words(raw: list[str]) -> int:
    """How many of these words are function words of three letters or fewer (THE, OF, TO)."""
    return sum(1 for w in raw if _bare(w) in FUNCTION_WORDS and len(_bare(w)) <= 3)


def bonded_burst_allowed(raw: list[str]) -> bool:
    """Whether a burst of these words, every boundary inside it bonded, may pass MAX_BURST_WORDS or MAX_BURST_CHARS
    (freeze pass C3): by at most CAPTION_BOND_EXTRA_WORDS words (only when two or more of them are short function
    words, or it holds a figure: THE 1941 SIEGE OF ODESA, TO THE YEAR 196 AD, BETWEEN 287 AND 300 KILOMETRES) and
    CAPTION_BOND_EXTRA_CHARS characters (UNDER THE RASHTRAKUTA DYNASTY), and fits at CAPTION_BOND_MIN_SIZE."""
    words = len(raw)
    text = " ".join(_phrase_words(raw))
    if words > MAX_BURST_WORDS + CAPTION_BOND_EXTRA_WORDS or len(text) > MAX_BURST_CHARS + CAPTION_BOND_EXTRA_CHARS:
        return False
    if words > MAX_BURST_WORDS and _short_function_words(raw) < 2 and not any(c.isdigit() for c in text):
        return False
    return _fits_caption_bonded(raw)


_ARTICLE_SLOT = {"the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "his", "her"}


def lead_in_bonds(raw: list[str], bonds: set[int], named: set[int] | None = None) -> set[int]:
    """Bonds joining the prepositions and articles right before a bonded group (a proper name, a figure with its
    unit, range or hedge) to it, with no clause punctuation between: TO THE YEAR 196 AD, OF SOME 7,000 PEOPLE (freeze
    pass C3). At most LEAD_IN_MAX words; the outer one is left off where the burst would not be allowed
    (bonded_burst_allowed), and all of them where even the nearest is not.

    Freeze pass N1 (operator decision, 2026-11-04): no burst shrinks below CAPTION_BOND_MIN_SIZE to hold a lead-in.
    Before a proper name (`named`, name_bonds) the ladder is (a) the lead-in words, the article and the name on one
    line at a rung of CAPTION_LADDER; (b) the article and the name on one line (UNDER | THE RASHTRAKUTA DYNASTY);
    (c) where even that does not fit, the article goes with the name, which draws on two lines (_two_line_layout).
    A lead-in that opens its clause stays with a figure group only where the burst fits at CAPTION_BOND_MIN_SIZE."""
    named = name_bonds(raw) if named is None else named
    n = len(raw)
    linked = set(bonds) | {i for i in range(n - 1) if _is_atomic_boundary(raw[i], raw[i + 1])}
    out: set[int] = set()
    i = 0
    while i < n:
        if i not in linked:
            i += 1
            continue
        a = i
        while i in linked:
            i += 1
        b = i   # the group a..b
        lead = a
        while lead - 1 >= 0 and a - (lead - 1) <= LEAD_IN_MAX and _bare(raw[lead - 1]) in LEAD_IN_WORDS \
                and (raw[lead - 1][:1].islower() or lead - 1 == 0 or _ends_clause(raw[lead - 2])) \
                and not _ends_clause(raw[lead - 1]) \
                and not (lead - 2 >= 0 and lead - 2 in linked):
            lead -= 1
        # A lead-in that opens its clause has no burst before it to join: before a figure group it stays with the
        # group where the burst fits at the bond floor (a function-word burst is never formed)
        opens = lead < a and (lead == 0 or _ends_clause(raw[lead - 1]))
        is_name = any(k in named for k in range(a, b))
        while lead < a:
            words = raw[lead:b + 1]
            text = " ".join(_phrase_words(words))
            if (len(words) <= MAX_BURST_WORDS and len(text) <= MAX_BURST_CHARS and _fits_caption_burst(words)) \
                    or bonded_burst_allowed(words) or (is_name and _fits_caption_bonded(words)) or (
                    opens and not is_name and len(words) <= MAX_BURST_WORDS + CAPTION_BOND_EXTRA_WORDS
                    and len(text) <= MAX_BURST_CHARS + CAPTION_BOND_EXTRA_CHARS and _fits_caption_bonded(words)):
                out.update(range(lead, a))
                break
            if is_name and lead == a - 1 and _bare(raw[lead]) in _ARTICLE_SLOT and not _fits_caption_bonded(words) \
                    and _two_line_layout(words) is not None:
                out.add(lead)   # N1 (c): the article goes with a name too wide for one line (two lines)
                break
            lead += 1
        i = b + 1
    return out


# A proper name is never split across bursts (design handover, 2026-11-04: RASHTRAKUTA | DYNASTY). A name is a run of
# capitalised words (ELLORA CAVES), linked by a lower-case particle only between two capitalised words (TEMPLE OF
# KAILASA), and may end on a lower-case head noun that names what it is (RASHTRAKUTA DYNASTY). Topic-agnostic: only
# the narration's own capitals and these generic words decide.
NAME_HEADS = DESIGNATORS | {
    "dynasties", "empire", "empires", "kingdom", "kingdoms", "temple", "temples", "river", "rivers", "valley",
    "mountains", "mountain", "range", "sea", "ocean", "island", "islands", "wall", "walls", "palace", "fort",
    "fortress", "castle", "city", "period", "era", "age", "civilisation", "civilization", "culture", "desert",
    "lake", "canal", "pyramid", "pyramids", "monastery", "cathedral", "abbey", "basilica", "mosque", "shrine",
    "sultanate", "caliphate", "republic", "basin", "plateau", "peninsula", "gorge", "strait", "bay", "coast",
    "people", "script", "inscription", "inscriptions", "style", "school", "order", "sect", "tradition", "language",
    "army", "fleet", "navy", "expedition", "mission", "museum", "library", "university", "observatory",
    "manuscript", "map", "event", "incident", "war", "wars", "revolution", "treaty", "church", "gallery", "statue",
    "statues",
}
NAME_PARTICLES = {"of", "de", "del", "della", "da", "di", "du", "von", "van", "al", "el", "la", "le"}
# Capitalised words that open a sentence without naming anything (a name starts after them): the function words,
# pronouns and the sentence adverbs a narration opens on.
NAME_STOPS = FUNCTION_WORDS | CLAUSE_STARTERS | {
    "i", "it", "he", "she", "we", "they", "you", "there", "here", "then", "now", "today", "later", "soon", "once",
    "still", "even", "only", "inside", "outside", "below", "above", "around", "beyond", "legend", "some", "most",
    "many", "every", "each", "no", "nobody", "nothing", "what", "one", "two", "three", "yes", "just", "also",
    "instead", "next", "first", "finally", "today's", "centuries", "thousands", "hundreds", "look", "imagine",
}
NAME_ARTICLES = {"the", "a", "an"}
# a name a number continues (AUGUST 5, CAVE 10, ROOM 101); after any other name a number is a new phrase (EGYPT 3000)
NAME_NUMBERED = DESIGNATORS | {"january", "february", "march", "april", "may", "june", "july", "august",
                               "september", "october", "november", "december", "cave", "room", "gate", "tomb",
                               "chapter", "book", "volume", "site", "level", "number", "apollo", "flight"}
NAME_MAX_WORDS = MAX_BURST_WORDS   # a longer run is no bond (one burst holds it at most)
NAME_MAX_CHARS = MAX_BURST_CHARS
NAME_REGNAL = re.compile(r"^(?=[IVX])X{0,3}(IX|IV|V?I{0,3})$")   # KRISHNA I, LOUIS XIV: a numeral after a name


def _regnal(word: str) -> bool:
    return bool(NAME_REGNAL.match(re.sub(r"[,;.:!?—]+$", "", word)))


def _capitalised(word: str) -> bool:
    core = word.strip("\"'“”‘’()[]")
    return bool(core) and core[0].isupper() and _bare(core) not in NAME_STOPS


def _possessive(word: str) -> bool:
    return bool(re.search(r"(['’]s|s['’])[,;.:!?]*$", word, re.I))


def _sentence_starts(raw: list[str]) -> set[int]:
    """Positions that open a sentence (the first word, and each word after terminal punctuation)."""
    return {0} | {k + 1 for k, w in enumerate(raw[:-1]) if TERMINAL_PUNCT.search(w) and not _is_abbreviation(w)}


def name_bonds(raw: list[str]) -> set[int]:
    """Word positions i whose boundary with word i + 1 lies inside a proper name (NAME_HEADS, NAME_PARTICLES).
    Never across clause or sentence punctuation. A sentence's first word joins a name only if the narration
    capitalises it elsewhere too (NEAR OUADANE and ANCIENT ROMAN open sentences; ELLORA recurs). A capitalised
    number word or a Roman numeral continues a name (WORLD WAR TWO, KRISHNA I). A run longer than NAME_MAX_WORDS
    or NAME_MAX_CHARS, or wider than one burst at the bond floor (CAPTION_BOND_MIN_SIZE), is no bond: forcing it
    would cost a worse cut elsewhere (review 2026-11-04: ON THE | ADRAR PLATEAU OF | MAURITANIA)."""
    n = len(raw)
    starts = _sentence_starts(raw)
    mid_caps = {_bare(w) for k, w in enumerate(raw) if k not in starts and _capitalised(w)}

    def opens(k: int) -> bool:
        return _capitalised(raw[k]) and (k not in starts or _bare(raw[k]) in mid_caps)

    def continues(w: str) -> bool:
        core = w.strip("\"'“”‘’()[]")
        return _capitalised(w) or _regnal(w) or (core[:1].isupper() and _bare(core) in SPELLED_NUMBERS)

    bonds: set[int] = set()
    i = 0
    while i < n:
        if not opens(i):
            i += 1
            continue
        j = i   # the run's last word
        # a possessive ends a name (RUSSIA'S | CATHERINE PALACE: two names, review 2026-11-04)
        while j + 1 < n and not _ends_clause(raw[j]) and not _possessive(raw[j]):
            nxt = raw[j + 1]
            if (continues(nxt) and not (_bare(nxt) == "i" and j == i)) or (   # CARTER I BELIEVE: the pronoun
                    _bare(raw[j]) in NAME_NUMBERED and re.fullmatch(r"\d{1,4}[,;.:!?]*", nxt)):  # AUGUST 5, CAVE 10
                j += 1
            elif (_bare(nxt) in NAME_PARTICLES and j + 2 < n and not _ends_clause(nxt)
                  and _capitalised(raw[j + 2])):
                j += 2
            elif _bare(nxt) in NAME_HEADS and nxt[:1].islower():
                j += 1
                break
            else:
                break
        words = j - i + 1
        text = [re.sub(r"[,;.:!?—]+$", "", w) for w in raw[i:j + 1]]
        # the article before a name goes with it (operator decision 2026-11-04: a burst never ends on THE); a name
        # whose article cannot fit one burst with it at the bond floor is no bond (WITHIN THE | ADRAR REGION OF
        # MAURITANIA would end a burst on THE), so the partition cuts it as before
        article = i > 0 and _bare(raw[i - 1]) in NAME_ARTICLES and not _ends_clause(raw[i - 1])
        span = [raw[i - 1], *text] if article else text
        if (1 < words <= NAME_MAX_WORDS and len(" ".join(text)) <= NAME_MAX_CHARS
                and _caption_text_width(span, CAPTION_BOND_MIN_SIZE) <= MAX_CAPTION_WIDTH):
            bonds.update(range(i - 1 if article else i, j))
        i = j + 1
    return bonds


# The bed's duck under the narration (QF-07, review 2026-11-05 #9). A 220 ms release, no hold and a full-band key let
# the bed swell a median 7.4 dB back up inside every 0.1-0.5 s gap between two words (pumping). The key now hears the
# voice's speech band only (200 Hz-4 kHz: no rumble, breath or sibilance), as a smooth envelope that holds each word
# for DUCK_HOLD_S and is levelled wherever it is speech, so the bed sits at one depth under the whole narration (a
# median 7 dB, as before) and comes back up only in real pauses, over DUCK_RELEASE_MS after the hold. The hold covers
# the review's whole 0.1-0.5 s word gap: most gaps in the fixture runs are 0.4 s (a 0.3 s hold measured 3.5 dB in
# development). The hold ends with the voice, so the loop bridge is the bed's own. The release is 400 ms, not the
# review's ~700: with a hold, a 700 ms release left the bed 0.8 dB down at the end of the Reel's loop bridge (a step
# at the loop seam); at 400 ms it is back within 1 dB at most 0.6 s after the recorded voiceovers end
# (docs/reviews/quality-fixes-2026-11/QF-07).
DUCK_KEY_BAND = (200, 4000)   # Hz: the sidechain key's high-pass and low-pass
DUCK_KEY_SMOOTH_HZ = 12       # Hz: the key's envelope (the speech band's power, low-passed)
DUCK_KEY_SPEECH_DB = -45      # dBFS: an envelope from here up is speech, and keys the duck at one level ...
DUCK_KEY_LEVEL_DB = -15       # dBFS: ... this one, so a quiet word ducks the bed as deep as a loud one
DUCK_HOLD_S = 0.5             # s: the key holds a word's envelope this long after it ends
DUCK_HOLD_STEP_S = 0.05       # s: the hold's taps (the max of the envelope and its copies 0.05 ... 0.5 s late)
DUCK_KEY_RATE = 1000          # Hz: the envelope is held and levelled at this rate (it is smooth below 12 Hz), then
                              # resampled back for the detector: the hold costs a 48th of what it would at full rate
DUCK_THRESHOLD = 0.06         # -24.4 dBFS: a median 7 dB duck under the levelled key at DUCK_RATIO (measured)
DUCK_RATIO = 4
DUCK_ATTACK_MS = 15
DUCK_RELEASE_MS = 400


def _duck_key(src: str, out: str, pad: str = "") -> str:
    """The sidechain key: the voice's speech band (DUCK_KEY_BAND) as an RMS envelope (the band squared and low-passed
    below DUCK_KEY_SMOOTH_HZ), held for DUCK_HOLD_S after each word and levelled to DUCK_KEY_LEVEL_DB wherever it is
    speech. The hold is the sample-wise max of the envelope and its copies delayed in DUCK_HOLD_STEP_S steps up to
    DUCK_HOLD_S, so a 0.1-0.5 s gap between two words still keys the duck and the bed does not swell inside it, while
    the attack follows the undelayed voice. The key only drives the detector: none of it reaches the mix (the voice's
    own filters are unchanged; _audio_chain pads it after the split). The hold's delayed copies end with the voice (amerge stops on its shortest input), so
    the hold does not run on past the narration's end; `pad` (the voice's apad, with its leading comma) then pads
    the key with silence, so it never ends before the bed."""
    taps = round(DUCK_HOLD_S / DUCK_HOLD_STEP_S) + 1
    lo, hi = DUCK_KEY_BAND
    floor = DUCK_KEY_SPEECH_DB - 10
    level = (f"compand=attacks=0.001:decays=0.05:delay=0.002:points=-90/-90|{floor}/{floor}|{DUCK_KEY_SPEECH_DB}/"
             f"{DUCK_KEY_LEVEL_DB}|0/{DUCK_KEY_LEVEL_DB}")
    labels = [f"{out}_t{k}" for k in range(taps)]
    delayed = "".join(f"[{lab}]adelay=delays={round(k * DUCK_HOLD_STEP_S * 1000)}:all=1[{lab}d];"
                      for k, lab in enumerate(labels) if k)
    merged = "".join(f"[{lab}{'d' if k else ''}]" for k, lab in enumerate(labels))
    held = "val(0)"
    for k in range(1, taps):
        held = f"max({held},val({k}))"
    return (f"[{src}]pan=mono|c0=0.5*c0+0.5*c1,highpass=f={lo},lowpass=f={hi},"
            f"asplit[{out}_a][{out}_b];[{out}_a][{out}_b]amultiply,lowpass=f={DUCK_KEY_SMOOTH_HZ},"
            f"aresample={DUCK_KEY_RATE},aformat=sample_fmts=dbl:sample_rates={DUCK_KEY_RATE}:channel_layouts=mono,"
            f"asplit={taps}{''.join(f'[{lab}]' for lab in labels)};{delayed}"
            f"{merged}amerge=inputs={taps},aeval=exprs='sqrt(max({held},0))':channel_layout=mono,{level},"
            f"aresample={AUDIO_SAMPLE_RATE}{pad}[{out}]")


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
    # the key is padded after its hold, not before: the hold ends with the narration, so the bed comes back up as the
    # voice ends (the loop bridge is the bed's own) instead of DUCK_HOLD_S later
    key_pad = f",{pad_filter[:-1]}" if pad_filter else ""
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
        f"[{vo_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"highpass=f=85,compand=attacks=0.02:decays=0.15:points=-80/-80|-40/-35|-20/-16|0/-10,"
        f"equalizer=f=3500:width_type=q:w=1.2:g=1.8,asplit[v_main][v_sc];"
        f"[v_main]{pad_filter}equalizer=f=7000:width_type=q:w=2.0:g=-2.2[v_voice];"
        f"{_duck_key('v_sc', 'v_key', key_pad)};"
        f"[{bg_idx}:a]aresample={AUDIO_SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"equalizer=f=3200:width_type=q:w=1.5:g=-3.0{bed_pad}[bg_carved];"
        f"[bg_carved][v_key]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}"
        f":attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[bg_ducked];"
        f"[v_voice][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0{cut},aformat=channel_layouts=stereo{card_filter}"
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


MASTER_TOLERANCE_LU = 0.5   # the delivery tolerance the gain is set to (the old search's `tolerance`)


def _limiter_cost_bound(frames: list, gain_db: float, gate_lufs: float) -> float:
    """An upper estimate (LU) of how much the master limiter can lower the integrated loudness of a programme
    whose unlimited mix the analysis measured, at `gain_db`. The limiter only ever turns the programme down, and
    only where a peak would cross its ceiling. Each 400 ms block (the BS.1770 gating block, read from the meter's
    momentary loudness every fourth 100 ms frame) is assumed turned down for the whole block by the largest excess
    of any true peak over the ceiling in its frames and in the frame on either side (attack 2.5 ms, release 80 ms).
    Over the blocks above the relative gate that is never less than the limiter really takes (QF-11: real mixes
    estimate 0.26-0.66 LU where the limiter took at most 0.2)."""
    from .encoded_audio import MASTER_LIMIT
    ceiling = 20 * math.log10(MASTER_LIMIT)
    before = after = 0.0
    for k in range(3, len(frames), 4):
        momentary = frames[k][1]
        if momentary is None or momentary < gate_lufs:
            continue
        peaks = [f[2] for f in frames[max(0, k - 4):k + 2] if f[2] is not None]
        excess = max(0.0, max(peaks, default=-200.0) + gain_db - ceiling)
        energy = 10 ** ((momentary + 0.691) / 10)
        before += energy
        after += energy * 10 ** (-excess / 10)
    return 10 * math.log10(before / after) if after > 0 else 0.0


# A restrained documentary photo curve, not a film-stock emulation (QF-86).
# One 16-bit operation on each photographic still, before scene compositing:
# black and white remain fixed, with a gentle middle-tone contrast adjustment.
PHOTO_CURVE = "curves=master='0/0 0.2/0.195 0.5/0.505 0.8/0.81 1/1'"


def film_grade_filter(grade_bias: float = 0.0, source: Path | None = None,
                      out_dir: Optional[Path] = None) -> str:
    """Photo-only 16-bit curves; the historical API name remains for callers.

    No external or cached LUT is read. The legacy source/out_dir parameters are
    accepted without accessing them. A bounded topic bias preserves both endpoints.
    """
    grade = PHOTO_CURVE
    bias = max(-0.05, min(0.05, float(grade_bias or 0.0)))
    if bias:
        grade += (f":red='0/0 0.5/{0.5 + bias / 2:.3f} 1/1'"
                  f":blue='0/0 0.5/{0.5 - bias / 2:.3f} 1/1'")
    return grade


# The picture chain in 16-bit RGB (QF-02, 2026-11-06). Curves are RGB filters; on the yuv420p stream
# ffmpeg converted to 8-bit RGB and back around each: a 0-64 shadow ramp (56 luma levels) kept 29 in the master.
# The composited picture is read into 16-bit planar RGB (gbrp16le) with the BT.709 matrix.
# Photo curves have already run on the individual stills; the lens and captions run here,
# followed by one conversion to BT.709 yuv420p
# (swscale dithers 16-bit RGB down to 8-bit YUV). ffmpeg's vignette has no 16-bit path, so the lens is drawn on a
# white frame (the same picture_vignette filter) and multiplied into the 16-bit picture.
# No grain (QF-03, 2026-11-06, operator decision): the unseeded noise=alls=8:allf=t+u made every render differ and
# took most of the master's bitrate at the same CRF, and the platforms' re-encodes strip it.
PICTURE_RGB16 = "gbrp16le"


def picture_chain(grade: str, width: int, height: int, *, vignette: bool = True,
                  vignette_enable: str = "", card_split: str = "", captions: str = "", tag: str = "pc",
                  lens_filter: str | None = None) -> str:
    """The master's picture chain, for one filter_complex chain on BT.709 yuv420p: `grade` (film_grade_filter),
    the lens vignette (`vignette_enable`: graphic_vignette_enable), `card_split` (a split whose second
    branch the caller labels, before the captions) and `captions` (the subtitles filter), all in 16-bit RGB, then
    one conversion back to BT.709 yuv420p, tagged. `tag` keeps the chain's two inner labels unique in a graph;
    `lens_filter`: the vignette filter drawn on the lens frame (default picture_vignette(width, height))."""
    yuv = ""
    if grade.startswith("eq="):   # the fallback grade's eq works on YUV, before the RGB curves
        yuv, grade = grade.split(",", 1)
        yuv += ","
    chain = f"{yuv}{BT709_TO_RGB},format={PICTURE_RGB16},{grade}"
    if vignette:
        lens = (f"color=c=white:s={width}x{height}:r={VIDEO_FPS},format=rgb24,{lens_filter or picture_vignette(width, height)},"
                f"format={PICTURE_RGB16}")
        chain += (f"[{tag}_pic];{lens}[{tag}_lens];"
                  f"[{tag}_pic][{tag}_lens]blend=all_mode=multiply:shortest=1{vignette_enable}")
    chain += f",{card_split}" if card_split else ","
    if captions:
        chain += f"{captions},"
    return chain + RGB_TO_BT709


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


# Freeze pass G1 (2026-11-04): a graphic's text inside a 9:16 caption plate's reach (the plate and CAPTION_GUARD_PAD
# around it) gets an opaque plate. The text is the clip's <clip>.contrast.json element boxes, else the poster still's
# bright, near-neutral pixels (luma over CAPTION_GUARD_INK_LUMA, chroma within CAPTION_GUARD_INK_CHROMA, more than
# CAPTION_GUARD_INK_SHARE of the area).
CAPTION_GUARD_PAD = 24
CAPTION_GUARD_INK_LUMA = 140
CAPTION_GUARD_INK_SHARE = 0.005
CAPTION_GUARD_INK_CHROMA = 48   # the poster scan's text pixels are near-neutral: max - min channel at most this
CAPTION_GUARD_ALPHA = "&H08&"   # the plate's fill: 97% opaque (CapBox draws &H20, 87%)
CAPTION_GUARD_GRAPHICS_BOTTOM = _graphics_box()[3]   # safe_zones.graphics_box's bottom edge: y 1090 at 1080 x 1920


def caption_graphic_clips(scene_images: list[Path], durations: list[float]) -> list[tuple[float, float, Path]]:
    """(start, end, clip) of each graphic clip on screen (caption_graphic_intervals, with the clip)."""
    out, start = [], 0.0
    for item, dur in zip(scene_images, durations):
        if Path(item).name.startswith("graphic_"):
            out.append((start, start + dur, Path(item)))
        start += dur
    return out


def graphic_caption_intrusion(clip: Path, rect: tuple[int, int, int, int], width: int = VIDEO_WIDTH,
                              height: int = VIDEO_HEIGHT) -> bool:
    """Whether a graphic clip's text reaches into `rect` (x0, y0, x1, y1 in frame px): any element box of its
    <clip>.contrast.json that overlaps it, else (no boxes recorded) the clip's poster still (graphic_*.jpg) with
    more than CAPTION_GUARD_INK_SHARE of the rect brighter than CAPTION_GUARD_INK_LUMA."""
    x0, y0, x1, y1 = rect
    sidecar = Path(str(clip) + CAPTION_CONTRAST_SIDECAR)
    if sidecar.is_file():
        try:
            elements = json.loads(sidecar.read_text(encoding="utf-8")).get("elements") or []
        except (OSError, ValueError):
            elements = []
        boxes = [e["box"] for e in elements if isinstance(e, dict) and len(e.get("box") or []) == 4]
        if boxes:
            return any(bx0 < x1 and bx1 > x0 and by0 < y1 and by1 > y0 for bx0, by0, bx1, by1 in boxes)
    poster = Path(clip).with_suffix(".jpg")
    if not poster.is_file():
        return False
    import numpy as np
    with Image.open(poster) as im:
        rgb = im.convert("RGB")
        if rgb.size != (width, height):
            rgb = rgb.resize((width, height))
        a = np.asarray(rgb.crop((max(0, x0), max(0, y0), min(width, x1), min(height, y1)))).astype(int)
    if not a.size:
        return False
    luma = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    # text is drawn in bone or white: bright and near-neutral (a map's coloured coastline is no text)
    ink = (luma > CAPTION_GUARD_INK_LUMA) & (a.max(-1) - a.min(-1) <= CAPTION_GUARD_INK_CHROMA)
    return float(ink.mean()) > CAPTION_GUARD_INK_SHARE


def _fits_wide(phrase: Any, max_width: int, max_chars: int | None = CAPTION_WIDE_MAX_CHARS) -> bool:
    """A 16:9 burst fits one line: at most `max_chars` characters (CAPTION_WIDE_MAX_CHARS; None for a bonded name,
    freeze pass N2) and inside `max_width` (its plate included) in Montserrat Bold at CAPTION_WIDE_SIZE, so it is
    drawn unshrunk. Measured unkerned, as libass draws it (unkerned_length, operator decision 2026-11-05)."""
    from .typography import get_font
    text = " ".join(_phrase_words(phrase))
    if max_chars is not None and len(text) > max_chars:
        return False
    font = get_font(CAPTION_WIDE_FONT, CAPTION_WIDE_SIZE, weight=700)
    return unkerned_length(font, text) + 2 * CAPTION_PAD_X <= max_width


_ARTICLES = {"the", "a", "an"}
_CUE_OPENERS = (DANGLING_TAILS - {"is", "was", "are", "were", "been", "being", "this", "that", "these", "those",
                                  "its", "their", "our", "my", "your", "his", "her", "the", "a", "an"})


CUE_AUXILIARIES = {"has", "have", "had", "is", "was", "are", "were", "will", "would", "can", "could"}
SIDECAR_TAIL_WORDS = 3     # S1: a cue never ends inside its sentence's last three words
SIDECAR_TAIL_S = 1.0       # nor inside its last second; a sentence ends mid-cue only with a second either side
NEW_CUE_COST = 10000.0     # fix round 1: a cue end the round-4 cues do not have (partition_sidecar_cues)


def _cue_break_ok(raw: list[str], k: int, bonds: set[int], relaxed: bool = False) -> bool:
    """Whether a cue or a cue line may end after word k (freeze pass C12, 2026-11-04): never inside a bond (a
    figure group, a name, the words leading into one) or an atomic pair, after an article or a dangling word
    (_dangles), nor before a sentence's last word (a cue never opens on AD. or 1978.). `relaxed` allows an end on a
    pre-modifier, possessive or hyphenated measure (never an article, preposition or conjunction)."""
    if k + 1 >= len(raw):
        return True
    word = raw[k]
    if TERMINAL_PUNCT.search(word) and not _is_abbreviation(word):
        return True
    if k in bonds or _is_atomic_boundary(word, raw[k + 1]):
        return False
    if _bare(raw[k + 1]) == "ago" and _bare(word) in TIME_UNITS:
        return False   # review round 4: a span of time keeps its AGO (104 MILLION YEARS AGO,)
    if _bare(word) in _ARTICLES or (_bare(word) in DANGLING_TAILS and _bare(word) in DANGLING_ENDS):
        return False
    # Freeze pass S1 (2026-11-04): never an auxiliary at a cue or line end, nor a dangling word but where nothing
    # else fits (`relaxed`, which costs the cue: partition_sidecar_cues)
    if _bare(word) in CUE_AUXILIARIES and not CLAUSE_PUNCT.search(word):
        return False
    if not relaxed and _dangles(word):
        return False
    nxt = raw[k + 1]
    return not (TERMINAL_PUNCT.search(nxt) and not _is_abbreviation(nxt))


def _long_name_line_break(words: list[str], k: int, named: set[int], max_chars: int) -> bool:
    """Freeze pass N2: a proper name longer than a cue line may break its line before a connector (THE
    INTERNATIONAL UNION / OF GEOLOGICAL SCIENCES), and only there."""
    if k - 1 not in named or _bare(words[k]) not in NAME_PARTICLES:
        return False
    a, b = k - 1, k
    while a - 1 in named:
        a -= 1
    while b in named:
        b += 1
    if a > 0 and _bare(words[a - 1]) in _ARTICLE_SLOT:
        a -= 1   # the article goes with the name (THE INTERNATIONAL UNION ...)
    return len(" ".join(words[a:b + 1])) > max_chars


def _break_rank(raw: list[str], k: int) -> int:
    """How good a break after word k is: a sentence end 3, a comma or semicolon 2, before a conjunction or
    preposition 1, else 0."""
    word = raw[k]
    if TERMINAL_PUNCT.search(word) and not _is_abbreviation(word):
        return 3
    if CLAUSE_PUNCT.search(word):
        return 2
    if k + 1 < len(raw) and _bare(raw[k + 1]) in _CUE_OPENERS:
        return 1
    return 0


SIDECAR_WEAK_BREAK_COST = 150   # fix round 2: a weak line break (_line_break_weak) loses to unbalanced lines (100)


def _line_break_weak(words: list[str], k: int) -> bool:
    """Whether a cue line break before word k reads badly though the cue rules allow it (fix round 2, the sidecar
    lines HEAD 0036b18 kept whole): it falls between two capitalised words with no punctuation between (a name the
    burst rules do not bond: ADRAR / PLATEAU, ROME'S / PANTHEON), or the bottom line opens on the last word of a
    clause (DESERT / LANDMARK, CONCENTRIC ...). Topic-agnostic: capitals and punctuation only."""
    if k <= 0 or k >= len(words):
        return False
    before, after = words[k - 1], words[k]
    if _ends_clause(before):
        return False
    caps = all(w.strip("\"'“”‘’()[]")[:1].isupper() for w in (before, after))
    return caps or (k + 1 < len(words) and _ends_clause(after) and not TERMINAL_PUNCT.search(after))


def _wrap_cue(words: list[str], max_chars: int = SIDECAR_MAX_LINE_CHARS, max_lines: int = SIDECAR_MAX_LINES,
              bonds: set[int] | None = None, strict: bool = False, relaxed: bool = False) -> str | None:
    """The cue's text in at most `max_lines` lines of at most `max_chars`, or None when it does not fit. Never
    breaks a line after a position in `bonds` (default caption_bonds(words), round 4: ROUGHLY / 280 FEET is never
    split across the lines). Freeze pass C12: nor inside a name or an atomic pair, after an article or a dangling
    word (_cue_break_ok); the shorter line is at least SIDECAR_LINE_BALANCE of the longer (with `strict`, else that
    is only preferred), a break after punctuation is preferred, and when the two lines are close the longer is the
    bottom one."""
    text = " ".join(words)
    if len(text) <= max_chars:
        return text
    if max_lines < 2:
        return None
    if bonds is None:
        bonds = caption_bonds(words)
    best: tuple[float, str] | None = None
    named = name_bonds(words)
    for k in range(1, len(words)):
        long_name = _long_name_line_break(words, k, named, max_chars)
        if not _cue_break_ok(words, k - 1, bonds, relaxed) and not long_name:
            continue
        # fix round 2 (HEAD-regression gate, sidecar lines): a break only the relaxed rules allow (a pre-modifier or
        # possessive end), one inside a run of capitalised words (ROME'S / PANTHEON, ADRAR / PLATEAU) or one that
        # opens the bottom line on the last word of a clause (MASSIVE DESERT / LANDMARK, ...) is never strict, and
        # costs more than unbalanced lines
        weak = not long_name and (not _cue_break_ok(words, k - 1, bonds) or _line_break_weak(words, k))
        if weak and strict:
            continue
        top, bottom = " ".join(words[:k]), " ".join(words[k:])
        if len(top) > max_chars or len(bottom) > max_chars:
            continue
        if len(words) >= 4 and (k == 1 or k == len(words) - 1):
            continue   # S1: no line of a cue of four words or more is a single word (EARTHQUAKE / DAMAGE)
        short, long_ = sorted((len(top), len(bottom)))
        balanced = short >= SIDECAR_LINE_BALANCE * long_
        if strict and not balanced:
            continue
        score = ((0 if balanced else 100) + (long_ - short) - 4 * _break_rank(words, k - 1)
                 + (2 if len(top) > len(bottom) else 0) + (SIDECAR_WEAK_BREAK_COST if weak else 0))
        if best is None or score < best[0]:
            best = (score, f"{top}\n{bottom}")
    return best[1] if best else None


def _head_break_ok(words: list[str], k: int, bonds: set[int]) -> bool:
    """Whether HEAD 0036b18's line break before word k of a cue is one the freeze-pass rules allow (fix round 2): both
    lines fit, neither is a lone word of a cue of four or more, the top line ends on no article, nothing is inside a
    bond, a name, an atomic pair or a span of time, the bottom line opens on no sentence's last word, and the break is
    not weak (_line_break_weak)."""
    if not 0 < k < len(words) or (len(words) >= 4 and (k == 1 or k == len(words) - 1)):
        return False
    if max(len(" ".join(words[:k])), len(" ".join(words[k:]))) > SIDECAR_MAX_LINE_CHARS:
        return False
    # fix round 3 (review: the sidecar lines HEAD 0036b18 kept, NAMED AFTER / POLISH BOOK DEALER, SHIELDING THESE /
    # SHALLOW TRENCHES, BURNED AND / WEDGED AWAY): HEAD's line may end on a preposition, conjunction, demonstrative or
    # auxiliary, never on an article; the caller takes it only over a freeze-pass break mid-phrase (_break_rank 0)
    word = words[k - 1]
    if not CLAUSE_PUNCT.search(word) and _bare(word) in _ARTICLES:
        return False
    if (k - 1) in bonds or _is_atomic_boundary(word, words[k]) or (k - 1) in name_bonds(words):
        return False
    if _bare(words[k]) == "ago" and _bare(word) in TIME_UNITS:
        return False
    nxt = words[k]
    if TERMINAL_PUNCT.search(nxt) and not _is_abbreviation(nxt):
        return False
    return not _line_break_weak(words, k)


SIDECAR_HEAD_LINE_COST = 40.0   # fix round 3: a new cue's line break between two words a HEAD 0036b18 line held


def _inside_noun_phrase(raw: list[str], k: int) -> bool:
    """Fix round 3 (review: THIS CIRCULAR || GEOLOGICAL FORMATION): whether a cut after word k falls inside the noun
    phrase a determiner opens: a determiner (THE, A, THIS, THEIR, ...) at most three words before, and from it to
    the word after the cut only lower-case content words with no punctuation. Topic-agnostic: word shapes and the
    closed function-word lists only."""
    if k + 1 >= len(raw):
        return False
    for j in range(k, max(-1, k - 4), -1):
        word = raw[j]
        if CLAUSE_PUNCT.search(word) or TERMINAL_PUNCT.search(word):
            return False
        if _bare(word) in _ARTICLE_SLOT:
            inner = all(w[:1].islower() and _bare(w) not in FUNCTION_WORDS and not CLAUSE_PUNCT.search(w)
                        and not TERMINAL_PUNCT.search(w) for w in raw[j + 1:k + 1])
            return j < k and inner and raw[k + 1][:1].islower() and _bare(raw[k + 1]) not in FUNCTION_WORDS
        if not word[:1].islower() or _bare(word) in FUNCTION_WORDS:
            return False
    return False


def partition_sidecar_cues(words: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """Closed-caption cues for the .srt/.vtt sidecars, (start, end, text): caption review round 2, finding 7. A cue
    holds up to SIDECAR_MAX_LINES lines of SIDECAR_MAX_LINE_CHARS and is on screen SIDECAR_MIN_CUE to
    SIDECAR_MAX_CUE seconds. Text is the narration's, word for word.

    Freeze pass C12 (2026-11-04): the cues are chosen together (fewest, best-ended cues). A cue ends best at a
    sentence end, then a comma or semicolon, then before a conjunction or preposition, and never inside a bond, a
    name or an atomic pair, after an article or a dangling word, nor so that the next cue opens on a sentence's last
    word (_cue_break_ok). A cue ends at a sentence end that falls in its last SIDECAR_SENTENCE_TAIL of text. A cue
    holds three words or more, or whole sentences, and is up at least SIDECAR_MIN_CUE_HARD before the next one
    (an orphan joins its neighbour); its lines break by the same rules, balanced (_wrap_cue). A cue reading faster
    than CAPTION_MAX_CPS costs more. Where no cue split meets every rule, the round-4 greedy split is used.

    Fix round 1 (operator decision 2026-11-04, the HEAD-regression gate): the round-4 cues are the base
    (round4_cue_ends). A cue end the base does not have costs NEW_CUE_COST, more than every other cost together, so
    one is made only where a rule forces it (a bond, SIDECAR_MAX_CUE, the two lines of SIDECAR_MAX_LINE_CHARS); a
    base cue end is always allowed (the rules above only rank it), and a base cue whose lines no other wrap fits takes
    the round-4 wrap."""
    timed = [w for w in words if str(w.get("word", "")).strip()]
    n = len(timed)
    if not n:
        return []
    raw = [str(w["word"]).strip() for w in timed]
    # the base's cue ends, but never one on an article (operator decision 2026-11-04: nothing ends on THE, A or AN)
    base_ends = {b for b in round4_cue_ends(timed) if _bare(raw[b]) not in _ARTICLES or _ends_clause(raw[b])}
    # nor one inside a proper name (fix round 2, operator decision 2026-11-04 as written: only a name bond or a hard
    # width limit forces a cut the base does not have, so every other base cue end stays, a figure link's included)
    named = name_bonds(raw)
    base_ends -= named
    # a base cue end at a figure link (HEAD 0036b18's 196 | AD., 13,000 | POUNDS) stays allowed but costs, so a split
    # that holds the link without a cue end the base lacks is taken (fix round 2)
    soft_ends = _base_figure_links(timed, base_ends) | {b for b in base_ends if b + 1 < n
                                                        and _r4_atomic_boundary(raw[b], raw[b + 1])}
    bonds = caption_bonds(timed) - base_ends
    head_texts: dict[tuple[int, int], str] = {}
    head_line_pairs: set[int] = set()   # fix round 3: the word pairs (k, k + 1) a HEAD 0036b18 cue line holds
    at = 0
    for _, _, head_text in _r4_sidecar_cues(timed):
        size = len(head_text.split())
        head_texts[(at, at + size - 1)] = head_text
        for line in head_text.split("\n"):
            n_line = len(line.split())
            head_line_pairs.update(range(at, at + n_line - 1))
            at += n_line
    starts = [float(w["start"]) for w in timed]
    ends = [_word_end(w) for w in timed]
    terminal = [bool(TERMINAL_PUNCT.search(r)) and not _is_abbreviation(r) for r in raw]
    sentence_start = [i == 0 or terminal[i - 1] for i in range(n)]
    # S1: a cue never ends inside the last SIDECAR_TAIL_WORDS words or SIDECAR_TAIL_S of its sentence
    sent_end = [0] * n
    e = n - 1
    for k in range(n - 1, -1, -1):
        if terminal[k]:
            e = k
        sent_end[k] = e
    in_tail = [not terminal[k] and k + 1 < n and (sent_end[k] - k <= SIDECAR_TAIL_WORDS
                                                   or ends[sent_end[k]] - ends[k] < SIDECAR_TAIL_S)
               for k in range(n)]
    can_end = [_cue_break_ok(raw, k, bonds) and not in_tail[k] for k in range(n)]
    can_end_relaxed = [_cue_break_ok(raw, k, bonds, relaxed=True) and not in_tail[k] for k in range(n)]
    max_words = SIDECAR_MAX_LINE_CHARS * SIDECAR_MAX_LINES // 2   # a word is at least a letter and a space

    relax_cost = 40.0   # a cue or line ending on a pre-modifier, or unbalanced lines, only where nothing else fits
    texts: dict[tuple[int, int], tuple[str | None, float]] = {}

    def text_of(a: int, b: int) -> tuple[str | None, float]:
        """The cue's wrapped text and what it costs: the strict wrap, else a relaxed one (relax_cost). Fix round 2
        (the HEAD-regression gate, sidecar lines): a cue HEAD 0036b18 has keeps HEAD's line break where that break
        is allowed (_head_break_ok) and the freeze-pass wrap would break mid-phrase instead (no punctuation before it,
        no conjunction or preposition after it: AN ORIGINAL STONE / MOSAIC against HEAD's AN ORIGINAL / STONE
        MOSAIC)."""
        if (a, b) not in texts:
            local = {k - a for k in bonds if a <= k < b}
            text = _wrap_cue(raw[a:b + 1], bonds=local, strict=True)
            cost_ = 0.0
            if text is None:
                text, cost_ = _wrap_cue(raw[a:b + 1], bonds=local, relaxed=True), relax_cost
            head = head_texts.get((a, b))
            if head is not None and "\n" in head and head != text:
                k_head = len(head.split("\n")[0].split())
                k_ours = len(text.split("\n")[0].split()) if text is not None and "\n" in text else None
                # fix round 3: the freeze-pass break is mid-phrase only where the bottom line opens on a content
                # word (TRACKING / ITS LOCATION, CONCLUDED IT / WAS INCINERATED break before a function word)
                if _head_break_ok(raw[a:b + 1], k_head, local) and (
                        k_ours is None or (_break_rank(raw[a:b + 1], k_ours - 1) == 0
                                           and _bare(raw[a + k_ours]) not in FUNCTION_WORDS)):
                    text, cost_ = head, 0.0
            texts[(a, b)] = (text, cost_)
        return texts[(a, b)]

    def _base_wrap(a: int, b: int) -> str | None:
        """A base cue no freeze-pass wrap fits: the round-4 wrap, its line break outside a bond or an atomic pair
        where one fits (fix round 1)."""
        words_ab = raw[a:b + 1]
        local = {k - a for k in bonds if a <= k < b}
        held = local | {k for k in range(len(words_ab) - 1) if _is_atomic_boundary(words_ab[k], words_ab[k + 1])}
        # fix round 2: before the bare round-4 wrap, one that keeps every proper name whole (HAL SAFLIENI: NICHE IN
        # THE / ORACLE ROOM split THE ORACLE ROOM where the lead-in bond on IN left no other break)
        names_local = {k - a for k in named if a <= k < b}
        return (_r4_wrap_cue(words_ab, bonds=held) or _r4_wrap_cue(words_ab, bonds=local)
                or _r4_wrap_cue(words_ab, bonds=names_local) or _r4_wrap_cue(words_ab, bonds=set()))

    def cost(a: int, b: int) -> float | None:
        on_base = b in base_ends or b == n - 1
        if (not can_end_relaxed[b] and not on_base) or ends[b] - starts[a] > SIDECAR_MAX_CUE + 1e-9:
            return None
        if b - a + 1 < 3 and not (sentence_start[a] and terminal[b]) and not on_base:
            return None
        if b + 1 < n and starts[b + 1] - starts[a] < SIDECAR_MIN_CUE_HARD - 1e-9 and not on_base:
            return None
        text, relax = text_of(a, b)
        if text is None and on_base:
            text, relax = _base_wrap(a, b), relax_cost
        if text is None:
            return None
        chars = sum(len(r) for r in raw[a:b + 1]) + b - a
        c = 10.0 + 8 * (3 - _break_rank(raw, b)) + relax + (0.0 if can_end[b] else relax_cost)
        c += 0.0 if on_base else NEW_CUE_COST
        # fix round 3 (review, the HEAD-regression gate for sidecar lines): where the cues must change, a line break
        # between two words a HEAD line held costs, so the change keeps HEAD's lines where it can
        # (ANCIENT WORKERS BURNED AND / WEDGED AWAY UPPER ROCK, stays HEAD's cue), and a new cue end inside a noun
        # phrase costs too (THIS CIRCULAR || GEOLOGICAL FORMATION)
        if "\n" in text and (a, b) not in head_texts:
            if a + len(text.split("\n")[0].split()) - 1 in head_line_pairs:
                c += SIDECAR_HEAD_LINE_COST
        if not on_base and _inside_noun_phrase(raw, b):
            c += relax_cost
        elif not on_base and b + 1 < n and _break_rank(raw, b) == 0 and _bare(raw[b + 1]) not in _ARTICLE_SLOT:
            c += 4.0   # a new cue end is best before a determiner, which opens a phrase (FORMED || THIS CIRCULAR ...)
        c += 2 * relax_cost if b in soft_ends else 0.0
        if b >= 1 and b + 1 < n and not terminal[b] and CLAUSE_PUNCT.search(raw[b - 1]) and not CLAUSE_PUNCT.search(
                raw[b]):
            c += relax_cost   # review round 4: never one word of the next clause at a cue's end (TIMELINE: DATING)
        at = 0
        for k in range(a, b):
            at += len(raw[k]) + 1
            if terminal[k] and (ends[k] - starts[a] < SIDECAR_TAIL_S - 1e-9
                                or ends[b] - starts[k + 1] < SIDECAR_TAIL_S - 1e-9):
                c += 200   # S1: a sentence ends mid-cue only with a second either side, unless no cue can (LOOK.)
            if terminal[k] and at >= (1 - SIDECAR_SENTENCE_TAIL) * chars:
                c += 30   # a sentence ends late in the cue: the cue should end there
        held = max(ends[b] + 0.1, starts[a] + SIDECAR_MIN_CUE)
        if b + 1 < n:
            held = min(held, starts[b + 1])
        held -= starts[a]
        if held > 0 and chars / held > CAPTION_MAX_CPS:
            c += 2 * (chars / held - CAPTION_MAX_CPS)
        return c

    best: list[tuple[float, int] | None] = [None] * (n + 1)
    best[n] = (0.0, n)
    for a in range(n - 1, -1, -1):
        for b in range(a, min(n, a + max_words)):
            if best[b + 1] is None:
                continue
            c = cost(a, b)
            if c is None:
                continue
            total = c + best[b + 1][0]
            if best[a] is None or total < best[a][0]:
                best[a] = (total, b)
    if best[0] is None:
        return _greedy_sidecar_cues(timed, bonds)
    groups: list[tuple[int, int]] = []
    a = 0
    while a < n:
        b = best[a][1]
        groups.append((a, b))
        a = b + 1
    cues: list[tuple[float, float, str]] = []
    for gi, (a, b) in enumerate(groups):
        start = starts[a]
        end = max(ends[b] + 0.1, start + SIDECAR_MIN_CUE)
        if gi + 1 < len(groups):
            end = min(end, starts[groups[gi + 1][0]])
        end = max(min(end, start + SIDECAR_MAX_CUE), start + 0.02)
        cues.append((start, end, text_of(a, b)[0] or _base_wrap(a, b) or " ".join(raw[a:b + 1])))
    return cues


def _greedy_sidecar_cues(timed: list[dict[str, Any]], bonds: set[int]) -> list[tuple[float, float, str]]:
    """The round-4 greedy cue split (partition_sidecar_cues' fallback where no split meets every rule)."""
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
        if held >= SIDECAR_MIN_CUE and ((TERMINAL_PUNCT.search(word) and not _is_abbreviation(word))
                                        or (CLAUSE_PUNCT.search(word) and text_len >= SIDECAR_MAX_LINE_CHARS)):
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
        self.last_mastering = None   # what the last calibrate_gain measured (QF-11, the composition record)

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
        graphic_clips: list[tuple[float, float, Path]] | None = None,
        timeline_offset_s: float = 0.0,
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
        5. 9:16: Genuine small-cap glyphs, preserving the narration's case and accents.
        While a graphic is on screen (`graphic_intervals`) the captions carry no gold: one gold event at a time.
        Round 4: no burst splits a hedge from its figure or term, or a range (caption_bonds); a 16:9 burst carries
        one gold span (single_gold_span); with `first_frame_whole` a Reel's first burst is whole from frame 0 when
        its narration starts that soon (burst_timings); with `cuts` a 9:16 burst's show and hide snap to picture cuts
        (snap_to_cuts). Both are 9:16 only: a 16:9 call ignores them. compose_video passes both for a 9:16 render;
        without them the timing is as in round 3.
        The designed contrast of bone and gold on the plate over a white ground is written beside the file
        (<stem>.contrast.json, caption_contrast_record); under brand.TEXT_CONTRAST the build fails.
        Freeze pass G1: with `graphic_clips` ((start, end, clip) as caption_graphic_clips reads them), a 9:16 burst
        over a graphic whose text reaches into its plate (graphic_caption_intrusion) draws its plate near-opaque
        (CAPTION_GUARD_ALPHA); each is listed in self.last_caption_graphic_overlap and logged as a warning.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries: one Montserrat Bold line, in the
        static 700 instance derived from the one Montserrat source, font_assets.WIDE_FAMILY). Either derived face is
        embedded in the ASS file ([Fonts]), so libass draws it without an installed font.
        """
        from .font_assets import (
            DERIVED_FAMILIES,
            WIDE_FAMILY,
            ass_embedded_font,
            derived_font,
        )
        from .typography import get_font
        is_widescreen = width > height
        caption_family = WIDE_FAMILY if is_widescreen else _caption_family()
        # QF-94: a word no bundled face draws is on the review card under "captions" (libass draws it)
        _record_caption_faces(words, caption_family, upper=not is_widescreen and not CAPTION_SMALL_CAPS)
        # the face the captions are measured in (typography.get_font: derived from the bundled fonts), embedded
        embedded_font = derived_font(caption_family) if caption_family in DERIVED_FAMILIES else None
        if is_widescreen:
            eff_margin_v = margin_v if margin_v is not None else round(CAPTION_WIDE_MARGIN_V * height / 1080)
        else:
            eff_margin_v = margin_v if margin_v is not None else CAPTION_MARGIN_V
        # 16:9: three quarters of the frame, and never a plate over CAPTION_WIDE_MAX_PLATE (freeze pass C1)
        max_caption_width = (min(int(width * 0.75), round(CAPTION_WIDE_MAX_PLATE * width / 1920)) if is_widescreen
                             else MAX_CAPTION_WIDTH)
        recon_plate = _brand.ass_colour(_brand.INK, RECON_PLATE_ALPHA)

        if is_widescreen:
            wide_px = round(CAPTION_WIDE_SIZE * height / 1080)
            wide_fs = ass_font_size(CAPTION_WIDE_FONT, wide_px, 700)
            badge_px = round(RECON_SIZE_WIDE * height / 1080)
            badge_fs = ass_font_size(RECON_FONT, badge_px, 600)
            styles = (
                f"Style: CapText,{caption_family},{wide_fs},{COL_PAST},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
                f"Style: CapBox,{caption_family},{wide_fs},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconBadge,{RECON_FONT},{badge_fs},{COL_ACTIVE},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconPlate,{RECON_FONT},{badge_fs},{recon_plate},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            )
        else:
            badge_px = RECON_SIZE_PORTRAIT
            badge_fs = ass_font_size(RECON_FONT, badge_px, 600)
            # CapText Bold 0 (QF-87): Anton has one weight (400); Bold -1 asked libass for 700, and libass thickened
            # the outlines synthetically (about 10% more ink than the face draws). The caption face draws as designed.
            styles = (
                f"Style: CapText,{caption_family},{FONT_SIZE_ASS},{COL_PAST},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,{REEL_LETTER_SPACING:g},0,1,0,0,5,0,0,0,1\n"
                f"Style: CapBox,{caption_family},{FONT_SIZE_ASS},{COL_BACK},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconBadge,{RECON_FONT},{badge_fs},{COL_ACTIVE},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1\n"
                f"Style: ReconPlate,{RECON_FONT},{badge_fs},{recon_plate},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
            )
        # YCbCr Matrix: None (QF-02): the captions are drawn on the 16-bit RGB picture, so libass writes the colours as
        # they are and the master's one BT.709 conversion encodes them; a TV.709 header (QF-01, drawn on BT.709 YUV)
        # squeezed them into TV range on RGB (brand gold 242/182/48 came out about 223/172/56).
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        key = emphasis_indices(words, emphasis)
        hedge_of = hedge_figures(words, emphasis)  # a hedge's gold waits for its figure (round 3)
        phrases = caption_phrases(words, emphasis, is_widescreen, max_caption_width)
        # The whole first frame is a Reel (9:16) feature only: a 16:9 documentary's first line appears with its
        # narration, as in round 3 (round 4 fix: it jumped to 0.0, up to 0.6 s before the voice).
        if is_widescreen:
            first_frame_whole = None
        timings = burst_timings(phrases, first_frame_whole=first_frame_whole, dwell=not is_widescreen)
        whole_first = bool(phrases) and timings[0][0] == 0.0 and first_frame_whole is not None and all(
            r == 0.0 for r in timings[0][2])
        if not is_widescreen:
            timings = snap_to_cuts(phrases, timings, cuts)
        raw_events: list[tuple[int, float, float, str, str]] = []  # (layer, start, end, style, text)

        # 9:16: centred in the Reel box (x 480 at 1080 wide: the organic box's right rail is wider than its left)
        center_x = width // 2 if is_widescreen else reel_caption_centre_x(width, height)
        caption_span = reel_caption_span(width, height)
        graphic_spans = list(graphic_intervals or [])

        # Pass 1: each burst's geometry
        geo: list[dict[str, Any]] = []
        base = 0
        for pi, phrase in enumerate(phrases):
            show, hide, _ = timings[pi]
            lines, two = None, None
            has_key = any(base + j in key for j in range(len(phrase)))
            shown = _phrase_words(phrase)
            if is_widescreen:
                font = get_font(CAPTION_WIDE_FONT, wide_px, weight=700)
                text_w = unkerned_length(font, " ".join(_ass_escape(w) for w in shown))   # as libass draws it
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
                # One size per Reel: bursts are formed to fit at a CAPTION_LADDER rung; only a single over-long word
                # shrinks further. Freeze pass N1: a name too wide for one line draws on two (reel_caption_lines).
                lines = reel_caption_lines(phrase) if max_caption_width == MAX_CAPTION_WIDTH else None
                fit = lines[1] if lines else _caption_fit_size(phrase, EMPHASIS_POP / 100 if has_key else 1.0,
                                                               max_width=max_caption_width)
                ass_size = FONT_SIZE_ASS if fit >= FONT_SIZE else round(fit / 0.5625)
                size_tag = f"{{\\fs{ass_size}}}" if fit < FONT_SIZE else ""
                if lines:
                    parts = [shown[:lines[0]], shown[lines[0]:]]
                    text_w = math.floor(max(_caption_ink_width(pw, fit) for pw in parts))   # as measure_caption_phrase
                    cap_h = max(30, round(reel_cap_height(fit)))
                else:
                    text_w, cap_h = measure_caption_phrase(phrase, fit, CAPTION_SC_SCALE)
                # The drawn ink plus CAPTION_INK_FIT_PAD a side (freeze pass C5: libass's own ink, which the burst
                # builder fits to the ink budget; operator decision 2026-11-04: the compliant 28 px, not 40), never
                # wider than the width every Reel platform leaves free; the plate is centred on the ink, not on the
                # advance box libass centres on the anchor
                box_w = min(round(text_w + CAPTION_INK_FIT_PAD * 2), caption_span)
                ink_dx = 0.0 if lines else _caption_ink_offset(shown, fit)
                box_h = REEL_PLATE_H
                # The pill ends CAPTION_REEL_CLEARANCE above the band the platforms draw over (eff_margin_v =
                # safe_zones.REEL.bottom; on the first publication its lower half sat under Meta's caption row).
                box_y = height - eff_margin_v - CAPTION_REEL_CLEARANCE - box_h
                # \an5 centres the line box (ass_size tall) on \pos; the line is capitals, so the cap band (cap top to
                # baseline) is centred in the plate (round 3: optically centred, was a fixed 7.5 px nudge).
                _, asc, desc = _font_vmetrics(caption_family)
                baseline = asc * ass_size / (asc + desc)
                ink_mid = (baseline - cap_h / 2) - ass_size / 2
                center_y = round(box_y + box_h / 2 - ink_mid)
                if lines:
                    # Two lines (N1; QF-87): the plate grows upward from the one-line plate's bottom edge to hold the
                    # two lines' measured ink, their baselines a pitch apart (_caption_two_line_geometry); each
                    # line's ink is centred on the plate's centre
                    pitch, box_h, lower_offset = _caption_two_line_geometry(parts, fit)
                    box_y = height - eff_margin_v - CAPTION_REEL_CLEARANCE - box_h
                    center_y = round(box_y + box_h + lower_offset)
                    two = {"k": lines[0], "y": [center_y - pitch, center_y],
                           "dx": [_caption_ink_offset(pw, fit) for pw in parts]}
            in_graphic = any(show < b and hide > a for a, b in graphic_spans)
            geo.append({"show": show, "hide": hide, "box_w": box_w, "box_h": box_h, "box_y": box_y,
                        "center_y": center_y, "size_tag": size_tag, "base": base, "gold": not in_graphic,
                        "ink_dx": 0.0 if is_widescreen else ink_dx, "two": two})
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
        overlaps: list[dict[str, Any]] = []
        self.last_caption_graphic_overlap = overlaps
        for pi, phrase in enumerate(phrases):
            g = geo[pi]
            show, hide, reveals = timings[pi]
            shown = _phrase_words(phrase)
            base = g["base"]
            box_w, box_h = g["box_w"], g["box_h"]
            box_x = round(center_x + g["ink_dx"] - box_w / 2)
            if not is_widescreen:   # never outside the width every Reel platform leaves free
                half = caption_span / 2
                box_x = max(round(center_x - half), min(box_x, round(center_x + half) - box_w))
            whole = pi == 0 and whole_first   # round 4: the first burst whole and settled on frame 0
            fade_in = 0 if is_widescreen or whole or (pi > 0 and joined_next[pi - 1]) else CAPTION_PLATE_FADE_IN_MS
            fade_out = 0 if is_widescreen or joined_next[pi] else CAPTION_PLATE_FADE_OUT_MS
            fad = f"\\fad({fade_in},{fade_out})" if fade_in or fade_out else ""

            # Layer 0: the plate, fixed for the whole burst (sized to every word of it, spoken or not)
            path = rounded_rect_path(box_w, box_h, CAPTION_BORDER_RADIUS)
            guard = ""
            if graphic_clips and not is_widescreen:   # G1: a graphic's text inside the plate's reach
                rect = (box_x - CAPTION_GUARD_PAD, g["box_y"] - CAPTION_GUARD_PAD, box_x + box_w + CAPTION_GUARD_PAD,
                        g["box_y"] + box_h + CAPTION_GUARD_PAD)
                hits = [Path(c) for a, b, c in graphic_clips
                        if show < b and hide > a and graphic_caption_intrusion(Path(c), rect, width, height)]
                if hits:
                    guard = f"\\1a{CAPTION_GUARD_ALPHA}"
                    for clip in hits:
                        overlaps.append({"burst": pi, "clip": clip.name, "rect": list(rect),
                                         "text": " ".join(shown)})
                        log.warning("Caption burst %d (%s) sits over the text of %s: its plate is drawn opaque. "
                                    "Graphics owner: the graphic breaches safe_zones.graphics_box (bottom y %d).",
                                    pi, " ".join(shown), clip.name, CAPTION_GUARD_GRAPHICS_BOTTOM)
            raw_events.append((0, show, hide, "CapBox",
                               f"{{\\pos({box_x},{g['box_y']}){fad}{guard}\\p1}}{path}{{\\p0}}"))
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
                def _wide_word(ji: int, w: str) -> str:
                    if ji not in gold_words:
                        return f"{{\\c{COL_PAST}}}{_ass_escape(w)}"
                    lead, core, tail = gold_parts(_ass_escape(w))   # C11/K1: its punctuation and quotes are bone
                    return ((f"{{\\c{COL_PAST}}}{lead}" if lead else "") + f"{{\\c{COL_EMPHASIS}}}{core}"
                            + (f"{{\\c{COL_PAST}}}{tail}" if tail else ""))
                line = " ".join(_wide_word(ji, w) for ji, w in enumerate(shown))
                raw_events.append((1, show, hide, "CapText", f"{pos}{g['size_tag']}{line}"))
                continue

            # Layer 1, 9:16: one event per spoken word; the words still to come are transparent placeholders. A
            # two-line burst has one event a line (N1), the reveal running on from line 1 into line 2.
            two = g["two"]
            spans = ([(0, two["k"], two["y"][0], two["dx"][0]), (two["k"], len(shown), two["y"][1], two["dx"][1])]
                     if two else [(0, len(shown), g["center_y"], None)])
            for wi in range(len(phrase)):
                if whole and wi < len(phrase) - 1:
                    continue   # shown whole: only the full line's event
                t_start = reveals[wi]
                t_end = reveals[wi + 1] if wi < len(phrase) - 1 else hide
                for lo, hi, line_y, line_dx in spans:
                    line_pos = pos if line_dx is None else f"{{\\pos({round(center_x - line_dx)},{line_y})\\q2}}"
                    visible, hidden = [], []
                    for ji in range(lo, hi):
                        disp_word = shown[ji]
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
                    if hidden:   # a line not yet spoken is all placeholder (it holds its place, N1)
                        line += (" " if visible else "") + CAPTION_HIDDEN + " ".join(hidden)
                    out_fad = f"{{\\fad(0,{fade_out})}}" if fade_out and wi == len(phrase) - 1 else ""
                    raw_events.append((1, t_start, t_end, "CapText", f"{line_pos}{out_fad}{g['size_tag']}{line}"))

        record = caption_contrast_record(contrast_rows)
        record_path = ass_path.with_name(ass_path.stem + CAPTION_CONTRAST_SIDECAR)
        record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
        low = [e for e in record["elements"] if e["drawn"] < _brand.TEXT_CONTRAST]
        if low:
            raise RenderError(f"Caption contrast under {_brand.TEXT_CONTRAST}:1 on the plate: {low[:3]}")

        # The AI-reconstruction disclosure (layer 2 plate, layer 3 text) while a reconstruction is on screen
        if reconstruction_intervals:
            label = RECON_BADGE_LABEL
            # Bone IBM Plex Mono on an INK plate, top-left: the 5% title-safe corner on 16:9; on 9:16 inside the Reel
            # box below the Reel's top bar, 19 px in from its left edge and 12 px under its top (x 132, y 300 on the
            # organic box; x 84 on the ads box until QF-24). The 9:16 badge was 22px, centred and unplated until the
            # caption review of 2026-11-02 (operator approved moving it).
            scale = height / 1080 if is_widescreen else 1
            pad_x, pad_y = round(14 * scale), round(8 * scale)
            b_top, b_bottom = _ink_band(RECON_FONT, badge_px, badge_fs, 600)
            tw = unkerned_length(get_font(RECON_FONT, badge_px, weight=600), label) + 1.5 * len(label)
            if is_widescreen:
                x0, y0 = round(0.05 * width), round(0.05 * height)
            else:
                reel_x0, reel_y0, _, _ = _REEL_UI.box(width, height)
                x0, y0 = reel_x0 + round(19 * width / VIDEO_WIDTH), reel_y0 + round(12 * height / VIDEO_HEIGHT)
            text_pos = (x0 + pad_x, round(y0 + pad_y - b_top))
            badge_text = f"{{\\an7\\pos({text_pos[0]},{text_pos[1]})}}{label}"
            plate_w, plate_h = round(tw + 2 * pad_x), round(b_bottom - b_top + 2 * pad_y)
            plate_text = f"{{\\pos({x0},{y0})\\p1}}{rounded_rect_path(plate_w, plate_h, 8)}{{\\p0}}"
            for start_t, end_t in reconstruction_intervals:
                raw_events.append((2, start_t, end_t, "ReconPlate", plate_text))
                raw_events.append((3, start_t, end_t, "ReconBadge", badge_text))

        # Sort all events chronologically (layer 0 background pill always appears under layer 1 text)
        raw_events.sort(key=lambda e: (e[1], e[0]))

        if embedded_font:
            header = header.replace("[Events]", ass_embedded_font(embedded_font) + "\n[Events]")
        lines = [header]
        for layer, ts, te, style, txt in raw_events:
            ts, te = ts + timeline_offset_s, te + timeline_offset_s
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

    @staticmethod
    def _analyse(vo_path: Path, bed_path: Path, chain: str, card_at: float | None = None) -> dict:
        """One decode of the mixed programme through `chain`: its integrated loudness (LUFS), loudness range (LU) and
        true peak (dBTP) by ITU-R BS.1770, and every 100 ms frame's (time, momentary loudness, true peak). With
        `card_at`, the same decode also measures the body before it and the end card after it."""
        meter = "ebur128@whole=peak=true:framelog=info"
        if card_at is None:
            graph = f"{chain},{meter}[out]"
        else:
            graph = (f"{chain},asplit=3[a_whole][a_body][a_card];[a_whole]{meter}[out];"
                     f"[a_body]atrim=end={card_at:.3f},ebur128@body,anullsink;"
                     f"[a_card]atrim=start={card_at:.3f},asetpts=PTS-STARTPTS,ebur128@card,anullsink")
        res = _run_ffmpeg(["ffmpeg", "-hide_banner", "-nostats", "-i", str(vo_path), "-i", str(bed_path),
                           "-filter_complex", graph, "-map", "[out]", "-f", "null", "-"])

        def summary(name: str) -> dict:
            found = re.search(rf"\[ebur128@{name} @ [^\]]+\] Summary:", res.stderr)
            block = res.stderr[found.end():] if found else ""
            block = block[:block.find("\n[")] if "\n[" in block else block
            out = {}
            for key, pattern in (("integrated_lufs", r"I:\s+(-?[\d.]+) LUFS"), ("loudness_range_lu", r"LRA:\s+([\d.]+) LU"),
                                 ("true_peak_dbtp", r"Peak:\s+(-?[\d.]+) dBFS")):
                match = re.search(pattern, block)
                if match:
                    out[key] = float(match.group(1))
            if "integrated_lufs" not in out:
                raise RenderError("Could not measure program loudness (silent audio?).")
            return out

        frames = []
        for t, m, a, b in re.findall(r"\[ebur128@whole @ [^\]]+\] t: ([\d.]+)\s+TARGET:\S+ LUFS\s+M:\s*(-?[\d.]+|-inf)"
                                     r"\s+S:.*?FTPK:\s*(-?[\d.]+|-inf)\s+(-?[\d.]+|-inf)", res.stderr):
            level = float(m) if m != "-inf" and float(m) > -120 else None
            peaks = [float(v) for v in (a, b) if v != "-inf"]
            frames.append((float(t), level, max(peaks) if peaks else None))
        if not frames:
            raise RenderError("Could not measure program loudness (no meter frames).")
        found = {"passes": 1, **summary("whole"), "frames": frames}
        if card_at is not None:
            found["body_lufs"] = summary("body")["integrated_lufs"]
            found["card_lufs"] = summary("card")["integrated_lufs"]
        return found

    def calibrate_gain(self, vo_path: Path, bed_path: Path, tolerance: float = MASTER_TOLERANCE_LU, accept: float = 1.0,
                       max_iter: int = 6, target_lufs: float = TARGET_LUFS,
                       target_true_peak: float = TARGET_TRUE_PEAK, pad: float = 0.0,
                       card_at: Optional[float] = None, card_db: float = 0.0,
                       end: float | None = None) -> float:
        """
        The gain that lands the limited mix on target_lufs (within `tolerance` LU, the usual delivery tolerance):
        one measurement of the unlimited mix, then the static gain target - measured (QF-11). The limited mix is
        measured again only when the limiter could cost more than the tolerance (_limiter_cost_bound), and then
        corrected as before; if it never gets that close, the best result within `accept` LU is used with a
        warning. What was measured is kept in `last_mastering` for the composition record.
        """
        from .credits import sha256_file
        # Same voice + bed + targets always give the same gain (each pass decodes the full mix), so a re-render
        # (QA re-plan, deep-dive) reuses it instead of re-measuring.
        key = (sha256_file(vo_path), sha256_file(bed_path), tolerance, accept, max_iter, target_lufs, target_true_peak, round(pad, 3),
               card_at, round(card_db, 2), end)
        cache = self.__dict__.setdefault("_gain_cache", {})
        records = self.__dict__.setdefault("_mastering_records", {})
        if all(key[:2]) and key in cache:
            log.info("Mastering: reusing calibrated gain %+.2f dB for unchanged audio", cache[key])
            self.last_mastering = {**records.get(key, {}), "reused": True, "analysis_passes": 0}
            return cache[key]
        self.__dict__.pop("_last_calibration", None)
        gain = self._calibrate_gain(vo_path, bed_path, tolerance, accept, max_iter, target_lufs, target_true_peak, pad,
                                    card_at, card_db, end)
        self.last_mastering = dict(self.__dict__.get("_last_calibration") or {})
        if all(key[:2]):
            cache[key] = gain
            records[key] = self.last_mastering
        return gain

    def end_card_level(self, vo_path: Path, bed_path: Path, card_at: float, pad: float, gain: float) -> Dict[str, float]:
        """The end card's loudness against the narrated body in the mastered programme, and the correction (dB)
        that sets it END_CARD_BELOW_BODY_LU under the body: the bed's music level differs from run to run, so the
        card's level is measured, not assumed (6.3 to 8.7 LU under the body on three masters, 2026-10-25). One
        decode measures both (QF-11; it was one each)."""
        chain = _audio_chain(0, 1, pad, end=card_at + pad if pad > 0 else None) + _master_chain(gain)
        found = self._analyse(vo_path, bed_path, chain, card_at=card_at)
        body, card = found["body_lufs"], found["card_lufs"]
        correction = max(-12.0, min(12.0, (body - END_CARD_BELOW_BODY_LU) - card))
        log.info("End card: %.1f LUFS against the body's %.1f; set %+.1f dB", card, body, correction)
        return {"body_lufs": round(body, 1), "card_lufs": round(card, 1), "correction_db": round(correction, 1),
                "passes": 1}

    def _calibrate_gain(self, vo_path: Path, bed_path: Path, tolerance: float, accept: float,
                        max_iter: int, target_lufs: float, target_true_peak: float, pad: float = 0.0,
                        card_at: Optional[float] = None, card_db: float = 0.0,
                        end: float | None = None) -> float:
        # Measure the same sample-bounded programme as delivery. A finite sidechain
        # can forward either input's EOF first under load, changing the measured gain.
        base = _audio_chain(0, 1, pad, card_at, card_db, end=end)
        from .encoded_audio import LOWPASS_HZ
        # The unlimited mix in the master chain's own band (its low-pass comes before the limiter).
        first = self._analyse(vo_path, bed_path, base + f",lowpass=f={LOWPASS_HZ}:poles=2")
        integrated = first["integrated_lufs"]
        gain = round(target_lufs - integrated, 2)
        bound = _limiter_cost_bound(first["frames"], gain, integrated - 10.0)
        record = {"method": "measured static gain ahead of the true-peak limiter (two-pass)",
                  "first_pass": {k: first.get(k) for k in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp")},
                  "gain_db": gain, "limiter_cost_bound_lu": round(bound, 2), "tolerance_lu": tolerance,
                  "verified": [], "analysis_passes": 1}
        self.__dict__["_last_calibration"] = record
        log.info("Mastering: %.1f LUFS unlimited -> gain %+.2f dB (limiter costs at most %.2f LU)",
                 integrated, gain, bound)
        if bound <= tolerance + 1e-9:
            return gain
        best_gain, best_err = gain, float("inf")
        for _ in range(max_iter):
            integrated, peak = self._measure(vo_path, bed_path, base + _master_chain(gain))
            record["verified"].append({"gain_db": gain, "integrated_lufs": integrated, "true_peak_dbtp": peak})
            record["analysis_passes"] += 1
            log.info("Mastering: gain %+.2f dB -> %.1f LUFS, %.1f dBTP", gain, integrated, peak)
            if peak > target_true_peak:
                raise RenderError(f"True peak {peak} dBTP exceeds {target_true_peak} after limiting.")
            err = abs(integrated - target_lufs)
            if err < best_err:
                best_gain, best_err = gain, err
            if err <= tolerance + 1e-9:
                record["gain_db"] = gain
                return gain
            gain += (target_lufs - integrated) * 1.5
        if best_err <= accept:
            log.warning("Loudness within %.1f LU of %.1f LUFS (not %.1f); using best gain.", best_err, target_lufs, tolerance)
            record["gain_db"] = best_gain
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
        act_cuts: list[float] | None = None,
        opening_card: Path | None = None,
        caption_offset_s: float = 0.0,
    ) -> Path:
        """
        end_card: non-empty ends a Reel on the loop bridge after the narration (src/end_card.py loop_bridge,
        operator decision 2026-11-03: in place of the full end card, which stays the 16:9 YouTube end screen); the
        value is the share line, drawn only when end_card.LOOP_LINE is on (off: the bridge has no line). The bed must
        run on for end_card.LOOP_SECONDS more (generate_ambient_bed(end_card_at=vo_dur)). Empty, or 16:9: no tail.
        grade_bias: a bounded warm (+) / cool (-) shift measured from the topic's hero photograph
        applied only to photographic stills before compositing; zero gives the neutral photo curve.
        durations: seconds on screen per scene (e.g. from scene_planner beats). Hard cuts land
        on these boundaries, rounded to the nearest video frame. Only boundaries explicitly
        listed in act_cuts get an eight-frame dissolve. Without durations, scenes share the narration equally.
        Supports both 9:16 vertical (Reels) and 16:9 widescreen (Documentaries) with aspect-aware photo compositing.
        """
        from .delivery_profiles import get_delivery_profile
        profile = delivery_profile or get_delivery_profile("deep_dive" if width > height else "reel")
        width, height = profile.width, profile.height
        from .checkpoints import file_inputs, fingerprint
        from .credits import sha256_file
        from .font_assets import DERIVED_FAMILIES, WIDE_FAMILY, derived_font
        from .typography import bundled_font_files, font_file
        extra_sources = [opening_card] if opening_card else []
        source_inputs = file_inputs([*scene_images, vo_path, bed_path, *extra_sources])
        caption_family = WIDE_FAMILY if width > height else _caption_family()
        try:
            derived_caption = (derived_font(caption_family, self.fonts_dir) if caption_family in DERIVED_FAMILIES
                               else self.fonts_dir / font_file(caption_family))
        except (OSError, ValueError) as exc:
            raise RenderError("Captions would not draw in the bundled font: " + str(exc)) from exc
        # every bundled face (the fallback faces a still may set a field in, QF-94, among them) and the caption
        # face libass draws (QF-87: derived from a bundled source) are sources, with their licence evidence
        font_inputs = file_inputs([*(self.fonts_dir / f for f in bundled_font_files() if (self.fonts_dir / f).is_file()),
                                   derived_caption])
        if not all(source_inputs.values()) or not all(font_inputs.values()):
            raise RenderError("Render sources and bundled fonts must exist")

        n = len(scene_images)
        if not durations or len(durations) != n:
            durations = [vo_dur / n] * n

        from . import card_composition
        design_card_inputs = card_composition.cache_inputs([*scene_images, *extra_sources])
        if opening_card:
            opening = card_composition.load_contract(opening_card)
            if (width >= height or opening["kind"] != "hook"
                    or not opening["caption_clear_until_s"] <= caption_offset_s <= opening["seconds"]
                    or any(float(w["start"]) < caption_offset_s for w in words)):
                raise RenderError("Opening hook is not aligned with its complete voice/caption schedule")
        elif caption_offset_s:
            raise RenderError("Caption offset requires a verified opening card")
        caption_durations = list(durations)
        caption_durations[0] -= caption_offset_s
        if caption_durations[0] <= 0:
            raise RenderError("Opening hook cannot replace the first picture interval")
        caption_words = [{**w, "start": float(w["start"]) - caption_offset_s} for w in words]
        card_clock = 0.0
        card_intervals = []
        for item, duration in zip(scene_images, caption_durations):
            if card_composition.is_card(item):
                contract = card_composition.load_contract(item)
                if (contract["width"], contract["height"]) != (width, height):
                    raise RenderError("Design card requires its authored native aspect and dimensions")
                if duration + 1e-6 < contract["seconds"]:
                    raise RenderError("Design card would be cut before its reading interval")
                if contract["kind"] == "hook":
                    raise RenderError("Hook requires an explicit caption-clear opening schedule")
                beat = {"start": card_clock, "end": card_clock + duration,
                        "text": " ".join(w["word"] for w in caption_words
                                         if card_clock <= w["start"] < card_clock + duration)}
                if not card_composition.matches(item, beat, caption_words):
                    raise RenderError("Design card does not match its complete authored interval and narration")
                card_intervals.append({"asset": str(item), "kind": contract["kind"],
                                       "start": card_clock + caption_offset_s,
                                       "end": card_clock + duration + caption_offset_s,
                                       "minimum_read_s": contract["seconds"]})
            card_clock += duration

        # Historical reconstruction badge intervals (crossfades included: reconstruction_badge_intervals)
        recon_intervals = reconstruction_badge_intervals(scene_images, caption_durations, vo_dur - caption_offset_s)

        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(caption_words, ass_path, emphasis=emphasis, width=width, height=height,
                             reconstruction_intervals=recon_intervals,
                             graphic_intervals=caption_graphic_intervals(scene_images, caption_durations),
                             first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S if height > width else None,
                             cuts=caption_cut_times(caption_durations) if height > width else None,
                             graphic_clips=caption_graphic_clips(scene_images, caption_durations),
                             timeline_offset_s=caption_offset_s)
        caption_overlap = list(getattr(self, "last_caption_graphic_overlap", []) or [])
        # The caption plates are measured in the bundled font; burn them in only if libass draws that font
        # (round 3: with only Montserrat-Variable.ttf bundled, the 16:9 line drew in Inter and ran off its plate).
        caption_font = (caption_family, 700 if width > height else 400)
        font_problems = libass_font_problems(self.fonts_dir, [caption_font], [derived_caption])
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

        # A cut consumes no overlap. Only an actual act boundary gets a dissolve;
        # each adjacent clip supplies half its overlap so later boundaries never drift.
        boundaries = [round(sum(durations[:k + 1]) * VIDEO_FPS) / VIDEO_FPS for k in range(n - 1)]
        fades = [8 / VIDEO_FPS if any(abs(b - a) <= 0.5 / VIDEO_FPS + 1e-6 for a in (act_cuts or []))
                 else 0.0 for b in boundaries]
        starts = [0.0, *[b - fade / 2 for b, fade in zip(boundaries, fades)]]
        ends = [*[b + fade / 2 for b, fade in zip(boundaries, fades)],
                math.ceil(vo_dur * VIDEO_FPS) / VIDEO_FPS]
        clip_durs = [end - start for start, end in zip(starts, ends)]
        if any(d <= 0 for d in clip_durs):
            raise RenderError("Scene boundaries must increase and fit inside the narration")
        macro_indices = set(macro_drifts or [])

        photo_grade = film_grade_filter(grade_bias)
        input_args: List[str] = []
        filter_parts: List[str] = []
        # QF-22: stills and the AI hook clip move through the shared camera (src/still_camera.py), rendered to
        # lossless clips just before the graph runs (cameras) and removed after it.
        import uuid

        from .still_camera import sweep_stale
        sweep_stale(output_mp4_path.parent)          # what a killed render left behind
        camera_dir = output_mp4_path.parent / f"{output_mp4_path.stem}.camera-{uuid.uuid4().hex[:12]}"
        cameras: list[tuple[int, Path, Path, float, dict[str, Any]]] = []
        for i, item in enumerate(scene_images):
            item = Path(item)
            clip_dur = clip_durs[i]
            if is_video(item):
                # Each clip is read with the matrix it was made with and enters the graph as BT.709 (QF-01)
                colorspace = video_colorspace(item)
                if i == 0 and width < height and is_ai_motion_clip(item):
                    # the AI hook clip gets the opening still's push, from its first frame (lead)
                    clip = camera_dir / f"{i}.mkv"
                    # QF-86: a hook clip animated from a photo takes the photo curve in the camera's RGB, before
                    # its BT.709 conversion, as the stills do; an illustration's or a manual clip's keeps its colour
                    cameras.append((i, item, clip, clip_dur, {"preset": "hook", "lead": True,
                                                              "colorspace": colorspace,
                                                              "grade": photo_grade if is_photo_clip(item) else None}))
                    input_args += ["-t", f"{clip_dur:.3f}", "-i", str(clip)]
                    filter_parts.append(f"[{i}:v]fps={VIDEO_FPS},setsar=1,{BT709_TAGS}[v{i}]")
                    continue
                input_args += ["-t", f"{clip_dur:.3f}", "-i", str(item)]
                # A parallax clip, or an AI motion clip animated from a photo, is a photograph in motion: it takes
                # the same 16-bit photo curve as a still, so a photo's grade does not depend on whether the depth
                # model ran. Graphic, illustration and footage clips keep their supplied colour (QF-86).
                clip_in = (f"scale=in_color_matrix={source_matrix(colorspace)},format=gbrp16le,{photo_grade},"
                           f"{RGB_TO_BT709}" if is_photo_clip(item) else to_bt709(colorspace))
                if width > height and self._is_portrait_video(item):
                    fg_h = int(round(height * 0.94)) // 2 * 2
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,split[bg_in{i}][fg_in{i}];"
                        f"[bg_in{i}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=25:5,eq=brightness=-0.15[bg{i}];"
                        f"[fg_in{i}]scale=-2:{fg_h}[fg{i}];"
                        f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps={VIDEO_FPS},setsar=1,{clip_in}[v{i}]"
                    )
                else:
                    filter_parts.append(
                        f"[{i}:v]tpad=stop_mode=clone:stop=-1,scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps={VIDEO_FPS},setsar=1,{clip_in}[v{i}]"
                    )
                continue

            from .image_presentation import fit_filter, full_image
            from .scene_planner import is_illustration
            # Keep authored graphics and generated illustrations out of the photo
            # curve, including derivatives whose lineage records their origin; a
            # broken or stale chain fails closed, as for clips (is_photo_still).
            still_grade = "null" if not is_photo_still(item) or is_illustration(item) else photo_grade
            if full_image(item):
                input_args += ["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", str(item)]
                filter_parts.append(
                    f"[{i}:v]{fit_filter(item, width, height)},fps={VIDEO_FPS},"
                    f"setsar=1,format=gbrp16le,{still_grade},{RGB_TO_BT709}[v{i}]"
                )
                continue
            from .still_camera import preset_for
            tilt = None
            if width > height:
                try:
                    with Image.open(item) as im:
                        if im.width / im.height < 1.3:
                            # Cinematic vertical reveal: the tall picture at full width, eased over its height
                            tilt = "up" if i % 2 else "down"
                except Exception:  # an unreadable size takes the ordinary move, as before
                    pass
            clip = camera_dir / f"{i}.mkv"
            cameras.append((i, item, clip, clip_dur, {
                "preset": preset_for(i, n, i in macro_indices), "focal": self.compute_focal_point(item),
                "tilt": tilt, "grade": None if still_grade == "null" else still_grade}))
            input_args += ["-t", f"{clip_dur:.3f}", "-i", str(clip)]
            filter_parts.append(f"[{i}:v]fps={VIDEO_FPS},setsar=1,{BT709_TAGS}[v{i}]")

        # Explicit trim/setpts also makes concat safe for footage with nonzero timestamps.
        for i, clip_dur in enumerate(clip_durs):
            # The hook is an added prefix. Start the original first moving
            # picture with its narration, instead of consuming its early frames
            # invisibly under the hook and freezing its ending for longer.
            prefix = (f"tpad=start_mode=clone:start_duration={caption_offset_s:.9f},"
                      if i == 0 and opening_card else "")
            filter_parts.append(f"[v{i}]{prefix}trim=duration={clip_dur:.9f},setpts=PTS-STARTPTS,settb=AVTB[c{i}]")
        # With an opening hook the scene timeline is overlaid by it first; the overlay is then [video_raw].
        timeline = "video_scenes" if opening_card else "video_raw"
        curr = "[c0]"
        for k in range(n - 1):
            out = f"[x{k}]" if k < n - 2 else f"[{timeline}]"
            if fades[k]:
                off = boundaries[k] - fades[k] / 2
                filter_parts.append(f"{curr}[c{k + 1}]xfade=transition=dissolve:duration={fades[k]:.9f}:offset={off:.9f}{out}")
            else:
                filter_parts.append(f"{curr}[c{k + 1}]concat=n=2:v=1:a=0,fps={VIDEO_FPS},settb=AVTB{out}")
            curr = out
        if n == 1:
            filter_parts.append(f"[c0]null[{timeline}]")

        input_args += ["-i", str(vo_path), "-i", str(bed_path)]
        if opening_card:
            input_args += ["-i", str(opening_card)]
            filter_parts.append(f"[{n + 2}:v]trim=duration={caption_offset_s:.9f},setpts=PTS-STARTPTS,"
                                f"scale={width}:{height},setsar=1[{n + 2}hook]")
            filter_parts.append(f"[{timeline}][{n + 2}hook]overlay=eof_action=pass:repeatlast=0:"
                                f"enable='lt(t,{caption_offset_s:.9f})'[video_raw]")
        if card:  # the bridge's sound is the bed running on: a bed made for the narration alone would cut out
            import soundfile as sf
            bed_seconds = sf.info(str(bed_path)).duration
            if bed_seconds < vo_dur + card_seconds - 0.02:
                raise RenderError(f"The ambient bed runs {bed_seconds:.2f}s; the loop bridge needs "
                                  f"{vo_dur + card_seconds:.2f}s (make it with end_card_at).")
        # The whole programme is mastered together: narration, and the card after it with the bed alone.
        gain = self.calibrate_gain(vo_path, bed_path, target_lufs=profile.target_lufs,
                                   target_true_peak=profile.target_true_peak, pad=card_seconds,
                                   end=vo_dur + card_seconds)
        mastering = {"passes": [self.last_mastering]}
        card_at, card_db, card_level = (vo_dur if card else None), 0.0, None
        if card:  # the card's own level, measured in the mastered programme, then the whole mastered again
            card_level = self.end_card_level(vo_path, bed_path, vo_dur, card_seconds, gain)
            card_db = card_level["correction_db"] if abs(card_level["correction_db"]) >= 0.5 else 0.0
            if card_db:
                gain = self.calibrate_gain(vo_path, bed_path, target_lufs=profile.target_lufs,
                                           target_true_peak=profile.target_true_peak, pad=card_seconds,
                                           card_at=card_at, card_db=card_db, end=vo_dur + card_seconds)
                mastering["passes"].append(self.last_mastering)
        # QF-11: what the mastering measured, and how many decodes of the mix it took before the render
        mastering.update(gain_db=gain, card_level=card_level,
                         analysis_passes=sum((p or {}).get("analysis_passes", 0) for p in mastering["passes"])
                         + (card_level or {}).get("passes", 0))
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
                                         end=vo_dur + card_seconds)
                            + _master_chain(gain) + seam_fade + "[a_final]")

        ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts_str = str(self.fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")
        # Graphics are drawn to their own measured contrast: the lens vignette is lifted while one is on screen,
        # so a graphic can use the frame's full width (2026-11-02; at the edges it read 2.7:1 after grading).
        vignette_enable = graphic_vignette_enable(scene_images, durations)
        # Photo curves ran before compositing, so graphics and footage retain their
        # own colour even during an act dissolve. Only lens/captions remain here.
        film_grade = "null"
        # The graded picture before the captions, split off for the end card's background (its last frame).
        card_split = "split[v_graded][v_card_src];[v_graded]" if card else ""
        # QF-02: from here `film_grade` is the whole picture chain in 16-bit RGB (picture_chain): the grade, the
        # lens vignette (picture_vignette, freeze pass V1: ffmpeg reads its aspect as the pixel aspect,
        # so the 16:9 master draws at aspect 1), the card split and the captions, then one dithered conversion to
        # BT.709 yuv420p. The yuv420p guards around it stay (the planar-RGB right-edge bar is measured in QF-02).
        film_grade = picture_chain(film_grade, width, height, vignette_enable=vignette_enable, card_split=card_split,
                                   captions=f"subtitles='{ass_str}':fontsdir='{fonts_str}'")
        filter_parts.append(f"[video_raw]format=yuv420p,{film_grade},format=yuv420p,{to_bt709('bt709')}[v_final]")
        if card:   # the end card's background frame: straight from the 16-bit RGB picture to 8-bit RGB
            filter_parts.append(f"[v_card_src]trim=start={max(0.0, vo_dur - 0.2):.3f}:end={vo_dur:.3f},"
                                "format=rgb24[v_last]")

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
        try:
            motions = render_cameras(cameras, (width, height))
            if not card:
                _run_ffmpeg([*head, "-map", "[v_final]", "-map", "[a_final]", *delivery_args,
                             "-t", f"{vo_dur:.2f}", str(output_mp4_path)])
            else:
                motion_durations = [b - a for a, b in zip([0.0, *boundaries], [*boundaries, vo_dur])]
                motion = loop_motion(scene_images, motion_durations, vo_dur, macro_indices, width, height,
                                     closing_transition=fades[-1] if fades else 0.0,
                                     opening_transition=fades[0] if fades else 0.0)
                motion = {**motion, **seam_from_cameras(motions, n)}   # what the cameras really did
                from .font_engine import surface
                with surface("end card"):
                    self.last_end_card = self._render_with_loop_bridge(head, delivery_args, end_card, vo_dur,
                                                                       output_mp4_path, motion,
                                                                       target_lufs=profile.target_lufs)
                self.last_end_card["level"] = card_level
        finally:
            shutil.rmtree(camera_dir, ignore_errors=True)
        from .encoded_audio import finalize_encoded_audio
        encoded_audio = finalize_encoded_audio(output_mp4_path, profile.target_lufs, profile.target_true_peak)
        if source_inputs != file_inputs([*scene_images, vo_path, bed_path, *extra_sources]):
            raise RenderError("Source files changed while rendering")
        self.export_subtitles_srt(words, output_mp4_path.with_suffix(".srt"))
        self.export_subtitles_vtt(words, output_mp4_path.with_suffix(".vtt"))
        import json
        manifest = {"master_sha256": sha256_file(output_mp4_path),
                    "audio_mastering": mastering, "encoded_audio": encoded_audio,
                    "scenes": file_inputs([*scene_images, *extra_sources]), "voiceover": file_inputs([vo_path]),
                    "ambient_bed": file_inputs([bed_path]), "fonts": font_inputs,
                    "captions": file_inputs([ass_path, output_mp4_path.with_suffix(".srt"), output_mp4_path.with_suffix(".vtt")]), "words_fingerprint": fingerprint(words),
                    "width": width, "height": height, "renderer_sha256": sha256_file(Path(__file__)),
                    "end_card": self.last_end_card, "design_cards": design_card_inputs,
                    "design_card_intervals": card_intervals,
                    "opening_offset_s": caption_offset_s,
                    "opening_card": ({"asset": str(opening_card), "start": 0.0, "end": caption_offset_s,
                                      "voice_start": caption_offset_s} if opening_card else None)}
        # QF-10: where an essential image is shown whole, its fit_filter padding is by design; the release gate's
        # bar check reads these spans (always written, so a master without the key was made before QF-10).
        from .image_presentation import full_image_spans
        bridge = None
        if self.last_end_card and self.last_end_card.get("duration_s"):
            bridge = (self.last_end_card["start_s"], self.last_end_card["start_s"] + self.last_end_card["duration_s"])
        manifest["full_image_spans"] = full_image_spans(scene_images, starts, ends, bridge)
        if caption_overlap:   # freeze pass G1: the bursts drawn opaque over a graphic's text
            manifest["caption_graphic_overlap"] = caption_overlap
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
        from . import config
        from . import end_card as card_mod
        stem = output_mp4_path.parent / output_mp4_path.stem
        body, audio = Path(f"{stem}_body.mkv"), Path(f"{stem}_programme.wav")
        last, first = Path(f"{stem}_card_bg.png"), Path(f"{stem}_frame0.png")
        total = vo_dur + card_mod.LOOP_SECONDS
        inter_encoder = _get_intermediate_video_encoder()
        kw: Dict[str, Any] = {"line": card_mod.LOOP_LINE, "follow": bool(config.LOOP_FOLLOW_OVERLAY), **(motion or {})}
        fallback: Optional[List[str]] = None
        follow_dropped: str | None = None
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
                # QF-83: then, with a FOLLOW, the bridge without it (with its push, then still) before no bridge.
                still = {**kw, "close_push": 0.0, "open_push": 0.0, "crossfade": card_mod.LOOP_CROSSFADE[1]}
                ladder = [("push", still)] + ([("FOLLOW", {**kw, "follow": False}),
                                               ("push and FOLLOW", {**still, "follow": False})] if kw.get("follow") else [])
                for label, option in ladder:
                    retry = card_mod.check_loop_bridge(background, frame0, share, **option)
                    if retry["ok"]:
                        log.warning("Loop bridge without its %s: %s", label, "; ".join(gate["problems"]))
                        if kw.get("follow") and not option.get("follow"):
                            follow_dropped = "; ".join(gate["problems"])
                        fallback, kw, gate = gate["problems"], option, retry
                        break
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
                   "-filter_complex", f"[0:v]setsar=1[b];[1:v]{RGB_TO_BT709},setsar=1[c];"
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
        if follow_dropped:   # QF-83: asked for, gated out; the review card says so (end_card.end_card_note)
            out["follow_skipped"] = f"the bridge with it would not pass its gate ({follow_dropped})"
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
        act_cuts: list[float] | None = None,
    ) -> Path:
        """Compose a 16:9 widescreen broadcast documentary with blurred-backdrop compositing."""
        return self.compose_video(
            scene_images=scene_images,
            vo_path=vo_path,
            bed_path=bed_path,
            words=words,
            vo_dur=vo_dur,
            output_mp4_path=output_mp4_path,
            durations=durations,
            emphasis=emphasis,
            act_cuts=act_cuts,
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
            "-pix_fmt", "yuv420p", *BT709_FLAGS,
            "-r", str(VIDEO_FPS),
            "-c:a", "copy",
            "-movflags", "+faststart",
        ]

        replacement_asset = Path(replacement_asset)
        input_args = ["-i", str(master_mp4)]
        filter_parts = []
        cameras: list[tuple[Path, Path, float]] = []

        if is_video(replacement_asset):
            input_args += ["-t", f"{beat_dur:.3f}", "-i", str(replacement_asset)]
            filter_parts.append(
                f"[1:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={VIDEO_FPS},setsar=1,{to_bt709(video_colorspace(replacement_asset))}[v_mid]"
            )
        else:
            # QF-22: the micro-patch's push through the shared camera (still_camera "splice", centred as before)
            camera = output_mp4_path.parent / f"{output_mp4_path.stem}.camera-{beat_index}.mkv"
            cameras.append((camera, replacement_asset, beat_dur))
            input_args += ["-t", f"{beat_dur:.3f}", "-i", str(camera)]
            filter_parts.append(f"[1:v]fps={VIDEO_FPS},setsar=1,{BT709_TAGS}[v_mid]")

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
        try:
            from .still_camera import ensure_disk, frame_count, render_clip, sweep_stale
            sweep_stale(output_mp4_path.parent)
            for camera, source, seconds in cameras:
                try:
                    ensure_disk(camera.parent, frame_count(seconds, VIDEO_FPS), (width, height))
                except RuntimeError as exc:
                    raise RenderError(str(exc)) from exc
                render_clip(source, camera, seconds, (width, height), VIDEO_FPS, preset="splice")
            _run_ffmpeg(cmd)
        finally:
            for camera, _, _ in cameras:
                camera.unlink(missing_ok=True)
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
