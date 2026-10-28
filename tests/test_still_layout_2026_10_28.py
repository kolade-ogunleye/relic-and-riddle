"""Stills never ship with text cut off or overlapping (still-layout audit, 2026-10-28).

Production Antikythera 1 (Story frame 2 "truncated mid-sentence at '35 meshing'") and Richat 2 (cover "MAURITANIA'S
BULLSEYE cut off", carousel header colliding) were rejected after the vision look only noted them. The layout is
now fitted with the fonts' real metrics, measured after drawing, and a cut-off or overlap the look reports is
redrawn once with the tighter fit, then raised to minor if it persists."""
import json

import pytest
from PIL import Image, ImageDraw

from src import config, safe_zones, still_layout, still_vision
from src.multi_format import MultiFormatGenerator
from src.visual_engine import COVER_STYLES, VisualEngine, cover_text_zone

# The production copy, verbatim (C:\tmp\audit\prod\output\<run>\verified_script.json).
ANTIKYTHERA = {
    "title": "The Antikythera Mechanism", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
    "story_slides": [
        {"kicker": "DEEP SEA DISCOVERY", "line1": "BRONZE IN THE REEF", "line2": "LOST TECHNOLOGY",
         "body": "In 1901, divers retrieved a corroded bronze artifact from an ancient Roman shipwreck."},
        {"kicker": "FORENSIC SCANS", "line1": "35 MESHTEETH GEARS", "line2": "ECLIPSE TRACKER",
         "body": "By one estimate, computer X-ray tomography scans indicate the original mechanism contained 35 meshing "
                 "bronze gears, 30 of which remain visible today."},
        {"kicker": "ANCIENT INGENUITY", "line1": "CALCULATING ORBITS", "line2": "WHICH CAME FIRST?",
         "body": "The bronze gear train was built to track solar and lunar movements through the zodiac, predict eclipses, "
                 "and model the Moon's variable speed between apogee and perigee."},
    ],
    "poll": {"question": "Which ancient feat is more impressive?", "options": ["Predicting eclipses", "Cutting miniature gears"]},
}
RICHAT = {
    "title": "The Eye of the Sahara", "poll": {"question": "What shaped the desert rings?",
                                                "options": ["Subsurface intrusion", "Meteorite collision"]},
    "thumbnail": {"eyebrow": "SAHARA ANOMALY", "line1": "THE 30-MILE DOME", "line2": "SEEN FROM SPACE",
                  "tagline": "MAURITANIA'S BULLSEYE"},
    "carousel_slides": [
        {"heading": "THE EYE OF THE SAHARA", "body": "A colossal geological dome in Mauritania that space shuttle crews track from orbit."},
        {"heading": "AN UPLIFTED DOME", "body": "An igneous intrusion pushed up sedimentary rock layers into concentric rings."},
        {"heading": "NO METEORITE IMPACT", "body": "Surveys found no shock metamorphism, and reported coesite was misidentified barite."},
        {"heading": "PREHISTORIC EVIDENCE", "body": "Aterian stone tools show modern humans inhabited the area up to 145,000 years ago."},
        {"heading": "ANCIENT BURIAL MOUNDS", "body": "Thousands of stone burial mounds in various styles line the protruding dikes."},
        {"heading": "WORLD HERITAGE SITE", "body": "In October 2022, it was recognized as one of the top 100 geological heritage sites."},
        {"heading": "ENDURING LANDMARKS", "body": "Which natural formations on Earth do you think will still remain visible from space?"},
    ],
}
LONG_LINE = ("By one estimate, computer X-ray tomography scans of every surviving fragment indicate that the original "
             "mechanism once contained at least thirty-five meshing bronze gears, cut by hand with triangular teeth, "
             "driven by a single crank on its side, read through dials on its front and back, and described in "
             "thousands of tiny engraved letters that scholars are still reading today, more than a century after divers "
             "first lifted it from the wreck")


def _photo(tmp_path, name="p.jpg", size=(1200, 1600)):
    import numpy as np
    rng = np.random.default_rng(len(name))
    arr = (rng.random((size[1], size[0], 3)) * 120 + 60).astype("uint8")  # textured, mid-bright
    path = tmp_path / name
    Image.fromarray(arr).save(path)
    return path


def _records(folder):
    return json.loads((folder / still_layout.SIDECAR).read_text(encoding="utf-8"))


def _assert_clean(entry, zone):
    assert entry["problems"] == [], entry["problems"]
    for e in entry["elements"]:
        assert still_layout.inside(tuple(e["box"]), tuple(zone)), (e["name"], e["box"], zone)
    boxes = [tuple(e["box"]) for e in entry["elements"]]
    assert not any(still_layout.intersects(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])


