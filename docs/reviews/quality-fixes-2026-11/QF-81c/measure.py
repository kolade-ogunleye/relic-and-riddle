"""QF-81c acceptance, offline: python measure.py <repo root>. Prints JSON; writes only a temporary folder.

Runs on the code at <repo root> (the QF-81b commit and the QF-81c commit alike). Each probe makes two planned runs as
QF-81's test fixture makes them (carousel_lazy.plan for real, over two photos of their own, their carousels booked
with the real main.PipelineCoordinator._book_followers): an older r1 and the newest r2, whose Reels posted three days
before the Tuesday 10 Nov 2026 carousel slot. r2 is approved on its review card (the fingerprint recorded as
telegram_bot does) where the probe says so. Then r2's carousel is damaged as the probe says, and the real daemon pass
(main.PipelineCoordinator._post_carousel_if_due) runs at the slot, and once more in the same slot (the daemon's next
pass). The renderer double draws three slide files per copy, from stills only (given none, its photo match fails, as
the real renderer's does); the slide audit passes; the publisher is a recorder; the release gate passes.

Probes (r2's carousel):
- plan_deleted: carousel_plan.json deleted after approval (no card record).
- plan_deleted_card_approval: the same, approved on the review card.
- plan_unreadable: the plan's bytes are not UTF-8 text.
- plan_corrupt: the plan is cut off mid-JSON.
- plan_without_photos: the plan is JSON without its photo list.
- slides_gone: r2 is a carousel drawn with its run (before QF-81, three slides), its booked slides deleted.
- partial_deck: the same, only its first slide deleted (QF-81d: the two left were posted as "Carousel posted").
- drawn_slide_changed_card_approval: the same, approved on the card, every slide there but the second one redrawn
  with other bytes (QF-81d).
- plan_changed_in_lock: approved on the card; the slot's checks pass, then the plan is rewritten to name another photo
  (with its correct SHA-256) while the slot holds the carousel's publication lock, before the draw.
- plan_deleted_in_lock: the same, the plan deleted instead.
- plan_no_photo: r2's plan as the make itself writes it (carousel_lazy.plan) when the run's scene media are all video
  or none is on disk: its photo list is empty (QF-81c review, round 1, blocking 1).
- plan_video_entries: the plan lists only a video clip (with its SHA-256): fewer stills than a deck needs.
- plan_no_photo_two_slots: plan_no_photo with a third, oldest episode r0 ready, over two slots (Tuesday, then
  Wednesday); after Tuesday the hold alert's Redraw is tapped when r2 is held (the reviewer's two-slot probe).
- normal: nothing damaged, approved on the card (r2 posts).

For each: r2's booking status, whether r2 was posted, whether the substituted photo was drawn, the number of hold or
"not posted" alerts (only r0 and r1 ever post), whether that alert says nothing was posted and that the Reel and other
posts are unaffected, whether any of them offers Redraw, and which runs posted. A silent failure is r2 failed with no
alert; slot_lost counts the slots in which nothing posted (only r0 and r1 may post); the two-slot probe also records
what posted in each slot and whether the Redraw was accepted (a Redraw that cannot help, booked first for the next
slot).

Network connections are refused; it refuses a root holding .env.
"""

