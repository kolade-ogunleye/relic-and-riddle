"""
src/notice_conditions.py
========================
The notifications panel's condition items (UI-01, design §6.1.2): derived from the state on every sync, not
written by a sender. They are the same questions the dashboard's "Needs you" block asked (pending and timed-out
reviews, ready proposals, account and provider problems, photo sets not ready, a re-renderable late failure, a
post candidate, an empty queue, the weekly digest). A condition item appears while its condition holds and
resolves itself when it clears, so the panel and the state never disagree; it carries no Dismiss.

Every text here is built from the database's own records (run labels, the run's recorded error, account and
provider messages), as the dashboard's block was.
"""
from __future__ import annotations

import html
import logging
import time
from typing import Any

from . import notifications as notes

log = logging.getLogger(__name__)
PREFIX = "cond:"
SYNC_SECONDS = 20.0   # the renderer re-derives the conditions this often (and after a decision)


def _approval_closes(db, run_id: str) -> float | None:
    try:
        value = (db.get_approval(run_id) or {}).get("expires_at")
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def current(db) -> list[dict[str, Any]]:
    """The conditions that hold now, each as an item: key, section, emoji, title, subject, body, actions and
    (for a Needs-you one) the count whose rise rings."""
    from . import control
    from .local_time import show
    e = html.escape
    out: list[dict[str, Any]] = []
    pending = db.list_pending_approvals()
    if pending:
        closing = sorted(((_approval_closes(db, p["run_id"]) or float("inf")), p) for p in pending)
        first_at, first = closing[0]
        names = [e(db.run_label(p["run_id"])) for _, p in closing[:3]]
        body = (f"{names[0]} closes first, at {e(show(first_at, '%H:%M'))}." if first_at != float("inf")
                else f"{names[0]} is waiting.")
        if len(names) > 1:
            body += "\n" + "\n".join(f"• {n}" for n in names[1:]) + (f"\n• and {len(pending) - 3} more" if len(pending) > 3 else "")
        newest = db.run_label(pending[0]["run_id"])
        out.append({"key": PREFIX + "reviews", "section": notes.NEEDS, "emoji": "🎬",
                    "title": "Review waiting" if len(pending) == 1 else "Reviews waiting",
                    "subject": str(len(pending)) if len(pending) > 1 else db.run_label(first["run_id"]),
                    "body": body, "count": len(pending), "alert": f"Review waiting · {newest}",
                    "actions": [_reviews_action(db)]})
    timed_out = db.timed_out_reviews() if hasattr(db, "timed_out_reviews") else []
    if _panel(db) and timed_out:
        # One item per run (UI-01e): a review that timed out in the clean chat is its run's own item (fail:<run>,
        # written by the wait's timeout), so the condition names only the others (timed out before it).
        with db._conn() as conn:
            own = {r[0].partition(":")[2] for r in conn.execute(
                "SELECT dedupe_key FROM notifications WHERE state = 'open' AND dedupe_key LIKE 'fail:%'")}
        timed_out = [r for r in timed_out if r["run_id"] not in own]
    if timed_out and len(timed_out) == 1 and _panel(db):
        # The clean chat's review panel (UI-01e, mock-up 3): the one review that timed out, with Resend for review
        run_id = timed_out[0]["run_id"]
        out.append({"key": PREFIX + "timed-out", "section": notes.NEEDS, "emoji": "⏰", "title": "Review timed out",
                    "subject": db.run_label(run_id), "run": run_id, "count": 1,
                    "body": "No decision came within the review window, so nothing was posted. The render is kept: "
                            "resend it for review, or reject it later from 🎬 Reviews.",
                    "actions": [{"text": "Resend for review", "callback_data": f"resend:{db.run_key(run_id)}"}]})
    elif timed_out:
        out.append({"key": PREFIX + "timed-out", "section": notes.NEEDS, "emoji": "⏰",
                    "title": "Review timed out" if len(timed_out) == 1 else f"{len(timed_out)} reviews timed out",
                    "subject": db.run_label(timed_out[0]["run_id"]) if len(timed_out) == 1 else "",
                    "body": "Nothing was posted; the render is kept. Resend it for review, or reject it, from 🎬 Reviews."
                            + ("\n" + "\n".join(f"• {e(db.run_label(r['run_id']))}" for r in timed_out[:3]) if len(timed_out) > 1 else ""),
                    "count": len(timed_out), "actions": [{"text": "🎬 Reviews", "callback_data": "creviews:"}]})
    try:
        from . import improve
        ready = [p for p in improve.proposals(db) if p["status"] == "ready"]
    except Exception as err:  # noqa: BLE001 - the panel never fails over the improvement ledger
        log.debug("Proposals not read for the panel: %s", err)
        ready = []
    if ready:
        out.append(proposals_item(ready))
    from . import accounts
    problems = accounts.cached(db).get("problems") or []
    if problems:
        out.append({"key": PREFIX + "accounts", "section": notes.NEEDS, "emoji": "⚠️", "title": "Publishing accounts",
                    "subject": "", "body": "\n".join(f"• {e(p)}" for p in problems), "count": len(problems),
                    "actions": [{"text": "🔗 Accounts", "callback_data": "caccounts:"}]})
    from . import provider_status
    lines = provider_status.dashboard_lines()
    if lines:
        out.append({"key": PREFIX + "providers", "section": notes.NEEDS, "emoji": "⛔", "title": "A provider is unavailable"
                    if len(lines) == 1 else f"{len(lines)} providers unavailable", "subject": "",
                    "body": "\n".join(ln.removeprefix("⛔ ") for ln in lines), "count": len(lines),
                    "actions": [{"text": "📊 Status", "callback_data": "cstat:"}]})
    topics = db.list_all_topics()
    queued = [t for t in topics if t.get("status") == "queued"]
    if not queued:
        out.append({"key": PREFIX + "empty-queue", "section": notes.NEEDS, "emoji": "📭", "title": "Topic queue is empty",
                    "subject": "", "body": "Scheduled runs are skipped until a topic is added.", "count": 1,
                    "actions": [{"text": "💡 Ideas", "callback_data": "cideas:"}, {"text": "📋 Queue", "callback_data": "qrefresh:"}]})
    else:
        ready_sets = control.ready_pools(db, queued)
        if ready_sets < len(queued) and ready_sets < 3:
            out.append({"key": PREFIX + "photos", "section": notes.NEEDS, "emoji": "🖼", "title": "Photo sets not ready",
                        "subject": "", "body": f"Only {ready_sets} of {len(queued)} queued topics have photos ready.",
                        "count": 1, "actions": [{"text": "🖼 Pictures", "callback_data": "cimages:"}]})
    active = db.get_active_progress() if hasattr(db, "get_active_progress") else None
    late = control.rerenderable(db) if not active else None
    if late and notes.has_key(db, notes.FAIL_PREFIX + late["run_id"]):
        late = None   # the run's own failure item says it (one failure, one item; dismissed stays dismissed)
    if late:
        what = {"rejected": "Rejected, can be made again", "regenerate": "Regenerate did not start",
                "timed_out": "Stopped at its deadline"}.get(late.get("status"), "")
        what = what or ("Failed after rendering" if late.get("master_video_path") else "Failed after its fact check")
        verb = "Make again" if late.get("status") in ("rejected", "regenerate") else "Re-render"
        reason = control.short_reason(late.get("error") or "", limit=1000)
        key = db.run_key(late["run_id"])
        out.append({"key": PREFIX + "late:" + late["run_id"], "section": notes.NEEDS, "emoji": "🔁", "title": what,
                    "subject": db.run_label(late["run_id"]), "run": late["run_id"],
                    "body": (e(reason) + "\n" if reason else "") + "The work made so far is kept.", "count": 1,
                    "actions": [{"text": verb, "callback_data": f"rerender:{key}"},
                                {"text": "Details", "callback_data": f"chist:{late['run_id']}"}]})
    candidates = control.post_candidates(db, limit=1) if not active else []
    if candidates:
        run_id = candidates[0]["run_id"]
        out.append({"key": PREFIX + "candidate:" + run_id, "section": notes.NEEDS, "emoji": "🚀", "title": "Post candidate",
                    "subject": db.run_label(run_id), "run": run_id,
                    "body": "A finished dry run that can be posted for real (you are asked to confirm first).", "count": 1,
                    "actions": [{"text": "Post for real", "callback_data": f"postreal:{db.run_key(run_id)}"}]})
    digest = control.weekly_digest(db)
    if digest:
        out.append({"key": PREFIX + "digest", "section": notes.INFO, "emoji": "📰", "title": "Weekly digest ready",
                    "subject": "", "body": e(digest.get("label") or ""), "count": 1,
                    "actions": [{"text": "Open digest", "callback_data": "cdigest:"}]})
    return out


