"""
src/notifications.py
====================
The pinned notifications panel's items (UI-01, design docs/design/telegram-clean-chat-2026-11-11.md §6.1, §8, §9).

Every sender writes an item here; nothing a sender says becomes a message of its own. The renderer
(telegram_layout) draws the items into the one pinned panel: a list page (header with the counts, Needs you above
Info, newest first, numbered items) or one item's page, switched by editing the same message.

An item is **Needs you** when nothing happens until the operator acts, or something is wrong that only the operator
can fix; everything else is **info**. Items with the same `dedupe_key` that are still open are one item (the latest
text, a count shown as ×n); replaceable keys (a weekly digest, the release notice) replace the older text instead.
The panel shows the last 50 items; Needs-you items are never dropped; every row stays 90 days for 📊 Status ›
🗒️ Notices.
"""
from __future__ import annotations

import hashlib
import html
import inspect
import json
import logging
import re
import sqlite3
import time
from collections.abc import Sequence
from typing import Any, Self

log = logging.getLogger(__name__)
NEEDS, INFO = "needs", "info"
KEEP = 50                  # items the panel shows; the oldest info items go first, Needs you never
RETAIN_DAYS = 90           # every row's life in the database (the Notices screen), as alerts had
PAGE_ITEMS = 5             # items a list page holds
PAGE_CHARS = 3600          # characters a list page holds after entities (Telegram's limit is 4,096)
TEXT_LIMIT = 4096
TITLE_LIMIT = 40           # a title shows whole on the list
LINES_LIMIT = 140          # the two plain lines under a title on the list
QUOTE_LIMIT = 1200         # an item page's folded text (telegram_ui.quote)
PLAIN_BODY = 280           # an item page shows a body this short as it is; a longer one folds
NOTE_LIMIT = 200           # why an action was not done, on the item's page
PUSH_REPEAT_SECONDS = 6 * 3600   # an open item's key rings again only after this
UNDO_SECONDS = 30          # Dismiss all info can be undone this long
RETURN_SECONDS = 120       # the panel returns to list page 1 after this without a tap
INFO_SECONDS = 24 * 3600   # an info item leaves the panel by itself this long after it was last written (UI-01g)
BACK_ONLINE_SECONDS = 12 * 3600   # ... and the info "Back online" after this (a night), or once Posting or History opens
CHECK_FRESH_SECONDS = 120  # a condition's last check counts as "still true" this long (it is checked every 20 s)
CHECK_SHOWN_SECONDS = 300  # the panel is written again for a newer check time alone once the one shown is this old
SERVICE_KEY = "service-state"   # "Bot stopped" and "Back online": one item, the newer replaces the older
STOPPED_TITLE = "Bot stopped"   # ... the one that leaves only when the bot is back, never by time
# Keys whose newer item replaces the older text instead of counting it (design §6.1.3).
REPLACEABLE = ("digest:", "maintenance:", "policy:", SERVICE_KEY, "release")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
REVISION_KEY = "notifications-rev"
VIEW_KEY = "notifications-view"
UNDO_KEY = "notifications-undo"
SEEN_NEEDS_KEY = "notifications-needs-seen"
FAIL_PREFIX = "fail:"       # a run's failure item: fail:<run>
LATE_PREFIX = "cond:late:"  # the late-failure condition for a run (notice_conditions)
DISMISS = "x"              # the verb of an item page's Dismiss button: n:<id>:x
OPEN, HANDLED, DISMISSED, DROPPED, RESOLVED = "open", "handled", "dismissed", "dropped", "resolved"
EXPIRED = "expired"        # an info item past its time: out of the panel, on Notices for RETAIN_DAYS (UI-01g)
# What an item no longer in the panel says on its page, and to a tap on one of its buttons (the same words)
GONE_WORDS = {EXPIRED: "This is older info: it moved to 📊 Status › 🗒️ Notices.", "": "This has been dealt with."}
CONDITION_PREFIX = "cond:"  # a condition item's key (notice_conditions): derived from the state, not sent

_ready: set = set()


