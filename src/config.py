"""
src/config.py
=============
Centralized configuration for the Autonomous Meta Content Creation Pipeline.
Handles environment variables, directory paths, and locked video/thumbnail specs.
"""

import os
import re
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
# QF-15: the offline English tagger for the payoff rule, shipped with the code (not the app root's data), hash-verified
PAYOFF_NLP_DIR = Path(__file__).resolve().parent.parent / "data/nlp/en_core_web_sm"
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
# Instagram, so a YouTube problem never changes the Reel's own outcome. On unless set false (QF-83: the one
# platform with Shorts revenue; an explicit YOUTUBE_SHORTS=false still turns it off).
YOUTUBE_SHORTS = os.getenv("YOUTUBE_SHORTS", "true").strip().lower() in ("true", "1", "yes")
# The Reel's loop bridge carries a one-word FOLLOW (src/end_card.py LOOP_FOLLOW; QF-83, operator 2026-11-10: on in
# production). It adds no time or narration; false gives the purely visual bridge.
LOOP_FOLLOW_OVERLAY = os.getenv("LOOP_FOLLOW_OVERLAY", "true").strip().lower() in ("true", "1", "yes")
YOUTUBE_SHORTS_PRIVACY = (os.getenv("YOUTUBE_SHORTS_PRIVACY") or YOUTUBE_PRIVACY).strip().lower()
if YOUTUBE_SHORTS_PRIVACY not in ("public", "unlisted", "private"):
    YOUTUBE_SHORTS_PRIVACY = YOUTUBE_PRIVACY
# YouTube's audience-retention curve per Short (src/youtube_analytics.py, read-only consent from the channel
# owner via scripts/youtube_analytics_auth.py). All three empty = YouTube keeps its counts only. The secret
# and refresh token are masked in every log line (src/ops.SecretMasker).
YOUTUBE_ANALYTICS_CLIENT_ID = (os.getenv("YOUTUBE_ANALYTICS_CLIENT_ID") or "").strip()
YOUTUBE_ANALYTICS_CLIENT_SECRET = (os.getenv("YOUTUBE_ANALYTICS_CLIENT_SECRET") or "").strip()
YOUTUBE_ANALYTICS_REFRESH_TOKEN = (os.getenv("YOUTUBE_ANALYTICS_REFRESH_TOKEN") or "").strip()
# Cap on pay-per-use media APIs (AI motion clips and AI illustrations together), USD per month;
# 0 = paid media APIs disabled (no AI motion and no illustration request).
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
# A carousel drawn at its slot or for its preview (QF-81) tries the picture plan CAROUSEL_PLAN_ATTEMPTS times, the
# first wait CAROUSEL_PLAN_RETRY_SECONDS (doubling): a plan that still fails holds the carousel promptly (its photos
# are never rotated), and the hold alert's Redraw tries again for the next slot (2 tries, 15 s).
CAROUSEL_PLAN_ATTEMPTS = max(1, int(os.getenv("CAROUSEL_PLAN_ATTEMPTS") or 2))
CAROUSEL_PLAN_RETRY_SECONDS = max(0.0, float(os.getenv("CAROUSEL_PLAN_RETRY_SECONDS") or 15))
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
# The brand's public contact (a URL or email) named in the User-Agent of other open-archive clients (archive.org).
# Unset: the User-Agent names the brand and purpose only. Kept out of the source (QF-49).
BRAND_CONTACT = (os.getenv("BRAND_CONTACT") or "").strip()
# Largest picture download read (MB) before it is refused undecoded; originals over 35 MB are fetched as thumbnails
try:
    COMMONS_DOWNLOAD_MAX_MB = max(1, int(os.getenv("COMMONS_DOWNLOAD_MAX_MB") or 50))
except ValueError:
    COMMONS_DOWNLOAD_MAX_MB = 50
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
# QF-08: the voice is requested lossless, so it is encoded once (AAC in the master), not MP3 first. pcm_48000 is 48 kHz
# 16-bit PCM at the master's own rate (no resample). A plan that does not offer it: pcm_44100, pcm_24000 (PCM at that
# rate, resampled once in the mix) or, last, mp3_44100_192 (decoded to WAV on arrival). Anything else -> pcm_48000.
_ELEVEN_FORMAT = (os.getenv("ELEVENLABS_OUTPUT_FORMAT") or "pcm_48000").strip().lower()
ELEVENLABS_OUTPUT_FORMAT = _ELEVEN_FORMAT if re.fullmatch(r"pcm_\d+|mp3_\d+_\d+", _ELEVEN_FORMAT) else "pcm_48000"
# Creator's default preserves the former 44.1 kHz bandwidth at a higher MP3 bitrate,
# decoded once to WAV. pcm_24000 remains an explicit choice with a 12 kHz ceiling.
_ELEVEN_FALLBACK_FORMAT = (os.getenv("ELEVENLABS_FALLBACK_FORMAT") or "mp3_44100_192").strip().lower()
ELEVENLABS_FALLBACK_FORMAT = (_ELEVEN_FALLBACK_FORMAT if re.fullmatch(r"pcm_\d+|mp3_\d+_\d+", _ELEVEN_FALLBACK_FORMAT)
                              else "mp3_44100_192")
