"""THENNOW (freeze cycle 6): the dev corpus of then-and-now pairs and the measures both THENNOW scripts share.

The corpus: every pair a dev run could draw (graphics/then_k x graphics/now_k_j, as corpus/photo_fitness.py's PAIR
reads it) of every run that drew the comparison (graphics/graphic_thennow.mp4: only such a run accepted a pair), in
both formats. The years are the run's own (the fixture catalogue's then-and-now case for that run: read off its
graphic_thennow.mp4); a candidate pair of the same run is drawn with the same two years (the text is not under test).

Everything here measures; nothing here draws what it measures. The measures read the tree under test only through
its own then_now functions (frame_pair, compose, pushed, marker_lines, ...) so base and tip are measured alike.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FORMATS = ((1080, 1920, "916"), (1920, 1080, "169"))
SIDE_MARGIN = 0.15          # then_now.SIDE_MARGIN at the cycle-6 tip: a place within this of the calmest is the calmest
CENTRE_HALF = 0.25          # photo_fitness centre_half: the shown box covers [0.25, 0.75] of both axes


def fixture_years(tree: Path) -> dict[str, tuple[int, int]]:
    cat = json.loads((tree / "docs/reviews/freeze-panel-2026-11-04/fixtures/fixtures.json").read_text(encoding="utf-8"))
    out = {}
    for case in cat["designs"]["thennow"]["cases"]:
        out[Path(str(case["run"]).replace("\\", "/")).name] = (int(case["old_year"]), int(case["new_year"]))
    return out


def pairs(tree: Path, dev: Path) -> list[dict[str, Any]]:
    """Every (run, then_k, now_k_j) of every run that drew the comparison, with the run's years."""
    years = fixture_years(tree)
    out = []
    for run in sorted((dev / "output").glob("run_*")):
        g = run / "graphics"
        if not (g / "graphic_thennow.mp4").is_file():
            continue
        if run.name not in years:
            raise SystemExit(f"{run.name}: drew a then-and-now but the fixture catalogue has no years for it")
        oy, ny = years[run.name]
        for old in sorted(list(g.glob("then_*.jpg")) + list(g.glob("then_*.png"))):
            k = old.stem.split("_")[1]
            for new in sorted(list(g.glob(f"now_{k}_*.jpg")) + list(g.glob(f"now_{k}_*.png"))):
                topic = run.name.split("_17")[0].replace("run_", "")
                out.append({"run": run.name, "topic": topic, "old": old, "new": new, "old_year": oy, "new_year": ny,
                            "id": f"{topic}__{old.stem}__{new.stem}"})
    return out


# Pool pairs the cycle-6 review constructed (REVIEW-thennow-c6.md C1-C4): photographs of four other runs, each a
# then_k / now_k_j pool pair of its run, with the years the review drew them with. Registered only (below).
POOL = (("run_wieliczka_salt_mine_1792974672", "then_1", "now_1_0", 1890, 2015),
        ("run_odessa_catacombs_1792970549", "then_3", "now_3_1", 1900, 2016),
        ("run_kailasa_temple_monolith_1792524470", "then_0", "now_0_0", 1870, 2012),
        ("run_amber_room_heist_1792805842", "then_2", "now_2_5", 1917, 2017))


def registered_pairs(tree: Path, dev: Path, cache: Path, then_now) -> list[dict[str, Any]]:
    """REGISTERED test pairs, the only kind production draws (make -> source -> register): no dev pair passes
    shares_viewpoint, so each dev pair above and each POOL pair is put through then_now.register with the identity
    matrix (the recent photograph cover-fitted to the old one's frame: one picture at one size, as register writes
    it). A stand-in for a real registration (whose warp is the matched view's), stated as such in the hand-back."""
    import numpy as np
    from PIL import Image, ImageOps
    src = [dict(p) for p in pairs(tree, dev)]
    for run, o, n, oy, ny in POOL:
        g = dev / "output" / run / "graphics"
        topic = run.split("_17")[0].replace("run_", "")
        src.append({"run": run, "topic": topic, "old": g / f"{o}.jpg", "new": g / f"{n}.jpg", "old_year": oy,
                    "new_year": ny, "id": f"{topic}__{o}__{n}"})
    out = []
    for p in src:
        d = cache / p["id"]
        reg = (d / "reg" / "old_reg.png", d / "reg" / "new_fit_reg.png")
        if not all(x.is_file() for x in reg):
            d.mkdir(parents=True, exist_ok=True)
            o = Image.open(p["old"]).convert("RGB")
            ImageOps.fit(Image.open(p["new"]).convert("RGB"), o.size, Image.LANCZOS).save(d / "new_fit.png")
            o.save(d / "old.png")
            reg = then_now.register(d / "old.png", d / "new_fit.png", np.array([[1.0, 0, 0], [0, 1.0, 0]]), d / "reg")
            if reg is None:
                raise SystemExit(f"{p['id']}: register() refused the identity pair")
        out.append({**p, "old": reg[0], "new": reg[1], "id": "reg__" + p["id"], "registered": True})
    return out


