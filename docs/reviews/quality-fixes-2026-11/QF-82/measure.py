"""Measure actual cover outputs and selection; one repo-root argument, offline."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path


def measure(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from PIL import Image, ImageDraw

    from src import cover_selector, still_layout
    from src.visual_engine import VisualEngine

    copy = {"eyebrow": "ANCIENT ENGINEERING", "line1": "STONE THAT", "line2": "STILL STANDS",
            "tagline": "What held it together?"}
    rows = [{"run_id": f"{style}-{i}", "platform": "instagram", "unavailable": None,
             "cover_style": style, "skip_rate": 0 if style == "depth_sandwich" else 99,
             "watch_ratio": 1 if style == "depth_sandwich" else .01,
             "share_rate": .1 if style == "depth_sandwich" else 0,
             "save_rate": .1 if style == "depth_sandwich" else 0}
            for i in range(24) for style in ("badge", "depth_sandwich")]
    decisions = [cover_selector.choose(copy, rows, previous="badge", seed=f"measurement-{i}")
                 for i in range(24)]
    with tempfile.TemporaryDirectory(prefix="qf82-") as directory:
        work = Path(directory)
        hero = work / "hero.png"
        image = Image.new("RGB", (1600, 2400), (38, 49, 61))
        draw = ImageDraw.Draw(image)
        for y in range(2400):
            draw.line((0, y, 1599, y), fill=(38 + y // 80, 49 + y // 100, 61 + y // 120))
        draw.rectangle((450, 350, 1150, 1900), fill=(120, 108, 91))
        image.save(hero)
        output = work / "run"
        cover, grid = VisualEngine().generate_thumbnails(hero, {"thumbnail": copy}, output)
        images = sorted(path.name for path in output.glob("*.jpg"))
        variants = [name for name in images if name.startswith("thumbnail_variant_")]
        sizes = {}
        for path in (cover, grid):
            with Image.open(path) as delivered:
                sizes[path.name] = list(delivered.size)
        proof = still_layout.load(cover) or {}
        hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (cover, grid)}
    fixed = sum(style == "badge" and "frozen" in why for style, why in decisions)
    return {"generated_cover_images": images, "unused_variant_count": len(variants),
            "fixed_choices": fixed, "selection_trials": len(decisions), "selection_styles": sorted({s for s,w in decisions}),
            "delivered_dimensions": sizes, "delivered_sha256": hashes, "layout_record_preserved": bool(proof),
            "acceptance_met": len(images) == 2 and not variants and fixed == len(decisions) and bool(proof)
            and sizes == {"thumbnail_cover.jpg": [1080, 1920], "thumbnail_profile_grid.jpg": [1080, 1080]},
            "limitations": "Offline procedural fixture measures execution/output policy, not photo or design-ceiling quality. Historical run artifacts are preserved; no real cover-specific signal is imported or invented."}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: measure.py REPO_ROOT")
    print(json.dumps(measure(Path(sys.argv[1]).resolve()), indent=2, sort_keys=True))