import contextlib
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
DRAWN = ("slides_gone", "partial_deck", "drawn_slide_changed_card_approval")   # r2 drawn with its run (before QF-81)
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
    from src import publication_lock as pl
    from src.db import PipelineDatabase
    from src.multi_format import PhotoMatchFailed
    from src.telegram_state import put, review_fingerprint
    from src.typography import is_video

    for name, value in (("VIDEO_FORMATS", ["reel"]), ("YOUTUBE_SHORTS", False), ("BOT_RUNS_DRY", True)):
        setattr(config, name, value)
    data = json.loads((root / "data" / "scripts" / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
    payload0 = {k: v for k, v in data.items() if not k.startswith("_")}
    assert payload0.get("carousel_slides"), "the curated script has carousel cards"
    real_lock = pl.publication_lock

    def photo(path, shade):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 80), (shade, 70, 50)).save(path)
        return path

    def probe(tmp, damage):
        bank = tmp / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=tmp / "test.db", topic_bank_file=bank)
        c = pipeline.PipelineCoordinator(db, schedule_posts=True)
        posted, drawn_from, alerts = [], [], []
        c._alert = lambda text, buttons=None: alerts.append((text, buttons))
        c.telegram_bot.stamp_review_outcome = lambda run_id, text: True

        def draw(stills, payload, output_dir, facebook=False, graphics=None, strict_match=False):
            drawn_from.extend(str(s) for s in stills)
            if not [s for s in stills if not is_video(Path(s))]:   # as the renderer: it draws stills only, and its
                raise PhotoMatchFailed("its photos could not be matched to its slides (no still)")   # match fails
            folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
            return [photo(folder / f"carousel_slide_{n:02d}.jpg", 90) for n in range(1, 4)]
        c.multiformat_gen.generate_carousel_sequence = draw
        c.publisher.publish_photos = lambda run_id, tid, kind, files, caption="", **k: (
            posted.append(run_id) or {"status": "published"})

        def make(run_id, reel_at, planned=True):
            run_dir = tmp / run_id
            run_dir.mkdir()
            (run_dir / "m.mp4").write_bytes(b"0")
            if planned:
                carousel_lazy.plan(run_dir, [photo(run_dir / "photos" / "p1.jpg", 90),
                                             photo(run_dir / "photos" / "p2.jpg", 40)], dict(payload0))
            else:
                for n in (1, 2, 3):
                    photo(run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg", 30 * n)
            db.create_run(run_id, "01_topic")
            db.update_run(run_id, "pending_approval", script_payload=dict(payload0, topic_id="01_topic"),
                          master_video_path=run_dir / "m.mp4", thumbnail_cover_path=run_dir / "m.mp4",
                          thumbnail_grid_path=run_dir / "m.mp4")
            c._book_followers(run_id, run_dir, reel_at)
            reel = db.add_scheduled_post(run_id, "reel", reel_at.astimezone(UTC).isoformat(), [])
            db.update_scheduled_post(reel, "published", detail={"posted_at": reel_at.astimezone(UTC).isoformat()})
            db.update_run(run_id, "published")
            return run_dir

        two_slots = damage == "plan_no_photo_two_slots"
        if two_slots:   # an older episode still, for the second slot
            make("r0", TUE - timedelta(days=3, hours=12))
        make("r1", TUE - timedelta(days=3, hours=6))
        run_dir = make("r2", TUE - timedelta(days=3), planned=damage not in DRAWN)
        if damage in ("plan_deleted_card_approval", "plan_changed_in_lock", "drawn_slide_changed_card_approval", "normal"):
            put(db, "review-card", f"r2:{CARD}", review_fingerprint(db, "r2"))
            db.record_approval("r2", "01_topic", CARD)
            db.update_approval_decision("r2", "approved", decided_by="card")
        plan = run_dir / carousel_lazy.PLAN
        other = photo(run_dir / "photos" / "other.jpg", 210)

        def substitute():
            record = json.loads(plan.read_text(encoding="utf-8"))
            record["stills"][1] = {"path": str(other), "sha256": carousel_lazy._sha256(other)}
            plan.write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")
        in_lock = {"plan_changed_in_lock": substitute,
                   "plan_deleted_in_lock": lambda: plan.unlink(missing_ok=True)}.get(damage)
        if damage in ("plan_deleted", "plan_deleted_card_approval"):
            plan.unlink()
        elif damage == "plan_unreadable":
            plan.write_bytes(b"\xff\xfe\x00not text")
        elif damage == "plan_corrupt":
            plan.write_bytes(plan.read_bytes()[:40])
        elif damage == "plan_without_photos":
            plan.write_text(json.dumps({"version": 2}), encoding="utf-8", newline="\n")
        elif damage == "slides_gone":
            for f in (run_dir / "carousel").glob("carousel_slide_*.jpg"):
                f.unlink()
        elif damage == "partial_deck":
            (run_dir / "carousel" / "carousel_slide_01.jpg").unlink()
        elif damage == "drawn_slide_changed_card_approval":
            photo(run_dir / "carousel" / "carousel_slide_02.jpg", 220)
        elif damage in ("plan_no_photo", "plan_no_photo_two_slots"):   # the make's own plan: no still on disk
            clip = run_dir / "clip.mp4"
            clip.write_bytes(b"not a still")
            carousel_lazy.plan(run_dir, [clip, run_dir / "photos" / "gone.jpg"], dict(payload0))
        elif damage == "plan_video_entries":
            clip = run_dir / "clip.mp4"
            clip.write_bytes(b"not a still")
            record = json.loads(plan.read_text(encoding="utf-8"))
            record["stills"] = [{"path": str(clip), "sha256": carousel_lazy._sha256(clip)}]
            plan.write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")

        @contextlib.contextmanager
        def lock(*a, **k):
            with real_lock(*a, **k):
                if in_lock and a[1] == "carousel:r2":
                    in_lock()
                yield

        def r2_post():
            return next(p for p in db.list_scheduled_posts(("waiting", "published", "failed", "expired"),
                                                           run_id="r2") if p["kind"] == "carousel")
        per_slot, redraw_accepted = [], False
        with patch.object(pl, "publication_lock", lock):
            for day in ((TUE, TUE + timedelta(days=1)) if two_slots else (TUE,)):
                slot, before = ps.carousel_time(day.date()), len(posted)
                for minutes in (1, 2):          # the slot's pass, then the daemon's next pass in the same slot
                    Frozen.now_value = slot + timedelta(minutes=minutes)
                    c._post_carousel_if_due(slot + timedelta(minutes=minutes))
                per_slot.append(posted[before:])
                if two_slots and day == TUE and r2_post()["status"] == "failed":   # the alert's Redraw tapped
                    redraw_accepted = "is drawn again" in c.redraw_carousel(str(r2_post()["id"]))
        post = r2_post()
        about = [(t, b) for t, b in alerts if "not posted" in t.lower() or "held" in t.lower()]   # r0, r1 only post
        text = about[-1][0] if about else ""
        failed = post["status"] == "failed"
        out = {"r2_status": post["status"], "r2_posted": "r2" in posted,
               "substituted_photo_drawn": str(other) in drawn_from,
               "r2_alerts": len(about), "posted_in_slot": posted,
               "alert_says_nothing_posted": "Nothing was posted" in text,
               "alert_says_reel_unaffected": "Reel and other posts are not affected" in text,
               "alert_offers_redraw": any(b.get("callback_data", "").startswith("carredraw:")
                                          for _, buttons in about for row in (buttons or []) for b in row),
               "silent_failure": failed and not about,
               "slot_lost": sum(not got for got in per_slot)}
        if two_slots:
            out.update(posted_per_slot=per_slot, redraw_accepted=redraw_accepted)
        return out

    damages = ("plan_deleted", "plan_deleted_card_approval", "plan_unreadable", "plan_corrupt", "plan_without_photos",
               "slides_gone", "plan_changed_in_lock", "plan_deleted_in_lock", "plan_no_photo", "plan_video_entries",
               "plan_no_photo_two_slots", "partial_deck", "drawn_slide_changed_card_approval", "normal")
    out = {}
    with patch.object(db_module, "datetime", Frozen), patch.object(posting_view, "datetime", Frozen), \
            patch.object(ps, "datetime", Frozen), patch.object(pipeline, "datetime", Frozen), \
            patch("src.release.require_release", lambda *a, **k: {"status": "pass"}), \
            patch("main.QualityAuditor.audit_carousel_slides",
                  lambda self, d, payload=None: {"score": 100, "certified": True, "issues": []}):
        for damage in damages:
            Frozen.now_value = TUE
            with tempfile.TemporaryDirectory() as tmp:
                out[damage] = probe(Path(tmp), damage)
    damaged = [d for d in damages if d != "normal"]
    out["silent_failures"] = sum(out[d]["silent_failure"] for d in damaged)
    out["slots_lost"] = sum(out[d]["slot_lost"] for d in damaged)
    out["damaged_carousels_posted"] = sum(out[d]["r2_posted"] for d in damaged)
    out["holds_offering_a_redraw_that_cannot_help"] = sum(out[d]["alert_offers_redraw"] for d in damaged)
    out["partial_decks_posted"] = sum(out[d]["r2_posted"] for d in ("partial_deck", "drawn_slide_changed_card_approval"))
    out["redraws_accepted_that_cannot_help"] = sum(out[d].get("redraw_accepted", False) for d in damaged)
    out["normal_runs_held"] = int(not out["normal"]["r2_posted"])
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
