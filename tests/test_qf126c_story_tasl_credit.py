"""QF-126c (operator, 2026-11-14: "follow the best practice"): a Story frame's photo credit follows Creative Commons'
recommended attribution (TASL): the title where the record supplies one, the author, the source (the address of
the photo's page, as text: a frame has no link; the shortest official one, the page by its Commons page id when the
run knows it), the licence's name and address, and the note of what was changed. A photo whose complete credit
cannot be set legibly on a frame is not used; a frame is never drawn with a credit cut.

Before: "Photo: <author> · <licence> · adapted: ..." and the licence's address, no title and no source.
"""
import concurrent.futures
import inspect
import itertools
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

from src import multi_format, safe_zones, typography
from src.multi_format import MultiFormatGenerator, story_credit, story_credit_report

PAGE = "https://commons.wikimedia.org/wiki/File:Nasca_-_Colibri_-_kolib%C5%99%C3%ADk_-_panoramio.jpg"
BY = {"title": "File:Nasca - Colibri - kolibřík - panoramio.jpg", "artist": "Pavel Špindler", "license": "CC BY 3.0",
      "license_url": "https://creativecommons.org/licenses/by/3.0", "source_url": PAGE, "attribution_required": True}
PD = {"title": "File:Plain test photo.jpg", "artist": "Test", "license": "Public domain",   # a synthetic record
      "source_url": "https://commons.wikimedia.org/wiki/File:Plain_test_photo.jpg", "attribution_required": False}
# A synthetic record whose name and title are long enough that the complete credit, with the page's full address,
# needs more than the frame's lines even at the type floor (the BY-SA frame adds its own licence line)
LONG = dict(BY, artist="A Photographer With A Long Name from A Long Place Name, with A Museum's Archive Team",
            title="File:" + "A bronze fragment seen from the front under raking light in a museum case.jpg",
            source_url="https://commons.wikimedia.org/wiki/File:" + "A_bronze_fragment_seen_from_the_front.jpg",
            license="CC BY-SA 3.0", license_url="https://creativecommons.org/licenses/by-sa/3.0")
SLIDE = {"kicker": "DRAWN FOR THE SKY", "line1": "A BIRD", "line2": "THE SIZE OF A FIELD",
         "body": "The hummingbird figure is ninety-three metres long, cut into the desert floor."}


def _topic(tmp_path, entry, name, colour=None):
    folder = tmp_path / "topic"
    folder.mkdir(exist_ok=True)
    photo = folder / name
    if colour:
        Image.new("RGB", (1200, 2000), colour).save(photo)
    else:
        rng = np.random.default_rng(len(name))
        Image.fromarray((rng.random((2000, 1200, 3)) * 120 + 60).astype("uint8")).save(photo)
    path = folder / "credits.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"reviewed": True, "images": []}
    data["images"].append(dict(entry, file=name))
    path.write_text(json.dumps(data), encoding="utf-8")
    return photo


def _payload():
    return {"title": "Nazca", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
            "story_slides": [SLIDE, dict(SLIDE, kicker="THE REVEAL")]}


def _manifest_credit(run):
    return json.loads((run / "companion_posting.json").read_text(encoding="utf-8"))["story_credits"]


def test_the_frame_names_title_author_source_licence_and_its_address_and_the_change(tmp_path):
    text = " ".join(story_credit(_topic(tmp_path, BY, "by.jpg"))["lines"])
    assert "“Nasca - Colibri - kolibřík - panoramio”" in text            # the title, without "File:" and ".jpg"
    assert "by Pavel Špindler" in text                                   # the author
    # the source as text, the address a browser shows (no scheme, the escapes as the letters they stand for)
    assert "commons.wikimedia.org/wiki/File:Nasca_-_Colibri_-_kolibřík_-_panoramio.jpg" in text
    assert "CC BY 3.0 creativecommons.org/licenses/by/3.0" in text        # the licence's name and its address
    assert "adapted: cropped, with text added" in text                    # what was changed
    order = [text.index(x) for x in ("“Nasca", "Pavel", "commons.wikimedia.org", "CC BY 3.0", "adapted")]
    assert order == sorted(order)                                         # in TASL order


def test_the_source_is_the_shortest_official_address_when_the_run_knows_the_page_id(tmp_path):
    photo = _topic(tmp_path, BY, "by.jpg")
    run = tmp_path / "run"
    run.mkdir()
    (run / "source_links.json").write_text(json.dumps({"page_ids": {PAGE: 7311552}}), encoding="utf-8")
    gen = MultiFormatGenerator()
    frames = gen.generate_story_sequence(photo, _payload(), run, scene_images=[photo])
    credit = " ".join(_manifest_credit(run)["story_01_hook.jpg"])
    assert "commons.wikimedia.org/?curid=7311552" in credit and "File:Nasca" not in credit.split("”", 1)[1]
    assert story_credit_report(frames)["problems"] == []


