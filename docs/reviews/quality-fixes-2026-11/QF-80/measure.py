"""QF-80 measurement: the Story sequence is hook and reveal; the poll frame is not made, booked or posted.

    python docs/reviews/quality-fixes-2026-11/QF-80/measure.py <repo root> [--runs DIR] [--metrics FILE] [--db FILE]

<repo root> is the code measured (the parent or this row). Prints JSON; writes nothing in either tree (renders go to
a temporary folder that is removed). No network: sockets are refused and Upload-Post is a local recorder.

- "fixture": the real renderer on a generated grey photo and the three-slide script below (the shape every script
  has: hook, reveal, the poll's question), and a booking made before this row (three frames on disk).
- "runs" (--runs, default <repo root>/output): real run folders, read only. Per run: the Story frames on disk, the
  frames the measured code books and posts (release.story_frames, as main.py uses it), poll-sticker rooms recorded in
  layout.json. Where a run's recorded recipes still find their photos, its frames are drawn again from those recipes
  by the measured code and their SHA-256 printed, so the parent's and this row's hook and reveal can be compared.
- "metrics" (--metrics, a production-sample metrics.json) and "db" (--db, a pipeline database: a temporary copy
  is read, never the file itself): Story publications and the Story reach recorded per frame (audience <platform>_story<part>).
"""

import argparse
import contextlib
import hashlib
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PAYLOAD = {
    "title": "The Eye of the Sahara", "caption": "c", "first_comment": "?", "hashtags": ["#x"],
    "narration_script": "Thirty miles across, and nobody built it.",
    "story_slides": [
        {"kicker": "VISIBLE FROM SPACE", "line1": "A BULLSEYE", "line2": "IN THE SAND", "body": "Thirty miles across."},
        {"kicker": "THE REVEAL", "line1": "NOT A CRATER", "line2": "A DOME", "body": "Erosion carved the rings."},
        {"kicker": "YOUR CALL", "line1": "WHAT DO", "line2": "YOU SEE?", "body": "Lost city or geology?"},
    ],
    "poll": {"question": "Lost city or geology?", "options": ["A lost city", "Geology"]},
}
LEGACY = ["story_01_hook.jpg", "story_02_reveal.jpg", "story_03_question.jpg"]
POLL_FRAME = LEGACY[2]


