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
from typing import List, Dict, Any, Tuple

from .config import (
    AUDIO_SAMPLE_RATE,
    FONTS_DIR,
    FONT_NAME,
    FONT_SIZE,
    CAPTION_MARGIN_V,
    MAX_BURST_WORDS,
    MAX_BURST_CHARS,
    COL_ACTIVE,
    COL_PAST,
    COL_FUTURE,
    COL_BACK,
    TARGET_LUFS,
    TARGET_TRUE_PEAK,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PRESET,
)
from .typography import is_video

log = logging.getLogger(__name__)

TRANSITIONS = [
    "zoomin", "dissolve", "smoothleft", "fadeblack",
    "zoomin", "dissolve", "smoothright", "fade",
    "zoomin", "dissolve", "smoothleft", "fadeblack",
]
TRANS_DUR = 0.40


class RenderError(RuntimeError):
    pass


def _run_ffmpeg(cmd: List[str]) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed (exit {res.returncode}):\n{tail}")
    return res


def _ass_escape(text: str) -> str:
    """Neutralizes ASS override syntax so caption text can't inject styling tags."""
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _audio_chain(vo_idx: int, bg_idx: int) -> str:
    """Voice cleanup + ducked bed + sum at the delivery sample rate, before mastering."""
    return (
        f"[{vo_idx}:a]aresample={AUDIO_SAMPLE_RATE},highpass=f=80,equalizer=f=3200:width_type=q:w=1.5:g=-2.5,"
        f"asplit[v_main][v_sc];"
        f"[{bg_idx}:a]aresample={AUDIO_SAMPLE_RATE}[bg_rs];"
        f"[bg_rs][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0"
    )


