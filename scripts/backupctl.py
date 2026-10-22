"""Explicit backup enrollment, cloud verification, retry and isolated restore commands."""
import argparse
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=("development", "production"),
                        help="Explicitly select the credential namespace; does not copy or edit .env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen", help="Create the recovery key without displaying or replacing it")
    sub.add_parser("auth", help="Authorize a personal Microsoft account using a browser/device code")
    sub.add_parser("check", help="Check API access to this environment's app backup folder")
    sub.add_parser("snapshot", help="Stage an encrypted snapshot; never claims cloud completion")
    sub.add_parser("cycle", help="Stage at most one daily snapshot and retry queued uploads")
    sync = sub.add_parser("sync", help="Upload pending snapshots and verify downloaded SHA-256")
    sync.add_argument("--force", action="store_true", help="Retry now rather than waiting for scheduled backoff")
    sub.add_parser("status", help="List local staging/upload/verification evidence")
    sub.add_parser("list-cloud", help="List snapshot IDs from OneDrive, not the local spool")
    restore = sub.add_parser("restore", help="Restore into a new isolated directory; publishing stays disabled")
    restore.add_argument("snapshot_id")
    restore.add_argument("--destination", type=Path, required=True)
    restore.add_argument("--cloud", action="store_true", help="Download from OneDrive; no local fallback")
    restore.add_argument("--keep-original-paths", action="store_true", help="Forensic restore without relocation")
    args = parser.parse_args(argv)
    if args.environment:
        existing = sys.modules.get("src.config")
        if existing and existing.BACKUP_ENVIRONMENT != args.environment:
            parser.error("Run backupctl in a fresh process when changing environment")
        os.environ["RELIC_ENVIRONMENT"] = args.environment
    from src import config
    from src.backup_crypto import BackupError, generate_key
    from src.backup_runtime import connection, pending_jobs, stage_current_snapshot, sync_pending, restore_job, run_cycle
    try:
        if args.command == "keygen":
            key_path = config.BACKUP_KEY_FILE.resolve()
            if config.BASE_DIR.resolve() in key_path.parents or config.BACKUP_SPOOL_DIR.resolve() in key_path.parents:
                raise BackupError("Keep the recovery key outside the app checkout and backup spool")
            generate_key(key_path)
            result = {"key_created": str(key_path), "recovery_required": "Store a secure independent copy; it is not in the backup"}
        elif args.command == "auth":
            store = connection()
            result = store.auth.login()
            result.update(store.connect())
        elif args.command == "check":
            result = connection().connect()
        elif args.command == "snapshot":
            result = stage_current_snapshot()
        elif args.command == "cycle":
            result = run_cycle()
        elif args.command == "sync":
            if config.BACKUP_TRANSPORT != "onedrive-api":
                raise BackupError("Enable onedrive-api explicitly in this process or environment before cloud sync")
            result = sync_pending(force=args.force)
        elif args.command == "status":
            result = pending_jobs()
        elif args.command == "list-cloud":
            result = connection().list_snapshots()
        else:
            result = restore_job(args.snapshot_id, args.destination, cloud=args.cloud,
                                 relocate=not args.keep_original_paths)
        print(json.dumps(result, indent=2))
        if args.command in {"sync", "cycle"} and any(r.get("state") not in {"verified", "busy"} for r in result):
            return 2
        if args.command in {"sync", "cycle"} and any(
                job.get("environment") == config.BACKUP_ENVIRONMENT and job.get("state") != "verified"
                for job in pending_jobs()):
            return 2  # Deferred backoff is pending work, not a successful upload.
        return 0
    except BackupError as exc:
        print(json.dumps({"state": "needs-attention", "reason": str(exc)}), file=sys.stderr)
        return 2
    except Exception as exc:
        # Never echo raw SDK/network exceptions, which can contain signed URLs.
        print(json.dumps({"state": "needs-attention", "reason": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
