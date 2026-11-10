"""QF-14 measurement: hook enforcement. Prints JSON and writes nothing. No network, no model, no provider call.

    python docs/reviews/quality-fixes-2026-11/QF-14/measure.py <repo root> [<runs dir> ...]
        [--sample <runs dir>] [--judgments <file>]

<repo root> is the code measured (the parent or this row). Committed inputs: the synthetic openings below and the
repository's own script library (data/script_library/*/verified_script.json), curated scripts (data/scripts) and
topic bank. Each optional <runs dir> holds run_*/script_metadata.json (with words.json and llm_calls.jsonl where
present): the development output/ folder. For those, only counts, run names and sentence hashes are printed, never
their text.

Production sample (decisions/PROD-SAMPLE-counts-only.md): pass a COPY of its output/ with --sample and the
builder's judgments of its openings with --judgments (a JSON file {sentence hash: "strong" | "weak"}); both stay
outside the repository. A --sample folder is printed as counts only: no run name, sentence hash or text.

JUDGED is the builder's reading of each real opening sentence of the library and the development runs (the
production sample's are in the --judgments file) (keyed by the sha256 of the sentence,
first 12 hex digits): "strong" is an opening worth keeping as it is (fact or riddle first), "weak" one that should be
edited. It is an input, stated as such; the rule never reads it. With it, the corpus table separates justified
refusals from false rejects (a strong opening refused though it is within the 12-word cap) and false accepts (a weak
opening the lexical floor lets through). A strong opening over the cap is counted apart: refused by the acceptance's own
cap, it is trimmed by the opening edit, not rewritten.

The routing replay runs each real script through the tree's own generate_script with a writer that returns that
same script every time (the worst case: a writer that keeps its opening) and a counting stand-in for every edit call,
in two modes: the opening edit fixes the hook and changes nothing else (the same words, marked as fixed for
hook_problems), or it fails. Spoken length is not measured (no narrator here), so the replay isolates the
hook; the long-draft case below covers a length fault and a hook fault together.
"""

import hashlib
import json
import statistics
import sys
from pathlib import Path
from unittest.mock import patch

FIX = "No mortar holds these stones together."   # the long-draft case's passing opening (never published)
EDITED = "\u2060"   # the replay's mark of a narration its stand-in opening edit fixed (a word joiner, never spoken)

BAD = {
    "label": "This is a temple carved from one rock.",
    "thirteen_words": "Workers carefully cut this enormous temple from one solid rock starting far above.",
    "number_at_word_9": "Beyond the hills along the river workers moved 200 stones.",
    "vague_size_only": "Sound travels along a massive curved stone wall for hours.",
}
# what the operator's ruling (decisions/QF-14-hook-rule.md) makes of each bad opening when its one edit fails
RULED = {"label": "blocked", "thirteen_words": "blocked", "number_at_word_9": "ships_flagged",
         "vague_size_only": "ships_flagged"}
CONTROLS = {
    "number": "Workers moved 200 stones across the river.",
    "question": "How do you carve a temple from the top down?",
    "negation": "No mortar holds these stones together.",
    "adversative_and_time_span": "Bridges rust within decades, yet these stone arches still stand.",
    "hyphenated_number_word": "Soldiers dismantled a palace hall in thirty-six hours.",
}

JUDGED = {
    "64f1f448ca66": "weak", "59482858ee41": "weak", "46275d756434": "strong", "70d08284b2b0": "weak",
    "fec032bfdda7": "strong", "221945ecdcab": "strong", "dadd67c94432": "strong", "f9c481d26498": "strong",
    "a568161a6db2": "weak", "22f1289fb491": "strong", "0ee8dce2bfc1": "weak", "cfe25f325790": "weak",
    "c4f6e35485f4": "weak", "d4341cfefa7d": "strong", "721b76068398": "strong", "bab8dcce590f": "strong",
    "1d7b78665e23": "weak", "fc831067a8d1": "weak", "3a8bf504aebc": "weak", "84a74b1a7965": "weak",
    "30d08134d514": "strong", "4e1444580149": "weak", "f88839197c59": "weak", "cff70796b8fe": "strong",
    "6429a43fc1e2": "weak", "698ce5ed8291": "strong",
}


