"""
src/telegram_bot.py
===================
Human-in-the-Loop (HITL) approval module for the Autonomous Meta Content Creation Pipeline.
Dispatches video previews, Variant B thumbnails, and complete metadata envelopes to a
Telegram channel/chat with one-tap inline keyboard buttons:
- [✅ Approve & Publish]
- [🔄 Regenerate]
- [❌ Reject]

Supports both live Telegram Bot API updates (via HTTP) and local queue fallback when
credentials are being configured.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests

from .config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    DATA_DIR,
)
from .db import PipelineDatabase
from .ops import SecretMasker

APPROVAL_QUEUE_FILE = DATA_DIR / "approval_queue.json"


class TelegramApprovalBot:
    def __init__(
        self,
        db: Optional[PipelineDatabase] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        queue_file: Path = APPROVAL_QUEUE_FILE
    ):
        self.db = db or PipelineDatabase()
        self.masker = SecretMasker()
        self.bot_token = bot_token if bot_token is not None else TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id if chat_id is not None else TELEGRAM_CHAT_ID
        self.queue_file = queue_file
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else ""
        self._ensure_queue_file()

    def _ensure_queue_file(self):
        if not self.queue_file.exists():
            with open(self.queue_file, "w", encoding="utf-8") as f:
                json.dump({"pending": {}, "decisions": {}}, f, indent=2)

    def is_configured(self) -> bool:
        return bool(
            self.bot_token
            and self.chat_id
            and self.bot_token != "your_telegram_bot_token_here"
            and self.chat_id != "your_telegram_chat_id_here"
        )

    def _load_queue(self) -> Dict[str, Any]:
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"pending": {}, "decisions": {}}

    def _save_queue(self, data: Dict[str, Any]):
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def format_card_text(self, script_payload: Dict[str, Any], run_id: str) -> str:
        """
        Formats a clean, structured HTML card for Telegram presentation.
        """
        title = script_payload.get("title", "Untitled Reel")
        wc = script_payload.get("word_count", len(script_payload.get("narration_script", "").split()))
        loc = script_payload.get("location", {})
        loc_str = f"{loc.get('name', 'Unknown')}, {loc.get('country', '')}"
        script = script_payload.get("narration_script", "")
        preview_script = script if len(script) <= 300 else script[:297] + "..."
        hashtags = " ".join(script_payload.get("hashtags", [])[:6])
        first_comment = script_payload.get("first_comment", "N/A")

        text = (
            f"🎬 <b>[NEW PRODUCTION REEL READY FOR REVIEW]</b>\n"
            f"🆔 <b>Run ID</b>: <code>{run_id}</code>\n\n"
            f"📌 <b>Title</b>: <b>{title}</b>\n"
            f"📍 <b>Location</b>: {loc_str}\n"
            f"⏱️ <b>Spoken Length</b>: ~42s ({wc} words)\n\n"
            f"📝 <b>Narration Script</b>:\n<i>\"{preview_script}\"</i>\n\n"
            f"🏷️ <b>Hashtags</b>:\n{hashtags}\n\n"
            f"💬 <b>Pinned First Comment</b>:\n<i>\"{first_comment}\"</i>\n\n"
            f"👉 <i>Click an option below to approve publishing to Facebook & Instagram:</i>"
        )
        return text

    def send_approval_request(
        self,
        video_path: Path,
        thumbnail_path: Path,
        script_payload: Dict[str, Any],
        run_id: str
    ) -> Optional[int]:
        """
        Dispatches media and interactive approval buttons to Telegram.
        Falls back to local file queue if Telegram credentials are not yet configured.
        """
        tid = script_payload.get("topic_id", "")
        # Record in local queue & SQLite DB
        self.db.record_approval(run_id, tid, None)
        queue = self._load_queue()
        queue["pending"][run_id] = {
            "run_id": run_id,
            "topic_id": tid,
            "title": script_payload.get("title", ""),
            "video_path": str(video_path.resolve()),
            "thumbnail_path": str(thumbnail_path.resolve()),
            "script_payload": script_payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending"
        }
        self._save_queue(queue)

        if not self.is_configured():
            print(f"[TELEGRAM BOT] Telegram credentials not configured. Recorded approval request {run_id} in SQLite & JSON queue.")
            return None

        # Build inline keyboard markup
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve & Publish", "callback_data": f"approve:{run_id}"},
                    {"text": "🔄 Regenerate", "callback_data": f"regen:{run_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{run_id}"}
                ]
            ]
        }

        caption = self.format_card_text(script_payload, run_id)

        try:
            # 1. First send the cover photo with details and buttons
            with open(thumbnail_path, "rb") as photo_file:
                resp = requests.post(
                    f"{self.base_url}/sendPhoto",
                    data={
                        "chat_id": self.chat_id,
                        "caption": caption,
                        "parse_mode": "HTML",
                        "reply_markup": json.dumps(reply_markup)
                    },
                    files={"photo": photo_file},
                    timeout=30
                )
                resp.raise_for_status()
                msg_data = resp.json()
                message_id = msg_data.get("result", {}).get("message_id")

            # 2. If video size is within Telegram limit (<= 49 MB), send video preview
            vid_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            if vid_size_mb <= 49.0:
                print(f"[TELEGRAM BOT] Sending video preview ({vid_size_mb:.1f} MB)...")
                with open(video_path, "rb") as vid_file:
                    requests.post(
                        f"{self.base_url}/sendVideo",
                        data={
                            "chat_id": self.chat_id,
                            "caption": f"🎥 Full Master Video Cut ({vid_size_mb:.1f} MB)\nRun ID: {run_id}"
                        },
                        files={"video": vid_file},
                        timeout=120
                    )
            else:
                print(f"[TELEGRAM BOT] Video file size ({vid_size_mb:.1f} MB) exceeds Telegram 50MB limit; cover and metadata dispatched.")

            print(f"[TELEGRAM BOT] Approval request successfully dispatched to Telegram (Msg ID: {message_id})")
            return message_id

        except Exception as e:
            print(f"[TELEGRAM BOT] Warning: Failed to send Telegram message: {e}")
            return None

    def wait_for_decision(
        self,
        run_id: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
        auto_approve_if_unconfigured: bool = False
    ) -> str:
        """
        Waits for a decision on run_id.
        Can receive decision from Telegram callback, or local approval queue file,
        or auto-approves if configured as test mode.
        Returns: 'approved', 'rejected', 'regenerate', or 'timeout'.
        """
        print(f"[TELEGRAM BOT] Awaiting HITL decision for Run '{run_id}' (timeout: {timeout_seconds}s)...")
        start_time = time.time()
        last_update_id = 0

        while time.time() - start_time < timeout_seconds:
            # 1. Check local file queue first (allows manual CLI approve/reject)
            queue = self._load_queue()
            if run_id in queue.get("decisions", {}):
                decision = queue["decisions"][run_id].get("decision", "rejected")
                print(f"[TELEGRAM BOT] Decision found in local queue: '{decision}'")
                return decision

            # If Telegram is not configured, check auto-approve policy
            if not self.is_configured():
                if auto_approve_if_unconfigured:
                    print("[TELEGRAM BOT] Telegram not configured; auto-approving for pipeline continuity.")
                    self.record_local_decision(run_id, "approved")
                    return "approved"
                else:
                    time.sleep(poll_interval)
                    continue

            # 2. Poll Telegram Bot API for callback query clicks
            try:
                params = {"timeout": 3}
                if last_update_id:
                    params["offset"] = last_update_id + 1

                resp = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        last_update_id = update.get("update_id", last_update_id)
                        cq = update.get("callback_query")
                        if not cq:
                            continue

                        cb_data = cq.get("data", "")
                        cq_id = cq.get("id")

                        if ":" in cb_data:
                            action, target_run_id = cb_data.split(":", 1)
                            if target_run_id == run_id:
                                decision = "approved" if action == "approve" else ("regenerate" if action == "regen" else "rejected")
                                # Acknowledge callback query
                                requests.post(
                                    f"{self.base_url}/answerCallbackQuery",
                                    data={"callback_query_id": cq_id, "text": f"Decision: {decision.upper()}"},
                                    timeout=10
                                )
                                # Update message
                                msg = cq.get("message")
                                if msg:
                                    chat_id = msg.get("chat", {}).get("id")
                                    msg_id = msg.get("message_id")
                                    emoji = "✅" if decision == "approved" else ("🔄" if decision == "regenerate" else "❌")
                                    requests.post(
                                        f"{self.base_url}/sendMessage",
                                        data={
                                            "chat_id": chat_id,
                                            "text": f"{emoji} <b>Decision Recorded:</b> <code>{decision.upper()}</code> for Run <code>{run_id}</code>.",
                                            "parse_mode": "HTML"
                                        },
                                        timeout=10
                                    )
                                self.record_local_decision(run_id, decision)
                                return decision
            except Exception as e:
                # Network hiccup, retry next loop
                pass

            time.sleep(poll_interval)

        print(f"[TELEGRAM BOT] Timeout ({timeout_seconds}s) reached without decision.")
        return "timeout"

    def record_local_decision(self, run_id: str, decision: str):
        """
        Manually records an approval or rejection in the local queue.
        Can be invoked via CLI `python main.py approve <run_id>`.
        """
        self.db.update_approval_decision(run_id, decision)
        queue = self._load_queue()
        queue.setdefault("decisions", {})[run_id] = {
            "decision": decision,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        if run_id in queue.get("pending", {}):
            queue["pending"][run_id]["status"] = decision
        self._save_queue(queue)
        print(f"[TELEGRAM BOT] Local decision for '{run_id}' set to '{decision}' in SQLite WAL & JSON.")


if __name__ == "__main__":
    bot = TelegramApprovalBot()
    print(f"Telegram Configured: {bot.is_configured()}")
    mock_payload = {
        "title": "The 18-Story City Hidden Beneath a Basement",
        "word_count": 104,
        "location": {"name": "Derinkuyu", "country": "Turkey"},
        "narration_script": "In 1963, a simple basement renovation uncovered...",
        "hashtags": ["#AncientMysteries", "#Derinkuyu", "#Archaeology"],
        "first_comment": "Archaeologists believe dozens more cities remain hidden. Would you explore one?"
    }
    card = bot.format_card_text(mock_payload, "run_test_001")
    print("\nSample Formatted Card:\n" + card)
