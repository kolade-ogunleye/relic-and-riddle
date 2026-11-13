"""Which real runs would get a hook, chapter or myth card composed, and what the opening does to their clock.

Usage: python real_runs.py <repo-root> <output-dir> [--fact-sheets <dir>]

Reads run folders (script_metadata.json, words.json, the master's captions .ass) and the fact sheets they name,
read-only, and prints counts only: no run, topic or copy is printed. Pure: no model, no network, no rendering, no
record key (signatures are not verified here; the composer refuses an unsigned field whatever this counts).
An eligible field is one the composer would try: a checked-card statement short enough for the hook
(card_composition.make_all: <= 30 characters and <= 6 words), a myth whose myth and correction are both spoken in
that order (the condition _myth_timing needs), a documentary whose chapters fit card_composition.chapter_cuts.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _first_caption(run: Path) -> float | None:
    for ass in run.glob("*_captions.ass"):
        starts = []
        for line in ass.read_text(encoding="utf-8").splitlines():
            if line.startswith("Dialogue:"):
                h, m, s = line.split(",", 2)[1].split(":")
                starts.append(int(h) * 3600 + int(m) * 60 + float(s))
        return min(starts) if starts else None
    return None


def measure(root: Path, output: Path, sheets_dir: Path | None) -> dict:
    sys.path.insert(0, str(root))
    from src import card_composition, design_cards
    from src.scene_planner import documentary_cuts
    clear = design_cards.CAPTION_CLEAR
    offset = math.ceil(clear * 30) / 30
    c = {"runs": 0, "reels": 0, "documentaries": 0, "with_words": 0, "fact_sheet_found": 0,
         "sheet_with_card_fields": 0, "statements": 0, "myths": 0, "quotes": 0,
         "hook_eligible_statement": 0, "myth_spoken_in_order": 0, "documentary_chapters": 0,
         "documentary_chapter_cuts_fit": 0, "first_caption_before_clear_today": 0,
         "first_caption_at_or_after_offset_with_hook": 0, "first_caption_today_s": []}
    for run in sorted(output.glob("run_*")):
        meta_path, words_path = run / "script_metadata.json", run / "words.json"
        if not meta_path.is_file():
            continue
        c["runs"] += 1
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        documentary = meta.get("delivery_profile") == "deep_dive_16x9" or bool(meta.get("chapters"))
        c["documentaries" if documentary else "reels"] += 1
        words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.is_file() else None
        if words:
            c["with_words"] += 1
        sheet_path = (meta.get("fact_review") or {}).get("sheet_path")
        sheet = None
        if sheet_path:
            candidates = [Path(sheet_path)] + ([sheets_dir / Path(sheet_path).name] if sheets_dir else [])
            found = next((p for p in candidates if p.is_file()), None)
            if found:
                sheet = json.loads(found.read_text(encoding="utf-8"))
                c["fact_sheet_found"] += 1
        fields = (sheet or {}).get("cards") or {}
        if any(fields.get(k) for k in ("statements", "myths", "quotes")):
            c["sheet_with_card_fields"] += 1
        for k in ("statements", "myths", "quotes"):
            c[k] += len(fields.get(k) or [])
        if not documentary and any(len(f.get("text", "")) <= 30 and len(f.get("text", "").split()) <= 6
                                   for f in fields.get("statements") or []):
            c["hook_eligible_statement"] += 1
        if words:
            for field in fields.get("myths") or []:
                myths = card_composition._phrase_times(words, field.get("myth", ""))
                fixes = card_composition._phrase_times(words, field.get("correction", ""))
                if any(f[0] >= m[1] for m in myths for f in fixes):
                    c["myth_spoken_in_order"] += 1
        if documentary and words and meta.get("chapters"):
            c["documentary_chapters"] += len(meta["chapters"])
            total = max(float(w["start"]) + float(w["dur"]) for w in words)
            try:
                card_composition.chapter_cuts(documentary_cuts(words, total), meta["chapters"], words, total)
                c["documentary_chapter_cuts_fit"] += 1
            except ValueError:
                pass
        if not documentary and words:
            first = _first_caption(run)
            if first is not None:
                c["first_caption_today_s"].append(round(first, 3))
                c["first_caption_before_clear_today"] += first < clear
            shifted = min(float(w["start"]) for w in words) + offset
            c["first_caption_at_or_after_offset_with_hook"] += shifted >= clear - 1e-9
    times = c.pop("first_caption_today_s")
    c["first_caption_today_s_min_median_max"] = ([min(times), sorted(times)[len(times) // 2], max(times)]
                                                 if times else None)
    c["caption_clear_until_s"] = clear
    c["hook_offset_s"] = offset
    return c


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fact-sheets", type=Path)
    args = parser.parse_args()
    print(json.dumps(measure(args.root.resolve(), args.output.resolve(), args.fact_sheets), indent=2))
