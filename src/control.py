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
    "/help — this list"
)

SendPhoto = Callable[[Path, str, Optional[list]], bool]


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


def status_text(db: PipelineDatabase, next_run: Optional[str]) -> str:
    e = html.escape
    lines = [
        f"<b>Status</b>: {'⏸ paused' if is_paused(db) else '▶️ active'}",
        f"Next scheduled run: {e(next_run or 'not scheduled')}",
    ]
    pending = db.list_pending_approvals()
    lines.append(f"Awaiting approval: {len(pending)}")
    for p in pending[:3]:
        lines.append(f"  • <code>{e(p['run_id'])}</code>")
    recent = db.list_recent_runs(5)
    if recent:
        lines.append("Recent runs:")
        for r in recent:
            lines.append(f"  • {e(r['status'])} — {e(r['topic_id'])}")
    topics = db.list_all_topics()
    lines.append(f"Topics: {sum(1 for t in topics if t['times_published'])} published / {len(topics)}")
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    spend = f"Claude spend this month: ${db.llm_spend(month):.2f}"
    if CLAUDE_MONTHLY_BUDGET_USD > 0:
        spend += f" of ${CLAUDE_MONTHLY_BUDGET_USD:.2f} budget"
    lines.append(spend)
    return "\n".join(lines)


def heartbeat_text(db: PipelineDatabase, next_run: Optional[str]) -> str:
    return "💓 <b>Daily heartbeat</b> — the daemon is running.\n" + status_text(db, next_run)


def make_command_handler(db: PipelineDatabase, next_run: Callable[[], Optional[str]],
                         trigger_run: Callable[[], str], send_photo: Optional[SendPhoto] = None,
                         assets_dir: Path = ASSETS_DIR) -> Callable[[str, str], str]:
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
        if command == "imgok":
            return "✅ " + html.escape(approve_set(_resolve_topic_dir(args, assets_dir), MIN_SCENE_IMAGES))
        if command == "status":
            return status_text(db, next_run())
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
