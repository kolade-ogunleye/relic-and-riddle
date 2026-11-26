"""PLATES-05 / PLATES-07 (panel 6): the masthead's contrast has margin on the worst real ground, and every masthead
decision (drawn, variant, withheld, withheld by rule) is on record for every frame of every deck.

Two parts, both through the tree's own masthead (src/masthead.py), nothing re-implemented:
1. DECKS: every carousel and Facebook slide of every dev run that kept its recipe (carousel*/still_layout.json),
   re-drawn by MultiFormatGenerator._render_carousel_slide as the run drew it (a still each). Per frame: the
   masthead record the layout carries (lay.meta["masthead"]); a frame with no record, or a withheld masthead that is
   on no omission ledger (refusal.collecting) and is not withheld by rule, is a violation (PLATES-07).
2. GROUNDS: every photograph of every dev run's topic as a full-bleed carousel ground (cover crop 1440 x 1800), the
   masthead planned at the carousel's own position, size and reach (masthead.plan): the distribution over every
   topic, not only the runs that kept a recipe.
Table: run or topic, frame, shown, variant, worst-glyph contrast, halo, exception, margin step. Summary: the
distribution (min, 5th percentile, median), the count under MARGIN (6.0), the count at the gate floor (< 5.7), the
halo exceptions, the withheld, the frames without a record. A drawn masthead under MARGIN is a violation unless its
record says no ruled step reaches it (`margin_short`, with the best ratio any step reached: the recorded ceiling).
--keep <dir>: also save each deck frame's ground before the masthead (PNG) and the call's arguments (JSON).
"""
from __future__ import annotations

import json
from pathlib import Path

import plates_common as pc

a, tree, dev = pc.setup(__doc__, lambda ap: (ap.add_argument("--keep", type=Path),
                                             ap.add_argument("--no-decks", action="store_true"),
                                             ap.add_argument("--no-grounds", action="store_true")))

import numpy as np  # noqa: E402

from src import masthead, refusal  # noqa: E402
from src.brand_system import type_px  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402
from src.photo_grade import cover  # noqa: E402
from src.typography import cap_height  # noqa: E402

MARGIN, FLOOR = 6.0, 5.7
rows, bad = [], []
calls: list = []
_draw = masthead.draw


def _spy(im, x0, baseline, size, reach=None, target=None, max_width=None):
    calls.append((im.copy(), {"x0": x0, "baseline": baseline, "size": size, "reach": reach, "max_width": max_width}))
    return _draw(im, x0, baseline, size, reach, target, max_width)


masthead.draw = _spy


def line(where, frame, rec, note=""):
    rec = rec or {}
    c = rec.get("contrast")
    shown = rec.get("shown")
    by_rule = bool(rec.get("fb_interior") or rec.get("by_rule"))
    row = {"where": where, "frame": frame, "shown": shown, "variant": rec.get("variant"), "contrast": c,
           "halo": rec.get("halo"), "halo_exception": bool(rec.get("halo_exception")), "by_rule": by_rule,
           "margin_step": rec.get("margin_step"), "margin_short": rec.get("margin_short"),
           "best_tried": rec.get("best_tried"), "note": note}
    ok = True
    if not rec:
        ok, row["note"] = False, "NO MASTHEAD RECORD"
    elif shown and c is not None and c < MARGIN and not rec.get("margin_short"):
        ok = False
        row["note"] = (note + " under the margin, no recorded ceiling").strip()
    elif note.startswith("UNRECORDED"):
        ok = False
    row["ok"] = ok
    rows.append(row)
    if not ok:
        bad.append(row)
    print(f"{where[:44]:44} {frame[:22]:22} {'drawn' if shown else 'by rule' if by_rule else 'WITHHELD' if rec else '-':8} "
          f"{str(rec.get('variant') or '-'):5} {'-' if c is None else format(c, '.2f'):>5} "
          f"halo {rec.get('halo') if rec.get('halo') is not None else '-'} "
          f"{'EXC ' if rec.get('halo_exception') else ''}{rec.get('margin_step') or ''} "
          f"{'pass' if ok else 'FAIL'} {row['note']}", flush=True)


