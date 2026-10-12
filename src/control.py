"""
src/control.py
==============
Operations over Telegram: slash commands, the daily heartbeat, and the dead-man's-switch ping.

Commands (approvers only, in the configured chat):
  /status   paused?, next scheduled run, pending approvals, recent runs
  /next     the topic the next run will produce
  /pause    stop scheduled runs (an in-progress run finishes)
  /resume   re-enable scheduled runs
  /run      start a production run now (still needs your approval to publish)
  /images   image sets waiting for review
  /review N send topic N's numbered contact sheet with an Approve button
  /drop N 2 5  remove images 2 and 5 from topic N (re-run the fetcher to refill)
  /propose  research 3 new topic ideas, each with Add / Skip buttons
  /help     this list
"""

import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import requests

from .config import ASSETS_DIR, CLAUDE_MONTHLY_BUDGET_USD, HEALTHCHECK_PING_URL, MIN_SCENE_IMAGES, is_configured
from .credits import CONTACT_SHEET, CREDITS_FILE, approve_set, drop_images, load_credits
from .db import PipelineDatabase

log = logging.getLogger(__name__)

PAUSED_KEY = "scheduler_paused"
HELP = (
    "<b>Relic &amp; Riddle commands</b>\n"
    "/status — schedule, approvals, recent runs\n"
    "/next — next topic\n"
    "/pause — stop scheduled runs\n"
    "/resume — re-enable scheduled runs\n"
    "/run — start a run now (you still approve before anything posts)\n"
    "/images — image sets waiting for review\n"
    "/review N — contact sheet for topic N, with an Approve button\n"
    "/drop N 2 5 — remove images 2 and 5 from topic N\n"
    "/propose — research 3 new topic ideas (Add/Skip buttons)\n"
    "/help — this list"
)

SendPhoto = Callable[[Path, str, Optional[list]], bool]

# Plain-language names for run statuses in Telegram.
STATUS_WORDS = {
    "producing": "being made", "pending_approval": "waiting for you", "approved": "approved",
    "published": "published", "simulated": "test run (not posted)", "rejected": "rejected",
    "regenerate": "regenerated", "timeout": "no decision in time", "failed": "failed",
    "publish_unknown": "not sure it posted",
}


def _fetched_sets(assets_dir: Path) -> List[Path]:
    return sorted(d for d in assets_dir.iterdir() if d.is_dir() and (d / CREDITS_FILE).exists())


def _resolve_topic_dir(ref: str, assets_dir: Path) -> Path:
    """'2', '02' or a full topic id -> that topic's asset folder."""
    ref = ref.strip()
    if not ref:
        raise ValueError("Give a topic number, e.g. /review 3")
    matches = [d for d in _fetched_sets(assets_dir)
               if d.name == ref or (ref.isdigit() and d.name.split("_")[0] == f"{int(ref):02d}")]
    if len(matches) != 1:
        raise ValueError(f"No fetched image set for topic '{html.escape(ref)}'. Send /images for the list.")
    return matches[0]


def images_text(assets_dir: Path = ASSETS_DIR) -> str:
    review, short, approved = [], [], 0
    for d in _fetched_sets(assets_dir):
        cr = load_credits(d)
        n = len(cr["images"])
        label = f"{d.name.split('_')[0]} — {html.escape(d.name.split('_', 1)[1].replace('_', ' '))} ({n})"
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
        lines += [f"Short of {MIN_SCENE_IMAGES} images (add your own or re-run the fetcher):"] + [f"  • {x}" for x in short]
    return "\n".join(lines)


def review_caption(topic_dir: Path) -> str:
    cr = load_credits(topic_dir)
    lines = [f"<b>{html.escape(topic_dir.name)}</b> — {len(cr['images'])} images"
             f"{' (approved)' if cr['reviewed'] else ''}"]
    for i, c in enumerate(cr["images"], 1):
        title = c.get("title", c["file"]).removeprefix("File:")
        lines.append(f"{i}. {html.escape(title[:48])} · {html.escape(c.get('license', ''))}")
    lines.append(f"Wrong ones? /drop {topic_dir.name.split('_')[0]} 2 5")
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


def quota_lines(voice_quota: Optional[dict]) -> List[str]:
    """Voice quota summary, with a warning when fewer than ~3 scripts' worth remain."""
    if not voice_quota or not voice_quota.get("limit"):
        return []
    left = max(0, voice_quota["limit"] - voice_quota["used"])
    reset = ""
    if voice_quota.get("resets"):
        reset = ", resets " + datetime.fromtimestamp(int(voice_quota["resets"]), timezone.utc).strftime("%d %b")
    line = (f"ElevenLabs ({html.escape(str(voice_quota.get('tier')))}): {left:,} of {voice_quota['limit']:,} "
            f"characters left (~{left // SCRIPT_CHARS} Reels){reset}")
    if left < 3 * SCRIPT_CHARS:
        return [f"⚠️ {line}. Runs will fall back to the free edge-tts voice when it runs out."]
    return [line]


