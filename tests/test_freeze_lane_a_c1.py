"""Design-freeze lane A, cycle 1 (maps, routes, timelines, then-and-now, scale / stat / cross-section, number cards).

Findings closed here (C:/tmp/qf-handoff/queue/LANE-AB-carry-in-r8.md, CYCLE0.r8-AD-regression.md, CYCLE0.r8i-AD-final.md)
and folded rows (QF-102, QF-104, QF-138), each as a class with an invariant, never a pinned measurement:

- R4: a depth's gap note ("ABOUT 181 M") sits on its referent's ground beside its bracket, by one rule on both formats
  (it sat on the rock outside the shaft on 16:9 and was dropped on 9:16).
- F2 / S2-4: the scale subject's drawn proportion states only its signed dimension (the Aswan obelisk, a length, stood
  as a block about 128 ft wide against its 138 ft): a length lies on the ground at its signed length, a height stands
  no wider than a slender share of its height.
- Memnon 9:16 NOW plate over the colossus: the 9:16 markers may take a centred column (the quietest of the candidates).
- QF-102: a site name with no break is returned whole as list[str] with its font, or refused with a reason.
- QF-104: make_all's pool workers see the caller's palette and poster-only mode.
- QF-138: no caught TypeError around the contrast measure; thin text is an explicit result; a real TypeError propagates.

Synthetic inputs only (CI has no output/); renders are plan-only (tests/test_figure_truth_r7.py's pattern).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageFont

from src import brand, graphics, then_now, typography

ROOT = Path(__file__).resolve().parents[1]
TALL, WIDE = (1080, 1920), (1920, 1080)

# real run scale facts (output/run_*/script_metadata.json), as literals
WIELICZKA = {"value": 327.0, "unit": "m", "dimension": "depth", "subject": "Wieliczka Salt Mine", "approximate": True}
DERINKUYU = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
OBELISK = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Unfinished obelisk", "approximate": True}
MONOLITH = {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa temple", "approximate": False}
KAILASA = {"value": 32.0, "unit": "m", "dimension": "height", "subject": "Kailasa temple monolith", "approximate": True}


@pytest.fixture
def plan_only(monkeypatch):
    """Renders composed but never encoded (0.1 s); the non-text marks (the subject, the shaft) captured as boxes."""
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    marks: dict[str, tuple[int, int, int, int]] = {}

    def nontext(out, frame, ms, ground=None):
        for name, mask in ms:
            marks[name] = mask.getbbox()
    monkeypatch.setattr(graphics, "record_nontext", nontext)

    def encode(frames, out, n, width=None, height=None):
        for _ in frames:
            pass
        Path(out).write_bytes(b"")
        return Path(out)
    monkeypatch.setattr(graphics, "_encode", encode)
    made: list[Any] = []
    init = typography.TextPlate.__init__

    def recording(self, *a, **k):
        init(self, *a, **k)
        made.append(self)
    monkeypatch.setattr(typography.TextPlate, "__init__", recording)
    marks["_plates"] = made
    return marks


def _record(out: Path) -> dict:
    return json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))


def _el(rec: dict, name: str) -> dict | None:
    return next((e for e in rec["elements"] if e["name"] == name), None)


def _px_per_unit(rec: dict) -> float:
    """The ruler's scale on the drawing, from its own records: px per ruler unit between the zero and the next tick."""
    rulers = sorted((r for r in rec["rulers"]), key=lambda r: r["value"])
    # freeze lane A cycle 3: between two non-zero ticks where the ruler has them (the zero's datum is the ground
    # line's centre, 1-2 px off the ticks' grid: over a subject taller than its landmark that offset alone exceeded
    # the 2.5 px tolerance), else from the zero
    pos = [r for r in rulers if r["value"] > 0]
    a, b = (pos[0], pos[1]) if len(pos) >= 2 else (rulers[0], pos[0])
    return abs(b["datum"] - a["datum"]) / (b["value"] - a["value"])


# ------------------------------------------------------------------------------------------------ R4 gap note

