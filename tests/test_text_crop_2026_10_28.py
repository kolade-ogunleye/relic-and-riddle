"""
The burned-in-text screen, 2026-10-28: text in an edge band (a museum label, a logo strip, a scale bar) is
cropped away instead of the photo being dropped; the verdict names the text, not the subject; the screen looks
at 1024 px. Production Richat x3 and Hal Saflieni, dev Trepanation ("Museum accession/catalog number label is
visible at the bottom of the frame"). Evidence: docs/reviews/text-crop-2026-10-28/.
"""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from src import beat_images, config
from src.config import BASE_DIR


@pytest.fixture(autouse=True)
def crop_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)


def _photo(path: Path, size=(1600, 2400), label=True, seed=0) -> Path:
    im = Image.new("RGB", size, (200 - seed * 10, 190, 170))
    d = ImageDraw.Draw(im)
    d.ellipse((300, 300, 1300, 1700), fill=(140, 120, 90))
    if label:  # a white accession card near the bottom edge
        w, h = size
        d.rectangle((int(w * 0.33), int(h * 0.88), int(w * 0.6), int(h * 0.94)), fill=(255, 255, 255))
        d.text((int(w * 0.36), int(h * 0.9)), "A650847", fill=(0, 0, 0))
    im.save(path, "JPEG", quality=92)
    return path


def test_the_prompt_names_museum_labels_and_accession_numbers():
    text = beat_images.PICTURE_SYSTEM.lower()
    for word in ("museum label", "placard", "accession", "catalogue number", "text_edge", "text_box"):
        assert word in text


def test_the_reason_logged_is_the_text_not_the_subject(monkeypatch, tmp_path, caplog):
    p = _photo(tmp_path / "x.jpg")
    verdict = beat_images.PictureVerdict(shows_subject=True, burned_in_text=True, reason="A trephined skull from Peru.",
                                         text_reason="an accession number label at the bottom edge",
                                         text_edge="bottom", text_box=[880, 330, 940, 600])
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: verdict)
    assert beat_images.picture_shows(p, "skull") == (False, "burned-in text: an accession number label at the bottom edge")
    look = beat_images.picture_look(p, "skull")
    assert look["text_edge"] == "bottom" and look["text_box"] == [880, 330, 940, 600]


def test_a_malformed_text_box_is_ignored(monkeypatch, tmp_path):
    p = _photo(tmp_path / "x.jpg")
    verdict = beat_images.PictureVerdict(shows_subject=True, burned_in_text=True, reason="r", text_edge="Bottom ",
                                         text_box=[940, 330, 880, 600])
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: verdict)
    look = beat_images.picture_look(p, "q")
    assert look["text_edge"] == "bottom" and look["text_box"] == []


def test_the_screen_looks_at_1024_px(monkeypatch, tmp_path):
    sizes = []

    def block(path, max_side=512):
        sizes.append(max_side)
        return {"type": "text", "text": "image"}
    monkeypatch.setattr("src.llm.image_block", block)
    monkeypatch.setattr("src.free_llm.structured",
                        lambda *a, **k: beat_images.PictureVerdict(shows_subject=True, reason="ok"))
    photos = [_photo(tmp_path / f"{i:02d}_commons.jpg", label=False, seed=i) for i in range(2)]
    beat_images.without_burned_in_text(photos, "subject", minimum=1)
    assert sizes == [1024, 1024]


def test_crop_removes_an_edge_band_and_keeps_its_source(tmp_path):
    src = _photo(tmp_path / "14_commons.jpg")
    out = beat_images.crop_text_band(src, "bottom", [880, 330, 940, 600])
    assert out is not None and out.name == src.name and out.parent != src.parent  # same name: storyboard, stems
    with Image.open(out) as im:
        assert im.size == (1600, round(2400 * (1 - 0.135)))
    from src.lineage import source_chain
    chain = source_chain(out)
    assert [Path(c["path"]).resolve() for c in chain if c.get("kind") != "lineage"] == [src.resolve()]
    record = json.loads(Path(str(out) + ".lineage.json").read_text(encoding="utf-8"))
    assert record["operation"] == "text_crop" and record["settings"]["edge"] == "bottom"
    assert beat_images.crop_text_band(src, "bottom", [880, 330, 940, 600]) == out  # made once