def test_the_lookup_for_page_ids_is_made_for_the_candidates_and_recorded_for_the_run(tmp_path):
    photo, pd = _topic(tmp_path, BY, "by.jpg"), _topic(tmp_path, PD, "pd.jpg")
    run = tmp_path / "run"
    asked = []

    def lookup(urls):
        asked.append(sorted(urls))
        return {PAGE: 7311552}
    assert "lookup" in inspect.signature(MultiFormatGenerator.generate_story_sequence).parameters
    MultiFormatGenerator().generate_story_sequence(photo, _payload(), run, scene_images=[photo, pd], lookup=lookup)
    assert asked == [[PAGE]]                                    # only the photo that asks for a credit
    assert json.loads((run / "source_links.json").read_text(encoding="utf-8")) == {"page_ids": {PAGE: 7311552}}
    assert "commons.wikimedia.org/?curid=7311552" in " ".join(_manifest_credit(run)["story_01_hook.jpg"])


def test_a_photo_whose_credit_cannot_be_set_whole_is_not_used(tmp_path):
    long, pd, pd2 = _topic(tmp_path, LONG, "long.jpg"), _topic(tmp_path, PD, "pd.jpg"), _topic(tmp_path, PD, "pd2.jpg")
    seen = []
    gen = MultiFormatGenerator()
    gen._match_photos = lambda cards, stills: seen.append(list(stills)) or [stills[0]] * len(cards)
    frames = gen.generate_story_sequence(long, _payload(), tmp_path / "run", scene_images=[long, pd, pd2])
    assert seen == [[pd, pd2]]
    assert len(frames) == 2 and story_credit_report(frames)["problems"] == []


def test_with_no_photo_whose_credit_fits_the_story_is_held_not_cut(tmp_path):
    long = _topic(tmp_path, LONG, "long.jpg")
    run = tmp_path / "run"
    raised = None
    try:
        MultiFormatGenerator().generate_story_sequence(long, _payload(), run, scene_images=[long])
    except Exception as e:  # noqa: BLE001 - the type is asserted below
        raised = e
    assert isinstance(raised, typography.LayoutError), raised
    assert not list((run / "stories").glob("story_*.jpg"))      # no frame drawn
    # the pipeline holds that deck alone, under "its text would not fit whole", with the reason
    import main
    future = concurrent.futures.Future()
    future.set_exception(raised)
    payload = {}
    assert main.hold_layout_error(future, run, "stories", payload) == []
    assert payload["layout_holds"][0]["deck"] == "stories"
    assert "credit" in payload["layout_holds"][0]["error"]


def test_a_frame_whose_credit_takes_more_than_its_lines_is_a_release_problem(tmp_path):
    photo = _topic(tmp_path, BY, "by.jpg")
    run = tmp_path / "run"
    frames = MultiFormatGenerator().generate_story_sequence(photo, _payload(), run, scene_images=[photo])
    assert story_credit_report(frames)["problems"] == []
    layout_file = run / "stories" / "layout.json"
    layout = json.loads(layout_file.read_text(encoding="utf-8"))
    credit = layout[0]["photo_credit"]
    credit["drawn"] = credit["drawn"] + ["x"] * 6               # as a redraw on a photo whose credit is too long draws it
    credit["fits"] = False
    layout_file.write_text(json.dumps(layout), encoding="utf-8")
    problems = story_credit_report(frames)["problems"]
    assert len(problems) == 1 and problems[0].startswith("story_01_hook.jpg") and "lines" in problems[0]


def test_a_frame_drawn_before_the_page_id_was_known_is_still_complete(tmp_path):
    photo = _topic(tmp_path, BY, "by.jpg")
    run = tmp_path / "run"
    frames = MultiFormatGenerator().generate_story_sequence(photo, _payload(), run, scene_images=[photo])
    (run / "source_links.json").write_text(json.dumps({"page_ids": {PAGE: 7311552}}), encoding="utf-8")
    assert story_credit_report(frames)["problems"] == []        # the full address is the same page, credited whole


