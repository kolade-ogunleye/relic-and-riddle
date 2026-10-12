"""
src/motion.py
=============
AI motion for a still that opens the video (plan item 7): a few seconds of subtle, realistic
movement generated from a REAL photo. Only where the planner picked a photo (real footage and
our own graphics come first), at most MOTION_MAX_CLIPS per video.

Provider chain (each used only if configured and within MEDIA_MONTHLY_BUDGET_USD):
  1. Google Veo 3.1 (MOTION_VEO_MODEL; Fast by default, Lite/Standard selectable)
     docs: https://ai.google.dev/gemini-api/docs/veo  (1080p requires 8 s clips)
  2. Kling 3.0  docs: https://kling.ai/document-api/api/video/3-0-omni/image-to-video
  3. otherwise the existing free 2.5D parallax (src/parallax.py) handles the still.
Runway and a local Wan model are not wired yet (see docs/sources.md for why).

Rules: the prompt allows camera movement and natural ambient motion only (no new objects,
people, text or structures); generated audio is stripped; the result is re-encoded locally;
the Reel carries the AI label; the review card names the provider.
"""

import base64
import io
import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import requests
from PIL import Image

from . import config, media_budget
from .footage import download, probe
from .typography import cover_crop

log = logging.getLogger(__name__)

SECONDS_OUT = 6.0  # trimmed length used in the video (longest beat plus transitions)
PROMPT = ("Animate this real photograph with a slow, smooth cinematic camera push-in and only natural ambient motion "
          "already implied by the scene (drifting dust, flickering light, moving clouds or water). Keep every object, "
          "structure, inscription and person exactly as in the photo. Do not add, remove or change anything. Photorealistic.")
VEO_PRICE_PER_SEC = {"veo-3.1-fast-generate-preview": 0.12, "veo-3.1-lite-generate-preview": 0.08, "veo-3.1-generate-preview": 0.40}
KLING_EST_PRICE_PER_SEC = 0.14  # estimate for the budget guard; Kling bills per task (see its pricing page)
POLL_SECONDS, TIMEOUT_SECONDS = 10, 600


class MotionError(RuntimeError):
    pass


def _jpeg_b64(image: Path) -> str:
    """The still, cropped to 9:16 as the video will show it, as base64 JPEG."""
    im = cover_crop(image, (1080, 1920)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def _finish(raw: Path, out: Path) -> Path:
    """Probe locally, keep SECONDS_OUT, drop audio, re-encode to 1080x1920 H.264."""
    info = probe(raw)
    if info["duration"] < SECONDS_OUT - 0.5:
        raise MotionError(f"clip too short ({info['duration']:.1f}s)")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", str(raw), "-t", f"{SECONDS_OUT:.2f}", "-an",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(out)],
                   check=True, timeout=300, capture_output=True)
    raw.unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------- Veo
def veo_available() -> bool:
    return config.is_configured(config.GEMINI_API_KEY)


def animate_veo(image: Path, out: Path) -> Path:
    model = config.MOTION_VEO_MODEL
    base = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}
    body = {"instances": [{"prompt": PROMPT, "image": {"inlineData": {"mimeType": "image/jpeg", "data": _jpeg_b64(image)}}}],
            "parameters": {"aspectRatio": "9:16", "durationSeconds": 8, "resolution": "1080p", "personGeneration": "allow_adult"}}
    resp = requests.post(f"{base}/models/{model}:predictLongRunning", headers=headers, json=body, timeout=60)
    if resp.status_code >= 400:
        raise MotionError(f"Veo rejected the request ({resp.status_code}): {resp.text[:300]}")
    op = resp.json().get("name")
    if not op:
        raise MotionError("Veo returned no operation name")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        state = requests.get(f"{base}/{op}", headers=headers, timeout=30).json()
        if state.get("error"):
            raise MotionError(f"Veo failed: {state['error'].get('message', state['error'])}")
        if state.get("done"):
            samples = state.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
            if not samples:
                raise MotionError(f"Veo returned no video (possibly filtered): {str(state)[:300]}")
            raw = out.with_name(out.stem + "_raw.mp4")
            download(samples[0]["video"]["uri"], "veo", raw, extra_headers={"x-goog-api-key": config.GEMINI_API_KEY})
            return _finish(raw, out)
    raise MotionError("Veo did not finish in time")


