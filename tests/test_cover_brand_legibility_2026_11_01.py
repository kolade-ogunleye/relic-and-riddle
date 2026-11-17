"""Cover masthead legibility (2026-11-01): RELIC & RIDDLE must clear WCAG AA on any photograph.

Regression: _brand() picked ink or bone from the mean luminance behind it with the halo disabled, so on
mid-tone or busy photographs (sandstone against sky, stone texture) neither colour was readable.
"""

from collections.abc import Callable

import numpy as np
import pytest
from PIL import Image

from src import brand
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


@pytest.mark.parametrize("centered", [False, True], ids=["left", "centered"])
@pytest.mark.parametrize("ground", list(GROUNDS), ids=list(GROUNDS))
def test_cover_brand_meets_aa_on_any_photograph(ground: str, centered: bool) -> None:
    lay = _lay()
    out = _brand(GROUNDS[ground](), lay, centered=centered)
    plate = lay.meta["plates"]["brand"][0]
    assert not plate.empty
    assert plate.fill == brand.BONE
    delivered = plate.measure(out.convert("RGB"))
    assert delivered is not None and delivered >= 4.5, f"{ground}: {delivered:.2f}:1"
    assert out.size == (W, H)
    assert lay.elements[-1]["name"] == "brand"


def test_cover_brand_on_paper_stays_ink_without_halo() -> None:
    lay = _lay()
    _brand(_flat(brand.BONE), lay, paper=True)
    plate = lay.meta["plates"]["brand"][0]
    assert plate.fill == brand.INK and plate.halo == 0.0


@pytest.mark.parametrize("centered", [False, True], ids=["left", "centered"])
def test_brand_mark_sits_before_the_name_and_group_is_placed(centered: bool) -> None:
    lay = _lay()
    out = _brand(_flat((120, 140, 160)), lay, centered=centered).convert("RGB")
    boxes = {e["name"]: e["box"] for e in lay.elements}
    mark, name = boxes["brand_mark"], boxes["brand"]
    assert mark[2] < name[0]                                   # the diamond, then a gap, then the name
    assert out.getpixel(((mark[0] + mark[2]) // 2, (mark[1] + mark[3]) // 2)) == brand.GOLD
    assert mark[1] < (name[1] + name[3]) / 2 < mark[3]         # on the name's centre line
    # One place on every style since the 2026-11-02 design review: the mark-and-name group is centred.
    assert abs((mark[0] + name[2]) / 2 - W / 2) <= 6
