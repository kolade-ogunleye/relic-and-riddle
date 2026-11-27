"""FIG-02 / FIG-04 (FIGURES, cycle 5): the cross-section's label blocks stand apart as groups, each leader ends on its own figure, every word inside the text box.

    python fig_section_labels.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("section",), fm.section_rows))
