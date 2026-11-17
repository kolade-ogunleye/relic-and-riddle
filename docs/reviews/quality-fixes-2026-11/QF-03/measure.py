"""QF-03 grain removed from the delivery master: bitrate at the same CRF, and render-to-render determinism.

Usage: python measure.py <repo root>

Imports the pipeline from <repo root> and runs VideoComposer.compose_video's own graph (the film grade, print LUT,
lens vignette, captions, then the delivery encode) on inputs made here: nothing is read from output/ or the
gitignored photos, and no network is touched. Prints JSON and writes measurements.json beside this file.

The generated reference clip: two photo-like stills from a fixed seed (smooth multi-scale texture, a sky-to-ground
gradient, hard-edged blocks and fine stone-like detail; 1600x2400), composed into a 6 s 9:16 Reel (3 s each, the
stills' Ken Burns branch, one caption word) and, the same stills, a 6 s 16:9 deep dive. The master's encoder is
pinned to the pipeline's libx264 fallback (VIDEO_PRESET, VIDEO_CRF; NVENC differs per GPU, so it is not used here),
so before and after are encoded at the same CRF. A second pair of committed photo fixtures runs through both
profiles too. All four compare against review commit 581a866 (operator decision 2026-11-06).

Measured, per profile:
- the master's file size and its video stream's bit rate (ffprobe), and the drop against review commit 581a866
  (>= 50 % on all four samples, against baseline-repro.json with matching fixture hashes);
- determinism: the same inputs rendered twice; sha256 of each master, and when they differ, the number of
  differing bytes and the MP4 boxes they fall in (mdat = the coded picture/sound, moov = the index/timestamps);
- the filter graph the master ran: whether it holds a `noise` filter.
Stand-in: the narration (a sine).
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 3
SECONDS = 6.0


def reference_still(path: Path, seed: int, w: int = 1600, h: int = 2400) -> Path:
    """A deterministic photo-like still: multi-scale smooth texture, gradient, blocks and fine detail."""
    import numpy as np
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w), np.float32)
    for cell, amp in ((400, 0.45), (120, 0.25), (30, 0.15), (6, 0.08)):
        small = rng.random((h // cell + 2, w // cell + 2)).astype(np.float32)
        layer = Image.fromarray((small * 255).astype(np.uint8)).resize((w + 2 * cell, h + 2 * cell), Image.BICUBIC)
        img += amp * (np.asarray(layer, np.float32)[cell:cell + h, cell:cell + w] / 255.0)
    img += np.linspace(0.25, -0.15, h, dtype=np.float32)[:, None]
    for _ in range(14):     # hard-edged masonry blocks
        x0, y0 = int(rng.integers(0, w - 200)), int(rng.integers(h // 3, h - 200))
        img[y0:y0 + int(rng.integers(80, 300)), x0:x0 + int(rng.integers(120, 400))] += float(rng.uniform(-0.2, 0.2))
    img = np.clip(img, 0.0, 1.0)
    rgb = np.stack([img * 0.95 + 0.03, img * 0.82 + 0.05, img * 0.62 + 0.08], axis=-1)
    Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)).save(path)
    return path


def mp4_boxes(data: bytes) -> list[tuple[str, int, int]]:
    """Top-level MP4 boxes as (type, start, end)."""
    out, pos = [], 0
    while pos + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if size < 8:
            break
        out.append((kind.decode("latin-1"), pos, pos + size))
        pos += size
    return out


def byte_diff(a: bytes, b: bytes) -> dict:
    if a == b:
        return {"identical": True, "differing_bytes": 0, "boxes": {}}
    n = min(len(a), len(b))
    import numpy as np
    diff = np.nonzero(np.frombuffer(a[:n], np.uint8) != np.frombuffer(b[:n], np.uint8))[0]
    boxes: dict[str, int] = {}
    for kind, start, end in mp4_boxes(a):
        k = int(((diff >= start) & (diff < end)).sum())
        if k:
            boxes[kind] = k
    return {"identical": False, "sizes": [len(a), len(b)], "differing_bytes": len(diff) + abs(len(a) - len(b)),
            "first_difference_at": int(diff[0]) if len(diff) else n, "boxes": boxes}


def probe(path: Path) -> dict:
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=bit_rate,nb_frames,width,height:format=duration,size", "-of", "json", str(path)],
                         capture_output=True, check=True, text=True, timeout=60)
    j = json.loads(res.stdout)
    s, f = j["streams"][0], j["format"]
    return {"size": f"{s['width']}x{s['height']}", "frames": int(s.get("nb_frames", 0)),
            "duration_s": round(float(f["duration"]), 3), "file_bytes": int(f["size"]),
            "video_kbps": round(int(s["bit_rate"]) / 1000, 1)}


def stream_md5(path: Path, stream: str) -> str:
    """md5 of one stream's coded packets (-c copy), so a whole-file difference is put on the picture or the sound."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{stream}", "-c", "copy", "-f", "md5",
                           "-"], capture_output=True, check=True, text=True, timeout=120).stdout.strip()[4:20]


