"""Measurement for quality step 5: look at the rendered Story frames and carousel slides of finished runs with
the still_vision check and print what it reports, before its findings mean anything to a release.

    python scripts/look_at_stills.py output/run_a output/run_b ...
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import still_vision  # noqa: E402


def main() -> int:
    runs = [Path(a) for a in sys.argv[1:]]
    if not runs:
        print(__doc__)
        return 2
    looked = flagged = 0
    for run in runs:
        meta = run / "script_metadata.json"
        payload = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        stories = sorted((run / "stories").glob("story_*.jpg"))
        slides = sorted((run / "carousel").glob("carousel_slide_*.jpg"))
        print(f"== {run.name}: {len(stories)} Story frames, {len(slides)} slides")
        for i, p in enumerate(stories):
            problems, reason = still_vision.look(p, "Story frame", still_vision.story_copy(payload, i))
            looked += 1
            flagged += bool(problems)
            print(f"  {p.name:24} {('OK' if not problems else ', '.join(problems)):40} {reason}")
        for i, p in enumerate(slides):
            problems, reason = still_vision.look(p, "carousel slide", still_vision.carousel_copy(payload, i))
            looked += 1
            flagged += bool(problems)
            print(f"  {p.name:24} {('OK' if not problems else ', '.join(problems)):40} {reason}")
    print(f"\n{looked} stills looked at, {flagged} with a finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
