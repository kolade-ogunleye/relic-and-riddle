"""QF-96 measurement: the three test orderings of the README, each in its own pytest process, at <repo root>.
Prints JSON, writes nothing (pytest runs with -p no:cacheprovider). No network.

    python docs/reviews/quality-fixes-2026-11/QF-96/measure.py <repo root>
"""
import json
import re
import subprocess
import sys
from pathlib import Path

TARGET = "tests/test_integrity_gates.py::test_logs_persist_to_a_masked_rotating_file"
ORDERINGS = {
    "script_library_then_integrity": ["tests/test_script_library.py", TARGET],
    "regenerate_reason_then_integrity": ["tests/test_regenerate_reason_2026_10_28.py", TARGET],
    "qf96_regression_test": ["tests/test_qf96_root_logging.py"],
}


def counts(root: Path, paths: list[str]) -> dict:
    if not all((root / p.split("::")[0]).is_file() for p in paths):
        return {"n/a": "file not at this root"}
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths], cwd=root, check=False,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = [line for line in run.stdout.splitlines() if re.search(r"\d+ (passed|failed)", line)][-1]
    return {kind: int(n) for n, kind in re.findall(r"(\d+) (passed|failed|error|errors|skipped)", last)}


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    print(json.dumps({name: counts(root, paths) for name, paths in ORDERINGS.items()}, indent=1, sort_keys=True))
