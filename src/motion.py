"""
src/motion.py
=============
AI motion for a still that opens the video (plan item 7): a few seconds of subtle, realistic
movement generated from a REAL verified photo. Only where the planner picked a photo (real footage and
our own graphics come first), at most MOTION_MAX_CLIPS per video.

Features:
1. Archetype-Aware Classification:
   - 'megastructure': 24mm wide-angle aerial descent / cliffside push-in
   - 'relic_artifact': 100mm macro probe rack-focus on micro-details/gears
   - 'riddle_event': 3D forensic cartography and document macro sweep
2. Material-Science Grounding:
   - Extracts authentic physical substrates from fact sheet evidence (e.g. Deccan basalt,
     oxidized bronze alloy, aged vellum) and enforces strict morphological rigidity.
3. Dual-Layer Anti-Hallucination Gate:
   - Evaluates generated video using mathematical homography keypoint tracking (src/video_qa.py).
   - If non-rigid warping, melting architecture, or phantom objects are detected, the clip is
     rejected and automatically falls back to 100% authentic 2.5D depth parallax (src/parallax.py).
"""

import base64
import io
import json
import logging
import subprocess
import time
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from PIL import Image

from . import config, media_budget
from .footage import download, probe
from .typography import cover_crop
from .video_qa import verify_motion_hook_integrity

log = logging.getLogger(__name__)

last_fallback_reason: Optional[str] = None

SECONDS_OUT = 6.0  # trimmed length used in the video (longest beat plus transitions)
KLING_EST_PRICE_PER_SEC = 0.14  # estimate for the budget guard; Kling bills per task
POLL_SECONDS, TIMEOUT_SECONDS = 10, 600

# Archetype keyword patterns
_RELIC_KEYWORDS = {
    "mechanism", "machine", "device", "gear", "astrolabe", "computer", "disc", "disk",
    "manuscript", "battery", "papyrus", "scroll", "tablet", "shroud", "relic",
    "artifact", "skull", "sword", "crown", "dagger", "jewel", "relics", "bronze"
}
_MEGASTRUCTURE_KEYWORDS = {
    "temple", "pyramid", "city", "caves", "cave", "wall", "monolith", "trench", "tomb",
    "citadel", "fortress", "palace", "catacomb", "underground", "excavation", "quarry",
    "structure", "architecture", "megalith", "ruins", "monument"
}
_RIDDLE_KEYWORDS = {
    "collapse", "event", "disappearance", "expedition", "signal", "blast",
    "incident", "strike", "war", "chronicle", "vanishing", "flight", "voyage"
}


class MotionError(RuntimeError):
    pass


def classify_archetype(topic: Optional[Dict[str, Any]] = None, fact_sheet: Optional[Dict[str, Any]] = None) -> str:
    """Classifies topic into 'megastructure', 'relic_artifact', or 'riddle_event' using word boundary matching."""
    text_corpus = ""
    if topic:
        text_corpus += f"{topic.get('title', '')} {topic.get('id', '')} {topic.get('category', '')} "
        text_corpus += " ".join(topic.get("key_facts", [])) + " "
    if fact_sheet:
        for f in fact_sheet.get("facts", []):
            text_corpus += f.get("statement", "") + " "
    corpus = text_corpus.lower()

    # Distinguish "bronze age" (historical era / collapse) from "bronze" (artifact material)
    normalized_corpus = corpus.replace("bronze age", "era_bronze_age")

    riddle_score = sum(1 for w in _RIDDLE_KEYWORDS if re.search(r"\b" + re.escape(w) + r"\b", normalized_corpus))
    relic_score = sum(1 for w in _RELIC_KEYWORDS if re.search(r"\b" + re.escape(w) + r"\b", normalized_corpus))
    mega_score = sum(1 for w in _MEGASTRUCTURE_KEYWORDS if re.search(r"\b" + re.escape(w) + r"\b", normalized_corpus))

    if relic_score > 0 and relic_score >= riddle_score and relic_score >= mega_score:
        return "relic_artifact"
    if riddle_score > 0 and riddle_score >= mega_score:
        return "riddle_event"
    if mega_score > 0:
        return "megastructure"
    return "megastructure"


