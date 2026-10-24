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

import html
import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests

from .local_time import show as show_time
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

ACTIONS = {"approve": "approved", "regen": "regenerate", "reject": "rejected", "cancel": "cancelled"}
TELEGRAM_VIDEO_LIMIT_MB = 49.0
REASON_CARD_HOURS = 2  # a decided review's reason card survives /clear this long (the window for a reply)
STALE_COMMAND_SECONDS = 600  # commands queued while the bot was down longer than this are not replayed
# Typed commands run as a job. Not /propose: it only starts the research job, and wrapping that in a
# second job showed an empty "Completed." over the research panel.
SLOW_COMMANDS = {"status", "add", "issue", "music", "discover", "ask", "review", "covers", "edittext", "fetchimages", "accounts"}
# Buttons on cards from earlier releases. They get an explicit answer and never run old behaviour.
RETIRED_ACTIONS = {"done": "Closed",
                   "umore": "This card is from an earlier version. Open /ideas for UNESCO marvels.",
                   "umoredone": "This card is from an earlier version. Open /ideas for UNESCO marvels."}

DONE_BUTTON = {"text": "← Dashboard", "callback_data": "cdash:"}
PAGER_ICONS = {"⏮", "◀", "▶", "⏭"}
_PANEL = ContextVar("telegram_navigation_panel", default=None)
PANEL_ACTIONS = {"reviewpick", "pickbump", "pickdrop", "cideas", "cpropose", "ccov", "cmusic", "cdisc", "cprop", "cstat", "cimages", "caccounts", "imagepick", "imagehelp", "review", "mkeep", "mnext", "mdel", "mcancel", "mreview", "musicmenu", "mfmenu", "mlibrary", "chelp"}
# Cards removed before acting (their own Cancel/Skip/Next buttons).
DELETE_FIRST = {"tskip"}
# Decision buttons: disabled before acting so a second tap can't repeat the decision.
ONE_SHOT = {"uadd", "tadd", "imgok", "mkeep", "cov", "rsn", "impok", "impno", "imprev",
            "pubok", "pubno", "simpub", "resend", "rerender"}
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
JOB_TITLES = {"fetchimages": "Fetching images", "mfetch": "Finding documentary music", "caccounts": "Checking accounts",
              "ccov": "Covers", "cmusic": "Music", "cstat": "Full status", "cprop": "Waiting ideas",
              "cdisc": "UNESCO marvels", "mreview": "Music review", "mlibrary": "Music library", "mkeep": "Keeping a track",
              "mdel": "Removing a track", "mnext": "Next track", "review": "Image review", "uadd": "Adding a marvel",
              "tadd": "Adding an idea", "edittext": "Editing text", "ask": "Ask", "issue": "Logging an issue",
              "status": "Full status", "accounts": "Checking accounts", "discover": "UNESCO marvels",
              "propose": "Researching topic ideas", "covers": "Covers", "music": "Music", "add": "Adding a topic"}

# The screen a background job was started from, by its resource (the part before ":"): its card
# offers a way back one level, not only to the dashboard (topic research had only ← Dashboard).
JOB_PARENTS = {
    "topic-research": ("← Topic ideas", "cideas:"),
    "propose": ("← Topic ideas", "cideas:"),
    "tadd": ("← Topic ideas", "cideas:"),
    "fetchimages": ("← Images", "cimages:"),
    "imagepick": ("← Images", "cimages:"),
    "mfetch": ("← Music", "cmusic:"),
    "uadd": ("← UNESCO marvels", "cdisc:"),
}


def job_parent(resource: str) -> Optional[Dict[str, str]]:
    found = JOB_PARENTS.get((resource or "").partition(":")[0])
    return {"text": found[0], "callback_data": found[1]} if found else None


# Names for screens a job can be started from, so its back button says where it goes.
SCREEN_NAMES = {"cideas": "Topic ideas", "cdisc": "UNESCO marvels", "cprop": "Waiting ideas", "cimages": "Images",
                "imagepick": "Images", "cmusic": "Music", "mfmenu": "Music", "mlibrary": "Music library",
                "creviews": "Reviews", "covers": "Covers", "ccov": "Covers", "qrefresh": "Queue",
                "cstat": "Full status", "caudit": "Audit", "cimprove": "Improvements", "reviewpick": "Storyboards"}


def screen_button(callback: Optional[str]) -> Optional[Dict[str, str]]:
    """'← <screen>' back to the exact screen (and page) a callback showed. Only the read-only screens
    named above qualify: a back button replays its callback, and replaying an action (adding a marvel,
    starting research) would repeat it. The dashboard has its own button."""
    name = SCREEN_NAMES.get((callback or "").partition(":")[0])
    return {"text": f"← {name}", "callback_data": callback} if name else None


_MASKER = SecretMasker()
# Keep-alive connections shared by every bot thread: a new TLS connection costs ~1 s, a reused one
# ~0.2 s. Sessions are per thread; the connection pool (thread-safe) is shared.
_HTTP = threading.local()
_POOL = requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=16)
CONNECT_TIMEOUT = 6  # a stalled connect fails fast (edits retry) instead of waiting out the OS timeout


def _post(url, timeout=30, **kwargs):
    session = getattr(_HTTP, "session", None)
    if session is None:
        session = _HTTP.session = requests.Session()
        session.mount("https://", _POOL)
    # Uploads keep one timeout: with a (connect, read) pair the short connect value also bounds each socket
    # write while the file is sent, which made review videos fail ("The write operation timed out").
    return session.post(url, timeout=timeout if kwargs.get("files") else (CONNECT_TIMEOUT, timeout), **kwargs)


def navigation_buttons(buttons=None):
    rows = format_responsive_keyboard(buttons) if buttons else []
    if not any(b.get("callback_data") == "cdash:" for row in rows for b in row):
        rows.append([DONE_BUTTON])
    return rows


CARD_PAGE_LIMIT = 3600  # telegram_pages.panel pages (as plain text) a card over 3,700 UTF-16 units


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
            eta = (f"this step is taking longer than usual (usually {_minutes(current)})"
                   + (f"; {_minutes(later)} after it" if later >= 30 else ""))
        else:
            eta = _minutes(max(0.0, current - step_elapsed) + later)
    else:
        pct = int(done / total * 100) if total else 0
        if typical and elapsed < typical - 60:
            eta = _minutes(typical - elapsed)
        elif typical:
            eta = f"running longer than usual (runs usually take {_minutes(typical)})"
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
    typical_steps = db.typical_step_seconds(total) if hasattr(db, "typical_step_seconds") else None
    bar, pct, eta = progress_figures(step, total, now - start, typical_run_seconds(), step_elapsed, typical_steps)
    return bar, pct, eta, int(max(0, now - start)), step_elapsed


