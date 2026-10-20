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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests

from .config import (
    APPROVAL_TIMEOUT_SECONDS,
    TELEGRAM_APPROVER_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    is_configured,
)
from .db import PipelineDatabase

log = logging.getLogger(__name__)

ACTIONS = {"approve": "approved", "regen": "regenerate", "reject": "rejected", "cancel": "cancelled"}
TELEGRAM_VIDEO_LIMIT_MB = 49.0


def _second_check_line(second: Optional[Dict[str, Any]]) -> str:
    if not second:
        return ""
    name = re.search(r"^\[([^\]]+)\]", second.get("summary", ""))
    who = f" by {name.group(1)}" if name else ""
    if second.get("status") == "pass":
        return f"✅ Second check{html.escape(who)}: passed ({second.get('confidence', 0)}% of {len(second.get('audits', []))} claims)\n"
    return "⚠️ Second check: unavailable (first check only)\n"


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
            if len(row) == 1:
                out_rows.append(row)
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


class TelegramApprovalBot:
    def __init__(
        self,
        db: Optional[PipelineDatabase] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        approver_ids: Optional[Set[str]] = None,
    ):
        self.db = db or PipelineDatabase()
        self.bot_token = bot_token if bot_token is not None else TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id if chat_id is not None else TELEGRAM_CHAT_ID
        self.approver_ids = approver_ids if approver_ids is not None else TELEGRAM_APPROVER_IDS
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._progress_start_time: Dict[str, float] = {}
        self._last_progress_edit: Dict[int, float] = {}
        self._last_progress_step: Dict[int, int] = {}

    def is_configured(self) -> bool:
        return is_configured(self.bot_token) and is_configured(self.chat_id) and bool(self.approver_ids)

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
            "caption": caption[:1024],
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
            log.warning("editMessageMedia failed: %s", e)
            return None

    def register_commands(self) -> bool:
        """Registers bot commands with Telegram for native autocomplete and the Menu button."""
        if not self.is_configured():
            return False
        commands = [
            {"command": "dashboard", "description": "Executive touch cockpit & quick actions"},
            {"command": "progress", "description": "Live production progress, ETA & render bar"},
            {"command": "status", "description": "Schedule, approvals & recent runs"},
            {"command": "queue", "description": "View queue with Bump/Drop buttons"},
            {"command": "next", "description": "Preview the next topic in queue"},
            {"command": "covers", "description": "Select 3-variant thumbnail cover"},
            {"command": "run", "description": "Start an on-demand production run"},
            {"command": "pause", "description": "Stop scheduled runs and auto-posts"},
            {"command": "resume", "description": "Re-enable scheduled production runs"},
            {"command": "propose", "description": "Research 3 new topic ideas"},
            {"command": "proposals", "description": "View pending topic proposal cards"},
            {"command": "discover", "description": "Discover UNESCO engineering marvels"},
            {"command": "music", "description": "Listen to and approve music tracks"},
            {"command": "review", "description": "Storyboard contact sheet (e.g. /review 1)"},
            {"command": "images", "description": "Image sets waiting for review"},
            {"command": "add", "description": "Propose topic manually (e.g. /add Title)"},
            {"command": "undo", "description": "Restore last dropped or skipped item"},
            {"command": "requeue", "description": "Restore an archived topic to queue"},
            {"command": "audit", "description": "Zero-Trust RBAC security audit log"},
            {"command": "issue", "description": "Log an issue/critique (attach photo/video)"},
            {"command": "ask", "description": "Probe AGY system intelligence, decisions & diagnostics"},
            {"command": "clear", "description": "Sweep and declutter chat history, restore clean cockpit"},
            {"command": "alerts", "description": "View recent system alerts and logs"},
            {"command": "help", "description": "Show all available commands"},
        ]
        try:
            self._api("setMyCommands", json={"commands": commands})
            return True
        except Exception as e:
            log.warning("Could not set Telegram commands: %s", e)
            return False

    def _api(self, method: str, timeout: float = 30, **kwargs) -> Dict[str, Any]:
        resp = requests.post(f"{self.base_url}/{method}", timeout=timeout, **kwargs)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if not resp.ok or not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: HTTP {resp.status_code} {data.get('description', '')}")
        return data["result"]

    @staticmethod
    def format_card_text(script_payload: Dict[str, Any], run_id: str) -> str:
        """HTML review card (Telegram message limit is 4,096 characters)."""
        e = html.escape
        loc = script_payload.get("location", {})
        audits = script_payload.get("fact_audits") or []
        flagged = [a for a in audits if a.get("verdict") != "supported"]
        sources = script_payload.get("sources", [])[:4]
        conf = script_payload.get("consensus_confidence", 100)
        integrity = f"🛡️ <b>Integrity Score:</b> {conf}% (Pass — {len(flagged)} hedged)" if not flagged else f"⚠️ <b>Integrity Alert:</b> {conf}% ({len(flagged)} disputed/hedged)"

        text = (
            f"🎬 <b><u>MASTER REVIEW</u></b>\n\n"
            f"📌 <b>{e(script_payload.get('title', ''))}</b>\n"
            f"📍 <i>{e(loc.get('name', ''))}, {e(loc.get('country', ''))}</i>\n"
            f"⏱️ <code>{script_payload.get('word_count', 0)} words</code> · source: <i>{e(script_payload.get('origin', '?'))}</i>\n"
            f"{integrity}\n"
            + _second_check_line(script_payload.get("second_check"))
            + "🎵 <b>Audio Mastering:</b> <code>EBU R128 (-14.0 LUFS)</code> · <i>Balanced Sub-Bass & Foley</i>\n\n"
            f"<blockquote expandable><b>Narration Script & Timings:</b>\n<i>{e(script_payload.get('narration_script', ''))}</i>\n\n"
            f"<b>First Comment:</b> <i>{e(script_payload.get('first_comment', ''))}</i>\n"
            f"<b>Hashtags:</b> {e(' '.join(script_payload.get('hashtags', [])))}</blockquote>\n"
        )
        poll = script_payload.get("poll") or {}
        if poll.get("question") and poll.get("options") and len(poll["options"]) >= 2:
            opts = [e(str(o)) for o in poll["options"]]
            text += f"📊 <b>Debate Poll:</b> {e(poll['question'])} (A: {opts[0]} / B: {opts[1]})\n"
        if script_payload.get("is_generated_by_ai"):
            text += "🤖 <b>AI Transparency:</b> Synthetic media label embedded for Meta CMP compliance\n"
        def _format_note(note_val: Any) -> str:
            if not note_val:
                return ""
            raw = str(note_val).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").strip()
            if ":" in raw:
                lbl, _, rest = raw.partition(":")
                return f"<b>{e(lbl)}:</b> {e(rest.strip())}"
            return e(raw)

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
        return safe_html_truncate(full, max_chars=3900)

    def send_approval_request(self, video_path: Path, thumbnail_path: Path, script_payload: Dict[str, Any], run_id: str) -> Optional[int]:
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), None)
        if not self.is_configured():
            log.warning("Telegram not configured. Review with: python main.py review approve|reject %s", run_id)
            return None

        try:
            with open(thumbnail_path, "rb") as photo:
                self._api("sendPhoto", data={"chat_id": self.chat_id, "caption": f"Cover · {script_payload.get('title', '')}"}, files={"photo": photo})

            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            if size_mb <= TELEGRAM_VIDEO_LIMIT_MB:
                with open(video_path, "rb") as vid:
                    self._api("sendVideo", timeout=300, data={"chat_id": self.chat_id, "caption": f"Review copy, 720p ({size_mb:.1f} MB)"},
                              files={"video": vid})
            else:
                log.warning("Video is %.1f MB, over Telegram's bot upload limit; review it locally: %s", size_mb, video_path)

            key = self.db.run_key(run_id)  # short: Telegram limits button data to 64 bytes
            topic_id = script_payload.get("topic_id", "")
            markup = {"inline_keyboard": format_responsive_keyboard([
                [
                    {"text": "🚀 Approve & Post", "callback_data": f"approve:{key}"},
                    {"text": "🎨 Flip Cover", "callback_data": f"cov:{topic_id}:b"},
                ],
                [
                    {"text": "🔄 Regenerate", "callback_data": f"regen:{key}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{key}"},
                ],
            ])}
            try:
                msg = self._api("sendMessage", data={
                    "chat_id": self.chat_id,
                    "text": self.format_card_text(script_payload, run_id),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                    "reply_markup": json.dumps(markup),
                })
                message_id = msg["message_id"]
            except Exception as e:
                if "can't parse entities" in str(e).lower() or "bad request" in str(e).lower():
                    log.warning("Telegram parse error on approval card (%s); retrying plain text", e)
                    plain_text = re.sub(r"<[^>]+>", "", self.format_card_text(script_payload, run_id))[:4000]
                    msg = self._api("sendMessage", data={
                        "chat_id": self.chat_id,
                        "text": plain_text,
                        "disable_web_page_preview": "true",
                        "reply_markup": json.dumps(markup),
                    })
                    message_id = msg["message_id"]
                else:
                    raise
            self.db.record_approval(run_id, script_payload.get("topic_id", ""), message_id)
            log.info("Approval request sent to Telegram (message %s).", message_id)
            return message_id
        except Exception as e:
            log.error("Failed to send Telegram approval request: %s. Use the CLI to review %s.", e, run_id)
            return None

    def _latest_update_id(self) -> int:
        try:
            updates = self._api("getUpdates", data={"offset": -1, "timeout": 0})
            return updates[-1]["update_id"] if updates else 0
        except Exception as e:
            log.warning("Could not read Telegram update offset: %s", e)
            return 0

    def _authorized(self, user_id: Any, chat_id: Any) -> bool:
        return str(user_id) in self.approver_ids and str(chat_id) == str(self.chat_id)

    def _answer_callback(self, cq_id: str, text: str = "") -> None:
        """Safely answers a Telegram callback query; suppresses expired query (400) errors."""
        try:
            self._api("answerCallbackQuery", data={"callback_query_id": cq_id, "text": text})
        except Exception as e:
            log.debug("answerCallbackQuery suppressed (likely expired query): %s", e)

    def _handle_callback(self, cq: Dict[str, Any], run_id: Optional[str] = None) -> Optional[str]:
        """Applies an approval button press. With run_id, ignores presses for other runs."""
        action, _, target = cq.get("data", "").partition(":")
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

        decision = ACTIONS[action]
        applied = self.db.update_approval_decision(run_id, decision, decided_by=f"telegram:{user_id}")
        if not applied:
            existing = (self.db.get_approval(run_id) or {}).get("decision", "unknown")
            self._answer_callback(cq["id"], f"⚠️ Expired/Closed ({existing})")
            try:
                self._api("editMessageReplyMarkup", data={
                    "chat_id": self.chat_id, "message_id": msg.get("message_id"),
                    "reply_markup": json.dumps({"inline_keyboard": []}),
                })
                self._api("sendMessage", data={"chat_id": self.chat_id, "parse_mode": "HTML",
                                               "text": f"⚠️ <b>Card Expired</b>: Run {html.escape(self.db.run_label(run_id))} is already closed (<code>{html.escape(str(existing))}</code>). No action taken."})
            except Exception as e:
                log.warning("Could not dismiss expired card: %s", e)
            return existing

        self._answer_callback(cq["id"], f"Decision: {decision.upper()}")
        msg_id = msg.get("message_id")
        orig_text = msg.get("text") or ""
        badge = (
            f"\n\n🎬 <b>APPROVED & QUEUED FOR PUBLISHING</b> (by {user_id})" if decision == "approved"
            else f"\n\n🔄 <b>REGENERATION REQUESTED</b> (by {user_id})" if decision == "regenerate"
            else f"\n\n❌ <b>REJECTED</b> (by {user_id})" if decision == "rejected"
            else f"\n\n✖ <b>CANCELLED / DISMISSED</b> (by {user_id})"
        )
        updated_text = (orig_text + badge) if orig_text else f"Decision: <b>{html.escape(decision.upper())}</b> for {html.escape(self.db.run_label(run_id))}"
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
        return decision

    # ------------------------------------------------------------ notifications
    def notify(self, text: str, buttons: Optional[list] = None, transient: bool = False, ttl_minutes: int = 60, record_alert: bool = True, silent: bool = False) -> bool:
        """Sends an operational message (HTML), optionally with inline buttons (single or multi-row).
        Never raises: alerts must not break a run."""
        if not self.is_configured():
            log.info("Notification (Telegram not configured): %s", text)
            if record_alert and hasattr(self.db, "record_alert"):
                self.db.record_alert(text, message_id=None, kind="transient" if transient else "persistent")
            return False
        truncated_text = safe_html_truncate(text, max_chars=4000)
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
                from datetime import datetime, timezone, timedelta
                expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
            if record_alert and hasattr(self.db, "record_alert"):
                self.db.record_alert(text, message_id=msg_id, kind="transient" if transient else "persistent", expires_at=expires_at)
            return msg_id if msg_id else True
        except Exception as e:
            err_msg = str(e).lower()
            if "can't parse entities" in err_msg or "bad request" in err_msg:
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
    def send_progress_card(self, run_id: str, topic_title: str) -> Optional[int]:
        """Sends an initial progress card for a running pipeline job, returning message_id."""
        if not self.is_configured():
            return None
        self._progress_start_time[run_id] = time.monotonic()
        text = (
            f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n"
            f"<b>{html.escape(topic_title)}</b> • <code>[{html.escape(self.db.run_label(run_id))}]</code>\n\n"
            f"🔄 <b>Step 1/5:</b> <i>Verifying script & historical fact sheets...</i>\n"
            f"◽ <b>Step 2/5:</b> Mastering narration voiceover & acoustics\n"
            f"◽ <b>Step 3/5:</b> Synthesizing original graphics & motion\n"
            f"◽ <b>Step 4/5:</b> Compositing video, parallax & karaoke subs\n"
            f"◽ <b>Step 5/5:</b> Packaging review & multiformat stills\n\n"
            f"Progress: <code>[▰▰▱▱▱▱▱▱▱▱] 20%</code>\n"
            f"⏱ <b>Elapsed:</b> <code>00:00</code> • <b>ETA:</b> <code>~01:30</code>"
        )
        buttons = [{"text": "🔄 Refresh Progress", "callback_data": f"cprog:{run_id}"}]
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

    def update_progress_card(self, message_id: int, step: int, total_steps: int, topic_title: str, run_id: str, details: str = "") -> bool:
        """Updates the progress card in-place with smooth bar, active sub-step, and ETA debouncing."""
        if not self.is_configured() or not message_id:
            return False

        now = time.monotonic()
        last_edit = self._last_progress_edit.get(message_id, 0.0)
        last_step = self._last_progress_step.get(message_id, 0)
        # Token-bucket rate limiter: ensure ≥1.2s between Telegram edits unless milestone step advances or step completes
        if (now - last_edit < 1.2) and (step == last_step) and (step < total_steps):
            return True

        milestones = [
            "Verifying script & historical fact sheets",
            "Mastering narration voiceover & acoustics",
            "Synthesizing original graphics & motion",
            "Compositing video, parallax & karaoke subs",
            "Packaging review & multiformat stills",
        ]

        lines = [
            f"⏳ <b><u>PRODUCTION PROGRESS</u></b>\n\n<b>{html.escape(topic_title)}</b> • <code>[{html.escape(self.db.run_label(run_id))}]</code>",
            "",
        ]
        for idx, m in enumerate(milestones, start=1):
            if idx < step:
                lines.append(f"✅ <b>Step {idx}/{total_steps}:</b> {m}")
            elif idx == step:
                detail_str = f" <i>({html.escape(details)})</i>" if details else ""
                lines.append(f"🔄 <b>Step {idx}/{total_steps}:</b> {m}...{detail_str}")
            else:
                lines.append(f"◽ <b>Step {idx}/{total_steps}:</b> {m}")

        bar_len = 10
        filled = max(1, min(bar_len, int((step / total_steps) * bar_len)))
        bar = "▰" * filled + "▱" * (bar_len - filled)
        pct = int((step / total_steps) * 100)

        # Elapsed & dynamic ETA calculation
        start_t = self._progress_start_time.get(run_id, now)
        elapsed_sec = int(now - start_t)
        elapsed_fmt = f"{elapsed_sec // 60:02d}:{elapsed_sec % 60:02d}"
        if step > 0 and step < total_steps:
            est_total = elapsed_sec / (step / total_steps)
            rem_sec = max(0, int(est_total - elapsed_sec))
            eta_fmt = f"~{rem_sec // 60:02d}:{rem_sec % 60:02d}"
        else:
            eta_fmt = "completing..." if step == total_steps else "~01:00"

        lines.append(f"\nProgress: <code>[{bar}] {pct}%</code>")
        lines.append(f"⏱ <b>Elapsed:</b> {elapsed_fmt} • <b>ETA:</b> {eta_fmt}")

        text = "\n".join(lines)
        self._last_progress_edit[message_id] = now
        self._last_progress_step[message_id] = step

        buttons = [{"text": "🔄 Refresh Progress", "callback_data": f"cprog:{run_id}"}]

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
        if not success:
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
        if hasattr(self.db, "clear_active_progress"):
            self.db.clear_active_progress()
        if not self.is_configured() or not message_id:
            return False
        if not success:
            text = (
                f"❌ <b>Render Failed:</b> {html.escape(topic_title)}\n"
                f"<code>[{html.escape(self.db.run_label(run_id))}]</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Error: <code>{html.escape(error[:400])}</code>"
            )
            return self.edit_message_text(message_id, text)
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
            if "can't parse entities" in err_msg or "bad request" in err_msg:
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

    def cleanup_stale_approvals(self, max_age_hours: int = 2) -> int:
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
            "caption": caption[:1024],
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": format_responsive_keyboard(buttons) if buttons else []}),
        }
        try:
            self._api("editMessageCaption", data=data)
            return True
        except Exception as e:
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

    def flip_cover(self, message_id: int, run_name: str, variant_letter: str) -> bool:
        """Flips the cover variant in-place on an existing Telegram photo message using editMessageMedia."""
        var_letter = variant_letter.lower()
        matches = [p for p in Path("output").rglob(run_name) if p.is_dir()]
        if not matches:
            return False
        r_dir = matches[0]
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
            ]
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
            buttons=buttons,
            cache_key=cache_key
        )
        return bool(res)

    def send_photo(self, path: Path, caption: str, buttons: Optional[list] = None, cache_key: Optional[str] = None) -> Any:
        """Sends an image with an HTML caption (max 1,024 chars) and optional inline buttons."""
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

    def poll_once(self, command_handler: Optional[Callable[[str, str], str]] = None, timeout: int = 25) -> None:
        """One long-poll: applies approval clicks for any run and answers authorized /commands."""
        self.cleanup_transient_messages()
        self.cleanup_stale_approvals()
        if self._offset is None:
            self._offset = self._latest_update_id() + 1
        updates = self._api("getUpdates", timeout=timeout + 10, data={
            "offset": self._offset, "timeout": timeout,
            "allowed_updates": json.dumps(["callback_query", "message"]),
        })
        for update in updates:
            self._offset = update["update_id"] + 1
            cq = update.get("callback_query")
            if cq:
                user_id = str(cq.get("from", {}).get("id", ""))
                chat_id = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
                username = str(cq.get("from", {}).get("username", "") or cq.get("from", {}).get("first_name", ""))
                data_str = cq.get("data", "")
                action, _, target = data_str.partition(":")
                ok = self._authorized(user_id, chat_id)
                try:
                    self.db.record_audit_event(
                        user_id=user_id,
                        username=username,
                        chat_id=chat_id,
                        event_type="callback_query",
                        payload=data_str,
                        status="allowed" if ok else "denied"
                    )
                except Exception as ea:
                    log.debug("Could not record callback audit: %s", ea)

                if not ok:
                    self._answer_callback(cq["id"], "Not authorized.")
                    continue

                if action in ACTIONS:
                    self._handle_callback(cq)
                elif action == "cov":
                    msg = cq.get("message") or {}
                    msg_id = msg.get("message_id")
                    if msg_id and ":" in target and msg.get("photo"):
                        r_name, var_letter = target.split(":", 1)
                        if self.flip_cover(msg_id, r_name, var_letter):
                            self._answer_callback(cq["id"], f"Flipped to Variant {var_letter.upper()}")
                            continue
                    if command_handler is not None:
                        reply = command_handler(action, target)
                        self._answer_callback(cq["id"], "Cover Selected")
                        if msg_id:
                            self.edit_message_any(msg_id, reply, is_media=bool(msg.get("photo")))
                elif action == "covlock":
                    msg = cq.get("message") or {}
                    msg_id = msg.get("message_id")
                    if command_handler is not None:
                        reply = command_handler("covlock", target)
                        self._answer_callback(cq["id"], "🔒 Cover Locked!")
                        if msg_id:
                            self.edit_message_caption(msg_id, reply, buttons=[])
                elif command_handler is not None:
                    # Other buttons (e.g. "imgok:<topic>") map to the same command handler.
                    msg = cq.get("message") or {}
                    msg_id = msg.get("message_id")
                    is_media = bool(msg.get("photo") or msg.get("audio") or msg.get("video") or msg.get("document"))

                    if msg_id and action in ("mnext", "mcancel", "umore", "umoredone", "uskip", "tskip", "mdel", "imgcancel"):
                        self.delete_message(msg_id)
                    elif msg_id and action not in ("qrender",):
                        try:
                            self._api("editMessageReplyMarkup", data={
                                "chat_id": self.chat_id,
                                "message_id": msg_id,
                                "reply_markup": json.dumps({"inline_keyboard": []}),
                            })
                        except Exception as em:
                            log.debug("Could not edit reply markup: %s", em)
                    try:
                        reply = command_handler(action, target)
                        ans = ("✅ Storyboard Approved!" if action == "imgok"
                               else "✖ Cancelled" if action in ("imgcancel", "mcancel", "umoredone")
                               else "⏭ Next Track" if action == "mnext"
                               else "➕ Discoveries" if action == "umore"
                               else "✅ Added to Queue" if action in ("uadd", "tadd")
                               else "✖ Skipped" if action in ("uskip", "tskip")
                               else "🗑 Track Removed" if action == "mdel"
                               else "✅ Track Kept" if action == "mkeep"
                               else "🚀 Bumped to #1" if action == "qbump"
                               else "🗑 Topic Dropped" if action == "qdrop"
                               else "🔄 Queue Refreshed" if action == "qrefresh"
                               else "⚡ Render Triggered!" if action == "qrender"
                               else "📌 Cover Selected" if action == "cov"
                               else "📊 System Status" if action == "cstat"
                               else "⚡ Live Progress" if action == "cprog"
                               else "🛡️ Audit Log" if action == "caudit"
                               else "🎨 Cover Studio" if action == "ccov"
                               else "💡 Proposals" if action == "cprop"
                               else "🏛️ Discoveries" if action == "cdisc"
                               else "🎵 Audio Library" if action == "cmusic"
                               else "⏸️ Paused" if action == "cpause"
                               else "▶️ Resumed" if action == "cresume"
                               else "🏛️ Cockpit" if action == "cdash"
                               else "🧪 Simulated Publish Complete" if action == "simpub"
                               else "Done!")
                        self._answer_callback(cq["id"], ans)
                        if action == "cdash" and msg_id:
                            from .control import dashboard_text_and_buttons
                            from .config import ASSETS_DIR
                            next_fn = getattr(self, "next_run_func", lambda: None)
                            dash_text, dash_buttons = dashboard_text_and_buttons(self.db, next_fn(), assets_dir=ASSETS_DIR)
                            self.edit_message_text(msg_id, dash_text, buttons=dash_buttons)
                        elif action in ("uadd", "tadd", "qbump", "qdrop", "qrefresh", "qrender", "cpause", "cresume", "cprog", "simpub") and msg_id:
                            self.edit_message_text(msg_id, reply)
                        elif action in ("imgok", "mkeep", "cov") and msg_id:
                            self.edit_message_any(msg_id, reply, is_media=is_media)
                        elif action in ("uskip", "tskip", "imgcancel", "mcancel", "mdel"):
                            pass  # Card was deleted; callback answer gave user instant toast
                        else:
                            self.notify(reply, record_alert=False)
                    except Exception as e:
                        self._answer_callback(cq["id"], "Failed")
                        self.notify(f"⚠️ {html.escape(action)} failed: {html.escape(str(e))[:300]}")
                continue
            msg = update.get("message") or {}
            # Direct video / document upload with /footage caption
            doc_or_vid = msg.get("video") or msg.get("document")
            caption = (msg.get("caption") or "").strip()
            if doc_or_vid and caption.startswith("/footage") and self._authorized(msg.get("from", {}).get("id", ""), msg.get("chat", {}).get("id", "")):
                _, _, args = caption[1:].partition(" ")
                topic_ref = args.strip()
                if topic_ref:
                    try:
                        file_id = doc_or_vid.get("file_id")
                        file_info = self._api("getFile", data={"file_id": file_id})
                        file_path = file_info.get("file_path")
                        if file_path:
                            download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
                            from . import config
                            from .control import _resolve_topic_dir
                            topic_dir = _resolve_topic_dir(topic_ref, config.ASSETS_DIR)
                            footage_dir = topic_dir / "footage"
                            footage_dir.mkdir(parents=True, exist_ok=True)
                            dest = footage_dir / "curated_footage.mp4"
                            with requests.get(download_url, stream=True, timeout=120) as r:
                                r.raise_for_status()
                                with open(dest, "wb") as f:
                                    for chunk in r.iter_content(chunk_size=65536):
                                        f.write(chunk)
                            cand = {
                                "provider": "curated",
                                "title": "Telegram Curated Clip",
                                "artist": f"Telegram User {msg.get('from', {}).get('first_name', 'Approver')}",
                                "license": "Curated Documentary Footage",
                                "attribution_required": False,
                            }
                            (footage_dir / "curated_footage.json").write_text(json.dumps(cand, indent=2, ensure_ascii=False), encoding="utf-8")
                            self.notify(f"✅ Video clip received and saved for <b>{html.escape(topic_dir.name)}</b>.")
                    except Exception as e:
                        log.exception("Direct video download from Telegram failed")
                continue

            # Direct evidence upload with /issue, /critique, /bug caption
            photos = msg.get("photo")
            media_item = (photos[-1] if photos else None) or msg.get("document") or msg.get("video") or msg.get("audio") or msg.get("voice")
            caption = (msg.get("caption") or "").strip()
            if media_item and caption.startswith(("/issue", "/critique", "/bug")) and self._authorized(msg.get("from", {}).get("id", ""), msg.get("chat", {}).get("id", "")):
                command, _, critique_text = caption[1:].partition(" ")
                command = command.split("@")[0].lower()
                try:
                    file_id = media_item.get("file_id")
                    file_info = self._api("getFile", data={"file_id": file_id})
                    file_path = file_info.get("file_path", "")
                    if file_path:
                        download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
                        evidence_dir = Path(__file__).resolve().parent.parent / "docs" / "issues" / "evidence"
                        evidence_dir.mkdir(parents=True, exist_ok=True)
                        ext = Path(file_path).suffix or ".jpg"
                        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        dest = evidence_dir / f"evidence_{ts}{ext}"
                        with requests.get(download_url, stream=True, timeout=120) as r:
                            r.raise_for_status()
                            with open(dest, "wb") as f:
                                for chunk in r.iter_content(chunk_size=65536):
                                    f.write(chunk)
                        args_with_ev = f"{critique_text.strip()} --evidence {dest}"
                        if command_handler:
                            reply = command_handler(command, args_with_ev)
                            self.notify(reply)
                except Exception as e:
                    log.exception("Evidence upload for /issue failed")
                    self.notify(f"⚠️ Failed to process issue evidence: {html.escape(str(e))[:300]}")
                continue

            text = (msg.get("text") or "").strip()
            reply_to = msg.get("reply_to_message")
            if not text or command_handler is None:
                continue

            user_id = str(msg.get("from", {}).get("id", ""))
            chat_id = str(msg.get("chat", {}).get("id", ""))
            username = str(msg.get("from", {}).get("username", "") or msg.get("from", {}).get("first_name", ""))
            is_auth = self._authorized(user_id, chat_id)

            if text.startswith("/"):
                command, _, args = text[1:].partition(" ")
                command = command.split("@")[0].lower()
                audit_payload = text
            elif reply_to:
                # Native Telegram swipe-to-reply: treat as contextual /ask query
                parent_text = (reply_to.get("text") or reply_to.get("caption") or "").strip()
                command = "ask"
                args = f"{text}\n\n[Context from replied message]: {parent_text[:400]}"
                audit_payload = f"/ask (reply) {text}"
            else:
                continue

            try:
                self.db.record_audit_event(
                    user_id=user_id,
                    username=username,
                    chat_id=chat_id,
                    event_type="command",
                    payload=audit_payload,
                    status="allowed" if is_auth else "denied"
                )
            except Exception as ea:
                log.debug("Could not record command audit: %s", ea)

            if not is_auth:
                log.warning("Ignoring command from unauthorized user %s.", user_id)
                continue  # "/status@relicandriddle_bot" -> "status"

            if command in ("clear", "clean", "purge"):
                cmd_mid = msg.get("message_id")
                # 1. Immediate responsiveness feedback
                self.send_chat_action("typing")
                if cmd_mid:
                    self.delete_message(cmd_mid)

                # 2. Determine sweep depth: default 500 messages (or up to 1000 if requested)
                depth = 500
                arg_clean = args.strip().lower()
                if arg_clean.isdigit():
                    depth = min(1000, max(50, int(arg_clean)))
                elif arg_clean in ("all", "full", "max", "entire"):
                    depth = 1000

                active_prog = self.db.get_active_progress() if hasattr(self.db, "get_active_progress") else None
                active_mid = (active_prog or {}).get("message_id")

                # 3. Collect candidate IDs in reverse order
                candidate_ids: List[int] = []
                if hasattr(self.db, "list_known_message_ids"):
                    for kid in self.db.list_known_message_ids():
                        if active_mid and kid == active_mid:
                            continue
                        candidate_ids.append(kid)

                if cmd_mid:
                    start_id = cmd_mid - 1
                    min_id = max(1, cmd_mid - depth)
                    for mid in range(start_id, min_id - 1, -1):
                        if active_mid and mid == active_mid:
                            continue
                        if mid not in candidate_ids:
                            candidate_ids.append(mid)

                # 4. Instant atomic bulk deletion (eliminates slow one-by-one UI animations!)
                deleted_count = self.delete_messages(candidate_ids) if candidate_ids else 0
                self.cleanup_transient_messages()

                # 5. Display fresh Executive Cockpit
                from .control import dashboard_text_and_buttons
                from .config import ASSETS_DIR
                next_fn = getattr(self, "next_run_func", lambda: None)
                dash_text, dash_buttons = dashboard_text_and_buttons(self.db, next_fn(), assets_dir=ASSETS_DIR)
                clean_text = (
                    f"🧹 <b><u>CHAT CLEARED</u></b>\n\n"
                    f"{deleted_count} message(s) purged.\n\n"
                    f"<i>(Note: Telegram API restricts bots from deleting human messages older than 48h. "
                    f"To completely wipe 100% of ancient messages from your device: Chat Header → 'Clear History')</i>\n\n"
                    + dash_text
                )
                self.notify(clean_text, buttons=dash_buttons, record_alert=False)

                # 6. Bring active progress card forward to the bottom of the fresh chat
                if active_prog:
                    if active_mid:
                        self.delete_message(active_mid)
                    new_mid = self.send_progress_card(active_prog["run_id"], active_prog["topic_title"])
                    if new_mid and active_prog.get("step"):
                        self.update_progress_card(
                            new_mid,
                            active_prog["step"],
                            active_prog.get("total_steps", 5),
                            active_prog["topic_title"],
                            active_prog["run_id"],
                            active_prog.get("details", "")
                        )
                continue

            if command in ("ask", "probe", "query", "why"):
                self.send_chat_action("typing")
            try:
                reply = command_handler(command, args.strip())
            except Exception as e:
                log.exception("Command /%s failed", command)
                reply = f"⚠️ /{html.escape(command)} failed: {html.escape(str(e))[:300]}"
            if reply:
                self.notify(reply, record_alert=False)

    def wait_for_decision(self, run_id: str, timeout_seconds: int = APPROVAL_TIMEOUT_SECONDS, poll_interval: int = 5) -> str:
        """Returns 'approved', 'rejected', 'regenerate', or 'timeout'. Never auto-approves."""
        log.info("Awaiting human decision for %s (timeout %ss)...", run_id, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        offset = self._latest_update_id() + 1 if self.is_configured() and not self.background_polling else 0

        while time.monotonic() < deadline:
            self.cleanup_transient_messages()
            approval = self.db.get_approval(run_id)
            if approval and approval["decision"] != "pending":
                log.info("Decision for %s: %s (by %s)", run_id, approval["decision"], approval.get("decided_by"))
                return approval["decision"]

            if not self.is_configured() or self.background_polling:
                time.sleep(poll_interval)
                continue

            try:
                long_poll = int(max(1, min(25, deadline - time.monotonic())))
                updates = self._api("getUpdates", timeout=long_poll + 10, data={
                    "offset": offset, "timeout": long_poll, "allowed_updates": json.dumps(["callback_query"]),
                })
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
            return "timeout"
        return (self.db.get_approval(run_id) or {}).get("decision", "timeout")

    def record_local_decision(self, run_id: str, decision: str) -> bool:
        """CLI decision path. Returns False if the run already has a decision."""
        if decision not in ACTIONS.values():
            raise ValueError(f"Invalid decision '{decision}'")
        applied = self.db.update_approval_decision(run_id, decision, decided_by="cli")
        if applied:
            self.dismiss_approval_buttons(run_id)
        return applied
