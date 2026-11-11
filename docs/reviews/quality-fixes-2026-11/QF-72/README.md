# QF-72: hourly SQLite backup, pruning, staleness alerts

Spec (docs/reviews/project-quality-review-2026-11-05.md, QF-72): backup RPO is a day with unbounded snapshots, and a
lapsed restore drill pages nobody. Fix: hourly SQLite online backup into the spool, pruning to N verified snapshots,
"last snapshot X h / last drill N d" on `/status` and the heartbeat, alerting over 26 h / 30 d. Acceptance: a
heartbeat with a 27-h-old snapshot contains "backup stale".

## What changed

- **Hourly copies** (`src/backup_checkpoints.py`, called by `backup_runtime.run_cycle`, which the backup task runs
  every 10 minutes): when the newest verified copy is 55 min old, `create_snapshot(database_only=True)` takes the
  database with SQLite's online backup API (committed `-wal` rows included while the pipeline writes) into an archive
  of the existing format (schema 1, same key, `kind: database`, only `data/pipeline.db`) in `<spool>/sqlite-hourly`.
  It counts only after a test restore: `restore_snapshot` (decrypt, every manifest hash, integrity and foreign keys)
  and then the restored file's schema digest and per-table row counts equal to the staged copy's. A failed copy is
  kept as `failed` (only the newest one) and the next attempt waits an hour, so a lasting fault cannot fill the disk.
  The staged copy is switched to a rollback journal so no plaintext `-wal`/`-shm` is left in the spool.
- **Retention** (`prune`): 48 verified hourly copies (each re-hashed before it counts; a damaged one neither counts
  nor is deleted) and 7 cloud-verified daily archives plus the one holding the latest drill receipt. Daily pruning
  reads no archive bytes (the downloaded-SHA-256 copy in OneDrive is the proof; the cloud is never touched).
  Pending, failed, other-environment, linked or unexplained content is never deleted. Restores hold the same lock.
- **Receipt** (`write_status`): after each cycle and each cloud drill the worker writes `data/backup-status.json`
  (timestamps and counts only). A fault in the hourly copy, retention or the receipt itself is logged and never
  stops the upload; the launchers' cycle result is unchanged. The new modules are imported inside those guards, so
  an older overlay that cannot load them still leaves the daily archive, the upload and the cloud drill running.
- **What the operator sees** (`src/backup_health.py`, production only: development has no backup worker):
  `/status` and the heartbeat text get a Recovery section, "Last snapshot X h / last drill N d / hourly copy M min";
  the dashboard gets a line, and Needs you one notice a day per set of alerts, while one holds: backup stale (no
  cloud-verified snapshot in 26 h), restore drill overdue (30 d), hourly copy stale (3 h), backup worker fault
  (a stage the last cycle named as failed: retention, hourly copy). A missing, foreign or
  unreadable receipt alerts. The daemon checks hourly and at the heartbeat.
- **Launcher** (`deployment/windows/runtime/content_runtime.py`): the backup adapter no longer puts the maintenance
  overlay first on the package path (the runtime contract already says the released app's own modules run), so the
  new code is what the backup task executes. This file is installed by `Install-ContentReleaseDeployer.ps1`, i.e. the
  elevated promotion, not by a signed-tag deploy.
- `scripts/backupctl.py`: `status --hourly`, `restore --database-only`. Settings `BACKUP_DATABASE_KEEP`,
  `BACKUP_SNAPSHOT_KEEP`, `BACKUP_STATUS_FILE` in `src/config.py` and `.env.example`. docs/backup.md and
  docs/runtime-contract.md updated.

## Before / after (measure.py, generated fixture; see measurements.json)

| | base 5c7840e9 | this row |
| --- | --- | --- |
| verified hourly copies after one worker cycle | 0 | 1 |
| hourly copy holds the commit that is only in `-wal` | n/a | yes |
| daily archives in the spool (10 old + today's) | 11 | 7 |
| heartbeat text with a 27-h-old snapshot contains "backup stale" | false | true |
| same with a fresh snapshot | false | false |
| archive written the pre-QF-72 way restores | true | true |

Real-data check (`--db`, a read-only online copy of the development database, 2026-11-11): 20 tables, 5,445 rows;
after the hourly copy and its isolated restore 5,445 rows and 0 tables whose row count or content hash (SHA-256 over
the ordered rows) differ. The copy is 1.97 MB encrypted and took 4.7 s on a fully loaded machine; 48 copies are
about 95 MB. The source counts are taken from the copy before the backup, not from the restored file.

## Downstream effects

- Production disk: the hourly spool holds 48 x the encrypted database size (see `real_db.spool_mb_at_default_retention`
  for the development database's size). Daily local archives drop from every day since enrollment (each several
  hundred MB, docs/backup.md) to 7 plus the drilled one; their OneDrive copies stay. The first cycle after release
  deletes the older local daily copies.
- Production alerts: the last recorded cloud drill is 2026-10-22, so "restore drill overdue" will page within days
  of release until a drill is run (`backup-restore`). Until the launcher is promoted, an overlay that shadows
  `backup_runtime` would mean no hourly copies and no receipt: production then shows "no status from the backup
  worker" and alerts, which is the truthful state.
- Workload task definitions: none changed. The backup task's recorded trigger already repeats every 10 minutes.
- Development: no change on screen or in notifications (no worker; nothing shown).
