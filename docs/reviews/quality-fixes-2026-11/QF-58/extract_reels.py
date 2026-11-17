"""Derives real_reels.json from real Reel runs: python extract_reels.py <repo root> <env name> <env root>...

Reads <env root>/output/run_*/ (the run's composition, provenance ledger, words.json and timings.json; never the
database) and prints JSON to stdout: for each vertical master, its scenes in order (kind, and for a photo the
pool file it shows and a per-run picture number), the narration beats segment_beats makes from words.json and the
voiceover's length (start, end, and when the words in each beat start and end), and the run's stage seconds up to
the scene plan. Only counts, file names of pool photos and timings: no media, paths or text. Writes nothing.
Run with pairs of <env name> <env root>. real_reels.json is `dev <development checkout>` only. For the production
sample (decisions/PROD-SAMPLE-counts-only.md), run `production_sample <copy of the sample>` with stdout redirected
to a file OUTSIDE the repository and pass that file to measure.py as <reels file>; it is never committed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from src.scene_planner import segment_beats

NOT_ORIGINAL = {"privacy", "motion", "source_versions", "geo", "relief", "upscaled", "text_crop"}
BEFORE_PLAN = ("script_and_fact_check", "voiceover", "music_and_bed", "image_prep_wait", "stills_graphics_sourcing")


def parts(path: str) -> list[str]:
    return re.split(r"[\\/]", path)


def voiceover_seconds(run: Path, words: list[dict]) -> float:
    mp3 = run / "voiceover.mp3"
    if mp3.is_file():
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(mp3)], capture_output=True, text=True, check=False)
        try:
            return float(out.stdout.strip())
        except ValueError:
            pass
    return float(words[-1]["start"]) + float(words[-1]["dur"])


def reel(run: Path) -> dict | None:
    compositions = sorted(run.glob("*.composition.json"))
    if not compositions or not (run / "words.json").is_file() or not (run / "provenance.json").is_file():
        return None
    composition = json.loads(compositions[0].read_text(encoding="utf-8"))
    if not composition.get("height", 0) > composition.get("width", 0):
        return None
    ledger = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    assets = {a.get("file"): a for a in ledger.get("assets", [])}
    words = json.loads((run / "words.json").read_text(encoding="utf-8"))
    total = voiceover_seconds(run, words)
    numbers: dict[str, int] = {}
    scenes = []
    for scene in composition["scenes"]:
        folder, name = parts(scene)[-2:]
        sources = [parts(s) for s in assets.get(name, {}).get("source_paths", [])]
        originals = [s for s in sources if s[-2] not in NOT_ORIGINAL]
        if folder == "graphics":
            scenes.append({"kind": "graphic"})
        elif any("gap_gen" in s[-1] for s in sources) or "gap_gen" in name:
            scenes.append({"kind": "illustration"})
        elif folder in ("parallax", "motion") and originals:
            source = "/".join(originals[-1][-2:])
            number = numbers.setdefault(source, len(numbers) + 1)
            pool_file = originals[-1][-1] if originals[-1][-2] == ledger.get("topic_id") else None
            scenes.append({"kind": "photo", "picture": number, "pool_file": pool_file,
                           "beat_image": originals[-1][-2] == "beat_images"})
        else:
            scenes.append({"kind": "film"})
    beats = []
    for beat in segment_beats(words, total):
        inside = [w for w in words if beat["start"] - 1e-6 <= w["start"] < beat["end"] - 1e-6]
        beats.append({"start": round(beat["start"], 3), "end": round(beat["end"], 3),
                      "speech_start": round(inside[0]["start"], 3) if inside else None,
                      "speech_end": round(max(w["start"] + w["dur"] for w in inside), 3) if inside else None})
    timings = json.loads((run / "timings.json").read_text(encoding="utf-8")) if (run / "timings.json").is_file() else []
    seconds = {}
    for row in timings:
        if row.get("stage") in BEFORE_PLAN:
            seconds[row["stage"]] = seconds.get(row["stage"], 0.0) + float(row.get("seconds") or 0)
    return {"run": run.name, "topic": ledger.get("topic_id"), "scenes": scenes, "beats": beats,
            "words": len(words), "voiceover_seconds": round(total, 3),
            "stage_seconds_before_plan": {k: round(v, 1) for k, v in seconds.items()}}


def main() -> None:
    args = sys.argv[2:]
    out = {"source": "extract_reels.py", "runs": []}
    for env, root in zip(args[::2], args[1::2]):
        for run in sorted((Path(root) / "output").glob("run_*")):
            record = reel(run)
            if record:
                out["runs"].append({"env": env, **record})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