def _panel(db) -> bool:
    """Whether the clean chat's review panel is in use (UI-01e): the switch is on."""
    from . import config
    return bool(getattr(config, "TELEGRAM_CLEAN_CHAT", False))


def _reviews_action(db) -> dict[str, str]:
    """🎬 Show reviews turns the review panel to the soonest-closing review when the panel is in the chat
    (design §10.1); otherwise 🎬 Reviews opens the list in the dashboard."""
    from .telegram_layout import slot_message
    if _panel(db) and slot_message(db, "review"):
        return {"text": "🎬 Show reviews", "callback_data": "rvshow:"}
    return {"text": "🎬 Reviews", "callback_data": "creviews:"}


def proposals_item(ready: list[dict[str, Any]]) -> dict[str, Any]:
    """The night's ready proposals as one item (design §6.1.3: three proposal cards were the 29 Oct complaint)."""
    n = len(ready)
    names = ", ".join(html.escape(str(p.get("title") or p.get("id"))) for p in ready[:3])
    more = f" and {n - 3} more" if n > 3 else ""
    return {"key": PREFIX + "proposals", "section": notes.NEEDS, "emoji": "🧭",
            "title": f"{n} improvement proposal{'s' if n != 1 else ''}", "subject": "",
            "body": f"Waiting for your decision: {names}{more}.\nAccept or decline each one in 🧭 Improvements.",
            "count": n, "actions": [{"text": "🧭 Improvements", "callback_data": "cimprove:"}]}


