"""Evidence renders for docs/reviews/still-layout-2026-10-28: the production copy of Antikythera 1 and Richat 2
through one version of the real still renderers (photos rotated, no model calls), measured by a spy on every
ImageDraw.text / rounded_rectangle call, so before and after are measured the same way.

usage: python render.py <code_root> <label> <out_dir> <repo_root>"""
import json
import sys
from pathlib import Path

code_root, label, out_dir, repo = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
sys.path.insert(0, str(code_root))
from PIL import Image, ImageDraw  # noqa: E402

from src.multi_format import MultiFormatGenerator  # noqa: E402
from src.visual_engine import VisualEngine  # noqa: E402

PROD = Path(r"C:\tmp\audit\prod\output")
RUNS = {"antikythera_mechanism": ("run_antikythera_mechanism_1792933211", "minimal"),
        "richat_structure_sahara_eye": ("run_richat_structure_sahara_eye_1793040996", "lower_third")}
MultiFormatGenerator._match_photos = lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))]

calls = []
_text, _rr = ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.rounded_rectangle


def spy_text(self, xy, text, fill=None, font=None, anchor=None, *a, **k):
    out = _text(self, xy, text, fill, font, anchor, *a, **k)
    black = isinstance(fill, tuple) and tuple(fill[:3]) == (0, 0, 0)          # a drop shadow, not a line of text
    if str(text).strip() and not black:
        box = self.textbbox(xy, text, font=font, anchor=anchor, stroke_width=k.get("stroke_width", 0))
        calls.append({"kind": "text", "text": str(text), "box": [int(v) for v in box], "size": list(self.im.size)})
    return out


def spy_rr(self, xy, *a, **k):
    out = _rr(self, xy, *a, **k)
    box = [int(v) for v in (xy if len(xy) == 4 else (*xy[0], *xy[1]))]
    fill = k.get("fill")
    if isinstance(fill, tuple) and tuple(fill[:3]) == (0, 0, 0):       # the cover's blurred halo, not a badge
        return out
    calls.append({"kind": "pill", "text": "", "box": box, "size": list(self.im.size)})
    return out


ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.rounded_rectangle = spy_text, spy_rr


def inside(b, z):
    return b[0] >= z[0] and b[1] >= z[1] and b[2] <= z[2] and b[3] <= z[3]


