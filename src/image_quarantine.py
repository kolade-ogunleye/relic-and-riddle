"""
src/image_quarantine.py
=======================
Pre-render quarantine for generated illustrations (the illustration tier, src/image_gen.py).

Every check FAILS CLOSED (2026-11-02, QA lead): a checker that is unavailable, raises, or cannot read the image
rejects the candidate. A generated picture that was never looked at is never shown.

Two entry points:
- verify_gap_asset(path, line, era): the original three-stage gate (dimensions, local OCR, vision), kept for
  callers of the legacy path.
- score_candidate(raw, final, spec, ...): the illustration rubric. ALL must pass, else the candidate is rejected:
    1. ink coverage (bright line pixels of the raw dark-ground image) within INK_COVERAGE;
    2. a dark ground before any overlay: at least DARK_SHARE of the pixels L* < DARK_L, and low chroma;
    3. resolution: the fitted frame's short side >= MIN_SHORT_SIDE, no upscale over MAX_UPSCALE, a sharpness floor;
    4. no text: local OCR finds no real-word token (OCR missing: rejected unless config.IMAGE_GEN_REQUIRE_OCR is
       off, then the vision check's text answer alone decides) and the vision check sees none;
    5. the vision check (one structured call): every required descriptor shown, no anachronism, no face of an
       identifiable person, the subject inside the safe zone, and it could NOT be mistaken for a photograph;
    6. a perceptual hash at least PHASH_MIN_DISTANCE bits from every other visual of the run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel

from . import config

log = logging.getLogger(__name__)

PASS = "pass"

INK_COVERAGE = (0.08, 0.45)   # share of bright (line) pixels in the raw dark-ground image
LINE_L = 50.0                 # L* above which a pixel is line
DARK_L = 20.0                 # L* below which a pixel is ground
DARK_SHARE = 0.50             # at least this share of the pixels is dark ground
MAX_CHROMA = 12.0             # mean CIELAB chroma: an engraving is neutral
MIN_SHORT_SIDE = 1080
MAX_UPSCALE = 1.5
PHASH_MIN_DISTANCE = 12
_WORD = re.compile(r"[A-Za-z]{3,}")


# ------------------------------------------------------------------ the legacy three-stage gate
def check_dimensions_and_quality(image_path: Path, aspect: str = "9:16") -> tuple[bool, str]:
    """Ensures the asset meets minimum broadcast resolution and the frame's orientation."""
    try:
        with Image.open(image_path) as im:
            w, h = im.size
            portrait = aspect != "16:9"
            short, long_ = (w, h) if portrait else (h, w)
            if short < 540 or long_ < 960:
                return False, f"Resolution too low ({w}x{h}; minimum 540x960)"
            ratio = (h / max(1, w)) if portrait else (w / max(1, h))
            if ratio < 1.3:
                return False, (f"Aspect ratio too wide for 9:16 vertical broadcast ({ratio:.2f})" if portrait
                               else f"Aspect ratio too tall for 16:9 broadcast ({ratio:.2f})")
        return True, PASS
    except (OSError, ValueError) as e:
        return False, f"Corrupted image file: {e}"


def _ocr_words(image: Image.Image) -> list[str] | None:
    """Real-word tokens local OCR reads in the image; None when OCR is not available or fails."""
    try:
        import pytesseract  # optional: not a dependency of the project
    except ImportError:
        return None
    try:
        text = pytesseract.image_to_string(image)
    except Exception as e:  # noqa: BLE001 - any OCR failure is "not available" (fail closed below)
        log.info("Local OCR could not run (%s).", e)
        return None
    return _WORD.findall(text or "")


def check_ocr_text_quick(image_path: Path) -> tuple[bool, str]:
    """Local OCR text screen. FAILS CLOSED: OCR missing or failing rejects (config.IMAGE_GEN_REQUIRE_OCR)."""
    try:
        with Image.open(image_path) as im:
            words = _ocr_words(im.convert("L"))
    except (OSError, ValueError) as e:
        return False, f"Unreadable image for the text screen: {e}"
    if words is None:
        if getattr(config, "IMAGE_GEN_REQUIRE_OCR", True):
            return False, "Local OCR (pytesseract/tesseract) is not available: the text screen cannot run"
        return True, "ocr skipped (IMAGE_GEN_REQUIRE_OCR=false): the vision check reads text"
    if words:
        return False, f"Detected printed text or signature in illustration: '{' '.join(words[:5])}'"
    return True, PASS


class QuarantineVerdict(BaseModel):
    """The vision gate's answer. The prompt names these exact fields (it said 'pass' while the schema said 'passed')."""
    passed: bool
    reason: str = ""


def _vision_ready() -> bool:
    from . import free_llm
    try:
        return bool(free_llm.ready())
    except Exception:  # noqa: BLE001 - an unknown state is "not ready" (fail closed)
        return False


