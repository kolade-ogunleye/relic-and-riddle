"""
Face privacy, 2026-10-28: model-proposed boxes are verified and given face proportions, the blur reads as soft
focus (no flat grey disc) while still destroying a face's detail, and a detector failure is never cached.
Evidence: docs/reviews/privacy-blur-2026-10-28/ (production Amber Room beat 7, dev Roman photo 19).
"""

import cv2
import numpy as np
import pytest

from src import privacy
from src.config import BASE_DIR


def _feature_energy(gray):
    """Energy at the scale of eyes, nose and mouth (band-pass between sigma 2 and 8)."""
    g = gray.astype(np.float32)
    return float((cv2.GaussianBlur(g, (0, 0), 2) - cv2.GaussianBlur(g, (0, 0), 8)).var())


def _synthetic_face(seed=3):
    rng = np.random.default_rng(seed)
    img = np.full((600, 600, 3), (60, 90, 120), np.uint8)
    img = cv2.add(img, rng.integers(0, 25, img.shape, dtype=np.uint8))
    cv2.ellipse(img, (300, 300), (110, 145), 0, 0, 360, (140, 165, 215), -1)
    for ex in (255, 345):
        cv2.ellipse(img, (ex, 270), (24, 12), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (ex, 270), 9, (40, 30, 20), -1)
    cv2.line(img, (300, 280), (290, 330), (90, 110, 160), 5)
    cv2.ellipse(img, (300, 370), (40, 14), 0, 0, 180, (60, 60, 150), 6)
    return img, (190, 155, 220, 290)


def test_blur_destroys_a_faces_detail():
    img, (x, y, w, h) = _synthetic_face()
    out = privacy.blur_faces_on_image(img, [(x, y, w, h)])
    before = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    after = cv2.cvtColor(out[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    assert _feature_energy(after) < _feature_energy(before) / 50  # measured 238 -> 1.6
    # the eyes' white-on-dark contrast is gone
    assert int(after.max()) - int(after.min()) < (int(before.max()) - int(before.min())) / 2


def test_blur_is_not_a_flat_disc():
    # Production Amber Room beat 7 (2026-10-26): the old box-sized Gaussian left one flat grey colour.
    rng = np.random.default_rng(1)
    base = cv2.resize(rng.integers(40, 220, (12, 12, 3), dtype=np.uint8), (400, 400), interpolation=cv2.INTER_CUBIC)
    img = cv2.add(base, rng.integers(0, 12, base.shape, dtype=np.uint8))
    box = (150, 140, 100, 120)
    out = privacy.blur_faces_on_image(img, [box])
    x, y, w, h = box
    inner = cv2.cvtColor(out[y + h // 4:y + 3 * h // 4, x + w // 4:x + 3 * w // 4], cv2.COLOR_BGR2GRAY).astype(np.float32)
    assert inner.std() > 3
    texture = inner - cv2.GaussianBlur(inner, (0, 0), 3)
    assert texture.std() > 1.0  # the photo's grain, laid back over the soft focus
    # the old blur, for comparison, flattened this patch
    old_k = (min(w, h) // 2) * 2 + 1
    flat = cv2.GaussianBlur(img[y:y + h, x:x + w], (old_k, old_k), old_k / 2.0)
    flat_tex = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY).astype(np.float32)
    assert (flat_tex - cv2.GaussianBlur(flat_tex, (0, 0), 3)).std() < texture.std()


def test_feather_is_a_quarter_of_the_box():
    img = np.full((400, 400, 3), 128, np.uint8)
    img[::2, ::2] = 200  # fine pattern, so the blurred area shows in the difference
    out = privacy.blur_faces_on_image(img, [(150, 150, 100, 100)])
    row = np.abs(out[200].astype(int) - img[200].astype(int)).max(1)
    changed = np.nonzero(row > 4)[0]
    full = np.nonzero(row > 0.9 * row.max())[0]
    ramp = (full.min() - changed.min())
    assert ramp >= 15  # the old feather was capped at 15 px whatever the box


@pytest.mark.parametrize("box, expect", [
    ((100, 100, 100, 270), (100, 100, 100, 130)),   # head-and-body: the top 1.3 * w is kept
    ((100, 100, 100, 120), (100, 100, 100, 120)),   # a face: unchanged
    ((100, 100, 300, 100), (100, 100, 300, 100)),   # a wide box may hold two faces: kept whole
    ((0, 0, 1000, 800), (0, 0, 1000, 800)),         # a close-up face: kept, the verifier decides
    ((10, 10, 0, 50), None),                         # empty
])
def test_face_geometry(box, expect):
    assert privacy._face_geometry(box, 2000, 2000) == expect


def test_model_boxes_are_verified_like_detector_boxes(monkeypatch):
    # Production Amber Room beat 7: a Gemini box on a plain mirror was blurred unverified.
    img = np.full((800, 800, 3), 120, np.uint8)
    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: [(300, 300, 80, 100)])
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)
    asked = []
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: asked.append(b) or False)
    assert privacy.detect_human_faces(img) == []
    assert asked == [(300, 300, 80, 100)]
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: True)
    assert privacy.detect_human_faces(img) == [(300, 300, 80, 100)]


def test_a_tall_model_box_is_blurred_as_a_head(monkeypatch):
    # Dev Roman photo 19: head-and-body boxes became tall ellipses ("fingers blocking the lens").
    img = np.full((2000, 2000, 3), 120, np.uint8)
    img[::2, ::2] = 180
    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: [(900, 900, 120, 330)])
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: True)
    faces = privacy.detect_human_faces(img)
    out = privacy.blur_faces_on_image(img, faces)
    changed = np.abs(out.astype(int) - img.astype(int)).max(2) > 8
    ys, xs = np.nonzero(changed)
    assert (ys.max() - ys.min()) / (xs.max() - xs.min()) <= 1.6


