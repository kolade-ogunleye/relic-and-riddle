"""Carousel chrome and direction cues (operator, 2026-10-28): "Whenever we're using the pointer emoji we need to
be sure we are pointing in the right direction" (the posted Facebook carousel's hand pointed right while its
caption sat above and the photos below); "the design we added to the Instagram carousel to make it look like a
slide is needless"; "the RELIC & RIDDLE text also appears in an unsafe UI location showing the N of M".

No copy carries a direction-dependent cue, Facebook's carousel caption never says "swipe", neither carousel copy
draws a counter, progress bar, dots or SWIPE cue, and nothing is drawn under Instagram's counter. The example
captions are the production carousel captions' opening lines (and test-only variations)."""
import json
from unittest.mock import MagicMock

import pytest
import requests
from PIL import Image, ImageDraw

from src import still_layout
from src.models import ScriptPackage
from src.multi_format import MultiFormatGenerator
from src.publisher import MetaPublisher, UploadPostPublisher
from src.script_engine import _DIRECTION_MARKS, facebook_photo_caption, neutral_cues, normalize_copy

DIRECTIONAL = ("swipe right", "swipe left", "swipe up", "swipe down", "below", "above", "scroll down")

# (draft, what Instagram's caption and every other copy says, what Facebook's photo post says)
CUES = [
    ("Swipe through the engineering genius behind Ellora's largest rock-cut Hindu temple. 👉",
     "Swipe through the engineering genius behind Ellora's largest rock-cut Hindu temple.",
     "Explore the engineering genius behind Ellora's largest rock-cut Hindu temple."),
    ("Swipe right to see the gears ➡️ What do you think? 👇\n\n#Antikythera #Greece",
     "Swipe through to see the gears. What do you think?\n\n#Antikythera #Greece",
     "See the gears. What do you think?\n\n#Antikythera #Greece"),
    ("Swipe to uncover the secrets behind Damascus steel ⚔️", "Swipe to uncover the secrets behind Damascus steel ⚔️",
     "Uncover the secrets behind Damascus steel ⚔️"),
    ("Swipe through to trace the disappearance of an imperial masterpiece. 🏛️",
     "Swipe through to trace the disappearance of an imperial masterpiece. 🏛️",
     "Trace the disappearance of an imperial masterpiece. 🏛️"),
    ("Swipe left for the map.", "Swipe through for the map.", "See the photos for the map."),
    ("Swipe up through the levels ⬆️", "Swipe through the levels", "Explore the levels"),
    ("Keep swiping to see how it ends → ", "Keep swiping to see how it ends", "See how it ends"),
    ("Tell us in the comments below! ⬇️", "Tell us in the comments!", "Tell us in the comments!"),
    ("Drop your guess below 👇🏽", "Drop your guess", "Drop your guess"),
    ("The photos above show the site in 1902.", "The photos show the site in 1902.", "The photos show the site in 1902."),
    ("See below.", "Take a look.", "Take a look."),
    ("Scroll down to the comments.", "Scroll to the comments.", "Scroll to the comments."),
    ("👇\nWhat would you have built? ☝️", "What would you have built?", "What would you have built?"),
    ("Built 1902 → 1910 ↗️ and never finished.", "Built 1902 to 1910 and never finished.",
     "Built 1902 to 1910 and never finished."),
    ("Start swiping and you will meet the builders. ◀️ ▶️", "Start swiping and you will meet the builders.",
     "Start looking through the photos and you will meet the builders."),
]

# Facts are not cues: a place below or above something keeps its words.
FACTS = ["The chamber lies 30 m below the surface.", "The carvings above the door survive.",
         "Water rose above the third level.", "The images above the altar were repainted.",
         "Temperatures fell below freezing.", "What would you have built? 🏛️"]


