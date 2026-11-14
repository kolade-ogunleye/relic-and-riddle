"""Re-run one case of the fixed sweep alone, through measure.py's own worker and checker.

usage: remeasure_one.py REPOSITORY_ROOT SITE_NUMBER WIDTH HEIGHT

SITE_NUMBER is 1-based as in the sweep's case names (site-066 is 66). Uses the same hash-bound inputs, the same
offline environment and the same render_one as measure.py, in a single process. Prints the case result as JSON and
writes nothing in the repository.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(root: Path, number: int, width: int, height: int) -> None:
    spec = importlib.util.spec_from_file_location("qf20_measure", HERE / "measure.py")
    measure = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = measure
    spec.loader.exec_module(measure)
    sites = json.loads((HERE / "sweep-sites.json").read_text(encoding="utf-8"))["sites"]
    with tempfile.TemporaryDirectory(prefix="qf20-one-") as temporary:
        scratch = Path(temporary)
        measure.unpack_inputs(scratch / "geo")
        measure.init_worker(str(root.resolve()), str(scratch))
        result = measure.render_one(("map", number - 1, sites[number - 1], (width, height)))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: remeasure_one.py REPOSITORY_ROOT SITE_NUMBER WIDTH HEIGHT")
    main(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
