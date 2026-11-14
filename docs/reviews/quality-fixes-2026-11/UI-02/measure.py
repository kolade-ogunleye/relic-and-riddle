"""UI-02 measurements: the publish receipt's header per outcome, and the invented values on the V2 review card.

    python measure.py <repo root>

Runs on the base (2debdf8b) and on the commit. Reads committed inputs only (the measured root's src/ and data/; the
real-run fixture tests/fixtures/ui02_review_v2/ of this script's own checkout, which the base lacks), prints JSON,
writes nothing in the repository (a throwaway database and app root in the system temp folder; no .env is read:
that app root has none). No network: nothing is sent (the receipt's bot is a stub; the card is only built).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui02-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)   # its .env is the app root's, and this one has none
for tracked in subprocess.run(["git", "-C", str(ROOT), "ls-files", "data"], capture_output=True, text=True,
                              check=True).stdout.splitlines():   # the committed data files only (reference facts...)
    (TMP / tracked).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / tracked, TMP / tracked)
sys.path.insert(0, str(ROOT))

PLACEHOLDERS = ("9 distinct / 10 beats", "✅ Cleared", "~$0.41", "$0.41", "Gemini 2.5 Pro", "ElevenLabs · Archivist",
                "Ancient Winds · licensed", "rc70", "made just now", "Ancient discoveries that defy conventional history.",
                "What do you think was the true purpose? #ancientmysteries #history",
                "Picture checks passed against narration", "Voice and audio pacing verified", "2 notes to check",
                "1 check failed")
RUN = "run_gol_gumbaz_whispering_gallery_1793278143"
# The committed fixture beside this script's checkout (the base has none: it is measured from the same records)
FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "ui02_review_v2"


class Bot:
    def __init__(self):
        self.texts, self.lines = [], []

    def notify(self, text, buttons=None, **kw):
        (self.texts if buttons else self.lines).append(text)
        return 1

    def edit_message_text(self, mid, text, buttons=None):
        self.texts.append(text)
        return True


def receipts() -> dict:
    from src import config
    from src.telegram_bot import PublishReceipt
    config.YOUTUBE_SHORTS = True
    cases = {
        "all ok": [("instagram", "live"), ("facebook", "live"), ("youtube", "live")],
        "instagram failed": [("instagram", "failed"), ("facebook", "live"), ("youtube", "live")],
        "all failed": [("instagram", "failed"), ("facebook", "failed"), ("youtube", "failed")],
        "short skipped": [("instagram", "live"), ("facebook", "live"), ("youtube", "skipped")],
    }
    out = {}
    for name, steps in cases.items():
        bot = Bot()
        r = PublishReceipt(bot, "m", "Title")
        for key, status in steps:
            r.update_platform(key, status, error="reason" if status in ("failed", "skipped") else None)
        r.finalize()
        final = bot.texts[-1]
        out[name] = {"header": final.splitlines()[0].replace("<b>", "").replace("</b>", ""),
                     "runs_line": bot.lines[-1], "lines_with_tick": final.count("✅")}
    return out


def v2_card() -> dict:
    from src import config
    from src.db import PipelineDatabase
    from src.telegram_bot import TelegramApprovalBot
    from src.telegram_state import initialize
    bank = TMP / "topic_bank.json"   # one stand-in topic, as the tests' database has
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / "m.db", topic_bank_file=bank)
    initialize(db)
    run_dir = TMP / RUN
    shutil.copytree(FIXTURES / RUN, run_dir)
    (run_dir / "master.mp4").write_bytes(b"x")
    payload = json.loads((run_dir / "script_metadata.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "release_report.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic
    db.create_run(RUN, topic)
    db.update_run(RUN, "pending_approval", script_payload=payload, master_video_path=run_dir / "master.mp4")
    config.TELEGRAM_CARDS_V2 = True
    bot = TelegramApprovalBot(db=db, bot_token="1:x", chat_id="1", approver_ids={"1"})
    if hasattr(bot, "_v2_review_plan"):
        blocks = bot._v2_review_plan(RUN, payload, report, run_dir)["blocks"]
    else:   # the base: the card as it was built
        from src.release import blocking_checks
        blocks = bot._build_review_rich_blocks(RUN, payload, report, bool(blocking_checks(report, dry_run=report.get("dry_run", False))))
    text = json.dumps(blocks, ensure_ascii=False)
    found = [p for p in PLACEHOLDERS if p in text]
    stripped = dict(report, checks=[c for c in report["checks"] if c["name"] != "duration"])
    try:
        if hasattr(bot, "_v2_review_plan"):
            bot._v2_review_plan(RUN, payload, stripped, run_dir)
        else:
            bot._build_review_rich_blocks(RUN, payload, stripped, False)
        missing_length = "V2 card sent"
    except Exception as e:  # noqa: BLE001
        missing_length = f"live card instead ({type(e).__name__})"
    return {"placeholder_literals_on_card": len(found), "placeholders": found,
            "length_shown": next((r[1]["text"] for b in blocks if b.get("type") == "table" for r in b["cells"]
                                  if r[0]["text"].get("text") == "Length"), None),
            "run_without_a_measured_length": missing_length}


def main() -> None:
    try:
        print(json.dumps({"receipt": receipts(), "v2_card": v2_card()}, ensure_ascii=False, indent=1))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
