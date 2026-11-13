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
from .encoding import BT709_FLAGS, to_bt709
from .footage import download, probe
from .typography import cover_crop
from .video_qa import verify_motion_hook_integrity

log = logging.getLogger(__name__)

last_fallback_reason: Optional[str] = None
last_motion_rejection = ""   # "no visible motion" when this run's paid clip was refused for it (fallback_note)

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
    target_out = Path(out_path).with_suffix(".png") if out_path else image.with_name(f"{image.stem}_anchor.png")
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
    im.save(target_out, "PNG")
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


def _grab(clip: Path, t: float, blur: float = 2.0):
    """One frame of `clip` at t seconds, grey, 270x480 and blurred, as float32 (0-255)."""
    import numpy as np
    from PIL import ImageFilter
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60).stdout
    im = Image.open(io.BytesIO(png)).convert("L").resize((270, 480))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im, dtype=np.float32)


def content_change_share(clip: Path) -> float:
    """How much of the frame's content changes across the clip (see MAX_CONTENT_CHANGE)."""
    try:
        times = [SECONDS_OUT * f for f in (0.25, 0.5, 0.75, 0.95)]
        return changed_share(_grab(clip, 0.1), [_grab(clip, t) for t in times])
    except Exception as e:  # unmeasurable: treat as changed, never as faithful
        log.warning("Could not measure the clip's content change (%s).", e)
        return 1.0


# The opposite failure: a clip that does not move. Production Derinkuyu (2026-10-29): a paid Kling clip of a lamp-lit
# corridor passed the homography and content checks (inlier ratio 0.997, 0% content change) because nothing in it
# moved: first and last frames differed by 2.5/255 on average with no global shift, a still for six seconds where
# the free parallax would have pushed in. Two measures on the 270x480 frames, the first frame against four later
# ones; either one shows visible motion:
#   - camera or subject displacement: the 90th percentile of the dense optical-flow magnitude, as a share of the
#     frame height (a push moves the edges, a pan moves everything);
#   - local motion (water, smoke, people, moving light): the share of 30x30 regions whose mean absolute difference
#     from the first frame exceeds MOTION_REGION_DIFF.
# Calibrated (2026-10-29) on synthetic 6.5 s clips encoded as _finish does (H.264 CRF 17), from a dark cave photo
# and a bright daylight one (flow / regions): a still with encoder noise and a 2.5/255 light flicker 0.01-0.02% / 0%;
# a 0.5% push 0.18-0.19% / 0% (fails); a 1% push 0.37-0.40% / 4-10% (the margin); a 2% push 0.74-0.79% / 17-23%;
# a 3% push 1.2% / 37-39%; a 1% pan 0.49% / 17-22%; a 3% pan 1.5% / 47-51%; water rippling over the bottom fifth
# 1.4-2.0% / 19%; smoke drifting over a tenth 0.02-0.3% / 12.5%. The free parallax pushes in 10%. Production's
# rejected-to-be Derinkuyu clip (2.5/255 mean change, no shift) sits with the still, far below both thresholds.
MIN_FLOW_SHARE = 0.005      # 0.5% of the frame height (2.4 px at 480, ~10 px at 1920)
MOTION_REGION_DIFF = 8.0    # a region's mean change, of 255, counted as moving
MIN_MOVING_SHARE = 0.08     # 8% of the regions moving


def motion_amount(clip: Path) -> Dict[str, float]:
    """{"flow_share", "moving_share", "mean_diff"} across the clip (see MIN_FLOW_SHARE). Raises when the clip
    cannot be read: the caller treats that as no motion, never as motion."""
    import cv2
    import numpy as np
    first = _grab(clip, 0.1, blur=1.0)
    later = [_grab(clip, SECONDS_OUT * f, blur=1.0) for f in (0.25, 0.5, 0.75, 0.95)]
    h, w = first.shape
    th, tw = h // 30, w // 30
    flow_share = moving = mean_diff = 0.0
    a = first.astype(np.uint8)
    for frame in later:
        flow = cv2.calcOpticalFlowFarneback(a, frame.astype(np.uint8), None, 0.5, 4, 21, 3, 5, 1.1, 0)
        magnitude = np.hypot(flow[..., 0], flow[..., 1])
        flow_share = max(flow_share, float(np.percentile(magnitude, 90)) / h)
        diff = np.abs(frame - first)
        tiles = diff[:th * 30, :tw * 30].reshape(th, 30, tw, 30).mean(axis=(1, 3))
        moving = max(moving, float((tiles > MOTION_REGION_DIFF).mean()))
        mean_diff = max(mean_diff, float(diff.mean()))
    return {"flow_share": round(flow_share, 5), "moving_share": round(moving, 4), "mean_diff": round(mean_diff, 2)}


