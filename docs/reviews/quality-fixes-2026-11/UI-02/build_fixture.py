"""Copies one real development run's records into tests/fixtures/ui02_review_v2/<run>/ for the V2 review card test.

    python build_fixture.py <checkout with output/> <repo root to write into> [run folder name]

Copied: script_metadata.json, release_report.json, voice.json, music_selection.json (the files the V2 card reads).
Absolute paths are made repo-relative (output/ and data/ are gitignored; CI has neither) and music_selection.json
keeps no local path at all. Writes LF JSON. Development data only, never the production sample.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FILES = ("script_metadata.json", "release_report.json", "voice.json", "music_selection.json")
DEFAULT_RUN = "run_gol_gumbaz_whispering_gallery_1793278143"


def relative(value):
    if isinstance(value, dict):
        return {k: relative(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relative(v) for v in value]
    if isinstance(value, str) and re.match(r"^[A-Za-z]:[\\/]", value):
        tail = re.split(r"[\\/]relic-and-riddle[\\/]", value, maxsplit=1)
        return tail[1].replace("\\", "/") if len(tail) == 2 else Path(value).name
    return value


def main() -> None:
    dev, repo = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    run = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_RUN
    source, target = dev / "output" / run, repo / "tests" / "fixtures" / "ui02_review_v2" / run
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        data = json.loads((source / name).read_text(encoding="utf-8"))
        data = relative(data)
        if name == "music_selection.json":   # the library file's local location is not the card's business
            data.pop("path", None)
            data.pop("paths", None)
            for track in data.get("tracks") or []:
                track.pop("path", None)
        text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        (target / name).write_bytes(text.encode("utf-8"))
        print(name, len(text))


if __name__ == "__main__":
    main()
