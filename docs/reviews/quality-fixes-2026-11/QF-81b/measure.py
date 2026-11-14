"""QF-81b acceptance, offline: python measure.py <repo root>. Prints JSON; writes only a temporary folder.

Runs on the code at <repo root> (the QF-81 parent and the QF-81b commit alike). For each probe, one planned run is
made as QF-81's test fixture makes it (carousel_lazy.plan for real, over two photos of its own), its Reel recorded as
posted three days before the Tuesday 10 Nov 2026 carousel slot, its carousel booked with the real
main.PipelineCoordinator._book_followers; then it is approved, and the real daemon pass
(main.PipelineCoordinator._post_carousel_if_due) runs at the slot. The renderer double draws three slide files per
copy; the slide audit passes; the publisher is a recorder; the release gate passes.

Probes:
- plan_rewritten_after_approval: approved on the review card (its fingerprint recorded as telegram_bot does), then
  carousel_plan.json rewritten so its second photo is another one, with that photo's correct SHA-256.
- unchanged_plan: approved on the card, nothing changed.
- preview_after_approval: approved on the card, the card's Preview carousel drawn, then the slot.
- approval_without_card: approved from the command line (no card fingerprint recorded).

For each: whether the approved fingerprint still matches at the slot, the carousel's booking status, whether it was
posted and whether the substituted photo was drawn.

Round 2 (review 1, blocking 1), on plan_rewritten_after_approval's hold:
- hold_alert: whether the alert asks for something that cannot be done for a published run ("make a new version and
  approve it"), whether it offers the Redraw button, and whether it says the carousel will not post.
- redraw_then_newer_episode: the held run's Redraw tapped after a newer episode (r2) is made ready, then the next
  carousel slot (Wed): whether the Redraw was refused, which run posted there, and how many hold alerts there were.
- newest_mismatched_older_ready: the newest episode's plan rewritten after approval, an older episode ready at the
  same slot (Tue): which run posted at that slot.

Network connections are refused; it refuses a root holding .env.
"""

import json
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
TUE = datetime(2026, 11, 10, tzinfo=ET)
CARD = 77


class Frozen(datetime):
    now_value = TUE

    @classmethod
    def now(cls, tz=None):
        return cls.now_value.astimezone(tz) if tz else cls.now_value.astimezone().replace(tzinfo=None)


