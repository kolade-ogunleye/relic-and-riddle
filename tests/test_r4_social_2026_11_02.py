"""Round 4 panel (2026-11-02), social workstream: the Facebook cover's inset for its 2:3 hero tile, the gallery's
strength and standalone keys, the closing slide's share voice, an even body rag, the locator map as the deck's drawn
slide, Instagram's swipe strip; Stories: the poll frame's own headline, the yes/no poll check, a span as the reveal's
hero, the inline qualifier, and the ground-aware scrim."""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src import brand, multi_format, still_layout
from src.multi_format import (
    MultiFormatGenerator,
    carousel_diagram,
    collage_crop,
    diagram_poster,
    even_rag,
    facebook_pick,
    gold_clash,
    ground_energy,
    is_map_poster,
    poll_problems,
    slide_alt_text,
    slide_strength,
    standalone_problems,
    story_figure,
    swipe_strip,
)
from src.typography import get_font

M = MultiFormatGenerator
CW, CH = M.CAROUSEL_SIZE


def _photo(path: Path, colour, size=(1200, 1600)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def _noise(path: Path, size=(1080, 1920), amp=90, base=(120, 110, 95), seed=1) -> Path:
    rng = np.random.default_rng(seed)
    a = np.clip(np.array(base, np.float32) + rng.normal(0, amp, (size[1], size[0], 1)), 0, 255).astype(np.uint8)
    Image.fromarray(np.repeat(a, 3, axis=2) if a.shape[2] == 1 else a).save(path)
    return path


def _story(tmp_path, slide, i, poll=None, photo=None):
    photo = photo or _photo(tmp_path / f"s{i}.jpg", (120, 110, 95), (1080, 1920))
    return M()._render_story_frame(photo, slide, i, 3, poll or {})


COVER = {"heading": "THE EYE OF THE SAHARA",
         "body": "A concentric formation in Mauritania that has long fascinated observers from orbit."}


# ---------------------------------------------------------------- Facebook

def test_the_facebook_cover_keeps_its_text_inside_the_hero_tiles_crop(tmp_path) -> None:
    photo = _photo(tmp_path / "c.jpg", (90, 100, 120))
    _, fb = M()._render_carousel_slide("cover", photo, photo, COVER, 0, 7, {}, "", True)
    crop = collage_crop((CW, CH), 2 / 3)                         # the 4-photo collage's tall left tile
    gutter = 60
    for e in fb.elements:
        if e["name"] in ("heading", "body"):
            assert e["box"][0] >= crop[0] + gutter - 2 and e["box"][2] <= crop[2] - gutter + 2, e
    brand_box = next(e["box"] for e in fb.elements if e["name"] == "header_brand")
    assert brand_box[0] >= crop[0]                               # the masthead's diamond inside the tile too
    assert not fb.problems()
    # Instagram's cover keeps the 80 px design margin
    _, ig = M()._render_carousel_slide("cover", photo, photo, COVER, 0, 7, {}, "", False)
    assert min(e["box"][0] for e in ig.elements if e["name"] == "heading") < crop[0]


def _rec(archetype="immersive", heading="H", body="A body.", elements=()):
    return {"recipe": {"archetype": archetype, "card": {"heading": heading, "body": body}},
            "elements": [{"name": n, "box": [0, 0, 1, 1]} for n in elements]}


def test_slide_strength_reads_figures_drawings_and_myth_busts() -> None:
    assert slide_strength(_rec("colossal_stat")) == 1
    assert slide_strength(_rec("diagram")) == 1
    assert slide_strength(_rec(elements=("stat",))) == 1
    assert slide_strength(_rec(heading="IMPACT THEORY DISPROVED")) == 1
    assert slide_strength(_rec(body="Initially considered an impact crater, the theory was disproved in 1969.")) == 1
    assert slide_strength(_rec(heading="ORBITAL LANDMARK", body="Visible from space.")) == 0


def test_standalone_problems_flag_a_body_that_leans_on_another_slide() -> None:
    assert standalone_problems(_rec(body="It was carved by hand."))
    assert standalone_problems(_rec(body="These rings formed slowly."))
    assert standalone_problems(_rec(heading="280"))
    assert standalone_problems(_rec(heading="ORBITAL LANDMARK", body="Visible from space, the formation ...")) == []


def _slides(tmp_path, specs):
    """Saved slides with layout records: specs of (archetype, heading, body, photo colour)."""
    out = []
    for i, (arch, heading, body, colour) in enumerate(specs):
        photo = _photo(tmp_path / f"p{i}.jpg", colour)
        path = tmp_path / f"carousel_slide_{i + 1:02d}.jpg"
        Image.new("RGB", (8, 8)).save(path)
        lay = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
        still_layout.save(path, lay, {"archetype": arch, "photo": str(photo), "photo_next": str(photo),
                                      "card": {"heading": heading, "body": body}}, 1)
        out.append(str(path))
    return out


def test_facebook_picks_the_strongest_slides_before_photo_novelty(tmp_path) -> None:
    slides = _slides(tmp_path, [
        ("cover", "THE EYE OF THE SAHARA", "Cover.", (10, 10, 10)),
        ("immersive", "ORBITAL LANDMARK", "Visible from space.", (20, 20, 20)),
        ("editorial_split", "IMPACT THEORY DISPROVED", "Initially considered an impact crater.", (30, 30, 30)),
        ("immersive", "MAGMATIC UPLIFT", "Subsurface intrusion.", (40, 40, 40)),
        ("colossal_stat", "ANCIENT COOLING", "Between 94 and 104 million years ago.", (50, 50, 50)),
        ("immersive", "GLOBAL HERITAGE", "Named in 2022.", (60, 60, 60)),
        ("debate", "YOUR TAKE", "What do you think?", (70, 70, 70)),
    ])
    picked = [Path(x).name for x in facebook_pick(slides)]
    assert picked == ["carousel_slide_01.jpg", "carousel_slide_03.jpg", "carousel_slide_05.jpg",
                      "carousel_slide_07.jpg"]
    # the gallery's five: the cover, the closing slide, the two strong slides, then the earliest other
    five = [Path(x).name for x in facebook_pick(slides, multi_format.FACEBOOK_GALLERY)]
    assert five == ["carousel_slide_01.jpg", "carousel_slide_02.jpg", "carousel_slide_03.jpg",
                    "carousel_slide_05.jpg", "carousel_slide_07.jpg"]


def test_a_slide_that_does_not_stand_alone_is_passed_over_even_when_strong(tmp_path) -> None:
    slides = _slides(tmp_path, [
        ("cover", "C", "Cover.", (10, 10, 10)),
        ("colossal_stat", "280 FT", "It reached about 280 feet deep.", (20, 20, 20)),
        ("immersive", "A CITY BELOW", "Stables and chapels.", (30, 30, 30)),
        ("immersive", "A SECOND", "Wells and air.", (40, 40, 40)),
        ("debate", "YOUR TAKE", "Why?", (50, 50, 50)),
    ])
    picked = [Path(x).name for x in facebook_pick(slides)]
    assert "carousel_slide_02.jpg" not in picked and len(picked) == 4


# ---------------------------------------------------------------- the closing slide and the body rag

def test_the_share_line_is_bold_with_a_full_line_of_air_above_its_rule(tmp_path) -> None:
    """Bold (SHARE_WEIGHT 700), one step over the 600 every other line is set in: the documented deviation from
    the panel's SemiBold (round 4 review: the test was named for SemiBold while it checked Bold)."""
    photo = _photo(tmp_path / "d.jpg", (60, 60, 70))
    card = {"heading": "EARTH'S HIDDEN MARVELS", "body": "What natural wonders on Earth do you think look most magnificent?"}
    share = "Send this to a space enthusiast you know, and follow for the next natural marvel."
    _, lay = M()._render_carousel_slide("debate", photo, photo, card, 6, 7, {}, share, False)
    shares, bodies = lay.plates["share"], lay.plates["body"]
    def ink(font):   # a variable font's weight, read from the glyph it draws
        return sum(font.getmask("H"))
    bold = get_font("Montserrat", shares[0].font.size, weight=multi_format.SHARE_WEIGHT)
    assert all(ink(p.font) == ink(bold) for p in shares)
    plain = get_font("Montserrat", shares[0].font.size)            # the question's weight, at the share's size
    assert ink(plain) < ink(bold) and ink(get_font("Montserrat", 40)) == ink(get_font("Montserrat", 40, weight=600))
    px = M._px(CW)
    step = round(px.exact(M.BODY_SPEC[0]) * M.BODY_LEADING)
    # the question's last baseline to the share line's first cap: rule gap (a full body line + 16) + rule + 26
    gap = shares[0].box[1] - bodies[-1].xy[1]
    assert gap >= step + px(16) + px(6) + px(26) - 6
    assert not lay.problems()


def test_even_rag_evens_a_short_first_line_and_keeps_the_line_count() -> None:
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = get_font("Montserrat", 48)
    lines = ["Stone", "rings cooled slowly under", "the open desert sky."]
    out = even_rag(d, lines, f, 1200, 34)
    assert len(out) == 3 and " ".join(out) == " ".join(lines)
    ws = [d.textlength(x, font=f) for x in out]
    assert max(ws) - min(ws) < max(d.textlength(x, font=f) for x in lines) - min(d.textlength(x, font=f) for x in lines)
    assert all(len(x) <= 34 for x in out)
    # already even: unchanged; a glued figure is never split
    even = ["Visible from space, this", "concentric formation serves", "as a prominent desert landmark."]
    assert even_rag(d, even, f, 1200, 34) == even
    # round 4 review: a line that ends on "the" is no longer even enough to keep (see dangles)
    dangling = ["Visible from space, the", "concentric formation serves as", "a prominent desert landmark."]
    out = even_rag(d, dangling, f, 1200, 34)
    assert out[0] == "Visible from space," and not any(multi_format.dangles(x) for x in out[:-1])
    glued = ["It is", "280 feet deep under the old town"]
    assert all("280 feet" in x or "280" not in x for x in even_rag(d, glued, f, 1200, 34))


# ---------------------------------------------------------------- the locator map as the drawn slide

def _map_poster(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (1080, 1920), brand.INK2)
    d = ImageDraw.Draw(im)
    d.ellipse((440, 860, 640, 1060), fill=brand.GOLD)            # the pin, at the poster's centre
    path = folder / "graphic_map.jpg"
    im.save(path)
    return path


def test_the_map_poster_is_the_diagram_only_when_no_explanatory_graphic_exists(tmp_path) -> None:
    graphics = tmp_path / "graphics"
    poster = _map_poster(graphics)
    assert diagram_poster(tmp_path) == ("map", poster) and is_map_poster(poster)
    section = graphics / "graphic_section.jpg"
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(section)
    assert diagram_poster(tmp_path)[0] == "section"              # a section, scale or route poster comes first
    cards = [{"heading": "C", "body": ""}, {"heading": "A", "body": "x"},
             {"heading": "SEEN FROM ORBIT", "body": "Visible from space in the desert."},
             {"heading": "B", "body": "y"}, {"heading": "Q", "body": "?"}]
    section.unlink()
    chosen = carousel_diagram(tmp_path / "run2", cards, [poster])
    assert chosen == (2, poster)


def test_the_map_slide_is_full_bleed_with_its_pin_in_the_upper_part(tmp_path) -> None:
    poster = _map_poster(tmp_path / "graphics")
    card = {"heading": "SEEN FROM ORBIT", "body": "Visible from space in the Mauritanian desert."}
    im, lay = M()._render_carousel_slide("diagram", poster, poster, card, 2, 7, {}, "", False, subject="S")
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    gold = (np.abs(a - np.array(brand.GOLD)).max(axis=2) < 40)
    ys = np.nonzero(gold[:, CW // 2 - 40:CW // 2 + 40].any(axis=1))[0]
    pin_y = (ys.min() + ys.max()) / 2
    assert abs(pin_y - multi_format.MAP_FOCUS[1] * CH) < 30
    meta = lay.meta["diagram"]
    assert meta["kind"] == "map" and meta["image_box"] == [0, 0, CW, CH]
    heading_top = min(e["box"][1] for e in lay.elements if e["name"] == "heading")
    assert pin_y < heading_top and not lay.problems()
    path = tmp_path / "carousel_slide_03.jpg"
    im.save(path)
    still_layout.save(path, lay, {"archetype": "diagram", "photo": str(poster), "photo_next": str(poster),
                                  "card": card}, 1)
    assert slide_alt_text(path).startswith("A locator map from the video.")


# ---------------------------------------------------------------- Instagram's swipe strip

def test_instagrams_swipe_strip_runs_across_the_seam_and_facebook_draws_none(tmp_path) -> None:
    photos = [_photo(tmp_path / f"q{i}.jpg", c) for i, c in enumerate([(60, 70, 90), (80, 70, 60), (70, 80, 60)])]
    payload = {"carousel_slides": [
        COVER,
        {"heading": "ORBITAL LANDMARK", "body": "Visible from space, the concentric formation serves as a landmark."},
        {"heading": "MAGMATIC UPLIFT", "body": "Subsurface igneous intrusion deformed the rock into rings."},
        {"heading": "YOUR TAKE", "body": "What do you think shaped it?"}],
        "title": "T", "narration_script": ""}
    ig = M().generate_carousel_sequence(photos * 2, payload, tmp_path / "run")
    r1, r2 = (still_layout.load(p)["recipe"]["strip"] for p in ig[:2])
    assert r1["side"] == "right" and r2["side"] == "left"
    assert r1["box"][1:4:2] == r2["box"][1:4:2]                  # one height across the seam
    assert r1["box"][2] == CW and r2["box"][0] == 0
    assert 60 <= r1["box"][2] - r1["box"][0] <= 120
    a1 = np.asarray(Image.open(ig[0]).convert("RGB"), dtype=np.int16)
    y = (r1["box"][1] + r1["box"][3]) // 2
    assert np.abs(a1[y, CW - 20] - np.array(brand.GOLD)).max() < 40
    a2 = np.asarray(Image.open(ig[1]).convert("RGB"), dtype=np.int16)
    assert np.abs(a2[y, 10] - np.array(brand.GOLD)).max() < 40
    assert still_layout.load(ig[2])["recipe"].get("strip") is None
    fb = M().generate_carousel_sequence(photos * 2, payload, tmp_path / "run", facebook=True)
    assert all(still_layout.load(p)["recipe"].get("strip") is None for p in fb)


def test_slide_ones_strip_is_skipped_where_its_text_reaches_the_edge(tmp_path) -> None:
    lay = still_layout.Layout("carousel", (CW, CH), (0, 0, CW, CH))
    lay.add("heading", (100, 900, CW - 10, 1000))
    im = Image.new("RGB", (CW, CH), (30, 30, 30))
    out = swipe_strip(im, lay, {"side": "right", "y0": 950, "y1": 955})
    assert "skipped" in lay.meta["swipe_strip"] and np.asarray(out).max() == 30


# ---------------------------------------------------------------- Stories

POLL = {"question": "Best way to view the Eye?", "options": ["From Earth orbit", "Walking the ridges"]}


def test_the_poll_frame_sets_its_own_lines_and_leaves_the_question_to_the_sticker(tmp_path) -> None:
    slide = {"kicker": "SURFACE EXPLORATION", "line1": "ORBIT OR GROUND", "line2": "HOW TO VIEW IT",
             "body": "Best way to view the Eye?"}
    _, lay = _story(tmp_path, slide, 2, POLL)
    titles = " ".join(p.text for n in ("title_1", "title_2") for p in lay.plates.get(n, []))
    assert titles == "ORBIT OR GROUND HOW TO VIEW IT" and "EYE?" not in titles
    assert lay.meta["poll_sticker_room"] and not lay.problems()
    # a slide with no lines of its own still asks the poll's question
    _, bare = _story(tmp_path, dict(slide, line1="", line2=""), 2, POLL)
    assert "VIEW THE EYE?" in " ".join(p.text for n in ("title_1", "title_2") for p in bare.plates.get(n, []))


def test_poll_problems_names_a_yes_no_poll() -> None:
    assert poll_problems({"question": "Would you hide underground for weeks?",
                          "options": ["Yes, safety first", "No, I need daylight"]}) == [
        "the options answer yes or no", "the question asks for yes or no"]
    assert poll_problems(POLL) == []
    assert poll_problems({"question": "Was it impact or uplift?", "options": ["Impact", "Uplift"]}) == []


def test_a_yes_no_poll_is_recorded_for_the_operator(tmp_path) -> None:
    photos = [_photo(tmp_path / f"p{i}.jpg", (90, 80, 70), (1080, 1920)) for i in range(3)]
    payload = {"story_slides": [
        {"kicker": "K", "line1": "ONE MAN", "line2": "KNOCKED", "body": "Short."},
        {"kicker": "K", "line1": "A CITY", "line2": "BELOW", "body": "Short."},
        {"kicker": "K", "line1": "WOULD YOU HIDE", "line2": "DOWN THERE?", "body": "Context."}],
        "poll": {"question": "Would you hide?", "options": ["Yes", "No"]}}
    M().generate_story_sequence(photos[0], payload, tmp_path / "run", scene_images=photos)
    manifest = json.loads((tmp_path / "run" / "companion_posting.json").read_text(encoding="utf-8"))
    assert manifest["story_poll_sticker"]["problems"]


def test_a_span_is_the_reveals_hero_whole() -> None:
    assert story_figure("ANCIENT ROCKS", "94–104 MILLION YEARS OLD") == {
        "number": "94–104", "unit": "MILLION YEARS OLD", "qualifier": "", "title": "ANCIENT ROCKS", "span": True}
    assert story_figure("A CITY", "BETWEEN 8 AND 9 KM WIDE")["number"] == "8–9"
    assert story_figure("THE", "ROCKS 94–104 MILLION YEARS OLD") is None     # words before it: the title stays
    assert story_figure("A CITY", "ABOUT 280 FT DEEP")["number"] == "280"           # a single figure as before


def test_the_reveal_draws_a_span_whole_and_never_its_first_number(tmp_path) -> None:
    slide = {"kicker": "WHEN", "line1": "ANCIENT ROCKS", "line2": "94-104 MILLION YEARS OLD", "body": "They cooled."}
    _, lay = _story(tmp_path, slide, 1)
    assert lay.plates["figure"][0].text == "94–104" and lay.meta["hero_figure"] == "94–104"
    assert lay.plates["figure_unit"][0].text == "MILLION YEARS OLD" and not lay.problems()


def test_the_qualifier_sits_inline_on_the_numerals_baseline(tmp_path) -> None:
    slide = {"kicker": "WHAT HE FOUND", "line1": "AN UNDERGROUND CITY", "line2": "~280 FEET DEEP", "body": "Stables."}
    _, lay = _story(tmp_path, slide, 1)
    q, fig, unit = (lay.plates[n][0] for n in ("figure_qualifier", "figure", "figure_unit"))
    assert q.xy[1] == fig.xy[1]                                  # one baseline: "ABOUT 280"
    assert q.box[2] < fig.box[0] and fig.xy[0] > multi_format.STORY_MARGIN
    assert abs(unit.xy[0] - fig.xy[0]) <= 1                       # the unit under the numeral it measures
    assert q.box[1] > fig.box[1]                                  # no qualifier line above the numeral
    assert not lay.problems()


def test_a_busy_ground_gets_a_deeper_scrim_and_a_flat_one_does_not(tmp_path) -> None:
    slide = {"kicker": "ORBITAL MYSTERY", "line1": "THE DESERT EYE", "line2": "SEEN FROM SPACE",
             "body": "A colossal concentric formation marks the desert."}
    _, flat = _story(tmp_path, slide, 0)
    assert flat.ground["scrim_target"] == multi_format.STORY_SCRIM_STEPS[0]
    busy = _noise(tmp_path / "busy.jpg")
    _, lay = _story(tmp_path, slide, 0, photo=busy)
    assert lay.ground["scrim_target"] < multi_format.STORY_SCRIM_STEPS[0]
    assert lay.meta["ground"] == lay.ground and not lay.problems()


def test_gold_type_on_a_gold_ground_is_shaded_or_set_in_bone(tmp_path) -> None:
    im = Image.new("RGB", (400, 200), (230, 170, 40))
    assert gold_clash(im, (50, 50, 350, 150)) > 0.9
    assert gold_clash(Image.new("RGB", (400, 200), (40, 80, 160)), (50, 50, 350, 150)) == 0
    assert ground_energy(Image.new("RGB", (400, 200), (90, 90, 90)), (50, 50, 350, 150)) == 0
    slide = {"kicker": "ORBITAL MYSTERY", "line1": "THE DESERT EYE", "line2": "SEEN FROM SPACE", "body": "Short."}
    photo = _photo(tmp_path / "gold.jpg", (250, 190, 50), (1080, 1920))
    _, lay = _story(tmp_path, slide, 0, photo=photo)
    g = lay.ground
    assert g["gold_to_bone"] or g["gold_clash"] < multi_format.GOLD_CLASH_SHARE
    if g["gold_to_bone"]:
        assert all(p.fill == brand.BONE for p in lay.plates["title_2"])
    assert not lay.problems()


def test_the_share_weight_is_one_step_above_the_text_weight() -> None:
    assert multi_format.SHARE_WEIGHT == 700 > multi_format.STORY_BODY_WEIGHT == 600


# ---------------------------------------------------------------- round 4 review fixes (2026-11-02)

RICHAT = [COVER,
          {"heading": "ORBITAL LANDMARK", "body": "Visible from space, the concentric formation serves as a prominent desert landmark."},
          {"heading": "IMPACT THEORY DISPROVED", "body": "Initially considered an impact crater, the theory was disproved in 1969."},
          {"heading": "MAGMATIC UPLIFT", "body": "Subsurface igneous intrusion deformed overlying sedimentary rock into concentric rings."},
          {"heading": "ANCIENT COOLING", "body": "Carbonatite rocks cooled between 94 and 104 million years ago beneath the surface."},
          {"heading": "YOUR TAKE", "body": "What do you think?"}]


def test_the_map_goes_on_the_slide_about_place_never_on_an_unrelated_one(tmp_path) -> None:
    poster = _map_poster(tmp_path / "graphics")
    assert carousel_diagram(tmp_path / "a", RICHAT, [poster]) == (1, poster)     # ORBITAL LANDMARK, not the myth-bust
    nothing = [dict(c, body="Rocks.") if i else c for i, c in enumerate(RICHAT)]
    nothing = [dict(c, heading="ROCKS") if i else c for i, c in enumerate(nothing)]
    assert carousel_diagram(tmp_path / "b", nothing, [poster]) is None           # no slide about place: no map slide
    # a tie between a DIAGRAM_SLIDES slot and slide 2 goes to the slot
    tie = [COVER, {"heading": "A", "body": "In the desert."}, {"heading": "B", "body": "Seen from the desert."},
           {"heading": "C", "body": "x"}, {"heading": "Q", "body": "?"}]
    assert carousel_diagram(tmp_path / "c", tie, [poster]) == (2, poster)
    # an explanatory drawing keeps its slot rule: the first slot when no slide speaks of it
    section = tmp_path / "graphics" / "graphic_section.jpg"
    Image.new("RGB", (1080, 1920), (20, 20, 20)).save(section)
    assert carousel_diagram(tmp_path / "d", nothing, [section])[0] == multi_format.DIAGRAM_SLIDES[0]


def _labelled_map(folder: Path, bar_y: int = 1035) -> Path:
    """A stand-in locator poster: a bone place label over the pin, and a bone scale bar with its figure below it,
    where the reviewed Derinkuyu stand-in had them (the bar on the kicker at MAP_FOCUS)."""
    folder.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (1080, 1920), (52, 56, 62))
    d = ImageDraw.Draw(im)
    for y in range(0, 1920, 90):                                    # coastline-like lines through the whole poster
        d.line([(0, y), (1080, y + 60)], fill=(150, 170, 190), width=3)
    d.text((540, 750), "DERINKUYU", font=get_font("Anton", 110), fill=brand.BONE, anchor="ms")
    d.ellipse((518, 938, 562, 982), fill=(90, 160, 230))
    d.line([(80, bar_y), (300, bar_y)], fill=(235, 235, 230), width=4)
    d.text((320, bar_y + 14), "250 MI", font=get_font("Montserrat", 40, weight=700), fill=brand.BONE, anchor="ls")
    path = folder / "graphic_map.jpg"
    im.save(path)
    return path


def test_the_map_slides_labels_clear_the_stack_and_none_is_half_under_the_foot(tmp_path) -> None:
    poster = _labelled_map(tmp_path / "graphics")
    card = {"heading": "IMPACT THEORY DISPROVED", "body": "Initially considered an impact crater, the theory was disproved in 1969."}
    im, lay = M()._render_carousel_slide("diagram", poster, poster, card, 2, 7, {}, "", False,
                                         subject="THE EYE OF THE SAHARA")
    meta = lay.meta["diagram"]
    assert meta["labels_clear"] and not lay.problems()
    stack_top = min(e["box"][1] for e in lay.elements if e["name"] in ("kicker", "heading", "body"))
    assert meta["stack_top"] == stack_top
    foot0, foot1 = meta["foot"]
    assert foot1 <= stack_top - M._px(CW)(multi_format.MAP_CLEAR)
    assert meta["labels"] and any(lb["place"] and lb["shown"] for lb in meta["labels"])
    for lb in meta["labels"]:
        y0, y1 = lb["box"][1], lb["box"][3]
        assert y1 <= foot0 or y0 >= foot1, lb                      # whole above the foot, or wholly under its ink
        assert not lb["shown"] or y1 < stack_top                   # nothing shown reaches the text
    # under the stack the map is all but ink: its lines no longer run through the body
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    body = next(e["box"] for e in lay.elements if e["name"] == "body")
    right = a[body[1]:body[3], body[2] + 20:CW - 40]
    assert np.abs(right - np.array(brand.INK)).max() < 24


def test_a_map_label_from_the_clips_record_is_measured_too(tmp_path) -> None:
    poster = _map_poster(tmp_path / "graphics")                      # no bone ink: only the record names the bar
    side = Path(str(poster.with_suffix(".mp4")) + ".contrast.json")
    side.write_text(json.dumps({"elements": [{"name": "bar_label", "text": "250 MI", "box": [300, 1120, 470, 1160]}]}),
                    encoding="utf-8")
    bands = multi_format.map_label_bands(poster, Image.open(poster).resize((CW, round(1920 * CW / 1080))),
                                         CW / 1080, M._px(CW))
    assert any(b[1] < 1120 * CW / 1080 < b[3] for b in bands)
    card = {"heading": "SEEN FROM ORBIT", "body": "Visible from space in the Mauritanian desert."}
    _, lay = M()._render_carousel_slide("diagram", poster, poster, card, 2, 7, {}, "", False, subject="S")
    meta = lay.meta["diagram"]
    assert meta["labels_clear"]
    for lb in meta["labels"]:
        assert lb["box"][3] <= meta["foot"][0] or lb["box"][1] >= meta["foot"][1]


def test_even_rag_never_trades_a_clean_break_for_a_dangling_conjunction() -> None:
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = get_font("Montserrat", 48)
    lines = ["Tell us in the comments", "and follow for more history."]
    out = even_rag(d, lines, f, 1200, 34)
    assert out == lines and not multi_format.dangles(out[0])
    # a given break that dangles is moved off its "and" when another break is in measure
    assert multi_format.dangles("Tell us in the comments and") and not multi_format.dangles("the comments, and.")
    moved = even_rag(d, ["Tell us in the comments and", "follow for more history."], f, 1200, 34)
    assert not any(multi_format.dangles(x) for x in moved[:-1]) and " ".join(moved) == "Tell us in the comments and follow for more history."


def test_the_closing_slides_body_does_not_end_a_line_on_and(tmp_path) -> None:
    photo = _photo(tmp_path / "e.jpg", (60, 60, 70))
    card = {"heading": "WHAT WOULD YOU HIDE?", "body": "Tell us in the comments and follow for more history."}
    _, lay = M()._render_carousel_slide("debate", photo, photo, card, 6, 7, {}, "", False)
    lines = [p.text for p in lay.plates["body"]]
    assert not any(multi_format.dangles(x) for x in lines[:-1]), lines


def test_redrawing_slide_two_moves_slide_ones_strip_with_it(tmp_path, monkeypatch) -> None:
    photos = [_photo(tmp_path / f"r{i}.jpg", c) for i, c in enumerate([(60, 70, 90), (80, 70, 60), (70, 80, 60)])]
    payload = {"carousel_slides": [
        COVER,
        {"heading": "ORBITAL LANDMARK", "body": "Visible from space, the concentric formation serves as a landmark."},
        {"heading": "MAGMATIC UPLIFT", "body": "Subsurface igneous intrusion deformed the rock into rings."},
        {"heading": "YOUR TAKE", "body": "What do you think shaped it?"}],
        "title": "T", "narration_script": ""}
    ig = M().generate_carousel_sequence(photos * 2, payload, tmp_path / "run")
    rec2 = still_layout.load(ig[1])
    old = rec2["recipe"]["strip"]["box"]
    real = multi_format.swipe_strip

    def lowered(im, lay, strip):     # slide 2's tight redraw puts its kicker rule 40 px lower
        rule = lay.meta.get("kicker_rule")
        if strip.get("side") == "left" and rule:
            lay.meta["kicker_rule"] = [rule[0], rule[1] + 40, rule[2], rule[3] + 40]
        return real(im, lay, strip)
    monkeypatch.setattr(multi_format, "swipe_strip", lowered)
    multi_format.redraw_carousel_slide(ig[1], rec2["recipe"])
    monkeypatch.setattr(multi_format, "swipe_strip", real)
    new2 = still_layout.load(ig[1])["recipe"]["strip"]["box"]
    assert new2[1] >= old[1] + 30                                   # slide 2's record keeps the strip it drew now
    new1 = still_layout.load(ig[0])["recipe"]["strip"]["box"]
    assert new1[1:4:2] == new2[1:4:2]                               # slide 1's part meets it across the seam
    a1 = np.asarray(Image.open(ig[0]).convert("RGB"), dtype=np.int16)
    assert np.abs(a1[(new1[1] + new1[3]) // 2, CW - 20] - np.array(brand.GOLD)).max() < 40
    # slide 1 drawn again on its own takes slide 2's current height too
    multi_format.redraw_carousel_slide(ig[0], still_layout.load(ig[0])["recipe"], tight=False)
    assert still_layout.load(ig[0])["recipe"]["strip"]["box"][1:4:2] == new2[1:4:2]


def test_the_share_line_does_not_end_a_line_on_an_article(tmp_path) -> None:
    photo = _photo(tmp_path / "f.jpg", (60, 60, 70))
    card = {"heading": "YOUR THEORY?", "body": "Tell us in the comments and follow for more history."}
    share = "Share this with someone who loves a good mystery, and follow for the next one."
    _, lay = M()._render_carousel_slide("debate", photo, photo, card, 6, 7, {}, share, False)
    lines = [p.text for p in lay.plates["share"]]
    assert " ".join(lines) == share and not any(multi_format.dangles(x) for x in lines[:-1]), lines
    assert not lay.problems()
