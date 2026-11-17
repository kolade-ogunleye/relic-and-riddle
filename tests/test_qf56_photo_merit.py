"""Batched pixel reviews preserve source quality and original-artifact priority."""

import base64
import importlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from src import image_review
from src.credits import load_credits, save_credits


def make_pool(root, count=25):
    records = []
    for index in range(count):
        path = root / f"{index:02d}.jpg"
        pixels = np.random.default_rng(index).integers(0, 256, (800, 900, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(path, quality=95)
        records.append({"file": path.name, "title": f"File:Artifact {index}", "artist": f"Author {index}",
                        "shot_class": ("wide", "interior", "detail")[index % 3]})
    save_credits(root, {"images": records, "reviewed": False})
    return records


def accept_batch(system, content, schema, **kwargs):
    count = sum(item.get("type") == "image" for item in content)
    return schema(verdicts=[{"index": index, "shows_topic": "yes", "usable": True, "safe": True,
                            "quality": 4, "depiction": "original", "reason": "Fixture original",
                            "shot_class": ("wide", "interior", "detail")[(index - 1) % 3]}
                           for index in range(1, count + 1)])


def test_every_photo_reviewed_in_twelve_image_batches_at_768(monkeypatch, tmp_path):
    make_pool(tmp_path)
    batches, sizes = [], []

    def review(system, content, schema, **kwargs):
        blocks = [item for item in content if item.get("type") == "image"]
        batches.append(len(blocks))
        for block in blocks:
            with Image.open(io.BytesIO(base64.b64decode(block["source"]["data"]))) as image:
                sizes.append(max(image.size))
        return accept_batch(system, content, schema, **kwargs)

    monkeypatch.setattr(image_review.free_llm, "call", review)
    image_review.review_pool({"id": "fixture", "title": "Artifact"}, tmp_path)
    credits = load_credits(tmp_path)
    all_records = credits["images"] + credits.get("diversity_reserve", [])
    assert batches == [12, 12, 1]
    assert sizes == [768] * 25
    assert len(all_records) == 25
    assert all(record["vision_review"]["quality"] == 4 and record["vision_review"]["depiction"] == "original"
               and record["technical_review"]["version"] == 1 for record in all_records)


def test_original_precedes_higher_relevance_reconstruction():
    def record(name, depiction, quality, relevance):
        return {"file": name, "relevance": relevance, "vision_review": {
            "shows_topic": "yes", "usable": True, "safe": True, "quality": quality, "depiction": depiction}}
    reconstruction = record("reconstruction.jpg", "reconstruction", 5, 1000)
    original = record("fragment.jpg", "original", 3, 1)
    assert image_review.usable_photos([reconstruction, original])[0] is original


def test_prechecks_detect_blur_clipping_cast_and_coarse_source_encoding(tmp_path):
    quality = importlib.import_module("src.photo_quality")
    pixels = np.random.default_rng(2).integers(0, 256, (800, 800, 3), dtype=np.uint8)
    sharp = Image.fromarray(pixels)
    blurred = sharp.filter(ImageFilter.GaussianBlur(8))

    def inspect(image, jpeg_quality=95):
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=jpeg_quality)
        return quality.inspect_bytes(buf.getvalue())

    assert inspect(sharp)["laplacian_variance"] > inspect(blurred)["laplacian_variance"] * 10
    assert "clipped highlights" in inspect(Image.new("RGB", (800, 800), "white"))["flags"]
    assert "possible colour cast" in inspect(Image.new("RGB", (800, 800), (210, 80, 40)))["flags"]
    assert "coarse JPEG quantization" in inspect(sharp, 10)["flags"]


def test_download_prechecks_original_quantization_before_reencoding(monkeypatch, tmp_path):
    from src import commons, url_guard
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(4).integers(0, 256, (800, 800, 3), dtype=np.uint8)).save(buf, "JPEG", quality=10)
    data = buf.getvalue()
    monkeypatch.setattr(url_guard, "follow", lambda *a, **k: object())
    monkeypatch.setattr(commons, "_capped_body", lambda *a, **k: data)
    path = tmp_path / "source.jpg"
    commons.download_jpeg("https://upload.wikimedia.org/fixture.jpg", path)
    sidecar = Path(str(path) + ".quality.json")
    assert sidecar.is_file()
    quality = importlib.import_module("src.photo_quality")
    measured = quality.inspect_path(path)
    assert measured["basis"] == "downloaded source bytes"
    assert "coarse JPEG quantization" in measured["flags"]
    assert measured["jpeg_quantization_mean"] > quality.inspect_bytes(path.read_bytes())["jpeg_quantization_mean"]
    Image.new("RGB", (40, 40), "white").save(path, quality=95)
    assert quality.inspect_path(path)["basis"].startswith("current pool bytes")


def test_unchecked_screen_processes_every_batch_without_changing_human_approval(monkeypatch, tmp_path):
    make_pool(tmp_path, 25)
    credits = load_credits(tmp_path)
    credits.update(reviewed=True, reviewed_by="operator")
    save_credits(tmp_path, credits)
    monkeypatch.setattr(image_review.free_llm, "call", accept_batch)
    assert image_review.screen_unchecked({"id": "fixture"}, tmp_path) == 25
    credits = load_credits(tmp_path)
    assert credits["reviewed"] and credits["reviewed_by"] == "operator"
    assert all("depiction" in record["vision_review"] for record in credits["images"])


