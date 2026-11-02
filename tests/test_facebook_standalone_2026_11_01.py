"""Facebook's multi-photo post (2026-11-01): each photo is opened on its own, without the post's caption, so
every photo after the cover names its subject; since 2026-11-02 Instagram's copy carries the same kicker."""

from pathlib import Path

from PIL import Image

from src import still_layout
from src.multi_format import MultiFormatGenerator, standalone_subject

PAYLOAD = {
    "narration_script": "A colossal dome in the desert.",
    "carousel_slides": [
        {"heading": "The Eye of the Sahara", "body": "A concentric formation in Mauritania seen from orbit."},
        {"heading": "Orbital landmark", "body": "Visible from space as a prominent desert landmark."},
        {"heading": "Magmatic uplift", "body": "An intrusion deformed the rock into rings."},
        {"heading": "Earth's hidden marvels", "body": "Which wonder looks most magnificent from space?"},
    ],
    "poll": {"question": "What shaped the rings?", "options": ["Intrusion", "Impact"]},
    "share_line": "Send this to a space enthusiast you know, and follow for the next natural marvel.",
}


def _photos(tmp_path: Path) -> list:
    out = []
    for i, c in enumerate(((90, 120, 140), (150, 120, 80), (60, 70, 90))):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (1600, 1200), c).save(p)
        out.append(p)
    return out


def _kickers(folder: Path) -> dict:
    records = {}
    for slide in sorted(folder.glob("carousel_slide_*.jpg")):
        rec = still_layout.load(slide) or {}
        records[slide.name] = (rec.get("recipe") or {}, [e["name"] for e in rec.get("elements", [])])
    return records


def test_subject_comes_from_the_cover_heading() -> None:
    assert standalone_subject(PAYLOAD) == "The Eye of the Sahara"
    assert standalone_subject({"carousel_slides": [{"heading": "One two three four five six seven"}]}) == ""
    assert standalone_subject({}) == ""


def test_facebook_photos_name_the_subject_and_instagram_slides_do_not(tmp_path: Path) -> None:
    gen = MultiFormatGenerator()
    gen.generate_carousel_sequence(_photos(tmp_path), PAYLOAD, tmp_path / "ig")
    gen.generate_carousel_sequence(_photos(tmp_path), PAYLOAD, tmp_path / "fb", facebook=True)
    ig, fb = _kickers(tmp_path / "ig" / "carousel"), _kickers(tmp_path / "fb" / "carousel_fb")
    assert fb["carousel_slide_01.jpg"][0]["subject"] == "The Eye of the Sahara"
    for name in ("carousel_slide_02.jpg", "carousel_slide_03.jpg"):
        assert "kicker" in fb[name][1], name             # the subject, on a slide that had no badge
        assert "kicker" in ig[name][1], name             # and on Instagram's copy too (2026-11-02: one system)
    assert "kicker" not in fb["carousel_slide_01.jpg"][1]  # the cover already is the subject
    for rec in fb.values():
        assert rec[0]["facebook"] is True


def test_facebook_text_survives_a_square_grid_tile(tmp_path: Path) -> None:
    """Facebook's multi-photo grid shows the 4:5 slides in tiles that may be square: every text element below
    the header (the subject kicker included) must sit inside the slide's centre square."""
    gen = MultiFormatGenerator()
    gen.generate_carousel_sequence(_photos(tmp_path), PAYLOAD, tmp_path / "fb", facebook=True)
    for slide in sorted((tmp_path / "fb" / "carousel_fb").glob("carousel_slide_*.jpg")):
        rec = still_layout.load(slide)
        w, h = Image.open(slide).size
        top, bottom = (h - w) / 2, (h + w) / 2
        for e in rec["elements"]:
            if e["name"] == "header_brand" or e.get("contrast") is None:
                continue
            assert top <= e["box"][1] and e["box"][3] <= bottom, (slide.name, e["name"], e["box"], top, bottom)