@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("fact", [WIELICZKA, DERINKUYU], ids=["landmark-vanishes", "landmark-rises-past"])
def test_the_gap_note_is_drawn_on_both_formats_beside_its_bracket_on_its_own_ground(tmp_path, plan_only, fact, size):
    out = graphics.render_scale(fact, tmp_path / "scale.mp4", *size)
    rec = _record(out)
    note = _el(rec, "bracket")
    assert note is not None, "the gap note is drawn on every format (both or neither: the rule is one)"
    x0, y0, x1, y1 = note["box"]
    sx0, sy0, sx1, sy1 = plan_only["shaft"]            # the shaft's outline: its walls and the ground line at sy0
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    ref = graphics.pick_reference(feet)
    if ref[1] < feet:      # the landmark vanishes: the note in the shaft's air, never across a wall
        assert sx0 + 3 < x0 and x1 < sx1 - 3, (note["box"], plan_only["shaft"])
        assert sy0 < y0 and y1 < sy1, (note["box"], plan_only["shaft"])
    else:                  # the landmark rises past the ground: the note above the ground line, never across it
        assert y1 < sy0, (note["box"], plan_only["shaft"])
    for name in ("figure", "figure_metric", "subject"):
        head = _el(rec, name)
        if head is not None:
            hx0, hy0, hx1, hy1 = head["box"]
            assert x1 < hx0 - 20 or x0 > hx1 + 20 or y1 < hy0 - 20 or y0 > hy1 + 20, (name, head["box"], note["box"])
    # truth: the note says the measured gap, hedged like the figure
    gap = graphics._short(abs(ref[1] - feet), graphics.lead_unit(fact))
    assert note["text"] == ("ABOUT " if fact["approximate"] else "") + gap


def test_gap_note_x_never_crosses_a_wall_and_refuses_when_no_side_holds_it():
    shaft = (300.0, 600.0)
    for bx in (320.0, 450.0, 580.0):
        for w in (40.0, 120.0, 200.0, 400.0):
            x = graphics.gap_note_x(bx, w, 12, shaft, False, 1000.0)
            if x is None:
                continue
            assert shaft[0] + graphics.GAP_NOTE_WALL <= x and x + w <= shaft[1] - graphics.GAP_NOTE_WALL
            assert x >= bx + graphics.GAP_NOTE_AIR or x + w <= bx - 12 - graphics.GAP_NOTE_AIR   # never on the bracket
    assert graphics.gap_note_x(450.0, 400.0, 12, shaft, False, 1000.0) is None
    above = graphics.gap_note_x(450.0, 100.0, 12, shaft, True, 1000.0)
    assert above == 450.0 + graphics.GAP_NOTE_AIR
    assert graphics.gap_note_x(950.0, 100.0, 12, shaft, True, 1000.0) is None    # never past the column


# ------------------------------------------------------------------------------------------------ F2 proportion

@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("fact", [OBELISK, MONOLITH, KAILASA], ids=["length", "ft-height", "m-height"])
def test_the_subject_is_drawn_at_its_signed_size_and_its_other_dimension_stays_slender(tmp_path, plan_only, fact,
                                                                                       size):
    rec = _record(graphics.render_scale(fact, tmp_path / "scale.mp4", *size))
    sx0, sy0, sx1, sy1 = plan_only["subject"]
    w, h = sx1 - sx0, sy1 - sy0
    unit = graphics.lead_unit(fact)
    px = _px_per_unit(rec) * graphics.per_ft(unit)            # px per foot, from the ruler as drawn
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    signed = feet * px
    if fact["dimension"] == "length":     # lies on the ground: its length along it, a slender thickness
        assert abs(w - signed) <= 2.5, (w, signed)
        assert h <= graphics.SUBJECT_LYING_ASPECT * w + 2 or h <= graphics.SUBJECT_MIN_PX + 1, (w, h)
    else:                                 # stands: its height up the ruler, never wider than a slender share of it
        assert abs(h - signed) <= 2.5, (h, signed)
        assert w <= graphics.SUBJECT_UPRIGHT_ASPECT * h + 2 or w <= graphics.SUBJECT_MIN_PX + 1, (w, h)
    # its label stands over it and clear of the ruler's figures
    lab = _el(rec, "block_label")
    assert lab is not None and lab["box"][3] <= sy0
    for r in rec["rulers"]:
        rb = r["box"]
        assert lab["box"][0] > rb[2] or lab["box"][3] < rb[1] or lab["box"][1] > rb[3], (lab["box"], rb)


