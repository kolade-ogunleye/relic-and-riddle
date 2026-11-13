"""Round-4 panel changes to the explanatory graphics (2026-11-02): the 9:16 timeline keeps its spans on their
segments with "LATER", pays for room with the years first and keeps compressed gaps honest where it can, draws
circa lowercase, lands its first node inside the cross-fade, drifts its ground and records its marks' non-text
contrast; the scale draws the subject as a monolith, sets its meaning in sentence case with the subject first,
carries a shared ruler and the landmark's note, keeps the 16:9 figure on one line and pushes in over the hold; the
number cards never show a figure other than the final value, draw the range's dash as a rule, step the first of
two cards back, write a million-scale figure as the voice says it and gate gold on gold; the cross-section draws no
full-depth shaft and no landing vaults, pulses the lantern on contact and drifts at the end; every 16:9 graphic
starts on one left edge; the maps carry a context globe, a label on its pin and measured neighbour names, and
the route a destination pin, smaller stops, direction chevrons and leaders to their labels' edges."""
import itertools
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, geo, graphics
from src.reference_facts import reference_note

DERINKUYU = [{"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
             {"year": 780, "approximate": True, "label": "Expanded against raiders"},
             {"year": 1923, "label": "Abandoned after the population exchange"},
             {"year": 1963, "label": "Rediscovered behind a wall"}]
SECTION = {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
           "levels": [{"depth_ft": 15.0, "label": "Stables and storage"}, {"depth_ft": 35.0, "label": "Living quarters"},
                      {"depth_ft": 140.0, "label": "Cruciform church"}, {"depth_ft": 280.0, "label": "Deepest wells"}]}
DEPTH = {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu depth", "approximate": True}
HEIGHT = {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa Temple", "approximate": True}
CARDS = [{"value": "about 1,200,000 tons", "label": "Rock removed by hand"},
         {"value": "94-104M years ago", "label": "Carbonatite rock cooling age"}]
LIBERTY = "Statue of Liberty (total)"


def _render(monkeypatch: pytest.MonkeyPatch, job, fps: int = 4) -> dict[str, Any]:
    """One render at `fps`: its frames, the plates it records and the sidecar it writes (no graded pass)."""
    state: dict[str, Any] = {}
    real_record = graphics.record_contrast
    monkeypatch.setattr(graphics, "VIDEO_FPS", fps)
    monkeypatch.setattr(graphics, "graded_frame", lambda frame, work, vignette=False: None)

    def fake_encode(frames, path, n, width=1080, height=1920):
        state["frames"] = [f.convert("RGB") for f in frames]
        state["path"] = path
        return path

    def spy_record(path, frame, plates):
        state["plates"] = [(name, p) for name, p in plates if p is not None and not p.empty]
        return real_record(path, frame, plates)
    monkeypatch.setattr(graphics, "_encode", fake_encode)
    monkeypatch.setattr(graphics, "record_contrast", spy_record)
    assert job() is not None
    side = Path(str(state["path"]) + graphics.CONTRAST_SIDECAR)
    state["record"] = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
    return state


def _photo(path: Path, rgb=(196, 150, 64)) -> Path:
    rng = np.random.default_rng(4)
    arr = np.clip(np.array(rgb, np.float32) + rng.normal(0, 24, (1200, 1600, 3)), 0, 255)
    Image.fromarray(arr.astype(np.uint8)).save(path)
    return path


def _rows(st) -> dict[str, Any]:
    return dict(st["plates"])


# ============================================================================ timeline
def test_tall_spans_sit_on_their_segments_between_the_entries_and_keep_later(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    rows = _rows(st)
    gaps = {int(n[3:]): p for n, p in st["plates"] if n.startswith("gap")}
    assert set(gaps) == {1, 2, 3}
    for k, p in gaps.items():
        assert p.text.endswith("YEARS LATER"), p.text
        above = max(q.box[3] for n, q in st["plates"] if n == f"label{k - 1}")
        below = rows[f"year{k}"].box[1]
        assert above < p.box[1] and p.box[3] < below, (k, p.text)        # between the two entries it measures
        assert p.box[0] < rows[f"year{k}"].box[2]                        # not after the later year on its baseline


def test_tall_labels_and_spans_keep_their_floors(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    spans = [p for n, p in st["plates"] if n.startswith("gap")]
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX
    assert min(p.font.size for p in spans) >= graphics.SPAN_MIN_PX
    years = [p for n, p in st["plates"] if n.startswith("year") and not n.endswith("circa")]
    assert min(p.font.size for p in years) > max(p.font.size for p in labels)   # the years still dominate


def test_span_helpers_round_4() -> None:
    f = graphics.get_font("Montserrat", graphics.span_font_px(38), weight=600)
    band, air = graphics.span_band(38, f)
    assert air == round(38 * graphics.SPAN_AIR) and band == graphics.cap_height(f) + 2 * air
    assert graphics.span_font_px(20) == graphics.SPAN_MIN_PX == 24    # 2026-11-03 hierarchy spec: 30 -> 24


def test_spacing_reports_whether_compressed_gaps_stay_honest() -> None:
    events = [{"year": -700}, {"year": 780}, {"year": 1923}, {"year": 1963}]
    roomy = graphics.timeline_positions(events, 0, 900, [120, 120, 150])
    tight = graphics.timeline_positions(events, 0, 600, [150, 150, 180])
    assert roomy.honest and roomy.breaks
    steps = [b - a for a, b in zip(roomy.positions, roomy.positions[1:])]
    assert min(steps[0], steps[1]) >= graphics.COMPRESSED_FLOOR * steps[2] - 1
    assert not tight.honest


def test_circa_is_drawn_lowercase_and_smaller_beside_the_year(tmp_path, monkeypatch) -> None:
    assert graphics.year_parts(-700, True) == ("c.", "700 BC")
    assert graphics.year_parts(1963, False) == ("", "1963")
    for size in ((1080, 1920), (1920, 1080)):
        st = _render(monkeypatch, lambda size=size: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", *size))
        rows = _rows(st)
        circa, year = rows["year0_circa"], rows["year0"]
        assert circa.text == "c." and year.text == "700 BC"
        assert circa.font.size < year.font.size and circa.box[2] <= year.box[0]
        assert "year2_circa" not in rows                                   # 1923 is exact


def test_first_landing_is_inside_the_cross_fade_and_the_ground_drifts() -> None:
    sch = graphics.reveal_schedule(4, graphics.CLIP_SECONDS)
    assert sch.at[0] <= 0.03 * graphics.CLIP_SECONDS < 0.4
    g = Image.fromarray(np.random.default_rng(1).integers(0, 255, (1920, 1080, 3), dtype=np.uint8))
    late = graphics.drifted(g, graphics.CLIP_SECONDS)
    assert graphics.drifted(g, 0).tobytes() == g.tobytes()
    assert late.size == g.size and late.tobytes() != graphics.drifted(g, graphics.CLIP_SECONDS - 1.5).tobytes()


def test_timeline_held_tail_is_not_frozen(tmp_path, monkeypatch) -> None:
    photo = _photo(tmp_path / "p.jpg")
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", photo=photo))
    assert st["frames"][-1].tobytes() != st["frames"][-4].tobytes()


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_timeline_records_its_marks_non_text_contrast(tmp_path, monkeypatch, size) -> None:
    photo = _photo(tmp_path / "p.jpg")
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", *size, photo=photo))
    rec = st["record"]
    names = {r["name"] for r in rec["nontext"]}
    assert {"spine", "node0", "node3", "break0", "break1"} <= names
    assert rec["nontext_target"] == graphics.NONTEXT_MIN
    assert rec["nontext_min"] >= graphics.NONTEXT_MIN


def test_spine_steps_up_to_gold_on_a_ground_too_close_to_gold_dim() -> None:
    ground = Image.new("RGB", (200, 200), (96, 76, 30))
    mask = graphics.mark_mask((200, 200), lambda d: d.line([(100, 10), (100, 190)], fill=255, width=6))
    assert graphics.spine_colour(ground, mask) == brand.GOLD
    assert graphics.spine_colour(Image.new("RGB", (200, 200), brand.INK), mask) == brand.GOLD_DIM


def test_wide_timeline_secondary_type_is_larger(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", 1920, 1080))
    # round 13 (coordinator directive 4, spec G): a span that fits on one row at its floor size is set there, not
    # on two rows at 32 px (was >= 32)
    assert min(p.font.size for n, p in st["plates"] if n.startswith("gap")) >= graphics.WIDE_SPAN_MIN_PX
    assert min(p.font.size for n, p in st["plates"] if n.startswith("label")) >= 34


# ============================================================================ scale
def test_scale_meaning_is_sentence_case_with_the_subject_first() -> None:
    lines = graphics.scale_meaning_lines(HEIGHT, 107.0, LIBERTY, 305.0, False)
    assert lines == ["Kailasa Temple: about 107 ft.", "The Statue of Liberty, 305 ft, is about 3 times as tall."]
    taller = dict(HEIGHT, subject="Kailasa Temple monolith height")
    assert graphics.scale_meaning_lines(taller, 457.5, LIBERTY, 305.0, False) == [
        "Kailasa Temple monolith: about 458 ft,", "about 1.5 times as tall as the Statue of Liberty, 305 ft."]
    depth = graphics.scale_meaning_lines(DEPTH, 280.0, LIBERTY, 305.0, True)
    assert depth == ["The Statue of Liberty, 305 ft,", "would show only its top about 25 ft."]
    # the same figures as the capitals version, never more precise
    caps = " ".join(graphics.scale_meaning(HEIGHT, 107.0, LIBERTY, 305.0, False))
    assert "305 FT" in caps and "3 TIMES" in caps


def test_reference_note_is_the_context_first_clause() -> None:
    assert reference_note(LIBERTY) == "GROUND TO TORCH TIP"
    assert reference_note("Great Pyramid (original)") == "ORIGINAL HEIGHT"
    assert reference_note("U.S. adult man (2021-23 mean)") == ""     # an abbreviation is not a clause
    assert reference_note("No such landmark") == ""


def test_height_ruler_marks() -> None:
    assert graphics.height_ruler(305.0) == [(0.0, "0 FT"), (100.0, "100"), (200.0, "200"), (300.0, "300")]
    assert graphics.height_ruler(60.0) == [(0.0, "0 FT"), (50.0, "50")]


def test_monolith_has_a_lit_face_a_shadow_plane_and_carving() -> None:
    im = Image.new("RGB", (300, 400), brand.INK)
    graphics._monolith(ImageDraw.Draw(im, "RGBA"), 50, 250, 380, 300)
    arr = np.asarray(im)
    lit = (np.abs(arr.astype(int) - brand.GOLD).max(-1) < 8).sum()
    shade = (np.abs(arr.astype(int) - brand.GOLD_DIM).max(-1) < 8).sum()
    assert lit > 10_000 and shade > 5_000
    ys = np.where((arr != brand.INK).any(-1).any(1))[0]
    assert ys.min() == 80 and ys.max() == 380                              # exactly its figure's height


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)])
def test_height_carries_the_shared_ruler_and_the_landmark_note(tmp_path, monkeypatch, size) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(HEIGHT, tmp_path / "s.mp4", *size))
    rows = _rows(st)
    marks = {p.text for n, p in st["plates"] if n.startswith("ruler")}
    assert marks >= {"0 FT", "100", "200"} and (size[0] < size[1] or "300" in marks)   # 9:16: 300 meets the heading's air
    note = " ".join(p.text for n, p in st["plates"] if n.startswith("ref_note"))
    assert note == "GROUND TO TORCH TIP"
    assert all(p.font.size >= (graphics.SCALE_RULER_PX - 4 if size[0] < size[1] else 20)
               for n, p in st["plates"] if n.startswith("ref_note"))
    assert all(p.contrast >= brand.TEXT_CONTRAST for _n, p in st["plates"])
    assert rows["meaning0"].text.startswith("Kailasa Temple")


def test_wide_figure_prefers_one_line(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_scale(DEPTH, tmp_path / "s.mp4", 1920, 1080))
    rows = _rows(st)
    assert rows["figure"].text == "ABOUT 280 FT DEEP" and "figure1" not in rows
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, _f = graphics.scale_figure_lines(probe, "ABOUT 280 FT DEEP", 96, 400, 64, 420)
    assert lines == ["ABOUT", "280 FT DEEP"]                              # balanced, no orphan


def test_scale_meaning_lands_on_the_impact_and_the_hold_moves(tmp_path, monkeypatch) -> None:
    assert graphics.SCALE_MEANING_AT < 3.0
    st = _render(monkeypatch, lambda: graphics.render_scale(DEPTH, tmp_path / "s.mp4"))
    f = st["frames"]
    assert f[-1].tobytes() != f[-5].tobytes()                              # no dead hold
    assert graphics.hold_push(graphics.CLIP_SECONDS) == pytest.approx(1.0)        # it ends on the planned layout
    assert graphics.hold_push(2.0) == pytest.approx(1 / (1 + graphics.SCALE_HOLD_PUSH))


# ============================================================================ number cards
def test_no_drawn_frame_shows_a_figure_other_than_the_final_one(tmp_path, monkeypatch) -> None:
    drawn: list[str] = []
    real = graphics._text

    def spy(d, xy, text, font, fill, anchor="la"):
        drawn.append(text)
        return real(d, xy, text, font, fill, anchor)
    monkeypatch.setattr(graphics, "_text", spy)
    _render(monkeypatch, lambda: graphics.render_stat_cards(CARDS, tmp_path / "n.mp4"))
    digits = {t for t in drawn if t.isdigit() or t in ",."}
    assert digits <= set("1,200,000") | set("94104")
    assert "8" not in digits and "6" not in digits and "3" not in digits and "7" not in digits


def test_range_dash_is_a_rule_wider_than_the_hyphen() -> None:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f = graphics.get_font("Anton", 230)
    cells = dict(graphics._figure_cells(probe, "94–104", f))
    assert cells["–"] > probe.textlength("-", font=f) and cells["–"] >= 0.45 * f.size
    x0, y0, x1, y1 = graphics._dash_rule(f, 0, 0)
    assert x1 - x0 == pytest.approx(f.size * graphics.DASH_EM) and y1 < 0 and y1 - y0 < f.size * 0.2
    one, nine = dict(graphics._figure_cells(probe, "1", f))["1"], dict(graphics._figure_cells(probe, "9", f))["9"]
    assert one < nine                                                      # proportional digits


def test_two_cards_step_the_first_back(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_stat_cards(CARDS, tmp_path / "n.mp4", _photo(tmp_path / "p.jpg")))
    rows = st["plates"]
    figs = [p for n, p in rows if n == "figure"]
    assert len(figs) == 2 and tuple(figs[0].fill) == brand.STONE and tuple(figs[1].fill) == brand.GOLD
    assert all(p.contrast >= brand.TEXT_CONTRAST for _n, p in rows)


def test_spoken_scale() -> None:
    assert graphics.spoken_scale("ABOUT 1,200,000 TONS", "they moved 1.2 million tons") == "ABOUT 1.2 MILLION TONS"
    assert graphics.spoken_scale("about 1,200,000 tons", "about 1.2 million tons") == "about 1.2 million tons"
    assert graphics.spoken_scale("ABOUT 1,200,000 TONS", "about 1,200,000 tons") == "ABOUT 1,200,000 TONS"
    assert graphics.spoken_scale("ABOUT 1,250,000 TONS", "1.25 million") == "ABOUT 1,250,000 TONS"   # not round
    assert graphics.spoken_scale("1,200,000", "") == "1,200,000"
    parts = graphics.figure_parts("ABOUT 1.2 MILLION TONS")
    assert parts["num"] == "1.2" and parts["unit"] == "MILLION TONS"


def test_gold_on_gold_gate_desaturates_only_a_warm_bright_band() -> None:
    warm = Image.new("RGB", (200, 400), (214, 160, 70))
    dark = Image.new("RGB", (200, 400), (30, 28, 26))
    out = graphics.stat_hue_gate(warm, 150, 250, (0, 150, 200, 250), feather=40)
    sat = lambda im: float(np.asarray(im.convert("HSV"))[180:220, :, 1].mean())
    assert sat(out) < 0.75 * sat(warm)
    assert graphics.stat_hue_gate(dark, 150, 250, (0, 150, 200, 250)) is dark


def test_wide_single_card_sits_on_the_grid_over_the_timelines_ground(tmp_path, monkeypatch) -> None:
    photo = _photo(tmp_path / "p.jpg")
    st = _render(monkeypatch, lambda: graphics.render_stat_cards(CARDS[:1], tmp_path / "n.mp4", photo, 1920, 1080))
    fig = _rows(st)["figure"]
    assert abs(fig.box[0] - graphics.WIDE_LEFT) <= 12
    assert 0.22 * 1080 <= fig.box[1] <= 0.40 * 1080                        # the cap line near a third down
    lum = lambda im: float(np.asarray(im.convert("L")).mean())
    wide = graphics.stat_backdrop_wide(photo, 1920, 1080)
    assert abs(lum(wide) - lum(graphics.timeline_backdrop(photo, 1920, 1080))) <= 12
    assert lum(wide) < lum(graphics._backdrop(photo, 1920, 1080))
    right, left = np.asarray(wide)[:, 1300:].std(), np.asarray(wide)[:, :300].std()
    assert right > left                                                    # the photo shows on the right, sharp


# ============================================================================ cross-section
def test_section_draws_no_accent_shaft_and_pulses_on_contact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(graphics, "accent", lambda: brand.TEAL)
    st = _render(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "x.mp4"))
    arr = np.asarray(st["frames"][-1]).astype(int)
    teal = (np.abs(arr - np.array(brand.TEAL)).max(-1) < 30).sum()
    assert teal < 50
    assert graphics.contact_pulse(0.09) == pytest.approx(1.4)
    assert graphics.contact_pulse(0.0) == graphics.contact_pulse(0.2) == 1.0
    assert graphics.LABEL_AFTER_CONTACT > 0
    assert st["record"]["nontext_min"] >= graphics.NONTEXT_MIN


def test_section_carves_rooms_only_at_level_floors(monkeypatch) -> None:
    calls: list[float] = []
    real = graphics._vault

    def spy(x0, x1, floor, h, seed, flat=2.4):
        calls.append(floor)
        return real(x0, x1, floor, h, seed, flat)
    monkeypatch.setattr(graphics, "_vault", spy)
    monkeypatch.setattr(graphics, "_encode", lambda frames, path, n, width=1080, height=1920: path)
    monkeypatch.setattr(graphics, "record_contrast", lambda *a, **k: {})
    monkeypatch.setattr(graphics, "record_nontext", lambda *a, **k: None)
    monkeypatch.setattr(graphics, "VIDEO_FPS", 2)
    graphics.render_cross_section(SECTION, Path("/nonexistent/x.mp4"))
    groups: list[list[float]] = []
    for f in sorted(set(calls)):                      # a level's rooms sit within 4 px of its floor
        if groups and f - groups[-1][-1] <= 6:
            groups[-1].append(f)
        else:
            groups.append([f])
    assert len(groups) == len(SECTION["levels"])      # no landing vault at a switchback


def test_section_end_hold_drifts(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "x.mp4", 1920, 1080))
    assert st["frames"][-1].tobytes() != st["frames"][-3].tobytes()


# ============================================================================ 16:9 grid
def test_wide_graphics_share_one_left_edge(tmp_path, monkeypatch) -> None:
    lefts = {}
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", 1920, 1080))
    lefts["timeline"] = min(r["box"][0] for r in st["record"]["elements"]) - 22      # its blocks start past the flag
    st = _render(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "x.mp4", 1920, 1080))
    lefts["section"] = _rows(st)["title0"].box[0]
    st = _render(monkeypatch, lambda: graphics.render_stat_cards(CARDS[:1], tmp_path / "n.mp4", None, 1920, 1080))
    lefts["numbers"] = _rows(st)["figure"].box[0]
    st = _render(monkeypatch, lambda: graphics.render_scale(HEIGHT, tmp_path / "s.mp4", 1920, 1080))
    lefts["scale"] = min(p.box[0] for n, p in st["plates"] if n.startswith("ruler"))
    assert graphics.wide_col(0) == graphics.WIDE_LEFT and graphics.wide_col(12) == graphics.WIDE_RIGHT
    for name, x in lefts.items():
        assert abs(x - graphics.WIDE_LEFT) <= 14, (name, x)
    assert graphics.WIDE_PRIMARY_MIN == 48 and graphics.WIDE_SECONDARY_MIN == 28 and graphics.WIDE_TERTIARY_MIN == 22


