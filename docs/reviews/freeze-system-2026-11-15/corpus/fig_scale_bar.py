"""FIG-11 (FIGURES, cycle 5): a map's two-unit scale bar never reads as an equation: the two labels end on their own
ticks, apart and in the order of their lengths, unless the two values are one length.

    python fig_scale_bar.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("map", "route"), fm.scale_bar_rows))
