"""Cycle 0 round 3: the builder's own adversarial sweep of the masthead's glyph judgement (before hand-back).

    python sweep.py <repo root> <out.json> [--workers 4]

Grounds (1440 x 700, the carousel masthead at 53.3 px, x 107, baseline 133): bone stripes on INK of widths
1-48 px at every 4 px offset across the first letters and at 0 / 30 / 60 degrees; checkerboards 1-8 px; uniform
noise (10 seeds); a narrow-letter brand name ("I1 II 1I") under stripes 1-8 px at 0 / 10 degrees. For every case:
shown or not, the judged ratio, the best tried, and, when drawn, an INDEPENDENT per-glyph check (tests/
test_brand_system_r3.py `_independent_glyph_min`). The sweep passes when no drawn case fails the independent check
and every undrawn case records a best under the gate with its reason.
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
os.environ.setdefault("FONT_SIZE", "80")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def cases():
    out = []
    for w in (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48):
        for off in range(-8, 100, 4):
            for ang in (0, 30, 60):
                out.append(("stripe", w, off, ang, None))
    for cell in range(1, 9):
        out.append(("checker", cell, 0, 0, None))
    for seed in range(10):
        out.append(("noise", seed, 0, 0, None))
    for w in (1, 2, 3, 4, 6, 8):
        for off in range(0, 40, 2):
            for ang in (0, 10):
                out.append(("stripe", w, off, ang, "narrow"))
    return out


def run(case):
    import numpy as np
    import test_brand_system_r3 as t
    from PIL import Image

    from src import brand, masthead
    kind, a, off, ang, name = case
    ctx = brand.use(brand.DEFAULT_BRAND.derive("narrow", text={"masthead": "I1 II 1I"})) if name else t._null()
    with ctx:
        if kind == "stripe":
            g = t._stripe(a, t.NAME_X + off, ang)
        elif kind == "checker":
            yy, xx = np.indices((t.H, t.W))
            c = np.where((xx // a + yy // a) % 2, 255, 0).astype(np.uint8)
            g = Image.fromarray(np.repeat(c[..., None], 3, axis=2))
        else:
            g = Image.fromarray(np.random.default_rng(a).integers(0, 256, (t.H, t.W, 3), dtype=np.uint8))
        ground, p, rec = masthead.plan(g, t.X, t.BASE, t.SIZE, reach=t.BASE + 400)
        ind = t._independent_glyph_min(ground, p) if rec["shown"] else None
    ok = (ind >= t.GATE - 0.02) if rec["shown"] else ("reason" in rec and (rec["best_tried"] or 0) < t.GATE)
    return {"case": [kind, a, off, ang, name], "shown": rec["shown"], "contrast": rec["contrast"],
            "best_tried": rec["best_tried"], "independent": None if ind is None else round(ind, 2), "ok": bool(ok)}


if __name__ == "__main__":
    out = Path(sys.argv[2])
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
    cs = cases()
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(run, cs, chunksize=8))
    bad = [r for r in rows if not r["ok"]]
    summary = {"cases": len(rows), "drawn": sum(r["shown"] for r in rows), "fail_closed": sum(not r["shown"] for r in rows),
               "failures": len(bad), "min_independent_drawn": min((r["independent"] for r in rows if r["shown"]),
                                                                   default=None)}
    out.write_text(json.dumps({"summary": summary, "failures": bad, "rows": rows}, indent=1), encoding="utf-8",
                   newline="\n")
    print(json.dumps(summary))
