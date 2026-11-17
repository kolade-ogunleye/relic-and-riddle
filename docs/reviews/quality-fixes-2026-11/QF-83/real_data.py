"""QF-83 real-data run: the loop bridge's FOLLOW over real Reel masters, development and (counts only) production.

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]
                        [--render <dir>] [--prod-metrics <copy of the sample's metrics.json>]

For every 9:16 master with an ending (its composition record's end_card start_s), the narration's last frame and
the Reel's frame 0 are read back from the master with FFmpeg (the composer's own inputs to the bridge: the graded
last frame and the delivered frame 0, captions included) and the bridge is rendered twice, purely visual and with
the FOLLOW, on the composer's still motion (no push: the masters predate QF-22's recorded seam motion). Development:
the latest master per topic, each measured by name. Production (a COPY of the sample, never the original): counts and
medians only (decisions\\PROD-SAMPLE-counts-only.md). `--render <dir>` (development masters only, never
production): `pair_<topic>.jpg`, bridge frame 14 at half size before (purely visual) and after (with the FOLLOW), and
`strip_<topic>.jpg`, the FOLLOW bridge's frames 0, 6, 14, 20, 26 and 29 at quarter size (its timing). Prints JSON; writes only temporary frames under the
system temp folder, and the strips when asked. `--prod-metrics`: how many YouTube Short publications and manual
thumbnail sets (channel "youtube:thumbnail") the sample records, by status (counts only).
"""

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

STRIP = (0, 6, 14, 20, 26, 29)
PAIR = 14   # fully up, mid-crossfade: both pictures under it


def masters(folder: Path, latest_per_topic: bool):
    found = []
    for comp in sorted(folder.glob("run_*/*_master.composition.json")):
        try:
            record = json.loads(comp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        start = (record.get("end_card") or {}).get("start_s")
        video = comp.with_name(comp.name.replace(".composition.json", ".mp4"))
        if start and video.is_file() and record.get("width", 1080) < record.get("height", 1920):
            found.append((comp.parent.name, video, float(start)))
    if latest_per_topic:
        by_topic = {}
        for run, video, start in found:
            topic, stamp = run[4:].rsplit("_", 1)
            if topic not in by_topic or stamp > by_topic[topic][0]:
                by_topic[topic] = (stamp, run, video, start)
        found = [v[1:] for _, v in sorted(by_topic.items())]
    return found


def frames_of(video: Path, start: float, tmp: Path):
    last, first = tmp / "last.png", tmp / "first.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-frames:v", "1", str(first)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start - 2 / 30):.3f}", "-i", str(video),
                    "-frames:v", "1", str(last)], check=True)
    with Image.open(last) as a, Image.open(first) as b:
        return a.convert("RGB"), b.convert("RGB")


def measure(end_card, last, first, render=None):
    kw = {"line": False, "close_push": 0.0, "open_push": 0.0}
    plain, follow = {}, {}
    before = after = None
    for i, frame in enumerate(end_card.loop_bridge(last, first, "", plain, **kw)):
        if i == PAIR:
            before = frame.resize((540, 960), Image.LANCZOS)
    shown = []
    for i, frame in enumerate(end_card.loop_bridge(last, first, "", follow, follow=True, **kw)):
        if i in STRIP:
            shown.append(frame.resize((270, 480), Image.LANCZOS))
        if i == PAIR:
            after = frame.resize((540, 960), Image.LANCZOS)
    if render is not None:
        pair = Image.new("RGB", (1080, 960))
        pair.paste(before, (0, 0))
        pair.paste(after, (540, 0))
        pair.save(render[0], quality=84)
        sheet = Image.new("RGB", (270 * len(shown), 480))
        for j, im in enumerate(shown):
            sheet.paste(im, (270 * j, 0))
        sheet.save(render[1], quality=84)
    return {"follow": follow["follow"], "contrast": follow["contrast"], "halo": follow.get("follow_halo"),
            "frames_up": follow["follow_frames"], "skipped": follow.get("follow_skipped"),
            "box": follow.get("follow_box"), "seam_plain": end_card.seam_problems(plain),
            "seam_follow": end_card.seam_problems(follow),
            "max_step_L": [plain["max_step_L"], follow["max_step_L"]], "mad_last": [plain["mad_last"], follow["mad_last"]]}


def summary(rows):
    drawn = [r for r in rows if r["follow"]]
    contrasts = [r["contrast"] for r in drawn]
    return {"masters": len(rows), "follow_drawn": len(drawn), "follow_skipped": len(rows) - len(drawn),
            "contrast_min": min(contrasts) if contrasts else None,
            "contrast_median": round(statistics.median(contrasts), 2) if contrasts else None,
            "seam_problems_plain": sum(bool(r["seam_plain"]) for r in rows),
            "seam_problems_follow": sum(bool(r["seam_follow"]) for r in rows),
            "follow_added_a_seam_problem": sum(set(r["seam_follow"]) - set(r["seam_plain"]) != set() for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--dev", type=Path)
    ap.add_argument("--prod", type=Path)
    ap.add_argument("--render", type=Path)
    ap.add_argument("--prod-metrics", type=Path)
    args = ap.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from src import end_card
    out = {"follow_supported": "follow" in end_card.loop_bridge.__code__.co_varnames}
    with tempfile.TemporaryDirectory(prefix="qf83-") as tmp:
        if args.dev:
            rows = {}
            for run, video, start in masters(args.dev, latest_per_topic=True):
                last, first = frames_of(video, start, Path(tmp))
                topic = run[4:].rsplit("_", 1)[0]
                render = (args.render / f"pair_{topic}.jpg", args.render / f"strip_{topic}.jpg") if args.render else None
                rows[run] = measure(end_card, last, first, render)
            out["dev"] = {"summary": summary(list(rows.values())), "runs": rows}
        if args.prod:
            rows = []
            for _, video, start in masters(args.prod, latest_per_topic=False):
                rows.append(measure(end_card, *frames_of(video, start, Path(tmp))))
            out["prod_counts"] = summary(rows)
        if args.prod_metrics:
            pubs = json.loads(args.prod_metrics.read_text(encoding="utf-8")).get("publications") or []
            counts = {}
            for p in pubs:
                if p.get("channel") in ("youtube-shorts", "youtube:thumbnail"):
                    key = f"{p['channel']}:{p.get('status')}"
                    counts[key] = counts.get(key, 0) + 1
            out["prod_publication_counts"] = dict(sorted(counts.items()))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
