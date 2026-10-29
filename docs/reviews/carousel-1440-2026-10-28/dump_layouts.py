"""Draws every carousel archetype (normal and tight, both copies) for a set of cards with one version of the
renderer, at a given canvas size, and dumps each slide's recorded layout and an image digest.

usage: python dump_layouts.py <code_root> <repo_root> <width> <height> <out.json> <photo_dir>"""
import hashlib
import json
import os
import sys
from pathlib import Path

code_root, repo, w, h, out, photo_dir = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5]), Path(sys.argv[6])
os.environ["RELIC_APP_ROOT"] = str(repo)
sys.path.insert(0, str(code_root))
from PIL import Image, ImageDraw  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402

MultiFormatGenerator.CAROUSEL_SIZE = (w, h)
photo_dir.mkdir(parents=True, exist_ok=True)
photos = []
for i in range(3):
    p = photo_dir / f"p{i}.jpg"
    if not p.is_file():
        im = Image.new("RGB", (2000, 2500), (200 - 40 * i, 190, 170))
        ImageDraw.Draw(im).rectangle([200, 300, 1700, 1100], fill=(250, 250, 245))
        im.save(p, quality=95)
    photos.append(p)
CARDS = [
    {"heading": "The calendar that outlived its empire", "body": "A shoebox of bronze tracked the Sun and Moon."},
    {"heading": "Çatalhöyük under Kraków", "body": "Divers raised 82 fragments in 1901, and nobody knew what they were for decades."},
    {"heading": "Then and now: the harbour compared", "body": "Unlike the modern port, the Roman harbour faced the open sea and survived it."},
    {"heading": "A gear train of 30 meshing wheels", "body": "The tunnel and shaft structure was engineered to channel the water through the block."},
    {"heading": "It weighed 1,200 tons", "body": "About 20,000 people lived in levels 85 meters deep, with ventilation and rolling stone doors."},
    {"heading": "An extraordinarily long heading that will not fit on a single line at all", "body": " ".join(["The body keeps going with more words than the panel can hold."] * 5)},
    {"heading": "Who built it?", "body": "Was it astronomy or metalwork that made it possible? Tell us what you think."},
]
SHARE = "Send this to a friend who loves gears, and follow for the next relic."
POLL = {"question": "Astronomy or metalwork?", "options": ["The astronomy", "The metalwork"]}
ARCHETYPES = ("cover", "immersive", "comparison", "blueprint", "colossal_stat", "debate", "editorial_split")
gen = MultiFormatGenerator()
res = {}
for ci, card in enumerate(CARDS):
    for archetype in ARCHETYPES:
        for tight in (False, True):
            for facebook in (False, True):
                im, lay = gen._render_carousel_slide(archetype, photos[ci % 3], photos[(ci + 1) % 3], card, ci, 7, POLL,
                                                     SHARE, facebook, tight=tight)
                d = lay.to_dict()
                res[f"{ci}/{archetype}/{'tight' if tight else 'normal'}/{'fb' if facebook else 'ig'}"] = {
                    "size": list(im.size), "layout": d, "problems": lay.problems(),
                    "pixels": hashlib.sha256(im.tobytes()).hexdigest()}
out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
print(len(res), "slides;", sum(1 for v in res.values() if v["problems"]), "with problems")
