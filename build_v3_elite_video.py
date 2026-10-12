import os
import subprocess
import asyncio
import numpy as np
import soundfile as sf
import edge_tts
from PIL import Image, ImageDraw, ImageFont

# 1. GENERATE RE-ENGINEERED HIGH-RETENTION HOOK SCRIPT
async def generate_voice_and_words():
    text = (
        "He thought he was remodeling his basement. But behind a fake stone wall... lay a tunnel descending 280 feet into solid volcanic rock. "
        "What he stumbled into in 1963 was Derinkuyu: an 18-story subterranean metropolis that once sheltered 20,000 people from the surface world. "
        "It was carved with fresh air ventilation shafts, underground wells, and 1,000-pound rolling stone doors lockable only from the inside. "
        "Archaeologists still cannot agree who engineered it... or what ancient catastrophe forced an entire population to live in the dark."
    )
    
    print("[1/5] Synthesizing upgraded high-retention narration with word boundaries...")
    communicate = edge_tts.Communicate(text, 'en-US-ChristopherNeural', rate='-2%', pitch='-2Hz', boundary='WordBoundary')
    words = []
    with open('voiceover_v3.mp3', 'wb') as f:
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                f.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append({
                    'word': chunk['text'],
                    'start': chunk['offset'] / 10000000.0,
                    'dur': chunk['duration'] / 10000000.0
                })
    print(f"Captured {len(words)} word boundaries. Total speech: {words[-1]['start'] + words[-1]['dur']:.2f}s")
    return words

def format_ass_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100: cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# 2. GENERATE SILKY-SMOOTH, ZERO-JITTER RUNNING SPEECH SUBTITLES
def generate_silky_running_subtitles(words, filename='karaoke_v3.ass'):
    print("[2/5] Compiling Zero-Jitter Proportional Running Speech Subtitles (Fontsize: 52)...")
    # Using Arial Bold / Trebuchet MS Bold for ultra-clean documentary typography
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Fontsize: 52 (Proportionate, elegant, not oversized)
; MarginV = 490 (Anchored in Golden Safe Zone, zero vertical jumping)
Style: DocumentaryRunning,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4,2,2,60,60,490,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Group words into clean 3-to-4 word phrases
    phrases = []
    curr = []
    for w in words:
        curr.append(w)
        if len(curr) >= 4 or (any(w['word'].endswith(p) for p in ['.', ',', ':', '...']) and len(curr) >= 3):
            phrases.append(curr)
            curr = []
    if curr:
        phrases.append(curr)

    # Style tags: ZERO scaling changes (\fscx\fscy removed) to guarantee ZERO reflow jitter!
    tag_active = r'{\c&H0000D7FF&}'  # Electric Warm Gold
    tag_past = r'{\c&H00FFFFFF&}'    # Pure Clean White
    tag_future = r'{\c&H00808080&}'  # Soft Anticipation Gray

    dialogues = []
    for p in phrases:
        for i, target_word in enumerate(p):
            t_start = target_word['start']
            if i < len(p) - 1:
                t_end = p[i + 1]['start']
            else:
                t_end = target_word['start'] + target_word['dur'] + 0.16
                
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
                f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},DocumentaryRunning,,0,0,0,,{text_line}"
            )

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(header + "\n".join(dialogues) + "\n")
    print(f"Generated {len(dialogues)} rock-solid subtitle events in {filename}.")

