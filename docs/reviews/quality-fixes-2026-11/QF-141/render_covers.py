"""QF-141 evidence renders: every catalogue cover case and every dev run cover with a verified hero, drawn by one
code root (the base or the fix), with each cover's record. Writes only under <out>; reads the dev checkout's
output/run_* and assets read-only.

    python render_covers.py <code root> <dev checkout> <out dir>

Cases: the freeze-panel fixtures' covers (fixtures.load("covers"), 5) and the covers survey's runs (26 dev runs
whose hero is the still that matches their delivered cover). Per case: thumbnail_cover.jpg, its grid tile, the
YouTube thumbnail and the cover's still_layout.json, as VisualEngine draws them in a run.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def _offline(*_a, **_k):
    raise OSError("offline: QF-141 renders make no network call")


def main() -> int:
    code, dev, out = (Path(a).resolve() for a in sys.argv[1:4])
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))
    socket.create_connection = _offline
    socket.socket.connect = _offline
    os.chdir(code)
    sys.path.insert(0, str(code))
    sys.path.insert(0, str(code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import fixtures as fx

    from src.visual_engine import VisualEngine
    cases = [("catalogue", c["topic"], fx.path(c["hero"]["photo"]), fx.payload(c)) for c in fx.load("covers")]
    for s in fx.section("covers")["survey"]:
        run = fx.path(s["run"])
        cases.append(("dev", run.name, fx.path(s["hero"]),
                      json.loads((run / "verified_script.json").read_text(encoding="utf-8"))))
    errors = {}
    for src, name, hero, payload in cases:
        d = out / src / name
        d.mkdir(parents=True, exist_ok=True)
        try:
            eng = VisualEngine()
            eng.generate_thumbnails(Path(hero), json.loads(json.dumps(payload)), d, style="badge")
            eng.generate_youtube_thumbnail(Path(hero), json.loads(json.dumps(payload)), d / "youtube_thumbnail.jpg")
            print("ok ", src, name, flush=True)
        except Exception as e:  # noqa: BLE001  one failed case is recorded, the rest render
            errors[f"{src}/{name}"] = f"{type(e).__name__}: {e}"[:300]
            print("ERR", src, name, errors[f"{src}/{name}"], flush=True)
    (out / "errors.json").write_text(json.dumps(errors, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
