"""
src/layout_repost.py
====================
The clean chat's layout over time (UI-01f, design docs/design/telegram-clean-chat-2026-11-11.md §5.2, §5.3, §5.5,
§6.3 path B, §7.2, §7.3, §7.5, §13; mock-up 13): Telegram lets a bot delete only messages younger than 48 hours, so
the layout is posted again, in order, before then, and nothing else stays in the chat.

- **The ordered rebuild** (one routine, every cause): every panel at or below `from_slot` (notifications, dashboard,
  the progress panels oldest first, the review panel) is sent again silently, top to bottom; each new copy is
  recorded in its row's "next" field before the following one is sent. Then every row is swapped to its new copy in
  one transaction, the old copies are deleted in one call and the gap sweep runs. The notifications copy is pinned
  again (its "pinned" service line is deleted when its update arrives). The review copy is sent from its file ids:
  nothing is uploaded again. A tap on an old copy is mapped to the new one (the alias table, kept in the database).
- **Interrupted**: a process that dies mid-rebuild leaves "next" fields; the next renderer deletes those copies and
  starts again, so no duplicate survives.
- **When**: at the first idle moment after the oldest panel is LAYOUT_REPOST_IDLE_HOURS (30) old (no tap or message
  for LAYOUT_IDLE_MINUTES, no input waiting, no review page opened in the last 5 minutes), forced at
  LAYOUT_REPOST_FORCE_HOURS (40); at /start and /clear; when a panel is found missing; when the dashboard leaves a
  media screen (path B); at start (reconcile).
- **The gap sweep** deletes every message between the oldest panel and the newest id seen that is not a panel, an
  alert still due, or a message received in the last 30 s (at most 1,000 ids, in calls of 100), after each rebuild
  and every hour.
- **A re-post that fails** (a copy refused, Telegram's flood control) deletes the copies it sent, keeps the old
  layout and waits before the next automatic try: Telegram's retry_after, else 1, 2, 4, 8, then every 15 minutes;
  it is logged once, and after 3 failures in a row one Needs-you item says so (closed once a re-post works). /start
  and /clear (the operator asking) skip the wait, never Telegram's. A review panel whose pager is empty is closed,
  not copied.
- **A copy that cannot be deleted** (over 48 h, after a long outage) is edited once to a quiet line with no buttons
  and recorded as stuck, so it is never edited again.
- **Migration**: the first start of this build deletes every known message of the earlier layout (alerts, review
  cards and albums, notices, cards) and sweeps 1,000 ids down from the new layout; reviews waiting are delivered into
  the review panel; if old messages remain that the bot cannot remove, one info item says how to clear them.

Lock order (telegram_layout's one order: Layout._rebuild_lock -> the bot's _dashboard_lock -> Layout._lock -> the
review panel's lock): only the renderer sends layout messages (the lease). A rebuild takes the three layout locks in
that order and holds them until its old copies are deleted, so no dashboard draw, notifications edit or second
rebuild lands on a copy being replaced; it never calls the bot's show_dashboard (it sends the dashboard's copy
itself) and is never started by code holding _dashboard_lock or Layout._lock (the renderer's tick runs the age rule
after releasing Layout._lock; a dashboard that needs a re-post asks for it once its lock is released).
"""
from __future__ import annotations

import contextlib
import html
import json
import logging
import time
from typing import Any

from . import config

log = logging.getLogger(__name__)

ALIASES_KEY = "panel-aliases"            # {old message id: new message id}: taps on an old copy act on the new
STUCK_KEY = "panel-stuck"                # old copies the bot could not delete (tombstoned, never edited again)
NEWEST_KEY = "layout-newest-id"          # the newest message id seen in the chat (sent or received)
SWEPT_KEY = "layout-swept-at"
SWEPT_UPTO_KEY = "layout-swept-upto"   # the newest id the last sweep reached
MIGRATED_KEY = "layout-migrated"         # set once the earlier layout's messages have been cleaned (§13)
STOPPED_KEY = "service-stopped"          # {"at": epoch, "planned": true}: a clean stop, for the "back" item
SEND_GAP_SECONDS = 1.1                   # one send per 1.1 s (F10)
SWEEP_SECONDS = 3600.0
SWEEP_LIMIT = 1000
RECENT_SECONDS = 30.0                    # a message received this recently may still be being read
REVIEW_LOOK_SECONDS = 300.0              # a review page opened this recently means someone is looking
STUCK_TEXT = "This old panel can't be removed by the bot. Long-press › Delete to clear it."
OLD_MESSAGES_TEXT = ("Older messages can't be removed by the bot. To tidy them once: chat menu › Clear history "
                     "(tick ‘Also delete for the bot’), then send /start.")
