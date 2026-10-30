"""The visual overhaul (2026-10-29, visual critic round 4): brand tokens, the data font, figure-and-unit glue,
balanced breaks, text placed at a measured contrast and recorded for video_qa, the Story and carousel layouts,
the number card, map and timeline, and the plain end card (operator decisions 2026-10-29: no capsule, no
"TELL US IN THE COMMENTS" on the closing slide)."""
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, end_card, graphics, multi_format, safe_zones, still_layout, typography, video_qa
from src.font_evidence import font_rights
from src.multi_format import MultiFormatGenerator, POLL_STICKER_ROOM

ROOT = Path(__file__).resolve().parent.parent
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _photo(tmp_path, name="bright.jpg", top=(235, 228, 214), bottom=(120, 110, 96)):
    """A bright, textured stand-in photo (the hard case for light text)."""
    rng = np.random.default_rng(7)
    rows = np.linspace(0, 1, 1920)[:, None, None]
    arr = np.array(top, float) * (1 - rows) + np.array(bottom, float) * rows
    arr = np.broadcast_to(arr, (1920, 1440, 3)).copy() + rng.normal(0, 18, (1920, 1440, 3))
    p = tmp_path / name
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p, quality=92)
    return p


# ---------------------------------------------------------------- tokens and fonts
def test_one_gold_everywhere():
    assert brand.GOLD == (242, 182, 48) and brand.BONE == (243, 237, 226) and brand.INK == (11, 14, 18)
    for name in ("multi_format", "graphics", "end_card", "visual_engine", "art_direction"):
        src = (ROOT / "src" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"\(\s*255,\s*215,\s*0\b", src), name       # the old pure gold
        assert not re.search(r"\(\s*212,\s*175,\s*55\b", src), name      # the old end-card gold
    assert multi_format.GOLD == graphics.GOLD == end_card.GOLD == brand.GOLD
    for colour in (brand.BONE, brand.GOLD, brand.STONE_LT):              # every text token on the ink ground
        assert typography.contrast_ratio(colour, brand.INK) >= 4.5
    assert brand.ass_colour(brand.GOLD) == "&H0030B6F2&"


def test_plex_mono_is_bundled_licensed_and_registered():
    for f in ("IBMPlexMono-Medium.ttf", "IBMPlexMono-SemiBold.ttf"):
        ok, record = font_rights(ROOT / "fonts" / f)
        assert ok and "IBM" in record["copyright"] and "Plex" in record["release"], f
    assert Path(typography.get_font(brand.DATA_FONT, 40, weight=500).path).name == "IBMPlexMono-Medium.ttf"
    assert Path(typography.get_font(brand.DATA_FONT, 40, weight=600).path).name == "IBMPlexMono-SemiBold.ttf"
    assert {"IBMPlexMono-Medium.ttf", "IBMPlexMono-SemiBold.ttf"} <= set(typography.bundled_font_files())
    f = typography.get_font(brand.DATA_FONT, 46)
    d = ImageDraw.Draw(Image.new("L", (8, 8)))
    assert len({round(d.textlength(c, font=f), 2) for c in "0123456789"}) == 1      # fixed pitch: no jitter


# ---------------------------------------------------------------- breaks
def test_a_figure_never_breaks_from_its_unit():
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = typography.get_font("Montserrat", 46)
    text = "The central ventilation shaft, 180 ft deep, served up to 20,000 people for 104 million years"
    for width in range(260, 900, 23):
        for lines in (typography.wrap_text(d, text, font, width), typography.balanced_wrap(d, text, font, width)):
            assert " ".join(lines) == text                                  # nothing lost, spaces as written
            for a, b in zip(lines, lines[1:]):
                assert not (re.search(r"\d$", a) and re.match(r"(ft|people|million)\b", b)), (a, b)
    assert typography.glue("c. 780 and 85 m") == "c. 780 and 85 m"


