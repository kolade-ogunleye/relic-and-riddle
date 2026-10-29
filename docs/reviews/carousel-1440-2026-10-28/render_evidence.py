"""Evidence renders for docs/reviews/carousel-1440-2026-10-28: the production scripts of Roman concrete and Nazca
(the two topics of docs/reviews/carousel-content-2026-10-28), drawn by one version of the real carousel renderer
(no model calls; photos in /photos sheet order, rotated the same way for both versions). Each photo is the copy
the pipeline gives the carousel: Real-ESRGAN's cached upscale (cache/upscaled) for a source under the video's
resolution bar, else the original; nothing is upscaled here.

Per slide: its size, file size, the layout check (still_layout.problems), what was shortened, the boxes in
1080-wide design px (box x 1080 / width, to compare the versions), and how many source pixels each output
pixel of its photo comes from (below 1.0 the photo is enlarged). A lossless copy of every slide is kept in the
work folder for the JPEG settings sweep (jpeg_sweep.py).

usage: python render_evidence.py <code_root> <label> <out_dir> <repo_root>"""
import json
import os
import sys
from pathlib import Path

code_root, label, out_dir, repo = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
os.environ["RELIC_APP_ROOT"] = str(repo)            # fonts and assets from the repository, for both versions
sys.path.insert(0, str(code_root))
from PIL import Image  # noqa: E402

from src import still_layout  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402
from src.upscale import _cache_path, backend, needs_upscale  # noqa: E402

RUNS = {"roman_self_healing_concrete": "run_roman_self_healing_concrete_1793163918",
        "nazca_lines_runway_paradox": "run_nazca_lines_runway_paradox_1792961797"}
MultiFormatGenerator._match_photos = lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))]
DISPLAY = (540, 675)                                 # both versions shown at the same size (half of 1080 wide)
work = Path("C:/tmp/rr_c1440/work") / label
lossless = []
_save = Image.Image.save


def spy_save(self, fp, *a, **k):
    if str(fp).endswith(".jpg") and "carousel" in str(fp):
        lossless.append((Path(str(fp)), self.copy()))
    return _save(self, fp, *a, **k)


Image.Image.save = spy_save


def pipeline_copy(photo: Path) -> Path:
    """The copy the pipeline hands the carousel (upscale.ensure_resolution's cached result), never computed here."""
    if needs_upscale(photo) and backend() != "none":
        cached = _cache_path(photo, backend())
        if cached.is_file():
            return cached
    return photo


def sheet_photos(topic):
    folder = repo / "assets" / topic
    images = json.load(open(folder / "credits.json", encoding="utf-8"))["images"]
    return [pipeline_copy(folder / im["file"]) for im in images if (folder / im["file"]).is_file()][:7]


def photo_density(photo: Path, region) -> float:
    """Source pixels per output pixel when `photo` fills `region` (w, h) by cover crop."""
    with Image.open(photo) as im:
        w, h = im.size
    return round(min(w / region[0], h / region[1]), 2)


REGIONS = {"cover": 1.0, "immersive": 1.0, "comparison": 380 / 1350, "blueprint": 620 / 1350,
           "colossal_stat": 520 / 1350, "debate": 560 / 1350, "editorial_split": 760 / 1350}
results = {}
for topic, run in RUNS.items():
    p = json.load(open(repo / "output" / run / "verified_script.json", encoding="utf-8"))
    payload = p.get("payload", p)
    photos = sheet_photos(topic)
    short = topic.split("_")[0]
    gen = MultiFormatGenerator()
    cw, chh = gen.CAROUSEL_SIZE
    for facebook in (False, True):
        copy = "carousel_fb" if facebook else "carousel"
        lossless.clear()
        slides = gen.generate_carousel_sequence(photos, payload, work / topic, facebook=facebook)
        for path, im in lossless:
            (work / "png" / short / copy).mkdir(parents=True, exist_ok=True)
            _save(im, work / "png" / short / copy / (path.stem + ".png"))
        for i, slide in enumerate(slides):
            rec = still_layout.load(slide) or {}
            w, h = Image.open(slide).size
            design = 1080 / w
            archetype = rec.get("recipe", {}).get("archetype")
            region = (w, round(h * REGIONS[archetype]))
            results[f"{short}/{copy}/{slide.name}"] = {
                "archetype": archetype, "size": [w, h], "bytes": slide.stat().st_size,
                "problems": still_layout.problems(rec), "shortened": rec.get("shortened"), "attempts": rec.get("attempts"),
                "photo": Path(rec.get("recipe", {}).get("photo", "")).name,
                "source_px_per_slide_px": photo_density(photos[i], region),
                "boxes_design_px": {e["name"]: [round(v * design) for v in e["box"]] for e in rec.get("elements", [])},
                "protected_design_px": [[round(v * design) for v in r["box"]] for r in rec.get("protected", [])],
            }
            dst = out_dir / label / short / copy / slide.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(slide) as im:
                _save(im.convert("RGB").resize(DISPLAY, Image.LANCZOS), dst, quality=85)

(out_dir / label).mkdir(parents=True, exist_ok=True)
(out_dir / label / "measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8",
                                                   newline="\n")
for k, v in results.items():
    print(label, k, v["size"], v["bytes"], v["problems"], v["shortened"], v["source_px_per_slide_px"])