def ensure(db) -> None:
    """The items table (additive: a rollback to an earlier release leaves it unused)."""
    path = str(getattr(db, "db_path", id(db)))
    if path in _ready:
        return
    with db._conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL,
            section TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            actions TEXT NOT NULL DEFAULT '[]',
            run_id TEXT,
            count INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL DEFAULT 'open',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            pushed_at REAL,
            push_pending INTEGER NOT NULL DEFAULT 0,
            closed_at REAL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_key ON notifications(dedupe_key, state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_state ON notifications(state, updated_at)")
        have = {r[1] for r in conn.execute("PRAGMA table_info(notifications)")}
        for column, decl in (("alert", "TEXT"), ("cond_count", "INTEGER"),   # UI-01b: condition items
                             ("note", "TEXT"),    # why the item's last action was not done (shown on its page)
                             ("checked_at", "REAL")):   # UI-01g: when a condition item was last found to hold
            if column not in have:
                conn.execute(f"ALTER TABLE notifications ADD COLUMN {column} {decl}")
    _ready.add(path)


def _row(row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["actions"] = json.loads(item.get("actions") or "[]")
    except ValueError:
        item["actions"] = []
    return item


def bump(db) -> int:
    """A new revision of the panel's content: the renderer draws when it changes."""
    rev = int(db.get_setting(REVISION_KEY) or 0) + 1
    db.set_setting(REVISION_KEY, str(rev))
    return rev


def revision(db) -> int:
    return int(db.get_setting(REVISION_KEY) or 0)


def replaceable(key: str) -> bool:
    return any(key == k or (k.endswith(":") and key.startswith(k)) for k in REPLACEABLE)


def post(db, section: str, key: str, title: str, body: str = "", *, emoji: str = "", subject: str = "",
         actions: Sequence[dict[str, str]] = (), run: str | None = None, replace: bool = False,
         now: float | None = None, ring: bool | None = None, alert: str | None = None,
         cond_count: int | None = None, keep_time: bool = False) -> dict[str, Any]:
    """Writes one item and bumps the panel's revision; it never sends. Returns {"id", "push"}: push is True when
    the item should ring (a Needs-you item added, reopened or escalated, its key quiet for 6 h). `ring` overrides
    that rule for an item that is still open (a condition whose count rose rings, an unchanged one does not);
    `alert` is the alert's own line when it differs from the title. `keep_time` leaves an open item's time as it
    was: for a writer that only takes something away (an Undo whose time is up), so the item neither looks new
    nor starts its day in the panel again (UI-01g)."""
    if section not in (NEEDS, INFO):
        raise ValueError(f"a notification needs a section (needs or info), not {section!r}")
    ensure(db)
    now = time.time() if now is None else now
    key = key or auto_key(title, subject)
    payload = json.dumps([{"text": str(a.get("text", "")), "callback_data": str(a.get("callback_data", ""))}
                          for a in actions if a.get("callback_data")])
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE dedupe_key = ? AND state = 'open' ORDER BY id DESC LIMIT 1",
                           (key,)).fetchone()
        if row is None and key.startswith(FAIL_PREFIX):
            # One failure is one item: the late-failure condition already showing this run becomes the run's
            # failure item (the same item, so it never rings twice; the condition skips a run with a fail: item).
            row = conn.execute("SELECT * FROM notifications WHERE dedupe_key = ? AND state = 'open' ORDER BY id DESC "
                               "LIMIT 1", (LATE_PREFIX + key[len(FAIL_PREFIX):],)).fetchone()
            if row is not None:
                conn.execute("UPDATE notifications SET dedupe_key = ?, cond_count = NULL WHERE id = ?", (key, row["id"]))
        if row:
            item = _row(row)
            escalated = item["section"] == INFO and section == NEEDS
            kept_section = NEEDS if NEEDS in (item["section"], section) else INFO
            count = 1 if (replace or replaceable(key)) else int(item["count"]) + 1
            push = kept_section == NEEDS and (escalated or not item["pushed_at"]
                                              or now - float(item["pushed_at"]) >= PUSH_REPEAT_SECONDS)
            if ring is not None:
                push = kept_section == NEEDS and (ring or escalated)
            conn.execute("""UPDATE notifications SET section = ?, emoji = ?, title = ?, subject = ?, body = ?, actions = ?,
                            run_id = COALESCE(?, run_id), count = ?, updated_at = ?, pushed_at = ?,
                            push_pending = MAX(push_pending, ?), alert = ?, cond_count = ?, note = NULL WHERE id = ?""",
                         (kept_section, emoji, title, subject, body, payload, run, count,
                          float(item["updated_at"]) if keep_time else now,
                          now if push else item["pushed_at"], int(push), alert, cond_count, item["id"]))
            item_id = item["id"]
        else:
            push = section == NEEDS and ring is not False
            cur = conn.execute("""INSERT INTO notifications (dedupe_key, section, emoji, title, subject, body, actions,
                                  run_id, count, state, created_at, updated_at, pushed_at, push_pending, alert, cond_count)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'open', ?, ?, ?, ?, ?, ?)""",
                               (key, section, emoji, title, subject, body, payload, run, now, now, now if push else None,
                                int(push), alert, cond_count))
            item_id = cur.lastrowid
        _retain(conn, now)
    bump(db)
    return {"id": item_id, "push": push}


def auto_key(title: str, subject: str = "") -> str:
    """A key for a sender that gives none: the same title and subject are the same item (×n)."""
    return "auto:" + hashlib.sha1(f"{title}\n{subject}".encode()).hexdigest()[:16]


def _retain(conn, now: float) -> None:
    """The panel keeps the last KEEP items: the oldest info items beyond go to Notices; Needs you never does."""
    needs = conn.execute("SELECT COUNT(*) FROM notifications WHERE state = 'open' AND section = 'needs'").fetchone()[0]
    room = max(0, KEEP - needs)
    extra = conn.execute("""SELECT id FROM notifications WHERE state = 'open' AND section = 'info'
                            ORDER BY updated_at DESC, id DESC LIMIT -1 OFFSET ?""", (room,)).fetchall()
    if extra:
        conn.executemany("UPDATE notifications SET state = 'dropped', closed_at = ? WHERE id = ?",
                         [(now, r[0]) for r in extra])
    conn.execute("DELETE FROM notifications WHERE state <> 'open' AND COALESCE(closed_at, updated_at) < ?",
                 (now - RETAIN_DAYS * 86400,))


def pending_pushes(db) -> list[dict[str, Any]]:
    """Open items waiting for their push alert."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE push_pending = 1 AND state = 'open' ORDER BY id").fetchall()
    return [_row(r) for r in rows]


def clear_pushes(db, ids: Sequence[int]) -> None:
    ensure(db)
    with db._conn() as conn:
        conn.executemany("UPDATE notifications SET push_pending = 0 WHERE id = ?", [(int(i),) for i in ids])
        # An item closed before its alert went never rings later.
        conn.execute("UPDATE notifications SET push_pending = 0 WHERE push_pending = 1 AND state <> 'open'")


def has_key(db, key: str) -> bool:
    """Whether any item, open or closed, was ever written under this key."""
    ensure(db)
    with db._conn() as conn:
        return conn.execute("SELECT 1 FROM notifications WHERE dedupe_key = ? LIMIT 1", (key,)).fetchone() is not None


def has_open(db, key: str) -> bool:
    ensure(db)
    with db._conn() as conn:
        return conn.execute("SELECT 1 FROM notifications WHERE dedupe_key = ? AND state = 'open' LIMIT 1",
                            (key,)).fetchone() is not None


def open_item(db, key: str) -> dict[str, Any] | None:
    """The open item of a key, or None: for a sender that brings its own item up to date (UI-01g)."""
    ensure(db)
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE dedupe_key = ? AND state = 'open' ORDER BY id DESC LIMIT 1",
                           (key,)).fetchone()
    return _row(row) if row else None


def checked(db, keys: Sequence[str], now: float | None = None) -> None:
    """The condition items of these keys were found to hold just now (notice_conditions.sync): their open items
    say "still true · checked HH:MM". Not a new revision: the renderer compares what it would draw."""
    keys = [k for k in keys if k]
    if not keys:
        return
    ensure(db)
    with db._conn() as conn:
        conn.execute(f"UPDATE notifications SET checked_at = ? WHERE state = 'open' AND dedupe_key IN "
                     f"({', '.join('?' * len(keys))})", (time.time() if now is None else now, *keys))


def expire(db, now: float | None = None, held=None) -> int:
    """Info leaves the panel by itself (UI-01g, operator 2026-11-14): an info item INFO_SECONDS after it was last
    written, the info "Back online" after BACK_ONLINE_SECONDS. Its row stays on Notices for RETAIN_DAYS, with
    Restore. Never a Needs-you item (it stays until handled or its condition clears), never a condition item
    (it leaves when its condition does), and never "Bot stopped" (it is true until the bot is back: only the start
    takes it away, whoever draws the panel meanwhile). `held(item)` keeps an item whose action still has time of
    its own (an Approve's Undo before its slot). An item written again since it was read here is left: the update
    asks again that it is as old as when it was chosen. Returns how many left."""
    ensure(db)
    now = time.time() if now is None else now
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE state = 'open' AND section = 'info' AND updated_at <= ?",
                            (now - min(INFO_SECONDS, BACK_ONLINE_SECONDS),)).fetchall()
    gone = []
    for item in map(_row, rows):
        key = str(item.get("dedupe_key") or "")
        if key.startswith(CONDITION_PREFIX) or (key == SERVICE_KEY and item.get("title") == STOPPED_TITLE):
            continue
        limit = BACK_ONLINE_SECONDS if key == SERVICE_KEY else INFO_SECONDS
        if now - float(item["updated_at"]) < limit:
            continue
        try:
            kept = held is not None and bool(held(item))
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as e:
            # its Undo could not be looked up (the database, or a record that does not read): not known, so the
            # item stays; a live Undo is never taken away by a failed lookup, and the next round asks again
            log.warning("Could not look up the Undo of item %s; it stays in the panel: %s", item["id"], e)
            kept = True
        if not kept:
            gone.append((item["id"], now - limit))
    left = 0
    if gone:
        with db._conn() as conn:
            for item_id, cutoff in gone:
                left += conn.execute("UPDATE notifications SET state = ?, closed_at = ? WHERE id = ? AND state = 'open' "
                                     "AND section = 'info' AND updated_at <= ?", (EXPIRED, now, item_id, cutoff)).rowcount
    if left:
        bump(db)
    return left


def service_seen(db) -> int:
    """The operator opened 🗓 Posting or 📚 History: "Back online" asked for exactly that look (or only said the bot
    is back), so its item is dealt with (UI-01g). Returns how many items closed."""
    return close_key(db, SERVICE_KEY, HANDLED)


def get(db, item_id: int) -> dict[str, Any] | None:
    ensure(db)
    with db._conn() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (int(item_id),)).fetchone()
    return _row(row) if row else None


def open_items(db) -> list[dict[str, Any]]:
    """The panel's items in order: Needs you (newest first), then info (newest first)."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("""SELECT * FROM notifications WHERE state = 'open'
                               ORDER BY CASE section WHEN 'needs' THEN 0 ELSE 1 END, updated_at DESC, id DESC""").fetchall()
    return [_row(r) for r in rows]


def close(db, item_id: int, state: str = HANDLED, now: float | None = None) -> bool:
    """Takes an item out of the panel (handled, dismissed or resolved); its row stays for Notices."""
    ensure(db)
    with db._conn() as conn:
        cur = conn.execute("UPDATE notifications SET state = ?, closed_at = ? WHERE id = ? AND state = 'open'",
                           (state, time.time() if now is None else now, int(item_id)))
    if cur.rowcount:
        bump(db)
    return bool(cur.rowcount)


def close_key(db, key: str, state: str = RESOLVED) -> int:
    """Closes the open item of a key (a condition that cleared, a failure re-rendered)."""
    ensure(db)
    with db._conn() as conn:
        cur = conn.execute("UPDATE notifications SET state = ?, closed_at = ? WHERE dedupe_key = ? AND state = 'open'",
                           (state, time.time(), key))
    if cur.rowcount:
        bump(db)
    return cur.rowcount


def dismiss_all_info(db, now: float | None = None) -> list[int]:
    """Dismiss all info: every open info item, on every page; Needs you is never cleared in bulk. Undo for 30 s."""
    ensure(db)
    now = time.time() if now is None else now
    with db._conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM notifications WHERE state = 'open' AND section = 'info'")]
        conn.executemany("UPDATE notifications SET state = 'dismissed', closed_at = ? WHERE id = ?", [(now, i) for i in ids])
    if ids:
        db.set_setting(UNDO_KEY, json.dumps({"ids": ids, "until": now + UNDO_SECONDS}))
        bump(db)
    return ids


def undo_state(db, now: float | None = None) -> dict[str, Any] | None:
    """The Dismiss all info that can still be undone, or None."""
    try:
        state = json.loads(db.get_setting(UNDO_KEY) or "null")
    except ValueError:
        return None
    now = time.time() if now is None else now
    return state if state and state.get("ids") and float(state.get("until", 0)) > now else None


def undo_dismiss_all(db, now: float | None = None) -> int:
    state = undo_state(db, now)
    db.set_setting(UNDO_KEY, "null")
    if not state:
        return 0
    ensure(db)
    with db._conn() as conn:
        ids = [int(i) for i in state["ids"]]
        cur = conn.executemany("UPDATE notifications SET state = 'open', closed_at = NULL WHERE id = ? AND state = 'dismissed'",
                               [(i,) for i in ids])
        _retain(conn, time.time() if now is None else now)
    bump(db)
    return cur.rowcount


def recent(db, days: int = RETAIN_DAYS, limit: int = 30) -> list[dict[str, Any]]:
    """Every item of the last `days`, newest first, open or not (📊 Status › 🗒️ Notices)."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM notifications WHERE updated_at >= ? ORDER BY updated_at DESC, id DESC LIMIT ?",
                            (time.time() - days * 86400, limit)).fetchall()
    return [_row(r) for r in rows]


def note(db, item_id: int, text: str) -> None:
    """Why the item's action was not done (its handler's own words), shown on its page until the sender writes the
    item again or an action on it is done; the item stays open (design §10.1: "If not started: the item's body says
    why")."""
    ensure(db)
    with db._conn() as conn:
        conn.execute("UPDATE notifications SET note = ? WHERE id = ?", (clip(plain(text), NOTE_LIMIT) or None,
                                                                         int(item_id)))
    bump(db)


def restore(db, item_id: int) -> bool:
    """Puts a dismissed, dropped, expired or handled item back in the panel (Notices › Restore); an info item's day
    in the panel starts again."""
    ensure(db)
    with db._conn() as conn:
        cur = conn.execute("""UPDATE notifications SET state = 'open', closed_at = NULL, updated_at = ?
                              WHERE id = ? AND state IN ('dismissed', 'dropped', 'handled', 'expired')""",
                           (time.time(), int(item_id)))
        if cur.rowcount:
            _retain(conn, time.time())
    if cur.rowcount:
        bump(db)
    return bool(cur.rowcount)


# ------------------------------------------------------------------ a sender's message, as an item
_TAG = re.compile(r"<[^>]+>")
_LEAD = re.compile(r"^([^\w<&\"'(\[]+)\s*")
NAVIGATION = {"cdash:", "close:", "done:", "expired:"}


def plain(text: str) -> str:
    return html.unescape(_TAG.sub("", str(text or "")))


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:·(—-")
    return (cut or text[:limit - 1]) + "…"


_BOLD_HEAD = re.compile(r"^<b>(.+?)</b>(.*)$", re.DOTALL)


def parse(text: str) -> tuple[str, str, str, str]:
    """(emoji, title, subject, body HTML) from a sender's HTML message. The first line is the title, its leading
    emoji apart: its bold part when it starts bold ("❌ <b>Run failed</b>"), else the words before a colon
    ("⚠️ Scheduled posting hit an error: …"), else the line cut at a word. A "· <name>" after the bold part is the
    subject ("📮 <b>Posted</b> · Sigiriya"); any other remainder of the line opens the body, so no word is lost."""
    text = str(text or "").strip()
    first, _, rest = text.partition("\n")
    first = first.strip()
    match = _LEAD.match(plain(first))
    emoji = match[1].strip() if match else ""
    head = first[len(emoji):].strip() if emoji and first.startswith(emoji) else first
    body = rest.strip()
    subject, lead = "", ""
    bold = _BOLD_HEAD.match(head)
    if bold:
        title = plain(bold[1]).strip().rstrip(":").strip()
        tail = bold[2].strip()
        if tail.startswith("·") and len(plain(tail).lstrip("· ").strip()) <= TITLE_LIMIT and "<a " not in tail:
            subject = plain(tail).lstrip("· ").strip()
        elif tail:
            lead = tail.lstrip(",:;·—- ").strip()
    else:
        line = plain(head).strip()
        name, colon, after = line.partition(": ")
        if colon and len(name) <= TITLE_LIMIT:
            title, lead = name, html.escape(after.strip())
        else:
            title = line.rstrip(":")
    if len(title) > TITLE_LIMIT:
        lead = html.escape(title) + (" " + lead if lead else "")
        title = clip(title, TITLE_LIMIT)
    if lead:
        body = lead + ("\n" + body if body else "")
    return emoji, title or "Notice", subject, body


def actions_from(buttons: Any) -> list[dict[str, str]]:
    """A sender's buttons as an item's actions: navigation (🏠, Close) is dropped, the panel has its own."""
    out: list[dict[str, str]] = []
    rows = buttons if isinstance(buttons, list) else []
    for row in rows:
        for b in (row if isinstance(row, list) else [row]):
            if isinstance(b, dict) and b.get("callback_data") and b["callback_data"] not in NAVIGATION:
                out.append({"text": str(b.get("text", "")), "callback_data": str(b["callback_data"])})
    return out


class Outcome(str):
    """A handler's reply that also says whether its action was done (Re-render started, updates being applied).
    An item's action is judged by `done`, never by the reply's words: a topic called "Notre-Dame" is not a
    refusal. It is a str, so every earlier reader of the reply (a toast, a dashboard line, a test) is unchanged."""

    done: bool

    def __new__(cls, text: str, done: bool) -> Self:
        reply = super().__new__(cls, text)
        reply.done = bool(done)
        return reply


ITEM_FIELDS = ("section", "key", "subject", "run", "replace", "actions")   # the clean chat's, for say()


def say(send, text: str, **kw: Any) -> Any:
    """Calls a notice sender (TelegramApprovalBot.notify, a coordinator's _alert, or a test's stand-in for either)
    with the text and only the keywords it takes; None values are left out. The clean chat's item fields (section,
    key, run, replace, actions: UI-01) are for the real bot; a stand-in that records only the text still gets it,
    and an error inside the sender is never mistaken for a signature mismatch. With the clean chat off the item
    fields are left out altogether: the earlier chat's senders are called exactly as before UI-01."""
    from . import config
    clean = bool(getattr(config, "TELEGRAM_CLEAN_CHAT", False))
    kw = {k: v for k, v in kw.items() if v is not None and (clean or k not in ITEM_FIELDS)}
    try:
        params = list(inspect.signature(send).parameters.values())
    except (TypeError, ValueError):   # a callable with no readable signature: the text alone
        return send(text)
    if any(p.kind is p.VAR_KEYWORD for p in params):
        return send(text, **kw)
    names = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    return send(text, **{k: v for k, v in kw.items() if k in names})


def failure_actions(db, run_id: str) -> list[dict[str, str]]:
    """A failed run's item actions (design §10.2, the failure card's own choice): Re-render once its script passed
    the fact check (src/resume_points.py), else Back to the queue; then Details (its review history)."""
    from .config import OUTPUT_DIR
    from .resume_points import worth_resuming
    try:   # it is called while a failure is being reported: it never raises over the failure it describes
        run = db.get_run(run_id) or {}
        late = bool(run) and worth_resuming(OUTPUT_DIR / run_id, run)
        key = db.run_key(run_id) if run else run_id
    except Exception:  # noqa: BLE001
        run, late, key = {}, False, run_id
    out = []
    if late:
        out.append({"text": "Re-render", "callback_data": f"rerender:{key}"})
    elif run.get("topic_id"):
        out.append({"text": "Back to the queue", "callback_data": f"requeue:{run['topic_id']}"})
    out.append({"text": "Details", "callback_data": f"chist:{key}"})
    return out


def links_from(buttons: Any) -> list[str]:
    """Link buttons (url) as body lines: an item page's buttons only act or open screens."""
    out = []
    for row in (buttons if isinstance(buttons, list) else []):
        for b in (row if isinstance(row, list) else [row]):
            if isinstance(b, dict) and b.get("url") and not b.get("callback_data"):
                out.append(f'<a href="{html.escape(str(b["url"]), quote=True)}">{html.escape(str(b.get("text", "Open")))}</a>')
    return out


# ------------------------------------------------------------------ drawing
def tg_len(text: str) -> int:
    """Characters as Telegram counts them: after entities, in UTF-16 units."""
    return len(plain(text).encode("utf-16-le")) // 2


def header(needs: int, info: int, dismissed: int = 0) -> str:
    """The first line, which the pin strip shows. The verb agrees with the count."""
    if dismissed:
        lead = f"<b>{needs} {'needs' if needs == 1 else 'need'} you</b>" if needs else "Nothing needs you"
        return f"🔔 {lead} · {dismissed} info dismissed"
    if not needs and not info:
        return "🔔 <b>All clear</b>"
    if not needs:
        return f"🔔 Nothing needs you · {info} info"
    lead = f"<b>{needs} {'needs' if needs == 1 else 'need'} you</b>"
    return f"🔔 {lead} · {info} info" if info else f"🔔 {lead}"


def when(ts: float, now: float) -> str:
    """A moment as a Telegram date-time entity (relative within 12 h, weekday and time after), with the
    operator-zone time as its text for clients that do not draw the entity."""
    from .local_time import show
    recent_ = now - ts < 12 * 3600
    fallback = show(ts, "%H:%M" if recent_ else "%a %H:%M")
    return f'<tg-time unix="{int(ts)}" format="{"r" if recent_ else "wt"}">{html.escape(fallback)}</tg-time>'


def checked_line(item: dict[str, Any], now: float) -> str:
    """A condition item's time (UI-01g, operator 2026-11-14): not when it was first raised, which makes a current
    item look old, but when it was last found to hold: "still true · checked Sat 14:32". The weekday is always
    there (the entity's format is the one the panel already uses for a day and a time), so a panel nobody has
    drawn for days cannot pass for today's. A check older than CHECK_FRESH_SECONDS claims nothing: "not checked
    since …" (the conditions could not be derived, or one is still being counted)."""
    from .local_time import show
    at = float(item.get("checked_at") or item["updated_at"])
    minute = int(at) // 60 * 60   # the minute it shows: a check every 20 s is then at most one edit a minute
    stamp = f'<tg-time unix="{minute}" format="wt">{html.escape(show(minute, "%a %H:%M"))}</tg-time>'
    return f"still true · checked {stamp}" if now - at < CHECK_FRESH_SECONDS else f"not checked since {stamp}"


_CHECKED = re.compile(r'(still true · checked <tg-time unix=")(\d+)("[^>]*>)[^<]*(</tg-time>)')


def check_times(text: str) -> list[int]:
    """The check times a drawn panel shows on its "still true" lines (each is the minute of a real check)."""
    return [int(m[2]) for m in _CHECKED.finditer(str(text or ""))]


def only_check_times_differ(drawn: str, text: str) -> bool:
    """Whether a new render of the panel differs from the one drawn in nothing but the time of the last check on
    its "still true" lines (operator ruling, 2026-11-14): such a render is drawn only once the time on screen is
    CHECK_SHOWN_SECONDS old (telegram_layout.Layout.draw_notifications). Everything else is a real change and is
    drawn at once: an item added, closed or written again, a count, a page, and "still true" becoming "not checked
    since" (that line is not matched here, so a check that stopped is said within CHECK_FRESH_SECONDS as before)."""
    blank = r"\g<1>\g<3>\g<4>"
    return drawn != text and _CHECKED.sub(blank, str(drawn or "")) == _CHECKED.sub(blank, str(text or ""))


def _meta(item: dict[str, Any], now: float, run_key=None) -> str:
    condition = str(item.get("dedupe_key") or "").startswith(CONDITION_PREFIX)
    parts = [checked_line(item, now) if condition else when(float(item["updated_at"]), now)]
    if item.get("run_id"):
        parts.append(f"run {html.escape(str(run_key(item['run_id']) if run_key else item['run_id']))}")
    if int(item.get("count") or 1) > 1:
        parts.append(f"×{int(item['count'])}")
    return "<i>" + " · ".join(parts) + "</i>"


def _heading(item: dict[str, Any]) -> str:
    lead = (html.escape(item["emoji"]) + " ") if item.get("emoji") else ""
    subject = f" · {html.escape(item['subject'])}" if item.get("subject") else ""
    return f"{lead}<b>{html.escape(item['title'])}</b>{subject}"


def lines_of(body: str) -> str:
    """The at most two plain lines a list item shows (≤ 140 characters, cut at a word)."""
    text = plain(body)
    lines = [ln.strip(" •") for ln in text.splitlines() if ln.strip(" •")]
    return clip(" ".join(lines[:2]) if lines else "", LINES_LIMIT)


def list_entry(n: int, item: dict[str, Any], now: float, run_key=None) -> str:
    lines = lines_of(item.get("body", ""))
    return "\n".join(x for x in (f"{CIRCLED[n]} {_heading(item)}", html.escape(lines) if lines else "",
                                 _meta(item, now, run_key)) if x)


def pages(items: Sequence[dict[str, Any]], now: float, run_key=None) -> list[list[dict[str, Any]]]:
    """Items packed into list pages: at most 5 a page and 3,600 characters with the header and headings; an item
    is never split across pages, and Needs you comes first (the items' order). The HTML is counted, not only the
    text Telegram shows, so the presentation layer's 4,000-character cut never shortens a page. A page never holds
    more items than there are numbers (CIRCLED): every item has its own number and its own button (UI-01g)."""
    out: list[list[dict[str, Any]]] = []
    page: list[dict[str, Any]] = []
    size = 80  # the header, the page line and the section headings
    for item in items:
        cost = len(list_entry(0, item, now, run_key)) + 24
        if page and (len(page) >= min(PAGE_ITEMS, len(CIRCLED)) or size + cost > PAGE_CHARS):
            out.append(page)
            page, size = [], 80
        page.append(item)
        size += cost
    if page:
        out.append(page)
    return out


def pager_row(page: int, last: int, callback) -> list[dict[str, str]]:
    """Only the arrows that lead somewhere new (design §6.1): ⏮ past page 2, ◀ past page 1, ▶ before the last,
    ⏭ two or more before it. Each takes an equal share of the row; the counter is in the text."""
    row = []
    if page > 2:
        row.append({"text": "⏮", "callback_data": callback(1)})
    if page > 1:
        row.append({"text": "◀", "callback_data": callback(page - 1)})
    if page < last:
        row.append({"text": "▶", "callback_data": callback(page + 1)})
    if page < last - 1:
        row.append({"text": "⏭", "callback_data": callback(last)})
    return row


def view(db) -> dict[str, Any]:
    try:
        state = json.loads(db.get_setting(VIEW_KEY) or "{}")
    except ValueError:
        state = {}
    return state if isinstance(state, dict) else {}


def set_view(db, page: int = 1, item: int | None = None, came_from: int = 1, tapped: float | None = None) -> None:
    db.set_setting(VIEW_KEY, json.dumps({"page": max(1, int(page)), "item": item, "from": max(1, int(came_from)),
                                         "tapped": time.time() if tapped is None else tapped}))


def render(db, now: float | None = None, run_key=None) -> tuple[str, list[list[dict[str, str]]]]:
    """The panel as it stands: the item page when one is open, else the list page in view."""
    now = time.time() if now is None else now
    items = open_items(db)
    state = view(db)
    undo = undo_state(db, now)
    needs = sum(1 for i in items if i["section"] == NEEDS)
    info = len(items) - needs
    head = header(needs, info, len(undo["ids"]) if undo else 0)
    if state.get("item"):
        item = get(db, state["item"])
        if item:
            return _item_page(item, items, head, now, run_key, int(state.get("from") or 1))
    return _list_page(items, head, int(state.get("page") or 1), bool(undo), now, run_key)


def _list_page(items, head, page_no, undoing, now, run_key):
    if not items:
        text = head + "\n\nNothing needs you, and there is nothing new.\n<i>New items arrive here; the ones that need you ring once.</i>"
        return text, ([[{"text": "↩️ Undo", "callback_data": "nundo"}]] if undoing else [])
    book = pages(items, now, run_key)
    page_no = min(max(1, page_no), len(book))
    shown = book[page_no - 1]
    blocks = [head + (f"\n<i>Page {page_no} of {len(book)}</i>" if len(book) > 1 else "")]
    section = None
    for n, item in enumerate(shown):
        entry = list_entry(n, item, now, run_key)
        if item["section"] != section:
            section = item["section"]
            entry = ("<b>Needs you</b>" if section == NEEDS else "<b>Info</b>") + "\n" + entry
        blocks.append(entry)
    rows = [[{"text": CIRCLED[n], "callback_data": f"n:{item['id']}"} for n, item in enumerate(shown)]]
    if undoing:
        rows.append([{"text": "↩️ Undo", "callback_data": "nundo"}])
    elif any(i["section"] == INFO for i in items):
        rows.append([{"text": "Dismiss all info", "callback_data": "nall"}])
    pager = pager_row(page_no, len(book), lambda p: f"np:{p}")
    if pager:
        rows.append(pager)
    return "\n\n".join(blocks), rows


def _item_body(body: str) -> str:
    """An item page's text: a short body as it is; a long one as its first lines, then the whole in one folded
    quote (never nested: a body with its own quote is kept as it is)."""
    body = (body or "").strip()
    if not body:
        return ""
    if tg_len(body) <= PLAIN_BODY or "<blockquote" in body:
        from .telegram_bot import safe_html_truncate
        return safe_html_truncate(body, 2800)
    from .telegram_ui import quote
    return html.escape(lines_of(body)) + "\n" + quote(plain(body), expandable=True, limit=QUOTE_LIMIT)


def action_ref(action: dict[str, str]) -> str:
    """An item-page button's address: a short hash of the action it runs, not its place, so a sender that updates
    the item's actions while its page is open can never make a tap run another action than the one shown."""
    return hashlib.sha1(str(action.get("callback_data", "")).encode()).hexdigest()[:6]


def find_action(item: dict[str, Any], ref: str) -> dict[str, str] | None:
    """The item's action a tap addressed (action_ref), or None when the item no longer offers it."""
    return next((a for a in item.get("actions") or [] if action_ref(a) == ref), None)


PANEL_OPENERS = {"rvshow"}   # actions that open a panel of their own (they keep their emoji, as a screen's button)


def opens_a_screen(callback: str) -> bool:
    """Whether an action opens a dashboard screen or a panel (the item page stays open) rather than acting."""
    from .telegram_ui import replayable
    return replayable(callback) or str(callback).partition(":")[0] in PANEL_OPENERS


def action_label(action: dict[str, str]) -> str:
    """An item-page button's label (design §6.1): an action carries no emoji ("Resend for review", "Undo
    approval"); a button that opens a screen keeps its screen's emoji ("🗓️ Posting")."""
    text = " ".join(str(action.get("text", "")).split())
    if opens_a_screen(action.get("callback_data", "")):
        return text
    bare = _LEAD.sub("", text, count=1).strip()
    return bare[:1].upper() + bare[1:] if bare else text


def _item_page(item, items, head, now, run_key, came_from):
    open_ = item["state"] == OPEN
    back = [{"text": "← Back to list", "callback_data": f"np:{came_from}"}]
    if not open_:   # an item that left by its time while its page was open is said as what happened
        mark = "🗒️" if item["state"] == EXPIRED else "✅"
        return head + "\n\n" + _heading(item) + f"\n{mark} {GONE_WORDS.get(item['state'], GONE_WORDS[''])}", [back]
    same = [i for i in items if i["section"] == item["section"]]
    where = next((n for n, i in enumerate(same, 1) if i["id"] == item["id"]), 1)
    place = f"<i>{'Needs you' if item['section'] == NEEDS else 'Info'} · {where} of {len(same)}</i>"
    refused = f"⚠️ <i>Not done: {html.escape(item['note'])}</i>" if item.get("note") else ""
    parts = [head + "\n" + place, "\n".join(x for x in (_heading(item), _item_body(item.get("body", "")), refused,
                                                          _meta(item, now, run_key)) if x)]
    from .telegram_ui import label
    verbs = [{"text": label(action_label(a)), "callback_data": f"n:{item['id']}:{action_ref(a)}"}
             for a in item["actions"]]
    if not str(item.get("dedupe_key", "")).startswith(CONDITION_PREFIX):   # a condition resolves itself: no Dismiss
        verbs.append({"text": "Dismiss", "callback_data": f"n:{item['id']}:{DISMISS}"})
    rows = [verbs[k:k + 3] for k in range(0, len(verbs), 3)]
    return "\n\n".join(parts), rows + [back]
