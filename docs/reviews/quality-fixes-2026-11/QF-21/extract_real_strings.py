"""Collects the copy real runs drew in tracked or featured type, for measure.py (QF-21).

    python extract_real_strings.py <checkout holding output/run_*> <out json>

Read-only over <checkout>/output/run_*/verified_script.json, verified_deep_dive.json and the graphics' lineage records
(output/ is not committed, so the strings are copied into real_strings.json beside this file). Only copy is kept:
run folder name, field, text. Nothing else of a run (paths, ids, prompts) is copied.
"""
import json
import sys
from pathlib import Path


def main(root: Path, out: Path) -> None:
    rows = []

    def add(run, design, field, text):
        text = " ".join(str(text or "").split())
        if text:
            rows.append({"run": run, "design": design, "field": field, "text": text})

    for folder in sorted((root / "output").glob("run_*")):
        run = folder.name
        script = folder / "verified_script.json"
        if script.is_file():
            p = json.loads(script.read_text(encoding="utf-8"))
            thumb = p.get("thumbnail") or {}
            add(run, "cover", "eyebrow", thumb.get("eyebrow"))
            add(run, "cover", "tagline", thumb.get("tagline"))
            for s in p.get("story_slides") or []:
                add(run, "story", "kicker", s.get("kicker"))
            for c in p.get("carousel_slides") or []:
                add(run, "carousel", "heading", c.get("heading"))
            for e in p.get("timeline") or []:
                add(run, "timeline", "label", e.get("label"))
            for c in p.get("stat_cards") or []:
                add(run, "stat_cards", "label", c.get("label"))
            if p.get("scale_fact"):
                add(run, "scale", "subject", p["scale_fact"].get("subject"))
            for s in p.get("route") or []:
                add(run, "route", "place", s.get("place"))
            for label in p.get("photo_labels") or []:
                add(run, "callouts", "label", label)
            loc = p.get("location") or {}
            add(run, "map", "country", loc.get("country"))
        deep = folder / "verified_deep_dive.json"
        if deep.is_file():
            d = json.loads(deep.read_text(encoding="utf-8"))
            for act in d.get("acts") or []:
                add(run, "chapter_card", "act_title", act.get("act_title") if isinstance(act, dict) else act)
        lineage = folder / "graphics" / "graphic_map.mp4.lineage.json"
        if lineage.is_file():
            place = json.loads(lineage.read_text(encoding="utf-8"))["settings"]["inputs"].get("place") or {}
            add(run, "map", "place", place.get("label"))
    seen, unique = set(), []
    for r in rows:
        key = (r["design"], r["field"], r["text"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    out.write_text(json.dumps(unique, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(len(unique), "strings")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
