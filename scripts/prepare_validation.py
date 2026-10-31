"""Prepare and fact-check a fresh documentary package without messaging or publishing."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import DATA_DIR
from src.fact_evidence import record_fact_review
from src.script_engine import ScriptEngine
from src.trace_check import check, evidence_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--feedback", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    bank = json.loads((DATA_DIR / "topics/topic_bank.json").read_text(encoding="utf-8"))
    topics = bank.get("topics", []) if isinstance(bank, dict) else bank
    topic = next(t for t in topics if t["id"] == args.topic_id)
    sheet_path = DATA_DIR / "fact_sheets" / f"{args.topic_id}.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    engine = ScriptEngine()
    feedback = args.feedback.read_text(encoding="utf-8") if args.feedback else ""
    package = engine.generate_deep_dive(topic, feedback=feedback)
    (args.output / "documentary_package.json").write_text(package.model_dump_json(indent=2), encoding="utf-8")
    passed, issues = engine.verify_deep_dive_act_by_act(package, topic)
    payload = {"title": package.title, "youtube_title": package.youtube_title,
               "narration_script": " ".join(a.narration_text for a in package.acts),
               "description": package.description, "caption": package.title + "\n\n" + package.description,
               "first_comment": package.closing_debate_question,
               "thumbnail": {"eyebrow": "DOCUMENTARY", "line1": topic["title"],
                             "line2": "HISTORICAL INVESTIGATION", "tagline": "EXPLORE THE EVIDENCE"},
               "delivery_profile": "deep_dive_16x9", "origin": "validation_documentary",
               "guardrail_passed": passed, "verification_issues": issues}
    if passed:
        passed, confidence, audits, notes = check(payload, sheet)
        payload.update(guardrail_passed=passed, final_fact_audits=audits,
                       verification_confidence=confidence, verification_summary=notes)
        if passed:
            status, confidence, audits, notes = evidence_check(payload, sheet)
            passed = status == "pass"
            payload.update(guardrail_passed=passed, second_check={"status": status, "confidence": confidence, "audits": audits, "summary": notes})
            if passed:
                record_fact_review(payload, sheet_path)
    (args.output / "script_metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"passed": passed, "issues": issues, "words": len(payload["narration_script"].split()),
                      "output": str(args.output.resolve())}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