def status_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    e = html.escape
    lines = [
        f"<b>Status</b>: {'⏸ paused' if is_paused(db) else '▶️ active'}",
        f"Next scheduled run: {e(next_run or 'not scheduled')}",
    ]
    pending = db.list_pending_approvals()
    lines.append(f"Awaiting approval: {len(pending)}")
    for p in pending[:3]:
        lines.append(f"  • {e(db.run_label(p['run_id']))}")
    recent = db.list_recent_runs(5)
    if recent:
        lines.append("Recent runs:")
        for r in recent:
            lines.append(f"  • {e(STATUS_WORDS.get(r['status'], r['status']))} — {e(db.run_label(r['run_id']))}")
    topics = db.list_all_topics()
    lines.append(f"Topics: {sum(1 for t in topics if t['times_published'])} published / {len(topics)}")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    spend = f"Claude spend this month: ${db.llm_spend(month):.2f}"
    if CLAUDE_MONTHLY_BUDGET_USD > 0:
        spend += f" of ${CLAUDE_MONTHLY_BUDGET_USD:.2f} budget"
    lines.append(spend)
    lines += quota_lines(voice_quota)
    return "\n".join(lines)


def heartbeat_text(db: PipelineDatabase, next_run: Optional[str], voice_quota: Optional[dict] = None) -> str:
    return "💓 <b>Daily heartbeat</b> — the daemon is running.\n" + status_text(db, next_run, voice_quota)


def make_command_handler(db: PipelineDatabase, next_run: Callable[[], Optional[str]],
                         trigger_run: Callable[[], str], send_photo: Optional[SendPhoto] = None,
                         assets_dir: Path = ASSETS_DIR,
                         voice_quota: Callable[[], Optional[dict]] = lambda: None,
                         apply_updates: Optional[Callable[[], str]] = None,
                         propose_topics: Optional[Callable[[], str]] = None,
                         run_actions: Optional[dict] = None) -> Callable[[str, str], str]:
    """run_actions: button actions on a run, e.g. {"pubok": fn(run_id) -> reply, "pubno": ..., "resend": ...}."""
    def send_sheet(topic_dir: Path) -> bool:
        if send_photo is None:
            return False
        key = topic_dir.name
        return send_photo(topic_dir / CONTACT_SHEET, review_caption(topic_dir),
                          [{"text": "✅ Approve set", "callback_data": f"imgok:{key}"}])

    def handle(command: str, args: str) -> str:
        if command in ("help", "start"):
            return HELP
        if command == "images":
            return images_text(assets_dir)
        if command == "review":
            topic_dir = _resolve_topic_dir(args, assets_dir)
            return "Contact sheet sent above." if send_sheet(topic_dir) else review_caption(topic_dir)
        if command == "drop":
            ref, *nums = args.split()
            if not nums or not all(n.isdigit() for n in nums):
                return "Usage: /drop N 2 5 (topic number, then image numbers)"
            topic_dir = _resolve_topic_dir(ref, assets_dir)
            msg = drop_images(topic_dir, [int(n) for n in nums])
            if load_credits(topic_dir)["images"]:
                send_sheet(topic_dir)
            return html.escape(msg)
        if command in ("pubok", "pubno", "resend"):
            action = (run_actions or {}).get(command)
            if action is None:
                return "That button only works while the daemon is running."
            return action(db.resolve_run(args))
        if command == "propose":
            return propose_topics() if propose_topics else "Topic proposals need the bot or daemon running with Claude configured."
        if command == "tadd":
            from . import topic_proposer
            topic_id = topic_proposer.add(db, args)
            return (f"➕ Added <code>{html.escape(topic_id)}</code> to the topic bank. Fetch its images with "
                    f"<code>python scripts/fetch_commons_images.py --topic-id {html.escape(topic_id)}</code>, then /images.")
        if command == "tskip":
            from . import topic_proposer
            topic_proposer.skip(db, args)
            return f"✖ Skipped {html.escape(args)}."
        if command == "upd":
            if apply_updates is None:
                return "Updates can only be applied while the daemon is running."
            return apply_updates()
        if command == "imgok":
            return "✅ " + html.escape(approve_set(_resolve_topic_dir(args, assets_dir), MIN_SCENE_IMAGES))
        if command == "status":
            return status_text(db, next_run(), voice_quota())
        if command == "next":
            t = db.get_next_topic()
            return f"Next topic: <b>{html.escape(t['title'])}</b> (<code>{html.escape(t['id'])}</code>)"
        if command == "pause":
            db.set_setting(PAUSED_KEY, "true")
            return "⏸ Scheduled runs paused. Send /resume to re-enable."
        if command == "resume":
            db.set_setting(PAUSED_KEY, "false")
            return f"▶️ Scheduled runs resumed. Next: {html.escape(next_run() or 'not scheduled')}"
        if command == "run":
            return trigger_run()
        return f"Unknown command /{html.escape(command)}. Send /help."
    return handle
