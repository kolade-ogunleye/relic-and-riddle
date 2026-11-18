"""QF-35a's render_surfaces.py with the board's exact memo installed (board_speed.py), as board.py starts it:

    python run_surfaces.py <render_surfaces.py> <render_surfaces.py's own arguments...>

The surfaces, their hashes and their errors are render_surfaces.py's; only the time changes.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    script, args = Path(sys.argv[1]).resolve(), sys.argv[2:]
    tree = Path(args[0]).resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")   # as render_surfaces.py sets them, before src loads
    sys.path.insert(0, str(tree))
    sys.path.insert(1, str(Path(__file__).resolve().parent))
    import board_speed
    board_speed.install()
    sys.argv = [str(script), *args]
    runpy.run_path(str(script), run_name="__main__")
