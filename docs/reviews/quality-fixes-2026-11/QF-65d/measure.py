"""QF-65d measurement: an older run's record-less stills, verified against its provenance.json before they are held
off Facebook.

    python docs/reviews/quality-fixes-2026-11/QF-65d/measure.py <repo root> [--runs DIR ...]

<repo root> is the code measured (the parent or this row); it is the only argument the acceptance needs. Prints
JSON; writes nothing in either tree (fixtures go to a temporary folder that is removed). No network: sockets are
refused and every request is recorded locally.

- "fixture_older_runs": an older run built by the measured tree's own lineage and ledger writers (src/lineage,
  src/credits.generate_provenance_ledger): a parallax clip of a credited Commons photo and our own map graphic, its
  manifest and provenance.json, then its intermediate clips deleted (parallax/, motion/) as on the real runs. Per
  case, a Story frame and a carousel with no still record go through MetaPublisher.publish_photos (live, requests
  recorded): sent to Facebook, Instagram flagged, and the reason given for a hold. Cases: "verified" (nothing else),
  "verified_from_another_machine" (the ledger's paths as a production VM records them), and the cases that must stay
  held: an AI motion clip, an AI illustration moved by a parallax clip, an illustrated beat in the picture check, the
  photo replaced since the run (same name, other bytes), no photo left to check, no ledger, an unreadable ledger,
  the manifest edited after the ledger, the ledger recording other bytes for the clip, an incomplete licence record.
- "runs" (--runs, output folders, read only): COUNTS only. Photo posts (Story frame sets, carousels, Facebook's
  carousel copy): checked, with a still without a record, kept off Facebook, and the kept-off posts by reason.
"""

import argparse
import hashlib
import json
import shutil
import socket
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
HELD_CASES = ["ai_motion_clip", "ai_illustration_parallax", "illustrated_beat", "photo_replaced", "photo_gone",
              "no_ledger", "unreadable_ledger", "manifest_edited", "clip_bytes_differ", "incomplete_licence"]
SENT_CASES = ["verified", "verified_from_another_machine"]


def _refuse(*_a, **_k):
    raise RuntimeError("measure.py makes no network call")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _older_run(work: Path, case: str) -> Path:
    from src import config
    from src.credits import generate_provenance_ledger
    from src.lineage import record_derived, record_graphic
    config.ASSETS_DIR = work / "assets"
    pool = work / "assets" / "topic"
    pool.mkdir(parents=True)
    photo = pool / "01_commons.jpg"
    photo.write_bytes(FAKE_JPEG + b"a stone hall")
    credit = {"file": "01_commons.jpg", "title": "File:Stone hall.jpg", "artist": "A. Photographer",
              "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0",
              "source_url": "https://commons.wikimedia.org/wiki/File:Stone_hall.jpg", "attribution_required": True}
    if case == "incomplete_licence":
        credit["artist"] = ""
    (pool / "credits.json").write_text(json.dumps({"images": [credit]}), encoding="utf-8")
    run = (work / "output" / "run_x").resolve()
    (run / "parallax").mkdir(parents=True)
    (run / "graphics").mkdir()
    clip = run / "parallax" / "scene_01.mp4"
    clip.write_bytes(b"parallax clip of the hall")
    record_derived(clip, [photo], "parallax", {"preset": "push"})
    generator = work / "graphics_generator.py"
    generator.write_text("# our own map\n", encoding="utf-8")
    graphic = run / "graphics" / "graphic_map.mp4"
    graphic.write_bytes(b"our own map")
    record_graphic(graphic, generator, {"kind": "map"})
    scenes = [clip, graphic]
    if case == "ai_motion_clip":
        (run / "motion").mkdir()
        motion = run / "motion" / "motion_01.mp4"
        motion.write_bytes(b"ai motion clip")
        record_derived(motion, [photo], "ai_motion", {"provider": "kling", "ai_generated": True})
        scenes.append(motion)
    if case == "ai_illustration_parallax":
        art = run / "beat_images" / "gap_gen_beat04.jpg"
        art.parent.mkdir()
        art.write_bytes(FAKE_JPEG + b"an illustration")
        drawn = run / "parallax" / "scene_04.mp4"
        drawn.write_bytes(b"parallax clip of the illustration")
        record_derived(drawn, [art], "parallax", {"preset": "drift"})
        scenes.append(drawn)
    master = run / "topic_master.mp4"
    master.write_bytes(b"the master video")
    manifest = master.with_suffix(".composition.json")
    manifest.write_text(json.dumps({"master_sha256": _sha(master), "scenes": {str(s): _sha(s) for s in scenes}}),
                        encoding="utf-8")
    generate_provenance_ledger(run, {"id": "topic"}, {}, master, media_items=scenes)
    for folder in ("parallax", "motion", "beat_images"):
        shutil.rmtree(run / folder, ignore_errors=True)
    ledger_file = run / "provenance.json"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    if case == "illustrated_beat":
        (run / "script_metadata.json").write_text(json.dumps({"picture_fit": {"generated": [{"beat": 3}]}}),
                                                  encoding="utf-8")
    elif case == "photo_replaced":
        photo.write_bytes(FAKE_JPEG + b"another photo under the same name")
    elif case == "photo_gone":
        photo.unlink()
    elif case == "no_ledger":
        ledger_file.unlink()
    elif case == "unreadable_ledger":
        ledger_file.write_text("{not json", encoding="utf-8")
    elif case == "manifest_edited":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifest.write_text(json.dumps({**data, "note": "edited after the ledger"}), encoding="utf-8")
    elif case == "clip_bytes_differ":
        next(a for a in ledger["assets"] if a["file"] == "scene_01.mp4")["sha256"] = "0" * 64
        ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
    elif case == "verified_from_another_machine":
        here = str(work.resolve())

        def move(value):
            if isinstance(value, dict):
                return {move(k): move(v) for k, v in value.items()}
            if isinstance(value, list):
                return [move(v) for v in value]
            if isinstance(value, str) and value.startswith(here):
                return "C:\\Elsewhere\\app" + value[len(here):].replace("/", "\\")
            return value
        manifest.write_text(json.dumps(move(json.loads(manifest.read_text(encoding="utf-8")))), encoding="utf-8")
        ledger = move(ledger)
        ledger["composition_manifest"]["sha256"] = _sha(manifest)
        ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
    return run


