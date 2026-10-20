"""Prepare a verified Reel package for local validation without publishing."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import DATA_DIR
from src.fact_evidence import record_fact_review
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
            record_fact_review(payload, DATA_DIR / "fact_sheets" / f"{args.topic_id}.json")
            break
        previous = payload
    (args.output / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
