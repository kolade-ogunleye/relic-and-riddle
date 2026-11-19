"""Design-freeze cycle 0 round 7, lane TRUTH: the figure rules (src/brand_system.py "Figures", "Place labels",
"Label floor"), built from the cycle 0 preview panel's measured cases (C:/tmp/qf-handoff/queue/CYCLE0.preview-panel-1.md):

- F11 zero datum: the Wieliczka scale's "0 FT" sat 28 px (9:16) / about 20 px (16:9) under its ground line, which
  reads as about 78 ft on a 36 px-per-100-ft ruler. Every ruler or axis label is centred on its tick, a zero on its
  datum, and the critic (graphics.zero_datum_faults) fails a layout record where one is not.
- F12 unit lead: a metre fact (Wieliczka, 327 m) led "ABOUT 1,073 FT DEEP" with a 0-1,000 FT ruler.
- F13 figure face (operator ruling 3): a stated quantity in the display face in gold, at rest too ("36" stepped back
  to stone beside "13,000" in gold; the scale's "327 M" was Plex Mono in stone).
- F14 place labels: the map's site name in Anton on a glow, the route's stops in Montserrat 800: one role.
- F16 label floor: 9:16 graphic labels of 14-20 px cap; nothing under 27 px on 1080 wide.
- F25 unit joiner: "0 M" set with the mono face's whole word space.

Synthetic inputs only (CI has no output/). Every render here is plan-only: a 0.1 s clip whose frames are composed
but never encoded, so the layout and its records are the ones a real render writes.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from src import brand, brand_system, figures, graphics, typography

ROOT = Path(__file__).resolve().parents[1]
TALL, WIDE = (1080, 1920), (1920, 1080)

# The real Wieliczka scale fact (output/run_wieliczka_salt_mine_1792974672/script_metadata.json), as a literal.
WIELICZKA = {"value": 327.0, "unit": "m", "dimension": "depth", "subject": "Wieliczka Salt Mine", "approximate": True}
DERINKUYU = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
KAILASA = {"value": 32.0, "unit": "m", "dimension": "height", "subject": "Kailasa temple monolith", "approximate": True}
MONOLITH = {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa temple", "approximate": False}
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables & wine presses"},
                      {"depth_ft": 35.0, "label": "Living quarters & halls"},
                      {"depth_ft": 140.0, "label": "Byzantine church"},
                      {"depth_ft": 280.0, "label": "Deepest wells"}]}
TWO_CARDS = [{"value": "36", "label": "Chambers carved in salt"}, {"value": "13,000", "label": "Visitors a day"}]


@pytest.fixture
def plan_only(monkeypatch):
    """Renders composed but never encoded (0.1 s, frames consumed); every TextPlate made is recorded."""
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "record_nontext", lambda *a, **k: None)

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
    return made


def _record(out: Path) -> dict:
    return json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))


def _family(font) -> str:
    return font.getname()[0]


# ------------------------------------------------------------------------------------------------ F11 zero datum

def test_the_critic_fails_the_panels_measured_offsets_and_passes_a_centred_zero():
    """The panel's cases as records: the 9:16 "0 FT" ink centre 28.5 px under the line, 16:9 20.5 px under."""
    for box, datum in (([203, 576, 286, 600], 559.5), ([240, 199, 310, 219], 188.5)):
        rec = {"rulers": [{"name": "ruler0", "text": "0 FT", "value": 0.0, "datum": datum, "box": box, "line": []}]}
        faults = graphics.zero_datum_faults(rec)
        assert faults and "off its datum" in faults[0]
    centred = {"rulers": [{"name": "ruler0", "text": "0 M", "value": 0.0, "datum": 573.5, "box": [247, 561, 291, 585],
                           "line": [[120.0, 239.0], [299.0, 820.0]]}]}
    assert graphics.zero_datum_faults(centred) == []
    through = dict(centred["rulers"][0], line=[[120.0, 820.0]])               # the datum line through its ink
    assert "runs through" in graphics.zero_datum_faults({"rulers": [through]})[0]
    assert graphics.zero_datum_faults({"elements": []}) == []                 # no rulers, nothing to judge


