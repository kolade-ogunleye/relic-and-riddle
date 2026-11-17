"""QF-139 acceptance: every libx264 encode gives the same bytes on machines with different core counts.

Usage: python measure.py <repo root>

x264's output depends on its thread count. Unless told, it takes the count from the machine (1.5 threads per logical
processor the process may run on), and before this row the master pass was told os.cpu_count(). This script runs
every encode path of the repository at <repo root> that writes libx264 into the delivered picture or the review
proxy, as four simulated machines: 32 logical processors (the development PC), 6 (the production VM: its masters
carry threads=6 from os.cpu_count(), its graphic clips x264's own 9), 4 (GitHub's standard Linux runner for a public
repository) and 2. A machine is simulated in this one process by both things the code and
the encoder read: os.cpu_count() returns its count, and the process (so every ffmpeg it starts) is restricted to that
many logical processors (SetProcessAffinityMask on Windows, sched_setaffinity on Linux), which is where ffmpeg and
x264 count cores. On a host with fewer processors than a machine needs, the affinity is capped at the host's and
the record says so.

Surfaces (inputs generated with fixed seeds, encoded once losslessly before the machines; nothing gitignored):
graphic clip (graphics._encode: graphics, design cards, callouts), then-and-now clip (then_now._encode), footage
segment (footage.make_segment), motion clip (motion._finish), Reel master ending on the loop bridge (body
intermediate and delivery pass), 16:9 documentary master, micro-patched master (splice_single_beat) and the review
proxy, made three times on every machine from a busy clip whose bitrate exceeds the proxy's rate cap
(-maxrate/-bufsize), where a frame-threaded x264 differs run to run. Parallax clips (parallax.render_clip) need the depth model and are covered by the regression test's command
check instead.

Prints JSON: per surface and machine the file's SHA-256 and the x264 thread count written into the stream; the
surfaces whose bytes differ between machines or runs (before > 0, after 0); the proxy's bitrate against its cap;
and the x264 build, since identical bytes need the same ffmpeg/x264 build (and CPU instruction set) on both
machines.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MACHINES = {"development PC (32)": 32, "production VM (6)": 6, "CI runner (4)": 4, "small runner (2)": 2}
PROXY_RUNS = 3


def _host_processors() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    kernel.GetProcessAffinityMask(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(process),
                                  ctypes.byref(system))
    return system.value.bit_count()


def _restrict(count: int) -> None:
    """This process, and every process it starts from now on, runs on the first `count` logical processors."""
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, range(count))
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    if not kernel.SetProcessAffinityMask(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.c_size_t((1 << count) - 1)):
        raise OSError("SetProcessAffinityMask failed")


def stream_threads(path: Path) -> str:
    """x264's settings as written into the stream: its thread counts and the build."""
    head = path.read_bytes()[: 16 << 20]
    threads = re.search(rb"threads=(\d+) lookahead_threads=(\d+) sliced_threads=(\d+)", head)
    return (f"threads={threads.group(1).decode()} lookahead_threads={threads.group(2).decode()}"
            if threads else "no x264 settings in the stream")


def x264_build(path: Path) -> str:
    found = re.search(rb"x264 - core (\d+ r\d+ [0-9a-f]+)", path.read_bytes()[: 16 << 20])
    return found.group(1).decode() if found else "?"


