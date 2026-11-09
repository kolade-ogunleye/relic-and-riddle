"""Root-only, offline crop-floor and essential-image geometry measurement.

The synthetic candidates exercise admission, not photographic merit. All search
and download responses are generated here; no operator photos or APIs are used.
"""

import contextlib
import importlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from scripts import fetch_commons_images as fetch
    from src import url_guard

    sizes = ([(2250, 3000)] * 9 + [(2200, 2100)] * 16 + [(2000, 1500)] * 12
             + [(8000, 1600)] * 10 + [(800, 600)])
    pages, dimensions = [], {}
    for i, (w, h) in enumerate(sizes):
        url = f"https://upload.wikimedia.org/fixture{i}.jpg"
        dimensions[url] = (w, h)
        pages.append({"title": f"File:Temple fixture{i}.jpg", "imageinfo": [{
            "width": w, "height": h, "size": 2_000_000, "mime": "image/jpeg", "url": url,
            "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]})
    title = pages[-1]["title"]

    class Response:
        def __init__(self, url):
            buffer = io.BytesIO()
            Image.new("RGB", dimensions[url], (180, 170, 160)).save(buffer, "JPEG")
            self.data = buffer.getvalue()
            self.content = self.data
            self.headers = {"Content-Length": str(len(self.data))}

        def iter_content(self, chunk_size):
            for start in range(0, len(self.data), chunk_size):
                yield self.data[start:start + chunk_size]

        def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="qf52-") as temporary:
        assets = Path(temporary)
        with contextlib.ExitStack() as stack:
            for name, replacement in {
                "search": lambda *a, **k: pages,
                "search_graded": lambda *a, **k: [],
                "find_categories": lambda *a, **k: [],
                "search_openverse": lambda *a, **k: [],
            }.items():
                stack.enter_context(patch.object(fetch, name, replacement))
            stack.enter_context(patch.object(url_guard, "follow", lambda url, *a, **k: Response(url)))
            stack.enter_context(patch("src.storyboard.generate_storyboard", lambda *a, **k: None))
            with contextlib.redirect_stdout(io.StringIO()):
                fetch.fetch_topic({"id": "crop-fixture", "title": "Temple"}, {
                    "queries": ["Temple"], "keywords": ["temple"], "museums": False,
                    "exclude": ["unrelated fixture"],
                    "essential_images": {title: "Only surviving inscription view"}},
                    False, target_images=60, assets_dir=assets)
        topic = assets / "crop-fixture"
        records = json.loads((topic / "credits.json").read_text(encoding="utf-8"))["images"]
        automatic = [r for r in records if r.get("presentation") != "full_image"]
        crops = [int(min(r["width"], r["height"] * 9 / 16)) for r in automatic]
        essential = [r for r in records if r.get("presentation") == "full_image"]
        result = {"fixture_candidates": len(pages), "automatic_images": len(automatic),
                  "automatic_under_1080": sum(w < 1080 for w in crops),
                  "automatic_under_1350": sum(w < 1350 for w in crops),
                  "automatic_upscale_fraction": sum(w < 1350 for w in crops) / max(1, len(crops)),
                  "minimum_automatic_crop": min(crops), "essential_exceptions": len(essential),
                  "essential_source_bytes_preserved": all((topic / r["preserved_source"]).is_file() for r in essential)
                                                        if essential else False}
        try:
            presentation = importlib.import_module("src.image_presentation")
        except ModuleNotFoundError:
            result["essential_presentation"] = "unsupported"
        else:
            path = assets / "native.png"
            Image.new("RGB", (200, 150), "white").save(path)
            presentation.designate(path, "Generated native-size geometry fixture")
            sizes_out = {}
            for w, h in [(1080, 1920), (1920, 1080)]:
                data = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                    presentation.fit_filter(path, w, h), "-frames:v", "1", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
                pixels = np.frombuffer(data, np.uint8).reshape(h, w, 3)
                ys, xs = np.where(pixels[..., 0] > 200)
                sizes_out[f"{w}x{h}"] = {"visible_size": [int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)],
                                           "visible_pixels": len(xs)}
            result["essential_presentation"] = sizes_out
    result["source_commit"] = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    destination = root / "docs/reviews/quality-fixes-2026-11/QF-52/measurements.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=2))
