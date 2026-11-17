"""QF-10b acceptance: a failed first QF-10 measurement no longer removes the operator's old-master note.

    python measure.py <repo root> [--real <output dir> ...]

Generated masters (the regression test's own fixtures) go through the real release gate. In each scenario the
first check fails to measure (its time limit is set to 0), and the second check measures normally. With `--real`,
every `run_*/*_master.mp4` under each directory is copied to a temporary folder, together with its composition
record and its release report as the gate before QF-10 wrote it, and the same two checks run there. Only counts
are printed, so a production-sample copy may be given (decisions/PROD-SAMPLE-counts-only). Nothing is written
outside the temporary folder. Prints JSON (LF).
"""

import argparse
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--real", type=Path, action="append", default=[])
args = ap.parse_args()
root = args.root.resolve()
sys.path.insert(0, str(root))
from src import config, release

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qf10_fixtures", here.parents[3] / "tests/test_qf10_picture_gate.py")
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def two_checks(master: Path) -> dict:
    """First check with no time to measure, then a normal one; the second check's picture_colour outcome."""
    base, per = config.PICTURE_QA_TIMEOUT_BASE_S, config.PICTURE_QA_TIMEOUT_PER_S
    config.PICTURE_QA_TIMEOUT_BASE_S, config.PICTURE_QA_TIMEOUT_PER_S = 0.0, 0.0
    try:
        first = release.write_release_report({"master_video_path": str(master), "script_payload": {}}, dry_run=True)
    finally:
        config.PICTURE_QA_TIMEOUT_BASE_S, config.PICTURE_QA_TIMEOUT_PER_S = base, per
    second = release.write_release_report({"master_video_path": str(master), "script_payload": {}}, dry_run=True)
    get = {c["name"]: c for c in second["checks"]}
    measured_first = next((c for c in first["checks"] if c["name"] == "picture_measurement"), {})
    colour = get.get("picture_colour") or {}
    return {"first_measurement": measured_first.get("status"), "colour": colour.get("status"),
            "noted": bool(colour.get("grandfathered")),
            "other_picture_blocks": sorted(n for n, c in get.items() if n.startswith("picture_")
                                           and n not in ("picture_colour", "picture_measurement")
                                           and c["status"] != "pass")}


rows = {}
with tempfile.TemporaryDirectory(prefix="qf10b-") as temp:
    temp = Path(temp)

    def case(name, kind="good", spans=None, old=True):
        (temp / name).mkdir()
        master = fx.pre_qf01(temp / name / "m.mp4", kind) if kind in ("good", "black") else fx.make(
            temp / name / "m.mp4", kind)
        fx.compose_record(master, **({"full_image_spans": spans} if spans is not None else {}))
        if old:
            fx.old_report(master)
        rows[name] = two_checks(master)

    case("gated_before_qf10")                       # the operator's case: must keep the note
    case("gated_before_qf10_black", kind="black")   # the note, but black still blocks
    case("never_gated", old=False)                  # no earlier report: blocked
    case("new_render", spans=[])                    # composition written by the QF-10 composer: blocked
    case("other_tags", kind="colour")               # not the pre-QF-01 set: blocked

    real = {}
    for i, base in enumerate(args.real):
        noted = blocked = failed_first = errors = other = 0
        for j, master in enumerate(sorted(Path(base).glob("run_*/*_master.mp4"))):
            run = temp / f"real{i}_{j}"
            run.mkdir()
            copy = run / master.name
            shutil.copy2(master, copy)
            comp = master.with_suffix(".composition.json")
            report = master.parent / "release_report.json"
            if not comp.is_file() or not report.is_file():
                errors += 1
                continue
            shutil.copy2(comp, copy.with_suffix(".composition.json"))
            shutil.copy2(report, run / "release_report.json")
            if "picture_measurement" in report.read_text(encoding="utf-8"):
                errors += 1        # not a report from the gate before QF-10
                continue
            r = two_checks(copy)
            failed_first += r["first_measurement"] == "fail"
            noted += r["noted"]
            blocked += r["colour"] == "fail"
            other += bool(r["other_picture_blocks"])
        real[f"set_{i + 1}"] = {"masters_checked": noted + blocked, "first_check_failed": failed_first,
                                "noted_after_failed_first_check": noted, "colour_blocked": blocked,
                                "other_picture_blocks": other, "skipped_no_record": errors}

expected = {"gated_before_qf10": True, "gated_before_qf10_black": True, "never_gated": False, "new_render": False,
            "other_tags": False}
result = {"scenarios": rows,
          "note_as_ruled": {k: rows[k]["noted"] == v for k, v in expected.items()},
          "black_still_blocks": rows["gated_before_qf10_black"]["other_picture_blocks"] == ["picture_black"],
          "real_masters": real}
result["acceptance_met"] = all(result["note_as_ruled"].values()) and result["black_still_blocks"] and all(
    r["first_measurement"] == "fail" for r in rows.values())
sys.stdout.reconfigure(newline="\n")
print(json.dumps(result, indent=1))
