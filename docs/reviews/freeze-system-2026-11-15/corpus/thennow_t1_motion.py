"""THENNOW T1 (freeze cycle 6; panel 7 MO-T1 / MO-TN-REVERSE, MO2 8.0 on all 12 cases): the then-and-now reveal,
measured on the renderer's own production-encoded clips (then_now._encode: libx264, never NVENC), over every dev pair
in both formats (thennow_corpus.pairs).

    python thennow_t1_motion.py <tree> <dev checkout> <dir> [--render] [--jobs N] [--strips]

--render draws every pair in both formats with THIS tree into <dir> (the heavy step: about a minute a clip, N clips
at once; run it once per tree). Without it the clips and their <clip>.meta.json in <dir> are measured (decoded
frames only, cheap). --strips writes <clip>.strip.png: a frame every 0.1 s across the reveal, from the encode.

Per clip, from the DECODED frames (the encode is what is measured, not the plan):
  divider   the bone rule with its INK keylines found on every frame (row on 9:16, column on 16:9): its position
            never moves back (each frame at or before the last, 1 px of codec tolerance) and never passes the
            resting split (2 px)                                                                       must hold
  markers   each year marker's presence (its plate's pixels against the frame before it entered and the last
            frame): once fully in (>= 0.9) it stays in (never under 0.8), and its entry never steps back
            (0.05); the renderer's own alpha schedule is checked the same way (exact)                 must hold
  luma      the largest mean absolute luma change between two consecutive frames: at most the system's
            per-frame limit (end_card.DIM_STEP_MAX, 8.0 luma, the field's steepest frame)             must hold
Exit 1 on any FAIL.
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import thennow_corpus as tc  # noqa: E402

TOL_BACK = 1.0         # px: the divider's codec tolerance frame to frame
TOL_PAST = 2.0         # px: never past the split by more
IN_FULL, IN_KEEP, IN_STEP = 0.9, 0.8, 0.05


def render_one(tree: Path, dev: Path, out: Path, pid: str, w: int, h: int) -> None:
    then_now = tc.use_tree(tree)
    pair = next(p for p in all_pairs(tree, dev, out, then_now) if p["id"] == pid)
    tag = "916" if h > w else "169"
    clip = out / f"thennow__{pid}__{tag}.mp4"
    unfit = getattr(then_now, "Unfit", None)
    try:
        then_now.render(pair["old"], pair["old_year"], pair["new"], pair["new_year"], clip, w, h)
    except Exception as e:      # round 3 (R4): a registered pair unfit for this format is refused, recorded
        if unfit is None or not isinstance(e, unfit):
            raise
        clip.with_suffix(".refused.json").write_text(json.dumps({"pair": pid, "format": f"{w}x{h}", "refused": str(e)}),
                                                     encoding="utf-8", newline="\n")
        return
    meta = json.loads(json.dumps(then_now.last_meta, default=str))
    n = len(meta["schedule"]["then"])
    text = None
    if hasattr(then_now, "then_text"):
        text = then_now.then_text(pair["old_year"])
        meta["divider"] = [then_now.divider_at(i / then_now.VIDEO_FPS, w, h, text) for i in range(n)]
    else:
        meta["divider"] = [then_now.divider_at(i / then_now.VIDEO_FPS, w, h) for i in range(n)]
    meta.update({"size": [w, h], "split": then_now._split_at(w, h), "fps": then_now.VIDEO_FPS,
                 "pair": pid, "years": [pair["old_year"], pair["new_year"]]})
    clip.with_suffix(".meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8", newline="\n")


def all_pairs(tree: Path, dev: Path, out: Path, then_now) -> list[dict]:
    """Every dev pair (unregistered stand-ins) and every registered test pair (thennow_corpus.registered_pairs, built
    once into <dir>/../thennow_reg_pairs)."""
    return tc.pairs(tree, dev) + tc.registered_pairs(tree, dev, out.parent / "thennow_reg_pairs", then_now)


def frames_of(clip: Path, size: tuple[int, int]):
    import numpy as np
    w, h = size
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


def find_divider(f, axis: int, bone: float):
    """The divider's position on one decoded frame (None when no rule is on it): the line whose three centre lines
    are bone across the frame (the drawn rule is five; 4:2:0 and the encode soften an outer one on some frames)
    with darker keylines 3-4 px either side."""
    import numpy as np
    y = f.astype(np.float32) if axis == 1 else f.T.astype(np.float32)
    y = y[:, 40:-40]
    b = (np.abs(y - bone) < 30).mean(axis=1)
    k = (y < bone - 50).mean(axis=1)
    n = len(b)
    p = np.arange(5, n - 5)
    b3 = np.lib.stride_tricks.sliding_window_view(b, 3).min(axis=1)[p - 1]    # 3 of its 5 lines: the encode
    s = np.minimum(b3, np.minimum(np.maximum(k[p - 4], k[p - 3]), np.maximum(k[p + 3], k[p + 4])))   # softens one
    j = int(np.argmax(s))
    return int(p[j]) if s[j] >= 0.6 else None


def presence(fr, box, bare_i: int, last_i: int):
    import numpy as np
    x0, y0, x1, y1 = (max(0, int(v)) for v in box)
    fin = fr[last_i][y0:y1, x0:x1].astype(np.float32)
    bare = fr[bare_i][y0:y1, x0:x1].astype(np.float32)
    m = np.abs(fin - bare) > 40
    if m.sum() < 50:
        return None
    den = float(np.median(np.abs(bare - fin)[m]))
    return [1.0 - float(np.median(np.abs(fr[i][y0:y1, x0:x1].astype(np.float32) - fin)[m])) / den
            for i in range(len(fr))]


def monotone_in(vals, tol_step: float, full: float, keep: float) -> tuple[bool, str]:
    first_full = next((i for i, v in enumerate(vals) if v >= full), None)
    if first_full is None:
        return False, "never fully in"
    after = min(vals[first_full:])
    entry = [vals[i] - vals[i - 1] for i in range(1, first_full + 1)]
    back = min(entry) if entry else 0.0
    ok = after >= keep and back >= -tol_step
    return ok, f"in at f{first_full}, least after {after:.2f}, largest step back in its entry {-min(0.0, back):.2f}"


def measure(clip: Path, strips: bool) -> list[tuple]:
    import numpy as np
    meta = json.loads(clip.with_suffix(".meta.json").read_text(encoding="utf-8"))
    w, h = meta["size"]
    axis = 0 if w > h else 1
    edge, split = (w, h)[axis], meta["split"]
    fr = frames_of(clip, (w, h))
    n = len(fr)
    last = fr[-1]
    line = last[split - 2:split + 3, :] if axis == 1 else last[:, split - 2:split + 3].T
    bone = float(np.median(line))
    rows = []
    pos, seen = [], False
    for i in range(n):
        p = find_divider(fr[i], axis, bone)
        if p is None:
            p = float(edge) if not seen else float("nan")
        else:
            seen = True
        pos.append(float(p))
    lost = sum(1 for p in pos if p != p)
    back = max((pos[i] - pos[i - 1] for i in range(1, n) if pos[i] == pos[i] and pos[i - 1] == pos[i - 1]),
               default=0.0)
    past = split - min(p for p in pos if p == p)
    plan = meta["divider"]
    off = max(abs(p - q) for p, q in zip(pos, plan) if p == p and 4 < q < edge - 4) if any(
        4 < q < edge - 4 for q in plan) else 0.0
    ok = lost == 0 and back <= TOL_BACK and past <= TOL_PAST
    rows.append((clip.stem, "divider", f"largest step back {max(0.0, back):.1f} px, past the split {max(0.0, past):.1f}"
                 f" px, lost on {lost} frames, off the plan by <= {off:.1f} px", "PASS" if ok else "FAIL"))
    for which in ("then", "now"):
        sched = meta["schedule"][which]
        ok_s, why_s = monotone_in(sched, 1e-9, 0.999, 0.999)
        rows.append((clip.stem, f"{which} schedule", why_s, "PASS" if ok_s else "FAIL"))
        box = meta["groups"][which]
        zero = [i for i, a in enumerate(sched) if a <= 0]
        if which == "then":
            bare_i = zero[0] if zero else 0
        else:          # the last frame before it enters on which the NOW photo is already under its whole box
            lo = box[0] if axis == 0 else box[1]
            under = [i for i in zero if plan[i] <= lo]
            bare_i = under[-1] if under else (zero[-1] if zero else 0)
        pres = presence(fr, box, bare_i, n - 1)
        if pres is None:
            rows.append((clip.stem, f"{which} decoded", "no plate pixels to read", "FAIL"))
            continue
        ok_d, why_d = monotone_in(pres[bare_i:], IN_STEP, IN_FULL, IN_KEEP)
        rows.append((clip.stem, f"{which} decoded", why_d + f" (from f{bare_i})", "PASS" if ok_d else "FAIL"))
    mad = [float(np.abs(fr[i].astype(np.int16) - fr[i - 1].astype(np.int16)).mean()) for i in range(1, n)]
    worst = int(np.argmax(mad)) + 1
    limit = 8.0
    rows.append((clip.stem, "luma", f"largest frame-to-frame mean |dY| {max(mad):.2f} at f{worst} (limit {limit})",
                 "PASS" if max(mad) <= limit else "FAIL"))
    if strips:
        write_strip(clip, fr, plan, meta, edge)
    return rows


def write_strip(clip: Path, fr, plan, meta, edge) -> None:
    """A frame every 0.1 s across the reveal (from 0.3 s before the divider moves to 0.8 s after it lands), from the
    decoded encode, each labelled with its time; one row of the clip's first frame and last frame for reference."""
    from PIL import Image, ImageDraw
    fps = meta["fps"]
    moving = [i for i in range(1, len(plan)) if abs(plan[i] - plan[i - 1]) > 0.01]
    i0 = max(0, (moving[0] if moving else 0) - round(0.3 * fps))
    i1 = min(len(fr) - 1, (moving[-1] if moving else len(fr) - 1) + round(0.8 * fps))
    idx = list(range(i0, i1 + 1, round(0.1 * fps)))
    h, w = fr[0].shape
    tw = 180 if h > w else 240
    th = round(tw * h / w)
    cols = 10
    rows_n = (len(idx) + cols - 1) // cols
    sheet = Image.new("L", (cols * tw, rows_n * (th + 16)), 0)
    d = ImageDraw.Draw(sheet)
    for k, i in enumerate(idx):
        im = Image.fromarray(fr[i]).resize((tw, th), Image.BILINEAR)
        x, y = (k % cols) * tw, (k // cols) * (th + 16)
        sheet.paste(im, (x, y + 16))
        d.text((x + 4, y + 2), f"{i / fps:.1f}s f{i}", fill=255)
    sheet.save(clip.with_suffix(".strip.png"))


def main() -> int:
    tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    if "--one" in sys.argv:
        k = sys.argv.index("--one")
        render_one(tree, dev, out, sys.argv[k + 1], int(sys.argv[k + 2]), int(sys.argv[k + 3]))
        return 0
    if "--render" in sys.argv:
        jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
        out.mkdir(parents=True, exist_ok=True)
        then_now = tc.use_tree(tree)
        todo = [(p["id"], w, h) for p in all_pairs(tree, dev, out, then_now) for w, h, _ in tc.FORMATS]

        def run(job):
            pid, w, h = job
            r = subprocess.run([sys.executable, __file__, str(tree), str(dev), str(out), "--one", pid, str(w), str(h)],
                               capture_output=True, text=True)
            print(f"rendered {pid} {w}x{h}: exit {r.returncode}", flush=True)
            if r.returncode:
                print(r.stderr[-1500:], flush=True)
            return r.returncode
        with ThreadPoolExecutor(jobs) as ex:
            if any(list(ex.map(run, todo))):
                return 2
    import figures_corpus as fc
    refused = sorted(out.glob("thennow__*.refused.json"))
    for r in refused:
        rec = json.loads(r.read_text(encoding="utf-8"))
        print(f"refused  {rec['pair']}  {rec['format']}: {rec['refused']}")
    rows = [r for clip in sorted(out.glob("thennow__*.mp4")) for r in measure(clip, "--strips" in sys.argv)]
    print(fc.table(rows, ("clip", "part", "measure", "verdict")))
    fails = sum(1 for r in rows if r[3] == "FAIL")
    print(f"\nTHENNOW T1: {len(rows)} rows over {len(list(out.glob('thennow__*.mp4')))} clips, FAIL {fails}; "
          f"{len(refused)} formats refused (recorded unfit)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