@pytest.mark.parametrize("fact", [WIELICZKA, DERINKUYU, KAILASA, MONOLITH], ids=["m-depth", "ft-depth", "m-height",
                                                                              "ft-height"])
@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_every_scale_ruler_is_on_its_tick_and_its_zero_on_the_datum(tmp_path, plan_only, fact, size):
    out = graphics.render_scale(fact, tmp_path / "scale.mp4", *size)
    rec = _record(out)
    assert graphics.zero_datum_faults(rec) == []
    rulers = rec["rulers"]
    assert rulers and rulers[0]["value"] == 0 and rulers[0]["line"], "the zero is drawn and its datum line recorded"
    for r in rulers:                                         # every ruler figure centred on its own tick
        assert abs((r["box"][1] + r["box"][3]) / 2 - r["datum"]) <= 2.0, r
    # instrumentation: every ruler figure on the clip went through ruler_label (none bypasses the rule)
    assert {e["name"] for e in rec["elements"] if e["name"].startswith("ruler")} == {r["name"] for r in rulers}


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_the_cross_section_axis_zero_is_on_the_surface_line(tmp_path, plan_only, size):
    out = graphics.render_cross_section(SECTION, tmp_path / "section.mp4", *size)
    rec = _record(out)
    assert rec["rulers"][0]["text"] == "0 FT" and graphics.zero_datum_faults(rec) == []
    for r in rec["rulers"]:
        assert abs((r["box"][1] + r["box"][3]) / 2 - r["datum"]) <= 2.0, r


def test_the_real_wieliczka_offset_before_and_after_is_the_panels_case():
    """Measured on the real fact (read-only, outside CI) by the lane's script: 9:16 +28.5 px and 16:9 +20.5 px before,
    -0.5 px after. Here: the same fact's layout, which the parametrised test above renders, holds the rule."""
    assert graphics.lead_unit(WIELICZKA) == "M"


# ------------------------------------------------------------------------------------------------- F12 unit lead

def test_the_signed_facts_unit_leads():
    assert graphics.scale_figure(WIELICZKA, 327 * graphics.FT_PER_M, True) == ("ABOUT 327 M DEEP", "1,073 FT")
    assert graphics.scale_figure(DERINKUYU, 280.0, True) == ("ABOUT 280 FT DEEP", "85 M")
    assert graphics.scale_figure(KAILASA, 32 * graphics.FT_PER_M, False) == ("ABOUT 32 M TALL", "105 FT")
    # the rulers count the lead unit; the floor mark is hedged and in the lead unit
    marks = graphics._depth_ruler(327 * graphics.FT_PER_M, "ABOUT ", "M")
    assert [t for _ft, t, _f in marks] == ["0 M", "100", "200", "300", "ABOUT 327 M"]
    assert marks[1][0] == pytest.approx(100 * graphics.FT_PER_M)            # placed at its true depth in feet
    assert [t for _ft, t in graphics.height_ruler(305.0, "M")] == ["0 M", "50"]
    # the line of meaning in the same unit (the landmark's sourced figure converted, never more precise)
    lines = graphics.scale_meaning_lines(WIELICZKA, 327 * graphics.FT_PER_M, "Great Pyramid (original)",
                                         480.6430446, True)
    assert lines == ["The Great Pyramid would vanish:", "its top about 181 m below the surface."]
    assert "ft" not in " ".join(lines)


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_a_metre_fact_draws_metres_first_everywhere(tmp_path, plan_only, size):
    rec = _record(graphics.render_scale(WIELICZKA, tmp_path / "scale.mp4", *size))
    texts = {e["name"]: e["text"] for e in rec["elements"]}
    assert texts["figure"] == "ABOUT 327 M DEEP" and texts["figure_metric"] == "1,073 FT"
    assert texts["ruler0"] == "0 M" and "FT" not in " ".join(t for n, t in texts.items() if n.startswith("ruler"))


