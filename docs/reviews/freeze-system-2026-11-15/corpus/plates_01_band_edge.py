"""PLATES-01 / PLATES-02 (panel 6): no band, plate or scrim edge steps faster than the ONE limit its ground allows,
and the hook's grade-out holds the ground's hue and saturation.

Over every photograph of every dev run's topic, each band primitive is drawn as its surface draws it (canonical
geometry, the worst case's strength) and read back from the pixels (plates_common.band_edges):
- carousel ground band (multi_format.scrim: the editorial ground, INK2 at full strength from y 980 of 1440 x 1800)
- carousel cover band (multi_format.scrim, target 0.26 from y 900)
- Story band (multi_format.scrim, floating: y 700-1100 of 1080 x 1920)
- myth 9:16 panel (design_cards._myth_ground: the toned panel over the blurred photograph)
- hook plate: its top and foot, and its ease right of the block (design_cards._plate_layer at 0.96, y 715-1055)
- hook wash (design_cards._wash_layer at 0.70)
- Reel cover type scrim (cover_design._scrim)
Table: topic, runs, photo, edge, slope (luma / px at 1080), limit, pass. A row passes at the rule's own limit (review
of the c5 fix, D6: no read-back allowance). Lander ruling 2026-11-26 (D1): no ramp runs past its designed reach
(band_edge.reach_cap); an edge over its limit is RECORDED, not passed, when the tree says the reach held it: a hook
plate or myth panel edge kept crisp (the tree's record; a crisp edge has no ramp to read back, so it is listed as such,
never skipped: D7), or a soft band whose ramp is as long as its reach cap. A myth edge neither read back nor recorded
crisp is a violation ("not measured").

PLATES-02: the hook wash and plate as the hook composes them (band_edge.hold_hue where the tree has it) on the same
photographs: hue shift and saturation kept along the grade-out to the ledger's bound (plates_common.hue_sat from
0.05 of full until the composite is under 20 luma, pixels whose 8-bit chroma can hold a hue: >= 12; review D4).
Violation: 95th percentile hue shift over 5 degrees, or 5th percentile saturation under 0.6 of the ground's.
"""
from __future__ import annotations

import plates_common as pc

a, tree, dev = pc.setup(__doc__)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src import band_edge, brand, cover_design, multi_format  # noqa: E402
from src import design_cards as dc  # noqa: E402
from src.photo_grade import cover  # noqa: E402

HUE_MAX, SAT_MIN = 5.0, 0.6
rows, bad, hues, hue_bad = [], [], [], []
caught: dict = {}
_po = dc._photo_outside


def _spy(photo, geo, y1, top=None, panel=None, foot0=None):
    r = _po(photo, geo, y1, top, panel, foot0)
    if r and panel is not None:
        caught["myth"] = (r[0], panel, r[1], (r[3] or {}) if len(r) > 3 else {})
    return r


dc._photo_outside = _spy


def cap(designed, width):
    """The tree's reach cap for a band designed `designed` px long (None: a tree before the ruling records none)."""
    rc = getattr(band_edge, "reach_cap", None)
    return rc(designed, width) if rc else None


def capped(cap):
    """A soft band's rows: over the limit but as long as the reach cap lets it run: recorded by the ruling."""
    def mark(found):
        for e in found or []:
            if not e["ok"] and cap and e.get("rows") and e["rows"][1] - e["rows"][0] >= cap - 8:
                e.update(ok=True, recorded="ramp at its designed reach (lander ruling 2026-11-26)")
        return found
    return mark


def add(topic, n, photo, edge, found, crisp=0):
    found = list(found or [])
    for _ in range(crisp):
        found.append({"slope": 0.0, "limit": None, "ok": True, "rows": None,
                      "recorded": "crisp panel edge past the reach (band_edge.reach, ruling 2026-11-26)"})
    for e in found or [{"slope": 0.0, "limit": None, "ok": True, "rows": None}]:
        row = {"topic": topic, "runs": n, "photo": photo.name, "edge": edge, **e}
        rows.append(row)
        if not e["ok"]:
            bad.append(row)
        limit = "-" if e["limit"] is None else format(e["limit"], ".2f")
        print(f"{topic[:28]:28} {n:2d} {photo.name:16} {edge:22} {e['slope']:5.2f} {limit:>5} "
              f"{'pass' if e['ok'] else 'FAIL'} {e.get('recorded', '')}", flush=True)


def grade(topic, n, photo, name, ground, layer, full):
    held = band_edge.hold_hue(ground, layer, full) if hasattr(band_edge, "hold_hue") else layer
    alpha = np.asarray(layer.getchannel("A"), np.float32) / 255.0
    got = pc.hue_sat(ground, Image.alpha_composite(ground.convert("RGBA"), held).convert("RGB"), alpha, full,
                     low=0.05, chroma=12)
    if got is None:
        return
    ok = got[1] <= HUE_MAX and got[2] >= SAT_MIN
    row = {"topic": topic, "runs": n, "photo": photo.name, "grade": name, "hue_median": got[0], "hue_p95": got[1],
           "saturation_p5": got[2], "px": got[3], "ok": ok}
    hues.append(row)
    if not ok:
        hue_bad.append(row)
    print(f"{topic[:28]:28} {n:2d} {photo.name:16} {name:22} hue {got[0]:4.1f} / {got[1]:4.1f} deg  "
          f"saturation kept {got[2]:.2f}  {'pass' if ok else 'FAIL'}", flush=True)


