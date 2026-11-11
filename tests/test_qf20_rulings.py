"""QF-20 operator rulings of 2026-11-11 (decisions/QF-20-question.md).

Q1: the independent checker accepts the sovereign's name for a home whose own name is a whole-word run of the
site's title (the renderer's test), and only then. Q2: Natural Earth's disputed-areas features are neutral ground
where Natural Earth records a claim (a NOTE_BRK naming one: "Claimed by", "Self admin.", "Between"), whatever the
TYPE, except a lease or an overlay; whole states and subunits with no recorded claim keep their ordinary country.
Also: a topic country given as the ground's own Natural Earth ADMIN is that ground's (QF-20 review 2).
"""
import pytest

from src import geo, map_layers
from tests import geo_truth

needs_data = pytest.mark.skipif(not geo_truth.available(), reason="Natural Earth data not present")


# ---------------------------------------------------------------------------------------------- Q1
@pytest.mark.parametrize("name, title, want", [
    ("Ascension", "Georgetown, Ascension", True),
    ("Palmyra Atoll", "Palmyra Atoll", True),
    ("Norfolk Island", "Kingston, Norfolk Island", True),
    ("Tristan da Cunha", "Edinburgh of the Seven Seas, Tristan da Cunha", True),
    ("George Town", "Georgetown", False),           # part of a word is no run of words
    ("Georgetown", "George Town", False),
    ("Norfolk", "Kingston, Norfolk Island", True),  # a whole word of the title
    ("Norfolk Island", "Norfolk", False),           # the title holds only part of the name
    ("Ascension", "Two Boats Village", False),
    ("Sao Tome", "São Tomé Cathedral", True),       # accents folded, as the renderer folds them
])
def test_the_checkers_title_run_is_whole_words_only(name, title, want) -> None:
    assert geo_truth.repeats_title(name, title) is want


def _home_lay(label: str, text: str) -> dict:
    return {"kind": "map", "anchor_lonlat": [-14.416, -7.926], "label": label,
            "country": {"text": text, "inside": True}}


@needs_data
def test_the_sovereign_is_accepted_where_the_grounds_name_is_a_run_of_the_title() -> None:
    assert geo_truth.faults(_home_lay("Georgetown, Ascension", "UNITED KINGDOM")) == []
    assert geo_truth.faults(_home_lay("Georgetown, Ascension", "ASCENSION")) == []


@needs_data
def test_the_sovereign_is_still_rejected_where_the_title_does_not_hold_the_grounds_name() -> None:
    got = geo_truth.faults(_home_lay("Two Boats Village", "UNITED KINGDOM"))
    assert len(got) == 1 and "is not a name of the ground" in got[0]


# ---------------------------------------------------------------------------------------------- Q2
@pytest.mark.parametrize("props, want", [
    ({"TYPE": "Disputed", "NOTE_BRK": "Admin. by Russia; Claimed by Ukraine"}, True),
    ({"TYPE": "Breakaway", "NOTE_BRK": "Self admin.; Claimed by Georgia"}, True),
    ({"TYPE": "Indeterminate", "NOTE_BRK": "Between Egypt and Sudan"}, True),
    ({"TYPE": "Disputed", "NOTE_BRK": ""}, False),                                     # Israel, Belize, Kosovo
    ({"TYPE": "Geo subunit", "NOTE_BRK": ""}, False),                                  # Ceuta, Melilla
    ({"TYPE": "Geo subunit", "NOTE_BRK": "Admin. by U.K.; Claimed by Argentina"}, True),   # South Georgia
    ({"TYPE": "Indeterminate", "NOTE_BRK": ""}, False),                                # Scarborough Reef
    ({"TYPE": "Lease", "NOTE_BRK": "Leased to Russia by Kazakhstan"}, False),
    ({"TYPE": "Overlay", "NOTE_BRK": "UN Patrolled Zone in Syria"}, False),
])
def test_only_a_recorded_contest_is_neutral(props, want) -> None:
    assert map_layers.is_contested(props) is want


def _disputed_features() -> dict[str, list[dict]]:
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "data/geo/ne_10m_admin_0_disputed_areas.geojson"
    out: dict[str, list[dict]] = {}
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        out.setdefault(str(f["properties"].get("BRK_NAME") or "").replace("﻿", ""), []).append(f["properties"])
    return out


# the operator's answer to Q2 names each of these; the governing rule is "Natural Earth records a claim"
@pytest.mark.parametrize("brk_name, neutral", [
    ("Taiwan", True), ("Crimea", True), ("W. Sahara", True), ("Jammu and Kashmir", True), ("Azad Kashmir", True),
    ("Falkland Is.", True), ("S. Georgia", True), ("Kuril Is.", True),
    ("Dokdo", True), ("Paracel Is.", True), ("Wake Atoll", True), ("Hans Island", True), ("Mayotte", True),
    ("Israel", False), ("Belize", False), ("Kosovo", False), ("Gaza", False), ("West Bank", False),
    ("Ceuta", False), ("Melilla", False),
    ("Guantanamo Bay USNB", False), ("Baykonur", False),                      # leases
    ("UNDOF Zone", False), ("Korean Demilitarized Zone (south)", False),     # UN and military overlays
    ("Korean Demilitarized Zone (north)", False), ("Korean islands under UN jurisdiction", False),
    ("Diego Garcia NSF", False),
    ("Scarborough Reef", False),                                             # no claim recorded
])
def test_each_area_the_ruling_names_is_drawn_as_it_says(brk_name, neutral) -> None:
    features = _disputed_features()[brk_name]
    assert features and all(map_layers.is_contested(p) is neutral for p in features), [
        (p.get("TYPE"), p.get("NOTE_BRK")) for p in features]