def test_antikythera_story_text_and_a_long_line_stay_whole_inside_the_story_box(tmp_path):
    photo = _photo(tmp_path)
    slides = MultiFormatGenerator().generate_story_sequence(photo, ANTIKYTHERA, tmp_path)
    records, layout = _records(tmp_path / "stories"), json.loads((tmp_path / "stories" / "layout.json").read_text(encoding="utf-8"))
    zone = safe_zones.safe_box("story")
    for slide, entry in zip(slides, layout):
        assert Image.open(slide).size == (1080, 1920)
        _assert_clean(records[slide.name], zone)
        assert entry["body_drawn"] == entry["body_source"]                 # "35 meshing" is followed by the rest
        assert safe_zones.inside(tuple(entry["text_box"]), zone)
    assert "30 of which remain visible today." in layout[1]["body_drawn"]
    # A body far past the three lines the script allows still cannot leave the box: whole while it fits,
    # shortened at a word with an ellipsis only when the box has no room left.
    long_payload = {**ANTIKYTHERA, "story_slides": [dict(ANTIKYTHERA["story_slides"][2], body=LONG_LINE * 2)] * 3}
    slides = MultiFormatGenerator().generate_story_sequence(photo, long_payload, tmp_path / "long")
    records = _records(tmp_path / "long" / "stories")
    for slide in slides:
        _assert_clean(records[slide.name], zone)
        assert "body" in records[slide.name]["shortened"]
    drawn = json.loads((tmp_path / "long" / "stories" / "layout.json").read_text(encoding="utf-8"))[2]["body_drawn"]
    assert drawn.endswith(chr(0x2026)) and " ".join((LONG_LINE * 2).split()).startswith(drawn[:-1])


def test_richat_cover_draws_its_tagline_inside_the_square_in_every_style(tmp_path, monkeypatch):
    drawn = []
    real = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: drawn.append(str(text)) or real(self, xy, text, *a, **k))
    photo = _photo(tmp_path, size=(1600, 2400))
    for style in COVER_STYLES:
        drawn.clear()
        cover, grid = VisualEngine().generate_thumbnails(photo, RICHAT, tmp_path / style, style=style, variants=False)
        entry = _records(tmp_path / style)["thumbnail_cover.jpg"]
        _assert_clean(entry, cover_text_zone())
        assert {"eyebrow", "title_1", "title_2", "tagline"} <= {e["name"] for e in entry["elements"]}, style
        assert "MAURITANIA'S BULLSEYE" in drawn, style                     # lower_third used to leave it out
        assert entry["shortened"] == [] and entry["recipe"]["style"] == style
        assert Image.open(grid).size == (1080, 1080)


def test_carousel_badges_and_headers_never_overlap(tmp_path, monkeypatch):
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos", lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))])
    photos = [_photo(tmp_path, f"{i}.jpg") for i in range(3)]
    gen = MultiFormatGenerator()
    zone = gen._zone(*gen.CAROUSEL_SIZE)
    for facebook in (False, True):
        slides = gen.generate_carousel_sequence(photos, RICHAT, tmp_path, facebook=facebook)
        records = _records(slides[0].parent)
        assert len(records) == 7
        for slide in slides:
            _assert_clean(records[slide.name], zone)
    # Every archetype, Richat's longest heading and body, both copies: the header band is reserved.
    card = RICHAT["carousel_slides"][0]
    for archetype in ("cover", "immersive", "comparison", "blueprint", "colossal_stat", "debate", "editorial_split"):
        for tight in (False, True):
            _, lay = gen._render_carousel_slide(archetype, photos[0], photos[1], card, 3, 7, RICHAT["poll"],
                                                "Send this to a geology enthusiast, and follow for the next marvel.", False, tight=tight)
            assert lay.problems() == [], (archetype, tight, lay.problems())
            names = {e["name"] for e in lay.elements}
            assert {"header_dossier", "header_brand", "swipe_cues"} <= names
    # The comparison slide's REF A badge used to sit at y=35, under the dossier header at y=70.
    _, lay = gen._render_carousel_slide("comparison", photos[0], photos[1], card, 3, 7, {}, "", False)
    boxes = {e["name"]: e["box"] for e in lay.elements}
    assert boxes["ref_a"][1] >= boxes["header_dossier"][3]


