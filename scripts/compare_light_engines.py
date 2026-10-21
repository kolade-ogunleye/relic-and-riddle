"""
Can a light-task model stand in for the current one? Run before setting FREE_LIGHT_MODEL or turning on
GROQ_LIGHT_FALLBACK.

  python scripts/compare_light_engines.py --candidate gemini:gemini-3.6-flash
  python scripts/compare_light_engines.py --candidate groq

On the most recent runs with timed narration (words.json), the reference (what light tasks use today:
the free writer model for the sound plan, the checker model for /ask) and the candidate both:
- mark sound moments; code validates both. The reference is also run TWICE, so the candidate's
  agreement with it is read against how much the reference agrees with itself (the baseline);
- answer three /ask questions from the same records. Measured: invented record names, and whether
  an answer about the release gate contradicts the release report (says it cleared while a check is
  open, or the reverse). Answers are written side by side to evals/light_engines/<candidate>.md.

Free keys only for the Gemini side when its quota allows; it may use the capped light paid fallback (cents).
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, free_llm, gemini, groq, probe, sound_plan  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402
from src.llm import LLMError  # noqa: E402

OUT = ROOT / "evals" / "light_engines"
QUESTIONS = ("Why did the release gate hold or pass this run?", "Was AI motion used, and why or why not?",
             "Which voice and music were used?")
ASK_SYSTEM = ("You answer a video producer's question about one production run of an automated history-documentary "
              "pipeline. Use ONLY the records given. Name the record each point comes from. If the records do not "
              "contain the answer, set recorded=false. Plain text, at most 120 words.")
CLEARED = re.compile(r"\b(cleared|passed|allowed|all checks pass|no issues)\b", re.I)
HELD = re.compile(r"\b(held|hold|needs[_ ]review|not (fully )?pass|blocked|flagged)\b", re.I)


def reference(role: str):
    def call(system, prompt, schema):
        saved, config.FREE_LIGHT_MODEL = config.FREE_LIGHT_MODEL, ""  # today's routing, not a candidate
        try:
            return free_llm.structured(system, prompt, schema, role=role, task_class="light")
        finally:
            config.FREE_LIGHT_MODEL = saved
    return call


def candidate(spec: str):
    if spec == "groq":
        return groq.structured, groq.GroqError
    model = spec.split(":", 1)[1]
    return (lambda system, prompt, schema: gemini.structured(system, prompt, schema, model=model,
                                                             key=config.GEMINI_FREE_API_KEY)), gemini.GeminiError


def agreement(a, b) -> float:
    if not a or not b:
        return 0.0
    hits = sum(any(m["kind"] == n["kind"] and abs(m["t"] - n["t"]) <= 1.2 for n in b) for m in a)
    return hits / max(len(a), len(b))


def contradicts_gate(answer: str, release_checks) -> bool:
    if not isinstance(release_checks, list):
        return False
    open_checks = any(c.get("status") != "pass" for c in release_checks)
    says_cleared, says_held = bool(CLEARED.search(answer)), bool(HELD.search(answer))
    return (open_checks and says_cleared and not says_held) or (not open_checks and says_held and not says_cleared)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default="groq", help="groq, or gemini:<model id>")
    ap.add_argument("--runs", type=int, default=4)
    args = ap.parse_args()
    if args.candidate == "groq" and not groq.available():
        print("Set GROQ_API_KEY first.")
        return 1
    config.GROQ_LIGHT_FALLBACK = False
    cand, cand_error = candidate(args.candidate)
    db = PipelineDatabase()
    with db._conn() as conn:
        run_ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 40")]
    runs = [db.get_run(r) for r in run_ids if (config.OUTPUT_DIR / r / "words.json").exists()][:args.runs]
    OUT.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"\W+", "_", args.candidate)
    report, stats = [f"# Light tasks: reference vs {args.candidate}", ""], {"plans": [], "ask": []}
    errors = (LLMError, gemini.GeminiError, groq.GroqError, cand_error)
    for run in runs:
        words = json.loads((config.OUTPUT_DIR / run["run_id"] / "words.json").read_text(encoding="utf-8"))
        words = words.get("words", words) if isinstance(words, dict) else words
        numbered = " ".join(f"[{i}]{w.get('word', '')}" for i, w in enumerate(words, 1))
        prompt = f"Topic: {run['topic_id']}\n\nNarration:\n{numbered}"
        report += [f"## {db.run_label(run['run_id'])}", ""]
        plans = {}
        for label, fn in (("reference", reference("writer")), ("reference_again", reference("writer")), ("candidate", cand)):
            try:
                plans[label] = (sound_plan.validate(fn(sound_plan.SYSTEM, prompt, sound_plan.SoundPlan), words) or {}).get("moments", [])
            except errors as e:
                plans[label] = None
                report.append(f"- {label} sound plan failed: {e}")
        row = {"run": run["run_id"], "candidate_usable": bool(plans.get("candidate")),
               "baseline": round(agreement(plans.get("reference") or [], plans.get("reference_again") or []), 2),
               "agreement": round(agreement(plans.get("reference") or [], plans.get("candidate") or []), 2)}
        stats["plans"].append(row)
        report.append(f"- Sound plan: candidate usable={row['candidate_usable']}; agreement with reference "
                      f"{row['agreement']:.0%} (reference vs itself {row['baseline']:.0%})")
        ev = probe.evidence(db, run)
        names = set(ev) | set(ev.get("review card notes") or {})
        body = json.dumps(ev, ensure_ascii=False, default=str)[:60000]
        for q in QUESTIONS:
            report += ["", f"**Q: {q}**"]
            for label, fn in (("reference", reference("checker")), ("candidate", cand)):
                try:
                    ans = fn(ASK_SYSTEM, f"<records>\n{body}\n</records>\n\nQuestion: {q}", probe._Answer)
                except errors as e:
                    report.append(f"- {label}: failed ({e})")
                    stats["ask"].append({"engine": label, "failed": True})
                    continue
                invented = [r for r in ans.records if not any(r.split(".")[0].lower() in n.lower() for n in names)]
                wrong = q.startswith("Why did the release gate") and contradicts_gate(ans.answer, ev.get("release_report.json"))
                stats["ask"].append({"engine": label, "invented_records": invented, "contradicts_records": wrong})
                report.append(f"- {label}: {ans.answer}" + (f"  ⚠️ unknown records: {invented}" if invented else "")
                              + ("  ❌ contradicts the release report" if wrong else ""))
        report.append("")
    (OUT / f"{name}.md").write_text("\n".join(report), encoding="utf-8")
    (OUT / f"{name}.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    plans = stats["plans"]
    n = max(1, len(plans))
    usable = sum(p["candidate_usable"] for p in plans) / n
    agree, base = sum(p["agreement"] for p in plans) / n, sum(p["baseline"] for p in plans) / n
    cand_rows = [a for a in stats["ask"] if a["engine"] == "candidate"]
    failed = sum(a.get("failed", False) for a in cand_rows)
    bad = sum(bool(a.get("invented_records")) or a.get("contradicts_records", False) for a in cand_rows)
    ok = usable == 1 and agree >= base - 0.15 and bad == 0 and failed == 0
    print(f"{args.candidate}: usable sound plans {usable:.0%}; agreement {agree:.0%} vs reference-with-itself {base:.0%}; "
          f"/ask answers wrong or citing unknown records: {bad}; failed calls: {failed}")
    print("Verdict:", "holds up (read the report before switching)" if ok else "does not hold up", f"-> evals/light_engines/{name}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
