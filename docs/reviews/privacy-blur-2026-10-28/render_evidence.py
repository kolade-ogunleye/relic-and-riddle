"""Evidence renders for docs/reviews/privacy-blur-2026-10-28 (no model calls: boxes are recovered from the old
cached blurs, then the new geometry and blur are applied to the same boxes)."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
from src import privacy  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)


def half(path, img):
    h, w = img.shape[:2]
    cv2.imwrite(str(path), cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 85])


def changed_regions(a, b, thresh=12):
    """The blurred areas of `b`: touching ellipses (a crowd) are split by eroding each blob harder until its
    parts are taller than wide (the old blur's ellipses were), then grown back by the erosion."""
    d = (np.abs(a.astype(int) - b.astype(int)).max(2) > thresh).astype(np.uint8)
    d = cv2.morphologyEx(d, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    out = []

    def split(mask, ox, oy):
        for e in (21, 41, 61, 81, 101):
            er = cv2.erode(mask, np.ones((e, e), np.uint8))
            n, _, stats, _ = cv2.connectedComponentsWithStats(er)
            parts = [st for st in stats[1:] if st[4] > 100]
            if parts and all(st[3] >= st[2] * 0.9 for st in parts):
                return [(max(0, ox + int(st[0]) - e // 2), max(0, oy + int(st[1]) - e // 2), int(st[2]) + e, int(st[3]) + e)
                        for st in parts]
        h, w = mask.shape
        if w > 1.5 * h:  # a row of overlapping tall ellipses: split at the dips of the top outline
            cols = np.where(mask.any(0), mask.argmax(0), h).astype(np.float32)
            top = cv2.GaussianBlur(cols.reshape(1, -1), (0, 0), 6).ravel()
            peaks = [i for i in range(1, w - 1) if top[i] >= top[i - 1] and top[i] > top[i + 1]]
            cuts = [0] + [i for i in peaks if top[i] - top.min() > 8] + [w]
            parts = []
            for a, b in zip(cuts, cuts[1:]):
                if b - a < 15:
                    continue
                rows = np.where(mask[:, a:b].any(1))[0]
                parts.append((ox + a, oy + int(rows.min()), b - a, int(rows.max() - rows.min() + 1)))
            if len(parts) > 1:
                return parts
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        return [(ox + int(st[0]), oy + int(st[1]), int(st[2]), int(st[3])) for st in stats[1:] if st[4] > 200]

    n, lab, stats, _ = cv2.connectedComponentsWithStats(d)
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 300:
            continue
        out.extend(split((lab[y:y + h, x:x + w] == i).astype(np.uint8), x, y))
    return out, lab, stats


def unpad(region, pad=0.20):
    x, y, w, h = region
    bw, bh = int(w / (1 + 2 * pad)), int(h / (1 + 2 * pad))
    return (x + (w - bw) // 2, y + (h - bh) // 2, bw, bh)


def region_stats(orig, blurred, regions):
    """Per changed region: h/w and the pixel std inside the changed area (a flat disc has std near 0)."""
    out = []
    for (x, y, w, h) in regions:
        diff = np.abs(orig[y:y + h, x:x + w].astype(int) - blurred[y:y + h, x:x + w].astype(int)).max(2) > 12
        patch = cv2.cvtColor(blurred[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        inner = cv2.erode(diff.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
        out.append({"box": [x, y, w, h], "h_over_w": round(h / w, 2),
                    "std_inside": round(float(patch[inner].std()) if inner.any() else 0.0, 2),
                    # texture: what is left after removing the gradients (a flat disc has none)
                    "texture_std": round(float((patch.astype(np.float32) - cv2.GaussianBlur(patch.astype(np.float32), (0, 0), 3))[inner].std()) if inner.any() else 0.0, 2)})
    return out


def box_stats(img, boxes, W, H):
    """Per blurred box, as the blur pads it: h/w of the blurred area, and the pixel std and texture std
    inside the inner ellipse (a flat disc has std near 0)."""
    out = []
    for (x, y, w, h) in boxes:
        px = max(10, int(w * 0.35)) if min(w, h) < 60 else int(w * 0.3)
        py = max(12, int(h * 0.40)) if min(w, h) < 60 else int(h * 0.3)
        x1, y1, x2, y2 = max(0, x - px), max(0, y - py), min(W, x + w + px), min(H, y + h + py)
        patch = cv2.cvtColor(img[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
        inner = np.zeros(patch.shape, np.uint8)
        cv2.ellipse(inner, ((x2 - x1) // 2, (y2 - y1) // 2), (max(1, w // 3), max(1, h // 3)), 0, 0, 360, 1, -1)
        inner = inner > 0
        out.append({"box": [x, y, w, h], "blurred_h_over_w": round((y2 - y1) / (x2 - x1), 2),
                    "std_inside": round(float(patch[inner].std()), 2),
                    "texture_std": round(float((patch - cv2.GaussianBlur(patch, (0, 0), 3))[inner].std()), 2)})
    return out


def laplacian_energy(gray):
    return float(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F).var())


report = {}
cases = {
    "amber_room_08": (ROOT / "assets/amber_room_heist/08_commons.jpg", ROOT / "cache/privacy/08_commons_ea5b1f7419ffa9ee.jpg"),
    "roman_concrete_19": (ROOT / "assets/roman_self_healing_concrete/19_commons.jpg", ROOT / "cache/privacy/19_commons_2e369ae7f2cb042e.jpg"),
}
for name, (src, old) in cases.items():
    if not (src.exists() and old.exists()):
        report[name] = "not available locally"
        continue
    orig, before = cv2.imread(str(src)), cv2.imread(str(old))
    H, W = orig.shape[:2]
    regions, _, _ = changed_regions(orig, before)
    boxes = [unpad(r) for r in regions]
    # The pipeline's own merge: the recovered boxes stand in for Gemini's answer, YuNet runs locally, and the
    # verifier (a model call, not made here) is taken to say yes to every candidate: the worst case for looks.
    privacy._detect_faces_cognitive_gemini = lambda im, _b=boxes: list(_b)
    # On the Amber Room photo YuNet's extra candidates are gilded busts and cherubs: the verifier is taken to
    # refuse those (as its prompt now names them) and to accept only the old box, to show how that blur looks.
    if name == "amber_room_08":
        privacy._is_living_human_candidate = lambda im, b, _r=boxes: any(abs(b[0] - r[0]) < 40 and abs(b[1] - r[1]) < 40 for r in _r)
    else:
        privacy._is_living_human_candidate = lambda im, b: True
    shaped = privacy.detect_human_faces(orig)
    after = privacy.blur_faces_on_image(orig, shaped)
    new_regions, _, _ = changed_regions(orig, after)
    report[name] = {
        "image": [W, H], "recovered_boxes": boxes, "after_face_geometry": shaped,
        "before": region_stats(orig, before, regions),
        "after": box_stats(after, shaped, W, H),
        "before_boxes": box_stats(before, [b for b in boxes], W, H),
    }
    half(OUT / f"{name}_before.jpg", before)
    half(OUT / f"{name}_after.jpg", after)
    # Zoom on the blurred area, 1:1 crop saved at half size
    xs = [r[0] for r in regions] + [r[0] + r[2] for r in regions]
    ys = [r[1] for r in regions] + [r[1] + r[3] for r in regions]
    m = 250
    x1, y1, x2, y2 = max(0, min(xs) - m), max(0, min(ys) - m), min(W, max(xs) + m), min(H, max(ys) + m)
    for tag, im in (("original", orig), ("before", before), ("after", after)):
        half(OUT / f"{name}_zoom_{tag}.jpg", im[y1:y2, x1:x2])

# Synthetic face: detail must be destroyed.
rng = np.random.default_rng(3)
img = np.full((600, 600, 3), (60, 90, 120), np.uint8)
img = cv2.add(img, rng.integers(0, 25, img.shape, dtype=np.uint8))
cv2.ellipse(img, (300, 300), (110, 145), 0, 0, 360, (140, 165, 215), -1)
for ex in (255, 345):
    cv2.ellipse(img, (ex, 270), (24, 12), 0, 0, 360, (250, 250, 250), -1)
    cv2.circle(img, (ex, 270), 9, (40, 30, 20), -1)
cv2.line(img, (300, 280), (290, 330), (90, 110, 160), 5)
cv2.ellipse(img, (300, 370), (40, 14), 0, 0, 180, (60, 60, 150), 6)
cv2.line(img, (230, 240), (275, 235), (40, 40, 60), 6)
cv2.line(img, (325, 235), (370, 240), (40, 40, 60), 6)
box = (190, 155, 220, 290)
after = privacy.blur_faces_on_image(img, [box])
g0 = cv2.cvtColor(img[155:445, 190:410], cv2.COLOR_BGR2GRAY)
g1 = cv2.cvtColor(after[155:445, 190:410], cv2.COLOR_BGR2GRAY)
# Mid-frequency (feature-scale) energy: band-pass between sigma 2 and sigma 8.
band = lambda g: float((cv2.GaussianBlur(g.astype(np.float32), (0, 0), 2) - cv2.GaussianBlur(g.astype(np.float32), (0, 0), 8)).var())
report["synthetic_face"] = {
    "laplacian_energy_before": round(laplacian_energy(g0), 1), "laplacian_energy_after": round(laplacian_energy(g1), 1),
    "feature_band_energy_before": round(band(g0), 1), "feature_band_energy_after": round(band(g1), 1),
    "std_inside_after": round(float(g1.std()), 2),
}
cv2.imwrite(str(OUT / "synthetic_face_before.jpg"), cv2.resize(img, (300, 300), interpolation=cv2.INTER_AREA))
cv2.imwrite(str(OUT / "synthetic_face_after.jpg"), cv2.resize(after, (300, 300), interpolation=cv2.INTER_AREA))
(OUT / "measurements.json").write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
print(json.dumps(report, indent=1))
