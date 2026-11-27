"""The changed-surfaces report between two boards of one palette (cycle 5, lane BOARD): one command instead of a
hand-built before|after sheet.

    python board_diff.py <before board dir> <after board dir> <out dir> [--before-label TEXT] [--after-label TEXT]
                         [--max-width 2400] [--sheets] [--only PREFIX,...]

Compares every media file of the two boards by its bytes (MANIFEST.json's sha256 where a board has one, else the
file's) and writes:
  <out>/index.md, index.json   per row: the stems that changed, were added or removed, with the changed pixels' box,
                               the share of pixels that differ and the mean difference; the counts of unchanged ones;
                               and which of the after board's files were reused (REUSE.json), which by definition
                               did not change
  <out>/pairs/<row>/<stem>.jpg one file per changed image: BEFORE | AFTER at 1:1, labelled; side by side when both fit
                               in --max-width, else one above the other; scaled only when one alone is wider than
                               --max-width (then the changed region is added at 1:1 underneath)
  <out>/pairs/<row>/<stem>.jpg for a changed clip: a strip pair, the same frames of both clips (evenly spaced, plus
                               the frame that differs most), each at its time, with the per-frame difference
A row sheet (rowNN_*.png) is a composite of surfaces: it is listed, and paired only with --sheets (its surfaces carry
the change). A `_half.jpg` is listed with its full-size twin, never paired. Rows are board.py's own (ROWS / on_row).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board  # noqa: E402  (ROWS, on_row: the one row filter)

IMAGE = {".png", ".jpg", ".jpeg"}
VIDEO = {".mp4", ".mov", ".webm", ".mkv"}
GAP, BAR = 16, 72   # the gap between the two sides; the label bar (two lines: which board, which stem)
STRIP_FRAMES, STRIP_H = 8, 480


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def media(root: Path) -> dict[str, str]:
    """{relative posix path: sha256} of a board's media files (its MANIFEST.json's hashes when it lists the file
    and the file's size is the listed one, else hashed now)."""
    listed = {}
    m = root / "MANIFEST.json"
    if m.is_file():
        try:
            listed = {e["path"]: e for e in json.loads(m.read_text(encoding="utf-8")).get("images") or []}
        except ValueError:
            listed = {}
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMAGE | VIDEO and "_ab" not in p.parts:
            rel = p.relative_to(root).as_posix()
            e = listed.get(rel)
            out[rel] = e["sha256"] if e and e.get("size") == p.stat().st_size else sha256_file(p)
    return out


def row_of(rel: str, clips: dict[str, Any]) -> str:
    """The board row a file belongs to (a surface can sit on two rows: the first is its group)."""
    name = Path(rel).name
    stem = Path(rel).stem
    m = re.match(r"row(\d\d)_", name)
    if m:
        return f"row {int(m.group(1)):02d}"
    if rel.startswith("clips/"):
        r = str((clips.get(name) or {}).get("row") or "")
        first = re.match(r"\d+", r)
        return f"row {int(first.group(0)):02d}" if first else "clips"
    if name.startswith("peer_feed_"):
        return "peer feeds"
    if name.startswith("refusals_"):
        return "refusals"
    if rel.startswith(("surfaces/", "cases/")):
        for r, (_n, prefixes) in sorted(board.ROWS.items()):
            if board.on_row(r, prefixes, stem):
                return f"row {r:02d}"
        for r, (_n, prefixes) in sorted(board.ROWS.items()):   # a case the row filter leaves to the board's own draw
            if stem.startswith(prefixes):
                return f"row {r:02d}"
    return "other"


def is_sheet(rel: str) -> bool:
    return "/" not in rel


def _font(size: int):
    from PIL import ImageFont
    return ImageFont.load_default(size=size)


