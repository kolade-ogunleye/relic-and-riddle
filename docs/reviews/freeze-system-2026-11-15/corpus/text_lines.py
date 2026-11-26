"""Corpus proof, TEXT-01 / TEXT-02 / TEXT-03 (freeze cycle 5): the ONE breaking policy over every drawn text of every
dev run, on every text surface.

    python text_lines.py --dev <dev checkout holding output/run_*> [--tree <tree>] [--data <collected.json>] [--quiet]
                         [--surfaces carousel,story,graphics,endcard,youtube,cards]

Measures, per drawn text (text_collect.py / text_surfaces.py set it through the renderer's own call path;
src/line_policy.faults judges it):
- TEXT-01  never_end / function_word_end: a line other than the last ends on an article, preposition, conjunction or
           another closed-class word; name_split (where the policy has it): a personal name parted across lines;
           weak_end (a copula or auxiliary: IS, WAS, DO) is counted per surface and is no violation;
- TEXT-02  unit_split: a line break inside a keep-together group (a number and its unit or scale word, a hedge and
           its figure, a date, an era, a range's far end);
- TEXT-03  lone_last_row: a one-word last row of a heading under the 0.55 balance minimum;
- measure_over (body copy, where the policy has it): a line over the body's character measure.

Surfaces: carousel heading and body, Story title and body, chapter / myth cards (committed card fields: no dev run
holds one), graphic labels (timeline, number cards, scale, map and route place names), the end card and end screen,
the YouTube headline; the Reel cover lockup and the carousel share line by an engine call (approx).

A violation PASSES only when it is RECORDED by the surface's own persisted record as the fallback the policy allows
where no legal break exists: the heading engine's Form.record (conjunction_end, locked_last_resort, lone_last_resort,
phrase_exception, keep_together_last_resort), a card's plan.meta anchors (line_end_last_resort), a still's
line_faults. A carousel BODY violation passes only when, besides, line_policy.legal_alternative finds no better
breaking that fits its width and lines (a record never excuses a break the setter could have avoided). Prints one
row per text with a violation (run, surface, measure, pass/fail), the totals per class and per surface family, and
exits 1 on any violation that is neither fixed nor recorded, or on a surface the collector could not set (an error
is never a pass). --tree runs the collector on another tree (the base) and judges it with THIS tree's policy.
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from src import line_policy  # noqa: E402

CLASS = {"never_end": "TEXT-01", "function_word_end": "TEXT-01", "unit_split": "TEXT-02", "lone_last_row": "TEXT-03",
         "name_split": "TEXT-01", "measure_over": "TEXT-MEASURE"}
HEADING_RECORDS = {"function_word_end": ("conjunction_end", "conjunction_end_last_resort"),
                   "unit_split": ("keep_together_last_resort",),
                   "lone_last_row": ("locked_last_resort", "lone_last_resort", "phrase_exception", "first_row_lone")}
FAULTS = ("never_end", "function_word_end", "name_split", "unit_split", "lone_last_row", "measure_over")
NBSP = chr(160)


def _arg(flag: str, default: str | None = None) -> str | None:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def collect(tree: Path, dev: Path, only: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "collected.json"
        extra = ["--surfaces", _arg("--surfaces")] if _arg("--surfaces") else []
        subprocess.run([sys.executable, "-B", str(HERE / "text_collect.py"), str(tree), str(dev), str(out), "--only",
                        only, *extra], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return json.loads(out.read_text(encoding="utf-8"))


_FAMILY = ((re.compile(r"^carousel_s\d\d_"), "carousel_"),
           (re.compile(r"^story_\d\d_"), "story_"),
           (re.compile(r"^card_chapter_act\d+_(\d+)(?:_title)?$"), r"card_chapter_\1"),
           (re.compile(r"^card_myth_.*_(\d+)(?:_(?:myth|fact))?$"), r"card_myth_\1"),
           (re.compile(r"^(graphic_[a-z]+)(?:_stop\d+|_site)?_(\d+)(?:_.*)?$"), r"\1_\2"))


def family(surface: str) -> str:
    """The surface family a row is totalled under (carousel_body, story_title, card_chapter_916, ...)."""
    for pat, to in _FAMILY:
        surface = pat.sub(to, surface)
    return surface


def _measure(size):
    from PIL import Image, ImageDraw

    from src import brand, typography
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = typography.get_font(brand.TEXT_FONT, size)
    return lambda t: d.textlength(str(t).replace(NBSP, " "), font=font)


def alternative(row: dict) -> list[str] | None:
    """A better legal breaking of a carousel body at its own size, width and lines (line_policy.legal_alternative),
    else None; None too where the policy has no such judge."""
    fn = getattr(line_policy, "legal_alternative", None)
    if fn is None or not row.get("body") or not row.get("size") or not row.get("width"):
        return None
    try:
        return fn(row["text"], _measure(row["size"]), row["width"], row.get("max_lines") or len(row["lines"]),
                  row["lines"], row.get("chars"))
    except Exception as ex:   # noqa: BLE001 - a judge that cannot run is reported, never a pass
        return [f"legal_alternative failed: {type(ex).__name__}: {ex}"[:120]]


def _names(rec, lines) -> set[str]:
    """The fault names a surface's own record carries for the drawn `lines`."""
    out: set[str] = set()
    if isinstance(rec, (list, tuple)):
        for r in rec:
            if not isinstance(r, dict):
                continue
            if r.get("lines") not in (None, list(lines)):
                continue      # a record of other lines (a trial fit, another field) names nothing here
            if r.get("fault"):
                out.add(r["fault"])
            out.update(f.get("fault") for f in r.get("faults") or [] if isinstance(f, dict))
    elif isinstance(rec, dict):
        for name, keys in HEADING_RECORDS.items():
            if any(rec.get(k) for k in keys):
                out.add(name)
        for sub in ("line_faults", "line_end_last_resort", "heading", "lockup", "title_form"):
            if isinstance(rec.get(sub), (list, tuple, dict)):
                out |= _names(rec[sub], lines)
    return out


