"""Corpus proof, TEXT-04 / TEXT-05 / MOTION-03 (freeze cycle 5): the caption burst policy over every dev run.

    python text_captions.py --dev <dev checkout holding output/run_*> [--tree <tree>] [--data <collected.json>] [--quiet]

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

The production call (added after the independent review of the first cycle-5 build, defects D4 to D9):

    python text_captions.py --production --dev <dev checkout> [--tree <tree>] [--cut-grid 3.1]
                            [--prod-data <a.json> --prod-cut-data <b.json>] [--quiet]

builds each run's 9:16 caption file as compose_video does (first_frame_whole, and with --cut-grid picture cuts every
that many seconds through snap_to_cuts; real cut lists are not stored in the runs) and prints, per tree: the share of
bursts at the top size, the two-line share, the back-to-back plate-HEIGHT-change rate, bursts under 0.8 s (with and
without the cut grid, recorded or not) and under 1.0 s, one-word bursts, line breaks and burst cuts inside a phrase
unit, bursts that straddle a clause, two-line bursts drawn with a line fault although ONE line holds them at the
clip's size (a wrong "no legal break" record), and words on screen more than 250 ms before they are spoken. It exits 1
on an unrecorded short burst, a size step, or a faulted two-line burst that one line would hold.
(`--collect <tree> <dev> <out.json> [grid]` is that mode's own collector: it imports <tree>, nothing of this tree.)
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BS = chr(92)


def _key(word: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", str(word)).upper())


def collect_production(tree: str, dev: str, out: str, grid: float = 0.0) -> None:
    """Every run's 9:16 caption file as production builds it, by the code of `tree`: per burst its show / hide, plate
    width and height, drawn size, lines, the burst's own words (punctuation kept), each word's reveal time and
    whether ONE line holds the burst at the size it is drawn at; the composer's record; the cuts passed."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, tree)
    from src import video_composer as vc

    def ts(text: str) -> float:
        h, m, r = text.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(r)

    hidden = "{" + BS + "alpha&HFF&}"
    res: dict = {}
    for run in sorted(Path(dev).glob("output/run_*")):
        if not (run / "words.json").is_file():
            continue
        words = json.loads((run / "words.json").read_text(encoding="utf-8"))
        vj = run / "verified_script.json"
        emph = (json.loads(vj.read_text(encoding="utf-8")).get("emphasis_words") or None) if vj.is_file() else None
        end = max(float(w["start"]) + float(w.get("duration", w.get("dur", 0)) or 0) for w in words)
        cuts = None
        if grid:
            cuts, t = [], grid
            while t < end:
                cuts.append(round(t, 3))
                t += grid
        with tempfile.TemporaryDirectory() as td:
            ass = Path(td) / "c.ass"
            comp = vc.VideoComposer()
            comp.build_subtitles([dict(w) for w in words], ass, emphasis=emph,
                                 first_frame_whole=vc.CAPTION_FIRST_FRAME_WHOLE_S, cuts=cuts)
            text = ass.read_text(encoding="utf-8")
        boxes, evs = [], []
        for line in text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            part = line[len("Dialogue:"):].split(",", 9)
            a, b, style, body = ts(part[1]), ts(part[2]), part[3].strip(), part[9]
            if style == "CapBox":
                nums = [float(v) for v in re.findall(r"[-\d.]+", body.split(BS + "p1}")[-1])]
                boxes.append((a, b, round(max(nums[0::2]) - min(nums[0::2])) if nums else 0,
                              round(max(nums[1::2]) - min(nums[1::2])) if nums else 0))
            elif style == "CapText":
                m = re.search(BS * 2 + r"pos\(([-\d.]+),([-\d.]+)\)", body)
                f = re.search(BS * 2 + r"fs(\d+)", body)
                strip = lambda x: " ".join(re.sub(r"\{[^}]*\}", "", x).split())   # noqa: E731
                evs.append((a, b, float(m.group(2)) if m else 0.0, strip(body.split(hidden)[0]), strip(body),
                            int(f.group(1)) if f else None))
        bursts, wi = [], 0
        budget = vc._ink_budget()
        for a, b, w, h in boxes:
            mine = [x for x in evs if a - 1e-3 <= x[0] < b - 1e-3]
            ys = sorted({x[2] for x in mine})
            lines = [next((x[4] for x in mine if x[2] == y), "") for y in ys]
            steps = [[st, sum(len(x[3].split()) for x in mine if abs(x[0] - st) < 1e-3)]
                     for st in sorted({round(x[0], 3) for x in mine})]
            want, got, j = _key(" ".join(lines)), "", wi
            while j < len(words) and len(got) < len(want):
                got += _key(words[j]["word"])
                j += 1
            raw = [str(x["word"]) for x in words[wi:j]] if got == want else None
            starts = [float(x["start"]) for x in words[wi:j]] if got == want else None
            if got == want:
                wi = j
            fs = mine[0][5] if mine else None
            px = vc.FONT_SIZE if fs is None else round(fs * 0.5625)
            one = bool(raw) and vc._caption_ink_width(vc._phrase_words(raw), px) <= budget
            bursts.append({"show": a, "hide": b, "w": w, "h": h, "fs": fs, "px": px, "lines": lines, "steps": steps,
                           "raw": raw, "starts": starts, "one_line_fits": one})
        rate = getattr(comp, "last_reading_rate", None) or {}
        res[run.name] = {"bursts": bursts, "rate": {k: v for k, v in rate.items() if k != "bursts"}, "cuts": cuts,
                         "top_fs": vc.FONT_SIZE_ASS}
        print(run.name, len(bursts), flush=True)
    Path(out).write_text(json.dumps({"tree": str(tree), "grid": grid, "runs": res}, ensure_ascii=True),
                         encoding="utf-8", newline="\n")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--collect":
    collect_production(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]) if len(sys.argv) > 5 else 0.0)
    raise SystemExit(0)

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
            kept = {e.get("kept_short", "").replace(" / ", " ") for e in (rate.get("recut") or []) if e.get("kept_short")}
            named = {" ".join(str(x).replace(" / ", " ").split()) for x in (
                [e.get("text") for e in rate.get("never_end_lines") or []] + list(rate.get("line_defect") or [])
                + list(rate.get("name_split") or []) + [e.get("text") for e in log if e.get("into", 0) is None]
                + [e.get("text") for e in rate.get("bond_extra") or []] + list(rate.get("range_seam") or [])) if x}
            ends = set(rate.get("never_end") or [])
            fs = [b["fs"] for b in bursts]
            if fmt == "916":
                under = {" ".join(str(e.get("text", "")).upper().split())
                         for e in (rate.get("clip_size") or {}).get("under") or []}
                tot["916 size_steps recorded (a burst under the clip's size)"] += sum(
                    a != b and abs(x["hide"] - y["show"]) < 1e-3 and bool(
                        {" ".join(" ".join(z["lines"]).upper().split()) for z in (x, y)} & under)
                    for a, b, x, y in zip(fs, fs[1:], bursts, bursts[1:]))
                steps = sum(a != b and abs(x["hide"] - y["show"]) < 1e-3 and not (
                    {" ".join(" ".join(z["lines"]).upper().split()) for z in (x, y)} & under)
                    for a, b, x, y in zip(fs, fs[1:], bursts, bursts[1:]))
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