def step_running_note(step_elapsed: Optional[float]) -> str:
    """' · 5:12 on this step' once a step has run a minute, so a long step is visibly still working."""
    if step_elapsed is None or step_elapsed < 60:
        return ""
    s = int(step_elapsed)
    return f" · {s // 60}:{s % 60:02d} on this step"


def typical_run_seconds(last: int = 5) -> Optional[float]:
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
            totals.append((max(stamps) - min(stamps)).total_seconds() + float(rows[0]["seconds"]))
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
        if len(totals) == last:
            break
    return sum(totals) / len(totals) if totals else None


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
        self._panel_revisions = {}
        self._panel_aliases = {}
        self._panel_media = {}
        # The screen (callback) each panel shows, and the one it showed before the latest tap: a job a
        # button starts returns to the screen that button was on.
        self._panel_screen: Dict[int, str] = {}
        self._panel_origin: Dict[int, Optional[str]] = {}
        self._panel_lock = threading.RLock()
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

    def show_dashboard(self, text=None, buttons=None, fresh=False):
        """One dashboard anchor survives /start and routine process reloads. Serialized: two refreshes at
        once (production's startup card and a queued Dashboard tap) each found the stored panel gone and
        each posted one; under the lock the second edits the panel the first posted."""
        with self._panel_lock:
            return self._show_dashboard_locked(text, buttons, fresh)

    def _show_dashboard_locked(self, text, buttons, fresh):
        from .control import dashboard_text_and_buttons
        from .config import ASSETS_DIR
        if text is None:
            text, buttons = dashboard_text_and_buttons(self.db, getattr(self, "next_run_func", lambda: None)(), assets_dir=ASSETS_DIR)
        stored = self.db.get_setting("telegram_dashboard_id")
        previous = int(stored) if stored and stored.isdigit() else None
        if previous and not fresh:
            current = self._current_panel(previous)
            self._panel_revisions[current] = self._panel_revisions.get(current, 0) + 1
            if self.show_navigation_panel(current, text, buttons):
                self.db.set_setting("telegram_dashboard_id", str(self._current_panel(current)))
                return self._current_panel(current)
        message_id = self.notify(text, buttons=buttons, record_alert=False)
        if type(message_id) is int:
            self.db.set_setting("telegram_dashboard_id", str(message_id))
            self._panel_media[message_id] = False
            # One panel in the chat: the one this replaces goes (a review card is never the panel).
            if previous and self._current_panel(previous) != message_id:
                self._delete_soon(self._current_panel(previous))
        return message_id

    def _typed_panel(self):
        """A typed command opens the one panel at the bottom of the chat and runs in it, as a tap would."""
        with self._panel_lock:
            message_id = self._show_dashboard_locked("⏳ <b>Working…</b>", [[DONE_BUTTON]], fresh=True)
        if type(message_id) is not int:
            return None
        return message_id, False, self._panel_revisions.get(message_id, 0)

    def show_navigation_panel(self, message_id, text, buttons=None, is_media=False):
        """A text screen must not keep an unrelated cover/audio attachment."""
        from .telegram_pages import panel as paginate
        text, buttons = paginate(self.db, _MASKER.mask(text), buttons)
        current = self._current_panel(message_id)
        media = self._panel_media.get(current, is_media)
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
            return edited
        result = self._api("sendMessage", data={"chat_id": self.chat_id,
            "text": safe_html_truncate(_MASKER.mask(text), 4000), "parse_mode": "HTML",
            "disable_web_page_preview": "true", "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []})})
        new_id = result["message_id"]
        if new_id == current:
            raise RuntimeError("Telegram returned an invalid replacement message identity")
        stored = self.db.get_setting("telegram_dashboard_id")
        anchor = self._current_panel(int(stored)) if stored and stored.isdigit() else None
        self._panel_aliases[current] = new_id
        self._panel_media[new_id] = False
        self._panel_revisions[new_id] = self._panel_revisions.get(current, 0)
        for screens in (self._panel_screen, self._panel_origin):  # the same panel, a new message
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
        return self.notify(text, buttons=buttons, record_alert=False)

    def start_operator_job(self, title, work, resource="", navigation=None, late=False):
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
                mid = self.notify(text, buttons=buttons, record_alert=False)
                if type(mid) is int:
                    self._panel_media[mid] = False
                    destination[0] = (mid, False, self._panel_revisions.get(mid, 0))
        def complete(job, result):
            if job["state"] == "completed":
                if result is None:
                    return None  # the work drew its own screen (a photo or audio panel): shown, not lost
                text, buttons = result if isinstance(result, tuple) else (str(result or "Completed."), [[DONE_BUTTON]])
            else:
                text = f"{'⏹' if job['state'] == 'cancelled' else '⚠️'} <b>{html.escape(title)}</b>: {job['state']}.\nExisting approved assets and usage records are preserved."
                buttons = [[{"text": "Jobs", "callback_data": "jobs:"}], *up, [DONE_BUTTON]]
                if job["state"] == "failed" and navigation and resource.partition(":")[0] in PANEL_ACTIONS:
                    buttons.insert(0, [{"text": "Try again", "callback_data": resource}])
            if job.get("error_detail"):
                text += "\n" + html.escape(job["error_detail"]) + "\nInspect /jobs before explicitly retrying."
            delivered = self.update_navigation_target(destination[0], text, buttons)
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
            buttons.append([{"text": f"✖ Cancel {job['title'][:24]}", "callback_data": f"jobcancel:{job['id']}"}])
        for run in runs:
            lines.append(f"🎬 {e(self.db.run_label(run[0]))} · producing")
            buttons.append([{"text": "✖ Cancel the run after its current stage", "callback_data": f"runstop:{self.db.run_key(run[0])}"}])
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
                        ["CC0", "CC BY 4.0", "Public domain"] + (["CC BY-SA 4.0"] if config.IMAGES_ALLOW_SHARE_ALIKE else []))
            buttons.insert(0, [{"text": licence, "callback_data": f"uploadlic:{state['id']}:{licence}"} for licence in licences])
        text = f"🖼 <b>Upload: {html.escape(state.get('title', state['topic']))}</b>\n\n{html.escape(PROMPTS[stage])}"
        if note:
            text = html.escape(note) + "\n\n" + text
        if not self.show_navigation_panel(state["message_id"], text, buttons):
            message_id = self.notify(text, buttons=buttons, record_alert=False)
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
            # Flood control (HTTP 429): wait as told, briefly, then retry. Uploads are not resent (their
            # file handles are consumed), and a long wait is left to the caller.
            if method != "answerCallbackQuery" and resp.status_code == 429 and retry_after and retry_after <= 30 and attempt < 2 and "files" not in kwargs:
                log.info("Telegram %s rate-limited; retrying in %ss.", method, retry_after)
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
            return result
        raise RuntimeError(f"Telegram {method} failed: still rate-limited")

    @staticmethod
    def release_lines(report: Optional[Dict[str, Any]]) -> str:
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
        if not open_checks:
            line = "🚦 <b>Release gate:</b> ✅ all checks pass\n"
        else:
            # What was found, in its own words (the card said "visual_checks: fail (… (>1.2s)'})" and
            # Approve led to a failure notice: 2026-10-24 production run).
            from .release import blocking_checks, check_problems
            blocked = bool(blocking_checks(report, dry_run=report.get("dry_run", False)))
            found = "\n".join(f"• {e(c['name'])}: {e(p)}" for c in open_checks for p in check_problems(c))
            line = ("⛔ <b>Release gate:</b> blocked. This version can't be posted: Edit, Regenerate or Reject it.\n"
                    if blocked else "⚠️ <b>Release gate:</b> needs review before a live post.\n")
            line += f"<blockquote expandable>{found}</blockquote>\n"
        if measured:
            line += f"📏 <b>Measured:</b> <code>{e(' · '.join(measured))}</code>\n"
        return line

    @staticmethod
    def format_card_text(script_payload: Dict[str, Any], run_id: str, release: Optional[Dict[str, Any]] = None) -> str:
        """HTML review card (Telegram message limit is 4,096 characters)."""
        e = html.escape
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

        text = (
            f"🎬 <b><u>MASTER REVIEW</u></b>\n\n"
            f"📌 <b>{e(script_payload.get('title', ''))}</b>\n"
            + (f"🔄 <i>Regenerated · your feedback: {e(regen)}</i>\n" if regen else "")
            + (f"📍 <i>{place}</i>\n" if place else "") +
            f"⏱️ {script_payload.get('word_count', 0)} words · source: <i>{e(script_payload.get('origin', '?'))}</i>\n"
            f"{integrity}\n"
            + _second_check_line(script_payload.get("second_check"))
            + TelegramApprovalBot.release_lines(release) + "\n"
            "<blockquote expandable><b>Narration and timings:</b>\n<i>\x00NARRATION\x00</i>\n\n"
            f"<b>First comment:</b> <i>{e(script_payload.get('first_comment', ''))}</i>\n"
            # Everything Approve publishes is readable here (the caption and carousel caption were only
            # visible in the Edit template: 2026-10-23 UI review). Inside the collapsed section.
            # Filled last from whatever room the full narration leaves (format_card_text's end); Edit always
            # has the whole text.
            + ("\x00CAPTION\x00" if script_payload.get("caption") else "")
            + ("\x00CAROUSEL\x00" if script_payload.get("carousel_caption") else "")
            + (f"<b>Hashtags:</b> {e(' '.join(script_payload['hashtags']))}" if script_payload.get("hashtags")
               else f"<b>Tags:</b> {e(', '.join(script_payload.get('tags') or []))}") + "</blockquote>\n"
        )
        poll = script_payload.get("poll") or {}
        if poll.get("question") and poll.get("options") and len(poll["options"]) >= 2:
            opts = [e(str(o)) for o in poll["options"]]
            text += f"📊 <b>Debate poll:</b> {e(poll['question'])} (A: {opts[0]} / B: {opts[1]})\n"
        if script_payload.get("is_generated_by_ai"):
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
        if script_payload.get("voice_note"):
            text += "\n" + _format_note(script_payload["voice_note"])
        if script_payload.get("music_note"):
            text += "\n" + _format_note(script_payload["music_note"])
        if script_payload.get("motion_note"):
            text += "\n" + _format_note(script_payload["motion_note"])
        if script_payload.get("graphics_note"):
            text += "\n" + _format_note(script_payload["graphics_note"])
        if script_payload.get("footage_note"):
            text += "\n" + _format_note(script_payload["footage_note"])
        if script_payload.get("beat_image_note"):
            text += "\n" + _format_note(script_payload["beat_image_note"])
        if script_payload.get("source_note"):
            text += "\n" + _format_note(script_payload["source_note"]) + "\n"
        if script_payload.get("qa_summary"):
            text += "\n" + script_payload["qa_summary"] + "\n"  # already HTML-escaped by video_qa
        if sources:
            text += "\n📚 " + "\n📚 ".join(e(s.get("url", "")) for s in sources) + "\n"
        full = text + f"\n<i>Run ID (for the command line): {e(run_id)}</i>"
        # A card over the page limit is paged as plain text and loses its collapsible script, so a long
        # (documentary) narration is shortened inside it instead; the full script is in the run folder.
        narration = script_payload.get("narration_script", "")
        bare = full.replace("\x00CAPTION\x00", "").replace("\x00CAROUSEL\x00", "")
        room = CARD_PAGE_LIMIT - _tg_len(bare.replace("\x00NARRATION\x00", ""))
        if _tg_len(e(narration)) > room:
            note = f" … [shortened: all {len(narration.split())} words are in the run's script_metadata.json]"
            cut = narration
            while cut and _tg_len(e(cut)) + _tg_len(note) > room:
                cut = cut[:-40].rsplit(" ", 1)[0]
            narration = cut + note
        full = full.replace("\x00NARRATION\x00", e(narration), 1)
        # The captions take only the room the narration leaves (adding them in full shortened the narration
        # of real cards: 2026-10-23 UI review); what does not fit says where the whole of it is.
        for key, label, marker in (("caption", "Caption", "\x00CAPTION\x00"),
                                   ("carousel_caption", "Carousel caption", "\x00CAROUSEL\x00")):
            if marker not in full:
                continue
            spare = CARD_PAGE_LIMIT - _tg_len(full.replace("\x00CAPTION\x00", "").replace("\x00CAROUSEL\x00", ""))
            head = f"\n<b>{label}:</b>\n"
            text_value = (script_payload.get(key) or "").strip()
            block = head + e(text_value) + "\n"
            if _tg_len(block) > spare:
                fit = spare - _tg_len(head) - 30
                block = head + (e(_clip_copy(text_value, fit)) if fit >= 120 else "<i>(too long for this card: see Edit)</i>") + "\n"
                if _tg_len(block) > spare:
                    block = ""
            full = full.replace(marker, block, 1)
        return full

    def send_approval_request(self, video_path: Path, thumbnail_path: Path, script_payload: Dict[str, Any], run_id: str) -> Optional[int]:
        """Everything one Approve publishes, then the card: an album with the cover, a scene strip and
        the review video; an album with the Story frames and carousel slides; the review card.
        Review media is protected (no forwarding or saving of unreleased work)."""
        previous = self.db.get_approval(run_id) if hasattr(self.db, "get_approval") else None
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), None)
        from .telegram_state import put
        put(self.db, "review-media", run_id, None)
        if not self.is_configured():
            log.warning("Telegram not configured. Review with: python main.py review approve|reject %s", run_id)
            return None
        if previous and previous.get("telegram_message_id"):
            self._strip_buttons(previous["telegram_message_id"])  # a resend leaves one live card
        release = None
        try:
            report_file = Path(video_path).parent / "release_report.json"
            release = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
        except (OSError, ValueError) as e:
            log.warning("Could not read the release report for the review card: %s", e)
        from .telegram_state import review_fingerprint, put, get as get_state
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
                        record_alert=True)
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
            put(self.db, "review-messages", run_id, media_ids + [card])
            stale = sorted(set(old) - set(media_ids) - {card})
            if stale:
                self.delete_messages(stale)
        return card

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
            if len(media) == 1:
                kind = media[0]["type"]
                sent = self._api("send" + kind.capitalize(), timeout=300, files={kind: files["f0"]},
                                 data={"chat_id": self.chat_id, "caption": media[0].get("caption", ""), "parse_mode": "HTML",
                                       "protect_content": "true"})
            else:
                sent = self._api("sendMediaGroup", timeout=300, files=files,
                                 data={"chat_id": self.chat_id, "media": json.dumps(media), "protect_content": "true"})
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
        stills = sorted((run_dir / "stories").glob("story_*.jpg")) + sorted((run_dir / "carousel").glob("carousel_slide_*.jpg"))
        n_story = len(list((run_dir / "stories").glob("story_*.jpg")))
        for start in range(0, len(stills), 10):
            batch = stills[start:start + 10]
            items = [{"type": "photo", "path": p} for p in batch]
            if start == 0:
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
        card_text = self.format_card_text({**script_payload, "_regeneration_note": regen} if regen else script_payload,
                                          run_id, release=release)
        # A version the release gate blocks is not offered for approval: Approve could only end in
        # "Release blocked" (2026-10-24). Edit, Regenerate and Reject stay; a corrected version gets a new card.
        from .release import blocking_checks
        blocked = bool(release) and bool(blocking_checks(release, dry_run=release.get("dry_run", False)))
        markup = {"inline_keyboard": format_responsive_keyboard([
            ([] if blocked else [{"text": "🚀 Approve & Post", "callback_data": f"approve:{key}"}])
            + [{"text": "✏️ Edit text", "callback_data": f"edit:{key}"}],
            [{"text": "🔄 Regenerate", "callback_data": f"regen:{key}"},
             {"text": "❌ Reject", "callback_data": f"reject:{key}"}],
        ] + ([] if script_payload.get("delivery_profile") == "deep_dive_16x9"  # 9:16 covers don't fit a documentary
             else [[{"text": "🎨 Covers", "callback_data": f"ccov:{key}"}]]))}
        from .telegram_pages import panel as paginate
        card_text, rows = paginate(self.db, card_text, markup["inline_keyboard"])
        markup["inline_keyboard"] = rows
        try:
            msg = self._api("sendMessage", data={"chat_id": self.chat_id, "text": card_text, "parse_mode": "HTML",
                                                 "disable_web_page_preview": "true", "reply_markup": json.dumps(markup)})
        except Exception as e:
            if "can't parse entities" not in str(e).lower() and "bad request" not in str(e).lower():
                raise
            log.warning("Telegram parse error on approval card (%s); retrying plain text", e)
            msg = self._api("sendMessage", data={"chat_id": self.chat_id, "text": re.sub(r"<[^>]+>", "", card_text)[:4000],
                                                 "disable_web_page_preview": "true", "reply_markup": json.dumps(markup)})
        message_id = msg["message_id"]
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
        with self._panel_lock:
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
        target = decode(self.db, cq.get("data", "")).partition(":")[2]
        resolved = self.db.resolve_run(target)
        try:
            with publication_lock(self.db.db_path, resolved or "unknown-review"):
                return self._handle_callback_locked(cq, run_id)
        except ValueError:
            self._answer_callback(cq["id"], "Review is busy or incomplete. Refresh before deciding.")
            return None

    def _handle_callback_locked(self, cq: Dict[str, Any], run_id: Optional[str] = None) -> Optional[str]:
        """Applies an approval button press. With run_id, ignores presses for other runs."""
        from .telegram_state import decode
        action, _, target = decode(self.db, cq.get("data", "")).partition(":")
        if action not in ACTIONS or (run_id is not None and self.db.resolve_run(target) != run_id):
            return None
        run_id = self.db.resolve_run(target)
        user_id = str(cq.get("from", {}).get("id", ""))
        msg = cq.get("message") or {}
        if not self._authorized(user_id, msg.get("chat", {}).get("id", "")):
            log.warning("Ignoring approval click from unauthorized user %s.", user_id)
            self._answer_callback(cq["id"], "Not authorized.")
            return None
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
            updated_text = self.format_card_text(run["script_payload"], run_id) + badge
        elif orig_text:
            updated_text = html.escape(orig_text) + badge
        else:
            updated_text = f"Decision: <b>{html.escape(decision.upper())}</b> for {html.escape(self.db.run_label(run_id))}"
        try:
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": msg_id,
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })
            if msg_id:
                self.edit_message_text(msg_id, updated_text)
            else:
                self._api("sendMessage", data={"chat_id": self.chat_id, "parse_mode": "HTML", "text": updated_text})
        except Exception as e:
            log.warning("Could not update Telegram card: %s", e)
        if decision in ("rejected", "regenerate"):
            self.ask_reason(run_id, decision)

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
        from .control import LAST_REVIEW_KEY
        from .telegram_state import get, put
        receipt = (f"<b>{html.escape(self.db.run_label(run_id))}</b>: {outcome}\n"
                   f"<i>Run ID: {html.escape(run_id)}</i>")
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
                    buttons=buttons, record_alert=False)

    # ------------------------------------------------------------ notifications
    def notify(self, text: str, buttons: Optional[list] = None, transient: bool = False, ttl_minutes: int = 60, record_alert: bool = True, silent: bool = False) -> bool:
        """Sends an operational message (HTML), optionally with inline buttons (single or multi-row).
        Never raises: alerts must not break a run."""
        panel = _PANEL.get()
        if panel and panel[0] is self:
            if len(panel) > 4 and self._panel_revisions.get(self._current_panel(panel[1]), 0) != panel[4]:
                panel[3] = True
                return False
            panel[3] = True
            # Every screen a command draws keeps a way back to the dashboard (a command's own button
            # list, such as /bump's topics, often had none).
            return self.show_navigation_panel(panel[1], text, is_media=panel[2], buttons=navigation_buttons(buttons))
        if not self.is_configured():
            log.info("Notification (Telegram not configured): %s", text)
            if record_alert and hasattr(self.db, "record_alert"):
                self.db.record_alert(text, message_id=None, kind="transient" if transient else "persistent")
            return False
        text = _MASKER.mask(text)  # error details can carry a credential; the chat never sees one
        if not buttons:  # no message is a dead end: one tap back to the panel
            buttons = [DONE_BUTTON]
        from .telegram_pages import panel as paginate
        text, buttons = paginate(self.db, text, buttons)
        truncated_text = text
        data = {"chat_id": self.chat_id, "text": truncated_text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
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

    def send_progress_card(self, run_id: str, topic_title: str) -> Optional[int]:
        """Sends an initial progress card for a running pipeline job, returning message_id."""
        if not self.is_configured():
            return None
        self._progress_start_time[run_id] = time.time()
        bar, pct, eta = progress_figures(1, 5, 0, typical_run_seconds(), 0.0,
                                         self.db.typical_step_seconds(5) if hasattr(self.db, "typical_step_seconds") else None)
        text = (
            f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n"
            f"<b>{html.escape(topic_title)}</b> · started {show_time(None, '%H:%M')}\n\n"
            f"🔄 <b>Step 1/5:</b> <i>{PROGRESS_STEPS[0]}...</i>\n"
            + "".join(f"◽ <b>Step {n}/5:</b> {m}\n" for n, m in enumerate(PROGRESS_STEPS[1:], start=2)) + "\n"
            f"Progress: <code>[{bar}] {pct}%</code>\n"
            f"⏱ <b>Elapsed:</b> 00:00 · <b>Time left:</b> {eta}"
        )
        text = self.progress_mode(run_id) + "\n\n" + text
        buttons = [{"text": "🔄 Refresh Progress", "callback_data": f"cprog:{self.db.run_key(run_id)}"}, {"text": "Cancel run", "callback_data": f"runstop:{self.db.run_key(run_id)}"}]
        markup = {"inline_keyboard": format_responsive_keyboard(buttons)}
        try:
            msg = self._api("sendMessage", data={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(markup),
            })
            mid = msg.get("message_id") if isinstance(msg, dict) else None
            if mid:
                from .telegram_state import put
                put(self.db, "message-run", f"{self.chat_id}:{mid}", run_id)
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
        self.update_progress_card(old, active.get("step", 1), active.get("total_steps", 5), active.get("topic_title", ""),
                                  active["run_id"], active.get("details", ""))
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

    def update_progress_card(self, message_id: int, step: int, total_steps: int, topic_title: str, run_id: str, details: str = "") -> bool:
        """Updates the progress card in-place with smooth bar, active sub-step, and ETA debouncing."""
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
            f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n<b>{html.escape(topic_title)}</b> · started {show_time(self._progress_start_time.get(run_id), '%H:%M')}",
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

        lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
        lines.append(f"⏱ <b>Elapsed:</b> {elapsed_sec // 60:02d}:{elapsed_sec % 60:02d} · <b>Time left:</b> {eta}")

        lines.insert(1, self.progress_mode(run_id))
        text = "\n".join(lines)
        self._last_progress_edit[message_id] = now
        self._last_progress_step[message_id] = step

        buttons = [{"text": "🔄 Refresh Progress", "callback_data": f"cprog:{self.db.run_key(run_id)}"}, {"text": "Cancel run", "callback_data": f"runstop:{self.db.run_key(run_id)}"}]

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

        success = self.edit_message_text(message_id, text, buttons=buttons)
        if not success and message_id in self._missing_messages:
            # Self-healing: if the card was purged/cleared from Telegram, recreate it!
            try:
                msg = self._api("sendMessage", data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons)}),
                })
                new_mid = msg.get("message_id") if isinstance(msg, dict) else None
                if new_mid:
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
        """Finishes the progress card into a completed receipt, or deletes it if video review was sent."""
        # The live card, not the one the run started with: Refresh Progress moves the card to the bottom (a
        # new message each time), and the run's own id then points at a deleted one. Read before clearing.
        message_id = self._live_progress_card(run_id) or message_id
        if hasattr(self.db, "clear_active_progress"):
            self.db.clear_active_progress(run_id)
        if not self.is_configured() or not message_id:
            return False
        if not success:
            run = self.db.get_run(run_id)
            if run and run.get("status") == "cancelled":
                text = (f"⏹ <b>Render cancelled:</b> {html.escape(topic_title)}\n"
                        f"<code>[{html.escape(self.db.run_label(run_id))}]</code>\n\n"
                        "Stopped at a stage boundary. Existing assets and incurred usage are retained.")
                return self.edit_message_text(message_id, text, buttons=[[DONE_BUTTON]])
            # The run's one failure card (the separate "Production failed" alert is not sent as well).
            from .control import short_reason
            # The whole reason, folded (as on the dashboard): 600 characters cut a release finding mid-way.
            late = bool(run and run.get("master_video_path"))
            text = (f"❌ <b>Production failed:</b> {html.escape(self.db.run_label(run_id))}\n\n"
                    f"<blockquote expandable>{html.escape(short_reason(error, 1200))}</blockquote>\n\n"
                    + ("Nothing was posted. Re-render plans its scenes again and re-checks it, reusing its "
                       "script, voiceover and accepted motion clips.\n\n" if late else
                       "Nothing was posted. The topic will be retried on a later run.\n\n")
                    + "<i>Reply to this card with a question to ask about this run.</i>")
            buttons = [[DONE_BUTTON]]
            if late:  # it got as far as a video (the release gate, say), so it can be made again in place
                buttons.insert(0, [{"text": "🔁 Re-render", "callback_data": f"rerender:{self.db.run_key(run_id)}"}])
            return self.edit_message_text(message_id, text, buttons=buttons)
        else:
            return self.delete_message(message_id)

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

    def edit_message_text(self, message_id: int, text: str, buttons: Optional[list] = None) -> bool:
        """Edits an existing Telegram message in-place (HTML) and updates or removes buttons."""
        if not self.is_configured() or not message_id:
            return False
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
        try:
            self._api("editMessageText", data=data)
            return True
        except Exception as e:
            err_msg = str(e).lower()
            if "message to edit not found" in err_msg:
                self._missing_messages.add(message_id)
            if "message is not modified" in err_msg:
                return True
            if "can't parse entities" in err_msg:
                log.warning("Telegram parse error in edit_message_text (%s); falling back to plain text", e)
                try:
                    fallback_data = dict(data)
                    fallback_data.pop("parse_mode", None)
                    fallback_data["text"] = re.sub(r"<[^>]+>", "", text)[:4000]
                    self._api("editMessageText", data=fallback_data)
                    return True
                except Exception as e2:
                    log.warning("Fallback plain text edit also failed: %s", e2)
            log.warning("Could not edit message text %s: %s", message_id, e)
            return False

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
            if cleaned:
                log.info("Cleaned up %d stale/timed-out approval(s) from Telegram.", cleaned)
        except Exception as e:
            log.debug("Error in cleanup_stale_approvals: %s", e)
        return cleaned

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

    def send_audio(self, path: Path, caption: str, buttons: Optional[list] = None) -> bool:
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
            self._panel_media[panel[1]] = True
            return True
        if not self.is_configured():
            return False
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
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
        buttons = [
            [
                {"text": f"{'✓' if var_letter == 'a' else '📌'} A: Blueprint", "callback_data": f"cov:{run_name}:a"},
                {"text": f"{'✓' if var_letter == 'b' else '📌'} B: Intrigue", "callback_data": f"cov:{run_name}:b"},
            ],
            [
                {"text": f"{'✓' if var_letter == 'c' else '📌'} C: Minimal", "callback_data": f"cov:{run_name}:c"},
            ],
            [
                {"text": f"🔒 Lock Variant {var_letter.upper()} ({style_name})", "callback_data": f"covlock:{run_name}:{var_letter}"}
            ],
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

    def send_photo(self, path: Path, caption: str, buttons: Optional[list] = None, cache_key: Optional[str] = None) -> Any:
        """Sends an image with an HTML caption (max 1,024 chars) and optional inline buttons."""
        panel = _PANEL.get()
        if panel and panel[0] is self:
            if len(panel) > 4 and self._panel_revisions.get(self._current_panel(panel[1]), 0) != panel[4]:
                panel[3] = True
                return False
            panel[3] = True
            if not self.edit_message_media(panel[1], path, caption, navigation_buttons(buttons), cache_key):
                raise RuntimeError("Could not update the media panel")
            self._panel_media[panel[1]] = True
            return True
        if not self.is_configured():
            return False
        cached_id = self.db.get_cached_telegram_media(cache_key) if cache_key else None
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
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

    def drain(self) -> None:
        """Waits for queued navigation and background commands to finish (shutdown, tests). Navigation
        work can queue more navigation, so the pools are drained until both are empty."""
        while True:
            with self._panel_lock:
                pools = [p for p in (self._navigation_pool, self._workers, self._cleanup) if p is not None]
                self._navigation_pool = self._workers = self._cleanup = None
            if not pools:
                return
            for pool in pools:
                pool.shutdown(wait=True)

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

    def poll_once(self, command_handler: Optional[Callable[[str, str], str]] = None, timeout: int = 25) -> None:
        """One long-poll: applies approval clicks for any run and answers authorized /commands."""
        if time.monotonic() - self._last_maintenance > 300:
            self._last_maintenance = time.monotonic()
            self._queue_navigation(lambda: (self.cleanup_transient_messages(), self.cleanup_stale_approvals()))
        with self._panel_lock:
            if not self.jobs.active:
                for cache in (self._panel_aliases, self._panel_media, self._panel_revisions,
                              self._last_progress_edit, self._last_progress_step, self._progress_start_time):
                    while len(cache) > 512:
                        cache.pop(next(iter(cache)))
        # Updates that arrived while the bot was down are processed, not skipped: an Approve tap
        # during a restart must not be lost (decisions are first-wins, so a replay is harmless).
        # Only commands older than STALE_COMMAND_SECONDS are dropped, so a day-old /run doesn't fire.
        data = {"timeout": timeout, "allowed_updates": json.dumps(["callback_query", "message"])}
        if self._offset is not None:
            data["offset"] = self._offset
        updates = self._api("getUpdates", timeout=timeout + 10, data=data)
        from .telegram_state import admit, decode
        for update in updates:
            try:
                self._offset = max(self._offset or 0, update["update_id"] + 1)
                if not admit(self.db, self.bot_token.split(":")[0], update["update_id"]):
                    continue
                self._offset = max(self._offset or 0, update["update_id"] + 1)
                cq = update.get("callback_query")
                if cq:
                    cq["data"] = decode(self.db, cq.get("data", ""))
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from",{}).get("id"), message.get("chat",{}).get("id")) and message.get("message_id"):
                        with self._panel_lock:
                            mid = self._current_panel(message["message_id"])
                            self._panel_revisions[mid] = self._panel_revisions.get(mid, 0) + 1
                            cq["_revision"] = self._panel_revisions[mid]
                            if cq.get("data") and not cq["data"].startswith(("jobcancel:", "jobs:")):
                                self._panel_origin[mid] = self._panel_screen.get(mid)  # where this button was
                                self._panel_screen[mid] = cq["data"]
                            self._panel_media[mid] = bool(message.get("photo") or message.get("audio") or message.get("video") or message.get("document"))
                msg = update.get("message") or {}
                action = (cq or {}).get("data", "").partition(":")[0]
                media_upload = msg.get("caption") and any(msg.get(k) for k in ("photo","document","video","audio","voice"))
                if (media_upload and self._authorized(msg.get("from",{}).get("id"), msg.get("chat",{}).get("id"))) or action in {"cov","covlock"}:
                    if cq:
                        self._answer_callback(cq["id"], "Working")
                    self._submit_update(update, command_handler)
                elif cq and action not in ACTIONS:
                    message = cq.get("message") or {}
                    if self._authorized(cq.get("from",{}).get("id"), message.get("chat",{}).get("id")):
                        if action == "jobcancel":
                            self.jobs.cancel(cq["data"].partition(":")[2])
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
                    if self.show_navigation_panel(msg['message_id'], dash_text, dash_buttons, is_media):
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
                        self.show_navigation_panel(msg_id, 'Upload cancelled. Approved assets are unchanged.', [[{'text': '← Images', 'callback_data': 'cimages:'}], [DONE_BUTTON]])
                    else:
                        if action == 'upload':
                            state = wizard.begin(chat_id, user_id, target, msg_id)
                        elif action == 'uploadown':
                            state = wizard.own_work(chat_id, user_id, expected_id=target)
                        else:
                            state = wizard.answer(chat_id, user_id, target.partition(':')[2], expected_id=target.partition(':')[0], expected_stage='licence')
                        if state.get('stage') == 'complete':
                            self.show_navigation_panel(msg_id, 'Image saved. Review the set before use.', [[{'text': 'Review set', 'callback_data': f"review:{state['topic']}"}], [{'text': '← Topic images', 'callback_data': f"imagehelp:{state['topic']}"}], [DONE_BUTTON]])
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
                        self.show_navigation_panel(msg_id, html.escape(str(exc)), [[{'text': '← Images', 'callback_data': 'cimages:'}], [DONE_BUTTON]])
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
                if action in {'jobs', 'jobcancel'}:
                    note = ''
                    if action == 'jobcancel':
                        note = '⏹ Cancellation requested. An in-flight request may finish.\n\n' if self.jobs.cancel(target) else 'This job is no longer owned by this bot process.\n\n'
                    text, buttons = self.jobs_view()
                    self.show_navigation_panel(msg_id, note + text, buttons, is_media)
                    self._answer_callback(cq['id'], 'Updated')
                    return
                if action == 'done':
                    if msg_id:
                        self.delete_message(msg_id)
                    self._answer_callback(cq['id'], 'Closed')
                    return
                if action in {'fetchimages', 'caccounts', 'mfetch', 'cstat', 'ccov', 'cmusic', 'mreview', 'review', 'cdisc', 'cprop', 'uadd', 'tadd', 'edittext', 'mkeep', 'mdel', 'mnext', 'mreview', 'mlibrary'}:
                    self._answer_callback(cq['id'], 'Working; you can keep using the bot')
                    revision = cq.get('_revision', self._panel_revisions.get(self._current_panel(msg_id), 0))
                    if action == 'fetchimages':
                        self.edit_message_any(msg_id, '🖼 Fetching images. You can keep using the bot.', is_media=is_media, buttons=[[DONE_BUTTON]])
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
                    panel = [self, msg_id, is_media, False]
                    token = _PANEL.set(panel) if msg_id and action in PANEL_ACTIONS else None
                    try:
                        reply = command_handler(action, target)
                    finally:
                        if token is not None:
                            _PANEL.reset(token)
                    if token is not None and panel[3]:
                        reply = ''
                    self._answer_callback(cq['id'], 'Request checked')
                    if isinstance(reply, tuple):
                        text, buttons = reply
                        if msg_id and action not in DELETE_FIRST:
                            self.show_navigation_panel(msg_id, text, is_media=is_media, buttons=buttons)
                        else:
                            self.notify(text, buttons=buttons, record_alert=False)
                    elif action == 'cdash' and msg_id:
                        from .control import dashboard_text_and_buttons
                        from .config import ASSETS_DIR
                        next_fn = getattr(self, 'next_run_func', lambda: None)
                        dash_text, dash_buttons = dashboard_text_and_buttons(self.db, next_fn(), assets_dir=ASSETS_DIR)
                        self.show_navigation_panel(msg_id, dash_text, is_media=is_media, buttons=dash_buttons)
                    elif action in EDIT_IN_PLACE and msg_id:
                        self.edit_message_text(msg_id, reply, buttons=[[DONE_BUTTON]])
                    elif reply and action in ('imgok', 'mkeep', 'cov') and msg_id:
                        self.edit_message_any(msg_id, reply, is_media=is_media)
                    elif action in DELETE_FIRST:
                        pass
                    elif reply:
                        if msg_id and action in PANEL_ACTIONS:
                            self.show_navigation_panel(msg_id, reply, is_media=is_media, buttons=[[DONE_BUTTON]])
                        else:
                            self.notify(reply, buttons=[[DONE_BUTTON]], record_alert=False)
                except Exception as e:
                    self._answer_callback(cq['id'], 'Failed')
                    if action in PANEL_ACTIONS:
                        log.warning('Navigation %s failed: %s', action, _MASKER.mask(str(e)))
                        label = {'ccov': 'Covers', 'cmusic': 'Music', 'mreview': 'music review'}.get(action, 'this screen')
                        text = f'⚠️ Could not open {label}. Please try again.'
                        buttons = [[{'text': 'Try again', 'callback_data': f'{action}:{target}'}], [DONE_BUTTON]]
                        if not self.show_navigation_panel(msg_id, text, buttons, is_media):
                            self.notify(text, buttons=buttons, record_alert=False)
                    else:
                        self.notify(f'⚠️ {html.escape(action)} failed: {html.escape(str(e))[:300]}')
            return
        msg = update.get('message') or {}
        if self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
            chat, user = (msg['chat']['id'], msg['from']['id'])
            wizard = self.image_wizard()
            state = wizard.load(chat, user)
            text = (msg.get('text') or '').strip()
            image = (msg.get('photo') or [None])[-1] or msg.get('document')
            if text.startswith('/images upload ') or (text.startswith('/image ') and '|' not in text):
                ref = text.split(' ', 2)[-1] if text.startswith('/images ') else text.partition(' ')[2]
                panel = self.notify('Preparing image upload…', record_alert=False)
                try:
                    state = wizard.begin(chat, user, ref, panel)
                    self.show_upload(state)
                except ValueError as exc:
                    self.show_navigation_panel(panel, html.escape(str(exc)), [[DONE_BUTTON]])
                return
            if state and (image or (text and (not text.startswith('/')) and (not msg.get('reply_to_message')))):
                if image and state['stage'] == 'file':
                    state['_navigation_revision'] = self._panel_revisions.get(self._current_panel(state['message_id']), 0)
                    self.jobs.submit(self._background(), 'Receiving image', lambda: self.receive_guided_image(msg, image, state), lambda job, result: False, resource=f"guided-image:{chat}:{user}")
                elif image:
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
        if image_item and image_caption.split(' ', 1)[0].split('@')[0].lower() == '/image':
            if not self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
                return
            try:
                from . import config
                from .telegram_images import parse_caption, add_image
                ref, rights = parse_caption(image_caption.partition(' ')[2])
                content = self.download_image(image_item)
                topic_id = add_image(self.db, config.ASSETS_DIR, ref, rights, content, msg.get('from', {}).get('id'))
                self.notify(f'🖼 Image saved for <code>{html.escape(topic_id)}</code>. The set needs human review.', buttons=[[{'text': 'Review set', 'callback_data': f'review:{ref}'}], [DONE_BUTTON]], record_alert=False)
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self.notify(f'⚠️ Image upload failed: {html.escape(message)}', buttons=[[DONE_BUTTON]], record_alert=False)
            return
        photo_item = (msg.get('photo') or [None])[-1] or msg.get('document')
        photo_caption = (msg.get('caption') or '').strip()
        issue_waiting = bool(self._awaiting and self._awaiting[0] == 'issue' and self._awaiting[1] > time.time())
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
                return
            if word not in {'/image', '/footage', '/issue', '/critique', '/bug'} and not (msg.get('video') or msg.get('document')):
                self.show_dashboard("📎 <b>Photo received</b>, but its caption doesn't say what to do with it.\n\n"
                                    "• Report a problem: caption <code>/issue what is wrong</code>\n"
                                    "• Ask about it: caption <code>/ask your question</code>\n"
                                    "• Add it to a topic: open Images → Upload my image", navigation_buttons(None), fresh=True)
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
                        self.notify(f'✅ Video clip received and saved for <b>{html.escape(topic_dir.name)}</b>.{note}')
                except Exception:
                    log.exception('Direct video download from Telegram failed')
            return
        photos = msg.get('photo')
        media_item = (photos[-1] if photos else None) or msg.get('document') or msg.get('video') or msg.get('audio') or msg.get('voice')
        caption = (msg.get('caption') or '').strip()
        waiting_issue = bool(media_item and self._awaiting and self._awaiting[0] == 'issue' and self._awaiting[1] > time.time()
                             and not caption.startswith('/'))
        if waiting_issue:
            self._awaiting = None
            caption = '/issue ' + (caption or 'See the attached screenshot.')
        if media_item and caption.split(' ',1)[0].split('@')[0].lower() in {'/issue','/critique','/bug'} and self._authorized(msg.get('from', {}).get('id', ''), msg.get('chat', {}).get('id', '')):
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
                self.show_dashboard(f'⚠️ Failed to process issue evidence: {html.escape(str(e))[:300]}', navigation_buttons(None), fresh=True)
            return
        text = (msg.get('text') or '').strip()
        reply_to = msg.get('reply_to_message')
        if not text or command_handler is None:
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
            return
        try:
            self.db.record_audit_event(user_id=user_id, username=username, chat_id=chat_id, event_type='command', payload=audit_payload, status='allowed' if is_auth else 'denied')
        except Exception as ea:
            log.debug('Could not record command audit: %s', ea)
        if not is_auth:
            log.warning('Ignoring command from unauthorized user %s.', user_id)
            return
        if text.startswith('/'):
            from .telegram_commands import menu
            aliases = {"image": "images", "fetchimages": "fetchimages", "critique": "issue", "bug": "issue", "commands": "help"}
            public = {entry["command"] for entry in menu()} | {"start"} | set(aliases)
            if command not in public:
                if msg.get('message_id'):
                    self._delete_soon(msg['message_id'])
                self.show_dashboard(f"🤔 I don't know <code>/{html.escape(command[:40])}</code>. Help lists every command.",
                                    [[{"text": "❓ Help", "callback_data": "chelp:Everyday"}], [DONE_BUTTON]], fresh=True)
                return
            command = aliases.get(command, command)
        if command in {'start', 'dashboard'}:
            # Always a new panel at the bottom: after Telegram's "Clear history" the old one is no longer
            # visible to the operator, and editing it would leave an empty chat (and its START button) again.
            # Telegram can send /start and /dashboard together: one panel answers both.
            with self._panel_lock:
                repeat = time.monotonic() - self._last_opened < 5
                if not repeat:
                    self._last_opened = time.monotonic()
            if not repeat:
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
        if command in {"fetchimages", "mfetch"}:
            if target:
                self._run_panel_command(command_handler, command, args, target[0], False, target[2], late=True)
            else:
                self.start_operator_job("Fetching images" if command == "fetchimages" else "Finding documentary music",
                    lambda: command_handler(command, args), resource=f"{command}:{args}")
            return
        panel = [self, target[0], False, False, target[2]] if target else None
        token = _PANEL.set(panel) if panel else None
        try:
            reply = command_handler(command, args)
        except Exception as e:
            log.warning("Command /%s failed: %s", command, _MASKER.mask(str(e)))
            reply = f"⚠️ /{html.escape(command)} failed: {html.escape(str(e))[:300]}"
        finally:
            if token is not None:
                _PANEL.reset(token)
        if panel and panel[3]:
            return  # the command drew its own screen in the panel
        if reply is None:
            return  # a job the command started owns the panel now
        text, buttons = reply if isinstance(reply, tuple) else (reply, [[DONE_BUTTON]])
        if target:
            if not self.update_navigation_target(target, text or "✅ Done.", navigation_buttons(buttons)):
                self.show_dashboard(text or "✅ Done.", navigation_buttons(buttons), fresh=True)
        elif text:
            self.notify(text, buttons=buttons, record_alert=False)

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
                          reminder_after: int = 1800) -> str:
        """Returns 'approved', 'rejected', 'regenerate', or 'timeout'. Never auto-approves. One reminder
        after `reminder_after` seconds when the window is long enough; on timeout, a Resend button."""
        timeout_seconds = timeout_seconds or APPROVAL_TIMEOUT_SECONDS
        with self.db._conn() as conn:
            conn.execute("UPDATE approvals SET expires_at=MIN(COALESCE(expires_at,?),?) WHERE run_id=?", (time.time()+timeout_seconds, time.time()+timeout_seconds, run_id))
        log.info("Awaiting human decision for %s (timeout %ss)...", run_id, timeout_seconds)
        started = time.monotonic()
        deadline = started + timeout_seconds
        reminded = timeout_seconds < reminder_after + 600  # no reminder for a short window
        offset: Optional[int] = None  # unconfirmed taps (e.g. made just before this wait) still count
        reminder: List[Any] = []  # the reminder's message, removed once the review is decided
        try:
            while time.monotonic() < deadline:
                self.cleanup_transient_messages()
                if not reminded and time.monotonic() - started >= reminder_after:
                    reminded = True
                    closes = show_time(time.time() + deadline - time.monotonic(), "%H:%M %Z")
                    reminder.append(self.notify(f"⏰ Still waiting for your review of {html.escape(self.db.run_label(run_id))} "
                                f"(the card stays open until {closes}).",
                                buttons=[{"text": "📨 Bring the card down", "callback_data": f"resend:{self.db.run_key(run_id)}"}],
                                transient=True, ttl_minutes=int((deadline - time.monotonic()) / 60) + 1))
                approval = self.db.get_approval(run_id)
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
                self.notify(f"⌛ No decision on {html.escape(self.db.run_label(run_id))}; nothing was posted. "
                            "The render is kept: resend it whenever you're ready.",
                            buttons=[{"text": "📨 Resend for review", "callback_data": f"resend:{self.db.run_key(run_id)}"}])
                return "timeout"
            return (self.db.get_approval(run_id) or {}).get("decision", "timeout")
        finally:
            if reminder and type(reminder[0]) is int:
                self._delete_soon(reminder[0])  # it stayed after the approval, offering a dead button

    def record_local_decision(self, run_id: str, decision: str) -> bool:
        """CLI decision path. Returns False if the run already has a decision."""
        if decision not in ACTIONS.values():
            raise ValueError(f"Invalid decision '{decision}'")
        applied = self.db.update_approval_decision(run_id, decision, decided_by="cli")
        if applied:
            self.dismiss_approval_buttons(run_id)
        return applied