def main():
    root = Path(sys.argv[1]).resolve()
    if (root / ".env").exists():
        raise SystemExit("run it on a checkout without a .env (a worktree): it must not read one")
    os.environ["FONT_SIZE"] = "80"
    sys.path.insert(0, str(root))
    os.chdir(root)

    def refuse(*a, **k):
        raise OSError("network refused (offline measurement)")
    socket.socket.connect = refuse
    socket.create_connection = refuse

    from PIL import Image

    import main as pipeline
    from src import carousel_lazy, config, posting_view
    from src import db as db_module
    from src import post_schedule as ps
    from src.db import PipelineDatabase
    from src.telegram_state import put, review_fingerprint

    for name, value in (("VIDEO_FORMATS", ["reel"]), ("YOUTUBE_SHORTS", False), ("BOT_RUNS_DRY", True)):
        setattr(config, name, value)
    data = json.loads((root / "data" / "scripts" / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
    payload0 = {k: v for k, v in data.items() if not k.startswith("_")}
    assert payload0.get("carousel_slides"), "the curated script has carousel cards"

    def photo(path, shade):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 80), (shade, 70, 50)).save(path)
        return path

    def probe(tmp, approve, change=None, preview=False):
        bank = tmp / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=tmp / "test.db", topic_bank_file=bank)
        c = pipeline.PipelineCoordinator(db, schedule_posts=True)
        posted, drawn_from, alerts, buttons = [], [], [], []
        c._alert = lambda text, buttons=None, _b=buttons: alerts.append(text) or _b.append(buttons)
        c.telegram_bot.stamp_review_outcome = lambda run_id, text: True

        def draw(stills, payload, output_dir, facebook=False, graphics=None, strict_match=False):
            drawn_from.extend(str(s) for s in stills)
            folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
            return [photo(folder / f"carousel_slide_{n:02d}.jpg", 90) for n in range(1, 4)]
        c.multiformat_gen.generate_carousel_sequence = draw
        c.publisher.publish_photos = lambda run_id, tid, kind, files, caption="", **k: (
            posted.append((run_id, kind)) or {"status": "published"})
        run_id, run_dir = "r1", tmp / "r1"
        run_dir.mkdir()
        (run_dir / "m.mp4").write_bytes(b"0")
        carousel_lazy.plan(run_dir, [photo(run_dir / "photos" / "p1.jpg", 90), photo(run_dir / "photos" / "p2.jpg", 40)],
                           dict(payload0))
        db.create_run(run_id, "01_topic")
        db.update_run(run_id, "pending_approval", script_payload=dict(payload0, topic_id="01_topic"),
                      master_video_path=run_dir / "m.mp4", thumbnail_cover_path=run_dir / "m.mp4",
                      thumbnail_grid_path=run_dir / "m.mp4")
        reel_at = TUE - timedelta(days=3)
        c._book_followers(run_id, run_dir, reel_at)
        reel = db.add_scheduled_post(run_id, "reel", reel_at.astimezone(UTC).isoformat(), [])
        db.update_scheduled_post(reel, "published", detail={"posted_at": reel_at.astimezone(UTC).isoformat()})
        db.update_run(run_id, "published")
        shown = None
        if approve == "card":
            shown = review_fingerprint(db, run_id)
            put(db, "review-card", f"{run_id}:{CARD}", shown)
            db.record_approval(run_id, "01_topic", CARD)
        else:
            db.record_approval(run_id, "01_topic")
        db.update_approval_decision(run_id, "approved", decided_by=approve)
        other = None
        if change == "substitute_photo":
            other = photo(run_dir / "photos" / "other.jpg", 210)
            record = json.loads((run_dir / carousel_lazy.PLAN).read_text(encoding="utf-8"))
            record["stills"][1] = {"path": str(other), "sha256": carousel_lazy._sha256(other)}
            (run_dir / carousel_lazy.PLAN).write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")
        if preview:
            c.preview_carousel(run_id)
        matches = None if shown is None else review_fingerprint(db, run_id) == shown
        slot = ps.carousel_time(TUE.date())
        Frozen.now_value = slot + timedelta(minutes=1)
        c._post_carousel_if_due(slot + timedelta(minutes=1))
        post = next(p for p in db.list_scheduled_posts(("waiting", "published", "failed", "expired"), run_id=run_id)
                    if p["kind"] == "carousel")
        error = str((post.get("detail") or {}).get("error", ""))
        result = {"fingerprint_still_matches": matches, "status": post["status"],
                  "carousel_posted": ("r1", "carousel") in posted,
                  "substituted_photo_drawn": bool(other) and str(other) in drawn_from,
                  "held_reason_says_approved": "since you approved" in error or "after you approved" in error}
        if change:
            text = alerts[-1] if alerts else ""
            result["hold_alert"] = {
                "asks_for_a_new_version": "new version" in text or "new version" in error,
                "offers_redraw": any(b.get("callback_data", "").startswith("carredraw:")
                                     for row in ((buttons[-1] if buttons else None) or []) for b in row),
                "says_it_will_not_post": "will not post" in text}
        return result

    def second_slot(tmp, newest_mismatched):
        """Two planned runs, both approved on their cards (see the module docstring for the two probes)."""
        bank = tmp / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=tmp / "test.db", topic_bank_file=bank)
        c = pipeline.PipelineCoordinator(db, schedule_posts=True)
        posted, alerts = [], []
        c._alert = lambda text, buttons=None: alerts.append(text)
        c.telegram_bot.stamp_review_outcome = lambda run_id, text: True

        def draw(stills, payload, output_dir, facebook=False, graphics=None, strict_match=False):
            folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
            return [photo(folder / f"carousel_slide_{n:02d}.jpg", 90) for n in range(1, 4)]
        c.multiformat_gen.generate_carousel_sequence = draw
        c.publisher.publish_photos = lambda run_id, tid, kind, files, caption="", **k: (
            posted.append(run_id) or {"status": "published"})

        def make(run_id, reel_at):
            run_dir = tmp / run_id
            run_dir.mkdir()
            (run_dir / "m.mp4").write_bytes(b"0")
            carousel_lazy.plan(run_dir, [photo(run_dir / "photos" / "p1.jpg", 90),
                                         photo(run_dir / "photos" / "p2.jpg", 40)], dict(payload0))
            db.create_run(run_id, "01_topic")
            db.update_run(run_id, "pending_approval", script_payload=dict(payload0, topic_id="01_topic"),
                          master_video_path=run_dir / "m.mp4", thumbnail_cover_path=run_dir / "m.mp4",
                          thumbnail_grid_path=run_dir / "m.mp4")
            c._book_followers(run_id, run_dir, reel_at)
            reel = db.add_scheduled_post(run_id, "reel", reel_at.astimezone(UTC).isoformat(), [])
            db.update_scheduled_post(reel, "published", detail={"posted_at": reel_at.astimezone(UTC).isoformat()})
            db.update_run(run_id, "published")
            put(db, "review-card", f"{run_id}:{CARD}", review_fingerprint(db, run_id))
            db.record_approval(run_id, "01_topic", CARD)
            db.update_approval_decision(run_id, "approved", decided_by="card")
            return run_dir

        def substitute(run_dir):
            other = photo(run_dir / "photos" / "other.jpg", 210)
            record = json.loads((run_dir / carousel_lazy.PLAN).read_text(encoding="utf-8"))
            record["stills"][1] = {"path": str(other), "sha256": carousel_lazy._sha256(other)}
            (run_dir / carousel_lazy.PLAN).write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")

        def at_slot(day):
            slot = ps.carousel_time(day.date())
            Frozen.now_value = slot + timedelta(minutes=1)
            c._post_carousel_if_due(slot + timedelta(minutes=1))

        def status(run_id):
            return next(p for p in db.list_scheduled_posts(("waiting", "published", "failed", "expired"), run_id=run_id)
                        if p["kind"] == "carousel")["status"]

        holds = lambda: sum("Carousel held" in a or "Carousel not posted" in a for a in alerts)
        if newest_mismatched:
            make("r1", TUE - timedelta(days=3, hours=6))
            substitute(make("r2", TUE - timedelta(days=3)))
            at_slot(TUE)
            return {"posted_at_slot": posted, "r1_status": status("r1"), "r2_status": status("r2")}
        substitute(make("r1", TUE - timedelta(days=3)))
        at_slot(TUE)
        held = next((p for p in db.list_scheduled_posts(("failed",), run_id="r1") if p["kind"] == "carousel"), None)
        first = list(posted)
        make("r2", TUE - timedelta(days=2, hours=12))
        Frozen.now_value = ps.carousel_time(TUE.date()) + timedelta(hours=2)
        reply = c.redraw_carousel(str(held["id"])) if held else None   # 47ae674 posted r1 at Tue: nothing to redraw
        del posted[:]
        at_slot(TUE + timedelta(days=1))
        return {"held_at_first_slot": held is not None, "posted_at_first_slot": first,
                "redraw_refused": None if reply is None else "is drawn again" not in reply,
                "posted_next_slot": posted, "hold_alerts": holds(), "r1_status": status("r1"), "r2_status": status("r2")}

    probes = {"plan_rewritten_after_approval": ("card", "substitute_photo", False),
              "unchanged_plan": ("card", None, False),
              "preview_after_approval": ("card", None, True),
              "approval_without_card": ("cli", None, False)}
    out = {}
    with patch.object(db_module, "datetime", Frozen), patch.object(posting_view, "datetime", Frozen), \
            patch.object(ps, "datetime", Frozen), patch.object(pipeline, "datetime", Frozen), \
            patch("src.release.require_release", lambda *a, **k: {"status": "pass"}), \
            patch("main.QualityAuditor.audit_carousel_slides",
                  lambda self, d, payload=None: {"score": 100, "certified": True, "issues": []}):
        for name, (approve, change, preview) in probes.items():
            Frozen.now_value = TUE
            with tempfile.TemporaryDirectory() as tmp:
                out[name] = probe(Path(tmp), approve, change, preview)
        for name, newest_mismatched in (("redraw_then_newer_episode", False), ("newest_mismatched_older_ready", True)):
            Frozen.now_value = TUE
            with tempfile.TemporaryDirectory() as tmp:
                out[name] = second_slot(Path(tmp), newest_mismatched)
    out["substituted_plans_posted"] = int(out["plan_rewritten_after_approval"]["carousel_posted"])
    out["normal_runs_held"] = sum(not out[n]["carousel_posted"] for n in probes if n != "plan_rewritten_after_approval")
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
