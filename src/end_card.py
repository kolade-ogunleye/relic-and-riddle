"""
src/end_card.py
===============
The 9:16 Reel ends on the loop bridge (loop_bridge, below; operator decision 2026-11-03): LOOP_SECONDS (1 s) after
the narration, with no share line (LOOP_LINE), resolving into the Reel's frame 0. The full card (frames) is the
16:9 YouTube end screen, and stays callable at 9:16 for the review renders; until 2026-11-03 it was the Reel's
ending too, as described below.

The Reel's closing card, appended after the narration. Plain type, no capsule ("plain_v4", operator decision
2026-10-29: the approved capsule v3 read as a button, visual critic round 4): the channel name, a gold rule drawn
out from a small gold diamond (the brand's mark, kept at the operator's request), the share ask in bone on two lines where it fits at 54 px or more, then FOLLOW FOR THE NEXT RELIC as one line of
tracked gold capitals. No outline, no dark ground behind it, no sheen.

The last shot keeps pushing in while it is dimmed and, when busy, softened, by the least that makes the card
read on that frame (measured: the text band at most 30/255 with little texture left, the rows above and below
at most 40/255). The channel name fades in, the gold rule draws out from the centre, the share line rises word by
word, then the follow line rises into place as the bell lands (BELL_AT). No handle: it differs between platforms.
Everything stays inside the Reel safe area's centred width (src/safe_zones.py), clear of the like/comment/share
rail, and every line's contrast is measured on the card's last frame (stats["contrast"]).

Its sound is part of the ambient bed (src/audio_engine.py), so it is mastered with the rest of the programme
and its recordings are in the bed's rights evidence: the music carries on under the card, a soft whoosh opens
it and the bell lands with the follow line at BELL_AT.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import brand
from .brand_system import colour_of, ease_out, tok
from .camera_motion import kenburns
from .safe_zones import REEL
from .typography import cap_height, get_font, tracked_width

log = logging.getLogger(__name__)

VARIANT = "plain_v4"     # capsule_v3 until 2026-10-29
SECONDS = 2.9            # the 9:16 card's shortest length after the narration; it holds longer until its lines read
FPS = 30                 # at the reading pace (card_seconds, freeze lane E cycle 1)
WIDTH, HEIGHT = 1080, 1920
WHOOSH_AT, BELL_AT = 0.0, 1.95   # seconds into the card
# The card's reveal, seconds into it (one timeline for frames() and its gates, _gate_lines). Since SYS batch 2
# (SYS-E-1) the reveal's durations and rises are the system's motion.* tokens, read when drawn (never at import):
# MAST_IN (motion.reveal), WORD_STAGGER (motion.word_stagger), WORD_IN (motion.word_in), SHARE_RISE
# (motion.rise_l), FOLLOW_IN (motion.line_in), FOLLOW_RISE and LOOP_FOLLOW_RISE (motion.rise_m), LOOP_CROSSFADE
# (motion.loop_crossfade, motion.loop_crossfade_visual), LOOP_PUSH (motion.push_per_frame), LOOP_PUSH_CAP
# (motion.push_cap): the names are served by __getattr__ below for their readers. The rest are this card's own.
DIM_IN = 0.45            # the dim comes in (9:16: ease_out over this; 16:9: ease_io, a field between two rests)
SOFTEN_IN = 0.6          # the softening comes in (on the dim's curve)
# Freeze cycle 5 (stream PLATES, panel 6 MO r1 / r2: on the brightest ground the 16:9 dim took the frame's mean luma
# 167 -> 56 in DIM_IN, 12.3-12.5 luma a frame at its steepest: eased, but felt as a hard darkening). The dim is a
# scrim coming in over time, so its curve takes the field's rule in time: on the 16:9 screen the frame's mean luma
# never changes faster than DIM_STEP_MAX a frame; DIM_IN is the least duration, a deeper drop takes longer
# (dim_seconds), never past DIM_IN_MAX (the lines' reveal follows the dim).
DIM_STEP_MAX = 8.0       # mean luma (0-255) per frame at FPS: the dim's steepest frame on the 16:9 end screen
DIM_IN_MAX = 0.9         # s: the longest the dim takes
MAST_AT = 0.15           # the masthead and its mark start to fade in (over MAST_IN)
RULE_AT, RULE_IN = 0.25, 0.5     # the gold rule draws out
SHARE_AT = 0.45          # the share line's first word starts to rise
_MOTION_NAMES = {"MAST_IN": "motion.reveal", "WORD_STAGGER": "motion.word_stagger", "WORD_IN": "motion.word_in",
                 "SHARE_RISE": "motion.rise_l", "FOLLOW_IN": "motion.line_in", "FOLLOW_RISE": "motion.rise_m",
                 "LOOP_FOLLOW_RISE": "motion.rise_m", "LOOP_PUSH": "motion.push_per_frame",
                 "LOOP_PUSH_CAP": "motion.push_cap"}


def motion(name: str):
    """A reveal / push value this card draws with (a _MOTION_NAMES name, or LOOP_CROSSFADE: (with the share line,
    without)), the active brand's token now."""
    if name == "LOOP_CROSSFADE":
        return (tok("motion.loop_crossfade"), tok("motion.loop_crossfade_visual"))
    return tok(_MOTION_NAMES[name])
# The masthead, the follow line (FOLLOW), the 16:9 hand-off (HANDOFF) and the colours (#D4AF37 gold until
# 2026-10-29) are the active brand's (src/brand.py), read when drawn (QF-35a); the names below serve their readers.
_BRAND_NAMES = {"BRAND": "MASTHEAD", "FOLLOW": "FOLLOW_LINE", "HANDOFF": "HANDOFF_LINE", "GOLD": "GOLD", "WHITE": "BONE",
                "INK": "INK"}


