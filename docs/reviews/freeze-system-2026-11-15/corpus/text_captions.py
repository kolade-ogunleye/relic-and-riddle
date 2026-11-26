"""Corpus proof, TEXT-04 / TEXT-05 / MOTION-03 (freeze cycle 5): the caption burst policy over every dev run.

    python text_captions.py [--tree <tree>] [--dev <dev checkout>] [--data <collected.json>] [--quiet]

Lander ruling 2026-11-26: a burst is split only to avoid a function-word line end or a qualifier / number split; no
burst is made that is on screen under 0.8 s; a two-line burst whose line is one CONTENT word is legal; a phrase unit
is never parted. Measures, per run and format, on the caption file as the composer builds it (text_collect.py):

- TEXT-04  short: 9:16 bursts on screen under 0.8 s           (pass only when recorded "kept_short")
           one_word: one-word 9:16 bursts                     (reported)
           line faults: a line of a two-line burst ending on a function word, or parting a keep-together group
                                                              (pass only when the composer's record names the burst)
           burst_end: a burst ending mid-sentence on a function word (pass only when recorded "never_end")
           phrase_unit: a burst cut inside a phrase unit      (reported: the judge is line_policy.phrase_unit)
- TEXT-05  size_steps: back-to-back bursts of one clip at different sizes (must be 0), and the clip's one size
           plate_jumps: back-to-back plates whose widths differ by more than 1.5x (reported)
- MOTION-03 empty_first_frame: bursts whose first word is fully transparent on the plate's first frame (must be 0)
- cps: bursts shown over 17 characters a second (reported only: the narration's pace is a copy-side matter, ruling
  2026-11-19; never fixed by display tricks).

Exits 1 on a violation that is neither fixed nor recorded.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from src import line_policy  # noqa: E402
from text_lines import _arg, collect  # noqa: E402

MIN_S = 0.8
STOP = ".?!\u2026"


def _ends_sentence(word: str) -> bool:
    return str(word).rstrip("\"'\u201d\u2019)").endswith(tuple(STOP))


def judge(data: dict) -> tuple[list[tuple], collections.Counter, collections.Counter]:
    table, tot, sizes = [], collections.Counter(), collections.Counter()
    for run, v in sorted(data["runs"].items()):
        for fmt, cap in sorted((v.get("captions") or {}).items()):
            if "error" in cap:
                tot[f"{fmt} build error UNRECORDED"] += 1
                table.append((run, f"captions_{fmt}", cap["error"], "FAIL"))
                continue
            bursts, rate = cap["bursts"], cap.get("rate") or {}
            tot[f"{fmt} bursts"] += len(bursts)
            log = rate.get("line_split") or []
            kept = {e.get("kept_short", "").replace(" / ", " ") for e in log if e.get("kept_short")}
            named = {" ".join(str(x).replace(" / ", " ").split()) for x in (
                [e.get("text") for e in rate.get("never_end_lines") or []] + list(rate.get("line_defect") or [])
                + list(rate.get("name_split") or []) + [e.get("text") for e in log if e.get("into", 0) is None]
                + [e.get("text") for e in rate.get("bond_extra") or []] + list(rate.get("range_seam") or [])) if x}
            ends = set(rate.get("never_end") or [])
            fs = [b["fs"] for b in bursts]
            if fmt == "916":
                steps = sum(a != b and abs(x["hide"] - y["show"]) < 1e-3 for a, b, x, y in zip(fs, fs[1:], bursts,
                                                                                               bursts[1:]))
                tot["916 size_steps"] += steps
                if steps:
                    tot["916 size_steps UNRECORDED"] += steps
                    table.append((run, "captions_916", f"size_steps: {steps}", "FAIL"))
                sizes[str(sorted({f or 142 for f in fs})) if len(set(fs)) > 1 else (fs[0] or 142 if fs else 0)] += 1
            for i, b in enumerate(bursts):
                raw = b.get("raw") or " ".join(b["lines"]).split()
                text = " ".join(raw)
                key = " ".join(line_policy.bare(w) if False else w for w in raw)
                dur = b["hide"] - b["show"]
                if dur > 0 and len(" ".join(" ".join(b["lines"]).split())) / dur > 17:
                    tot[f"{fmt} cps_over_17 (copy side, reported)"] += 1
                if b.get("first_alpha") == 255:
                    tot[f"{fmt} empty_first_frame UNRECORDED"] += 1
                if fmt != "916":
                    continue
                if len(raw) == 1:
                    tot["916 one_word (reported)"] += 1
                if dur < MIN_S - 1e-6:
                    ok = key in kept
                    tot["916 short<0.8"] += 1
                    tot[f"916 short<0.8 {'recorded' if ok else 'UNRECORDED'}"] += 1
                    table.append((run, "captions_916", f"short {dur:.2f}s: {text}", "pass (recorded)" if ok else "FAIL"))
                if len(b["lines"]) == 2:
                    k = len(b["lines"][0].split())
                    lines = [" ".join(raw[:k]), " ".join(raw[k:])] if len(raw) == sum(len(x.split()) for x in b["lines"]) \
                        else b["lines"]
                    for f in line_policy.violations(lines):
                        ok = key in named or " ".join(" ".join(b["lines"]).split()) in named or any(
                            key.rstrip(".,;:") == n.rstrip(".,;:") for n in named)
                        tot[f"916 line {f['fault']}"] += 1
                        tot[f"916 line {'recorded' if ok else 'UNRECORDED'}"] += 1
                        table.append((run, "captions_916", f"{f['fault']}: {' / '.join(lines)}",
                                      "pass (recorded)" if ok else "FAIL"))
                if i + 1 < len(bursts) and raw and not _ends_sentence(raw[-1]):
                    nxt = bursts[i + 1].get("raw") or " ".join(bursts[i + 1]["lines"]).split()
                    if line_policy.end_class(raw[-1]) in ("never", "last_resort"):
                        ok = text in ends
                        tot["916 burst_end function word"] += 1
                        tot[f"916 burst_end {'recorded' if ok else 'UNRECORDED'}"] += 1
                        table.append((run, "captions_916", f"burst end: {text} | {' '.join(nxt)}",
                                      "pass (recorded)" if ok else "FAIL"))
                    if nxt and b.get("raw") and line_policy.phrase_unit(raw[:-1], raw[-1], nxt[0]):
                        tot["916 phrase_unit cut (reported)"] += 1
            ws = [b["plate_w"] for b in bursts]
            tot[f"{fmt} plate_jumps>1.5x (reported)"] += sum(
                abs(x["hide"] - y["show"]) < 1e-3 and max(a, c) / max(1, min(a, c)) > 1.5
                for a, c, x, y in zip(ws, ws[1:], bursts, bursts[1:]))
    return table, tot, sizes


def main() -> int:
    dev = Path(_arg("--dev", r"C:\Users\omokolade\GitHub\relic-and-riddle"))
    data = (json.loads(Path(_arg("--data")).read_text(encoding="utf-8")) if _arg("--data")
            else collect(Path(_arg("--tree", str(ROOT))), dev, "captions"))
    table, tot, sizes = judge(data)
    if "--quiet" not in sys.argv:
        print("run | surface | measure | result")
        for row in table:
            print(" | ".join(row))
    print(f"\ntree: {data.get('tree')}")
    for k in sorted(tot):
        print(f"{k}: {tot[k]}")
    print("9:16 clip sizes (ASS px: clips):", dict(sizes))
    bad = sum(v for k, v in tot.items() if k.endswith("UNRECORDED"))
    print(f"unrecorded violations: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
