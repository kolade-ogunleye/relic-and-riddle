"""Story frames credit their photos on the frame, and credits name the author, not the artist field's notice
(design review, 2026-10-29).

Stories go out one frame per request with no caption (publisher.publish_photos), so a CC BY / BY-SA photo on a
frame was credited nowhere. And credits.asset_attribution printed the artist field whole: Derinkuyu photo 18's
(development assets/derinkuyu_underground_city/credits.json, quoted verbatim below) is nine sentences."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import credits, safe_zones, still_layout
from src.multi_format import (POLL_STICKER_ROOM, STORY_CREDIT_CONTRAST, STORY_CREDIT_MIN_PX, MultiFormatGenerator,
                              redraw_story_frame, story_credit, story_credit_report)

MORODER = ("This Photo was taken by Wolfgang Moroder.  \n\nFeel free to use my photos, but please mention me as the author "
           "and send me a  message. \n\n\n\nThis image is not in the public domain. Please respect the copyright "
           "protection. It may only be used according to the rules mentioned here. This specifically excludes use in "
           "social media, if applicable terms of the licenses listed here are not appropriate.\n\n\n\nPlease do not "
           "upload an updated image here without consultation with the Author. The author would like to make "
           "corrections only at his own source. This ensures that the changes are preserved.Please if you think that "
           "any changes should be required, please inform the author.Otherwise you can upload a new image with a new "
           "name. Please use one of the templates derivative or extract.")
BY_SA = {"title": "File:Rolling stone door Derinkuyu.jpg", "artist": MORODER, "license": "CC BY-SA 3.0",
         "license_url": "https://creativecommons.org/licenses/by-sa/3.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:Rolling_stone_door_Derinkuyu.jpg",
         "categories": ["Underground City of Derinkuyu", "Photos by Moroder", "User:Moroder/Taken with Nikon D800"]}
CC0 = {"title": "File:Marble.jpg", "artist": "Gary Todd from Xinzheng, China", "license": "CC0",
       "source_url": "https://commons.wikimedia.org/wiki/File:Marble.jpg", "attribution_required": False}
SLIDE = {"kicker": "EIGHTEEN STOREYS DOWN", "line1": "A CITY", "line2": "UNDER THE FIELDS",
         "body": "Stone doors a metre wide rolled shut from the inside, sealing whole families below."}
POLL = {"question": "Shelter or prison?", "options": ["A shelter", "A prison"]}


# ---------------------------------------------------------------- the author's name

@pytest.mark.parametrize("artist, name", [
    (MORODER, "Wolfgang Moroder"),                                             # Derinkuyu 18
    ("Nevit Dilmen (talk)", "Nevit Dilmen"),                                   # a Commons signature
    ("User:Kleuske", "Kleuske"),
    ("https://commons.wikimedia.org/wiki/User:Hamelin_de_Guettelet", "Hamelin de Guettelet"),
    ("Unknown artistUnknown artist", "Unknown"),                               # two spans run together
    ('<a href="//commons.wikimedia.org/wiki/User:Foo_Bar">Foo Bar</a>', "Foo Bar"),
    ("Photo by J. R. Smith", "J. R. Smith"),
    ("© 2012 Jane Doe, all rights reserved", "Jane Doe"),
    ("Copyright Jane Doe 2010", "Jane Doe"),
    ("I would appreciate being notified. Please credit me.", "Unknown"),       # notices name no one
    ("Please respect the copyright protection.", "Unknown"),
    ("taken by me", "Unknown"),
])
def test_the_author_is_the_name_the_field_states(artist, name):
    assert credits.author_name({"artist": artist}) == name


@pytest.mark.parametrize("artist", [  # plain names on development assets: printed exactly as before
    "Marc Ryckaert (MJJR)", "German Archaeological Institute, photo E. Kücük.", "Jimmyoneill at English Wikipedia",
    "Warren LeMay from Cullowhee, NC, United States", "Николай Максимович", "SentinelHub", "Y.Shishido",
])
def test_a_plain_name_is_kept_as_written(artist):
    assert credits.author_name({"artist": artist}) == artist


def test_when_no_name_is_stated_the_recorded_uploader_is_used_and_nothing_is_invented():
    phrase = "Picture taken by the author"
    assert credits.author_name({"artist": phrase}) == "Unknown"
    assert credits.author_name({"artist": phrase, "categories": ["User:Abc/Gallery"]}) == "Abc"
    assert credits.author_name({"artist": phrase, "categories": ["User:Abc/x", "User:Xyz/y"]}) == "Unknown"  # two: unsure
    assert credits.author_name({"artist": phrase, "uploaded_by": "8728071488"}) == "Unknown"  # a Telegram id
    assert credits.author_name({"artist": phrase, "uploaded_by": "Omok"}) == "Omok"


def test_the_moroder_credit_line_names_him_and_carries_none_of_his_notice():
    line = credits.asset_attribution(BY_SA)
    assert line.startswith("📷 Wolfgang Moroder | File:Rolling stone door Derinkuyu.jpg | CC BY-SA 3.0 | https://")
    assert "Feel free" not in line and "social media" not in line and len(line.splitlines()) == 1
    assert credits.is_attribution_line(line)
    assert "Wolfgang Moroder (CC BY-SA 3.0)" in credits.caption_credit_line({"images": [BY_SA]})
    # a plain field gives the credit line it gave before
    plain = dict(BY_SA, artist="Marc Ryckaert (MJJR)")
    assert credits.asset_attribution(plain).startswith("📷 Marc Ryckaert (MJJR) | ")


def test_every_development_artist_field_gives_a_short_one_line_name():
    root = Path(__file__).resolve().parent.parent / "assets"
    seen = 0
    for f in root.glob("*/credits.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        for entry in data if isinstance(data, list) else data.get("images", []):
            name = credits.author_name(entry)
            assert name and "\n" not in name and len(name) <= credits.AUTHOR_MAX, (f, entry.get("artist"))
            seen += 1
    if not seen:
        pytest.skip("no development assets in this checkout")


# ---------------------------------------------------------------- the Story frame's credit

def _topic(tmp_path, entry, name="p.jpg", colour=None):
    folder = tmp_path / "topic"
    folder.mkdir(exist_ok=True)
    photo = folder / name
    if colour:
        Image.new("RGB", (1200, 2000), colour).save(photo)
    else:
        rng = np.random.default_rng(7)
        Image.fromarray((rng.random((2000, 1200, 3)) * 120 + 60).astype("uint8")).save(photo)
    path = folder / "credits.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"reviewed": True, "images": []}
    if entry is not None:
        data["images"].append(dict(entry, file=name))
    path.write_text(json.dumps(data), encoding="utf-8")
    return photo


def test_what_each_licence_asks_the_frame_to_say(tmp_path):
    sa = story_credit(_topic(tmp_path, BY_SA, "sa.jpg"))
    assert sa["required"] and sa["share_alike"] and sa["author"] == "Wolfgang Moroder"
    assert sa["lines"] == ["Photo: Wolfgang Moroder · CC BY-SA 3.0 · adapted: cropped, with text added",
                           "This frame: CC BY-SA 4.0 · creativecommons.org/licenses/by-sa/4.0"]
    by = story_credit(_topic(tmp_path, dict(BY_SA, artist="Arian Zwegers", license="CC BY 2.0",
                                            license_url="https://creativecommons.org/licenses/by/2.0/"), "by.jpg"))
    assert by["lines"] == ["Photo: Arian Zwegers · CC BY 2.0 · adapted: cropped, with text added",
                           "creativecommons.org/licenses/by/2.0"]
    # Openverse leaves attribution_required unset: a CC BY licence still asks for it
    assert story_credit(_topic(tmp_path, dict(BY_SA, attribution_required=None), "ov.jpg"))["required"]
    assert not story_credit(_topic(tmp_path, CC0, "cc0.jpg"))["required"]
    assert not story_credit(_topic(tmp_path, dict(CC0, license="Public domain"), "pd.jpg"))["required"]
    assert not story_credit(_topic(tmp_path, None, "map.jpg"))["required"]   # our own graphic: no credits entry


@pytest.mark.parametrize("index", [0, 1, 2])
def test_a_by_sa_frame_draws_its_credit_in_the_story_box_clear_of_everything(tmp_path, index):
    photo = _topic(tmp_path, BY_SA, colour=(250, 250, 250))                # a white photo: the hardest background
    im, lay = MultiFormatGenerator()._render_story_frame(photo, SLIDE, index, 3, POLL)
    credit = lay.meta["photo_credit"]
    assert credit["required"] and credit["complete"] and credit["px"] >= STORY_CREDIT_MIN_PX
    words = [[w for w in " ".join(x).split() if w != "·"] for x in (credit["drawn"], credit["lines"])]
    assert words[0] == words[1]                                            # wrapped at its breaks, nothing lost
    assert lay.problems() == []                                            # in its zone, over nothing, not under the sticker
    box = tuple(credit["box"])
    assert [e["box"] for e in lay.elements if e["name"] == "photo_credit"] == [list(box)]
    sx0, sy0, sx1, sy1 = safe_zones.safe_box("story")
    assert safe_zones.inside(box, (sx0, sy0, sx1, sy1))                    # under no top bar and over no reply bar
    assert box[3] <= POLL_STICKER_ROOM[0]                                  # never in the sticker's room
    assert credit["contrast_unshaded"] < STORY_CREDIT_CONTRAST <= credit["contrast"]  # the backing was needed and did it
    # the text block keeps its own box above the credit
    assert lay.meta["text_box"][3] < box[1]


def test_the_contrast_is_measured_on_the_pixels_under_the_line(tmp_path):
    photo = _topic(tmp_path, BY_SA, colour=(250, 250, 250))
    gen = MultiFormatGenerator()
    im, lay = gen._render_story_frame(photo, SLIDE, 1, 3, {})
    x0, y0, x1, y1 = lay.meta["photo_credit"]["backing"]
    px = np.asarray(im.convert("L").crop((x0, y0, x1, y1)), dtype=np.float32)
    background = px[px < 150]                                              # leave out the light glyphs
    lum = ((background / 255.0 + 0.055) / 1.055) ** 2.4
    assert (0.7287 + 0.05) / (np.percentile(lum, 90) + 0.05) >= STORY_CREDIT_CONTRAST  # LIGHT (225) is 0.7529; the stroke darker


def test_a_cc0_or_unrecorded_photo_gets_no_credit_and_the_frame_is_unchanged(tmp_path):
    gen = MultiFormatGenerator()
    plain = _topic(tmp_path, None, "plain.jpg", colour=(120, 110, 100))
    cc0 = _topic(tmp_path, CC0, "cc0.jpg", colour=(120, 110, 100))
    a, lay_a = gen._render_story_frame(plain, SLIDE, 1, 3, {})
    b, lay_b = gen._render_story_frame(cc0, SLIDE, 1, 3, {})
    assert not lay_b.meta["photo_credit"]["required"] and "photo_credit" not in [e["name"] for e in lay_b.elements]
    assert np.array_equal(np.asarray(a), np.asarray(b))


def test_a_long_name_stays_within_three_lines_and_a_missing_glyph_is_reported(tmp_path):
    long_name = dict(BY_SA, artist="Tilemahos Efthimiadis from Athens, Greece, with the Hellenic Maritime Museum")
    photo = _topic(tmp_path, long_name, "long.jpg")
    _, lay = MultiFormatGenerator()._render_story_frame(photo, SLIDE, 1, 3, {})
    credit = lay.meta["photo_credit"]
    assert len(credit["drawn"]) <= 3 and credit["complete"] and lay.problems() == []
    cjk = _topic(tmp_path, dict(BY_SA, artist="山田太郎"), "cjk.jpg")
    _, lay = MultiFormatGenerator()._render_story_frame(cjk, SLIDE, 1, 3, {})
    assert lay.meta["photo_credit"]["complete"] is False                   # the release report then flags it


def _payload():
    return {"title": "Derinkuyu", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
            "story_slides": [SLIDE, dict(SLIDE, kicker="THE REVEAL"), dict(SLIDE, kicker="YOUR CALL")], "poll": POLL}


def test_the_sequence_records_what_it_credited_and_the_report_asserts_it(tmp_path):
    sa = _topic(tmp_path, BY_SA, "sa.jpg")
    cc0 = _topic(tmp_path, CC0, "cc0.jpg")
    gen = MultiFormatGenerator()
    gen._match_photos = lambda cards, stills: [sa, cc0, sa]
    out = tmp_path / "run"
    frames = gen.generate_story_sequence(sa, _payload(), out, scene_images=[sa, cc0])
    report = story_credit_report(frames)
    assert report["problems"] == []
    assert [c["frame"] for c in report["credited"]] == ["story_01_hook.jpg"]  # no third frame (QF-80)
    manifest = json.loads((out / "companion_posting.json").read_text(encoding="utf-8"))
    assert manifest["story_credits"]["story_02_reveal.jpg"] == []
    assert manifest["story_credits"]["story_01_hook.jpg"][0].startswith("Photo: Wolfgang Moroder")
    for frame in frames:
        assert still_layout.check(frame) == []
    # The tighter redraw keeps the credit
    entry = redraw_story_frame(frames[0], still_layout.load(frames[0])["recipe"])  # the credited hook (QF-80)
    assert "photo_credit" in [e["name"] for e in entry["elements"]] and not entry["problems"]
    assert story_credit_report(frames)["problems"] == []
    # A frame drawn without its credit (as before this change) is a problem for the release report
    layout_file = out / "stories" / "layout.json"
    layout = json.loads(layout_file.read_text(encoding="utf-8"))
    layout[0]["photo_credit"]["lines"] = []
    layout_file.write_text(json.dumps(layout), encoding="utf-8")
    problems = story_credit_report(frames)["problems"]
    assert len(problems) == 1 and problems[0].startswith("story_01_hook.jpg")
    del layout[0]["photo_credit"]
    layout_file.write_text(json.dumps(layout), encoding="utf-8")
    assert "no layout record" in story_credit_report(frames)["problems"][0]
