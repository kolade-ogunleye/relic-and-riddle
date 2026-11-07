"""Round 3 panel (2026-11-02), social workstream: Story poll band, one text anchor, matched figure pair; Instagram's
content headlines at the Facebook treatment, range-aware figure slides, whole-word routing; Facebook pick, collage
gate and platform re-encode contrast; masthead gradient, warm closing slide, display typography."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, multi_format, still_layout
from src.multi_format import (
    MultiFormatGenerator,
    collage_crop,
    facebook_collage_report,
    facebook_pick,
    masthead_scrim,
    platform_encode,
    poll_room_box,
    range_figure,
    typeset,
    warm_ground,
)

M = MultiFormatGenerator
PX = M._px(M.CAROUSEL_SIZE[0])


def _photo(path: Path, colour, size=(1200, 1600), top=None) -> Path:
    im = Image.new("RGB", size, colour)
    if top is not None:   # a bright band across the top (sky)
        im.paste(Image.new("RGB", (size[0], size[1] // 4), top), (0, 0))
    im.save(path)
    return path


def _frame(tmp_path, slide, i, poll=None, colour=(120, 110, 95)):
    photo = _photo(tmp_path / f"s{i}.jpg", colour, (1080, 1920))
    return M()._render_story_frame(photo, slide, i, 3, poll or {})


# ---------------------------------------------------------------- Stories

POLL = {"question": "Would you hide underground for weeks?", "options": ["Yes", "No"]}
QUESTION = {"kicker": "YOUR TURN", "line1": "WOULD YOU HIDE", "line2": "DOWN THERE?", "body": "Context only."}


def test_the_poll_room_is_igs_sticker_footprint_centred_on_the_text_column(tmp_path) -> None:
    _, lay = _frame(tmp_path, QUESTION, 2, POLL, colour=(200, 200, 200))
    room = lay.meta["poll_sticker_room"]
    assert room[2] - room[0] == multi_format.STORY_STICKER_W == 760 and room[3] - room[1] == 300
    centre = multi_format.STORY_MARGIN + multi_format.story_body_width() / 2
    assert abs((room[0] + room[2]) / 2 - centre) <= 1
    assert poll_room_box(room[1]) == tuple(room)


def test_the_poll_room_sits_on_a_feathered_band_not_a_hard_edged_box(tmp_path) -> None:
    im, lay = _frame(tmp_path, QUESTION, 2, POLL, colour=(200, 200, 200))
    x0, y0, x1, y1 = lay.meta["poll_sticker_room"]
    g = np.asarray(im.convert("L"), dtype=np.float32)
    inside = g[y0 + 4:y1 - 4, x0 + 4:x1 - 4]
    assert np.percentile(inside, 95) <= 100                  # deep enough for the white sticker (about L* 40)
    # No box: a band across the frame, so left and right of the room match it, and its edges fade, not step.
    mid = (y0 + y1) // 2
    assert abs(g[mid, 20] - g[mid, (x0 + x1) // 2]) < 6 and abs(g[mid, 1060] - g[mid, (x0 + x1) // 2]) < 6
    below = g[y1:y1 + 140, 40]
    assert np.abs(np.diff(below)).max() < 8                  # feathered over ~120 px under the room


def test_the_poll_rooms_position_is_written_for_the_operator(tmp_path) -> None:
    photos = [_photo(tmp_path / f"p{i}.jpg", c, (1080, 1920)) for i, c in enumerate([(90, 80, 70), (60, 70, 90), (200, 200, 200)])]
    payload = {"story_slides": [
        {"kicker": "DID YOU KNOW?", "line1": "ONE MAN KNOCKED", "line2": "THROUGH HIS WALL", "body": "Cappadocia, 1963."},
        {"kicker": "WHAT HE FOUND", "line1": "AN UNDERGROUND CITY", "line2": "~280 FEET DEEP", "body": "Stables and wells."},
        QUESTION], "poll": POLL, "narration_script": "It was about 280 feet deep."}
    M().generate_story_sequence(photos[0], payload, tmp_path / "run", scene_images=photos)
    manifest = json.loads((tmp_path / "run" / "companion_posting.json").read_text(encoding="utf-8"))
    layout = json.loads((tmp_path / "run" / "stories" / "layout.json").read_text(encoding="utf-8"))
    pos = manifest["story_poll_sticker"]["position"]
    assert pos["frame"] == "story_03_question.jpg" and pos["box"] == layout[2]["poll_sticker_room"]
    assert pos["frame_size"] == [1080, 1920] and manifest["story_poll_sticker"]["question"] == POLL["question"]


def test_every_frame_sets_its_kicker_at_one_top_y(tmp_path) -> None:
    slides = [{"kicker": "DID YOU KNOW?", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."},
              {"kicker": "WHAT HE FOUND", "line1": "A CITY", "line2": "UNDER THE FIELDS", "body": "A longer body here."},
              QUESTION]
    colours = [(30, 30, 30), (230, 230, 230), (120, 110, 95)]   # busy and calm photos no longer move it
    tops = []
    for i, (s, c) in enumerate(zip(slides, colours)):
        _, lay = _frame(tmp_path, s, i, POLL if i == 2 else None, colour=c)
        tops.append(next(e["box"][3] for e in lay.elements if e["name"] == "kicker"))
    assert max(tops) - min(tops) <= 2


def test_the_reveal_sets_qualifier_and_unit_as_one_matched_bone_pair(tmp_path) -> None:
    slide = {"kicker": "WHAT HE FOUND", "line1": "AN UNDERGROUND CITY", "line2": "~280 FEET DEEP", "body": "Stables."}
    _, lay = _frame(tmp_path, slide, 1)
    q, fig, unit = (lay.plates[n][0] for n in ("figure_qualifier", "figure", "figure_unit"))
    assert q.font.size == unit.font.size and q.font.path == unit.font.path      # one face, one size
    assert q.fill == unit.fill == brand.BONE and fig.fill == brand.GOLD          # the numeral is the one gold read
    assert unit.box[1] - fig.box[3] <= 40 and fig.box[1] - q.box[3] <= 40          # tightened around the numeral


# ---------------------------------------------------------------- carousel

CARD = {"heading": "IMPACT THEORY DISPROVED", "body": "Initially considered an impact crater, the theory was disproved in 1969."}


@pytest.fixture
def photos(tmp_path):
    return [_photo(tmp_path / f"c{i}.jpg", c) for i, c in enumerate([(70, 90, 120), (150, 120, 90), (90, 80, 60)])]


def test_instagram_content_headlines_take_the_two_tone_treatment(photos) -> None:
    _, ig = M()._render_carousel_slide("editorial_split", photos[0], photos[1], CARD, 2, 7, {}, "", False, subject="S")
    heads = ig.plates["heading"]
    assert [p.text for p in heads] == ["IMPACT THEORY", "DISPROVED"]
    assert heads[0].fill == brand.BONE and heads[1].fill == brand.GOLD
    assert heads[0].font.size >= PX.exact(M.CONTENT_HEADING[1]) - 0.01 and heads[0].font.size >= PX.exact(124) - 0.01
    assert not ig.problems() and ig.meta["baselines"]["heading"] == round(heads[0].xy[1])
    # a short heading stays one bone line, at the larger size
    _, one = M()._render_carousel_slide("editorial_split", photos[0], photos[1], dict(CARD, heading="ANCIENT COOLING"),
                                        2, 7, {}, "", False, subject="S")
    assert len(one.plates["heading"]) == 1 and one.plates["heading"][0].font.size >= PX.exact(124) - 0.01


def test_a_range_is_read_whole_with_an_en_dash() -> None:
    assert range_figure("Carbonatite rocks cooled between 94 and 104 million years ago.") == ("94–104", "MILLION YEARS AGO")
    assert range_figure("94 - 104 million years ago") == ("94–104", "MILLION YEARS AGO")
    assert range_figure("It was disproved in 1969.") is None
    assert range_figure("Room for 18 and 20 people") is None                 # "and" is a span only after "between"
    assert range_figure("From 30 to 12 feet") is None                         # ends out of order: no span


def test_a_range_card_becomes_a_figure_slide_that_draws_the_span_whole(photos, monkeypatch) -> None:
    card = {"heading": "ANCIENT COOLING", "body": "Carbonatite rocks cooled between 94 and 104 million years ago beneath the surface."}
    assert M.route_carousel_archetype(card, 4, 7) == "colossal_stat"
    _, lay = M()._render_carousel_slide("colossal_stat", photos[0], photos[1], card, 4, 7, {}, "", False, subject="S")
    stat = [p.text for p in lay.plates["stat"]]
    assert stat[0] == "94–104" and "94" not in stat[1:] and " ".join(stat[1:]) == "MILLION YEARS AGO"
    assert lay.meta["stat"] == "94–104 MILLION YEARS AGO" and not lay.problems()
    # the unit's lockup sits beside the figure, its foot on the figure's baseline
    fig, last = lay.plates["stat"][0], lay.plates["stat"][-1]
    assert last.box[0] > fig.box[2] and abs(last.xy[1] - fig.xy[1]) <= 1


def test_comparison_routing_takes_whole_words_and_an_explicit_contrast() -> None:
    sub = {"heading": "MAGMATIC UPLIFT", "body": "Subsurface igneous intrusion deformed overlying sedimentary rock."}
    beneath = {"heading": "HIDDEN DEPTHS", "body": "It formed beneath the surface over ages."}
    canvas = {"heading": "CANVAS", "body": "Painted on canvas by hand."}                        # "vs" inside a word
    assert {M.route_carousel_archetype(c, 3, 7) for c in (sub, beneath, canvas)} <= {"immersive", "editorial_split"}
    assert M.route_carousel_archetype({"heading": "ROME VS. NOW", "body": "Two ages."}, 2, 7) == "comparison"


def test_facebook_prefers_a_single_photo_over_a_two_photo_stack(tmp_path, photos) -> None:
    folder = tmp_path / "carousel_fb"
    folder.mkdir()
    slides = []
    for i in range(7):
        p = folder / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (8, 8)).save(p)
        lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
        arch = "comparison" if i == 1 else "immersive"
        still_layout.save(p, lay, {"archetype": arch, "photo": str(tmp_path / f"x{i}.jpg"),
                                   "photo_next": str(tmp_path / f"x{i + 1}.jpg")})
        slides.append(str(p))
    picked = facebook_pick(slides, 4)
    assert slides[1] not in picked and picked == [slides[0], slides[2], slides[3], slides[6]]


def test_delivered_ratios_are_measured_again_after_a_platform_reencode(photos, tmp_path) -> None:
    im, lay = M()._render_carousel_slide("immersive", photos[1], photos[0], CARD, 1, 7, {}, "", True, subject="S")
    path = tmp_path / "slide.jpg"
    M._save_slide(im, path)
    multi_format._delivered_contrast(lay, path)
    measured = [e for e in lay.elements if e.get("contrast") is not None]
    assert measured and all("contrast_platform" in e for e in measured)
    assert lay.meta["platform_contrast_low"] == [e["name"] for e in measured if e.get("platform_low")]
    assert platform_encode(im).size == im.size


def test_kickers_and_the_masthead_are_planned_with_a_margin(photos) -> None:
    assert multi_format.KICKER_TARGET == multi_format.MASTHEAD_TARGET == 5.5
    _, lay = M()._render_carousel_slide("immersive", photos[1], photos[0], CARD, 1, 7, {}, "", True, subject="S")
    assert lay.plates["kicker"][0].contrast >= 5.4 and lay.plates["header_brand"][0].contrast >= 5.4


def test_the_masthead_gets_a_top_edge_gradient_only_where_the_photo_needs_it(tmp_path) -> None:
    dark = Image.new("RGB", (1440, 1800), (30, 34, 40))
    box = (107, 93, 800, 133)
    assert masthead_scrim(dark, box, brand.BONE) is dark                     # dark ground: untouched
    sky = Image.new("RGB", (1440, 1800), (120, 170, 225))
    out = np.asarray(masthead_scrim(sky, box, brand.BONE, feather=400).convert("L"), dtype=np.float32)
    col = out[:, 1300]                                                     # the whole width, not a box
    assert col[0] < col[133] < col[500] and col[700] == pytest.approx(np.asarray(sky.convert("L"))[700, 0], abs=1)
    assert np.abs(np.diff(col[:600])).max() < 3                             # a gradient, no edge
    assert multi_format._contrast(Image.fromarray(out.astype(np.uint8)).convert("RGB"), (107, 87, 800, 139), brand.BONE) > 0


def test_the_closing_slide_puts_its_gold_on_an_ink_band_over_a_warm_photo(tmp_path, photos) -> None:
    warm = _photo(tmp_path / "gold.jpg", (196, 150, 60))
    cool = _photo(tmp_path / "blue.jpg", (70, 90, 120))
    assert warm_ground(Image.open(warm), (0, 0, 1200, 1600)) and not warm_ground(Image.open(cool), (0, 0, 1200, 1600))
    card = {"heading": "EARTH'S HIDDEN MARVELS", "body": "What wonders do you think look best from space?"}
    _, lay = M()._render_carousel_slide("debate", warm, photos[0], card, 6, 7, {}, "Send this to a friend.", False, subject="S")
    assert lay.meta.get("ink_band") and not lay.problems()
    gold = lay.plates["heading"][-1]
    assert gold.fill == brand.GOLD and gold.contrast >= 7
    _, plain = M()._render_carousel_slide("debate", cool, photos[0], card, 6, 7, {}, "Send this.", False, subject="S")
    assert not plain.meta.get("ink_band")


def test_display_typography_changes_glyphs_never_words() -> None:
    assert typeset("EARTH'S HIDDEN MARVELS") == "EARTH’S HIDDEN MARVELS"
    assert typeset("94 - 104 million years") == "94–104 million years"
    assert typeset("THE 25-MILE EYE") == "THE 25-MILE EYE"                    # an adjective, no span
    assert typeset("2025-11-02") == "2025-11-02"                              # a date, no span
    assert typeset('He said "no"...') == "He said “no”…"
    assert typeset("the '60s") == "the ’60s"


def test_slides_and_frames_draw_the_typeset_glyphs(photos, tmp_path) -> None:
    card = {"heading": "EARTH'S MARVELS", "body": "Rocks cooled 94 - 104 million years ago..."}
    _, lay = M()._render_carousel_slide("editorial_split", photos[0], photos[1], card, 3, 7, {}, "", False, subject="S")
    drawn = " ".join(p.text for ps in lay.plates.values() for p in ps)
    assert "EARTH’S" in drawn and "'" not in drawn and "94–104" in drawn
    _, st = _frame(tmp_path, {"kicker": "IT'S OLD", "line1": "EARTH'S EYE", "line2": "SEEN...", "body": "It's 94 - 104 Ma."}, 0)
    drawn = " ".join(p.text for ps in st.plates.values() for p in ps)
    assert "'" not in drawn and "…" in drawn and "94–104" in drawn


# ---------------------------------------------------------------- Facebook collage gate

def test_the_collage_crop_is_the_tiles_centre_crop() -> None:
    assert collage_crop((1440, 1800), 1.0) == (0, 180, 1440, 1620)
    assert collage_crop((1440, 1800), 2 / 3) == (120, 0, 1320, 1800)
    assert collage_crop((1440, 1800), 0.8) == (0, 0, 1440, 1800)


def test_the_collage_report_names_each_cut_and_draws_its_board(tmp_path) -> None:
    folder = tmp_path / "carousel_fb"
    folder.mkdir()
    slides = []
    for i in range(4):
        p = folder / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (1440, 1800), (40, 40, 40)).save(p)
        lay = still_layout.Layout("carousel", (1440, 1800), (0, 0, 1440, 1800))
        lay.add("heading", (300, 900, 1100, 1000))
        lay.add("body", (60 if i == 0 else 300, 1100, 1100, 1300))   # the cover's body runs past its 2:3 tile
        still_layout.save(p, lay, {"archetype": "immersive"})
        slides.append(p)
    board = tmp_path / "collage.jpg"
    rep = facebook_collage_report(slides, board)
    assert rep["layout"] == "4 photos" and len(rep["tiles"]) == 4
    assert rep["problems"] == ["carousel_slide_01.jpg: the collage tile cuts its body"]
    assert Image.open(board).size == (1080, 1080)
    assert facebook_collage_report(slides[:3])["layout"] is None


# ---------------------------------------------------------------- Round 3 fix (2026-11-02): the content frame

ONE_LINE = {"heading": "ANCIENT COOLING", "body": "Carbonatite rocks cooled slowly beneath the desert surface."}
TWO_LINE = "IMPACT THEORY DISPROVED BY SCIENTISTS"
BODIES = {
    1: "Rocks cooled slowly.",
    2: "Carbonatite rocks cooled slowly beneath the desert surface.",
    4: "Carbonatite rocks cooled very slowly beneath the desert surface over a span of many millions of years "
       "while the rings formed.",
}
# Round 2's content frame, canvas px (kicker baseline, heading first-line baseline), as committed in fb18d0a: the
# two-line deck frame must keep it value for value (Facebook's diagram slide lost ~10% of its drawing when it moved).
ROUND2_FRAME = {False: (939, 1132), True: (922, 1129)}


def _slide(photos, card, archetype="editorial_split", facebook=False, frame=None):
    kw = {} if frame is None else {"heading_frame": frame}
    return M()._render_carousel_slide(archetype, photos[0], photos[1], card, 2, 7, {}, "", facebook, subject="S",
                                      **kw)[1]


def _ink(plate, edge):
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return d.textbbox(plate.xy, plate.text, font=plate.font, anchor="ls")[edge]


@pytest.mark.parametrize("facebook", [False, True])
def test_the_two_line_deck_frame_is_round_twos_frame_and_shared_by_one_and_two_line_headings(photos, facebook) -> None:
    one = _slide(photos, ONE_LINE, facebook=facebook, frame=2)
    two = _slide(photos, dict(ONE_LINE, heading=TWO_LINE), facebook=facebook, frame=2)
    assert len(one.plates["heading"]) == 1 and len(two.plates["heading"]) == 2
    for lay in (one, two):
        b = lay.meta["baselines"]
        assert (b["kicker"], b["heading"]) == ROUND2_FRAME[facebook] and b["heading_frame"] == 2
        assert round(lay.plates["heading"][0].xy[1]) == b["heading"]
    assert M()._content_baselines(PX, facebook, 2) == ROUND2_FRAME[facebook]
    # a slide drawn alone sits on the two-line frame (round 2's default)
    assert _slide(photos, ONE_LINE, facebook=facebook).meta["baselines"] == one.meta["baselines"]


@pytest.mark.parametrize("facebook", [False, True])
def test_a_one_line_deck_frame_reserves_no_second_heading_line(photos, facebook) -> None:
    one = _slide(photos, ONE_LINE, facebook=facebook, frame=1)
    old = _slide(photos, ONE_LINE, facebook=facebook, frame=2)
    b = one.meta["baselines"]
    assert b["heading_frame"] == 1 and b["heading"] == M()._content_frame(PX, facebook, 1)["heading_last"]
    assert b["heading"] > old.meta["baselines"]["heading"] and b["kicker"] > old.meta["baselines"]["kicker"]
    gap = _ink(one.plates["body"][0], 1) - _ink(one.plates["heading"][-1], 3)
    gap_old = _ink(old.plates["body"][0], 1) - _ink(old.plates["heading"][-1], 3)
    step = one.plates["body"][1].xy[1] - one.plates["body"][0].xy[1]
    assert gap < 2.5 * step + one.plates["body"][0].font.size * 2.0 < gap_old
    bottom = PX(M.KEY_BOTTOM - M.KEY_GAP)
    assert max(_ink(p, 3) for p in one.plates["body"]) == bottom


@pytest.mark.parametrize("lines", sorted(BODIES))
def test_every_body_length_meets_the_one_line_frame_and_ends_on_the_shared_bottom_line(photos, lines) -> None:
    lay = _slide(photos, dict(ONE_LINE, body=BODIES[lines]), frame=1)
    assert len(lay.plates["body"]) == lines and not lay.problems()
    assert "baselines" in lay.meta          # on the fixed frame, not bottom-aligned
    assert lay.meta["baselines"] == _slide(photos, ONE_LINE, frame=1).meta["baselines"]
    bottom = PX(M.KEY_BOTTOM - M.KEY_GAP)
    assert max(_ink(p, 3) for p in lay.plates["body"]) == bottom


def test_the_one_line_frame_reserves_every_body_line_a_content_slide_draws() -> None:
    assert M.BODY_LINES_ONE_LINE_FRAME >= M.BODY_LINES
    assert M.BODY_LINES_TWO_LINE_FRAME == 3     # round 2's reservation, unchanged


def _deck(photos, tmp_path, headings, facebook, name="run"):
    cards = [{"heading": h, "body": "Carbonatite rocks cooled slowly beneath the desert surface."} for h in headings]
    payload = {"title": "Test", "carousel_slides": cards, "narration_script": "", "poll": {}}
    return M().generate_carousel_sequence(photos * 3, payload, tmp_path / name, facebook=facebook)


def _content(slides):
    recs = [still_layout.load(p) for p in slides]
    return [r for r in recs[1:-1] if r["recipe"]["archetype"] not in M.NON_CONTENT_ARCHETYPES]


def _kicker_foot(rec):
    return next(e["box"] for e in rec["elements"] if e["name"] == "kicker")[3]


@pytest.mark.parametrize("facebook", [False, True])
def test_a_deck_takes_one_frame_and_every_content_kicker_stays_on_one_line(photos, tmp_path, facebook) -> None:
    ones = ["Cover heading", "Ancient cooling", "Rock rings", "Desert eye", "Old stone", "Your turn"]
    mixed = ["Cover heading", "Ancient cooling", TWO_LINE, "Desert eye", "Old stone", "Your turn"]
    for name, headings, frame in (("ones", ones, 1), ("mixed", mixed, 2)):
        content = _content(_deck(photos, tmp_path, headings, facebook, name))
        assert len(content) >= 3
        assert {r["recipe"]["heading_frame"] for r in content} == {frame}
        feet = {_kicker_foot(r) for r in content}
        assert max(feet) - min(feet) <= 2, (name, feet)   # no jump between one- and two-line slides
        assert all(not r["problems"] for r in content)


@pytest.mark.parametrize("facebook", [False, True])
def test_a_redraw_from_the_recipe_lands_on_the_decks_frame(photos, tmp_path, facebook) -> None:
    slides = _deck(photos, tmp_path, ["Cover heading", "Ancient cooling", "Rock rings", "Desert eye", "Your turn"],
                   facebook)
    rec = still_layout.load(slides[2])
    assert rec["recipe"]["heading_frame"] == 1
    _, lay = M()._render_carousel_slide(rec["recipe"]["archetype"], Path(rec["recipe"]["photo"]),
                                         Path(rec["recipe"]["photo_next"]), rec["recipe"]["card"], 2, len(slides), {},
                                         "", facebook, subject=rec["recipe"]["subject"],
                                         heading_frame=rec["recipe"]["heading_frame"])
    assert lay.meta["baselines"]["heading_frame"] == 1
    multi_format.redraw_carousel_slide(slides[2], rec["recipe"])
    assert abs(_kicker_foot(still_layout.load(slides[2])) - _kicker_foot(rec)) <= 40   # tight, but the same frame


def _poster(path: Path) -> Path:
    from PIL import ImageDraw
    im = Image.new("RGB", (1080, 1350), (14, 14, 18))
    ImageDraw.Draw(im).rectangle((140, 160, 940, 1180), fill=(200, 170, 90))
    im.save(path)
    return path


def _diagram(tmp_path, heading, facebook, frame):
    from src.multi_format import _render_state
    poster = _poster(tmp_path / "graphic_section.jpg")
    gen = M()
    cw, chh = M.CAROUSEL_SIZE
    card = {"heading": heading, "body": "Dug into soft volcanic tuff, roughly 280 feet at its deepest."}
    lay = still_layout.Layout("carousel", (cw, chh), gen._zone(cw, chh))
    prev = (getattr(_render_state, "facebook", False), getattr(_render_state, "heading_frame", 2))
    _render_state.facebook, _render_state.heading_frame = facebook, frame
    try:
        gen._render_diagram(poster, card, 2, cw, chh, lay=lay)
    finally:
        _render_state.facebook, _render_state.heading_frame = prev
    return lay


def _art_h(lay):
    box = lay.meta["diagram"]["image_box"]
    return box[3] - box[1]


@pytest.mark.parametrize("facebook", [False, True])
def test_the_diagram_takes_the_room_of_a_one_line_deck_frame(tmp_path, facebook) -> None:
    one = _diagram(tmp_path, "Not built", facebook, 1)
    two = _diagram(tmp_path, "Not built", facebook, 2)
    assert len(one.plates["heading"]) == 1
    assert _art_h(one) > _art_h(two) * 1.15            # the line the deck does not use goes to the drawing
    assert one.meta["baselines"]["kicker"] == M()._content_baselines(PX, facebook, 1)[0]
    for lay in (one, two):                              # the drawing stays clear of the kicker
        kicker_top = min(_ink(p, 1) for p in lay.plates["kicker"])
        assert lay.meta["diagram"]["image_box"][3] < kicker_top and not lay.problems()


@pytest.mark.parametrize("facebook", [False, True])
def test_a_two_line_diagram_heading_keeps_round_twos_frame_and_its_drawing(tmp_path, facebook) -> None:
    # Derinkuyu's Facebook slide 3, "CARVED, / NOT BUILT": its kicker on round 2's line, its drawing no smaller than
    # round 2 drew it (602 canvas px tall for this poster on Facebook, 619 on Instagram, at HEAD fb18d0a + round 3).
    lay = _diagram(tmp_path, "Carved by hand, never built of stone", facebook, 2)
    assert len(lay.plates["heading"]) == 2
    assert (lay.meta["baselines"]["kicker"], lay.meta["baselines"]["heading"]) == ROUND2_FRAME[facebook]
    assert _art_h(lay) >= (602 if facebook else 619)
    assert _art_h(lay) == _art_h(_diagram(tmp_path, "Not built", facebook, 2))   # one frame, one drawing size
    # a two-line heading on a one-line deck frame (measured otherwise) never overruns: it takes the two-line frame
    over = _diagram(tmp_path, "Carved by hand, never built of stone", facebook, 1)
    assert over.meta["baselines"]["heading_frame"] == 2 and not over.problems()


def test_the_facebook_copy_writes_its_collage_report_marked_advisory(photos, tmp_path) -> None:
    cards = [{"heading": f"Heading {i}", "body": "A short body line for the slide."} for i in range(6)]
    payload = {"title": "Test", "carousel_slides": cards, "narration_script": "", "poll": {}}
    slides = M().generate_carousel_sequence(photos * 3, payload, tmp_path / "run", facebook=True)
    rep = json.loads((slides[0].parent / multi_format.COLLAGE_REPORT).read_text())
    assert rep["advisory"] is True
    assert rep["posted"] == [Path(p).name for p in facebook_pick([str(p) for p in slides])]
    assert rep["layout"] == f"{len(rep['posted'])} photos" and (slides[0].parent / "collage_board.jpg").is_file()
    assert "ADVISORY" in multi_format.write_collage_report.__doc__
    # the Instagram copy writes none
    ig = M().generate_carousel_sequence(photos * 3, payload, tmp_path / "run", facebook=False)
    assert not (ig[0].parent / multi_format.COLLAGE_REPORT).exists()


def test_the_platform_contrast_flags_are_kept_per_element_in_still_layout(photos, tmp_path) -> None:
    cards = [{"heading": f"Heading {i}", "body": "A short body line for the slide."} for i in range(5)]
    payload = {"title": "Test", "carousel_slides": cards, "narration_script": "", "poll": {}}
    slides = M().generate_carousel_sequence(photos * 3, payload, tmp_path / "run", facebook=True)
    for p in slides:
        rec = still_layout.load(p)
        measured = [e for e in rec["elements"] if "contrast_platform" in e]
        assert measured
        assert all(bool(e.get("platform_low")) == (e["contrast_platform"] < brand.TEXT_CONTRAST) for e in measured)
