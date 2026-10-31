"""Application-owned v1 interface for an external OS task/service supervisor."""
from dataclasses import dataclass
from pathlib import Path
import importlib
import json
import os
import re
import runpy
import sys
from uuid import UUID

CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ServiceSettings:
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
    def load(cls, path):
        path = Path(path)
        if path.stat().st_size > 16384:
            raise ValueError("Oversized service settings")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        version = value.pop("contract_version", 1) if isinstance(value, dict) else None
        if type(version) is not int or version != CONTRACT_VERSION:
            raise ValueError("Unsupported service contract")
        names = tuple(cls.__dataclass_fields__)
        if set(value) != set(names) or any(not isinstance(value[k], str) for k in names):
            raise ValueError("Invalid service settings fields")
        result = cls(*(Path(value[k]) for k in names[:7]), **{k: value[k] for k in names[7:]})
        if any(not getattr(result, k).is_absolute() for k in names[:7]):
            raise ValueError("Service paths must be absolute")
        UUID(result.client_id)
        if not result.expected_bot_id.isdigit():
            raise ValueError("Invalid expected bot identity")
        if result.source_token_cache.resolve() == result.token_cache.resolve():
            raise ValueError("Managed cache must be separate from enrollment custody")
        if result.runtime_root.resolve() not in result.token_cache.resolve().parents:
            raise ValueError("Managed cache must be under the service runtime root")
        return result

    def configure(self):
        # Must run before config import: no reading dev configuration for a
        # production command. OS account selection and ACLs belong to the host.
        loaded = sys.modules.get("src.config")
        if loaded and Path(loaded.BASE_DIR).resolve() != self.app_root.resolve():
            raise ValueError("Use a fresh process for a different application root")
        os.environ["RELIC_APP_ROOT"] = str(self.app_root)
        os.environ["RELIC_ENVIRONMENT"] = "production"
        config = importlib.import_module("src.config")
        if config.TELEGRAM_BOT_TOKEN.split(":", 1)[0] != self.expected_bot_id:
            raise ValueError("Production bot identity mismatch")
        config.BACKUP_ONEDRIVE_DIR = self.legacy_archive
        return config


def configure_backup(settings, config):
    """Configure the backup process only; the pipeline never gets these paths."""
    config.BACKUP_ENABLED = True
    config.BACKUP_TRANSPORT = "onedrive-api"
    config.BACKUP_ENVIRONMENT = "production"
    config.BACKUP_SPOOL_DIR = settings.runtime_root / "spool" / "Backup"
    config.BACKUP_KEY_FILE = settings.backup_key_file
    config.ONEDRIVE_CLIENT_ID = settings.client_id
    config.ONEDRIVE_SCOPE = "files"
    config.ONEDRIVE_BACKUP_PATH = "Backups/Content Creation/Relic & Riddle"
    config.ONEDRIVE_TOKEN_CACHE = settings.token_cache
    config.BACKUP_SYNC_FOLDER_FALLBACK = False


def execute(command, settings, *, snapshot_id=""):
    if command not in {"pipeline", "backup-check", "backup-cycle", "backup-restore"}:
        raise ValueError("Unknown service operation")
    if command == "backup-restore" and not re.fullmatch(r"production-\d{8}T\d{6}Z-[a-f0-9]{12}", snapshot_id):
        raise ValueError("Invalid production snapshot identity")
    config = settings.configure()
    if command == "pipeline":
        # Pipeline credentials cannot be used to run the separate API worker.
        # Local deliverable archiving preserves the environment's existing flag.
        config.BACKUP_TRANSPORT = "sync-folder"
        os.chdir(settings.app_root)
        sys.argv = [str(settings.app_root / "main.py"), "daemon"]  # live since 2026-10-25 (operator): every production run is a posting candidate
        runpy.run_path(sys.argv[0], run_name="__main__")
        return {"contract_version": CONTRACT_VERSION, "state": "stopped"}, 0
    configure_backup(settings, config)
    from .backup_crypto import load_key
    from .onedrive_backup import OneDriveAuth
    if not settings.token_cache.exists():
        auth = OneDriveAuth(settings.client_id, settings.source_token_cache,
                            load_key(settings.backup_key_file), environment="production", scope="files")
        auth.export_cache(settings.token_cache)
    from . import backup_runtime as backup
    if command == "backup-check":
        result = backup.connection().connect()
        return {"contract_version": CONTRACT_VERSION, "state": "connected", **result}, 0
    if command == "backup-cycle":
        result = backup.cycle_summary(backup.run_cycle())
        return result, 0 if result["cycle_verified"] else 2
    if command == "backup-restore":
        # Validate before deriving a destination, including for a missing spool.
        backup.job_directory(snapshot_id)
        result = backup.restore_job(snapshot_id, settings.runtime_root / "restore-drill" / snapshot_id, cloud=True)
        return {"contract_version": CONTRACT_VERSION, **result}, 0
    raise ValueError("Unknown service operation")
