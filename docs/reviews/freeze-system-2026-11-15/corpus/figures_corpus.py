"""The FIGURES stream's corpus harness (design freeze, cycle 5): every data graphic of every dev run that has the
graphic's inputs, laid out by THIS tree and captured as records (no clip is encoded, no full render in the loop).

    python figures_corpus.py <tree> <dev checkout> <cache.json> [--kinds scale,section,...] [--fixtures-only]

One capture feeds every fig_*.py class script (each prints its table and exits non-zero on a violation that is neither
fixed nor recorded). The capture reads what the renderers themselves plan: the text plates handed to
graphics.record_contrast (text, face, size, colour, ink box, planned contrast, their per-run parts), the ruler rows,
the essential marks handed to graphics.record_nontext (a leader's own mask), the omission ledger (src.refusal) and a
renderer's layout record where the tree writes one. It works on the base tree too (nothing here needs a field a
cycle-5 fix added), so base and tip are measured by the same instrument.

Cheapness: the renderers draw CLIP_SECONDS x VIDEO_FPS frames; the capture sets the module's frame rate to 1 (five
frames, the last one the planned layout the plates were planned on) and the graded re-measure off (planned ratios are
the measure). The numbers are layout numbers, not delivery measurements.

Inputs per kind:
  map       each run's recorded place (graphics/graphic_map.mp4.lineage.json), its narration and palette
  numbers   verified_script.json stat_cards
  timeline  verified_script.json timeline
  scale     verified_script.json scale_fact, plus the committed stress facts (SCALE_FIXTURES: no dev run carries a
            foot depth, a metre height or an exact length; labelled FIXTURE)
  route     verified_script.json route (the run's stops are drawn when the offline place cache locates them; else the
            board's stand-in second stop, labelled STAND-IN, as render_surfaces.py draws it)
  section   no run carries a cross-section: the committed test specs (SECTION_FIXTURES, labelled FIXTURE)
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

FORMATS = ((1080, 1920, "916"), (1920, 1080, "169"))
KINDS = ("scale", "section", "numbers", "timeline", "route", "map")

# Committed stress inputs (FIXTURE): real signed shapes the dev corpus does not carry. No topic list drives any code;
# these only widen what the proof measures.
SCALE_FIXTURES = [
    {"value": 280.0, "unit": "ft", "dimension": "depth", "subject": "Derinkuyu underground city", "approximate": True},
    {"value": 85.0, "unit": "m", "dimension": "depth", "subject": "Derinkuyu underground city", "approximate": True},
    {"value": 107.0, "unit": "ft", "dimension": "height", "subject": "Kailasa Temple", "approximate": True},
    {"value": 32.6, "unit": "m", "dimension": "height", "subject": "Kailasa Temple", "approximate": False},
    {"value": 42.0, "unit": "m", "dimension": "length", "subject": "Unfinished obelisk", "approximate": False},
    {"value": 6.5, "unit": "m", "dimension": "height", "subject": "Standing stone", "approximate": True},
    {"value": 455.0, "unit": "ft", "dimension": "height", "subject": "Great Pyramid of Giza", "approximate": False},
    {"value": 1500.0, "unit": "ft", "dimension": "depth", "subject": "A very deep mine shaft", "approximate": True},
    {"value": 20.0, "unit": "m", "dimension": "depth", "subject": "Well", "approximate": True},
]
SECTION_FIXTURES = [
    {"subject": "Derinkuyu underground city", "total_depth_ft": 280.0, "approximate": True,
     "levels": [{"depth_ft": 15.0, "label": "Stables, wine presses and storage"},
                {"depth_ft": 35.0, "label": "Living quarters and kitchens"},
                {"depth_ft": 140.0, "label": "Cruciform church and school"},
                {"depth_ft": 280.0, "label": "Deepest wells and shaft base"}]},
    {"subject": "Salt mine", "total_depth_ft": 1073.0, "approximate": True,
     "levels": [{"depth_ft": 210.0, "label": "First level"},
                {"depth_ft": 443.0, "label": "Chapel of St Kinga"},
                {"depth_ft": 1073.0, "label": "Deepest workings"}]},
    {"subject": "Stepwell", "total_depth_ft": 100.0, "approximate": False,
     "levels": [{"depth_ft": 20.0, "label": "Upper gallery"},
                {"depth_ft": 30.0, "label": "Second gallery"},
                {"depth_ft": 40.0, "label": "Third gallery and shrine"},
                {"depth_ft": 55.0, "label": "Lower landing"},
                {"depth_ft": 100.0, "label": "Water table"}]},
    {"subject": "Cave system", "total_depth_ft": 600.0, "approximate": True,
     "levels": [{"depth_ft": 550.0, "label": "Lower chamber"},
                {"depth_ft": 600.0, "label": "Sump"}]},
]


def _json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_inputs(dev: Path) -> list[dict[str, Any]]:
    """Every (run, kind) of the dev checkout that has the kind's inputs, plus the committed fixtures."""
    jobs: list[dict[str, Any]] = []
    for run in sorted((dev / "output").glob("run_*")):
        if not run.is_dir():
            continue
        script = _json(run / "verified_script.json") or {}
        pal = _json(run / "palette.json")
        narration = script.get("narration_script") or ""
        common = {"run": run.name, "palette": pal, "narration": narration, "source": "RUN"}
        if script.get("scale_fact"):
            jobs.append({**common, "kind": "scale", "input": script["scale_fact"]})
        if script.get("stat_cards"):
            jobs.append({**common, "kind": "numbers", "input": script["stat_cards"], "run_dir": str(run)})
        if script.get("timeline"):
            jobs.append({**common, "kind": "timeline", "input": script["timeline"], "run_dir": str(run)})
        if script.get("route"):
            jobs.append({**common, "kind": "route", "input": script["route"]})
        if script.get("cross_section"):
            jobs.append({**common, "kind": "section", "input": script["cross_section"]})
        lin = _json(run / "graphics" / "graphic_map.mp4.lineage.json")
        place = ((lin or {}).get("settings") or {}).get("inputs", {}).get("place") if lin else None
        if place:
            nar = (((lin["settings"]["inputs"].get("payload") or {}).get("narration_script")) or narration)
            jobs.append({**common, "kind": "map", "input": place, "narration": nar})
    for k, fact in enumerate(SCALE_FIXTURES):
        jobs.append({"run": f"fixture_scale_{k}_{fact['dimension']}_{fact['unit']}", "kind": "scale", "input": fact,
                     "palette": None, "narration": "", "source": "FIXTURE"})
    for k, cs in enumerate(SECTION_FIXTURES):
        jobs.append({"run": f"fixture_section_{k}", "kind": "section", "input": cs, "palette": None, "narration": "",
                     "source": "FIXTURE"})
    return jobs


