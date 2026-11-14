"""Carousel content and compliance (platform audit, 2026-10-28).

1. No slide states a qualifier its script did not: "REF B // SUBTERRANEAN DEPTH" was drawn on every comparison
   slide (it called the Pantheon's dome subterranean), "VERIFIED BY ARCHEOLOGICAL RECORD" on every stat slide
   (over Nazca's hedged "By one estimate, ..."), and keyword labels such as "DISCOVERY RECORD" on Roman concrete's
   marine slide ("seawalls" has "wall").
2. Every carousel photo carries alt text (Upload-Post's instagram_alt_text / facebook_alt_text); Stories none.
3. Facebook's multi-photo post gets at most four photos (cover, two content slides, the closing slide).
4. Both copies show the same photos: they are matched once.
5. Every photo the slides show whose licence asks for it is credited (asset_attribution's format), in the
   caption, or in the first comment when the caption would run over 2,200 characters.

The cards are the production scripts' own (output/run_roman_self_healing_concrete_1793163918 and
output/run_nazca_lines_runway_paradox_1792961797, verified_script.json)."""
import json
import threading
from unittest.mock import MagicMock

import pytest
import requests
from PIL import Image, ImageDraw

from src import still_layout
from src.credits import asset_attribution
from src.multi_format import (
    CAROUSEL_PHOTOS, CAROUSEL_SHARE_ALIKE, MultiFormatGenerator, carousel_credits, facebook_pick, slide_alt_text,
)
from src.publisher import MetaPublisher, UploadPostPublisher, _caption_for

ROMAN = {
    "carousel_slides": [
        {"heading": "ROMAN CONCRETE SECRETS", "body": "Why ancient Roman monuments endure while modern structures crumble after decades."},
        {"heading": "MODERN DECAY", "body": "Modern concrete structures can begin to crumble just a few decades after construction."},
        {"heading": "THE PANTHEON'S DOME", "body": "Dedicated in AD 128, it remains the world's largest unreinforced concrete dome."},
        {"heading": "HARSH ENVIRONMENTS", "body": "Roman concrete withstood severe marine conditions in docks, seawalls, and sewers."},
        {"heading": "THE HOT-MIXING SECRET", "body": "Volcanic pozzolans and quicklime produced self-healing properties inside the concrete."},
        {"heading": "OVERTURNING HISTORY", "body": "Chemical evidence overturned longstanding assumptions based on the texts of Vitruvius."},
        {"heading": "LOST INGENUITY", "body": "Did ancient civilizations build with a deeper respect for future generations than we do?"},
    ],
    "poll": {"question": "Built for the future?", "options": ["Yes", "No"]},
    "carousel_caption": "Swipe through to discover how ancient Roman concrete heals itself. 🏛️\n\n#Pantheon #AncientRome",
}
NAZCA_STAT = {"heading": "Massive Animal Shapes",
              "body": "By one estimate, the etched condor spans 134 meters, and the hummingbird spans 93 meters."}
NAZCA_FOUND = {"heading": "Simple Surveying",
               "body": "Archaeologists found wooden stakes at the ends of lines, showing how makers aligned them."}
OLD_LABELS = ("REF A", "REF B", "SURFACE CONTEXT", "SUBTERRANEAN", "VERIFIED", "ARCHEOLOGICAL", "ARCHAEOLOGICAL",
              "DISCOVERY RECORD", "ARCHIVAL EVIDENCE", "FIELD INVESTIGATION", "ENGINEERING BLUEPRINT",
              "COMPARATIVE EVIDENCE", "HISTORICAL ENIGMA", "FORENSIC")
LICENCES = [("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0", True),
            ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0", True),
            ("Public domain", "", False)]


def _photo(path, shade):
    Image.new("RGB", (1400, 1600), (shade, shade - 20, shade - 40)).save(path)
    return path


