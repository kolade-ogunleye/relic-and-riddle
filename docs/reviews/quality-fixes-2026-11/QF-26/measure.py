"""QF-26 acceptance: edge aliasing of the drawn geometry (land, borders, scale silhouettes, strata, bars and rules)
on this tree's own drawing path, and on its real renders. Prints JSON; writes nothing (renders go to a temporary
folder that is removed).

    python docs/reviews/quality-fixes-2026-11/QF-26/measure.py <repo root>

Runs on the base too (where every site draws with Pillow's ImageDraw). Needs the gitignored map data in
<repo root>/tools/geo (scripts/fetch_geodata.py) and FFmpeg for the contrast sidecars.

1. "primitives": the real geometry each family is drawn from, through the drawing call the tree's site makes
   (src/raster.py where it exists, else ImageDraw), against a reference of the same geometry: fills exactly
   (16 x 16 point samples a pixel, even-odd), strokes as ImageDraw draws them at 16x, box-reduced (its error is
   about 1/16 px):
   - land: the Natural Earth rings of five dev-run places (data/topics/topic_bank.json), projected as the map
     base projects them, filled with their holes (relief_base's land mask);
   - borders: the same rings stroked 3 px with round joins (relief_base's border line);
   - silhouettes: the reference landmark each dev-run scale fact picks (pick_reference), drawn by the tree's
     _silhouette into a recorder and replayed as one mask (its polygons; the few ellipses are left out).
   Edge band: the pixels within one of a partly covered reference pixel. Metrics: RMSE there, the area bias in
   pixels and the count of grey levels drawn there (2 = aliased).
2. "frames": the last frame of real designs rendered by the tree from committed inputs: the map (three dev-run
   places, both formats among them), the scale graphic (the two scale facts the dev runs rendered, inputs.json, a
   height and a depth, both formats) and the stat cards (two committed scripts in data/script_library, 9:16). Per
   frame:
   - stair_corners: 2 x 2 blocks of exactly two flat tones split 3 : 1 at least 40 luma apart (the corner of a
     stair-step; an antialiased slanted edge has none, a whole-pixel rectangle has four);
   - flat_colours: the colours of the pixels whose 3 x 3 neighbourhood is one colour, with their share of the
     frame (top 12): the drawn tokens, which must not change;
   - contrast: the frame's own <clip>.contrast.json minima (text drawn/graded, non-text), WCAG 1.4.11 3:1 for the
     marks: unchanged or better;
   - home_rim (maps): the home coast's GOLD_LIGHT inner edge (review 1). gold = R - B > 40, dark = luma < 45; the
     rim zone is gold within 2 px of dark. peak = median of the 5 x 5 luma maximum over the rim zone; interior =
     median luma of gold farther than 7 px; excess = peak - interior; width_px = the rim's equivalent width across
     the coast: the summed luma excess over the interior of the gold within 6 px of dark, divided by the peak excess
     and by the coast's length (that gold's count / 6). For a fixed profile it is the band's FWHM times a constant;
     a thresholded pixel count instead moves about 4 % when a 1.3 px band shifts by half a pixel. The frame's gold
     and dark classes also take in the neighbours' borders and the INK line's antialiased edge, so this figure moves
     with them too; home_edge isolates the edge itself;
   - home_edge (maps): the GOLD_LIGHT edge alone, on the map's relief base (the image relief_base returns, before
     the frame's resample), as the viewer sees it: the base drawn again with HOME_EDGE_ALPHA = 0, and the edge's
     visible share r = the projection of (base - base without edge) on (GOLD_LIGHT - base without edge), so what
     the INK line and the borders drawn over it hide is not counted. Across the coast: at every third pixel of the
     home fill's outline, r sampled every 0.1 px along the normal into the home (bilinear); per profile the peak
     and the full width at half that peak; peak and fwhm_px = their medians, fwhm_mean_px the mean; ink_px = the
     sum of r (the visible gold in whole pixels).
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SS = 16
PLACES = ["hal_saflieni_hypogeum", "kailasa_temple_monolith", "greek_fire_siphon", "unfinished_obelisk_aswan",
          "roman_self_healing_concrete"]
MAP_FRAMES = [("hal_saflieni_hypogeum", "reel"), ("kailasa_temple_monolith", "wide"), ("greek_fire_siphon", "reel"),
              ("flannan_isle_lighthouse_disappearance", "reel"), ("amber_room_heist", "wide"),
              ("odessa_catacombs", "reel"), ("camp_century_nuclear_ice_city", "reel")]   # the last a polar view
HERE = Path(__file__).resolve().parent
SCALE_FACTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))["scale_facts"]
STAT_SCRIPTS = ["unfinished_obelisk_aswan", "singing_colossi_of_memnon"]
SIZES = {"reel": (1080, 1920), "wide": (1920, 1080)}


def _tree(root: Path):
    # src has no __init__.py (a namespace package): no other tree's src may be on the path
    sys.path[:] = [p for p in sys.path if not (Path(p or ".").resolve() / "src").is_dir()]
    sys.path.insert(0, str(root))
    from src import geo, graphics
    try:
        from src import raster
    except ImportError:
        raster = None
    return geo, graphics, raster


def _draw(raster, im: Image.Image, grid: str, mode: str | None = None):
    """The drawing call the tree's site makes: src/raster.py, else ImageDraw (the base)."""
    return raster.Draw(im, mode, grid=grid) if raster is not None else ImageDraw.Draw(im, mode)