def test_subject_extent_states_only_the_signed_dimension():
    for feet in (5.0, 60.0, 138.0, 600.0):
        for px_ft in (0.3, 1.7, 4.0):
            w, h = graphics.subject_extent(feet, px_ft, 170, True)
            assert w == pytest.approx(feet * px_ft) and h <= max(graphics.SUBJECT_MIN_PX,
                                                                 graphics.SUBJECT_LYING_ASPECT * w + 1e-9)
            w, h = graphics.subject_extent(feet, px_ft, 170, False)
            assert h == pytest.approx(feet * px_ft) and w <= max(graphics.SUBJECT_MIN_PX, min(170, 0.5 * h) + 1e-9)
    assert graphics.subject_lying(OBELISK) and not graphics.subject_lying(MONOLITH)
    assert not graphics.subject_lying(WIELICZKA, depth=True)


# ------------------------------------------------------------------------------------------------ then-and-now side

def test_the_916_markers_may_take_a_centred_column_and_left_stays_the_default():
    sides = then_now.marker_sides(*TALL, 130)
    centres = [s for s in sides if s.startswith("centre:")]
    assert sides[0] == "left" and sides[-1] == "right" and len(centres) >= 3
    x0, _y0, x1, _ = then_now.safe_zones.graphics_box(*TALL)
    for s in centres:
        x = int(s.split(":")[1])
        assert x0 + 18 + 130 <= x <= x1 - 18 - 130
    assert then_now.quieter_side({"left": 0.10, "centre:400": 0.095, "right": 0.12}) == "left"   # under the margin
    assert then_now.quieter_side({"left": 0.13, "centre:400": 0.10, "centre:600": 0.09, "right": 0.12}) == "centre:600"
    assert then_now.quieter_side({"left": 0.0, "right": 0.0}) == "left"


@pytest.mark.parametrize("which,year", [("then", 1876), ("now", 2011)])
def test_a_centred_marker_centres_each_line_on_its_column(which, year):
    side = "centre:600"
    kxy, fxy, anchor = then_now.marker_lines(which, year, *TALL, side=side)
    assert anchor == "lt"
    kf, yf = then_now.kicker_font("portrait"), then_now.year_font("portrait")
    rule = then_now.marker_rule(kxy, which.upper(), "portrait", anchor)
    k_ink = then_now._ink(kf, which.upper(), then_now.kicker_track("portrait"))
    k_left, k_right = rule[0], kxy[0] + k_ink[2]
    y_ink = then_now._ink(yf, str(year))
    y_left, y_right = fxy[0] + y_ink[0], fxy[0] + y_ink[2]
    assert abs((k_left + k_right) / 2 - 600) <= 2 and abs((y_left + y_right) / 2 - 600) <= 2


# ------------------------------------------------------------------------------------------------ QF-102

@pytest.mark.parametrize("name", ["Catherine Palace", "Wieliczka", "Mohenjo-daro", "Agia Sophia of Trebizond",
                                  "Göbekli Tepe", "Санкт-Петербург"])
@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_a_site_name_is_returned_whole_as_lines_and_a_font(name, size):
    lines, font = graphics.marker_label_lines(name, *size)
    assert isinstance(lines, list) and all(isinstance(ln, str) for ln in lines)
    assert isinstance(font, ImageFont.FreeTypeFont) and 1 <= len(lines) <= graphics.MARKER_LABEL_ROWS
    assert " ".join(" ".join(lines).split()) == " ".join(name.upper().split())


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_a_site_name_with_no_break_is_refused_with_its_reason_never_cut(size):
    with pytest.raises(typography.TextFitError) as e:
        graphics.marker_label_lines("Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch" * 3, *size)
    assert "LLANFAIR" in " ".join(e.value.lines).upper()


# ------------------------------------------------------------------------------------------------ QF-104

