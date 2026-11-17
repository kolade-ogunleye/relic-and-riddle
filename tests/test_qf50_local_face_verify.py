"""QF-50: face verification runs on this machine by default (no photo or face crop goes to a cloud model),
archival (monochrome) photos are still blurred, docs/privacy.md maps every personal-data store in four
tables, and the audience_* tables have a prune rule. The local verdict is YuNet's score, the face's size
in the photo and a printed-letter check; skin tone no longer decides (it dropped real faces in shade)."""
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from src import audience, config, privacy

REPO = Path(__file__).resolve().parents[1]
STORES = ("audit_log", "audience_", "cache/privacy", "assets/")
FACE = (160, 145, 80, 110)


def _landmarks(box):
    """Where YuNet puts a frontal face's eyes, nose tip and mouth corners in its box."""
    x, y, w, h = box
    return [x + .32 * w, y + .40 * h, x + .68 * w, y + .40 * h, x + .50 * w, y + .60 * h,
            x + .36 * w, y + .78 * h, x + .64 * w, y + .78 * h]


class _FakeYuNet:
    """Stands in for the YuNet ONNX model (not in the repository): proposes the given boxes, each with
    plausible landmarks and the given score."""

    def __init__(self, *boxes, score=0.9):
        self.boxes, self.score = boxes, score

    def detect(self, img):
        if not self.boxes:
            return 1, None
        return 1, np.array([list(b) + _landmarks(b) + [self.score] for b in self.boxes], dtype=np.float32)


def _calls_out(monkeypatch):
    """Configures both cloud models and records every request that would leave the machine."""
    import requests
    calls = []
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "AIza-configured-for-the-test")
    monkeypatch.setattr(privacy, "claude_available", lambda: True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(("http", a, k)) or SimpleNamespace(
        status_code=200, headers={}, json=lambda: {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}))
    monkeypatch.setattr(privacy, "structured_call", lambda *a, **k: calls.append(("claude", a, k)) or
                        SimpleNamespace(is_living_human=True))
    return calls


def _photo(colour=True, bg=(70, 100, 130), skin=(140, 165, 215), hair=(40, 45, 55)):
    """A deterministic face: hair, a shaded oval, eyes, nose and mouth, on a noisy background."""
    rng = np.random.default_rng(5)
    img = np.full((400, 400, 3), bg, np.uint8)
    img = cv2.add(img, rng.integers(0, 12, img.shape, dtype=np.uint8))
    cv2.ellipse(img, (200, 175), (52, 58), 0, 180, 360, hair, -1)
    cv2.ellipse(img, (200, 200), (45, 60), 0, 0, 360, skin, -1)
    shade = np.zeros((400, 400), np.float32)
    cv2.ellipse(shade, (225, 205), (30, 50), 0, 0, 360, 1.0, -1)
    img = (img * (1 - 0.25 * cv2.GaussianBlur(shade, (0, 0), 12)[..., None])).astype(np.uint8)
    for ex in (183, 217):
        cv2.ellipse(img, (ex, 190), (7, 4), 0, 0, 360, (40, 40, 40), -1)
    cv2.line(img, (200, 195), (197, 215), tuple(int(c * 0.8) for c in skin), 2)
    cv2.ellipse(img, (200, 232), (14, 5), 0, 0, 180, (60, 60, 110), 2)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    if not colour:
        img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return img


def _typed_page():
    """Typed letters on a yellowed page, as in a scanned case file: YuNet scores "o", "e", "a" up to 0.85."""
    page = np.full((400, 400, 3), (190, 210, 222), np.uint8)
    for i, ch in enumerate("oeaoe"):
        cv2.putText(page, ch, (40 + i * 60, 220), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (40, 40, 50), 5, cv2.LINE_AA)
    return cv2.GaussianBlur(page, (0, 0), 1.0)


def _relief(size=1200):
    """Carved stone: a weathered grey-brown texture with shaded hollows, deterministic."""
    rng = np.random.default_rng(11)
    noise = cv2.GaussianBlur(rng.normal(0, 1, (size, size)).astype(np.float32), (0, 0), 6)
    stone = 120 + 25 * noise / noise.std()
    for _ in range(12):
        cx, cy = int(rng.integers(100, size - 100)), int(rng.integers(100, size - 100))
        cv2.ellipse(stone, (cx, cy), (60, 80), 0, 0, 360, 80.0, -1)
    stone = np.clip(cv2.GaussianBlur(stone, (0, 0), 3), 0, 255)
    return np.dstack([stone * 0.85, stone * 0.95, stone]).astype(np.uint8)


