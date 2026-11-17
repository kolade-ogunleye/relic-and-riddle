"""QF-15 measurement: the payoff rule and the beat order. Prints JSON and writes nothing. No network, no model call, no
provider call (the offline spaCy tagger committed in data/nlp is the only model, and only on a tree that has it).

    python docs/reviews/quality-fixes-2026-11/QF-15/measure.py <repo root> [<runs dir> ...]
        [--sample <runs dir>] [--judgments <file>]

<repo root> is the code measured (the parent or this row). Committed inputs: the synthetic payoffs below, the
repository's script library (data/script_library/*/verified_script.json) and its prompts. Each optional <runs dir>
holds run_*/script_metadata.json (with words.json, llm_calls.jsonl and the master's composition.json where present):
the development output/ folder. For those, only counts, run names and sentence hashes are printed, never their text.

Production sample (decisions/PROD-SAMPLE-counts-only.md): pass a COPY of its output/ with --sample and the builder's
readings of its scripts with --judgments (a JSON file {"judged": {hash: "payoff" | "not"}, "modern_relevance":
[sentence hash, ...]}); both stay outside the repository. A --sample folder is printed as counts only: no run name,
sentence hash or text. JUDGED and MODERN_RELEVANCE below hold the library's and the development runs' readings.

Operator ruling 2026-11-10 (option C): a missing, misplaced or hook-repeating payoff blocks after its one edit; a
payoff naming none of the opening's nouns ships flagged. The outcomes below assume the one edit changes nothing.

JUDGED is the builder's reading of each real script (keyed by the sha256 of its first and penultimate sentences, first
12 hex digits): "payoff" where the penultimate sentence answers the opening or resolves its tension (an outcome, a
cause, an explanation of the opening's own subject), "not" where it is a side detail, a date, a debate or a
modern-relevance aside. It is an input, stated as such; the rule never reads it. None of these scripts was written to
the rule (no payoff_sentence), so the corpus reads each penultimate sentence as the payoff the writer would have
marked. MODERN_RELEVANCE lists, by the sha256 of the sentence, every sentence the builder read as the retired beat 4
(why the subject matters to modern science, engineering or life today).

The routing replay runs each real script, its penultimate sentence marked as its payoff, through the tree's own
generate_script with a writer that returns that same script every time (the worst case: a writer that keeps its words)
and a counting stand-in for every edit call, in two modes: the one edit fixes what it is given (the same words, marked
as fixed for the hook and payoff checks), or it fails. Spoken length is not measured (no narrator here).
"""

import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from unittest.mock import patch

EDITED = "⁠"   # the replay's mark of a narration its stand-in edit fixed (a word joiner, never spoken)
HOOK = "In Cappadocia, a house renovation in 1963 revealed an underground tunnel."
# payoffs that must be refused (the hook above as the opening), and the controls that must pass
BAD = {
    "missing": None,
    "not_penultimate": "OTHER",
    "repeats_the_hook": HOOK,
    "shared_verb_only": "Later families revealed nothing about the builders.",
    "shared_adjective_only": "Underground air stayed cool all year.",
    "shared_number_only": "By 1963 the villagers had moved away.",
}
CONTROLS = {
    "same_noun": "Byzantine-era Christians later widened the tunnel to hide from raids.",
    "plural_inflection": "Its earliest tunnels may be nearly three thousand years old.",
    "noun_tagged_as_adjective_in_the_hook": "MODERN",   # review 2, finding 2 (the same tagging, a synthetic pair)
}
# the tagger reads "concrete" in the hook as an adjective ("crumbles" as a noun) and in the payoff as a noun
MODERN_HOOK = "Modern concrete crumbles fast, yet a sunken Roman pier still holds firm."
MODERN_PAYOFF = "Volcanic ash let tiny crystals grow through the cured concrete."
FILLER = "The evidence explains the mechanism beneath the plain."
CLOSING = "That is where the story began."