# ---------------------------------------------------------------------------------------------- F13 figure face

def _plates_with(made, text):
    return [p for p in made if getattr(p, "text", None) == text]


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_the_scale_figure_and_its_conversion_are_the_figure_face_in_gold(tmp_path, plan_only, size):
    graphics.render_scale(WIELICZKA, tmp_path / "scale.mp4", *size)
    figure_family = graphics.figure_font(40).getname()[0]
    # Round 8 (operator ruling 8): the number and its unit gold, the hedge and the dimension word bone; one face
    for text in ("327 M", "1,073 FT"):
        plates = _plates_with(plan_only, text)
        assert plates and all(_family(p.font) == figure_family and p.fill == tuple(brand.GOLD) for p in plates), text
    for text in ("ABOUT", "DEEP"):
        plates = _plates_with(plan_only, text)
        assert plates and all(_family(p.font) == figure_family and p.fill == tuple(brand.BONE) for p in plates), text
    conv = _plates_with(plan_only, "1,073 FT")[0].font.size
    lead = _plates_with(plan_only, "327 M")[0].font.size
    assert conv <= brand_system.tok("figure.conversion") * lead + 1
    data_family = graphics.data_font(30).getname()[0]
    assert {_family(p.font) for p in plan_only if p.text in ("0", "M", "100", "200", "300")} == {data_family}


def test_two_number_cards_keep_both_figures_gold_at_rest(tmp_path, plan_only):
    rec = _record(graphics.render_stat_cards(TWO_CARDS, tmp_path / "n.mp4", width=1080, height=1920))
    figure_family = graphics.figure_font(40).getname()[0]
    for text in ("3", "6", "1", ",", "0"):
        assert all(p.fill == tuple(brand.GOLD) and _family(p.font) == figure_family
                   for p in _plates_with(plan_only, text)), text
    figures = [e for e in rec["elements"] if e["name"] == "figure"]
    assert [e["text"] for e in figures] == ["36", "13,000"]
    # the first card's words step back; its figure does not
    assert any(p.fill == tuple(brand.STONE_LT) for p in plan_only if "CHAMBERS" in (getattr(p, "text", "") or ""))


def test_a_number_cards_conversion_is_the_figure_face_in_gold(tmp_path, plan_only):
    graphics.render_stat_cards([{"value": "280 FT (85 M)", "label": "Depth"}], tmp_path / "n.mp4", width=1080,
                               height=1920)
    sec = _plates_with(plan_only, "85 M")
    assert sec and all(p.fill == tuple(brand.GOLD) and _family(p.font) == graphics.figure_font(40).getname()[0]
                       for p in sec)


@pytest.mark.parametrize("size", [TALL, WIDE], ids=["9x16", "16x9"])
def test_the_cross_sections_depths_and_metres_stay_gold(tmp_path, plan_only, size):
    graphics.render_cross_section(SECTION, tmp_path / "section.mp4", *size)
    # the metres by the one conversion rule (SYS-R8-1: whole metres drew "5 M" for 15 ft, outside the gate's tolerance)
    metres = [f"{figures.converted_number(lv['depth_ft'], 'FT', 'M')} M" for lv in SECTION["levels"]]
    for text in ("15 FT", "35 FT", "140 FT", "280 FT", *metres):
        plates = _plates_with(plan_only, text)
        assert plates and all(p.fill == tuple(brand.GOLD) for p in plates), text


def test_a_timeline_year_stays_gold_when_its_event_steps_back(tmp_path, plan_only):
    events = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
              {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
              {"year": 1912, "label": "Italy takes the Dodecanese"}]
    graphics.render_timeline(events, tmp_path / "t.mp4", width=1080, height=1920)
    for year in ("1300", "1522", "1912"):
        assert all(p.fill == tuple(brand.GOLD) for p in _plates_with(plan_only, year)), year