def visible_motion(measured: Dict[str, float]) -> bool:
    return measured.get("flow_share", 0.0) >= MIN_FLOW_SHARE or measured.get("moving_share", 0.0) >= MIN_MOVING_SHARE


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
            # BT.709 limited range, tagged, read with the clip's own matrix (QF-01)
            "-vf", ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,"
                    f"{to_bt709(info.get('color_space'), info.get('height') or 0)}"),
            "-c:v", "libx264", "-preset", "medium", "-crf", "17", *BT709_FLAGS, "-pix_fmt", "yuv420p", str(out)
        ],
        check=True, timeout=300, capture_output=True
    )
    raw.unlink(missing_ok=True)
    _gate(out, archetype)
    return out


# The gates' version, in each accepted clip's .qa.json: a clip accepted under older gates (no motion check) is
# measured again before it is reused or carried over (production Derinkuyu, 2026-10-29: the still clip was carried
# into the re-render "already paid for"). 3 (QF-88): the picture check (black, freeze, flashing) joined the gates.
GATE_VERSION = 3


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _gate(out: Path, archetype: str = "megastructure", keep: bool = True) -> Dict[str, Any]:
    """The clip's checks: geometry (homography), content fidelity and visible motion. Returns the measurements
    (also written beside an accepted clip as .qa.json); raises MotionError on a rejection. keep=False (another
    run's clip, checked before it is carried over): a rejected clip is left where it is, never moved or deleted."""
    # Extract first and near-last frame to verify against morphological hallucination
    f0 = out.with_name(f"{out.stem}_verify_f0.jpg")
    f_end = out.with_name(f"{out.stem}_verify_fend.jpg")

    def reject(qa: Dict[str, Any], what: str) -> None:
        if keep:
            _keep_rejected(out, [f0, f_end], qa)
        raise MotionError(f"{what}: {qa.get('reason')}")
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
            reject(qa, "Anti-hallucination homography check failed")
        changed = content_change_share(out)
        qa["content_change_share"] = round(changed, 3)
        if changed > MAX_CONTENT_CHANGE:
            # The homography check passed a clip that painted sky over Kailasa's rock overhang: the
            # camera geometry held while the content was rewritten. Large regional change is rejected.
            qa["pass"], qa["reason"] = False, f"{changed:.0%} of the frame's regions changed content"
            reject(qa, "Content-fidelity check failed")
        try:
            moved = motion_amount(out)
        except Exception as e:  # unmeasurable: treated as a still, never as motion
            log.warning("Could not measure the clip's motion (%s).", e)
            moved = {"flow_share": 0.0, "moving_share": 0.0, "mean_diff": 0.0}
        qa.update(moved)
        if not visible_motion(moved):
            # Paid for, faithful and still: the beat keeps its free parallax push instead (see MIN_FLOW_SHARE).
            qa["pass"], qa["reason"] = False, (
                f"no visible motion (flow {100 * moved['flow_share']:.2f}% of the height < {100 * MIN_FLOW_SHARE:.1f}%, "
                f"{100 * moved['moving_share']:.0f}% of regions moving < {100 * MIN_MOVING_SHARE:.0f}%)")
            log.warning("AI motion clip %s rejected: %s.", out.name, qa["reason"])
            reject(qa, "Motion check failed")
        # QF-88: an AI clip that goes black, freezes or flashes (WCAG 2.3.1) is rejected before it is used. One
        # that cannot be measured here is logged and kept: the release gate decodes the master it goes into, and
        # that check fails closed.
        from .picture_qa import ingest_problems
        qa["picture_check"] = "passed"   # QF-88: read by the release gate's ingest note
        try:
            problems = ingest_problems(out)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            log.warning("AI motion clip %s not picture-checked at ingest (%s); the release gate checks the master.",
                        out.name, e)
            problems, qa["picture_check"] = [], f"not measured ({str(e)[:160]})"
        if problems:
            qa["pass"], qa["reason"] = False, "; ".join(problems)
            reject(qa, "Picture check failed")
        log.info("AI motion hook verified: %s (inliers=%d, ratio=%.2f, content change %.0f%%, flow %.2f%%, "
                 "%.0f%% of regions moving)", qa.get("reason"), qa.get("inliers", 0), qa.get("inlier_ratio", 0.0),
                 100 * changed, 100 * moved["flow_share"], 100 * moved["moving_share"])
        qa.update(archetype=archetype, accepted=True, gate=GATE_VERSION)
        if keep:
            # The accepted clip's measurements too (rejected ones were already kept): the gate's thresholds can
            # only be calibrated from both sides, and production had no rejections to learn from (2026-10-24).
            # The clip's hash ties them to this clip: a reuse trusts them only for the same bytes.
            try:
                qa["sha256"] = _sha256(out)
                out.with_suffix(".qa.json").write_text(json.dumps(qa, indent=1), encoding="utf-8")
            except (OSError, TypeError, ValueError) as e:
                log.debug("Motion QA record not written: %s", e)
        return qa
    finally:
        f0.unlink(missing_ok=True)
        f_end.unlink(missing_ok=True)