def _bar(width: int, text: str, fill=(24, 24, 24), ink=(255, 210, 80)):
    """The label bar: the board's label on its first line, the rest (" | "-separated) on the second, each cut to
    the width from its END inward, so the stem's last part (the format, the frame) is always read."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (width, BAR), fill)
    d = ImageDraw.Draw(im)
    head, _, rest = text.partition("  |  ")
    for y, line, f, colour in ((6, head, _font(26), ink), (40, rest, _font(20), (235, 235, 235))):
        while len(line) > 8 and d.textlength(line, font=f) > width - 16:
            line = "..." + line[7:] if line.startswith("...") else "..." + line[4:]
        d.text((8, y), line, fill=colour, font=f)
    return im


def measure(a, b) -> dict[str, Any]:
    """Where and how much two images differ: the changed pixels' box, their share and the mean absolute difference."""
    import numpy as np
    if a.size != b.size:
        return {"size_before": list(a.size), "size_after": list(b.size), "box": None, "share": 1.0, "mean_abs": None}
    d = np.abs(np.asarray(a.convert("RGB"), dtype=np.int16) - np.asarray(b.convert("RGB"), dtype=np.int16)).max(axis=2)
    ys, xs = np.nonzero(d)
    if len(xs) == 0:
        return {"size": list(a.size), "box": None, "share": 0.0, "mean_abs": 0.0}
    return {"size": list(a.size), "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
            "share": round(float(len(xs)) / d.size, 5), "mean_abs": round(float(d.mean()), 3),
            "max_abs": int(d.max())}


def image_pair(before, after, labels: tuple[str, str], stem: str, max_width: int, m: dict[str, Any]):
    """BEFORE | AFTER, each under its label, at 1:1 where --max-width allows (see the module docstring)."""
    from PIL import Image
    a, b = before.convert("RGB"), after.convert("RGB")
    wide = max(a.width, b.width)
    scale = min(1.0, max_width / wide)
    if scale < 1.0:
        a = a.resize((max(1, round(a.width * scale)), max(1, round(a.height * scale))), Image.LANCZOS)
        b = b.resize((max(1, round(b.width * scale)), max(1, round(b.height * scale))), Image.LANCZOS)
    note = "1:1" if scale == 1.0 else f"scaled to {scale:.2f}"
    side = a.width + GAP + b.width <= max_width
    parts = [(f"{labels[0]}  |  {stem}  |  {note}", a), (f"{labels[1]}  |  {stem}  |  {note}", b)]
    if side:
        W = a.width + GAP + b.width
        H = BAR + max(a.height, b.height)
        sheet = Image.new("RGB", (W, H), (255, 255, 255))
        x = 0
        for text, im in parts:
            sheet.paste(_bar(im.width, text), (x, 0))
            sheet.paste(im, (x, BAR))
            x += im.width + GAP
    else:
        W = max(a.width, b.width)
        H = sum(BAR + im.height for _t, im in parts) + GAP
        sheet = Image.new("RGB", (W, H), (255, 255, 255))
        y = 0
        for text, im in parts:
            sheet.paste(_bar(W, text), (0, y))
            sheet.paste(im, (0, y + BAR))
            y += BAR + im.height + GAP
    box = m.get("box")
    if scale < 1.0 and box and before.size == after.size:   # the changed region at 1:1, so nothing is judged scaled
        pad = 40
        x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
        x1, y1 = min(before.width, box[2] + pad), min(before.height, box[3] + pad)
        half = (max_width - GAP) // 2
        if x1 - x0 > half:   # the region's left part: as much as fits side by side
            x1 = x0 + half
        y1 = min(y1, y0 + 2400)
        ca, cb = before.convert("RGB").crop((x0, y0, x1, y1)), after.convert("RGB").crop((x0, y0, x1, y1))
        W2 = max(sheet.width, ca.width * 2 + GAP)
        grown = Image.new("RGB", (W2, sheet.height + GAP + BAR + ca.height), (255, 255, 255))
        grown.paste(sheet, (0, 0))
        y = sheet.height + GAP
        grown.paste(_bar(W2, f"changed region {[x0, y0, x1, y1]} at 1:1: {labels[0]} | {labels[1]}"), (0, y))
        grown.paste(ca, (0, y + BAR))
        grown.paste(cb, (ca.width + GAP, y + BAR))
        sheet = grown
    return sheet