def test_face_verification_is_local_by_default():
    assert config.FACE_VERIFY_CLOUD is False
    assert privacy.cloud_verify() is False


def test_detect_human_faces_makes_no_cloud_call_with_the_flag_off(monkeypatch):
    calls = _calls_out(monkeypatch)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    faces = privacy.detect_human_faces(_photo())
    assert calls == []          # no photo, no face crop left the machine
    assert faces == [FACE]      # and the face is still found and kept for the blur


def test_the_cloud_is_used_only_when_switched_on(monkeypatch):
    calls = _calls_out(monkeypatch)
    monkeypatch.setattr("src.config.FACE_VERIFY_CLOUD", True)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    privacy.detect_human_faces(_photo())
    assert calls and calls[0][0] == "http"
    assert calls[0][2]["headers"]["x-goog-api-key"] == "AIza-configured-for-the-test"  # the billed key


def test_archival_monochrome_faces_are_still_blurred_locally(monkeypatch):
    _calls_out(monkeypatch)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    assert privacy.detect_human_faces(_photo(colour=False)) == [FACE]
    portrait = _photo(colour=False, bg=(215, 215, 215), skin=(165, 170, 180), hair=(30, 30, 30))
    assert privacy.detect_human_faces(portrait) == [FACE]  # a studio portrait on a light ground is not a letter


def test_a_face_in_shade_is_blurred_whatever_its_skin_tone(monkeypatch):
    # Dev Kailasa and Aswan photos: the skin-tone share dropped visitors in shade, faces with low chroma.
    _calls_out(monkeypatch)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    for skin in ((50, 58, 72), (62, 70, 84), (90, 110, 150)):
        assert privacy.detect_human_faces(_photo(skin=skin, hair=(20, 20, 25))) == [FACE], skin


def test_typed_letters_are_not_blurred(monkeypatch):
    # Dev Dyatlov case-file pages: YuNet boxes on "o" and "e" with face-like landmarks and scores to 0.85.
    _calls_out(monkeypatch)
    letters = [(40, 180, 45, 50), (100, 180, 45, 50), (160, 180, 45, 50)]
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(*letters, score=0.84))
    assert privacy.detect_human_faces(_typed_page()) == []
    assert all(privacy._printed_letter(_typed_page(), b) for b in letters)
    assert not privacy._printed_letter(_photo(colour=False), FACE)


def test_unsure_boxes_on_carved_stone_are_not_blurred(monkeypatch):
    # Dev reliefs, maps and satellite views: large boxes at YuNet's threshold, specks too small to be a face.
    _calls_out(monkeypatch)
    stone = _relief()
    for box, score in (((300, 300, 260, 300), 0.62), ((600, 600, 80, 95), 0.52)):
        monkeypatch.setattr(privacy, "_get_yunet", lambda w, h, b=box, s=score: _FakeYuNet(b, score=s))
        assert privacy.detect_human_faces(stone) == [], (box, score)
    # A speck under 0.3 % of the shorter side: at most 9 px in any frame, not an identifiable face.
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet((900, 200, 8, 8), score=0.95))
    assert privacy.detect_human_faces(_relief(3000)) == []
    # A confident, face-sized box on the same stone is still blurred: the local check cannot tell a carved face
    # from a living one, and keeps it (docs/privacy.md).
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet((600, 600, 80, 95), score=0.88))
    assert privacy.detect_human_faces(stone) == [(600, 600, 80, 95)]


def test_the_cascade_stands_in_only_for_a_yunet_that_cannot_run(monkeypatch):
    # Dev photos: after YuNet found nothing, the cascade's boxes were all false (windows, trees, letters).
    _calls_out(monkeypatch)
    cascade = SimpleNamespace(detectMultiScale=lambda *a, **k: [(160, 145, 80, 80)])
    monkeypatch.setattr(privacy, "_get_cascade", lambda: cascade)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet())
    assert privacy.detect_human_faces(_photo()) == []
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: None)  # no YuNet model: the cascade is all there is
    assert privacy.detect_human_faces(_photo()) == [(160, 145, 80, 80)]


