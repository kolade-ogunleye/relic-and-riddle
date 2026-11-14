"""QF-49 acceptance metric: operator and infrastructure identifiers in the tracked tree.

    python docs/reviews/quality-fixes-2026-11/QF-49/measure.py <repo root>

Runs the row's own grep (git grep -Il over the four patterns, case-sensitive, as section 9.2 states it) and the
regression test's wider denylist (tests/test_no_operator_identifiers.py: case-insensitive, plus the production host's
short name and the lab jump host) over the tracked tree of <repo root>, and counts what is left outside the test's
attribution allowlist (taken from this checkout's test, so a before and an after are judged by the same list).
Prints the metric as JSON and writes measurements.json beside this script. Every path printed is repo-relative.
Each pattern carries one bracketed letter so this file does not match itself.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROW_PATTERNS = ("omokolad[e]", r"lab\.internal", "kolade-ogunley[e]", r"@gmail\.com")


def _grep(root: Path, args: list[str], patterns) -> list[str]:
    cmd = ["git", "-C", str(root), "grep", "--full-name", *args]
    for p in patterns:
        cmd += ["-e", p]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if out.returncode not in (0, 1):
        raise SystemExit(out.stderr)
    return out.stdout.splitlines()


def _test_module():
    spec = importlib.util.spec_from_file_location("_qf49_test", HERE.parents[3] / "tests" / "test_no_operator_identifiers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def measure(root: Path) -> dict:
    test = _test_module()
    allowed_files = {p for p, _ in test.ALLOW}
    row_files = _grep(root, ["-IlE"], ROW_PATTERNS)
    lines = []
    for line in _grep(root, ["-nIiE"], test.DENY):
        path, number, text = line.split(":", 2)
        lines.append((path, int(number), text.strip()))
    outside = [(p, n) for p, n, t in lines if (p, t) not in test.ALLOW]
    per_pattern = {p: len(_grep(root, ["-IlE"], [p])) for p in ROW_PATTERNS}
    top = {}
    for p in row_files:
        key = "/".join(p.split("/")[:2])
        top[key] = top.get(key, 0) + 1
    return {
        "row_grep_files": len(row_files),
        "row_grep_files_outside_allowlist": len([p for p in row_files if p not in allowed_files]),
        "row_grep_files_by_pattern": per_pattern,
        "row_grep_files_by_folder": dict(sorted(top.items(), key=lambda kv: -kv[1])),
        "denylist_lines": len(lines),
        "denylist_lines_outside_allowlist": len(outside),
        "denylist_files_outside_allowlist": sorted({p for p, _ in outside}),
        "allowlisted_lines": sorted(f"{p}:{n}" for p, n, t in lines if (p, t) in test.ALLOW),
        "regression_test_in_suite": (root / "tests" / "test_no_operator_identifiers.py").is_file(),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py <repo root>")
    result = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(result, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