def _master_chain(gain_db: float) -> str:
    """Gain + true-peak-safe limiter (limiting at 4x rate catches inter-sample peaks)."""
    return (
        f",volume={gain_db:.2f}dB,aresample={AUDIO_SAMPLE_RATE * 4},"
        f"alimiter=limit=0.79:attack=1:release=50:level=disabled:asc=1,"
        f"aresample={AUDIO_SAMPLE_RATE}"
    )


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

    def build_subtitles(self, words: List[Dict[str, Any]], ass_path: Path) -> Path:
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT_NAME},{FONT_SIZE},{COL_PAST},&H000000FF,&H00000000,{COL_BACK},-1,0,0,0,100,100,0.5,0,3,14,0,2,40,40,{CAPTION_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        # Short bursts (max 2 words / 13 chars) for the large Anton font
        phrases: List[List[Dict[str, Any]]] = []
        cur: List[Dict[str, Any]] = []
        for w in words:
            candidate = " ".join(x["word"] for x in cur + [w])
            if cur and (len(cur) >= MAX_BURST_WORDS or len(candidate) > MAX_BURST_CHARS):
                phrases.append(cur)
                cur = [w]
            else:
                cur.append(w)
        if cur:
            phrases.append(cur)

        raw = []
        for pi, phrase in enumerate(phrases):
            if pi < len(phrases) - 1:
                next_start = phrases[pi + 1][0]["start"]
            else:
                next_start = phrase[-1]["start"] + phrase[-1]["dur"] + 0.10
            for wi, target in enumerate(phrase):
                t_end = phrase[wi + 1]["start"] if wi < len(phrase) - 1 else next_start
                parts = []
                for ji, w in enumerate(phrase):
                    colour = COL_PAST if ji < wi else COL_ACTIVE if ji == wi else COL_FUTURE
                    parts.append(f"{{\\c{colour}}}{_ass_escape(w['word'].upper())}")
                raw.append((target["start"], t_end, " ".join(parts)))

        raw.sort(key=lambda e: e[0])
        lines = [header]
        for i, (ts, te, txt) in enumerate(raw):
            if i < len(raw) - 1:
                te = min(te, raw[i + 1][0] - 0.01)
            te = max(te, ts + 0.02)
            lines.append(f"Dialogue: 0,{self._format_ass_time(ts)},{self._format_ass_time(te)},Cap,,0,0,0,,{txt}")

        ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Wrote %d caption events -> %s", len(raw), ass_path.name)
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
                       max_iter: int = 6) -> float:
        """
        Finds the gain that lands the limited mix on TARGET_LUFS (within `tolerance` LU,
        the usual delivery tolerance). The limiter absorbs part of each gain step, so steps
        are scaled up. If it never gets that close, the best result within `accept` LU is
        used with a warning; platforms re-normalize, so that is not worth failing a run.
        """
        base = _audio_chain(0, 1)
        integrated, _ = self._measure(vo_path, bed_path, base)
        gain = TARGET_LUFS - integrated
        best_gain, best_err = gain, float("inf")
        for _ in range(max_iter):
            integrated, peak = self._measure(vo_path, bed_path, base + _master_chain(gain))
            log.info("Mastering: gain %+.2f dB -> %.1f LUFS, %.1f dBTP", gain, integrated, peak)
            if peak > TARGET_TRUE_PEAK:
                raise RenderError(f"True peak {peak} dBTP exceeds {TARGET_TRUE_PEAK} after limiting.")
            err = abs(integrated - TARGET_LUFS)
            if err < best_err:
                best_gain, best_err = gain, err
            if err <= tolerance + 1e-9:
                return gain
            gain += (TARGET_LUFS - integrated) * 1.5
        if best_err <= accept:
            log.warning("Loudness within %.1f LU of %.1f LUFS (not %.1f); using best gain.", best_err, TARGET_LUFS, tolerance)
            return best_gain
        raise RenderError(f"Loudness did not converge to {TARGET_LUFS} LUFS (best was {best_err:.1f} LU off).")

    def compose_video(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
    ) -> Path:
        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(words, ass_path)

        n = len(scene_images)
        step = (vo_dur + TRANS_DUR) / n
        clip_dur = step + TRANS_DUR
        frames = int(clip_dur * VIDEO_FPS) + 1
        w2, h2 = VIDEO_WIDTH * 2, VIDEO_HEIGHT * 2

        input_args: List[str] = []
        filter_parts: List[str] = []
        for i, item in enumerate(scene_images):
            item = Path(item)
            if is_video(item):
                input_args += ["-t", f"{clip_dur:.3f}", "-i", str(item)]
                filter_parts.append(
                    f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
                )
                continue

            input_args += ["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", str(item)]
            if i == 0 or i == n - 1:  # matching forward drift for the loop seam
                z, x, y = "min(pzoom+0.0016,1.24)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif i % 2 == 1:
                z, x, y = "max(1.20-0.0014*on,1.0)", "iw/2-(iw/zoom/2)+sin(on/35)*32", "ih/2-(ih/zoom/2)"
            else:
                z, x, y = "min(pzoom+0.0013,1.18)", "iw/2-(iw/zoom/2)-sin(on/35)*28", "ih/2-(ih/zoom/2)+sin(on/45)*16"
            filter_parts.append(
                f"[{i}:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
                f"setsar=1,scale=out_range=tv,format=yuv420p[v{i}]"
            )

        curr = "[v0]"
        for k in range(n - 1):
            out = f"[x{k}]" if k < n - 2 else "[video_raw]"
            off = round((k + 1) * step, 3)
            filter_parts.append(f"{curr}[v{k + 1}]xfade=transition={TRANSITIONS[k % len(TRANSITIONS)]}:duration={TRANS_DUR}:offset={off}{out}")
            curr = out
        if n == 1:
            filter_parts.append("[v0]null[video_raw]")

        input_args += ["-i", str(vo_path), "-i", str(bed_path)]
        gain = self.calibrate_gain(vo_path, bed_path)
        filter_parts.append(_audio_chain(n, n + 1) + _master_chain(gain) + "[a_final]")

        ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts_str = str(self.fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")
        filter_parts.append(
            "[video_raw]eq=contrast=1.07:brightness=0.008:saturation=1.08,"
            "noise=alls=7:allf=t+u,vignette=PI/4.2:aspect=9/16,"
            f"subtitles='{ass_str}':fontsdir='{fonts_str}',scale=out_range=tv,format=yuv420p[v_final]"
        )

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[v_final]", "-map", "[a_final]",
            "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF),
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-r", str(VIDEO_FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SAMPLE_RATE),
            "-movflags", "+faststart",
            "-t", f"{vo_dur:.2f}",
            str(output_mp4_path),
        ]
        log.info("Rendering master -> %s", output_mp4_path.name)
        _run_ffmpeg(cmd)
        size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
        log.info("Master rendered: %s (%.2f MB)", output_mp4_path.name, size_mb)
        return output_mp4_path

    @staticmethod
    def make_review_proxy(master_mp4: Path, proxy_path: Path) -> Path:
        """Small 720p copy for phone review over Telegram (bot uploads are capped near 50 MB)."""
        _run_ffmpeg([
            "ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(master_mp4),
            "-vf", "scale=720:1280", "-c:v", "libx264", "-preset", "fast", "-crf", "28",
            "-maxrate", "1500k", "-bufsize", "3000k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(proxy_path),
        ])
        log.info("Review proxy: %s (%.1f MB)", proxy_path.name, os.path.getsize(proxy_path) / (1024 * 1024))
        return proxy_path
