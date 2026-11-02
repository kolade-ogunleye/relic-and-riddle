"""
src/config.py
=============
Centralized configuration for the Autonomous Meta Content Creation Pipeline.
Handles environment variables, directory paths, and locked video/thumbnail specs.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

# Force UTF-8 for console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Base Paths
# RELIC_APP_ROOT is set by the production launcher (src/service_runtime.py), not in .env: it decides
# where .env is read from.
BASE_DIR = Path(os.getenv("RELIC_APP_ROOT") or Path(__file__).resolve().parent.parent).resolve()
DATA_DIR = BASE_DIR / "data"
TOPICS_DIR = DATA_DIR / "topics"
CURATED_SCRIPTS_DIR = DATA_DIR / "scripts"
SFX_DIR = DATA_DIR / "sfx"
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = BASE_DIR / "fonts"
OUTPUT_DIR = BASE_DIR / "output"
ISSUES_DIR = BASE_DIR / "docs" / "issues"  # operator /issue reports and their evidence
LOG_DIR = BASE_DIR / "logs"  # rotating, secret-masked pipeline.log

for p in [DATA_DIR, TOPICS_DIR, CURATED_SCRIPTS_DIR, SFX_DIR, ASSETS_DIR, FONTS_DIR, OUTPUT_DIR,
          LOG_DIR, ISSUES_DIR]:
    p.mkdir(parents=True, exist_ok=True)


ENV_FILE_KEYS: set = set()  # keys this process took from .env (not set in the real environment)


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader. Real environment variables always win."""
    for k, v in read_env_file(path).items():
        if k not in os.environ:
            ENV_FILE_KEYS.add(k)
        os.environ.setdefault(k, v)


def worker_environment(path: Optional[Path] = None) -> Dict[str, str]:
    """The environment for a run's worker: this process's, with .env read again for the keys that came from it.
    The daemon reads .env once at start, and a worker inherited those values, so an operator's edit (prod,
    2026-10-28: IMAGES_ALLOW_SHARE_ALIKE=true) silently waited for a restart. Real environment variables still win."""
    env = dict(os.environ)
    current = read_env_file(path or BASE_DIR / ".env")
    for k in ENV_FILE_KEYS - set(current):
        env.pop(k, None)  # taken out of .env since
    for k, v in current.items():
        if k in ENV_FILE_KEYS or k not in env:
            env[k] = v
    return env