CLAUSE = re.compile(r"[,;:\u2014\u2013]+[\"'\u201d\u2019)]*$")


def straddles_clause(raw: list[str]) -> bool:
    """A burst with clause punctuation inside it, other than after a one-word lead-in (TODAY, A COMPLETED ...)."""
    return any(CLAUSE.search(w) or _ends_sentence(w) for w in raw[1:-1])


def judge_production(data: dict, cut: dict | None = None) -> tuple[list[tuple], collections.Counter]:
    """The review's caption measures on the production call (collect_production)."""
    table, tot = [], collections.Counter()
    for run, v in sorted(data["runs"].items()):
        bursts, rate = v["bursts"], v.get("rate") or {}
        top = v.get("top_fs") or 142
        tot["clips"] += 1
        tot["bursts"] += len(bursts)
        sizes = {b["fs"] or top for b in bursts}
        tot[f"clips at {'/'.join(str(x) for x in sorted(sizes, reverse=True))}"] += 1
        kept = {_key(e.get("kept_short", "")) for e in (rate.get("recut") or []) if e.get("kept_short")}
        clip = rate.get("clip_size") or {}
        under = {_key(e.get("text", "")) for e in clip.get("under") or []}
        tot["bursts under the clip's size (recorded)"] += len(clip.get("under") or [])
        tot["bursts with no legal form at any size (recorded)"] += len(clip.get("no_legal_form") or [])
        for i, b in enumerate(bursts):
            raw = b.get("raw") or " ".join(b["lines"]).split()
            dur = b["hide"] - b["show"]
            tot["bursts at the top size"] += (b["fs"] or top) == top
            if i and abs(bursts[i - 1]["hide"] - b["show"]) < 1e-3:
                tot["back-to-back pairs"] += 1
                tot["plate HEIGHT steps back-to-back"] += bursts[i - 1]["h"] != b["h"]
                if (bursts[i - 1]["fs"] or top) != (b["fs"] or top):
                    # a step is recorded where one of the two is a burst the clip's record names as standing at
                    # its own smaller size (clip_size "under": no legal lines at the clip's size and no legal split)
                    ok = any(_key(" ".join(x.get("raw") or x["lines"])) in under for x in (bursts[i - 1], b))
                    tot["size steps back-to-back"] += 1
                    tot[f"size steps back-to-back {'recorded' if ok else 'UNRECORDED'}"] += 1
                    table.append((run, "captions_916", f"size step at {b['show']:.2f}s",
                                  "pass (recorded)" if ok else "FAIL"))
            if dur < MIN_S - 1e-6:
                ok = _key(" ".join(raw)) in kept
                tot["under 0.8 s"] += 1
                tot[f"under 0.8 s {'recorded' if ok else 'UNRECORDED'}"] += 1
                table.append((run, "captions_916", f"short {dur:.2f}s: {' '.join(raw)}",
                              "pass (recorded)" if ok else "FAIL"))
            tot["under 1.0 s"] += dur < 1.0 - 1e-6
            tot["one-word bursts"] += len(raw) == 1
            tot["bursts straddling a clause"] += straddles_clause(raw)
            if len(b["lines"]) == 2:
                tot["two-line bursts"] += 1
                k = len(b["lines"][0].split())
                l1, l2 = raw[:k], raw[k:]
                if l1 and l2:
                    unit = line_policy.phrase_unit(l1[:-1], l1[-1], l2[0]) is not None
                    tot["line breaks inside a phrase unit"] += unit
                    fault = bool(line_policy.violations([" ".join(l1), " ".join(l2)])) or any(
                        len(x) == 1 and line_policy.is_function(x[0]) for x in (l1, l2))
                    tot["two-line bursts with a line fault"] += fault
                    if b.get("one_line_fits"):
                        tot["two-line bursts ONE line holds at the clip size"] += 1
                        if fault:
                            tot["faulted two-line bursts ONE line holds UNRECORDED"] += 1
                            table.append((run, "captions_916", f"one line holds: {' / '.join(b['lines'])}", "FAIL"))
            if i + 1 < len(bursts) and raw and not _ends_sentence(raw[-1]):
                nxt = bursts[i + 1].get("raw") or " ".join(bursts[i + 1]["lines"]).split()
                if nxt and line_policy.phrase_unit(raw[:-1], raw[-1], nxt[0]):
                    tot["burst cuts inside a phrase unit"] += 1
            if b.get("starts") and len(b["starts"]) == len(raw):
                for j, start in enumerate(b["starts"]):
                    shown = next((t for t, vis in b["steps"] if vis > j), None)
                    if shown is not None and shown - start < -0.25:
                        tot["words shown > 250 ms before spoken"] += 1
                        tot["worst early (ms)"] = max(tot["worst early (ms)"], round((start - shown) * 1000))
    for run, v in sorted((cut or {}).get("runs", {}).items()):
        rate = v.get("rate") or {}
        kept = {_key(e.get("kept_short", "")) for e in (rate.get("recut") or []) if e.get("kept_short")}
        for b in v["bursts"]:
            raw = b.get("raw") or " ".join(b["lines"]).split()
            dur = b["hide"] - b["show"]
            if dur < MIN_S - 1e-6:
                ok = _key(" ".join(raw)) in kept
                tot["cut grid: under 0.8 s"] += 1
                tot[f"cut grid: under 0.8 s {'recorded' if ok else 'UNRECORDED'}"] += 1
                if not ok:
                    table.append((run, "captions_916 (cut grid)", f"short {dur:.2f}s: {' '.join(raw)}", "FAIL"))
    return table, tot