class _OneFace:
    def detect(self, img):
        return None, np.array([[100, 100, 90, 110] + [0] * 10 + [0.95]], dtype=np.float32)


def test_a_detector_failure_is_not_cached(monkeypatch, tmp_path):
    # Audit 2026-10-28: a Gemini timeout cached a YuNet-only answer for good.
    monkeypatch.setattr(privacy, "FACE_PRIVACY_ENABLED", True)
    monkeypatch.setattr(privacy, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _OneFace())
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: True)
    photo = tmp_path / "photo.jpg"
    cv2.imwrite(str(photo), np.full((400, 400, 3), 120, np.uint8))
    cache_file = privacy._cache_path(photo)

    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: None)  # timed out
    out = privacy.blur_human_faces(photo)
    assert out != photo and out.exists() and not cache_file.exists()  # blurred for this run, not cached

    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: [])  # answered: no faces
    out = privacy.blur_human_faces(photo)
    assert out == cache_file and cache_file.exists()


def test_gemini_failure_is_told_from_no_faces(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "AIza-test")
    img = np.zeros((64, 64, 3), np.uint8)
    monkeypatch.setattr(privacy, "_post_vision", lambda *a, **k: SimpleNamespace(status_code=503, headers={}))
    assert privacy._detect_faces_cognitive_gemini(img) is None
    ok = {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}
    monkeypatch.setattr(privacy, "_post_vision", lambda *a, **k: SimpleNamespace(status_code=200, headers={}, json=lambda: ok))
    assert privacy._detect_faces_cognitive_gemini(img) == []

    def boom(*a, **k):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(privacy, "_post_vision", boom)
    assert privacy._detect_faces_cognitive_gemini(img) is None


def test_cache_key_names_the_algorithm(monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    cv2.imwrite(str(photo), np.zeros((10, 10, 3), np.uint8))
    first = privacy._cache_path(photo)
    monkeypatch.setattr(privacy, "ALGORITHM_VERSION", "v10_next")
    assert privacy._cache_path(photo) != first and first.name.startswith("p_")


ROMAN = BASE_DIR / "assets" / "roman_self_healing_concrete" / "19_commons.jpg"
AMBER = BASE_DIR / "assets" / "amber_room_heist" / "08_commons.jpg"


def _the_dev_photo(path, sha16):
    """True only for the exact development photo the measurements were taken on: production keeps its own photos
    under the same names, and rc66's verify under the pipeline account ran these on them (2026-10-28)."""
    import hashlib
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()[:16] == sha16


@pytest.mark.skipif(not _the_dev_photo(ROMAN, "01605c2b9f3f6271"), reason="the dev photo is not here")
def test_roman_photo_19_head_and_body_boxes_blur_as_soft_heads(monkeypatch):
    # Two of the Gemini boxes recovered from the old cached blur (docs/reviews/privacy-blur-2026-10-28).
    img = cv2.imread(str(ROMAN))
    boxes = [(684, 2589, 115, 362), (2080, 2508, 138, 430)]
    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: list(boxes))
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: True)
    faces = privacy.detect_human_faces(img)
    assert len(faces) == 2 and all(h <= 1.6 * w for (_, _, w, h) in faces)
    out = privacy.blur_faces_on_image(img, faces)
    for (x, y, w, h) in faces:
        inner = cv2.cvtColor(out[y + h // 4:y + 3 * h // 4, x + w // 4:x + 3 * w // 4], cv2.COLOR_BGR2GRAY)
        assert inner.std() > 3  # no flat disc


@pytest.mark.skipif(not _the_dev_photo(AMBER, "8a10b7abeba550b1"), reason="the dev photo is not here")
def test_amber_room_mirror_box_is_not_blurred_when_the_verifier_refuses(monkeypatch, tmp_path):
    # Production Amber Room beat 7: Gemini's box over the plain mirror (recovered from the cached blur).
    monkeypatch.setattr(privacy, "FACE_PRIVACY_ENABLED", True)
    monkeypatch.setattr(privacy, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: [(3521, 3115, 144, 230)])
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: False)
    monkeypatch.setattr(privacy, "is_artwork", lambda p: False)
    assert privacy.blur_human_faces(AMBER) == AMBER


def test_a_face_whose_check_cannot_run_is_blurred_and_not_cached(monkeypatch):
    """Review 2026-10-28: a verifier error dropped a real face (skin-colour fallback) and cached it as complete."""
    img = np.full((800, 800, 3), 120, np.uint8)
    monkeypatch.setattr(privacy, "_detect_faces_cognitive_gemini", lambda im: [(300, 300, 80, 100), (500, 300, 80, 100)])
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)
    verdicts = {300: True, 500: None}
    monkeypatch.setattr(privacy, "_is_living_human_candidate", lambda im, b: verdicts[b[0]])
    status = {}
    assert privacy.detect_human_faces(img, status=status) == [(300, 300, 80, 100), (500, 300, 80, 100)]
    assert status["complete"] is False


def test_the_verifier_says_unanswered_instead_of_guessing_from_skin_colour(monkeypatch):
    img = np.full((400, 400, 3), 120, np.uint8)
    monkeypatch.setattr(privacy, "is_configured", lambda v: True, raising=False)
    import src.config as config
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "is_configured", lambda v: bool(v))
    monkeypatch.setattr(privacy, "_post_vision", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    monkeypatch.setattr(privacy, "claude_available", lambda: False)
    assert privacy._is_living_human_candidate(img, (100, 100, 80, 100)) is None
