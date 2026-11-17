"""
The evaluation harness base (QF-79): the loop every writer eval runs, built on scripts/eval_prompts.py, the only
eval that measures the production writer on the configured engine. QF-17 (narration on a stronger model) extends
it with its own variants and scorers instead of reviving the stale Claude-only comparisons (eval_models.py,
eval_facts_first.py).

The harness itself never builds an engine and never calls a provider: the caller passes `draft`, a function from a
topic to a scored row. A live run passes eval_prompts.score_draft bound to a ScriptEngine (the configured writer,
free quota by default: see eval_prompts.main); tests pass a stub. Rows are appended to a JSONL file as they are
made, so an interrupted run resumes where it stopped.

    python scripts/eval_harness.py RESULTS.jsonl --baseline current --candidate general   # offline summary only
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import eval_prompts

Draft = Callable[[dict], dict]
Variant = Callable[[str], AbstractContextManager]

# The paired-comparison weights and margin of eval_prompts.summarize, so both report the same winner.
WEIGHTS = {"traced": 1.0, "structure": -0.2, "tropes": -0.2, "length_ok": 0.2}
MARGIN = 0.02


def load(results: Path | None) -> list[dict]:
    """The rows already recorded in `results` (none when there is no file yet)."""
    if results is None or not results.exists():
        return []
    return [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(topics: Iterable[dict], variants: Iterable[str], draft: Draft, *, runs: int = 1,
        results: Path | None = None, variant: Variant = eval_prompts.variant,
        max_drafts: int | None = None) -> list[dict]:
    """Drafts each (topic, variant, run) not yet in `results` under that variant's prompts and returns every row.

    `variant` is the context manager that installs a variant's prompts (eval_prompts.variant by default; it is
    called with the name alone, so a "candidate:<id>" variant needs its rule bound first, for example
    functools.partial(eval_prompts.variant, candidate_rule=rule)); `max_drafts` bounds the new drafts of this call
    (a quota or a spend limit is the caller's to enforce)."""
    rows = load(results)
    done = {(r["topic"], r["variant"], r["run"]) for r in rows}
    topics, variants, made = list(topics), list(variants), 0
    for n in range(1, runs + 1):
        for topic in topics:
            for name in variants:
                if (topic["id"], name, n) in done:
                    continue
                if max_drafts is not None and made >= max_drafts:
                    return rows
                with variant(name):
                    scored = draft(topic)
                made += 1
                row = {"topic": topic["id"], "variant": name, "run": n, **scored}
                rows.append(row)
                done.add((topic["id"], name, n))
                if results is not None:
                    results.parent.mkdir(parents=True, exist_ok=True)
                    with results.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
    return rows


def score(row: dict) -> float:
    """One draft's composite: traced share, minus structure problems and tropes, plus the length check."""
    return sum(weight * float(row[key]) for key, weight in WEIGHTS.items())


def compare(rows: list[dict], baseline: str, candidate: str, margin: float = MARGIN) -> dict[str, int]:
    """Paired by topic: on how many topics the candidate's mean composite beats the baseline's by more than
    `margin`, the reverse, or neither. Topics without both variants are left out."""
    wins = {"candidate": 0, "baseline": 0, "tie": 0}
    for topic in sorted({r["topic"] for r in rows}):
        means = {}
        for name in (baseline, candidate):
            drafts = [score(r) for r in rows if r["topic"] == topic and r["variant"] == name]
            if drafts:
                means[name] = statistics.mean(drafts)
        if len(means) < 2:
            continue
        if means[candidate] > means[baseline] + margin:
            wins["candidate"] += 1
        elif means[baseline] > means[candidate] + margin:
            wins["baseline"] += 1
        else:
            wins["tie"] += 1
    return wins


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("results", type=Path, help="a results JSONL file (eval_prompts writes data/evals/prompts/)")
    ap.add_argument("--baseline", default="current")
    ap.add_argument("--candidate", default="general")
    args = ap.parse_args(argv)
    rows = load(args.results)
    if (args.baseline, args.candidate) == ("current", "general"):
        # eval_prompts' table ends with its own paired line, which compares exactly this pair.
        print(eval_prompts.summarize(rows))
    print(json.dumps({"pair": [args.baseline, args.candidate], **compare(rows, args.baseline, args.candidate)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
