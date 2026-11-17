"""QF-60b acceptance: a reconstruction is granted by its structure, never by its prose.

    python measure.py <repo root>

Part 1 runs <root>'s code on the lists of this tree's tests/test_qf60b_reconstruction_reason.py through the three
places that decide a reconstruction (the planner's grant, the generation gate with generation off, the relaxed
nothing-added rule); a verdict is granted only when all three agree:
- prose only (no structured cause, no subject words), to be refused: every reason list of rounds 1-3 (placeholders
  and paraphrases, subjectless, genuine, the 34 VERSION 4-style and 20 VERSION 5-style reasons, restatements and
  copies, hedges, contradictions, scripts and foreign clauses, edge cases) and review 3's 14 probes;
- structured and genuine, to be granted: STRUCTURED (10, all four kinds) and every (kind, cause) pair the table
  allows (6);
- structured but wrong, to be refused: every (kind, cause) pair the table does not allow (14); review 4's probes
  (SURVIVING: before_photography on a look that survives unchanged and on a mechanism that still works); review
  3's probes with their subject words and no cause (14) or with each cause their kind does not allow; causes
  outside the list or mistyped (BAD_CAUSES); subject words that are not the sentence's own (BAD_SUBJECTS); subject
  words of another sentence; a reviewer's reason that is a bare placeholder.
Part 2 reads the committed real_data*.json beside this file, written by real_data.py over the 22 real runs
(development output and the production-sample copy; counts only) on QF-60 (84c3eeb), round 3 (1f7de19a) and this
commit.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from src import image_gen, image_quarantine, reconstruction

here = Path(__file__).resolve().parent
sys.path.append(str(here.parents[3] / "tests"))   # the test file imports the pre-voice test's fixture
spec = importlib.util.spec_from_file_location("qf60b_lists", here.parents[3] / "tests/test_qf60b_reconstruction_reason.py")
lists = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lists)
MODES = ("voice", "tail_1s", "tail_3s")


def verdict(kind: str, line: str, reason: str, cause=None, subject=None, structured: bool = True) -> dict:
    v = {"sentence": line, "kind": "generable", "visual": "named_subject", "reconstruction": True,
         "reconstruction_kind": kind, "reconstruction_reason": reason,
         "placement": {"by": "word_position", "sentence_index": 0, "beat": line}}
    if structured:
        v.update(reconstruction_cause=cause, reconstruction_subject_words=subject)
    return v


def places(v: dict) -> dict:
    line = v["sentence"]
    spec = {"line": line, "required": ["bronze gears"], "why_illustrated": image_gen.RECONSTRUCTION_REASON,
            **reconstruction.bind(v)}
    with patch.object(image_gen, "is_available", lambda: False):
        _, info = image_gen.illustrate_beat(line, dict(spec), None, reason=image_gen.RECONSTRUCTION_REASON)
    return {"planner": image_gen.illustration_reason(line, verdict=v) == image_gen.RECONSTRUCTION_REASON,
            "gate": info.get("reason") == "generation off or no key",
            "nothing_added_relaxed": "may show more" in image_quarantine._nothing_added_rule(spec)}


def tally(verdicts: list) -> dict:
    rows = [places(v) for v in verdicts]
    out = {k: f"{sum(r[k] for r in rows)}/{len(rows)}" for k in ("planner", "gate", "nothing_added_relaxed")}
    out["granted_all_three"] = f"{sum(all(r.values()) for r in rows)}/{len(rows)}"
    return out


def prose(rows: list, kind: str = "mechanism_in_use", line: str = lists.GEARS) -> dict:
    """Reasons as the only answer: (kind, line, reason) tuples or bare reasons on `kind` and `line`."""
    return tally([verdict(*(r if isinstance(r, tuple) else (kind, line, r)), structured=False) for r in rows])


def keyed(rows: list) -> list:
    return [(lists.SENTENCES[k][0], lists.SENTENCES[k][1], reason) for k, reason, *_ in rows]


def per_kind(kind: str, **changes) -> dict:
    line, subject, reason = lists.PER_KIND[kind]
    v = verdict(kind, line, reason, min(lists.COMPATIBLE[kind]), subject)
    v.update(changes)
    return v


def real(name: str) -> dict | None:
    path = here / name
    return json.loads(path.read_text(encoding="utf-8"))["total"] if path.is_file() else None


def per_mode(total: dict | None) -> dict | None:
    if total is None:
        return None
    return {mode: {
        "beats_inside_one_sentence": m["beats_inside_one_sentence"],
        "prose_only_granted": f"{m['prose_granted']}/{m['prose_checks']}",
        "structured_genuine_granted": f"{m['structured_granted']}/{m['structured_checks']}",
        "mismatched_cause_granted": f"{m['mismatched_granted']}/{m['mismatched_checks']}",
        "other_sentence_subject_granted": f"{m['other_subject_granted']}/{m['other_subject_checks']}",
        "wrong_sentence_granted": f"{m['wrong_sentence_granted']}/{m['wrong_sentence_checks']}"}
        for mode, m in total.items() if mode in MODES}


probes = lists.ROUND3_PROBES
result = {
    "scope": "Verdict lists through the three checks of <root> (part 1); real beats of 22 runs, counts only, at the "
             "voice length and +1/+3 s tail (part 2)",
    "prose_only": {
        "placeholders": prose(lists.PLACEHOLDERS, lists.KIND, lists.LINE),
        "subjectless": prose(lists.SUBJECTLESS, lists.KIND, lists.LINE),
        "genuine": prose(list(lists.GENUINE)),
        "version_4_style": prose(keyed(lists.REALISTIC)),
        "version_5_style": prose(keyed(lists.REWRITTEN)),
        "restated_or_copied": prose(lists.RESTATED + lists.COPIED),
        "hedged": prose(lists.HEDGED),
        "contrary": prose(lists.CONTRARY),
        "scripts_and_foreign_clauses": prose(lists.SCRIPTS),
        "genuine_edge_cases": prose(lists.GRANTED_EDGE),
        "round3_probes": prose(probes),
    },
    "structured_genuine": tally([verdict(k, line, r, c, s) for k, line, s, c, r in lists.STRUCTURED]),
    "allowed_pairs": tally([per_kind(k, reconstruction_cause=c) for k, c in lists.ALLOWED]),
    "mismatched_pairs": tally([per_kind(k, reconstruction_cause=c) for k, c in lists.MISMATCHED]),
    "round4_probes_before_photography": tally([verdict(k, line, r, "before_photography", s)
                                               for k, line, s, r in lists.SURVIVING]),
    "round3_probes_subject_no_cause": tally([verdict(k, line, r, None, lists.PROBE_SUBJECTS[line])
                                             for k, line, r in probes]),
    "round3_probes_disallowed_cause": tally([verdict(k, line, r, c, lists.PROBE_SUBJECTS[line])
                                             for k, line, r in probes for c in lists.CAUSES
                                             if c not in lists.COMPATIBLE[k]]),
    "bad_causes": tally([per_kind("mechanism_in_use", reconstruction_cause=c) for c in lists.BAD_CAUSES]),
    "bad_subjects": tally([per_kind("mechanism_in_use", reconstruction_subject_words=s) for s in lists.BAD_SUBJECTS]),
    "other_sentence_subjects": tally([per_kind(k, reconstruction_subject_words=lists.PER_KIND[o][1])
                                      for k in lists.COMPATIBLE for o in lists.COMPATIBLE if o != k]),
    "placeholder_reviewer_reason": tally([per_kind("mechanism_in_use", reconstruction_reason=r)
                                          for r in ("none", "N/A", "TBD", "No reason given.", "unknown")]),
}
base, round3, now = real("real_data_base.json"), real("real_data_round3.json"), real("real_data.json")
result["real_runs"], result["real_sentences"] = now["runs"], now["sentences"]
for name, total in (("base", base), ("round3", round3), ("this_commit", now)):
    if total is not None:
        result[f"real_{name}"] = per_mode(total)
        result[f"real_{name}_model_reasons_prose_only_granted"] = (
            f"{total['model_reasons_granted']}/{total['model_reasons']}")
        result[f"real_{name}_sentence_copied_prose_only_granted"] = {
            k: f"{n}/{total['sentences']}" for k, n in total["sentence_copied_granted"].items()}


def none_granted(t: dict) -> bool:
    return all(t[k].startswith("0/") for k in ("planner", "gate", "nothing_added_relaxed", "granted_all_three"))


def all_granted(t: dict) -> bool:
    n = t["granted_all_three"].split("/")[1]
    return all(t[k] == f"{n}/{n}" for k in ("planner", "gate", "nothing_added_relaxed", "granted_all_three"))


modes = list(MODES)
result["lists_met"] = (all(none_granted(t) for t in result["prose_only"].values())
                       and all_granted(result["structured_genuine"]) and all_granted(result["allowed_pairs"])
                       and all(none_granted(result[k]) for k in (
                           "mismatched_pairs", "round4_probes_before_photography",
                           "round3_probes_subject_no_cause", "round3_probes_disallowed_cause",
                           "bad_causes", "bad_subjects", "other_sentence_subjects", "placeholder_reviewer_reason")))
result["real_no_prose_grant"] = (all(now[m]["prose_granted"] == 0 for m in modes)
                                 and now["model_reasons_granted"] == 0
                                 and all(n == 0 for n in now["sentence_copied_granted"].values()))
result["real_no_wrong_grant"] = all(now[m][k] == 0 for m in modes for k in (
    "mismatched_granted", "other_subject_granted", "wrong_sentence_granted"))
result["real_no_structured_grant_lost"] = all(
    now[m]["structured_granted"] == base[m]["structured_granted"] for m in modes)
result["acceptance_met"] = (result["lists_met"] and result["real_no_prose_grant"] and result["real_no_wrong_grant"]
                            and result["real_no_structured_grant_lost"])
print(json.dumps(result, indent=2))