ALIAS_LIMIT = 200
# The earlier chat's headings of a notice that needed the operator: at the migration such a notice becomes a
# Needs-you item before its message is deleted (design §13, "Needs you never lost"). Every heading mark of a Needs-you
# sender (section or group "needs" in main.py and src/) is here: tests/test_ui01f_repost.py scans for them.
NEEDS_MARKS = ("❌", "⚠️", "⚠", "💥", "🩺", "⏰", "⌛", "⛔", "🚫", "🔴", "🆘", "❓",
               "🛑", "⏱", "🗄", "🧾", "🔁", "📭", "🏛", "🖼")
# Headings only informational senders use (digests, receipts, a run started, posted, scheduled, stopped, back): a
# persistent notice is Needs you unless its mark is one of these; a transient one is Needs you only by NEEDS_MARKS
# (the earlier chat gave every Needs-you notice without buttons a 6 h life, and info ones too).
INFO_MARKS = ("✅", "📮", "📊", "📦", "⚖", "🔄", "🔎", "▶", "🗓", "📜", "🔑", "🧹", "⏸", "⏹", "🎬", "🟢", "📝", "⚡")
RETRY_KEY = "layout-repost-retry"        # a re-post that failed: {"fails", "until", "limited", "reason", "said"}
RETRY_STEPS = (60.0, 120.0, 240.0, 480.0, 900.0)   # its back-off: 1, 2, 4, 8, then every 15 minutes
RETRY_SAY_AFTER = 3                      # failures in a row before one Needs-you item says so
RETRY_ITEM_KEY = "layout-repost"


def needs_you(emoji: str, persistent: bool) -> bool:
    """An earlier-chat notice's place at the migration (§13): Needs you when its heading carries a failure or warning
    mark (NEEDS_MARKS); else info when its mark is an informational sender's (INFO_MARKS); else Needs you when it was
    persistent (it carried buttons: it asked for an action), info when transient. Never a Needs-you notice silenced."""
    mark = (emoji or "").strip()
    if mark.startswith(NEEDS_MARKS):
        return True
    if mark.startswith(INFO_MARKS):
        return False
    return bool(persistent)


def _json(db, key: str, default):
    try:
        value = json.loads(db.get_setting(key) or "null")
    except ValueError:
        value = None
    return default if value is None else value


def seen(db, message_id: Any) -> None:
    """Records the newest message id seen in the chat (the gap sweep's upper bound)."""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return
    if mid > int(db.get_setting(NEWEST_KEY) or 0):
        db.set_setting(NEWEST_KEY, str(mid))


def aliases(db) -> dict[int, int]:
    return {int(k): int(v) for k, v in (_json(db, ALIASES_KEY, {}) or {}).items()}


def _rows(db) -> list[dict[str, Any]]:
    from .telegram_layout import SLOT_PREFIX
    out = []
    for card in db.list_telegram_cards(topic_id=0, card_kind="layout"):
        name = str(card.get("card_id", "")).removeprefix(SLOT_PREFIX)
        out.append({**card, "name": name, "payload": dict(card.get("payload") or {})})
    return out


def order(names: list[str]) -> list[str]:
    """Layout order: notifications, dashboard, the progress panels (oldest first, by name order of arrival is not
    known here: the caller sorts by sent_at), the review panel last of the temporaries."""
    rank = {"notifications": 0, "dashboard": 1}
    return sorted(names, key=lambda n: (rank.get(n, 3 if n == "review" else 2), n))


