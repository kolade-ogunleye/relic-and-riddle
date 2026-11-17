"""QF-21: the type engine measures and draws one shaped OpenType run (fontTools + uharfbuzz).

Acceptance (STATUS.md): kerning-pair, per-font feature and caption width parity tests. Every reference below is
independent of src.font_engine: Pillow's own raqm layout, fontTools tables, or the pixels libass draws.
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, features

from src import typography as type_

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = [("Anton", None), ("Montserrat", 700), ("Cinzel", 700)]


@pytest.fixture(autouse=True)
def _raqm():
    assert features.check("raqm"), "Pillow raqm layout is unavailable (FriBiDi missing)"


def _prefix_layout(text, font, xy, tracking, feats, size=(1600, 260)):
    """The reference: each letter where Pillow's raqm layout puts it in the whole kerned run (the prefix's
    advance less the letter's own), plus `tracking` per letter before it."""
    im = Image.new("L", size)
    d = ImageDraw.Draw(im)
    for index, char in enumerate(text):
        at = font.getlength(text[:index + 1], features=list(feats)) - font.getlength(char, features=list(feats))
        d.text((xy[0] + at + index * tracking, xy[1]), char, font=font, fill=255, anchor="ls", features=list(feats))
    return im


# ---------------------------------------------------------------- kerning pairs

@pytest.mark.parametrize("family,weight", FAMILIES)
@pytest.mark.parametrize("text", ["AVATAR", "WAVY VAULT", "TO AVOW"])
def test_tracked_text_keeps_the_kerning_pairs(family, weight, text):
    """Tracked text was drawn a letter at a time with each letter's own advance, so AV, WA, TO lost their kerning
    (review section 3). It now draws the letters where the kerned run puts them, plus the tracking."""
    font = type_.get_font(family, 100, weight=weight)
    tracking = 5
    actual = Image.new("L", (1600, 260))
    type_._draw_tracked(ImageDraw.Draw(actual), (30, 180), text, font, 255, "ls", tracking)
    expected = _prefix_layout(text, font, (30, 180), tracking, ("kern", "case"))
    assert actual.getbbox() == expected.getbbox()
    assert np.array_equal(np.asarray(actual), np.asarray(expected))
    assert type_.tracked_width(text, font, tracking) == pytest.approx(
        font.getlength(text, features=["kern", "case"]) + tracking * (len(text) - 1), abs=0.02)


@pytest.mark.parametrize("family,weight,pair", [("Anton", None, "YJ"), ("Montserrat", 700, "LT"),
                                                ("Cinzel", 700, "AV")])
def test_the_fonts_kern_pairs_reach_the_tracked_measure(family, weight, pair):
    """Each display face's strongest capital kern pair (read by Pillow's raqm layout) is in the tracked width
    every layout fits with, and in the callout, end-card and design-card copies of that measure (one run)."""
    from src import callouts, design_cards, end_card
    font = type_.get_font(family, 200, weight=weight)
    kerned, single = font.getlength(pair), font.getlength(pair[0]) + font.getlength(pair[1])
    assert kerned < single - 5, "the pair is kerned in this face"
    want = kerned + 6
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    assert type_.tracked_width(pair, font, 6) == pytest.approx(want, abs=0.02)
    assert callouts._tracked(probe, pair, font, 6) == pytest.approx(want, abs=0.02)
    assert end_card._tracked_width(font, pair, 6) == pytest.approx(want, abs=0.02)
    assert design_cards._tracked_width(pair, font, 6) == pytest.approx(want, abs=0.02)


def test_a_stroked_tracked_run_strokes_under_every_letter():
    """A tracked run is drawn as Pillow draws a stroked run: every stroke, then every fill, so a kerned
    neighbour's stroke never covers a letter (at no tracking the two paths draw the same pixels)."""
    font = type_.get_font("Montserrat", 90, weight=700)
    images = []
    for tracking in (0, 1e-9):
        im = Image.new("RGB", (900, 200))
        type_._draw_tracked(ImageDraw.Draw(im), (40, 140), "WAVY AV", font, (240, 230, 210), "ls", tracking,
                            stroke=6, stroke_fill=(20, 20, 20))
        images.append(np.asarray(im).astype(int))
    assert np.abs(images[0] - images[1]).max() <= 1


