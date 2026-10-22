"""Archive explicitly named validation runs; retain paid usage and all unrelated state.

Preview is the default. Stop the bot/daemon before --apply --workers-stopped.
Failed validation attempts additionally require --include-failed. Never use the
whole-database reset tool for routine production deployment.
"""
import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def archive_runs(root, run_ids, *, apply=False, workers_stopped=False, include_failed=False):
    root = Path(root).resolve()
    run_ids = list(dict.fromkeys(run_ids))
    if not run_ids or any(not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in run_ids):
        raise ValueError("Supply explicit, safe validation run IDs")
    if apply and not workers_stopped:
        raise ValueError("Stop workers and supply --workers-stopped before applying")
    db_path = root / "data" / "pipeline.db"
    if not db_path.is_file():
        raise ValueError("Existing database not found")
    marks = ",".join("?" for _ in run_ids)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    moved = []
    try:
        # Validate and hold the write lock throughout the archive operation.
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        rows = conn.execute(f"SELECT * FROM runs WHERE run_id IN ({marks})", run_ids).fetchall()
        if len(rows) != len(run_ids):
            raise ValueError("One or more requested runs do not exist; nothing archived")
        permitted = {"simulated", "failed"} if include_failed else {"simulated"}
        if any(row["status"] not in permitted for row in rows):
            raise ValueError("Only simulated runs (or explicitly included failed attempts) can be archived")
        if conn.execute(
            f"SELECT 1 FROM publications WHERE run_id IN ({marks}) AND status != 'simulated' LIMIT 1", run_ids
        ).fetchone():
            raise ValueError("Live or uncertain publication evidence exists; nothing archived")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "parent_run_id" in columns and conn.execute(
            f"SELECT 1 FROM runs WHERE parent_run_id IN ({marks}) AND run_id NOT IN ({marks}) LIMIT 1",
            run_ids + run_ids,
        ).fetchone():
            raise ValueError("An unselected child run depends on this run")
        tables = []
        counts = {}
        for entry in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = entry[0]
            quoted = '"' + name.replace('"', '""') + '"'
            fields = {row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")}
            if "run_id" in fields:
                # Usage records must survive even if a future schema links them to runs.
                if name in {"llm_usage", "media_usage"}:
                    continue
                tables.append((name, quoted))
                counts[name] = conn.execute(
                    f"SELECT COUNT(*) FROM {quoted} WHERE run_id IN ({marks})", run_ids
                ).fetchone()[0]
        output = (root / "output").resolve()
        if root not in output.parents:
            raise ValueError("Output directory resolves outside the application root")
        folders = []
        for run_id in run_ids:
            folder = output / run_id
            if folder.resolve().parent != output:
                raise ValueError("Run directory resolves outside output")
            if folder.exists():
                folders.append(folder)
        result = {"run_ids": run_ids, "rows": counts, "output_folders": [p.name for p in folders],
                  "paid_usage": "preserved", "applied": apply}
        if not apply:
            return result
        archive_parent = (root / "archive" / "validation").resolve()
        if root not in archive_parent.parents:
            raise ValueError("Archive directory resolves outside the application root")
        archive = archive_parent / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        archive.mkdir(parents=True, exist_ok=False)
        # Use a separate read connection: backing up a connection holding its own
        # write transaction can block. The held lock excludes other writers.
        with sqlite3.connect(str(db_path)) as source, sqlite3.connect(str(archive / "pipeline.db")) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database backup failed verification")
        result["archive"] = str(archive)
        (archive / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        for folder in folders:
            dest = archive / folder.name
            shutil.move(str(folder), str(dest))
            moved.append((folder, dest))
        # Current foreign keys only point from run-owned tables into runs.
        for name, quoted in sorted(tables, key=lambda item: item[0] == "runs"):
            conn.execute(f"DELETE FROM {quoted} WHERE run_id IN ({marks})", run_ids)
        conn.execute(
            f"UPDATE topics SET status='queued', last_run_id=NULL, updated_at=? WHERE last_run_id IN ({marks})",
            [datetime.now(timezone.utc).isoformat()] + run_ids,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        for folder, archived in reversed(moved):
            shutil.move(str(archived), str(folder))
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=ROOT,
                        help="Application root when running a separately shipped maintenance tool")
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers-stopped", action="store_true")
    parser.add_argument("--include-failed", action="store_true")
    args = parser.parse_args()
    result = archive_runs(args.app_root, args.run_id, apply=args.apply, workers_stopped=args.workers_stopped,
                          include_failed=args.include_failed)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
