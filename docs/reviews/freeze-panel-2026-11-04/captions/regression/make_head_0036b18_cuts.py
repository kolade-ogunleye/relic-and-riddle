"""Writes head_0036b18_cuts.json: where HEAD 0036b18 (the round-4 phrasing, fix round 1's base) ends its 9:16 bursts,
16:9 bursts and sidecar cues, for the five real freeze-panel runs (fixtures, own words.json timings) and the test
narrations of tests/test_freeze_captions_2026_11_04.py (synthetic edge texts and script-library scripts, labelled by
their ids, with that file's harness timings). Run with the HEAD worktree as --code-root:

    python make_head_0036b18_cuts.py --code-root <HEAD worktree> --out head_0036b18_cuts.json
"""
import argparse
import importlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]


def timed(text):   # tests/test_freeze_captions_2026_11_04.py's harness timings (copied verbatim)
    words, at = [], 0.25
    for w in text.split():
        letters = len(re.sub(r"[^\w]", "", w))
        dur = round(0.11 + 0.055 * letters, 3)
        words.append({"word": w, "start": round(at, 3), "dur": dur})
        at += dur + 0.04
        if re.search(r"[.!?…]$", w):
            at += 0.45
        elif re.search(r"[,;:]$", w):
            at += 0.18
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", required=True)
    ap.add_argument("--out", default=str(HERE / "head_0036b18_cuts.json"))
    a = ap.parse_args()
    sys.path.insert(0, a.code_root)
    vc = importlib.import_module("src.video_composer")
    sys.path.insert(0, str(REPO / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
    import fixtures as fx
    tests = (REPO / "tests/test_freeze_captions_2026_11_04.py").read_text("utf-8")
    ns: dict = {}
    synth_src = tests[tests.index("SYNTH = {"):tests.index("REAL = [")]
    exec(synth_src, ns)
    real = re.search(r"REAL = (\[[^\]]*\])", tests).group(1)
    cases = {nid: (timed(text), emph) for nid, (text, emph) in ns["SYNTH"].items()}
    for topic in json.loads(real.replace("'", '"')):
        d = json.loads((REPO / "data/script_library" / topic / "verified_script.json").read_text("utf-8"))
        cases[topic] = (timed(d["narration_script"]), list(d.get("emphasis_words") or []))
    for c in fx.load("captions"):
        cases["run:" + c["topic"]] = (fx.words(c), list(c.get("emphasis_words") or []))

    def cuts(phrases):
        out, at = [], -1
        for p in phrases:
            at += len(p)
            out.append(at)
        return out[:-1]
    result = {}
    for case, (words, emph) in cases.items():
        hedge_of = vc.hedge_figures(words, emph)
        bonds = vc.caption_bonds(words) | {b for h, f in hedge_of.items() for b in range(h, f)}

        def fits(ph):
            return vc._fits_wide(ph, 1440)
        wide = vc.merge_short_bursts(vc.partition_words_grammatical(
            words, vc.CAPTION_WIDE_MIN_WORDS, vc.CAPTION_WIDE_TARGET_WORDS, vc.CAPTION_WIDE_MAX_WORDS,
            vc.CAPTION_WIDE_MAX_CHARS, fits=fits, glance=(24, vc.CAPTION_WIDE_MAX_CHARS), bonds=bonds),
            fits, vc.CAPTION_WIDE_MAX_WORDS)
        reel = vc.merge_short_bursts(vc.partition_words_grammatical(words, bonds=bonds), vc._fits_caption_width,
                                     vc.MAX_BURST_WORDS)
        cues = [t.split() for _, _, t in vc.partition_sidecar_cues(words)]
        result[case] = {"words": [w["word"] for w in words], "9:16": cuts(reel), "16:9": cuts(wide),
                        "cues": cuts(cues)}
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(result), "cases ->", a.out)


if __name__ == "__main__":
    main()
