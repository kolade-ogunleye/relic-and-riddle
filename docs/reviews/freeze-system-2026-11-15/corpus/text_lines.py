"""Corpus proof, TEXT-01 / TEXT-02 / TEXT-03 (freeze cycle 5): the ONE breaking policy over every heading and body of
every dev run.

    python text_lines.py [--tree <tree>] [--dev <dev checkout>] [--data <collected.json>] [--quiet]

Measures, per drawn text (text_collect.py sets it by the tree's own layout calls; src/line_policy.faults judges it):
- TEXT-01  never_end / function_word_end: a line other than the last ends on an article, preposition, conjunction or
           another closed-class word;
- TEXT-02  unit_split: a line break inside a keep-together group (a number and its unit or scale word, a hedge and
           its figure, a date, an era, a range's far end);
- TEXT-03  lone_last_row: a one-word last row of a heading under the 0.55 balance minimum.

A violation PASSES only when it is RECORDED as the fallback the policy allows where no legal break exists: the
heading engine's own record (Form.record: conjunction_end, locked_last_resort, lone_last_resort, phrase_exception) or
the body breaker's note (line_policy.note: "no legal break at this size and width"). Prints one row per text with a
violation (run, surface, measure, pass/fail), the totals per class, and exits 1 on any violation that is neither
fixed nor recorded. --tree runs the collector on another tree (the base) and judges it with THIS tree's policy.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from src import line_policy  # noqa: E402

CLASS = {"never_end": "TEXT-01", "function_word_end": "TEXT-01", "unit_split": "TEXT-02", "lone_last_row": "TEXT-03"}
HEADING_RECORDS = {"function_word_end": ("conjunction_end", "conjunction_end_last_resort"),
                   "unit_split": ("keep_together_last_resort",),
                   "lone_last_row": ("locked_last_resort", "lone_last_resort", "phrase_exception", "first_row_lone")}


def _arg(flag: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def collect(tree: Path, dev: Path, only: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "collected.json"
        subprocess.run([sys.executable, str(HERE / "text_collect.py"), str(tree), str(dev), str(out), "--only", only],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return json.loads(out.read_text(encoding="utf-8"))


def recorded(row: dict, fault: dict) -> bool:
    if row.get("heading"):
        rec = row.get("record") or {}
        return any(rec.get(k) for k in HEADING_RECORDS.get(fault["fault"], ()))
    return any(n.get("lines") == row["lines"] and any(f.get("fault") == fault["fault"] for f in n.get("faults", []))
               for n in row.get("notes") or [])


def judge(data: dict) -> tuple[list[tuple], collections.Counter]:
    table, tot = [], collections.Counter()
    for run, v in sorted(data["runs"].items()):
        for row in v.get("lines") or []:
            tot["texts"] += 1
            if row.get("refused"):
                tot["refused (recorded by the renderer)"] += 1
                continue
            for f in line_policy.violations(row["lines"], row.get("widths"), heading=bool(row.get("heading"))):
                ok = recorded(row, f)
                cls = CLASS[f["fault"]]
                tot[f"{cls} {f['fault']}"] += 1
                tot[f"{cls} {'recorded' if ok else 'UNRECORDED'}"] += 1
                table.append((run, row["surface"], f"{f['fault']}: {' / '.join(row['lines'])}",
                              "pass (recorded)" if ok else "FAIL"))
    return table, tot


def main() -> int:
    dev = Path(_arg("--dev", r"C:\Users\omokolade\GitHub\relic-and-riddle"))
    data = (json.loads(Path(_arg("--data")).read_text(encoding="utf-8")) if _arg("--data")
            else collect(Path(_arg("--tree", str(ROOT))), dev, "lines"))
    table, tot = judge(data)
    if "--quiet" not in sys.argv:
        print("run | surface | measure | result")
        for row in table:
            print(" | ".join(row))
    print(f"\ntree: {data.get('tree')}")
    for k in sorted(tot):
        print(f"{k}: {tot[k]}")
    bad = sum(v for k, v in tot.items() if k.endswith("UNRECORDED"))
    print(f"unrecorded violations: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
