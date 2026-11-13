"""QF-87: Bebas removed, one Montserrat source, true small capitals in the captions (a face derived from Anton's
own smcp glyphs, embedded in the ASS file), the profile-grid masthead at 9 px cap height or absent, accents kept."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import typography, video_composer
from src.config import FONTS_DIR


def test_font_registry_matches_exactly_the_shipped_sources():
    # FONT_FILES, its static weights and the QF-94 fallback faces: every bundled file has a registry entry
    shipped = {path.name for path in FONTS_DIR.glob("*.ttf")}
    assert shipped == set(typography.bundled_font_files())
    assert set(typography.FONT_FILES.values()) <= shipped
    assert "BebasNeue-Regular.ttf" not in shipped
    assert [name for name in shipped if name.startswith("Montserrat")] == ["Montserrat-Variable.ttf"]


def test_font_evidence_recorder_covers_only_current_shipped_sources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from scripts import record_font_evidence as recorder
    fonts = tmp_path / "fonts"
    shutil.copytree(FONTS_DIR, fonts)
    original = json.loads((fonts / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]
    requests = []
    notices = {"anton": "Anton-Regular.ttf", "montserrat": "Montserrat-Variable.ttf",
               "cinzel": "Cinzel-Variable.ttf"}

    def response(url, timeout):
        assert timeout == 30
        assert url.startswith("https://raw.githubusercontent.com/google/fonts/main/ofl/")
        directory = url.split("/")[-2]
        assert directory in notices, "Deleted or unknown font source was requested"
        requests.append(directory)
        notice = (FONTS_DIR / "licenses" / (notices[directory] + ".OFL.txt")).read_text(encoding="utf-8")
        return SimpleNamespace(text=notice, raise_for_status=lambda: None)

    monkeypatch.setattr(recorder, "FONTS_DIR", fonts)
    monkeypatch.setattr(recorder.requests, "get", response)
    recorder.main()
    records = json.loads((fonts / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]
    assert [row["file"] for row in records] == typography.bundled_font_files()
    assert len(records) == 7 and sorted(requests) == ["anton", "cinzel", "montserrat"]
    retained = [row for row in original if row["file"] not in recorder.GOOGLE_FONT_DIRECTORIES]
    assert {row["file"] for row in retained} == {"IBMPlexMono-Medium.ttf", "IBMPlexMono-SemiBold.ttf",
                                                 "NotoSans-LGC.ttf", "NotoSerif-LGC.ttf"}
    kept = sorted((row for row in records if row["file"] not in recorder.GOOGLE_FONT_DIRECTORIES),
                  key=lambda row: row["file"])
    assert kept == sorted(retained, key=lambda row: row["file"])      # unchanged, field for field
    assert b"\r" not in (fonts / "license_evidence.json").read_bytes()


def test_font_evidence_recorder_refuses_lost_archive_proof_before_requests(tmp_path, monkeypatch):
    from scripts import record_font_evidence as recorder
    fonts = tmp_path / "fonts"
    shutil.copytree(FONTS_DIR, fonts)
    original = (fonts / "license_evidence.json").read_bytes()
    archive_font = fonts / "IBMPlexMono-Medium.ttf"
    archive_font.write_bytes(archive_font.read_bytes() + b"tampered")
    monkeypatch.setattr(recorder, "FONTS_DIR", fonts)

    def forbidden_request(*args, **kwargs):
        pytest.fail("Archive proof must be checked before any retrieval")

    monkeypatch.setattr(recorder.requests, "get", forbidden_request)
    with pytest.raises(ValueError, match="intact retained source evidence"):
        recorder.main()
    assert (fonts / "license_evidence.json").read_bytes() == original


def test_caption_small_caps_never_scale_full_capital_outlines(tmp_path):
    words = [{"word": word, "start": i * .5, "dur": .4}
             for i, word in enumerate(["Königsberg", "keeps", "its", "letters."])]
    ass = video_composer.VideoComposer().build_subtitles(words, tmp_path / "caption.ass")
    text = ass.read_text(encoding="utf-8")
    assert not re.search(r"\\fsc[xy](?:[1-9]\d?|0)(?!\d)", text)
    assert "RR Caption" in text
    plain = re.sub(r"\{[^}]*\}", "", "\n".join(line for line in text.splitlines()
                                                  if line.startswith("Dialogue:")))
    assert "Königsberg" in plain and "letters" in plain


@pytest.mark.parametrize("style", ["minimal", "badge", "depth_sandwich"])
def test_native_cover_has_no_unreadable_grid_masthead(style, tmp_path):
    from test_r4_covers_2026_11_02 import DERINKUYU, _cover
    image, layout = _cover(DERINKUYU, style)
    image.save(tmp_path / (style + ".png"))
    masthead = layout.meta["grid_proof"]["elements"].get("brand")
    assert masthead is None or masthead["cap_124"] >= 9.0, masthead
    from src.cover_design import GRID_HOOK, MASTHEAD_GRID_CAP
    assert GRID_HOOK["brand"] == MASTHEAD_GRID_CAP == 9      # the grid proof holds a drawn masthead to 9 px
    # at BRAND_PX the masthead is about 5 px at 124 px wide, so a cover has none, and says why
    assert layout.meta["masthead"]["shown"] is False and layout.meta["masthead"]["cap_124"] < 9
    assert "brand" not in layout.meta.get("plates", {})


@pytest.mark.parametrize("text", ["Königsberg", "Ko\u0308nigsberg", "Évora São João Łódź"])
def test_derived_caption_uses_the_original_smallcap_glyphs_and_advances(text):
    from src.font_assets import CAPTION_FAMILY
    from src.font_engine import shape
    original = typography.get_font("Anton", 100)
    derived = typography.get_font(CAPTION_FAMILY, 100)
    expected = shape(text, original, features=("-kern", "smcp"))
    actual = shape(text, derived, features=("-kern",))
    assert actual == expected
    masks = []
    for font, features in [(original, ["-kern", "smcp"]), (derived, ["-kern"])]:
        image = Image.new("L", (1800, 250))
        ImageDraw.Draw(image).text((20, 180), text, font=font, fill=255, anchor="ls", features=features)
        masks.append(np.asarray(image))
    assert np.array_equal(*masks)
    assert shape(text, derived) != shape(text, original)


def test_font_defaults_are_shared_by_pillow_and_tracked_text():
    # the QF-21 defaults hold on the one Montserrat source: kern by default, tnum/lnum figures where asked
    from src.font_engine import FIGURE_FEATURES, default_features, shape
    font = typography.get_font("Montserrat", 100, weight=700)
    assert font.getlength("AVATAR") < font.getlength("AVATAR", features=["-kern"])
    assert typography.tracked_width("AVATAR", font) == pytest.approx(font.getlength("AVATAR"), abs=.05)
    figures = list(FIGURE_FEATURES)
    assert font.getlength("1111", features=figures) == font.getlength("8888", features=figures)
    assert font.getlength("1111", features=figures) != font.getlength("1111", features=["pnum"])
    data_font = typography.get_font("IBM Plex Mono", 100)
    plate = typography.plan_text(Image.new("RGB", (900, 250)), (30, 180), "1198", data_font,
                                  (255, 255, 255), "ls", halo_px=0)
    assert plate.features == default_features("1198")
    assert shape("1111", data_font).advance == shape("8888", data_font).advance


@pytest.mark.parametrize("family", ["Anton", "Montserrat"])
def test_konigsberg_smcp_has_other_bounds_and_keeps_its_accent(family):
    """The row's acceptance: "Königsberg" set with and without smcp draws different bounds, accent intact."""
    from src.font_engine import choose_face, missing_glyphs
    font = typography.get_font(family, 200)
    assert missing_glyphs("Königsberg", font) == "" and choose_face("Königsberg", font) is font

    def ink(text, features):
        image = Image.new("L", (2400, 400))
        ImageDraw.Draw(image).text((40, 300), text, font=font, fill=255, anchor="ls", features=features)
        return np.asarray(image)
    plain, small = ink("Königsberg", ["kern"]), ink("Königsberg", ["kern", "smcp"])
    box = lambda a: (np.nonzero(a.any(0))[0][[0, -1]].tolist(), np.nonzero(a.any(1))[0][[0, -1]].tolist())
    assert box(plain) != box(small)
    # the accent is drawn: the umlauted word has ink where the bare word has none, in both settings
    for features, drawn in ((["kern"], plain), (["kern", "smcp"], small)):
        bare = ink("Konigsberg", features)
        assert (drawn > 0).sum() > (bare > 0).sum()
        assert ((drawn > 0) & (bare == 0)).sum() > 100


