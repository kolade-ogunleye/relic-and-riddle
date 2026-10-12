"""
src/ops.py
==========
Zero-Trust Operations & Maintenance Suite for the Autonomous Meta Content Pipeline.
1. Secret Masking: In-memory masking of API tokens, chat IDs, and authorization headers in logs.
2. Asset Lifecycle Manager: Cleans up intermediate scratch audio/video files (> 7 days) while
   preserving master MP4 cuts, thumbnails, ASS captions, and metadata permanently.
3. Pre-Flight Health Checker: Validates FFmpeg, disk space (> 5GB), font files, and connectivity.
"""

import os
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple

from .config import (
    BASE_DIR,
    OUTPUT_DIR,
    FONTS_DIR,
    UPLOAD_POST_API_KEY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
    KLING_API_KEY,
)


class SecretMasker:
    """Sanitizes sensitive credentials from strings, dictionaries, and error messages."""
    def __init__(self):
        self.sensitive_tokens = []
        for k in [
            UPLOAD_POST_API_KEY,
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            ANTHROPIC_API_KEY,
            GEMINI_API_KEY,
            KLING_API_KEY,
        ]:
            if k and len(k) > 4 and "your_" not in k:
                self.sensitive_tokens.append(k)

    def mask(self, text: str) -> str:
        if not text:
            return text
        sanitized = str(text)
        for token in self.sensitive_tokens:
            if len(token) > 8:
                replacement = token[:3] + "..." + token[-3:]
            else:
                replacement = "***"
            sanitized = sanitized.replace(token, replacement)
        return sanitized


class AssetLifecycleManager:
    """Maintains storage hygiene by pruning intermediate scratch files older than retention_days."""
    def __init__(self, output_dir: Path = OUTPUT_DIR, retention_days: int = 7):
        self.output_dir = output_dir
        self.retention_days = retention_days

    def purge_intermediate_assets(self) -> Dict[str, Any]:
        now = time.time()
        cutoff_sec = now - (self.retention_days * 86400)
        purged_files = 0
        reclaimed_bytes = 0

        # Patterns that are safe to delete after retention window:
        scratch_patterns = ["ambient_bed.wav", "voiceover.mp3", "*temp*", "*.tmp"]

        for run_dir in self.output_dir.iterdir():
            if run_dir.is_dir() and run_dir.name.startswith("run_"):
                # Check run folder modification time
                mtime = run_dir.stat().st_mtime
                if mtime < cutoff_sec:
                    for pattern in scratch_patterns:
                        for f in run_dir.glob(pattern):
                            try:
                                size = f.stat().st_size
                                f.unlink()
                                purged_files += 1
                                reclaimed_bytes += size
                            except Exception:
                                pass

        mb_reclaimed = reclaimed_bytes / (1024 * 1024)
        print(f"[OPS LIFECYCLE] Purged {purged_files} intermediate files ({mb_reclaimed:.2f} MB reclaimed).")
        return {"purged_count": purged_files, "mb_reclaimed": round(mb_reclaimed, 2)}


class PreFlightHealthChecker:
    """Pre-run health checks ensuring zero mid-execution crashes."""
    def __init__(self, min_disk_gb: float = 5.0):
        self.min_disk_gb = min_disk_gb

    def check_ffmpeg(self) -> Tuple[bool, str]:
        try:
            res = subprocess.check_output(["ffmpeg", "-version"], stderr=subprocess.STDOUT).decode()
            first_line = res.splitlines()[0]
            return True, first_line
        except Exception as e:
            return False, f"FFmpeg not found or failed: {e}"

    def check_disk_space(self) -> Tuple[bool, str]:
        try:
            total, used, free = shutil.disk_usage(str(BASE_DIR))
            free_gb = free / (1024 ** 3)
            if free_gb < self.min_disk_gb:
                return False, f"Insufficient disk space: {free_gb:.2f} GB free (Required: {self.min_disk_gb} GB)"
            return True, f"{free_gb:.2f} GB free disk space available"
        except Exception as e:
            return False, f"Could not check disk space: {e}"

    def check_fonts(self) -> Tuple[bool, str]:
        anton = FONTS_DIR / "Anton-Regular.ttf"
        montserrat = FONTS_DIR / "Montserrat-Variable.ttf"
        if not anton.exists():
            return False, f"Missing required font: {anton}"
        if not montserrat.exists():
            return False, f"Missing required font: {montserrat}"
        return True, "All required typography fonts verified on disk"

    def check_network(self) -> Tuple[bool, str]:
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3)
            return True, "Internet connectivity verified"
        except Exception:
            return False, "No active internet connection detected"

    def run_all(self) -> Dict[str, Any]:
        results = {
            "ffmpeg": self.check_ffmpeg(),
            "disk": self.check_disk_space(),
            "fonts": self.check_fonts(),
            "network": self.check_network(),
        }
        all_passed = all(status for status, _ in results.values())
        return {
            "all_passed": all_passed,
            "details": {k: {"passed": status, "msg": msg} for k, (status, msg) in results.items()}
        }


if __name__ == "__main__":
    checker = PreFlightHealthChecker()
    res = checker.run_all()
    print("Pre-Flight Health Check Results:")
    for k, v in res["details"].items():
        icon = "✓" if v["passed"] else "✗"
        print(f"  [{icon}] {k.upper()}: {v['msg']}")
    print(f"Overall Health: {'READY' if res['all_passed'] else 'ATTENTION REQUIRED'}")
