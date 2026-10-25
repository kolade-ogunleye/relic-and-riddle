"""One-time reset of production to a single posting candidate (operator, 2026-10-25: "there's nothing like dry
run on production. Any run is a candidate for posting ... make run_kailasa_temple_monolith_1792854124 the only
existing candidate").

Without --apply it only lists what it would do. With --apply, in this order, and only while nothing runs:
  1. refuses if a pipeline process is running, a run is producing/publishing, or a busy lease is held;
  2. snapshots the database (SQLite backup API) and moves every file it removes into archive/reset-<UTC>/,
     so the reset is reversible (operator's choice: snapshot, keep archive/backups);
  3. database: keeps only the kept run (its row number becomes 1), its approval, its real publications
     (simulated / dry-run receipts dropped), its bookings, its findings, the usage and audit rows tied to it
     (operator's choice: the others are deleted, which resets the spending caps), and the operator settings;
     every other topic goes back to "queued" with its priority; alerts, improvement proposals and job history
     are cleared; id counters restart after the rows kept; VACUUM;
  4. files: every other output/ run folder, archive/validation, docs/issues, old logs, publication locks of
     deleted runs and Python caches move into the snapshot. Topic photos, fact sheets, music, sound effects,
     caches (operator's choice) and .env are not touched.

Usage (production, elevated, pipeline stopped):
    .venv\\Scripts\\python.exe scripts\\production_reset.py                 # list only
    .venv\\Scripts\\python.exe scripts\\production_reset.py --apply
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KEEP_RUN = "run_kailasa_temple_monolith_1792854124"
ID_TABLES = ("publications", "scheduled_posts", "findings", "llm_usage", "media_usage", "audit_log", "alerts")
# Settings that hold operator choices or live bot state; everything else is per-run or per-job history.
KEEP_SETTINGS = {"daemon_schedule", "scheduler_paused", "telegram_dashboard_id", "publishing_check",
                 "release_announced", "deploy_notice"}
RUN_KEY_ACTIONS = ("approve", "reject", "regen", "resend", "rerender", "postreal", "promote", "postnow", "retryreel",
                   "pubok", "pubno", "simpub", "edit", "cprog", "chist")


def _iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def running_processes(app: Path) -> list:
    """Command lines of other Python processes running this application's main.py (Windows; elevated sees all)."""
    if os.name != "nt":
        return []
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main\\.py' } | "
              "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ["(could not list processes)"]
    me = str(os.getpid())
    return [line for line in out.splitlines() if line.strip() and not line.startswith(me + " ")]


