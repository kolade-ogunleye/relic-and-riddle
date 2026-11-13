"""
src/telegram_bot.py
===================
Human-in-the-loop approval. Sends the cover, the video preview and a review card with
[Approve & Publish] [Regenerate] [Reject] buttons to Telegram, then waits for a decision
from an authorized approver.

Decisions can also be made from the CLI (`python main.py review approve <run_id>`);
both paths write to the same SQLite row, and the first decision wins.
Nothing is ever approved automatically: no decision before the timeout means "timeout".
"""

import copy
import html
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests

from .local_time import show as show_time
from . import config
from .config import (
    APPROVAL_TIMEOUT_SECONDS,
    TELEGRAM_APPROVER_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    is_configured,
)
from .db import PipelineDatabase
from .ops import SecretMasker

log = logging.getLogger(__name__)

ACTIONS = {
    "approve": "approved", "regen": "regenerate", "reject": "rejected", "cancel": "cancelled",
    "rejres": "rejected", "keepit": "keep", "undoappr": "undo_approved", "undorej": "undo_rejected",
}
DECISIONS = {"approved", "regenerate", "rejected", "cancelled"}   # what a review can be decided as
TELEGRAM_VIDEO_LIMIT_MB = 49.0
REASON_CARD_HOURS = 2  # a decided review's reason card survives /clear this long (the window for a reply)
STALE_COMMAND_SECONDS = 600  # commands queued while the bot was down longer than this are not replayed
# Typed commands run as a job. Not /propose: it only starts the research job, and wrapping that in a
# second job showed an empty "Completed." over the research panel.
SLOW_COMMANDS = {"status", "add", "issue", "music", "discover", "ask", "review", "covers", "edittext", "fetchimages", "accounts",
                 "filmfind"}
# Buttons on cards from earlier releases. They get an explicit answer and never run old behaviour.
RETIRED_ACTIONS = {"done": "Closed",
                   "umore": "This card is from an earlier version. Open /ideas for UNESCO marvels.",
                   "umoredone": "This card is from an earlier version. Open /ideas for UNESCO marvels."}

from .telegram_ui import HOME as DONE_BUTTON
from .telegram_ui import hourglass  # 🏠 Dashboard: one home button everywhere (2026-10-25 overhaul)
PAGER_ICONS = {"⏮", "◀", "▶", "⏭"}
_PANEL = ContextVar("telegram_navigation_panel", default=None)
# A notifications item's action being run (UI-01): its callback's answers are collected here and its outcome
# recorded, so the item closes only when the action was done and the tap gets one toast saying what happened.
_ITEM_ACTION = ContextVar("notifications_item_action", default=None)
# Toasts that only say a tap arrived: an item action's own toast says what was done instead.
# Item actions whose handler closes the item itself when it is done (the review panel's Undo approval).
CLOSE_THEIR_ITEM = {"rvua"}
LEGACY_ACTIONS_NS = "alert-actions"   # an earlier-chat notice's buttons by "<chat>:<message>" (UI-01f, §13)
# Item actions whose handler says whether it was done (notifications.Outcome); a reply that does not say is not done.
SAY_THEIR_OUTCOME = {"upd", "rerender", "carredraw", "carspace", "carexpire"}
PLAIN_TOASTS = {"", "Working", "Request checked", "Request received", "Updated", "Working; you can keep using the bot"}
PANEL_ACTIONS = {"ccar", "reviewpick", "pickbump", "pickdrop", "cideas", "cpropose", "ccov", "cmusic", "cdisc", "cprop", "cstat", "cimages", "caccounts", "imagepick", "imagehelp", "review", "mkeep", "mnext", "mdel", "mcancel", "mreview", "musicmenu", "mfmenu", "mlibrary", "chelp"}
# Cards removed before acting (their own Cancel/Skip/Next buttons).
DELETE_FIRST = {"tskip"}
# Decision buttons: disabled before acting so a second tap can't repeat the decision.
ONE_SHOT = {"uadd", "tadd", "imgok", "mkeep", "cov", "rsn", "impok", "impno", "imprev",
            "pubok", "pubno", "simpub", "resend", "rerender", "promote", "requeue", "fbcaptions", "fbcover", "ytshort", "rebook", "ytthumb",
            "newversion", "carredraw", "carspace", "carexpire"}
# Replies that only acknowledge (the card or panel itself shows the result): a nudge that leaves on its own,
# not a message that stays (operator, 2026-10-25: "Sending ... for review again" and "Thanks, noted" stayed).
NUDGE_REPLIES = {"resend", "whytext", "requeue"}
# Of those, the notices whose only job was the button tapped: removed once it is used.
NOTICE_ACTIONS = {"resend"}
# How long a rejected run's reason card stays open for details before it leaves the chat.
REASON_CARD_MINUTES = 30
# After "Other" on a Regenerate, until how long after the Regenerate tap the new version waits for your words
# before deciding what to keep (production 2026-10-26: the words came 20 s after the tap, 7 s after the run
# began, and the script was kept unread). At least 10 s after the reason is seen.
REGEN_DETAIL_SECONDS = 90
STOP_CONFIRM_SECONDS = 300  # the progress panel's Cancel run confirmation stands this long (UI-01c)
REAP_GRACE_SECONDS = 30     # a run that has just ended is left to its own process's finisher this long (UI-01c)
INTERRUPTED_ERROR = "Interrupted: the process making it ended (crash or stop) before it finished."
# Replies that replace the card they came from (text only).
EDIT_IN_PLACE = {"uadd", "tadd", "simpub", "rsn", "impok", "impno", "imprev"}
TOASTS = {"imgok": "✅ Storyboard Approved!", "imgcancel": "✖ Cancelled", "mcancel": "✖ Cancelled",
          "mnext": "⏭ Next Track", "uadd": "✅ Added to Queue",
          "tadd": "✅ Added to Queue", "uskip": "✖ Skipped", "tskip": "✖ Skipped", "mdel": "🗑 Track Removed",
          "mkeep": "✅ Track Kept", "qbump": "🚀 Bumped to #1", "qdrop": "🗑 Topic Dropped", "qrefresh": "🔄 Refreshed",
          "qrender": "⚡ Run started", "qdoc": "🎬 Reel + documentary started", "cov": "📌 Cover Selected", "cstat": "📊 Status", "cprog": "⚡ Progress",
          "caudit": "🛡️ Activity log", "ccov": "🎨 Covers", "cprop": "💡 Ideas", "cdisc": "🏛️ Discoveries",
          "cmusic": "🎵 Music", "cpause": "⏸️ Paused", "cresume": "▶️ Resumed", "cdash": "🏛️ Dashboard",
          "simpub": "🧪 Simulated", "rsn": "📝 Noted", "impok": "✅ Done", "impno": "✖ Declined",
          "imprev": "↩ Reverted", "creviews": "🎬 Reviews", "cimprove": "🧭 Improvement", "cideas": "💡 Ideas", "canalytics": "📈 Audience"}

# Plain names for jobs in /jobs and progress panels (never the internal button name).
JOB_TITLES = {"ccar": "Carousel preview", "fetchimages": "Fetching images", "filmfind": "Finding archive film", "mfetch": "Finding documentary music", "caccounts": "Checking accounts",
              "ccov": "Covers", "cmusic": "Music", "cstat": "Status", "cprop": "Waiting ideas",
              "cdisc": "UNESCO marvels", "mreview": "Music review", "mlibrary": "Music library", "mkeep": "Keeping a track",
              "mdel": "Removing a track", "mnext": "Next track", "review": "Image review", "uadd": "Adding a marvel",
              "tadd": "Adding an idea", "edittext": "Editing text", "ask": "Ask", "issue": "Logging an issue",
              "status": "Status", "accounts": "Checking accounts", "discover": "UNESCO marvels",
              "propose": "Researching topic ideas", "covers": "Covers", "music": "Music", "add": "Adding a topic"}

# The screen a background job was started from, by its resource (the part before ":"): its card
# offers a way back one level, not only to the dashboard (topic research had only ← Dashboard).
JOB_PARENTS = {
    "topic-research": ("← Topic ideas", "cideas:"),
    "propose": ("← Topic ideas", "cideas:"),
    "tadd": ("← Topic ideas", "cideas:"),
    "fetchimages": ("← Pictures", "cimages:"),
    "imagepick": ("← Pictures", "cimages:"),
    "mfetch": ("← Music", "cmusic:"),
    "uadd": ("← UNESCO marvels", "cdisc:"),
}


from .telegram_ui import screen_name  # noqa: E402


def job_parent(resource: str) -> Optional[Dict[str, str]]:
    found = JOB_PARENTS.get((resource or "").partition(":")[0])
    return screen_button(found[1]) or {"text": found[0], "callback_data": found[1]} if found else None


# Names for screens a job can be started from, so its back button says where it goes.
# The names come from telegram_ui.SCREENS, the one table of screen names (these said "Full status", "Images",
# "Audit" and "Storyboards" after the screens were renamed: review, 2026-10-25).
SCREEN_NAMES = {"cideas", "cdisc", "cprop", "cimages", "imagepick", "cmusic", "mfmenu", "mlibrary", "creviews",
                "covers", "ccov", "qrefresh", "cstat", "caudit", "cimprove", "reviewpick"}


def screen_button(callback: Optional[str]) -> Optional[Dict[str, str]]:
    """'← <screen>' back to the exact screen (and page) a callback showed. Only the read-only screens
    named above qualify: a back button replays its callback, and replaying an action (adding a marvel,
    starting research) would repeat it. The dashboard has its own button."""
    from .telegram_ui import SCREENS
    key = (callback or "").partition(":")[0]
    return {"text": f"← {SCREENS[key][1]}", "callback_data": callback} if key in SCREEN_NAMES and key in SCREENS else None


_MASKER = SecretMasker()
# Keep-alive connections shared by every bot thread: a new TLS connection costs ~1 s, a reused one
# ~0.2 s. Sessions are per thread; the connection pool (thread-safe) is shared.
_HTTP = threading.local()
_POOL = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=16)
CONNECT_TIMEOUT = 6  # a stalled connect fails fast (edits retry) instead of waiting out the OS timeout


class TelegramRateLimited(RuntimeError):
    """Telegram's flood control on a message edit (HTTP 429), with the seconds it asked the bot to wait."""

    def __init__(self, method: str, retry_after: float, description: str = ""):
        super().__init__(f"Telegram {method} failed: HTTP 429 {description}".rstrip())
        self.retry_after = float(retry_after)


class V2CardUnavailable(ValueError):
    """A value the V2 review card shows that the run does not record: the live card is sent instead, never a
    stand-in value (UI-02)."""


V2_VOICE_NAMES = {"azure": "Azure Speech", "elevenlabs": "ElevenLabs"}
# The run's notes the live card lists (_card_text), shown on the V2 card under its findings
V2_NOTE_KEYS = ("scene_plan_note", "topic_origin_note", "fact_sheet_note", "hook_signal_note", "payoff_note",
                "picture_lines_note", "voice_note", "music_note", "motion_note", "graphics_note",
                "reconstruction_note", "footage_note", "beat_image_note", "source_note")
V2_SCRIPT_ROOM = 1500   # characters of narration the V2 card shows; the rest is named (Edit text has it whole)


def undo_minutes() -> float:
    """A V2 card's Undo window (config.REVIEW_UNDO_MINUTES): the worker acts on its decision only after it."""
    return float(config.REVIEW_UNDO_MINUTES)


def undo_until(approval: dict[str, Any]) -> float | None:
    """When the Undo window of this decision ends (epoch seconds), or None without a decision time."""
    try:
        return datetime.fromisoformat(str(approval.get("decision_timestamp"))).timestamp() + undo_minutes() * 60
    except (TypeError, ValueError):
        return None


def v2_card_decision(db: Any, run_id: str, approval: dict[str, Any]) -> bool:
    """The decision was tapped on the run's delivered V2 review pair (its video message), so its Undo applies."""
    if not str(approval.get("decided_by") or "").startswith("telegram:") or not hasattr(db, "get_telegram_card"):
        return False
    if clean_chat():
        # The clean chat's review panel (UI-01e) decides with the same Undo window: its Undo is the decided item's.
        from .review_panel import STATE
        from .telegram_state import get
        if get(db, STATE, run_id) and get(db, "review-card", f"{run_id}:{approval.get('telegram_message_id')}"):
            return True
    card = db.get_telegram_card(f"review:{run_id}") or {}
    return (card.get("card_kind") == "review_pair"
            and (card.get("payload") or {}).get("video_mid") == approval.get("telegram_message_id"))


V2_ONLY_ACTIONS = {"keepit", "undoappr", "undorej", "rejres"}   # buttons only the V2 review card carries


def _v2_review_keyboard(key: str, blocked: bool) -> dict[str, Any]:
    """The V2 review video's decision buttons; no Approve on a version the release gate blocks (the card's rule:
    "Keep it" and an Undo rebuilt it with Approve whatever the gate said)."""
    return {"inline_keyboard": [
        ([] if blocked else [{"text": "🚀 Approve & post", "style": "success", "callback_data": f"approve:{key}"}])
        + [{"text": "✏️ Edit text", "style": "primary", "callback_data": f"edit:{key}"}],
        [{"text": "♻️ Redo with a note", "style": "primary", "callback_data": f"regen:{key}"},
         {"text": "❌ Reject", "style": "danger", "callback_data": f"reject:{key}"}],
    ]}


def _v2_reason_keyboard(target: str) -> dict[str, Any]:
    """Reject's reasons on the V2 card: improve.REASONS, the one set every reason card uses (operator ruling,
    UI-01 spec), three to a row, then "← Keep it"."""
    from .improve import REASONS
    chips = [{"text": label, "callback_data": f"rejres:{target}:{code}"} for code, label in REASONS.items()]
    rows = [chips[i:i + 3] for i in range(0, len(chips), 3)]
    return {"inline_keyboard": rows + [[{"text": "← Keep it", "style": "primary", "callback_data": f"keepit:{target}"}]]}


def _age_anchor(run: dict[str, Any]) -> float:
    """When a run's record last changed, as epoch seconds (0 when it cannot be read: old enough)."""
    from datetime import UTC, datetime
    try:
        at = datetime.fromisoformat(str(run.get("updated_at") or ""))
    except ValueError:
        return 0.0
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()


def clean_chat() -> bool:
    """Whether the clean chat (UI-01) is on. A function: _process_update imports `config` locally further down,
    so the module name is not reachable there."""
    return bool(getattr(config, "TELEGRAM_CLEAN_CHAT", False))


def plain_reason(error: Any, limit: int = 160) -> str:
    """A failure reason in plain words: no exception or outcome class name in front ("PublishFailed: ..."),
    cut at a word (control.short_reason)."""
    from .control import short_reason
    text = re.sub(r"^(?:[A-Z]\w*(?:Error|Exception|Failed|Unknown|NotConfigured)|NotConfigured):\s*", "", str(error or "").strip())
    return short_reason(text, limit) or "no reason was returned"


def _plain(text: Any) -> str:
    """HTML card text as plain rich-message text: tags removed, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def _run_json(run_dir: Path | None, name: str) -> dict[str, Any]:
    """A run's JSON record, or {} when it is missing or unreadable."""
    try:
        data = json.loads((Path(run_dir) / name).read_text(encoding="utf-8")) if run_dir else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


RECEIPT_NAMES = {"instagram": "Instagram Reel", "facebook": "Facebook Reel", "youtube": "YouTube Short",
                 "youtube-video": "YouTube video"}


class PublishReceipt:
    """Consolidated in-place publication notification in topic Runs (4909): one message, edited as each
    attempted platform comes out, ending with its outcome and a single Dashboard button. It says what really
    happened (UI-02): the header is ✅ only when every attempted platform posted, ⚠️ "Posted with problems" when
    some did not, ❌ when none did; a failure is "❌ <platform>: <plain reason>"; the Short is listed only when it
    is attempted; the Runs log line names only what posted."""
    def __init__(self, bot, run_id: str, title: str, platforms: list[str] | None = None,
                 initial: dict[str, dict[str, Any]] | None = None):
        self.bot = bot
        self.run_id = run_id
        self.title = title
        self.message_id = None
        keys = list(platforms) if platforms is not None else (
            ["instagram", "facebook"] + (["youtube"] if config.YOUTUBE_SHORTS else []))
        self.platforms = {k: {"name": RECEIPT_NAMES.get(k, k), "status": "waiting" if k == "youtube" else "uploading",
                              "url": None, "error": None} for k in keys}
        for k, state in (initial or {}).items():
            if k in self.platforms:
                self.platforms[k].update(state)
        self.follow_up = ""
        self._send_or_update()

    def update_platform(self, key: str, status: str, url: Optional[str] = None, error: Optional[str] = None):
        """`status`: uploading, waiting, live, failed, unknown (sent, not confirmed) or skipped (not attempted)."""
        if key in self.platforms:
            self.platforms[key].update(status=status, url=url, error=error)
            self._send_or_update()

    def _attempted(self) -> list[dict[str, Any]]:
        return [p for p in self.platforms.values() if p["status"] != "skipped"]

    def finalize(self, follow_up: str = ""):
        self.follow_up = follow_up
        self._send_or_update(final=True)
        # Outcome line in Runs log (silent): only what posted is named
        live = [p["name"] for p in self._attempted() if p["status"] == "live"]
        missed = [p["name"] for p in self._attempted() if p["status"] != "live"]
        title = html.escape(self.title)
        if live and not missed:
            line = f"📮 {title} posted · {_and_list(live)}"
        elif live:
            line = f"⚠️ {title} posted with problems · {_and_list(live)}; not {_and_list(missed)}"
        else:
            line = f"❌ {title} not posted"
        if not config.TELEGRAM_CLEAN_CHAT:   # the clean chat has one item per episode, the receipt itself
            self.bot.notify(line, group="runs", silent=True)

    def _header(self, final: bool) -> str:
        title = f"<b>{html.escape(self.title)}</b>"
        if not final:
            return f"⚡ <b>Posting</b> · {title}"
        attempted = self._attempted()
        live = [p for p in attempted if p["status"] == "live"]
        if attempted and len(live) == len(attempted):
            return f"✅ <b>Posted</b> · {title}"
        if live:
            return f"⚠️ <b>Posted with problems</b> · {title}"
        if attempted and all(p["status"] == "failed" for p in attempted):
            return f"❌ <b>Not posted</b> · {title}"
        return f"⚠️ <b>Not confirmed</b> · {title}" if attempted else f"❌ <b>Not posted</b> · {title}"

    def _render_text(self, final: bool = False) -> str:
        lines = [self._header(final) + "\n"]
        for p in self.platforms.values():
            why = html.escape(plain_reason(p.get("error")))
            if p["status"] == "live" and p["url"]:
                lines.append(f"✅ {p['name']}: <a href=\"{html.escape(str(p['url']))}\">live</a>")
            elif p["status"] == "live":
                lines.append(f"✅ {p['name']}: live")
            elif p["status"] == "uploading":
                lines.append(f"⏳ {p['name']}: uploading…")
            elif p["status"] == "waiting":
                lines.append(f"◽ {p['name']}: queued")
            elif p["status"] == "failed":
                lines.append(f"❌ {p['name']}: {why}")
            elif p["status"] == "unknown":
                lines.append(f"⚠️ {p['name']}: not confirmed ({why}); check the account before posting it again")
            elif p["status"] == "skipped":
                lines.append(f"◽ {p['name']}: not attempted ({why})")
        if self.follow_up:
            lines.append(f"\n{self.follow_up}")
        return "\n".join(lines)

    def _send_or_update(self, final: bool = False):
        text = self._render_text(final=final)
        buttons = [[DONE_BUTTON]]
        if config.TELEGRAM_CLEAN_CHAT:
            # One "📮 Posted" item per episode, updated in place as each platform lands (design §10.1).
            missed = final and any(p["status"] != "live" for p in self._attempted())
            self.bot.notify(text, group="runs", section="needs" if missed else "info", key=f"posted:{self.run_id}",
                            run=self.run_id, replace=True)
            return
        if not self.message_id:
            res = self.bot.notify(text, buttons=buttons, group="runs", silent=True)
            if isinstance(res, int):
                self.message_id = res
        else:
            self.bot.edit_message_text(self.message_id, text, buttons=buttons)


def _and_list(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _post(url, timeout=30, **kwargs):
    session = getattr(_HTTP, "session", None)
    if session is None:
        session = _HTTP.session = requests.Session()
        session.mount("https://", _POOL)
    # Uploads keep one timeout: with a (connect, read) pair the short connect value also bounds each socket
    # write while the file is sent, which made review videos fail ("The write operation timed out").
    return session.post(url, timeout=timeout if kwargs.get("files") else (CONNECT_TIMEOUT, timeout), **kwargs)


def navigation_buttons(buttons=None, parent=None):
    """The screen's buttons plus the one navigation row: ← the parent screen (when given) and 🏠 Dashboard."""
    from .telegram_ui import with_nav
    rows = format_responsive_keyboard(buttons) if buttons else []
    return with_nav(rows, parent) if parent else (rows if any(b.get("callback_data") == "cdash:" for row in rows for b in row)
                                                  else rows + [[DONE_BUTTON]])


# The review card's budget (HTML, UTF-16 units): under telegram_pages.PAGE_ABOVE with room for a decision's
# badge, so the card and its approved/rejected form stay one formatted message (a paged card lost its
# blockquotes and its buttons failed: 2026-10-25).
CARD_PAGE_LIMIT = 3880


_HASHTAG_ONLY_LINE = re.compile(r"^(?:\s*#\w+)+\s*$")
_TRAILING_HASHTAGS = re.compile(r"(?:\s+#\w+)+\s*$")


# The rest is sent whole under the card (operator, 2026-10-28: "why can't I have all the details... we are already
# using a blockquote"): "reply /ask" asked the model to retell a list the bot already had
CARD_LIST_MORE = "the full list is in the reply under this card"
FOOTAGE_MAX_BYTES = 20 * 1024 * 1024  # the Bot API's getFile limit
FORM = "/footage topic | source: URL | licence: CC BY 4.0 | licence_url: URL"


def _capped(lines: List[str], cap: Optional[int]) -> List[str]:
    # One item past the cap is shown, not counted: "1 item: the full list is in the reply" stood in for a one-line
    # list, and as long as the line itself (production review card, 2026-10-29).
    if cap is None or len(lines) <= cap + 1:
        return lines
    if cap == 0:  # nothing shown: say how many there are, not "…and N more" under no item
        return [f"<i>{len(lines)} item{'s' if len(lines) != 1 else ''}: {CARD_LIST_MORE}</i>"]
    return lines[:cap] + [f"<i>…and {len(lines) - cap} more: {CARD_LIST_MORE}</i>"]


def listed(heading: str, items: List[str], cap: Optional[int] = None) -> str:
    """A card list the one way the card shows one: its heading in bold, then each item whole in an expandable
    blockquote (Telegram folds it), never cut to fit. `cap` (a long card): the first `cap` items, then
    "…and N more". `heading` and `items` are HTML already."""
    head = heading if "<b>" in heading else f"<b>{heading}</b>"
    body = _capped([x for x in items if x], cap)
    return head + ("\n<blockquote expandable>" + "\n".join(f"• {x}" for x in body) + "</blockquote>" if body else "")


# Freeze panel 2026-11-04 (M12 (3), operator decision): a design card's quality notes (its <graphic>.design.json, written
# by design_cards) reach the review card, so a myth card whose correction drops every specific is seen before approval
DESIGN_NOTE_TEXT = {
    "correction_drops_specifics": "the correction keeps none of its fact's figures, dates or names",
}


def design_card_notes(run_dir: Path) -> List[str]:
    """Each drawn design card's quality notes in `run_dir` (or one folder under it), as 'graphic_mythfact_01: ...'
    lines. A refused, unreadable or note-free record gives none."""
    out: List[str] = []
    try:
        found = sorted(set(run_dir.glob("graphic_*.design.json")) | set(run_dir.glob("*/graphic_*.design.json")))
    except OSError:
        return out
    for p in found:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("refused"):
            continue
        for n in rec.get("quality_notes") or []:
            out.append(f"{p.name.split('.', 1)[0]}: {DESIGN_NOTE_TEXT.get(str(n), str(n))}")
    return out


def _refusal_effect(where: str) -> str:
    """What a refused field costs on its surface, in the operator's words."""
    if where.startswith("Story frame"):
        return "so the frame is left out and the Story posts short"
    if where.startswith("graphic") or where == "photo callouts":
        return "so the graphic is left out of the video"
    if where.startswith("design card"):
        return "so the card is left out"
    return "so that render stopped"


def glyph_notes(run_dir: Path) -> list[str]:
    """The run's lettering notes (QF-94), from its glyph ledger (font_engine.LEDGER_NAME), each naming the surface it
    was drawn on: a field set in a fallback face because its face has no glyph for some of its characters; a field
    no bundled face can draw, and what that cost (a Story frame not drawn, a graphic left out); a caption word libass
    draws in a system face; and one count of the fields an emoji was removed from. A part of a field (a line, a word,
    a fitting trial: `part_of`) is shown with its field, not on its own line."""
    from .font_engine import LEDGER_NAME
    out: list[str] = []
    seen: set[tuple] = set()
    emoji: set[str] = set()
    try:
        lines = (Path(run_dir) / LEDGER_NAME).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return out
    for line in lines:
        try:
            ev = json.loads(line)
            kind, face = ev["kind"], str(ev["face"])
            # a field's lines (a credit's licence line and its "This frame" line) joined as the field reads
            text = " / ".join(" ".join(line.split()) for line in str(ev["text"]).splitlines() if line.strip())
            chars, scripts = " ".join(str(ev.get("chars") or "")), ", ".join(ev.get("scripts") or [])
            where = str(ev.get("where") or "an image (surface not named)")
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if kind == "emoji_removed":
            emoji.add(text)
            continue
        # a caption word is listed once whichever caption face measured it (9:16 Anton, upper-cased; 16:9
        # Montserrat): the note does not name the face (QF-94b)
        key = ((kind, text.casefold(), where) if kind == "caption_system_face"
               else (kind, text, face, ev.get("fallback"), where))
        if ev.get("part_of") or key in seen:
            continue
        seen.add(key)
        head = where[0].upper() + where[1:]
        if kind == "fallback":
            out.append(f"{head}: \u201c{text}\u201d set in {ev.get('fallback')} ({face} has no {chars}, {scripts})")
        elif kind == "refused":
            out.append(f"{head}: \u201c{text}\u201d not drawn, {_refusal_effect(where)}: no bundled face draws "
                       f"{chars} ({scripts})")
        elif kind == "caption_system_face":
            out.append(f"{head}: \u201c{text}\u201d has {chars} ({scripts}), which no bundled face draws; libass "
                       "draws them in a system face")
    if emoji:
        out.append(f"Emoji removed from {len(emoji)} field(s) (no emoji in art)")
    return out


LAYOUT_HOLD_NAMES = {"stories": "Story frames", "carousel": "Instagram carousel", "carousel_fb": "Facebook carousel"}


def layout_hold_lines(holds: Any) -> list[str]:
    """Each surface held for a typography.LayoutError (payload["layout_holds"]: main.hold_layout_error for the
    decks, graphics.make_all for the graphics; QF-23), as 'Story frames: Checked copy does not fit ...' lines."""
    out: list[str] = []
    for h in holds if isinstance(holds, list) else []:
        if isinstance(h, dict) and h.get("deck"):
            deck = str(h["deck"])
            name = LAYOUT_HOLD_NAMES.get(deck) or ("graphic: " + deck.split("/", 1)[1] if deck.startswith("graphics/")
                                                   else deck)
            out.append(f"{name}: {h.get('error') or 'held'}")
    return out


def detail_messages(full_card: str, limit: int = CARD_PAGE_LIMIT) -> List[str]:
    """The card's lists whole, for the message under a card whose lists were shortened: each section with an
    expandable blockquote, in order, packed into as few messages as Telegram's limit allows (a section longer
    than one message is split between its items, each part in its own blockquote)."""
    sections = [sec for sec in full_card.split("\n\n") if "<blockquote expandable>" in sec]
    parts: List[str] = []
    for sec in sections:
        head, _, rest = sec.partition("<blockquote expandable>")
        items = rest.rsplit("</blockquote>", 1)[0].split("\n")
        chunk: List[str] = []
        for item in items:
            trial = head + "<blockquote expandable>" + "\n".join(chunk + [item]) + "</blockquote>"
            if chunk and _tg_len(trial) > limit:
                parts.append(head + "<blockquote expandable>" + "\n".join(chunk) + "</blockquote>")
                chunk = []
            chunk.append(item)
        parts.append(head + "<blockquote expandable>" + "\n".join(chunk) + "</blockquote>")
    messages: List[str] = []
    for part in parts:
        if messages and _tg_len(messages[-1] + "\n\n" + part) <= limit:
            messages[-1] += "\n\n" + part
        else:
            messages.append(part)
    return messages


def without_hashtags(text: str) -> str:
    """A caption as the review card shows it: lines of hashtags and a closing run of them taken out (the card
    lists the hashtags on their own line). A hashtag inside a sentence stays, so no sentence is cut."""
    lines = [ln for ln in (text or "").splitlines() if not _HASHTAG_ONLY_LINE.match(ln)]
    return _TRAILING_HASHTAGS.sub("", "\n".join(lines).strip()).strip()


def _one_line(text: str) -> str:
    """A posted string as one card list item, HTML-escaped: each line break shown as ⏎ (a list item is one line)."""
    return html.escape(str(text or "")).replace("\r\n", "\n").replace("\n", " ⏎ ")


def _schedule_line(posted: dict[str, Any]) -> str:
    """'🗓 Slot: Tue 27 Oct 17:00 MDT · review closes Tue 27 Oct 16:40 MDT': the Reel slot an Approve books now
    (post_schedule.next_reel_slot), only when it books one (otherwise that it posts at once, or nothing on a dry
    run), and when this review closes (QF-43)."""
    from . import post_schedule
    if posted.get("error"):   # nothing was worked out: Approve posts nothing (the "Also posted" list says why)
        return ""

    def when(iso: str | None) -> str:
        try:
            return post_schedule.fmt(datetime.fromisoformat(str(iso)))
        except (TypeError, ValueError):
            return ""
    slot = when(posted.get("slot"))
    closes = when(posted.get("closes"))
    if posted.get("scheduling_hold") and posted.get("posting") != "dry_run":   # QF-73: Approve cannot post it yet
        return ("🗓 <b>Waiting:</b> " + html.escape(str(posted["scheduling_hold"]))
                + (f" · <b>review closes</b> {html.escape(closes)}" if closes else "") + "\n")
    if slot and closes and slot[:10] == closes[:10]:   # the same day: its time alone ("Tue 27 Oct 16:40 MDT" -> "16:40 MDT")
        closes = closes[11:]
    line = "🗓 <b>Slot:</b> " + (html.escape(slot) if slot else "none, a dry run posts nothing"
                                 if posted.get("posting") == "dry_run" else "posted as soon as you approve")
    return line + (f" · <b>review closes</b> {html.escape(closes)}" if closes else "") + "\n"


def _posted_hashtags_line(posted: dict[str, Any]) -> str:
    """The hashtags posted (the topic's candidate list decides)."""
    tags = posted.get("hashtags") or []
    line = f"<b>Hashtags:</b> {html.escape(' '.join(tags))}" if tags else "<b>Hashtags:</b> none (none is on this topic's list)"
    return line   # the written ones left out are in the "Also posted" list (_also_posted)


def _changed_lines(shown: str, posted: str) -> str:
    """`posted` said as its changes to `shown` ("“old” → “new”" per changed line), or "" when that is not shorter
    than `posted` itself. Exact: every other line of `posted` is the same line of `shown`."""
    import difflib
    a, b = shown.splitlines(), posted.splitlines()
    parts: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        old, new = " ⏎ ".join(a[i1:i2]), " ⏎ ".join(b[j1:j2])
        parts.append(f"“{old}” → “{new}”" if old and new else f"without “{old}”" if old else f"with “{new}” added")
    said = "; ".join(parts)
    return said if parts and len(said) < len(posted) else ""


def _also_posted(posted: dict[str, Any], copy: dict[str, str]) -> list[str]:
    """Every other string Approve posts, word for word, as card list items (QF-43): the Facebook Reel title, the
    YouTube Short's title, description and tags (a documentary: its YouTube title, description and tags), the
    carousel caption as Facebook's photo grid carries it, and each slide's alt text. A string the card already shows
    (the title, the caption, the carousel caption) is named, not repeated; one that differs from it only in a few
    lines is said as those lines' changes."""
    e = html.escape
    if posted.get("error"):
        return [f"<i>could not be worked out ({e(str(posted['error'])[:200])}): Approve will not post</i>"]
    if posted.get("documentary"):
        return [f"YouTube title: {_one_line(copy.get('youtube.title'))}",
                f"YouTube description: {_one_line(copy.get('youtube.description'))}",
                f"YouTube tags: {_one_line(copy.get('youtube.tags') or 'none')}"]
    title, caption = copy.get("reel.title", ""), copy.get("reel.caption", "")
    tags = ", ".join(t.lstrip("#") for t in posted.get("hashtags") or [])
    short = []
    short.append("the title above" if copy.get("short.title") == title else f"title {_one_line(copy.get('short.title'))}")
    description = copy.get("short.description", "")
    short.append("the caption above" if description == caption
                 else f"description {_one_line(description)}")
    short.append("the hashtags as tags" if copy.get("short.tags", "") == tags
                 else f"tags {_one_line(copy.get('short.tags') or 'none')}")
    off = "" if posted.get("shorts") else " (not posted: YouTube Shorts are turned off)"
    # The first item is the one the card keeps when its room is short (_card_text): the YouTube Short when it is
    # posted; while YOUTUBE_SHORTS is off it is not, and goes last.
    items = ["Facebook Reel: the title and the caption above"]   # the card's title and caption are these (_card_text)
    items.insert(0 if posted.get("shorts") else 1, f"YouTube Short{off}: " + ", ".join(short))
    if "carousel.facebook" in copy:
        ig, fb = copy.get("carousel.instagram", ""), copy["carousel.facebook"]
        above = "the carousel caption above"
        if not posted.get("carousel_own", True):   # no carousel caption of its own: the card shows none (_card_text)
            changes = _changed_lines(caption, ig)
            items.append("Instagram carousel caption: " + (
                "the caption above" if ig == caption else f"the caption above with {e(changes)}" if changes
                else _one_line(ig)) + ", then the photo credit lines")
            above = "the Instagram carousel caption"
        changes = _changed_lines(ig, fb)
        items.append("Facebook carousel caption: " + (
            above if fb == ig else f"{above} with {e(changes)}" if changes
            else _one_line(fb)) + ", then the photo credit lines")
    for label in sorted((k for k in copy if k.startswith("alt.")), key=lambda k: (k.split(".")[1] != "instagram",
                                                                                  int(k.rsplit(".", 1)[1]))):
        _, platform, n = label.split(".")
        twin = copy.get(f"alt.instagram.{n}")
        if platform == "facebook" and copy[label] == twin:
            continue
        items.append(f"Alt text, slide {n} ({platform.capitalize()}): {_one_line(copy[label])}")
    if posted.get("carousel_at_slot"):   # QF-81: drawn on demand (🖼 Preview carousel) or at its slot
        items.append("Carousel slides and their alt text: drawn from this run's photos and carousel cards when you "
                     "tap 🖼 Preview carousel or when it takes its carousel slot, and checked before they post")
    if posted.get("dropped"):
        items.append(f"Hashtags not posted (not on this topic's list): {e(' '.join(posted['dropped']))}")
    return items


def _tg_len(text: str) -> int:
    """Length as Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def _video_dimensions(path) -> Dict[str, int]:
    """width, height and duration for an uploaded video, so Telegram shows its real shape."""
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=20)
        info = json.loads(out.stdout or "{}")
        stream = (info.get("streams") or [{}])[0]
        dims = {"width": int(stream["width"]), "height": int(stream["height"])}
        dims["duration"] = int(round(float((info.get("format") or {}).get("duration", 0))))
        return dims
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return {}


def _second_check_line(second: Optional[Dict[str, Any]]) -> str:
    if not second:
        return ""
    name = re.search(r"^\[([^\]]+)\]", second.get("summary", ""))
    who = f" by {name.group(1)}" if name else ""
    if second.get("status") == "pass":
        return f"✅ Second check{html.escape(who)}: passed ({second.get('confidence', 0)}% of {len(second.get('audits', []))} claims)\n"
    return "⚠️ Second check: unavailable (first check only)\n"


CREDIT_CARD_CLIP = 60     # a credit that is not a photo's, on a card with no room left (QF-42)
CREDIT_CARD_TITLE = 32    # a photo credit's title in its short form on a long card (QF-42)
CREDITS_HEAD = "📷 <b>Credits posted with the captions:</b>"
CREDITS_UNLISTED = "📷 <b>Credits:</b>"  # the count alone, on a card the findings fill (shortened first)
CARD_CAPTION_ROOM = 400   # the caption text the credits leave room for before they are shortened
CAPTION_TOO_LONG = "<i>(too long for this card: see Edit)</i>"
ALSO_HEAD = "📤 <b>Also posted, word for word</b>"   # what else Approve posts (QF-43)
ALSO_SHORT = "📤 <b>Also posted:</b>"                # its one-line count, in the room a decision's badge keeps
AI_HEAD = "🤖 <b>AI transparency:</b>"                 # whether the post carries the AI label, and why (QF-65a)
_CARD_BLOCK_END = "</blockquote>\n"


def _ai_label_line(posted: Any, brief: bool = False) -> str:
    """The card's AI-label line from its record (publishable.card_record, QF-65a), whole or `brief` (whether the
    label is on and its first reason); "" for a record without the assessment."""
    if not isinstance(posted, dict):
        return ""
    from .ai_label import card_line
    text = card_line(posted.get("ai_label"), documentary=bool(posted.get("documentary")),
                     photos=posted.get("ai_label_photos"), brief=brief,
                     # QF-81: a carousel drawn at its slot is labelled from its slides there (the slot's alert names
                     # a Facebook hold); the card's assessment holds the Stories only
                     stills="Stories" if posted.get("carousel_at_slot") else "Stories/carousel")
    return f"{AI_HEAD} {html.escape(text)}" if text else ""


def _said_once(card: str) -> str:
    """Where the rest of a shortened list is, said once: each shortened list repeated it (production review card,
    2026-10-29). The card's room is measured on this form, the one sent (QF-42)."""
    first, sep, rest = card.partition(f": {CARD_LIST_MORE}")
    return first + sep + rest.replace(f": {CARD_LIST_MORE}", "")


def _with_publish_lines(card: str, posted: dict[str, Any], limit: int, reserve: int, shift: int = 0) -> str:
    """The fitted review card (QF-42's fit, unchanged) with what else Approve posts and when (QF-43), each only in
    room the card leaves, so neither costs the narration, a caption or a finding anything (review of QF-43 on 30
    real cards: laid out with the card, they cut narration and dropped captions' "see Edit" lines):
    - the "Also posted, word for word" list after the copy's folded section, in the longest form within `limit`
      (whole; its first items and "…and N more"; its count): the reply under the card has it whole
      (needs_publish_reply);
    - on the card waiting for a decision only (`reserve` 0), in the room CARD_PAGE_LIMIT keeps for a decision's
      badge: the slot line under the title, then (when no form of the list fit) the list's count. The card stays
      one message under telegram_pages.PAGE_ABOVE; the decided card, rebuilt with its badge, has neither (its
      receipt says what was booked). A card the findings already push past that keeps them in the reply.
    - the AI-label line (QF-65a) under the title, on every card with the record: brief (the label and its first
      reason) in the room the fit gives up for it (card_fit_limit, ai_line_room), then whole once everything
      else is laid out when the room left takes it; the reply under the card has it whole otherwise."""
    from .telegram_pages import PAGE_ABOVE
    also = _also_posted(posted, posted.get("copy") or {})
    ai_whole, ai_brief = _ai_label_line(posted), _ai_label_line(posted, brief=True)
    # `shift`: the room the fit freed beyond the AI line's own (TelegramApprovalBot._ai_layout); the slot
    # line and the "Also posted" list or count are laid out in the room they had without the line.
    page_cap, list_cap = PAGE_ABOVE - shift, limit - shift

    with_ai = _under_title

    def whole_ai(text: str, room: int) -> str:
        """The brief AI line swapped for the whole one when the room takes it."""
        trial = text.replace(ai_brief + "\n", ai_whole + "\n", 1) if ai_brief and ai_whole != ai_brief else text
        return trial if _tg_len(trial) <= room else text

    card = with_ai(card, ai_brief)

    def with_list(text: str, cap: int | None) -> str:
        head = text.find("<b>Narration and timings:</b>")
        at = text.find(_CARD_BLOCK_END, head) if head >= 0 else -1
        if not also or at < 0:
            return text
        at += len(_CARD_BLOCK_END)
        section = (listed(ALSO_HEAD, also, cap) if cap is None or cap >= 0   # -1: its count on one line
                   else f"{ALSO_SHORT} <i>{len(also)} item{'s' if len(also) != 1 else ''} in the reply</i>")
        return _said_once(text[:at] + "\n" + section + "\n" + text[at:])

    listed_ = False
    for k in (None, *range(len(also) - 2, -1, -1)):
        trial = with_list(card, k)
        if trial != card and _tg_len(trial) <= list_cap:
            card, listed_ = trial, True
            break
    if reserve > 0:
        return whole_ai(card, limit)
    pin = card.find("📌 <b>")
    title = card.find("\n", pin) if pin >= 0 else -1
    if title >= 0 and _schedule_line(posted):
        trial = card[:title + 1] + _schedule_line(posted) + card[title + 1:]
        if _tg_len(trial) <= page_cap:
            card = trial
    if not listed_ and also:
        for trial in (with_list(card, 0), with_list(card, -1)):
            if trial != card and _tg_len(trial) <= page_cap:
                card = trial
                break
    return whole_ai(card, PAGE_ABOVE)


def _under_title(card: str, line: str) -> str:
    """The card with `line` (the AI-label line, QF-65a) under its title; unchanged without a line or a title."""
    pin = card.find("📌 <b>")
    at = card.find("\n", pin) if pin >= 0 else -1
    if not line or at < 0:
        return card
    return card[:at + 1] + line + "\n" + card[at + 1:]


def _publish_lines_shown(card: str) -> tuple[str, str]:
    """What a card shows of Approve's lines (QF-43): its slot line and its "Also posted" section, as text."""
    slot = next((ln for ln in card.splitlines() if ln.startswith("🗓 <b>Slot:</b>")), "")
    at = card.find("📤 <b>Also posted")
    if at < 0:
        return slot, ""
    end = card.find("</blockquote>", at) if card.startswith(ALSO_HEAD, at) else card.find("\n", at)
    return slot, card[at:end if end >= 0 else len(card)]


def _captions_named(card: str) -> int:
    """How many captions a review card names: their text, or where to read it (QF-42)."""
    return sum(card.count(f"<b>{label}:</b>") for label in ("Caption", "Carousel caption"))


def _qf42_kept(card: str, reference: str, previews: bool = True) -> bool:
    """Whether a fitted card keeps what QF-42's fit keeps on `reference` (the card without the AI-label line): the
    narration whole if it was, every caption named, the credits' own lines, and (`previews`) each caption preview."""
    return (("[shortened:" not in card or "[shortened:" in reference)
            and _captions_named(card) >= _captions_named(reference)
            and (CREDITS_UNLISTED not in card or CREDITS_UNLISTED in reference)
            and (not previews or _caption_previews(card) >= _caption_previews(reference)))


def _without_ai_line(card: str) -> str:
    """The card without its AI-label line (QF-65a), for comparing what else it shows."""
    return re.sub(r"(?m)^" + re.escape(AI_HEAD) + r"[^\n]*\n", "", card, count=1)


def ai_line_room(posted: Any) -> int:
    """The room the card's brief AI-label line takes (QF-65a): kept out of the fit, so the line is always on the card
    and the narration, captions, findings and the other publish lines are laid out in what is left. 0 without one."""
    line = _ai_label_line(posted, brief=True)
    return _tg_len(line) + 1 if line else 0


def needs_publish_reply(card: str, posted: Any) -> bool:
    """Whether the reply under the card must carry what Approve posts: the card shows the "Also posted" list only in
    part, or not at all, or has no slot line (_with_publish_lines)."""
    if not isinstance(posted, dict):
        return False
    also = _also_posted(posted, posted.get("copy") or {})
    slot = _schedule_line(posted)
    ai = _ai_label_line(posted)   # QF-65a: a card showing the brief AI line has the whole one in the reply
    return (bool(also and listed(ALSO_HEAD, also) not in card) or bool(slot and slot not in card)
            or bool(ai and ai not in card))


def _caption_previews(card: str) -> int:
    """How many captions a review card shows some text of: on it, and not only where to read it (QF-42)."""
    return sum(f"<b>{label}:</b>" in card for label in ("Caption", "Carousel caption")) - card.count(CAPTION_TOO_LONG)


def _credit_card_line(line: str, form: int) -> str:
    """A caption credit line as a long review card lists it (QF-42): whole (form 0); a photo credit
    ("📷 author | title | licence | source | licence URL") without its two links (1), as author and title cut to
    CREDIT_CARD_TITLE (2), or the author alone (3). Any other credit line (music, footage, the share-alike notice)
    is whole, cut to CREDIT_CARD_CLIP characters in form 3."""
    from .control import clip_words
    fields = [f.strip() for f in line.removeprefix("📷").split(" | ")] if line.startswith("📷") else []
    if len(fields) != 5:
        return line if form < 3 else clip_words(line, CREDIT_CARD_CLIP)
    author, title, licence = fields[0], fields[1].removeprefix("File:"), fields[2]
    return line if not form else "📷 " + " · ".join(
        [author, title, licence] if form == 1 else [author, clip_words(title, CREDIT_CARD_TITLE)] if form == 2
        else [author])


def _clip_copy(text: str, limit: int) -> str:
    """Public copy shortened at a word for the review card, saying where the whole of it is."""
    from .control import clip_words
    text = (text or "").strip()
    return text if len(text) <= limit else clip_words(text, limit) + " (full text: Edit)"


def safe_html_truncate(html_str: str, max_chars: int = 4000) -> str:
    """Safely truncates HTML text without breaking open tags or HTML entities.
    Automatically closes any unclosed tags in reverse nesting order.
    """
    if len(html_str) <= max_chars:
        return html_str

    token_pattern = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;)")
    tokens = token_pattern.split(html_str)

    stack = []
    result = []
    current_len = 0

    for token in tokens:
        if not token:
            continue
        closing_cost = sum(len(t) + 3 for t in stack) + 1  # +1 for ellipsis
        if token.startswith("<") and token.endswith(">"):
            tag_content = token[1:-1].strip()
            if tag_content.startswith("/"):
                tag_name = tag_content[1:].split()[0].lower()
                if stack and stack[-1] == tag_name:
                    stack.pop()
                result.append(token)
                current_len += len(token)
            elif not tag_content.endswith("/"):
                tag_name = tag_content.split()[0].lower()
                new_closing_cost = closing_cost + len(tag_name) + 3
                if current_len + len(token) + new_closing_cost > max_chars:
                    break
                stack.append(tag_name)
                result.append(token)
                current_len += len(token)
            else:
                if current_len + len(token) + closing_cost > max_chars:
                    break
                result.append(token)
                current_len += len(token)
        else:
            if current_len + len(token) + closing_cost > max_chars:
                remaining = max(0, max_chars - current_len - closing_cost - 1)
                if remaining > 0:
                    sub = token[:remaining]
                    if "&" in sub and ";" not in sub[sub.rfind("&"):]:
                        sub = sub[:sub.rfind("&")]
                    result.append(sub + "…")
                elif not result or not result[-1].endswith("…"):
                    result.append("…")
                break
            else:
                result.append(token)
                current_len += len(token)

    for tag in reversed(stack):
        result.append(f"</{tag}>")

    return "".join(result)


def format_responsive_keyboard(buttons: Optional[Any], max_per_row: int = 2, max_btn_len: int = 16) -> List[List[Dict[str, str]]]:
    """
    Transforms any flat list or nested list of inline buttons into an ergonomically
    responsive grid optimized for all screen sizes (smartphones, tablets, and desktop).
    - Buttons with text > max_btn_len receive their own full-width row (100% width).
    - Shorter buttons are paired in rows of at most max_per_row (default 2).
    - Eliminates cramped 3-4 button rows that truncate titles on mobile devices.
    """
    if not buttons:
        return []

    # If already a list of rows, process row by row
    if isinstance(buttons, list) and buttons and isinstance(buttons[0], list):
        out_rows: List[List[Dict[str, str]]] = []
        for row in buttons:
            if not isinstance(row, list):
                continue
            if len(row) == 1 or (len(row) <= 5 and all(str(b.get("text", "")).strip() in PAGER_ICONS or str(b.get("text", "")).isdigit() for b in row if isinstance(b, dict))):
                out_rows.append(row)  # one button, or a pager/number row (⏮ ◀ ▶ ⏭, 1 2 3 4) that fits any screen
                continue
            cur: List[Dict[str, str]] = []
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                text = str(btn.get("text", "")).strip()
                if len(text) > max_btn_len:
                    if cur:
                        out_rows.append(cur)
                        cur = []
                    out_rows.append([btn])
                else:
                    cur.append(btn)
                    if len(cur) >= max_per_row:
                        out_rows.append(cur)
                        cur = []
            if cur:
                out_rows.append(cur)
        return out_rows

    # Flat list: pack up to max_per_row per row, isolating long buttons
    flat = [b for b in buttons if isinstance(b, dict)] if isinstance(buttons, list) else []
    out_rows: List[List[Dict[str, str]]] = []
    cur: List[Dict[str, str]] = []
    for btn in flat:
        text = str(btn.get("text", "")).strip()
        if len(text) > max_btn_len:
            if cur:
                out_rows.append(cur)
                cur = []
            out_rows.append([btn])
        else:
            cur.append(btn)
            if len(cur) >= max_per_row:
                out_rows.append(cur)
                cur = []
    if cur:
        out_rows.append(cur)
    return out_rows


def scene_strip(video: Path, out: Path, frames: int = 12, cols: int = 6) -> Optional[Path]:
    """One frame every few seconds of the review video, tiled with timestamps, so picture choices
    can be judged at a glance. None if ffmpeg can't read the video."""
    import subprocess
    from PIL import Image, ImageDraw
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                                   capture_output=True, text=True, timeout=30, check=True).stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
    tile_w, tiles = 270, []
    for k in range(frames):
        t = dur * (k + 0.5) / frames
        frame = out.with_name(f"_strip_{k:02d}.jpg")
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", f"scale={tile_w}:-2", str(frame)], timeout=60, check=True)
            with Image.open(frame) as im:
                tiles.append((t, im.convert("RGB")))
        except (subprocess.SubprocessError, OSError):
            continue
        finally:
            frame.unlink(missing_ok=True)
    if not tiles:
        return None
    tile_h = tiles[0][1].height
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (12, 12, 14))
    d = ImageDraw.Draw(sheet)
    for k, (t, im) in enumerate(tiles):
        x, y = (k % cols) * tile_w, (k // cols) * tile_h
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + 64, y + 26], fill=(0, 0, 0))
        d.text((x + 6, y + 6), f"{int(t // 60)}:{int(t % 60):02d}", fill=(255, 215, 0))
    sheet.save(out, quality=85)
    return out



def _minutes(seconds: float) -> str:
    return f"~{int(round(seconds / 60))} min" if seconds >= 90 else "about a minute" if seconds >= 30 else "under a minute"


# What happens between one progress report and the next (main.py reports a step when the one before
# it is done). The old names were one step behind: "Mastering narration voiceover" stayed on screen
# while the pictures were prepared, a reused voiceover having taken 0.08 s (2026-10-24).
PROGRESS_STEPS = [
    "Script, fact check & voiceover",
    "Music, photos, Stories & carousel",
    "Graphics, scene plan, motion & render",
    "Quality checks",
    "Review package",
]


# Each step's usual share of a run, measured on 8 complete runs (2026-10-24): the script stage and the
# render dominate, the checks and the review package take a minute. Used until this install has
# recorded its own step lengths; a flat 20% a step put the bar at 60% with most of the work still to do.
STEP_SHARES = [0.33, 0.19, 0.44, 0.02, 0.02]


def step_seconds(db, total: int, typical: Optional[float]) -> Optional[List[float]]:
    """How long each step usually takes: this install's recorded medians, else the measured shares of
    its usual run length (None when neither is known)."""
    recorded = db.typical_step_seconds(total) if hasattr(db, "typical_step_seconds") else None
    if recorded:
        return recorded
    if typical and total == len(STEP_SHARES):
        return [share * typical for share in STEP_SHARES]
    return None


TRANSPORT_SIGNS = ("timed out", "timeout", "502", "503", "504", "bad gateway", "connection", "remotedisconnected",
                   "max retries", "rate-limited", "temporarily unavailable", "name resolution")


def transport_error(error: BaseException) -> bool:
    """A failure of the connection to Telegram rather than of our request: a long poll that timed out, a
    gateway error, a dropped connection. Expected now and then; retried quietly."""
    text = str(error).lower()
    return any(sign in text for sign in TRANSPORT_SIGNS)


def progress_figures(step: int, total: int, elapsed: float, typical: Optional[float],
                     step_elapsed: Optional[float] = None, typical_steps: Optional[List[float]] = None) -> tuple:
    """Bar, percent and time left for a run on step `step` of `total` (1-based, the step in progress).

    With per-step history (typical_steps: how long each step usually takes), progress is weighted by
    those lengths and grows within a step, and time left is what remains of this step plus the usual
    length of the later ones. A step running past its usual length says so rather than counting down
    (2026-10-23: a slow fact check showed "under a minute" left with four steps still to go).
    Without it, only the whole-run length of recent runs is known: the bar counts finished steps and a
    run past that length says it is running long. With no history at all it says so."""
    done = max(0, min(total, step - 1))
    if typical_steps and len(typical_steps) == total and sum(typical_steps) > 0 and step_elapsed is not None:
        current = typical_steps[min(step, total) - 1]
        later = sum(typical_steps[step:])
        within = min(step_elapsed, current * 0.95)  # a step is never shown complete before it is
        pct = min(99, int((sum(typical_steps[:done]) + within) / sum(typical_steps) * 100))
        if step_elapsed > current * 1.25 + 30:
            from . import slowdown
            why = slowdown.current()
            eta = (f"this step is taking longer than usual (usually {_minutes(current)})"
                   + (f": {why}" if why else "")
                   + (f"; {_minutes(later)} after it" if later >= 30 else ""))
        else:
            eta = _minutes(max(0.0, current - step_elapsed) + later)
    else:
        pct = int(done / total * 100) if total else 0
        if typical and elapsed < typical - 60:
            eta = _minutes(typical - elapsed)
        elif typical:
            from . import slowdown
            why = slowdown.current()
            eta = f"running longer than usual (runs usually take {_minutes(typical)})" + (f": {why}" if why else "")
        else:
            eta = "estimating…"
    bar = "▰" * (pct // 10) + "▱" * (10 - pct // 10)
    return bar, pct, eta


def progress_estimate(db, active: Optional[dict], step: int, total: int) -> tuple:
    """progress_figures for a live run from its progress record: bar, percent, time left, elapsed
    seconds and seconds on the current step (None when the record has no step times)."""
    now = time.time()
    active = active or {}
    start = float(active.get("start_time") or now)
    started = (active.get("step_started") or {}).get(str(step))
    step_elapsed = max(0.0, now - float(started)) if started else None
    # A regeneration that keeps its voice and music is judged by past runs that did the same: judged by full runs,
    # a 10-minute Richat regeneration was promised ~27 min (self-audit, production 2026-10-26)
    reused = bool(active.get("run_id") and hasattr(db, "get_setting") and db.get_setting("regenerating:" + str(active["run_id"])))
    typical = typical_run_seconds(reused_voice=reused)
    steps = None if reused else step_seconds(db, total, typical)  # step medians come from full runs
    bar, pct, eta = progress_figures(step, total, now - start, typical, step_elapsed, steps)
    return bar, pct, eta, int(max(0, now - start)), step_elapsed


def step_running_note(step_elapsed: Optional[float]) -> str:
    """' · 5:12 on this step' once a step has run a minute, so a long step is visibly still working."""
    if step_elapsed is None or step_elapsed < 60:
        return ""
    s = int(step_elapsed)
    return f" · {s // 60}:{s % 60:02d} on this step"


VOICE_REUSED_SECONDS = 10  # a voiceover stage this short was carried over, not made


def typical_run_seconds(last: int = 5, reused_voice: Optional[bool] = None) -> Optional[float]:
    """Average wall-clock length of the most recent finished runs, from their stage timings (stages
    overlap, so it is first start to last finish, not a sum). None without history.
    Only a run made in one go to its review counts: a resumed run's stamps span the hours between its
    sessions (2026-10-24: "~79 min" left for a re-render), and one that stopped early is not a run's length."""
    from .config import OUTPUT_DIR
    totals = []
    for timings in sorted(OUTPUT_DIR.glob("*/timings.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:last * 4]:
        try:
            rows = json.loads(timings.read_text(encoding="utf-8"))
            stamps = [datetime.fromisoformat(r["at"].replace("Z", "+00:00")) for r in rows]
            if not any(r.get("stage") == "review_proxy" for r in rows):
                continue
            gaps = [(b - a).total_seconds() - float(r["seconds"]) for a, b, r in zip(stamps, stamps[1:], rows[1:])]
            if any(g > 600 for g in gaps):  # idle for 10 minutes between stages: two sessions
                continue
            if reused_voice is not None:  # like with like: a run that carried its voice over, or one that made it
                voice = next((float(r["seconds"]) for r in rows if r.get("stage") == "voiceover"), None)
                if voice is None or (voice < VOICE_REUSED_SECONDS) != reused_voice:
                    continue
            totals.append((max(stamps) - min(stamps)).total_seconds() + float(rows[0]["seconds"]))
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        if len(totals) == last:
            break
    if not totals and reused_voice is not None:
        return typical_run_seconds(last)  # none of that kind yet: every run
    return sum(totals) / len(totals) if totals else None


def origin_label(origin: str) -> str:
    """Who wrote the script, in plain words. "gemini-free" names the writing engine, not the key it used:
    production sends writing and checking to the billed key (FREE_TIER_HEAVY=false), and the card said
    "gemini-free" for a run billed on the paid key (operator, 2026-10-25)."""
    from . import config
    if origin == "gemini-free":
        return "Gemini, billed key" if not config.FREE_TIER_HEAVY else "Gemini, free tier first (billed key if it is busy)"
    return {"curated": "curated script", "claude": "Claude"}.get(origin, origin)



def illustration_review_items(payload: dict[str, Any], run_dir: Path) -> list[list[dict[str, Any]]]:
    """Albums (at most 10 photos each) showing each illustrated beat of the run (payload["picture_fit"]["generated"])
    followed by its reference image(s), captioned with the beat's narrated line and each reference's title and
    licence. Files that are missing are left out; [] when the run has no illustration."""
    e = html.escape
    items: list[dict[str, Any]] = []
    for g in (payload.get("picture_fit") or {}).get("generated") or []:
        if not isinstance(g, dict):
            continue
        art = Path(str(g.get("path") or "")) if g.get("path") else Path(run_dir) / str(g.get("to") or "")
        if not art.is_file():
            continue
        refs = [r for r in g.get("references") or [] if isinstance(r, dict) and Path(str(r.get("path") or "")).is_file()]
        items.append({"type": "photo", "path": art, "caption": (
            f"🏛 Beat {e(str(g.get('beat')))} illustration (needs your approval) · “{e(str(g.get('line') or '')[:600])}”"
            + ("" if refs else " · no reference: a kind of thing, drawn from the facts"))})
        for r in refs:
            items.append({"type": "photo", "path": Path(str(r["path"])), "caption": (
                f"Reference for beat {e(str(g.get('beat')))}: {e(str(r.get('title') or '')[:300])} "
                f"({e(str(r.get('licence') or '?'))}" + (f", {e(str(r['url'])[:300])}" if r.get("url") else "") + ")")})
    return [items[i:i + 10] for i in range(0, len(items), 10)]

class TelegramApprovalBot:
    def __init__(
        self,
        db: Optional[PipelineDatabase] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approver_ids: Optional[Set[str]] = None,
    ):
        self.db = db or PipelineDatabase()
        from .telegram_state import initialize
        initialize(self.db)
        self._missing_messages = set()
        self._workers = None
        self._offset = None
        self.bot_token = bot_token if bot_token is not None else TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id if chat_id is not None else TELEGRAM_CHAT_ID
        self.approver_ids = approver_ids if approver_ids is not None else TELEGRAM_APPROVER_IDS
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._progress_start_time: Dict[str, float] = {}
        self._last_progress_edit: Dict[int, float] = {}
        self._last_progress_step: Dict[int, int] = {}
        import threading
        # Telegram's flood control, per message (edit_message_text): until when its edits are held, the latest
        # content asked for meanwhile (with its order, so an older edit never replaces a newer one) and the timer
        # that sends it once the wait is over.
        self._edit_lock = threading.Lock()
        self._edit_seq = 0
        self._edit_held_until: Dict[int, float] = {}
        self._edit_pending: Dict[int, tuple] = {}
        self._edit_timers: Dict[int, Any] = {}
        self._edit_sending: Set[int] = set()     # messages whose held edit is being sent
        self._edit_chat_held_until = 0.0         # flood control is usually per chat: every edit waits
        self._edit_delivered: Dict[int, int] = {}  # the latest edit (its order) shown on each message
        self._edit_closed = False                # drained: edits go straight out, nothing is held or timed
        self._edit_timer = threading.Timer  # replaceable in tests
        self._panel_revisions = {}
        self._panel_aliases = {}
        self._panel_media = {}
        # The screen (callback) each panel shows, and the one it showed before the latest tap: a job a
        # button starts returns to the screen that button was on.
        self._panel_screen: Dict[int, str] = {}
        self._panel_origin: Dict[int, Optional[str]] = {}
        self._panel_trail: Dict[int, List[str]] = {}  # the screens shown, Dashboard first: ← goes back along it
        self._panel_lock = threading.RLock()  # bookkeeping only (revisions, aliases, trail): never across a request
        # Posting or moving the one panel is serialized on its own lock, the ticker's redraw and a run's receipt on
        # theirs, so the poll thread never waits on a Telegram request another thread is making (review, 2026-10-25).
        self._dashboard_lock = threading.RLock()
        self._progress_lock = threading.Lock()
        self._answered_lock = threading.Lock()
        self._finished_runs: set = set()  # runs whose progress card became a receipt: the ticker leaves it
        self._answered = {}  # callback ids already answered (insertion-ordered, bounded)
        self._last_opened = 0.0  # monotonic time of the last /start or /dashboard panel
        self._dispatch_slots = threading.BoundedSemaphore(16)
        self._navigation_pool = None
        self._last_maintenance = 0
        from .operator_jobs import Jobs
        self.jobs = Jobs(self.db)

    def navigation_target(self):
        panel = _PANEL.get()
        if panel and panel[0] is self:
            return panel[1], panel[2], self._panel_revisions.get(panel[1], 0)
        return None

    def _current_panel(self, message_id):
        while message_id in self._panel_aliases:
            message_id = self._panel_aliases[message_id]
        return message_id

    def show_dashboard(self, text=None, buttons=None, fresh=False, rebuild=False, expect=None):
        """One dashboard anchor survives /start and routine process reloads. Serialized: two refreshes at
        once (production's startup card and a queued Dashboard tap) each found the stored panel gone and
        each posted one; under the lock the second edits the panel the first posted. `expect` (panel, revision,
        text): drawn only in that panel, and only while it is still at that revision with that text
        (draw_counted_dashboard, QF-115)."""
        if text is None:  # built before the lock: building reads the database, not Telegram's
            from .control import dashboard_text_and_buttons
            from .config import ASSETS_DIR
            text, buttons = dashboard_text_and_buttons(self.db, getattr(self, "next_run_func", lambda: None)(), assets_dir=ASSETS_DIR)
        if not rebuild:
            self._panel_first()
            made = None if expect is not None else self._first_dashboard(text, buttons)
            if made:
                self._counted_meanwhile(text)
                return made
        with self._dashboard_lock:
            shown = self._show_dashboard_locked(text, buttons, fresh, rebuild, expect=expect)
        self._repost_if_asked()
        self._counted_meanwhile(text)
        return shown

    def _counted_meanwhile(self, text) -> None:
        """QF-115: a dashboard that says it is counting the photo sets, drawn after the count ended (the count can
        end while the layout is being posted, before the dashboard's message is known): drawn again now. Called
        holding no lock."""
        from .control import COUNTING_POOLS, counting
        if text and COUNTING_POOLS in text and not counting():
            self.draw_counted_dashboard()

    def _first_dashboard(self, text, buttons) -> int | None:
        """The clean chat (UI-01f): the first dashboard goes in its place, under the notifications panel and above
        any temporary panel, by posting the layout again from the dashboard down. Called before _dashboard_lock is
        taken (the re-post takes _rebuild_lock, then _dashboard_lock: telegram_layout's lock order); only the
        renderer does it. Returns the dashboard's message, or None when it is drawn the usual way."""
        if not clean_chat() or not self.is_configured() or self.db.get_setting("telegram_dashboard_id"):
            return None
        layout = self.layout
        if not layout.renders_here():
            return None
        with layout._rebuild_lock:
            if self.db.get_setting("telegram_dashboard_id"):   # another thread placed it meanwhile: drawn in place
                return None
            self._pending_dashboard = (text, buttons)
            try:
                layout.rebuild("dashboard")
            finally:
                self._pending_dashboard = None
        made = self.db.get_setting("telegram_dashboard_id")
        return int(made) if made and made.isdigit() else None

    def _panels_below_dashboard(self) -> bool:
        """Whether a progress or review panel sits below the dashboard (a dashboard sent at the bottom would break the
        order)."""
        from .telegram_layout import SLOT_PREFIX
        names = [str(c.get("card_id", "")).removeprefix(SLOT_PREFIX)
                 for c in self.db.list_telegram_cards(topic_id=0, card_kind="layout")]
        return any(n.startswith("progress:") or n == "review" for n in names)

    DASHBOARD_ASK_KEY = "dashboard-redraw"   # a dashboard another process asked the renderer to draw (UI-01f)

    def _ask_renderer_for_dashboard(self, text, buttons) -> None:
        """A process that is not the renderer records the dashboard it would draw; the renderer's tick draws it
        (design §5.4: only the renderer sends layout messages), as review-panel-redraw does for the review."""
        self.db.set_setting(self.DASHBOARD_ASK_KEY, json.dumps({"text": text, "buttons": buttons, "at": time.time()}))

    def draw_asked_dashboard(self) -> bool:
        """The renderer's side: the dashboard another process asked for is drawn here (in place, or posted again in
        order when it leaves a media screen). Never called holding _dashboard_lock or Layout._lock."""
        raw = self.db.get_setting(self.DASHBOARD_ASK_KEY)
        if not raw or not clean_chat() or not self.layout.is_renderer():
            return False
        self.db.set_setting(self.DASHBOARD_ASK_KEY, "")
        try:
            asked = json.loads(raw)
        except ValueError:
            asked = {}
        self.show_dashboard(asked.get("text"), asked.get("buttons"))
        return True

    def pools_counted(self) -> None:
        """control.when_pools_counted (QF-115): the photo sets a dashboard said it was counting are counted. With the
        clean chat's renderer running, its tick draws the dashboard again (outside Layout._lock: the lock order);
        otherwise it is drawn here, in the counter's thread, which holds no lock."""
        layout = getattr(self, "_layout", None)
        thread = getattr(layout, "_thread", None)
        if clean_chat() and thread is not None and thread.is_alive():
            self._counted_due = True
            return
        self.draw_counted_dashboard()

    def draw_counted_dashboard(self) -> bool:
        """The dashboard drawn again with the photo-set count it was waiting for, only while it still shows the
        counting line and nothing was opened in it since that line was drawn (a tap raises the panel's revision as
        it arrives, before its screen is drawn): a screen the operator opened meanwhile is left alone (home counts
        when next drawn). Checked again under _dashboard_lock just before the draw (show_dashboard's expect): the
        card is built in between. The startup card is built again by its own builder (startup_card, set by main),
        so its started line stays; a note leading the dashboard (⏸ Paused…) is kept."""
        from .config import ASSETS_DIR
        from .control import (
            COUNTING_POOLS,
            DASHBOARD_HEADING,
            STARTED_MARKS,
            dashboard_text_and_buttons,
        )
        self._counted_due = False
        stored = self.db.get_setting("telegram_dashboard_id")
        if not (stored and stored.isdigit()):
            return False
        with self._panel_lock:
            panel = self._current_panel(int(stored))
            content = (getattr(self, "_content", {}) or {}).get(panel)
            revision = self._panel_revisions.get(panel, 0)
            marked = (getattr(self, "_counting_marks", {}) or {}).get(panel, revision)
        if not content or COUNTING_POOLS not in content[0] or marked != revision:
            return False
        build = getattr(self, "startup_card", None)
        if callable(build) and any(mark in content[0] for mark in STARTED_MARKS):
            text, buttons = build()
        else:
            text, buttons = dashboard_text_and_buttons(self.db, getattr(self, "next_run_func", lambda: None)(),
                                                       assets_dir=ASSETS_DIR)
        if COUNTING_POOLS in text:   # still counting (a set changed meanwhile): the count's end calls again
            return False
        lead = content[0].find(DASHBOARD_HEADING)
        if lead > 0:
            text = content[0][:lead] + text
        return bool(self.show_dashboard(text, buttons, expect=(panel, revision, content[0])))

    def _mark_counting(self, message_id, text) -> None:
        """QF-115: the panel's revision when a dashboard that says it is counting the photo sets is drawn in it;
        draw_counted_dashboard draws the count only while the panel is still at that revision. A screen built before
        the count ended and drawn after it (its listener has been called) is drawn again from a new thread: this
        may run under _dashboard_lock or Layout._lock, so it never draws here."""
        from .control import COUNTING_POOLS, counting
        if text and COUNTING_POOLS in text:
            with self._panel_lock:
                self.__dict__.setdefault("_counting_marks", {})[message_id] = self._panel_revisions.get(message_id, 0)
            if not counting():
                threading.Thread(target=self.pools_counted, name="photo-set-redraw", daemon=True).start()

    def _repost_if_asked(self) -> None:
        """A dashboard drawn under _dashboard_lock that had to be posted again in order (design §6.3 path B: it
        left a media screen) asked for it; the re-post runs now that the lock is released (lock order). The
        renderer's tick runs one left behind."""
        slot = getattr(self, "_repost_from", None)
        if not slot or self._dashboard_lock._is_owned():
            return
        self._repost_from = None
        try:
            self.layout.rebuild(slot)
        except Exception as e:  # noqa: BLE001 - the age rule and the next tick post it again
            log.warning("Could not post the layout again from the %s: %s", slot, e)

    def _panel_first(self) -> None:
        """The clean chat: the pinned panel is made before a dashboard is drawn, so the dashboard always sits
        under it. Called before _dashboard_lock is taken, never under it (telegram_layout's lock order); only the
        renderer sends the panel (ensure checks the lease)."""
        if not clean_chat() or not self.is_configured():
            return
        from .telegram_layout import NOTIFICATIONS, slot_message
        if slot_message(self.db, NOTIFICATIONS):
            return
        try:
            self.layout.ensure()
        except Exception as e:  # noqa: BLE001 - the dashboard is still drawn
            log.warning("Could not post the notifications panel before the dashboard: %s", e)

    def _show_dashboard_locked(self, text, buttons, fresh, rebuild=False, expect=None):
        from .control import dashboard_text_and_buttons
        from .config import ASSETS_DIR
        if text is None:
            text, buttons = dashboard_text_and_buttons(self.db, getattr(self, "next_run_func", lambda: None)(), assets_dir=ASSETS_DIR)
        stored = self.db.get_setting("telegram_dashboard_id")
        previous = int(stored) if stored and stored.isdigit() else None
        clean = config.TELEGRAM_CLEAN_CHAT and not rebuild
        if clean:
            # The clean chat (UI-01b): the dashboard is drawn in place, never sent fresh at the bottom (that broke
            # the layout's order). Only the ordered rebuild sends a new copy (telegram_layout.rebuild).
            fresh = False
        if expect is not None and not (previous and not fresh):
            return None   # QF-115: the panel the count was for is gone; the next dashboard draw counts
        if previous and not fresh:
            current = self._current_panel(previous)
            with self._panel_lock:   # checked with the revision raised together: a tap raises it under this lock
                if expect is not None and (
                        current != expect[0] or self._panel_revisions.get(current, 0) != expect[1]
                        or ((getattr(self, "_content", {}) or {}).get(current) or (None,))[0] != expect[2]):
                    return None   # QF-115: a screen was opened in the panel meanwhile; it is left alone
                self._panel_revisions[current] = self._panel_revisions.get(current, 0) + 1
            if self.show_navigation_panel(current, text, buttons):
                self.db.set_setting("telegram_dashboard_id", str(self._current_panel(current)))
                return self._current_panel(current)
        if clean and self.is_configured() and not self.layout.renders_here():
            # UI-01f: a process that is not the renderer never sends a dashboard (design §5.4): it asks the renderer.
            self._ask_renderer_for_dashboard(text, buttons)
            return None
        if clean and previous and self.is_configured() and self._panels_below_dashboard():
            # The dashboard's message is gone and panels sit below it: it is posted again in its place, in order
            # (after the lock is released), never at the bottom under them.
            self.__dict__.setdefault("_content", {})[self._current_panel(previous)] = (text, copy.deepcopy(buttons))
            self._repost_from = "dashboard"
            return self._current_panel(previous)
        message_id = self.notify(text, buttons=buttons, record_alert=False, group="studio")
        if type(message_id) is int:
            self.__dict__.setdefault("_content", {})[message_id] = (text, copy.deepcopy(buttons))
            self._mark_counting(message_id, text)
            self.db.set_setting("telegram_dashboard_id", str(message_id))
            self._record_kind(message_id, media=False)
            # One panel in the chat: the one this replaces goes (a review card is never the panel).
            if previous and self._current_panel(previous) != message_id:
                self._delete_soon(self._current_panel(previous))
        return message_id

    def _typed_panel(self):
        """A typed command opens the one panel at the bottom of the chat and runs in it, as a tap would."""
        self._dashboard_touched = time.time()   # the operator is using the dashboard (its return home waits)
        self._panel_first()                     # before the lock (telegram_layout's lock order)
        message_id = self._first_dashboard("⏳ <b>Working…</b>", [[DONE_BUTTON]])   # UI-01f, before the lock too
        if not message_id:
            with self._dashboard_lock:   # the clean chat draws it in the dashboard (UI-01b)
                message_id = self._show_dashboard_locked("⏳ <b>Working…</b>", [[DONE_BUTTON]], fresh=True)
            self._repost_if_asked()
        if type(message_id) is not int:
            return None
        return message_id, False, self._panel_revisions.get(message_id, 0)

    def show_navigation_panel(self, message_id, text, buttons=None, is_media=False):
        """A text screen must not keep an unrelated cover/audio attachment."""
        from .telegram_pages import panel as paginate
        text, buttons = paginate(self.db, _MASKER.mask(text), buttons)
        current = self._current_panel(message_id)
        with self._dashboard_lock:
            stored = self.db.get_setting("telegram_dashboard_id")
            anchor = self._current_panel(int(stored)) if stored and stored.isdigit() else None
            if clean_chat() and anchor and anchor != current:
                # The clean chat (UI-01b): every screen is drawn in the dashboard. A screen asked for from another
                # message (an older card) never turns that message into a second dashboard.
                current, is_media = anchor, self._is_media(anchor)
            if anchor and anchor != current and current in self._protected_messages():
                # A screen asked for from a card that must stay (a review card, its receipt): it opens as the
                # one panel at the bottom and the old panel goes. Drawn in the card, it left two dashboards in
                # the chat, since a protected card never becomes the panel (production, 2026-10-25). Outside
                # the panel context, so the fresh message is sent, not routed back here.
                token = _PANEL.set(None)
                try:   # the clean chat draws it in the dashboard itself (fresh is ignored there: UI-01b)
                    return type(self._show_dashboard_locked(text, buttons, fresh=True)) is int
                finally:
                    _PANEL.reset(token)
        media = self._is_media(current, is_media)
        edited = False
        if not media:
            edited = self.edit_message_text(current, text, buttons=buttons)
            if edited:
                # A screen opened in another message (a failure card's "← Dashboard") makes that message the
                # one panel: the old panel goes, as for a photo panel below (it was left behind, two panels).
                stored = self.db.get_setting("telegram_dashboard_id")
                anchor = self._current_panel(int(stored)) if stored and stored.isdigit() else None
                protected = self._protected_messages()
                if anchor and anchor != current and current not in protected:
                    self.db.set_setting("telegram_dashboard_id", str(current))
                    if anchor not in protected:
                        self._delete_soon(anchor)
            if edited or not self._is_media(current):
                return edited
            # The edit found a photo/audio/video panel this process had no record of (a restart, a worker
            # process): it is replaced by a text message below, as a known media panel is.
        if clean_chat() and media and self.is_configured():
            stored = self.db.get_setting("telegram_dashboard_id")
            if stored == str(current) and not self.layout.renders_here():
                # Not the renderer (a run's worker ending its run): only the renderer sends a dashboard (design
                # §5.4), so it is asked to draw this one; the layout's order holds.
                self._ask_renderer_for_dashboard(text, buttons)
                return True
            if stored == str(current):
                # Design §6.3 path B: the dashboard leaves a picture or music screen. A media message cannot become
                # text, so the dashboard and every panel below it are posted again in order and the old copies go.
                self.__dict__.setdefault("_content", {})[current] = (text, copy.deepcopy(buttons))
                if self._dashboard_lock._is_owned():
                    # Drawn under the dashboard lock (show_dashboard): the re-post takes _rebuild_lock first, so it
                    # runs once that lock is released (_repost_if_asked; the renderer's tick as a fallback).
                    self._repost_from = "dashboard"
                    return True
                self._record_kind(current, media=False)   # its copy is text
                if self.layout.rebuild("dashboard") is not None and self.db.get_setting("telegram_dashboard_id") != stored:
                    return True
                self._record_kind(current, media=True)
                # The re-post could not run (its back-off, a refused copy): the media panel stays as it is, never a
                # new dashboard at the bottom (that broke the order); the content is kept for the next re-post.
                return True
        screen_data = {
            "chat_id": self.chat_id,
            "text": safe_html_truncate(_MASKER.mask(text), 4000),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []}),
        }
        if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS") and "studio" in config.TELEGRAM_TOPICS:
            screen_data["message_thread_id"] = config.TELEGRAM_TOPICS["studio"]
        result = self._api("sendMessage", data=screen_data)
        new_id = result["message_id"]
        if new_id == current:
            raise RuntimeError("Telegram returned an invalid replacement message identity")
        stored = self.db.get_setting("telegram_dashboard_id")
        anchor = self._current_panel(int(stored)) if stored and stored.isdigit() else None
        self._panel_aliases[current] = new_id
        self._record_kind(new_id, media=False)
        self._panel_revisions[new_id] = self._panel_revisions.get(current, 0)
        for screens in (self._panel_screen, self._panel_origin, self._panel_trail):  # the same panel, a new message
            if current in screens:
                screens[new_id] = screens[current]
        # The replacement is the newest screen, so it is the one panel: an older panel elsewhere goes too.
        self.db.set_setting("telegram_dashboard_id", str(new_id))
        if anchor and anchor not in (current, new_id) and anchor not in self._protected_messages():
            self._delete_soon(anchor)
        if not self.delete_message(current):
            self._strip_buttons(current)
        return True

    @property
    def _awaiting(self):
        """(command, deadline): a bare /ask or /issue takes the operator's next message. Kept in the
        database with a wall-clock deadline, so a restart in between no longer drops it silently
        (2026-10-23 audit)."""
        try:
            value = json.loads(self.db.get_setting("awaiting_reply") or "null") if hasattr(self.db, "get_setting") else None
        except ValueError:
            return None
        return tuple(value) if isinstance(value, list) and len(value) == 2 else None

    @_awaiting.setter
    def _awaiting(self, value):
        if hasattr(self.db, "set_setting"):
            self.db.set_setting("awaiting_reply", json.dumps(list(value)) if value else "")

    _EDIT_START = re.compile(r"^\s*(?:title|first comment|carousel caption|caption)[ \t]*:", re.IGNORECASE)

    def _open_edit_for(self, text: str) -> Optional[str]:
        """The review an Edit card was opened for in the last 30 minutes, when `text` starts with a field
        label (a pasted template); otherwise None, and the message is handled as usual."""
        if not text or not self._EDIT_START.match(text):
            return None
        from .control import EDIT_OPEN_KEY, EDIT_OPEN_SECONDS
        try:
            opened = json.loads(self.db.get_setting(EDIT_OPEN_KEY) or "{}")
        except ValueError:
            return None
        if opened.get("run") and time.time() - float(opened.get("at") or 0) <= EDIT_OPEN_SECONDS:
            return str(opened["run"])
        return None

    def clear_protected(self) -> Set[int]:
        """Messages a clear never removes: the live progress card, and every open review's card with its
        albums (cover, scene strip, video, Stories and carousel are what Approve publishes; /clear kept
        only the card and deleted its media: 2026-10-23 audit)."""
        from .telegram_state import get
        keep: Set[int] = set()
        if hasattr(self.db, 'list_telegram_cards'):
            from .telegram_layout import panel_ids
            keep.update(panel_ids(self.db))   # the clean chat's always-on panels (UI-01)
        progress = self.db.get_active_progress() if hasattr(self.db, 'get_active_progress') else None
        if (progress or {}).get('message_id'):
            keep.add(progress['message_id'])
        for approval in (self.db.list_pending_approvals() if hasattr(self.db, 'list_pending_approvals') else []):
            if approval.get('telegram_message_id'):
                keep.add(approval['telegram_message_id'])
            keep.update(m for m in (get(self.db, 'review-messages', approval['run_id']) or []) if isinstance(m, int))
        # A Reject/Regenerate reason card, for REASON_CARD_HOURS after the decision: replying to it is how
        # details reach the improvement log and the regenerated run (a /clear deleted it: 2026-10-23 audit).
        if hasattr(self.db, '_conn'):
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=REASON_CARD_HOURS)).isoformat()
            try:
                with self.db._conn() as conn:
                    keep.update(r[0] for r in conn.execute(
                        "SELECT telegram_message_id FROM approvals WHERE decision IN ('rejected', 'regenerate') "
                        "AND telegram_message_id IS NOT NULL AND decision_timestamp >= ?", (cutoff,)))
            except Exception as e:
                log.debug("Could not list reason cards to keep: %s", e)
        return keep

    def clear_chat(self, below: Optional[int] = None, depth: int = 500) -> int:
        """Removes the bot's messages (and, given a message id, the `depth` messages below it), keeping
        what clear_protected names. /clear and an installed release use this one routine."""
        keep = self.clear_protected()
        candidates: List[int] = []
        if hasattr(self.db, 'list_known_message_ids'):
            candidates = [m for m in self.db.list_known_message_ids() if m not in keep]
        if below:
            for mid in range(below - 1, max(1, below - depth) - 1, -1):
                if mid not in keep and mid not in candidates:
                    candidates.append(mid)
        if candidates:
            self.delete_messages(candidates)
        self.cleanup_transient_messages()
        return len(candidates)

    def _protected_messages(self):
        """Messages that are never removed to keep one panel: the same list a clear keeps."""
        return self.clear_protected()

    def update_navigation_target(self, target, text, buttons):
        if target:
            message_id, is_media, revision = target
            with self._panel_lock:
                message_id = self._current_panel(message_id)
                current = self._panel_revisions.get(message_id, 0) == revision
            if current:
                return self.show_navigation_panel(message_id, text, buttons=buttons, is_media=is_media)
            return False
        return self.notify(text, buttons=buttons, record_alert=False, group="studio")

    def start_operator_job(self, title, work, resource="", navigation=None, late=False):
        from .telegram_ui import SCREENS, with_nav
        destination = [navigation if navigation is not None else self.navigation_target()]
        # Back to the screen whose button started the job; for a typed command (no panel), by what it is.
        origin = self._panel_origin.get(self._current_panel(destination[0][0])) if destination[0] else None
        parent = screen_button(origin) or job_parent(resource)
        up = [[parent]] if parent else []
        def announce(job):
            buttons = [[{"text": "Cancel job", "callback_data": f"jobcancel:{job['id']}"}], [{"text": "Jobs", "callback_data": "jobs:"}], *up, [DONE_BUTTON]]
            text = f"⏳ <b>{html.escape(title)}</b>\n\nYou can keep using the bot. Cancellation stops further work at a safe boundary; an in-flight request may finish."
            if destination[0]:
                self.update_navigation_target(destination[0], text, buttons)
            else:
                mid = self.notify(text, buttons=buttons, record_alert=False, group="studio")
                if type(mid) is int:
                    self._record_kind(mid, media=False)
                    destination[0] = (mid, False, self._panel_revisions.get(mid, 0))
        def complete(job, result):
            if job["state"] == "completed":
                if result is None:
                    return None  # the work drew its own screen (a photo or audio panel): shown, not lost
                text, buttons = result if isinstance(result, tuple) else (str(result or "Completed."), [[DONE_BUTTON]])
                screen = resource.partition(":")[0]
                if screen in SCREENS and screen != "cdash" and destination[0]:
                    buttons = with_nav(buttons, self.back_for(destination[0][0], resource))
            else:
                text = f"{'⏹' if job['state'] == 'cancelled' else '⚠️'} <b>{html.escape(title)}</b>: {job['state']}.\nExisting approved assets and usage records are preserved."
                buttons = [[{"text": "Jobs", "callback_data": "jobs:"}], *up, [DONE_BUTTON]]
                if job["state"] == "failed" and navigation and resource.partition(":")[0] in PANEL_ACTIONS:
                    buttons.insert(0, [{"text": "Try again", "callback_data": resource}])
            if job.get("error_detail"):
                text += "\n" + html.escape(job["error_detail"]) + "\nInspect /jobs before explicitly retrying."
            delivered = self.update_navigation_target(destination[0], text, buttons)
            if not delivered and config.TELEGRAM_CLEAN_CHAT:
                # The operator moved on while it ran: the result is an info item with a way to its screen; the
                # dashboard is not yanked back (design §6.2).
                head = "🧰 <b>" + html.escape(title) + (" done" if job["state"] == "completed" else f": {job['state']}") + "</b>"
                return bool(self.notify(head + "\n" + text, buttons=buttons, group="info", key=f"job:{job['id']}"))
            if not delivered and late:
                # Requested work whose screen was left while it ran: its result becomes the one panel
                # (a second message would leave two cards in the chat).
                delivered = type(self.show_dashboard(text, navigation_buttons(buttons), fresh=True)) is int
            return delivered
        return self.jobs.submit(self._background(), title, work, complete, resource, announce)

    def jobs_view(self):
        """What is running now (with Cancel) and what finished recently, in plain words."""
        e = html.escape
        jobs = self.jobs.recent()
        running = [j for j in jobs if j["id"] in self.jobs.active and j["state"] in {"queued", "running"}]
        with self.db._conn() as conn:
            runs = conn.execute("SELECT run_id FROM runs WHERE status='producing' ORDER BY created_at DESC LIMIT 3").fetchall()
        lines, buttons = ["🧰 <b>Jobs</b>", "", "<b>Running now</b>"], []
        for job in running:
            lines.append(f"⏳ {e(job['title'])} · since {show_time(job['updated_at'], '%H:%M')}")
            from .telegram_ui import label
            buttons.append([{"text": label(f"✖ Cancel {job['title']}"), "callback_data": f"jobcancel:{job['id']}"}])
        for run in runs:
            lines.append(f"🎬 {e(self.db.run_label(run[0]))} · being made\n   <i>Cancel run stops it after its current stage.</i>")
            buttons.append([{"text": "⏹ Cancel run", "callback_data": f"runstop:{self.db.run_key(run[0])}"}])
        if not running and not runs:
            lines.append("Nothing is running.")
        icons = {"completed": "✅", "failed": "❌", "cancelled": "⏹"}
        finished = [j for j in jobs if j not in running][:8]
        if finished:
            lines += ["", "<b>Recent</b>"]
        for job in finished:
            line = f"{icons.get(job['state'], '•')} {show_time(job['updated_at'], '%H:%M')} {e(job['title'])}"
            if job["state"] == "cancelled":
                line += " (cancelled)"
            elif job.get("error_detail"):
                line += " — " + e(job["error_detail"][:140])
            elif job.get("delivery") in {"suppressed_or_unconfirmed", "failed"} and not job["title"].startswith("Receiving"):
                line += "\n   ⚠️ its result was not shown: you had left that screen. Open it again."
            lines.append(line)
        if not jobs and not runs:
            lines[-1] = "Nothing is running, and no jobs have run yet."
        return "\n".join(lines), buttons + [[{"text": "🔄 Refresh", "callback_data": "jobs:"}], [DONE_BUTTON]]

    def is_configured(self) -> bool:
        return is_configured(self.bot_token) and is_configured(self.chat_id) and bool(self.approver_ids)

    def create_publish_receipt(self, run_id: str, title: str, platforms: list[str] | None = None,
                               initial: dict[str, dict[str, Any]] | None = None) -> PublishReceipt:
        return PublishReceipt(self, run_id, title, platforms=platforms, initial=initial)

    def image_wizard(self):
        from . import config
        from .image_upload import ImageUpload
        return ImageUpload(self.db, config.BASE_DIR / "staging" / "telegram-uploads", config.ASSETS_DIR)

    def show_upload(self, state, note="", expected_revision=None):
        from .image_upload import PROMPTS
        if expected_revision is not None:
            current = self._current_panel(state["message_id"])
            if self._panel_revisions.get(current, 0) != expected_revision:
                return False
        stage = state["stage"]
        buttons = [[{"text": "Cancel upload", "callback_data": f"uploadcancel:{state['id']}"}]]
        if stage == "source":
            buttons.insert(0, [{"text": "📷 I took this photo", "callback_data": f"uploadown:{state['id']}"}])
        if stage == "licence":
            from . import config
            from .image_upload import OWN_WORK_LICENCES
            licences = (list(OWN_WORK_LICENCES) if state["rights"].get("own_work") else
                        # No "Public domain" button (QF-57): an upload carries no recorded worldwide basis, so a
                        # public-domain upload would be held and never used. CC0 covers a worldwide waiver.
                        ["CC0", "CC BY 4.0"] + (["CC BY-SA 4.0"] if config.IMAGES_ALLOW_SHARE_ALIKE else []))
            buttons.insert(0, [{"text": licence, "callback_data": f"uploadlic:{state['id']}:{licence}"} for licence in licences])
        text = f"🖼 <b>Upload: {html.escape(state.get('title', state['topic']))}</b>\n\n{html.escape(PROMPTS[stage])}"
        if note:
            text = html.escape(note) + "\n\n" + text
        if not self.show_navigation_panel(state["message_id"], text, buttons):
            message_id = self.notify(text, buttons=buttons, record_alert=False, group="studio")
        else:
            message_id = self._current_panel(state["message_id"])
        if type(message_id) is int:
            from .image_upload import _LOCK
            wizard = self.image_wizard()
            with _LOCK:
                current = wizard.load(state["chat"], state["user"])
                if current and current["id"] == state["id"]:
                    current["message_id"] = message_id
                    wizard.save(state["chat"], state["user"], current)
        return True

    def download_image(self, image):
        from .telegram_images import MAX_BYTES
        from .operator_jobs import checkpoint
        if image.get("file_size", 0) > MAX_BYTES:
            raise ValueError("Image exceeds 20 MiB")
        info = self._api("getFile", data={"file_id": image["file_id"]})
        content = bytearray()
        deadline = time.monotonic() + 90
        with requests.get(f"https://api.telegram.org/file/bot{self.bot_token}/{info['file_path']}", stream=True, timeout=30) as response:
            response.raise_for_status()
            for chunk in response.iter_content(65536):
                checkpoint()
                if time.monotonic() > deadline:
                    raise ValueError("Upload download timed out; retry explicitly")
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError("Image exceeds 20 MiB")
        return content

    def receive_guided_image(self, msg, image, state):
        chat, user = msg["chat"]["id"], msg["from"]["id"]
        try:
            updated = self.image_wizard().file(chat, user, self.download_image(image), state["id"])
            if updated:
                self._delete_soon(msg.get("message_id"))  # received and staged: the copy in the chat is clutter
                self.show_upload(updated, expected_revision=state.get("_navigation_revision"))
        except Exception as exc:
            if clean_chat():   # read, and answered in the upload panel: the photo leaves (UI-01d)
                self._delete_soon(msg.get("message_id"))
            current = self.image_wizard().load(chat, user)
            if current and current["id"] == state["id"]:
                self.show_upload(current, str(exc) if isinstance(exc, ValueError) else "The image could not be read; try another image")

    def edit_message_media(
        self,
        message_id: int,
        media_path_or_file_id: Any,
        caption: str = "",
        buttons: Optional[list] = None,
        cache_key: Optional[str] = None,
    ) -> Optional[str]:
        """Edits an existing media message in-place using Telegram's editMessageMedia API.
        If a cached Telegram file_id string is provided, zero bytes are uploaded.
        Returns the active Telegram file_id."""
        if not self.is_configured():
            return None
        is_file_id = isinstance(media_path_or_file_id, str) and not Path(media_path_or_file_id).is_file()
        media_input = {
            "type": "photo",
            "media": media_path_or_file_id if is_file_id else "attach://photo_file",
            "caption": safe_html_truncate(_MASKER.mask(caption), 1024),
            "parse_mode": "HTML",
        }
        data = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "media": json.dumps(media_input),
        }
        if buttons:
            markup = {"inline_keyboard": format_responsive_keyboard(buttons)}
            data["reply_markup"] = json.dumps(markup)

        try:
            if is_file_id:
                res = self._api("editMessageMedia", data=data)
            else:
                path = Path(media_path_or_file_id)
                with open(path, "rb") as f:
                    res = self._api("editMessageMedia", data=data, files={"photo_file": f})

            new_file_id = None
            if isinstance(res, dict) and res.get("photo"):
                new_file_id = res["photo"][-1].get("file_id")
                if new_file_id and cache_key:
                    self.db.set_cached_telegram_media(cache_key, new_file_id, "photo")
            return new_file_id
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return str(media_path_or_file_id)
            log.warning("editMessageMedia failed: %s", _MASKER.mask(str(e)))
            return None

    def register_commands(self) -> bool:
        """Registers bot commands with Telegram for native autocomplete and the Menu button."""
        if not self.is_configured():
            return False
        from .telegram_commands import menu
        commands = menu()
        try:
            self._api("setMyCommands", json={"commands": commands})
            return True
        except Exception as e:
            log.warning("Could not set Telegram commands: %s", e)
            return False

    def _api(self, method: str, timeout: float = 30, **kwargs) -> Dict[str, Any]:
        from .telegram_presentation import format_payload
        for field in ("data", "json"):
            if isinstance(kwargs.get(field), dict):
                from .telegram_state import prepare
                kwargs[field] = prepare(self.db, format_payload(method, kwargs[field]))
        # Edits address an existing message, so a connection retry cannot create a
        # second card. Sending/uploading a new message is deliberately excluded.
        retry_edit = method in {"editMessageMedia", "editMessageText", "editMessageCaption", "editMessageReplyMarkup"}
        streams = []
        for attachment in kwargs.get("files", {}).values():
            stream = attachment[1] if isinstance(attachment, tuple) else attachment
            if hasattr(stream, "tell") and hasattr(stream, "seek"):
                streams.append((stream, stream.tell()))
            elif not isinstance(stream, (bytes, str)):
                retry_edit = False
        for attempt in range(3):
            try:
                resp = _post(f"{self.base_url}/{method}", timeout=timeout, **kwargs)
            except requests.RequestException as e:  # its message carries the URL, and so the token
                if retry_edit and attempt < 2 and isinstance(e, (requests.ConnectionError, requests.Timeout)):
                    for stream, position in streams:
                        stream.seek(position)
                    log.info("Telegram %s connection interrupted; retrying the existing message edit.", method)
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Telegram {method} failed: {_MASKER.mask(str(e))}") from None
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if method == "editMessageText" and resp.status_code == 429 and retry_after:
                # A text edit is not waited for here: edit_message_text holds that message's edits for the time
                # Telegram gave and then sends the latest content once. Waiting and retrying in place blocked the
                # run for 16 s an edit and was refused again (production 2026-10-26: 25 refusals in 40 minutes).
                raise TelegramRateLimited(method, retry_after, str(data.get("description", "")))
            # Flood control (HTTP 429): wait as told, briefly, then retry. Uploads are not resent (their
            # file handles are consumed), and a long wait is left to the caller.
            if method != "answerCallbackQuery" and resp.status_code == 429 and retry_after and retry_after <= 30 and attempt < 2 and "files" not in kwargs:
                log.info("Telegram %s rate-limited; retrying in %ss.", method, retry_after)
                from . import slowdown
                slowdown.note("Telegram is rate-limiting the bot, waiting")
                time.sleep(float(retry_after))
                continue
            if method.startswith("editMessage") and "message is not modified" in str(data.get("description", "")):
                return data.get("result") or {"message_not_modified": True}
            if not resp.ok or not data.get("ok"):
                raise RuntimeError(f"Telegram {method} failed: HTTP {resp.status_code} {data.get('description', '')}")
            result = data["result"]
            if method in {"sendMessage", "sendPhoto", "sendAudio", "sendVideo", "sendMediaGroup"}:
                from .telegram_state import put
                payload = kwargs.get("data") or kwargs.get("json") or {}
                caption = html.unescape(re.sub(r"<[^>]+>", "", str(payload.get("text") or payload.get("caption") or "")))
                named = re.search(r"\brun_[\w]+", caption)
                run_id = named[0] if named and self.db.get_run(named[0]) else None
                for message in result if isinstance(result, list) else [result]:
                    if isinstance(message, dict) and message.get("message_id"):
                        put(self.db, "outbound", f"{self.chat_id}:{message['message_id']}", message['message_id'])
                        if run_id:
                            put(self.db, "message-run", f"{self.chat_id}:{message['message_id']}", run_id)
            if method in {"sendMessage", "sendPhoto", "sendAudio", "sendVideo", "sendMediaGroup", "editMessageMedia"}:
                # Each card's kind, so a later edit uses the method its message takes (_record_kind).
                for message in result if isinstance(result, list) else [result]:
                    if isinstance(message, dict) and type(message.get("message_id")) is int:
                        self._record_kind(message["message_id"], media=method != "sendMessage")
            return result
        raise RuntimeError(f"Telegram {method} failed: still rate-limited")

    @staticmethod
    def release_lines(report: Optional[Dict[str, Any]], cap: Optional[int] = None) -> str:
        """The release gate's measured values and verdict, for the review card (nothing assumed)."""
        if not report or not report.get("checks"):
            return "🚦 <b>Release gate:</b> <i>not run yet</i>\n"
        e = html.escape
        checks = {c["name"]: c for c in report.get("checks", [])}
        measured = []
        dur = re.search(r"Measured ([\d.]+)s", str((checks.get("duration") or {}).get("detail", "")))
        if dur:
            measured.append(f"{float(dur.group(1)):.1f} s")
        loud = (checks.get("loudness") or {}).get("detail")
        if isinstance(loud, dict) and loud.get("integrated_lufs") is not None:
            measured.append(f"{loud['integrated_lufs']} LUFS, peak {loud.get('true_peak_dbtp')} dBTP")
        drift = re.search(r"Measured ([\d.]+)ms", str((checks.get("sync_drift") or {}).get("detail", "")))
        if drift:
            measured.append(f"A/V drift {float(drift.group(1)):.0f} ms")
        open_checks = [c for c in report.get("checks", []) if c.get("status") != "pass"]
        minor = [c for c in report.get("checks", []) if c.get("status") == "pass" and c.get("minor")]
        if not open_checks and minor:
            # Minor audit findings allowed through (MINOR_FINDINGS_BLOCK_RELEASE=false): shown for the reviewer.
            from .release import check_problems
            found = "\n".join(_capped([f"• {e(c['name'])}: {e(p)}" for c in minor for p in check_problems(c)], cap))
            line = f"🚦 <b>Release gate:</b> ✅ all checks pass · minor findings, not blocking:\n<blockquote expandable>{found}</blockquote>\n"
        elif not open_checks:
            line = "🚦 <b>Release gate:</b> ✅ all checks pass\n"
        else:
            # What was found, in its own words (the card said "visual_checks: fail (… (>1.2s)'})" and
            # Approve led to a failure notice: 2026-10-24 production run).
            from .release import blocking_checks, check_problems
            blocking = sorted(blocking_checks(report, dry_run=report.get("dry_run", False)),
                              key=lambda c: c.get("status") != "fail")   # a failed check before one awaiting review
            blocked = bool(blocking)
            # What blocks first, then what does not (dev, 2026-10-27, Trepanation: ten files awaiting rights
            # review came first and the blocking visual_checks finding was cut off behind "…and 10 more").
            later = [c for c in open_checks if not any(c is b for b in blocking)]
            # A blocked check's minor findings are notes, after every reason (production, rc72, 2026-10-29: a minor
            # photo note was listed as a reason the release was blocked).
            found = "\n".join(_capped([f"• {e(c['name'])}: {e(p)}" for c in blocking for p in check_problems(c)]
                                      + [f"• {e(c['name'])}" + (" (not blocking)" if blocked else "") + f": {e(p)}"
                                         for c in later for p in check_problems(c)]
                                      + [f"• {e(c['name'])} (minor, not blocking): {e(p)}"
                                         for c in open_checks for p in check_problems(c, "notes")]
                                      # the operator's old-master note (QF-10b) is shown even when another
                                      # check blocks: it says what a re-render will also fix
                                      + [f"• {e(c['name'])} (note, not blocking): {e(p)}"
                                         for c in report.get("checks", [])
                                         if c.get("status") == "pass" and c.get("grandfathered")
                                         for p in check_problems(c)], cap))
            line = ("⛔ <b>Release gate:</b> blocked. This version can't be posted: Edit, Regenerate or Reject it.\n"
                    if blocked else "⚠️ <b>Release gate:</b> needs review before a live post.\n")
            line += f"<blockquote expandable>{found}</blockquote>\n"
        if measured:
            line += f"📏 <b>Measured:</b> <code>{e(' · '.join(measured))}</code>\n"
        return line

    @staticmethod
    def format_card_text(script_payload: Dict[str, Any], run_id: str, release: Optional[Dict[str, Any]] = None,
                         reserve: int = 0) -> str:
        """HTML review card (Telegram message limit is 4,096 characters), always one message: the narration
        and the captions come first; when the findings leave them no room, each folded list is shortened
        to its first items and "…and N more" (a long Wieliczka card lost its narration and both captions,
        then was paged as plain text with buttons that failed: review of 2026-10-25)."""
        card, _ = TelegramApprovalBot._ai_layout(script_payload, run_id, release, reserve)
        return card

    @staticmethod
    def _ai_layout(script_payload: dict[str, Any], run_id: str, release: dict[str, Any] | None,
                   reserve: int) -> tuple[str, int]:
        """(the card, the room its narration, captions and findings were fitted in). With the AI-label record
        (QF-65a) the brief AI line is a fact of the card: it does not change what else Approve's lines show (the slot
        line and the "Also posted" list or count, as the card without the record lays them out, the narration's
        old AI line included), unless keeping them would cost what QF-42 keeps (the last point):
        - when the QF-42 fit leaves room for it with those lines unchanged, the fit is untouched;
        - otherwise the fit gives up the line's room from what the card without the record fills, and when a fit
          step (a finding list's cap) frees more than that, the publish lines are held to the room they had
          (_with_publish_lines' `shift`);
        - what QF-42 keeps comes first (_qf42_kept: the narration whole, every caption named, the credits' lines,
          the caption previews): when giving up the line's room would cost one of them, the fit takes the room still
          left under the page cap with the publish lines unchanged; when no fit does, the card keeps QF-42's fit and
          the slot line or the "Also posted" count that no longer fits goes to the reply (needs_publish_reply), never
          a caption or the narration. A lost caption preview alone (the caption still named, "see Edit") is the
          line's accepted cost on the waiting card;
        - the decided card (`reserve`: its badge) stays in CARD_PAGE_LIMIT less the badge and has no slot line or
          "Also posted" count to give: where the line's room would cost what QF-42 keeps, it is QF-42's card with
          the line under its title only where that card still has room for it (round 3, QF-65b). A card QF-42 cannot
          fit in its page (its floor) has no line."""
        limit = CARD_PAGE_LIMIT - max(0, reserve)
        posted = script_payload.get("_publish_copy")
        natural = TelegramApprovalBot._fitted_card(script_payload, run_id, release, limit)
        if not isinstance(posted, dict):
            return natural, limit
        need = ai_line_room(posted)
        card = _with_publish_lines(natural, posted, limit, reserve)
        if not need:
            return card, limit
        from .telegram_pages import PAGE_ABOVE
        bare = {k: v for k, v in posted.items() if k not in ("ai_label", "ai_label_photos")}
        bare_payload = {**script_payload, "_publish_copy": bare}
        before_fit = TelegramApprovalBot._fitted_card(bare_payload, run_id, release, limit)
        bare_card = _with_publish_lines(before_fit, bare, limit, reserve)
        before = _publish_lines_shown(bare_card)
        # The page the card fills: one message under PAGE_ABOVE; the decided card stays in the badge's room (QF-43).
        cap = limit if reserve > 0 else PAGE_ABOVE
        if _tg_len(card) <= cap and _publish_lines_shown(card) == before:
            return card, limit

        def laid_out(fit_limit: int) -> tuple[str, str]:
            fitted = TelegramApprovalBot._fitted_card(script_payload, run_id, release, fit_limit)
            shift = max(0, _tg_len(before_fit) - (_tg_len(fitted) + need))
            return fitted, _with_publish_lines(fitted, posted, limit, reserve, shift=shift)

        def on_page(text: str) -> str:
            """The card within `cap`, or the line goes to the reply: QF-42's fit cannot shrink below the page less
            the line (its floor; a long name's Approved badge on 2 of 140 real decided renders, QF-65a round 3)."""
            return _without_ai_line(text) if _tg_len(text) > cap else text

        fit_limit = max(0, _tg_len(before_fit) - need)
        fitted, laid = laid_out(fit_limit)
        if _qf42_kept(fitted, before_fit):
            return on_page(laid), fit_limit
        if reserve > 0:
            # The decided card (rebuilt with its badge, _render_decision) has no slot line or "Also posted" count to
            # give, and its page is the badge's room (QF-43): where the line's room would cost what QF-42 keeps (a
            # caption or the whole narration on up to 33 of 860 fixture shapes, QF-65a round 2 review), it is QF-42's
            # card without the line, its publish lines as that card lays them out (and without the narration's old
            # AI line, which the record replaces). The AI line, whole or else brief, is then put back under the title
            # where that card still has room for it in the badge's room (QF-65b): the line is the only change, so it
            # costs QF-42 nothing (the reviewer's real decided renders without the line: 11 -> 8).
            plain = _without_ai_line(bare_card)
            for line in (_ai_label_line(posted), _ai_label_line(posted, brief=True)):
                trial = _under_title(plain, line)
                if trial != plain and _tg_len(trial) <= limit:
                    return trial, limit
            return plain, limit
        # The line's own room would cost what QF-42 keeps (a caption's preview or its "see Edit" pointer, the whole
        # narration, the credits' lines: a real card lost its caption's pointer, QF-65a round 2): the fit takes the
        # room the page still has under `cap` with the publish lines as the card without the record shows them.
        roomy = min(limit, cap - need - (_tg_len(bare_card) - _tg_len(before_fit)))
        if roomy > fit_limit:
            fitted_r, laid_r = laid_out(roomy)
            if (_qf42_kept(fitted_r, before_fit) and _tg_len(laid_r) <= cap
                    and _publish_lines_shown(laid_r) == before):
                return laid_r, roomy
        if not _qf42_kept(fitted, before_fit, previews=False) and _tg_len(card) <= cap:
            return card, limit   # no fit keeps a caption named or the narration whole: the publish lines go to the reply
        return on_page(laid), fit_limit

    @staticmethod
    def card_fit_limit(script_payload: dict[str, Any], run_id: str, release: dict[str, Any] | None = None,
                       reserve: int = 0) -> int:
        """The room the card's narration, captions and findings are fitted in (_ai_layout): the page limit, or,
        when the brief AI-label line (QF-65a) would change what else the card shows, what the card would fill
        less the line's room (or, when that would cost what QF-42 keeps, the room left under the page cap)."""
        return TelegramApprovalBot._ai_layout(script_payload, run_id, release, reserve)[1]

    @staticmethod
    def _fitted_card(script_payload: dict[str, Any], run_id: str, release: dict[str, Any] | None, limit: int) -> str:
        """The card in `limit`, fitted as QF-42 fits it. What else Approve posts and the slot it books are laid out
        afterwards, in the room this leaves (_with_publish_lines), so they cost the narration, the captions and the
        findings nothing (review of QF-43 on 30 real cards)."""

        def first_fit(credits: bool, previews: int = 0) -> tuple:
            card = ""
            for cap in (None, 4, 3, 2, 1, 0):
                card = TelegramApprovalBot._card_text(script_payload, run_id, release, cap, limit, credits=credits)
                # The narration whole first; the findings are shortened only as far as that needs; the captions
                # take what is left (the long caption is clipped, "full text: Edit", as before). Every caption
                # credit keeps its own line (QF-42), in the shortest form at worst (_card_text's credits).
                captions = all(f"<b>{label}:</b>" in card for key, label in (("caption", "Caption"), ("carousel_caption", "Carousel caption"))
                               if script_payload.get(key))  # at least its first lines, or where to read it
                if ("[shortened:" not in card and captions and CREDITS_UNLISTED not in card
                        and _tg_len(card) <= limit and _caption_previews(card) >= previews):
                    return card, True
            return card, False

        card, fits = first_fit(True)
        if not fits or CREDITS_HEAD not in card:
            return card
        # The credits list costs no caption the preview the card without it shows (review of QF-42: six cards lost
        # theirs): the findings are shortened further first, when that gives it back.
        target = _caption_previews(first_fit(False)[0])
        if _caption_previews(card) >= target:
            return card
        better, fits = first_fit(True, target)
        return better if fits else card

    @staticmethod
    def _card_text(script_payload: Dict[str, Any], run_id: str, release: Optional[Dict[str, Any]], cap: Optional[int],
                   limit: int = CARD_PAGE_LIMIT, credits: bool = True, extras: bool = False) -> str:
        """The review card at findings cap `cap`. `credits=False`: the card as it was before the credits list
        (QF-42), each credit line only inside its caption: format_card_text's measure of the captions' room.
        `extras`: with the whole "Also posted" list (QF-43), as the reply under the card has it."""
        e = html.escape
        # What Approve posts, as each platform receives it (QF-43: publishable.card_record, added by
        # send_review_card): the caption after the illustration disclosure, the hashtags from the topic's list. A
        # carousel posting the Reel caption (a script without its own) is in the "Also posted" list, not repeated.
        posted = script_payload.get("_publish_copy")
        copy = (posted or {}).get("copy") or {} if isinstance(posted, dict) else {}
        if isinstance(posted, dict):
            script_payload = {**script_payload, **{key: copy[label] for key, label in (
                ("title", "reel.title"), ("caption", "reel.caption"), ("carousel_caption", "carousel.instagram"),
                ("first_comment", "reel.first_comment")) if label in copy and (key != "carousel_caption"
                                                                               or script_payload.get(key))}}
        loc = script_payload.get("location") or {}
        place = ", ".join(e(str(v)) for v in (loc.get("name"), loc.get("country")) if v)
        regen = script_payload.get("_regeneration_note")  # added by send_review_card for a regenerated run
        audits = script_payload.get("fact_audits") or []
        from .models import GOOD_VERDICTS
        flagged = [a for a in audits if a.get("verdict") not in GOOD_VERDICTS]
        sources = script_payload.get("sources", [])[:4]
        conf = script_payload.get("consensus_confidence")
        integrity = f"🛡️ <b>Integrity score:</b> {conf}% (Pass — {len(flagged)} hedged)" if not flagged else f"⚠️ <b>Integrity alert:</b> {conf}% ({len(flagged)} disputed/hedged)"

        if not audits or conf is None:
            integrity = "⚠️ <b>Integrity:</b> verification not recorded"
        # Every image credit the captions carry, each on its own line in its own folded list (QF-42): they were only
        # inside the caption block, which a long caption clips or replaces with "too long for this card".
        # The list has a length budget (review of QF-42: whole lines, two URLs each, pushed real cards past Telegram's
        # 4,096): it is filled after the narration, in the longest of _credit_card_line's forms that fits.
        from .credits import split_attribution
        credit_lines = list(dict.fromkeys(line.strip() for key in ("caption", "carousel_caption")
                                          for line in split_attribution(script_payload.get(key) or "")[1])) if credits else []
        credit_forms = []
        for form in range(4):
            shown = [_credit_card_line(x, form) for x in credit_lines]
            if form >= 2:  # a short form can repeat itself (five photos by one author): each once, with its count
                counts = Counter(shown)
                shown = [x if counts[x] == 1 else f"{x} (×{counts[x]})" for x in dict.fromkeys(shown)]
            credit_forms.append("\n" + listed(f"{CREDITS_HEAD} {len(credit_lines)}", [e(x) for x in shown])
                                + (f"\n<i>Shortened to fit: {CARD_LIST_MORE}</i>" if shown != credit_lines else "") + "\n")

        text = (
            f"🎬 <b>Master review</b>\n\n"
            f"📌 <b>{e(script_payload.get('title', ''))}</b>\n"
            + (f"🔄 <i>Regenerated · your feedback: {e(regen)}</i>\n" if regen else "")
            + (f"📍 <i>{place}</i>\n" if place else "") +
            f"⏱️ {script_payload.get('word_count', 0)} words · written by: <i>{e(origin_label(script_payload.get('origin', '?')))}</i>\n"
            f"{integrity}\n"
            + _second_check_line(script_payload.get("second_check"))
            + TelegramApprovalBot.release_lines(release, None if cap is None else max(cap, 2)) + "\n"
            "<blockquote expandable><b>Narration and timings:</b>\n<i>\x00NARRATION\x00</i>\n\n"
            f"<b>First comment:</b> <i>{e(script_payload.get('first_comment', ''))}</i>\n"
            # Everything Approve publishes is readable here (the caption and carousel caption were only
            # visible in the Edit template: 2026-10-23 UI review). Inside the collapsed section.
            # Filled last from whatever room the full narration leaves (format_card_text's end); Edit always
            # has the whole text.
            + ("\x00CAPTION\x00" if script_payload.get("caption") else "")
            + ("\x00CAROUSEL\x00" if script_payload.get("carousel_caption") else "")
            + (_posted_hashtags_line(posted) if isinstance(posted, dict) and not posted.get("documentary")
               else f"<b>Hashtags:</b> {e(' '.join(script_payload['hashtags']))}" if script_payload.get("hashtags")
               else f"<b>Tags:</b> {e(', '.join(script_payload.get('tags') or []))}") + "</blockquote>\n"
            + ("\x00CREDITS\x00" if credit_lines else "")
        )
        if extras and isinstance(posted, dict) and _also_posted(posted, copy):
            # Its own section (detail_messages' sections are split at a blank line). On the card it takes only the
            # room the fitted card leaves (_with_publish_lines); the reply under the card has it whole.
            text += "\n" + listed(ALSO_HEAD, _also_posted(posted, copy)) + "\n"
        poll = script_payload.get("poll") or {}
        if poll.get("question") and poll.get("options") and len(poll["options"]) >= 2:
            opts = [e(str(o)) for o in poll["options"]]
            text += f"📊 <b>Debate poll:</b> {e(poll['question'])} (A: {opts[0]} / B: {opts[1]})\n"
        # Whether the post carries the AI label and why, from the media Approve posts (QF-65a: the card's record,
        # publishable.card_record). The card lays it out with what else Approve posts (_with_publish_lines), so it
        # costs the narration nothing; the reply under the card (`extras`) has it whole. A card without the record
        # keeps the narration's line.
        ai_line = _ai_label_line(posted)
        if ai_line and extras:
            text += ai_line + "\n"
        elif not ai_line and script_payload.get("is_generated_by_ai"):
            text += "🤖 <b>AI transparency:</b> Synthetic media label embedded for Meta CMP compliance\n"
        def _format_note(note_val: Any) -> str:
            if not note_val:
                return ""
            raw = str(note_val).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").strip()
            if ":" in raw:
                lbl, _, rest = raw.partition(":")
                # The emoji stays outside the bold, as on every other line of the card ("🛡️ <b>Integrity
                # score:</b>"): notes had it inside ("<b>🗂 Topic origin:</b>").
                icon, words = (lbl.split(" ", 1) + [""])[:2] if lbl and not lbl[0].isalnum() else ("", lbl)
                return (f"{e(icon)} " if icon else "") + f"<b>{e(words.strip())}:</b> {e(rest.strip())}"
            return e(raw)

        if script_payload.get("scene_plan_note"):  # first: a picture problem decides the review
            text += "\n" + _format_note(script_payload["scene_plan_note"])
        if script_payload.get("topic_origin_note"):
            text += "\n" + _format_note(script_payload["topic_origin_note"])
        if script_payload.get("fact_sheet_note"):
            text += "\n" + _format_note(script_payload["fact_sheet_note"])
        if script_payload.get("hook_signal_note"):  # an opening shipped without an early signal (QF-14): judge it
            text += "\n" + _format_note(script_payload["hook_signal_note"])
        if script_payload.get("payoff_note"):  # a payoff shipped naming no opening noun (QF-15): judge it
            text += "\n" + _format_note(script_payload["payoff_note"])
        if script_payload.get("hedge_note"):  # an opening, payoff or loop shipped qualified (QF-16): judge it
            text += "\n" + _format_note(script_payload["hedge_note"])
        if script_payload.get("trimmed_sentences"):  # said, so the reviewer can judge the flow without them
            where = list(script_payload.get("trimmed_from") or [])  # where each was read, beyond the narration
            where += [""] * (len(script_payload["trimmed_sentences"]) - len(where))
            text += "\n" + _format_note("✂️ Removed after the fact check blocked them: " + " ".join(
                f"“{x}”" + (f" ({w})" if w and w != "narration" else "")
                for x, w in zip(script_payload["trimmed_sentences"], where)))
        if script_payload.get("picture_lines_note"):  # lines checked against the photos before the voice (2026-10-28)
            text += "\n" + _format_note(script_payload["picture_lines_note"])
        if script_payload.get("voice_note"):
            text += "\n" + _format_note(script_payload["voice_note"])
        if script_payload.get("music_note"):
            text += "\n" + _format_note(script_payload["music_note"])
        if script_payload.get("motion_note"):
            text += "\n" + _format_note(script_payload["motion_note"])
        if script_payload.get("graphics_note"):
            text += "\n" + _format_note(script_payload["graphics_note"])
        if script_payload.get("thumbnail_note"):   # QF-23: the YouTube thumbnail's headline, never cut
            text += "\n" + _format_note(script_payload["thumbnail_note"])
        holds = layout_hold_lines(script_payload.get("layout_holds"))
        if holds:   # QF-23: a deck or graphic refused rather than drawn with its checked copy cut
            text += "\n" + listed(f"✋ Held, its text would not fit whole: {len(holds)}", [e(x) for x in holds], cap) + "\n"
        if script_payload.get("reconstruction_note"):
            text += "\n" + _format_note(script_payload["reconstruction_note"])
        if script_payload.get("footage_note"):
            text += "\n" + _format_note(script_payload["footage_note"])
        # Each outside photo whole, folded (operator, 2026-10-25: cut mid-word); a run made before the lines were
        # kept has them in its credits.
        photo_lines = script_payload.get("beat_image_lines") or [
            f"line {c.get('beat', '?')}: {str(c.get('looked') or c.get('title', '')).removeprefix('File:')}"
            for c in script_payload.get("beat_image_credits") or [] if isinstance(c, dict)]
        if photo_lines and script_payload.get("beat_image_note"):
            text += "\n" + listed(e(script_payload["beat_image_note"].partition(":")[0]), [e(x) for x in photo_lines], cap)
        elif script_payload.get("beat_image_note"):
            text += "\n" + _format_note(script_payload["beat_image_note"])
        if script_payload.get("source_note"):
            text += "\n" + _format_note(script_payload["source_note"]) + "\n"
        if script_payload.get("qa_summary"):  # already HTML-escaped by video_qa: its heading, then each issue folded
            head, *found = script_payload["qa_summary"].splitlines()
            text += "\n" + (listed(head, [x.lstrip("· ").strip() for x in found], cap) if found else head) + "\n"
        # The look at the stills and the cover (info findings: never a block) was kept but shown nowhere when
        # the gate passed (2026-10-24 review doc said they were on the card; audit 2026-10-25).
        notes = [i for i in ((script_payload.get("self_healing") or {}).get("issues") or [])
                 if isinstance(i, dict) and str(i.get("severity", "")).lower() == "info" and i.get("issue")]
        if notes:
            # Each note whole, folded, as the gate's findings are (operator, 2026-10-25: cut mid-word at 160)
            text += "\n" + listed(f"👀 Look at the stills and cover: {len(notes)} note(s)", cap=cap, items=
                                  [f"{e(str(i.get('card') or i.get('type') or ''))}: {e(str(i['issue']))}" for i in notes]) + "\n"
        from .config import OUTPUT_DIR
        r_dir = (Path(OUTPUT_DIR) / str(run_id)).resolve() if run_id else None
        card_notes = (design_card_notes(r_dir) if r_dir is not None and r_dir.parent == Path(OUTPUT_DIR).resolve()
                      and r_dir.is_dir() else [])
        if card_notes:     # freeze panel M12 (3): e.g. a myth card whose correction drops every specific
            text += "\n" + listed(f"🃏 Design cards: {len(card_notes)} note(s)", [e(x) for x in card_notes], cap) + "\n"
        type_notes = (glyph_notes(r_dir) if r_dir is not None and r_dir.parent == Path(OUTPUT_DIR).resolve()
                      and r_dir.is_dir() else [])
        if type_notes:     # QF-94: a field set in a fallback face, or refused, is never silent
            text += "\n" + listed(f"🔤 Lettering: {len(type_notes)} note(s)", [e(x) for x in type_notes], cap) + "\n"
        if sources:
            text += "\n📚 " + "\n📚 ".join(e(s.get("url", "")) for s in sources) + "\n"
        full = text + f"\n<i>Run ID (for the command line):</i> <code>{e(run_id)}</code>"  # code: Telegram linked the digits
        # A card over the page limit is paged as plain text and loses its collapsible script, so a long
        # (documentary) narration is shortened inside it instead; the full script is in the run folder.
        narration = script_payload.get("narration_script", "")
        unlisted = f"\n{CREDITS_UNLISTED} {len(credit_lines)}: {CARD_LIST_MORE}\n"  # no form of the list fits
        bare = full.replace("\x00CAPTION\x00", "").replace("\x00CAROUSEL\x00", "").replace("\x00NARRATION\x00", "")
        note = f" … [shortened: all {len(narration.split())} words are in the run's script_metadata.json]"
        # Room is kept for every credit's own line in the shortest form (the author: QF-42); on a card the notes
        # already fill (no word of the narration fits), for the count alone.
        room = limit - _tg_len(_said_once(bare.replace("\x00CREDITS\x00", credit_forms[-1] if credit_lines else "")))
        if credit_lines and room < _tg_len(e(narration)) and room <= _tg_len(e(note)):
            room = limit - _tg_len(_said_once(bare.replace("\x00CREDITS\x00", unlisted)))
        if _tg_len(e(narration)) > room:
            cut = narration
            while cut and _tg_len(e(cut)) + _tg_len(e(note)) > room:
                cut = cut[:-40].rsplit(" ", 1)[0]
            narration = cut + note
        full = full.replace("\x00NARRATION\x00", e(narration), 1)
        # The captions take only the room the narration leaves (adding them in full shortened the narration
        # of real cards: 2026-10-23 UI review); what does not fit says where the whole of it is.
        # Without its hashtags: they have their own line on the card (operator, 2026-10-25). Posted as written.
        captions = [(label, marker, without_hashtags(script_payload.get(key) or "")) for key, label, marker in (
            ("carousel_caption", "Carousel caption", "\x00CAROUSEL\x00"),
            ("caption", "Caption", "\x00CAPTION\x00")) if marker in full]  # the short one first
        if credit_lines:  # shown in the credits list
            captions = [(label, marker, split_attribution(value)[0]) for label, marker, value in captions]

        def caption_block(label: str, value: str, spare: int) -> tuple:
            """The caption's block in `spare` (whole, clipped at a word, or where to read it) and how many
            characters of its text it shows."""
            head = f"\n<b>{label}:</b>\n"
            block = head + e(value) + "\n"
            if _tg_len(block) <= spare:
                return block, len(value)
            fit = spare - _tg_len(head) - 30
            while fit >= 120:  # clipped at a word to the room (its escaped form can be longer than the clip)
                clipped = _clip_copy(value, fit)
                block = head + e(clipped) + "\n"
                if _tg_len(block) <= spare:
                    return block, len(clipped.removesuffix(" (full text: Edit)"))
                fit -= _tg_len(block) - spare
            block = head + CAPTION_TOO_LONG + "\n"  # else where to read it, when that fits
            return (block, 0) if _tg_len(block) <= spare else ("", 0)

        def fill(card: str) -> tuple:
            """`card` with its captions in the room left, and its score: (captions on the card, captions with a
            preview, characters of their text shown). A caption on the card, if only as where to read it, ranks
            ahead of another's preview (review of QF-42: Wieliczka dropped its carousel caption for a clipped main
            caption, though both "see Edit" lines fit)."""
            spare = limit - _tg_len(_said_once(card.replace("\x00CAPTION\x00", "").replace("\x00CAROUSEL\x00", "")))
            whole = [_tg_len(f"\n<b>{label}:</b>\n" + e(value) + "\n") for label, _, value in captions]
            heads = [_tg_len(f"\n<b>{label}:</b>\n") for label, _, _ in captions]
            # On a card with credits the short caption, filled first, may keep back the room of the main caption's
            # clipped preview, or of where to read it (review of QF-42: a whole carousel caption took the room, and
            # the caption read "too long for this card" or was left out): whichever shows the captions best.
            keeps = [[0] * len(captions)] + ([[min(w, h + 150) for w, h in zip(whole, heads)],
                                              [min(w, h + _tg_len(CAPTION_TOO_LONG + "\n")) for w, h in zip(whole, heads)]]
                                             if credit_lines else [])
            best = None
            for keep in keeps:
                out, left, score = card, spare, (0, 0, 0)
                for i, (label, marker, value) in enumerate(captions):
                    block, chars = caption_block(label, value, left - sum(keep[i + 1:]))
                    out, left = out.replace(marker, block, 1), left - _tg_len(block)
                    score = (score[0] + bool(block), score[1] + bool(block and (chars or not value)), score[2] + chars)
                if best is None or score > best[0]:
                    best = (score, out)
            return best[1], best[0]

        if credit_lines:
            # The credits next, in the longest form that leaves each caption a preview and the captions their text
            # (at most CARD_CAPTION_ROOM of it); else the form that shows the most of the captions (a shorter form
            # before a caption is dropped); the count alone, and where the lines are, only when no form fits (a
            # card the findings already fill: format_card_text then shortens those first).
            whole = sum(len(value) for _, _, value in captions)
            best = None
            for section in credit_forms:
                with_credits = full.replace("\x00CREDITS\x00", section, 1)
                if _tg_len(_said_once(with_credits.replace("\x00CAPTION\x00", "").replace("\x00CAROUSEL\x00", ""))) > limit:
                    continue
                card, score = fill(with_credits)
                if score[1] == len(captions) and score[2] >= min(whole, CARD_CAPTION_ROOM):
                    best = (score, card)
                    break
                if best is None or score > best[0]:
                    best = (score, card)
            full = best[1] if best else fill(full.replace("\x00CREDITS\x00", unlisted, 1))[0]
        else:
            full = fill(full)[0]
        return _said_once(full)

    def send_approval_request(self, video_path: Path, thumbnail_path: Path, script_payload: Dict[str, Any], run_id: str) -> Optional[int]:
        """Everything one Approve publishes, then the card: an album with the cover, a scene strip and
        the review video; an album with the Story frames and carousel slides; the review card.
        Review media is protected (no forwarding or saving of unreleased work)."""
        previous = self.db.get_approval(run_id) if hasattr(self.db, "get_approval") else None
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), None)
        from .telegram_state import put, get as get_state_early
        put(self.db, "review-media", run_id, None)
        if not self.is_configured():
            log.warning("Telegram not configured. Review with: python main.py review approve|reject %s", run_id)
            return None
        earlier_card = (get_state_early(self.db, "review-messages", run_id) or [None])[-1]
        if clean_chat():
            pass   # the review panel is shared by every review waiting: its keyboard is redrawn, never stripped
        elif previous and previous.get("telegram_message_id"):
            self._strip_buttons(previous["telegram_message_id"])  # a resend leaves one live card
        elif isinstance(earlier_card, int):
            # An edit or a cover lock clears the approval's card id first, so the card the chat still showed
            # kept its buttons while the new one was delivered: two live review cards (prod, 2026-10-29).
            self._strip_buttons(earlier_card)
        release = None
        try:
            report_file = Path(video_path).parent / "release_report.json"
            release = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
        except (OSError, ValueError) as e:
            log.warning("Could not read the release report for the review card: %s", e)
        if release is not None:
            try:  # the gate as it stands now: a card reopened after a fix showed the old verdict (Amber Room,
                # regenerated 26 Oct, blocked again on 28 Oct by its pre-rc66 rights record)
                from .release import write_release_report
                run = self.db.get_run(run_id) if hasattr(self.db, "get_run") else None
                if run and run.get("master_video_path"):
                    release = write_release_report(run, dry_run=bool(release.get("dry_run")))
            except Exception as e:  # never lose the card over it: the saved verdict stands
                log.warning("Could not re-check the release gate for the review card: %s", e)
        from .telegram_state import review_fingerprint, put, get as get_state
        if clean_chat():
            # One paged review panel (UI-01e): the review joins it; no album, card or "In full" reply is sent.
            try:
                return self.review.deliver(run_id, Path(video_path), Path(thumbnail_path), script_payload, release)
            except Exception as e:  # noqa: BLE001 - the review stays pending; Resend delivers it again
                log.warning("Could not deliver the review of %s to the panel: %s", run_id, _MASKER.mask(str(e)))
                # A sender's item (UI-01a): through notifications.say with its §10.2 action; delivery closes it.
                from . import notifications as notes
                notes.say(self.notify, f"⚠️ <b>Review not delivered</b> · {html.escape(self.db.run_label(run_id))}\n\n"
                          "Approval is unavailable until it is delivered. Resend it once the cause is fixed.\n"
                          + html.escape(plain_reason(e, 300)),
                          group="needs", key=f"undelivered:{run_id}", run=run_id, replace=True,
                          subject=self.db.run_label(run_id),
                          actions=[{"text": "Resend for review", "callback_data": f"resend:{self.db.run_key(run_id)}"}])
                return None
        if config.TELEGRAM_CARDS_V2:
            # Only when every value it shows comes from the run; otherwise the live card, and why (UI-02)
            try:
                plan = self._v2_review_plan(run_id, script_payload, release, Path(video_path).parent)
            except V2CardUnavailable as e:
                log.warning("V2 review card not used for %s: %s. Sending the live card instead.", run_id, e)
            else:
                return self._send_review_pair_v2(Path(video_path), Path(thumbnail_path), script_payload, run_id,
                                                 release, plan=plan)
        earlier = get_state(self.db, "review-messages", run_id) or []  # the previous delivery's card and albums
        media_ids: List[int] = []
        try:
            fingerprint = review_fingerprint(self.db, run_id)
            media_ids = self._send_review_media(Path(video_path), Path(thumbnail_path), script_payload) or []
            if fingerprint != review_fingerprint(self.db, run_id):
                raise ValueError("Review artifacts changed during delivery")
            put(self.db, "review-media", run_id, fingerprint)
        except Exception as e:
            log.warning("Could not send the review media: %s", _MASKER.mask(str(e)))
            # A way out on the notice itself: it said "resend the review" but offered only the dashboard.
            self.notify("⚠️ Review media was not delivered. Approval is unavailable. Resend the review after resolving delivery.",
                        buttons=[{"text": "📨 Resend for review", "callback_data": f"resend:{self.db.run_key(run_id)}"}],
                        record_alert=True, group="needs")
            return None
        try:
            card = self.send_review_card(run_id, script_payload, release)
        except Exception as e:
            log.error("Failed to send Telegram approval request: %s. Use the CLI to review %s.", e, run_id)
            return None
        if card:
            # Only once the new card is in the chat: the previous card and its albums go, so a resend or an
            # edit replaces the review instead of stacking another copy (dev, 2026-10-23). Removing them
            # first would leave no review if this delivery had failed.
            old = [m for m in [*(earlier or []), (previous or {}).get("telegram_message_id")] if isinstance(m, int)]
            old += self._superseded_review_messages(run_id)  # a regenerated version replaces its predecessor
            from .telegram_state import get as state_get
            notice = state_get(self.db, "timeout-notice", run_id)
            if isinstance(notice, int):  # "No decision ... resend it": done now
                old.append(notice)
                put(self.db, "timeout-notice", run_id, None)
            put(self.db, "review-messages", run_id, media_ids + [card])
            # The first album opens with the cover (_send_review_media): a cover lock replaces that photo in
            # place (refresh_review_cover) instead of delivering the whole review again.
            put(self.db, "review-cover", run_id, media_ids[0] if media_ids and Path(thumbnail_path).is_file() else None)
            stale = sorted(set(old) - set(media_ids) - {card})
            if stale:
                removed = self.delete_messages(stale)
                log.info("Replaced the earlier review of %s: %d of %d message(s) removed.", run_id, removed, len(stale))
        return card

    def _send_review_pair_v2(self, video_path: Path, thumbnail_path: Path, script_payload: Dict[str, Any],
                             run_id: str, release: dict[str, Any] | None = None,
                             plan: dict[str, Any] | None = None) -> int | None:
        """Telegram V2 Two-Card Review Pair in topic Reviews (4908):
        1. Plain sendVideo with cover and solid inline decision buttons.
        2. Rich card directly underneath with computed verdict lede, hook quote, facts table, and folded Details.
        3. Plain notification line in Needs you (4907): '🎬 Review waiting · {short_name}'.
        """
        from .telegram_state import put, get as get_state, review_fingerprint
        if not video_path.is_file() or not 0 < video_path.stat().st_size <= TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024:
            raise ValueError("A deliverable review video is required")

        # Honor test overrides/harnesses on _send_review_media if monkeypatched
        if hasattr(self, "_send_review_media") and getattr(self, "_send_review_media") != type(self)._send_review_media:
            try:
                self._send_review_media(video_path, thumbnail_path, script_payload)
            except Exception as e:
                log.warning("Preview media failure hook triggered: %s", e)
                return None

        key = self.db.run_key(run_id)
        plan = plan or self._v2_review_plan(run_id, script_payload, release, video_path.parent)
        blocked = plan["blocked"]
        title = script_payload.get("title", self.db.run_label(run_id))

        previous = self.db.get_approval(run_id) if hasattr(self.db, "get_approval") else None
        earlier = (get_state(self.db, "review-messages", run_id) or []) + self._superseded_review_messages(run_id)
        notice_id = get_state(self.db, "review-need-notice", run_id)
        if isinstance(notice_id, int):
            earlier.append(notice_id)
            put(self.db, "review-need-notice", run_id, None)

        # 1. Message 1: sendVideo with cover and solid inline decision buttons
        kb = _v2_review_keyboard(key, blocked)
        fingerprint = review_fingerprint(self.db, run_id)
        vid_params = {
            "chat_id": self.chat_id,
            "message_thread_id": config.TELEGRAM_TOPICS["reviews"],
            "supports_streaming": True,
            "caption": f"🎬 {title}",
            "reply_markup": json.dumps(kb),
            "disable_notification": True,
        }
        vid_files = {"video": open(video_path, "rb")}
        if thumbnail_path.is_file():
            vid_files["cover"] = open(thumbnail_path, "rb")

        try:
            vid_resp = self._api("sendVideo", timeout=300, data=vid_params, files=vid_files)
        except Exception as e:
            log.warning("Could not send review video: %s", e)
            vid_resp = None
        finally:
            for f in vid_files.values():
                f.close()

        vid_mid = vid_resp.get("message_id") if isinstance(vid_resp, dict) else None
        if not vid_mid:
            log.error("Failed to send review video for %s: %s", run_id, vid_resp)
            return None
        if fingerprint != review_fingerprint(self.db, run_id):
            log.warning("Review artifacts of %s changed during delivery; the review is not offered.", run_id)
            self.delete_message(vid_mid)
            return None
        # The media is delivered for this copy: recorded now, so the live card can be the fallback (the rich
        # card's failure deleted the video and sent nothing, since send_review_card refused without it: UI-02)
        put(self.db, "review-media", run_id, fingerprint)

        # 2. Message 2: Rich Details Card directly under Message 1
        card_blocks = plan["blocks"]
        rich_params = {
            "chat_id": self.chat_id,
            "message_thread_id": config.TELEGRAM_TOPICS["reviews"],
            "rich_message": json.dumps({"blocks": card_blocks}),
            "disable_notification": True,
        }
        card_mid = None
        try:
            rich_resp = self._api("sendRichMessage", timeout=120, data=rich_params)
            card_mid = rich_resp.get("message_id") if isinstance(rich_resp, dict) else None
        except Exception as e:
            log.warning("sendRichMessage failed: %s", e)
            rich_resp = None

        if not card_mid:
            log.warning("Failed to send review rich card for %s: %s; sending the live card under the video", run_id, rich_resp)
            # The live card decides: the video keeps no decision buttons of its own
            try:
                self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": vid_mid,
                                                          "reply_markup": json.dumps({"inline_keyboard": []})})
            except Exception as e:  # noqa: BLE001 - the live card is sent either way
                log.warning("Could not remove the review video's buttons: %s", e)
            try:
                card_mid = self.send_review_card(run_id, script_payload, release)
            except Exception as e:
                log.error("Failed to send fallback review card: %s", e)
                card_mid = None
            if not card_mid:
                # Only once no card could be sent: no headless video sits in the chat
                self.delete_message(vid_mid)
                return None
            put(self.db, "review-messages", run_id, [vid_mid, card_mid])
            put(self.db, "review-cover", run_id, None)
            stale = sorted({m for m in earlier if isinstance(m, int)} - {vid_mid, card_mid})
            if stale:
                self.delete_messages(stale)
            return card_mid

        from . import publishable
        publishable.save_record(self.db, run_id, plan["posted"])   # before the card's buttons can decide (QF-43)
        put(self.db, "review-messages", run_id, [vid_mid, card_mid])
        put(self.db, "review-card", f"{run_id}:{vid_mid}", fingerprint)
        put(self.db, "review-card", f"{run_id}:{card_mid}", fingerprint)
        put(self.db, "message-run", f"{self.chat_id}:{card_mid}", run_id)
        put(self.db, "message-run", f"{self.chat_id}:{vid_mid}", run_id)
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), vid_mid)

        # 3. Message 3: Single audible alert line in 🔔 Needs you
        from .control import clip_words
        short_name = clip_words(self.db.run_label(run_id), 36)   # at a word, with an ellipsis (it cut mid-word)
        need_notice = self._api("sendMessage", data={
            "chat_id": self.chat_id,
            "message_thread_id": config.TELEGRAM_TOPICS["needs"],
            "text": f"🎬 Review waiting · {short_name}",
            "link_preview_options": json.dumps({"is_disabled": True}),
        })
        if isinstance(need_notice, dict) and "message_id" in need_notice:
            put(self.db, "review-need-notice", run_id, need_notice["message_id"])

        self.db.put_telegram_card(
            card_id=f"review:{run_id}",
            topic_id=config.TELEGRAM_TOPICS["reviews"],
            message_id=vid_mid,
            card_kind="review_pair",
            payload={"video_mid": vid_mid, "card_mid": card_mid, "run_id": run_id, "status": "waiting"},
        )

        # 4. Clean up stale review messages only after successful delivery
        old = [m for m in [*(earlier or []), (previous or {}).get("telegram_message_id")] if isinstance(m, int)]
        old += self._superseded_review_messages(run_id)
        from .telegram_state import get as state_get
        notice = state_get(self.db, "timeout-notice", run_id)
        if isinstance(notice, int):
            old.append(notice)
            put(self.db, "timeout-notice", run_id, None)
        put(self.db, "review-cover", run_id, vid_mid if Path(thumbnail_path).is_file() else None)
        stale = sorted(set(old) - {vid_mid, card_mid})
        if stale:
            self.delete_messages(stale)

        return vid_mid

    def _is_v2_card(self, run_id: str | None, message_id: Any) -> bool:
        """Whether `message_id` is the video of the run's delivered V2 review pair (a live card sent instead of it,
        under the flag, is not: its taps take the live card's path)."""
        if not run_id or not isinstance(message_id, int) or not hasattr(self.db, "get_telegram_card"):
            return False
        card = self.db.get_telegram_card(f"review:{run_id}") or {}
        return card.get("card_kind") == "review_pair" and (card.get("payload") or {}).get("video_mid") == message_id

    def _v2_blocked(self, run_id: str) -> bool:
        """The release gate blocks this version (its saved report): no Approve button is offered."""
        from .release import blocking_checks
        report = self._release_report(self.db.get_run(run_id) or {})
        return bool(report) and bool(blocking_checks(report, dry_run=report.get("dry_run", False)))

    def _v2_card_status(self, run_id: str, status: str, **extra: Any) -> None:
        card = self.db.get_telegram_card(f"review:{run_id}")
        if card:
            p = {k: v for k, v in (card.get("payload") or {}).items() if k != "reason"}
            p.update(status=status, **extra)
            self.db.put_telegram_card(card["card_id"], card["topic_id"], card["message_id"], card["card_kind"], p,
                                      revision=card["revision"] + 1)

    def close_undo(self, run_id: str) -> bool:
        """The V2 review video loses its Undo button: called when the worker claims the decision (its Undo window
        has ended), so no Undo stays tappable once it can no longer work. Best effort; never raises."""
        if clean_chat():
            try:   # the clean chat: the decided item's Undo (UI-01e), kept for a booked Approve until near its slot
                self.review.claimed(run_id)
                return True
            except Exception as e:  # noqa: BLE001 - a stale Undo is answered "too late"; acting goes on
                log.info("Could not update the Undo of %s: %s", run_id, e)
                return False
        try:
            card = self.db.get_telegram_card(f"review:{run_id}") if hasattr(self.db, "get_telegram_card") else None
            video = ((card or {}).get("payload") or {}).get("video_mid")
            if not isinstance(video, int) or not self.is_configured():
                return False
            self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": video,
                                                      "reply_markup": json.dumps({"inline_keyboard": []})})
            return True
        except Exception as e:  # noqa: BLE001 - a stale button is answered "too late"; acting goes on
            log.info("Could not remove the Undo button of %s: %s", run_id, e)
            return False

    def _undo_refusal(self, run_id: str, decision: str, fresh: bool) -> str:
        """Why an Undo did nothing, in plain words."""
        if undo_minutes() <= 0:
            return "Undo is turned off."
        if not fresh:
            return "Not undone: this copy changed or is not verified. Resend it for review."
        approval = self.db.get_approval(run_id) or {}
        what = "rejection" if decision == "rejected" else "approval"
        if approval.get("decision") != decision:
            return f"Nothing to undo: the review is no longer at this {what}."
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(str(approval.get("decision_timestamp")))).total_seconds()
        except (TypeError, ValueError):
            age = 0.0
        if age > undo_minutes() * 60:
            return f"Too late to undo: {undo_minutes():g} minutes have passed."
        if decision == "rejected":
            return "Too late to undo: the rejection was acted on. Restore the topic from the queue's “Can be restored” list."
        return "Too late to undo: publishing or booking has started. Posting shows what happens next."

    def _v2_review_plan(self, run_id: str, payload: dict[str, Any], release: dict[str, Any] | None,
                        run_dir: Path | None) -> dict[str, Any]:
        """The V2 review card, every value from the run (UI-02): {"blocks", "posted" (publishable.card_record, the
        copy Approve posts, saved once the card is delivered), "blocked"}. Raises V2CardUnavailable, naming the
        value, when the run does not record one the card shows; the caller then sends the live card. The card had
        shown invented values: "9 distinct / 10 beats", "✅ Cleared", "~$0.41", "Gemini 2.5 Pro", "ElevenLabs ·
        Archivist", "Ancient Winds · licensed", "rc70", "made just now", a 52 s default length, a stock caption and
        first comment, and two findings lines that were never checked."""
        from . import publishable
        from .release import blocking_checks
        run = self.db.get_run(run_id) if hasattr(self.db, "get_run") else None
        try:
            sizer = getattr(self, "approval_window", None)
            decider = getattr(self, "approve_posting", None)
            mode = self.db.get_setting("run_mode:" + run_id) if hasattr(self.db, "get_setting") else None
            posting = decider(run_id) if callable(decider) else ("dry_run" if mode == "dry_run" else "now")
            posted = publishable.card_record(payload, self.db, run_dir=run_dir if run_dir and run_dir.is_dir() else None,
                                             window_seconds=sizer() if callable(sizer) else None,
                                             topic_id=(run or {}).get("topic_id") or "", posting=posting,
                                             # the media Approve posts decide the AI label, as on the live card
                                             # (QF-65a; without them the label is on for no media reason)
                                             master=(run or {}).get("master_video_path") or None,
                                             cover=(run or {}).get("thumbnail_cover_path") or None, run_id=run_id)
        except Exception as e:  # noqa: BLE001 - the live card shows the reason; the V2 card is not sent without it
            raise V2CardUnavailable(f"what Approve posts could not be worked out ({type(e).__name__}: {e})") from None
        if not release or not release.get("checks"):
            raise V2CardUnavailable("the run has no release gate report")
        blocking = blocking_checks(release, dry_run=release.get("dry_run", False))
        blocked = bool(blocking)
        blocks = self._build_review_rich_blocks(run_id, payload, release, blocked, posted=posted, run_dir=run_dir)
        return {"blocks": blocks, "posted": posted, "blocked": blocked}

    def _build_review_rich_blocks(self, run_id: str, payload: dict[str, Any], release: dict[str, Any] | None,
                                  blocked: bool, posted: dict[str, Any] | None = None,
                                  run_dir: Path | None = None) -> list[dict[str, Any]]:
        """The V2 rich card's blocks. Every value comes from the run: its payload (script_metadata), its release
        gate report, the copy Approve posts (`posted`), and its voice.json and music_selection.json. A value the
        card needs and the run does not record raises V2CardUnavailable; an optional row (voice, music, who wrote
        it) is left out when its record is missing, never filled in."""
        from .release import blocking_checks
        if not isinstance(posted, dict) or posted.get("error"):
            raise V2CardUnavailable("what Approve posts is not recorded"
                                    + (f" ({posted['error']})" if isinstance(posted, dict) and posted.get("error") else ""))
        if not release or not release.get("checks"):
            raise V2CardUnavailable("the run has no release gate report")
        copy = posted.get("copy") or {}
        title = copy.get("reel.title") or payload.get("title")
        if not title:
            raise V2CardUnavailable("the run records no title")
        measured = re.search(r"Measured ([\d.]+)s", str(next((c.get("detail") for c in release["checks"]
                                                             if c.get("name") == "duration"), "")))
        if not measured:
            raise V2CardUnavailable("the release gate measured no length")
        length = float(measured.group(1))
        narration = str(payload.get("narration_script") or "").strip()
        if not narration:
            raise V2CardUnavailable("the run records no narration")
        key = self.db.run_key(run_id)
        checks = release["checks"]
        open_checks = [c for c in checks if c.get("status") != "pass"]

        def count(n: int, one: str, many: str) -> str:
            return f"{n} {one if n == 1 else many}"

        if blocked:
            n = len(blocking_checks(release, dry_run=release.get("dry_run", False)))
            lede = ["⛔ ", {"type": "bold", "text": "Can't be approved yet"}, f" · {count(n, 'check blocks', 'checks block')} it"]
            gate = "⛔ Blocked"
        elif open_checks:
            lede = ["⚠️ ", {"type": "bold", "text": "Needs review before a live post"},
                    f" · {count(len(open_checks), 'open check', 'open checks')}"]
            gate = "⚠️ Needs review"
        else:
            lede = ["✅ ", {"type": "bold", "text": "Ready to approve"}, f" · {length:.0f} s"]
            gate = "✅ All checks pass"

        blocks: list[dict[str, Any]] = [
            {"type": "heading", "size": 2, "text": f"🎬 {title}"},
            {"type": "paragraph", "text": lede},
        ]
        hook = payload.get("hook_sentence") or payload.get("hook") or ""
        if hook:
            blocks.append({"type": "paragraph", "text": {"type": "italic", "text": f"“{hook}” (opening line)"}})

        def row(label: str, value: str) -> list[dict[str, Any]]:
            return [{"text": {"type": "bold", "text": label}, "align": "left", "valign": "middle"},
                    {"text": value, "align": "right", "valign": "middle"}]

        blocks.append({"type": "table", "is_bordered": True, "is_striped": True, "is_compact": True,
                       "cells": [row("Length", f"{length:.1f} s"), row("Release gate", gate)]})

        illustrated = [g for g in (payload.get("picture_fit") or {}).get("generated") or [] if isinstance(g, dict)]
        if illustrated:
            from .image_gen import review_note
            blocks.append({"type": "paragraph", "text": _plain(review_note(illustrated))})

        # The whole narration when it is a Reel's (it showed the first four sentences under a summary that
        # implied all of it); a long one up to SCRIPT_ROOM, then where the rest is.
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
        shown, used = [], 0
        for s in sentences:
            if shown and used + len(s) > V2_SCRIPT_ROOM:
                break
            shown.append(s)
            used += len(s)
        script = [{"type": "paragraph", "text": s} for s in shown]
        if len(shown) < len(sentences):
            script.append({"type": "paragraph", "text": {"type": "italic", "text": (
                f"…and {count(len(sentences) - len(shown), 'more sentence', 'more sentences')}: Edit text shows the whole script.")}})
        blocks.append({"type": "details", "summary": f"📜 Script · {length:.0f} s, {len(narration.split())} words",
                       "blocks": script})

        # The gate's own findings (as release_lines words them for the live card) and the run's notes
        found = [line[2:].strip() for line in _plain(TelegramApprovalBot.release_lines(release)).splitlines()
                 if line.startswith("• ")]
        found += [_plain(payload[k]) for k in V2_NOTE_KEYS if payload.get(k) and _plain(payload[k])]
        if found:
            blocks.append({"type": "details", "summary": f"🔎 Findings and notes · {len(found)}",
                           "blocks": [{"type": "list", "items": [{"blocks": [{"type": "paragraph", "text": f}]}
                                                                 for f in found]}]})

        # What Approve posts, word for word (QF-43's record, saved when the card is delivered)
        posts: list[dict[str, Any]] = []
        for label, value in (("Caption", copy.get("reel.caption")), ("First comment", copy.get("reel.first_comment")),
                             ("Carousel caption", copy.get("carousel.instagram") if posted.get("carousel_own") else "")):
            if value:
                posts += [{"type": "heading", "size": 3, "text": label}, {"type": "paragraph", "text": value}]
        if not posted.get("documentary"):
            posts.append({"type": "paragraph", "text": _plain(_posted_hashtags_line(posted))})
        posts += [{"type": "paragraph", "text": _plain(item)} for item in _also_posted(posted, copy)]
        when = _plain(_schedule_line(posted))
        if when:
            posts.append({"type": "paragraph", "text": when})
        ai = _plain(_ai_label_line(posted))   # whether the posts carry the AI label, and why (QF-65a), whole
        if ai:
            posts.append({"type": "paragraph", "text": ai})
        if not copy.get("reel.caption") and not posted.get("documentary"):
            raise V2CardUnavailable("the run records no caption")
        blocks.append({"type": "details", "summary": "📤 What Approve posts", "blocks": posts})

        details = []
        if payload.get("origin"):
            details.append(row("Written by", origin_label(str(payload["origin"]))))
        voice = _run_json(run_dir, "voice.json")
        if voice.get("provider"):
            details.append(row("Voice", f"{V2_VOICE_NAMES.get(voice['provider'], voice['provider'])} · "
                                        + ("licensed" if voice.get("licensed") else "not licensed")))
        music = str(_run_json(run_dir, "music_selection.json").get("credit") or "")
        music = re.sub(r"^\W*Music:\s*", "", music).strip()
        if music:
            details.append(row("Music", music))
        if details:
            blocks.append({"type": "details", "summary": "Run details", "blocks": [
                {"type": "table", "is_bordered": True, "is_striped": True, "is_compact": True, "cells": details}]})

        buttons = [{"text": "📋 Copy run id", "copy_text": {"text": run_id}}]
        if payload.get("delivery_profile") != "deep_dive_16x9":   # 9:16 covers don't fit a documentary
            buttons.insert(0, {"text": "🎨 Covers", "style": "primary", "callback_data": f"ccov:{key}"})
        blocks.append({"type": "buttons", "align": "center", "buttons": buttons})
        return blocks

    def refresh_review_cover(self, run_id: str) -> bool:
        """After a cover lock, the review in the chat shows the locked cover and stays the one live card:
        its cover photo is replaced in place (editMessageMedia) and the same card is bound to the new copy.
        A lock re-sent the whole review, a second card with its albums 51 s after the first (prod, 2026-10-29,
        Derinkuyu: cards 1744 and 1760). Returns False when the review cannot be refreshed in place (no
        recorded card or cover photo, the edit failed, the review closed); the caller then resends it,
        which replaces the earlier card."""
        from .telegram_state import get, put, review_fingerprint
        if not self.is_configured():
            return False
        run = self.db.get_run(run_id) or {}
        approval = self.db.get_approval(run_id) or {}
        if run.get("status") != "pending_approval" or approval.get("decision") != "pending":
            return False
        if clean_chat():
            # The review panel (UI-01e): False, so the caller delivers it again, which redraws the panel in place
            # with the locked cover (uploaded once) and binds the copy; nothing new is sent to the chat.
            return False
        messages = [m for m in (get(self.db, "review-messages", run_id) or []) if isinstance(m, int)]
        cover_id = get(self.db, "review-cover", run_id)
        if not messages or not isinstance(cover_id, int) or cover_id not in messages[:-1]:
            return False
        card = messages[-1]
        if approval.get("telegram_message_id") not in (None, card):
            return False  # another card is current: not this one to rebind
        root = Path(run.get("master_video_path") or "").parent
        cover = Path(run.get("thumbnail_cover_path") or root / "thumbnail_cover.jpg")
        if not cover.is_file():
            return False
        payload = run.get("script_payload") or {}
        media = {"type": "photo", "media": "attach://photo_file", "parse_mode": "HTML",
                 "caption": f"Cover · {html.escape(str(payload.get('title', '')))}"}
        try:
            fingerprint = review_fingerprint(self.db, run_id)
            with cover.open("rb") as f:
                self._api("editMessageMedia", files={"photo_file": f},
                          data={"chat_id": self.chat_id, "message_id": cover_id, "media": json.dumps(media)})
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                log.warning("Could not replace the cover on the review of %s: %s", run_id, _MASKER.mask(str(e)))
                return False
        if fingerprint != review_fingerprint(self.db, run_id):
            return False  # changed again while the photo went up: a resend delivers the current copy
        put(self.db, "review-media", run_id, fingerprint)
        put(self.db, "review-card", f"{run_id}:{card}", fingerprint)
        with self.db._conn() as conn:  # the lock cleared the card id (its old copy); the same card is current again
            bound = conn.execute("UPDATE approvals SET telegram_message_id=? WHERE run_id=? AND decision='pending' "
                                 "AND (telegram_message_id IS NULL OR telegram_message_id=?)",
                                 (card, run_id, card)).rowcount
        if not bound:
            return False
        from .card_ledger import record as record_card
        record_card(run_id, "review-cover", f"Cover photo (message {cover_id}) replaced in place; card {card} bound to the new copy.",
                    None, card)
        log.info("Review card %s of %s now shows the locked cover (edited in place).", card, run_id)
        return True

    def _superseded_review_messages(self, run_id: str) -> List[int]:
        """The card and albums of earlier runs of this topic that were sent back to Regenerate: the new
        version's review replaces them (its run is a different one, so a resend's replacement missed them)."""
        from .telegram_state import get, put
        run = self.db.get_run(run_id) if hasattr(self.db, "get_run") else None
        if not run:
            return []
        try:
            with self.db._conn() as conn:
                rows = conn.execute("SELECT r.run_id, a.telegram_message_id FROM runs r LEFT JOIN approvals a ON a.run_id = r.run_id "
                                    "WHERE r.topic_id = ? AND r.status = 'regenerate' AND r.run_id != ?",
                                    (run["topic_id"], run_id)).fetchall()
        except Exception as e:
            log.debug("Could not look up superseded reviews: %s", e)
            return []
        ids: List[int] = []
        for row in rows:
            ids += [m for m in (get(self.db, "review-messages", row["run_id"]) or []) if isinstance(m, int)]
            if isinstance(row["telegram_message_id"], int):
                ids.append(row["telegram_message_id"])
            put(self.db, "review-messages", row["run_id"], [])  # removed once
        return ids

    def _strip_buttons(self, message_id: int) -> None:
        try:
            self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": message_id,
                                                      "reply_markup": json.dumps({"inline_keyboard": []})})
        except Exception as e:
            log.debug("Could not remove buttons from %s: %s", message_id, e)

    def _album(self, items: List[Dict[str, Any]]) -> None:
        """sendMediaGroup (2-10 items, photos and videos mixed); a single item is sent on its own."""
        files, media = {}, []
        try:
            for k, it in enumerate(items):
                name = f"f{k}"
                files[name] = open(it["path"], "rb")
                entry = {"type": it["type"], "media": f"attach://{name}"}
                if it.get("caption"):
                    entry.update(caption=it["caption"][:1024], parse_mode="HTML")
                if it["type"] == "video":
                    entry["supports_streaming"] = True
                    entry.update(_video_dimensions(it["path"]))  # without them Telegram can show 16:9 as a square
                media.append(entry)
            # Not protected (operator, 2026-10-29): the review video can be saved or forwarded to check it elsewhere.
            rev_tid = config.TELEGRAM_TOPICS.get("reviews") if (config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS")) else None
            if len(media) == 1:
                kind = media[0]["type"]
                send_data = {"chat_id": self.chat_id, "caption": media[0].get("caption", ""), "parse_mode": "HTML"}
                if rev_tid:
                    send_data["message_thread_id"] = rev_tid
                sent = self._api("send" + kind.capitalize(), timeout=300, files={kind: files["f0"]},
                                 data=send_data)
            else:
                send_data = {"chat_id": self.chat_id, "media": json.dumps(media)}
                if rev_tid:
                    send_data["message_thread_id"] = rev_tid
                sent = self._api("sendMediaGroup", timeout=300, files=files,
                                 data=send_data)
        finally:
            for f in files.values():
                f.close()
        messages = sent if isinstance(sent, list) else [sent]
        return [m["message_id"] for m in messages if isinstance(m, dict) and isinstance(m.get("message_id"), int)]

    def _send_review_media(self, video_path: Path, thumbnail_path: Path, payload: Dict[str, Any]) -> List[int]:
        """Sends the review's albums; returns their message ids (so a later delivery can remove them)."""
        if not video_path.is_file() or not 0 < video_path.stat().st_size <= TELEGRAM_VIDEO_LIMIT_MB * 1024 * 1024:
            raise ValueError("A deliverable review video is required")
        sent: List[int] = []
        run_dir = video_path.parent
        e = html.escape
        self.send_chat_action("upload_video")  # Telegram's own "sending a video…" indicator while the album goes
        first: List[Dict[str, Any]] = []
        if thumbnail_path.is_file():
            first.append({"type": "photo", "path": thumbnail_path, "caption": f"Cover · {e(payload.get('title', ''))}"})
        strip = scene_strip(video_path, run_dir / "review_scene_strip.jpg")
        if strip:
            first.append({"type": "photo", "path": strip, "caption": "One frame every few seconds, in order"})
        size_mb = os.path.getsize(video_path) / (1024 * 1024) if video_path.is_file() else 0
        if 0 < size_mb <= TELEGRAM_VIDEO_LIMIT_MB:
            first.append({"type": "video", "path": video_path, "caption": f"Review copy, 720p ({size_mb:.1f} MB)"})
        elif size_mb:
            log.warning("Video is %.1f MB, over Telegram's bot upload limit; review it locally: %s", size_mb, video_path)
        if first:
            sent += self._album(first) or []
        # Every illustrated beat beside its reference(s) and its narrated line (reliable depiction, 2026-11-02): the
        # operator approves each illustration on the review itself.
        for batch in illustration_review_items(payload, run_dir):
            sent += self._album(batch) or []
        from .multi_format import RETIRED_STORY_FRAMES
        story = [p for p in sorted((run_dir / "stories").glob("story_*.jpg"))
                 if p.name not in RETIRED_STORY_FRAMES]  # an older run's poll frame is not posted (QF-80)
        stills = story + sorted((run_dir / "carousel").glob("carousel_slide_*.jpg"))
        n_story = len(story)
        from .carousel_lazy import planned
        for start in range(0, len(stills), 10):
            batch = stills[start:start + 10]
            items = [{"type": "photo", "path": p} for p in batch]
            if start == 0 and len(stills) == n_story and planned(run_dir):   # QF-81: drawn at its slot
                items[0]["caption"] = (f"Also posted on approval: {n_story} Story frame(s); the carousel is drawn "
                                       "when it takes its carousel slot (🖼 Preview carousel on the card shows it now)")
            elif start == 0:
                items[0]["caption"] = (f"Also posted on approval: {n_story} Story frame(s), then the "
                                       f"{len(stills) - n_story}-slide carousel")
            sent += self._album(items) or []
        return sent

    def send_review_card(self, run_id: str, script_payload: Dict[str, Any], release: Optional[Dict[str, Any]] = None) -> Optional[int]:
        from .telegram_state import get, review_fingerprint, put
        fingerprint = review_fingerprint(self.db, run_id)
        if get(self.db, "review-media", run_id) != fingerprint:
            raise ValueError("Review media must be delivered for this exact copy before approval")
        key = self.db.run_key(run_id)  # short: Telegram limits button data to 64 bytes
        regen = self.db.get_setting("regenerating:" + run_id) if hasattr(self.db, "get_setting") else None
        # What Approve posts, word for word per platform, with the slot it books and when the review closes; kept
        # as the card's record, which the envelope must match (publisher.build_envelope; QF-43).
        from . import publishable
        from .config import OUTPUT_DIR
        run = self.db.get_run(run_id) if hasattr(self.db, "get_run") else None
        run_dir = Path((run or {}).get("master_video_path") or Path(OUTPUT_DIR) / str(run_id) / "x").parent
        try:
            sizer = getattr(self, "approval_window", None)
            decider = getattr(self, "approve_posting", None)   # the coordinator's; a bot alone posts at once
            mode = self.db.get_setting("run_mode:" + run_id) if hasattr(self.db, "get_setting") else None
            posting = decider(run_id) if callable(decider) else ("dry_run" if mode == "dry_run" else "now")
            posted = publishable.card_record(script_payload, self.db, run_dir=run_dir if run_dir.is_dir() else None,
                                             window_seconds=sizer() if callable(sizer) else None,
                                             topic_id=(run or {}).get("topic_id") or "", posting=posting,
                                             master=(run or {}).get("master_video_path") or None,
                                             cover=(run or {}).get("thumbnail_cover_path") or None, run_id=run_id)
        except Exception as e:  # noqa: BLE001  shown on the card; with no copy recorded, the envelope check refuses to post
            log.warning("Could not work out what Approve posts for %s: %s", run_id, e)
            posted = {"error": f"{type(e).__name__}: {e}"}
        script_payload = {**script_payload, "_publish_copy": posted}
        card_text = self.format_card_text({**script_payload, "_regeneration_note": regen} if regen else script_payload,
                                          run_id, release=release)
        # A version the release gate blocks is not offered for approval: Approve could only end in
        # "Release blocked" (2026-10-24). Edit, Regenerate and Reject stay; a corrected version gets a new card.
        from .release import blocking_checks
        blocked = bool(release) and bool(blocking_checks(release, dry_run=release.get("dry_run", False)))
        markup = {"inline_keyboard": format_responsive_keyboard([
            ([] if blocked else [{"text": "🚀 Approve & Post", "callback_data": f"approve:{key}"}])
            + [{"text": "✏️ Edit text", "callback_data": f"edit:{key}"}],
            [{"text": "♻️ Regenerate", "callback_data": f"regen:{key}"},
             {"text": "❌ Reject", "callback_data": f"reject:{key}"}],
        ] + ([] if script_payload.get("delivery_profile") == "deep_dive_16x9"  # 9:16 covers don't fit a documentary
             else [[{"text": "🎨 Covers", "callback_data": f"ccov:{key}"}]])
          + ([[{"text": "🖼 Preview carousel", "callback_data": f"ccar:{key}"}]] if posted.get("carousel_at_slot") else [])
          + [[DONE_BUTTON]])}
        # One message, never paged: a paged card moved into a panel whose buttons failed (2026-10-25);
        # format_card_text keeps it inside the limit by shortening its folded lists.
        rev_tid = config.TELEGRAM_TOPICS.get("reviews") if (config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS")) else None
        try:
            send_card_data = {"chat_id": self.chat_id, "text": card_text, "parse_mode": "HTML",
                              "disable_web_page_preview": "true", "reply_markup": json.dumps(markup)}
            if rev_tid:
                send_card_data["message_thread_id"] = rev_tid
            msg = self._api("sendMessage", data=send_card_data)
        except Exception as e:
            if "can't parse entities" not in str(e).lower() and "bad request" not in str(e).lower():
                raise
            log.warning("Telegram parse error on approval card (%s); retrying plain text", e)
            send_card_data = {"chat_id": self.chat_id, "text": re.sub(r"<[^>]+>", "", card_text)[:4000],
                              "disable_web_page_preview": "true", "reply_markup": json.dumps(markup)}
            if rev_tid:
                send_card_data["message_thread_id"] = rev_tid
            msg = self._api("sendMessage", data=send_card_data)
        message_id = msg["message_id"]
        # The lists the card had to shorten, whole, in the reply under it; and what else Approve posts, and the slot,
        # when the card had no room for them (QF-43: _with_publish_lines).
        if CARD_LIST_MORE in card_text or needs_publish_reply(card_text, posted):
            try:
                if CARD_LIST_MORE in card_text:
                    full = self._card_text({**script_payload, "_regeneration_note": regen} if regen else script_payload,
                                           run_id, release, None, 10 ** 6, extras=True)
                else:
                    also = _also_posted(posted, posted.get("copy") or {})
                    full = "\n\n" + listed(ALSO_HEAD, also) + "\n" if also else ""
                slot = _schedule_line(posted) if isinstance(posted, dict) else ""
                slot = "" if slot in card_text else slot
                # The whole AI-label line when the card had room only for its brief form (QF-65a); the full
                # card text (CARD_LIST_MORE) already carries it.
                ai = _ai_label_line(posted)
                ai = "" if (ai in card_text or CARD_LIST_MORE in card_text) else ai
                slot = "\n".join(x for x in (slot, ai) if x)
                for n, text in enumerate(detail_messages(full) or ([""] if slot else []), 1):
                    detail_data = {"chat_id": self.chat_id, "parse_mode": "HTML",
                                   "text": ("📋 <b>In full</b>\n\n" + slot + ("\n" if slot and text else "")
                                            if n == 1 else "") + text,
                                   "reply_to_message_id": message_id,
                                   "disable_web_page_preview": "true"}
                    if rev_tid:
                        detail_data["message_thread_id"] = rev_tid
                    sent = self._api("sendMessage", data=detail_data)
                    put(self.db, "message-run", f"{self.chat_id}:{sent.get('message_id')}", run_id)
            except Exception as e:  # the card stands without it
                log.warning("Could not send the review card's lists in full: %s", e)
        from .card_ledger import record as record_card
        record_card(run_id, "review", card_text, markup["inline_keyboard"], message_id)
        publishable.save_record(self.db, run_id, posted)   # before the card's buttons can decide
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), message_id)
        put(self.db, "message-run", f"{self.chat_id}:{message_id}", run_id)
        put(self.db, "review-card", f"{run_id}:{message_id}", fingerprint)
        log.info("Approval request sent to Telegram (message %s).", message_id)
        return message_id

    def _authorized(self, user_id: Any, chat_id: Any) -> bool:
        return str(user_id) in self.approver_ids and str(chat_id) == str(self.chat_id)

    def _answer_callback(self, cq_id: str, text: str = "") -> None:
        """Safely answers a Telegram callback query; suppresses expired query (400) errors."""
        # Telegram shows only the first answer and rejects the rest; each rejected call still costs a round trip.
        acting = _ITEM_ACTION.get()
        if acting is not None and acting["cq"] == cq_id:   # an item's action: one toast once its outcome is known
            acting["toasts"].append(str(text or ""))
            return
        with self._answered_lock:
            if cq_id in self._answered:
                return
            self._answered[cq_id] = None
            while len(self._answered) > 512:
                self._answered.pop(next(iter(self._answered)))
        try:
            self._api("answerCallbackQuery", timeout=5, data={"callback_query_id": cq_id, "text": text})
        except Exception as e:
            log.debug("answerCallbackQuery suppressed (likely expired query): %s", e)

    def _handle_callback(self, cq: Dict[str, Any], run_id: Optional[str] = None) -> Optional[str]:
        from .telegram_state import decode
        from .publication_lock import publication_lock
        action, _, target = decode(self.db, cq.get("data", "")).partition(":")
        from .review_panel import REVIEW_ACTIONS
        if clean_chat() and action in REVIEW_ACTIONS:
            msg = cq.get("message") or {}
            if not self._authorized(str(cq.get("from", {}).get("id", "")), msg.get("chat", {}).get("id", "")):
                self._answer_callback(cq["id"], "Not authorized.")
                return None
            decided = self.review.tap({**cq, "data": decode(self.db, cq.get("data", ""))})
            ref = target.partition(":")[0]
            return decided if decided and (run_id is None or self.db.resolve_run(ref) == run_id) else None
        run_target = target.partition(":")[0] if action == "rejres" else target
        resolved = self.db.resolve_run(run_target)
        try:
            with publication_lock(self.db.db_path, resolved or "unknown-review"):
                return self._handle_callback_locked(cq, run_id)
        except ValueError:
            self._answer_callback(cq["id"], "Review is busy or incomplete. Refresh before deciding.")
            return None

    def _handle_callback_locked(self, cq: Dict[str, Any], run_id: Optional[str] = None) -> Optional[str]:
        """Applies an approval button press. With run_id, ignores presses for other runs."""
        from .telegram_state import decode, get, put, review_fingerprint
        action, _, target = decode(self.db, cq.get("data", "")).partition(":")
        run_target = target.partition(":")[0] if action == "rejres" else target
        if action not in ACTIONS or (run_id is not None and self.db.resolve_run(run_target) != run_id):
            return None
        run_id = self.db.resolve_run(run_target)
        user_id = str(cq.get("from", {}).get("id", ""))
        msg = cq.get("message") or {}
        msg_id = msg.get("message_id")
        if not self._authorized(user_id, msg.get("chat", {}).get("id", "")):
            log.warning("Ignoring approval click from unauthorized user %s.", user_id)
            self._answer_callback(cq["id"], "Not authorized.")
            return None

        # --- Telegram V2 Review Pair Interactive Flow ---
        # Only on a V2 card, and only while the flag is on: with it off, "← Keep it" was recorded as the decision
        # "keep" and failed the run and its topic; an Undo reset any decision (UI-02).
        v2_card = config.TELEGRAM_CARDS_V2 and self._is_v2_card(run_id, msg_id)
        if action in V2_ONLY_ACTIONS - {"rejres"} and not v2_card:
            self._answer_callback(cq["id"], "This button belongs to a review layout that is switched off. Nothing changed.")
            return None

        if v2_card and action == "reject":
            self._answer_callback(cq["id"], "Pick a reason.")
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "reply_markup": json.dumps(_v2_reason_keyboard(target)),
            })
            return "reject_prompt"

        if v2_card and action == "keepit":
            self._answer_callback(cq["id"], "Kept.")
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "reply_markup": json.dumps(_v2_review_keyboard(target, self._v2_blocked(run_id))),
            })
            return "kept"

        if v2_card and action == "rejres":
            from . import improve
            target_key, _, reason = target.partition(":")
            if reason not in improve.REASONS:   # a chip of the retired 7-reason set: not a decision
                self._answer_callback(cq["id"], "That reason is no longer offered. Tap Reject again.")
                self._api("editMessageReplyMarkup", data={
                    "chat_id": self.chat_id, "message_id": msg_id,
                    "reply_markup": json.dumps(_v2_review_keyboard(target_key, self._v2_blocked(run_id))),
                })
                return None
            expected = get(self.db, "review-card", f"{run_id}:{msg_id}")
            if not expected or expected != review_fingerprint(self.db, run_id):
                self._answer_callback(cq["id"], "This copy is not verified or changed. Resend it for review.")
                return None
            applied = self.db.decide_telegram_review(run_id, msg_id, "rejected", f"telegram:{user_id}")
            if not applied:
                existing = (self.db.get_approval(run_id) or {}).get("decision", "unknown")
                self._answer_callback(cq["id"], f"⚠️ Expired/Closed ({existing})")
                return existing
            label = improve.REASONS[reason]
            improve.record_reason(self.db, run_id, reason, "rejected")   # the ledger the live card's chips feed
            self._v2_card_status(run_id, "rejected", reason=reason)
            sender = cq.get("from") or {}
            name = next((n for n in (sender.get("first_name"), sender.get("username")) if n and re.search(r"\w", n)), "you")
            until = undo_until(self.db.get_approval(run_id) or {}) if undo_minutes() > 0 else None
            # The Undo works for its whole window: the worker sets the topic aside only once it has passed (UI-02)
            undo_kb = {"inline_keyboard": [[{"text": "↩️ Undo", "style": "primary", "callback_data": f"undorej:{target_key}"}]]
                       if until else []}
            when = f" Undo until {show_time(until, '%H:%M %Z')}; then the" if until else " The"
            self._api("editMessageCaption", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "caption": (f"❌ Rejected by {name} · reason: {label}. Nothing was posted.{when} topic is set aside "
                            "(restore it from the queue's “Can be restored” list)."),
                "reply_markup": json.dumps(undo_kb),
            })
            notice_id = get(self.db, "review-need-notice", run_id)
            if notice_id:
                self.delete_message(notice_id)
                put(self.db, "review-need-notice", run_id, None)
            self._answer_callback(cq["id"], f"Rejected ({label}).")
            return "rejected"

        if v2_card and action in ("undorej", "undoappr"):
            decision = "rejected" if action == "undorej" else "approved"
            expected = get(self.db, "review-card", f"{run_id}:{msg_id}")
            fresh = bool(expected) and expected == review_fingerprint(self.db, run_id)
            # Reopened only while the decision is untouched (db.undo_review_decision): it reset any decision,
            # days later, on a published or regenerated run, and redrew Approve buttons that could not decide.
            sizer = getattr(self, "approval_window", None)     # what it gives back: at most the review window
            try:
                window = float(sizer()) if callable(sizer) else float(APPROVAL_TIMEOUT_SECONDS)
            except Exception as e:  # noqa: BLE001 - sized as a review without the coordinator's slot rules
                log.warning("Review window not sized for the Undo (%s); the configured timeout caps it.", e)
                window = float(APPROVAL_TIMEOUT_SECONDS)
            if not fresh or not self.db.undo_review_decision(run_id, decision, msg_id, undo_minutes() * 60, window):
                self._answer_callback(cq["id"], self._undo_refusal(run_id, decision, fresh))
                self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": msg_id,
                                                          "reply_markup": json.dumps({"inline_keyboard": []})})
                return None
            self._v2_card_status(run_id, "waiting")
            run = self.db.get_run(run_id) or {}
            title = run.get("script_payload", {}).get("title", self.db.run_label(run_id))
            self._api("editMessageCaption", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "caption": f"🎬 {title}",
                "reply_markup": json.dumps(_v2_review_keyboard(target, self._v2_blocked(run_id))),
            })
            closes = (self.db.get_approval(run_id) or {}).get("expires_at")
            self._answer_callback(cq["id"], ("Rejection" if decision == "rejected" else "Approval") + " undone. The review is open"
                                  + (f" until {show_time(float(closes), '%H:%M %Z')}." if closes else " again."))
            return "undone"

        if not self.db.get_approval(run_id):
            self._answer_callback(cq["id"], "Unknown run.")
            return None

        from .telegram_state import get, review_fingerprint
        expected = get(self.db, "review-card", f"{run_id}:{msg.get('message_id')}")
        if not expected or expected != review_fingerprint(self.db, run_id):
            self._answer_callback(cq["id"], "This copy is not verified or changed. Resend it for review.")
            return None
        decision = ACTIONS[action]
        applied = self.db.decide_telegram_review(run_id, msg.get("message_id"), decision, f"telegram:{user_id}")
        if not applied:
            existing = (self.db.get_approval(run_id) or {}).get("decision", "unknown")
            self._answer_callback(cq["id"], f"⚠️ Expired/Closed ({existing})")
            self._queue_navigation(lambda: self._strip_buttons(msg.get("message_id")))
            return existing

        if v2_card:
            self._v2_card_status(run_id, decision)

        self._answer_callback(cq["id"], f"Decision: {decision.upper()}")
        self._queue_navigation(lambda: self._render_decision(cq, run_id, decision, user_id))
        return decision

    def _render_decision(self, cq, run_id, decision, user_id):
        msg = cq.get("message") or {}
        msg_id = msg.get("message_id")
        orig_text = msg.get("text") or ""
        sender = cq.get("from") or {}
        # A name, not an ID; a placeholder first name such as "-" is no name.
        name = next((n for n in (sender.get("first_name"), sender.get("username")) if n and re.search(r"\w", n)), "you")
        who = html.escape(name)
        if decision == "approved" and config.TELEGRAM_CARDS_V2 and self._is_v2_card(run_id, msg_id):
            until = undo_until(self.db.get_approval(run_id) or {}) if undo_minutes() > 0 else None
            undo_kb = {"inline_keyboard": [[{"text": "↩️ Undo approval", "style": "primary",
                                              "callback_data": f"undoappr:{self.db.run_key(run_id)}"}]] if until else []}
            # What happens, and when: the worker acts only once the Undo window has passed (UI-02). Plain text.
            from . import publishable
            posting = (publishable.load_record(self.db, run_id) or {}).get("posting")
            then = show_time(until, "%H:%M %Z") if until else ""   # with its zone, as the slot line and reminder
            stamp_caption = (f"✅ Approved by {name} · " + (
                "release check and publishing under way" if not until else
                f"books its slot after {then} unless you undo" if posting == "slot" else
                f"dry run after {then} unless you undo; nothing is posted" if posting == "dry_run" else
                f"posts after {then} unless you undo"))
            self._api("editMessageCaption", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "caption": stamp_caption,
                "reply_markup": json.dumps(undo_kb),
            })
            from .telegram_state import get as state_get, put as state_put
            notice_id = state_get(self.db, "review-need-notice", run_id)
            if notice_id:
                self.delete_message(notice_id)
                state_put(self.db, "review-need-notice", run_id, None)
            return
        if decision in ("rejected", "regenerate") and msg_id:
            # One card for the whole decision (operator, 2026-10-23): the review becomes a short receipt
            # with the reason chips on it, instead of the full card plus a separate "Why?" message.
            if self._reason_card(msg_id, run_id, decision, who):
                # Its albums are not needed once decided; the card stays for the reason and any reply.
                from .telegram_state import get, put
                albums = [m for m in (get(self.db, "review-messages", run_id) or []) if isinstance(m, int) and m != msg_id]
                if albums:
                    self.delete_messages(albums)
                put(self.db, "review-messages", run_id, [msg_id])
                if decision == "rejected" and hasattr(self.db, "record_alert"):
                    # Its clock starts now, reason or not: a rejected Nazca card with no chip tapped stayed in
                    # the chat for good (operator, 2026-10-28). A reason or a reply settles it sooner.
                    self.db.record_alert("reason card", message_id=msg_id, kind="transient", expires_at=(
                        datetime.now(timezone.utc) + timedelta(minutes=REASON_CARD_MINUTES)).isoformat())
                return
        badge = (
            # Sentence case, as everywhere else (docs/telegram-style.md); the receipt replaces it once the
            # release check and publishing have a result.
            f"\n\n✅ <b>Approved</b> by {who} · release check and publishing under way" if decision == "approved"
            else f"\n\n🔄 <b>Regeneration requested</b> by {who}" if decision == "regenerate"
            else f"\n\n❌ <b>Rejected</b> by {who}" if decision == "rejected"
            else f"\n\n✖ <b>Cancelled</b> by {who}"
        )
        # The card is rebuilt from the run's payload (the message text Telegram returns has lost its
        # formatting, and re-sending it as HTML could break on a stray "<").
        run = self.db.get_run(run_id) if hasattr(self.db, "get_run") else None
        if run and run.get("script_payload"):
            # With the gate's report, as on the card that was tapped (it said "Release gate: not run yet")
            from .publishable import load_record
            shown = load_record(self.db, run_id)   # what the tapped card showed would be posted
            updated_text = self.format_card_text({**run["script_payload"], **({"_publish_copy": shown} if shown else {})},
                                                 run_id, release=self._release_report(run),
                                                 reserve=_tg_len(badge)) + badge   # the badge fits: never paged
        elif orig_text:
            updated_text = html.escape(orig_text) + badge
        else:
            updated_text = f"Decision: <b>{html.escape(decision.upper())}</b> for {html.escape(self.db.run_label(run_id))}"
        from .card_ledger import record as record_card
        record_card(run_id, "decision", updated_text, None, msg_id)
        try:
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })
            if msg_id:
                self.edit_message_text(msg_id, updated_text)
            else:
                self.notify(updated_text, group="reviews", record_alert=False)
        except Exception as e:
            log.warning("Could not update Telegram card: %s", e)
        if decision in ("rejected", "regenerate"):
            self.ask_reason(run_id, decision)

    @staticmethod
    def _release_report(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The run's release report (next to its master), or None."""
        try:
            path = Path(run.get("master_video_path") or "").parent / "release_report.json"
            return json.loads(path.read_text(encoding="utf-8")) if run.get("master_video_path") and path.exists() else None
        except (OSError, ValueError):
            return None

    def stamp_review_outcome(self, run_id: str, outcome: str) -> bool:
        """After the decision and its result, the review card becomes a short receipt (what was approved and
        what happened), and the panel comes to the bottom: the card said "delivery not yet confirmed" long
        after a dry run had finished. The full review stays on record in the run folder and the database."""
        # One panel (operator, 2026-10-23): the decided review's card and its albums (cover, video, Stories,
        # carousel) leave the chat, and the receipt becomes the dashboard's "Last review" line. The receipt
        # sat above the dashboard as a second panel with the review's media still under it.
        appr = self.db.get_approval(run_id) if hasattr(self.db, "get_approval") else None
        mid = (appr or {}).get("telegram_message_id")
        if not self.is_configured():
            return False
        if clean_chat():
            # The decided review left the panel already (UI-01e); its item says what happened: a booking adds the
            # slot to "✅ Approved", a publication is the receipt's "📮 Posted" (the same item, in place).
            from .card_ledger import record as record_card
            record_card(run_id, "receipt", outcome)
            if outcome.startswith("🗓"):
                self.review.booked(run_id)
            return True
        from .control import LAST_REVIEW_KEY
        from .telegram_state import get, put
        receipt = (f"<b>{html.escape(self.db.run_label(run_id))}</b>: {outcome}\n"
                   f"<i>Run ID:</i> <code>{html.escape(run_id)}</code>")
        from .card_ledger import record as record_card
        record_card(run_id, "receipt", receipt)
        self.db.set_setting(LAST_REVIEW_KEY, json.dumps({"text": receipt, "at": time.time(), "run_id": run_id}))
        review = [m for m in (get(self.db, "review-messages", run_id) or []) if isinstance(m, int)]
        stale = sorted(set(review + ([mid] if isinstance(mid, int) else [])))
        try:
            shown = type(self.show_dashboard(fresh=True)) is int   # the panel first; then the review goes
        except Exception as e:
            log.debug("Could not bring the panel down after the receipt: %s", e)
            shown = False
        if shown and stale:
            self.delete_messages(stale)
            put(self.db, "review-messages", run_id, [])
        elif not shown and isinstance(mid, int):  # no panel: the card itself keeps the receipt
            self.edit_message_text(mid, f"🎬 {receipt}", buttons=[[DONE_BUTTON]])
        return True

    def _reason_card(self, message_id: int, run_id: str, decision: str, who: str) -> bool:
        """The decided review card, as a receipt that asks why (the reason chips edit this same card)."""
        from .improve import REASONS
        key = self.db.run_key(run_id)
        when = show_time(time.time(), "%H:%M")
        if decision == "regenerate":
            head = (f"🔄 <b>Regeneration requested</b> by {who} at {when}. A new version is being made; its "
                    "progress card follows, and its review replaces this card. Pick what was wrong so it can act on it.")
        else:
            # Said as it is: a rejected topic is set aside, not queued (it said "goes back to the queue").
            head = (f"❌ <b>Rejected</b> by {who} at {when}. Nothing was posted; the topic is set aside "
                    "(restore it from the queue's \"Can be restored\" list).")
        verb = "regenerate" if decision == "regenerate" else "reject"
        text = (f"🎬 <b>{html.escape(self.db.run_label(run_id))}</b>\n\n{head}\n\n"
                f"📝 <b>Why {verb}?</b> One tap helps the next version (and the system) improve; then you can "
                f"reply to this card with details. [run {html.escape(key)}]")
        buttons = [{"text": label, "callback_data": f"rsn:{key}:{code}"} for code, label in REASONS.items()] + [DONE_BUTTON]
        return bool(self.edit_message_text(message_id, text, buttons=buttons))

    def ask_reason(self, run_id: str, decision: str) -> None:
        """One tap tells the system what was wrong; it goes to the improvement ledger and, for a
        regenerate, into the new run (script revised, or pictures re-planned)."""
        from .improve import REASONS
        key = self.db.run_key(run_id)
        buttons = [{"text": label, "callback_data": f"rsn:{key}:{code}"} for code, label in REASONS.items()]
        verb = "regenerate" if decision == "regenerate" else "reject"
        self.notify(f"📝 <b>Why {verb}?</b> One tap helps the next version (and the system) improve. "
                    f"After tapping, you can reply to this message with details. [run {html.escape(key)}]",
                    buttons=buttons, record_alert=False, group="reviews")

    def show_regeneration_plan(self, run_id: str, fb: Dict[str, Any], plan: str) -> bool:
        """The decided review card, once the new version has settled what it carries over: what it keeps and
        redoes, said before it starts (2026-10-26: the card said "made fresh" while the script was kept word
        for word). It stays a reason card ("✅ Reason:" and the run tag), so a reply still adds details."""
        from .control import clip_words
        from .improve import REASONS
        mid = (self.db.get_approval(run_id) or {}).get("telegram_message_id")
        if not self.is_configured() or not isinstance(mid, int):
            return False
        if clean_chat():
            return False   # the review panel is not this run's card any more (UI-01e)
        reason = fb.get("reason")
        said = (f"✅ Reason: <b>{html.escape(REASONS.get(reason, reason))}</b>"
                + (f": <i>{html.escape(clip_words(str(fb['text']), 200))}</i>" if fb.get("text") else "") + "."
                if reason else "✅ Reason: <b>none given</b>.")
        text = (f"🎬 <b>{html.escape(self.db.run_label(run_id))}</b>\n\n🔄 <b>New version starting.</b> {plan}\n\n"
                f"{said} Reply to this card to add details; steps not yet run use them. "
                f"Its review replaces this card. [run {html.escape(self.db.run_key(run_id))}]")
        return bool(self.edit_message_text(mid, text, buttons=[[DONE_BUTTON]]))

    # ------------------------------------------------------------ notifications
    NOTICE_TTL_MINUTES = 360  # an informational notice leaves the chat after this; its record stays in the database

    def notify(self, text: str, buttons: Optional[list] = None, transient: Optional[bool] = None, ttl_minutes: Optional[int] = None,
               record_alert: bool = True, silent: bool = False, group: Optional[str] = None,
               section: str | None = None, key: str | None = None, subject: str | None = None,
               run: str | None = None, replace: bool = False, actions: list | None = None) -> bool:
        """Sends an operational message (HTML), optionally with inline buttons (single or multi-row).
        Never raises: alerts must not break a run. `actions` are the clean chat's item-page buttons for a notice
        that had none of its own (design §10.2); the earlier chat ignores them, as it did before.
        In Telegram V2 (TELEGRAM_CARDS_V2), routes by topic (group):
          studio (4906), needs (4907), reviews (4908), runs (4909), info (4910).
        Only group='needs' notifies audibly; all other groups are strictly silent."""
        if config.TELEGRAM_CARDS_V2:
            if not group and config.RELIC_STRICT_TOPICS:
                raise ValueError("TELEGRAM_CARDS_V2 requires explicit 'group' parameter on all notify() calls.")
            if group != "needs":
                silent = True  # strictly: "only Needs you rings"
        if transient is None:
            transient = not buttons
        if ttl_minutes is None:
            ttl_minutes = self.NOTICE_TTL_MINUTES if transient else 0
        panel = _PANEL.get()
        if panel and panel[0] is self:
            if len(panel) > 4 and self._panel_revisions.get(self._current_panel(panel[1]), 0) != panel[4]:
                panel[3] = True
                return False
            panel[3] = True
            # Every screen a command draws keeps a way back to the dashboard (a command's own button
            # list, such as /bump's topics, often had none).
            return self.show_navigation_panel(panel[1], text, is_media=panel[2], buttons=navigation_buttons(buttons))
        if config.TELEGRAM_CLEAN_CHAT and not (group == "studio" and record_alert is False):
            # The clean chat (UI-01): a sender's notice is an item of the pinned notifications panel, never a
            # message of its own. Only the screens a command draws (studio, unrecorded) still send here.
            return self._post_notice(text, buttons, group, section, key, subject, run, replace, record_alert,
                                     actions)
        if not self.is_configured():
            log.info("Notification (Telegram not configured): %s", text)
            if record_alert and hasattr(self.db, "record_alert"):
                self.db.record_alert(text, message_id=None, kind="transient" if transient else "persistent")
            return False
        text = _MASKER.mask(text)  # error details can carry a credential; the chat never sees one
        # No message is a dead end: one tap back to the panel, whether or not it carries buttons of its own
        # (a timeout notice or a proposal card had only its actions: 2026-10-25 overhaul).
        buttons = navigation_buttons(buttons) if buttons else [DONE_BUTTON]
        offered = buttons   # the notice's own buttons, before a long text's page arrows
        from .telegram_pages import panel as paginate
        text, buttons = paginate(self.db, text, buttons)
        truncated_text = text
        data = {"chat_id": self.chat_id, "text": truncated_text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if config.TELEGRAM_CARDS_V2:
            tid = config.TELEGRAM_TOPICS.get(group or "studio", config.TELEGRAM_TOPICS["studio"])
            data["message_thread_id"] = tid
        if silent:
            data["disable_notification"] = "true"
        if buttons:
            markup = {"inline_keyboard": format_responsive_keyboard(buttons)}
            data["reply_markup"] = json.dumps(markup)
        try:
            res = self._api("sendMessage", data=data)
            msg_id = res.get("message_id") if isinstance(res, dict) else None
            expires_at = None
            if transient and ttl_minutes > 0:
                expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
            # A transient message is always recorded: its expiry lives in that record (without it, it stayed).
            if (record_alert or transient) and hasattr(self.db, "record_alert"):
                self.db.record_alert(text, message_id=msg_id, kind="transient" if transient else "persistent", expires_at=expires_at)
                self._remember_actions(msg_id, offered)   # the clean chat's first start keeps them (§13)
            return msg_id if msg_id else True
        except Exception as e:
            err_msg = str(e).lower()
            if "message is not modified" in err_msg:
                return True
            if "can't parse entities" in err_msg:
                log.warning("Telegram parse error in notify (%s); falling back to plain text", e)
                try:
                    fallback_data = dict(data)
                    fallback_data.pop("parse_mode", None)
                    fallback_data["text"] = re.sub(r"<[^>]+>", "", text)[:4000]
                    res = self._api("sendMessage", data=fallback_data)
                    msg_id = res.get("message_id") if isinstance(res, dict) else None
                    if record_alert and hasattr(self.db, "record_alert"):
                        self.db.record_alert(text, message_id=msg_id, kind="transient" if transient else "persistent")
                    return msg_id if msg_id else True
                except Exception as e2:
                    log.warning("Fallback plain text notify also failed: %s", e2)
            log.warning("Could not send Telegram notification: %s", e)
            return False

    def _post_notice(self, text: str, buttons: list | None, group: str | None, section: str | None,
                     key: str | None, subject: str | None, run: str | None, replace: bool,
                     record_alert: bool, extra: list | None = None) -> bool:
        """A sender's message as a notifications-panel item (design §8): Needs you for group "needs" (or a
        notice with actions to take), info for the run log and info groups. Its buttons become the item page's
        actions, its links lines of the body. Returns True, never a message id: nothing was sent."""
        from . import notifications as notes
        text = _MASKER.mask(text)
        emoji, title, parsed_subject, body = notes.parse(text)
        subject = subject or parsed_subject
        links = notes.links_from(buttons)
        if links:
            body = (body + "\n" if body else "") + " · ".join(links)
        actions = notes.actions_from(buttons)
        actions += [a for a in notes.actions_from([list(extra or [])]) if a not in actions]
        if section is None:
            section = (notes.NEEDS if group == "needs" else notes.INFO if group in ("runs", "info", "reviews")
                       else notes.NEEDS if actions else notes.INFO)
        try:
            notes.post(self.db, section, key or "", title, body, emoji=emoji, subject=subject or "",
                       actions=actions, run=run, replace=replace)
        except Exception as e:  # noqa: BLE001 - alerts must not break a run
            log.warning("Could not record the notice %r: %s", title, e)
            return False
        if record_alert and hasattr(self.db, "record_alert"):
            self.db.record_alert(text, message_id=None, kind="persistent")   # the alert history, as before
        self.layout.kick()
        return True

    @property
    def review(self):
        """The clean chat's one review panel (review_panel.ReviewPanel), made on first use."""
        if getattr(self, "_review", None) is None:
            from .review_panel import ReviewPanel
            self._review = ReviewPanel(self)
        return self._review

    @property
    def layout(self):
        """The clean chat's renderer for this bot (telegram_layout.Layout), made on first use."""
        if getattr(self, "_layout", None) is None:
            from .telegram_layout import Layout
            self._layout = Layout(self)
        return self._layout

    def close_layout(self) -> None:
        """At shutdown: the panel is drawn once more (the offline notice just posted), then the renderer lease is
        given up, so the next start renders at once instead of waiting up to 90 s for the old lease to expire."""
        layout = getattr(self, "_layout", None)
        if layout is None or not config.TELEGRAM_CLEAN_CHAT:
            return
        try:
            layout.halt()
            if layout.is_renderer():
                layout.tick(force=True)
        except Exception as e:  # noqa: BLE001 - a shutdown never fails on the panel
            log.warning("Could not draw the notifications panel at shutdown: %s", e)
        finally:
            layout.release()

    # ------------------------------------------------------------ the ordered re-post (UI-01f)
    def _note_update(self, update: dict[str, Any]) -> None:
        """An update from the chat: the operator was active now (the re-post waits for an idle moment), its message
        id is the newest seen (the gap sweep's bound), and a message just received is not swept while it is read."""
        from .layout_repost import seen
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        mid = msg.get("message_id")
        if mid:
            seen(self.db, mid)
        if update.get("message") and mid:
            recent = self.__dict__.setdefault("_recent_ids", {})
            recent[int(mid)] = time.time()
            for old in [k for k, at in recent.items() if time.time() - at > 120]:
                recent.pop(old, None)
        sender = (update.get("message") or update.get("callback_query") or {}).get("from") or {}
        if str(sender.get("id", "")) in self.approver_ids:
            self._last_activity = time.time()

    def panel_copy(self, message_id: int | None, name: str) -> dict[str, Any] | None:
        """A layout panel as it shows now, as sendMessage data (silent is added by the caller): the content last
        drawn in it, or for a dashboard with none recorded its home screen, for a progress panel its run's
        current stage."""
        from .telegram_pages import panel as paginate
        content = None
        if message_id:
            content = (getattr(self, "_content", {}) or {}).get(self._current_panel(int(message_id)))
        if name == "dashboard" and getattr(self, "_pending_dashboard", None):
            content = self._pending_dashboard
        if content is None and name == "dashboard":
            from .config import ASSETS_DIR
            from .control import dashboard_text_and_buttons
            content = dashboard_text_and_buttons(self.db, getattr(self, "next_run_func", lambda: None)(), assets_dir=ASSETS_DIR)
        if content is None and name.startswith("progress:"):
            run_id = name.partition(":")[2]
            content = (f"{hourglass()} <b>Progress</b>\n\n<b>{html.escape(self.db.run_label(run_id))}</b>",
                       self._progress_buttons(run_id))
        if content is None:
            return None
        self.__dict__.setdefault("_copied", {})[name] = content   # what the new copy shows (adopt_copies)
        text, buttons = content
        text, buttons = paginate(self.db, _MASKER.mask(text), buttons)
        return {"chat_id": self.chat_id, "text": safe_html_truncate(text, 4000), "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []})}

    def adopt_copies(self, sent: dict[str, int], old: dict[str, int | None]) -> None:
        """After the re-post's swap: each panel's new message takes over its old one's screen, path, content and
        taps (the alias table); the progress record and the reviews follow their panels."""
        with self._panel_lock:
            for name, new in sent.items():
                before = old.get(name)
                media = name == "review"
                if before:
                    before = int(before)
                    self._panel_aliases[before] = int(new)
                    for cache in (self._panel_screen, self._panel_origin, self._panel_trail, self._panel_revisions,
                                  self.__dict__.setdefault("_content", {})):
                        if before in cache:
                            cache[int(new)] = cache[before]
                self._panel_media[int(new)] = media
                copied = self.__dict__.setdefault("_copied", {}).pop(name, None)
                if copied is not None:
                    self.__dict__.setdefault("_content", {})[int(new)] = copied
        for name, new in sent.items():
            self._record_kind(int(new), media=name == "review")
            if name.startswith("progress:"):
                try:
                    record = json.loads(self.db.get_setting("active_progress") or "{}")
                except ValueError:
                    record = {}
                if record.get("run_id") == name.partition(":")[2]:
                    record["message_id"] = int(new)
                    self.db.set_setting("active_progress", json.dumps(record))
                self._last_progress_edit[int(new)] = time.monotonic()
            elif name == "review":
                self.review.rebind(int(new))

    def pending_legacy_reviews(self) -> list[dict[str, Any]]:
        """Reviews waiting on a card of the earlier layout (no review-panel record): moved into the review panel at
        the first start of the clean chat's re-post build (design §13)."""
        from .review_panel import STATE
        from .telegram_state import get
        out = []
        for approval in self.db.list_pending_approvals():
            run = self.db.get_run(approval["run_id"]) or {}
            if run.get("status") == "pending_approval" and run.get("master_video_path") and not get(self.db, STATE, approval["run_id"]):
                out.append(run)
        return out

    def redeliver_review(self, run: dict[str, Any]) -> int | None:
        """The review copy sent again (as a Resend does), into the review panel."""
        root = Path(run["master_video_path"]).parent
        proxy = root / "review_proxy.mp4"
        video = proxy if proxy.is_file() else Path(run["master_video_path"])
        cover = Path(run.get("thumbnail_cover_path") or root / "thumbnail_cover.jpg")
        return self.send_approval_request(video, cover, run.get("script_payload") or {}, run["run_id"])

    def legacy_message_ids(self) -> list[int]:
        """Every message id the earlier layout recorded (design §13): alerts, review cards and albums, the timeout
        and "Review waiting" notices, cards, media screens, the messages runs were mapped to."""
        from .layout_repost import STUCK_KEY
        ids: set = set(self.db.list_known_message_ids() if hasattr(self.db, "list_known_message_ids") else [])
        with self.db._conn() as conn:
            for namespace, key, value in conn.execute(
                    "SELECT namespace, key, value FROM telegram_transport WHERE namespace IN "
                    "('review-messages', 'timeout-notice', 'review-need-notice', 'message-run', 'review-cover')"):
                try:
                    data = json.loads(value)
                except ValueError:
                    data = None
                if namespace == "message-run":
                    tail = str(key).rpartition(":")[2]
                    if tail.isdigit():
                        ids.add(int(tail))
                elif isinstance(data, list):
                    ids.update(m for m in data if isinstance(m, int))
                elif isinstance(data, int):
                    ids.add(data)
        for card in self.db.list_telegram_cards() if hasattr(self.db, "list_telegram_cards") else []:
            if card.get("card_kind") == "layout":
                continue
            payload = card.get("payload") or {}
            ids.update(m for m in (card.get("message_id"), payload.get("video_mid"), payload.get("card_mid"))
                       if isinstance(m, int))
        ids.update(self._media_ids())
        stuck = set(json.loads(self.db.get_setting(STUCK_KEY) or "[]") or [])
        return sorted(m for m in ids if isinstance(m, int) and m > 0 and m not in stuck)

    def legacy_notices(self) -> list[dict[str, Any]]:
        """The earlier chat's notices still in the chat (alerts not purged: persistent ones, and transient ones not
        yet expired) younger than 48 hours, oldest first: {"id", "text", "message_id", "run", "kind", "actions"}
        (design §13). The run is the one their message was mapped to, if any; the actions are the buttons the notice
        carried when this build recorded them (_remember_actions), else none."""
        from .telegram_state import get
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=48)
        out = []
        try:
            with self.db._conn() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT id, message_id, text, kind, expires_at, created_at FROM alerts WHERE purged = 0 "
                    "AND message_id IS NOT NULL AND kind IN ('persistent', 'transient') ORDER BY id")]
        except Exception as e:  # noqa: BLE001 - no alerts table: nothing to move
            log.debug("No earlier notices to move: %s", e)
            return []
        for row in rows:
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                expires = datetime.fromisoformat(str(row["expires_at"])) if row.get("expires_at") else None
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if created < cutoff or not str(row.get("text") or "").strip():
                continue
            if row["kind"] == "transient" and expires is not None and expires <= now:   # gone with its lifetime
                continue
            key = f"{self.chat_id}:{row['message_id']}"
            run = get(self.db, "message-run", key)
            actions = get(self.db, LEGACY_ACTIONS_NS, key) or []
            out.append({"id": row["id"], "text": row["text"], "message_id": row["message_id"], "run": run or None,
                        "kind": row["kind"], "actions": [a for a in actions if isinstance(a, dict)]})
        return out

    def _remember_actions(self, message_id: Any, buttons: Any) -> None:
        """A notice's own buttons, kept by its message id (the alerts table records only its text): when the clean
        chat starts (§13) its Needs-you item offers the same actions instead of only Details or 📊 Status."""
        from . import notifications as notes
        actions = notes.actions_from(buttons)
        if not (message_id and actions):
            return
        try:
            from .telegram_state import put
            put(self.db, LEGACY_ACTIONS_NS, f"{self.chat_id}:{message_id}", actions)
        except Exception as e:  # noqa: BLE001 - a notice is never lost over its record
            log.debug("Could not keep the notice's buttons: %s", e)

    def chat_is_older_than_48h(self) -> bool:
        """Whether the earlier chat holds messages the bot can no longer delete (its oldest recorded alert)."""
        try:
            with self.db._conn() as conn:
                row = conn.execute("SELECT MIN(created_at) FROM alerts WHERE message_id IS NOT NULL").fetchone()
            oldest = datetime.fromisoformat(str(row[0])) if row and row[0] else None
        except Exception:  # noqa: BLE001 - unknown: no note
            return False
        if oldest is None:
            return False
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        return datetime.now(UTC) - oldest > timedelta(hours=48)

    def stopped_footer(self, line: str) -> bool:
        """A clean stop: the dashboard's last line says the bot is stopped (design §7.3)."""
        stored = self.db.get_setting("telegram_dashboard_id")
        if not (stored and stored.isdigit()):
            return False
        with self._dashboard_lock:   # a dashboard edit (telegram_layout's lock order: taken before Layout._lock)
            mid = self._current_panel(int(stored))
            content = (getattr(self, "_content", {}) or {}).get(mid)
            if not content or self._is_media(mid):
                return False
            text, buttons = content
            return self.edit_message_text(mid, f"{text}\n\n<i>{html.escape(line)}</i>", buttons=buttons)

    def service_stopped(self) -> None:
        """The bot or daemon stops cleanly: said on the dashboard and in the service-state item, then the renderer
        lease is released (the next process takes it at once), as close_layout does. The renderer thread stops
        first, so nothing draws after the stop line; only the renderer says it (the lease)."""
        layout = self.layout
        try:
            layout.halt()
            if layout.is_renderer():
                layout.repost.stopping()
        except Exception as e:  # noqa: BLE001 - stopping goes on
            log.warning("Could not say the bot stopped: %s", e)
        finally:
            layout.release()

    def _notifications_tap(self, cq: dict[str, Any], command_handler) -> None:
        """A tap on the notifications panel (n:<item>[:<verb>], np:<page>, nall, nundo). It acts in place with a
        toast, or opens a dashboard screen in the dashboard; it never sends a message."""
        from . import notifications as notes
        data = cq.get("data", "")
        action, _, rest = data.partition(":")
        state = notes.view(self.db)
        came_from = int(state.get("from") or 1) if state.get("item") else int(state.get("page") or 1)
        answer = ""
        dispatch = None
        acting = None
        if action == "np":
            notes.set_view(self.db, int(rest) if rest.isdigit() else 1)
        elif action == "nall":
            ids = notes.dismiss_all_info(self.db)
            notes.set_view(self.db, 1)
            answer = f"{len(ids)} info dismissed. Undo for {notes.UNDO_SECONDS} s." if ids else "No info to dismiss"
        elif action == "nundo":
            answer = "Restored" if notes.undo_dismiss_all(self.db) else "Too late to undo"
            notes.set_view(self.db, 1)
        elif action == "n":
            ref, _, verb = rest.partition(":")
            item = notes.get(self.db, int(ref)) if ref.isdigit() else None
            if not item:
                answer = "That item has gone."
                notes.set_view(self.db, 1)
            elif not verb:
                notes.set_view(self.db, came_from, item["id"], came_from)
            elif item["state"] != notes.OPEN:
                answer = "This has been dealt with."
                notes.set_view(self.db, came_from, item["id"], came_from)
            elif verb == notes.DISMISS:
                notes.close(self.db, item["id"], notes.DISMISSED)
                notes.set_view(self.db, came_from)
                answer = "Dismissed"
            elif notes.find_action(item, verb):
                dispatch = notes.find_action(item, verb)["callback_data"]
                if notes.opens_a_screen(dispatch):   # a screen: drawn in the dashboard, the item page stays
                    notes.set_view(self.db, came_from, item["id"], came_from)
                else:   # an action: the item closes only once it is done (_act_on_item)
                    acting = item
            else:
                answer = "That button is no longer offered."
        if acting is not None:
            self._act_on_item(cq, acting, dispatch, command_handler, came_from)
            return
        if dispatch is None:
            self._answer_callback(cq["id"], answer)
        try:
            self.layout.redraw()
        except Exception as e:  # noqa: BLE001 - the tap's own action still runs
            log.warning("Could not redraw the notifications panel: %s", e)
        if dispatch is not None:
            self._dispatch_in_dashboard(cq, dispatch, command_handler)

    def _act_on_item(self, cq: dict[str, Any], item: dict[str, Any], callback: str, command_handler,
                     came_from: int) -> None:
        """An item's action (Re-render, Back to the queue, Retry, Resend for review ...), run as a tap on the dashboard.
        The item closes only when the action was done (design §6.1, spec: Needs you stays until handled); when it
        was refused or failed, the item stays open and its page says why, in the handler's own words (design §10.1).
        The tap gets one toast saying what happened; nothing is sent as a message (design §6.1, §9)."""
        from . import notifications as notes
        kind, _, target = callback.partition(":")
        acting = {"cq": cq["id"], "done": None, "reason": "", "said": "", "toasts": []}
        token = _ITEM_ACTION.set(acting)
        error = None
        try:
            self._dispatch_in_dashboard(cq, callback, command_handler)
        except Exception as e:  # noqa: BLE001 - a failed action leaves its item open, saying why
            error = e
            log.warning("The item's %s action failed: %s", kind, _MASKER.mask(str(e)))
        finally:
            _ITEM_ACTION.reset(token)
        done = acting["done"]
        if done is None:
            done = error is None and self._item_action_done(kind, target)
        reason = acting["reason"] or acting["said"] or next((t for t in acting["toasts"] if t not in PLAIN_TOASTS), "") or (
            f"it failed ({type(error).__name__}). Try again, or open Details." if error is not None else "")
        now = notes.get(self.db, item["id"]) or item
        if now.get("state") != notes.OPEN:   # the action took care of its item itself
            notes.set_view(self.db, came_from)
            toast = acting["said"] or next((t for t in acting["toasts"] if t not in PLAIN_TOASTS), "") or "Done"
        elif done:
            if not str(item.get("dedupe_key", "")).startswith("cond:"):   # a condition resolves itself
                notes.close(self.db, item["id"], notes.HANDLED)
            notes.set_view(self.db, came_from)
            toast = acting["said"] or next((t for t in acting["toasts"] if t not in PLAIN_TOASTS), "") or "Done"
        else:
            notes.note(self.db, item["id"], reason or "the action did not run.")
            notes.set_view(self.db, came_from, item["id"], came_from)
            toast = notes.clip(notes.plain(reason), 190) or "Not done"
        self._answer_callback(cq["id"], notes.clip(notes.plain(toast), 190))
        try:
            self.layout.redraw()
        except Exception as e:  # noqa: BLE001 - the item is recorded; the next tick draws it
            log.warning("Could not redraw the notifications panel: %s", e)

    @staticmethod
    def _item_outcome(done: bool, reason: str = "") -> None:
        """A branch that ran an item's action says whether it was done (and if not, why)."""
        acting = _ITEM_ACTION.get()
        if acting is not None:
            acting["done"] = bool(done)
            acting["reason"] = "" if done else str(reason or "")

    def _item_action_done(self, kind: str, target: str) -> bool:
        """Whether an item's action did what its item asks, read from the recorded state where there is one (a
        topic back in the queue, a booking posted); any other action that ran without an error is done."""
        if kind in CLOSE_THEIR_ITEM:   # done means its item was closed by it (handled above)
            return False
        if kind in SAY_THEIR_OUTCOME:   # done only when its handler says so (notifications.Outcome)
            return False
        try:
            if kind == "requeue":
                return (self.db.get_topic_by_id(target.strip()) or {}).get("status") == "queued"
            if kind == "retrypost" and target.strip().isdigit():
                post = next((p for p in self.db.list_scheduled_posts(
                    ("pending", "waiting", "published", "failed", "unknown", "cancelled", "expired", "blocked"))
                    if p["id"] == int(target)), None)
                return bool(post) and post.get("status") == "published"
        except Exception as e:  # noqa: BLE001 - not known: the item stays open (never lost)
            log.warning("Could not tell whether the %s action was done: %s", kind, e)
            return False
        return True

    @staticmethod
    def _item_reply(reply: Any) -> None:
        """An item's action answered: its first line is what the toast (or, when not done, the item) says."""
        acting = _ITEM_ACTION.get()
        text = reply[0] if isinstance(reply, tuple) else reply
        if acting is not None and not acting["said"] and isinstance(text, str) and text.strip():
            from . import notifications as notes
            acting["said"] = " ".join(notes.plain(text.strip().split("\n\n", 1)[0]).split())
        if acting is not None and acting["done"] is None and getattr(text, "done", None) is not None:
            # The handler said whether it was done (notifications.Outcome): its words are only what is shown
            TelegramApprovalBot._item_outcome(bool(text.done), acting["said"])

    def _say_reply(self, cq: dict[str, Any], reply: Any) -> None:
        """The clean chat's answer to a tap whose reply only acknowledges (Back to the queue, Resend for review):
        the callback's toast when it fits (≤ 190 characters) and the tap is not answered yet, else a line drawn in
        the dashboard. Never a message (design §6.1, §9: "No other ring")."""
        from . import notifications as notes
        text = reply[0] if isinstance(reply, tuple) else reply
        if not (isinstance(text, str) and text.strip()):
            return
        plain_text = " ".join(notes.plain(text).split())
        acting = _ITEM_ACTION.get()
        if acting is not None and acting["cq"] == cq.get("id"):
            acting["said"] = plain_text
            if len(plain_text) <= 190:
                return
        elif len(plain_text) <= 190 and cq.get("id") not in self._answered:
            self._answer_callback(cq["id"], plain_text)
            return
        self.show_dashboard(text, [[DONE_BUTTON]])

    def dashboard_idle_tick(self, now: float | None = None) -> bool:
        """Back home by itself (design §6.2): after 15 minutes without a tap the dashboard returns to its home
        screen; 30 minutes while typed input is armed or a wizard (upload, music review) is mid-step."""
        if not config.TELEGRAM_CLEAN_CHAT:
            return False
        from .telegram_layout import HOME_IDLE_BUSY_SECONDS, HOME_IDLE_SECONDS
        from .telegram_ui import NOT_REPLAYABLE
        now = time.time() if now is None else now
        stored = self.db.get_setting("telegram_dashboard_id")
        if not (stored and stored.isdigit()):
            return False
        mid = self._current_panel(int(stored))
        screen = self._panel_screen.get(mid)
        if not screen or screen.partition(":")[0] == "cdash":
            return False
        busy = bool(self._awaiting) or screen.partition(":")[0] in NOT_REPLAYABLE
        if now - float(getattr(self, "_dashboard_touched", now)) < (HOME_IDLE_BUSY_SECONDS if busy else HOME_IDLE_SECONDS):
            return False
        with self._panel_lock:
            self._panel_screen[mid] = "cdash:"
            self._panel_trail[mid] = ["cdash:"]
        self.show_dashboard()
        return True

    def _dispatch_in_dashboard(self, cq: dict[str, Any], callback: str, command_handler) -> None:
        """Runs an item's action as a tap on the dashboard, so its screen or result is drawn there (the item
        page stays where it is)."""
        stored = self.db.get_setting("telegram_dashboard_id")
        if not (stored and stored.isdigit()):
            self.show_dashboard()
            stored = self.db.get_setting("telegram_dashboard_id")
        if not (stored and stored.isdigit()):
            self._answer_callback(cq["id"], "The dashboard is not available; open it with /dashboard")
            return
        from .telegram_ui import step
        with self._panel_lock:
            mid = self._current_panel(int(stored))
            self._panel_revisions[mid] = self._panel_revisions.get(mid, 0) + 1
            revision = self._panel_revisions[mid]
            self._panel_origin[mid] = self._panel_screen.get(mid)
            self._panel_screen[mid] = callback
            self._panel_trail[mid] = step(self._panel_trail.get(mid, []), callback)
        message = {"message_id": mid, "chat": {"id": self.chat_id}}
        update = {"callback_query": {"id": cq["id"], "from": cq.get("from") or {}, "message": message,
                                     "data": callback, "_revision": revision}}
        self._process_update(update, command_handler)

    # ------------------------------------------------------------ live render progress
    def progress_mode(self, run_id):
        mode = self.db.get_setting("run_mode:" + run_id)
        regen = self.db.get_setting("regenerating:" + run_id)  # set when the run is a regeneration
        note = f"\n🔄 Regeneration · your feedback: <b>{html.escape(regen)}</b>" if regen else ""
        if mode == "dry_run":
            return "🧪 Dry run · publishing/scheduling skipped · configured generation may incur API costs" + note
        if mode == "live":
            return "🔴 Live production · publication requires approval and release checks" + note
        return "⚠️ Run mode not recorded · inspect before publication" + note

    PROGRESS_TICK_SECONDS = 60

    def start_progress_ticker(self, run_id: str) -> threading.Thread:
        """Redraws the run's progress card every PROGRESS_TICK_SECONDS, so its elapsed time, the running
        step's time and the time left move during a long step (operator, 2026-10-25: it only changed when a
        step did). It stops once the run is no longer being made, or another run's card is the live one. An
        edit does not buzz the phone, so this is silent."""
        def tick():
            while True:
                time.sleep(self.PROGRESS_TICK_SECONDS)
                try:
                    run = self.db.get_run(run_id)
                    if not run or run.get("status") != "producing":
                        return
                    active = self.db.get_active_progress()
                    if active and active.get("run_id") != run_id:
                        return
                    if active and active.get("message_id"):
                        with self._progress_lock:  # never over the receipt the run's end is writing (review, 2026-10-25)
                            if run_id in self._finished_runs:
                                return
                            self.update_progress_card(active["message_id"], int(active.get("step") or 1),
                                                      int(active.get("total_steps") or 5), active.get("topic_title") or "",
                                                      run_id, active.get("details") or "")
                except Exception as e:  # a missed tick is harmless; the next one tries again
                    log.debug("Progress tick for %s failed: %s", run_id, _MASKER.mask(str(e)))
        thread = threading.Thread(target=tick, name=f"progress-tick-{run_id[-10:]}", daemon=True)
        thread.start()
        return thread

    def send_progress_card(self, run_id: str, topic_title: str) -> Optional[int]:
        """Sends an initial progress card for a running pipeline job, returning message_id."""
        if not self.is_configured():
            return None
        self._progress_start_time[run_id] = time.time()
        typical = typical_run_seconds()
        bar, pct, eta = progress_figures(1, 5, 0, typical, 0.0, step_seconds(self.db, 5, typical))
        text = (
            f"{hourglass()} <b>Progress</b>\n\n"
            f"<b>{html.escape(topic_title)}</b> · started {show_time(None, '%H:%M')}\n\n"
            f"🔄 <b>Step 1/5:</b> <i>{PROGRESS_STEPS[0]}...</i>\n"
            + "".join(f"◽ <b>Step {n}/5:</b> {m}\n" for n, m in enumerate(PROGRESS_STEPS[1:], start=2)) + "\n"
            + ("" if clean_chat() else f"Progress: <code>[{bar}] {pct}%</code>\n")   # the clean chat: no bar (§6.4)
            + f"⏱ <b>Elapsed:</b> 00:00 · <b>Time left:</b> {eta}"
        )
        text = self.progress_mode(run_id) + "\n\n" + text
        buttons = self._progress_buttons(run_id)
        markup = {"inline_keyboard": format_responsive_keyboard(buttons)}
        try:
            from .telegram_state import get as state_get, put as state_put
            reuse = state_get(self.db, "progress-reuse", run_id) if not clean_chat() else None
            mid = None
            if reuse:  # a re-render tapped on a card: that card becomes the progress card
                state_put(self.db, "progress-reuse", run_id, "")
                if self.edit_message_text(int(reuse), text, buttons=buttons):
                    mid = int(reuse)
            if mid is None:
                prog_data = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(markup),
                }
                if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS") and "runs" in config.TELEGRAM_TOPICS:
                    prog_data["message_thread_id"] = config.TELEGRAM_TOPICS["runs"]
                msg = self._api("sendMessage", data=prog_data)
                mid = msg.get("message_id") if isinstance(msg, dict) else None
            if mid:
                from .telegram_state import put
                from .card_ledger import record as record_card
                put(self.db, "message-run", f"{self.chat_id}:{mid}", run_id)
                record_card(run_id, "progress", text, buttons, mid)
                self.__dict__.setdefault("_content", {})[mid] = (text, copy.deepcopy(buttons))
                if clean_chat():   # a layout row: the reaper deletes it if its run ends without its finisher
                    from .process_state import identity
                    from .telegram_layout import put_slot
                    put_slot(self.db, f"progress:{run_id}", mid, owner_pid=os.getpid(),
                             owner_identity=identity(os.getpid()))   # the process making the run (design §5.6)
                self._last_progress_edit[mid] = time.monotonic()
                self._last_progress_step[mid] = 1
                if hasattr(self.db, "set_active_progress"):
                    self.db.set_active_progress(
                        run_id=run_id,
                        topic_title=topic_title,
                        step=1,
                        total_steps=5,
                        details="Verifying script & historical fact sheets",
                        message_id=mid,
                        start_time=time.time(),
                    )
                self.start_progress_ticker(run_id)
            return mid
        except Exception as e:
            log.warning("Could not send Telegram progress card: %s", e)
            return None

    def bring_progress_down(self) -> bool:
        """Progress during a run: the live card moves to the bottom of the chat (deleted, then redrawn from
        the recorded progress; the worker keeps updating the new card) instead of a second copy that goes
        stale. False when no verified run is being made."""
        active = self.db.get_active_progress() if hasattr(self.db, "get_active_progress") else None
        if not active or active.get("worker_state") != "running" or not active.get("message_id"):
            return False
        old = int(active["message_id"])
        self.delete_message(old)
        self._missing_messages.add(old)
        self._last_progress_edit.pop(old, None)
        # Redrawn outright: editing the card just deleted only produced "message to edit not found" first.
        self.update_progress_card(old, active.get("step", 1), active.get("total_steps", 5), active.get("topic_title", ""),
                                  active["run_id"], active.get("details", ""), redraw=True)
        new = self._live_progress_card(active["run_id"])
        if clean_chat() and new and new != old:   # the layout row follows the panel (its finisher and the reaper)
            from .telegram_layout import SLOT_PREFIX, put_slot
            card = self.db.get_telegram_card(SLOT_PREFIX + f"progress:{active['run_id']}") or {}
            extra = {k: v for k, v in (card.get("payload") or {}).items() if k != "sent_at"}
            put_slot(self.db, f"progress:{active['run_id']}", new, **extra)
        return True

    def _live_progress_card(self, run_id: str) -> Optional[int]:
        """The progress card now in the chat for this run, from the raw record (get_active_progress drops a
        record whose run has already ended, which is exactly when the card is finished)."""
        try:
            record = json.loads(self.db.get_setting("active_progress") or "{}")
        except (ValueError, TypeError):
            return None
        mid = record.get("message_id") if record.get("run_id") == run_id else None
        return int(mid) if mid else None

    def update_progress_card(self, message_id: int, step: int, total_steps: int, topic_title: str, run_id: str,
                             details: str = "", redraw: bool = False) -> bool:
        """Updates the progress card in-place with smooth bar, active sub-step, and ETA debouncing.
        redraw: the card is known to be gone (Refresh Progress deleted it); post a new one at once."""
        if not self.is_configured() or not message_id:
            return False

        active = self.db.get_active_progress()
        run = self.db.get_run(run_id)
        if run and run.get("status") != "producing":
            return False
        if active and active.get("run_id") != run_id:
            return False
        if active:
            message_id = active.get("message_id") or message_id
            self._progress_start_time[run_id] = active.get("start_time", time.time())

        now = time.monotonic()
        last_edit = self._last_progress_edit.get(message_id, 0.0)
        last_step = self._last_progress_step.get(message_id, 0)
        # Token-bucket rate limiter: ensure ≥1.2s between Telegram edits unless milestone step advances or step completes
        if (now - last_edit < 1.2) and (step == last_step) and (step < total_steps):
            return True

        milestones = PROGRESS_STEPS

        lines = [
            f"{hourglass()} <b>Progress</b>\n\n<b>{html.escape(topic_title)}</b> · started {show_time(self._progress_start_time.get(run_id), '%H:%M')}",
            "",
        ]
        record = dict(active or {})
        record.setdefault("start_time", self._progress_start_time.get(run_id, time.time()))
        if record.get("step") != step:  # this update starts the step: count it from now
            record["step_started"] = {**(record.get("step_started") or {}), str(step): time.time()}
        bar, pct, eta, elapsed_sec, step_elapsed = progress_estimate(self.db, record, step, total_steps)
        for idx, m in enumerate(milestones, start=1):
            if idx < step:
                lines.append(f"✅ <b>Step {idx}/{total_steps}:</b> {m}")
            elif idx == step:
                detail_str = f" <i>({html.escape(details)})</i>" if details else ""
                lines.append(f"🔄 <b>Step {idx}/{total_steps}:</b> {m}...{detail_str}{step_running_note(step_elapsed)}")
            else:
                lines.append(f"◽ <b>Step {idx}/{total_steps}:</b> {m}")

        if not clean_chat():   # the clean chat drops the bar (design §6.4): the stages say it
            lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
        lines.append(f"⏱ <b>Elapsed:</b> {elapsed_sec // 60:02d}:{elapsed_sec % 60:02d} · <b>Time left:</b> {eta}")

        lines.insert(1, self.progress_mode(run_id))
        text = "\n".join(lines)
        self._last_progress_edit[message_id] = now
        self._last_progress_step[message_id] = step

        buttons = self._progress_buttons(run_id)

        # Keep DB updated with real-time active progress
        if hasattr(self.db, "set_active_progress"):
            self.db.set_active_progress(
                run_id=run_id,
                topic_title=topic_title,
                step=step,
                total_steps=total_steps,
                details=details,
                message_id=message_id,
            )

        from .card_ledger import record as record_card
        success = False if redraw else self.edit_message_text(message_id, text, buttons=buttons)
        if success:
            record_card(run_id, "progress", text, buttons, message_id)
        if not success and (redraw or message_id in self._missing_messages):
            # Self-healing: if the card was purged/cleared from Telegram, recreate it!
            try:
                heal_data = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons)}),
                }
                if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS") and "runs" in config.TELEGRAM_TOPICS:
                    heal_data["message_thread_id"] = config.TELEGRAM_TOPICS["runs"]
                msg = self._api("sendMessage", data=heal_data)
                new_mid = msg.get("message_id") if isinstance(msg, dict) else None
                if new_mid:
                    record_card(run_id, "progress", text, buttons, new_mid)
                    self._last_progress_edit[new_mid] = now
                    self._last_progress_step[new_mid] = step
                    if hasattr(self.db, "set_active_progress"):
                        self.db.set_active_progress(
                            run_id=run_id,
                            topic_title=topic_title,
                            step=step,
                            total_steps=total_steps,
                            details=details,
                            message_id=new_mid,
                        )
                    return True
            except Exception as e:
                log.warning("Could not recreate progress card: %s", e)
        return success

    def finish_progress_card(self, message_id: int, topic_title: str, run_id: str, success: bool = True, error: str = "") -> bool:
        """Finishes the progress card into a completed receipt, or deletes it if video review was sent. The
        dashboard is redrawn after it, so it never shows the previous outcome (operator, 2026-10-25: it still
        said 53.7 s after the re-render had failed at 54.4 s)."""
        with self._progress_lock:
            self._finished_runs.add(run_id)  # the ticker's next redraw must not overwrite the receipt
        try:
            return self._finish_progress_card(message_id, topic_title, run_id, success, error)
        finally:
            try:
                if self.db.get_setting("telegram_dashboard_id"):  # redraw the panel there is; never post one
                    self.show_dashboard()
            except Exception as e:  # a redraw never breaks a run's ending
                log.debug("Dashboard not redrawn after %s: %s", run_id, e)

    def _finish_progress_card(self, message_id: int, topic_title: str, run_id: str, success: bool = True, error: str = "") -> bool:
        # The live card, not the one the run started with: Refresh Progress moves the card to the bottom (a
        # new message each time), and the run's own id then points at a deleted one. Read before clearing.
        message_id = self._live_progress_card(run_id) or message_id
        if hasattr(self.db, "clear_active_progress"):
            self.db.clear_active_progress(run_id)
        if not self.is_configured() or not message_id:
            return False
        if clean_chat():
            return self._end_progress_panel(message_id, run_id, success, error)
        if not success:
            run = self.db.get_run(run_id)
            if run and run.get("status") == "cancelled":
                # The label once (it was printed twice: once as the title, once in a code line; 2026-10-24),
                # and the run id in the receipt's form.
                text = (f"⏹ <b>Render cancelled:</b> {html.escape(self.db.run_label(run_id))}\n"
                        f"<i>Run ID:</i> <code>{html.escape(run_id)}</code>\n\n"
                        "Stopped at a stage boundary. Existing assets and incurred usage are retained; "
                        "the topic is back in the queue.")
                from .card_ledger import record as record_card
                record_card(run_id, "cancelled", text, [[DONE_BUTTON]], message_id)
                return self.edit_message_text(message_id, text, buttons=[[DONE_BUTTON]])
            # The run's one failure card (the separate "Production failed" alert is not sent as well).
            from .control import short_reason
            # The whole reason, folded (as on the dashboard): 600 characters cut a release finding mid-way.
            # Re-render once the script has passed the fact check (src/resume_points.py), saying what it keeps.
            from .config import OUTPUT_DIR
            from .resume_points import kept_phrase, worth_resuming
            late = bool(run) and worth_resuming(OUTPUT_DIR / run_id, run)
            kept = kept_phrase(OUTPUT_DIR / run_id, run) if late else ""
            # The run id is on the card, as on the review card and receipts: a typed /rerender or /resume
            # needs it, and the operator had nowhere to read it once the review card was gone (2026-10-24).
            from .control import failed_heading
            text = (f"❌ <b>{failed_heading(self.db, run_id)}:</b> {html.escape(self.db.run_label(run_id))}\n"
                    f"<i>Run ID:</i> <code>{html.escape(run_id)}</code>\n\n"
                    f"<blockquote expandable>{html.escape(short_reason(error, 1200))}</blockquote>\n\n"
                    + (f"Nothing was posted. Re-render continues from where it stopped, keeping {html.escape(kept)} "
                       "(anything whose inputs changed is made again; a voiceover outside the length limits is made again, "
                       "after its script is lengthened or shortened for this voice if it needs it).\n\n" if late else
                       "Nothing was posted. It stopped before its script passed the fact check, so nothing is worth "
                       "keeping. The topic is set aside, not retried on its own: tap Back to the queue to have it "
                       "made again, or restore it later from the queue.\n\n")
                    + "<i>Reply to this card with a question to ask about this run.</i>")
            buttons = [[DONE_BUTTON]]
            if late:  # from a fact-checked script onward it is made again in place
                buttons.insert(0, [{"text": "🔁 Re-render", "callback_data": f"rerender:{self.db.run_key(run_id)}"}])
            elif run and run.get("topic_id"):  # a failed topic is set aside (operator, 2026-10-25: "wasn't requeued")
                buttons.insert(0, [{"text": "↩️ Back to the queue", "callback_data": f"requeue:{run['topic_id']}"}])
            from .card_ledger import record as record_card
            record_card(run_id, "failure", text, buttons, message_id)
            return self.edit_message_text(message_id, text, buttons=buttons)
        else:
            return self.delete_message(message_id)

    def _progress_buttons(self, run_id: str) -> list:
        """The progress panel's keyboard. The clean chat (UI-01c): Cancel run alone (no Refresh: it redraws
        itself; no 🏠: the dashboard is just above), or, for 5 minutes after it is tapped, its confirmation
        ← Keep making · Cancel run. The earlier chat: Refresh, Cancel run and 🏠."""
        key = self.db.run_key(run_id)
        if not clean_chat():
            return [[{"text": "🔄 Refresh", "callback_data": f"cprog:{key}"},
                     {"text": "⏹ Cancel run", "callback_data": f"runstop:{key}"}], [DONE_BUTTON]]
        asked = getattr(self, "_stop_asked", {}).get(run_id)
        if asked and time.time() - asked < STOP_CONFIRM_SECONDS:
            return [[{"text": "← Keep making", "callback_data": f"pkeep:{key}"},
                     {"text": "Cancel run", "style": "danger", "callback_data": f"pstopok:{key}"}]]
        return [[{"text": "Cancel run", "callback_data": f"pstop:{key}"}]]

    def _progress_tap(self, cq: dict[str, Any]) -> None:
        """Cancel run on the progress panel: the confirmation replaces only its keyboard (design §6.4)."""
        action, _, ref = cq.get("data", "").partition(":")
        run_id = self.db.resolve_run(ref)
        mid = (cq.get("message") or {}).get("message_id")
        stops = self.__dict__.setdefault("_stop_asked", {})
        if action == "pstop":
            stops[run_id] = time.time()
            self._answer_callback(cq["id"], "Cancel this run? It stops at the next step; what was made and paid for "
                                            "is kept, and the topic goes back in the queue.")
        elif action == "pkeep":
            stops.pop(run_id, None)
            self._answer_callback(cq["id"], "Still making it")
        else:
            stops.pop(run_id, None)
            accepted = self.db.request_run_cancel(run_id)
            self._answer_callback(cq["id"], "Cancel requested: it stops at the next step." if accepted
                                  else "This run is no longer being made; nothing was cancelled.")
            if accepted and mid:
                self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": mid,
                                                          "reply_markup": json.dumps({"inline_keyboard": []})})
                return
        if mid:
            self._api("editMessageReplyMarkup", data={"chat_id": self.chat_id, "message_id": mid, "reply_markup":
                                                      json.dumps({"inline_keyboard": self._progress_buttons(run_id)})})

    def _end_progress_panel(self, message_id: int, run_id: str, success: bool, error: str) -> bool:
        """The clean chat (UI-01c): the progress panel lives exactly as long as its run. It is deleted at the end;
        a failure becomes the Needs-you item "❌ <heading> · <title>" (Re-render, or Back to the queue when nothing
        is worth keeping, · Details) and rings once; a cancel becomes an info item. Returns True: the ending was
        said (so no second failure alert is sent)."""
        from . import notifications as notes
        from .telegram_layout import drop_slot
        self.delete_message(message_id)
        drop_slot(self.db, f"progress:{run_id}")
        if success:
            return True
        run = self.db.get_run(run_id) or {}
        label = self.db.run_label(run_id)
        if run.get("status") == "cancelled":
            notes.post(self.db, notes.INFO, f"cancel:{run_id}", "Render cancelled", emoji="⏹️", subject=label, run=run_id,
                       body="Stopped at a step boundary. What was made and paid for is kept; the topic is back in the queue.")
        else:
            from .config import OUTPUT_DIR
            from .control import failed_heading, short_reason
            from .resume_points import kept_phrase, worth_resuming
            late = bool(run) and worth_resuming(OUTPUT_DIR / run_id, run)
            kept = kept_phrase(OUTPUT_DIR / run_id, run) if late else ""
            reason = html.escape(short_reason(error, 1000))
            if late:
                body = f"Nothing was posted. Re-render continues from where it stopped, keeping {html.escape(kept)}.\n{reason}"
                actions = [{"text": "Re-render", "callback_data": f"rerender:{self.db.run_key(run_id)}"}]
            else:
                body = ("Nothing was posted. It stopped before its script passed the fact check, so nothing is worth "
                        f"keeping; the topic is set aside.\n{reason}")
                actions = ([{"text": "Back to the queue", "callback_data": f"requeue:{run['topic_id']}"}]
                           if run.get("topic_id") else [])
            actions.append({"text": "Details", "callback_data": f"chist:{self.db.run_key(run_id)}"})
            notes.post(self.db, notes.NEEDS, f"fail:{run_id}", failed_heading(self.db, run_id), body, emoji="❌",
                       subject=label, actions=actions, run=run_id, replace=True)
        self.layout.kick()
        return True

    def reap_progress_panels(self, now: float | None = None) -> int:
        """The reaper (design §5.6, §7.4; the renderer runs it every 60 s). A progress panel goes when its run is
        no longer being made and its finisher did not remove it (after REAP_GRACE_SECONDS, so the finisher always
        has its turn), or when the process making the run has died (its recorded pid is gone or is another
        process now): that run is marked interrupted, its topic goes back in the queue, and it becomes the
        Needs-you item "⚠️ Run interrupted" under its failure key, so a worker-crash notice or the start-up
        clearing for the same run are the same item. A cancelled run is the info item "⏹️ Render cancelled"."""
        from . import notifications as notes
        from .telegram_layout import SLOT_PREFIX, drop_slot
        now = time.time() if now is None else now
        reaped = 0
        for card in self.db.list_telegram_cards(topic_id=0, card_kind="layout"):
            name = str(card.get("card_id", "")).removeprefix(SLOT_PREFIX)
            if not name.startswith("progress:"):
                continue
            run_id = name.partition(":")[2]
            run = self.db.get_run(run_id) or {}
            status = run.get("status")
            if status == "producing":
                if not self._run_owner_dead(card.get("payload") or {}, run_id):
                    continue
                self._mark_interrupted(run)
                status = "interrupted"
            elif run and now - _age_anchor(run) < REAP_GRACE_SECONDS:
                continue   # just ended: its own process is still finishing it
            self.delete_message(int(card["message_id"]))
            drop_slot(self.db, name)
            reaped += 1
            label = self.db.run_label(run_id) if run else run_id
            if status == "cancelled":
                if not notes.has_open(self.db, f"cancel:{run_id}"):
                    notes.post(self.db, notes.INFO, f"cancel:{run_id}", "Render cancelled", emoji="⏹️", subject=label,
                               run=run_id, body="Stopped at a step boundary. What was made and paid for is kept.")
            elif (status in ("failed", "timed_out", "interrupted") or not run) and not notes.has_open(self.db, f"fail:{run_id}"):
                notes.post(self.db, notes.NEEDS, f"fail:{run_id}", "Run interrupted", emoji="⚠️", subject=label,
                           run=run_id, body="The process making it ended before it finished. Nothing was posted; the "
                                            "topic is back in the queue.",
                           actions=notes.failure_actions(self.db, run_id))
        return reaped   # the renderer's tick draws the panel right after (no kick: it runs under Layout._lock)

    def _run_owner_dead(self, payload: dict[str, Any], run_id: str) -> bool:
        """Whether the process recorded as making run_id has ended: its pid is gone, or is now another process
        (a different creation identity). A process that cannot be checked ("unknown") is never declared dead on a
        guess, as main.reconcile_interrupted_runs decides."""
        from .process_state import identity
        pid, owner = payload.get("owner_pid"), payload.get("owner_identity")
        if not pid:
            try:
                record = json.loads(self.db.get_setting("run_owner:" + run_id) or "{}")
            except ValueError:
                record = {}
            pid, owner = record.get("pid"), record.get("identity")
        if not pid:
            return False
        observed = identity(int(pid))
        if observed is None:
            return True
        return observed != "unknown" and bool(owner) and owner != "unknown" and observed != owner

    def _mark_interrupted(self, run: dict[str, Any]) -> None:
        """The run whose process died: failed as interrupted, its progress record cleared, its topic back in the
        queue (the start-up reconcile's own steps, main.reconcile_interrupted_runs)."""
        from .worker_deadline import forget
        run_id = run["run_id"]
        self.db.update_run(run_id, "failed", error=INTERRUPTED_ERROR)
        if hasattr(self.db, "clear_active_progress"):
            self.db.clear_active_progress(run_id)
        if run.get("topic_id") and hasattr(self.db, "record_topic_status"):
            self.db.record_topic_status(run["topic_id"], "queued", run_id=run_id)
        forget(self.db, run_id)
        log.warning("Run %s was interrupted (its process ended); marked failed, topic back in the queue.", run_id)

    def delete_message(self, message_id: int) -> bool:
        """Deletes a message from Telegram. Returns True if deleted, False otherwise."""
        if not self.is_configured() or not message_id:
            return False
        try:
            self._api("deleteMessage", data={"chat_id": self.chat_id, "message_id": message_id})
            return True
        except Exception as e:
            log.debug("Could not delete Telegram message %s: %s", message_id, e)
            return False

    def send_chat_action(self, action: str = "typing") -> bool:
        """Sends a chat action (e.g. 'typing') to provide instant responsiveness feedback."""
        if not self.is_configured():
            return False
        try:
            self._api("sendChatAction", data={"chat_id": self.chat_id, "action": action})
            return True
        except Exception:
            return False

    def delete_messages(self, message_ids: List[int]) -> int:
        """
        Deletes multiple messages in atomic batches of up to 100 using Telegram's
        bulk deleteMessages API. Fast, instant, and eliminates slow one-by-one UI animations.
        """
        if not self.is_configured() or not message_ids:
            return 0
        valid_ids = [m for m in message_ids if isinstance(m, int) and m > 0]
        if not valid_ids:
            return 0

        chunks = [valid_ids[i:i + 100] for i in range(0, len(valid_ids), 100)]

        def _delete_chunk(chunk: List[int]) -> int:
            try:
                self._api("deleteMessages", data={
                    "chat_id": self.chat_id,
                    "message_ids": json.dumps(chunk),
                })
                return len(chunk)
            except Exception as e:
                log.debug("Bulk deleteMessages failed for chunk: %s. Falling back to individual delete.", e)
                cnt = 0
                consecutive_fails = 0
                for mid in chunk:
                    try:
                        if self.delete_message(mid):
                            cnt += 1
                            consecutive_fails = 0
                        else:
                            consecutive_fails += 1
                    except Exception:
                        consecutive_fails += 1
                    if consecutive_fails >= 5:
                        break
                return cnt

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, max(1, len(chunks)))) as pool:
            results = list(pool.map(_delete_chunk, chunks))

        return sum(results)

    MEDIA_MESSAGES_KEY = "telegram_media_messages"  # message ids known to be photo/audio/video (no text to edit)
    MEDIA_MESSAGES_KEPT = 300

    def _media_ids(self) -> List[int]:
        try:
            value = json.loads(self.db.get_setting(self.MEDIA_MESSAGES_KEY) or "[]") if hasattr(self.db, "get_setting") else []
        except (TypeError, ValueError):
            return []
        return [m for m in value if type(m) is int] if isinstance(value, list) else []

    def _record_kind(self, message_id: Optional[int], media: bool) -> None:
        """Records whether a tracked message is media (photo/audio/video with a caption) or text, in memory and in
        the database: a new process (a restart, the run's worker) knew nothing of it and sent editMessageText to a
        photo panel ("there is no text in the message to edit": dev 2026-10-28, production 2026-10-29)."""
        if not message_id:
            return
        cache = getattr(self, "_panel_media", None)
        if cache is None:
            cache = self._panel_media = {}
        cache[message_id] = bool(media)
        if not hasattr(self.db, "set_setting"):
            return
        try:
            ids = self._media_ids()
            if media and message_id not in ids:
                ids = (ids + [message_id])[-self.MEDIA_MESSAGES_KEPT:]
            elif not media and message_id in ids:
                ids = [m for m in ids if m != message_id]
            else:
                return  # unchanged: no write
            self.db.set_setting(self.MEDIA_MESSAGES_KEY, json.dumps(ids))
        except Exception as e:  # bookkeeping never breaks a screen
            log.debug("Could not record the kind of message %s: %s", message_id, e)

    def _is_media(self, message_id: Optional[int], default: bool = False) -> bool:
        """Whether a message is media: this process's record first, then the one kept in the database."""
        cache = getattr(self, "_panel_media", None) or {}
        if message_id in cache:
            return cache[message_id]
        return True if message_id and message_id in self._media_ids() else default

    def _edit_caption_instead(self, message_id: int, text: str, buttons: Optional[list]) -> bool:
        """A text edit meant for a media message: its caption takes the text when it fits Telegram's 1,024
        characters; otherwise False, and the caller sends a fresh card, as after any failed edit."""
        if len(_MASKER.mask(text)) > 1024:
            log.info("Message %s is media and the new text is longer than a caption; a fresh card replaces it.", message_id)
            return False
        return self.edit_message_caption(message_id, text, buttons)

    def edit_message_text(self, message_id: int, text: str, buttons: Optional[list] = None) -> bool:
        """Edits an existing Telegram message in-place (HTML) and updates or removes buttons.
        True means shown, or queued: while Telegram's flood control has the bot wait (a 429 on an edit holds the
        chat's edits for the time it gave), or while a held edit of this message is being sent, the content is kept
        and sent once the wait is over; a later edit of the same message replaces it (the latest wins). A queued
        edit that then fails is logged, and one whose message is gone marks it missing (_missing_messages), so the
        next edit of it returns False and its caller posts a fresh one."""
        if not self.is_configured() or not message_id:
            return False
        content = (text, copy.deepcopy(buttons))  # a caller may change its list after a queued edit returns
        self.__dict__.setdefault("_content", {})[message_id] = content   # a re-post sends this copy (UI-01f)
        self._mark_counting(message_id, text)
        with self._edit_lock:  # the order edits were asked for: the latest content wins a hold
            self._edit_seq += 1
            seq = self._edit_seq
        if message_id in self._missing_messages:
            # Known to be gone (deleted by a clear, a redraw or an install): no request, no log line; the
            # caller posts a fresh one as it would after a failed edit (six attempts on one cleared panel in
            # the three seconds after a restart, 2026-10-24).
            return False
        if self._is_media(message_id):
            # A photo/audio/video message has a caption, not text: its caption is edited instead (a panel never
            # gets here: show_navigation_panel replaces a media panel with a text message).
            return self._edit_caption_instead(message_id, text, buttons)
        if self._edit_is_held(message_id, seq, content):
            return True  # sent when the wait is over, unless a later edit replaces it first
        # False when Telegram answers that the message is media: it is recorded so, the caller replaces it now
        # (a panel) or sends a fresh card, and a later edit of it goes to its caption.
        return self._deliver_edit(message_id, seq, content)

    def _deliver_edit(self, message_id: int, seq: int, content: tuple, hold_on_limit: bool = True) -> bool:
        """One editMessageText request for `content`. A 429 holds the edits (hold_on_limit) and counts as queued;
        on shutdown (drain) it is only logged."""
        with self._edit_lock:
            if seq < self._edit_delivered.get(message_id, -1):
                return True  # a newer content is already shown: this older one is not sent after it
        text, buttons = content
        from .telegram_pages import panel as paginate
        text, buttons = paginate(self.db, _MASKER.mask(text), buttons)
        markup = {"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []}
        data = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": safe_html_truncate(text, max_chars=4000),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "reply_markup": json.dumps(markup),
        }

        def shown() -> bool:
            with self._edit_lock:
                self._edit_delivered[message_id] = max(seq, self._edit_delivered.get(message_id, -1))
            return True

        def limited(e: TelegramRateLimited) -> bool:
            if hold_on_limit and not self._edit_closed:
                self._hold_edits(message_id, e.retry_after, seq, content)
                return True
            log.warning("Could not edit message text %s before shutting down: %s", message_id, e)
            return False
        try:
            self._api("editMessageText", data=data)
            return shown()
        except TelegramRateLimited as e:
            return limited(e)
        except Exception as e:
            err_msg = str(e).lower()
            if "message to edit not found" in err_msg:
                # Expected after a card or panel was cleared: every caller then posts a fresh one. Noted,
                # not a warning (28 of them on production in three days, all handled).
                self._missing_messages.add(message_id)
                log.info("Message %s is no longer in the chat; a fresh one replaces it.", message_id)
                return False
            if "message is not modified" in err_msg:
                return shown()
            if "there is no text in the message to edit" in err_msg:
                # A media message (photo/audio/video with a caption): recorded as such, so the caller edits its
                # caption or replaces it (edit_message_text, show_navigation_panel), and no later edit asks again.
                self._record_kind(message_id, media=True)
                log.info("Message %s is media, not text; its caption is edited or it is replaced.", message_id)
                return False
            if "can't parse entities" in err_msg:
                log.warning("Telegram parse error in edit_message_text (%s); falling back to plain text", e)
                try:
                    fallback_data = dict(data)
                    fallback_data.pop("parse_mode", None)
                    fallback_data["text"] = re.sub(r"<[^>]+>", "", text)[:4000]
                    self._api("editMessageText", data=fallback_data)
                    return shown()
                except TelegramRateLimited as e2:
                    return limited(e2)
                except Exception as e2:
                    log.warning("Fallback plain text edit also failed: %s", e2)
            log.warning("Could not edit message text %s: %s", message_id, e)
            return False

    def _keep_latest_edit(self, message_id: int, seq: int, content: tuple) -> None:
        """Keeps `content` as the message's edit to send when its hold ends, unless a later one is kept already
        (called under _edit_lock)."""
        if seq >= self._edit_pending.get(message_id, (-1, None))[0] and seq >= self._edit_delivered.get(message_id, -1):
            self._edit_pending[message_id] = (seq, content)

    def _held_edit_timer(self, message_id: int, until: float):
        """A new timer to send the message's held edit at `until`, or None when one is set or a send of it is in
        flight (which sends what is queued after it). Called under _edit_lock; the caller starts it outside."""
        if message_id in self._edit_timers or message_id in self._edit_sending or self._edit_closed:
            return None
        timer = self._edit_timer(max(0.0, until - time.monotonic()), self._send_held_edit, args=(message_id,))
        timer.daemon = True
        self._edit_timers[message_id] = timer
        return timer

    def _edit_is_held(self, message_id: int, seq: int, content: tuple) -> bool:
        """Whether this edit must wait: Telegram asked the bot to wait before editing in this chat (its flood control
        is usually per chat), or a held edit of this message is being sent. If so the content is kept to be sent
        then (the latest asked for wins), and no request is made now."""
        with self._edit_lock:
            until = max(self._edit_held_until.get(message_id, 0.0), self._edit_chat_held_until)
            if self._edit_closed or (time.monotonic() >= until and message_id not in self._edit_sending):
                return False
            self._keep_latest_edit(message_id, seq, content)
            timer = self._held_edit_timer(message_id, until)
        if timer is not None:
            timer.start()
        return True

    def _hold_edits(self, message_id: int, seconds: float, seq: int, content: tuple) -> None:
        """Telegram's flood control refused an edit: the chat's edits are held for the time it gave (they are not
        tried again meanwhile, which only lengthens the wait), and each message's latest content is sent once then."""
        with self._edit_lock:
            until = time.monotonic() + max(0.0, seconds) + 0.5
            until = self._edit_held_until[message_id] = max(until, self._edit_held_until.get(message_id, 0.0))
            self._edit_chat_held_until = max(until, self._edit_chat_held_until)
            self._keep_latest_edit(message_id, seq, content)
            timer = self._held_edit_timer(message_id, until)
        if timer is not None:
            timer.start()
            # A long wait freezes the progress and review cards: said as a warning, not a note.
            log.log(logging.WARNING if seconds > 60 else logging.INFO,
                    "Telegram asked the bot to wait %.0f s before editing message %s; its latest content is sent "
                    "then.", seconds, message_id)

    _HOLD_SLACK = 0.05  # a timer may wake a hair before its time; that is the time

    def _take_held_edit(self, message_id: int):
        """Under _edit_lock: the message's queued edit to send now (it is then marked as being sent), or None. While
        the message's or the chat's hold is still on (a longer hold came after this timer was set, or the send was
        refused again) nothing is sent, and a timer for the end of the hold is returned for the caller to start."""
        self._edit_sending.discard(message_id)
        until = max(self._edit_held_until.get(message_id, 0.0), self._edit_chat_held_until)
        if time.monotonic() < until - self._HOLD_SLACK:
            timer = self._held_edit_timer(message_id, until) if message_id in self._edit_pending else None
            return None, timer
        self._edit_held_until.pop(message_id, None)
        pending = self._edit_pending.pop(message_id, None)
        if pending is not None:
            self._edit_sending.add(message_id)
        return pending, None

    def _send_held_edit(self, message_id: int) -> None:
        """The hold is over: the latest content asked for meanwhile is sent, once. Edits made while it is in flight
        are queued and sent after it, so an older content never lands last; a send refused again waits out its new
        hold on a timer (never a loop of requests)."""
        with self._edit_lock:
            self._edit_timers.pop(message_id, None)
            if message_id in self._edit_sending:
                return
            pending, timer = self._take_held_edit(message_id)
        try:
            while pending is not None:
                try:
                    self._deliver_edit(message_id, *pending)
                except Exception as e:  # the timer thread never raises; the next edit tries again
                    log.warning("Could not send the held edit of message %s: %s", message_id, _MASKER.mask(str(e)))
                with self._edit_lock:  # the sending mark is cleared in the same step: nothing is left queued unsent
                    pending, timer = self._take_held_edit(message_id)
        except BaseException:
            with self._edit_lock:
                self._edit_sending.discard(message_id)
            raise
        if timer is not None:
            timer.start()

    def _wait_for_held_sends(self, seconds: float = 5.0) -> None:
        """Waits (at most `seconds`) for held edits already on their way, so a drain sends what queued behind them."""
        deadline = time.monotonic() + seconds
        while True:
            with self._edit_lock:
                if not self._edit_sending:
                    return
            if time.monotonic() >= deadline:
                break
            threading.Event().wait(0.02)
        log.warning("A held Telegram edit was still being sent after %.0f s; draining without it.", seconds)

    FLUSH_WAIT_SECONDS = 5.0  # a drain waits out a chat hold this short before its final sends

    def _flush_held_edits(self, final: bool = False, wait_limit: float = FLUSH_WAIT_SECONDS) -> None:
        """Shutdown: the timers are cancelled and every held edit is sent now, once (a final card is not lost, and
        no timer reaches the network after the bot is done). `final`: no more edits are held or timed after it.
        A chat hold with at most FLUSH_WAIT_SECONDS left is waited out first; a longer one is not (said in the log),
        and its edits are tried anyway."""
        with self._edit_lock:
            if final:
                self._edit_closed = True
            left = self._edit_chat_held_until - time.monotonic() if self._edit_pending else 0.0
        if 0 < left <= min(self.FLUSH_WAIT_SECONDS, wait_limit):
            threading.Event().wait(left)
        elif left > 0:
            log.warning("Telegram holds edits for %.0f s more; sending the held edits before stopping anyway.", left)
        with self._edit_lock:
            timers = list(self._edit_timers.values())
            self._edit_timers.clear()
            pending = dict(self._edit_pending)
            self._edit_pending.clear()
            self._edit_held_until.clear()
            self._edit_chat_held_until = 0.0
        for timer in timers:
            timer.cancel()
        for message_id, (seq, content) in pending.items():
            try:
                self._deliver_edit(message_id, seq, content, hold_on_limit=False)
            except Exception as e:
                log.warning("Could not send the held edit of message %s: %s", message_id, _MASKER.mask(str(e)))

    NUDGE_SECONDS = 60

    def nudge(self, text: str) -> bool:
        """A short message that buzzes the phone and leaves after NUDGE_SECONDS (operator, 2026-10-25: a nudge
        when the dashboard updates, not a new message that stays). Telegram does not notify an edit, so the
        nudge is the only way to buzz; the panel carries the substance. A cleanup sweep removes it if the
        timer could not (it is recorded as a transient notice, expiring in two minutes)."""
        if not self.is_configured():
            return False
        try:
            nudge_data = {"chat_id": self.chat_id, "text": _MASKER.mask(text), "parse_mode": "HTML"}
            if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS") and "needs" in config.TELEGRAM_TOPICS:
                nudge_data["message_thread_id"] = config.TELEGRAM_TOPICS["needs"]
            res = self._api("sendMessage", data=nudge_data)
        except Exception as e:
            log.warning("Nudge not sent: %s", _MASKER.mask(str(e)))
            return False
        msg_id = res.get("message_id") if isinstance(res, dict) else None
        if not msg_id:
            return False
        if hasattr(self.db, "record_alert"):
            self.db.record_alert(text, message_id=msg_id, kind="transient",
                                 expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat())
        timer = threading.Timer(self.NUDGE_SECONDS, lambda: self.delete_message(msg_id))
        timer.daemon = True
        timer.start()
        return True

    def _is_dashboard(self, message_id) -> bool:
        stored = self.db.get_setting("telegram_dashboard_id") if hasattr(self.db, "get_setting") else None
        return bool(stored and stored.isdigit()) and self._current_panel(int(stored)) == self._current_panel(message_id)

    def _settle_rejected_card(self, run_key: str, card_id: int, now: bool = False) -> None:
        """A rejected run's review leaves the chat (operator, 2026-10-25: decided cards stayed): its video and
        frames at once, the reason card when details are added (now) or after REASON_CARD_MINUTES. The run,
        its decision and reason stay in History. A regenerated run's card is replaced by the new review."""
        try:
            run_id = self.db.resolve_run(run_key)
            if (self.db.get_approval(run_id) or {}).get("decision") != "rejected":
                return
            from .telegram_state import get, put
            album = [m for m in (get(self.db, "review-messages", run_id) or []) if isinstance(m, int) and m != card_id]
            if album:
                self.delete_messages(album)
            put(self.db, "review-messages", run_id, [card_id])
            if now:
                self.delete_message(card_id)
            elif hasattr(self.db, "record_alert"):
                self.db.record_alert("reason card", message_id=card_id, kind="transient", expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=REASON_CARD_MINUTES)).isoformat())
        except Exception as e:  # tidying must never break the reply
            log.debug("Rejected review not tidied: %s", e)

    def run_card_message(self, run_id: str) -> Optional[int]:
        """The message of the run's latest failure card, from its card ledger, or None."""
        from .card_ledger import cards
        from .config import OUTPUT_DIR
        for card in reversed(cards(OUTPUT_DIR / run_id)):
            if card.get("kind") == "failure" and card.get("message_id"):
                return int(card["message_id"])
        return None

    def cleanup_transient_messages(self) -> int:
        """Deletes expired transient alerts from the Telegram chat and updates the DB."""
        if not hasattr(self.db, "get_expired_alerts"):
            return 0
        expired = self.db.get_expired_alerts()
        deleted_count = 0
        for alert in expired:
            msg_id = alert.get("message_id")
            if msg_id:
                if self.delete_message(msg_id):
                    deleted_count += 1
            self.db.mark_alert_purged(alert["id"])
        if deleted_count:
            log.info("Cleaned up %d transient alert(s) from Telegram chat.", deleted_count)
        return deleted_count

    def dismiss_approval_buttons(self, run_id: str) -> bool:
        """Strips inline action buttons from a run's review card in Telegram."""
        if not self.is_configured():
            return False
        if clean_chat():
            return self.review.refresh()   # the review leaves the pager; the panel goes with the last one (UI-01e)
        appr = self.db.get_approval(run_id) if hasattr(self.db, "get_approval") else None
        if not appr or not appr.get("telegram_message_id"):
            return False
        try:
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": appr["telegram_message_id"],
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })
            return True
        except Exception as e:
            log.debug("Could not dismiss approval buttons for %s: %s", run_id, e)
            return False

    def timeout_notice(self, run_id: str) -> None:
        """Says a review closed without a decision, with Resend: from the wait, and from the stale sweep (which
        closed reviews silently: their buttons just went, 2026-10-25)."""
        if clean_chat():
            # The run's own Needs-you item (UI-01e, design §6.5, mock-up 3), said through notifications.say under the
            # run's failure key fail:<run> (UI-01a's §10.2 rule: one item per run, replacing an earlier one), with
            # Resend for review · Details; it resolves itself once resent (notice_conditions.sync).
            from . import notifications as notes
            key = self.db.run_key(run_id)
            notes.say(self.notify, f"⏰ <b>Review timed out</b> · {html.escape(self.db.run_label(run_id))}\n\n"
                      "No decision came within the review window, so nothing was posted. The render is kept: "
                      "resend it for review, or reject it later from 🎬 Reviews.",
                      group="needs", key=f"fail:{run_id}", run=run_id, replace=True,
                      subject=self.db.run_label(run_id),
                      actions=[{"text": "Resend for review", "callback_data": f"resend:{key}"},
                               {"text": "Details", "callback_data": f"chist:{key}"}])
            return
        notice = self.notify(f"⌛ No decision on {html.escape(self.db.run_label(run_id))}; nothing was posted. "
                             "The render is kept: resend it here, or later from Reviews on the dashboard.",
                             buttons=[{"text": "📨 Resend for review", "callback_data": f"resend:{self.db.run_key(run_id)}"}],
                             group="needs")
        if type(notice) is int:  # removed when the review is sent again, from wherever
            from .telegram_state import put
            put(self.db, "timeout-notice", run_id, notice)

    def cleanup_stale_approvals(self, max_age_hours: int = 26) -> int:  # past the longest review window (24 h)
        """Sweeps any pending approvals older than max_age_hours, marks them timed out, and strips buttons."""
        if not hasattr(self.db, "list_pending_approvals"):
            return 0
        cleaned = 0
        try:
            pending = self.db.list_pending_approvals()
            now = datetime.now(timezone.utc)
            for item in pending:
                created_str = item.get("created_at")
                if not created_str:
                    continue
                created = datetime.fromisoformat(created_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600.0
                if age_hours >= max_age_hours:
                    r_id = item["run_id"]
                    if self.db.update_approval_decision(r_id, "timeout", decided_by="stale_sweeper"):
                        self.dismiss_approval_buttons(r_id)
                        cleaned += 1
                        try:  # one notice failing must not stop the sweep of the others
                            self.timeout_notice(r_id)
                        except Exception as exc:
                            log.warning("Could not post the timeout notice for %s: %s", r_id, _MASKER.mask(str(exc)))
            if cleaned:
                log.info("Cleaned up %d stale/timed-out approval(s) from Telegram.", cleaned)
        except Exception as e:
            log.debug("Error in cleanup_stale_approvals: %s", e)
        return cleaned

    def reap_decided_reviews(self, max_hours: float = 6.0) -> int:
        """Reaps decided review cards from topic Reviews (4908) after max_hours.
        Ensures the review messages are deleted from the chat while the full record
        and artifacts remain preserved in SQLite and local disk. Also sweeps expired cards."""
        if not hasattr(self.db, "list_telegram_cards"):
            return 0
        now = time.time()
        if hasattr(self.db, "sweep_expired_telegram_cards"):
            try:
                self.db.sweep_expired_telegram_cards()   # its own age rule (it was given the time as max_age_hours)
            except Exception as e:
                log.debug("sweep_expired_telegram_cards failed: %s", e)

        cutoff = now - (max_hours * 3600.0)
        reaped = 0
        try:
            cards = self.db.list_telegram_cards(topic_id=config.TELEGRAM_TOPICS.get("reviews"))
            for card in cards:
                if card.get("card_kind") != "review_pair":
                    continue
                payload = card.get("payload") or {}
                status = payload.get("status")
                updated_at = card.get("updated_at")
                ts = 0.0
                if isinstance(updated_at, (int, float)):
                    ts = float(updated_at)
                elif isinstance(updated_at, str):
                    try:
                        ts = datetime.fromisoformat(updated_at).timestamp()
                    except Exception:
                        try:
                            ts = float(updated_at)
                        except Exception:
                            ts = 0.0
                if status in ("approved", "rejected") and ts < cutoff:   # an archived card is gone already
                    mids = [m for m in (payload.get("video_mid"), payload.get("card_mid"), card.get("message_id")) if isinstance(m, int)]
                    if mids:
                        self.delete_messages(list(set(mids)))
                    payload["status"] = "archived"
                    self.db.put_telegram_card(
                        card_id=card["card_id"],
                        topic_id=card["topic_id"],
                        message_id=card["message_id"],
                        card_kind=card["card_kind"],
                        payload=payload,
                        expires_at=card.get("expires_at"),
                    )
                    reaped += 1
            if reaped:
                log.info("Reaped %d decided review card(s) older than %.1f hours.", reaped, max_hours)
        except Exception as e:
            log.warning("reap_decided_reviews encountered error: %s", e)
        return reaped

    def edit_message_caption(self, message_id: int, caption: str, buttons: Optional[list] = None) -> bool:
        """Edits the caption and optional buttons of a media message (photo/audio/video)."""
        if not self.is_configured() or not message_id:
            return False
        data = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "caption": safe_html_truncate(_MASKER.mask(caption), 1024),
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []}),
        }
        try:
            self._api("editMessageCaption", data=data)
            return True
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return True
            log.warning("Could not edit message caption %s: %s", message_id, e)
            return False

    def edit_message_any(self, message_id: int, text: str, is_media: bool = False, buttons: Optional[list] = None) -> bool:
        """Edits either a media caption or text message, with automatic fallback."""
        if not self.is_configured() or not message_id:
            return False
        if is_media:
            if self.edit_message_caption(message_id, text, buttons):
                return True
            return self.edit_message_text(message_id, text, buttons)
        else:
            if self.edit_message_text(message_id, text, buttons):
                return True
            return self.edit_message_caption(message_id, text, buttons)

    def send_audio(self, path: Path, caption: str, buttons: Optional[list] = None, group: str = "studio") -> bool:
        """Sends a playable audio file with an HTML caption and optional inline buttons."""
        panel = _PANEL.get()
        if panel and panel[0] is self:
            if len(panel) > 4 and self._panel_revisions.get(self._current_panel(panel[1]), 0) != panel[4]:
                panel[3] = True
                return False
            panel[3] = True
            with open(path, "rb") as audio:
                self._api("editMessageMedia", data={"chat_id": self.chat_id, "message_id": panel[1],
                    "media": json.dumps({"type": "audio", "media": "attach://audio", "caption": safe_html_truncate(caption, 1024), "parse_mode": "HTML"}),
                    "reply_markup": json.dumps({"inline_keyboard": navigation_buttons(buttons)})}, files={"audio": audio})
            self._record_kind(panel[1], media=True)
            return True
        if not self.is_configured():
            return False
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS"):
            data["message_thread_id"] = config.TELEGRAM_TOPICS.get(group, config.TELEGRAM_TOPICS["studio"])
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": format_responsive_keyboard(buttons)})
        try:
            with open(path, "rb") as f:
                res = self._api("sendAudio", timeout=120, data=data, files={"audio": f})
            msg_id = res.get("message_id") if isinstance(res, dict) else None
            return msg_id if msg_id else True
        except Exception as e:
            log.warning("Could not send audio: %s", e)
            return False

    def flip_cover(self, message_id: int, run_name: str, variant_letter: str, back: Optional[dict] = None) -> bool:
        """Flips the cover variant in-place on an existing Telegram photo message using editMessageMedia."""
        var_letter = variant_letter.lower()
        from .config import OUTPUT_DIR
        if Path(run_name).name != run_name or run_name in (".", "..") or var_letter not in ("a", "b", "c"):
            return False
        r_dir = (OUTPUT_DIR / run_name).resolve()
        if r_dir.parent != OUTPUT_DIR.resolve():
            return False
        if not r_dir.is_dir():
            return False
        var_file = (r_dir / f"thumbnail_variant_{var_letter}_blueprint.jpg" if var_letter == "a"
                    else r_dir / f"thumbnail_variant_{var_letter}_intrigue.jpg" if var_letter == "b"
                    else r_dir / f"thumbnail_variant_{var_letter}_minimal.jpg")
        if not var_file.exists():
            return False

        styles = {"a": "Forensic Blueprint", "b": "Discovery Intrigue", "c": "Cinematic Minimal"}
        style_name = styles.get(var_letter, var_letter.upper())
        # ✓ the cover the video has, 👁 the one shown (a flip ticked the preview as if chosen: 2026-10-25);
        # Lock only while the video waits for its review, as on the Covers screen.
        from .control import chosen_cover
        chosen = chosen_cover(r_dir)
        mark = lambda v: "✓" if v == chosen else ("👁" if v == var_letter else "📌")  # noqa: E731
        run_rec = self.db.get_run(run_name) or {}
        reviewable = run_rec.get("status") == "pending_approval" and (self.db.get_approval(run_name) or {}).get("decision") == "pending"
        buttons = [
            [
                {"text": f"{mark('a')} A: Blueprint", "callback_data": f"cov:{run_name}:a"},
                {"text": f"{mark('b')} B: Intrigue", "callback_data": f"cov:{run_name}:b"},
            ],
            [
                {"text": f"{mark('c')} C: Minimal", "callback_data": f"cov:{run_name}:c"},
            ],
            *([[{"text": f"🔒 Lock {var_letter.upper()}", "callback_data": f"covlock:{run_name}:{var_letter}"}]]
              if reviewable else []),
            [back or DONE_BUTTON],  # keep the way back the panel was opened with (Close or Dashboard)
        ]
        caption = (
            f"🎨 <b>Thumbnail Cover Studio</b> • <code>{html.escape(run_name)}</code>\n\n"
            f"Previewing: <b>Variant {var_letter.upper()} ({style_name})</b>\n"
            f"Tap A, B, or C to flip preview in-place, or Lock to finalize:"
        )
        cache_key = f"cover:{run_name}:{var_letter}"
        cached_id = self.db.get_cached_telegram_media(cache_key)
        res = self.edit_message_media(
            message_id=message_id,
            media_path_or_file_id=cached_id or var_file,
            caption=caption,
            buttons=navigation_buttons(buttons),
            cache_key=cache_key
        )
        return bool(res)

    def send_photo(self, path: Path, caption: str, buttons: Optional[list] = None, cache_key: Optional[str] = None, group: str = "studio") -> Any:
        """Sends an image with an HTML caption (max 1,024 chars) and optional inline buttons."""
        panel = _PANEL.get()
        if panel and panel[0] is self:
            if len(panel) > 4 and self._panel_revisions.get(self._current_panel(panel[1]), 0) != panel[4]:
                panel[3] = True
                return False
            panel[3] = True
            if not self.edit_message_media(panel[1], path, caption, navigation_buttons(buttons), cache_key):
                raise RuntimeError("Could not update the media panel")
            self._record_kind(panel[1], media=True)
            return True
        if not self.is_configured():
            return False
        cached_id = self.db.get_cached_telegram_media(cache_key) if cache_key else None
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if config.TELEGRAM_CARDS_V2 and hasattr(config, "TELEGRAM_TOPICS"):
            data["message_thread_id"] = config.TELEGRAM_TOPICS.get(group, config.TELEGRAM_TOPICS["studio"])
        if buttons:
            markup = {"inline_keyboard": format_responsive_keyboard(buttons)}
            data["reply_markup"] = json.dumps(markup)
        try:
            if cached_id:
                data["photo"] = cached_id
                res = self._api("sendPhoto", data=data)
            else:
                with open(path, "rb") as f:
                    res = self._api("sendPhoto", data=data, files={"photo": f})
                if isinstance(res, dict) and res.get("photo") and cache_key:
                    file_id = res["photo"][-1].get("file_id")
                    if file_id:
                        self.db.set_cached_telegram_media(cache_key, file_id, "photo")
            msg_id = res.get("message_id") if isinstance(res, dict) else None
            return msg_id if msg_id else True
        except Exception as e:
            log.warning("Could not send photo: %s", e)
            return False

    # ------------------------------------------------- shared update poller
    # Telegram allows one getUpdates consumer per bot. In daemon mode a single background
    # thread calls poll_once() for both approval clicks and commands; approval waits then
    # only watch the database (background_polling = True).
    background_polling = False
    _offset: Optional[int] = None

    @staticmethod
    def warm_up() -> threading.Thread:
        """Imports the modules behind the slower panels in the background, so the first tap on Ideas or
        Discoveries does not pay a ~4 s import (the LLM SDKs and the UNESCO list)."""
        def load():
            import importlib
            for name in ("src.topic_proposer", "src.unesco", "src.music", "src.accounts"):
                try:
                    importlib.import_module(name)
                except Exception as exc:  # a failed warm-up only costs the first tap its import time
                    log.debug("Warm-up import of %s failed: %s", name, exc)
        thread = threading.Thread(target=load, name="telegram-warm-up", daemon=True)
        thread.start()
        return thread

    def _submit_update(self, update, handler):
        def work():
            self._process_update(update, handler)
        self.jobs.submit(self._background(), "Receiving media", work, lambda job, result: False,
                         resource=f"telegram-upload:{update.get('update_id')}")

    DRAIN_SECONDS = 10.0  # the whole drain: its passes, its waits and the final sends

    def drain(self) -> None:
        """Waits for queued navigation and background commands to finish (shutdown, tests). Navigation
        work can queue more navigation, so the pools are drained until both are empty, within DRAIN_SECONDS
        in all; what is left then is said in the log."""
        deadline = time.monotonic() + self.DRAIN_SECONDS

        def left() -> float:
            return max(0.0, deadline - time.monotonic())
        joined: list = []  # every pool drained, to say which are still busy when the time is up
        while True:
            self._wait_for_held_sends(min(5.0, left()))  # a held edit already on its way first, then what queued
            with self._panel_lock:
                pools = [p for p in (self._navigation_pool, self._workers, self._cleanup) if p is not None]
                self._navigation_pool = self._workers = self._cleanup = None
            last = not pools or not left()
            # Held edits go now; on the last pass nothing is held or timed after it.
            self._flush_held_edits(final=last, wait_limit=left())
            if last:
                self._wait_for_held_sends(min(5.0, left()))  # a timer that took its edit just before the flush
                with self._edit_lock:
                    unsent = len(self._edit_pending) + len(self._edit_sending)
                busy = [p for p in joined + pools if any(t.is_alive() for t in getattr(p, "_threads", ()))]
                if unsent or busy:
                    log.warning("Telegram drain stopped after %.0f s with %d edit(s) unsent and %d pool(s) still "
                                "busy.", self.DRAIN_SECONDS, unsent, len(busy))
                return
            joined += pools
            for pool in pools:
                pool.shutdown(wait=False)
                for thread in list(getattr(pool, "_threads", ())):
                    thread.join(left())

    def _queue_navigation(self, work):
        if not self._dispatch_slots.acquire(blocking=False):
            log.warning("Telegram navigation queue is full; reopen the current command")
            return False
        def guarded():
            try:
                work()
            except Exception as exc:
                log.warning("Telegram navigation failed: %s", _MASKER.mask(str(exc)))
            finally:
                self._dispatch_slots.release()
        import concurrent.futures
        with self._panel_lock:
            if self._navigation_pool is None:
                self._navigation_pool = concurrent.futures.ThreadPoolExecutor(2, thread_name_prefix="tg-navigation")
        self._navigation_pool.submit(guarded)
        return True

    LAST_POLL_KEY = "telegram_last_poll"

    def _offline_gap(self) -> bool:
        """Whether this batch follows a time the bot was not polling for longer than STALE_COMMAND_SECONDS
        (the last poll is kept in the database, so a restart after an outage knows it was one)."""
        now = time.time()
        try:
            last = float(self.db.get_setting(self.LAST_POLL_KEY) or 0)
        except (TypeError, ValueError, AttributeError):
            last = 0.0
        if now - getattr(self, "_last_poll_saved", 0.0) > 60 and hasattr(self.db, "set_setting"):
            self.db.set_setting(self.LAST_POLL_KEY, f"{now:.0f}")
            self._last_poll_saved = now
        return bool(last) and now - last > STALE_COMMAND_SECONDS

    @staticmethod
    def _stale_tap(data: str) -> bool:
        """A tap that does something (starts work, posts, changes the queue) rather than show a screen or
        decide a review; such taps are not replayed after an outage."""
        from .telegram_ui import replayable
        action = (data or "").partition(":")[0]
        return not (action in ACTIONS or replayable(data) or action in {"done", "resend", "jobs", "rsn"})

    def poll_once(self, command_handler: Optional[Callable[[str, str], str]] = None, timeout: int = 25) -> None:
        """One long-poll: applies approval clicks for any run and answers authorized /commands."""
        if time.monotonic() - self._last_maintenance > 300:
            self._last_maintenance = time.monotonic()
            self._queue_navigation(lambda: (self.cleanup_transient_messages(), self.cleanup_stale_approvals(), self.reap_decided_reviews()))
        with self._panel_lock:
            if not self.jobs.active:
                for cache in (self._panel_aliases, self._panel_media, self._panel_revisions,
                              self._last_progress_edit, self._last_progress_step, self._progress_start_time):
                    while len(cache) > 512:
                        cache.pop(next(iter(cache)))
        # Updates that arrived while the bot was down are processed, not skipped: an Approve tap
        # during a restart must not be lost (decisions are first-wins, so a replay is harmless).
        # Only commands older than STALE_COMMAND_SECONDS are dropped, so a day-old /run doesn't fire.
        from .review_panel import REVIEW_ACTIONS
        from .telegram_layout import NOTE_ACTIONS, PROGRESS_ACTIONS
        clean = config.TELEGRAM_CLEAN_CHAT and self.is_configured()
        if clean:
            self.layout.start()   # the long-running bot renders the layout (telegram_layout)
        data = {"timeout": timeout, "allowed_updates": json.dumps(["callback_query", "message"])}
        if self._offset is not None:
            data["offset"] = self._offset
        updates = self._api("getUpdates", timeout=timeout + 10, data=data)
        from .telegram_state import admit, decode
        offline = self._offline_gap()  # taps queued while the bot was down: decisions and screens only
        for update in updates:
            try:
                self._offset = max(self._offset or 0, update["update_id"] + 1)
                if not admit(self.db, self.bot_token.split(":")[0], update["update_id"]):
                    continue
                self._offset = max(self._offset or 0, update["update_id"] + 1)
                cq = update.get("callback_query")
                if clean:   # UI-01f: idle moments, the gap sweep's bound and the messages being read
                    self._note_update(update)
                if cq:
                    cq["data"] = decode(self.db, cq.get("data", ""))
                    if offline and self._stale_tap(cq["data"]):
                        # A Render, Fetch or Research tap made while the bot was down for a day must not fire
                        # when it comes back (review of 2026-10-25); a decision (first wins) or a screen still does.
                        log.info("Skipping a %s tap made while the bot was offline.", cq["data"].partition(":")[0])
                        self._answer_callback(cq["id"], "Made while the bot was offline: tap it again if you still want it")
                        continue
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from",{}).get("id"), message.get("chat",{}).get("id")) and message.get("message_id"):
                        with self._panel_lock:
                            mid = self._current_panel(message["message_id"])
                            self._panel_revisions[mid] = self._panel_revisions.get(mid, 0) + 1
                            cq["_revision"] = self._panel_revisions[mid]
                            if cq.get("data") and not cq["data"].startswith(("jobcancel:", "jobs:")):
                                self._panel_origin[mid] = self._panel_screen.get(mid)  # where this button was
                                self._panel_screen[mid] = cq["data"]
                                self._dashboard_touched = time.time()
                            if cq.get("data"):
                                from .telegram_ui import step
                                self._panel_trail[mid] = step(self._panel_trail.get(mid, []), cq["data"])
                            self._record_kind(mid, media=bool(message.get("photo") or message.get("audio") or message.get("video") or message.get("document") or message.get("animation")))
                msg = update.get("message") or {}
                action = (cq or {}).get("data", "").partition(":")[0]
                if clean and msg.get("pinned_message") and str((msg.get("chat") or {}).get("id")) == str(self.chat_id):
                    # The service line a pin leaves ("… pinned 🔔 …") goes at once (design §5.2).
                    self._delete_soon(msg.get("message_id"))
                    continue
                if clean and cq and action in PROGRESS_ACTIONS:
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from", {}).get("id"), message.get("chat", {}).get("id")):
                        self._queue_navigation(lambda tap=cq: self._progress_tap(tap))
                    else:
                        self._answer_callback(cq["id"], "Not authorized.")
                    continue
                if clean and cq and action in REVIEW_ACTIONS:
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from", {}).get("id"), message.get("chat", {}).get("id")):
                        self._queue_navigation(lambda tap=cq: self.review.tap(tap, command_handler))
                    else:
                        self._answer_callback(cq["id"], "Not authorized.")
                    continue
                if clean and cq and action in NOTE_ACTIONS:
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from", {}).get("id"), message.get("chat", {}).get("id")):
                        self._queue_navigation(lambda tap=cq: self._notifications_tap(tap, command_handler))
                    else:
                        self._answer_callback(cq["id"], "Not authorized.")
                    continue
                media_upload = msg.get("caption") and any(msg.get(k) for k in ("photo","document","video","audio","voice"))
                if (media_upload and self._authorized(msg.get("from",{}).get("id"), msg.get("chat",{}).get("id"))) or action in {"cov","covlock"}:
                    if cq:
                        self._answer_callback(cq["id"], "Working")
                    self._submit_update(update, command_handler)
                elif cq and action not in ACTIONS:
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from",{}).get("id"), message.get("chat",{}).get("id")):
                        if action == "jobcancel":  # cancelled here, at once; the Jobs screen then says so
                            cq["_cancelled"] = self.jobs.cancel(cq["data"].partition(":")[2])
                            cq["data"] = "jobs:"
                        if action not in RETIRED_ACTIONS:  # those answer with their own explanation
                            self._answer_callback(cq["id"], "Request received")
                    self._queue_navigation(lambda item=update: self._process_update(item, command_handler))
                elif (msg.get("text") or "").split(" ",1)[0].split("@")[0].lower() in {"/start","/dashboard","/clear","/jobs"}:
                    self._queue_navigation(lambda item=update: self._process_update(item, command_handler))
                else:
                    self._process_update(update, command_handler)
            except Exception as exc:
                log.error("Telegram update %s interrupted: %s", update.get("update_id"), _MASKER.mask(str(exc)))
                # Keep processing the rest of the batch; admitted actions are never replayed.

    def _process_update(self, update, command_handler):
        sent = (update.get('message') or {}).get('date')
        if sent and time.time() - sent > STALE_COMMAND_SECONDS:
            log.info('Skipping a message sent %d min ago while the bot was offline.', (time.time() - sent) // 60)
            self._leave_unanswered(update.get('message') or {}, stale=True)   # not replayed, not left, and said
            return
        cq = update.get('callback_query')
        if cq:
            user_id = str(cq.get('from', {}).get('id', ''))
            chat_id = str((cq.get('message') or {}).get('chat', {}).get('id', ''))
            username = str(cq.get('from', {}).get('username', '') or cq.get('from', {}).get('first_name', ''))
            data_str = cq.get('data', '')
            action, _, target = data_str.partition(':')
            ok = self._authorized(user_id, chat_id)
            try:
                self.db.record_audit_event(user_id=user_id, username=username, chat_id=chat_id, event_type='callback_query', payload=data_str, status='allowed' if ok else 'denied')
            except Exception as ea:
                log.debug('Could not record callback audit: %s', ea)
            if not ok:
                self._answer_callback(cq['id'], 'Not authorized.')
                return
            from .review_panel import REVIEW_ACTIONS
            if action in REVIEW_ACTIONS and clean_chat():
                self.review.tap(cq, command_handler)
                return
            if action == 'cprog' and self.bring_progress_down():
                self._answer_callback(cq['id'], 'Progress card moved to the bottom')
                return
            if action == 'close':
                msg = cq.get('message') or {}
                self._answer_callback(cq['id'], 'Closed')
                if msg.get('message_id'):  # the card becomes the dashboard again: one panel, never an empty chat
                    from .control import dashboard_text_and_buttons
                    from .config import ASSETS_DIR
                    dash_text, dash_buttons = dashboard_text_and_buttons(self.db, getattr(self, 'next_run_func', lambda: None)(), assets_dir=ASSETS_DIR)
                    is_media = bool(msg.get('photo') or msg.get('audio') or msg.get('video') or msg.get('document'))
                    if self.show_navigation_panel(msg['message_id'], dash_text, dash_buttons, is_media) and not clean_chat():
                        self.db.set_setting('telegram_dashboard_id', str(self._current_panel(msg['message_id'])))
                return
            if action == 'ccov' and target:
                # From a review card: the cover studio opens in the one panel, never over the review card.
                self._answer_callback(cq['id'], 'Opening covers')
                self._run_command(command_handler, 'covers', target, self._typed_panel())
                return
            if action in RETIRED_ACTIONS:
                # Buttons on cards sent by earlier releases: answer explicitly, never run old behaviour.
                msg_id = (cq.get('message') or {}).get('message_id')
                if action == 'done' and msg_id:
                    self.delete_message(msg_id)
                    self._answer_callback(cq['id'], 'Closed')
                else:
                    if msg_id:
                        self._strip_buttons(msg_id)
                    self._answer_callback(cq['id'], RETIRED_ACTIONS[action])
                return
            if action not in ACTIONS:
                self._answer_callback(cq['id'], 'Working')
            if action == 'page':
                from .telegram_pages import render
                key, _, number = target.partition(':')
                text, buttons = render(self.db, key, int(number))
                self.show_navigation_panel(cq['message']['message_id'], text, buttons,
                                           bool(cq['message'].get('photo')))
                return
            if action in {'upload', 'uploadcancel', 'uploadlic', 'uploadown'}:
                msg_id = (cq.get('message') or {})['message_id']
                wizard = self.image_wizard()
                try:
                    if action == 'uploadcancel':
                        wizard.cancel(chat_id, user_id, expected_id=target)
                        self.show_navigation_panel(msg_id, 'Upload cancelled. Approved assets are unchanged.', [[{'text': '← Pictures', 'callback_data': 'cimages:'}], [DONE_BUTTON]])
                    else:
                        if action == 'upload':
                            state = wizard.begin(chat_id, user_id, target, msg_id)
                        elif action == 'uploadown':
                            state = wizard.own_work(chat_id, user_id, expected_id=target)
                        else:
                            state = wizard.answer(chat_id, user_id, target.partition(':')[2], expected_id=target.partition(':')[0], expected_stage='licence')
                        if state.get('stage') == 'complete':
                            self.show_navigation_panel(msg_id, 'Image saved. Review the set before use.', [[{'text': '🖼 Review the set', 'callback_data': f"review:{state['topic']}"}], [{'text': '← Topic pictures', 'callback_data': f"imagehelp:{state['topic']}"}], [DONE_BUTTON]])
                        else:
                            self.show_upload(state)
                    self._answer_callback(cq['id'], 'Updated')
                except ValueError as exc:
                    # The tap was already acknowledged, so the reason goes in the panel where it is seen.
                    self._answer_callback(cq['id'], str(exc)[:150])
                    current = wizard.load(chat_id, user_id)
                    if current:
                        self.show_upload(current, str(exc))
                    else:
                        self.show_navigation_panel(msg_id, html.escape(str(exc)), [[{'text': '← Pictures', 'callback_data': 'cimages:'}], [DONE_BUTTON]])
                return
            if action in ACTIONS:
                self._handle_callback(cq)
            elif action == 'cov':
                msg = cq.get('message') or {}
                msg_id = msg.get('message_id')
                if msg_id and ':' in target:
                    r_name, var_letter = target.split(':', 1)
                    rows = (msg.get('reply_markup') or {}).get('inline_keyboard') or []
                    back = next((b for row in rows for b in row if b.get('callback_data') in ('close:', 'cdash:')), None)
                    if self.flip_cover(msg_id, r_name, var_letter, back):
                        self._answer_callback(cq['id'], f'Flipped to Variant {var_letter.upper()}')
                        return
                if command_handler is not None:
                    reply = command_handler(action, target)
                    self._answer_callback(cq['id'], 'Cover Selected')
                    if msg_id:
                        self.edit_message_any(msg_id, reply, is_media=bool(msg.get('photo')))
            elif action == 'covlock':
                msg = cq.get('message') or {}
                msg_id = msg.get('message_id')
                if command_handler is not None:
                    reply = command_handler('covlock', target)
                    self._answer_callback(cq['id'], 'Cover request checked')
                    if msg_id:
                        self.edit_message_caption(msg_id, reply, buttons=[[DONE_BUTTON]])
            elif command_handler is not None:
                msg = cq.get('message') or {}
                msg_id = msg.get('message_id')
                is_media = bool(msg.get('photo') or msg.get('audio') or msg.get('video') or msg.get('document'))
                if clean_chat() and msg_id and action in PANEL_ACTIONS and msg_id == self.review.message():
                    # A screen asked for from the review panel (UI-01e: 🖼 Preview carousel, QF-81; 🎨 Covers) is
                    # drawn in the dashboard: the review panel's video and caption are never drawn over.
                    opened = self._typed_panel()
                    if opened:
                        msg_id, is_media = opened[0], opened[1]
                if action in {'jobs', 'jobcancel'}:
                    note = ''
                    cancelled = cq.get('_cancelled') if action == 'jobs' else (self.jobs.cancel(target) if action == 'jobcancel' else None)
                    if cancelled is not None:  # the confirmation never showed: the cancel was made before this point
                        note = '⏹ Cancellation requested. An in-flight request may finish.\n\n' if cancelled else 'This job is no longer owned by this bot process.\n\n'
                    text, buttons = self.jobs_view()
                    from .telegram_ui import with_nav
                    buttons = with_nav([row for row in buttons if row != [DONE_BUTTON]], self.back_for(msg_id, "jobs:"),
                                       extra=None)
                    self.show_navigation_panel(msg_id, note + text, buttons, is_media)
                    self._answer_callback(cq['id'], 'Updated')
                    return
                if action == 'done':
                    if msg_id:
                        self.delete_message(msg_id)
                    self._answer_callback(cq['id'], 'Closed')
                    return
                if action == 'expired':  # a button too old to remember (its long data was pruned)
                    self._answer_callback(cq['id'], 'That button has expired; here is the Dashboard')
                    if msg_id:
                        from .control import dashboard_text_and_buttons
                        from .config import ASSETS_DIR
                        dash_text, dash_buttons = dashboard_text_and_buttons(self.db, getattr(self, 'next_run_func', lambda: None)(), assets_dir=ASSETS_DIR)
                        self.show_navigation_panel(msg_id, dash_text, is_media=is_media, buttons=dash_buttons)
                    return
                if action == 'rerender':
                    # One message per run (operator, 2026-10-25: a Re-render left the dashboard, a notice and a
                    # new failure card). The run's own card (the failure card, or the card tapped) says it is
                    # being re-rendered, and the progress card and its outcome then take over that message;
                    # a tap on the dashboard leaves the dashboard as the dashboard, redrawn.
                    from .telegram_state import put
                    run_id = self.db.resolve_run(target)
                    on_dashboard = bool(msg_id) and str(msg_id) == str(self.db.get_setting("telegram_dashboard_id") or "")
                    card = None if (on_dashboard or is_media or not msg_id) else msg_id
                    card = card or self.run_card_message(run_id)
                    if msg_id and not on_dashboard:
                        self._strip_buttons(msg_id)
                    reply = command_handler(action, target)
                    text = reply[0] if isinstance(reply, tuple) else (reply or "")
                    from_item = _ITEM_ACTION.get() is not None
                    told = getattr(text, "done", None)   # the handler's outcome (notifications.Outcome), not its words
                    if told is not None:
                        started = bool(told)
                    elif from_item:   # a handler that does not say: its item stays open (never lost)
                        started = False
                    else:   # the earlier chat's reading of a reply that does not say (origin/main)
                        started = "already" not in text.lower() and not text.startswith("🔁 That") and "Not" not in text[:12]
                    self._item_outcome(started, text)   # from an item: it stays open, saying why, when not started
                    if card and started:
                        put(self.db, "progress-reuse", run_id, str(card))
                        self.edit_message_text(card, text, buttons=[[DONE_BUTTON]])
                    elif text and from_item:
                        pass   # the item's page says why (and the toast)
                    elif text and clean_chat():
                        self.show_dashboard(text, [[DONE_BUTTON]])
                    elif text:
                        self.notify(text, buttons=[[DONE_BUTTON]], record_alert=False, transient=True, group="studio")
                    self._answer_callback(cq['id'], 'Re-render started' if started else 'Not started')
                    if on_dashboard and (started or not clean_chat()):   # a refusal is never drawn over
                        self.show_dashboard()
                    return
                if action in {'ccar', 'fetchimages', 'filmfind', 'caccounts', 'mfetch', 'cstat', 'ccov', 'cmusic', 'mreview', 'review', 'cdisc', 'cprop', 'uadd', 'tadd', 'edittext', 'mkeep', 'mdel', 'mnext', 'mreview', 'mlibrary'}:
                    self._answer_callback(cq['id'], 'Working; you can keep using the bot')
                    revision = cq.get('_revision', self._panel_revisions.get(self._current_panel(msg_id), 0))
                    if action == 'fetchimages':
                        self.edit_message_any(msg_id, '🖼 Fetching images. You can keep using the bot.', is_media=is_media, buttons=[[DONE_BUTTON]])
                    elif action == 'filmfind':
                        self.edit_message_any(msg_id, '🎞 Searching the Internet Archive and Europeana for openly licensed film. '
                                              'You can keep using the bot.', is_media=is_media, buttons=[[DONE_BUTTON]])
                    try:
                        self._run_panel_command(command_handler, action, target, msg_id, is_media, revision)
                    except ValueError as exc:
                        # "Already running" or "queue full": back to the screen this button was on, too.
                        up = screen_button(self._panel_origin.get(self._current_panel(msg_id))) if msg_id else None
                        self.show_navigation_panel(msg_id, html.escape(str(exc)),
                                                   [[{'text': 'Jobs', 'callback_data': 'jobs:'}], *([[up]] if up else []), [DONE_BUTTON]], is_media)
                    return
                if msg_id and action in DELETE_FIRST:
                    self.delete_message(msg_id)
                elif msg_id and action in ONE_SHOT:
                    self._strip_buttons(msg_id)
                try:
                    self.send_chat_action("typing")  # the native "typing…" while the screen is built
                    panel = [self, msg_id, is_media, False]
                    token = _PANEL.set(panel) if msg_id and action in PANEL_ACTIONS else None
                    try:
                        reply = command_handler(action, target)
                    finally:
                        if token is not None:
                            _PANEL.reset(token)
                    if token is not None and panel[3]:
                        reply = ''
                    self._item_reply(reply)   # an item's action: what its toast (or its page) says
                    self._answer_callback(cq['id'], 'Request checked')
                    if isinstance(reply, tuple):
                        text, buttons = reply
                        from .telegram_ui import SCREENS, parent_of, with_nav
                        if action in SCREENS and action != "cdash":  # ← back where it came from and 🏠, on every screen
                            buttons = with_nav(buttons, self.back_for(msg_id, f"{action}:{target}"))
                        if msg_id and action not in DELETE_FIRST:
                            self.show_navigation_panel(msg_id, text, is_media=is_media, buttons=buttons)
                        else:
                            self.notify(text, buttons=buttons, record_alert=False, group="studio")
                    elif action == 'cdash' and msg_id:
                        from .control import dashboard_text_and_buttons
                        from .config import ASSETS_DIR
                        next_fn = getattr(self, 'next_run_func', lambda: None)
                        dash_text, dash_buttons = dashboard_text_and_buttons(self.db, next_fn(), assets_dir=ASSETS_DIR)
                        self.show_navigation_panel(msg_id, dash_text, is_media=is_media, buttons=dash_buttons)
                    elif action in NUDGE_REPLIES and clean_chat():
                        self._say_reply(cq, reply)   # a toast, or a dashboard line: never a message (design §6.1)
                    elif action in NUDGE_REPLIES:
                        if msg_id and action in NOTICE_ACTIONS and not self._is_dashboard(msg_id):
                            self.delete_message(msg_id)  # the notice that offered it (timed out, reminder)
                        if reply:
                            self.nudge(reply)
                    elif action in EDIT_IN_PLACE and msg_id:
                        if action == "rsn" and target.endswith(":other") and isinstance(reply, str) and (
                                self.db.get_approval(self.db.resolve_run(target.partition(":")[0])) or {}).get("decision") == "regenerate":
                            # Under Other only your words can say the script was the problem (2026-10-26).
                            reply = reply.replace(" [run ", "\n⏳ The new version waits for your words (up to "
                                                  f"{REGEN_DETAIL_SECONDS} s from Regenerate) before it decides "
                                                  "what to keep. [run ", 1)
                        self.edit_message_text(msg_id, reply, buttons=[[DONE_BUTTON]])
                        if action == "rsn":
                            self._settle_rejected_card(target.partition(":")[0], msg_id)
                    elif reply and action in ('imgok', 'mkeep', 'cov') and msg_id:
                        self.edit_message_any(msg_id, reply, is_media=is_media)
                    elif action in DELETE_FIRST:
                        pass
                    elif reply:
                        from .telegram_ui import nav, parent_of
                        if msg_id and action in PANEL_ACTIONS:
                            self.show_navigation_panel(msg_id, reply, is_media=is_media, buttons=nav(self.back_for(msg_id, f"{action}:{target}")))
                        elif _ITEM_ACTION.get() is not None:   # an item's action: its answer in the dashboard
                            if _ITEM_ACTION.get()["done"] is not False:   # a refusal: the item's page says why
                                self.show_dashboard(reply, [[DONE_BUTTON]])
                        else:
                            self.notify(reply, buttons=[[DONE_BUTTON]], record_alert=False, group="studio")
                except Exception as e:
                    if _ITEM_ACTION.get() is not None:   # an item's action: the item stays open and says why
                        log.warning('The item action %s failed: %s', action, _MASKER.mask(str(e)))
                        self._item_outcome(False, f'it failed: {str(e)[:150]}')
                        return
                    self._answer_callback(cq['id'], 'Failed')
                    if action in PANEL_ACTIONS:
                        log.warning('Navigation %s failed: %s', action, _MASKER.mask(str(e)))
                        label = {'ccov': 'Covers', 'cmusic': 'Music', 'mreview': 'music review'}.get(action, 'this screen')
                        text = f'⚠️ Could not open {label}. Please try again.'
                        buttons = [[{'text': 'Try again', 'callback_data': f'{action}:{target}'}], [DONE_BUTTON]]
                        if not self.show_navigation_panel(msg_id, text, buttons, is_media):
                            self.notify(text, buttons=buttons, record_alert=False, group="studio")
                    else:
                        what = JOB_TITLES.get(action) or (screen_name(f"{action}:") or "That").split(" ", 1)[-1]
                        self.notify(f'⚠️ {html.escape(what)} did not work: {html.escape(str(e)[:300])}', group="studio")
            return
        msg = update.get('message') or {}
        typed = (msg.get('text') or '').strip()
        if (clean_chat() and typed and not typed.startswith('/') and self.review.armed()
                and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', ''))
                and (msg.get('reply_to_message') or {}).get('message_id') in (None, self.review.message())):
            # The review panel asked for words (UI-01e, design §6.6): the next plain text, or a reply to the panel,
            # answers it; the message leaves once read and the panel shows the answer.
            self._delete_soon(msg.get('message_id'))
            if command_handler is not None:
                self.review.typed(typed, command_handler)
            return
        if self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            chat, user = (msg['chat']['id'], msg['from']['id'])
            wizard = self.image_wizard()
            state = wizard.load(chat, user)
            text = (msg.get('text') or '').strip()
            image = (msg.get('photo') or [None])[-1] or msg.get('document')
            upload_match = re.match(r'^/photos\s+(?:upload\s+(\S.*)|(\S+)\s+upload)\s*$', text, re.IGNORECASE)
            if upload_match:
                ref = upload_match.group(1) or upload_match.group(2)
                target = self._typed_panel() if clean_chat() else None
                if target:
                    # The clean chat (design §6.2, §10.1 "Upload wizard step sent fresh -> D"): the wizard opens in
                    # the dashboard, as the button path does; its screen is the wizard (the 30-min return home).
                    panel = target[0]
                    with self._panel_lock:
                        self._panel_screen[panel] = f"upload:{ref}"
                else:
                    panel = self.notify('Preparing image upload…', record_alert=False, group="studio")
                try:
                    state = wizard.begin(chat, user, ref, panel)
                    self.show_upload(state)
                except ValueError as exc:
                    self.show_navigation_panel(panel, html.escape(str(exc)), [[DONE_BUTTON]])
                if clean_chat() and msg.get('message_id'):
                    self._delete_soon(msg['message_id'])   # read and answered in the dashboard: it leaves (§6.7)
                return
            if state and (image or (text and (not text.startswith('/')) and (not msg.get('reply_to_message')))):
                if image and state['stage'] == 'file':
                    state['_navigation_revision'] = self._panel_revisions.get(self._current_panel(state['message_id']), 0)
                    self.jobs.submit(self._background(), 'Receiving image', lambda: self.receive_guided_image(msg, image, state), lambda job, result: False, resource=f"guided-image:{chat}:{user}")
                elif image:
                    if clean_chat() and msg.get('message_id'):   # answered in the upload panel: it leaves (UI-01d)
                        self._delete_soon(msg['message_id'])
                    self.show_upload(state, 'The image was already received. Please answer the current prompt.')
                elif text:
                    if msg.get('message_id'):
                        self._delete_soon(msg['message_id'])
                    try:
                        state = wizard.answer(chat, user, text)
                        if state['stage'] == 'complete':
                            self.show_navigation_panel(state['message_id'], 'Image saved. Review the set before use.', [[{'text': 'Review set', 'callback_data': f"review:{state['topic']}"}], [DONE_BUTTON]])
                        else:
                            self.show_upload(state)
                    except ValueError as exc:
                        self.show_upload(state, str(exc))
                return
        image_item = (msg.get('photo') or [None])[-1] or msg.get('document')
        image_caption = (msg.get('caption') or '').strip()
        # A photo sent with '/photos <topic> | source: … | licence: …' as its caption joins that topic's set
        if image_item and image_caption.split(' ', 1)[0].split('@')[0].lower() == '/photos' and '|' in image_caption:
            if not self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
                return
            try:
                from . import config
                from .telegram_images import parse_caption, add_image
                ref, rights = parse_caption(image_caption.partition(' ')[2])
                content = self.download_image(image_item)
                topic_id = add_image(self.db, config.ASSETS_DIR, ref, rights, content, msg.get('from', {}).get('id'))
                self._upload_reply(msg, f'🖼 Image saved for <code>{html.escape(topic_id)}</code>. The set needs human review.', buttons=[[{'text': 'Review set', 'callback_data': f'review:{ref}'}], [DONE_BUTTON]], record_alert=False, group="studio")
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self._upload_reply(msg, f'⚠️ Image upload failed: {html.escape(message)}', buttons=[[DONE_BUTTON]], record_alert=False, group="studio")
            return
        photo_item = (msg.get('photo') or [None])[-1] or msg.get('document')
        photo_caption = (msg.get('caption') or '').strip()
        issue_waiting = bool(self._awaiting and self._awaiting[0] == 'issue' and self._awaiting[1] > time.time())
        if (photo_item and clean_chat() and self.review.armed() and not photo_caption.startswith('/')
                and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', ''))):
            # The review panel asked for text (UI-01e, design §6.6): the photo leaves and the panel says so.
            self._delete_soon(msg.get('message_id'))
            self.review.typed_photo()
            return
        if photo_item and command_handler and not issue_waiting and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            caption = photo_caption
            word = caption.split(' ', 1)[0].split('@')[0].lower()
            if word == '/ask':
                question = caption.partition(' ')[2].strip()
                reply = command_handler('ask', question) if question else None
                note = ("\n\n<i>The screenshot itself isn't read: describe what it shows in the question, or send it "
                        "with /issue to report a problem.</i>")
                self.show_dashboard((reply if isinstance(reply, str) else "🔍 Add your question as the photo's caption: /ask &lt;question&gt;") + note,
                                    navigation_buttons(None), fresh=True)
                self._leave_unanswered(msg, hint=False)   # read and answered: the photo leaves (UI-01d)
                return
            if (clean_chat() and not caption and self._awaiting and self._awaiting[1] > time.time()
                    and not (msg.get('video') or msg.get('document'))):
                self._leave_unanswered(msg)   # "That was a photo; type the text instead." (UI-01d)
                return
            if word not in {'/photos', '/footage', '/issue'} and not (msg.get('video') or msg.get('document')):
                self.show_dashboard("📎 <b>Photo received</b>, but its caption doesn't say what to do with it.\n\n"
                                    "• Report a problem: caption <code>/issue what is wrong</code>\n"
                                    "• Ask about it: caption <code>/ask your question</code>\n"
                                    "• Add it to a topic: Pictures → Photo sets → the topic → Upload my image", navigation_buttons(None), fresh=True)
                self._leave_unanswered(msg, hint=False)   # the dashboard says what to do; the photo leaves (UI-01d)
                return
        doc_or_vid = msg.get('video') or msg.get('document')
        caption = (msg.get('caption') or '').strip()
        if doc_or_vid and caption.split(' ',1)[0].split('@')[0].lower() == '/footage' and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            _, _, args = caption[1:].partition(' ')
            parts = [s.strip() for s in args.split('|')]
            topic_ref = parts[0]
            rights = {}
            for part in parts[1:]:
                key, _, value = part.partition(':')
                key = key.strip().lower().replace('license', 'licence')
                if key in ('source', 'licence', 'licence_url', 'author') and value.strip():
                    rights[key] = value.strip()
            # Every outcome is said (it failed silently: an unknown topic, a clip over Telegram's 20 MB, a bare
            # caption: review of 2026-10-25).
            if not topic_ref:
                self._upload_reply(msg, f"🎞 Say which topic the clip is for: <code>{html.escape(FORM)}</code>", group="studio")
                return
            if (doc_or_vid.get('file_size') or 0) > FOOTAGE_MAX_BYTES:
                self._upload_reply(msg, f"🎞 That clip is {doc_or_vid['file_size'] / 1048576:.0f} MB; a bot can receive up to "
                                   f"{FOOTAGE_MAX_BYTES // 1048576} MB. Trim or compress it and send it again.", group="studio")
                return
            if topic_ref:
                try:
                    file_id = doc_or_vid.get('file_id')
                    file_info = self._api('getFile', data={'file_id': file_id})
                    file_path = file_info.get('file_path')
                    if file_path:
                        from . import config
                        from .control import _resolve_topic_dir
                        topic_dir = _resolve_topic_dir(topic_ref, config.ASSETS_DIR, db=self.db)
                        from .telegram_images import topic_for
                        topic_for(self.db, topic_dir.name)
                        footage_dir = topic_dir / 'footage'
                        footage_dir.mkdir(parents=True, exist_ok=True)
                        dest = footage_dir / 'curated_footage.mp4'
                        content = self.download_image(doc_or_vid)
                        import tempfile, subprocess
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False, dir=footage_dir) as staged:
                            staged.write(content)
                            staged_path = Path(staged.name)
                        try:
                            check = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(staged_path)], capture_output=True, timeout=20)
                            if check.returncode or b"video" not in check.stdout:
                                raise ValueError("Upload is not a readable video")
                            staged_path.replace(dest)
                        finally:
                            staged_path.unlink(missing_ok=True)
                        cand = {'provider': 'curated', 'title': 'Telegram Curated Clip', 'artist': rights.get('author') or 'unknown', 'license': rights.get('licence', 'unverified'), 'source_url': rights.get('source', ''), 'license_url': rights.get('licence_url', ''), 'uploaded_by': f"telegram:{msg.get('from', {}).get('id', '')}"}
                        (footage_dir / 'curated_footage.json').write_text(json.dumps(cand, indent=2, ensure_ascii=False), encoding='utf-8')
                        missing = [k for k in ('source', 'licence', 'licence_url') if k not in rights]
                        note = '' if not missing else f"\n⚠️ No {', '.join(missing)} given, so the release gate will hold any video using it. Resend with <code>/footage topic | source: URL | licence: CC BY 4.0 | licence_url: URL</code>."
                        self._upload_reply(msg, f'✅ Video clip received and saved for <b>{html.escape(topic_dir.name)}</b>.{note}', group="studio")
                    else:
                        self._upload_reply(msg, "🎞 Telegram didn't hand the clip over (no file path). Send it again.", group="studio")
                except Exception as exc:
                    log.exception('Direct video download from Telegram failed')
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    self._upload_reply(msg, f"🎞 The clip wasn't saved: {html.escape(reason[:300])}", group="studio")
            return
        photos = msg.get('photo')
        media_item = (photos[-1] if photos else None) or msg.get('document') or msg.get('video') or msg.get('audio') or msg.get('voice')
        caption = (msg.get('caption') or '').strip()
        # Only the operator's own photo answers the pending /issue (anyone else's cancelled it: review, 2026-10-25)
        waiting_issue = bool(media_item and self._awaiting and self._awaiting[0] == 'issue' and self._awaiting[1] > time.time()
                             and not caption.startswith('/')
                             and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')))
        if waiting_issue:
            self._awaiting = None
            caption = '/issue ' + (caption or 'See the attached screenshot.')
        if media_item and caption.split(' ',1)[0].split('@')[0].lower() == '/issue' and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            command, _, critique_text = caption[1:].partition(' ')
            command = 'issue'
            try:
                file_id = media_item.get('file_id')
                file_info = self._api('getFile', data={'file_id': file_id})
                file_path = file_info.get('file_path', '')
                if file_path:
                    from . import config as _config
                    evidence_dir = _config.ISSUES_DIR / 'evidence'
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    ext = Path(file_path).suffix or '.jpg'
                    from uuid import uuid4
                    dest = evidence_dir / f"evidence_{uuid4().hex}{ext}"
                    content = self.download_image(media_item)
                    staged = dest.with_suffix(dest.suffix + ".tmp")
                    try:
                        staged.write_bytes(content)
                        staged.replace(dest)
                    finally:
                        staged.unlink(missing_ok=True)
                    args_with_ev = f'{critique_text.strip()} --evidence {dest}'
                    if command_handler:
                        reply = command_handler(command, args_with_ev)
                        self.show_dashboard(reply if isinstance(reply, str) else reply[0], navigation_buttons(None), fresh=True)
                        self._delete_soon(msg.get('message_id'))
            except Exception as e:
                log.exception('Evidence upload for /issue failed')
                self.show_dashboard(f'⚠️ Failed to process issue evidence: {html.escape(str(e)[:300])}', navigation_buttons(None), fresh=True)
            return
        text = (msg.get('text') or '').strip()
        reply_to = msg.get('reply_to_message')
        if not text or command_handler is None:
            if command_handler is not None:
                self._leave_unanswered(msg)
            return
        user_id = str(msg.get('from', {}).get('id', ''))
        chat_id = str(msg.get('chat', {}).get('id', ''))
        username = str(msg.get('from', {}).get('username', '') or msg.get('from', {}).get('first_name', ''))
        is_auth = self._authorized(user_id, chat_id)
        if is_auth and not text.startswith('/') and not reply_to:
            with self._panel_lock:
                waiting, self._awaiting = self._awaiting, None
            if waiting and waiting[1] > time.time():
                text = f'/{waiting[0]} {text}'
        if text.startswith('/'):
            command, _, args = text[1:].partition(' ')
            command = command.split('@')[0].lower()
            if command in {'ask', 'issue'} and not args.strip() and is_auth:
                # Help can only send the bare command: the next message is the question or report.
                if clean_chat() and self.review.armed():
                    self.review.cancelled_by(command)   # one input at a time: the review panel says so
                with self._panel_lock:
                    self._awaiting = (command, time.time() + 600)
            if command == 'issue' and args.strip() and reply_to:
                # A report made in reply to a card carries that card (and its run) as evidence.
                from .telegram_state import get
                run_ref = get(self.db, 'message-run', f"{self.chat_id}:{reply_to.get('message_id')}")
                parent_text = (reply_to.get('text') or reply_to.get('caption') or '').strip()
                args = (f'run {self.db.run_key(run_ref)} ' if run_ref else '') + args.strip() + (
                    f'\n\n[Replied card]: {parent_text[:1200]}' if parent_text else '')
            audit_payload = text
        elif reply_to and re.search('\\[edit run (\\d+)\\]', reply_to.get('text') or ''):
            command = 'edittext'
            args = re.search('\\[edit run (\\d+)\\]', reply_to['text']).group(1) + ' ' + text
            audit_payload = f'/edittext {text[:80]}'
        elif not reply_to and self._open_edit_for(text):
            # The edit template pasted into the chat rather than sent as a reply (operator, 2026-10-23).
            command = 'edittext'
            args = self._open_edit_for(text) + ' ' + text
            audit_payload = f'/edittext {text[:80]}'
        elif reply_to and re.search('\\[run (\\d+)\\]', reply_to.get('text') or '') and (
                (reply_to.get('text') or '').startswith(('📝', '✅ Reason')) or
                any(marker in (reply_to.get('text') or '') for marker in ('Why regenerate?', 'Why reject?', '✅ Reason:'))):
            # A reason card: the old separate message, or the decided review card that now carries it.
            command = 'whytext'
            args = re.search('\\[run (\\d+)\\]', reply_to['text']).group(1) + ' ' + text
            audit_payload = f'/whytext {text[:80]}'
        elif reply_to:
            parent_text = (reply_to.get('text') or reply_to.get('caption') or '').strip()
            from .telegram_state import get, decode
            run_ref = get(self.db, 'message-run', f"{self.chat_id}:{reply_to.get('message_id')}")
            if run_ref:
                run_ref = self.db.run_key(run_ref)
            for row in (reply_to.get('reply_markup') or {}).get('inline_keyboard', []):
                for button in row:
                    action, _, target = decode(self.db, button.get('callback_data', '')).partition(':')
                    if action in {'approve', 'reject', 'regen', 'cprog', 'resend'} and target:
                        run_ref = target
                        break
            command = 'ask'
            args = f'{text}\n\n[Context from replied message]: {parent_text[:1800]}'
            if run_ref:
                args = f'run {run_ref} {args}'
            audit_payload = f'/ask (reply) {text}'
        else:
            self._leave_unanswered(msg)   # plain text that answers nothing
            return
        try:
            self.db.record_audit_event(user_id=user_id, username=username, chat_id=chat_id, event_type='command', payload=audit_payload, status='allowed' if is_auth else 'denied')
        except Exception as ea:
            log.debug('Could not record command audit: %s', ea)
        if not is_auth:
            log.warning('Ignoring command from unauthorized user %s.', user_id)
            return
        if text.startswith('/'):
            from .telegram_commands import ALIASES, menu, normalise_command
            public = {entry["command"] for entry in menu()} | set(ALIASES)
            if command not in public:
                if msg.get('message_id'):
                    self._delete_soon(msg['message_id'])
                self.show_dashboard(f"🤔 I don't know <code>/{html.escape(command[:40])}</code>. Help lists every command.",
                                    [[{"text": "❓ Help", "callback_data": "chelp:Everyday"}], [DONE_BUTTON]], fresh=True)
                return
            command, args = normalise_command(command, args)  # an older name or a subcommand, as the handler knows it
        if command in {'start', 'dashboard'}:
            # Always a new panel at the bottom: after Telegram's "Clear history" the old one is no longer
            # visible to the operator, and editing it would leave an empty chat (and its START button) again.
            # Telegram can send /start and /dashboard together: one panel answers both.
            with self._panel_lock:
                repeat = time.monotonic() - self._last_opened < 5
                if not repeat:
                    self._last_opened = time.monotonic()
            if not repeat and clean_chat():
                # The recovery after a Telegram "Clear history" too: the layout is posted again in order, the
                # old copies deleted (design §5.3, §6.7).
                self.layout.rebuild(urgent=True)
                if not self.db.get_setting("telegram_dashboard_id"):
                    self.show_dashboard()
            elif not repeat:
                self.show_dashboard(fresh=True)
            if msg.get('message_id'):
                self._delete_soon(msg['message_id'])  # after the panel exists, so the chat is never empty
            return
        if command == 'clear':
            cmd_mid = msg.get('message_id')
            self.send_chat_action('typing')
            if cmd_mid:
                self.delete_message(cmd_mid)
            depth = 500
            arg_clean = args.strip().lower()
            if arg_clean.isdigit():
                depth = min(1000, max(50, int(arg_clean)))
            elif arg_clean in ('all', 'full', 'max', 'entire'):
                depth = 1000
            self.clear_chat(below=cmd_mid, depth=depth)
            from .control import dashboard_text_and_buttons
            from .config import ASSETS_DIR
            next_fn = getattr(self, 'next_run_func', lambda: None)
            dash_text, dash_buttons = dashboard_text_and_buttons(self.db, next_fn(), assets_dir=ASSETS_DIR)
            if clean_chat():
                self.layout.rebuild(urgent=True)
                self.show_dashboard(dash_text, dash_buttons)
                return
            self.show_dashboard(dash_text, dash_buttons, fresh=True)
            return
        if command == 'progress' and is_auth and self.bring_progress_down():
            if msg.get('message_id'):
                self._delete_soon(msg['message_id'])
            return
        if command == 'jobs':
            if msg.get('message_id'):
                self._delete_soon(msg['message_id'])
            text, buttons = self.jobs_view()
            self.show_dashboard(text, buttons, fresh=True)
            return
        if command == 'images':
            verb, _, ref = args.partition(' ')
            if verb == 'fetch':
                command, args = ('fetchimages', ref)
        if command == 'music' and args.startswith('fetch '):
            command, args = ('mfetch', args.partition(' ')[2])
        if msg.get('message_id'):
            self._delete_soon(msg['message_id'])  # the panel shows what was asked; the chat keeps one panel
        if command in NUDGE_REPLIES:
            # Details for a reason card: acknowledged by a nudge; a rejected run's card has done its job.
            try:
                reply = command_handler(command, args.strip())
            except Exception as e:
                log.warning("Command /%s failed: %s", command, _MASKER.mask(str(e)))
                reply = f"⚠️ /{html.escape(command)} failed: {html.escape(str(e)[:300])}"
            if command == 'whytext' and reply_to and reply_to.get('message_id'):
                self._settle_rejected_card(args.split(' ', 1)[0], reply_to['message_id'], now=True)
            if isinstance(reply, tuple):  # a refusal with buttons (an unrequeueable topic) still says why
                reply = reply[0]
            if isinstance(reply, str) and reply and clean_chat():
                self.show_dashboard(reply, [[DONE_BUTTON]])   # a typed reply is a dashboard line (design §10.1)
            elif isinstance(reply, str) and reply:
                self.nudge(reply)
            return
        target = self._typed_panel()
        if command in SLOW_COMMANDS:
            self.send_chat_action('typing')
            if target:
                self._run_panel_command(command_handler, command, args.strip(), target[0], False, target[2], late=True)
            else:
                self.start_operator_job(JOB_TITLES.get(command, command.replace('_',' ').capitalize()), lambda: command_handler(command, args.strip()), resource=f'{command}:{args.strip()}')
        else:
            self._run_command(command_handler, command, args.strip(), target)

    _workers = None
    _cleanup = None

    UNANSWERED = "🤔 Type a command, or use the buttons. Help lists every command."
    PHOTO_FOR_TEXT = "That was a photo; type the text instead."
    MEDIA_FOR_TEXT = "That wasn't text; type it instead."

    def _upload_reply(self, msg: dict[str, Any], text: str, **notify: Any) -> None:
        """The answer to an upload (/photos caption, /footage): the clean chat (UI-01d, design §6.6, §10.1) draws
        it in the dashboard and deletes the upload once it is read (staged, or refused with what to do); the
        earlier chat sends it as before."""
        if not clean_chat():
            group = notify.pop("group", "studio")
            self.notify(text, group=group, **notify)
            return
        self.show_dashboard(text, navigation_buttons(notify.get("buttons")))
        if msg.get('message_id'):
            self._delete_soon(msg['message_id'])

    def _leave_unanswered(self, msg: dict[str, Any], hint: bool = True, stale: bool = False) -> None:
        """The clean chat (UI-01d, design §6.6): the operator's messages are never permanent. A message the bot has no
        use for (plain text that answers nothing, a sticker, a photo nothing asked for, one sent while the bot was
        off) is deleted once read, and the dashboard says what to do instead of leaving it unanswered."""
        if not clean_chat() or not msg.get('message_id'):
            return
        if not self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            return
        self._delete_soon(msg['message_id'])
        if stale:   # it was not acted on: said in the dashboard, so it never looks handled
            self._stale_dropped = getattr(self, "_stale_dropped", 0) + 1
            n = self._stale_dropped
            text = (f"⌛ {n} message{'s' if n != 1 else ''} sent while the bot was off "
                    f"{'were' if n != 1 else 'was'} not acted on; send {'them' if n != 1 else 'it'} again.")
        elif not hint:
            return
        else:
            media = any(msg.get(k) for k in ('photo', 'document', 'video', 'audio', 'voice', 'sticker', 'animation'))
            if media and self.review.armed():
                self.review.typed_photo()   # the review panel asked for text: it says so (design §6.6)
                return
            waiting = self._awaiting
            armed = waiting and waiting[1] > time.time()
            text = (self.PHOTO_FOR_TEXT if media and armed and msg.get('photo') else self.MEDIA_FOR_TEXT if media and armed
                    else self.UNANSWERED)
        try:
            self.show_dashboard(text, [[{"text": "❓ Help", "callback_data": "chelp:Everyday"}], [DONE_BUTTON]])
        except Exception as e:  # noqa: BLE001 - the message is gone either way
            log.debug("Dashboard hint not drawn: %s", e)

    def _delete_soon(self, message_id) -> None:
        """Removes a message off the reply path: what the operator sees next does not wait for it."""
        if not message_id:
            return
        import concurrent.futures
        with self._panel_lock:
            if self._cleanup is None:
                self._cleanup = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tg-cleanup")
            self._cleanup.submit(self.delete_message, message_id)

    def _background(self):
        import concurrent.futures
        with self._panel_lock:
            if self._workers is None:
                self._workers = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg-command")
            return self._workers

    def _run_command(self, command_handler: Callable[[str, str], str], command: str, args: str, target=None) -> None:
        if command in {"fetchimages", "mfetch", "filmfind"}:
            if target:
                self._run_panel_command(command_handler, command, args, target[0], False, target[2], late=True)
            else:
                self.start_operator_job(JOB_TITLES.get(command, "Working"),
                    lambda: command_handler(command, args), resource=f"{command}:{args}")
            return
        panel = [self, target[0], False, False, target[2]] if target else None
        token = _PANEL.set(panel) if panel else None
        try:
            reply = command_handler(command, args)
        except Exception as e:
            log.warning("Command /%s failed: %s", command, _MASKER.mask(str(e)))
            reply = f"⚠️ /{html.escape(command)} failed: {html.escape(str(e)[:300])}"
        finally:
            if token is not None:
                _PANEL.reset(token)
        if panel and panel[3]:
            return  # the command drew its own screen in the panel
        if reply is None:
            return  # a job the command started owns the panel now
        text, buttons = reply if isinstance(reply, tuple) else (reply, [[DONE_BUTTON]])
        from .telegram_ui import SCREENS, parent_of
        parent = parent_of(command) if command in SCREENS and command not in ("dashboard", "cdash") else None
        if command == "analytics" and args.strip():  # a typed sub-screen goes back to the overview, not Status
            parent = "canalytics:"
        if target:
            if not self.update_navigation_target(target, text or "✅ Done.", navigation_buttons(buttons, parent)):
                self.show_dashboard(text or "✅ Done.", navigation_buttons(buttons, parent), fresh=True)
        elif text:
            self.notify(text, buttons=buttons, record_alert=False, group="studio")

    def back_for(self, message_id, callback):
        """Where ← goes from `callback` shown in this panel: the screen it was opened from, else its parent."""
        from .telegram_ui import came_from
        trail = self._panel_trail.get(self._current_panel(message_id), []) if message_id else []
        return came_from(trail, callback)

    def _run_panel_command(self, handler, action, target, message_id, is_media, revision, late=False):
        def work():
            panel = [self, self._current_panel(message_id), is_media, False, revision]
            token = _PANEL.set(panel)
            try:
                result = handler(action, target)
                return None if panel[3] else result
            finally:
                _PANEL.reset(token)
        return self.start_operator_job(JOB_TITLES.get(action, action.replace("_", " ").capitalize()),
            work, resource=f"{action}:{target}", navigation=(message_id, is_media, revision), late=late)

    def wait_for_decision(self, run_id: str, timeout_seconds: Optional[int] = None, poll_interval: int = 5,
                          reminder_after: int = 1800, window: Optional[Callable[[], int]] = None) -> str:
        """Returns 'approved', 'rejected', 'regenerate', or 'timeout'. Never auto-approves. One reminder
        after `reminder_after` seconds when the window is long enough; on timeout, a Resend button."""
        timeout_seconds = timeout_seconds or APPROVAL_TIMEOUT_SECONDS
        with self.db._conn() as conn:
            # A review an Undo reopened keeps the time the Undo gave it (UI-02); any other is narrowed to this wait
            conn.execute("UPDATE approvals SET expires_at=CASE WHEN decided_by='undone' THEN MAX(COALESCE(expires_at,0),?) "
                         "ELSE MIN(COALESCE(expires_at,?),?) END WHERE run_id=?",
                         (time.time()+timeout_seconds, time.time()+timeout_seconds, time.time()+timeout_seconds, run_id))
        log.info("Awaiting human decision for %s (timeout %ss)...", run_id, timeout_seconds)
        started = time.monotonic()
        deadline = started + timeout_seconds
        reminded = timeout_seconds < reminder_after + 600  # no reminder for a short window
        offset: Optional[int] = None  # unconfirmed taps (e.g. made just before this wait) still count
        reminder: List[Any] = []  # the reminder's message, removed once the review is decided
        delivered: Optional[str] = None  # when the card was last sent: a resend restarts the window
        try:
            while time.monotonic() < deadline:
                self.cleanup_transient_messages()
                if not reminded and time.monotonic() - started >= reminder_after and clean_chat():
                    reminded = True   # the clean chat (UI-01e): the reviews item rings once more, no message
                    self.review.remind(run_id, time.time() + deadline - time.monotonic())
                if not reminded and time.monotonic() - started >= reminder_after:
                    reminded = True
                    closes = show_time(time.time() + deadline - time.monotonic(), "%H:%M %Z")
                    reminder.append(self.notify(f"⏰ Still waiting for your review of {html.escape(self.db.run_label(run_id))} "
                                f"(the card stays open until {closes}).",
                                buttons=[{"text": "📨 Bring the card down", "callback_data": f"resend:{self.db.run_key(run_id)}"}],
                                transient=True, ttl_minutes=int((deadline - time.monotonic()) / 60) + 1, group="needs"))
                approval = self.db.get_approval(run_id)
                if approval and approval.get("created_at") != delivered:
                    if delivered is not None:  # brought down again (a resend): its window starts again
                        sizer = window or getattr(self, "approval_window", None)
                        again = sizer() if callable(sizer) else timeout_seconds   # sized as the first was, from now
                        deadline = max(deadline, time.monotonic() + again)
                        reminded = again < reminder_after + 600
                        started = time.monotonic()
                        for old in [m for m in reminder if type(m) is int]:  # the earlier reminder's button is stale
                            self._delete_soon(old)
                        reminder.clear()
                    delivered = approval.get("created_at")
                if approval and approval.get("expires_at") and approval.get("decided_by") == "undone":
                    # A V2 Undo gives the review back its time (db.undo_review_decision, at most the review window):
                    # the wait follows. Only then: a delivery (record_approval) sets a 24 h placeholder that the wait
                    # narrows itself, and it clears the Undo's mark, so a card re-sent after an Undo is not followed.
                    try:
                        deadline = max(deadline, time.monotonic() + float(approval["expires_at"]) - time.time())
                    except (TypeError, ValueError):
                        pass
                if approval and approval["decision"] != "pending":
                    log.info("Decision for %s: %s (by %s)", run_id, approval["decision"], approval.get("decided_by"))
                    return approval["decision"]

                if not self.is_configured() or self.background_polling:
                    time.sleep(poll_interval)
                    continue

                try:
                    long_poll = int(max(1, min(25, deadline - time.monotonic())))
                    req = {"timeout": long_poll, "allowed_updates": json.dumps(["callback_query"])}
                    if offset is not None:
                        req["offset"] = offset
                    updates = self._api("getUpdates", timeout=long_poll + 10, data=req)
                    for update in updates:
                        offset = update["update_id"] + 1
                        cq = update.get("callback_query")
                        if cq:
                            decision = self._handle_callback(cq, run_id)
                            if decision:
                                return decision
                except Exception as e:
                    log.warning("Telegram polling error (will retry): %s", e)
                    time.sleep(poll_interval)

            if self.db.update_approval_decision(run_id, "timeout", decided_by="system"):
                log.warning("No decision for %s within %ss.", run_id, timeout_seconds)
                self.dismiss_approval_buttons(run_id)
                self.timeout_notice(run_id)
                return "timeout"
            return (self.db.get_approval(run_id) or {}).get("decision", "timeout")
        finally:
            for old in [m for m in reminder if type(m) is int]:
                self._delete_soon(old)  # each stayed after the approval, offering a dead button

    def record_local_decision(self, run_id: str, decision: str) -> bool:
        """CLI decision path. Returns False if the run already has a decision."""
        if decision not in DECISIONS:   # never a card's pseudo-decision ("keep", "undo_approved": UI-02)
            raise ValueError(f"Invalid decision '{decision}'")
        if decision == "approved":  # the card's rule (send_review_card): a blocked version is not approved
            from .release import release_problems
            run = self.db.get_run(run_id) or {}
            report = self._release_report(run)
            problems = release_problems(report, dry_run=bool((report or {}).get("dry_run"))) if report else []
            if problems:
                raise ValueError("Release blocked, so it is not approved: " + "; ".join(problems))
        applied = self.db.update_approval_decision(run_id, decision, decided_by="cli")
        if applied:
            self.dismiss_approval_buttons(run_id)
        return applied
