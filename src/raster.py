"""One antialiased path for drawn geometry (QF-26): land and borders, scale silhouettes, strata, bars and rules.

`Draw(im, mode=None, grid="pixel")` takes the calls Pillow's ImageDraw takes for shapes (polygon, line, rectangle,
ellipse) and draws them antialiased. Skia (skia-python, CPU raster surfaces only, no GPU context) computes each
shape's exact area coverage in an A8 mask; the shape's colour is then laid on the Pillow image through that
coverage. A pixel the shape wholly covers takes exactly the colour Pillow would have written there, a pixel it
misses keeps its own, and only the pixels a boundary crosses take the covered share (QF-26: contrast and colour
unchanged except at the edges).

Coordinates are continuous: pixel (i, j) is the square [i, i + 1) x [j, j + 1).
- polygon: the ring itself, exactly (Pillow's fill of fractional rings is centred on the ring too, and dilated
  about half a pixel; measured in docs/reviews/quality-fixes-2026-11/QF-26/qualify.py).
- grid "pixel" (layout geometry, the default): a coordinate names a pixel as Pillow reads it, so a whole-pixel
  rectangle, ellipse or axis-aligned line covers exactly the pixels Pillow fills, and fractional geometry lies
  between them instead of being cut to a whole pixel: a box [x0, y0, x1, y1] covers [x0, x1 + 1) x [y0, y1 + 1);
  a line runs through the pixel centres of its points (+0.5, and half a pixel more across it for an even width,
  where Pillow puts the extra row) and ends half a pixel past its end points.
- grid "exact" (projected map geometry): boxes and lines are the stated geometry, so a border is centred on the
  coast its land fill ends at (Pillow cuts a fractional line to the row below; its mean centre is the same).

Blending follows ImageDraw: drawn in mode "RGBA" on an RGB image the ink's alpha blends; otherwise the ink
replaces (on RGBA, all four channels, interpolated by coverage on premultiplied colour, so no dark fringe). Masks
("L") take the fill value by coverage.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import skia
from PIL import Image

Point = tuple[float, float]
# Skia's CPU path AA takes 4 sub-scanlines a pixel row (horizontal coverage is exact); drawing each shape 4x tall and
# averaging the rows gives 16, which cuts the edge error 3-4x on every family (QF-26/qualify.py, "vertical_ss").
VERTICAL_SS = 4
BAND = 512           # rows of the image a coverage pass takes at a time (bounds the memory of a large shape)


def _points(xy: Sequence[Any]) -> list[Point]:
    if not xy:
        return []
    if isinstance(xy[0], (int, float)):
        return [(float(xy[i]), float(xy[i + 1])) for i in range(0, len(xy) - 1, 2)]
    return [(float(p[0]), float(p[1])) for p in xy]


def _box(xy: Sequence[Any]) -> tuple[float, float, float, float]:
    if len(xy) == 2:
        (x0, y0), (x1, y1) = xy
    else:
        x0, y0, x1, y1 = xy
    return float(x0), float(y0), float(x1), float(y1)


def _area(pts: Sequence[Point]) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, [*pts[1:], pts[0]]))


class Rings:
    """Rings filled together as one shape (Draw.fill_rings): each outer ring adds, each hole takes away (non-zero
    winding on rings oriented by their role), so the edge two neighbouring polygons share leaves no seam (drawn one
    by one, two half-covered pixels of the same land would add to three quarters), an enclave inside its
    neighbour's hole is land in any order, and a lake stays water. `line` records a stroke drawn over the fill
    after it (the polar view's closed seams, graphics._close_seams)."""

    def __init__(self) -> None:
        self.path = skia.Path()
        self.path.setFillType(skia.PathFillType.kWinding)
        self.strokes: list[tuple[list[Point], Any, float]] = []

    def add(self, xy: Sequence[Any], hole: bool = False) -> None:
        pts = _points(xy)
        if len(pts) < 3:
            return
        if (_area(pts) < 0) != hole:          # outer rings one way round, holes the other
            pts = pts[::-1]
        self.path.addPoly([skia.Point(x, y) for x, y in pts], True)

    def oval(self, box: Sequence[float]) -> None:
        x0, y0, x1, y1 = _box(box)
        rect = skia.Rect.MakeLTRB(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.path.addOval(rect, skia.PathDirection.kCW)    # an outer ring, the way round add() turns them

    def line(self, xy: Sequence[Any], fill: Any = None, width: float = 1) -> None:
        self.strokes.append((_points(xy), fill, float(width)))


class Draw:
    """ImageDraw's shape calls, antialiased (see the module)."""

    def __init__(self, im: Image.Image, mode: str | None = None, grid: str = "pixel") -> None:
        if im.mode not in ("L", "RGB", "RGBA"):
            raise ValueError(f"raster.Draw: unsupported image mode {im.mode}")
        if grid not in ("pixel", "exact"):
            raise ValueError(f"raster.Draw: unknown grid {grid!r}")
        self.im, self.grid = im, grid
        self.blend = mode == "RGBA" and im.mode == "RGB"

    # ------------------------------------------------------------------ shapes
    def polygon(self, xy: Sequence[Any], fill: Any = None, outline: Any = None, width: int = 1) -> None:
        pts = _points(xy)
        if len(pts) < 3:
            if len(pts) == 2 and (fill is not None or outline is not None):
                self.line(pts, fill=outline if outline is not None else fill, width=width)
            return
        path = skia.Path()
        path.addPoly([skia.Point(x, y) for x, y in pts], True)
        self._shape(path, fill, outline, width)

    def rectangle(self, xy: Sequence[Any], fill: Any = None, outline: Any = None, width: int = 1) -> None:
        x0, y0, x1, y1 = self._rect(xy)
        if x1 <= x0 or y1 <= y0:
            return
        path = skia.Path()
        path.addRect(skia.Rect.MakeLTRB(x0, y0, x1, y1))
        self._shape(path, fill, outline, width)

    def ellipse(self, xy: Sequence[Any], fill: Any = None, outline: Any = None, width: int = 1) -> None:
        x0, y0, x1, y1 = self._rect(xy)
        if x1 <= x0 or y1 <= y0:
            return
        path = skia.Path()
        path.addOval(skia.Rect.MakeLTRB(x0, y0, x1, y1))
        self._shape(path, fill, outline, width)

    def line(self, xy: Sequence[Any], fill: Any = None, width: float = 0, joint: str | None = None) -> None:
        self.lines([xy], fill=fill, width=width, joint=joint)

    def lines(self, runs: Sequence[Sequence[Any]], fill: Any = None, width: float = 0,
              joint: str | None = None) -> None:
        """Several lines of one ink and width stroked as one shape (where they cross they are one stroke, not two
        laid over each other), in one pass over the image."""
        if fill is None:
            return
        w = float(width) if width else 1.0
        path = skia.Path()
        for xy in runs:
            self._line_path(path, _points(xy), w, joint)
        if not path.countPoints():
            return
        paint = skia.Paint(AntiAlias=True, Color=skia.ColorWHITE, Style=skia.Paint.kStroke_Style, StrokeWidth=w,
                           StrokeCap=skia.Paint.kButt_Cap,
                           StrokeJoin=skia.Paint.kRound_Join if joint == "curve" else skia.Paint.kMiter_Join)
        self._paint_path(path, fill, paint, outset=2 * w + 1)

    def _line_path(self, path: skia.Path, pts: list[Point], w: float, joint: str | None) -> None:
        if len(pts) < 2:
            return
        if joint == "curve":      # one stroke with round joins (closed when it ends where it starts)
            closed = len(pts) > 2 and pts[0] == pts[-1]
            if closed:
                pts = pts[:-1]
            q = [self._shift(p, w, pts, i) for i, p in enumerate(pts)]
            if not closed and self.grid == "pixel":   # the ends half a pixel past the end points, as ImageDraw
                q[0], q[-1] = self._extend(q[0], q[1]), self._extend(q[-1], q[-2])
            path.addPoly([skia.Point(x, y) for x, y in q], closed)
        else:                     # ImageDraw without a joint strokes each segment on its own (no join at a corner)
            for a, b in itertools.pairwise(pts):
                pa, pb = self._segment(a, b, w)
                if pa != pb:
                    path.moveTo(*pa)
                    path.lineTo(*pb)

    def fill_rings(self, rings: Rings, fill: Any) -> None:
        """`rings` filled with `fill` as one shape (see Rings), then its recorded strokes."""
        if rings.path.countPoints():
            self._paint_path(rings.path, fill)
        for pts, ink, width in rings.strokes:
            self.line(pts, fill=fill if ink is None else ink, width=width)

    # ------------------------------------------------------------------ geometry on the grid
    def _rect(self, xy: Sequence[Any]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = _box(xy)
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        if self.grid == "pixel":
            return x0, y0, x1 + 1, y1 + 1
        return x0, y0, x1, y1

    def _across(self, a: Point, b: Point, w: float) -> Point:
        """The extra half pixel ImageDraw gives an even-width line, across it on the side it turns to: +y for a
        level line drawn left to right (-y right to left), +x for an upright one drawn downwards (-x upwards),
        shared by angle between."""
        if self.grid != "pixel" or round(w) % 2:
            return 0.0, 0.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        return 0.5 * dy / n, 0.5 * dx / n

    def _shift(self, p: Point, w: float, pts: Sequence[Point], i: int) -> Point:
        if self.grid != "pixel":
            return p
        a, b = (pts[i], pts[i + 1]) if i + 1 < len(pts) else (pts[i - 1], pts[i])
        ex, ey = self._across(a, b, w)
        return p[0] + 0.5 + ex, p[1] + 0.5 + ey

    @staticmethod
    def _extend(p: Point, toward: Point) -> Point:
        dx, dy = p[0] - toward[0], p[1] - toward[1]
        n = math.hypot(dx, dy)
        return p if n == 0 else (p[0] + 0.5 * dx / n, p[1] + 0.5 * dy / n)

    def _segment(self, a: Point, b: Point, w: float) -> tuple[Point, Point]:
        if self.grid != "pixel":
            return a, b
        ex, ey = self._across(a, b, w)
        pa, pb = (a[0] + 0.5 + ex, a[1] + 0.5 + ey), (b[0] + 0.5 + ex, b[1] + 0.5 + ey)
        if pa == pb:
            return pa, pb
        return self._extend(pa, pb), self._extend(pb, pa)

    # ------------------------------------------------------------------ coverage and paint
    def _shape(self, path: skia.Path, fill: Any, outline: Any, width: int) -> None:
        if fill is not None:
            self._paint_path(path, fill)
        if outline is not None and width and outline != fill:      # ImageDraw draws no outline in the fill's ink
            # ImageDraw draws a shape's outline inside it: the band `width` px deep from the edge
            stroke = skia.Paint(AntiAlias=True, Color=skia.ColorWHITE, Style=skia.Paint.kStroke_Style,
                                StrokeWidth=2.0 * float(width), StrokeJoin=skia.Paint.kMiter_Join)
            self._paint_path(path, outline, stroke, clip=path, outset=width)

    def _paint_path(self, path: skia.Path, ink: Any, paint: skia.Paint | None = None, clip: skia.Path | None = None,
                    outset: float = 0.0) -> None:
        """`path` (filled, or drawn with `paint`) laid on the image in `ink` by its coverage, BAND rows a pass."""
        if ink is None:
            return
        b = path.computeTightBounds()
        x0 = max(0, math.floor(b.left() - outset) - 1)
        y0 = max(0, math.floor(b.top() - outset) - 1)
        x1 = min(self.im.width, math.ceil(b.right() + outset) + 1)
        y1 = min(self.im.height, math.ceil(b.bottom() + outset) + 1)
        for top in range(y0, y1, BAND):
            self._paint(self._coverage(path, paint, clip, x0, top, x1 - x0, min(BAND, y1 - top)), ink)

    @staticmethod
    def _coverage(path: skia.Path, paint: skia.Paint | None, clip: skia.Path | None, x0: int, y0: int, w: int,
                  h: int) -> tuple[int, int, np.ndarray, np.ndarray, np.ndarray] | None:
        """The shape's coverage of the w x h pixels at (x0, y0), as the pixels it touches: (x0, y0, their rows,
        their columns, their 0-1 coverage), or None when it covers none of them."""
        if w <= 0 or h <= 0:
            return None
        cov = np.zeros((h * VERTICAL_SS, w), np.uint8)
        surface = skia.Surface.MakeRasterDirect(skia.ImageInfo.MakeA8(w, h * VERTICAL_SS), cov, w)
        canvas = surface.getCanvas()
        canvas.scale(1.0, float(VERTICAL_SS))
        canvas.translate(-x0, -y0)
        if clip is not None:
            canvas.clipPath(clip, skia.ClipOp.kIntersect, True)
        canvas.drawPath(path, paint if paint is not None else skia.Paint(AntiAlias=True, Color=skia.ColorWHITE))
        del canvas, surface
        total = cov.reshape(h, VERTICAL_SS, w).sum(axis=1, dtype=np.uint16)
        ys, xs = np.nonzero(total)
        if not len(ys):
            return None
        return x0, y0, ys, xs, total[ys, xs] / (255.0 * VERTICAL_SS)

    def _paint(self, cov: tuple[int, int, np.ndarray, np.ndarray, np.ndarray] | None, ink: Any) -> None:
        """`ink` laid on the pixels `cov` touches by their coverage (only those pixels are read and written)."""
        if cov is None or ink is None:
            return
        x0, y0, ys, xs, share = cov
        box = (x0, y0, x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)
        region = np.asarray(self.im.crop(box)).copy()
        dst = region[ys, xs].astype(np.float64)
        c = share[:, None] if dst.ndim == 2 else share
        if self.im.mode == "L":
            v = float(ink if not isinstance(ink, (tuple, list)) else ink[0])
            out = dst + (v - dst) * c
        else:
            ink = (ink, ink, ink) if isinstance(ink, (int, float)) else tuple(ink)
            rgb = np.array(ink[:3], dtype=np.float64)
            alpha = float(ink[3]) if len(ink) > 3 else 255.0
            if self.blend:                                  # RGB image, "RGBA" draw: the ink's alpha blends
                out = dst + (rgb - dst) * (c * alpha / 255.0)
            elif self.im.mode == "RGB":
                out = dst + (rgb - dst) * c
            else:                                           # RGBA replace, on premultiplied colour
                da = dst[:, 3:4] / 255.0
                oa = da + (alpha / 255.0 - da) * c
                prem = dst[:, :3] * da + (rgb * alpha / 255.0 - dst[:, :3] * da) * c
                out_rgb = np.where(oa > 0, prem / np.maximum(oa, 1e-12), dst[:, :3])
                out = np.concatenate([out_rgb, oa * 255.0], axis=-1)
        region[ys, xs] = np.clip(np.floor(out + 0.5), 0, 255).astype(np.uint8)
        self.im.paste(Image.fromarray(region, self.im.mode), box[:2])
