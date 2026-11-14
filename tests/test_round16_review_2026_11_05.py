"""Round 16 (2026-11-05): the review of round 15's invariants.

1. A home that holds its name nowhere at its first framing is framed closer before it is dropped (Luxembourg).
2. A home name set outside its fill names, and leads to, the part of the home that holds the site (Melilla's SPAIN
   pointed at the Chafarinas).
3. The topic's country overrides a polygon that holds the site only within geo.SITE_HINT_KM, and the marker is
   then moved onto the chosen ground (the marker never stands on a fill other than the highlighted one).
4. A route's base-drawn names (its home's among them) go through the name check.
5. R11: only the spans one row leaves over the spine or unplaced take two rows.
6. "of" holds a proper name together ("The Tower of Jesus Christ"); a title's abbreviation keeps its next word.
7. The outline gate covers every outline in an island or small-country close-up, neighbours too.
8. An outside name touches its ground or keeps LEADER_MIN_PX off it (a shorter leader read as an apostrophe).
"""
import json
import logging

import pytest

from src import geo, graphics
from tests import geo_truth
from tests.test_r4_graphics_2026_11_02 import _render

needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()),
                                reason="Natural Earth data not fetched")
P = lambda lat, lon, c, lab: {"lat": lat, "lon": lon, "country": c, "label": lab, "approximate": False}
SIZES = [(1080, 1920), (1920, 1080)]


