"""QF-53b: the diversity checks hold on the real pools (real hashes, real attribution, legacy records)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import photo_pool

EVIDENCE = Path(__file__).parents[1] / "docs/reviews/quality-fixes-2026-11/QF-53b"
REAL_PAIRS = json.loads((EVIDENCE / "real_pairs.json").read_text(encoding="utf-8"))["pairs"]
RULE = 15  # photo_pool.DUPLICATE_DISTANCE, asserted below


def _record(pair: dict, side: str) -> dict:
    return {"file": pair[side], "phash": pair[f"{side}_phash"], "dhash": pair[f"{side}_dhash"]}


def _name(pair: dict) -> str:
    return f'{pair["env"]} {pair["topic"]} {pair["a"]}/{pair["b"]}'


@pytest.mark.parametrize("pair", [p for p in REAL_PAIRS if p["label"] == "distinct"], ids=_name)
def test_different_real_objects_are_not_duplicates(pair):
    # The Met swords, the Lycurgus cup lit and unlit, different Voynich folios (labelled from the pool files).
    assert photo_pool.duplicate_of(_record(pair, "b"), [_record(pair, "a")]) is None


@pytest.mark.parametrize("pair", [p for p in REAL_PAIRS if p["label"] == "duplicate"
                                  and p["phash_bits"] + p["dhash_bits"] <= RULE], ids=_name)
def test_real_re_edits_and_identical_uploads_are_duplicates(pair):
    # The Derinkuyu "Enhancer" re-edits, the identical operator uploads, one satellite scene recoloured.
    a = _record(pair, "a")
    assert photo_pool.duplicate_of(_record(pair, "b"), [a]) is a


def test_every_labelled_real_duplicate_beyond_the_rule_is_a_recorded_hash_limit():
    assert photo_pool.DUPLICATE_DISTANCE == RULE
    beyond = [p for p in REAL_PAIRS if p["label"] == "duplicate"
              and p["phash_bits"] + p["dhash_bits"] > photo_pool.DUPLICATE_DISTANCE]
    assert all("hash limit" in p["note"] for p in beyond)
    closest_distinct = min(p["phash_bits"] + p["dhash_bits"] for p in REAL_PAIRS if p["label"] == "distinct")
    assert closest_distinct > photo_pool.DUPLICATE_DISTANCE


@pytest.mark.parametrize("artist", ["Unknown", "Unknown author Unknown author", "unknown",
                                    "Anonymous, Italian, 16th century", "From the Middle Ages, unknown", "", None])
def test_real_unnamed_attribution_is_unverified(artist):
    # Real strings from the pools: trepanation has 15/15 "Unknown", the Voynich manuscript 16 "Unknown author".
    records = [{"file": f"{i}.jpg", "artist": artist, "shot_class": ("wide", "interior", "detail")[i % 3]}
               for i in range(6)]
    result = photo_pool.assess(records)
    assert result["unverified_authors"] == 6
    assert "An author supplies more than 35% of the pool" not in result["problems"]


def test_a_named_institution_still_counts_as_one_author():
    records = [{"file": f"{i}.jpg", "artist": "The Metropolitan Museum of Art", "shot_class": "detail"}
               for i in range(9)]
    assert photo_pool.assess(records)["max_author_share"] == 1.0


def _textured(path: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)).resize((256, 256)).save(path)


def test_two_photographer_pool_keeps_all_its_photos_with_a_notice(tmp_path):
    # Amber Room: 7 + 7 photos by two photographers, 4 by others. Round 2 trimmed it to 12; operator decision
    # QF-53b-author-cap keeps all 18 and reports the share.
    authors = ["Sunny"] * 7 + ["Demidov"] * 7 + ["Decou", "Decou", "Giggel", ""]
    records = []
    for i, author in enumerate(authors):
        _textured(tmp_path / f"{i}.jpg", 530 + i)
        records.append({"file": f"{i}.jpg", "artist": author,
                        "shot_class": "interior" if i in (3, 11) else "unknown",
                        **photo_pool.signature(tmp_path / f"{i}.jpg")})
    selected, reserves = photo_pool.deduplicated(records)
    result = photo_pool.assess(selected)
    assert len(selected) == 18 and not reserves
    assert "An author supplies more than 35% of the pool" in result["problems"] and not result["holds"]

def test_legacy_records_without_crop_width_do_not_fail_a_new_fetch():
    legacy = [{"file": f"{i}.jpg", "width": 4000, "height": 3000} for i in range(9)]
    assert photo_pool.crop_budget([*legacy, {"file": "new.jpg", "vertical_crop_width": 1400}])
    assert not photo_pool.crop_budget([*legacy, {"file": "new.jpg", "vertical_crop_width": 1000}])


def test_flat_frames_are_duplicates_only_when_identical(tmp_path):
    red, blue, red2 = tmp_path / "red.png", tmp_path / "blue.png", tmp_path / "red2.png"
    Image.new("RGB", (64, 48), (200, 0, 0)).save(red)
    Image.new("RGB", (64, 48), (0, 0, 200)).save(blue)
    Image.new("RGB", (64, 48), (200, 0, 0)).save(red2)
    r, b, r2 = ({"file": p.name, **photo_pool.signature(p)} for p in (red, blue, red2))
    assert r["hash_flat"] and b["hash_flat"]
    # No structure: the dHash is all zeros and the pHash is rounding noise, so neither says what the frame shows.
    assert r["dhash"] == b["dhash"] == "0000000000000000"
    assert photo_pool.duplicate_of(b, [r]) is None
    assert photo_pool.duplicate_of(r2, [r]) is r


def _pool(topic_dir: Path, fingerprint: str) -> None:
    topic_dir.mkdir()
    images = []
    for i in range(6):
        _textured(topic_dir / f"{i:02d}_commons.jpg", 600 + i)
        images.append({"file": f"{i:02d}_commons.jpg", "artist": f"Author {i}", "title": f"File:{i}.jpg",
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "kept": True,
                                                         "quality": 3, "depiction": "original"}})
    (topic_dir / "credits.json").write_text(json.dumps(
        {"images": images, "reviewed": True, "reviewed_by": "vision", "review_fingerprint": fingerprint}),
        encoding="utf-8")


def test_a_legacy_comma_fingerprint_is_not_sent_back_to_the_model(tmp_path, monkeypatch):
    from src import image_review
    topic_dir = tmp_path / "t"
    _pool(topic_dir, ",".join(f"{i:02d}_commons.jpg" for i in range(6)))

    def no_call(*args, **kwargs):
        raise AssertionError("an unchanged pool must not be sent to the vision model")
    monkeypatch.setattr(image_review, "_verdicts", no_call)
    result = image_review.review_pool({"id": "t", "title": "t"}, topic_dir)
    assert "unchanged since last review" in result["status"]


def test_a_legacy_fingerprint_with_a_changed_file_list_is_reviewed(tmp_path, monkeypatch):
    from src import image_review
    topic_dir = tmp_path / "t"
    _pool(topic_dir, "00_commons.jpg,01_commons.jpg")
    calls = []
    monkeypatch.setattr(image_review, "_verdicts", lambda *a, **k: calls.append(1))
    image_review.review_pool({"id": "t", "title": "t"}, topic_dir)
    assert calls


def test_operator_approval_stands_with_a_diversity_notice(tmp_path):
    from src.credits import approve_set, load_credits
    topic_dir = tmp_path / "t"
    _pool(topic_dir, "")
    credits = json.loads((topic_dir / "credits.json").read_text(encoding="utf-8"))
    for image in credits["images"]:
        image["artist"] = "One Photographer"
    (topic_dir / "credits.json").write_text(json.dumps(credits), encoding="utf-8")
    message = approve_set(topic_dir, 5)
    after = load_credits(topic_dir)
    assert after["reviewed"] is True and after["reviewed_by"] == "operator"
    assert "Diversity notice" in message and "35%" in message


def test_the_fetchers_refill_on_the_real_pools_moves_only_their_near_duplicates(monkeypatch, tmp_path):
    # Review 3/4: replays the fetcher's own refill (fetch_topic) on copies of every real pool, every search answering
    # nothing, with each photo's recorded signature. Only labelled near duplicates may leave; a quota never moves a
    # pool's own photo (operator decision QF-53b-author-cap).
    import shutil

    from scripts import fetch_commons_images as fetch
    from src.credits import load_credits
    signatures = json.loads((EVIDENCE / "real_signatures.json").read_text(encoding="utf-8"))["signatures"]
    specs = json.loads((Path(__file__).parents[1] / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    missing = []

    def recorded(path):
        key = f"{Path(path).parent.name}/{Path(path).name}"
        if key not in signatures:
            missing.append(key)
            raise FileNotFoundError(key)
        return dict(signatures[key])

    monkeypatch.setattr(photo_pool, "signature", recorded)
    for name in ("search", "search_graded", "find_categories", "search_openverse"):
        monkeypatch.setattr(fetch, name, lambda *a, **k: [])
    monkeypatch.setattr("src.museum_sources.search_all", lambda *a, **k: [])
    monkeypatch.setattr("src.storyboard.generate_storyboard", lambda *a, **k: None)
    monkeypatch.setattr("src.image_presentation.full_image", lambda *a, **k: True)  # placeholders hold no pixels
    moved = []
    for credits in sorted((Path(__file__).parents[1] / "assets").glob("*/credits.json")):
        topic = credits.parent.name
        if topic not in specs:
            continue
        pool = tmp_path / topic
        pool.mkdir()
        shutil.copy2(credits, pool / "credits.json")
        for image in json.loads(credits.read_text(encoding="utf-8")).get("images", []):
            (pool / image["file"]).write_bytes(b"")  # load_credits keeps only records whose file exists
        before = {image["file"] for image in load_credits(pool).get("images", [])}
        fetch.fetch_topic({"id": topic, "title": topic}, specs[topic], False, assets_dir=tmp_path)
        after = load_credits(pool)
        kept = after.get("images", [])
        assert not [image["file"] for image in kept if image.get("hash_error")], topic
        for image in after.get("diversity_reserve", []):
            if image["file"] in before - {k["file"] for k in kept}:
                assert image["reserve_reason"] == "near duplicate", (topic, image["file"], image["reserve_reason"])
                assert photo_pool.duplicate_of(image, kept) is not None, (topic, image["file"])
                moved.append(f'{topic}/{image["file"]}')
        assert len(kept) + len([m for m in moved if m.startswith(topic + "/")]) == len(before), topic
    assert not missing, missing
    assert len(moved) == 6, moved  # measurements_qf53b.json: refill_moved_to_reserve