def extract_material_substrate(topic: Optional[Dict[str, Any]] = None, fact_sheet: Optional[Dict[str, Any]] = None) -> str:
    """Extracts the authentic geological or physical material substrate from topic evidence."""
    corpus = ""
    if topic:
        corpus += f"{topic.get('title', '')} {topic.get('id', '')} " + " ".join(topic.get("key_facts", [])) + " "
    if fact_sheet:
        for f in fact_sheet.get("facts", []):
            corpus += f.get("statement", "") + " "
            for ev in f.get("evidence", []):
                corpus += ev.get("quote", "") + " "
    c = corpus.lower()

    # Materials only, never topic names: the evidence says what a thing is made of.
    if "basalt" in c or "deccan" in c:
        return "Deccan Traps tholeiitic basalt with dark volcanic patina and vertical chisel drainage grooves"
    if "bronze" in c or "gear" in c:
        return "Authentic oxidized ancient bronze alloy with verdigris copper carbonate patina and hand-cut gear teeth"
    if "tuff" in c or "cappadocia" in c:
        return "Carved volcanic tuff stone with subterranean atmospheric mineral patination"
    if "concrete" in c or "pozzolanic" in c:
        return "Roman pozzolanic self-healing lime-clast concrete with volcanic ash matrix"
    if "parchment" in c or "vellum" in c or "manuscript" in c:
        return "Aged animal vellum parchment with medieval iron gall ink inscriptions"
    if "limestone" in c:
        return "Weathered megalithic limestone with natural sedimentary strata and quarry marks"

    arch = classify_archetype(topic, fact_sheet)
    if arch == "relic_artifact":
        return "Solid unyielding ancient alloy and weathered historical relic surface"
    if arch == "riddle_event":
        return "Archival documentation, cartographic vellum, and historical artifact surface"
    return "Solid carved geological bedrock and weathered stone masonry"


def build_motion_prompt(archetype: str, title: str, substrate: str) -> str:
    """A "living photograph": the camera stays locked and every solid thing stays exactly as photographed;
    only light and natural elements move. In the 2026-10-20 pilot this kept Kling faithful in 3 of 3
    clips, where camera-move prompts had produced warped architecture (2 of 2 rejected)."""
    keep = (f"Locked-off tripod camera, no camera movement. Everything solid stays exactly as in the photo: no change "
            f"to the shape, edges, carvings, inscriptions or proportions of {title} ({substrate}), and nothing new appears.")
    if archetype == "relic_artifact":
        motion_ = "Only a slow, soft sweep of museum light across the surface and faint dust drifting in the air."
    elif archetype == "riddle_event":
        motion_ = "Only a gentle change of light across the scene and faint dust drifting in the air."
    else:
        motion_ = ("Only gentle natural motion that is already in the photo: moving light and soft shadows, faint dust, "
                   "and any clouds, water, foliage or people present moving naturally.")
    return f"Living photograph. {keep} {motion_} No text, no new objects, silent."


PROMPT = build_motion_prompt("megastructure", "historical monument", "weathered stone masonry")


def pre_condition_anchor_frame(image: Path, out_path: Optional[Path] = None, archetype: str = "megastructure") -> Path:
    """Pre-conditions the authentic archival photo: crops to vertical 9:16 centering salient features."""
    if not Path(image).exists():
        return image
    target_out = out_path or image.with_name(f"{image.stem}_anchor.jpg")
    try:
        im = Image.open(image).convert("RGB")
    except Exception:
        return image
    w, h = im.size

    # 9:16 target aspect ratio
    target_ratio = 9.0 / 16.0
    current_ratio = w / h

    if current_ratio > target_ratio:
        # Image is wider than 9:16: crop sides
        new_w = int(h * target_ratio)
        if archetype == "relic_artifact":
            # Exact center crop for relics
            left = (w - new_w) // 2
        else:
            # Subtle top-weighted center crop for landscapes (captures cliff/monument facade)
            left = (w - new_w) // 2
        im = im.crop((left, 0, left + new_w, h))
    else:
        # Image is taller than 9:16: crop top/bottom
        new_h = int(w / target_ratio)
        top = (h - new_h) // 3 if archetype == "megastructure" else (h - new_h) // 2
        im = im.crop((0, top, w, top + new_h))

    im = im.resize((1080, 1920), Image.Resampling.LANCZOS)
    im.save(target_out, "JPEG", quality=95)
    return target_out