def recorded(row: dict, fault: dict) -> bool:
    name = fault["fault"]
    if name in _names(row.get("record"), row["lines"]):
        return True
    return any(n.get("lines") == row["lines"] and any(f.get("fault") == name for f in n.get("faults", []))
               for n in row.get("notes") or [])


def row_faults(row: dict) -> list[dict]:
    """The row's violations. A Story title is two authored fields: the line-end and keep-together rules run over all
    its rows (the field seam is a line break like any other); the lone-last-row rule runs inside each field (a
    one-word second FIELD is the copy's payoff field, not a break the engine chose)."""
    lines = row["lines"]
    if row.get("fields"):
        out = [f for f in line_policy.violations(lines) if f["fault"] != "lone_last_row"]
        at = 0
        for n in row["fields"]:
            out += [f for f in line_policy.violations(lines[at:at + n], heading=True)
                    if f["fault"] == "lone_last_row"]
            at += n
        return out
    out = list(line_policy.violations(lines, row.get("widths"), heading=bool(row.get("heading"))))
    if row.get("body") and row.get("chars") and not any(f["fault"] == "measure_over" for f in out):
        over = [k for k, ln in enumerate(lines) if len(ln.replace(NBSP, " ")) > row["chars"]]
        if over:
            out.append({"fault": "measure_over", "line": over[0], "word": str(len(lines[over[0]]))})
    return out


def judge(data: dict) -> tuple[list[tuple], collections.Counter, dict]:
    table, tot, fam = [], collections.Counter(), collections.defaultdict(collections.Counter)
    for run, v in sorted(data["runs"].items()):
        if v.get("lines_error"):
            tot["collector errors"] += 1
            table.append((run, "*", v["lines_error"], "FAIL (collector error)"))
        for row in v.get("lines") or []:
            f_ = fam[family(row.get("surface", "?"))]
            if row.get("error"):
                tot["collector errors"] += 1
                f_["errors"] += 1
                table.append((run, row.get("surface", "?"), row["error"], "FAIL (collector error)"))
                continue
            tot["texts"] += 1
            f_["texts"] += 1
            if row.get("refused"):
                tot["refused (recorded by the renderer)"] += 1
                f_["refused"] += 1
                continue
            f_["weak_end"] += sum(f["fault"] == "weak_end" for f in line_policy.faults(row["lines"]))
            alt = False
            for f in row_faults(row):
                ok = recorded(row, f)
                why = "pass (recorded)" if ok else "FAIL"
                if row.get("body") and f["fault"] != "measure_over":
                    alt = alternative(row) if alt is False else alt
                    if alt:
                        ok, why = False, "FAIL (a legal alternative fits: " + " / ".join(alt) + ")"
                cls = CLASS.get(f["fault"], f["fault"])
                tot[f"{cls} {f['fault']}"] += 1
                tot[f"{cls} {'recorded' if ok else 'UNRECORDED'}"] += 1
                f_[f["fault"]] += 1
                f_["recorded" if ok else "UNRECORDED"] += 1
                table.append((run, row["surface"], f"{f['fault']}: {' / '.join(row['lines'])}", why))
    return table, tot, fam


def main() -> int:
    dev = Path(_arg("--dev") or os.environ.get("RR_DEV_CHECKOUT") or ".")
    if _arg("--data"):   # one collected file, or several (one per surface group), merged run by run
        data = {"tree": None, "runs": {}}
        for part in _arg("--data").split(","):
            got = json.loads(Path(part).read_text(encoding="utf-8"))
            data["tree"] = data["tree"] or got.get("tree")
            for run, v in got["runs"].items():
                into = data["runs"].setdefault(run, {})
                into.setdefault("lines", []).extend(v.get("lines") or [])
                if v.get("lines_error"):
                    into["lines_error"] = v["lines_error"]
    else:
        data = collect(Path(_arg("--tree", str(ROOT))), dev, "lines")
    table, tot, fam = judge(data)
    if "--quiet" not in sys.argv:
        print("run | surface | measure | result")
        for row in table:
            print(" | ".join(row))
    print(f"\ntree: {data.get('tree')}")
    for k in sorted(tot):
        print(f"{k}: {tot[k]}")
    print("\nsurface family | texts | refused | errors | " + " | ".join(FAULTS)
          + " | recorded | UNRECORDED | weak_end (no violation)")
    for name in sorted(fam):
        c = fam[name]
        print(" | ".join([name, str(c["texts"]), str(c["refused"]), str(c["errors"])] + [str(c[k]) for k in FAULTS]
                         + [str(c["recorded"]), str(c["UNRECORDED"]), str(c["weak_end"])]))
    bad = sum(v for k, v in tot.items() if k.endswith("UNRECORDED"))
    print(f"\nunrecorded violations: {bad}")
    print(f"collector errors: {tot['collector errors']}")
    return 1 if bad or tot["collector errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
