"""QF-139 on real development masters (read only): the delivery pass and the review proxy, on simulated machines,
before and after.

Usage: python real_runs.py <repo root> <work dir> <master.mp4> [<master.mp4> ...]

Masters: each master's picture and sound (decoded from the run's master, read only) is encoded again with the
master's delivery arguments (video_composer.compose_video: the encoder, then yuv420p, BT.709 tags, 30 fps, closed 2 s
GOP, AAC 192k 48 kHz stereo, faststart) on three simulated machines: 32 logical processors (the development PC), 6
(the production VM) and 4 (GitHub's standard Linux runner for a public repository). A machine is simulated as in
measure.py: os.cpu_count() and the process affinity (every ffmpeg inherits it). Two argument sets:
- machine-derived (the parent, as QF-134 left it): libx264 slow CRF 18 then "-threads <os.cpu_count()>";
- fixed (this row): encoding.delivery_video_encoder() of <repo root> (libx264 slow CRF 18, -threads 6).
On the production VM's 6 processors the two are the same command. Indicative cost for the clips (graphic, footage,
motion, then-and-now), which took x264's own 9 threads there: the same master at -threads 9 against 6 on 6 processors.

Review proxy (rate-capped: -maxrate/-bufsize; a real master is far above its cap): the parent's proxy command
(-threads os.cpu_count()) three times on 32 processors and once on 6, and this row's
video_composer.VideoComposer.make_review_proxy (one x264 thread) three times on 32, once on 6 and once on 4.

Prints JSON: SHA-256, the x264 threads written into the stream, the size, wall time and CPU time of every encode
(wall time depends on what else the host runs; CPU time much less). No run or topic names (masters are numbered in
the order given).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from measure import _host_processors, _restrict, stream_threads, x264_build

MACHINES = {"development PC (32)": 32, "production VM (6)": 6, "CI runner (4)": 4}
TAIL = ["-pix_fmt", "yuv420p", "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", "-r", "30", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-flags", "+cgop",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-max_muxing_queue_size", "8192",
        "-movflags", "+faststart"]
PARENT_ENCODER = ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]


def encode(master: Path, out: Path, encoder: list[str], processors: int, count: int, host: int) -> dict:
    _restrict(min(processors, host))
    real = os.cpu_count
    os.cpu_count = lambda: count
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-threads", str(os.cpu_count() or 0),
               "-i", str(master), "-map", "0:v:0", "-map", "0:a:0", *encoder, *TAIL, str(out)]
        start = time.perf_counter()
        process = subprocess.Popen(cmd)
        cpu = _wait_cpu_seconds(process)
        seconds = time.perf_counter() - start
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {process.returncode}")
    finally:
        os.cpu_count = real
        _restrict(host)
    data = out.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "x264": stream_threads(out), "bytes": len(data),
            "encode_s": round(seconds, 1), "cpu_s": round(cpu, 1), "affinity_used": min(processors, host)}


def _wait_cpu_seconds(process: subprocess.Popen) -> float:
    """Waits for `process`; returns the CPU time (user + kernel) it used."""
    if sys.platform != "win32":
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        process.wait(timeout=7200)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    import ctypes
    from ctypes import wintypes
    process.wait(timeout=7200)
    times = [wintypes.FILETIME() for _ in range(4)]
    ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(int(process._handle)), *map(ctypes.byref, times))
    return sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in times[2:]) / 1e7


def proxy_parent(master: Path, out: Path, processors: int, host: int) -> dict:
    """The parent's review proxy command (video_composer.make_review_proxy before this row), timed."""
    seconds = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                             "default=noprint_wrappers=1:nokey=1", str(master)]).decode().strip())
    kbps = max(400, min(1500, int(44 * 8 * 1024 / max(seconds, 1)) - 128))
    count = str(processors)
    command = ["-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'", "-c:v", "libx264", "-preset", "fast",
               "-crf", "28", "-threads", count, "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k", "-pix_fmt",
               "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    return _timed(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-threads", count, "-i", str(master),
                   *command, str(out)], out, processors, processors, host)


def proxy_fixed(master: Path, out: Path, processors: int, host: int) -> dict:
    """This row's review proxy: the repository's own make_review_proxy, timed."""
    from src.video_composer import VideoComposer
    _restrict(min(processors, host))
    real = os.cpu_count
    os.cpu_count = lambda: processors
    try:
        start = time.perf_counter()
        VideoComposer.make_review_proxy(master, out)
        seconds = time.perf_counter() - start
    finally:
        os.cpu_count = real
        _restrict(host)
    data = out.read_bytes()
    out.unlink()
    return {"sha256": hashlib.sha256(data).hexdigest(), "x264": stream_threads_bytes(data), "bytes": len(data),
            "encode_s": round(seconds, 1)}


