"""Fixes for the PR #3 review (2026-11-02)."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src import beat_images, engagement_learning, hook_learning
from src.multi_format import MultiFormatGenerator
from src.visual_engine import VisualEngine


def _barred(path: Path) -> Path:
    rng = np.random.default_rng(1)
    im = Image.fromarray(rng.normal(110, 35, (90, 120, 3)).clip(0, 255).astype(np.uint8)).resize((1200, 900))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1200, 40), fill=(170, 180, 8))
    d.text((8, 20), "Copernicus Sentinel data [2023], processed by Sentinel Hub", fill=(20, 20, 10), anchor="lm")
    im.save(path, quality=95)
    return path


def test_the_screen_cache_stays_in_the_topic_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(beat_images.config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(beat_images, "picture_look", lambda p, subject, size=None, **k: {"looked": True})
    monkeypatch.setattr(beat_images, "_text_verdict", lambda look: {"text": False, "reason": "clear", "edge": "none",
                                                                     "box": [], "object": ""})
    topic = tmp_path / "topic"
    topic.mkdir()
    photo = _barred(topic / "01_commons.jpg")
    beat_images.without_burned_in_text([photo], "subject", minimum=1)
    assert (topic / beat_images.TEXT_SCREEN_FILE).is_file()
    assert not list((tmp_path / "text_crop").rglob(beat_images.TEXT_SCREEN_FILE))


def test_story_and_carousel_rows_do_not_steer_the_reel() -> None:
    rows = [{"platform": "instagram_story1", "run_id": "a", "hook_form": "question", "skip_rate": 10.0,
             "share_form": "identity", "share_rate": 0.05, "question_form": "open", "comments": 5.0, "reach": 100.0},
            {"platform": "facebook_carousel", "run_id": "b", "hook_form": "figure", "skip_rate": 10.0,
             "share_form": "interest", "share_rate": 0.05, "question_form": "opinion", "comments": 5.0, "reach": 100.0}]
    assert hook_learning.evidence(rows) == {}
    assert engagement_learning._evidence(rows, "share_form", engagement_learning.share_reward) == {}


PAYLOAD = {"narration_script": "x", "share_line": "Send this to a space enthusiast you know, and follow.",
           "poll": {"question": "What shaped the rings?", "options": ["A", "B"]},
           "carousel_slides": [{"heading": "The Eye of the Sahara", "body": "A formation in Mauritania."},
                               {"heading": "Orbital landmark", "body": "Seen from space."},
                               {"heading": "Earth's hidden marvels", "body": "Which wonder looks best from orbit?"}]}


def _photos(tmp_path):
    out = []
    for i, c in enumerate(((90, 120, 140), (150, 120, 80))):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1600, 1200), c).save(p)
        out.append(p)
    return out


def test_the_closing_slide_names_its_subject_and_concurrent_copies_keep_theirs(tmp_path, monkeypatch) -> None:
    import threading

    from src import multi_format
    drawn: dict = {}
    real = multi_format.draw_kicker

    def capture(im, x, baseline, text, *a, **k):
        drawn.setdefault(threading.get_ident(), []).append(text)
        return real(im, x, baseline, text, *a, **k)
    monkeypatch.setattr(multi_format, "draw_kicker", capture)
    gen = MultiFormatGenerator()
    copies: dict = {}
    photos = _photos(tmp_path)                  # written once, before the two copies render at the same time

    def run(out, facebook):
        copies[threading.get_ident()] = facebook
        gen.generate_carousel_sequence(photos, PAYLOAD, out, facebook)
    with ThreadPoolExecutor(2) as pool:
        for f in [pool.submit(run, tmp_path / "ig", False), pool.submit(run, tmp_path / "fb", True)]:
            f.result()
    assert sorted(copies.values()) == [False, True]
    for ident in copies:
        kickers = drawn[ident]
        assert kickers[0] == "THE EYE OF THE SAHARA"                       # slide 2, both copies
        assert kickers[-1] == "THE EYE OF THE SAHARA · YOUR TAKE"          # the closing slide names it too


def test_a_cropped_first_photo_still_finds_its_topic_credits(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(beat_images.config, "PICTURE_VISION_CHECK", False)
    topic = tmp_path / "topic"
    topic.mkdir()
    first = _barred(topic / "01_commons.jpg")
    rng = np.random.default_rng(9)
    context = topic / "02_commons.jpg"
    Image.fromarray(rng.integers(0, 256, (150, 100, 3), dtype=np.uint8)).resize((1200, 1800), Image.Resampling.NEAREST).save(context)
    ok = {"usable": True, "safe": True}
    (topic / "credits.json").write_text(json.dumps({"reviewed": True, "images": [
        {"file": "01_commons.jpg", "vision_review": {**ok, "shows_topic": "yes"}},
        {"file": "02_commons.jpg", "vision_review": {**ok, "shows_topic": "partly"}}]}), encoding="utf-8")
    cropped = beat_images.without_burned_in_text([first, context], "s", minimum=1)
    assert cropped[0].parent != topic                       # the first photo is now a caption-strip copy
    assert VisualEngine.hero_image(cropped) == cropped[0]   # the context shot cannot take its place
