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
        self._lock = threading.RLock()
        self._drawn: dict[str, Any] = {}      # slot -> the (text, keyboard) last drawn
        self._rev_seen = -1
        self._rev_since = 0.0
        self._last_edit = 0.0
        self._held_until = 0.0
        self._heartbeat = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._later: Any | None = None   # a CLI process's one pending redraw
        self.clock = time.time
        self.timer = threading.Timer        # replaceable in tests

    # ---------------------------------------------------------- lease
    def is_renderer(self) -> bool:
        return holder(self.db, self.clock()) == self.owner

    def take(self, seconds: float = LEASE_SECONDS) -> bool:
        return acquire(self.db, self.owner, seconds, self.clock())

    def start(self) -> None:
        """The long-running bot's renderer thread: holds the lease and keeps the panel current."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
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

    def _loop(self) -> None:
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
        with self._lock:
            self.ensure(now)
            self.draw_notifications(now, force=force)
            self.push(now)
            self.delete_due_alerts(now)
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
    def ensure(self, now: float | None = None) -> int | None:
        """The notifications panel exists, pinned, above the dashboard. When it is missing it is sent, and the
        dashboard (if there is one) is sent again under it and its old copy deleted: the order holds."""
        mid = slot_message(self.db, NOTIFICATIONS)
        if mid:
            return mid
        return self.rebuild(NOTIFICATIONS)

    def rebuild(self, from_slot: str = NOTIFICATIONS) -> int | None:
        """Sends the panels from `from_slot` down again, in order, then deletes the old copies (design §5.2).
        The old copies stay on screen until every new one is sent, so the chat is never empty."""
        with self._lock:
            old_notes = slot_message(self.db, NOTIFICATIONS)
            new_notes = old_notes
            if from_slot == NOTIFICATIONS:
                text, rows = notes.render(self.db, self.clock(), self._run_key)
                new_notes = self._send(text, rows)
                if not new_notes:
                    return None
                put_slot(self.db, NOTIFICATIONS, new_notes, self.clock())
                self._drawn[NOTIFICATIONS] = (text, rows)
                self._pin(new_notes)
            # The dashboard goes below: a fresh copy under the panel, the old one deleted by show_dashboard.
            if self.bot.db.get_setting("telegram_dashboard_id"):
                try:
                    self.bot.show_dashboard(fresh=True)
                except Exception as e:  # noqa: BLE001 - the panel above is already drawn
                    log.warning("Could not re-send the dashboard under the notifications panel: %s", e)
            if old_notes and old_notes != new_notes:
                self.bot.delete_message(old_notes)
            return new_notes

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
                self.rebuild(NOTIFICATIONS)
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
        line = newest["title"] + (f" · {newest['subject']}" if newest.get("subject") else "")
        return "🔔 " + notes.clip(line, ALERT_LIMIT - 2)
    count = max(needs, len(waiting))
    lead = f"🔔 {count} {'needs' if count == 1 else 'need'} you · newest: "
    return lead + notes.clip(newest["title"], max(8, ALERT_LIMIT - len(lead)))
