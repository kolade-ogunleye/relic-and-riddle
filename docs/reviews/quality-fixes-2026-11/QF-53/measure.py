"""python measure.py <repo root>: generated photo-pool stress test, no network.

The images are synthetic textures, not historical photographs. The measured
claim is deterministic duplicate/source/class enforcement, not visual merit.
Actual sourcing/download/review code runs against mocked searches and a model
that accepts every image (including duplicates). Originals stay on disk.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def fixtures():
    pages, bodies, classes = [], {}, {}
    for index, artist in enumerate(["Photographer A"] * 20 + ["Photographer B"] * 8 + ["Photographer C"] * 8):
        kind = ("wide", "interior", "detail")[index % 3]
        title = f"File:Temple {kind} view {index:02d}.jpg"
        url = f"https://upload.wikimedia.org/fixture-{index:02d}.jpg"
        seed = 0 if index < 5 else index
        pixels = np.random.default_rng(seed).integers(0, 256, (32, 32, 3), dtype=np.uint8)
        data = io.BytesIO()
        Image.fromarray(pixels).resize((3000, 2400), Image.Resampling.BICUBIC).save(data, "JPEG", quality=95)
        bodies[url] = data.getvalue()
        classes[title] = kind
        pages.append({"title": title, "imageinfo": [{
            "mime": "image/jpeg", "width": 3000, "height": 2400, "size": len(bodies[url]),
            "url": url, "descriptionurl": "https://commons.wikimedia.org/wiki/" + title,
            "extmetadata": {"LicenseShortName": {"value": "CC0"}, "Artist": {"value": artist}}}]})
    return pages, bodies, classes


class Response:
    status_code = 200

    def __init__(self, data):
        self.content = data
        self.headers = {"Content-Length": str(len(data))}

    def iter_content(self, size=65536):
        for index in range(0, len(self.content), size):
            yield self.content[index:index + size]

    def raise_for_status(self):
        pass

    def close(self):
        pass


def independent_metrics(folder, images, classes):
    from src.image_quarantine import phash
    hashes = []
    for record in images:
        with Image.open(folder / record["file"]) as image:
            a = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS))
            d = 0
            for bit in (a[:, 1:] > a[:, :-1]).flat:
                d = (d << 1) | int(bit)
            hashes.append((phash(image), d))
    distances = [min((left[0] ^ right[0]).bit_count(), (left[1] ^ right[1]).bit_count())
                 for index, left in enumerate(hashes) for right in hashes[:index]]
    authors = Counter(record["artist"] for record in images)
    kinds = Counter(classes[record["title"]] for record in images)
    return {"photos": len(images), "pairs_within_ten_bits": sum(d <= 10 for d in distances),
            "minimum_pair_distance": min(distances, default=None),
            "max_author_share": max(authors.values(), default=0) / max(1, len(images)),
            "authors": dict(authors), "shot_classes_from_fixture_truth": dict(kinds)}


def main():
    from scripts import fetch_commons_images as fetch
    from src import commons, image_review, url_guard
    from src.credits import load_credits

    pages, bodies, classes = fixtures()
    with tempfile.TemporaryDirectory(prefix="qf53-") as temporary:
        root = Path(temporary)
        folder = root / "fixture"
        topic = {"id": "fixture", "title": "Temple"}

        def all_yes(*args, **kwargs):
            records = load_credits(folder)["images"][:image_review.MAX_PHOTOS]
            return image_review.PoolReview(verdicts=[image_review.PhotoVerdict(
                index=i + 1, shows_topic="yes", usable=True, safe=True, reason="Mock accepts every image",
                shot_class=classes[record["title"]]) for i, record in enumerate(records)])

        with contextlib.ExitStack() as stack:
            for target, replacement in (
                ("scripts.fetch_commons_images.search", lambda *a, **k: pages),
                ("scripts.fetch_commons_images.search_graded", lambda *a, **k: []),
                ("scripts.fetch_commons_images.find_categories", lambda *a, **k: []),
                ("scripts.fetch_commons_images.search_openverse", lambda *a, **k: []),
                ("src.storyboard.generate_storyboard", lambda *a, **k: None),
                ("src.image_review.image_block", lambda *a, **k: {"type": "image"}),
                ("src.image_review.free_llm.call", all_yes),
            ):
                stack.enter_context(patch(target, replacement))
            stack.enter_context(patch.object(url_guard, "_addresses", return_value=["1.1.1.1"]))
            stack.enter_context(patch.object(commons.requests, "get", side_effect=lambda url, **k: Response(bodies[url])))
            stack.enter_context(patch.object(commons.time, "sleep", return_value=None))
            with contextlib.redirect_stdout(io.StringIO()):
                fetch.fetch_topic(topic, {"queries": ["Temple"], "keywords": ["temple"], "museums": False,
                                         "exclude": ["unrelated fixture"]},
                                  allow_sa=False, target_images=24, assets_dir=root)
            fetched = load_credits(folder)
            before_review = independent_metrics(folder, fetched["images"], classes)
            summary = image_review.review_pool(topic, folder)
            reviewed = load_credits(folder)
            result = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                      "inputs": "36 generated textures: 20/8/8 author burst; first five exact duplicates; three known shot classes",
                      "model": "mock accepts all photos; no live API",
                      "after_fetch": before_review,
                      "after_review": independent_metrics(folder, reviewed["images"], classes),
                      "approval_status": summary["status"],
                      "reserves": len(reviewed.get("diversity_reserve", [])),
                      "original_files_retained": len(list(folder.glob("*_commons.jpg")))}
    metric = result["after_review"]
    result["acceptance_met"] = (metric["pairs_within_ten_bits"] == 0 and metric["max_author_share"] <= 0.35
                                and len(metric["shot_classes_from_fixture_truth"]) >= 3)
    text = json.dumps(result, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