@pytest.fixture
def topic(tmp_path):
    """A topic folder like assets/<topic>/: photos with their credits.json (one CC BY, one BY-SA, one PD)."""
    folder = tmp_path / "assets" / "t"
    folder.mkdir(parents=True)
    images, photos = [], []
    for i, (licence, url, required) in enumerate(LICENCES, 1):
        photos.append(_photo(folder / f"{i:02d}_commons.jpg", 90 + 30 * i))
        images.append({"file": f"{i:02d}_commons.jpg", "title": f"File:Photo {i}.jpg", "artist": f"Artist {i}",
                       "license": licence, "license_url": url, "attribution_required": required,
                       "source_url": f"https://commons.wikimedia.org/wiki/File:Photo_{i}.jpg",
                       "description": f"The vault of building {i} seen from below. DSC0064{i}"})
    (folder / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
    return {"photos": photos, "images": images, "run": tmp_path / "run"}


@pytest.fixture
def rotate(monkeypatch):
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos",
                        lambda self, cards, stills: [stills[i % len(stills)] for i in range(len(cards))])


def _spy_text(monkeypatch):
    drawn = []
    real = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text",
                        lambda self, xy, text, *a, **k: drawn.append(str(text)) or real(self, xy, text, *a, **k))
    return drawn


def _response(body):
    r = MagicMock(spec=requests.Response)
    r.status_code = 200
    r.json.return_value = body
    return r


# ---------------------------------------------------------------- 1. no claim the script did not make

def test_no_slide_states_a_qualifier_its_card_does_not(topic, rotate, monkeypatch):
    drawn = _spy_text(monkeypatch)
    gen = MultiFormatGenerator()
    for facebook in (False, True):
        gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=facebook)
    assert not [t for t in drawn if any(label in t.upper() for label in OLD_LABELS)]
    # The marine slide was "DISCOVERY RECORD", the comparison slide "COMPARATIVE EVIDENCE" over REF A / REF B.
    archetypes = [MultiFormatGenerator.route_carousel_archetype(c, i, 7) for i, c in enumerate(ROMAN["carousel_slides"])]
    assert archetypes[1] == "comparison" and archetypes[3] == "immersive"
    assert [MultiFormatGenerator._get_card_category(c, a) for c, a in zip(ROMAN["carousel_slides"], archetypes)] == \
        [""] * 6 + ["YOUR TAKE"]                                     # the closing slide asks a question; it says so


def test_the_closing_slide_is_labelled_only_when_it_asks():
    assert MultiFormatGenerator._get_card_category({"heading": "THE END", "body": "Follow for more."}, "debate") == ""
    assert MultiFormatGenerator._get_card_category(NAZCA_FOUND, "blueprint") == ""   # "found": no DISCOVERY RECORD


def test_a_stat_slide_shows_the_cards_own_figure_and_no_verified_badge(topic, monkeypatch):
    drawn = _spy_text(monkeypatch)
    gen = MultiFormatGenerator()
    assert gen.route_carousel_archetype(NAZCA_STAT, 4, 7) == "colossal_stat"
    for tight in (False, True):
        drawn.clear()
        _, lay = gen._render_carousel_slide("colossal_stat", topic["photos"][0], topic["photos"][1], NAZCA_STAT, 4, 7,
                                            {}, "", False, tight=tight)
        names = {e["name"] for e in lay.elements}
        assert lay.problems() == [] and "stat" in names and not names & {"verified", "badge"}
        # The figure and its unit are drawn as two lines on one baseline since 2026-10-29 (the unit smaller, in bone).
        assert "134" in drawn and "METERS" in drawn and not any("VERIFIED" in t for t in drawn)
    # A stat-routed card with no figure gets no stat line (it used to show the category label instead).
    _, lay = gen._render_carousel_slide("colossal_stat", topic["photos"][0], topic["photos"][1],
                                        {"heading": "DEEP", "body": "The mine runs deep below the town."}, 2, 7, {}, "", False)
    assert "stat" not in {e["name"] for e in lay.elements} and lay.problems() == []


@pytest.mark.parametrize("archetype", ["cover", "immersive", "comparison", "blueprint", "colossal_stat", "debate",
                                       "editorial_split"])
def test_every_archetype_lays_out_cleanly_without_its_badge(topic, archetype):
    gen = MultiFormatGenerator()
    for card in ROMAN["carousel_slides"][1:]:
        for tight in (False, True):
            _, lay = gen._render_carousel_slide(archetype, topic["photos"][0], topic["photos"][1], card, 3, 7,
                                                ROMAN["poll"], "Send this to a friend, and follow for the next marvel.",
                                                False, tight=tight)
            assert lay.problems() == [], (archetype, card["heading"], tight, lay.problems())
            assert not {e["name"] for e in lay.elements} & {"ref_a", "ref_b", "verified"}


