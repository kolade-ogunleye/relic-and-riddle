"""Boards: a surface's last frame in the default brand (left) and in the test brand (right), from two
render_surfaces.py --keep folders. Half size, JPEG, each under 300 KB.

    python make_boards.py <default keep dir> <second keep dir> <out dir> [--right=<label>] <surface name> [...]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

LIMIT = 300 * 1024


RIGHT = "test brand (red/blue swapped, Montserrat display, other masthead)"


def board(left: Path, right: Path, dst: Path, label: str = RIGHT) -> None:
    a, b = Image.open(left).convert("RGB"), Image.open(right).convert("RGB")
    w, h = a.width // 2, a.height // 2
    gap, band = 16, 36
    im = Image.new("RGB", (2 * w + gap, h + band), (255, 255, 255))
    im.paste(a.resize((w, h), Image.LANCZOS), (0, band))
    im.paste(b.resize((w, h), Image.LANCZOS), (w + gap, band))
    d = ImageDraw.Draw(im)
    d.text((8, 10), "default brand (today's values)", fill=(0, 0, 0))
    d.text((w + gap + 8, 10), label, fill=(0, 0, 0))
    for q in (85, 78, 70, 62, 55):
        im.save(dst, quality=q)
        if dst.stat().st_size <= LIMIT:
            return
    raise SystemExit(f"{dst.name} stays over 300 KB")


def main(default: str, second: str, out: str, *names: str) -> None:
    o = Path(out)
    o.mkdir(parents=True, exist_ok=True)
    label = RIGHT
    if names and names[0].startswith("--right="):     # the right half's label (a --brand second-colours folder)
        label, names = names[0][len("--right="):], names[1:]
    for n in names:
        board(Path(default) / f"{n}.png", Path(second) / f"{n}.png", o / f"{n}.jpg", label)
        print(n, (o / f"{n}.jpg").stat().st_size)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit("usage: make_boards.py <default keep> <second keep> <out> <name> [...]")
    main(*sys.argv[1:])