JUDGED = {
    "c60f79d9467a": "payoff", "5a8c01d6c651": "not", "7ce7591cd33e": "not", "2c13932c73e4": "not",
    "b026614067ec": "payoff", "ca2d1490b4cd": "payoff", "114b4c921504": "payoff", "f64378ef9147": "payoff",
    "0b43c8ee6dcd": "not", "7bca48eec1a0": "not", "fa6a8dcc18bd": "not", "ab10f8f869c2": "not",
    "2c5a0404cca7": "not", "e79e6188692a": "payoff", "5f04f97a5224": "not", "8f07052c84f2": "not",
    "788665c2fd5a": "payoff", "1f3a7aa1a341": "not", "db71ef2c5ab8": "not", "d97c695c9750": "not",
    "f57e8327b09f": "not", "9ce6641bae74": "not", "11eda958ba95": "not", "340e62c66f88": "not",
    "8dd131ec7812": "not", "538a5c3fe129": "payoff", "4e7dd922d35a": "payoff", "2629ed04cc71": "not",
}
MODERN_RELEVANCE = {"074be588b288", "67368b43ee0e", "9edaedc1dc5e"}


def sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:12]


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x.strip()]


# what the operator's ruling (decisions/QF-15-payoff-rule.md, option C) makes of each bad payoff when its one edit fails
RULED = {"missing": "blocked", "not_penultimate": "blocked", "repeats_the_hook": "blocked",
         "shared_verb_only": "ships_flagged", "shared_adjective_only": "ships_flagged", "shared_number_only": "ships_flagged"}


def outcome(engine, narration, payoff):
    """What the tree does with this payoff if its one edit changes nothing (the worst case): "blocked" (a blocking
    payoff fault), "ships_flagged" (only the noun-link finding: the review card's flag) or "ships_clean" (on the
    parent, every payoff: it has no check)."""
    check = checker(engine)
    faults = check(narration, payoff) if check else []
    return "blocked" if engine.blocking_problems(faults) else "ships_flagged" if faults else "ships_clean"


def with_hook(engine, narration, payoff):
    """The same with QF-14's hook rule as well: blocked when either blocks, flagged when either flags."""
    hook = engine.hook_problems(narration)
    mine = outcome(engine, narration, payoff)
    if mine == "blocked" or engine.blocking_problems(hook):
        return "blocked"
    return "ships_flagged" if mine == "ships_flagged" or hook else "ships_clean"


def checker(engine):
    """The tree's payoff check as (narration, payoff) -> faults, or None where the tree has none (the parent)."""
    return getattr(engine, "payoff_problems", None)


def strict_nouns(root: Path):
    """Round 2's rule (a noun in both sentences), for the comparison; None where the tree has no tagger."""
    try:
        from src import narrative_payoff
    except ImportError:
        return None
    return lambda hook, payoff: bool(narrative_payoff.nouns(hook) & narrative_payoff.nouns(payoff))


def synthetic(engine):
    check = checker(engine)

    def narration(payoff, hook=HOOK):
        return f"{hook} {FILLER} {payoff} {CLOSING}"
    bad, controls, ruled = {}, {}, {}
    for name, payoff in BAD.items():
        text = narration(payoff or FILLER) if name != "not_penultimate" else narration(CONTROLS["same_noun"])
        marked = "" if payoff is None else (FILLER if name == "not_penultimate" else payoff)
        bad[name] = True if check is None else not check(text, marked)
        ruled[name] = outcome(engine, text, marked)
    for name, payoff in CONTROLS.items():
        hook, payoff = (MODERN_HOOK, MODERN_PAYOFF) if payoff == "MODERN" else (HOOK, payoff)
        controls[name] = True if check is None else not check(narration(payoff, hook), payoff)
    return {"bad_admitted": bad, "bad_admitted_count": sum(bad.values()), "bad_outcome": ruled,
            "controls_admitted": controls, "controls_admitted_count": sum(controls.values())}