def plan(db: sqlite3.Connection, keep: str) -> dict:
    """What the reset keeps and deletes, without changing anything."""
    run = db.execute("SELECT run_id, topic_id, created_at FROM runs WHERE run_id = ?", (keep,)).fetchone()
    if not run:
        raise SystemExit(f"{keep} is not in this database; nothing done.")
    approval = db.execute("SELECT decision_timestamp FROM approvals WHERE run_id = ?", (keep,)).fetchone()
    # The run's own window: from its start to its review decision (its writer, checks and media spend).
    start = _iso(run[2])
    end = _iso(approval[0]) if approval and approval[0] else start
    rowkey = str(db.execute("SELECT rowid FROM runs WHERE run_id = ?", (keep,)).fetchone()[0])
    post_ids = {str(r[0]) for r in db.execute("SELECT id FROM scheduled_posts WHERE run_id = ?", (keep,))}

    def in_window(ts: str) -> bool:
        try:
            return start <= _iso(ts) <= end
        except ValueError:
            return False

    def audit_tied(payload: str, ts: str) -> bool:
        p = payload or ""
        action, _, target = p.partition(":")
        return (keep in p or in_window(ts)
                or (action in RUN_KEY_ACTIONS and target == rowkey)
                or (action in ("retrypost", "movepost", "cancelpost") and target in post_ids))

    keep_ids = {
        "publications": [r[0] for r in db.execute(
            "SELECT id FROM publications WHERE run_id = ? AND status NOT IN ('simulated') AND channel != 'dry-run'", (keep,))],
        "scheduled_posts": [r[0] for r in db.execute("SELECT id FROM scheduled_posts WHERE run_id = ?", (keep,))],
        "findings": [r[0] for r in db.execute("SELECT id FROM findings WHERE run_id = ?", (keep,))],
        "llm_usage": [r[0] for r in db.execute("SELECT id, created_at FROM llm_usage") if in_window(r[1])],
        "media_usage": [r[0] for r in db.execute("SELECT id, item, created_at FROM media_usage")
                        if keep in (r[1] or "") or in_window(r[2])],
        "audit_log": [r[0] for r in db.execute("SELECT id, payload, created_at FROM audit_log") if audit_tied(r[1], r[2])],
        "alerts": [],
    }
    deleted_runs = [r[0] for r in db.execute("SELECT run_id FROM runs WHERE run_id != ?", (keep,))]
    settings = [r[0] for r in db.execute("SELECT key FROM settings")]
    keep_settings = [k for k in settings if k in KEEP_SETTINGS or k.endswith(":" + keep)]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {"run": run, "window": (start.isoformat(), end.isoformat()), "rowkey": rowkey, "keep_ids": keep_ids,
            "deleted_runs": deleted_runs, "keep_settings": keep_settings,
            "drop_settings": [k for k in settings if k not in keep_settings], "tables": tables,
            "counts": {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ID_TABLES if t in tables}}


def _renumber(db: sqlite3.Connection, table: str, keep_ids: list) -> None:
    """Keep these rows, numbered 1..n in their original order, and restart the table's id counter after them."""
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    marks = ",".join("?" * len(keep_ids)) or "NULL"
    rows = db.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id IN ({marks}) ORDER BY id", keep_ids).fetchall()
    db.execute(f"DELETE FROM {table}")
    for n, row in enumerate(rows, 1):
        values = list(row)
        values[cols.index("id")] = n
        db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
    if db.execute("SELECT name FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        db.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        if rows:
            db.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, len(rows)))


def apply_db(db: sqlite3.Connection, p: dict, keep: str) -> None:
    deleted = p["deleted_runs"]
    marks = ",".join("?" * len(deleted)) or "NULL"
    with db:
        for table in ("approvals", "issues"):
            if table in p["tables"]:
                db.execute(f"DELETE FROM {table} WHERE run_id != ? OR run_id IS NULL", (keep,))
        if "audience_metrics" in p["tables"]:
            db.execute("DELETE FROM audience_metrics WHERE run_id != ?", (keep,))
        if "proposals" in p["tables"]:
            db.execute("DELETE FROM proposals")
        for table, ids in p["keep_ids"].items():
            if table in p["tables"]:
                _renumber(db, table, ids)
        db.execute(f"DELETE FROM runs WHERE run_id IN ({marks})", deleted)
        db.execute("UPDATE runs SET rowid = 1 WHERE run_id = ?", (keep,))
        db.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in p["drop_settings"]])
        if "telegram_transport" in p["tables"]:  # bot records about deleted runs (their cards, media, message links)
            for run_id in deleted:
                db.execute("DELETE FROM telegram_transport WHERE key LIKE ? OR value LIKE ?", (f"%{run_id}%", f"%{run_id}%"))
        topic = p["run"][1]
        cols = {r[1] for r in db.execute("PRAGMA table_info(topics)")}
        sets = "status = 'queued'" + (", last_run_id = NULL" if "last_run_id" in cols else "") + \
               (", times_published = 0" if "times_published" in cols else "")
        db.execute(f"UPDATE topics SET {sets} WHERE id != ?", (topic,))
        db.execute("UPDATE topics SET status = 'published'" + (", last_run_id = ?" if "last_run_id" in cols else "")
                   + " WHERE id = ?", ((keep, topic) if "last_run_id" in cols else (topic,)))
    db.execute("VACUUM")


