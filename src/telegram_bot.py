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
import time
from pathlib import Path
from typing import Dict, Any, Optional, Set

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
            f"🎬 <b>Reel ready for review</b>\n"
            f"🆔 <code>{e(run_id)}</code>\n\n"
            f"📌 <b>{e(script_payload.get('title', ''))}</b>\n"
            f"📍 {e(loc.get('name', ''))}, {e(loc.get('country', ''))}\n"
            f"⏱️ {script_payload.get('word_count', 0)} words · source: {e(script_payload.get('origin', '?'))}\n"
            f"✅ Fact check: {script_payload.get('consensus_confidence', 0)}% of {len(audits)} claims"
            + (f" ({len(flagged)} hedged/disputed)" if flagged else "") + "\n\n"
            f"📝 <i>{e(script_payload.get('narration_script', ''))}</i>\n\n"
            f"💬 First comment: <i>{e(script_payload.get('first_comment', ''))}</i>\n"
            f"🏷️ {e(' '.join(script_payload.get('hashtags', [])))}\n"
        )
        if sources:
            text += "\n📚 " + "\n📚 ".join(e(s.get("url", "")) for s in sources) + "\n"
        return text[:4000]

    def send_approval_request(self, video_path: Path, thumbnail_path: Path, script_payload: Dict[str, Any], run_id: str) -> Optional[int]:
        self.db.record_approval(run_id, script_payload.get("topic_id", ""), None)
        if not self.is_configured():
            log.warning("Telegram not configured. Review with: python main.py review approve|reject %s", run_id)
            return None

        try:
            with open(thumbnail_path, "rb") as photo:
                self._api("sendPhoto", data={"chat_id": self.chat_id, "caption": f"Cover · {run_id}"}, files={"photo": photo})

            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            if size_mb <= TELEGRAM_VIDEO_LIMIT_MB:
                with open(video_path, "rb") as vid:
                    self._api("sendVideo", timeout=300, data={"chat_id": self.chat_id, "caption": f"Master cut ({size_mb:.1f} MB)"},
                              files={"video": vid})
            else:
                log.warning("Video is %.1f MB, over Telegram's bot upload limit; review it locally: %s", size_mb, video_path)

            markup = {"inline_keyboard": [[
                {"text": "✅ Approve & Publish", "callback_data": f"approve:{run_id}"},
                {"text": "🔄 Regenerate", "callback_data": f"regen:{run_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{run_id}"},
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

    def _handle_callback(self, cq: Dict[str, Any], run_id: str) -> Optional[str]:
        action, _, target = cq.get("data", "").partition(":")
        if target != run_id or action not in ACTIONS:
            return None
        user_id = str(cq.get("from", {}).get("id", ""))
        msg = cq.get("message") or {}
        if user_id not in self.approver_ids or str(msg.get("chat", {}).get("id", "")) != str(self.chat_id):
            log.warning("Ignoring approval click from unauthorized user %s.", user_id)
            self._api("answerCallbackQuery", data={"callback_query_id": cq["id"], "text": "Not authorized."})
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
                                           "text": f"Decision recorded: <b>{html.escape(decision.upper())}</b> for <code>{html.escape(run_id)}</code>"})
        except Exception as e:
            log.warning("Could not update Telegram card: %s", e)
        return decision

    def wait_for_decision(self, run_id: str, timeout_seconds: int = APPROVAL_TIMEOUT_SECONDS, poll_interval: int = 5) -> str:
        """Returns 'approved', 'rejected', 'regenerate', or 'timeout'. Never auto-approves."""
        log.info("Awaiting human decision for %s (timeout %ss)...", run_id, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        offset = self._latest_update_id() + 1 if self.is_configured() else 0

        while time.monotonic() < deadline:
            approval = self.db.get_approval(run_id)
            if approval and approval["decision"] != "pending":
                log.info("Decision for %s: %s (by %s)", run_id, approval["decision"], approval.get("decided_by"))
                return approval["decision"]

            if not self.is_configured():
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
