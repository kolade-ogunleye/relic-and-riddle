"""Old | new film grade, half size, for the stills named below (balanced as the pipeline prepares them).

usage: .venv/Scripts/python.exe docs/reviews/film-grade-2026-10-29/render_evidence.py <workdir>
"""
import subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from src import colour_balance as cb, video_composer as vc  # noqa: E402

WORK = Path(sys.argv[1]).resolve()
OUT = Path(__file__).resolve().parent
cb.CACHE_DIR = WORK / "cb_cache"
STILLS = [("derinkuyu_underground_city", "25"), ("derinkuyu_underground_city", "32"),
          ("richat_structure_sahara_eye", "01"), ("richat_structure_sahara_eye", "14"),
          ("nazca_lines_runway_paradox", "14"), ("roman_self_healing_concrete", "01"),
          ("odessa_catacombs", "01"), ("amber_room_heist", "20"), ("gol_gumbaz_whispering_gallery", "30"),
          ("sunken_city_of_heracleion", "03")]
OLD = vc.FILM_PRE_CURVE + ",lut3d='" + str(vc.FILM_PRINT_LUT.resolve()).replace(chr(92), "/").replace(":", chr(92) + ":") + "'"
NEW = vc.film_grade_filter(0.0, out_dir=WORK / "luts")


def grade(src, pre, chain, dst):
    vf = f"{pre}scale=out_range=tv,format=yuv420p,format=yuv420p,{chain},format=yuv420p"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)], check=True)
    return Image.open(dst).convert("RGB")


def pair(a, b, dst, label_h=28):
    a, b = (im.resize((im.width // 2, im.height // 2), Image.LANCZOS) for im in (a, b))
    c = Image.new("RGB", (a.width + b.width + 8, a.height + label_h), (255, 255, 255))
    c.paste(a, (0, label_h)); c.paste(b, (a.width + 8, label_h))
    d = ImageDraw.Draw(c)
    d.text((8, 7), "old grade (full Kodak 2383 LUT)", fill=(0, 0, 0))
    d.text((a.width + 16, 7), "new grade (print tone, picture's hue)", fill=(0, 0, 0))
    c.save(dst, "JPEG", quality=86)


WORK.mkdir(parents=True, exist_ok=True)
for topic, num in STILLS:
    src = cb.balanced(REPO / "assets" / topic / f"{num}_commons.jpg")
    pre = "scale=1600:1600:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,"
    o = grade(src, pre, OLD, WORK / f"{topic}_{num}_old.png")
    n = grade(src, pre, NEW, WORK / f"{topic}_{num}_new.png")
    pair(o, n, OUT / f"{topic.split('_')[0]}_{num}_old_new.jpg")
# The master's own framing of the Derinkuyu door (the 0:17 shot): the 9:16 cover crop, pre-zoom curve and vignette.
src = cb.balanced(REPO / "assets" / "derinkuyu_underground_city" / "25_commons.jpg")
pre = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
       "curves=master='0/0.02 0.1/0.11 0.5/0.50 0.9/0.89 1/0.97',")
post = ",vignette=PI/4.2:aspect=9/16"
o = grade(src, pre, OLD + post, WORK / "door_frame_old.png")
n = grade(src, pre, NEW + post, WORK / "door_frame_new.png")
pair(o, n, OUT / "derinkuyu_25_master_frame_old_new.jpg")
