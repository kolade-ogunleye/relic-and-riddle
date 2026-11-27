"""Where is a photograph's subject? A derived subject box, kept as data beside the photograph (design-freeze cycle 6,
SUBJECT stream).

The photo system knew a subject only where a person had reviewed it (data/cover_art_direction.json, bound to the
photograph's bytes: cover_design.reviewed_focus). One photograph of the 353 in the dev pool had one, so every window
rule of cycle 5 was made safe WITHOUT a subject: salience found an aircraft's wing, a Soyuz and a fountain as often as
the subject, and a rule built on it cut subjects. This module adds a DERIVED box, measured, and lets it stand for a
subject only where it was measured safe:

- one record per photograph, `<photo>.subject.json` beside it (the lineage / audit sidecar convention), bound to the
  photograph's SHA-256 and to this module's VERSION, made once and read thereafter (deterministic: the same bytes give
  the same record; CPU inference);
- the record holds the box (0-1 of the whole EXIF-turned picture), the method, the models and their SHA-256, the
  confidence and `reviewed: false`; a REVIEWED box (cover_design.reviewed_focus) always takes precedence and is never
  overwritten;
- `subject_focus(path)` is what the photo system reads: the reviewed box, else the derived box when its confidence is
  at least ACCEPT, else None, in which case every caller behaves exactly as before (no box).

No topic, no photo list and no landmark is named here: the method reads pixels (salient-object segmentation by
independent models, and how far they agree), so it holds for any niche. Where the models are not installed
(tools/models/subject/), nothing is derived and nothing changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VERSION = 1
SIDECAR = ".subject.json"
_LOCK = threading.Lock()


def sidecar(path: Path) -> Path:
    return Path(str(path) + SIDECAR)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def read(path: Path, digest: str | None = None) -> dict[str, Any] | None:
    """The derived record of the photograph at `path` when one exists for these bytes and this VERSION, else None."""
    f = sidecar(path)
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
        digest = digest or _sha256(Path(path))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("sha256") != digest or rec.get("version") != VERSION:
        return None
    return rec


def write(path: Path, rec: dict[str, Any]) -> None:
    """Writes the record atomically (a parallel render reads a whole record or none)."""
    f = sidecar(path)
    fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix=f.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            json.dump(rec, out, indent=1, sort_keys=True)
        os.replace(tmp, f)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---- the method: salient-object segmentation by independent models, and how far they agree
MODEL_DIR = Path(__file__).resolve().parents[1] / "tools" / "models" / "subject"
# (name, file, input side, mean, std, sigmoid): the rembg ONNX exports of U2-Net (Apache-2.0) and IS-Net (DIS,
# Apache-2.0); docs/reviews/freeze-system-2026-11-15/corpus/subject/SOURCES.md has their URLs and SHA-256
MODELS = (
    ("u2net", "u2net.onnx", 320, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225), False),
    ("isnet", "isnet-general-use.onnx", 1024, (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), False),
)
MASK_T = 0.5          # a mask pixel at or over this is the object
KEEP_COMPONENT = 0.05  # of the mask's foreground: a smaller component is a speck, not part of the subject
THUMB = 1280          # px: the picture the models read (the longer side)
# Under ACCEPT no derived box stands for a subject: the photo system behaves exactly as without one. Set from the
# measured error on the labelled set (corpus/subject/README.md): no confidence of this method separated the boxes
# that cut a labelled subject from those that do not (confident, crisp, agreed boxes still took a dome's oculus, a
# map's red zone, a tourist for the photograph's subject), so nothing derived is accepted: the boxes are RECORDED
# (an instrument), not consumed.
ACCEPT = 1.01
_sessions: dict[str, Any] = {}


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return all((MODEL_DIR / f).is_file() for _, f, *_ in MODELS)


def _session(name: str, file: str):
    with _LOCK:
        if name not in _sessions:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = min(6, os.cpu_count() or 1)
            _sessions[name] = ort.InferenceSession(str(MODEL_DIR / file), so, providers=["CPUExecutionProvider"])
        return _sessions[name]


def _mask(image, spec):
    """One model's salient-object mask (0-1) at 320 px square, read on `image` (RGB, any size)."""
    import numpy as np
    from PIL import Image
    name, file, side, mean, std, sigmoid = spec
    a = np.asarray(image.resize((side, side), Image.LANCZOS), dtype=np.float32)
    a = a / max(float(a.max()), 1e-6)
    a = (a - np.array(mean, np.float32)) / np.array(std, np.float32)
    s = _session(name, file)
    y = s.run(None, {s.get_inputs()[0].name: a.transpose(2, 0, 1)[None].astype(np.float32)})[0][0, 0]
    if sigmoid:
        y = 1 / (1 + np.exp(-y))
    y = (y - y.min()) / max(float(y.max() - y.min()), 1e-6)
    return np.asarray(Image.fromarray((y * 255).astype(np.uint8)).resize((320, 320), Image.BILINEAR),
                      np.float32) / 255


