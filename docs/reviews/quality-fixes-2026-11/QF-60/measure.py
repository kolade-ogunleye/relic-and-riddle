"""QF-60 acceptance: keyword false positives, the structured verdict, and whether it reaches the real beats.

    python measure.py <repo root>

Part 1 runs <root>'s code on the 25 ordinary lines of tests/test_qf60_reconstruction_verdict.py (the provider's
answer is mocked: this is the permission boundary, not a live semantic-model accuracy score). Part 2 reads the
committed real_data*.json beside this file, written by real_data.py over the 22 real runs (development output and
the production-sample copy; counts only) for the base, rounds 3 and 4 and this commit, at the pipeline's voice
length and with 0, 1 and 3 s of tail silence after the last word.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from src import free_llm, image_gen, image_quarantine, prevoice_lines

here = root / "docs/reviews/quality-fixes-2026-11/QF-60"
spec = importlib.util.spec_from_file_location("qf60_fixtures", root / "tests/test_qf60_reconstruction_verdict.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
reasons = [image_gen.illustration_reason(line) for line in fixtures.ORDINARY]
line = "The ancient mechanism drove its bronze gears."


def answer(system, prompt, schema, **kwargs):
    return schema(lines=[{"index": 1, **{k: v for k, v in fixtures.verdict(line).items() if k != "sentence"}}])


with patch.object(free_llm, "call", side_effect=answer):
    row = prevoice_lines.judge(line, [{"file": "fragments.png", "text": "Surviving fragments"}], "Mechanism")[0]
bare = {"line": "Inside, the gears turned.", "required": ["bronze gears"],
        "why_illustrated": image_gen.RECONSTRUCTION_REASON}
strict = "may show more" not in image_quarantine._nothing_added_rule(bare)


MODES = ("voice", "tail_0s", "tail_1s", "tail_3s")


def real(name):
    return json.loads((here / name).read_text(encoding="utf-8"))["total"]


def per_mode(total):
    """At each voice length: valid verdicts granted/refused on the beats inside one sentence, multi-beat-sentence
    beats granted, and the grants that must never happen (wrong sentence, invalid, other sentence's verdict)."""
    out = {}
    for mode in MODES:
        m = total.get(mode)
        if not m:
            continue
        out[mode] = {
            "beats": m["beats"], "beats_inside_one_sentence": m["beats_inside_one_sentence"],
            "valid_granted": f"{m['valid_verdict_granted']}/{m['beats_inside_one_sentence']}",
            "valid_refused": m["valid_verdict_refused"], "refused_unplaced": m["refused_unplaced"],
            "multi_beat_sentence_beats_granted": f"{m['multi_beat_sentence_beats_granted']}/{m['multi_beat_sentence_beats']}",
            "wrong_sentence_granted": f"{m['wrong_sentence_granted']}/{m['wrong_sentence_checks']}",
            "invalid_verdict_granted": f"{m['invalid_verdict_granted']}/{m['invalid_verdict_checks']}",
            "other_sentence_verdict_granted": f"{m['other_sentence_verdict_granted']}/{m['other_sentence_verdict_checks']}"}
    return out


base, round3, round4, now = (real(f"real_data{n}.json") for n in ("_base", "_round3", "_round4", ""))
result = {
    "scope": "Permission boundary with the provider mocked (part 1); real beats and verdicts of 22 runs at four voice "
             "lengths (part 2)",
    "ordinary_lines": [{"line": text, "reason": reason} for text, reason in zip(fixtures.ORDINARY, reasons)],
    "ordinary_lines_total": len(reasons),
    "ordinary_lines_flagged": sum(bool(reason) for reason in reasons),
    "three_examples_empty": all(not reason for reason in reasons[:3]),
    "structured_verdict_retained": row.get("reconstruction") is True and row.get("reconstruction_kind") == "mechanism_in_use",
    "unsupported_reconstruction_keeps_strict_reference_rule": strict,
    "real_runs": now["runs"], "real_sentences": now["sentences"],
    "real_runs_timeline_differs_from_script": now["runs_timeline_differs_from_script"],
    "real_old_regex_sentences_flagged": f"{base['sentences_flagged_without_verdict']}/{base['sentences']}",
    "real_sentences_flagged_now": f"{now['sentences_flagged_without_verdict']}/{now['sentences']}",
    "real_round3": per_mode(round3), "real_round4": per_mode(round4), "real_this_commit": per_mode(now),
}
never = ("wrong_sentence_granted", "invalid_verdict_granted", "other_sentence_verdict_granted")
result["real_never_granted_in_every_mode"] = all(now[mode][k] == 0 for mode in MODES for k in never)
result["real_every_inside_beat_granted_unless_unplaced"] = all(
    now[mode]["valid_verdict_refused"] == now[mode]["refused_unplaced"] for mode in MODES)
result["acceptance_met"] = (result["ordinary_lines_flagged"] <= 1 and result["three_examples_empty"]
                            and result["structured_verdict_retained"] and strict
                            and now["sentences_flagged_without_verdict"] == 0
                            and result["real_never_granted_in_every_mode"]
                            and result["real_every_inside_beat_granted_unless_unplaced"])
print(json.dumps(result, indent=2))