def test_tracking_keeps_combining_marks_on_their_letter():
    font = type_.get_font("Montserrat", 80, weight=700)
    composed, decomposed = "Königsberg", "Königsberg"
    assert type_.tracked_width(composed, font, 3) == pytest.approx(type_.tracked_width(decomposed, font, 3))
    masks = []
    for text in (composed, decomposed):
        image = Image.new("L", (1000, 200))
        type_._draw_tracked(ImageDraw.Draw(image), (20, 150), text, font, 255, "ls", 3)
        masks.append(np.asarray(image))
    assert np.array_equal(*masks)


def test_empty_run_has_no_advance():
    from src.font_engine import shape
    assert shape("", type_.get_font("Anton", 80)).advance == 0
    assert shape("", type_.get_font("Anton", 80)).glyphs == ()


# ---------------------------------------------------------------- per-font features

def test_each_fonts_features_are_read_from_its_tables():
    """The feature inventory, coverage, axes and the libass metadata come from the font's own tables (fontTools),
    as the review's coordinator verified them: Anton smcp/c2sc/case/cpsp/kern; Montserrat smcp/tnum/pnum/onum/
    case; Cinzel has neither case nor smcp; IBM Plex Mono no kerning."""
    from fontTools.ttLib import TTFont

    from src.font_engine import inspect_font
    from src.video_composer import _font_vmetrics, _libass_face
    expected = {"Anton-Regular.ttf": ({"smcp", "c2sc", "case", "cpsp", "kern"}, set()),
                "Montserrat-Variable.ttf": ({"smcp", "c2sc", "tnum", "pnum", "onum", "lnum", "case", "kern"}, set()),
                "Cinzel-Variable.ttf": ({"kern"}, {"case", "smcp", "tnum"}),
                "IBMPlexMono-SemiBold.ttf": (set(), {"kern", "case", "smcp"})}
    for filename, (present, absent) in expected.items():
        path = ROOT / "fonts" / filename
        info = inspect_font(str(path))
        assert present <= info.features and not absent & info.features, filename
        with TTFont(str(path)) as ttf:
            tags = {r.FeatureTag for t in ("GSUB", "GPOS") if t in ttf for r in ttf[t].table.FeatureList.FeatureRecord}
            assert info.features == tags
            assert info.metrics == (ttf["head"].unitsPerEm, ttf["OS/2"].usWinAscent, ttf["OS/2"].usWinDescent)
        assert ord("ö") in info.coverage and 0x10FFFD not in info.coverage
    for family in ("Anton", "Montserrat"):
        font = type_.get_font(family, 80)
        info = inspect_font(str(font.path))
        assert _font_vmetrics(family) == info.metrics
        assert _libass_face(font.path) == (info.names, info.weight)


@pytest.mark.parametrize("family,weight", [("Anton", None), ("Montserrat", 700)])
def test_capitals_take_the_case_forms_and_mixed_case_keeps_its_own(family, weight):
    """Default features: a run without lower case is set with `case` (the hyphen, brackets and middle dot raised to
    the capitals), on every path a renderer takes (ImageDraw.text, a measure, a tracked plate); a mixed-case run
    keeps the default forms."""
    font = type_.get_font(family, 120, weight=weight)

    def pillow(text, feats):
        im = Image.new("L", (1400, 240))
        ImageDraw.Draw(im).text((20, 180), text, font=font, fill=255, anchor="ls", features=feats)
        return np.asarray(im)

    def plain(text):
        im = Image.new("L", (1400, 240))
        ImageDraw.Draw(im).text((20, 180), text, font=font, fill=255, anchor="ls")
        return np.asarray(im)

    caps = "SAINT-MALO (AD 700) · X"
    assert not np.array_equal(pillow(caps, ["kern", "case"]), pillow(caps, ["kern"])), "case changes these glyphs"
    assert np.array_equal(plain(caps), pillow(caps, ["kern", "case"]))
    assert font.getlength(caps) == font.getlength(caps, features=["kern", "case"])
    plate = type_.plan_text(Image.new("RGB", (1400, 240)), (20, 180), caps, font, (255, 255, 255), "ls", 3,
                            halo_px=0)
    assert plate.features == ("kern", "case")
    mixed = "Saint-Malo (AD 700) · x"
    assert np.array_equal(plain(mixed), pillow(mixed, ["kern"]))
    assert type_.plan_text(Image.new("RGB", (1400, 240)), (20, 180), mixed, font, (255, 255, 255), "ls", 3,
                           halo_px=0).features == ("kern",)


