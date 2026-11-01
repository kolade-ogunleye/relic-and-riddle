"""Offline cover review. Run from the repository: python <this file> before|after|gallery.

The before phase freezes the input recipes and refuses to overwrite earlier evidence.
No providers, bot, queue or production services are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

TOPICS = {
    "richat": ("Eye of the Sahara", "run_richat_structure_sahara_eye_1793272675"),
    "gumbaz_exterior": ("Gol Gumbaz · exterior", "run_gol_gumbaz_whispering_gallery_1793278143"),
    "derinkuyu": ("Derinkuyu", None),
    "gumbaz": ("Gol Gumbaz · interior study", "run_gol_gumbaz_whispering_gallery_1793278143"),
}
SHOWCASE = ("richat", "gumbaz_exterior", "derinkuyu")
STYLES = {
    "depth_sandwich": ("A", "Evidence plate", "An archival treatment with clear documentary hierarchy."),
    "badge": ("B", "Discovery", "Bold two-tone type with a strong reading order."),
    "minimal": ("C", "Cinema", "A restrained title treatment that gives the photograph room."),
    "lower_third": ("Rotation", "Editorial", "The existing rotation style, with a lower editorial title stack."),
}


def freeze_inputs():
    manifest = {}
    for key, (name, run_name) in TOPICS.items():
        if run_name:
            run = ROOT / "output" / run_name
            payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
            recipe = json.loads((run / "still_layout.json").read_text(encoding="utf-8"))["thumbnail_cover.jpg"]["recipe"]
            hero = Path(recipe["hero"])
            if key == "gumbaz_exterior":
                hero = ROOT / "assets/gol_gumbaz_whispering_gallery/03_commons.jpg"
        else:
            hero = ROOT / "assets/derinkuyu_underground_city/19_commons.jpg"
            payload = json.loads((ROOT / "data/scripts/derinkuyu_underground_city.json").read_text(encoding="utf-8"))
        manifest[key] = {"name": name, "hero": str(hero.relative_to(ROOT)),
                         "hero_sha256": hashlib.sha256(hero.read_bytes()).hexdigest(), "payload": payload}
    (HERE / "inputs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def render(phase):
    from src.visual_engine import VisualEngine
    if phase == "before":
        if (HERE / "before").exists():
            raise SystemExit("Before evidence already exists; preserved without changes.")
        freeze_inputs()
    inputs = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    for key, topic in inputs.items():
        hero = ROOT / topic["hero"]
        if hashlib.sha256(hero.read_bytes()).hexdigest() != topic["hero_sha256"]:
            raise RuntimeError(f"Photo changed since before capture: {hero}")
        for style in STYLES:
            dest = HERE / phase / key / style
            VisualEngine().generate_thumbnails(hero, topic["payload"], dest, style=style, variants=False)
            print(phase, key, style, flush=True)


def add_showcase():
    """Preserve the original interior study; add an exterior with the same script.

    The pre-change renderer was clean at HEAD when this task started. Pin its commit
    and source hash so this additional before render remains reproducible.
    """
    manifest = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    if "gumbaz_exterior" in manifest:
        raise SystemExit("Exterior evidence already exists; preserved without changes.")
    hero = ROOT / "assets/gol_gumbaz_whispering_gallery/03_commons.jpg"
    manifest["gumbaz_exterior"] = {
        "name": TOPICS["gumbaz_exterior"][0], "hero": str(hero.relative_to(ROOT)),
        "hero_sha256": hashlib.sha256(hero.read_bytes()).hexdigest(), "payload": manifest["gumbaz"]["payload"],
    }
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(["git", "show", f"{revision}:src/visual_engine.py"], cwd=ROOT)
    baseline = types.ModuleType("src._before_cover")
    baseline.__package__ = "src"
    exec(compile(source, "baseline_visual_engine.py", "exec"), baseline.__dict__)
    for style in STYLES:
        baseline.VisualEngine().generate_thumbnails(hero, manifest["gumbaz_exterior"]["payload"],
                                                   HERE / "before/gumbaz_exterior" / style,
                                                   style=style, variants=False)
        print("before gumbaz_exterior", style, flush=True)
    (HERE / "exterior_baseline.json").write_text(json.dumps({"revision": revision,
        "renderer_sha256": hashlib.sha256(source).hexdigest()}, indent=2), encoding="utf-8")
    (HERE / "inputs.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def contact_sheet(square=False):
    from PIL import Image, ImageDraw
    from src.typography import get_font

    width, thumb_w = 1440, 324
    thumb_h = thumb_w if square else 576
    row_h = thumb_h + 88
    canvas = Image.new("RGB", (width, 124 + len(SHOWCASE) * row_h + 32), (11, 14, 18))
    draw = ImageDraw.Draw(canvas)
    ink, bone, gold, stone = (11, 14, 18), (243, 237, 226), (242, 182, 48), (184, 177, 163)
    draw.text((36, 22), "RELIC & RIDDLE", font=get_font("Cinzel", 24), fill=bone)
    draw.text((36, 60), "COVER STUDIES  /  " + ("PROFILE GRID" if square else "FULL FRAME"),
              font=get_font("Montserrat", 31, weight=800), fill=gold)
    for row, key in enumerate(SHOWCASE):
        name = TOPICS[key][0].replace(" · exterior", "")
        y = 124 + row * row_h
        draw.text((36, y), name.upper(), font=get_font("Montserrat", 23, weight=800), fill=bone)
        for col, (style, (letter, title, _)) in enumerate(STYLES.items()):
            x = 36 + col * 348
            with Image.open(HERE / "after" / key / style / "thumbnail_cover.jpg") as im:
                im = im.convert("RGB")
                if square:
                    im = im.crop((0, 420, 1080, 1500))
                canvas.paste(im.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS), (x, y + 38))
            draw.text((x, y + 46 + thumb_h), f"{letter} / {title}",
                      font=get_font("Montserrat", 19, weight=600), fill=stone)
    dest = HERE / ("overview-grid.jpg" if square else "overview.jpg")
    canvas.save(dest, quality=93)


def gallery():
    inputs = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    dataset = {"topics": {k: {"name": TOPICS[k][0]} for k in TOPICS if k in inputs},
               "styles": {k: {"letter": v[0], "title": v[1], "description": v[2]} for k, v in STYLES.items()}}
    page = (HERE / "gallery_template.html").read_text(encoding="utf-8")
    page = page.replace("__DATASET__", json.dumps(dataset, ensure_ascii=False).replace("<", "\\u003c"))
    (HERE / "index.html").write_text(page, encoding="utf-8")
    contact_sheet()
    contact_sheet(square=True)
    measurements = {}
    for phase in ("before", "after"):
        measurements[phase] = {}
        for key in TOPICS:
            for style in STYLES:
                path = HERE / phase / key / style / "still_layout.json"
                record = json.loads(path.read_text(encoding="utf-8"))["thumbnail_cover.jpg"]
                ratios = [e["contrast"] for e in record["elements"] if e.get("contrast") is not None]
                measurements[phase][f"{key}/{style}"] = {
                    "problems": record.get("problems", []), "shortened": record.get("shortened", []),
                    "attempts": record.get("attempts"), "measured_text_elements": len(ratios),
                    "minimum_contrast": min(ratios) if ratios else None,
                }
    (HERE / "measurements.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
    print("Built index.html, overview.jpg, overview-grid.jpg and measurements.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after", "gallery", "add-showcase"))
    args = parser.parse_args()
    if args.phase in ("before", "after"):
        render(args.phase)
    elif args.phase == "add-showcase":
        add_showcase()
    else:
        gallery()
