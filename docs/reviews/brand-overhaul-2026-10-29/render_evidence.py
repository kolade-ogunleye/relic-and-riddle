"""Render the overhaul's surfaces for three topics from one code tree (before = HEAD export, after = working tree).

Usage: python render.py <code_root> <out_dir> [surface ...]
The photo assets, fonts, geo data and dev runs are read from the main repo (RELIC_APP_ROOT). Nothing is posted,
no API is called (photo matching is fixed to the run's recorded photos)."""
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAIN = Path(r"C:\Users\omokolade\GitHub\relic-and-riddle")
os.environ["RELIC_APP_ROOT"] = str(MAIN)
CODE, OUT = Path(sys.argv[1]), Path(sys.argv[2])
ONLY = set(sys.argv[3:])
sys.path.insert(0, str(CODE))
FF = Path(r"C:\Users\omokolade\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin")
os.environ["PATH"] = str(FF) + os.pathsep + os.environ["PATH"]

from PIL import Image  # noqa: E402
from src import graphics, multi_format, end_card  # noqa: E402
from src.typography import cover_crop  # noqa: E402

A, U = MAIN / "assets", MAIN / "cache" / "upscaled"
RICHAT_RUN = MAIN / "output" / "run_richat_structure_sahara_eye_1793272675"
GUMBAZ_RUN = MAIN / "output" / "run_gol_gumbaz_whispering_gallery_1793278143"


def run_photos(run):
    st = json.loads((run / "stories" / "still_layout.json").read_text(encoding="utf-8"))
    stories = [Path(st[k]["recipe"]["photo"]) for k in sorted(st)]
    car = [Path(p) for p in json.loads((run / "carousel_photos.json").read_text(encoding="utf-8"))["photos"]]
    return stories, car


def payload_of(run=None, script=None):
    return json.loads(((run / "verified_script.json") if run else script).read_text(encoding="utf-8"))


