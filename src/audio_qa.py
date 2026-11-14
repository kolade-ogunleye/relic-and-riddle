"""Objective sound checks on a delivered master, for the release gate (QF-11).

One FFmpeg decode reads every audio sample of the master and splits it three ways:
- the programme as delivered, through `ebur128` (ITU-R BS.1770 loudness and 4x-oversampled true peak, EBU Tech 3341
  momentary and short-term windows, EBU Tech 3342 loudness range), `astats` and `silencedetect`;
- a mobile-speaker simulation (AUDIO_QA_PHONE_LOW_HZ to AUDIO_QA_PHONE_HIGH_HZ, 24 dB per octave at each edge),
  measured with the same meter;
- the sibilance band (AUDIO_QA_SIBILANCE_LOW_HZ to AUDIO_QA_SIBILANCE_HIGH_HZ), measured with the same meter.

Two checks block a release, each an objective fault: `audio_dropout` (the sound drops to silence inside the
programme, or a long silence at either end) and `audio_measurement` (the sound could not be measured completely: any
decode error, timeout, short decode or changed file; never reported as a pass). One check is a note, shown on the
review card and never blocking: `audio_phone` (a 3 s window in which most of the loudness is below what a phone
speaker plays). Loudness range, maximum momentary and short-term loudness, peak-to-loudness ratio, sibilance and the
astats figures are recorded with the measurement. The integrated loudness and true-peak limits stay the gate's own
`loudness` and `true_peak` checks.
"""

import json
import math
import re
import statistics
import subprocess
import time
from pathlib import Path

from . import config
from .credits import sha256_file

# Bumped whenever a check's definition changes, so a measurement recorded by an older definition is redone.
METHOD_VERSION = 2   # 2: the silence detector is read in every FFmpeg form and checked against the meter
NAMES = ("audio_dropout", "audio_phone")

_FRAME = re.compile(r"\[ebur128@(\w+) @ [^\]]+\] t: ([\d.]+)\s+TARGET:\S+ LUFS\s+M:\s*(-?[\d.]+|-inf|nan)\s+"
                    r"S:\s*(-?[\d.]+|-inf|nan)(.*)$", re.MULTILINE)
# The frame's true peak per channel (dBFS), printed by a meter with peak=true: "FTPK:  -3.7  -3.7 dBFS".
_FTPK = re.compile(r"FTPK:((?:\s+(?:-?[\d.]+|-inf))+)\s+dBFS")
# silencedetect logged through its private context until FFmpeg 8.0 (`av_log(s, ...)`: the line reads
# "[silencedetect @ 0x...]") and through the filter's own from 8.1 (`av_log(ctx, ...)`: "[silencedetect@gap @ ...]").
# The graph holds one silencedetect, so every one of these forms is its output.
_SILENCE = re.compile(r"\[(?:silencedetect(?:@\w+)?|Parsed_silencedetect_\d+) @ [^\]]+\] silence_(start|end): (-?[\d.]+)")


def method() -> dict:
    """What a measurement depends on besides the master's bytes: a recorded result is reused only on an equal one."""
    return {"version": METHOD_VERSION,
            **{name: getattr(config, name) for name in sorted(dir(config)) if name.startswith("AUDIO_QA_")
               and name not in ("AUDIO_QA_TIMEOUT_BASE_S", "AUDIO_QA_TIMEOUT_PER_S")}}


def timeout_s(duration: float) -> float:
    return config.AUDIO_QA_TIMEOUT_BASE_S + config.AUDIO_QA_TIMEOUT_PER_S * duration


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _spans(intervals: list) -> str:
    shown = ", ".join(f"{_clock(i['start'])}-{_clock(i['end'])}" for i in intervals[:4])
    return shown + (f" and {len(intervals) - 4} more" if len(intervals) > 4 else "")


def _band(low: float, high: float) -> str:
    """Two cascaded 2-pole sections at each edge: 24 dB per octave outside the band."""
    return f"highpass=f={low},highpass=f={low},lowpass=f={high},lowpass=f={high}"


def graph() -> str:
    c = config
    return ("[0:a:0]asplit=3[full][phone][sib];"
            "[full]ebur128@full=peak=true:framelog=info,astats@stats=measure_perchannel=none,"
            f"silencedetect@gap=noise={c.AUDIO_QA_SILENCE_DB}dB:d={c.AUDIO_QA_SILENCE_S}[out];"
            f"[phone]{_band(c.AUDIO_QA_PHONE_LOW_HZ, c.AUDIO_QA_PHONE_HIGH_HZ)},ebur128@phone=framelog=info,anullsink;"
            f"[sib]{_band(c.AUDIO_QA_SIBILANCE_LOW_HZ, c.AUDIO_QA_SIBILANCE_HIGH_HZ)},"
            "ebur128@sib=framelog=info,anullsink")


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"a non-finite audio measurement ({text})")
    return value


