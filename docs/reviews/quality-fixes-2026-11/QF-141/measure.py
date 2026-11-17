"""QF-141 measurements: cover fields left as fragments by a withheld figure.

    python measure.py [<code root>]        the text sweep on this tree (or <code root>), as JSON (= after.json)
    python measure.py sweep <code root> <dev checkout> <prod sample or -> <out.json>
    python measure.py compare <fix root> <base sweep> <fix sweep> <base renders> <fix renders> <out dir>

The text sweep covers every cover copy source: the catalogue (the freeze-panel fixtures' covers and the script
library's payloads), every dev run with a verified_script.json (the main checkout's output/run_*, read only) and
the production sample's runs (C:/tmp/prod-export-data, read only; counted, never named or quoted). Per case the copy
is built as VisualEngine.generate_thumbnails builds it (badge, spoken units, figure_check, the reveal rule) and the
fields withhold_figure changes are kept. A fragment is cover_design.withheld_fragment (the fix's measurable
definition; the base is judged by the same function). With no arguments the sweep runs on this tree with the main
checkout and the production sample at their usual places and prints the summary.

compare writes before.json (the base sweep's summary), after.json (the fix's) and comparison.json: the changed
fields per case (production cases as counts only) and the renders of render_covers.py, per case and file byte and
pixel identity, and the fields each cover drew (its record's grid_proof texts).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

FIELDS = ("eyebrow", "line1", "line2", "tagline", "badge")
DRAWN = {"eyebrow": "eyebrow", "title_1": "line1", "title_2": "line2", "tagline": "tagline"}
HERE = Path(__file__).resolve().parent
PROD = Path(r"C:\tmp\prod-export-data")


def _main_checkout() -> Path:
    """The main checkout (QF_MAIN_CHECKOUT, else the folder of this worktree's common .git): its output/run_*."""
    if os.environ.get("QF_MAIN_CHECKOUT"):
        return Path(os.environ["QF_MAIN_CHECKOUT"])
    import subprocess
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=HERE,
                            capture_output=True, text=True, check=True).stdout.strip()
    return Path(common).parent