@pytest.mark.parametrize("text", ["Königsberg", "Ko\u0308nigsberg", "COUNTERREVOLUTIONARIES",
                                  "the Hal Saflieni Hypogeum", "an estimated 720 tons"])
def test_cached_glyph_ink_matches_independent_whole_run_raster(text):
    from src.font_assets import CAPTION_FAMILY
    from src.font_engine import run_ink_bounds, shape
    font = typography.get_font(CAPTION_FAMILY, 1000)
    features = ("-kern", "-liga", "-clig", "-calt", "-case")
    bounds = run_ink_bounds(shape(text, font, features=features), font)
    mask, offset = font.getmask2(text, anchor="ls", features=list(features))
    box = mask.getbbox()
    expected = (offset[0]+box[0], offset[1]+box[1], offset[0]+box[2], offset[1]+box[3])
    # Pillow rounds glyph origins to pixels. At reference1000, one pixel is
    # less than0.09 delivery pixels; the native libass proof tests final ink.
    assert bounds == pytest.approx(expected, abs=1)


def test_derived_cache_is_reproducible_and_binds_the_source_and_licence(tmp_path):
    from src.font_assets import CAPTION_FAMILY, WIDE_FAMILY, derived_font
    from src.font_evidence import font_rights
    fonts = tmp_path / "fonts"
    shutil.copytree(FONTS_DIR, fonts)
    for family in (CAPTION_FAMILY, WIDE_FAMILY):
        path = derived_font(family, fonts)
        first = path.read_bytes()
        valid, record = font_rights(path)
        assert valid and record["source_sha256"] and record["builder_sha256"]
        assert Path(record["derived_from"]).parent == fonts.resolve()
        path.unlink()
        assert derived_font(family, fonts).read_bytes() == first
        path.write_bytes(first + b"tampered")
        assert not font_rights(path)[0]
        assert derived_font(family, fonts).read_bytes() == first
    source = fonts / "Anton-Regular.ttf"
    source.write_bytes(source.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="intact source rights"):
        derived_font(CAPTION_FAMILY, fonts)


