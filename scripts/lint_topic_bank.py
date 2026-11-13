"""Offline editorial lint of the topic bank (QF-73; run in CI): every topic cites a public source, its hook passes
the opening rule, a key fact carries a number, a scale or a contrast, it has a region group and a sensitivity
assessment, no #ReelsViral, and no region group holds more than 40% of the bank. Exit 1 on any problem.

    python scripts/lint_topic_bank.py [path/to/topic_bank.json]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from src.topic_policy import lint_bank
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", nargs="?", type=Path, default=ROOT / "data/topics/topic_bank.json")
    args = parser.parse_args(argv)
    problems = lint_bank(json.loads(args.path.read_text(encoding="utf-8")))
    print(json.dumps({"passed": not problems, "problems": problems}, indent=2, ensure_ascii=False))
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
