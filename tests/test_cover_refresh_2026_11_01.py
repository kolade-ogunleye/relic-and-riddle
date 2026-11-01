"""Cover refresh: measure readable text on the saved JPEG, including repair renders.

These checks use generated grounds and local fonts; they make no provider calls.
"""
import numpy as np
import pytest
from PIL import Image

from src import brand, still_layout
from src.visual_engine import COVER_STYLES, VisualEngine


PAYLOAD = {
    "thumbnail": {
        "eyebrow": "CAPPADOCIA, TURKEY",
        "line1": "280 FEET DOWN",
        "line2": "BEHIND A WALL",
        "tagline": "ROOM FOR 20,000 PEOPLE",
    },
}
COPY_FIELDS = {"eyebrow", "title_1", "title_2", "tagline"}


def _hero(tmp_path, ground):
    if ground == "textured":
        rng = np.random.default_rng(17)
        yy, xx = np.indices((1920, 1080))
        checker = ((xx // 32 + yy // 32) % 2) * 150 + 65
        pixels = checker[..., None] + rng.normal(0, 18, (1920, 1080, 3))
        im = Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))
    else:
        im = Image.new("RGB", (1080, 1920), (248, 245, 236) if ground == "bright" else (18, 20, 24))
    path = tmp_path / "hero.jpg"
    im.save(path, quality=95)
    return path


def _assert_delivered_text(path, layout):
    """Re-measure the saved pixels using the actual glyph masks, not planned ratios."""
    entry = still_layout.load(path)
    assert entry is not None
    assert not still_layout.problems(entry)
    elements = {e["name"]: e for e in entry["elements"]}
    assert COPY_FIELDS <= elements.keys()
    plates = layout.meta["plates"]
    assert COPY_FIELDS <= plates.keys()
    with Image.open(path) as jpeg:
        for name, text_plates in plates.items():
            values = [p.measure(jpeg) for p in text_plates if not p.empty]
            assert values and all(v is not None and v >= brand.TEXT_CONTRAST for v in values), (name, values)
            assert elements[name]["contrast"] == pytest.approx(min(values), abs=0.001)
            assert "contrast_planned" in elements[name]
    return entry


def _assert_profile_crop(cover, grid):
    with Image.open(cover) as portrait, Image.open(grid) as square:
        assert portrait.size == (1080, 1920) and square.size == (1080, 1080)
        # Cropping at y=420 shifts JPEG's 8px blocks by four pixels. Compare at
        # phone size so high-frequency compression noise is not mistaken for a
        # changed composition. The crop coordinates are independent of the renderer.
        reference = portrait.crop((0, 420, 1080, 1500)).resize((360, 360), Image.Resampling.LANCZOS)
        actual = square.resize((360, 360), Image.Resampling.LANCZOS)
        assert np.abs(np.asarray(reference, dtype=float) - np.asarray(actual, dtype=float)).mean() < 1.25


@pytest.mark.parametrize("ground", ["bright", "dark", "textured"])
@pytest.mark.parametrize("style", COVER_STYLES)
def test_cover_delivered_contrast_and_profile_crop(tmp_path, monkeypatch, ground, style):
    engine = VisualEngine()
    real_render = engine._render_checked
    layouts = []

    def capture(*args, **kwargs):
        result = real_render(*args, **kwargs)
        layouts.append(result[1])
        return result

    monkeypatch.setattr(engine, "_render_checked", capture)
    cover, grid = engine.generate_thumbnails(_hero(tmp_path, ground), PAYLOAD, tmp_path / "out",
                                             style=style, variants=False)
    entry = _assert_delivered_text(cover, layouts[-1])
    assert entry["recipe"]["style"] == style
    assert not entry["shortened"]
    _assert_profile_crop(cover, grid)


@pytest.mark.parametrize("style", COVER_STYLES)
def test_cover_redraw_keeps_delivered_contrast_metadata(tmp_path, monkeypatch, style):
    method = "_cover_" + style
    original = getattr(VisualEngine, method)
    layouts = []

    def capture(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        layouts.append(kwargs["lay"])
        return result

    monkeypatch.setattr(VisualEngine, method, capture)
    cover, grid = VisualEngine().generate_thumbnails(_hero(tmp_path, "bright"), PAYLOAD, tmp_path / "out",
                                                    style=style, variants=False)
    before = still_layout.load(cover)
    entry = still_layout.redraw(cover)
    assert entry is not None and entry["tight"]
    assert entry["attempts"] == before["attempts"] + 1
    _assert_delivered_text(cover, layouts[-1])
    _assert_profile_crop(cover, grid)
