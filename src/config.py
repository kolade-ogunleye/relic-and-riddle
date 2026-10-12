"""
src/config.py
=============
Centralized configuration for the Autonomous Meta Content Creation Pipeline.
Handles environment variables, directory paths, and locked video/thumbnail specs.
"""

import os
import sys
from pathlib import Path

# Force UTF-8 for console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TOPICS_DIR = DATA_DIR / "topics"
CURATED_SCRIPTS_DIR = DATA_DIR / "scripts"
SFX_DIR = DATA_DIR / "sfx"
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = BASE_DIR / "fonts"
OUTPUT_DIR = BASE_DIR / "output"

for p in [DATA_DIR, TOPICS_DIR, CURATED_SCRIPTS_DIR, SFX_DIR, ASSETS_DIR, FONTS_DIR, OUTPUT_DIR]:
    p.mkdir(parents=True, exist_ok=True)


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader. Real environment variables always win."""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k.startswith("export "):
                k = k[len("export "):].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            os.environ.setdefault(k, v)


_load_env_file(BASE_DIR / ".env")


def is_configured(value: str) -> bool:
    """True when a credential is present and is not an .env.example placeholder."""
    return bool(value) and not value.startswith("your_")


# Claude (script writing + search-grounded fact checking)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# Voice
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")

# Publisher 1: Upload-Post.com (https://docs.upload-post.com/api/upload-video/)
UPLOAD_POST_API_KEY = os.getenv("UPLOAD_POST_API_KEY", "")
UPLOAD_POST_USER = os.getenv("UPLOAD_POST_USER", "")
UPLOAD_POST_BASE_URL = os.getenv("UPLOAD_POST_BASE_URL", "https://api.upload-post.com/api")
UPLOAD_POST_FACEBOOK_PAGE_ID = os.getenv("UPLOAD_POST_FACEBOOK_PAGE_ID", "")

# Publisher 2: Direct Instagram Graph API (Reels, resumable upload)
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_IG_ACCOUNT_ID = os.getenv("META_IG_ACCOUNT_ID", "")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v26.0")

# Telegram HITL approval
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
# Comma-separated Telegram user IDs allowed to approve. Defaults to the chat ID,
# which is the approver's user ID when the bot talks to them in a private chat.
def _approver_ids(raw: str) -> set:
    """Numeric Telegram user IDs only; placeholders and junk are ignored."""
    return {s.strip() for s in raw.split(",") if s.strip().lstrip("-").isdigit()}


# The bot's own ID (token prefix) can never be an approver.
TELEGRAM_APPROVER_IDS = _approver_ids(os.getenv("TELEGRAM_APPROVER_IDS", TELEGRAM_CHAT_ID)) - {TELEGRAM_BOT_TOKEN.split(":")[0]}
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "3600"))

# Publishing defaults
META_IS_AI_GENERATED = os.getenv("META_IS_AI_GENERATED", "true").lower() in ("true", "1", "yes")
DAILY_RUN_TIME = os.getenv("SCHEDULE_DAILY_RUN_TIME", "09:00")
TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# ------------------------------------------------------------------------------
# LOCKED PRODUCTION SPECIFICATIONS
# ------------------------------------------------------------------------------
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_CRF = 18
VIDEO_PRESET = "slow"
AUDIO_SAMPLE_RATE = 48000
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK = -1.5

TARGET_DURATION_MIN = 38.0
TARGET_DURATION_MAX = 50.0
HARD_DURATION_CEILING = 58.0
TARGET_WORD_COUNT_MIN = 95
TARGET_WORD_COUNT_MAX = 115
MIN_SCENE_IMAGES = 5

# Narration & Voiceover (edge-tts)
NARRATOR_VOICE = "en-US-ChristopherNeural"
NARRATOR_RATE = "-2%"
NARRATOR_PITCH = "-2Hz"

# Subtitle / Caption Typography (Anton Locked Standard)
FONT_NAME = "Anton"
FONT_SIZE = 92
CAPTION_MARGIN_V = 540  # y = 1380px, strictly above native Meta UI overlays
MAX_BURST_WORDS = 2
MAX_BURST_CHARS = 13

# Caption Colors (ASS BGR Hex)
COL_ACTIVE = r"&H0000D7FF&"    # Warm Gold #FFD700
COL_PAST = r"&H00FFFFFF&"      # Clean White
COL_FUTURE = r"&H00909090&"    # Soft Gray
COL_BACK = r"&HC8101010&"      # Translucent Charcoal Pill

# Thumbnail Specifications (Variant B Locked Standard)
THUMB_OPACITY = 215            # ~85% opacity
THUMB_BLUR_RADIUS = 10         # Soft feathered halo
THUMB_EYEBROW_FONT_SIZE = 32
THUMB_TITLE_FONT_SIZE = 92
THUMB_TAGLINE_FONT_SIZE = 40