def test_balanced_breaks_keep_the_line_count_and_narrow_the_widest():
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = typography.get_font("Montserrat", 48)
    text = "A bullseye of rock rings, visible from space and seen by every crew since 1965, about 25 miles across."
    greedy, balanced = typography.wrap_text(d, text, font, 900), typography.balanced_wrap(d, text, font, 900)
    widths = lambda ls: [typography.line_width(d, ln, font) for ln in ls]  # noqa: E731
    assert len(balanced) == len(greedy) and max(widths(balanced)) <= max(widths(greedy))
    assert max(widths(balanced)) - min(widths(balanced)) < max(widths(greedy)) - min(widths(greedy))


# ---------------------------------------------------------------- measured contrast
def test_place_text_raises_a_halo_only_as_far_as_the_photo_needs():
    font = typography.get_font("Anton", 120)
    bright = Image.new("RGB", (1080, 600), (232, 222, 204))
    out, plate = typography.place_text(bright, (80, 300), "THE EYE OF THE SAHARA", font, brand.BONE, "ls")
    assert plate.contrast_unaided < 1.5 and plate.halo > 0 and plate.contrast >= 4.5
    assert plate.measure(out) >= 4.5                                       # on the composed pixels
    dark = Image.new("RGB", (1080, 600), (20, 22, 26))
    _, plate = typography.place_text(dark, (80, 300), "THE EYE OF THE SAHARA", font, brand.BONE, "ls")
    assert plate.halo == 0 and plate.contrast > 10


def _story(tmp_path, i, count=3, poll=None, body="Stairways cut through the rock lead down, level by level, about 280 ft."):
    gen = MultiFormatGenerator()
    slide = {"kicker": "Down to about", "line1": "A city", "line2": "underground", "body": body}
    return gen._render_story_frame(_photo(tmp_path), slide, i, count, poll or {})


def test_story_frame_layout_contrast_and_safe_box(tmp_path):
    im, lay = _story(tmp_path, 0)
    rec = lay.to_dict()
    assert not still_layout.problems(rec)
    names = [e["name"] for e in rec["elements"]]
    assert names[:3] == ["kicker", "title_1", "title_2"] and "body" in names and "reel_pointer" in names
    sx0, sy0, sx1, sy1 = safe_zones.safe_box("story")
    for e in rec["elements"]:
        assert e["contrast"] >= 4.5, e                                     # measured on the bright photo
        assert sx0 <= e["box"][0] and e["box"][2] <= sx1 and sy0 <= e["box"][1] and e["box"][3] <= sy1
        assert e["box"][0] >= multi_format.STORY_MARGIN - 1                 # one left-aligned column
    t1, t2 = (next(e for e in rec["elements"] if e["name"] == n) for n in ("title_1", "title_2"))
    assert t1["fill"] == list(brand.BONE) and t2["fill"] == list(brand.GOLD) and t1["px"] == t2["px"]  # two-tone, one size
    assert lay.meta["body_px"] >= multi_format.STORY_BODY_PX


def test_the_poll_frame_keeps_the_sticker_room_empty(tmp_path):
    im, lay = _story(tmp_path, 2, poll={"question": "Hideout or home?", "options": ["Hideout", "Home"]},
                     body="Up to 20,000 people could shelter here. Would you?")
    rec = lay.to_dict()
    assert not still_layout.problems(rec)
    assert all(e["box"][3] < POLL_STICKER_ROOM[0] for e in rec["elements"])
    assert lay.meta["body_drawn"] == "Up to 20,000 people could shelter here."   # the frame's one question is the poll's


# ---------------------------------------------------------------- carousel
def _slide(tmp_path, archetype, card, i=1, total=7, share=""):
    gen = MultiFormatGenerator()
    p = _photo(tmp_path)
    return gen._render_carousel_slide(archetype, p, p, card, i, total, {}, share, False)