def test_make_all_workers_see_the_palette_and_the_poster_only_mode(tmp_path, monkeypatch):
    from src import art_direction, card_composition, geo
    seen: dict[str, tuple[Any, bool]] = {}

    def probe(name):
        def job(*_a, **_k):
            seen[name] = (art_direction.palette().get("accent"), graphics._POSTER_ONLY.get())
        return job
    monkeypatch.setattr(graphics, "render_stat_cards", probe("numbers"))
    monkeypatch.setattr(graphics, "render_scale", probe("scale"))
    monkeypatch.setattr(graphics, "render_timeline", probe("timeline"))
    monkeypatch.setattr(geo, "locate", lambda topic: None)
    monkeypatch.setattr(card_composition, "make_all", lambda *a, **k: [])
    payload = {"stat_cards": [{"value": "36", "label": "Hours"}], "scale_fact": OBELISK,
               "timeline": [{"year": 1941, "label": "Taken"}, {"year": 1944, "label": "Lost"}]}
    palette = {"accent": [12, 34, 56], "grade_bias": 0.0}
    with graphics.poster_only():
        graphics.make_all(payload, {"title": "x"}, tmp_path, palette=palette)
    assert set(seen) == {"numbers", "scale", "timeline"}, "three jobs ran in the pool"
    for name, (accent, poster) in seen.items():
        assert list(accent) == [12, 34, 56] and poster, name


# ------------------------------------------------------------------------------------------------ QF-138

