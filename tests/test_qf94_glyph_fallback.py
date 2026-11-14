"""QF-94: drawn text never drops a character silently.

typography.drawable() removed every character its face had no glyph for ("ΑΓΙΑ ΣΟΦΙΑ" in Anton became "Σ", a
Cyrillic heading became empty) and the card drew text that was not the signed field. Now a field its face cannot
draw whole is set in a bundled fallback face (Noto subsets for Greek, Cyrillic and extended Latin, operator ruling
2026-11-10), matched to the face's weight, width and cap height, recorded per field; a field no bundled face can
draw is refused with a reason the review card shows.
"""
import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import font_engine as FE
from src import typography as T

GlyphRefused = getattr(FE, "GlyphRefused", type("GlyphRefusedMissing", (Exception,), {}))   # absent on the base

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = (("Anton", None), ("Montserrat", 600), ("Cinzel", 700), ("IBM Plex Mono", 500))
STRESS = ("ΑΓΙΑ ΣΟΦΙΑ", "Αγία Σοφία", "ΓΝΩΘΙ ΣΑΥΤΟΝ", "Москва", "КИРИЛЛИЦА", "Ħal Saflieni", "ĦAMRUN ħ",
          "İstanbul, Ağrı ılık şişe", "IĞDIR ŞANLIURFA", "Hà Nội, Việt Nam", "THÀNH PHỐ HỒ CHÍ MINH",
          "Łódź Ő ő ā Tōhoku ß", "Hagia Sophia (Αγία Σοφία)", "Moscow (Москва)")
ARABIC = ("ابن الهيثم", "Ibn al-Haytham (ابن الهيثم)", "صلاح الدين")


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / FE.LEDGER_NAME
    FE.set_glyph_ledger(path)
    yield path
    FE.set_glyph_ledger(None)


def _events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def _ink(text, font, size=(1600, 260), track=0):
    im = Image.new("L", size, 0)
    T._draw_tracked(ImageDraw.Draw(im), (20, 180), text, font, 255, "ls", track)
    return np.asarray(im)


@pytest.mark.parametrize("family,weight", FAMILIES)
@pytest.mark.parametrize("text", STRESS)
def test_drawable_never_drops_a_letter(family, weight, text):
    """The regression: drawable() returned a shorter string (letters removed) for every face without the glyphs."""
    font = T.get_font(family, 60, weight=weight)
    assert T.drawable(text, font) == text
    assert T.drawable(text, family) == text
    assert T.drawable(text.upper(), font) == text.upper()


@pytest.mark.parametrize("family,weight", FAMILIES)
@pytest.mark.parametrize("text", ARABIC)
def test_a_field_no_bundled_face_draws_is_refused_with_a_reason(family, weight, text, ledger):
    font = T.get_font(family, 60, weight=weight)
    with pytest.raises(GlyphRefused) as got:
        T.drawable(text, font)
    assert got.value.code == "no_glyph" and "Arabic" in got.value.reason and got.value.missing
    assert text in got.value.reason
    with pytest.raises(GlyphRefused):     # and never drawn with a letter missing, whichever path draws it
        ImageDraw.Draw(Image.new("L", (400, 100))).text((10, 10), text, font=font, fill=255)
    refused = [e for e in _events(ledger) if e["kind"] == "refused"]
    assert [e["text"] for e in refused] == [text] and refused[0]["scripts"] == ["Arabic"]


