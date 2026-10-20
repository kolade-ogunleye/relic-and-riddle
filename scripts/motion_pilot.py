"""
A small paid pilot (about $5): can Kling 3.0 or Gemini Omni Flash turn our real, licensed photos
into a "living photograph" hook (camera locked, the structure untouched, only light, sky, dust or
water moving) that passes our geometry check and beats the free local parallax?

Each provider animates the same photos with the same prompt. Every clip goes through motion._finish
(trim to the hook length, 1080x1920, homography warping check); rejected clips are kept with their
measurements. Spend is recorded in the media ledger. Results and frames: staging/motion_pilot/.

Usage:
  python scripts/motion_pilot.py            # list photos and the cost estimate
  python scripts/motion_pilot.py --apply
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, motion  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402
from src.media_budget import record  # noqa: E402

OUT = ROOT / "staging" / "motion_pilot"
PHOTOS = [  # (topic, file, archetype)
    ("kailasa_temple_monolith", "03_commons.jpg", "megastructure"),
    ("gol_gumbaz_whispering_gallery", "01_commons.jpg", "megastructure"),
    ("antikythera_mechanism", "03_commons.jpg", "relic_artifact"),
]
PROMPT = ("Living photograph. Locked-off tripod camera, no camera movement. Everything solid stays exactly as in the "
          "photo: no change to the building's or object's shape, carvings, edges or proportions, and nothing new "
          "appears. Only gentle natural motion: drifting clouds and shifting sunlight with soft moving shadows, faint "
          "dust in the air. No people, no text, about 6 seconds, silent.")
KLING_COST = 0.84            # 6 s at 1080p, as billed in the ledger
OMNI_COST_PER_SECOND = 0.10  # 720p, Gemini API pricing page


def omni(image: Path, raw: Path) -> float:
    """Gemini Omni Flash image-to-video (Interactions API). Returns the clip's seconds."""
    body = {"model": "gemini-omni-1.1-flash",
            "input": [{"type": "image", "mime_type": "image/jpeg", "data": motion._jpeg_b64(image)},
                      {"type": "text", "text": PROMPT}],
            "response_format": {"type": "video", "aspect_ratio": "9:16", "resolution": "720p", "delivery": "uri"},
            "generation_config": {"video_config": {"task": "image_to_video"}}}
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}
    base = "https://generativelanguage.googleapis.com/v1beta"
    r = requests.post(f"{base}/interactions", headers=headers, json=body, timeout=600)
    if r.status_code >= 400:
        raise motion.MotionError(f"Omni rejected the request ({r.status_code}): {r.text[:300]}")
    data = r.json()
    video = None
    for step in data.get("steps", []):
        for item in step.get("content", []) or []:
            if item.get("type") == "video":
                video = item
    if not video:
        raise motion.MotionError(f"Omni returned no video: {json.dumps(data)[:300]}")
    if video.get("data"):
        raw.write_bytes(base64.b64decode(video["data"]))
    else:
        file_id = video["uri"].rstrip("/").split("/")[-1].split(":")[0]
        for _ in range(60):
            f = requests.get(f"{base}/files/{file_id}", headers=headers, timeout=30).json()
            if f.get("state") == "ACTIVE":
                break
            time.sleep(5)
        dl = requests.get(f"{base}/files/{file_id}:download", params={"alt": "media"}, headers=headers, timeout=300)
        dl.raise_for_status()
        raw.write_bytes(dl.content)
    return motion.probe(raw)["duration"]


def frames(clip: Path, dest: Path) -> None:
    """First, middle and last frame side by side, for review."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf",
                    "select='eq(n\\,0)+eq(n\\,90)+eq(n\\,165)',scale=360:-1,tile=3x1", "-frames:v", "1", str(dest)],
                   check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Generate (spends about $5)")
    args = parser.parse_args()
    photos = [(t, config.ASSETS_DIR / t / f, a) for t, f, a in PHOTOS if (config.ASSETS_DIR / t / f).exists()]
    print(f"{len(photos)} photos x 2 providers, about ${len(photos) * (KLING_COST + 8 * OMNI_COST_PER_SECOND):.2f}")
    if not args.apply:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    db, results = PipelineDatabase(), []
    for topic, image, archetype in photos:
        for provider in ("kling", "omni"):
            name = f"{topic}_{provider}"
            out, raw = OUT / f"{name}.mp4", OUT / f"{name}_raw.mp4"
            result = {"topic": topic, "provider": provider, "photo": image.name}
            try:
                if provider == "kling":
                    motion.animate_kling(image, out, PROMPT, archetype=archetype,
                                         on_billed=lambda: record(db, "kling-3.0", f"pilot:{name}", KLING_COST))
                else:
                    seconds = omni(image, raw)
                    record(db, "gemini-omni-1.1-flash", f"pilot:{name}", round(seconds * OMNI_COST_PER_SECOND, 2))
                    result["seconds"] = round(seconds, 1)
                    motion._finish(raw, out, archetype=archetype)
                result["verdict"] = "passed the geometry check"
                frames(out, OUT / f"{name}_frames.jpg")
            except motion.MotionError as e:
                result["verdict"] = f"rejected: {e}"
                kept = OUT / "rejected" / f"{name}.mp4"
                if kept.exists():
                    frames(kept, OUT / f"{name}_frames.jpg")
            except Exception as e:  # one provider's failure must not stop the pilot
                result["verdict"] = f"error: {type(e).__name__}: {str(e)[:200]}"
            results.append(result)
            print(f"  {name}: {result['verdict']}")
    (OUT / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
