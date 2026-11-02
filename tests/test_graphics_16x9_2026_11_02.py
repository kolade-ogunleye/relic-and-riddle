"""The 16:9 explanatory graphics rebuilt from their 9:16 versions (design review round 2, 2026-11-02): the
timeline, the scale comparison, the cross-section, the number cards, the rolling-stone door and the ventilation
section. On the 1920x1080 last frame every recorded word is a payload word (or one of the fixed tags), no two
words collide, type is at least 28 px (sub-labels 22 px), colours are brand tokens, nothing is drawn in YouTube's
timestamp corner and each word measures at least 4.5:1 drawn and after the master's grade. Also the round's
number-card fixes for both orientations: a range written "94 – 104", "MILLION" for millions of years, and a
count-up figure measured as one element (its digits read against each other at 1.1:1)."""
import json
import re
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand, graphics

W, H = 1920, 1080
TIMESTAMP = (1620, 950, 1920, 1080)          # YouTube's timestamp corner
TIMELINE = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
            {"year": 780, "approximate": True, "label": "Expanded against raiders"},
            {"year": 1923, "label": "Abandoned after the population exchange"},
            {"year": 1963, "label": "Rediscovered behind a wall"}]
DEPTH = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
HEIGHT = {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa Temple", "approximate": True}
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables and storage"}, {"depth_ft": 35.0, "label": "Living quarters"},
                      {"depth_ft": 140.0, "label": "Cruciform church"}, {"depth_ft": 280.0, "label": "Deepest wells"}]}
CARDS = [{"value": "94-104M years ago", "label": "Carbonatite rock cooling age"},
         {"value": "40+ km", "label": "Across the rings"}]
DEFENSE = {"title": "Rolling Stone Door Defense Mechanism"}
VENTILATION = {"title": "Subterranean Ventilation & Convection Dynamics"}
FIXED = {"ILLUSTRATIVE", "INSIDE", "OUTSIDE", "APPROX", "DEPTHS", "FT", "M", "ABOUT", "YEARS", "LATER", "C", "BC",
         "AD", "DEEP", "TALL", "MILLION"}
INVENTED = ("SAFE REFUGE", "INVADERS", "DEFENDER", "SURFACE PASSAGE", "DRAWS COOL", "CONVECTION", "GROUND LEVEL",
            "(TOTAL)", "(HEIGHT)")
OFF_BRAND = [(80, 195, 245), (245, 150, 60), (210, 90, 80), (150, 150, 158)]   # the old 16:9 sky blue, amber, red, grey
BRAND = {brand.INK, brand.INK2, brand.BONE, brand.STONE, brand.STONE_LT, brand.GOLD, brand.GOLD_DIM, brand.TEAL,
         brand.CORAL}


