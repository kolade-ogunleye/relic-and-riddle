import sys
from pathlib import Path

if "--i-know-this-is-archived" not in sys.argv:
    sys.exit("scripts/archive/rename_topics.py is an archived one-off and is not maintained.\n"
             "If run, it renames the configured chat's Telegram forum topics.\n"
             "Refusing; see scripts/README.md, or pass --i-know-this-is-archived.")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.telegram_bot import TelegramApprovalBot
import src.config as config

def rename_all_topics():
    b = TelegramApprovalBot()
    clean_names = {
        "studio": "Studio",
        "needs": "Needs you",
        "reviews": "Reviews",
        "runs": "Runs",
        "info": "Info",
    }
    for key, name in clean_names.items():
        tid = config.TELEGRAM_TOPICS.get(key)
        if tid:
            try:
                res = b._api("editForumTopic", data={"chat_id": b.chat_id, "message_thread_id": tid, "name": name})
                print(f"Topic {tid} ({key}) renamed to '{name}': {res}")
            except Exception as e:
                print(f"Topic {tid} ({key}) failed: {e}")

if __name__ == "__main__":
    rename_all_topics()
