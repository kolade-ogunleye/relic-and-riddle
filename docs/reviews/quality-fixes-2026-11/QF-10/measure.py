"""QF-10 acceptance: each picture check fails a deliberately broken master, through the real release gate.

    python measure.py <repo root>

Runs on any tree (on the base, which has no picture checks, it reports what the gate sees: nothing). Inputs are
committed: the generated fixtures of tests/test_qf10_picture_gate.py, the real Wieliczka house-graphic frame in
tests/fixtures/qf10, and the real_data*.json files beside this file (written by real_data.py: per-item results for the
development masters, counts only for the production-sample copy; this script only reads them). Writes nothing
outside a temporary folder; prints JSON.
"""

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from src import release

here = Path(__file__).resolve().parent
# The fixture generators come from this commit's regression test (make, still, the committed Wieliczka frame). On a
# tree without src.picture_qa (the base) a placeholder lets that module load; the gate measured is always <root>'s.
try:
    import src.picture_qa
except ImportError:
    import types
    sys.modules["src.picture_qa"] = types.ModuleType("src.picture_qa")
    import src
    src.picture_qa = sys.modules["src.picture_qa"]
spec = importlib.util.spec_from_file_location("qf10_fixtures", here.parents[3] / "tests/test_qf10_picture_gate.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

BROKEN = ("black", "freeze", "bars", "range", "colour")
rows, detected = [], 0
with tempfile.TemporaryDirectory(prefix="qf10-") as temp:
    temp = Path(temp)

    def at(name):            # one folder per case: the gate writes its release_report.json beside the master
        (temp / name).mkdir()
        return temp / name / "m.mp4"
    cases = [("good", fixtures.make(at("good"), "good"))]
    cases += [(kind, fixtures.make(at(kind), kind)) for kind in BROKEN]
    graphic = fixtures.WIELICZKA
    cases.append(("wieliczka_house_graphic", fixtures.still(at("graphic"), graphic)))
    cases.append(("wieliczka_letterboxed", fixtures.still(at("boxed"), graphic,
                                                          "scale=270:380,pad=270:480:0:50:black,")))
    cases.append(("halftone_codec_ringing", fixtures.halftone(at("halftone"))))
    cases.append(("wieliczka_full_range_tagged_limited", fixtures.full_range(at("full"), graphic)))
    # QF-52 essential image shown whole (fit_filter padding through the real grade, vignette and CRF 18) for 1.5 s,
    # then 1.5 s of true letterboxing; the composition record names the essential span.
    essential = fixtures.essential_then_letterboxed(at("essential"))
    fixtures.compose_record(essential, full_image_spans=[[0.0, 1.5]])
    cases.append(("essential_image_then_letterbox", essential))
    # A master made before QF-01 (tags unset) that the gate before QF-10 checked: a note, not a block.
    old = fixtures.pre_qf01(at("old"))
    fixtures.compose_record(old)
    fixtures.old_report(old)
    cases.append(("pre_qf01_gated_before_qf10", old))
    for name, master in cases:
        t0 = time.perf_counter()
        report = release.write_release_report({"master_video_path": str(master), "script_payload": {}},
                                              dry_run=True)
        picture = {c["name"]: c["status"] for c in report["checks"] if c["name"].startswith("picture_")}
        bars = next((c.get("intervals") for c in report["checks"] if c["name"] == "picture_bars"), None)
        rows.append({"fixture": name, "picture_checks": picture, "bars_intervals": bars,
                     "colour_grandfathered": any(c.get("grandfathered") for c in report["checks"]),
                     "seconds": round(time.perf_counter() - t0, 2)})

expect = {**{k: f"picture_{k}" for k in BROKEN}, "wieliczka_letterboxed": "picture_bars",
          "wieliczka_full_range_tagged_limited": "picture_range"}
CLEAN = ("good", "wieliczka_house_graphic", "halftone_codec_ringing")
caught = {name: next(r for r in rows if r["fixture"] == name)["picture_checks"].get(check) == "fail"
          for name, check in expect.items()}
clean = {name: (bool(r["picture_checks"]) and all(s == "pass" for s in r["picture_checks"].values()))
         for r in rows for name in [r["fixture"]] if name in CLEAN}
row = {r["fixture"]: r for r in rows}
essential_row = row["essential_image_then_letterbox"]
# The essential image's padding is not reported; the letterboxing after it is.
essential_ok = (essential_row["picture_checks"].get("picture_bars") == "fail"
                and all(i["start"] >= 1.5 for i in essential_row["bars_intervals"] or []))
old_row = row["pre_qf01_gated_before_qf10"]
grandfather_ok = old_row["picture_checks"].get("picture_colour") == "pass" and old_row["colour_grandfathered"]


def committed(name):
    path = here / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# real_data.py's committed runs: dev; production-sample copy (counts only); design-card renders; one-core timing.
real = committed("real_data.json")
prod = committed("real_data_prod.json").get("prod") or {}
cards = committed("real_data_cards.json")
single = committed("real_data_single_thread.json")


def failures(section, key):
    return [{"item": r[key], "kind": r.get("kind", "master"), "failed": r.get("failed"), "error": r.get("error")}
            for r in section if r.get("error") or any(n != "picture_colour" for n in (r.get("failed") or {}))]


dev = real.get("dev") or {}
result = {
    "scope": "generated broken masters + a real house-graphic frame through write_release_report; real-data "
             "results read from the committed real_data*.json",
    "fixtures": rows,
    "broken_detected": f"{sum(caught.values())}/{len(caught)}",
    "caught": caught,
    "clean_pictures_pass": clean,
    # The gate holds no reference picture, so it must not print an SSIM verdict (review 3, finding 1).
    "ssim_check_reported": any("picture_ssim" in r["picture_checks"] for r in rows),
    "essential_image_padding_not_bars_letterbox_after_it_caught": essential_ok,
    "pre_qf01_master_gated_before_qf10_noted_not_blocked": grandfather_ok,
    "real_data": {
        "method": real.get("method"),
        "development": dev.get("summary"),
        "development_failures_other_than_colour_tags": {
            k: failures(dev.get(k) or [], {"masters": "master", "graphics": "clip", "stills": "still"}[k])
            for k in ("masters", "graphics", "stills")},
        "production_sample_copy": prod.get("summary"),
        "production_items_failed_other_than_colour_tags": prod.get(
            "items_failed_other_than_colour_tags"),
        "production_measurement_errors": prod.get("measurement_errors"),
        "design_cards": (cards.get("cards") or {}).get("summary"),
        "design_card_failures": failures((cards.get("cards") or {}).get("rows") or [], "card"),
        "single_thread": single.get("single_thread_summary"),
    },
}
result["acceptance_met"] = (all(caught.values()) and all(clean.values()) and len(clean) == len(CLEAN)
                            and not result["ssim_check_reported"] and essential_ok and grandfather_ok)
sys.stdout.reconfigure(newline="\n")   # LF when redirected to a file on Windows
print(json.dumps(result, indent=1))
