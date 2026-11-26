"""FIG-10 (FIGURES, cycle 5): every name a map or a route draws is a measured, recorded label at the text gate on its own ground (the site's name at the place target); none drawn unmeasured by the base.

    python fig_map_label_contrast.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("map", "route"), fm.contrast_rows))
