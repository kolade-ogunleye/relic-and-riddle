"""FIG-02 (FIGURES, cycle 5): no label on another label on any data graphic; on the drawings an axis figure keeps half its cap from every other label.

    python fig_label_collisions.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale", "section", "numbers", "timeline", "route", "map"), fm.collision_rows))