def unique(paths):
    seen, out = set(), []
    for p in paths:
        payload = json.loads(p.read_text(encoding="utf-8"))
        key = " ".join((payload.get("narration_script") or "").split())
        if key and key not in seen:
            seen.add(key)
            out.append((p.parent.name, p.parent, payload))
    return out


def corpus(engine, strict, scripts, quote, per_item=True):
    """The penultimate sentence as the marked payoff, against the builder's reading: the confusion table (the false
    rejects and accepts per item, or as counts when per_item is False)."""
    check = checker(engine)
    table = {"payoff_admitted": 0, "payoff_refused": 0, "not_admitted": 0, "not_refused": 0, "unjudged": 0}
    strict_table = dict(table)
    rows = []
    for name, _, payload in scripts:
        s = sentences(payload.get("narration_script"))
        mark = JUDGED.get(sha(s[0] + "\n" + s[-2]))
        admitted = True if check is None else not check(" ".join(s), s[-2])
        row = {"run": name, "sha": sha(s[0] + "\n" + s[-2]), "mark": mark, "admitted": admitted,
               "outcome": outcome(engine, " ".join(s), s[-2]),
               "outcome_with_the_hook_rule": with_hook(engine, payload.get("narration_script") or "", s[-2])}
        if quote:
            row["hook"], row["payoff"] = s[0], s[-2]
        rows.append(row)
        for t, ok in ((table, admitted), (strict_table, strict(s[0], s[-2]) if strict else None)):
            if ok is None:
                continue
            t["unjudged" if mark is None else f"{mark}_{'admitted' if ok else 'refused'}"] += 1
    def tally(key, mark=None):
        out = {"blocked": 0, "ships_flagged": 0, "ships_clean": 0}
        for r in rows:
            if mark is None or r["mark"] == mark:
                out[r[key]] += 1
        return out
    out = {"scripts": len(scripts), "admitted": sum(r["admitted"] for r in rows), "confusion": table,
           "outcomes": tally("outcome"), "outcomes_judged_payoff": tally("outcome", "payoff"),
           "outcomes_judged_not": tally("outcome", "not"),
           "outcomes_with_the_hook_rule": tally("outcome_with_the_hook_rule"),
           "false_rejects": [r for r in rows if r["mark"] == "payoff" and not r["admitted"]],
           "false_accepts": [r for r in rows if r["mark"] == "not" and r["admitted"]]}
    if not per_item:
        out.update(false_rejects=len(out["false_rejects"]), false_accepts=len(out["false_accepts"]),
                   per_item="omitted: counts only (production sample)")
    if strict:
        out["confusion_round2_strict_nouns"] = strict_table
    return out


def rates(table):
    payoff = table["payoff_admitted"] + table["payoff_refused"]
    not_ = table["not_admitted"] + table["not_refused"]
    admitted = table["payoff_admitted"] + table["not_admitted"]
    return {"recall_of_judged_payoffs": round(table["payoff_admitted"] / payoff, 2) if payoff else None,
            "refusal_of_judged_non_payoffs": round(table["not_refused"] / not_, 2) if not_ else None,
            "precision_of_what_is_admitted": round(table["payoff_admitted"] / admitted, 2) if admitted else None}


