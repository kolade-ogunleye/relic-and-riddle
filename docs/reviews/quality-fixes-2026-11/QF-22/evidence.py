"""QF-22 evidence on real run media: the composer's camera before and after, rendered by each commit's own code.

Usage: python evidence.py BASE_ROOT NEW_ROOT RUN_DIR OUT_DIR

RUN_DIR is a dev run (output/run_*, read only) whose Reel opened on an AI hook clip (motion/motion_01.mp4); its
photographs (the parallax clips' own sources, from their lineage) are composed as stills on the push, pull, drift and
push presets, ending on the loop bridge, exactly as compose_video moves stills when a shot is not a parallax clip.
Each root renders in its own process. The motion is read back from the delivered H.264 master by tracking corners
(pyramidal Lucas-Kanade, sub-pixel) through each shot: per frame, the median distance the tracked points moved.
Writes OUT_DIR/evidence.json, a trace chart per shot and, per shot and root, a strip of its first, middle and
last frame (half size, JPEG <= 300 KB).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FPS = 30
DUR = 2.5
RENDER = r'''
import json, os, sys
from pathlib import Path
root, work, spec = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
os.environ["RELIC_APP_ROOT"] = str(work / "app")
sys.path.insert(0, str(root))
import numpy as np, soundfile as sf
from src import end_card
from src.audio_engine import AudioEngine
from src.video_composer import VideoComposer
vo_dur = sum(spec["durations"])
sr = 48000
t = np.arange(int(sr * vo_dur)) / sr
voice = work / "voice.wav"
sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), sr)
music = work / "music.wav"
tm = np.arange(int(sr * (vo_dur + 4))) / sr
sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), sr)
bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.bed_tail(True), work / "bed.wav", music_path=music,
                                         end_card_at=vo_dur)
master = work / "master.mp4"
VideoComposer().compose_video([Path(p) for p in spec["scenes"]], voice, bed, [], vo_dur, master,
                              durations=spec["durations"], end_card="Send this.")
print(json.dumps(json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))["end_card"]))
'''


def render(root: Path, scenes: list[str], durations: list[float], work: Path) -> tuple[Path, dict]:
    work.mkdir(parents=True, exist_ok=True)
    app = work / "app"
    shutil.copytree(root / "fonts", app / "fonts")
    (app / "data").mkdir()
    shutil.copyfile(root / "data/reference_facts.json", app / "data/reference_facts.json")
    env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                                                                  "COMSPEC", "PATHEXT"}}
    env.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RELIC_APP_ROOT=str(app), USERPROFILE=str(app))
    out = subprocess.run([sys.executable, "-I", "-c", RENDER, str(root), str(work),
                          json.dumps({"scenes": scenes, "durations": durations})],
                         capture_output=True, text=True, env=env, timeout=3600, check=True)
    return work / "master.mp4", json.loads(out.stdout.strip().splitlines()[-1])


def frames(master: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(master), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080)


def track(gray: np.ndarray, a: int, b: int) -> list[float]:
    """Median per-frame displacement (px) of corners tracked through frames a..b, above the caption band."""
    import cv2
    mask = np.zeros(gray.shape[1:], np.uint8)
    mask[120:1000, 60:1020] = 255
    pts = cv2.goodFeaturesToTrack(gray[a], 300, 0.01, 12, mask=mask)
    steps = []
    for k in range(a, b):
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(gray[k], gray[k + 1], pts, None, winSize=(31, 31), maxLevel=3,
                                              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 0.001))
        good = st.ravel() == 1
        steps.append(float(np.median(np.linalg.norm((nxt - pts)[good].reshape(-1, 2), axis=1))))
        pts = nxt[good].reshape(-1, 1, 2)
    return steps


def summary(steps: list[float]) -> dict:
    s = np.array(steps)
    moving = s[len(s) // 6: -len(s) // 6]                 # the shot's middle two thirds: it is moving there
    return {"frames": len(s) + 1, "held_frames_mid": int(np.count_nonzero(moving < 0.05)),
            "max_step_px": round(float(s.max()), 3), "max_jerk_px": round(float(np.abs(np.diff(s)).max()), 3),
            "mean_step_px": round(float(s.mean()), 3)}


def chart(path: Path, title: str, series: dict[str, list[float]]) -> None:
    w, h, pad = 900, 300, 40
    im = Image.new("RGB", (w, h), (250, 248, 242))
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    top = max(max(v) for v in series.values()) * 1.1 or 1
    d.text((pad, 8), f"{title}: per-frame motion (px, tracked corners)", fill=(20, 20, 20), font=font)
    d.line([(pad, h - pad), (w - 10, h - pad)], fill=(120, 120, 120))
    d.line([(pad, pad), (pad, h - pad)], fill=(120, 120, 120))
    d.text((4, pad - 6), f"{top:.2f}", fill=(80, 80, 80), font=font)
    d.text((4, h - pad - 6), "0", fill=(80, 80, 80), font=font)
    colours = {"before": (200, 60, 40), "after": (30, 110, 170)}
    for k, (name, values) in enumerate(series.items()):
        xs = [pad + i * (w - pad - 20) / max(1, len(values) - 1) for i in range(len(values))]
        ys = [h - pad - v / top * (h - 2 * pad) for v in values]
        d.line(list(zip(xs, ys)), fill=colours[name], width=2)
        d.text((w - 200, 10 + 14 * k), f"{name} ({'zoompan' if name == 'before' else 'shared camera'})",
               fill=colours[name], font=font)
    im.save(path, quality=88)


def strip(path: Path, master: Path, times: list[float], limit: int = 300_000) -> None:
    """Half-size frames (540x960) at `times`, side by side, as a JPEG of at most `limit` bytes."""
    import io
    tiles = []
    for t in times:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(master), "-frames:v", "1",
                              "-vf", "scale=540:960:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, timeout=120).stdout
        tiles.append(Image.frombytes("RGB", (540, 960), raw))
    out = Image.new("RGB", (540 * len(tiles) + 6 * (len(tiles) - 1), 960), (0, 0, 0))
    for i, tile in enumerate(tiles):
        out.paste(tile, (i * 546, 0))
    for quality in range(90, 40, -5):
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=quality)
        if buf.tell() <= limit:
            break
    path.write_bytes(buf.getvalue())


def main(base: Path, new: Path, run: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sources = []
    for name in sorted(os.listdir(run / "parallax")):
        if name.endswith(".mp4.lineage.json"):
            for s in json.loads((run / "parallax" / name).read_text(encoding="utf-8")).get("sources", []):
                p = Path(s["path"])
                if p.is_file() and str(p) not in sources and "cache" not in p.parts:
                    sources.append(str(p))
    scenes = [str(run / "motion" / "motion_01.mp4"), *sources[:3]]   # hook clip, then pull, drift, push (close)
    stills = [str(p) for p in sources[:4]]                           # push (open), pull, drift, push (close)
    durations = [DUR] * 4
    result = {"run": run.name, "scenes_hook_reel": [Path(s).name for s in scenes],
              "scenes_still_reel": [Path(s).name for s in stills], "durations": durations, "roots": {}}
    with tempfile.TemporaryDirectory(prefix="qf22-evidence-") as tmp:
        for label, root in (("before", base), ("after", new)):
            row = {}
            for reel, media in (("hook_reel", scenes), ("still_reel", stills)):
                master, seam = render(root, media, durations, Path(tmp) / label / reel)
                gray = frames(master)
                cuts = [round(sum(durations[:k + 1]) * FPS) for k in range(3)]
                bounds = [0, *cuts, round(sum(durations) * FPS)]
                shots = [track(gray, a + 1, b - 2) for a, b in pairwise(bounds)]
                row[reel] = {"seam": {k: seam.get(k) for k in ("variant", "push", "close_zoom", "open_push",
                                                                 "max_step_L", "mad_last")},
                             "shots": [summary(s) for s in shots], "steps": shots}
                for k, (a, b) in enumerate(pairwise(bounds)):   # each shot's first, middle and last frame
                    strip(out / f"strip_{reel}_shot{k}_{label}.jpg", master,
                          [(a + 1) / FPS, (a + b) / 2 / FPS, (b - 2) / FPS])
            result["roots"][label] = row
    for reel in ("hook_reel", "still_reel"):
        for k in range(4):
            chart(out / f"trace_{reel}_shot{k}.jpg", f"{reel} shot {k}",
                  {label: result["roots"][label][reel]["steps"][k] for label in ("before", "after")})
    for label in result["roots"]:
        for reel in result["roots"][label].values():
            reel["steps"] = [[round(v, 4) for v in s] for s in reel["steps"]]
    (out / "evidence.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({lab: {r: [s for s in v["shots"]] for r, v in row.items()}
                      for lab, row in result["roots"].items()}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: evidence.py BASE_ROOT NEW_ROOT RUN_DIR OUT_DIR")
    main(*(Path(a) for a in sys.argv[1:]))
