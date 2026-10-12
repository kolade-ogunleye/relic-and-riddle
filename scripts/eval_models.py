"""
Small blind evals that decide whether a cheaper model can be the default (docs: plan item 1).

  python scripts/eval_models.py estimate                 # cost estimate only, no API calls
  python scripts/eval_models.py run --max-usd 30         # all evals; stops before exceeding the cap
  python scripts/eval_models.py run --only light         # one eval: scripts | second_check | light

Evals
  scripts       6 topics, a script from Opus 5.5 and from Sonnet 5.5 each; Fable 5.1 judges each pair
                blind (order randomized). Pairs are saved for a human spot-check.
  second_check  3 factual errors (date, number, attribution) are planted in each Opus script; each
                checker model must flag them. 2 clean scripts measure false alarms. A cheaper
                checker passes the bar if it catches >= 95% of what Fable catches and adds no false alarms.
  light         beat image queries (Sonnet vs Opus): judged by how many on-topic Commons candidates
                each query plan finds; music mood: agreement between models.

Results: evals/<timestamp>/results.json and summary.md. Real Claude spend is recorded in the
database with task "eval". Nothing is published; no Telegram messages are sent.
"""

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import llm  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

OPUS, SONNET, FABLE, HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-5-5"
EVAL_TOPICS = 6
CLEAN_CONTROLS = 2
# Rough per-call costs from this project's usage records (USD), for the estimate and the cap.
EST = {"script": {OPUS: 0.45, SONNET: 0.22}, "judge": 0.15, "plant": 0.03,
       "check": {FABLE: 1.87, OPUS: 0.75, SONNET: 0.37}, "match": 0.002, "light": 0.02}
CHECKERS = [FABLE, OPUS, SONNET]


class BudgetStop(RuntimeError):
    pass


class Spend:
    def __init__(self, cap: float, db: PipelineDatabase):
        self.cap, self.total, self.db = cap, 0.0, db

    def record(self, **kw):
        self.total += kw["cost_usd"]
        kw["task"] = "eval"
        self.db.record_llm_usage(**kw)

    def ensure(self, next_cost: float, what: str):
        if self.total + next_cost > self.cap:
            raise BudgetStop(f"Stopping before {what}: ${self.total:.2f} spent, next ≈ ${next_cost:.2f}, cap ${self.cap:.2f}.")


def use_model(model: str):
    """Routes the default model chain (script writing) to one model, no fallback."""
    llm.CLAUDE_MODEL, llm.CLAUDE_FALLBACK_MODELS = model, []


# ---------------------------------------------------------------- scripts eval
class PairVerdict(BaseModel):
    better: str          # "A", "B" or "tie"
    reasons: str


JUDGE = """You judge short vertical history-documentary scripts (about 110 words of narration) for a
Reels channel. Judge as a demanding editor: hook strength in the first sentence, accuracy and
hedging of uncertain facts, storytelling and pacing, specificity (no generic filler or AI-sounding
phrasing), clarity when heard aloud, and a satisfying close. Length differences alone don't matter."""


def eval_scripts(topics: List[Dict[str, Any]], spend: Spend, out: Path) -> Dict[str, Any]:
    from src.script_engine import ScriptEngine
    scripts: Dict[str, Dict[str, Any]] = {}
    for t in topics:
        for model in (OPUS, SONNET):
            spend.ensure(EST["script"][model], f"{model} script for {t['id']}")
            use_model(model)
            payload = ScriptEngine().generate_script(t)
            if payload.get("origin") != "claude":
                raise RuntimeError(f"{model} did not produce a Claude script for {t['id']}")
            scripts.setdefault(t["id"], {})[model] = payload
    (out / "scripts.json").write_text(json.dumps(scripts, indent=1, ensure_ascii=False), encoding="utf-8")

    results, rng = [], random.Random(7)
    for tid, pair in scripts.items():
        a_model, b_model = rng.sample([OPUS, SONNET], 2)  # blind, randomized order
        spend.ensure(EST["judge"], f"judging {tid}")
        verdict = llm.structured_call(JUDGE, (
            f"Script A:\n{pair[a_model]['narration_script']}\n\nScript B:\n{pair[b_model]['narration_script']}\n\n"
            "Which is better for this channel? Answer A, B or tie."), PairVerdict, effort="medium", models=[FABLE])
        winner = {"A": a_model, "B": b_model}.get(verdict.better.strip().upper()[:1], "tie")
        results.append({"topic": tid, "winner": winner, "reasons": verdict.reasons, "A": a_model, "B": b_model})
    sonnet_ok = sum(1 for r in results if r["winner"] in (SONNET, "tie"))
    return {"pairs": results, "sonnet_wins_or_ties": sonnet_ok, "of": len(results),
            "switch": sonnet_ok / max(1, len(results)) >= 0.45, "scripts": scripts}


# ---------------------------------------------------------- second-check eval
class PlantedError(BaseModel):
    kind: str            # "date", "number" or "attribution"
    original: str        # exact phrase in the original narration
    corrupted: str       # the replacement phrase (plausible but false)


