"""Run the fixed 200-site locator sweep and the route sweep from committed, hash-bound inputs."""

import gzip
import hashlib
import json
import lzma
import os
import shutil
import socket
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = HERE.parents[3] / "data/geo/benchmark-inputs"
SIZES = ((1080, 1920), (1920, 1080))


def _external_input(record):
    """A manifest input kept out of the repository (the 233 MB shaded relief): read from its fetched path under the
    repository root, refused with the fetch instruction when missing, and refused when its size or SHA-256 differs
    from the manifest. Never skipped."""
    source = (INPUTS.parents[2] / record["external_path"]).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Benchmark input missing: {record['external_path']} ({record['path']}, sha256 "
                                f"{record['sha256']}). Fetch it with: {record['fetch']}")
    digest = hashlib.sha256()
    with open(source, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    if source.stat().st_size != record["bytes"] or digest.hexdigest() != record["sha256"]:
        raise ValueError(f"Benchmark input differs from the manifest: {record['external_path']} (sha256 "
                         f"{digest.hexdigest()}, {source.stat().st_size} bytes; expected {record['sha256']}, "
                         f"{record['bytes']} bytes). Fetch it again with: {record['fetch']}")
    return source


def unpack_inputs(destination):
    records = json.loads((INPUTS / "manifest.json").read_text(encoding="utf-8"))
    for record in records:
        if record.get("external_path"):
            source = _external_input(record)
            target = (destination / record["path"]).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Input path leaves benchmark directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            continue
        packed = (INPUTS / record["file"]).read_bytes()
        if len(packed) != record["packed_bytes"] or hashlib.sha256(packed).hexdigest() != record["packed_sha256"]:
            raise ValueError("Packed input differs: " + record["file"])
        raw = lzma.decompress(packed) if record["file"].endswith(".xz") else gzip.decompress(packed)
        if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Unpacked input differs: " + record["file"])
        target = (destination / record["path"]).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError("Input path leaves benchmark directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return records


def no_network(*_args, **_kwargs):
    raise RuntimeError("The map benchmark is offline")


def init_worker(root, scratch):
    global GRAPHICS, TRUTH, OUTPUT, FRAME_HASHES
    root, scratch = Path(root), Path(scratch)
    # Application configuration sees only a generated empty environment/home.
    # Committed fonts and the exact source raster are copied, never linked.
    keep = {key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    os.environ.clear()
    os.environ.update(keep)
    app = scratch / ("worker-" + str(os.getpid()))
    app.mkdir()
    os.environ.update(RELIC_APP_ROOT=str(app), USERPROFILE=str(app), FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    shutil.copytree(root / "fonts", app / "fonts")
    (app / "data").mkdir()
    shutil.copyfile(root / "data/reference_facts.json", app / "data/reference_facts.json")
    socket.create_connection = no_network
    socket.socket.connect = no_network
    sys.path.insert(0, str(root))
    from src import geo, graphics
    from tests import geo_truth
    data = scratch / "geo"
    geo.COUNTRIES_FILE = data / "ne_50m_admin_0_countries.geojson"
    geo.COUNTRIES_10M_FILE = data / "ne_10m_admin_0_map_subunits.geojson"
    geo.MARINE_FILE = data / "ne_10m_geography_marine_polys.geojson"
    geo.ICE_SHELVES_FILE = data / "ne_50m_antarctic_ice_shelves_polys.geojson"
    geo.RELIEF_HR_FILE = geo.RELIEF_FILE = data / "relief/SR_HR.tif"
    geo.RELIEF_50M_FILE = data / "relief/not-supplied.tif"
    # Change only where the independent checker obtains its unchanged raw inputs.
    geo_truth.SUBUNITS = geo.COUNTRIES_10M_FILE
    geo_truth.MARINE = geo.MARINE_FILE
    geo_truth.SHELVES = geo.ICE_SHELVES_FILE
    graphics.VIDEO_FPS = 1
    graphics._encode = capture_frames
    # This sweep measures geographical placement, as the original sweep does.
    # It does not claim graded contrast or independent aesthetic qualification.
    graphics.graded_frame = lambda *_args, **_kwargs: None
    GRAPHICS, TRUTH, OUTPUT, FRAME_HASHES = graphics, geo_truth, app / "output", {}


def capture_frames(frames, path, _count, width=1080, height=1920):
    last = None
    count = 0
    for frame in frames:
        last = frame
        count += 1
    if last is None or last.size != (width, height):
        raise ValueError("Renderer produced no final frame at the requested size")
    FRAME_HASHES[str(path)] = {"frames": count, "sha256": hashlib.sha256(last.convert("RGB").tobytes()).hexdigest()}
    return path


def located(stops):
    """Offline geocoder for a route case: each stop at its committed coordinates."""
    table = {stop["place"]: stop for stop in stops}

    def locate(topic):
        stop = table[topic["location"]["name"]]
        return {"lat": stop["lat"], "lon": stop["lon"], "country": stop["country"], "label": stop["place"],
                "approximate": False, "qid": "", "matched": "name"}
    return locate


def render_one(job):
    kind, index, case, size = job
    width, height = size
    if kind == "route":
        key = f"route-{case['name']}-{width}x{height}"
    else:
        key = f"site-{index + 1:03d}-{width}x{height}"
    path = OUTPUT / (key + ".mp4")  # identifier only; capture_frames writes no video
    try:
        if kind == "route":
            GRAPHICS.geo.locate = located(case["stops"])
            stops = [{"place": stop["place"], "country": stop["country"], "label": stop["label"]}
                     for stop in case["stops"]]
            drawn = GRAPHICS.render_route(stops, path, width=width, height=height, narration=case["narration"])
        else:
            place = {"lat": case["latitude"], "lon": case["longitude"], "country": case["country"],
                     "label": case["label"], "approximate": False}
            drawn = GRAPHICS.render_map(place, path, width=width, height=height)
        if drawn is None:
            raise RuntimeError("Map was not rendered")
        layout = json.loads(Path(str(path) + ".layout.json").read_text(encoding="utf-8"))
        faults = list(TRUTH.faults(layout))
        if kind == "map" and layout.get("country") is None and not layout.get("home_drop"):
            faults.append("home dropped without a reason")
        return {"case": key, "kind": kind, "faults": faults, "home_drop": layout.get("home_drop", ""),
                "frame": FRAME_HASHES.pop(str(path))}
    except Exception as exc:  # noqa: BLE001 -- each exception is an explicit failed case in the result
        return {"case": key, "kind": kind, "faults": [type(exc).__name__ + ": " + str(exc)]}


def main(root):
    root = root.resolve()
    layer_hashes = {}
    if (root / "src/map_layers.py").is_file():
        layer_root = root / "data/geo"
        layers = json.loads((layer_root / "natural-earth-manifest.json").read_text(encoding="utf-8"))
        for layer in layers:
            name = layer["stem"] + ".geojson"
            digest = hashlib.sha256((layer_root / name).read_bytes()).hexdigest()
            if digest != layer["geojson_sha256"]:
                raise ValueError("Committed layer differs: " + name)
            layer_hashes[name] = digest
    sites = json.loads((HERE / "sweep-sites.json").read_text(encoding="utf-8"))["sites"]
    if len(sites) != 200:
        raise ValueError("The fixed sweep must contain all 200 sites")
    routes = json.loads((HERE / "route-cases.json").read_text(encoding="utf-8"))["routes"]
    with tempfile.TemporaryDirectory(prefix="qf20-measure-") as temporary:
        scratch = Path(temporary)
        records = unpack_inputs(scratch / "geo")
        jobs = [("map", index, site, size) for index, site in enumerate(sites) for size in SIZES]
        jobs += [("route", index, case, size) for index, case in enumerate(routes) for size in SIZES]
        with ProcessPoolExecutor(max_workers=4, initializer=init_worker, initargs=(str(root), str(scratch))) as pool:
            results = []
            for result in pool.map(render_one, jobs, chunksize=1):
                results.append(result)
                if len(results) % 20 == 0:
                    print(f"Geography sweep: {len(results)}/{len(jobs)}", file=sys.stderr, flush=True)
    sources = ["src/geo.py", "src/graphics.py", "src/map_geometry.py", "src/map_layers.py",
               "src/config.py", "src/safe_zones.py", "src/timeline_dense.py", "tests/geo_truth.py"]
    maps = [row for row in results if row["kind"] == "map"]
    drawn_routes = [row for row in results if row["kind"] == "route"]
    output = {"sites": len(sites), "renders": len(maps), "passed": sum(not row["faults"] for row in maps),
              "route_cases": len(routes), "route_renders": len(drawn_routes),
              "route_passed": sum(not row["faults"] for row in drawn_routes),
              "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                for name in sources if (root / name).is_file()},
              "input_sha256": {row["path"]: row["sha256"] for row in records} | layer_hashes,
              "sweep_sha256": hashlib.sha256((HERE / "sweep-sites.json").read_bytes()).hexdigest(),
              "route_cases_sha256": hashlib.sha256((HERE / "route-cases.json").read_bytes()).hexdigest(),
              "independent_design_review": "pending; not assessed by this geography measurement",
              "results": results}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py REPOSITORY_ROOT")
    main(Path(sys.argv[1]))