def _summary(stderr: str, name: str) -> dict:
    found = re.search(rf"\[ebur128@{name} @ [^\]]+\] Summary:", stderr)
    if not found:
        raise ValueError(f"the loudness meter ({name}) printed no summary")
    block = stderr[found.end():]
    block = block[:block.find("\n[")] if "\n[" in block else block
    values = {}
    for key, pattern in (("integrated_lufs", r"I:\s+(-?[\d.]+|-inf) LUFS"), ("loudness_range_lu", r"LRA:\s+([\d.]+) LU"),
                         ("true_peak_dbtp", r"Peak:\s+(-?[\d.]+|-inf) dBFS")):
        match = re.search(pattern, block)
        if match:
            values[key] = _number(match.group(1))
        elif key != "true_peak_dbtp" or name == "full":
            raise ValueError(f"the loudness meter ({name}) gave no {key}")
    return values


def _frames(stderr: str) -> dict:
    """Each meter's 100 ms frames: (time, momentary, short-term, true peak dBFS); -inf (silence) kept as None for
    the loudness and as -inf for the peak; the peak is None where the meter prints none (peak=false)."""
    frames = {"full": [], "phone": [], "sib": []}
    for name, t, m, s, rest in _FRAME.findall(stderr):
        if name in frames:
            level = [float(v) if v not in ("-inf", "nan") and float(v) > -120 else None for v in (m, s)]
            found = _FTPK.search(rest)
            peak = max(float(v) for v in found.group(1).split()) if found else None
            frames[name].append((float(t), *level, peak))
    return frames


def _astats(stderr: str) -> dict:
    lines = dict(re.findall(r"\[astats@stats @ [^\]]+\] ([A-Za-z][A-Za-z _]+): (-?[\d.]+|-?inf)", stderr))
    keys = {"Peak level dB": "peak_dbfs", "RMS level dB": "rms_dbfs", "RMS peak dB": "rms_peak_dbfs",
            "Flat factor": "flat_factor", "Peak count": "peak_count", "Noise floor dB": "noise_floor_dbfs",
            "DC offset": "dc_offset", "Number of samples": "samples_per_channel"}
    if any(k not in lines for k in keys):
        raise ValueError("the sample statistics are incomplete")
    out = {}
    for k, v in keys.items():
        number = float(lines[k])
        out[v] = round(number, 4) if math.isfinite(number) else None
    return out


def _silences(stderr: str, duration: float) -> list:
    found, start = [], None
    for kind, value in _SILENCE.findall(stderr):
        if kind == "start":
            start = max(0.0, float(value))
        elif start is not None:
            found.append({"start": round(start, 3), "end": round(float(value), 3)})
            start = None
    if start is not None:          # silent to the end: silencedetect never closes it
        found.append({"start": round(start, 3), "end": round(duration, 3)})
    return found


def _check_detector(silences: list, frames: list) -> None:
    """Fail closed when the silence detector's output disagrees with the meter's own true peaks (an unread or
    misread detector must never pass as "no silence"). Three 100 ms frames in a row whose true peak is below
    AUDIO_QA_SILENCE_DB hold 0.3 s of samples below it (the true peak is never below the sample peak), which the
    detector must report (it reports 0.2 s); and a frame wholly inside a reported silence must not peak above it."""
    level = config.AUDIO_QA_SILENCE_DB
    if not frames or any(f[3] is None for f in frames):
        raise ValueError("the loudness meter printed no frame true peaks, so the silence detector cannot be checked")

    def reported(a: float, b: float) -> bool:
        return any(sil["start"] <= a + 0.05 and sil["end"] >= b - 0.05 for sil in silences)

    run = []
    for f in [*frames, (math.inf, None, None, 0.0)]:
        if f[3] < level:
            run.append(f[0])
            continue
        if len(run) >= 3 and not reported(run[0] - 0.1, run[-1]):
            raise ValueError(f"the silence detector reported no silence at {_clock(run[0] - 0.1)}-{_clock(run[-1])}, "
                             f"where every sample is below {level} dBFS (its output could not be read)")
        run = []
    for sil in silences:
        loud = [f for f in frames if sil["start"] + 0.05 <= f[0] - 0.1 and f[0] <= sil["end"] - 0.05
                and f[3] > level + 1.0]
        if loud:
            raise ValueError(f"the silence detector reported silence at {_clock(sil['start'])}-{_clock(sil['end'])}, "
                             f"where the sound peaks at {max(f[3] for f in loud):.1f} dBFS")


