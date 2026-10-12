import os
import subprocess
import asyncio
import numpy as np
import soundfile as sf
import edge_tts

# 1. GENERATE VOICEOVER & MILLISECOND WORD BOUNDARIES
async def generate_voice_and_words():
    text = (
        "In 1963, a man in Turkey knocked down a wall in his basement... and uncovered a tunnel leading into complete darkness. "
        "What he stumbled upon was Derinkuyu: an 18-story underground city plunging nearly 300 feet into solid volcanic rock. "
        "It was engineered with fresh air ventilation shafts, water wells, and 1,000-pound rolling stone doors that could only be locked from the inside. "
        "Over 20,000 people could vanish here for months. But historians still cannot agree who built it... or what terrified an entire civilization into hiding underground."
    )
    
    print("[1/5] Synthesizing voiceover with millisecond word boundaries...")
    communicate = edge_tts.Communicate(text, 'en-US-ChristopherNeural', rate='-2%', pitch='-2Hz', boundary='WordBoundary')
    words = []
    with open('voiceover_v2.mp3', 'wb') as f:
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({
                    'word': chunk['text'],
                    'start': chunk['offset'] / 10000000.0,
                    'dur': chunk['duration'] / 10000000.0
                })
    print(f"Captured {len(words)} word boundaries.")
    return words

def format_ass_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# 2. GENERATE DYNAMIC RUNNING-SPEECH ACTIVE-WORD HIGHLIGHT SUBTITLES
def generate_running_speech_ass(words, filename='karaoke_v2.ass'):
    print("[2/5] Compiling Dynamic Running-Speech Active-Highlight ASS subtitles...")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; MarginV = 540 (Safe Zone: Centered well above Facebook/IG mobile interface overlays)
Style: RunningSpeech,Impact,74,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,5,3,2,60,60,540,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Group words into natural running phrases of 3 to 4 words
    phrases = []
    curr = []
    for w in words:
        curr.append(w)
        if len(curr) >= 4 or (any(w['word'].endswith(p) for p in ['.', ',', ':', '...']) and len(curr) >= 3):
            phrases.append(curr)
            curr = []
    if curr:
        phrases.append(curr)

    # Style tags
    tag_active = r'{\c&H0000E6FF&\fscx114\fscy114}'  # Electric Yellow + 114% scale pop
    tag_reset = r'{\c&H00FFFFFF&\fscx100\fscy100}'   # Return to Crisp White
    tag_past = r'{\c&H00FFFFFF&}'                     # Already spoken: Clean White
    tag_future = r'{\c&H00999999&}'                   # Anticipation / Running speech: Dim Gray

    dialogues = []
    for p in phrases:
        for i, target_word in enumerate(p):
            t_start = target_word['start']
            # End time extends to next word's start, or end of current word + buffer
            if i < len(p) - 1:
                t_end = p[i + 1]['start']
            else:
                t_end = target_word['start'] + target_word['dur'] + 0.18
                
            line_parts = []
            for j, w in enumerate(p):
                word_clean = w['word'].upper()
                if j == i:
                    line_parts.append(f"{tag_active}{word_clean}{tag_reset}")
                elif j < i:
                    line_parts.append(f"{tag_past}{word_clean}")
                else:
                    line_parts.append(f"{tag_future}{word_clean}")
                    
            text_line = " ".join(line_parts)
            dialogues.append(
                f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},RunningSpeech,,0,0,0,,{text_line}"
            )

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(header + "\n".join(dialogues) + "\n")
    print(f"Generated {len(dialogues)} active word highlight events in {filename}.")

