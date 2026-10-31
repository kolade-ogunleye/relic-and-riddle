"""Render and inspect a prepared documentary without publishing or sending messages."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import graphics
from src.audio_engine import AudioEngine
from src.config import DATA_DIR
from src.credits import add_provenance_attributions, generate_provenance_ledger
from src.fact_evidence import current_fact_review, record_fact_review
from src.release import write_release_report
from src.trace_check import check, evidence_check
from src.video_composer import VideoComposer
from src.video_qa import QualityAuditor
from src.visual_engine import VisualEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("sources", type=Path)
    parser.add_argument("--images", nargs="+", required=True,
                        help="Explicit filenames inspected for relevance")
    args = parser.parse_args()
    root = args.package
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    package = json.loads((root / "documentary_package.json").read_text(encoding="utf-8"))
    topic_id = package["topic_id"]
    bank = json.loads((DATA_DIR / "topics/topic_bank.json").read_text(encoding="utf-8"))
    topic = next(t for t in bank.get("topics", []) if t["id"] == topic_id)
    credit_data = json.loads((args.sources / "credits.json").read_text(encoding="utf-8"))
    selected = [c for c in credit_data["images"] if c["file"] in args.images]
    if len(selected) != len(set(args.images)):
        raise ValueError("Some selected images have no recorded source evidence")
    photos = [(args.sources / c["file"]).resolve() for c in selected]
    for credit, photo in zip(selected, photos):
        credit["file"] = str(photo)
    (root / "credits.json").write_text(json.dumps({"reviewed": False, "images": selected}, indent=2), encoding="utf-8")
    payload.update(rolling_stone_defense={"title": "ILLUSTRATIVE STONE DOOR"},
                   ventilation_airflow={"title": "ILLUSTRATIVE AIRFLOW"})
    if not current_fact_review(payload):
        sheet_path = DATA_DIR / "fact_sheets" / f"{topic_id}.json"
        passed, confidence, audits, notes = check(payload, json.loads(sheet_path.read_text(encoding="utf-8")))
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        status, evidence_confidence, evidence_audits, evidence_notes = evidence_check(payload, sheet)
        passed = passed and status == "pass"
        payload["second_check"] = {"status": status, "confidence": evidence_confidence,
                                   "audits": evidence_audits, "summary": evidence_notes}
        payload.update(guardrail_passed=passed, final_fact_audits=audits,
                       verification_confidence=confidence, verification_summary=notes)
        (root / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if not passed:
            raise ValueError("Final diagram/package review failed: " + notes + "; " + evidence_notes)
        record_fact_review(payload, sheet_path)
    print("Package verified; generating graphics", flush=True)
    made = graphics.make_all(payload, topic, root / "graphics", width=1920, height=1080)
    by_name = {p.stem: p for p in made}
    words = json.loads((root / "words.json").read_text(encoding="utf-8"))
    duration = AudioEngine.get_duration(root / "voiceover.mp3")
    cues = {0.0: by_name["graphic_map"]} if "graphic_map" in by_name else {}
    for pattern, name in ((r"rolling|cylinders|doors", "graphic_defense"),
                          (r"ventilation", "graphic_ventilation")):
        word = next((w for w in words if re.search(pattern, w["word"], re.I)), None)
        if word and name in by_name:
            cues[max(0.0, word["start"] - 0.3)] = by_name[name]
    boundaries = [float(t) for t in range(0, int(duration), 8)
                  if t == 0 or all(abs(t - cue) >= 3 for cue in cues)]
    boundaries = sorted(set([*boundaries, *cues, duration]))
    scenes, durations, timeline = [], [], []
    photo_index = 0
    for start, end in zip(boundaries, boundaries[1:]):
        asset = cues.get(start)
        if asset is None:
            asset = photos[photo_index % len(photos)]
            photo_index += 1
        scenes.append(asset)
        durations.append(end - start)
        timeline.append({"scene_index": len(scenes) - 1, "asset": str(asset), "start": start,
                         "end": end, "dur": end - start,
                         "text": " ".join(w["word"] for w in words if start <= w["start"] < end)})
    (root / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    master = root / "documentary_master.mp4"
    print(f"Rendering {len(scenes)} cuts, {duration:.3f} seconds", flush=True)
    composer = VideoComposer()
    composer.compose_deep_dive(scenes, root / "voiceover.mp3", root / "ambient_bed.wav",
                              words, duration, master, durations=durations)
    print("Render complete; auditing", flush=True)
    audit = QualityAuditor().audit_rendered_timeline(master, timeline, words,
        out_dir=root / "audit", caption_path=root / "documentary_master_captions.ass", width=1920, height=1080)
    payload["self_healing"] = audit
    hero = photos[-1]
    visual = VisualEngine()
    cover, grid = visual.generate_thumbnails(hero, payload, root)
    thumbnail = visual.generate_youtube_thumbnail(hero, payload, root / "thumbnail_youtube_16x9.jpg")
    payload["thumbnail_youtube"] = str(thumbnail)
    ledger = generate_provenance_ledger(root, topic, payload, master, root / "voiceover.mp3",
        root / "ambient_bed.wav", scenes, audit)
    add_provenance_attributions(payload, ledger)
    (root / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    run = {"master_video_path": str(master), "script_payload": payload,
           "thumbnail_cover_path": str(cover), "thumbnail_grid_path": str(grid), "content_format": "deep_dive"}
    report = write_release_report(run)
    print(json.dumps({"release": report["status"], "audit": audit["certified"],
                      "failed": [c["name"] for c in report["checks"] if c["status"] != "pass"]}), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
