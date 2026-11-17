"""One explore-then-learn rule for every choice the audience's numbers decide (cover styles, hook forms).

Each option is tried MIN_TRIES times before the numbers count, least-tried first; then Thompson sampling: each
option's rewards (0..1, one per run) form a Beta posterior and the option with the highest draw wins, so the
better options go out more often while the others keep being tried now and then. The draw is seeded, so a
resumed run makes the same choice from the same numbers.
"""
from __future__ import annotations

import random

MIN_TRIES = 3


def choose(options: list[str], seen: dict[str, list[float]], seed: str, label: str,
           min_tries: int = MIN_TRIES) -> tuple[str, str]:
    """(option, why) among `options` given each one's per-run rewards in `seen`."""
    if not options:
        raise ValueError("nothing to choose from")
    rng = random.Random(f"{label}:{seed}")
    tries = {o: len(seen.get(o, [])) for o in options}
    fewest = min(tries.values())
    if fewest < min_tries:
        option = rng.choice([o for o in options if tries[o] == fewest])
        return option, f"exploring: {option} has {fewest} measured run(s) of {min_tries} before the numbers count"
    draws = {}
    for o in options:
        gains = sum(seen[o])
        draws[o] = rng.betavariate(1 + gains, 1 + len(seen[o]) - gains)
    option = max(options, key=lambda o: draws[o])
    means = ", ".join(f"{o} {sum(seen[o]) / len(seen[o]):.2f} ({len(seen[o])})" for o in options)
    return option, f"learned: {option} drew highest; mean reward {means}"


def per_run_evidence(scored: list[tuple[dict, float]], key: str) -> dict[str, list[float]]:
    """Each option's rewards, one per run: `scored` holds (audience row, reward) on any platform; each reward is
    taken against its platform's median first (platforms report on different scales), then a run counts once,
    the mean of its platforms. `key` names the row field holding the option."""
    from statistics import median
    level = {p: median([v for r, v in scored if r["platform"] == p]) for p in {r["platform"] for r, _ in scored}}
    per_run: dict = {}
    for r, v in scored:
        adjusted = min(1.0, max(0.0, 0.5 + v - level[r["platform"]]))
        per_run.setdefault(r.get("run_id") or id(r), (r[key], []))[1].append(adjusted)
    out: dict[str, list[float]] = {}
    for option, values in per_run.values():
        out.setdefault(option, []).append(sum(values) / len(values))
    return out
