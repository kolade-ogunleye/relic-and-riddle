"""PLATES-06 (panel 6): a palette whose masthead roles cannot carry the name on a bright hero is caught when it is
configured (masthead.palette_problems, called by brand.configure), not discovered as a missing mark on a slide.

For each test palette (default; "second": the board's second brand, colours channel-swapped, Montserrat display;
"second-colours": the same colours, the default display; "third": every channel moved, Cinzel), as
docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py builds them:
1. the configure-time verdict (masthead.palette_problems: the stored validation grounds, after every ruled step);
2. every photograph of every dev run's topic as a full-bleed carousel ground (cover crop 1440 x 1800), the masthead
   planned at the carousel's position, size and reach (masthead.plan, the margin steps included), under that palette:
   drawn / withheld, and the worst-glyph contrast.
A violation: a palette the configure check ACCEPTS whose masthead the gate withholds on a corpus ground (the check
did not catch it); a palette it refuses is listed with its reasons (the run never draws with it).
"""
from __future__ import annotations

import plates_common as pc

a, tree, dev = pc.setup(__doc__, lambda ap: ap.add_argument("--palettes", default="default,second,second-colours"))

import numpy as np  # noqa: E402

from src import brand, masthead  # noqa: E402
from src.brand_system import type_px  # noqa: E402
from src.multi_format import MultiFormatGenerator  # noqa: E402
from src.photo_grade import cover  # noqa: E402
from src.typography import cap_height  # noqa: E402

sys_path = tree / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"
__import__("sys").path.insert(0, str(sys_path))
import render_surfaces  # noqa: E402

BUILD = {"default": lambda: brand.DEFAULT_BRAND,
         "second": lambda: render_surfaces.second_brand(brand, "Montserrat"),
         "second-colours": lambda: render_surfaces.second_brand(brand, None),
         "third": lambda: render_surfaces.third_brand(brand)}
photos = pc.corpus_photos(dev, a.per_topic)
grounds = [(t, n, p, cover(p, (1440, 1800))) for t, n, p in photos]
rows, bad, summary = [], [], {}
for name in a.palettes.split(","):
    cfg = BUILD[name]()
    problems = masthead.palette_problems(cfg)
    accepted = not problems
    drawn, withheld, cs = 0, [], []
    with brand.use(cfg, validate=False):
        cw = 1440
        size = type_px("masthead", "carousel")
        font = masthead.font_for(size)
        k = cw / 1080
        x0 = round(80 * k)
        base = round(MultiFormatGenerator.HEADER_Y * k) + cap_height(font)
        for topic, n, photo, ground in grounds:
            _, _, rec = masthead.plan(ground, x0, base, size, reach=base + round(300 * k),
                                      max_width=cw - round(280 * k) - x0)
            shown = bool(rec.get("shown"))
            c = rec.get("contrast")
            row = {"palette": name, "topic": topic, "photo": photo.name, "shown": shown, "contrast": c,
                   "best_tried": rec.get("best_tried"), "fit_floor": rec.get("fit_floor_px")}
            ok = shown or not accepted or rec.get("fit_floor_px") is not None
            row["ok"] = ok
            rows.append(row)
            if shown:
                drawn += 1
                cs.append(float(c))
            else:
                withheld.append(f"{topic}/{photo.name} best {rec.get('best_tried')}")
            if not ok:
                bad.append(row)
            print(f"{name:14} {topic[:28]:28} {photo.name:16} {'drawn' if shown else 'WITHHELD':8} "
                  f"{'-' if c is None else format(c, '.2f'):>5} {'pass' if ok else 'FAIL'}", flush=True)
    summary[name] = {"configure": "accepted" if accepted else "refused: " + " | ".join(problems)[:400],
                     "grounds": len(grounds), "drawn": drawn, "withheld": len(withheld),
                     "contrast min / p5 / median": (f"{min(cs):.2f} / {np.percentile(cs, 5):.2f} / {np.median(cs):.2f}"
                                                    if cs else "-"),
                     "withheld grounds": withheld[:12]}
pc.finish(a, "PLATES-06 the palette's masthead is checked at configure", rows, bad, summary)