if not a.no_decks:
    gen = MultiFormatGenerator()
    for run in pc.runs(dev):
        for sub in ("carousel", "carousel_fb"):
            f = run / sub / "still_layout.json"
            if not f.is_file():
                continue
            for name, rec in sorted(json.loads(f.read_text(encoding="utf-8")).items()):
                r = rec.get("recipe") or {}
                if not r:
                    continue
                calls.clear()
                try:
                    with refusal.collecting() as ledger:
                        im, lay = gen._render_carousel_slide(
                            r["archetype"], Path(r["photo"]), Path(r["photo_next"]), r["card"], int(r["index"]),
                            int(r["total"]), r.get("poll") or {}, r.get("share") or "", bool(r.get("facebook")),
                            tight=bool(rec.get("tight")), subject=r.get("subject") or "",
                            heading_frame=int(r.get("heading_frame") or 2))
                except refusal.LayoutError as e:
                    print(f"{run.name[4:]:44} {sub}/{name}: refused ({str(e)[:90]})", flush=True)
                    continue
                m = (getattr(lay, "meta", None) or {}).get("masthead")
                note = ""
                if m and not m.get("shown") and not (m.get("fb_interior") or m.get("by_rule")):
                    on_ledger = any("masthead" in str(x.get("omitted", "")).lower() for x in ledger)
                    note = "withheld, on the omission ledger" if on_ledger else "UNRECORDED: withheld, on no ledger"
                line(run.name[4:], f"{sub}/{Path(name).stem[-8:]}", m, note)
                if a.keep and calls:
                    a.keep.mkdir(parents=True, exist_ok=True)
                    stem = f"{run.name[4:]}__{sub}__{Path(name).stem}"
                    calls[-1][0].convert("RGB").save(a.keep / f"{stem}.png")
                    (a.keep / f"{stem}.json").write_text(json.dumps({**calls[-1][1], "record": m}, default=str),
                                                         encoding="utf-8", newline="\n")

if not a.no_grounds:
    cw, chh = 1440, 1800
    size = type_px("masthead", "carousel") * cw / 1440
    font = masthead.font_for(size)
    k = cw / 1080
    x0 = round(80 * k)
    base = round(MultiFormatGenerator.HEADER_Y * k) + cap_height(font)
    for topic, n, photo in pc.corpus_photos(dev, a.per_topic):
        ground = cover(photo, (cw, chh))
        _, _, rec = masthead.plan(ground, x0, base, size, reach=base + round(300 * k),
                                  max_width=cw - round(280 * k) - x0)
        line(f"ground {topic} ({n} runs)", photo.name, rec)

drawn = [r["contrast"] for r in rows if r["shown"] and r["contrast"] is not None]
summary = {
    "frames": len(rows),
    "drawn": len(drawn),
    "contrast min / p5 / median": (f"{min(drawn):.2f} / {np.percentile(drawn, 5):.2f} / {np.median(drawn):.2f}"
                                   if drawn else "-"),
    f"drawn under the margin {MARGIN}": sum(c < MARGIN for c in drawn),
    f"drawn at the gate floor (< {FLOOR})": sum(c < FLOOR for c in drawn),
    "of those, with a recorded ceiling (margin_short)": sum(1 for r in rows if r["shown"] and r["margin_short"]),
    "halo exceptions": sum(r["halo_exception"] and bool(r["shown"]) for r in rows),
    "withheld (gate)": sum(1 for r in rows if r["shown"] is False and not r["by_rule"]),
    "withheld by rule": sum(1 for r in rows if r["by_rule"]),
    "withheld and on no ledger": sum(1 for r in rows if str(r["note"]).startswith("UNRECORDED")),
    "frames without a record": sum(1 for r in rows if r["note"] == "NO MASTHEAD RECORD"),
}
pc.finish(a, "PLATES-05 masthead margin / PLATES-07 masthead records", rows, bad, summary)