def sha(sentence: str) -> str:
    return hashlib.sha256(" ".join(sentence.split()).encode()).hexdigest()[:12]


def reasons(problems):
    out = []
    for p in problems:
        out.append("label" if "opens on a label" in p else "long" if "-word sentence" in p
                   else "signal" if "spoken tokens" in p else "empty" if "no opening sentence" in p else "other")
    return out


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def outcome(engine, narration):
    """What the tree does with this opening if its one opening edit changes nothing (the worst case): "blocked" (a
    blocking hook fault: the cap or a label), "ships_flagged" (only a hook finding that does not block: on QF-14 a
    missing signal, shipped with the review card's flag; on the parent every hook finding was advisory) or
    "ships_clean"."""
    faults = engine.hook_problems(narration)
    return "blocked" if engine.blocking_problems(faults) else "ships_flagged" if faults else "ships_clean"


def outcomes(engine, narrations):
    out = {"blocked": 0, "ships_flagged": 0, "ships_clean": 0}
    for n in narrations:
        out[outcome(engine, n)] += 1
    return out


def corpus(engine, scripts, quote, per_item=True):
    """scripts: [(run name, payload)] (unique narrations). Counts, the precision table and the refusals (per item, or
    as counts when per_item is False)."""
    rows, words = [], []
    table = {"refused_weak": 0, "refused_strong_over_the_cap": 0, "false_reject": 0, "passed_strong": 0,
             "passed_weak": 0, "unjudged": 0}
    by_reason = {"label": 0, "long": 0, "signal": 0, "empty": 0, "other": 0}
    for name, payload in scripts:
        first = engine._first_sentence(payload.get("narration_script") or "")
        n = len(engine._spoken_words(first))
        words.append(n)
        faults = engine.hook_problems(payload.get("narration_script") or "")
        refused = bool(engine.blocking_problems(faults))
        why = reasons(faults)
        mark = JUDGED.get(sha(first))
        if mark is None:
            table["unjudged"] += 1
        elif not refused:
            table["passed_" + mark] += 1
        elif mark == "weak":
            table["refused_weak"] += 1
        else:
            table["refused_strong_over_the_cap" if "long" in why else "false_reject"] += 1
        if refused:
            for r in set(why):
                by_reason[r] += 1
        row = {"run": name, "sha": sha(first), "spoken_words": n, "refused": refused, "reasons": why, "mark": mark}
        if quote:
            row["first_sentence"] = first
        rows.append(row)
    refusals = [r for r in rows if r["refused"]]
    false_accepts = [r for r in rows if not r["refused"] and r["mark"] == "weak"]
    return {"scripts": len(scripts), "refused": sum(r["refused"] for r in rows),
            "outcomes": outcomes(engine, [p.get("narration_script") or "" for _, p in scripts]),
            "outcomes_strong": outcomes(engine, [p.get("narration_script") or "" for _, p in scripts
                                                 if JUDGED.get(sha(engine._first_sentence(p.get("narration_script") or "")))
                                                 == "strong"]),
            "outcomes_weak": outcomes(engine, [p.get("narration_script") or "" for _, p in scripts
                                               if JUDGED.get(sha(engine._first_sentence(p.get("narration_script") or "")))
                                               == "weak"]),
            "flagged_advisory_or_blocking": sum(bool(r["reasons"]) for r in rows),
            "refused_by_reason": by_reason, "precision": table,
            "first_sentence_words": {"min": min(words, default=None), "median": median(words),
                                     "max": max(words, default=None),
                                     "at_most_12": sum(w <= 12 for w in words)},
            "refusals": refusals if per_item else len(refusals),
            "false_accepts": false_accepts if per_item else len(false_accepts)}


