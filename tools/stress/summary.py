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
    sys.stdout.reconfigure(encoding="utf-8")
    print(digest(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))


def modes_table(parts: dict[str, list[dict]]) -> str:
    """Failure classes per surface, per mode, with the REACHABLE / GUARDED split of the failing cases and one
    real-run-derived repro (a mutation case) where one exists. `parts`: mode -> results (report.partial.jsonl rows,
    each with its "reach" tag; an edge run re-tagged by realism.py retag carries it too)."""
    from surfaces import LANE
    rows: dict[tuple[str, str], dict] = {}
    for mode, results in parts.items():
        for r in results:
            if r.get("status") != "fail":
                continue
            tagged = (r.get("reach") or {}).get("tag", "UNTAGGED")
            data = (r.get("reach") or {}).get("data_tag", "UNTAGGED")
            for cls in sorted({f["cls"] for f in r.get("failures") or []}):
                row = rows.setdefault((r["surface"], cls), {})
                cell = row.setdefault(mode, {"n": 0, "REACHABLE": 0, "GUARDED": 0, "data_reachable": 0})
                cell["n"] += 1
                cell[tagged] = cell.get(tagged, 0) + 1
                cell["data_reachable"] += data == "REACHABLE"
                if mode == "mutation" and "repro" not in row:
                    row["repro"] = f"{r['id']} ({r.get('real_run')}: {r.get('mutation')})"
    modes = list(parts)
    out = ["| surface | lane | class | " + " | ".join(f"{m} fail (R/G; data-R)" for m in modes) +
           " | real-run repro |", "|---|---|---|" + "---|" * len(modes) + "---|"]
    for (surface, cls), row in sorted(rows.items()):
        cells = []
        for m in modes:
            c = row.get(m)
            cells.append(f"{c['n']} ({c.get('REACHABLE', 0)}/{c.get('GUARDED', 0)}; {c['data_reachable']})" if c
                         else "0")
        out.append(f"| {surface} | {LANE.get(surface, '?')} | {cls} | " + " | ".join(cells) +
                   f" | {row.get('repro', '-')} |")
    return "\n".join(out)
