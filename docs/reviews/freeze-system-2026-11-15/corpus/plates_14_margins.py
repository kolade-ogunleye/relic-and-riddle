"""PLATES-14 (panel 6): every text and plate box keeps the margin token (safe_zones.TEXT_INSET, 24 px at 1080 wide,
scaled to the canvas) to its frame's safe box on every side.

Over every frame plates_draw.py drew (Instagram and Facebook carousel slides, both Story frames, the Reel cover):
each recorded element's box, and its plate's box when it has one, against the frame's SAFE box (safe_zones, read
from the repository this script is in): the Story's safe_box("story"); the Reel cover's cover_box (the centre square
both profile-grid crops keep); a carousel slide's 3:4 profile-grid crop (the slide's centre, its height x 3 / 4 wide:
45 px off each side of a 1440 slide; safe_zones' module note). A box outside the safe box has a negative margin.
The masthead lockup and the swipe strip are not text boxes (the masthead has its own ruled position, PLATES-05; the
strip runs to the edge by design, PLATES-13).
Table: run, surface, frame, element, the side and its margin, pass/fail.
"""
from __future__ import annotations

import sys
from pathlib import Path

import plates_records as pr

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from src import safe_zones  # noqa: E402

INSET = safe_zones.TEXT_INSET


def safe(sub: str, size) -> tuple[int, int, int, int]:
    w, h = size
    if sub == "stories":
        return safe_zones.safe_box("story", w, h)
    if sub == "":
        return safe_zones.cover_box(w, h)
    side = round((w - h * 3 / 4) / 2)
    return side, 0, w - side, h


SKIP = ("header_brand",)
a = pr.setup(__doc__)
rows, bad = [], []

for run in pr.runs(a.drawn):
    for sub in (*pr.DECKS, "stories", ""):
        for name, entry in pr.frames(run, sub):
            size = entry.get("size") or [1080, 1920]
            zone = safe(sub, size)
            need = round(INSET * size[0] / 1080)
            for el in entry.get("elements") or []:
                if el["name"] in SKIP or el.get("masthead"):
                    continue
                boxes = [("text", el["box"])]
                if isinstance(el.get("plate"), dict) and el["plate"].get("box"):
                    boxes.append(("plate", el["plate"]["box"]))
                for what, b in boxes:
                    sides = {"left": b[0] - zone[0], "top": b[1] - zone[1], "right": zone[2] - b[2],
                             "bottom": zone[3] - b[3]}
                    side = min(sides, key=sides.get)
                    m = sides[side]
                    ok = m >= need
                    row = {"run": run.name[4:], "surface": sub or "reel_cover", "frame": name, "element": el["name"],
                           "box": what, "side": side, "margin": m, "need": need, "ok": ok}
                    rows.append(row)
                    if not ok:
                        bad.append(row)
                    if not ok or what == "plate":
                        print(f"{run.name[4:40]:36} {(sub or 'reel_cover')[:11]:11} {name[-10:-4]:6} {el['name'][:14]:14} "
                              f"{what:5} {side:6} {m:5} / {need} {'pass' if ok else 'FAIL'}", flush=True)

by = {}
for r in rows:
    s = by.setdefault(r["surface"], [0, 0, None])
    s[0] += 1
    s[1] += not r["ok"]
    s[2] = r["margin"] if s[2] is None else min(s[2], r["margin"])
summary = {"boxes": len(rows), f"under the {INSET} px token (scaled)": len(bad),
           "per surface (boxes, under, smallest margin)": by}
pr.finish(a, "PLATES-14 text and plate margins inside the safe box", rows, bad, summary)
