"""QF-84 acceptance: the music's mood, what counts as a use, and the set in rotation.

    python measure.py <repo root>

Runs the code of <repo root> (on the base too). Inputs are committed: <root>/data/music/library.json, and
real_data.json beside this file (real_data.py's track measurements and its development and production-sample counts;
this script only reads them). Writes nothing outside a temporary folder; prints JSON.

- classifier_failure: choose_mood when no model is configured and when the model call fails.
- failed_plan_runs: twelve Reels whose mood could not be decided (base: the classifier failed; this commit: the sound
  plan gave no mood), through music.music_for over the committed library (silent stand-in audio in a temporary
  folder): the picks per mood and the share of the largest mood.
- uses_after_twelve_dry_runs: how many `uses` the twelve picks above wrote into the library.
- twelve_published_reels: a SIMULATION of the acceptance's twelve published Reels, three per mood, through
  music_for and (on this commit) the publication accounting of a temporary database: published uses per rotating
  track. The base has no published accounting; its analogue is the `uses` the twelve picks wrote. The real twelve
  published Reels are an operational hold, measured after they exist (README).
- set: approved tracks in rotation per mood, and the measured rule (music_set.py) against the committed library.
- real_data: the committed development and production-sample counts (counts only for production).
"""

import inspect
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
import music_set
import numpy as np
import soundfile as sf

from src import free_llm, music
from src.db import PipelineDatabase
from src.llm import LLMError

library = json.loads((root / "data" / "music" / "library.json").read_text(encoding="utf-8"))
rotation_file = root / "data" / "music" / "rotation.json"   # QF-84's set (absent on the base: every approved track)
retired = json.loads(rotation_file.read_text(encoding="utf-8"))["retired"] if rotation_file.is_file() else {}
live = [t for t in library if t.get("reviewed") and t["file"] not in retired]
real = json.loads((here / "real_data.json").read_text(encoding="utf-8"))
has_record = "mood_record" in inspect.signature(music.music_for).parameters
out = {"code": "QF-84" if has_record else "base"}


def classifier(ready, fails):
    saved = free_llm.ready, free_llm.call

    def failing(*a, **k):
        raise LLMError("429 quota")
    free_llm.ready = lambda: ready
    if fails:
        free_llm.call = failing
    try:
        return music.choose_mood({"title": "T", "narration_script": "x"})
    finally:
        free_llm.ready, free_llm.call = saved


out["classifier_failure"] = {"no_model": classifier(False, False), "call_fails": classifier(True, True)}


