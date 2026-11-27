"""PLATES-01, its cost (lander ruling 2026-11-26 on the review of the c5 fix, D1): a plate or panel darkens only the
area its type needs plus its ramp, the ramp never past its designed form (band_edge.designed_feather), and the tree under
test never darkens more of the photograph than the base did.

Over every photograph of every dev run's topic (plates_common.corpus_photos), through the tree's own layer calls
(no frame is rendered), on 1080 x 1920:
- hook plate: design_cards._plate_layer at the plate's corpus geometry (rows 715-1055, strength 0.96, full to x 901),
  its feathers from the tree's _plate_feathers (a base tree: the designed HOOK_PLATE_FEATHER);
- myth 9:16 panel: design_cards._myth_ground's photograph-outside mask over the panel (MYTH_PANEL), as composed.
Per photograph and surface: the share of the frame OUTSIDE the type's own box (the hook plate's full-strength box,
x 0-901 x rows 715-1055; the myth panel's rows) whose luma falls by more than 20 against the photograph as shown
there without the plate or panel (the ground: the cover crop; the myth's graded, blurred outside), the panel's ramps
(px) and whether an edge was kept crisp past the reach (the tree's record).
--against <base json>: each photograph's share may not exceed the base's by more than SLACK (0.5 points: 8-bit
alpha and the feathers' rounding); exit 1 on any that does.
"""
from __future__ import annotations

import json

import plates_common as pc

a, tree, dev = pc.setup(__doc__, lambda ap: ap.add_argument("--against", type=__import__("pathlib").Path))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src import design_cards as dc  # noqa: E402
from src.photo_grade import cover  # noqa: E402

SLACK = 0.5
DARK = 20.0
base = {}
if a.against:
    base = {(r["photo_key"], r["surface"]): r for r in json.loads(a.against.read_text(encoding="utf-8"))["rows"]}
rows, bad = [], []
caught: dict = {}
_po = dc._photo_outside


def _spy(photo, geo, y1, top=None, panel=None, foot0=None):
    r = _po(photo, geo, y1, top, panel, foot0)
    if r and panel is not None:
        caught["myth"] = (r[0], panel, r[1], r[2], r[3] if len(r) > 3 else None)
    return r


dc._photo_outside = _spy


def share(before: Image.Image, after: Image.Image, keep: np.ndarray) -> float:
    d = pc.luma(before) - pc.luma(after)
    return round(100.0 * float(((d > DARK) & ~keep).mean()), 2)


def add(topic, photo, surface, value, ramps, crisp):
    key = f"{topic}/{photo.name}"
    row = {"photo_key": key, "topic": topic, "photo": photo.name, "surface": surface, "share": value,
           "ramps": ramps, "crisp": crisp}
    b = base.get((key, surface))
    if b is not None:
        row["base"] = b["share"]
        row["ok"] = value <= b["share"] + SLACK
    else:
        row["ok"] = True
    rows.append(row)
    if not row["ok"]:
        bad.append(row)
    was = "" if b is None else f"  base {b['share']:6.2f} %"
    print(f"{topic[:28]:28} {photo.name:18} {surface:11} dark>20 {value:6.2f} %{was}"
          f"  ramps {ramps} crisp {crisp} {'pass' if row['ok'] else 'FAIL'}", flush=True)


H, W = 1920, 1080
y0, y1, st, right = 715, 1055, 0.96, 901
hook_keep = np.zeros((H, W), bool)
hook_keep[y0:y1 + 1, :right] = True
for topic, n, photo in pc.corpus_photos(dev, a.per_topic):
    tall = cover(photo, (W, H)).convert("RGB")
    if hasattr(dc, "_plate_feathers"):
        feathers, side = dc._plate_feathers(tall, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
        layer = dc._plate_layer(tall.size, y0, y1, st, feathers, right, side)
    else:
        feathers, side = (dc.HOOK_PLATE_FEATHER, dc.HOOK_PLATE_FEATHER), dc.HOOK_PLATE_SIDE
        layer = dc._plate_layer(tall.size, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
    comp = Image.alpha_composite(tall.convert("RGBA"), layer).convert("RGB")
    add(topic, photo, "hook plate", share(tall, comp, hook_keep), [int(v) for v in feathers] + [int(side)],
        [n for n, v in zip(("top", "foot"), feathers) if not v])
    geo = dc._Geo(W, H)
    caught.clear()
    dc._myth_ground(photo, geo, 0.0, 1.0, dc.MYTH_PANEL[0], dc.MYTH_PANEL[1])
    if "myth" in caught:
        outside, panel, mask, ramps, edges = caught["myth"]
        keep = np.zeros((H, W), bool)
        keep[round(geo.y(dc.MYTH_PANEL[0])):round(geo.y(dc.MYTH_PANEL[1])) + 1, :] = True
        add(topic, photo, "myth panel", share(outside, Image.composite(outside, panel, mask), keep),
            [round(v) for v in ramps], (edges or {}).get("crisp", []))

by = {}
for r in rows:
    s = by.setdefault(r["surface"], [])
    s.append(r["share"])
summary = {f"{k}: photographs / share dark>20 max / p95 / median": (len(v), max(v), round(float(np.percentile(v, 95)), 2),
                                                                      round(float(np.median(v)), 2))
           for k, v in by.items()}
summary["edges kept crisp past the reach"] = sum(len(r["crisp"]) for r in rows)
if base:
    summary["photographs darker than the base by more than the slack"] = len(bad)
    summary["largest increase (points)"] = max((r["share"] - r.get("base", r["share"]) for r in rows), default=0)
pc.finish(a, "PLATES-01 cost: frame share darkened outside the type", rows, bad, summary)
