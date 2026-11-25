"""Design-freeze lane A, cycle 4: invariants (every drop recorded, labels name their own ticks, no halo cloud past the
overlay-label cap, no widowed referent note where a form avoids one, one baseline for paired figures), never pinned
measurements."""
from __future__ import annotations

import pytest
from PIL import Image

from src import brand, graphics
from src.brand_system import tok
from src.refusal import Declined, collecting
from src.typography import get_font

FIVE = [{"year": 1300, "label": "The Knights of St John reach Rhodes", "approximate": True},
        {"year": 1522, "label": "Suleiman the Magnificent takes the city"},
        {"year": 1523, "label": "The knights sail for Malta"},
        {"year": 1912, "label": "Italy takes the Dodecanese"},
        {"year": 1988, "label": "The Medieval City of Rhodes is listed"}]


# ----------------------------------------------------------------------------------- item 1: every drop recorded
def test_five_to_four_fallback_records_the_dropped_event(monkeypatch):
    """Panel 5 (4 runs): the second brand's dense timeline lost 1523 with only a log line. The fit fallback now
    records every event it leaves out on the ledger, naming the surface, the event and the rule."""
    from src import timeline_dense
    monkeypatch.setattr(timeline_dense, "fits_both_formats", lambda events: False)
    with collecting() as rows:
        kept = graphics.timeline_events(FIVE, surface="timeline t.mp4")
    assert len(kept) == 4
    dropped = [e for e in FIVE if e not in kept]
    assert len(rows) == len(dropped) == 1
    row = rows[0]
    assert row["surface"] == "timeline t.mp4" and row["drawn"] and row["rule"]
    assert str(dropped[0]["year"]) in row["omitted"] and dropped[0]["label"].upper() in row["omitted"]


def test_over_limit_drops_are_recorded_one_row_each():
    six = [{"year": 1000 + 100 * k, "label": f"Event number {k}"} for k in range(8)]
    with collecting() as rows:
        kept = graphics.timeline_events(six, limit=4, surface="timeline t.mp4")
    assert len(rows) == len(six) - len(kept)
    assert {r["omitted"] for r in rows} == {f"the event {e['year']} {e['label'].upper()}" for e in six
                                            if e not in kept}


def test_a_drop_with_no_ledger_refuses_the_surface(monkeypatch):
    from src import timeline_dense
    monkeypatch.setattr(timeline_dense, "fits_both_formats", lambda events: False)
    with pytest.raises(Declined):
        graphics.timeline_events(FIVE, surface="timeline t.mp4")
    assert len(graphics.timeline_events(FIVE)) == 4           # a planning call names no surface: nothing recorded


def test_route_stop_drops_are_recorded():
    stops = [{"label": f"Stop {k}"} for k in range(9)]
    with collecting() as rows:
        kept = graphics.route_stops(stops, surface="route r.mp4")
    assert len(rows) == len(stops) - len(kept) > 0
    assert all(r["surface"] == "route r.mp4" and r["omitted"].startswith("the stop ") for r in rows)


def test_a_redrawn_surface_records_a_part_once():
    with collecting() as rows:
        for _ in range(3):
            graphics.record_drop("map m.mp4", "the scale bar", "rule", "why")
        graphics.record_drop("map n.mp4", "the scale bar", "rule", "why")
    assert len(rows) == 2


# ------------------------------------------------------------------------- item 3: the scale bar's labels
def _bars():
    for lat in (0.0, 35.0, 59.7, 70.0):
        for px_per_deg in (40.0, 110.0, 260.0, 900.0):
            for narration in ("", "about 300 miles"):
                for rng in (graphics.SCALE_RANGE_TALL, graphics.SCALE_RANGE_WIDE):
                    yield graphics.scale_bar(lat, px_per_deg, narration, bar_range=rng)


@pytest.mark.parametrize("direction", [1, -1])
def test_every_scale_label_names_its_own_tick(direction):
    """TY MG-1: the 9:16 "30 MI" started at the zero end and read as "100 KM = 30 MI". Every label's ink ends on its
    own tick, or (wider than its run) is centred on it; it never reaches past the zero end."""
    font = graphics.scale_label_font(1080, 1920)
    track = font.size * graphics.SCALE_LABEL_TRACK
    x = 500.0
    seen = 0
    for bar in _bars():
        parts = {"scale": bar.primary, "scale_secondary": bar.secondary}
        for name, text, xy in graphics.scale_bar_label_specs(bar, x, 300.0, font, direction):
            part = parts[name]
            tick = x + direction * part.px
            bb = graphics.ink_box(xy, text, font, "ls", track)
            ends = abs((bb[2] if direction > 0 else bb[0]) - tick) <= 1.0
            centred = abs((bb[0] + bb[2]) / 2 - tick) <= 1.0
            assert ends or centred, (text, bb, tick)
            if direction > 0:
                assert bb[0] >= x - 0.5, (text, bb, x)
            else:
                assert bb[2] <= x + 0.5, (text, bb, x)
            seen += 1
    assert seen > 20