# Free/Starter cannot request 192 kbps MP3. Keep their former format by default;
# an operator may explicitly choose lower-rate PCM instead.
_ELEVEN_BASIC_FORMAT = (os.getenv("ELEVENLABS_BASIC_FALLBACK_FORMAT") or "mp3_44100_128").strip().lower()
ELEVENLABS_BASIC_FALLBACK_FORMAT = (_ELEVEN_BASIC_FORMAT if re.fullmatch(r"pcm_\d+|mp3_\d+_\d+", _ELEVEN_BASIC_FORMAT)
                                    else "mp3_44100_128")
# Plans asked for ELEVENLABS_FALLBACK_FORMAT directly. Free/Starter use the basic
# setting; other/unknown plans try preferred, Creator fallback, then basic fallback.
ELEVENLABS_FALLBACK_FORMAT_TIERS = frozenset(
    t.strip().lower() for t in (os.getenv("ELEVENLABS_FALLBACK_FORMAT_TIERS") or "creator").split(",")
    if t.strip())
# The take's seed (0-4294967295): the same text and settings read the same way again (ElevenLabs: best effort).
try:
    ELEVENLABS_SEED = max(0, min(4294967295, int(os.getenv("ELEVENLABS_SEED") or 1)))
except ValueError:
    ELEVENLABS_SEED = 1
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


def _positive_minutes(name: str, default: str, environment: dict[str, str] | None = None) -> float:
    value = float((os.environ if environment is None else environment).get(name) or default)
    if not 0 < value < float("inf"):  # NaN and infinity fail this too
        raise ValueError(f"{name} must be a positive number of minutes, not {value}")
    return value


# Run deadline (QF-74). A run's production (everything up to its review card, then again after each decision) gets
# RUN_DEADLINE_MINUTES; at the next stage boundary past it the run stops as timed_out, keeps its files for a
# re-render and its topic goes back in the queue. Waiting for your review never counts. Measured
# (docs/reviews/quality-fixes-2026-11/QF-74): the longest of 35 development productions took 75 minutes; of 37
# production-sample attempts one ran past 150 minutes and finished normally (three master renders), none past 180,
# so 180 stops none of them. A stage that never reaches a boundary is stopped RUN_DEADLINE_GRACE_MINUTES later by
# the supervisor: 60 covers the longest single step measured (57 minutes in development; none past 60 in the
# sample), so a working stage always ends at its own boundary first.
RUN_DEADLINE_MINUTES = _positive_minutes("RUN_DEADLINE_MINUTES", "180")
RUN_DEADLINE_GRACE_MINUTES = _positive_minutes("RUN_DEADLINE_GRACE_MINUTES", "60")


def run_deadline_limits(environment: dict[str, str] | None = None) -> tuple[float, float]:
    """(deadline, hard stop) in seconds of production for a worker started with `environment` (a bot-started worker
    reads .env again: worker_environment), or with this process's settings."""
    deadline = _positive_minutes("RUN_DEADLINE_MINUTES", str(RUN_DEADLINE_MINUTES), environment)
    grace = _positive_minutes("RUN_DEADLINE_GRACE_MINUTES", str(RUN_DEADLINE_GRACE_MINUTES), environment)
    return deadline * 60, (deadline + grace) * 60


WORKER_POLL_SECONDS = 30  # how often the supervisor looks at its worker's runs
# Set by the supervisor in its worker's environment (never in .env): which runs the worker it watches has claimed.
WORKER_ATTEMPT_ID = os.getenv("RELIC_WORKER_ATTEMPT", "")
# Per-stage budgets in minutes (script, voice, pictures, render, QA, upload); a run's total for a stage, retries
# included, above warning_multiplier times its budget logs a WARNING and files one finding.
STAGE_BUDGET_FILE = BASE_DIR / "data" / "stage_budget.json"

# Telegram Bot Chat Upgrade v2 (Topics & Two-Card System)
# Feature-flagged: disabled by default until operator acceptance.
RELIC_ENVIRONMENT = (os.getenv("RELIC_ENVIRONMENT") or "development").strip().lower()
TELEGRAM_CARDS_V2 = os.getenv("TELEGRAM_CARDS_V2", "false").lower() in ("true", "1", "yes")
RELIC_STRICT_TOPICS = os.getenv("RELIC_STRICT_TOPICS", "false").lower() in ("true", "1", "yes")
# How long a decision taken on a V2 review card can be undone: the worker acts on it only once this has passed, so the
# card's Undo works for the whole window (UI-02). A booked slot loses nothing; a post-now posts this much later.
# At most 20 minutes, the approval window's margin before the Reel slot (main._approval_window): the longer the Undo,
# the further past its slot a last-minute approval is acted on (UI-02 review 3). Higher values are read as 20.
REVIEW_UNDO_MINUTES_MAX = 20.0


