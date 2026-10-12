import numpy as np
import pytest

from src import parallax


def test_presets_loop_seam_and_variety():
    names = [parallax.preset_for(i, 8) for i in range(8)]
    assert names[0] == names[-1] == "push"
    assert len(set(names[1:-1])) >= 3


def test_unavailable_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(parallax, "available", lambda: False)
    assert parallax.render_scenes([tmp_path / "a.jpg"], [3.0], tmp_path, 0.4) is None


@pytest.mark.skipif(not parallax.available(), reason="depth model not installed")
def test_depth_map_is_normalised_and_image_sized():
    img = np.zeros((300, 200, 3), np.uint8)
    img[:, :100] = 200  # two flat regions
    d = parallax.depth_map(img)
    assert d.shape == (300, 200) and 0.0 <= d.min() and d.max() <= 1.0