def sweep(code: Path, dev: Path, prod: Path | None) -> list[dict]:
    os.environ.update(FONT_SIZE="80", RR_FIXTURE_REPO=str(dev))
    os.chdir(code)
    sys.path.insert(0, str(code))
    sys.path.insert(0, str(code / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    import logging
    logging.disable(logging.WARNING)
    import fixtures as fx

    from src.cover_design import must_withhold, resolving_tagline, withhold_figure
    from src.visual_engine import (
        _in_spoken_units,
        cover_figure,
        extract_curiosity_badge,
        reveal_line,
    )
    cases = [("catalogue", c["topic"], fx.payload(c)) for c in fx.load("covers")]
    cases += [("library", Path(p).parent.name, json.loads(Path(p).read_text(encoding="utf-8")))
              for p in sorted(glob.glob(str(code / "data" / "script_library" / "*" / "verified_script.json")))]
    cases += [("dev", Path(p).parent.name, json.loads(Path(p).read_text(encoding="utf-8")))
              for p in sorted(glob.glob(str(dev / "output" / "run_*" / "verified_script.json")))]
    if prod and prod.is_dir():
        cases += [("prod", f"prod case {i + 1}", json.loads(Path(p).read_text(encoding="utf-8")))
                  for i, p in enumerate(sorted(glob.glob(str(prod / "output" / "run_*" / "verified_script.json"))))]
    rows = []
    for src, name, payload in cases:
        copy = dict(payload.get("thumbnail") or {})
        if not copy:
            rows.append({"src": src, "run": name, "copy": False})
            continue
        if "badge" not in copy:
            copy["badge"] = extract_curiosity_badge(payload)
        copy = _in_spoken_units(copy, payload)
        figure = cover_figure(copy, payload)
        if resolving_tagline(copy, reveal_line(payload)):
            copy = dict(copy, tagline="")
        withheld = must_withhold(figure)
        after = withhold_figure(copy, figure) if withheld else dict(copy)
        num = str((figure or {}).get("number") or "")
        num = num.rstrip("0").rstrip(".") if "." in num else num
        rows.append({"src": src, "run": name, "copy": True, "withheld": withheld,
                     # the withheld number in a field a cover draws (the badge is read, never drawn)
                     "figure_left": bool(withheld and num and any(
                         re.search(r"(?<![\d.,])" + re.escape(num) + r"(?![\d,]|\.\d)", str(after.get(k) or ""))
                         for k in DRAWN.values())),
                     "fields": {k: [str(copy.get(k) or ""), str(after.get(k) or "")] for k in FIELDS
                                if str(copy.get(k) or "") != str(after.get(k) or "")}})
    return rows


def summary(rows: list[dict], fragment) -> dict:
    out = {"cases": 0, "with_copy": 0, "withheld": 0, "figure_still_withheld": 0, "fields_changed": 0,
           "fields_dropped": 0, "fragments": 0, "fragments_in_drawn_fields": 0, "fragments_in_badge": 0,
           "by_source": {}}
    for r in rows:
        s = out["by_source"].setdefault(r["src"], {"cases": 0, "withheld": 0, "fragments": 0})
        out["cases"] += 1
        s["cases"] += 1
        if not r["copy"]:
            continue
        out["with_copy"] += 1
        if r["withheld"]:
            out["withheld"] += 1
            s["withheld"] += 1
            out["figure_still_withheld"] += not r["figure_left"]
        n = sum(bool(fragment(after)) for _, after in r["fields"].values())
        out["fields_changed"] += len(r["fields"])
        out["fields_dropped"] += sum(not after.strip() for _, after in r["fields"].values())
        out["fragments"] += n
        # the badge is read for a figure's qualifier and drawn on no surface; the other four fields are drawn
        badge = sum(bool(fragment(after)) for k, (_, after) in r["fields"].items() if k == "badge")
        out["fragments_in_badge"] += badge
        out["fragments_in_drawn_fields"] += n - badge
        s["fragments"] += n
    return out


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def compare(fix: Path, base_sweep: Path, fix_sweep: Path, base_r: Path, fix_r: Path, out: Path) -> None:
    sys.path.insert(0, str(fix))
    import numpy as np
    from PIL import Image

    from src.cover_design import must_withhold, withheld_fragment
    base, after = (json.loads(p.read_text(encoding="utf-8")) for p in (base_sweep, fix_sweep))
    changed = []
    for b, f in zip(base, after):
        assert (b["src"], b["run"]) == (f["src"], f["run"])
        if b.get("fields") or f.get("fields"):
            row = {"src": b["src"], "case": b["run"],
                   "fragments_before": [k for k, (_, v) in b["fields"].items() if withheld_fragment(v)],
                   "fragments_after": [k for k, (_, v) in f["fields"].items() if withheld_fragment(v)]}
            if b["src"] != "prod":       # the production sample's copy is not reproduced here
                row.update(copy={k: v[0] for k, v in b["fields"].items()},
                           before={k: v[1] for k, v in b["fields"].items()},
                           after={k: v[1] for k, v in f["fields"].items()})
            changed.append(row)
    renders = {"cases": 0, "errors_base": json.loads((base_r / "errors.json").read_text(encoding="utf-8")),
               "errors_fix": json.loads((fix_r / "errors.json").read_text(encoding="utf-8")),
               "without_withheld_figure": 0, "without_withheld_identical": 0, "without_withheld_differ": [],
               "drawn_fragments_before": 0, "drawn_fragments_after": 0, "withheld_cases": []}
    for d in sorted(p for p in base_r.glob("*/*") if p.is_dir()):
        rel = d.relative_to(base_r)
        e = fix_r / rel
        if not (d / "still_layout.json").is_file() or not (e / "still_layout.json").is_file():
            continue
        rec = {side: json.loads((p / "still_layout.json").read_text(encoding="utf-8"))["thumbnail_cover.jpg"]
               ["recipe"] for side, p in (("before", d), ("after", e))}
        renders["cases"] += 1
        files = {}
        for name in ("thumbnail_cover.jpg", "thumbnail_profile_grid.jpg", "youtube_thumbnail.jpg"):
            a, b2 = (np.asarray(Image.open(p / name).convert("RGB"), dtype=np.int16) for p in (d, e))
            files[name] = {"bytes_identical": _sha(d / name) == _sha(e / name),
                           "pixels_identical": bool(a.shape == b2.shape and (a == b2).all())}
        drawn, fragments = {}, []
        for side, r in rec.items():
            els = (r.get("grid_proof") or {}).get("elements") or {}
            drawn[side] = {k: v.get("text") for k, v in els.items()}
            for el, key in DRAWN.items():
                t = str((els.get(el) or {}).get("text") or "")
                if t and t.strip().upper() != str((r.get("copy") or {}).get(key) or "").strip().upper() \
                        and withheld_fragment(t):
                    fragments.append({"side": side, "field": key, "drawn": t})
        renders["drawn_fragments_before"] += sum(x["side"] == "before" for x in fragments)
        renders["drawn_fragments_after"] += sum(x["side"] == "after" for x in fragments)
        case = str(rel).replace("\\", "/")
        if must_withhold(rec["before"].get("figure")):
            renders["withheld_cases"].append({"case": case, "files": files, "drawn_fragments": fragments,
                                              "drawn": drawn})
        else:
            renders["without_withheld_figure"] += 1
            if all(v["pixels_identical"] and v["bytes_identical"] for v in files.values()):
                renders["without_withheld_identical"] += 1
            else:
                renders["without_withheld_differ"].append({"case": case, "files": files})
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("before.json", summary(base, withheld_fragment)),
                       ("after.json", summary(after, withheld_fragment)),
                       ("comparison.json", {"changed": changed, "renders": renders})):
        (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"before": summary(base, withheld_fragment), "after": summary(after, withheld_fragment),
                      "renders": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in renders.items()}},
                     indent=1))


def main(argv: list[str]) -> int:
    if len(argv) <= 1 and argv[:1] != ["sweep"]:
        code = Path(argv[0]).resolve() if argv else HERE.parents[3]
        rows = sweep(code, _main_checkout(), PROD)
        from src.cover_design import withheld_fragment
        print(json.dumps(summary(rows, withheld_fragment), indent=1))
    elif argv[0] == "sweep":
        rows = sweep(Path(argv[1]).resolve(), Path(argv[2]).resolve(), None if argv[3] == "-" else Path(argv[3]))
        Path(argv[4]).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                 newline="\n")
    else:
        compare(*(Path(x).resolve() for x in argv[1:7]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