def review_undo_minutes(raw: str) -> float:
    """REVIEW_UNDO_MINUTES as set, held between 0 (no Undo) and REVIEW_UNDO_MINUTES_MAX."""
    return min(REVIEW_UNDO_MINUTES_MAX, max(0.0, float(raw)))


REVIEW_UNDO_MINUTES = review_undo_minutes(os.getenv("REVIEW_UNDO_MINUTES", "10"))

# The clean chat (UI-01, operator-approved design of 2026-11-12): one pinned notifications panel that every sender
# writes into, the dashboard drawn in place, and temporary panels that leave. Off restores the earlier chat (each
# notice a message of its own): the rollback during the on-device acceptance. Off by default until the whole chain
# (through UI-01f's 40 h re-post) has passed that acceptance: a release carrying only part of it must not switch an
# environment whose .env does not ask for it to a half-built clean chat. The development .env turns it on.
TELEGRAM_CLEAN_CHAT = os.getenv("TELEGRAM_CLEAN_CHAT", "false").strip().lower() in ("true", "1", "yes")
# Minutes before a booked Reel's slot at which the clean chat's "✅ Approved" item loses its Undo (UI-01e, the
# operator's 29 Oct ruling: Undo until 15 minutes before the first post). Until then Undo cancels the booking.
REVIEW_APPROVE_UNDO_LEAD_MINUTES = max(0.0, float(os.getenv("REVIEW_APPROVE_UNDO_LEAD_MINUTES", "15")))
# Seconds a push alert (the one line that rings for a Needs-you item) stays before it is deleted. The design's 8 s
# is to be proved on the phones (design Q1): raise it if the lock-screen banner leaves with the message.
PUSH_ALERT_SECONDS = max(1.0, float(os.getenv("PUSH_ALERT_SECONDS", "8")))

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
# A carousel goes out CAROUSEL_GAP_HOURS after its Reel at the soonest (Upload-Post flags similar content within
# 48 h, docs/sources.md: don't go lower) and expires CAROUSEL_MAX_AGE_DAYS after its Reel posts (or, while the Reel
# hasn't posted, after the Reel's slot), but never before its first carousel slot CAROUSEL_GAP_HOURS after the Reel:
# every carousel gets its slot (operator, 2026-11-03).
CAROUSEL_GAP_HOURS = float(os.getenv("CAROUSEL_GAP_HOURS", "48"))
CAROUSEL_MAX_AGE_DAYS = float(os.getenv("CAROUSEL_MAX_AGE_DAYS", "7"))
# Prepared-image cache entries (cache/upscaled, privacy, text_crop, colour_balance) that no run folder's provenance or
# lineage names are freed by `python -m src.retention --apply --caches` once unchanged this many days (QF-110/QF-100).
CACHE_RETENTION_DAYS = float(os.getenv("CACHE_RETENTION_DAYS", "30"))

