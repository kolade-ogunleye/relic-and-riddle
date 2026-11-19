"""report.json -> a markdown digest: per surface the counts and runtime, then failures grouped by class with one
repro each (the first case of the group) and how many cases share it.

    python tools/stress/summary.py <out dir>/report.json > digest.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _sig(detail: str) -> str:
    """A failure's signature: its detail with the case-specific values taken out (quoted text, numbers, boxes)."""
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", detail.split("\n")[0])
    s = re.sub(r"\[[^\]]*\]", "[…]", s)
    s = re.sub(r"\d+(\.\d+)?", "#", s)
    return s[:160]


def digest(rep: dict) -> str:
    from surfaces import LANE
    out = [f"seed {rep['seed']}, {rep['cases']} cases, {rep['workers']} workers, wall {rep['wall_seconds']} s", ""]
    out.append("| surface | lane | cases | pass | refused | fail | s/case |")
    out.append("|---|---|---|---|---|---|---|")
    for s, v in sorted(rep["surfaces"].items()):
        out.append(f"| {s} | {LANE.get(s, '?')} | {v['cases']} | {v['passes']} | {len(v['refusals'])} | "
                   f"{len(v['failures'])} | {v['seconds'] / max(1, v['cases']):.1f} |")
    for s, v in sorted(rep["surfaces"].items()):
        if not v["failures"]:
            continue
        out += ["", f"### {s} (lane {LANE.get(s, '?')}): {len(v['failures'])} of {v['cases']} cases fail"]
        groups: dict[tuple, list] = {}
        for f in v["failures"]:
            for ff in f["failures"]:
                groups.setdefault((ff["cls"], _sig(ff["detail"])), []).append((f, ff))
        for (cls, sig), rows in sorted(groups.items(), key=lambda kv: (-len({r[0]['id'] for r in kv[1]}), kv[0])):
            f, ff = rows[0]
            n = len({r[0]["id"] for r in rows})
            out.append(f"- **{cls}** x{n} cases: {ff['detail'][:400]}")
            out.append(f"  repro `{f['repro']}` (classes {f['classes']})")
    refs = {s: v["refusal_classes"] for s, v in rep["surfaces"].items() if v["refusal_classes"]}
    if refs:
        out += ["", "### refusals (recorded, by class)"]
        for s, c in sorted(refs.items()):
            out.append(f"- {s}: " + ", ".join(f"{k} x{n}" for k, n in sorted(c.items(), key=lambda kv: -kv[1])))
    return "\n".join(out)


if __name__ == "__main__":
    print(digest(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