def _map(monkeypatch, tmp_path, place, size, name="m") -> dict:
    w, h = size
    _render(monkeypatch, lambda: graphics.render_map(place, tmp_path / f"{name}.mp4", width=w, height=h), fps=1)
    return json.loads((tmp_path / f"{name}.mp4.layout.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------- 1. framed closer
@needs_data
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_luxembourg_names_its_home(size, tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(49.6116, 6.1319, "Luxembourg", "Luxembourg City"), size)
    assert lay["country"] is not None and lay["country"]["text"] == "LUXEMBOURG", lay["home_drop"]
    assert lay["country"]["inside"]
    assert not geo_truth.faults(lay), geo_truth.faults(lay)


def test_the_closer_framing_is_bounded() -> None:
    assert 0 < graphics.HOME_ZOOM_MIN < graphics.HOME_ZOOM_STEP < 1
    steps, z = 0, 1.0
    while z * graphics.HOME_ZOOM_STEP >= graphics.HOME_ZOOM_MIN:
        z *= graphics.HOME_ZOOM_STEP
        steps += 1
    assert 1 <= steps <= 4


# ------------------------------------------------------------------------------------- 2. the site's part
@needs_data
def test_melillas_spain_is_by_melilla(tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(35.2923, -2.9381, "Spain", "Melilla la Vieja"), (1920, 1080))
    c = lay["country"]
    assert c is not None and c["text"] == "SPAIN"
    box = c["box"]
    ax, ay = lay["anchor"]
    gap = max(box[0] - ax, ax - box[2], 0.0) + max(box[1] - ay, ay - box[3], 0.0)
    assert gap < 200, (box, lay["anchor"])                     # it was 430 px off, on a leader to the Chafarinas
    if c["leader"]:
        gx, gy = c["leader"][1]
        assert abs(gx - ax) + abs(gy - ay) < 200, c["leader"]
    assert not geo_truth.faults(lay), geo_truth.faults(lay)


def test_an_outside_name_is_kept_by_the_sites_part() -> None:
    import numpy as np
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[900:960, 500:560] = True                              # the site's part
    home[400:420, 900:920] = True                              # an islet of the home, far off
    comps = graphics._components(home)
    lab = graphics._site_component(comps, (530.0, 930.0), graphics.NAME_SITE_REACH_PX)
    part = comps[0] == lab
    near_islet = (860.0, 380.0, 990.0, 396.0)
    # beside the far islet it would read as that islet's: with the site's part as its ground it is refused
    assert graphics.home_name_fault(near_islet, False, part, home, (530.0, 930.0))
    assert graphics.ground_leader((300.0, 850.0, 430.0, 870.0), part)[1][0] >= 495


# ------------------------------------------------------------------------------------- 3. the topic's country
@needs_data
@pytest.mark.parametrize("lat, lon, given, want, overrode", [
    (50.7543, 6.0207, "Netherlands", "Netherlands", True),      # Vaals, 1.75 km into Germany's 1:10m ground
    (-4.215, -69.94, "Colombia", "Colombia", True),             # Leticia, 1.4 km into Brazil's
    # QF-20 (operator ruling Q2, 2026-11-11): the Rock lies by contested Gibraltar, neutral ground: no home
    (36.1438, -5.3426, "United Kingdom", None, False),           # the Rock's summit, by contested Gibraltar
    # QF-20 (operator ruling, qf20_spec "Enclaves missing from Natural Earth" and HANDOFF section 7): past the 2 km
    # tie-break a topic country that is not the ground's names no home, the decision recorded (km 0: render_map's
    # home_drop "neutral or unmatched ground"), never the ground's country
    (51.4386, 4.9286, "Belgium", None, False),                   # Baarle, 3.4 km: past SITE_HINT_KM
    (69.0599, 20.5486, "Finland", None, False),                  # Treriksroset, 3.9 km
    (20.3536, 100.0837, "Thailand", None, False),                # the Golden Triangle, 2.5 km
])
def test_the_topic_country_overrides_held_ground_only_within_its_error(lat, lon, given, want, overrode) -> None:
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=given)
    assert (got.country or {}).get("display") == want
    assert got.overrode is overrode
    if want is None:
        assert got.km == 0  # the recorded no-home decision, not a failed open-sea lookup


@needs_data
def test_an_overriding_home_gets_the_marker() -> None:
    with geo.detail_view():
        home = geo.site_home(-69.94, -4.215, hint="Colombia").country
        moved = graphics.snap_onto(-69.94, -4.215, home)
    assert moved is not None
    assert any(geo.point_in_polygons(moved[0], moved[1], [poly]) for poly in home["polygons"])
    assert abs(moved[0] + 69.94) + abs(moved[1] + 4.215) < 0.05


@needs_data
def test_vaals_marker_stands_on_the_highlighted_ground(tmp_path, monkeypatch) -> None:
    lay = _map(monkeypatch, tmp_path, P(50.7543, 6.0207, "Netherlands", "Vaalserberg"), (1920, 1080))
    lo, la = lay["anchor_lonlat"]
    with geo.detail_view():
        nl = geo.site_home(6.0207, 50.7543, hint="Netherlands").country
    assert any(geo.point_in_polygons(lo, la, [poly]) for poly in nl["polygons"])
    assert lay["country"]["text"] == "NETHERLANDS"


# ------------------------------------------------------------------------------------- 4. route names checked
R = lambda c, pl: [{"place": n, "country": c, "label": lab} for n, lab in pl]


@needs_data
@pytest.mark.parametrize("size", SIZES, ids=["9x16", "16x9"])
def test_a_routes_base_drawn_home_name_is_checked(size, tmp_path, monkeypatch) -> None:
    coords = {"marrakesh": (31.6295, -7.9811), "fes": (34.0331, -5.0003), "tangier": (35.7595, -5.8340)}
    real = geo.locate

    def fake(topic):
        n = topic["location"]["name"].lower()
        if n in coords:
            la, lo = coords[n]
            return {"lat": la, "lon": lo, "country": "Morocco", "label": topic["location"]["name"],
                    "approximate": False, "qid": "x", "matched": "name"}
        return real(topic)
    monkeypatch.setattr(geo, "locate", fake)
    if hasattr(graphics, "locate"):
        monkeypatch.setattr(graphics, "locate", fake)
    w, h = size
    out = tmp_path / "r.mp4"
    stops = R("Morocco", [("Marrakesh", "Marrakesh"), ("Fes", "Fes"), ("Tangier", "Tangier")])
    _render(monkeypatch, lambda: graphics.render_route(stops, out, width=w, height=h, narration=""), fps=1)
    lay = json.loads((tmp_path / "r.mp4.layout.json").read_text(encoding="utf-8"))
    kinds = {(c["kind"], c["text"]) for c in lay["name_checks"]}
    assert lay["country"] is not None and ("home", lay["country"]["text"]) in kinds, (lay["country"], kinds)
    assert all(not c["fault"] for c in lay["name_checks"] if c["kind"] == "home")
    assert not geo_truth.faults(lay), geo_truth.faults(lay)


# ------------------------------------------------------------------------------------- 5 and 6. timelines
def test_of_holds_a_name_together_and_a_lead_article_does_not() -> None:
    src = "The Tower of Jesus Christ is completed"
    assert graphics.label_hard_fault(["THE TOWER", "OF JESUS CHRIST", "IS COMPLETED"], src)
    assert graphics.label_hard_fault(["THE TOWER OF", "JESUS CHRIST IS COMPLETED"], src)
    assert not graphics.label_hard_fault(["THE TOWER OF JESUS CHRIST", "IS COMPLETED"], src)
    assert not graphics.label_hard_fault(["THE", "TOWER OF JESUS CHRIST IS COMPLETED"], src)


def test_a_titles_abbreviation_keeps_its_next_word() -> None:
    font = graphics.get_font("Anton", 60)
    for measure in (300, 360, 420, 520):
        lines = graphics.route_name_lines("Ruins of St Paul's, Macau", font, measure, 3)
        if lines:
            assert not any(ln.split()[-1] == "ST" for ln in lines[:-1]), lines


E = lambda y, lab, a=False: {"year": y, "label": lab, "approximate": a}
SAGRADA = [E(1882, "The first stone is laid"), E(1883, "Antoni Gaudi takes over the project"),
           E(1926, "Gaudi dies with one tower finished"), E(2010, "Pope Benedict XVI consecrates the basilica"),
           E(2026, "The Tower of Jesus Christ is completed")]
WESTMINSTER = [E(960, "Benedictine monks settle the site", True), E(1066, "William the Conqueror is crowned here"),
               E(1245, "Henry III begins the Gothic rebuilding"), E(1953, "Elizabeth II is crowned in the abbey"),
               E(1987, "Named a World Heritage Site")]
PISA = [E(1173, "Work begins on the bell tower"), E(1178, "The tower starts to lean"),
        E(1372, "The bell chamber is finished"), E(1990, "The tower closes for repairs"), E(2001, "It reopens to visitors")]


@pytest.mark.parametrize("events", [SAGRADA, WESTMINSTER, PISA], ids=["sagrada", "westminster", "pisa"])
def test_the_reviews_new_timelines_keep_every_name_whole(events, tmp_path, monkeypatch, caplog) -> None:
    with caplog.at_level(logging.INFO, logger=graphics.log.name):
        st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920,
                                                                   height=1080), fps=1)
    errs = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert not errs, errs
    labels: dict[str, list[str]] = {}
    for n, p in st["plates"]:
        if n.startswith("label"):
            labels.setdefault(n, []).append(getattr(p, "text", ""))
    for rows in labels.values():
        src = next((e["label"] for e in events if " ".join(rows) == e["label"].upper()), None)
        assert not graphics.label_hard_fault(rows, src), rows
    spans = [getattr(p, "text", "") for n, p in st["plates"] if n.startswith("gap")]
    assert "LATER" not in spans and "YEARS" not in spans, spans


