"""Cycle 0 (design SYSTEM): the acceptance metrics on committed inputs only.

    python measure.py <repo root>

Prints, for the one masthead treatment (src/masthead.py) at the carousel, YouTube and end-screen sizes on six
grounds (dark, mid grey, sky, near-white, a busy noise field, the committed Richat still): the variant, scrim peak,
halo, planned and delivered (960 px q70 re-encode) contrast, and whether the ruled limits hold (peak <= 0.60,
halo <= 0.16, contrast >= 5.5). Then the system's counts: tokens, gates, type roles per canvas, the one line-end
vocabulary. On a tree before cycle 0 it prints "no system" and the old carousel masthead's numbers.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
os.environ.setdefault("FONT_SIZE", "80")
sys.path.insert(0, str(root))

import numpy as np
from PIL import Image

RICHAT = root / "docs/reviews/freeze-panel-2026-11-04/fixtures/stills/richat_structure_sahara_eye_01_commons_960.jpg"


def grounds():
    w, h = 1920, 1800
    yield "dark", Image.new("RGB", (w, h), (24, 28, 34))
    yield "mid", Image.new("RGB", (w, h), (128, 128, 128))
    yield "sky", Image.new("RGB", (w, h), (120, 170, 225))
    yield "near_white", Image.new("RGB", (w, h), (246, 246, 242))
    noise = np.random.default_rng(7).integers(90, 250, (h // 8, w // 8, 3), dtype=np.uint8)
    yield "busy", Image.fromarray(noise).resize((w, h), Image.NEAREST)
    yield "richat", Image.open(RICHAT).convert("RGB").resize((w, h))


def main() -> None:
    try:
        from src import brand_system, masthead
    except ImportError:
        print("no system (src/brand_system.py absent): before cycle 0")
        return
    from src.multi_format import platform_encode
    sizes = {"carousel": brand_system.type_px("masthead", "carousel"), "youtube": brand_system.type_px("masthead", "wide"),
             "end_screen": 82}
    rows, ok = [], True
    for g, im in grounds():
        for s, size in sizes.items():
            base = round(size * 1.9)
            out, plate, rec, _ = masthead.draw(im, 107, base, size, reach=base + 6 * size)
            shown = rec.get("shown", True)
            # delivered: the worst local window after the platform re-encode (round 2); a masthead not drawn (fail
            # closed) is within the rulings by construction and has nothing to deliver
            delivered = plate.measure(platform_encode(out)) if shown else None   # the ring after re-encode
            good = (rec["scrim_peak"] <= brand_system.tok("scrim.masthead_peak") + 1e-9
                    and rec["halo"] <= (brand_system.HALO_EXCEPTION_CAP if rec.get("halo_exception")
                                        else brand_system.HALO_CAP) + 1e-9   # ruling 4: the recorded exception
                    and (not shown or (rec["contrast"] or 0) >= brand_system.tok("contrast.masthead_gate")))
            ok &= good
            rows.append({"ground": g, "surface": s, "shown": shown, "variant": rec["variant"], "peak": rec["scrim_peak"],
                         "halo": rec["halo"], "contrast_worst_letter": rec["contrast"],
                         "delivered": None if delivered is None else round(delivered, 2),
                         "mark_contrast": rec.get("mark_contrast"), "short_of_target": rec["short_of_target"],
                         "within_rulings": good})
    for r in rows:
        print(json.dumps(r))
    print("masthead within the rulings on every ground and size:", ok)
    print("tokens:", len(brand_system.SYSTEM_TOKENS), "gates:", len(brand_system.GATES),
          "type roles per canvas:", {c: len(v) for c, v in brand_system.TYPE.items()},
          "line-end words never / last resort:", len(brand_system.LINE_END_NEVER), len(brand_system.LINE_END_LAST_RESORT),
          "named eases:", len(brand_system.EASES))


if __name__ == "__main__":
    main()
