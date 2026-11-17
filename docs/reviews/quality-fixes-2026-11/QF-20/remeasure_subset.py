"""Re-measure a subset of measure.py's cases (same inputs, worker and checker), 4 processes; prints JSON, writes nothing.

usage: remeasure_subset.py REPOSITORY_ROOT SITE_NUMBERS ROUTE_INDEXES   (1-based sites, 0-based routes, comma-separated)
"""
import importlib.util
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sites_wanted = [int(x) for x in sys.argv[2].split(",")]
routes_wanted = [int(x) for x in sys.argv[3].split(",")]
here = root / "docs/reviews/quality-fixes-2026-11/QF-20"
spec = importlib.util.spec_from_file_location("qf20_measure", here / "measure.py")
measure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = measure
spec.loader.exec_module(measure)

if __name__ == "__main__":
    sites = json.loads((here / "sweep-sites.json").read_text(encoding="utf-8"))["sites"]
    routes = json.loads((here / "route-cases.json").read_text(encoding="utf-8"))["routes"]
    jobs = [("map", n - 1, sites[n - 1], size) for n in sites_wanted for size in measure.SIZES]
    jobs += [("route", i, routes[i], size) for i in routes_wanted for size in measure.SIZES]
    with tempfile.TemporaryDirectory(prefix="qf20-subset-") as t:
        scratch = Path(t)
        measure.unpack_inputs(scratch / "geo")
        with ProcessPoolExecutor(max_workers=4, initializer=measure.init_worker, initargs=(str(root), str(scratch))) as pool:
            results = list(pool.map(measure.render_one, jobs, chunksize=1))
    print(json.dumps(results, indent=1))
