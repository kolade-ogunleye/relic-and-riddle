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
  /images   image sets waiting for review
  /review N send topic N's numbered contact sheet with an Approve button
  /drop N 2 5  remove images 2 and 5 from topic N (re-run the fetcher to refill)
  /propose  research 3 new topic ideas, each with Add / Skip buttons
  /help     this list
"""

import html
import json
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
        "location": {"name": title, "country": loc_country, "lat": loc_lat, "lon": loc_lon},
        "key_facts": [
            f"Documented historical site: {title}",
            "Architectural or archaeological significance recognized by researchers.",
            "Subject of ongoing academic and field study."
        ],
        "meta_categories": ["History & Philosophy", "Travel & Culture"],
        "hashtags": [f"#{slug[:20].replace('_', '')}", "#ancienthistory", "#archaeology", "#relicandriddle"],
        "sources": [{"title": title, "url": f"https://en.wikipedia.org/wiki/{slug}"}]
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

    drop_ref = ref_hint if ref_hint else (topic_dir.name.split('_')[0] if topic_dir.name.split('_')[0].isdigit() else topic_dir.name)
    lines.append(f"\nWrong ones? /drop {drop_ref} 2 5")
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
                lines.append("🚨 <b>Voice Alert:</b> Azure Speech has auth errors and ElevenLabs is unconfigured. " + _unlicensed_consequence())
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
            lines.append("🚨 <b>Licence Alert:</b> both ElevenLabs and Azure Speech are unavailable. " + _unlicensed_consequence())

    return lines


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
    lines = [f"Paid model fallback this month: light ${light:.2f} of ${config.PAID_FALLBACK_LIGHT_MONTHLY_USD:.2f}, "
             f"heavy ${heavy:.2f} of ${config.PAID_FALLBACK_HEAVY_MONTHLY_USD:.2f}"]
    claude = sum(c for task, c in by_task.items() if not task.startswith(("paid-fallback", "groq")))
    if claude or config.SCRIPT_ENGINE == "claude":
        lines.append(f"Claude this month: ${claude:.2f}" + (f" of ${CLAUDE_MONTHLY_BUDGET_USD:.2f}" if CLAUDE_MONTHLY_BUDGET_USD > 0 else ""))
    media = db.media_spend(month)
    lines.append(f"AI motion this month: ${media:.2f} of ${MEDIA_MONTHLY_BUDGET_USD:.2f}")
    return lines


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
             "rejected": "✖", "approved": "👍", "scheduled": "🗓", "cancelled": "⏹"}
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
    lines += [f"• {line.replace(' this month', '').replace('Paid model fallback', 'AI model fallback')}"
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
    if waiting_proposals:
        needs.append(f"🧭 <b>{len(waiting_proposals)} improvement proposal(s)</b> — /improve")
    if account_problems:
        needs.append("⚠️ " + e(account_problems[0]))
    if queued and ready < len(queued) and ready < 3:
        needs.append(f"🖼 Only {ready} queued topic(s) have photos ready")
    recent = db.list_recent_runs(limit=1)
    if recent and recent[0].get("status") == "failed":
        needs.append(f"❌ Last run failed: {e(short_reason(recent[0].get('error') or ''))}")

    active = db.get_active_progress() if hasattr(db, "get_active_progress") else None
    from . import config
    bot_id = config.TELEGRAM_BOT_TOKEN.split(":", 1)[0]
    environment = {"8701645452": "DEV", "8837268021": "PRODUCTION"}.get(bot_id, "IDENTITY UNVERIFIED")
    lines = [f"🏛️ <b><u>RELIC &amp; RIDDLE</u></b> · {environment}", ""]
    lines += (["<b>Needs you</b>"] + needs + [""]) if needs else ["✅ <b>Nothing needs you right now.</b>", ""]
    lines.append(f"⏯️ Schedule: {'⏸ <b>paused</b>' if paused else '▶️ active'}"
                 + (f" · next run {e(next_run)}" if next_run and not paused else ""))
    if active:
        label = "Making" if active.get("worker_state") == "running" else "Last recorded run (worker not active/verified)"
        lines.append(f"⏳ {label}: {e(db.run_label(active['run_id']))} · step {active.get('step', 1)}/{active.get('total_steps', 5)}")
    lines.append(f"📋 Queue: {len(queued)} topics · {ready} with photos ready"
                 + (f" · next: <b>{e(next_t['title'])}</b>" if next_t else ""))
    lines += spend_lines(db)

    row1 = [{"text": f"🎬 Reviews ({len(pending)})", "callback_data": "creviews:"}] if pending else []
    if waiting_proposals:
        row1.append({"text": f"🧭 Proposals ({len(waiting_proposals)})", "callback_data": "cimprove:"})
    buttons = [row1] if row1 else []
    buttons += [
        [{"text": "⚡ Progress" if active else (f"⚡ Make #{next((i for i,t in enumerate(topics,1) if next_t and t['id']==next_t['id']),1)} now"),
          "callback_data": "cprog:" if active else (f"qrender:{next_t['id']}" if next_t else "qrender:1")},
         {"text": "📋 Queue", "callback_data": "qrefresh:"}],
        [{"text": "💡 Topic ideas", "callback_data": "cideas:"}, {"text": "🎨 Covers", "callback_data": "ccov:"}],
        [{"text": "🎵 Music", "callback_data": "cmusic:"}, {"text": "📊 Full status", "callback_data": "cstat:"}],
        [{"text": "🖼 Images", "callback_data": "cimages:"}, {"text": "🔄 Check accounts", "callback_data": "caccounts:"}],
        [{"text": "🧰 Jobs", "callback_data": "jobs:"}, {"text": "Help", "callback_data": "chelp:Everyday"}],
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


def make_command_handler(db: PipelineDatabase, next_run: Callable[[], Optional[str]],
                         trigger_run: Callable[[], str], send_photo: Optional[SendPhoto] = None,
                         assets_dir: Path = ASSETS_DIR,
                         voice_quota: Callable[[], Optional[dict]] = lambda: None,
                         apply_updates: Optional[Callable[[], str]] = None,
                         propose_topics: Optional[Callable[[], str]] = None,
                         run_actions: Optional[dict] = None,
                         send_audio: Optional[SendPhoto] = None,
                         send_message: Optional[Callable[[str, Optional[list]], Any]] = None,
                         delete_message: Optional[Callable[[int], bool]] = None) -> Callable[[str, str], str]:
    active_proposal_msg_ids: List[int] = []
    undo_stack: List[Dict[str, Any]] = []

    def render_queue_text_and_buttons(show_all: bool = False, page: int = 0) -> Tuple[str, List[Dict[str, str]]]:
        def _readiness(topic_id: str) -> str:
            try:
                td = _resolve_topic_dir(topic_id, assets_dir, db=db)
                if td.exists():
                    cr = load_credits(td)
                    imgs = cr.get("images", [])
                    if cr.get("reviewed") and len(imgs) >= MIN_SCENE_IMAGES:
                        return "🟢 Ready to Render"
                    elif imgs:
                        return "🟡 Needs Image Review"
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
        lines = [f"📋 <b><u>TOPIC QUEUE</u></b> (<i>{len(queued)} queued</i>)", ""]
        limit = 5
        page = max(0, min(page, max(0, (len(queued)-2)//limit)))
        lines[0] += f" · page {page+1}/{max(1, (max(0,len(queued)-1)+limit-1)//limit)}"

        buttons = []
        if next_t and next_t.get("status") == "queued":
            sn = topic_serial.get(next_t['id'], 1)
            p_badge = " [🚀 Priority #1]" if next_t.get("priority", 0) > 0 else ""
            r_badge = _readiness(next_t['id'])
            lines.append(f"🎯 <b>Next in line:</b> {sn}. {html.escape(next_t['title'])}{p_badge}\n    Status: <b>{r_badge}</b> (<code>{html.escape(next_t['id'])}</code>)\n")
            buttons.append({"text": f"⚡ Render #{sn} Now", "callback_data": f"qrender:{next_t['id']}"})
            remaining = [t for t in queued if t["id"] != next_t["id"]]
            if remaining:
                lines.append("<b>Other Queued Topics:</b>")
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
            buttons.append({"text": f"🗑 Drop #{sn}", "callback_data": f"qdrop:{next_t['id']}"})
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
            lines.append(f"\n<b>Eligible to Requeue ({len(requeueable)}):</b>")
            for t in requeueable[:5]:
                s = topic_serial.get(t['id'], '')
                st = t.get("status", "")
                lines.append(f"  {s}. {html.escape(t['title'])} [{st}] (<code>{html.escape(t['id'])}</code>)")
            lines.append("\n<i>To restore any topic: <code>/requeue &lt;number or id&gt;</code></i>")

        pages = max(1, (max(0, len(queued) - 1) + limit - 1) // limit)
        # Rows, not a flat list: Render beside Refresh, Bump beside Drop, every page button on one row.
        render = [b for b in buttons if b["callback_data"].startswith("qrender:")]
        changes = [b for b in buttons if b["callback_data"].startswith(("qbump:", "qdrop:"))]
        rows = [render + [{"text": "🔄 Refresh Queue", "callback_data": "qrefresh:"}]]
        if changes:
            rows.append(changes)
        return "\n".join(lines), rows + pager("qpage:", page, pages) + [[HOME]]

    """run_actions: button actions on a run, e.g. {"pubok": fn(run_id) -> reply, "pubno": ..., "resend": ...}."""
    def send_sheet(topic_dir: Path) -> bool:
        if send_photo is None:
            return False
        key = topic_dir.name
        caption = review_caption(topic_dir)
        write_contact_sheet(topic_dir, load_credits(topic_dir)["images"])
        from .telegram_images import review_digest
        key += "@" + review_digest(topic_dir)
        return send_photo(topic_dir / CONTACT_SHEET, caption,
                          [{"text": "🎬 Approve Storyboard", "callback_data": f"imgok:{key}"},
                           {"text": "✖ Cancel", "callback_data": f"imgcancel:{key}"}])

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
        if command == "qrender":
            current = db.get_next_topic()
            serials = {str(i): t["id"] for i,t in enumerate(db.list_all_topics(), 1)}
            requested = serials.get(args, args)
            if not current or current["id"] != requested:
                text, buttons = render_queue_text_and_buttons()
                return "⚠️ The queue changed. Review the current next topic before starting.\n\n" + text, buttons
            res = trigger_run()
            q_text, q_buttons = render_queue_text_and_buttons()
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
        if command == "bump":
            ref = args.strip()
            if not ref:
                queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
                if len(queued) > 1:
                    topic_serial = {t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}
                    buttons = [
                        {"text": f"⬆ Bump #{topic_serial.get(t['id'], i)} {t['title'][:20]}", "callback_data": f"qbump:{t['id']}"}
                        for i, t in enumerate(queued[1:6], 2)
                    ]
                    if send_message:
                        send_message(
                            "⬆ <b><u>BUMP TOPIC</u></b>\n\nTap a topic below or copy & run: <code>/bump 2</code>",
                            buttons
                        )
                        return ""
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
                return "Usage: /drop_topic &lt;number or topic_id&gt;"
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
            ref = args.strip()
            run_dir = None
            if ref:
                from .config import OUTPUT_DIR
                run = db.get_run(db.resolve_run(ref))
                if run and run.get("master_video_path"):
                    run_dir = Path(run["master_video_path"]).resolve().parent
                    if not run_dir.is_relative_to(OUTPUT_DIR.resolve()):
                        raise ValueError("Run output lies outside the configured output root")
                else:
                    topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
                    runs = [p for p in OUTPUT_DIR.glob(f"run_{topic_dir.name}_*") if p.is_dir()]
                    run_dir = max(runs, key=lambda p: p.stat().st_mtime) if runs else None
                if not run_dir:
                    return "No rendered run found for this reference."
            else:
                all_runs = sorted(
                    [p for p in config.OUTPUT_DIR.glob("run_*") if p.is_dir() and not p.name.startswith("_")],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                if all_runs:
                    run_dir = all_runs[0]
                else:
                    return "ℹ️ <b>Cover Studio:</b> No rendered runs found in output. Produce a run first with /run, or specify: <code>/covers &lt;topic_id&gt;</code>." 
            var_a = run_dir / "thumbnail_variant_a_blueprint.jpg"
            var_b = run_dir / "thumbnail_variant_b_intrigue.jpg"
            var_c = run_dir / "thumbnail_variant_c_minimal.jpg"
            if not (var_a.exists() or var_b.exists() or var_c.exists()):
                return f"⚠️ No thumbnail variant suite found in <code>{html.escape(run_dir.name)}</code>."
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
                [
                    {"text": f"🔒 Lock Variant {selected.upper()}", "callback_data": f"covlock:{run_dir.name}:{selected}"}
                ],
                # Opened from a review card it is a separate message under the card: Close returns to the card.
                [{"text": "✖ Close", "callback_data": "close:"} if ref else HOME]
            ]
            caption = (
                f"🎨 <b>Thumbnail Cover Studio</b> • <code>{html.escape(run_dir.name)}</code>\n\n"
                f"Previewing: <b>Variant {selected.upper()}</b>\n"
                f"Tap A, B, or C to flip preview in-place, or Lock to finalize:"
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
                    header += f" (<i>{html.escape(act)}</i>)"

                if len(raw_pl) <= 35 and "\n" not in raw_pl:
                    lines.append(f"{header} → <code>{html.escape(raw_pl)}</code>")
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
                dash_text, _ = dashboard_text_and_buttons(db, next_run(), assets_dir=assets_dir)
                return (
                    "ℹ️ <b>No Production Running:</b> Pipeline is currently idle.\n\n"
                    "Send /run to start production on the next queued topic.\n\n"
                    + dash_text
                )
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
                return (f"⚠️ <b>Last recorded progress — {worker_state}</b>\n\n{html.escape(note)}\n"
                        f"{html.escape(topic_title)} · step {step}/{total_steps}\n"
                        f"Last update: {html.escape(active_prog.get('updated_at', 'unknown'))}\n"
                        "This is not confirmation of an active run. Inspect the worker before retrying.")

            milestones = [
                "Verifying script & historical fact sheets",
                "Mastering narration voiceover & acoustics",
                "Synthesizing original graphics & motion",
                "Compositing video, parallax & karaoke subs",
                "Packaging review & multiformat stills",
            ]
            lines = [
                mode_text,
                f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n<b>{html.escape(topic_title)}</b> • <code>[{html.escape(db.run_label(run_id))}]</code>",
                "",
            ]
            for idx, m in enumerate(milestones, start=1):
                if idx < step:
                    lines.append(f"✅ <b>Step {idx}/{total_steps}:</b> {m}")
                elif idx == step:
                    detail_str = f" <i>({html.escape(details)})</i>" if details else ""
                    lines.append(f"🔄 <b>Step {idx}/{total_steps}:</b> {m}...{detail_str}")
                else:
                    lines.append(f"◽ <b>Step {idx}/{total_steps}:</b> {m}")

            bar_len = 10
            filled = max(1, min(bar_len, int((step / total_steps) * bar_len)))
            bar = "▰" * filled + "▱" * (bar_len - filled)
            pct = int((step / total_steps) * 100)

            import time
            now = time.time()
            start_t = active_prog.get("start_time", now)
            elapsed_sec = int(max(0, now - start_t))
            elapsed_fmt = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
            if 0 < step < total_steps:
                est_total = elapsed_sec / (step / total_steps)
                rem_sec = max(0, int(est_total - elapsed_sec))
                eta_fmt = f"~{rem_sec // 60:02d}:{rem_sec % 60:02d}"
            else:
                eta_fmt = "completing..." if step == total_steps else "~01:00"

            lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
            lines.append(f"⏱ <b>Elapsed:</b> {elapsed_fmt} • <b>ETA:</b> {eta_fmt}")
            lines.append(f"Last stage update: {html.escape(active_prog.get('updated_at', 'unknown'))}")
            return "\n".join(lines)
        if command in ("ideas", "cideas"):
            text = ("💡 <b>Topic ideas</b>\n\nResearch new ones, browse UNESCO marvels, or decide on ideas "
                    "already waiting (each has Add/Skip).")
            started = running_job(db, "topic-research")
            research = ({"text": "🧰 Research running: Jobs", "callback_data": "jobs:"} if started else
                        {"text": "🔬 Research 3 new ideas", "callback_data": "cpropose:"})
            if started:
                text += f"\n\n🔬 <b>Research is running</b> (started {started}). Its result arrives as a message; keep browsing meanwhile."
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
            return ("✏️ <b>Edit text</b> — reply to this message with the new wording. Start with <code>title:</code>, "
                    "<code>first comment:</code> or <code>carousel caption:</code> to change those; otherwise it replaces "
                    "the caption. The fact check runs again on it before the card updates. On-screen text and narration "
                    "can't be edited here (they are rendered); use Regenerate for those.\n\n"
                    f"<b>Current title:</b> {html.escape(payload.get('title', ''))}\n"
                    f"<b>Current caption:</b>\n<blockquote expandable>{html.escape(payload.get('caption', ''))}</blockquote>\n"
                    f"[edit run {html.escape(args.strip())}]")
        if command == "edittext":
            key, _, text = args.partition(" ")
            fields = {"title": "title", "first comment": "first_comment", "carousel caption": "carousel_caption",
                      "caption": "caption"}
            field, body = "caption", text.strip()
            m = re.match(r"^(title|first comment|carousel caption|caption)\s*:\s*(.+)$", body, re.IGNORECASE | re.DOTALL)
            if m:
                field, body = fields[m.group(1).lower()], m.group(2).strip()
            action = (run_actions or {}).get("edit")
            if action is None:
                return "Editing works while the bot or daemon is running."
            return action(db.resolve_run(key), field, body)
        if command == "cimprove":
            from . import improve
            text, buttons = improve.improve_text(db)
            return text, buttons + [[HOME]]
        if command == "creviews":
            pending = db.list_pending_approvals()
            if not pending:
                return "No reviews are waiting."
            lines = ["🎬 <b>Reviews waiting</b> (the review card is in the chat above; Resend brings it to the bottom):"]
            buttons = []
            for p in pending[:5]:
                lines.append(f"• {html.escape(db.run_label(p['run_id']))}")
                buttons.append([{"text": f"📨 Resend {db.run_key(p['run_id'])}", "callback_data": f"resend:{db.run_key(p['run_id'])}"}])
            return "\n".join(lines), buttons + [[HOME]]
        if command == "cstat":
            return status_text(db, next_run(), voice_quota()), [[HOME]]
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
                return f"🎵 <b>Fetch new music</b>\n\nChoose a documentary mood. Fetches up to {config.MUSIC_FETCH_PER_MOOD} new CC0/CC BY tracks per mood; no paid generation. Each one waits for your review.", [[{"text": mood.title(), "callback_data": f"mfetch:{mood}"}] for mood in music.MOODS] + [[{"text": "All moods", "callback_data": "mfetch:all"}], [{"text": "← Music", "callback_data": "musicmenu:"}], [HOME]]
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
                    [[{"text": "Fetch more", "callback_data": f"fetchimages:{args}"}, {"text": "Upload my image", "callback_data": f"upload:{args}"}],
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
        if command == "review":
            ref = args.strip()
            if not ref:
                queued = [t for t in db.list_all_topics() if t.get("status") == "queued"]
                topic_serial = {t["id"]: i for i, t in enumerate(db.list_all_topics(), 1)}
                buttons = []
                for t in queued[:5]:
                    try:
                        td = _resolve_topic_dir(t["id"], assets_dir, db=db)
                        if td.exists():
                            cr = load_credits(td)
                            if cr.get("images"):
                                sn = topic_serial.get(t["id"], 1)
                                buttons.append({"text": f"🎬 Review #{sn} {t['title'][:20]}", "callback_data": f"review:{sn}"})
                    except Exception:
                        pass
                if buttons and send_message:
                    send_message(
                        "🎬 <b><u>STORYBOARD REVIEW</u></b>\n\nTap a topic below or copy & run: <code>/review 1</code>",
                        buttons
                    )
                    return ""
                example_sn = "1"
                try:
                    nxt = db.get_next_topic()
                    if nxt:
                        all_t = db.list_all_topics()
                        example_sn = str({t["id"]: i for i, t in enumerate(all_t, 1)}.get(nxt["id"], 1))
                except Exception:
                    pass
                return (
                    f"🎬 <b><u>STORYBOARD REVIEW</u></b>\n\nTap to copy & edit: <code>/review {example_sn}</code>\n\n"
                    "<i>Usage: <code>/review &lt;topic_number_or_id&gt;</code>\n"
                    "Tip: Tap the code box above to copy instantly into your input box.</i>"
                )
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
                return (
                    f"🗑 <b><u>DROP IMAGE</u></b>\n\nTap to copy & edit: <code>/drop {example_num} 2 5</code>\n\n"
                    "<i>Usage: <code>/drop &lt;topic#&gt; &lt;image_numbers...&gt;</code>\n"
                    "Tip: Tap the code box above to copy instantly into your input box.</i>"
                )
            ref, nums = parts[0], parts[1:]
            topic_dir = _resolve_topic_dir(ref, assets_dir, db=db)
            msg = drop_images(topic_dir, [int(n) for n in nums])
            if load_credits(topic_dir)["images"]:
                send_sheet(topic_dir)
            return html.escape(msg)
        if command == "swap":
            parts = args.split()
            if len(parts) < 3 or not (parts[1].isdigit() and parts[2].isdigit()):
                return (
                    "🔄 <b><u>SWAP BEATS</u></b>\n\nTap to copy & edit: <code>/swap 1 2 4</code>\n\n"
                    "<i>Usage: <code>/swap &lt;topic#&gt; &lt;beat_1&gt; &lt;beat_2&gt;</code>\n"
                    "Tip: Tap the code box above to copy instantly into your input box.</i>"
                )
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
            elif command == "mcancel":
                return f"✖ Music review cancelled for <i>{html.escape(args)}</i>. Track remains pending."
            pending = music.pending_tracks()
            if not pending:
                approved = sum(1 for t in music.load_library() if t.get("reviewed"))
                return (reply + "\n" if reply else "") + (
                    f"🎵 No tracks waiting for review ({approved} approved). Open Music → Find new tracks.")

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
            if len(pending) > 1:
                buttons.append([
                    {"text": "⏭ Next Track", "callback_data": f"mnext:{key}"},
                    {"text": "✖ Cancel", "callback_data": f"mcancel:{key}"},
                ])
            else:
                buttons.append([{"text": "✖ Cancel", "callback_data": f"mcancel:{key}"}])
            buttons.append([HOME])

            if send_audio is None or not send_audio(music.MUSIC_DIR / nxt["file"], (reply + "\n\n" if reply else "") + music.review_caption(nxt, len(pending)), buttons):
                return (reply + "\n" if reply else "") + music.review_caption(nxt, len(pending))
            return reply or ""
        if command in ("pubok", "pubno", "resend", "simpub"):
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
                return (f"🔬 <b>Research is already running</b> (started {started}).\n\nIts result arrives as a message "
                        "when it finishes, usually within a few minutes.",
                        [[{"text": "🧰 Jobs", "callback_data": "jobs:"}], [{"text": "← Topic ideas", "callback_data": "cideas:"}], [HOME]])
            return propose_topics() if propose_topics else "Topic proposals need the bot or daemon running with Claude configured."
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
            return f"Next topic: <b>{html.escape(t['title'])}</b> (<code>{html.escape(t['id'])}</code>)"
        if command == "pause":
            db.set_setting(PAUSED_KEY, "true")
            return "⏸ Scheduled runs and posts paused. Send /resume to re-enable."
        if command == "resume":
            db.set_setting(PAUSED_KEY, "false")
            return f"▶️ Scheduled runs and posts resumed. Next: {html.escape(next_run() or 'not scheduled')}"
        if command == "footage":
            # Footage is supplied as a file the operator holds the rights to (send the video with the
            # caption "/footage <topic>"). Links are not downloaded: a downloaded clip carries no licence
            # evidence, and downloading from YouTube conflicts with its Terms of Service.
            return ("🎥 <b>Add footage:</b> send the video file itself to this chat with the caption "
                    "<code>/footage &lt;topic_id_or_number&gt;</code>.\n"
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
            lines = [f"🔔 <b><u>SYSTEM ALERTS</u></b> (<i>last {len(alerts)}</i>):", ""]
            for a in alerts:
                purged_tag = " [purged from chat]" if a.get("purged") else ""
                kind = a.get("kind", "persistent")
                full_text = a.get("text", "").strip()
                clean_first = re.sub(r"<[^>]+>", "", full_text.splitlines()[0]) if full_text else ""
                ts = show_time(a.get("created_at"), "%Y-%m-%d %H:%M:%S") if a.get("created_at") else ""
                if len(full_text) <= 80 and "\n" not in full_text:
                    lines.append(f"• <code>{ts}</code> [<i>{kind}</i>{purged_tag}]: {html.escape(html.unescape(clean_first))}")
                else:
                    header = html.escape(html.unescape(clean_first[:80]))
                    lines.append(f"• <code>{ts}</code> [<i>{kind}</i>{purged_tag}]: <b>{header}</b>\n<blockquote expandable>{html.escape(full_text)}</blockquote>")
            return "\n".join(lines)
        if command == "add":
            title = args.strip()
            if not title:
                return (
                    "➕ <b><u>ADD TOPIC</u></b>\n\nTap to copy & edit: <code>/add The Lost Labyrinth of Hawara</code>\n\n"
                    "<i>Usage: <code>/add &lt;topic title&gt;</code>\n"
                    "Tip: Tap the code box above to copy, enter your title, and send!</i>"
                )
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
                    + f"\n\n<b>{html.escape(w['title'])}</b> · {html.escape(w['country'])}\n\n{html.escape(about)}")
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
                return ("🛠️ <b>Report a problem</b>\n\n<code>/issue run 12 the map label covers the pin</code>\n\n"
                        "<i>Name the run (its number on the card) if it's about one; attach a photo or clip with "
                        "/issue as its caption. It goes into the improvement ledger with that run's records.</i>")
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
            seen = f" Seen {recurring['count']}× in {improve.RECURRENCE_DAYS} days." if recurring and recurring["count"] > 1 else ""
            return (f"🛠️ <b>Logged</b> as <code>{html.escape(klass)}</code>"
                    + (f" for {html.escape(db.run_label(run_id))}" if run_id else "") + f".{seen}\n"
                    + (f"Evidence attached: <code>{html.escape(Path(evidence_path).name)}</code>\n" if evidence_path else "")
                    + "<i>It counts toward an improvement proposal once it recurs; /improve shows what's pending.</i>")

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
            return (f"✅ Reason: <b>{html.escape(improve.REASONS.get(reason, reason))}</b>. "
                    f"Reply to this message to add details. [run {html.escape(key)}]")
        if command == "whytext":
            from . import improve
            key, _, text = args.partition(" ")
            run_id = db.resolve_run(key)
            prev = improve.operator_feedback(db, run_id) or {}
            improve.record_reason(db, run_id, prev.get("reason", "other"), prev.get("decision", ""), text.strip())
            return "📝 Thanks — noted" + (" and passed to the new version." if prev.get("decision") == "regenerate" else ".")
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
