"""Objective picture checks on a delivered master, for the release gate (QF-10).

One FFmpeg decode reads every frame of the master. Each frame is sampled on a regular grid (every STRIDE-th pixel
of every STRIDE-th line, nearest neighbour: the sampled values are the decoded values, nothing is filtered or
averaged) and handed to the analysers, which keep their own state across frames. The decode is the cost; the
analysers add little to it. The grid keeps 1080-line masters at 270 x 480 (Reel) or 480 x 270 (16:9) samples.

Each analyser reports one release check: picture_black, picture_freeze, picture_bars and picture_range. The colour
tags (picture_colour) come from ffprobe. A check that could not be measured is never reported as a pass: any decode
error, timeout, missing frame or changed file raises, and the gate records picture_measurement: fail.

Extension point (QF-88): an analyser is any object with a `name`, `frame(index, time, y, u, v)` and
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

from . import config
from .credits import sha256_file

# Bumped whenever an analyser's definition changes, so a measurement recorded by an older definition is redone.
METHOD_VERSION = 1
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


def analysers(deep_dive: bool, exempt_bars=()) -> list:
    """Every per-frame analyser of the gate, in report order (QF-88 appends its own here)."""
    return [Black(deep_dive), Freeze(deep_dive), Bars(deep_dive, exempt_bars), Range(deep_dive)]


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
    """Decode every frame and call `each(index, y, u, v)` with its sample grid; returns the frame count."""
    sw, sh = max(2, width // stride // 2 * 2), max(2, height // stride // 2 * 2)
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-threads", str(config.PICTURE_QA_THREADS),
           "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn", "-fps_mode", "passthrough",
           "-vf", f"scale={sw}:{sh}:flags=neighbor", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    luma, chroma = sw * sh, (sw // 2) * (sh // 2)
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
            each(count, frame[:luma].reshape(sh, sw), frame[luma:luma + chroma].reshape(sh // 2, sw // 2),
                 frame[luma + chroma:].reshape(sh // 2, sw // 2))
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

    def each(index, y, u, v):
        if index >= len(times):
            raise ValueError("The master decoded more frames than it holds")
        for gate in gates:
            gate.frame(index, times[index], y, u, v)

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
        checks.append({"name": gate.name, "status": "fail" if found else "pass", "detail": sentence,
                       "intervals": found})
    return {"master_sha256": before, "deep_dive": deep_dive, "exempt_bars": exempt, "decoded_frames": count,
            "duration": round(duration, 3),
            "sample_grid": list(grid), "stride": stride, "elapsed_s": round(time.monotonic() - started, 2),
            "timeout_s": round(limit, 1), "method": method(), "checks": checks}