def file_moves(app: Path, keep: str, deleted_runs: list) -> list:
    """(source, path inside the snapshot) for every file or folder the reset removes."""
    moves = []
    out = app / "output"
    if out.is_dir():
        moves += [(d, Path("output") / d.name) for d in sorted(out.iterdir()) if d.name != keep]
    for rel in ("archive/validation", "docs/issues"):
        if (app / rel).exists():
            moves.append((app / rel, Path(rel)))
    logs = app / "logs"
    if logs.is_dir():
        moves += [(f, Path("logs") / f.name) for f in sorted(logs.glob("*.log*")) if f.is_file()]
    locks = app / "data" / "publication_locks"
    if locks.is_dir():
        moves += [(f, Path("data/publication_locks") / f.name) for f in sorted(locks.iterdir())
                  if any(r in f.name for r in deleted_runs)]
    for cache in (".pytest_cache",):
        if (app / cache).exists():
            moves.append((app / cache, Path(cache)))
    return moves


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--keep", default=KEEP_RUN)
    ap.add_argument("--apply", action="store_true", help="Change production (otherwise list only)")
    args = ap.parse_args(argv)
    app = args.app.resolve()
    db_path = app / "data" / "pipeline.db"
    ro = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    p = plan(ro, args.keep)
    busy = [k for (k,) in ro.execute("SELECT key FROM settings WHERE key LIKE 'busy:%' AND value != ''")]
    active = ro.execute("SELECT run_id FROM runs WHERE status IN ('producing', 'publishing')").fetchall()
    ro.close()
    moves = file_moves(app, args.keep, p["deleted_runs"])

    print(f"Keep {args.keep} (row {p['rowkey']} -> 1); its window {p['window'][0]} .. {p['window'][1]}")
    print(f"Delete {len(p['deleted_runs'])} runs: {', '.join(p['deleted_runs'])}")
    for table, ids in p["keep_ids"].items():
        if table in p["counts"]:
            print(f"  {table}: keep {len(ids)} of {p['counts'][table]}, renumbered from 1")
    print(f"  settings: keep {sorted(p['keep_settings'])}; drop {len(p['drop_settings'])}")
    print(f"  topics: all but {p['run'][1]} back to 'queued' (priorities kept); proposals and alerts cleared")
    print(f"Move {len(moves)} files/folders into archive/reset-<UTC>/:")
    for src, _ in moves:
        print(f"  {src.relative_to(app)}")
    if not args.apply:
        print("\nListed only. Re-run with --apply once the pipeline is stopped.")
        return 0

    procs = running_processes(app)
    if procs or busy or active:
        print(f"\nRefused: stop the pipeline first. Processes: {procs or 'none'}; busy leases: {busy or 'none'}; "
              f"active runs: {[r[0] for r in active] or 'none'}")
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snap = app / "archive" / f"reset-{stamp}"
    snap.mkdir(parents=True)
    src = sqlite3.connect(db_path)
    with sqlite3.connect(snap / "pipeline.db") as dst:
        src.backup(dst)
    print(f"\nDatabase snapshot: {snap / 'pipeline.db'}")
    apply_db(src, p, args.keep)
    src.close()
    for source, rel in moves:
        target = snap / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    for cache in app.rglob("__pycache__"):
        if ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
    (snap / "reset.json").write_text(json.dumps({"kept_run": args.keep, "deleted_runs": p["deleted_runs"],
                                                 "moved": [str(r) for _, r in moves], "at": stamp}, indent=2),
                                     encoding="utf-8", newline="\n")
    check = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    print("After:", {t: check.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("runs", *p["counts"])},
          "| run row:", check.execute("SELECT rowid, run_id FROM runs").fetchall())
    print(f"Done. Everything removed is in {snap}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
