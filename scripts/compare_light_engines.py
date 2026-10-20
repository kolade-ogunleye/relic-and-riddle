"""
Does the free Groq model hold up against Gemini on our light text tasks? Run before turning on
GROQ_LIGHT_FALLBACK.

On the most recent runs that have timed narration (words.json):
- Sound plan: both models mark moments on the same narration; code validates both (sound_plan.validate).
  Measured: usable plans, and agreement (a Groq moment of the same kind within 3 words of a Gemini one).
- /ask: three fixed questions per run against the same records. Measured: answers whose "records" are
  all real record names (no invented sources), and how often each says "not recorded". The answers are
  written side by side to evals/light_engines/report.md for reading.

  python scripts/compare_light_engines.py --runs 4
Gemini calls use the free key first and may use the capped light paid fallback (cents); Groq is free.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, free_llm, groq, probe, sound_plan  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402
from src.llm import LLMError  # noqa: E402

OUT = ROOT / "evals" / "light_engines"
QUESTIONS = ("Why did the release gate hold or pass this run?", "Was AI motion used, and why or why not?",
             "Which voice and music were used?")
ASK_SYSTEM = ("You answer a video producer's question about one production run of an automated history-documentary "
              "pipeline. Use ONLY the records given. Name the record each point comes from. If the records do not "
              "contain the answer, set recorded=false. Plain text, at most 120 words.")


def gemini(system, prompt, schema):
    return free_llm.structured(system, prompt, schema, role="checker", task_class="light")


def agreement(a, b) -> float:
    if not a or not b:
        return 0.0
    hits = sum(any(m["kind"] == n["kind"] and abs(m["t"] - n["t"]) <= 1.2 for n in b) for m in a)
    return hits / max(len(a), len(b))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    args = ap.parse_args()
    if not groq.available():
        print("Set GROQ_API_KEY first (https://console.groq.com/keys).")
        return 1
    config.GROQ_LIGHT_FALLBACK = False  # the Gemini side must really be Gemini
    db = PipelineDatabase()
    with db._conn() as conn:
        run_ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 40")]
    runs = [db.get_run(r) for r in run_ids if (config.OUTPUT_DIR / r / "words.json").exists()][:args.runs]
    OUT.mkdir(parents=True, exist_ok=True)
    report, stats = ["# Light engines: Gemini vs Groq", ""], {"plans": [], "ask": []}
    for run in runs:
        words = json.loads((config.OUTPUT_DIR / run["run_id"] / "words.json").read_text(encoding="utf-8"))
        words = words.get("words", words) if isinstance(words, dict) else words
        numbered = " ".join(f"[{i}]{w.get('word', '')}" for i, w in enumerate(words, 1))
        prompt = f"Topic: {run['topic_id']}\n\nNarration:\n{numbered}"
        report += [f"## {db.run_label(run['run_id'])}", ""]
        plans = {}
        for name, fn in (("gemini", gemini), ("groq", groq.structured)):
            try:
                raw = fn(sound_plan.SYSTEM, prompt, sound_plan.SoundPlan)
                plans[name] = (sound_plan.validate(raw, words) or {}).get("moments", [])
            except (LLMError, groq.GroqError) as e:
                plans[name] = None
                report.append(f"- {name} sound plan failed: {e}")
        usable = {k: v is not None and len(v) > 0 for k, v in plans.items()}
        agree = agreement(plans.get("gemini") or [], plans.get("groq") or [])
        stats["plans"].append({"run": run["run_id"], **usable, "agreement": round(agree, 2)})
        report.append(f"- Sound plan: usable gemini={usable['gemini']} groq={usable['groq']}, agreement {agree:.0%}")
        ev = probe.evidence(db, run)
        names = set(ev) | {k for k in (ev.get("review card notes") or {})}
        body = json.dumps(ev, ensure_ascii=False, default=str)[:60000]
        for q in QUESTIONS:
            report += ["", f"**Q: {q}**"]
            for name, fn in (("gemini", gemini), ("groq", groq.structured)):
                try:
                    ans = fn(ASK_SYSTEM, f"<records>\n{body}\n</records>\n\nQuestion: {q}", probe._Answer)
                except (LLMError, groq.GroqError) as e:
                    report.append(f"- {name}: failed ({e})")
                    continue
                invented = [r for r in ans.records if not any(r.split(".")[0].lower() in n.lower() for n in names)]
                stats["ask"].append({"engine": name, "recorded": ans.recorded, "invented_records": invented})
                report.append(f"- {name}{' (not recorded)' if not ans.recorded else ''}: {ans.answer}"
                              + (f"  ⚠️ unknown records: {invented}" if invented else ""))
        report.append("")
    (OUT / "report.md").write_text("\n".join(report), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    plans = stats["plans"]
    groq_usable = sum(p["groq"] for p in plans) / max(1, len(plans))
    mean_agree = sum(p["agreement"] for p in plans) / max(1, len(plans))
    groq_invented = sum(bool(a["invented_records"]) for a in stats["ask"] if a["engine"] == "groq")
    ok = groq_usable == 1 and mean_agree >= 0.5 and groq_invented == 0
    print(f"Groq usable sound plans: {groq_usable:.0%}; agreement with Gemini: {mean_agree:.0%}; "
          f"/ask answers citing unknown records: {groq_invented}")
    print("Recommendation:", "turn on GROQ_LIGHT_FALLBACK (then read report.md)" if ok else "keep it off; see evals/light_engines/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
