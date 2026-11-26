"""FIG-12 (FIGURES, cycle 5): every timeline sets its event labels in one placement relative to their years (below,
or beside), never two in one column. Label-on-label crowding is fig_label_collisions.py's.

    python fig_timeline_placement.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("timeline",), fm.placement_rows))