def __getattr__(name: str):
    """A brand string or colour this module bound at import before QF-35a: the active brand's, read now."""
    if name in _BRAND_NAMES:
        return getattr(brand, _BRAND_NAMES[name])
    if name in _MOTION_NAMES or name == "LOOP_CROSSFADE":   # SYS-E-1: the motion tokens, read now
        return motion(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


BAND = (0.26, 0.66)      # the rows the card's text occupies
TARGETS = {"band": 30.0, "band_std": 12.0, "outside": 40.0}   # 0-255 luminance after the dim
TOP = 780                # y of the share line's first row (9:16)
MAST_TOP = 490           # y of the masthead's cap top (9:16): the composition spreads through the Reel box
MASTHEAD_W = 740         # the masthead group at cover scale (panel round 2, 2026-11-02: ~200 px wide here, big
                         # everywhere else); on 9:16 the Reel box's width (735; 696 centred on the ads box) caps it first
RULE_HALF = 120          # half the gold rule under the masthead
FOLLOW_GAP = 60          # px from the share block's last row to the follow line
# The share ask: two lines at the largest of these that fits (operator, 2026-10-29: two lines at >= 54 px), else
# three at the largest that fits, then longer asks smaller. Montserrat SemiBold (panel round 2: ~56 px, balanced).
SHARE_SIZES = (58, 56, 54)
SHARE_WEIGHT = 600
SHARE_PLAN = ((2, SHARE_SIZES), (3, SHARE_SIZES), (4, (52,)), (5, (46,)))
# The 16:9 YouTube end screen: YouTube draws its own elements over the last 5-20 s (a video or playlist slot and a
# subscribe button); the card's text stays clear of where they are placed (1920x1080 px).
LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT = 1920, 1080
END_SCREEN_SECONDS = 10.0
END_SCREEN_SLOTS = {"video": (1100, 260, 1820, 660), "subscribe": (140, 700, 420, 980),
                    # panel round 4: a second element (a playlist or the latest upload) fills the lower right; the
                    # channel's YouTube Studio template must place it here before slot_frames may frame it
                    "video2": (1100, 700, 1580, 970)}
END_SCREEN_VIDEO_SLOTS = ("video", "video2")
YOUTUBE_TIMESTAMP_ZONE = (1620, 950, 1920, 1080)   # the player's timestamp and controls (1920x1080 px)
END_SCREEN_GAP = 60
# The 16:9 hand-off (panel round 4): YouTube's own line, pointing the eye at the watch-next slot. A fixed brand line
# with no topic claim (the Reel's share ask stays on the 9:16 bridge and in the captions): brand.HANDOFF_LINE.
LANDSCAPE_SHARE_SIZES = (64, 60, 56)
LANDSCAPE_PUSH = 0.02    # the 16:9 push over END_SCREEN_SECONDS, easing to rest (panel round 3: YouTube's tiles sit
                         # over it, so it barely moves)
LANDSCAPE_SCRIM_X = (1000, 1500)   # the 16:9 dim is full left of the first x and eases to LANDSCAPE_RIGHT_DIM by the
LANDSCAPE_RIGHT_DIM = 0.55         # second: a scrim behind the text column, the right side lighter and sharper.
# The ramp's far end (freeze lane E cycle 3, panel 4 PL-MG-04 / CD MG-endscreen-column-dim): at 1260 (stretched to
# ~1315 by the slope gate) the field ended in a soft vertical edge at x ~1100 on bright grounds, the dark column read
# as a panel; at 1500 its steepest column step falls by about a third on the bright board grounds (Aswan 0.32 -> 0.21,
# Richat 0.24 -> 0.16 luma / px over 40 px) and most of the added dim lies under YouTube's video slots. The start
# stays at 1000, so the text column (to END_SCREEN_SLOTS["video"][0] - END_SCREEN_GAP) keeps its full dim.
# A matte on the 16:9 ground (freeze lane E cycle 4, panel 5 CD-r9-endscreen-rim): the narration's last frame may hold
# its picture in a window on flat black, an essential image shown whole (image_presentation.fit_filter pads it with
# black) or the retired 16:9 vignette (aspect 16/9 until 2026-11-04, picture_vignette), which crushed the corners
# outside an ellipse. Under the right column's lighter dim the window's hard edge read as a bright/dark rim under the
# video slot. The end screen extends its INK field over the matte (matte_window, frames()): the window's edge is a
# band edge (brand_system.edge_feather), eased in with the dim, so the end screen's first frame is still the last
# frame as it is. A window is a matte only when the outside is flat and dark, the picture runs up to its edge (a cut,
# not an object's own outline), and it is a rectangle (padding, a scan's border) or, any other shape, spans the frame
# on digital black (MATTE_HULL_BLACK: a crushed vignette; a photographed black ground, an object on black velvet or a
# chandelier in the dark, is never a matte). Measured over 394 landscape photos of the dev runs and assets: 5 hits,
# all the black borders of scans (a plan, a painting, a manuscript page); a dark foreground strip, a dark rock edge, the
# Pantheon's dark walls, chandeliers and a book on black are not.
MATTE_LEVEL = 24          # a matte pixel: no channel above this (fit_filter's black after grain, grade and encode)
MATTE_FLAT = 4.0          # the matte's luma spread (std) at most this: padding, not a night sky or a dark wall
MATTE_MIN_SHARE = 0.005   # of the frame outside the window, at least
MATTE_DARK_SHARE = 0.95   # of the outside that is matte-dark, at least
MATTE_CUT_SHARE = 0.5     # of the window's inner rim where the picture is off the matte's black, at least
MATTE_RIM_STEP = 6.0      # luma over the matte's median that is picture, not matte, at the rim
MATTE_RECT = 0.98         # the window is a rectangle when its area is at least this share of its bounding box
MATTE_TILE = 64           # px: the stretch of the window's edge whose own step sets the feather (matte_field)
MATTE_HULL_BLACK = 1.0    # a window of any other shape: the matte's median luma at most this (digital black)
KICKER = "WATCH NEXT"    # generic: the video YouTube shows in the slot is not known at render time
# The kicker is the system's (src/brand_system.py kicker.*, cycle 0): the text face at 800 in tracked gold capitals led
# by a flat gold rule, at the wide canvas's kicker size (it was IBM Plex Mono 600 at 30 px, 0.18 em: a second kicker
# voice, RUBRIC anchor BC-A2).
SLOT_FRAME_OFFSET = 12   # the optional hairline frames around the YouTube element slots
CREDITS_LINE = "IMAGE CREDITS IN DESCRIPTION"   # only when the description really carries credit lines
CREDITS_SIZE = 22
# The 9:16 loop bridge (operator, 2026-11-02): the Reel's ending, resolving into frame 0. Wired into the composer in
# place of the Reel's end card on 2026-11-03, without its share line (operator: LOOP_LINE False; the line is still
# drawn when a caller asks for it, line=True). LOOP_VARIANT is what the composition record calls it.
LOOP_VARIANT = "loop_bridge_v1"
LOOP_LINE = False
LOOP_SECONDS = 1.0
LOOP_TIMES = {"in": 0.25, "out": 0.5}   # the line is up by "in"; with the line, the crossfade starts at "out"
LOOP_DIM = 0.4                          # of the end card's measured dim (panel round 3: 0.8 read as a black dip that
                                        # flashed on every loop), under the share line only (line=True): it seats a
                                        # line planned on the dimmed frame. A bridge with FOLLOW (its own held plate,
                                        # planned on the undimmed pictures) or with no element is not dimmed (freeze
                                        # lane E cycle 3, panel 4 MO-E1: on a bright ending the dim read as a dip to
                                        # dark before the crossfade, 171 -> 113 luma in 6 frames, its first step 2.5x a
                                        # dissolve step). Where it is drawn it comes in on ease_io (a field moving
                                        # between two rests), never at full speed.
# LOOP_CROSSFADE (the crossfade's length: with the share line, without: the purely visual bridge), LOOP_PUSH (zoom
# per frame: the composer's forward drift on the closing and opening shots, still_camera "push" / "hook", the speed
# they keep at the loop seam, QF-22) and LOOP_PUSH_CAP (the furthest that push goes, still_camera.endpoint_for) are
# motion tokens (motion(); SYS-E-1).
LOOP_LINE_RISE = 12                     # px the bridge's share line rises (line=True; not shipped: LOOP_LINE False)
LOOP_MAX_STEP_L = 25.0                  # the largest mean-L* change allowed between consecutive frames of the seam
LOOP_MAX_MAD = 8.0                      # the bridge's last frame against frame 0: mean absolute difference, 0-255
# What LOOP_MAX_MAD gates (stats["mad_last"], seam_problems): the bridge's last frame as loop_bridge draws it (8-bit
# RGB, WIDTH x HEIGHT, before the BT.709 conversion and the delivery encode) against `first`, the Reel's frame 0 as
# the composer reads it back from the encoded body; the mean of |difference| over every pixel and all three channels.
# A decoded delivery clip measures more (the encode's own noise on both frames: tests allow the gate + 4); a reading
# taken from an encoded clip is not this measure (LOOP_SEAM_MEASURE, recorded with it).
LOOP_SEAM_MEASURE = f"rgb_mad_8bit_{WIDTH}x{HEIGHT}_pre_encode"
LOOP_CENTRE = 0.40                      # the line's vertical centre, as a share of the height (y 768: the Reel box)
FOLLOW_SIZES = range(40, 29, -1)
FOLLOW_TRACK = 0.12      # the follow line's letter spacing, as a share of its size
# The loop bridge's FOLLOW (QF-83; operator 2026-11-10: on in production, config.LOOP_FOLLOW_OVERLAY): the end card's
# follow ask cut to its one word, in the follow line's type (tracked Montserrat 800 capitals) but in bone, centred on
# LOOP_CENTRE inside the Reel box. Not gold: the bridge is not dimmed under it (LOOP_DIM), and gold over its widest halo
# measured 3.5-3.7:1 on a white frame and under 4.5:1 on one of seven real Reel endings; bone holds 4.8:1 on all.
# It rises in over LOOP_FOLLOW_FRAMES[0] frames from the bridge's first frame and fades out over its last
# LOOP_FOLLOW_FRAMES[1] frames, so the bridge's first and last frames carry none of it and the seam still lands on
# frame 0. It is planned by the one overlay-label rule (_follow_plate: label halo, INK type, then a held INK plate) on
# the lightest and darkest of the pictures it sits on and every full-opacity frame is measured; under LOOP_FOLLOW_MIN
# the bridge is shipped without it (the review card says so).
LOOP_FOLLOW = "FOLLOW"
LOOP_FOLLOW_SIZE = 64
LOOP_FOLLOW_FRAMES = (6, 8)
# LOOP_FOLLOW_RISE: px it rises while it comes in, the end card's follow line's (motion.rise_m; SYS-E-1)
LOOP_FOLLOW_MIN = 4.5                   # WCAG AA on every full-opacity frame (the plan aims for brand.PLACE_TARGET)

_FOLLOW_CLAUSE = re.compile(r"[\s,;:—–-]*(?:and\s+|then\s+|&\s+)?(?:please\s+)?follow\b.*$", re.IGNORECASE)


def card_text(share_line: str) -> str:
    """The share half of a caption's share-and-follow line: the card says FOLLOW itself ("Send this to the
    codebreaker you know, and follow for the next relic." -> "Send this to the codebreaker you know.")."""
    line = " ".join((share_line or "").split())
    head = _FOLLOW_CLAUSE.sub("", line).rstrip(" ,;:-—–")
    if not head:
        return ""
    return head if head[-1] in ".!?" else head + "."


def end_card_record(master, fallback: bool = False) -> Optional[Dict[str, Any]]:
    """What the run's payload keeps about its end card (from the master's composition record), so audience
    analysis can compare Reels with and without one; None when the master has none."""
    import json
    from pathlib import Path
    try:
        card = json.loads(Path(master).with_suffix(".composition.json").read_text(encoding="utf-8")).get("end_card")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(card, dict):
        return None
    # The share line's fallback only matters where a line is drawn: the loop bridge (no line) never shows it.
    out = {"variant": card.get("variant"), "start_s": card.get("start_s"), "duration_s": card.get("duration_s"),
           "text": card.get("text"), "fallback": bool(fallback) and card.get("text") is not None}
    if isinstance(card.get("note"), dict):   # the loop bridge's render-time gate failed (check_loop_bridge)
        out["note"] = card["note"]
    if card.get("follow") or card.get("follow_skipped"):   # QF-83: whether the bridge asked for the follow
        out.update(follow=bool(card.get("follow")), fallback=False if card.get("follow") else out["fallback"])
        if card.get("follow_skipped"):
            out["follow_skipped"] = card["follow_skipped"]
    return out


def end_card_note(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The review card's note about the Reel's ending: the loop bridge's gate note when the bridge was dropped
    (a warning), an info note when the bridge was asked for its FOLLOW but shipped without it (QF-83), else an info
    note when the end card had to use the plain fallback line."""
    if record and isinstance(record.get("note"), dict):
        return record["note"]
    if record and record.get("follow_skipped"):
        return {"type": "end_card", "card": "End card", "severity": "info",
                "issue": f"The loop bridge shipped without its FOLLOW: {record['follow_skipped']}."}
    if not record or not record.get("fallback"):
        return None
    return {"type": "end_card", "card": "End card", "severity": "info",
            "issue": (f"The writer's share line did not pass its checks, so the end card shows the plain line "
                      f"“{record.get('text')}”, not one about this story.")}


def ease(t: float) -> float:
    """The system's ease_out (src/brand_system.py: an element arriving)."""
    return ease_out(t)


def dim_seconds(drop: float, curve=None) -> float:
    """How long the dim takes to come in for a mean-luma `drop` (0-255, the frame's mean before less after): DIM_IN,
    or as many whole frames as keep the steepest frame of `curve` (ease_io by default) at or under DIM_STEP_MAX,
    never over DIM_IN_MAX."""
    curve = ease_io if curve is None else curve
    n = max(1, round(DIM_IN * FPS))
    while n < round(DIM_IN_MAX * FPS):
        steepest = max(curve((i + 1) / n) - curve(i / n) for i in range(n))
        if steepest * max(0.0, float(drop)) <= DIM_STEP_MAX:
            break
        n += 1
    return n / FPS


def ease_io(t: float) -> float:
    """The system's ease_in_out (smoothstep: a field moving between rests), in this module's own arithmetic form
    (3t^2 - 2t^3), kept so the shipped loop bridge stays bit-identical (the other form differs by 1 level on a few px)."""
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t


def _tracked_width(font, text: str, spacing: float) -> float:
    """The width plan_text draws `text` at with `spacing` px tracking: the shaped, kerned run (QF-21; the sum of
    single letters it was dropped the kerning the plate is drawn with)."""
    return tracked_width(text, font, spacing)


def _balanced(words: List[str], font, width: float, max_lines: int) -> Optional[List[List[str]]]:
    """`words` on the fewest lines (up to max_lines) that fit `width`, the most even break of those, by the heading
    engine's one line rule (freeze cycle 4, SYS item 2: heading.set_lines; no line but the last ends on an article or
    a preposition, no one-word last line, a value kept with its unit); None when no such break fits (the caller then
    takes a smaller size or another line)."""
    from .heading import set_lines
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    got = set_lines(" ".join(words), lambda t: probe.textlength(t, font=font), width, max_lines)
    return [ln.split() for ln in got] if got else None


_FOLLOW_WORD = re.compile(r"\bfollow", re.IGNORECASE)


def says_follow(text: str) -> bool:
    """Whether a share line already asks for a follow (then the card does not ask again)."""
    return bool(_FOLLOW_WORD.search(text or ""))


def _masthead(max_w: int, room: float | None = None, canvas: str = "tall") -> dict[str, Any]:
    """The system's masthead (src/brand_system.py masthead.*: the gold diamond, then the name in tracked masthead
    capitals in bone). On the tall canvas, at the largest size from its masthead size whose group fits MASTHEAD_W and
    `max_w` (the end card's brand moment); on the wide canvas (the 16:9 end screen) at the system's wide masthead
    size, type_px("masthead", "wide"), never grown (freeze lane E cycle 3: the token decides; it drew 64 from the tall
    size while the board's table said 60). Round 5: where even that size is wider than `room` (a longer brand name),
    the size steps down the system's fit ladder (masthead.fit_ladder, to tok("masthead.fit_floor") of it); below the
    floor the group stays too wide and frames() refuses it with its reason."""
    from .brand_system import masthead_spec, type_px
    from .masthead import fit_ladder
    goal = min(MASTHEAD_W, max_w)
    start = int(type_px("masthead", canvas))
    room = goal if room is None else room

    def at(size):
        spec = masthead_spec(size)
        font = get_font(brand.MASTHEAD_FONT, size, weight=spec["weight"])
        r, gap = round(spec["mark_r"]), round(spec["gap"])
        from .masthead import group_width
        group = group_width(size)   # round 7: the one fit width (masthead.group_width, its drawn bounds)
        return {"text": brand.MASTHEAD, "font": font, "track": spec["track"], "mark_r": r, "gap": gap,
                "width": round(group), "size": size, "edge": spec["edge"]}
    first = at(start)
    if canvas != "tall" and first["width"] <= room:
        return first   # the canvas's own size: only the tall end card grows into its brand moment
    if first["width"] > room:
        for size in fit_ladder(start)[1:]:   # round 7 (sweep): the ladder's own sizes, never rounded below its floor
            got = at(size)
            if got["width"] <= room:
                return got
        return first
    best = None
    for size in range(start, 140):
        spec = masthead_spec(size)
        font = get_font(brand.MASTHEAD_FONT, size, weight=spec["weight"])
        track = spec["track"]
        r, gap = round(spec["mark_r"]), round(spec["gap"])
        from .masthead import group_width
        group = group_width(size)
        if group > goal and best is not None:
            break
        best = {"text": brand.MASTHEAD, "font": font, "track": track, "mark_r": r, "gap": gap, "width": round(group),
                "size": size, "edge": spec["edge"]}
    return best


def landscape_masthead(room: float) -> dict[str, Any]:
    """The 16:9 end screen's masthead: the wide canvas's masthead size (the token), down the fit ladder only where
    its group is wider than `room`. What a record or a board table should quote as the end screen's size."""
    return _masthead(round(room), room=room, canvas="wide")


def reel_column(width: int = WIDTH, height: int = HEIGHT) -> tuple[float, int]:
    """(centre x, width) of the 9:16 card's column: the Reel box, its group centred in it (x 480 at 1080 x 1920).
    QF-24: the shared organic Reel box (x 113-848) leaves 616 px centred on the frame (its right rail, 232 px, is wider
    than its left, 113 px), which set the masthead below its cover scale and the share line a size and a line
    smaller; centred in the box, as the captions are (video_composer.reel_caption_centre_x), the card keeps its
    reviewed sizes (decided on real renders: docs/reviews/quality-fixes-2026-11/QF-24/README.md). Cycle 0 round 7
    (panel F19): the column is the box less the 9:16 margin token (safe_zones.text_box: x 137-824), not its edges."""
    from .safe_zones import text_box
    x0, _, x1, _ = text_box("reel", width, height)
    return round((x0 + x1) / 2), x1 - x0


def layout(share: str, width: int = WIDTH, height: int = HEIGHT) -> dict[str, Any]:
    """Where everything goes. 9:16: the masthead centred at cover scale, a short gold rule, the share line in bone
    and, unless the share line already says follow, FOLLOW FOR THE NEXT RELIC in tracked gold, all centred in the
    Reel safe area (reel_column) and spread through its height. 16:9 (the YouTube end screen): the masthead top-left
    in title-safe, then the share line, left-aligned, all left of the end screen's video element and above its
    subscribe button (END_SCREEN_SLOTS); no follow line (the subscribe element asks)."""
    from .brand_system import tok, type_px
    from .typography import drawable, field_font
    landscape = width > height
    words = drawable(share, brand.TEXT_FONT).split() or ["…"]  # no emoji or missing-glyph boxes (2026-10-29)
    floor = None   # 16:9: the lowest the text column may reach (above the subscribe slot)
    cx = width / 2
    if landscape:
        x0, y0 = round(width * 0.08), round(height * 0.12)
        max_w = min(END_SCREEN_SLOTS[k][0] for k in END_SCREEN_VIDEO_SLOTS) - END_SCREEN_GAP - x0
        plan = ((1, LANDSCAPE_SHARE_SIZES), (2, LANDSCAPE_SHARE_SIZES), (3, LANDSCAPE_SHARE_SIZES), (4, (48,)),
                (5, (44, 40, 36)))   # the last step only when the floor (above the subscribe slot) bites
        m = landscape_masthead(max_w)
        # the column's kicker: the system's kicker face at the wide canvas's size (end_screen_extras draws it)
        kfont = get_font(brand.TEXT_FONT, type_px("kicker", "wide"), weight=tok("kicker.weight"))
        top0 = y0 + m["font"].getbbox("R", anchor="lt")[3] + 40 + 70 + _column_kicker_step(kfont)
        floor = END_SCREEN_SLOTS["subscribe"][1] - END_SCREEN_GAP
    else:
        cx, max_w = reel_column(width, height)
        plan = SHARE_PLAN
    share_font, lines = None, None
    for max_lines, sizes in plan:
        for size in sizes:
            # the share line's words are planned one by one: all in the line's face (QF-94)
            share_font = field_font(" ".join(words), get_font(brand.TEXT_FONT, size, weight=SHARE_WEIGHT))
            lines = _balanced(words, share_font, max_w, max_lines)
            if lines and floor is not None and top0 + len(lines) * round(size * 1.24) > floor:
                lines = None   # it would run into the subscribe slot: fewer lines or a smaller size
            if lines:
                break
        if lines:
            break
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    if not lines:  # longer still (the writer's limit keeps it far shorter): filled lines, never wider than the area
        lines = [[]]
        for w in words:
            if lines[-1] and probe.textlength(" ".join(lines[-1] + [w]), font=share_font) > max_w:
                lines.append([])
            lines[-1].append(w)
    line_h = round(share_font.size * 1.24)
    mast = landscape_masthead(max_w) if landscape else _masthead(max_w, room=max_w)
    follow = not landscape and not says_follow(share)
    follow_font = spacing = follow_w = None
    for size in FOLLOW_SIZES:  # the largest follow line that fits the safe width
        follow_font, spacing = get_font(brand.TEXT_FONT, size, weight=800), size * FOLLOW_TRACK
        follow_w = _tracked_width(follow_font, brand.FOLLOW_LINE, spacing)
        if follow_w <= max_w - 20:
            break
    cap = mast["font"].getbbox("R", anchor="lt")
    if landscape:
        mast_xy = (x0 + 2 * mast["mark_r"] + mast["gap"], y0)
        mark_c = (x0 + mast["mark_r"], y0 + (cap[1] + cap[3]) / 2)
        rule_y = y0 + cap[3] + 40
        rule = (x0, x0 + 240)
        # The WATCH NEXT kicker leads the hand-off line in the text column (freeze lane E cycle 3): its cap line
        # where the line's was, the line one kicker.gap below its baseline (_column_kicker_step).
        k_cap = rule_y + 70 + share_font.getbbox("H", anchor="la")[1]
        kicker_baseline = k_cap + cap_height(kfont)
        top = rule_y + 70 + _column_kicker_step(kfont)
        align_x = x0
    ink = probe.textbbox((0, 0), brand.FOLLOW_LINE, font=follow_font)
    if not landscape:   # the group (masthead cap top to the last line's ink) centred in the Reel box, inside it
        _, by0, _, by1 = REEL.box(width, height)
        g_top = MAST_TOP + cap[1]
        g_bottom = TOP + len(lines) * line_h + (FOLLOW_GAP + ink[3] if follow else 0)
        dy = round((by0 + by1) / 2 - (g_top + g_bottom) / 2)
        dy = max(by0 - g_top, min(by1 - g_bottom, dy)) if g_bottom - g_top <= by1 - by0 else by0 - g_top
        mast_top = MAST_TOP + dy
        mast_x0 = round(cx - mast["width"] / 2)
        mast_xy = (mast_x0 + 2 * mast["mark_r"] + mast["gap"], mast_top)
        mark_c = (mast_x0 + mast["mark_r"], mast_top + (cap[1] + cap[3]) / 2)
        rule_y = mast_top + cap[3] + 64
        rule = (cx - RULE_HALF, cx + RULE_HALF)
        top = TOP + dy
        align_x = None
    share_bottom = top + len(lines) * line_h
    follow_y = share_bottom + FOLLOW_GAP
    if follow:
        box = (cx - follow_w / 2, follow_y + ink[1], cx + follow_w / 2, follow_y + ink[3])
    else:   # no follow line: the box is the share block's
        widest = max(probe.textlength(" ".join(ln), font=share_font) for ln in lines)
        left = align_x if landscape else cx - widest / 2
        box = (left, top, left + widest, share_bottom)
    return {"share_font": share_font, "lines": lines, "line_h": line_h, "follow_font": follow_font, "spacing": spacing,
            "follow_y": follow_y, "box": box, "max_width": max_w, "follow": follow, "top": top,
            "masthead": mast, "masthead_xy": mast_xy, "mark_centre": mark_c, "rule_y": rule_y, "rule": rule,
            "align_x": align_x, "landscape": landscape, "masthead_top": mast_xy[1], "centre_x": cx,
            "kicker_baseline": kicker_baseline if landscape else None, "kicker_font": kfont if landscape else None,
            "share_widths": [probe.textlength(" ".join(ln), font=share_font) for ln in lines]}


def _bump(height: int) -> np.ndarray:
    rows = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    return 0.5 + 0.5 * np.cos(np.pi * np.clip(np.abs(rows - 0.46) / 0.54, 0, 1))   # smooth: no crease at the peak


DIM_MAX = 0.97   # the deepest the card's dim goes (fit_dim), the field ramp's worst case (_scrim)


def fit_dim(base: Image.Image) -> Tuple[np.ndarray, np.ndarray, Dict[str, float]]:
    """The lightest dim (per row) and softening (per row) that make the card read on this frame, and what they
    measure to. A busy text band (handwriting, foliage) is softened too, over the whole frame so no hazy stripe
    shows; a smooth one (a sky) is not."""
    h = base.size[1]
    lum = np.asarray(base.convert("L"), dtype=np.float32)
    bump = _bump(h)
    a, b = int(h * BAND[0]), int(h * BAND[1])
    texture = float(lum[a:b].std())
    soften = float(np.clip((texture - 18) / 30, 0, 0.85))
    soft = np.asarray(base.convert("L").filter(ImageFilter.GaussianBlur(14)), dtype=np.float32)
    weight = soften * (0.55 + 0.45 * bump)
    seen = lum * (1 - weight) + soft * weight
    ink = float(np.mean(brand.INK))
    for floor in np.arange(0.50, 0.92, 0.02):
        for peak in np.arange(0.86, 0.975, 0.01):
            shape = np.clip(floor + (peak - floor) * bump, 0, DIM_MAX)
            out = seen * (1 - shape) + ink * shape
            if (out[a:b].mean() <= TARGETS["band"] and out[a:b].std() <= TARGETS["band_std"]
                    and out[:a].mean() <= TARGETS["outside"] and out[b:].mean() <= TARGETS["outside"]):
                break
        else:
            continue
        break
    stats = {"floor": round(float(floor), 2), "peak": round(float(peak), 2), "soften": round(soften, 2),
             "texture": round(texture, 1), "band": round(float(out[a:b].mean()), 1),
             "band_std": round(float(out[a:b].std()), 1), "above": round(float(out[:a].mean()), 1),
             "below": round(float(out[b:].mean()), 1)}
    return shape, weight, stats



def _pushed(arr: Image.Image, z: float, width: int, height: int) -> np.ndarray:
    """`arr` zoomed by z about its centre at width x height, through the shared camera (QF-22; Lanczos, as before)."""
    return np.asarray(kenburns(arr, (width, height), zoom=z, resample=Image.LANCZOS), dtype=np.float32)


def zoom_at(t: float, seconds: float | None = None) -> float:
    """The push-in: from the last frame as it is (1.0), easing out to 3.5% closer by the card's end (`seconds`, the
    card's length: SECONDS unless it holds longer to be read, card_seconds)."""
    u = max(0.0, min(1.0, t / (seconds or SECONDS)))
    return 1.0 + 0.035 * (1 - (1 - u) ** 2)


def word_at(k: int) -> float:
    """When the share line's word k (0-based) starts to rise."""
    return SHARE_AT + motion("WORD_STAGGER") * k


def share_done(n_words: int) -> float:
    """When the last of `n_words` share words has risen (the 16:9 extras follow it)."""
    return word_at(max(0, n_words - 1)) + motion("WORD_IN")


def reading_need(lines: list[tuple[float, str]], cps: float | None = None) -> float:
    """Seconds into the card by which every line has been read once at the reading pace (tok("motion.caption_cps"),
    17: the operator's target, freeze lane E cycle 1): `lines` as (when it starts to show, its text) in reading
    order. The reader starts on the first line as it shows and reads each in turn, never before it shows."""
    cps = float(cps or tok("motion.caption_cps"))
    t = end = 0.0
    for start, text in lines:
        chars = len(" ".join((text or "").split()))
        if not chars:
            continue
        t = max(t, start) + chars / cps
        end = max(end, t)
    return end


def card_lines(lay: dict[str, Any], *, kicker: bool = False, credits: bool = False) -> list[tuple[float, str]]:
    """The lines a card's viewer reads, in order, each with when it starts to show (frames()'s own timeline): the
    share line, then the follow line (9:16) or the WATCH NEXT kicker and the credits line (16:9, when drawn). The
    masthead is the brand's mark, read at a glance, and is not counted."""
    n = sum(len(ln) for ln in lay["lines"])
    out = [(SHARE_AT, " ".join(" ".join(ln) for ln in lay["lines"]))]
    if lay.get("follow"):
        out.append((BELL_AT - motion("FOLLOW_IN"), brand.FOLLOW_LINE))
    if kicker:
        out.append((share_done(n), KICKER))
    if credits:
        out.append((share_done(n) + 0.3, CREDITS_LINE))
    return out


def card_seconds(lay: dict[str, Any], *, kicker: bool = False, credits: bool = False) -> float:
    """The card's length. 9:16: SECONDS, held on, in whole frames, until every line has been read once at the reading
    pace (reading_need over card_lines). The share line's hold is a design choice, not a caption's (operator,
    2026-11-19): 2.9 s showed the real 56-character line and the follow line at 33 characters a second. 16:9: the end
    screen's own END_SCREEN_SECONDS (YouTube's element template; its lines need under half of it), its reading
    recorded by held_layout."""
    if lay["landscape"]:
        return END_SCREEN_SECONDS
    need = reading_need(card_lines(lay, kicker=kicker, credits=credits))
    return max(SECONDS, float(np.ceil(round(need * FPS, 6)) / FPS))


def end_screen_encoder(seconds: float | None = None) -> list[str]:
    """The 16:9 end screen's encoder (freeze lane E cycle 4, panel 5 MO-es-keyframe): the one delivery encoder
    (encoding.delivery_video_encoder) with the end screen as ONE group of pictures. At x264's default keyint (250) its
    still tail took a second I-frame at frame 250 (8.33 s): a one-frame pulse on the dark field (MAD 0.44-0.59, an
    8x8 block stepping ~8 luma) on every board clip. The keyint covers the screen's frames and scene-cut detection is
    off (as on the masters), so the only I-frame is its first; nothing else in the encode changes (no still-image
    tune: it would change the picture's grain)."""
    from .encoding import delivery_video_encoder
    n = int(round((END_SCREEN_SECONDS if seconds is None else seconds) * FPS))
    return [*delivery_video_encoder(), "-g", str(max(1, n)), "-sc_threshold", "0"]


def card_room(vo_dur: float) -> float:
    """The most a 9:16 card may last after a narration of `vo_dur` seconds: what the release gate leaves of the master
    (config.RELEASE_DURATION_MAX, the Reel master's ceiling, re-measured at publish)."""
    from . import config
    return max(0.0, float(config.RELEASE_DURATION_MAX) - float(vo_dur))


def _word_xs(lay: dict[str, Any], width: int) -> list[tuple[int, int, str, float, int]]:
    """(line, word, text, x, y) of every word of the share line as it rests."""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = lay["share_font"]
    out = []
    for li, line in enumerate(lay["lines"]):
        text = " ".join(line)
        left = lay["align_x"] if lay["align_x"] is not None else lay["centre_x"] - probe.textlength(text, font=f) / 2
        for wi, w in enumerate(line):
            x = left + probe.textlength(" ".join(line[:wi]) + (" " if wi else ""), font=f)
            out.append((li, wi, w, x, lay["top"] + li * lay["line_h"]))
    return out


def mark_diamond(lay: dict[str, Any]) -> list[tuple[float, float]]:
    """The masthead's diamond on this layout: its four points."""
    cx, cy = lay["mark_centre"]
    r = lay["masthead"]["mark_r"]
    return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]


def _mark(d: ImageDraw.ImageDraw, lay: dict[str, Any], a: float, r_draw: float) -> None:
    """The masthead's gold diamond (the system's mark, drawn by masthead.draw_mark once masthead.verify passed it) and
    the gold rule drawn out under the masthead."""
    if a > 0:
        from . import masthead
        masthead.draw_mark(d, mark_diamond(lay), lay["masthead"].get("edge", 3), alpha=int(255 * a))
    if r_draw > 0:
        x0, x1 = lay["rule"]
        y = lay["rule_y"]
        if lay["landscape"]:
            d.line([(x0, y), (x0 + (x1 - x0) * r_draw, y)], fill=(*brand.GOLD, 235), width=4)
        else:
            mid, half = (x0 + x1) / 2, (x1 - x0) / 2 * r_draw
            d.line([(mid - half, y), (mid + half, y)], fill=(*brand.GOLD, 235), width=4)


def landscape_zoom_at(t: float) -> float:
    """The 16:9 push: at most LANDSCAPE_PUSH closer over END_SCREEN_SECONDS, easing to rest (YouTube's element
    tiles sit over the plate, so it barely moves under them)."""
    u = max(0.0, min(1.0, t / END_SCREEN_SECONDS))
    return 1.0 + LANDSCAPE_PUSH * (1 - (1 - u) ** 2)


def _cover(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """`im` filling `size` by a centred crop, never stretched (a no-op when it is that size already)."""
    im = im.convert("RGB")
    if im.size == tuple(size):
        return im
    from PIL import ImageOps
    return ImageOps.fit(im, size, Image.LANCZOS)


def _scrim(width: int) -> np.ndarray:
    """Per column, the share of the dim (and softening) the 16:9 end screen applies: all of it behind the text
    column, easing to LANDSCAPE_RIGHT_DIM over the end screen's video slot side. It is a FIELD (src/brand_system.py):
    its ramp is long enough that on the brightest ground fit_dim can dim (white under its deepest shape, DIM_MAX) its
    steepest point stays within the gate scrim.slope_max (cycle 0 round 7, tokens sweep #4: 0.558-0.595 luma / px on
    bone and white grounds over the 260 px ramp), so it starts at LANDSCAPE_SCRIM_X[0] and runs at least to
    LANDSCAPE_SCRIM_X[1]."""
    from .brand_system import SMOOTHSTEP_PEAK, tok
    x = np.arange(width, dtype=np.float32)
    a, b = LANDSCAPE_SCRIM_X
    step = (1 - LANDSCAPE_RIGHT_DIM) * DIM_MAX * (255.0 - float(np.dot(brand.INK, (0.299, 0.587, 0.114))))
    b = max(b, a + int(np.ceil(SMOOTHSTEP_PEAK * step / tok("scrim.slope_max"))))
    u = np.clip((x - a) / (b - a), 0, 1)
    return 1 - (1 - LANDSCAPE_RIGHT_DIM) * (3 * u * u - 2 * u ** 3)


def matte_window(base: Image.Image) -> Optional[Tuple[np.ndarray, Dict[str, Any]]]:
    """The picture's window on `base` when it sits on a matte (see MATTE_LEVEL), else None: (distance in px from the
    matte at `base`'s size, 0 on the matte; the record). Found at half size: the picture is every pixel brighter than
    MATTE_LEVEL (specks opened away), its window their convex hull; a matte when the outside is at least
    MATTE_MIN_SHARE of the frame, MATTE_DARK_SHARE dark and flat (MATTE_FLAT), at least MATTE_CUT_SHARE of the window's
    inner rim (3 px) is picture (off the matte's black by MATTE_RIM_STEP), and the window is a rectangle (MATTE_RECT)
    or spans the frame across or down on digital black (MATTE_HULL_BLACK)."""
    import cv2
    w, h = base.size
    sw, sh = max(1, w // 2), max(1, h // 2)
    a = np.asarray(base.convert("RGB").resize((sw, sh), Image.BOX), dtype=np.uint8)
    dark = a.max(axis=2) <= MATTE_LEVEL
    pic = cv2.morphologyEx((~dark).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    pts = cv2.findNonZero(pic)
    if pts is None:
        return None
    hull = cv2.convexHull(pts)
    win = np.zeros((sh, sw), np.uint8)
    cv2.fillConvexPoly(win, hull, 1)
    out = win == 0
    share = float(out.mean())
    if share < MATTE_MIN_SHARE:
        return None
    x, y, bw, bh = cv2.boundingRect(hull)
    spans = (x <= 1 and x + bw >= sw - 1) or (y <= 1 and y + bh >= sh - 1)
    rect = float(win.sum()) >= MATTE_RECT * bw * bh
    lum = a.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    dark_share, flat = float(dark[out].mean()), float(lum[out & dark].std()) if (out & dark).any() else 0.0
    # distance (px at half size) to the outside: the frame's own border is not a matte edge (cv2 measures only to
    # zero pixels inside the image)
    dist = cv2.distanceTransform(win, cv2.DIST_L2, 5)
    rim = (win == 1) & (dist <= 3)
    # the picture at the rim: anything off the matte's own black (dark ground inside a window is still picture)
    m0 = float(np.median(lum[out & dark])) if (out & dark).any() else 0.0
    cut = float((lum[rim] > m0 + MATTE_RIM_STEP).mean()) if rim.any() else 0.0
    rec = {"window": "rectangle" if rect else "hull", "share": round(share, 4), "dark": round(dark_share, 3),
           "flat": round(flat, 2), "black": round(m0, 2), "cut": round(cut, 3), "spans": bool(spans)}
    if dark_share < MATTE_DARK_SHARE or flat > MATTE_FLAT or cut < MATTE_CUT_SHARE:
        return None
    if not (rect or (spans and m0 <= MATTE_HULL_BLACK)):
        return None
    full = np.asarray(Image.fromarray(dist * 2.0, "F").resize((w, h), Image.BILINEAR), dtype=np.float32)
    return full, rec


def matte_field(dist: np.ndarray, rest: Image.Image, width: int) -> Tuple[np.ndarray, int]:
    """The INK field over a matte (matte_window's `dist`): 1 on the matte, easing to 0 into the picture by smoothstep
    over the band-edge feather (brand_system.edge_feather) for the step the edge crosses on `rest` (the ground at rest),
    measured again over the longer reach, as band_edge.feather does. The step is the brightest stretch of the edge's:
    per MATTE_TILE square along it, the picture's 95th-percentile luma within the feather, less INK's, the largest of
    them (a long curved edge is mostly under the text column's full dim; its bright stretch is where the rim shows).
    Returns (the field, its feather in px)."""
    from .band_edge import smoothstep
    from .brand_system import edge_feather
    lum = np.asarray(rest.convert("RGB"), dtype=np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    ink = float(np.dot(np.asarray(brand.INK, np.float32), [0.2126, 0.7152, 0.0722]))
    ys, xs = np.mgrid[0:lum.shape[0], 0:lum.shape[1]]
    tiles = (ys // MATTE_TILE) * (lum.shape[1] // MATTE_TILE + 1) + xs // MATTE_TILE
    length = 1.0
    for _ in range(4):
        band = (dist > 0) & (dist <= max(length, 4.0))
        step = 0.0
        if band.any():
            t, v = tiles[band], lum[band]
            order = np.argsort(t, kind="stable")
            t, v = t[order], v[order]
            cuts = np.flatnonzero(np.diff(t)) + 1
            for part in np.split(v, cuts):
                if part.size >= MATTE_TILE:   # a stretch of edge, not a corner's few pixels
                    step = max(step, float(np.percentile(part, 95)) - ink)
        need = edge_feather(max(0.0, step), width)
        if need <= length:
            break
        length = float(need)
    length = max(1.0, length)
    return (1.0 - smoothstep(dist / length)).astype(np.float32), int(round(length))


def mean_lstar(im: Image.Image) -> float:
    """The frame's mean CIE L* (0-100), on a 1/8 scale copy: how bright the picture reads as a whole."""
    a = np.asarray(im.convert("RGB").resize((max(1, im.width // 8), max(1, im.height // 8)), Image.BOX),
                   dtype=np.float32) / 255
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    y = lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    # round 7 (sweep): L* per pixel, then the mean (averaging luminance first read a half-black half-white frame 76)
    lstar = np.where(y > 216 / 24389, 116 * np.cbrt(y) - 16, y * 24389 / 27)
    return float(lstar.mean())


def kicker_rise(a: float) -> int:
    """px the end screen's kicker still sits below its rest at entry progress `a` (0-1): motion.rise_s, read now."""
    return round(tok("motion.rise_s") * (1 - a))


def _column_kicker_step(kfont) -> int:
    """px the hand-off line moves down to seat the column's kicker (`kfont`) above it: the kicker's cap height plus
    the system's one kicker gap (tok("kicker.gap") em: its baseline to the heading's cap line)."""
    from .brand_system import tok
    return round(cap_height(kfont) + tok("kicker.gap") * kfont.size)


def end_screen_extras(width: int = LANDSCAPE_WIDTH, lay: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where the 16:9 end screen's quiet extras go: the WATCH NEXT kicker (the system's kicker: its gold rule, then
    tracked gold capitals in the text face) leading the hand-off line in the text column, left-aligned with it
    (freeze lane E cycle 3: above the video slot it labelled an empty area whenever YouTube drew no element there,
    late or suppressed, and its cap line aligned with nothing; in the column it labels the line and the frame reads
    finished with or without YouTube's elements), the optional hairline frames SLOT_FRAME_OFFSET around the YouTube
    element slots, and the optional credits line bottom-left, right of the subscribe slot and above YouTube's
    progress bar and timestamp. `lay`: the 16:9 layout (default: the hand-off line's)."""
    from .brand_system import tok
    if lay is None:
        lay = layout(brand.HANDOFF_LINE, width, LANDSCAPE_HEIGHT)
    vx0, vy0, vx1, vy1 = END_SCREEN_SLOTS["video"]
    sx0, sy0, sx1, sy1 = END_SCREEN_SLOTS["subscribe"]
    wx0, wy0, wx1, wy1 = END_SCREEN_SLOTS["video2"]
    o = SLOT_FRAME_OFFSET
    kfont = lay["kicker_font"]
    size = kfont.size
    ink = kfont.getbbox(KICKER, anchor="ls")
    kx, kick_baseline = lay["align_x"], lay["kicker_baseline"]
    ch = cap_height(kfont)
    rule_w, rule_gap = max(8, round(ch * tok("kicker.rule"))), round(ch * tok("kicker.rule_gap"))
    thick = max(3, round(ch * tok("kicker.rule_thick")))
    mid = kick_baseline - ch / 2
    cfont = get_font(brand.DATA_FONT, CREDITS_SIZE, weight=tok("credit.weight"))   # freeze cycle 4: the token
    return {"kicker": {"text": KICKER, "font": kfont, "xy": (kx, kick_baseline), "track": tok("kicker.track") * size,
                       "text_xy": (kx + rule_w + rule_gap, kick_baseline),
                       "rule": (kx, round(mid - thick / 2), kx + rule_w - 1, round(mid - thick / 2) + thick - 1),
                       "top": kick_baseline + ink[1]},
            "frames": {"video": (vx0 - o, vy0 - o, vx1 + o, vy1 + o), "subscribe": (sx0 - o, sy0 - o, sx1 + o, sy1 + o),
                       "video2": (wx0 - o, wy0 - o, wx1 + o, wy1 + o)},
            "credits": {"text": CREDITS_LINE, "font": cfont, "xy": (sx1 + o + 48, sy1), "track": 0.08 * CREDITS_SIZE}}


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def end_screen_problems(lay: dict[str, Any], width: int = LANDSCAPE_WIDTH, *, credits: bool = False) -> list[str]:
    """What, on a 16:9 layout, runs into YouTube's end-screen elements: the text column (masthead to the last line)
    and its kicker against every slot (with its hairline frame) and the player's timestamp zone, and (when drawn)
    the credits line likewise. Empty when all clear."""
    ex = end_screen_extras(width, lay)
    m = lay["masthead"]
    x0 = lay["masthead_xy"][0] - 2 * m["mark_r"] - m["gap"]
    col_right = max(x0 + m["width"], lay["rule"][1], lay["align_x"] + max(lay["share_widths"]))
    column = (x0, lay["masthead_top"], col_right, lay["top"] + len(lay["lines"]) * lay["line_h"])
    k = ex["kicker"]
    kw = _tracked_width(k["font"], k["text"], k["track"])
    kicker = (k["xy"][0], k["top"], k["xy"][0] + kw, k["xy"][1])
    zones = {**ex["frames"], "timestamp": YOUTUBE_TIMESTAMP_ZONE}
    items = {"text column": (column, zones), "kicker": (kicker, zones)}
    if credits:
        c = ex["credits"]
        cw = _tracked_width(c["font"], c["text"], c["track"])
        cb = c["font"].getbbox(c["text"], anchor="ls")
        items["credits line"] = ((c["xy"][0], c["xy"][1] + cb[1], c["xy"][0] + cw, c["xy"][1] + cb[3]), zones)
    return [f"the {name} runs into the {zone} slot" if zone != "timestamp" else f"the {name} runs into the timestamp"
            for name, (box, zs) in items.items() for zone, z in zs.items() if _overlap(box, z)]


def held_layout(text: str, width: int, height: int, *, credits: bool = False,
                room: float | None = None) -> tuple[str, dict[str, Any], float, dict[str, Any]]:
    """(the line drawn, its layout, the card's length, the reading record). The card holds until its lines read at the
    reading pace (card_seconds). `room` (9:16: card_room, what the release gate leaves after the narration) bounds it:
    where the line cannot be read inside it, the card takes the shorter verified line, the plain fallback's share half
    (script_engine.SHARE_FALLBACK, never new wording), when that one can; else it keeps the line, lasts `room` and the
    record says the pace it is shown at (recorded, not failed: the copy is the fix)."""
    landscape = width > height
    lay = layout(text, width, height)
    seconds = card_seconds(lay, kicker=landscape, credits=credits and landscape)
    line = "as given"
    if room is not None and seconds > room + 1e-9 and not landscape:
        from .script_engine import SHARE_FALLBACK
        alt = card_text(SHARE_FALLBACK)
        if len(alt) < len(" ".join(text.split())):
            alt_lay = layout(alt, width, height)
            alt_s = card_seconds(alt_lay)
            if alt_s <= room + 1e-9:
                text, lay, seconds, line = alt, alt_lay, alt_s, "shorter verified line (the fallback's)"
    lines = card_lines(lay, kicker=landscape, credits=credits and landscape)
    need = reading_need(lines)
    if room is not None and seconds > room + 1e-9:
        seconds = max(1.0 / FPS, float(np.floor(round(room * FPS, 6)) / FPS))
    chars = sum(len(" ".join(t.split())) for _, t in lines)
    window = seconds - lines[0][0]
    rec = {"target_cps": float(tok("motion.caption_cps")), "need_s": round(need, 3), "seconds": round(seconds, 3),
           "room_s": None if room is None else round(room, 3), "line": line, "chars": chars,
           "shown_cps": round(chars / window, 1) if window > 0 else None}
    rec["over_pace"] = need > seconds + 1e-9
    if rec["over_pace"]:
        log.warning("End card: its lines need %.2f s to read at %s cps; the card lasts %.2f s (%s cps)", need,
                    rec["target_cps"], seconds, rec["shown_cps"])
    return text, lay, seconds, rec


def frames(last: Image.Image, share: str, stats: dict[str, Any] | None = None, *, slot_frames: bool = False,
           credits: bool = False, handoff: bool = True, room: float | None = None) -> Iterator[Image.Image]:
    """The card's frames (RGB, FPS) over the narration's last frame: WIDTHxHEIGHT when `last` is upright, the 16:9
    YouTube end screen (1920x1080) when it is wide (`last` is cropped to the size, never stretched), for
    card_seconds: SECONDS (9:16) or END_SCREEN_SECONDS (16:9), held on until every line reads at the reading pace
    (held_layout; `room` bounds a 9:16 card, see there; stats["reading"] records it). `stats`, when given, receives
    the measured dim, the layout and each line's contrast
    on the last frame. 16:9 only: the dim is a scrim behind the text column (the right side, under YouTube's video
    element, lighter and sharper), the push is at most LANDSCAPE_PUSH and the WATCH NEXT kicker leads the line in the
    text column, in with the gold rule (its contrast in stats["contrast_kicker"]); `slot_frames` draws GOLD_DIM
    hairlines around the element slots (only for a channel whose YouTube Studio elements are placed at exactly
    END_SCREEN_SLOTS); `credits` adds IMAGE CREDITS IN DESCRIPTION (only when the description carries them;
    stats["contrast_credits"]). The 16:9 screen says HANDOFF, YouTube's hand-off to the watch-next slot, in place of
    the Reel's share ask (panel round 4; `handoff=False` keeps `share`); stats["text"] records the line drawn."""
    from .typography import plan_text
    landscape = last.width > last.height
    width, height = (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT) if landscape else (WIDTH, HEIGHT)
    base = _cover(last, (width, height))
    shape, soften_w, dim = fit_dim(base)
    if landscape:   # (rows,) -> (rows, columns): the dim and softening concentrate in the text column
        cols = _scrim(width)[None, :]
        shape, soften_w = shape * cols, soften_w * cols
    soft_base = base.filter(ImageFilter.GaussianBlur(14))
    text = brand.HANDOFF_LINE if landscape and handoff else share
    text, lay, seconds, reading = held_layout(text, width, height, credits=credits, room=room)
    mast = lay["masthead"]
    total = round(seconds * FPS)
    ink = np.array(brand.INK, np.float32)

    # The 16:9 field (its dim and softening) moves between two rests on ease_io (freeze lane E cycle 3, panel 4
    # MO-E2: on ease_out its first frame stepped MAD 27 as the narration handed over); the 9:16 card keeps ease_out.
    field = ease_io if landscape else ease

    # freeze cycle 5 (PLATES): the 16:9 dim's duration follows the drop it makes (dim_seconds); 9:16 keeps DIM_IN
    dim_in = DIM_IN
    if landscape:
        lum0 = np.asarray(base.convert("RGB"), np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        ink_l = float(np.dot(ink, (0.2126, 0.7152, 0.0722)))
        dim_drop = float((shape * np.maximum(lum0 - ink_l, 0.0)).mean())
        dim_in = dim_seconds(dim_drop, field)

    matte_im: Image.Image | None = None   # the INK field over a matte (16:9, matte_window), in `base`'s pixels

    def ground(t: float) -> Image.Image:
        z = landscape_zoom_at(t) if landscape else zoom_at(t, seconds)
        im = _pushed(base, z, width, height)
        sw = (field(t / SOFTEN_IN) * soften_w)[..., None]
        im = im * (1 - sw) + _pushed(soft_base, z, width, height) * sw
        if matte_im is not None:   # it moves with the picture (the push) and comes in with the dim
            m = (field(t / dim_in) * np.clip(np.asarray(kenburns(matte_im, (width, height), zoom=z,
                                                                  resample=Image.LANCZOS)), 0, 1))[..., None]
            im = im * (1 - m) + ink * m
        dark = (field(t / dim_in) * shape)[..., None]
        return Image.fromarray((im * (1 - dark) + ink * dark).clip(0, 255).astype(np.uint8))

    # Every line is planned on the card's last frame (the ground at rest) and drawn from that plan on each frame.
    rest = ground((total - 1) / FPS)
    found = matte_window(base) if landscape else None
    if stats is not None and landscape:
        stats["matte"] = found[1] if found else None
    if found is not None:
        mf, feather = matte_field(found[0], rest, width)
        found[1]["feather_px"] = feather
        matte_im = Image.fromarray(mf, "F")
        rest = ground((total - 1) / FPS)
    # The masthead (cycle 0 round 7): planned by the system's planner for a ground it cannot scrim (masthead.plan_on:
    # bone at the least halo, the light variant, the ruling-4 exception; halo.step and colour_light read), then the
    # ONE gate (masthead.verify: room, name, mark) on the ground at rest AND on every frame where the name is fully
    # opaque (sweep r7: an earlier opaque frame read 4.73 while the resting frame recorded 16.60); failing closed.
    from . import masthead
    brand_plate, mast_worst, plan_rec = masthead.plan_on(rest, lay["masthead_xy"], "lt", mast["font"], mast["track"],
                                                         mast["text"])
    # its room: the 9:16 column (the Reel box); on 16:9, from its left edge to END_SCREEN_GAP short of the video slot
    room = (lay["max_width"] if not landscape else
            END_SCREEN_SLOTS["video"][0] - END_SCREEN_GAP - (lay["masthead_xy"][0] - 2 * mast["mark_r"] - mast["gap"]))
    mast_rec: dict[str, Any] = {"shown": True, "contrast": round(mast_worst, 2), "size": mast["size"],
                                "group_width": mast["width"], "room": room, **plan_rec}
    diamond, edge = mark_diamond(lay), lay["masthead"].get("edge", 3)
    if masthead.verify(rest, brand_plate, diamond, edge, mast_rec, judged=mast_worst, width=mast["width"],
                       room=room) is not None:
        worst_frame = None
        for i in range(total):
            t = i / FPS
            if ease((t - MAST_AT) / motion("MAST_IN")) < 1.0:
                continue
            frame_rec: dict[str, Any] = dict(plan_rec)
            if masthead.verify(ground(t), brand_plate, diamond, edge, frame_rec) is None:
                worst_frame = (i, frame_rec)
                break
            mast_rec["contrast_min_frames"] = round(min(mast_rec.get("contrast_min_frames", 99.0),
                                                        frame_rec["contrast"]), 2)
        if worst_frame is not None:
            i, frame_rec = worst_frame
            mast_rec.update(shown=False, reason=f"{frame_rec['reason']} (on frame {i}, fully opaque)")
    mast_shown = mast_rec["shown"]
    if not mast_shown:
        log.warning("End card: %s", mast_rec["reason"])
    words = _word_xs(lay, width)
    word_plates = [plan_text(rest, (round(x), y), w, lay["share_font"], brand.BONE) for _, _, w, x, y in words]
    follow_plate = None
    extras = end_screen_extras(width, lay) if landscape else None
    kicker_plate = credits_plate = None
    # The end screen's overlay labels (the follow line, the WATCH NEXT kicker) are ONE set (cycle 0 round 8 R1):
    # planned together by the one overlay-label rule (operator ruling 2) on one treatment, typography.plan_label_set
    # (each its own only when no one step holds on both grounds: the gate is never traded).
    from .brand_system import GATES as _K_GATES
    from .multi_format import finish_kicker, kicker_label
    from .typography import plan_label_set
    members, names = [], []
    if lay["follow"]:
        members.append({"ground": rest, "rows": [((lay["centre_x"], lay["follow_y"]), brand.FOLLOW_LINE,
                                                   lay["follow_font"], "ma", lay["spacing"])],
                        "colours": (brand.GOLD, brand.BONE), "target": tok(LINE_GATES["follow"])})
        names.append("follow")
    if extras:
        k = extras["kicker"]
        k_gate = _K_GATES["contrast.kicker"]   # round 7 (F20, sweep): the one kicker gate, as plan_kicker's
        members.append(kicker_label(rest, k["text_xy"], k["text"], k["font"], colour_of("kicker.colour"), "ls",
                                    k["track"], k["rule"], k_gate))
        names.append("kicker")
    planned_set, set_rec = plan_label_set(members) if members else ([], None)
    for name, (planned, rec) in zip(names, planned_set):
        if name == "follow":
            follow_plate = planned[0] if planned else None
            if stats is not None:
                stats["follow_gate"] = rec
        else:
            kicker_plate, kicker_rec = finish_kicker(planned, rec, k["rule"], k_gate, k_gate, k["text"])
            if stats is not None:
                stats["kicker_gate"] = kicker_rec
    if stats is not None and set_rec is not None:
        stats["label_set"] = set_rec
    if extras:
        if credits:
            c = extras["credits"]
            credits_plate = plan_text(rest, c["xy"], c["text"], c["font"], brand.STONE_LT, "ls", c["track"])
    refused_lines = _gate_lines(ground, total, width, height, landscape, word_plates, follow_plate, credits_plate)
    if "share" in refused_lines:
        word_plates = []
    if "follow" in refused_lines:
        follow_plate = None
    if "credits" in refused_lines:
        credits_plate = None
    done = share_done(len(word_plates))   # the last word has risen
    if stats is not None:
        stats.update(dim_in=round(dim_in, 3))
        stats.update(dim=dim, box=[round(v, 1) for v in lay["box"]], share_lines=[" ".join(ln) for ln in lay["lines"]],
                     share_size=lay["share_font"].size, follow_size=lay["follow_font"].size if lay["follow"] else None,
                     follow=lay["follow"], safe_width=lay["max_width"], masthead_width=mast["width"],
                     size=[width, height], seconds=seconds, masthead_top=lay["masthead_top"], text=text,
                     masthead=mast_rec, refused_lines=refused_lines, reading=reading)
        if landscape:
            stats.update(kicker=KICKER, slot_frames=bool(slot_frames), credits_line=bool(credits),
                         push=LANDSCAPE_PUSH, handoff=text == brand.HANDOFF_LINE,
                         layout_problems=end_screen_problems(lay, width, credits=credits))
    # the reveal's motion tokens, read once per card when it is drawn (SYS-E-1)
    mast_in, stagger, word_in = motion("MAST_IN"), motion("WORD_STAGGER"), motion("WORD_IN")
    share_rise, follow_in, follow_rise = motion("SHARE_RISE"), motion("FOLLOW_IN"), motion("FOLLOW_RISE")
    for i in range(total):
        t = i / FPS
        frame = ground(t)
        a = ease((t - MAST_AT) / mast_in)
        if mast_shown:
            frame = brand_plate.draw(frame, a)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        _mark(d, lay, a if mast_shown else 0.0, ease((t - RULE_AT) / RULE_IN))
        if extras and slot_frames:   # hairline reservations: fade in after the share line, then hold still
            fa = int(255 * ease((t - done - 0.1) / 0.5))
            if fa > 0:
                d.rectangle(extras["frames"]["video"], outline=(*brand.GOLD_DIM, fa), width=1)
                d.rectangle(extras["frames"]["video2"], outline=(*brand.GOLD_DIM, fa), width=1)
                d.ellipse(extras["frames"]["subscribe"], outline=(*brand.GOLD_DIM, fa), width=1)
        frame = Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")
        for k, plate in enumerate(word_plates):   # the share line rises word by word
            wa = ease((t - SHARE_AT - stagger * k) / word_in)
            if wa > 0:
                frame = plate.draw(frame, wa, round(share_rise * (1 - wa)))
        if follow_plate is not None:
            fa = ease((t - (BELL_AT - follow_in)) / follow_in)   # the follow line lands with the bell
            if fa > 0:
                frame = follow_plate.draw(frame, fa, round(follow_rise * (1 - fa)))
        if kicker_plate is not None:   # it leads the line: in with the gold rule, before the line's first word
            ka = ease((t - RULE_AT) / mast_in)
            if ka > 0:
                rise = kicker_rise(ka)
                frame = kicker_plate.draw(frame, ka, rise)
                x0r, y0r, x1r, y1r = extras["kicker"]["rule"]
                rl = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                ImageDraw.Draw(rl).rectangle([x0r, y0r + rise, x1r, y1r + rise],
                                             fill=(*kicker_plate.fill, int(255 * ka)))
                frame = Image.alpha_composite(frame.convert("RGBA"), rl).convert("RGB")
        if credits_plate is not None:
            ca = ease((t - done - 0.3) / 0.5)
            if ca > 0:
                frame = credits_plate.draw(frame, ca)
        if stats is not None and i == total - 1:
            measured = {"share": min((v for v in (p.measure(frame) for p in word_plates) if v is not None), default=None)}
            if mast_shown:   # an undrawn masthead (fail closed) has no ratio to measure; its record says why
                measured["brand"] = brand_plate.measure(frame)
            if follow_plate is not None:
                measured["follow"] = follow_plate.measure(frame)
            stats["contrast"] = measured   # the YouTube extras are kept apart (their own keys)
            if kicker_plate is not None:
                stats["contrast_kicker"] = kicker_plate.measure(frame)
            if credits_plate is not None:
                stats["contrast_credits"] = credits_plate.measure(frame)
        yield frame


LINE_GATES = {"share": "contrast.text", "follow": "contrast.kicker", "credits": "contrast.text"}


def _line_room(width: int, height: int, landscape: bool) -> tuple[list, list]:
    """(the boxes a line must lie inside, the boxes it must not touch): 9:16, the Reel column (the organic Reel box,
    reel_column); 16:9, the frame, clear of YouTube's element slots and the timestamp corner."""
    if not landscape:
        cx, w = reel_column(width, height)
        return [(cx - w / 2, 0, cx + w / 2, height)], []
    return [(0, 0, width, height)], [*END_SCREEN_SLOTS.values(), YOUTUBE_TIMESTAMP_ZONE]


def _gate_lines(ground, total: int, width: int, height: int, landscape: bool, word_plates, follow_plate,
                credits_plate) -> dict[str, str]:
    """Cycle 0 round 7 (sweep: share text drawn at 4.32 and the follow line at 3.54 without refusal; a longer follow
    or hand-off line ran out of the Reel box or into the video slot): every end-card line passes its gate (LINE_GATES)
    as the system judges text (masthead.judge, the worst glyph) on EVERY frame where it is fully opaque, and lies
    inside its room (_line_room); else it is not drawn and the record says why. {line: reason} for those refused."""
    from . import masthead
    inside, clear = _line_room(width, height, landscape)
    done = share_done(len(word_plates))
    word_in, follow_in = motion("WORD_IN"), motion("FOLLOW_IN")
    lines = {"share": (word_plates, word_at, word_in),
             "follow": ([follow_plate] if follow_plate is not None else [], lambda k: BELL_AT - follow_in, follow_in),
             "credits": ([credits_plate] if credits_plate is not None else [], lambda k: done + 0.3, 0.5)}
    refused: dict[str, str] = {}
    judged: list[tuple[str, int, Any]] = []   # (line, first fully opaque frame, plate)
    for name, (plates, start, dur) in lines.items():
        plates = [p for p in plates if p is not None and not p.empty]
        if not plates:
            continue
        for p in plates:
            x0, y0, x1, y1 = p.box
            if not any(a <= x0 and b <= y0 and x1 <= c and y1 <= d for a, b, c, d in inside) or any(
                    x0 < c and a < x1 and y0 < d and b < y1 for a, b, c, d in clear):
                refused[name] = f"{name} line not drawn: '{p.text}' ink {p.box} runs out of its room"
                break
        if name not in refused:   # each plate from when it is fully opaque (frames()'s own timing) to the card's end
            judged += [(name, max(0, int(np.ceil((start(k) + dur) * FPS))), p) for k, p in enumerate(plates)]
    # every frame's ground is drawn once and every line opaque on it judged there (the card holds longer since lane E
    # cycle 1: a ground per plate per frame cost the 9:16 card ~10x its frames)
    first = min((i0 for _, i0, _ in judged), default=total)
    for i in range(first, total):
        live = [(name, p) for name, i0, p in judged if i0 <= i and name not in refused]
        if not live:
            continue
        g = ground(i / FPS)
        for name, p in live:
            if name in refused:
                continue
            gate = tok(LINE_GATES[name])
            got = masthead.judge(g, p)
            if got < gate:
                refused[name] = (f"{name} line not drawn: '{p.text}' reaches {got:.2f}:1 on frame {i}, under the "
                                 f"{gate}:1 gate")
    refused = {name: refused[name] for name in lines if name in refused}
    for name, why in refused.items():
        log.warning("End card: %s", why)
    return refused


def bed_tail(reel: bool) -> float:
    """Seconds the music bed runs past the narration: a Reel's loop bridge plus 50 ms of headroom (the full card,
    SECONDS, until 2026-11-03), else the half second a 16:9 master keeps."""
    return LOOP_SECONDS + 0.05 if reel else 0.5


def loop_text(share: str) -> str:
    """The loop bridge's one line: the share half only (it never asks for a follow)."""
    return card_text(share) or " ".join((share or "").split())


def _smoother(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)


def _seam_curve(n: int, last: float) -> Callable[[float], float]:
    """The crossfade's mix over its progress u (0 at its start, 1 at the Reel's frame 0): a cubic Hermite that
    starts at rest (no step into the crossfade) and ends still moving, its end slope solved so that the bridge's
    last frame (u = (n-1)/n) sits at exactly `last` (1 - 1/LOOP_SECONDS/FPS: 29/30). Smootherstep ended at rest,
    which put the last 2-3 frames within a grey level of frame 0: a held frame, then a stall, at every loop."""
    v = (n - 1) / n
    base = 3 * v * v - 2 * v ** 3
    m = (last - base) / (v ** 3 - v * v) if n > 1 else 1.0
    if not 0.0 <= m <= 3.0:   # outside this the cubic overshoots or runs backwards
        raise ValueError(f"no monotone seam curve over {n} frames ending at {last}")

    def mix(u: float) -> float:
        u = max(0.0, min(1.0, u))
        return 3 * u * u - 2 * u ** 3 + m * (u ** 3 - u * u)
    return mix


def _pulled(im: Image.Image, z: float, width: int, height: int) -> np.ndarray:
    """`im` (WIDTHxHEIGHT) zoomed by z about its centre, z <= 1 allowed: the frames *before* frame 0 of a shot that
    pushes in from frame 0. Frame 0 is the widest crop the composer renders, so the few pixels outside it (at most
    ~2 % a side, only where the mix is low) are its own edge mirrored."""
    if z >= 1.0:
        return _pushed(im, z, width, height)
    w, h = im.size
    mx, my = int(np.ceil(w * (1 / z - 1) / 2)) + 4, int(np.ceil(h * (1 / z - 1) / 2)) + 4
    pad = Image.fromarray(np.pad(np.asarray(im.convert("RGB")), ((my, my), (mx, mx), (0, 0)), mode="symmetric"))
    cw, ch = w / z, h / z
    box = (mx + (w - cw) / 2, my + (h - ch) / 2, mx + (w + cw) / 2, my + (h + ch) / 2)
    return np.asarray(kenburns(pad, (width, height), source_box=box, resample=Image.LANCZOS), dtype=np.float32)


def loop_bridge(last: Image.Image, first: Image.Image, share: str,
                stats: dict[str, Any] | None = None, *, line: bool = True, follow: bool = False,
                crossfade: float | None = None, close_zoom: float = 1.0, close_push: float | None = None,
                open_push: float | None = None) -> Iterator[Image.Image]:
    """The 9:16 Reel's ending (operator, 2026-11-02): a loop bridge of LOOP_SECONDS instead of the end card.

    `last` is the narration's graded last frame and `first` the Reel's real frame 0 as the composer renders it
    (graded, at its push-in start crop, WIDTHxHEIGHT: anything else is refused, since a resized still would not
    be the frame the loop lands on). The last shot keeps its forward drift (below) through the bridge
    while, under the share line only, a light dim (LOOP_DIM of the card's measured dim) comes in on ease_io; the picture
    then crossfades into `first` over
    `crossfade` seconds (LOOP_CROSSFADE: 0.5 with the share line, 0.7 without; 0.5-0.7) on a curve that starts at
    rest and is still moving at the seam (_seam_curve), and the dim lifts on that same curve, so no frame steps
    more than LOOP_MAX_STEP_L in mean L*. The bridge's last frame is at mix 29/30 exactly: one ordinary step short
    of frame 0, so frame 0 plays once when the Reel loops and the fade does not stall on it.
    Motion through the seam: the outgoing shot continues the closing shot's own push, `close_push` (absolute zoom
    per frame at its end) over `close_zoom` (its absolute zoom on `last`), as video_composer.loop_motion gives them
    from the composer's camera (still_camera: a closing still ends moving at LOOP_PUSH; 0 for a clip that moves by
    itself or a held full image: no push it did not have). The incoming
    picture is frame 0 at the opening shot's push run backwards (1 + open_push * (i - 30): wider than frame 0,
    its edge mirrored for the <= 2 % outside it), so it arrives at frame 0 already moving at the opening shot's
    rate and the Reel's frames 1.. carry on from there. `line` (the operator's 2026-11-02 spec): the share line
    rises in bone at a measured contrast, holds and fades out in the first 40% of the crossfade; False makes the bridge purely visual (round-3
    panel recommendation, pending the operator) and records text None. `follow` (QF-83, config.LOOP_FOLLOW_OVERLAY)
    draws LOOP_FOLLOW instead of the share line (never both: one element on the bridge), see LOOP_FOLLOW_SIZE; it
    adds no time. `stats` receives the line (or None), its size and contrast at the hold (with FOLLOW: the lowest
    over its full-opacity frames), the dim, the crossfade and the largest mean-L* step over the seam."""
    from .typography import plan_text
    width, height = WIDTH, HEIGHT
    close_push = motion("LOOP_PUSH") if close_push is None else close_push   # default: the token, read now
    open_push = motion("LOOP_PUSH") if open_push is None else open_push
    if first.size != (width, height):
        raise ValueError(f"loop_bridge needs the Reel's rendered frame 0 at {width}x{height}, not {first.size[0]}x"
                         f"{first.size[1]} (a resized still is not the frame the loop lands on)")
    if close_zoom <= 0 or close_push < 0 or open_push < 0:
        raise ValueError("loop_bridge: close_zoom must be > 0 and the pushes >= 0")
    a_rate = close_push / close_zoom     # relative zoom per frame on `last`, which is already at close_zoom
    lo, hi = motion("LOOP_CROSSFADE")
    cf = float(crossfade if crossfade is not None else (lo if line and not follow else hi))
    cf = max(lo, min(hi, cf))
    a_img = _cover(last, (width, height))
    b_img = first.convert("RGB")
    shape, _, dim = fit_dim(a_img)
    dimmed = bool(line) and not follow   # the dim seats the share line only (LOOP_DIM)
    shape = (shape * (LOOP_DIM if dimmed else 0.0))[..., None]
    ink = np.array(brand.INK, np.float32)
    total = round(LOOP_SECONDS * FPS)
    t_in = LOOP_TIMES["in"]
    n_cf = round(cf * FPS)
    i_out = total - n_cf                 # the crossfade's first frame (mix 0); it runs to the Reel's frame 0
    t_out = i_out / FPS
    t_hold = (t_in + t_out) / 2          # where the line's contrast is measured
    text = loop_text(share) if line and not follow else None
    plates, lay = [], None
    follow_plate, follow_skipped, follow_contrasts = None, None, []
    if follow:
        follow_plate, follow_skipped = _follow_plate(a_img, b_img, a_rate, open_push, total)
        text = LOOP_FOLLOW if follow_plate is not None else None
    elif line:
        lay = layout(text, width, height)
        lines, line_h = lay["lines"], lay["line_h"]
        top = round(LOOP_CENTRE * height - len(lines) * line_h / 2)
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        z = 1 + a_rate * (round(t_hold * FPS) + 1)
        held_arr = _pushed(a_img, z, width, height)
        held = Image.fromarray((held_arr * (1 - shape) + ink * shape).clip(0, 255).astype(np.uint8))
        for li, ln in enumerate(lines):
            s = " ".join(ln)
            x = lay["centre_x"] - probe.textlength(s, font=lay["share_font"]) / 2
            plates.append(plan_text(held, (round(x), top + li * line_h), s, lay["share_font"], brand.BONE))
    mix_at = _seam_curve(n_cf, 1 - 1 / total)
    follow_rise = motion("LOOP_FOLLOW_RISE")
    hold_i = min(range(total), key=lambda k: abs(k / FPS - t_hold))
    prev_l = mean_lstar(a_img)
    steps: list[float] = []
    mix = 0.0
    frame = None
    for i in range(total):
        t = i / FPS                      # progress i/total: frame 0 of the Reel would be total/total
        z = 1 + a_rate * (i + 1)         # the last shot's drift carries on from its last frame
        mix = mix_at((i - i_out) / n_cf) if i >= i_out else 0.0
        dark = ease_io(t / t_in) * (1 - mix) * shape   # a field: from rest, never a full-speed first step
        ground = _pushed(a_img, z, width, height) * (1 - dark) + ink * dark
        if mix > 0:                      # frame 0, (total - i) frames earlier on the opening shot's push
            ground = ground * (1 - mix) + _pulled(b_img, 1 + open_push * (i - total), width, height) * mix
        frame = Image.fromarray(ground.clip(0, 255).astype(np.uint8))
        if plates:
            la = ease(t / t_in) * (1 - ease_io((t - t_out) / (0.4 * cf)))
            for p in plates:
                frame = p.draw(frame, la, round(LOOP_LINE_RISE * (1 - ease(t / t_in))))
        if follow_plate is not None:
            fa = follow_alpha(i, total)
            rise = ease(i / LOOP_FOLLOW_FRAMES[0])
            frame = follow_plate.draw(frame, fa, round(follow_rise * (1 - rise)))
            if fa >= 1.0:   # measured where it is fully up, on the frame as drawn
                follow_contrasts.append(follow_plate.measure(frame))
        if stats is not None:
            cur = mean_lstar(frame)
            steps.append(abs(cur - prev_l))
            prev_l = cur
            if plates and i == hold_i:
                vals = [v for v in (p.measure(frame) for p in plates) if v is not None]
                stats["contrast"] = min(vals) if vals else None
        yield frame
    if stats is not None:
        steps.append(abs(mean_lstar(first) - prev_l))   # the bridge's last frame -> the Reel's frame 0
        stats["mad_last"] = round(float(np.abs(np.asarray(frame, np.float32) - np.asarray(b_img, np.float32)).mean()), 2)
        stats["mad_measure"] = LOOP_SEAM_MEASURE   # what LOOP_MAX_MAD gates (see there)
        stats.update(text=text, lines=[" ".join(ln) for ln in lay["lines"]] if lay else [],
                     share_size=lay["share_font"].size if lay else None, dim=dim, seconds=LOOP_SECONDS,
                     follow=follow_plate is not None, line=bool(line), crossfade=round(cf, 3), loop_dim=LOOP_DIM if dimmed else 0.0, push=close_push,
                     close_zoom=close_zoom, open_push=open_push, mix_last=round(mix, 4),
                     max_step_L=round(max(steps), 2))
        stats.setdefault("contrast", None)
        # the bridge's one element against the reading pace (recorded, never failed: the bridge's second is the
        # operator's, LOOP_SECONDS; a line over the pace is a copy matter). Shown: from its first frame up to the
        # start of its fade (the share line: the crossfade's start; FOLLOW: its last full-opacity frame).
        shown = None
        if text is not None and follow_plate is not None:
            up = [i for i in range(total) if follow_alpha(i, total) >= 1.0]
            shown = (up[-1] + 1) / FPS
        elif text is not None:
            shown = t_out
        if shown is not None:
            need = reading_need([(0.0, text)])
            stats["reading"] = {"target_cps": float(tok("motion.caption_cps")), "need_s": round(need, 3),
                                "shown_s": round(shown, 3), "over_pace": need > shown + 1e-9,
                                "shown_cps": round(len(" ".join(text.split())) / shown, 1)}
        if follow:
            vals = [v for v in follow_contrasts if v is not None]
            stats.update(follow_requested=True, follow_skipped=follow_skipped,
                         contrast=(min(vals) if vals else 0.0) if follow_plate is not None else None,
                         follow_frames=len(follow_contrasts),
                         follow_box=list(follow_plate.box) if follow_plate is not None else None,
                         follow_size=LOOP_FOLLOW_SIZE if follow_plate is not None else None,
                         follow_halo=follow_plate.halo if follow_plate is not None else None,
                         follow_variant=getattr(follow_plate, "variant", None) if follow_plate is not None else None,
                         follow_plate=(follow_plate.backing.record() if getattr(follow_plate, "backing", None) is not None
                                       else None))


def follow_alpha(i: int, total: int) -> float:
    """The FOLLOW's opacity on the bridge's frame i of total: 0 on its first frame, up after LOOP_FOLLOW_FRAMES[0],
    then out over its last LOOP_FOLLOW_FRAMES[1] frames to 0 on its last (the seam frame stays frame 0's)."""
    n_in, n_out = LOOP_FOLLOW_FRAMES
    return ease_io(i / n_in) * (1 - ease_io((i - (total - 1 - n_out)) / n_out))


def _follow_plate(a_img: Image.Image, b_img: Image.Image, a_rate: float, open_push: float, total: int):
    """The bridge's FOLLOW plate (typography.plan_label), planned on the brightest picture it can sit on: per pixel,
    the lightest of the outgoing shot (undimmed) and the incoming frame 0, each at the first and the last frame it is
    fully up, and its INK variant on the darkest. Every frame under it is a mix of those two (a bridge with FOLLOW
    is not dimmed: LOOP_DIM), so the variant holds on all of them; or (None, why) when no variant reaches the placement
    target there
    (the bridge then ships without it). Solved on the two endpoint frames only, 3 of 31 production endings measured
    under 4.5:1 mid-crossfade (QF-83 real_data)."""
    from .typography import cap_height, drawable, plan_label
    width, height = a_img.size
    font = get_font(brand.TEXT_FONT, LOOP_FOLLOW_SIZE, weight=800)
    spacing = LOOP_FOLLOW_SIZE * FOLLOW_TRACK
    y = round(LOOP_CENTRE * height + cap_height(font) / 2)        # its baseline: the caps centred on LOOP_CENTRE
    xy = (reel_column(width, height)[0], y)
    up = [i for i in range(total) if follow_alpha(i, total) >= 1.0]
    outgoing = [_pushed(a_img, 1 + a_rate * (i + 1), width, height) for i in (up[0], up[-1])]
    incoming = [_pulled(b_img, 1 + open_push * (i - total), width, height) for i in (up[0], up[-1])]
    lightest = Image.fromarray(np.maximum.reduce(outgoing + incoming).clip(0, 255).astype(np.uint8))
    # Operator ruling 2 (2026-11-17; panel F15 / MO-2: a 0.47-deep halo cloud held still while the ground crossfaded
    # under it): the ONE overlay-label rule, typography.plan_label. Bone on the label halo (one spread, never above
    # tok("halo.label_max")) judged on the LIGHTEST picture under it; else INK type judged on the DARKEST (the outgoing
    # shot and the incoming frame 0, undimmed: the bridge with FOLLOW draws no dim, lane E cycle 3); else a held INK
    # plate behind the word, which loop_bridge fades and raises on the FOLLOW's own alpha curve (TextPlate.draw draws
    # it with the word). The halo exception (a 40 px spread up to 1.0) is retired. Every frame under it is a mix of
    # those two grounds.
    if not drawable(LOOP_FOLLOW, font).strip():
        return None, "the word did not render"
    darkest = Image.fromarray(np.minimum.reduce(outgoing + incoming).clip(0, 255).astype(np.uint8))
    plates, rec = plan_label(lightest, [(xy, LOOP_FOLLOW, font, "ms", spacing)], (brand.BONE,),
                             target=max(LOOP_FOLLOW_MIN, brand.PLACE_TARGET), dark_ground=darkest)
    if not rec["shown"]:
        return None, rec["reason"]
    return plates[0], None


def seam_problems(stats: dict[str, Any]) -> list[str]:
    """Why a rendered loop bridge would hitch at the seam, from loop_bridge's own stats: a step over LOOP_MAX_STEP_L
    in mean L*, a last frame LOOP_MAX_MAD or further from frame 0, a last frame that is not one step short of it
    (mix 29/30), or a FOLLOW (QF-83) measured under LOOP_FOLLOW_MIN on a full-opacity frame. Empty when clean."""
    problems = []
    if stats.get("follow") and (stats.get("contrast") or 0.0) < LOOP_FOLLOW_MIN:
        problems.append(f"its FOLLOW measures {stats.get('contrast')}:1 (at least {LOOP_FOLLOW_MIN}:1)")
    step = stats.get("max_step_L")
    if step is None or step > LOOP_MAX_STEP_L:
        problems.append(f"a frame steps {step} L* at the seam (at most {LOOP_MAX_STEP_L})")
    mad = stats.get("mad_last")
    if mad is None or mad >= LOOP_MAX_MAD:
        problems.append(f"the last frame is {mad} from frame 0 (under {LOOP_MAX_MAD})")
    total = round(LOOP_SECONDS * FPS)
    mix = stats.get("mix_last")
    if mix is None or abs(mix - (1 - 1 / total)) > 1e-3:
        problems.append(f"the last frame's mix is {mix}, not {total - 1}/{total}")
    return problems


def check_loop_bridge(last: Image.Image, first: Image.Image, share: str, **kw: Any) -> dict[str, Any]:
    """The render-time gate on a loop bridge (panel round 4): renders it once, frames discarded as they come so
    memory stays at one frame, and reads its own stats. {"ok", "problems", "stats", "note"}: when not ok the
    composer retries the bridge without its push and on its longest crossfade, and when that fails too it ends the
    Reel at the narration with no bridge (its music bed is sized to the bridge, so the plain end
    card cannot follow instead, as it did until 2026-11-03), and `note` is the review card's QA line saying why. A
    frame 0 of the wrong size is a problem too (never resized to fit)."""
    stats: dict[str, Any] = {}
    try:
        for _ in loop_bridge(last, first, share, stats, **kw):
            pass
        problems = seam_problems(stats)
    except ValueError as exc:
        problems = [str(exc)]
    note = None
    if problems:
        note = {"type": "end_card", "card": "End card", "severity": "warning",
                "issue": "The loop bridge would hitch at the seam (" + "; ".join(problems)
                         + "), so the Reel ends at the narration without it (the plain end card no longer follows a "
                           "Reel: the music bed is sized to the bridge)."}
    return {"ok": not problems, "problems": problems, "stats": stats, "note": note}
