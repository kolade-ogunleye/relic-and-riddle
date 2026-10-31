"""Exports this checkout's step-1 work (fact sheets and the scripts checked against them) into the script library,
for a release to carry to the other environment (operator, 2026-10-27: production should not pay again for research
and scripts development already made and checked). Voice, music and clips are never exported.

    python scripts/export_script_library.py TOPIC_ID [TOPIC_ID ...]

For each topic: the fact sheet in data/fact_sheets, and the newest run's verified script that passed the fact check
against exactly that sheet, under today's check rules, and today's script checks. A topic without such a script
exports its fact sheet alone. An existing library entry is replaced only with --replace.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import OUTPUT_DIR, PipelineCoordinator  # noqa: E402
from src import config, script_library  # noqa: E402
from src.fact_evidence import current_fact_review  # noqa: E402


def newest_checked_script(topic_id: str):
    runs = sorted(OUTPUT_DIR.glob(f"run_{topic_id}_*"), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in runs:
        try:
            saved = json.loads((d / "verified_script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(saved, dict) and saved.get("guardrail_passed") and current_fact_review(saved)
                and PipelineCoordinator._passes_today(saved, d.name)):
            return d.name, saved
    return None, None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("topics", nargs="+")
    parser.add_argument("--replace", action="store_true", help="Replace an existing library entry")
    args = parser.parse_args(argv)
    failed = 0
    for tid in args.topics:
        sheet = config.DATA_DIR / "fact_sheets" / f"{tid}.json"
        if not sheet.is_file():
            print(f"{tid}: no fact sheet here; nothing exported")
            failed += 1
            continue
        if script_library.entry_dir(tid).exists() and not args.replace:
            print(f"{tid}: already in the library (use --replace)")
            continue
        run_id, script = newest_checked_script(tid)
        source = {"topic_id": tid, "run_id": run_id, "script_engine": config.SCRIPT_ENGINE,
                  "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "contains": ["fact_sheet"] + (["verified_script"] if script else [])}
        script_library.export(tid, sheet, script, source)
        print(f"{tid}: fact sheet" + (f" and the script of {run_id}" if script else " only (no script checked against it)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
