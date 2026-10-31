"""JPEG settings sweep for the 1440x1800 slides: every lossless slide render_evidence.py kept, encoded at each
quality and chroma subsampling, measured for file size and fidelity against the lossless slide: PSNR of the
whole slide and of its text (the union of its recorded text and badge boxes), on luma and on chroma (the gold
type and the red/gold strokes live in chroma, which 4:2:0 halves).

usage: python jpeg_sweep.py <work_label_dir> <out.json>"""
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

work, out = Path(sys.argv[1]), Path(sys.argv[2])
SETTINGS = [(q, sub) for q in (90, 92, 95, 97, 98, 100) for sub in (2, 0)]   # Pillow: 2 = 4:2:0, 0 = 4:4:4
NAMES = {2: "4:2:0", 0: "4:4:4"}


def psnr(a, b):
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 99.0 if mse == 0 else round(10 * np.log10(255 ** 2 / mse), 2)


def text_mask(record, size):
    mask = np.zeros((size[1], size[0]), bool)
    for e in record.get("elements", []):
        x0, y0, x1, y1 = e["box"]
        mask[max(0, y0):y1, max(0, x0):x1] = True
    return mask


rows = {f"q{q} {NAMES[s]}": {"bytes": [], "psnr_y": [], "psnr_text_y": [], "psnr_text_chroma": []} for q, s in SETTINGS}
for png in sorted((work / "png").rglob("*.png")):
    short, copy = png.parent.parent.name, png.parent.name
    topic_dir = next(d for d in work.iterdir() if d.is_dir() and d.name.startswith(short))
    record = json.loads((topic_dir / copy / "still_layout.json").read_text(encoding="utf-8"))[png.stem + ".jpg"]
    ref = Image.open(png).convert("RGB")
    ref_ycc = np.asarray(ref.convert("YCbCr"))
    mask = text_mask(record, ref.size)
    for q, s in SETTINGS:
        buf = io.BytesIO()
        ref.save(buf, "JPEG", quality=q, subsampling=s)
        dec = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB").convert("YCbCr"))
        r = rows[f"q{q} {NAMES[s]}"]
        r["bytes"].append(buf.tell())
        r["psnr_y"].append(psnr(ref_ycc[..., 0], dec[..., 0]))
        r["psnr_text_y"].append(psnr(ref_ycc[..., 0][mask], dec[..., 0][mask]))
        r["psnr_text_chroma"].append(psnr(ref_ycc[..., 1:][mask], dec[..., 1:][mask]))
summary = {k: {"slides": len(v["bytes"]), "max_bytes": max(v["bytes"]), "mean_bytes": round(sum(v["bytes"]) / len(v["bytes"])),
               "min_psnr_y": min(v["psnr_y"]), "min_psnr_text_y": min(v["psnr_text_y"]),
               "min_psnr_text_chroma": min(v["psnr_text_chroma"]),
               "mean_psnr_text_chroma": round(sum(v["psnr_text_chroma"]) / len(v["psnr_text_chroma"]), 2)}
           for k, v in rows.items()}
out.write_text(json.dumps(summary, indent=1), encoding="utf-8", newline="\n")
for k, v in summary.items():
    print(k, v)
