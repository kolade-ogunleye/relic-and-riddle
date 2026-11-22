"""The portability board (cycle 3, lane BOARD; BC4 and the 2026-11-19 portability ruling: the second brand and a
non-history copy set on every surface, same invariants): one lane's freeze candidate drawn three ways, side by side.

    python board.py <tree> <dev> <out> --portability <jobs file> [--workers N]

<jobs file>: render_surfaces.py job names, one per line (as surfaces.json "seconds" keys; render_surfaces.py
--list-jobs prints them): the lane's candidate surfaces. Each is drawn by render_surfaces.py (through run_surfaces.py,
the board's exact memo) as
  - default: the default palette on the runs' own copy,
  - second: the second palette (QF-35a's test brand: every colour moved, another display face and masthead),
  - nonhistory: the default palette on the non-history stress copy set (portability_copy.json: generic nature,
    science and true-crime copy, generated for this board, never a topic of this channel; a case takes entry
    k mod 3) wherever the surface's copy comes from a run; a surface whose words are recorded inputs or test fields
    draws its own words and its tile says so.
The three variants run as their own processes (in parallel when --workers > 1). Output in <out>/portability:
<variant>/surfaces/*.png and surfaces.json, portability_NN.png / _half.jpg (three tiles per surface: default |
second | non-history; a refused one is its refusal tile with the reason) and portability.json (per surface and
variant: hash, refusal, the copy set's record, the second brand's configure-time verdict).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

VARIANTS = (("default", "default palette, the runs' own copy", []),
            ("second", "second palette (QF-35a test brand), the runs' own copy", ["--brand", "second"]),
            ("nonhistory", "default palette, the non-history stress copy set", None))   # None: --copy-set
COPY_SET = "portability_copy.json"
SURFACES_PER_SHEET = 6


def render(here: Path, tree: Path, dev: Path, out: Path, jobs: Path, parallel: bool) -> dict[str, dict]:
    """The three variants' render_surfaces.py runs: {variant: its surfaces.json}."""
    script = tree / "docs/reviews/quality-fixes-2026-11/QF-35a/render_surfaces.py"
    procs = []
    for name, _what, extra in VARIANTS:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        args = ["--copy-set", str(here / COPY_SET)] if extra is None else extra
        cmd = [sys.executable, str(here / "run_surfaces.py"), str(script), str(tree), str(dev), str(d / "surfaces.json"),
               "--keep", str(d / "surfaces"), "--jobs", str(jobs), *args]
        log = open(d / "render.log", "w", encoding="utf-8")
        procs.append((name, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT), log))
        if not parallel:
            procs[-1][1].wait()
    res = {}
    for name, p, log in procs:
        p.wait()
        log.close()
        if p.returncode:
            raise RuntimeError(f"portability {name}: render_surfaces.py failed ({p.returncode}); see "
                               f"{out / name / 'render.log'}")
        res[name] = json.loads((out / name / "surfaces.json").read_text(encoding="utf-8"))
    return res


def main(here: Path, tree: Path, dev: Path, out: Path, jobs: Path, workers: int) -> None:
    import board
    import board_evidence as be
    from PIL import Image
    t0 = time.perf_counter()
    pout = out / "portability"
    pout.mkdir(parents=True, exist_ok=True)
    res = render(here, tree, dev, pout, jobs, workers > 1)
    names = sorted({n for r in res.values() for n in (r.get("surfaces") or {})} |
                   {n for r in res.values() for n in (r.get("errors") or {}) if "__" in n})
    record: dict = {"jobs": jobs.read_text(encoding="utf-8").splitlines(), "variants": {}, "surfaces": {},
                    "second_brand_validation": res["second"].get("brand_validation"),
                    "copy_set": res["nonhistory"].get("copy_set")}
    for name, what, _x in VARIANTS:
        record["variants"][name] = {"what": what, "src_sha256": res[name].get("src_sha256"),
                                    "count": res[name].get("count")}
    swapped = set((res["nonhistory"].get("copy_set") or {}).get("swapped") or [])
    groups = {"cover__": "covers", "social__": "social", "endcard__": "endcard", "endscreen__": "endcard",
              "bridge__": "endcard", "bridge_follow__": "endcard", "captions__": "captions"}
    tiles = []
    for n in names:
        if n.endswith("__ass"):   # the caption files: hashed, no picture (their frames are the __frame surfaces)
            continue
        group = next((g for p, g in groups.items() if n.startswith(p)), None)
        row = {}
        for variant, what, _x in VARIANTS:
            rec = (res[variant].get("surfaces") or {}).get(n)
            err = (res[variant].get("errors") or {}).get(n)
            png = pout / variant / "surfaces" / f"{n}.png"
            note = what if variant != "nonhistory" or group in swapped else (
                "default palette; this surface's words are its recorded inputs or test fields (no copy-set field)")
            row[variant] = {"sha256": (rec or {}).get("sha256"), "refused": (rec or {}).get("refused"),
                            "error": str(err).strip().splitlines()[-1][:300] if err else None, "note": note}
            if png.is_file() and rec and "refused" not in rec:
                im = Image.open(png).convert("RGB")
            else:
                why = (rec or {}).get("refused") or row[variant]["error"] or "not drawn"
                im = be.refusal_tile(n, why, word="REFUSED" if (rec or {}).get("refused") else "NOT DRAWN")
            tiles.append((be.label(n, f"{variant.upper()}: {note}"), im))
        record["surfaces"][n] = row
    made = []
    per = 3 * SURFACES_PER_SHEET
    for k in range(0, len(tiles), per):
        name = f"portability_{k // per + 1:02d}"
        board.sheet(tiles[k:k + per], pout / name, tile_h=720, cols=3)
        made.append(name)
    record["sheets"] = made
    record["seconds"] = round(time.perf_counter() - t0, 1)
    (pout / "portability.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8", newline="\n")
    print("portability:", pout, len(record["surfaces"]), "surfaces,", len(made), "sheets")