@pytest.mark.parametrize("edge, box, size", [
    ("bottom", [700, 330, 940, 600], (1600, 2400)),   # reaches 30% in: not an edge band
    ("inside", [400, 400, 500, 600], (1600, 2400)),   # in the middle of the picture
    ("none", [], (1600, 2400)),
    ("bottom", [880, 330, 940, 600], (1400, 1150)),   # the crop would fall under the 1000 px height a photo needs
])
def test_crop_refuses(tmp_path, edge, box, size):
    src = _photo(tmp_path / "p.jpg", size=size, label=False)
    assert beat_images.crop_text_band(src, edge, box) is None


@pytest.mark.parametrize("edge, box, expect", [
    ("top", [0, 0, 80, 1000], (1600, 2400 - round(2400 * 0.095))),
    ("left", [0, 0, 1000, 100], (1600 - round(1600 * 0.115), 2400)),
    ("right", [0, 900, 1000, 1000], (1600 - round(1600 * 0.115), 2400)),
])
def test_crop_each_edge(tmp_path, edge, box, expect):
    src = _photo(tmp_path / "p.jpg", label=False)
    with Image.open(beat_images.crop_text_band(src, edge, box)) as im:
        assert im.size == expect


def _looker(monkeypatch, verdicts):
    """picture_look by the photo's height: the original has its label, a crop is shorter."""
    looks = []

    def look(path, query, size=512):
        with Image.open(path) as im:
            h = im.height
        looks.append((Path(path).name, h, size))
        return verdicts(Path(path), h)
    monkeypatch.setattr(beat_images, "picture_look", look)
    return looks


def test_a_photo_with_an_edge_label_is_cropped_not_dropped(monkeypatch, tmp_path, caplog):
    photos = [_photo(tmp_path / f"{i:02d}_commons.jpg", label=(i == 1), seed=i) for i in range(4)]

    def verdicts(path, h):
        if path.name == "01_commons.jpg" and h == 2400:
            return {"checked": True, "shows": True, "burned_in_text": True, "reason": "A trephined skull.",
                    "text_reason": "an accession number label at the bottom", "text_edge": "bottom",
                    "text_box": [880, 330, 940, 600]}
        return {"checked": True, "shows": True, "burned_in_text": False, "reason": "a skull"}
    looks = _looker(monkeypatch, verdicts)
    caplog.set_level("INFO", logger="src.beat_images")
    kept = beat_images.without_burned_in_text(photos, "skull", minimum=2)
    assert [p.name for p in kept] == [p.name for p in photos]          # the order and the names stand
    assert kept[1] != photos[1] and kept[1].parent.parent == beat_images.TEXT_CROP_DIR
    assert kept[0] == photos[0] and kept[2] == photos[2]
    assert ("01_commons.jpg", 2076, 1024) in looks                     # the crop was looked at too
    assert "accession number label" in caplog.text and "does not show the subject" not in caplog.text
    cache = json.loads((tmp_path / beat_images.TEXT_SCREEN_FILE).read_text(encoding="utf-8"))
    entry = next(v for v in cache.values() if v["text"])
    assert entry["reason"] == "an accession number label at the bottom" and entry["screen"] == beat_images.TEXT_SCREEN_VERSION
    looks.clear()
    again = beat_images.without_burned_in_text(photos, "skull", minimum=2)
    assert again == kept and looks == []                                # cached by hash, the crop made once


def test_a_crop_that_still_shows_text_is_set_aside(monkeypatch, tmp_path):
    photos = [_photo(tmp_path / f"{i:02d}_commons.jpg", label=(i == 1), seed=i) for i in range(4)]

    def verdicts(path, h):
        if path.name == "01_commons.jpg":
            return {"checked": True, "shows": True, "burned_in_text": True, "reason": "r", "text_reason": "a label",
                    "text_edge": "bottom", "text_box": [880, 330, 940, 600]}
        return {"checked": True, "shows": True, "burned_in_text": False, "reason": "ok"}
    _looker(monkeypatch, verdicts)
    kept = beat_images.without_burned_in_text(photos, "skull", minimum=2)
    assert [p.name for p in kept] == ["00_commons.jpg", "02_commons.jpg", "03_commons.jpg"]