def read_env_file(path: Path) -> Dict[str, str]:
    """KEY=VALUE pairs of an .env file ({} when there is none)."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
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
            elif " #" in v:
                v = v.split(" #", 1)[0].strip()
            elif "\t#" in v:
                v = v.split("\t#", 1)[0].strip()
            values.setdefault(k, v)  # the first line wins, as at start-up
    return values


_load_env_file(BASE_DIR / ".env")


def is_configured(value: str) -> bool:
    """True when a credential is present and is not an .env.example placeholder."""
    return bool(value) and not value.startswith("your_")


# Claude (script writing + search-grounded fact checking)
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
CLAUDE_MODEL = (os.getenv("CLAUDE_MODEL") or "claude-opus-5-5").strip()
# Tried in order when CLAUDE_MODEL is overloaded/unavailable (same account credit).
CLAUDE_FALLBACK_MODELS = [m.strip() for m in (os.getenv("CLAUDE_FALLBACK_MODELS") or "claude-sonnet-5-5").split(",") if m.strip()]
# Optional monthly spend cap in USD (0 = no cap). When reached: use CLAUDE_BUDGET_MODEL, or stop if empty.
CLAUDE_MONTHLY_BUDGET_USD = float(os.getenv("CLAUDE_MONTHLY_BUDGET_USD") or 0)
# On the free engine, Claude stands in when both free and billed Gemini fail (off by default; needs
# ANTHROPIC_API_KEY and CLAUDE_MONTHLY_BUDGET_USD above zero, which caps it).
CLAUDE_FALLBACK = os.getenv("CLAUDE_FALLBACK", "false").strip().lower() in ("true", "1", "yes")
CLAUDE_BUDGET_MODEL = (os.getenv("CLAUDE_BUDGET_MODEL") or "").strip()

# Second, independent fact check (runs after the first passes). Claude models in order of
# ability; Gemini (free tier with a key from aistudio.google.com) is the last fallback.
SECOND_CHECK_ENABLED = (os.getenv("SECOND_CHECK_ENABLED") or "true").lower() in ("true", "1", "yes")
SECOND_CHECK_MODELS = [m.strip() for m in (os.getenv("SECOND_CHECK_MODELS") or "claude-fable-5-1,claude-opus-5-5").split(",")
                       if m.strip()]
# When the second check disputes a claim the first passed, this model re-checks once and its
# verdict stands, so the strongest reviewer decides contested facts ("" = no escalation).
SECOND_CHECK_ESCALATION_MODEL = (os.getenv("SECOND_CHECK_ESCALATION_MODEL") or "claude-fable-5-1").strip()
# Simple text tasks (picture search queries, music mood) where a top model adds nothing.
LIGHT_TASK_MODELS = [m.strip() for m in (os.getenv("LIGHT_TASK_MODELS") or "claude-sonnet-5-5,claude-opus-5-5").split(",")
                     if m.strip()]
# Music: "library" = approved free tracks first, ElevenLabs Music only if none fits;
# "elevenlabs" = generated score first (paid plan).
MUSIC_PREFER = (os.getenv("MUSIC_PREFER") or "library").strip().lower()
# Tracks one "Find new tracks" tap downloads per mood (CC0/CC BY from Openverse; each needs review).
MUSIC_FETCH_PER_MOOD = max(1, int(os.getenv("MUSIC_FETCH_PER_MOOD") or 3))
# The 16:9 documentary's YouTube visibility once published: public, unlisted or private.
YOUTUBE_PRIVACY = (os.getenv("YOUTUBE_PRIVACY") or "public").strip().lower()
if YOUTUBE_PRIVACY not in ("public", "unlisted", "private"):
    YOUTUBE_PRIVACY = "public"
# Which video formats run: "reel" (9:16), "documentary" (16:9, being rebuilt: docs/documentary-rebuild.md)
# or both. A format not listed is paused on every route (Telegram, command line, the documentary after
# a Reel); the daily scheduled run makes every listed format.
VIDEO_FORMATS = [f for f in (x.strip().lower() for x in (os.getenv("VIDEO_FORMATS") or "reel").split(","))
                 if f in ("reel", "documentary")] or ["reel"]


def documentary_enabled() -> bool:
    return "documentary" in VIDEO_FORMATS


def effective_format(requested: str) -> "str | None":
    """A requested run format ("reel", "deep-dive", "dual") narrowed to VIDEO_FORMATS; None if none is on."""
    want = {"reel": {"reel"}, "deep-dive": {"documentary"}, "dual": {"reel", "documentary"}}.get(requested, {"reel"})
    have = want & set(VIDEO_FORMATS)
    return "dual" if len(have) == 2 else "reel" if have == {"reel"} else "deep-dive" if have else None
# Approved Reels are also uploaded to YouTube as Shorts: a separate upload after Facebook and
# Instagram, so a YouTube problem never changes the Reel's own outcome.
YOUTUBE_SHORTS = os.getenv("YOUTUBE_SHORTS", "false").strip().lower() in ("true", "1", "yes")
YOUTUBE_SHORTS_PRIVACY = (os.getenv("YOUTUBE_SHORTS_PRIVACY") or YOUTUBE_PRIVACY).strip().lower()
if YOUTUBE_SHORTS_PRIVACY not in ("public", "unlisted", "private"):
    YOUTUBE_SHORTS_PRIVACY = YOUTUBE_PRIVACY
# Cap on pay-per-use media APIs (AI video), USD per month; 0 = paid media APIs disabled.
MEDIA_MONTHLY_BUDGET_USD = float(os.getenv("MEDIA_MONTHLY_BUDGET_USD") or 0)
# AI motion (src/motion.py): Veo through the Gemini API (billing on), then Kling. Runs only when
# MEDIA_MONTHLY_BUDGET_USD > 0. Veo Fast is the default: Standard costs about 3x more per second.
# Museum and archive open-access searches for a line Commons cannot show (2026-10-29); "false" turns them off.
MUSEUM_LINE_SEARCH = (os.getenv("MUSEUM_LINE_SEARCH") or "true").lower() in ("true", "1", "yes")
MOTION_MAX_CLIPS = int(os.getenv("MOTION_MAX_CLIPS") or 1)  # the opening still; 2 = also one more
KLING_API_KEY = (os.getenv("KLING_API_KEY") or "").strip()
# Script research, writing and fact checks: "free" = Gemini free tier on GEMINI_FREE_API_KEY (a key from a
# Google project WITHOUT billing, so it can never be charged) with Wikipedia + cited pages as evidence;
# "claude" = the paid Claude pipeline with web search. The free path never falls back to a paid key.
SCRIPT_ENGINE = (os.getenv("SCRIPT_ENGINE") or "free").strip().lower()
GEMINI_FREE_API_KEY = (os.getenv("GEMINI_FREE_API_KEY") or "").strip()
# A spare free key from another Google project. Google's API terms (developers.google.com/terms, 2d) say
# limits must not be circumvented, and free-tier limits are per project, so the spare is NOT used while
# GEMINI_SPARE_KEY_PERMITTED is false. Turn it on only if Google's terms come to allow it.
GEMINI_FREE_API_KEY_SPARE = (os.getenv("GEMINI_FREE_API_KEY_SPARE") or "").strip()
GEMINI_SPARE_KEY_PERMITTED = (os.getenv("GEMINI_SPARE_KEY_PERMITTED") or "false").strip().lower() in ("1", "true", "yes")
FREE_WRITER_MODEL = (os.getenv("FREE_WRITER_MODEL") or "gemini-3.8-flash").strip()
# Light text-only tasks (sound plan, /ask, issue sorting, rule drafting, callouts, search terms) on their
# own free Gemini model, which has its own free limits in the same project; empty = the writer/checker
# models. Set only after scripts/compare_light_engines.py --candidate gemini:<model> holds up.
FREE_LIGHT_MODEL = (os.getenv("FREE_LIGHT_MODEL") or "").strip()
# Heavy tasks (scripts, fact sheets, fact checks) on the free key first (true), or straight to the capped
# billed key (false). Development and production share one free key, so by production's daily run its
# quota is used up and every heavy call was refused first; false there skips that, and the free quota
# stays with development. Light tasks always try the free key.
FREE_TIER_HEAVY = os.getenv("FREE_TIER_HEAVY", "true").strip().lower() in ("true", "1", "yes")
# The picture plan (which photo goes with which sentence) is retried within the run before photos are
# matched by their captions instead: SCENE_PLAN_ATTEMPTS tries, the first wait SCENE_PLAN_RETRY_SECONDS,
# doubling each time (3 tries, 60 s: about 3 minutes of waiting at most).
SCENE_PLAN_ATTEMPTS = max(1, int(os.getenv("SCENE_PLAN_ATTEMPTS") or 3))
SCENE_PLAN_RETRY_SECONDS = max(0.0, float(os.getenv("SCENE_PLAN_RETRY_SECONDS") or 60))
# Second free model for light, text-only tasks (src/groq.py); off until measured against Gemini.
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODEL = (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip()
GROQ_LIGHT_FALLBACK = (os.getenv("GROQ_LIGHT_FALLBACK") or "false").strip().lower() in ("1", "true", "yes")
FREE_CHECKER_MODEL = (os.getenv("FREE_CHECKER_MODEL") or "gemini-3.7-flash").strip()  # a different model checks the writer's work
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
# When the free Gemini quota is used up, these task classes may use the billed GEMINI_API_KEY up to a
# monthly cap in USD (0 = never): light = photo review and sound plan; heavy = writing and fact checks.
PAID_FALLBACK_LIGHT_MONTHLY_USD = float(os.getenv("PAID_FALLBACK_LIGHT_MONTHLY_USD") or 0)
PAID_FALLBACK_HEAVY_MONTHLY_USD = float(os.getenv("PAID_FALLBACK_HEAVY_MONTHLY_USD") or 0)
SMITHSONIAN_API_KEY = (os.getenv("SMITHSONIAN_API_KEY") or "").strip()  # api.data.gov key (free); masked in logs
# Europeana (European museums, libraries and archives; free key from pro.europeana.eu). Unset: the source is off
# (the public demo key is for trying the API, not for a pipeline). Masked in logs.
EUROPEANA_API_KEY = (os.getenv("EUROPEANA_API_KEY") or "").strip()
# Archive film is often a whole reel (Prelinger's "Father Nile (Egypt)", 1931: 86 MB); only a 6 s segment is kept
ARCHIVE_FILM_MAX_MB = max(20, int(os.getenv("ARCHIVE_FILM_MAX_MB") or 250))
# Wikimedia's User-Agent policy: a contact (URL or email) for the bot, or requests may be throttled/blocked.
COMMONS_CONTACT = (os.getenv("COMMONS_CONTACT") or "").strip()
# Parallel ffmpeg encodes (1-8): more renders at once on a stronger GPU, fewer on a shared one.
try:
    RENDER_ENCODING_SLOTS = max(1, min(8, int(os.getenv("RENDER_ENCODING_SLOTS") or 2)))
except ValueError:
    RENDER_ENCODING_SLOTS = 2
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-3.8-flash").strip()

# Voice
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
# eleven_v4: ElevenLabs' recommended narration model (same credit rate as multilingual_v2). If it
# rejects the timestamped request, the call falls back to ELEVENLABS_FALLBACK_MODEL.
ELEVENLABS_MODEL = (os.getenv("ELEVENLABS_MODEL") or "eleven_v4").strip()
ELEVENLABS_FALLBACK_MODEL = (os.getenv("ELEVENLABS_FALLBACK_MODEL") or "eleven_multilingual_v2").strip()
ELEVENLABS_SPEED = float(os.getenv("ELEVENLABS_SPEED") or 1.0)
# A narration a little over the time limit is spoken up to this much faster instead of being rewritten
# (each rewrite is a full model call and can overshoot both ways). 1.08 = at most 8% faster.
SPEECH_SPEEDUP_MAX = max(1.0, min(1.15, float(os.getenv("SPEECH_SPEEDUP_MAX") or 1.08)))
# Dry runs voice with the free licensed voice (Azure F0) so tests don't spend ElevenLabs credits.
ELEVENLABS_IN_DRY_RUNS = os.getenv("ELEVENLABS_IN_DRY_RUNS", "false").lower() in ("true", "1", "yes")
# Dry runs skip the paid AI motion clip (Veo/Kling) unless this is on: on, a dry run looks like the live
# product (within MEDIA_MONTHLY_BUDGET_USD), so validation shows what will actually be posted.
MOTION_IN_DRY_RUNS = os.getenv("MOTION_IN_DRY_RUNS", "false").lower() in ("true", "1", "yes")
# A minor finding of the visual audit (a graphic a second late, say) blocks release by default, as every
# finding did. Off, a master whose only findings are minor passes the gate with them listed on the review
# card for the reviewer; major and critical findings always block (operator's choice, 2026-10-24). Off by
# default: minors are shown and the operator decides from their severity (operator, 2026-10-28).
MINOR_FINDINGS_BLOCK_RELEASE = os.getenv("MINOR_FINDINGS_BLOCK_RELEASE", "false").lower() in ("true", "1", "yes")
# Runs started from the bot (main.py bot: development) are dry runs unless this is false: a Render there
# started a live run, so Approve & Post would have posted to the connected accounts. Production runs the
# daemon, whose own --dry-run flag decides.
BOT_RUNS_DRY = os.getenv("BOT_RUNS_DRY", "true").strip().lower() in ("true", "1", "yes")

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
# The same Page Upload-Post posts to, so its id is the default: only the Page token is ours to add (2026-10-27)
META_PAGE_ID = os.getenv("META_PAGE_ID", "") or UPLOAD_POST_FACEBOOK_PAGE_ID
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

# Telegram Bot Chat Upgrade v2 (Topics & Two-Card System)
# Feature-flagged: disabled by default until operator acceptance.
RELIC_ENVIRONMENT = (os.getenv("RELIC_ENVIRONMENT") or "development").strip().lower()
TELEGRAM_CARDS_V2 = os.getenv("TELEGRAM_CARDS_V2", "false").lower() in ("true", "1", "yes")
RELIC_STRICT_TOPICS = os.getenv("RELIC_STRICT_TOPICS", "false").lower() in ("true", "1", "yes")

TELEGRAM_TOPICS = {
    "studio": int(os.getenv("TELEGRAM_TOPIC_STUDIO", "4906")),
    "needs": int(os.getenv("TELEGRAM_TOPIC_NEEDS", "4907")),
    "reviews": int(os.getenv("TELEGRAM_TOPIC_REVIEWS", "4908")),
    "runs": int(os.getenv("TELEGRAM_TOPIC_RUNS", "4909")),
    "info": int(os.getenv("TELEGRAM_TOPIC_INFO", "4910")),
}

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
HARD_DURATION_CEILING = 52.0  # longest voiceover allowed; a Reel master is the voiceover plus the end card (end_card.SECONDS)
RELEASE_DURATION_MAX = 58.0  # release-gate tolerance for the Reel master (re-measured at publish time)
# The longest RECORDED take kept (operator, 2026-10-28: "apply 54.5"). The script is still planned under
# HARD_DURATION_CEILING; a take that lands past it is kept up to this, so ElevenLabs' +/-7% take spread no longer
# fails a run after the voice is paid for (52.9, 53.7 and 54.4 s on production, 25 Oct). 54.5 s plus the 2.9 s
# end card is a 57.4 s master: inside RELEASE_DURATION_MAX and YouTube Shorts' 60 s.
VOICE_DURATION_CEILING = float(os.getenv("VOICE_DURATION_CEILING") or 54.5)
TARGET_WORD_COUNT_MIN = 105  # what the writer is asked for (about 45 s at a typical pace)
TARGET_WORD_COUNT_MAX = 115
# Sanity bounds only: the measured spoken length decides (production 2026-10-23: at the licensed voice's
# pace 105 words ran 52.9 s, so a fixed 105-115 window and the 51.5 s limit left no valid draft).
WORD_COUNT_FLOOR = 85
WORD_COUNT_CEILING = 130
MIN_SCENE_IMAGES = 5
# Local AI upscaler for low-resolution stills (https://github.com/xinntao/Real-ESRGAN, portable Vulkan build).
REALESRGAN_BIN = os.getenv("REALESRGAN_BIN", str(BASE_DIR / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan.exe"))
# Per-beat images searched on Wikimedia Commons during a run. CC BY-SA images are excluded by
# default: share-alike asks that adaptations (the video) be released under the same licence.
BEAT_IMAGES_ENABLED = os.getenv("BEAT_IMAGES_ENABLED", "true").lower() in ("true", "1", "yes")
# A found picture is looked at (a light vision call) before it is kept: the title and metadata checks let a
# photograph of the wrong temple through (quality step 1, 2026-10-24). At most one call per kept candidate.
PICTURE_VISION_CHECK = os.getenv("PICTURE_VISION_CHECK", "true").lower() in ("true", "1", "yes")
# A picture check that fails (model overloaded, rate-limited, down) is asked once more after this wait before the
# picture is left out (operator, 2026-10-27: the gate is enforced, but a good photo is not lost to a blip).
PICTURE_CHECK_RETRY_SECONDS = max(0.0, float(os.getenv("PICTURE_CHECK_RETRY_SECONDS") or 15))
# A look at each rendered Story frame and carousel slide (quality step 5, 2026-10-25): findings are shown on
# the review card and kept in the ledger; they do not block a release until the check is calibrated.
STILL_VISION_CHECK = os.getenv("STILL_VISION_CHECK", "true").lower() in ("true", "1", "yes")
# Operator-supplied footage (src/footage.py). Share of runtime footage may take (Meta's originality
# rules: third-party clips support our narration, never replace it), and the quality bar.
FOOTAGE_ENABLED = os.getenv("FOOTAGE_ENABLED", "true").lower() in ("true", "1", "yes")
FOOTAGE_MAX_SHARE = float(os.getenv("FOOTAGE_MAX_SHARE") or 0.4)
FOOTAGE_MAX_MB = int(os.getenv("FOOTAGE_MAX_MB") or 600)  # per download (AI motion results)
# One switch for every image path (fetch, upload, release). With it on, a video that uses a CC BY-SA
# image is itself released under CC BY-SA 4.0 (the release gate requires that notice in the post).
IMAGES_ALLOW_SHARE_ALIKE = (os.getenv("IMAGES_ALLOW_SHARE_ALIKE") or os.getenv("BEAT_IMAGES_ALLOW_SHARE_ALIKE") or "false").lower() in ("true", "1", "yes")
BEAT_IMAGES_ALLOW_SHARE_ALIKE = IMAGES_ALLOW_SHARE_ALIKE  # earlier name
CALLOUTS_ENABLED = (os.getenv("CALLOUTS_ENABLED") or "false").lower() in ("true", "1", "yes")
FACE_PRIVACY_ENABLED = (os.getenv("FACE_PRIVACY_ENABLED") or "true").lower() in ("true", "1", "yes")
FACE_PRIVACY_SKIN_THRESHOLD = float(os.getenv("FACE_PRIVACY_SKIN_THRESHOLD") or 0.35)

# Narration & Voiceover (edge-tts and Azure Speech)
VOICE_PREFER = (os.getenv("VOICE_PREFER") or "elevenlabs").strip().lower()  # "elevenlabs" or "azure"

# Generative Gap-Filling Subsystem (Phase 2.5, v1.0.0-rc78)
GENERATIVE_GAP_FILLING = os.getenv("GENERATIVE_GAP_FILLING", "true").lower() in ("true", "1", "yes")
MAX_GAP_IMAGES_PER_RUN = int(os.getenv("MAX_GAP_IMAGES_PER_RUN", "2"))
VOICE_REQUIRE_LICENSED = os.getenv("VOICE_REQUIRE_LICENSED", "true").lower() in ("true", "1", "yes")  # real runs need a commercial voice licence
NARRATOR_VOICE = (os.getenv("NARRATOR_VOICE") or "en-US-BrianMultilingualNeural").strip()
# Licensed free voice fallback: Azure Speech (F0 tier: 0.5M characters/month), same voice.
AZURE_SPEECH_KEY = (os.getenv("AZURE_SPEECH_KEY") or "").strip()
AZURE_SPEECH_REGION = (os.getenv("AZURE_SPEECH_REGION") or "eastus").strip()
NARRATOR_RATE = (os.getenv("NARRATOR_RATE") or "-2%").strip()
NARRATOR_PITCH = (os.getenv("NARRATOR_PITCH") or "-2Hz").strip()

# Subtitle / Caption Typography (Anton Locked Standard)
FONT_NAME = (os.getenv("FONT_NAME") or "Anton").strip()
FONT_SIZE = int(os.getenv("FONT_SIZE") or 72)
FONT_SIZE_ASS = 128  # Compensates for libass PlayResX/PlayResY scale to deliver exact 72px rendered height
# The bottom band the Reel platforms draw their own interface over (safe_zones.REEL: Meta's 35%, 672 px,
# the same figure on YouTube Shorts); the caption pill ends above it.
from .safe_zones import REEL as _REEL_UI  # noqa: E402
CAPTION_MARGIN_V = _REEL_UI.bottom
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
# The plate: the brand's INK (#0B0E12) at alpha 0x20 (~87% opacity), the ground every other surface uses; it was a
# neutral #101010 until the caption review of 2026-11-02 (round 2, finding 5). The 16:9 burn-in uses the same plate.
COL_BACK = r"&H20120E0B&"      # brand.ass_colour(brand.INK, 0x20); the round-2 tests hold the two equal

# Caption timing (caption review round 2, 2026-11-02, finding 4). A burst appears two frames before its first
# word is heard (reading starts a beat behind the eye); across a pause longer than CAPTION_PAUSE_GAP it leaves
# CAPTION_PAUSE_TAIL after its last word instead of holding through the silence; a burst on screen for less than
# CAPTION_MIN_CHUNK joins a neighbour (a flash is not readable).
CAPTION_LEAD_FRAMES = 2
CAPTION_PAUSE_GAP = 0.8
CAPTION_PAUSE_TAIL = 0.25
CAPTION_MIN_CHUNK = 0.6

# 16:9 burn-in (documentary; round 2, finding 6): Montserrat Bold (the bundled variable font's Bold instance; with
# Bold=0 libass loads its default, Thin), one line of at most 42 characters, 4-8 words a burst, the plate's
# lower edge CAPTION_WIDE_MARGIN_V above the frame's bottom (y 1000 at 1080). A sentence splits into bursts of at
# least CAPTION_WIDE_MIN_WORDS (3, so a 5-7 word sentence too long for one line can split without an orphan word);
# the phrase builder favours 24-42 characters, which is 4-8 words in practice.
CAPTION_WIDE_FONT = "Montserrat"
CAPTION_WIDE_SIZE = 52  # px rendered em, as FONT_SIZE; the ASS Fontsize comes from the font's metrics (81)
CAPTION_WIDE_MARGIN_V = 80
CAPTION_WIDE_MAX_CHARS = 42
CAPTION_WIDE_MIN_WORDS = 3
CAPTION_WIDE_TARGET_WORDS = 6
CAPTION_WIDE_MAX_WORDS = 8

# Closed-caption sidecars (.srt/.vtt; round 2, finding 7): their own cues, not the Reel's 2-5 word bursts.
SIDECAR_MAX_LINE_CHARS = 42
SIDECAR_MAX_LINES = 2
SIDECAR_MIN_CUE = 1.2
SIDECAR_MAX_CUE = 7.0

# The AI-reconstruction disclosure badge (round 2, finding 8): the data font, bone on an INK plate.
RECON_FONT = "IBM Plex Mono"
RECON_SIZE_PORTRAIT = 30  # the 9:16 size once tests/test_gap_engine.py lets the badge move (it locks \fs22)
RECON_SIZE_WIDE = 26
RECON_PLATE_ALPHA = 0x26  # ~85% opacity

# Thumbnail Specifications (Variant B Locked Standard)
THUMB_OPACITY = 215            # ~85% opacity
THUMB_BLUR_RADIUS = 10         # Soft feathered halo
THUMB_EYEBROW_FONT_SIZE = 32
THUMB_TITLE_FONT_SIZE = 92
THUMB_TAGLINE_FONT_SIZE = 40

# Production Archive & Tier 2 Backup
BACKUP_ENABLED = (os.getenv("BACKUP_ENABLED") or "true").lower() in ("true", "1", "yes")
_default_backup_dir = Path.home() / "OneDrive" / "Backups" / "Created Contents" / "RelicAndRiddle"
BACKUP_ONEDRIVE_DIR = Path(os.getenv("BACKUP_ONEDRIVE_DIR") or _default_backup_dir)
# Keep the current desktop-sync configuration until API access is explicitly provisioned.
# A sync-folder copy is staging evidence, never proof of a cloud upload.
BACKUP_TRANSPORT = (os.getenv("BACKUP_TRANSPORT") or "sync-folder").strip().lower()
BACKUP_SPOOL_DIR = Path(os.getenv("BACKUP_SPOOL_DIR") or BASE_DIR / "archive" / "backups")
BACKUP_ENVIRONMENT = (os.getenv("RELIC_ENVIRONMENT") or "development").strip().lower()
_backup_secret_dir = Path.home() / ".relic-and-riddle" / BACKUP_ENVIRONMENT / "backup"
BACKUP_KEY_FILE = Path(os.getenv("BACKUP_KEY_FILE") or _backup_secret_dir / "recovery.key")
ONEDRIVE_CLIENT_ID = (os.getenv("ONEDRIVE_CLIENT_ID") or "").strip()
ONEDRIVE_SCOPE = (os.getenv("ONEDRIVE_SCOPE") or "appfolder").strip().lower()
ONEDRIVE_BACKUP_PATH = (os.getenv("ONEDRIVE_BACKUP_PATH") or "").strip()
ONEDRIVE_TOKEN_CACHE = Path(os.getenv("ONEDRIVE_TOKEN_CACHE") or _backup_secret_dir / f"tokens-{ONEDRIVE_SCOPE}.enc")
BACKUP_SYNC_FOLDER_FALLBACK = (os.getenv("BACKUP_SYNC_FOLDER_FALLBACK") or "false").lower() in ("true", "1", "yes")
