"""QF-22 acceptance: edge-position traces through every camera consumer, on the real code paths.

Usage: python measure.py REPO_ROOT   (prints JSON; writes nothing outside a temporary directory)

Inputs are generated here (no gitignored data): a calibrated soft edge (a tanh step 3 px wide), whose position is
read back from each rendered frame as the centroid of the intensity gradient along one row. The camera's formulas
are never used to compute a position, only to drive each consumer the way the renderer drives it.

1. Drawn paths (graphics, end card, loop bridge, then-and-now, callouts): each consumer's own function called with
   its own time curve over 181 frames on a float-valued edge (no 8-bit rounding hides a sub-pixel move).
2. Composer paths: a real VideoComposer.compose_video Reel (stills on the push, pull, drift and push presets, ending
   on the loop bridge) and a Reel opening on an AI hook clip (motion_00.mp4), decoded from the delivered H.264 file.

Per path: repeated steps (a frame that does not move while the shot moves), reverse steps, the largest step and the
largest change of step between neighbouring frames ("jerk": a whole- or half-pixel tick shows as a jerk near the tick
size; a smooth move's jerk is a small fraction of a pixel). Acceptance (STATUS.md): every trace monotone, no integer
steps: no repeated or reverse step, jerk under 0.25 px on the drawn paths and under 0.35 px on the H.264 composer
traces (their measured encode noise is below 0.1 px; a half-pixel tick of the old 2x zoompan render measures 0.5).
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image

FRAMES = 181
SOURCES = ["src/camera_motion.py", "src/still_camera.py", "src/video_composer.py", "src/graphics.py",
           "src/end_card.py", "src/then_now.py", "src/callouts.py", "src/design_cards.py"]


def no_network(*_args, **_kwargs):
    raise RuntimeError("QF-22 measurement is offline")


def isolate(root: Path, app: Path) -> None:
    keep = {k: v for k, v in os.environ.items()
            if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    os.environ.clear()
    os.environ.update(keep)
    os.environ.update(RELIC_APP_ROOT=str(app), USERPROFILE=str(app), FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    shutil.copytree(root / "fonts", app / "fonts")
    (app / "data").mkdir()
    shutil.copyfile(root / "data/reference_facts.json", app / "data/reference_facts.json")
    socket.create_connection = no_network
    socket.socket.connect = no_network
    sys.path.insert(0, str(root))


def edge_line(width: int, at: float) -> np.ndarray:
    return 127.5 * (1 + np.tanh((np.arange(width, dtype=np.float64) - at) / 3))


def centroid(row: np.ndarray, half: int = 12) -> float:
    """The edge's position: the centroid of the rising gradient within `half` px of its peak (so flat-area encode
    noise, the vignette's slope and a frame border far from the edge carry no weight)."""
    g = np.diff(np.asarray(row, dtype=np.float64))
    peak = int(np.argmax(g))
    lo, hi = max(0, peak - half), min(len(g), peak + half + 1)
    g = np.where(g[lo:hi] > 0, g[lo:hi], 0.0)
    return float(np.dot(np.arange(lo, hi) + 0.5, g) / g.sum())


def stats(trace: list[float], tol: float = 1e-6) -> dict:
    d = np.diff(trace)
    direction = 1.0 if trace[-1] >= trace[0] else -1.0
    steps = d * direction
    jerk = np.abs(np.diff(d)) if len(d) > 1 else np.zeros(1)
    return {"samples": len(trace), "travel_px": round(float(abs(trace[-1] - trace[0])), 3),
            "repeated_steps": int(np.count_nonzero(np.abs(d) <= tol)),
            "reverse_steps": int(np.count_nonzero(steps < -tol)),
            "max_step_px": round(float(np.abs(d).max()), 4), "max_jerk_px": round(float(jerk.max()), 4)}


