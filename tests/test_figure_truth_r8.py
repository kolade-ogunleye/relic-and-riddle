"""Design-freeze cycle 0 round 8, lane TRUTH (QF-147, the diagram slides, operator ruling 8), built from the official
panel's measured cases (C:/tmp/qf-handoff/queue/CYCLE0.panel-official-1.md, consensus 1, 2, 7) and the production
sample cases of QF-147 (counts and phrasings only, as synthetic inputs; CI has no output/):

- Gol Gumbaz 1793797514 s5 drew "11" for "11.5 feet (3.5 meters)";
- Derinkuyu 1793286295 s4 drew "18 LEVELS" for "eight to 18 levels";
- Aswan 1793624688 s2 drew "137 FEET" for "between roughly 137 feet (41.75 m) and 138 feet (42 m)";
- Derinkuyu 1793314157 (published) s4 drew "20,000 PEOPLE" for "up to 20,000", s3 "280 FEET" over a body about levels;
- Aswan 1792993374 s4 drew "41" for "between roughly 41.75 and 42 meters";
- plus unseen phrasings: "some 11.5 feet", "nearly 20,000", "eight to 18", "between 137 and 138 feet", a descending
  span ("roughly 145,000 to 29,000 years ago").
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from src import brand, figure_gate, figures, graphics, heading, multi_format, typography

ROOT = Path(__file__).resolve().parents[1]

# (body, the pre-fix extractor's drawn figure, the figure drawn now)
QF147 = [
    ("By one estimate, a massive stone cornice projects outward by 11.5 feet (3.5 meters).", "11",
     "ESTIMATED 11.5 FT"),
    ("Depending on how tiers are surveyed, researchers describe eight to 18 levels.", "18 LEVELS", "8\u201318 LEVELS"),
    ("Surveys place its length between roughly 137 feet (41.75 m) and 138 feet (42 m).", "137 FEET",
     "ROUGHLY 137\u2013138 FT"),
    ("By one estimate, the complex sheltered up to 20,000 people alongside their livestock.", "20,000 PEOPLE",
     "UP TO 20,000 PEOPLE"),
    ("Surveys place the completed length between roughly 41.75 and 42 meters.", "41", "ROUGHLY 41.75\u201342 M"),
]
UNSEEN = [
    ("The cornice juts out some 11.5 feet over the terrace.", "SOME 11.5 FT"),
    ("The city held nearly 20,000 people in a siege.", "NEARLY 20,000 PEOPLE"),
    ("Researchers count eight to 18 levels below the town.", "8\u201318 LEVELS"),
    ("The monolith measures between 137 and 138 feet.", "137\u2013138 FT"),
    ("Tools here date from roughly 145,000 to 29,000 years ago.", "ROUGHLY 145,000\u201329,000 YEARS AGO"),
]
MISMATCH = {"heading": "PLUNGING 280 FEET DOWN",
            "body": "Depending on how tiers are surveyed, researchers record between eight and 18 levels."}


# ------------------------------------------------------------------------------------------- the figure, whole
@pytest.mark.parametrize("body, old, new", QF147, ids=["decimal", "word-span", "between-pair", "hedge", "41"])
def test_the_body_figure_is_parsed_whole_and_the_old_extractions_fail_the_critic(body, old, new):
    fig = figures.stat_figure(body)
    assert fig is not None and fig.drawn() == new
    assert figures.truth_faults(new, [body]) == []
    assert figures.truth_faults(old, [body]), f"the pre-fix figure {old!r} must fail against its body"


@pytest.mark.parametrize("body, new", UNSEEN, ids=["some", "nearly", "eight-to", "between-and", "descending"])
def test_unseen_phrasings_are_drawn_whole(body, new):
    fig = figures.stat_figure(body)
    assert fig is not None and fig.drawn() == new
    assert figures.truth_faults(new, [body]) == []
    # each one part-dropped form fails: no hedge, one end of a span, a truncated decimal
    h, num, unit, tail = figures.split_drawn(new)
    if h:
        assert figures.truth_faults(" ".join(x for x in (num, unit) if x), [body])
    if "\u2013" in num:
        assert figures.truth_faults(f"{num.split(chr(0x2013))[-1]} {unit}", [body])
    if "." in num:
        assert figures.truth_faults(num.split(".")[0], [body])


def test_a_heading_figure_over_a_body_about_something_else_is_never_the_slides_figure():
    fig = figures.stat_figure(MISMATCH["body"])
    assert fig.drawn() == "8\u201318 LEVELS"
    assert figures.truth_faults("280 FEET", [MISMATCH["body"]])
    assert figures.stat_figure("Defenders could seal individual tunnel sections.") is None


def test_ruling_8_gold_scope():
    assert figures.gold_runs("ABOUT 40 KM ACROSS") == [("ABOUT ", False), ("40 KM", True), (" ACROSS", False)]
    assert figures.gold_runs("UP TO 20,000 PEOPLE") == [("UP TO ", False), ("20,000 PEOPLE", True)]
    assert figures.gold_runs("ABOUT 138 FT LONG") == [("ABOUT ", False), ("138 FT", True), (" LONG", False)]
    assert figures.gold_runs("THE 1,200-TON STONE") == [("THE ", False), ("1,200-TON", True), (" STONE", False)]
    # the heading engine: a payoff run's qualifiers step to bone (LEAD), the quantity gold (FIGURE)
    runs = heading.runs("ABOUT 40 KM ACROSS".split(), [], 0)[0]
    assert runs == [("ABOUT", heading.LEAD), ("40 KM", heading.FIGURE), ("ACROSS", heading.LEAD)]


def test_one_lead_unit_per_run():
    assert figures.deck_lead({"scale_fact": {"value": 138.0, "unit": "ft"}}) == "imperial"
    assert figures.deck_lead({"scale_fact": {"value": 327.0, "unit": "m"}}) == "metric"
    assert figures.deck_lead({}) is None
    # a pair leads in the deck's unit
    assert typography.spoken_units("It drops 85 m (280 ft) below.", "", "imperial") == "It drops 280 ft (85 m) below."
    # the far end of a span is never converted alone (it made a false span: "41.75 and 138 feet")
    narr = "It is 42 meters (138 feet) long."
    assert typography.spoken_units("between roughly 41.75 and 42 meters", narr, "imperial") == \
        "between roughly 41.75 and 42 meters"


# ------------------------------------------------------------------------------------------- the stat slide
def _photo(tmp_path: Path) -> Path:
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 1600), (96, 84, 70)).save(p)
    return p


def _stat_slide(tmp_path, body, heading="A FIGURE"):
    p = _photo(tmp_path)
    return multi_format.MultiFormatGenerator()._render_carousel_slide(
        "colossal_stat", p, p, {"heading": heading, "body": body}, 3, 7, {}, "", False, subject="SUBJECT")


@pytest.mark.parametrize("body, new", [(b, n) for b, _o, n in QF147] + UNSEEN,
                         ids=["decimal", "word-span", "between-pair", "hedge", "41", "some", "nearly", "eight-to",
                              "between-and", "descending"])
def test_the_stat_slide_draws_the_body_figure_whole_in_ruling_8_colours(tmp_path, body, new):
    _im, lay = _stat_slide(tmp_path, body)
    assert lay.meta["stat"] == new and lay.meta["stat_figure"]["faults"] == []
    plates = lay.plates["stat"]
    gold, bone = tuple(multi_format.figure_colour()), tuple(brand.BONE)
    hedge, num, unit, tail = figures.split_drawn(new)
    by_text = {" ".join(p.text.split()): tuple(p.fill) for p in plates}
    assert by_text[num] == gold
    if hedge:
        assert by_text[hedge] == bone
    if unit:
        assert any(t.split()[0] == unit.split()[0] and c == gold for t, c in by_text.items())
    if tail:
        assert any(t == tail and c == bone for t, c in by_text.items())
    # the record carries the drawn text whole, and the gate passes it against its own body
    rec = next(e for e in lay.elements if e["name"] == "stat")
    assert figure_gate.figure_faults(rec["text"], figures.parse(body)) == []


def test_a_mismatched_heading_figure_is_not_drawn(tmp_path):
    _im, lay = _stat_slide(tmp_path, MISMATCH["body"], MISMATCH["heading"])
    assert lay.meta["stat"] == "8\u201318 LEVELS"
    _im, lay = _stat_slide(tmp_path, "Defenders could seal individual tunnel sections.", MISMATCH["heading"])
    assert "stat" not in lay.meta and "stat" not in lay.plates
    assert lay.meta["stat_figure"]["reason"] == "the body states no figure"


def test_the_stat_extractor_regex_is_gone():
    src = (ROOT / "src" / "multi_format.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_render_colossal_stat")
    body = ast.unparse(fn)
    assert "re.search" not in body and "range_figure" not in body and "figures.stat_figure" in body


# ------------------------------------------------------------------------------------------- number cards
@pytest.fixture
def plan_only(monkeypatch):
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "record_nontext", lambda *a, **k: None)
    made = []
    init = typography.TextPlate.__init__

    def recording(self, *a, **k):
        init(self, *a, **k)
        made.append(self)
    monkeypatch.setattr(typography.TextPlate, "__init__", recording)
    return made


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
def test_a_number_cards_unit_is_gold_and_its_qualifier_bone(tmp_path, plan_only, size):
    out = graphics.render_stat_cards([{"value": "Est. over 20,000 people", "label": "Sheltered in a siege"}],
                                     tmp_path / "n.mp4", width=size[0], height=size[1])
    rec = json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    names = {e["name"]: e["text"] for e in rec["elements"]}
    assert names["figure"] == "20,000" and names["unit"] == "PEOPLE" and names["prefix"] == "EST. OVER"
    fills = {p.text: tuple(p.fill) for p in plan_only}
    assert fills["PEOPLE"] == tuple(brand.GOLD) and fills["EST. OVER"] == tuple(brand.BONE)


# ------------------------------------------------------------------------------------------- the scale
OBELISK = {"value": 138.0, "unit": "ft", "dimension": "length", "subject": "Unfinished obelisk", "approximate": True}


def test_a_length_is_never_labelled_tall():
    fig, conv = graphics.scale_figure(OBELISK, 138.0, False)
    assert fig == "ABOUT 138 FT LONG" and conv == "42 M"
    lines = graphics.scale_meaning_lines(OBELISK, 138.0, "Statue of Liberty (total)", 305.0, False)
    assert lines[0] == "Unfinished obelisk: about 138 ft long." and "as tall" not in " ".join(lines)


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
def test_the_scales_quantities_are_the_figure_face_and_never_plex_mono(tmp_path, plan_only, size):
    graphics.render_scale(OBELISK, tmp_path / "scale.mp4", *size)
    fam = graphics.figure_font(40).getname()[0]
    data = graphics.data_font(40).getname()[0]
    quantities = [p for p in plan_only if re.search(r"\d", p.text or "") and re.search(r"(FT|M)$", p.text)
                  and not re.fullmatch(r"0 ?(FT|M)|\d[\d,]*", p.text)]
    labels = [p for p in quantities if p.font.getname()[0] == data and p.text not in ("FT", "M")]
    assert not [p.text for p in labels if p.text.startswith("ABOUT")], "a stated quantity in the data face"
    assert any(p.text == "138 FT" and p.font.getname()[0] == fam and tuple(p.fill) == tuple(brand.GOLD)
               for p in plan_only)
    assert not any("TALL" in (p.text or "") for p in plan_only)


# ------------------------------------------------------------------------------------------- one rule, every renderer
def test_every_quantity_renderer_gilds_through_the_one_rule():
    """AST guard: the renderers that gild a stated quantity read src/figures.py (gold_runs / gold_word_indices /
    split_drawn / quantity_plate), never their own figure regex."""
    need = {"src/heading.py": "gold_word_indices", "src/design_cards.py": "gold_runs",
            "src/graphics.py": "gold_runs", "src/multi_format.py": "split_drawn"}
    for path, name in need.items():
        assert name in (ROOT / path).read_text(encoding="utf-8"), path
    src = (ROOT / "src" / "design_cards.py").read_text(encoding="utf-8")
    assert "_FIG_RUN" not in src


# ------------------------------------------------------------------------------------------- the diagram slide
def test_a_stale_poster_is_redrawn_from_the_signed_fact_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    run = tmp_path / "run"
    (run / "graphics").mkdir(parents=True)
    stale = run / "graphics" / "graphic_scale.jpg"
    Image.new("RGB", (1080, 1920), (254, 215, 0)).save(stale)          # an older drawing: no stamp, #FED700
    payload = {"scale_fact": OBELISK}
    got, why = multi_format.current_poster(run, stale, payload)
    assert got is not None and got != stale and multi_format.REDRAWN_DIR in str(got) and "redrawn" in why
    assert graphics.poster_current(got)
    with Image.open(got) as im:
        px = im.convert("RGB").getcolors(1 << 22)
    assert not any(c == (254, 215, 0) for _n, c in px), "no stale #FED700 survives"
    again, why2 = multi_format.current_poster(run, stale, payload)
    assert again == got and "cached" in why2
    # another brand's tokens: another fingerprint, redrawn again
    other = brand.DEFAULT_BRAND.derive("teal figures", system={"figure.colour": "TEAL"})
    with brand.use(other):
        third, _w = multi_format.current_poster(run, stale, payload)
    assert third != got
    # no signed field: no drawing (the slide keeps its photo)
    none, reason = multi_format.current_poster(run, stale, {})
    assert none is None and "signed" in reason


def test_a_current_stored_poster_is_used_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.1)
    monkeypatch.setattr(graphics, "graded_frame", lambda *a, **k: None)
    out = tmp_path / "graphics" / "graphic_scale.mp4"
    with graphics.poster_only():
        graphics.render_scale(OBELISK, out, 1080, 1920)
    poster = out.with_suffix(".jpg")
    assert graphics.poster_current(poster) and not out.exists()       # poster only: no clip encoded
    got, why = multi_format.current_poster(tmp_path, poster, {"scale_fact": OBELISK})
    assert got == poster and "stored" in why


# ------------------------------------------------------------------------------------------- the release gate
def _run(tmp_path, stat_text, extra=()):
    run = tmp_path / "run"
    (run / "carousel").mkdir(parents=True)
    body = QF147[3][0]
    rec = {"carousel_slide_04.jpg": {"elements": [
        {"name": "stat", "text": stat_text, "font": "Anton.ttf", "box": [0, 0, 1, 1]},
        {"name": "body", "text": body, "font": "Montserrat.ttf", "box": [0, 0, 1, 1]},
        *extra], "recipe": {"card": {"heading": "ENGINEERED CAPACITY", "body": body}}}}
    (run / "carousel" / "still_layout.json").write_text(json.dumps(rec), encoding="utf-8")
    payload = {"carousel_slides": [{"heading": "ENGINEERED CAPACITY", "body": body}],
               "stat_cards": [{"value": "Up to 20,000", "label": "Estimated shelter capacity"}],
               "scale_fact": {"value": 280.0, "unit": "ft", "dimension": "depth", "approximate": True},
               "narration_script": "Up to 20,000 people may have sheltered here, some 280 feet down."}
    return run, payload


def test_the_gate_holds_a_run_whose_drawn_figure_is_not_signed_whole(tmp_path):
    run, payload = _run(tmp_path, "20,000 PEOPLE")
    status, detail = figure_gate.release_check(run, payload)
    assert status == "fail"
    assert any("carousel/carousel_slide_04.jpg (stat)" in d and "20,000 PEOPLE" in d and "UP TO" in d for d in detail)


@pytest.mark.parametrize("drawn", ["UP TO 20,000 PEOPLE"])
def test_the_gate_passes_the_whole_figure(tmp_path, drawn):
    run, payload = _run(tmp_path, drawn)
    status, _detail = figure_gate.release_check(run, payload)
    assert status == "pass"


def test_the_gate_judges_every_record_kind_and_holds_an_unrecorded_text(tmp_path):
    run, payload = _run(tmp_path, "UP TO 20,000 PEOPLE")
    (run / "graphics").mkdir()
    side = {"elements": [{"name": "figure", "text": "ABOUT 280 FT DEEP"}, {"name": "figure_metric", "text": "85 M"},
                         {"name": "ruler0", "text": "0 FT"}, {"name": "ruler1", "text": "100"}]}
    (run / "graphics" / "graphic_scale.mp4.contrast.json").write_text(json.dumps(side), encoding="utf-8")
    res = figure_gate.check_run(run, payload)
    assert res["faults"] == [] and res["derived"] == 2 and res["judged"] >= 3    # the conversion "85 M" accepted
    side["elements"][0]["text"] = "280 FT DEEP"                                     # the hedge dropped on the scale
    (run / "graphics" / "graphic_scale.mp4.contrast.json").write_text(json.dumps(side), encoding="utf-8")
    assert any("graphic_scale.mp4 (figure)" in f for f in figure_gate.check_run(run, payload)["faults"])
    run2, payload2 = _run(tmp_path / "b", "UP TO 20,000 PEOPLE", extra=[{"name": "heading", "box": [0, 0, 1, 1]}])
    assert figure_gate.release_check(run2, payload2)[0] == "needs_review"


@pytest.mark.parametrize("body, old, new", QF147, ids=["decimal", "word-span", "between-pair", "hedge", "41"])
@pytest.mark.parametrize("surface", ["cover/thumbnail_cover.jpg", "stories/story_02_reveal.jpg",
                                     "cards/hook.mp4", "graphics/graphic_numbers.mp4"])
def test_the_gate_on_every_figure_bearing_surface(tmp_path, body, old, new, surface):
    """The same drawn figure judged on each kind of record (a cover or Story still, a card or graphic clip)."""
    payload = {"narration_script": body, "carousel_slides": [{"heading": "X", "body": body}]}
    for drawn, ok in ((old, False), (new, True)):
        run = tmp_path / ("ok" if ok else "bad")
        folder, name = surface.split("/")
        (run / ("" if folder == "cover" else folder)).mkdir(parents=True, exist_ok=True)
        el = {"name": "figure", "text": drawn}
        if name.endswith(".jpg"):
            (run / ("" if folder == "cover" else folder) / "still_layout.json").write_text(
                json.dumps({name: {"elements": [{**el, "box": [0, 0, 1, 1]}]}}), encoding="utf-8")
        else:
            (run / folder / f"{name}.contrast.json").write_text(json.dumps({"elements": [el]}), encoding="utf-8")
        status, _ = figure_gate.release_check(run, payload)
        assert status == ("pass" if ok else "fail"), (surface, drawn)


def test_the_release_report_carries_the_gate():
    src = (ROOT / "src" / "release.py").read_text(encoding="utf-8")
    assert '"drawn_figures"' in src and "figure_gate" in src
