"""
Scripted Telegram acceptance against the real Bot API, for the DEVELOPMENT bot only.

Drives the bot's own update processing (the same code the poller runs) with synthetic operator
updates, so every read-only command and dashboard panel is rendered and SENT to Telegram for
real: Telegram itself then validates each message's HTML, length, caption and keyboard. It runs
on a temporary copy of the database, never presses decision or mutating buttons (approve, add,
skip, keep, remove, run, pause), refuses the production bot, and reports every Bot API failure.

A human tap is still the final word on usability; this covers what a test double cannot:
the real API accepting the real payloads.

  python scripts/telegram_acceptance.py            # needs .env with the dev bot token
  python scripts/telegram_acceptance.py --trace    # also time every Bot API call
  python scripts/telegram_acceptance.py --keep     # leave the sent messages in the chat for inspection
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRODUCTION_BOT_ID = "8837268021"
SYNTHETIC_BASE = 10_000  # message ids of the synthetic operator messages
TYPED = ["/dashboard", "/status", "/progress", "/jobs", "/help", "/queue", "/next", "/ideas", "/proposals",
         "/discover", "/images", "/music", "/covers", "/footage", "/alerts", "/audit", "/improve",
         "/accounts", "/requeue list", "/nonsense_command"]
PANELS = ["cstat:", "caudit:", "cprog:", "qrefresh:", "cideas:", "cdisc:subterranean:0", "cprop:", "cimages:",
          "cmusic:", "ccov:", "chelp:", "cimprove:", "creviews:", "cdash:", "done:", "umoredone:"]


def main() -> int:
    from src import config
    from src.db import PipelineDatabase
    bot_id = config.TELEGRAM_BOT_TOKEN.split(":")[0]
    if bot_id == PRODUCTION_BOT_ID or not config.TELEGRAM_BOT_TOKEN:
        print("Refusing: this is not the development bot.")
        return 2
    work = Path(tempfile.mkdtemp(prefix="tg-accept-"))
    shutil.copy2(config.DATA_DIR / "pipeline.db", work / "pipeline.db")
    db = PipelineDatabase(db_path=work / "pipeline.db")
    db.set_setting("telegram_dashboard_id", "")  # never touch the operator's real panel

    from src.control import make_command_handler
    from src.telegram_bot import TelegramApprovalBot
    bot = TelegramApprovalBot(db=db)
    calls, failures, ours = [], [], []
    real_api = bot._api

    trace = "--trace" in sys.argv

    def api(method, **kw):
        started = time.perf_counter()
        try:
            result = real_api(method, **kw)
            calls.append(method)
            if method.startswith("send") and isinstance(result, dict) and result.get("message_id"):
                ours.append(result["message_id"])
            if trace:
                print(f"    {method:22} {time.perf_counter() - started:5.2f}s")
            return result
        except Exception as e:
            if trace:
                print(f"    {method:22} {time.perf_counter() - started:5.2f}s  error")
            synthetic = method == "answerCallbackQuery" or (  # synthetic ids cannot be answered or deleted
                method == "deleteMessage" and int((kw.get("data") or {}).get("message_id", 0)) >= SYNTHETIC_BASE) or (
                method == "deleteMessage" and "not found" in str(e))  # a panel already removed by Done: nothing left to do
            if not synthetic:
                failures.append((method, str(e)[:240]))
            raise
    bot._api = api
    handler = make_command_handler(db, lambda: None, lambda: "Runs are disabled in acceptance.",
                                   send_photo=bot.send_photo, send_audio=bot.send_audio,
                                   send_message=bot.notify, delete_message=bot.delete_message)
    chat, user = int(config.TELEGRAM_CHAT_ID), int(sorted(config.TELEGRAM_APPROVER_IDS)[0])
    bot.notify("🧪 <b>Scripted acceptance run</b> (dev bot, temporary database). Read-only commands and panels "
               "follow; no decision buttons are pressed.", record_alert=False)

    def message(text, n):
        return {"update_id": n, "message": {"message_id": SYNTHETIC_BASE + n, "text": text, "date": int(time.time()),
                                            "from": {"id": user}, "chat": {"id": chat}}}

    n = 1
    for text in TYPED:
        before, started = len(failures), time.perf_counter()
        bot._process_update(message(text, n), handler)
        bot.drain()
        print(f"{text:22} {'ok' if len(failures) == before else 'FAILED'}  {time.perf_counter() - started:5.2f}s")
        n += 1
    dashboard = bot.show_dashboard(fresh=True)
    bot.drain()
    for data in PANELS:
        before, started = len(failures), time.perf_counter()
        current = bot._current_panel(dashboard) if isinstance(dashboard, int) else dashboard
        update = {"update_id": n, "callback_query": {"id": f"acceptance-{n}", "data": data, "from": {"id": user},
                                                     "message": {"message_id": current, "chat": {"id": chat}}}}
        bot._process_update(update, handler)
        bot.drain()
        print(f"panel {data:24} {'ok' if len(failures) == before else 'FAILED'}  {time.perf_counter() - started:5.2f}s")
        if data == "done:":  # Done removed the dashboard: open a fresh one for the next panels
            dashboard = bot.show_dashboard(fresh=True)
            bot.drain()
        n += 1
    bot.drain()
    if "--keep" not in sys.argv:  # leave the operator's chat as it was
        bot.delete_messages(sorted(set(ours)))
    print(f"\nBot API calls: {len(calls)}; failures: {len(failures)}")
    for method, error in failures:
        print(f"  {method}: {error}")
    shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
