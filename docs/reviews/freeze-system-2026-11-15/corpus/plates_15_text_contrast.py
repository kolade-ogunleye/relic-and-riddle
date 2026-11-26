"""PLATES-15 (panel 6): text on a photograph's band reads at the 7:1 target (tok contrast.high) on its own ground,
or its record says the band could not take it there (the cap held it short: the recorded ceiling); never under the
4.8 placement target.

Over every frame plates_draw.py drew (Instagram and Facebook carousel slides, both Story frames, the Reel cover):
each text element's measured ratio as composed (the still_layout record's "contrast": its worst glyph on the worst
5 % of its own surround). Kickers and the masthead are labels with their own ruled gates (plan_label 5.5,
masthead.plan 5.5 / 6.5: PLATES-04 and PLATES-05) and are listed apart, not judged here.
A row passes at >= 7.0; under it, it passes only when its frame's text band records `short` (the band's cap reached
before the target: frame meta "text_band", the Story's ground "text_band", the photo credit's own) and the element
still reads at >= 4.8. A base tree records no text band: every element under 7.0 there is a violation.
"""
from __future__ import annotations

import numpy as np

import plates_records as pr

TARGET, FLOOR = 7.0, 4.8
LABELS = ("kicker", "header_brand", "eyebrow")
a = pr.setup(__doc__)
rows, bad = [], []


def band_of(entry: dict, el: dict, story: dict) -> dict | None:
    meta = entry.get("meta") or {}
    if el["name"] == "photo_credit":
        return (meta.get("photo_credit") or (story.get("photo_credit") or {})).get("text_band")
    return meta.get("text_band") or (meta.get("ground") or {}).get("text_band") or \
        ((story.get("ground") or {}).get("text_band"))


for run in pr.runs(a.drawn):
    stories = pr.story_layout(run)
    for sub in (*pr.DECKS, "stories", ""):
        for name, entry in pr.frames(run, sub):
            story = stories.get(name, {}) if sub == "stories" else {}
            for el in entry.get("elements") or []:
                c = el.get("contrast")
                if c is None or el.get("masthead"):
                    continue
                label = el["name"] in LABELS or el["name"].startswith("kicker")
                band = band_of(entry, el, story) or {}
                ok = label or c >= TARGET or (bool(band.get("short")) and c >= FLOOR)
                why = ("label (own gate)" if label else "target" if c >= TARGET else
                       "recorded ceiling" if ok else "UNDER THE TARGET, no recorded ceiling" if c >= FLOOR else
                       "UNDER THE 4.8 FLOOR")
                row = {"run": run.name[4:], "surface": sub or "reel_cover", "frame": name, "archetype": pr.archetype(entry),
                       "element": el["name"], "contrast": round(float(c), 2), "label": label,
                       "band_need": band.get("need"), "band_short": band.get("short"), "ok": ok, "why": why}
                rows.append(row)
                if not ok:
                    bad.append(row)
                print(f"{run.name[4:40]:36} {(sub or 'reel_cover')[:11]:11} {name[-10:-4]:6} {row['archetype'][:10]:10} "
                      f"{el['name'][:13]:13} {c:6.2f} {'pass' if ok else 'FAIL'} {why}", flush=True)

text = [r for r in rows if not r["label"]]
vals = [r["contrast"] for r in text]
by_surface = {}
for r in text:
    s = by_surface.setdefault(r["surface"], [0, 0, 0])
    s[0] += 1
    s[1] += r["contrast"] < TARGET
    s[2] += r["contrast"] < FLOOR
summary = {
    "text elements judged": len(text),
    "labels listed apart (kicker / masthead)": len(rows) - len(text),
    "contrast min / p5 / median": (f"{min(vals):.2f} / {np.percentile(vals, 5):.2f} / {np.median(vals):.2f}"
                                   if vals else "-"),
    f"under {TARGET}": sum(v < TARGET for v in vals),
    f"under {TARGET} with a recorded ceiling": sum(1 for r in text if r["contrast"] < TARGET and r["ok"]),
    f"under the {FLOOR} floor": sum(v < FLOOR for v in vals),
    "per surface (elements, under 7.0, under 4.8)": by_surface,
}
pr.finish(a, "PLATES-15 text on a photograph at 7:1", rows, bad, summary)
