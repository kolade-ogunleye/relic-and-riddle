"""Shared by the PLATES corpus proofs (freeze cycle 5): the tree under test and the dev checkout on the path, the
photographs of every dev run's topic, and the measures (band alpha read back from a before | after pair, the slope a
band itself puts in, the ground's own detail, hue and saturation along a grade-out). Cheap: layout calls and image
statistics on stills, no clip is rendered.

    python plates_<class>.py <tree> <dev checkout> [--per-topic N] [--json out.json]

<tree>: the commit measured (base or tip; the scripts feature-detect what a base tree lacks). <dev checkout>: read
only (output/run_*, assets/<topic>/*.jpg). Exit 1 on a violation that is neither fixed nor recorded.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

import numpy as np
from PIL import Image

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def setup(doc: str, extra=None):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("--per-topic", type=int, default=0, help="at most N photographs per topic (0: all)")
    ap.add_argument("--json", type=Path)
    if extra:
        extra(ap)
    a = ap.parse_args()
    tree, dev = a.tree.resolve(), a.dev.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    return a, tree, dev


def runs(dev: Path) -> list[Path]:
    return sorted(p for p in (dev / "output").glob("run_*") if p.is_dir())


def topic_of(run: Path, topics: list[str]) -> str | None:
    name = run.name[4:]
    hit = [t for t in topics if t in name]
    return max(hit, key=len) if hit else None


def corpus_photos(dev: Path, per_topic: int = 0) -> list[tuple[str, int, Path]]:
    """(topic, the number of dev runs of that topic, photo) for every photograph of every topic a dev run exists
    for."""
    topics = sorted(p.name for p in (dev / "assets").iterdir() if p.is_dir())
    count: dict[str, int] = {}
    for r in runs(dev):
        t = topic_of(r, topics)
        if t:
            count[t] = count.get(t, 0) + 1
    out = []
    for t, n in sorted(count.items()):
        photos = sorted(p for p in (dev / "assets" / t).glob("*.jpg") if "contact_sheet" not in p.name)   # photographs only
        for p in (photos[:per_topic] if per_topic else photos):
            out.append((t, n, p))
    return out


def luma(im) -> np.ndarray:
    return np.asarray(im.convert("RGB") if isinstance(im, Image.Image) else im, np.float32)[..., :3] @ LUMA


def _smooth(v: np.ndarray, n: int) -> np.ndarray:
    n = max(1, min(int(n), len(v)))
    return np.convolve(np.pad(v, (n // 2, n - 1 - n // 2), mode="edge"), np.ones(n) / n, mode="valid")


def texture(lb: np.ndarray, a: int, b: int, width: int, blocks: int = 16, pct: float = 25) -> float:
    """The ground's own detail over rows a..b (the rule's statement, written again here, not imported): per column
    block the mean absolute luma change row to row; the lower quartile of the blocks; luma per px at 1080 wide."""
    if b - a < 2:
        return 0.0
    d = np.abs(np.diff(lb[a:b], axis=0)).mean(axis=0)
    e = np.linspace(0, lb.shape[1], blocks + 1).astype(int)
    return float(np.percentile([d[e[i]:e[i + 1]].mean() for i in range(blocks)], pct)) * width / 1080.0


def band_edges(before, after, toward, floating: bool, width: int | None = None, transpose: bool = False,
               gain: float = 2.0, gate: float = 0.5, ceiling: tuple[float, float] = (2.0, 1.0)) -> list[dict]:
    """Every edge of a band read back from the pixels: the band's alpha per row (the median over the columns of
    (before - after) / (before - toward)), and per eased run of rows the steepest luma change the band itself puts in
    (|d alpha| x (the row's 95th percentile ground luma - the band's), luma per px at 1080 wide) against the limit
    the rule gives that ground: gain x texture, between the field gate and the ceiling (ground band, floating band);
    a row passes at the limit itself, no read-back allowance.
    `width`: the canvas width the crop came from (the slope is stated at 1080 wide)."""
    lb, la = luma(before), luma(after)
    if isinstance(toward, Image.Image):
        lt = luma(toward)
    else:
        lt = np.full_like(lb, float(np.dot(np.asarray(toward[:3], np.float32), LUMA)))
    if transpose:
        lb, la, lt = lb.T, la.T, lt.T
    width = before.width if width is None else width
    k = 1080.0 / width
    den = lb - lt
    ok = np.abs(den) > 24
    n = ok.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        al = np.where(ok, (lb - la) / np.where(ok, den, 1.0), np.nan)
    good = n >= max(8, 0.05 * lb.shape[1])
    if good.sum() < 8:
        return []
    alpha = np.full(lb.shape[0], np.nan)
    alpha[good] = np.nanmedian(al[good], axis=1)
    idx = np.arange(len(alpha))
    alpha = _smooth(np.clip(np.interp(idx, idx[good], alpha[good]), 0, 1), 5)
    step = _smooth(np.maximum(np.percentile(lb, 95, axis=1) - np.median(lt, axis=1), 0), 9)
    d = np.abs(np.gradient(alpha))
    slope = d * step / k
    moving = d > 2e-4
    out, i = [], 0
    while i < len(moving):
        if not moving[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(moving) and moving[j + 1:j + 4].any():
            j += 1
        if j - i >= 8 and alpha[i:j + 1].max() - alpha[i:j + 1].min() > 0.05:
            y = i + int(slope[i:j + 1].argmax())
            limit = min(ceiling[1 if floating else 0], max(gate, gain * texture(lb, i, j + 1, width)))
            out.append({"rows": [i, j], "slope": round(float(slope[y]), 2), "at": y, "limit": round(limit, 2),
                        "ok": bool(slope[y] <= limit)})   # the rule's own limit (review of the c5 fix, D6)
        i = j + 1
    return out


def _hue_sat(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(hue in degrees, HSV saturation 0-1) of float RGB, unquantised."""
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    c = np.maximum(mx - mn, 1e-6)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    h = np.where(mx == r, ((g - b) / c) % 6, np.where(mx == g, (b - r) / c + 2, (r - g) / c + 4)) * 60.0
    return h, (mx - mn) / np.maximum(mx, 1e-6)