# ============================================================================ maps
def test_globe_box_stays_in_the_safe_areas() -> None:
    x0, y0, x1, y1 = graphics.globe_box(1080, 1920)
    rx0, ry0, rx1, _ry1 = graphics.get_text_box(1080, 1920)
    assert rx0 <= x0 and x1 <= rx1 and ry0 <= y0 and y1 <= 540 and x1 - x0 == graphics.GLOBE_PX
    _wx0, wy0, wx1, wy1 = graphics.globe_box(1920, 1080)
    assert wx1 <= graphics.WIDE_TITLE_SAFE[2] and wy0 >= graphics.WIDE_TITLE_SAFE[1] and wy1 < 540


def test_globe_inset_draws_land_home_and_footprint(monkeypatch) -> None:
    def blob(cx, cy, rx, ry):
        return [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]
    turkey = {"names": {"turkey"}, "polygons": [[blob(35, 39, 9, 3.2)]], "display": "Turkey"}
    egypt = {"names": {"egypt"}, "polygons": [[blob(30, 26.5, 5, 4.5)]], "display": "Egypt"}
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "countries", lambda: [turkey, egypt])
    g = graphics.globe_inset(160, 34.7, 38.4, turkey, (30.0, 35.0, 40.0, 42.0))
    arr = np.asarray(g.convert("RGBA")).astype(int)
    seen = lambda c, tol=30: ((np.abs(arr[..., :3] - np.array(c)).max(-1) < tol) & (arr[..., 3] > 200)).sum()
    # round 6 (deliberate): the home country solid GOLD (GOLD_DIM before), other land GLOBE_LAND (STONE before)
    assert seen(brand.GOLD) > 100 and seen(graphics.GLOBE_LAND, 40) > 30
    assert arr[0, 0, 3] == 0                                                # nothing outside the disc
    *_xy, vis = geo.orthographic(34.7 + 180, -38.4, 34.7, 38.4)
    assert not vis and geo.orthographic(34.7, 38.4, 34.7, 38.4) == (0.0, 0.0, True)


def test_route_helpers() -> None:
    assert graphics.leader_end((0, 0), (100, -10, 200, 10)) == (92, 0)
    assert graphics.leader_end((150, 100), (100, -10, 200, 10)) == (150, 18)
    # round 6 (deliberate): the chevrons are gone; one arrowhead per leg at ROUTE_ARROW_AT of its length
    tip, dirn = graphics.route_arrow([(0, 0), (300, 0), (600, 0)])
    assert round(tip[0]) == round(600 * graphics.ROUTE_ARROW_AT) and dirn[0] > 0
    assert not hasattr(graphics, "ROUTE_CHEVRON_EVERY")
    assert graphics.ROUTE_FIT > 0.62 and graphics.ROUTE_STOP_R < 16


def test_map_neighbour_name_plates_are_measured() -> None:
    frame = Image.new("RGB", (400, 200), brand.INK)
    glyph = Image.new("L", (120, 40), 0)
    ImageDraw.Draw(glyph).text((4, 4), "SYRIA", font=graphics.get_font("Montserrat", 30, weight=700), fill=255)
    frame.paste(graphics.MAP_NAME, (100, 80), glyph)
    plate = graphics._MaskPlate("SYRIA", glyph, (100, 80), graphics.MAP_NAME, frame)
    assert plate.contrast is not None and plate.contrast >= brand.TEXT_CONTRAST
    labels = [{"name": "Syria", "sprite": None, "box": (0, 0, 1, 1), "glyph": glyph, "gbox": (100, 80, 220, 120)}]
    plates = graphics._base_name_plates(labels, frame, (0, 0, 400, 200), 400, 200)
    assert len(plates) == 1 and plates[0].text == "SYRIA"
    assert graphics.MAP_NAME == brand.STONE_LT          # round 6 (deliberate): STONE_LT, as spec E (STONE before)


def test_scale_bar_carries_the_other_unit_at_its_own_length() -> None:
    bar = graphics.scale_bar(38.4, 60, "It is 280 feet deep, 25 miles away.")
    sec = bar["secondary"]
    assert bar["unit"] == "MI" and sec["unit"] == "KM"
    assert sec["px"] <= bar["px"] + 0.5
    px_per_km = 60 / bar["km_per_deg_lat"]
    assert sec["px"] == pytest.approx(sec["value"] * px_per_km, abs=0.2)
    metric = graphics.scale_bar(38.4, 60, "")
    assert metric["unit"] == "KM" and metric["secondary"]["unit"] == "MI"
    im = Image.new("RGB", (600, 200), brand.INK)
    graphics._draw_scale_bar(im, bar, 50, 100, 1.0)
    arr = np.asarray(im)
    assert (arr[104:114, round(50 + sec["px"])] != brand.INK).any()        # the second unit's end tick, under
    plates = graphics._scale_bar_plates(im, bar, 50, 100)
    assert [n for n, _p in plates] == ["scale", "scale_secondary"]
    assert plates[1][1].box[1] > plates[0][1].box[3]


# ============================================================================ round-4 review fixes (2026-11-02)
def test_scale_bar_second_label_sits_on_its_own_tick_not_the_bars_end() -> None:
    # round 6 (deliberate): each label right-aligned to its own end tick (centred on it before)
    for narration in ("", "It is 280 feet deep, 25 miles away."):
        bar = graphics.scale_bar(38.4, 60, narration)
        sec = bar["secondary"]
        im = Image.new("RGB", (900, 200), brand.INK)
        rows = dict(graphics._scale_bar_plates(im, bar, 50, 100))
        main, other = rows["scale"].box, rows["scale_secondary"].box
        # round 7 (deliberate, R2): the secondary is a shorter tick on the bar; a label wider than its run starts at
        # the zero end instead of passing it (as test_scale_bar_2026_11_03 allows)
        assert abs(other[2] - (50 + sec["px"])) <= 1 or abs(other[0] - 50) <= 1, (bar["label"], sec["label"], other)
        assert abs(main[2] - (50 + bar["px"])) <= 1
        assert other[0] >= 50 - 1 and main[0] >= 50 - 1              # never left of the bar's zero end


def test_circa_mark_keeps_a_legibility_floor_when_the_years_step_down(tmp_path, monkeypatch) -> None:
    small = graphics.get_font("Anton", 40)
    assert graphics.circa_font(small).size == graphics.CIRCA_MIN_PX >= graphics.SPAN_MIN_PX
    assert graphics.circa_font(graphics.get_font("Anton", 180)).size == round(180 * 0.5)   # 2026-11-03: 0.36 -> 0.5
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    circas = [p for n, p in st["plates"] if n.endswith("_circa")]
    assert circas and min(p.font.size for p in circas) >= graphics.CIRCA_MIN_PX


def test_tall_span_leans_toward_its_later_entry_with_more_air_than_a_label_line(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    gaps = {int(n[3:]): p for n, p in st["plates"] if n.startswith("gap")}
    assert set(gaps) == {1, 2, 3} and all(p.text.endswith("YEARS LATER") for p in gaps.values())
    for k, p in gaps.items():
        lines = [q for n, q in st["plates"] if n == f"label{k - 1}"]
        above = max(q.box[3] for q in lines)
        below = min(q.box[1] for n, q in st["plates"] if n in (f"year{k}", f"year{k}_circa"))
        air_up, air_down = p.box[1] - above, below - p.box[3]
        assert air_up > air_down, (k, p.text, air_up, air_down)     # nearer its own date than the earlier words
        if len(lines) > 1:                                           # and further from them than their line gap
            line_air = min(b.box[1] - a.box[3] for a, b in itertools.pairwise(lines))
            assert air_up > line_air, (k, air_up, line_air)
    above, below = graphics.span_airs(34, graphics.get_font("Montserrat", 30, weight=600))
    _band, air = graphics.span_band(34, graphics.get_font("Montserrat", 30, weight=600))
    assert above + below == 2 * air and above > below


def test_wide_spans_keep_later_and_sit_clear_of_the_blocks(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", 1920, 1080))
    spans: dict[int, list[Any]] = {}
    for n, p in st["plates"]:
        if n.startswith("gap"):
            spans.setdefault(int(n[3:].split("_")[0]), []).append(p)
    assert set(spans) == {1, 2, 3}
    for k, ps in spans.items():
        words = " ".join(p.text for p in ps)
        assert words.endswith("YEARS LATER"), words                 # the same words as 9:16
    blocks = {}
    for n, p in st["plates"]:
        if n.startswith(("year", "label")):
            k = int(n[4] if n.startswith("year") else n[5])
            b = blocks.setdefault(k, list(p.box))
            blocks[k] = [min(b[0], p.box[0]), min(b[1], p.box[1]), max(b[2], p.box[2]), max(b[3], p.box[3])]
    for k, ps in spans.items():
        box = graphics._union([p.box for p in ps])
        for b in blocks.values():
            assert graphics.wide_span_clearance(box, tuple(b)) >= graphics.WIDE_SPAN_CLEAR, (k, box, b)
    # round 15 (ruling R11, deliberate): two rows only as the count over its unit, never "LATER" alone
    assert graphics.wide_span_lines("40 YEARS LATER", True) == ["40", "YEARS LATER"]
    assert graphics.wide_span_lines("40 YEARS LATER", False) == ["40 YEARS LATER"]


def test_neighbour_names_never_sit_on_the_home_country() -> None:
    def square(name, lon0, lat0, lon1, lat1, label):
        ring = [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1)]
        return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": 4, "display": name,
                "label_at": label, "label_rank": 2, "type": "Sovereign country", "extent": (lon0, lat0, lon1, lat1)}
    home = square("Turkey", 26.0, 36.0, 45.0, 42.0, (35.0, 39.0))
    syria = square("Syria", 30.0, 28.0, 42.0, 36.0, (36.0, 36.05))   # its point on the shared border
    real = geo.countries
    geo.countries = lambda: [home, syria]
    try:
        bw, bh, kx, coslat, lat0, lon0 = 1600, 1200, 60.0, math.cos(math.radians(36.0)), 36.0, 36.0

        def project(lon, lat):
            return bw / 2 + (lon - lon0) * kx * coslat, bh / 2 - (lat - lat0) * kx
        labels: list[dict[str, Any]] = []
        graphics.relief_base(lat0, lon0, bw, bh, kx, coslat, home, project, (0, 0, bw, bh), transient=labels,
                             name_px=40)
    finally:
        geo.countries = real
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).polygon([project(x, y) for x, y in home["polygons"][0][0]], fill=255)
    arr = np.asarray(mask)
    named = [lab for lab in labels if lab["name"] == "Syria"]
    assert named                                                     # still named, moved onto its own land
    for lab in named:
        assert not graphics.name_on_mask(arr, lab.get("gbox") or lab["box"], pad=0), lab["box"]
    assert graphics.name_on_mask(arr, (790, 590, 810, 610)) and not graphics.name_on_mask(None, (0, 0, 9, 9))


def test_section_drift_settles_to_rest_on_the_last_frame(tmp_path, monkeypatch) -> None:
    assert graphics.section_drift(0.0, 4.0, 5.9, 1.02) == pytest.approx(1.02)
    assert graphics.section_drift(5.9, 4.0, 5.9, 1.02) == pytest.approx(1.0)
    assert graphics.section_drift(3.0, 4.0, 3.9, 1.02) == 1.0                # no hold: no drift
    vals = [graphics.section_drift(4.0 + i * 0.1, 4.0, 5.9, 1.02) for i in range(20)]
    assert all(b <= a + 1e-9 for a, b in itertools.pairwise(vals)) and min(vals) >= 1.0
    st = _render(monkeypatch, lambda: graphics.render_cross_section(SECTION, tmp_path / "x.mp4"))
    rows = _rows(st)
    last = np.asarray(st["frames"][-1]).astype(np.int16)
    for name in ("level3_depth", "level3_label0"):
        b = rows[name].box                                           # the recorded word is where the last frame has it
        patch = last[b[1]:b[3], b[0]:b[2]]
        assert (np.abs(patch - np.array(rows[name].fill, np.int16)).max(axis=2) <= 40).mean() > 0.12, name


def test_gold_on_gold_gate_brings_an_ochre_band_under_the_warm_measure(tmp_path) -> None:
    photo = Image.open(_photo(tmp_path / "p.jpg")).convert("RGB")
    box = (100, 300, 900, 700)
    out = graphics.stat_hue_gate(photo, 300, 700, box)
    sat = float(np.asarray(out.crop(box).convert("HSV"))[..., 1].mean()) / 255
    assert sat <= graphics.STAT_GROUND_SAT + 0.01
    before = float(np.asarray(photo.crop(box).convert("HSV"))[..., 1].mean()) / 255
    assert before - sat >= 0.3                                       # a visible change, not a 40 % nudge
    assert graphics.stat_scrim_target(out, box) > graphics.stat_scrim_target(photo, box)   # no longer "warm"


# ============================================================================ round-4 review 2 (2026-11-02)
def _ink_air(st: dict[str, Any], k: int) -> tuple[int, int, int]:
    """(span gap k's air above to the earlier label, its air below to the later year, the earlier entry's own
    tightest air: year to label and label line to line), all measured on the recorded plates."""
    rows = _rows(st)
    p = rows[f"gap{k}"]
    lines = sorted((q for n, q in st["plates"] if n == f"label{k - 1}"), key=lambda q: q.box[1])
    year = rows[f"year{k - 1}"]
    inner = min([lines[0].box[1] - year.box[3]] + [b.box[1] - a.box[3] for a, b in itertools.pairwise(lines)])
    below = min(q.box[1] for n, q in st["plates"] if n in (f"year{k}", f"year{k}_circa"))
    return p.box[1] - lines[-1].box[3], below - p.box[3], inner


