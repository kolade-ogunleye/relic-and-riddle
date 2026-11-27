"""MASTHEAD-01 (freeze cycle 6, panel 7 PL3 M1): on every HOOK surface that carries a masthead (the Instagram and
Facebook carousel covers, the YouTube thumbnail; the Reel cover and its grid tile carry none, operator ruling 2) the
mark does not compete with the hook: its drawn group width over the ink width of the surface's OWN first title row is
at most masthead.hook_ratio_max (0.78), or the surface records it at its ceiling (the mark at its ladder's floor, no
legal re-break or declared title size; the record names why).

Reads what plates_draw.py (the decks) and masthead_draw_youtube.py (the thumbnails) left for one tree and palette:

    python masthead_01_hook_ratio.py <decks>/<brand> <youtube>/<brand> [--json out.json] [--ratio 0.78] [--also 0.70]

The first row is measured the same way at base and tip (written again here, not imported from the tree): the raster
ink of the record's first heading line at the drawn size (covers), youtube_thumbnail.hook_row on the drawn plates
(thumbnails). The masthead's width is its record's group_width. A violation: over the token and not recorded at its
ceiling. `--also`: the same counts at another value (information only; the token decides).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

DECKS = ("carousel", "carousel_fb")
SURFACE = {"carousel": "IG cover", "carousel_fb": "FB cover"}


def ink(text: str, size: float, font_path: str | None = None) -> float:
    """The raster ink width of `text` set in the display face at `size` px (the tree's own font loader)."""
    from PIL import Image, ImageDraw

    from src import brand
    from src.typography import get_font
    f = get_font(brand.DISPLAY_FONT, size)
    m = Image.new("L", (math.ceil(f.getlength(text) + 4 * size) + 16, math.ceil(4 * size) + 16), 0)
    ImageDraw.Draw(m).text((2 * size, 2 * size + 8), text, font=f, fill=255, anchor="ls")
    bb = m.getbbox()
    return 0.0 if bb is None else float(bb[2] - bb[0])


def cover_rows(root: Path) -> list[dict]:
    out = []
    for run in sorted(p for p in Path(root).iterdir() if (p / "draw.json").is_file()):
        for sub in DECKS:
            f = run / sub / "still_layout.json"
            if not f.is_file():
                continue
            e = json.loads(f.read_text(encoding="utf-8")).get("carousel_slide_01.jpg") or {}
            meta = e.get("meta") or {}
            h, m, rec = meta.get("heading") or {}, meta.get("masthead") or {}, meta.get("hook_ratio") or {}
            lines = h.get("lines") or []
            w = (e.get("size") or [1440, 1800])[0]
            row = ink(lines[0], float(h["size"]) * w / 1080) if lines else None
            out.append({"run": run.name[4:], "surface": SURFACE[sub], "first_row": lines[0] if lines else None,
                        "lines": lines, "title_px": h.get("size"), "row_px": row,
                        "mast_px": m.get("group_width") if m.get("shown") else None,
                        "mast_size": m.get("size_px") if m.get("shown") else None, "record": rec})
    return out


def youtube_rows(root: Path) -> list[dict]:
    out = []
    for run in sorted(p for p in Path(root).iterdir() if (p / "yt.json").is_file()):
        d = json.loads((run / "yt.json").read_text(encoding="utf-8"))
        m = d.get("masthead") or {}
        first = None
        if d.get("ok"):
            heads = [p for p in d.get("plates") or [] if p["name"] in ("figure", "figure_unit", "title_1", "title_2",
                                                                        "title")]
            first = " ".join(p["text"] for p in heads if p["box"][1] == min(q["box"][1] for q in heads)) if heads \
                else None
        out.append({"run": run.name[4:], "surface": "YouTube", "first_row": first, "lines": [],
                    "title_px": (d.get("lockup") or {}).get("sizes"), "row_px": d.get("hook_row") if d.get("ok") else None,
                    "mast_px": m.get("group_width") if m.get("shown") else None,
                    "mast_size": m.get("size_px") if m.get("shown") else None, "record": d.get("hook_ratio") or {},
                    "refused": None if d.get("ok") else (d.get("error") or "refused")[:160]})
    return out


def judge(row: dict, ratio_max: float) -> tuple[float | None, bool, str]:
    if row.get("refused"):
        return None, True, "refused (no thumbnail drawn: nothing to compete)"
    if not row["mast_px"] or not row["row_px"]:
        return None, True, "no masthead drawn" if not row["mast_px"] else "no title row"
    r = row["mast_px"] / row["row_px"]
    if r <= ratio_max + 1e-9:
        return r, True, "holds"
    rec = row["record"]
    if rec.get("at_ceiling") and rec.get("reason"):
        return r, True, "at ceiling, recorded"
    return r, False, "OVER, not recorded"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decks", type=Path)
    ap.add_argument("youtube", type=Path)
    ap.add_argument("--tree", type=Path, default=Path(__file__).resolve().parents[4])
    ap.add_argument("--json", type=Path)
    ap.add_argument("--ratio", type=float, default=0.78)
    ap.add_argument("--also", type=float, default=0.70)
    a = ap.parse_args()
    sys.path.insert(0, str(a.tree.resolve()))
    rows = cover_rows(a.decks) + youtube_rows(a.youtube)
    bad, counts = [], {"rows": len(rows), "over token": 0, "over, recorded at ceiling": 0, "violations": 0,
                       f"over {a.also}": 0}
    for row in rows:
        r, ok, why = judge(row, a.ratio)
        row.update(ratio=None if r is None else round(r, 3), ok=ok, why=why)
        if r is not None and r > a.ratio + 1e-9:
            counts["over token"] += 1
            counts["over, recorded at ceiling"] += ok
        if r is not None and r > a.also + 1e-9:
            counts[f"over {a.also}"] += 1
        if not ok:
            bad.append(row)
            counts["violations"] += 1
        print(f"{row['run'][:42]:42} {row['surface']:8} {str(row['first_row'])[:24]:24} "
              f"row {row['row_px'] or 0:7.1f} mast {row['mast_px'] or 0:6.1f} @{row['mast_size'] or 0:5.1f} "
              f"{'-' if r is None else format(r, '.3f'):>6} {'pass' if ok else 'FAIL'} {why}", flush=True)
    print(f"\nMASTHEAD-01 hook ratio (token {a.ratio}): {json.dumps(counts)}")
    if a.json:
        a.json.write_text(json.dumps({"counts": counts, "rows": rows}, indent=1, ensure_ascii=False, default=str),
                          encoding="utf-8", newline="\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
