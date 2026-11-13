"""Objective picture checks on a delivered master, for the release gate (QF-10).

One FFmpeg decode reads every frame of the master. Each frame is sampled on a regular grid (every STRIDE-th pixel
of every STRIDE-th line, nearest neighbour: the sampled values are the decoded values, nothing is filtered or
averaged) and handed to the analysers, which keep their own state across frames. The decode is the cost; the
analysers add little to it. The grid keeps 1080-line masters at 270 x 480 (Reel) or 480 x 270 (16:9) samples.

Each analyser reports one release check: picture_black, picture_freeze, picture_bars, picture_range and (QF-88,
METHOD_VERSION 2) picture_flash, the WCAG 2.3.1 photosensitivity check. The colour
tags (picture_colour) come from ffprobe. A check that could not be measured is never reported as a pass: any decode
error, timeout, missing frame or changed file raises, and the gate records picture_measurement: fail.

Extension point: an analyser is any object with a `name`, `frame(index, time, y, u, v)` and
`check(duration) -> (intervals, sentence)`; `analysers()` lists them, so a per-frame gate such as QF-88's
photosensitivity check joins the same decode instead of adding a second one (bump METHOD_VERSION with it).
"""

import json
import math
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter, uniform_filter

from . import config
from .credits import sha256_file

# Bumped whenever an analyser's definition changes, so a measurement recorded by an older definition is redone.
METHOD_VERSION = 2
SAMPLE_LINES = 270        # the grid keeps at least this many samples across the short side


def method() -> dict:
    """What a measurement depends on besides the master's bytes: a recorded result is reused only on an equal one."""
    return {"version": METHOD_VERSION, "sample_lines": SAMPLE_LINES,
            **{name: getattr(config, name) for name in sorted(dir(config)) if name.startswith("PICTURE_QA_")
               and name not in ("PICTURE_QA_THREADS", "PICTURE_QA_TIMEOUT_BASE_S", "PICTURE_QA_TIMEOUT_PER_S")}}


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _spans(intervals: list) -> str:
    shown = ", ".join(f"{_clock(i['start'])}-{_clock(i['end'])}" for i in intervals[:4])
    return shown + (f" and {len(intervals) - 4} more" if len(intervals) > 4 else "")


class _Runs:
    """Intervals over which a per-frame condition holds for at least `floor` seconds. A run is open from the first
    frame where it holds to the first frame where it stops (or the end of the programme)."""

    def __init__(self, floor: float):
        self.floor, self.start, self.intervals = floor, None, []

    def frame(self, time_s: float, holds: bool):
        if holds and self.start is None:
            self.start = time_s
        elif not holds and self.start is not None:
            self._close(time_s)

    def _close(self, end: float):
        if end - self.start >= self.floor - 1e-6:
            self.intervals.append({"start": round(self.start, 3), "end": round(end, 3)})
        self.start = None

    def finish(self, duration: float) -> list:
        if self.start is not None:
            self._close(duration)
        return self.intervals


class Black:
    """A frame is black when >= 98% of its luma samples are <= 20 (ffmpeg blackdetect pix_th=0.02 of the 16-235
    range, pic_th=0.98); PICTURE_QA_BLACK_S of them in a row fail."""
    name = "picture_black"
    level = 16 + math.floor(0.02 * (235 - 16))

    def __init__(self, deep_dive: bool):
        self.runs = _Runs(config.PICTURE_QA_BLACK_S)

    def frame(self, index, time_s, y, u, v):
        self.runs.frame(time_s, np.count_nonzero(y <= self.level) >= 0.98 * y.size)

    def check(self, duration):
        found = self.runs.finish(duration)
        return (found, f"The picture goes black at {_spans(found)}: check the cut or transition there and "
                       "re-render." if found else "No black picture.")


class Freeze:
    """ffmpeg freezedetect's rule: each frame is compared with the frame its still began on (mean absolute
    difference over all samples, as a fraction of 255); below -60 dB (0.001) it is the same picture. A still held
    longer than the format's longest permitted hold fails."""
    name = "picture_freeze"
    noise = 10 ** (-60 / 20)

    def __init__(self, deep_dive: bool):
        self.hold = config.PICTURE_QA_FREEZE_LONG_S if deep_dive else config.PICTURE_QA_FREEZE_REEL_S
        self.runs, self.reference = _Runs(self.hold), None

    def frame(self, index, time_s, y, u, v):
        planes = [p.astype(np.int16) for p in (y, u, v)]
        if self.reference is None:
            self.reference, self.reference_time = planes, time_s
            return
        size = sum(p.size for p in planes)
        mafd = sum(int(np.abs(p - r).sum()) for p, r in zip(planes, self.reference)) / size / 255
        if mafd <= self.noise:
            if self.runs.start is None:
                self.runs.start = self.reference_time
        else:
            self.runs.frame(time_s, False)
            self.reference, self.reference_time = planes, time_s

    def check(self, duration):
        found = self.runs.finish(duration)
        return (found, f"The picture stands still for longer than {self.hold:.0f} s at {_spans(found)}: shorten "
                       "that shot or give it motion, then re-render." if found
                else f"No picture held longer than {self.hold:.1f} s.")


class Bars:
    """Letterbox or pillarbox bars: equal bands at both edges of an axis (each >= PICTURE_QA_BAR_FRACTION of the
    side, within 1% of each other) of padding lines, the same size throughout, for PICTURE_QA_BAR_S. A padding line
    is flat code-16 black, as padding is: no sample above luma 16 + PICTURE_QA_BAR_LEVEL and at least
    PICTURE_QA_BAR_FLAT_SHARE of its samples exactly 16 (an encoded pad is all 16 away from the picture edge). A
    house graphic's dark ground is neither: grain and vignette spread it over several codes in every line
    (Wieliczka's depth graphic: 16-27, mean 19, 5% of samples at 16), so a dark graphic with content in its middle
    is not mistaken for bars."""
    name = "picture_bars"

    def __init__(self, deep_dive: bool, exempt=()):
        self.runs, self.geometry = _Runs(config.PICTURE_QA_BAR_S), None
        self.level = 16 + config.PICTURE_QA_BAR_LEVEL
        # Essential images shown whole (QF-52 fit_filter): their black padding is the design, read from the
        # composition record (image_presentation.full_image_spans), never inferred from the pixels.
        self.exempt, self.skipped = [tuple(span) for span in exempt], False

    @staticmethod
    def _edges(flat: np.ndarray) -> tuple:
        if flat.all():
            return 0, 0      # a wholly black frame is picture_black's
        return int(np.argmin(flat)), int(np.argmin(flat[::-1]))

    def _bars(self, y) -> tuple | None:
        h, w = y.shape
        share = config.PICTURE_QA_BAR_FLAT_SHARE
        black = y == 16
        top, bottom = self._edges((y.max(axis=1) <= self.level) & (black.mean(axis=1) >= share))
        left, right = self._edges((y.max(axis=0) <= self.level) & (black.mean(axis=0) >= share))
        found = []
        for a, b, side, axis in ((top, bottom, h, "letterbox"), (left, right, w, "pillarbox")):
            if min(a, b) >= config.PICTURE_QA_BAR_FRACTION * side and abs(a - b) <= 0.01 * side + 1:
                found.append((axis, a, b, side))
        return tuple(found) or None

    def _same(self, bars) -> bool:
        return (len(bars) == len(self.geometry) and
                all(p[0] == q[0] and abs(p[1] - q[1]) <= 0.01 * p[3] + 1 and abs(p[2] - q[2]) <= 0.01 * p[3] + 1
                    for p, q in zip(bars, self.geometry)))

    def frame(self, index, time_s, y, u, v):
        if any(a - 1e-6 <= time_s < b for a, b in self.exempt):
            self.skipped = True
            bars = None
        else:
            bars = self._bars(y)
        if self.geometry is not None and not (bars and self._same(bars)):
            self.runs.frame(time_s, False)
            self.geometry = None
        if bars and self.geometry is None:
            self.geometry = bars
            self.runs.frame(time_s, True)

    def check(self, duration):
        found = self.runs.finish(duration)
        spans = _spans([{"start": a, "end": b} for a, b in self.exempt])
        whole = (f" (not checked over {spans}, where an essential photo is shown whole: its black border is by "
                 "design)") if self.skipped else ""
        return (found, f"Black bars at the picture's edges at {_spans(found)}: the picture does not fill the frame "
                       f"there; re-render it filling the frame{whole}." if found
                else f"No letterbox or pillarbox bars{whole}.")


