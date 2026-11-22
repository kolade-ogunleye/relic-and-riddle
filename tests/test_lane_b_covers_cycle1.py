"""Freeze lane B, cycle 1: covers, grid tile and YouTube thumbnail rules (invariants, synthetic inputs).

- Operator ruling 7 (2026-11-16): a cover tagline emptied by a withheld figure falls back to another already-verified
  line of the run's copy (its carousel slide headings), never invented text; empty only if none fits.
- QF-143 review finding 3: the year that became the YouTube hero is never drawn again in the headline that falls back
  to the line it came from.
"""
from __future__ import annotations

import re

from PIL import Image

from src import cover_design
from src import youtube_thumbnail as yt

HEADINGS = [{"heading": "THE 327-METRE DESCENT"}, {"heading": "FOLLOW FOR MORE"}, {"heading": "SOLID SALT HALLS"},
            {"heading": "MASSIVE LABYRINTH"}, {"heading": "A"}, {"heading": "CARVED FROM"}]


def test_a_fallback_tagline_is_a_verified_run_line_that_stands_alone_and_adds_to_the_headline():
    copy = {"eyebrow": "SUBTERRANEAN MARVEL", "line1": "CARVED FROM", "line2": "SOLID SALT", "tagline": ""}
    alts = cover_design.tagline_alternates({"carousel_slides": HEADINGS})
    got = cover_design.fallback_tagline(copy, alts)
    assert got is not None
    source, text = got
    assert (source, text) in alts                      # a line of the run's copy, never invented
    assert not any(c.isdigit() for c in text)          # no figure (it would need its own check)
    assert not cover_design.withheld_fragment(text) and 2 <= len(text.split()) <= 4
    assert not cover_design.redundant_tagline(dict(copy, tagline=text))
    assert not re.search(r"\b(FOLLOW|SAVE|SHARE|YOUR)\b", text)
    # the fewest words shared with the headline: SOLID SALT HALLS repeats two, MASSIVE LABYRINTH none
    shown = {"CARVED", "FROM", "SOLID", "SALT", "SUBTERRANEAN", "MARVEL"}
    assert not set(text.split()) & shown


def test_no_fitting_line_leaves_the_tagline_empty():
    copy = {"eyebrow": "X", "line1": "CARVED FROM", "line2": "SOLID SALT", "tagline": ""}
    assert cover_design.fallback_tagline(copy, cover_design.tagline_alternates(
        {"carousel_slides": [{"heading": "A 300-FOOT DROP"}, {"heading": "SALT"}, {"heading": "SHARE THIS NOW"}]})) is None
    assert cover_design.fallback_tagline(copy, ()) is None


def test_the_cover_draws_the_fallback_only_where_the_withheld_figure_emptied_the_tagline():
    from src import still_layout
    im = Image.new("RGB", (1080, 1920), (70, 66, 60))
    copy = {"eyebrow": "SUBTERRANEAN MARVEL", "line1": "CARVED FROM", "line2": "SOLID SALT",
            "tagline": "327 METERS DOWN"}
    figure = {"number": "327", "unit": "M", "conflict": True, "reason": "a range"}
    alts = cover_design.tagline_alternates({"carousel_slides": HEADINGS})
    lay = still_layout.Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    cover_design.render_cover(im.copy(), copy, "badge", lay=lay, figure=figure, alternates=alts)
    fb = lay.meta.get("tagline_fallback")
    assert fb and fb["from"] and (fb["from"], fb["text"]) in alts
    assert any(e.get("text") == fb["text"] for e in lay.to_dict()["elements"] if e.get("name") == "tagline")
    # a tagline the figure did not empty is drawn as written, no fallback
    lay2 = still_layout.Layout("cover", (1080, 1920), (0, 0, 1080, 1920))
    cover_design.render_cover(im.copy(), dict(copy, tagline="NEVER FINISHED"), "badge", lay=lay2, figure=figure,
                              alternates=alts)
    assert "tagline_fallback" not in lay2.meta


def test_the_youtube_year_hero_is_not_drawn_again_in_its_lines_fallback():
    copy = {"eyebrow": "A VERY LONG EYEBROW LINE", "line1": "THE LOST UNDERGROUND CITY",
            "line2": "OF THE ANCIENTS LONG GONE", "tagline": "REDISCOVERED BY DIVERS 1963"}
    meta = {}
    _, plates = yt.render(Image.new("RGB", (1920, 1080), (96, 92, 88)), copy, meta=meta)
    texts = [p.text for n, p in plates if not p.empty and n != "brand"]
    assert sum(t.count("1963") for t in texts) == 1, texts


