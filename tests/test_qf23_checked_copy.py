"""Checked facts and their qualifications must survive text fitting intact."""

import pytest
from PIL import Image, ImageDraw

from src import typography


@pytest.mark.parametrize("text", [
    "The object is about two thousand years old, according to the surviving inscription.",
    "The doorway is closed. The proposed reconstruction remains disputed.",
    "Antidisestablishmentarianism",
])
@pytest.mark.parametrize("quiet", [False, True])
def test_an_impossible_box_refuses_to_remove_checked_words(text, quiet):
    draw = ImageDraw.Draw(Image.new("RGB", (800, 300)))
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        typography.fit_text(draw, text, "Montserrat", 40, 100, 1, 40, quiet=quiet)


def test_fitting_keeps_all_sentences_and_qualifiers():
    draw = ImageDraw.Draw(Image.new("RGB", (1600, 600)))
    text = "About two thousand years old. Its exact date remains disputed."
    lines, font, shortened = typography.fit_text(draw, text, "Montserrat", 40, 900, 3, 34)
    assert " ".join(lines).replace("\u00a0", " ") == text
    assert not shortened
    assert all(typography.line_width(draw, line, font) <= 900 for line in lines)


def test_a_trial_reports_overflow_without_returning_a_truncated_candidate():
    draw = ImageDraw.Draw(Image.new("RGB", (800, 300)))
    text = "The proposed reconstruction remains disputed."
    lines, _font, overflow = typography.probe_text(draw, text, "Montserrat", 40, 100, 1, 40)
    assert overflow
    assert " ".join(lines).replace("\u00a0", " ") == text
    assert not any("…" in line for line in lines)


def test_tracked_checked_copy_has_the_same_refusal_policy():
    from src import graphics
    draw = ImageDraw.Draw(Image.new("RGB", (800, 300)))
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        graphics.fit_tracked(draw, "The proposed reconstruction remains disputed.", "Montserrat", 40,
                             100, 1, 40, 2, quiet=True)


def test_legacy_line_fitting_cannot_drop_a_qualifying_sentence_or_overflow():
    draw = ImageDraw.Draw(Image.new("RGB", (800, 300)))
    font = typography.get_font("Montserrat", 40)
    for text in ("The doorway is closed. The proposed reconstruction remains disputed.",
                 "Antidisestablishmentarianism"):
        with pytest.raises(ValueError, match="Checked copy does not fit"):
            typography.fit_lines(draw, text, font, 100, 1)


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_an_unfitting_marker_name_refuses_without_dropping_rows(size):
    from src import graphics
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        graphics.marker_label_lines("Unbreakable" * 100, *size)


def test_portrait_timeline_refuses_a_selected_truncated_layout(tmp_path, monkeypatch):
    from src import graphics
    monkeypatch.setattr(graphics, "_encode", lambda *_a, **_k: pytest.fail("Checked copy was truncated"))
    events = [{"year": 1800, "label": "The proposed reconstruction remains disputed. " * 15},
              {"year": 1900, "label": "Survey completed"}]
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        graphics.render_timeline(events, tmp_path / "timeline.mp4")
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------- one refusal type, held per surface, on the card

def test_the_refusal_is_one_type_every_hold_understands():
    from src.typography import LayoutError, TextFitError
    # TextFitError is the checked-copy case of the one LayoutError (the parked freeze work's BodyFitError and its
    # missing-credit refusal subclass or raise the same class), and stays a ValueError for the existing callers.
    assert issubclass(TextFitError, LayoutError) and issubclass(LayoutError, ValueError)
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    with pytest.raises(LayoutError) as got:
        typography.fit_text(draw, "The proposed reconstruction remains disputed.", "Montserrat", 40, 100, 1, 40)
    assert got.value.text == "The proposed reconstruction remains disputed."
    assert " ".join(got.value.lines) == got.value.text      # the whole copy, never a shortened candidate