def test_tall_span_clears_the_earlier_label_by_twice_its_own_line_air(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    gaps = {int(n[3:]) for n, _p in st["plates"] if n.startswith("gap")}
    assert gaps == {1, 2, 3}
    for k in gaps:
        up, down, inner = _ink_air(st, k)
        assert up >= graphics.SPAN_SEPARATION * inner - 4, (k, up, inner)   # "40 YEARS LATER": 23 px over 18 before
        assert 0 < down < up, (k, up, down)                                 # still the later date's lead-in
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    years = [p for n, p in st["plates"] if n.startswith("year") and not n.endswith("circa")]
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX  # paid for without the label floor
    assert min(p.font.size for p in years) >= 2 * max(p.font.size for p in labels)   # 62 over 34 before


def test_tall_column_starts_on_the_reel_box_top_inside_the_safe_zone(tmp_path, monkeypatch) -> None:
    from src import safe_zones
    box = safe_zones.graphics_box(1080, 1920)
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    tops = [p.box[1] for _n, p in st["plates"]]
    assert box[1] <= min(tops) <= box[1] + graphics.TALL_TOP_PAD + 2      # 360 before: 72 px of the box unused
    assert max(p.box[3] for _n, p in st["plates"]) <= box[3]
    assert all(box[0] <= p.box[0] and p.box[2] <= box[2] for _n, p in st["plates"])


def test_span_clear_airs_and_inner_air() -> None:
    f_gap = graphics.get_font("Montserrat", 30, weight=600)
    lean = graphics.span_airs(34, f_gap)
    assert graphics.span_clear_airs(34, f_gap, 0) == lean                 # no inner air: the lean alone
    above, below = graphics.span_clear_airs(34, f_gap, 18)
    assert above == math.ceil(graphics.SPAN_SEPARATION * 18) and below == lean[1]
    f_lab = graphics.get_font("Montserrat", 34, weight=800)
    cap = graphics.cap_height(f_lab)
    assert graphics.entry_inner_air(53, f_lab, 42, 44, 1) == 42 - cap
    assert graphics.entry_inner_air(53, f_lab, 46, 44, 2) == 44 - cap


def test_separation_never_takes_the_labels_under_their_floor(tmp_path, monkeypatch) -> None:
    # a column too short for the separation at the label floor: the spans fall back to the bare lean
    monkeypatch.setattr(graphics, "SPAN_SEPARATION", 6.0)
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX
    assert {int(n[3:]) for n, _p in st["plates"] if n.startswith("gap")} == {1, 2, 3}


def test_span_fit_shrinks_before_it_shortens_and_never_below_the_floor() -> None:
    seen: list[tuple[str, int]] = []

    def only_at(px_ok: int, words: str):
        def fits(tx: str, px: int) -> bool:
            seen.append((tx, px))
            return px <= px_ok and tx == words
        return fits
    assert graphics.span_fit("40 YEARS LATER", 36, only_at(32, "40 YEARS LATER")) == ("40 YEARS LATER", 32)
    assert graphics.span_fit("40 YEARS LATER", 36, only_at(36, "40 YEARS")) == ("40 YEARS", 36)   # all sizes tried first
    seen.clear()
    assert graphics.span_fit("40 YEARS LATER", 31, only_at(0, "")) is None
    assert min(px for _tx, px in seen) == graphics.SPAN_MIN_PX                # the legibility floor holds
    assert graphics.span_fallback_y(100, 300, 20) == 240         # review 4: a third of the slack under it


def test_a_span_no_band_holds_is_set_beside_the_spine_not_dropped(tmp_path, monkeypatch) -> None:
    # operator (round 4 review 2): keep the information on 9:16; where no band holds the span it sits on its
    # segment beside the spine, at the legibility floor
    monkeypatch.setattr(graphics, "span_fit", lambda text, px, fits: None)
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    rows = _rows(st)
    gaps = {int(n[3:]): p for n, p in st["plates"] if n.startswith("gap")}
    assert set(gaps) == {1, 2, 3}
    for k, p in gaps.items():
        assert p.font.size == graphics.SPAN_MIN_PX and p.text.endswith("YEARS LATER")
        assert rows[f"year{k - 1}"].box[1] < p.box[1] and p.box[3] < rows[f"year{k}"].box[3]   # on its segment
    _assert_clear(st["plates"])


# ============================================================================ round 4 review 3: the 9:16 timeline
LONG = "Abandoned after the long population exchange of the region"
SIX = [{"year": -1200, "approximate": True, "label": "First chambers cut into the soft rock"},
       {"year": -700, "approximate": True, "label": "Possibly Phrygian tunnels"},
       {"year": 780, "approximate": True, "label": "Expanded against raiders from the south"},
       {"year": 1100, "label": "Churches carved beside the halls"},
       {"year": 1923, "label": LONG}, {"year": 1963, "label": "Rediscovered behind a wall"}]
FIVE_CLOSE = [{"year": y, "label": LONG} for y in (1900, 1905, 1910, 1915, 2020)]
TWO_LINE = [{"year": 1900, "label": "Expanded against raiders from the south and the east"},
            {"year": 1905, "label": "Rediscovered behind a wall in the cellar of a house"},
            {"year": 1910, "label": "Churches carved beside the halls of the city"},
            {"year": 1960, "label": "Opened to visitors after the long survey"}]
TALL_CASES = {"derinkuyu": DERINKUYU, "six": SIX, "five_close": FIVE_CLOSE, "two_line": TWO_LINE}


def _assert_clear(plates) -> None:
    """No two recorded plates overlap (tests/test_r3_graphics_2026_11_02.py's _no_collisions)."""
    hits = [(a, b) for (a, pa), (b, pb) in itertools.combinations(plates, 2)
            if graphics.LayoutConstraintSolver.boxes_intersect(pa.box, pb.box)]
    assert not hits, hits


def _refused_tall(monkeypatch: pytest.MonkeyPatch, case: str, job) -> bool:
    """QF-24: a TALL_CASES set in QF23_REFUSED (five_close on the organic Reel box: its best two-row break needs 697 px
    at the 30 px floor where the column holds 646, 706 on the ads box, and no three-row stack of its four drawn
    entries fits the 9:16 column) raises the checked-copy refusal and encodes nothing (_refused_or_render); True when
    it is refused, checked here."""
    if f"tl/{case}-9x16" not in QF23_REFUSED:
        return False
    assert _refused_or_render(monkeypatch, f"tl/{case}-9x16", job) is None
    return True


@pytest.mark.parametrize("case", sorted(TALL_CASES))
def test_a_fallback_span_sits_in_the_clear_band_and_collides_with_nothing(tmp_path, monkeypatch, case) -> None:
    # round 4 review 3: centred between the dots at the label column, the fallback sat on label2 (Derinkuyu, six
    # events) and on every label of five close entries; it now sits in the band between the two entries
    monkeypatch.setattr(graphics, "span_fit", lambda text, px, fits: None)
    events = TALL_CASES[case]
    if _refused_tall(monkeypatch, case, lambda: graphics.render_timeline(events, tmp_path / "t.mp4")):
        return
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"))
    rows = _rows(st)
    n = min(4, len(events))
    gaps = {int(nm[3:]): p for nm, p in st["plates"] if nm.startswith("gap")}
    assert set(gaps) == set(range(1, n)), case                           # never dropped
    for k, p in gaps.items():
        assert p.font.size == graphics.SPAN_MIN_PX
        earlier = max(q.box[3] for nm, q in st["plates"] if nm == f"label{k - 1}")
        later = min(q.box[1] for nm, q in st["plates"] if nm in (f"year{k}", f"year{k}_circa"))
        assert earlier < p.box[1] and p.box[3] < later, (case, k)        # in the band between the entries
        assert rows[f"year{k - 1}"].box[1] < p.box[1] and p.box[3] < rows[f"year{k}"].box[3]
    _assert_clear(st["plates"])


def test_free_span_xy_finds_a_clear_place_or_none() -> None:
    box = (100, 80, 300, 100)                      # the span's ink set at (100, 100)
    label = (100, 60, 400, 95)                     # what it would sit on at its own baseline
    assert graphics.free_span_xy(box, (100, 100), [100], [100], [], 900) == (100, 100)
    # the first baseline collides: the next one clear of the label by the air is taken
    x, y = graphics.free_span_xy(box, (100, 100), [100, 110, 130], [100], [label], 900, 6)
    assert (x, y) == (100, 130)
    # no baseline clears it at the column's x: beside it, right of the label
    x, y = graphics.free_span_xy(box, (100, 100), [100], [100], [label], 900, 6)
    assert x == 406 and y == 100
    assert graphics.free_span_xy(box, (100, 100), [100], [100], [label], 500, 6) is None   # no room: none
    assert graphics.span_fallback_y(400, 480, 22) == pytest.approx(480 - 58 / 3)   # review 4: leaning late


@pytest.mark.parametrize("case", sorted(TALL_CASES))
def test_tall_labels_hold_their_floor_and_spans_stay_under_them(tmp_path, monkeypatch, case) -> None:
    # round 4 review 3: a crowded four-entry column took the labels to 27 px under 30 px spans
    if _refused_tall(monkeypatch, case, lambda: graphics.render_timeline(TALL_CASES[case], tmp_path / "t.mp4")):
        return
    st = _render(monkeypatch, lambda: graphics.render_timeline(TALL_CASES[case], tmp_path / "t.mp4"))
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    gaps = [p for n, p in st["plates"] if n.startswith("gap")]
    assert min(p.font.size for p in labels) >= graphics.TALL_LABEL_MIN_PX, case
    assert gaps and max(p.font.size for p in gaps) <= min(p.font.size for p in labels) - graphics.SPAN_UNDER_LABEL
    assert min(p.font.size for p in gaps) >= graphics.SPAN_MIN_PX
    _assert_clear(st["plates"])


def test_a_label_too_long_for_one_line_at_the_floor_wraps_instead_of_shrinking(tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(SIX, tmp_path / "t.mp4"))
    first = [p for n, p in st["plates"] if n == "label0"]
    assert len(first) == 2 and all(p.font.size == graphics.TALL_LABEL_MIN_PX for p in first)
    assert " ".join(p.text for p in first) == "FIRST CHAMBERS CUT INTO THE SOFT ROCK"


@pytest.mark.parametrize("events", [[DERINKUYU[0], {"year": 1923, "label": LONG}, DERINKUYU[3]],
                                    [DERINKUYU[0], {"year": 1923, "label": LONG}]],
                         ids=["three", "two"])
def test_a_label_too_long_for_two_lines_takes_a_third_where_the_column_holds_it(tmp_path, monkeypatch,
                                                                                   events) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4"))
    rows = [p for n, p in st["plates"] if n == "label1"]
    # round 6 (deliberate): at 30 px the label holds on 2 or 3 rows (3 at 34 px)
    assert len(rows) in (2, 3) and "…" not in "".join(p.text for p in rows)   # never cut where it fits
    assert " ".join(p.text for p in rows) == events[1]["label"].upper()
    assert min(p.font.size for n, p in st["plates"] if n.startswith("label")) >= graphics.TALL_LABEL_MIN_PX
    assert {int(n[3:]) for n, _p in st["plates"] if n.startswith("gap")} == set(range(1, len(events)))
    _assert_clear(st["plates"])


# ============================================================================ round 4 review 4 (2026-11-02)
ROUTE_POINTS = {"istanbul": (41.0082, 28.9784), "ankara": (39.9334, 32.8597), "derinkuyu": (38.3735, 34.7347),
                "antalya": (36.8969, 30.7133), "izmir": (38.4237, 27.1428), "kars": (40.6013, 43.0975),
                "trabzon": (41.0027, 39.7168), "bodrum": (37.0344, 27.4305)}


def _stop(place: str, label: str) -> dict[str, str]:
    return {"place": place, "country": "Turkey", "label": label}


ROUTES = {
    "derinkuyu": [_stop("Istanbul", "Constantinople"), _stop("Ankara", "Ancyra"),
                  _stop("Derinkuyu", "Derinkuyu underground city")],
    "sw_corner": [_stop("Kars", "Kars fortress on the eastern frontier"),
                  _stop("Izmir", "Smyrna harbour of the Aegean coast"),
                  _stop("Bodrum", "Halicarnassus mausoleum site of the ancient wonder")],
    "five": [_stop("Istanbul", "Constantinople the imperial capital"), _stop("Izmir", "Smyrna"),
             _stop("Antalya", "Attaleia harbour town"), _stop("Derinkuyu", "Derinkuyu underground city of Cappadocia"),
             _stop("Kars", "Ani ruins near Kars")],
    "ns": [_stop("Trabzon", "Trebizond"), _stop("Antalya", "Attaleia on the southern coast of Anatolia")],
}
# The stops commit HEAD named on these routes (the reviewer's fake-geo renders, before the globe and the lifted bar)
HEAD_NAMED = {(1080, 1920): {"derinkuyu": 3, "sw_corner": 3, "five": 4, "ns": 2},
              (1920, 1080): {"derinkuyu": 3, "sw_corner": 3, "five": 5, "ns": 1}}


@pytest.fixture
def route_geo(monkeypatch):
    """The synthetic Natural Earth stand-in of tests/test_r3_graphics_2026_11_02.py, with the reviewer's stops."""
    def country(name, cx, cy, rx, ry):
        ring = [(cx + rx * math.cos(2 * math.pi * i / 48), cy + ry * math.sin(2 * math.pi * i / 48)) for i in range(48)]
        return {"names": {name.lower()}, "sovereign": name.lower(), "polygons": [[ring]], "size": len(ring),
                "display": name, "label_at": (cx, cy), "label_rank": 2, "type": "Sovereign country",
                "extent": (cx - rx, cy - ry, cx + rx, cy + ry)}
    world = [country("Turkey", 35.0, 39.0, 9.0, 3.2), country("Ukraine", 31.5, 49.0, 7.0, 2.8),
             country("Egypt", 30.0, 26.5, 5.0, 4.5), country("Iran", 54.0, 32.5, 7.5, 5.0)]
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "countries", lambda: world)
    monkeypatch.setattr(geo, "find_country", lambda name: next((c for c in world if name.lower() in c["names"]), None))
    monkeypatch.setattr(geo, "locate", lambda topic: dict(
        zip(("lat", "lon"), ROUTE_POINTS[topic["location"]["name"].lower()]), label=topic["location"]["name"],
        approximate=False, matched="name", country=topic["location"]["country"]))
    monkeypatch.setattr(graphics, "_relief", lambda: None)
    return world


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(ROUTES))
def test_route_names_outrank_the_globe_and_the_scale_bar(tmp_path, monkeypatch, route_geo, case, size) -> None:
    # 9:16 at review 4: Trebizond -> Attaleia 0/2 named (HEAD 2/2), Kars/Izmir/Bodrum 2/3 (3/3), five stops 3/5 (4/5)
    stops = ROUTES[case]
    st = _render(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0], height=size[1]))
    named = {n for n, _p in st["plates"] if n.startswith("stop")}
    assert len(named) >= HEAD_NAMED[size][case], (case, sorted(named))         # never fewer than HEAD
    if len(stops) <= 3:
        assert len(named) == len(stops), (case, sorted(named))                # and every name on 2 and 3 stops
    words = " ".join(p.text for n, p in st["plates"] if n.startswith("stop")).split()
    assert words == " ".join(s["label"].upper() for k, s in enumerate(stops) if f"stop{k}" in named).split()
    names = [p.box for n, p in st["plates"] if n.startswith("stop")]
    figures = [p.box for n, p in st["plates"] if n.startswith("scale")]
    assert not any(graphics.LayoutConstraintSolver.boxes_intersect(a, b, graphics.ROUTE_SCALE_AIR)
                   for a in figures for b in names)                           # the bar's figures keep their air
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])
    _assert_clear(st["plates"])
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in st["record"]["elements"]), st["record"]["elements"]


def test_route_names_of_different_stops_keep_more_air_than_a_names_own_line_gap(tmp_path, monkeypatch,
                                                                                route_geo) -> None:
    st = _render(monkeypatch, lambda: graphics.render_route(ROUTES["five"], tmp_path / "r.mp4"))
    by_stop: dict[str, list[Any]] = {}
    for n, p in st["plates"]:
        if n.startswith("stop"):
            by_stop.setdefault(n, []).append(p.box)
    line_gap = max(b[1] - a[3] for rows in by_stop.values() for a, b in itertools.pairwise(rows))
    assert graphics.ROUTE_NAME_AIR > line_gap
    for (_a, ra), (_b, rb) in itertools.combinations(by_stop.items(), 2):
        assert not any(graphics.LayoutConstraintSolver.boxes_intersect(p, q, graphics.ROUTE_NAME_AIR - 1)
                       for p in ra for q in rb), (_a, _b)


