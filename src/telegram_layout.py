"""
src/telegram_layout.py
======================
The clean chat's layout (UI-01, design docs/design/telegram-clean-chat-2026-11-11.md §4, §5, §7, §9): the pinned
notifications panel at the top, the dashboard under it, then the temporary panels.

- **The layout table** is `telegram_cards` (topic 0, kind "layout"), one row per always-on panel, holding its
  message and the time it was sent (an edit never changes it).
- **The renderer** is the one process that writes the panels: the long-running bot (daemon or studio) takes the
  lease at start and keeps it every 30 s; it expires after 90 s. Every other process (a run worker, a CLI run, a job)
  only writes rows (notifications.post) and leaves the drawing to the holder; with no holder, the process that
  needs the panel drawn takes the lease for that moment (a CLI run alone).
- **The panel** is drawn by editing it (2 s debounce, at most one edit every 3 s); it is sent only when it is
  missing, and then the panels below it are sent again after it, so the order holds (the ordered rebuild).
- **Push alerts**: a Needs-you item that should ring sends one line with sound, at most one a minute (more join
  the next one), deleted after PUSH_ALERT_SECONDS. The deletion is recorded, so a restart still makes it.

Lock order (one order, so the dashboard and the layout can never wait on each other):
  Layout._rebuild_lock  ->  TelegramApprovalBot._dashboard_lock  ->  Layout._lock
- Layout._lock is the innermost lock: code holding it never draws the dashboard (show_dashboard, which takes
  _dashboard_lock) and never starts a rebuild. A panel found missing while it is held is only noted
  (_rebuild_due); the rebuild runs once the lock is released.
- Code holding _dashboard_lock never calls ensure() or rebuild(): show_dashboard makes sure the panel exists
  before it takes its lock.
- A rebuild holds _rebuild_lock for the whole send-in-order (the panel, then the dashboard under it), and
  Layout._lock only while it writes the panel itself. With the clean chat on the rebuild is the whole layout's
  re-post (layout_repost, UI-01f): it takes _rebuild_lock, _dashboard_lock and Layout._lock, in that order, and
  holds them until the old copies are deleted; it sends the dashboard's copy itself, never through show_dashboard.
  A dashboard drawn under _dashboard_lock that needs a re-post (it left a media screen) asks for it, and the
  re-post runs once that lock is released.
- The review panel's own lock (review_panel.ReviewPanel._lock, UI-01e) comes after all three: the tick takes it
  under Layout._lock, and since UI-01f each entry that draws the panel takes Layout._lock first; code holding it
  never takes another of these locks (kick, redraw, show_dashboard and command handlers are deferred until it is
  released).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from . import config
from . import notifications as notes

log = logging.getLogger(__name__)

LEASE_KEY = "tg-renderer"
LEASE_SECONDS = 90          # a lease nobody renews is free after this
HEARTBEAT_SECONDS = 30
DEBOUNCE_SECONDS = 2.0      # writes this close together are one edit
EDIT_GAP_SECONDS = 3.0      # the panel is edited at most this often
ALERT_GAP_SECONDS = 60.0    # at most one push alert a minute; items inside the window join the next
ALERT_LIMIT = 40            # a lock screen shows a line this long whole
ALERTS_KEY = "push-alerts"  # [{"id": message id, "until": epoch}]: alerts to delete
LAST_ALERT_KEY = "push-last"
NOTIFICATIONS = "notifications"
DASHBOARD = "dashboard"
SLOT_PREFIX = "layout:"
PROCESS_IDENTITY = f"{os.getpid()}-{int(time.time())}"
NOTE_ACTIONS = {"n", "np", "nall", "nundo"}   # the notifications panel's own callbacks (telegram_ui.PANEL_KINDS)
PROGRESS_ACTIONS = {"pstop", "pkeep", "pstopok"}   # the progress panel's Cancel run and its confirmation
REAP_SECONDS = 60.0         # the reaper's round (design §7.4)
SYNC_SECONDS = 20.0         # the condition items are re-derived this often (notice_conditions)
HOME_IDLE_SECONDS = 15 * 60          # the dashboard goes back home after this without a tap (design §6.2)
HOME_IDLE_BUSY_SECONDS = 30 * 60     # ... and after this while typed input or a wizard is mid-step


def enabled() -> bool:
    return bool(getattr(config, "TELEGRAM_CLEAN_CHAT", False))


# ------------------------------------------------------------------ the layout table
def slot(db, name: str) -> dict[str, Any] | None:
    card = db.get_telegram_card(SLOT_PREFIX + name) if hasattr(db, "get_telegram_card") else None
    return card if card and card.get("card_kind") == "layout" else None


def slot_message(db, name: str) -> int | None:
    card = slot(db, name)
    return int(card["message_id"]) if card and card.get("message_id") else None


def put_slot(db, name: str, message_id: int, sent_at: float | None = None, **extra: Any) -> None:
    payload = {"sent_at": time.time() if sent_at is None else sent_at, **extra}
    db.put_telegram_card(SLOT_PREFIX + name, 0, int(message_id), "layout", payload)


def drop_slot(db, name: str) -> None:
    db.delete_telegram_card(SLOT_PREFIX + name)


def panel_ids(db) -> set:
    """Every layout panel's message (what a clear or a sweep never deletes)."""
    out = set()
    for card in db.list_telegram_cards(topic_id=0, card_kind="layout") if hasattr(db, "list_telegram_cards") else []:
        if card.get("message_id"):
            out.add(int(card["message_id"]))
    return out