def sync(db, now: float | None = None) -> dict[str, int]:
    """Brings the panel's condition items in line with the state: new conditions are added (a Needs-you one
    rings), a changed one is updated in place (it rings again only when its count rose), a cleared one resolves
    itself. A condition dismissed with Dismiss all info stays dismissed until its text changes. Returns counts."""
    now = time.time() if now is None else now
    notes.ensure(db)
    try:
        held = current(db)
    except Exception as err:  # noqa: BLE001 - a broken query leaves the panel as it was, never empty
        log.warning("Condition items not derived: %s", err)
        return {"added": 0, "updated": 0, "resolved": 0}
    counts = {"added": 0, "updated": 0, "resolved": 0}
    keys = {c["key"] for c in held}
    with db._conn() as conn:
        rows = {r["dedupe_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE dedupe_key LIKE 'cond:%' AND id IN "
            "(SELECT MAX(id) FROM notifications WHERE dedupe_key LIKE 'cond:%' GROUP BY dedupe_key)")}
    for c in held:
        last = rows.get(c["key"])
        same = last and (last["title"], last["subject"], last["body"]) == (c["title"], c["subject"], c["body"])
        if last and same and (last["state"] == notes.OPEN or last["state"] == notes.DISMISSED):
            continue
        rose = bool(last and last["state"] == notes.OPEN and int(c.get("count") or 1) > int(last.get("cond_count") or 0))
        ring = None if not last or last["state"] != notes.OPEN else rose
        notes.post(db, c["section"], c["key"], c["title"], c["body"], emoji=c["emoji"], subject=c["subject"],
                   actions=c["actions"], run=c.get("run"), replace=True, now=now, ring=ring,
                   alert=c.get("alert"), cond_count=int(c.get("count") or 1))
        counts["updated" if last and last["state"] == notes.OPEN else "added"] += 1
    for key, row in rows.items():
        if row["state"] == notes.OPEN and key not in keys:
            counts["resolved"] += notes.close_key(db, key, notes.RESOLVED)
    # A run's failure item resolves itself once the run is no longer stopped (re-rendered, approved, published).
    from .control import STOPPED
    with db._conn() as conn:
        fails = [r["dedupe_key"] for r in conn.execute(
            "SELECT dedupe_key FROM notifications WHERE state = 'open' AND dedupe_key LIKE 'fail:%'")]
    for key in fails:
        run = db.get_run(key.partition(":")[2]) if key.partition(":")[2] else None
        if run and run.get("status") not in STOPPED + ("timeout",):   # a timed-out review waits for Resend
            counts["resolved"] += notes.close_key(db, key, notes.RESOLVED)
    return counts


def announce_digest(db, bot) -> None:
    """This week's digest is ready. The clean chat: the info item "📰 Weekly digest ready" (a condition), which
    arrives silently (design §10.1: no buzz); the earlier chat buzzed a nudge that left after a minute (operator,
    2026-10-25)."""
    from . import config
    if not getattr(config, "TELEGRAM_CLEAN_CHAT", False):
        bot.nudge("🔔 Dashboard updated: this week's digest is ready.")
        return
    sync(db)
    kick = getattr(getattr(bot, "layout", None), "kick", None)
    if callable(kick):
        kick()


def is_condition(item: dict[str, Any]) -> bool:
    return str(item.get("dedupe_key", "")).startswith(PREFIX)
