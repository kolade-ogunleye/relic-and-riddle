"""
src/privacy.py
==============
Automated human face de-identification & privacy compliance for documentary media.

Detects living human visitors/tourists/passersby in visual media before
final composition and applies a high-grade elliptical feathered Gaussian blur
matching BBC / National Geographic documentary broadcast standards.

Uses a 2-stage verification architecture:
1. Candidate Detection: OpenCV's deep-learning neural face detector (YuNet ONNX).
2. Cognitive Verification: Claude Vision classification to verify living flesh-and-blood
   humans vs ancient stone statues, relief carvings, deities, or historical artifacts.
"""

import concurrent.futures
import hashlib
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
from pydantic import BaseModel

from .config import BASE_DIR, FACE_PRIVACY_ENABLED, FACE_PRIVACY_SKIN_THRESHOLD
from .llm import claude_available, image_block, structured_call

log = logging.getLogger(__name__)

CACHE_DIR = BASE_DIR / "cache" / "privacy"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = BASE_DIR / "tools" / "yunet.onnx"

# One detector of each kind per thread: anonymize_all blurs images in parallel, and a shared OpenCV
# detector is not safe across threads (each call resets its input size, then detects).
_DETECTORS = threading.local()
# The detectors are shared objects whose input size is set per image: images checked in
# parallel take turns on them (detection is fast; the cloud calls are what run concurrently).
_DETECTOR_LOCK = threading.Lock()
MAX_PARALLEL_IMAGES = 3


def _post_vision(url: str, body: dict, timeout: float, key: str = ""):
    """POST with a short backoff on rate limits and transient errors, so a busy API is not
    mistaken for "no faces found"."""
    import requests
    for attempt in range(3):
        r = requests.post(url, json=body, timeout=timeout, headers={"x-goog-api-key": key})  # never in the URL
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
            return r
        retry_after = r.headers.get("Retry-After", "")
        time.sleep(min(30.0, float(retry_after)) if retry_after.isdigit() else 2.0 * 2 ** attempt)
    return r


class FaceVerification(BaseModel):
    is_living_human: bool
    explanation: Optional[str] = None


def _get_yunet(w: int, h: int) -> Optional[cv2.FaceDetectorYN]:
    if not MODEL_PATH.exists():
        return None
    try:
        detector = getattr(_DETECTORS, "yunet", None)
        if detector is None:
            detector = _DETECTORS.yunet = cv2.FaceDetectorYN.create(
                str(MODEL_PATH),
                "",
                (w, h),
                score_threshold=0.50,
                nms_threshold=0.3,
                top_k=50,
            )
        else:
            detector.setInputSize((w, h))
        return detector
    except Exception as e:
        log.warning("Could not initialize YuNet face detector (%s); falling back to cascade.", e)
        return None


def _get_cascade() -> cv2.CascadeClassifier:
    cascade = getattr(_DETECTORS, "cascade", None)
    if cascade is None:
        frontal_path = cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
        cascade = _DETECTORS.cascade = cv2.CascadeClassifier(frontal_path)
    return cascade


# The detector and blur that made a cached copy are part of its key: a copy keyed by path and mtime alone
# outlived every later fix (dev Roman photo 19 kept its grey ellipses, audit 2026-10-28).
ALGORITHM_VERSION = "v9_verified_soft"


def _cache_path(path: Path) -> Path:
    from .config import GEMINI_MODEL
    stat = path.stat()
    key = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{ALGORITHM_VERSION}|{GEMINI_MODEL}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{path.stem}_{digest}.jpg"


# Face geometry for a proposed box (audit 2026-10-28, dev Roman photo 19): Gemini returned head-and-body boxes
# (h/w 1.9-2.8, 300-370 px on a 4032 px photo) that became tall grey ellipses, read by the video judge as
# "fingers blocking the lens". A face is roughly as tall as it is wide; a tall box keeps its top (the head).
# Never dropped or narrowed for its size or width: a close-up face fills much of a photo, and a wide box may
# hold two faces side by side; both would have been published unblurred (review, 2026-10-28). The verifier
# decides whether a large box is a face.
FACE_MAX_ASPECT = 1.6     # h / w
HEAD_KEEP_ASPECT = 1.3    # a tall box keeps its top 1.3 * w


