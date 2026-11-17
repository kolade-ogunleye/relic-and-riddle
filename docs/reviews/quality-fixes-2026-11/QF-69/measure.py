"""QF-69 measurements: the V2 review card's invented literals, and every Undo/cancel of a Reel that began to post.

    python measure.py <repo root>

Runs on the base (6a7ef303) and on the commit. Reads committed inputs only (the measured root's src/, main.py and
tracked data/; the real-run fixture tests/fixtures/ui02_review_v2/ of the measured root), prints JSON, writes nothing
in the repository (a throwaway database and app root in the system temp folder; no .env is read: that app root has
none). No network: nothing is sent (the bots' _api is a stub; the card is only built).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(sys.argv[1]).resolve()
TMP = Path(tempfile.mkdtemp(prefix="qf69-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)   # its .env is the app root's, and this one has none
for tracked in subprocess.run(["git", "-C", str(ROOT), "ls-files", "data", "fonts"], capture_output=True, text=True,
                              check=True).stdout.splitlines():   # the committed data files and fonts only
    (TMP / tracked).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / tracked, TMP / tracked)
sys.path.insert(0, str(ROOT))

# The ten values the V2 card invented (review 2026-11-05, QF-69), with the stock copy UI-02 also removed
LITERALS = ("9 distinct / 10 beats", "✅ Cleared", "~$0.41", "Gemini 2.5 Pro", "ElevenLabs · Archivist",
            "Ancient Winds · licensed", "rc70", "made just now", "Ancient discoveries that defy conventional history.",
            "What do you think was the true purpose? #ancientmysteries #history")
RUN = "run_gol_gumbaz_whispering_gallery_1793278143"
FIXTURE = ROOT / "tests" / "fixtures" / "ui02_review_v2" / RUN
_N = [0]


def database():
    from src.db import PipelineDatabase
    from src.telegram_state import initialize
    _N[0] += 1
    bank = TMP / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / f"m{_N[0]}.db", topic_bank_file=bank)
    initialize(db)
    return db


def v2_card_literals() -> int:
    from src import config
    from src.telegram_bot import TelegramApprovalBot
    db = database()
    run_dir = TMP / RUN
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "master.mp4").write_bytes(b"x")
    payload = json.loads((run_dir / "script_metadata.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / "release_report.json").read_text(encoding="utf-8"))
    topic = db.list_all_topics()[0]["id"]
    payload["topic_id"] = topic
    db.create_run(RUN, topic)
    db.update_run(RUN, "pending_approval", script_payload=payload, master_video_path=run_dir / "master.mp4")
    config.TELEGRAM_CARDS_V2 = True
    bot = TelegramApprovalBot(db=db, bot_token="1:x", chat_id="1", approver_ids={"1"})
    bot._api = lambda *a, **k: {"message_id": 1}
    blocks = bot._v2_review_plan(RUN, payload, report, run_dir)["blocks"]
    config.TELEGRAM_CARDS_V2 = False
    text = json.dumps(blocks, ensure_ascii=False)
    return sum(1 for literal in LITERALS if literal in text)


def coordinator(db):
    import main
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.schedule_posts, c.only_promoted = db, True, False
    c.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    c.uploads = []
    c._publish_run_locked = lambda rid, *a, **k: c.uploads.append(rid) or {"status": "published", "publish_result": {}}
    c._may_post = lambda rid: True
    c._require_release = lambda *a, **k: None
    c._alert = lambda *a, **k: None   # notices go nowhere
    return c


def booked(db, run_id, status="scheduled"):
    topic = db.list_all_topics()[0]["id"]
    db.create_run(run_id, topic)
    (TMP / run_id).mkdir(exist_ok=True)
    db.update_run(run_id, status, master_video_path=TMP / run_id / "m.mp4", script_payload={"title": "T"})
    slot = datetime.now(UTC) + timedelta(hours=3)
    reel = db.add_scheduled_post(run_id, "reel", slot.isoformat(), [])
    db.add_scheduled_post(run_id, "story", (slot + timedelta(minutes=45)).isoformat(), ["s.jpg"])
    return reel


def booking_status(db, post_id) -> str:
    with db._conn() as conn:
        return conn.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()["status"]


def posting_cases() -> dict:
    """Each case: a Reel that has begun to post (or whose cancelled booking a pass or Post now had already read).
    'cancelled' counts a booking marked cancelled while its Reel was being or had been posted; 'uploaded' counts an
    upload of a booking the operator had cancelled."""
    from src.publication_lock import publication_lock
    out = {}
    for posting in ("publishing", "published", "publish_unknown"):
        db = database()
        c = coordinator(db)
        reel = booked(db, "r_" + posting)
        db.update_run("r_" + posting, posting)
        reply = c.cancel_post(reel)
        out[f"cancel while {posting}"] = {"refused": reply.startswith("⏹ Not cancelled"),
                                          "booking": booking_status(db, reel)}
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_unknown_pub")
    db.record_publication(db.list_all_topics()[0]["id"], "r_unknown_pub", "upload-post:reel", "unknown", {})
    reply = c.cancel_post(reel)
    out["cancel with an unknown-outcome publication"] = {"refused": reply.startswith("⏹ Not cancelled"),
                                                         "booking": booking_status(db, reel)}
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_locked")
    db.update_run("r_locked", "publishing")
    with publication_lock(db.db_path, "r_locked"):   # the upload in progress: claimed, its lock held
        reply = c.cancel_post(reel)
    out["cancel while the upload holds its lock"] = {"refused": reply.startswith("⏹ Not cancelled"),
                                                     "booking": booking_status(db, reel)}
    # Round 2: the lock is also held for the release checks before the claim; nothing has posted then
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_checks")
    with publication_lock(db.db_path, "r_checks"):
        reply = c.cancel_post(reel)
    out["control: cancel during the checks before the claim"] = {
        "cancelled": reply.startswith("⏹ Booking cancelled"), "booking": booking_status(db, reel)}
    # A posting pass read the booking, then the operator's cancel landed, then the pass uploaded
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_race")
    post = next(p for p in db.list_scheduled_posts(("pending",), run_id="r_race") if p["kind"] == "reel")
    c.cancel_post(reel)
    post["due_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    try:
        c._post_one_due(post, datetime.now(UTC))
    except ValueError:
        pass
    out["pass read the booking, then it was cancelled"] = {"uploaded": len(c.uploads), "booking": booking_status(db, reel)}
    # Post now read the booking, then the operator's cancel landed
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_now")
    real = db.list_scheduled_posts

    def cancelled_between(statuses=("pending", "waiting"), run_id=None):
        rows = real(statuses, run_id=run_id)
        if statuses == ("pending", "blocked") and rows:
            c.cancel_post(rows[0]["id"])
        return rows
    db.list_scheduled_posts = cancelled_between
    c.post_now("r_now")
    out["post now read the booking, then it was cancelled"] = {"uploaded": len(c.uploads),
                                                               "booking": booking_status(db, reel)}
    # Round 2: the poster's other writes, each after it read the booking and the operator then cancelled it
    revived = {}
    for writer in ("missed-slot reschedule", "hold", "block", "move"):
        db = database()
        c = coordinator(db)
        c._reel_slot = lambda rid, after, days: datetime.now(UTC) + timedelta(days=2)
        c._block_reasons = lambda rid, exc: ["x"]
        c._episode_action = lambda rid: []
        reel = booked(db, "r_w")
        post = next(p for p in db.list_scheduled_posts(("pending",), run_id="r_w") if p["kind"] == "reel")
        c.cancel_post(reel)
        if writer == "hold":
            c._hold_booking(post, ConnectionError("x"))
        elif writer == "block":
            c._block_booking(post, ValueError("Release blocked: x"), datetime.now(UTC))
        elif writer == "move":
            c.move_post(reel)
        else:
            post["due_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
            c._post_one_due(post, datetime.now(UTC))
        revived[writer] = booking_status(db, reel)
    out["writes after a cancel"] = revived
    db = database()
    c = coordinator(db)
    reel = booked(db, "r_before")
    reply = c.cancel_post(reel)
    out["control: cancel before posting"] = {"cancelled": reply.startswith("⏹ Booking cancelled"),
                                             "booking": booking_status(db, reel)}
    return out


def main_() -> None:
    try:
        literals = v2_card_literals()
        cases = posting_cases()
        began = [v for k, v in cases.items() if k.startswith("cancel ")]
        revived = sum(1 for s in cases["writes after a cancel"].values() if s != "cancelled")
        races = [v for k, v in cases.items() if "then it was cancelled" in k]
        refused = sum(1 for v in began if v["refused"] and v["booking"] == "pending")
        uploaded = sum(v["uploaded"] for v in races)
        result = {
            "v2_card_invented_literals": literals,
            "undo_of_a_posting_reel_refused": f"{refused}/{len(began)}",
            "cancelled_bookings_uploaded": uploaded,
            "cancelled_bookings_brought_back_by_a_later_write": f"{revived}/{len(cases['writes after a cancel'])}",
            "control_cancel_before_posting_works": cases["control: cancel before posting"]["booking"] == "cancelled",
            "control_cancel_during_the_checks_works": cases["control: cancel during the checks before the claim"][
                "booking"] == "cancelled",
            "cases": cases,
        }
        result["acceptance_met"] = (literals == 0 and refused == len(began) and uploaded == 0 and revived == 0
                                    and result["control_cancel_before_posting_works"]
                                    and result["control_cancel_during_the_checks_works"])
        print(json.dumps(result, ensure_ascii=False, indent=1))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main_()
