"""QF-16 measurement: the hedge rule. Prints JSON and writes nothing. No network, no model call, no provider call.

    python docs/reviews/quality-fixes-2026-11/QF-16/measure.py <repo root> [<runs dir> ...]
        [--sample <runs dir>] [--judgments <file>]

<repo root> is the code measured (the parent or this row); it runs on both. Committed inputs: the synthetic cases
below, the repository's fact sheets (data/fact_sheets, read through the tree's own fact_sheet.load) and its script
library (data/script_library/*/verified_script.json). Each optional <runs dir> holds run_*/script_metadata.json (with
words.json and *_captions.ass where present): the development output/ folder. For those, only counts, run names and
sentence hashes are printed, never their text.

Production sample (decisions/PROD-SAMPLE-counts-only.md): pass a COPY of its output/ with --sample and the builder's
readings of its flagged sentences with --judgments (a JSON file {"judged": {sentence hash: "justified" | "false"}});
both stay outside the repository. A --sample folder is printed as counts only. Production renders from the same
committed fact sheets (script_library.seed_fact_sheet ships them), so each script is read against its topic's sheet.

Operator ruling 2026-11-11 (decisions/QF-16-question.md, under delegation):
1 (B): the automatic hedge takes only a weak date or measured quantity (a number with a unit), said as its quoted
  range when the evidence has one; a weak count or event figure is not hedged automatically. Cached sheets are re-checked
  in place on load (no RULES_VERSION bump).
2 (P3): in the opening, payoff (penultimate) and final loop sentence, a disputed fact of the run's sheet blocks after
  the one shared repair edit; any other qualifier there gets that edit and then ships flagged. Questions are exempt.
3: body qualifiers are neither blocked nor flagged; one quantity matcher serves every marker.

JUDGED is the builder's reading of each sentence the rule blocks or flags (sha256 of the sentence, first 12 hex):
"justified" where the sentence does carry a hedge or a disputed claim the close should not rest on, "false" where it
does not (the rule's false hit). It is an input, stated as such; the rule never reads it. The outcomes assume the one
edit changes nothing (the worst case). The routing replay runs each real script through the tree's own
generate_script with a writer that returns that same script every time and a counting stand-in for every edit call,
the edit either fixing what it is given or failing (as QF-14 and QF-15 measure).
"""

import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from unittest.mock import patch

EDITED = "⁠"   # the replay's mark of a narration its stand-in edit fixed (a word joiner, never spoken)
JUDGED = {   # the library's and the development runs' flagged or blocked close sentences (all read as real qualifiers)
    "ee684c37213d": "justified", "dbff01c80271": "justified", "fb080ba5ca1c": "justified",
    "f88839197c59": "justified", "cff70796b8fe": "justified",
}

# A small sheet for the synthetic cases: F1 disputed (a measured figure with its hedge), F2 undisputed.
SHEET = {"topic_id": "t", "facts": [
    {"id": "F1", "kind": "number", "disputed": True, "statement": "The shaft is 280 feet (85 m) deep.",
     "hedge": "by one estimate, the shaft is 280 feet (85 m) deep.", "evidence": []},
    {"id": "F2", "kind": "date", "disputed": False, "statement": "The shaft was found in 1963.", "hedge": "",
     "evidence": []},
    {"id": "F3", "kind": "number", "disputed": True, "statement": "It holds 144 chambers.",
     "hedge": "an estimated 144 chambers", "evidence": []}]}