def test_a_refused_deck_is_held_alone_and_its_saved_frames_leave_the_posting_glob(tmp_path):
    import concurrent.futures

    import main
    from src.typography import TextFitError
    (tmp_path / "stories").mkdir()
    (tmp_path / "stories" / "story_01_hook.jpg").write_bytes(b"frame")      # saved before the refusal
    bad = concurrent.futures.Future()
    bad.set_exception(TextFitError("The proposed reconstruction remains disputed.", where="the Story body"))
    good = concurrent.futures.Future()
    good.set_result([tmp_path / "carousel" / "carousel_slide_01.jpg"])
    payload = {}
    assert main.hold_layout_error(bad, tmp_path, "stories", payload) == []
    assert main.hold_layout_error(good, tmp_path, "carousel", payload) == [tmp_path / "carousel" / "carousel_slide_01.jpg"]
    assert not list((tmp_path / "stories").glob("story_*.jpg"))
    assert (tmp_path / "stories" / main.LAYOUT_HELD_DIR / "story_01_hook.jpg").is_file()
    (hold,) = payload["layout_holds"]
    assert hold["deck"] == "stories" and hold["moved"] == ["story_01_hook.jpg"]
    assert hold["error"].startswith("Checked copy does not fit the Story body")
    other = concurrent.futures.Future()
    other.set_exception(ValueError("not a layout refusal"))
    with pytest.raises(ValueError, match="not a layout refusal"):
        main.hold_layout_error(other, tmp_path, "carousel_fb", payload)


def test_a_refused_graphic_is_dropped_and_recorded_for_the_review_card(tmp_path, monkeypatch):
    from src import graphics
    from src.typography import TextFitError

    def refuse(*_a, **_k):
        raise TextFitError("Of volcanic stone removed by hand over many centuries", where="the number card's label")
    monkeypatch.setattr(graphics, "render_stat_cards", refuse)
    payload = {"stat_cards": [{"value": "1,200,000 TONS", "label": "Of volcanic stone removed by hand"}]}
    assert graphics.make_all(payload, {}, tmp_path) == []
    (hold,) = payload["layout_holds"]
    assert hold["deck"] == "graphics/numbers" and hold["error"].startswith("Checked copy does not fit")


def test_the_review_card_lists_each_held_surface_with_its_reason():
    from src.telegram_bot import TelegramApprovalBot
    payload = {"title": "The Richat Structure", "narration_script": "Sediment.", "first_comment": "Why?",
               "layout_holds": [
                   {"deck": "stories", "error": 'Checked copy does not fit 5 line(s) of 860 px at 46 px: "A & B"',
                    "moved": ["story_01_hook.jpg"]},
                   {"deck": "graphics/timeline", "error": "Checked copy does not fit the portrait timeline", "moved": []}]}
    text = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "Held, its text would not fit whole: 2" in text
    assert "Story frames: Checked copy does not fit 5 line(s) of 860 px at 46 px: &quot;A &amp; B&quot;" in text
    assert "graphic: timeline: Checked copy does not fit the portrait timeline" in text
    assert "Held" not in TelegramApprovalBot.format_card_text({**payload, "layout_holds": []}, "run_x")


def test_a_still_whose_copy_fits_only_cut_gets_the_tighter_fit_before_it_is_refused():
    from src import still_layout
    from src.typography import TextFitError
    calls = []

    def render(tight):
        calls.append(tight)
        if not tight:
            raise TextFitError("The proposed reconstruction remains disputed.", where="the design size")
        lay = still_layout.Layout("story", (1080, 1920), (0, 0, 1080, 1920), tight=True)
        return "image", lay
    im, lay, attempts = still_layout.drawn(render)
    assert calls == [False, True] and im == "image" and attempts == 2 and lay.tight

    def never(tight):
        raise TextFitError("The proposed reconstruction remains disputed.", where="either fit")
    with pytest.raises(TextFitError):
        still_layout.drawn(never)


def test_a_kicker_is_refused_whole_rather_than_cut_with_an_ellipsis():
    from src import multi_format
    font = typography.get_font("Montserrat", 24, weight=800)
    assert multi_format._fit_kicker("RICHAT", font, 400) == ("RICHAT", False)
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        multi_format._fit_kicker("THE EYE OF THE SAHARA SEEN FROM ORBIT", font, 120)


def test_the_analytics_chart_falls_back_to_its_figures_when_a_name_would_be_cut(db, monkeypatch):
    from src import audience_chart
    from src.control import make_command_handler
    from src.typography import TextFitError

    def refuse(*_a, **_k):
        raise TextFitError("Unbreakable" * 40, where="the chart's title column")
    monkeypatch.setattr(audience_chart, "chart_for", refuse)
    sent = []
    h = make_command_handler(db, lambda: None, lambda: "", send_photo=lambda *a, **k: sent.append(a) or True)
    text, _rows = h("cachart", "")
    assert not sent and "does not fit the chart whole" in text