class Range:
    """A picture outside limited range (luma 16-235, chroma 16-240): more than PICTURE_QA_RANGE_FRACTION of any plane
    in out-of-range areas, for PICTURE_QA_RANGE_S, fails (a full-range picture tagged limited puts its blacks and
    whites there). An area counts when a sample and its eight grid neighbours are all beyond the range by more than
    PICTURE_QA_RANGE_TOLERANCE codes. Codec ringing does not count: it under- and overshoots in isolated samples
    beside hard edges, and a fine halftone photo rings at several per cent of its samples, in specks of a few
    pixels."""
    name = "picture_range"

    def __init__(self, deep_dive: bool):
        self.runs, self.worst = _Runs(config.PICTURE_QA_RANGE_S), 0.0
        t = config.PICTURE_QA_RANGE_TOLERANCE
        self.limits = ((16 - t, 235 + t), (16 - t, 240 + t), (16 - t, 240 + t))

    @staticmethod
    def _areas(out: np.ndarray) -> int:
        h, w = out.shape
        if h < 3 or w < 3:
            return int(np.count_nonzero(out))
        core = out[1:-1, 1:-1].copy()
        for dy in range(3):
            for dx in range(3):
                core &= out[dy:dy + h - 2, dx:dx + w - 2]
        return int(np.count_nonzero(core))

    def frame(self, index, time_s, y, u, v):
        fraction = max(self._areas((p < lo) | (p > hi)) / p.size for p, (lo, hi) in zip((y, u, v), self.limits))
        self.worst = max(self.worst, fraction)
        self.runs.frame(time_s, fraction > config.PICTURE_QA_RANGE_FRACTION)

    def check(self, duration):
        found = self.runs.finish(duration)
        return (found, f"Part of the picture is darker or brighter than video allows at {_spans(found)} (a "
                       "full-range picture tagged as limited range): re-render it." if found else
                f"Within limited range (worst frame {self.worst:.2%} of the picture in out-of-range areas, allowed "
                f"{config.PICTURE_QA_RANGE_FRACTION:.0%}).")


def _rgb_luts() -> tuple:
    """Linear R, G, B of every (Y, Cb >> 2, Cr >> 2): BT.709 limited range to R'G'B', then the sRGB transfer function,
    as WCAG's relative luminance defines it (chroma in steps of 4 codes: under 0.4% of luminance)."""
    y = (np.arange(256, dtype=np.float32)[:, None, None] - 16) / 219
    c = (np.arange(64, dtype=np.float32) * 4 + 1.5 - 128) / 224
    cb, cr = c[None, :, None], c[None, None, :]
    out = []
    for gamma in (y + 1.5748 * cr + 0 * cb, y - 0.187324 * cb - 0.468124 * cr, y + 1.8556 * cb + 0 * cr):
        g = np.clip(gamma, 0.0, 1.0)
        out.append(np.where(g <= 0.04045, g / 12.92, ((g + 0.055) / 1.055) ** 2.4).astype(np.float32).ravel())
    return tuple(out)


_LUTS: list = []


def _linear_rgb(y, u, v) -> tuple:
    """Linear R, G, B (0-1) through _rgb_luts: of the flash grid when `y` has twice the chroma's size (chroma
    repeated to it), else of the chroma grid (`y` already sampled to it)."""
    if not _LUTS:
        _LUTS.extend(_rgb_luts())
    chroma = (u >> 2).astype(np.int32) * 64 + (v >> 2)
    if y.shape != u.shape:
        chroma = np.repeat(np.repeat(chroma, 2, 0), 2, 1)
    index = y.astype(np.int32) * 4096 + chroma
    return tuple(lut[index] for lut in _LUTS)


class _Swings:
    """Per-sample luminance transitions: a change of at least `step` from the extreme the sample reached since its
    last transition, in the other direction, where the darker state is below `dark` (hysteresis, so a slow fade is
    one transition and noise none). Returns the samples rising and those falling on this frame."""

    def __init__(self, step: float, dark: float):
        self.step, self.dark, self.ext = step, dark, None

    def frame(self, value):
        if self.ext is None:
            self.ext, self.lo, self.hi = value.copy(), value.copy(), value.copy()
            self.dir = np.zeros(value.shape, np.int8)
            none = np.zeros(value.shape, bool)
            return none, none
        d = self.dir
        low = np.where(d == -1, self.ext, self.lo)
        high = np.where(d == 1, self.ext, self.hi)
        up = (d != 1) & (value - low >= self.step) & (low < self.dark)
        down = (d != -1) & (high - value >= self.step) & (value < self.dark) & ~up
        further = ((d == 1) & (value > self.ext)) | ((d == -1) & (value < self.ext))
        self.ext = np.where(up | down | further, value, self.ext)
        self.dir = np.where(up, 1, np.where(down, -1, d)).astype(np.int8)
        self.lo, self.hi = np.minimum(self.lo, value), np.maximum(self.hi, value)
        return up, down