def _edge_stats(drawn: np.ndarray, ref: np.ndarray) -> dict:
    part = (ref > 0.004) & (ref < 0.996)
    band = part.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            band |= np.roll(np.roll(part, dy, 0), dx, 1)
    err = drawn[band] - ref[band]
    return {"edge_px": int(band.sum()), "edge_rmse": round(float(np.sqrt(np.mean(err ** 2))), 4),
            "area_bias_px": round(float(drawn.sum() - ref.sum()), 1),
            "edge_levels": len(np.unique(np.round(drawn[band] * 255)))}


def _fill_reference(size: tuple[int, int], polys: list) -> np.ndarray:
    """Exact fill coverage: SS x SS point samples a pixel, each polygon even-odd over its rings (outer and holes),
    the polygons painted in order (scanline crossings, so thousands of coast edges stay fast)."""
    w, h = size
    out = np.zeros((h, w))
    xs = (np.arange(w * SS) + 0.5) / SS
    for poly in polys:
        edges = np.array([(a[0], a[1], b[0], b[1]) for ring in poly for a, b in zip(ring, ring[1:] + ring[:1])
                          if a[1] != b[1]], float).reshape(-1, 4)
        if not len(edges):
            continue
        y_lo, y_hi = np.minimum(edges[:, 1], edges[:, 3]), np.maximum(edges[:, 1], edges[:, 3])
        top, bot = max(0, int(y_lo.min())), min(h, math.ceil(y_hi.max()))
        if bot <= top:
            continue
        cov = np.zeros((bot - top, w))
        for r in range((bot - top) * SS):
            y = top + (r + 0.5) / SS
            on = (y_lo <= y) & (y < y_hi)
            if not on.any():
                continue
            e = edges[on]
            xc = np.sort(e[:, 0] + (y - e[:, 1]) * (e[:, 2] - e[:, 0]) / (e[:, 3] - e[:, 1]))
            inside = (np.searchsorted(xc, xs) % 2).astype(float)
            cov[r // SS] += inside.reshape(w, SS).mean(axis=1)
        cov /= SS
        out[top:bot] = out[top:bot] * (1 - cov) + cov
    return out


def _reference(size: tuple[int, int], paint) -> np.ndarray:
    big = Image.new("L", (size[0] * SS, size[1] * SS), 0)
    paint(ImageDraw.Draw(big), SS)
    return np.asarray(big.resize(size, Image.BOX), np.float64) / 255


def _rings(geo, topic: dict, size: int = 720, span: float = 6.0):
    place = geo.locate(topic)
    lat0, lon0 = float(place["lat"]), float(place["lon"])
    kx, coslat = size / span, math.cos(math.radians(lat0))

    def project(lon: float, lat: float) -> tuple[float, float]:
        return size / 2 + (lon - lon0) * kx * coslat, size / 2 - (lat - lat0) * kx
    half = span / 2 + 1
    polys = geo.view_polygons(lon0, lat0, half / max(coslat, 0.2), half)
    return [[[project(x, y) for x, y in ring] for ring in poly] for _c, poly in polys]


class _Recorder:
    """A draw that records the filled shapes it is given (the silhouette's geometry, as the tree computes it)."""

    def __init__(self, shapes: list) -> None:
        self.shapes = shapes

    def polygon(self, xy, fill=None, outline=None, width=1):
        self.shapes.append(("polygon", [tuple(map(float, p)) for p in xy]))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self.shapes.append(("ellipse", list(xy)))

    def line(self, *a, **k):        # detail lines lie inside the filled shapes
        pass

    def rectangle(self, xy, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = (float(v) for v in xy)
        self.shapes.append(("polygon", [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))


def primitives(root: Path) -> dict:
    geo, graphics, raster = _tree(root)
    topics = {t["id"]: t for t in json.loads((root / "data/topics/topic_bank.json").read_text(encoding="utf-8"))["topics"]}
    out: dict = {"engine": "src.raster (Skia)" if raster is not None else "ImageDraw", "land": {}, "borders": {}}
    size = (720, 720)
    for key in PLACES:
        polys = _rings(geo, topics[key])

        def fill(d, k: float = 1.0, polys: list = polys) -> None:
            for poly in polys:
                d.polygon([(x * k, y * k) for x, y in poly[0]], fill=255)
                for hole in poly[1:]:
                    d.polygon([(x * k, y * k) for x, y in hole], fill=0)

        def stroke(d, k: float = 1.0, polys: list = polys) -> None:
            for poly in polys:
                pts = [(x * k, y * k) for x, y in poly[0]]
                d.line(pts + [pts[0]], fill=255, width=round(3 * k), joint="curve")
        land = Image.new("L", size, 0)
        fill(_draw(raster, land, "exact"))
        out["land"][key] = _edge_stats(np.asarray(land, np.float64) / 255, _fill_reference(size, polys))
        line = Image.new("L", size, 0)
        stroke(_draw(raster, line, "exact"))
        out["borders"][key] = _edge_stats(np.asarray(line, np.float64) / 255, _reference(size, stroke))
    out["silhouettes"] = {}
    for fact in SCALE_FACTS.values():
        feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
        label = graphics.pick_reference(feet)[0]
        shapes: list = []

        graphics._silhouette(_Recorder(shapes), label, 300.25, 690.5, 640.0, (200, 200, 200))
        polys = [s for s in shapes if s[0] == "polygon"]

        def sil(d, k: float = 1.0, polys=polys) -> None:
            for _kind, pts in polys:
                d.polygon([(x * k, y * k) for x, y in pts], fill=255)
        im = Image.new("L", (600, 720), 0)
        sil(_draw(raster, im, "pixel"))
        out["silhouettes"][label.split(" (")[0]] = _edge_stats(np.asarray(im, np.float64) / 255, _fill_reference(
            (600, 720), [[[(x, y) for x, y in pts]] for _kind, pts in polys]))
    return out


def _find(obj, key: str):
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r:
                return r
    return None


def _frame_stats(arr: np.ndarray) -> dict:
    a = arr.astype(np.int32)
    lum = a @ np.array([299, 587, 114]) / 1000.0
    # 2 x 2 blocks: three pixels within 4 of each other per channel, the fourth >= 40 luma from them
    q = [a[:-1, :-1], a[:-1, 1:], a[1:, :-1], a[1:, 1:]]
    ql = [lum[:-1, :-1], lum[:-1, 1:], lum[1:, :-1], lum[1:, 1:]]
    corners = np.zeros(lum[:-1, :-1].shape, bool)
    for odd in range(4):
        rest = [i for i in range(4) if i != odd]
        same = np.ones_like(corners)
        for i in rest:
            for j in rest:
                if i < j:
                    same &= np.abs(q[i] - q[j]).max(-1) <= 4
        far = np.ones_like(corners)
        for i in rest:
            far &= np.abs(ql[odd] - ql[i]) >= 40
        corners |= same & far
    key = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]
    flat = np.ones(key.shape, bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            flat &= np.roll(np.roll(key, dy, 0), dx, 1) == key
    vals, counts = np.unique(key[flat], return_counts=True)
    top = sorted(zip(counts.tolist(), vals.tolist()), reverse=True)[:12]
    return {"stair_corners": int(corners.sum()),
            "flat_colours": [{"rgb": [v >> 16, (v >> 8) & 255, v & 255], "share": round(c / key.size, 4)}
                             for c, v in top]}


def home_rim(arr: np.ndarray) -> dict | None:
    import cv2
    a = arr.astype(np.float64)
    lum = a @ np.array([0.299, 0.587, 0.114])
    gold = (a[..., 0] - a[..., 2]) > 40
    dark = lum < 45
    if not gold.any() or not dark.any():
        return None
    dist = cv2.distanceTransform((~dark).astype(np.uint8), cv2.DIST_L2, 5)
    zone, inner, near = gold & (dist <= 2), gold & (dist > 7), gold & (dist <= 6)
    if not zone.any() or not inner.any():
        return None
    peak5 = cv2.dilate(lum.astype(np.float32), np.ones((5, 5), np.uint8))
    peak, interior = float(np.median(peak5[zone])), float(np.median(lum[inner]))
    area = float(np.clip(lum[near] - interior, 0, None).sum())
    width = area / max(peak - interior, 1e-6) / (float(near.sum()) / 6)
    return {"peak": round(peak, 1), "interior": round(interior, 1), "excess": round(peak - interior, 1),
            "width_px": round(width, 3), "coast_len_px": round(float(near.sum()) / 6, 1)}


def home_edge(with_edge: np.ndarray, without: np.ndarray, gold: tuple[int, int, int], alpha: float) -> dict:
    import cv2
    a, b = with_edge.astype(np.float32), without.astype(np.float32)
    g = np.array(gold, np.float32) - b
    den = (g * g).sum(-1)
    r = np.where(den > 900, ((a - b) * g).sum(-1) / np.maximum(den, 1), 0).clip(0, 1).astype(np.float32)
    # the home's coast: the outline of its warm fill on the base without the edge
    home = cv2.morphologyEx(((b[..., 0] - b[..., 2]) > 40).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(home, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    ts = np.arange(-3.0, 9.0, 0.1, dtype=np.float32)
    peaks, widths = [], []
    for c in contours:
        c = c[:, 0, :].astype(np.float32)
        n = len(c)
        if n < 40:
            continue
        for i in range(0, n, 3):
            t = c[(i + 4) % n] - c[(i - 4) % n]
            ln = float(np.hypot(*t))
            if ln < 4:
                continue
            nx, ny = -t[1] / ln, t[0] / ln
            qx, qy = round(float(c[i][0] + 3 * nx)), round(float(c[i][1] + 3 * ny))
            if not (0 <= qy < home.shape[0] and 0 <= qx < home.shape[1]):
                continue
            if not home[qy, qx]:                     # the normal into the home
                nx, ny = -nx, -ny
            prof = cv2.remap(r, (c[i][0] + ts * nx).reshape(1, -1), (c[i][1] + ts * ny).reshape(1, -1),
                             cv2.INTER_LINEAR)[0]
            k = int(prof.argmax())
            m = float(prof[k])
            if m < 0.2:                              # hidden (under a name, a border's end) or no edge here
                continue
            lo = hi = k
            while lo > 0 and prof[lo - 1] >= m / 2:
                lo -= 1
            while hi < len(prof) - 1 and prof[hi + 1] >= m / 2:
                hi += 1
            peaks.append(m)
            widths.append((hi - lo + 1) * 0.1)
    if not peaks:
        return {"peak": None}
    return {"peak": round(float(np.median(peaks)), 4), "fwhm_px": round(float(np.median(widths)), 3),
            "fwhm_mean_px": round(float(np.mean(widths)), 4), "ink_px": round(float(r.sum()), 1),
            "profiles": len(peaks)}


def _contrast(record: dict | None) -> dict | None:
    if not record:
        return None
    el = record.get("elements") or []
    drawn = [e["drawn"] for e in el if e.get("drawn") is not None]
    graded = [e["graded"] for e in el if e.get("graded") is not None]
    nt = record.get("nontext") or []
    return {"text_drawn_min": round(min(drawn), 2) if drawn else None,
            "text_graded_min": round(min(graded), 2) if graded else None,
            "nontext": {r["name"]: round(r["graded"] if r.get("graded") is not None else r["drawn"], 2) for r in nt}}


def last_frame(root: Path, design: str, key: str, fmt: str,
               edge: dict | None = None) -> tuple[Image.Image | None, dict | None]:
    """The design's last frame as the tree renders it from the committed input (encoded as the pipeline encodes it,
    in a temporary folder), and its contrast record; given `edge` (a map), home_edge of its relief base into it."""
    geo, graphics, _raster = _tree(root)
    real_base = graphics.relief_base
    if edge is not None:
        def spy(*a, **k):
            base = real_base(*a, **k)    # the last base the render builds (it rebuilds when a name is dropped)
            alpha = graphics.HOME_EDGE_ALPHA
            graphics.HOME_EDGE_ALPHA = 0.0
            try:      # the lists relief_base fills (names drawn or deferred) are the render's: fresh ones here
                plain = real_base(*a, **{n: [] if isinstance(v, list) else v for n, v in k.items()})
            finally:
                graphics.HOME_EDGE_ALPHA = alpha
            edge.update(home_edge(np.asarray(base), np.asarray(plain), graphics.GOLD_LIGHT, alpha))
            return base
        graphics.relief_base = spy
    from src import encoding
    encoding._DETECTED_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    width, height = SIZES[fmt]
    grab: dict = {}
    real_encode = graphics._encode

    def capture(frames, out, n, width=width, height=height):
        def tee():
            for f in frames:
                grab["last"] = f
                yield f
        return real_encode(tee(), out, n, width=width, height=height)
    graphics._encode = capture
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "graphics" / f"{design}.mp4"
            if design == "map":
                topic = next(t for t in json.loads((root / "data/topics/topic_bank.json").read_text(encoding="utf-8"))["topics"]
                             if t["id"] == key)
                made = graphics.render_map(geo.locate(topic), out, width=width, height=height)
            else:
                if design == "scale":
                    made = graphics.render_scale(SCALE_FACTS[key], out, width=width, height=height)
                else:
                    vs = json.loads((root / f"data/script_library/{key}/verified_script.json").read_text(encoding="utf-8"))
                    made = graphics.render_stat_cards(_find(vs, "stat_cards"), out, None, width=width, height=height,
                                                      narration=_find(vs, "narration_script") or "")
            side = Path(str(out) + graphics.CONTRAST_SIDECAR)
            record = json.loads(side.read_text(encoding="utf-8")) if made and side.exists() else None
    finally:
        graphics._encode = real_encode
        graphics.relief_base = real_base
    return (grab["last"].convert("RGB") if "last" in grab else None), record


def render(args: tuple[str, str, str, str]) -> tuple[str, dict]:
    root_s, design, key, fmt = args
    edge: dict | None = {} if design == "map" else None
    frame, record = last_frame(Path(root_s), design, key, fmt, edge)
    name = f"{design}/{key}/{fmt}"
    if frame is None:
        return name, {"rendered": False}
    stats = _frame_stats(np.asarray(frame))
    stats["contrast"] = _contrast(record)
    if design == "map":
        stats["home_rim"] = home_rim(np.asarray(frame))
        stats["home_edge"] = edge
    return name, stats


def frames(root: Path) -> dict:
    jobs = [(str(root), "map", k, f) for k, f in MAP_FRAMES]
    jobs += [(str(root), "scale", k, f) for k in SCALE_FACTS for f in ("reel", "wide")]
    jobs += [(str(root), "numbers", k, "reel") for k in STAT_SCRIPTS]
    with ProcessPoolExecutor(max_workers=4) as pool:
        return dict(sorted(pool.map(render, jobs)))


def main(root: Path) -> dict:
    return {"primitives": primitives(root), "frames": frames(root)}


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1))
