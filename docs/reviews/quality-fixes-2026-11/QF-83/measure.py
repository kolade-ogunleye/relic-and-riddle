"""QF-83 acceptance, offline: python measure.py <repo root>. Prints JSON; writes only a temporary database.

1. The source defaults (a fresh interpreter, no .env, the settings unset): YOUTUBE_SHORTS, LOOP_FOLLOW_OVERLAY.
2. The real coordinator's dry run (main.PipelineCoordinator.publish_run, dry_run=True) with that Shorts default:
   which channels it records. The release gate is the only boundary replaced (no rendered master exists here).
3. A successful Short upload through MetaPublisher.publish_short against a mocked uploader that also offers
   set_youtube_thumbnail: how many thumbnail requests follow, and whether the receipt carries a thumbnail field.
4. The loop bridge with the FOLLOW, where the code supports it, on the committed QF-03 photograph (Wieliczka passage)
   and on plain white and black frames: frames, contrast over its full-opacity frames, box, seam problems against the
   purely visual bridge, and whether the first and last frames equal the visual bridge's.
Network connections are refused for the whole run. It refuses a root that holds a .env (the defaults are read with
RELIC_APP_ROOT at an empty folder in any case).
"""

import inspect
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def source_default(root: Path, name: str, app_root: Path):
    env = {k: v for k, v in os.environ.items() if k not in ("YOUTUBE_SHORTS", "LOOP_FOLLOW_OVERLAY")}
    env["RELIC_APP_ROOT"] = str(app_root)
    out = subprocess.run([sys.executable, "-c", f"from src import config; print(repr(getattr(config, '{name}', None)))"],
                         cwd=root, env=env, capture_output=True, text=True, timeout=300, check=True)
    return eval(out.stdout.strip().splitlines()[-1])


def main():
    root = Path(sys.argv[1]).resolve()
    if (root / ".env").exists():
        raise SystemExit("run it on a checkout without a .env (a worktree): it must not read one")
    sys.path.insert(0, str(root))

    def refuse(*a, **k):
        raise RuntimeError("offline measurement: no network")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    import numpy as np
    from PIL import Image, ImageOps

    with tempfile.TemporaryDirectory(prefix="qf83-") as tmp:
        work = Path(tmp)
        for name in ("YOUTUBE_SHORTS", "LOOP_FOLLOW_OVERLAY"):
            os.environ.pop(name, None)
        defaults = {n: source_default(root, n, work) for n in ("YOUTUBE_SHORTS", "LOOP_FOLLOW_OVERLAY")}
        import main as pipeline
        from src import config, end_card, publisher, release
        from src.db import PipelineDatabase
        bank = work / "topics.json"
        bank.write_text(json.dumps({"topics": [{"id": "fixture", "title": "Fixture", "hook": "Fixture",
                                                "location": {"name": "Fixture", "country": "Fixture"}}]}))
        db = PipelineDatabase(work / "pipeline.sqlite", topic_bank_file=bank)
        for name in ("master.mp4", "cover.jpg"):
            (work / name).write_bytes(b"offline fixture")
        db.create_run("dry", "fixture")
        db.update_run("dry", "pending_approval", master_video_path=work / "master.mp4",
                      thumbnail_cover_path=work / "cover.jpg", thumbnail_grid_path=work / "cover.jpg",
                      script_payload={"title": "Fixture", "caption": "Checked fixture", "topic_id": "fixture"})
        release.require_release = lambda run, dry_run=False: None
        config.TELEGRAM_CARDS_V2 = False
        config.YOUTUBE_SHORTS = bool(defaults["YOUTUBE_SHORTS"])
        coord = pipeline.PipelineCoordinator.__new__(pipeline.PipelineCoordinator)
        coord.db, coord.publisher = db, publisher.MetaPublisher(db=db, publishers=[])
        coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
        coord.publish_run("dry", dry_run=True)
        dry = sorted(f"{p['channel']}:{p['status']}" for p in db.list_publications("dry"))

        calls = []
        uploader = SimpleNamespace(
            publish_short=lambda envelope: {"status": "published", "post_urls": {"youtube": "https://youtube.com/shorts/fixture1234"}},
            set_youtube_thumbnail=lambda video_id, image: calls.append(video_id) or {"set": True, "error": None})
        db.create_run("upload", "fixture")
        short = publisher.MetaPublisher(db=db, publishers=[uploader]).publish_short(
            {"run_id": "upload", "topic_id": "fixture", "media": {"thumbnail_path": str(work / "cover.jpg")}, "metadata": {}})
        receipt = json.loads(db.list_publications("upload")[-1]["response_payload_json"])

    supported = "follow" in inspect.signature(end_card.loop_bridge).parameters
    bridges = {}
    if supported:
        fixture = root / "docs/reviews/quality-fixes-2026-11/QF-03/fixtures/passage.jpg"
        with Image.open(fixture) as im:
            photo = ImageOps.fit(im.convert("RGB"), (1080, 1920))
        cases = {"wieliczka_passage": (photo, photo), "white": (Image.new("RGB", (1080, 1920), "white"),) * 2,
                 "black": (Image.new("RGB", (1080, 1920), "black"),) * 2}
        for name, (last, first) in cases.items():
            plain, follow = {}, {}
            kw = {"line": False, "close_push": 0.0, "open_push": 0.0}
            a = list(end_card.loop_bridge(last, first, "", plain, **kw))
            b = list(end_card.loop_bridge(last, first, "", follow, follow=True, **kw))
            bridges[name] = {"frames": len(b), "text": follow["text"], "contrast": follow["contrast"],
                             "frames_full_opacity": follow["follow_frames"], "halo": follow["follow_halo"],
                             "box": follow["follow_box"], "skipped": follow["follow_skipped"],
                             "seam_problems": end_card.seam_problems(follow),
                             "seam_problems_visual": end_card.seam_problems(plain),
                             "ends_equal_visual": all(np.array_equal(np.asarray(a[i]), np.asarray(b[i])) for i in (0, -1))}
    out = {"defaults": defaults, "dry_run_receipts": dry,
           "upload": {"thumbnail_requests": len(calls), "result_has_thumbnail": "thumbnail" in short,
                      "receipt_has_thumbnail": "thumbnail" in receipt, "status": short.get("status")},
           "follow_supported": supported, "bridges": bridges, "network_calls": 0}
    out["acceptance"] = bool(defaults["YOUTUBE_SHORTS"] and "youtube-shorts:simulated" in dry and not calls
                             and not out["upload"]["receipt_has_thumbnail"] and defaults["LOOP_FOLLOW_OVERLAY"]
                             and supported and all(b["text"] == "FOLLOW" and b["contrast"] >= end_card.LOOP_FOLLOW_MIN
                                                   and b["ends_equal_visual"] and b["frames"] == 30
                                                   and b["seam_problems"] == b["seam_problems_visual"]
                                                   for b in bridges.values()))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
