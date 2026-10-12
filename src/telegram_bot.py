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
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

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

ACTIONS = {"approve": "approved", "regen": "regenerate", "reject": "rejected"}
TELEGRAM_VIDEO_LIMIT_MB = 49.0


def _second_check_line(second: Optional[Dict[str, Any]]) -> str:
    if not second:
        return ""
    name = re.search(r"^\[([^\]]+)\]", second.get("summary", ""))
    who = f" by {name.group(1)}" if name else ""
    if second.get("status") == "pass":
        return f"✅ Second check{html.escape(who)}: passed ({second.get('confidence', 0)}% of {len(second.get('audits', []))} claims)\n"
    return "⚠️ Second check: unavailable (first check only)\n"


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

    def is_configured(self) -> bool:
        return is_configured(self.bot_token) and is_configured(self.chat_id) and bool(self.approver_ids)

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
        text = (
            f"🎬 <b>Reel ready for review</b>\n\n"
            f"📌 <b>{e(script_payload.get('title', ''))}</b>\n"
            f"📍 {e(loc.get('name', ''))}, {e(loc.get('country', ''))}\n"
            f"⏱️ {script_payload.get('word_count', 0)} words · source: {e(script_payload.get('origin', '?'))}\n"
            f"✅ Fact check: {script_payload.get('consensus_confidence', 0)}% of {len(audits)} claims"
            + (f" ({len(flagged)} hedged/disputed)" if flagged else "") + "\n"
            + _second_check_line(script_payload.get("second_check")) + "\n"
            f"📝 <i>{e(script_payload.get('narration_script', ''))}</i>\n\n"
            f"💬 First comment: <i>{e(script_payload.get('first_comment', ''))}</i>\n"
            f"🏷️ {e(' '.join(script_payload.get('hashtags', [])))}\n"
        )
        if script_payload.get("fact_sheet_note"):
            text += "\n" + e(script_payload["fact_sheet_note"])
        if script_payload.get("voice_note"):
            text += "\n" + e(script_payload["voice_note"])
        if script_payload.get("music_note"):
            text += "\n" + e(script_payload["music_note"])
        if script_payload.get("motion_note"):
            text += "\n" + e(script_payload["motion_note"])
        if script_payload.get("graphics_note"):
            text += "\n" + e(script_payload["graphics_note"])
        if script_payload.get("footage_note"):
            text += "\n" + e(script_payload["footage_note"])
        if script_payload.get("beat_image_note"):
            text += "\n" + e(script_payload["beat_image_note"])
        if script_payload.get("source_note"):
            text += "\n" + e(script_payload["source_note"]) + "\n"
        if script_payload.get("qa_summary"):
            text += "\n" + script_payload["qa_summary"] + "\n"  # already HTML-escaped by video_qa
        if sources:
            text += "\n📚 " + "\n📚 ".join(e(s.get("url", "")) for s in sources) + "\n"
        return text[:3900] + f"\n<i>Run ID (for the command line): {e(run_id)}</i>"

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
            markup = {"inline_keyboard": [[
                {"text": "✅ Approve & Publish", "callback_data": f"approve:{key}"},
                {"text": "🔄 Regenerate", "callback_data": f"regen:{key}"},
                {"text": "❌ Reject", "callback_data": f"reject:{key}"},
            ]]}
            msg = self._api("sendMessage", data={
                "chat_id": self.chat_id,
                "text": self.format_card_text(script_payload, run_id),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
                "reply_markup": json.dumps(markup),
            })
            message_id = msg["message_id"]
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
            self._api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": "Not authorized."})
            return None
        if not self.db.get_approval(run_id):
            self._api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": "Unknown run."})
            return None

        decision = ACTIONS[action]
        applied = self.db.update_approval_decision(run_id, decision, decided_by=f"telegram:{user_id}")
        if not applied:
            decision = (self.db.get_approval(run_id) or {}).get("decision", decision)
        self._api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": f"Decision: {decision.upper()}"})
        try:
            self._api("editMessageReplyMarkup", data={
                "chat_id": self.chat_id, "message_id": msg.get("message_id"),
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })
            self._api("sendMessage", data={"chat_id": self.chat_id, "parse_mode": "HTML",
                                           "text": f"Decision recorded: <b>{html.escape(decision.upper())}</b> for {html.escape(self.db.run_label(run_id))}"})
        except Exception as e:
            log.warning("Could not update Telegram card: %s", e)
        return decision

    # ------------------------------------------------------------ notifications
    def notify(self, text: str, buttons: Optional[list] = None) -> bool:
        """Sends an operational message (HTML), optionally with one row of inline buttons.
        Never raises: alerts must not break a run."""
        if not self.is_configured():
            log.info("Notification (Telegram not configured): %s", text)
            return False
        data = {"chat_id": self.chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
        try:
            self._api("sendMessage", data=data)
            return True
        except Exception as e:
            log.warning("Could not send Telegram notification: %s", e)
            return False

    def send_audio(self, path: Path, caption: str, buttons: Optional[list] = None) -> bool:
        """Sends a playable audio file with an HTML caption and optional inline buttons."""
        if not self.is_configured():
            return False
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
        try:
            with open(path, "rb") as f:
                self._api("sendAudio", timeout=120, data=data, files={"audio": f})
            return True
        except Exception as e:
            log.warning("Could not send audio: %s", e)
            return False

    def send_photo(self, path: Path, caption: str, buttons: Optional[list] = None) -> bool:
        """Sends an image with an HTML caption (max 1,024 chars) and optional inline buttons."""
        if not self.is_configured():
            return False
        data = {"chat_id": self.chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})
        try:
            with open(path, "rb") as f:
                self._api("sendPhoto", data=data, files={"photo": f})
            return True
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
                action, _, target = cq.get("data", "").partition(":")
                if action in ACTIONS:
                    self._handle_callback(cq)
                elif command_handler is not None:
                    # Other buttons (e.g. "imgok:<topic>") map to the same command handler.
                    ok = self._authorized(cq.get("from", {}).get("id", ""),
                                          (cq.get("message") or {}).get("chat", {}).get("id", ""))
                    self._api("answerCallbackQuery", data={"callback_query_id": cq["id"],
                                                           "text": "Working…" if ok else "Not authorized."})
                    if ok:
                        try:
                            self.notify(command_handler(action, target))
                        except Exception as e:
                            self.notify(f"⚠️ {html.escape(action)} failed: {html.escape(str(e))[:300]}")
                continue
            msg = update.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text.startswith("/") or command_handler is None:
                continue
            if not self._authorized(msg.get("from", {}).get("id", ""), msg.get("chat", {}).get("id", "")):
                log.warning("Ignoring command from unauthorized user %s.", msg.get("from", {}).get("id"))
                continue
            command, _, args = text[1:].partition(" ")
            command = command.split("@")[0].lower()  # "/status@relicandriddle_bot" -> "status"
            try:
                reply = command_handler(command, args.strip())
            except Exception as e:
                log.exception("Command /%s failed", command)
                reply = f"⚠️ /{html.escape(command)} failed: {html.escape(str(e))[:300]}"
            self.notify(reply)

    def wait_for_decision(self, run_id: str, timeout_seconds: int = APPROVAL_TIMEOUT_SECONDS, poll_interval: int = 5) -> str:
        """Returns 'approved', 'rejected', 'regenerate', or 'timeout'. Never auto-approves."""
        log.info("Awaiting human decision for %s (timeout %ss)...", run_id, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        offset = self._latest_update_id() + 1 if self.is_configured() and not self.background_polling else 0

        while time.monotonic() < deadline:
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
            return "timeout"
        return (self.db.get_approval(run_id) or {}).get("decision", "timeout")

    def record_local_decision(self, run_id: str, decision: str) -> bool:
        """CLI decision path. Returns False if the run already has a decision."""
        if decision not in ACTIONS.values():
            raise ValueError(f"Invalid decision '{decision}'")
        return self.db.update_approval_decision(run_id, decision, decided_by="cli")
