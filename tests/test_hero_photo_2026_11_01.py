"""The hero photograph (2026-11-01): a run's cover, Stories and graphics are built on its strongest still."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from src import hero_photo


def _vivid(path: Path, seed: int = 1, size=(1200, 1800)) -> Path:
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(base).resize(size, Image.Resampling.NEAREST).save(path, quality=95)
    return path


def _flat(path: Path, size=(700, 900)) -> Path:
    arr = np.full((size[1], size[0], 3), 128, dtype=np.uint8)
    arr[::40] = 140                                       # a dull grey wall with faint courses
    Image.fromarray(arr).save(path, quality=95)
    return path


def test_a_vivid_sharp_photo_outscores_a_flat_grey_one(tmp_path: Path) -> None:
    assert hero_photo.score(_vivid(tmp_path / "a.jpg")) > hero_photo.score(_flat(tmp_path / "b.jpg")) + 0.3


def test_blur_lowers_sharpness(tmp_path: Path) -> None:
    sharp = _vivid(tmp_path / "s.jpg")
    soft = tmp_path / "soft.jpg"
    Image.open(sharp).filter(ImageFilter.GaussianBlur(4)).save(soft, quality=95)
    assert hero_photo.measures(soft)["sharpness"] < hero_photo.measures(sharp)["sharpness"] - 0.4


def test_the_first_still_is_replaced_only_by_a_clearly_stronger_one(tmp_path: Path) -> None:
    weak, strong = _flat(tmp_path / "01_commons.jpg"), _vivid(tmp_path / "02_commons.jpg")
    assert hero_photo.pick([weak, strong]) == strong
    similar = _vivid(tmp_path / "03_commons.jpg", seed=2)
    assert hero_photo.pick([similar, strong]) == similar          # within the margin: the storyboard's order


def test_only_strong_photos_compete_and_videos_are_skipped(tmp_path: Path) -> None:
    weak, context = _flat(tmp_path / "01_commons.jpg"), _vivid(tmp_path / "09_commons.jpg")
    clip = tmp_path / "00_hook.mp4"
    clip.write_bytes(b"")
    assert hero_photo.pick([clip, weak, context], strong_stems={"01_commons"}) == weak
    assert hero_photo.pick([clip, weak, context], strong_stems=None) == context


def test_an_unreadable_photo_never_stops_the_run(tmp_path: Path) -> None:
    bad = tmp_path / "01_commons.jpg"
    bad.write_bytes(b"not a picture")
    good = _vivid(tmp_path / "02_commons.jpg")
    assert hero_photo.pick([bad, good]) == bad                    # the first stays when it cannot be compared


def test_the_visual_engine_reads_the_topic_folders_strong_photos(tmp_path: Path) -> None:
    import json

    from src.visual_engine import VisualEngine
    weak = _flat(tmp_path / "01_commons.jpg")
    topic_shot, context_shot = _vivid(tmp_path / "02_commons.jpg"), _vivid(tmp_path / "03_commons.jpg", seed=5)
    verdict = {"usable": True, "safe": True}
    (tmp_path / "credits.json").write_text(json.dumps({"reviewed": True, "images": [
        {"file": "01_commons.jpg", "vision_review": {**verdict, "shows_topic": "yes"}},
        {"file": "02_commons.jpg", "vision_review": {**verdict, "shows_topic": "yes"}},
        {"file": "03_commons.jpg", "vision_review": {**verdict, "shows_topic": "partly"}},
    ]}), encoding="utf-8")
    assert VisualEngine.hero_image([weak, context_shot, topic_shot]) == topic_shot   # a context shot never wins