class Corruption(BaseModel):
    narration_script: str
    errors: List[PlantedError]


class Caught(BaseModel):
    caught: bool


PLANT = """You create test cases for a fact-checker. Change exactly three facts in the narration so they
become plausible but FALSE: one date, one number or measurement, one attribution (who did, built,
said or found something). Keep everything else word for word, and don't hedge the false facts."""


def _flagged_claims(audits: List[Dict[str, Any]]) -> List[str]:
    return [f"{a['claim']} ({a['verdict']}: {a['note']})" for a in audits
            if not (a["verdict"] == "supported" and a["sources"]) and not (a["verdict"] == "disputed" and a["hedged_in_script"])]


def eval_second_check(scripts: Dict[str, Dict[str, Any]], spend: Spend, checkers: List[str]) -> Dict[str, Any]:
    from src.fact_checker import ClaimFactChecker
    checker = ClaimFactChecker()
    cases = []
    for tid, pair in scripts.items():
        payload = dict(pair[OPUS])
        spend.ensure(EST["plant"], f"planting errors in {tid}")
        c = llm.structured_call(PLANT, f"<narration>\n{payload['narration_script']}\n</narration>", Corruption,
                                effort="low", models=[SONNET])
        if len(c.errors) != 3 or any(e.corrupted not in c.narration_script for e in c.errors):
            print(f"  {tid}: planting failed validation; skipped")
            continue
        cases.append({"topic": tid, "payload": dict(payload, narration_script=c.narration_script),
                      "planted": [e.model_dump() for e in c.errors]})
    clean = [{"topic": tid, "payload": dict(pair[OPUS]), "planted": []} for tid, pair in list(scripts.items())[:CLEAN_CONTROLS]]

    per_model: Dict[str, Dict[str, Any]] = {}
    for model in checkers:
        caught, false_alarms, details = set(), 0, []
        for case in cases + clean:
            spend.ensure(EST["check"][model], f"{model} check of {case['topic']}")
            report, urls = checker._check_with_claude(case["payload"], models=[model])
            flagged = _flagged_claims(checker.evaluate(report, urls)[2])
            hits = []
            for i, err in enumerate(case["planted"]):
                spend.ensure(EST["match"], "matching")
                m = llm.structured_call(
                    "Decide whether any flagged claim corresponds to the planted false fact (same fact, shown as wrong).",
                    f"Planted false fact: {err['corrupted']} (true: {err['original']})\nFlagged claims:\n- "
                    + ("\n- ".join(flagged) or "(none)"), Caught, effort="low", models=[HAIKU, SONNET])
                if m.caught:
                    caught.add((case["topic"], i))
                    hits.append(i)
            if not case["planted"] and flagged:
                false_alarms += 1
            details.append({"topic": case["topic"], "planted": len(case["planted"]), "caught": hits, "flagged": flagged})
        per_model[model] = {"caught": sorted(caught), "n_caught": len(caught), "false_alarms": false_alarms, "details": details}

    ref = set(map(tuple, per_model.get(FABLE, {}).get("caught", [])))
    verdicts = {}
    for model, r in per_model.items():
        if model == FABLE:
            continue
        mine = set(map(tuple, r["caught"]))
        share = len(mine & ref) / len(ref) if ref else 1.0
        verdicts[model] = {"share_of_fable_catches": round(share, 3), "extra_false_alarms":
                           r["false_alarms"] - per_model.get(FABLE, {}).get("false_alarms", 0),
                           "meets_bar": share >= 0.95 and r["false_alarms"] <= per_model.get(FABLE, {}).get("false_alarms", 0)}
    return {"planted_total": sum(len(c["planted"]) for c in cases), "clean_controls": len(clean),
            "per_model": per_model, "verdicts": verdicts}


# ------------------------------------------------------------------ light eval
def eval_light(scripts: Dict[str, Dict[str, Any]], topics: Dict[str, Dict[str, Any]], spend: Spend) -> Dict[str, Any]:
    from src import beat_images, commons, music
    from src.config import LIGHT_TASK_MODELS  # noqa: F401  (documented default under test)
    out = {"beat_queries": [], "music_mood": []}
    orig = list(LIGHT_TASK_MODELS)
    for tid, pair in scripts.items():
        text = pair[OPUS]["narration_script"]
        beats = [{"text": s.strip()} for s in text.replace("?", ".").split(".") if s.strip()]
        row = {"topic": tid}
        for model in (OPUS, SONNET):
            spend.ensure(EST["light"], "beat queries")
            beat_images.config.LIGHT_TASK_MODELS = [model]
            plan = beat_images.plan_queries(beats, topics[tid])
            found = 0
            for item in plan.items[:beat_images.MAX_BEATS]:
                try:
                    found += sum(1 for _ in commons.candidates(commons.search(item.query, limit=30, max_wait=60), item.keywords))
                except Exception as e:  # Commons outages shouldn't sink the eval
                    print(f"  commons search failed: {e}")
            row[model] = {"queries": [i.query for i in plan.items], "candidates_found": found}
        out["beat_queries"].append(row)
        moods = {}
        for model in (OPUS, SONNET):
            spend.ensure(EST["light"], "music mood")
            music.LIGHT_TASK_MODELS = [model]
            moods[model] = music.choose_mood(pair[OPUS])
        out["music_mood"].append({"topic": tid, **moods})
    beat_images.config.LIGHT_TASK_MODELS = orig
    music.LIGHT_TASK_MODELS = orig
    s_found = sum(r[SONNET]["candidates_found"] for r in out["beat_queries"])
    o_found = sum(r[OPUS]["candidates_found"] for r in out["beat_queries"])
    agree = sum(1 for r in out["music_mood"] if r[OPUS] == r[SONNET])
    out["summary"] = {"sonnet_candidates": s_found, "opus_candidates": o_found,
                      "mood_agreement": f"{agree}/{len(out['music_mood'])}",
                      "switch": s_found >= 0.9 * o_found}
    return out


