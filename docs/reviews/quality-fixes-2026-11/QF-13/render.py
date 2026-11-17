"""QF-13 evidence boards from real run data.

    python docs/reviews/quality-fixes-2026-11/QF-13/render.py <repo root> <data root> <out dir> [<run name> ...]

For the first still of each named run (default: the last run measure.py would pick), the measured commit's chain
is run as committed and as the lossless reference (measure.py, part B), and one board is written per still:
the 9:16 photo ground (typography.cover_crop) at 1:1 on the most detailed 600 x 600 window, committed | lossless
| |difference| x 32, saved as a half-size JPEG. On the parent the left tile carries the JPEG generations; on this
row the two tiles are identical and the difference is black.
"""

import runpy
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
root, data, out_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
names = sys.argv[4:]
sys.argv = [str(HERE / "measure.py"), str(root), str(data)]
m = runpy.run_path(str(HERE / "measure.py"), run_name="qf13_measure")

runs = sorted(p for p in (data / "output").glob("run_*") if (p / "provenance.json").is_file())
runs = [r for r in runs if r.name in names] if names else [r for r in runs if m["_run_stills"](r, data)][-1:]
out_dir.mkdir(parents=True, exist_ok=True)
T = 600
with tempfile.TemporaryDirectory(prefix="qf13-render-") as tmp:
    for run in runs:
        src, faces = m["_run_stills"](run, data)[0]
        grounds = {}
        for mode, lossless in (("committed", False), ("lossless", True)):
            layer, _, _ = m["_prepare"](src, Path(tmp) / run.name / mode, lossless, faces)
            from src.typography import cover_crop
            grounds[mode] = np.asarray(cover_crop(layer, (1080, 1920)).convert("RGB"))
        ref = grounds["lossless"].astype(np.float32)
        gray = ref.mean(axis=2)
        detail = np.abs(np.diff(gray, axis=0))[:, :-1] + np.abs(np.diff(gray, axis=1))[:-1, :]
        best, at = -1.0, (0, 0)
        for y in range(0, 1920 - T, 60):
            for x in range(0, 1080 - T, 60):
                v = float(detail[y:y + T, x:x + T].mean())
                if v > best:
                    best, at = v, (x, y)
        x, y = at
        a, b = grounds["committed"][y:y + T, x:x + T], grounds["lossless"][y:y + T, x:x + T]
        diff = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * 32, 0, 255).astype(np.uint8)
        board = Image.new("RGB", (3 * T + 40, T + 60), (16, 16, 16))
        for i, (tile, label) in enumerate(((a, "committed chain"), (b, "lossless reference"), (diff, "|diff| x 32"))):
            board.paste(Image.fromarray(tile), (10 + i * (T + 10), 50))
            ImageDraw.Draw(board).text((10 + i * (T + 10), 18), label, fill=(230, 230, 230))
        board = board.resize((board.width // 2, board.height // 2), Image.LANCZOS)
        board.save(out_dir / f"{run.name}_{src.stem}.jpg", quality=88)
        print(out_dir / f"{run.name}_{src.stem}.jpg", f"window {at}", f"max diff {int(np.abs(a.astype(int) - b).max())}")