def stream_threads_bytes(data: bytes) -> str:
    found = re.search(rb"threads=(\d+) lookahead_threads=(\d+)", data[: 16 << 20])
    return f"threads={found.group(1).decode()}" if found else "?"


def _timed(cmd: list[str], out: Path, processors: int, count: int, host: int) -> dict:
    _restrict(min(processors, host))
    real = os.cpu_count
    os.cpu_count = lambda: count
    try:
        start = time.perf_counter()
        process = subprocess.Popen(cmd)
        cpu = _wait_cpu_seconds(process)
        seconds = time.perf_counter() - start
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {process.returncode}")
    finally:
        os.cpu_count = real
        _restrict(host)
    data = out.read_bytes()
    out.unlink()
    return {"sha256": hashlib.sha256(data).hexdigest(), "x264": stream_threads_bytes(data), "bytes": len(data),
            "encode_s": round(seconds, 1), "cpu_s": round(cpu, 1), "affinity_used": min(processors, host)}


def main(root: Path, work: Path, masters: list[Path]) -> dict:
    sys.path.insert(0, str(root))
    os.chdir(root)
    from src.encoding import delivery_video_encoder
    fixed = delivery_video_encoder()
    host = _host_processors()
    work.mkdir(parents=True, exist_ok=True)
    result = {"host_logical_processors": host, "fixed_encoder": fixed, "masters": []}
    for number, master in enumerate(masters, 1):
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height,nb_frames:format=duration", "-of", "json", str(master)],
                               capture_output=True, text=True, check=True).stdout
        info = json.loads(probe)
        stream = info["streams"][0]
        x264 = "?"
        runs: dict = {"machine-derived (parent)": {}, "fixed (QF-139)": {}}
        for name, count in MACHINES.items():
            out = work / f"m{number}_parent_{count}.mp4"
            runs["machine-derived (parent)"][name] = encode(master, out, [*PARENT_ENCODER, "-threads", str(count)],
                                                            count, count, host)
            out.unlink()
            out = work / f"m{number}_fixed_{count}.mp4"
            runs["fixed (QF-139)"][name] = encode(master, out, fixed, count, count, host)
            x264 = x264_build(out)
            out.unlink()
        out = work / f"m{number}_clip_auto_6.mp4"
        clips_before = encode(master, out, [*PARENT_ENCODER, "-threads", "9"], 6, 6, host)
        out.unlink()
        proxies: dict = {"machine-derived (parent)": [], "fixed (QF-139, one thread)": []}
        for processors in (32, 32, 32, 6):
            proxies["machine-derived (parent)"].append(
                {"processors": processors,
                 **proxy_parent(master, work / f"m{number}_proxy_parent.mp4", processors, host)})
        for processors in (32, 32, 32, 6, 4):
            proxies["fixed (QF-139, one thread)"].append(
                {"processors": processors,
                 **proxy_fixed(master, work / f"m{number}_proxy_fixed.mp4", processors, host)})
        parent, fixed_runs = runs["machine-derived (parent)"], runs["fixed (QF-139)"]
        on_vm = fixed_runs["production VM (6)"]
        result["masters"].append({
            "master": number,
            "picture": f"{stream['width']}x{stream['height']}",
            "seconds": round(float(info["format"]["duration"]), 1),
            "x264": x264,
            "distinct_files": {"machine-derived (parent)": len({r["sha256"] for r in parent.values()}),
                               "fixed (QF-139)": len({r["sha256"] for r in fixed_runs.values()})},
            "fixed_equals_parent_on_production_vm": on_vm["sha256"] == parent["production VM (6)"]["sha256"],
            "runs": runs,
            "clips_on_production_vm (indicative, this master)": {
                "x264's own 9 threads (before)": {k: clips_before[k] for k in ("encode_s", "cpu_s", "bytes")},
                "fixed 6": {k: on_vm[k] for k in ("encode_s", "cpu_s", "bytes")}},
            "cost_on_development_pc": {"fixed": {k: fixed_runs["development PC (32)"][k] for k in ("encode_s", "cpu_s")},
                                       "parent (-threads 32)": {k: parent["development PC (32)"][k]
                                                                for k in ("encode_s", "cpu_s")}},
            "review_proxy": {
                "distinct_files": {k: len({r["sha256"] for r in v}) for k, v in proxies.items()},
                "runs": proxies},
        })
    return result


if __name__ == "__main__":
    root_arg, work_arg, *master_args = sys.argv[1:]
    print(json.dumps(main(Path(root_arg).resolve(), Path(work_arg), [Path(m) for m in master_args]), indent=2))
