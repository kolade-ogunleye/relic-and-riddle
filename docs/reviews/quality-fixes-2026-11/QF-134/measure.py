"""QF-134 acceptance: delivered and judged video does not depend on the machine's hardware.

Usage: python measure.py <repo root>

Runs every delivered or judged encode path of the repository at <repo root> twice, once as a machine whose NVIDIA
GPU answers the NVENC probe and once as a machine without one (production; the probe is mocked, everything else
runs for real with the local ffmpeg): a graphic clip (graphics._encode: graphics, design cards, callouts, the
dense timeline), the Reel master ending on the loop bridge (its body intermediate and its delivery pass), the 16:9
documentary master and a micro-patched master (splice_single_beat). It records every ffmpeg command that encodes
video (paths of the repository and the work folder and temporary file names normalised, so two commits
compare), the encoder each output file carries (libx264 writes its settings into the stream; NVENC does not) and
the master manifest's encoder record. Inputs are generated (no run data, nothing gitignored). Prints JSON:
- hardware_dependent_surfaces: surfaces whose commands differ between the two machines (before > 0, after 0)
- nvenc_outputs_with_gpu: delivered files encoded by NVENC on the GPU machine (before > 0, after 0)
- production_sha256: per surface, the hash of the commands on the machine without NVENC; equal before and after
  proves production's arguments did not change.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# The work folder: one per run (two runs at once must not share it), normalised to <work> in the commands.
WORK = Path(tempfile.gettempdir())


def _file_encoder(path: Path) -> str:
    head = path.read_bytes()[: 8 << 20]
    if b"x264 - core" in head:
        crf = re.search(rb"crf=([\d.]+)", head)
        return "libx264" + (f" crf={crf.group(1).decode()}" if crf else "")
    return "not libx264 (NVENC)"


def run(root: Path) -> dict:
    global WORK
    WORK = Path(tempfile.mkdtemp(prefix="qf134_measure_"))
    try:
        return _run(root)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


def _run(root: Path) -> dict:
    import os
    sys.path.insert(0, str(root))
    os.chdir(root)
    import numpy as np
    from PIL import Image

    from src import encoding, graphics
    from src import end_card as card_mod
    from src import video_composer as vc

    real_run, real_popen = subprocess.run, subprocess.Popen
    commands: list[list[str]] = []

    class Recording(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and str(cmd[0]) == "ffmpeg" and "-c:v" in cmd \
                    and cmd[cmd.index("-c:v") + 1] != "copy" and "null" not in cmd:
                commands.append([str(c) for c in cmd])
            super().__init__(cmd, *args, **kwargs)

    def spellings(path: Path) -> set[str]:   # as given and resolved (a short 8.3 temp name expands), each escaped
        forms = set()
        for text in {str(path), str(path.resolve())}:
            forms |= {text, text.replace("\\", "/"), text.replace("\\", "/").replace(":", "\\:")}
        return forms
    roots, works = spellings(root), spellings(WORK)

    def normal(cmd: list[str]) -> list[str]:
        out = []
        for arg in cmd:
            for r in sorted(roots, key=len, reverse=True):
                arg = arg.replace(r, "<root>")
            for w in sorted(works, key=len, reverse=True):
                arg = arg.replace(w, "<work>")
            arg = re.sub(r"\.camera-[0-9a-f]{12}", ".camera-<id>", arg)
            out.append(re.sub(r"tmp[0-9a-z_]{8}\.mkv", "tmp<id>.mkv", arg))   # still_camera's temporary clips
        return out

    def machine(nvenc: bool) -> dict:
        encoding._DETECTED_ENCODER = None

        def probe(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "h264_nvenc" in cmd and "null" in cmd:
                return SimpleNamespace(returncode=0 if nvenc else 1)
            return real_run(cmd, *args, **kwargs)
        subprocess.run, subprocess.Popen = probe, Recording
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True)
        rng = np.random.default_rng(134)
        scenes = []
        for i in range(3):
            pixels = rng.integers(0, 255, (1920, 1080, 3), dtype=np.uint8)
            path = WORK / f"scene{i}.png"
            Image.fromarray(pixels).resize((1080, 1920)).save(path)
            scenes.append(path)
        voice, bed = WORK / "voice.wav", WORK / "bed.wav"
        real_run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                  "-t", "3.2", str(voice)], check=True)
        real_run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000",
                  "-t", f"{3.2 + card_mod.LOOP_SECONDS + 1:.2f}", str(bed)], check=True)
        words = [{"word": w, "start": i * 0.7, "dur": 0.6} for i, w in enumerate(["One", "encoder", "everywhere"])]
        surfaces = {}
        try:
            def surface(name, make, output):
                commands.clear()
                make()
                surfaces[name] = {"commands": sorted(normal(c) for c in commands), "output": _file_encoder(output)}

            graphic = WORK / "graphic.mp4"
            surface("graphic clip", lambda: graphics._encode((Image.open(scenes[i % 3]) for i in range(30)), graphic,
                                                             30), graphic)
            reel = WORK / "reel_master.mp4"
            surface("reel master with loop bridge",
                    lambda: vc.VideoComposer().compose_video(scenes, voice, bed, words, 3.0, reel,
                                                             durations=[1.0, 1.0, 1.0], end_card="Follow for more"),
                    reel)
            surfaces["reel master with loop bridge"]["manifest"] = json.loads(
                reel.with_suffix(".composition.json").read_text(encoding="utf-8")).get("video_encoder")
            wide = WORK / "documentary_master.mp4"
            surface("documentary master",
                    lambda: vc.VideoComposer().compose_deep_dive(scenes, voice, bed, words, 3.0, wide,
                                                                 durations=[1.0, 1.0, 1.0]), wide)
            surfaces["documentary master"]["manifest"] = json.loads(
                wide.with_suffix(".composition.json").read_text(encoding="utf-8")).get("video_encoder")
            patched = WORK / "patched_master.mp4"
            surface("micro-patched master",
                    lambda: vc.VideoComposer.splice_single_beat(reel, 1, scenes[0], [1.0, 1.0, 1.0], patched), patched)
        finally:
            subprocess.run, subprocess.Popen = real_run, real_popen
        return surfaces

    gpu, production = machine(nvenc=True), machine(nvenc=False)

    def digest(commands):
        return hashlib.sha256(json.dumps(commands).encode()).hexdigest()

    result = {
        "hardware_dependent_surfaces": sorted(n for n in gpu if gpu[n]["commands"] != production[n]["commands"]),
        "nvenc_outputs_with_gpu": sorted(n for n in gpu if gpu[n]["output"].startswith("not libx264")),
        "production_sha256": {n: digest(production[n]["commands"]) for n in production},
        "gpu_machine_sha256": {n: digest(gpu[n]["commands"]) for n in gpu},
        "video_encoders": {
            "gpu machine": {n: sorted({" ".join(c[c.index("-c:v"):c.index("-c:v") + 6]) for c in gpu[n]["commands"]})
                            for n in gpu},
            "production": {n: sorted({" ".join(c[c.index("-c:v"):c.index("-c:v") + 6])
                                      for c in production[n]["commands"]}) for n in production}},
        "output_encoder": {"gpu machine": {n: gpu[n]["output"] for n in gpu},
                           "production": {n: production[n]["output"] for n in production}},
        "manifest_video_encoder": {"gpu machine": {n: gpu[n].get("manifest") for n in gpu if "manifest" in gpu[n]}},
        "differences": {n: [[a, b] for ca, cb in zip(gpu[n]["commands"], production[n]["commands"])
                            for a, b in zip(ca, cb) if a != b][:6] for n in gpu},
        "production_commands": {n: production[n]["commands"] for n in production},
    }
    result["counts"] = {"hardware_dependent_surfaces": len(result["hardware_dependent_surfaces"]),
                        "nvenc_outputs_with_gpu": len(result["nvenc_outputs_with_gpu"]), "surfaces": len(gpu)}
    return result


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1]).resolve()), indent=2))
