"""
Measures the verify-first pipeline (fact sheet -> write -> trace check -> independent second check ->
surgical revisions) before it replaces the old one.

  python scripts/eval_facts_first.py --max-usd 25

Per topic: drafts needed to pass (first-pass rate), final pass/fail, cost. Then, for each passing
script, three errors are planted (date, number, attribution) and the trace check must catch them
(baseline: the old web fact check caught 17 of 18 = 94%). Results: evals/<timestamp>-facts/.
No Telegram, no rendering, nothing published.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import fact_checker, llm, script_engine, trace_check  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402
from scripts.eval_models import PLANT, Corruption, SONNET, OPUS, Spend, BudgetStop  # noqa: E402

TOPICS = ["01_derinkuyu_underground_city", "02_roman_self_healing_concrete", "05_gobekli_tepe", "07_camp_century_nuclear_ice_city"]
MAX_REVISIONS = 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-usd", type=float, default=25.0)
    args = ap.parse_args()
    db = PipelineDatabase()
    spend = Spend(args.max_usd, db)
    llm.configure_usage(spend.record, db.llm_spend)
    # The production default for the second check is still Fable pending a decision; the eval uses Opus.
    fact_checker.SECOND_CHECK_MODELS = [OPUS]
    out = ROOT / "evals" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-facts")
    out.mkdir(parents=True, exist_ok=True)
    results = {"topics": [], "catch": []}
    eng = script_engine.ScriptEngine()

    def save():
        (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    try:
        for tid in TOPICS:
            topic = db.get_topic_by_id(tid)
            start = spend.total
            spend.ensure(3.5, f"fact sheet + script for {tid}")
            payload, notes, drafts, passed = None, "", 0, False
            for revision in range(MAX_REVISIONS + 1):
                payload = eng.generate_script(topic, revision_notes=notes, previous=payload)
                drafts += 1
                spend.ensure(2.2, f"checks for {tid}")
                passed, payload, notes = eng.verify_claims(payload, topic=topic)
                print(f"{tid} draft {drafts}: {'PASS' if passed else 'fail'} - {notes[:200]}")
                if passed or payload.get("origin") != "claude":
                    break
            results["topics"].append({"topic": tid, "drafts": drafts, "passed": passed, "fact_sheet": payload.get("fact_sheet"),
                                      "cost": round(spend.total - start, 2), "last_notes": notes[:1500],
                                      "narration": payload.get("narration_script")})
            save()
            if not passed:
                continue
            # Planted errors: does the trace check catch them?
            spend.ensure(0.6, f"planting for {tid}")
            c = llm.structured_call(PLANT, f"<narration>\n{payload['narration_script']}\n</narration>", Corruption,
                                    effort="low", models=[SONNET])
            errors = [e for e in c.errors if e.corrupted.strip() != e.original.strip() and e.original in payload["narration_script"]]
            corrupted = dict(payload, narration_script=c.narration_script)
            sheet = eng._sheet(topic)
            ok, _, audits, msg = trace_check.check(corrupted, sheet)
            caught = [e.corrupted for e in errors if e.corrupted.lower()[:25] in msg.lower() or any(
                e.corrupted.lower()[:25] in a["claim"].lower() and a["verdict"] != "traced" for a in audits)]
            results["catch"].append({"topic": tid, "planted": len(errors), "caught": len(caught),
                                     "errors": [e.model_dump() for e in errors], "trace_message": msg[:1500]})
            print(f"{tid} planted {len(errors)}, trace check caught {len(caught)}")
            save()
    except BudgetStop as e:
        print(e)
        results["stopped"] = str(e)
    finally:
        results["spend"] = round(spend.total, 2)
        t = results["topics"]
        results["summary"] = {
            "first_pass": f"{sum(1 for x in t if x['passed'] and x['drafts'] == 1)}/{len(t)}",
            "passed_within_revisions": f"{sum(1 for x in t if x['passed'])}/{len(t)}",
            "mean_cost_per_topic": round(sum(x["cost"] for x in t) / max(1, len(t)), 2),
            "planted_caught": f"{sum(x['caught'] for x in results['catch'])}/{sum(x['planted'] for x in results['catch'])}",
        }
        save()
        llm.configure_usage(None, None)
        print(json.dumps(results["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