def opening_seconds(engine, run_dir: Path, payload):
    """When the first sentence's last word ends in the real voiceover (words.json), and when its 12th word ends."""
    try:
        timed = json.loads((run_dir / "words.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    n = len(engine._spoken_words(engine._first_sentence(payload.get("narration_script") or "")))
    if not timed or n == 0:
        return None
    def end(i):
        word = timed[min(i, len(timed)) - 1]
        return round(word["start"] + word["dur"], 2)
    return {"first_sentence_ends_s": end(n), "word_12_ends_s": end(12)}


def replay(engine_mod, payload, mode: str):
    """Calls one writer run makes for this script (see the module docstring), and how it ends."""
    from pydantic import ValidationError

    from src import audio_engine, fact_evidence
    from src.models import ScriptPackage
    try:
        pkg = ScriptPackage.model_validate({k: payload.get(k) for k in ScriptPackage.model_fields if k in payload})
    except ValidationError:
        return None, "not_a_script_package"   # an old run's record that lacks today's fields: not replayed
    writer = engine_mod.ScriptEngine()
    calls = {"writer": 0, "opening_edit": 0, "length_edit": 0, "other_edit": 0}
    sheet = {"topic_id": payload.get("topic_id") or "t", "facts": [], "sources": []}

    def write(topic, feedback):
        calls["writer"] += 1
        return pkg

    def revise(previous, findings, s):
        kind = ("opening_edit" if findings.startswith("OPENING:") else "length_edit" if findings.startswith("LENGTH:")
                else "other_edit")
        calls[kind] += 1
        if kind != "opening_edit" or mode != "fixes":
            return None
        # the edit fixes the hook and changes nothing else: the same words, marked as edited for hook_problems
        return ScriptPackage.model_validate(dict(previous, narration_script=EDITED + previous["narration_script"]))

    real_hook_problems = engine_mod.hook_problems

    def hook_problems(narration):
        return [] if (narration or "").startswith(EDITED) else real_hook_problems(narration)

    def counted(*args, **kwargs):
        calls["other_edit"] += 1

    with patch.object(engine_mod, "_ready", lambda: True), \
            patch.object(fact_evidence, "require_signing_key", lambda: None), \
            patch.object(audio_engine, "measure_spoken_seconds", lambda *a, **k: None), \
            patch.object(writer, "_sheet", lambda topic: sheet), \
            patch.object(writer, "_sheet_context", lambda s: ("", [])), \
            patch.object(writer, "_with_graphics", lambda core, *a, **k: core), \
            patch.object(writer, "_finalize", lambda p, topic, **k: {"narration_script": p.narration_script}), \
            patch.object(writer, "_load_curated", lambda tid: None), \
            patch.object(writer, "_generate_with_claude", write), \
            patch.object(writer, "_revise_surgically", revise), \
            patch.object(writer, "_edit_copy", counted), \
            patch.object(writer, "_shorten_story_slides", counted), \
            patch.object(engine_mod, "hook_problems", hook_problems):
        try:
            writer.generate_script({"id": sheet["topic_id"], "title": payload.get("title") or ""})
            outcome = "accepted"
        except engine_mod.ScriptUnavailableError:
            outcome = "no_script"
    return calls, outcome


def routing(engine_mod, scripts):
    out = {}
    for mode in ("fixes", "fails"):
        totals, outcomes, per_run = {}, {}, []
        for _, payload in scripts:
            calls, outcome = replay(engine_mod, payload, mode)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if calls is None:
                continue
            for k, v in calls.items():
                totals[k] = totals.get(k, 0) + v
            per_run.append(sum(calls.values()))
        out["opening_edit_" + mode] = {"calls": totals, "outcomes": outcomes,
                                       "heavy_calls_per_run_mean": round(sum(per_run) / len(per_run), 2) if per_run else None,
                                       "heavy_calls_per_run_max": max(per_run, default=None)}
    return out


def long_draft_with_long_hook(engine_mod, curated):
    """A draft too long when spoken whose first sentence is also over the cap: the calls it costs."""
    from src import fact_evidence
    from src.models import ScriptPackage
    long_hook, closing = BAD["thirteen_words"], curated["closing_sentence"]

    def narration(first, n):
        return first + " " + " ".join(["word"] * n) + ". " + closing

    base = {k: v for k, v in curated.items() if k in ScriptPackage.model_fields}
    draft = ScriptPackage.model_validate(dict(base, narration_script=narration(long_hook, 120), hook_sentence=long_hook))
    writer = engine_mod.ScriptEngine()
    calls = []

    def revise(previous, findings, s):
        calls.append(findings.split(":", 1)[0])
        first = FIX if findings.startswith("OPENING:") else long_hook
        return ScriptPackage.model_validate(dict(previous, narration_script=narration(first, 90), hook_sentence=first))

    def length(pkg):
        return [] if len(pkg.narration_script.split()) < 140 else [
            "Narration runs 60.0s when spoken (limit 51.5s). This draft is 150 words; cut the narration to 100-110 words."]

    sheet = {"topic_id": "t", "facts": [], "sources": []}
    with patch.object(engine_mod, "_ready", lambda: True), \
            patch.object(fact_evidence, "require_signing_key", lambda: None), \
            patch.object(engine_mod, "validate_structure", lambda pkg: []), \
            patch.object(engine_mod, "spoken_length_problems", length), \
            patch.object(writer, "_sheet", lambda topic: sheet), \
            patch.object(writer, "_sheet_context", lambda s: ("", [])), \
            patch.object(writer, "_with_graphics", lambda core, *a, **k: core), \
            patch.object(writer, "_finalize", lambda p, topic, **k: {"narration_script": p.narration_script}), \
            patch.object(writer, "_load_curated", lambda tid: None), \
            patch.object(writer, "_generate_with_claude", lambda topic, feedback: calls.append("WRITER") or draft), \
            patch.object(writer, "_revise_surgically", revise):
        try:
            out = writer.generate_script({"id": "t", "title": "T"})
            opens = engine_mod._first_sentence(out["narration_script"])
        except engine_mod.ScriptUnavailableError:
            opens = None
    return {"calls": calls, "writer_drafts": calls.count("WRITER"), "accepted_opening_words":
            len(engine_mod._spoken_words(opens)) if opens else None}


def heldout(engine):
    """Precision and recall on the committed held-out openings (tests/data/qf14_heldout_*.json): written apart from the
    52-script corpus, part A by the reviewer of round 3, part B by the builder before the round-4 lists. An opening is
    scored as the narration's first sentence."""
    out = {}
    for part in ("reviewer", "builder"):
        path = Path(__file__).resolve().parents[4] / "tests" / "data" / f"qf14_heldout_{part}.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        def admitted(s):   # passes clean: no hook finding at all
            return not engine.hook_problems(s + " The next sentence follows.")
        strong = sum(map(admitted, data["strong"]))
        weak = sum(map(admitted, data["weak"]))
        out[part] = {"strong": len(data["strong"]), "weak": len(data["weak"]), "strong_admitted": strong,
                     "weak_admitted": weak, "recall_strong": round(strong / len(data["strong"]), 3),
                     "precision_admitted": round(strong / (strong + weak), 3) if strong + weak else None,
                     "weak_refused": round(1 - weak / len(data["weak"]), 3),
                     "strong_refused": [s for s in data["strong"] if not admitted(s)],
                     "outcomes_strong": outcomes(engine, [s + " The next sentence follows." for s in data["strong"]]),
                     "outcomes_weak": outcomes(engine, [s + " The next sentence follows." for s in data["weak"]])}
    return out


def unique(paths):
    seen, out = set(), []
    for p in paths:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = " ".join((payload.get("narration_script") or "").split())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((p.parent.name, payload))
    return out


def writer_calls(run_dirs):
    """The real script_writing calls' size (llm_calls.jsonl): what one heavy writer call costs in tokens and time."""
    rows = []
    for d in run_dirs:
        for f in sorted(d.glob("run_*/llm_calls.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("task") == "script_writing" and r.get("role") == "writer" and r.get("tokens_in"):
                    rows.append(r)
    if not rows:
        return None
    return {"calls": len(rows), "tokens_in_median": median([r["tokens_in"] for r in rows]),
            "tokens_out_median": median([r.get("tokens_out") or 0 for r in rows]),
            "seconds_median": median([r.get("seconds") or 0 for r in rows])}


def main(root: Path, run_dirs: list[Path], samples: frozenset = frozenset(), judgments: Path | None = None) -> dict:
    sys.path.insert(0, str(root))
    if judgments is not None:
        JUDGED.update(json.loads(judgments.read_text(encoding="utf-8")))
    from src import config
    from src import script_engine as engine

    def admitted(text):
        return not engine.blocking_problems(engine.hook_problems(text))

    result = {
        "rule": {"hook_max_words": engine.HOOK_MAX_WORDS,
                 "signal_within_words": getattr(config, "HOOK_SIGNAL_WITHIN", "n/a"),
                 "a_hook_fault_blocks": bool(engine.blocking_problems([engine.HOOK_PROBLEM + "x"]))},
        "synthetic": {"bad_admitted": {k: admitted(v) for k, v in BAD.items()},
                      "controls_admitted": {k: admitted(v) for k, v in CONTROLS.items()},
                      # operator ruling 2026-11-10: the cap and a label block; a missing signal ships flagged
                      "bad_outcome": {k: outcome(engine, v) for k, v in BAD.items()},
                      "controls_outcome": {k: outcome(engine, v) for k, v in CONTROLS.items()}},
    }
    result["synthetic"]["bad_admitted_count"] = sum(result["synthetic"]["bad_admitted"].values())
    result["synthetic"]["controls_admitted_count"] = sum(result["synthetic"]["controls_admitted"].values())

    library = unique(sorted((root / "data" / "script_library").glob("*/verified_script.json")))
    sources = {"script_library": (library, True)}
    sampled = set()
    for i, d in enumerate(run_dirs, 1):
        sources[f"runs_{i}"] = (unique(sorted(d.glob("run_*/script_metadata.json"))), False)
        if d in samples:
            sampled.add(f"runs_{i}")
    result["corpus"] = {name: corpus(engine, scripts, quote, name not in sampled)
                        for name, (scripts, quote) in sources.items()}
    for name in sorted(sampled):
        result["corpus"][name]["per_item"] = "omitted: counts only (production sample)"
    every = [s for scripts, _ in sources.values() for s in scripts]
    seen, pooled = set(), []
    for name, payload in every:   # the same narration in two environments counts once
        key = " ".join(payload["narration_script"].split())
        if key not in seen:
            seen.add(key)
            pooled.append((name, payload))
    total = corpus(engine, pooled, False)
    result["corpus_pooled"] = {k: total[k] for k in ("scripts", "refused", "outcomes", "outcomes_strong", "outcomes_weak",
                                                     "refused_by_reason", "precision", "first_sentence_words")}

    timing = []
    for d in run_dirs:
        for p in sorted(d.glob("run_*/script_metadata.json")):
            payload = json.loads(p.read_text(encoding="utf-8"))
            t = opening_seconds(engine, p.parent, payload)
            if t:
                timing.append(t)
    result["opening_in_real_voiceovers"] = {
        "runs": len(timing),
        "first_sentence_ends_s_median": median([t["first_sentence_ends_s"] for t in timing]),
        "first_sentence_ends_s_max": max((t["first_sentence_ends_s"] for t in timing), default=None),
        "word_12_ends_s_median": median([t["word_12_ends_s"] for t in timing]),
        "runs_whose_first_sentence_ends_after_word_12": sum(t["first_sentence_ends_s"] > t["word_12_ends_s"]
                                                            for t in timing)}

    from src import audio_engine, free_llm
    curated = []
    for p in sorted((root / "data" / "scripts").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        payload = {**{k: v for k, v in d.items() if not k.startswith("_")}, "origin": "curated", "topic_id": p.stem}
        # the dry-run curated path (no fact sheet, unreviewed allowed): no checker is reached on either tree
        with patch.object(free_llm, "enabled", lambda: True), \
                patch.object(audio_engine, "measure_spoken_seconds", lambda *a, **k: None):
            passed, _, why = engine.ScriptEngine().verify_claims(payload, allow_unreviewed_curated=True)
        curated.append({"topic": p.stem, "reviewed_by_human": bool(d.get("_reviewed_by_human")),
                        "first_sentence_words": len(engine._spoken_words(engine._first_sentence(d["narration_script"]))),
                        "verify_claims_passes": passed, "refused_for_its_hook": not passed and engine.HOOK_PROBLEM in why})
    result["curated_scripts"] = curated
    bank = json.loads((root / "data" / "topics" / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    hooks = [t.get("hook") or "" for t in bank]
    result["topic_bank_suggested_hooks"] = {"topics": len(hooks), "fail_the_rule": sum(not admitted(h) for h in hooks),
                                            "note": "writer input only ('verify before using'); QF-73 rewrites them"}

    result["routing_replay"] = {"script_library": routing(engine, library),
                                **{f"runs_{i}": routing(engine, sources[f"runs_{i}"][0])
                                   for i in range(1, len(run_dirs) + 1)}}
    data = json.loads((root / "data" / "scripts" / "derinkuyu_underground_city.json").read_text(encoding="utf-8"))
    result["long_draft_with_long_hook"] = long_draft_with_long_hook(engine, data)
    result["real_writer_call_size"] = writer_calls(run_dirs)

    from main import PipelineCoordinator
    from src import script_library
    with patch.object(PipelineCoordinator, "_spoken_length_now", lambda self, saved: ""):
        c = PipelineCoordinator.__new__(PipelineCoordinator)
        c.db, c.reuse_from = None, None
        routed = []
        for entry in sorted((root / "data" / "script_library").glob("*/verified_script.json")):
            saved = json.loads(entry.read_text(encoding="utf-8"))
            with patch.object(script_library, "kept_script", lambda tid, sheet, s=saved: dict(s)):
                lib = c._script_from_library({"id": entry.parent.name})
            routed.append({"topic": entry.parent.name, "hook_fault": bool(engine.hook_problems(saved["narration_script"])),
                           "returned": lib is not None, "edit_marker": bool(lib and lib.get("_weak_hook_here")),
                           "too_long": bool(lib and lib.get("_too_long_here"))})
    result["library_path"] = {
        "entries": len(routed), "with_a_hook_fault": sum(r["hook_fault"] for r in routed),
        "routed_to_the_opening_edit": sum(r["edit_marker"] for r in routed),
        "used_as_is_with_a_hook_fault": sum(r["returned"] and r["hook_fault"] and not r["edit_marker"] for r in routed),
        # the real library path (not the generate_script replay): an entry used as it is costs no heavy call; an
        # edited one costs at least one writer edit, then the full fact checks (this environment's narrator measured
        # as fitting, so only the hook decides here)
        "heavy_calls_min": sum(r["edit_marker"] or r["too_long"] for r in routed),
        "entries_fact_checked_again": sum(r["edit_marker"] or r["too_long"] for r in routed)}
    result["heldout"] = heldout(engine)

    result["acceptance_met"] = bool(
        result["rule"]["hook_max_words"] == 12 and result["rule"]["a_hook_fault_blocks"]
        and result["synthetic"]["bad_outcome"] == RULED
        and set(result["synthetic"]["controls_outcome"].values()) == {"ships_clean"}
        and result["library_path"]["used_as_is_with_a_hook_fault"] == 0)
    return result


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    dirs, samples, judged_file, args = [], set(), None, sys.argv[2:]
    while args:
        arg = args.pop(0)
        if arg == "--judgments":
            judged_file = Path(args.pop(0)).resolve()
        elif arg == "--sample":
            dirs.append(Path(args.pop(0)).resolve())
            samples.add(dirs[-1])
        else:
            dirs.append(Path(arg).resolve())
    print(json.dumps(main(Path(sys.argv[1]).resolve(), dirs, frozenset(samples), judged_file), indent=1,
                     ensure_ascii=False))