def use_tree(tree: Path):
    sys.path.insert(0, str(tree))
    import os
    os.chdir(tree)
    from src.art_direction import use_palette
    use_palette(None)
    from src import then_now
    return then_now


def detail(frame, box) -> float:
    """The ground's detail under `box`: the mean absolute luma change pixel to pixel, across and down (luma per px
    at 1080 wide). The measure the cycle-6 rule names (then_now.ground_detail), restated here so base and tip are
    read by one definition."""
    import numpy as np
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(frame.width, x1), min(frame.height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0.0
    a = np.asarray(frame.convert("RGB").crop((x0, y0, x1, y1)), np.float32)
    y = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    d = (float(np.abs(np.diff(y, axis=0)).mean()) + float(np.abs(np.diff(y, axis=1)).mean())) / 2
    return d * min(frame.width, frame.height) / 1080.0


def shown_boxes(then_now, info: dict, sizes: list[tuple[int, int]], width: int, height: int) -> dict[str, Any]:
    """The part of each photograph (shares of its trimmed width and height) the frame shows at rest in its panel:
    from the tip's own record (info["panels"]) where it has one, else derived from the shared window frame_pair
    recorded (zoom, v_centre, u_centre; centred when it records none): the push's end shows the window's central
    1 / (1 + PUSH); THEN is the part of it above the split, NOW below (9:16) / left and right (16:9)."""
    if info.get("panels"):
        return {w: dict(info["panels"][w]["shown"], mode=info["panels"][w]["mode"]) for w in ("then", "now")}
    big = (round(width * (1 + then_now.PUSH)), round(height * (1 + then_now.PUSH)))
    z = float(info.get("zoom", 1.0))
    v = float(info.get("v_centre", 0.5))
    u = float(info.get("u_centre", 0.5))
    split = then_now._split_at(width, height)
    out = {}
    for which, (sw, sh) in zip(("then", "now"), sizes):
        x0, y0, x1, y1 = then_now._cover_window((sw, sh), big, z, v, u)
        cx, cy, ww, wh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / (1 + then_now.PUSH), (y1 - y0) / (1 + then_now.PUSH)
        vx0, vy0, vx1, vy1 = cx - ww / 2, cy - wh / 2, cx + ww / 2, cy + wh / 2
        if width > height:
            f = split / width
            px0, px1 = (vx0, vx0 + f * ww) if which == "then" else (vx0 + f * ww, vx1)
            py0, py1 = vy0, vy1
        else:
            f = split / height
            py0, py1 = (vy0, vy0 + f * wh) if which == "then" else (vy0 + f * wh, vy1)
            px0, px1 = vx0, vx1
        box = [round(px0 / sw, 4), round(py0 / sh, 4), round(px1 / sw, 4), round(py1 / sh, 4)]
        half = box[0] <= CENTRE_HALF + 1e-4 and box[2] >= 1 - CENTRE_HALF - 1e-4 and \
            box[1] <= CENTRE_HALF + 1e-4 and box[3] >= 1 - CENTRE_HALF - 1e-4
        out[which] = {"w": round(box[2] - box[0], 4), "h": round(box[3] - box[1], 4), "box": box,
                      "centre_half": bool(half), "mode": "shared window"}
    return out


def rest_view(window, size, push: float) -> tuple[float, float, float, float]:
    """The part of a picture (shares) a source `window` shows at rest: its central 1 / (1 + push)."""
    x0, y0, x1, y1 = window
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 / (1 + push), (y1 - y0) / 2 / (1 + push)
    return (cx - hw) / size[0], (cy - hh) / size[1], (cx + hw) / size[0], (cy + hh) / size[1]


def trimmed_sizes(then_now, pair: dict, info: dict) -> list[tuple[int, int]]:
    ims = [then_now._load_clean(pair["old"]), then_now._load_clean(pair["new"])]
    t = info.get("trimmed_px") or {}
    out = []
    for k, im in zip(("old", "new"), ims):
        a = t.get(k, [0, 0, 0, 0])
        out.append((im.width - a[0] - a[2], im.height - a[1] - a[3]))
    return out
