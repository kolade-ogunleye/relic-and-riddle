"""PLATEFORM (cycle 6, the block family; lander ruling 5): the hook frame and the 9:16 myth card as production-encoded
clips (board_motion.encode: the delivery encoder), base and block, each decoded once and read as a time strip (frames
at real times, the block's mean luma and the type's alpha per frame beside them) and run through the photosensitivity
check (picture_qa.measure: WCAG 2.3.1 general and red flash).

    python pf_motion.py <tree> <dev checkout> <out> <form>   (cases: hook obelisk, myth tunguska 09 and hero richat)

Writes <out>/<form>/<case>.mp4, <case>__strip.jpg and motion.json ({case: {frames, flash, block luma per frame}}).
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def main() -> None:
    tree, dev, out_root, form = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]), sys.argv[4]
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev), RR_PLATE_FORM=form)

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
    sys.path.insert(0, str(tree / "docs/reviews/freeze-system-2026-11-15"))
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    import board_motion as bm
    import fixtures as fx
    from src import config, design_cards as dc, picture_qa
    from tests import test_freeze_cards_2026_11_04 as fc
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"
    heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
    richat = fc.real_sheet("richat_structure_sahara_eye")
    tung = sorted((dev / "assets" / "tunguska_event_1908").glob("09*.jpg"))[0]
    cases = {"hook__hero_obelisk": ("hook", Path(heroes["unfinished_obelisk_aswan"])),
             "myth__tunguska_09": ("myth", tung),
             "myth__hero_richat": ("myth", Path(fx.still("richat_structure_sahara_eye_01_commons_960.jpg")))}
    out = out_root / form
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(tree / "fonts" / "IBMPlexMono-Medium.ttf"), 22)
    res = {}
    for name, (kind, photo) in cases.items():
        plan = fc.hook_plan(photo) if kind == "hook" else fc.richat_myth(richat, photo=photo, size=fc.TALL)
        size = (plan.width, plan.height)
        clip = out / f"{name}.mp4"
        enc = bm.encode((dc.frame_at(plan, i) for i in range(plan.frames)), clip, size)
        meta = plan.meta.get("scrim") if kind == "hook" else plan.meta.get("panel_edges")
        pf = (meta or {}).get("plate_form") or {}
        blocks = pf.get("blocks") or []
        box = blocks[0]["box"] if blocks else None
        frames = list(bm.decode(clip, size))
        # the block's own area, where the type is not: its right strip past the longest line (the block's mean luma)
        lum = []
        for im in frames:
            if box:
                a = np.asarray(im.convert("L"), np.float32)[box[1] + 4:box[3] - 4, box[0] + 4:box[0] + 16]
                lum.append(round(float(a.mean()), 1))
        n = len(frames)
        picks = sorted(set([0, 6, 9, 12] + list(range(30, n, 15)) + [n - 1])) if kind == "myth" else \
            sorted(set([0, 15, 30, 32, 34, 36, 38, 40, 42, 45, n - 1]))
        picks = [p for p in picks if p < n]
        tw, th = 216, 384
        S = Image.new("RGB", (len(picks) * (tw + 6) + 6, th + 60), (236, 233, 226))
        d = ImageDraw.Draw(S)
        for j, p in enumerate(picks):
            S.paste(frames[p].resize((tw, th)), (6 + j * (tw + 6), 6))
            d.text((6 + j * (tw + 6), th + 10), f"f{p} {p / 30:.2f}s", fill=(20, 20, 20), font=font)
            if lum:
                d.text((6 + j * (tw + 6), th + 32), f"block L {lum[p]}", fill=(20, 20, 20), font=font)
        S.save(out / f"{name}__strip.jpg", quality=86)
        qa = picture_qa.measure(clip)
        flash = [{k: c.get(k) for k in ("name", "status", "detail")} for c in (qa.get("checks") or [])
                 if "flash" in str(c.get("name", ""))]
        res[name] = {"frames": n, "encoder": enc["encoder"], "block": box, "laid": pf.get("laid"),
                     "block_luma": lum, "flash": flash}
        print(name, n, "frames; block", box, "flash", json.dumps(flash, default=str)[:300], flush=True)
    (out / "motion.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
