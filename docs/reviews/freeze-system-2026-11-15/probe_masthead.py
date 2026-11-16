"""Probe: the carousel masthead on real runs' recipes (IG + FB), as the tree draws it.

    python probe_masthead.py <tree> <dev checkout> <out.json>
Records per slide: the masthead plate's planned contrast, halo, the delivered ring contrast after the platform
re-encode (960 px, q70), the INK alpha added at the masthead's cap centre (luma ratio before/after) and timings.
"""
import json
import os
import sys
import time
from pathlib import Path

tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(tree))

from src import multi_format as mf
from src.multi_format import MultiFormatGenerator

RUNS = ["run_gol_gumbaz_whispering_gallery_1793278143", "run_nazca_lines_runway_paradox_1793247464",
        "run_richat_structure_sahara_eye_1793272675", "run_nazca_lines_runway_paradox_1793251266"]
res = []
gen = MultiFormatGenerator()
keep = out.parent / (out.stem + "_png")
keep.mkdir(parents=True, exist_ok=True)
for run in RUNS:
    for sub in ("carousel", "carousel_fb"):
        f = dev / "output" / run / sub / "still_layout.json"
        if not f.is_file():
            continue
        for name, rec in sorted(json.loads(f.read_text(encoding="utf-8")).items()):
            r = rec.get("recipe") or {}
            if not r:
                continue
            t0 = time.perf_counter()
            im, lay = gen._render_carousel_slide(
                r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]), int(r["total"]),
                r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")), tight=bool(rec.get("tight")),
                subject=r.get("subject") or "", heading_frame=int(r.get("heading_frame") or 2))
            el = next((e for e in lay.elements if e.get("name") == "header_brand"), {})
            plate = (lay.plates.get("header_brand") or [None])[-1]
            delivered = None
            if plate is not None:
                try:
                    delivered = plate.measure(mf.platform_encode(im))
                except Exception as e:  # noqa: BLE001
                    delivered = f"err {e}"
            row = {"run": run[4:], "sub": sub, "slide": name, "archetype": r["archetype"],
                   "contrast": el.get("contrast"), "unaided": el.get("contrast_unaided"), "halo": el.get("halo"),
                   "delivered": delivered, "mast": lay.meta.get("masthead") or el.get("masthead"),
                   "box": el.get("box"), "s": round(time.perf_counter() - t0, 2)}
            res.append(row)
            im.convert("RGB").crop((0, 0, 760, 260)).save(keep / f"{run[4:30]}_{sub}_{Path(name).stem}.png")
            print(json.dumps(row), flush=True)
out.write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
