"""Offline second-pass cover review; baseline is the approved first redesign.

Run ``python <this file> after`` and then ``python <this file> gallery``.
The prior pass is frozen in before/ and is never regenerated or overwritten.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
TOPICS = {
    "richat": "Eye of the Sahara",
    "gumbaz_exterior": "Gol Gumbaz · exterior",
    "derinkuyu": "Derinkuyu",
    "gumbaz": "Gol Gumbaz · interior study",
}
SHOWCASE = ("richat", "gumbaz_exterior", "derinkuyu")
STYLES = {
    "depth_sandwich": ("A", "Evidence plate", "A composed photographic plate with a clear editorial caption."),
    "badge": ("B", "Discovery", "A bold two-tone headline composed around the photograph."),
    "minimal": ("C", "Cinema", "A quieter composition with an emphatic, focused title."),
    "lower_third": ("Rotation", "Editorial", "The existing rotation layout with a measured lower title stack."),
}

def inputs():
    return json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))

def verify_baseline():
    baseline = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))
    for relative, expected in baseline["files"].items():
        path = HERE / "before" / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Baseline changed: {path}")

def render():
    from src.visual_engine import VisualEngine
    verify_baseline()
    for key, topic in inputs().items():
        hero = ROOT / topic["hero"]
        if hashlib.sha256(hero.read_bytes()).hexdigest() != topic["hero_sha256"]:
            raise RuntimeError(f"Photo changed since baseline: {hero}")
        for style in STYLES:
            dest = HERE / "after" / key / style
            VisualEngine().generate_thumbnails(hero, topic["payload"], dest, style=style, variants=False)
            print("after", key, style, flush=True)

def overview(square=False):
    from PIL import Image, ImageDraw
    from src.typography import get_font
    width, thumb_w = 1440, 324
    thumb_h = thumb_w if square else 576
    row_h = thumb_h + 88
    canvas = Image.new("RGB", (width, 124 + len(SHOWCASE) * row_h + 32), (11, 14, 18))
    draw = ImageDraw.Draw(canvas)
    bone, gold, stone = (243, 237, 226), (242, 182, 48), (184, 177, 163)
    draw.text((36, 22), "RELIC & RIDDLE", font=get_font("Cinzel", 24), fill=bone)
    draw.text((36, 60), "COVER REFINEMENT / " + ("PROFILE GRID" if square else "FULL FRAME"),
              font=get_font("Montserrat", 30, weight=800), fill=gold)
    for row, key in enumerate(SHOWCASE):
        y = 124 + row * row_h
        draw.text((36, y), TOPICS[key].replace(" · exterior", "").upper(), font=get_font("Montserrat", 23, weight=800), fill=bone)
        for col, (style, (letter, title, _)) in enumerate(STYLES.items()):
            x = 36 + col * 348
            with Image.open(HERE / "after" / key / style / "thumbnail_cover.jpg") as source:
                im = source.convert("RGB")
                if square:
                    im = im.crop((0, 420, 1080, 1500))
                canvas.paste(im.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS), (x, y + 38))
            draw.text((x, y + 46 + thumb_h), f"{letter} / {title}", font=get_font("Montserrat", 19), fill=stone)
    canvas.save(HERE / ("overview-grid.jpg" if square else "overview.jpg"), quality=93)

def phone_board():
    from PIL import Image, ImageDraw
    from src.typography import get_font
    # Each image is exactly 180 px wide; viewed at original size, no zoom is needed.
    width, col_w, row_h = 1656, 204, 248
    canvas = Image.new("RGB", (width, 124 + len(SHOWCASE) * row_h + 24), (11, 14, 18))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 24), "180 PX / PRIOR PASS → REFINED", font=get_font("Montserrat", 29, weight=800), fill=(242,182,48))
    for col,(style,(letter,title,_)) in enumerate(STYLES.items()):
        draw.text((24 + col * col_w * 2, 78), letter + " / " + title, font=get_font("Montserrat", 18), fill=(243,237,226))
    for row,key in enumerate(SHOWCASE):
        y = 124 + row * row_h
        draw.text((24,y),TOPICS[key].replace(" · exterior", "").upper(),font=get_font("Montserrat",18,weight=800),fill=(184,177,163))
        for col,style in enumerate(STYLES):
            for n,phase in enumerate(("before","after")):
                x=24+(col*2+n)*col_w
                with Image.open(HERE/phase/key/style/"thumbnail_cover.jpg") as im:
                    thumb=im.convert("RGB").crop((0,420,1080,1500)).resize((180,180),Image.Resampling.LANCZOS)
                    canvas.paste(thumb,(x,y+32))
                draw.text((x,y+217),"Prior pass" if phase=="before" else "Refined",font=get_font("Montserrat",13),fill=(184,177,163) if phase=="before" else (242,182,48))
    canvas.save(HERE/"phone-comparison.jpg",quality=95)

def gallery():
    verify_baseline()
    data = inputs()
    dataset = {"topics": {k: {"name": v, "hero": "../../../" + data[k]["hero"].replace("\\", "/")} for k,v in TOPICS.items()},
               "styles": {k: {"letter": v[0], "title": v[1], "description": v[2]} for k,v in STYLES.items()}}
    page = (HERE / "gallery_template.html").read_text(encoding="utf-8")
    page = page.replace("__DATASET__", json.dumps(dataset, ensure_ascii=False).replace("<", "\\u003c"))
    (HERE / "index.html").write_text(page, encoding="utf-8")
    overview()
    overview(square=True)
    phone_board()
    measurements = {}
    for phase in ("before", "after"):
        measurements[phase] = {}
        for key in TOPICS:
            for style in STYLES:
                record=json.loads((HERE/phase/key/style/"still_layout.json").read_text(encoding="utf-8"))["thumbnail_cover.jpg"]
                ratios=[e["contrast"] for e in record["elements"] if e.get("contrast") is not None]
                measurements[phase][f"{key}/{style}"]={
                    "problems":record.get("problems",[]),"shortened":record.get("shortened",[]),
                    "attempts":record.get("attempts"),"measured_text_elements":len(ratios),
                    "minimum_contrast":min(ratios) if ratios else None,
                    "recipe":record.get("recipe",{}),
                }
    (HERE/"measurements.json").write_text(json.dumps(measurements,indent=2),encoding="utf-8")
    print("Built gallery, full-frame/square overviews, 180px comparison and measurements.",flush=True)

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase",choices=("after","gallery","verify-baseline"))
    phase=parser.parse_args().phase
    {"after":render,"gallery":gallery,"verify-baseline":verify_baseline}[phase]()
