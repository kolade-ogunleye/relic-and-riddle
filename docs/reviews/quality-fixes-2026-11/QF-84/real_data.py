"""QF-84 real-data facts: the music library's tracks measured, and the music the real runs chose and published.

    python real_data.py <repo root> [--dev-db <COPY of the development database>] [--dev <output dir>]
                        [--music <music dir>] [--prod <COPY of the production sample (metrics.json, output/)>]

Reads only. The database is opened read-only through sqlite3 (a copy; never PipelineDatabase, which migrates).
- tracks: every library track whose audio is in <music dir> (default <root>/data/music; the audio is not in the
  repository, its library.json is), decoded with FFmpeg: duration, EBU R128 integrated loudness, loudness range
  (LRA), true peak, the share of the first 60 s's power below 120 Hz (what a phone speaker does not reproduce) and
  the seconds of the first 60 s more than 30 dB below their loudest second.
- dev: the development runs (music_selection.json, sound_plan.json, llm_calls.jsonl) and the database's publications.
- prod: counts only (decisions PROD-SAMPLE-counts-only): no run, file, track or per-item record.
Prints JSON (the committed real_data.json is this output); writes nothing.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--dev-db", type=Path)
ap.add_argument("--dev", type=Path)
ap.add_argument("--music", type=Path)
ap.add_argument("--prod", type=Path)
args = ap.parse_args()
root = args.root.resolve()
music_dir = (args.music or root / "data" / "music").resolve()
dev_dir = (args.dev or root / "output").resolve()
library = json.loads((root / "data" / "music" / "library.json").read_text(encoding="utf-8"))
SR = 48000
COMPANION = ("dry-run", "coordinator")   # with any "<channel>:<kind>": not a Reel's own receipt


def reel_receipt(channel: str, status: str) -> bool:
    return status == "published" and ":" not in channel and channel not in COMPANION


def measure_track(path: Path) -> dict:
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True, timeout=300).stdout
    mono = np.frombuffer(pcm, dtype=np.float32)
    meter = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=300, check=False).stderr
    summary = meter[meter.rfind("Summary:"):]

    def field(name):
        found = re.search(name + r":\s+(-?[\d.]+|-inf)", summary)
        return None if not found or found.group(1) == "-inf" else float(found.group(1))
    first = mono[:60 * SR]
    seconds = first[:len(first) // SR * SR].reshape(-1, SR)
    level = 20 * np.log10(np.sqrt((seconds.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    power = np.abs(np.fft.rfft(first.astype(np.float64))) ** 2
    freqs = np.fft.rfftfreq(len(first), 1 / SR)
    total = float(power.sum()) or 1.0
    return {"duration_s": round(len(mono) / SR, 1), "integrated_lufs": field("I"), "lra_lu": field("LRA"),
            "true_peak_dbtp": field("Peak"), "sub120_share": round(float(power[freqs < 120].sum()) / total, 3),
            "quiet_seconds_first_60": int((level < level.max() - 30).sum())}


def run_facts(output: Path) -> dict:
    moods, sources, files, plans, words = Counter(), Counter(), Counter(), Counter(), 0
    by_run = {}
    for sel in sorted(output.glob("run_*/music_selection.json")):
        try:
            music = json.loads(sel.read_text(encoding="utf-8"))
        except ValueError:
            continue
        moods[str(music.get("mood"))] += 1
        sources[str(music.get("source"))] += 1
        if music.get("file"):
            files[music["file"]] += 1
            by_run[sel.parent.name] = music
    for plan in output.glob("run_*/sound_plan.json"):
        plans["null" if json.loads(plan.read_text(encoding="utf-8")) is None else "plan"] += 1
    for w in output.glob("run_*/words.json"):
        words += bool(json.loads(w.read_text(encoding="utf-8")))
    return {"runs_with_selection": sum(sources.values()), "selection_moods": dict(moods),
            "selection_sources": dict(sources), "distinct_tracks_selected": len(files),
            "max_selection_mood_share": round(max(moods.values()) / sum(moods.values()), 3) if moods else None,
            "sound_plans": dict(plans), "runs_with_timed_words": words}, by_run


def published_facts(published_runs: set, by_run: dict) -> dict:
    moods, used = Counter(), set()
    for run in published_runs:
        music = by_run.get(run)
        if music:
            moods[str(music.get("mood"))] += 1
            used.update(t["file"] for t in music.get("tracks") or [{"file": music["file"]}])
    reviewed = {t["file"] for t in library if t.get("reviewed")}
    return {"reel_published_runs": len(published_runs), "published_with_library_music": sum(moods.values()),
            "published_moods": dict(moods),
            "max_published_mood_share": round(max(moods.values()) / sum(moods.values()), 3) if moods else None,
            "reviewed_tracks": len(reviewed), "reviewed_tracks_with_a_published_use": len(used & reviewed)}


out = {"tracks": [], "library": {}}
for t in library:
    path = music_dir / t["file"]
    row = {"file": t["file"], "mood": t["mood"], "reviewed": bool(t.get("reviewed")), "creator": t.get("creator"),
           "license": t.get("license")}
    if path.is_file():
        row.update(measure_track(path))
    out["tracks"].append(row)
out["library"] = {"tracks": len(library), "reviewed_by_mood": dict(Counter(t["mood"] for t in library if t.get("reviewed"))),
                  "creators": len({t.get("creator") for t in library}),
                  "legacy_uses_by_mood": {m: sum(t.get("uses", 0) for t in library if t["mood"] == m)
                                          for m in sorted({t["mood"] for t in library})}}

dev, dev_runs = run_facts(dev_dir)
calls, answered = Counter(), Counter()
for log in dev_dir.glob("run_*/llm_calls.jsonl"):
    outcomes = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("task") == "music_mood":
            calls[str(record.get("outcome"))] += 1
            outcomes.append(record.get("outcome"))
    if "ok" in outcomes and log.parent.name in dev_runs:   # the classifier answered: the mood it gave
        answered[str(dev_runs[log.parent.name].get("mood"))] += 1
dev["mood_classifier_calls"] = dict(calls)
dev["selection_moods_where_the_classifier_answered"] = dict(answered)
if args.dev_db:
    db = sqlite3.connect(f"file:{args.dev_db.resolve().as_posix()}?mode=ro", uri=True)
    pubs = db.execute("SELECT run_id, channel, status FROM publications").fetchall()
    dev["publications"] = dict(Counter(f"{c}/{s}" for _, c, s in pubs))
    dev.update(published_facts({r for r, c, s in pubs if reel_receipt(c, s)}, dev_runs))
out["dev"] = dev

if args.prod:
    prod, prod_runs = run_facts(args.prod / "output")
    metrics = json.loads((args.prod / "metrics.json").read_text(encoding="utf-8"))
    pubs = metrics.get("publications") or []
    prod["publications"] = dict(Counter(f"{p['channel']}/{p['status']}" for p in pubs))
    prod.update(published_facts({p["run_id"] for p in pubs if reel_receipt(p["channel"], p["status"])}, prod_runs))
    # The first-start backfill (PipelineDatabase.backfill_music_selections), replayed on a temporary database from
    # the copy's runs and receipts and its run folders' music_selection.json: what production would count.
    sys.path.insert(0, str(root))
    from src.db import PipelineDatabase
    if hasattr(PipelineDatabase, "backfill_music_selections"):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="qf84-backfill-") as temp:
            replay = PipelineDatabase(db_path=Path(temp) / "replay.db", topic_bank_file=Path(temp) / "none.json")
            with replay._conn() as conn:
                for r in metrics.get("runs") or []:
                    conn.execute("INSERT OR IGNORE INTO topics (id, title) VALUES (?, ?)", (r["topic_id"], "t"))
                    conn.execute("INSERT OR IGNORE INTO runs (run_id, topic_id, status, created_at, updated_at) "
                                 "VALUES (?, ?, ?, ?, ?)", (r["run_id"], r["topic_id"], r["status"], r["created_at"],
                                                           r["created_at"]))
                for p in pubs:
                    conn.execute("INSERT INTO publications (topic_id, run_id, channel, status, created_at) "
                                 "VALUES ('t', ?, ?, ?, ?)", (p["run_id"], p["channel"], p["status"], p["created_at"]))
            filled = replay.backfill_music_selections(args.prod / "output")
            again = replay.backfill_music_selections(args.prod / "output")
            uses = replay.music_published_uses()
            prod["backfill"] = {"runs_filled": filled, "runs_filled_on_a_second_start": again,
                                "published_uses": sum(uses.values()), "tracks_with_a_published_use": len(uses)}
    out["prod"] = prod                 # counts only

sys.stdout.reconfigure(encoding="utf-8", newline="\n")
json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
print()
