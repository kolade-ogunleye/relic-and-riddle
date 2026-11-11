"""QF-86: where the cut-gap floor comes from (review r2 blocking 2). Usage: python floor.py <repo root>.

A bare photo reaches the master as a parallax clip and is graded there (video_composer.compose_video, is_photo_clip:
yuv420p -> 16-bit RGB -> PHOTO_CURVE -> yuv420p). The same photo inside a graphic is graded first, in 8-bit RGB at
full chroma (src/photo_grade.py), then encoded as a graphic clip, which the master passes through (to_bt709). This
script puts the committed real photos through the actual compose_video at each run's real bias and splits the gap
between the two (CIE76 median / p95, every 4th pixel, lens vignette off, frame at 0.6 s) into its parts:

- as_built: the two paths as delivered (this machine's encoder).
- layer_16bit_dithered: the layer graded with the master's own 16-bit curve plus TPDF dither instead of the 8-bit
  table (as_built minus this = the 8-bit precision's share).
- graphic_lossless: only the graphic clip encoded losslessly (x264 qp 0) (the graphic's second encode's share).
- all_lossless: every encode lossless (both clips and the master): no encode loss anywhere.
- order_only (all lossless): one path, the parallax one, fed the raw photo with the curve on against the
  pre-graded layer with the curve off; the only difference is grading after rather than before 4:2:0.
- path_only (all lossless, curve off on both): the parallax-clip path against the graphic-clip path, same pixels;
  mean_rgb is parallax minus graphic, in 8-bit codes.
- path_only variants: the parallax path's two swscale steps with an explicit input range, and with accurate_rnd +
  full_chroma_int, to see whether swscale's rounding is the offset.
- control: the bare clip encoded twice and mastered twice (0 = the encoder is deterministic).

Runs on the base too: without src/photo_grade.py there is no graded layer, and it prints {"applicable": false}.
Reads committed inputs only (inputs/), writes only to a temporary directory, prints JSON. No network.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
LOSSLESS = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0"]
CASES = (("end_ground_aswan.jpg", "aswan"), ("photo_richat_01.jpg", "richat"), ("end_ground_aswan.jpg", None))


def lab(rgb) -> np.ndarray:
    x = np.asarray(rgb, dtype=np.float64) / 255.0
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = x @ np.array([[.4124564, .3575761, .1804375], [.2126729, .7151522, .0721750],
                        [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def gap(a, b) -> dict:
    de = np.linalg.norm(lab(a[::4, ::4]) - lab(b[::4, ::4]), axis=-1)
    return {"median": round(float(np.median(de)), 3), "p95": round(float(np.percentile(de, 95)), 3)}


def mean_rgb(a, b) -> list[float]:
    return [round(float(v), 3) for v in (a.astype(np.int64) - b.astype(np.int64)).reshape(-1, 3).mean(0)]


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))

    def offline(*args, **kwargs):
        raise RuntimeError("QF-86 evidence must not use the network")

    socket.socket.connect = offline
    # One encoder on every machine and run (review r3, non-blocking 1): encoding.get_video_encoder's 3 s nvenc probe
    # can time out on a cold start and silently switch encoder, so the probe's own fallback, libx264, is pinned.
    from src import encoding
    encoding._DETECTED_ENCODER = ["-c:v", "libx264", "-preset", encoding.VIDEO_PRESET, "-crf", str(encoding.VIDEO_CRF)]
    if not (root / "src" / "photo_grade.py").is_file():
        return {"applicable": False, "reason": "no graded photo layer on this tree (src/photo_grade.py absent)"}
    from src import encoding, graphics, photo_grade
    from src import video_composer as vc
    from src.delivery_profiles import get_delivery_profile
    from src.lineage import record_derived
    from src.typography import cover_crop

    runs = json.loads((INPUTS / "inputs.json").read_text(encoding="utf-8"))["runs"]
    out: dict = {"encoder": encoding.get_video_encoder(), "lossless": LOSSLESS, "cases": {}}
    with tempfile.TemporaryDirectory(prefix="qf86-floor-") as directory:
        work = Path(directory)
        voice = work / "voice.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                        "-t", "1.5", str(voice)], check=True, timeout=60)
        count = [0]

        def fresh(sub: str) -> Path:
            count[0] += 1
            d = work / f"w{count[0]}" / sub
            d.mkdir(parents=True)
            return d

        def frame(path: Path) -> np.ndarray:
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.6", "-i", str(path), "-vf",
                                  f"{encoding.BT709_TO_RGB},format=rgb24", "-frames:v", "1", "-f", "rawvideo",
                                  "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=120).stdout
            return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3)

        def clip(im: Image.Image, src: Path | None, lossless: bool) -> Path:
            """`im` as 1 s of clip: a parallax clip of `src` (lineage recorded), else a graphic clip."""
            name = "scene_01.mp4" if src else "graphic_photo.mp4"
            path = fresh("parallax" if src else "graphics") / name
            if lossless:
                with patch.object(encoding, "get_video_encoder", lambda: LOSSLESS):
                    graphics._encode((im for _ in range(30)), path, 30, 1080, 1920)
            else:
                graphics._encode((im for _ in range(30)), path, 30, 1080, 1920)
            if src:
                record_derived(path, [src], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
                assert vc.is_photo_clip(path), path
            return path

        def master(item: Path, bias: float, *, lossless: bool = False, curve: bool = True,
                   matrix: str | None = None, rgb_to: str | None = None) -> np.ndarray:
            count[0] += 1
            mp4 = work / f"m{count[0]}.mp4"
            patches = [patch.object(vc, "picture_vignette", lambda width, height: "null")]
            if lossless:
                patches.append(patch.object(vc, "_get_video_encoder", lambda: LOSSLESS))
            if not curve:
                patches.append(patch.object(vc, "film_grade_filter", lambda *a, **k: "null"))
            if matrix:      # the parallax path's yuv -> RGB step (vc.source_matrix is only used there)
                patches.append(patch.object(vc, "source_matrix", lambda *a, **k: matrix))
            if rgb_to:      # the RGB -> yuv step (the parallax path's and the master's own final one)
                patches.append(patch.object(vc, "RGB_TO_BT709", rgb_to))
            for p in patches:
                p.start()
            try:
                vc.VideoComposer().compose_video([item], voice, voice, [], 1, mp4, durations=[1], grade_bias=bias,
                                                 delivery_profile=get_delivery_profile("reel"))
            finally:
                for p in reversed(patches):
                    p.stop()
            return frame(mp4)

        def dithered(im: Image.Image, bias: float) -> Image.Image:
            """`im` through the master's own 16-bit curve with TPDF dither to 8-bit (no 8-bit table)."""
            ramp = np.tile((np.arange(256, dtype=np.uint16) * 257)[None, None, :], (3, 1, 1))
            res = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-s", "256x1",
                                  "-i", "-", "-vf", f"format=gbrp16le,{vc.film_grade_filter(bias)},format=gbrp16le",
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-"],
                                 input=ramp.tobytes(), capture_output=True, check=True, timeout=60).stdout
            g, b, r = np.frombuffer(res, "<u2").reshape(3, 256).astype(np.float64) / 257.0
            a = np.asarray(im.convert("RGB"))
            f = np.stack([r[a[..., 0]], g[a[..., 1]], b[a[..., 2]]], -1)
            rng = np.random.default_rng(1)
            f = f + rng.random(f.shape) - rng.random(f.shape)
            return Image.fromarray(np.clip(np.round(f), 0, 255).astype(np.uint8))

        accurate = "flags=accurate_rnd+full_chroma_int"
        for name, run in CASES:
            bias = float(runs[run]["grade_bias"]) if run else 0.0
            src = fresh("src") / name
            src.write_bytes((INPUTS / name).read_bytes())
            raw = cover_crop(src, (1080, 1920)).convert("RGB")
            layer = photo_grade.cover(src, (1080, 1920), bias)
            row: dict = {"grade_bias": bias}
            bare = master(clip(raw, src, False), bias)
            row["control_bare_twice"] = gap(bare, master(clip(raw, src, False), bias))
            row["as_built"] = gap(bare, master(clip(layer, None, False), bias))
            row["layer_16bit_dithered"] = gap(bare, master(clip(dithered(raw, bias), None, False), bias))
            row["graphic_lossless"] = gap(bare, master(clip(layer, None, True), bias))
            par_raw, par_layer = clip(raw, src, True), clip(layer, src, True)
            gfx_raw, gfx_layer = clip(raw, None, True), clip(layer, None, True)
            bare_l = master(par_raw, bias, lossless=True)
            row["all_lossless"] = gap(bare_l, master(gfx_layer, bias, lossless=True))
            row["order_only"] = gap(bare_l, master(par_layer, bias, lossless=True, curve=False))
            par_id = master(par_raw, bias, lossless=True, curve=False)
            gfx_id = master(gfx_raw, bias, lossless=True, curve=False)
            row["path_only"] = {**gap(par_id, gfx_id), "mean_rgb": mean_rgb(par_id, gfx_id),
                                "graphic_path_vs_drawn": gap(np.asarray(raw), gfx_id),
                                "parallax_path_vs_drawn": gap(np.asarray(raw), par_id),
                                "mean_rgb_graphic_path_minus_drawn": mean_rgb(gfx_id, np.asarray(raw)),
                                "mean_rgb_parallax_path_minus_drawn": mean_rgb(par_id, np.asarray(raw))}
            variants = {
                "explicit_in_range_tv": {"matrix": "bt709:in_range=tv"},
                "accurate_rnd_full_chroma": {"matrix": f"bt709:{accurate}",
                                             "rgb_to": encoding.RGB_TO_BT709.replace(
                                                 "out_range=tv", f"out_range=tv:{accurate}", 1)},
            }
            for key, kw in variants.items():
                gfx_v = master(gfx_raw, bias, lossless=True, curve=False, rgb_to=kw.get("rgb_to"))
                par_v = master(par_raw, bias, lossless=True, curve=False, **kw)
                row[f"path_only_{key}"] = {**gap(par_v, gfx_v), "mean_rgb": mean_rgb(par_v, gfx_v)}
            out["cases"][f"{name}@{bias:+.3f}"] = row
            print(name, bias, json.dumps(row), file=sys.stderr, flush=True)
    return out


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1))