def run(root: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix="qf139_measure_"))
    try:
        return _run(root, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(root: Path, work: Path) -> dict:
    sys.path.insert(0, str(root))
    os.chdir(root)
    import numpy as np
    from PIL import Image

    from src import end_card as card_mod
    from src import footage, graphics, motion, then_now
    from src import video_composer as vc

    # The motion clip's acceptance gates (geometry, fidelity, visible motion) judge a real clip, not this noise; the
    # encode before them is what is measured.
    motion._gate = lambda *args, **kwargs: {}
    host = _host_processors()
    real_cpu_count = os.cpu_count

    # Inputs, made once before any machine (lossless, so they are the same pictures for every machine).
    inputs = work / "inputs"
    inputs.mkdir()
    rng = np.random.default_rng(139)
    scenes = []
    for i in range(3):
        path = inputs / f"scene{i}.png"
        # a textured picture (coarse noise, smoothly enlarged): a photograph's detail, at a photograph's bitrate
        coarse = Image.fromarray(rng.integers(0, 255, (96, 54, 3), dtype=np.uint8))
        coarse.resize((1080, 1920), Image.Resampling.BICUBIC).save(path)
        scenes.append(path)
    voice, bed = inputs / "voice.wav", inputs / "bed.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-t", "3.2", str(voice)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000",
                    "-t", f"{3.2 + card_mod.LOOP_SECONDS + 1:.2f}", str(bed)], check=True)
    film = inputs / "film.mkv"   # stands in for downloaded footage and a generated motion clip (FFV1: lossless)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=7",
                    "-vf", "noise=alls=4:allf=t", "-c:v", "ffv1", "-threads", "1", str(film)], check=True)
    busy = inputs / "busy.mkv"   # the proxy's source: far above the proxy's 1.5 Mbit/s cap at CRF 28
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=6",
                    "-vf", "noise=alls=30:allf=t", "-c:v", "ffv1", "-threads", "1", str(busy)], check=True)
    words = [{"word": w, "start": i * 0.7, "dur": 0.6} for i, w in enumerate(["Same", "bytes", "everywhere"])]
    frames = [Image.open(scenes[i % 3]).convert("RGB") for i in range(30)]

    def machine(count: int) -> dict:
        out = work / f"m{count}"
        out.mkdir()
        used = min(count, host)
        _restrict(used)
        os.cpu_count = lambda: count
        surfaces = {}
        try:
            def surface(name, make, output):
                make()
                surfaces[name] = {"sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                                  "x264": stream_threads(output)}

            surface("graphic clip", lambda: graphics._encode(iter(frames), out / "graphic.mp4", 30), out / "graphic.mp4")
            surface("then-and-now clip", lambda: then_now._encode(iter(frames), out / "then_now.mp4", 30),
                    out / "then_now.mp4")
            surface("footage segment", lambda: footage.make_segment(film, footage.probe(film), out / "footage.mp4"),
                    out / "footage.mp4")
            raw = out / "motion_raw.mkv"   # _finish consumes its raw clip
            shutil.copyfile(film, raw)
            surface("motion clip", lambda: motion._finish(raw, out / "motion.mp4"), out / "motion.mp4")
            reel = out / "reel_master.mp4"
            surface("reel master with loop bridge",
                    lambda: vc.VideoComposer().compose_video(scenes, voice, bed, words, 3.0, reel,
                                                             durations=[1.0, 1.0, 1.0], end_card="Follow for more"),
                    reel)
            wide = out / "documentary_master.mp4"
            surface("documentary master",
                    lambda: vc.VideoComposer().compose_deep_dive(scenes, voice, bed, words, 3.0, wide,
                                                                 durations=[1.0, 1.0, 1.0]), wide)
            patched = out / "patched_master.mp4"
            surface("micro-patched master",
                    lambda: vc.VideoComposer.splice_single_beat(reel, 1, scenes[0], [1.0, 1.0, 1.0], patched), patched)
            runs = []
            for run_number in range(PROXY_RUNS):
                proxy = out / f"review_proxy_{run_number}.mp4"
                vc.VideoComposer.make_review_proxy(busy, proxy)
                runs.append(hashlib.sha256(proxy.read_bytes()).hexdigest())
            surfaces["review proxy (rate-capped)"] = {"sha256": runs[0], "runs": runs, "x264": stream_threads(proxy),
                                                      "kbps": round(proxy.stat().st_size * 8 / 1000 / 6.0)}
            build = x264_build(reel)
        finally:
            os.cpu_count = real_cpu_count
            _restrict(host)
        return {"logical_processors_simulated": count, "affinity_used": used, "surfaces": surfaces, "x264": build}

    machines = {name: machine(count) for name, count in MACHINES.items()}
    names = list(next(iter(machines.values()))["surfaces"])
    differ = sorted(n for n in names if len({h for m in machines.values()
                                             for h in m["surfaces"][n].get("runs", [m["surfaces"][n]["sha256"]])}) > 1)
    proxy = "review proxy (rate-capped)"
    return {
        "host_logical_processors": host,
        "x264_build": sorted({m["x264"] for m in machines.values()}),
        "surfaces_whose_bytes_differ_between_machines_or_runs": differ,
        "counts": {"surfaces": len(names), "differ_between_machines_or_runs": len(differ),
                   "identical_on_all_machines": len(names) - len(differ)},
        "review_proxy": {"distinct_files_in_all_runs": len({h for m in machines.values() for h in m["surfaces"][proxy]["runs"]}),
                         "runs_per_machine": PROXY_RUNS,
                         "distinct_files_on_one_machine": {name: len(set(m["surfaces"][proxy]["runs"]))
                                                           for name, m in machines.items()},
                         "kbps": sorted({m["surfaces"][proxy]["kbps"] for m in machines.values()}),
                         "cap_kbps": 1500},
        "x264_threads_in_stream": {n: {name: m["surfaces"][n]["x264"] for name, m in machines.items()} for n in names},
        "sha256": {n: {name: m["surfaces"][n]["sha256"] for name, m in machines.items()} for n in names},
    }


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]).resolve()), indent=2))