def test_the_post_draw_check_catches_a_broken_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STILL_VISION_CHECK", False)                # numbers only: no model is asked
    lay = still_layout.Layout("story", (1080, 1920), safe_zones.safe_box("story"))
    lay.add("title", (100, 600, 980, 700))
    lay.add("badge", (500, 650, 700, 720))                                  # over the title
    lay.add("body", (100, 1200, 980, 1300))                                 # under the reply bar
    lay.add("tagline", (900, 800, 1100, 850))                               # off the canvas
    found = lay.problems()
    assert any(p.startswith("layout_overlap: title") and "badge" in p for p in found)
    assert any(p.startswith("text_cut_off: body") and "safe zone" in p for p in found)
    assert any(p.startswith("text_cut_off: tagline") and "canvas" in p for p in found)
    # A renderer whose first layout fails is drawn once more with its tighter fit, and the record says so.
    calls = []

    def render(tight):
        calls.append(tight)
        out = still_layout.Layout("cover", (1080, 1920), cover_text_zone(), tight=tight)
        out.add("title_1", (100, 800, 980, 900))
        out.add("title_2", (100, 880 if not tight else 910, 980, 980))
        return Image.new("RGB", (1080, 1920)), out
    _, final, attempts = still_layout.drawn(render)
    assert calls == [False, True] and attempts == 2 and final.problems() == [] and final.tight
    # A kept record that no longer holds (a box moved past the zone) is found again from the file alone,
    # and the audit redraws that still from its recipe.
    photo = _photo(tmp_path, size=(1600, 2400))
    cover, _ = VisualEngine().generate_thumbnails(photo, RICHAT, tmp_path / "c", style="lower_third", variants=False)
    data = _records(cover.parent)
    data[cover.name]["elements"][0]["box"][3] = 1900
    (cover.parent / still_layout.SIDECAR).write_text(json.dumps(data), encoding="utf-8")
    assert still_layout.check(cover) and still_layout.check(cover)[0].startswith("text_cut_off")
    issues = still_vision.findings([cover], "Reel cover", [""], "cover_look", "cover")
    assert issues == [] and still_layout.check(cover) == [] and _records(cover.parent)[cover.name]["tight"]


@pytest.fixture
def looked(monkeypatch):
    """still_vision.look replaced by a scripted verdict per call; counts the redraws."""
    state = {"verdicts": [], "calls": 0, "redraws": 0}

    def fake_look(path, kind, copy=""):
        state["calls"] += 1
        return state["verdicts"].pop(0) if state["verdicts"] else ([], "")
    real_redraw = still_layout.redraw

    def counting_redraw(path):
        state["redraws"] += 1
        return real_redraw(path)
    monkeypatch.setattr(still_vision, "look", fake_look)
    monkeypatch.setattr(still_layout, "redraw", counting_redraw)
    return state


def test_a_cut_off_finding_redraws_once_then_is_minor_if_it_persists(tmp_path, looked):
    photo = _photo(tmp_path, size=(1600, 2400))
    cover, _ = VisualEngine().generate_thumbnails(photo, RICHAT, tmp_path / "a", style="lower_third", variants=False)
    before = cover.read_bytes()
    cut = (["text_cut_off"], "The final line of text ('MAURITANIA'S BULLSEYE') has been cut off.")
    looked["verdicts"] = [cut, cut]
    issues = still_vision.findings([cover], "Reel cover", ["SAHARA ANOMALY / ... / MAURITANIA'S BULLSEYE"], "cover_look", "cover")
    assert looked["redraws"] == 1 and looked["calls"] == 2                   # one redraw, one re-check
    assert cover.read_bytes() != before and _records(cover.parent)[cover.name]["tight"]
    assert _records(cover.parent)[cover.name]["attempts"] == 2
    assert len(issues) == 1 and issues[0]["severity"] == "minor" and issues[0]["redrawn"]
    assert "still seen after one redraw" in issues[0]["issue"]
    # Fixed by the redraw: kept as a note, never a block.
    cover2, _ = VisualEngine().generate_thumbnails(photo, RICHAT, tmp_path / "b", style="badge", variants=False)
    looked.update(verdicts=[(["layout_overlap"], "The header overlaps the badge."), ([], "")], calls=0, redraws=0)
    issues = still_vision.findings([cover2], "Reel cover", [""], "cover_look", "cover")
    assert looked["redraws"] == 1 and issues[0]["severity"] == "info" and "no longer sees it" in issues[0]["issue"]
    # Any other finding stays information, with no redraw.
    looked.update(verdicts=[(["text_over_subject"], "The title sits across the dome.")], calls=0, redraws=0)
    issues = still_vision.findings([cover2], "Reel cover", [""], "cover_look", "cover")
    assert looked["redraws"] == 0 and issues[0]["severity"] == "info"
    # A still with no layout record (drawn before this change) cannot be redrawn: minor, so the setting decides.
    old = tmp_path / "old.jpg"
    Image.new("RGB", (1080, 1920), (90, 80, 70)).save(old)
    looked.update(verdicts=[cut], calls=0, redraws=0)
    issues = still_vision.findings([old], "Reel cover", [""], "cover_look", "cover")
    assert issues[0]["severity"] == "minor" and "no layout record" in issues[0]["issue"] and looked["calls"] == 1


