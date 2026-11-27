"""PLATES review 2 (lander rulings N1, N2, 2026-11-26): against the BASE tree's own stills, per stem, every still of
every surface plates_draw.py drew (Instagram and Facebook carousel slides, both Story frames, the Reel cover):
1. darkening (N1): the share of the frame where the tree under test is more than DARK luma darker than the base,
   outside the type (each text element's box, base and tip, grown by PAD px: a line that moved is not a darker
   photograph). The ceiling is the base's own share of that stem: 0. A still darker on more than SLACK % of its
   frame is a violation.
2. contrast under the type (N2): each text element's recorded ratio (still_layout "contrast_platform", the platform
   re-encode, else "contrast") against the base's same element on the same still: lower by more than DROP is a
   violation, and so is an element the base drew that the tree does not (the reel pointer line, removed by the
   ruling of 2026-11-26, is listed apart).

    python plates_vs_base.py <base drawn>/<brand> <tip drawn>/<brand> [--json out.json]

Seconds over the dev corpus (JPEG reads and records only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import plates_records as pr

DARK, SLACK, PAD, DROP = 20.0, 0.5, 6, 0.05
RULED_OUT = ("reel_pointer",)
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("base", type=Path)
ap.add_argument("tip", type=Path)
ap.add_argument("--json", type=Path)
a = ap.parse_args()
L = np.array([0.2126, 0.7152, 0.0722], np.float32)
dark_rows, con_rows, bad = [], [], []
ruled = 0

for run in pr.runs(a.tip):
    brun = a.base / run.name
    if not (brun / "draw.json").is_file():
        continue
    for sub in (*pr.DECKS, "stories", ""):
        base = dict(pr.frames(brun, sub))
        for name, et in pr.frames(run, sub):
            eb = base.get(name)
            ft, fb = (run / sub / name), (brun / sub / name)
            if not eb or not ft.is_file() or not fb.is_file():
                continue
            lt = np.asarray(Image.open(ft).convert("RGB"), np.float32) @ L
            lb = np.asarray(Image.open(fb).convert("RGB"), np.float32) @ L
            if lt.shape != lb.shape:
                continue
            keep = np.ones(lt.shape, bool)
            for e in (et.get("elements") or []) + (eb.get("elements") or []):
                x0, y0, x1, y1 = e["box"]
                keep[max(0, y0 - PAD):y1 + PAD, max(0, x0 - PAD):x1 + PAD] = False
            share = round(100.0 * float(((lb - lt > DARK) & keep).mean()), 2)
            ok = share <= SLACK
            mt = (et.get("meta") or {}).get("masthead") or {}
            mb = (eb.get("meta") or {}).get("masthead") or {}
            # the masthead's longer top-scrim reach (PLATES-05, first builder; ruled "no FURTHER lengthening") or a
            # masthead the base withheld and the tree draws: named, still counted
            cause = ("masthead margin reach (PLATES-05)" if mt.get("margin_step") else
                     "masthead newly drawn" if mt.get("shown") and not mb.get("shown") else "band")
            row = {"run": run.name[4:], "surface": sub or "reel_cover", "still": name, "darker_share": share, "ok": ok,
                   "cause": cause}
            dark_rows.append(row)
            if not ok:
                bad.append(row)
                print(f"DARKER {run.name[4:40]:36} {(sub or 'reel_cover')[:11]:11} {name[-12:-4]:8} {share:6.2f} % "
                      f"{cause}", flush=True)
            cb = {e["name"]: e for e in eb.get("elements") or []}
            ct = {e["name"]: e for e in et.get("elements") or []}
            for k, e in cb.items():
                v_b = e.get("contrast_platform", e.get("contrast"))
                if v_b is None:
                    continue
                if k not in ct:
                    if k in RULED_OUT:
                        ruled += 1
                        continue
                    r = {"run": run.name[4:], "surface": sub or "reel_cover", "still": name, "element": k,
                         "base": v_b, "tip": None, "ok": False}
                else:
                    v_t = ct[k].get("contrast_platform", ct[k].get("contrast"))
                    r = {"run": run.name[4:], "surface": sub or "reel_cover", "still": name, "element": k,
                         "base": round(float(v_b), 2), "tip": None if v_t is None else round(float(v_t), 2)}
                    r["ok"] = v_t is not None and float(v_t) >= float(v_b) - DROP
                con_rows.append(r)
                if not r["ok"]:
                    bad.append(r)
                    print(f"LOWER  {run.name[4:40]:36} {(sub or 'reel_cover')[:11]:11} {name[-12:-4]:8} {k:14} "
                          f"{r['base']} -> {r['tip']}", flush=True)

by = {}
for r in dark_rows:
    s = by.setdefault(r["surface"], {"stills": 0, "darker": 0, "max share": 0.0, "darker by cause": {}})
    s["stills"] += 1
    s["darker"] += not r["ok"]
    if not r["ok"]:
        s["darker by cause"][r["cause"]] = s["darker by cause"].get(r["cause"], 0) + 1
    s["max share"] = max(s["max share"], r["darker_share"])
cby = {}
for r in con_rows:
    s = cby.setdefault(r["surface"], {"elements": 0, "lower": 0, "largest drop": 0.0})
    s["elements"] += 1
    s["lower"] += not r["ok"]
    if r["tip"] is not None:
        s["largest drop"] = round(max(s["largest drop"], r["base"] - r["tip"]), 2)
summary = {"darkening (N1), per surface": by, "contrast vs base (N2), per surface": cby,
           "reel pointer lines removed by ruling": ruled}
print(f"\nPLATES vs base (N1 darkening, N2 contrast): {len(dark_rows)} stills, {len(con_rows)} elements, "
      f"{len(bad)} violations")
for k, v in summary.items():
    print(f"  {k}: {v}")
if a.json:
    a.json.write_text(json.dumps({"summary": summary, "violations": bad, "dark": dark_rows, "contrast": con_rows},
                                 indent=1), encoding="utf-8", newline="\n")
sys.exit(1 if bad else 0)
