"""QF-114 acceptance: what posts after a reviewed file is swapped behind an approved card.

    python measure.py <repo root>

Runs the code of <repo root> (the base too) on temporary databases with a stub publisher (nothing leaves the
machine). Each case delivers a run on a review card (its fingerprint recorded as the bot records it), approves it,
swaps one file or the copy, and asks each real posting path to post: the Reel's release path
(PipelineCoordinator._require_release, the step before every Reel upload, Post now and Retry; the release gate
itself is stubbed so only the binding decides), the Stories booking (_post_photos), a Stories retry (retry_post)
the YouTube Short button (post_youtube_short), and, round 2 (review 1, B3), the episode's finishing buttons
(set_facebook_cover, add_facebook_captions, set_youtube_thumbnail) and the carousel's check before it takes its slot
(_approval_broken). Counts the uploads that went out, and that an unchanged run still posts.
"""
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

import main
import src.publisher
import src.release
from src import config
from src.db import PipelineDatabase
from src.telegram_state import initialize, put, review_fingerprint

config.YOUTUBE_SHORTS = True
src.release.require_release = lambda run, dry_run=False: {}
src.release.story_credit_problems = lambda run_dir: []   # the frames are stand-ins: only the binding decides

def _drop_card(db):
    """The approved card's records gone (purged by the 90-day sweep before QF-114 kept them)."""
    with db._conn() as conn:
        conn.execute("DELETE FROM telegram_transport WHERE namespace IN ('review-card', 'review-files')")


SWAPS = {
    "none": lambda f, db: None,
    "master": lambda f, db: (f / "master.mp4").write_bytes(b"another video"),
    "composition record": lambda f, db: (f / "master.composition.json").write_text('{"master_sha256": "y"}'),
    "provenance ledger": lambda f, db: (f / "provenance.json").write_text('{"master": {"sha256": "y"}}'),
    "cover": lambda f, db: (f / "thumbnail_cover.jpg").write_bytes(b"another cover"),
    "public copy": lambda f, db: db.update_run("run_b", "approved", script_payload={"caption": "a new caption"}),
    "captions": lambda f, db: (f / "master.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOther words\n"),
    "card record gone": lambda f, db: _drop_card(db),
}


def case(tmp: Path, swap):
    bank = tmp / "bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "t1", "title": "T", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=tmp / "p.db", topic_bank_file=bank)
    initialize(db)
    folder = tmp / "run_b"
    (folder / "stories").mkdir(parents=True)
    (folder / "master.mp4").write_bytes(b"offline video")
    (folder / "thumbnail_cover.jpg").write_bytes(b"offline cover")
    (folder / "master.composition.json").write_text('{"master_sha256": "x"}')
    (folder / "provenance.json").write_text('{"master": {"sha256": "x"}}')
    (folder / "master.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nWords\n")
    frame = folder / "stories" / "story_02_reveal.jpg"
    frame.write_bytes(b"frame")
    db.create_run("run_b", "t1")
    db.update_run("run_b", "pending_approval", master_video_path=folder / "master.mp4",
                  thumbnail_cover_path=folder / "thumbnail_cover.jpg", thumbnail_grid_path=folder / "thumbnail_cover.jpg",
                  script_payload={"caption": "the approved caption"})
    db.record_approval("run_b", "t1", 77)
    fingerprint = review_fingerprint(db, "run_b")
    put(db, "review-media", "run_b", fingerprint)
    put(db, "review-card", "run_b:77", fingerprint)
    db.update_approval_decision("run_b", "approved", "operator")
    swap(folder, db)
    sent = []
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c._alert = lambda *a, **k: None
    c._recheck_facts = lambda run, retry_now=False: None
    c.publisher = SimpleNamespace(
        publish_photos=lambda *a, **k: sent.append("photos") or {"status": "published", "post_urls": {}},
        publish_short=lambda *a, **k: sent.append("short") or {"status": "published", "post_urls": {}},
        build_envelope=lambda *a, **k: {"platforms": ["instagram"]})
    went = {}
    try:
        c._require_release(db.get_run("run_b"))
        went["reel"] = True
    except Exception:  # noqa: BLE001 - refused: nothing uploaded
        went["reel"] = False
    slot = datetime(2026, 11, 10, 17, 45, tzinfo=UTC).isoformat()
    story = db.add_scheduled_post("run_b", "story", slot, [str(frame)])
    before = len(sent)
    booking = next(p for p in db.list_scheduled_posts(("pending",)) if p["id"] == story)
    c._post_photos(booking, db.get_run("run_b"), "story")
    went["stories"] = len(sent) > before
    retry = db.add_scheduled_post("run_b", "story", slot, [str(frame)])
    db.update_scheduled_post(retry, "unknown", detail={"status": "unknown", "posted_parts": 1, "part": 2,
                                                       "failed": {"instagram": "media error"}})
    before = len(sent)
    c.retry_post(retry)
    went["stories retry"] = len(sent) > before
    before = len(sent)
    c.post_youtube_short("run_b")
    went["youtube short"] = len(sent) > before

    class Recorder:   # every Facebook and Upload-Post finishing upload, recorded instead of sent
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: sent.append(name) or {"set": True, "locale": "en_US"}
    src.publisher.FacebookPageReelsPublisher = Recorder
    src.publisher.UploadPostPublisher = Recorder
    c.facebook_video_id = lambda db, run_id: "fb1"
    c.youtube_short_id = lambda db, run_id: "yt1"
    for name, button in (("facebook cover", c.set_facebook_cover), ("facebook captions", c.add_facebook_captions),
                         ("youtube thumbnail", c.set_youtube_thumbnail)):
        before = len(sent)
        button("run_b")
        went[name] = len(sent) > before
    went["carousel takes its slot"] = c._approval_broken("run_b") is None
    return went


out = {}
with tempfile.TemporaryDirectory() as tmp:
    for i, (name, swap) in enumerate(SWAPS.items()):
        base = Path(tmp) / str(i)
        base.mkdir()
        out[name] = case(base, swap)
swapped = {k: v for k, v in out.items() if k != "none"}
print(json.dumps({"posted_by_path": out,
                  "uploads_of_a_swapped_review": sum(sum(v.values()) for v in swapped.values()),
                  "upload_attempts_of_a_swapped_review": sum(len(v) for v in swapped.values()),
                  "unchanged_review_posts_on_every_path": all(out["none"].values())}, indent=1))
