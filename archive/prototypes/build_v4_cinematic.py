import os
import subprocess
import asyncio
import numpy as np
import soundfile as sf
import edge_tts

# 1. GENERATE SPEECH & WORD BOUNDARIES
async def get_words():
    text = (
        "He thought he was remodeling his basement. But behind a fake stone wall... lay a tunnel descending 280 feet into solid volcanic rock. "
        "What he stumbled into in 1963 was Derinkuyu: an 18-story subterranean metropolis that once sheltered 20,000 people from the surface world. "
        "It was carved with fresh air ventilation shafts, underground wells, and 1,000-pound rolling stone doors lockable only from the inside. "
        "Archaeologists still cannot agree who engineered it... or what ancient catastrophe forced an entire population to live in the dark."
    )
    c = edge_tts.Communicate(text, 'en-US-ChristopherNeural', rate='-2%', pitch='-2Hz', boundary='WordBoundary')
    words = []
    with open('voiceover_v4.mp3', 'wb') as f:
        async for chunk in c.stream():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({
                    'word': chunk['text'],
                    'start': chunk['offset'] / 10000000.0,
                    'dur': chunk['duration'] / 10000000.0
                })
    return words

def format_ass_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100: cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# 2. GENERATE STRICTLY SINGLE-LINE CAPTIONS WITH SLEEK DOCUMENTARY PILL GRAPHIC
def generate_single_line_pill_ass(words, filename='single_line_pill.ass'):
    print("[1/4] Compiling strictly single-line captions with sleek Netflix-style pill background...")
    # BorderStyle=3: Creates a sleek semi-transparent dark pill box behind the text
    # Outline=10: Sets generous padding around the text
    # MarginV=480: Locked to a fixed baseline in the golden safe zone
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; PrimaryColour = White text (&H00FFFFFF)
; OutlineColour = Pitch Black border (&H00000000)
; BackColour = Sleek Semi-Transparent Dark Charcoal Pill Box (&HB0000000)
; BorderStyle = 3 (Opaque Background Box / Pill)
; MarginV = 480 (Permanent single-line baseline)
Style: SingleLinePill,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&HB0121212,-1,0,0,0,100,100,1,0,3,12,0,2,60,60,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Strict single-line chunker: Max 3 words or max 20 chars per burst (GUARANTEED single line, zero wrapping!)
    phrases = []
    curr = []
    for w in words:
        candidate = " ".join([x['word'] for x in curr] + [w['word']])
        if len(curr) >= 3 or len(candidate) > 20:
            if curr:
                phrases.append(curr)
            curr = [w]
        else:
            curr.append(w)
    if curr:
        phrases.append(curr)

    tag_active = r'{\c&H0000D7FF&}'  # Electric Warm Gold for active word
    tag_past = r'{\c&H00FFFFFF&}'    # Pure Clean White for past words
    tag_future = r'{\c&H00808080&}'  # Soft Muted Gray for upcoming words

    dialogues = []
    for p in phrases:
        for i, target_word in enumerate(p):
            t_start = target_word['start']
            if i < len(p) - 1:
                t_end = p[i + 1]['start']
            else:
                t_end = target_word['start'] + target_word['dur'] + 0.14
                
            line_parts = []
            for j, w in enumerate(p):
                word_clean = w['word'].upper()
                if j == i:
                    line_parts.append(f"{tag_active}{word_clean}")
                elif j < i:
                    line_parts.append(f"{tag_past}{word_clean}")
                else:
                    line_parts.append(f"{tag_future}{word_clean}")
                    
            text_line = " ".join(line_parts)
            dialogues.append(
                f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},SingleLinePill,,0,0,0,,{text_line}"
            )

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(header + "\n".join(dialogues) + "\n")
    print(f"Generated {len(dialogues)} strictly single-line events in {filename}.")