# ------------------------------------------------------------------ QF-142: a withheld figure in every form
def _no_figure_left(text, value):
    """No form of `value` is left in `text`: no digit run equal to it, no spelled-out number equal to it."""
    from src import figures
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if any(abs(n - value) < 1e-9 for n in nums):
        return False
    words = re.findall(r"[A-Z]+", text.upper())
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + 5) + 1):
            v = cover_design._spelled_value(words[i:j])
            if v is not None and abs(v - value) < 1e-9 and all(w.lower() in figures.NUMBER_WORDS or w in ("HUNDRED",)
                                                               for w in words[i:j]):
                return False
    return True


WITHHELD = [("144", "FT", ["THE MINE (144 FT, 9 LEVELS)", "THE MINE (9 LEVELS, 144 FT)", "130 TO 144 FT DEEP",
                           "144-150 FT DEEP SHAFT", "BETWEEN 130 AND 144 FT OF ROCK", "ONE HUNDRED FORTY-FOUR FEET DOWN",
                           "UP TO 144 FT TALL", "THE 144 FT (WOW) SHAFT"]),
            ("25", "MI", ["25 TO ALMOST 31 MI", "A RING 25 TO ALMOST 31 MI WIDE", "TWENTY-FIVE MILES ACROSS",
                          "THE RING SPANS TWENTY FIVE MILES", "ONE EYE, 25 MILES WIDE"])]


def test_a_withheld_figure_leaves_no_form_of_itself_in_any_field():
    for number, unit, lines in WITHHELD:
        fig = {"number": number, "unit": unit, "conflict": True}
        for line in lines:
            for key in ("eyebrow", "line1", "line2", "tagline", "badge"):
                out = cover_design.withhold_figure({key: line}, fig)[key]
                assert _no_figure_left(out, float(number)), (line, out)
                # what is left is tidy: no empty or opening-mark bracket, no stray separating mark, no dangling word
                assert not re.search(r"\(\s*[,;:]|[,;:]\s*\)|\(\s*\)", out), (line, out)
                assert not out or not cover_design.withheld_fragment(out), (line, out)


def test_other_numbers_and_number_words_stand():
    fig = {"number": "144", "unit": "FT", "conflict": True}
    for line in ("NO ONE KNOWS", "FIVE MILES DOWN", "THE MINE (9 LEVELS)", "THE 1,200-TON MONSTER"):
        assert cover_design.withhold_figure({"line1": line}, fig)["line1"] == line


def test_the_article_before_a_withheld_figure_stays_when_words_follow_it():
    fig = {"number": "144", "unit": "FT", "conflict": True}
    assert cover_design.withhold_figure({"line1": "THE 144 FT (WOW) SHAFT"}, fig)["line1"].startswith("THE ")
    assert cover_design.withhold_figure({"line1": "THE MINE (THE 144 FT)"}, fig)["line1"] == "THE MINE"


# ------------------------------------------------------------------ QF-144: photo-borne text bands on lane B surfaces
def _banded_hero(path):
    """A photograph (warm, never green) with a source caption bar burned in across its top (QF-144's class: the
    Sentinel credit band), saved to `path`."""
    import numpy as np
    from PIL import ImageDraw

    from src import brand
    from src.typography import get_font
    rng = np.random.default_rng(3)
    grey = rng.normal(150, 30, (64, 96, 1)).clip(40, 230)
    im = Image.fromarray((grey * np.array([1.0, 0.75, 0.6])).astype("uint8")).resize((2400, 1350), Image.BICUBIC)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 2400, 110), fill=(180, 196, 0))
    d.text((20, 20), "Richat Structure, Mauritania - 6 January 2023 - Enhanced natural colors - Contains",
           font=get_font(brand.TEXT_FONT, 60), fill=(0, 0, 0))
    im.save(path, quality=95)


