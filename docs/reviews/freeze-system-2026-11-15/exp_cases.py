"""The expected-cases file freeze_gate.py checks a palette's board against (cycle 4, lane BOARD).

    python exp_cases.py <base expected-cases.json> <palette> <tree> [--refusals <board>/refusals_<palette>.json]
                        [--drop-cycle4] > exp_<palette>.json

<base>: a palette's expected cases as freeze_gate.py reads them (a list of {surface, case, format}; the lander's
exp_default.json of the previous integration). The result is the base with:
  - its refusal table renamed to <palette>'s (refusals_<base palette> -> refusals_<palette>);
  - cycle 4's new evidence (unless --drop-cycle4, for a tree before it): per then-and-now case of the tree's catalogue
    (docs/reviews/freeze-panel-2026-11-04/fixtures/fixtures.json) and format, its clip (clips/thennow__<topic>__<tag>)
    and its row-12 strip, curves and timing table; the callout's clip and its row-12 sheets; row 13's Facebook
    masthead sheet;
  - with --refusals: every surface that palette's board recorded as refused (its refusal table) left out, each named
    on stderr, so the gate asks for what the palette draws and the refusal stays visible in the table and on its row.
second-colours (a colour-only re-theme) starts from the DEFAULT palette's cases: it must draw what the default draws.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FIXTURES = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "fixtures.json"
THENNOW_TAGS = ("916", "169")


def thennow_topics(tree: Path) -> list[str]:
    cat = json.loads((tree / FIXTURES).read_text(encoding="utf-8"))
    return [c["topic"] for c in (cat["designs"].get("thennow") or {}).get("cases") or []]


def cycle4_rows(tree: Path) -> list[dict]:
    rows = []
    for topic in thennow_topics(tree):
        for tag in THENNOW_TAGS:
            # freeze_common.classify: clips/thennow__<topic>__<tag>.mp4 -> case thennow__<topic>, format <tag>;
            # row12_motion_thennow__<topic>__<tag>.png -> case <topic>, format <tag>; its _half/_curves/_timing
            # files -> case <topic>__<tag>_<kind>, format default
            rows.append({"surface": "clips", "case": f"thennow__{topic}", "format": tag})
            rows.append({"surface": "row12_motion_thennow", "case": topic, "format": tag})
            for kind in ("half", "curves", "timing"):
                rows.append({"surface": "row12_motion_thennow", "case": f"{topic}__{tag}_{kind}", "format": "default"})
    rows.append({"surface": "clips", "case": "callouts__reference_pair", "format": "916"})
    rows.append({"surface": "row12_motion_callouts", "case": "reference_pair", "format": "916"})
    for kind in ("half", "curves", "timing"):
        rows.append({"surface": "row12_motion_callouts", "case": f"reference_pair__916_{kind}", "format": "default"})
    for part in ("", "_half"):
        rows.append({"surface": f"row13_fb_masthead{part}", "case": f"row13_fb_masthead{part}", "format": "default"})
    return rows


HOOK_GROUNDS = ("amber",)   # cycle 5: the hook clip on every ground beyond the first (the saturated yellow ground)


def cycle5_rows(tree: Path) -> list[dict]:
    """Cycle 5's evidence: per caption fixture of the tree's catalogue, its whole-run clip, band sheet and burst
    table; the hook clip and its row-12 sheets on the Amber ground."""
    cat = json.loads((tree / FIXTURES).read_text(encoding="utf-8"))
    rows = []
    for c in (cat["designs"].get("captions") or {}).get("cases") or []:
        tag = "169" if c.get("format") == "16:9" else "916"
        # freeze_common.classify: clips/captions_run__<topic>__<tag>.mp4 -> case captions_run__<topic>, format <tag>;
        # row07_captions_run__<topic>__<tag>.png -> surface row07_captions_run, case <topic>, format <tag>
        rows.append({"surface": "clips", "case": f"captions_run__{c['topic']}", "format": tag})
        rows.append({"surface": "row07_captions_run", "case": c["topic"], "format": tag})
        for kind in ("half", "bursts"):
            rows.append({"surface": "row07_captions_run", "case": f"{c['topic']}__{tag}_{kind}", "format": "default"})
    for g in HOOK_GROUNDS:
        rows.append({"surface": "clips", "case": f"hook__{g}", "format": "916", "refused_as": f"card__hook__{g}__916"})
        for kind in ("", "_half", "_curves"):
            rows.append({"surface": "row12_motion_hook", "case": f"{g}{kind}", "format": "default",
                         "refused_as": f"card__hook__{g}__916"})
    return rows


def refused_stems(refusals: Path) -> set[str]:
    """The surface names a palette's refusal table records as refused or not drawn (its `surface` column's name)."""
    rows = json.loads(refusals.read_text(encoding="utf-8"))
    return {str(r["surface"]).split(" ")[0] for r in rows if str(r.get("what", "")).startswith(("refused", "not "))}


def build(base: list[dict], palette: str, tree: Path, refusals: Path | None = None, cycle4: bool = True,
          cycle5: bool = True) -> tuple[list[dict], list[str]]:
    out, dropped = [], []
    for r in base:
        r = dict(r)
        for k in ("surface", "case"):
            r[k] = re.sub(r"^refusals_[\w-]+$", f"refusals_{palette}", r[k])
        out.append(r)
    if cycle4:
        have = {(r["surface"], r["case"], r["format"]) for r in out}
        out += [r for r in cycle4_rows(tree) if (r["surface"], r["case"], r["format"]) not in have]
    if cycle4 and cycle5:   # a tree before cycle 4 is before cycle 5 too
        have = {(r["surface"], r["case"], r["format"]) for r in out}
        out += [r for r in cycle5_rows(tree) if (r["surface"], r["case"], r["format"]) not in have]
    gone = refused_stems(refusals) if refusals is not None else set()
    keep = []
    for r in out:
        stem = r["case"] + (f"__{r['format']}" if r["format"] != "default" else "")
        as_stem = r.pop("refused_as", None)   # a row that stands or falls with another surface's refusal
        if (r["surface"] == "surfaces" and (stem in gone or r["case"] in gone)) or (as_stem and as_stem in gone):
            dropped.append(f"{r['surface']}/{stem}")
        else:
            keep.append(r)
    return keep, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("palette")
    ap.add_argument("tree", type=Path)
    ap.add_argument("--refusals", type=Path)
    ap.add_argument("--drop-cycle4", action="store_true")
    ap.add_argument("--drop-cycle5", action="store_true", help="for a tree before cycle 5's evidence")
    a = ap.parse_args(argv)
    base = json.loads(a.base.read_text(encoding="utf-8"))
    out, dropped = build(base, a.palette, a.tree, a.refusals, not a.drop_cycle4, not a.drop_cycle5)
    for d in dropped:
        print(f"exp_cases: {d} left out (refused on this palette's board)", file=sys.stderr)
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
