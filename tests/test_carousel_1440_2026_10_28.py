"""Carousel slides at 1440x1800 (operator, 2026-10-28: Meta's recommended 4:5 feed size and the widest the Graph
API takes). Same 4:5 layout, drawn at 4/3 the resolution: every carousel pixel value is a 1080-wide design
value scaled by _px, so a 1080 canvas still draws the slides exactly as before (the boxes the renderer recorded
before the change are kept in tests/fixtures), a 1440 canvas draws the same layout 4/3 larger, Instagram's
counter region scales with it, every archetype passes its layout check, and a slide stays far under Meta's
8 MB image limit."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import config, still_layout
from src.multi_format import MultiFormatGenerator
from src.video_qa import QualityAuditor

FIXTURE = Path(__file__).parent / "fixtures" / "carousel_1080_layouts_2026_10_28.json"
ARCHETYPES = ("cover", "immersive", "comparison", "blueprint", "colossal_stat", "debate", "editorial_split")
# The cards and photos the fixture was recorded with (the renderer before the change, at 1080x1350).
CARDS = [
    {"heading": "The calendar that outlived its empire", "body": "A shoebox of bronze tracked the Sun and Moon."},
    {"heading": "Çatalhöyük under Kraków", "body": "Divers raised 82 fragments in 1901, and nobody knew what they were for decades."},
    {"heading": "Then and now: the harbour compared", "body": "Unlike the modern port, the Roman harbour faced the open sea and survived it."},
    {"heading": "A gear train of 30 meshing wheels", "body": "The tunnel and shaft structure was engineered to channel the water through the block."},
    {"heading": "It weighed 1,200 tons", "body": "About 20,000 people lived in levels 85 meters deep, with ventilation and rolling stone doors."},
    {"heading": "An extraordinarily long heading that will not fit on a single line at all",
     "body": " ".join(["The body keeps going with more words than the panel can hold."] * 5)},
    {"heading": "Who built it?", "body": "Was it astronomy or metalwork that made it possible? Tell us what you think."},
]
SHARE = "Send this to a friend who loves gears, and follow for the next relic."
POLL = {"question": "Astronomy or metalwork?", "options": ["The astronomy", "The metalwork"]}
EIGHT_MB = 8 * 1024 * 1024


def _photos(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(3):
        p = folder / f"p{i}.jpg"
        im = Image.new("RGB", (2000, 2500), (200 - 40 * i, 190, 170))
        ImageDraw.Draw(im).rectangle([200, 300, 1700, 1100], fill=(250, 250, 245))
        im.save(p, quality=95)
        out.append(p)
    return out


def _noise(path: Path, size=(3000, 3750)):
    """The hardest photo to compress: full-range noise."""
    rng = np.random.default_rng(7)
    Image.fromarray((rng.random((size[1], size[0], 3)) * 255).astype("uint8")).save(path, quality=98)
    return path


@pytest.fixture
def gen(monkeypatch):
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos",
                        lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))])
    return MultiFormatGenerator()


def test_slides_are_1440x1800_on_both_copies_and_pass_every_check(gen, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STILL_VISION_CHECK", False)               # the measured checks only
    assert MultiFormatGenerator.CAROUSEL_SIZE == (1440, 1800)
    photos = _photos(tmp_path / "photos")
    payload = {"title": "T", "poll": POLL, "carousel_slides": CARDS}
    for facebook in (False, True):
        slides = gen.generate_carousel_sequence(photos, payload, tmp_path, facebook=facebook)
        records = json.loads((slides[0].parent / still_layout.SIDECAR).read_text(encoding="utf-8"))
        for slide in slides:
            assert Image.open(slide).size == (1440, 1800)
            assert records[slide.name]["size"] == [1440, 1800]
            assert records[slide.name]["problems"] == [], (facebook, slide.name, records[slide.name]["problems"])
            assert slide.stat().st_size < EIGHT_MB / 4
    audit = QualityAuditor().audit_carousel_slides(tmp_path)
    assert not [i for i in audit["issues"] if i["type"] == "non_standard_aspect_ratio"], audit["issues"]


def test_every_archetype_normal_and_tight_both_copies_lays_out_cleanly_at_1440(gen, tmp_path):
    photos = _photos(tmp_path / "photos")
    cw, chh = gen.CAROUSEL_SIZE
    counter = gen._ig_counter(cw)
    assert counter == (1440 - 373, 0, 1440, 187)                               # 280 x 140 design px, scaled
    # The accented heading and the overlong card, normal and tight; the Facebook copy (which only rewords
    # "swipe") for the first of them.
    runs = [(ci, tight, False) for ci in (1, 5) for tight in (False, True)] + [(1, tight, True) for tight in (False, True)]
    for card_index, tight, facebook in runs:
        for archetype in ARCHETYPES:
            _, lay = gen._render_carousel_slide(archetype, photos[0], photos[1], CARDS[card_index], card_index, 7,
                                                POLL, SHARE, facebook, tight=tight)
            rec = lay.to_dict()
            assert rec["size"] == [1440, 1800] and rec["zone"] == [53, 40, 1387, 1760]
            assert lay.problems() == [], (card_index, archetype, tight, facebook, lay.problems())
            assert rec["protected"] == [{"name": "instagram_counter", "box": list(counter)}]
            for e in lay.elements:
                assert not still_layout.intersects(tuple(e["box"]), counter), (archetype, e)
            brand = next(e["box"] for e in lay.elements if e["name"] == "header_brand")
            assert brand[0] < cw // 2 and brand[3] <= (chh - cw) // 2     # out whole in a square grid crop


def test_a_1080_canvas_draws_every_recorded_case_cleanly(gen, tmp_path, monkeypatch):
    """S = 1: every case of the fixture (recorded from the pre-1440 renderer) still lays out without a problem on
    the 1080 canvas. Until the visual overhaul (2026-10-29) this compared the boxes exactly; the overhaul's
    bottom-aligned stacks move every box, so the cases are kept and the exact boxes retired."""
    monkeypatch.setattr(MultiFormatGenerator, "CAROUSEL_SIZE", (1080, 1350))
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    photos = _photos(tmp_path / "photos")
    seen = 0
    for key in expected:
        ci, archetype, fit, _ = key.split("/")
        ci = int(ci)
        _, lay = gen._render_carousel_slide(archetype, photos[ci % 3], photos[(ci + 1) % 3], CARDS[ci], ci, 7, POLL, SHARE,
                                            False, tight=fit == "tight")
        rec = lay.to_dict()
        assert rec["size"] == [1080, 1350] and lay.problems() == [], (key, lay.problems())
        seen += 1
    assert seen == 28


def test_the_1440_layout_is_the_1080_layout_at_four_thirds(gen, tmp_path, monkeypatch):
    """Same look, only sharper: every recorded box at 1440 is its 1080 box x 4/3. The lines and sizes are the
    1080 slide's (decided on the design canvas); only the fonts' hinting differs, so a line's length may differ
    by about 1% (FreeType rounds each glyph's advance to whole pixels at either size)."""
    photos = _photos(tmp_path / "photos")
    boxes = {}
    for size in ((1080, 1350), (1440, 1800)):
        monkeypatch.setattr(MultiFormatGenerator, "CAROUSEL_SIZE", size)
        for archetype in ARCHETYPES:
            _, lay = gen._render_carousel_slide(archetype, photos[0], photos[1], CARDS[0], 0, 7, POLL, SHARE, False)
            boxes[(size[0], archetype)] = {e["name"]: e["box"] for e in lay.elements}
    for archetype in ARCHETYPES:
        small, large = boxes[(1080, archetype)], boxes[(1440, archetype)]
        assert small.keys() == large.keys(), archetype
        for name, box in small.items():
            scaled = [v * 4 / 3 for v in box]
            dx = max(abs(scaled[i] - large[name][i]) for i in (0, 2))
            dy = max(abs(scaled[i] - large[name][i]) for i in (1, 3))
            assert dx <= 0.015 * 1440 and dy <= 6, (archetype, name, box, large[name])


def test_a_worst_case_slide_stays_far_under_metas_8_mb(gen, tmp_path):
    photo = _noise(tmp_path / "noise.jpg")
    payload = {"title": "T", "poll": POLL, "carousel_slides": CARDS}
    slides = gen.generate_carousel_sequence([photo], payload, tmp_path)
    sizes = [s.stat().st_size for s in slides]
    assert all(Image.open(s).size == (1440, 1800) for s in slides)
    assert max(sizes) < EIGHT_MB / 2, sizes                                     # full-range noise, full bleed


def test_the_audit_accepts_1440_slides_and_still_flags_odd_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STILL_VISION_CHECK", False)
    folder = tmp_path / "carousel"
    folder.mkdir()
    rng = np.random.default_rng(3)
    for i, size in enumerate(((1440, 1800), (1200, 1500))):
        arr = (rng.random((size[1], size[0], 3)) * 60 + 40).astype("uint8")
        arr[:, :40] = arr[:, -40:] = 30                                         # quiet edges: no margin finding
        Image.fromarray(arr).save(folder / f"carousel_slide_0{i + 1}.jpg", quality=92)
    issues = QualityAuditor().audit_carousel_slides(tmp_path)["issues"]
    odd = [i["slide"] for i in issues if i["type"] == "non_standard_aspect_ratio"]
    assert odd == ["carousel_slide_02.jpg"]
