"""QF-88 real-data run: the three gates over every real master, graphic, AI clip and footage segment.

    python real_data.py <repo root> --set <label>=<output dir> [--set ...] [--assets <assets dir>] [--counts <label>]

For each set (dev output, or a COPY of the production sample's output; never the original):
- masters (run_*/*_master.mp4, *_16x9.mp4): copied to a temporary folder with their composition record, release
  report, palette and graphics stills. The release gate runs twice: first as QF-10b shipped it (picture_qa
  METHOD_VERSION 1, no flash analyser), then as this row ships it (METHOD_VERSION 2). Kept: the second check's
  picture_flash, whether QF-10b's old-master note was given on each check, any other picture block, and the
  caption_rate / graphic_colour_vision / graphic_contrast checks;
- graphic clips (graphics/graphic_*.mp4): picture_qa.measure, picture_flash;
- AI motion clips and footage segments: picture_qa.ingest_problems, as motion._gate and footage take them in;
- captions (*_captions.ass): every burst's delivered rate (plate show to hide) against its own words' spoken rate
  (words.json, matched in order), the rule video_composer.reading_rates applies; a master rendered before QF-88 has
  no reading_rate record, so this is how its captions are measured;
- graphics stills (graphics/graphic_*.jpg) and other stills (stories, carousel, covers): colour_vision.check with
  the run's accent (palette.json), so photos are a stress set for false role matches.
`--assets`: operator footage (<topic>/footage/**/*.mp4) cut as footage.make_segment cuts it, then ingest_problems.
A set named in `--counts` is reported as counts and medians only (decisions PROD-SAMPLE-counts-only). Prints JSON
(LF); writes only under the system temp folder.
"""

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("root", type=Path)
ap.add_argument("--set", action="append", default=[])
ap.add_argument("--assets", type=Path)
ap.add_argument("--counts", action="append", default=[])
ap.add_argument("--parts", default="masters,graphics,ingest,captions,cvd", help="comma-separated parts to run")
args = ap.parse_args()
root = args.root.resolve()
sys.path.insert(0, str(root))
from src import colour_vision, config, picture_qa, release

STILLS = ("stories/story_*.jpg", "carousel/*.jpg", "thumbnail_cover.jpg", "thumbnail_profile_grid.jpg")


