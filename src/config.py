"""
src/config.py
=============
Centralized configuration manager for the Autonomous Meta Content Creation Pipeline.
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
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
TOPICS_DIR = DATA_DIR / "topics"
SFX_DIR = DATA_DIR / "sfx"
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = BASE_DIR / "fonts"
OUTPUT_DIR = BASE_DIR / "output"

# Ensure all critical runtime directories exist
for p in [CONFIG_DIR, DATA_DIR, TOPICS_DIR, SFX_DIR, ASSETS_DIR, FONTS_DIR, OUTPUT_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# Load .env manually if python-dotenv is present or parse simple KEY=VALUE
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Tier 1 Publisher: Upload-Post.com
UPLOAD_POST_API_KEY = os.getenv("UPLOAD_POST_API_KEY", "")
UPLOAD_POST_BASE_URL = os.getenv("UPLOAD_POST_BASE_URL", "https://api.upload-post.com/api/upload")

# Tier 2 Failover: Postiz (Self-Hosted Open-Source)
POSTIZ_API_KEY = os.getenv("POSTIZ_API_KEY", "")
POSTIZ_BASE_URL = os.getenv("POSTIZ_BASE_URL", "http://localhost:5000/api/v1")

# Tier 3 Failover: Direct Meta Graph API
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_IG_ACCOUNT_ID = os.getenv("META_IG_ACCOUNT_ID", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
KLING_API_KEY = os.getenv("KLING_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # Default Adam / Documentary deep baritone

# Publishing Defaults
META_IS_AI_GENERATED = os.getenv("META_IS_AI_GENERATED", "true").lower() in ("true", "1", "yes")
DAILY_RUN_TIME = os.getenv("SCHEDULE_DAILY_RUN_TIME", "09:00")
TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# ------------------------------------------------------------------------------
# LOCKED PRODUCTION SPECIFICATIONS
# ------------------------------------------------------------------------------
# Video Dimensions & Encoding
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_CRF = 18
VIDEO_PRESET = "slow"

# Duration Target & Limits
TARGET_DURATION_MIN = 38.0
TARGET_DURATION_MAX = 50.0
HARD_DURATION_CEILING = 58.0
TARGET_WORD_COUNT_MIN = 95
TARGET_WORD_COUNT_MAX = 115

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