# 3. SYNTHESIZE MASTER AUDIO WITH SIDECHAIN DUCKING & TRANSITION FOLEY
def generate_master_audio(transition_offsets, duration=38.0):
    print("[3/5] Synthesizing acoustic layer with transition Foley...")
    sr = 44100
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    
    # Atmospheric cinematic pad (55Hz + 110Hz + slow LFO drift)
    drone = (
        0.30 * np.sin(2 * np.pi * 55 * t) +
        0.20 * np.sin(2 * np.pi * 82.4 * t + 0.3) +
        0.12 * np.sin(2 * np.pi * 110 * t + 0.7)
    )
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.12 * t)
    audio = drone * (0.65 + 0.35 * lfo)
    
    # Opening Sub-Bass Impact Hit (0.0s to 2.2s)
    hit_t = np.linspace(0, 2.2, int(sr * 2.2), endpoint=False)
    hit_freq = 95 * np.exp(-3.2 * hit_t) + 38
    hit_amp = np.exp(-2.5 * hit_t)
    audio[:len(hit_t)] += 0.55 * np.sin(2 * np.pi * hit_freq * hit_t) * hit_amp
    
    # Transition Foley Whooshes at each xfade offset
    whoosh_dur = 0.40
    whoosh_t = np.linspace(0, whoosh_dur, int(sr * whoosh_dur), endpoint=False)
    whoosh_env = np.sin(np.pi * (whoosh_t / whoosh_dur)) ** 2  # smooth bell curve
    np.random.seed(42)
    noise = np.random.uniform(-0.15, 0.15, len(whoosh_t))
    whoosh_sfx = np.convolve(noise, np.ones(25)/25, mode='same') * whoosh_env * 0.45
    sub_swell = 0.25 * np.sin(2 * np.pi * 65 * whoosh_t) * whoosh_env
    trans_hit = whoosh_sfx + sub_swell
    
    for offset in transition_offsets:
        idx_start = int(offset * sr)
        idx_end = idx_start + len(trans_hit)
        if idx_end < len(audio):
            audio[idx_start:idx_end] += trans_hit
            
    # Fade in / out
    fade_in = int(sr * 0.8)
    fade_out = int(sr * 2.0)
    audio[:fade_in] *= np.linspace(0, 1, fade_in)
    audio[-fade_out:] *= np.linspace(1, 0, fade_out)
    
    # Stereo & scale
    audio = audio * 0.28
    stereo = np.column_stack((audio, audio))
    sf.write('ambient_bed_v2.wav', stereo, sr)
    print("Synthesized ambient_bed_v2.wav with Foley hits.")

# 4. RENDER MASTER V2 VIDEO WITH XFADE TRANSITIONS, GRAIN, VIGNETTE & RUNNING SUBTITLES
def render_v2_video():
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
    
    clip_dur = 3.20
    trans_dur = 0.35
    step = clip_dur - trans_dur  # 2.85s
    offsets = [round((k + 1) * step, 2) for k in range(len(scenes) - 1)]
    
    # Synthesize audio with Foley aligned to these exact offsets
    generate_master_audio(offsets, duration=37.6)
    
    print(f"[4/5] Rendering 13-scene visual sequence with xfade transitions ({clip_dur}s per clip, {trans_dur}s xfade)...")
    
    input_args = []
    filter_parts = []
    
    for i, img in enumerate(scenes):
        input_args.extend(['-loop', '1', '-t', str(clip_dur), '-i', img])
        frames = int(clip_dur * 30)
        if i % 3 == 0:
            z_expr = "min(pzoom+0.0018,1.25)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif i % 3 == 1:
            z_expr = "max(1.22-0.0018*on,1.0)"
            x_expr = "iw/2-(iw/zoom/2)+sin(on/30)*25"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            z_expr = "min(pzoom+0.0015,1.22)"
            x_expr = "iw/2-(iw/zoom/2)-sin(on/30)*20"
            y_expr = "ih/2-(ih/zoom/2)+sin(on/45)*15"
            
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
    input_args.extend(['-i', 'voiceover_v2.mp3', '-i', 'ambient_bed_v2.wav'])
    vo_idx = len(scenes)
    bg_idx = len(scenes) + 1
    
    filter_parts.append(
        f"[{vo_idx}:a]asplit[v_main][v_sc];"
        f"[{bg_idx}:a][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
    )
    
    # Final Visual Grade: Vignette + 35mm Film Grain + Contrast boost + Running Speech Subtitles
    sub_path = os.path.abspath('karaoke_v2.ass').replace('\\', '/').replace(':', '\\:')
    filter_parts.append(
        f"[video_stitched]eq=contrast=1.06:brightness=0.01:saturation=1.12,"
        f"noise=alls=10:allf=t+u,"
        f"vignette=PI/4:aspect=9/16,"
        f"subtitles='{sub_path}'[v_final]"
    )
    
    full_filter = ";".join(filter_parts)
    output_mp4 = 'derinkuyu_v2_master.mp4'
    
    cmd = [
        'ffmpeg', '-y',
        *input_args,
        '-filter_complex', full_filter,
        '-map', '[v_final]',
        '-map', '[a_final]',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
        '-c:a', 'aac', '-b:a', '192k',
        '-shortest',
        output_mp4
    ]
    
    print("[5/5] Executing master single-pass FFmpeg compositing pipeline...")
    subprocess.run(cmd, check=True)
    
    size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
    print(f"\nSUCCESS! Rendered V2 Master: {output_mp4} ({size_mb:.2f} MB)")

def main():
    words = asyncio.run(generate_voice_and_words())
    generate_running_speech_ass(words, 'karaoke_v2.ass')
    render_v2_video()

if __name__ == '__main__':
    main()
