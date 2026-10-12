import os
import subprocess

def create_ass_subtitles():
    # Convert SRT into a beautifully styled ASS subtitle file for Reels
    # Bold, yellow highlight, black outline, centered above bottom UI
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ReelsStyle,Arial,64,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,3,2,60,60,340,1
Style: ReelsWhite,Arial,60,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,3,2,60,60,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:04.20,ReelsStyle,,0,0,0,,In 1963, a man in Turkey\\nknocked down a basement wall...
Dialogue: 0,0:00:04.20,0:00:08.30,ReelsWhite,,0,0,0,,...and uncovered a tunnel\\nleading into darkness.
Dialogue: 0,0:00:08.30,0:00:12.50,ReelsStyle,,0,0,0,,What he found was DERINKUYU:
Dialogue: 0,0:00:12.50,0:00:16.70,ReelsWhite,,0,0,0,,An 18-story underground city\\n300 feet inside solid rock.
Dialogue: 0,0:00:16.70,0:00:21.40,ReelsStyle,,0,0,0,,Engineered with ventilation shafts\\nand fresh water wells...
Dialogue: 0,0:00:21.40,0:00:26.10,ReelsWhite,,0,0,0,,...and 1,000-pound stone doors\\nlockable only from the INSIDE.
Dialogue: 0,0:00:26.10,0:00:29.90,ReelsStyle,,0,0,0,,Over 20,000 people\\ncould vanish here for months.
Dialogue: 0,0:00:29.90,0:00:33.80,ReelsWhite,,0,0,0,,Yet historians still cannot agree\\nwho engineered it...
Dialogue: 0,0:00:33.80,0:00:37.56,ReelsStyle,,0,0,0,,What were they hiding from?\\nTell us your theory below.
"""
    with open('subtitles.ass', 'w', encoding='utf-8') as f:
        f.write(ass_header)
    print("Created subtitles.ass")

def build_video():
    create_ass_subtitles()
    
    # Scene plan with durations (total = 37.56s)
    scenes = [
        ('assets/scene1_stairs.jpg', 6.5),
        ('assets/scene2_tunnel.jpg', 6.0),
        ('assets/scene3_door.jpg', 7.5),
        ('assets/scene4_pillars.jpg', 6.5),
        ('assets/scene5_room.jpg', 5.5),
        ('assets/scene6_cappadocia.jpg', 5.56)
    ]
    
    # 1. Render individual scene clips with subtle dynamic Ken Burns zoom
    clip_files = []
    for idx, (img_path, dur) in enumerate(scenes):
        clip_name = f'clip_{idx}.mp4'
        frames = int(dur * 30)
        # Alternate slow zoom in and zoom out
        if idx % 2 == 0:
            zoom_expr = f"min(pzoom+0.0012,1.20)"
        else:
            zoom_expr = f"if(eq(on,1),1.18,max(pzoom-0.0012,1.0))"
            
        cmd = [
            'ffmpeg', '-y', '-loop', '1', '-i', img_path,
            '-vf', f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='{zoom_expr}':d={frames}:s=1080x1920:fps=30",
            '-t', str(dur),
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-r', '30',
            clip_name
        ]
        print(f"Rendering scene {idx+1}/{len(scenes)}: {clip_name} ({dur}s)...")
        subprocess.run(cmd, check=True)
        clip_files.append(clip_name)
        
    # 2. Concat clips
    with open('concat_list.txt', 'w') as f:
        for c in clip_files:
            f.write(f"file '{c}'\n")
            
    raw_video = 'raw_stitched.mp4'
    concat_cmd = [
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', 'concat_list.txt',
        '-c', 'copy', raw_video
    ]
    print("Stitching clips together...")
    subprocess.run(concat_cmd, check=True)
    
    # 3. Add audio and burn ASS subtitles with safe path handling
    sub_path = os.path.abspath('subtitles.ass').replace('\\', '/').replace(':', '\\:')
    final_output = 'derinkuyu_prototype.mp4'
    
    render_cmd = [
        'ffmpeg', '-y',
        '-i', raw_video,
        '-i', 'final_audio.aac',
        '-vf', f"subtitles='{sub_path}'",
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
        '-c:a', 'aac', '-b:a', '192k',
        '-shortest',
        final_output
    ]
    print("Compositing final video with burned subtitles and audio...")
    subprocess.run(render_cmd, check=True)
    
    # Clean up intermediate clips
    for c in clip_files:
        if os.path.exists(c):
            os.remove(c)
    if os.path.exists(raw_video):
        os.remove(raw_video)
    if os.path.exists('concat_list.txt'):
        os.remove('concat_list.txt')
        
    print(f"\nSUCCESS! Rendered final prototype video: {final_output}")
    size_mb = os.path.getsize(final_output) / (1024 * 1024)
    print(f"File size: {size_mb:.2f} MB")

if __name__ == '__main__':
    build_video()
