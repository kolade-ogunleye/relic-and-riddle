"""
src/ops.py
==========
Operations & maintenance suite for the Autonomous Meta Content Pipeline.
1. Logging with secret masking applied to every record (tokens never reach logs).
2. Asset Lifecycle Manager: prunes intermediate scratch (> 7 days) while preserving masters,
   narration, beds, thumbnails, captions and metadata.
3. Pre-Flight Health Checker: FFmpeg/ffprobe, disk space, fonts, connectivity, credentials.
"""

import json
import logging
import re
import shutil
import socket
import subprocess
import time
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests

from . import config
from .config import BASE_DIR, OUTPUT_DIR, FONTS_DIR, is_configured

# Telegram bot tokens appear inside request URLs (".../bot<id>:<secret>/sendPhoto").
_TELEGRAM_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")
# Google API keys (Gemini) can appear in library-built URLs even though our own calls send a header.
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


class SecretMasker:
    """Sanitizes every configured credential out of strings."""

    SECRET_NAMES = (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "UPLOAD_POST_API_KEY",
        "META_ACCESS_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "AZURE_SPEECH_KEY",
        "GEMINI_API_KEY",
        "META_PAGE_ACCESS_TOKEN",
        "GEMINI_FREE_API_KEY",
        "KLING_API_KEY",
        "HEALTHCHECK_PING_URL",  # the ping URL itself is the credential
        "SMITHSONIAN_API_KEY",
        "GEMINI_FREE_API_KEY_SPARE",  # were missing: an error carrying either was shown unmasked
        "GROQ_API_KEY",
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
        sanitized = _GOOGLE_KEY_RE.sub("AIza***", sanitized)
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
    masking = _MaskingFilter(SecretMasker())
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    handler.addFilter(masking)
    root.addHandler(handler)
    # A persistent, rotating copy so a daemon or bot failure can be diagnosed after the console is gone.
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(config.LOG_DIR / "pipeline.log", maxBytes=5_000_000,
                                           backupCount=5, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        file_handler.addFilter(masking)
        root.addHandler(file_handler)
    except OSError as e:
        root.warning("File logging unavailable (%s); console only.", e)
    root.setLevel(level)
    # Library debug output can include full request URLs; keep it quiet.
    for noisy in ("urllib3", "httpx", "httpx2", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._pipeline_configured = True


log = logging.getLogger(__name__)


class AssetLifecycleManager:
    """Maintains storage hygiene by pruning intermediate scratch files older than retention_days."""

    # Narration and bed are kept: a run's release check and rights evidence need them (a run can
    # wait weeks for approval or a slot, and published runs may need them in a dispute).
    SCRATCH_PATTERNS = ("*temp*", "*.tmp")
    SCRATCH_DIRS = ("motion", "parallax", "qa_frames", "stage_cache", "audit")

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
            # Purge loose scratch patterns
            for pattern in self.SCRATCH_PATTERNS:
                for f in run_dir.glob(pattern):
                    try:
                        size = f.stat().st_size
                        f.unlink()
                        purged_files += 1
                        reclaimed_bytes += size
                    except OSError as e:
                        log.warning("Could not purge %s: %s", f, e)
            # Purge intermediate scratch directories (motion, parallax, qa_frames, etc.)
            for d_name in self.SCRATCH_DIRS:
                s_dir = run_dir / d_name
                if s_dir.is_dir():
                    try:
                        for sf in s_dir.rglob("*"):
                            if sf.is_file():
                                reclaimed_bytes += sf.stat().st_size
                                purged_files += 1
                        shutil.rmtree(s_dir)
                    except OSError as e:
                        log.warning("Could not remove scratch directory %s: %s", s_dir, e)

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
        if config.SCRIPT_ENGINE == "claude" and not is_configured(config.ANTHROPIC_API_KEY):
            missing.append("ANTHROPIC_API_KEY (script writing + fact checking)")
        if config.SCRIPT_ENGINE != "claude" and not is_configured(config.GEMINI_FREE_API_KEY):
            missing.append("GEMINI_FREE_API_KEY (free script writing + fact checking)")
        if not (is_configured(config.TELEGRAM_BOT_TOKEN) and is_configured(config.TELEGRAM_CHAT_ID)):
            missing.append("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (human approval)")
        elif config.TELEGRAM_CHAT_ID.strip() == config.TELEGRAM_BOT_TOKEN.split(":")[0]:
            missing.append("a correct TELEGRAM_CHAT_ID: it is set to the bot's own ID; use your personal "
                           "numeric user ID (from @userinfobot) and send /start to the bot")
        if not config.TELEGRAM_APPROVER_IDS:
            missing.append("TELEGRAM_APPROVER_IDS (at least one person's numeric Telegram user ID, not the bot's ID)")
        upload_post = is_configured(config.UPLOAD_POST_API_KEY) and is_configured(config.UPLOAD_POST_USER)
        graph = is_configured(config.META_ACCESS_TOKEN) and is_configured(config.META_IG_ACCOUNT_ID)
        if not (upload_post or graph):
            missing.append("a publisher (UPLOAD_POST_API_KEY+UPLOAD_POST_USER or META_ACCESS_TOKEN+META_IG_ACCOUNT_ID)")
        azure = is_configured(config.AZURE_SPEECH_KEY) and is_configured(config.AZURE_SPEECH_REGION)
        if not azure:
            from .audio_engine import AudioEngine  # only when needed: asks ElevenLabs for the plan
            if not (is_configured(config.ELEVENLABS_API_KEY) and AudioEngine().eleven_licensed()):
                missing.append("a commercially licensed voice (ElevenLabs on a paid plan, or AZURE_SPEECH_KEY+"
                               "AZURE_SPEECH_REGION); ElevenLabs Free and edge-tts are for test runs only")
        if missing:
            return False, "Missing: " + "; ".join(missing)
        return True, "Script, approval and publishing credentials configured"

    NARRATION_CHARS = 900  # a 115-word script with its punctuation, rounded up

    def probe_live_services(self) -> Tuple[bool, str]:
        """Light real calls proving the credentials work (presence alone said "OK" while Azure
        returned 401). Nothing is generated or billed."""
        problems, notes = [], []
        azure_ok = False
        if is_configured(config.AZURE_SPEECH_KEY) and is_configured(config.AZURE_SPEECH_REGION):
            try:
                r = requests.post(f"https://{config.AZURE_SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken",
                                  headers={"Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY}, timeout=15)
                azure_ok = r.status_code == 200
                (notes if azure_ok else problems).append(
                    "Azure Speech key OK" if azure_ok else f"Azure Speech rejected the key/region (HTTP {r.status_code})")
            except requests.RequestException as e:
                problems.append(f"Azure Speech unreachable ({type(e).__name__})")
        eleven_ok = False
        if is_configured(config.ELEVENLABS_API_KEY):
            from .audio_engine import AudioEngine
            quota = AudioEngine().voice_quota() or {}
            left = quota.get("limit", 0) - quota.get("used", 0)
            paid = bool(quota.get("tier")) and quota["tier"] != "free"
            eleven_ok = paid and left >= self.NARRATION_CHARS
            if eleven_ok:
                notes.append(f"ElevenLabs {quota['tier']} plan OK ({left} characters left)")
            else:
                problems.append(f"ElevenLabs is not usable for a licensed voice ({quota.get('tier') or 'unknown'} plan, "
                                f"{left} characters left)")
        if not (azure_ok or eleven_ok):
            problems.insert(0, "No working commercially licensed voice")
        if is_configured(config.GEMINI_API_KEY) and config.GEMINI_API_KEY == config.GEMINI_FREE_API_KEY:
            problems.append("GEMINI_API_KEY and GEMINI_FREE_API_KEY are the same key: the free path could be billed, "
                            "or paid features run on free-tier limits")
        for name in ("GEMINI_FREE_API_KEY", "GEMINI_API_KEY"):
            key = getattr(config, name)
            if not is_configured(key):
                continue
            try:
                r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1},
                                 headers={"x-goog-api-key": key}, timeout=15)
                if r.status_code != 200:
                    problems.append(f"{name} rejected (HTTP {r.status_code})")
                else:
                    notes.append(f"{name} OK")
            except requests.RequestException as e:
                problems.append(f"{name} unreachable ({type(e).__name__})")
        if problems:
            return False, "; ".join(problems)
        return True, "; ".join(notes) or "Live services answered"

    def run_all(self, require_live: bool = False) -> Dict[str, Any]:
        results = {
            "ffmpeg": self.check_ffmpeg(),
            "ffprobe": self.check_ffprobe(),
            "disk": self.check_disk_space(),
            "fonts": self.check_fonts(),
            "network": self.check_network(),
            "credentials": self.check_live_credentials(),
        }
        if config.BACKUP_ENABLED and config.BACKUP_TRANSPORT == "onedrive-api":
            from .backup_runtime import check_readiness
            results["backup"] = check_readiness() if require_live else (
                True, "API backup configured; use health --live to verify cloud and restore evidence")
        if require_live:  # prove the credentials work before a live run spends anything
            results["services"] = self.probe_live_services()
        required = [k for k in results if k != "credentials" or require_live]
        return {
            "all_passed": all(results[k][0] for k in required),
            "details": {
                k: {"passed": ok, "msg": msg, "required": k in required}
                for k, (ok, msg) in results.items()
            },
        }


# ------------------------------------------------------------------ per-run evidence
# A run's own log at DEBUG (output/<run>/run.log) and a record of every model call it made
# (output/<run>/llm_calls.jsonl: task, model, seconds, tokens, outcome). pipeline.log keeps INFO for every
# process at once; following one run from start to end through it meant guessing where the minutes went
# (2026-10-24: the script stage's rounds could be timed only from gaps between unrelated lines).
_run_dir_for_evidence: Optional[Path] = None
NOISY_AT_DEBUG = ("PIL", "matplotlib", "numba", "onnxruntime", "charset_normalizer", "asyncio", "fontTools", "h5py",
                  "urllib3", "httpx", "httpcore", "requests", "apscheduler", "telegram", "edge_tts", "google")


class RunLog:
    """Attaches a masked DEBUG file handler for one run's folder; the other handlers keep their level."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.handler: Optional[logging.Handler] = None
        self._restore: list = []

    def start(self) -> "RunLog":
        global _run_dir_for_evidence
        root = logging.getLogger()
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(self.run_dir / "run.log", encoding="utf-8")
        except OSError as e:
            log.debug("Run log unavailable (%s).", e)
            return self
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        handler.addFilter(_MaskingFilter(SecretMasker()))
        for h in root.handlers:  # they go on showing INFO and up, whatever the root level becomes
            self._restore.append((h, h.level))
            if h.level < logging.INFO:
                h.setLevel(logging.INFO)
        self._restore.append((root, root.level))
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        for name in NOISY_AT_DEBUG:  # a library's tag dumps are not the run's story (PIL filled the first run.log)
            noisy = logging.getLogger(name)
            self._restore.append((noisy, noisy.level))
            if noisy.level < logging.INFO:
                noisy.setLevel(logging.INFO)
        root.addHandler(handler)
        self.handler = handler
        _run_dir_for_evidence = self.run_dir
        return self

    def stop(self) -> None:
        global _run_dir_for_evidence
        if self.handler is None:
            return
        root = logging.getLogger()
        root.removeHandler(self.handler)
        self.handler.close()
        for target, level in self._restore:
            target.setLevel(level)
        self.handler, self._restore = None, []
        if _run_dir_for_evidence == self.run_dir:
            _run_dir_for_evidence = None


def record_llm_call(task: str, model: str, seconds: float, tokens_in: int = 0, tokens_out: int = 0,
                    outcome: str = "ok", role: str = "") -> None:
    """One line per model call in the current run's llm_calls.jsonl. Never raises."""
    run_dir = _run_dir_for_evidence
    if run_dir is None:
        return
    try:
        line = {"at": datetime.now(timezone.utc).isoformat(), "task": task, "role": role, "model": model,
                "seconds": round(seconds, 2), "tokens_in": tokens_in, "tokens_out": tokens_out, "outcome": outcome}
        with (run_dir / "llm_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.debug("Model call not recorded: %s", e)