def production(dev: Path) -> int:
    tree = Path(_arg("--tree", str(ROOT)))
    grid = float(_arg("--cut-grid", "3.1"))

    def got(flag: str, g: float) -> dict:
        if _arg(flag):
            return json.loads(Path(_arg(flag)).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "prod.json"
            subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--collect", str(tree), str(dev),
                            str(out)] + ([str(g)] if g else []), check=True, stdout=subprocess.DEVNULL)
            return json.loads(out.read_text(encoding="utf-8"))
    data, cut = got("--prod-data", 0.0), got("--prod-cut-data", grid)
    table, tot = judge_production(data, cut)
    if "--quiet" not in sys.argv:
        print("run | surface | measure | result")
        for row in table:
            print(" | ".join(row))
    print(f"\ntree: {data.get('tree')} (production call; cut grid {grid:g} s)")
    for k in sorted(tot):
        print(f"{k}: {tot[k]}")
    n, pairs = max(1, tot["bursts"]), max(1, tot["back-to-back pairs"])
    print(f"share at the top size: {tot['bursts at the top size'] / n:.3f}")
    print(f"two-line share: {tot['two-line bursts'] / n:.3f}")
    print(f"plate-height-change rate: {tot['plate HEIGHT steps back-to-back'] / pairs:.3f}")
    bad = sum(v for k, v in tot.items() if k.endswith("UNRECORDED"))
    print(f"unrecorded violations: {bad}")
    return 1 if bad else 0


def main() -> int:
    dev = Path(_arg("--dev") or os.environ.get("RR_DEV_CHECKOUT") or ".")
    if "--production" in sys.argv:
        return production(dev)
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