class _RedSwings:
    """Per-sample red transitions (WCAG 2.2, note 3 to "general flash and red flash thresholds"): a change of more
    than `step` in CIE 1976 UCS chromaticity (u', v') from the state of the sample's last transition, where either
    state is a saturated red (R / (R + G + B) >= `share`, linear R, G, B). A sample darker than NEAR_BLACK has no
    measurable chromaticity (codec noise): it is given the white point's, so black to red is a transition and noise
    in black is not."""
    NEAR_BLACK = 0.002
    WHITE = (0.1978, 0.4683)          # D65 in u', v'

    def __init__(self, step: float, share: float):
        self.step, self.share, self.anchor = step, share, None

    @classmethod
    def _uv(cls, r, g, b):
        x = 0.4124 * r + 0.3576 * g + 0.1805 * b
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = 0.0193 * r + 0.1192 * g + 0.9505 * b
        lit = y >= cls.NEAR_BLACK
        den = np.where(lit, x + 15 * y + 3 * z, 1.0)
        return np.where(lit, 4 * x / den, cls.WHITE[0]), np.where(lit, 9 * y / den, cls.WHITE[1]), lit

    def frame(self, r, g, b):
        """Only samples that are red now or were red at their last transition can make one, so u'v' is computed
        for those alone (the first frame sets every sample's anchor)."""
        red = (r >= self.share * (r + g + b)) & (0.2126 * r + 0.7152 * g + 0.0722 * b >= self.NEAR_BLACK)
        if self.anchor is None:
            uu, vv, _ = self._uv(r, g, b)
            self.anchor = [uu, vv, red]
            none = np.zeros(r.shape, bool)
            return none, none
        au, av, ared = self.anchor
        moved = np.zeros(r.shape, bool)
        at = np.flatnonzero(red | ared)
        if at.size:
            uu, vv, _ = self._uv(r.flat[at], g.flat[at], b.flat[at])
            go = np.hypot(uu - au.flat[at], vv - av.flat[at]) > self.step
            hit = at[go]
            moved.flat[hit] = True
            au.flat[hit], av.flat[hit], ared.flat[hit] = uu[go], vv[go], red.flat[hit]
        return moved & red, moved & ~red          # turning red, turning from red