def test_greek_in_anton_is_set_whole_in_the_matched_fallback(ledger):
    anton = T.get_font("Anton", 80)
    face = FE.field_face("ΑΓΙΑ ΣΟΦΙΑ", anton)
    assert Path(face.path).name == "NotoSans-LGC.ttf" and face._rr_fallback_for == "Anton-Regular.ttf"
    # every letter drawn: the ink of the field equals the fallback face's own drawing of it, and has 10 glyphs'
    # worth of columns (Anton drew only the sigma)
    drawn, own = _ink("ΑΓΙΑ ΣΟΦΙΑ", anton), _ink("ΑΓΙΑ ΣΟΦΙΑ", face)
    assert np.array_equal(drawn, own)
    cols = np.flatnonzero(drawn.max(axis=0) > 127)
    sigma = np.flatnonzero(_ink("Σ", anton).max(axis=0) > 127)
    assert cols.size > 6 * sigma.size
    # matched: the same cap height as Anton at that size (within a pixel), the heaviest instance, condensed
    assert abs(T.cap_height(face) - T.cap_height(anton)) <= 1
    assert dict(face._rr_variations)["wght"] >= 800
    ev = _events(ledger)
    assert [(e["kind"], e["text"], e["face"], e["fallback"]) for e in ev] == [
        ("fallback", "ΑΓΙΑ ΣΟΦΙΑ", "Anton-Regular.ttf", "NotoSans-LGC.ttf")]
    assert set(ev[0]["chars"]) == set("ΑΓΙΟΦ") and ev[0]["scripts"] == ["Greek"]


def test_tracked_and_plain_draws_take_the_same_face():
    anton = T.get_font("Anton", 72)
    face = FE.field_face("МОСКВА", anton)
    for track in (0, 6):
        assert np.array_equal(_ink("МОСКВА", anton, track=track), _ink("МОСКВА", face, track=track))
        assert T.tracked_width("МОСКВА", anton, track) == T.tracked_width("МОСКВА", face, track)
    im_a, im_b = Image.new("L", (900, 200)), Image.new("L", (900, 200))
    ImageDraw.Draw(im_a).text((10, 150), "МОСКВА", font=anton, fill=255, anchor="ls")
    ImageDraw.Draw(im_b).text((10, 150), "МОСКВА", font=face, fill=255, anchor="ls")
    assert np.array_equal(np.asarray(im_a), np.asarray(im_b)) and np.asarray(im_a).max() == 255


def test_the_serif_falls_back_to_the_serif():
    cinzel = T.get_font("Cinzel", 64)
    face = FE.field_face("ΑΓΙΑ ΣΟΦΙΑ", cinzel)
    assert Path(face.path).name == "NotoSerif-LGC.ttf" and face._rr_fallback_for == "Cinzel-Variable.ttf"


def test_a_wrapped_field_is_set_in_one_face_and_recorded_once(ledger):
    """Per field: a line of the field that the brand face could draw is still drawn in the field's face."""
    draw = ImageDraw.Draw(Image.new("L", (8, 8)))
    text = "HAGIA SOPHIA MUSEUM AND MOSQUE ΑΓΙΑ ΣΟΦΙΑ"
    lines, font, short = T.fit_text(draw, text, "Anton", 80, 900, 3, 60)
    assert not short and " ".join(lines) == text and len(lines) > 1
    assert Path(font.path).name == "NotoSans-LGC.ttf"
    assert lines[0].isascii()                          # a line Anton could draw alone, set in the field's face
    lines2, font2 = T.fit_lines(draw, text, T.get_font("Anton", 80), 900, 3)
    assert " ".join(lines2) == text and Path(font2.path).name == "NotoSans-LGC.ttf"
    fallbacks = [e for e in _events(ledger) if e["kind"] == "fallback"]
    # the whole field once; its lines and fitting trials are written as parts of it, which the card folds in
    assert [e["text"] for e in fallbacks if not e.get("part_of")] == [text]
    assert all(e["part_of"] == text and e["text"].rstrip("…") in text for e in fallbacks if e.get("part_of"))