def test_scale_bar_figures_are_reference_type_under_the_stop_names() -> None:
    # round 6 (deliberate): Montserrat 600 at SCALE_LABEL_PX (24) replaces the 30 px Plex Mono on the map's bar
    # round 6 review (deliberate): a bar with both units (at max_px=120 the two ends no longer read apart, and the
    # numbers may not repeat: that bar is one unit now)
    bar = graphics.scale_bar(39.0, 60.0, "")
    im = Image.new("RGB", (900, 300), brand.INK)
    rows = dict(graphics._scale_bar_plates(im, bar, 50, 150))
    main, other = rows["scale"], rows["scale_secondary"]
    assert main.font.size == other.font.size == graphics.SCALE_LABEL_PX < 34            # under the 34 px names
    assert graphics.SCALE_LABEL_PX >= graphics.SPAN_MIN_PX                              # at the tertiary floor
    assert graphics.SCALE_LABEL_WEIGHT == 600                                           # the names 800
    assert main.box[2] - main.box[0] <= 120                                             # no wider than a 120 px bar


def test_globe_box_moves_to_the_top_left_and_shrinks_inside_the_safe_areas() -> None:
    from src import safe_zones
    for size in ((1080, 1920), (1920, 1080)):
        tr, tl = graphics.globe_box(*size), graphics.globe_box(*size, corner="tl")
        assert tl[1] == tr[1] and tl[2] - tl[0] == tr[2] - tr[0] and tl[2] < tr[0]
        small = graphics.globe_box(*size, shrink=0.7)
        assert small[2] == tr[2] and small[2] - small[0] == round((tr[2] - tr[0]) * 0.7)
    rx0, _ry0, rx1, _ry1 = safe_zones.graphics_box(1080, 1920)
    tl, tr = graphics.globe_box(1080, 1920, "tl"), graphics.globe_box(1080, 1920)
    assert tl[0] - rx0 == rx1 - tr[2] and tl[0] >= rx0                                   # mirrored in the Reel box


def test_free_span_xy_never_sets_a_span_beside_a_year() -> None:
    box = (100, 80, 300, 100)                      # the span's ink set at (100, 100)
    year = (100, 60, 400, 95)                      # a year's ink on that baseline
    # beside a label the span may sit right of it; beside a year it may not ("C. AD 780  ABOUT 1,500 YEARS")
    assert graphics.free_span_xy(box, (100, 100), [100], [100], [year], 900, 6) == (406, 100)
    assert graphics.free_span_xy(box, (100, 100), [100], [100], [year], 900, 6, [year]) is None
    # a baseline clear of the year is still taken, at the column's x
    assert graphics.free_span_xy(box, (100, 100), [100, 130], [100], [year], 900, 6, [year]) == (100, 130)


def test_a_fallback_span_never_lands_beside_a_year(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(graphics, "span_fit", lambda text, px, fits: None)
    monkeypatch.setattr(graphics, "span_fallback_y", lambda top, bottom, cap, inner_air=0.0: top - 4)  # on a label
    st = _render(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4"))
    years = [p.box for n, p in st["plates"] if n.startswith("year")]
    for n, p in st["plates"]:
        if n.startswith("gap"):
            level = [y for y in years if y[1] < p.box[3] and p.box[1] < y[3]]
            assert not level, (n, p.text, level)


def test_timeline_events_keep_the_first_and_last_and_log_the_dropped(caplog) -> None:
    six = [{"year": y, "label": str(y)} for y in (-1200, -700, 780, 1100, 1923, 1963)]
    with caplog.at_level("WARNING", logger=graphics.log.name):
        kept = graphics.timeline_events(list(reversed(six)))
    assert [e["year"] for e in kept] == [-1200, 780, 1100, 1963]            # first, last, evenly spaced between
    assert "700 BC" in caplog.text and "1923" in caplog.text
    weighted = [{**e, "significance": s} for e, s in zip(six, (0, 1, 0, 0, 9, 5))]
    assert [e["year"] for e in graphics.timeline_events(weighted)] == [-1200, -700, 1923, 1963]
    assert graphics.timeline_events(six[:4]) == six[:4]
    assert [e["year"] for e in graphics.timeline_events(six[:5])] == [-1200, -700, 1100, 1923]


def test_a_six_event_timeline_still_draws_its_last_event(tmp_path, monkeypatch) -> None:
    six = [{"year": y, "label": f"Event {k}"} for k, y in enumerate((-1200, -700, 780, 1100, 1923, 1963))]
    st = _render(monkeypatch, lambda: graphics.render_timeline(six, tmp_path / "t.mp4"))
    years = [p.text for n, p in st["plates"] if n.startswith("year") and not n.endswith("_circa")]
    assert len(years) == 4 and years[0].endswith("1200 BC") and years[-1] == "1963"


XL = "Abandoned after the long and bitter population exchange between the two neighbouring states of the region"


def test_a_two_entry_label_grows_rows_while_the_column_has_room(tmp_path, monkeypatch) -> None:
    # review 4: two 108-character labels were cut at three rows with ~120 px of the column spare
    st = _render(monkeypatch, lambda: graphics.render_timeline([{"year": 1923, "label": XL}, {"year": 1925, "label": XL}],
                                                               tmp_path / "t.mp4"))
    for k in (0, 1):
        rows = [p for n, p in st["plates"] if n == f"label{k}"]
        assert len(rows) > graphics.TALL_LABEL_ROWS, (k, [p.text for p in rows])
        assert min(p.font.size for p in rows) >= graphics.TALL_LABEL_MIN_PX
        # review 5: drawn whole, no ellipsis, where it fits (the whole label's tracking came off every line)
        assert " ".join(p.text for p in rows) == XL.upper() and not any("…" in p.text for p in rows), k
        assert all(graphics.tracked_width(p.text, p.font, 2) <= graphics.TEXT_RIGHT - p.box[0] + 4 for p in rows)
    _x0, _top, _x1, bottom = graphics.get_text_box(1080, 1920)
    assert max(p.box[3] for _n, p in st["plates"]) <= bottom
    _assert_clear(st["plates"])


def test_a_two_entry_span_shares_its_slack_without_crowding_the_later_year(tmp_path, monkeypatch) -> None:
    # review 4: "2 YEARS LATER" sat ~15 px over a 150 px year and ~120 px under the earlier label
    st = _render(monkeypatch, lambda: graphics.render_timeline([{"year": 1923, "label": "A"},
                                                                {"year": 1925, "label": "B"}], tmp_path / "t.mp4"))
    rows = _rows(st)
    up = rows["gap1"].box[1] - rows["label0"].box[3]
    down = rows["year1"].box[1] - rows["gap1"].box[3]
    assert up > down >= up / 3, (up, down)                       # still the later date's lead-in, not pinned to it
    assert graphics.span_slack_below(90) == 30


def test_a_fallback_span_is_separated_from_a_two_line_label_by_more_than_its_leading(tmp_path, monkeypatch) -> None:
    # review 4: centred in a short band the fallback sat 24 px under a two-line label whose line gap is 18 px
    monkeypatch.setattr(graphics, "span_fit", lambda text, px, fits: None)
    st = _render(monkeypatch, lambda: graphics.render_timeline(TWO_LINE, tmp_path / "t.mp4"))
    for k in (1, 2, 3):
        up, down, _inner = _ink_air(st, k)
        lines = sorted((q for n, q in st["plates"] if n == f"label{k - 1}"), key=lambda q: q.box[1])
        leading = min(b.box[1] - a.box[3] for a, b in itertools.pairwise(lines))
        assert up >= 1.5 * leading and up > down > 0, (k, up, down, leading)
    assert graphics.span_fallback_y(100, 300, 20) == 300 - 60     # a third of the band's slack under it
    assert graphics.span_fallback_y(400, 480, 22, 18) == pytest.approx(480 - 58 / 3)  # 39 px over it (>= 36)
    assert graphics.span_fallback_y(400, 430, 22) == 426         # no room to lean: centred


# ============================================================================ round 4 review 5 (2026-11-02)
def _render_fast(monkeypatch: pytest.MonkeyPatch, job) -> dict[str, Any]:
    """_render at 1 fps: the layout and the last frame's plates are the same, the clip has fewer frames."""
    return _render(monkeypatch, job, fps=1)


def _refused_or_render(monkeypatch: pytest.MonkeyPatch, key: str, job) -> dict[str, Any] | None:
    """QF-23: a timeline set in QF23_REFUSED (`key`: "<family>/<set>-<9x16|16x9>") raises the checked-copy refusal
    and encodes nothing (None); any other set renders (_render_fast) and must not be refused."""
    if key not in QF23_REFUSED:
        return _render_fast(monkeypatch, job)
    monkeypatch.setattr(graphics, "_encode", lambda *_a, **_k: pytest.fail(f"{key}: a refused timeline was encoded"))
    with pytest.raises(graphics.TextFitError, match="Checked copy does not fit the "):
        job()
    return None


@pytest.mark.parametrize("anchor", ["ls", "rs", "ms", "la"])
@pytest.mark.parametrize("xy", [(700, 200), (700.4, 199.6), (712.75, 333.25)])
def test_ink_box_is_the_planned_plates_box(anchor, xy) -> None:
    ground = Image.new("RGB", (1400, 600), brand.INK)
    font = graphics.get_font("Montserrat", 34, weight=800)
    for text, track in (("SINOPE, BIRTHPLACE", 2), ("ICONIUM", 0), ("ABOUT 320 YEARS", 1.5)):
        plate = graphics.plan_text(ground, xy, text, font, brand.BONE, anchor, track=track)
        assert graphics.ink_box(xy, text, font, anchor, track) == plate.box, (text, anchor, xy)
    # a place off the frame is measured whole, not clipped (review 5: Sinope's name planned at y = -2)
    box = graphics.ink_box((300, 10), "SINOPE", font, "ls", 2)
    assert box is not None and box[1] < 0
    assert graphics.ink_box((0, 0), "", font) is None


def test_fit_tracked_pays_each_lines_own_tracking() -> None:
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    text = "REDISCOVERED BEHIND A WALL IN THE CELLAR OF A HOUSE"
    width = graphics.tracked_width("REDISCOVERED BEHIND A WALL", graphics.get_font("Montserrat", 34, weight=800), 2) + 8
    lines, font, short = graphics.fit_tracked(probe, text, "Montserrat", 34, width, 3, 34, 2, weight=800, quiet=True)
    assert not short and " ".join(lines) == text
    assert all(graphics.tracked_width(ln, font, 2) <= width for ln in lines)
    old, _f, _s = graphics.fit_text(probe, text, "Montserrat", 34, int(width - 2 * len(text)), 3, 34, weight=800,
                                    quiet=True)
    assert len(lines) < len(old)                      # the whole label's tracking off every line wrapped early
    one, _f, _s = graphics.fit_tracked(probe, "CHURCHES CARVED", "Montserrat", 38, 560, 3, 34, 2, weight=800)
    assert one == ["CHURCHES CARVED"]


EDGE_POINTS = {"sinop": (42.0264, 35.1551), "konya": (37.8746, 32.4932), "hakkari": (37.5744, 43.7408),
               "edirne": (41.6771, 26.5557), "izmir": (38.4237, 27.1428), "kars": (40.6013, 43.0975),
               "antalya": (36.8969, 30.7133), "bodrum": (37.0344, 27.4305), "trabzon": (41.0027, 39.7168),
               "kaymakli": (38.4619, 34.7514), "nevsehir": (38.6244, 34.7239), "konya2": (37.8746, 32.4932)}
LONG_NAME = "Sinope, birthplace of Diogenes the Cynic philosopher"
EDGE_ROUTES = {
    "top": [_stop("Konya", "Iconium"), _stop("Sinop", LONG_NAME)],                          # review 5's crash
    "top_first": [_stop("Sinop", LONG_NAME), _stop("Konya", "Iconium")],
    "bottom": [_stop("Trabzon", "Trebizond"), _stop("Antalya", "Attaleia on the southern coast of Anatolia")],
    "left": [_stop("Kars", "Kars"), _stop("Edirne", "Adrianople, the city of the emperor Hadrian")],
    "right": [_stop("Edirne", "Adrianople"), _stop("Hakkari", "Hakkari in the mountains of the far south-east")],
    "corners": [_stop("Edirne", "Adrianople on the Thracian plain"), _stop("Sinop", LONG_NAME),
                _stop("Kars", "Kars fortress on the eastern frontier"), _stop("Hakkari", "Hakkari mountains"),
                _stop("Bodrum", "Halicarnassus mausoleum site of the ancient wonder")],
}


@pytest.fixture
def edge_geo(monkeypatch, route_geo):
    points = {**ROUTE_POINTS, **EDGE_POINTS}
    monkeypatch.setattr(geo, "locate", lambda topic: dict(
        zip(("lat", "lon"), points[topic["location"]["name"].lower()]), label=topic["location"]["name"],
        approximate=False, matched="name", country=topic["location"]["country"]))
    return points


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(EDGE_ROUTES))
def test_a_route_with_stops_at_every_edge_renders_inside_the_box(tmp_path, monkeypatch, edge_geo, case, size) -> None:
    # review 5: Konya -> Sinop at 1080x1920 crashed (a name above Sinope planned at y = -2, ring_contrast TypeError)
    stops = EDGE_ROUTES[case]
    st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                 height=size[1]))
    named = {n for n, _p in st["plates"] if n.startswith("stop")}
    assert len(named) >= min(len(stops), 4), (case, sorted(named))
    if len(stops) == 2:
        assert len(named) == 2, (case, sorted(named))
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])
    _assert_clear(st["plates"])
    assert all(e["drawn"] >= brand.TEXT_CONTRAST for e in st["record"]["elements"])


def _stop_points(monkeypatch) -> list[tuple[float, float]]:
    """The route's stop points in order, as name_places measures its candidates against them (box_distance)."""
    seen: list[tuple[float, float]] = []
    real = graphics.box_distance

    def spy(pt, box):
        seen.append(tuple(pt))
        return real(pt, box)
    monkeypatch.setattr(graphics, "box_distance", spy)
    return seen


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", ["sw_corner", "five"])
def test_a_stops_name_is_nearer_its_own_stop_than_any_other(tmp_path, monkeypatch, route_geo, case, size) -> None:
    # review 5: "SMYRNA HARBOUR OF THE AEGEAN COAST" sat beside the Halicarnassus pin and read as its name
    seen = _stop_points(monkeypatch)
    st = _render_fast(monkeypatch, lambda: graphics.render_route(ROUTES[case], tmp_path / "r.mp4", width=size[0],
                                                                 height=size[1]))
    pts = list(dict.fromkeys(seen))[:len(ROUTES[case])]
    assert len(pts) == len(ROUTES[case])
    for k in range(len(pts)):
        boxes = [p.box for n, p in st["plates"] if n == f"stop{k}"]
        if not boxes:
            continue
        u = graphics._union(boxes)
        own = graphics.box_distance(pts[k], u)
        other = min(graphics.box_distance(q, u) for j, q in enumerate(pts) if j != k)
        assert other >= min(own + 12, graphics.ROUTE_LEADER_AT), (case, k, own, other)


def test_route_leader_runs_from_the_stops_rim_to_the_nearest_point_of_its_name() -> None:
    start, end = graphics.route_leader((100, 100), (150, 20, 400, 60), 13)
    assert end == (142, 68)                                          # the block's corner, grown by 8 px
    assert math.dist((100, 100), start) == pytest.approx(13)
    start, end = graphics.route_leader((200, 200), (100, 20, 400, 60), 25)
    assert end == (200, 68) and start == (200, 175)                  # straight up to the block's underside
    assert graphics.box_distance((0, 0), (3, 4, 10, 10)) == 5
    assert graphics.box_distance((5, 5), (0, 0, 10, 10)) == 0
    assert graphics.ROUTE_LEADER_W == max(1, round(2 * 1080 / graphics.VIDEO_WIDTH))   # the timeline flags' weight


def test_route_stops_keep_the_first_and_the_last_and_log_the_dropped(caplog) -> None:
    six = [{"label": f"S{k}"} for k in range(6)]
    with caplog.at_level("WARNING", logger=graphics.log.name):
        kept = graphics.route_stops(six)
    assert [s["label"] for s in kept] == ["S0", "S1", "S2", "S4", "S5"]        # the destination kept
    assert "S3" in caplog.text and "6 stops, 5 drawn" in caplog.text
    assert graphics.route_stops(six[:5]) == six[:5]
    assert [s["label"] for s in graphics.route_stops([{"label": str(k)} for k in range(9)])] == \
        ["0", "2", "4", "6", "8"]
    assert graphics.even_keep(3, 2) == [0, 2] and graphics.even_keep(2, 5) == [0, 1]


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
def test_a_six_stop_route_draws_its_destination(tmp_path, monkeypatch, edge_geo, size, caplog) -> None:
    stops = [_stop("Izmir", "Smyrna"), _stop("Bodrum", "Halicarnassus"), _stop("Antalya", "Attaleia"),
             _stop("Konya", "Iconium"), _stop("Kars", "Kars"), _stop("Trabzon", "Trebizond")]
    with caplog.at_level("WARNING", logger=graphics.log.name):
        st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                     height=size[1]))
    words = [p.text for n, p in st["plates"] if n.startswith("stop")]
    assert "TREBIZOND" in words and "ICONIUM" not in words, words       # the sixth stop is the destination
    assert "dropped Iconium" in caplog.text


