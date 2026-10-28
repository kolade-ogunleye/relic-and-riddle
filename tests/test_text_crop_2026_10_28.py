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


def _the_dev_photo(path, sha16):
    """True only for the exact development photo the measurements were taken on: production keeps its own photos
    under the same names, and rc66's verify under the pipeline account ran these on them (2026-10-28)."""
    import hashlib
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()[:16] == sha16


@pytest.mark.skipif(not _the_dev_photo(TREPANATION, "2af0d401ec124c37"), reason="the dev photo is not here")
def test_trepanation_photo_14_label_is_cropped_away(tmp_path):
    # The "A650847" card, measured on the photo: rows 87.1%-94.0%, columns 32%-60%.
    out = beat_images.crop_text_band(TREPANATION, "bottom", [871, 322, 940, 597])
    with Image.open(TREPANATION) as a, Image.open(out) as b:
        assert b.size == (a.width, round(a.height * (1 - 0.144)))
        assert b.height >= 1000 and b.height < 0.871 * a.height   # no row of the card is left


# ---------------------------------------------------------------- the photos found per beat (audit 2026-10-28)
# The beat search set a found picture with a label strip aside at 512 px, uncropped; it now takes the pool screen's
# look: 1024 px, the edge band cropped away (crop_text_band) and the copy looked at again.
def _beat_search(monkeypatch, tmp_path, label=True):
    from src.models import BeatQueries, BeatQuery
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=1, query="trephined skull", keywords=["skull"])]))
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: [q])
    cand = {"title": "File:Trephined skull.jpg", "download_url": "https://x/a", "source_url": "https://x/a.html",
            "artist": "Wellcome Collection", "license": "CC BY 4.0", "license_url": "", "attribution_required": True,
            "description": "Trephined skull", "relevance": 5}
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter([dict(cand)]))
    monkeypatch.setattr(beat_images.commons, "download_jpeg",
                        lambda url, dest, max_wait=30: _photo(dest, label=label))
    return [{"text": "Healers cut a hole in the skull.", "start": 0, "end": 3}], {"title": "Trepanation", "location": {}}


def test_a_found_picture_with_an_edge_label_is_cropped_not_set_aside(monkeypatch, tmp_path, caplog):
    beats, topic = _beat_search(monkeypatch, tmp_path)

    def verdicts(path, h):
        if h == 2400:   # the download, with its accession card
            return {"checked": True, "shows": True, "burned_in_text": True, "reason": "A trephined skull.",
                    "text_reason": "an accession number label at the bottom", "text_edge": "bottom",
                    "text_box": [880, 330, 940, 600]}
        return {"checked": True, "shows": True, "burned_in_text": False, "reason": "a trephined skull, cropped"}
    looks = _looker(monkeypatch, verdicts)
    caplog.set_level("INFO", logger="src.beat_images")
    work = tmp_path / "beat_images"
    paths, creds = beat_images.source(beats, topic, work)
    assert len(paths) == 1 and paths[0].parent.parent == beat_images.TEXT_CROP_DIR and paths[0].name == "beat01_1.jpg"
    assert looks == [("beat01_1.jpg", 2400, 1024), ("beat01_1.jpg", 2076, 1024)]   # both looks at 1024 px
    assert creds[0]["file"] == str(paths[0]) and creds[0]["cropped_from"] == str(work / "beat01_1.jpg")
    assert creds[0]["looked"] == "a trephined skull, cropped"
    assert (work / "beat01_1.jpg").is_file()                         # the original stays: the copy's lineage source
    from src.lineage import source_chain
    assert any(s.get("path") == str((work / "beat01_1.jpg").resolve()) for s in source_chain(paths[0]))
    record = json.loads((work / "credits.json").read_text(encoding="utf-8"))["images"]
    assert [r["file"] for r in record] == ["beat01_1.jpg"]             # names the original, beside it
    assert "cropped away" in caplog.text


def test_a_found_picture_whose_crop_still_shows_text_is_set_aside(monkeypatch, tmp_path):
    beats, topic = _beat_search(monkeypatch, tmp_path)
    looks = _looker(monkeypatch, lambda path, h: {"checked": True, "shows": True, "burned_in_text": True, "reason": "r",
                                                  "text_reason": "a label", "text_edge": "bottom",
                                                  "text_box": [880, 330, 940, 600]})
    paths, creds = beat_images.source(beats, topic, tmp_path / "beat_images")
    assert paths == [] and creds == [] and len(looks) == 2               # cropped, looked at again, still text
    assert not (tmp_path / "beat_images" / "beat01_1.jpg").exists()


def test_a_found_picture_with_text_inside_or_off_subject_is_not_cropped(monkeypatch, tmp_path):
    beats, topic = _beat_search(monkeypatch, tmp_path)
    looks = _looker(monkeypatch, lambda path, h: {"checked": True, "shows": True, "burned_in_text": True, "reason": "r",
                                                  "text_reason": "a placard", "text_edge": "inside",
                                                  "text_box": [400, 300, 600, 700]})
    assert beat_images.source(beats, topic, tmp_path / "a") == ([], []) and len(looks) == 1
    looks = _looker(monkeypatch, lambda path, h: {"checked": True, "shows": False, "burned_in_text": True, "reason": "a jar",
                                                  "text_reason": "a label", "text_edge": "bottom",
                                                  "text_box": [880, 330, 940, 600]})
    assert beat_images.source(beats, topic, tmp_path / "b") == ([], []) and len(looks) == 1   # wrong picture: no crop
    assert not beat_images.TEXT_CROP_DIR.exists() or not any(beat_images.TEXT_CROP_DIR.rglob("*.jpg"))


def test_the_pool_screen_and_the_beat_search_share_one_crop_rule():
    import inspect
    assert "cropped_clear(" in inspect.getsource(beat_images.without_burned_in_text)
    assert "cropped_clear(" in inspect.getsource(beat_images.screened_picture)
    assert "crop_text_band(" not in inspect.getsource(beat_images.without_burned_in_text)


def test_the_planner_reads_a_cropped_copys_caption_through_its_lineage(tmp_path):
    """Audit 2026-10-28: a cropped or blurred copy has no credits.json beside it; its lineage leads to the original."""
    import json
    from PIL import Image
    from src import scene_planner
    from src.lineage import record_derived
    topic = tmp_path / "assets" / "t"; topic.mkdir(parents=True)
    original = topic / "14_commons.jpg"; Image.new("RGB", (8, 8)).save(original)
    (topic / "credits.json").write_text(json.dumps({"images": [{"file": "14_commons.jpg", "title": "File:Skull.jpg",
                                                                  "description": "A trepanned skull"}]}), encoding="utf-8")
    crop_dir = tmp_path / "cache" / "text_crop" / "ab"; crop_dir.mkdir(parents=True)
    crop = crop_dir / "14_commons.jpg"; Image.new("RGB", (8, 6)).save(crop)
    record_derived(crop, [original], "text_crop", {})
    scene_planner._folder_credits.cache_clear()
    assert "trepanned skull" in scene_planner._photo_caption(crop)
    assert scene_planner._photo_caption(tmp_path / "nowhere.jpg") == ""