class _FlashStage:
    """Flash counting on one grid. Every 10-degree field, at every position on the frame (exact sliding windows over
    integral images), keeps the direction of its last transition. On a frame where more than PICTURE_QA_FLASH_AREA
    of its samples rise (or fall), it makes a transition only if that is opposite to its last one: WCAG note 2 counts
    transitions between adjacent peaks and valleys, so a second rise after a rise (a change spread over frames, a
    fast fade, two areas falling a frame apart) adds none. A frame on which more than that share rises and more than
    that share falls (a counter-phase pattern: each part reverses) is one transition.

    A field's count over the last second is the most transitions n such that either
    - the field made n transitions that always count (s of them): single-direction ones (more than that share
      rising, or falling, not both), and counter-phase ones whose rise or fall is coarse (more than that share of the
      field changing by majority over boxes PICTURE_QA_FLASH_COARSE of its shorter side, about 1 degree: a band, a
      block, a cut between pictures). WCAG's "combined area of flashes occurring concurrently" adds up whatever
      parts of the field change together, so these count whichever pixels make them (bands flashing in turn, a
      strobe moving across the field, a bright band jumping from place to place); or
    - the field made n transitions counting its fine counter-phase frames as well, and more than that share of it is
      samples that themselves made n (a pattern flashing in place); or
    - s plus the field's fine counter-phase frames on which more than that share of it rose (or fell) coarsely or
      by samples that themselves made n is at least n: a fine pattern flashing in place on the same frames as a
      coarse change, each under that share, together over it; or
    - the field made n counting its fine counter-phase frames, and more than that share of it is samples that made
      the remaining n - s, and at least PICTURE_QA_FLASH_IN_PLACE, themselves on such frames: a fine pattern
      flashing in place beside flashes that always count.
    Only fine counter-phase frames are excused: motion through a detailed picture (a whip pan) keeps a field a third
    rising and a third falling in fine detail frame after frame, which adds up to flashes no part of the picture
    makes. More than 2 x PICTURE_QA_FLASH_PER_S fail (more than 3 flashes, each a pair of opposing transitions).

    Held, not failed (operator decision A after QF-88 round 5): a field whose count exceeds that only by the last
    condition with fewer in-place transitions (1 to PICTURE_QA_FLASH_IN_PLACE - 1). One fine counter-phase frame
    after s = 6 that always count meets it both when a fine pattern reverses in place (3.5 flashes) and when a whip
    moves over detail (no area flashes 3.5 times); only a motion test could tell them apart, so the operator looks
    at it. The same holds for the third condition reached with fewer than PICTURE_QA_FLASH_IN_PLACE such frames
    (round 7): one or two of them after five or six that always count are what a whip over gilded detail into a
    flat card gives too (a production-sample Amber Room master), so they are held; with that many the pattern
    flashed in place at the limit by itself, and the count fails.

    The fine stage's second reading (`dilate`, QF-88 round 7). Each fine-stage sample is a 0.1-degree box at
    0.05-degree steps, and a box straddling the edge of a square that reverses averages half rising and half
    falling: no change. That lost about 21% of a 0.44-degree checker's area, and a checker over 52-60% of a field
    reversing 8 times a second read as under the area share. The second reading counts a sample as rising (falling)
    where it does, or where a box within one step of it rises (falls) as part of a solid change, at least 2 x 2
    boxes (_solid, a binary opening). A square over 0.1 degree, whose inside is such a change, is then counted whole.
    A pattern under 0.1 degree stays exempt: its boxes average to a near-steady mean, and the few that still swing
    (a 0.04-degree checker's moire: 12% of the field) are scattered, not solid; reaching out from them as well gave
    49% and failed it. Squares of exactly 0.1 degree, which leave only single boxes changing, are the first
    reading's (they fail there over a whole field, as before). The reach decides only what the edge loss hides:
    - a frame is counter-phase where more than the area share rises and falls with the reach; a single-direction
      transition still needs that share of the boxes themselves (reached in every direction, every changing box of
      a whip over detail grows by a step on each side, and whips into a card read as 7 single-direction
      transitions: dev Richat and Roman masters, a production-sample Amber Room);
    - whether a change is coarse is judged on the boxes themselves, so fine detail never reads as a band;
    - a sample's own transitions over the second are its box's own or, if more, the most that solid change within
      one step of it made (_own; never the sum, so an edge moving across neighbouring boxes is not one box flashing
      in place), and a fine frame's change takes the reach (a band beside a reversing checker counts the checker
      whole).
    Flash runs both readings; either one's fail fails and either one's hold holds, so the second never lets pass
    what the first fails."""

    @staticmethod
    def _solid(mask):
        """The samples of `mask` inside some 2 x 2 block of it (a binary opening by 2 x 2): the second reading
        reaches out only from changes at least that wide."""
        core = mask[:-1, :-1] & mask[1:, :-1] & mask[:-1, 1:] & mask[1:, 1:]
        out = np.zeros_like(mask)
        out[:-1, :-1] |= core
        out[1:, :-1] |= core
        out[:-1, 1:] |= core
        out[1:, 1:] |= core
        return out

    @staticmethod
    def _reach(mask):
        """The samples within one step (3 x 3) of `mask`."""
        rows = mask.copy()
        rows[1:] |= mask[:-1]
        rows[:-1] |= mask[1:]
        out = rows.copy()
        out[:, 1:] |= rows[:, :-1]
        out[:, :-1] |= rows[:, 1:]
        return out

    def __init__(self, dilate: bool = False):
        self.dilate = dilate            # the second reading: a sample counts where solid change beside it does
        self.trackers = {"general": _Swings(config.PICTURE_QA_FLASH_LUMINANCE, config.PICTURE_QA_FLASH_DARK),
                         "red": _RedSwings(config.PICTURE_QA_FLASH_RED, config.PICTURE_QA_FLASH_RED_SHARE)}
        self.limit = 2 * config.PICTURE_QA_FLASH_PER_S
        self.recent = {"general": [], "red": []}      # (time, transitions per field position) within the last second
        self.counts = {"general": None, "red": None}
        self.single = {"general": None, "red": None}    # each field's transitions that always count, last second
        self.single_recent = {"general": [], "red": []}
        self.last = {"general": None, "red": None}      # each field's last transition: 1 up, -1 down, 0 both, 2 none
        self.own = {"general": None, "red": None}       # each sample's own transitions within the last second
        self.own_recent = {"general": [], "red": []}
        self.own_fine = {"general": None, "red": None}  # the same, on frames where some field was a fine counter-phase
        self.own_fine_recent = {"general": [], "red": []}
        self.own_solid = {"general": None, "red": None}       # the second reading: the same, of solid changes only
        self.own_fine_solid = {"general": None, "red": None}
        # Each fine counter-phase frame of the last second: (time, its fine counter-phase fields, and for its rise and
        # its fall: the samples, their coarse boxes and each field's coarse share), kept as it arrives, so a count
        # measures only where samples that made it themselves could take a field over the area share (_reached).
        self.fine_frames = {"general": [], "red": []}
        self.fine_count = {"general": None, "red": None}   # each field's fine counter-phase frames, last second
        self.best = {"general": 0, "red": 0}           # the highest count reported below the limit so far
        self._memo = {}                                # this frame's field shares of the samples that made n

    @staticmethod
    def _field_shares(mask, fh, fw):
        """The share of `mask` in every fh x fw window (every position)."""
        h, w = mask.shape
        ii = np.zeros((h + 1, w + 1), np.int32)
        ii[1:, 1:] = mask.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32)
        return (ii[fh:, fw:] - ii[:-fh, fw:] - ii[fh:, :-fw] + ii[:-fh, :-fw]) / (fh * fw)

    @staticmethod
    def _coarse_mask(mask, fh, fw):
        """The samples that change coarsely: those whose box (PICTURE_QA_FLASH_COARSE of the field's shorter side,
        about 1 degree, centred on the sample) mostly changes. A band, a block or a cut is coarse; fine detail
        changing in places, as when the picture moves over it, is not."""
        side = max(1, round(min(fh, fw) * config.PICTURE_QA_FLASH_COARSE))
        return uniform_filter(mask.astype(np.float32), side, mode="nearest") > 0.5

    def _reached(self, kind, n, fh, fw, fine=True) -> bool:
        """Some field made at least n transitions in the last second as the class docstring counts them; without
        `fine`, by every condition but the third (the costly one, which step() asks only above what the others give)."""
        counts, single, area = self.counts[kind], self.single[kind], config.PICTURE_QA_FLASH_AREA
        if (single >= n).any():
            return True
        reached = counts >= n
        if not reached.any():
            return False
        if self._remainder(kind, n, fh, fw, config.PICTURE_QA_FLASH_IN_PLACE):
            return True
        _, hot_share = self._hot_share(kind, n, fh, fw)
        if hot_share is None:     # no sample made n itself: neither of the other two conditions can hold
            return False
        if (reached & (hot_share > area)).any():
            return True
        return fine and self._in_place(kind, n, fh, fw, config.PICTURE_QA_FLASH_IN_PLACE)

    def _hot_share(self, kind, n, fh, fw):
        """Each field's share of the samples that made n transitions themselves (kept for the frame: step)."""
        if (kind, n) not in self._memo:
            hot = self._own(kind, "own") >= n
            self._memo[(kind, n)] = (hot, self._field_shares(hot, fh, fw) if hot.any() else None)
        return self._memo[(kind, n)]

    def _own(self, kind, which):
        """Each sample's own transitions in the last second (`own` or `own_fine`); in the second reading, its box's
        own or, if more, the most that solid change (_solid) within one step of it made (the most, not the sum: a box
        never takes its neighbours' transitions at other times as its own, so an edge moving over detail does not
        read as one box flashing in place)."""
        counts = getattr(self, which)[kind]
        if not self.dilate:
            return counts
        if (kind, which) not in self._memo:
            self._memo[(kind, which)] = np.maximum(counts, maximum_filter(getattr(self, which + "_solid")[kind], 3))
        return self._memo[(kind, which)]

    def _in_place(self, kind, n, fh, fw, least=1) -> bool:
        """The third condition (the class docstring). A fine counter-phase frame counts where, on that frame, more
        than the area share of the field rose (or fell) coarsely or by samples that made n themselves. Only fields that could reach n this way
        are measured, and a frame only where its coarse share plus every such sample of the field could exceed the
        area share. At least `least` such frames are needed: with fewer the count is held, not failed (_held). The
        most such frames any field reaching n has is kept for the frame, so the fail and the hold ask it once."""
        if (kind, "in_place", n) not in self._memo:
            self._memo[(kind, "in_place", n)] = self._in_place_frames(kind, n, fh, fw)
        return self._memo[(kind, "in_place", n)] >= max(1, least)

    def _in_place_frames(self, kind, n, fh, fw) -> int:
        """_in_place: the most counted fine frames of any field that reaches n with them (0: none), or
        PICTURE_QA_FLASH_IN_PLACE as soon as some field has that many (no call asks for more)."""
        counts, single, area = self.counts[kind], self.single[kind], config.PICTURE_QA_FLASH_AREA
        hot, hot_share = self._hot_share(kind, n, fh, fw)
        if hot_share is None:
            return 0
        fine_count = self.fine_count[kind]
        open_ = (counts >= n) & (single < n) & (single + fine_count >= n)
        if not open_.any():
            return 0
        # the share of the samples that made n is at most hot_share (the bound, with rounding room)
        could = [(fields, [(moved, box, coarse + hot_share > area - 1e-9) for moved, box, coarse in changes])
                 for _, fields, changes in self.fine_frames[kind]]
        bound = np.zeros(counts.shape, np.int16)
        for fields, changes in could:
            bound += fields & (changes[0][2] | changes[1][2])
        open_ &= (single + bound >= n) & (bound >= 1)
        if not open_.any():
            return 0
        extra, enough = np.zeros(counts.shape, np.int16), config.PICTURE_QA_FLASH_IN_PLACE
        for fields, changes in could:
            at = fields & open_
            if not at.any():
                continue
            counted = np.zeros(counts.shape, bool)
            for moved, box, possible in changes:
                if not (at & possible).any():
                    continue
                made = moved & hot & ~box
                if made.any():
                    counted |= self._field_shares(box | made, fh, fw) > area
            extra += at & counted
            if (open_ & (single + extra >= n) & (extra >= enough)).any():
                return enough
        reached = open_ & (single + extra >= n)
        return int(extra[reached].max()) if reached.any() else 0

    def _remainder(self, kind, n, fh, fw, least) -> bool:
        """Some field made at least n transitions counting its fine counter-phase frames, s of them always counting,
        and more than the area share of its samples made max(n - s, least) or more themselves on fine counter-phase
        frames: the remainder flashed in place, at least `least` times."""
        counts, single, area = self.counts[kind], self.single[kind], config.PICTURE_QA_FLASH_AREA
        reached = (counts >= n) & (single < n)
        if not reached.any():
            return False
        need = np.where(reached, np.maximum(n - single, least), 0)
        for k in np.unique(need[need > 0]):
            if ((need == k) & (self._field_shares(self._own(kind, "own_fine") >= k, fh, fw) > area)).any():
                return True
        return False

    def _held(self, kind, n, fh, fw) -> bool:
        """The last condition with any number of in-place transitions: a few fine counter-phase frames after the
        transitions that always count take the field over n wherever more than the area share of it changed on them,
        as a fine pattern reversing in place and a whip over detail both do. Only a motion test could tell them apart
        (the class docstring), so it is held for the operator's look."""
        return self._remainder(kind, n, fh, fw, 1) or self._in_place(kind, n, fh, fw, 1)

    @staticmethod
    def _highest(test, low, high) -> int:
        """The highest n in (low, high] for which `test(n)` holds, else 0. Every count condition holds for n - 1
        where it holds for n (fewer transitions are needed, more samples qualify), so a bisection finds it."""
        lo, hi = low + 1, high
        if hi < lo or not test(lo):
            return 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if test(mid):
                lo = mid
            else:
                hi = mid - 1
        return lo

    def step(self, time_s, r, g, b, decide=True, count=True, first=None) -> dict | None:
        """{kind: (some field's count is over the limit, a count reached on this frame, a held count over the limit
        or 0)}. The count is computed exactly while it is over the limit, and below it only when it can exceed the
        highest count already reported (the caller keeps the maximum), so a calm frame costs no window sums. With
        `decide` False only the state is brought up to date (a replay), and None is returned; with `count` False too,
        only each field's last direction (a replayed frame that leaves the window before the stage counts).
        `first`: the first reading's stage, already stepped on the same boxes: the second reading takes its
        per-sample transitions (`swings`; its own trackers are then unused) and its fields' rise and fall (`levels`)
        instead of computing them again."""
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        h, w = luminance.shape
        fh, fw = max(1, round(h * config.PICTURE_QA_FLASH_FIELD)), max(1, round(w * config.PICTURE_QA_FLASH_FIELD))
        area = config.PICTURE_QA_FLASH_AREA
        positions = (h - fh + 1, w - fw + 1)
        out = {}
        self._memo = {}
        self.swings = first.swings if first else {"general": self.trackers["general"].frame(luminance),
                                                  "red": self.trackers["red"].frame(r, g, b)}
        self.levels, self.out = {}, {}
        for kind, (up, down) in self.swings.items():
            raw, solid = (up, down), None
            if self.dilate:   # see the class docstring ("The fine stage's second reading")
                solid = (self._solid(up), self._solid(down))
                up, down = up | self._reach(solid[0]), down | self._reach(solid[1])
            if self.counts[kind] is None:
                self.counts[kind] = np.zeros(positions, np.int16)
                self.last[kind] = np.full(positions, 2, np.int8)    # 2: no transition yet
                self.single[kind] = np.zeros(positions, np.int16)
                self.fine_count[kind] = np.zeros(positions, np.int16)
            events, together, fine_both = None, False, False
            if up.sum() > area * fh * fw or down.sum() > area * fh * fw:
                rise = self._field_shares(up, fh, fw) > area
                fall = self._field_shares(down, fh, fw) > area
                if self.dilate:   # the reach makes a frame counter-phase only; a single direction is the boxes' own
                    both = rise & fall
                    if first is None:
                        own = (self._field_shares(raw[0], fh, fw) > area, self._field_shares(raw[1], fh, fw) > area)
                    else:     # the first reading's (none: no field of the boxes themselves rose or fell over it)
                        own = first.levels.get(kind, (False, False))
                    rise, fall = both | own[0], both | own[1]
                else:
                    self.levels[kind] = (rise, fall)
                last = self.last[kind]
                both = rise & fall
                together = bool(rise.any() or fall.any())
                turned_up = rise & ~fall & (last != 1)
                turned_down = fall & ~rise & (last != -1)
                if not count:
                    self.last[kind] = np.where(both, 0, np.where(turned_up, 1, np.where(turned_down, -1, last))
                                               ).astype(np.int8)
                    continue
                events = (both | turned_up | turned_down).astype(np.int16)
                single = turned_up | turned_down
                if both.any():      # a counter-phase frame whose rise or fall is coarse always counts
                    up_box, down_box = self._coarse_mask(raw[0], fh, fw), self._coarse_mask(raw[1], fh, fw)
                    coarse_up, coarse_down = self._field_shares(up_box, fh, fw), self._field_shares(down_box, fh, fw)
                    coarse = both & ((coarse_up > area) | (coarse_down > area))
                    single |= coarse
                    fine = both & ~coarse
                    fine_both = bool(fine.any())
                    if fine_both:
                        self.fine_frames[kind].append((time_s, fine, ((up.copy(), up_box, coarse_up),
                                                                      (down.copy(), down_box, coarse_down))))
                        self.fine_count[kind] += fine
                single = single.astype(np.int16)
                if single.any():
                    self.single_recent[kind].append((time_s, single))
                    self.single[kind] += single
                self.last[kind] = np.where(both, 0, np.where(turned_up, 1, np.where(turned_down, -1, last))
                                           ).astype(np.int8)
            if not count:
                continue
            counts, recent = self.counts[kind], self.recent[kind]
            if events is not None and events.any():
                recent.append((time_s, events))
                counts += events
            while recent and recent[0][0] <= time_s - 1.0 + 1e-6:
                counts -= recent.pop(0)[1]
            single_recent = self.single_recent[kind]
            while single_recent and single_recent[0][0] <= time_s - 1.0 + 1e-6:
                self.single[kind] -= single_recent.pop(0)[1]
            fine_frames = self.fine_frames[kind]
            while fine_frames and fine_frames[0][0] <= time_s - 1.0 + 1e-6:
                self.fine_count[kind] -= fine_frames.pop(0)[1]
            # Each sample's own transitions over the same second, on the frames where some field rose or fell over
            # the area share ("flashes occurring concurrently"; a texture moving between them is not a flash). They
            # decide only a count that needs the field's fine counter-phase frames: such a count stands where more
            # than the area share of the field made those itself (a pattern flashing in place), not where different
            # parts rose and fell in turn (a whip). Single-direction and coarse counter-phase transitions always
            # count (_reached).
            if self.own[kind] is None:
                for name in ("own", "own_fine", "own_solid", "own_fine_solid"):
                    getattr(self, name)[kind] = np.zeros(up.shape, np.int16)
            # each box's own transitions, and the second reading's solid ones (read with the reach: _own)
            moved = raw[0] | raw[1]
            solid_moved = solid[0] | solid[1] if solid is not None else None
            for flag, recent_, names in ((together, self.own_recent[kind], ("own", "own_solid")),
                                         (fine_both, self.own_fine_recent[kind], ("own_fine", "own_fine_solid"))):
                totals = [getattr(self, name)[kind] for name in names]
                if flag:
                    recent_.append((time_s, moved, solid_moved))
                    totals[0] += moved
                    if solid_moved is not None:
                        totals[1] += solid_moved
                while recent_ and recent_[0][0] <= time_s - 1.0 + 1e-6:
                    _, gone, gone_solid = recent_.pop(0)
                    totals[0] -= gone
                    if gone_solid is not None:
                        totals[1] -= gone_solid
            if not decide:
                continue
            top = int(counts.max()) if recent else 0
            # The other conditions by bisection (each holds for n - 1 where it holds for n). The costly third one is
            # asked only when they leave the field at or under the limit, upwards from it, so it is asked once a
            # frame where it adds nothing; a failing frame reports the count the other conditions give.
            reached = self._highest(lambda n, k=kind: self._reached(k, n, fh, fw, fine=False), self.limit, top)
            if not reached:
                while self.limit + reached < top and self._reached(kind, self.limit + reached + 1, fh, fw):
                    reached += 1
                reached = self.limit + reached if reached else 0
            if not reached and min(top, self.limit) > self.best[kind]:
                reached = next((n for n in range(min(top, self.limit), self.best[kind], -1)
                                if self._reached(kind, n, fh, fw)), 0)
                self.best[kind] = max(self.best[kind], reached)
            held = 0
            # no count above the limit: the held one, upwards from it (asked once a frame; not when the first
            # reading holds already, as the two are combined)
            if not reached and not (first is not None and first.out.get(kind, (0, 0, 0))[2]):
                while self.limit + held < top and self._held(kind, self.limit + held + 1, fh, fw):
                    held += 1
                held = self.limit + held if held else 0
            out[kind] = (reached > self.limit, reached, held)
        self.out = out
        return out if decide else None


