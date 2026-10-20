"""Render a prepared Reel and its packaging locally; never publish or send messages."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import graphics
from src.audio_engine import AudioEngine
from src.config import DATA_DIR
from src.credits import add_provenance_attributions, generate_provenance_ledger
from src.fact_evidence import current_fact_review
from src.multi_format import MultiFormatGenerator
from src.release import write_release_report
from src.scene_planner import plan_scenes, segment_beats
from src.video_composer import VideoComposer
from src.video_qa import QualityAuditor
from src.visual_engine import VisualEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic_id")
    parser.add_argument("package", type=Path)
    parser.add_argument("--images", nargs="+", required=True)
    args = parser.parse_args()
    root = args.package
    payload = json.loads((root / "script_metadata.json").read_text(encoding="utf-8"))
    if not current_fact_review(payload):
        raise ValueError("Package does not have a current successful fact review")
    topic = next(t for t in json.loads((DATA_DIR / "topics/topic_bank.json").read_text(encoding="utf-8"))["topics"]
                 if t["id"] == args.topic_id)
    source_dir = ROOT / "assets" / args.topic_id
    original = json.loads((source_dir / "credits.json").read_text(encoding="utf-8"))
    selected = [c for c in original["images"] if c["file"] in args.images]
    if len(selected) != len(set(args.images)):
        raise ValueError("Selected images must have source evidence")
    photos = [(source_dir / c["file"]).resolve() for c in selected]
    for credit, photo in zip(selected, photos):
        credit["file"] = str(photo)
    (root / "credits.json").write_text(json.dumps({"reviewed": False, "images": selected}, indent=2), encoding="utf-8")
    made = graphics.make_all(payload, topic, root / "graphics", photo=photos[0])
    words = json.loads((root / "words.json").read_text(encoding="utf-8"))
    duration = AudioEngine.get_duration(root / "voiceover.mp3")
    beats = segment_beats(words, duration)
    scenes, durations, notes = plan_scenes(beats, photos + made)
    print(f"Rendering {len(scenes)} cuts, {duration:.3f} seconds: {notes}", flush=True)
    master = root / "reel_master.mp4"
    composer = VideoComposer()
    composer.compose_video(scenes, root / "voiceover.mp3", root / "ambient_bed.wav", words,
                           duration, master, durations=durations)
    timeline, cursor = [], 0.0
    for index, (scene, length) in enumerate(zip(scenes, durations)):
        timeline.append({"scene_index": index, "asset": str(scene), "start": cursor,
                         "end": cursor + length, "dur": length, "text": beats[index]["text"]})
        cursor += length
    (root / "timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    audit = QualityAuditor().audit_rendered_timeline(master, timeline, words, out_dir=root / "audit",
                                                      caption_path=root / "reel_master_captions.ass")
    payload["self_healing"] = audit
    visual = VisualEngine()
    cover, grid = visual.generate_thumbnails(photos[0], payload, root)
    multiformat = MultiFormatGenerator()
    multiformat.generate_story_sequence(photos[0], payload, root, scene_images=photos)
    multiformat.generate_carousel_sequence(photos, payload, root)
    composer.export_subtitles_srt(words, root / "captions.srt")
    composer.export_subtitles_vtt(words, root / "captions.vtt")
    ledger = generate_provenance_ledger(root, topic, payload, master, root / "voiceover.mp3",
        root / "ambient_bed.wav", scenes, audit)
    add_provenance_attributions(payload, ledger)
    (root / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = write_release_report({"master_video_path": str(master), "script_payload": payload,
                                   "thumbnail_cover_path": str(cover), "thumbnail_grid_path": str(grid)})
    print(json.dumps({"status": report["status"], "failed": [c["name"] for c in report["checks"]
                                                           if c["status"] != "pass"]}), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
