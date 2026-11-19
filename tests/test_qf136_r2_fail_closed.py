"""QF-136 round 2: the visual critic fails closed. Each case is a probe from the round-1 adversary (Codex) or the
round-1 review that the gate passed; each must now fail, and its control (the same frame drawn right) must pass.

Pictures are drawn here with the repo's bundled faces over a committed fixture photograph; captions and masters with
libass (ffmpeg). Nothing is pinned to a measured value: each test states an invariant of the gate.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STILL = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "stills" / \
    "richat_structure_sahara_eye_01_commons_960.jpg"
ANTON = ROOT / "fonts" / "Anton-Regular.ttf"
W, H = 1080, 1920
needs_libass = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg with libass is not installed here")


def critic():
    spec = importlib.util.find_spec("src.visual_critic")
    assert spec is not None, "QF-136: src/visual_critic.py (the visual critic gate) is missing"
    return importlib.import_module("src.visual_critic")


def ground() -> Image.Image:
    with Image.open(STILL) as im:
        photo = im.convert("RGB")
    s = H / photo.height
    photo = photo.resize((round(photo.width * s), H), Image.LANCZOS)
    x = (photo.width - W) // 2
    return photo.crop((x, 0, x + W, H))


def card(vc, tmp_path: Path, truth: str, drawn: str, extra: str | None = None, erase: int = 0, px: int = 100,
         zone_at_ink: bool = False):
    """`drawn` in Anton `px` (100) px on a dark band over the photograph; the record (box, face) is right, the truth is
    `truth`. `extra`: a second line under it. `erase`: that many columns of the last glyph's ink removed."""
    im = ground()
    d = ImageDraw.Draw(im)
    ft = ImageFont.truetype(str(ANTON), px)
    xy = (150, 700)
    box = d.textbbox(xy, drawn, font=ft, anchor="ls")
    d.rectangle((90, 540, 960, 1000), fill=(18, 16, 14))
    d.text(xy, drawn, font=ft, fill=(240, 236, 226), anchor="ls")
    if extra:
        d.text((150, 880), extra, font=ft, fill=(240, 236, 226), anchor="ls")
    zone = None
    if erase or zone_at_ink:                    # the last columns of ink erased, the zone ending where the ink now ends (Codex)
        mk = Image.new("L", im.size, 0)
        ImageDraw.Draw(mk).text(xy, drawn, font=ft, fill=255, anchor="ls")
        xr = mk.getbbox()[2] - 1
        d.rectangle((xr - erase + 1, box[1] - 5, xr + 6, box[3] + 5), fill=(18, 16, 14))
        zone = (113, 300, xr - erase + 1, 1000)
    p = tmp_path / "frame.png"
    im.save(p)
    f = vc.Frame(name="frame.png", kind="card", size=(W, H), deliverable="reel", image=p,
                 expect=[{"field": "title", "text": truth, "truth": "signed field"}],
                 elements=[{"name": "title", "box": list(box), "text": truth, "font": "Anton-Regular.ttf", "px": px}],
                 zone=zone, overlap_rule=True)
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    return entry


def text_findings(entry) -> list[dict]:
    return [d for d in entry["findings"] if d["class"] == "text_identity"]