@pytest.mark.parametrize("archetype", ["cover", "immersive", "editorial_split", "comparison", "blueprint", "colossal_stat"])
def test_carousel_stacks_end_on_the_shared_line(tmp_path, archetype):
    card = {"heading": "Carved, not built", "body": "Dug into soft volcanic tuff, roughly 280 ft at its deepest, by hand."}
    im, lay = _slide(tmp_path, archetype, card, i=0 if archetype == "cover" else 2)
    rec = lay.to_dict()
    assert not still_layout.problems(rec)
    px = MultiFormatGenerator._px(im.width)
    text = [e for e in rec["elements"] if e["name"] != "header_brand"]
    assert max(e["box"][3] for e in text) <= px(MultiFormatGenerator.KEY_BOTTOM)
    assert max(e["box"][3] for e in text) >= px(MultiFormatGenerator.KEY_BOTTOM - MultiFormatGenerator.KEY_GAP) - px(14)
    assert min(e["box"][1] for e in text) >= px(MultiFormatGenerator.KEY_TOP)
    assert all(e["contrast"] >= 4.5 for e in rec["elements"]), rec["elements"]


def test_the_closing_slide_has_the_share_line_and_no_comments_footer(tmp_path, monkeypatch):
    drawn = []
    real = typography.TextPlate.__init__

    def spy(self, text, *a, **k):
        drawn.append(text)
        real(self, text, *a, **k)
    monkeypatch.setattr(typography.TextPlate, "__init__", spy)
    share = "Send this to a friend who loves ancient engineering, and follow for the next one."
    im, lay = _slide(tmp_path, "debate", {"heading": "Hideout or home?", "body": "Which was it?"}, i=6, share=share)
    assert not any("COMMENTS" in t.upper() for t in drawn)
    assert "share" in [e["name"] for e in lay.elements] and "footer" not in [e["name"] for e in lay.elements]
    shared = next(e for e in lay.elements if e["name"] == "share")
    assert shared["contrast"] >= 4.5
    from src.script_engine import engagement_bait
    assert not any(engagement_bait(t) for t in drawn)


def test_alt_text_of_the_closing_slide_has_no_comments_ask(tmp_path):
    slide = tmp_path / "carousel" / "carousel_slide_07.jpg"
    slide.parent.mkdir()
    Image.new("RGB", (8, 8)).save(slide)
    (slide.parent / "still_layout.json").write_text(json.dumps({slide.name: {"recipe": {
        "archetype": "debate", "card": {"heading": "Your take", "body": "Hideout or home?"}, "share": "Send this on."}}}))
    alt = multi_format.slide_alt_text(slide)
    assert "comments" not in alt.lower() and "Send this on." in alt


# ---------------------------------------------------------------- graphics
def test_figure_parts():
    assert graphics.figure_parts("ABOUT 280 FT (85 M)") == {"pre": "ABOUT", "num": "280", "unit": "FT", "sec": "85 M"}
    assert graphics.figure_parts("25+ MILES") == {"pre": "", "num": "25+", "unit": "MILES", "sec": ""}
    assert graphics.figure_parts("EST. 144 FT (44 M)")["pre"] == "EST."
    assert graphics.figure_parts("94–104 MILLION YEARS")["num"] == "94–104"


def test_scale_bar_follows_the_narration_unit():
    feet = graphics.scale_bar(21.1, 60, "The rings span 25 miles (40 km) across.")
    metres = graphics.scale_bar(21.1, 60, "It reaches 85 m (280 ft) down.")
    assert feet["unit"] == "MI" and metres["unit"] == "KM" and graphics.scale_bar(21.1, 60, "")["unit"] == "KM"
    for bar in (feet, metres):
        assert 90 <= bar["px"] <= 260
    assert feet["px"] == pytest.approx(feet["value"] * 1.609344 / feet["km_per_deg_lat"] * 60, rel=1e-3)


@needs_ffmpeg
def test_number_card_fits_the_box_and_measures_on_the_graded_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 2.0)
    out = graphics.render_stat_cards([{"value": "about 280 ft (85 m)", "label": "below the surface"}],
                                     tmp_path / "graphic_numbers.mp4", _photo(tmp_path),
                                     narration="roughly 280 feet (85 m) down")
    rec = json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    x0, y0, x1, y1 = safe_zones.graphics_box()
    assert {e["name"] for e in rec["elements"]} >= {"prefix", "figure", "unit", "label", "secondary"}
    for e in rec["elements"]:
        assert x0 <= e["box"][0] and e["box"][2] <= x1 and e["box"][3] <= y1, e
        assert (e["graded"] if e["graded"] is not None else e["drawn"]) >= 4.5, e
    assert video_qa.graphic_contrast_problems(tmp_path) == []


