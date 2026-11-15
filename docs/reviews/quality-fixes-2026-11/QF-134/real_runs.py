"""QF-134 on real development runs (read only): which encoder made the delivered files, and a full-length re-render.

Usage: python real_runs.py <repo root> <output dir> <work dir> [runs]

1. Scan: every master (Reel and documentary) and every graphic clip in <output dir>/run_*/ (stage caches skipped),
   classified by the encoder written into its stream (libx264 records its settings there; NVENC does not).
2. Re-render: for the newest [runs] (default 2) Reels that have their parallax clips, narration, bed and words, the
   master is composed again with the code at <repo root> on this machine's real hardware (no mock): the run's own
   scene clips, narration, bed and words, ending on the loop bridge, scenes sharing the narration equally. The clips
   are copied into <work dir> first, so nothing is written into the run folder. Records the wall time of the
   compose, the encoder of every ffmpeg video encode (paths dropped), the master's stream encoder and the manifest's
   encoder record. Run once on the parent commit and once on the row's commit to compare. Prints counts and timings,
   no run or topic names.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def stream_encoder(path: Path) -> str:
    head = path.read_bytes()[: 8 << 20]
    if b"x264 - core" in head:
        preset = "slow" if b"subme=8" in head else ("veryfast" if b"subme=2" in head else "other")
        crf = re.search(rb"crf=([\d.]+)", head)
        return f"libx264 ({preset}-class, crf {crf.group(1).decode() if crf else '?'})"
    return "NVENC (no x264 settings in the stream)"


def scan(output: Path) -> dict:
    counts: dict = collections.defaultdict(collections.Counter)
    for path in output.glob("run_*/**/*.mp4"):
        if "stage_cache" in path.parts:
            continue
        name = path.name
        if name.endswith("_master.mp4") or "documentary" in name:
            kind = "master"
        elif name.startswith("graphic_"):
            kind = "graphic clip"
        elif path.parent.name in ("cards", "design_cards") or name.startswith(("hook_", "chapter_", "myth_")):
            kind = "design card"
        else:
            continue
        counts[kind]["NVENC" if stream_encoder(path).startswith("NVENC") else "libx264"] += 1
    return {k: dict(v) for k, v in counts.items()}


def duration(path: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                          "default=noprint_wrappers=1:nokey=1", str(path)]).decode().strip())


def rerender(root: Path, output: Path, work: Path, runs: int) -> list:
    sys.path.insert(0, str(root))
    os.chdir(root)
    from src import video_composer as vc

    chosen = []
    for run in sorted(output.glob("run_*"), key=lambda p: p.name.rsplit("_", 1)[-1], reverse=True):
        clips = sorted((run / "parallax").glob("scene_*.mp4"))
        need = [run / "voiceover.mp3", run / "ambient_bed.wav", run / "words.json"]
        if len(clips) >= 4 and all(p.is_file() for p in need) and list(run.glob("*_master.mp4")):
            chosen.append((run, clips))
        if len(chosen) == runs:
            break
    results = []
    real_popen = subprocess.Popen
    encodes: list = []

    class Recording(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and str(cmd[0]) == "ffmpeg" and "-c:v" in cmd:
                at = cmd.index("-c:v")
                if cmd[at + 1] != "copy":
                    encodes.append(" ".join(str(c) for c in cmd[at:at + 6]))
            super().__init__(cmd, *args, **kwargs)

    for index, (run, clips) in enumerate(chosen, 1):
        case = work / f"case{index}"
        shutil.rmtree(case, ignore_errors=True)
        case.mkdir(parents=True)
        local = []
        for clip in clips:
            shutil.copy2(clip, case / clip.name)
            local.append(case / clip.name)
        voice = case / "voiceover.mp3"
        bed = case / "ambient_bed.wav"
        shutil.copy2(run / "voiceover.mp3", voice)
        shutil.copy2(run / "ambient_bed.wav", bed)
        words = json.loads((run / "words.json").read_text(encoding="utf-8"))
        vo_dur = duration(voice)
        master = case / "master.mp4"
        encodes.clear()
        subprocess.Popen = Recording
        try:
            start = time.perf_counter()
            vc.VideoComposer().compose_video(local, voice, bed, words, vo_dur, master,
                                             durations=[vo_dur / len(local)] * len(local),
                                             end_card="Follow for more")
            seconds = time.perf_counter() - start
        finally:
            subprocess.Popen = real_popen
        manifest = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))
        results.append({"case": index, "scenes": len(local), "narration_s": round(vo_dur, 2),
                        "master_s": round(duration(master), 2), "compose_wall_s": round(seconds, 1),
                        "video_encodes": encodes[:], "master_stream": stream_encoder(master),
                        "manifest_video_encoder": manifest.get("video_encoder"),
                        "end_card": (manifest.get("end_card") or {}).get("variant")})
        shutil.rmtree(case, ignore_errors=True)
    return results


if __name__ == "__main__":
    root, output, work = (Path(a).resolve() for a in sys.argv[1:4])
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    print(json.dumps({"scan": scan(output), "rerender": rerender(root, output, work, count)}, indent=2))