@pytest.mark.parametrize("truth,drawn", [
    ("THE DOME", "TNE DOME"),                          # a short word with a wrong glyph (Codex 1, 2)
    ("THE DOME", "TBE DOME"),
    ("327 METERS DOWN", "321 METERS DOWN"),            # a wrong digit (review B2)
    ("DAY 7 OF THE SIEGE", "DAY OF THE SIEGE"),        # a single digit dropped
    ("BUILT IN STONE", "BUILT STONE"),                 # a two-letter word dropped
    ("A DOME", "DOME"),                                # a one-letter word dropped
    ("ROUGHLY 137–138 FT", "ROUGHLY 137138 FT"),  # the en dash dropped (the GLYPH-DROP mechanism)
    ("KRAKÓW SALT MINE", "KRAKOW SALT MINE"),     # the accent dropped
    ("ROME’S LOST SECRET", "ROMES LOST SECRET"),  # the apostrophe dropped
    ("WHISPERING GALLERY", "WHISPREING GALLERY"),      # two letters transposed
    ("GALLERY", "ALLERY"),                             # the first letter dropped
    ("GALLERY", "GALLER"),                             # the last letter dropped
    ("SOLID SALT", "SALT SOLID"),                      # words out of order (Codex 4)
    ("SOLID SALT", "SOLID SALT LIES"),                 # a word added
])
def test_a_changed_character_word_or_order_is_a_text_identity_failure(tmp_path, truth, drawn):
    vc = critic()
    entry = card(vc, tmp_path, truth, drawn)
    assert not entry["pass"] and text_findings(entry), entry["findings"]


@pytest.mark.parametrize("text", ["ACOUSTIC DOME", "A DOME", "327 METERS DOWN", "ROUGHLY 137–138 FT",
                                  "KRAKÓW SALT MINE", "ROME’S LOST SECRET", "DAY 7 OF THE SIEGE"])
def test_the_same_frame_drawn_right_passes(tmp_path, text):
    vc = critic()
    entry = card(vc, tmp_path, text, text, px=80)        # 80 px: the longest line stays inside the Reel box
    assert entry["pass"], entry["findings"]


def test_a_repeated_line_is_not_the_truth(tmp_path):
    vc = critic()
    entry = card(vc, tmp_path, "ACOUSTIC DOME", "ACOUSTIC DOME", extra="ACOUSTIC DOME")
    assert text_findings(entry), entry["findings"]


@pytest.mark.parametrize("erase", [1, 2, 3])
def test_columns_of_ink_cut_at_the_zone_edge_are_found(tmp_path, erase):
    vc = critic()
    entry = card(vc, tmp_path, "ACOUSTIC DOME", "ACOUSTIC DOME", erase=erase, px=80)
    assert not entry["pass"], entry["fields"]


def test_the_same_word_ending_at_the_zone_edge_uncut_passes(tmp_path):
    vc = critic()
    im_entry = card(vc, tmp_path, "ACOUSTIC DOME", "ACOUSTIC DOME", px=80, zone_at_ink=True)
    assert im_entry["pass"], im_entry["findings"]


def test_a_word_too_faint_to_match_well_still_has_its_contrast_measured(tmp_path):
    # Codex contrast/erode_5: DOME in grey 114 on black, its strokes thinned: 4.37:1 and must fail contrast.
    import cv2
    vc = critic()
    im = Image.new("RGB", (W, H), "black")
    d = ImageDraw.Draw(im)
    ft = ImageFont.truetype(str(ANTON), 100)
    a = d.textbbox((150, 700), "ACOUSTIC", font=ft, anchor="ls")
    d.text((150, 700), "ACOUSTIC", font=ft, anchor="ls", fill="white")
    b = d.textbbox((530, 700), "DOME", font=ft, anchor="ls")
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).text((530, 700), "DOME", font=ft, anchor="ls", fill=255)
    mask = Image.fromarray(cv2.erode(np.asarray(mask), np.ones((5, 5), np.uint8)))
    im.paste((114, 114, 114), (0, 0), mask)
    p = tmp_path / "frame.png"
    im.save(p)
    f = vc.Frame(name="frame.png", kind="card", size=(W, H), deliverable="reel", image=p,
                 expect=[{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}],
                 elements=[{"name": "title", "font": "Anton-Regular.ttf", "px": 100,
                            "box": [a[0], min(a[1], b[1]), b[2], max(a[3], b[3])]}], overlap_rule=True)
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert not entry["pass"], entry["fields"]
    assert {"contrast", "text_identity"} & {d["class"] for d in entry["findings"]}