def _err(exc: Exception) -> str:
    text = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*[\\/]([^\\/\s'\"]+)", r"\1", str(exc))
    return f"{type(exc).__name__}: {text}"[:200]


def gate(master: Path, version: int) -> dict:
    keep_version, keep_analysers = picture_qa.METHOD_VERSION, picture_qa.analysers
    if version == 1:   # QF-10b as shipped: no flash analyser, method 1
        picture_qa.METHOD_VERSION = 1
        picture_qa.analysers = lambda deep_dive, exempt_bars=(): [
            a for a in keep_analysers(deep_dive, exempt_bars) if a.name != "picture_flash"]
    try:
        return release.write_release_report({"master_video_path": str(master), "script_payload": {}}, dry_run=True)
    finally:
        picture_qa.METHOD_VERSION, picture_qa.analysers = keep_version, keep_analysers


def master_row(master: Path, temp: Path) -> dict:
    run = temp / "run"
    shutil.rmtree(run, ignore_errors=True)
    run.mkdir()
    copy = run / master.name
    shutil.copy2(master, copy)
    for src, dst in ((master.with_suffix(".composition.json"), copy.with_suffix(".composition.json")),
                     (master.parent / "release_report.json", run / "release_report.json"),
                     (master.parent / "palette.json", run / "palette.json")):
        if src.is_file():
            shutil.copy2(src, dst)
    for still in master.parent.glob("graphics/graphic_*.jpg"):
        (run / "graphics").mkdir(exist_ok=True)
        shutil.copy2(still, run / "graphics" / still.name)
    before = (run / "release_report.json").read_text(encoding="utf-8") if (run / "release_report.json").is_file() else ""
    row = {"old_report": bool(before) and "picture_measurement" not in before}
    t0 = time.perf_counter()
    first = {c["name"]: c for c in gate(copy, 1)["checks"]}
    t1 = time.perf_counter()
    second = {c["name"]: c for c in gate(copy, 2)["checks"]}
    row["second_check_wall_s"] = round(time.perf_counter() - t1, 1)
    row["first_check_wall_s"] = round(t1 - t0, 1)
    row["noted"] = [bool((first.get("picture_colour") or {}).get("grandfathered")),
                    bool((second.get("picture_colour") or {}).get("grandfathered"))]
    row["picture_measurement"] = (second.get("picture_measurement") or {}).get("status")
    row["flash"] = (second.get("picture_flash") or {}).get("status")
    row["flash_detail"] = (second.get("picture_flash") or {}).get("detail")
    row["flash_held"] = (second.get("picture_flash") or {}).get("held") or []
    row["hold_sheet_bytes"] = [s["bytes"] for s in (second.get("picture_flash") or {}).get("contact_sheets") or []]
    row["other_picture_blocks"] = sorted(n for n, c in second.items() if n.startswith("picture_") and c["status"] != "pass"
                                         and n not in ("picture_colour", "picture_flash"))
    for name in ("caption_rate", "graphic_colour_vision", "graphic_contrast"):
        row[name] = (second.get(name) or {}).get("status")
    row["cvd_detail"] = (second.get("graphic_colour_vision") or {}).get("detail")
    measured = (second.get("picture_measurement") or {}).get("measurement") or {}
    row["duration_s"], row["measure_elapsed_s"] = measured.get("duration"), measured.get("elapsed_s")
    return row


def burst_rates(ass: Path) -> list:
    """(shown cps, spoken cps) of each delivered burst: the CapBox plate's show and hide, the burst's last CapText
    line, and the next words of words.json in order (as many as the burst shows)."""
    def t(s):
        h, m, x = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(x)
    boxes, texts = [], []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if parts[3] == "CapBox":
            boxes.append((t(parts[1]), t(parts[2])))
        elif parts[3].startswith("CapText"):
            texts.append((t(parts[1]), t(parts[2]), re.sub(r"\{[^}]*\}", "", parts[9]).replace(chr(92) + "N", " ")))
    words = json.loads((ass.parent / "words.json").read_text(encoding="utf-8"))
    words = words.get("words", []) if isinstance(words, dict) else words
    # a token with no letter or digit ("...", a dash) is not drawn as a caption word in the delivered files
    words = [w for w in words if re.search(r"\w", str(w.get("word", "")))]
    out, k = [], 0
    for show, hide in boxes:
        inside = [x for x in texts if x[0] >= show - 1e-3 and x[1] <= hide + 1e-3]
        if not inside or hide <= show:
            continue
        text = inside[-1][2].strip()
        n = len(text.split())
        ws = words[k:k + n]
        k += n
        if len(ws) < n or not ws:
            break
        end = float(ws[-1]["start"]) + float(ws[-1].get("dur") or 0.3)
        if k < len(words) and float(words[k]["start"]) - end <= config.CAPTION_PAUSE_GAP:
            end = max(end, float(words[k]["start"]))   # reading_rates: its words hold the narration until the next
        spoken = len(text) / (end - float(ws[0]["start"])) if end > float(ws[0]["start"]) else None
        out.append((len(text) / (hide - show), spoken, hide - show, end - float(ws[0]["start"])))
    return out


def survey(base: Path, temp: Path) -> dict:
    rows = {"masters": [], "graphics": [], "ingest": [], "captions": [], "cvd": []}
    parts = set(args.parts.split(","))
    masters = sorted(base.glob("run_*/*_master.mp4")) + sorted(base.glob("run_*/*_16x9.mp4"))
    masters = masters if "masters" in parts else []
    for i, m in enumerate(masters):
        try:
            rows["masters"].append({"item": m.relative_to(base).as_posix(), **master_row(m, temp)})
        except Exception as exc:  # noqa: BLE001 - recorded, never a pass
            rows["masters"].append({"item": m.relative_to(base).as_posix(), "error": _err(exc)})
        print(f"master {i + 1}/{len(masters)}: {rows['masters'][-1].get('flash')} "
              f"noted {rows['masters'][-1].get('noted')}", file=sys.stderr, flush=True)
    for clip in sorted(base.glob("run_*/graphics/graphic_*.mp4")) if "graphics" in parts else []:
        try:
            res = picture_qa.measure(clip)
            flash = next(c for c in res["checks"] if c["name"] == "picture_flash")
            rows["graphics"].append({"item": clip.relative_to(base).as_posix(), "flash": flash["status"]})
        except Exception as exc:  # noqa: BLE001
            rows["graphics"].append({"item": clip.relative_to(base).as_posix(), "error": _err(exc)})
    for clip in (sorted(base.glob("run_*/**/motion_*.mp4")) + sorted(base.glob("run_*/footage/footage_*.mp4"))
                 if "ingest" in parts else []):
        try:
            found = picture_qa.ingest_problems(clip)
            rows["ingest"].append({"item": clip.relative_to(base).as_posix(), "kind": clip.stem.split("_")[0],
                                   "problems": found})
        except Exception as exc:  # noqa: BLE001
            rows["ingest"].append({"item": clip.relative_to(base).as_posix(), "kind": clip.stem.split("_")[0],
                                   "error": _err(exc)})
    for ass in sorted(base.glob("run_*/*_captions.ass")) if "captions" in parts else []:
        try:
            rates = burst_rates(ass)
        except (OSError, ValueError, KeyError) as exc:
            rows["captions"].append({"item": ass.relative_to(base).as_posix(), "error": _err(exc)})
            continue
        if not rates:
            rows["captions"].append({"item": ass.relative_to(base).as_posix(), "bursts": 0})
            continue
        limit, slack = config.CAPTION_MAX_CPS, config.CAPTION_CPS_TIMING_SLACK_S
        rows["captions"].append({
            "item": ass.relative_to(base).as_posix(), "bursts": len(rates),
            "over_17": sum(r[0] > limit for r in rates), "over_20": sum(r[0] > 20 for r in rates),
            "spoken_over_17": sum(1 for r in rates if r[1] and r[1] > limit),
            "timing_defects": sum(1 for s, _, shown, spoken in rates if s > limit and shown < spoken - slack),
            "worst_cps": round(max(r[0] for r in rates), 1)})
    for run in sorted(base.glob("run_*")) if "cvd" in parts else []:
        try:
            accent = json.loads((run / "palette.json").read_text(encoding="utf-8")).get("accent")
        except (OSError, ValueError):
            accent = None
        stills = [("graphic", p) for p in sorted(run.glob("graphics/graphic_*.jpg"))]
        stills += [("still", p) for pattern in STILLS for p in sorted(run.glob(pattern))]
        for kind, p in stills:
            try:
                r = colour_vision.check(p, accent)
            except Exception as exc:  # noqa: BLE001
                rows["cvd"].append({"item": p.relative_to(base).as_posix(), "kind": kind, "error": _err(exc)})
                continue
            rows["cvd"].append({"item": p.relative_to(base).as_posix(), "kind": kind, "accent": accent,
                                "pass": r["pass"], "roles": r["roles"],
                                "worst_pair": min((p2["de00"][p2["worst_vision"]] for p2 in r["pairs"]), default=None),
                                "pairs": [p2["pair"] for p2 in r["pairs"]],
                                "nontext_short": [n["mark"] for n in r["nontext"] if not n["meets"]]})
    accents = sorted({tuple(json.loads(p.read_text(encoding="utf-8"))["accent"]) for p in base.glob("run_*/palette.json")})
    rows["accents"] = [{"accent": list(a), "problem": colour_vision.accent_problem(a),
                        "worst_vs_gold": min(colour_vision.pair_distance(a, colour_vision.ROLES["gold"]).values())}
                       for a in accents]
    return rows


def summary(rows: dict) -> dict:
    ms = [r for r in rows["masters"] if "error" not in r]
    old = [r for r in ms if r["old_report"]]
    caps = [r for r in rows["captions"] if r.get("bursts")]
    cvd_g = [r for r in rows["cvd"] if r["kind"] == "graphic" and "error" not in r]
    cvd_s = [r for r in rows["cvd"] if r["kind"] == "still" and "error" not in r]
    pairs = [r["worst_pair"] for r in cvd_g if r["worst_pair"] is not None]
    walls = [r["second_check_wall_s"] for r in ms]
    return {
        "masters": {"items": len(rows["masters"]), "errors": len(rows["masters"]) - len(ms),
                    "flash_fail": sum(r["flash"] == "fail" for r in ms),
                    "flash_held": sum(r["flash"] == "needs_review" for r in ms),
                    "hold_sheet_bytes_max": max((b for r in ms for b in r.get("hold_sheet_bytes") or []), default=None),
                    "measurement_fail": sum(r["picture_measurement"] != "pass" for r in ms),
                    "other_picture_blocks": sum(bool(r["other_picture_blocks"]) for r in ms),
                    "old_report_masters": len(old),
                    "noted_qf10b_then_qf88": sum(r["noted"] == [True, True] for r in old),
                    "noted_lost_on_remeasure": sum(r["noted"][0] and not r["noted"][1] for r in old),
                    "caption_rate_checks": sum(r["caption_rate"] is not None for r in ms),
                    "cvd_fail": sum(r["graphic_colour_vision"] == "fail" for r in ms),
                    "contrast_notes": sum(r["graphic_contrast"] is not None for r in ms),
                    "second_check_wall_s": {"median": statistics.median(walls), "max": max(walls)} if walls else None},
        "graphic_clips": {"items": len(rows["graphics"]), "errors": sum("error" in r for r in rows["graphics"]),
                          "flash_fail": sum(r.get("flash") == "fail" for r in rows["graphics"])},
        "ingest": {k: {"items": sum(r["kind"] == k for r in rows["ingest"]),
                       "errors": sum(r["kind"] == k and "error" in r for r in rows["ingest"]),
                       "rejected": sum(r["kind"] == k and bool(r.get("problems")) for r in rows["ingest"])}
                   for k in sorted({r["kind"] for r in rows["ingest"]})},
        "captions": {"masters": len(caps), "bursts": sum(r["bursts"] for r in caps),
                     "bursts_over_17": sum(r["over_17"] for r in caps), "bursts_over_20": sum(r["over_20"] for r in caps),
                     "bursts_spoken_over_17": sum(r["spoken_over_17"] for r in caps),
                     "masters_with_a_burst_over_17": sum(r["over_17"] > 0 for r in caps),
                     "timing_defects": sum(r["timing_defects"] for r in caps),
                     "masters_failed_by_the_gate": sum(r["timing_defects"] > 0 for r in caps),
                     "masters_noted": sum(r["over_17"] > 0 and r["timing_defects"] == 0 for r in caps),
                     "median_worst_cps": statistics.median([r["worst_cps"] for r in caps]) if caps else None},
        "cvd": {"graphics": len(cvd_g), "graphics_fail": sum(not r["pass"] for r in cvd_g),
                "graphics_with_a_pair": len(pairs), "worst_pair_de00_min": min(pairs) if pairs else None,
                "worst_pair_de00_median": statistics.median(pairs) if pairs else None,
                "stills": len(cvd_s), "stills_fail": sum(not r["pass"] for r in cvd_s),
                "graphics_with_a_contrast_note": sum(bool(r["nontext_short"]) for r in cvd_g),
                "errors": sum("error" in r for r in rows["cvd"]),
                "run_accents": len(rows["accents"]),
                "accents_rejected_at_source": sum(a["problem"] is not None for a in rows["accents"]),
                "accent_vs_gold_de00_min": min((a["worst_vs_gold"] for a in rows["accents"]), default=None)},
    }


out = {"method": picture_qa.method(), "parts": args.parts.split(","), "sets": {}}
with tempfile.TemporaryDirectory(prefix="qf88-real-") as temp:
    temp = Path(temp)
    for spec in args.set:
        label, path = spec.split("=", 1)
        rows = survey(Path(path), temp)
        entry = {"summary": summary(rows)}
        if label not in args.counts:
            entry["rows"] = rows
        out["sets"][label] = entry
    if args.assets and "ingest" in set(args.parts.split(",")):
        from src import footage
        rows = []
        for i, src in enumerate(sorted(args.assets.glob("*/footage/**/*.mp4"))):
            try:
                seg = footage.make_segment(src, footage.probe(src), temp / f"seg_{i}.mp4")
                rows.append({"item": f"{src.parent.name}/{src.name}", "problems": picture_qa.ingest_problems(seg)})
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                rows.append({"item": f"{src.parent.name}/{src.name}", "error": _err(exc)})
        out["operator_footage"] = {"rows": rows, "rejected": sum(bool(r.get("problems")) for r in rows),
                                   "errors": sum("error" in r for r in rows)}
sys.stdout.reconfigure(newline="\n")
print(json.dumps(out, indent=1))
