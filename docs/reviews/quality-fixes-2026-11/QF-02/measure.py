"""QF-02 16-bit grade: shadow levels and black through the master's real picture chain.

Usage: python measure.py <repo root>

Imports the pipeline from <repo root> and runs VideoComposer.compose_video's own graph (the real film grade, print
LUT, grain, lens vignette and captions, then the delivery encode) on inputs made here: nothing is read from output/
or the gitignored photos, and no network is touched. Prints JSON and writes measurements.json beside this file.

Measured:
- shadow_ramp: a BT.709 yuv420p clip whose frames step through every luma code of a 0-64 RGB shadow ramp
  (Y16..Y71, 56 levels, 4 frames each, flat). The 9:16 master is rendered twice, its encoder pinned: lossless
  (the graph's own output) and the pipeline's libx264 fallback (VIDEO_PRESET, VIDEO_CRF; NVENC differs per GPU,
  so it is not used here). Each is decoded and, for each level, the mean Y of a
  centred 192x192 box over its middle two frames (the lens vignette is 1 at the centre). A level is "kept" when its
  mean differs from the level below by more than 0.15 code: 8-bit round trips merge neighbouring levels onto one
  code (difference 0, grain averaged out); the 16-bit chain's dithered conversion keeps a fractional step.
  Also the distinct 8-bit codes of the same boxes (they cannot exceed the graded span) for reference.
- blacks (both encoders): RGB black through each branch: a black still (9:16 zoompan branch, with its tone curve; 16:9 portrait
  tilt branch) and a black graphic clip (yuv Y16). Mean centre Y of the master; target video black Y16 +-1.
- right_edge: on a mid-grey clip master, the mean Y of the last 8 columns against the 8 before them (the lens
  vignette changes little over 16 px): the planar-RGB right-edge black bar the yuv420p format guards were added
  against would take the last 8 to about black.
- vignette_corner: the 64 px corner/centre luma ratio (above black) on a mid-grey still master (9:16).
- conversions: swscale's per-link conversions in the 9:16 ramp graph (ffmpeg -v verbose), and those taking the
  YUV picture to 8-bit RGB (the round trips the grade's RGB filters forced). The lens frame's own rgb24 -> gbrp16le
  is not the picture's.
Stand-in: the narration (a sine).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAMP_LEVELS = list(range(16, 72))     # Y codes of RGB 0..64 (BT.709 limited)
FRAMES_PER_LEVEL = 4
STEP_THRESHOLD = 0.15
BOX = 96
TAGGED = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"


def frames(path: Path, w: int, h: int):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True, timeout=600).stdout
    n = w * h * 3 // 2
    a = np.frombuffer(raw, np.uint8)
    return [a[i * n:i * n + w * h].reshape(h, w) for i in range(len(a) // n)]


def centre(y, box=BOX):
    h, w = y.shape
    return y[h // 2 - box:h // 2 + box, w // 2 - box:w // 2 + box].astype(float)


def ramp_clip(path: Path, w: int, h: int) -> Path:
    """A flat-frame clip stepping through RAMP_LEVELS (yuv420p, BT.709 limited, tagged), lossless."""
    import numpy as np
    frames_ = []
    for level in RAMP_LEVELS:
        y = np.full((h, w), level, np.uint8)
        uv = np.full((h // 2, w // 2), 128, np.uint8)
        frames_.extend([y.tobytes() + uv.tobytes() + uv.tobytes()] * FRAMES_PER_LEVEL)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
                    "-r", "30", "-i", "-", "-vf", TAGGED, "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                    str(path)], input=b"".join(frames_), check=True, timeout=300)
    return path


def flat_clip(path: Path, w: int, h: int, level: int, seconds: float = 2.0) -> Path:
    import numpy as np
    y = np.full((h, w), level, np.uint8)
    uv = np.full((h // 2, w // 2), 128, np.uint8)
    n = int(seconds * 30)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
                    "-r", "30", "-i", "-", "-vf", TAGGED, "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                    str(path)], input=(y.tobytes() + uv.tobytes() + uv.tobytes()) * n, check=True, timeout=300)
    return path


SCALE_LINE = re.compile(r"w:\d+ h:\d+ fmt:(?P<f0>\S+) (?:csp:\S+ )?range:\S+[^\n]*?-> w:\d+ h:\d+ fmt:(?P<f1>\S+)")


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    import numpy as np
    from PIL import Image

    from src import video_composer as vc
    from src.delivery_profiles import get_delivery_profile

    result: dict = {"repo": "<repo root argument>", "ramp_levels_in": len(RAMP_LEVELS),
                    "step_threshold_code": STEP_THRESHOLD}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                              check=False, text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "src"], capture_output=True,
                               check=False, text=True, timeout=30).stdout.strip()
        result["commit"] = head + (" (src modified)" if dirty else "")
    except OSError:
        pass
    work = Path(tempfile.mkdtemp(prefix="qf02_"))
    vo = work / "vo.wav"
    stderr_log: list[str] = []

    def verbose_ffmpeg(cmd):
        cmd = [str(c) for c in cmd]
        if cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
            cmd = [cmd[0], "-v", "verbose", *cmd[1:]]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                             check=False)
        if res.returncode != 0:
            raise RuntimeError("ffmpeg failed: " + res.stderr[-1500:])
        if "-filter_complex" in cmd:
            stderr_log.append(res.stderr)
        return res

    vc._run_ffmpeg = verbose_ffmpeg

    def compose(name, scenes, seconds, profile, words=None):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                            "-af", "volume=0.3", "-ac", "1", "-t", f"{seconds + 0.5:.2f}", str(vo)], check=True,
                           timeout=60)
        out = work / f"{name}.mp4"
        words = words or [{"word": "Dark.", "start": 0.2, "dur": 0.4}]
        vc.VideoComposer().compose_video(scenes, vo, vo, words, seconds, out,
                                         durations=[seconds / len(scenes)] * len(scenes),
                                         delivery_profile=get_delivery_profile(profile))
        return out

    from src.config import VIDEO_CRF, VIDEO_PRESET
    encoders = {"graph (lossless encode)": ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0"],
                "delivery (libx264 fallback encoder)": ["-c:v", "libx264", "-preset", VIDEO_PRESET,
                                                        "-crf", str(VIDEO_CRF)]}
    w, h = 1080, 1920
    clip = ramp_clip(work / "ramp_01.mp4", w, h)
    black_still_p = work / "black_still_portrait.png"
    Image.new("RGB", (1200, 1600), (0, 0, 0)).save(black_still_p)
    black_still_l = work / "black_still_landscape.png"
    Image.new("RGB", (2400, 1600), (0, 0, 0)).save(black_still_l)
    black_clip = flat_clip(work / "black_01.mp4", 1080, 1920, 16)
    result["shadow_ramp"], result["blacks"] = {}, {}
    for label, enc in encoders.items():
        vc._get_video_encoder = lambda enc=enc: list(enc)
        # ---- the shadow ramp -------------------------------------------------------------------------------
        seconds = len(RAMP_LEVELS) * FRAMES_PER_LEVEL / 30
        stderr_log.clear()
        master = compose("ramp_master", [clip], seconds, "reel")
        conv = [f"{m.group('f0')}->{m.group('f1')}" for m in SCALE_LINE.finditer("\n".join(stderr_log))]
        fr = frames(master, w, h)
        means, codes = [], set()
        for k in range(len(RAMP_LEVELS)):
            boxes = [centre(fr[k * FRAMES_PER_LEVEL + j]) for j in (1, 2) if k * FRAMES_PER_LEVEL + j < len(fr)]
            means.append(float(np.mean(boxes)))
            for b in boxes:
                codes.update(np.unique(b).astype(int).tolist())
        steps = np.diff(means)
        kept = 1 + int((steps > STEP_THRESHOLD).sum())
        eight_bit_rgb = ("rgb24", "gbrp", "bgr24", "rgba", "bgra", "argb", "abgr", "0rgb", "bgr0", "rgb0")
        yuv_rgb8 = sorted({c for c in conv if c.startswith("yuv") and c.split("->")[1] in eight_bit_rgb})
        result["shadow_ramp"][label] = {"encoder": " ".join(enc), "levels_in": len(RAMP_LEVELS), "levels_kept": kept,
                                        "merged_steps": int((steps <= STEP_THRESHOLD).sum()),
                                        "mean_y_per_level": [round(m, 2) for m in means],
                                        "distinct_8bit_codes_in_boxes": len(codes),
                                        "conversions": sorted(set(conv)), "yuv_to_8bit_rgb_conversions": yuv_rgb8}
        # ---- blacks ----------------------------------------------------------------------------------------
        blacks = {}
        for name, scenes, profile, (bw, bh) in (
                ("9:16 still (zoompan branch, its tone curve)", [black_still_p], "reel", (1080, 1920)),
                ("16:9 still (zoompan branch)", [black_still_l], "deep_dive", (1920, 1080)),
                ("16:9 portrait still (tilt branch)", [black_still_p], "deep_dive", (1920, 1080)),
                ("9:16 black clip (Y16)", [black_clip], "reel", (1080, 1920))):
            out = compose("black_" + str(len(blacks)), scenes, 2.0, profile)
            fr = frames(out, bw, bh)
            y = float(np.mean([centre(f) for f in fr[20:40]]))
            blacks[name] = {"mean_y": round(y, 2), "ok": abs(y - 16) <= 1}
        result["blacks"][label] = blacks
    vc._get_video_encoder = lambda: list(encoders["graph (lossless encode)"])

    # ---- the right edge and the corners ------------------------------------------------------------------
    grey = flat_clip(work / "grey_01.mp4", 1080, 1920, 126)
    out = compose("grey_master", [grey], 2.0, "reel")
    f = frames(out, 1080, 1920)[30].astype(float)
    mid = f[800:1120]
    result["right_edge"] = {"centre_y": round(float(centre(f).mean()), 2),
                            "last_8_columns_y": round(float(mid[:, -8:].mean()), 2),
                            "the_8_columns_before_y": round(float(mid[:, -16:-8].mean()), 2),
                            "first_8_columns_y": round(float(mid[:, :8].mean()), 2),
                            "ratio_last_to_before": round(float(mid[:, -8:].mean() / mid[:, -16:-8].mean()), 3)}
    grey_still = work / "grey_still.png"
    Image.new("RGB", (1200, 2000), (128, 128, 128)).save(grey_still)
    out = compose("grey_still_master", [grey_still], 2.0, "reel")
    f = frames(out, 1080, 1920)[30].astype(float) - 16
    c = centre(f).mean()
    corners = [f[y:y + 64, x:x + 64].mean() / c for y in (0, 1920 - 64) for x in (0, 1080 - 64)]
    result["vignette_corner"] = {"centre_y_above_black": round(float(c), 2),
                                 "min_corner_ratio": round(float(min(corners)), 3)}

    result["acceptance"] = {
        "ramp_levels_kept_ge_50": all(r["levels_kept"] >= 50 for r in result["shadow_ramp"].values()),
        "blacks_at_16_pm_1": all(b["ok"] for e in result["blacks"].values() for b in e.values()),
        "no_yuv_to_8bit_rgb_round_trip": not any(r["yuv_to_8bit_rgb_conversions"]
                                                 for r in result["shadow_ramp"].values()),
        "no_right_edge_bar": result["right_edge"]["ratio_last_to_before"] > 0.97}
    result["acceptance"]["met"] = all(result["acceptance"].values())
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    res = main(Path(sys.argv[1]).resolve())
    text = json.dumps(res, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