@pytest.mark.parametrize("family", ["Anton", "Montserrat"])
def test_true_small_caps_keep_their_accents_and_match_pillow(family):
    """`smcp` draws the face's own small-capital glyphs (no scaled capitals): different bounds from the lower
    case, the accent kept, the same pixels as Pillow's raqm with the feature, tracked or not."""
    font = type_.get_font(family, 100)
    text, feats = "Königsberg", ("kern", "smcp")
    plain, actual, tracked, reference, unaccented = (Image.new("L", (1300, 260)) for _ in range(5))
    type_._draw_tracked(ImageDraw.Draw(plain), (20, 180), text, font, 255, "ls")
    type_._draw_tracked(ImageDraw.Draw(actual), (20, 180), text, font, 255, "ls", features=feats)
    ImageDraw.Draw(reference).text((20, 180), text, font=font, fill=255, anchor="ls", features=list(feats))
    assert actual.getbbox() != plain.getbbox()
    assert np.array_equal(np.asarray(actual), np.asarray(reference))
    assert type_.tracked_width(text, font, features=feats) == pytest.approx(
        font.getlength(text, features=list(feats)), abs=0.02)
    type_._draw_tracked(ImageDraw.Draw(tracked), (20, 180), text, font, 255, "ls", 4, features=feats)
    assert np.array_equal(np.asarray(tracked), np.asarray(_prefix_layout(text, font, (20, 180), 4, feats, (1300, 260))))
    type_._draw_tracked(ImageDraw.Draw(unaccented), (20, 180), "Konigsberg", font, 255, "ls", features=feats)
    assert not np.array_equal(np.asarray(actual), np.asarray(unaccented))


def test_small_caps_are_a_no_op_where_the_face_has_none():
    """Cinzel carries no smcp: asking for it changes nothing (no faux small caps are made up)."""
    font = type_.get_font("Cinzel", 100, weight=700)
    assert type_.tracked_width("Relic", font, 2, features=("kern", "smcp")) == type_.tracked_width(
        "Relic", font, 2, features=("kern",))


def test_tabular_figures_use_the_fonts_real_feature():
    from src.font_engine import FIGURE_FEATURES
    font = type_.get_font("Montserrat", 100, weight=600)
    widths = [type_.tracked_width(digit * 4, font, features=FIGURE_FEATURES) for digit in "0123456789"]
    assert max(widths) - min(widths) < 0.01
    proportional = [type_.tracked_width(digit * 4, font) for digit in "0123456789"]
    assert max(proportional) - min(proportional) > 20


@pytest.mark.parametrize("family", ["Anton", "Montserrat"])
def test_a_featured_tracked_plate_uses_the_same_features_for_mask_and_pixels(family):
    font = type_.get_font(family, 100)
    text, tracking, feats, xy = "Königsberg", 4, ("kern", "smcp"), (35, 180)
    base = Image.new("RGB", (1300, 260))
    plate = type_.plan_text(base, xy, text, font, (255, 255, 255), "ls", tracking, halo_px=0, features=feats)
    actual = plate.draw(base)
    reference = _prefix_layout(text, font, xy, tracking, feats, base.size)
    assert actual.getbbox() == reference.getbbox() == plate.box
    assert np.array_equal(np.asarray(actual.convert("L")) > 0, np.asarray(reference) > 0)
    from src.graphics import _twin
    twin = _twin(base, plate, (220, 220, 220))
    assert twin.features == feats and twin.box == plate.box


def test_variable_weights_share_one_instance_between_pillow_and_the_shaper():
    for weight in (400, 700, 900):
        font = type_.get_font("Montserrat", 90, weight=weight)
        assert type_.tracked_width("AVATAR", font) == pytest.approx(font.getlength("AVATAR"), abs=0.05)


# ---------------------------------------------------------------- caption width parity