@pytest.mark.parametrize("events", [PISA], ids=["pisa"])
def test_a_span_that_fits_one_row_keeps_it(events, tmp_path, monkeypatch) -> None:
    st = _render(monkeypatch, lambda: graphics.render_timeline(events, tmp_path / "t.mp4", width=1920, height=1080),
                 fps=1)
    rows: dict[str, list[str]] = {}
    for n, p in st["plates"]:
        if n.startswith("gap"):
            rows.setdefault(n.split("_")[0], []).append(getattr(p, "text", ""))
    # QF-20 (spec ruling R11, decisions/QF-20.md capacity 4 -> 5): all five PISA events are drawn (the base dropped
    # one), and no spacing of the run holds all four spans on one row (reviewer's experiment, QF-20 review 2): the
    # 1372-1990 span alone takes the count-over-unit form "618 / YEARS LATER"; every other span keeps one row
    assert len(rows) == 4, rows
    two_row = [span for span in rows.values() if len(span) > 1]
    assert two_row == [["618", "YEARS LATER"]], rows
    assert all(span[0].endswith("YEARS LATER") for span in rows.values() if len(span) == 1), rows


# ------------------------------------------------------------------------------------- 7. the outline gate
@needs_data
@pytest.mark.parametrize("lat, lon, country", [(22.1987, 113.5439, "China"), (46.7811, -56.1764, "France"),
                                                (8.72, 167.731, "Marshall Islands")])
def test_every_outline_in_a_close_up_holds_its_detail(lat, lon, country) -> None:
    w, h = 1080, 1920
    with geo.detail_view():
        home = geo.site_home(lon, lat, hint=country).country
        span = graphics.locator_span(lat, lon, home, w, h)
        isl = graphics.island_span(lat, lon, home, w, h, span) or span
        own, other = graphics.view_edge_px(lat, lon, home, w, h, isl)
    assert own <= graphics.VIEW_EDGE_PX_OWN * 1.05 or isl >= span, (own, isl)
    assert other <= graphics.VIEW_EDGE_PX_OTHER * 1.05 or isl >= span, (other, isl)


# ------------------------------------------------------------------------------------- 8. no stub leaders
def test_an_outside_name_is_never_a_stub_away() -> None:
    import numpy as np
    w, h = 1080, 1920
    home = np.zeros((h, w), bool)
    home[900:960, 500:560] = True
    land = home.copy()
    got = graphics.country_outside_place("MALTA", home, (530.0, 930.0), [], w, h, land=land)
    assert got is not None
    import cv2
    d = cv2.distanceTransform((~home).astype(np.uint8), cv2.DIST_L2, 5)
    x0, y0, x1, y1 = (int(v) for v in got[1])
    gap = float(d[max(0, y0):y1, max(0, x0):x1].min())
    assert gap < graphics.LEADER_TOUCH_PX or gap >= graphics.LEADER_MIN_PX, gap


@needs_data
def test_a_title_repeating_the_homes_name_takes_its_sovereign() -> None:
    with geo.detail_view():
        home = geo.site_home(-162.0787, 5.8885, hint="United States").country
        assert graphics.home_title_form("PALMYRA ATOLL", "Palmyra Atoll", home) == "UNITED STATES OF AMERICA"
        macao = geo.site_home(113.5439, 22.1987, hint="China").country
        assert graphics.home_title_form("MACAO", "Ruins of St Paul's, Macau", macao) == "MACAU"
        assert graphics.home_title_form("BERMUDA", "Hamilton, Bermuda", None) == "BERMUDA"