def flat_clip(path: Path, w: int = 1080, h: int = 1920, level: int = 126, frames: int = 30) -> Path:
    import numpy as np
    uv = np.full((h // 2, w // 2), 128, np.uint8).tobytes()
    raw = (np.full((h, w), level, np.uint8).tobytes() + uv + uv) * frames
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-r", "30",
                    "-i", "-", "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                    "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv", str(path)],
                   input=raw, check=True, timeout=300)
    return path


def lumas(path: Path, w: int = 1080, h: int = 1920, box: int = 200):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True, timeout=600).stdout
    n = w * h * 3 // 2
    a = np.frombuffer(raw, np.uint8)
    return [a[i * n:i * n + w * h].reshape(h, w)[h // 2 - box:h // 2 + box, w // 2 - box:w // 2 + box].astype(float)
            for i in range(len(a) // n)]


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from src import video_composer as vc
    from src.config import VIDEO_CRF, VIDEO_PRESET
    from src.delivery_profiles import get_delivery_profile

    result: dict = {"repo": "<repo root argument>"}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                              check=False, text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "src"], capture_output=True,
                               check=False, text=True, timeout=30).stdout.strip()
        result["commit"] = head + (" (src modified)" if dirty else "")
    except OSError:
        pass
    ffv = subprocess.run(["ffmpeg", "-version"], check=False, capture_output=True, text=True, timeout=30).stdout.split("\n")[0]
    result["ffmpeg"] = " ".join(ffv.split()[:3])
    encoder = ["-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF)]
    vc._get_video_encoder = lambda: list(encoder)
    result["encoder"] = " ".join(encoder)

    graphs: list[str] = []
    real_run = vc._run_ffmpeg

    def recording(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "-filter_complex" in cmd:
            graphs.append(cmd[cmd.index("-filter_complex") + 1])
        return real_run(cmd, *a, **k)

    vc._run_ffmpeg = recording
    work = Path(tempfile.mkdtemp(prefix="qf03_"))
    stills = [reference_still(work / f"ref_{i:02d}.png", SEED + i) for i in range(2)]
    samples = {"generated": stills,
               "photos": [HERE / "fixtures/salt.jpg", HERE / "fixtures/passage.jpg"]}
    result["fixtures"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in samples['photos']}
    vo = work / "vo.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-af", "volume=0.3", "-ac", "1", "-t", f"{SECONDS + 0.5:.2f}", str(vo)], check=True, timeout=60)
    words = [{"word": "Stone.", "start": 0.3, "dur": 0.5}]
    result["profiles"] = {}
    for sample, profile in ((sample, profile) for sample in samples for profile in ("reel", "deep_dive")):
        renders = []
        for k in range(2):
            out = work / f"{sample}_{profile}_{k}.mp4"
            graphs.clear()
            vc.VideoComposer().compose_video(list(samples[sample]), vo, vo, words, SECONDS, out,
                                             durations=[SECONDS / 2] * 2,
                                             delivery_profile=get_delivery_profile(profile))
            renders.append(out)
            master_graph = next((g for g in graphs if "[v_final]" in g), "")
        a, b = (p.read_bytes() for p in renders)
        result["profiles"][f"{sample}/{profile}"] = {
            **probe(renders[0]),
            "noise_filter_in_master_graph": "noise=" in master_graph,
            "sha256": [hashlib.sha256(a).hexdigest()[:16], hashlib.sha256(b).hexdigest()[:16]],
            "video_stream_md5": [stream_md5(r, "v") for r in renders],
            "audio_stream_md5": [stream_md5(r, "a") for r in renders],
            "second_render": byte_diff(a, b)}

    # ---- temporal grain: a flat, unmoving 9:16 clip through the master, encoded lossless -------------------
    vc._get_video_encoder = lambda: ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0"]
    import numpy as np
    flat = flat_clip(work / "grey_01.mp4")
    out = work / "flat_master.mp4"
    vc.VideoComposer().compose_video([flat], vo, vo, [{"word": "Grey.", "start": 0.9, "dur": 0.1}], 1.0, out,
                                     durations=[1.0], delivery_profile=get_delivery_profile("reel"))
    fr = lumas(out)
    result["static_clip_flicker"] = {
        "what": "mean |Y(n+1) - Y(n)| over a centred 400x400 box, frames 3-24 (before the caption), lossless encode",
        "mean_abs_frame_diff_code": round(float(np.mean([np.abs(fr[i + 1] - fr[i]).mean() for i in range(3, 24)])), 3)}
    result["acceptance"] = {
        "no_noise_filter": not any(p["noise_filter_in_master_graph"] for p in result["profiles"].values()),
        "video_bitstreams_identical": all(len(set(p["video_stream_md5"])) == 1 for p in result["profiles"].values()),
        "renders_byte_identical": all(p["second_render"]["identical"] for p in result["profiles"].values()),
        "bitrate_drop_ge_50pct": False}
    # Operator decision 2026-11-06: compare all four samples with the review
    # baseline 581a866. The committed small photo fixtures are identical on both runs.
    before = HERE / "baseline-repro.json"
    prev_doc = json.loads(before.read_text(encoding="utf-8")) if before.exists() else {}
    if (prev_doc and prev_doc.get("commit") == "581a866" and prev_doc.get("commit") != result.get("commit")
            and prev_doc.get("fixtures") == result.get("fixtures")):
        try:
            prev = prev_doc["profiles"]
            drops = {p: round(1 - result["profiles"][p]["video_kbps"] / prev[p]["video_kbps"], 3)
                     for p in result["profiles"] if p in prev}
            result["bitrate_drop_vs_before"] = drops
            result["acceptance"]["bitrate_drop_ge_50pct"] = len(drops) == 4 and all(d >= 0.5 for d in drops.values())
        except (KeyError, ValueError, ZeroDivisionError):
            pass
    result["acceptance"]["met"] = all(result["acceptance"].values())
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    res = main(Path(sys.argv[1]).resolve())
    text = json.dumps(res, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