def test_text_set_in_another_face_than_the_truths_fails(tmp_path):
    # Codex note 11: IBM Plex Mono drawn where the truth (and the record) name Anton.
    vc = critic()
    im = Image.new("RGB", (W, H), (18, 16, 14))
    d = ImageDraw.Draw(im)
    ft = ImageFont.truetype(str(ROOT / "fonts" / "IBMPlexMono-SemiBold.ttf"), 80)
    box = d.textbbox((150, 700), "ACOUSTIC DOME", font=ft, anchor="ls")
    d.text((150, 700), "ACOUSTIC DOME", font=ft, anchor="ls", fill=(240, 236, 226))
    p = tmp_path / "frame.png"
    im.save(p)
    f = vc.Frame(name="frame.png", kind="card", size=(W, H), deliverable="reel", image=p,
                 expect=[{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field", "font": "Anton-Regular.ttf"}],
                 elements=[{"name": "title", "text": "ACOUSTIC DOME", "font": "Anton-Regular.ttf", "px": 80, "box": list(box)}],
                 overlap_rule=True)
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert not entry["pass"], entry["findings"]


# ---------------------------------------------------------------------------------------------- truth sources
def test_a_cover_withholds_a_disputed_figure_as_its_renderer_must():
    # Review B3: a range or disputed figure is lifted out of the cover (operator default, round 3).
    vc = critic()
    copy = {"eyebrow": "DESERT ANOMALY", "line1": "THE 25-MILE", "line2": "SAHARA EYE", "tagline": "CARVED BY NATURE"}
    rows = vc.cover_truth(copy, {"number": "25", "unit": "MILES", "conflict": True})
    assert [r["text"] for r in rows] == ["DESERT ANOMALY", "SAHARA EYE", "CARVED BY NATURE"]
    kept = vc.cover_truth(copy, {"number": "25", "unit": "MILES", "conflict": False, "hedged": False})
    assert "THE 25-MILE" in [r["text"] for r in kept]


def test_a_withheld_figure_that_would_leave_a_fragment_drops_the_line():
    # Review D2 / QF-141: "327 METERS DOWN" withheld would leave "DOWN": the renderer draws no fragment.
    vc = critic()
    fig = {"number": "327.0", "unit": "M", "hedged": True, "conflict": False, "fact_id": "fact-6", "hedge_fits": False}
    rows = vc.cover_truth({"line1": "WIELICZKA", "tagline": "327 METERS DOWN"}, fig)
    assert [r["field"] for r in rows] == ["line1"]


def write_graphic_set(s: Path, kind: str, inputs: dict, drawn: str) -> None:
    im = ground()
    d = ImageDraw.Draw(im)
    ft = ImageFont.truetype(str(ANTON), 100)
    box = d.textbbox((150, 700), drawn, font=ft, anchor="ls")
    d.rectangle((90, 540, 960, 1000), fill=(18, 16, 14))
    d.text((150, 700), drawn, font=ft, fill=(240, 236, 226), anchor="ls")
    im.save(s / f"graphic_{kind}.png")
    (s / f"graphic_{kind}.mp4.contrast.json").write_text(json.dumps({"elements": [
        {"name": "label", "text": drawn, "box": list(box), "font": "Anton-Regular.ttf", "px": 100}]}), encoding="utf-8")
    (s / f"graphic_{kind}.mp4.lineage.json").write_text(json.dumps({"settings": {"inputs": inputs}}), encoding="utf-8")


def test_a_graphic_is_judged_against_its_inputs_never_its_own_record(tmp_path):
    # Codex 9: the record and the pixels agree on ACOUSIC DOME; the renderer was given ACOUSTIC DOME.
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    write_graphic_set(s, "map", {"place": {"name": "ACOUSTIC DOME"}}, "ACOUSIC DOME")
    f = next(x for x in vc.discover(s) if x.kind == "graphic")
    assert [e["text"] for e in f.expect] == ["ACOUSTIC DOME"]
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert text_findings(entry), entry["findings"]


def test_a_graphic_kind_without_a_truth_reader_fails_rather_than_trusting_its_record(tmp_path):
    # Codex 9 as probed: a number card whose record and pixels agree on the wrong label.
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    write_graphic_set(s, "numbers", {"payload": {"stat_cards": [{"value": "40", "label": "ACOUSTIC DOME"}]}},
                      "ACOUSIC DOME")
    entry, _ = vc.judge_frame(next(x for x in vc.discover(s) if x.kind == "graphic"), tmp_path / "work", None)
    assert not entry["pass"] and "coverage" in entry["failed_classes"]


def gate_of(vc, s: Path, out: Path, manifest=None) -> dict:
    return vc.check_set(s, out, faces=False, sheet=False, manifest=manifest)


def test_an_empty_set_fails(tmp_path):
    vc = critic()
    (tmp_path / "set").mkdir()
    rep = gate_of(vc, tmp_path / "set", tmp_path / "r")
    assert rep["gate"] == "fail" and rep["coverage"]["findings"]


def test_a_render_error_or_a_picture_nobody_judged_fails(tmp_path):
    # Codex 10: a broken picture and render_errors.json with no record: zero frames judged, gate passed.
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    (s / "broken.png").write_bytes(b"not a png")
    (s / "render_errors.json").write_text(json.dumps({"missing_frame": "renderer failed"}), encoding="utf-8")
    rep = gate_of(vc, s, tmp_path / "r")
    assert rep["gate"] == "fail"
    assert any("render errors" in d["detail"] for d in rep["coverage"]["findings"])


def still_layout_set(s: Path, recipe: dict | None, still: str = "thumbnail_cover.jpg") -> None:
    Image.new("RGB", (W, H), "black").save(s / still)
    rec = {"kind": "cover", "size": [W, H], "elements": [{"name": "line1", "box": [100, 800, 900, 900], "text": "X"}]}
    if recipe is not None:
        rec["recipe"] = recipe
    (s / "still_layout.json").write_text(json.dumps({still: rec}), encoding="utf-8")


@pytest.mark.parametrize("recipe", [None, {"renderer": "some_new_renderer.draw", "copy": {"line1": "THE DOME"}}])
def test_a_black_still_with_no_truth_reader_fails(tmp_path, recipe):
    # Review B1: no recipe, or a renderer the critic has no truth reader for: nothing was checked, never a pass.
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    still_layout_set(s, recipe)
    f = vc.discover(s)[0]
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert not entry["pass"] and "coverage" in entry["failed_classes"], entry["findings"]


def test_a_picture_beside_records_that_names_no_record_fails(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    still_layout_set(s, {"renderer": "visual_engine.redraw_cover", "copy": {"line1": "THE DOME"}})
    Image.new("RGB", (W, H), "black").save(s / "story_frame_09.jpg")
    rep = gate_of(vc, s, tmp_path / "r")
    assert any(d["element"] == "story_frame_09.jpg" for d in rep["coverage"]["findings"])


def test_a_graphic_without_its_poster_frame_fails(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    (s / "graphic_map.mp4.contrast.json").write_text(json.dumps({"elements": []}), encoding="utf-8")
    entry, _ = vc.judge_frame(vc.discover(s)[0], tmp_path / "work", None)
    assert not entry["pass"] and "coverage" in entry["failed_classes"]


def test_a_frame_the_manifest_expects_must_be_judged(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    still_layout_set(s, {"renderer": "visual_engine.redraw_cover", "copy": {"line1": "THE DOME"}})
    manifest = {"images": [{"path": "thumbnail_cover.jpg"}, {"path": "covers/x/thumbnail_cover.jpg"}]}
    rep = gate_of(vc, s, tmp_path / "r", manifest)
    assert [d["element"] for d in rep["coverage"]["findings"]] == ["covers/x/thumbnail_cover.jpg"]


def test_every_report_says_what_it_did_not_gate(tmp_path):
    vc = critic()
    (tmp_path / "set").mkdir()
    rep = gate_of(vc, tmp_path / "set", tmp_path / "r")
    assert rep["coverage"]["not_gated"] and "not_judged" in rep["coverage"]
    assert rep["advisory"] is True        # the operator's ruling: the critic blocks nothing


# ---------------------------------------------------------------------------------------------- captions
ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Anton,80,&H00FFFFFF&,&H000000FF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
SPOKEN = [{"word": w, "start": i * 2.0, "dur": 1.5} for i, w in enumerate(["ACOUSTIC", "DOME", "ANCIENT"])]


def line(a: float, b: float, text: str, y: int = 500) -> str:
    return f"Dialogue: 0,0:00:{a:05.2f},0:00:{b:05.2f},Main,,0,0,0,,{{\\an7\\pos(200,{y})}}{text}\n"


def caption_set(tmp_path: Path, events: list[str], words=SPOKEN) -> Path:
    s = tmp_path / "set"
    s.mkdir(exist_ok=True)
    (s / "test_captions.ass").write_text(ASS_HEAD + "".join(events), encoding="utf-8")
    (s / "words.json").write_text(json.dumps(words), encoding="utf-8")
    return s


def caption_entry(vc, s: Path, tmp_path: Path, samples: int = 8):
    f = next(x for x in vc.discover(s) if x.kind == "caption")
    entry, _ = vc.judge_frame(f, tmp_path / "work", None, samples=samples)
    return entry


@needs_libass
@pytest.mark.parametrize("events", [
    [line(20, 22, "ACOUSTIC"), line(22, 24, "DOME"), line(24, 26, "ANCIENT")],      # shown 20 s after it is said
    [line(0, 2, "ACOUSTIC"), line(4, 6, "ANCIENT")],                                # a word left out
    [],                                                                              # no captions at all
    [line(0, 2, r"{\alpha&HFF&}ACOUSTIC"), line(2, 4, r"{\alpha&HFF&}DOME"),        # every caption transparent
     line(4, 6, r"{\alpha&HFF&}ANCIENT")],
])
def test_captions_must_show_the_narration_when_it_is_spoken(tmp_path, events):
    vc = critic()
    entry = caption_entry(vc, caption_set(tmp_path, events), tmp_path)
    assert text_findings(entry), entry["findings"]


@needs_libass
def test_captions_that_show_the_narration_in_time_pass(tmp_path):
    vc = critic()
    entry = caption_entry(vc, caption_set(tmp_path, [line(0, 2, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT")]),
                          tmp_path)
    assert entry["pass"], entry["findings"]


@needs_libass
def test_every_event_is_sampled_when_there_are_few(tmp_path):
    # Codex 8: three events, the last one outside the Reel box; samples=8 skipped it.
    vc = critic()
    s = caption_set(tmp_path, [line(0, 2, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT", 1750)])
    entry = caption_entry(vc, s, tmp_path)
    assert "safe_zone" in entry["failed_classes"], entry["findings"]


@needs_libass
@pytest.mark.parametrize("burned,ok", [("THE DOME", True), ("TNE DOME", False)])
def test_the_master_must_show_its_captions_glyphs(tmp_path, burned, ok):
    # Codex 7: the .ass and words say THE DOME; the master was burned with TNE DOME.
    vc = critic()
    words = [{"word": "THE", "start": 0, "dur": .7}, {"word": "DOME", "start": .7, "dur": 1.3}]
    s = caption_set(tmp_path, [line(0, 2, "THE DOME")], words)
    burn = tmp_path / "burn.ass"
    burn.write_text(ASS_HEAD + line(0, 2, burned), encoding="utf-8")
    filt = f"subtitles='{vc._ffmpeg_esc(burn)}':fontsdir='{vc._ffmpeg_esc(vc.FONTS_DIR)}'"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x101010:s=1080x1920:r=25:d=2", "-vf",
                    filt, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", str(s / "test.mp4")], check=True)
    f = next(x for x in vc.discover(s) if x.kind == "master")
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert bool(text_findings(entry)) is (not ok), entry["findings"]
