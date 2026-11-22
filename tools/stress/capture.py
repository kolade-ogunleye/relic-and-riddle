"""What the renderers actually draw, read without changing a pixel.

- every TextPlate that is drawn (typography.TextPlate.draw): its text, font size, face, fallback, box, fill, the
  canvas size and its planned contrast; kept once per (text, box, px);
- every line a still records through still_layout.Layout.text (text drawn with ImageDraw directly);
- the frames of every clip the graphics, callouts and then-and-now would encode (the encoder is replaced by a
  collector, as QF-35a's render_surfaces.py does): the last frame is kept, nothing is encoded.

install() is idempotent; reset() starts a new case.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PLATES: dict[tuple, dict[str, Any]] = {}
LINES: list[dict[str, Any]] = []
CLIPS: dict[str, dict[str, Any]] = {}
_installed = False


def reset() -> None:
    PLATES.clear()
    LINES.clear()
    CLIPS.clear()


def _plate_row(plate, im) -> dict[str, Any]:
    font = plate.font
    return {"text": str(plate.text), "px": round(float(getattr(font, "size", 0) or 0), 1),
            "face": Path(str(getattr(font, "path", "") or "")).name,
            "fallback_for": getattr(font, "_rr_fallback_for", None),
            "box": [int(v) for v in plate.box], "fill": list(plate.fill), "canvas": list(im.size),
            "contrast": plate.contrast, "halo": plate.halo, "light": bool(plate.light),
            "_plate": plate}


def install() -> None:
    global _installed
    if _installed:
        return
    from src import still_layout, typography
    real_draw = typography.TextPlate.draw

    def draw(self, im, alpha: float = 1.0, dy: int = 0):
        out = real_draw(self, im, alpha, dy)
        if not self.empty and alpha > 0:
            key = (str(self.text), tuple(self.box), round(float(getattr(self.font, "size", 0) or 0), 1),
                   tuple(im.size))
            row = PLATES.get(key)
            if row is None:
                row = PLATES[key] = _plate_row(self, im)
                row["alpha_max"] = 0.0
            row["alpha_max"] = max(row["alpha_max"], float(alpha))
            row["dy_seen"] = row.get("dy_seen", set()) | {int(dy)}
        return out
    typography.TextPlate.draw = draw

    real_text = still_layout.Layout.text

    def text(self, d, xy, txt, font, name, anchor="la", zone=None, record=True, **kw):
        box = real_text(self, d, xy, txt, font, name, anchor, zone, record, **kw)
        LINES.append({"text": str(txt), "name": name, "px": round(float(getattr(font, "size", 0) or 0), 1),
                      "face": Path(str(getattr(font, "path", "") or "")).name, "box": [int(v) for v in box],
                      "canvas": list(self.size)})
        return box
    still_layout.Layout.text = text

    def encode(frames, out, n=None, width=None, height=None, **_k):
        rec = CLIPS.setdefault(Path(out).name, {"frames": 0, "last": None, "first": None})
        for im in frames:
            rgb = im.convert("RGB")
            if rec["first"] is None:
                rec["first"] = rgb
            rec["last"] = rgb
            rec["frames"] += 1
        if rec["last"] is not None:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            rec["last"].save(Path(out).with_suffix(".jpg"), quality=90)
            Path(out).write_bytes(b"")        # the clip's place (a renderer may check that it exists)
        return Path(out)
    from src import callouts, design_cards, graphics, then_now
    for mod in (graphics, callouts, then_now):
        if hasattr(mod, "_encode"):
            mod._encode = encode

    # the cards are pure (frame_at): their records are made from the frames they measure (the last frame, the hook's
    # frames 0 and 32), so the encoder is not fed at all; the latest frame each plan was asked for is kept
    real_frame_at = design_cards.frame_at

    def frame_at(plan, i, **k):
        im = real_frame_at(plan, i, **k)
        rec = CLIPS.setdefault(f"card:{plan.kind}", {"frames": int(plan.frames), "last": None, "first": None, "i": -1})
        if i >= rec["i"]:
            rec["i"], rec["last"] = i, im.convert("RGB")
        return im
    design_cards.frame_at = frame_at

    def lazy(frames, out, n=None, width=None, height=None, **_k):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"")
        return Path(out)
    design_cards._encode = lazy
    _installed = True


def plates() -> list[dict[str, Any]]:
    return list(PLATES.values())


def on_image(row: dict[str, Any], im) -> bool:
    """Whether a captured plate is on the delivered image `im`: its glyph core (the mask's full-ink pixels) reads
    as its fill on at least half its pixels (a trial plate drawn and discarded, or a frame's plate that the last
    frame no longer shows, is not)."""
    import numpy as np
    p = row["_plate"]
    if p.glyphs is None or tuple(im.size) != tuple(row["canvas"]):
        return False
    core = p.glyphs >= 250
    if core.sum() < 4:
        core = p.glyphs >= 160
    if core.sum() < 1:
        return False
    x0, y0 = p.origin
    h, w = p.glyphs.shape
    arr = np.asarray(im.convert("RGB").crop((x0, y0, x0 + w, y0 + h)), np.int16)
    if arr.shape[:2] != core.shape:
        return False
    d = np.abs(arr[core] - np.array(row["fill"], np.int16)).max(axis=1)
    return float((d <= 48).mean()) >= 0.5