def test_caption_measure_shapes_the_run_as_libass_does():
    """The caption measure (unkerned, operator decision 2026-11-05) is one shaped run without kerning: equal to
    the single letters' sum for plain text, but a combining mark adds no width of its own (summing letters
    counted Montserrat's U+0308 as 0.84 em)."""
    from src import config
    from src import video_composer as vc
    wide = type_.get_font(config.CAPTION_WIDE_FONT, config.CAPTION_WIDE_SIZE, weight=700)
    anton = type_.get_font(vc.FONT_NAME, vc._LIBASS_REF_PX)
    for font in (wide, anton):
        assert vc.unkerned_length(font, "WAVY VAULT") == pytest.approx(
            sum(font.getlength(ch) for ch in "WAVY VAULT"), abs=0.05)
    assert vc.unkerned_length(anton, "WAVY") > anton.getlength("WAVY") + 10   # kerning stays out
    assert vc.unkerned_length(wide, "Königsberg") == pytest.approx(
        vc.unkerned_length(wide, "Königsberg"), abs=0.05)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_a_16x9_caption_plate_fits_the_ink_libass_draws_for_decomposed_accents(tmp_path):
    """Parity on libass's own pixels: a 16:9 burst whose accent arrives decomposed (NFD) gets the plate the
    composed spelling gets, as libass draws both the same width."""
    from src.video_composer import VideoComposer

    def gaps(text, name):
        words = [{"word": text, "start": 0.1, "dur": 0.4}]
        ass = VideoComposer().build_subtitles(words, tmp_path / f"{name}.ass", width=1920, height=1080)
        png = tmp_path / f"{name}.png"

        def esc(p):
            return str(p.resolve()).replace("\\", "/").replace(":", "\\:")
        vf = f"setpts=PTS+0.300/TB,subtitles='{esc(ass)}':fontsdir='{esc(ROOT / 'fonts')}'"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xF2EEE6:s=1920x1080:r=30",
                        "-vf", vf, "-frames:v", "1", str(png)], check=True, timeout=120)
        a = np.asarray(Image.open(png).convert("RGB")).astype(int)
        ys, _ = np.nonzero(a.sum(-1) < 200)
        band = a[ys.min() + 16:ys.max() - 15]
        plate = np.nonzero((band.sum(-1) < 200).any(0))[0]
        pl, pr = int(plate.min()), int(plate.max()) + 1
        ink = band.sum(-1) > 300
        ink[:, :pl + 2] = False
        ink[:, pr - 2:] = False
        cols = np.nonzero(ink.any(0))[0]
        return int(cols.min()) - pl, pr - int(cols.max()) - 1, int(cols.max()) - int(cols.min())

    composed, decomposed = gaps("Königsberg", "nfc"), gaps("Königsberg", "nfd")
    assert abs(composed[2] - decomposed[2]) <= 1, "libass draws both spellings the same width"
    assert abs(composed[0] - decomposed[0]) <= 1 and abs(composed[1] - decomposed[1]) <= 1, (composed, decomposed)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_libass_draws_the_16x9_captions_without_ligatures(tmp_path):
    """libass's own pixels for the 16:9 CapText style (Montserrat Bold, Spacing 0): an fi run and an fl run draw the
    same ink width, so libass forms neither ligature (Montserrat's fi ligature is 3/1000 em wider than f + i and its
    fl ligature 1/1000 em narrower: with them the runs would differ by about 5 px at 24 pairs). The caption measure
    therefore shapes without liga/clig at every Spacing (font_engine.LIBASS_FEATURES), and equals both."""
    from src import config
    from src import video_composer as vc
    from src.video_composer import VideoComposer
    ass = VideoComposer().build_subtitles([{"word": "Königsberg", "start": 0.1, "dur": 0.4}], tmp_path / "w.ass",
                                          width=1920, height=1080)
    lines = [ln for ln in ass.read_text("utf-8").splitlines() if ",CapBox," not in ln]

    def esc(p):
        return str(p.resolve()).replace("\\", "/").replace(":", "\\:")

    def ink(run, name):
        path = tmp_path / f"{name}.ass"
        path.write_text("\n".join(ln.replace("Königsberg", run) for ln in lines) + "\n", encoding="utf-8")
        png = tmp_path / f"{name}.png"
        vf = f"setpts=PTS+0.300/TB,subtitles='{esc(path)}':fontsdir='{esc(ROOT / 'fonts')}'"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30",
                        "-vf", vf, "-frames:v", "1", str(png)], check=True, timeout=120)
        cols = np.nonzero((np.asarray(Image.open(png).convert("L")) > 60).any(0))[0]
        return int(cols.max()) - int(cols.min()) + 1

    fi, fl = ink("fi" * 24, "fi"), ink("fl" * 24, "fl")
    font = type_.get_font(config.CAPTION_WIDE_FONT, config.CAPTION_WIDE_SIZE, weight=700)
    with_liga = font.getlength("fi" * 24, features=["-kern"]) - font.getlength("fl" * 24, features=["-kern"])
    assert with_liga > 3, "the face's ligatures would show at this size"
    assert abs(fi - fl) <= 1, (fi, fl)
    assert vc.unkerned_length(font, "fi" * 24) == pytest.approx(vc.unkerned_length(font, "fl" * 24), abs=0.05)