def test_the_committed_table_lists_every_feature_as_the_rule_draws_it() -> None:
    from pathlib import Path
    table = (Path(__file__).resolve().parents[1] / "docs/reviews/quality-fixes-2026-11/QF-20/disputed-areas-q2.md"
             ).read_text(encoding="utf-8")
    rows = [line.split(" | ") for line in table.splitlines() if line.startswith("| ") and line[2:3].isdigit()]
    features = [p for ps in _disputed_features().values() for p in ps]
    assert len(rows) == len(features) == 99
    assert sum(r[-1].startswith("yes") for r in rows) == sum(map_layers.is_contested(p) for p in features)


@needs_data
@pytest.mark.parametrize("lat, lon, given, want", [
    (32.08, 34.78, "Israel", "Israel"),              # Tel Aviv
    (17.50, -88.20, "Belize", "Belize"),             # Belize City
    (42.66, 21.16, "Kosovo", "Kosovo"),              # Pristina
    (35.889, -5.318, "Spain", "Spain"),              # Ceuta
    (35.292, -2.94, "Spain", "Spain"),               # Melilla
])
def test_ground_with_no_recorded_claim_is_named_as_usual(lat, lon, given, want) -> None:
    assert not map_layers.neutral_at(lon, lat)
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=given)
    assert got.country is not None and got.country["display"] == want


@needs_data
@pytest.mark.parametrize("lat, lon, given", [
    (44.95, 34.10, "Ukraine"),                       # Simferopol, Crimea
    (25.03, 121.56, "Taiwan"),                       # Taipei
    (27.15, -13.20, "Morocco"),                      # Laayoune, Western Sahara
    (-51.69, -57.86, "United Kingdom"),              # Stanley, Falkland Islands
    (-54.2811, -36.5092, "United Kingdom"),          # Grytviken, South Georgia
])
def test_contested_ground_stays_neutral(lat, lon, given) -> None:
    assert map_layers.neutral_at(lon, lat)
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=given)
    assert got.country is None and got.km == 0      # the recorded neutral decision, not an open-sea miss


# ---------------------------------------------------------------------------------------------- the benchmark relief
def _measure_module(monkeypatch, inputs):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "docs/reviews/quality-fixes-2026-11/QF-20/measure.py"
    spec = importlib.util.spec_from_file_location("qf20_measure_relief", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "INPUTS", inputs)
    return module


@pytest.mark.parametrize("content", [None, b"not the relief"], ids=["missing", "mismatched"])
def test_the_benchmark_refuses_a_missing_or_mismatched_relief(tmp_path, monkeypatch, content) -> None:
    import hashlib
    import json
    inputs = tmp_path / "data/geo/benchmark-inputs"
    inputs.mkdir(parents=True)
    good = b"the relief"
    (inputs / "manifest.json").write_text(json.dumps([{
        "path": "relief/SR_HR.tif", "bytes": len(good), "sha256": hashlib.sha256(good).hexdigest(),
        "external_path": "tools/geo/relief/SR_HR.tif", "fetch": "python scripts/fetch_relief.py"}]), encoding="utf-8")
    measure = _measure_module(monkeypatch, inputs)
    if content is not None:
        (tmp_path / "tools/geo/relief").mkdir(parents=True)
        (tmp_path / "tools/geo/relief/SR_HR.tif").write_bytes(content)
    with pytest.raises(FileNotFoundError if content is None else ValueError, match="fetch_relief.py"):
        measure.unpack_inputs(tmp_path / "out")
    assert not (tmp_path / "out/relief/SR_HR.tif").exists()
    (tmp_path / "tools/geo/relief").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools/geo/relief/SR_HR.tif").write_bytes(good)
    measure.unpack_inputs(tmp_path / "out")
    assert (tmp_path / "out/relief/SR_HR.tif").read_bytes() == good


# ---------------------------------------------------------------------------------------------- the ground's ADMIN
@needs_data
@pytest.mark.parametrize("lat, lon, given, want", [
    (-0.37, -160.02, "United States Minor Outlying Islands", "Jarvis Island"),      # sweep site 115
    (28.21, -177.37, "United States Minor Outlying Islands", "Midway Islands"),
    (5.88, -162.08, "United States Minor Outlying Islands", "Palmyra Atoll"),
    (-10.42, 105.69, "Indian Ocean Territories", "Christmas Island"),
    (-12.19, 96.83, "Indian Ocean Territories", "Cocos (Keeling) Islands"),
])
def test_a_topic_country_given_as_the_grounds_admin_names_that_ground(lat, lon, given, want) -> None:
    # the enclave ruling (no home past the 2 km tie-break) is for a stated country that differs from the ground;
    # the entity's own Natural Earth ADMIN is not one (the base named these; review 2 found them dropped)
    with geo.detail_view():
        got = geo.site_home(lon, lat, hint=given)
    assert got.country is not None and got.country["display"] == want
