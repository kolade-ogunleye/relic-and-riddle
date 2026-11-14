"""Recompute the input-file hashes recorded in before.json and after.json from the committed inputs.

usage: recompute_input_hashes.py REPOSITORY_ROOT [--write]

Both sweeps ran with sweep-sites.json in a CRLF working copy; the committed file holds the same content in LF.
measure.py reads the file with json.loads, so the renders and every measured number are the same either way; only
the recorded sweep_sha256 named the CRLF bytes. This script hashes the committed files with measure.py's own
expression (sha256 of the file's bytes), prints old and new values, and with --write replaces only those two hash
fields (adding "input_hashes_recomputed" with the old values). No measured result is touched.
"""
import hashlib
import json
import sys
from pathlib import Path

FIELDS = {"sweep_sha256": "sweep-sites.json", "route_cases_sha256": "route-cases.json"}


def main(root: Path, write: bool) -> None:
    here = root / "docs/reviews/quality-fixes-2026-11/QF-20"
    want = {key: hashlib.sha256((here / name).read_bytes()).hexdigest() for key, name in FIELDS.items()}
    report = {"committed": want}
    for result in ("before.json", "after.json"):
        data = json.loads((here / result).read_text(encoding="utf-8"))
        old = {key: data.get(key) for key in FIELDS}
        report[result] = {"recorded": old, "matches": old == want}
        if write and old != want:
            data.setdefault("input_hashes_recomputed", {"previous": old,
                                                       "reason": "CRLF working copy at measurement time; "
                                                                 "same content as the committed LF file"})
            data.update(want)
            with open(here / result, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=1)
                f.write("\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--write"):
        raise SystemExit("usage: recompute_input_hashes.py REPOSITORY_ROOT [--write]")
    main(Path(sys.argv[1]).resolve(), len(sys.argv) == 3)
