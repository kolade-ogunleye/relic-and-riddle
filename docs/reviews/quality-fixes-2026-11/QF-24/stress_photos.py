"""The photo-ground designs against every dev hero photo (worst case, not an average), base and this commit.

    python stress_photos.py <tree> <dev checkout holding output/run_*>

Read-only over the dev runs: each run's first hero still (beat_images/, sorted) is a ground. Four real copies (the
number pairs and the timeline whose layout QF-24 changes most, measurements_dev.json) are drawn on every ground as
the pipeline draws them at 1080 x 1920; each drawn line's contrast is measured on the final frame (TextPlate.measure,
as record_contrast does before the grade). Prints JSON (counts and minima; photo names are dev run folders).
"""
import json
import os
import socket
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

COPIES = ("run_trepanation_miracle_1792995462", "run_richat_structure_sahara_eye_1792778206",
          "run_amber_room_heist_1792790550", "run_amber_room_heist_1792781224")
_READY: list = []
_STATE: dict = {}


def _offline(*_a, **_k):
    raise RuntimeError("offline")


def _job(job):
    tree, run, payload, photo = job
    if not _READY:
        _READY.append(True)
        os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
        socket.create_connection = _offline
        socket.socket.connect = _offline
        sys.path.insert(0, tree)
        from src import graphics
        graphics.VIDEO_FPS = 2
        graphics.graded_frame = lambda *a, **k: None
        graphics._encode = lambda frames, path, n, *a, **k: [None for _ in frames] and Path(path)
        graphics.record_contrast = lambda o, frame, plates: _STATE.update(
            rows=[(n, p.measure(frame)) for n, p in plates if p is not None and not p.empty]) or {}
        graphics.record_nontext = lambda *a, **k: {}
    from src import brand, graphics
    _STATE.clear()
    with tempfile.TemporaryDirectory() as tmp:
        if payload.get("stat_cards"):
            kind = "numbers"
            graphics.render_stat_cards(payload["stat_cards"], Path(tmp) / "g.mp4", Path(photo),
                                       narration=payload.get("narration_script") or "")
        else:
            kind = "timeline"
            graphics.render_timeline(payload["timeline"], Path(tmp) / "g.mp4", photo=Path(photo))
    vals = [v for _n, v in _STATE.get("rows", []) if v is not None]
    return {"copy": run, "graphic": kind, "photo": Path(photo).parent.parent.name,
            "min_contrast": round(min(vals), 2) if vals else None,
            "under_target": sum(v < brand.TEXT_CONTRAST for v in vals), "lines": len(vals)}


def main(tree: str, dev: str) -> None:
    out = Path(dev) / "output"
    photos = []
    for folder in sorted(out.glob("run_*")):
        stills = sorted((folder / "beat_images").glob("*.jpg"))
        if stills:
            photos.append(str(stills[0]))
    jobs = []
    for run in COPIES:
        p = json.loads((out / run / "verified_script.json").read_text(encoding="utf-8"))
        payload = {k: p.get(k) for k in ("stat_cards", "narration_script")} if run != COPIES[-1] else \
            {"timeline": p["timeline"]}
        jobs += [(str(Path(tree).resolve()), run, payload, ph) for ph in photos]
    with ProcessPoolExecutor(4) as pool:
        rows = list(pool.map(_job, jobs))
    summary = {}
    for r in rows:
        s = summary.setdefault(r["copy"], {"graphic": r["graphic"], "grounds": 0, "min_contrast": None,
                                           "grounds_with_a_line_under_target": 0, "worst_ground": None})
        s["grounds"] += 1
        s["grounds_with_a_line_under_target"] += r["under_target"] > 0
        if r["min_contrast"] is not None and (s["min_contrast"] is None or r["min_contrast"] < s["min_contrast"]):
            s["min_contrast"], s["worst_ground"] = r["min_contrast"], r["photo"]
    print(json.dumps({"schema": 1, "photos": len(photos), "summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stress_photos.py <tree> <dev checkout>")
    main(*sys.argv[1:])
