"""Provider caption strips printed along a photo's edge (2026-11-01).

Richat's "Copernicus Sentinel data [2023], processed by Sentinel Hub/Pierre Markuse" bar went out on two
Stories: the vision screen did not run for it. photo_hygiene finds such a strip from the pixels alone.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src import beat_images
from src.photo_hygiene import (
    caption_strip,
    clean_hero,
    remap_focus,
    without_caption_strip,
)
from src.typography import get_font

W, H = 1200, 900


def _photo(seed: int = 1) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = rng.normal(110, 35, (H // 10, W // 10, 3)).clip(0, 255).astype(np.uint8)
    return Image.fromarray(base).resize((W, H), Image.Resampling.BICUBIC)


def _with_bar(im: Image.Image, edge: str = "top", colour=(170, 180, 8), depth: int = 40) -> Image.Image:
    im = im.copy()
    d = ImageDraw.Draw(im)
    y0 = 0 if edge == "top" else H - depth
    d.rectangle((0, y0, W, y0 + depth), fill=colour)
    d.text((8, y0 + depth // 2), "Copernicus Sentinel data [2023], processed by Sentinel Hub/Pierre Markuse",
           font=get_font("Montserrat", int(depth * 0.6)), fill=(20, 20, 10), anchor="lm")
    return im


def test_top_and_bottom_caption_bars_are_found() -> None:
    top = caption_strip(_with_bar(_photo()))
    bottom = caption_strip(_with_bar(_photo(), "bottom", (240, 240, 240)))
    assert top is not None and top[0] == "top" and 40 <= top[1] <= 56
    assert bottom is not None and bottom[0] == "bottom" and 40 <= bottom[1] <= 56


def test_the_strip_is_cropped_and_nothing_else() -> None:
    clean, box = without_caption_strip(_with_bar(_photo()))
    assert box[1] >= 40 and clean.size == (W, H - box[1])
    assert caption_strip(clean) is None


def test_photographs_without_a_bar_are_left_alone() -> None:
    sky = np.zeros((H, W, 3), dtype=np.uint8)
    sky[:] = np.linspace(200, 120, H, dtype=np.uint8)[:, None, None] * np.array([0.5, 0.75, 1.0])
    sky[int(H * 0.7):] = (90, 70, 40)                     # clear sky over a hard horizon
    ceiling = np.asarray(_photo(4)).copy()
    ceiling[:60] = 12                                     # a dark vault ...
    ceiling[20:30, 100:140] = 250                         # ... with a lamp, fading into the room
    ceiling[60:90] = np.linspace(12, 110, 30, dtype=np.uint8)[:, None, None]
    flat_band = np.asarray(_photo(5)).copy()
    flat_band[:40] = (30, 30, 30)                         # a letterbox bar with no marks
    for arr in (sky, ceiling, flat_band):
        assert caption_strip(Image.fromarray(arr)) is None
    assert caption_strip(_photo(7)) is None


def test_reviewed_focus_follows_the_crop() -> None:
    focus = {"focus_box": [0.25, 0.2, 0.75, 0.8], "sha256": "s", "reviewed": "r"}
    clean, moved = clean_hero(_with_bar(_photo()).convert("RGBA"), focus)
    k = H - clean.size[1]
    assert moved["focus_box"][1] == (0.2 * H - k) / (H - k)
    assert moved["caption_strip_box"] == [0, k, W, H]
    assert remap_focus([0, 0, 1, 0.01], (0, 100, W, H), (W, H)) is None
    same, kept = clean_hero(_photo().convert("RGBA"), focus)
    assert kept is focus and same.size == (W, H)


def test_scene_pool_gets_a_cropped_copy_with_lineage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(beat_images.config, "PICTURE_VISION_CHECK", False)
    barred, plain = tmp_path / "a.jpg", tmp_path / "b.jpg"
    _with_bar(_photo()).save(barred, quality=95)
    _photo(2).save(plain, quality=95)
    out = beat_images.without_burned_in_text([barred, plain], "subject", minimum=1)
    assert out[1] == plain
    assert out[0] != barred and out[0].name == barred.name and out[0].is_file()
    with Image.open(out[0]) as im:
        assert im.size[1] < H and caption_strip(im) is None