def music_dir(temp):
    for t in library:
        path = temp / t["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), np.zeros((4800, 2), np.float32), 48000)
    (temp / "library.json").write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")
    if rotation_file.is_file():
        (temp / "rotation.json").write_bytes(rotation_file.read_bytes())
    music.MUSIC_DIR, music.LIBRARY = temp, temp / "library.json"


def uses_written(temp):
    now = json.loads((temp / "library.json").read_text(encoding="utf-8"))
    return sum(t.get("uses", 0) for t in now) - sum(t.get("uses", 0) for t in library)


with tempfile.TemporaryDirectory(prefix="qf84-") as temp:
    temp = Path(temp)
    music_dir(temp / "a")
    picks = Counter()
    for i in range(12):
        payload = {"topic_id": f"topic{i}", "title": f"Topic {i}"}
        if has_record:
            from src import sound_plan
            got = music.music_for(payload, 45, temp, {"tier": "free"},
                                  mood_record=sound_plan.no_mood("the sound plan failed (429 quota)"))
        else:
            saved = free_llm.ready
            free_llm.ready = lambda: False
            try:
                got = music.music_for(payload, 45, temp, {"tier": "free"})
            finally:
                free_llm.ready = saved
        picks[(got.get("tracks") or [{"mood": "held (drone)"}])[0]["mood"] if got["source"] == "library"
              else "held (drone)"] += 1
    held = picks.pop("held (drone)", 0)
    out["failed_plan_runs"] = {"library_picks_by_mood": dict(picks), "held_with_reason": held,
                               "largest_library_mood_share": round(max(picks.values()) / sum(picks.values()), 3)
                               if picks else None}
    out["uses_after_twelve_dry_runs"] = uses_written(temp / "a")

    music_dir(temp / "b")
    db = PipelineDatabase(db_path=temp / "b.db", topic_bank_file=temp / "none.json") if has_record else None
    chosen, moods = Counter(), Counter()
    for i, mood in enumerate(list(music.MOODS) * 3):
        payload = {"topic_id": f"topic{i}", "title": f"Topic {i}"}
        if has_record:
            got = music.music_for(payload, 45, temp, {"tier": "free"}, mood_record={"mood": mood, "reason": ""},
                                  published=db.music_published_uses())
            with db._conn() as conn:
                conn.execute("INSERT INTO topics (id, title) VALUES (?, ?)", (f"topic{i}", f"Topic {i}"))
            db.create_run(f"r{i}", f"topic{i}")
            db.record_music_selection(f"r{i}", music.music_selection_rows(got))
            db.record_publication(f"topic{i}", f"r{i}", "upload-post", "published", {})
        else:
            original = music.choose_mood
            music.choose_mood = lambda p, m=mood: m
            try:
                got = music.music_for(payload, 45, temp, {"tier": "free"})
            finally:
                music.choose_mood = original
        moods[got["mood"]] += 1
        chosen[got["file"]] += 1
    rotating = [t["file"] for t in live]
    if has_record:
        counted = db.music_published_uses()
        basis = "published uses (publication receipts)"
    else:
        now = {t["file"]: t.get("uses", 0) for t in json.loads((temp / "b" / "library.json").read_text(encoding="utf-8"))}
        counted = {t["file"]: now[t["file"]] - t.get("uses", 0) for t in library if now[t["file"]] != t.get("uses", 0)}
        basis = "uses written by the picks (no published accounting on the base)"
    out["twelve_published_reels"] = {
        "simulation": "12 Reels, 3 per mood, each published once on upload-post", "counted_as": basis,
        "max_mood_share": round(max(moods.values()) / 12, 3),
        "tracks_in_rotation": len(rotating),
        "rotating_tracks_with_at_least_one_use": sum(1 for f in rotating if counted.get(f, 0) >= 1),
        "every_rotating_track_used": all(counted.get(f, 0) >= 1 for f in rotating),
        "distinct_tracks_chosen": len(chosen)}

rule = music_set.verdicts(real["tracks"])
out["set"] = {
    "approved_in_rotation_by_mood": dict(Counter(t["mood"] for t in live)),
    "creators_in_rotation": len({t.get("creator") for t in live}),
    "retired_with_reason": sum(1 for t in library if retired.get(t["file"])),
    "rule_keeps_by_mood": dict(Counter(t["mood"] for t in real["tracks"] if t["file"] in rule and rule[t["file"]] is None)),
    "rotation_matches_rule": all(bool(retired.get(t["file"])) == bool(rule[t["file"]]) for t in library if t["file"] in rule)}
out["real_data"] = {"library": real["library"],
                    "dev": {k: real["dev"][k] for k in ("runs_with_selection", "selection_moods",
                                                        "max_selection_mood_share", "distinct_tracks_selected",
                                                        "sound_plans", "mood_classifier_calls",
                                                        "selection_moods_where_the_classifier_answered",
                                                        "reel_published_runs")},
                    "prod": {k: real["prod"][k] for k in ("runs_with_selection", "selection_moods",
                                                          "max_selection_mood_share", "sound_plans",
                                                          "reel_published_runs", "published_moods",
                                                          "max_published_mood_share", "reviewed_tracks",
                                                          "reviewed_tracks_with_a_published_use", "backfill")
                             if k in real["prod"]}}
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
print(json.dumps(out, indent=1, ensure_ascii=False))
