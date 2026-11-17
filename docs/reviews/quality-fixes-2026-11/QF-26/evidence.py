"""QF-26 evidence: the same real designs rendered by two trees (measure.last_frame, committed inputs), compared.

    python docs/reviews/quality-fixes-2026-11/QF-26/evidence.py <before root> <after root> [<out dir>]

Prints JSON (evidence.json) and, given <out dir>, writes there (each a JPEG under 300 KB):
- <design>_<key>_<fmt>.jpg: the last frame, before (left) and after (right), at half size;
- <design>_<key>_<fmt>_detail.jpg: two native-pixel crops, before over after, each shown at 4x with
  nearest-neighbour (the antialiased edges are a pixel wide, invisible at half size): on a map the first is where
  the home coast's gold edge changed most (review 1: the worst case of that edge), the second where the frames
  differ most; elsewhere the two windows that differ most.
Per design: the pixels that changed, the share of them within one pixel of a drawn edge (a pixel whose 4-neighbour
luma step is 8 or more, in either frame), and the largest change anywhere farther than that from an edge (the
colours inside shapes, which must not change).
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure

DESIGNS = [("map", "hal_saflieni_hypogeum", "reel"), ("map", "kailasa_temple_monolith", "wide"),
           ("map", "flannan_isle_lighthouse_disappearance", "reel"), ("map", "camp_century_nuclear_ice_city", "reel"),
           ("map", "odessa_catacombs", "reel"),
           ("scale", "wieliczka_salt_mine", "reel"), ("scale", "unfinished_obelisk_aswan", "wide"),
           ("numbers", "unfinished_obelisk_aswan", "reel")]
LIMIT = 300 * 1024


def _frame(args: tuple[str, str, str, str]) -> bytes | None:
    root, design, key, fmt = args
    frame, _record = measure.last_frame(Path(root), design, key, fmt)
    if frame is None:
        return None
    import io
    buf = io.BytesIO()
    frame.save(buf, "PNG")
    return buf.getvalue()


def _edges(lum: np.ndarray) -> np.ndarray:
    e = np.zeros(lum.shape, bool)
    dx, dy = np.abs(np.diff(lum, axis=1)) >= 8, np.abs(np.diff(lum, axis=0)) >= 8
    e[:, 1:] |= dx
    e[:, :-1] |= dx
    e[1:] |= dy
    e[:-1] |= dy
    return e


def _grow(m: np.ndarray) -> np.ndarray:
    g = m.copy()
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            g |= np.roll(np.roll(m, oy, 0), ox, 1)
    return g


def _save(im: Image.Image, path: Path) -> None:
    for q in (88, 82, 76, 70, 64, 58):
        im.save(path, quality=q, optimize=True)
        if path.stat().st_size <= LIMIT:
            return
    raise RuntimeError(f"{path.name} over 300 KB")


def compare(a: Image.Image, b: Image.Image) -> dict:
    x, y = np.asarray(a).astype(int), np.asarray(b).astype(int)
    lum = lambda v: v @ np.array([299, 587, 114]) / 1000.0
    near = _grow(_edges(lum(x)) | _edges(lum(y)))
    diff = np.abs(x - y).max(axis=-1)
    changed = diff > 0
    return {"changed_px": int(changed.sum()), "changed_share_of_frame": round(float(changed.mean()), 4),
            "changed_within_1px_of_an_edge": round(float(near[changed].mean()), 4) if changed.any() else None,
            "max_change_away_from_edges": int(diff[~near].max()) if (~near).any() else 0}


def boards(name: str, a: Image.Image, b: Image.Image, out: Path) -> None:
    w, h = a.width // 2, a.height // 2
    sheet = Image.new("RGB", (2 * w + 10, h + 26), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for k, (im, label) in enumerate(((a, "before (base)"), (b, "after (QF-26)"))):
        sheet.paste(im.resize((w, h), Image.LANCZOS), (k * (w + 10), 26))
        d.text((k * (w + 10) + 4, 6), f"{name}: {label}", fill=(0, 0, 0))
    _save(sheet, out / f"{name}.jpg")
    diff = np.abs(np.asarray(a).astype(int) - np.asarray(b).astype(int)).max(axis=-1).astype(float)
    side, zoom = 96, 4
    picks: list[tuple[int, int]] = []
    weights = [diff, diff]
    if name.startswith("map_"):                  # the home coast's gold edge first
        x = np.asarray(a).astype(float)
        lum = x @ np.array([0.299, 0.587, 0.114])
        gold, dark = (x[..., 0] - x[..., 2]) > 40, lum < 45
        weights[0] = diff * (gold & _grow(_grow(dark)))
    for weight in weights:
        ii = np.cumsum(np.cumsum(np.pad(weight, ((1, 0), (1, 0))), 0), 1)
        best, at = -1.0, (0, 0)
        for cy in range(0, diff.shape[0] - side, side // 2):
            for cx in range(0, diff.shape[1] - side, side // 2):
                if any(abs(cx - px) < side and abs(cy - py) < side for px, py in picks):
                    continue
                v = ii[cy + side, cx + side] - ii[cy, cx + side] - ii[cy + side, cx] + ii[cy, cx]
                if v > best:
                    best, at = v, (cx, cy)
        picks.append(at)
    tile = side * zoom
    detail = Image.new("RGB", (2 * tile + 10, 2 * tile + 10 + 26), (255, 255, 255))
    dd = ImageDraw.Draw(detail)
    dd.text((4, 6), f"{name}: native pixels x{zoom}; top before, bottom after; crops at {picks}", fill=(0, 0, 0))
    for k, (cx, cy) in enumerate(picks):
        box = (cx, cy, cx + side, cy + side)
        for r, im in enumerate((a, b)):
            detail.paste(im.crop(box).resize((tile, tile), Image.NEAREST), (k * (tile + 10), 26 + r * (tile + 10)))
    _save(detail, out / f"{name}_detail.jpg")


def main(before: Path, after: Path, out: Path | None) -> dict:
    jobs = [(str(root), *d) for d in DESIGNS for root in (before, after)]
    with ProcessPoolExecutor(max_workers=4, max_tasks_per_child=1) as pool:   # one tree per process
        pngs = list(pool.map(_frame, jobs))
    import io
    result = {}
    for k, (design, key, fmt) in enumerate(DESIGNS):
        name = f"{design}_{key}_{fmt}"
        pa, pb = pngs[2 * k], pngs[2 * k + 1]
        if pa is None or pb is None:
            result[name] = {"rendered": False}
            continue
        a, b = Image.open(io.BytesIO(pa)).convert("RGB"), Image.open(io.BytesIO(pb)).convert("RGB")
        result[name] = compare(a, b)
        if design == "map":
            result[name]["home_rim"] = {"before": measure.home_rim(np.asarray(a)), "after": measure.home_rim(np.asarray(b))}
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
            boards(name, a, b, out)
    return result


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(),
                          Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None), indent=1))
