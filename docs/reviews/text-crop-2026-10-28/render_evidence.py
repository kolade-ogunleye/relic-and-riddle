"""Evidence for docs/reviews/text-crop-2026-10-28: Trepanation photo 14 (accession label at the bottom edge),
cropped by src.beat_images.crop_text_band with the text box a vision verdict would give (measured here by hand
from the pixels; no model call)."""
import json
import sys
from pathlib import Path

from PIL import Image

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
from src import beat_images  # noqa: E402
from src.lineage import source_chain  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
beat_images.TEXT_CROP_DIR = Path(sys.argv[3])
src = ROOT / "assets/trepanation_miracle/14_commons.jpg"
box = [871, 322, 940, 597]  # the "A650847" card, [ymin, xmin, ymax, xmax] of 1000
crop = beat_images.crop_text_band(src, "bottom", box)


def half(im, path):
    im = im.convert("RGB")
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(path, "JPEG", quality=85)


def frame(im):
    """The 1080x1920 cover crop a scene starts from (centre)."""
    w, h = im.size
    tw = min(w, int(h * 9 / 16))
    th = int(tw * 16 / 9)
    x, y = (w - tw) // 2, (h - th) // 2
    return im.crop((x, y, x + tw, y + th)).resize((1080, 1920), Image.LANCZOS)


with Image.open(src) as a, Image.open(crop) as b:
    half(a, OUT / "trepanation_14_before.jpg")
    half(b, OUT / "trepanation_14_after.jpg")
    half(frame(a), OUT / "trepanation_14_frame_before.jpg")
    half(frame(b), OUT / "trepanation_14_frame_after.jpg")
    chain = source_chain(crop)
    m = {"source": str(src.relative_to(ROOT)), "source_size": list(a.size), "crop_size": list(b.size),
         "text_box": box, "edge": "bottom", "cut_fraction": round(beat_images._crop_band("bottom", box), 4),
         "crop_name": crop.name, "lineage_operation": json.loads(Path(str(crop) + ".lineage.json").read_text())["operation"],
         "lineage_sources": [Path(c["path"]).name for c in chain if c.get("kind") != "lineage"]}
(OUT / "measurements.json").write_text(json.dumps(m, indent=1), encoding="utf-8", newline="\n")
print(json.dumps(m, indent=1))
