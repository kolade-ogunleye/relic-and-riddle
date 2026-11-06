"""Pools are approved on merit by a vision review when the operator isn't available; the operator's
own decision always stands, and no model means no approval."""
import json

import numpy as np
import pytest
from PIL import Image

from src import image_review
from src.credits import drop_images, load_credits, sheet_order, write_contact_sheet
from src.llm import LLMError

TOPIC = {"id": "kailasa", "title": "Kailasa Temple", "location": {"name": "Ellora", "country": "India"}}


def _pool(tmp_path, n, **extra):
    imgs = []
    for i in range(1, n + 1):
        f = f"{i:02d}_commons.jpg"
        # Distinct valid source fixtures: this suite isolates the model's decisions.
        pixels = np.random.default_rng(i).integers(0, 256, (48, 64, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(tmp_path / f)
        imgs.append({"file": f, "title": f"File:Kailasa {i}.jpg", "license": "CC0",
                     "artist": f"Photographer {i}", "shot_class": ("wide", "interior", "detail")[i % 3]})
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": False, "images": imgs, **extra}), encoding="utf-8")
    return imgs


def _verdicts(spec):
    return image_review.PoolReview(verdicts=[image_review.PhotoVerdict(
        index=i, reason="r", shot_class=("wide", "interior", "detail")[i % 3], **v) for i, v in spec.items()])


def test_pool_approved_on_merit_and_failures_dropped(monkeypatch, tmp_path):
    _pool(tmp_path, 7)
    good = {"shows_topic": "yes", "usable": True, "safe": True}
    spec = {i: dict(good) for i in range(1, 8)}
    spec[2] = {**good, "shows_topic": "no"}          # a namesake town
    spec[4] = {**good, "duplicate_of": 3}            # near-duplicate
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: _verdicts(spec))
    summary = image_review.review_pool(TOPIC, tmp_path)
    credits = load_credits(tmp_path)
    assert summary["status"] == "approved" and credits["reviewed"] and credits["reviewed_by"] == "vision"
    assert [i["file"][:2] for i in credits["images"]] == ["01", "03", "05", "06", "07"]


def test_too_few_good_photos_means_held(monkeypatch, tmp_path):
    _pool(tmp_path, 5)
    spec = {i: {"shows_topic": "no", "usable": True, "safe": True} for i in range(1, 6)}
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: _verdicts(spec))
    assert image_review.review_pool(TOPIC, tmp_path)["status"] == "held"
    assert load_credits(tmp_path)["reviewed"] is False


def test_operator_decision_stands_and_no_model_means_no_approval(monkeypatch, tmp_path):
    _pool(tmp_path, 6, reviewed=True, reviewed_by="operator")
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: pytest.fail("re-reviewed an operator decision"))
    assert image_review.review_pool(TOPIC, tmp_path)["status"] == "operator decision stands"
    _pool(tmp_path, 6)

    def down(*a, **k):
        raise LLMError("quota")
    monkeypatch.setattr(image_review.free_llm, "call", down)
    assert image_review.review_pool(TOPIC, tmp_path)["status"] == "review unavailable"
    assert load_credits(tmp_path)["reviewed"] is False


def test_drop_numbers_match_the_contact_sheet(tmp_path):
    imgs = _pool(tmp_path, 4)
    # Photos that show the topic come first on the sheet (2026-10-25), so photo 03 is labelled 1.
    imgs[2]["vision_review"] = {"shows_topic": "yes", "usable": True, "safe": True}
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": False, "images": imgs}), encoding="utf-8")
    assert [i["file"] for i in sheet_order(tmp_path, imgs)][0] == "03_commons.jpg"
    write_contact_sheet(tmp_path, imgs)
    drop_images(tmp_path, [1])
    assert not (tmp_path / "03_commons.jpg").exists() and (tmp_path / "01_commons.jpg").exists()


def test_dropped_photos_are_never_fetched_again(tmp_path):
    imgs = _pool(tmp_path, 3)
    write_contact_sheet(tmp_path, imgs)
    drop_images(tmp_path, [2])
    rejected = [r["title"] for r in load_credits(tmp_path).get("rejected_by_review", [])]
    assert rejected == ["File:Kailasa 2.jpg"]  # the fetcher skips these titles