def test_plate_records_the_face_it_is_drawn_in():
    ground = Image.new("RGB", (1200, 300), (40, 40, 40))
    plate = T.plan_text(ground, (20, 200), "Αγία Σοφία", T.get_font("Montserrat", 64, weight=600), (240, 230, 210),
                        "ls")
    rec = plate.record()
    assert rec["font"] == "NotoSans-LGC.ttf" and rec["fallback_for"] == "Montserrat-Variable.ttf"
    plain = T.plan_text(ground, (20, 200), "Hagia Sophia", T.get_font("Montserrat", 64, weight=600),
                        (240, 230, 210), "ls")
    assert plain.record()["font"] == "Montserrat-Variable.ttf" and "fallback_for" not in plain.record()


def test_faces_that_cover_a_field_keep_it():
    """Maltese, Turkish and Vietnamese are in Montserrat and Anton: no fallback, nothing recorded."""
    for family, weight in (("Anton", None), ("Montserrat", 600)):
        font = T.get_font(family, 60, weight=weight)
        for text in ("ĦAL SAFLIENI ħ", "IĞDIR ŞANLIURFA ı", "THÀNH PHỐ HỒ CHÍ MINH"):
            assert FE.field_face(text, font) is font
    # a base letter and its combining mark are the precomposed letter the face has (HarfBuzz composes them)
    assert FE.missing_glyphs("Hà Nội", T.get_font("Anton", 60)) == ""


def test_emoji_are_still_removed_and_recorded(ledger):
    assert T.drawable("A marvel \U0001f30d.", "Montserrat") == "A marvel."
    ev = _events(ledger)
    assert [(e["kind"], e["text"]) for e in ev] == [("emoji_removed", "A marvel \U0001f30d.")]


def test_fallback_match_is_measured_for_any_face():
    """Face-agnostic (QF-87 may change the brand faces): the match is read from the faces, within the axes."""
    for family, weight in FAMILIES:
        brand = T.get_font(family, 100, weight=weight)
        for fb in brand._rr_fallbacks[:2]:               # the two Noto subsets
            values, factor = FE.fallback_match(str(brand.path), getattr(brand, "_rr_variations", ()), fb)
            axes = {tag: (lo, hi) for tag, lo, _d, hi in FE.inspect_font(fb).axes}
            assert all(axes[tag][0] <= v <= axes[tag][1] for tag, v in values)
            assert 0.7 < factor < 1.4
    assert T.fallback_files("Cinzel")[0] == "NotoSerif-LGC.ttf"
    assert T.fallback_files("Anton")[:2] == ["NotoSans-LGC.ttf", "NotoSerif-LGC.ttf"]
    assert "Anton-Regular.ttf" not in T.fallback_files("Anton") and "Montserrat-Variable.ttf" in T.fallback_files("Anton")


def test_a_character_only_another_brand_face_has_is_drawn_in_it():
    """The ruling refuses a field only when no bundled face has the glyph: Cinzel and the Noto subsets have no
    arrow, Montserrat has."""
    face = FE.field_face("Café → ½", T.get_font("Cinzel", 60))
    assert Path(face.path).name == "Montserrat-Variable.ttf" and face._rr_fallback_for == "Cinzel-Variable.ttf"


def test_fallback_faces_are_licensed_and_bundled():
    from src.font_evidence import font_rights
    for name in T.FALLBACK_FILES.values():
        ok, record = font_rights(ROOT / "fonts" / name)
        assert ok, record
        assert "Noto" in record["copyright"] and record["role"] == "fallback face (QF-94)"
        assert (ROOT / "fonts" / name).stat().st_size < 1_000_000
        assert name in T.bundled_font_files()
    for name in T.FALLBACK_FILES.values():
        cover = FE.inspect_font(str(ROOT / "fonts" / name)).coverage
        assert {0x0391, 0x03C9, 0x0416, 0x0436, 0x0126, 0x0131, 0x1EC7} <= cover
        assert not any(0x0600 <= cp <= 0x06FF for cp in cover)      # no Arabic: refused, not drawn