def passes_gates(clip: Path, keep: bool = True) -> bool:
    """A clip on disk (this run's, after a restart, or another run's before it is carried over) against today's
    gates. Its .qa.json is trusted only when it records this GATE_VERSION, a pass and this clip's hash; otherwise
    the clip is measured again (the archetype its record names, else the strictest). keep: as for _gate."""
    clip = Path(clip)
    record_path = clip.with_suffix(".qa.json")
    record: Dict[str, Any] = {}
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        if (record.get("gate") == GATE_VERSION and record.get("accepted") and record.get("pass", True)
                and record.get("sha256") == _sha256(clip)):
            return True
    except OSError:
        return False
    try:
        _gate(clip, str(record.get("archetype") or "relic_artifact"), keep=keep)
        return True
    except (MotionError, OSError, ValueError, subprocess.SubprocessError) as e:
        log.warning("Motion: %s is not reused (%s).", clip.name, e)
        return False


# ----------------------------------------------------------------- Kling
_NO_CREDIT = re.compile(r"balance|credit|insufficient|top.?up|recharge|arrears|quota exceeded|"
                                      r"not enough (?:money|funds)", re.IGNORECASE)


def out_of_credit(message: str) -> bool:
    """A provider's refusal for want of credit ("Account balance not enough"), not for the request."""
    return bool(_NO_CREDIT.search(message or ""))


