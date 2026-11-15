"""QF-108 evidence: a photo beat spliced into a master takes the master's photo grade. Repository root is the ONLY
argument.

    python docs/reviews/quality-fixes-2026-11/QF-108/measure.py <repo root>

Runs unchanged on the base (e49616fc) and on the QF-108 commit: it imports `src` from <repo root> and reads only
committed inputs (QF-86/inputs/: real run photos, with each run's real grade bias in QF-86/inputs/inputs.json).
Every picture goes through that tree's real composer and real micro-patch with the production encoder (QF-134:
encoding.delivery_video_encoder), in a temporary folder that is deleted; nothing in the repository is written, and
no network is used. Prints one JSON document.

For each case, a master of one real photo on three 1-second beats is composed at the run's real bias
(VideoComposer.compose_video), and its middle beat is spliced with the same photo at the same bias
(VideoComposer.splice_single_beat; the base has no bias parameter and is called without one). Lens off in both (the
vignette is the master's, applied to the whole picture; the grade alone is measured, as QF-86's cut_gap). Both
paths render the photo on the splice's own camera move ("splice", centred), so the spliced beat and the master's
own beat compare pixel for pixel:
- spliced_vs_master_beat: CIE76 (median / p95 over every 4th pixel), mean L* and chroma shift, and the red-minus-blue
  shift in 8-bit codes, between the master's middle beat and the spliced one, at t 1.2 / 1.5 / 1.9 s (worst of the
  three reported).
- neighbour_floor: the same comparison at t 0.5 and 2.5 s, inside the untouched neighbours (the patch re-encodes
  them): the encoder's floor.
- cut: mean L*, chroma and red-minus-blue of the last frame before the cut (t 0.95) and the first after it (t 1.05)
  in the patched master: the cut a viewer sees.
Cases: the six QF-86 cut-gap photos at their runs' real biases (and the cool bound -0.05), 9:16; the warmest real run
(Richat, +0.05) in 16:9; a photo parallax clip (a photo in motion, graded as compose_video grades it); and an
illustration (a gap_gen_ still), which must keep its own colour (pixel sha256 identical on both trees).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
QF86 = HERE.parent / "QF-86" / "inputs"

# (key, input, run whose real bias is used (None: the cool bound), profile, kind)
CASES = (
    ("gol_gumbaz", "photo_gol_gumbaz_04.jpg", "gol_gumbaz", "reel", "still"),
    ("roman", "end_ground_roman.jpg", "roman", "reel", "still"),
    ("aswan", "end_ground_aswan.jpg", "aswan", "reel", "still"),
    ("wieliczka", "end_ground_wieliczka.jpg", "wieliczka", "reel", "still"),
    ("richat", "photo_richat_01.jpg", "richat", "reel", "still"),
    ("aswan_cool_bound", "end_ground_aswan.jpg", None, "reel", "still"),
    ("richat_16x9", "photo_richat_01.jpg", "richat", "deep_dive", "still"),
    ("gol_gumbaz_parallax_clip", "photo_gol_gumbaz_04.jpg", "gol_gumbaz", "reel", "clip"),
    ("illustration_control", "photo_richat_01.jpg", "richat", "reel", "illustration"),
)
BEAT_TIMES = (1.2, 1.5, 1.9)
NEIGHBOUR_TIMES = (0.5, 2.5)


def lab(rgb):
    """CIE Lab (D65) of sRGB values 0-255, any shape (..., 3)."""
    x = np.asarray(rgb, dtype=float) / 255
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = x @ np.array([[.4124564, .3575761, .1804375], [.2126729, .7151522, .0721750],
                        [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def warmth(a: np.ndarray) -> float:
    a = a.astype(np.float64)
    return float((a[..., 0] - a[..., 2]).mean())


def compare(master: np.ndarray, spliced: np.ndarray) -> dict:
    a, b = lab(master[::4, ::4]), lab(spliced[::4, ::4])
    de = np.linalg.norm(a - b, axis=-1)
    ca, cb = np.hypot(a[..., 1], a[..., 2]), np.hypot(b[..., 1], b[..., 2])
    return {"delta_e76_median": round(float(np.median(de)), 3), "delta_e76_p95": round(float(np.percentile(de, 95)), 3),
            "mean_L_shift": round(float((b[..., 0] - a[..., 0]).mean()), 3),
            "mean_chroma_shift": round(float((cb - ca).mean()), 3),
            "red_minus_blue_shift": round(warmth(spliced) - warmth(master), 3)}


def worst(rows: list[dict]) -> dict:
    return max(rows, key=lambda r: r["delta_e76_median"])


def summary(a: np.ndarray) -> dict:
    x = lab(a[::4, ::4]).reshape(-1, 3)
    return {"mean_L": round(float(x[:, 0].mean()), 3), "mean_chroma": round(float(np.hypot(x[:, 1], x[:, 2]).mean()), 3),
            "red_minus_blue": round(warmth(a), 3)}


class Rig:
    def __init__(self, work: Path):
        from src import still_camera
        from src import video_composer as vc
        from src.delivery_profiles import get_delivery_profile
        from src.encoding import BT709_TO_RGB
        self.vc, self.camera, self.profile, self.to_rgb = vc, still_camera, get_delivery_profile, BT709_TO_RGB
        self.work = work
        self.voice = work / "voice.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                        "-t", "3", str(self.voice)], check=True, timeout=60)

    def frame(self, path: Path, w: int, h: int, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-vf",
                              f"{self.to_rgb},format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, timeout=120).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3)

    def run(self, key: str, item: Path, profile_name: str, bias: float) -> tuple[Path, Path, tuple[int, int]]:
        """(master, patched, size): the master of `item` on three beats at `bias`, and its middle beat spliced."""
        p = self.profile(profile_name)
        real = self.camera.render_clip

        def same_move(source, output, duration, size, fps, **kwargs):
            # the master's scenes on the splice's own move, so the two beats compare pixel for pixel
            kwargs.update(preset="splice", focal=(0.5, 0.5), tilt=None, lead=False, trail=False)
            return real(source, output, duration, size, fps, **kwargs)
        out = self.work / key
        out.mkdir(exist_ok=True)
        master, patched = out / "master.mp4", out / "patched.mp4"
        with patch.object(self.vc, "picture_vignette", lambda width, height: "null"), \
                patch.object(self.camera, "render_clip", same_move):
            self.vc.VideoComposer().compose_video([item, item, item], self.voice, self.voice, [], 3, master,
                                                  durations=[1, 1, 1], grade_bias=bias, delivery_profile=p)
        import inspect
        extra = {"grade_bias": bias} if "grade_bias" in inspect.signature(
            self.vc.VideoComposer.splice_single_beat).parameters else {}
        self.vc.VideoComposer.splice_single_beat(master, 1, item, [1.0, 1.0, 1.0], patched,
                                                 width=p.width, height=p.height, **extra)
        return master, patched, (p.width, p.height)


def note(stage: str) -> None:
    print(f"qf108 measure: {stage}", file=sys.stderr, flush=True)


def prepare(work: Path, key: str, name: str, kind: str, size: tuple[int, int]) -> Path:
    """The case's scene item in its own folder: the photo as a run holds it, a parallax clip of it with its lineage
    record, or the same pixels under an illustration's name."""
    folder = work / f"in_{key}"
    folder.mkdir()
    if kind == "illustration":
        item = folder / "gap_gen_03.jpg"
        shutil.copy(QF86 / name, item)
        return item
    photo = folder / name
    shutil.copy(QF86 / name, photo)
    if kind == "still":
        return photo
    from src import graphics
    from src.lineage import record_derived
    from src.typography import cover_crop
    raw = cover_crop(photo, size).convert("RGB")
    (folder / "parallax").mkdir()
    clip = graphics._encode((raw for _ in range(90)), folder / "parallax" / "scene_01.mp4", 90, *size)
    record_derived(clip, [photo], "parallax", {"preset": "hook", "duration": 3.0, "fps": 30, "variant": 0})
    return clip


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))

    def offline(*args, **kwargs):
        raise RuntimeError("QF-108 evidence must not use the network")

    socket.socket.connect = offline
    from src import encoding
    manifest = json.loads((QF86 / "inputs.json").read_text(encoding="utf-8"))
    runs = manifest["runs"]
    for _, name, *_ in CASES:
        assert hashlib.sha256((QF86 / name).read_bytes()).hexdigest() == manifest["files"][name]["sha256"], name
    results: dict = {"encoder": encoding.delivery_video_encoder(),
                     "delta_e_definition": "CIE76, D65, every 4th pixel; red_minus_blue in 8-bit codes",
                     "network_calls": 0, "cases": {}}
    with tempfile.TemporaryDirectory(prefix="qf108-measure-") as directory:
        work = Path(directory)
        rig = Rig(work)
        for key, name, run, profile, kind in CASES:
            note(key)
            bias = runs[run]["grade_bias"] if run else -0.05
            p = rig.profile(profile)
            item = prepare(work, key, name, kind, (p.width, p.height))
            master, patched, (w, h) = rig.run(key, item, profile, bias)
            beat = [compare(rig.frame(master, w, h, t), rig.frame(patched, w, h, t)) for t in BEAT_TIMES]
            floor = [compare(rig.frame(master, w, h, t), rig.frame(patched, w, h, t)) for t in NEIGHBOUR_TIMES]
            before, after = rig.frame(patched, w, h, 0.95), rig.frame(patched, w, h, 1.05)
            row = {"input": name, "kind": kind, "profile": profile, "grade_bias": bias,
                   "spliced_vs_master_beat": worst(beat), "neighbour_floor": worst(floor),
                   "cut": {"last_before": summary(before), "first_after": summary(after),
                           "red_minus_blue_step": round(warmth(after) - warmth(before), 3)}}
            if kind == "illustration":
                row["spliced_beat_sha256"] = hashlib.sha256(rig.frame(patched, w, h, 1.5).tobytes()).hexdigest()
            results["cases"][key] = row
    photo_rows = [r for r in results["cases"].values() if r["kind"] != "illustration"]
    results["acceptance"] = {
        "worst_photo_beat_delta_e76_median": max(r["spliced_vs_master_beat"]["delta_e76_median"] for r in photo_rows),
        "worst_photo_beat_red_minus_blue_shift": max(abs(r["spliced_vs_master_beat"]["red_minus_blue_shift"])
                                                     for r in photo_rows),
        "worst_neighbour_floor_delta_e76_median": max(r["neighbour_floor"]["delta_e76_median"]
                                                      for r in results["cases"].values()),
    }
    return results


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1, ensure_ascii=False))
