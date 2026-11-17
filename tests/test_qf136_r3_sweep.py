"""QF-136 round 3: probes from the find-all sweep (text and captions partitions) that the round-2 gate passed.
Each must now fail; controls pass. Pictures are drawn with the bundled faces; captions with libass (ffmpeg)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.test_qf136_r2_fail_closed import (
    ASS_HEAD,
    SPOKEN,
    H,
    W,
    caption_entry,
    caption_set,
    card,
    critic,
    ground,
    line,
    text_findings,
)

ROOT = Path(__file__).resolve().parents[1]
needs_libass = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg with libass is not installed here")


@pytest.mark.parametrize("truth,drawn", [
    ("DISCOVERY", "DISCQVERY"),                       # a letter its face draws nearly alike (sweep 1)
    ("ACOUSTIC DOME", "NOT ACOUSTIC DOME"),           # a word added before the field (sweep 4)
])
def test_a_near_alike_letter_or_an_added_prefix_fails(tmp_path, truth, drawn):
    vc = critic()
    entry = card(vc, tmp_path, truth, drawn, px=80)
    assert not entry["pass"] and text_findings(entry), entry["findings"]


def _frame(vc, tmp_path, fields, draw):
    """A frame drawn by `draw(d)`, its truth `fields` [(name, text)] and record boxes from what was drawn."""
    im = ground()
    d = ImageDraw.Draw(im)
    d.rectangle((60, 300, 1020, 1500), fill=(18, 16, 14))
    boxes = draw(d)
    p = tmp_path / "frame.png"
    im.save(p)
    return vc.Frame(name="frame.png", kind="still", size=(W, H), deliverable="story", image=p,
                    expect=[{"field": n, "text": t, "truth": "recipe"} for n, t in fields],
                    elements=[{"name": n, "box": list(b)} for n, b in boxes], overlap_rule=True)


def test_a_body_set_in_capitals_its_design_does_not_use_fails(tmp_path):
    vc = critic()
    ft = ImageFont.truetype(str(ROOT / "fonts" / "Montserrat-Variable.ttf"), 48)

    def draw(d):
        d.text((120, 900), "ACOUSTIC DOME", font=ft, fill=(240, 236, 226), anchor="ls")
        return [("body", d.textbbox((120, 900), "ACOUSTIC DOME", font=ft, anchor="ls"))]
    f = _frame(vc, tmp_path, [("body", "Acoustic dome")], draw)
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert not entry["pass"], entry["findings"]


def test_fields_out_of_their_design_order_fail(tmp_path):
    vc = critic()
    anton = ImageFont.truetype(str(ROOT / "fonts" / "Anton-Regular.ttf"), 90)
    mont = ImageFont.truetype(str(ROOT / "fonts" / "Montserrat-Variable.ttf"), 44)

    def draw(d):                         # the kicker set under the headline
        d.text((120, 700), "ACOUSTIC DOME", font=anton, fill=(240, 236, 226), anchor="ls")
        d.text((120, 800), "ANCIENT MARVEL", font=mont, fill=(240, 236, 226), anchor="ls")
        return [("line1", d.textbbox((120, 700), "ACOUSTIC DOME", font=anton, anchor="ls")),
                ("kicker", d.textbbox((120, 800), "ANCIENT MARVEL", font=mont, anchor="ls"))]
    f = _frame(vc, tmp_path, [("kicker", "ANCIENT MARVEL"), ("line1", "ACOUSTIC DOME")], draw)
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert not entry["pass"], entry["findings"]


def test_a_field_with_no_known_face_role_is_not_judged_as_passing(tmp_path):
    vc = critic()
    entry = card(vc, tmp_path, "ACOUSTIC DOME", "ACOUSTIC DOME", px=80)
    assert entry["pass"], entry["findings"]          # "title": the display role
    assert vc.intended_family("no_such_field") is None


def test_a_named_weight_is_enforced():
    vc = critic()
    st = vc.Style("Montserrat-Variable.ttf", "Medium", 48, 0.0, "plain")
    assert not vc._same_face(st, "Montserrat-Variable.ttf", "SemiBold")
    assert vc._same_face(st, "Montserrat-Variable.ttf", "Medium")
    assert not vc._same_face(vc.Style("IBMPlexMono-Medium.ttf", None, 48, 0.0, "plain"), "IBMPlexMono-SemiBold.ttf")


def test_a_picture_or_layout_entry_the_gate_did_not_judge_fails(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    Image.new("RGB", (64, 64), "black").save(s / "stray.png")
    (s / "still_layout.json").write_text(json.dumps({"missing.jpg": {"kind": "cover"}}), encoding="utf-8")
    rep = vc.check_set(s, tmp_path / "r", faces=False, sheet=False)
    assert rep["gate"] == "fail"
    assert any(d["element"] == "stray.png" for d in rep["coverage"]["findings"])
    assert any(e["kind"] == "unreadable" for e in rep["frames"])


def test_a_records_expectation_that_disagrees_with_its_recipe_fails(tmp_path):
    vc = critic()
    s = tmp_path / "set"
    s.mkdir()
    Image.new("RGB", (W, H), "black").save(s / "f.png")
    (s / "f.record.json").write_text(json.dumps({
        "frame": "f.png", "kind": "still", "size": [W, H],
        "expect": [{"field": "line1", "text": "THE DOME", "truth": "recipe"}],
        "recipe": {"kind": "story", "slide": {"line1": "THE VAULT"}, "index": 0, "count": 3},
        "elements": []}), encoding="utf-8")
    f = vc.discover(s)[0]
    assert [e["text"] for e in f.expect] == ["THE VAULT"]
    entry, _ = vc.judge_frame(f, tmp_path / "work", None)
    assert "coverage" in entry["failed_classes"]


# ---------------------------------------------------------------------------------------------- captions
@needs_libass
@pytest.mark.parametrize("events,words", [
    # an extra word in a second style (sweep B4)
    ([line(0, 2, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT"),
      "Dialogue: 0,0:00:03.00,0:00:03.50,Main,,0,0,0,,{\\an7\\pos(200,900)}EXTRA\n".replace("Main", "Second")], SPOKEN),
    # a ten-millisecond caption (sweep B9)
    ([line(0, 0.01, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT")], SPOKEN),
    # a word that fades in over two seconds (sweep B9)
    ([line(0, 2, r"{\alpha&HFF&\t(0,2000,\alpha&H00&)}ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT")], SPOKEN),
    # a line shown again ten seconds later, said once (sweep B6)
    ([line(0, 2, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT"), line(14, 16, "ANCIENT")], SPOKEN),
])
def test_sweep_caption_defects_fail(tmp_path, events, words):
    vc = critic()
    head = ASS_HEAD.replace("Style: Main,", "Style: Second,Anton,80,&H00FFFFFF&,&H000000FF&,&H00000000&,&H00000000&,"
                            "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\nStyle: Main,")
    s = tmp_path / "set"
    s.mkdir()
    (s / "test_captions.ass").write_text(head + "".join(events), encoding="utf-8")
    (s / "words.json").write_text(json.dumps(words), encoding="utf-8")
    entry = caption_entry(vc, s, tmp_path)
    assert not entry["pass"], entry["findings"]


@needs_libass
def test_captions_covered_by_an_opaque_plate_fail(tmp_path):
    vc = critic()
    plate = "Dialogue: 2,0:00:00.00,0:00:06.00,Main,,0,0,0,,{\\an7\\pos(150,480)\\p1\\c&H101010&}m 0 0 l 700 0 l 700 140 l 0 140{\\p0}\n"
    s = caption_set(tmp_path, [line(0, 2, "ACOUSTIC"), line(2, 4, "DOME"), line(4, 6, "ANCIENT"), plate])
    entry = caption_entry(vc, s, tmp_path)
    assert "contrast" in entry["failed_classes"], entry["findings"]


@needs_libass
def test_every_event_is_judged_even_with_samples_zero(tmp_path):
    vc = critic()
    events = [line(i * 0.5, i * 0.5 + 0.4, w) for i, w in enumerate(["ACOUSTIC", "DOME"])] + \
        [line(1.0, 1.4, "ANCIENT", 1750)]
    words = [{"word": w, "start": i * 0.5, "dur": 0.4} for i, w in enumerate(["ACOUSTIC", "DOME", "ANCIENT"])]
    s = caption_set(tmp_path, events, words)
    entry = caption_entry(vc, s, tmp_path, samples=0)
    assert "safe_zone" in entry["failed_classes"], entry["findings"]


# ---------------------------------------------------------------------------------------------- memory
_PEAK = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from src import visual_critic as vc
vc.check_set(Path(sys.argv[2]), Path(sys.argv[3]), faces=False, workers=1)
print(json.loads((Path(sys.argv[3]) / "timing.json").read_text())["memory_peak_mb"]["main"])
"""