def check_historical_anachronism(image_path: Path, line_text: str, era: str) -> tuple[bool, str]:
    """Vision check for anachronism, text and cartoon styling. FAILS CLOSED when unavailable or failing."""
    from . import free_llm
    if not _vision_ready():
        return False, "The vision check is not available: a generated image is never shown unchecked"
    system = ("You verify a generated historical illustration for a short documentary before it is shown. "
              "Return a JSON object with the fields 'passed' (boolean) and 'reason' (string).")
    prompt = (f"Narration sentence: '{line_text}'\nHistorical era: '{era}'\n\n"
              "Inspect the image for:\n"
              "1. Disqualifying anachronisms (modern technology, modern ships or clothing in an earlier era).\n"
              "2. Any visible letters, words, numbers, fake signatures, watermarks or labels.\n"
              "3. Cartoon, anime or video-game styling.\n"
              "4. Anything that could be mistaken for a real photograph.\n"
              "If any of these is present set passed=false with a short reason; otherwise passed=true.")
    try:
        verdict = free_llm.structured(system, prompt, QuarantineVerdict, images=[image_path], role="checker",
                                      task_class="light")
    except Exception as e:  # noqa: BLE001 - fail closed on any checker failure
        log.info("Quarantine vision check failed (%s); rejected.", e)
        return False, f"The vision check failed: {e}"
    if not isinstance(verdict, QuarantineVerdict) or not verdict.passed:
        return False, (getattr(verdict, "reason", "") or "Failed the vision check")
    return True, PASS


def verify_gap_asset(image_path: Path, line_text: str, era: str, aspect: str = "9:16") -> tuple[bool, str]:
    """The three-stage pre-render gate (dimensions, local OCR, vision); every stage fails closed."""
    for check in (lambda: check_dimensions_and_quality(image_path, aspect), lambda: check_ocr_text_quick(image_path),
                  lambda: check_historical_anachronism(image_path, line_text, era)):
        ok, reason = check()
        if not ok:
            log.warning("Gap asset %s quarantined: %s", Path(image_path).name, reason)
            return False, reason
    log.info("Gap asset %s PASSED quarantine gate.", Path(image_path).name)
    return True, PASS


# ------------------------------------------------------------------ the illustration rubric (2026-11-02)
def _lab(image: Image.Image, max_side: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """(L*, chroma) arrays of an RGB image (sRGB, D65), on a copy at most max_side wide."""
    im = image.convert("RGB")
    if max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side))
    c = np.asarray(im, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: np.ndarray) -> np.ndarray:
        return np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16.0 / 116.0)
    fx, fy, fz = f(x), f(y), f(z)
    lightness = 116.0 * fy - 16.0
    chroma = np.hypot(500.0 * (fx - fy), 200.0 * (fy - fz))
    return lightness, chroma


def tone_stats(image: Image.Image) -> dict[str, float]:
    """{"ink": share of line pixels, "dark": share of ground pixels, "chroma": mean chroma}."""
    lightness, chroma = _lab(image)
    return {"ink": float((lightness > LINE_L).mean()), "dark": float((lightness < DARK_L).mean()),
            "chroma": float(chroma.mean())}


def sharpness(image: Image.Image) -> float:
    """Variance of the Laplacian of the grayscale image (higher: crisper lines)."""
    a = np.asarray(image.convert("L"), dtype=np.float64)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    return float(lap.var())


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n)) * np.sqrt(2.0 / n)
    m[0, :] = np.sqrt(1.0 / n)
    return m


_DCT32 = _dct_matrix(32)


def phash(image: Image.Image) -> int:
    """64-bit perceptual hash (DCT of a 32x32 grayscale copy, the 8x8 low frequencies against their median)."""
    a = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT32 @ a @ _DCT32.T)[:8, :8].flatten()
    med = np.median(low[1:])
    bits = 0
    for v in low:
        bits = (bits << 1) | int(v > med)
    return bits


def phash_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class DescriptorCheck(BaseModel):
    descriptor: str
    shown: bool


class IllustrationVerdict(BaseModel):
    """The rubric's vision answer: every field must come back in the passing direction."""
    descriptors: list[DescriptorCheck] = []
    has_text: bool = True
    anachronism: bool = True
    identifiable_face: bool = True
    subject_in_safe_zone: bool = False
    could_be_mistaken_for_photograph: bool = True
    reason: str = ""


