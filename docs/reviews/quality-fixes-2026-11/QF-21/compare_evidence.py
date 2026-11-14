"""Before/after boards for the QF-21 evidence renders (render_evidence.py output of two trees).

    python compare_evidence.py <before dir> <after dir> <boards dir> [name ...]

Per render present in both: the changed pixels (any channel differs by more than 8 of 255, so encoder noise in a
poster does not count) and their box; for the names given (all changed ones without names), a labelled board:
the two frames at half size side by side, and under them each changed text line (up to four), before over
after, enlarged up to 2x.
Boards are JPEG (quality 82, at most 300 KB). Prints JSON.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def label(im: Image.Image, text: str) -> Image.Image:
    band = Image.new("RGB", (im.width, 34), (20, 20, 20))
    d = ImageDraw.Draw(band)
    d.text((10, 7), text, fill=(235, 235, 235), font=ImageFont.load_default(18))
    out = Image.new("RGB", (im.width, im.height + 34))
    out.paste(band, (0, 0))
    out.paste(im, (0, 34))
    return out


def bands(diff, gap: int = 16, most: int = 4) -> list[tuple[int, int, int, int]]:
    """The changed text lines: runs of changed rows (closer than `gap` rows), each with its own column extent;
    the `most` with the most changed pixels, top to bottom."""
    rows = np.nonzero(diff.any(1))[0]
    groups, start, prev = [], None, None
    for r in rows:
        if start is None:
            start = prev = r
        elif r - prev > gap:
            groups.append((start, prev))
            start = r
        prev = r
    if start is not None:
        groups.append((start, prev))
    out = []
    for y0, y1 in groups:
        cols = np.nonzero(diff[y0:y1 + 1].any(0))[0]
        out.append((int(cols.min()), int(y0), int(cols.max()) + 1, int(y1) + 1, int(diff[y0:y1 + 1].sum())))
    out = sorted(out, key=lambda b: -b[4])[:most]
    return [b[:4] for b in sorted(out, key=lambda b: b[1])]


def board(name: str, a: Image.Image, b: Image.Image, diff, out: Path) -> None:
    half = [label(x.resize((x.width // 2, x.height // 2), Image.LANCZOS), t)
            for x, t in ((a, "before (base 1ab9aded)"), (b, "after (QF-21)"))]
    top = Image.new("RGB", (half[0].width * 2 + 8, half[0].height), (60, 60, 60))
    top.paste(half[0], (0, 0))
    top.paste(half[1], (half[0].width + 8, 0))
    parts = [top]
    for x0, y0, x1, y1 in bands(diff):
        pad = 16
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(a.width, x1 + pad), min(a.height, y1 + pad)
        if x1 - x0 < 240:   # wide enough for its label
            grow = 240 - (x1 - x0)
            x0 = max(0, x0 - grow // 2)
            x1 = min(a.width, x0 + 240)
        zoom = min(2.0, top.width / (x1 - x0))
        for x, t in ((a, "before"), (b, "after")):
            c = x.crop((x0, y0, x1, y1))
            c = c.resize((round(c.width * zoom), round(c.height * zoom)), Image.LANCZOS)
            parts.append(label(c, f"{t}: x {x0}-{x1}, y {y0}-{y1}, {zoom:.1f}x"))
    w = max(p.width for p in parts)
    h = sum(p.height + 8 for p in parts)
    sheet = Image.new("RGB", (w, h), (60, 60, 60))
    y = 0
    for p in parts:
        sheet.paste(p, (0, y))
        y += p.height + 8
    sheet = label(sheet, name)
    q = 82
    while True:
        sheet.save(out, quality=q)
        if out.stat().st_size <= 300_000 or q <= 40:
            break
        if q <= 60:
            sheet = sheet.resize((sheet.width * 9 // 10, sheet.height * 9 // 10), Image.LANCZOS)
        q -= 6


def main(before: Path, after: Path, boards: Path, names: list[str]) -> None:
    boards.mkdir(parents=True, exist_ok=True)
    rows = {}
    for p in sorted(after.glob("*.png")):
        q = before / p.name
        if not q.is_file():
            rows[p.stem] = {"before": "missing"}
            continue
        a, b = Image.open(q).convert("RGB"), Image.open(p).convert("RGB")
        if a.size != b.size:
            rows[p.stem] = {"size": [a.size, b.size]}
            continue
        diff = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)).max(-1) > 8
        ys, xs = np.nonzero(diff)
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None
        rows[p.stem] = {"changed_px": int(diff.sum()), "box": box}
        if (names and p.stem in names) or (not names and box):
            board(p.stem, a, b, diff, boards / f"{p.stem}.jpg")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