# ---------------------------------------------------------------------- main
def estimate() -> float:
    scripts = EVAL_TOPICS * (EST["script"][OPUS] + EST["script"][SONNET]) + EVAL_TOPICS * EST["judge"]
    checks = EVAL_TOPICS * EST["plant"] + sum(EST["check"][m] for m in CHECKERS) * (EVAL_TOPICS + CLEAN_CONTROLS) \
        + len(CHECKERS) * EVAL_TOPICS * 3 * EST["match"]
    light = EVAL_TOPICS * 4 * EST["light"]
    print(f"Scripts eval:       ~${scripts:.2f}")
    print(f"Second-check eval:  ~${checks:.2f}  ({', '.join(CHECKERS)}; Gemini added when GEMINI_API_KEY is set, free)")
    print(f"Light-task eval:    ~${light:.2f}")
    total = scripts + checks + light
    print(f"Total:              ~${total:.2f}")
    return total


def summary_md(res: Dict[str, Any]) -> str:
    lines = [f"# Model eval {res['at']}", f"Spend: ${res['spend']:.2f}", ""]
    if "scripts" in res:
        s = res["scripts"]
        lines += ["## Script writing (Opus 5.5 vs Sonnet 5.5, blind, Fable 5.1 judge)",
                  f"Sonnet wins or ties: {s['sonnet_wins_or_ties']}/{s['of']} → switch: {s['switch']}", ""]
        lines += [f"- {p['topic']}: winner {p['winner']} — {p['reasons'][:300]}" for p in s["pairs"]]
    if "second_check" in res:
        sc = res["second_check"]
        lines += ["", "## Second fact check", f"Planted errors: {sc['planted_total']}; clean controls: {sc['clean_controls']}"]
        for m, r in sc["per_model"].items():
            lines.append(f"- {m}: caught {r['n_caught']}/{sc['planted_total']}, false alarms {r['false_alarms']}")
        for m, v in sc["verdicts"].items():
            lines.append(f"- {m} vs Fable: {v['share_of_fable_catches'] * 100:.0f}% of Fable's catches, "
                         f"extra false alarms {v['extra_false_alarms']} → meets bar: {v['meets_bar']}")
    if "light" in res:
        lines += ["", "## Light tasks", json.dumps(res["light"]["summary"])]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["estimate", "run"])
    ap.add_argument("--max-usd", type=float, default=30.0)
    ap.add_argument("--only", choices=["scripts", "second_check", "light"])
    ap.add_argument("--scripts-from", help="Reuse scripts.json from an earlier eval folder")
    args = ap.parse_args()
    if args.action == "estimate":
        estimate()
        return 0
    if not llm.claude_available():
        print("ANTHROPIC_API_KEY is not configured.")
        return 1

    db = PipelineDatabase()
    spend = Spend(args.max_usd, db)
    llm.configure_usage(spend.record, db.llm_spend)
    out = ROOT / "evals" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    topics = [t for t in db.list_all_topics() if t["id"] != "01_derinkuyu_underground_city"][:EVAL_TOPICS]
    res: Dict[str, Any] = {"at": out.name}
    try:
        if args.scripts_from:
            scripts = json.loads((Path(args.scripts_from) / "scripts.json").read_text(encoding="utf-8"))
        elif args.only in (None, "scripts", "second_check", "light"):
            res["scripts"] = eval_scripts(topics, spend, out)
            scripts = res["scripts"].pop("scripts")
        if args.only in (None, "second_check"):
            res["second_check"] = eval_second_check(scripts, spend, CHECKERS)
        if args.only in (None, "light"):
            res["light"] = eval_light(scripts, {t["id"]: t for t in topics}, spend)
    except BudgetStop as e:
        print(e)
        res["stopped"] = str(e)
    finally:
        res["spend"] = round(spend.total, 2)
        (out / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        (out / "summary.md").write_text(summary_md(res), encoding="utf-8")
        llm.configure_usage(None, None)
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