DA = A / "derinkuyu_underground_city"
TOPICS = {
    "richat": dict(payload=payload_of(RICHAT_RUN), photos=run_photos(RICHAT_RUN),
                   stat=[{"value": "25+ miles", "label": "across the rings"}],
                   stat_photo=A / "richat_structure_sahara_eye" / "12_commons.jpg",
                   place={"lat": 21.124, "lon": -11.402, "label": "Richat Structure", "country": "Mauritania"},
                   caption=("Concentric rings span the sands", A / "richat_structure_sahara_eye" / "02_commons.jpg"),
                   end=A / "richat_structure_sahara_eye" / "02_commons.jpg"),
    "gumbaz": dict(payload=payload_of(GUMBAZ_RUN), photos=run_photos(GUMBAZ_RUN),
                   stat=None, stat_photo=U / "03_commons_749e0ecf629ab119.jpg",
                   place={"lat": 16.830, "lon": 75.736, "label": "Gol Gumbaz", "country": "India"},
                   caption=("Set with lime mortar", U / "03_commons_749e0ecf629ab119.jpg"),
                   end=A / "gol_gumbaz_whispering_gallery" / "02_commons.jpg"),
    "derinkuyu": dict(payload=dict(payload_of(script=MAIN / "data" / "scripts" / "derinkuyu_underground_city.json"),
                                   share_line="Send this to a friend who loves ancient engineering, and follow for the next one.",
                                   timeline=[{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
                                             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
                                             {"year": 1923, "approximate": False, "label": "Abandoned after the population exchange"},
                                             {"year": 1963, "approximate": False, "label": "Rediscovered behind a wall"}]),
                      photos=([DA / "17_commons.jpg", DA / "20_commons.jpg", DA / "18_commons.jpg"],
                              [DA / f for f in ("19_commons.jpg", "20_commons.jpg", "31_commons.jpg", "17_commons.jpg",
                                                "21_commons.jpg", "28_commons.jpg", "18_commons.jpg")]),
                      stat=[{"value": "about 280 ft (85 m)", "label": "below the surface"}],
                      stat_photo=DA / "20_commons.jpg",
                      place={"lat": 38.373, "lon": 34.734, "label": "Derinkuyu", "country": "Turkey"},
                      caption=("Carved from volcanic tuff", DA / "19_commons.jpg"), end=DA / "31_commons.jpg"),
}


def grade(src: Path, dst: Path, ass: Path = None, seek: float = None):
    """The master chain on one frame: format, film grade (hue-safe print), noise, vignette, then captions."""
    from src.video_composer import film_grade_filter
    lut = film_grade_filter(0.0, out_dir=OUT / "_luts")
    vf = f"format=yuv420p,{lut},format=yuv420p,noise=alls=8:allf=t+u:all_seed=7,vignette=PI/4.2:aspect=9/16"
    if ass:
        a = str(ass.resolve()).replace("\\", "/").replace(":", "\\:")
        f = str((MAIN / "fonts").resolve()).replace("\\", "/").replace(":", "\\:")
        vf += f",subtitles='{a}':fontsdir='{f}'"
    cmd = [str(FF / "ffmpeg.exe"), "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(src)]
    cmd += ["-vf", vf + ",format=rgb24", "-ss", f"{seek or 0:.3f}", "-frames:v", "1", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def stories(name, t, out):
    gen = multi_format.MultiFormatGenerator()
    photos = t["photos"][0]
    gen._match_photos = lambda cards, stills: list(photos)
    gen.generate_story_sequence(photos[0], t["payload"], out, scene_images=list(photos) + [photos[0]])


def carousel(name, t, out):
    gen = multi_format.MultiFormatGenerator()
    photos = t["photos"][1]
    gen._carousel_photos = lambda cards, stills, output_dir: list(photos)
    gen.generate_carousel_sequence(list(photos), t["payload"], out)


def video_graphics(name, t, out):
    g = out / "graphics"
    g.mkdir(parents=True, exist_ok=True)
    narration = t["payload"].get("narration_script") or ""
    cards = t["stat"] or t["payload"].get("stat_cards")
    made = []
    if cards:
        made.append(graphics.render_stat_cards(cards, g / "graphic_numbers.mp4", t["stat_photo"], narration=narration))
    kw = {"narration": narration} if "narration" in inspect.signature(graphics.render_map).parameters else {}
    made.append(graphics.render_map(t["place"], g / "graphic_map.mp4", **kw))
    if t["payload"].get("timeline"):
        made.append(graphics.render_timeline(t["payload"]["timeline"], g / "graphic_timeline.mp4"))
    for clip in made:
        if clip:
            poster = Path(clip).with_suffix(".jpg")
            png = g / (poster.stem + "_poster.png")
            Image.open(poster).save(png)
            grade(png, g / (poster.stem + "_graded.png"))


def captions(name, t, out):
    from src.video_composer import VideoComposer
    text, photo = t["caption"]
    c = out / "captions"
    c.mkdir(parents=True, exist_ok=True)
    words, at = [], 0.0
    for w in text.split():
        words.append({"word": w, "start": at, "dur": 0.32})
        at += 0.36
    ass = VideoComposer(MAIN / "fonts").build_subtitles(words, c / "captions.ass", emphasis=[text.split()[-1]])
    cover_crop(photo, (1080, 1920)).convert("RGB").save(c / "bg.png")
    grade(c / "bg.png", c / "graded_bg.png")
    grade(c / "bg.png", c / "caption.png", ass=ass, seek=words[-1]["start"] + 0.1)


def endcard(name, t, out):
    e = out / "end"
    e.mkdir(parents=True, exist_ok=True)
    cover_crop(t["end"], (1080, 1920)).convert("RGB").save(e / "bg.png")
    grade(e / "bg.png", e / "graded_bg.png")
    share = t["payload"].get("share_line") or ""
    frames = list(end_card.frames(Image.open(e / "graded_bg.png").convert("RGB"), end_card.card_text(share), stats := {}))
    frames[-1].save(e / "end_card.png")
    frames[len(frames) // 2].save(e / "end_card_mid.png")
    (e / "stats.json").write_text(json.dumps(stats, indent=1, default=str), encoding="utf-8")


def cover(name, t, out):
    from src.visual_engine import VisualEngine
    hero = t["photos"][1][0]
    VisualEngine().generate_thumbnails(hero, t["payload"], out / "cover", style="badge", variants=False)


SURFACES = {"stories": stories, "carousel": carousel, "graphics": video_graphics, "captions": captions,
            "end": endcard, "cover": cover}

if __name__ == "__main__":
    for name, t in TOPICS.items():
        out = OUT / name
        out.mkdir(parents=True, exist_ok=True)
        for s, fn in SURFACES.items():
            if ONLY and s not in ONLY and f"{name}:{s}" not in ONLY:
                continue
            try:
                fn(name, t, out)
                print("ok", name, s, flush=True)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("FAILED", name, s, e, flush=True)