BODY = "Workers cut the rock by hand. Each level has its own air shaft."
OPEN = "A farmer found a hidden shaft in 1963."
PAY = "The shaft was found in 1963 by a farmer."
LOOP = "And it still keeps its secret."
# (case, narration, the ruling's outcome if the one edit changes nothing)
CASES = [
    ("auto_lead_in_the_opening", f"By one estimate, a shaft drops 280 feet. {BODY} {PAY} {LOOP}", "blocked"),
    ("disputed_figure_in_the_payoff", f"{OPEN} {BODY} The shaft drops 280 feet into the rock. {LOOP}", "blocked"),
    ("disputed_metric_figure_in_the_loop", f"{OPEN} {BODY} {PAY} It drops 85 m.", "blocked"),
    ("disputed_figure_spelled_out", f"{OPEN} {BODY} {PAY} One hundred forty-four chambers wait below.", "blocked"),
    ("qualifier_in_the_opening", f"A shaft drops roughly 300 feet below a farm. {BODY} {PAY} {LOOP}", "flagged"),
    ("uncertain_claim_in_the_loop", f"{OPEN} {BODY} {PAY} It was most likely a refuge.", "flagged"),
    ("evidential_in_the_payoff", f"{OPEN} {BODY} Soot on the walls suggests long use. {LOOP}", "flagged"),
    ("control_question_in_the_loop", f"{OPEN} {BODY} {PAY} Could it have held a whole town?", "clean"),
    ("control_question_with_a_disputed_figure", f"Can a shaft drop 280 feet by hand? {BODY} {PAY} {LOOP}", "clean"),
    ("control_undisputed_figure", f"{OPEN} {BODY} {PAY} {LOOP}", "clean"),
    ("control_body_hedges", f"{OPEN} By one estimate, the shaft drops 280 feet. It was probably a refuge. {PAY} {LOOP}",
     "clean"),
    ("control_spatial_and_topical",
     f"Farms spread around the hidden shaft from 1963. {BODY} {PAY} Some still stand about the valley.", "clean"),
]
# The one quantity matcher (finding 4): each qualifier with its quantity in words, digits or a multiple.
QUANTITATIVE = [
    "By one estimate, scans reveal thirty-five bronze gears.",
    "By one estimate, the dome is one hundred forty-four feet across.",
    "By one estimate, a crew of two hundred fifty cut it.",
    "Its grains measure roughly twice as wide.",
    "Up to 20,000 people sheltered here.",
    "The walls are some two millennia old.",
]
# The automatic hedge's scope (ruling B): (case, statement, kind, quote, hedged automatically?)
AUTO = [
    ("weak_count", "Miners carved four chapels in the rock.", "number", "Miners carved four chapels in the rock.", False),
    ("weak_count_in_digits", "Visitors left 107 inscriptions.", "number", "Visitors left 107 inscriptions.", False),
    ("weak_measured_quantity", "The city reached 85 meters deep.", "number", "The city reached 85 meters deep.", True),
    ("weak_mass", "200,000 tons of rock", "number", "over 200,000 tons of rock", True),
    ("weak_date", "It was rediscovered in 1963.", "date", "It was rediscovered in 1963.", True),
    ("weak_range", "The trenches are 10 to 15 centimeters deep.", "number",
     "The trenches are 10 to 15 centimeters deep.", True),
    ("weak_figure_quoted_as_a_range", "The city reached 85 meters deep.", "number",
     "The city reached between 60 and 85 meters deep.", True),
]


def sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:12]


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if x.strip()]


def check(engine):
    return getattr(engine, "hedge_problems", None)


def outcome(engine, narration, sheet):
    rule = check(engine)
    faults = rule(narration, sheet) if rule else []
    return ("blocked" if engine.blocking_problems(faults) else "flagged" if faults else "clean"), faults


def synthetic(engine, fact_sheet):
    from src.models import DraftFact
    cases = {name: outcome(engine, text, SHEET)[0] for name, text, _ in CASES}
    quantitative = None
    try:
        from src import narrative_hedges
        quantitative = sum(all(q for _, q in narrative_hedges.markers(s)) and bool(narrative_hedges.markers(s))
                           for s in QUANTITATIVE)
    except ImportError:
        pass
    auto = {}
    for name, statement, kind, quote, _ in AUTO:
        kept, _ = fact_sheet._apply_rules(
            [DraftFact(id="F1", statement=statement, kind=kind, quote_ids=[1], disputed=False, hedge="")],
            [{"url": "https://travel-blog.example/x", "quote": quote}], auto_hedge=True)
        auto[name] = {"kept": bool(kept), "hedge": kept[0]["hedge"] if kept else ""}
    return {"close_cases": cases, "close_cases_as_ruled": sum(cases[n] == want for n, _, want in CASES),
            "of": len(CASES), "quantitative_qualifiers_read_as_quantitative": quantitative, "of_quantitative": len(QUANTITATIVE),
            "auto_hedge": auto, "auto_hedge_as_ruled": sum(auto[n]["kept"] == want for n, *_, want in AUTO),
            "of_auto": len(AUTO)}