# ------------------------------------------------------------------------------
# LOCKED PRODUCTION SPECIFICATIONS
# ------------------------------------------------------------------------------
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
ONNX_DML_DEVICE = os.getenv("ONNX_DML_DEVICE", "auto").strip().lower()
VIDEO_CRF = 18
VIDEO_PRESET = "slow"
AUDIO_SAMPLE_RATE = 48000
TARGET_LUFS = -14.0
TARGET_TRUE_PEAK = -1.5
# Objective picture checks of the delivered master in the release gate (QF-10, src/picture_qa.py). Fixed quality
# thresholds, not settings. Black: >= 98% of the picture at blackdetect's near-black for 0.1 s (three frames at
# 30 fps). Freeze: the same picture held longer than the format's longest permitted hold (a Reel's six-second photo
# hold, a deep dive's twelve-second cut) plus a frame. Bars: flat black bands, each >= 2% of the side, at both edges
# of an axis for 1 s, flat meaning no sample above luma 16 + PICTURE_QA_BAR_LEVEL and at least
# PICTURE_QA_BAR_FLAT_SHARE of a line's samples exactly 16 (padding). Range: more than 1% of a plane in
# areas (3 x 3 samples) beyond limited range by over two codes, for 0.5 s; isolated codec ringing does not count. The
# decode may take PICTURE_QA_TIMEOUT_BASE_S plus PICTURE_QA_TIMEOUT_PER_S per programme second; PICTURE_QA_THREADS 0
# lets FFmpeg use every core.
PICTURE_QA_BLACK_S = 0.10
PICTURE_QA_FREEZE_REEL_S = 6.1
PICTURE_QA_FREEZE_LONG_S = 12.1
PICTURE_QA_BAR_FRACTION = 0.02
PICTURE_QA_BAR_LEVEL = 2
PICTURE_QA_BAR_FLAT_SHARE = 0.9
PICTURE_QA_BAR_S = 1.0
PICTURE_QA_RANGE_FRACTION = 0.01
PICTURE_QA_RANGE_TOLERANCE = 2
PICTURE_QA_RANGE_S = 0.5
PICTURE_QA_TIMEOUT_BASE_S = 120.0
PICTURE_QA_TIMEOUT_PER_S = 5.0
PICTURE_QA_THREADS = 0
# Objective sound checks of the delivered master in the release gate (QF-11, src/audio_qa.py). Fixed quality
# thresholds, not settings; one decode measures them (ITU-R BS.1770 / EBU Tech 3341 meter). Dropout (blocks): the
# sound below AUDIO_QA_SILENCE_DB dBFS for AUDIO_QA_SILENCE_S or more anywhere more than AUDIO_QA_EDGE_S inside the
# programme, or for AUDIO_QA_EDGE_SILENCE_S or more at either end. Phone (a note, never blocking): a mobile-speaker
# simulation, AUDIO_QA_PHONE_LOW_HZ to AUDIO_QA_PHONE_HIGH_HZ at 24 dB per octave; a 3 s window above the relative
# gate whose loudness through it is more than AUDIO_QA_PHONE_LOSS_LU below the full band is noted. Sibilance (the
# AUDIO_QA_SIBILANCE band) is recorded only. The decode may take AUDIO_QA_TIMEOUT_BASE_S plus AUDIO_QA_TIMEOUT_PER_S
# per programme second (QF-10's picture limit).
AUDIO_QA_SILENCE_DB = -60
AUDIO_QA_SILENCE_S = 0.2
AUDIO_QA_EDGE_S = 0.5
AUDIO_QA_EDGE_SILENCE_S = 1.0
AUDIO_QA_PHONE_LOW_HZ = 300
AUDIO_QA_PHONE_HIGH_HZ = 8000
AUDIO_QA_PHONE_LOSS_LU = 10.0
AUDIO_QA_SIBILANCE_LOW_HZ = 4000
AUDIO_QA_SIBILANCE_HIGH_HZ = 10000
AUDIO_QA_TIMEOUT_BASE_S = 120.0
AUDIO_QA_TIMEOUT_PER_S = 5.0
# Sound-effect headroom (QF-06, 2026-11-05): every one-shot effect enters the music bed at SFX_PEAK_DBFS peak before
# its cue gain (it was set to the RMS of the synthesized stand-in it replaces, so its peak went wherever the
# recording's crest factor took it), and the bed bus (music, setting layer and effects together) is peak-limited at
# SFX_BUS_CEILING_DBFS before it is mixed with the narration, so no effect peak reaches the narration through the
# master limiter.
SFX_PEAK_DBFS = float(os.getenv("SFX_PEAK_DBFS") or -17.0)
SFX_BUS_CEILING_DBFS = float(os.getenv("SFX_BUS_CEILING_DBFS") or -6.0)

TARGET_DURATION_MIN = 42.0
TARGET_DURATION_MAX = 48.0
HARD_DURATION_CEILING = 52.0  # longest voiceover allowed; a Reel master is the voiceover plus the loop bridge (end_card.LOOP_SECONDS)
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
# The hook (QF-14, fixed rules, not settings): the narration's first sentence, which is the first caption burst's
# sentence, is at most HOOK_MAX_WORDS spoken words, and a number, scale, contrast or question arrives within its first
# HOOK_SIGNAL_WITHIN. Both get the one targeted opening edit; the cap then blocks, a missing signal ships flagged on
# the review card (operator ruling 2026-11-10; script_engine.hook_problems).
HOOK_MAX_WORDS = 12
HOOK_SIGNAL_WITHIN = 8
MIN_SCENE_IMAGES = 9
PHOTO_POOL_REFRESH_DAYS = 90
PHOTO_POOL_REFRESH_BELOW = 12
PHOTO_POOL_FETCH_PASSES = 3
REEL_PHOTO_HOLD_MAX = 6.0
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
# Face verification runs on this machine by default: YuNet proposes faces, face geometry trims them, and
# YuNet's score, the face's size in the photo and a printed-letter check decide (privacy._local_verdict);
# archival photos are blurred like colour ones. true sends whole photos and face crops to the billed GEMINI_API_KEY (paid tier, under
# Google's data processing terms) and, as a fallback, to Anthropic: both are named processors in
# docs/privacy.md. Never the free-tier key.
FACE_VERIFY_CLOUD = (os.getenv("FACE_VERIFY_CLOUD") or "false").strip().lower() in ("true", "1", "yes")
# With FACE_VERIFY_CLOUD off, the faces the local check keeps in an art-like photo (its source record names a relief,
# carving, statue, stained glass, gilding...) cannot be told from carved or painted ones on this machine. true sends
# only those face crops (never the whole photo) to Gemini on the billed GEMINI_API_KEY, which must be on Google's paid
# tier (the operator confirms the billing; Google is the processor, docs/privacy.md); each call is recorded in llm_usage.
# Off, or when the call fails, they are blurred and no crop is sent. Never the free-tier key. Off by default.
FACE_VERIFY_CLOUD_AMBIGUOUS = (os.getenv("FACE_VERIFY_CLOUD_AMBIGUOUS") or "false").strip().lower() in ("true", "1", "yes")

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
# 9:16 caption size: 80 px (operator decision 2026-11-03, the round-3 panel's 72 -> 80; 72 until then).
FONT_SIZE = int(os.getenv("FONT_SIZE") or 80)
# The ASS Fontsize that draws FONT_SIZE under libass's PlayResX/PlayResY scale (a shrunk word uses the same
# fit / 0.5625): 142 for 80 px (it was a fixed 128 for 72 px, so a FONT_SIZE set in .env drew at the old size).
FONT_SIZE_ASS = round(FONT_SIZE / 0.5625)
# The bottom band the Reel platforms draw their own interface over (safe_zones.REEL: Meta's 35%, 672 px,
# the same figure on YouTube Shorts); the caption pill ends above it.
from .safe_zones import REEL as _REEL_UI  # noqa: E402
CAPTION_MARGIN_V = _REEL_UI.bottom
MIN_BURST_WORDS = 2
TARGET_BURST_WORDS = 3
MAX_BURST_WORDS = 4  # round 3 (2026-11-02): at most 4 words a burst, 3 the target (was 5)
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

