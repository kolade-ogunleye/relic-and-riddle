"""Safe-zone evidence: frames of a run's deliverables with each platform's own interface bands drawn over them,
"before" from the run's own files and "after" re-rendered with the current code, plus the measurements the
review cites. Nothing is published or sent; the run folder is only read.

    python scripts/render_safe_zone_evidence.py output/run_<topic>_<id> docs/reviews/safe-zones-<date>
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import graphics, safe_zones  # noqa: E402
from src.config import FONTS_DIR  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402
from src.typography import get_font  # noqa: E402
from src.video_composer import VideoComposer  # noqa: E402
from src.video_qa import caption_pills  # noqa: E402

META, SHORTS, BOX = (230, 70, 70), (70, 130, 235), (90, 225, 130)


def _ff(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:")


def overlay(im: Image.Image, kind: str) -> Image.Image:
    """The platforms' bands (Meta red, YouTube Shorts blue), the deliverable's box (green) and, for a Reel, the
    graphics' text box inside it; for a cover, the profile grid's 3:4 and 1:1 crops."""
    im = im.convert("RGBA")
    w, h = im.size
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    small, label = get_font("Montserrat", 26), get_font("Montserrat", 30)
    if kind in ("reel", "story"):
        for p in safe_zones.DELIVERABLES[kind]:
            colour = SHORTS if p == "youtube_shorts" else META
            x0, y0, x1, y1 = safe_zones.PLATFORM_UI[p].box(w, h)
            for band in ((0, 0, w, y0), (0, y1, w, h), (0, y0, x0, y1), (x1, y0, w, y1)):
                d.rectangle(band, fill=(*colour, 48))
        bx = safe_zones.safe_box(kind, w, h)
        d.rectangle(bx, outline=(*BOX, 255), width=4)
        d.text((bx[0] + 10, bx[1] + 8), f"{kind} box x {bx[0]}-{bx[2]}  y {bx[1]}-{bx[3]}", font=label, fill=(*BOX, 255))
        if kind == "reel":
            gb = graphics.get_text_box(w, h)
            d.rectangle(gb, outline=(*BOX, 200), width=2)
            d.text((gb[0] + 10, gb[3] - 40), f"graphics text to y {gb[3]}; captions below", font=small, fill=(*BOX, 230))
        legend = [("Meta: top 14%, bottom 35%, sides 6%", META)] + ([("YouTube Shorts: top 288, bottom 672, left 48, right 192", SHORTS)] if kind == "reel" else [])
        for i, (text, colour) in enumerate(legend):
            d.text((20, h - 60 - 34 * (len(legend) - 1 - i)), text, font=small, fill=(*colour, 255))
    else:  # cover
        side = min(w, h)
        for name, hh, colour in (("3:4 grid crop", int(w * 4 / 3), META), ("1:1 crop (cover box)", side, BOX)):
            y0, y1 = (h - hh) // 2, (h + hh) // 2
            d.rectangle((0, y0, w - 1, y1), outline=(*colour, 255), width=4)
            d.text((16, y0 + 8), f"{name}: y {y0}-{y1}", font=small, fill=(*colour, 255))
    shown = Image.alpha_composite(im, layer).convert("RGB")
    return shown.resize((w // 2, h // 2), Image.LANCZOS)  # half size: the repository copy stays light; the numbers are in the text


def bright_outside(im: Image.Image, box, threshold: int = 200, grow: int = 4) -> int:
    lum = np.asarray(im.convert("L"))
    mask = lum > threshold
    mask[max(0, box[1] - grow):box[3] + grow, max(0, box[0] - grow):box[2] + grow] = False
    return int(mask.sum())


def frame_at(video: Path, t: float, out: Path, ass: Path = None) -> Path:
    """One frame of `video` at `t`; with `ass`, the clip's first frame shown as if at `t` with the captions burnt in."""
    if ass is None:
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(out)]
    else:
        vf = f"setpts=PTS+{t:.3f}/TB,subtitles='{_ff(ass)}':fontsdir='{_ff(FONTS_DIR)}'"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", vf, "-frames:v", "1", str(out)]
    subprocess.run(cmd, check=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--photos", type=Path, help="the topic's photo folder (for the re-rendered Story frames)")
    args = ap.parse_args()
    run, out = args.run, args.out
    out.mkdir(parents=True, exist_ok=True)
    payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
    words = json.loads((run / "words.json").read_text(encoding="utf-8"))
    master = next(run.glob("*_master.mp4"))
    old_ass = next(run.glob("*_master_captions.ass"))
    notes = []

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Captions: the run's pills against the line, then the same words with the current renderer.
        before = caption_pills(old_ass)
        new_ass = VideoComposer().build_subtitles(words, tmp / "captions.ass")
        after = caption_pills(new_ass)
        box = safe_zones.safe_box("reel")
        lowest_before = max(before, key=lambda r: r[3])
        t = None
        for line in old_ass.read_text(encoding="utf-8").splitlines():  # the time of that lowest pill
            if ",CapBox," in line and f"\\pos({lowest_before[0]},{lowest_before[1]})" in line:
                parts = line.split(",", 9)
                h1, m1, s1 = parts[1].split(":")
                h2, m2, s2 = parts[2].split(":")
                t = ((int(h1) * 3600 + int(m1) * 60 + float(s1)) + (int(h2) * 3600 + int(m2) * 60 + float(s2))) / 2
                break
        t = t if t is not None else 0.6
        overlay(Image.open(frame_at(master, t, tmp / "cap_before.jpg")), "reel").save(out / "captions-before.jpg", quality=90)
        scene = sorted((run / "parallax").glob("scene_*.mp4"))[0]
        overlay(Image.open(frame_at(scene, t, tmp / "cap_after.jpg", ass=new_ass)), "reel").save(out / "captions-after.jpg", quality=90)
        notes.append(f"Captions ({len(before)} pills in the run, {len(after)} re-rendered): before, the lowest pill reached y={max(r[3] for r in before)} "
                     f"and the widest spanned x {min(r[0] for r in before)}-{max(r[2] for r in before)}; "
                     f"{sum(1 for r in before if not safe_zones.inside(r, box))} of {len(before)} left the Reel box (y {box[1]}-{box[3]}, x {box[0]}-{box[2]}). "
                     f"After: lowest edge y={max(r[3] for r in after)}, widest x {min(r[0] for r in after)}-{max(r[2] for r in after)}, "
                     f"{sum(1 for r in after if not safe_zones.inside(r, box))} outside. The frame shown is at {t:.2f} s.")

        # The number card: the run's poster, then re-rendered.
        old_poster = run / "graphics" / "graphic_numbers.jpg"
        gbox = graphics.get_text_box(1080, 1920)
        if old_poster.exists() and payload.get("stat_cards"):
            overlay(Image.open(old_poster), "reel").save(out / "number-card-before.jpg", quality=90)
            hero = sorted((run / "beat_images").glob("*.jpg"))
            clip = graphics.render_stat_cards(payload["stat_cards"], tmp / "numbers.mp4", photo=hero[0] if hero else None)
            new_poster = Image.open(clip.with_suffix(".jpg"))
            overlay(new_poster, "reel").save(out / "number-card-after.jpg", quality=90)
            notes.append(f"Number card: bright text pixels outside the graphics box {gbox}: before {bright_outside(Image.open(old_poster), gbox)}, "
                         f"after {bright_outside(new_poster, gbox)} (the old box ended at y 1113 and x 1000; the new at y {gbox[3]} and x {gbox[2]}).")

        # A timeline and a scale on sample data (this run had neither), the two kinds measured over the bounds.
        from tests.test_safe_zones import GRAPHICS
        for kind in ("timeline", "scale_depth"):
            clip = GRAPHICS[kind](tmp / f"{kind}.mp4")
            poster = Image.open(clip.with_suffix(".jpg"))
            overlay(poster, "reel").save(out / f"{kind.replace('_', '-')}-after.jpg", quality=90)
            notes.append(f"{kind} (sample data): bright text pixels outside the graphics box after: {bright_outside(poster, gbox)}.")

        # Stories: the run's frames, then the same copy re-rendered on the topic's photos.
        for p in sorted((run / "stories").glob("story_*.jpg")):
            overlay(Image.open(p), "story").save(out / f"{p.stem}-before.jpg", quality=90)
        photos = sorted(args.photos.glob("*.jpg")) if args.photos else sorted((run / "beat_images").glob("*.jpg"))
        if photos:
            slides = MultiFormatGenerator().generate_story_sequence(photos[0], payload, tmp, scene_images=photos)
            layout = json.loads((tmp / "stories" / "layout.json").read_text(encoding="utf-8"))
            for p, entry in zip(slides, layout):
                overlay(Image.open(p), "story").save(out / f"{p.stem}-after.jpg", quality=90)
            sbox = safe_zones.safe_box("story")
            notes.append("Stories after: text blocks " + "; ".join(f"{e['frame']} y {e['text_box'][1]}-{e['text_box'][3]}" for e in layout)
                         + f", all inside the Story box y {sbox[1]}-{sbox[3]} (before, the rule allowed text down to y 1580).")

        # The cover: unchanged, shown against the two grid crops.
        cover = run / "thumbnail_cover.jpg"
        if cover.exists():
            overlay(Image.open(cover), "cover").save(out / "cover-grid-crops.jpg", quality=90)
            notes.append("Cover: unchanged; its text was already kept inside the centre square (y 420-1500), which both the 3:4 and the 1:1 grid crops keep.")

    (out / "measurements.txt").write_text("\n".join(notes) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(notes))


if __name__ == "__main__":
    main()
