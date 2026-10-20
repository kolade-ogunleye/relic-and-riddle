"""Pools are approved on merit by a vision review when the operator isn't available; the operator's
own decision always stands, and no model means no approval."""
import json

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
        Image.new("RGB", (64, 48), (i * 10, 0, 0)).save(tmp_path / f)
        imgs.append({"file": f, "title": f"File:Kailasa {i}.jpg", "license": "CC0"})
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": False, "images": imgs, **extra}), encoding="utf-8")
    return imgs


def _verdicts(spec):
    return image_review.PoolReview(verdicts=[image_review.PhotoVerdict(index=i, reason="r", **v) for i, v in spec.items()])


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
    # The storyboard puts photo 03 first, so the sheet labels it 1.
    (tmp_path / "storyboard.json").write_text(json.dumps([{"beat": 1, "file": "03_commons.jpg"}]), encoding="utf-8")
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
