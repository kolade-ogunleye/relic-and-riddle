"""QF-82 evidence: one real development run's covers drawn again with the code at a repo root, as a half-size sheet.

Usage: render_evidence.py REPO_ROOT RUN_DIR OUT_JPEG

RUN_DIR is a development run_* folder (output/ is gitignored) whose delivered cover has a recorded recipe and
whose recorded hero still exists. Every JPEG the renderer leaves in a fresh folder is laid out left to right at half
size with its file name, so the sheet shows exactly what a new run folder holds. Development data only.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root, run, out = (Path(a).resolve() for a in sys.argv[1:4])
    sys.path.insert(0, str(root))
    import logging
    logging.disable(logging.CRITICAL)
    from PIL import Image, ImageDraw, ImageFont

    from src.visual_engine import VisualEngine
    record = json.loads((run / "still_layout.json").read_text(encoding="utf-8"))["thumbnail_cover.jpg"]["recipe"]
    payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="qf82-evidence-") as directory:
        folder = Path(directory) / "run"
        VisualEngine().generate_thumbnails(Path(record["hero"]), payload, folder, style=record.get("style") or "badge")
        files = sorted(folder.glob("*.jpg"))
        tiles = []
        for path in files:
            with Image.open(path) as im:
                tiles.append((path.name, im.convert("RGB").resize((im.width // 2, im.height // 2))))
    gap, label = 24, 56
    width = sum(t.width for _, t in tiles) + gap * (len(tiles) + 1)
    height = max(t.height for _, t in tiles) + label + gap * 2
    sheet = Image.new("RGB", (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=28)
    x = gap
    for name, tile in tiles:
        sheet.paste(tile, (x, gap))
        draw.text((x, gap + tile.height + 8), name.replace("thumbnail_", ""), fill=(230, 230, 230), font=font)
        x += tile.width + gap
    draw.text((gap, height - 36), f"{len(tiles)} cover files in a new run folder", fill=(255, 210, 120), font=font)
    sheet.save(out, quality=72)  # under the 300 KB evidence limit
    print(json.dumps({"files": [name for name, _ in tiles], "sheet": [width, height]}))


if __name__ == "__main__":
    main()
