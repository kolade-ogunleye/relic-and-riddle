"""Real map composition must retain visible neighbouring countries' names."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src import geo

# The real render reads the gitignored Natural Earth data (scripts/fetch_geodata.py, fetch_marine.py and
# fetch_relief.py); the benchmark machinery it runs through refuses a missing or different relief loudly, so
# without the data the test skips, as the repository's other map tests do, and with it the render runs in full.
needs_data = pytest.mark.skipif(not (geo.available() and geo.COUNTRIES_10M_FILE.is_file() and geo.MARINE_FILE.is_file()
                                     and geo.RELIEF_HR_FILE.is_file()),
                                reason="Natural Earth outlines, marine polygons or SR_HR relief not fetched")


@needs_data
@pytest.mark.parametrize("site,size,expected", [
    ({"latitude": 51.1536, "longitude": 14.9872, "country": "Germany",
      "label": "Goerlitz Old Town Bridge"}, (1080, 1920), "CZECHIA"),
    ({"latitude": 40.2556, "longitude": 24.2119, "country": "Greece",
      "label": "Mt Athos, Karyes"}, (1920, 1080), "TURKEY"),
    ({"latitude": 40.6600, "longitude": 72.8300, "country": "Kyrgyzstan",
      "label": "Barak"}, (1920, 1080), "TAJIKISTAN"),
    ({"latitude": 57.7769, "longitude": 26.0466, "country": "Estonia",
      "label": "Valga and Valka"}, (1080, 1920), "FINLAND"),
    ({"latitude": 14.6672, "longitude": -17.3986, "country": "Senegal",
      "label": "Goree Island"}, (1080, 1920), "GAMBIA"),
    ({"latitude": 14.6672, "longitude": -17.3986, "country": "Senegal",
      "label": "Goree Island"}, (1920, 1080), "GAMBIA"),
], ids=["gorlitz", "athos", "barak", "valga", "goree-tall", "goree-wide"])
def test_visible_neighbour_retained_with_scale_bar_and_other_labels(site, size, expected):
    root = Path(__file__).resolve().parents[1]
    program = r'''
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "map_benchmark", root / "docs/reviews/quality-fixes-2026-11/QF-20/measure.py"
)
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
spec.loader.exec_module(benchmark)
with tempfile.TemporaryDirectory(prefix="neighbour-layout-") as temporary:
    scratch = Path(temporary)
    benchmark.unpack_inputs(scratch / "geo")
    benchmark.init_worker(str(root), str(scratch))
    site, size = json.loads(sys.argv[2])
    result = benchmark.render_one(("map", 0, site, tuple(size)))
    layout_path = benchmark.OUTPUT / f"site-001-{size[0]}x{size[1]}.mp4.layout.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8")) if layout_path.is_file() else {}
    print(json.dumps({"result": result, "neighbours": layout.get("neighbours", []), "water": layout.get("water", []),
                      "country": (layout.get("country") or {}).get("text")}))
'''
    completed = subprocess.run([sys.executable, "-c", program, str(root), json.dumps([site, size])], cwd=root,
                               capture_output=True, text=True, encoding="utf-8", timeout=1200, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    rendered = json.loads(completed.stdout)
    assert rendered["result"]["faults"] == [], rendered
    assert not rendered["result"]["home_drop"], rendered
    if site["label"] == "Goree Island":
        # freeze lane A cycle 3 (lander rulings, deliberate): the site stands in the middle third (GAMBIA's 16:9 name
        # was found only with Goree at x 0.26 of title-safe), and the 9:16 neighbour floor stays 27 px (GAMBIA's strip
        # holds it at no allowed zoom). Where no strip holds it, it is left out and recorded on the ledger; the frame
        # still names SENEGAL and at least one other context name (a sea or a neighbour)
        assert expected in rendered["neighbours"] or             f"the neighbour's name {expected}" in rendered["result"].get("omissions", []), rendered
        assert rendered["country"] == "SENEGAL", rendered
        assert rendered["neighbours"] or rendered["water"], rendered
        return
    assert expected in rendered["neighbours"], rendered
    # freeze lane A cycle 3 (lander ruling, the scale bar is furniture): the bar is placed after every name and never
    # displaces one; where no corner holds it, it is omitted and recorded on the ledger (the benchmark opens it, as
    # make_all does), never the neighbour's name
    assert f"the neighbour's name {expected}" not in rendered["result"].get("omissions", []), rendered