@pytest.mark.parametrize("ground", [(196, 160, 96), (230, 200, 130), (34, 32, 28)])
def test_scale_labels_take_the_overlay_label_rule(ground):
    """TY-S2-map-halo: the bar's figures drew a 0.75 halo floor, a cloud on flat gold land. They are one overlay label
    on the one rule now: a halo no deeper than place.halo_max, else INK type or a held plate, every row at the place
    gate, both rows on one step."""
    rest = Image.new("RGB", (1080, 1920), ground)
    bar = graphics.scale_bar(59.7, 110.0, "")
    x, y = 200.0, 1000.0
    plates = graphics._scale_bar_plates(graphics._with_bar(rest, bar, x, y), bar, x, y)
    assert plates and len(plates) == len(graphics.scale_bar_label_specs(bar, x, y, graphics.scale_label_font()))
    steps = {getattr(p, "step", None) for _n, p in plates}
    assert len(steps) == 1 and None not in steps
    for _n, p in plates:
        if p.step[0] == "halo":
            assert p.halo <= tok("place.halo_max") + 1e-6
        assert (p.contrast or 0) >= graphics.GATES["contrast.place"] - 1e-6


# ------------------------------------------------------------------------- item 2: the referent note's widow
def test_referent_widow_rule():
    assert graphics.referent_widow(["GROUND", "TO TORCH", "TIP"])
    assert not graphics.referent_widow(["GROUND", "TO TORCH TIP"])
    assert not graphics.referent_widow(["GROUND TO TORCH TIP"])


def test_referent_block_takes_a_form_without_a_widow_where_one_holds():
    """TY-S2 (Aswan 16:9 "305 FT / GROUND / TO TORCH / TIP"): a form that leaves one word alone on the note's last
    row is kept only where no other form sets the note without one."""
    ground = Image.new("RGB", (1920, 1080), brand.INK)
    f_fig = graphics.figure_font(40)
    note = "GROUND TO TORCH TIP"
    font_n = get_font(brand.TEXT_FONT, 30, weight=600)
    # the right of the tick holds the note only on three rows ("TO TORCH" fits, "TO TORCH TIP" does not); the left
    # holds it whole
    right = 900 + 34 + graphics._tracked_w("TO TORCH", font_n, 1) + 6

    def clear(b):
        return 0 <= b[0] and b[2] <= right and 0 <= b[1] and b[3] <= 1080
    fig, rows, _tick, rec = graphics.referent_block(ground, "305 FT", f_fig, 1.0, note, 30, 900.0, 860.0, 300.0, 1.0,
                                                   clear)
    assert fig is not None and rows and rec["shown"]
    lines = [ln for ln, _xy, _f in rows]
    assert " ".join(lines).split() == note.split()
    assert not graphics.referent_widow(lines) and not rec.get("widow")


# ------------------------------------------------------------------------- item 7: paired number cards
def test_two_wide_cards_share_one_figure_baseline_inside_the_box(monkeypatch):
    """TY-G1: "36" sat 61 px above "13,000" (its card carries a kicker). The pair's figures share one baseline and
    every block stays inside the text box."""
    captured = {}

    def fake_clip(cards, plans, bg, out, n, width, height):
        captured["plans"] = plans
        return out
    monkeypatch.setattr(graphics, "_stat_clip", fake_clip)
    cards = [{"value": "36 hours", "label": "time to disassemble the room"},
             {"value": "est. over 13,000 lbs", "label": "amber in completed room"}]
    for w, h in ((1920, 1080), (1280, 720)):
        graphics._render_stat_cards_landscape(cards, None, None, w, h)
        plans = captured["plans"]
        bases = {p["y0"] + p["num_y"] for p in plans}
        assert len(bases) == 1
        _x0, top, _x1, bottom = graphics.get_text_box(w, h)
        for p in plans:
            assert top - 0.5 <= p["y0"] + p["top"] and p["y0"] + p["bottom"] <= bottom + 0.5


def test_stepped_back_card_is_full_again_for_the_held_frame():
    assert 0 < graphics.STAT_RESTORE < 1
    from src.art_direction import count_start
    assert count_start(1) + 0.4 < graphics.STAT_RESTORE * graphics.CLIP_SECONDS
    assert graphics.STAT_RESTORE * graphics.CLIP_SECONDS + 0.5 < graphics.CLIP_SECONDS - 1 / 30


# ------------------------------------------------------------------------- item 6: label footprint in the safe box
@pytest.mark.parametrize("px", [24, 28, 34, 46])
@pytest.mark.parametrize("text", ["KHOLAT SYAKHL", "CATHERINE PALACE", "Q"])
def test_footprint_pad_covers_every_overlay_label_step(px, text):
    """PL-route916-edge: a stop's name was placed by its ink and its held plate ran 6 px past the safe box. The pad a
    place is judged with covers the widest step of the one overlay-label rule (the held plate) on every side."""
    from src.typography import label_ladder, plan_label
    ground = Image.new("RGB", (1080, 1920), (196, 160, 96))
    font = graphics.place_font(px)
    pad = graphics.label_footprint_pad(px)
    for step in label_ladder((brand.BONE,)):
        plates, rec = plan_label(ground, [((400, 900), text, font, "ls", 2)], (brand.BONE,), target=1.0,
                                 halo_px=tok("place.halo_px"), halo_max=tok("place.halo_max"), only=step)
        if not rec["shown"]:
            continue
        for p in plates:
            ink = graphics.ink_box((400, 900), text, font, "ls", 2)
            back = getattr(p, "backing", None)
            if back is not None:
                bx = back.record()["box"]
                assert bx[0] >= ink[0] - pad and bx[2] <= ink[2] + pad
                assert bx[1] >= ink[1] - pad and bx[3] <= ink[3] + pad
