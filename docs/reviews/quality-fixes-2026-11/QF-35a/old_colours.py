"""How much of today's palette is left on a surface drawn in the test brand: exact pixel counts, last frames.

    python old_colours.py <tree> <default keep dir> <second keep dir> [second|third]

The two folders are render_surfaces.py --keep results (the default brand and --brand second) of <tree>. For every
surface in both, the pixels that equal a default palette colour exactly are counted in the default frame and in the
test brand's frame, and the test brand's own colours likewise. Prints JSON, per palette role: the two sums over all
surfaces, `excess` (summed over surfaces: how many more pixels of today's colour the test brand's frame holds than
the default frame of the same surface, never negative) and the surfaces behind it.

A flat fill, a line or the body of a letter is the exact colour; an antialiased edge or a translucent layer is not,
so the counts are a floor of what is drawn. Roles are reported in two classes:
- marks (a channel of 64 or more: text, figures, lines, land tints): a photograph rarely holds the exact value, and
  where it does the default frame of the same surface holds it too, so `excess` is what the test brand left over;
- grounds (every channel under 64: INK, the plates, the earth and rock grounds): a dark photograph pixel, a shaded
  photograph or a gradient passes through these values by chance in either brand (the test brand's shade is a warm
  near-black, so its shaded photographs land on today's warm near-blacks), so their counts are listed, not judged.
LEGIBLE_TEXT is its own mirror under the red/blue swap and is not counted. With `third` (the port's brand,
render_surfaces.py third_brand, which moves every channel of every colour) every palette colour is counted, greys
included, and the test brand's own colours are that brand's.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image


def counts(path: Path, colours: dict) -> dict:
    a = np.asarray(Image.open(path).convert("RGB"))
    packed = (a[..., 0].astype(np.uint32) << 16) | (a[..., 1].astype(np.uint32) << 8) | a[..., 2]
    values, n = np.unique(packed, return_counts=True)
    have = dict(zip(values.tolist(), n.tolist()))
    return {role: have.get((c[0] << 16) | (c[1] << 8) | c[2], 0) for role, c in colours.items()}


def main(tree: str, default_dir: str, second_dir: str, which: str = "second") -> None:
    sys.path.insert(0, str(Path(tree).resolve()))
    from src import brand
    base = brand.DEFAULT_BRAND
    today = {r: base.colour(r) for r, v in base.palette.items() if not isinstance(v, str)}
    if which == "third":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from render_surfaces import third_brand
        test_brand = third_brand(brand)
        theirs = {r: test_brand.colour(r) for r in today}
    else:
        today = {r: c for r, c in today.items() if c[0] != c[2]}
        theirs = {r: (c[2], c[1], c[0]) for r, c in today.items()}
    d, s = Path(default_dir), Path(second_dir)
    names = sorted(p.name for p in d.glob("*.png") if (s / p.name).is_file())
    rows = {r: {"class": "mark" if max(today[r]) >= 64 else "ground", "default": 0, "test_brand": 0, "excess": 0,
                "own_in_default": 0, "own_in_test_brand": 0, "surfaces_with_excess": []} for r in today}
    for name in names:
        in_default, in_second = counts(d / name, today), counts(s / name, today)
        own_default, own_second = counts(d / name, theirs), counts(s / name, theirs)
        for role, row in rows.items():
            row["default"] += in_default[role]
            row["test_brand"] += in_second[role]
            row["own_in_default"] += own_default[role]
            row["own_in_test_brand"] += own_second[role]
            more = in_second[role] - in_default[role]
            if more > 0:
                row["excess"] += more
                row["surfaces_with_excess"].append([name[:-4], more])
    for row in rows.values():
        row["surfaces_with_excess"] = sorted(row["surfaces_with_excess"], key=lambda x: -x[1])[:8]
    marks = [r for r in rows.values() if r["class"] == "mark"]
    out = {
        "surfaces": len(names),
        "groups": dict(sorted(Counter(n.split("__")[0] for n in names).items())),
        "only_default": sorted(p.stem for p in d.glob("*.png") if not (s / p.name).is_file()),
        "marks": {"roles": len(marks), "today_px_in_default_frames": sum(r["default"] for r in marks),
                  "today_px_in_test_brand_frames": sum(r["test_brand"] for r in marks),
                  "excess_px": sum(r["excess"] for r in marks),
                  "test_brand_px_in_its_own_frames": sum(r["own_in_test_brand"] for r in marks)},
        "roles": {r: row for r, row in rows.items() if row["default"] or row["test_brand"] or row["own_in_test_brand"]},
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        raise SystemExit("usage: old_colours.py <tree> <default keep dir> <second keep dir> [second|third]")
    main(*sys.argv[1:])
