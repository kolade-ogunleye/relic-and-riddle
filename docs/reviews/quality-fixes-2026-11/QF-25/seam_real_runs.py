"""The hook's handover to the opening picture, on the real Reels whose opening picture is the hook's own photo.

Usage: python seam_real_runs.py <repo-root> <output-dir>

For every run folder whose parallax scene_01 clip was made from the hero named in palette.json (the case where the
hook's ground and the first picture are the same photograph), it builds the hook on that photo, composes a short
master (the run's real scene_01 and scene_02 clips, its real narration and words, the first 4 s), and measures the
frame-to-frame mean absolute RGB difference above the caption band (rows 0-1090 of 1920; the 9:16 captions sit
at 1130-1370). The hook's claim is the committed fixture statement "About 40 km across" (no real sheet has a
hook-length statement); everything else is the run's own media. Prints counts and numbers only, no run or topic
names. Offline: no model, no key of the operator's (a test-only record key signs the fixture statement), no
network.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _stem(name: str) -> str | None:
    m = re.search(r"(\d+_commons)", name)
    return m.group(1) if m else None


def _frames(path: Path, count: int):
    import numpy as np
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", str(count),
                                   "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080, 3)


def measure(root: Path, output: Path) -> dict:
    sys.path[:0] = [str(root), str(root / "tests")]
    os.chdir(root)
    import numpy as np

    from src import config
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"
    from test_design_cards_2026_11_03 import SHEET, statement

    from src import card_composition, encoding
    from src import video_composer as vc
    from src.design_cards import HOOK_HOLD
    from src.scene_planner import strong_shot

    def enc():
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    encoding.get_video_encoder = enc
    vc._get_video_encoder = enc
    work = Path(tempfile.mkdtemp(prefix="qf25_seam_"))
    sheet = copy.deepcopy(SHEET)
    statement("About 40 km across", "f_len", sheet)
    facts = work / "facts.json"
    facts.write_text(json.dumps(sheet), encoding="utf-8")
    result = {"reels_with_parallax_opening": 0, "opening_is_hook_photo": 0, "hero_strong_shot": 0, "cases": []}
    for run in sorted(output.glob("run_*")):
        palette, lineage = run / "palette.json", run / "parallax" / "scene_01.mp4.lineage.json"
        if not (palette.is_file() and lineage.is_file() and (run / "words.json").is_file()):
            continue
        result["reels_with_parallax_opening"] += 1
        hero = _stem(json.loads(palette.read_text(encoding="utf-8")).get("source", ""))
        sources = [Path(s["path"]) for s in json.loads(lineage.read_text(encoding="utf-8")).get("sources", [])]
        photo = next((s for s in sources if hero and _stem(s.name) == hero and s.is_file()), None)
        if photo is None:
            continue
        result["opening_is_hook_photo"] += 1
        if not strong_shot(photo):
            continue
        result["hero_strong_shot"] += 1
        case = work / f"case{result['hero_strong_shot']:02d}"
        case.mkdir()
        words = [w for w in json.loads((run / "words.json").read_text(encoding="utf-8"))
                 if float(w["start"]) + float(w["dur"]) <= 3.9]
        card_composition.make_all({"design_card_sheet": str(facts)}, case / "cards", photo=photo, words=words)
        hook = card_composition.opening_card(case / "cards")
        if hook is None:
            result["cases"].append({"hook": False})
            continue
        voice = case / "voice.wav"
        subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", str(run / "voiceover.mp3"), "-t", "4.0",
                               "-ar", "48000", str(voice)])
        clips = [run / "parallax" / "scene_01.mp4"] + sorted(p for p in (run / "parallax").glob("scene_*.mp4")
                                                             if p.name != "scene_01.mp4")[:1]
        durations = [3.0, 1.0][:len(clips)]
        durations[-1] = 4.0 - sum(durations[:-1])
        beats = [{"start": 0.0, "end": 4.0, "text": " ".join(w["word"] for w in words)}]
        schedule = card_composition.opening_schedule(hook, words, durations, beats, voice, voice, 4.0,
                                                     case / "opening")
        master = case / "master.mp4"
        vc.VideoComposer().compose_video(clips, schedule["voice"], schedule["voice"], schedule["words"],
                                         schedule["total"], master, durations=schedule["durations"],
                                         opening_card=hook, caption_offset_s=schedule["offset"])
        offset = schedule["offset"]
        frames = _frames(master, round((offset + 1.0) * 30)).astype(np.float32)[:, :1090]
        steps = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2, 3))   # steps[i]: frame i -> i + 1
        at = round(offset * 30)                                          # first frame without the hook
        fade = round(HOOK_HOLD * 30)
        result["cases"].append({
            "hook": True,
            "handover_step": round(float(steps[at - 1]), 2),             # last hook-blended frame -> first picture
            "max_step_during_exit": round(float(steps[fade:at].max()), 2),
            "max_step_after": round(float(steps[at:].max()), 2),         # the picture's own motion
            "median_step_after": round(float(np.median(steps[at:])), 2),
            "first_word_s": min(float(w["start"]) for w in schedule["words"]) if schedule["words"] else None,
        })
    hooked = [c for c in result["cases"] if c.get("hook")]
    if hooked:
        result["max_handover_step"] = max(c["handover_step"] for c in hooked)
        result["max_step_during_exit"] = max(c["max_step_during_exit"] for c in hooked)
        result["max_step_after"] = max(c["max_step_after"] for c in hooked)
    return result


if __name__ == "__main__":
    print(json.dumps(measure(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()), indent=2))
