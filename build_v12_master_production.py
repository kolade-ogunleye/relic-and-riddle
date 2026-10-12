"""
build_v12_master_production.py
==============================
The Master Production Cut of Derinkuyu:
1. SCRIPT: 104 words, ~42 seconds (in the 38-50s Golden Monetization Zone).
   Self-contained circular loop:
   Ending: "To this day, nobody knows who built this impossible world—all we know is how it was found."
   Restart: "In 1963, a simple basement renovation in Turkey uncovered the largest underground city in human history..."
2. NARRATIVE VALUE: 100% of historical and architectural facts preserved.
3. AUDIO: Seamless ambient drone + diegetic hammer strike & heavy rolling stone door SFX + sidechain ducking.
4. TYPOGRAPHY: Anton Regular running-speech single-line pill captions, MarginV=540px (Meta UI Safe Zone).
5. VISUALS: 13 archival scenes, forward match-cut camera drift, cinematic xfade transitions, subtle grain & vignette.
"""

import os
import subprocess
import asyncio
import numpy as np
import soundfile as sf
import edge_tts

SCRIPT_TEXT = (
    "In 1963, a simple basement renovation in Turkey uncovered the largest underground city in human history. "
    "Behind a knocked-down wall lay Derinkuyu: an 18-story subterranean metropolis carved 280 feet into solid volcanic rock. "
    "It was engineered with thousands of fresh air ventilation shafts, underground water wells, "
    "and massive 1,000-pound rolling stone doors lockable only from the inside. "
    "It was built to shelter twenty thousand people from an unknown surface catastrophe—"
    "yet archaeologists still cannot agree who engineered it, or why an entire population fled into the dark. "
    "To this day, nobody knows who built this impossible world—all we know is how it was found."
)

VOICE        = 'en-US-ChristopherNeural'
AUDIO_FILE   = 'voiceover_v12_master.mp3'
ASS_FILE     = 'v12_captions_master.ass'
AMBIENT_FILE = 'ambient_v12_master.wav'
OUTPUT_MP4   = 'derinkuyu_v12_master.mp4'
FONTS_DIR    = os.path.abspath('fonts')
FONT_NAME    = 'Anton'
FONT_SIZE    = 92   # High-impact Anton punch
MARGIN_V     = 540  # Locked at 540px to safely clear all Meta UI overlays

COL_ACTIVE   = r'&H0000D7FF&'   # Warm Gold #FFD700
COL_PAST     = r'&H00FFFFFF&'   # White
COL_FUTURE   = r'&H00909090&'   # Soft Muted Gray
COL_BACK     = r'&HC8101010&'   # Translucent Charcoal Pill

async def generate_voiceover(audio_path):
    print("[1/4] Generating voiceover with perfected circular narrative script...")
    c = edge_tts.Communicate(SCRIPT_TEXT, VOICE, rate='-2%', pitch='-2Hz', boundary='WordBoundary')
    words = []
    with open(audio_path, 'wb') as f:
        async for chunk in c.stream():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({
                    'word': chunk['text'],
                    'start': chunk['offset'] / 10000000.0,
                    'dur': chunk['duration'] / 10000000.0
                })
    words.sort(key=lambda w: w['start'])
    return words

def format_ass_time(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100: cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def build_ass(words, ass_path):
    print("[2/4] Compiling zero-overlap ASS captions for master cut...")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT_NAME},{FONT_SIZE},{COL_PAST},&H000000FF,&H00000000,{COL_BACK},-1,0,0,0,100,100,0.5,0,3,14,0,2,40,40,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Max 2 words or 13 chars per burst for 92pt Anton
    phrases = []
    cur = []
    for w in words:
        candidate = ' '.join(x['word'] for x in cur + [w])
        if len(cur) >= 2 or len(candidate) > 13:
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
            next_phrase_start = phrases[pi + 1][0]['start']
        else:
            next_phrase_start = phrase[-1]['start'] + phrase[-1]['dur'] + 0.10

        for wi, target in enumerate(phrase):
            t_start = target['start']
            t_end_raw = phrase[wi + 1]['start'] if wi < len(phrase) - 1 else next_phrase_start

            parts = []
            for ji, w in enumerate(phrase):
                word_clean = w['word'].upper()
                if ji < wi:
                    parts.append(f"{{\\c{COL_PAST}}}{word_clean}")
                elif ji == wi:
                    parts.append(f"{{\\c{COL_ACTIVE}}}{word_clean}")
                else:
                    parts.append(f"{{\\c{COL_FUTURE}}}{word_clean}")
            raw.append((t_start, t_end_raw, ' '.join(parts)))

    raw.sort(key=lambda e: e[0])
    safe = []
    for i, (ts, te, txt) in enumerate(raw):
        if i < len(raw) - 1:
            te = min(te, raw[i + 1][0] - 0.001)
        te = max(te, ts + 0.020)
        safe.append((ts, te, txt))

    lines = [header]
    for ts, te, txt in safe:
        lines.append(f"Dialogue: 0,{format_ass_time(ts)},{format_ass_time(te)},Cap,,0,0,0,,{txt}")

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"[ASS] Generated {len(safe)} zero-overlap dialogue events.")

