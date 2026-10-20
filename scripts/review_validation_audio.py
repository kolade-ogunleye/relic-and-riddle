"""Automated listening review of a real encoded master; never a human sign-off."""
import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config
from src.credits import sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("master", type=Path)
    args = parser.parse_args()
    master = args.master.resolve()
    digest = sha256_file(master)
    if not digest or not config.is_configured(config.GEMINI_FREE_API_KEY):
        raise ValueError("Existing master and configured free review provider are required")
    listening_copy = master.with_suffix(".listening.mp3")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(master), "-vn",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(listening_copy)],
                   check=True, timeout=120)
    audio = base64.b64encode(listening_copy.read_bytes()).decode("ascii")
    if len(audio) > 19_000_000:
        raise ValueError("Audio exceeds the inline review request limit")
    prompt = ("Listen to the complete attached soundtrack from a documentary/reel master. "
              "Assess narration intelligibility, music masking speech, audible clipping/distortion, "
              "unnatural gaps, abrupt start/end or cut-off words. Do not infer quality from metadata. "
              "Return JSON with status ('pass' or 'needs_review'), summary, and findings: "
              "an array of objects with timestamp_seconds, severity ('minor' or 'blocking'), detail. "
              "Use needs_review for any blocking defect or inability to assess. "
              "This is an automated listening assessment, not a human approval.")
    body = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {
        "mime_type": "audio/mp3", "data": audio}}]}],
        "generationConfig": {"responseMimeType": "application/json"}}
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{config.FREE_CHECKER_MODEL}:generateContent",
        headers={"x-goog-api-key": config.GEMINI_FREE_API_KEY}, json=body, timeout=120)
    # Do not expose request headers or URLs containing credentials on failure.
    if response.status_code != 200:
        raise RuntimeError(f"Audio provider did not complete review (HTTP {response.status_code})")
    parts = response.json()["candidates"][0]["content"]["parts"]
    report = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
    if report.get("status") not in ("pass", "needs_review") or not isinstance(report.get("findings"), list):
        raise ValueError("Incomplete audio review response")
    if sha256_file(master) != digest:
        raise ValueError("Master changed during review")
    report.update(review_type="automated_listening", human_approval=False,
                  provider=config.FREE_CHECKER_MODEL, master_sha256=digest,
                  listening_copy=str(listening_copy), listening_sha256=sha256_file(listening_copy),
                  limitation="192 kbps MP3 review derivative; encoded master measurements are separate")
    destination = master.parent / "audio_listening_review.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
