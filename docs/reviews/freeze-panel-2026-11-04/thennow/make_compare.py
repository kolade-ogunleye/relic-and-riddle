"""Labelled before | after sheets for the then-and-now freeze round: one JPEG per variant, compare/<variant>_before_after.jpg.

Each sheet has, for 9:16 and then 16:9, the THEN-alone and the resting-split frames (half size), before on the left and
after on the right, labelled with the variant, format, moment and each marker's type mode and glow depth (meta.json).

Usage: python make_compare.py --before <before dir> --after <after dir> --out <compare dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FORMATS = ("9x16", "16x9")
MOMENTS = ("then_alone", "mid_wipe", "split")


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _meta(d: Path) -> dict:
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _meta_line(d: Path) -> str:
    m = _meta(d)
    if not m:
        return ""
    mode = m.get("type_mode") or {}
    depth = m.get("glow_depth") or {}
    if not depth and isinstance(m.get("glow"), dict):
        depth = {w: g.get("depth") for w, g in m["glow"].items()}
    words = m.get("kicker") or {}
    parts = []
    for w in ("then", "now"):
        parts.append(f"{words.get(w, w.upper())} {mode.get(w, '?')}, glow {depth.get(w) if depth.get(w) is not None else 'none'}")
    extra = []
    if m.get("halo_H") is not None:
        extra.append(f"halo {m['halo_H']}")
    side = m.get("side_916")
    if isinstance(side, dict):
        extra.append(f"side {side.get('side')}")
    fr = m.get("framing")
    if isinstance(fr, dict):
        extra.append(f"zoom {fr.get('zoom')}")
    return "; ".join(parts) + ("  |  " + ", ".join(extra) if extra else "")


def _case_line(d: Path) -> str:
    m = _meta(d)
    return f"{m.get('run', '?')}  ({m.get('topic', '?')})  THEN {m.get('old_year')} / {m.get('new_year')}"


def _labelled(im, text, sub, case="", label=""):
    import textwrap
    lines = [(text, (240, 240, 240), 24), (sub, (190, 190, 190), 16)]
    if case:
        lines.append((case, (200, 200, 200), 16))
    for t in textwrap.wrap(str(label or "").upper(), 58):
        lines.append((t, (255, 170, 60), 16))
    bar = 10 + sum(s + 8 for _, _, s in lines)
    out = Image.new("RGB", (im.width, im.height + bar), (18, 18, 18))
    out.paste(im.convert("RGB"), (0, bar))
    d = ImageDraw.Draw(out)
    y = 6
    for t, c, s in lines:
        d.text((10, y), t, fill=c, font=_font(s))
        y += s + 8
    return out


def _row(cells: list, gap: int = 12) -> Image.Image:
    w = sum(c.width for c in cells) + gap * (len(cells) - 1)
    row = Image.new("RGB", (w, max(c.height for c in cells)), (60, 60, 60))
    x = 0
    for c in cells:
        row.paste(c, (x, 0))
        x += c.width + gap
    return row


def sheet(before: Path, after: Path, variant: str) -> Image.Image | None:
    """9:16: one row, before's three moments then after's (portrait cells). 16:9: a before row over an after row
    (landscape cells), so both formats fill about the same width (review round 2: half the canvas was empty)."""
    rows = []
    for fmt in FORMATS:
        bd, ad = before / fmt / variant, after / fmt / variant
        if not bd.exists():          # a variant the before tree had no switch for: its base case, as shipped
            bd = before / fmt / variant.split("__")[0]
        sides = []
        for side, d in ((f"BEFORE ({before.name}: HEAD tree, as shipped)", bd), ("AFTER (this round's tree)", ad)):
            cells = []
            for k in MOMENTS:
                f = d / f"frame_{k}_half.jpg"
                if f.exists():
                    cells.append(_labelled(Image.open(f), f"{side}  {variant}  {fmt}  {k}", _meta_line(d),
                                           _case_line(d), _meta(d).get("label")))
            if cells:
                sides.append(_row(cells))
        if not sides:
            continue
        if fmt == "9x16":
            rows.append(_row(sides, gap=36))
        else:
            rows.extend(sides)
    if not rows:
        return None
    w = max(r.width for r in rows)
    out = Image.new("RGB", (w, sum(r.height for r in rows) + 16 * (len(rows) - 1)), (30, 30, 30))
    y = 0
    for r in rows:
        out.paste(r, ((w - r.width) // 2, y))
        y += r.height + 16
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    before, after, out = Path(args.before), Path(args.after), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    variants = sorted({p.name for fmt in FORMATS for p in (after / fmt).glob("*") if p.is_dir()
                       and (p / "frame_split_half.jpg").exists()})
    for v in variants:
        im = sheet(before, after, v)
        if im is None:
            continue
        p = out / f"{v}_before_after.jpg"
        q = 86
        while True:
            im.save(p, quality=q)
            if p.stat().st_size < 2_000_000 or q <= 50:          # under 2 MB (decimal)
                break
            q -= 8
        print(p)


if __name__ == "__main__":
    main()