def hits(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def measure(elements, size, zone):
    """Problems in the drawn boxes: past the canvas or zone, or overlapping (a text inside its own pill and the
    halo/shading layers do not count)."""
    w, h = size
    pills = [e for e in elements if e["kind"] == "pill" and e["size"] == [w, h]]
    texts = [e for e in elements if e["kind"] == "text" and e["size"] == [w, h]]
    own = [t for t in texts if not any(inside(t["box"], p["box"]) for p in pills)]
    items = own + pills
    found = []
    for e in items:
        name = e["text"][:32] or "pill"
        if not inside(e["box"], (0, 0, w, h)):
            found.append(f"cut off by the canvas: '{name}' {e['box']}")
        elif not inside(e["box"], zone):
            found.append(f"outside the safe zone {list(zone)}: '{name}' {e['box']}")
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if hits(a["box"], b["box"]):
                found.append(f"overlap: '{a['text'][:28] or 'pill'}' {a['box']} x '{b['text'][:28] or 'pill'}' {b['box']}")
    return found


def sheet_photos(topic):
    """The topic's photos in /photos sheet order (credits.json), the first seven that are on disk."""
    folder = repo / "assets" / topic
    images = json.load(open(folder / "credits.json", encoding="utf-8"))["images"]
    return [folder / im["file"] for im in images if (folder / im["file"]).is_file()][:7]


def half(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB")
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(dst, quality=85)


results = {}
saved = []                                       # (path, the draw calls since the previous save)
_save = Image.Image.save


def spy_save(self, fp, *a, **k):
    saved.append((Path(str(fp)), list(calls)))
    calls.clear()
    return _save(self, fp, *a, **k)


Image.Image.save = spy_save
work = Path("C:/tmp/rr_evidence/work") / label
from src import safe_zones  # noqa: E402
STORY_ZONE = safe_zones.safe_box("story")
COVER_ZONE = (0, 420, 1080, 1500)
CAROUSEL_ZONE = (40, 30, 1040, 1320)
for topic, (run, style) in RUNS.items():
    p = json.load(open(PROD / run / "verified_script.json", encoding="utf-8"))
    payload = p.get("payload", p)
    photos = sheet_photos(topic)
    short = topic.split("_")[0]
    gen = MultiFormatGenerator()

    calls.clear(); saved.clear()
    gen.generate_story_sequence(photos[0], payload, work / topic, scene_images=photos)
    for k, (path, elements) in enumerate(list(saved)):
        body = payload["story_slides"][k]["body"]
        drawn = " ".join(c["text"] for c in elements if c["kind"] == "text")
        found = measure(elements, (1080, 1920), STORY_ZONE)
        if k < len(payload["story_slides"]) - 1 and " ".join(body.split()) not in " ".join(drawn.split()):
            found.append("its body is not drawn whole")
        results[f"{short}/story/{path.name}"] = found
        half(path, out_dir / label / short / "story" / path.name)

    calls.clear(); saved.clear()
    gen.generate_carousel_sequence(photos, payload, work / topic)
    for path, elements in list(saved):
        # the cues are lines and dots, not text: their band is one element
        elements = elements + [{"kind": "pill", "text": "swipe cues", "box": [80, 1350 - 67, 1000, 1307], "size": [1080, 1350]}]
        results[f"{short}/carousel/{path.name}"] = measure(elements, (1080, 1350), CAROUSEL_ZONE)
        half(path, out_dir / label / short / "carousel" / path.name)

    calls.clear(); saved.clear()
    cover, _ = VisualEngine().generate_thumbnails(photos[0], payload, work / f"{topic}_cover", style=style, variants=False)
    elements = next(c for pth, c in saved if pth.name == "thumbnail_cover.jpg")
    t = payload["thumbnail"]
    drawn = [c["text"] for c in elements if c["kind"] == "text"]
    found = measure(elements, (1080, 1920), COVER_ZONE)
    for key in ("eyebrow", "line1", "line2", "tagline"):
        if t.get(key) and t[key].upper() not in drawn:
            found.append(f"missing from the cover: {key} '{t[key].upper()}'")
    results[f"{short}/cover/{style}"] = found
    half(cover, out_dir / label / short / f"cover_{style}.jpg")

# Stress copy (longer than the script stage allows), through the same entry points: what the layout does at
# its limits. Photos: the Antikythera set.
STRESS = {"title": "T", "poll": {"question": "Which mattered more?", "options": ["The astronomy", "The metalwork"]},
          "carousel_slides": [
              {"heading": "THE CALENDAR THAT OUTLIVED THE EMPIRE THAT BUILT IT",
               "body": "A shoebox of bronze that tracked the Sun, the Moon and the games of Olympia for a century of sailors."},
              {"heading": "A CALENDAR OF THE HEAVENS IN YOUR HANDS",
               "body": "Turn the crank and the dials show the Moon's phase, its place in the zodiac, the next eclipse and the year of the "
                       "next games, all from one train of hand-cut teeth that nobody would match for a thousand years."},
              {"heading": "THIRTY INTERLOCKING GEARWHEELS SURVIVE",
               "body": "The mechanism's surviving fragments hold thirty gearwheels, the largest about thirteen centimetres across, each "
                       "tooth filed by hand to a triangle, with inscriptions on every face that explain what each dial shows."},
              {"heading": "WHAT WAS FORGOTTEN, AND WHY DID NOBODY BUILD ANOTHER?",
               "body": "If a workshop could make this two thousand years ago, what else did the ancient world know that we lost?"}],
          "thumbnail": {"eyebrow": "ANCIENT ASTRONOMICAL COMPUTER", "line1": "THE SHIPWRECK THAT HELD",
                        "line2": "THE OLDEST COMPUTER ON EARTH", "tagline": "THIRTY HAND-CUT BRONZE GEARS STILL TURN"}}
photos = sheet_photos("antikythera_mechanism")
gen = MultiFormatGenerator()
calls.clear(); saved.clear()
gen.generate_carousel_sequence(photos, STRESS, work / "stress")
for path, elements in list(saved):
    elements = elements + [{"kind": "pill", "text": "swipe cues", "box": [80, 1350 - 67, 1000, 1307], "size": [1080, 1350]}]
    results[f"stress/carousel/{path.name}"] = measure(elements, (1080, 1350), CAROUSEL_ZONE)
    half(path, out_dir / label / "stress" / "carousel" / path.name)
for style in ("badge", "lower_third", "minimal", "depth_sandwich"):
    calls.clear(); saved.clear()
    cover, _ = VisualEngine().generate_thumbnails(photos[0], STRESS, work / f"stress_{style}", style=style, variants=False)
    elements = next(c for pth, c in saved if pth.name == "thumbnail_cover.jpg")
    drawn = [c["text"] for c in elements if c["kind"] == "text"]
    found = measure(elements, (1080, 1920), COVER_ZONE)
    for key in ("eyebrow", "line1", "line2", "tagline"):
        if not any(STRESS["thumbnail"][key].upper().split()[0] in d for d in drawn):
            found.append(f"missing from the cover: {key}")
    results[f"stress/cover/{style}"] = found
    half(cover, out_dir / label / "stress" / f"cover_{style}.jpg")

(out_dir / label / "measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
for k, v in results.items():
    print(label, k, "OK" if not v else v)