def mask_box(m) -> tuple[list[float] | None, dict[str, float]]:
    """(box 0-1, measures) of a mask: the union of its components holding KEEP_COMPONENT of its foreground."""
    import cv2
    import numpy as np
    fg = (m >= MASK_T).astype(np.uint8)
    area = int(fg.sum())
    feats = {"fg": round(area / fg.size, 4)}
    if area == 0:
        return None, feats
    n, _lab, st, _ = cv2.connectedComponentsWithStats(fg, 8)
    comps = [(int(st[i, 4]), st[i]) for i in range(1, n)]
    kept = [s for a, s in comps if a >= KEEP_COMPONENT * area] or [max(comps, key=lambda c: c[0])[1]]
    H, W = m.shape
    x0 = int(min(s[0] for s in kept)); y0 = int(min(s[1] for s in kept))
    x1 = int(max(s[0] + s[2] for s in kept)); y1 = int(max(s[1] + s[3] for s in kept))
    soft = float(((m > 0.1) & (m < 0.9)).sum() / max(1, int((m > 0.1).sum())))
    feats.update(soft=round(soft, 4), components=len(kept))
    return [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)], feats


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 1e-12 else 0.0


def derive(image) -> dict[str, Any]:
    """The derived subject of a picture (PIL, the whole EXIF-turned photograph): the union of the models' boxes, and
    its confidence: the least agreement (IoU) between them, times the crispness of the least crisp mask."""
    image = image.convert("RGB")
    image.thumbnail((THUMB, THUMB))
    boxes, measures = {}, {}
    for spec in MODELS:
        b, f = mask_box(_mask(image, spec))
        boxes[spec[0]] = b
        measures[spec[0]] = {**f, "box": b}
    got = [b for b in boxes.values() if b]
    if len(got) < len(MODELS):
        return {"box": None, "confidence": 0.0, "measures": measures}
    box = [min(b[0] for b in got), min(b[1] for b in got), max(b[2] for b in got), max(b[3] for b in got)]
    agree = min(_iou(a, b) for i, a in enumerate(got) for b in got[i + 1:])
    crisp = 1.0 - max(measures[n]["soft"] for n in boxes)
    return {"box": box, "confidence": round(agree * crisp, 4), "measures": measures}


def model_ids() -> list[dict[str, str]]:
    return [{"name": n, "file": f, "sha256": _sha256(MODEL_DIR / f) if (MODEL_DIR / f).is_file() else ""}
            for n, f, *_ in MODELS]


def record(path: Path, compute: bool = True) -> dict[str, Any] | None:
    """The derived record of the photograph at `path`: read from its sidecar, else (with `compute` and the models
    installed) derived once and written beside it. None when there is none."""
    path = Path(path)
    if not compute and not sidecar(path).is_file():
        return None                                       # nothing recorded: no second read of the photograph
    try:
        digest = _sha256(path)
    except OSError:
        return None
    rec = read(path, digest)
    if rec is not None or not compute or not available():
        return rec
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im.draft("RGB", (2 * THUMB, 2 * THUMB))
            whole = ImageOps.exif_transpose(im).convert("RGB")
        got = derive(whole)
    except Exception:                                      # a photograph the models cannot read has no box
        log.exception("subject: %s could not be derived", path.name)
        return None
    rec = {"version": VERSION, "sha256": digest, "box": got["box"], "confidence": got["confidence"],
           "method": "salient-object segmentation, models' union; confidence = least pairwise IoU x crispness",
           "models": model_ids(), "measures": got["measures"], "reviewed": False}
    try:
        write(path, rec)
    except OSError:
        log.warning("subject: no sidecar written beside %s", path.name)
    return rec


# A labelled set of boxes (sha256 -> 0-1 box) fed as the subject by a test or a corpus script only (use_oracle): how
# the window rules behave with a CORRECT subject, whatever source later supplies one. Never set in production.
_oracle: dict[str, list[float]] = {}


def use_oracle(boxes: dict[str, list[float]] | None) -> None:
    """Feeds `boxes` (sha256 -> 0-1 box on the whole picture) as subjects (tests and corpus scripts); None clears."""
    _oracle.clear()
    _oracle.update(boxes or {})


def subject_focus(path: Path, kept=None, compute: bool = False) -> dict[str, Any] | None:
    """The subject the photo system may use, in cover_design.reviewed_focus's shape ({"focus_box", "sha256",
    "reviewed", "source"}): the REVIEWED box when the photograph has one (always first), else the derived box when its
    confidence is at least ACCEPT, else None (every caller then keeps its rule without a subject)."""
    from .cover_design import reviewed_focus
    found = reviewed_focus(Path(path), kept)
    if found is not None:
        return found                                      # the reviewed record as it is (its own provenance)
    if _oracle:
        try:
            digest = _sha256(Path(path))
        except OSError:
            digest = ""
        if digest in _oracle:
            return {"focus_box": list(_oracle[digest]), "sha256": digest, "reviewed": "", "source": "oracle"}
    rec = record(Path(path), compute=compute)
    if rec is None or not rec.get("box") or float(rec.get("confidence") or 0.0) < ACCEPT:
        return None
    return {"focus_box": list(rec["box"]), "sha256": rec["sha256"], "reviewed": "",
            "source": "derived", "confidence": rec["confidence"]}
