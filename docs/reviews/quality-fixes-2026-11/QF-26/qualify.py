"""QF-26 rasteriser qualification: Pillow's ImageDraw, OpenCV's LINE_AA fill (when installed) and the Skia path
(src/raster.py) against an exact coverage reference, on the target families. Prints JSON; writes nothing.

    python docs/reviews/quality-fixes-2026-11/QF-26/qualify.py <repo root>

The reference is 32 x 32 point samples per pixel (pixel (i, j) the square [i, i + 1) x [j, j + 1)): an even-odd
point-in-ring test for fills, the distance to the polyline for strokes (round joins, butt ends: the ends
ImageDraw and the exact grid draw). Metrics on the edge band
(pixels within one of a partly covered reference pixel): RMSE, the largest error, the area bias in pixels, the
coverage centroid's shift and the number of distinct grey levels drawn there. "digests" are sha256 of the Skia
path's output for each case, for the Windows / Linux determinism check (the same script on both).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

W, H, S = 192, 160, 32
FILLS = {
    "land_with_hole": [[(17.25, 25.75), (61.5, 12.25), (153.75, 40.5), (175.25, 101.75), (123.5, 143.25),
                        (91.75, 101.5), (30.25, 130.75)], [(69.25, 48.5), (118.75, 52.25), (103.5, 83.75),
                                                           (61.75, 75.25)]],
    "scale_silhouette": [[(23.25, 140.5), (40.5, 123.75), (60.75, 121.5), (69.25, 78.75), (79.5, 78.75),
                          (84.25, 39.5), (76.5, 35.25), (80.5, 16.25), (89.75, 33.5), (102.5, 58.25),
                          (113.25, 78.5), (123.5, 122.25), (151.75, 125.5), (168.25, 140.5)]],
    "fractional_rectangle": [[(37.3, 28.6), (145.9, 28.6), (145.9, 130.2), (37.3, 130.2)]],
    "thin_sliver": [[(10.3, 20.2), (180.6, 60.7), (180.9, 62.1)]],
    "small_island": [[(50.4, 50.2), (53.1, 49.7), (54.2, 52.6), (51.0, 53.4)]],
}
STROKES = {   # (points, width, closed): a border ring, a rule, a diagonal
    "border_ring_w3": ([(20.3, 30.6), (90.2, 15.1), (170.7, 60.4), (140.1, 140.8), (40.6, 120.3)], 3, True),
    "rule_w1": ([(12.0, 40.4), (180.0, 40.4)], 1, False),
    "diagonal_w2": ([(15.2, 150.1), (175.3, 12.7)], 2, False),
}


def _samples() -> tuple[np.ndarray, np.ndarray]:
    return (np.arange(H * S) + 0.5) / S, (np.arange(W * S) + 0.5) / S


def ref_fill(rings: list) -> np.ndarray:
    ys, xs = _samples()
    inside = np.zeros((H * S, W * S), bool)
    X, Y = xs[None, :], ys[:, None]
    for ring in rings:
        r = np.asarray(ring, float)
        for (x1, y1), (x2, y2) in zip(r, np.roll(r, -1, axis=0)):
            if y1 != y2:
                inside ^= ((y1 > Y) != (y2 > Y)) & (X < x1 + (Y - y1) * (x2 - x1) / (y2 - y1))
    return inside.reshape(H, S, W, S).mean(axis=(1, 3))


def ref_stroke(pts: list, width: float, closed: bool) -> np.ndarray:
    ys, xs = _samples()
    out = np.zeros((H * S, W * S), bool)
    p = list(pts) + ([pts[0]] if closed else [])
    for row in range(0, H * S, 512):          # in bands, to bound memory
        Y, X = np.meshgrid(ys[row:row + 512], xs, indexing="ij")
        d2 = np.full(X.shape, np.inf)
        for (ax, ay), (bx, by) in itertools.pairwise(p):
            dx, dy = bx - ax, by - ay
            t = ((X - ax) * dx + (Y - ay) * dy) / (dx * dx + dy * dy)
            perp = (X - ax - t * dx) ** 2 + (Y - ay - t * dy) ** 2
            d2 = np.minimum(d2, np.where((t >= 0) & (t <= 1), perp, np.inf))
        for vx, vy in (p if closed else p[1:-1]):          # the round joins
            d2 = np.minimum(d2, (X - vx) ** 2 + (Y - vy) ** 2)
        out[row:row + 512] = d2 <= (width / 2) ** 2
    return out.reshape(H, S, W, S).mean(axis=(1, 3))


def _grey(draw_cls, fn) -> np.ndarray:
    im = Image.new("L", (W, H), 0)
    fn(draw_cls(im))
    return np.asarray(im, np.float64) / 255


def fill_with(d, rings: list) -> None:
    for k, ring in enumerate(rings):
        d.polygon(ring, fill=255 if k == 0 else 0)


def stroke_with(d, pts: list, width: int, closed: bool) -> None:
    d.line(list(pts) + ([pts[0]] if closed else []), fill=255, width=width, joint="curve")


def opencv_fill(rings: list) -> np.ndarray | None:
    try:
        import cv2
    except ImportError:
        return None
    im = np.zeros((H, W), np.uint8)
    cv2.fillPoly(im, [np.rint((np.asarray(r) - 0.5) * 256).astype(np.int32) for r in rings], 255,
                 lineType=cv2.LINE_AA, shift=8)
    return im.astype(np.float64) / 255


def edge_band(ref: np.ndarray) -> np.ndarray:
    def grow(m: np.ndarray) -> np.ndarray:
        g = m.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                g |= np.roll(np.roll(m, dy, 0), dx, 1)
        return g
    return grow(ref > 0.001) & grow(ref < 0.999)


def metrics(c: np.ndarray, ref: np.ndarray) -> dict:
    e = edge_band(ref)
    yy, xx = np.mgrid[0:H, 0:W]

    def cen(a: np.ndarray) -> tuple[float, float]:
        return float((a * xx).sum() / a.sum()), float((a * yy).sum() / a.sum())
    (cx, cy), (rx, ry) = cen(c), cen(ref)
    return {"edge_rmse": round(float(np.sqrt(np.mean((c[e] - ref[e]) ** 2))), 4),
            "max_error": round(float(np.abs(c - ref).max()), 4),
            "area_bias_px": round(float(c.sum() - ref.sum()), 2),
            "centroid_shift_px": [round(cx - rx, 3), round(cy - ry, 3)],
            "edge_levels": len(np.unique(np.round(c[e] * 255)))}


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    import skia

    from src import raster

    def skia_exact(im: Image.Image) -> raster.Draw:
        return raster.Draw(im, grid="exact")
    cases, digests = {}, {}
    for name, rings in FILLS.items():
        ref = ref_fill(rings)
        sk = _grey(skia_exact, lambda d, r=rings: fill_with(d, r))
        row = {"pillow": metrics(_grey(ImageDraw.Draw, lambda d, r=rings: fill_with(d, r)), ref),
               "skia": metrics(sk, ref)}
        ss, raster.VERTICAL_SS = raster.VERTICAL_SS, 1        # Skia's own 4 sub-scanlines, for the record
        row["skia_vertical_ss_1"] = metrics(_grey(skia_exact, lambda d, r=rings: fill_with(d, r)), ref)
        raster.VERTICAL_SS = ss
        cv = opencv_fill(rings)
        if cv is not None:
            row["opencv_aa"] = metrics(cv, ref)
        cases[name] = row
        digests[name] = hashlib.sha256(np.rint(sk * 255).astype(np.uint8).tobytes()).hexdigest()
    for name, (pts, w, closed) in STROKES.items():
        ref = ref_stroke(pts, w, closed)
        sk = _grey(skia_exact, lambda d, p=pts, ww=w, c=closed: stroke_with(d, p, ww, c))
        cases[name] = {"pillow": metrics(_grey(ImageDraw.Draw, lambda d, p=pts, ww=w, c=closed:
                                               stroke_with(d, p, ww, c)), ref),
                       "skia": metrics(sk, ref)}
        digests[name] = hashlib.sha256(np.rint(sk * 255).astype(np.uint8).tobytes()).hexdigest()
    # colour compositing (RGB blend, RGBA replace with an inside outline) on a ground, for the determinism digest
    rgb = Image.new("RGB", (W, H), (14, 22, 34))
    raster.Draw(rgb, "RGBA").polygon(FILLS["land_with_hole"][0], fill=(201, 162, 39, 200))
    rgba = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    raster.Draw(rgba).polygon(FILLS["scale_silhouette"][0], fill=(232, 227, 218, 255), outline=(20, 20, 20, 255),
                              width=2)
    digests["rgb_blend"] = hashlib.sha256(rgb.tobytes()).hexdigest()
    digests["rgba_replace"] = hashlib.sha256(rgba.tobytes()).hexdigest()
    wins = {k: v["skia"]["edge_rmse"] < v["pillow"]["edge_rmse"]
            and abs(v["skia"]["area_bias_px"]) < abs(v["pillow"]["area_bias_px"]) for k, v in cases.items()}
    return {"skia": skia.__version__, "platform": sys.platform, "reference": f"{S}x{S} point samples per pixel",
            "cases": cases, "skia_beats_pillow_on_edge_rmse_and_area_bias": wins, "all": all(wins.values()),
            "digests": digests}


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1))