def _caption_peak(tmp_path: Path, n: int) -> float:
    """Peak resident memory (MB) of a check, in a fresh process, of one caption file with `n` failing events (each
    runs past its plate: a finding, and a contact-sheet shot, every event)."""
    import subprocess
    import sys
    s = tmp_path / f"set{n}"
    s.mkdir()
    plate = "Dialogue: 0,{a},{b},Main,,0,0,0,,{{\\an7\\pos(190,490)\\p1}}m 0 0 l 120 0 l 120 120 l 0 120{{\\p0}}\n"
    ev, words = [], []
    for i in range(n):
        a, b = f"0:00:{i * 0.5:05.2f}", f"0:00:{i * 0.5 + 0.4:05.2f}"
        ev.append(plate.format(a=a, b=b) + f"Dialogue: 1,{a},{b},Main,,0,0,0,,{{\\an7\\pos(200,500)}}ACOUSTIC\n")
        words.append({"word": "ACOUSTIC", "start": i * 0.5, "dur": 0.4})
    (s / "test_captions.ass").write_text(ASS_HEAD + "".join(ev), encoding="utf-8")
    (s / "words.json").write_text(json.dumps(words), encoding="utf-8")
    res = subprocess.run([sys.executable, "-c", _PEAK, str(ROOT), str(s), str(tmp_path / f"r{n}")],
                         capture_output=True, text=True, timeout=1800, check=True)
    return float(res.stdout.strip().splitlines()[-1])


@needs_libass
def test_a_check_s_memory_does_not_grow_with_the_events_it_judges(tmp_path):
    # Round 3: every judged event once kept a full frame for the contact sheet; one check reached 48 GB. Ten times
    # the failing events must not cost more than a bounded amount.
    few, many = _caption_peak(tmp_path, 12), _caption_peak(tmp_path, 120)
    assert many - few < 150, (few, many)
