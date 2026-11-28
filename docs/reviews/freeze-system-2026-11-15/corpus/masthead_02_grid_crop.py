"""MASTHEAD-02 (freeze cycle 6, panel 7 PL1 M2): the Instagram cover is the post's profile-grid tile, and the grid
shows its centre 3:4 (x 45-1395 on the 1440 x 1800 slide). Nothing slide 1 draws is cut by that crop: every element's
box (heading, body, masthead) lies inside it, and the swipe strip's slide-1 part lies wholly OUTSIDE it (round 2,
lander ruling 2026-11-27: from the crop's edge to the slide's, so the feed shows one line across the seam and the grid
tile none of it), or is not drawn and says why. The Facebook cover draws no strip at all.

Reads what plates_draw.py left for one tree and palette:

    python masthead_02_grid_crop.py <decks>/<brand> [--json out.json]

A violation: an element outside the crop, a strip part reaching into it (the seam stub at x 1344-1440 was one on every
Instagram cover at base), or a strip on a Facebook cover.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def grid_crop(w: int, h: int) -> tuple[int, int, int, int]:
    cw, ch = min(w, round(h * 3 / 4)), min(h, round(w * 4 / 3))
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    return x0, y0, x0 + cw, y0 + ch


def inside(b, c) -> bool:
    return b[0] >= c[0] and b[1] >= c[1] and b[2] <= c[2] and b[3] <= c[3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decks", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows, bad = [], []
    for run in sorted(p for p in a.decks.iterdir() if (p / "draw.json").is_file()):
        for sub in ("carousel", "carousel_fb"):
            f = run / sub / "still_layout.json"
            if not f.is_file():
                continue
            e = json.loads(f.read_text(encoding="utf-8")).get("carousel_slide_01.jpg") or {}
            w, h = e.get("size") or [1440, 1800]
            crop = grid_crop(w, h)
            strip = (e.get("meta") or {}).get("swipe_strip") or {}
            out = [el["name"] for el in e.get("elements") or [] if not inside(el["box"], crop)]
            if strip.get("box") and strip["box"][0] < crop[2]:   # the part must lie wholly outside the crop
                out.append(f"swipe strip {strip['box']}")
            fb_strip = sub == "carousel_fb" and bool(strip.get("box"))
            ok = not (out if sub == "carousel" else fb_strip)
            why = ("strip skipped: " + strip["skipped"][:60] if strip.get("skipped") else
                   "strip drawn inside the crop" if strip.get("box") else "no strip") if ok else \
                ("CUT BY THE GRID CROP: " + ", ".join(out) if out else "FB COVER DRAWS A STRIP")
            row = {"run": run.name[4:], "deck": sub, "crop": list(crop), "outside": out, "strip": strip, "ok": ok,
                   "why": why}
            rows.append(row)
            if not ok:
                bad.append(row)
            print(f"{run.name[4:46]:42} {sub:11} {'pass' if ok else 'FAIL'} {why}", flush=True)
    ig = [r for r in rows if r["deck"] == "carousel"]
    counts = {"covers": len(rows), "IG covers": len(ig), "IG covers cut by the grid crop": sum(not r["ok"] for r in ig),
              "IG strips drawn (outside the crop)": sum(bool(r["strip"].get("box")) for r in ig),
              "IG strips skipped": sum(bool(r["strip"].get("skipped")) for r in ig), "violations": len(bad)}
    print(f"\nMASTHEAD-02 grid crop of every cover: {json.dumps(counts)}")
    if a.json:
        a.json.write_text(json.dumps({"counts": counts, "rows": rows}, indent=1), encoding="utf-8", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
