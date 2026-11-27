"""Corpus proof, freeze cycle 6 round 2 (review B1, B2; panel 7 C4): the AI badge on the FRAMES it covers.

    python captions_c6_badge.py --tree <tree> --dev <checkout holding output/run_*> [--quiet]

Over every graphic clip of every dev run (output/run_*/graphics/graphic_*.mp4), in its own format, three timelines as
compose_video builds them (scene clips back to back, the badge on for a generated illustration's beat and half a
crossfade either side: reconstruction_badge_intervals): an illustration before the graphic ("illus_first"), after it
("graphic_first"), and the FORCED case, the badge on for the whole graphic ("forced", as the board's forced Amber
case). The tree's own production chain decides the badge: clear_badge_intervals, then build_subtitles with the
graphic clips. This script reads the drawn badge back from the ASS (ReconPlate events) and, for each stretch the plate
stands over the graphic, decodes the graphic's frames at the stretch's start, every third frame and its end, and
measures the drawn-ink share (luma over 140) in the plate's box grown by 12 px at 1080 wide: a violation is a share
over 0.5 % on any of those frames (an overprint), a corner change inside one reconstruction interval on a stretch
under 1.0 s, or a drawn stretch under 1.0 s (the badge's minimum dwell) inside a longer interval. Exits 1 on a
violation.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BS = chr(92)
INK_LUMA, INK_SHARE, MIN_S = 140, 0.005, 1.0


def _arg(flag: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


tree = Path(_arg("--tree", str(Path(__file__).resolve().parents[4])))
dev = Path(_arg("--dev", "."))
sys.dont_write_bytecode = True
sys.path.insert(0, str(tree))
import numpy as np  # noqa: E402

from src import video_composer as vc  # noqa: E402


def probe(clip: Path) -> tuple[float, tuple[int, int]] | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", str(clip)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        s = d["streams"][0]
        return float(d["format"]["duration"]), (int(s["width"]), int(s["height"]))
    except (ValueError, KeyError, IndexError):
        return None


def ink(clip: Path, t: float, rect, size) -> float:
    w, h = size
    x0, y0, x1, y1 = max(0, rect[0]), max(0, rect[1]), min(w, rect[2]), min(h, rect[3])
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(clip), "-frames:v", "1",
                        "-vf", f"scale={w}:{h},crop={x1 - x0}:{y1 - y0}:{x0}:{y0}", "-f", "rawvideo", "-pix_fmt",
                        "rgb24", "-"], capture_output=True)
    n = (x1 - x0) * (y1 - y0) * 3
    if len(r.stdout) < n:
        return 0.0
    a = np.frombuffer(r.stdout[:n], np.uint8).reshape(y1 - y0, x1 - x0, 3).astype(np.float32)
    return float(((a @ np.array([0.299, 0.587, 0.114], np.float32)) > INK_LUMA).mean())


def plates(text: str) -> list[tuple[float, float, int, int]]:
    out = []
    for line in text.splitlines():
        if line.startswith("Dialogue:") and ",ReconPlate," in line:
            part = line[len("Dialogue:"):].split(",", 9)
            ts = [sum(float(x) * m for x, m in zip(p.strip().split(":"), (3600, 60, 1))) for p in part[1:3]]
            m = re.search(BS * 2 + r"pos\((\d+),(\d+)\)", part[9])
            out.append((ts[0], ts[1], int(m.group(1)), int(m.group(2))))
    return out


rows, bad, tot = [], [], collections.Counter()
words = [{"word": w, "start": 0.3 + 0.4 * i, "dur": 0.3} for i, w in enumerate(("This is a caption line " * 12).split())]
for run in sorted(dev.glob("output/run_*")):
    for clip in sorted((run / "graphics").glob("graphic_*.mp4")):
        got = probe(clip)
        if got is None:
            tot["graphic clips without decodable video (counted taken by the tree)"] += 1
            continue
        dur, (cw, ch) = got
        w, h = (1920, 1080) if cw > ch else (1080, 1920)
        clear = round(12 * w / (1920 if w > h else 1080))
        with tempfile.TemporaryDirectory() as td:
            gen = Path(td) / "gen_01.png"
            photo = Path(td) / "07_commons.jpg"
            cases = {"illus_first": ([gen, clip, photo], [4.0, dur, 3.0]),
                     "graphic_first": ([photo, clip, gen], [3.0, dur, 4.0])}
            for name, (scenes, durs) in cases.items():
                ivs = vc.reconstruction_badge_intervals(scenes, durs, sum(durs))
                ivs, _ = vc.clear_badge_intervals(ivs, scenes, durs, w, h)
                gclips = vc.caption_graphic_clips(scenes, durs)
                cases[name] = (ivs, gclips)
            cases["forced"] = ([(0.0, dur)], [(0.0, dur, clip)])
            for name, (ivs, gclips) in cases.items():
                ass = Path(td) / f"{name}.ass"
                comp = vc.VideoComposer()
                comp.build_subtitles([dict(x) for x in words], ass, width=w, height=h,
                                     reconstruction_intervals=ivs, graphic_clips=gclips)
                plan = getattr(comp, "last_badge", None) or []
                text = ass.read_text(encoding="utf-8")
                ga, gb = next((a, b) for a, b, c in gclips if Path(c) == clip)
                drawn = plates(text)
                worst, frames = 0.0, 0
                for a, b, x0, y0 in drawn:
                    lo, hi = max(a, ga), min(b, gb)
                    if hi <= lo:
                        continue
                    corner = "top_left" if x0 < w / 2 else "top_right"
                    gx0, gy0, gx1, gy1 = vc.recon_badge_geometry(w, h, corner)["box"]
                    rect = (gx0 - clear, gy0 - clear, gx1 + clear, gy1 + clear)
                    n = max(1, round((hi - lo) * vc.VIDEO_FPS))
                    for f in sorted(set(list(range(0, n, 3)) + [n - 1])):
                        share = ink(clip, lo - ga + f / vc.VIDEO_FPS, rect, (w, h))
                        frames += 1
                        worst = max(worst, share)
                # corner changes and short stretches inside one interval (the tree's own plan, read back from the ASS)
                faults = []
                for a, b in ivs:
                    mine = sorted((s, e, x) for s, e, x, _ in drawn if s >= a - 1e-3 and e <= b + 1e-3)
                    for (s1, e1, x1), (s2, e2, x2) in zip(mine, mine[1:]):
                        if (x1 < w / 2) != (x2 < w / 2) and e2 - s2 < MIN_S - 1e-3:
                            faults.append(f"corner change for {e2 - s2:.2f} s")
                    if len(mine) > 1 or (mine and b - a > MIN_S):
                        faults += [f"stretch of {e - s:.2f} s" for s, e, _ in mine if e - s < MIN_S - 1e-3]
                ok = worst <= INK_SHARE and not faults
                corners = sorted({("top_left" if x < w / 2 else "top_right") for s, e, x, _ in drawn
                                  if max(s, ga) < min(e, gb)})
                row = {"run": run.name[4:], "clip": clip.name, "case": name, "format": "16:9" if w > h else "9:16",
                       "badge_over_graphic_s": round(sum(max(0.0, min(e, gb) - max(s, ga)) for s, e, _, _ in drawn), 2),
                       "corners_over_graphic": corners, "frames_checked": frames, "worst_ink": round(worst, 4),
                       "faults": faults, "ok": ok, "plan": plan}
                rows.append(row)
                tot[f"{name}: cases"] += 1
                tot[f"{name}: badge stays over the graphic"] += row["badge_over_graphic_s"] > 0
                tot[f"{name}: alternative corner used"] += "top_right" in corners
                tot["frames checked"] += frames
                if not ok:
                    bad.append(row)
                    tot["violations UNRECORDED"] += 1
                if "--quiet" not in sys.argv:
                    print(f"{row['run'][:40]:40} {clip.name:24} {name:13} {row['format']:5} over {row['badge_over_graphic_s']:5.2f} s "
                          f"{','.join(corners) or '-':18} frames {frames:3} worst ink {worst:.2%} "
                          f"{'; '.join(faults)} {'pass' if ok else 'FAIL'}", flush=True)
print()
for k in sorted(tot):
    print(f"{k}: {tot[k]}")
out = _arg("--json")
if out:
    Path(out).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8", newline="\n")
print(f"violations: {len(bad)}")
raise SystemExit(1 if bad else 0)
