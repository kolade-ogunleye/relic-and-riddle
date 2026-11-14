"""Generated 25-photo pool, mocked model; only input is repository root."""

import base64
import io
import json
import runpy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image


def main(root):
    sys.path.insert(0, str(root))
    from src import image_review
    from src.credits import load_credits
    fixture = runpy.run_path(str(root / "tests/test_qf56_photo_merit.py"))
    calls, dimensions = [], []

    def review(system, content, schema, **kwargs):
        blocks = [item for item in content if item.get("type") == "image"]
        calls.append(len(blocks))
        for block in blocks:
            with Image.open(io.BytesIO(base64.b64decode(block["source"]["data"]))) as image:
                dimensions.append(max(image.size))
        return fixture["accept_batch"](system, content, schema, **kwargs)

    with tempfile.TemporaryDirectory(prefix="qf56-") as temporary:
        folder = Path(temporary)
        fixture["make_pool"](folder, 25)
        with patch.object(image_review.free_llm, "call", review):
            image_review.review_pool({"id": "fixture", "title": "Artifact"}, folder)
        credits = load_credits(folder)
        records = credits["images"] + credits.get("diversity_reserve", []) + credits.get("rejected_by_review", [])
        counts = {"records_retained": len(records),
                  "quality_and_depiction": sum(bool((record.get("vision_review") or {}).get("quality")
                                                    and (record.get("vision_review") or {}).get("depiction")) for record in records),
                  "technical_checks": sum(bool(record.get("technical_review")) for record in records)}
    photos = [{"file": name, "relevance": relevance, "vision_review": {
        "shows_topic": "yes", "usable": True, "safe": True, "quality": quality, "depiction": depiction}}
        for name, relevance, quality, depiction in (("reconstruction", 1000, 5, "reconstruction"), ("original", 1, 3, "original"))]
    result = {"source_commit": "the commit that adds this file",
              "scope": "generated photos; mocked visual decisions; production batching, measurement and ranking",
              "batch_sizes": calls, "review_image_long_edges": sorted(set(dimensions)),
              **counts, "preferred_topic_photo": image_review.usable_photos(photos)[0]["file"]}
    target = root / "docs/reviews/quality-fixes-2026-11/QF-56/measurements.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=2))
