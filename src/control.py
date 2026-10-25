"""
src/control.py
==============
Operations over Telegram: slash commands, the daily heartbeat, and the dead-man's-switch ping.

Commands (approvers only, in the configured chat):
  /status   paused?, next scheduled run, pending approvals, recent runs
  /next     the topic the next run will produce
  /pause    stop scheduled runs and posts (an in-progress run finishes)
  /resume   re-enable scheduled runs
  /run      start a production run now (still needs your approval to publish)
  /produce N  render the next N queued topics back to back, each to review (the next starts after your decision)
  /posts    every booked post with its slot and status, links once posted; post now, move or cancel a booking
  /images   image sets waiting for review
  /review N send topic N's numbered contact sheet with an Approve button
  /drop N 2 5  remove images 2 and 5 from topic N (re-run the fetcher to refill)
  /propose  research 3 new topic ideas, each with Add / Skip buttons
  /help     this list
"""

import html
import json
import time as clock  # "time" is imported locally inside handle(), which shadows a plain name
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .config import ASSETS_DIR, CLAUDE_MONTHLY_BUDGET_USD, MEDIA_MONTHLY_BUDGET_USD, HEALTHCHECK_PING_URL, MIN_SCENE_IMAGES, is_configured
from .credits import CONTACT_SHEET, CREDITS_FILE, approve_set, drop_images, load_credits, save_credits, write_contact_sheet
from .db import PipelineDatabase
from . import config
from .topic_store import serialized

log = logging.getLogger(__name__)

PAUSED_KEY = "scheduler_paused"
COLLAPSE_OVER = 280  # characters: longer text starts collapsed (Telegram's chevron), shorter stays plain


EDIT_OPEN_KEY = "edit_open"   # the review an Edit card was last opened for, and when
EDIT_OPEN_SECONDS = 30 * 60   # a pasted template without a reply counts for that review this long
EDIT_LABELS = (("title", "title"), ("first comment", "first_comment"),
               ("carousel caption", "carousel_caption"), ("caption", "caption"))
COPY_BUTTON_LIMIT = 256  # Telegram's copy_text button holds at most this many characters


def edit_prompt(payload: Dict[str, Any], run_key: str):
    """✏️ Edit text: the current copy as a ready-to-edit template in a code block (Telegram offers Copy
    on it), plus one-tap copy buttons for the short fields. Credit lines are left out: an edited
    caption keeps them automatically. Unchanged fields sent back are ignored."""
    from .credits import is_attribution_line, is_hashtag_line
    def own(text):  # the wording only: hashtags and credits are kept for the operator
        kept = [ln for ln in (text or "").splitlines() if not is_attribution_line(ln) and not is_hashtag_line(ln)]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    fields = [(label, own(payload.get(key)) if "caption" in key else (payload.get(key) or "").strip())
              for label, key in EDIT_LABELS]
    template = "\n\n".join(f"{label}: {value}" for label, value in fields if value)  # a blank line between fields
    # A heading and short lines, not one nine-sentence paragraph (operator, 2026-10-23 UI review).
    intro = ("✏️ <b>Edit text</b>\n\n"
             "Copy the block below, change what you want and send it back: as a reply to this message, "
             "or pasted into the chat within 30 minutes.\n\n"
             "• Unchanged fields are ignored: send the whole block or only the lines you change.\n"
             "• Each field starts with its label; a caption can run over several lines.\n"
             "• A blank field (<code>title:</code> alone) is ignored, never emptied.\n"
             "• Hashtags and credit lines are kept (add a hashtag line to replace the hashtags).\n"
             "• The fact check runs again before the card updates.\n"
             "• Narration and on-screen text are part of the video: use Regenerate for those.")
    trailer = f"\n[edit run {html.escape(run_key)}]"
    block = f"\n\n<pre><code class=\"language-text\">{html.escape(template)}</code></pre>" if template else ""
    if len(intro) + len(template) + len(trailer) > 3900:  # Telegram's 4,096-character message limit
        short = "\n".join(f"{label}: {value}" for label, value in fields if value and "caption" not in label)
        block = (f"\n\n<pre><code class=\"language-text\">{html.escape(short)}</code></pre>"
                 f"\n<b>Caption</b> (too long to copy here as a block):\n{collapsible(dict(fields).get('caption', ''), 0)}")
    buttons = [[{"text": f"📋 Copy {label}", "copy_text": {"text": value}}]
               for label, value in fields if value and label in ("title", "first comment") and len(value) <= COPY_BUTTON_LIMIT]
    return (intro + block + trailer, buttons) if buttons else intro + block + trailer


def collapsible(text: str, over: int = COLLAPSE_OVER) -> str:
    """Escaped text, in an expandable block once it is long: a card stays short until opened."""
    escaped = html.escape(text or "")
    return f"<blockquote expandable>{escaped}</blockquote>" if len(text or "") > over else escaped
# Set by the production launcher: where /deploy leaves its request for the release deployer, and
# where the deployer reports what it is doing.
DEPLOY_REQUEST_ENV = "RELIC_DEPLOY_REQUEST"
DEPLOY_STATUS_ENV = "RELIC_DEPLOY_STATUS"


def request_deploy(db: PipelineDatabase) -> str:
    """/deploy: asks the release deployer to look for a signed release now instead of at its next
    2-minute check. It still installs only a signed release, only when production is idle, and only
    after the tests pass on this machine."""
    import os
    target = os.environ.get(DEPLOY_REQUEST_ENV)
    if not target:
        return "🚀 Automatic deployment runs on production only; this bot installs nothing."
    Path(target).write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "by": "telegram"}), encoding="utf-8")
    text = ("🚀 <b>Deployment check requested.</b> The deployer looks for a new signed release within a minute. "
            "It installs one only when nothing is running, reviewing or due, and only after its tests pass here. "
            "While it installs, this bot is quiet for about five minutes; I'll report when it is back on the release.")
    try:
        status = json.loads(Path(os.environ.get(DEPLOY_STATUS_ENV, "")).read_text(encoding="utf-8-sig"))
        text += f"\n\nLast deployer status: {html.escape(str(status.get('Status', '')))[:400]}"
    except (OSError, ValueError):
        pass
    return text


DEPLOY_NOTICE_KEY = "deploy_notice"  # the last "tag:phase" said, so each is said once


def deployer_notice(db: PipelineDatabase) -> Optional[str]:
    """What the release deployer is about to do, from its status file, said once per release and phase:
    a release waiting to install, or installing now. The operator found the bot silent for five minutes
    with no warning (2026-10-24: taps during the rc20 install went unanswered). None when nothing is new."""
    import os
    path = os.environ.get(DEPLOY_STATUS_ENV, "")
    if not path:
        return None
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    text, tag = str(status.get("Status", "")), html.escape(str(status.get("Tag", "")))
    if not tag:
        return None
    try:  # the status outlives the install: a daemon just restarted on the release must not announce it
        from .release_info import release_label
        if release_label() == str(status.get("Tag", "")):
            db.set_setting(DEPLOY_NOTICE_KEY, f"{tag}:installed")
            return None
    except Exception:
        pass
    if text.startswith("Installing "):
        phase, notice = "installing", (f"⏸ <b>Installing release {tag}.</b> The bot is quiet for about five minutes "
                                       "while the release is checked out and tested; it reports when it is back.")
    elif re.match(r"^\S+ is ready", text):
        phase, notice = "ready", (f"📦 <b>Release {tag} is ready to install.</b> It installs when nothing is running or "
                                  "waiting for a decision; the bot is then quiet for about five minutes and reports "
                                  "when it is back on the release.")
    else:
        return None
    key = f"{tag}:{phase}"
    if db.get_setting(DEPLOY_NOTICE_KEY) == key:
        return None
    db.set_setting(DEPLOY_NOTICE_KEY, key)
    return notice

SendPhoto = Callable[[Path, str, Optional[list]], bool]
HOME = {"text": "← Dashboard", "callback_data": "cdash:"}
from .local_time import show as show_time


def running_job(db, resource: str):
    """When a job for this resource is running in this process (a record left by a stopped process is not
    running), its start time for the operator; else None."""
    import os
    with db._conn() as conn:
        for (value,) in conn.execute("SELECT value FROM settings WHERE key LIKE 'operator_job:%'"):
            job = json.loads(value)
            if job.get("resource") == resource and job.get("state") in ("queued", "running") and job.get("owner_pid") == os.getpid():
                return show_time(job.get("updated_at"), "%H:%M")
    return None


def job_button(db, resource: str, label: str, running: str = "⏳ Fetching… · Jobs") -> Dict[str, str]:
    """A button that starts a job, or, while that job runs, says so and opens Jobs (as Render does):
    a second tap on "Fetch more" looked like nothing happened, or was refused as a duplicate."""
    if running_job(db, resource):
        return {"text": running, "callback_data": "jobs:"}
    return {"text": label, "callback_data": resource}