@pytest.mark.parametrize("draft,everywhere,facebook", CUES)
def test_no_pointer_arrow_or_swipe_direction_survives(draft, everywhere, facebook):
    assert neutral_cues(draft) == everywhere
    assert facebook_photo_caption(draft) == facebook
    for out in (everywhere, facebook):
        assert not _DIRECTION_MARKS.search(out), out
        assert not any(cue in out.lower() for cue in DIRECTIONAL), out
    assert "swip" not in facebook.lower()


@pytest.mark.parametrize("text", FACTS)
def test_facts_and_plain_copy_are_left_alone(text):
    assert neutral_cues(text) == text and facebook_photo_caption(text) == text


def test_normalize_copy_clears_every_public_text(curated_payload):
    p = dict(curated_payload)
    p.update(title="The city under Cappadocia 👇", carousel_caption="Swipe right ➡️ through 18 stories.\n\n#Derinkuyu",
             first_comment="Which level would you hide in? Comment below 👇", share_line="Send this to a caver 👉 and follow.",
             caption=p["caption"].replace("\n", " 👇\n", 1))
    p["carousel_slides"] = [dict(s) for s in p["carousel_slides"]]
    p["carousel_slides"][-1]["body"] = "Tell us in the comments below ⬇️"
    p["story_slides"] = [dict(s) for s in p["story_slides"]]
    p["story_slides"][0]["body"] = "Swipe up 👆 for the full story."
    out = normalize_copy(ScriptPackage.model_validate(p)).model_dump()
    texts = [out["title"], out["caption"], out["carousel_caption"], out["first_comment"], out["share_line"],
             *(s["heading"] + " " + s["body"] for s in out["carousel_slides"]),
             *(" ".join(s[k] for k in ("kicker", "line1", "line2", "body")) for s in out["story_slides"]),
             *out["thumbnail"].values()]
    for t in texts:
        assert not _DIRECTION_MARKS.search(t) and not any(c in t.lower() for c in DIRECTIONAL), t
    assert out["carousel_caption"] == "Swipe through 18 stories.\n\n#Derinkuyu"   # "right ➡️ through": one "through"
    assert out["first_comment"] == "Which level would you hide in? Comment"
    assert out["carousel_slides"][-1]["body"] == "Tell us in the comments"
    assert out["narration_script"] == p["narration_script"]                 # the voice is never touched


def _response(status, body):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.json.return_value = body
    return r


def test_facebooks_photo_post_never_says_swipe(monkeypatch, db, tmp_path):
    db.create_run("r1", "01_topic")
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers, data, files, timeout: sent.append(
        ([v for k, v in data if k == "platform[]"], dict(data)["description"]))
        or _response(200, {"success": True, "results": {"instagram": {"success": True, "url": "u"}}}))
    ig, fb = tmp_path / "carousel", tmp_path / "carousel_fb"
    ig.mkdir(); fb.mkdir()
    slides = []
    for i in (1, 2):
        (ig / f"carousel_slide_{i:02d}.jpg").write_bytes(b"IG")
        (fb / f"carousel_slide_{i:02d}.jpg").write_bytes(b"FB")
        slides.append(str(ig / f"carousel_slide_{i:02d}.jpg"))
    caption = "Swipe through to see how a corroded lump revealed a mechanism. ⚙️ 👉\n\n#Antikythera"
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    res = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "carousel", slides, caption=caption)
    assert res["status"] == "published"
    assert sent[0] == (["instagram"], "Swipe through to see how a corroded lump revealed a mechanism. ⚙️\n\n#Antikythera")
    assert sent[1] == (["facebook"], "See how a corroded lump revealed a mechanism. ⚙️\n\n#Antikythera")
    # A dry run records the caption each request would carry.
    MetaPublisher(db=db, publishers=[]).publish_photos("r1", "01_topic", "carousel", slides, caption=caption, dry_run=True)
    rows = [json.loads(p["response_payload_json"]) for p in db.list_publications("r1") if p["status"] == "simulated"]
    assert [r["caption"] for r in rows] == [sent[0][1], sent[1][1]]