def test_design_card_refusal_carries_the_reason():
    from src.design_cards import CardRefused, _glyphs_ok
    _glyphs_ok("ΑΓΙΑ ΣΟΦΙΑ", "Anton", None)          # drawn in the fallback face: no refusal
    with pytest.raises(CardRefused) as got:
        _glyphs_ok("ابن الهيثم", "Anton", None)
    assert got.value.code == "no_glyph" and "Arabic" in got.value.reason


def test_review_card_names_the_surface_and_what_a_refusal_cost(tmp_path):
    from src.telegram_bot import glyph_notes
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        with FE.surface("cover"):
            T.drawable("ΑΓΙΑ ΣΟΦΙΑ", "Anton")
            T.drawable("ΑΓΙΑ ΣΟΦΙΑ", "Anton")            # once per field and surface
        with FE.surface("Story frame 2"), pytest.raises(GlyphRefused):
            T.drawable("Photo: 山田太郎 · CC BY-SA 3.0\nThis frame: cropped", "IBM Plex Mono")
        with FE.surface("graphic map"), pytest.raises(GlyphRefused):
            T.drawable("ابن الهيثم", "Montserrat")
        for text in ("A marvel \U0001f30d.", "Wow \u2728", "A marvel \U0001f30d."):
            T.drawable(text, "Montserrat")
    finally:
        FE.set_glyph_ledger(None)
    notes = glyph_notes(tmp_path)
    assert notes[0] == ("Cover: “ΑΓΙΑ ΣΟΦΙΑ” set in NotoSans-LGC.ttf (Anton-Regular.ttf has no "
                        "Α Γ Ι Ο Φ, Greek)")
    # review 2, finding 4: "not drawn" once, and the credit's two lines joined with " / "
    assert notes[1].startswith("Story frame 2: “Photo: 山田太郎 · CC BY-SA 3.0 / This frame: cropped” not drawn, so "
                               "the frame is left out and the Story posts short: no bundled face draws")
    assert notes[1].count("not drawn") == 1 and "(CJK)" in notes[1]
    assert notes[2].startswith("Graphic map:") and "the graphic is left out of the video" in notes[2] and "(Arabic)" in notes[2]
    assert notes[3:] == ["Emoji removed from 2 field(s) (no emoji in art)"]     # one count line, not one per field


def test_a_story_frame_credited_to_an_author_no_face_draws_needs_review_naming_the_frame(tmp_path):
    """Review 1, blocking 2: the Story (hook and reveal since QF-80), frame 2's photo credited (CC BY-SA) to a CJK
    author. The frame is refused (never drawn without the name) and the release asks the operator, naming the frame
    and the Story's shortfall, instead of passing."""
    from src import release
    from src.multi_format import MultiFormatGenerator

    def topic(name, entry):
        folder = tmp_path / "topic"
        folder.mkdir(exist_ok=True)
        rng = np.random.default_rng(7)
        Image.fromarray((rng.random((2000, 1200, 3)) * 120 + 60).astype("uint8")).save(folder / name)
        path = folder / "credits.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"reviewed": True, "images": []}
        data["images"].append(dict(entry, file=name))
        path.write_text(json.dumps(data), encoding="utf-8")
        return folder / name
    by_sa = {"title": "File:x.jpg", "license": "CC BY-SA 3.0", "attribution_required": True,
             "license_url": "https://creativecommons.org/licenses/by-sa/3.0",
             "source_url": "https://commons.wikimedia.org/wiki/File:x.jpg"}
    ok = topic("ok.jpg", dict(by_sa, artist="Jane Doe"))
    cjk = topic("cjk.jpg", dict(by_sa, artist="山田太郎"))
    gen = MultiFormatGenerator()
    gen._match_photos = lambda cards, stills: [ok, cjk]
    slide = {"kicker": "THE HEIST", "line1": "A ROOM", "line2": "OF AMBER", "body": "It was taken in 1941."}
    payload = {"title": "x", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
               "story_slides": [slide, dict(slide, kicker="THE FATE")], "poll": {}}
    run = tmp_path / "run"
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        frames = gen.generate_story_sequence(ok, payload, run, scene_images=[ok, cjk])
    finally:
        FE.set_glyph_ledger(None)
    assert [f.name for f in frames] == ["story_01_hook.jpg"]
    problems = release.story_refusals(run)
    assert len(problems) == 1 and problems[0].startswith("Story frame 2 not drawn: no bundled face draws")
    assert "(CJK)" in problems[0] and "山田太郎" in problems[0] and "would post 1 of 2 frames" in problems[0]
    report = release.write_release_report({"script_payload": {}, "master_video_path": str(run / "x_master.mp4")})
    check = next(c for c in report["checks"] if c["name"] == "story_frames")
    assert check["status"] == "needs_review" and check["detail"] == problems
    assert release.release_problems(report) and any(p.startswith("story_frames: Story frame 2") for p in
                                                     release.release_problems(report))
    events = [e for e in _events(tmp_path / FE.LEDGER_NAME) if e["kind"] == "refused" and not e.get("part_of")]
    assert {e["where"] for e in events} == {"Story frame 2"}


