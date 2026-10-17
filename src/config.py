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
# Tried in order when CLAUDE_MODEL is overloaded/unavailable (same account credit).
CLAUDE_FALLBACK_MODELS = [m.strip() for m in os.getenv("CLAUDE_FALLBACK_MODELS", "claude-sonnet-5-5").split(",") if m.strip()]
# Optional monthly spend cap in USD (0 = no cap). When reached: use CLAUDE_BUDGET_MODEL, or stop if empty.
CLAUDE_MONTHLY_BUDGET_USD = float(os.getenv("CLAUDE_MONTHLY_BUDGET_USD", "0") or 0)
CLAUDE_BUDGET_MODEL = os.getenv("CLAUDE_BUDGET_MODEL", "").strip()

# Second, independent fact check (runs after the first passes). Claude models in order of
# ability; Gemini (free tier with a key from aistudio.google.com) is the last fallback.
SECOND_CHECK_ENABLED = os.getenv("SECOND_CHECK_ENABLED", "true").lower() in ("true", "1", "yes")
SECOND_CHECK_MODELS = [m.strip() for m in os.getenv("SECOND_CHECK_MODELS", "claude-fable-5-1,claude-opus-5-5").split(",")
                       if m.strip()]
# When the second check disputes a claim the first passed, this model re-checks once and its
# verdict stands, so the strongest reviewer decides contested facts ("" = no escalation).
SECOND_CHECK_ESCALATION_MODEL = os.getenv("SECOND_CHECK_ESCALATION_MODEL", "claude-fable-5-1").strip()
# Simple text tasks (picture search queries, music mood) where a top model adds nothing.
LIGHT_TASK_MODELS = [m.strip() for m in os.getenv("LIGHT_TASK_MODELS", "claude-sonnet-5-5,claude-opus-5-5").split(",")
                     if m.strip()]
# Music: "library" = approved free tracks first, ElevenLabs Music only if none fits;
# "elevenlabs" = generated score first (paid plan).
MUSIC_PREFER = os.getenv("MUSIC_PREFER", "library").strip().lower()
# Cap on pay-per-use media APIs (AI video), USD per month; 0 = paid media APIs disabled.
MEDIA_MONTHLY_BUDGET_USD = float(os.getenv("MEDIA_MONTHLY_BUDGET_USD", "0") or 0)
# AI motion (src/motion.py): Veo through the Gemini API (billing on), then Kling. Runs only when
# MEDIA_MONTHLY_BUDGET_USD > 0. Veo Fast is the default: Standard costs about 3x more per second.
MOTION_VEO_MODEL = os.getenv("MOTION_VEO_MODEL", "veo-3.1-fast-generate-preview")
MOTION_MAX_CLIPS = int(os.getenv("MOTION_MAX_CLIPS", "1"))  # the opening still; 2 = also one more
KLING_API_KEY = os.getenv("KLING_API_KEY", "")
# Script research, writing and fact checks: "free" = Gemini free tier on GEMINI_FREE_API_KEY (a key from a
# Google project WITHOUT billing, so it can never be charged) with Wikipedia + cited pages as evidence;
# "claude" = the paid Claude pipeline with web search. The free path never falls back to a paid key.
SCRIPT_ENGINE = os.getenv("SCRIPT_ENGINE", "free").strip().lower()
GEMINI_FREE_API_KEY = os.getenv("GEMINI_FREE_API_KEY", "")
FREE_WRITER_MODEL = os.getenv("FREE_WRITER_MODEL", "gemini-3.8-flash")
FREE_CHECKER_MODEL = os.getenv("FREE_CHECKER_MODEL", "gemini-3.7-flash")  # a different model checks the writer's work
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

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
# Publisher 3: Facebook Page Reels direct (fallback, Facebook only). Page token with
# pages_show_list, pages_read_engagement, pages_manage_posts; defaults to META_ACCESS_TOKEN.
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_PAGE_ACCESS_TOKEN = os.getenv("META_PAGE_ACCESS_TOKEN", "")
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

# Operations: daily "alive" message time, and an optional dead-man's-switch URL
# (e.g. a free https://healthchecks.io check) pinged on each heartbeat and finished run.
HEARTBEAT_TIME = os.getenv("HEARTBEAT_TIME", "08:00")
HEALTHCHECK_PING_URL = os.getenv("HEALTHCHECK_PING_URL", "")

# Publishing defaults
META_IS_AI_GENERATED = os.getenv("META_IS_AI_GENERATED", "true").lower() in ("true", "1", "yes")
DAILY_RUN_TIME = os.getenv("SCHEDULE_DAILY_RUN_TIME", "09:00")
TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")
# Posting slots (daemon only; CLI publishing is immediate). An approved Reel takes the next
# free day's slot; days alternate between the listed times. Stories follow the Reel; a
# carousel goes out on the listed weekdays at whichever Reel time that day's Reel isn't using.
# Slots are in the audience's timezone (US Eastern), independent of DEFAULT_TIMEZONE (yours).
POST_TIMEZONE = os.getenv("POST_TIMEZONE", "America/New_York")
POST_SCHEDULING = os.getenv("POST_SCHEDULING", "true").lower() in ("true", "1", "yes")
REEL_POST_TIMES = [t.strip() for t in os.getenv("REEL_POST_TIMES", "12:00,19:00").split(",") if t.strip()]
STORY_DELAY_MINUTES = int(os.getenv("STORY_DELAY_MINUTES", "45"))
CAROUSEL_DAYS = [d.strip().lower()[:3] for d in os.getenv("CAROUSEL_DAYS", "tue,wed,thu").split(",") if d.strip()]

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

