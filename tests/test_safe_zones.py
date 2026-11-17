"""Every deliverable keeps its text and figures inside the area the platforms leave free of their own interface
(operator, 2026-10-24: "ensure our visual contents are within each social media platform's safe UI"): one spec
(src/safe_zones.py), and each renderer measured against it rather than trusted: the caption pills' rectangles,
bright text pixels on every 9:16 graphic, the Story frames' recorded text blocks, and the covers' centre square."""
import json

import numpy as np
import pytest
from PIL import Image

from src import graphics, safe_zones
from src.config import CAPTION_MARGIN_V, VIDEO_HEIGHT, VIDEO_WIDTH
from src.video_composer import MAX_CAPTION_WIDTH, VideoComposer, reel_caption_span
from src.video_qa import QualityAuditor, caption_pills

W, H = VIDEO_WIDTH, VIDEO_HEIGHT


def test_the_reel_box_is_the_strictest_of_its_three_platforms():
    # QF-24 (decision QF-24/QF-87 layout, 2026-11-09, deliberate change): the organic viewers' profiles, not the ads
    # guides' (Meta's 14% / 35% / 6%, Shorts' 288 top and 192 right: Inset(288, 672, 65, 192), box (65, 288, 888,
    # 1248), centred width 696, (32, 144, 444, 624) at 540 x 960, margin 672, text box (65, 330, 888, 1058)): Shorts'
    # 288 top and 232 right rail, Facebook's 550 bottom and the Meta screenshots' 113 left decide the Reel box.
    assert safe_zones.REEL == safe_zones.Inset(top=288, bottom=550, left=113, right=232)
    assert safe_zones.STORY == safe_zones.Inset(top=269, bottom=672, left=65, right=65)
    assert safe_zones.safe_box("reel") == (113, 288, 848, 1370) and safe_zones.safe_box("story") == (65, 269, 1015, 1248)
    assert safe_zones.REEL.centred_width() == 616 and safe_zones.REEL.box(540, 960) == (56, 144, 424, 685)
    assert safe_zones.cover_box() == (0, 420, 1080, 1500)
    # The renderers' constants come from the spec, so they cannot drift apart again.
    assert CAPTION_MARGIN_V == safe_zones.REEL.bottom == 550
    # the pill, padding included, fits the box centred on the box (QF-24: the reviewed 640 px measure is kept and
    # centred at x 480, not cut to the 616 px left around the frame's centre; was == centred_width(), 696)
    assert MAX_CAPTION_WIDTH == 640 and MAX_CAPTION_WIDTH + 2 * 28 <= reel_caption_span() == 734
    assert graphics.get_text_bounds(W, H) == (330, 1090) and graphics.get_text_box(W, H) == (113, 330, 848, 1090)
    assert graphics.centred_text_width(W, H) == 616 and graphics.TEXT_BOTTOM == 1090
    assert graphics.get_text_bounds(1920, 1080) == (129, 820)                         # the documentary's bounds unchanged


def test_every_caption_pill_ends_above_the_platforms_bottom_band(tmp_path):
    # The first publication's pills were centred on the 35% line (y 1202-1309 against 1248): half under Meta's row.
    words, t = [], 0.0
    for text in ("Visible from space, a bullseye in the Sahara.", "Pseudoarchaeoastronomical claims followed.",
                 "It is 25 to almost 31 miles across!", "Ok."):
        for word in text.split():
            words.append({"word": word, "start": round(t, 3), "dur": 0.3})
            t += 0.35
    ass = VideoComposer().build_subtitles(words, tmp_path / "c.ass")
    pills = caption_pills(ass)
    box = safe_zones.safe_box("reel")
    assert len(pills) >= 4
    assert all(safe_zones.inside(p, box) for p in pills), [p for p in pills if not safe_zones.inside(p, box)]
    assert box[3] - 20 <= max(p[3] for p in pills) <= box[3]                     # on the line, not floating upward
    assert min(p[0] for p in pills) >= box[0] and max(p[2] for p in pills) <= box[2]


def _bright_outside(frame: Image.Image, box, threshold: int = 200, grow: int = 0) -> int:
    """Bright, text-like pixels (white or gold) outside `box`; structural strokes are dimmer than this."""
    lum = np.asarray(frame.convert("L"))
    mask = lum > threshold
    x0, y0, x1, y1 = box
    mask[max(0, y0 - grow):y1 + grow, max(0, x0 - grow):x1 + grow] = False
    return int(mask.sum())


CARDS = [{"value": "1,200,000 TONS", "label": "Of volcanic stone removed from the hillside by hand"},
         {"value": "94–104M YEARS AGO", "label": "Carbonatite rock cooling age, the recorded diameter"}]
TIMELINE = [{"year": -9500, "approximate": True, "label": "The first enclosures are raised on the hilltop ridge"},
            {"year": -8200, "approximate": True, "label": "The site is deliberately buried under rubble fill"},
            {"year": 1963, "label": "A survey notes the mounds and moves on"},
            {"year": 1994, "label": "Excavation begins and continues to this day"}]
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables, wine presses and storage chambers"},
                      {"depth_ft": 35.0, "label": "Living quarters, kitchens and communal halls"},
                      {"depth_ft": 140.0, "label": "Byzantine cruciform church and school"},
                      {"depth_ft": 280.0, "label": "Deepest wells and ventilation shaft base"}]}
# QF-23: SECTION's first two labels fit its column only cut ("and storage…", "kitchens and…"), so it is refused; the
# box check runs on the same section with labels the column holds whole
SECTION_WHOLE = {**SECTION, "levels": [dict(SECTION["levels"][0], label="Stables and storage"),
                                       dict(SECTION["levels"][1], label="Living quarters"), *SECTION["levels"][2:]]}
