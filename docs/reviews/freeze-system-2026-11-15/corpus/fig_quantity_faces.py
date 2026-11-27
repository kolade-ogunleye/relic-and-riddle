"""FIG-01 / FIG-03 (FIGURES, cycle 5): every stated quantity on every data graphic through the one figure rule (operator rulings 3 and R8-8): the figure face, number and unit in the figure colour, qualifiers in bone; the data face only on rulers, axes and scale bars, which never carry a hedge or the figure colour.

    python fig_quantity_faces.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale", "section", "numbers", "timeline", "route", "map"), fm.quantity_rows, need_tokens=True))
