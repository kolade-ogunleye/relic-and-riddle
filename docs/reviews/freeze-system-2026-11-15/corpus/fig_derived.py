"""FIG-07 (FIGURES, cycle 5; lander ruling 2026-11-26): every drawn figure is signed, sourced, a conversion, or derived with a recorded derivation (operands labelled on the drawing, difference / ratio / sum, hedged unless exact); else withheld and recorded.

    python fig_derived.py <tree> <dev checkout> <capture cache.json> [--fixtures-only]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figures_measures as fm  # noqa: E402

if __name__ == "__main__":
    sys.exit(fm.run(__doc__.strip(), ("scale", "timeline"), fm.derived_rows))