def _catches_typeerror(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    names = [t] if not isinstance(t, ast.Tuple) else list(t.elts)
    return any(isinstance(n, ast.Name) and n.id == "TypeError" for n in names)


@pytest.mark.parametrize("module", ["graphics.py", "timeline_dense.py", "then_now.py"])
def test_no_contrast_measure_is_wrapped_in_a_caught_typeerror(module):
    """QF-138: a broad except TypeError round plan_text / ring_contrast was the stack's thin-face signal; it is gone,
    so a real TypeError in the measure path is never swallowed."""
    tree = ast.parse((ROOT / "src" / module).read_text(encoding="utf-8"))
    measure = {"plan_text", "ring_contrast", "text_contrast", "place_text", "plan_label", "_JoinedPlate",
               "quantity_plate", "_plan_thin", "ruler_label"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(_catches_typeerror(h) for h in node.handlers):
            called = {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                      for b in node.body for n in ast.walk(b) if isinstance(n, ast.Call)}
            assert not called & measure, (module, node.lineno, called & measure)


@pytest.mark.parametrize("call", ["plan_thin", "ruler_label", "section_plan"])
def test_an_unrelated_typeerror_in_the_measure_path_propagates(monkeypatch, call):
    ground = Image.fromarray(np.full((200, 600, 3), 10, np.uint8))
    font = typography.get_font(brand.TEXT_FONT, 24, weight=700)

    def broken(*_a, **_k):
        raise TypeError("an unrelated fault")
    monkeypatch.setattr(typography, "ring_contrast", broken)
    with pytest.raises(TypeError, match="unrelated"):
        if call == "plan_thin":
            graphics._plan_thin(ground, (20, 100), "GEORGIA", font, brand.STONE, "ls")
        elif call == "ruler_label":
            graphics.ruler_label(ground, 300, 100, "0 M", graphics.data_font(20), brand.STONE_LT)
        else:
            graphics._plan(ground, (20, 100), "0 FT", graphics.data_font(14), brand.STONE_LT)


def test_thin_text_is_an_explicit_result():
    """A face too thin to erode is measured on its drawn colour (a ratio, not an exception); a plate with no
    measurable ratio is the explicit thin_unmeasured result."""
    ground = Image.fromarray(np.full((200, 600, 3), 10, np.uint8))
    thin = typography.get_font(brand.TEXT_FONT, 10, weight=300)
    plate = graphics._plan_thin(ground, (20, 100), "6 YEARS LATER", thin, brand.STONE, "ls", halo_px=None, track=2)
    assert plate.contrast is not None and not graphics.thin_unmeasured(plate)

    class _P:
        empty, contrast = False, None
    assert graphics.thin_unmeasured(_P())
    _P.empty = True
    assert not graphics.thin_unmeasured(_P())


# ------------------------------------------------------------------------------------------------ R3 diagram drawing

@pytest.mark.parametrize("fact", [OBELISK, WIELICZKA, KAILASA], ids=["length", "depth", "height"])
@pytest.mark.parametrize("box", [(1296, 900), (1296, 1100), (972, 700)], ids=["wide-box", "tall-box", "1080-slide"])
def test_a_carousel_diagram_is_the_drawing_alone_at_the_carousel_label_floor(tmp_path, plan_only, fact, box):
    """R3 (r8 AD, lanes A/D): the carousel diagram was the 9:16 scale graphic shrunk into the slide (labels ~19 px
    against the 36 px floor at 1440). The drawing is composed for the slide's own art box: no heading, figure line or
    meaning (the slide's copy says them), every label at the carousel floor or more, everything on the canvas."""
    slide_w = 1440 if box[0] > 1000 else 1080
    floor = graphics.diagram_label_floor(slide_w)
    rec = _record(graphics.render_scale(fact, tmp_path / "d.mp4", *box, drawing_floor=floor))
    names = {e["name"] for e in rec["elements"]}
    assert not names & {"subject", "figure", "figure_metric"} and not any(n.startswith("meaning") for n in names)
    assert "block_label" in names or "bracket" in names
    for pl in plan_only["_plates"]:
        if not pl.empty:
            assert pl.font.size >= floor - 0.5, (pl.text, pl.font.size, floor)
    for e in rec["elements"]:
        bx0, by0, bx1, by1 = e["box"]
        assert 0 <= bx0 and bx1 <= box[0] and 0 <= by0 and by1 <= box[1], (e["name"], e["box"])


# ------------------------------------------------------------------------------------------------ cycle 2

@pytest.mark.parametrize("fact", [OBELISK, WIELICZKA, KAILASA], ids=["length", "depth", "height"])
def test_the_diagram_slide_copy_is_the_graphics_own_verified_words(tmp_path, plan_only, fact):
    """Ruling 4: a carousel diagram slide sets only the scale graphic's own figure, conversion and meaning, the
    strings the 9:16 graphic draws from the signed fact (never new wording)."""
    copy = graphics.scale_copy(fact)
    rec = _record(graphics.render_scale(fact, tmp_path / "s.mp4", *TALL))
    texts = {e["name"]: e["text"] for e in rec["elements"]}
    assert texts["figure"] == copy["figure"] and texts.get("figure_metric", "") == copy["metric"]
    drawn = " ".join(t for n, t in sorted(texts.items()) if n.startswith("meaning"))
    assert " ".join(" ".join(copy["meaning"]).split()) == " ".join(drawn.split())


@pytest.mark.parametrize("place,want", [
    ({"label": "Ouadane", "region": "Adrar Plateau", "city": "Adrar Plateau", "country": "Mauritania"},
     "ADRAR PLATEAU, MAURITANIA"),
    ({"label": "Catherine Palace", "country": "Russia"}, ""),
    ({"label": "Wieliczka", "city": "Wieliczka", "country": "Poland"}, ""),
    ({"label": "Kailasa Temple, Ellora Caves", "region": "Ellora Caves", "country": "India"}, ""),
    ({"label": "Pantheon", "city": "Rome", "country": "Italy"}, "ROME, ITALY"),
])
def test_the_region_line_names_the_recorded_region_never_the_site_again(place, want):
    assert graphics.region_line(place) == want


def _layout(anchor=(540.0, 900.0), above=True):
    lines, font = graphics.marker_label_lines("Ouadane", *TALL)
    base = anchor[1] - 120 if above else anchor[1] + 120
    rows = graphics._label_rows(lines, font, anchor[0], base, "ms")
    lb = graphics._rows_box(rows, font)
    return graphics.MarkerLayout("pin", anchor, tuple(rows), font.size, lb, "above" if above else "below", None,
                                 (anchor[0] - 20, anchor[1] - 70, anchor[0] + 20, anchor[1]))


@pytest.mark.parametrize("above", [True, False], ids=["label-over-pin", "label-under-pin"])
def test_the_region_line_joins_the_site_label_always_under_the_name_under_the_gates(above):
    box = graphics.get_text_box(*TALL)
    lay = _layout(above=above)
    out = graphics.with_region_line(lay, "ADRAR PLATEAU, MAURITANIA", *TALL, box)
    rec = graphics.LAST_REGION_LINE
    assert rec["shown"] and len(out.extra) == 1
    text, (_x, _base), _anchor, px = out.extra[0]
    assert text == "ADRAR PLATEAU, MAURITANIA" and px < lay.font_px            # one step under the site's
    assert px >= graphics.label_floor(*TALL)                                   # never under the floor
    rb = rec["box"]
    assert box[0] <= rb[0] and rb[2] <= box[2] and box[1] <= rb[1] and rb[3] <= box[3]
    assert not graphics.LayoutConstraintSolver.boxes_intersect(rb, lay.marker_box, 0)
    assert rec["side"] == "below"                                             # always under the name (ruling)
    name_ink = graphics._rows_box(list(out.rows), graphics.place_font(lay.font_px))
    assert rb[1] >= name_ink[3]                                               # under the name's ink, never above
    if above:      # the label over its pin keeps its foot: the line takes the name's old place, the name rises
        assert abs(out.label_box[3] - lay.label_box[3]) <= 8                 # (a comma descends a few px)
    else:          # the label under its pin: the name stays where it was
        assert out.rows == lay.rows
    assert out.label_box[1] <= rb[1] and out.label_box[3] >= rb[3]            # the label box holds it
    # one member of the label set: planned with the site's rows, one treatment
    ground = Image.new("RGB", TALL, (200, 190, 170))
    plates = graphics.label_plates_for(ground, out)
    names = [n for n, _ in plates]
    assert names.count("region") == 1 and names.count("place") == len(lay.rows)
    variants = {getattr(p, "variant", None) for _, p in plates}
    assert len(variants) == 1


def test_a_region_line_with_no_place_is_not_drawn_and_says_why():
    lay = _layout()
    tiny = (lay.label_box[0], lay.label_box[1], lay.label_box[2], lay.label_box[3])   # no room past the label
    out = graphics.with_region_line(lay, "ADRAR PLATEAU, MAURITANIA", *TALL, tiny)
    assert out is lay and not graphics.LAST_REGION_LINE["shown"] and graphics.LAST_REGION_LINE["reason"]
    assert graphics.with_region_line(lay, "", *TALL, graphics.get_text_box(*TALL)) is lay


# ------------------------------------------------------------------------------------------------ referent note

@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
@pytest.mark.parametrize("fact", [OBELISK, KAILASA, MONOLITH, DERINKUYU, WIELICZKA],
                         ids=["aswan-length", "m-height", "ft-height", "ft-depth", "m-depth"])
def test_a_landmarks_referent_note_is_drawn_whole_at_its_floor_or_recorded_refused(tmp_path, plan_only, fact, size):
    """Regression (cycle-1 before/after, Aswan 9:16): "GROUND TO TORCH TIP" stopped being drawn (it would have set at
    24 px against its 30). A referent note is meaning: on the board's real scale facts it is drawn, whole, at or
    above the label floor on both formats (else it is refused with a recorded reason, never silently dropped)."""
    from src.reference_facts import reference_note
    feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
    ref = graphics.pick_reference(feet)
    rec = _record(graphics.render_scale(fact, tmp_path / "s.mp4", *size))
    note = reference_note(ref[0]) if fact["dimension"] != "depth" else ""
    rows = [e for e in rec["elements"] if e["name"].startswith("ref_note")]
    if not note:
        assert not rows
        return
    got = graphics.LAST_REFERENT_NOTE
    assert got.get("note") == note
    real = fact in (OBELISK,)          # the board's real height/length scale fact: must be drawn
    if real or got.get("shown"):
        assert got["shown"], got
        assert " ".join(" ".join(e["text"] for e in rows).split()) == " ".join(note.split())   # whole
        from src.brand_system import label_floor_px
        floor = max(label_floor_px(*size), 30)        # the note's own floor: 30 px on both canvases
        texts = {e["text"] for e in rows}
        for pl in plan_only["_plates"]:
            if pl.text in texts:
                assert pl.font.size >= floor - 0.5, (pl.text, pl.font.size, floor)
        # one unit with its figure (lander, cycle 2): the note's first row directly under the figure, within one row
        # gap, every row aligned to the figure's edge (left for a block going right, right for the free-side block)
        fig = _el(rec, "ref_label")["box"]
        boxes = sorted((e["box"] for e in rows), key=lambda b: b[1])
        cap = boxes[0][3] - boxes[0][1]
        assert 0 <= boxes[0][1] - fig[3] <= 1.6 * cap, (fig, boxes[0])
        edge = 2 if got["form"] == "free side block" else 0
        for b in boxes:
            assert abs(b[edge] - fig[edge]) <= 4, (got["form"], fig, b)
    else:
        assert got["reason"], got


def test_the_referent_note_keeps_off_the_landmarks_drawn_pixels():
    m = graphics.landmark_mask(TALL, "Statue of Liberty (ground to torch)", 600, 900, 400)
    assert m.any() and not graphics.off_mask(m, (590, 700, 610, 720))     # inside the statue's body
    assert graphics.off_mask(m, (100, 100, 200, 150))                       # open air