def fallback_note(used: List[str], tried_paid: bool) -> str:
    """The review card's line when no AI motion was made because a paid provider is out of credit, or because this
    run's paid clip showed no visible motion ("" otherwise): "Opening motion: parallax (Kling out of credit)"."""
    if used or not tried_paid:
        return ""
    from . import provider_status
    shut = [n for n in ("kling",) if n in provider_status.capped()]
    if not shut:
        if last_motion_rejection:  # paid for and refused: the operator sees where the money went
            return f"🎞 Opening motion: parallax (the paid clip had {last_motion_rejection}; kept in motion/rejected)"
        return ""
    names = ", ".join(provider_status.NAMES.get(n, n) for n in shut)
    return f"🎞 Opening motion: parallax ({names} out of credit)"


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
        message = str(data.get('message') or resp.text[:300])
        if out_of_credit(message):
            # Said on the dashboard as soon as it is seen, not only in the log (production Derinkuyu, 2026-10-29:
            # "Account balance not enough" fell back to parallax silently).
            from . import provider_status
            provider_status.note_capped("kling", f"the account balance is not enough ({message[:120]}); top up Kling")
        raise MotionError(f"Kling rejected the request ({resp.status_code}): {message}")
    from . import provider_status
    provider_status.clear("kling")  # a request accepted: any out-of-credit notice is over
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
    anchor_img = pre_condition_anchor_frame(image, out.with_name(f"{out.stem}_anchor.png"), archetype=archetype)

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
    seed_dirs: Optional[List[Path]] = None,
    durations: Optional[List[float]] = None,
) -> Tuple[List[Path], List[str]]:
    """Animates up to MOTION_MAX_CLIPS photos, the opening beat first. Returns (scenes, providers used).
    `memo` ({photo: (clip, provider)}) carries clips across a re-plan so nothing is paid for twice.
    `seed_dirs`: other runs' motion folders (the run a regeneration replaces) whose accepted clips are
    copied in and reused. allow_paid=False (dry runs without --paid-motion): no paid clips, local parallax only.
    Only a strong shot is animated, or has a clip reused (animatable): never a context photo (Nazca, 2026-10-28:
    a clip of the highway through the desert, made from a context shot, was carried into the regeneration), nor
    a shot `durations` says lasts longer than the clip (it would freeze).
    The clip is the motion hook: it opens the video. A later beat takes one only as the "one more" of
    MOTION_MAX_CLIPS >= 2, once the opening moves (footage or a hook clip there already, or the clip made for it);
    when the opening still cannot take one, no clip is bought or placed (opening_slots). Production Derinkuyu
    (2026-10-29): the paid clip was placed third, at 12.7 s, with a parallax still opening the video."""
    memo = {} if memo is None else memo
    from .typography import is_video
    global last_motion_rejection
    last_motion_rejection = ""
    if config.MOTION_MAX_CLIPS <= 0:
        return scenes, []
    ok = opening_slots(scenes, durations)
    # A clip this run already paid for is still on disk after a restart or a resume (the memo lives in
    # memory): reusing it costs nothing, so it is used whether or not this pass may buy one. A clip on disk or
    # carried over passes today's checks again first (passes_gates).
    memo.update({k: v for k, v in _clips_on_disk(work_dir).items() if k not in memo})
    for seed in seed_dirs or []:
        memo.update({k: v for k, v in _adopt_clips(work_dir, Path(seed)).items() if k not in memo})
    if not any(ok):
        return scenes, []
    if not allow_paid or config.MEDIA_MONTHLY_BUDGET_USD <= 0:
        reused = _reuse(scenes, memo, ok)
        return reused if reused[1] else (scenes, [])
    scenes, used = list(scenes), []
    work_dir.mkdir(parents=True, exist_ok=True)

    # Zero-waste guard: a clip already made in this run (e.g. before a re-plan) is reused, but only
    # where its own photo still appears: placed on another beat it would show the wrong subject.
    scenes, reused = _reuse(scenes, memo, ok)
    if reused:
        return scenes, reused

    for i in range(len(scenes)):  # the opening beat first, then in order
        if len(used) >= config.MOTION_MAX_CLIPS:
            break
        if is_video(scenes[i]):
            continue  # footage, graphics or a manual hook clip already move
        if not ok[i]:
            continue  # a context shot, one found for a line, or a held shot longer than the clip stays a still
        key = str(scenes[i])
        if key not in memo and can_move(Path(scenes[i])) is False:
            log.info("Motion: no clip bought for %s: nothing its record names can move (water, fire, smoke, sky, "
                     "foliage, people, cloth, dust or sand); it keeps the free parallax.", Path(scenes[i]).name)
            if i == 0:
                break  # the opening stays a still: a later clip would not open the video
            continue
        try:
            result = memo.get(key) or animate(db, Path(scenes[i]), work_dir / f"motion_{len(memo) + 1:02d}.mp4", topic=topic, fact_sheet=fact_sheet)
        except TypeError:
            result = memo.get(key) or animate(db, Path(scenes[i]), work_dir / f"motion_{len(memo) + 1:02d}.mp4")
        if result is not None:
            memo[key] = result
        if result is None:
            if "no visible motion" in (last_fallback_reason or ""):
                last_motion_rejection = "no visible motion"
            break  # nothing available or affordable, or the opening's clip was rejected: stop asking
        scenes[i] = result[0]
        used.append(result[1])
    return scenes, used


