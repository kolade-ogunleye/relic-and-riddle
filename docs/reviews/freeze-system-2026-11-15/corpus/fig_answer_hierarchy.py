"""FIG-09 (FIGURES, cycle 5): on a scale drawing the subject's own figure (the answer: a depth's floor figure, a
height's or a length's block label) is one step above the drawing's other stated quantities (the landmark's figure,
the derived bracket).

    python fig_answer_hierarchy.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale",), fm.hierarchy_rows))
