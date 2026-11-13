import json
import sqlite3
import sys
from pathlib import Path

if "--i-know-this-is-archived" not in sys.argv:
    sys.exit("scripts/archive/clean_dev_telegram.py is an archived one-off and is not maintained.\n"
             "If run, it deletes the configured Telegram chat's messages and resets the database's card state.\n"
             "Refusing; see scripts/README.md, or pass --i-know-this-is-archived.")

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.telegram_bot import TelegramApprovalBot
import src.config as config

def clean_telegram_dev_chat():
    b = TelegramApprovalBot()
    if not b.is_configured():
        print("Telegram bot not configured.")
        return

    # 1. Gather all message IDs from database
    db_path = Path("data/pipeline.db")
    db_mids = set()
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        for r in conn.execute("SELECT telegram_message_id FROM approvals WHERE telegram_message_id IS NOT NULL"):
            if r[0]:
                db_mids.add(int(r[0]))
        for r in conn.execute("SELECT message_id FROM alerts WHERE message_id IS NOT NULL"):
            if r[0]:
                db_mids.add(int(r[0]))
        for r in conn.execute("SELECT message_id FROM telegram_cards"):
            if r[0]:
                db_mids.add(int(r[0]))
        for k, v in conn.execute("SELECT key, value FROM settings WHERE key LIKE '%telegram%'"):
            if k == "telegram_dashboard_id" and v and str(v).isdigit():
                db_mids.add(int(v))
            elif k == "telegram_media_messages" and v:
                try:
                    for m in json.loads(v):
                        db_mids.add(int(m))
                except Exception:
                    pass
        conn.close()

    print(f"Collected {len(db_mids)} distinct message IDs from SQLite.")

    # 2. Probe current latest message ID
    probe = b._api("sendMessage", data={"chat_id": b.chat_id, "text": "🧹 Initiating full dev channel sweep..."})
    latest_id = probe.get("message_id", 1400)
    print(f"Latest message ID in chat: {latest_id}")

    # Protected forum topic thread roots
    protected = set(config.TELEGRAM_TOPICS.values()) | {1}

    # 3. Combine db_mids with recent ID range [latest_id down to max(1, latest_id - 500)]
    all_to_delete = set(db_mids)
    for mid in range(latest_id - 500, latest_id + 1):
        if mid not in protected and mid > 0:
            all_to_delete.add(mid)

    sorted_ids = sorted(all_to_delete, reverse=True)
    print(f"Total targets to purge: {len(sorted_ids)}")

    # Try batch deletes in chunks of 50
    chunk_size = 50
    deleted_count = 0
    failed_singles = 0

    for i in range(0, len(sorted_ids), chunk_size):
        chunk = sorted_ids[i:i + chunk_size]
        try:
            res = b._api("deleteMessages", data={"chat_id": b.chat_id, "message_ids": json.dumps(chunk)})
            if res:
                deleted_count += len(chunk)
                continue
        except Exception:
            # Fall back to single delete if batch fails (e.g. non-deletable mixed in)
            pass

        for mid in chunk:
            try:
                if b.delete_message(mid):
                    deleted_count += 1
                else:
                    failed_singles += 1
            except Exception:
                failed_singles += 1

    print(f"Sweep complete! Purged messages: {deleted_count}, un-deletable or non-existent: {failed_singles}")

    # 4. Clean up SQLite dev state
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM telegram_cards")
        conn.execute("UPDATE settings SET value = NULL WHERE key = 'telegram_dashboard_id'")
        conn.execute("UPDATE settings SET value = '[]' WHERE key = 'telegram_media_messages'")
        conn.commit()
        conn.close()
        print("SQLite telegram card state reset.")

if __name__ == "__main__":
    clean_telegram_dev_chat()
