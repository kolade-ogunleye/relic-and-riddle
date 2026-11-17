"""Cover masthead (2026-11-01; operator ruling 2, 2026-11-16: the Reel cover carries no masthead).

Regression: _brand() picked ink or bone from the mean luminance behind it with the halo disabled, so on
mid-tone or busy photographs (sandstone against sky, stone texture) neither colour was readable.
"""

from collections.abc import Callable

import numpy as np
import pytest
from PIL import Image

from src.cover_design import _brand
from src.still_layout import Layout

W, H = 1080, 1920


def _flat(rgb) -> Image.Image:
    return Image.new("RGB", (W, H), rgb)


def _lay() -> Layout:
    return Layout("cover", (W, H), (0, 0, W, H))


def _busy() -> Image.Image:
    rng = np.random.default_rng(7)
    return Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8))


def _stripes() -> Image.Image:
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    arr[:, ::2] = 255  # alternating 1px black/white columns: mean ~ 128, the old threshold's blind spot
    return Image.fromarray(arr)


GROUNDS: dict[str, Callable[[], Image.Image]] = {
    "white": lambda: _flat((255, 255, 255)),
    "sand": lambda: _flat((214, 186, 140)),
    "mid_grey_at_old_threshold": lambda: _flat((135, 135, 135)),
    "sky_blue": lambda: _flat((120, 170, 220)),
    "black": lambda: _flat((0, 0, 0)),
    "noise": _busy,
    "stripes": _stripes,
}


@pytest.mark.parametrize("paper", [False, True], ids=["photo", "paper"])
@pytest.mark.parametrize("ground", list(GROUNDS), ids=list(GROUNDS))
def test_the_cover_draws_no_masthead_on_any_ground(ground: str, paper: bool) -> None:
    """Operator ruling 2 (2026-11-16): no masthead on the Reel cover; cycle 0 round 7 removed the cover's masthead
    path (unreached since QF-87; its own solver aimed at 4.8 with a halo up to 1.0 and met the system's 5.5 masthead
    gate only on black). Every ground comes back untouched, the record says why, nothing named brand is laid out.
    (Was: the cover masthead met AA on any photograph, inked on paper, with the diamond before the name.)"""
    lay = _lay()
    im = GROUNDS[ground]()
    out = _brand(im, lay, paper=paper, grid_delivery=False)
    assert out is im and lay.meta["masthead"]["shown"] is False and "ruling 2" in lay.meta["masthead"]["reason"]
    assert not any(e["name"] in ("brand", "brand_mark") for e in lay.elements) and "plates" not in lay.meta