def opening_slots(scenes: List[Path], durations: Optional[List[float]] = None) -> List[bool]:
    """Per beat, whether it may take (or have reused) a motion clip. The opening (beat 1) when it is a strong
    still no longer than the clip; a later beat only as the "one more" of MOTION_MAX_CLIPS >= 2, and only when
    the opening moves: it is footage or a clip already, or may take one itself. All False when the opening is a
    still that may not take one (said in the log): the clip would not open the video, so none is paid for."""
    from .typography import is_video
    if not scenes:
        return []

    def fits(i: int) -> bool:
        return animatable(scenes[i]) and not (durations and i < len(durations) and durations[i] > SECONDS_OUT)
    ok = [False] * len(scenes)
    if not is_video(Path(scenes[0])):
        ok[0] = fits(0)
        if not ok[0]:
            longer = bool(durations) and durations[0] > SECONDS_OUT
            log.info("Motion: no clip: the opening shot %s %s, and a clip on a later beat would not open the video.",
                     Path(scenes[0]).name, f"lasts {durations[0]:.1f}s, longer than the {SECONDS_OUT:.0f}s clip"
                     if longer else "is not a Topic or Kept photo")
            return ok
    if config.MOTION_MAX_CLIPS >= 2:
        for i in range(1, len(scenes)):
            ok[i] = fits(i)
    return ok


# What can plausibly move in a photo, from what is already recorded about it (the pool review's verdict, which
# says what the picture shows, and the archive title and description): no new vision call. A clip of solid stone
# under a locked camera is a still (production Derinkuyu, 2026-10-29: "a rolling stone door mechanism and
# corridor", paid for and static). Lamps are left out: an electric lamp's light does not move.
_CAN_MOVE = re.compile(
    r"\b(?:water|waters|river|rivers|sea|seas|ocean|lake|lagoon|waves?|surf|waterfalls?|streams?|fountains?"
    r"|flood\w*|rain|snow\w*|tides?|harbou?r|shore|beach|fire|fires|flames?|torch(?:es)?|candles?|bonfires?|burning"
    r"|smoke|smoking|steam|mist|misty|fog|foggy|haze|clouds?|cloudy|sky|skies|sunset|sunrise|storm"
    r"|trees?|forest|woods|jungle|foliage|leaves|grass\w*|palms?|reeds?|vegetation|plants?|crops?|fields?"
    r"|people|person|visitors?|tourists?|crowds?|pilgrims?|worshipp?ers?|dancers?|dancing|workers?|men|women|children"
    r"|cloth|fabric|flags?|banners?|curtains?|sails?|robes?|garments?|dust|dusty|sand|sandy|dunes?|desert"
    r"|birds?|animals?|horses?|camels?|cattle|boats?|ships?)\b", re.I)
_NONE_OF = re.compile(r"\b(?:no|without|empty of|devoid of|free of)\s+(?:\w+\s+){0,2}$", re.I)


def can_move(photo: Path) -> Optional[bool]:
    """True when the photo's record names something that can move, False when the record names nothing that can,
    None when there is no record (a photo you supplied: your choice, and the motion check still judges the clip)."""
    from .scene_planner import _photo_entry
    try:
        entry = _photo_entry(Path(photo))
    except (OSError, ValueError) as e:
        log.info("Motion: %s's record could not be read (%s).", Path(photo).name, e)
        return None
    if not entry:
        return None
    verdict = entry.get("vision_review") or {}
    if verdict.get("people_prominent"):
        return True
    # What the photo review saw in the pixels decides first (2026-10-29, image_review.PhotoVerdict.can_move): the
    # caption's words below are the fallback for a verdict made before the review said so.
    if isinstance(verdict.get("can_move"), bool):
        return verdict["can_move"]
    text = " ".join(str(v or "") for v in (verdict.get("reason"), entry.get("title"), entry.get("description")))
    for m in _CAN_MOVE.finditer(text):
        if not _NONE_OF.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False


def animatable(photo: Path) -> bool:
    """A photo AI motion may be made from: a Topic or Kept photo, or one you supplied (src/scene_planner.strong_shot)."""
    from .scene_planner import strong_shot
    try:
        return strong_shot(Path(photo))
    except (OSError, ValueError) as e:  # an unreadable record: not animated
        log.info("Motion: %s not animated (its record could not be read: %s).", Path(photo).name, e)
        return False


