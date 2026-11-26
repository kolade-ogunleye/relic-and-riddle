"""RECORDS (FIGURES, cycle 5): every axis figure a drawing's ruler implies is drawn or recorded as yielded (the 9:16
Wieliczka '300'), and a map's region line is drawn or recorded.

    python fig_yield_records.py <tree> <dev checkout> <capture cache.json> [--fixtures-only] [--maps]

Without --maps only the drawings are measured (scale and section: minutes); --maps also reads the map records (a
map capture is hours when the tree's src changed; the region line exists on one dev run's place).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    maps = "--maps" in sys.argv
    if maps:
        sys.argv.remove("--maps")
    import figures_corpus as fc  # noqa: E402
    regions: dict = {}

    def measure(rec):
        if rec["kind"] == "map":
            return fm.region_line_rows(rec, regions.get(rec["run"], ""))
        return fm.axis_yield_rows(rec)
    if maps:
        dev = Path(sys.argv[2])
        sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
        from src import graphics
        regions.update({j["run"]: graphics.region_line(j["input"]) for j in fc.run_inputs(dev) if j["kind"] == "map"})
    sys.exit(fm.run(__doc__.strip(), ("scale", "section", "map") if maps else ("scale", "section"), measure))