# --- the slides ---------------------------------------------------------------------------------------------

CARDS = [{"heading": "The calendar that outlived its empire", "body": "A shoebox of bronze tracked the Sun and Moon."},
         {"heading": "Thirty gearwheels survive", "body": "The largest is about thirteen centimetres across."},
         {"heading": "Versus the surface", "body": "Compared with the city above, the tunnels stayed cool."},
         {"heading": "Measured in 1902", "body": "Divers raised 82 fragments from the wreck."},
         {"heading": "A workshop's secret", "body": "Nobody built another for a thousand years."},
         {"heading": "Lost knowledge", "body": "What else did the ancient world know that we lost?"},
         {"heading": "What was forgotten?", "body": "If a workshop could make this, what else did we lose?"}]
PAYLOAD = {"title": "T", "poll": {"question": "Astronomy or metalwork?", "options": ["The astronomy", "The metalwork"]},
           "carousel_slides": CARDS}
ARCHETYPES = ("cover", "immersive", "comparison", "blueprint", "colossal_stat", "debate", "editorial_split")


def _photos(tmp_path, n=3):
    out = []
    for i in range(n):
        p = tmp_path / f"p{i}.jpg"
        im = Image.new("RGB", (1600, 2000), (200 - 40 * i, 190, 170))            # bright: the hardest for legibility
        ImageDraw.Draw(im).rectangle([200, 300, 1400, 900], fill=(250, 250, 245))
        im.save(p)
        out.append(p)
    return out


@pytest.fixture
def gen(monkeypatch):
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos",
                        lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))])
    return MultiFormatGenerator()


def test_no_slide_chrome_on_either_copy(gen, tmp_path, monkeypatch):
    drawn, lines, dots = [], [], []
    real_text, real_line, real_ellipse = ImageDraw.ImageDraw.text, ImageDraw.ImageDraw.line, ImageDraw.ImageDraw.ellipse
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: drawn.append(str(text)) or real_text(self, xy, text, *a, **k))
    monkeypatch.setattr(ImageDraw.ImageDraw, "ellipse", lambda self, xy, *a, **k: dots.append(xy) or real_ellipse(self, xy, *a, **k))
    monkeypatch.setattr(ImageDraw.ImageDraw, "line", lambda self, xy, *a, **k: lines.append((xy, k.get("width", 1))) or real_line(self, xy, *a, **k))
    photos = _photos(tmp_path)
    for facebook in (False, True):
        slides = gen.generate_carousel_sequence(photos, PAYLOAD, tmp_path, facebook=facebook)
        records = json.loads((slides[0].parent / still_layout.SIDECAR).read_text(encoding="utf-8"))
        for slide in slides:
            names = {e["name"] for e in records[slide.name]["elements"]}
            assert not names & {"header_dossier", "swipe_cues", "swipe"}, (facebook, slide.name, names)
            assert "header_brand" in names
    assert not any("SWIPE" in t or "ARCHIVAL RECORD" in t or " OF 0" in t or "// 0" in t or " DATA" in t for t in drawn)
    assert not any(ch in t for t in drawn for ch in "→←↑↓"), [t for t in drawn if any(ch in t for ch in "→←↑↓")]
    assert not dots                                                            # no progress dots
    # The progress bar was a 2-3 px line across the slide near its bottom (y = 1305).
    assert not any(isinstance(xy, list) and len(xy) == 2 and xy[0][1] == xy[1][1] > 1250 and w in (2, 3) for xy, w in lines)


