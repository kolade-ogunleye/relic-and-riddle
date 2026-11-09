"""Pixel duplicates and source/shot diversity cannot be waived by a model's yes."""

import json

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


def _bits(n):
    return f"{(1 << n) - 1:016x}"


def test_summed_distance_boundary_is_a_duplicate():
    # QF-53b: pHash + dHash distance <= 15 (calibrated on the real pools; see QF-53b evidence).
    module = _module()
    zero = {"phash": "0000000000000000", "dhash": "0000000000000000"}
    fifteen = {"phash": _bits(8), "dhash": _bits(7)}
    sixteen = {"phash": _bits(8), "dhash": _bits(8)}
    assert module.duplicate_of(fifteen, [zero]) is zero
    assert module.duplicate_of(sixteen, [zero]) is None


def test_one_close_hash_is_not_enough():
    # The Met swords 30975/30903 are 10 + 7 apart: a close pHash alone does not make different objects duplicates.
    module = _module()
    zero = {"phash": "0000000000000000", "dhash": "0000000000000000"}
    assert module.duplicate_of({"phash": _bits(10), "dhash": _bits(7)}, [zero]) is None
    assert module.duplicate_of({"phash": _bits(10), "dhash": _bits(2)}, [zero]) is zero
    assert module.duplicate_of({"phash": "0000000000000000", "dhash": "ffffffffffffffff"}, [zero]) is None


def test_same_file_name_cannot_reuse_a_stale_pixel_signature(tmp_path):
    module = _module()
    path = tmp_path / "photo.png"
    _photo(path, 53)
    before = module.signature(path)
    _photo(path, 54)
    after = module.signature(path)
    assert before["source_sha256"] != after["source_sha256"]
    assert module.duplicate_of(after, [before]) is None


def test_one_photographer_pool_keeps_every_photo_and_reports_its_share(tmp_path):
    # Operator decision QF-53b-author-cap: quotas govern what the fetcher adds; a pool's photos are never trimmed.
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    selected, reserves = module.deduplicated(records)
    result = module.assess(selected)
    assert selected == records and not reserves
    assert result["max_author_share"] == 0.70
    assert "An author supplies more than 35% of the pool" in result["problems"]
    assert not result["holds"]
    assert all((tmp_path / image["file"]).exists() for image in records)

def test_unnamed_authors_are_unverified_not_one_author(tmp_path):
    # QF-53b: "Unknown author" / missing attribution is reported, never counted as one shared author.
    module = _module()
    records = _records(tmp_path, ["", "Unknown author", "Anonymous, 16th century", "unknown", "", ""])
    result = module.assess(records)
    assert result["unverified_authors"] == 6
    assert result["max_author_share"] == 0
    assert "An author supplies more than 35% of the pool" not in result["problems"]


def test_named_author_share_still_counts_against_the_whole_pool(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 3 + ["", "", ""])
    result = module.assess(records)
    assert result["max_author_share"] == 0.5 and result["unverified_authors"] == 3
    assert "An author supplies more than 35% of the pool" in result["problems"]


def test_three_classes_required_even_with_many_authors(tmp_path):
    module = _module()
    records = _records(tmp_path, [f"Author {n}" for n in range(6)])
    for image in records:
        image["shot_class"] = "wide"
    result = module.assess(records)
    assert not result["ok"] and len(result["shot_classes"]) == 1


def test_human_keeps_are_retained_and_the_imbalance_is_reported(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records:
        image["operator_keep"] = True
    selected, reserves = module.deduplicated(records)
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
    verdicts = image_review.PoolReview(verdicts=[image_review.PhotoVerdict(quality=3, depiction="original", 
        index=i + 1, shows_topic="yes", usable=True, safe=True, reason="Looks good",
        shot_class=record["shot_class"]) for i, record in enumerate(records)])
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: verdicts)
    result = image_review.review_pool({"id": "fixture", "title": "Temple"}, tmp_path)
    credits = json.loads((tmp_path / "credits.json").read_text(encoding="utf-8"))
    # The five copies leave the pool as near-duplicate reserves; one photo is too few for the video: held.
    assert result["status"] == "held" and result["kept"] == 1
    assert [r["reserve_reason"] for r in credits["diversity_reserve"]] == ["near duplicate"] * 5

def test_new_human_approval_checks_actual_files(tmp_path):
    from src.credits import approve_set, load_credits
    _review_fixture(tmp_path, duplicates=True)
    res = approve_set(tmp_path, 5)
    credits = load_credits(tmp_path)
    assert credits["reviewed"] is True
    assert "Diversity notice" in res
    assert not credits["pool_diversity"]["ok"]


def test_changed_pixels_trigger_a_fresh_review(monkeypatch, tmp_path):
    from src import image_review
    records = _review_fixture(tmp_path)
    calls = []
    verdicts = image_review.PoolReview(verdicts=[image_review.PhotoVerdict(quality=3, depiction="original", 
        index=i + 1, shows_topic="yes", usable=True, safe=True, reason="Looks good",
        shot_class=record["shot_class"]) for i, record in enumerate(records)])
    monkeypatch.setattr(image_review.free_llm, "call", lambda *a, **k: calls.append(1) or verdicts)
    topic = {"id": "fixture", "title": "Temple"}
    assert image_review.review_pool(topic, tmp_path)["status"] == "approved"
    _photo(tmp_path / records[0]["file"], 900)
    image_review.review_pool(topic, tmp_path)
    assert len(calls) == 2


def test_a_refill_never_moves_the_pools_own_photos_for_a_quota(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 20)
    selected, reserve = module.fetch_subset(records, 20, retained=20)
    assert selected == records and not reserve
    offered = []  # reserves offered back by a refill: different pictures by the same author
    for index in range(2):
        path = tmp_path / f"offered_{index}.jpg"
        _photo(path, 900 + index)
        offered.append({"file": path.name, "artist": "Author A", "shot_class": "wide", **module.signature(path)})
    selected, reserve = module.fetch_subset(records[:7] + offered, 20, retained=7)
    assert selected == records[:7] and [r["reserve_reason"] for r in reserve] == ["author quota"] * 2

def test_a_low_crop_share_above_ten_percent_holds_approval(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records:
        image["vertical_crop_width"] = 1350
    for image in records[:3]:
        image["vertical_crop_width"] = 1181  # 3 of 20 under 1350
    result = module.assess(module.deduplicated(records)[0])
    assert "Automatic photo crop budget no longer meets the resolution floor" in result["holds"]

def test_essential_exception_is_retained_but_does_not_waive_diversity(tmp_path):
    module = _module()
    records = _records(tmp_path, ["Author A"] * 14 + ["Author B"] * 3 + ["Author C"] * 3)
    for image in records[:14]:
        image["presentation"] = "full_image"
    selected, reserve = module.deduplicated(records)
    assert selected == records and not reserve
    assert not module.assess(selected)["ok"]
    selected, reserve = module.fetch_subset(records, 20)
    assert all(image in selected for image in records[:14])