def test_a_fallback_face_reports_the_size_asked_for():
    """Review 1, finding 3: a renderer that rebuilds a family's font from a fitted font's size gets the design
    size, not the fallback's cap-height-matched raster size."""
    anton = T.get_font("Anton", 80)
    face = FE.field_face("МОСКВА 1941", anton)
    assert face.size == 80 and face._rr_px == 96
    again = T.get_font("Anton", face.size)
    assert again is anton and FE.field_face("МОСКВА 1941", again) is face
    draw = ImageDraw.Draw(Image.new("L", (8, 8)))
    _lines, fitted, _ = T.fit_text(draw, "МОСКВА № 7", "Anton", 80, 2000, 1, 60)
    assert fitted.size == 80 and abs(T.cap_height(fitted) - T.cap_height(anton)) <= 1


def test_a_caption_word_no_face_draws_is_measured_not_fatal(tmp_path):
    """Review 1, finding 5: libass draws captions (with a system face for what the bundled ones lack): an Arabic
    word in the narration is measured in the caption face, never refused. A measure records nothing (the caption
    measures are cached across runs): build_subtitles records the word (test below)."""
    from src.video_composer import unkerned_length
    font = T.get_font("Montserrat", 80)
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        width = unkerned_length(font, "ابن الهيثم")
        assert width == FE.shape_in_face("ابن الهيثم", font, features=FE.LIBASS_FEATURES).advance > 0
        assert unkerned_length(font, "AMBER") == FE.shape("AMBER", font, features=FE.LIBASS_FEATURES).advance
    finally:
        FE.set_glyph_ledger(None)
    assert _events(tmp_path / FE.LEDGER_NAME) == []


# Review 2, blocking 1: measured on base 4b288e84 (the caption face's own boxes and advances for the Arabic word)
REEL_ARABIC = "THE SCHOLAR ابن الهيثم WROTE"
REEL_ARABIC_BASE = (891.53, 0.74, 889.94)          # _libass_line at 80: (advance, ink left, ink right)


def test_the_reel_caption_measure_takes_a_word_no_bundled_face_draws():
    """Review 2, blocking 1: the 9:16 caption fitter boxes each run's first and last glyph (_glyph_box). It went
    through the fallback lookup and raised GlyphRefused on an Arabic word, so the caption build stopped. The glyph
    is now boxed in the caption face itself, as unkerned_length measures it: the measure is the base's."""
    from src import video_composer as vc
    words = REEL_ARABIC.split()
    line = vc._libass_line(tuple(words), 80.0)
    assert all(abs(got - want) <= 0.05 for got, want in zip(line, REEL_ARABIC_BASE)), line
    assert abs(vc._caption_ink_width(words, 80) - (REEL_ARABIC_BASE[2] - REEL_ARABIC_BASE[1])) <= 2
    mixed = [*words, "Αθήνα"]      # the reviewer's case: Greek set in the fallback
    assert vc._caption_ink_width(mixed, 80) > vc._caption_ink_width(words, 80)
    assert vc._caption_fit_size(mixed) >= vc.CAPTION_HARD_MIN
    vc._caption_ink_offset(mixed, 80)


