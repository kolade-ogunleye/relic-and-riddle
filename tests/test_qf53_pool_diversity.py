"""Pixel duplicates and source/shot diversity cannot be waived by a model's yes."""

import numpy as np
from PIL import Image, ImageEnhance


def _module():
    from src import photo_pool
    return photo_pool


def _photo(path, seed):
    rng = np.random.default_rng(seed)
    # Structured but distinct luminance, robust to resampling/recompression.
    a = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
    Image.fromarray(a).resize((512, 512), Image.Resampling.BICUBIC).save(path)


def _records(tmp_path, authors):
    module = _module()
    records = []
    for index, author in enumerate(authors):
        path = tmp_path / f"{index}.jpg"
        _photo(path, index + 53)
        records.append({"file": path.name, "artist": author,
                        "shot_class": ("wide", "interior", "detail")[index % 3], **module.signature(path)})
    return records


def test_brightness_and_recompression_do_not_evade_duplicate_detection(tmp_path):
    module = _module()
    source, derivative = tmp_path / "source.png", tmp_path / "derivative.jpg"
    _photo(source, 53)
    with Image.open(source) as image:
        ImageEnhance.Brightness(image).enhance(0.8).resize((640, 640)).save(derivative, quality=80)
    original = {"file": source.name, **module.signature(source)}
    assert module.duplicate_of(module.signature(derivative), [original]) is original


def test_exact_ten_bit_boundary_is_a_duplicate():
    module = _module()
    zero = {"phash": "0000000000000000", "dhash": "0000000000000000"}
    ten = {"phash": f"{(1 << 10) - 1:016x}", "dhash": "ffffffffffffffff"}
    eleven = {"phash": f"{(1 << 11) - 1:016x}", "dhash": "ffffffffffffffff"}
    assert module.duplicate_of(ten, [zero]) is zero
    assert module.duplicate_of(eleven, [zero]) is None


def test_dhash_also_rejects_near_duplicates():
    module = _module()
    original = {"phash": "0000000000000000", "dhash": "0000000000000000"}
    candidate = {"phash": "ffffffffffffffff", "dhash": "0000000000000001"}
    assert module.duplicate_of(candidate, [original]) is original


def test_same_file_name_cannot_reuse_a_stale_pixel_signature(tmp_path):
    module = _module()
    path = tmp_path / "photo.png"
    _photo(path, 53)
    before = module.signature(path)
    _photo(path, 54)
    after = module.signature(path)
    assert before["source_sha256"] != after["source_sha256"]
    assert module.duplicate_of(after, [before]) is None


def test_one_photographer_burst_is_balanced_without_deleting_assets(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    before = module.assess(records)
    selected, reserves = module.balanced(records)
    after = module.assess(selected)
    assert before["max_author_share"] == 0.70
    assert len(selected) == 9 and len(reserves) == 11
    assert after["ok"] and after["max_author_share"] <= 0.35
    assert len(after["shot_classes"]) >= 3
    assert all((tmp_path / image["file"]).exists() for image in records)


def test_missing_authors_cannot_manufacture_source_diversity(tmp_path):
    module = _module()
    records = _records(tmp_path, [""] * 6)
    assert module.assess(records)["max_author_share"] == 1
    assert not module.assess(records)["ok"]


def test_three_classes_required_even_with_many_authors(tmp_path):
    module = _module()
    records = _records(tmp_path, [f"Author {n}" for n in range(6)])
    for image in records:
        image["shot_class"] = "wide"
    result = module.assess(records)
    assert not result["ok"] and len(result["shot_classes"]) == 1


def test_human_keeps_are_retained_and_incompatible_pool_is_held(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records:
        image["operator_keep"] = True
    selected, reserves = module.balanced(records)
    assert selected == records and not reserves
    assert not module.assess(selected)["ok"]


def test_final_pixel_review_overrides_provisional_metadata_class():
    module = _module()
    image = {"shot_class": "wide", "vision_review": {"shot_class": "detail"}}
    assert module.shot_class(image) == "detail"
    assert module.provisional_shot_class({"title": "Temple interior chamber"}) == "interior"


def _review_fixture(tmp_path, *, duplicates=False):
    from src.credits import save_credits
    # Exercise the old approval paths on the parent too, without importing the new helper.
    records = []
    for index in range(6):
        path = tmp_path / f"{index}.jpg"
        _photo(path, index + 53)
        records.append({"file": path.name, "artist": f"Author {index}",
                        "shot_class": ("wide", "interior", "detail")[index % 3]})
    if duplicates:
        data = (tmp_path / records[0]["file"]).read_bytes()
        for record in records[1:]:
            (tmp_path / record["file"]).write_bytes(data)
    save_credits(tmp_path, {"reviewed": False, "images": records})
    return records


def test_model_yes_cannot_approve_pixel_duplicates(monkeypatch, tmp_path):
    from src import image_review
    records = _review_fixture(tmp_path, duplicates=True)
    verdicts = image_review.PoolReview(verdicts=[image_review.PhotoVerdict(
        index=i + 1, shows_topic="yes", usable=True, safe=True, reason="Looks good",
        shot_class=record["shot_class"]) for i, record in enumerate(records)])
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: verdicts)
    result = image_review.review_pool({"id": "fixture", "title": "Temple"}, tmp_path)
    assert result["status"] == "held"
    assert result["diversity"]["duplicate_pairs"]


def test_new_human_approval_checks_actual_files(tmp_path):
    import pytest

    from src.credits import approve_set, load_credits
    _review_fixture(tmp_path, duplicates=True)
    with pytest.raises(ValueError, match="Near-duplicate"):
        approve_set(tmp_path, 5)
    assert not load_credits(tmp_path)["reviewed"]


def test_changed_pixels_trigger_a_fresh_review(monkeypatch, tmp_path):
    from src import image_review
    records = _review_fixture(tmp_path)
    calls = []
    verdicts = image_review.PoolReview(verdicts=[image_review.PhotoVerdict(
        index=i + 1, shows_topic="yes", usable=True, safe=True, reason="Looks good",
        shot_class=record["shot_class"]) for i, record in enumerate(records)])
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: calls.append(1) or verdicts)
    topic = {"id": "fixture", "title": "Temple"}
    assert image_review.review_pool(topic, tmp_path)["status"] == "approved"
    _photo(tmp_path / records[0]["file"], 900)
    image_review.review_pool(topic, tmp_path)
    assert len(calls) == 2


def test_existing_full_burst_leaves_room_for_other_authors(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 20)
    selected, reserve = module.fetch_subset(records, 20)
    assert len(selected) == 7 and len(reserve) == 13
    assert all(image["reserve_reason"] == "author quota" for image in reserve)


def test_balancing_cannot_increase_low_crop_share_above_ten_percent(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records:
        image["vertical_crop_width"] = 1350
    records[0]["vertical_crop_width"] = 1181
    selected, reserve = module.balanced(records)
    assert len(selected) == 9
    assert all(image["vertical_crop_width"] >= 1350 for image in selected)
    assert records[0] in reserve


def test_essential_exception_is_retained_but_does_not_waive_diversity(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records[:14]:
        image["presentation"] = "full_image"
    selected, reserve = module.balanced(records)
    assert selected == records and not reserve
    assert not module.assess(selected)["ok"]
    selected, reserve = module.fetch_subset(records, 20)
    assert all(image in selected for image in records[:14])
