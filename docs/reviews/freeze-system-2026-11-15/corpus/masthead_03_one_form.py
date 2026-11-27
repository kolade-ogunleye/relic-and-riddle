"""MASTHEAD-03 (freeze cycle 6 round 2, lander ruling 2026-11-27): ONE masthead form and ONE size per post. Every
slide of a run's Instagram and Facebook decks that draws a masthead draws it in the same form (line / stacked) at the
same size; a slide that fell back to the one-line form because the stacked form reached no gate on its ground is
listed (recorded `form_fallback`), never silent.

    python masthead_03_one_form.py <decks>/<brand> [--json out.json]

A violation: a post whose slides differ in form or size without a recorded fallback.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decks", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    rows, bad = [], []
    for run in sorted(p for p in a.decks.iterdir() if (p / "draw.json").is_file()):
        forms, sizes, fallbacks = set(), set(), []
        for sub in ("carousel", "carousel_fb"):
            f = run / sub / "still_layout.json"
            if not f.is_file():
                continue
            for name, e in sorted(json.loads(f.read_text(encoding="utf-8")).items()):
                m = (e.get("meta") or {}).get("masthead") or {}
                if not m.get("shown"):
                    continue
                if m.get("form_fallback"):
                    fallbacks.append(f"{sub}/{name}")
                    continue
                forms.add(m.get("form", "line"))
                sizes.add(round(float(m.get("size_px") or 0), 2))
        ok = len(forms) <= 1 and len(sizes) <= 1
        row = {"run": run.name[4:], "forms": sorted(forms), "sizes": sorted(sizes), "fallbacks": fallbacks, "ok": ok}
        rows.append(row)
        if not ok:
            bad.append(row)
        print(f"{run.name[4:46]:42} {','.join(row['forms']) or '-':12} {row['sizes']} "
              f"{'fallback ' + str(fallbacks) if fallbacks else ''} {'pass' if ok else 'FAIL'}", flush=True)
    counts = {"posts": len(rows), "stacked posts": sum(r["forms"] == ["stacked"] for r in rows),
              "posts mixing forms or sizes": len(bad), "slides fallen back to one line": sum(len(r["fallbacks"])
                                                                                          for r in rows)}
    print(f"\nMASTHEAD-03 one form per post: {json.dumps(counts)}")
    if a.json:
        a.json.write_text(json.dumps({"counts": counts, "rows": rows}, indent=1), encoding="utf-8", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