def test_the_figure_tokens_drive_the_figures():
    with brand.use(brand.DEFAULT_BRAND.derive("teal figures", system={"figure.colour": "TEAL", "figure.face": "data"})):
        assert graphics.figure_fill() == tuple(brand.TEAL)
        assert graphics.figure_font(40).getname()[0] == graphics.data_font(40).getname()[0]
    assert graphics.figure_fill() == tuple(brand.GOLD)


def test_no_graphic_sets_a_figure_outside_the_figure_face():
    """AST: brand.DISPLAY_FONT appears only where a graphic sets a heading or a unit word (the cutaway's title, a
    number card's unit), never a figure; figures go through figure_font / figure_family."""
    tree = ast.parse((ROOT / "src" / "graphics.py").read_text(encoding="utf-8"))
    users = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and any(isinstance(n, ast.Attribute) and ast.unparse(n) == "brand.DISPLAY_FONT"
                                                   for n in ast.walk(fn)):
            users.add(fn.name)
    assert users <= {"_render_cross_section", "_stat_layout"}, users


# ------------------------------------------------------------------------------------- F25 unit joiner

def test_a_drawn_number_is_joined_to_its_unit_by_the_token_gap():
    ground = graphics.Image.new("RGB", (600, 200), brand.SEA)
    font = graphics.data_font(34)
    plate = graphics.ruler_label(ground, 400, 100, "0 FT", font, brand.STONE_LT)
    num, unit = plate.plates
    gap = unit.box[0] - num.box[2]
    assert abs(gap - brand_system.tok("data.unit_gap") * font.size) <= 1.5
    assert gap < 0.5 * font.getlength(" ")                                  # not the mono face's word space
    assert plate.box[2] in (399, 400, 401)                                  # its ink's right edge on x
    assert abs((plate.box[1] + plate.box[3]) / 2 - 100) <= 1                # its figures centred on the tick
    with brand.use(brand.DEFAULT_BRAND.derive("wide joiner", system={"data.unit_gap": 0.5})):
        wide = graphics.ruler_label(ground, 400, 100, "0 FT", font, brand.STONE_LT)
        assert wide.plates[1].box[0] - wide.plates[0].box[2] > gap + 5


# ----------------------------------------------------------------------------------- F14 place labels

def test_one_place_label_role_for_the_map_and_the_route():
    # cycle 0 round 8 (operator ruling 1): the story's own site is one step up, the same face (was place.px)
    _lines, font = graphics.marker_label_lines("Catherine Palace", *TALL)
    assert _family(font) == brand.TEXT_FONT
    assert font.size == round(brand_system.tok("place.px") * brand_system.tok("place.site_step"))
    assert graphics.place_steps(*TALL) == [34, 32, 30, 28] == list(graphics.ROUTE_NAME_STEPS)
    assert graphics.place_steps(*WIDE)[0] == 34
    with brand.use(brand.DEFAULT_BRAND.derive("bigger places", system={"place.px": 40})):
        assert graphics.marker_label_lines("Catherine Palace", *TALL)[1].size == round(40 * 1.4)
        assert graphics.place_steps(*TALL)[0] == 40


@pytest.mark.parametrize("ground", [(14, 18, 24), (105, 105, 105), (135, 135, 135), (200, 190, 170)],
                         ids=["dark", "mid-dark", "mid-light", "bright"])
def test_a_place_labels_halo_never_passes_the_system_cap(ground):
    rest = graphics.Image.new("RGB", (900, 300), ground)
    plate = graphics.place_plate(rest, (100, 150), "CATHERINE PALACE", graphics.place_font(34), "ls")
    assert plate.halo <= brand_system.tok("place.halo_max") + 1e-9
    assert plate.contrast >= brand_system.tok("contrast.place"), (ground, plate.fill, plate.contrast)