@pytest.mark.parametrize("width,height", [(1080, 1920), (1920, 1080)])
def test_build_subtitles_records_a_caption_word_no_face_draws_under_captions(tmp_path, width, height):
    """Review 2, blocking 1: the caption build goes on, and the word is on the review card under "Captions" (not
    "A still", not "that render stopped"), once per run even when the cached measures are already warm."""
    from src.telegram_bot import glyph_notes
    from src.video_composer import VideoComposer
    words = [{"word": w, "start": 0.1 + 0.4 * k, "dur": 0.3} for k, w in enumerate(REEL_ARABIC.split())]
    for run in ("a", "b"):                                    # the second run hits warm caches
        run_dir = tmp_path / run
        run_dir.mkdir()
        FE.set_glyph_ledger(run_dir / FE.LEDGER_NAME)
        try:
            ass = VideoComposer().build_subtitles(words, run_dir / "c.ass", width=width, height=height)
        finally:
            FE.set_glyph_ledger(None)
        assert "ابن" in ass.read_text(encoding="utf-8")
        ev = _events(run_dir / FE.LEDGER_NAME)
        assert {(e["kind"], e["where"], e["text"]) for e in ev} == {
            ("caption_system_face", "captions", "ابن"), ("caption_system_face", "captions", "الهيثم")}
        notes = glyph_notes(run_dir)
        assert notes[0] == ("Captions: “ابن” has ا ب ن (Arabic), which no bundled face draws; libass draws them "
                            "in a system face")
        assert len(notes) == 2 and all(n.startswith("Captions: ") and "not drawn" not in n for n in notes)


@pytest.mark.parametrize("where,effect", [
    ("graphic then/now", "so the graphic is left out of the video"),
    ("photo callouts", "so the graphic is left out of the video"),
    ("YouTube thumbnail", "so that render stopped"), ("cover", "so that render stopped")])
def test_each_surface_is_named_with_what_a_refusal_cost(tmp_path, where, effect):
    """Review 2, finding 2: then/now (both paths), the documentary's callouts and thumbnails run on a named surface."""
    from src.telegram_bot import glyph_notes
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        with pytest.raises(GlyphRefused):
            FE.on_surface(where, T.drawable, "ابن الهيثم", "Montserrat")
    finally:
        FE.set_glyph_ledger(None)
    note = glyph_notes(tmp_path)[0]
    assert note.startswith(where[0].upper() + where[1:] + ": “ابن الهيثم” not drawn, " + effect + ": "), note


