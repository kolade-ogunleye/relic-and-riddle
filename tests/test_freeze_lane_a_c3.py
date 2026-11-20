"""Design-freeze lane A, cycle 3: invariants (gates met, inside the safe box, drawn text == signed text, bounded
steps), never pinned measurements."""
from __future__ import annotations

import pytest
from PIL import Image

from src import brand, brand_system, graphics, safe_zones, then_now
from src.refusal import LayoutError
from src.typography import get_font


def _rows_ok(rows: list[str]) -> bool:
    words = " ".join(rows).split()
    cuts, at = set(), 0
    for ln in rows[:-1]:
        at += len(ln.split())
        cuts.add(at)
    return not graphics.label_never_cuts(words, cuts)


@pytest.mark.parametrize("text", ["AMBER IN COMPLETED ROOM", "DISASSEMBLED BY GERMAN TROOPS",
                                  "ITALY TAKES THE DODECANESE", "BUILDS A ROYAL ESTATE", "GROUND TO TORCH TIP"])
@pytest.mark.parametrize("share", [0.45, 0.62, 0.8])
def test_label_wrappers_keep_the_heading_engines_hard_rule(text, share):
    """A-S2-2: the strict wrap never ends a row on a never-end word or inside a quantity; where it returns a break,
    the words are unchanged."""
    font = get_font(brand.TEXT_FONT, 46, weight=800)
    width = graphics._tracked_w(text, font, 3) * share
    got = graphics.tracked_wrap(text, font, width, 3, strict=True, max_lines=4)
    if got is not None:
        assert " ".join(got).split() == text.split()
        assert _rows_ok(got)
    fitted = graphics.tracked_wrap(text, font, width * 1.25, 3, lines=2) or [text]
    ruled = graphics.ruled_rows(fitted, font, width * 1.25, 3)
    assert " ".join(ruled).split() == text.split()
    if graphics.balanced_label_rows(fitted, font, width * 1.25, 3)[1]:
        assert _rows_ok(ruled)


def test_probe_steps_size_before_a_hard_cut():
    """A size step is tried before a row may end on a never-end word (BUILDS A / ROYAL ESTATE)."""
    import PIL.ImageDraw as D
    font = get_font(brand.TEXT_FONT, 46, weight=800)
    text = "BUILDS A ROYAL ESTATE"
    width = graphics._tracked_w(text, font, 3) * 0.62
    lines, f, short = graphics._probe_tracked(D.Draw(Image.new("L", (8, 8))), text, brand.TEXT_FONT, 46, width, 2, 30,
                                              3, weight=800)
    assert not short and len(lines) <= 2 and _rows_ok(lines) and f.size <= 46


def test_stop_words_are_the_systems_list():
    system = {w.upper() for w in brand_system.LINE_END_NEVER | brand_system.LINE_END_LAST_RESORT}
    assert graphics.TALL_STOP_WORDS == system
    assert graphics.label_hard_fault(["AMBER IN", "COMPLETED ROOM"])
    assert not graphics.label_hard_fault(["AMBER", "IN COMPLETED ROOM"])


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_tall_text_column_is_the_inset_reel_box(size):
    """MG-1: a 9:16 graphic's text column is the Reel box inset by the system's margin token."""
    w, h = size
    tb = graphics.get_text_box(w, h)
    col = safe_zones.text_box("reel", w, h)
    assert (tb[0], tb[2]) == (col[0], col[2])
    assert graphics.inset_x0(w, h) >= col[0]


@pytest.mark.parametrize("lat", [10.0, 45.0, 60.0])
def test_tall_scale_bar_stays_inside_the_text_column(lat):
    """A-S1-2: the bar's ticks, casing and figures inside the 9:16 text column, wherever it is placed."""
    w, h = 1080, 1920
    bar = graphics.scale_bar(lat, 40.0, "", bar_range=graphics.scale_range(w, h))
    lay = graphics.place_scale_bar(bar, w, h, [(80, 900, 400, 1060)])
    tb = graphics.get_text_box(w, h)
    box = graphics.scale_bar_box(bar, lay.x, lay.y, graphics.scale_label_font(w, h), lay.direction)
    assert tb[0] <= box[0] and box[2] <= tb[2]


def test_scale_bar_figures_in_the_data_face():
    """A-S2-3 (ruling 3): rulers, axes and scale-bar figures in the data face."""
    assert graphics.scale_label_font(1080, 1920).font.family == graphics.data_font(30).font.family


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)])
def test_data_floor_on_both_orientations(size):
    w, h = size
    need = brand_system.label_floor_px(w, h, "data_label")
    assert graphics.data_floored(10, w, h) >= need - 1e-6
    assert graphics.scale_label_font(w, h).size >= need - 1e-6