# 16:9 burn-in (documentary; round 2, finding 6): Montserrat Bold (measured with the variable font's Bold instance;
# libass needs a static face, below), one line of at most 42 characters, 4-8 words a burst, the plate's
# lower edge CAPTION_WIDE_MARGIN_V above the frame's bottom (round 3: 130, y 950 at 1080, clear of YouTube's scrubber
# and hover controls, about y 990-1080, and its timestamp corner, x 1620-1920, y 950-1080; one line still sits under
# the 16:9 graphics floor, y 820). Each 16:9 burst is shown whole and still (no word-by-word reveal). A sentence
# splits into bursts of at least CAPTION_WIDE_MIN_WORDS (3, so a 5-7 word sentence too long for one line can split
# without an orphan word); the phrase builder favours 24-42 characters, which is 4-8 words in practice.
# libass matches "Montserrat" only against a static face named so (it reads face 0 of a file and no variation axes:
# Montserrat-Variable.ttf is "Montserrat Thin" to it), so the 16:9 burn-in needs a static Montserrat Bold in fonts/;
# compose_video stops (libass_font_problems) rather than burn in a fallback face that overruns the plate.
CAPTION_WIDE_FONT = "Montserrat"
CAPTION_WIDE_SIZE = 52  # px rendered em, as FONT_SIZE; the ASS Fontsize comes from the font's metrics (81)
CAPTION_WIDE_MARGIN_V = 130
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

# Thumbnail Specifications. Automatic cover selection is fixed to this style until a cover-specific signal exists
# (YouTube long-form CTR or a manual Instagram test, QF-82); Reel-content rates never restart it, and there is
# deliberately no environment switch for it.
COVER_FIXED_STYLE = "badge"
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
# The backup worker's local retention (QF-72): verified hourly SQLite copies (48 = two days of hourly recovery points)
# and cloud-verified daily archives kept in the spool (their OneDrive copies are never deleted). Pending, damaged or
# unknown archives never count and are never deleted.
BACKUP_DATABASE_KEEP = max(2, int(os.getenv("BACKUP_DATABASE_KEEP") or "48"))
BACKUP_SNAPSHOT_KEEP = max(2, int(os.getenv("BACKUP_SNAPSHOT_KEEP") or "7"))
# The worker's redacted status receipt (timestamps and counts only), which the pipeline reads for /status and alerts.
# Both processes must resolve the same path; the default is inside the application's data directory.
BACKUP_STATUS_FILE = BASE_DIR / (os.getenv("BACKUP_STATUS_FILE") or "data/backup-status.json")  # relative: to the app root

# >>> Picture coverage (2026-11-02): every narrated line has a planned, honest visual -------------------------------
# Added as one block by the picture-coverage change; the lines above are owned by other changes.
# Before the paid voice: rounds a named-subject line no photo shows goes back to the writer (src/prevoice_lines.py).
PREVOICE_REWRITE_ROUNDS = max(1, int(os.getenv("PREVOICE_REWRITE_ROUNDS", "2")))
# The fallback cascade under a line no photo shows (src/scene_planner.fit_to_lines, src/picture_coverage.py).
PICTURE_CASCADE = os.getenv("PICTURE_CASCADE", "true").lower() in ("true", "1", "yes")
DETAIL_CROP_MIN_WIDTH = int(os.getenv("DETAIL_CROP_MIN_WIDTH", "600"))  # px of the source a punch-in keeps, at least
# Topic image gate: distinct approved authoritative photos a topic needs before it is produced ahead of others.
TOPIC_MIN_AUTHORITATIVE_IMAGES = int(os.getenv("TOPIC_MIN_AUTHORITATIVE_IMAGES", "8"))
# Further open-access sources (src/museum_sources.py). Openverse and the Art Institute are off by default: their API
# parameters could not be checked against the official documentation from the development container (2026-11-02);
# enable after a live check. The Library of Congress is on by default (operator decision 2026-11-03; off until then).
MUSEUM_OPENVERSE_ENABLED = os.getenv("MUSEUM_OPENVERSE_ENABLED", "false").lower() in ("true", "1", "yes")
FLICKR_API_KEY = os.getenv("FLICKR_API_KEY", "")  # optional; verifies Openverse Flickr results at their source
MUSEUM_LOC_ENABLED = os.getenv("MUSEUM_LOC_ENABLED", "true").lower() in ("true", "1", "yes")
MUSEUM_ARTIC_ENABLED = os.getenv("MUSEUM_ARTIC_ENABLED", "false").lower() in ("true", "1", "yes")
# <<< Picture coverage ------------------------------------------------------------------------------------------