def test_count_up_digits_roll_in_place():
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = typography.get_font("Anton", 300)
    widths = {round(sum(w for _, w in graphics._cells(d, graphics._shown_value("280", p), f)), 1) for p in (0.5, 0.9, 1.0)}
    assert len(widths) == 1                                                # three digits: one width while rolling


@needs_ffmpeg
def test_timeline_four_events_one_line_labels_above_the_caption_band(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 2.0)
    events = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
              {"year": 780, "approximate": True, "label": "Expanded against raiders"},
              {"year": 1923, "approximate": False, "label": "Abandoned after the population exchange"},
              {"year": 1963, "approximate": False, "label": "Rediscovered behind a wall"}]
    out = graphics.render_timeline(events, tmp_path / "graphic_timeline.mp4")
    rec = json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    labels = [e for e in rec["elements"] if e["name"].startswith("label")]
    # One shared label size; a label that fits one line at it takes one (three here), the longest takes two at the
    # same size rather than shrinking every label below the readable floor (art_direction.READABLE_FRACTION).
    assert sorted(e["name"] for e in labels).count("label2") == 2 and len(labels) == 5
    assert len({e["box"][3] - e["box"][1] for e in labels}) <= 2
    assert max(e["box"][3] for e in rec["elements"]) <= safe_zones.graphics_box()[3]
    assert all(e["drawn"] >= 4.5 for e in rec["elements"])


# ---------------------------------------------------------------- end card
def test_end_card_is_plain_type_with_a_two_line_ask():
    assert end_card.VARIANT == "plain_v4"
    assert not hasattr(end_card, "_Outline") and not hasattr(end_card, "CAPSULE_PAD_X")   # no capsule (2026-10-29)
    lay = end_card.layout("Send this to a space enthusiast you know.")
    assert len(lay["lines"]) == 2 and lay["share_font"].size >= 54
    x0, _, x1, y1 = safe_zones.REEL.box()
    assert x0 <= lay["box"][0] and lay["box"][2] <= x1 and lay["box"][3] <= y1


def test_end_card_lines_measure_on_its_last_frame():
    last = Image.new("RGB", (1080, 1920), (238, 232, 220))
    stats = {}
    frames = list(end_card.frames(last, "Send this to a friend who loves ancient engineering.", stats))
    assert len(frames) == round(end_card.SECONDS * end_card.FPS)
    assert all(v is not None and v >= 4.5 for v in stats["contrast"].values()), stats["contrast"]


# ---------------------------------------------------------------- video_qa
def test_video_qa_asserts_recorded_contrast(tmp_path):
    (tmp_path / "still_layout.json").write_text(json.dumps({"story_01_hook.jpg": {"elements": [
        {"name": "title_1", "box": [0, 0, 1, 1], "contrast": 6.1}, {"name": "body", "box": [0, 0, 1, 1], "contrast": 3.2}]}}))
    found = video_qa.still_contrast_problems(tmp_path)
    assert len(found) == 1 and "body" in found[0] and "3.20" in found[0]
    (tmp_path / "graphic_map.mp4.contrast.json").write_text(json.dumps({"clip": "graphic_map.mp4", "elements": [
        {"name": "scale", "text": "200 MI", "graded": 4.1, "drawn": 5.0}]}))
    assert "scale" in video_qa.graphic_contrast_problems(tmp_path)[0]


def test_caption_mask_keeps_only_glyph_fills():
    ass = ("Dialogue: 0,0:00:00.00,0:00:01.00,CapBox,,0,0,0,,{\\pos(390,1134)\\p1}m 0 0 l 10 0{\\p0}\n"
           "Dialogue: 1,0:00:00.00,0:00:01.00,CapText,,0,0,0,,{\\pos(540,1180)\\q2}{\\c&H0030B6F2&\\fscx100}R{\\c&H00909090&}INGS")
    mask = video_qa._caption_mask_ass(ass)
    assert "CapBox" not in mask and "\\bord0" in mask and "&H0030B6F2&" not in mask and "&H00909090&" not in mask