@pytest.mark.parametrize("feet", [20.0, 30.0, 60.0, 100.0, 170.0])
def test_twenty_to_hundred_feet_has_a_sourced_reference(feet):
    """Stress 1 (real Memnon, 60 ft): a subject of 20-100 ft has a landmark within 1/3x-3x, from a sourced row."""
    ref = graphics.pick_reference(feet)
    assert ref is not None
    row = next(r for r in graphics.references() if r["label"] == ref[0])
    assert row["source_url"].startswith("https://") and row["quote"] and row["feet"] > 0
    assert 1 / 3 <= feet / row["feet"] <= 3


def test_every_reference_has_a_drawing():
    for r in graphics.references():
        kind = graphics._landmark_kind(r["label"])
        assert kind in graphics._LANDMARK_DRAWINGS and kind in graphics.SILHOUETTE_WIDTH


@pytest.mark.parametrize("fact", [{"value": 1000.0, "unit": "m", "dimension": "height", "subject": "X"},
                                  {"value": 0.0, "unit": "ft", "dimension": "height", "subject": "X"}])
def test_scale_without_a_reference_is_a_recorded_decline(fact, tmp_path):
    """Stress 1: never a silent None; the refusal names the rule."""
    with pytest.raises(LayoutError) as e:
        graphics.render_scale(fact, tmp_path / "s.mp4", width=1080, height=1920)
    assert e.value.code in ("scale_no_reference", "scale_not_positive") and "declined" in e.value.reason


@pytest.mark.parametrize("u", [0.0, 0.2, 0.5, 0.8, 1.0])
def test_cover_window_across_stays_inside_the_photo(u):
    """A-S2-1: the shared window searched across is always inside the photo and keeps the cover crop's size."""
    size, big = (3000, 2000), (1123, 1997)
    box = then_now._cover_window(size, big, 1.2, 0.4, u)
    centred = then_now._cover_window(size, big, 1.2, 0.4)
    assert 0 <= box[0] and box[2] <= size[0] + 1e-6 and 0 <= box[1] and box[3] <= size[1] + 1e-6
    assert abs((box[2] - box[0]) - (centred[2] - centred[0])) < 1e-6


def test_span_label_takes_a_lighter_tier_to_reach_the_gate():
    """Stress 12: a span planned under the text gate in its tier is set in the lighter tier that reaches it."""
    ground = Image.new("RGB", (600, 200), (250, 250, 248))
    font = get_font(brand.TEXT_FONT, 28, weight=graphics.TALL_SPAN_WEIGHT)
    plate = graphics._plan_gated(ground, (40, 120), "ABOUT 6,000 YEARS LATER", font,
                                 (brand.STONE, brand.STONE_LT, brand.BONE), "ls", track=2)
    first = graphics._plan_thin(ground, (40, 120), "ABOUT 6,000 YEARS LATER", font, brand.STONE, "ls", halo_px=None,
                                track=2)
    assert plate.contrast is None or plate.contrast >= (first.contrast or 0) - 1e-6


def test_two_number_cards_step_the_first_back_by_design(tmp_path, monkeypatch):
    """A-S2-5, lander ruling 3 (cycle 3): the first of two number cards steps back when the second lands (the F13
    hierarchy, the timeline's grammar): its words dim to STONE_LT while its figure and unit stay gold. Two label
    colours on the last frame are this intended step-back, not two treatments of one role."""
    twins: list[tuple[str, tuple]] = []
    real = graphics._twin

    def spy(ground, plate, fill):
        twins.append((plate.text, tuple(fill)))
        return real(ground, plate, fill)
    monkeypatch.setattr(graphics, "_twin", spy)
    monkeypatch.setattr(graphics, "CLIP_SECONDS", 0.2)
    cards = [{"value": "36 hours", "label": "Time to disassemble the room"},
             {"value": "13,000 lbs", "label": "Amber in completed room"}]
    with graphics.poster_only():
        graphics.render_stat_cards(cards, tmp_path / "n.mp4", width=1080, height=1920)
    dimmed = dict(twins)
    label_rows = [t for t in dimmed if "TIME" in t or "THE ROOM" in t]      # the first card's label rows
    assert label_rows and all(dimmed[t] == tuple(brand.STONE_LT) for t in label_rows)
    figure_fill = tuple(graphics.figure_fill())
    assert any(fill == figure_fill for _t, fill in twins)          # the stepped-back card's quantity stays gold
    assert not any("AMBER" in t or "13" in t for t in dimmed)       # the second card never steps back


def test_obelisk_upper_face_keeps_the_signed_length():
    """MG-3: the obelisk's upper face is drawn straight up; its ink never runs past the signed length's end."""
    from PIL import ImageDraw
    im = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
    graphics._obelisk_lying(ImageDraw.Draw(im), 100, 600, 180, 600 * graphics.SUBJECT_LYING_ASPECT)
    bb = im.getbbox()
    assert bb[0] >= 100 and bb[2] <= 601 and bb[3] <= 181
    assert 180 - bb[1] <= 500 * graphics.SUBJECT_LYING_ASPECT + 1