def _photo(path: Path) -> Path:
    rng = np.random.default_rng(4)
    arr = np.clip(np.array([196, 150, 64], np.float32) + rng.normal(0, 30, (1200, 1600, 3)), 0, 255)
    Image.fromarray(arr.astype(np.uint8)).save(path)              # gold rock, the hardest ground for gold numerals
    return path


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    """Each 16:9 graphic's recorded words (plates), last frame, sidecar and a few frames, at 4 fps (the same
    timing, fewer frames)."""
    work = tmp_path_factory.mktemp("g169")
    photo = _photo(work / "rock.jpg")
    jobs = {"timeline": lambda o: graphics.render_timeline(TIMELINE, o, width=W, height=H, photo=photo),
            "scale_depth": lambda o: graphics.render_scale(DEPTH, o, width=W, height=H),
            "scale_height": lambda o: graphics.render_scale(HEIGHT, o, width=W, height=H),
            "section": lambda o: graphics.render_cross_section(SECTION, o, width=W, height=H),
            "numbers": lambda o: graphics.render_stat_cards(CARDS, o, photo, width=W, height=H),
            "defense": lambda o: graphics.render_rolling_stone_defense(DEFENSE, o, width=W, height=H),
            "ventilation": lambda o: graphics.render_ventilation_airflow(VENTILATION, o, width=W, height=H)}
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        state: dict = {}
        real_record = graphics.record_contrast

        def fake_encode(frames, path, n, width=W, height=H):
            state["frames"] = [f.convert("RGB") for f in frames]
            return path

        def spy_record(path, frame, plates):
            state["plates"] = [(name, p) for name, p in plates if p is not None and not p.empty]
            return real_record(path, frame, plates)
        mp.setattr(graphics, "VIDEO_FPS", 4)
        mp.setattr(graphics, "_encode", fake_encode)
        mp.setattr(graphics, "record_contrast", spy_record)
        for name, job in jobs.items():
            state.clear()
            clip = job(work / f"{name}.mp4")
            assert clip is not None, name
            rec = json.loads(Path(str(clip) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
            out[name] = {"plates": state["plates"], "frames": state["frames"], "record": rec}
    return out


def _vocabulary(name: str) -> set[str]:
    texts: list[str] = []
    if name == "timeline":
        texts = [e["label"] for e in TIMELINE]
    elif name.startswith("scale"):
        fact = DEPTH if name == "scale_depth" else HEIGHT
        ref = graphics.pick_reference(fact["value"])
        texts = [fact["subject"], *graphics.scale_meaning(fact, fact["value"], ref[0], ref[1], name == "scale_depth")]
    elif name == "section":
        texts = [SECTION["subject"], *[lv["label"] for lv in SECTION["levels"]]]
    elif name == "numbers":
        texts = [c["value"] + " " + c["label"] for c in CARDS]
    elif name == "defense":
        texts = [graphics.diagram_title("defense", DEFENSE), *graphics.diagram_labels("defense", DEFENSE).values()]
    elif name == "ventilation":
        texts = [graphics.diagram_title("ventilation", VENTILATION),
                 *graphics.diagram_labels("ventilation", VENTILATION).values()]
    return {w for t in texts for w in re.findall(r"[A-Z]+", t.upper())} | FIXED


NAMES = ["timeline", "scale_depth", "scale_height", "section", "numbers", "defense", "ventilation"]


@pytest.mark.parametrize("name", NAMES)
def test_only_payload_words_are_drawn(renders, name):
    texts = [p.text for _, p in renders[name]["plates"]]
    assert texts
    vocab = _vocabulary(name)
    for t in texts:
        assert set(re.findall(r"[A-Z]+", t.upper())) <= vocab, (name, t)
        assert not any(bad in t.upper() for bad in INVENTED), (name, t)


@pytest.mark.parametrize("name", NAMES)
def test_no_two_words_collide(renders, name):
    boxes = [(n, p.box) for n, p in renders[name]["plates"]]
    hits = [(a, b) for (a, ba), (b, bb) in combinations(boxes, 2)
            if graphics.LayoutConstraintSolver.boxes_intersect(ba, bb)]
    assert not hits, hits


def test_timeline_events_keep_24_px_apart_and_alternate_about_the_spine(renders):
    plates = renders["timeline"]["plates"]
    blocks = {}
    for n, p in plates:
        m = re.match(r"(year|label)(\d)", n)
        if m:
            b = blocks.setdefault(int(m.group(2)), list(p.box))
            blocks[int(m.group(2))] = [min(b[0], p.box[0]), min(b[1], p.box[1]), max(b[2], p.box[2]), max(b[3], p.box[3])]
    assert len(blocks) == 4
    for a, b in combinations(sorted(blocks), 2):
        assert not graphics.LayoutConstraintSolver.boxes_intersect(tuple(blocks[a]), tuple(blocks[b]), margin=24), (a, b)
    others = [p.box for n, p in plates if n.startswith("gap")]
    for gb in others:
        for b in blocks.values():
            assert not graphics.LayoutConstraintSolver.boxes_intersect(gb, tuple(b), margin=24)
    centres = [(blocks[k][1] + blocks[k][3]) / 2 for k in sorted(blocks)]
    mid = sum(centres) / 4
    assert [c < mid for c in centres] == [True, False, True, False]


@pytest.mark.parametrize("name", NAMES)
def test_type_sizes(renders, name):
    for n, p in renders[name]["plates"]:
        sub = n.startswith(("gap", "ruler", "tag", "approx")) or n.endswith("_metres")
        floor = 22 if sub else 28
        assert p.font.size >= floor, (name, n, p.text, p.font.size)
    if name == "timeline":
        assert all(p.font.size >= 32 for n, p in renders[name]["plates"] if n.startswith("label"))
        assert all(p.font.size >= 86 and p.fill == brand.GOLD for n, p in renders[name]["plates"] if n.startswith("year"))
    if name == "numbers":
        labels = [p for n, p in renders[name]["plates"] if n == "label"]
        assert labels and all(p.font.size >= 40 and p.fill == brand.BONE for p in labels)


@pytest.mark.parametrize("name", NAMES)
def test_brand_colours_only(renders, name):
    assert {tuple(p.fill) for _, p in renders[name]["plates"]} <= BRAND
    arr = np.asarray(renders[name]["frames"][-1]).astype(np.int16)
    for colour in OFF_BRAND:
        near = (np.abs(arr - np.array(colour, np.int16)).max(axis=2) <= 6).sum()
        assert near < 400, (name, colour, int(near))          # antialiased edges only, never a drawn area


@pytest.mark.parametrize("name", NAMES)
def test_youtube_timestamp_corner_is_clear(renders, name):
    for n, p in renders[name]["plates"]:
        assert not graphics.LayoutConstraintSolver.boxes_intersect(p.box, TIMESTAMP), (name, n)
    corner = np.asarray(renders[name]["frames"][-1].convert("L").crop(TIMESTAMP))
    assert int((corner > 150).sum()) == 0, name


@pytest.mark.parametrize("name", NAMES)
def test_every_word_measures_4_5_drawn_and_graded(renders, name):
    rows = renders[name]["record"]["elements"]
    assert rows
    low = [(e["name"], e["text"], e["drawn"], e["graded"]) for e in rows
           if e["drawn"] is None or e["drawn"] < brand.TEXT_CONTRAST
           or (e["graded"] is not None and e["graded"] < brand.TEXT_CONTRAST)]
    assert not low, low


def test_scale_names_the_landmark_once_and_a_height_is_tall(renders):
    depth = [p.text for _, p in renders["scale_depth"]["plates"]]
    height = [p.text for _, p in renders["scale_height"]["plates"]]
    assert any(t.endswith("DEEP") for t in depth) and any(t.endswith("TALL") for t in height)
    assert not any("(" in t and ")" in t and "M)" not in t for t in depth + height)
    # the height rises from the ground: the gold block's ink sits above the bone ground line
    arr = np.asarray(renders["scale_height"]["frames"][-1]).astype(np.int16)
    gold = np.abs(arr - np.array(brand.GOLD, np.int16)).max(axis=2) <= 10
    gold[:, 1000:] = False                                       # the drawing, not the figure's words
    bone = np.abs(arr - np.array(brand.BONE, np.int16)).max(axis=2) <= 10
    ground = max(y for y in range(arr.shape[0]) if bone[y, 300:1000].sum() > 400)
    ys = np.nonzero(gold)[0]
    assert len(ys) > 1000 and ys.max() <= ground + 2 and ys.min() < ground - 100


def test_section_keeps_true_depth_and_runs_the_ruler_past_the_deepest(renders):
    rows = {n: p for n, p in renders["section"]["plates"]}
    rulers = [p.text for n, p in rows.items() if n.startswith("ruler")]
    assert rulers[0] == "0 FT" and "300" in rulers and "250" in rulers
    ys = {p.text: (p.box[1] + p.box[3]) / 2 for n, p in rows.items() if n.startswith("ruler")}
    step = (ys["300"] - ys["0 FT"]) / 300
    assert ys["150"] == pytest.approx(ys["0 FT"] + 150 * step, abs=2)       # a linear axis
    labels = [rows[f"level{k}_label0"].box[0] for k in range(4)]
    assert max(labels) - min(labels) <= 4 and min(labels) > 1000            # one column at the right


def test_vertical_section_ruler_also_reaches_300(tmp_path, monkeypatch):
    seen = []
    real = graphics._plan
    monkeypatch.setattr(graphics, "_plan", lambda im, xy, text, *a, **k: seen.append(text) or real(im, xy, text, *a, **k))
    monkeypatch.setattr(graphics, "_encode", lambda frames, out, n, width=1080, height=1920: None)
    graphics.render_cross_section(SECTION, tmp_path / "v.mp4", width=1080, height=1920)
    assert "300" in seen and "250" in seen


def test_ventilation_air_in_lands_and_holds_before_air_out(renders):
    frames = renders["ventilation"]["frames"]

    def coral(im):
        arr = np.asarray(im).astype(np.int16)
        return int((np.abs(arr - np.array(brand.CORAL, np.int16)).max(axis=2) <= 14).sum())

    def teal(im):
        arr = np.asarray(im).astype(np.int16)
        return int((np.abs(arr - np.array(brand.TEAL, np.int16)).max(axis=2) <= 14).sum())
    at = {round(i / 4, 2): f for i, f in enumerate(frames)}
    words = coral(frames[0])                                     # nothing coral yet (the out label comes late)
    assert teal(at[2.5]) > 2000 and coral(at[2.5]) <= words + 50  # the in-air has landed; the out-air waits
    assert coral(frames[-1]) > coral(at[2.5]) + 2000


def test_figure_text_writes_ranges_and_millions_without_changing_the_figures():
    assert graphics.figure_text("94-104M YEARS AGO") == "94 – 104 MILLION YEARS AGO"
    assert graphics.figure_text("94–104 m years ago") == "94 – 104 MILLION years ago"
    assert graphics.figure_text("1404-1438") == "1404 – 1438"
    assert graphics.figure_text("280 FT (85 M)") == "280 FT (85 M)"           # metres stay metres
    assert graphics.figure_text("5M VISITORS") == "5M VISITORS"
    assert graphics.figure_parts("94 – 104 MILLION YEARS AGO")["num"] == "94 – 104"


def test_number_card_label_and_range_in_16x9(renders):
    texts = [p.text for _, p in renders["numbers"]["plates"]]
    assert "94 – 104" in texts and "MILLION YEARS AGO" in texts


@pytest.mark.parametrize("value", ["1,200,000 TONS", "94-104M years ago"])
def test_vertical_count_up_figure_is_measured_as_one_element_on_gold_rock(tmp_path, monkeypatch, value):
    # 2026-11-02 platform review: the 9:16 card's record gave "0" glyphs 1.06-1.51:1. Each digit was its own plate
    # and its 2-10 px ring took in the gold digit beside it; the figure itself read 5.7:1 on the photo.
    monkeypatch.setattr(graphics, "VIDEO_FPS", 4)
    out = graphics.render_stat_cards([{"value": value, "label": "Of volcanic stone removed by hand"}],
                                     tmp_path / "n.mp4", _photo(tmp_path / "rock.jpg"))
    rec = json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))
    figures = [e for e in rec["elements"] if e["name"] == "figure"]
    assert len(figures) == 1 and figures[0]["text"] == graphics.figure_parts(graphics.figure_text(value).upper())["num"]
    for e in rec["elements"]:
        assert e["drawn"] >= brand.TEXT_CONTRAST, e
        assert e["graded"] is None or e["graded"] >= brand.TEXT_CONTRAST, e
    x0, _, x1, _ = graphics.get_text_box(1080, 1920)
    assert all(x0 <= e["box"][0] and e["box"][2] <= x1 for e in rec["elements"])   # a long unit goes under it