def beats(engine, scripts, samples=frozenset()):
    """Sentences and composed scenes per real script, and the retired modern-relevance beat's share of the voice. Runs
    of a --sample folder are counted in "where_in_sample", never named."""
    counts, scenes, seconds, shares, carrying = [], [], [], [], []
    for name, run_dir, payload in scripts:
        s = sentences(payload.get("narration_script"))
        counts.append(len(s))
        modern = [i for i, x in enumerate(s) if sha(x) in MODERN_RELEVANCE]
        if modern:
            carrying.append({"run": name, "sentence_index": modern, "of": len(s),
                             "sample": run_dir.parent.resolve() in samples})
        for comp in sorted(run_dir.glob("*_master.composition.json")):
            scenes.append(len(json.loads(comp.read_text(encoding="utf-8")).get("scenes") or []))
            break
        try:
            timed = json.loads((run_dir / "words.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not modern or not timed:
            continue
        # word i of the narration is word i of words.json (QF-14's opening timing reads it the same way)
        spans, at = [], 0
        for x in s:
            n = len(engine._spoken_words(x))
            spans.append((at, at + n))
            at += n
        if at != len(timed):
            continue
        total = timed[-1]["start"] + timed[-1]["dur"] - timed[0]["start"]
        for i in modern:
            a, b = spans[i]
            took = timed[b - 1]["start"] + timed[b - 1]["dur"] - timed[a]["start"]
            seconds.append(round(took, 2))
            shares.append(round(took / total, 3))
    return {"scripts": len(scripts),
            "sentences_per_script": {"min": min(counts, default=None), "median": median(counts),
                                     "max": max(counts, default=None)},
            "composed_scenes_per_run": {"runs": len(scenes), "min": min(scenes, default=None),
                                        "median": median(scenes), "max": max(scenes, default=None)},
            "scripts_with_a_modern_relevance_sentence": len(carrying),
            "where": [{k: v for k, v in c.items() if k != "sample"} for c in carrying if not c["sample"]],
            "where_in_sample": sum(c["sample"] for c in carrying),
            "modern_relevance_spoken_s": {"timed": len(seconds), "median": median(seconds),
                                          "max": max(seconds, default=None)},
            "modern_relevance_share_of_the_voice": {"median": median(shares), "max": max(shares, default=None)}}


def replay(engine_mod, payload, mode: str):
    """Calls one writer run makes for this script, its penultimate sentence marked as its payoff, and how it ends."""
    from pydantic import ValidationError

    from src import audio_engine, fact_evidence
    from src.models import ScriptPackage
    s = sentences(payload.get("narration_script"))
    marked = dict(payload, payoff_sentence=s[-2])
    try:
        pkg = ScriptPackage.model_validate({k: marked.get(k) for k in ScriptPackage.model_fields if k in marked})
    except ValidationError:
        return None, "not_a_script_package"
    writer = engine_mod.ScriptEngine()
    calls = {"writer": 0, "opening_or_payoff_edit": 0, "length_edit": 0, "other_edit": 0}
    sheet = {"topic_id": payload.get("topic_id") or "t", "facts": [], "sources": []}

    def write(topic, feedback):
        calls["writer"] += 1
        return pkg

    def revise(previous, findings, s):
        kind = ("opening_or_payoff_edit" if findings.startswith(("OPENING:", "PAYOFF:"))
                else "length_edit" if findings.startswith("LENGTH:") else "other_edit")
        calls[kind] += 1
        if kind != "opening_or_payoff_edit" or mode != "fixes":
            return None
        return ScriptPackage.model_validate(dict(previous, narration_script=EDITED + previous["narration_script"]))

    real_hook, real_payoff = engine_mod.hook_problems, checker(engine_mod)

    def hook_problems(narration):
        return [] if (narration or "").startswith(EDITED) else real_hook(narration)

    def payoff_problems(narration, payoff):
        return [] if (narration or "").startswith(EDITED) else real_payoff(narration, payoff)

    def counted(*args, **kwargs):
        calls["other_edit"] += 1

    patches = [patch.object(engine_mod, "_ready", lambda: True),
               patch.object(fact_evidence, "require_signing_key", lambda: None),
               patch.object(audio_engine, "measure_spoken_seconds", lambda *a, **k: None),
               patch.object(writer, "_sheet", lambda topic: sheet),
               patch.object(writer, "_sheet_context", lambda s: ("", [])),
               patch.object(writer, "_with_graphics", lambda core, *a, **k: core),
               patch.object(writer, "_finalize", lambda p, topic, **k: {"narration_script": p.narration_script}),
               patch.object(writer, "_load_curated", lambda tid: None),
               patch.object(writer, "_generate_with_claude", write),
               patch.object(writer, "_revise_surgically", revise),
               patch.object(writer, "_edit_copy", counted),
               patch.object(writer, "_shorten_story_slides", counted),
               patch.object(engine_mod, "hook_problems", hook_problems)]
    if real_payoff is not None:
        patches.append(patch.object(engine_mod, "payoff_problems", payoff_problems))
    for p in patches:
        p.start()
    try:
        writer.generate_script({"id": sheet["topic_id"], "title": payload.get("title") or ""})
        outcome = "accepted"
    except engine_mod.ScriptUnavailableError:
        outcome = "no_script"
    finally:
        for p in reversed(patches):
            p.stop()
    return calls, outcome


def routing(engine_mod, scripts):
    out = {}
    for mode in ("fixes", "fails"):
        totals, outcomes, per_run = {}, {}, []
        for _, _, payload in scripts:
            calls, outcome = replay(engine_mod, payload, mode)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if calls is None:
                continue
            for k, v in calls.items():
                totals[k] = totals.get(k, 0) + v
            per_run.append(sum(calls.values()))
        out["edit_" + mode] = {"calls": totals, "outcomes": outcomes,
                               "heavy_calls_per_run_mean": round(sum(per_run) / len(per_run), 2) if per_run else None,
                               "heavy_calls_per_run_max": max(per_run, default=None)}
    return out


def writer_calls(run_dirs):
    """The real heavy writer calls' size (llm_calls.jsonl): what one call costs in tokens and time."""
    rows = []
    for d in run_dirs:
        for f in sorted(d.glob("run_*/llm_calls.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                r = json.loads(line) if line.strip() else {}
                if r.get("task") == "script_writing" and r.get("role") == "writer" and r.get("tokens_in"):
                    rows.append(r)
    if not rows:
        return None
    return {"calls": len(rows), "tokens_in_median": median([r["tokens_in"] for r in rows]),
            "tokens_out_median": median([r.get("tokens_out") or 0 for r in rows]),
            "seconds_median": median([r.get("seconds") or 0 for r in rows])}


def prompt_size(engine):
    """The writer's fixed instructions, in characters (the only per-call size this row changes)."""
    from src.models import ScriptCore
    return {"system_prompt_chars": len(engine.SYSTEM_PROMPT),
            "verified_user_template_chars": len(engine.VERIFIED_USER_PROMPT_TEMPLATE),
            "revise_system_chars": len(engine.REVISE_SYSTEM),
            "writer_schema_chars": len(json.dumps(ScriptCore.model_json_schema())),
            "writer_schema_requires_payoff": "payoff_sentence" in ScriptCore.model_json_schema().get("required", [])}


def library_path(root: Path, engine):
    from main import PipelineCoordinator
    from src import script_library
    check = checker(engine)
    with patch.object(PipelineCoordinator, "_spoken_length_now", lambda self, saved: ""):
        c = PipelineCoordinator.__new__(PipelineCoordinator)
        c.db, c.reuse_from = None, None
        rows = []
        for entry in sorted((root / "data" / "script_library").glob("*/verified_script.json")):
            saved = json.loads(entry.read_text(encoding="utf-8"))
            with patch.object(script_library, "kept_script", lambda tid, sheet, s=saved: dict(s)):
                lib = c._script_from_library({"id": entry.parent.name})
            fault = (bool(check(saved["narration_script"], saved.get("payoff_sentence") or ""))
                     if check else True)   # the parent has no check: every entry lacks a payoff the rule accepts
            notes = (lib or {}).get("_weak_hook_here") or ""
            rows.append({"topic": entry.parent.name, "payoff_fault": fault, "has_payoff_sentence":
                         bool(saved.get("payoff_sentence")), "routed_to_the_edit": "PAYOFF:" in notes,
                         "edited_at_all": bool(notes)})
    return {"entries": len(rows), "with_a_payoff_fault": sum(r["payoff_fault"] for r in rows),
            "with_payoff_sentence": sum(r["has_payoff_sentence"] for r in rows),
            "routed_to_the_payoff_edit": sum(r["routed_to_the_edit"] for r in rows),
            "with_a_payoff_fault_and_no_payoff_edit": sum(r["payoff_fault"] and not r["routed_to_the_edit"] for r in rows),
            "edited_before_use": sum(r["edited_at_all"] for r in rows)}


def main(root: Path, run_dirs: list[Path], samples: frozenset = frozenset(), judgments: Path | None = None) -> dict:
    sys.path.insert(0, str(root))
    if judgments is not None:
        extra = json.loads(judgments.read_text(encoding="utf-8"))
        JUDGED.update(extra.get("judged") or {})
        MODERN_RELEVANCE.update(extra.get("modern_relevance") or [])
    from src import script_engine as engine
    strict = strict_nouns(root)
    result = {"rule": {"has_payoff_check": checker(engine) is not None,
                       "a_payoff_fault_blocks": bool(checker(engine) and engine.blocking_problems(
                           [engine.PAYOFF_PROBLEM + "x"])),
                       "modern_relevance_beat_in_prompt": "Modern Relevance" in engine.SYSTEM_PROMPT,
                       "story_order_in_prompt": "hook -> stake -> two or three concrete reveals -> payoff -> loop"
                       in engine.SYSTEM_PROMPT},
              "synthetic": synthetic(engine)}
    library = unique(sorted((root / "data" / "script_library").glob("*/verified_script.json")))
    sources = {"script_library": (library, True)}
    sampled = set()
    for i, d in enumerate(run_dirs, 1):
        sources[f"runs_{i}"] = (unique(sorted(d.glob("run_*/script_metadata.json"))), False)
        if d in samples:
            sampled.add(f"runs_{i}")
    result["corpus"] = {name: corpus(engine, strict, s, quote, name not in sampled)
                        for name, (s, quote) in sources.items()}
    seen, pooled = set(), []
    for scripts, _ in sources.values():   # the same narration in two environments counts once
        for row in scripts:
            key = " ".join(row[2]["narration_script"].split())
            if key not in seen:
                seen.add(key)
                pooled.append(row)
    total = corpus(engine, strict, pooled, False)
    result["corpus_pooled"] = {"scripts": total["scripts"], "admitted": total["admitted"],
                               **{k: total[k] for k in ("outcomes", "outcomes_judged_payoff", "outcomes_judged_not",
                                                        "outcomes_with_the_hook_rule")},
                               "confusion": total["confusion"], "rates": rates(total["confusion"]),
                               "false_rejects": len(total["false_rejects"]), "false_accepts": len(total["false_accepts"])}
    if strict:
        result["corpus_pooled"]["confusion_round2_strict_nouns"] = total["confusion_round2_strict_nouns"]
        result["corpus_pooled"]["rates_round2_strict_nouns"] = rates(total["confusion_round2_strict_nouns"])
    result["beats"] = beats(engine, pooled, frozenset(samples))
    result["library_path"] = library_path(root, engine)
    result["routing_replay"] = {name: routing(engine, s) for name, (s, _) in sources.items()}
    result["real_writer_call_size"] = writer_calls(run_dirs)
    result["prompt_size"] = prompt_size(engine)
    syn = result["synthetic"]
    result["acceptance_met"] = bool(
        result["rule"]["a_payoff_fault_blocks"] and syn["bad_admitted_count"] == 0 and syn["bad_outcome"] == RULED
        and syn["controls_admitted_count"] == len(CONTROLS)
        and not result["rule"]["modern_relevance_beat_in_prompt"] and result["rule"]["story_order_in_prompt"]
        and result["library_path"]["with_a_payoff_fault_and_no_payoff_edit"] == 0)
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