GRAPHICS = {
    "numbers": lambda out: graphics.render_stat_cards(CARDS, out),
    "timeline": lambda out: graphics.render_timeline(TIMELINE, out),
    "scale_depth": lambda out: graphics.render_scale({"value": 280.0, "unit": "ft", "dimension": "depth",
                                                      "subject": "Derinkuyu underground city depth", "approximate": True}, out),
    "scale_height": lambda out: graphics.render_scale({"value": 32.0, "unit": "m", "dimension": "height",
                                                       "subject": "Kailasa temple monolith height", "approximate": True}, out),
    "section": lambda out: graphics.render_cross_section(SECTION_WHOLE, out),
}


def test_a_section_whose_labels_fit_only_cut_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "_encode", lambda *_a, **_k: pytest.fail("a refused section was encoded"))
    with pytest.raises(graphics.TextFitError, match="Checked copy does not fit the cross-section's label column"):
        graphics.render_cross_section(SECTION, tmp_path / "s.mp4")


@pytest.mark.parametrize("kind", sorted(GRAPHICS))
def test_a_graphics_text_and_figures_stay_inside_the_reel_box(kind, tmp_path):
    clip = GRAPHICS[kind](tmp_path / f"{kind}.mp4")
    assert clip is not None
    poster = Image.open(clip.with_suffix(".jpg"))                                     # the last frame: every element shown
    assert poster.size == (W, H)
    outside = _bright_outside(poster, graphics.get_text_box(W, H), grow=4)
    assert outside == 0, f"{kind}: {outside} bright pixels outside {graphics.get_text_box(W, H)}"


@pytest.mark.skipif(not graphics.geo.available(), reason="Natural Earth data not installed")
def test_the_map_label_stays_inside_the_reel_box(tmp_path):
    clip = graphics.render_map({"lat": 21.124, "lon": -11.401, "label": "Richat Structure, the Eye of the Sahara",
                                "country": "Mauritania"}, tmp_path / "map.mp4")
    poster = Image.open(clip.with_suffix(".jpg"))
    assert _bright_outside(poster, graphics.get_text_box(W, H), threshold=235, grow=4) == 0  # the relief is never this bright


def test_story_text_blocks_stay_inside_the_story_box(tmp_path):
    from src.multi_format import MultiFormatGenerator
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1080, 1920), (50, 50, 50)).save(hero)
    payload = {
        "title": "The Eye of the Sahara", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
        "story_slides": [
            {"kicker": "VISIBLE FROM SPACE", "line1": "A BULLSEYE", "line2": "IN THE SAND", "body": "Thirty miles across, and nobody built it. The rings are rock of different ages."},
            {"kicker": "THE REVEAL", "line1": "NOT A CRATER", "line2": "A DOME", "body": "Erosion carved the rings from a dome that rose and collapsed."},
            {"kicker": "YOUR CALL", "line1": "WHAT DO", "line2": "YOU SEE?", "body": "Is it a lost city, or geology? Which do you believe?"},
        ],
        "poll": {"question": "Lost city or geology?", "options": ["A lost city", "Geology, nothing more"]},
    }
    slides = MultiFormatGenerator().generate_story_sequence(hero, payload, tmp_path)
    layout = json.loads((tmp_path / "stories" / "layout.json").read_text(encoding="utf-8"))
    box = safe_zones.safe_box("story")
    assert [e["frame"] for e in layout] == [p.name for p in slides] and len(layout) == 2  # hook, reveal (QF-80)
    for entry, slide in zip(layout, slides):
        assert safe_zones.inside(tuple(entry["text_box"]), box), (entry["frame"], entry["text_box"])
        # the recorded block is honest: nothing bright is drawn outside it (the photo is flat grey)
        assert _bright_outside(Image.open(slide), entry["text_box"], grow=8) == 0, entry["frame"]
    # and the audit reads the same record
    report = QualityAuditor().audit_story_cards(tmp_path)
    assert not [i for i in report["issues"] if i["type"] == "story_text_outside_safe_zone"]
    layout[0]["text_box"][3] = 1300
    (tmp_path / "stories" / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    report = QualityAuditor().audit_story_cards(tmp_path)
    assert [i["card"] for i in report["issues"] if i["type"] == "story_text_outside_safe_zone"] == ["story_01_hook.jpg"]


def test_the_audit_reads_the_caption_pills_as_rectangles(tmp_path):
    ass = tmp_path / "c.ass"
    ass.write_text("[Events]\n"
                   "Dialogue: 0,0:00:00.08,0:00:01.14,CapBox,,0,0,0,,{\\pos(278,1202)\\p1}m 14 0 l 510 0 b 518 0 524 6 524 14 l 524 93 b 524 101 518 107 510 107 l 14 107 b 6 107 0 101 0 93 l 0 14 b 0 6 6 0 14 0{\\p0}\n"
                   "Dialogue: 1,0:00:00.08,0:00:01.14,CapText,,0,0,0,,{\\pos(540,1248)\\q2}VISIBLE\n", encoding="utf-8")
    assert caption_pills(ass) == [(278, 1202, 802, 1309)]                           # the first publication's lowest pill
    # 61 px under the ads line (y 1248) it was measured against: the finding. QF-24 (decision QF-24/QF-87 layout,
    # deliberate change): the organic Reel box ends at y 1370, so the Story box, which keeps that line, holds the check
    assert not safe_zones.inside((278, 1202, 802, 1309), safe_zones.safe_box("story"))