def test_partial_batch_cannot_drop_unreviewed_remainder(monkeypatch, tmp_path):
    make_pool(tmp_path, 13)
    credits = load_credits(tmp_path)
    credits.update(reviewed=True, reviewed_by="vision")
    save_credits(tmp_path, credits)
    calls = []

    def review(system, content, schema, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            return schema(verdicts=[])
        return accept_batch(system, content, schema, **kwargs)

    monkeypatch.setattr(image_review.free_llm, "call", review)
    image_review.review_pool({"id": "fixture"}, tmp_path)
    credits = load_credits(tmp_path)
    assert len(credits["images"]) == 13 and not credits["reviewed"]


def test_duplicate_across_batches_is_not_missed_when_screening(monkeypatch, tmp_path):
    records = make_pool(tmp_path, 13)
    (tmp_path / records[-1]["file"]).write_bytes((tmp_path / records[0]["file"]).read_bytes())
    monkeypatch.setattr(image_review.free_llm, "call", accept_batch)
    image_review.screen_unchecked({"id": "fixture"}, tmp_path)
    assert load_credits(tmp_path)["images"][-1]["vision_review"]["duplicate_of"] == 1


def test_model_schema_requires_quality_and_depiction():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        image_review.PhotoVerdict(index=1, shows_topic="yes", usable=True, safe=True, reason="missing")
    with pytest.raises(ValidationError):
        image_review.PhotoVerdict(index=1, shows_topic="yes", usable=True, safe=True, reason="out of range",
                                  quality=6, depiction="original")


def test_old_auto_review_is_refreshed_to_get_merit_fields(monkeypatch, tmp_path):
    from src import photo_pool
    records = make_pool(tmp_path, 6)
    photo_pool.refresh(tmp_path, records)
    for record in records:
        record["vision_review"] = {"shows_topic": "yes", "usable": True, "safe": True}
    save_credits(tmp_path, {"images": records, "reviewed": True, "reviewed_by": "vision",
                           "review_fingerprint": image_review.pool_fingerprint(records)})
    calls = []
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: calls.append(1) or accept_batch(*a, **k))
    image_review.review_pool({"id": "fixture"}, tmp_path)
    assert calls and all(record["vision_review"].get("quality") for record in load_credits(tmp_path)["images"])


def _twin_pool(root, **last):
    """13 photos (two batches); photo 13 is a byte copy of photo 1, as Derinkuyu's cross-batch re-exposures are."""
    records = make_pool(root, 13)
    (root / records[-1]["file"]).write_bytes((root / records[0]["file"]).read_bytes())
    credits = load_credits(root)
    credits["images"][-1].update(last)
    save_credits(root, credits)
    return records


def _review_with(changes):
    """accept_batch, with per-photo changes keyed by the photo's number in the whole pool."""
    seen = []

    def review(system, content, schema, **kwargs):
        offset = sum(seen)
        reply = accept_batch(system, content, schema, **kwargs)
        seen.append(len(reply.verdicts))
        return schema(verdicts=[v.model_copy(update=changes.get(v.index + offset, {})) for v in reply.verdicts])
    return review


def _after_review(monkeypatch, root, changes):
    monkeypatch.setattr(image_review.free_llm, "call", _review_with(changes))
    image_review.review_pool({"id": "fixture", "title": "Artifact"}, root)
    credits = load_credits(root)
    return ({r["file"] for r in credits["images"]}, {r["file"] for r in credits.get("diversity_reserve", [])},
            {r["file"] for r in credits.get("rejected_by_review", [])})


def test_a_pool_review_keeps_the_better_of_two_cross_batch_twins(monkeypatch, tmp_path):
    _twin_pool(tmp_path)
    # Derinkuyu 27/29 and 22/31: the darker twin comes first, the model rates the later one higher.
    pool, reserve, rejected = _after_review(monkeypatch, tmp_path, {1: {"quality": 2}, 13: {"quality": 5}})
    assert "12.jpg" in pool and "00.jpg" in reserve and not rejected


def test_a_pool_review_never_rejects_an_essential_twin_from_a_later_batch(monkeypatch, tmp_path):
    _twin_pool(tmp_path, presentation="full_image")
    pool, reserve, rejected = _after_review(monkeypatch, tmp_path, {})
    assert "12.jpg" in pool and "00.jpg" in reserve and not rejected


def test_a_pool_review_keeps_the_twin_of_a_photo_the_model_refused(monkeypatch, tmp_path):
    _twin_pool(tmp_path)
    pool, reserve, rejected = _after_review(monkeypatch, tmp_path, {1: {"usable": False}})
    assert "12.jpg" in pool and rejected == {"00.jpg"} and "12.jpg" not in reserve


@pytest.mark.parametrize("last, changes", [({"presentation": "full_image"}, {}), ({}, {1: {"usable": False}})])
def test_screening_marks_no_protected_photo_and_no_twin_of_a_refused_one(monkeypatch, tmp_path, last, changes):
    _twin_pool(tmp_path, **last)
    monkeypatch.setattr(image_review.free_llm, "call", _review_with(changes))
    image_review.screen_unchecked({"id": "fixture"}, tmp_path)
    assert load_credits(tmp_path)["images"][-1]["vision_review"]["duplicate_of"] is None