def test_the_run_names_the_surface_of_then_now_callouts_and_thumbnails():
    """Review 2, finding 2: main.py submits each of these on its surface, on the Reel and the documentary path."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert src.count('on_surface, "graphic then/now", then_now.make') == 2
    assert len(re.findall(r'on_surface, "photo callouts",\s*callouts\.make', src)) == 2
    assert 'on_surface("YouTube thumbnail", self.visual_engine.generate_youtube_thumbnail' in src
    assert src.count('on_surface("cover", self.visual_engine.generate_thumbnails') == 2


def test_the_contact_sheet_draws_an_archive_title_no_bundled_face_draws(tmp_path):
    """An operator sheet, not art: a CJK or Arabic file title is drawn as before (visible .notdef boxes), never a
    failed sheet (the fallback lookup raised there too)."""
    from src.credits import write_contact_sheet
    Image.new("RGB", (400, 300), (90, 80, 70)).save(tmp_path / "a.jpg")
    path = write_contact_sheet(tmp_path, [{"file": "a.jpg", "title": "File:山田 ابن.jpg"}])
    assert path.is_file()


def test_a_contact_sheet_inside_a_run_records_no_false_refusal(tmp_path):
    """Review 3, finding 2 (QF-94b): photo screening redraws the sheet inside the run's RunLog. Its banner was first
    drawn through the fallback lookup, which recorded a refusal ("not drawn, so that render stopped") before the
    plain face drew it: a false note on the review card. The banner is now drawn in the plain face directly."""
    from src.credits import write_contact_sheet
    from src.telegram_bot import glyph_notes
    Image.new("RGB", (400, 300), (90, 80, 70)).save(tmp_path / "a.jpg")
    Image.new("RGB", (400, 300), (90, 80, 70)).save(tmp_path / "b.jpg")
    images = [{"file": "a.jpg", "title": "File:ابن الهيثم 1958.jpg"},
              {"file": "b.jpg", "title": "File:x.jpg", "vision_review": {"reason": "shows 東京 tower at night"}}]
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        path = write_contact_sheet(tmp_path, images)
    finally:
        FE.set_glyph_ledger(None)
    assert path.is_file()
    assert _events(tmp_path / FE.LEDGER_NAME) == [] and glyph_notes(tmp_path) == []


def test_a_caption_word_built_in_both_formats_is_one_card_line(tmp_path):
    """Review 3, finding 4 (QF-94b): one run building 9:16 (Anton, upper-cased) and 16:9 (Montserrat) captions
    records the word in each face; the card, which does not name the face, lists it once."""
    from src.telegram_bot import glyph_notes
    from src.video_composer import VideoComposer
    words = [{"word": w, "start": 0.1 + 0.4 * k, "dur": 0.3} for k, w in enumerate(["THE", "東京x", "TOWER"])]
    FE.set_glyph_ledger(tmp_path / FE.LEDGER_NAME)
    try:
        VideoComposer().build_subtitles(words, tmp_path / "v.ass", width=1080, height=1920)
        VideoComposer().build_subtitles(words, tmp_path / "w.ass", width=1920, height=1080)
    finally:
        FE.set_glyph_ledger(None)
    assert len(_events(tmp_path / FE.LEDGER_NAME)) == 2           # the ledger keeps each face's event
    assert glyph_notes(tmp_path) == [("Captions: “東京X” has 東 京 (CJK), which no bundled face draws; libass draws "
                                      "them in a system face")]


def test_an_emoji_is_labelled_emoji_not_by_its_name():
    """Review 3, finding 6 (QF-94b): "🔥" was labelled "(Fire)", the first word of its Unicode name."""
    assert FE.scripts_of("🔥") == ["emoji"] and FE.scripts_of("✨東") == ["CJK", "emoji"]
    assert FE.scripts_of("ابن") == ["Arabic"] and FE.scripts_of("→") == ["Rightwards"]


def test_the_audience_chart_falls_back_to_text_on_a_title_no_face_draws(db, tmp_path, monkeypatch):
    """/analytics chart: a title no bundled face draws makes the chart refuse; its figures come as text, saying why."""
    from src import audience_chart, config
    from src.control import make_command_handler
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    db.create_run("run_a", "01_topic", content_format="reel")
    db.update_run("run_a", "approved", script_payload={"title": "東京 Reel"})
    db.record_publication("01_topic", "run_a", "upload-post", "published", {})
    monkeypatch.setattr(audience_chart, "chart_for", _refuse)
    h = make_command_handler(db, lambda: None, lambda: "", send_photo=lambda *a, **k: True)
    text, _ = h("cachart", "")
    assert "The chart could not draw 東 京 (CJK); its figures are below." in text


def _refuse(*_a, **_k):
    raise GlyphRefused("東京 Reel", "Montserrat-Variable.ttf", "東京")


def test_a_plate_built_from_the_brand_font_records_the_face_that_drew_it():
    """Review 1, finding 7: TextPlate built straight from the brand font (design_cards, graphics._ThinPlate)."""
    anton = T.get_font("Anton", 72)
    plate = T.TextPlate("МОСКВА", anton, (240, 230, 210), (20, 150), "ls", 0, 0, (0, 0, 0), 21, False,
                        (900, 220))
    rec = plate.record()
    assert rec["font"] == "NotoSans-LGC.ttf" and rec["fallback_for"] == "Anton-Regular.ttf"
    assert rec["px"] == 72 and rec["fallback_px"] == 87


def test_a_substring_field_drawn_without_drawable_is_recorded(ledger):
    """Review 1, finding 6: every distinct string drawn in a fallback face is in the ledger; a part of the field
    being drawn is marked part_of, a field drawn on its own is not."""
    anton = T.get_font("Anton", 60)
    T.drawable("МОСКВА И ПЕТЕРБУРГ", anton)
    T._draw_tracked(ImageDraw.Draw(Image.new("L", (900, 200))), (10, 150), "МОСКВА", anton, 255, "ls", 4)
    with FE.surface("graphic timeline"):
        ImageDraw.Draw(Image.new("L", (900, 200))).text((10, 150), "МОСКВА", font=anton, fill=255)
    ev = [(e["text"], e.get("part_of"), e["where"]) for e in _events(ledger) if e["kind"] == "fallback"]
    assert ("МОСКВА", "МОСКВА И ПЕТЕРБУРГ", None) in ev
    assert ("МОСКВА", None, "graphic timeline") in ev       # its own field on its own surface


def test_run_log_points_the_ledger_at_the_run(tmp_path):
    from src.ops import RunLog
    sink = RunLog(tmp_path).start()
    try:
        T.drawable("Москва ΑΓΙΑ", "Anton")
    finally:
        sink.stop()
    assert [e["text"] for e in _events(tmp_path / FE.LEDGER_NAME)] == ["Москва ΑΓΙΑ"]
    T.drawable("ΚΑΙ ΑΛΛΟ", "Anton")                 # after the run: logged, not written to its ledger
    assert len(_events(tmp_path / FE.LEDGER_NAME)) == 1


@pytest.mark.parametrize("refused,name,kept", [(1, "story_02_reveal.jpg", "story_01_hook.jpg"),
                                               (0, "story_01_hook.jpg", "story_02_reveal.jpg")])
def test_a_story_frame_no_face_can_draw_is_refused_and_the_others_drawn(tmp_path, monkeypatch, refused, name, kept):
    """The Story's frames since QF-80 are hook and reveal: either one refused is not written (an earlier render of it
    removed), and the other is drawn."""
    from src.multi_format import MultiFormatGenerator
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1080, 1920), (90, 80, 70)).save(photo)
    gen = MultiFormatGenerator()
    real = gen._render_story_frame

    def frame(ph, slide, i, count, poll, tight=False):
        if i == refused:
            raise GlyphRefused("ابن", "IBMPlexMono-Medium.ttf", "ابن")
        return real(ph, slide, i, count, poll, tight=tight)
    monkeypatch.setattr(gen, "_render_story_frame", frame)
    slide = {"kicker": "THE HEIST", "line1": "A ROOM", "line2": "OF AMBER", "body": "It was taken in 1941."}
    stale = tmp_path / "run" / "stories" / name
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    payload = {"title": "x", "narration_script": "x", "first_comment": "?", "hashtags": ["#x"],
               "story_slides": [slide, slide], "poll": {}}
    frames = gen.generate_story_sequence(photo, payload, tmp_path / "run", scene_images=[photo])
    assert [f.name for f in frames] == [kept]
    assert not stale.exists()
    layout = json.loads((tmp_path / "run" / "stories" / "layout.json").read_text(encoding="utf-8"))
    assert [e["frame"] for e in layout] == ["story_01_hook.jpg", "story_02_reveal.jpg"]
    assert "Arabic" in layout[refused]["refused"] and "refused" not in layout[1 - refused]