def test_the_complete_credit_sits_in_the_story_box_under_the_text_on_a_white_photo(tmp_path):
    # the worst real form: share-alike (the frame's own licence line too) and the page's full address, no page id
    photo = _topic(tmp_path, dict(BY, license="CC BY-SA 3.0", license_url="https://creativecommons.org/licenses/by-sa/3.0"),
                   "sa.jpg",
                   colour=(250, 250, 250))
    for index in (0, 1):
        _, lay = MultiFormatGenerator()._render_story_frame(photo, SLIDE, index, 2, {})
        credit = lay.meta["photo_credit"]
        assert credit.get("fits") and credit["complete"] and credit["px"] >= multi_format.STORY_CREDIT_MIN_PX
        assert len(credit["drawn"]) <= multi_format.STORY_CREDIT_LINES
        assert credit["contrast"] >= multi_format.STORY_CREDIT_CONTRAST and lay.problems() == []
        assert safe_zones.inside(tuple(credit["box"]), safe_zones.safe_box("story"))
        assert lay.meta["text_box"][3] < credit["box"][1]
        # every character of every line, nothing cut (an address may be broken across lines)
        chars = ["".join(" ".join(x).replace("·", " ").split()) for x in (credit["drawn"], credit["lines"])]
        assert chars[0] == chars[1]


def test_an_address_wider_than_the_line_is_broken_only_where_a_printed_address_may_be(tmp_path):
    assert hasattr(multi_format, "story_credit_fit")
    from PIL import ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    address = "commons.wikimedia.org/wiki/File:Nasca_-_Colibri_-_kolibřík_-_panoramio_aerial_view_of_the_figure.jpg"
    setting = multi_format.story_credit_fit(d, [address], 600)
    drawn = setting["drawn"]
    assert setting["fits"] and len(drawn) > 1 and "".join(drawn) == address
    assert all(typography.text_size(d, ln, setting["font"])[0] <= 600 for ln in drawn)
    for before, after in itertools.pairwise(drawn):            # after a "/", or before "_", "-", "." or "?"
        assert before.endswith("/") or after[0] in "_-.?", (before, after)
    # an address that fits is never broken, at the largest size that sets it
    whole = multi_format.story_credit_fit(d, ["commons.wikimedia.org/?curid=7311552"], 600)
    assert whole["drawn"] == ["commons.wikimedia.org/?curid=7311552"] and whole["px"] == multi_format.STORY_CREDIT_PX



# ---------------------------------------------------------------- review 1: thin lines, the pools, the booked notice

ROOT = Path(__file__).resolve().parent.parent
BOUND = 999_999_999


def _pool_entries(assets):
    for credits_file in sorted(assets.glob("*/credits.json")):
        data = json.loads(credits_file.read_text(encoding="utf-8"))
        for entry in data if isinstance(data, list) else data.get("images", []):
            if isinstance(entry, dict) and entry.get("file"):
                yield credits_file.parent.name, entry


def _staged(folder, entry, colour=None):
    folder.mkdir(parents=True)
    (folder / "credits.json").write_text(json.dumps({"images": [dict(entry, file="p.jpg")]}), encoding="utf-8")
    photo = folder / "p.jpg"
    if colour:
        Image.new("RGB", (1200, 2000), colour).save(photo)
    return photo


def test_a_short_thin_credit_line_at_the_floor_is_drawn_and_measured():
    # Review 1, blocking 1: at 22 px a line holding only a name ("by Pinakpani") of IBM Plex Mono 500 has ~2 px
    # strokes and no eroded interior; plan_text raised TypeError there, and the frame (and the run's packaging) failed
    assert hasattr(multi_format, "story_credit_plates")
    from src import brand
    from src.typography import get_font
    font = get_font(brand.DATA_FONT, multi_format.STORY_CREDIT_MIN_PX, weight=500)
    for colour in ((250, 250, 250), (128, 128, 128), (30, 30, 30)):   # the frame's scrim comes first on a real photo
        canvas = Image.new("RGB", (1080, 240), colour)
        raised = None
        try:
            _, plates = multi_format.story_credit_plates(canvas, 84, 60, 29, ["by Pinakpani", "Argenberg"], font,
                                                         brand.STONE_LT)
        except TypeError as e:
            raised = e
        assert raised is None, raised
        assert all(p.contrast is not None for p in plates)       # measured on its drawn colour


def test_the_development_photos_whose_short_lines_crashed_draw_their_frames(tmp_path):
    # the two committed development records review 1 found: judged to fit, then a TypeError on the frame
    wanted = {("kailasa_temple_monolith", "03_commons.jpg"), ("tunguska_event_1908", "15_commons.jpg")}
    found = [(t, e) for t, e in _pool_entries(ROOT / "assets") if (t, e["file"]) in wanted]
    assert len(found) == 2
    for topic, entry in found:
        photo = _staged(tmp_path / topic, entry, colour=(250, 250, 250))
        # freeze c5 PLATES-14: the Story column keeps the margin token (902 px, was 912), so the Tunguska credit no
        # longer sets in STORY_CREDIT_LINES at the floor: the Story does not choose that photograph (story_credit_fits,
        # the designed fallback); a frame drawn on it anyway draws whole and says so
        fits = multi_format.story_credit_fits(photo, {})
        assert fits or topic == "tunguska_event_1908", topic
        raised, lay = None, None
        try:
            _, lay = MultiFormatGenerator()._render_story_frame(photo, SLIDE, 1, 2, {})
        except TypeError as e:
            raised = e
        assert raised is None, raised
        credit = lay.meta["photo_credit"]
        assert bool(credit["fits"]) == fits and credit["complete"]
        assert credit["contrast"] >= multi_format.STORY_CREDIT_CONTRAST and (lay.problems() == [] or not fits)