TARGET_DURATION_MIN = 42.0
TARGET_DURATION_MAX = 48.0
HARD_DURATION_CEILING = 52.0
TARGET_WORD_COUNT_MIN = 105
TARGET_WORD_COUNT_MAX = 115
MIN_SCENE_IMAGES = 5
# Local AI upscaler for low-resolution stills (https://github.com/xinntao/Real-ESRGAN, portable Vulkan build).
REALESRGAN_BIN = os.getenv("REALESRGAN_BIN", str(BASE_DIR / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan.exe"))
# Per-beat images searched on Wikimedia Commons during a run. CC BY-SA images are excluded by
# default: share-alike asks that adaptations (the video) be released under the same licence.
BEAT_IMAGES_ENABLED = os.getenv("BEAT_IMAGES_ENABLED", "true").lower() in ("true", "1", "yes")
# Real public footage (src/footage.py). Share of runtime footage may take (Meta's originality
# rules: third-party clips support our narration, never replace it), and the quality bar.
FOOTAGE_ENABLED = os.getenv("FOOTAGE_ENABLED", "true").lower() in ("true", "1", "yes")
FOOTAGE_MAX_SHARE = float(os.getenv("FOOTAGE_MAX_SHARE", "0.4"))
FOOTAGE_MIN_CROP_WIDTH = int(os.getenv("FOOTAGE_MIN_CROP_WIDTH", "1080"))  # px after the 9:16 crop: 4K landscape or vertical
FOOTAGE_MAX_MB = int(os.getenv("FOOTAGE_MAX_MB", "600"))  # per original download (4K originals are large)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_FOOTAGE_ENABLED = os.getenv("YOUTUBE_FOOTAGE_ENABLED", "true").lower() in ("true", "1", "yes")
YOUTUBE_FALLBACK_TO_YTDLP = os.getenv("YOUTUBE_FALLBACK_TO_YTDLP", "true").lower() in ("true", "1", "yes")
BEAT_IMAGES_ALLOW_SHARE_ALIKE = os.getenv("BEAT_IMAGES_ALLOW_SHARE_ALIKE", "false").lower() in ("true", "1", "yes")
CALLOUTS_ENABLED = os.getenv("CALLOUTS_ENABLED", "false").lower() in ("true", "1", "yes")
FACE_PRIVACY_ENABLED = os.getenv("FACE_PRIVACY_ENABLED", "true").lower() in ("true", "1", "yes")
FACE_PRIVACY_SKIN_THRESHOLD = float(os.getenv("FACE_PRIVACY_SKIN_THRESHOLD", "0.35"))

# Narration & Voiceover (edge-tts and Azure Speech)
NARRATOR_VOICE = os.getenv("NARRATOR_VOICE", "en-US-BrianMultilingualNeural")
# Licensed free voice fallback: Azure Speech (F0 tier: 0.5M characters/month), same voice.
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "")
NARRATOR_RATE = os.getenv("NARRATOR_RATE", "-2%")
NARRATOR_PITCH = os.getenv("NARRATOR_PITCH", "-2Hz")

# Subtitle / Caption Typography (Anton Locked Standard)
FONT_NAME = "Anton"
FONT_SIZE = 72
FONT_SIZE_ASS = 128  # Compensates for libass PlayResX/PlayResY scale to deliver exact 72px rendered height
# Bottom 35% of the frame (672 px) is Meta's Reels safe zone for its own UI (username,
# caption, audio row); captions sit just above it, baseline at y = 1248.
CAPTION_MARGIN_V = 672
MIN_BURST_WORDS = 2
TARGET_BURST_WORDS = 3
MAX_BURST_WORDS = 5
MAX_BURST_CHARS = 28
CAPTION_SMALL_CAPS = True
CAPTION_SC_SCALE = 82  # % scale for small capitals (82% of font size)

# Caption Pill Background (Equal Margins & Subtle Border Radius)
CAPTION_PAD_X = 28
CAPTION_PAD_Y = 16
CAPTION_BORDER_RADIUS = 14

# Caption Colors (ASS BGR Hex)
COL_ACTIVE = r"&H00FFFFFF&"    # Clean White (spoken word)
COL_EMPHASIS = r"&H0000D7FF&"  # Warm Gold #FFD700, only for the script's 1-3 key words
EMPHASIS_POP = 100             # 100% scale (no animated size pop; eliminates line jitter & glitching)
COL_PAST = r"&H00FFFFFF&"      # Clean White
COL_FUTURE = r"&H00909090&"    # Soft Gray
COL_BACK = r"&H20101010&"      # Translucent Charcoal Pill (alpha 0x20 = ~87% opacity)

# Thumbnail Specifications (Variant B Locked Standard)
THUMB_OPACITY = 215            # ~85% opacity
THUMB_BLUR_RADIUS = 10         # Soft feathered halo
THUMB_EYEBROW_FONT_SIZE = 32
THUMB_TITLE_FONT_SIZE = 92
THUMB_TAGLINE_FONT_SIZE = 40