def _face_geometry(box: Tuple[int, int, int, int], W: int, H: int) -> Optional[Tuple[int, int, int, int]]:
    """The box with a head-and-body box cut to its head, or None when it is empty."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return None
    if h > FACE_MAX_ASPECT * w:
        h = int(round(HEAD_KEEP_ASPECT * w))
    return (x, y, w, h)


def _detect_faces_cognitive_gemini(img: np.ndarray) -> Optional[List[Tuple[int, int, int, int]]]:
    """Uses Gemini vision to detect living human faces/heads with 2D bounding boxes. None when the call
    failed (a timeout, an error reply): "no faces found" and "not looked at" are different answers."""
    from .config import GEMINI_API_KEY, GEMINI_MODEL, is_configured
    if not is_configured(GEMINI_API_KEY) or img is None or img.size == 0:
        return []

    H, W = img.shape[:2]
    # Keep high resolution (up to 3072) so tiny distant figures in crowds are clearly visible
    scale = min(1.0, 3072.0 / max(H, W))
    if scale < 1.0:
        disp = cv2.resize(img, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    else:
        disp = img

    try:
        import base64, json
        _, buf = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, 90])
        data = base64.b64encode(buf.tobytes()).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        prompt = (
            "Detect EVERY SINGLE living human head and face in this photograph with zero omissions. "
            "Include foreground visitors, distant background pedestrians, people in crowds, and partially occluded faces. "
            "If multiple people are standing next to or behind each other, return an individual box for each person. "
            "Do NOT detect ancient stone carvings, statues, animal sculptures, reliefs, or faces in paintings, "
            "manuscripts, mosaics, frescoes, drawings, engravings or other artwork: only real people photographed. "
            "Do NOT detect gilded or carved ornaments, cherubs, busts, masks, mannequins, or plain areas of mirror "
            "or glass. Box the face and head only, not the body. "
            "Return a JSON list of objects: [{\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": \"face\"}] "
            "where coordinates are normalized integers in [0, 1000]."
        )
        body = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": data}}
                ]
            }],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        r = _post_vision(url, body, timeout=25, key=GEMINI_API_KEY)
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            boxes = json.loads(text)
            out: List[Tuple[int, int, int, int]] = []
            for b in boxes:
                coords = b.get("box_2d", [])
                if len(coords) == 4:
                    ymin, xmin, ymax, xmax = coords
                    px_x = int(xmin * W / 1000)
                    px_y = int(ymin * H / 1000)
                    px_w = int((xmax - xmin) * W / 1000)
                    px_h = int((ymax - ymin) * H / 1000)
                    if px_w > 0 and px_h > 0:
                        out.append((max(0, px_x), max(0, px_y), min(W - max(0, px_x), px_w), min(H - max(0, px_y), px_h)))
            return out
        log.warning("Cognitive face detection failed (HTTP %s)", r.status_code)
    except Exception as e:
        log.warning("Cognitive face detection error (%s)", e)
    return None


def _is_living_human_candidate(img: np.ndarray, box: Tuple[int, int, int, int]) -> Optional[bool]:
    """Verifies whether candidate box is a real living human or an ancient statue. None when a vision model
    was asked and could not answer: the caller keeps the box (blurred) and does not cache the result, since
    the skin-colour test drops real faces on a padded crop (review, 2026-10-28). The skin-colour test decides
    only when no vision model is configured at all."""
    asked = False
    H, W = img.shape[:2]
    x, y, w, h = box
    cx, cy = x + w // 2, y + h // 2
    crop_w = min(max(int(w * 1.5), 140), w + 160)
    crop_h = min(max(int(h * 1.5), 140), h + 160)
    x1, y1 = max(0, cx - crop_w // 2), max(0, cy - crop_h // 2)
    x2, y2 = min(W, cx + crop_w // 2), min(H, cy + crop_h // 2)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return False

    # Stage 2A: Multimodal Gemini Vision Check (fastest & highly discriminative)
    from .config import GEMINI_API_KEY, GEMINI_MODEL, is_configured
    if is_configured(GEMINI_API_KEY):
        asked = True
        try:
            import base64, json
            _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            data = base64.b64encode(buf.tobytes()).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            prompt_text = (
                "Is there a real modern living human person in this crop whose face is visible? "
                "Distinguish living humans from ancient stone/rock carvings, statues, reliefs, and from painted, "
                "drawn or illuminated figures (manuscripts, paintings, mosaics, frescoes, engravings), and from "
                "gilded or carved ornaments, cherubs, busts, masks, mannequins and plain mirror or glass. "
                "Answer in JSON: {\"is_living_human\": bool, \"explanation\": str}"
            )
            body = {
                "contents": [{
                    "parts": [
                        {"text": prompt_text},
                        {"inline_data": {"mime_type": "image/jpeg", "data": data}}
                    ]
                }],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            r = _post_vision(url, body, timeout=15, key=GEMINI_API_KEY)
            if r.status_code == 200:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                verdict = json.loads(text)
                return bool(verdict.get("is_living_human", False))
        except Exception as e:
            log.warning("Gemini vision verification error (%s); trying fallback.", e)

    # Stage 2B: Claude Vision Fallback
    if claude_available():
        asked = True
        tmp_path = CACHE_DIR / f"verify_tmp_{x}_{y}_{uuid.uuid4().hex[:8]}.jpg"  # unique per concurrent check
        cv2.imwrite(str(tmp_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        system_prompt = (
            "You are an automated visual privacy compliance analyzer. Distinguish real modern living humans "
            "(tourists, visitors, bystanders) from ancient carved stone statues, relief sculptures, historical artifacts, "
            "and figures in artwork (paintings, manuscripts, mosaics, frescoes, drawings, engravings)."
        )
        content = [
            image_block(tmp_path, 384),
            {
                "type": "text",
                "text": (
                    "Is this cropped image showing a real living modern human person (set is_living_human=True), "
                    "or a sculpture, relief, or a figure in a painting, manuscript or other artwork (set is_living_human=False)?"
                ),
            },
        ]
        try:
            res = structured_call(system_prompt, content, FaceVerification, effort="low")
            return bool(res.is_living_human)
        except Exception as e:
            log.warning("Claude vision face verification error (%s); falling back to chromaticity.", e)
        finally:
            tmp_path.unlink(missing_ok=True)

    if asked:
        return None  # asked and unanswered: kept as a face (fail closed), never decided by skin colour
    # Stage 2C: Physical skin chromaticity fallback (no vision model configured)
    ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
    cr, cb = ycrcb[:, :, 1], ycrcb[:, :, 2]
    skin_mask = (cr >= 135) & (cr <= 180) & (cb >= 85) & (cb <= 135)
    return float(skin_mask.sum()) / float(crop.shape[0] * crop.shape[1]) >= 0.40


def detect_human_faces(
    img: np.ndarray,
    threshold: float = FACE_PRIVACY_SKIN_THRESHOLD,
    status: Optional[dict] = None,
) -> List[Tuple[int, int, int, int]]:
    """
    Detects bounding boxes of living human faces in an image.
    Uses multi-stage detection:
    1. Cognitive Vision detection (catches distant pedestrians, turned profiles, crowd members).
    2. YuNet neural face detection (local facial landmarks).
    3. Every candidate, from either, narrowed to face proportions and verified against sculptures,
       carvings, ornaments and artwork.
    `status`, when given, gets "complete": False if a detector failed (the answer is then not cached).
    """
    status = {} if status is None else status
    status["complete"] = True
    if img is None or img.size == 0:
        return []

    H, W = img.shape[:2]

    # Stage 1: Cognitive Vision Detection
    cognitive_boxes = _detect_faces_cognitive_gemini(img)
    if cognitive_boxes is None:
        cognitive_boxes, status["complete"] = [], False

    # Stage 2: YuNet Neural Face Detection
    yunet_candidates: List[Tuple[int, int, int, int]] = []
    with _DETECTOR_LOCK:
        yunet = _get_yunet(W, H)

        if yunet is not None:
            try:
                _, faces = yunet.detect(img)
                if faces is not None and len(faces) > 0:
                    for f in faces:
                        x, y, w, h = map(int, f[:4])
                        if w >= 8 and h >= 8:
                            yunet_candidates.append((max(0, x), max(0, y), min(W - max(0, x), w), min(H - max(0, y), h)))
            except Exception as e:
                log.warning("YuNet detection error (%s); falling back to cascade.", e)
                status["complete"] = False

        if not yunet_candidates and not cognitive_boxes:
            cascade = _get_cascade()
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(16, 16))
            for (x, y, w, h) in boxes:
                yunet_candidates.append((int(x), int(y), int(w), int(h)))

    # Stage 3: Merge and Verify. A model-proposed box is verified exactly as a detector box is: accepted
    # unverified, a Gemini box on a plain mirror beside a gilded column became a flat grey oval that the
    # release gate blocked, and the regeneration reused the same photo (production Amber Room beat 7,
    # 2026-10-26). Boxes are first narrowed to face proportions (dev Roman photo 19, audit 2026-10-28).
    candidates: List[Tuple[int, int, int, int]] = []
    for b in [*cognitive_boxes, *yunet_candidates]:
        b = _face_geometry(b, W, H)
        if b is None:
            continue
        bx, by, bw, bh = b
        bcx, bcy = bx + bw // 2, by + bh // 2
        covered = any(
            (gx <= bcx <= gx + gw and gy <= bcy <= gy + gh) or
            (bx <= gx + gw // 2 <= bx + bw and by <= gy + gh // 2 <= by + bh)
            for (gx, gy, gw, gh) in candidates
        )
        if not covered:  # a detector box on a face the model already proposed is one candidate, checked once
            candidates.append(b)
    verified_faces = []
    for b in candidates:
        verdict = _is_living_human_candidate(img, b)
        if verdict is None:  # the check could not run: blurred anyway, and the result is not cached
            status["complete"] = False
        if verdict is not False:
            verified_faces.append(b)
    rejected = len(candidates) - len(verified_faces)
    if rejected:
        log.info("Face privacy: %d of %d candidate box(es) were not a living person's face.", rejected, len(candidates))

    # Stage 4: Deduplicate overlapping boxes
    deduped: List[Tuple[int, int, int, int]] = []
    for b in verified_faces:
        bx, by, bw, bh = b
        bcx, bcy = bx + bw // 2, by + bh // 2
        dup = False
        for db in deduped:
            dx, dy, dw, dh = db
            if dx <= bcx <= dx + dw and dy <= bcy <= dy + dh and abs(bw - dw) < 40 and abs(bh - dh) < 40:
                dup = True
                break
        if not dup:
            deduped.append(b)

    return deduped


# Soft-focus de-identification (audit 2026-10-28). The old blur used a Gaussian kernel as large as the box with
# sigma = kernel / 2, so the patch averaged to one flat colour, and a 15 px feather cap gave it a hard rim: a
# grey disc on the Amber Room's gilded wall (production beat 7) and grey ellipses on Roman photo 19. Now the face
# is resampled to about FACE_CELLS samples across (no eye, nose or mouth survives), the colour gradients of the
# face and its surroundings are kept, the photo's own grain is laid back over it, and the mask is feathered over
# a quarter of the box: it reads as a patch out of focus, not a cut-out.
FACE_CELLS = 6           # samples across the face's shorter side after the blur
FEATHER_FRACTION = 0.25  # of the padded box's shorter side


def _grain(region: np.ndarray, mask: np.ndarray, seed: int) -> np.ndarray:
    """Noise as strong as the fine grain of the photo around the face (none of the face's own detail)."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32)
    high = (gray - cv2.GaussianBlur(gray, (0, 0), 1.0))[mask < 0.05]
    sigma = float(np.median(np.abs(high - np.median(high)))) * 1.4826 if high.size > 50 else 2.0
    sigma = min(6.0, max(1.5, sigma))
    return np.random.default_rng(seed).normal(0.0, sigma, region.shape[:2]).astype(np.float32)


def blur_faces_on_image(
    img: np.ndarray,
    faces: List[Tuple[int, int, int, int]],
    pad_ratio: float = 0.30,
) -> np.ndarray:
    """
    De-identifies the given face boxes with a feathered elliptical soft focus: the face is resampled to a few
    samples across (its detail destroyed), its colour and the photo's grain are kept, and the edge is feathered
    over a quarter of the box. No mosaic, no flat disc.
    """
    if not faces:
        return img

    out = img.copy()
    H, W = out.shape[:2]

    for (x, y, w, h) in faces:
        # For small distant faces, expand padding so hair/shawl/cap is completely covered. Larger faces get 30%
        # (was 20%): a YuNet box stops at the mouth, and a soft edge left a white beard showing below it
        # (dev Roman photo 19 evidence render, 2026-10-28).
        pad_x = max(10, int(w * 0.35)) if min(w, h) < 60 else int(w * pad_ratio)
        pad_y = max(12, int(h * 0.40)) if min(w, h) < 60 else int(h * pad_ratio)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(W, x + w + pad_x)
        y2 = min(H, y + h + pad_y)

        fw = x2 - x1
        fh = y2 - y1
        if fw <= 8 or fh <= 8:
            continue

        # The box plus a margin: the feather fades into it, and the soft focus takes its colours from it too.
        m = max(4, int(min(fw, fh) * 0.5))
        X1, Y1, X2, Y2 = max(0, x1 - m), max(0, y1 - m), min(W, x2 + m), min(H, y2 + m)
        region = out[Y1:Y2, X1:X2]
        rh, rw = region.shape[:2]

        # Strength scales with the face: one sample per (shorter face side / FACE_CELLS) pixels.
        cell = max(2.0, min(w, h) / FACE_CELLS)
        small = cv2.resize(region, (max(1, round(rw / cell)), max(1, round(rh / cell))), interpolation=cv2.INTER_AREA)
        soft = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        soft = cv2.GaussianBlur(soft, (0, 0), cell * 0.6)  # no cell edges

        feather = max(2.0, FEATHER_FRACTION * min(fw, fh))
        cx, cy = (x1 + x2) // 2 - X1, (y1 + y2) // 2 - Y1
        mask = np.zeros((rh, rw), dtype=np.float32)
        cv2.ellipse(mask, (cx, cy), (max(1, fw // 2), max(1, fh // 2)), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), feather / 2.0)
        # The face box itself is always fully covered, whatever the feather.
        core = np.zeros_like(mask)
        cv2.ellipse(core, (x - X1 + w // 2, y - Y1 + h // 2), (max(1, int(w * 0.65)), max(1, int(h * 0.7))),
                    0, 0, 360, 1.0, -1)
        mask = np.maximum(mask, cv2.GaussianBlur(core, (0, 0), max(1.0, feather / 6.0)))

        soft += _grain(region, mask, seed=x * 7919 + y)[:, :, None]
        blended = soft * mask[:, :, None] + region.astype(np.float32) * (1.0 - mask[:, :, None])
        out[Y1:Y2, X1:X2] = np.clip(blended, 0, 255).astype(np.uint8)

    return out


ARTWORK_WORDS = ("manuscript", "illuminat", "painting", "painted", "fresco", "mosaic", "engraving", "etching",
                 "woodcut", "lithograph", "drawing", "illustration", "miniature", "tapestry", "icon", "codex",
                 "skylitzes", "sketch", "watercolour", "watercolor", "oil on", "print by")


def is_artwork(image_path: Path) -> bool:
    """True when the image's own source record says it is artwork, or dates it before photography
    could show living people (before 1880). Its figures are not real people to protect, and blurring
    them damages the historical image (12th-century Madrid Skylitzes faces were blurred)."""
    import json
    image_path = Path(image_path)
    from .lineage import sidecar
    for _ in range(4):  # a derived copy (upscaled, cropped) is judged by the original it was made from
        try:
            sources = json.loads(sidecar(image_path).read_text(encoding="utf-8")).get("sources") or []
        except (OSError, ValueError, AttributeError):
            break
        if len(sources) != 1:
            break
        image_path = Path(sources[0]["path"])
    for credits in (image_path.parent / "credits.json", image_path.parent.parent / "credits.json"):
        try:
            records = json.loads(credits.read_text(encoding="utf-8")).get("images", [])
        except (OSError, ValueError, AttributeError):
            continue
        for record in records:
            if record.get("file") != image_path.name:
                continue
            year = record.get("year")
            if isinstance(year, int) and year < 1880:
                return True
            text = " ".join(str(record.get(k) or "") for k in ("title", "description")).lower()
            text += " " + " ".join(record.get("categories") or []).lower()
            return any(word in text for word in ARTWORK_WORDS)
    return False


def blur_human_faces(
    image_path: Path,
    output_path: Optional[Path] = None,
    threshold: float = FACE_PRIVACY_SKIN_THRESHOLD,
) -> Path:
    """
    Inspects image at `image_path`. If living human faces are detected,
    creates an anonymized version with blurred faces.
    Returns path to anonymized image (cached or saved to output_path).
    If no human faces are present or feature is disabled, returns original path.
    """
    image_path = Path(image_path)
    if not FACE_PRIVACY_ENABLED or not image_path.exists():
        return image_path
    if is_artwork(image_path):
        log.info("Face privacy skipped for %s: its source record marks it as artwork.", image_path.name)
        return image_path

    # Check cache if output_path not explicitly specified
    cache_file = _cache_path(image_path) if output_path is None else Path(output_path)
    if cache_file.exists():
        return cache_file

    img = cv2.imread(str(image_path))
    if img is None:
        log.warning("Could not read image %s for face privacy check", image_path)
        return image_path

    status: dict = {}
    faces = detect_human_faces(img, threshold=threshold, status=status)
    if not faces:
        return image_path

    log.info("Detected and blurred %d human face(s) in %s", len(faces), image_path.name)
    blurred = blur_faces_on_image(img, faces)

    if not status["complete"] and output_path is None:
        # A detector failed (a Gemini timeout): this copy serves this run only, and the photo is looked at
        # again next time. Cached, a timeout's detector-only answer was reused for good (audit 2026-10-28).
        cache_file = cache_file.with_name(f"{cache_file.stem}_partial.jpg")  # never read back as a cache
        log.info("Face privacy for %s ran without every detector; its copy is not cached.", image_path.name)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cache_file), blurred, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return cache_file


def anonymize_media(
    media_path: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Universal media privacy filter. For images, detects and blurs human faces.
    Non-image media or images without human faces are returned untouched.
    """
    media_path = Path(media_path)
    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    if media_path.suffix.lower() in image_extensions:
        result = blur_human_faces(media_path, output_path=output_path)
        if Path(result).resolve() != media_path.resolve():
            from .lineage import record_derived
            record_derived(result, [media_path], "face_blur")
        return result
    return media_path


def anonymize_all(paths: List[Path]) -> List[Path]:
    """Applies human face de-identification across a list of media paths, in order.
    Each distinct file is checked once (a repeated path gets the same verdict), and
    separate files are checked in parallel; every file gets exactly the check it would alone."""
    paths = [Path(p) for p in paths]
    unique = list(dict.fromkeys(paths))
    if len(unique) <= 1:
        done = {p: anonymize_media(p) for p in unique}
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_IMAGES, len(unique))) as pool:
            done = dict(zip(unique, pool.map(anonymize_media, unique)))
    return [done[p] for p in paths]