class Flash:
    """Photosensitive flashing (WCAG 2.2 SC 2.3.1, "general flash and red flash thresholds"; config
    PICTURE_QA_FLASH_*).

    - Each sample's luminance (_Swings) and red (_RedSwings) transitions; a 10-degree field (the estimate in WCAG's
      note 1: a third of each side), at any position on the frame, makes a transition when more than
      PICTURE_QA_FLASH_AREA of it rises, or falls, at once, opposite to its last transition (both at once: one
      transition); a field fails at more than 3 flashes (6 transitions) in a second. Its single-direction
      transitions, and its counter-phase ones whose rise or fall is coarse (about 1 degree and up), always count,
      whichever parts of it make them; its fine counter-phase ones count where samples that made the count
      themselves, alone or with a coarse change on the same frame, take more than that share of it (_FlashStage).
    - Held: a count over the limit that only a motion test could confirm (_FlashStage) is neither a pass nor a
      fail. The check's status is "needs_review" with the stretch in `held`: the release waits for the operator's
      look (release.py), never posts by itself and is never rejected as a hazard.
    - Two stages on the one decode. The screen stage counts the chroma grid's samples (decoded pixels, nearest
      neighbour): a flashing quarter field holds hundreds of them, and a pattern finer than the grid aliases into
      flashing, so it errs strict. Only where it finds a field failing or held does the fine stage decide, on the
      flash grid (PICTURE_QA_FLASH_GRID squared area means, one per 0.05 degree), each sample the mean of 2 x 2 of
      them: a 0.1-degree box at 0.05-degree steps. The fine stage replays the last FINE_HISTORY_S of buffered
      flash-grid frames (state only, no counting), so its counts cover the whole second and each field's last
      direction is known before it (a field's first transition in a fresh stage counts whatever came before: a
      rise after an earlier rise counted as a 7th transition on dev masters with 1.5 s), and stays on until the
      screen stage has been clear for FINE_REPLAY_S.
    - WCAG exempts fine, balanced patterns with squares under 0.1 degree. The 0.1-degree box averages squares up to
      0.05 degree to a steady mean, so they are not counted. Squares of 0.05 to 0.1 degree still count: stricter
      than WCAG, never looser. Squares of 0.1 degree or more leave whole boxes flashing at every phase; the boxes that
      straddle their edges do not, so the fine stage reads the boxes twice: as they are (round 6), and with each
      sample also counted where solid change within one 0.05-degree step of it does (_FlashStage `dilate`, round 7),
      so the squares' whole area is counted. Either reading's fail fails, and either one's hold holds: adding the
      second never lets pass what the first failed (a frame the reach makes counter-phase can lose the
      single-direction count the first reading gives it). Where the first fails on a frame the second is not asked.
    - A fail always stays a fail: a stretch is held only where no field fails anywhere in the master, so a held
      stretch beside a hazard never turns the check into a hold the operator could clear (round 7)."""
    name = "picture_flash"
    fine = True                    # also takes the decode's flash grid (_decode)
    FINE_REPLAY_S = 1.5
    FINE_HISTORY_S = 5.0

    def __init__(self, deep_dive: bool):
        self.screen, self.fine_stages, self.fine_until = _FlashStage(), None, -1.0
        self.buffer = []          # (time, flash-grid planes) of the last FINE_HISTORY_S
        self.found, self.worst = {"general": [], "red": []}, {"general": 0, "red": 0}
        self.holds, self.held_worst = {"general": [], "red": []}, {"general": 0, "red": 0}
        self.held = []            # the held stretches, set by check()

    @staticmethod
    def _box(planes) -> tuple:
        """Linear R, G, B of the 0.1-degree boxes: the mean of each 2 x 2 of flash-grid samples."""
        return tuple((p[:-1, :-1] + p[1:, :-1] + p[:-1, 1:] + p[1:, 1:]) * 0.25 for p in _linear_rgb(*planes))

    @staticmethod
    def _mark(spans, time_s):
        start = max(0.0, time_s - 1.0)
        if spans and start <= spans[-1][1] + 1e-6:
            spans[-1][1] = time_s
        else:
            spans.append([start, time_s])

    def frame(self, index, time_s, y, u, v, flash=None):
        screen = self.screen.step(time_s, *_linear_rgb(y[::2, ::2], u, v))
        for kind, (hit, top, held) in screen.items():
            if not (hit or held) or flash is None:   # a screen count over the limit is the fine stage's to settle
                self.worst[kind] = max(self.worst[kind], top)
        if flash is None:              # no flash grid: the screen stage decides (strict)
            result = screen
        else:
            self.buffer.append((time_s, flash))
            while self.buffer and self.buffer[0][0] < time_s - self.FINE_HISTORY_S - 1e-6:
                self.buffer.pop(0)
            if any(hit or held for hit, _, held in screen.values()):
                if self.fine_stages is None:
                    # The boxes as they are (round 6) and the boxes with the step beside them (round 7): either
                    # one's fail fails, so adding the second never lets pass what the first failed.
                    self.fine_stages = (_FlashStage(), _FlashStage(dilate=True))
                    raw, reach = self.fine_stages
                    for t, planes in self.buffer[:-1]:
                        boxes, counted = self._box(planes), t > time_s - 1.0 + 1e-6
                        raw.step(t, *boxes, decide=False, count=counted)
                        reach.step(t, *boxes, decide=False, count=counted, first=raw)
                self.fine_until = time_s + self.FINE_REPLAY_S
            if self.fine_stages is None:
                return
            boxes = self._box(flash)
            raw, reach = self.fine_stages
            both = [raw.step(time_s, *boxes)]
            # Where the first reading already fails, the second can only agree: its state is kept, its counts not
            # asked (they are what costs).
            failed = any(hit for hit, _, _ in both[0].values())
            second = reach.step(time_s, *boxes, first=raw, decide=not failed)
            if second is not None:
                both.append(second)
            result = {kind: (any(r[kind][0] for r in both), max(r[kind][1] for r in both),
                             max(r[kind][2] for r in both)) for kind in both[0]}
            if time_s > self.fine_until:
                self.fine_stages = None
        for kind, (hit, top, held) in result.items():
            if flash is not None:
                self.worst[kind] = max(self.worst[kind], top)
            if hit:
                self._mark(self.found[kind], time_s)
            elif held:
                self._mark(self.holds[kind], time_s)
                self.held_worst[kind] = max(self.held_worst[kind], held)

    @staticmethod
    def _merged(found) -> list:
        spans = []
        for a, b in sorted(span for kind in found.values() for span in kind):
            if spans and a <= spans[-1][1] + 1e-6:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        return [{"start": round(a, 3), "end": round(b, 3)} for a, b in spans]

    def check(self, duration):
        found = self._merged(self.found)
        kinds = [kind for kind in ("general", "red") if self.found[kind]]
        per_s = config.PICTURE_QA_FLASH_PER_S
        worst = {k: f"{v / 2:g}" for k, v in self.worst.items()}   # flashes: transitions / 2
        # A stretch is held only when nothing fails: a failing interval anywhere keeps the whole check a fail (a held
        # stretch beside it must not turn a hazard into a hold the operator could clear; QF-88 round 7).
        self.held = [] if found else self._merged(self.holds)
        if found:
            what = " and ".join({"general": "flashes", "red": "flashes saturated red"}[k] for k in kinds)
            n = f"{max(self.worst[k] for k in kinds) / 2:g}"
            sentence = (f"The picture {what} up to {n} times in a second at {_spans(found)}, over more than a "
                        f"quarter of a 10-degree field (more than {per_s} flashes a second can trigger seizures; "
                        "WCAG 2.3.1): slow, shrink or remove the flashing there, then re-render.")
            return found, sentence
        if self.held:
            n = f"{max(self.held_worst.values()) / 2:g}"
            length = sum(s["end"] - s["start"] for s in self.held)
            sentence = (f"Possible flash; needs your look: {_spans(self.held)} ({length:.1f} s). Up to {n} flashes "
                        f"in a second there if a fine pattern reverses in place, fewer if the picture is moving over "
                        "detail (a whip or zoom); the check cannot tell the two apart without a motion test, so this "
                        "master waits for you. Watch that stretch (the contact sheet shows its frames): if nothing "
                        f"flashes more than {per_s} times in a second, clear it; if it does, re-render.")
            return [], sentence
        sentence = (f"No harmful flashing (WCAG 2.3.1): no quarter of a 10-degree field flashes more than {per_s} "
                    f"times in a second (the most anywhere: {worst['general']} flashes, {worst['red']} red).")
        return found, sentence