def drawn_paths(root: Path) -> dict:
    from src import callouts, end_card, graphics, then_now
    from src.art_direction import push_in
    w, h = 1024, 96
    src = Image.fromarray(np.tile(edge_line(w, 300).astype(np.float32), (h, 1)))
    src8 = Image.fromarray(np.repeat(np.tile(edge_line(w, 300).round().astype(np.uint8), (h, 1))[..., None], 3, 2))
    clip = graphics.CLIP_SECONDS
    drift = graphics.GroundDrift(src)
    hold0 = graphics.SCALE_HOLD_FROM
    push = callouts.Push(w, h)
    sampler = getattr(callouts, "camera_sample", None)

    def callout_ground(t):
        if sampler is not None:
            return sampler(src, (w, h), push.crop(t), resample=Image.LANCZOS)
        return src.resize((w, h), Image.LANCZOS, box=push.crop(t))   # callouts.render's background, as on the base

    paths = {
        # the stat card's photo drift (render_stat_cards: _pushed_in(bg, push_in(t, CLIP_SECONDS)))
        "stat_card_photo_push": lambda u: graphics._pushed_in(src, push_in(u * clip, clip)),
        # the hook card's ground (design_cards._hook_compose: _pushed_in(bg, 1 + LOOP_PUSH * i)), 1.4 s hook
        "hook_card_push": lambda u: graphics._pushed_in(src, 1.0 + end_card.LOOP_PUSH * u * 42),
        # the scale drawing's hold push and the cross-section's drift (_push_about about a point)
        "scale_hold_push": lambda u: graphics._push_about(src, graphics.hold_push(hold0 + u * (clip - hold0)),
                                                          (700.0, 48.0)),
        # the timeline's and the cards' photo ground (GroundDrift)
        "timeline_ground_drift": lambda u: drift(u * clip),
        # the end card's push (frames: _pushed(base, zoom_at(t)))
        "end_card_push": lambda u: end_card._pushed(src, end_card.zoom_at(u * end_card.SECONDS), w, h),
        # the loop bridge's pre-roll of frame 0 (z < 1, the mirrored edge) at the opening shot's speed
        "loop_bridge_preroll": lambda u: end_card._pulled(src8, 1 + end_card.LOOP_PUSH * (u * 30 - 30), w, h),
        # then-and-now's shared push
        "then_now_push": lambda u: then_now.pushed(src, u * then_now.CLIP_SECONDS, (w, h)),
        # the push-in under the photo callouts
        "callouts_push": lambda u: callout_ground(u * callouts.CLIP_SECONDS),
    }
    out = {}
    for name, render in paths.items():
        trace = []
        for i in range(FRAMES):
            arr = np.asarray(render(i / (FRAMES - 1)), dtype=np.float64)
            row = arr[h // 2]
            trace.append(centroid(row.mean(axis=1) if row.ndim == 2 else row))
        out[name] = stats(trace)
    return out


def _decode_rows(path: Path, width: int, height: int, row: int) -> np.ndarray:
    """A 16-row band's mean per frame (the traced edge is vertical: averaging rows only lowers the encode noise)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"crop={width}:16:0:{row}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True, check=True, timeout=600).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 16, width).astype(np.float64).mean(axis=1)


def _audio(work: Path, vo_dur: float):
    import soundfile as sf

    from src import end_card
    from src.audio_engine import AudioEngine
    sr = 48000
    t = np.arange(int(sr * vo_dur)) / sr
    voice = work / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), sr)
    music = work / "music.wav"
    tm = np.arange(int(sr * (vo_dur + 4))) / sr
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), sr)
    bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.bed_tail(True), work / "ambient_bed.wav",
                                             music_path=music, end_card_at=vo_dur)
    return voice, bed


def _still(path: Path, at: float, size=(1600, 2844)) -> Path:
    w, h = size
    line = edge_line(w, at * w).round().astype(np.uint8)
    arr = np.repeat(np.tile(line, (h, 1))[..., None], 3, 2)
    Image.fromarray(arr).save(path, quality=100, subsampling=0)
    return path


def _segments(durations: list[float], fps: int, total: int) -> list[tuple[int, int]]:
    """Each shot's middle 80%: an eased end moves less than the H.264 encode's noise on the traced edge (a few
    hundredths of a pixel), so a tick or a hold is only told from noise where the shot really moves."""
    cuts = [round(sum(durations[:k + 1]) * fps) for k in range(len(durations) - 1)]
    bounds = [0, *cuts, total]
    return [(a + round(0.1 * (b - a)), b - 1 - round(0.1 * (b - a))) for a, b in pairwise(bounds)]


def composer_paths(work: Path) -> dict:
    from src.video_composer import VIDEO_FPS, VideoComposer
    width, height = 1080, 1920
    words = [{"word": "Old", "start": 0.2, "dur": 0.4}, {"word": "maps.", "start": 0.7, "dur": 0.5}]
    out = {}
    # A: four stills (push, pull, drift, push) ending on the loop bridge. The edge sits 30% across on the pushes and
    # 70% across on the pull and the drift, where the zoom and the preset's lateral drift move it the same way.
    durations = [2.5, 2.5, 2.5, 2.5]
    vo_dur = sum(durations)
    voice, bed = _audio(work, vo_dur)
    stills = [_still(work / f"s{i}.jpg", at) for i, at in enumerate((0.3, 0.7, 0.7, 0.3))]
    master = work / "stills_master.mp4"
    VideoComposer().compose_video(stills, voice, bed, words, vo_dur, master, durations=durations,
                                  end_card="Send this.")
    rows = _decode_rows(master, width, height, 420)
    body = round(vo_dur * VIDEO_FPS)
    trace = [centroid(r) for r in rows[:body]]
    record = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8")).get("end_card") or {}
    for k, (a, b) in enumerate(_segments(durations, VIDEO_FPS, body)):
        out[f"composer_still_{k}_{['push', 'pull', 'drift', 'push'][k]}"] = stats(trace[a:b + 1], tol=0.002)
    out["loop_seam_record"] = {key: record.get(key) for key in ("variant", "push", "close_zoom", "open_push",
                                                                "max_step_L", "mad_last")}
    # B: an AI hook clip (a generated static edge video named motion_00.mp4) opening the Reel.
    clip = work / "motion_00.mp4"
    _still(work / "hook_frame.png", 0.3, (1080, 1920))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(work / "hook_frame.png"), "-t", "3",
                    "-r", "30", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(clip)],
                   check=True, timeout=300)
    durations = [2.5, 2.5]
    vo_dur = sum(durations)
    voice, bed = _audio(work, vo_dur)
    master = work / "hook_master.mp4"
    VideoComposer().compose_video([clip, stills[0]], voice, bed, words, vo_dur, master, durations=durations,
                                  end_card="Send this.")
    rows = _decode_rows(master, width, height, 420)
    a, b = _segments(durations, VIDEO_FPS, round(vo_dur * VIDEO_FPS))[0]
    out["composer_hook_clip_push"] = stats([centroid(r) for r in rows[a:b + 1]], tol=0.002)
    return out


def main(root: Path) -> None:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="qf22-measure-") as directory:
        app = Path(directory) / "app"
        app.mkdir()
        isolate(root, app)
        drawn = drawn_paths(root)
        work = Path(directory) / "work"
        work.mkdir()
        composer = composer_paths(work)
    traces = {**drawn, **{k: v for k, v in composer.items() if k.startswith("composer_")}}

    def ok(name, row):
        bound = 0.35 if name.startswith("composer_") else 0.25
        return row["repeated_steps"] == 0 and row["reverse_steps"] == 0 and row["max_jerk_px"] < bound
    result = {"schema_version": 2,
              "source_sha256": {n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                                for n in SOURCES if (root / n).is_file()},
              "paths": traces, "loop_seam_record": composer.get("loop_seam_record"),
              "failing_paths": sorted(n for n, row in traces.items() if not ok(n, row))}
    result["acceptance_met"] = not result["failing_paths"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py REPO_ROOT")
    main(Path(sys.argv[1]))
