from src import visual_engine


def test_the_cover_says_figures_in_the_unit_the_narration_speaks():
    # Richat's cover said "40+ KM" while its narration said "25 miles": one unit per run (2026-10-29).
    payload = {"narration_script": "A ring 25 miles (40 km) wide."}
    copy = visual_engine._in_spoken_units({"line1": "THE 40+ KM EYE", "badge": "40 KM", "n": 3}, payload)
    assert "KM" not in copy["line1"] and "25" in copy["line1"] and "25" in copy["badge"] and copy["n"] == 3
    assert visual_engine._in_spoken_units({"line1": "40 KM"}, {"narration_script": "It is 40 km wide."}) == {"line1": "40 KM"}