def decode(path: Path, height: int = STRIP_H) -> tuple[list, float]:
    """(every frame of a clip as an RGB image scaled to `height`, its frame rate), decoded once by ffmpeg."""
    import numpy as np
    from PIL import Image
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate", "-of", "json", str(path)], capture_output=True,
                           text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    num, den = (st["r_frame_rate"].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    w = max(2, round(st["width"] * height / st["height"] / 2) * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={w}:{height}:flags=lanczos",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (w * height * 3)
    arr = np.frombuffer(raw[: n * w * height * 3], dtype=np.uint8).reshape(n, height, w, 3)
    return [Image.fromarray(arr[i]) for i in range(n)], fps


def clip_pair(before: Path, after: Path, labels: tuple[str, str], stem: str, max_width: int
              ) -> tuple[Any, dict[str, Any]]:
    """A strip pair of two clips: the same frame numbers of both, evenly spaced plus the frame that differs most."""
    import numpy as np
    from PIL import Image, ImageDraw
    fa, fps = decode(before)
    fb, _ = decode(after)
    n = min(len(fa), len(fb))
    mad = []
    if n and fa[0].size == fb[0].size:
        mad = [float(np.abs(np.asarray(fa[i], dtype=np.int16) - np.asarray(fb[i], dtype=np.int16)).mean())
               for i in range(n)]
    worst = int(np.argmax(mad)) if mad else 0
    longest = max(len(fa), len(fb))
    picks = sorted({round(k * (longest - 1) / (STRIP_FRAMES - 1)) for k in range(STRIP_FRAMES)} | {worst})
    tile_w = max((f[0].width for f in (fa, fb) if f), default=270)
    cols = len(picks)
    scale = min(1.0, (max_width - GAP * (cols - 1)) / (tile_w * cols))
    tw, th = max(1, round(tile_w * scale)), max(1, round(STRIP_H * scale))
    W = cols * tw + GAP * (cols - 1)
    f = _font(20)
    rows = []
    for text, frames in ((labels[0], fa), (labels[1], fb)):
        row = Image.new("RGB", (W, BAR + 28 + th), (40, 40, 40))
        row.paste(_bar(W, f"{text}  |  {stem}  |  {len(frames)} frames at {fps:g} fps, each frame {th} px high"),
                  (0, 0))
        d = ImageDraw.Draw(row)
        for k, i in enumerate(picks):
            x = k * (tw + GAP)
            if i < len(frames):
                row.paste(frames[i].resize((tw, th), Image.LANCZOS), (x, BAR + 28))
            mark = f" diff {mad[i]:.2f}" if i < len(mad) else " (no twin)"
            d.text((x + 4, BAR + 2), f"f{i:03d} t={i / fps:.2f}s{mark}{' MOST' if mad and i == worst else ''}",
                   fill=(255, 255, 255), font=f)
        rows.append(row)
    sheet = Image.new("RGB", (W, sum(r.height for r in rows) + GAP), (255, 255, 255))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + GAP
    return sheet, {"frames_before": len(fa), "frames_after": len(fb), "fps": fps,
                   "frames_differing": sum(1 for v in mad if v > 0), "worst_frame": worst if mad else None,
                   "worst_mean_abs": round(max(mad), 3) if mad else None,
                   "mean_abs": round(sum(mad) / len(mad), 3) if mad else None}


def report(before: Path, after: Path, out: Path, labels: tuple[str, str], max_width: int = 2400,
           sheets: bool = False, only: tuple[str, ...] = ()) -> dict[str, Any]:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None   # the row sheets are large and our own
    ma, mb = media(before), media(after)
    clips = {}
    reused: set[str] = set()
    for root in (after, before):
        try:
            for k, v in (json.loads((root / "board.json").read_text(encoding="utf-8")).get("clips") or {}).items():
                clips.setdefault(k, v)
        except (OSError, ValueError):
            pass
    try:
        rec = json.loads((after / "REUSE.json").read_text(encoding="utf-8"))
        reused = {f for f, r in (rec.get("files") or {}).items() if r.get("status") == "reused"}
    except (OSError, ValueError):
        pass
    rows: dict[str, dict[str, Any]] = {}
    out.mkdir(parents=True, exist_ok=True)
    for rel in sorted(set(ma) | set(mb)):
        stem = Path(rel).stem
        if only and not stem.startswith(only) and not rel.startswith(only):
            continue
        row = rows.setdefault(row_of(rel, clips), {"changed": [], "added": [], "removed": [], "unchanged": 0,
                                                    "reused": 0})
        if rel in reused:
            row["reused"] += 1
        if rel not in ma:
            row["added"].append({"path": rel})
            continue
        if rel not in mb:
            row["removed"].append({"path": rel})
            continue
        if ma[rel] == mb[rel]:
            row["unchanged"] += 1
            continue
        entry: dict[str, Any] = {"path": rel, "stem": stem}
        half = stem.endswith("_half")
        suffix = Path(rel).suffix.lower()
        if suffix in VIDEO:
            sheet, m = clip_pair(before / rel, after / rel, labels, stem, max_width)
            entry.update(kind="clip", **m)
        elif half:
            entry.update(kind="half-scale twin", pair=None)
            row["changed"].append(entry)
            continue
        else:
            a, b = Image.open(before / rel), Image.open(after / rel)
            m = measure(a, b)
            entry.update(kind="sheet" if is_sheet(rel) else "surface", **m)
            if m.get("share") == 0.0:   # other bytes, the same pixels (an encoder or metadata difference)
                entry["note"] = "bytes differ, pixels identical"
                row["changed"].append(entry)
                continue
            if is_sheet(rel) and not sheets:
                entry["pair"] = None
                row["changed"].append(entry)
                continue
            sheet = image_pair(a, b, labels, stem, max_width, m)
        group = row_of(rel, clips).replace(" ", "")
        dst = out / "pairs" / group / f"{stem}.jpg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dst, quality=90)
        entry["pair"] = dst.relative_to(out).as_posix()
        entry["pair_size"] = list(sheet.size)
        row["changed"].append(entry)
    import board_reuse   # round 3: a board made with --unsafe-reuse-for-iteration is marked in the index
    not_evidence = {side: why for side, why in (("before", board_reuse.not_evidence(before)),
                                                ("after", board_reuse.not_evidence(after))) if why}
    result = {"before": str(before), "after": str(after), "labels": list(labels), "max_width": max_width,
              "not_evidence": not_evidence,
              "rows": dict(sorted(rows.items())),
              "counts": {k: sum(len(r[k]) if isinstance(r[k], list) else r[k] for r in rows.values())
                         for k in ("changed", "added", "removed", "unchanged", "reused")}}
    (out / "index.json").write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    lines = [f"# Changed surfaces: {labels[0]} -> {labels[1]}", "",
             f"before: `{before}`  ", f"after: `{after}`  ",
             "counts: " + ", ".join(f"{k} {v}" for k, v in result["counts"].items()), ""]
    for side, why in not_evidence.items():
        lines[1:1] = [f"**{board_reuse.NOT_EVIDENCE}: the {side} board is an incremental board** ({why}); this "
                      f"report is an iteration aid, not evidence.", ""]
    for name, r in result["rows"].items():
        if not (r["changed"] or r["added"] or r["removed"]):
            continue
        lines += [f"## {name}", "",
                  f"{len(r['changed'])} changed, {len(r['added'])} added, {len(r['removed'])} removed, "
                  f"{r['unchanged']} unchanged ({r['reused']} reused files)", ""]
        for e in r["changed"]:
            if e["kind"] == "clip":
                what = (f"clip, {e['frames_differing']} of {e['frames_before']} frames differ, most at frame "
                        f"{e['worst_frame']} (mean {e['worst_mean_abs']})")
            elif e["kind"] == "half-scale twin":
                what = "half-scale twin of a changed sheet"
            elif e.get("note"):
                what = e["note"]
            elif e.get("box") is None:
                what = f"size {e.get('size_before')} -> {e.get('size_after')}"
            else:
                what = f"changed box {e['box']}, {e['share'] * 100:.2f} % of pixels, mean {e['mean_abs']}"
            link = f" [pair]({e['pair']})" if e.get("pair") else ""
            lines.append(f"- `{e['path']}` ({e['kind']}): {what}{link}")
        lines += [f"- ADDED `{e['path']}`" for e in r["added"]] + [f"- REMOVED `{e['path']}`" for e in r["removed"]]
        lines.append("")
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--before-label", default=None)
    ap.add_argument("--after-label", default=None)
    ap.add_argument("--max-width", type=int, default=2400)
    ap.add_argument("--sheets", action="store_true", help="pair the row sheets too (scaled, with the changed region)")
    ap.add_argument("--only", default="", help="only stems or paths with one of these prefixes (comma-separated)")
    a = ap.parse_args(argv)

    def head(root: Path, word: str) -> str:
        try:
            h = (json.loads((root / "board.json").read_text(encoding="utf-8")).get("tree") or {}).get("head") or ""
        except (OSError, ValueError):
            h = ""
        return f"{word} {root.name}" + (f" {h[:8]}" if h else "")
    labels = (a.before_label or head(a.before, "BEFORE"), a.after_label or head(a.after, "AFTER"))
    r = report(a.before.resolve(), a.after.resolve(), a.out.resolve(), labels, a.max_width, a.sheets,
               tuple(x for x in a.only.split(",") if x))
    print(f"{a.out / 'index.md'}: " + ", ".join(f"{k} {v}" for k, v in r["counts"].items()))
    for name, row in r["rows"].items():
        for e in row["changed"]:
            if e.get("pair"):
                print(f"  {name}: {e['path']} -> {e['pair']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
