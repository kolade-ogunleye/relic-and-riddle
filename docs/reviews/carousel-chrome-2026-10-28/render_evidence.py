"""Evidence renders for docs/reviews/carousel-chrome-2026-10-28: the production copy of Kailasa (whose carousel
caption ended in a pointing hand) and Richat 2, drawn by one version of the real carousel and Story renderers (no
model calls; photos in /photos sheet order). Every ImageDraw text, line and dot is spied on, so before and after
are measured the same way: what is drawn under Instagram's counter (the right 280 px of the top 140 px), and
whether any slide chrome (dossier counter, progress bar, dots, SWIPE cue) or arrow is drawn.

usage: python render_evidence.py <code_root> <label> <out_dir> <repo_root>"""
import json
import os
import sys
from pathlib import Path

code_root, label, out_dir, repo = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
os.environ["RELIC_APP_ROOT"] = str(repo)            # fonts and assets from the repository, for both versions
sys.path.insert(0, str(code_root))
from PIL import Image, ImageDraw  # noqa: E402

from src.multi_format import MultiFormatGenerator  # noqa: E402

PROD = Path(r"C:\tmp\audit\prod\output")
RUNS = {"kailasa_temple_monolith": "run_kailasa_temple_monolith_1792854124",
        "richat_structure_sahara_eye": "run_richat_structure_sahara_eye_1793040996"}
COUNTER = (1080 - 280, 0, 1080, 140)
ARROWS = "→←↑↓➡⬅⬆⬇👉👈👆👇☝"
MultiFormatGenerator._match_photos = lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))]

calls = []
_text, _line, _ellipse = ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.line, ImageDraw.ImageDraw.ellipse


def spy_text(self, xy, text, fill=None, font=None, anchor=None, *a, **k):
    out = _text(self, xy, text, fill, font, anchor, *a, **k)
    if str(text).strip():
        box = self.textbbox(xy, text, font=font, anchor=anchor, stroke_width=k.get("stroke_width", 0))
        calls.append({"kind": "text", "text": str(text), "box": [int(v) for v in box]})
    return out


def spy_line(self, xy, *a, **k):
    out = _line(self, xy, *a, **k)
    pts = list(xy)
    if len(pts) == 2 and pts[0][1] == pts[1][1] and pts[0][1] > 1250 and k.get("width", 1) in (2, 3):
        calls.append({"kind": "progress_bar", "text": "", "box": [int(pts[0][0]), int(pts[0][1]) - 2, int(pts[1][0]), int(pts[1][1]) + 2]})
    return out


def spy_ellipse(self, xy, *a, **k):
    out = _ellipse(self, xy, *a, **k)
    calls.append({"kind": "dot", "text": "", "box": [int(v) for v in xy]})
    return out


ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.line, ImageDraw.ImageDraw.ellipse = spy_text, spy_line, spy_ellipse
saved = []
_save = Image.Image.save


def spy_save(self, fp, *a, **k):
    saved.append((Path(str(fp)), list(calls)))
    calls.clear()
    return _save(self, fp, *a, **k)


Image.Image.save = spy_save


def hits(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def sheet_photos(topic):
    folder = repo / "assets" / topic
    images = json.load(open(folder / "credits.json", encoding="utf-8"))["images"]
    return [folder / im["file"] for im in images if (folder / im["file"]).is_file()][:7]


def half(src: Path, dst: Path, overlay: bool = False):
    """Half size; `overlay` also marks where Instagram draws its "N/M" counter (a mock pill, outlined region)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(src).convert("RGB")
    if overlay:
        d = ImageDraw.Draw(im, "RGBA")
        _line(d, [(COUNTER[0], COUNTER[3]), (COUNTER[2] - 1, COUNTER[3])], fill=(255, 60, 60, 255), width=3)
        _line(d, [(COUNTER[0], 0), (COUNTER[0], COUNTER[3])], fill=(255, 60, 60, 255), width=3)
        d.rounded_rectangle([930, 39, 1041, 111], radius=36, fill=(20, 20, 20, 190))
        from src.typography import get_font
        _text(d, (985, 75), "2/7", fill=(255, 255, 255), font=get_font("Montserrat", 36), anchor="mm")
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(dst, quality=85)


results = {}
work = Path("C:/tmp/rr_evidence_chrome/work") / label
for topic, run in RUNS.items():
    p = json.load(open(PROD / run / "verified_script.json", encoding="utf-8"))
    payload = p.get("payload", p)
    photos = sheet_photos(topic)
    short = topic.split("_")[0]
    gen = MultiFormatGenerator()
    for facebook in (False, True):
        copy = "carousel_fb" if facebook else "carousel"
        calls.clear(); saved.clear()
        gen.generate_carousel_sequence(photos, payload, work / topic, facebook=facebook)
        for path, elements in list(saved):
            texts = [e for e in elements if e["kind"] == "text"]
            results[f"{short}/{copy}/{path.name}"] = {
                "under_ig_counter": [e["text"] or e["kind"] for e in elements if hits(e["box"], COUNTER)],
                "chrome": sorted({e["kind"] for e in elements if e["kind"] in ("progress_bar", "dot")}
                                 | {e["text"] for e in texts if "SWIPE" in e["text"] or "ARCHIVAL RECORD" in e["text"]}),
                "arrows": [e["text"] for e in texts if any(c in e["text"] for c in ARROWS)],
                "brand_box": next((e["box"] for e in texts if e["text"] == "RELIC & RIDDLE"), None),
            }
            half(path, out_dir / label / short / copy / path.name)
            if not facebook:
                half(path, out_dir / label / short / "instagram_overlay" / path.name, overlay=True)
    calls.clear(); saved.clear()
    gen.generate_story_sequence(photos[0], payload, work / topic, scene_images=photos)
    for path, elements in list(saved):
        results[f"{short}/story/{path.name}"] = {"arrows": [e["text"] for e in elements if e["kind"] == "text" and any(c in e["text"] for c in ARROWS)]}
        half(path, out_dir / label / short / "story" / path.name)
    # The captions each platform's request carries (the after version only has the rule).
    try:
        from src.publisher import _caption_for
        caption = payload.get("carousel_caption") or ""
        results[f"{short}/captions"] = {"instagram": _caption_for("carousel", caption, ["instagram"]),
                                        "facebook": _caption_for("carousel", caption, ["facebook"])}
    except ImportError:
        results[f"{short}/captions"] = {"instagram": payload.get("carousel_caption"), "facebook": payload.get("carousel_caption")}

(out_dir / label / "measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
for k, v in results.items():
    print(label, k, json.dumps(v, ensure_ascii=False)[:220])
