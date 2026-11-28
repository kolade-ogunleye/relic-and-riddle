"""Corpus proof, freeze cycle 6 CAPTION GATE (capgate, lander's ruling 3): the release gate's caption contrast check run
over every development master before it may block a run.

    RR_DEV=<dev checkout> python capgate_c6.py --out <scratch dir> [--tree <tree>] [--only <substring>] [--crops]
                                               [--json <rows.json>]

Every output/run_*/<stem>_master.mp4 under RR_DEV that has its captions (<stem>_master_captions.ass) and its
composition record (<stem>_master.composition.json) is measured with <tree>'s own src/video_qa.caption_contrast (the
tree this file is in by default), with the end card's start from the composition record as text_contrast_audit
reads it. The dev checkout is only read: every file the gate writes (its mask file, its per-frame record) goes under
--out/<run>/. Per run it prints: bursts, bursts under the bar fully shown, dips reported (fade frames, not blocking),
the frames under the bar, the verdict, and the blocking findings. A tree from before the capgate fix has only the 6
sampled midpoints: its row says so ("sampled 6"), and its verdict is the old one (any sample under the bar fails).
`--crops` saves, for every run that fails, the master frame of its worst blocking reading and the same frame cropped
around the caption, under --out/<run>/, so the lander can see whether the caption is really unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()


def masters(dev: Path):
    for mp4 in sorted(dev.glob("output/run_*/*_master.mp4")):
        ass = mp4.with_name(mp4.stem + "_captions.ass")
        comp = mp4.with_suffix(".composition.json")
        yield mp4, ass, comp, ass.is_file() and comp.is_file()


def end_card(comp: Path):
    try:
        start = (json.loads(comp.read_text(encoding="utf-8")).get("end_card") or {}).get("start_s")
    except (OSError, ValueError):
        return None
    return float(start) if start else None


# The retired caption design drew the words still to be spoken VISIBLE in this grey (ASS &H00909090&) at 3.2-4.49:1.
# The lander ruled (2026-11-28) that they count as text a viewer reads, so those masters fail correctly; the current
# composer never draws a word still to be spoken (tests/test_capgate_c6_2026_11_28.py,
# test_the_current_composer_never_draws_a_word_still_to_be_spoken). A failing run whose every blocking frame shows
# such a word is marked as failing on that retired design, so the table is read correctly.
RETIRED_PREVIEW_GREY = "909090"
RETIRED_NOTE = "fails on the retired preview-word design (not drawn by the current composer)"


def _visible_colours(text: str) -> set:
    """The colours (RRGGBB as ASS writes BBGGRR, upper case) of the words a CapText event draws visible."""
    import re
    alpha, colour, out = "00", None, set()
    for tags, chunk in re.findall(r"\{([^}]*)\}|([^{]+)", text):
        if tags:
            for a in re.findall(r"\\alpha&H([0-9A-Fa-f]{2})&", tags.split("\\t(")[0]):
                alpha = a.upper()
            if re.search(r"\\t\([^)]*\\alpha&H00&", tags):
                alpha = "00"
            for c in re.findall(r"\\c&H([0-9A-Fa-f]{6,8})&", tags):
                colour = c.upper()[-6:]
        elif chunk.strip() and alpha != "FF" and colour:
            out.add(colour)
    return out


def retired_preview(ass: Path, fps: float, under: list) -> bool:
    """True when every frame of every blocking reading shows a word drawn visible in RETIRED_PREVIEW_GREY."""
    events = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:") and ",CapText," in line:
            p = line.split(",", 9)
            s, e = (round(sum(float(x) * k for x, k in zip(v.strip().split(":"), (3600, 60, 1))) * 1000)
                    for v in (p[1], p[2]))
            events.append((s, e, p[9]))
    if not under:
        return False
    for u in under:
        n0 = round(u["from"] * fps)
        for n in range(n0, n0 + int(u["frames"])):
            t = int(n * (1.0 / fps) * 1000)
            if not any(RETIRED_PREVIEW_GREY in _visible_colours(x) for s, e, x in events if s <= t < e):
                return False
    return True


def crop(mp4: Path, t: float, out: Path, ass: Path) -> list[str]:
    """The master frame at `t` and the caption region of it (the plates' union at that time, padded)."""
    import re
    full = out / f"frame_{t:07.3f}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", "-update", "1",
                    str(full)], check=True, timeout=120)
    from PIL import Image
    boxes = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if line.startswith("Dialogue:") and ",CapBox," in line:
            p = line.split(",", 9)
            hms = [sum(float(x) * k for x, k in zip(s.strip().split(":"), (3600, 60, 1))) for s in (p[1], p[2])]
            m = re.search(r"\\pos\((-?\d+),(-?\d+)\)", p[9])
            pts = [int(v) for v in re.findall(r"-?\d+", p[9].split("\\p1}", 1)[-1])]
            if m and pts and hms[0] <= t < hms[1]:
                x, y = int(m.group(1)), int(m.group(2))
                boxes.append((x + min(pts[0::2]), y + min(pts[1::2]), x + max(pts[0::2]), y + max(pts[1::2])))
    shot = [str(full)]
    with Image.open(full) as im:
        if boxes:
            b = (max(0, min(b[0] for b in boxes) - 40), max(0, min(b[1] for b in boxes) - 40),
                 min(im.width, max(b[2] for b in boxes) + 40), min(im.height, max(b[3] for b in boxes) + 40))
            path = out / f"crop_{t:07.3f}.png"
            im.crop(b).save(path)
            shot.append(str(path))
    return shot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--crops", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    dev = Path(os.environ["RR_DEV"]).resolve()
    tree, out = Path(a.tree or HERE.parents[4]).resolve(), Path(a.out).resolve()
    if out == dev or dev in out.parents:
        sys.exit("--out must not be inside the dev checkout (it is read-only)")
    sys.path.insert(0, str(tree))
    from src import video_qa
    from src.brand import TEXT_CONTRAST
    fixed = hasattr(video_qa, "caption_verdict")
    rows, skipped = [], []
    for mp4, ass, comp, ok in masters(dev):
        run = mp4.parent.name
        if a.only and a.only not in run:
            continue
        if not ok:
            skipped.append(f"{run} ({'no captions file' if not ass.is_file() else 'no composition record'})")
            continue
        where = out / run
        if where.exists():
            shutil.rmtree(where)
        t0 = time.time()
        try:
            cap = video_qa.caption_contrast(mp4, ass, where, until=end_card(comp))
        except Exception as e:  # noqa: BLE001 - reported, never a crash of the whole corpus
            rows.append({"run": run, "error": f"{type(e).__name__}: {e}"})
            print(f"{run:55s} ERROR {e}", flush=True)
            continue
        if fixed:
            findings = video_qa.caption_findings(cap, TEXT_CONTRAST)
            why = video_qa.caption_unmeasured(cap) if hasattr(video_qa, "caption_unmeasured") else None
            if why:   # fails closed, as the gate does (lander ruling 2)
                findings.insert(0, f"caption contrast could not be measured ({why})")
            row = {"run": run, "bursts": cap["bursts"], "fully_shown_under": sum(
                1 for s in cap["samples"] if s["contrast"] is not None and s["contrast"] < TEXT_CONTRAST),
                "dips": len(cap["dips"]), "unread": len(cap.get("unread") or []),
                "no_reading_blocks": sum(1 for u in cap["under"] if u["kind"] == "no_reading"),
                "badge": None if not cap.get("badge") else {
                    "intervals": cap["badge"]["bursts"], "min": cap["badge"]["min"],
                    "blocking": len(cap["badge"]["under"])},
                "frames": cap["frames"], "frames_under": cap["frames_under"],
                "blocking": len(cap["under"]), "verdict": "fail" if findings else "pass", "findings": findings,
                "dip_list": [(d["t"], round(d["contrast"], 2), d["frames"]) for d in cap["dips"]],
                "min_fully_shown": None if cap["min"] is None else round(cap["min"], 2)}
            worst = cap["under"][0] if cap["under"] else None
            if findings and not why and retired_preview(ass, float(cap["fps"]), cap["under"]):
                row["note"] = RETIRED_NOTE
        else:
            vals = [s for s in cap["samples"] if s["contrast"] is not None]
            low = [s for s in vals if s["contrast"] < TEXT_CONTRAST]
            worst = min(low, key=lambda s: s["contrast"]) if low else None
            row = {"run": run, "bursts": "sampled 6", "fully_shown_under": f"{len(low)} of {len(vals)} samples",
                   "dips": "-", "frames": len(cap["samples"]), "frames_under": len(low),
                   "verdict": "fail" if low else "pass",
                   "findings": [f"captions: {worst['contrast']:.2f}:1 at {worst['t']:.2f}s"] if worst else [],
                   "min_fully_shown": None if cap["min"] is None else round(cap["min"], 2)}
        row["seconds"] = round(time.time() - t0, 1)
        if a.crops and worst is not None:
            row["crops"] = crop(mp4, float(worst["t"]), where, ass)
        rows.append(row)
        print(f"{run:55s} bursts {row['bursts']!s:>9}  fully-shown under {row['fully_shown_under']!s:>12}  "
              f"dips {row['dips']!s:>3}  unread {row.get('unread', '-')!s:>3}  frames under {row['frames_under']!s:>5}/{row['frames']!s:<5} "
              f"{row['verdict'].upper():4s} ({row['seconds']} s)", flush=True)
        if row.get("note"):
            print(f"    {row['note']}", flush=True)
        for f in row["findings"][:4]:
            print(f"    {f}", flush=True)
    print(f"\n{len(rows)} masters measured ({'every burst, every frame' if fixed else '6 sampled midpoints'}); "
          f"{sum(1 for r in rows if r.get('verdict') == 'pass')} pass, {sum(1 for r in rows if r.get('verdict') == 'fail')} "
          f"fail ({sum(1 for r in rows if r.get('note') == RETIRED_NOTE)} of them on the retired preview-word design), "
          f"{sum(1 for r in rows if 'error' in r)} not measured")
    for s in skipped:
        print(f"skipped: {s}")
    if a.json:
        Path(a.json).write_bytes(json.dumps({"tree": str(tree), "fixed": fixed, "rows": rows, "skipped": skipped},
                                            indent=1).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
