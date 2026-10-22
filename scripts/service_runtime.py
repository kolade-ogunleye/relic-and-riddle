"""Stable application entry point; OS identity and process supervision stay external."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("contract", "pipeline", "backup-check", "backup-cycle", "backup-restore"))
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--snapshot-id", default="")
    args = parser.parse_args(argv)
    from src.service_runtime import CONTRACT_VERSION, ServiceSettings, execute
    if args.command == "contract":
        print(json.dumps({"contract_version": CONTRACT_VERSION,
                          "operations": ["pipeline", "backup-check", "backup-cycle", "backup-restore"]}))
        return 0
    if not args.settings:
        parser.error("--settings is required for runtime operations")
    try:
        settings = ServiceSettings.load(args.settings)
        result, code = execute(args.command, settings, snapshot_id=args.snapshot_id)
        print(json.dumps(result, indent=2))
        return code
    except Exception as exc:
        # SDK exceptions may contain token-bearing URLs. Host monitors only need
        # the stable state and exception type, never raw network/credential data.
        print(json.dumps({"contract_version": CONTRACT_VERSION, "state": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