# >>> Illustration tier (2026-11-02): labelled illustrations for lines no authoritative photo shows -----------------
# Added as one block by the illustration-tier change (src/image_gen.py, src/image_quarantine.py,
# src/scene_planner.fit_to_lines); the lines above are owned by other changes.
# The image model and its API style. "auto" reads the style from the model id: an "imagen-*" model uses the legacy
# :predict endpoint, anything else the Gemini generateContent endpoint (image output). The default model id is NOT
# confirmed from the development container (no route to ai.google.dev): the operator must confirm it is current and
# reachable with GEMINI_API_KEY (image_gen.doctor() lists the models the key can reach). A configured model the key
# cannot reach means no illustration is generated (fail closed, logged).
# Verified against ai.google.dev/gemini-api/docs/image-generation on 2026-11-02: imagen-3.0-generate-002 was shut
# down on 2025-11-10; gemini-3-pro-image is "optimized for professional asset production" (no shutdown announced).
IMAGE_GEN_MODEL = (os.getenv("IMAGE_GEN_MODEL") or "gemini-3-pro-image").strip()
IMAGE_GEN_API = (os.getenv("IMAGE_GEN_API") or "auto").strip().lower()   # auto | gemini | imagen
IMAGE_GEN_CANDIDATES = max(1, min(4, int(os.getenv("IMAGE_GEN_CANDIDATES", "4"))))   # requests per beat, at most
IMAGE_GEN_MAX_CALLS_PER_RUN = max(0, int(os.getenv("IMAGE_GEN_MAX_CALLS_PER_RUN", "8")))  # rejected ones count too
IMAGE_GEN_MAX_LONGFORM = max(0, int(os.getenv("IMAGE_GEN_MAX_LONGFORM", "4")))   # illustrated beats, long-form video
IMAGE_GEN_MAX_RUNTIME_SHARE = float(os.getenv("IMAGE_GEN_MAX_RUNTIME_SHARE", "0.15"))  # of a Reel/Short's runtime
IMAGE_GEN_MIN_SHOT_SECONDS = float(os.getenv("IMAGE_GEN_MIN_SHOT_SECONDS", "1.5"))     # never on a shorter shot
IMAGE_GEN_LONGFORM_SECONDS = float(os.getenv("IMAGE_GEN_LONGFORM_SECONDS", "180"))     # longer = long-form caps
# The text screen needs local OCR (pytesseract and the tesseract binary): without it every candidate is rejected.
IMAGE_GEN_REQUIRE_OCR = os.getenv("IMAGE_GEN_REQUIRE_OCR", "true").lower() in ("true", "1", "yes")
IMAGE_GEN_MIN_SHARPNESS = float(os.getenv("IMAGE_GEN_MIN_SHARPNESS", "40"))   # Laplacian variance of the fitted frame
# Reference-grounded illustration (2026-11-02): reference images sent with a request (1-3), the reference comparison's
# minimum confidence for "same object, nothing added", whether museum/LoC images may be fetched as references, and the
# requested output size (generationConfig.imageConfig.imageSize: 512 | 1K | 2K | 4K; Gemini 3 image models only).
IMAGE_GEN_REFERENCE_MAX = max(1, min(3, int(os.getenv("IMAGE_GEN_REFERENCE_MAX", "3"))))
IMAGE_GEN_REFERENCE_MIN_CONFIDENCE = float(os.getenv("IMAGE_GEN_REFERENCE_MIN_CONFIDENCE", "0.85"))
IMAGE_GEN_REMOTE_REFERENCES = os.getenv("IMAGE_GEN_REMOTE_REFERENCES", "true").lower() in ("true", "1", "yes")
IMAGE_GEN_IMAGE_SIZE = (os.getenv("IMAGE_GEN_IMAGE_SIZE") or "2K").strip()
# A reference shown on screen as the exact photo needs at least this short side (px).
IMAGE_GEN_SHOW_MIN_SIDE = max(64, min(8192, int(os.getenv("IMAGE_GEN_SHOW_MIN_SIDE", "720"))))
IMAGE_GEN_TIMEOUT = float(os.getenv("IMAGE_GEN_TIMEOUT", "60"))
# Estimated cost of one illustration request, USD: asked of MEDIA_MONTHLY_BUDGET_USD before the request is sent and
# recorded in media_usage ("AI images") when it returns HTTP 200 (QF-46). [UNVERIFIED] Google's list price for a
# gemini-3-pro-image 1K/2K output image (about $0.134) plus reference-image input, rounded up; not yet read from the
# project's billing. Never below $0.01, so a request always counts against the cap.
IMAGE_GEN_COST_USD = max(0.01, float(os.getenv("IMAGE_GEN_COST_USD") or 0.14))
# <<< Illustration tier ---------------------------------------------------------------------------------------------