def test_the_privacy_data_map_has_its_four_tables():
    text = (REPO / "docs" / "privacy.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    tables = {s.splitlines()[0].split(". ", 1)[-1].strip(): s for s in sections if re.match(r"\d\. ", s)}
    assert set(tables) == {"Purpose", "Retention", "Processors", "Deletion"}
    for name, body in tables.items():
        assert "|---|" in body, name
        for store in STORES:
            assert store in body, (name, store)
    assert "FACE_VERIFY_CLOUD" in text and "Anthropic" in text and "Gemini" in text


def test_audience_rows_are_pruned_and_not_measured_again(db):
    old = (datetime.now(UTC) - timedelta(days=800)).isoformat()
    audience.ensure_schema(db)
    db.create_run("run_old", "01_topic")
    db.record_publication("01_topic", "run_old", "upload-post", "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ?", (old,))
        conn.execute("INSERT INTO audience_metrics VALUES (?,?,?,?,?,?)", ("run_old", "instagram", "28d", "x", "{}", old))
        conn.execute("INSERT INTO audience_media_ids VALUES (?,?,?,?,?)", ("run_old", "instagram", "1", "x", old))
        conn.execute("INSERT INTO audience_sources VALUES (?,?,?,?,?,?,?)", (old, "run_old", "28d", "instagram", "x", "ok", ""))
    pruned = db.prune_history()
    assert {k: pruned.get(k) for k in ("audience_metrics", "audience_media_ids", "audience_sources")} == {
        "audience_metrics": 1, "audience_media_ids": 1, "audience_sources": 1}
    assert audience.due_snapshots(db) == []  # a pruned post is not measured again


# Operator decision 2026-11-05 (option C): faces the local check keeps in an art-like photo are asked of Gemini's
# paid tier only with FACE_VERIFY_CLOUD_AMBIGUOUS on; off, or a failed call, blurs them and sends nothing.
SPECK = (20, 20, 10, 10)  # under 0.3 % of nothing: a 10 px box YuNet scores low, rejected on this machine


class _ScoredYuNet:
    """Proposes each (box, score) pair with frontal landmarks."""

    def __init__(self, *pairs):
        self.pairs = pairs

    def detect(self, img):
        return 1, np.array([list(b) + _landmarks(b) + [s] for b, s in self.pairs], dtype=np.float32)


def _gemini(monkeypatch, db, reply=None, status_code=200, raises=None):
    """Configures the billed key and records every Gemini request; the ledger is the test database."""
    import requests
    sent = []
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "AIza-configured-for-the-test")
    monkeypatch.setattr(privacy, "claude_available", lambda: True)
    monkeypatch.setattr(privacy, "structured_call", lambda *a, **k: sent.append(("claude", a, k)))
    monkeypatch.setattr(privacy, "_usage_ledger", lambda: db)

    def post(url, json=None, timeout=None, headers=None):
        sent.append(("http", url, json, headers))
        if raises:
            raise raises
        body = {"candidates": [{"content": {"parts": [{"text": reply or '{"is_living_human": false}'}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 20, "thoughtsTokenCount": 0}}
        return SimpleNamespace(status_code=status_code, headers={}, json=lambda: body)
    monkeypatch.setattr(requests, "post", post)
    return sent


def _usage(db):
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT model, input_tokens, output_tokens, cost_usd, task FROM llm_usage")]


def test_ambiguous_face_check_is_off_by_default():
    assert config.FACE_VERIFY_CLOUD_AMBIGUOUS is False
    assert privacy.ambiguous_cloud() is False


def test_no_crop_is_sent_with_the_ambiguous_setting_off(monkeypatch, db):
    sent = _gemini(monkeypatch, db)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    assert privacy.detect_human_faces(_photo(), art_like=True) == [FACE]  # kept, blurred: fail closed
    assert sent == [] and _usage(db) == []


def test_only_the_ambiguous_crops_are_sent_with_the_setting_on(monkeypatch, db):
    import base64
    sent = _gemini(monkeypatch, db, reply='{"is_living_human": false, "explanation": "carved stone"}')
    monkeypatch.setattr("src.config.FACE_VERIFY_CLOUD_AMBIGUOUS", True)
    monkeypatch.setattr("src.config.GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _ScoredYuNet((FACE, 0.9), (SPECK, 0.51)))
    # Not an art-like photo: the local verdict stands, nothing is sent.
    assert privacy.detect_human_faces(_photo()) == [FACE]
    assert sent == []
    # Art-like: only the face the local check kept goes out (the speck it rejected does not), as a crop.
    status: dict = {}
    assert privacy.detect_human_faces(_photo(), status=status, art_like=True) == []  # Gemini: a carving
    assert len(sent) == 1
    _, url, body, headers = sent[0]
    assert "gemini-3.8-flash:generateContent" in url
    assert headers == {"x-goog-api-key": "AIza-configured-for-the-test"}  # the billed key, never in the URL
    assert body["system_instruction"]["parts"][0]["text"] == privacy.VERIFY_PROMPT  # the task in its own role (QF-41)
    parts = body["contents"][0]["parts"]
    assert not any("text" in p for p in parts)  # no system text inside contents (QF-41)
    part = next(p["inline_data"] for p in parts if "inline_data" in p)
    crop = cv2.imdecode(np.frombuffer(base64.b64decode(part["data"]), np.uint8), cv2.IMREAD_COLOR)
    assert crop.shape[:2] == (164, 140)  # the padded face crop, not the 400 x 400 photo
    rows = _usage(db)
    assert rows == [{"model": "gemini-3.8-flash (face check)", "input_tokens": 1000, "output_tokens": 20,
                     "cost_usd": round(1000 / 1e6 * 0.75 + 20 / 1e6 * 3.75, 6), "task": privacy.AMBIGUOUS_TASK}]
    assert status["cloud_calls"] == 1 and status["cloud_cost_usd"] == rows[0]["cost_usd"]
    # A living visitor in the same kind of photo stays blurred.
    sent.clear()
    _gemini(monkeypatch, db, reply='{"is_living_human": true}')
    assert privacy.detect_human_faces(_photo(), art_like=True) == [FACE]


def test_a_failed_ambiguous_check_blurs_the_face(monkeypatch, db):
    monkeypatch.setattr("src.config.FACE_VERIFY_CLOUD_AMBIGUOUS", True)
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    for kwargs in ({"status_code": 400}, {"raises": TimeoutError("read timed out")}, {"reply": "not json"}):
        sent = _gemini(monkeypatch, db, **kwargs)
        status: dict = {}
        assert privacy.detect_human_faces(_photo(), status=status, art_like=True) == [FACE], kwargs
        assert len(sent) == 1 and sent[0][0] == "http", kwargs  # Gemini only: no Claude fallback
        assert status["complete"] is False, kwargs  # an unanswered check is not cached
    assert len(_usage(db)) == 3  # every request that went out is in the ledger
    # No billed key: nothing is sent, the face is blurred.
    sent = _gemini(monkeypatch, db)
    monkeypatch.setattr("src.config.GEMINI_API_KEY", "")
    assert privacy.detect_human_faces(_photo(), art_like=True) == [FACE] and sent == []


def test_blur_human_faces_asks_only_about_photos_whose_record_names_art(monkeypatch, db, tmp_path):
    import json
    sent = _gemini(monkeypatch, db, reply='{"is_living_human": false}')
    monkeypatch.setattr("src.config.FACE_VERIFY_CLOUD_AMBIGUOUS", True)
    monkeypatch.setattr(privacy, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(privacy, "_get_yunet", lambda w, h: _FakeYuNet(FACE))
    folder = tmp_path / "topic"
    folder.mkdir()
    for name in ("01_commons.jpg", "02_commons.jpg"):
        cv2.imwrite(str(folder / name), _photo())
    (folder / "credits.json").write_text(json.dumps({"images": [
        {"file": "01_commons.jpg", "title": "File:Temple premises.jpg", "categories": ["Reliefs and sculptures"]},
        {"file": "02_commons.jpg", "title": "File:Harbour at dawn.jpg", "categories": ["Harbours"]}]}), encoding="utf-8")
    assert privacy.looks_like_art(folder / "01_commons.jpg") and not privacy.looks_like_art(folder / "02_commons.jpg")
    assert privacy.blur_human_faces(folder / "01_commons.jpg") == folder / "01_commons.jpg"  # a carving: untouched
    assert len(sent) == 1
    assert privacy.blur_human_faces(folder / "02_commons.jpg") != folder / "02_commons.jpg"  # decided locally
    assert len(sent) == 1