def _dropout(silences: list, duration: float) -> tuple:
    edge = config.AUDIO_QA_EDGE_S
    inside = [s for s in silences if s["start"] > edge and s["end"] < duration - edge]
    ends = [s for s in silences if s not in inside and s["end"] - s["start"] >= config.AUDIO_QA_EDGE_SILENCE_S]
    found = inside + ends
    if found:
        return found, (f"The sound drops to silence at {_spans(found)}: check the voice and the music there and "
                       "re-render.")
    longest = max((s["end"] - s["start"] for s in silences), default=0.0)
    return [], (f"No silence inside the programme (below {config.AUDIO_QA_SILENCE_DB} dBFS for "
                f"{config.AUDIO_QA_SILENCE_S} s or more" + (f"; {longest:.2f} s at an end" if silences else "") + ").")


def _windows(full: list, other: list, gate: float) -> list:
    """(time, full-band short-term, other-band short-term) for the 3 s windows above the relative gate."""
    out = []
    for f, o in zip(full, other):
        if f[0] >= 3.0 - 1e-6 and f[2] is not None and f[2] > gate:
            out.append((f[0], f[2], o[2] if o[2] is not None else -120.0))
    return out


def measure(path: Path, *, deep_dive: bool = False) -> dict:
    """Decode every sample of `path` once and return its sound checks; raises when any part cannot be measured."""
    path = Path(path)
    started = time.monotonic()
    before = sha256_file(path)
    if not before:
        raise ValueError("the master is missing")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams", "-show_format",
                            "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=60)
    media = json.loads(probe.stdout)
    if not media.get("streams"):
        raise ValueError("the master has no sound")
    stream = media["streams"][0]
    rate, channels = int(stream["sample_rate"]), int(stream["channels"])
    duration = float(stream.get("duration") or media["format"]["duration"])
    if rate <= 0 or channels <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("the master's sound has no valid format or duration")
    limit = timeout_s(duration)
    try:
        result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-xerror", "-i", str(path),
                                 "-filter_complex", graph(), "-map", "[out]", "-f", "null", "-"],
                                capture_output=True, text=True, check=False, timeout=limit)
    except subprocess.TimeoutExpired:
        raise ValueError(f"the decode did not finish within {limit:.0f} s (it is tried again at the next check)")
    if result.returncode != 0:
        raise ValueError("the sound did not decode cleanly: " + (result.stderr.strip()[-300:] or
                                                                f"ffmpeg exit {result.returncode}"))
    if sha256_file(path) != before:
        raise ValueError("the master changed during measurement")
    return parse(result.stderr, duration=duration, rate=rate, channels=channels, deep_dive=deep_dive,
                 master_sha256=before, started=started, limit=limit)


