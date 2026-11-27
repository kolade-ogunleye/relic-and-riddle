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
    sys.path.insert(0, str(tree))
    sys.path.insert(1, str(Path(__file__).resolve().parent))
    if os.environ.get("BOARD_DEPS") == "1":   # cycle 5: everything the job can depend on (board_deps.py), a record only
        import board_deps   # started before anything of the tree loads and before this process sets its own
        board_deps.install(tree, skip=[s for s in os.environ.get("BOARD_DEPS_SKIP", "").split(os.pathsep) if s])
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")   # as render_surfaces.py sets them, before src loads
    import board_speed
    board_speed.install()
    import board_inputs   # cycle 5: the tree's own data files a render reads (tests' fixtures, the catalogue) too
    board_inputs.install(board_inputs.tree_roots(tree))
    sys.argv = [str(script), *args]
    runpy.run_path(str(script), run_name="__main__")