def _jpeg_b64(image: Path) -> str:
    """The still, cropped to 9:16 as the video will show it, as base64 JPEG."""
    im = cover_crop(image, (1080, 1920)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


# Share of 30x30 regions (on a 270x480 thumbnail) whose content may change across the clip. Calibrated
# on the 2026-10-20 pilot: faithful clips (including moving sunlight and walking visitors) stayed at or
# below 24%; the one that rewrote the scene reached 64%. Six clips is a small sample: this catches large
# rewrites, not subtle ones, so a person still watches the hook before approval.
MAX_CONTENT_CHANGE = 0.40


def changed_share(first: Any, later: List[Any], threshold: float = 18.0) -> float:
    """Worst share of regions whose mean absolute difference from the first frame exceeds threshold."""
    import numpy as np
    h, w = first.shape
    th, tw = h // 30, w // 30
    worst = 0.0
    for frame in later:
        diff = np.abs(frame[:th * 30, :tw * 30] - first[:th * 30, :tw * 30])
        tiles = diff.reshape(th, 30, tw, 30).mean(axis=(1, 3))
        worst = max(worst, float((tiles > threshold).mean()))
    return worst


def content_change_share(clip: Path) -> float:
    """How much of the frame's content changes across the clip (see MAX_CONTENT_CHANGE)."""
    import io
    import numpy as np
    from PIL import ImageFilter

    def grab(t: float):
        png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60).stdout
        im = Image.open(io.BytesIO(png)).convert("L").resize((270, 480)).filter(ImageFilter.GaussianBlur(2))
        return np.asarray(im, dtype=np.float32)

    try:
        times = [SECONDS_OUT * f for f in (0.25, 0.5, 0.75, 0.95)]
        return changed_share(grab(0.1), [grab(t) for t in times])
    except Exception as e:  # unmeasurable: treat as changed, never as faithful
        log.warning("Could not measure the clip's content change (%s).", e)
        return 1.0


def _keep_rejected(clip: Path, frames: List[Path], qa: Dict[str, Any]) -> None:
    """A rejected clip was paid for: keep it, the frames the check compared and its measurements in
    rejected/ beside it, so the geometry threshold can be calibrated from real cases (and the
    operator can see what was refused). It is never used in a video from there."""
    kept = clip.parent / "rejected"
    kept.mkdir(exist_ok=True)
    try:
        clip.replace(kept / clip.name)
        for frame in frames:
            if frame.exists():
                frame.replace(kept / frame.name)
        (kept / f"{clip.stem}.qa.json").write_text(json.dumps(qa, indent=1, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("Could not keep the rejected clip (%s); deleting it.", e)
        clip.unlink(missing_ok=True)


def _finish(raw: Path, out: Path, archetype: str = "megastructure") -> Path:
    """Probe locally, keep SECONDS_OUT, drop audio, re-encode to 1080x1920 H.264, and verify homography."""
    info = probe(raw)
    if info["duration"] < SECONDS_OUT - 0.5:
        raise MotionError(f"clip too short ({info['duration']:.1f}s)")

    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", str(raw),
            "-t", f"{SECONDS_OUT:.2f}", "-an",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
            "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", str(out)
        ],
        check=True, timeout=300, capture_output=True
    )
    raw.unlink(missing_ok=True)

    # Extract first and near-last frame to verify against morphological hallucination
    f0 = out.with_name(f"{out.stem}_verify_f0.jpg")
    f_end = out.with_name(f"{out.stem}_verify_fend.jpg")
    try:
        try:  # one frame each: seconds, never minutes; a hung ffmpeg must not hold the run
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.2", "-i", str(out), "-frames:v", "1", str(f0)],
                           check=True, timeout=120)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{SECONDS_OUT - 1.2:.2f}", "-i", str(out), "-frames:v", "1",
                            str(f_end)], check=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            raise MotionError("Frame extraction for the fidelity check timed out.") from e
        qa = verify_motion_hook_integrity(f0, f_end, archetype=archetype)
        if not qa.get("pass", False):
            log.warning("AI video motion hook failed anti-hallucination verification: %s", qa.get("reason"))
            _keep_rejected(out, [f0, f_end], qa)
            raise MotionError(f"Anti-hallucination homography check failed: {qa.get('reason')}")
        changed = content_change_share(out)
        qa["content_change_share"] = round(changed, 3)
        if changed > MAX_CONTENT_CHANGE:
            # The homography check passed a clip that painted sky over Kailasa's rock overhang: the
            # camera geometry held while the content was rewritten. Large regional change is rejected.
            qa["pass"], qa["reason"] = False, f"{changed:.0%} of the frame's regions changed content"
            _keep_rejected(out, [f0, f_end], qa)
            raise MotionError(f"Content-fidelity check failed: {qa['reason']}")
        log.info("AI motion hook verified: %s (inliers=%d, ratio=%.2f, content change %.0f%%)", qa.get("reason"),
                 qa.get("inliers", 0), qa.get("inlier_ratio", 0.0), 100 * changed)
    finally:
        f0.unlink(missing_ok=True)
        f_end.unlink(missing_ok=True)

    return out