@pytest.mark.parametrize("composed,decomposed", [("Évora", "E\u0301vora"), ("Königsberg", "Ko\u0308nigsberg")])
def test_native_caption_canonical_accents_are_identical(composed, decomposed, tmp_path):
    """Default small caps must not choose a small-cap mark above a full capital."""
    import unicodedata
    frames = []
    for index, word in enumerate((composed, decomposed)):
        words = [{"word": word, "start": .1, "dur": 1.2}]
        original = json.dumps(words, ensure_ascii=False).encode("utf-8")
        composer = video_composer.VideoComposer()
        before_srt = composer.export_subtitles_srt(words, tmp_path / f"before-{index}.srt").read_bytes()
        before_vtt = composer.export_subtitles_vtt(words, tmp_path / f"before-{index}.vtt").read_bytes()
        ass = composer.build_subtitles(words, tmp_path / f"accent-{index}.ass")
        assert unicodedata.normalize("NFC", word) in ass.read_text(encoding="utf-8")
        assert json.dumps(words, ensure_ascii=False).encode("utf-8") == original
        assert composer.export_subtitles_srt(words, tmp_path / f"after-{index}.srt").read_bytes() == before_srt
        assert composer.export_subtitles_vtt(words, tmp_path / f"after-{index}.vtt").read_bytes() == before_vtt
        assert word.encode("utf-8") in before_srt and word.encode("utf-8") in before_vtt
        escaped = str(ass.resolve()).replace("\\", "/").replace(":", "\\:")
        fonts = str(FONTS_DIR.resolve()).replace("\\", "/").replace(":", "\\:")
        png = tmp_path / f"accent-{index}.png"
        result = subprocess.run(["ffmpeg", "-v", "verbose", "-y", "-f", "lavfi", "-i",
                                 "color=c=0xF2EEE6:s=1080x1920:r=30", "-vf",
                                 f"setpts=PTS+0.6/TB,subtitles='{escaped}':fontsdir='{fonts}'",
                                 "-frames:v", "1", "-threads", "1", str(png)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=120, check=False)
        (tmp_path / f"accent-{index}.log").write_text(result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stderr[-2000:]
        selected = [line for line in result.stderr.splitlines() if "fontselect:" in line]
        assert selected and all("RRCaption-Regular" in line for line in selected), selected
        frames.append(np.asarray(Image.open(png).convert("RGB")))
    assert np.array_equal(*frames), "Canonically equivalent accents must have the same native ink"


@pytest.mark.parametrize("width,height,family,postscript", [
    (1080, 1920, "RR Caption", "RRCaption-Regular"),
    (1920, 1080, "RR Sans", "RRSans-Bold"),
])
def test_actual_libass_uses_embedded_face_and_retains_timing(width, height, family, postscript, tmp_path):
    from src.video_qa import caption_pills
    words = [{"word": "Königsberg", "start": .1, "dur": 1.2}]
    ass = video_composer.VideoComposer().build_subtitles(words, tmp_path / "caption.ass", width=width, height=height)
    text = ass.read_text(encoding="utf-8")
    dialogue = [line for line in text.splitlines() if line.startswith("Dialogue:") and ",CapText," in line]
    # The existing lead is two 30-fps frames: 0.10 - 2/30 -> ASS 0.03.
    assert dialogue and all("0:00:00.03,0:00:01.40" in line for line in dialogue)
    assert "Königsberg" in re.sub(r"\{[^}]*\}", "", "\n".join(dialogue))
    def esc(path):
        return str(path.resolve()).replace("\\", "/").replace(":", "\\:")
    png = tmp_path / "caption.png"
    result = subprocess.run(["ffmpeg", "-v", "verbose", "-y", "-f", "lavfi", "-i",
                             f"color=c=0xF2EEE6:s={width}x{height}:r=30", "-vf",
                             f"setpts=PTS+0.6/TB,subtitles='{esc(ass)}':fontsdir='{esc(FONTS_DIR)}'",
                             "-frames:v", "1", str(png)], capture_output=True, text=True, timeout=120, check=False)
    (tmp_path / "ffmpeg.log").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-2000:]
    selected = [line for line in result.stderr.splitlines() if "fontselect:" in line]
    assert selected and all(f"({family}," in line and postscript in line for line in selected), selected
    pixels = np.asarray(Image.open(png).convert("RGB")).astype(int)
    x0, y0, x1, y1 = caption_pills(ass)[0]
    ink = (pixels[y0+4:y1-4, x0+4:x1-4].sum(-1) > 300)
    ys, xs = np.nonzero(ink)
    assert len(xs) and min(xs) >= 20 and max(xs) < (x1-x0)-24
    record = {"fontselect": selected, "pill": [x0,y0,x1,y1],
              "ink": [int(xs.min()+x0+4), int(ys.min()+y0+4), int(xs.max()+x0+5), int(ys.max()+y0+5)]}
    (tmp_path / "measurements.json").write_text(json.dumps(record, indent=2), encoding="utf-8")


def test_derived_faces_built_at_once_by_many_workers_agree(tmp_path):
    """Test workers and render processes build a derived face at import: concurrent builds of the same face end with
    one intact file, never an error (Windows refuses a replace while another process holds the file open)."""
    from concurrent.futures import ThreadPoolExecutor

    from src.font_assets import CAPTION_FAMILY, WIDE_FAMILY, derived_font
    from src.font_evidence import font_rights
    fonts = tmp_path / "fonts"
    shutil.copytree(FONTS_DIR, fonts)
    for family in (CAPTION_FAMILY, WIDE_FAMILY):
        with ThreadPoolExecutor(8) as pool:
            paths = list(pool.map(lambda _, f=family: derived_font(f, fonts), range(16)))
        assert len(set(paths)) == 1 and font_rights(paths[0])[0]


def test_the_hook_frame_draws_no_masthead_under_nine_pixels(tmp_path, fact_record_key):
    """Lander ruling 2026-11-13: the row's rule reaches the Reel's first frame. At 5.2 px on the 124 px tile the hook
    card draws no masthead (no name, no diamond) and is never refused for it."""
    from test_design_cards_2026_11_03 import SHEET, hook

    from src import design_cards as dc
    photo = tmp_path / "ground.png"
    Image.new("RGB", (1600, 2400), (70, 60, 50)).save(photo)
    plan = dc.plan_hook_frame(hook(photo, eyebrow="The Richat ring"), SHEET)
    assert plan.meta["masthead"] == {"shown": False, "cap_124": 5.2,
                                     "reason": "under 9 px cap height at profile-grid size"}
    assert not {e.name for e in plan.elements} & {"brand", "brand_mark"}
    assert "brand" not in plan.meta["grid_proof"]["elements"] and plan.meta["grid_proof"]["hook_fails"] == []


@pytest.mark.parametrize("text,lines", [
    ("of Army Group North", (["of"], ["Army", "Group", "North"])),                       # amber: the name whole
    ("when Königsberg Castle", (["when"], ["Königsberg", "Castle"])),                    # amber: whole, at 80
    ("to Central Asian traditions", (["to", "Central", "Asian"], ["traditions"])),       # gol gumbaz
    ("near the Podkamennaya", (["near"], ["the", "Podkamennaya"])),                      # tunguska: no other break
])
def test_two_line_breaks_keep_names_and_phrases_whole(text, lines):
    """QF-87 review 1: a two-line break is ranked by phrase structure, then the larger rung, then width: never inside
    a name when a break outside it fits (OF ARMY | GROUP NORTH, WHEN KÖNIGSBERG | CASTLE at 74 were drawn), never a
    lone function word on line 1 when another break fits. The four real bursts draw at 80."""
    words = text.split()
    k, size = video_composer.reel_caption_lines(words)
    assert (words[:k], words[k:]) == lines and size == 80


@pytest.mark.parametrize("text,lines", [
    ("of Mohenjo Daro Citadel", (["of", "Mohenjo"], ["Daro", "Citadel"])),
    ("at Angkor Wat Temple", (["at", "Angkor"], ["Wat", "Temple"])),
])
def test_a_two_line_burst_never_shrinks_to_keep_a_name_whole(text, lines):
    """QF-87 round 3 (lander ruling 2026-11-13): size first. A "lone function word | long name" burst draws at the
    largest rung any break fits, splitting the name there, never a smaller size to keep it whole (ranked name-first,
    OF | MOHENJO DARO CITADEL drew at 66 and AT | ANGKOR WAT TEMPLE at 74 next to bursts at 80; the production
    sample had 8 such 66 px bursts, 2 at size-first). The review-1 bursts above keep their names at 80."""
    words = text.split()
    k, size = video_composer.reel_caption_lines(words)
    assert size == 80 and (words[:k], words[k:]) == lines