# ------------------------------------------------------------------ the renderer lease
def _lease(db) -> dict[str, Any]:
    try:
        value = json.loads(db.get_setting(LEASE_KEY) or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def holder(db, now: float | None = None) -> str | None:
    """The identity of the live lease's holder, or None when nobody holds it."""
    lease = _lease(db)
    now = time.time() if now is None else now
    return lease.get("owner") if lease.get("owner") and float(lease.get("until", 0)) > now else None


def acquire(db, owner: str = PROCESS_IDENTITY, seconds: float = LEASE_SECONDS, now: float | None = None) -> bool:
    """Takes or renews the lease when it is free, expired or already ours (compare and set on the stored value)."""
    now = time.time() if now is None else now
    raw = db.get_setting(LEASE_KEY)
    current = _lease(db)
    if current.get("owner") not in (None, owner) and float(current.get("until", 0)) > now:
        return False
    new = json.dumps({"owner": owner, "pid": os.getpid(), "until": now + seconds})
    with db._conn() as conn:
        if raw is None:
            cur = conn.execute("INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                               (LEASE_KEY, new))
        else:
            cur = conn.execute("UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ? AND value = ?",
                               (new, LEASE_KEY, raw))
        return cur.rowcount == 1


def release(db, owner: str = PROCESS_IDENTITY) -> None:
    if _lease(db).get("owner") == owner:
        db.set_setting(LEASE_KEY, json.dumps({}))


# ------------------------------------------------------------------ the renderer
class Layout:
    """Draws the layout panels for one bot. Only the lease holder calls Telegram."""

    def __init__(self, bot, owner: str = PROCESS_IDENTITY):
        self.bot = bot
        self.db = bot.db
        self.owner = owner
        self._lock = threading.RLock()          # innermost (see the lock order above)
        self._rebuild_lock = threading.RLock()  # outermost: one ordered rebuild at a time
        self._rebuild_due = False                # the panel went missing while _lock was held
        self._drawn: dict[str, Any] = {}      # slot -> the (text, keyboard) last drawn
        self._rev_seen = -1
        self._rev_since = 0.0
        self._last_edit = 0.0
        self._held_until = 0.0
        self._heartbeat = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._later: Any | None = None   # a CLI process's one pending redraw
        self._synced = 0.0                 # when the condition items were last derived
        self._reaped = 0.0                 # when the reaper last ran
        self.clock = time.time
        self.timer = threading.Timer        # replaceable in tests

    # ---------------------------------------------------------- lease
    def is_renderer(self) -> bool:
        return holder(self.db, self.clock()) == self.owner

    def take(self, seconds: float = LEASE_SECONDS) -> bool:
        return acquire(self.db, self.owner, seconds, self.clock())

    def start(self) -> None:
        """The long-running bot's renderer thread: holds the lease and keeps the panel current. Its first round
        reconciles the layout with the chat (UI-01f, design §7.5, §13)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._started = False
        self._thread = threading.Thread(target=self._loop, name="telegram-layout", daemon=True)
        self._thread.start()

    def halt(self) -> None:
        """Stops the renderer thread; it finishes its current pass first."""
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def stop(self) -> None:
        """Stops the renderer thread and gives up the lease."""
        self.halt()
        self.release()

    def release(self) -> None:
        release(self.db, self.owner)

    def started(self) -> bool:
        """The renderer's start (design §7.3, §7.5, §13): the earlier layout migrated once, an interrupted rebuild
        finished, every panel checked and the missing or too-old ones posted again, then "back online"."""
        if not enabled() or not self.bot.is_configured() or not (self.is_renderer() or self.take()):
            return False
        last_poll = float(self.db.get_setting("telegram_last_poll") or 0) or None
        with self._rebuild_lock:   # outermost (lock order); a re-post takes the dashboard lock and _lock itself
            if self.repost.migrate() is None:
                self.repost.reconcile()
            self.repost.back(last_poll)
        self._started = True
        return True

    def _loop(self) -> None:
        while not self._stop.is_set() and not self._started:
            try:
                self.started()
            except Exception as e:  # noqa: BLE001 - the renderer goes on; the age rule repairs the layout later
                log.warning("Telegram layout start-up check failed: %s", e)
                self._started = True
            if not self._started:
                self._stop.wait(5.0)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - the renderer never dies on one bad draw
                log.warning("Telegram layout tick failed: %s", e)
            self._stop.wait(1.0)

    def tick(self, force: bool = False) -> bool:
        """One pass: renew the lease, then draw what changed, ring what should ring and delete expired alerts.
        Returns whether this process is the renderer."""
        now = self.clock()
        if now - self._heartbeat >= HEARTBEAT_SECONDS or not self.is_renderer():
            if not self.take():
                return False
            self._heartbeat = now
        self.ensure(now)   # before _lock: a rebuild draws the dashboard (lock order)
        with self._lock:
            if force or now - self._synced >= SYNC_SECONDS:
                self._synced = now
                from . import notice_conditions
                notice_conditions.sync(self.db, now)   # condition items follow the state (UI-01b)
            if now - self._reaped >= REAP_SECONDS and hasattr(self.bot, "reap_progress_panels"):
                self._reaped = now
                self.bot.reap_progress_panels()   # UI-01c: a run that ended without its finisher
                if getattr(self.bot, "_review", None) is not None or slot_message(self.db, "review"):
                    self.bot.review.tick(now)     # UI-01e: the reason step, the note, an input, Undo windows
            if hasattr(self.bot, "review") and self.db.get_setting("review-panel-redraw"):
                self.bot.review.redraw_if_asked()   # UI-01e: a review another process delivered
            self.draw_notifications(now, force=force)
            self.push(now)
            self.delete_due_alerts(now)
        self.repair()
        asked = getattr(self.bot, "draw_asked_dashboard", None)
        if callable(asked):
            asked()   # UI-01f: a dashboard another process asked for (only the renderer sends one), outside _lock
        if enabled() and getattr(self, "_started", True):
            # UI-01f: the 30 h idle / 40 h forced re-post and the hourly gap sweep, outside _lock (a re-post takes
            # _rebuild_lock, then the dashboard lock, then _lock: the lock order)
            self.repost.tick(now)
            asked = getattr(self.bot, "_repost_if_asked", None)
            if callable(asked):
                asked()   # a dashboard re-post asked for under the dashboard lock and not run yet (path B)
        idle = getattr(self.bot, "dashboard_idle_tick", None)
        if callable(idle):
            idle(now)
        return True

    def kick(self) -> None:
        """A sender wrote an item. The renderer's own thread draws it on its tick (debounced); another holder of
        the lease draws it in its process. With no holder (a CLI process alone) this process draws it: now, or
        once the 3-second edit gap has passed, so a burst of notices is still one edit."""
        if not self.bot.is_configured() or (self._thread and self._thread.is_alive()):
            return
        other = holder(self.db, self.clock())
        if other and other != self.owner:
            return
        wait = EDIT_GAP_SECONDS - (self.clock() - self._last_edit)
        if wait <= 0:
            self._kick_now()
        elif self._later is None:
            self._later = self.timer(wait + 0.05, self._kick_now)
            self._later.daemon = True
            self._later.start()

    def _kick_now(self) -> None:
        self._later = None
        try:
            self.tick(force=True)
        except Exception as e:  # noqa: BLE001 - a notice never breaks the run that sent it
            log.warning("Could not draw the notifications panel: %s", e)

    # ---------------------------------------------------------- the notifications panel
    def renders_here(self) -> bool:
        """Whether this process may send the panels: it holds the renderer lease, or takes it because nobody does
        (a CLI process alone). Only the renderer ever sends a panel (design §5.4)."""
        return self.is_renderer() or self.take()

    def ensure(self, now: float | None = None) -> int | None:
        """The notifications panel exists, pinned, above the dashboard. When it is missing, the renderer sends it
        and then the panels below it again under it (the ordered rebuild: layout_repost with the clean chat on),
        deleting the old copies: the order holds. Another process leaves it to the renderer and returns None. Never
        call it holding _dashboard_lock or _lock."""
        mid = slot_message(self.db, NOTIFICATIONS)
        if mid or not self.renders_here():
            return mid
        with self._rebuild_lock:
            return slot_message(self.db, NOTIFICATIONS) or self._rebuild(NOTIFICATIONS)

    @property
    def repost(self):
        """The ordered rebuild, the reconcile, the gap sweep and the migration (layout_repost, UI-01f)."""
        if getattr(self, "_repost", None) is None:
            from .layout_repost import Repost
            self._repost = Repost(self)
        return self._repost

    def rebuild(self, from_slot: str = NOTIFICATIONS, urgent: bool = False) -> int | None:
        """Sends the panels from `from_slot` down again, in order, then deletes the old copies (design §5.2).
        The old copies stay on screen until every new one is sent, so the chat is never empty. Renderer only;
        never call it holding _dashboard_lock or _lock. With the clean chat on it is the whole layout's re-post
        (layout_repost: the dashboard, progress and review panels too; the dashboard's copy is sent there, under
        the dashboard lock, never through show_dashboard). `urgent` (the operator's /start, /clear) does not wait
        out a failed re-post's back-off."""
        if not self.renders_here():
            return None
        with self._rebuild_lock:
            return self._rebuild(from_slot, urgent)

    def _rebuild(self, from_slot: str, urgent: bool = False) -> int | None:
        if enabled():
            sent = self.repost.rebuild(from_slot, urgent=urgent)
            return slot_message(self.db, NOTIFICATIONS) if sent is not None else None
        old_notes = slot_message(self.db, NOTIFICATIONS)
        new_notes = old_notes
        if from_slot == NOTIFICATIONS:
            with self._lock:   # only while the panel itself is written (lock order)
                text, rows = notes.render(self.db, self.clock(), self._run_key)
                new_notes = self._send(text, rows)
                if not new_notes:
                    return None
                put_slot(self.db, NOTIFICATIONS, new_notes, self.clock())
                self._drawn[NOTIFICATIONS] = (text, rows)
                self._pin(new_notes)
        # The dashboard goes below: a fresh copy under the panel, the old one deleted by show_dashboard. _lock is
        # not held here: show_dashboard takes _dashboard_lock.
        if self.bot.db.get_setting("telegram_dashboard_id"):
            try:
                self.bot.show_dashboard(fresh=True, rebuild=True)
            except Exception as e:  # noqa: BLE001 - the panel above is already drawn
                log.warning("Could not re-send the dashboard under the notifications panel: %s", e)
        if old_notes and old_notes != new_notes:
            self.bot.delete_message(old_notes)
        return new_notes

    def repair(self) -> int | None:
        """Re-posts the panel in order when an edit found it gone (outside _lock: lock order)."""
        if not self._rebuild_due:
            return None
        self._rebuild_due = False
        return self.ensure()

    def redraw(self) -> bool:
        """Draws the panel now (a tap on it), under _lock as the renderer's tick does, so two edits never race
        and an older render never lands last; a panel found gone is posted again after the lock is released."""
        with self._lock:
            drawn = self.draw_notifications(force=True)
        self.repair()
        return drawn

    def _run_key(self, run_id: str) -> str:
        try:
            return self.db.run_key(run_id)
        except Exception:  # noqa: BLE001 - a run the database no longer has keeps its id
            return run_id

    def _markup(self, rows: list[list[dict[str, str]]]) -> str:
        return json.dumps({"inline_keyboard": rows})

    def _send(self, text: str, rows) -> int | None:
        data = {"chat_id": self.bot.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
                "disable_notification": "true", "reply_markup": self._markup(rows)}
        try:
            result = self.bot._api("sendMessage", data=data)
        except RuntimeError as e:
            if "can't parse entities" not in str(e).lower():
                log.warning("Could not send the notifications panel: %s", e)
                return None
            data.pop("parse_mode")
            data["text"] = notes.plain(text)
            log.warning("Notifications panel sent as plain text: %s", e)
            result = self.bot._api("sendMessage", data=data)
        return result.get("message_id") if isinstance(result, dict) else None

    def _pin(self, message_id: int) -> None:
        try:
            self.bot._api("pinChatMessage", data={"chat_id": self.bot.chat_id, "message_id": message_id,
                                                  "disable_notification": "true"})
        except Exception as e:  # noqa: BLE001 - an unpinned panel is still the first message
            log.warning("Could not pin the notifications panel: %s", e)

    def draw_notifications(self, now: float | None = None, force: bool = False) -> bool:
        """Edits the panel to its current content: debounced 2 s after a write, at most one edit every 3 s, and
        not at all while Telegram holds edits (429). Back to list page 1 after 2 minutes without a tap, or at once
        when a Needs-you item arrives while a list page is shown (an open item page is not moved)."""
        now = self.clock() if now is None else now
        mid = slot_message(self.db, NOTIFICATIONS)
        if not mid or now < self._held_until:
            return False
        rev = notes.revision(self.db)
        if rev != self._rev_seen:
            self._rev_seen, self._rev_since = rev, now
        state = notes.view(self.db)
        page, item = int(state.get("page") or 1), state.get("item")
        needs = sum(1 for i in notes.open_items(self.db) if i["section"] == notes.NEEDS)
        seen = int(self.db.get_setting(notes.SEEN_NEEDS_KEY) or 0)
        if needs != seen:
            self.db.set_setting(notes.SEEN_NEEDS_KEY, str(needs))
            if needs > seen and not item and page != 1:
                notes.set_view(self.db, 1, None, 1, float(state.get("tapped") or now))
        if (page != 1 or item) and now - float(state.get("tapped") or 0) >= notes.RETURN_SECONDS:
            notes.set_view(self.db, 1, None, 1, now)
        if not force and (now - self._rev_since < DEBOUNCE_SECONDS or now - self._last_edit < EDIT_GAP_SECONDS):
            return False   # still settling (ten writes in a second are one edit), or edited a moment ago
        text, rows = notes.render(self.db, now, self._run_key)
        if self._drawn.get(NOTIFICATIONS) == (text, rows):
            return False
        return self._edit(mid, text, rows, now)

    def _edit(self, mid: int, text: str, rows, now: float) -> bool:
        from .telegram_bot import TelegramRateLimited
        data = {"chat_id": self.bot.chat_id, "message_id": mid, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": "true", "reply_markup": self._markup(rows)}
        try:
            self.bot._api("editMessageText", data=data)
        except TelegramRateLimited as e:
            self._held_until = now + float(e.retry_after)
            return False
        except RuntimeError as e:
            reason = str(e).lower()
            if "message to edit not found" in reason or "message_id_invalid" in reason:
                log.warning("The notifications panel is gone (%s); posting it again in order.", e)
                drop_slot(self.db, NOTIFICATIONS)
                self._rebuild_due = True   # posted again once _lock is released (lock order)
                return True
            if "can't parse entities" in reason:
                log.warning("Notifications panel drawn as plain text: %s", e)
                data.pop("parse_mode")
                data["text"] = notes.plain(text)
                self.bot._api("editMessageText", data=data)
            else:
                log.warning("Could not draw the notifications panel: %s", e)
                return False
        self._drawn[NOTIFICATIONS] = (text, rows)
        self._last_edit = now
        return True

    # ---------------------------------------------------------- push alerts
    def push(self, now: float | None = None) -> int | None:
        """Sends the one-line alert for the items waiting to ring, at most one a minute (design §9)."""
        now = self.clock() if now is None else now
        waiting = notes.pending_pushes(self.db)
        if not waiting or now - float(self.db.get_setting(LAST_ALERT_KEY) or 0) < ALERT_GAP_SECONDS:
            return None
        text = alert_text(waiting, sum(1 for i in notes.open_items(self.db) if i["section"] == notes.NEEDS))
        try:
            result = self.bot._api("sendMessage", data={"chat_id": self.bot.chat_id, "text": text,
                                                        "disable_web_page_preview": "true"})
        except Exception as e:  # noqa: BLE001 - the panel still shows the item
            log.warning("Could not send the push alert: %s", e)
            return None
        notes.clear_pushes(self.db, [i["id"] for i in waiting])
        self.db.set_setting(LAST_ALERT_KEY, str(now))
        mid = result.get("message_id") if isinstance(result, dict) else None
        if mid:
            due = self._due_alerts() + [{"id": int(mid), "until": now + float(config.PUSH_ALERT_SECONDS)}]
            self.db.set_setting(ALERTS_KEY, json.dumps(due))
            timer = self.timer(float(config.PUSH_ALERT_SECONDS) + 0.2, self._delete_alerts_later)
            timer.daemon = True
            timer.start()
        return mid

    def _delete_alerts_later(self) -> None:
        try:
            with self._lock:
                self.delete_due_alerts(self.clock())
        except Exception as e:  # noqa: BLE001 - the next tick deletes it
            log.debug("Alert deletion deferred: %s", e)

    def _due_alerts(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.db.get_setting(ALERTS_KEY) or "[]")
        except ValueError:
            value = []
        return [v for v in value if isinstance(v, dict) and v.get("id")]

    def delete_due_alerts(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        alerts = self._due_alerts()
        due = [a for a in alerts if float(a.get("until", 0)) <= now]
        if not due:
            return 0
        for alert in due:
            self.bot.delete_message(int(alert["id"]))   # "not found" is success: it is gone either way
        self.db.set_setting(ALERTS_KEY, json.dumps([a for a in alerts if a not in due]))
        return len(due)


def alert_text(waiting: list[dict[str, Any]], needs: int) -> str:
    """One line of at most 40 characters: '🔔 Render failed · Göbekli Tepe', or for several at once
    '🔔 3 need you · newest: Review waiting'."""
    newest = max(waiting, key=lambda i: (float(i["updated_at"]), i["id"]))
    if len(waiting) == 1:
        line = newest.get("alert") or newest["title"] + (f" · {newest['subject']}" if newest.get("subject") else "")
        if line.startswith("⏰ "):   # the review reminder: "⏰ Review closes 16:40 · <title>" (design §9)
            return notes.clip(line, ALERT_LIMIT)
        return "🔔 " + notes.clip(line, ALERT_LIMIT - 2)
    count = max(needs, len(waiting))
    lead = f"🔔 {count} {'needs' if count == 1 else 'need'} you · newest: "
    newest_name = (newest.get("alert") or newest["title"]).removeprefix("⏰ ").partition(" · ")[0]
    return lead + notes.clip(newest_name, max(8, ALERT_LIMIT - len(lead)))