def _stat_photo(run: Path):
    prov = _json(run / "provenance.json")
    if not isinstance(prov, dict):
        return None
    for item in (prov.get("scenes") or prov.get("items") or []):
        if not isinstance(item, dict):
            continue
        p = Path(str(item.get("source") or item.get("photo") or ""))
        if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file():
            return p
    return None


def _hex(c) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in tuple(c)[:3])


def _face(font) -> str:
    try:
        fam, style = font.getname()
        return f"{fam} {style}".strip()
    except Exception:   # noqa: BLE001
        return str(getattr(font, "path", "?"))


def _plate_row(name: str, plate: Any) -> dict[str, Any] | None:
    if plate is None or getattr(plate, "empty", True):
        return None
    parts = getattr(plate, "plates", None) or [plate]
    runs = [{"text": p.text, "face": _face(p.font), "px": int(getattr(p.font, "size", 0)), "fill": _hex(p.fill),
             "box": [int(v) for v in p.box]} for p in parts if not getattr(p, "empty", False)]
    row = {"name": name, "text": plate.text, "box": [int(v) for v in plate.box], "planned": plate.contrast,
           "halo": getattr(plate, "halo", None), "runs": runs}
    for key in ("derived", "role"):
        v = getattr(plate, key, None)
        if v is not None:
            row[key] = dict(v) if isinstance(v, dict) else v
    return row


