"""
Run once after moving or renaming the project folder.

Stored run records hold absolute file paths (master video, thumbnails). This rewrites any
path under the old project folder to the same path under the current one.

Usage:
  python scripts/relocate.py "C:\\Users\\<user>\\GitHub\\New folder"
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import BASE_DIR  # noqa: E402
from src.db import DB_PATH  # noqa: E402

COLUMNS = ("master_video_path", "thumbnail_cover_path", "thumbnail_grid_path")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    old_root = str(Path(sys.argv[1]))
    new_root = str(BASE_DIR)
    if old_root == new_root:
        print("Old and new folders are the same; nothing to do.")
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    changed = 0
    with conn:
        for col in COLUMNS:
            cur = conn.execute(
                f"UPDATE runs SET {col} = ? || substr({col}, ?) WHERE {col} LIKE ? || '%'",
                (new_root, len(old_root) + 1, old_root),
            )
            changed += cur.rowcount
    conn.close()
    print(f"Updated {changed} stored path(s): {old_root} -> {new_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