def _pools():
    out = [ROOT / "assets"]
    sample = os.environ.get("RR_PROD_SAMPLE")       # the production sample's root when given (never in CI)
    if sample and (Path(sample) / "assets").is_dir():
        out.append(Path(sample) / "assets")
    return out


def test_every_credit_judged_to_fit_is_drawn_without_error_in_every_pool(tmp_path):
    from PIL import ImageDraw

    from src import brand
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    ground = Image.new("RGB", (1080, 360), brand.INK)   # every glyph mask measured (the halo search: the frame tests)
    judged = drawn = 0
    for k, pool in enumerate(_pools()):
        for n, (_, entry) in enumerate(_pool_entries(pool)):
            photo = _staged(tmp_path / f"{k}_{n:04d}", entry)
            for pages in ({}, {entry.get("source_url"): BOUND}):   # without and with a page id
                credit = story_credit(photo, pages)
                if not credit["required"] or not (credit["source"] and credit["license_url"]):
                    continue
                setting = multi_format.story_credit_fit(d, credit["lines"])   # what story_credit_fits judges by
                if not setting["fits"]:
                    continue
                judged += 1
                lead = round(setting["px"] * multi_format.STORY_CREDIT_LEADING / multi_format.STORY_CREDIT_PX)
                _, plates = multi_format.story_credit_plates(ground, 84, 40, lead, setting["drawn"], setting["font"],
                                                             brand.STONE_LT)
                assert all(p.contrast is not None for p in plates), credit["lines"]
                drawn += 1
    assert judged == drawn and judged > 200


def test_a_booked_story_with_an_older_credit_says_why_first_and_keeps_the_whole_reason_on_details(
        db, tmp_path, curated_payload, monkeypatch):
    # Review 1, blocking 2: the notice quoted the drawn and needed credits first and was cut at 300 characters
    # mid-word, before its reason; its only button, Retry, failed again
    from main import PipelineCoordinator
    from src import notifications, posting_view
    photo = _topic(tmp_path, BY, "by.jpg")
    run_dir = tmp_path / "r1"
    frames = MultiFormatGenerator().generate_story_sequence(photo, _payload(), run_dir, scene_images=[photo])
    layout_file = run_dir / "stories" / "layout.json"
    layout = json.loads(layout_file.read_text(encoding="utf-8"))
    for e in layout:   # drawn with the credit's form before QF-126c, as every Story booked before it is
        e["photo_credit"]["lines"] = ["Photo: Pavel Špindler · CC BY 3.0 · adapted: cropped, with text added",
                                      "creativecommons.org/licenses/by/3.0"]
    layout_file.write_text(json.dumps(layout), encoding="utf-8")
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    monkeypatch.setattr("src.config.TELEGRAM_CLEAN_CHAT", True)
    c = PipelineCoordinator(db, schedule_posts=True)
    curated_payload.update(topic_id="01_topic", human_reviewed=True)
    db.create_run("r1", "01_topic")
    seen = []
    c._alert = lambda text, buttons=None, **item: seen.append((text, buttons))
    stored = {}
    monkeypatch.setattr(c.db, "update_scheduled_post", lambda pid, status, detail=None: stored.update(detail or {}))
    post = {"id": 7, "run_id": "r1", "kind": "story", "files": [str(f) for f in frames]}
    assert c._post_photos(post, db.get_run("r1"), "story") == "failed"
    text = " ".join(notifications.plain(seen[-1][0]).split())
    whole = stored["error"].removeprefix("Story credits: ")
    assert len(whole) > 300                                          # what the old 300-character cut took apart
    assert "Story frame 1: drawn with another credit" in text and "the Stories must be drawn again" in text
    assert len(text) <= notifications.QUOTE_LIMIT
    assert whole in text or text.endswith(("…; the whole reason is on Details", "…; more of the reason is on Details"))
    assert "Retry" not in str(seen[-1][1]) and "Details" in str(seen[-1][1])
    shown = posting_view.post_error(stored)                          # Details: the reason whole, or cut at a word
    assert shown == stored["error"] or (shown.endswith("…") and stored["error"].startswith(shown[:-1]))