def sheet_effects(root: Path, fact_sheet):
    """What the tree's own load() makes of every committed sheet: facts kept, automatic hedges, and sheets that would
    be researched again on next use (load returns None)."""
    raw_facts = kept = auto_before = auto_after = rebuilt = ranges_said = stale = 0
    per_sheet, dropped_kinds = [], {}
    for p in sorted((root / "data" / "fact_sheets").glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw_facts += len(raw["facts"])
        stale += raw.get("rules_version") != fact_sheet.RULES_VERSION
        auto_before += sum(bool(f.get("disputed")) and (f.get("hedge") or "").lower().startswith(
            ("by one estimate", "by one account")) for f in raw["facts"])
        with patch.object(fact_sheet, "SHEETS_DIR", root / "data" / "fact_sheets"):
            sheet = fact_sheet.load(raw["topic_id"], max_age_days=100000)
        if sheet is None:
            rebuilt += 1
            continue
        kept += len(sheet["facts"])
        per_sheet.append(len(sheet["facts"]))
        auto_after += sum(bool(f.get("disputed")) and (f.get("hedge") or "").lower().startswith(
            ("by one estimate", "by one account")) for f in sheet["facts"])
        before = {f["id"]: f for f in raw["facts"]}
        for f in sheet["facts"]:
            if f.get("hedge") != before.get(f["id"], {}).get("hedge") and "between" in (f.get("hedge") or ""):
                ranges_said += 1
        for fid in set(before) - {f["id"] for f in sheet["facts"]}:
            k = before[fid].get("kind", "")
            dropped_kinds[k] = dropped_kinds.get(k, 0) + 1
    return {"sheets": len(per_sheet) + rebuilt, "researched_again_on_next_use": rebuilt,
            "built_under_older_rules": stale, "facts_stored": raw_facts,
            "facts_loaded": kept, "automatic_hedges_stored": auto_before, "automatic_hedges_loaded": auto_after,
            "dropped_on_load_by_kind": dropped_kinds, "hedges_now_saying_their_quoted_range": ranges_said,
            "facts_per_loaded_sheet": {"min": min(per_sheet, default=None), "median": median(per_sheet)},
            "loaded_sheets_below_minimum": sum(n < fact_sheet.MIN_VERIFIED_FACTS for n in per_sheet)}


def unique(paths):
    seen, out = set(), []
    for p in paths:
        payload = json.loads(p.read_text(encoding="utf-8"))
        key = " ".join((payload.get("narration_script") or "").split())
        if key and key not in seen:
            seen.add(key)
            out.append((p.parent.name, p.parent, payload))
    return out


def sheet_for(fact_sheet, root, payload, cache):
    tid = payload.get("topic_id") or ""
    if tid not in cache:
        with patch.object(fact_sheet, "SHEETS_DIR", root / "data" / "fact_sheets"):
            cache[tid] = fact_sheet.load(tid, max_age_days=100000) if tid else None
    return cache[tid]


def corpus(engine, fact_sheet, root, scripts, per_item, cache):
    """Each real script's outcome under the hedge rule (if the one edit changes nothing), the hits by position and
    kind against the builder's reading, the body qualifiers (never read by the rule) and, with QF-14's and QF-15's
    rules too (the penultimate sentence marked as the payoff), the combined outcome."""
    try:
        from src import narrative_hedges
    except ImportError:
        narrative_hedges = None
    out = {"scripts": len(scripts), "outcomes": {"blocked": 0, "flagged": 0, "clean": 0},
           "outcomes_with_hook_and_payoff_rules": {"blocked": 0, "flagged": 0, "clean": 0},
           "hits": {}, "marks": {"justified": 0, "false": 0, "unjudged": 0},
           "body_qualifiers": {"sentences": 0, "quantitative": 0, "not_quantitative": 0, "refused": 0},
           "close_sentences_with_auto_lead": 0}
    rows = []
    for name, run_dir, payload in scripts:
        narration = payload.get("narration_script") or ""
        sheet = sheet_for(fact_sheet, root, payload, cache)
        result, faults = outcome(engine, narration, sheet)
        out["outcomes"][result] += 1
        s = sentences(narration)
        hook = engine.hook_problems(narration)
        pay = engine.payoff_problems(narration, s[-2]) if hasattr(engine, "payoff_problems") and len(s) >= 3 else []
        allf = hook + pay + faults
        combined = "blocked" if engine.blocking_problems(allf) else "flagged" if allf else "clean"
        out["outcomes_with_hook_and_payoff_rules"][combined] += 1
        for f in faults:
            m = re.search(r"the (opening|payoff|closing loop) ", f)
            kind = "blocks" if engine.blocking_problems([f]) else "flags"
            key = f"{m.group(1) if m else '?'}_{kind}"
            out["hits"][key] = out["hits"].get(key, 0) + 1
            where = {"opening": 0, "payoff": len(s) - 2, "closing loop": len(s) - 1}.get(m.group(1) if m else "", 0)
            h = sha(s[where])
            mark = JUDGED.get(h)
            out["marks"][mark or "unjudged"] += 1
            rows.append({"run": name, "sha": h, "where": m.group(1) if m else "?", "kind": kind, "mark": mark})
        for i, x in enumerate(s):
            if i in (0, len(s) - 2, len(s) - 1):
                if re.search(r"\bby one (estimate|account)\b", x, re.IGNORECASE):
                    out["close_sentences_with_auto_lead"] += 1
                continue
            if narrative_hedges is not None and (found := narrative_hedges.markers(x)):
                out["body_qualifiers"]["sentences"] += 1
                out["body_qualifiers"]["quantitative" if all(q for _, q in found) else "not_quantitative"] += 1
    if per_item:
        out["per_item"] = rows
    else:
        out["per_item"] = "omitted: counts only (production sample)"
    out["_rows"] = rows
    return out


def replay(engine_mod, payload, sheet, mode: str):
    """Calls one writer run makes for this script, its penultimate sentence marked as its payoff, and how it ends."""
    from pydantic import ValidationError

    from src import audio_engine, fact_evidence
    from src.models import ScriptPackage
    s = sentences(payload.get("narration_script"))
    marked = dict(payload, payoff_sentence=s[-2] if len(s) >= 3 else "")
    try:
        pkg = ScriptPackage.model_validate({k: marked.get(k) for k in ScriptPackage.model_fields if k in marked})
    except ValidationError:
        return None, "not_a_script_package"
    writer = engine_mod.ScriptEngine()
    calls = {"writer": 0, "opening_payoff_or_hedge_edit": 0, "length_edit": 0, "other_edit": 0}
    stub = {"topic_id": payload.get("topic_id") or "t", "facts": [], "sources": []}

    def write(topic, feedback):
        calls["writer"] += 1
        return pkg

    def revise(previous, findings, s):
        kind = ("opening_payoff_or_hedge_edit" if findings.startswith(("OPENING:", "PAYOFF:", "HEDGE:"))
                else "length_edit" if findings.startswith("LENGTH:") else "other_edit")
        calls[kind] += 1
        if kind != "opening_payoff_or_hedge_edit" or mode != "fixes":
            return None
        return ScriptPackage.model_validate(dict(previous, narration_script=EDITED + previous["narration_script"]))

    real_hook, real_payoff, real_hedge = engine_mod.hook_problems, getattr(engine_mod, "payoff_problems", None), check(engine_mod)

    def hook_problems(narration):
        return [] if (narration or "").startswith(EDITED) else real_hook(narration)

    def payoff_problems(narration, payoff):
        return [] if (narration or "").startswith(EDITED) else real_payoff(narration, payoff)

    def hedge_problems(narration, *a, **k):
        return [] if (narration or "").startswith(EDITED) else real_hedge(narration, sheet)

    def counted(*args, **kwargs):
        calls["other_edit"] += 1

    patches = [patch.object(engine_mod, "_ready", lambda: True),
               patch.object(fact_evidence, "require_signing_key", lambda: None),
               patch.object(audio_engine, "measure_spoken_seconds", lambda *a, **k: None),
               patch.object(writer, "_sheet", lambda topic: stub),
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
    if real_hedge is not None:
        patches.append(patch.object(engine_mod, "hedge_problems", hedge_problems))
    for p in patches:
        p.start()
    try:
        writer.generate_script({"id": stub["topic_id"], "title": payload.get("title") or ""})
        result = "accepted"
    except engine_mod.ScriptUnavailableError:
        result = "no_script"
    finally:
        for p in reversed(patches):
            p.stop()
    return calls, result


def routing(engine_mod, fact_sheet, root, scripts, cache):
    out = {}
    for mode in ("fixes", "fails"):
        totals, outcomes, per_run = {}, {}, []
        for _, _, payload in scripts:
            calls, result = replay(engine_mod, payload, sheet_for(fact_sheet, root, payload, cache), mode)
            outcomes[result] = outcomes.get(result, 0) + 1
            if calls is None:
                continue
            for k, v in calls.items():
                totals[k] = totals.get(k, 0) + v
            per_run.append(sum(calls.values()))
        out["edit_" + mode] = {"calls": totals, "outcomes": outcomes,
                               "heavy_calls_per_run_mean": round(sum(per_run) / len(per_run), 2) if per_run else None,
                               "heavy_calls_per_run_max": max(per_run, default=None)}
    return out


def _ass_time(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def bursts(ass: Path) -> list[float]:
    """Caption bursts' rates (characters, spaces included, over the plate's time on screen; decisions/QF-88)."""
    plates, texts = [], []
    for line in ass.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        start, end, style, text = _ass_time(parts[1]), _ass_time(parts[2]), parts[3], parts[9]
        if style == "CapBox":
            plates.append((start, end))
        elif style == "CapText":
            texts.append((start, re.sub(r"\{[^}]*\}", "", text).replace("\\N", " ").strip()))
    rates = []
    for start, end in plates:
        words = next((t for s, t in texts if start - 0.001 <= s < end), "")
        if words and end > start:
            rates.append(len(words) / (end - start))
    return rates


def pace(engine, scripts, touched_shas):
    """Caption rate per burst over each source's masters (unchanged by this row: nothing is voiced again; the one
    edit keeps the length), and the speech rate (characters per second) of the close sentences the rule touches."""
    rates, touched = [], []
    for _, run_dir, payload in scripts:
        for ass in sorted(run_dir.glob("*_captions.ass"))[:1]:
            rates += bursts(ass)
        try:
            timed = json.loads((run_dir / "words.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = sentences(payload.get("narration_script"))
        spans, at = [], 0
        for x in s:
            n = len(engine._spoken_words(x))
            spans.append((at, at + n))
            at += n
        if not timed or at != len(timed):
            continue
        for i, x in enumerate(s):
            if sha(x) in touched_shas:
                a, b = spans[i]
                took = timed[b - 1]["start"] + timed[b - 1]["dur"] - timed[a]["start"]
                if took > 0:
                    touched.append(round(len(x) / took, 2))
    return {"bursts": len(rates), "bursts_over_17_cps": sum(r > 17 for r in rates),
            "bursts_over_20_cps": sum(r > 20 for r in rates), "burst_cps_median": median(rates),
            "burst_cps_max": round(max(rates), 2) if rates else None,
            "touched_close_sentences_timed": len(touched), "touched_speech_cps_median": median(touched),
            "touched_speech_cps_max": max(touched, default=None)}


def main(root: Path, run_dirs: list[Path], samples: frozenset = frozenset(), judgments: Path | None = None) -> dict:
    sys.path.insert(0, str(root))
    if judgments is not None:
        JUDGED.update(json.loads(judgments.read_text(encoding="utf-8")).get("judged") or {})
    from src import fact_sheet
    from src import script_engine as engine
    result = {"rule": {"has_hedge_check": check(engine) is not None,
                       "auto_hedge_scope_check": hasattr(fact_sheet, "_is_auto_hedge"),
                       "rules_version": fact_sheet.RULES_VERSION},
              "synthetic": synthetic(engine, fact_sheet), "fact_sheets": sheet_effects(root, fact_sheet)}
    library = unique(sorted((root / "data" / "script_library").glob("*/verified_script.json")))
    sources, sampled = {"script_library": library}, set()
    for i, d in enumerate(run_dirs, 1):
        sources[f"runs_{i}"] = unique(sorted(d.glob("run_*/script_metadata.json")))
        if d in samples:
            sampled.add(f"runs_{i}")
    cache: dict = {}
    seen, pooled = set(), []
    for scripts in sources.values():   # the same narration in two environments counts once
        for row in scripts:
            key = " ".join(row[2]["narration_script"].split())
            if key not in seen:
                seen.add(key)
                pooled.append(row)
    result["corpus"] = {}
    touched = set()
    for name, scripts in sources.items():
        c = corpus(engine, fact_sheet, root, scripts, name not in sampled, cache)
        touched |= {r["sha"] for r in c.pop("_rows")}
        result["corpus"][name] = c
    total = corpus(engine, fact_sheet, root, pooled, False, cache)
    total.pop("_rows")
    total.pop("per_item")
    result["corpus_pooled"] = total
    result["routing_replay"] = {name: routing(engine, fact_sheet, root, s, cache) for name, s in sources.items()}
    result["pace"] = {name: pace(engine, s, touched) for name, s in sources.items() if name != "script_library"}
    syn = result["synthetic"]
    sheets = result["fact_sheets"]
    result["acceptance_met"] = bool(
        result["rule"]["has_hedge_check"] and syn["close_cases_as_ruled"] == syn["of"]
        and syn["quantitative_qualifiers_read_as_quantitative"] == syn["of_quantitative"]
        and syn["auto_hedge_as_ruled"] == syn["of_auto"]
        and sheets["researched_again_on_next_use"] == sheets["built_under_older_rules"] and sheets["loaded_sheets_below_minimum"] == 0
        and total["body_qualifiers"]["refused"] == 0)
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