def parse(stderr: str, *, duration: float, rate: int, channels: int, deep_dive: bool = False,
          master_sha256: str = "", started: float | None = None, limit: float = 0.0) -> dict:
    """The checks and figures from the decode's log (FFmpeg 6.x to 8.x); raises when any part is missing or the
    silence detector disagrees with the meter, so nothing unread is reported as a pass."""
    started = time.monotonic() if started is None else started
    full, phone, sib = (_summary(stderr, n) for n in ("full", "phone", "sib"))
    frames = _frames(stderr)
    stats = _astats(stderr)
    samples = stats["samples_per_channel"]
    if samples is None or abs(samples / rate - duration) > 0.1:
        raise ValueError(f"decoded {samples} samples per channel; the master holds {duration:.3f} s at {rate} Hz")
    if (not frames["full"] or len({len(v) for v in frames.values()}) != 1
            or frames["full"][-1][0] < duration - 0.25):
        raise ValueError("the loudness meters did not cover the whole programme")

    gate = full["integrated_lufs"] - 20.0      # BS.1770 / Tech 3342 relative gate for short-term windows
    momentary = [f[1] for f in frames["full"] if f[1] is not None]
    short = [w[1] for w in _windows(frames["full"], frames["full"], gate)]
    phone_windows = [(t, a - b) for t, a, b in _windows(frames["full"], frames["phone"], gate)]
    worst_phone = max(phone_windows, key=lambda w: w[1], default=None)
    sib_rel = [(f[0], s[1] - f[1]) for f, s in zip(frames["full"], frames["sib"])
               if f[1] is not None and s[1] is not None and f[1] > full["integrated_lufs"] - 10.0]
    worst_sib = max(sib_rel, key=lambda w: w[1], default=None)
    silences = _silences(stderr, duration)
    _check_detector(silences, frames["full"])

    dropouts, dropout_text = _dropout(silences, duration)
    loss = full["integrated_lufs"] - phone["integrated_lufs"]
    limit_lu = config.AUDIO_QA_PHONE_LOSS_LU
    over = [w for w in phone_windows if w[1] > limit_lu]
    phone_check = {"name": "audio_phone", "status": "pass",
                   "detail": (f"On a phone speaker ({config.AUDIO_QA_PHONE_LOW_HZ}-{config.AUDIO_QA_PHONE_HIGH_HZ} Hz) "
                              f"the programme plays {loss:.1f} LU quieter, its worst 3 s "
                              + (f"(from {_clock(worst_phone[0] - 3.0)}) {worst_phone[1]:.1f} LU; a note is shown "
                                 f"above {limit_lu:.0f} LU." if worst_phone else
                                 "window not measured (shorter than 3 s).")),
                   "intervals": []}
    if over:
        spans = [{"start": round(t - 3.0, 1), "end": round(t, 1)} for t, _ in over]
        merged = []
        for s in spans:
            if merged and s["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = s["end"]
            else:
                merged.append(dict(s))
        phone_check.update(minor=True, intervals=merged, detail=(
            f"On a phone speaker most of the sound at {_spans(merged)} is too low to be heard (up to "
            f"{worst_phone[1]:.1f} LU of its loudness is below {config.AUDIO_QA_PHONE_LOW_HZ} Hz): a phone plays "
            "that part much quieter than the meter reads. Not blocking."))
    checks = [{"name": "audio_dropout", "status": "fail" if dropouts else "pass", "detail": dropout_text,
               "intervals": dropouts},
              phone_check]
    return {"master_sha256": master_sha256, "deep_dive": deep_dive, "duration": round(duration, 3), "sample_rate": rate,
            "channels": channels, "decoded_samples_per_channel": int(samples),
            "integrated_lufs": full["integrated_lufs"], "true_peak_dbtp": full["true_peak_dbtp"],
            "loudness_range_lu": full["loudness_range_lu"],
            # EBU R 128 note 2: loudness range is not recommended for a programme shorter than a minute
            "loudness_range_applicable": duration >= 60.0,
            "max_momentary_lufs": max(momentary) if momentary else None,
            "max_short_term_lufs": max(short) if short else None,
            "median_short_term_lufs": statistics.median(short) if short else None,
            "peak_to_loudness_lu": round(full["true_peak_dbtp"] - full["integrated_lufs"], 1),
            "phone": {"band_hz": [config.AUDIO_QA_PHONE_LOW_HZ, config.AUDIO_QA_PHONE_HIGH_HZ],
                      "integrated_lufs": phone["integrated_lufs"], "loss_lu": round(loss, 1),
                      "worst_window_loss_lu": round(worst_phone[1], 1) if worst_phone else None,
                      "worst_window_end_s": round(worst_phone[0], 1) if worst_phone else None,
                      "windows": len(phone_windows)},
            "sibilance": {"band_hz": [config.AUDIO_QA_SIBILANCE_LOW_HZ, config.AUDIO_QA_SIBILANCE_HIGH_HZ],
                          "integrated_rel_lu": round(sib["integrated_lufs"] - full["integrated_lufs"], 1),
                          "worst_momentary_rel_lu": round(worst_sib[1], 1) if worst_sib else None,
                          "worst_at_s": round(worst_sib[0], 1) if worst_sib else None},
            "astats": stats, "silence_intervals": silences,
            "elapsed_s": round(time.monotonic() - started, 2), "timeout_s": round(limit, 1), "method": method(),
            "checks": checks}


def recorded(report_path: Path, path: Path, *, deep_dive: bool) -> dict | None:
    """The measurement an earlier release report holds for these exact bytes (sha256), format and method, with its
    checks; None when there is none, so the master is decoded again."""
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        checks = {c["name"]: c for c in report["checks"]}
        found = checks["audio_measurement"]
        record = found["measurement"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (found.get("status") != "pass" or record.get("deep_dive") is not deep_dive or record.get("method") != method()
            or any(n not in checks for n in NAMES) or record.get("master_sha256") != sha256_file(Path(path))):
        return None
    return {**record, "reused_from": report.get("checked_at"), "checks": [checks[n] for n in NAMES]}