# ---------------------------------------------------------------- 4. one match for both copies

def test_both_copies_show_the_same_photos_matched_once(topic, monkeypatch):
    calls = []
    lock = threading.Lock()

    def match(self, cards, stills):  # a different answer each time, as two vision matches can give
        with lock:
            calls.append(1)
            shift = len(calls)
        return [stills[(i + shift) % len(stills)] for i in range(len(cards))]

    monkeypatch.setattr(MultiFormatGenerator, "_match_photos", match)
    gen = MultiFormatGenerator()
    threads = [threading.Thread(target=gen.generate_carousel_sequence, args=(topic["photos"], ROMAN, topic["run"]),
                                kwargs={"facebook": fb}) for fb in (False, True)]       # in parallel, as main.py runs them
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    ig = [still_layout.load(topic["run"] / "carousel" / f"carousel_slide_{i:02d}.jpg")["recipe"] for i in range(1, 8)]
    fb = [still_layout.load(topic["run"] / "carousel_fb" / f"carousel_slide_{i:02d}.jpg")["recipe"] for i in range(1, 8)]
    assert [r["photo"] for r in ig] == [r["photo"] for r in fb]
    assert [r["photo_next"] for r in ig] == [r["photo_next"] for r in fb]
    # A redraw of one copy reuses the record; a changed card is matched again.
    gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=True)
    assert len(calls) == 1 and (topic["run"] / CAROUSEL_PHOTOS).is_file()
    changed = dict(ROMAN, carousel_slides=[dict(ROMAN["carousel_slides"][0], body="Another body.")] + ROMAN["carousel_slides"][1:])
    gen.generate_carousel_sequence(topic["photos"], changed, topic["run"])
    assert len(calls) == 2


def test_a_run_drawn_before_the_record_reuses_the_other_copys_photos(topic, rotate, monkeypatch):
    gen = MultiFormatGenerator()
    gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])
    (topic["run"] / CAROUSEL_PHOTOS).unlink()                         # as a run drawn before 2026-10-28
    monkeypatch.setattr(MultiFormatGenerator, "_match_photos", lambda *a: pytest.fail("matched again"))
    gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=True)


# ---------------------------------------------------------------- 2. alt text

def test_alt_text_says_what_the_photo_shows_and_what_the_slide_says(topic, rotate, monkeypatch):
    slides = MultiFormatGenerator().generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])
    alts = [slide_alt_text(s) for s in slides]
    assert all(0 < len(a) <= 1000 for a in alts)
    assert alts[0].startswith("Photo: The vault of building 1 seen from below.") and "DSC" not in alts[0]
    assert "ROMAN CONCRETE SECRETS." in alts[0] and ROMAN["carousel_slides"][0]["body"] in alts[0]
    assert alts[1].startswith("Two photos: The vault of building 2 seen from below; and The vault of building 3")
    assert "comments" not in alts[6].lower()  # no comments footer on the closing slide (operator, 2026-10-29)
    # QF-23: a body thirty times over is refused on the slide (it was drawn cut); the alt text's own cap is held on
    # the record such a body leaves
    huge = dict(ROMAN, carousel_slides=[dict(c, body=c["body"] * 30) for c in ROMAN["carousel_slides"]])
    from src import multi_format
    from src.typography import TextFitError
    with pytest.raises(TextFitError, match="Checked copy does not fit"):
        MultiFormatGenerator().generate_carousel_sequence(topic["photos"], huge, topic["run"] / "huge")
    real = multi_format.slide_record
    monkeypatch.setattr(multi_format, "slide_record", lambda s: {**real(s), "card": dict(
        real(s).get("card") or {}, body=str((real(s).get("card") or {}).get("body") or "") * 30)})
    assert all(len(slide_alt_text(s)) > 900 and len(slide_alt_text(s)) <= 1000 for s in slides)
    assert slide_alt_text(topic["run"] / "no_record.jpg") == ""


