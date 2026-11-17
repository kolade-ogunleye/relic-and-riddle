"""One-off: copies the small run facts measure.py needs from the real runs into inputs.json (QF-05, 2026-11-05).

    python build_inputs.py <repo root with output/>

Read-only over output/; imports nothing from src. The words come from the committed fixture copies
(docs/reviews/freeze-panel-2026-11-04/fixtures/runs/<run>/words.json); this adds, per run, what is not committed
there: the voiceover's length (ffprobe), the run's own sound plan (sound_plan.json), its delivery profile, the
chapters a deep dive recorded (its act cues), the scene count of its master's composition record and whether the
picture plan held any lines together (picture_hold.merged: none means the plan cut on every beat boundary).
"""
import json
import subprocess
import sys
from pathlib import Path

RUNS = ["run_amber_room_heist_1792790550", "run_gol_gumbaz_whispering_gallery_1793278143",
        "run_nazca_lines_runway_paradox_1792961797", "run_richat_structure_sahara_eye_1792740961_long",
        "run_richat_structure_sahara_eye_1793474843", "run_tunguska_event_1908_1792930600",
        "run_unfinished_obelisk_aswan_1792985912", "run_voynich_manuscript_1792965308"]


def main(repo: Path) -> None:
    out = {}
    for run in RUNS:
        d = repo / "output" / run
        meta = json.loads((d / "script_metadata.json").read_text(encoding="utf-8"))
        vo = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                             str(d / "voiceover.mp3")], capture_output=True, text=True, check=True).stdout.strip()
        comp = next(d.glob("*.composition.json"), None)
        scenes = len(json.loads(comp.read_text(encoding="utf-8")).get("scenes") or {}) if comp else None
        out[run] = {
            "vo_dur": round(float(vo), 3),
            "delivery_profile": meta.get("delivery_profile") or "reel_9x16",
            "deep_dive": bool(meta.get("chapters")),
            "act_cues": [c["start_sec"] for c in meta.get("chapters") or [] if c["start_sec"] > 0],
            "sound_plan": json.loads((d / "sound_plan.json").read_text(encoding="utf-8")),
            "composition_scenes": scenes,
            "picture_hold_merged": (meta.get("picture_hold") or {}).get("merged"),
        }
    dst = Path(__file__).resolve().parent / "inputs.json"
    dst.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(dst)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