@pytest.mark.parametrize("case", sorted(ROUTES))
def test_wide_route_scale_bar_stays_out_of_the_timestamp_corner_and_under_the_names(tmp_path, monkeypatch,
                                                                                     route_geo, case) -> None:
    # review 5: on 16:9 the bar went bottom-right (YouTube's timestamp) before trying a shorter one on the left
    st = _render_fast(monkeypatch, lambda: graphics.render_route(ROUTES[case], tmp_path / "r.mp4", width=1920,
                                                                 height=1080))
    figures = [p for n, p in st["plates"] if n.startswith("scale")]
    names = [p for n, p in st["plates"] if n.startswith("stop")]
    assert figures and max(p.box[2] for p in figures) < 1920 / 2, (case, [p.box for p in figures])
    assert min(p.font.size for p in names) >= graphics.SCALE_FIGURE_PX + 4      # the names clearly dominate
    # review 9 (deliberate): 34 px still, and only a stop otherwise left unnamed steps down, to 28 px at the least
    assert graphics.ROUTE_NAME_PX == graphics.ROUTE_NAME_STEPS[0] == 34 and graphics.ROUTE_NAME_MIN_PX == 28


def test_span_air_rule_keeps_one_and_a_half_line_gaps_even_at_the_bare_lean() -> None:
    font = graphics.get_font("Montserrat", 30, weight=600)
    for inner in (10, 14, 18, 20, 24, 30):
        above, _below = graphics.span_air_rule(34, font, inner, False)
        assert above >= 1.5 * inner, (inner, above)
        sep, _b = graphics.span_air_rule(34, font, inner, True)
        assert sep >= 2 * inner
    # a fallback in a band that holds 1.5 x but not 2 x the inner air takes 1.5 x, not less
    assert graphics.span_fallback_y(400, 480, 22, 30) == pytest.approx(480 - (58 - 47))   # 45 + the overshoot


TL_CASES = {
    "derinkuyu": DERINKUYU,
    "four_d": [{"year": -1200, "approximate": True, "label": "First chambers cut"},
               {"year": 780, "approximate": True, "label": "Expanded against raiders"},
               {"year": 1100, "label": "Churches carved"}, {"year": 1963, "label": "Rediscovered behind a wall"}],
    "four_d_alt": [{"year": -1200, "approximate": True, "label": "First chambers cut into rock"},
                   {"year": 780, "approximate": True, "label": "Expanded against raiders"},
                   {"year": 1100, "label": "Churches carved"}, {"year": 1963, "label": "Rediscovered behind a wall"}],
    "six": SIX,
    "seven": [{"year": y, "label": f"Event of {y}"} for y in (100, 200, 300, 400, 500, 600, 700)],
    "two_xl": [{"year": 1923, "label": XL}, {"year": 1925, "label": XL}],
    "two_far": [{"year": -3000, "approximate": True, "label": "Founded"},
                {"year": 1963, "label": "Rediscovered behind a wall"}],
    "two_line": TWO_LINE,
    "four_xl_close": [{"year": 1900 + i, "label": XL} for i in range(4)],
    "four_xl_mixed": [{"year": -9000, "approximate": True, "label": XL}, {"year": -8999, "label": "X"},
                      {"year": 1, "label": XL}, {"year": 2, "approximate": True, "label": XL}],
    "three_xxl": [{"year": 1500, "label": XL + " and of the wider empire beyond the mountains"},
                  {"year": 1700, "label": "Short"}, {"year": 1701, "label": XL}],
    "circa_all": [{"year": -12000, "approximate": True, "label": "Earliest traces"},
                  {"year": -11000, "approximate": True, "label": "Second phase"},
                  {"year": -500, "approximate": True, "label": LONG},
                  {"year": -499, "approximate": True, "label": LONG}],
    "five_close": FIVE_CLOSE,
}


@pytest.mark.parametrize("case", sorted(TL_CASES))
def test_wide_timeline_spans_never_collide_are_never_dropped_and_everything_is_title_safe(tmp_path, monkeypatch,
                                                                                          case) -> None:
    # review 5: "ABOUT 320 YEARS LATER" overprinted "FIRST CHAMBERS CUT"; bottom-row labels ran under y = 820
    events = graphics.timeline_events(TL_CASES[case])
    st = _refused_or_render(monkeypatch, f"tl/{case}-16x9",
                            lambda: graphics.render_timeline(TL_CASES[case], tmp_path / "t.mp4", 1920, 1080))
    if st is None:   # QF-23: refused rather than drawn with its cut labels
        return
    plates = st["plates"]
    hits = [(a, b) for (a, pa), (b, pb) in itertools.combinations(plates, 2)
            if not (a.startswith("label") and a == b) and graphics.LayoutConstraintSolver.boxes_intersect(pa.box, pb.box)]
    assert not hits, (case, hits)
    want = {k for k in range(1, len(events)) if graphics.gap_text(events[k - 1], events[k])}
    assert {int(n[3:].split("_")[0]) for n, _p in plates if n.startswith("gap")} == want, case   # never dropped
    x0, y0, x1, y1 = graphics.WIDE_TITLE_SAFE
    out = [(n, p.box) for n, p in plates if not (x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1]
                                                 and p.box[3] <= y1)]
    assert not out, (case, out)
    blocks: dict[int, list[Any]] = {}
    for n, p in plates:
        if n.startswith(("year", "label")):
            blocks.setdefault(int(n[4] if n.startswith("year") else n[5]), []).append(p.box)
    for n, p in plates:
        if n.startswith("gap"):                       # at least the blocks' own 24 px from every block
            assert all(graphics.box_clearance(p.box, graphics._union(b)) >= 24 - 1 for b in blocks.values()), \
                (case, n, p.text)
    labels = [p for n, p in plates if n.startswith("label")]
    assert min(p.font.size for p in labels) >= 34


def test_wide_timeline_labels_wrap_only_where_their_column_ends(tmp_path, monkeypatch) -> None:
    # review 5: 2 px of tracking per character of the whole label came off every line ("EXPANDED / AGAINST RAIDERS")
    st = _render_fast(monkeypatch, lambda: graphics.render_timeline(DERINKUYU, tmp_path / "t.mp4", 1920, 1080))
    rows = {k: [p.text for n, p in st["plates"] if n == f"label{k}"] for k in range(4)}
    # round 6 (deliberate): in the 420 px column, at most three balanced rows (two at the wider measure before)
    assert len(rows[2]) <= graphics.WIDE_LABEL_ROWS and " ".join(rows[2]) == "ABANDONED AFTER THE POPULATION EXCHANGE"
    assert not any("…" in t for ts in rows.values() for t in ts)


@pytest.mark.parametrize("case", sorted(TL_CASES))
def test_tall_spans_keep_one_and_a_half_line_gaps_under_a_wrapped_label(tmp_path, monkeypatch, case) -> None:
    # review 5: a span set at the bare lean sat 22 px under a two-line label whose line gap is 18 px
    st = _refused_or_render(monkeypatch, f"tl/{case}-9x16",
                            lambda: graphics.render_timeline(TL_CASES[case], tmp_path / "t.mp4"))
    if st is None:   # QF-23: refused rather than drawn with its cut labels
        return
    for n, p in st["plates"]:
        if not n.startswith("gap"):
            continue
        k = int(n[3:])
        lines = sorted((q for m, q in st["plates"] if m == f"label{k - 1}"), key=lambda q: q.box[1])
        if len(lines) < 2 or p.box[1] < lines[-1].box[3]:
            continue                                  # one line, or set beside the entries (free_span_xy)
        leading = min(b.box[1] - a.box[3] for a, b in itertools.pairwise(lines))
        assert p.box[1] - lines[-1].box[3] >= 1.5 * leading, (case, n, p.box[1] - lines[-1].box[3], leading)
    _assert_clear(st["plates"])


# ============================================================================ round 4 review 6 (2026-11-03)
def test_fit_tracked_relaxes_its_tracking_reserve_at_each_size() -> None:
    # review 6: the reserve came from the lines wrapped at 38 px and was never relaxed: "POPULATI…" at 270 px
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, font, short = graphics.fit_tracked(probe, "POPULATION 1923 CARVED", "Montserrat", 38, 270, 3, 34, 3)
    assert not short and lines == ["POPULATION", "1923 CARVED"], lines
    assert all(graphics.tracked_width(ln, font, 3) <= 270 for ln in lines)
    assert font.size == 34


_ORACLE_PROBE = ImageDraw.Draw(Image.new("L", (8, 8)))


def _greedy_rows(words: list[str], font, width: float, track: float) -> int | None:
    """The fewest lines `words` take at `width`, each measured tracked (an independent greedy oracle), or None
    where a word alone is wider."""
    def w_of(t: str) -> float:
        return _ORACLE_PROBE.textlength(t, font=font) + track * max(0, len(t) - 1)
    rows, cur = 0, ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if not cur or w_of(trial) <= width:
            cur = trial
        else:
            rows, cur = rows + 1, w
        if w_of(cur) > width:
            return None
    return rows + 1


FIT_VOC = ("first chambers cut expanded against raiders churches carved rediscovered behind a wall abandoned after "  # noqa: SIM905
           "the population exchange walls rebuilt by the emperor opened to visitors constantinople underground "
           "mausoleum of in on it halicarnassus fortifications").split()


def test_fit_tracked_never_cuts_a_label_that_fits_over_1500_labels_at_712_px() -> None:
    # review 6: the 9:16 label measure; rows 2..TALL_LABEL_ROWS_MAX as the timeline tries them
    import random
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    rnd = random.Random(6)
    false_cuts, midword = [], []
    for _ in range(1500):
        text = graphics.drawable(" ".join(rnd.choice(FIT_VOC) for _ in range(rnd.randint(4, 30))).upper(),
                                 "Montserrat")
        words = text.split()
        for rows in range(2, graphics.TALL_LABEL_ROWS_MAX + 1):
            # QF-23: the search behind fit_tracked (which refuses where this trial cuts), as the timeline tries it
            lines, font, short = graphics._probe_tracked(probe, text, "Montserrat", 42, 712, rows, 34, 2,
                                                         weight=800, quiet=True)
            if not short:
                assert " ".join(lines) == text
                assert len(lines) <= rows and all(graphics.tracked_width(ln, font, 2) <= 712 for ln in lines)
                break
            last = lines[-1].removesuffix("…").split()[-1]
            if last not in words:
                midword.append(lines)
        least = [got for px in range(42, 33, -2)
                 if (got := _greedy_rows(words, graphics.get_font("Montserrat", px, weight=800), 712, 2))]
        oracle = min(least) if least and min(least) <= graphics.TALL_LABEL_ROWS_MAX else None
        if short and oracle is not None:
            false_cuts.append(text)
    assert not false_cuts, false_cuts[:3]
    assert not midword, midword[:3]


def test_fit_tracked_cuts_only_at_a_word_boundary() -> None:
    # QF-23: the timeline layouts' trial (_probe_tracked) still scores a cut candidate at a word boundary; the fit a
    # label is drawn from (fit_tracked) refuses it instead
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    text = "POPULATION EXCHANGE REDISCOVERED HALICARNASSUS"
    lines, _font, short = graphics._probe_tracked(probe, text, "Montserrat", 34, 300, 2, 34, 2, weight=800, quiet=True)
    assert short and lines[-1].endswith("…")
    assert all(w.removesuffix("…") in {"POPULATION", "EXCHANGE", "REDISCOVERED", "HALICARNASSUS"}
               for ln in lines for w in ln.split())
    with pytest.raises(graphics.TextFitError, match="Checked copy does not fit"):
        graphics.fit_tracked(probe, text, "Montserrat", 34, 300, 2, 34, 2, weight=800, quiet=True)
    # a first word wider than the measure is set smaller, never broken (the trial); fit_tracked refuses it
    one, f1, cut = graphics._probe_tracked(probe, "HALICARNASSUS", "Montserrat", 38, 250, 1, 34, 2, weight=800,
                                           quiet=True)
    assert cut and one == ["HALICARNASSUS"] and graphics.tracked_width(one[0], f1, 2) <= 250
    with pytest.raises(graphics.TextFitError, match="Checked copy does not fit"):
        graphics.fit_tracked(probe, "HALICARNASSUS", "Montserrat", 38, 250, 1, 34, 2, weight=800, quiet=True)


def test_tracked_wrap_keeps_no_lone_first_word_where_a_break_avoids_it() -> None:
    font = graphics.get_font("Montserrat", 34, weight=800)
    text = "FIRST CHAMBERS CUT INTO ROCK"
    width = graphics.tracked_width("CHAMBERS CUT INTO", font, 2) + 2
    lines = graphics.tracked_wrap(text, font, width, 2)
    assert lines and len(lines[0].split()) > 1, lines
    assert all(graphics.tracked_width(ln, font, 2) <= width for ln in lines)
    assert graphics.tracked_wrap("HALICARNASSUS", font, 100, 2) is None          # a word wider than the measure


# The reviewer's timeline fuzz (rev11/tl11.py, FUZZ=40): the same seed, vocabulary and draws
BASE_VOC = ("first chambers cut expanded against raiders churches carved rediscovered behind a wall abandoned after "  # noqa: SIM905
            "the population exchange walls rebuilt by the emperor opened to visitors").split()
# round 4 review 7: the reviewer's long-word vocabulary (rev12/tl12.py, SEED=1203 YLO=-12000 WMAX=12), recovered
# from its repro sets (nz27, nz39: the same 34 words in the same order give the same draws)
LONG_VOC = ("first chambers cut expanded against raiders churches carved rediscovered behind a wall abandoned after "  # noqa: SIM905
            "the population exchange constantinople halicarnassus mausoleum fortifications 1923 earthquake of in "
            "sultan's court byzantine-era cisterns flooded by the sea-wall reconstructed").split()


def _fuzz_sets(seed: int, voc: list[str], count: int = 40, ylo: int = -3000, wmax: int = 9,
               prefix: str = "fz") -> dict[str, list[dict[str, Any]]]:
    """The reviewer harness's draws (tl11/tl12): `count` sets of 3-6 events from `ylo` to 2019, each label 2 to
    `wmax` words of `voc`, about 30 % circa."""
    import random
    rnd = random.Random(seed)
    out = {}
    for i in range(count):
        n = rnd.randint(3, 6)
        ys = sorted(rnd.sample(range(ylo, 2020), n))
        out[f"{prefix}{i}"] = [{"year": y or 1, "approximate": rnd.random() < 0.3,
                                "label": " ".join(rnd.choice(voc) for _ in range(rnd.randint(2, wmax))).capitalize()}
                               for y in ys]
    return out


def _fuzz_timelines() -> dict[str, list[dict[str, Any]]]:
    return _fuzz_sets(11, BASE_VOC)


FUZZ_TL = _fuzz_timelines()
# Round 4 review 7 (2026-11-03): the rules as properties over several seeds and vocabularies: seed 11 (above),
# the reviewer's seed 1203 with the long words (nz18/23/33/39 ran out of title-safe, nz22/23/27/36 set a span
# level with a year), and two seeds of ours: long words over a deep range, and the base words crowded into
# 500 years (short gaps, tight spans).
# Round 4 review 8: the second reviewer's seed 6061 with its own long words (rev13_1/voc_new.txt, WMAX=11 from
# 9000 BC); the sets it named (qz9 stacked every label a word a line, qz0 the slowest search) and those whose span
# sat under a block (qz12, qz14)
QZ_VOC = ("archaeologists excavated the amphitheatre necropolis basilica of saint sophia restored after crusaders "  # noqa: SIM905
          "sacked hippodrome aqueduct collapsed during earthquake mosaics uncovered beneath plaster caravanserai "
          "monastery hermits frescoes whitewashed iconoclasm 1453 1071 seljuk ottoman-era garrison besieged "
          "reinforced dismantled for stone unesco listed tourists arrive").split()
# Round 4 review 9 (2026-11-03): the reviewer's realistic Babylon and Ephesus sets (babylon4's "GERMAN EXCAVATIONS"
# ran out of title-safe: a line ending in S overshoots its advance by 5-6 px, and every layout was judged outside),
# a family whose labels end their lines in S at the frame's right edge (seed 8080 se8 ran to x 1768 before), and
# the third reviewer's seed 5150 (xv1/18/21 shrank the 9:16 years "to keep 4 entries whole" and cut a label all the
# same; xv15's last-resort span read as part of the label row)
BABYLON_TL = {
    "babylon5": [{"year": -2100, "approximate": True, "label": "Ziggurat of the moon god raised"},
                 {"year": -605, "label": "Nebuchadnezzar rebuilds the walls"},
                 {"year": -539, "label": "Persians take the city"}, {"year": -323, "label": "Alexander dies here"},
                 {"year": 1899, "label": "German excavations begin at the Ishtar gate"}],
    "babylon4": [{"year": -2100, "approximate": True, "label": "Ziggurat of the moon god raised"},
                 {"year": -605, "label": "Nebuchadnezzar rebuilds the walls"},
                 {"year": -323, "label": "Alexander dies here"},
                 {"year": 1899, "label": "German excavations begin at the Ishtar gate"}],
    "babylon3": [{"year": -605, "label": "Nebuchadnezzar rebuilds the walls"},
                 {"year": -323, "label": "Alexander dies here"},
                 {"year": 1899, "label": "German excavations begin at the Ishtar gate"}],
    "ephesus4": [{"year": -550, "approximate": True, "label": "Temple of Artemis built"},
                 {"year": -356, "label": "Temple burned by Herostratus"}, {"year": 262, "label": "Goths sack the city"},
                 {"year": 1869, "label": "Wood finds the temple's site"}],
}
S_VOC = ("excavations walls ruins temples towers columns fortifications cisterns mosaics frescoes baths tombs gates "  # noqa: SIM905
         "arches aqueducts palaces citadels ramparts catacombs begins rebuilds the of").split()