def _stills(run: Path, kind: str) -> list[str]:
    folder = run / ("stories" if kind == "story" else "carousel")
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in (1, 2):
        p = folder / (f"story_{i:02d}.jpg" if kind == "story" else f"carousel_slide_{i:02d}.jpg")
        p.write_bytes(FAKE_JPEG + p.name.encode())
        out.append(str(p))
    return out


class _Resp:
    status_code = 200
    ok = True
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _publish(work: Path, run: Path, kind: str) -> dict:
    import contextlib

    import requests

    from src import publisher
    from src.db import PipelineDatabase
    bank = work / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [{"id": "topic", "title": "T", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=work / "m.db", topic_bank_file=bank)
    db.create_run("run_x", "topic")
    sent = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append(list(data))
        names = [v for k, v in data if k == "platform[]"]
        return _Resp({"success": True, "results": {p: {"success": True} for p in names}})
    try:
        with patch.object(requests, "post", fake_post):
            up = publisher.UploadPostPublisher(api_key="k" * 20, user="u")
            res = publisher.MetaPublisher(db=db, publishers=[up]).publish_photos(
                "run_x", "topic", kind, _stills(run, kind), caption="c", platforms=["instagram", "facebook"])
    finally:
        with contextlib.suppress(Exception):
            db._conn.close()
    ig = [x for x in sent if ("platform[]", "instagram") in x]
    return {"facebook": any(("platform[]", "facebook") in x for x in sent),
            "instagram_flagged": bool(ig) and all(("is_ai_generated", "true") in x for x in ig),
            "held_reason": (res.get("held_platforms") or {}).get("facebook", "")}


def older_runs() -> dict:
    out = {}
    for case in SENT_CASES + HELD_CASES:
        for kind in ("story", "carousel"):
            with tempfile.TemporaryDirectory() as d:
                work = Path(d)
                out[f"{kind}_{case}"] = _publish(work, _older_run(work, case), kind)
    return out


def _reason(text: str) -> str:
    if "no video record" in text:
        return "no_video_record" + (":provenance_lists_ai_media" if "provenance.json lists" in text else "")
    if "showing AI media" in text:
        return "ai_media:" + ("ai_motion_clip" if "AI motion" in text else "ai_illustration" if "illustration" in text
                              else "other")
    if "checksum mismatch" in text:
        return "provenance_checksum_mismatch"
    if "cannot verify it" in text:
        return "provenance_cannot_verify:" + text.rsplit("cannot verify it: ", 1)[-1][:40]
    return "video_not_wholly_traceable"


def runs(folder: Path) -> dict:
    """Counts only: every photo post of every run in `folder`, as the measured code would send it to Facebook."""
    from src import ai_label
    from src.publishable import photo_posts
    c = Counter({"photo_posts_checked": 0, "with_a_still_without_record": 0, "kept_off_facebook": 0,
                 "kept_off_facebook_with_a_still_without_record": 0,
                 "with_a_still_without_record_sent_to_facebook": 0})
    reasons: Counter = Counter()
    for run in sorted(p for p in Path(folder).glob("run_*") if p.is_dir()):
        stills = photo_posts(run)
        for group in ([p for p in stills if p.parent.name == f] for f in ("stories", "carousel", "carousel_fb")):
            if not group:
                continue
            label = ai_label.assess_photos(group)
            held = bool(label.get("facebook_hold", label["ai_media"]))
            bare = any(ai_label.still_sources(p) is None for p in group)
            c["photo_posts_checked"] += 1
            c["with_a_still_without_record"] += bare
            c["kept_off_facebook"] += held
            c["kept_off_facebook_with_a_still_without_record"] += held and bare
            c["with_a_still_without_record_sent_to_facebook"] += bare and not held
            if held:
                reasons[_reason((label.get("facebook_reasons") or [""])[0])] += 1
    return {**dict(c), "kept_off_by_reason": dict(sorted(reasons.items()))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--runs", action="append", default=[])
    args = ap.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    socket.socket.connect = _refuse
    import logging
    logging.disable(logging.CRITICAL)
    result = {"fixture_older_runs": older_runs()}
    cases = result["fixture_older_runs"]
    result["acceptance_met"] = (
        all(v["facebook"] and v["instagram_flagged"] for k, v in cases.items()
            if k.split("_", 1)[1] in SENT_CASES)
        and all(not v["facebook"] and v["instagram_flagged"] and v["held_reason"] for k, v in cases.items()
                if k.split("_", 1)[1] in HELD_CASES)
        and all("AI motion clip" in cases[f"{k}_ai_motion_clip"]["held_reason"] for k in ("story", "carousel")))
    result["runs"] = {f"folder_{i + 1}": runs(Path(f)) for i, f in enumerate(args.runs)}
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