def test_crowded_photos_rank_last_and_are_flagged_to_the_planner(monkeypatch, tmp_path):
    from src import scene_planner as sp
    _pool(tmp_path, 6)
    good = {"shows_topic": "yes", "usable": True, "safe": True}
    spec = {i: dict(good) for i in range(1, 7)}
    spec[1] = {**good, "people_prominent": True}
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: _verdicts(spec))
    image_review.review_pool(TOPIC, tmp_path)
    images = load_credits(tmp_path)["images"]
    assert images[-1]["file"] == "01_commons.jpg"
    sp._folder_credits.cache_clear()
    assert "faces blurred" in sp._photo_caption(tmp_path / "01_commons.jpg")


def test_an_unchanged_pool_is_not_sent_again(monkeypatch, tmp_path):
    _pool(tmp_path, 5)
    calls = []
    spec = {i: {"shows_topic": "no" if i == 5 else "yes", "usable": True, "safe": True} for i in range(1, 6)}
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: calls.append(1) or _verdicts(spec))
    assert image_review.review_pool(TOPIC, tmp_path)["status"] == "held"           # 4 kept: too few
    assert image_review.review_pool(TOPIC, tmp_path)["status"].startswith("held (unchanged")
    assert len(calls) == 1                                                          # no second vision call
    credits = load_credits(tmp_path)                                                # a refill adds a photo
    Image.new("RGB", (64, 48)).save(tmp_path / "09_commons.jpg")
    credits["images"].append({"file": "09_commons.jpg", "title": "File:Kailasa 9.jpg", "license": "CC0"})
    (tmp_path / "credits.json").write_text(json.dumps(credits), encoding="utf-8")
    image_review.review_pool(TOPIC, tmp_path)
    assert len(calls) == 2                                                          # changed pool: reviewed
    image_review.review_pool(TOPIC, tmp_path, force=True)
    assert len(calls) == 3                                                          # forced: reviewed



def test_only_new_photos_are_screened_and_the_approval_stands(tmp_path, monkeypatch):
    # Production 2026-10-25: photos fetched after an approval were never looked at, and reached the video.
    imgs = _pool(tmp_path, 3, reviewed=True, reviewed_by="operator")
    imgs[0]["vision_review"] = {"shows_topic": "yes", "usable": True, "safe": True}
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": True, "reviewed_by": "operator", "images": imgs}), encoding="utf-8")
    sent = []

    def call(system, content, schema, **kw):
        sent.append(sum(1 for c in content if c.get("type") == "image"))
        return _verdicts({1: {"shows_topic": "partly", "usable": True, "safe": True},
                          2: {"shows_topic": "no", "usable": True, "safe": True}})
    monkeypatch.setattr(image_review.free_llm, "call", call)
    monkeypatch.setattr(image_review, "image_block", lambda p, *a: {"type": "image"})
    assert image_review.screen_unchecked(TOPIC, tmp_path) == 2 and sent == [2]
    cr = load_credits(tmp_path)
    assert [image_review.tier(i) for i in cr["images"]] == ["topic", "context", "weak"]
    assert cr["reviewed"] is True and cr["reviewed_by"] == "operator" and len(cr["images"]) == 3
    assert image_review.screen_unchecked(TOPIC, tmp_path) == 0                     # nothing left to look at


def test_a_kept_photo_is_used_and_stories_use_only_strong_ones(tmp_path):
    from src.credits import keep_images
    imgs = _pool(tmp_path, 3)
    imgs[0]["vision_review"] = {"shows_topic": "yes", "usable": True, "safe": True}
    imgs[1]["vision_review"] = {"shows_topic": "partly", "usable": True, "safe": True}
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": True, "images": imgs}), encoding="utf-8")
    keep_images(tmp_path, [3])                                                    # the unchecked one, kept by you
    cr = load_credits(tmp_path)
    assert [image_review.tier(i) for i in cr["images"]] == ["topic", "context", "kept"]
    stems = image_review.strong_stems(tmp_path)
    assert stems == {"01_commons", "03_commons"}
    paths = [tmp_path / "01_commons_ab12.jpg", tmp_path / "02_commons.jpg", tmp_path / "03_commons.jpg"]
    assert image_review.only_strong(paths, stems) == [paths[0], paths[2]]          # prepared copies keep the stem


def test_removing_photos_keeps_an_approved_set_approved(tmp_path):
    imgs = _pool(tmp_path, 7)
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": True, "images": imgs}), encoding="utf-8")
    write_contact_sheet(tmp_path, imgs)
    assert "stays approved" in drop_images(tmp_path, [1])
    assert load_credits(tmp_path)["reviewed"] is True
    drop_images(tmp_path, [1, 2])                                                 # too few left: a fresh decision
    assert load_credits(tmp_path)["reviewed"] is False