class Repost:
    """The rebuild, the reconcile, the sweep and the migration for one Layout (the renderer)."""

    def __init__(self, layout):
        self.layout = layout
        self.bot = layout.bot
        self.db = layout.db
        self.sleep = time.sleep           # replaceable in tests
        self._swept = 0.0

    # ---------------------------------------------------------- the plan
    def plan(self, from_slot: str) -> list[dict[str, Any]]:
        """The rows at or below `from_slot`, top to bottom. The dashboard is a row of its own here: its message is
        the bot's telegram_dashboard_id."""
        from .telegram_layout import DASHBOARD, NOTIFICATIONS
        rows = {r["name"]: r for r in _rows(self.db)}
        stored = self.db.get_setting("telegram_dashboard_id")
        if stored and stored.isdigit():
            rows[DASHBOARD] = {"name": DASHBOARD, "message_id": int(stored), "payload": {}}
        temporaries = sorted((r for n, r in rows.items() if n.startswith("progress:")),
                             key=lambda r: float(r["payload"].get("sent_at") or 0))
        # An always-on panel that does not exist yet is created in its place (a first start, a Clear history)
        # (the dashboard only when it is the one asked for: a notice alone creates the notifications panel)
        fresh = {"message_id": None, "payload": {}, "fresh": True}
        line = [rows.get(NOTIFICATIONS) or {"name": NOTIFICATIONS, **fresh}]
        if DASHBOARD in rows or from_slot == DASHBOARD:
            line.append(rows.get(DASHBOARD) or {"name": DASHBOARD, **fresh})
        line += temporaries
        if "review" in rows:
            line.append(rows["review"])
        names = [r["name"] for r in line]
        return line[names.index(from_slot):] if from_slot in names else line

    def oldest(self) -> float | None:
        """When the oldest panel copy was sent (the dashboard's from its own record)."""
        times = [float(r["payload"].get("sent_at") or 0) for r in _rows(self.db) if r["payload"].get("sent_at")]
        dash = _json(self.db, "layout-dashboard-sent", None)
        if dash:
            times.append(float(dash))
        return min(times) if times else None

    # ---------------------------------------------------------- sending one copy
    def _copy(self, row: dict[str, Any]) -> int | None:
        """Sends the row's panel again as it is now, silently; returns the new message id."""
        from . import notifications as notes
        from .telegram_layout import NOTIFICATIONS
        name = row["name"]
        if name == NOTIFICATIONS:
            text, rows = notes.render(self.db, self.layout.clock(), self.layout._run_key)
            mid = self.layout._send(text, rows)
            if mid:
                self.layout._drawn[NOTIFICATIONS] = (text, rows)
                self.layout._pin(mid)
            return mid
        if name == "review":
            return self.bot.review.copy_send()
        data = self.bot.panel_copy(row.get("message_id"), name)
        if data is None:
            return None
        data = {**data, "disable_notification": "true"}
        try:
            result = self.bot._api("sendMessage", data=data)
        except RuntimeError as e:
            if "can't parse entities" not in str(e).lower():
                raise
            data.pop("parse_mode", None)
            data["text"] = notes.plain(data["text"])
            result = self.bot._api("sendMessage", data=data)
        mid = result.get("message_id") if isinstance(result, dict) else None
        if mid:
            seen(self.db, mid)
        return mid

    def _mark_next(self, row: dict[str, Any], new: int) -> None:
        from .telegram_layout import DASHBOARD, SLOT_PREFIX
        if row.get("fresh") and row["name"] != DASHBOARD:
            self.db.put_telegram_card(SLOT_PREFIX + row["name"], 0, int(new), "layout", {"next": int(new), "fresh": True})
            return
        if row["name"] == DASHBOARD:
            self.db.set_setting("layout-dashboard-next", json.dumps({"old": row["message_id"], "next": new}))
            return
        card = self.db.get_telegram_card(SLOT_PREFIX + row["name"]) or {}
        payload = {**(card.get("payload") or {}), "next": int(new)}
        self.db.put_telegram_card(SLOT_PREFIX + row["name"], 0, int(card.get("message_id") or row["message_id"]),
                                  "layout", payload)

    # ---------------------------------------------------------- the rebuild
    def rebuild(self, from_slot: str = "notifications", urgent: bool = False) -> dict[str, int] | None:
        """Posts the layout again from `from_slot` down, in order; then swaps, deletes the old copies in one call and
        sweeps. Only the renderer does it. Returns {slot: new message id}, or None when it could not (or a failed
        re-post is still waiting out its back-off; `urgent`, the operator's /start or /clear, waits only for
        Telegram's own retry_after)."""
        layout = self.layout
        if not layout.renders_here() or self.held(layout.clock(), urgent):
            return None
        dashboard = getattr(self.bot, "_dashboard_lock", None) or contextlib.nullcontext()
        with layout._rebuild_lock, dashboard, layout._lock:   # the lock order (module docstring)
            self.recover()
            self._close_empty_review()
            plan = self.plan(from_slot)
            if not plan:
                return {}
            pager = list(self.bot.review.pager()) if any(r["name"] == "review" for r in plan) else None
            sent: dict[str, int] = {}
            for n, row in enumerate(plan):
                if n:
                    self.sleep(SEND_GAP_SECONDS)
                error = None
                try:
                    new = self._copy(row)
                except Exception as e:  # noqa: BLE001 - the old copies stay; the next try waits (back-off)
                    error, new = e, None
                if not new:
                    self._undo_sent(plan, sent)
                    self._failed(row["name"], error)
                    return None
                sent[row["name"]] = int(new)
                self._mark_next(row, new)
            old = self._swap(plan, sent)
            self._delete_old(old)
            if pager is not None and list(self.bot.review.pager()) != pager:
                self.bot.review.refresh()   # a review delivered during the re-post: the new copy shows the pager now
            self._succeeded()
            self.sweep(force=True, floor=min(old) if old else None)
            return sent

    def _undo_sent(self, plan: list[dict[str, Any]], sent: dict[str, int]) -> None:
        """A copy could not be sent: the new copies already sent go, the old ones stay (the order still holds)."""
        if sent:
            self.bot.delete_messages(list(sent.values()))
        self._clear_next()

    def _clear_next(self) -> None:
        from .telegram_layout import SLOT_PREFIX
        for r in _rows(self.db):
            if r["payload"].get("fresh"):
                self.db.delete_telegram_card(SLOT_PREFIX + r["name"])   # a panel created by the interrupted run
                continue
            if "next" in r["payload"]:
                payload = {k: v for k, v in r["payload"].items() if k != "next"}
                self.db.put_telegram_card(SLOT_PREFIX + r["name"], 0, int(r["message_id"]), "layout", payload)
        self.db.set_setting("layout-dashboard-next", "")

    def _swap(self, plan: list[dict[str, Any]], sent: dict[str, int]) -> list[int]:
        """Every row to its new copy in one transaction; the old ids are mapped to the new (taps on an old copy)."""
        from .telegram_layout import DASHBOARD, SLOT_PREFIX
        now = self.layout.clock()
        old_ids: list[int] = []
        mapping = aliases(self.db)
        with self.db._conn() as conn:
            for row in plan:
                new = sent[row["name"]]
                old = int(row["message_id"]) if row.get("message_id") else None
                if old:
                    old_ids.append(old)
                    mapping[old] = new
                if row["name"] == DASHBOARD:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                                 ("telegram_dashboard_id", str(new)))
                    conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                                 ("layout-dashboard-sent", json.dumps(now)))
                    conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                                 ("layout-dashboard-next", ""))
                    continue
                card = conn.execute("SELECT payload FROM telegram_cards WHERE card_id = ?",
                                    (SLOT_PREFIX + row["name"],)).fetchone()
                payload = {k: v for k, v in json.loads((card[0] if card else None) or "{}").items()
                           if k not in ("next", "fresh")}
                payload["sent_at"] = now
                conn.execute("UPDATE telegram_cards SET message_id = ?, payload = ?, revision = revision + 1 "
                             "WHERE card_id = ?", (new, json.dumps(payload), SLOT_PREFIX + row["name"]))
            kept = dict(list(mapping.items())[-ALIAS_LIMIT:])
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                         (ALIASES_KEY, json.dumps({str(k): v for k, v in kept.items()})))
        self.bot.adopt_copies(sent, {row["name"]: row.get("message_id") for row in plan})
        return old_ids

    def _delete_old(self, old: list[int]) -> None:
        """The old copies, in one call; a copy Telegram refuses to delete (over 48 h) is tombstoned once."""
        if not old:
            return
        try:
            self.bot._api("deleteMessages", data={"chat_id": self.bot.chat_id, "message_ids": json.dumps(old)})
            return
        except Exception as e:  # noqa: BLE001 - one by one, to find the copy that cannot go
            log.info("The old layout copies did not go in one call (%s); one at a time.", e)
        stuck = set(_json(self.db, STUCK_KEY, []) or [])
        for mid in old:
            try:
                self.bot._api("deleteMessage", data={"chat_id": self.bot.chat_id, "message_id": mid})
            except Exception as e:  # noqa: BLE001 - decided below
                reason = str(e).lower()
                if "not found" in reason or "already deleted" in reason:
                    continue
                if mid in stuck:
                    continue
                try:
                    if self.bot._is_media(mid):
                        self.bot._api("editMessageCaption", data={"chat_id": self.bot.chat_id, "message_id": mid,
                                                                  "caption": STUCK_TEXT,
                                                                  "reply_markup": json.dumps({"inline_keyboard": []})})
                    else:
                        self.bot._api("editMessageText", data={"chat_id": self.bot.chat_id, "message_id": mid,
                                                               "text": STUCK_TEXT,
                                                               "reply_markup": json.dumps({"inline_keyboard": []})})
                except Exception as e2:  # noqa: BLE001 - recorded either way, never tried again
                    log.info("Could not tombstone the old copy %s: %s", mid, e2)
                stuck.add(mid)
        self.db.set_setting(STUCK_KEY, json.dumps(sorted(stuck)[-500:]))

    # ---------------------------------------------------------- a re-post that failed: the back-off
    def _retry(self) -> dict[str, Any]:
        state = _json(self.db, RETRY_KEY, {})
        return state if isinstance(state, dict) else {}

    def held(self, now: float, urgent: bool = False) -> bool:
        """Whether a failed re-post is still waiting out its back-off (Telegram's retry_after, even when urgent)."""
        state = self._retry()
        if now < float(state.get("limited") or 0) or now < float(getattr(self.layout, "_held_until", 0) or 0):
            return True
        return not urgent and now < float(state.get("until") or 0)

    def _failed(self, slot: str, error: BaseException | None) -> None:
        """A copy could not be sent: wait before the next try; the first failure is logged, the rest quietly."""
        import re
        now = self.layout.clock()
        state = self._retry()
        fails = int(state.get("fails") or 0) + 1
        wait = RETRY_STEPS[min(fails, len(RETRY_STEPS)) - 1]
        retry_after = getattr(error, "retry_after", None)
        if retry_after is None and error is not None:
            found = re.search(r"retry after (\d+)", str(error), re.IGNORECASE)
            retry_after = float(found[1]) if found else None
        reason = f"the {slot} copy: {str(error)[:160]}" if error is not None else f"the {slot} copy was not sent"
        state.update(fails=fails, until=now + max(wait, float(retry_after or 0)), reason=reason,
                     limited=now + float(retry_after) if retry_after else 0)
        self.db.set_setting(RETRY_KEY, json.dumps(state))
        if fails == 1:
            log.warning("The re-post stopped at %s; the old layout stays and it is tried again in %.0f s: %s",
                        slot, state["until"] - now, error)
        else:
            log.debug("The re-post failed again (%d in a row) at %s: %s", fails, slot, error)

    def _succeeded(self) -> None:
        state = self._retry()
        if not state:
            return
        self.db.set_setting(RETRY_KEY, "")
        if state.get("said"):
            from . import notifications as notes
            notes.close_key(self.db, RETRY_ITEM_KEY, notes.RESOLVED)

    def _say_failure(self) -> None:
        """After 3 failures in a row, one Needs-you item says so (outside the layout's locks: it goes through the
        senders' path)."""
        state = self._retry()
        if int(state.get("fails") or 0) < RETRY_SAY_AFTER or state.get("said"):
            return
        state["said"] = True
        self.db.set_setting(RETRY_KEY, json.dumps(state))
        from . import notifications as notes
        notes.say(self.bot.notify, "⚠️ <b>Panels not posted again</b>\n\nThe bot could not post the chat's panels "
                  f"again ({html.escape(str(state.get('reason') or ''))}). The old ones stay and it keeps trying (up "
                  "to every 15 minutes); Telegram lets a bot delete its messages for 48 hours only.", group="needs",
                  section=notes.NEEDS, key=RETRY_ITEM_KEY, replace=True, record_alert=False,
                  actions=[{"text": "📊 Status", "callback_data": "cstat:"}])

    def _close_empty_review(self) -> None:
        """A review panel whose pager is empty (its last review decided or timed out) is closed, not copied."""
        from .telegram_layout import slot_message
        if not slot_message(self.db, "review"):
            return
        review = self.bot.review
        with review._lock:   # innermost (the lock order)
            if not review.pager():
                review.close()

    # ---------------------------------------------------------- interrupted, reconcile
    def recover(self) -> int:
        """A rebuild that was interrupted left new copies recorded in "next": they are deleted (the old copies
        are still the rows' messages), so no duplicate survives. Returns how many were removed."""
        leftovers = [int(r["payload"]["next"]) for r in _rows(self.db) if r["payload"].get("next")]
        # a rebuild that reached its swap but not its deletion: the old copies are known by the alias table
        dash = _json(self.db, "layout-dashboard-next", None)
        if isinstance(dash, dict) and dash.get("next"):
            leftovers.append(int(dash["next"]))
        if leftovers:
            log.warning("A re-post of the layout was interrupted; removing its %d half-sent copies.", len(leftovers))
            self.bot.delete_messages(leftovers)
            self._clear_next()
        return len(leftovers)

    def _alive(self, row: dict[str, Any]) -> bool | None:
        """Whether the row's message is still in the chat: a no-op edit of its keyboard ("not modified" is
        alive, "not found" is missing). None when it could not be told (a network error)."""
        data = {"chat_id": self.bot.chat_id, "message_id": int(row["message_id"]),
                "reply_markup": json.dumps({"inline_keyboard": self._keyboard(row)})}
        try:
            self.bot._api("editMessageReplyMarkup", data=data)
            return True
        except Exception as e:  # noqa: BLE001 - classified below
            reason = str(e).lower()
            if "not modified" in reason:
                return True
            if "not found" in reason or "message_id_invalid" in reason:
                return False
            return None

    def _keyboard(self, row: dict[str, Any]) -> list:
        from . import notifications as notes
        from .telegram_layout import NOTIFICATIONS
        if row["name"] == NOTIFICATIONS:
            return notes.render(self.db, self.layout.clock(), self.layout._run_key)[1]
        if row["name"] == "review":
            return self.bot.review.face(self.bot.review.view())[1]
        data = self.bot.panel_copy(row.get("message_id"), row["name"]) or {}
        return (json.loads(data.get("reply_markup") or "{}") or {}).get("inline_keyboard", [])

    def reconcile(self) -> str | None:
        """At start (design §7.5): finish an interrupted rebuild, check each panel, rebuild from the first missing
        one or from the top when a copy is older than LAYOUT_REPOST_FORCE_HOURS, then reap and sweep. Returns the
        slot rebuilt from, if any."""
        from .telegram_layout import NOTIFICATIONS
        if not (self.layout.is_renderer() or self.layout.take()):
            return None
        with self.bot._panel_lock:   # taps on copies replaced before this process started still find their panel
            self.bot._panel_aliases.update(aliases(self.db))
        interrupted = self.recover()
        start = None
        line = self.plan(NOTIFICATIONS)
        for row in line:
            if row.get("fresh") or self._alive(row) is False:
                start = row["name"]
                break
        oldest = self.oldest()
        if oldest is not None and self.layout.clock() - oldest >= float(config.LAYOUT_REPOST_FORCE_HOURS) * 3600:
            start = NOTIFICATIONS
        if interrupted and start is None:
            start = NOTIFICATIONS          # it was interrupted for a reason: the whole layout again
        if start is not None:
            self.rebuild(start)
        if hasattr(self.bot, "reap_progress_panels"):
            self.bot.reap_progress_panels()
        self.sweep(force=True)
        return start

    # ---------------------------------------------------------- the age rule
    def idle(self, now: float) -> bool:
        """No tap or message for LAYOUT_IDLE_MINUTES, no input waiting, no review page opened in the last 5 min."""
        last = float(getattr(self.bot, "_last_activity", 0.0) or 0.0)
        if now - last < float(config.LAYOUT_IDLE_MINUTES) * 60:
            return False
        waiting = self.bot._awaiting
        if waiting and float(waiting[1]) > now:
            return False
        from .telegram_layout import slot
        review = (slot(self.db, "review") or {}).get("payload") or {}
        return now - float(review.get("opened") or 0) >= REVIEW_LOOK_SECONDS

    def due(self, now: float) -> bool:
        """The age rule (design §5.3): idle after 30 h, forced at 40 h, so every copy stays deletable."""
        oldest = self.oldest()
        if oldest is None:
            return False
        age = now - oldest
        if age >= float(config.LAYOUT_REPOST_FORCE_HOURS) * 3600:
            return True
        return age >= float(config.LAYOUT_REPOST_IDLE_HOURS) * 3600 and self.idle(now)

    def tick(self, now: float) -> bool:
        """The renderer's round: the age rule, then the hourly sweep."""
        self._say_failure()
        if self.due(now) and not self.held(now):
            log.info("Re-posting the layout (its oldest copy is %.1f h old).", (now - (self.oldest() or now)) / 3600)
            self.rebuild()
            return True
        if now - float(self.db.get_setting(SWEPT_KEY) or 0) >= SWEEP_SECONDS:
            self.sweep(force=True)
        return False

    # ---------------------------------------------------------- the gap sweep
    def sweep(self, force: bool = False, below: int | None = None, depth: int = SWEEP_LIMIT,
              floor: int | None = None) -> int:
        """Deletes every message that is not a panel between the oldest panel and the newest id seen (or the
        `depth` ids below `below`), except an alert still due and a message received in the last 30 s."""
        from .telegram_layout import panel_ids
        now = self.layout.clock()
        if not force and now - float(self.db.get_setting(SWEPT_KEY) or 0) < SWEEP_SECONDS:
            return 0
        self.db.set_setting(SWEPT_KEY, str(now))
        keep = set(panel_ids(self.db))
        stored = self.db.get_setting("telegram_dashboard_id")
        if stored and stored.isdigit():
            keep.add(int(stored))
        keep |= {int(a["id"]) for a in self.layout._due_alerts()}
        keep |= set(_json(self.db, STUCK_KEY, []) or [])
        recent = getattr(self.bot, "_recent_ids", {}) or {}
        keep |= {mid for mid, at in recent.items() if now - at < RECENT_SECONDS}
        if below is not None:
            ids = list(range(int(below) - 1, max(0, int(below) - depth) - 1, -1))
        else:
            # From the newest id seen down to the oldest panel (or, after a rebuild, its oldest old copy; else where
            # the last sweep stopped), at most `depth` ids.
            newest = int(self.db.get_setting(NEWEST_KEY) or 0)
            done = int(self.db.get_setting(SWEPT_UPTO_KEY) or 0)
            lowest = floor if floor is not None else (max(done, min(keep)) if keep and done else min(keep) if keep else newest)
            if not newest or newest <= lowest:
                return 0
            ids = list(range(newest, max(lowest - 1, newest - depth), -1))
            self.db.set_setting(SWEPT_UPTO_KEY, str(newest))
        ids = [m for m in ids if m > 0 and m not in keep]
        if ids:
            self.bot.delete_messages(ids)
        return len(ids)

    # ---------------------------------------------------------- the migration
    def migrate(self) -> dict[str, int] | None:
        """The first start of this build (design §13): the layout is posted at the bottom (the reviews waiting go
        into the review panel), every known message of the earlier chat is deleted, then 1,000 ids below the new
        layout are swept. Runs once."""
        if self.db.get_setting(MIGRATED_KEY):
            return None
        if not (self.layout.is_renderer() or self.layout.take()):
            return None
        self.db.set_setting(MIGRATED_KEY, str(self.layout.clock()))
        from .telegram_layout import panel_ids
        for run in self.bot.pending_legacy_reviews():
            try:
                self.bot.redeliver_review(run)
            except Exception as e:  # noqa: BLE001 - it stays in 🎬 Reviews; Resend delivers it
                log.warning("Could not move the review of %s into the review panel: %s", run.get("run_id"), e)
        before = set(panel_ids(self.db))
        sent = self.rebuild() or {}
        self._notices_to_items()   # before their messages are deleted (after the layout exists: no extra post)
        layout = set(panel_ids(self.db))
        stored = self.db.get_setting("telegram_dashboard_id")
        if stored and stored.isdigit():
            layout.add(int(stored))
        known = [m for m in self.bot.legacy_message_ids() if m not in layout and m not in before | layout]
        removed = self.bot.delete_messages(known) if known else 0
        first = min(layout) if layout else None
        swept = self.sweep(force=True, below=first, depth=SWEEP_LIMIT) if first else 0
        refused = len(known) - int(removed or 0)
        if refused > 0 or self.bot.chat_is_older_than_48h():
            from . import notifications as notes
            notes.say(self.bot.notify, f"🧹 <b>Older messages stay</b>\n\n{OLD_MESSAGES_TEXT}", group="info",
                      section=notes.INFO, key="old-messages", replace=True, record_alert=False)
        return {"posted": len(sent), "known_deleted": int(removed or 0), "swept": swept, "refused": max(0, refused)}

    def _notices_to_items(self) -> int:
        """The earlier chat's notices still in it and young enough to be deleted (persistent alerts, and transient
        ones not yet expired: a posting error, Publish failed, a worker that ended, a deadline) become items first,
        in their senders' own words and silently (they rang when they were sent). Needs you or info by
        needs_you(); a Needs-you item keeps the buttons the notice carried when they were recorded, else offers the
        run's Details (its message mapped to a run) or 📊 Status. Returns how many."""
        from . import notifications as notes
        made = 0
        for notice in self.bot.legacy_notices():
            emoji, title, subject, body = notes.parse(notice["text"])
            needs = needs_you(emoji, persistent=notice.get("kind", "persistent") == "persistent")
            run = notice.get("run")
            own = notes.actions_from([list(notice.get("actions") or [])])
            actions = (own or ([{"text": "Details", "callback_data": f"chist:{self.db.run_key(run)}"}] if run else
                               [{"text": "📊 Status", "callback_data": "cstat:"}])) if needs else own
            # silent: each rang once in the earlier chat when it was sent
            notes.post(self.db, notes.NEEDS if needs else notes.INFO, f"earlier:{notice['id']}", title, body,
                       emoji=emoji, subject=subject, actions=actions, run=run, replace=True, ring=False)
            made += 1
        return made

    # ---------------------------------------------------------- stop and back (design §7.3)
    def stopping(self) -> None:
        """A clean stop: the dashboard's footer and the service-state item say the bot is stopped."""
        from . import notifications as notes
        from .local_time import show
        now = self.layout.clock()
        self.db.set_setting(STOPPED_KEY, json.dumps({"at": now, "planned": True}))
        line = f"⏸️ Bot stopped {show(now, '%H:%M')} · buttons work again when it is back"
        notes.say(self.bot.notify, f"⏸️ <b>Bot stopped</b>\n\n{line.split(' · ', 1)[1].capitalize()}",
                  group="info", section=notes.INFO, key="service-state", replace=True, record_alert=False)
        self.bot.stopped_footer(line)   # under the dashboard lock, released before _lock is taken (lock order)
        try:
            with self.layout._lock:
                self.layout.draw_notifications(now, force=True)
        except Exception as e:  # noqa: BLE001 - the item is recorded; the next start draws it
            log.debug("Notifications panel not drawn at the stop: %s", e)

    def back(self, last_poll: float | None) -> None:
        """At start: the service-state item says the bot is back and how long it was down. A planned stop rings
        nothing; an unplanned outage of more than 15 minutes is a Needs-you item (the schedule may have missed a run)."""
        from . import notifications as notes
        now = self.layout.clock()
        stopped = _json(self.db, STOPPED_KEY, {}) or {}
        since = float(stopped.get("at") or last_poll or 0)
        if not since or now - since < 60:
            self.db.set_setting(STOPPED_KEY, "")
            return
        planned = bool(stopped.get("planned"))
        down = int(now - since)
        hours, minutes = divmod(down // 60, 60)
        said = (f"{hours} h {minutes} min" if hours else f"{minutes} min")
        unplanned_long = not planned and down > 15 * 60
        # Said through the senders' path (notifications.say, bot.notify: UI-01a); a Needs-you item rings, info does not
        notes.say(self.bot.notify, "🟢 <b>Back online</b>\n\n" + f"Was down {said}."
                  + ("" if planned else " It stopped without a clean shutdown; check 🗓️ Posting and 📜 History for a "
                                        "run the schedule may have missed."),
                  group="needs" if unplanned_long else "info", section=notes.NEEDS if unplanned_long else notes.INFO,
                  key="service-state", replace=True, record_alert=False,
                  actions=[{"text": "🗓️ Posting", "callback_data": "cposts:"}] if unplanned_long else None)
        self.db.set_setting(STOPPED_KEY, "")