def _refuse(*_a, **_k):
    raise RuntimeError("measure.py makes no network call")


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(work: Path) -> dict:
    import requests
    from PIL import Image

    import main as pipeline
    from src import audience, release
    from src.db import PipelineDatabase
    from src.multi_format import MultiFormatGenerator
    from src.publisher import MetaPublisher, UploadPostPublisher

    hero = work / "hero.jpg"
    Image.new("RGB", (1080, 1920), (55, 63, 68)).save(hero)
    made = work / "made"
    frames = MultiFormatGenerator().generate_story_sequence(hero, PAYLOAD, made)
    layout = json.loads((made / "stories" / "layout.json").read_text(encoding="utf-8"))
    companion = json.loads((made / "companion_posting.json").read_text(encoding="utf-8"))

    old = work / "run_old"            # a run rendered before this row: three frames on disk
    (old / "stories").mkdir(parents=True)
    files = []
    for name in LEGACY:
        Image.new("RGB", (108, 192), (60, 60, 60)).save(old / "stories" / name)
        files.append(str(old / "stories" / name))
    bank = work / "bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "t1", "title": "T", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=work / "measure.db", topic_bank_file=bank)
    db.create_run("run_old", "t1")
    coord = pipeline.PipelineCoordinator.__new__(pipeline.PipelineCoordinator)
    coord.db = db
    coord._book_followers("run_old", old, datetime(2026, 11, 10, 17, 0, tzinfo=UTC))
    booked = [p for p in db.list_scheduled_posts() if p["kind"] == "story"]

    calls = []

    def recorder(url, headers, data, files, timeout):
        calls.append(headers["Idempotency-Key"])
        return SimpleNamespace(status_code=200, text="",
                               json=lambda: {"success": True, "results": {"instagram": {"success": True}}})
    with patch.object(requests, "post", recorder):
        up = UploadPostPublisher(api_key="k" * 20, user="u", base_url="https://upload-post.invalid/api")
        MetaPublisher(db=db, publishers=[up]).publish_photos("run_old", "t1", "story", files)
        sent_old = list(calls)
        calls.clear()
        MetaPublisher(db=db, publishers=[up]).publish_photos("run_old", "t1", "story", [files[1]],
                                                            platforms=["instagram"], retry_tag="retry1-instagram")
    retry = [p for p in db.list_publications("run_old") if p["channel"] == "upload-post:story"][-1]
    receipt = json.loads(retry["response_payload_json"])
    reach_label = [t[0] for t in audience.targets({"channel": retry["channel"], "response": receipt,
                                                    "run_id": "run_old"}, {})]

    story = db.add_scheduled_post("run_old", "story", "2026-11-10T17:45:00+00:00", files)
    db.update_scheduled_post(story, "unknown", detail={"part": 3, "posted_parts": 2, "failed": {"instagram": "x"}})
    sent = []
    coord.publisher = SimpleNamespace(publish_photos=lambda *a, **k: sent.append(a) or {"status": "published"})
    coord.retry_post(story)
    return {
        "frames_made": [p.name for p in frames],
        "poll_sticker_rooms_made": sum(bool(e.get("poll_sticker_room")) for e in layout),
        "companion_has_poll_sticker": bool(companion.get("story_poll_sticker")),
        "old_run_frames_counted": len(release.story_frames(old)),
        "old_run_frames_booked": sum(len(b["files"]) for b in booked),
        "old_booking_requests_sent": sent_old,
        "reveal_retry_request": calls,
        "reveal_retry_reach_recorded_as": reach_label,
        "poll_frame_retry_sent_to_publisher": len(sent),
    }