# 3. BUILD ARCHIVAL TELEMETRY HUD OVERLAY (IF NOT PRESENT)
def ensure_archival_hud():
    if not os.path.exists('archival_hud_overlay.png'):
        img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font_bold = ImageFont.truetype('arialbd.ttf', 24)
            font_small = ImageFont.truetype('arial.ttf', 18)
        except:
            font_bold = font_small = ImageFont.load_default()

        # Top left documentary badge
        draw.rounded_rectangle([60, 160, 520, 235], radius=8, fill=(0, 0, 0, 160), outline=(255, 215, 0, 180), width=1)
        draw.text((80, 172), '● DECLASSIFIED ARCHIVE', fill=(255, 215, 0, 240), font=font_bold)
        draw.text((80, 202), 'NEVŞEHIR PROVINCE, 1963', fill=(220, 220, 220, 220), font=font_small)

        # Top right coordinates stamp
        draw.rounded_rectangle([720, 160, 1020, 235], radius=8, fill=(0, 0, 0, 160), outline=(100, 100, 100, 120), width=1)
        draw.text((740, 172), 'DEPTH: -85 METERS', fill=(255, 255, 255, 240), font=font_bold)
        draw.text((740, 202), '18 SUBTERRANEAN TIERS', fill=(180, 180, 180, 200), font=font_small)

        img.save('archival_hud_overlay.png')
        print("Generated archival_hud_overlay.png")

# 4. RENDER ELITE-GRADE VIDEO MASTER (V3)
def render_elite_master():
    ensure_archival_hud()
    
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
    
    # 13 clips: 3.10s each, 0.35s xfade overlap -> step = 2.75s
    # Total duration = (13 * 3.10) - (12 * 0.35) = 40.30 - 4.20 = 36.10s
    clip_dur = 3.10
    trans_dur = 0.35
    step = clip_dur - trans_dur  # 2.75s
    offsets = [round((k + 1) * step, 2) for k in range(len(scenes) - 1)]
    
    print(f"[3/5] Assembling 13 scenes with xfade offsets: {offsets[:3]}...")
    
    input_args = []
    filter_parts = []
    
    # 1. Inputs and zoompan filter for each scene
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
        
    # Overlay the Archival HUD graphic (input #13)
    input_args.extend(['-loop', '1', '-t', '36.5', '-i', 'archival_hud_overlay.png'])
    hud_idx = len(scenes)
    filter_parts.append(f"[video_stitched][{hud_idx}:v]overlay=0:0[video_with_hud]")
    
    # Audio inputs: Voiceover (#14) and Ambient bed with diegetic hammer/stone door SFX (#15)
    input_args.extend(['-i', 'voiceover_v3.mp3', '-i', 'ambient_bed_v3.wav'])
    vo_idx = len(scenes) + 1
    bg_idx = len(scenes) + 2
    
    filter_parts.append(
        f"[{vo_idx}:a]asplit[v_main][v_sc];"
        f"[{bg_idx}:a][v_sc]sidechaincompress=threshold=0.08:ratio=7:attack=15:release=220[bg_ducked];"
        f"[v_main][bg_ducked]amix=inputs=2:duration=first:dropout_transition=2[a_final]"
    )
    
    # Final Visual Grade: Vignette + 35mm Fine Film Grain + Contrast grade + Zero-Jitter Subtitles
    sub_path = os.path.abspath('karaoke_v3.ass').replace('\\', '/').replace(':', '\\:')
    filter_parts.append(
        f"[video_with_hud]eq=contrast=1.06:brightness=0.01:saturation=1.10,"
        f"noise=alls=8:allf=t+u,"
        f"vignette=PI/4.5:aspect=9/16,"
        f"subtitles='{sub_path}'[v_final]"
    )
    
    full_filter = ";".join(filter_parts)
    output_mp4 = 'derinkuyu_v3_elite.mp4'
    
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
    
    print("[4/5] Executing Elite Single-Pass FFmpeg Master Render...")
    subprocess.run(cmd, check=True)
    
    size_mb = os.path.getsize(output_mp4) / (1024 * 1024)
    print(f"\nSUCCESS! Rendered Elite Master: {output_mp4} ({size_mb:.2f} MB)")

def main():
    words = asyncio.run(generate_voice_and_words())
    generate_silky_running_subtitles(words, 'karaoke_v3.ass')
    render_elite_master()

if __name__ == '__main__':
    main()