# ----------------------------------------------------------------- Kling
def kling_available() -> bool:
    return config.is_configured(config.KLING_API_KEY)


def animate_kling(image: Path, out: Path) -> Path:
    base = "https://api-singapore.klingai.com"
    headers = {"Authorization": f"Bearer {config.KLING_API_KEY}", "Content-Type": "application/json"}
    body = {"contents": [{"type": "prompt", "text": PROMPT}, {"type": "first_frame", "url": _jpeg_b64(image)}],
            "settings": {"resolution": "1080p", "duration": 6, "audio": "off", "multi_shot": False},
            "options": {"watermark_info": {"enabled": False}}}
    resp = requests.post(f"{base}/image-to-video/kling-3.0", headers=headers, json=body, timeout=60)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or data.get("code") not in (0, None):
        raise MotionError(f"Kling rejected the request ({resp.status_code}): {data.get('message') or resp.text[:300]}")
    task_id = (data.get("data") or {}).get("id")
    if not task_id:
        raise MotionError("Kling returned no task id")
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        q = requests.get(f"{base}/tasks", headers=headers, params={"task_ids": task_id}, timeout=30).json()
        task = (q.get("data") or [{}])[0]
        if task.get("status") == "failed":
            raise MotionError(f"Kling failed: {task.get('message', '')}")
        if task.get("status") == "succeeded":
            video = next((o for o in task.get("outputs", []) if o.get("type") == "video" and o.get("url")), None)
            if not video:
                raise MotionError("Kling returned no video")
            raw = out.with_name(out.stem + "_raw.mp4")
            download(video["url"], "kling", raw)
            return _finish(raw, out)
    raise MotionError("Kling did not finish in time")


# ----------------------------------------------------------------- chain
def providers() -> List[Tuple[str, Callable[[], bool], Callable[[Path, Path], Path], float]]:
    """(name, available, animate, estimated cost of one clip), best first."""
    veo_cost = 8 * VEO_PRICE_PER_SEC.get(config.MOTION_VEO_MODEL, 0.40)
    return [(config.MOTION_VEO_MODEL, veo_available, animate_veo, veo_cost),
            ("kling-3.0", kling_available, animate_kling, 6 * KLING_EST_PRICE_PER_SEC)]


def animate(db, image: Path, out: Path) -> Optional[Tuple[Path, str]]:
    """(clip, provider) from the first provider that is configured, within budget and succeeds;
    None means the still stays a still (and gets the free parallax treatment)."""
    for name, available, run, cost in providers():
        if not available():
            continue
        if not media_budget.allow(db, cost):
            log.info("Motion: %s skipped (media budget).", name)
            continue
        try:
            clip = run(image, out)
        except (MotionError, requests.RequestException, ValueError, OSError, subprocess.SubprocessError) as e:
            log.warning("Motion via %s failed (%s); trying the next option.", name, e)
            continue
        media_budget.record(db, name, out.name, cost)
        return clip, name
    return None


def apply(db, scenes: List[Path], work_dir: Path, memo: Optional[dict] = None) -> Tuple[List[Path], List[str]]:
    """Animates up to MOTION_MAX_CLIPS photos, the opening beat first. Returns (scenes, providers used).
    `memo` ({photo: (clip, provider)}) carries clips across a re-plan so nothing is paid for twice."""
    memo = {} if memo is None else memo
    from .typography import is_video
    if config.MEDIA_MONTHLY_BUDGET_USD <= 0 or config.MOTION_MAX_CLIPS <= 0:
        return scenes, []
    scenes, used = list(scenes), []
    work_dir.mkdir(parents=True, exist_ok=True)
    for i in range(len(scenes)):  # the opening beat first, then in order
        if len(used) >= config.MOTION_MAX_CLIPS:
            break
        if is_video(scenes[i]):
            continue  # footage, graphics or a manual hook clip already move
        key = str(scenes[i])
        result = memo.get(key) or animate(db, Path(scenes[i]), work_dir / f"motion_{len(memo) + 1:02d}.mp4")
        if result is not None:
            memo[key] = result
        if result is None:
            break  # nothing available or affordable: stop asking
        scenes[i] = result[0]
        used.append(result[1])
    return scenes, used