# ----------------------------------------------------------------- Kling
def kling_available() -> bool:
    return config.is_configured(config.KLING_API_KEY)


def animate_kling(image: Path, out: Path, prompt: Optional[str] = None, archetype: str = "megastructure",
                  on_billed: Optional[Callable[[], None]] = None) -> Path:
    base = "https://api-singapore.klingai.com"
    headers = {"Authorization": f"Bearer {config.KLING_API_KEY}", "Content-Type": "application/json"}
    active_prompt = prompt or PROMPT
    body = {
        "contents": [{"type": "prompt", "text": active_prompt}, {"type": "first_frame", "url": _jpeg_b64(image)}],
        "settings": {"resolution": "1080p", "duration": 6, "audio": "off", "multi_shot": False},
        "options": {"watermark_info": {"enabled": False}}
    }
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
            if on_billed:
                on_billed()  # Kling bills a generated clip whether or not our checks accept it
            raw = out.with_name(out.stem + "_raw.mp4")
            download(video["url"], "kling", raw)
            try:
                return _finish(raw, out, archetype=archetype)
            except TypeError:
                return _finish(raw, out)
    raise MotionError("Kling did not finish in time")


# ----------------------------------------------------------------- chain
def providers(
    topic: Optional[Dict[str, Any]] = None,
    fact_sheet: Optional[Dict[str, Any]] = None
) -> List[Tuple[str, Callable[[], bool], Callable[[Path, Path], Path], float]]:
    """(name, available, animate, estimated cost of one clip), best first."""
    archetype = classify_archetype(topic, fact_sheet)
    substrate = extract_material_substrate(topic, fact_sheet)
    title = topic.get("title", "historical subject") if topic else "historical subject"
    prompt = build_motion_prompt(archetype, title, substrate)

    kling_cost = 6 * KLING_EST_PRICE_PER_SEC

    return [
        ("kling-3.0", kling_available, lambda img, out, on_billed=None: animate_kling(
            img, out, prompt=prompt, archetype=archetype, on_billed=on_billed), kling_cost),
        # Veo 3.1 Preview was retired by Google (shut down 2026-10-22). Its successor, Gemini Omni
        # Flash, uses a different API and has no published per-second price yet; not wired in.
    ]


