"""Explicit-path adapters for the existing content application and backup tool.

Secret values are consumed only by the application's existing configuration and
credential providers. This adapter never returns them or prints SDK exceptions.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import re
import runpy
import sys
import subprocess
import threading
import tempfile
import traceback
from types import ModuleType
from uuid import UUID


def watch_launcher() -> None:
    """End this worker tree if Task Scheduler terminates its launcher."""
    if os.name != "nt":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Windows venv python.exe may be a redirector with another child process.
    # Watch the actual PowerShell launcher, not that intermediate parent.
    launcher_pid = int(os.environ.get("HNSHL_CONTENT_LAUNCHER_PID", str(os.getppid())))
    if launcher_pid <= 0 or launcher_pid == os.getpid():
        raise ValueError("Invalid workload launcher identity")
    handle = kernel.OpenProcess(0x00100000, False, launcher_pid)  # SYNCHRONIZE only.
    if not handle:
        raise OSError("Cannot watch the workload launcher")
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"

    def parent_ended() -> None:
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel.CloseHandle(handle)
        try:
            subprocess.run([str(taskkill), "/PID", str(os.getpid()), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
        os._exit(1)

    threading.Thread(target=parent_ended, name="launcher-watch", daemon=True).start()


@dataclass(frozen=True)
class LaunchSettings:
    app_root: Path
    maintenance_root: Path
    runtime_root: Path
    backup_key_file: Path
    source_token_cache: Path
    token_cache: Path
    legacy_archive: Path
    client_id: str
    expected_bot_id: str

    @classmethod
    def load(cls, path: Path) -> LaunchSettings:
        if path.stat().st_size > 16384:
            raise ValueError("Launch manifest is oversized")
        with path.open(encoding="utf-8-sig") as stream:
            value: object = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Launch manifest must be an object")
        fields = ("app_root", "maintenance_root", "runtime_root", "backup_key_file",
                  "source_token_cache", "token_cache", "legacy_archive", "client_id", "expected_bot_id")
        if set(value) != set(fields) or any(not isinstance(value.get(k), str) for k in fields):
            raise ValueError("Launch manifest fields are invalid")
        result = cls(*(Path(value[k]) for k in fields[:7]),
                     client_id=value["client_id"], expected_bot_id=value["expected_bot_id"])
        if not all(p.is_absolute() for p in (result.app_root, result.maintenance_root,
                                           result.runtime_root, result.backup_key_file,
                                           result.source_token_cache, result.token_cache, result.legacy_archive)):
            raise ValueError("Launch paths must be absolute")
        UUID(result.client_id)
        if not result.expected_bot_id.isdigit():
            raise ValueError("Expected bot identity must be numeric")
        if result.source_token_cache == result.token_cache:
            raise ValueError("Managed backup cache must be separate from the original cache")
        if result.runtime_root not in result.token_cache.parents:
            raise ValueError("Managed cache must stay inside the protected runtime")
        return result


def load_application(settings: LaunchSettings) -> tuple[ModuleType, ModuleType]:
    os.environ["RELIC_ENVIRONMENT"] = "Production"
    sys.path.insert(0, str(settings.app_root))
    package = importlib.import_module("src")
    config = importlib.import_module("src.config")
    # Existing application provider reads its own config; no values leave this process.
    token: str = config.TELEGRAM_BOT_TOKEN
    if token.split(":", 1)[0] != settings.expected_bot_id:
        raise ValueError("Production identity mismatch")
    if Path(config.BASE_DIR).resolve() != settings.app_root.resolve():
        raise ValueError("Unexpected application configuration module")
    return package, config


def configure_backup(settings: LaunchSettings, package: ModuleType, config: ModuleType) -> None:
    # Preserve production data and destination; eliminate Path.home() dependencies.
    # The released application's own backup modules run (docs/runtime-contract.md): the hourly SQLite copies,
    # retention and status receipt live there (QF-72), and a maintenance overlay placed first on the package path
    # would shadow them with older code. maintenance_root stays a manifest field and the interpreter's location.
    package.__path__ = [str(settings.app_root / "src")]
    config.BACKUP_TRANSPORT = "onedrive-api"
    config.BACKUP_ENVIRONMENT = "production"
    config.BACKUP_SPOOL_DIR = settings.runtime_root / "spool" / "Backup"
    config.BACKUP_KEY_FILE = settings.backup_key_file
    config.ONEDRIVE_CLIENT_ID = settings.client_id
    config.ONEDRIVE_SCOPE = "files"
    config.ONEDRIVE_BACKUP_PATH = "Backups/Content Creation/Relic & Riddle"
    config.ONEDRIVE_TOKEN_CACHE = settings.token_cache
    # A logged-off service cannot promise upload by a desktop sync client.
    # The durable API spool remains queued for retries; cloud verification is required.
    config.BACKUP_SYNC_FOLDER_FALLBACK = False
    config.BACKUP_ENABLED = True


def bootstrap_backup_cache(settings: LaunchSettings) -> None:
    if settings.token_cache.exists():
        return
    if not settings.source_token_cache.is_file():
        raise ValueError("Original backup enrollment is unavailable")
    crypto = importlib.import_module("src.backup_crypto")
    provider = importlib.import_module("src.onedrive_backup")
    # The existing provider performs credential handling in memory and writes the
    # service-owned encrypted cache. Original key/cache files are never modified.
    auth = provider.OneDriveAuth(settings.client_id, settings.source_token_cache,
                                 crypto.load_key(settings.backup_key_file),
                                 environment="production", scope="files")
    auth.path = settings.token_cache
    auth.cache.has_state_changed = True
    auth._save()


def telegram_check(config: ModuleType, expected_bot_id: str) -> bool:
    requests = importlib.import_module("requests")
    with requests.Session() as session:
        session.trust_env = False
        # Read-only identity request. Never expose a token-bearing URL or exception.
        with session.get("https://api.telegram.org/bot" + config.TELEGRAM_BOT_TOKEN + "/getMe",
                         timeout=(5, 15)) as response:
            response.raise_for_status()
            payload: object = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                return False
            result: object = payload.get("result")
            return isinstance(result, dict) and str(result.get("id")) == expected_bot_id


def check_backup_isolation(settings: LaunchSettings) -> None:
    # Access-check only: never read a byte of either file in the pipeline process.
    for path in (settings.backup_key_file, settings.source_token_cache):
        try:
            with path.open("rb"):
                pass
        except PermissionError:
            continue
        raise PermissionError("Pipeline identity has excessive access to backup custody")


def execute(command: str, settings: LaunchSettings, snapshot_id: str = "") -> int:
    if command == "backup-restore" and not re.fullmatch(r"production-\d{8}T\d{6}Z-[a-f0-9]{12}", snapshot_id):
        raise ValueError("A valid production snapshot identity is required")
    package, config = load_application(settings)
    config.BACKUP_ONEDRIVE_DIR = settings.legacy_archive
    if command == "pipeline-check":
        check_backup_isolation(settings)
        verified = telegram_check(config, settings.expected_bot_id)
        if getattr(config, "BACKUP_ENABLED", False):
            with tempfile.NamedTemporaryFile(prefix=".hnshl-", suffix=".tmp", dir=settings.legacy_archive):
                pass
        print(json.dumps({"application_identity_verified": verified, "legacy_archive_path_verified": True,
                          "backup_custody_isolated": True}))
        return 0 if verified else 1
    if command == "pipeline":
        os.chdir(settings.app_root)
        sys.argv = [str(settings.app_root / "main.py"), "daemon"]  # live since 2026-10-25 (operator): every production run is a posting candidate
        runpy.run_path(sys.argv[0], run_name="__main__")
        return 0
    configure_backup(settings, package, config)
    bootstrap_backup_cache(settings)
    runtime = importlib.import_module("src.backup_runtime")
    if command == "backup-check":
        runtime.connection().connect()
        previous = runtime.pending_jobs(settings.app_root / "archive" / "backups")
        pending = sum(job.get("environment") == "production" and job.get("state") != "verified" for job in previous)
        print(json.dumps({"backup_api_verified": True, "desktop_fallback_enabled": False,
                          "original_pending_jobs": pending}))
    elif command == "backup-cycle":
        results = runtime.run_cycle()
        verified = all(r.get("state") in {"verified", "busy"} for r in results)
        jobs = runtime.pending_jobs()
        pending = any(job.get("environment") == "production" and job.get("state") != "verified"
                      for job in jobs)
        proven = [job for job in jobs if job.get("environment") == "production" and job.get("state") == "verified"
                  and job.get("receipt", {}).get("verification") == "download-sha256"
                  and job.get("receipt", {}).get("sha256") == job.get("sha256")]
        latest = max(proven, key=lambda job: job.get("created_at", "")) if proven else None
        fresh = False
        if latest and re.fullmatch(r"production-\d{8}T\d{6}Z-[a-f0-9]{12}", latest.get("snapshot_id", "")):
            timestamp = datetime.strptime(latest["snapshot_id"][11:27], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            fresh = now - timedelta(hours=26) <= timestamp <= now + timedelta(minutes=5)
        success = verified and not pending and fresh
        print(json.dumps({"cycle_verified": success, "jobs": len(results),
                          "latest_snapshot_id": latest.get("snapshot_id") if latest else None,
                          "verification": "download-sha256" if latest else None}))
        return 0 if success else 2
    elif command == "backup-restore":
        destination = settings.runtime_root / "restore-drill" / snapshot_id
        result = runtime.restore_job(snapshot_id, destination, cloud=True, relocate=True)
        verified = (result.get("state") == "restore-tested" and result.get("source") == "onedrive-api"
                    and result.get("snapshot_id") == snapshot_id and result.get("database_integrity") == "ok"
                    and result.get("publishing_disabled") is True and result.get("checksums_verified", 0) > 0)
        print(json.dumps({"restore_completed": verified, "snapshot_id": snapshot_id,
                          "checksums_verified": result.get("checksums_verified", 0),
                          "database_integrity": result.get("database_integrity"),
                          "publishing_disabled": result.get("publishing_disabled", False)}))
        return 0 if verified else 1
    else:
        raise ValueError("Unsupported runtime operation")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pipeline-check", "pipeline", "backup-check",
                                            "backup-cycle", "backup-restore"))
    parser.add_argument("--snapshot-id", default="")
    args = parser.parse_args(argv)
    try:
        watch_launcher()
        settings = LaunchSettings.load(Path(__file__).resolve().parent.parent / "launch.json")
        return execute(args.command, settings, args.snapshot_id)
    except Exception as exc:
        # Even an SDK exception's message may contain a credential-bearing URL.
        frames = traceback.extract_tb(exc.__traceback__)
        origin = frames[-1] if frames else None
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__,
                          "error_module": Path(origin.filename).name if origin else "unknown",
                          "error_line": origin.lineno if origin else 0}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