# >>> Captions round 3 (2026-11-02): the panel's caption changes (src/video_composer.py caption code) ----------------
# Caption values new in round 3; MAX_BURST_WORDS and CAPTION_WIDE_MARGIN_V changed in place above (one definition each).
# 9:16 type size: the panel's 72 -> 80 px change was held this round and made on 2026-11-03 with the operator's
# approval (FONT_SIZE, above; the burst tests pinned at 72 px were updated with it). Bursts hold at most 4 words
# (MAX_BURST_WORDS, above). The plate's height
# follows the type's cap height (CAPTION_REEL_PAD_Y_EM above and below), so the capitals sit optically centred.
CAPTION_REEL_PAD_Y_EM = 0.35                    # x the drawn cap height
# The pill's bottom edge stands this far above the platforms' bottom band (safe_zones.REEL.bottom), so it reads apart
# from the Instagram and Facebook username and description rows (was safe_zones.CAPTION_CLEARANCE, 8 px; the panel
# suggested about 28, held at 20 while tests/test_safe_zones.py keeps the pill within 20 px of the band). The pill's
# top stays below safe_zones.caption_band_top (y 1098), and so clear of the graphics floor (y 1058).
CAPTION_REEL_CLEARANCE = 20
# Words ease in over CAPTION_WORD_EASE_MS instead of cutting on; the plate fades in (CAPTION_PLATE_FADE_IN_MS) only where
# it really appears after a pause and out (CAPTION_PLATE_FADE_OUT_MS) only where it leaves before one. Between bursts
# heard back to back its width keeps to at most CAPTION_PLATE_STEPS sizes per run, so it no longer strobes every burst.
CAPTION_WORD_EASE_MS = 70
CAPTION_PLATE_FADE_IN_MS = 60
CAPTION_PLATE_FADE_OUT_MS = 80
CAPTION_PLATE_STEPS = 3
CAPTION_PLATE_SLACK = 96   # px: bursts within this of a run's plate width share it (48 px more padding a side at most)
# Reading rate: a burst wants at least CAPTION_READ_PER_CHAR s a character plus CAPTION_READ_BASE s on screen; a shorter
# one joins a neighbour where it can, else holds into the following pause (never past the next burst). Bursts faster
# than CAPTION_MAX_CPS characters a second are logged.
CAPTION_READ_PER_CHAR = 0.045
CAPTION_READ_BASE = 0.25
CAPTION_MAX_CPS = 17.0
# 16:9: CAPTION_WIDE_MARGIN_V (130, y 950 at 1080) is set with the other 16:9 values, above.
# <<< Captions round 3 ------------------------------------------------------------------------------------------------

# >>> Captions round 4 (2026-11-02): the panel's caption changes (src/video_composer.py caption code) ----------------
# 9:16 first frame: a Reel whose narration starts within CAPTION_FIRST_FRAME_WHOLE_S of the start (its first word's
# onset less the two-frame lead) shows its first burst whole, bone and gold, from frame 0, with no fade, so a muted
# autoplay or feed-preview frame already carries the hook line. compose_video turns it on for 9:16 only
# (build_subtitles' first_frame_whole, which a 16:9 call ignores: a documentary's first line waits for its
# narration); a first word at 1.0 s keeps the word-by-word reveal.
CAPTION_FIRST_FRAME_WHOLE_S = 0.6
# 9:16 cue-to-cut: a burst's show or hide CAPTION_CUT_SNAP_FRAMES frames or fewer from a picture cut moves onto the
# cut; where that would leave a burst under its reading time or overlap a neighbour, a boundary nearer than
# CAPTION_CUT_MIN_FRAMES moves out to that many frames from the cut instead (no plate change a frame off a cut).
CAPTION_CUT_SNAP_FRAMES = 6
CAPTION_CUT_MIN_FRAMES = 2
# <<< Captions round 4 ------------------------------------------------------------------------------------------------

