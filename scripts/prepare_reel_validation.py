"""Prepare a verified Reel package for local validation without publishing."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import DATA_DIR
from src.fact_evidence import stamp_checked_review
from src.trace_check import check, checker_models, evidence_check
from src.script_engine import ScriptEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic_id")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    bank = json.loads((DATA_DIR / "topics/topic_bank.json").read_text(encoding="utf-8"))
    topic = next(t for t in bank["topics"] if t["id"] == args.topic_id)
    engine = ScriptEngine()
    previous, notes, passed = None, "", False
    for attempt in range(1, 4):
        payload = engine.generate_script(topic, revision_notes=notes, previous=previous)
        passed, payload, notes = engine.verify_claims(payload, topic=topic)
        payload.update(guardrail_passed=passed, delivery_profile="reel_9x16")
        (args.output / f"script_attempt_{attempt}.json").write_text(json.dumps({"payload": payload, "notes": notes}, indent=2), encoding="utf-8")
        print(json.dumps({"attempt": attempt, "passed": passed, "notes": notes}), flush=True)
        if passed:
            # the script stage's pass is not a fact check: the copy goes through both checkers and is stamped with
            # their signed runs (operator decision 2026-11-04; fact_evidence.record_fact_review refuses otherwise)
            sheet_path = DATA_DIR / "fact_sheets" / f"{args.topic_id}.json"
            sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
            traced = check(payload, sheet)
            evidence = evidence_check(payload, sheet) if traced[0] else ("fail", 0.0, [], "trace check failed")
            passed = traced[0] and evidence[0] == "pass"
            payload["guardrail_passed"] = passed
            if passed:
                stamp_checked_review(payload, sheet_path, traced, evidence, checker_models())
            else:
                notes = f"{traced[3]}; {evidence[3]}"
                print(json.dumps({"attempt": attempt, "passed": False, "notes": notes}), flush=True)
                previous = payload
                continue
            break
        previous = payload
    (args.output / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
