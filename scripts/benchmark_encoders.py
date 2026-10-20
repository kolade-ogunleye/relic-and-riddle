"""Compare configured CPU and hardware encoders on a reproducible mixed sample."""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.config import VIDEO_CRF, VIDEO_PRESET
from src.credits import sha256_file
from src.encoding import get_video_encoder


def run(command):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=300)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("master", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--times", nargs=3, type=float, default=[1, 27, 75])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    parts = []
    for index, timestamp in enumerate(args.times):
        path = args.output / f"reference_{index}.mkv"
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(timestamp), "-i", str(args.master),
             "-t", "2", "-an", "-c:v", "ffv1", "-level", "3", str(path)])
        parts.append(path)
    reference = args.output / "reference.mkv"
    run(["ffmpeg", "-y", "-v", "error", *[arg for path in parts for arg in ("-i", str(path))],
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[out]", "-map", "[out]",
         "-c:v", "ffv1", "-level", "3", str(reference)])
    encoders = {"cpu": ["-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF)]}
    detected = get_video_encoder()
    if "h264_nvenc" in detected:
        encoders["nvenc"] = detected
    results = []
    for name, encoder in encoders.items():
        output = args.output / f"{name}.mp4"
        started = time.perf_counter()
        run(["ffmpeg", "-y", "-v", "error", "-i", str(reference), "-an", *encoder,
             "-pix_fmt", "yuv420p", str(output)])
        elapsed = time.perf_counter() - started
        comparison = run(["ffmpeg", "-hide_banner", "-i", str(reference), "-i", str(output),
                          "-filter_complex", "[0:v][1:v]ssim", "-f", "null", "-"])
        match = re.search(r"All:([0-9.]+)", comparison.stderr)
        result = {"encoder": name, "arguments": encoder, "seconds": elapsed,
                  "bytes": output.stat().st_size, "ssim": float(match.group(1)) if match else None}
        results.append(result)
        print(json.dumps(result), flush=True)
    (args.output / "benchmark.json").write_text(json.dumps({"source": str(args.master.resolve()),
        "source_sha256": sha256_file(args.master), "sample_times": args.times,
        "sample_duration": 6, "results": results}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
