"""QF-61 real-data check: on real runs, does any number reach an illustration prompt that is not a structured,
checked count or measure?

    python real_data.py <repo root> [--dev <output dir>] [--prod <copy of the production sample's output dir>]

Reads only (never writes) every run folder under the development output (default <root>/output) and the optional
production-sample COPY that has the pre-voice record (picture_lines.json), the timed words (words.json) and the
voiceover: the 22 runs QF-60 measured. The code under test is <root>'s own src, so the same script measures any
commit. Each run's fact sheet is <root>/data/fact_sheets/<the file its fact review names>; whether its SHA-256 equals
the run's recorded sheet_sha256 is counted.

Every sentence of the run's narration (src.prevoice_lines.sentences) goes through <root>'s image_gen.spec_from_facts
with the run's own payload and sheet, exactly as the planner calls it. Each spec's generator prompt is read by
measure.py's oracle (prose_numbers: numbers outside the fixed template and the structured counts sentence), and each
spec count or measure must be an exact entry of a supporting fact's quantities record (not_structured). Round 6 also
counts subjects with a broken sentence (broken_sentences) and "Draw only" lists that leave out a "clearly shows" item
or a subject word (inventory_conflicts), by the same oracle. Round 7 counts the subject's sentences and those that
are not byte-identical to a sentence of the supporting facts' statements (measure.subject_tiles), and lists the
supporting fact ids of each development spec (tracked fact-sheet ids, no run text) so that two commits can be
compared; the production sample gives counts only.

Prints JSON of counts only (decisions\\PROD-SAMPLE-counts-only.md): no run, sentence or file name.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--dev", type=Path, help="development output dir (default <root>/output)")
ap.add_argument("--prod", type=Path)
args = ap.parse_args()
root = args.root.resolve()
sys.path.insert(0, str(root))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import (
    broken_sentences,
    inventory_conflicts,
    not_structured,
    prose_numbers,
    subject_tiles,
)

from src import image_gen, prevoice_lines
from src.audio_engine import voiceover_file


def runs(folder: Path | None) -> list[Path]:
    if folder is None or not folder.is_dir():
        return []
    return sorted(d for d in folder.glob("run_*") if (d / "picture_lines.json").is_file()
                  and (d / "words.json").is_file() and voiceover_file(d).is_file())


out = {"runs": 0, "dev_runs": 0, "prod_runs": 0, "runs_with_sheet": 0, "sheet_hash_matches": 0, "sentences": 0,
       "sentences_with_facts": 0, "facts_with_structured_quantities": 0, "specs": 0, "refused_for_quantities": 0,
       "spec_counts": 0, "spec_measures": 0, "spec_numbers_not_from_a_structured_field": 0,
       "prompts_with_prose_numbers": 0, "prose_numbers": 0, "prompts_with_no_number_instruction": 0,
       "refused_no_clean_subject": 0, "subjects_with_broken_sentences": 0, "draw_only_excludes_a_clearly_shows_item": 0,
       "draw_only_excludes_a_subject_word": 0, "dev_specs": 0, "prod_specs": 0, "subject_sentences": 0,
       "subject_sentences_altered": 0, "prompts_without_the_spec_subject": 0, "dev_spec_fact_ids": []}
for label, folder in (("dev", args.dev or root / "output"), ("prod", args.prod)):
    for run in runs(folder):
        out["runs"] += 1
        out[f"{label}_runs"] += 1
        payload = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
        review = payload.get("fact_review") or {}
        name = Path(str(review.get("sheet_path") or "").replace("\\", "/")).name
        path = root / "data" / "fact_sheets" / name
        if not name or not path.is_file():
            continue
        out["runs_with_sheet"] += 1
        out["sheet_hash_matches"] += hashlib.sha256(path.read_bytes()).hexdigest() == review.get("sheet_sha256")
        sheet = json.loads(path.read_text(encoding="utf-8"))
        out["facts_with_structured_quantities"] += sum("quantities" in f for f in sheet.get("facts") or []
                                                        if isinstance(f, dict))
        for sentence in prevoice_lines.sentences(str(payload.get("narration_script") or "")):
            out["sentences"] += 1
            facts = image_gen._supporting_facts(sentence, sheet, payload)
            out["sentences_with_facts"] += bool(facts)
            spec, why = image_gen.spec_from_facts(sentence, None, sheet, payload)
            if spec is None:
                out["refused_for_quantities"] += "quantit" in why
                out["refused_no_clean_subject"] += "without their numbers" in why
                continue
            out["subjects_with_broken_sentences"] += bool(broken_sentences(spec))
            inv = inventory_conflicts(image_gen, spec)
            out["draw_only_excludes_a_clearly_shows_item"] += inv["excludes_a_clearly_shows_item"]
            out["draw_only_excludes_a_subject_word"] += inv["excludes_a_subject_word"]
            out["specs"] += 1
            out[f"{label}_specs"] += 1
            n_sentences, altered = subject_tiles(spec, [f.get("statement") for f in facts])
            out["subject_sentences"] += n_sentences
            out["subject_sentences_altered"] += altered
            out["prompts_without_the_spec_subject"] += f"Subject: {spec.get('subject')}" not in image_gen.build_prompt(
                spec)
            if label == "dev":
                out["dev_spec_fact_ids"].append(f"{name.rsplit('.', 1)[0]}:" + "+".join(spec.get("fact_ids") or []))
            out["spec_counts"] += len(spec.get("counts") or [])
            out["spec_measures"] += len(spec.get("measures") or [])
            out["spec_numbers_not_from_a_structured_field"] += not_structured(spec, facts)
            n = prose_numbers(image_gen, spec)
            out["prose_numbers"] += n
            out["prompts_with_prose_numbers"] += bool(n)
            no_count = getattr(image_gen, "NO_COUNT", None)
            out["prompts_with_no_number_instruction"] += bool(no_count) and no_count in image_gen.build_prompt(spec)
out["dev_spec_fact_ids"].sort()
print(json.dumps(out, indent=2))