def test_on_the_mid_grey_dead_band_the_label_takes_the_held_plate():
    """A flat 120-luma ground: bone at 0.16 measures 4.31, INK 4.38. Cycle 0 round 8 (operator ruling 2): the
    halo exception (bone at the least deeper halo, a cloud) is retired; the label takes a held INK plate, bone on it,
    no halo. Updated from the round-7 exception for that reason."""
    rest = graphics.Image.new("RGB", (900, 300), (120, 120, 120))
    plate = graphics.place_plate(rest, (100, 150), "CATHERINE PALACE", graphics.place_font(34), "ls")
    assert plate.variant == "plate" and plate.halo == 0.0 and plate.fill == tuple(brand.BONE)
    assert plate.measure(plate.draw(rest)) >= brand_system.tok("contrast.place")


def test_no_place_label_is_planned_outside_the_role():
    """AST: render_route and the marker's plates plan their names only through place_plates (one variant per label, no other halo or face)."""
    tree = ast.parse((ROOT / "src" / "graphics.py").read_text(encoding="utf-8"))
    for name in ("render_route", "label_plates_for"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = [ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)]
        assert "place_plates" in calls, name
        named = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and ast.unparse(c.func) == "get_font"
                 and any(kw.arg == "weight" and ast.unparse(kw.value) == "800" for kw in c.keywords)]
        assert not named, name


# ------------------------------------------------------------------------------------- F16 the 9:16 floor

def test_the_floor_token():
    assert graphics.label_floor(*TALL) == 27 == brand_system.TYPE_FLOOR["tall"]["label"]
    assert graphics.label_floor(*WIDE) == 0
    assert graphics.SPAN_MIN_PX == 27


SMALL_CARDS = [{"value": "about 1,200,000 tonnes (1.1 million metric tonnes)", "label": "Salt carved out of the "
                "mine over seven centuries of work"},
               {"value": "about 245 km (152 mi)", "label": "Galleries and passages on nine levels below ground"}]


@pytest.mark.parametrize("job", ["scale", "scale_height", "numbers", "numbers_small", "section", "timeline"])
def test_no_9x16_graphic_label_is_under_the_floor(tmp_path, plan_only, job):
    out = tmp_path / f"{job}.mp4"
    {"scale": lambda: graphics.render_scale(WIELICZKA, out, *TALL),
     "scale_height": lambda: graphics.render_scale(KAILASA, out, *TALL),
     "numbers": lambda: graphics.render_stat_cards(TWO_CARDS, out, width=1080, height=1920),
     "numbers_small": lambda: graphics.render_stat_cards(SMALL_CARDS, out, width=1080, height=1920),
     "section": lambda: graphics.render_cross_section(SECTION, out, *TALL),
     "timeline": lambda: graphics.render_timeline(
         [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
          {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
          {"year": 1523, "label": "The knights sail for Malta"},
          {"year": 1912, "label": "Italy takes the Dodecanese"},
          {"year": 1988, "label": "The Medieval City of Rhodes is listed"}], out, width=1080, height=1920)}[job]()
    drawn = {p.text: p.font.size for p in plan_only if not p.empty}
    under = {t: s for t, s in drawn.items() if s < 27}
    assert drawn and not under, under


@pytest.mark.parametrize("job", ["map", "route"])
def test_no_9x16_map_or_route_label_is_under_the_floor(tmp_path, monkeypatch, plan_only, job):
    """Drawn on the observer's synthetic world (tests/surface_registry._synthetic_geo: never skipped for the missing
    Natural Earth files, never the network), so it runs the same in CI."""
    from tests.surface_registry import GRAPHIC_INPUTS, _synthetic_geo
    for mod, name, value in _synthetic_geo():
        monkeypatch.setattr(mod, name, value)
    GRAPHIC_INPUTS[f"render_{job}"](None, tmp_path / f"{job}.mp4", TALL)
    drawn = {p.text: p.font.size for p in plan_only if not p.empty}
    under = {t: s for t, s in drawn.items() if s < 27}
    assert drawn and not under, under
