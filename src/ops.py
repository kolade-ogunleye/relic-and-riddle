"""
src/ops.py
==========
Operations & maintenance suite for the Autonomous Meta Content Pipeline.
1. Logging with secret masking applied to every record (tokens never reach logs).
2. Asset Lifecycle Manager: prunes intermediate scratch audio (> 7 days) while
   preserving master MP4 cuts, thumbnails, ASS captions, and metadata.
3. Pre-Flight Health Checker: FFmpeg/ffprobe, disk space, fonts, connectivity, credentials.
"""

import logging
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Tuple

from . import config
from .config import BASE_DIR, OUTPUT_DIR, FONTS_DIR, is_configured

# Telegram bot tokens appear inside request URLs (".../bot<id>:<secret>/sendPhoto").
_TELEGRAM_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")


class SecretMasker:
    """Sanitizes every configured credential out of strings."""

    SECRET_NAMES = (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "UPLOAD_POST_API_KEY",
        "META_ACCESS_TOKEN",
        "TELEGRAM_BOT_TOKEN",
    )

    def __init__(self):
        self.sensitive_tokens = [
            v for v in (getattr(config, n, "") for n in self.SECRET_NAMES)
            if is_configured(v) and len(v) > 4
        ]

    def mask(self, text: str) -> str:
        if not text:
            return text
        sanitized = str(text)
        for token in self.sensitive_tokens:
            replacement = token[:3] + "..." + token[-3:] if len(token) > 12 else "***"
            sanitized = sanitized.replace(token, replacement)
        return _TELEGRAM_TOKEN_RE.sub("bot***", sanitized)


class _MaskingFilter(logging.Filter):
    def __init__(self, masker: SecretMasker):
        super().__init__()
        self.masker = masker

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.masker.mask(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = self.masker.mask(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """Configures root logging once; every handler masks secrets."""
    root = logging.getLogger()
    if getattr(root, "_pipeline_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    handler.addFilter(_MaskingFilter(SecretMasker()))
    root.addHandler(handler)
    root.setLevel(level)
    # Library debug output can include full request URLs; keep it quiet.
    for noisy in ("urllib3", "httpx", "httpx2", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._pipeline_configured = True


log = logging.getLogger(__name__)


class AssetLifecycleManager:
    """Maintains storage hygiene by pruning intermediate scratch files older than retention_days."""

    SCRATCH_PATTERNS = ("ambient_bed.wav", "voiceover.mp3", "*temp*", "*.tmp")

    def __init__(self, output_dir: Path = OUTPUT_DIR, retention_days: int = 7):
        self.output_dir = output_dir
        self.retention_days = retention_days

    def purge_intermediate_assets(self) -> Dict[str, Any]:
        cutoff_sec = time.time() - self.retention_days * 86400
        purged_files = 0
        reclaimed_bytes = 0

        for run_dir in self.output_dir.iterdir():
            if not (run_dir.is_dir() and run_dir.name.startswith("run_")):
                continue
            if run_dir.stat().st_mtime >= cutoff_sec:
                continue
            for pattern in self.SCRATCH_PATTERNS:
                for f in run_dir.glob(pattern):
                    try:
                        size = f.stat().st_size
                        f.unlink()
                        purged_files += 1
                        reclaimed_bytes += size
                    except OSError as e:
                        log.warning("Could not purge %s: %s", f, e)

        mb_reclaimed = reclaimed_bytes / (1024 * 1024)
        log.info("Purged %d intermediate files (%.2f MB reclaimed).", purged_files, mb_reclaimed)
        return {"purged_count": purged_files, "mb_reclaimed": round(mb_reclaimed, 2)}


class PreFlightHealthChecker:
    """Pre-run checks so a cycle fails before spending money, not halfway through."""

    def __init__(self, min_disk_gb: float = 5.0):
        self.min_disk_gb = min_disk_gb

    def _check_binary(self, name: str) -> Tuple[bool, str]:
        try:
            res = subprocess.run([name, "-version"], capture_output=True, text=True, check=True, timeout=20)
            return True, res.stdout.splitlines()[0]
        except Exception as e:
            return False, f"{name} not found or failed: {e}"

    def check_ffmpeg(self) -> Tuple[bool, str]:
        return self._check_binary("ffmpeg")

    def check_ffprobe(self) -> Tuple[bool, str]:
        return self._check_binary("ffprobe")

    def check_disk_space(self) -> Tuple[bool, str]:
        try:
            free_gb = shutil.disk_usage(str(BASE_DIR)).free / (1024 ** 3)
            if free_gb < self.min_disk_gb:
                return False, f"Insufficient disk space: {free_gb:.2f} GB free (Required: {self.min_disk_gb} GB)"
            return True, f"{free_gb:.2f} GB free disk space available"
        except Exception as e:
            return False, f"Could not check disk space: {e}"

    def check_fonts(self) -> Tuple[bool, str]:
        for name in ("Anton-Regular.ttf", "Montserrat-Variable.ttf"):
            if not (FONTS_DIR / name).exists():
                return False, f"Missing required font: {FONTS_DIR / name}"
        return True, "All required typography fonts verified on disk"

    def check_network(self) -> Tuple[bool, str]:
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=3):
                return True, "Internet connectivity verified"
        except OSError:
            return False, "No active internet connection detected"

    def check_live_credentials(self) -> Tuple[bool, str]:
        """Everything an unattended production cycle needs."""
        missing = []
        if not is_configured(config.ANTHROPIC_API_KEY):
            missing.append("ANTHROPIC_API_KEY (script writing + fact checking)")
        if not (is_configured(config.TELEGRAM_BOT_TOKEN) and is_configured(config.TELEGRAM_CHAT_ID)):
            missing.append("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (human approval)")
        elif config.TELEGRAM_CHAT_ID.strip() == config.TELEGRAM_BOT_TOKEN.split(":")[0]:
            missing.append("a correct TELEGRAM_CHAT_ID: it is set to the bot's own ID; use your personal "
                           "numeric user ID (from @userinfobot) and send /start to the bot")
        upload_post = is_configured(config.UPLOAD_POST_API_KEY) and is_configured(config.UPLOAD_POST_USER)
        graph = is_configured(config.META_ACCESS_TOKEN) and is_configured(config.META_IG_ACCOUNT_ID)
        if not (upload_post or graph):
            missing.append("a publisher (UPLOAD_POST_API_KEY+UPLOAD_POST_USER or META_ACCESS_TOKEN+META_IG_ACCOUNT_ID)")
        if missing:
            return False, "Missing: " + "; ".join(missing)
        return True, "Script, approval and publishing credentials configured"

    def run_all(self, require_live: bool = False) -> Dict[str, Any]:
        results = {
            "ffmpeg": self.check_ffmpeg(),
            "ffprobe": self.check_ffprobe(),
            "disk": self.check_disk_space(),
            "fonts": self.check_fonts(),
            "network": self.check_network(),
            "credentials": self.check_live_credentials(),
        }
        required = [k for k in results if k != "credentials" or require_live]
        return {
            "all_passed": all(results[k][0] for k in required),
            "details": {
                k: {"passed": ok, "msg": msg, "required": k in required}
                for k, (ok, msg) in results.items()
            },
        }
