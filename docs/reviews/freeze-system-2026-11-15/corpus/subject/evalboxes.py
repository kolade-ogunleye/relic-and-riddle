"""Boxes from each candidate's raw output; evaluation against the builder's labelled set."""
import json, sys
from pathlib import Path
import numpy as np, cv2
from PIL import Image
HERE = Path(__file__).resolve().parent
SEL = {o["id"]: o for o in json.load(open(HERE / "gt_select.json"))}
LAB = [json.loads(l) for l in open(HERE / "labels.jsonl")]

def mask(model, sha):
    f = HERE / "raw" / model / (sha[:20] + ".png")
    return np.asarray(Image.open(f), np.float32) / 255 if f.is_file() else None

def seg_box(m, t=0.5, keep=0.05):
    """(box, feats): the union of the mask's components holding >= keep of its foreground."""
    fg = (m >= t).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg, 8)
    H, W = m.shape
    area = fg.sum()
    feats = {"fg": float(area / fg.size)}
    if area == 0:
        return None, feats
    comps = [(st[i, 4], st[i]) for i in range(1, n)]
    big = max(a for a, _ in comps)
    kept = [s for a, s in comps if a >= keep * area] or [max(comps, key=lambda c: c[0])[1]]
    x0 = min(s[0] for s in kept); y0 = min(s[1] for s in kept)
    x1 = max(s[0] + s[2] for s in kept); y1 = max(s[1] + s[3] for s in kept)
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    box = [x0 / W, y0 / H, x1 / W, y1 / H]
    soft = ((m > 0.1) & (m < 0.9)).sum() / max(1, (m > 0.1).sum())
    inside = m[y0:y1, x0:x1]
    feats.update(soft=float(soft), ncomp=int(len(kept)), big=float(big / area),
                 fill=float((inside >= t).mean()), mean_in=float(inside.mean()),
                 mean_out=float((m.sum() - inside.sum()) / max(1, m.size - inside.size)),
                 lost=float(1 - sum(a for a, s in comps if a >= keep * area) / area))
    return box, feats

def area(b): return max(0, b[2] - b[0]) * max(0, b[3] - b[1])
def inter(a, b): return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
def iou(a, b):
    i = inter(a, b); return i / max(1e-9, area(a) + area(b) - i)
def cover(gt, p): return inter(gt, p) / max(1e-9, area(gt))   # share of the subject inside the predicted box
def grow(b, m):
    w, h = b[2] - b[0], b[3] - b[1]
    return [max(0, b[0] - m * w), max(0, b[1] - m * h), min(1, b[2] + m * w), min(1, b[3] + m * h)]
def union(*bs):
    bs = [b for b in bs if b]
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)] if bs else None