def runs(folder: Path, work: Path) -> dict:
    from src import release
    from src.multi_format import MultiFormatGenerator

    rows, redrawn = [], []
    for run in sorted(p for p in folder.glob("run_*") if (p / "stories").is_dir()):
        stories = run / "stories"
        on_disk = sorted(p.name for p in stories.glob("story_*.jpg"))
        if not on_disk:
            continue
        try:
            layout = json.loads((stories / "layout.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            layout = []
        rows.append({"poll_frame_on_disk": POLL_FRAME in on_disk, "frames_on_disk": len(on_disk),
                     "frames_booked": len(release.story_frames(run)),
                     "poll_frame_booked": POLL_FRAME in [p.name for p in release.story_frames(run)],
                     "layout": bool(layout), "poll_rooms": sum(bool(e.get("poll_sticker_room")) for e in layout)})
        try:
            record = json.loads((stories / "still_layout.json").read_text(encoding="utf-8"))
            payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        photos = [Path(record.get(name, {}).get("recipe", {}).get("photo", "")) for name in LEGACY]
        if not all(p.is_file() for p in photos):
            continue
        out = work / run.name
        # The recorded photo of each frame, in order (the vision match is not repeated: it calls a model).
        with patch.object(MultiFormatGenerator, "_match_photos", lambda self, cards, stills: stills[:len(cards)]):
            made = MultiFormatGenerator().generate_story_sequence(photos[0], payload, out, scene_images=photos)
        redrawn.append({"run": run.name, "frames": {p.name: _sha(p) for p in made}})
    total = len(rows)
    return {
        "story_runs": total,
        "runs_with_poll_frame_on_disk": sum(r["poll_frame_on_disk"] for r in rows),
        "frames_on_disk": sum(r["frames_on_disk"] for r in rows),
        "frames_booked": sum(r["frames_booked"] for r in rows),
        "runs_booking_the_poll_frame": sum(r["poll_frame_booked"] for r in rows),
        "runs_with_layout_json": sum(r["layout"] for r in rows),
        "poll_rooms_in_layout_json": sum(r["poll_rooms"] for r in rows),
        "redrawn_from_recipes": redrawn,
    }


def story_rows(publications, metrics) -> dict:
    parts = Counter()
    for p in publications:
        if p["channel"] == "upload-post:story":
            parts[(p["status"], p.get("part"))] += 1
    reach = Counter()
    rows = Counter()
    for m in metrics:
        label = m["platform"]
        if "_story" in label:
            rows[label] += 1
            if isinstance((m.get("metrics") or {}).get("reach"), (int, float)):
                reach[label] += 1
    return {"story_publications": {f"{s} part {n}": c for (s, n), c in sorted(parts.items(), key=str)},
            "story_metric_rows": dict(sorted(rows.items())), "story_rows_with_reach": dict(sorted(reach.items()))}


def from_db(path: Path) -> dict:
    # A copy (with its write-ahead log) in a temporary folder: even a read-only open of a WAL database makes its
    # -shm/-wal files beside it, so the database itself is never opened.
    with tempfile.TemporaryDirectory(prefix="qf80-db-") as tmp:
        for suffix in ("", "-wal"):
            if Path(str(path) + suffix).is_file():
                shutil.copyfile(str(path) + suffix, Path(tmp) / ("copy.db" + suffix))
        return _read_db(Path(tmp) / "copy.db")


def _read_db(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    with contextlib.closing(con):
        pubs = [{"channel": r["channel"], "status": r["status"],
                 "part": json.loads(r["response_payload_json"] or "{}").get("part")}
                for r in con.execute("SELECT channel, status, response_payload_json FROM publications")]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        metrics = ([{"platform": r["platform"], "metrics": json.loads(r["metrics_json"] or "{}")}
                    for r in con.execute("SELECT platform, metrics_json FROM audience_metrics")]
                   if "audience_metrics" in tables else [])
    return story_rows(pubs, metrics)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--runs", type=Path)
    ap.add_argument("--metrics", type=Path)
    ap.add_argument("--db", type=Path)
    args = ap.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    socket.socket.connect = _refuse
    socket.create_connection = _refuse
    from PIL import features
    if not features.check("raqm"):
        raise SystemExit("Pillow raqm is required (FriBiDi on PATH)")
    out = {}
    with tempfile.TemporaryDirectory(prefix="qf80-") as tmp:
        work = Path(tmp)
        (work / "fixture").mkdir()
        out["fixture"] = fixture(work / "fixture")
        folder = (args.runs or root / "output").resolve()
        if folder.is_dir():
            (work / "runs").mkdir()
            out["runs"] = runs(folder, work / "runs")
    if args.metrics:
        data = json.loads(args.metrics.read_text(encoding="utf-8"))
        out["metrics"] = story_rows([dict(p, part=None) for p in data.get("publications", [])],
                                    data.get("audience_metrics", []))
    if args.db:
        out["db"] = from_db(args.db)
    f = out["fixture"]
    out["acceptance"] = {
        "two_frames_made": f["frames_made"] == LEGACY[:2],
        "no_poll_room_made": f["poll_sticker_rooms_made"] == 0 and not f["companion_has_poll_sticker"],
        "poll_frame_never_booked_or_sent": (f["old_run_frames_booked"] == 2 and f["poll_frame_retry_sent_to_publisher"] == 0
                                            and f["old_booking_requests_sent"] == ["run_old-story-1", "run_old-story-2"]),
        "reveal_reach_recorded_as_frame_2": f["reveal_retry_reach_recorded_as"] == ["instagram_story2"],
        "real_runs_booking_the_poll_frame": (out.get("runs") or {}).get("runs_booking_the_poll_frame"),
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