def test_upload_post_gets_alt_text_per_photo_for_carousels_and_none_for_stories(topic, rotate, db, monkeypatch):
    db.create_run("r1", "01_topic")
    gen = MultiFormatGenerator()
    slides = [str(s) for s in gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])]
    gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=True)
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers, data, files, timeout: sent.append(
        (list(data), [f[1][0] for f in files], [f[1][1].name for f in files]))
        or _response({"success": True, "results": {"instagram": {"success": True, "url": "u"}}}))
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    res = MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "carousel", slides, caption="Cap")
    assert res["status"] == "published" and len(sent) == 2
    (ig_data, ig_names, ig_paths), (fb_data, fb_names, fb_paths) = sent
    ig, fb = dict(ig_data), dict(fb_data)
    assert "facebook_alt_text" not in ig and "instagram_alt_text" not in fb
    ig_alts, fb_alts = json.loads(ig["instagram_alt_text"]), json.loads(fb["facebook_alt_text"])
    assert ig_alts == [slide_alt_text(p) for p in ig_paths] and len(ig_alts) == 7
    assert fb_alts == [slide_alt_text(p) for p in fb_paths] and len(fb_alts) == 5 and all("carousel_fb" in p for p in fb_paths)
    sent.clear()
    MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "story", slides[:2])
    assert sent and not any("alt_text" in k or "first_comment" in k for data, _, _ in sent for k, _ in data)


# ---------------------------------------------------------------- 3. Facebook gets five (four until 2026-11-03)

def test_facebook_gets_the_cover_two_content_slides_and_the_closing_slide(topic, rotate, db, monkeypatch):
    db.create_run("r1", "01_topic")
    gen = MultiFormatGenerator()
    slides = [str(s) for s in gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])]
    fb_copy = [str(s) for s in gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=True)]
    picked = facebook_pick(fb_copy)
    assert len(picked) == 5 and picked[0] == fb_copy[0] and picked[-1] == fb_copy[-1] and picked == sorted(picked)
    assert facebook_pick(fb_copy[:3]) == fb_copy[:3]
    # A slide that had to be shortened or failed its layout check is passed over while another is clean.
    rec = still_layout.load(fb_copy[1])
    path = still_layout.sidecar(fb_copy[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    data[fb_copy[1].split("\\")[-1].split("/")[-1]] = dict(rec, shortened=["body"])
    path.write_text(json.dumps(data), encoding="utf-8")
    assert fb_copy[1] not in facebook_pick(fb_copy)
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers, data, files, timeout: sent.append(
        (headers["Idempotency-Key"], list(data), len(files)))
        or _response({"success": True, "results": {"facebook": {"success": True, "url": "u"}}}))
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "carousel", slides, caption="Cap",
                                                         platforms=["facebook"], retry_tag="retry1-facebook")
    key, data, nfiles = sent[0]
    # The caption is the post's: Upload-Post puts it on the first photo, which is the cover.
    assert nfiles == 5 and dict(data)["title"] == "Cap" and dict(data)["description"] == "Cap"
    assert key == "r1-carousel-1-retry1-facebook"


def test_a_dry_run_records_alt_text_first_comment_and_facebooks_four(topic, rotate, db):
    db.create_run("r1", "01_topic")
    gen = MultiFormatGenerator()
    slides = [str(s) for s in gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])]
    gen.generate_carousel_sequence(topic["photos"], ROMAN, topic["run"], facebook=True)
    res = MetaPublisher(db=db, publishers=[]).publish_photos("r1", "01_topic", "carousel", slides, caption="Cap",
                                                             first_comment="📷 credits", dry_run=True)
    assert res["planned_parts"] == 2
    rows = [json.loads(p["response_payload_json"]) for p in db.list_publications("r1")]
    assert [len(r["photos"]) for r in rows] == [7, 5]
    assert all(len(r["alt_text"]) == len(r["photos"]) and r["first_comment"] == "📷 credits" for r in rows)


# ---------------------------------------------------------------- 5. credits