XV_VOC = ("ziggurat nebuchadnezzar's assyrian ashurbanipal cuneiform tablets library burned looted reassembled by the "  # noqa: SIM905
          "british museum hanging gardens irrigation canals silted river euphrates shifted course 539 conquered "
          "persians alexander died here ishtar gate glazed-brick lions rebuilt saddam-era bricks stamped a of in "
          "and").split()
XV_TL = _fuzz_sets(5150, XV_VOC, 24, -10000, 10, "xv")
FUZZ_FAMILIES = {
    "bb": BABYLON_TL,
    "se": _fuzz_sets(8080, S_VOC, 16, 1500, 6, "se"),
    "xv": {k: v for k, v in XV_TL.items() if k in ("xv1", "xv15", "xv18", "xv21")},
    "nz": _fuzz_sets(1203, LONG_VOC, 40, -12000, 12, "nz"),
    "lw": _fuzz_sets(4242, LONG_VOC, 16, -9000, 12, "lw"),
    "cr": _fuzz_sets(97, BASE_VOC, 16, 1500, 9, "cr"),
    "qz": {k: v for k, v in _fuzz_sets(6061, QZ_VOC, 30, -9000, 11, "qz").items()
           if k in ("qz0", "qz4", "qz9", "qz12", "qz14", "qz26")},
}
# QF-23 (checked copy never truncated): the committed timeline sets, by size, whose labels no layout holds whole
# (docs/reviews/quality-fixes-2026-11/QF-23/timeline_refusals.py lists them: 63 of 370 renders, all fuzz, XL or xv
# stress sets). Each is refused (TextFitError) where the base drew it with its cut labels logged; every other set
# renders as before.
# QF-24 (decision QF-24/QF-87 layout, 2026-11-13: keep the approved rectangle, fix the surfaces): re-taken on the
# shared organic Reel box (label column to x 848, 40 px narrower; foot 1090, 32 px lower), 66 of 370 renders, all
# 9:16 changes. Newly refused: cr/cr14, fz/fz11, lw/lw0, nz/nz9, nz/nz26, nz/nz32, nz/nz37, qz/qz26, se/se1,
# tl/five_close, xv/xv7, xv/xv17, xv/xv18; drawn whole now (the taller column): cr/cr9, fz/fz2, fz/fz17, fz/fz26,
# fz/fz37, lw/lw11, nz/nz6, nz/nz10, qz/qz0, xv/xv11 (docs/reviews/quality-fixes-2026-11/QF-24/census_*.json).
QF23_REFUSED: frozenset[str] = frozenset({
    "cr/cr14-9x16", "fz/fz11-9x16", "fz/fz9-9x16", "lw/lw0-9x16", "lw/lw12-9x16", "lw/lw13-9x16", "lw/lw2-9x16",
    "lw/lw4-9x16", "lw/lw6-9x16", "lw/lw9-16x9", "lw/lw9-9x16", "nz/nz0-9x16", "nz/nz11-9x16", "nz/nz14-16x9",
    "nz/nz14-9x16", "nz/nz15-16x9", "nz/nz15-9x16", "nz/nz16-9x16", "nz/nz18-9x16", "nz/nz2-16x9", "nz/nz2-9x16",
    "nz/nz22-9x16", "nz/nz23-16x9", "nz/nz23-9x16", "nz/nz24-16x9", "nz/nz24-9x16", "nz/nz25-16x9", "nz/nz25-9x16",
    "nz/nz26-9x16", "nz/nz27-9x16", "nz/nz3-9x16", "nz/nz31-9x16", "nz/nz32-9x16", "nz/nz35-9x16", "nz/nz36-16x9",
    "nz/nz36-9x16", "nz/nz37-9x16", "nz/nz38-9x16", "nz/nz39-9x16", "nz/nz5-16x9", "nz/nz5-9x16", "nz/nz9-9x16",
    "qz/qz12-16x9", "qz/qz12-9x16", "qz/qz14-9x16", "qz/qz26-9x16", "qz/qz4-16x9", "qz/qz4-9x16", "qz/qz9-16x9",
    "qz/qz9-9x16", "se/se1-9x16", "tl/five_close-9x16", "tl/four_xl_close-16x9", "tl/four_xl_close-9x16",
    "tl/four_xl_mixed-16x9", "tl/four_xl_mixed-9x16", "xv/xv0-9x16", "xv/xv1-9x16", "xv/xv10-9x16", "xv/xv17-9x16",
    "xv/xv18-9x16", "xv/xv21-16x9", "xv/xv21-9x16", "xv/xv22-9x16", "xv/xv6-9x16", "xv/xv7-9x16",
})
NAMED_TL = {"derinkuyu": DERINKUYU, "two_line4": TWO_LINE,
            "four_d": [{"year": -1200, "approximate": True, "label": "First chambers cut"},
                       {"year": 780, "approximate": True, "label": "Expanded against raiders"},
                       {"year": 1100, "label": "Churches carved"},
                       {"year": 1963, "label": "Rediscovered behind a wall"}],
            "two_xl": [{"year": 1923, "label": XL}, {"year": 1925, "label": XL}],
            "three_xxl": [{"year": 1500, "label": XL + " and of the wider empire beyond the mountains"},
                          {"year": 1700, "label": "Short"}, {"year": 1701, "label": XL}]}


def _label_line_gap(lines: list[Any], leading: float = 1.3) -> float:
    """A label's own line gap: the ink gap between its lines, or for one line its `leading` (1.3 on 16:9; 9:16 passes
    graphics.TALL_LEADING, 1.15 since the 2026-11-03 hierarchy spec) less its cap."""
    lines = sorted(lines, key=lambda p: p.box[1])
    if len(lines) > 1:
        return min(b.box[1] - a.box[3] for a, b in itertools.pairwise(lines))
    f = lines[0].font
    return round(f.size * leading) - graphics.cap_height(f)


def _spy_spine(monkeypatch: pytest.MonkeyPatch, st: dict[str, Any]) -> None:
    """Record the spine's place and its nodes (graphics.spine_marks' arguments) into `st["spine"]`."""
    real = graphics.spine_marks

    def spy(size, along_x, fixed, a0, a1, lw, nodes, *a, **k):
        st["spine"] = {"along_x": along_x, "fixed": fixed, "nodes": [n[0] for n in nodes]}
        return real(size, along_x, fixed, a0, a1, lw, nodes, *a, **k)
    monkeypatch.setattr(graphics, "spine_marks", spy)


def _logged_labels(log_text: str, marker: str) -> set[str]:
    """The labels a 16:9 last-resort log line names (each line names its labels, " | " between them)."""
    out: set[str] = set()
    for ln in log_text.splitlines():
        if marker in ln:
            out.update(ln.split(marker, 1)[1].strip().removesuffix(".").split(" | "))
    return out


def _timeline_checks(st: dict[str, Any], events, size: tuple[int, int], log_text: str) -> dict[str, Any]:
    plates = st["plates"]
    tall = size[1] > size[0]
    box = (graphics.safe_zones.graphics_box(*size) if tall
           else tuple(round(v * size[0] / 1920) for v in graphics.WIDE_TITLE_SAFE))
    out = [(n, p.box) for n, p in plates if not graphics.safe_zones.inside(tuple(p.box), box)]
    assert not out, out                                                   # review 6 D: every plate in the box
    hits = [(a, b) for (a, pa), (b, pb) in itertools.combinations(plates, 2)
            if not (a == b and a.startswith("label")) and not (a.startswith("gap") and a.split("_")[0] == b.split("_")[0])
            and graphics.LayoutConstraintSolver.boxes_intersect(pa.box, pb.box)]
    assert not hits, hits
    labels: dict[int, list[Any]] = {}
    for n, p in plates:
        if n.startswith("label"):
            labels.setdefault(int(n[5:]), []).append(p)
    kept = graphics.timeline_events(events)
    for k, ps in labels.items():                                          # a cut falls on a word boundary
        words = set(graphics.drawable(kept[k]["label"].upper(), "Montserrat").split())
        for p in ps:
            assert all(w.removesuffix("…").rstrip(",;:-") in words for w in p.text.split()), p.text
    spans: dict[int, list[Any]] = {}
    for n, p in plates:
        if n.startswith("gap"):
            spans.setdefault(int(n[3:].split("_")[0]), []).append(p)
    assert set(spans) == {k for k in range(1, len(kept)) if graphics.gap_text(kept[k - 1], kept[k])}   # never dropped
    years = {n: p for n, p in plates if n.startswith("year")}
    for k, ps in spans.items():                                           # review 6 C: 1.5 x the label's line gap
        u = graphics._union([p.box for p in ps])
        if tall:
            air = u[1] - max(p.box[3] for p in labels[k - 1])
            gap = _label_line_gap(labels[k - 1], graphics.TALL_LEADING)    # 2026-11-03: 1.15, not 1.3, on 9:16
            assert air >= 1.5 * gap, (k, air, gap)
        else:
            for j, lp in labels.items():
                block = graphics._union([p.box for p in lp] + [p.box for n, p in years.items() if n.split("_")[0] == f"year{j}"])
                air = graphics.box_clearance(u, block)
                assert air >= 1.5 * _label_line_gap(lp), (k, j, air, _label_line_gap(lp))
        for p in ps:                                                      # review 6 H / 7: no span line level with a year
            assert not any(p.box[1] < y.box[3] and y.box[1] < p.box[3] for y in years.values()), (p.text, k)
    cut = [p.text for ps in labels.values() for p in ps if "…" in p.text]
    # review 7: never a label cut to a single word while another layout keeps two (logged where none does).
    # Review 9 (2026-11-03): the log excuses the labels it names only (any such line excused every label)
    one_word = [k for k, ps in labels.items() if any("…" in p.text for p in ps)
                and len(" ".join(p.text for p in ps).replace("…", "").split()) < 2]
    single_named = _logged_labels(log_text, "label(s) cut to a single word: no layout keeps two: ")
    for k in one_word:
        assert graphics.drawable(kept[k]["label"].upper(), "Montserrat") in single_named, (k, single_named)
    year_cap = min(graphics.cap_height(p.font) for n, p in years.items() if not n.endswith("_circa"))
    label_cap = max(graphics.cap_height(p.font) for ps in labels.values() for p in ps)
    if tall:                                                              # review 6 I: the years dominate
        floor = graphics.TALL_YEAR_RATIO if "years set under" not in log_text else graphics.TALL_YEAR_RATIO_MIN
        if "the last resort" in log_text or "set at the tightest" in log_text:
            floor = graphics.TALL_YEAR_RATIO_LAST      # round 7 (deliberate, R3): the spans' rows' last resort
        assert year_cap >= floor * label_cap, (year_cap, label_cap)
        assert not cut or "a label cut" in log_text
        bottom = max(p.box[3] for _n, p in plates)
        # 2026-11-03 hierarchy spec: inside the box, and on its foot unless the years are at their starting size
        # and every gap between entries is at its TALL_GAP_CAP x cap limit (the spare height stays under it)
        assert bottom <= box[3], (bottom, box)
        third = (len(kept) > 2 and max(len(ps) for ps in labels.values()) >= 3
                 and "Timeline copy-fit" not in log_text)
        if third:   # round 6 review (deliberate, spec F give-way 5): a third label row only at the least ratio
            assert year_cap < graphics.TALL_YEAR_RATIO * label_cap + 1 and "Timeline years at" in log_text
        if bottom < box[3] - 3 and not third:
            year_px = min(p.font.size for n, p in years.items() if not n.endswith("_circa"))
            # round 6 (deliberate): the starting sizes 128 / 150 / 190 (104 / 120 / 180 before)
            assert year_px == (128 if len(kept) >= 4 else 150 if len(kept) == 3 else 190), (bottom, year_px)
            for k in range(1, len(kept)):
                top_k = min(y.box[1] for n, y in years.items() if n.split("_")[0] == f"year{k}")
                gap = top_k - max(p.box[3] for p in labels[k - 1])
                assert gap >= graphics.TALL_GAP_CAP * year_cap - 3, (k, gap, year_cap)
    else:
        assert not cut or "label(s) cut" in log_text                      # review 7: every last resort logged
        cut_named = _logged_labels(log_text, "label(s) cut: no measure holds them whole: ")
        for k, ps in labels.items():                                      # review 9: each cut label by name
            if any("…" in p.text for p in ps):
                assert graphics.drawable(kept[k]["label"].upper(), "Montserrat") in cut_named, (k, cut_named)
        # review 8: a logged lone word excuses that label only (one log switched the check off for them all)
        lone_log = next((ln for ln in log_text.splitlines() if "start with a lone word" in ln), "")
        for k, ps in labels.items():                                      # review 6 H: no lone short first word
            if graphics.drawable(kept[k]["label"].upper(), "Montserrat") in lone_log:
                continue
            ps = sorted(ps, key=lambda p: p.box[1])
            if len(ps) > 1 and len(ps[0].text.split()) == 1:
                assert not (len(ps) >= 3 and all(len(p.text.split()) == 1 for p in ps)), [p.text for p in ps]
                assert (ps[0].box[2] - ps[0].box[0]) >= graphics.WIDE_LONE_SHARE * max(p.box[2] - p.box[0]
                                                                                       for p in ps[1:]), \
                    [p.text for p in ps]
    spine = st.get("spine")
    if spine:
        _span_reading_checks(spans, labels, years, spine, size, log_text,
                             {k: graphics.gap_text(kept[k - 1], kept[k]) for k in spans})
    return {"cut": cut}


def _span_reading_checks(spans, labels, years, spine, size, log_text, texts) -> None:
    """Round 4 review 7 (2026-11-03): each span reads as its own segment's time: between its two events, leaning
    toward the later one, never over the earlier event's year, never on a label line's baseline beside it, and by
    the spine (9:16: in the band between the earlier label and the later year) unless a last resort is logged."""
    tall = size[1] > size[0]
    nodes, fixed = spine["nodes"], spine["fixed"]
    k_ = size[0] / 1920
    for k, ps in spans.items():
        u = graphics._union([p.box for p in ps])
        if tall:
            top = max(p.box[3] for p in labels[k - 1])
            year_top = min(y.box[1] for n, y in years.items() if n.split("_")[0] == f"year{k}")
            # review 8: only a span its own fallback log names is excused ("Timeline spans set without their
            # full separation", which fires on tl4, switched the check off for every span)
            if f"Timeline span {' '.join(p.text for p in ps)!r}" not in log_text:   # in its band, leaning later
                assert top < u[1] and u[3] < year_top, (k, u, top, year_top)
                assert (u[1] + u[3]) / 2 >= (top + year_top) / 2 - 1, (k, u, top, year_top)   # leans later
            continue
        a, b = nodes[k - 1], nodes[k]
        assert a < u[0] and u[2] < b, (k, u, a, b)                         # between its two events
        assert (u[0] + u[2]) / 2 >= (a + b) / 2 - 3 * k_, (k, u, a, b)    # toward the later one (ink: +-3 px)
        above = (u[1] + u[3]) / 2 < fixed
        earlier_above = (k - 1) % 2 == 0
        if above == earlier_above:                                       # never over the earlier event's year
            yb = graphics._union([y.box for n, y in years.items() if n.split("_")[0] == f"year{k - 1}"])
            assert u[2] <= yb[0] or yb[2] <= u[0], (k, u, yb)
        near = graphics.WIDE_SPAN_LEVEL_NEAR * k_
        for p in ps:                                                      # no baseline shared with a label line
            for lp in (q for qs in labels.values() for q in qs):
                if p.box[1] < lp.box[3] and lp.box[1] < p.box[3]:
                    assert graphics.box_clearance(p.box, lp.box) >= near, (p.text, lp.text)
        off = min(abs(u[1] - fixed), abs(u[3] - fixed))
        if f"the span {texts[k]!r} set further from the spine" not in log_text:      # review 8: that span only
            assert off <= (graphics.WIDE_SPAN_OFF + 2) * k_ + 2, (k, off)
        else:                       # review 9: and there it never reads as part of a label row (xv15: 100 px)
            row_near = graphics.WIDE_SPAN_ROW_NEAR * k_
            for p in ps:
                for lp in (q for qs in labels.values() for q in qs):
                    if p.box[1] < lp.box[3] and lp.box[1] < p.box[3]:
                        assert graphics.box_clearance(p.box, lp.box) >= row_near - 1, (p.text, lp.text)


