"""
build_v5_final.py -- V5 FINAL MASTER
====================================
Key fixes over V4:
  1. ZERO-OVERLAP caption architecture: a strict second-pass enforces
     event[i].end <= event[i+1].start for EVERY consecutive pair,
     eliminating the two-layer libass rendering bug completely.
  2. Font: Montserrat ExtraBold (modern documentary journalism -- clean,
     legible, proportional at mobile 9:16 sizes). Falls back to
     BebasNeue-Regular if Montserrat is missing.
  3. Caption size: 44pt (not too big, not too small for 1080x1920).
  4. Single horizontal line locked at MarginV=430 (golden lower-third).
  5. No scale-popping, no vertical position shift.
  6. Running-speech colours: gold active, white past, gray future.
  7. All other V4 wins retained: 13 scenes, xfade variety, diegetic SFX,
     sidechain ducking, cinematic grain+vignette+contrast.
"""

import os
import re
import subprocess
import asyncio
import numpy as np
import soundfile as sf
import edge_tts


# -----------------------------------------------------------------------------
# 1. GENERATE SPEECH + WORD BOUNDARIES
# -----------------------------------------------------------------------------
SCRIPT_TEXT = (
    "He thought he was remodeling his basement. But behind a fake stone wall... "
    "lay a tunnel descending 280 feet into solid volcanic rock. "
    "What he stumbled into in 1963 was Derinkuyu: an 18-story subterranean metropolis "
    "that once sheltered 20,000 people from the surface world. "
    "It was carved with fresh air ventilation shafts, underground wells, "
    "and 1,000-pound rolling stone doors lockable only from the inside. "
    "Archaeologists still cannot agree who engineered it... "
    "or what ancient catastrophe forced an entire population to live in the dark."
)

VOICE          = 'en-US-ChristopherNeural'
AUDIO_FILE     = 'voiceover_v5.mp3'
ASS_FILE       = 'v10_captions_120pt.ass'
AMBIENT_FILE   = 'ambient_bed_v3.wav'
OUTPUT_MP4     = 'derinkuyu_v10_anton120.mp4'

FONTS_DIR      = os.path.abspath('fonts')
# Preferred font (Montserrat variable = ExtraBold weight 800 via \b1 tag)
FONT_NAME      = 'Anton'
FONT_FILE_PREF = os.path.join(FONTS_DIR, 'Anton-Regular.ttf')
FONT_FILE_FALL = os.path.join(FONTS_DIR, 'Montserrat-Variable.ttf')

# Caption style constants
FONT_SIZE      = 120          # pt -- balanced for 1080×1920
MARGIN_V       = 430         # px from bottom -- locked golden lower-third
# Colours in ASS BGR hex format
COL_ACTIVE     = r'&H0000D7FF&'   # warm gold  #FFD700 -> BGR 00D7FF
COL_PAST       = r'&H00FFFFFF&'   # clean white
COL_FUTURE     = r'&H00909090&'   # muted gray (slightly lighter than v4)
COL_BACK       = r'&HC8101010&'   # near-black pill, 78% opacity
COL_OUTLINE    = r'&H00000000&'   # black outline


# -----------------------------------------------------------------------------
# 2. TTS + WORD BOUNDARIES
# -----------------------------------------------------------------------------
async def get_words(audio_out: str) -> list[dict]:
    """Stream edge-tts, capture WordBoundary events, return sorted word list."""
    c = edge_tts.Communicate(SCRIPT_TEXT, VOICE, rate='-2%', pitch='-2Hz',
                              boundary='WordBoundary')
    words = []
    with open(audio_out, 'wb') as f:
        async for chunk in c.stream():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({
                    'word':  chunk['text'],
                    'start': chunk['offset']   / 10_000_000.0,
                    'dur':   chunk['duration'] / 10_000_000.0,
                })
    # Sort by start time (should already be sorted, but guarantee it)
    words.sort(key=lambda w: w['start'])
    return words