def generate_master_audio(duration, audio_out, words):
    """
    Generates seamless ambient subterranean drone with accurately timed diegetic SFX:
    - Hammer strike at t=0.0s (opening renovation beat / restart hook)
    - Stone rolling door SFX on 'rolling stone doors' beat
    """
    sr = 44100
    total_samples = int(sr * duration)
    t = np.linspace(0, duration, total_samples, endpoint=False)
    
    # Subterranean continuous drone (55Hz / 82.5Hz harmonic)
    drone = 0.24 * np.sin(2 * np.pi * 55.0 * t) + 0.16 * np.sin(2 * np.pi * 82.5 * t)
    
    # 1. Diegetic Hammer Strike at t=0.0s
    hammer_path = 'sfx_hammer.wav'
    if os.path.exists(hammer_path):
        h_data, _ = sf.read(hammer_path)
        if len(h_data.shape) > 1: h_data = h_data[:, 0]
        h_len = min(len(h_data), total_samples)
        drone[:h_len] += h_data[:h_len] * 0.40
        
    # 2. Diegetic Stone Door SFX timed to the word 'rolling' or 'doors'
    stone_path = 'sfx_stone_door.wav'
    door_time = 18.0  # default
    for w in words:
        if 'rolling' in w['word'].lower() or 'doors' in w['word'].lower():
            door_time = w['start']
            break
            
    if os.path.exists(stone_path):
        s_data, _ = sf.read(stone_path)
        if len(s_data.shape) > 1: s_data = s_data[:, 0]
        s_start_idx = int(door_time * sr)
        s_len = min(len(s_data), total_samples - s_start_idx)
        if s_len > 0:
            drone[s_start_idx:s_start_idx + s_len] += s_data[:s_len] * 0.45
            
    audio = drone * 0.22
    stereo = np.column_stack((audio, audio))
    sf.write(audio_out, stereo, sr)
    print(f"[AUDIO] Master ambient bed generated -> {audio_out}")

def render_v12():
    words = asyncio.run(generate_voiceover(AUDIO_FILE))
    build_ass(words, ASS_FILE)
    
    cmd_probe = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', AUDIO_FILE]
    vo_dur = float(subprocess.check_output(cmd_probe).decode().strip())
    print(f"[VO] Duration: {vo_dur:.2f}s (Optimal 38-50s zone)")
    
    generate_master_audio(vo_dur, AMBIENT_FILE, words)
    
    scenes = [
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
        'assets/scene2_tunnel.jpg',   # Match-cut: tunnel push forward matching opening stairs
    ]
    
    n = len(scenes)
    trans_dur = 0.40
    step = (vo_dur + trans_dur) / n
    clip_dur = step + trans_dur
    
    offsets = [round((k + 1) * step, 3) for k in range(n - 1)]
    frames = int(clip_dur * 30)
    
    input_args = []
    filter_parts = []
    
    for i, img in enumerate(scenes):
        input_args.extend(['-loop', '1', '-t', f"{clip_dur:.3f}", '-i', img])
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
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1080x1920:fps=30,"
            f"format=yuv420p[v{i}]"
        )
        
    transitions = [
        'zoomin', 'dissolve', 'smoothleft', 'fadeblack',
        'zoomin', 'dissolve', 'smoothright', 'fade',
        'zoomin', 'dissolve', 'smoothleft', 'fadeblack',
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
    input_args.extend(['-i', AUDIO_FILE, '-i', AMBIENT_FILE])
    vo_idx = n
    bg_idx = n + 1
    
    filter_parts.append(
        f"[{vo_idx}:a]asplit[v_main][v_sc];"
        f"[{bg_idx}:a][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
    )
    
    ass_path = os.path.abspath(ASS_FILE).replace('\\', '/').replace(':', '\\:')
    fonts_dir = FONTS_DIR.replace('\\', '/').replace(':', '\\:')
    
    filter_parts.append(
        f"[video_raw]eq=contrast=1.07:brightness=0.008:saturation=1.08,"
        f"noise=alls=7:allf=t+u,"
        f"vignette=PI/4.2:aspect=9/16,"
        f"subtitles='{ass_path}':fontsdir='{fonts_dir}'[v_final]"
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
        '-t', f"{vo_dur:.2f}",
        OUTPUT_MP4
    ]
    
    print("[3/4] Rendering V12 Master Production Video...")
    subprocess.run(cmd, check=True)
    print(f"\n[4/4] SUCCESS! Master Production Cut rendered -> {OUTPUT_MP4}")

if __name__ == '__main__':
    render_v12()