# 3. COMPOSE AUDIO (DIEGETIC HAMMER + STONE DOOR + AMBIENT BED)
def ensure_audio(duration=36.5):
    if os.path.exists('ambient_bed_v3.wav'):
        return 'ambient_bed_v3.wav'
    sr = 44100
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    drone = 0.28 * np.sin(2 * np.pi * 55 * t) + 0.18 * np.sin(2 * np.pi * 82.4 * t + 0.3)
    audio = drone * 0.25
    stereo = np.column_stack((audio, audio))
    sf.write('ambient_bed_v3.wav', stereo, sr)
    return 'ambient_bed_v3.wav'

# 4. RENDER V4 CINEMATIC MASTER
def render_v4():
    words = asyncio.run(get_words())
    generate_single_line_pill_ass(words, 'single_line_pill.ass')
    bg_audio = ensure_audio()
    
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
        'assets/scene6_cappadocia.jpg'
    ]
    
    clip_dur = 3.10
    trans_dur = 0.35
    step = clip_dur - trans_dur
    offsets = [round((k + 1) * step, 2) for k in range(len(scenes) - 1)]
    
    input_args = []
    filter_parts = []
    
    for i, img in enumerate(scenes):
        input_args.extend(['-loop', '1', '-t', str(clip_dur), '-i', img])
        frames = int(clip_dur * 30)
        if i % 3 == 0:
            z_expr = "min(pzoom+0.0016,1.22)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif i % 3 == 1:
            z_expr = "max(1.20-0.0016*on,1.0)"
            x_expr = "iw/2-(iw/zoom/2)+sin(on/30)*20"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            z_expr = "min(pzoom+0.0014,1.20)"
            x_expr = "iw/2-(iw/zoom/2)-sin(on/30)*15"
            y_expr = "ih/2-(ih/zoom/2)+sin(on/45)*10"
            
        filter_parts.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1080x1920:fps=30,"
            f"format=yuv420p[v{i}]"
        )
        
    transitions = [
        'zoomin', 'dissolve', 'smoothleft', 'fadeblack',
        'zoomin', 'dissolve', 'smoothright', 'fade',
        'zoomin', 'dissolve', 'smoothleft', 'fadeblack'
    ]
    
    curr = "[v0]"
    for k in range(len(scenes) - 1):
        nxt = f"[v{k+1}]"
        out = f"[x{k}]" if k < len(scenes) - 2 else "[video_stitched]"
        t_type = transitions[k % len(transitions)]
        off = offsets[k]
        filter_parts.append(f"{curr}{nxt}xfade=transition={t_type}:duration={trans_dur}:offset={off}{out}")
        curr = out
        
    # Audio inputs: Voiceover (#13) and Ambient bed (#14)
    input_args.extend(['-i', 'voiceover_v4.mp3', '-i', bg_audio])
    vo_idx = len(scenes)
    bg_idx = len(scenes) + 1
    
    filter_parts.append(
        f"[{vo_idx}:a]asplit[v_main][v_sc];"
        f"[{bg_idx}:a][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
    )
    
    # Visual Finish: Clean frame (NO top telemetry clutter), subtle grain, vignette, single-line pill captions
    sub_path = os.path.abspath('single_line_pill.ass').replace('\\', '/').replace(':', '\\:')
    filter_parts.append(
        f"[video_stitched]eq=contrast=1.06:brightness=0.01:saturation=1.10,"
        f"noise=alls=8:allf=t+u,"
        f"vignette=PI/4.5:aspect=9/16,"
        f"subtitles='{sub_path}'[v_final]"
    )
    
    full_filter = ";".join(filter_parts)
    output_mp4 = 'derinkuyu_v4_cinematic.mp4'
    
    cmd = [
        'ffmpeg', '-y',
        *input_args,
        '-filter_complex', full_filter,
        '-map', '[v_final]',
        '-map', '[a_final]',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '19',
        '-c:a', 'aac', '-b:a', '192k',
        '-shortest',
        output_mp4
    ]
    
    print("[2/4] Executing V4 Cinematic Master Render with single-line pill captions...")
    subprocess.run(cmd, check=True)
    
    size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
    print(f"\nSUCCESS! Rendered V4 Cinematic Master: {output_mp4} ({size_mb:.2f} MB)")

if __name__ == '__main__':
    render_v4()
