"""QF-81 acceptance, offline: python measure.py <repo root>. Prints JSON; writes only a temporary folder.

Three weeks of the real calendar, on the code at <repo root> (it runs on the parent and on the QF-81 commit alike):
one episode a day, its Reel posted at 12:00 Eastern, Mon 2 to Sun 22 Nov 2026, and the daemon's carousel pass
(main.PipelineCoordinator._post_carousel_if_due, twice per slot) at each day's carousel slot (Tue-Thu).

1. Making each run: the exact `with ThreadPoolExecutor` block of main.PipelineCoordinator._render_approve_publish,
   compiled from <repo root>/main.py and executed with local doubles for everything but the carousel's own calls
   (the renderer double draws three slide files per copy and counts the call; carousel_lazy.plan, where it exists,
   runs for real).
2. Booking each run's followers with the real main.PipelineCoordinator._book_followers.
3. The real daemon pass at each slot, with the publisher replaced by a recorder and the slide audit
   (QualityAuditor.audit_carousel_slides, the per-slide vision look) by a counter.

Counted: carousel copies drawn (while the run is made / at the slot), slide audits (the run's quality audit audits
the Instagram copy of every run that has one, video_qa.QualityAuditor.certify; at the slot, the counter), carousels
posted, expired, run folders holding carousel slides at the end, and drawn-but-never-posted runs. Network
connections are refused for the whole run; it refuses a root that holds a .env.
"""

import ast
import json
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAYS = 21   # three weeks, one episode a day: the steady state the review measured (seven a week, three slots)


class InlinePool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, fn, *args, **kwargs):
        value = fn(*args, **kwargs)
        return SimpleNamespace(result=lambda: value)


