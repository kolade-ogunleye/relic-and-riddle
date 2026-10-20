"""Quarantine an interrupted upload locally; never upload or notify anyone."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.db import PipelineDatabase, DB_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--older-workers-stopped", action="store_true", required=True,
                        help="Confirm workers predating OS upload locks have been stopped")
    args = parser.parse_args()
    db = PipelineDatabase(db_path=args.database)
    changed = db.recover_interrupted_publication(args.run_id)
    print("Outcome unknown: check platform accounts before confirming posted/not posted."
          if changed else "No interrupted publishing claim was changed.")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
