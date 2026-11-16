"""What the real Nazca slides that fail closed would need: the masthead halo cap (an operator ruling, NOT changed in the
code) at 0.16 / 0.24 / 0.32, measured by patching the gate in this process only.

    python halo_cap_what_if.py <tree> <dev checkout> <out.json>
"""
import json
import os
import sys
from pathlib import Path
from types import MappingProxyType

os.environ.setdefault("FONT_SIZE", "80")
sys.path.insert(0, sys.argv[1])
from src import brand_system
from src.multi_format import MultiFormatGenerator

dev = Path(sys.argv[2])
want = {("run_nazca_lines_runway_paradox_1793247464", "carousel", "carousel_slide_04.jpg"),
        ("run_nazca_lines_runway_paradox_1793247464", "carousel", "carousel_slide_06.jpg"),
        ("run_nazca_lines_runway_paradox_1793247464", "carousel_fb", "carousel_slide_05.jpg"),
        ("run_nazca_lines_runway_paradox_1793251266", "carousel", "carousel_slide_04.jpg"),
        ("run_nazca_lines_runway_paradox_1793251266", "carousel_fb", "carousel_slide_05.jpg")}
base_gates = dict(brand_system.GATES)
out = {}
for run, sub, name in sorted(want):
    rec = json.loads((dev / "output" / run / sub / "still_layout.json").read_text(encoding="utf-8"))[name]
    r = rec["recipe"]
    for cap in (0.16, 0.24, 0.32):
        brand_system.GATES = MappingProxyType({**base_gates, "halo.masthead_max": cap})
        _, lay = MultiFormatGenerator()._render_carousel_slide(
            r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]), int(r["total"]),
            r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")), tight=bool(rec.get("tight")),
            subject=r.get("subject") or "", heading_frame=int(r.get("heading_frame") or 2))
        m = lay.meta["masthead"]
        key = f"{run[-10:]} {sub} {name[-6:-4]} cap {cap}"
        out[key] = {k: m.get(k) for k in ("shown", "contrast", "halo", "scrim_peak", "levels_ceiling", "best_tried")}
        print(key, out[key], flush=True)
brand_system.GATES = MappingProxyType(base_gates)
Path(sys.argv[3]).write_text(json.dumps(out, indent=1), encoding="utf-8", newline="\n")