# >>> Captions freeze pass (2026-11-04, design panel; src/video_composer.py caption code). Fixed values, not settings.
# 9:16 plate: the drawn ink (libass: small-caps \fscx, the style's Spacing, the first and last glyphs' side bearings)
# sits CAPTION_INK_FIT_PAD px inside each side of its plate (ink budget 640 px; operator decision 2026-11-04: the
# compliant 28). CAPTION_INK_PAD_MIN (40) is WITHDRAWN by that decision: no plate geometry uses it; kept only so
# older evidence scripts import. MAX_CAPTION_WIDTH and CAPTION_PAD_X are unchanged.
CAPTION_INK_PAD_MIN = 40
CAPTION_INK_FIT_PAD = 28
# Freeze pass round 2 (2026-11-04), fixed values. A 9:16 name too wide for one line at CAPTION_BOND_MIN_SIZE draws on
# two lines whose baselines are CAPTION_TWO_LINE_PITCH_EM x the cap height apart (operator decision N1).
CAPTION_TWO_LINE_PITCH_EM = 1.30
# P1: after clause or sentence punctuation, a silence of CAPTION_PAUSE_BREAK_S or more is a hard burst break; without
# punctuation, a break at a silence of CAPTION_PAUSE_SOFT_S or more is rewarded.
CAPTION_PAUSE_BREAK_S = 0.30
CAPTION_PAUSE_SOFT_S = 0.45
# D1: every burst is on screen at least CAPTION_MIN_ON_SCREEN_S (a merge, else a hold into the following silence);
# the phrase builder avoids bursts under CAPTION_SHORT_BURST_S. A one-word lead-in before a name needs only
# CAPTION_DWELL_AFTER_LAST_S.
CAPTION_MIN_ON_SCREEN_S = 0.50
CAPTION_SHORT_BURST_S = 0.55
# A bonded burst (a proper name, a figure group, the prepositions and articles leading into one: UNDER THE
# RASHTRAKUTA DYNASTY) may hold CAPTION_BOND_EXTRA_WORDS more words and CAPTION_BOND_EXTRA_CHARS more characters than
# MAX_BURST_WORDS / MAX_BURST_CHARS, when it fits at CAPTION_BOND_MIN_SIZE.
CAPTION_BOND_EXTRA_WORDS = 1
CAPTION_BOND_EXTRA_CHARS = 2
# A sentence of one or two words (NO. LOOK.) is its own burst when it is on screen at least this long; else it joins
# the following burst, never the one before.
CAPTION_SHORT_SENTENCE_MIN_S = 0.50
# The phrase builder rewards a break after a comma followed by at least this much silence.
CAPTION_COMMA_PAUSE_S = 0.15
# 9:16 dwell: a burst stays whole on screen at least CAPTION_DWELL_AFTER_LAST_S after its last word appears, and one
# holding a figure at least CAPTION_DIGIT_MIN_S in all, where the speech allows (else it joins a same-clause neighbour).
CAPTION_DWELL_AFTER_LAST_S = 0.30
CAPTION_DIGIT_MIN_S = 0.70
# 9:16 cue-to-cut (C9): where a boundary cannot snap onto a cut it moves at least CAPTION_CUT_CLEAR_FRAMES frames clear
# of it (a plate change one to three frames off a cut reads as a mistake).
CAPTION_CUT_CLEAR_FRAMES = 4
# 16:9: no plate wider than this (px at 1920 wide, 78% of the frame); a longer line shrinks to fit.
CAPTION_WIDE_MAX_PLATE = 1500
# Sidecar cues (.srt/.vtt): a cue lasts at least SIDECAR_MIN_CUE_HARD s (an orphan joins its neighbour); a cue ends at a
# sentence end that falls in the last SIDECAR_SENTENCE_TAIL of its text; of a cue's two lines the shorter is at least
# SIDECAR_LINE_BALANCE of the longer.
SIDECAR_MIN_CUE_HARD = 1.0
SIDECAR_SENTENCE_TAIL = 0.45
SIDECAR_LINE_BALANCE = 0.60
# <<< Captions freeze pass --------------------------------------------------------------------------------------------

# >>> Signed fact-check records and held quote cards (operator decisions A and B, 2026-11-03) ---------------------------
# FACT_RECORD_KEY: the secret every fact-check record is signed with (HMAC-SHA256, src/record_signing.py) when the
# checker runs, and verified with wherever a record is trusted (the design cards, the release gate, and so the
# publisher). Unset (or under 32 bytes, or a "your_" placeholder: src/record_signing.MIN_KEY_BYTES), nothing can
# be signed or verified, so every record is untrusted: in production
# (RELIC_ENVIRONMENT=production, which the launcher src/service_runtime.py sets) the fact check refuses to run and
# nothing publishes; in development every record stays untrusted (a warning, no hard stop); tests set a test key
# (tests/conftest.py). Never logged (src/ops.SecretMasker). Rotating it makes every record untrusted: the next run
# re-checks its copy and a fact sheet's card fields refuse until they are checked again.
FACT_RECORD_KEY = (os.getenv("FACT_RECORD_KEY") or "").strip()
# Quote cards are held (decision B): while false, design_cards.plan_quote_card refuses with reason "held". Do not
# turn on until signed records and a real checker key exist; then the speaker, work and date must come only from
# the checker's signed record, with the evidence's prose parsed only to refuse.
QUOTE_CARDS_ENABLED = (os.getenv("QUOTE_CARDS_ENABLED") or "false").strip().lower() in ("true", "1", "yes")
# <<< Signed fact-check records ---------------------------------------------------------------------------------------