for topic, n, photo in pc.corpus_photos(dev, a.per_topic):
    car = cover(photo, (1440, 1800))
    out = multi_format.scrim(car, 980, 1800, feather=240, target=0.0, cap=1.0, floor=1.0, colour=brand.INK2)
    add(topic, n, photo, "carousel ground band", capped(cap(240, 1440))(pc.band_edges(car, out, brand.INK2, False)))
    out = multi_format.scrim(car, 900, 1800, feather=400, target=0.26)
    add(topic, n, photo, "carousel cover band", capped(cap(400, 1440))(pc.band_edges(car, out, brand.INK, False)))
    tall = cover(photo, (1080, 1920))
    out = multi_format.scrim(tall, 700, 1100, feather=320, target=0.30)
    add(topic, n, photo, "story band", capped(cap(320, 1080))(pc.band_edges(tall, out, brand.INK, True)))
    geo = dc._Geo(1080, 1920)
    caught.clear()
    dc._myth_ground(photo, geo, 0.0, 1.0, dc.MYTH_PANEL[0], dc.MYTH_PANEL[1])
    if "myth" in caught:
        outside, panel, mask, edges = caught["myth"]
        found = pc.band_edges(outside, Image.composite(outside, panel, mask), panel, True)
        crisp = len(edges.get("crisp") or [])
        lo = float(np.asarray(outside.convert("L"), np.float32).mean())
        missing = 2 - crisp - len(found)
        if missing > 0 and lo > 60:   # a bright photograph whose edge neither read back nor is recorded crisp (D7)
            found = list(found) + [{"slope": 0.0, "limit": None, "ok": False, "rows": None,
                                    "recorded": "NOT MEASURED"}] * missing
        add(topic, n, photo, "myth 9:16 panel", found, crisp)
    y0, y1, st, right = 715, 1055, 0.96, 901
    if hasattr(dc, "_plate_feathers"):
        feathers, side = dc._plate_feathers(tall, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
        layer = dc._plate_layer(tall.size, y0, y1, st, feathers, right, side)
    else:
        layer = dc._plate_layer(tall.size, y0, y1, st, dc.HOOK_PLATE_FEATHER, right)
    comp = Image.alpha_composite(tall.convert("RGBA"), layer).convert("RGB")
    left = (0, 0, right, 1920)                       # the plate's top and foot, read left of its ease
    add(topic, n, photo, "hook plate top/foot",
        pc.band_edges(tall.crop(left), comp.crop(left), brand.INK, True, 1080),
        sum(1 for v in feathers if not v) if hasattr(dc, "_plate_feathers") else 0)
    band = (0, y0, 1080, y1)                         # its ease right of the block, read across the plate's rows
    add(topic, n, photo, "hook plate side",
        capped(cap(dc.HOOK_PLATE_SIDE, 1080))(pc.band_edges(tall.crop(band), comp.crop(band), brand.INK, True, 1080,
                                                           transpose=True)))
    if hasattr(dc, "_wash_feathers"):
        wash_layer = dc._wash_layer(tall.size, 736, 1040, 0.70, right, dc._wash_feathers(tall, 736, 1040, 0.70))
    else:
        wash_layer = dc._wash_layer(tall.size, 736, 1040, 0.70, right)
    wash = Image.alpha_composite(tall.convert("RGBA"), wash_layer).convert("RGB")
    add(topic, n, photo, "hook wash top/foot",
        capped(cap(min(dc.HOOK_WASH_FEATHER), 1080))(pc.band_edges(tall.crop(left), wash.crop(left), brand.INK, True,
                                                                   1080)))
    box = (137, 1100, 824, 1500)
    sc = cover_design._scrim(tall, box, cover_design.MONUMENT_SCRIM, cover_design.MONUMENT_SCRIM_FEATHER)
    mid = (box[0] + 100, 0, box[2] - 100, 1920)
    add(topic, n, photo, "reel cover scrim",
        capped(cap(cover_design.MONUMENT_SCRIM_FEATHER, 1080))(
            pc.band_edges(tall.crop(mid), sc.convert("RGB").crop(mid), brand.INK, True, 1080)))
    grade(topic, n, photo, "hook wash grade-out", tall, wash_layer, 0.70)
    grade(topic, n, photo, "hook plate grade-out", tall, layer, st)

by: dict = {}
for r in rows:
    s = by.setdefault(r["edge"], {"edges": 0, "fail": 0, "max": 0.0})
    s["edges"] += 1
    s["fail"] += 0 if r["ok"] else 1
    s["max"] = max(s["max"], r["slope"])
for name in ("hook wash grade-out", "hook plate grade-out"):
    sel = [r for r in hues if r["grade"] == name]
    if sel:
        by[name] = {"grounds": len(sel), "fail": sum(not r["ok"] for r in sel),
                    "hue_p95_max": max(r["hue_p95"] for r in sel),
                    "saturation_p5_min": min(r["saturation_p5"] for r in sel)}
pc.finish(a, "PLATES-01 band edge / PLATES-02 grade-out", rows + hues, bad + hue_bad, by)
