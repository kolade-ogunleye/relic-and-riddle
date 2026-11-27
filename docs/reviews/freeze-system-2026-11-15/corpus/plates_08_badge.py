"""PLATES-08 (panel 6 AD r2): the AI-reconstruction badge never stays over a graphic's drawn elements.

The badge is on for a generated illustration's beat and half a crossfade either side, so it reaches whatever clip
neighbours the illustration. Over EVERY graphic clip of every dev run (output/run_*/graphics/graphic_*.mp4), as if a
reconstruction stood before and after it, in the clip's own format (its poster's shape):
- the measure (written here, not imported): the badge plate's box grown by 12 px at 1080 wide against the clip's
  element boxes (<clip>.contrast.json), else the bright pixels of its poster still in that box; overlap area in px;
- the tree's answer: video_composer.clear_badge_intervals (a tree without it keeps the badge on, as it did).
Table: run, clip, format, evidence, overlap px, badge kept over the clip, pass. A violation: the corner the badge is
drawn in is taken (or nothing shows it free) and the badge stays over the clip. Freeze cycle 6 (panel 7 C4): the tree
draws the badge in its place, else in its documented alternative corner (vc.badge_corner_for), else it yields; the
proof measures the corner the tree draws in, by its own statement of that corner's box (badge_box).
"""
from __future__ import annotations

import json
from pathlib import Path

import plates_common as pc

a, tree, dev = pc.setup(__doc__)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src import video_composer as vc  # noqa: E402
from src.typography import get_font  # noqa: E402


def badge_box(w: int, h: int, corner: str = "top_left") -> tuple[int, int, int, int]:
    """The badge plate's box as build_subtitles draws it (its own statement of the geometry: a tree that moves the
    badge must move this proof with it). Freeze cycle 6 (panel 7 C4): padding 14 / 10 px at 1080 (the badge tokens);
    the documented alternative corner "top_right": the same inset from the right edge, on 9:16 on two lines broken at
    the separator (AI ILLUSTRATION / NOT A PHOTO), 1.75 cap heights baseline to baseline."""
    wide = w > h
    px = round(vc.RECON_SIZE_WIDE * h / 1080) if wide else vc.RECON_SIZE_PORTRAIT
    fs = vc.ass_font_size(vc._brand.BADGE_FONT, px, 600)
    scale = h / 1080 if wide else 1
    pad_x, pad_y = round(14 * scale), round(10 * scale)
    top, bottom = vc._ink_band(vc._brand.BADGE_FONT, px, fs, 600)
    font = get_font(vc._brand.BADGE_FONT, px, weight=600)
    label = vc.RECON_BADGE_LABEL
    lines = label.split(" · ") if (corner == "top_right" and not wide) else [label]
    tw = max(vc.unkerned_length(font, ln) + 1.5 * len(ln) for ln in lines)
    hb = font.getbbox("H")
    step = round(1.75 * (hb[3] - hb[1]))
    if wide:
        from src.graphics import WIDE_TITLE_SAFE
        x0, y0 = round(WIDE_TITLE_SAFE[0] * w / 1920), round(WIDE_TITLE_SAFE[1] * h / 1080)
        right = round(WIDE_TITLE_SAFE[2] * w / 1920)
    else:
        rx, ry, rx1, _ = vc._REEL_UI.box(w, h)
        x0, y0 = rx + round(19 * w / 1080), ry + round(12 * h / 1920)
        right = rx1 - round(19 * w / 1080)
    pw = round(tw + 2 * pad_x)
    if corner == "top_right":
        x0 = right - pw
    return x0, y0, x0 + pw, y0 + round(bottom - top + 2 * pad_y + (len(lines) - 1) * step)


def occupied(clip: Path, rect, w: int, h: int) -> tuple[str, int | None]:
    """(evidence, overlap px): the element boxes under `rect`, else the poster's bright pixels in it; None px when
    nothing shows the corner (no record, no poster)."""
    x0, y0, x1, y1 = rect
    side = Path(str(clip) + ".contrast.json")
    if side.is_file():
        boxes = [e["box"] for e in json.loads(side.read_text(encoding="utf-8")).get("elements") or []
                 if isinstance(e, dict) and len(e.get("box") or []) == 4]
        if boxes:
            return "element boxes", int(sum(max(0, min(b[2], x1) - max(b[0], x0)) * max(0, min(b[3], y1) - max(b[1], y0))
                                            for b in boxes))
    poster = clip.with_suffix(".jpg")
    if not poster.is_file():
        return "none", None
    with Image.open(poster) as im:
        rgb = im.convert("RGB").resize((w, h)) if im.size != (w, h) else im.convert("RGB")
    crop = np.asarray(rgb.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1))), np.float32)
    bright = crop @ np.array([0.299, 0.587, 0.114], np.float32) > 140
    return "poster", int(bright.sum()) if bright.mean() > 0.005 else 0


rows, bad = [], []
for run in pc.runs(dev):
    for clip in sorted((run / "graphics").glob("graphic_*.mp4")):
        poster = clip.with_suffix(".jpg")
        if poster.is_file():
            with Image.open(poster) as im:
                wide = im.width > im.height
        else:
            wide = False
        w, h = (1920, 1080) if wide else (1080, 1920)
        # the corner the tree draws the badge in over this clip (cycle 6: its place, else its documented alternative)
        corner = vc.badge_corner_for(clip, w, h)[0] if hasattr(vc, "badge_corner_for") else "top_left"
        box = badge_box(w, h, corner or "top_left")
        clear = round(12 * w / (1920 if wide else 1080))
        rect = (box[0] - clear, box[1] - clear, box[2] + clear, box[3] + clear)
        evidence, px = occupied(clip, rect, w, h)
        taken = px is None or px > 0
        scenes, durations = [run / "gen_before.png", clip, run / "gen_after.png"], [4.0, 5.0, 4.0]
        intervals = vc.reconstruction_badge_intervals(scenes, durations, 13.0)
        if hasattr(vc, "clear_badge_intervals"):
            intervals, _ = vc.clear_badge_intervals(intervals, scenes, durations, w, h)
        over = round(sum(max(0.0, min(e, 9.0) - max(s, 4.0)) for s, e in intervals), 3)
        ok = not (taken and over > 0)
        row = {"run": run.name[4:], "clip": clip.name, "format": "16:9" if wide else "9:16", "corner": corner,
               "evidence": evidence,
               "overlap_px": px, "badge_over_clip_s": over, "ok": ok}
        rows.append(row)
        if not ok:
            bad.append(row)
        print(f"{run.name[4:][:44]:44} {clip.name:22} {row['format']:5} {evidence:13} "
              f"{'?' if px is None else px:>7} px  badge over the clip {over:5.2f} s  {'pass' if ok else 'FAIL'}",
              flush=True)

taken = [r for r in rows if r["overlap_px"] is None or r["overlap_px"] > 0]
pc.finish(a, "PLATES-08 badge over drawn content", rows, bad, {
    "graphic clips": len(rows),
    "corner taken or unproven": len(taken),
    "of those, badge still over the clip": len(bad),
    "largest overlap px": max([r["overlap_px"] or 0 for r in rows] or [0]),
    "free corners where the badge yields anyway": sum(1 for r in rows if r["overlap_px"] == 0
                                                       and r["badge_over_clip_s"] < 5.0)})