def _band_pixels(im):
    import numpy as np
    a = np.asarray(im.convert("RGB"), np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return int(((g > r + 12) & (g > 3 * b + 10) & (g > 40)).sum())


def test_no_lane_b_surface_shows_a_burned_in_caption_band(tmp_path):
    """QF-144 (lane B part): the Reel cover, the profile grid tile and the YouTube thumbnail all crop their hero
    through the hero window (photo_hygiene.clean_hero: hero_window.clean, the round-8 photo_text detector among its
    detectors), so a source caption band never reaches a delivered frame (target 0 band pixels)."""
    from src.visual_engine import VisualEngine
    hero = tmp_path / "banded.jpg"
    _banded_hero(hero)
    payload = {"thumbnail": {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA EYE", "line2": "",
                             "tagline": "CARVED BY NATURE"}, "title": "The Sahara Eye", "narration_script": "x."}
    reel, grid = VisualEngine().generate_thumbnails(hero, payload, tmp_path / "c", variants=False, style="badge")
    yt_path = VisualEngine().generate_youtube_thumbnail(hero, payload, tmp_path / "yt.jpg")
    for p in (reel, grid, yt_path):
        with Image.open(p) as im:
            assert _band_pixels(im) < 50, (p.name, _band_pixels(im))
    assert _band_pixels(Image.open(hero)) > 10000   # the probe holds the band (else this test proves nothing)


def test_the_documentary_split_prefers_lines_within_the_youtube_line_budget(tmp_path):
    """QF-109 (split part): of the title's whole-word splits that set, one whose two lines each hold the YouTube
    headline's line budget (youtube_thumbnail.LINE_WORDS) is preferred: a longer line is left out of the thumbnail."""
    from main import documentary_cover
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (90, 80, 70)).save(hero)
    title = "The Ice Man Of The Alps"   # its 22-character split leaves a five-word line 1
    cover = documentary_cover(title, hero=hero)
    assert f"{cover['line1']} {cover['line2']}".split() == title.upper().split()
    assert all(len(cover[k].split()) <= yt.LINE_WORDS for k in ("line1", "line2")), cover


def test_the_reel_lockup_never_stacks_a_one_word_middle_row():
    """SYS-R8-2 open question 5 (the Reel's part of R2): a field set valid alone can leave a one-word middle row once
    stacked under the other field; the Reel lockup never draws that on the ladder (next tier, else the recorded last
    resort, which the grid proof refuses)."""
    from PIL import ImageDraw

    from src import heading
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for l1, l2 in (("THE 1,200-TON", "BEDROCK MONSTER"), ("ROMAN CONCRETE", "HEALS ITSELF"),
                   ("CARVED FROM", "SOLID SALT"), ("THE 6-TON ROOM", "STOLEN IN 36 HOURS")):
        for width in (420, 560, 700, 912):
            out, rec = cover_design._lockup(m, {"line1": l1, "line2": l2}, width, 108)
            if rec.get("tier") is None:
                continue   # the last resort: off the ladder, refused by the grid proof
            rows = [" ".join(t for t, _ in lr) for k in ("line1", "line2") if k in out for lr in out[k][1]]
            assert all(len(r.split()) >= 2 for r in rows[1:-1]), rows
            assert all(heading.end_ok(r.split()[-1]) for r in rows[:-1]), rows


def test_the_youtube_headline_never_repeats_the_video_title_where_the_copy_has_another_line():
    """Lane findings 2026-11-16: the no-figure headline was not checked against the video title (THE RICHAT STRUCTURE
    over a video titled the same). The figure path's rule now holds on the headline path: the line that repeats it
    gives way to the pair's other line where it stands alone, else it stays and is recorded."""
    flat = Image.new("RGB", (1920, 1080), (96, 92, 88))
    title = "The Richat Structure Explained"
    copy = {"eyebrow": "DESERT ANOMALY", "line1": "THE RICHAT STRUCTURE", "line2": "SAHARA EYE", "tagline": "WHY"}
    meta = {}
    _, plates = yt.render(flat, copy, meta=meta, youtube_title=title)
    drawn = " ".join(p.text for n, p in plates if n.startswith("title"))
    assert len(yt._content(drawn) & yt._content(title)) < 2, drawn
    assert meta.get("tension_from") == "line2"
    copy = {"eyebrow": "RICHAT STRUCTURE MAURITANIA", "line1": "THE RICHAT STRUCTURE", "line2": "", "tagline": ""}
    meta = {}
    yt.render(flat, copy, meta=meta, youtube_title=title)
    assert meta.get("title_overlap") is True   # nothing else stands: drawn as written, recorded