def test_every_photo_that_needs_credit_is_credited_in_the_caption(topic, rotate):
    from main import PipelineCoordinator
    slides = [str(s) for s in MultiFormatGenerator().generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])]
    lines, readable = carousel_credits(slides)
    by, sa, pd = topic["images"]
    from src.multi_format import CAROUSEL_ADAPTED
    assert readable and lines == [CAROUSEL_SHARE_ALIKE, asset_attribution(by), asset_attribution(sa), CAROUSEL_ADAPTED]
    assert asset_attribution(pd) == ""                                # public domain asks for none
    payload = dict(ROMAN, caption="Reel text\n\n📷 Images via Wikimedia Commons: Artist 1 (CC BY 4.0)")
    caption, comment = PipelineCoordinator._carousel_post(payload, slides)
    assert caption == ROMAN["carousel_caption"] + "\n\n" + "\n".join(lines) and comment == ""
    assert PipelineCoordinator._carousel_caption(payload, slides) == caption
    # Slides drawn before records were kept: the Reel caption's summary line, as before.
    assert PipelineCoordinator._carousel_caption(payload) == ROMAN["carousel_caption"] + \
        "\n\n📷 Images via Wikimedia Commons: Artist 1 (CC BY 4.0)\n📷 Photos adapted: cropped, with text added."


def test_credits_that_do_not_fit_the_caption_go_in_the_first_comment(topic, rotate, db, monkeypatch):
    from main import PipelineCoordinator
    slides = [str(s) for s in MultiFormatGenerator().generate_carousel_sequence(topic["photos"], ROMAN, topic["run"])]
    long = dict(ROMAN, carousel_caption="Swipe through. " + "x" * 2050)
    caption, comment = PipelineCoordinator._carousel_post(long, slides)
    assert caption == long["carousel_caption"] and comment == "\n".join(carousel_credits(slides)[0])
    db.create_run("r1", "01_topic")
    sent = []
    monkeypatch.setattr(requests, "post", lambda url, headers, data, files, timeout: sent.append(dict(data))
                        or _response({"success": True, "results": {"instagram": {"success": True, "url": "u"}}}))
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    MetaPublisher(db=db, publishers=[up]).publish_photos("r1", "01_topic", "carousel", slides, caption=caption, first_comment=comment)
    assert sent[0]["instagram_first_comment"] == comment and sent[1]["facebook_first_comment"] == comment
    assert "facebook_first_comment" not in sent[0] and "instagram_first_comment" not in sent[1]


def test_credit_lines_travel_untouched_through_the_caption_rules():
    credit = "📷 Ann | File:Swipe left arrow → sign.jpg | CC BY 4.0 | https://x/File:Swipe | https://cc/by/4.0"
    caption = "Swipe right to see the gears ➡️\n\n#Gears\n\n" + credit
    for platforms in (["instagram"], ["facebook"]):
        out = _caption_for("carousel", caption, platforms)
        assert out.endswith("\n\n" + credit) and "➡" not in out.split(credit)[0]
    assert _caption_for("carousel", "Plain.\n\n" + credit, ["facebook"]) == "Plain.\n\n" + credit



def test_an_openverse_style_cc_by_sa_photo_is_credited_though_its_flag_is_unset():
    """rc68 review, 2026-10-28: Openverse entries leave attribution_required unset; a BY-SA carousel went out with
    only the licence line and no author."""
    from src.credits import asset_attribution, needs_attribution
    entry = {"file": "01_commons.jpg", "title": "Odesa catacombs", "artist": "Marion Golsteijn", "license": "CC BY-SA 4.0",
             "source_url": "https://example.org/a", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
             "attribution_required": None}
    assert needs_attribution(entry) and "Marion Golsteijn" in asset_attribution(entry)
    assert not needs_attribution({"license": "Public domain", "attribution_required": None})



def test_credits_say_the_photos_were_adapted():
    """CC BY 4.0 s3(a)(1)(B), rc68 review 2: indicate that the photos were changed."""
    from src.credits import caption_credit_line
    from src.multi_format import CAROUSEL_ADAPTED
    line = caption_credit_line({"images": [{"artist": "A. Photographer", "license": "CC BY 4.0", "attribution_required": True}]})
    assert "adapted" in line and line.startswith("📷 ")
    assert CAROUSEL_ADAPTED.startswith("📷 ") and "adapted" in CAROUSEL_ADAPTED