def test_a_cover_style_that_cannot_hold_the_copy_whole_is_not_drawn_cut(tmp_path, monkeypatch):
    from src.typography import TextFitError
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1600, 2400), (90, 80, 70)).save(hero)
    copy = {"thumbnail": {"eyebrow": "CAPPADOCIA, TURKEY", "line1": "280 FEET DOWN", "line2": "FOUND BEHIND A WALL",
                          "tagline": "Room for up to 20,000 people"}}
    real = VisualEngine._render_checked

    def refuse(refused):
        def checked(self, im, copy_, style):
            if style in refused:
                raise TextFitError("FOUND BEHIND A WALL", where=f"the {style} cover")
            return real(self, im, copy_, style)
        return checked
    # the chosen style refused: the next style that holds the copy whole is the cover; none: the cover is refused
    monkeypatch.setattr(VisualEngine, "_render_checked", refuse({"badge"}))
    cover, _ = VisualEngine().generate_thumbnails(hero, copy, tmp_path / "b", style="badge", variants=False)
    from src import still_layout
    assert cover.is_file() and still_layout.load(cover)["recipe"]["style"] != "badge"
    monkeypatch.setattr(VisualEngine, "_render_checked", refuse({"badge", "minimal", "lower_third", "depth_sandwich",
                                                                 "monument"}))
    with pytest.raises(TextFitError):
        VisualEngine().generate_thumbnails(hero, copy, tmp_path / "c", style="badge", variants=False)


# ---------------------------------------------------------------- review 1

LONG_DOC_TITLE = "The Antikythera Mechanism and the Lost Science of Ancient Greek Astronomy"


def test_the_documentary_cover_copy_is_split_on_whole_words_never_cut():
    from main import documentary_cover
    from src.visual_engine import documentary_cover_copy, extract_curiosity_badge
    for title in (LONG_DOC_TITLE, "Egypt's Largest Obelisk Trapped in Bedrock", "Supercalifragilisticexpialidocious Day"):
        cover = documentary_cover(title)
        assert "…" not in cover["line1"] + cover["line2"]
        assert f"{cover['line1']} {cover['line2']}".split() == title.upper().split()       # every word, in order
        copy = documentary_cover_copy({"title": title})
        assert "…" not in copy["line1"] and copy["line1"].split() == title.upper().split()[:len(copy["line1"].split())]
    # the curiosity badge: whole copy or the next source, never cut by characters
    assert extract_curiosity_badge({"thumbnail": {"eyebrow": "ANCIENT ENGINEERING MYSTERIES"}}) == "ARCHAEOLOGICAL RECORD"
    assert extract_curiosity_badge({"stat_cards": [{"value": "1,200,000 TONS", "label": "of volcanic stone removed"}]}) \
        == "1,200,000 TONS"


def test_a_long_documentary_title_is_drawn_whole_or_the_cover_refuses(tmp_path):
    from main import documentary_cover
    from src.typography import TextFitError
    from src.visual_engine import VisualEngine
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1600, 2400), (90, 80, 70)).save(hero)
    drawn = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        drawn.append(str(text))
        return real(self, xy, text, *a, **k)
    ImageDraw.ImageDraw.text = spy
    try:
        try:
            VisualEngine().generate_thumbnails(hero, {"title": LONG_DOC_TITLE,
                                                      "thumbnail": documentary_cover(LONG_DOC_TITLE)},
                                               tmp_path / "doc", variants=False)
        except TextFitError as e:
            assert str(e).startswith("Checked copy does not fit")
            return
    finally:
        ImageDraw.ImageDraw.text = real
    assert not any("…" in t for t in drawn)
    words = set(" ".join(drawn).upper().split())
    assert set(LONG_DOC_TITLE.upper().split()) <= words


def _closing_slide(tmp_path, subject):
    from src import multi_format as mf
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (2000, 2500), (90, 80, 70)).save(photo)
    kicks = []
    real = mf.draw_kicker

    def spy(im, x, b, text, font, *a, **k):
        kicks.append((text, font.size))
        return real(im, x, b, text, font, *a, **k)
    card = {"heading": "The enduring debate",
            "body": "Does this giant show human ambition pushed too far, or genius cut short by nature?"}
    mf.draw_kicker = spy
    try:
        _, lay = mf.MultiFormatGenerator()._render_carousel_slide("debate", photo, photo, card, 6, 7, {},
                                                                 "Send this to a friend.", False, subject=subject)
    finally:
        mf.draw_kicker = real
    return kicks, {**lay.to_dict(), "problems": lay.problems()}


