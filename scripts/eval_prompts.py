"""
Before/after test of the script writer's prompts (scripts/prompt_variants.py), on the configured
engine (the free Gemini engine costs nothing but is rate-limited; let it run over a few sessions).

For each topic, variant and run, the FIRST draft is scored (no retries, so the prompt is measured,
not the repair loop):
  - structure: problems from validate_structure (0 is best)
  - traced: share of claims the trace check traces to the fact sheet (higher is better), and pass
  - length: whether the narration measures inside the spoken-length limits
  - tropes: banned phrases in the narration (0 is best)
Results are appended per draft to evals/prompts/results.jsonl (so an interrupted run resumes),
and summarize() prints per-variant means and the paired, per-topic comparison.

  python scripts/eval_prompts.py --runs 2            # all topics with an approved photo pool
  python scripts/eval_prompts.py --summary           # just the table
"""

import argparse
import json
import statistics
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import fact_sheet, script_engine, trace_check  # noqa: E402
from src.llm import LLMError  # noqa: E402
from scripts.prompt_variants import VARIANTS  # noqa: E402

OUT = ROOT / "evals" / "prompts"
RESULTS = OUT / "results.jsonl"


@contextmanager
def variant(name: str):
    system, rules = VARIANTS[name]
    saved = script_engine.SYSTEM_PROMPT, script_engine.FACTS_ONLY_RULES
    script_engine.SYSTEM_PROMPT, script_engine.FACTS_ONLY_RULES = system, rules
    try:
        yield
    finally:
        script_engine.SYSTEM_PROMPT, script_engine.FACTS_ONLY_RULES = saved


def score_draft(topic: Dict, engine: script_engine.ScriptEngine) -> Dict:
    pkg = engine._generate_with_claude(topic, "")
    payload = engine._finalize(pkg, topic, origin="eval", fact_sheet_used=True)
    sheet = fact_sheet.load(topic["id"])
    passed, traced, _, _ = trace_check.check(payload, sheet) if sheet else (False, 0.0, [], "")
    return {"structure": len(script_engine.validate_structure(pkg)),
            "traced": round(float(traced), 3), "passed": bool(passed),
            "length_ok": not script_engine.spoken_length_problems(pkg),
            "tropes": len(script_engine.find_banned_tropes(pkg.narration_script)),
            "words": len(pkg.narration_script.split())}


def summarize(rows: List[Dict]) -> str:
    if not rows:
        return "No results yet."
    lines = ["variant   drafts  structure  traced  pass  length_ok  tropes"]
    by = {}
    for r in rows:
        by.setdefault(r["variant"], []).append(r)
    for v, rs in sorted(by.items()):
        mean = lambda k: statistics.mean(float(r[k]) for r in rs)  # noqa: E731
        lines.append(f"{v:9} {len(rs):6}  {mean('structure'):9.2f}  {mean('traced'):6.2f}  {mean('passed'):4.2f}"
                     f"  {mean('length_ok'):9.2f}  {mean('tropes'):6.2f}")
    # Paired by topic: does "general" beat "current" on the same topic?
    topics = {r["topic"] for r in rows}
    wins = {"general": 0, "current": 0, "tie": 0}
    for t in topics:
        m = {v: [r for r in rows if r["topic"] == t and r["variant"] == v] for v in ("current", "general")}
        if not all(m.values()):
            continue
        s = {v: statistics.mean(r["traced"] - 0.2 * r["structure"] - 0.2 * r["tropes"] + 0.2 * r["length_ok"]
                                for r in rs) for v, rs in m.items()}
        wins["general" if s["general"] > s["current"] + 0.02 else "current" if s["current"] > s["general"] + 0.02
             else "tie"] += 1
    lines.append(f"paired by topic: general better {wins['general']}, current better {wins['current']}, tie {wins['tie']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--topics", nargs="*", help="Topic ids (default: topics with an approved photo pool)")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines()] if RESULTS.exists() else []
    if args.summary:
        print(summarize(rows))
        return 0
    from src.asset_pipeline import pool_ready
    from src.db import PipelineDatabase
    db = PipelineDatabase()
    topics = [db.get_topic_by_id(t) for t in args.topics] if args.topics else \
        [t for t in db.list_all_topics() if pool_ready(t["id"])]
    done = {(r["topic"], r["variant"], r["run"]) for r in rows}
    engine = script_engine.ScriptEngine()
    for run in range(1, args.runs + 1):
        for t in topics:
            for v in VARIANTS:
                if (t["id"], v, run) in done:
                    continue
                try:
                    with variant(v):
                        result = score_draft(t, engine)
                except (LLMError, fact_sheet.FactStarvedTopicError, ValueError) as e:
                    print(f"  {t['id']} {v} run {run}: skipped ({str(e)[:120]})")
                    if "quota" in str(e).lower():
                        print("Free quota used up; resume later with the same command.")
                        print(summarize(rows))
                        return 0
                    continue
                row = {"topic": t["id"], "variant": v, "run": run, **result}
                rows.append(row)
                with RESULTS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"  {t['id']} {v} run {run}: {result}")
    print(summarize(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
