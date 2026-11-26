"""FIG-05 (FIGURES, cycle 5): a 9:16 scale drawing takes at least 0.45 of the graphics box's height.

    python fig_drawing_share.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale",), fm.share_rows))