def test_facebooks_grid_copy_never_says_swipe_on_a_slide(gen, tmp_path, monkeypatch):
    drawn = []
    real_text = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: drawn.append(str(text)) or real_text(self, xy, text, *a, **k))
    cards = [dict(CARDS[0], body="Swipe to see how ancient engineers sculpted a temple 👉")] + CARDS[1:]
    photos = _photos(tmp_path)
    gen.generate_carousel_sequence(photos, dict(PAYLOAD, carousel_slides=cards), tmp_path, facebook=True)
    assert not any("swip" in t.lower() or _DIRECTION_MARKS.search(t) for t in drawn), drawn
    assert any(t.startswith("See how ancient") for t in drawn)
    drawn.clear()
    gen.generate_carousel_sequence(photos, dict(PAYLOAD, carousel_slides=cards), tmp_path, facebook=False)
    assert any(t.startswith("Swipe to see how") for t in drawn) and not any(_DIRECTION_MARKS.search(t) for t in drawn)


def test_nothing_lies_under_instagrams_counter_and_every_check_passes(gen, tmp_path):
    photos = _photos(tmp_path)
    cw, chh = gen.CAROUSEL_SIZE
    counter = gen._ig_counter(cw)
    assert counter[0] <= cw - 240 and counter[3] >= 110                      # the right of the top ~110 px
    for archetype in ARCHETYPES:
        for tight in (False, True):
            for facebook in (False, True):
                _, lay = gen._render_carousel_slide(archetype, photos[0], photos[1], CARDS[3], 3, 7, PAYLOAD["poll"],
                                                    "Send this to a friend who loves gears, and follow for the next relic.",
                                                    facebook, tight=tight)
                assert lay.problems() == [], (archetype, tight, lay.problems())
                assert lay.to_dict()["protected"] == [{"name": "instagram_counter", "box": list(counter)}]
                for e in lay.elements:
                    assert not still_layout.intersects(tuple(e["box"]), counter), (archetype, e)
                brand = next(e["box"] for e in lay.elements if e["name"] == "header_brand")
                assert brand[0] < cw // 2 and brand[3] <= 135                # top-left; out whole in a square crop


def test_the_check_reports_anything_drawn_under_the_counter():
    lay = still_layout.Layout("carousel", (1080, 1350), (40, 30, 1040, 1320), protected=[("instagram_counter", (800, 0, 1080, 140))])
    lay.add("header_brand", (840, 70, 1000, 92))                              # where the brand used to be
    found = lay.problems()
    assert found and found[0].startswith("layout_overlap: header_brand") and "instagram_counter" in found[0]
    assert still_layout.problems(lay.to_dict()) == found                       # re-derived from the saved record


def test_the_text_uses_the_band_the_cues_reserved(gen, tmp_path):
    photos = _photos(tmp_path)
    cw, chh = gen.CAROUSEL_SIZE
    assert gen._footer_top(chh) == chh - 30 - 2 * gen.BAND_GAP                # was chh - 67 - 16, above the cues
    _, lay = gen._render_carousel_slide("debate", photos[0], photos[1], CARDS[6], 6, 7, {}, "Send this on.", False)
    footer = next(e["box"] for e in lay.elements if e["name"] == "footer")
    assert footer[3] <= gen._footer_top(chh) and footer[3] > chh - 100       # on the footer line, not 120 px up


def test_story_frames_carry_no_arrow(gen, tmp_path, monkeypatch):
    drawn = []
    real_text = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *a, **k: drawn.append(str(text)) or real_text(self, xy, text, *a, **k))
    slide = {"kicker": "THE FIND", "line1": "A LUMP", "line2": "OF BRONZE", "body": "Divers raised it in 1901."}
    payload = {"story_slides": [slide, slide, slide], "poll": PAYLOAD["poll"], "title": "T", "caption": "", "first_comment": ""}
    photos = _photos(tmp_path)
    gen.generate_story_sequence(photos[0], payload, tmp_path, scene_images=photos)
    assert "FULL STORY IN OUR NEW REEL" in drawn and "REPLY WITH A OR B" in drawn
    assert not any(_DIRECTION_MARKS.search(t) or "BELOW" in t for t in drawn)
    manifest = json.loads((tmp_path / "companion_posting.json").read_text(encoding="utf-8"))
    assert "below" not in manifest["platforms"]["facebook_reels"]["caption"]
