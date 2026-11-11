"""Collects the checked copy real runs fit into boxes, for measure.py (QF-23).

    python extract_real_inputs.py <checkout holding output/run_*> <out json>

Read-only over <checkout>/output/run_*/verified_script.json (output/ is not committed, so the fields are copied into
real_inputs.json beside this file). Only the copy the stills and graphics draw is kept, field by field, with the run
folder's name: no paths, ids, prompts, credits or anything else of a run. Not for the production sample: its copy
stays outside the repository (measure.py reads it in place, given the folder as its second argument).
"""
import json
import sys
from pathlib import Path

FIELDS = ("title", "thumbnail", "story_slides", "carousel_slides", "poll", "share_line", "timeline", "stat_cards",
          "scale_fact", "cross_section", "route", "location", "narration_script", "delivery_profile")


def collect(output: Path) -> list:
    rows = []
    for folder in sorted(output.glob("run_*")):
        script = folder / "verified_script.json"
        if not script.is_file():
            continue
        p = json.loads(script.read_text(encoding="utf-8"))
        rows.append({"run": folder.name, **{k: p[k] for k in FIELDS if p.get(k) not in (None, "", [], {})}})
    return rows


def main(root: Path, out: Path) -> None:
    rows = collect(root / "output")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{len(rows)} runs -> {out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: extract_real_inputs.py <checkout> <out json>")
    main(Path(sys.argv[1]), Path(sys.argv[2]))