def test_story_and_carousel_cut_offs_redraw_through_the_audit(tmp_path, looked, monkeypatch):
    from src.video_qa import QualityAuditor
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos", lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))])
    photo = _photo(tmp_path)
    MultiFormatGenerator().generate_story_sequence(photo, ANTIKYTHERA, tmp_path)
    MultiFormatGenerator().generate_carousel_sequence([photo, _photo(tmp_path, "q.jpg")], RICHAT, tmp_path)
    cut = (["text_cut_off"], "The body text is truncated mid-sentence at '35 meshing'.")
    looked["verdicts"] = [([], ""), cut, ([], "")]                         # frame 2 cut, its re-check clean
    report = QualityAuditor().audit_story_cards(tmp_path / "stories", payload=ANTIKYTHERA)
    looks = [i for i in report["issues"] if i["type"] == "story_frame_look"]
    assert looked["redraws"] == 1 and [i["severity"] for i in looks] == ["info"] and looks[0]["card"] == "story_02_reveal.jpg"
    assert _records(tmp_path / "stories")["story_02_reveal.jpg"]["tight"]
    layout = json.loads((tmp_path / "stories" / "layout.json").read_text(encoding="utf-8"))
    assert [e["frame"] for e in layout] == ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]
    # Nothing measured or cut; the production bodies' five lines are the script stage's three-line rule's to
    # prevent (story_text_too_small), and are drawn whole.
    assert {i["type"] for i in report["issues"] if i["severity"] in ("critical", "major")} <= {"story_text_too_small"}
    overlap = (["layout_overlap"], "The header text 'ARCHIVAL RECORD // 01 OF 07' collides with the solar panel.")
    looked.update(verdicts=[overlap, overlap], calls=0, redraws=0)
    report = QualityAuditor().audit_carousel_slides(tmp_path / "carousel", payload=RICHAT)
    looks = [i for i in report["issues"] if i["type"] == "carousel_slide_look"]
    assert looked["redraws"] == 1 and looks[0]["severity"] == "minor" and looks[0]["slide"] == "carousel_slide_01.jpg"
    assert {"header_dossier", "header_brand"} <= {e["name"] for e in _records(tmp_path / "carousel")["carousel_slide_01.jpg"]["elements"]}


def test_switched_off_the_measured_check_still_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STILL_VISION_CHECK", False)
    photo = _photo(tmp_path, size=(1600, 2400))
    cover, _ = VisualEngine().generate_thumbnails(photo, RICHAT, tmp_path, style="minimal", variants=False)
    data = _records(tmp_path)
    data[cover.name]["elements"].append({"name": "stray", "box": [0, 0, 2000, 10]})
    data[cover.name]["recipe"] = None                                       # nothing to redraw it from
    (tmp_path / still_layout.SIDECAR).write_text(json.dumps(data), encoding="utf-8")
    issues = still_vision.findings([cover], "Reel cover", [""], "cover_look", "cover")
    assert [i["severity"] for i in issues] == ["major"] and "canvas" in issues[0]["issue"]


def test_accented_headings_and_titles_never_overlap_the_line_below_or_above():
    """Review 2026-10-28: accented capitals rose above the cap height and every redraw repeated the overlap."""
    import glob
    from pathlib import Path
    from PIL import Image
    from src.multi_format import MultiFormatGenerator
    from src.visual_engine import VisualEngine, calm_center_y, VIDEO_WIDTH, VIDEO_HEIGHT
    photo = Path(__file__).parent / "_accent_photo.jpg"
    Image.new("RGB", (1600, 1200), (90, 80, 70)).save(photo)
    try:
        g = MultiFormatGenerator()
        poll = {"question": "Planned or lucky?", "options": ["Careful planning", "Lucky accident"]}
        for arch in ("blueprint", "editorial_split", "debate"):
            for head in ("Çatalhöyük", "Teotihuacán", "Kraków salt mine", "Île de Pâques"):
                for tight in (False, True):
                    _, lay = g._render_carousel_slide(arch, photo, photo, {"heading": head, "body": "Built 4,000 years ago, it still stands."},
                                                      1, 6, poll, "Share this.", False, tight=tight)
                    assert not lay.problems(), (arch, head, tight, lay.problems())
        ve = VisualEngine()
        from src.typography import cover_crop
        im = cover_crop(photo, (VIDEO_WIDTH, VIDEO_HEIGHT)); ve._text_cy = calm_center_y(im)
        for l1, l2 in (("ÉPHÈSE", "LOST CITY"), ("CÁDIZ", "THE OLDEST CITY"), ("TÜRKIYE", "UNDERGROUND")):
            for style in ("badge", "lower_third", "minimal", "depth_sandwich"):
                _, lay, _ = ve._render_checked(im, {"eyebrow": "AB", "line1": l1, "line2": l2, "tagline": "A TAGLINE", "badge": "1518"}, style)
                assert not lay.problems(), (style, l1, lay.problems())
    finally:
        photo.unlink(missing_ok=True)
