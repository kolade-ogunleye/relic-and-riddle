"""FIG-06 (FIGURES, cycle 5): every label of a data graphic at or over 10 pt at the delivered size (a 390 pt wide phone
frame, the 16:9 inline player included): 28 px on 9:16 at 1080, 50 px on 16:9 at 1920. OPEN at cycle 5: the instrument
and the counts; the 16:9 reflow is not built (see the hand-back).

    python fig_label_floor.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale", "section", "numbers", "timeline", "route", "map"), fm.floor_rows))