def test_text_inside_the_picture_is_still_set_aside(monkeypatch, tmp_path):
    photos = [_photo(tmp_path / f"{i:02d}_commons.jpg", label=False, seed=i) for i in range(4)]

    def verdicts(path, h):
        text = path.name == "02_commons.jpg"
        return {"checked": True, "shows": True, "burned_in_text": text, "reason": "r", "text_reason": "a placard",
                "text_edge": "inside" if text else "none", "text_box": [400, 300, 600, 700] if text else []}
    _looker(monkeypatch, verdicts)
    kept = beat_images.without_burned_in_text(photos, "s", minimum=2)
    assert [p.name for p in kept] == ["00_commons.jpg", "01_commons.jpg", "03_commons.jpg"]


def test_an_older_screen_is_looked_at_again_once(monkeypatch, tmp_path):
    from src.credits import sha256_file
    p = _photo(tmp_path / "00_commons.jpg", label=False)
    (tmp_path / beat_images.TEXT_SCREEN_FILE).write_text(json.dumps({sha256_file(p): {"text": False, "reason": "512 px"}}))
    looks = _looker(monkeypatch, lambda path, h: {"checked": True, "shows": True, "burned_in_text": False, "reason": "ok"})
    beat_images.without_burned_in_text([p], "s", minimum=1)
    beat_images.without_burned_in_text([p], "s", minimum=1)
    assert len(looks) == 1


def test_an_older_verdict_stands_when_the_new_look_cannot_run(monkeypatch, tmp_path):
    from src.credits import sha256_file
    photos = [_photo(tmp_path / f"{i:02d}_commons.jpg", label=False, seed=i) for i in range(3)]
    (tmp_path / beat_images.TEXT_SCREEN_FILE).write_text(json.dumps({sha256_file(photos[1]): {"text": True, "reason": "a logo"}}))
    _looker(monkeypatch, lambda path, h: {"checked": False, "shows": False, "burned_in_text": False, "reason": beat_images.UNAVAILABLE})
    kept = beat_images.without_burned_in_text(photos, "s", minimum=1)
    assert kept == [photos[0], photos[2]]


def test_a_cropped_copy_keeps_its_credit_and_artwork_record(tmp_path):
    # The credit and the privacy step's artwork check follow the copy's lineage back to the original's record.
    src = _photo(tmp_path / "14_commons.jpg")
    (tmp_path / "credits.json").write_text(json.dumps({"images": [{"file": "14_commons.jpg", "title": "Engraving of a skull",
                                                                    "year": 1700}]}), encoding="utf-8")
    out = beat_images.crop_text_band(src, "bottom", [880, 330, 940, 600])
    from src import privacy
    assert privacy.is_artwork(out)


def test_a_cropped_copy_resolves_its_rights_in_the_provenance_ledger(monkeypatch, tmp_path):
    # The release gate's ledger (src/credits.py) finds the credit through the copy's lineage, as for an upscale.
    from src import credits
    assets = tmp_path / "assets"
    topic_dir = assets / "topic_x"
    topic_dir.mkdir(parents=True)
    src = _photo(topic_dir / "14_commons.jpg")
    (topic_dir / "credits.json").write_text(json.dumps({"images": [{
        "file": "14_commons.jpg", "title": "Trephined skull", "artist": "Wellcome Collection",
        "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:x.jpg"}]}), encoding="utf-8")
    monkeypatch.setattr("src.config.ASSETS_DIR", assets)
    out = beat_images.crop_text_band(src, "bottom", [880, 330, 940, 600])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    master = run_dir / "m.mp4"
    master.write_bytes(b"x")
    ledger = json.loads(credits.generate_provenance_ledger(run_dir, {"id": "topic_x"}, {}, master,
                                                           media_items=[out]).read_text(encoding="utf-8"))
    record = ledger["assets"][0]
    assert record["license"] == "CC BY 4.0" and record["rights_status"] == "verified"
    assert record["source_paths"] == [str(src.resolve())] and not ledger["issues"]


TREPANATION = BASE_DIR / "assets" / "trepanation_miracle" / "14_commons.jpg"


@pytest.mark.skipif(not TREPANATION.exists(), reason="dev photo not present")
def test_trepanation_photo_14_label_is_cropped_away(tmp_path):
    # The "A650847" card, measured on the photo: rows 87.1%-94.0%, columns 32%-60%.
    out = beat_images.crop_text_band(TREPANATION, "bottom", [871, 322, 940, 597])
    with Image.open(TREPANATION) as a, Image.open(out) as b:
        assert b.size == (a.width, round(a.height * (1 - 0.144)))
        assert b.height >= 1000 and b.height < 0.871 * a.height   # no row of the card is left
