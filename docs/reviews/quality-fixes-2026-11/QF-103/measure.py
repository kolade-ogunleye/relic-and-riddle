"""QF-103 acceptance: the /posts Stories line for every record shape the code has written.

    python measure.py <repo root>

Runs the code of <repo root> (the base too) on a temporary database: each case books a run's Stories, writes the
receipts and booking record of one period's code, and reads the episode's /posts screen (control.posting_episode).
Prints, per case, the Frames line shown and the frames that really went out (or, not yet sent, will post).

The production case is the shape QF-80 review 5 reconstructed from the code history: 2a90c5d6's publish_photos
returned {status unknown, error, posted_parts 2}, and c497369c's retry_post stored dict(detail, retries,
last_retry) with failed {} and a receipt numbered part 1. Its receipts' statuses and order are the production
sample's (published, published, unknown, published).
"""
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from PIL import Image

from src import control
from src.db import PipelineDatabase

FRAMES = ["story_01_hook.jpg", "story_02_reveal.jpg"]
POLL = "story_03_question.jpg"
CASES = {
    # name: (files, booking status, booking detail, receipts [(status, part)], frames that went out)
    "booked, two frames": (FRAMES, "pending", {}, [], 2),
    "booked before QF-80 with the poll frame": (FRAMES + [POLL], "pending", {}, [], 2),
    "posted before QF-80 (three frames)": (FRAMES + [POLL], "published", {"status": "published", "posted_parts": 3},
                                           [("published", 1), ("published", 2), ("published", 3)], 3),
    "posted, two frames": (FRAMES, "published", {"status": "published", "posted_parts": 2},
                           [("published", 1), ("published", 2)], 2),
    "production: frame 3 finished by a retry before c497369c": (
        FRAMES + [POLL], "published",
        {"status": "unknown", "error": "partial: failed=['instagram']", "posted_parts": 2, "retries": 1,
         "last_retry": {"status": "published", "error": None, "post_urls": {}}, "failed": {}},
        [("published", 1), ("published", 2), ("unknown", 3), ("published", 1)], 3),
    "reveal reached one platform": (FRAMES, "unknown", {"status": "unknown", "posted_parts": 1, "part": 2,
                                                        "failed": {"instagram": "media error"}},
                                    [("published", 1), ("unknown", 2)], 1),
    "reveal finished by today's retry": (FRAMES, "published", {"status": "unknown", "posted_parts": 1, "part": 2,
                                                               "failed": {}, "retries": 1,
                                                               "last_retry": {"status": "published"}},
                                         [("published", 1), ("unknown", 2), ("published", 2)], 2),
    "failed, nothing sent (its retry will post 2)": (FRAMES, "failed", {"error": "image files are missing",
                                                                        "posted_parts": 0}, [], 2),
}


def line(text: str) -> str | None:
    block = text.split("<b>Stories</b>")[1].split("\n\n")[0]
    found = [x.strip() for x in block.split("\n") if x.strip().startswith("Frames")]
    return found[0] if found else None


out = {}
with tempfile.TemporaryDirectory() as tmp:
    for i, (name, (files, status, detail, receipts, went_out)) in enumerate(CASES.items()):
        base = Path(tmp) / str(i)
        base.mkdir()
        bank = base / "bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "t1", "title": "T", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=base / "p.db", topic_bank_file=bank)
        db.create_run("run_k", "t1")
        db.update_run("run_k", "scheduled", master_video_path=base / "m.mp4", script_payload={"title": "K"})
        (base / "stories").mkdir()
        paths = []
        for f in files:
            Image.new("RGB", (108, 192)).save(base / "stories" / f)
            paths.append(str(base / "stories" / f))
        story = db.add_scheduled_post("run_k", "story", datetime(2026, 11, 10, 17, 45, tzinfo=UTC).isoformat(), paths)
        for r_status, part in receipts:
            db.record_publication("t1", "run_k", "upload-post:story", r_status, {"part": part})
        db.update_scheduled_post(story, status, detail=detail)
        shown = line(control.posting_episode(db, "run_k")[0])
        digits = [int(t) for t in (shown or "").replace(":", " ").split() if t.isdigit()]
        out[name] = {"shown": shown, "went_out_or_will_post": went_out,
                     "count_right": bool(digits) and digits[0] == went_out}

print(json.dumps({"cases": out, "cases_total": len(out),
                  "cases_with_a_right_count": sum(v["count_right"] for v in out.values())}, indent=1))