# -----------------------------------------------------------------------------
# 3. ASS CAPTION GENERATOR -- ZERO-OVERLAP ARCHITECTURE
# -----------------------------------------------------------------------------
def _t(sec: float) -> str:
    """Format seconds -> ASS H:MM:SS.CC timecode."""
    sec = max(0.0, sec)
    h   = int(sec // 3600)
    m   = int((sec % 3600) // 60)
    s   = int(sec % 60)
    cs  = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _choose_font() -> str:
    if os.path.exists(FONT_FILE_PREF):
        return FONT_NAME
    if os.path.exists(FONT_FILE_FALL):
        return 'Bebas Neue'
    return 'Arial'   # hard fallback


def build_ass(words: list[dict], ass_path: str) -> None:
    """
    Build a zero-overlap .ass subtitle file with running-speech highlighting.

    Algorithm
    ---------
    Phase 1 -- Chunk words into short display phrases (≤3 words, ≤22 chars).
    Phase 2 -- For each phrase, emit one Dialogue event per word:
               the active word is gold, previous words are white, next gray.
               Within a phrase: event[i].end = event[i+1].start (no gap, no overlap).
               At phrase boundaries: last-word-end = first-word-of-next-phrase.start.
    Phase 3 -- Global safety pass: enforce event[i].end ≤ event[i+1].start − 1 ms
               for ALL consecutive event pairs. Truncate any violator.
    """
    font = _choose_font()
    print(f"[FONT] Using: {font}")

    # -- ASS header ----------------------------------------------------------
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{FONT_SIZE},{COL_PAST},&H000000FF,{COL_OUTLINE},{COL_BACK},-1,0,0,0,100,100,0.5,0,3,15,0,2,30,30,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # -- Phase 1: chunk into phrases -----------------------------------------
    phrases: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        candidate = ' '.join(x['word'] for x in cur + [w])
        if len(cur) >= 2 or len(candidate) > 11:
            if cur:
                phrases.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        phrases.append(cur)

    # -- Phase 2: build raw dialogue events ----------------------------------
    # Pre-compute the start time of the FIRST word of every NEXT phrase so we
    # can correctly cap the last word of each phrase.
    raw: list[tuple[float, float, str]] = []   # (t_start, t_end_raw, text_line)

    for pi, phrase in enumerate(phrases):
        # What is the hard ceiling for the last word of this phrase?
        if pi < len(phrases) - 1:
            next_phrase_start = phrases[pi + 1][0]['start']
        else:
            # Last phrase: extend slightly past the last word's natural end
            next_phrase_start = phrase[-1]['start'] + phrase[-1]['dur'] + 0.12

        for wi, target in enumerate(phrase):
            t_start = target['start']

            # Within-phrase: end = next word's start; for last word: next phrase start
            if wi < len(phrase) - 1:
                t_end_raw = phrase[wi + 1]['start']
            else:
                t_end_raw = next_phrase_start

            # Build ASS text line with colour tags
            parts = []
            for ji, w in enumerate(phrase):
                word_upper = w['word'].upper()
                if ji < wi:
                    parts.append(f"{{\\c{COL_PAST}}}{word_upper}")
                elif ji == wi:
                    parts.append(f"{{\\c{COL_ACTIVE}}}{word_upper}")
                else:
                    parts.append(f"{{\\c{COL_FUTURE}}}{word_upper}")
            text_line = ' '.join(parts)

            raw.append((t_start, t_end_raw, text_line))

    # -- Phase 3: global zero-overlap safety pass -----------------------------
    # Sort by start time (phrases are ordered but enforce anyway)
    raw.sort(key=lambda e: e[0])

    safe: list[tuple[float, float, str]] = []
    for i, (ts, te, txt) in enumerate(raw):
        if i < len(raw) - 1:
            next_ts = raw[i + 1][0]
            te = min(te, next_ts - 0.001)   # hard 1 ms gap guarantee
        te = max(te, ts + 0.020)            # minimum 20 ms event duration
        safe.append((ts, te, txt))

    # -- Write file -----------------------------------------------------------
    lines = [header]
    for ts, te, txt in safe:
        lines.append(f"Dialogue: 0,{_t(ts)},{_t(te)},Cap,,0,0,0,,{txt}")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"[ASS] Wrote {len(safe)} zero-overlap events -> {ass_path}")

    # -- Verification: count any remaining overlaps ---------------------------
    overlaps = sum(
        1 for i in range(len(safe) - 1) if safe[i][1] > safe[i + 1][0]
    )
    if overlaps:
        print(f"[ASS] [WARN] WARNING: {overlaps} overlapping events remain -- check input data!")
    else:
        print("[ASS] [OK] Zero overlapping events confirmed.")


# -----------------------------------------------------------------------------
# 4. AMBIENT AUDIO BED (reuse if exists)
# -----------------------------------------------------------------------------
def ensure_ambient(path: str, duration: float = 38.0) -> str:
    if os.path.exists(path):
        print(f"[AUDIO] Reusing existing ambient bed: {path}")
        return path
    sr = 44100
    t  = np.linspace(0, duration, int(sr * duration), endpoint=False)
    drone = (0.28 * np.sin(2 * np.pi * 55 * t)
           + 0.18 * np.sin(2 * np.pi * 82.4 * t + 0.3))
    audio  = drone * 0.22
    stereo = np.column_stack((audio, audio))
    sf.write(path, stereo, sr)
    print(f"[AUDIO] Generated ambient bed: {path}")
    return path


# -----------------------------------------------------------------------------
# 5. VIDEO RENDER
# -----------------------------------------------------------------------------
SCENES = [
    'assets/scene1_stairs.jpg',
    'assets/scene07_ozkonak_entrance.jpg',
    'assets/scene2_tunnel.jpg',
    'assets/scene08_ozkonak_tunnel.jpg',
    'assets/scene3_door.jpg',
    'assets/scene09_kaymakli_arch.jpg',
    'assets/scene4_pillars.jpg',
    'assets/scene5_room.jpg',
    'assets/scene13_derinkuyu_church.jpg',
    'assets/scene10_goreme_town.jpg',
    'assets/scene11_panorama.jpg',
    'assets/scene12_uchisar.jpg',
    'assets/scene6_cappadocia.jpg',
]

CLIP_DUR   = 3.10   # seconds per scene (before overlap)
TRANS_DUR  = 0.40   # xfade transition length
STEP       = CLIP_DUR - TRANS_DUR

# Varied transitions for cinematic feel
TRANSITIONS = [
    'zoomin', 'dissolve', 'smoothleft', 'fadeblack',
    'zoomin', 'dissolve', 'smoothright', 'fade',
    'zoomin', 'dissolve', 'smoothleft', 'fadeblack',
]


def build_zoompan(i: int, frames: int) -> str:
    """Return zoompan expression string for scene index i."""
    if i % 3 == 0:
        # Slow push-in
        return (f"zoompan=z='min(pzoom+0.0014,1.20)'"
                f":x='iw/2-(iw/zoom/2)'"
                f":y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s=1080x1920:fps=30")
    elif i % 3 == 1:
        # Slow pull-back with slight drift right
        return (f"zoompan=z='max(1.18-0.0014*on,1.0)'"
                f":x='iw/2-(iw/zoom/2)+sin(on/35)*18'"
                f":y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s=1080x1920:fps=30")
    else:
        # Diagonal drift
        return (f"zoompan=z='min(pzoom+0.0012,1.18)'"
                f":x='iw/2-(iw/zoom/2)-sin(on/35)*14'"
                f":y='ih/2-(ih/zoom/2)+sin(on/50)*9'"
                f":d={frames}:s=1080x1920:fps=30")


def render(vo_path: str, ass_path: str, ambient_path: str, output: str) -> None:
    n      = len(SCENES)
    frames = int(CLIP_DUR * 30)

    input_args: list[str]   = []
    filter_parts: list[str] = []

    # -- Video inputs + per-scene zoompan ----------------------------------
    for i, img in enumerate(SCENES):
        input_args.extend(['-loop', '1', '-t', str(CLIP_DUR), '-i', img])
        zp = build_zoompan(i, frames)
        filter_parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,{zp},format=yuv420p[v{i}]"
        )

    # -- xfade chain -------------------------------------------------------
    offsets = [round((k + 1) * STEP, 3) for k in range(n - 1)]
    curr    = "[v0]"
    for k in range(n - 1):
        nxt  = f"[v{k+1}]"
        out  = f"[x{k}]" if k < n - 2 else "[video_raw]"
        tt   = TRANSITIONS[k % len(TRANSITIONS)]
        off  = offsets[k]
        filter_parts.append(
            f"{curr}{nxt}xfade=transition={tt}:duration={TRANS_DUR}:offset={off}{out}"
        )
        curr = out

    # -- Audio inputs ------------------------------------------------------
    input_args.extend(['-i', vo_path, '-i', ambient_path])
    vo_idx = n
    bg_idx = n + 1

    # Sidechain ducking: ambient ducks under voiceover
    filter_parts.append(
        f"[{vo_idx}:a]asplit[v_main][v_sc];"
        f"[{bg_idx}:a][v_sc]sidechaincompress="
        f"threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
    )

    # -- Visual finish: colour grade + grain + vignette + captions ---------
    # Escape the ASS path for FFmpeg filter syntax (Windows backslash -> forward,
    # colon after drive letter -> escaped with backslash)
    ass_abs = os.path.abspath(ass_path)
    ass_ffmpeg = ass_abs.replace('\\', '/').replace(':', '\\:')

    fonts_ffmpeg = FONTS_DIR.replace('\\', '/').replace(':', '\\:')

    filter_parts.append(
        f"[video_raw]"
        f"eq=contrast=1.07:brightness=0.008:saturation=1.08,"
        f"noise=alls=7:allf=t+u,"
        f"vignette=PI/4.2:aspect=9/16,"
        f"subtitles='{ass_ffmpeg}':fontsdir='{fonts_ffmpeg}'"
        f"[v_final]"
    )

    full_filter = ";".join(filter_parts)

    cmd = [
        'ffmpeg', '-y',
        *input_args,
        '-filter_complex', full_filter,
        '-map', '[v_final]',
        '-map', '[a_final]',
        '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-shortest',
        output,
    ]

    print(f"\n[RENDER] Executing FFmpeg -- this will take ~2-4 minutes …")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}")

    size_mb = os.path.getsize(output) / (1024 * 1024)
    print(f"\n[OK] SUCCESS  ->  {output}  ({size_mb:.2f} MB)")


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  DERINKUYU V10 -- Anton 120pt Single-Line Captions")
    print("=" * 60)

    # Step 1: TTS (skip if audio already exists from a previous run)
    if os.path.exists(AUDIO_FILE):
        print(f"[TTS] Reusing {AUDIO_FILE} -- delete to regenerate.")
        # Still need word boundaries for captions; re-run TTS silently
        # but write to a temp file so we don't overwrite the good audio
        tmp_audio = AUDIO_FILE + '.tmp'
        words = asyncio.run(get_words(tmp_audio))
        if os.path.exists(tmp_audio):
            os.remove(tmp_audio)
    else:
        print("[TTS] Generating voiceover …")
        words = asyncio.run(get_words(AUDIO_FILE))

    print(f"[TTS] {len(words)} word boundaries captured.")

    # Step 2: Build ASS captions
    build_ass(words, ASS_FILE)

    # Step 3: Ambient audio bed
    ensure_ambient(AMBIENT_FILE)

    # Step 4: Render
    render(AUDIO_FILE, ASS_FILE, AMBIENT_FILE, OUTPUT_MP4)


if __name__ == '__main__':
    main()