def _render_checked(monkeypatch, caplog, tmp_path, events, size, refusal_ok: bool = False) -> dict[str, Any]:
    """The render with every property check. QF-23: a set whose chosen layout still cuts a label is refused
    (typography.TextFitError) and nothing is drawn; only a caller that expects that refusal (`refusal_ok`) gets it
    back, as {"refused": its message}; every other caller fails on it as before."""
    st: dict[str, Any] = {}
    _spy_spine(monkeypatch, st)
    try:
        with caplog.at_level("INFO"):
            got = _render_fast(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", *size))
    except graphics.TextFitError as e:
        if not refusal_ok:
            raise
        assert str(e).startswith("Checked copy does not fit the ") and "timeline" in str(e), e
        assert not list(tmp_path.glob("t.mp4*")), "a refused timeline writes nothing"
        return {"refused": str(e)}
    got["spine"] = st.get("spine")
    return {**got, "checks": _timeline_checks(got, events, size, caplog.text)}


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(FUZZ_TL, key=lambda c: int(c[2:])))
def test_timeline_fuzz_sets_keep_every_rule(tmp_path, monkeypatch, caplog, case, size) -> None:
    # review 6 (the reviewer's 40 fuzz sets): 16:9 cut labels in 10 sets at HEAD (fz8, fz2, fz9, fz16, fz38 with a
    # whole layout to spare), spans sat 23-24 px under 18-20 px line gaps, 9:16 labels ran 1-2 px out of the box
    events = FUZZ_TL[case]
    tag = "9x16" if size[1] > size[0] else "16x9"
    full = _render_checked(monkeypatch, caplog, tmp_path, events, size, refusal_ok=True)
    assert ("refused" in full) == (f"fz/{case}-{tag}" in QF23_REFUSED), (case, tag, full.get("refused"))
    if "refused" in full:   # QF-23: refused rather than drawn with its cut labels
        return
    got = full["checks"]
    if size[0] > size[1]:
        assert not got["cut"], (case, got["cut"])                         # review 6 A: none cut on 16:9
        assert "lone word" not in caplog.text                             # and no lone first word either


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", [f"{fam}/{c}" for fam, sets in FUZZ_FAMILIES.items() for c in sets])
def test_timeline_fuzz_properties_over_seeds_and_vocabularies(tmp_path, monkeypatch, caplog, case, size) -> None:
    # review 7: every rule on every set of the reviewer's seed 1203 (long words) and two more seeds, both sizes:
    # inside the safe box, no overlaps, no span level with a year, spans never dropped and 1.5 x the line gap, no
    # mid-word or single-word cut, the span's reading (_span_reading_checks) and every last resort logged
    # QF-23: the sets in QF23_REFUSED (the base drew each with its cut labels logged) are refused, and only they
    fam, name = case.split("/")
    got = _render_checked(monkeypatch, caplog, tmp_path, FUZZ_FAMILIES[fam][name], size, refusal_ok=True)
    tag = "9x16" if size[1] > size[0] else "16x9"
    assert ("refused" in got) == (f"{case}-{tag}" in QF23_REFUSED), (case, tag, got.get("refused"))


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(NAMED_TL))
def test_named_timelines_keep_every_rule_and_stay_whole(tmp_path, monkeypatch, caplog, case, size) -> None:
    # review 6: two_line4's "5 YEARS LATER" 24 px under a 20 px line gap; four_d's "FIRST / CHAMBERS CUT"; the long
    # labels whole wherever a wider measure holds them
    events = NAMED_TL[case]
    got = _render_checked(monkeypatch, caplog, tmp_path, events, size)["checks"]
    assert not got["cut"], (case, got["cut"])
    assert "lone word" not in caplog.text


def test_tall_years_are_the_hero_again(tmp_path, monkeypatch) -> None:
    # review 6 I: "C. 1200 BC" fell from a 104 px Anton hero to 62 px, near the labels' size class, so the spans
    # could keep their bands; the spans give way first now
    st = _render_fast(monkeypatch, lambda: graphics.render_timeline(NAMED_TL["four_d"], tmp_path / "t.mp4"))
    years = [p for n, p in st["plates"] if n.startswith("year") and not n.endswith("circa")]
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    assert min(p.font.size for p in years) >= 95
    assert min(graphics.cap_height(p.font) for p in years) >= 3 * max(graphics.cap_height(p.font) for p in labels)


def test_span_fallback_keeps_one_and_a_half_line_gaps_when_the_band_is_short() -> None:
    # review 6 C: "the most the band gives" set fz35's span 23 px under an 18 px line gap
    least = graphics.span_least_above(20)
    assert graphics.span_fallback_y(400, 440, 22, 20) == 400 + least + 22
    assert graphics.span_fallback_y(400, 440, 22, 20) - 22 - 400 >= 1.5 * 20


def test_a_one_line_label_still_sets_its_span_one_and_a_half_line_gaps_under_it() -> None:
    entries = [{"inner": 18, "line": 20}, {"inner": 19, "line": 20}]
    assert graphics.span_basis(entries) == 20                             # the line gap, not the year-to-label air
    assert graphics.span_basis([{"inner": 37, "line": 25}]) == 37


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", ["six_spread", "sw_corner", "five", "corners"])
def test_route_names_are_clearly_their_own_stops_outside_a_cluster(tmp_path, monkeypatch, edge_geo, caplog,
                                                                   case, size) -> None:
    # review 6 E: a vague place counted as named and the first layout naming everything won, so on 16:9 six_spread
    # HALICARNASSUS sat nearer another stop than its own and stacked under SMYRNA as its second line
    routes = {**ROUTES, **EDGE_ROUTES,
              "six_spread": [_stop("Izmir", "Smyrna"), _stop("Bodrum", "Halicarnassus"), _stop("Antalya", "Attaleia"),
                             _stop("Konya", "Iconium"), _stop("Kars", "Kars"), _stop("Trabzon", "Trebizond")]}
    stops = graphics.route_stops(routes[case])
    seen = _stop_points(monkeypatch)
    with caplog.at_level("INFO", logger=graphics.log.name):
        st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                     height=size[1]))
    pts = list(dict.fromkeys(seen))[:len(stops)]
    # round 4 review 7: only stops under ROUTE_CLUSTER_PX apart are a cluster. Review 8 (2026-11-03): the last
    # resort is a property, not a fixed case (this test pinned six_spread 9:16 HALICARNASSUS as one): outside a
    # cluster a name is never nearer another stop than its own; one nearer its own by under ROUTE_NAME_MARGIN, or a
    # stop left unnamed, is logged at WARNING
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    last_resort = {k for k in range(len(pts))
                   if f"name of stop {k} ({stops[k]['label']}) is nearer its own stop by under" in warned}
    unnamed = {k for k in range(len(pts)) if f"label of stop {k} ({stops[k]['label']}); the stop is drawn unlabelled"
               in warned}
    clusters = {k for k in range(len(pts))
                if f"name of stop {k} ({stops[k]['label']}) is as near another stop as its own: a cluster" in caplog.text}
    for k in clusters:                                                    # a cluster is a genuine one
        assert min(math.dist(pts[k], q) for j, q in enumerate(pts) if j != k) < graphics.ROUTE_CLUSTER_PX
    assert len(pts) - 1 not in unnamed, case                              # the destination is always named
    for k in range(len(pts)):
        boxes = [p.box for n, p in st["plates"] if n == f"stop{k}"]
        assert bool(boxes) != (k in unnamed), (case, k)
        if not boxes or k in clusters:
            continue
        u = graphics._union(boxes)
        own = graphics.box_distance(pts[k], u)
        other = min(graphics.box_distance(q, u) for j, q in enumerate(pts) if j != k)
        assert other > own, (case, k, own, other)                         # never read as another stop's name
        if k not in last_resort:
            assert other >= own + graphics.ROUTE_NAME_MARGIN - 1, (case, k, own, other)
    if case == "six_spread" and size == (1920, 1080):
        assert not clusters                                               # the reviewer's case: every name clear


RAND_POINTS = {**ROUTE_POINTS, **EDGE_POINTS, "palmyra": (34.55, 38.27), "aleppo": (36.20, 37.13),
               "athens": (37.98, 23.73), "nigde": (37.97, 34.68), "goreme": (38.6431, 34.8289),
               "silifke": (36.37, 33.93), "sinop": (42.0264, 35.1551)}
RAND_NAMES = ["Ancient harbour of the Lycian league", "Citadel", "Rock-cut tombs above the river",
              "Caravanserai on the Silk Road", "Necropolis", "Monastery of the hermits in the valley", "Basilica",
              "Hippodrome and aqueduct of the emperors", "Fortress", "Theatre"]


def _rand_routes() -> dict[str, list[dict[str, str]]]:
    """The reviewer's rr7 (seed 8123: ROCK-CUT TOMBS 304 px from its stop and 41 px from the Basilica stop), and
    random routes of 2 to 6 stops on two seeds over this file's gazetteer."""
    out = {"rr7": [_stop("Antalya", "Citadel"), _stop("Derinkuyu", "Rock-cut tombs above the river"),
                   _stop("Kars", "Fortress"), _stop("Palmyra", "Basilica"), _stop("Ankara", "Caravanserai on the Silk Road")]}
    for seed in (8123, 4242):
        rnd = random.Random(seed)
        for i in range(3):
            out[f"s{seed}_{i}"] = [_stop(p.capitalize(), rnd.choice(RAND_NAMES))
                                   for p in rnd.sample(sorted(RAND_POINTS), rnd.randint(3, 6))]
    return out


RAND_ROUTES = _rand_routes()


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(RAND_ROUTES))
def test_route_names_never_read_as_another_stops_on_random_routes(tmp_path, monkeypatch, route_geo, caplog, case,
                                                                  size) -> None:
    # round 4 review 8: a last-resort name sat beside the wrong stop (rr7 9:16). Outside a cluster no name is
    # nearer another stop than its own, the destination is always named, and every last resort (a name nearer its
    # own stop by under the margin, a stop left unnamed) is logged at WARNING
    monkeypatch.setattr(geo, "locate", lambda topic: dict(
        zip(("lat", "lon"), RAND_POINTS[topic["location"]["name"].lower()]), label=topic["location"]["name"],
        approximate=False, matched="name", country=topic["location"]["country"]))
    stops = graphics.route_stops(RAND_ROUTES[case])
    seen = _stop_points(monkeypatch)
    with caplog.at_level("INFO", logger=graphics.log.name):
        st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                     height=size[1]))
    pts = list(dict.fromkeys(seen))[:len(stops)]
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    cluster = {k for k, q in enumerate(pts)
               if min(math.dist(q, q2) for j, q2 in enumerate(pts) if j != k) < graphics.ROUTE_CLUSTER_PX}
    named = {int(n[4:]) for n, _p in st["plates"] if n.startswith("stop")}
    assert len(pts) - 1 in named, case
    for k in range(len(pts)):
        if k not in named:
            assert f"stop {k} ({stops[k]['label']}); the stop is drawn unlabelled" in warned, (case, k)
            continue
        if k in cluster:
            continue
        u = graphics._union([p.box for n, p in st["plates"] if n == f"stop{k}"])
        own = graphics.box_distance(pts[k], u)
        other = min(graphics.box_distance(q, u) for j, q in enumerate(pts) if j != k)
        assert other > own, (case, k, own, other)
        if other < own + graphics.ROUTE_NAME_MARGIN - 1:
            assert f"name of stop {k} ({stops[k]['label']}) is nearer its own stop by under" in warned, (case, k)
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])
    _assert_clear(st["plates"])


def _seg_dist(p, a, b) -> float:
    (ax, ay), (bx, by) = a, b
    L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
    f = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
    return math.dist(p, (ax + (bx - ax) * f, ay + (by - ay) * f))


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", ["longname", "ns", "derinkuyu", "sw_corner", "five", "corners", "right"])
def test_the_context_globe_keeps_clear_of_stops_legs_and_leaders(tmp_path, monkeypatch, edge_geo, case,
                                                                 size) -> None:
    # review 6 F: checked against the names only, the globe covered Constantinople (longname 9:16) and sat on
    # Trebizond (ns 9:16)
    routes = {**ROUTES, **EDGE_ROUTES,
              "longname": [_stop("Istanbul", "The Great Palace of the Byzantine Emperors at Constantinople"),
                           _stop("Derinkuyu", "Derinkuyu underground city of the Cappadocian plateau and its "
                                              "eighteen levels")]}
    stops = graphics.route_stops(routes[case])
    cap: dict[str, Any] = {}
    real_rb, real_nt = graphics.relief_base, graphics.record_nontext

    def rb(*a, **k):
        cap["project"] = a[7]
        return real_rb(*a, **k)

    def nt(out, frame, marks, *a, **k):
        cap["marks"] = marks
        return real_nt(out, frame, marks, *a, **k)
    monkeypatch.setattr(graphics, "relief_base", rb)
    monkeypatch.setattr(graphics, "record_nontext", nt)
    st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                 height=size[1]))
    globe = [m for n, m in cap["marks"] if n == "globe"]
    if not globe:
        return                                                            # dropped: nothing to cover
    bb = globe[0].getbbox()
    c, r = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2), (bb[2] - bb[0]) / 2
    pts = []
    for s in stops:
        lat, lon = edge_geo[s["place"].lower()]
        x, y = cap["project"](lon, lat)
        pts.append((x / 2, y / 2))
    for k, q in enumerate(pts):
        rim = graphics.ROUTE_STOP_R          # round 6 (deliberate): the destination is the same dot (22 before)
        assert math.dist(c, q) - r >= rim + graphics.GLOBE_AIR, (case, k)
    for (la0, lo0), (la1, lo1) in itertools.pairwise([edge_geo[s["place"].lower()] for s in stops]):
        leg = [tuple(v / 2 for v in cap["project"](lo, la)) for lo, la in graphics.great_circle((lo0, la0), (lo1, la1))]
        assert all(_seg_dist(c, a, b) - r >= 6 + graphics.GLOBE_AIR for a, b in itertools.pairwise(leg)), case
    for k, q in enumerate(pts):                                           # the leaders the names were given
        boxes = [p.box for n, p in st["plates"] if n == f"stop{k}"]
        if boxes and graphics.box_distance(q, graphics._union(boxes)) > graphics.ROUTE_LEADER_AT:
            rim = graphics.ROUTE_STOP_R      # round 6 (deliberate): 22 before for the destination
            a, b = graphics.route_leader(q, graphics._union(boxes), rim + 3)
            assert _seg_dist(c, a, b) - r >= graphics.GLOBE_AIR, (case, k)


@pytest.mark.parametrize("case", sorted(ROUTES))
def test_wide_scale_bar_figures_are_clearly_secondary_to_the_names(tmp_path, monkeypatch, route_geo, case) -> None:
    # review 6 J: 34 px names (cap 24) over 30 px figures (cap 21) were near one size on 16:9
    st = _render_fast(monkeypatch, lambda: graphics.render_route(ROUTES[case], tmp_path / "r.mp4", width=1920,
                                                                 height=1080))
    figures = [p for n, p in st["plates"] if n.startswith("scale")]
    names = [p for n, p in st["plates"] if n.startswith("stop")]
    # round 6 (deliberate): the bar's labels are SCALE_LABEL_PX Montserrat 600 (SCALE_FIGURE_PX_WIDE before)
    assert figures and {p.font.size for p in figures} == {graphics.SCALE_LABEL_PX}
    assert 24 <= graphics.SCALE_FIGURE_PX_WIDE <= 26 and graphics.SCALE_LABEL_PX >= graphics.WIDE_TERTIARY_MIN
    assert min(graphics.cap_height(p.font) for p in names) >= 1.3 * max(graphics.cap_height(p.font) for p in figures)
    assert graphics.scale_figure_px(1080, 1920) == graphics.SCALE_FIGURE_PX


def test_the_globe_is_composited_in_its_own_box_only() -> None:
    # review 6 G: the whole frame went to RGBA and back on every frame (~11 ms) long after the fade
    rng = np.random.default_rng(3)
    frame = Image.fromarray(rng.integers(0, 255, (400, 300, 3), dtype=np.uint8))
    globe = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    ImageDraw.Draw(globe).ellipse([2, 2, 58, 58], fill=(200, 160, 60, 255))
    box = (100, 120, 160, 180)
    whole = frame.convert("RGBA")
    whole.alpha_composite(globe, box[:2])
    for t in (0.1, graphics.GLOBE_FADE, graphics.GLOBE_OUT + 0.15, 3.0):
        got = graphics._draw_globe(frame.copy(), globe, box, t)
        a = graphics.globe_alpha(t)
        if a >= 0.999:
            assert np.array_equal(np.asarray(got), np.asarray(whole.convert("RGB")))
        diff = np.any(np.asarray(got) != np.asarray(frame), axis=2)
        if t == 3.0:                       # round 6 (deliberate): faded out by 2.4 s, the frame unchanged
            assert not diff.any()
            continue
        if t == graphics.GLOBE_OUT + 0.15:
            assert 0 < a < 1
        ys, xs = np.nonzero(diff)
        assert xs.min() >= box[0] and xs.max() < box[2] and ys.min() >= box[1] and ys.max() < box[3]


