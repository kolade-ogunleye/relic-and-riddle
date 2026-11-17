"""QF-136: the visual critic is a gate that verifies text from the pixels, not from the renderer's record.

Every test draws its own frame (Pillow, a committed real photo as ground) or caption file, so the truth is known;
the record handed to the critic is a hint that may lie. Precedent: GLYPH-DROP (drawable() deleted glyphs while the
records still held the signed string): a record cannot be the evidence for the code that wrote it.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STILL = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "stills" / \
    "richat_structure_sahara_eye_01_commons_960.jpg"
WORDS = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "runs" / \
    "run_tunguska_event_1908_1792930600" / "words.json"
ANTON = ROOT / "fonts" / "Anton-Regular.ttf"
W, H = 1080, 1920


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


def draw_line(im: Image.Image, text: str, xy, px: int = 100, fill=(240, 236, 226), band=True) -> tuple:
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(ANTON), px)
    box = d.textbbox(xy, text, font=font, anchor="ls")
    if band:
        d.rectangle((box[0] - 30, box[1] - 24, box[2] + 30, box[3] + 24), fill=(18, 16, 14))
    d.text(xy, text, font=font, fill=fill, anchor="ls")
    return box


def frame(vc, im: Image.Image, tmp_path: Path, expect, elements, deliverable="reel", kind="card", zone=None):
    p = tmp_path / "f.png"
    im.save(p)
    return vc.Frame(name="f.png", kind=kind, size=im.size, deliverable=deliverable, image=p, expect=expect,
                    elements=elements, zone=zone, overlap_rule=True)


def judge(vc, f, tmp_path):
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    return entry


def classes(entry) -> list[str]:
    return [d["class"] for d in entry["findings"]]


def test_a_dropped_glyph_is_found_from_the_pixels_while_the_record_holds_the_signed_string(tmp_path):
    """GLYPH-DROP: the frame shows ACOUSIC; the record (the renderer's own word) says ACOUSTIC DOME, as drawable()'s
    records did. The critic shapes the truth itself and does not find ACOUSTIC in the pixels."""
    vc = critic()
    im = ground()
    box = draw_line(im, "ACOUSIC DOME", (150, 700))
    truth = [{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}]
    record = [{"name": "title", "box": list(box), "text": "ACOUSTIC DOME"}]
    entry = judge(vc, frame(vc, im, tmp_path, truth, record), tmp_path)
    missing = [d for d in entry["findings"] if d["class"] == "text_identity"]
    assert "ACOUSTIC" in [d.get("word") for d in missing], entry["findings"]
    assert entry["pass"] is False and "text_identity" in entry["failed_classes"]


def test_the_whole_field_passes_and_its_box_comes_from_the_pixels(tmp_path):
    vc = critic()
    im = ground()
    box = draw_line(im, "ACOUSTIC DOME", (150, 700))
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}],
                            [{"name": "title", "box": list(box)}]), tmp_path)
    assert entry["findings"] == [], entry["findings"]
    row = entry["fields"][0]
    assert all(w["ncc"] >= vc.WORD_MIN for w in row["words"])
    assert max(abs(a - b) for a, b in zip(row["box"], box)) <= 3      # the pixels' box, not the record's


def test_a_record_box_away_from_its_text_is_a_record_mismatch(tmp_path):
    vc = critic()
    im = ground()
    box = draw_line(im, "ACOUSTIC DOME", (150, 700))
    lying = [box[0], box[1] + 90, box[2], box[3] + 90]
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}],
                            [{"name": "title", "box": lying}]), tmp_path)
    assert "record_mismatch" in classes(entry), entry["findings"]
    assert "text_identity" not in classes(entry)


def test_record_text_that_is_not_the_truth_is_a_record_mismatch(tmp_path):
    vc = critic()
    im = ground()
    box = draw_line(im, "ACOUSTIC DOME", (150, 700))
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}],
                            [{"name": "title", "box": list(box), "text": "ACOUSTIC DOMES"}]), tmp_path)
    assert [d["basis"] for d in entry["findings"] if d["class"] == "record_mismatch"] == ["record"]


def test_a_word_over_the_frame_edge_is_a_mid_word_cut(tmp_path):
    vc = critic()
    im = ground()
    box = draw_line(im, "WHISPERING DOME", (-120, 900), band=False)
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "WHISPERING DOME", "truth": "input"}],
                            [{"name": "title", "box": [max(0, box[0]), box[1], box[2], box[3]]}]), tmp_path)
    cuts = [d for d in entry["findings"] if d["class"] in ("text_cut", "text_identity")]
    assert cuts, entry["findings"]
    assert any(d["class"] == "text_cut" and d["mid_word"] for d in cuts) or \
        any(d["class"] == "text_identity" and d["word"] == "WHISPERING" for d in cuts)


def test_text_under_the_story_interface_names_the_platforms(tmp_path):
    vc = critic()
    im = ground()
    box = draw_line(im, "ACOUSTIC DOME", (150, 200))          # the Story box starts at y 269
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "ACOUSTIC DOME", "truth": "input"}],
                            [{"name": "title", "box": list(box)}], deliverable="story", kind="still"), tmp_path)
    sz = [d for d in entry["findings"] if d["class"] == "safe_zone"]
    assert sz and sz[0]["platforms"] == ["facebook_story", "instagram_story"], entry["findings"]


def test_contrast_is_measured_on_the_pixels_whatever_the_record_says(tmp_path):
    vc = critic()
    im = Image.new("RGB", (W, H), (200, 196, 188))
    box = draw_line(im, "ACOUSTIC DOME", (150, 700), fill=(236, 232, 224), band=False)
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "title", "text": "ACOUSTIC DOME", "truth": "input"}],
                            [{"name": "title", "box": list(box), "contrast": 7.0}]), tmp_path)
    c = [d for d in entry["findings"] if d["class"] == "contrast" and d["basis"] == "pixels"]
    assert c and c[0]["value"] < 1.5, entry["findings"]


def test_fields_whose_inks_cross_are_an_overlap(tmp_path):
    vc = critic()
    im = ground()
    a = draw_line(im, "ACOUSTIC DOME", (150, 700))
    # the second line set too tight: its capitals' tops run into the first line's foot (a leading collapse)
    b = draw_line(im, "WHISPERING HALL", (150, 776), fill=(250, 210, 40), band=False)
    entry = judge(vc, frame(vc, im, tmp_path, [{"field": "t1", "text": "ACOUSTIC DOME", "truth": "input"},
                                               {"field": "t2", "text": "WHISPERING HALL", "truth": "input"}],
                            [{"name": "t1", "box": list(a)}, {"name": "t2", "box": list(b)}]), tmp_path)
    assert "overlap" in classes(entry), entry["findings"]


def test_the_gate_never_scores_writes_only_outside_the_set_and_repeats_itself(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    im = ground()
    box = draw_line(im, "ACOUSIC DOME", (150, 700))
    im.save(s / "card.png")
    (s / "card.record.json").write_text(json.dumps({
        "frame": "card.png", "kind": "card", "size": [W, H], "deliverable": "reel",
        "expect": [{"field": "title", "text": "ACOUSTIC DOME", "truth": "signed field"}],
        "elements": [{"name": "title", "box": list(box)}]}), encoding="utf-8")
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in s.iterdir()}
    with pytest.raises(ValueError):
        vc.check_set(s, s / "report", faces=False)
    r1 = vc.check_set(s, tmp_path / "r1", faces=False)
    vc.check_set(s, tmp_path / "r2", faces=False)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in s.iterdir()} == before
    assert (tmp_path / "r1" / "report.json").read_bytes() == (tmp_path / "r2" / "report.json").read_bytes()
    assert r1["gate"] == "fail" and r1["per_class"]["text_identity"]["pass"] is False
    text = (tmp_path / "r1" / "report.json").read_text(encoding="utf-8")
    assert '"score"' not in text and "rating" not in text
    assert r1["faces"].startswith("NOT CHECKED")      # said, never silent; and a gate without faces fails
    for jpg in (tmp_path / "r1" / "sheet").glob("*.jpg"):
        assert jpg.stat().st_size <= 300 * 1024


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Badge,{family},39,&H00E2EDF3&,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1
Style: Plate,{family},39,&H26120E0B&,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Plate,,0,0,0,,{{\\pos(132,300)\\p1}}m 0 0 l {plate_w} 0 l {plate_w} 45 l 0 45{{\\p0}}
Dialogue: 1,0:00:00.00,0:00:02.00,Badge,,0,0,0,,{{\\an7\\pos(146,300)}}AI ILLUSTRATION NOT A PHOTO
"""

needs_libass = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg with libass is not installed here")


@needs_libass
@pytest.mark.parametrize("family,plate_w,want", [
    ("RR Critic Test No Such Family", 900, {"glyph_fallback"}),        # QF-133: a face libass never resolves
    ("Anton", 900, set()),                                           # a bundled face, its plate wide enough
    ("Anton", 200, {"plate_overrun"}),                               # the badge runs past its plate
])
def test_caption_faces_and_plates_are_judged_on_libass_pixels(tmp_path, family, plate_w, want):
    vc = critic()
    ass = tmp_path / "badge.ass"
    ass.write_text(ASS_HEAD.format(family=family, plate_w=plate_w), encoding="utf-8")
    f = vc.Frame(name="badge.ass", kind="caption", size=(W, H), deliverable="reel", ass=ass)
    found, rows = vc.caption_findings(f, tmp_path / "work")
    # no narration given here: said as a coverage finding (round 2), the rest judged as before
    assert {d["class"] for d in found} == want | {"coverage"}, found
    assert rows and rows[0]["glyphs"]


@needs_libass
def test_caption_words_must_be_the_narrations(tmp_path):
    vc = critic()
    from src.video_composer import VideoComposer
    words = json.loads(WORDS.read_text(encoding="utf-8"))
    ass = tmp_path / "c.ass"
    VideoComposer().build_subtitles(words, ass)
    (tmp_path / "c.words.json").write_text(json.dumps(words), encoding="utf-8")
    f = vc.discover(tmp_path)[0]
    ok, _ = vc.caption_findings(f, tmp_path / "work", samples=1)
    assert not [d for d in ok if d["class"] == "text_identity"], ok
    text = ass.read_text(encoding="utf-8")
    word = next(w["word"] for w in words if len(w["word"]) >= 6 and w["word"].isalpha())
    ass.write_text(text.replace(word, word[:2] + word[3:]), encoding="utf-8")       # a glyph dropped in the captions
    bad, _ = vc.caption_findings(f, tmp_path / "work", samples=1)
    assert [d for d in bad if d["class"] == "text_identity"], bad


def test_the_safe_zone_table_and_gates_are_the_repos():
    vc = critic()
    from src import brand, graphics, safe_zones
    assert vc.NONTEXT_MIN == graphics.NONTEXT_MIN and vc.TEXT_CONTRAST == brand.TEXT_CONTRAST
    assert vc.platform_box("story", (W, H), "still") == safe_zones.safe_box("story", W, H)
    assert vc.platform_box("reel", (W, H), "graphic") == safe_zones.graphics_box(W, H)
    assert vc.covering_platforms((500, 1500, 600, 1560), "reel", (W, H)) == \
        ["facebook_reels", "instagram_reels", "youtube_shorts"]


def test_faces_run_through_the_pipelines_model_on_gpu_and_cpu_alike():
    vc = critic()
    if not vc.FACE_MODEL.is_file():
        pytest.skip(f"the pipeline's face model is not installed here ({vc.FACE_MODEL.name}, gitignored tools/)")
    import cv2
    img = np.asarray(ground())
    cpu = vc.FaceDetector(device="cpu").detect(img)
    auto = vc.FaceDetector(device="auto").detect(img)
    assert [f["box"] for f in cpu] == [f["box"] for f in auto]
    ref = cv2.FaceDetectorYN.create(str(vc.FACE_MODEL), "", (W, H), score_threshold=vc.FACE_SCORE)
    _, faces = ref.detect(np.ascontiguousarray(img[..., ::-1]))
    assert len(cpu) == (0 if faces is None else len(faces))


def test_a_face_under_a_field_is_found(tmp_path):
    vc = critic()
    out = vc.face_findings([("title", (100, 100, 400, 200))], [{"box": [150, 150, 250, 260], "confidence": 0.9}])
    assert [d["class"] for d in out] == ["face_overlap"] and out[0]["value"] >= vc.FACE_SHARE
    assert vc.face_findings([("title", (100, 100, 400, 200))], [{"box": [500, 500, 600, 600], "confidence": 0.9}]) == []