def short_reason(error: str, limit: int = 280) -> str:
    """A failure reason for the dashboard: no exception class name, and cut at a word, never mid-word."""
    text = re.sub(r"^[A-Z]\w*(Error|Exception):\s*", "", (error or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def pager(prefix: str, page: int, pages: int) -> list:
    """⏮ first, ◀ previous, ▶ next, ⏭ last for a paged panel; a button that would not move is left out."""
    row = []
    if page > 1:
        row.append({"text": "⏮", "callback_data": f"{prefix}0"})
    if page > 0:
        row.append({"text": "◀", "callback_data": f"{prefix}{page - 1}"})
    if page < pages - 1:
        row.append({"text": "▶", "callback_data": f"{prefix}{page + 1}"})
    if page < pages - 2:
        row.append({"text": "⏭", "callback_data": f"{prefix}{pages - 1}"})
    return [row] if row else []
from .telegram_commands import help_text
HELP = help_text()

# Plain-language names for run statuses in Telegram.
STATUS_WORDS = {
    "producing": "being made", "pending_approval": "waiting for you", "approved": "approved",
    "published": "published", "simulated": "test run (not posted)", "rejected": "rejected",
    "regenerate": "regenerated", "timeout": "no decision in time", "failed": "failed",
    "cancelled": "cancelled by operator", "publish_unknown": "not sure it posted", "scheduled": "scheduled to post",
}


def _fetched_sets(assets_dir: Path) -> List[Path]:
    root = assets_dir.resolve()
    return sorted(d for d in assets_dir.iterdir() if d.is_dir() and d.resolve().parent == root
                  and (d / CREDITS_FILE).exists()) if assets_dir.is_dir() else []


def _resolve_topic_dir(ref: str, assets_dir: Path, db: Optional[PipelineDatabase] = None) -> Path:
    """Resolve only a single known folder inside the canonical asset root."""
    from pathlib import PureWindowsPath
    ref = ref.strip()
    if not ref or ref in (".", "..") or any(c in ref for c in "/\\:") or PureWindowsPath(ref).drive:
        raise ValueError("Use a topic number or ID from /images")
    if db is not None and ref.isdigit():
        topics = db.list_all_topics()
        if not 1 <= int(ref) <= len(topics):
            raise ValueError("Unknown topic number")
        ref = topics[int(ref)-1]["id"]
        candidate = assets_dir / ref
        if candidate.resolve().parent != assets_dir.resolve() or not (candidate / CREDITS_FILE).is_file():
            raise ValueError("This topic has no fetched images; open /images to fetch or upload")
        return candidate
    matches = [d for d in _fetched_sets(assets_dir) if d.name.lower() == ref.lower()
               or (not ref.isdigit() and d.name.split("_",1)[-1] == ref)]
    if db is None and ref.isdigit():
        matches = [d for d in _fetched_sets(assets_dir) if d.name.split("_")[0] in (ref, f"{int(ref):02d}")]
        if not matches:
            sets = _fetched_sets(assets_dir)
            matches = sets[int(ref)-1:int(ref)] if int(ref)>0 else []
    if len(matches) != 1:
        raise ValueError("No unique fetched image set; open /images")
    return matches[0]


@serialized
def _add_topic_to_bank(db: PipelineDatabase, title: str, u_site=None) -> Tuple[str, str, Dict[str, Any]]:
    from . import topic_proposer
    bank = topic_proposer._bank()
    existing_titles = [t["title"] for t in bank.get("topics", [])]
    dup = topic_proposer.near_duplicate(title, existing_titles)
    if dup:
        raise ValueError(f"Duplicate detected! Matches existing topic: {dup}")
    slug = topic_proposer._slugify(title)
    topic_id = slug

    from .unesco import UNESCOEngine
    unesco_engine = UNESCOEngine()
    if not u_site:
        u_site = unesco_engine.find_site(title)
    unesco_desc = None
    loc_country = "Global"
    loc_lat, loc_lon = 0.0, 0.0
    u_tag = ""
    if u_site:
        unesco_desc = f"{u_site.site} (UNESCO #{u_site.id_number}): {u_site.short_description or u_site.justification}"
        loc_country = u_site.states or loc_country
        loc_lat = u_site.latitude or 0.0
        loc_lon = u_site.longitude or 0.0
        u_tag = f"\n🏛️ <b>UNESCO Verified:</b> #{u_site.id_number} ({html.escape(u_site.site)})"

    new_topic = {
        "id": topic_id,
        "title": title,
        "sub_theme": "Historical Curiosities & Ancient Marvels",
        "hook": f"What really lies within {title}?",
        # latitude/longitude: the keys every reader uses (lat/lon here were never read).
        "location": {"name": title, "country": loc_country, "latitude": loc_lat, "longitude": loc_lon},
        "key_facts": [
            f"Documented historical site: {title}",
            "Architectural or archaeological significance recognized by researchers.",
            "Subject of ongoing academic and field study."
        ],
        "meta_categories": ["History & Philosophy", "Travel & Culture"],
        "hashtags": [f"#{slug[:20].replace('_', '')}", "#ancienthistory", "#archaeology", "#relicandriddle"],
        "sources": [{"title": title, "url": f"https://en.wikipedia.org/wiki/{slug}"}],
        "added": topic_proposer.added_record("operator", "added by title in Telegram"
                                             + (f" (UNESCO #{u_site.id_number})" if u_site else "")),
    }
    bank["topics"].append(new_topic)
    bank["total"] = len(bank["topics"])

    from .shot_archetypes import decompose_topic_visuals
    decomp = decompose_topic_visuals(
        title,
        context_facts=new_topic["key_facts"],
        unesco_context=unesco_desc,
    )
    queries = json.loads(topic_proposer.QUERIES.read_text(encoding="utf-8"))
    queries[topic_id] = {
        "queries": decomp["queries"],
        "keywords": decomp["keywords"],
        "archetypes": decomp.get("archetypes", {})
    }
    from .topic_store import commit
    commit(topic_proposer.BANK, topic_proposer.QUERIES, bank, queries)
    db._seed_topics_from_json()
    return topic_id, u_tag, decomp


def images_text(assets_dir: Path = ASSETS_DIR, db: Optional[PipelineDatabase] = None) -> str:
    review, short, approved = [], [], 0
    all_topics = db.list_all_topics() if db else []
    topic_order = {t["id"]: i for i, t in enumerate(all_topics, 1)}
    topic_titles = {t["id"]: t.get("title", "") for t in all_topics}

    def _set_sort_key(d: Path) -> tuple:
        parts = d.name.split("_", 1)
        if len(parts) > 1 and parts[0].isdigit():
            return (0, int(parts[0]), d.name)
        if d.name in topic_order:
            return (0, topic_order[d.name], d.name)
        return (1, 999999, d.name)

    sets = sorted(_fetched_sets(assets_dir), key=_set_sort_key)

    for d in sets:
        cr = load_credits(d)
        n = len(cr["images"])
        parts = d.name.split("_", 1)
        if len(parts) > 1 and parts[0].isdigit():
            label = f"{parts[0]} — {html.escape(parts[1].replace('_', ' '))} ({n})"
        elif d.name in topic_order:
            rank = topic_order[d.name]
            title = topic_titles.get(d.name) or d.name.replace("_", " ")
            label = f"{rank}. {html.escape(title)} (<code>{d.name}</code>) ({n})"
        else:
            label = f"{html.escape(d.name.replace('_', ' '))} ({n})"
        if cr["reviewed"] and n >= MIN_SCENE_IMAGES:
            approved += 1
        elif n >= MIN_SCENE_IMAGES:
            review.append(label)
        else:
            short.append(label)
    lines = [f"<b>Image sets</b>: {approved} approved, {len(review)} to review, {len(short)} short"]
    if review:
        lines += ["To review (send /review N):"] + [f"  • {x}" for x in review]
    if short:
        lines += [f"Short of {MIN_SCENE_IMAGES} images (open /images to fetch or upload):"] + [f"  • {x}" for x in short]
    return "\n".join(lines)


def review_caption(topic_dir: Path, ref_hint: Optional[str] = None) -> str:
    cr = load_credits(topic_dir)
    status_tag = " (approved)" if cr.get("reviewed") else ""
    lines = [f"🎬 <b>{html.escape(topic_dir.name)}</b> — {len(cr['images'])} images{status_tag}"]
    drop_ref = ref_hint if ref_hint else (topic_dir.name.split('_')[0] if topic_dir.name.split('_')[0].isdigit() else topic_dir.name)
    # First, so the 1,024-character photo caption never cuts it off; the Copy buttons fill the topic in.
    lines.append(f"<i>Edit with the numbers on the sheet: /drop {html.escape(drop_ref)} &lt;images&gt; · "
                 f"/swap {html.escape(drop_ref)} &lt;beat&gt; &lt;beat&gt;</i>")

    # Storyboard beat-grounded narrative breakdown
    from .storyboard import load_storyboard, generate_storyboard
    sb = load_storyboard(topic_dir)
    if not sb and cr.get("images"):
        try:
            sb = generate_storyboard(topic_dir.name, topic_dir)
        except Exception:
            sb = None

    if sb:
        lines.append("\n📋 <b>Storyboard Beat Direction:</b>")
        for b in sb:
            b_num = b.get("beat", 1)
            b_type = b.get("type", "SCENE")
            b_txt = b.get("text", "")
            snippet = b_txt if len(b_txt) <= 52 else b_txt[:49] + "..."
            lines.append(f"  <b>{b_num}. [{b_type}]</b>: <i>\"{html.escape(snippet)}\"</i> (<code>{html.escape(b.get('file', ''))}</code>)")
    else:
        for i, c in enumerate(cr["images"], 1):
            title = c.get("title", c["file"]).removeprefix("File:")
            lines.append(f"{i}. {html.escape(title[:48])} · {html.escape(c.get('license', ''))}")

    return "\n".join(lines)


def is_paused(db: PipelineDatabase) -> bool:
    return db.get_setting(PAUSED_KEY, "false") == "true"


def ping_healthcheck(suffix: str = "") -> None:
    """Pings the dead-man's-switch URL (suffix '/fail' reports a failure). Never raises."""
    if not is_configured(HEALTHCHECK_PING_URL):
        return
    try:
        requests.get(HEALTHCHECK_PING_URL.rstrip("/") + suffix, timeout=10)
    except requests.RequestException as e:
        log.warning("Healthcheck ping failed: %s", e)


SCRIPT_CHARS = 750  # a 45-second narration is ~650-750 characters


def check_azure_speech() -> dict:
    from .config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, is_configured
    if not is_configured(AZURE_SPEECH_KEY):
        return {"configured": False, "ok": False, "status": "Not configured"}
    region = AZURE_SPEECH_REGION if is_configured(AZURE_SPEECH_REGION) else "eastus"
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    try:
        r = requests.post(url, headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}, timeout=3)
        if r.status_code == 200:
            return {"configured": True, "ok": True, "region": region, "status": f"Ready (licensed fallback · {region})"}
        elif r.status_code == 401:
            return {"configured": True, "ok": False, "region": region, "status": f"❌ 401 Auth Error (invalid key or wrong region: {region})"}
        else:
            return {"configured": True, "ok": False, "region": region, "status": f"⚠️ HTTP {r.status_code} ({region})"}
    except Exception:
        return {"configured": True, "ok": False, "region": region, "status": f"⚠️ Probe failed ({region})"}


def quota_lines(voice_quota: Optional[dict]) -> List[str]:
    """Voice quota and provider fallback summary."""
    lines = []
    from .config import AZURE_SPEECH_KEY, is_configured

    has_azure = is_configured(AZURE_SPEECH_KEY)
    az = check_azure_speech() if has_azure else {"configured": False, "ok": False}

    if not voice_quota or not voice_quota.get("limit"):
        if az.get("configured"):
            lines.append(f"Azure Speech: {az['status']}")
            if not az.get("ok"):
                lines.append("🚨 <b>Voice alert:</b> Azure Speech has auth errors and ElevenLabs is unconfigured. " + _sentence(_unlicensed_consequence()))
        return lines

    left = max(0, voice_quota["limit"] - voice_quota["used"])
    reset = ""
    if voice_quota.get("resets"):
        reset = ", resets " + show_time(int(voice_quota["resets"]), "%d %b")
    line = (f"ElevenLabs ({html.escape(str(voice_quota.get('tier')))}): {left:,} of {voice_quota['limit']:,} "
            f"characters left (~{left // SCRIPT_CHARS} Reels){reset}")

    if left < 3 * SCRIPT_CHARS:
        if az.get("ok"):
            lines.append(f"⚠️ {line}. Falls back to Azure Speech ({az.get('region')}) when exhausted.")
        else:
            lines.append(f"⚠️ {line}. When it runs out: " + _unlicensed_consequence())
    else:
        lines.append(line)

    if az.get("configured"):
        lines.append(f"Azure Speech: {az['status']}")
        if left < 3 * SCRIPT_CHARS and not az.get("ok"):
            lines.append("🚨 <b>Licence alert:</b> both ElevenLabs and Azure Speech are unavailable. " + _sentence(_unlicensed_consequence()))

    return lines


AUDIT_NAMES = {"cdash": "Dashboard", "approve": "Approve", "reject": "Reject", "regen": "Regenerate",
               "cancel": "Cancel review", "rsn": "Reason", "cprog": "Refresh progress", "qrender": "Render",
               "qdoc": "Render with documentary", "qrefresh": "Refresh queue", "qbump": "Bump", "qdrop": "Drop",
               "resend": "Resend review", "rerender": "Re-render", "postreal": "Post for real?", "promote": "Post for real",
               "chistory": "Review history", "chist": "Review details", "postnow": "Post now", "produce": "Produce a batch",
               "cposts": "Posting", "posts": "Posting", "cancelpost": "Cancel a booking", "movepost": "Move a booking",
               "retrypost": "Retry the failed platform",
               "edit": "Edit text", "runstop": "Cancel run", "runstopok": "Confirm cancel",
               "simpub": "Simulated publish", "pubok": "It posted", "pubno": "It didn't post", "creviews": "Reviews",
               "cideas": "Topic ideas", "cdisc": "UNESCO marvels", "cprop": "Waiting ideas", "cpropose": "Research ideas",
               "cimages": "Images", "cmusic": "Music", "cstat": "Full status", "jobs": "Jobs", "jobcancel": "Cancel job",
               "uadd": "Add marvel", "uskip": "Skip marvel", "tadd": "Add idea", "tskip": "Skip idea",
               "cpause": "Pause", "cresume": "Resume", "caccounts": "Check accounts", "cov": "Cover", "covlock": "Lock cover"}


def audit_action_name(payload: str) -> str:
    """A button's or command's plain name for the audit log ("" when unknown or already readable)."""
    if payload.startswith("/"):
        return ""
    return AUDIT_NAMES.get(payload.partition(":")[0], "")


def clip_words(text: str, limit: int) -> str:
    """Text cut to `limit` at a word boundary, with an ellipsis ("…(Catherine " and "resolv" were cut
    mid-word: 2026-10-23 UI review)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:·(—-")
    return (cut or text[:limit - 1]) + "…"


def _sentence(text: str) -> str:
    """Capitalised as a sentence start ("… unconfigured. real runs stop" read as a typo)."""
    return text[:1].upper() + text[1:] if text else text


def _unlicensed_consequence() -> str:
    from . import config
    return ("real runs stop (a licensed voice is required); dry runs use the free test voice."
            if config.VOICE_REQUIRE_LICENSED else "runs use the free edge-tts voice, which is not licensed for monetized posts.")


def spend_lines(db: PipelineDatabase) -> List[str]:
    """This month's spend by what it is, against each cap."""
    from . import config
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    by_task = {r["task"]: r["cost"] for r in db.llm_spend_by_task(month)}
    light, heavy = by_task.pop("paid-fallback:light", 0.0), by_task.pop("paid-fallback:heavy", 0.0)
    lines = [f"AI on the billed key this month: light ${light:.2f} of ${config.PAID_FALLBACK_LIGHT_MONTHLY_USD:.2f}, "
             f"heavy ${heavy:.2f} of ${config.PAID_FALLBACK_HEAVY_MONTHLY_USD:.2f}"]
    claude = sum(c for task, c in by_task.items() if not task.startswith(("paid-fallback", "groq")))
    if claude or config.SCRIPT_ENGINE == "claude":
        lines.append(f"Claude this month: ${claude:.2f}" + (f" of ${CLAUDE_MONTHLY_BUDGET_USD:.2f}" if CLAUDE_MONTHLY_BUDGET_USD > 0 else ""))
    media = db.media_spend(month)
    lines.append(f"AI motion this month: ${media:.2f} of ${MEDIA_MONTHLY_BUDGET_USD:.2f}")
    return lines


def spend_summary(db: PipelineDatabase) -> str:
    """The same spend as one dashboard line (three unmarked lines read as loose text: 2026-10-23 UI
    review). Full status keeps one line per budget."""
    parts = [re.sub(r" this month: ", " ", line).replace(" of $", "/$") for line in spend_lines(db)]
    parts = [p.replace("AI on the billed key ", "billed AI ") for p in parts]
    return "💳 This month · " + " · ".join(parts)


REACHED_REVIEW = {"pending_approval", "approved", "rejected", "regenerate", "timeout", "simulated", "published",
                  "scheduled", "publish_unknown", "publishing"}
RERENDER_HOURS = 48  # a late failure older than this is not offered on the dashboard (Render it afresh)


def rerenderable(db: PipelineDatabase) -> Optional[Dict[str, Any]]:
    """The newest recent run that failed after its video was made and whose topic has not been made again
    since: the one Re-render on the dashboard offers."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=RERENDER_HOURS)).isoformat()
    made_since: set = set()
    for r in db.list_recent_runs(limit=10):
        if (r.get("updated_at") or r.get("created_at") or "") < since:
            break
        if r.get("status") in ("failed", "rejected") and r.get("master_video_path") and r.get("topic_id") not in made_since:
            return r
        if r.get("status") in REACHED_REVIEW:
            made_since.add(r.get("topic_id"))
    return None


def health_line(db: PipelineDatabase, days: int = 7) -> str:
    """How the system has been doing, on one dashboard line: runs, how many reached review, failures,
    the median length of finished runs, and what the post-run self-audit found (src/card_ledger.py).
    The operator had to ask for every one of these (2026-10-24)."""
    import json as _json
    import statistics
    from datetime import datetime, timedelta, timezone
    from . import card_ledger, improve
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    runs = [r for r in db.list_recent_runs(limit=80) if (r.get("created_at") or "") >= since]
    reached = sum(1 for r in runs if r.get("status") in REACHED_REVIEW)
    failed = sum(1 for r in runs if r.get("status") == "failed")
    lengths = []
    for r in runs:
        try:
            timings = _json.loads((config.OUTPUT_DIR / r["run_id"] / "timings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        seconds = card_ledger.run_seconds(timings)
        if seconds:
            lengths.append(seconds / 60)
    try:
        improve.ensure_schema(db)
        with db._conn() as conn:
            audit = conn.execute("SELECT COUNT(*) FROM findings WHERE source = 'self_audit' AND created_at >= ?",
                                 (since,)).fetchone()[0]
    except Exception:
        audit = 0
    text = f"🩺 Last {days} days · {len(runs)} run(s) · {reached} reached review · {failed} failed"
    if lengths:
        text += f" · median {statistics.median(lengths):.0f} min"
    text += f" · self-audit: {audit} finding(s)" if audit else " · self-audit clean"
    return text


def ready_pools(db: PipelineDatabase, topics: List[Dict[str, Any]]) -> int:
    """Queued topics whose photo pool passes the same readiness rule production uses."""
    from .asset_pipeline import pool_ready
    n = 0
    for t in topics:
        try:
            n += bool(pool_ready(t["id"]))
        except Exception:
            pass
    return n


def status_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    e = html.escape
    lines = [f"📊 <b>Studio status</b> · {'⏸ paused' if is_paused(db) else '▶️ active'}", "", "<b>Production</b>",
             f"• Next run: {e(next_run or 'not scheduled')}"]
    try:
        nxt = db.get_next_topic()
        if nxt:
            serial = {t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(nxt["id"], 1)
            lines.append(f"• Next topic: <b>{serial}. {e(nxt['title'])}</b>")
    except Exception:
        pass
    pending = db.list_pending_approvals()
    lines.append(f"• Waiting for your review: {len(pending)}")
    for p in pending[:3]:
        lines.append(f"   ◦ {e(db.run_label(p['run_id']))}")
    upcoming = db.list_scheduled_posts(("pending", "waiting"))
    if upcoming:
        from .post_schedule import fmt
        names = {"reel": "Reel", "story": "Stories", "carousel": "Carousel"}
        lines.append("• Upcoming posts:")
        for p in upcoming[:6]:
            when = fmt(datetime.fromisoformat(p["due_at"])) if p["due_at"] else "next carousel slot"
            lines.append(f"   ◦ {names.get(p['kind'], p['kind'])} {e(when)} — {e(db.run_label(p['run_id']))}")

    icons = {"failed": "❌", "simulated": "🧪", "published": "✅", "pending_approval": "🎬", "producing": "⏳",
             "rejected": "✖", "approved": "👍", "scheduled": "🗓", "cancelled": "⏹", "regenerate": "🔄",
             "timeout": "⌛", "publish_unknown": "⚠️"}  # every status an icon (Regenerated had a bare bullet)
    recent = db.list_recent_runs(5)
    lines += ["", "<b>Recent runs</b>"]
    for r in recent:
        word = STATUS_WORDS.get(r["status"], r["status"])
        lines.append(f"{icons.get(r['status'], '•')} {e(word[:1].upper() + word[1:])} · {e(db.run_label(r['run_id']))}")
    if not recent:
        lines.append("None yet.")

    topics = db.list_all_topics()
    published = sum(1 for t in topics if t.get("times_published", 0) > 0)
    queued = sum(1 for t in topics if t.get("status") == "queued")
    lines += ["", f"<b>Topics</b> · {queued} queued · {published} published · {len(topics)} total"]

    lines += ["", "<b>Budgets and voices this month</b>"]
    lines += [f"• {line.replace(' this month', '')}"
              for line in spend_lines(db) + quota_lines(voice_quota)]

    from . import accounts
    lines += ["", "<b>Accounts and issues</b>", "• " + accounts.status_line(db).replace("Publishing accounts: ", "Publishing: ")]
    try:
        from . import improve
        improve.sync_issue_files(db)
        still_open, fixed = len(db.list_open_issues()), db.count_issues("fixed")
        lines.append(f"• Issues: ⚠️ {still_open} open (see /improve)" + (f" · {fixed} fixed" if fixed else "") if still_open
                     else "• Issues: ✅ none open" + (f" · {fixed} fixed" if fixed else ""))
    except Exception as exc:
        log.debug("Issue counts unavailable: %s", exc)
    return "\n".join(lines)


def heartbeat_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    return "💓 <b>Daily heartbeat</b> — the daemon is running.\n" + status_text(db, next_run, voice_quota)


LAST_REVIEW_KEY = "last_review_outcome"
LAST_REVIEW_HOURS = 12


def last_review_outcome(db: PipelineDatabase) -> Optional[str]:
    """The latest decided review's receipt (HTML), for the dashboard, for LAST_REVIEW_HOURS."""
    try:
        record = json.loads(db.get_setting(LAST_REVIEW_KEY) or "{}")
    except ValueError:
        return None
    if not record.get("text") or clock.time() - float(record.get("at") or 0) > LAST_REVIEW_HOURS * 3600:
        return None
    return f"🧾 <b>Last review</b> · {record['text']}"


HISTORY_BLOCKED = {"rejected", "failed", "cancelled"}
HISTORY_APPROVED = {"approved", "simulated", "published"}
HISTORY_BUTTON = {"text": "📚 Review history", "callback_data": "chistory:"}


def _read_run_json(run_id: str, name: str):
    try:
        return json.loads((config.OUTPUT_DIR / run_id / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run_findings(db: PipelineDatabase, r: Dict[str, Any]) -> List[str]:
    """Why a run was blocked, in words: your reason, then the release gate's own lines, else the error."""
    from . import improve
    from .release import release_problems
    out: List[str] = []
    fb = improve.operator_feedback(db, r["run_id"]) or {}
    if fb.get("reason") or fb.get("text"):
        out.append("you: " + " · ".join(x for x in (fb.get("reason"), fb.get("text")) if x))
    report = _read_run_json(r["run_id"], "release_report.json")
    if report:
        out += release_problems(report, dry_run=True)[:4]
    if not out and r.get("error"):
        out.append(str(r["error"])[:160])
    return out or ["no reason recorded"]


def _self_audit_count(run_id: str) -> int:
    from . import card_ledger
    try:
        return len(card_ledger.audit(config.OUTPUT_DIR / run_id))
    except Exception:
        return 0


def _audience_line(db: PipelineDatabase, run_id: str) -> str:
    """The latest audience numbers per platform, when the collector has any (after publishing)."""
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT platform, metrics_json FROM audience_metrics WHERE run_id=? ORDER BY fetched_at DESC",
                                (run_id,)).fetchall()
    except Exception:
        return ""
    seen, parts = set(), []
    for platform, metrics_json in rows:
        if platform in seen:
            continue
        seen.add(platform)
        try:
            metrics = json.loads(metrics_json)
        except ValueError:
            continue
        nums = [f"{k} {v:,}" for k, v in metrics.items()
                if isinstance(v, (int, float)) and k in ("plays", "views", "reach", "likes", "shares", "saves", "comments")][:3]
        if nums:
            parts.append(f"{platform}: " + ", ".join(nums))
    return " · ".join(parts)


def review_history(db: PipelineDatabase, limit: int = 6) -> Tuple[str, List[List[Dict[str, str]]]]:
    """📚 Review history, from the runs' own records: what was blocked (to address whenever it suits you)
    and what was approved (to see what did well) (operator, 2026-10-24)."""
    e = html.escape
    runs = db.list_recent_runs(limit=80)
    blocked = [r for r in runs if r.get("status") in HISTORY_BLOCKED and (r.get("master_video_path") or r.get("status") == "rejected")][:limit]
    approved = [r for r in runs if r.get("status") in HISTORY_APPROVED][:limit]
    late = rerenderable(db)
    lines = ["📚 <b>Review history</b>", "",
             "⛔ <b>Blocked</b> · rejected by you, or stopped at the release gate. Each keeps its reasons; Make again rebuilds one whenever it suits you."]
    buttons: List[List[Dict[str, str]]] = []
    if not blocked:
        lines.append("• none among the recent runs")
    for r in blocked:
        why = "; ".join(e(x) for x in _run_findings(db, r)[:2])
        lines.append(f"• {e(db.run_label(r['run_id']))} · {e(r['status'])}\n  <i>{why}</i>")   # the label carries the date
        row = [{"text": f"📄 Why: {clip_words(db.run_label(r['run_id']), 24)}", "callback_data": f"chist:{db.run_key(r['run_id'])}"}]
        if late and late["run_id"] == r["run_id"]:
            row.append({"text": "🔁 Make again", "callback_data": f"rerender:{db.run_key(r['run_id'])}"})
        buttons.append(row)
    lines += ["", "✅ <b>Approved</b> · what passed, with its self-audit and the audience numbers once they exist."]
    if not approved:
        lines.append("• none among the recent runs")
    for r in approved:
        n = _self_audit_count(r["run_id"])
        audience = _audience_line(db, r["run_id"])
        how = ("self-audit clean" if n == 0 else f"{n} self-audit finding(s)") + (f" · {e(audience)}" if audience else "")
        lines.append(f"• {e(db.run_label(r['run_id']))} · {e(r['status'])}\n  <i>{how}</i>")
        buttons.append([{"text": f"📄 Details: {clip_words(db.run_label(r['run_id']), 22)}", "callback_data": f"chist:{db.run_key(r['run_id'])}"}])
    return "\n".join(lines), buttons


def history_details(db: PipelineDatabase, key: str) -> Tuple[str, List[List[Dict[str, str]]]]:
    """One run from the history: its reasons when blocked; its audit, gate and audience when approved."""
    from .release import release_problems
    e = html.escape
    run_id = db.resolve_run(key)
    r = db.get_run(run_id) or {}
    when = (r.get("created_at") or "")[:16].replace("T", " ")
    lines = [f"📄 <b>{e(db.run_label(run_id))}</b> · {e(r.get('status') or 'unknown')} · {e(when)}",
             f"Run ID: <code>{e(run_id)}</code>", ""]
    if r.get("status") in HISTORY_BLOCKED:
        lines.append("<b>Why it was blocked</b>")
        lines += [f"• {e(x)}" for x in _run_findings(db, r)[:8]]
        lines += ["", "Re-render (dashboard) rebuilds it with the voice and clips it already has; Regenerate is for the words."]
    else:
        n = _self_audit_count(run_id)
        audience = _audience_line(db, run_id)
        report = _read_run_json(run_id, "release_report.json")
        lines.append("<b>How it did</b>")
        lines.append("• self-audit: clean" if n == 0 else f"• self-audit: {n} finding(s) (cards against the run's records)")
        if report:
            problems = release_problems(report, dry_run=True)
            lines.append("• release gate: passed" if not problems else "• release gate: " + e("; ".join(problems[:3])))
        lines.append(f"• audience: {e(audience)}" if audience else "• audience: no numbers yet (collected after publishing)")
    return "\n".join(lines), [[HISTORY_BUTTON], [HOME]]


POST_ICON = {"pending": "🗓", "waiting": "⏳", "published": "✅", "failed": "❌", "unknown": "❓", "cancelled": "⏹"}
POST_NAME = {"reel": "Reel", "story": "Stories", "carousel": "Carousel"}


def _post_links(db: PipelineDatabase, run_id: str) -> str:
    """The links from a run's publication receipts, one per platform."""
    urls: Dict[str, str] = {}
    for pub in db.list_publications(run_id):
        try:
            payload = json.loads(pub.get("response_payload_json") or "{}")
        except ValueError:
            continue
        for platform, url in (payload.get("post_urls") or {}).items():
            if url and platform not in urls:
                urls[platform] = str(url)
    return " · ".join(f'<a href="{html.escape(u)}">{html.escape(p)}</a>' for p, u in urls.items())


def posting_panel(db: PipelineDatabase, limit: int = 10) -> Tuple[str, List[List[Dict[str, str]]]]:
    """🗓 Posting: every booking with its slot and status, the links once posted, and what can be done with
    a booking: post it now, move it to the next slot, cancel it (operator, 2026-10-24). Bookings live
    here, not on the platforms; the daemon posts each when its slot comes."""
    from .post_schedule import fmt
    e = html.escape
    upcoming = db.list_scheduled_posts(("pending", "waiting"))
    recent = sorted(db.list_scheduled_posts(("published", "failed", "unknown", "cancelled")), key=lambda p: p["id"], reverse=True)[:limit]
    lines = ["🗓 <b>Posting</b>", "", "<b>Upcoming</b> · booked here; the daemon posts each when its slot comes."]
    buttons: List[List[Dict[str, str]]] = []
    if not upcoming:
        lines.append("• nothing booked")
    for p in upcoming:
        when = fmt(datetime.fromisoformat(p["due_at"])) if p.get("due_at") else "next carousel slot"
        lines.append(f"• {POST_ICON.get(p['status'], '•')} {POST_NAME.get(p['kind'], p['kind'])} · {e(when)} — {e(db.run_label(p['run_id']))}")
        if p["kind"] == "reel" and p["status"] == "pending":
            buttons.append([{"text": f"⚡ Post now: {clip_words(db.run_label(p['run_id']), 20)}", "callback_data": f"postnow:{db.run_key(p['run_id'])}"}])
            buttons.append([{"text": "⏭ Move to the next slot", "callback_data": f"movepost:{p['id']}"},
                            {"text": "⏹ Cancel the booking", "callback_data": f"cancelpost:{p['id']}"}])
    lines += ["", "<b>Recent</b>"]
    if not recent:
        lines.append("• nothing posted yet")
    for p in recent:
        when = fmt(datetime.fromisoformat(p["due_at"])) if p.get("due_at") else ""
        detail = p.get("detail") or {}
        note = e(str(detail.get("error") or detail.get("reason") or ""))[:120]
        links = _post_links(db, p["run_id"]) if p["status"] == "published" else ""
        lines.append(f"• {POST_ICON.get(p['status'], '•')} {POST_NAME.get(p['kind'], p['kind'])} · {e(when)} — {e(db.run_label(p['run_id']))} · {e(p['status'])}"
                     + (f"\n  {links}" if links else "") + (f"\n  <i>{note}</i>" if note else ""))
        if p["kind"] in ("story", "carousel") and p["status"] in ("unknown", "failed") and (
                detail.get("failed") or "failed=[" in str(detail.get("error") or "")):
            # a partial publish: re-send only the platform(s) that failed (operator, 2026-10-24)
            buttons.append([{"text": f"🔁 Retry the failed platform: {POST_NAME.get(p['kind'], p['kind'])}", "callback_data": f"retrypost:{p['id']}"}])
    return "\n".join(lines), buttons


def post_candidates(db: PipelineDatabase, hours: int = 48, limit: int = 3) -> List[Dict[str, Any]]:
    """Finished dry runs (simulated) of the last `hours`, newest first: what "Post for real" can publish."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    out = []
    for r in db.list_recent_runs(limit=20):
        if (r.get("updated_at") or r.get("created_at") or "") < since:
            break
        if r.get("status") == "simulated" and r.get("master_video_path"):
            out.append(r)
        if len(out) >= limit:
            break
    return out


def dashboard_text_and_buttons(db: PipelineDatabase, next_run: Optional[str] = None, assets_dir: Path = ASSETS_DIR) -> Tuple[str, List[List[Dict[str, str]]]]:
    """What needs you first (reviews, proposals, problems), then the state of production."""
    e = html.escape
    paused = is_paused(db)
    topics = db.list_all_topics()
    queued = [t for t in topics if t.get("status") == "queued"]
    ready = ready_pools(db, queued)
    pending = db.list_pending_approvals()
    try:
        from . import improve
        waiting_proposals = [p for p in improve.proposals(db) if p["status"] == "ready"]
    except Exception:
        waiting_proposals = []
    from . import accounts
    account_problems = accounts.cached(db).get("problems") or []
    next_t = None
    try:
        next_t = db.get_next_topic()
    except Exception:
        pass

    needs = []
    if pending:
        needs.append(f"🎬 <b>{len(pending)} review(s) waiting</b>: " + ", ".join(e(db.run_label(p['run_id'])) for p in pending[:2]))
    timed_out = db.timed_out_reviews() if hasattr(db, "timed_out_reviews") else []
    if timed_out:  # their topics wait for this decision instead of being made again
        needs.append(f"⌛ <b>{len(timed_out)} review(s) timed out</b>: Resend or Reject in Reviews · "
                     + ", ".join(e(db.run_label(r['run_id'])) for r in timed_out[:2]))
    if waiting_proposals:
        needs.append(f"🧭 <b>{len(waiting_proposals)} improvement proposal(s)</b> — /improve")
    if account_problems:
        needs.append("⚠️ " + e(account_problems[0]))
    from . import provider_status
    needs += provider_status.dashboard_lines()  # a provider shut by a limit or cap, said here not only in the log
    if queued and ready < len(queued) and ready < 3:
        needs.append(f"🖼 Only {ready} queued topic(s) have photos ready")
    recent = db.list_recent_runs(limit=1)
    active = db.get_active_progress() if hasattr(db, "get_active_progress") else None
    late = rerenderable(db) if not active else None
    if late and not (recent and late["run_id"] == recent[0]["run_id"]):
        # A run that failed after its video was made is waiting for a decision too, even when a later run
        # (a cancelled scheduled one) is the latest: the panel said "Nothing needs you" above a Re-render
        # button (2026-10-24).
        reason = short_reason(late.get("error") or "", limit=1200)
        what = "Rejected, can be made again" if late.get("status") == "rejected" else "Failed after rendering"
        # The run id in code, not italics: Telegram linked its digits as a phone number (2026-10-24).
        needs.append(f"🔁 <b>{what}</b>: {e(db.run_label(late['run_id']))} · Run ID <code>{e(late['run_id'])}</code> · Re-render below"
                     + (f"\n<blockquote expandable>{e(reason)}</blockquote>" if reason else ""))
    if recent and recent[0].get("status") == "failed":
        # Which run, on one line; the whole reason folded underneath (it was cut off mid-sentence:
        # operator, 2026-10-23).
        reason = short_reason(recent[0].get("error") or "", limit=1200)
        needs.append(f"❌ <b>Last run failed</b>: {e(db.run_label(recent[0]['run_id']))} · Run ID <code>{e(recent[0]['run_id'])}</code>"
                     + (f"\n<blockquote expandable>{e(reason)}</blockquote>" if reason else ""))

    from . import config
    bot_id = config.TELEGRAM_BOT_TOKEN.split(":", 1)[0]
    environment = {"8701645452": "DEV", "8837268021": "PRODUCTION"}.get(bot_id, "IDENTITY UNVERIFIED")
    try:
        from .release_info import release_label
        release = release_label()
    except Exception:  # the dashboard never fails over a version label
        release = None
    if release:
        environment += f" ({e(release)})"
    lines = [f"🏛️ <b><u>RELIC &amp; RIDDLE</u></b> · {environment}", ""]
    lines += (["<b>Needs you</b>"] + needs + [""]) if needs else ["✅ <b>Nothing needs you right now.</b>", ""]
    outcome = last_review_outcome(db)
    if outcome:  # the decided review's receipt lives here, so the chat keeps one panel
        lines += [outcome, ""]
    lines.append(f"⏯️ Schedule: {'⏸ <b>paused</b>' if paused else '▶️ active'}"
                 + (f" · next run {e(next_run)}" if next_run and not paused else ""))
    if active:
        label = "Making" if active.get("worker_state") == "running" else "Last recorded run (worker not active/verified)"
        lines.append(f"⏳ {label}: {e(db.run_label(active['run_id']))} · step {active.get('step', 1)}/{active.get('total_steps', 5)}")
    lines.append(f"📋 Queue: {len(queued)} topics · {ready} with photos ready"
                 + (f" · next: <b>{e(next_t['title'])}</b>" if next_t else ""))
    lines.append(spend_summary(db))
    lines.append(health_line(db))

    waiting_reviews = len(pending) + len(timed_out)  # timed-out reviews are decisions waiting too
    row1 = [{"text": f"🎬 Reviews ({waiting_reviews})", "callback_data": "creviews:"}] if waiting_reviews else []
    if waiting_proposals:
        row1.append({"text": f"🧭 Proposals ({len(waiting_proposals)})", "callback_data": "cimprove:"})
    candidates = post_candidates(db, limit=1) if not active else []
    if candidates:  # a finished dry run can be posted for real from here (asked to confirm first)
        row1.append({"text": f"🚀 Post for real: {clip_words(db.run_label(candidates[0]['run_id']), 24)}",
                     "callback_data": f"postreal:{db.run_key(candidates[0]['run_id'])}"})
    booked = len(db.list_scheduled_posts(("pending", "waiting")))
    if booked:  # what is booked, with its status and the actions on it (operator, 2026-10-24)
        row1.append({"text": f"🗓 Posting ({booked})", "callback_data": "cposts:"})
    if late:
        # A run that failed after its video was made (the release gate, say) is made again in place. Not only
        # the latest run: a cancelled scheduled run hid the button for the run that mattered (2026-10-24).
        verb = "Make again" if late.get("status") == "rejected" else "Re-render"  # rejected: regenerated with the reason given
        text = f"🔁 {verb}" if recent and late["run_id"] == recent[0]["run_id"] else f"🔁 {verb}: {clip_words(db.run_label(late['run_id']), 28)}"
        row1.append({"text": text, "callback_data": f"rerender:{db.run_key(late['run_id'])}"})
    buttons = [row1] if row1 else []
    buttons += [
        [{"text": "⚡ Progress" if active else (f"⚡ Render #{next((i for i,t in enumerate(topics,1) if next_t and t['id']==next_t['id']),1)} now"),
          "callback_data": "cprog:" if active else (f"qrender:{next_t['id']}" if next_t else "qrender:1")},
         {"text": "📋 Queue", "callback_data": "qrefresh:"}],
        [{"text": "💡 Topic ideas", "callback_data": "cideas:"}, {"text": "🎨 Covers", "callback_data": "ccov:"}],
        [{"text": "🎵 Music", "callback_data": "cmusic:"}, {"text": "📊 Full status", "callback_data": "cstat:"}],
        [{"text": "🖼 Images", "callback_data": "cimages:"}, {"text": "🔄 Check accounts", "callback_data": "caccounts:"}],
        [{"text": "🧰 Jobs", "callback_data": "jobs:"}, {"text": "❓ Help", "callback_data": "chelp:Everyday"}],
        [{"text": "▶️ Resume" if paused else "⏸️ Pause", "callback_data": "cresume:" if paused else "cpause:"}],
    ]
    return "\n".join(lines), buttons


def startup_dashboard_card(db: PipelineDatabase, mode: str = "bot", time_str: Optional[str] = None,
                           timezone_str: Optional[str] = None, assets_dir: Path = ASSETS_DIR) -> Tuple[str, List[List[Dict[str, str]]]]:
    """Keep the dashboard heading, with startup state in its body."""
    started = (f"🟢 Daemon started: daily run at {html.escape(time_str or '09:00')} {html.escape(timezone_str or 'UTC')}"
               if mode == "daemon" else "🟢 Bot started (commands and reviews; no scheduled runs)")
    text, buttons = dashboard_text_and_buttons(db, None, assets_dir=assets_dir)
    heading, _, body = text.partition("\n\n")
    return heading + "\n\n" + started + "\n\n" + body, buttons


RUN_STARTING_KEY = "run_starting"  # the topic a queue Render just started, until its worker marks it
RUN_STARTING_SECONDS = 15 * 60


def make_command_handler(db: PipelineDatabase, next_run: Callable[[], Optional[str]],
                         trigger_run: Callable[[], str], send_photo: Optional[SendPhoto] = None,
                         assets_dir: Path = ASSETS_DIR,
                         voice_quota: Callable[[], Optional[dict]] = lambda: None,
                         apply_updates: Optional[Callable[[], str]] = None,
                         propose_topics: Optional[Callable[[], str]] = None,
                         run_actions: Optional[dict] = None,
                         produce_batch: Optional[Callable[[int], str]] = None,
                         send_audio: Optional[SendPhoto] = None,
                         send_message: Optional[Callable[[str, Optional[list]], Any]] = None,
                         delete_message: Optional[Callable[[int], bool]] = None) -> Callable[[str, str], str]:
    active_proposal_msg_ids: List[int] = []
    undo_stack: List[Dict[str, Any]] = []

    def busy_topic(starting: Optional[str] = None) -> Optional[str]:
        """The topic being made now: the live run's, one marked in production, or the one just started (its
        job may not have begun yet, so its status still says queued)."""
        if starting:
            return starting
        prog = db.get_active_progress() if hasattr(db, "get_active_progress") else None
        if prog and prog.get("run_id"):
            run = db.get_run(prog["run_id"]) or {}
            if run.get("topic_id"):
                return run["topic_id"]
        marked = next((t["id"] for t in db.list_all_topics() if t.get("status") == "in_production"), None)
        if marked:
            return marked
        # A run started from the queue whose worker has not yet marked its topic (a second Render tap on
        # another page still showed "Render #20 Now"): the record kept for its first minutes.
        try:
            record = json.loads(db.get_setting(RUN_STARTING_KEY) or "{}")
        except ValueError:
            record = {}
        recent = record.get("at") and clock.time() - float(record["at"]) < RUN_STARTING_SECONDS
        return record.get("topic") if recent else None

    def render_queue_text_and_buttons(show_all: bool = False, page: int = 0,
                                      starting: Optional[str] = None) -> Tuple[str, List[Dict[str, str]]]:
        busy = busy_topic(starting)

        def _readiness(topic_id: str) -> str:
            if topic_id == busy:
                return "🔄 In production"
            try:
                td = _resolve_topic_dir(topic_id, assets_dir, db=db)
                if td.exists():
                    cr = load_credits(td)
                    imgs = cr.get("images", [])
                    if cr.get("reviewed") and len(imgs) >= MIN_SCENE_IMAGES:
                        return "🟢 Ready to render"
                    elif imgs:
                        return "🟡 Needs image review"
            except Exception:
                pass
            return "🔵 Script Ready"

        topics = db.list_all_topics()
        queued = [t for t in topics if t.get("status") == "queued"]
        requeueable = [t for t in topics if t.get("status") in ("rejected", "failed", "published", "archived", "cancelled")]
        next_t = None
        try:
            next_t = db.get_next_topic()
        except Exception:
            pass
        topic_serial = {t["id"]: i for i, t in enumerate(topics, 1)}
        lines = [f"📋 <b>Topic queue</b> · {len(queued)} queued", ""]
        limit = 5
        page = max(0, min(page, max(0, (len(queued)-2)//limit)))
        lines[0] += f" · page {page+1} of {max(1, (max(0,len(queued)-1)+limit-1)//limit)}"

        buttons = []
        if next_t and (next_t.get("status") == "queued" or next_t["id"] == busy):
            sn = topic_serial.get(next_t['id'], 1)
            p_badge = " [🚀 Priority #1]" if next_t.get("priority", 0) > 0 else ""
            r_badge = _readiness(next_t['id'])
            lines.append(f"🎯 <b>Next:</b> {sn}. {html.escape(next_t['title'])}{p_badge}\n    <b>{r_badge}</b>\n")
            if busy:  # Telegram has no disabled button: it says what is happening and opens the progress instead
                buttons.append({"text": f"⏳ Rendering #{topic_serial.get(busy, sn)} · progress", "callback_data": "cprog:"})
            else:
                buttons.append({"text": f"⚡ Render #{sn} now", "callback_data": f"qrender:{next_t['id']}"})  # as on the dashboard
            remaining = [t for t in queued if t["id"] != next_t["id"]]
            if remaining:
                lines.append("<b>Other queued topics</b>")
                for t in remaining[page*limit:(page+1)*limit]:
                    s = topic_serial.get(t['id'], '')
                    p_badge = " [🚀 Priority]" if t.get("priority", 0) > 0 else ""
                    rb = _readiness(t['id'])
                    lines.append(f"  {s}. {html.escape(t['title'])}{p_badge}\n    <i>{rb}</i>")
                if len(remaining) > limit:
                    lines.append(f"  <i>{len(remaining)} other topics · use the page buttons below</i>")
                # Add bump button for the second item
                second_t = remaining[0]
                buttons.append({"text": f"⬆ Bump #{topic_serial.get(second_t['id'], 2)}", "callback_data": f"qbump:{second_t['id']}"})
            if next_t["id"] != busy:  # never drop the topic being made
                buttons.append({"text": f"🗑 Drop #{sn}", "callback_data": f"qdrop:{next_t['id']}"})
            elif remaining:  # while it is made, Drop pairs with Bump on the topic after it (Bump alone was offered)
                after = remaining[0]
                buttons.append({"text": f"🗑 Drop #{topic_serial.get(after['id'], 2)}", "callback_data": f"qdrop:{after['id']}"})
        elif queued:
            for t in queued[page*limit:(page+1)*limit]:
                s = topic_serial.get(t['id'], '')
                rb = _readiness(t['id'])
                lines.append(f"  {s}. {html.escape(t['title'])}\n    <i>{rb}</i>")
            if len(queued) > limit + 1:
                lines.append(f"  <i>...and {len(queued) - (limit + 1)} more (send /queue all to view all)</i>")
        else:
            lines.append("  <i>Queue is currently empty! Use /add &lt;title&gt; or /propose.</i>")

        if requeueable:
            lines.append(f"\n<b>Can be restored</b> · {len(requeueable)}")
            for t in requeueable[:5]:
                s = topic_serial.get(t['id'], '')
                st = t.get("status", "")
                lines.append(f"  {s}. {html.escape(t['title'])} · {html.escape(STATUS_WORDS.get(st, st))}")
            example = topic_serial.get(requeueable[0]["id"], 1)
            lines.append(f"\nRestore one with its number: <code>/requeue {example}</code>")

        pages = max(1, (max(0, len(queued) - 1) + limit - 1) // limit)
        # Rows, not a flat list: Render beside Refresh, Bump beside Drop, every page button on one row.
        render = [b for b in buttons if b["callback_data"].startswith(("qrender:", "cprog:"))]
        changes = [b for b in buttons if b["callback_data"].startswith(("qbump:", "qdrop:"))]
        rows = [render + [{"text": "🔄 Refresh Queue", "callback_data": "qrefresh:"}]]
        from . import config as app_config
        if render and not busy and app_config.documentary_enabled():  # the same topic as a Reel plus the 16:9 documentary (paused while rebuilt)
            rows.append([{"text": "🎬 Reel + 16:9 documentary", "callback_data": "qdoc:" + render[0]["callback_data"].partition(":")[2]}])
        if changes:
            rows.append(changes)
        return "\n".join(lines), rows + pager("qpage:", page, pages) + [[HOME]]

    """run_actions: button actions on a run, e.g. {"pubok": fn(run_id) -> reply, "pubno": ..., "resend": ...}."""
    def send_sheet(topic_dir: Path) -> bool:
        if send_photo is None:
            return False
        key = topic_dir.name
        serial = str({t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(topic_dir.name, topic_dir.name))
        caption = review_caption(topic_dir, ref_hint=serial)
        write_contact_sheet(topic_dir, load_credits(topic_dir)["images"])
        from .telegram_images import review_digest
        key += "@" + review_digest(topic_dir)
        # Edit right from the sheet (copy the command with this topic filled in, add the numbers), or go
        # up one level: the topic's images (fetch, upload, review) or another topic.
        return send_photo(topic_dir / CONTACT_SHEET, caption,
                          [[{"text": "✅ Approve", "callback_data": f"imgok:{key}"},
                            {"text": "← Topic images", "callback_data": f"imgcancel:{key}"}],
                           [{"text": f"📋 Copy /drop {serial}", "copy_text": {"text": f"/drop {serial} "}},
                            {"text": f"📋 Copy /swap {serial}", "copy_text": {"text": f"/swap {serial} "}}],
                           [{"text": "🖼 Other topics", "callback_data": "reviewpick:0"}]])

    def handle(command: str, args: str) -> str:
        if command == "runstop":
            run_id = db.resolve_run(args)
            return ("⏹ <b>Cancel this run after its current stage?</b>\n\n"
                    f"{html.escape(db.run_label(run_id))}\nAlready incurred provider usage is retained. An in-flight stage may finish. This does not cancel an upload.",
                    [[{"text": "Confirm cancellation", "callback_data": f"runstopok:{db.run_key(run_id)}"}], [HOME]])
        if command == "runstopok":
            accepted = db.request_run_cancel(db.resolve_run(args))
            text = "Cancellation requested; the worker will acknowledge at its next stage boundary." if accepted else "This run is no longer producing. Inspect its current status; no upload was cancelled."
            return text, [[{"text": "Jobs", "callback_data": "jobs:"}], [HOME]]
        if command in ("qrender", "qdoc"):
            from . import config as app_config
            if command == "qdoc" and not app_config.documentary_enabled():  # a button left on an older message
                q_text, q_buttons = render_queue_text_and_buttons()
                return ("⏸ The 16:9 documentary is paused while it is rebuilt; nothing was started. "
                        "Use Render for the Reel.\n\n" + q_text), q_buttons
            current = db.get_next_topic()
            serials = {str(i): t["id"] for i,t in enumerate(db.list_all_topics(), 1)}
            requested = serials.get(args, args)
            if not current or current["id"] != requested:
                text, buttons = render_queue_text_and_buttons()
                return "⚠️ The queue changed. Review the current next topic before starting.\n\n" + text, buttons
            # The documentary is made after the Reel and reviewed separately; approving it uploads it to YouTube.
            res = trigger_run("dual") if command == "qdoc" else trigger_run()
            started = "already" not in res.lower()  # else another run is busy: the panel shows that one
            if started:
                db.set_setting(RUN_STARTING_KEY, json.dumps({"topic": current["id"], "at": clock.time()}))
            q_text, q_buttons = render_queue_text_and_buttons(starting=current["id"] if started else None)
            return f"⚡ <b>Run:</b> {html.escape(res)}\n\n{q_text}", q_buttons
        if command == "qbump":
            if not db.bump_topic(args):
                return "Queue changed; this item cannot be bumped.", [[HOME]]
            q_text, q_buttons = render_queue_text_and_buttons()
            return f"🚀 Bumped topic to #1 in queue!\n\n{q_text}", q_buttons
        if command == "qdrop":
            t_obj = db.get_topic_by_id(args)
            if not db.drop_topic(args):
                return "Queue changed; this item cannot be dropped.", [[HOME]]
            undo_stack.append({"type": "qdrop", "topic_id": args, "desc": f"Drop of {t_obj['title'] if t_obj else args}"})
            q_text, q_buttons = render_queue_text_and_buttons()
            return f"🗑 Dropped topic from queue (type /undo to restore).\n\n{q_text}", q_buttons
        if command in ("qrefresh", "qpage"):
            return render_queue_text_and_buttons(page=int(args) if args.isdigit() else 0)
        if command in ("pickbump", "pickdrop"):
            # One paged topic picker for bump and drop: full numbered titles in the message (they wrap),
            # number buttons, the shared pager, back to the queue.
            verb = "bump" if command == "pickbump" else "drop"
            topics = db.list_all_topics()
            serial = {t["id"]: i for i, t in enumerate(topics, 1)}
            queued = [t for t in topics if t.get("status") == "queued"][1 if verb == "bump" else 0:]
            if not queued:
                return "📋 Nothing in the queue to " + verb + ".", [[{"text": "← Queue", "callback_data": "qrefresh:"}], [HOME]]
            pages = -(-len(queued) // 8)
            page = min(int(args) if args.isdigit() else 0, pages - 1)
            shown = queued[page * 8:page * 8 + 8]
            title = ("⬆ <b>Bump a topic to the front of the queue</b>" if verb == "bump" else
                     "🗑 <b>Drop a topic from the queue</b> (<code>/undo</code> restores it)")
            text = title + (f" · page {page + 1} of {pages}" if pages > 1 else "") + "\n\n" + "\n".join(
                f"<b>{serial[t['id']]}.</b> {html.escape(t['title'])}" for t in shown)
            numbers = [{"text": str(serial[t["id"]]), "callback_data": f"q{verb}:{t['id']}"} for t in shown]
            return text, ([row for row in (numbers[:4], numbers[4:]) if row] + pager(command + ":", page, pages)
                          + [[{"text": "← Queue", "callback_data": "qrefresh:"}], [HOME]])
        if command == "bump":
            ref = args.strip()
            if not ref:
                queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
                if len(queued) > 1:
                    text, buttons = handle("pickbump", "0")
                    if send_message:
                        send_message(text, buttons)
                        return ""
                    return text, buttons
                return (
                    "⬆ <b><u>BUMP TOPIC</u></b>\n\nTap to copy & edit: <code>/bump 2</code>\n\n"
                    "<i>Usage: <code>/bump &lt;number or topic_id&gt;</code>\n"
                    "Tip: Tap the code box above to copy instantly to input box.</i>"
                )
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found in database."
            if not db.bump_topic(match["id"]):
                return "Queue changed; topic cannot be bumped."
            return f"🚀 Promoted <b>{html.escape(match['title'])}</b> (<code>{html.escape(match['id'])}</code>) to priority #1 in the queue!"
        if command == "drop_topic":
            ref = args.strip()
            if not ref:
                return handle("pickdrop", "0")
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found."
            if not db.drop_topic(match["id"]):
                return "Queue changed; topic cannot be archived."
            undo_stack.append({"type": "qdrop", "topic_id": match["id"], "desc": f"Drop of {match['title']}"})
            return f"🗑 Archived <b>{html.escape(match['title'])}</b> from queue. Type /undo to restore."
        if command == "undo":
            if not undo_stack:
                return "ℹ️ Nothing to undo."
            last = undo_stack.pop()
            if last["type"] == "qdrop":
                if not db.requeue_topic(last["topic_id"]):
                    return "Topic state changed; the old action cannot be undone."
                return f"↩️ <b>Undone:</b> Restored {html.escape(last['desc'])} back to queue!"
            if last["type"] == "uskip":
                return f"↩️ <b>Undone:</b> Restored skipped wonder {html.escape(last.get('site_id', ''))}."
            if last["type"] == "tskip":
                return f"↩️ <b>Undone:</b> Restored skipped proposal {html.escape(last.get('slug', ''))}."
            return f"↩️ <b>Undone:</b> Restored {html.escape(last.get('desc', 'action'))}."
        if command == "covers":
            # Covers are chosen for a video awaiting review (Lock refuses any other run), so that is what
            # this opens; otherwise the newest run that has covers, as a preview. A run folder without
            # covers (cancelled, failed, still producing) is never chosen.
            ref = args.strip()
            from .config import OUTPUT_DIR
            back = [[{"text": "🎬 Reviews", "callback_data": "creviews:"}], [HOME]]

            def has_covers(d):
                return any((d / f"thumbnail_variant_{v}.jpg").exists() for v in ("a_blueprint", "b_intrigue", "c_minimal"))

            def run_folder(run):
                if run and run.get("master_video_path"):
                    d = Path(run["master_video_path"]).resolve().parent
                    if not d.is_relative_to(OUTPUT_DIR.resolve()):
                        raise ValueError("Run output lies outside the configured output root")
                    return d
                return None

            run_dir = None
            if ref:
                run = db.get_run(db.resolve_run(ref))
                run_dir = run_folder(run)
                if run_dir is None:
                    topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
                    runs = [p for p in OUTPUT_DIR.glob(f"run_{topic_dir.name}_*") if p.is_dir() and has_covers(p)]
                    run_dir = max(runs, key=lambda p: p.stat().st_mtime) if runs else None
                if not run_dir or not has_covers(run_dir):
                    return "🎨 This run has no covers yet (they are made when the video is rendered).", back
            else:
                waiting = [d for d in (run_folder(db.get_run(a["run_id"])) for a in db.list_pending_approvals()) if d and has_covers(d)]
                if len(waiting) > 1:
                    text = "🎨 <b>Covers</b>: choose the video in review\n\n" + "\n".join(
                        f"<b>{i}.</b> {html.escape(db.run_label(d.name))}" for i, d in enumerate(waiting, 1))
                    return text, [[{"text": str(i), "callback_data": f"ccov:{db.run_key(d.name)}"} for i, d in enumerate(waiting, 1)][:5]] + back
                if waiting:
                    run_dir = waiting[0]
                else:
                    rendered = sorted((p for p in OUTPUT_DIR.glob("run_*") if p.is_dir() and not p.name.startswith("_") and has_covers(p)),
                                      key=lambda p: p.stat().st_mtime, reverse=True)
                    if not rendered:
                        return ("🎨 <b>Covers</b>\n\nNo rendered video has covers yet. They are made when a video is "
                                "rendered, and chosen from its review card."), back
                    run_dir = rendered[0]
            run_rec = db.get_run(run_dir.name) or {}
            reviewable = run_rec.get("status") == "pending_approval" and (db.get_approval(run_dir.name) or {}).get("decision") == "pending"
            var_a = run_dir / "thumbnail_variant_a_blueprint.jpg"
            var_b = run_dir / "thumbnail_variant_b_intrigue.jpg"
            var_c = run_dir / "thumbnail_variant_c_minimal.jpg"
            selected = "b" if var_b.exists() else "a" if var_a.exists() else "c"
            buttons = [
                [
                    {"text": "📌 A: Blueprint", "callback_data": f"cov:{run_dir.name}:a"},
                    {"text": "✓ B: Intrigue", "callback_data": f"cov:{run_dir.name}:b"},
                ],
                [
                    {"text": "📌 C: Minimal", "callback_data": f"cov:{run_dir.name}:c"},
                    {"text": "🔄 Refresh", "callback_data": "ccov:"},
                ],
            ]
            if reviewable:
                buttons.append([{"text": f"🔒 Lock Variant {selected.upper()}", "callback_data": f"covlock:{run_dir.name}:{selected}"}])
            buttons.append([HOME])
            label = html.escape(db.run_label(run_dir.name)) if run_rec else f"<code>{html.escape(run_dir.name)}</code>"
            caption = (
                f"🎨 <b>Cover Studio</b> · {label}\n\n"
                f"Previewing: <b>Variant {selected.upper()}</b>\n"
                + ("Tap A, B or C to flip the preview, or Lock to choose it for this video."
                   if reviewable else "<i>Preview only: this video isn't awaiting review, so its cover can't be "
                   "changed. Covers are chosen from a video's review card.</i>")
            )
            cover_to_show = var_b if var_b.exists() else (var_a if var_a.exists() else var_c)
            if send_photo and cover_to_show.exists():
                send_photo(cover_to_show, caption, buttons, cache_key=f"cover:{run_dir.name}:{selected}")
                return ""
            return caption
        if command in ("cov", "covlock"):
            parts = args.split(":")
            if len(parts) >= 2:
                r_name, var_letter = parts[0], parts[1].lower()
                from .config import OUTPUT_DIR
                if Path(r_name).name != r_name or r_name in (".","..") or var_letter not in ("a","b","c"):
                    raise ValueError("Invalid cover reference")
                r_dir = (OUTPUT_DIR / r_name).resolve()
                if r_dir.parent != OUTPUT_DIR.resolve():
                    raise ValueError("Invalid cover path")
                run = db.get_run(r_name)
                if not run or run.get("status") != "pending_approval":
                    return "This run is not awaiting review; its cover cannot be changed."
                if (db.get_approval(r_name) or {}).get("decision") != "pending":
                    return "This review is closed; its cover cannot be changed."
                if command == "cov":
                    return "Preview selection only. Use the cover panel to preview and Lock to commit."
                var_file = (r_dir / f"thumbnail_variant_{var_letter}_blueprint.jpg" if var_letter == "a"
                            else r_dir / f"thumbnail_variant_{var_letter}_intrigue.jpg" if var_letter == "b"
                            else r_dir / f"thumbnail_variant_{var_letter}_minimal.jpg")
                if var_file.exists():
                    approval = db.get_approval(r_name) or {}
                    with db._conn() as conn:
                        changed = conn.execute("UPDATE approvals SET telegram_message_id=NULL, revision=revision+1 WHERE run_id=? AND decision='pending' AND revision=?", (r_name, approval.get("revision",0))).rowcount
                    if not changed:
                        return "Review changed; cover was not modified."
                    import shutil
                    from PIL import Image
                    shutil.copy2(var_file, r_dir / "thumbnail_cover.jpg")
                    try:
                        im = Image.open(r_dir / "thumbnail_cover.jpg")
                        w, h = im.size
                        top = max(0, min(h - w, (h // 2) - (w // 2)))
                        im.crop((0, top, w, top + w)).save(r_dir / "thumbnail_profile_grid.jpg", quality=95)
                    except Exception:
                        pass
                    style_name = "Forensic Blueprint" if var_letter == "a" else "Discovery Intrigue" if var_letter == "b" else "Cinematic Minimal"
                    action_word = "locked" if command == "covlock" else "selected and locked"
                    return f"✅ Cover {action_word}: <b>Variant {var_letter.upper()} ({style_name})</b> for <code>{html.escape(r_name)}</code>. Resend this run for review before approving."
            return "Cover was not changed: the requested variant is missing or invalid."
        if command == "audit":
            limit = 10
            if args.strip().isdigit():
                limit = min(50, max(1, int(args.strip())))
            logs = db.list_audit_logs(limit=limit)
            if not logs:
                return "🛡️ No audit log entries recorded yet."
            lines = [f"🛡️ <b><u>AUDIT LOG</u></b> (<i>last {len(logs)} events</i>):", ""]
            for l in logs:
                st_icon = "🟢" if l.get("status") == "allowed" else "🔴"
                ts = show_time(l.get("created_at"), "%m-%d %H:%M:%S") if l.get("created_at") else ""
                actor = str(l.get("username") or "").strip()
                uid = str(l.get("user_id") or "").strip()
                if not actor or actor in ("-", "_", "none", "unknown"):
                    actor = f"op:{uid[-4:]}" if uid else "operator"
                elif not actor.startswith("@") and not actor.startswith("op:"):
                    actor = f"@{actor}"

                act = str(l.get("action") or l.get("event_type") or "").strip()
                raw_pl = str(l.get("payload") or "").strip()
                header = f"{st_icon} <code>{ts}</code> • <b>{html.escape(actor)}</b>"
                if act:
                    header += f" (<i>{html.escape({'callback_query': 'button'}.get(act, act))}</i>)"

                if len(raw_pl) <= 35 and "\n" not in raw_pl:
                    # What was done, then the raw record as evidence (it showed only "cdash:", "rsn:16:other").
                    name = audit_action_name(raw_pl)
                    lines.append(f"{header} → " + (f"{html.escape(name)} " if name else "") + f"<code>{html.escape(raw_pl)}</code>")
                else:
                    lines.append(f"{header}:\n<blockquote expandable>{html.escape(raw_pl)}</blockquote>")
            return "\n".join(lines)
        if command in ("start", "dashboard"):  # /start is Telegram's own first command
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            if send_message:
                send_message(dash_text, dash_buttons)
                return ""
            return dash_text
        if command == "cdash":
            dash_text, _ = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return dash_text
        if command in ("accounts", "caccounts"):
            from . import accounts
            accounts.check(db, force=True)
            return accounts.status_line(db), [[HOME]]
        if command in ("help", "chelp", "commands"):
            from .telegram_commands import GROUPS, help_text
            if not send_message and command == "help":
                return HELP
            group = args if args in GROUPS else "Everyday"
            return help_text(group), [[{"text": name, "callback_data": f"chelp:{name}"}] for name in GROUPS if name != group] + [[HOME]]
        if command in ("progress", "cprog"):
            active_prog = db.get_active_progress() if hasattr(db, "get_active_progress") else None
            if args and (not active_prog or db.resolve_run(args) != active_prog.get("run_id")):
                run = db.get_run(db.resolve_run(args))
                return "ℹ️ This progress card is no longer current. Run status: " + html.escape((run or {}).get("status", "not found"))
            if not active_prog:
                return ("⏸ <b>Nothing is being made right now</b>\n\nStart the next topic from the queue, or check "
                        "the last run in Full status.",
                        [[{"text": "📋 Queue", "callback_data": "qrefresh:"}, {"text": "📊 Full status", "callback_data": "cstat:"}], [HOME]])
            step = active_prog.get("step", 1)
            total_steps = active_prog.get("total_steps", 5)
            topic_title = active_prog.get("topic_title", "Unknown Topic")
            run_id = active_prog.get("run_id", "active_run")
            details = active_prog.get("details", "")
            mode = db.get_setting("run_mode:" + run_id)
            mode_text = "🧪 Dry run · publishing/scheduling skipped · generation may incur API costs" if mode == "dry_run" else "🔴 Live production" if mode == "live" else "⚠️ Run mode not recorded"
            worker_state = active_prog.get("worker_state", "unverified")
            if worker_state != "running":
                note = "The worker has exited or its PID was reused." if worker_state == "interrupted" else "Worker ownership is not recorded or cannot be verified."
                updated = show_time(active_prog.get("updated_at"), "%d %b %H:%M %Z") if active_prog.get("updated_at") else "unknown"
                return (f"⚠️ <b>Last recorded progress — {worker_state}</b>\n\n{html.escape(note)}\n"
                        f"{html.escape(topic_title)} · step {step}/{total_steps}\n"
                        f"Last update: {html.escape(updated)}\n"
                        "This is not confirmation of an active run. Check Jobs before starting another.",
                        [[{"text": "🧰 Jobs", "callback_data": "jobs:"}], [HOME]])

            from .telegram_bot import PROGRESS_STEPS as milestones
            lines = [
                mode_text,
                f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n<b>{html.escape(topic_title)}</b> · started "
                f"{show_time(active_prog.get('start_time'), '%H:%M') if active_prog.get('start_time') else 'earlier'}",
                "",
            ]
            from .telegram_bot import progress_estimate, step_running_note
            bar, pct, eta, elapsed_sec, step_elapsed = progress_estimate(db, active_prog, step, total_steps)
            for idx, m in enumerate(milestones, start=1):
                if idx < step:
                    lines.append(f"✅ <b>Step {idx}/{total_steps}:</b> {m}")
                elif idx == step:
                    detail_str = f" <i>({html.escape(details)})</i>" if details else ""
                    lines.append(f"🔄 <b>Step {idx}/{total_steps}:</b> {m}...{detail_str}{step_running_note(step_elapsed)}")
                else:
                    lines.append(f"◽ <b>Step {idx}/{total_steps}:</b> {m}")

            lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
            lines.append(f"⏱ <b>Elapsed:</b> {elapsed_sec // 60:02d}:{elapsed_sec % 60:02d} · <b>Time left:</b> {eta}")
            lines.append(f"Last stage update: {show_time(active_prog.get('updated_at'), '%H:%M:%S %Z') if active_prog.get('updated_at') else 'unknown'}")
            return "\n".join(lines)
        if command in ("ideas", "cideas"):
            text = ("💡 <b>Topic ideas</b>\n\nResearch new ones, browse UNESCO marvels, or decide on ideas "
                    "already waiting (each has Add/Skip).")
            started = running_job(db, "topic-research")
            research = ({"text": "🧰 Research running: Jobs", "callback_data": "jobs:"} if started else
                        {"text": "🔬 Research 3 new ideas", "callback_data": "cpropose:"})
            if started:
                text += f"\n\n🔬 <b>Research is running</b> (started {started}). Its result opens here when it finishes; keep browsing meanwhile."
            buttons = [[research],
                       [{"text": "🏛️ UNESCO marvels", "callback_data": "cdisc:"},
                        {"text": "📥 Waiting ideas", "callback_data": "cprop:"}],
                       [HOME]]
            if command == "cideas":
                return text, buttons
            if send_message:
                send_message(text, buttons)
                return ""
            return text
        if command == "edit":
            run_id = db.resolve_run(args.strip())
            payload = (db.get_run(run_id) or {}).get("script_payload") or {}
            # A pasted template (not sent as a reply) goes to this review for a while: see EDIT_OPEN_KEY.
            db.set_setting(EDIT_OPEN_KEY, json.dumps({"run": args.strip(), "at": clock.time()}))
            return edit_prompt(payload, args.strip())
        if command == "edittext":
            key, _, text = args.partition(" ")
            fields = {"title": "title", "first comment": "first_comment", "carousel caption": "carousel_caption",
                      "caption": "caption"}
            # Several fields in one reply ("title: X; first comment: Y", or one per line): a reply that did
            # this set the title to the whole text (dev, 2026-10-23).
            # After a label's colon, only spaces on the same line: "title:" left blank above "first comment: …"
            # made the title the whole next line (blank fields are ignored instead).
            parts = re.split(r"(?:^|\n|;\s*)(title|first comment|carousel caption|caption)[ \t]*:[ \t]*",
                             text.strip(), flags=re.IGNORECASE)
            changes = {fields[label.lower()]: value.strip().rstrip(";").strip()
                       for label, value in zip(parts[1::2], parts[2::2]) if value.strip()}
            if not changes and text.strip():
                changes = {"caption": text.strip()}  # no label: the whole reply is the caption, as before
            action = (run_actions or {}).get("edit")
            if action is None:
                return "Editing works while the bot or daemon is running."
            return action(db.resolve_run(key), changes)
        if command == "cimprove":
            from . import improve
            text, buttons = improve.improve_text(db)
            return text, buttons + [[HOME]]
        if command == "creviews":
            pending = db.list_pending_approvals()
            timed_out = db.timed_out_reviews() if hasattr(db, "timed_out_reviews") else []
            candidates = post_candidates(db)
            if not pending and not timed_out and not candidates:
                return "No reviews are waiting.", [[HISTORY_BUTTON], [HOME]]
            if not pending and not timed_out:
                lines = ["🚀 <b>Post candidates</b> · finished dry runs of the last two days; each asks you to confirm.", ""]
                buttons = []
                for r in candidates:
                    lines.append(f"• {html.escape(db.run_label(r['run_id']))}")
                    buttons.append([{"text": f"🚀 Post for real: {clip_words(db.run_label(r['run_id']), 26)}",
                                     "callback_data": f"postreal:{db.run_key(r['run_id'])}"}])
                return "\n".join(lines), buttons + [[HISTORY_BUTTON], [HOME]]
            # Where the card is can't be promised: the chat may have been cleared or the card deleted.
            lines = ["🎬 <b>Reviews waiting</b>", "",
                     "Resend sends a review card again at the bottom of the chat, with its video, and removes the "
                     "earlier one if it is still there.", ""]
            buttons = []

            def resend_button(run_id):
                title = ((db.get_run(run_id) or {}).get("script_payload") or {}).get("title") or db.run_key(run_id)
                return [{"text": f"📨 Resend: {clip_words(title, 28)}", "callback_data": f"resend:{db.run_key(run_id)}"}]
            for p in pending[:5]:
                lines.append(f"• {html.escape(db.run_label(p['run_id']))}")
                buttons.append(resend_button(p["run_id"]))
            if timed_out:  # a timed-out review's topic waits for this decision (TIMEOUT_HOLD_DAYS)
                lines += ["", "⌛ <b>Timed out</b> (its topic waits for you; Resend opens the card with Reject on it):"]
                for r in timed_out[:5]:
                    lines.append(f"• {html.escape(db.run_label(r['run_id']))}")
                    buttons.append(resend_button(r["run_id"]))
            return "\n".join(lines), buttons + [[HISTORY_BUTTON], [HOME]]
        if command == "chistory":
            text, buttons = review_history(db)
            return text, buttons + [[{"text": "🎬 Reviews", "callback_data": "creviews:"}], [HOME]]
        if command == "chist":
            if not args.strip():
                return handle("chistory", "")
            return history_details(db, args.strip())
        if command == "cstat":
            return status_text(db, next_run(), voice_quota()), [[{"text": "📈 Audience", "callback_data": "canalytics:"},
                                                                 {"text": "🗓 Posting", "callback_data": "cposts:"}], [HOME]]
        if command in ("analytics", "canalytics"):
            from . import audience
            return audience.analytics_text(db), [[{"text": "📊 Full status", "callback_data": "cstat:"}], [HOME]]
        if command == "caudit":
            return handle("audit", "10"), [[HOME]]
        if command == "ccov":
            return handle("covers", args)  # the latest render, whichever topic it is
        if command == "cprop":
            from . import topic_proposer
            from .models import TopicProposal
            waiting = topic_proposer.pending(db)
            if not waiting:
                return "💡 No topic proposals waiting. Research new ideas from Topic ideas.", [[{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
            slugs = list(waiting)
            index = slugs.index(args) if args in waiting else min(int(args), len(slugs) - 1) if args.isdigit() else 0
            slug = slugs[index]
            p = TopicProposal.model_validate(waiting[slug])
            text = f"💡 <b>Waiting idea {index + 1} of {len(slugs)}</b>\n\n" + topic_proposer.proposal_text(p)
            return text, [[{"text": "➕ Add", "callback_data": f"ptadd:{index}:{slug}"}, {"text": "✖ Skip", "callback_data": f"ptskip:{index}:{slug}"}],
                          *pager("cprop:", index, len(slugs)), [{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
        if command in ("ptadd", "ptskip"):
            from . import topic_proposer
            index, _, slug = args.partition(":") if args.partition(":")[0].isdigit() else ("0", "", args)
            if slug in topic_proposer.pending(db):
                (topic_proposer.add if command == "ptadd" else topic_proposer.skip)(db, slug)
            return handle("cprop", index)  # the idea that moved into this place
        if command == "cdisc":
            return handle("discoverpanel", args or "subterranean:0")
        if command == "cmusic":
            return handle("music", "")
        if command in ("mfmenu", "musicmenu") or (command == "music" and not args):
            from . import music
            pending = music.pending_tracks()
            library = music.load_library()
            approved = sum(bool(t.get("reviewed")) for t in library)
            text = (f"🎵 <b>Documentary music</b>\n\n{approved} approved · {len(pending)} waiting for review\n\n"
                    "Find instrumental tracks for historical documentaries, listen, then approve them for use. New downloads are never approved automatically.")
            if command == "mfmenu":
                return f"🎵 <b>Fetch new music</b>\n\nChoose a documentary mood. Fetches up to {config.MUSIC_FETCH_PER_MOOD} new CC0/CC BY tracks per mood; no paid generation. Each one waits for your review.", [[job_button(db, f"mfetch:{mood}", mood.title(), f"⏳ Fetching {mood}… · Jobs")] for mood in music.MOODS] + [[job_button(db, "mfetch:all", "All moods", "⏳ Fetching all moods… · Jobs")], [{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
            return text, [[{"text": "Find new tracks", "callback_data": "mfmenu:"}], [{"text": f"Review waiting ({len(pending)})", "callback_data": "mreview:"}], [{"text": "Browse approved", "callback_data": "mlibrary:0"}], [HOME]]
        if command == "mlibrary":
            from . import music
            tracks = [t for t in music.load_library() if t.get("reviewed")]
            page = max(0, int(args)) if args.isdigit() else 0
            start = page * 5
            lines = [f"🎵 <b>Approved music</b> · {len(tracks)} tracks"]
            for track in tracks[start:start+5]:
                lines.append(f"\n<b>{html.escape(track['title'])}</b>\n{html.escape(track['mood'])} · {html.escape(music.license_name(track))}")
            pages = max(1, -(-len(tracks) // 5))
            if pages > 1:
                lines[0] += f" · page {page + 1} of {pages}"
            return "\n".join(lines), pager("mlibrary:", page, pages) + [[{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
        if command == "mfetch":
            from scripts.fetch_music import fetch_music_tracks
            added = fetch_music_tracks(mood=None if args == "all" else args, add_count=config.MUSIC_FETCH_PER_MOOD)
            return f"🎵 Downloaded {added} new track(s). Listen and approve before use.", [[{"text": "Review waiting", "callback_data": "mreview:"}], [{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
        if command == "music" and args:
            action, _, ref = args.partition(" ")
            if action == "fetch":
                return handle("mfetch", ref) if ref else handle("mfmenu", "")
            if action in ("review", "approved"):
                return handle("mreview" if action == "review" else "mlibrary", ref)
        if command == "cimages":
            return "🖼 <b>Topic images</b>\n\nChoose a topic to fetch, upload or review its images.", [[{"text": "Browse topics", "callback_data": "imagepick:0"}], [HOME]]
        if command == "imagepick":
            topics = db.list_all_topics()
            pages = max(1, -(-len(topics) // 8))
            page = min(int(args) // 8 if args.isdigit() else 0, pages - 1)  # the argument is the first item's offset
            shown = list(enumerate(topics, 1))[page * 8:page * 8 + 8]
            # Full titles wrap in the message; the buttons carry only the numbers.
            lines = ["🖼 <b>Choose a topic for images</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else ""), ""]
            lines += [f"<b>{i}.</b> {html.escape(t['title'])}" for i, t in shown]
            numbers = [{"text": str(i), "callback_data": f"imagehelp:{t['id']}"} for i, t in shown]
            nav = [[{**b, "callback_data": "imagepick:" + str(int(b["callback_data"].rpartition(":")[2]) * 8)} for b in row]
                   for row in pager("imagepick:", page, pages)]
            return "\n".join(lines), [row for row in (numbers[:4], numbers[4:]) if row] + nav + [[HOME]]
        if command in ("image", "imagehelp"):
            if not args:
                return handle("imagepick", "")
            from .telegram_images import topic_for
            topic = topic_for(db, args)
            args = topic["id"]
            return (f"🖼 <b>{html.escape(topic['title'])}</b>\n\nFetch licensed photos, upload your own with guided prompts, or review this set. New images need human review.",
                    [[job_button(db, f"fetchimages:{args}", "Fetch more"), {"text": "Upload my image", "callback_data": f"upload:{args}"}],
                     [{"text": "Review set", "callback_data": f"review:{args}"}], [{"text": "← Images", "callback_data": "cimages:"}], [HOME]])
        if command == "fetchimages":
            if not args:
                return handle("imagepick", "")
            from .telegram_images import fetch_images
            topic_id, count = fetch_images(db, assets_dir, args)
            return (f"🖼 Fetch completed: {count} eligible image(s) for <code>{html.escape(topic_id)}</code>. Review before use.",
                    [[{"text": "Review set", "callback_data": f"review:{args}"}], [{"text": "← Topic images", "callback_data": f"imagehelp:{args}"}], [HOME]])
        if command == "cpause":
            handle("pause", "")
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return f"⏸ <b>Scheduler Paused!</b>\n\n{dash_text}", dash_buttons
        if command == "cresume":
            handle("resume", "")
            dash_text, dash_buttons = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
            return f"▶️ <b>Scheduler Resumed!</b>\n\n{dash_text}", dash_buttons
        if command == "images":
            action, _, ref = args.partition(" ")
            if action in {"fetch", "upload", "review"}:
                return handle({"fetch": "fetchimages", "upload": "image", "review": "review"}[action], ref)
            if args:
                return handle("imagehelp", args)
            if send_message:
                return handle("cimages", "")
            return images_text(assets_dir, db=db)
        if command == "reviewpick":
            # Every topic with fetched images, paged, each marked approved / needs review / too few images
            # (the picker used to stop at the first five queued topics, which hid the rest).
            topics = db.list_all_topics()
            serial = {t["id"]: i for i, t in enumerate(topics, 1)}
            sets = []
            for t in topics:
                try:
                    td = _resolve_topic_dir(t["id"], assets_dir, db=db)
                    cr = load_credits(td)
                except Exception:
                    continue
                if cr.get("images"):
                    count = len(cr["images"])
                    mark = "⛔" if count < MIN_SCENE_IMAGES else "✅" if cr.get("reviewed") else "⚠️"
                    sets.append((serial[t["id"]], t["title"], count, mark))
            back = [[{"text": "← Images", "callback_data": "cimages:"}], [HOME]]
            if not sets:
                return "🎬 <b>Storyboard review</b>\n\nNo topic has fetched images yet. Open Images to fetch or upload some.", back
            pages = -(-len(sets) // 8)
            page = min(int(args) if args.isdigit() else 0, pages - 1)
            shown = sets[page * 8:page * 8 + 8]
            counts = {m: sum(1 for s in sets if s[3] == m) for m in ("✅", "⚠️", "⛔")}
            text = ("🎬 <b>Storyboard review</b>" + (f" · page {page + 1} of {pages}" if pages > 1 else "")
                    + f"\n✅ approved {counts['✅']} · ⚠️ needs your review {counts['⚠️']} · ⛔ too few images (need {MIN_SCENE_IMAGES}) {counts['⛔']}\n\n"
                    + "\n".join(f"<b>{n}.</b> {mark} {html.escape(title)} · {count} images" for n, title, count, mark in shown))
            numbers = [{"text": str(n), "callback_data": f"review:{n}"} for n, _, _, _ in shown]
            return text, [row for row in (numbers[:4], numbers[4:]) if row] + pager("reviewpick:", page, pages) + back
        if command == "review":
            ref = args.strip()
            if not ref:
                text, buttons = handle("reviewpick", "0")
                if send_message:
                    send_message(text, buttons)
                    return ""
                return text, buttons
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            if send_sheet(topic_dir):
                return ""
            return review_caption(topic_dir, ref_hint=args.strip())
        if command == "drop":
            parts = args.split()
            if not parts or len(parts) < 2 or not all(n.isdigit() for n in parts[1:]):
                example_num = "1"
                try:
                    nxt = db.get_next_topic()
                    if nxt:
                        all_t = db.list_all_topics()
                        example_num = str({t["id"]: i for i, t in enumerate(all_t, 1)}.get(nxt["id"], 1))
                except Exception:
                    pass
                return (f"🗑 <b>Remove images from a storyboard</b>\n\nSend the topic number, then the image numbers "
                        f"shown on its storyboard sheet: <code>/drop {example_num} 2 5</code>\n\n"
                        "<i>Easiest from the sheet itself: it has a Copy /drop button.</i>",
                        [[{"text": "🎬 Storyboards", "callback_data": "reviewpick:0"}], [HOME]])
            ref, nums = parts[0], parts[1:]
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            msg = drop_images(topic_dir, [int(n) for n in nums])
            if load_credits(topic_dir)["images"]:
                send_sheet(topic_dir)
            return html.escape(msg)
        if command == "swap":
            parts = args.split()
            if len(parts) < 3 or not (parts[1].isdigit() and parts[2].isdigit()):
                try:
                    nxt = db.get_next_topic()
                    example = {x["id"]: i for i, x in enumerate(db.list_all_topics(), 1)}.get(nxt["id"], 1) if nxt else 1
                except Exception:
                    example = 1
                return (f"🔄 <b>Swap two beats in a storyboard</b>\n\nSend the topic number, then the two beat numbers "
                        f"shown on its storyboard sheet: <code>/swap {example} 2 4</code>\n\n"
                        "<i>Easiest from the sheet itself: it has a Copy /swap button.</i>",
                        [[{"text": "🎬 Storyboards", "callback_data": "reviewpick:0"}], [HOME]])
            ref, b1, b2 = parts[0], int(parts[1]), int(parts[2])
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            from .storyboard import load_storyboard, save_storyboard
            sb = load_storyboard(topic_dir)
            if not sb or len(sb) < 2:
                return f"⚠️ No storyboard found for {html.escape(topic_dir.name)}. Run /review {html.escape(ref)} first."
            if not (1 <= b1 <= len(sb) and 1 <= b2 <= len(sb)):
                return f"⚠️ Beat numbers must be between 1 and {len(sb)}."
            idx1, idx2 = b1 - 1, b2 - 1
            for key in ("file", "title", "license"):
                sb[idx1][key], sb[idx2][key] = sb[idx2].get(key), sb[idx1].get(key)
            save_storyboard(topic_dir, sb)

            # Reorder credits.json images list to reflect the updated storyboard beat sequence
            cr = load_credits(topic_dir)
            sb_files = [item["file"] for item in sb if "file" in item]
            cr_images_by_file = {img["file"]: img for img in cr.get("images", []) if "file" in img}
            new_cr_images = []
            for f in sb_files:
                if f in cr_images_by_file:
                    new_cr_images.append(cr_images_by_file[f])
            for img in cr.get("images", []):
                if img.get("file") not in sb_files:
                    new_cr_images.append(img)
            cr["images"] = new_cr_images
            cr["reviewed"] = False
            save_credits(topic_dir, cr)

            write_contact_sheet(topic_dir, cr.get("images", []), storyboards=sb)
            send_sheet(topic_dir)
            return (f"🔄 Swapped Beat {b1} (<code>{html.escape(sb[idx1].get('file', ''))}</code>) "
                    f"and Beat {b2} (<code>{html.escape(sb[idx2].get('file', ''))}</code>) for <b>{html.escape(topic_dir.name)}</b>.")
        if command in ("music", "mreview", "mkeep", "mdel", "mcancel", "mnext"):
            from . import music
            reply = ""
            if command == "mkeep":
                reply = "✅ " + html.escape(music.approve_track(args))
            elif command == "mdel":
                reply = "🗑 " + html.escape(music.remove_track(args))
            elif command == "mcancel":  # Cancel on cards from earlier releases: up to the Music menu
                return handle("musicmenu", "")
            to_music = {"text": "← Music", "callback_data": "musicmenu:"}
            pending = music.pending_tracks()
            if not pending:
                approved = sum(1 for t in music.load_library() if t.get("reviewed"))
                return (reply + "\n" if reply else "") + (
                    f"🎵 No tracks waiting for review ({approved} approved). Find new tracks from Music."), [[to_music], [HOME]]

            cur_idx = 0
            if command == "mnext":
                for idx, t in enumerate(pending):
                    if music.track_key(t) == args:
                        cur_idx = (idx + 1) % len(pending)
                        break
                reply = f"⏭ Skipped to track {cur_idx + 1} of {len(pending)}."
            elif command == "music" and args.strip():
                if args.strip().isdigit():
                    num = int(args.strip())
                    if 1 <= num <= len(pending):
                        cur_idx = num - 1

            nxt = pending[cur_idx]
            key = music.track_key(nxt)
            buttons = [
                [
                    {"text": "✅ Keep Track", "callback_data": f"mkeep:{key}"},
                    {"text": "🗑 Remove Track", "callback_data": f"mdel:{key}"},
                ]
            ]
            # Leaving the review goes up one level (the track stays waiting), not all the way to the dashboard.
            buttons.append([{"text": "⏭ Next Track", "callback_data": f"mnext:{key}"}, to_music] if len(pending) > 1 else [to_music])
            buttons.append([HOME])

            if send_audio is None or not send_audio(music.MUSIC_DIR / nxt["file"], (reply + "\n\n" if reply else "") + music.review_caption(nxt, len(pending)), buttons):
                return (reply + "\n" if reply else "") + music.review_caption(nxt, len(pending))
            return reply or ""
        if command in ("post", "postreal"):
            # Your per-video go-ahead for a finished dry run: asked once, then "promote" does it (the daemon
            # stays in dry-run mode; only this run is posted or booked). Before, a good dry-run output could
            # be posted only from the command line as the pipeline account (2026-10-24).
            run_id = db.resolve_run(args.strip())
            run = db.get_run(run_id)
            if not run:
                return "🚀 Unknown run. Use the Run ID from its card or receipt."
            if run.get("status") not in ("simulated", "approved"):
                return f"🚀 {html.escape(db.run_label(run_id))} is {html.escape(run['status'].replace('_', ' '))}; only a finished dry run (simulated) can be posted."
            from . import config as app_config
            where = "booked into the next free posting slot (Stories after it, the carousel in its next slot)" if app_config.POST_SCHEDULING else "posted now"
            buttons = [[{"text": "✅ Yes, post it", "callback_data": f"promote:{db.run_key(run_id)}"}]]
            if app_config.POST_SCHEDULING:  # on demand (operator, 2026-10-24): the Reel at once, Stories and carousel after it
                buttons.append([{"text": "⚡ Post now instead", "callback_data": f"postnow:{db.run_key(run_id)}"}])
            return (f"🚀 <b>Post this for real?</b>\n\n{html.escape(db.run_label(run_id))}\n\n"
                    f"It will be {where} on Facebook, Instagram and YouTube, after the release checks run in live mode. "
                    "This is the one action that publishes; the daily runs stay in dry-run mode.",
                    buttons + [[HOME]])
        if command in ("posts", "cposts"):
            text, buttons = posting_panel(db)
            return text, buttons + [[{"text": "📊 Full status", "callback_data": "cstat:"}], [HOME]]
        if command in ("cancelpost", "movepost", "retrypost"):
            action = (run_actions or {}).get(command)
            if action is None:
                return "Booking changes work while the bot or daemon is running."
            try:
                result = action(int(args.strip()))
            except ValueError:
                return "That booking id is not valid."
            text, buttons = posting_panel(db)
            return f"{result}\n\n{text}", buttons + [[HOME]]
        if command == "produce":
            if produce_batch is None:
                return "Batch production works while the bot is running."
            try:
                n = max(1, min(int(args.strip() or "1"), 14))
            except ValueError:
                return "Usage: /produce <episodes, 1-14> — renders that many queued topics back to back, each to review."
            return produce_batch(n)
        if command in ("pubok", "pubno", "resend", "simpub", "rerender", "promote", "postnow"):
            action = (run_actions or {}).get(command)
            if action is None:
                return "That button only works while the daemon is running."
            return action(db.resolve_run(args))
        if command == "tdone":
            cleared = 0
            if delete_message is not None:
                for mid in list(active_proposal_msg_ids):
                    try:
                        if delete_message(mid):
                            cleared += 1
                    except Exception:
                        pass
            active_proposal_msg_ids.clear()
            return f"✖ Topic proposals ended. Cleared {cleared} card(s) from chat." if cleared else "✖ Topic proposals ended."
        if command == "proposals":
            return handle("cprop", args)
        if command in ("propose", "cpropose"):
            started = running_job(db, "topic-research")
            if started:  # a second tap must not look like a failure, nor start a duplicate (and its cost)
                return (f"🔬 <b>Research is already running</b> (started {started}).\n\nIts result opens here "
                        "when it finishes, usually within a few minutes.",
                        [[{"text": "🧰 Jobs", "callback_data": "jobs:"}], [{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]])
            if not propose_topics:
                return "Topic proposals need the bot or daemon running with a script engine configured."
            # The research job now owns the panel ("⏳ Researching topic ideas", then its result): None tells
            # the bot not to draw over it with an empty "Done".
            return propose_topics() or None
        if command == "tadd":
            from . import topic_proposer
            topic_id = topic_proposer.add(db, args)
            return f"➕ Added <code>{html.escape(topic_id)}</code>. Open /images to fetch or upload its photos."
        if command == "tskip":
            from . import topic_proposer
            topic_proposer.skip(db, args)
            return f"✖ Skipped {html.escape(args)}."
        if command == "upd":
            if apply_updates is None:
                return "Updates can only be applied while the daemon is running."
            return apply_updates()
        if command in ("imgok", "imgcancel"):
            # Both end on the topic's image menu (where the review was opened), not on a dead card.
            ref, _, digest = args.rpartition("@") if "@" in args else (args, "", "")
            back = ([[{"text": "← Topic images", "callback_data": f"imagehelp:{ref}"}]] if ref else []) + [[HOME]]
            if not ref:
                return "This review is obsolete. Open /review again before approving.", back
            t_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            if command == "imgcancel":
                state = "approved" if load_credits(t_dir).get("reviewed") else "unapproved"
                return f"Storyboard review closed for <b>{html.escape(t_dir.name)}</b>. Set remains {state}.", back
            from .telegram_images import review_digest
            if not digest:
                return "This review is obsolete. Open /review again before approving.", back
            if digest != review_digest(t_dir):
                return "Images changed since this review. Open /review again; nothing was approved.", back
            if load_credits(t_dir).get("reviewed"):
                return f"ℹ️ Topic <b>{html.escape(t_dir.name)}</b> was already approved.", back
            return "✅ " + html.escape(approve_set(t_dir, MIN_SCENE_IMAGES)), back
        if command == "status":
            return status_text(db, next_run(), voice_quota())
        if command == "next":
            t = db.get_next_topic()
            serial = {x["id"]: i for i, x in enumerate(db.list_all_topics(), 1)}.get(t["id"], 1)
            return (f"🎯 <b>Next topic:</b> {serial}. {html.escape(t['title'])}",
                    [[{"text": "📋 Queue", "callback_data": "qrefresh:"}], [HOME]])
        if command == "pause":
            db.set_setting(PAUSED_KEY, "true")
            return "⏸ Scheduled runs and posts paused. Send /resume to re-enable."
        if command == "deploy":
            return request_deploy(db), [[HOME]]
        if command == "resume":
            db.set_setting(PAUSED_KEY, "false")
            return f"▶️ Scheduled runs and posts resumed. Next: {html.escape(next_run() or 'not scheduled')}"
        if command == "footage":
            # Footage is supplied as a file the operator holds the rights to (send the video with the
            # caption "/footage <topic>"). Links are not downloaded: a downloaded clip carries no licence
            # evidence, and downloading from YouTube conflicts with its Terms of Service.
            try:  # a ready-to-copy caption for the next topic (the copy button needs a concrete command)
                nxt = db.get_next_topic()
                example = str({t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}.get(nxt["id"], 1)) if nxt else "1"
            except Exception:
                example = "1"
            return ("🎥 <b>Add footage:</b> send the video file itself to this chat with this caption "
                    f"(the topic's number or ID): <code>/footage {example}</code>\n"
                    "<i>Links aren't downloaded: their rights can't be verified, and YouTube's terms prohibit downloading.</i>")
        if command == "queue":
            ref = args.strip().lower()
            q_text, q_buttons = render_queue_text_and_buttons(show_all=(ref in ("all", "full")))
            if send_message:
                send_message(q_text, q_buttons)
                return ""
            return q_text
        if command == "requeue":
            ref = args.strip()
            if not ref or ref.lower() in ("list", "all", "ls"):
                q_text, _ = render_queue_text_and_buttons(show_all=True)
                return q_text
            topics = db.list_all_topics()
            match = None
            if ref.isdigit():
                idx = int(ref) - 1
                if 0 <= idx < len(topics):
                    match = topics[idx]
            if not match:
                for t in topics:
                    if t["id"] == ref or t["id"].lower() == ref.lower():
                        match = t
                        break
                    if ref.isdigit() and t["id"].split("_")[0] == f"{int(ref):02d}":
                        match = t
                        break
                    if t["id"].split("_", 1)[-1] == ref:
                        match = t
                        break
            if not match:
                return f"⚠️ Topic '{html.escape(ref)}' not found in database. Send /queue for list."
            if not db.requeue_topic(match["id"]):
                return "This item cannot be requeued from its current state.", [[HOME]]
            return f"🔄 Topic <b>{html.escape(match['title'])}</b> (<code>{html.escape(match['id'])}</code>) restored to the queue."
        if command == "alerts":
            limit = 10
            if args.strip().isdigit():
                limit = min(50, max(1, int(args.strip())))
            alerts = db.list_recent_alerts(limit=limit)
            if not alerts:
                return "🔔 No alerts recorded in the database yet."
            lines = [f"🔔 <b>Alerts</b> · last {len(alerts)}", ""]
            for a in alerts:
                # Alerts are stored as the HTML that was sent: shown here as plain text (they were escaped a
                # second time, so their tags appeared on screen).
                plain = html.unescape(re.sub(r"<[^>]+>", "", a.get("text", ""))).strip()
                first, _, rest = plain.partition("\n")
                if re.sub(r"[^A-Za-z]", "", first).isupper():  # older alerts were headed in capitals
                    first = re.sub(r"^(\W*)(\w)", lambda m: m[1] + m[2].upper(), first.lower())  # "❌ Production failed"
                ts = show_time(a.get("created_at"), "%d %b %H:%M") if a.get("created_at") else ""
                note = " · removed from chat" if a.get("purged") else ""
                lines.append(f"• <b>{html.escape(clip_words(first, 90))}</b>\n   {ts}{note}")  # never mid-word
                body = re.sub(r"\n{2,}", "\n", rest).strip()
                if body:
                    lines.append(f"<blockquote expandable>{html.escape(clip_words(body, 600))}</blockquote>")
            return "\n".join(lines), [[HOME]]
        if command == "add":
            title = args.strip()
            if not title:
                return ("➕ <b>Add a topic</b>\n\nSend /add followed by the title, for example "
                        "<code>/add The Lost Labyrinth of Hawara</code>\n\n"
                        "<i>Its facts are researched and checked before it can be made.</i>",
                        [[{"text": "💡 Topic ideas", "callback_data": "cideas:"}], [HOME]])
            try:
                topic_id, u_tag, decomp = _add_topic_to_bank(db, title)
            except ValueError as e:
                return f"⚠️ {html.escape(str(e))}.\nTopic was not added."
            arch_lines = [f"  • <b>{arch_name.capitalize()}:</b> {html.escape(arch_q)}" for arch_name, arch_q in decomp.get("archetypes", {}).items()]
            arch_summary = "\n" + "\n".join(arch_lines) if arch_lines else ""
            return (f"➕ Added <b>{html.escape(title)}</b> (<code>{html.escape(topic_id)}</code>) to topic queue!{u_tag}\n"
                    f"🎬 <b>Documentary Shot Archetypes:</b>{arch_summary}\n\n"
                    f"Open <code>/images {html.escape(topic_id)}</code> to fetch, upload or review images.")
        if command in ("discover", "discoverpanel"):
            from .unesco import UNESCOEngine
            from . import topic_proposer
            engine = UNESCOEngine()
            bank = topic_proposer._bank()
            existing_titles = [t["title"] for t in bank.get("topics", [])]
            skipped = json.loads(db.get_setting("unesco_skipped", "[]"))
            existing_titles += [s.site for key, s in engine.load_sites().items() if str(key) in skipped]

            theme = None
            offset = 0
            if command == "discoverpanel":
                parts = args.split(":", 1)
                theme = parts[0].strip() or None
                if len(parts) > 1 and parts[1].strip().isdigit():
                    offset = int(parts[1].strip())
            else:
                parts = args.strip().split()
                if parts:
                    if not parts[0].isdigit():
                        theme = parts[0]
                        if len(parts) > 1 and parts[1].isdigit():
                            offset = int(parts[1])
                    else:
                        offset = int(parts[0])

            # One marvel per page, like Waiting ideas: its full text wraps in the message, and the buttons stay
            # short (three per page cut both the descriptions and the "Add <name>" buttons off).
            back = [[{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]]
            matches = engine.discover_engineering_wonders(limit=10_000, offset=0, sub_theme=theme, exclude_titles=existing_titles)
            if not matches:
                return (f"🏛️ No unqueued UNESCO engineering marvels found for '{html.escape(theme or 'all')}'. "
                        "Every matching site has been added or skipped."), back
            index = min(offset, len(matches) - 1)
            w = matches[index]
            about = (w.get("short_description") or "").strip()
            if len(about) > 900:
                about = about[:900].rsplit(" ", 1)[0] + "…"
            text = (f"🏛️ <b>UNESCO marvel {index + 1} of {len(matches)}</b>" + (f" · {html.escape(theme)}" if theme else "")
                    + f"\n\n<b>{html.escape(w['title'])}</b> · {html.escape(w['country'])}\n\n{collapsible(about)}")
            place = f"{w['id_number']}:{theme or ''}:{index}"
            buttons = [[{"text": "➕ Add", "callback_data": f"uadd:{place}"}, {"text": "✖ Skip", "callback_data": f"uskip:{place}"}]]
            buttons += [[{**b, "callback_data": f"cdisc:{theme or ''}:{b['callback_data'].rpartition(':')[2]}"} for b in row]
                        for row in pager("cdisc:", index, len(matches))]
            return text, buttons + back

        if command == "uadd":
            ref, _, place = args.strip().partition(":")  # place: "<theme>:<index>" when added from the panel
            if not ref.isdigit():
                return "Usage: uadd:&lt;unesco_id&gt;"
            from .unesco import UNESCOEngine
            engine = UNESCOEngine()
            sites = engine.load_sites()
            site = sites.get(int(ref))
            if not site:
                return f"⚠️ UNESCO site #{html.escape(ref)} not found in database."
            try:
                topic_id, u_tag, decomp = _add_topic_to_bank(db, site.site, u_site=site)
            except ValueError as e:
                err_str = str(e)
                if "Duplicate detected" in err_str:
                    return f"ℹ️ <b>{html.escape(site.site)}</b> is already queued in the topic bank."
                return f"⚠️ {html.escape(err_str)}"
            added = (f"➕ Added UNESCO Marvel <b>{html.escape(site.site)}</b> (<code>{html.escape(topic_id)}</code>) to queue!{u_tag}\n\n"
                     f"Open <code>/images {html.escape(topic_id)}</code> to fetch, upload or review images.")
            if place:  # from the panel: the next marvel moves into this place
                text, buttons = handle("discoverpanel", place)
                return added.split("\n\n")[0] + "\n\n" + text, buttons
            return added
        if command == "uskip":
            with db._conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM settings WHERE key='unesco_skipped'").fetchone()
                skipped = set(json.loads(row[0]) if row else [])
                site_id, _, page = args.strip().partition(":")
                skipped.add(site_id)
                conn.execute("INSERT OR REPLACE INTO settings (key,value,updated_at) VALUES ('unesco_skipped',?,?)", (json.dumps(sorted(skipped)), datetime.now(timezone.utc).isoformat()))
            if page:  # from the panel: show the same page again, without the skipped site
                return handle("discoverpanel", page)
            return f"Skipped UNESCO site #{html.escape(site_id)}."
        if command == "ask":
            from .probe import probe_system
            return probe_system(args.strip(), db=db, assets_dir=assets_dir)

        if command == "issue":
            raw_input = args.strip()
            if not raw_input:
                return ("🛠️ <b>Report a problem</b>\n\n"
                        "✍️ <b>Describe it now</b> in your next message (within 10 minutes), or send a screenshot with "
                        "the description as its caption. Name the run (its number on the card) if it's about one.\n\n"
                        "Example: <code>/issue run 12 the map label covers the pin</code>\n\n"
                        "<i>Your reports are listed in /improve.</i>")
            evidence_path = None
            critique = raw_input
            if "--evidence" in raw_input:
                crit_part, _, ev_part = raw_input.partition("--evidence")
                critique, evidence_path = crit_part.strip(), ev_part.strip()
            from .probe import resolve_run
            named = re.search(r"\brun[ _#]?(\d+)\b|\b(run_\w+)", critique, re.IGNORECASE)
            run = resolve_run(db, critique) if named else None
            run_id = run["run_id"] if run else None
            issue_id = "iss_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            db.record_issue(issue_id, critique, run_id=run_id, evidence_path=evidence_path)
            from . import improve
            klass = improve.triage_issue(db, issue_id, critique, run_id)
            from . import config as _config
            _config.ISSUES_DIR.mkdir(parents=True, exist_ok=True)
            (_config.ISSUES_DIR / f"{issue_id}.md").write_text(
                f"# {issue_id}\n\n- Created: {datetime.now(timezone.utc).isoformat()}\n- Run: {run_id or 'not named'}\n"
                f"- Class: {klass}\n- Evidence: {evidence_path or 'none'}\n- Status: open\n\n## Report\n{critique}\n",
                encoding="utf-8")
            recurring = next((r for r in improve.recurring(db, min_count=1) if r["klass"] == klass), None)
            similar = recurring["count"] - 1 if recurring else 0
            area = klass.partition(".")[2] or "other"
            return (f"✅ <b>Report saved</b> under <i>{html.escape(improve.AREA_NAMES.get(area, area))}</i>"
                    + (f" for {html.escape(db.run_label(run_id))}" if run_id else "")
                    + (" with your screenshot" if evidence_path else "") + ".\n\n"
                    + (f"{similar} similar report{'s' if similar != 1 else ''} in the last {improve.RECURRENCE_DAYS} days. " if similar else "")
                    + f"{len(db.list_open_issues())} open in total; /improve lists them.")

        if command == "improve":
            from . import improve
            text, buttons = improve.improve_text(db)
            if send_message:
                send_message(text, buttons + [[HOME]])
                return ""
            return text
        if command == "impinfo":
            from . import improve
            p = improve.get_proposal(db, args.strip())
            if not p:
                return "That proposal no longer exists."
            text, buttons = improve.card(p)
            if send_message:
                send_message(text, buttons)
                return ""
            return text
        if command in ("impok", "impno", "imprev"):
            from . import improve
            pid = args.strip()
            fn = {"impok": improve.adopt, "impno": improve.decline, "imprev": improve.revert}[command]
            return fn(db, pid, by="telegram")
        if command == "rsn":
            from . import improve
            key, _, reason = args.partition(":")
            run_id = db.resolve_run(key)
            appr = db.get_approval(run_id) or {}
            improve.record_reason(db, run_id, reason, appr.get("decision", ""))
            # The reason chips sit on the decided review card itself: this text replaces that card, so it
            # keeps what was decided.
            decided = (f"🔄 <b>Regeneration requested.</b> In the new version, "
                       f"{improve.REGENERATE_EFFECT.get(reason, 'it is logged for improvement')}. Its review replaces this card."
                       if appr.get("decision") == "regenerate"
                       else "❌ <b>Rejected.</b> Nothing was posted." if appr.get("decision") == "rejected" else "")
            return (f"🎬 <b>{html.escape(db.run_label(run_id))}</b>\n\n" + (f"{decided}\n\n" if decided else "")
                    + f"✅ Reason: <b>{html.escape(improve.REASONS.get(reason, reason))}</b>. "
                    f"Reply to this card to add details. [run {html.escape(key)}]")
        if command == "whytext":
            from . import improve
            key, _, text = args.partition(" ")
            run_id = db.resolve_run(key)
            prev = improve.operator_feedback(db, run_id) or {}
            improve.record_reason(db, run_id, prev.get("reason", "other"), prev.get("decision", ""), text.strip())
            # Said as it is: the new version reads these words at its script and picture steps, so only
            # the steps it has not reached yet can use them.
            return "📝 Thanks, noted" + (". Steps of the new version that haven't run yet use it."
                                         if prev.get("decision") == "regenerate" else ".")
        if command == "run":
            return trigger_run()
        return f"Unknown command /{html.escape(command)}. Send /help."
    original_handle = handle
    def guarded_handle(command, args):
        if command == "covlock" and ":" in args:
            from .publication_lock import publication_lock
            with publication_lock(db.db_path, args.partition(":")[0]):
                return original_handle(command, args)
        valid_mutation_args = (command not in {"drop","swap"} or (len(args.split()) >= (2 if command == "drop" else 3) and all(v.isdigit() for v in args.split()[1:])))
        if command in {"review", "imgok", "drop", "swap"} and args.strip() and valid_mutation_args:
            ref = args.split()[0].split("@",1)[0]
            directory = _resolve_topic_dir(ref, assets_dir, db=db)
            from .publication_lock import publication_lock
            with publication_lock(db.db_path, "topic-images:" + directory.name):
                with db._conn() as conn:
                    if conn.execute("SELECT 1 FROM runs WHERE topic_id=? AND status='producing'", (directory.name,)).fetchone():
                        raise ValueError("This topic is producing; images cannot be changed or approved")
                return original_handle(command, args)
        return original_handle(command, args)
    return guarded_handle