def animate(
    db,
    image: Path,
    out: Path,
    topic: Optional[Dict[str, Any]] = None,
    fact_sheet: Optional[Dict[str, Any]] = None
) -> Optional[Tuple[Path, str]]:
    """(clip, provider) from the first provider that is configured, within budget and succeeds;
    None means the still stays a still (and gets the free 2.5D parallax treatment)."""
    archetype = classify_archetype(topic, fact_sheet)
    anchor_img = pre_condition_anchor_frame(image, out.with_name(f"{out.stem}_anchor.jpg"), archetype=archetype)

    try:
        prov_list = providers(topic, fact_sheet)
    except TypeError:
        prov_list = providers()

    global last_fallback_reason
    last_fallback_reason = None
    fallback_reasons: List[str] = []

    for name, available, run, cost in prov_list:
        if not available():
            fallback_reasons.append(f"{name}: unconfigured (missing API key)")
            continue
        if not media_budget.allow(db, cost):
            log.info("Motion: %s skipped (media budget).", name)
            fallback_reasons.append(f"{name}: media budget limit reached")
            continue
        billed: List[bool] = []

        def on_billed(name=name, cost=cost, billed=billed):
            # Record spend the moment the provider has made (and charged for) a clip, so a clip
            # our checks then reject still counts against the monthly budget.
            if not billed:
                billed.append(True)
                media_budget.record(db, name, out.name, cost)

        try:
            clip = run(anchor_img, out, on_billed=on_billed)
            _record_lineage(image, anchor_img, clip, name)
            if fallback_reasons:
                last_fallback_reason = "; ".join(fallback_reasons)
            on_billed()  # providers that don't report billing are charged on success, as before
            return clip, name
        except (MotionError, requests.RequestException, ValueError, OSError, subprocess.SubprocessError) as e:
            log.warning("Motion via %s failed (%s); trying the next option.", name, e)
            fallback_reasons.append(f"{name} failed ({e})")
            continue

    if fallback_reasons:
        last_fallback_reason = "; ".join(fallback_reasons)
    return None


def _record_lineage(photo: Path, anchor: Path, clip, provider: str) -> None:
    """Clip <- anchor frame <- photo, so the clip's rights and credit resolve to the photo's."""
    from .lineage import record_derived
    clip_path = Path(clip[0] if isinstance(clip, tuple) else clip)
    try:
        if Path(anchor).resolve() != Path(photo).resolve():
            record_derived(anchor, [photo], "motion_anchor_frame")
        record_derived(clip_path, [anchor], "ai_motion", {"provider": provider, "ai_generated": True})
    except (ValueError, OSError) as e:
        log.warning("Could not record the motion clip's lineage (%s); the release gate will hold it.", e)


def apply(
    db,
    scenes: List[Path],
    work_dir: Path,
    memo: Optional[dict] = None,
    topic: Optional[Dict[str, Any]] = None,
    fact_sheet: Optional[Dict[str, Any]] = None,
    allow_paid: bool = True,
) -> Tuple[List[Path], List[str]]:
    """Animates up to MOTION_MAX_CLIPS photos, the opening beat first. Returns (scenes, providers used).
    `memo` ({photo: (clip, provider)}) carries clips across a re-plan so nothing is paid for twice.
    allow_paid=False (dry runs without --paid-motion): no paid clips, local parallax only."""
    memo = {} if memo is None else memo
    from .typography import is_video
    if not allow_paid or config.MEDIA_MONTHLY_BUDGET_USD <= 0 or config.MOTION_MAX_CLIPS <= 0:
        return scenes, []
    scenes, used = list(scenes), []
    work_dir.mkdir(parents=True, exist_ok=True)

    # Zero-waste guard: a clip already made in this run (e.g. before a re-plan) is reused, but only
    # where its own photo still appears: placed on another beat it would show the wrong subject.
    if memo:
        reused = []
        for photo, (existing_clip, provider) in memo.items():
            if Path(existing_clip).is_file():
                for i in range(len(scenes)):
                    if str(scenes[i]) == photo:
                        scenes[i] = existing_clip
                        reused.append(provider)
                        break
        if reused:
            return scenes, reused

    for i in range(len(scenes)):  # the opening beat first, then in order
        if len(used) >= config.MOTION_MAX_CLIPS:
            break
        if is_video(scenes[i]):
            continue  # footage, graphics or a manual hook clip already move
        key = str(scenes[i])
        try:
            result = memo.get(key) or animate(db, Path(scenes[i]), work_dir / f"motion_{len(memo) + 1:02d}.mp4", topic=topic, fact_sheet=fact_sheet)
        except TypeError:
            result = memo.get(key) or animate(db, Path(scenes[i]), work_dir / f"motion_{len(memo) + 1:02d}.mp4")
        if result is not None:
            memo[key] = result
        if result is None:
            break  # nothing available or affordable: stop asking
        scenes[i] = result[0]
        used.append(result[1])
    return scenes, used