def test_draw_plate_composites_the_plates_box_only_with_the_same_pixels() -> None:
    # review 6 G: TextPlate.draw turned the whole frame to RGBA and back for each plate on every frame
    rng = np.random.default_rng(1)
    frame = Image.fromarray(rng.integers(0, 255, (500, 800, 3), dtype=np.uint8))
    font = graphics.get_font("Montserrat", 34, weight=800)
    for xy in ((100, 200), (700, 30), (-10, 490)):
        plate = graphics.plan_text(frame, xy, "HALICARNASSUS", font, brand.BONE, "ls", track=2, halo_px=21,
                                   min_halo=0.7)
        for alpha, dy in ((1.0, 0), (0.4, 5)):
            want = np.asarray(plate.draw(frame.copy(), alpha, dy))
            got = np.asarray(graphics.draw_plate(frame.copy(), plate, alpha, dy))
            assert np.array_equal(want, got), (xy, alpha, dy)


def test_ground_drift_caches_its_steps_and_leaves_a_flat_ground_alone() -> None:
    # QF-22: sample every distinct camera position; cache only an exact repeat.
    rng = np.random.default_rng(2)
    g = Image.fromarray(rng.integers(0, 255, (640, 360, 3), dtype=np.uint8))
    drift = graphics.GroundDrift(g)
    calls = []
    real = graphics._pushed_in
    try:
        graphics._pushed_in = lambda bg, s: calls.append(s) or real(bg, s)
        frames = [drift(i / 30) for i in range(int(graphics.CLIP_SECONDS * 30))]
    finally:
        graphics._pushed_in = real
    assert len(calls) == len(frames) - 1  # frame zero is the unchanged source
    count = len(calls)
    repeated = drift((len(frames) - 1) / 30)
    assert len(calls) == count and repeated.tobytes() == frames[-1].tobytes()
    assert frames[0].tobytes() == g.tobytes() and frames[-1].tobytes() != frames[-40].tobytes()
    frames[5].paste((0, 0, 0), (0, 0, 10, 10))                           # each call is the caller's own copy
    assert drift(5 / 30).getpixel((0, 0)) != (0, 0, 0) or g.getpixel((0, 0)) == (0, 0, 0)
    flat = graphics.GroundDrift(Image.new("RGB", (64, 64), brand.INK))
    assert flat(graphics.CLIP_SECONDS).tobytes() == Image.new("RGB", (64, 64), brand.INK).tobytes()


def test_wide_span_three_line_break_and_tracked_wrap_options() -> None:
    # round 15 (ruling R11, deliberate): no three-row break ("LATER" alone); any stacking is the count over its unit
    assert graphics.wide_span_lines("ABOUT 5,500 YEARS LATER", 3) == ["ABOUT 5,500", "YEARS LATER"]
    assert graphics.wide_span_lines("40 YEARS LATER", 3) == ["40", "YEARS LATER"]
    font = graphics.get_font("Montserrat", 34, weight=800)
    text = "MAUSOLEUM FORTIFICATIONS REDISCOVERED BEHIND CHAMBERS BY"
    width = graphics.tracked_width("FORTIFICATIONS", font, 2) + 4
    fewest = graphics.tracked_wrap(text, font, width, 2)
    assert graphics.tracked_wrap(text, font, width, 2, lines=len(fewest) - 1) is None
    more = graphics.tracked_wrap(text, font, width, 2, lines=len(fewest) + 1)
    assert more and len(more) == len(fewest) + 1 and " ".join(more) == text
    balanced = graphics.tracked_wrap(text, font, width, 2, lone_share=graphics.WIDE_LONE_SHARE)
    first = graphics.tracked_width(balanced[0], font, 2)
    assert first >= graphics.WIDE_LONE_SHARE * max(graphics.tracked_width(ln, font, 2) for ln in balanced[1:])


@pytest.mark.parametrize("case", ["nz0", "nz2", "nz5"])
def test_tall_years_drop_to_their_floor_before_a_label_is_cut(tmp_path, monkeypatch, caplog, case) -> None:
    # review 7: the rows were reduced at the full ratio first (nz0, nz2, nz5 cut at a 2.42 year ratio); where a
    # cut cannot be avoided the years give way to TALL_YEAR_RATIO_MIN first, so fewer labels are cut.
    # Round 4 review 9 (2026-11-03), a deliberate change: the years give way only where that cuts fewer words
    # (logged so). nz0 keeps more words at the least ratio; nz2 and nz5 cut the same words at either ratio, so
    # their years now keep the full ratio (this asserted `not cut or ratio < TALL_YEAR_RATIO` for all three).
    events = FUZZ_FAMILIES["nz"][case]
    with caplog.at_level("INFO"):
        st = _refused_or_render(monkeypatch, f"nz/{case}-9x16",
                                lambda: graphics.render_timeline(events, tmp_path / "t.mp4"))
    if st is None:   # QF-23: refused rather than drawn with its cut labels (each case here was cut)
        return
    years = [p for n, p in st["plates"] if n.startswith("year") and not n.endswith("circa")]
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    cut = [p.text for p in labels if "…" in p.text]
    ratio = min(graphics.cap_height(p.font) for p in years) / max(graphics.cap_height(p.font) for p in labels)
    if cut and ratio < graphics.TALL_YEAR_RATIO:
        assert "to cut fewer words" in caplog.text, (case, ratio, cut)
    assert "entries whole" not in caplog.text or not cut, (case, cut)
    # 2026-11-03 hierarchy spec, a deliberate change: the ratios rose to 3.0 / 2.5 and the labels keep their row
    # budget (2 rows at 4 entries) with copy-fit rows up to 6: nz0 and now nz5 keep more words at the least ratio,
    # nz2 cuts the same words at either (this asserted nz0 alone under the full ratio, nz2 and nz5 at it)
    # round 6 (deliberate): at 4.5 / 4.0 nz2 too keeps more words at the least ratio (it held the full one before)
    if case in ("nz0", "nz2", "nz5"):
        assert cut and ratio < graphics.TALL_YEAR_RATIO, (case, ratio, cut)
    else:
        assert ratio >= graphics.TALL_YEAR_RATIO, (case, ratio, cut)
    # round 7 (deliberate, R3): with the spans back in their own rows these copy-fit sets reach the last resort
    # (TALL_YEAR_RATIO_LAST, logged), never under it
    floor = (graphics.TALL_YEAR_RATIO_LAST if "the last resort" in caplog.text or "set at the tightest" in caplog.text
             else graphics.TALL_YEAR_RATIO_MIN)
    assert ratio >= floor - 0.05


@pytest.mark.parametrize("case", ["nz27", "four_d"])
def test_a_wide_span_under_a_block_keeps_twice_its_line_gap(tmp_path, monkeypatch, caplog, case) -> None:
    # round 4 review 8: nz27's "ABOUT 7,500 YEARS LATER" sat 20 px under "CARVED FIRST CHAMBERS BY THE" across its
    # width (1.5 x its line gap) and read as its next line; under or over a block it takes WIDE_SPAN_ACROSS x
    events = FUZZ_FAMILIES["nz"][case] if case in FUZZ_FAMILIES["nz"] else NAMED_TL[case]
    st = _render_checked(monkeypatch, caplog, tmp_path, events, (1920, 1080))
    labels: dict[int, list[Any]] = {}
    spans: dict[int, list[Any]] = {}
    for n, p in st["plates"]:
        if n.startswith("label"):
            labels.setdefault(int(n[5:]), []).append(p)
        if n.startswith("gap"):
            spans.setdefault(int(n[3:].split("_")[0]), []).append(p)
    assert spans
    for k, ps in spans.items():
        u = graphics._union([p.box for p in ps])
        for j, lp in labels.items():
            b = graphics._union([p.box for p in lp])
            if u[0] < b[2] and b[0] < u[2]:
                air = graphics.box_clearance(u, b)
                assert air >= graphics.WIDE_SPAN_ACROSS * _label_line_gap(lp) - 1, (case, k, j, air)


def test_wide_blocks_hold_title_safe_on_both_axes_and_spans_lean_later(tmp_path, monkeypatch, caplog) -> None:
    # review 7: nz39's widened label ran to x 1854 (title-safe ends at 1766); four_d's "ABOUT 320 YEARS LATER" sat
    # on "c. AD 780"; nz27's stacked "LATER" read "LATER AD 341"
    for case in ("nz39", "nz18", "nz27"):
        _render_checked(monkeypatch, caplog, tmp_path, FUZZ_FAMILIES["nz"][case], (1920, 1080))
    _render_checked(monkeypatch, caplog, tmp_path, NAMED_TL["four_d"], (1920, 1080))


# ---------------------------------------------------------------- round 4 review 9 (2026-11-03)
@pytest.mark.parametrize("case", ["xv1", "xv18", "xv21"])
def test_tall_years_shrink_only_for_what_it_buys_and_the_log_says_so(tmp_path, monkeypatch, caplog, case) -> None:
    # review 9: xv1, xv18 and xv21 logged "the years set under 2.0 x ... to keep 4 entries whole" and then "a label
    # cut" in the same render: the years had shrunk for nothing (the same labels are cut at the full ratio)
    with caplog.at_level("INFO"):
        st = _refused_or_render(monkeypatch, f"xv/{case}-9x16",
                                lambda: graphics.render_timeline(XV_TL[case], tmp_path / "t.mp4"))
    if st is None:   # QF-23: refused rather than drawn with its cut labels
        return
    labels = [p for n, p in st["plates"] if n.startswith("label")]
    years = [p for n, p in st["plates"] if n.startswith("year") and not n.endswith("circa")]
    cut = [p.text for p in labels if "…" in p.text]
    ratio = min(graphics.cap_height(p.font) for p in years) / max(graphics.cap_height(p.font) for p in labels)
    assert not ("entries whole" in caplog.text and "a label cut" in caplog.text), case
    assert bool(cut) == ("a label cut" in caplog.text), (case, cut)
    if ratio < graphics.TALL_YEAR_RATIO:
        assert "years set under" in caplog.text
        assert not cut or "to cut fewer words" in caplog.text, (case, ratio, cut)


def test_route_name_lines_keep_their_lines_balanced_and_no_function_word_alone() -> None:
    font = graphics.get_font("Montserrat", 34, weight=800)
    long_ = "Lighthouse of the Ptolemies on the eastern mole"
    assert graphics.route_name_lines(long_, font, 380 * 0.6, 3) is None          # six lines before: never past 3
    got = graphics.route_name_lines(long_, font, 380, 3)
    assert got and len(got) == 3 and " ".join(got) == long_.upper()
    customs = graphics.route_name_lines("Customs house of the Genoese traders", font, 380 * 0.6, 3)
    assert customs is None or len(customs) <= 3
    for text in ("Customs house of the Genoese traders", "Tomb of the satrap and his household",
                 "Cistern of the thousand and one columns", long_, "Spring sanctuary of Apollo"):
        for px in graphics.ROUTE_NAME_STEPS:
            f = graphics.get_font("Montserrat", px, weight=800)
            for measure, rows in ((380, 2), (380, 3), (228, 3), (532, 2)):
                lines = graphics.route_name_lines(text, f, measure, rows)
                if lines is None:
                    continue
                assert len(lines) <= rows and " ".join(lines) == text.upper()
                assert all(graphics._tracked_w(ln, f, 0) <= measure for ln in lines)
                assert not any(set(ln.split()) <= graphics.ROUTE_FUNCTION_WORDS for ln in lines) or len(lines) == 1
                if len(lines) > 1:                                               # the fewest lines it takes
                    assert graphics.route_name_lines(text, f, measure, len(lines) - 1) is None
    cistern = graphics.route_name_lines("Cistern of the thousand and one columns", font, 380, 3)
    widths = [graphics._tracked_w(ln, font, 0) for ln in cistern]
    assert min(widths) >= 0.6 * max(widths), cistern                             # balanced, not "CISTERN / ..."


REVIEW9_POINTS = {"hopa": (41.39, 41.43), "kaymakli": (38.4619, 34.7514), "luxor": (25.69, 32.64),
                  "istanbul": (41.0082, 28.9784), "bodrum": (37.0344, 27.4305), "alexandria": (31.20, 29.92),
                  "aswan": (24.09, 32.90), "cairo": (30.04, 31.24), "mardin": (37.31, 40.74), "kas": (36.20, 29.64),
                  "izmir": (38.4237, 27.1428), "trabzon": (41.0027, 39.7168), "antalya": (36.8969, 30.7133),
                  "kars": (40.6013, 43.0975)}
_LH, _CU = "Lighthouse of the Ptolemies on the eastern mole", "Customs house of the Genoese traders"
REVIEW9_ROUTES = {   # the reviewer's rr4 and rr8 (route_new.py, RSEED 2718) and its zigzag
    "rr4": [_stop("Hopa", "Watchtower"), _stop("Kaymakli", "Agora"), _stop("Luxor", "Kale"), _stop("Istanbul", "Agora"),
            _stop("Bodrum", _LH), _stop("Alexandria", _CU)],
    "rr8": [_stop("Aswan", "Mint"), _stop("Cairo", _CU), _stop("Mardin", "Tomb of the satrap and his household"),
            _stop("Kas", _LH), _stop("Istanbul", "Cistern of the thousand and one columns")],
    "zigzag": [_stop("Izmir", "Smyrna"), _stop("Trabzon", "Trebizond"), _stop("Antalya", "Attaleia"),
               _stop("Kars", "Kars"), _stop("Bodrum", "Halicarnassus")],
}
FAR_STOP_PX = 150     # a stop this far from every other is always named (rr4 9:16 stop 3 sat 172 px off, unnamed)


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080)], ids=["9x16", "16x9"])
@pytest.mark.parametrize("case", sorted(REVIEW9_ROUTES))
def test_route_names_hold_their_lines_and_far_stops_and_the_destination_are_named(tmp_path, monkeypatch, route_geo,
                                                                                   caplog, case, size) -> None:
    # review 9: the 'further' measure ran names past three lines ("LIGHTHOUSE / OF THE / PTOLEMIES / ON THE /
    # EASTERN / MOLE", "THE" alone); stops far from any other were left unnamed (rr4 9:16 stop 3, rr8 9:16 stop 2)
    # and the zigzag's destination too
    monkeypatch.setattr(geo, "locate", lambda topic: dict(
        zip(("lat", "lon"), REVIEW9_POINTS[topic["location"]["name"].lower()]), label=topic["location"]["name"],
        approximate=False, matched="name", country=topic["location"]["country"]))
    stops = graphics.route_stops(REVIEW9_ROUTES[case])
    seen = _stop_points(monkeypatch)
    with caplog.at_level("INFO", logger=graphics.log.name):
        st = _render_fast(monkeypatch, lambda: graphics.render_route(stops, tmp_path / "r.mp4", width=size[0],
                                                                     height=size[1]))
    pts = list(dict.fromkeys(seen))[:len(stops)]
    by_stop: dict[int, list[Any]] = {}
    for n, p in st["plates"]:
        if n.startswith("stop"):
            by_stop.setdefault(int(n[4:]), []).append(p)
    assert len(pts) - 1 in by_stop, (case, sorted(by_stop))                     # the destination, always
    for k, q in enumerate(pts):
        if min(math.dist(q, q2) for j, q2 in enumerate(pts) if j != k) >= FAR_STOP_PX:
            assert k in by_stop, (case, k)
    for k, ps in by_stop.items():
        assert len(ps) <= 3, (case, [p.text for p in ps])                        # never past three lines
        if len(ps) > 1:
            assert not any(set(p.text.split()) <= graphics.ROUTE_FUNCTION_WORDS for p in ps), [p.text for p in ps]
        assert all(p.font.size >= graphics.ROUTE_NAME_MIN_PX for p in ps)
        assert " ".join(p.text for p in sorted(ps, key=lambda p: p.box[1])) == stops[k]["label"].upper()
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert "runs past its" not in caplog.text
    cluster = {k for k, q in enumerate(pts)
               if min(math.dist(q, q2) for j, q2 in enumerate(pts) if j != k) < graphics.ROUTE_CLUSTER_PX}
    for k, ps in by_stop.items():
        if k in cluster:
            continue
        u = graphics._union([p.box for p in ps])
        own = graphics.box_distance(pts[k], u)
        other = min(graphics.box_distance(q, u) for j, q in enumerate(pts) if j != k)
        if f"the destination's name, stop {k} " in warned:                     # its leader last resort
            assert k == len(pts) - 1 and other >= graphics.ROUTE_LEADER_AT - 1, (case, k, own, other)
            assert own > graphics.ROUTE_LEADER_AT                                # so it hangs on a leader
            continue
        assert other > own, (case, k, own, other)
    x0, y0, x1, y1 = graphics.get_text_box(*size)
    assert all(x0 <= p.box[0] and p.box[2] <= x1 and y0 <= p.box[1] and p.box[3] <= y1 for _, p in st["plates"])
    _assert_clear(st["plates"])