def test_the_closing_kicker_is_whole_one_scale_step_down_where_the_full_scale_cut_it(tmp_path):
    # the real-data case (6 dev and 11 production-sample carousels): "THE UNFINISHED OBELISK · YOUR…" at scale 1.0
    from src import multi_format as mf
    from src.typography import TextFitError
    with pytest.raises(TextFitError):   # at the slide's full scale the kicker does not fit whole
        mf._fit_kicker("THE UNFINISHED OBELISK · YOUR TAKE", typography.get_font("Montserrat", 45, weight=800), 1216)
    kicks, rec = _closing_slide(tmp_path, "The Unfinished Obelisk")
    assert [k for k, _ in kicks] == ["THE UNFINISHED OBELISK · YOUR TAKE"]
    assert kicks[0][1] < 45 and not rec["shortened"] and not rec["problems"]


def test_a_kicker_no_scale_holds_names_its_subject_alone_and_says_so(tmp_path):
    kicks, rec = _closing_slide(tmp_path, "The Great Unfinished Obelisk of Aswan")
    assert [k for k, _ in kicks] == ["THE GREAT UNFINISHED OBELISK OF ASWAN"]   # the subject whole, the house label gone
    assert rec["shortened"] == ["kicker_category"] and not rec["problems"]


def test_a_cross_section_label_two_lines_would_cut_is_set_whole_on_three(tmp_path, monkeypatch):
    from src import graphics
    got = []
    monkeypatch.setattr(graphics, "VIDEO_FPS", 1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "record_contrast", lambda out, frame, plates: got.extend(
        (n, p.text) for n, p in plates if p is not None and not p.empty) or {})
    section = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
               "levels": [{"depth_ft": 15.0, "label": "Stables, wine presses and storage chambers"},
                          {"depth_ft": 140.0, "label": "Church"}, {"depth_ft": 280.0, "label": "Wells"}]}
    assert graphics.render_cross_section(section, tmp_path / "s.mp4") is not None
    texts = [t for _n, t in got]
    i = texts.index("Stables, wine")
    assert texts[i:i + 3] == ["Stables, wine", "presses and", "storage chambers"]
    assert not any("…" in t for t in texts)


# ---------------------------------------------------------------- operator decision: thumbnail headline (option 1)

def _thumbnail(copy, **kw):
    from src import youtube_thumbnail
    meta = {}
    youtube_thumbnail.render(Image.new("RGB", (1920, 1080), (90, 80, 70)), copy, meta=meta, **kw)
    titles = [p.text for n, p in meta["_plates"] if n.startswith("title") or n == "kicker"]
    return meta, titles


def test_the_thumbnail_headline_is_a_whole_verified_line_or_none_never_a_cut_one():
    from src import youtube_thumbnail
    from src.telegram_bot import TelegramApprovalBot
    whole = {"line1": "THE LOST SCIENCE OF ANCIENT", "line2": "GREEK ASTRONOMY", "eyebrow": "DOCUMENTARY INVESTIGATION",
             "tagline": "WHAT THE EVIDENCE SHOWS"}
    # line 1 is longer than the 3-word budget: it is left out whole, never drawn as its first three words
    meta, titles = _thumbnail(whole)
    assert "GREEK ASTRONOMY" in titles and not any("LOST" in t for t in titles)
    assert meta["headline_lines_dropped"] == ["line1"]
    # neither title line fits: another verified line that does (the eyebrow here)
    meta, titles = _thumbnail({**whole, "line2": "AND THE MECHANISM THAT KEPT IT", "eyebrow": "DOCUMENTARY"})
    assert meta["headline_from"] == "eyebrow" and titles == ["DOCUMENTARY"]
    # no verified line fits: no headline, recorded for the review card
    meta, titles = _thumbnail({**whole, "line2": "AND THE MECHANISM THAT KEPT IT",
                               "eyebrow": "A DOCUMENTARY INVESTIGATION SERIES", "tagline": "WHAT THE EVIDENCE SHOWS US"})
    assert titles == [] and "no verified line fits" in meta["headline_dropped"]
    note = youtube_thumbnail.headline_note(meta)
    text = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": "x.", "first_comment": "?",
                                                 "thumbnail_note": note}, "run_x")
    assert "drawn without its headline" in text
    # a headline that fits is drawn as it was
    meta, titles = _thumbnail({**whole, "line1": "THE LOST SCIENCE"})
    assert titles == ["THE LOST SCIENCE", "GREEK ASTRONOMY"] and not youtube_thumbnail.headline_note(meta)