def _reuse(scenes: List[Path], memo: dict, ok: Optional[List[bool]] = None) -> Tuple[List[Path], List[str]]:
    """Clips in `memo` placed where their own photo still appears, on a beat `ok` allows. A clip whose photo is
    not animatable (a context shot: an earlier run's clip of it) is never placed. A clip for a later beat is
    placed only when the opening moves (opening_slots): with the opening still a still, none is."""
    from .typography import is_video
    original, scenes, placed = list(scenes), list(scenes), []
    for photo, (existing_clip, provider) in memo.items():
        if len(placed) >= config.MOTION_MAX_CLIPS:
            break
        if Path(existing_clip).is_file() and animatable(Path(photo)):
            for i in range(len(scenes)):
                if ok is not None and not ok[i]:
                    continue
                if str(scenes[i]) == photo or Path(scenes[i]).resolve() == Path(photo).resolve():
                    scenes[i] = existing_clip
                    placed.append((i, provider))
                    break
    if ok is not None and scenes and not is_video(Path(scenes[0])):
        return original, []
    return scenes, [prov for i, prov in sorted(placed)]


def _clip_records(work_dir: Path, keep: bool = True) -> List[Tuple[Path, Path, Path, str]]:
    """(photo, clip, anchor, provider) for the accepted clips in work_dir. Lineage is written only for a
    clip that passed its checks, and it must still verify: clip, anchor frame and photo unchanged. The clip
    must also pass today's checks (passes_gates: one accepted before the motion check is measured again);
    keep=False for another run's folder, which is never changed."""
    from .lineage import sidecar, source_chain
    found = []
    for record in sorted(Path(work_dir).glob("motion_*.mp4.lineage.json")):
        clip = Path(str(record)[: -len(".lineage.json")])
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            if data.get("operation") != "ai_motion":
                continue
            source_chain(clip)  # raises on a changed clip, anchor or photo
            anchor = Path(data["sources"][0]["path"])
            photo = anchor
            if sidecar(anchor).is_file():
                anchor_data = json.loads(sidecar(anchor).read_text(encoding="utf-8"))
                if anchor_data.get("operation") == "motion_anchor_frame":
                    photo = Path(anchor_data["sources"][0]["path"])
            if not passes_gates(clip, keep=keep):
                continue
            found.append((photo, clip, anchor, (data.get("settings") or {}).get("provider", "ai-motion")))
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            log.info("Motion: not reusing %s (%s).", clip.name, e)
    return found


def _clips_on_disk(work_dir: Path) -> dict:
    """{photo: (clip, provider)} for the accepted clips in work_dir."""
    return {str(photo): (clip, provider) for photo, clip, anchor, provider in _clip_records(work_dir)}


def _adopt_clips(work_dir: Path, seed_dir: Path) -> dict:
    """Another run's accepted clips, copied into work_dir with their anchor frames and fresh lineage (a
    copied sidecar would name the old path), so a regeneration keeps the clips the run it replaces paid
    for. {photo: (clip, provider)} for the copies."""
    import shutil
    found = {}
    if not seed_dir.is_dir() or seed_dir.resolve() == Path(work_dir).resolve():
        return found
    work_dir.mkdir(parents=True, exist_ok=True)
    for photo, clip, anchor, provider in _clip_records(seed_dir, keep=False):  # each passes today's checks first
        new_clip, new_anchor = Path(work_dir) / clip.name, Path(work_dir) / anchor.name
        if new_clip.exists():
            continue
        try:
            shutil.copy2(clip, new_clip)
            if clip.with_suffix(".qa.json").is_file():  # its measurements (passes_gates trusts them for these bytes)
                qa = json.loads(clip.with_suffix(".qa.json").read_text(encoding="utf-8"))
                if qa.get("gate") == GATE_VERSION:
                    new_clip.with_suffix(".qa.json").write_text(json.dumps(qa, indent=1), encoding="utf-8")
            if anchor.resolve() != photo.resolve():
                shutil.copy2(anchor, new_anchor)
            else:
                new_anchor = photo
            _record_lineage(photo, new_anchor, new_clip, provider)
            found[str(photo)] = (new_clip, provider)
            log.info("Motion: %s carried over from %s (already paid for).", clip.name, seed_dir.parent.name)
        except (OSError, ValueError) as e:
            log.info("Motion: could not carry %s over (%s).", clip.name, e)
    return found