def hue_sat(before, after, alpha: np.ndarray, full: float, upto: float | None = 0.8, low: float = 0.1,
            chroma: float = 0.0):
    """Along a grade-out PATH, to the ledger's own bound (PLATES-02: "until luma < 20"; review of the c5 fix, D4: the
    window 0.1-0.8 of full missed the drift just above it): pixels where the layer's alpha is at least `low` of its
    `full` strength and at most `upto` of it (0.99: up to the layer's body, which is INK by ruling R2; None: the
    body too), the composite still lit >= 20 luma, the ground saturated
    >= 0.25 and lit >= 60: the hue shift (degrees) and the HSV saturation kept (after / before): (median |hue|, 95th
    percentile |hue|, 5th percentile saturation ratio, pixels); None when the ground has no such pixels. `chroma`:
    only composite pixels whose 8-bit chroma (max - min channel) is at least this (12: an 8-bit pixel with less
    cannot hold a hue to within the 5 degree rule; its rounding alone moves it 2.5 degrees or more)."""
    b = np.asarray(before.convert("RGB"), np.float32)
    a = np.asarray(after.convert("RGB"), np.float32)
    hb, sb = _hue_sat(b)
    ha, sa = _hue_sat(a)
    sel = (alpha >= low * full) & (a @ LUMA >= 20) & (sb >= 0.25) & (b @ LUMA >= 60)
    if upto is not None:
        sel &= alpha <= upto * full
    if chroma:
        sel &= (a.max(axis=-1) - a.min(axis=-1)) >= chroma
    if sel.sum() < 500:
        return None
    dh = np.abs((ha - hb + 180.0) % 360.0 - 180.0)[sel]
    ratio = sa[sel] / np.maximum(sb[sel], 1e-3)
    return (round(float(np.median(dh)), 1), round(float(np.percentile(dh, 95)), 1),
            round(float(np.percentile(ratio, 5)), 2), int(sel.sum()))


def finish(a, name: str, rows: list[dict], bad: list[dict], summary: dict) -> None:
    print(f"\n{name}: {len(rows)} rows, {len(bad)} violations")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    if a.json:
        a.json.write_text(json.dumps({"class": name, "summary": summary, "violations": len(bad), "rows": rows},
                                     indent=1), encoding="utf-8", newline="\n")
    sys.exit(1 if bad else 0)