def production_block(root: Path):
    tree = ast.parse((root / "main.py").read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_render_approve_publish")
    block = next(n for n in ast.walk(method) if isinstance(n, ast.With)
                 and "ThreadPoolExecutor" in ast.unparse(n.items[0].context_expr))
    return compile(ast.Module(body=[block], type_ignores=[]), "main._render_approve_publish:stills-block", "exec")


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
    from src import db as db_module
    from src import post_schedule as ps
    from src import posting_view
    from src.db import PipelineDatabase

    clock = {"now": datetime(2026, 11, 2, 8, 0, tzinfo=ET)}

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].astimezone().replace(tzinfo=None)

    script = json.loads((root / "data" / "scripts" / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
    payload = {k: v for k, v in script.items() if not k.startswith("_")}
    payload.update(topic_id="01_topic", human_reviewed=True)
    counts = {"drawn_while_made": 0, "drawn_at_slot": 0, "audits_at_slot": 0}
    with tempfile.TemporaryDirectory(prefix="qf81-measure-") as tmp:
        work = Path(tmp)
        photo = work / "photo.jpg"
        Image.new("RGB", (64, 80), (120, 90, 60)).save(photo)

        def draw(stills, script_payload, output_dir, facebook=False, **kwargs):
            key = "drawn_at_slot" if clock.get("slot") else "drawn_while_made"
            counts[key] += 1
            folder = Path(output_dir) / ("carousel_fb" if facebook else "carousel")
            folder.mkdir(parents=True, exist_ok=True)
            slides = []
            for n in range(1, 4):
                Image.new("RGB", (36, 45), (40 * n, 60, 90)).save(folder / f"carousel_slide_{n:02d}.jpg")
                slides.append(folder / f"carousel_slide_{n:02d}.jpg")
            return slides

        def audit(self, folder, payload=None):
            counts["audits_at_slot"] += 1
            return {"score": 100, "certified": True, "issues": []}

        bank = work / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        posted = []
        with patch.object(db_module, "datetime", Frozen), patch.object(ps, "datetime", Frozen), \
                patch.object(posting_view, "datetime", Frozen), patch.object(pipeline, "datetime", Frozen), \
                patch("src.release.require_release", lambda *a, **k: {"status": "pass"}), \
                patch.object(pipeline.QualityAuditor, "audit_carousel_slides", audit), \
                patch("src.art_direction.record", lambda *a, **k: {}):
            db = PipelineDatabase(db_path=work / "measure.db", topic_bank_file=bank)
            coord = pipeline.PipelineCoordinator(db, schedule_posts=True)
            coord._alert = lambda *a, **k: None
            coord.telegram_bot.stamp_review_outcome = lambda *a, **k: True
            coord.multiformat_gen.generate_carousel_sequence = draw
            coord.multiformat_gen.generate_story_sequence = lambda *a, **k: []
            coord.visual_engine.generate_thumbnails = lambda *a, **k: (photo, photo)
            coord.publisher.publish_photos = (lambda run_id, tid, kind, files, caption="", **k:
                                              posted.append(run_id) or {"status": "published"})
            code = production_block(root)
            runs, audits_while_made, slots = [], 0, 0
            for day in range(DAYS):
                run_id = f"run_{day:02d}"
                run_dir = work / run_id
                run_dir.mkdir()
                reel_at = datetime(2026, 11, 2, 12, 0, tzinfo=ET) + timedelta(days=day)
                clock["now"], clock["slot"] = reel_at - timedelta(hours=4), False
                scope = {"Path": Path, "self": coord, "run_dir": run_dir, "packaging_inputs": {}, "hero_img": photo,
                         "payload": dict(payload), "style": "fixture", "strong_media": [photo], "scene_media": [photo],
                         "topic": {"id": "01_topic"}, "words": [], "beats": [], "query_plan": None,
                         "auto_approve": False, "concurrent": SimpleNamespace(futures=SimpleNamespace(
                             ThreadPoolExecutor=InlinePool)),
                         "cached_artifacts": lambda rd, stage, inputs, make: make(), "graphics": SimpleNamespace(
                             make_all=lambda *a, **k: []),
                         "callouts": SimpleNamespace(make=lambda *a, **k: None),
                         "then_now": SimpleNamespace(make=lambda *a, **k: (None, [])),
                         "beat_images": SimpleNamespace(source=lambda *a, **k: ([], [])),
                         "footage": SimpleNamespace(source=lambda *a, **k: ([], [])),
                         # QF-23's Story hold (a base before it has none: the deck's own result)
                         "hold_layout_error": getattr(pipeline, "hold_layout_error", lambda f, *a: f.result())}
                exec(code, scope)  # noqa: S102 - the checkout's own production block
                # the run's quality audit audits the Instagram copy when it has one (QualityAuditor.certify)
                audits_while_made += bool(list((run_dir / "carousel").glob("carousel_slide_*.jpg")))
                (run_dir / "m.mp4").write_bytes(b"0")
                db.create_run(run_id, "01_topic")
                db.update_run(run_id, "pending_approval", script_payload=dict(payload),
                              master_video_path=run_dir / "m.mp4")
                coord._book_followers(run_id, run_dir, reel_at)
                reel = db.add_scheduled_post(run_id, "reel", reel_at.astimezone(UTC).isoformat(), [])
                db.update_scheduled_post(reel, "published",
                                         detail={"posted_at": reel_at.astimezone(UTC).isoformat()})
                db.update_run(run_id, "published")
                runs.append(run_dir)
                slot = ps.carousel_time(reel_at.date())
                if slot:   # the daemon's pass at today's carousel slot, twice
                    slots += 1
                    clock["now"], clock["slot"] = slot + timedelta(minutes=1), True
                    coord._post_carousel_if_due(clock["now"])
                    coord._post_carousel_if_due(clock["now"])
            statuses = {}
            for p in db.list_scheduled_posts(("waiting", "published", "expired", "failed", "unknown", "cancelled")):
                if p["kind"] == "carousel":
                    statuses[p["status"]] = statuses.get(p["status"], 0) + 1
            with_slides = [d.name for d in runs if list((d / "carousel").glob("carousel_slide_*.jpg"))]
        print(json.dumps({
            "episodes": len(runs), "carousel_slots": slots,
            "copies_drawn_while_made": counts["drawn_while_made"], "copies_drawn_at_slot": counts["drawn_at_slot"],
            "copies_drawn_total": counts["drawn_while_made"] + counts["drawn_at_slot"],
            "slide_audits": audits_while_made + counts["audits_at_slot"],
            "carousels_posted": len(posted), "duplicate_posts": len(posted) - len(set(posted)),
            "carousel_status": dict(sorted(statuses.items())),
            "run_folders_with_carousel_slides": len(with_slides),
            "drawn_but_never_posted": len([r for r in with_slides if r not in posted]),
            "folders_with_slides_are_exactly_the_posted": sorted(with_slides) == sorted(posted),
        }, indent=1))


if __name__ == "__main__":
    main()
