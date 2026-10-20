"""
Start the pipeline database afresh (every ID counter back to 1, every topic queued with no
history), keeping a full backup and the two things a reset must not lose:

  - this month's paid usage (media_usage and llm_usage rows), so the monthly spend caps
    still count money already spent;
  - topic proposals waiting for your Add/Skip.

--wipe (test environments): no backup and nothing carried over; everything goes.

Dry run by default: prints what it would do. Stop the bot and the daemon first.

Usage:
  python scripts/reset_database.py                  # dry run
  python scripts/reset_database.py --apply          # back up, rebuild, carry over
  python scripts/reset_database.py --apply --wipe --purge-output --purge-legacy
      test environment: no backup, nothing kept
  python scripts/reset_database.py --apply --purge-output --purge-legacy
      also delete output/ (old run folders) and archive/legacy_data/
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import DB_PATH, PipelineDatabase  # noqa: E402

KEEP_SETTINGS = ("topic_proposals_pending",)
USAGE_TABLES = ("media_usage", "llm_usage")


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def carry_over(conn: sqlite3.Connection, month: str) -> dict:
    """This month's usage rows and the settings worth keeping, read from the old database."""
    kept = {}
    for table in USAGE_TABLES:
        cols = [c for c in _columns(conn, table) if c != "id"]
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE created_at LIKE ?", (month + "%",)).fetchall()
        kept[table] = (cols, rows)
    marks = ",".join("?" * len(KEEP_SETTINGS))
    kept["settings"] = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", KEEP_SETTINGS).fetchall()
    return kept


def summary(conn: sqlite3.Connection) -> str:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence'")]
    return ", ".join(f"{t}={conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in sorted(tables))


def reset(db_path: Path, archive_root: Path, apply: bool, now: datetime = None, wipe: bool = False):
    """Returns the backup path (where it would go, on a dry run), or None with wipe."""
    now = now or datetime.now(timezone.utc)
    backup = None if wipe else archive_root / now.strftime("%Y%m%dT%H%M%SZ") / "pipeline.db"
    old = sqlite3.connect(db_path)
    kept = ({t: ([], []) for t in USAGE_TABLES} | {"settings": []}) if wipe else carry_over(old, now.strftime("%Y-%m"))
    print(f"Current database: {summary(old)}")
    print(f"Will keep: {len(kept['media_usage'][1])} media and {len(kept['llm_usage'][1])} LLM usage rows from "
          f"{now:%Y-%m}; settings {[k for k, _ in kept['settings']]}")
    print(f"Backup: {backup or 'none (--wipe)'}")
    if not apply:
        old.close()
        print("Dry run: nothing changed. Re-run with --apply.")
        return backup
    if backup:
        backup.parent.mkdir(parents=True)
        with sqlite3.connect(backup) as dest:
            old.backup(dest)  # a consistent copy, WAL contents included
    old.close()
    if backup and sqlite3.connect(backup).execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup failed its integrity check; the live database was not touched.")
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    fresh = PipelineDatabase(db_path=db_path)  # creates the schema and loads the topic bank
    with fresh._conn() as conn:
        for table in USAGE_TABLES:
            cols, rows = kept[table]
            if rows:
                conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
        conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", kept["settings"])
    with sqlite3.connect(db_path) as conn:
        print(f"Fresh database: {summary(conn)}")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Make the changes (default: dry run)")
    parser.add_argument("--wipe", action="store_true", help="No backup and nothing carried over (test environments)")
    parser.add_argument("--purge-output", action="store_true", help="Also delete output/ (old run folders)")
    parser.add_argument("--purge-legacy", action="store_true", help="Also delete archive/legacy_data/")
    args = parser.parse_args()
    reset(DB_PATH, ROOT / "archive" / "db_reset", args.apply, wipe=args.wipe)
    for flag, folder in ((args.purge_output, ROOT / "output"), (args.purge_legacy, ROOT / "archive" / "legacy_data")):
        if flag and folder.exists():
            if args.apply:
                shutil.rmtree(folder)
                print(f"Deleted {folder}")
            else:
                print(f"Would delete {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