def vision_rubric(image_path: Path, spec: dict[str, Any]) -> tuple[bool, str]:
    """One structured vision call over the composited frame. FAILS CLOSED when unavailable or failing."""
    from . import free_llm
    if not _vision_ready():
        return False, "the vision check is not available"
    required = [str(d) for d in spec.get("required") or []]
    system = ("You check a generated illustration for a history documentary before it is shown. Be strict: it is "
              "shown only when every answer is in the passing direction. Return JSON with exactly the fields "
              "'descriptors' (list of {'descriptor', 'shown'}), 'has_text', 'anachronism', 'identifiable_face', "
              "'subject_in_safe_zone', 'could_be_mistaken_for_photograph' and 'reason'.")
    prompt = (f"It should depict: {spec.get('subject', '')}\nEra: {spec.get('era_label') or spec.get('era', '')}\n"
              "For each required descriptor say whether the image clearly shows it: "
              + "; ".join(required) + "\n"
              "has_text: any letters, words, numbers, signatures, labels or watermarks are visible.\n"
              "anachronism: anything that does not belong to the era is shown.\n"
              "identifiable_face: a face of a real, identifiable person is shown.\n"
              "subject_in_safe_zone: the subject sits in the middle third, clear of the frame edges and the lower fifth.\n"
              "could_be_mistaken_for_photograph: a viewer could take it for a real photograph.")
    try:
        v = free_llm.structured(system, prompt, IllustrationVerdict, images=[image_path], role="checker",
                                task_class="light")
    except Exception as e:  # noqa: BLE001 - fail closed on any checker failure
        return False, f"the vision check failed: {e}"
    if not isinstance(v, IllustrationVerdict):
        return False, "the vision check returned no verdict"
    shown = {d.descriptor.strip().lower(): d.shown for d in v.descriptors}
    missing = [d for d in required if not shown.get(d.strip().lower(), False)]
    problems = ([f"not shown: {', '.join(missing)}"] if missing else []) + [
        msg for bad, msg in ((v.has_text, "text in the image"), (v.anachronism, "an anachronism"),
                             (v.identifiable_face, "an identifiable face"),
                             (not v.subject_in_safe_zone, "the subject outside the safe zone"),
                             (v.could_be_mistaken_for_photograph, "it could be mistaken for a photograph")) if bad]
    if problems:
        return False, "; ".join(problems) + (f" ({v.reason})" if v.reason else "")
    return True, PASS


def raw_rubric(raw: Image.Image, target_size: tuple[int, int]) -> tuple[bool, list[str]]:
    """The checks on the raw generated image, before any overlay: tone, coverage, resolution and sharpness."""
    reasons: list[str] = []
    stats = tone_stats(raw)
    if not INK_COVERAGE[0] <= stats["ink"] <= INK_COVERAGE[1]:
        reasons.append(f"ink coverage {stats['ink']:.0%} outside {INK_COVERAGE[0]:.0%}-{INK_COVERAGE[1]:.0%}")
    if stats["dark"] < DARK_SHARE:
        reasons.append(f"only {stats['dark']:.0%} dark ground (needs {DARK_SHARE:.0%} at L*<{DARK_L:.0f})")
    if stats["chroma"] > MAX_CHROMA:
        reasons.append(f"coloured (mean chroma {stats['chroma']:.1f})")
    from .substrate_compositor import fit_crop, upscale_factor
    if min(target_size) < MIN_SHORT_SIDE:
        reasons.append(f"the frame's short side is under {MIN_SHORT_SIDE}px")
    scale = upscale_factor(raw.size, target_size)
    if scale > MAX_UPSCALE:
        reasons.append(f"needs a {scale:.2f}x upscale (at most {MAX_UPSCALE}x)")
    else:
        sharp = sharpness(fit_crop(raw.convert("L"), target_size))
        floor = float(getattr(config, "IMAGE_GEN_MIN_SHARPNESS", 40.0))
        if sharp < floor:
            reasons.append(f"too soft (sharpness {sharp:.0f} < {floor:.0f})")
    return not reasons, reasons


def score_candidate(raw_path: Path, final_path: Path, spec: dict[str, Any], target_size: tuple[int, int],
                    others: Sequence[Path] = ()) -> tuple[bool, list[str], float]:
    """The whole rubric for one candidate: (passed, reasons it failed, score). Fails closed on any error."""
    try:
        with Image.open(raw_path) as im:
            raw = im.convert("RGB")
        with Image.open(final_path) as im:
            final = im.convert("RGB")
    except (OSError, ValueError) as e:
        return False, [f"unreadable candidate: {e}"], 0.0
    ok, reasons = raw_rubric(raw, target_size)
    if not ok:
        return False, reasons, 0.0
    ok, why = check_ocr_text_quick(Path(raw_path))
    if not ok:
        return False, [why], 0.0
    mine = phash(final)
    for other in others:
        try:
            with Image.open(other) as im:
                d = phash_distance(mine, phash(im))
        except (OSError, ValueError):
            continue
        if d < PHASH_MIN_DISTANCE:
            return False, [f"too like {Path(other).name} (pHash distance {d} < {PHASH_MIN_DISTANCE})"], 0.0
    ok, why = vision_rubric(Path(final_path), spec)
    if not ok:
        return False, [why], 0.0
    stats = tone_stats(raw)
    # Among passing candidates: crisper line work and coverage nearest the middle of the band are preferred.
    mid = sum(INK_COVERAGE) / 2
    score = sharpness(raw.convert("L")) / (1.0 + 10.0 * abs(stats["ink"] - mid))
    return True, [], float(score)
