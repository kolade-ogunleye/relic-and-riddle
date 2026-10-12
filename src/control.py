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
  /help     this list
"""

import html
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from .config import CLAUDE_MONTHLY_BUDGET_USD, HEALTHCHECK_PING_URL, is_configured
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
    "/help — this list"
)


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
                         trigger_run: Callable[[], str]) -> Callable[[str, str], str]:
    def handle(command: str, args: str) -> str:
        if command in ("help", "start"):
            return HELP
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