def _leader_profile(mask) -> dict[str, Any] | None:
    """A leader's own mask read as a path: its datum end (left), its label end (right) and how many px of x its
    transfer (the run between the two heights) takes."""
    import numpy as np
    m = np.asarray(mask) > 127
    cols = np.where(m.any(axis=0))[0]
    if cols.size < 6:
        return None
    ys = np.array([np.mean(np.where(m[:, c])[0]) for c in cols])
    # the datum end carries a dot (taller than the line): the path's heights are the line's, read off the dot
    head, tail = float(np.median(ys[: max(3, cols.size // 6)])), float(np.median(ys[-max(3, cols.size // 12):]))
    lo, hi = min(head, tail), max(head, tail)
    between = [(int(c), float(y)) for c, y in zip(cols, ys) if lo + 2 < y < hi - 2] if hi - lo > 4 else []
    return {"start": [int(cols[0]), round(head, 1)], "end": [int(cols[-1]), round(tail, 1)],
            "transfer_dx": (between[-1][0] - between[0][0] + 1) if between else 0,
            "transfer_x": [between[0][0], between[-1][0]] if between else None, "dy": round(tail - head, 1)}


def capture_one(job: dict[str, Any], fmt: tuple[int, int, str], work: Path) -> dict[str, Any]:
    """One graphic laid out by this tree; its record."""
    from src import graphics
    from src.art_direction import use_palette
    from src.photo_grade import using
    from src.refusal import collecting, hold_rows
    w, h, tag = fmt
    rec: dict[str, Any] = {"run": job["run"], "kind": job["kind"], "format": tag, "size": [w, h],
                           "source": job["source"], "input": job["input"] if job["kind"] in ("scale", "section") else None,
                           "elements": [], "rulers": [], "marks": [], "leaders": [], "omissions": [], "layout": None}
    out = work / f"{job['kind']}_{job['run']}_{tag}.mp4"
    real_rc, real_nt, real_graded = graphics.record_contrast, graphics.record_nontext, graphics.graded_frame

    def rc(out_, frame, plates, rulers=None):
        for name, plate in plates:
            row = _plate_row(name, plate)
            if row:
                rec["elements"].append(row)
        rec["rulers"] = [dict(r) for r in (rulers or [])]
        rec["frame_size"] = list(frame.size)
        return real_rc(out_, frame, plates, rulers)

    def nt(out_, frame, marks, ground=None):
        for name, mask in marks:
            bb = mask.getbbox()
            rec["marks"].append({"name": name, "box": list(bb) if bb else None})
            if name.startswith("leader"):
                prof = _leader_profile(mask)
                if prof:
                    rec["leaders"].append({"name": name, **prof})
        return real_nt(out_, frame, marks, ground)

    graphics.record_contrast, graphics.record_nontext = rc, nt
    graphics.graded_frame = lambda *a, **k: None
    fps = graphics.VIDEO_FPS
    graphics.VIDEO_FPS = 1
    t0 = time.time()
    try:
        use_palette(job.get("palette"))
        with collecting() as omitted, using(0.0), graphics.poster_only():
            kind, inp = job["kind"], job["input"]
            if kind == "scale":
                made = graphics.render_scale(inp, out, width=w, height=h)
            elif kind == "section":
                made = graphics.render_cross_section(inp, out, width=w, height=h)
            elif kind == "numbers":
                made = graphics.render_stat_cards(inp, out, _stat_photo(Path(job["run_dir"])), width=w, height=h,
                                                  narration=job["narration"])
            elif kind == "timeline":
                made = graphics.render_timeline(inp, out, width=w, height=h, photo=_stat_photo(Path(job["run_dir"])))
            elif kind == "map":
                made = graphics.render_map(inp, out, width=w, height=h, narration=job["narration"])
            elif kind == "route":
                made = graphics.render_route(inp, out, width=w, height=h, narration=job["narration"])
                if made is None:   # the run's stops are not all in the offline place cache: the board's stand-in
                    rec["source"] = "STAND-IN"
                    stops = [{"place": inp[0]["place"], "country": inp[0].get("country"), "label": inp[0]["place"]},
                             {"place": "Kholat Syakhl", "country": "Russia", "label": "Kholat Syakhl"}]
                    made = graphics.render_route(stops, out, width=w, height=h, narration=job["narration"])
            else:
                raise ValueError(kind)
            rec["drawn"] = made is not None
        rec["omissions"] = hold_rows(omitted)
    except Exception as e:   # noqa: BLE001  a refusal is a result (recorded), anything else is an error row
        rec["drawn"] = False
        rec["refused"] = {"class": type(e).__name__, "code": getattr(e, "code", None), "text": str(e)[:300]}
        if type(e).__name__ not in ("LayoutError", "TextFitError", "Declined", "ContrastError"):
            rec["error"] = traceback.format_exc()[-1500:]
    finally:
        graphics.record_contrast, graphics.record_nontext, graphics.graded_frame = real_rc, real_nt, real_graded
        graphics.VIDEO_FPS = fps
        use_palette(None)
    side = Path(str(out) + ".contrast.json")
    sidecar = _json(side) if side.is_file() else None
    if isinstance(sidecar, dict):
        rec["layout"] = sidecar.get("layout")
        rec["sidecar_elements"] = {e["name"]: {k: v for k, v in e.items() if k in ("derived", "role")}
                                   for e in sidecar.get("elements") or [] if "derived" in e or "role" in e}
    rec["seconds"] = round(time.time() - t0, 2)
    for p in work.glob(out.stem + "*"):
        with contextlib.suppress(OSError):
            if p.is_file():
                p.unlink()
    return rec


def tree_sha(tree: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((tree / "src").glob("*.py")):
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def capture(tree: Path, dev: Path, cache: Path, kinds=KINDS, fixtures_only: bool = False,
            quiet: bool = False) -> dict[str, Any]:
    """Every record of `kinds`, from `cache` when it was made by this tree's src/ (its sha), else captured now."""
    tree = Path(tree).resolve()
    sha = tree_sha(tree)
    have = _json(cache) if cache.is_file() else None
    if not (isinstance(have, dict) and have.get("src_sha") == sha):
        have = {"src_sha": sha, "tree": tree.name, "records": {}}
    sys.path.insert(0, str(tree))
    os.environ.setdefault("RR_FIXTURE_REPO", str(dev))
    work = cache.parent / f"_work_{sha}"
    work.mkdir(parents=True, exist_ok=True)
    jobs = [j for j in run_inputs(dev) if j["kind"] in kinds and (not fixtures_only or j["source"] == "FIXTURE")]
    for job in jobs:
        for fmt in FORMATS:
            key = f"{job['kind']}|{job['run']}|{fmt[2]}"
            if key in have["records"]:
                continue
            rec = capture_one(job, fmt, work)
            have["records"][key] = rec
            if not quiet:
                print(f"captured {key} drawn={rec.get('drawn')} {rec['seconds']} s"
                      + (f" REFUSED {rec['refused']['class']}" if rec.get("refused") else ""), flush=True)
            cache.write_text(json.dumps(have, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    keys = {f"{j['kind']}|{j['run']}|{f[2]}" for j in jobs for f in FORMATS}
    return {"src_sha": sha, "records": {k: v for k, v in have["records"].items() if k in keys}}


def table(rows: list[tuple], head: tuple) -> str:
    cols = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    line = lambda r: "  ".join(str(v).ljust(c) for v, c in zip(r, cols)).rstrip()   # noqa: E731
    return "\n".join([line(head), line(tuple("-" * c for c in cols)), *[line(r) for r in rows]])


def cli(doc: str, kinds) -> tuple[dict[str, Any], argparse.Namespace]:
    ap = argparse.ArgumentParser(description=doc)
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("cache", type=Path, help="the capture cache (reused while the tree's src/ is unchanged)")
    ap.add_argument("--fixtures-only", action="store_true")
    a = ap.parse_args()
    return capture(a.tree, a.dev, a.cache, kinds, a.fixtures_only), a


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", type=Path)
    ap.add_argument("dev", type=Path)
    ap.add_argument("cache", type=Path)
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--fixtures-only", action="store_true")
    a = ap.parse_args()
    got = capture(a.tree, a.dev, a.cache, tuple(a.kinds.split(",")), a.fixtures_only)
    print(f"{len(got['records'])} records, src {got['src_sha']}")
