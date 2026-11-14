"""Evidence renders for QF-94: a real run's cover and Story frame with a field in a script its face cannot draw.

    python render_evidence.py <tree root> <run folder> <photo> <out dir> <prefix>

Real data: the dev Amber Room run's own copy (verified_script.json) and its photo 2 ("The Amber Room panoramic
photo", whose own Commons record names the room in Russian, "Янтарная комната"). No posted card has a field that
needs a fallback face (measure.py), so the cover's title lines and the Story frame's first headline line carry that
Russian name from the photo's record; everything else is the run's copy as written. Drawn through the tree's own
renderers (visual_engine.generate_thumbnails, multi_format's Story frame) and saved as half-size JPEGs <prefix>_*.jpg.
Run once with the base tree (before) and once with the row's tree (after).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

NAME = "Янтарная комната"


def main(root: Path, run: Path, photo: Path, out: Path, prefix: str) -> None:
    os.environ.setdefault("FONT_SIZE", "80")
    sys.path.insert(0, str(root.resolve()))
    from PIL import Image

    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import VisualEngine
    payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
    first, second = NAME.upper().split()
    thumb = dict(payload["thumbnail"], line1=first, line2=second)
    out.mkdir(parents=True, exist_ok=True)

    def half(im, name):
        im = im.convert("RGB")
        im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(out / f"{prefix}_{name}.jpg", quality=82)

    with tempfile.TemporaryDirectory() as tmp:
        cover, _grid = VisualEngine().generate_thumbnails(photo, {**payload, "thumbnail": thumb}, Path(tmp),
                                                          style="badge", variants=False)
        with Image.open(cover) as im:
            half(im, "cover")
    slide = dict(payload["story_slides"][0], line1=NAME.upper())
    try:
        im, _lay = MultiFormatGenerator()._render_story_frame(photo, slide, 0, 3, payload.get("poll") or {})
        half(im, "story")
    except Exception as e:  # noqa: BLE001  the row's tree refuses a frame it cannot draw whole: say so
        print("story refused:", e)
    print("written", sorted(p.name for p in out.glob(prefix + "_*.jpg")))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5])