def analysers(deep_dive: bool, exempt_bars=()) -> list:
    """Every per-frame analyser of the gate, in report order (QF-88 added Flash on the same decode)."""
    return [Black(deep_dive), Freeze(deep_dive), Bars(deep_dive, exempt_bars), Range(deep_dive), Flash(deep_dive)]


def timeout_s(duration: float) -> float:
    """The decode's limit grows with the programme: a 360 s deep dive is given seven times a 50 s Reel's time."""
    return config.PICTURE_QA_TIMEOUT_BASE_S + config.PICTURE_QA_TIMEOUT_PER_S * duration


def _probe(path: Path) -> tuple:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format",
                          "-show_entries", "packet=pts_time", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, timeout=120)
    media = json.loads(out.stdout)
    video = media["streams"][0]
    width, height = int(video["width"]), int(video["height"])
    duration = float(video.get("duration") or media["format"]["duration"])
    if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("The master has no valid picture size or duration")
    # The frames' presentation times, in display order (packets arrive in decode order).
    times = sorted(float(p["pts_time"]) for p in media.get("packets", []))
    if not times or any(not math.isfinite(t) for t in times):
        raise ValueError("The master's frames have no timestamps")
    start = times[0]
    return video, width, height, duration, [t - start for t in times]


def _decode(path: Path, width: int, height: int, stride: int, timeout: float, each):
    """Decode every frame once and call `each(index, y, u, v, flash)` with its sample grid (nearest neighbour) and,
    in `flash`, the same frame as (y, u, v) on the flash grid (QF-88: PICTURE_QA_FLASH_GRID square, each sample the
    area mean of its share of the frame). One FFmpeg process splits the decoded frame into both and stacks them
    side by side in one raw frame; returns the frame count and the sample grid."""
    sw, sh = max(2, width // stride // 2 * 2), max(2, height // stride // 2 * 2)
    n = config.PICTURE_QA_FLASH_GRID
    tall = max(sh, n)
    graph = (f"[0:v:0]split[a][b];[a]scale={sw}:{sh}:flags=neighbor,format=yuv420p,pad={sw}:{tall}:0:0[s];"
             f"[b]scale={n}:{n}:flags=area,format=yuv420p,pad={n}:{tall}:0:0[f];[s][f]hstack=inputs=2,format=yuv420p")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-threads", str(config.PICTURE_QA_THREADS),
           "-i", str(path), "-an", "-sn", "-dn", "-fps_mode", "passthrough", "-filter_complex", graph,
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    wide = sw + n
    luma, chroma = wide * tall, (wide // 2) * (tall // 2)
    size = luma + 2 * chroma
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()
    expired = threading.Event()

    def stop():
        expired.set()
        proc.kill()
    timer = threading.Timer(timeout, stop)
    timer.start()
    count = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if not buf or expired.is_set():
                break
            if len(buf) != size:
                raise ValueError("The decode ended inside a frame")
            frame = np.frombuffer(buf, np.uint8)
            y = frame[:luma].reshape(tall, wide)
            u = frame[luma:luma + chroma].reshape(tall // 2, wide // 2)
            v = frame[luma + chroma:].reshape(tall // 2, wide // 2)
            each(count, y[:sh, :sw], u[:sh // 2, :sw // 2], v[:sh // 2, :sw // 2],
                 (y[:n, sw:], u[:n // 2, sw // 2:], v[:n // 2, sw // 2:]))
            count += 1
        code = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(5)
    stderr = (errors[0] if errors else b"").decode("utf-8", "replace").strip()
    if expired.is_set():
        raise ValueError(f"the decode did not finish within {timeout:.0f} s (it is tried again at the next check)")
    if code != 0 or stderr:
        raise ValueError("the video did not decode cleanly: " + (stderr[-300:] or f"ffmpeg exit {code}"))
    return count, (sw, sh)


def recorded(report_path: Path, path: Path, *, deep_dive: bool, exempt_bars=()) -> dict | None:
    """The measurement an earlier release report holds for these exact bytes (sha256), format and method, with its
    checks; None when there is none, so the master is decoded again."""
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        checks = {c["name"]: c for c in report["checks"]}
        found = checks["picture_measurement"]
        record = found["measurement"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    names = [gate.name for gate in analysers(deep_dive)]
    if (found.get("status") != "pass" or record.get("deep_dive") is not deep_dive or record.get("method") != method()
            or record.get("exempt_bars", []) != _spans_list(exempt_bars)
            or any(n not in checks for n in ("picture_colour", *names))
            or record.get("master_sha256") != sha256_file(Path(path))):
        return None
    return {**record, "reused_from": report.get("checked_at"),
            "checks": [checks[n] for n in ("picture_colour", *names)]}


def _spans_list(spans) -> list:
    return [[round(float(a), 3), round(float(b), 3)] for a, b in spans or ()]


# Masters made before QF-01 carry the BT.709 matrix and limited range but no primaries or transfer tag.
PRE_QF01_TAGS = {"color_space": "bt709", "color_primaries": None, "color_transfer": None, "color_range": "tv",
                 "pix_fmt": "yuv420p"}


def measure(path: Path, *, deep_dive: bool = False, exempt_bars=()) -> dict:
    """Decode every frame of `path` and return its picture checks; raises when any part cannot be measured.
    `exempt_bars`: (start, end) spans showing an essential image whole (composition record), not checked for bars."""
    path = Path(path)
    started = time.monotonic()
    before = sha256_file(path)
    if not before:
        raise ValueError("Picture master is missing")
    video, width, height, duration, times = _probe(path)
    stride = max(1, min(width, height) // SAMPLE_LINES)
    limit = timeout_s(duration)
    exempt = _spans_list(exempt_bars)
    gates = analysers(deep_dive, exempt)

    def each(index, y, u, v, flash):
        if index >= len(times):
            raise ValueError("The master decoded more frames than it holds")
        for gate in gates:
            gate.frame(index, times[index], y, u, v, **({"flash": flash} if getattr(gate, "fine", False) else {}))

    count, grid = _decode(path, width, height, stride, limit, each)
    if count != len(times):
        raise ValueError(f"Decoded {count} frames; the master holds {len(times)}")
    if sha256_file(path) != before:
        raise ValueError("Picture master changed during measurement")
    tags = {key: video.get(key) for key in ("color_space", "color_primaries", "color_transfer", "color_range",
                                            "pix_fmt")}
    tags_ok = (all(tags[key] == "bt709" for key in ("color_space", "color_primaries", "color_transfer"))
               and tags["color_range"] == "tv" and tags["pix_fmt"] == "yuv420p")
    wrong = ", ".join(f"{k.replace('color_', '')} {v or 'unset'}" for k, v in tags.items()
                      if v != {"color_range": "tv", "pix_fmt": "yuv420p"}.get(k, "bt709"))
    checks = [{"name": "picture_colour", "status": "pass" if tags_ok else "fail",
               "detail": "Tagged BT.709 limited range yuv420p." if tags_ok else
               f"The video's colour tags are incomplete or wrong ({wrong}), so players may show its colours "
               "wrongly: re-render it to tag it BT.709.", "tags": tags}]
    for gate in gates:
        found, sentence = gate.check(duration)
        held = None if found else getattr(gate, "held", None)     # a hold only where nothing fails
        checks.append({"name": gate.name, "status": "fail" if found else "needs_review" if held else "pass",
                       "detail": sentence, "intervals": found, **({"held": held, "hold": True} if held else {})})
    return {"master_sha256": before, "deep_dive": deep_dive, "exempt_bars": exempt, "decoded_frames": count,
            "duration": round(duration, 3),
            "sample_grid": list(grid), "stride": stride, "elapsed_s": round(time.monotonic() - started, 2),
            "timeout_s": round(limit, 1), "method": method(), "checks": checks}


def hold_sheet(path: Path, start: float, end: float, out: Path) -> dict:
    """A JPEG contact sheet of a held flash stretch (QF-88) from the master's own frames: every frame from `start` to
    `end` (evenly thinned to PICTURE_HOLD_SHEET_FRAMES), in order, each labelled with its time, so the operator can
    see what changes there. Kept at PICTURE_HOLD_SHEET_KB or less; returns {"path", "start", "end", "frames",
    "bytes"} and raises when the frames cannot be read."""
    from PIL import Image, ImageDraw, ImageFont
    path, out = Path(path), Path(out)
    _, width, height, _, times = _probe(path)
    picked = [i for i, t in enumerate(times) if start - 1e-6 <= t <= end + 1e-6]
    if not picked:
        raise ValueError(f"no frames between {_clock(start)} and {_clock(end)}")
    keep = config.PICTURE_HOLD_SHEET_FRAMES
    if len(picked) > keep:
        picked = [picked[round(k * (len(picked) - 1) / (keep - 1))] for k in range(keep)]
    tw = config.PICTURE_HOLD_SHEET_TILE
    th = max(2, round(tw * height / width / 2) * 2)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-i", str(path), "-an", "-sn", "-dn",
           "-fps_mode", "passthrough", "-vf", f"select='between(n,{picked[0]},{picked[-1]})',scale={tw}:{th}:flags=area",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s(times[-1] + 1), check=False)
    size = tw * th * 3
    if proc.returncode != 0 or len(proc.stdout) < size * (picked[-1] - picked[0] + 1):
        raise ValueError("the stretch's frames could not be decoded: "
                         + proc.stderr.decode("utf-8", "replace").strip()[-200:])
    cols = min(len(picked), 8)
    rows = -(-len(picked) // cols)
    label = 14
    sheet = Image.new("RGB", (cols * tw, rows * (th + label)), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for k, i in enumerate(picked):
        at = (i - picked[0]) * size
        tile = Image.frombuffer("RGB", (tw, th), proc.stdout[at:at + size], "raw", "RGB", 0, 1)
        x, y = (k % cols) * tw, (k // cols) * (th + label)
        sheet.paste(tile, (x, y + label))
        draw.text((x + 3, y + 1), f"{_clock(times[i])} #{i}", fill=(235, 235, 235), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    limit = config.PICTURE_HOLD_SHEET_KB * 1024
    for quality in (85, 75, 65, 55, 45, 35):
        sheet.save(out, "JPEG", quality=quality, optimize=True)
        if out.stat().st_size <= limit:
            break
    while out.stat().st_size > limit:
        sheet = sheet.resize((sheet.width * 3 // 4, sheet.height * 3 // 4), Image.LANCZOS)
        sheet.save(out, "JPEG", quality=35, optimize=True)
    return {"path": out.name, "start": round(start, 3), "end": round(end, 3), "frames": len(picked),
            "bytes": out.stat().st_size}


def ingest_problems(path: Path) -> list:
    """Footage and AI motion clips checked when they are taken in (QF-88: archive film, operator footage and AI hook
    clips were never measured): one decode through the black, freeze and photosensitivity analysers, as a Reel.
    Returns the failed checks' sentences ([] when the clip is clean); raises when it cannot be measured (a caller
    then leaves the verdict to the release gate's decode of the master, which fails closed)."""
    path = Path(path)
    _, width, height, duration, times = _probe(path)
    gates = [Black(False), Freeze(False), Flash(False)]

    def each(index, y, u, v, flash):
        if index >= len(times):
            raise ValueError("The clip decoded more frames than it holds")
        for gate in gates:
            gate.frame(index, times[index], y, u, v, **({"flash": flash} if getattr(gate, "fine", False) else {}))

    count, _ = _decode(path, width, height, max(1, min(width, height) // SAMPLE_LINES), timeout_s(duration), each)
    if count != len(times):
        raise ValueError(f"Decoded {count} frames; the clip holds {len(times)}")
    out = []
    for gate in gates:
        found, sentence = gate.check(duration)
        if found:
            out.append(f"{gate.name}: {sentence}")
    return out
