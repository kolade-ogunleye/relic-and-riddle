"""
src/video_composer.py
=====================
Programmatic FFmpeg assembly pipeline embodying all locked production standards:
1. Anton 92pt running-speech kinetic captions (Warm Gold active, White past, Gray future).
2. Zero-overlap ASS dialogue event architecture.
3. Meta UI Safe Zone alignment (MarginV = 540px / y = 1380px).
4. Varied camera motion with opening/ending forward match-cut camera drift.
5. Dynamic sidechain audio ducking of ambient score under voiceover.
6. Cinematic grading (contrast, saturation, film grain, vignette).
7. H.264 CRF 18 slow preset master encode.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from .config import (
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
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PRESET,
)


class VideoComposer:
    def __init__(self, fonts_dir: Path = FONTS_DIR):
        self.fonts_dir = fonts_dir

    def _format_ass_time(self, sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        cs = int(round((sec - int(sec)) * 100))
        if cs >= 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def build_subtitles(self, words: List[Dict[str, Any]], ass_path: Path) -> Path:
        """
        Compiles the zero-overlap ASS subtitle file strictly following locked standards.
        """
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
        # Chunk into short bursts (max 2 words or 13 chars) for large Anton font
        phrases = []
        cur = []
        for w in words:
            candidate = " ".join(x["word"] for x in cur + [w])
            if len(cur) >= MAX_BURST_WORDS or len(candidate) > MAX_BURST_CHARS:
                if cur:
                    phrases.append(cur)
                cur = [w]
            else:
                cur.append(w)
        if cur:
            phrases.append(cur)

        raw = []
        for pi, phrase in enumerate(phrases):
            if pi < len(phrases) - 1:
                next_phrase_start = phrases[pi + 1][0]["start"]
            else:
                next_phrase_start = phrase[-1]["start"] + phrase[-1]["dur"] + 0.10

            for wi, target in enumerate(phrase):
                t_start = target["start"]
                t_end_raw = phrase[wi + 1]["start"] if wi < len(phrase) - 1 else next_phrase_start

                parts = []
                for ji, w in enumerate(phrase):
                    word_clean = w["word"].upper()
                    if ji < wi:
                        parts.append(f"{{\\c{COL_PAST}}}{word_clean}")
                    elif ji == wi:
                        parts.append(f"{{\\c{COL_ACTIVE}}}{word_clean}")
                    else:
                        parts.append(f"{{\\c{COL_FUTURE}}}{word_clean}")
                raw.append((t_start, t_end_raw, " ".join(parts)))

        # Enforce strict zero-overlap
        raw.sort(key=lambda e: e[0])
        safe = []
        for i, (ts, te, txt) in enumerate(raw):
            if i < len(raw) - 1:
                te = min(te, raw[i + 1][0] - 0.001)
            te = max(te, ts + 0.020)
            safe.append((ts, te, txt))

        lines = [header]
        for ts, te, txt in safe:
            lines.append(f"Dialogue: 0,{self._format_ass_time(ts)},{self._format_ass_time(te)},Cap,,0,0,0,,{txt}")

        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print(f"[VIDEO COMPOSER] Wrote {len(safe)} zero-overlap ASS subtitle events -> {ass_path}")
        return ass_path

    def compose_video(
        self,
        scene_images: List[Path],
        vo_path: Path,
        bed_path: Path,
        words: List[Dict[str, Any]],
        vo_dur: float,
        output_mp4_path: Path,
    ) -> Path:
        """
        Executes the master FFmpeg render assembling video, transitions, subtitles, and ducked audio.
        """
        ass_path = output_mp4_path.parent / (output_mp4_path.stem + "_captions.ass")
        self.build_subtitles(words, ass_path)

        n = len(scene_images)
        trans_dur = 0.40
        step = (vo_dur + trans_dur) / n
        clip_dur = step + trans_dur
        offsets = [round((k + 1) * step, 3) for k in range(n - 1)]
        frames = int(clip_dur * VIDEO_FPS)

        input_args = []
        filter_parts = []

        for i, item in enumerate(scene_images):
            item_path = Path(item)
            is_video = item_path.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")

            if is_video:
                input_args.extend(["-t", f"{clip_dur:.3f}", "-i", str(item_path)])
                filter_parts.append(
                    f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},"
                    f"format=yuv420p[v{i}]"
                )
            else:
                input_args.extend(["-loop", "1", "-t", f"{clip_dur:.3f}", "-i", str(item_path)])
                # Symmetrical match-cut forward drift on opening and ending shots
                if i == 0 or i == n - 1:
                    z_expr = "min(pzoom+0.0016,1.24)"
                    x_expr = "iw/2-(iw/zoom/2)"
                    y_expr = "ih/2-(ih/zoom/2)"
                elif i % 2 == 1:
                    z_expr = "max(1.20-0.0014*on,1.0)"
                    x_expr = "iw/2-(iw/zoom/2)+sin(on/35)*16"
                    y_expr = "ih/2-(ih/zoom/2)"
                else:
                    z_expr = "min(pzoom+0.0013,1.18)"
                    x_expr = "iw/2-(iw/zoom/2)-sin(on/35)*14"
                    y_expr = "ih/2-(ih/zoom/2)+sin(on/45)*8"

                filter_parts.append(
                    f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={VIDEO_FPS},"
                    f"format=yuv420p[v{i}]"
                )

        transitions = [
            "zoomin", "dissolve", "smoothleft", "fadeblack",
            "zoomin", "dissolve", "smoothright", "fade",
            "zoomin", "dissolve", "smoothleft", "fadeblack"
        ]

        curr = "[v0]"
        for k in range(n - 1):
            nxt = f"[v{k+1}]"
            out = f"[x{k}]" if k < n - 2 else "[video_raw]"
            tt = transitions[k % len(transitions)]
            off = offsets[k]
            filter_parts.append(f"{curr}{nxt}xfade=transition={tt}:duration={trans_dur}:offset={off}{out}")
            curr = out

        # Audio inputs
        input_args.extend(["-i", str(vo_path), "-i", str(bed_path)])
        vo_idx = n
        bg_idx = n + 1

        filter_parts.append(
            f"[{vo_idx}:a]highpass=f=80,equalizer=f=3200:width_type=q:w=1.5:g=-2.5,asplit[v_main][v_sc];"
            f"[{bg_idx}:a][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
            f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2,"
            f"loudnorm=I=-14:LRA=7:tp=-1.5[a_final]"
        )

        ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts_dir_str = str(self.fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")

        filter_parts.append(
            f"[video_raw]eq=contrast=1.07:brightness=0.008:saturation=1.08,"
            f"noise=alls=7:allf=t+u,"
            f"vignette=PI/4.2:aspect=9/16,"
            f"subtitles='{ass_str}':fontsdir='{fonts_dir_str}'[v_final]"
        )

        full_filter = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-filter_complex", full_filter,
            "-map", "[v_final]",
            "-map", "[a_final]",
            "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF),
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{vo_dur:.2f}",
            str(output_mp4_path)
        ]

        print(f"[VIDEO COMPOSER] Executing master render -> {output_mp4_path}...")
        subprocess.run(cmd, check=True)
        size_mb = os.path.getsize(output_mp4_path) / (1024 * 1024)
        print(f"[VIDEO COMPOSER] SUCCESS! Master video rendered: {output_mp4_path} ({size_mb:.2f} MB)")
        return output_mp4_path
