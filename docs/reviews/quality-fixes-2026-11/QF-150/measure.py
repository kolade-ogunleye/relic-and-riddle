"""QF-150 measurement: the script-stage caption break lint against the composer's records on real runs.

For each run folder with a words.json (the voiced, aligned words), the truth is what the render records today:
video_composer.caption_phrases on the real timed words (with the script's emphasis words), and its never-end burst ends
(never_end_bursts) and line defects (line_defect_bursts). The prediction is the lint: the same planning on the
script's narration_script at nominal timing (script_engine.nominal_caption_words), before any voice exists.

Matched by (kind, index of the word the defect ends on); also by sentence and by run. Writes counts and run folder
names only (never narration text): measurements.json beside this file.

    python docs/reviews/quality-fixes-2026-11/QF-150/measure.py <worktree> <dev output dir> <prod output dir>
"""
import json
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, sys.argv[1])
    from src import script_engine as se

    rows = []
    for env, root in (("dev", Path(sys.argv[2])), ("prod", Path(sys.argv[3]))):
        for d in sorted(root.glob("run_*")):
            if not (d / "words.json").exists():
                continue
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
            sf = d / "verified_script.json"
            script = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
            emphasis = script.get("emphasis_words")
            narration = script.get("narration_script") or " ".join(w["word"] for w in words)
            truth = se.caption_break_findings(narration, emphasis, words=words)
            pred = se.caption_break_findings(narration, emphasis)
            t, p = {(f["kind"], f["at"]) for f in truth}, {(f["kind"], f["at"]) for f in pred}

            def key(s: str) -> str:
                return " ".join(s.lower().split())
            st, sp = {key(f["sentence"]) for f in truth}, {key(f["sentence"]) for f in pred}
            rows.append({
                "env": env, "run": d.name, "script": bool(script),
                "tokens_match": se.caption_tokens(narration) == [w["word"] for w in words],
                "sentences": len(se._split_sentences(narration)),
                "truth_burst": sum(f["kind"] == "burst" for f in truth),
                "truth_line": sum(f["kind"] == "line" for f in truth),
                "pred_burst": sum(f["kind"] == "burst" for f in pred),
                "pred_line": sum(f["kind"] == "line" for f in pred),
                "defect_tp": len(t & p), "defect_fp": len(p - t), "defect_fn": len(t - p),
                "sentence_tp": len(st & sp), "sentence_fp": len(sp - st), "sentence_fn": len(st - sp),
                "flagged_sentences": len(sp)})

    def total(key: str, env: str | None = None) -> int:
        return sum(r[key] for r in rows if env is None or r["env"] == env)
    tp, fp, fn = total("defect_tp"), total("defect_fp"), total("defect_fn")
    stp, sfp, sfn = total("sentence_tp"), total("sentence_fp"), total("sentence_fn")
    stn = total("sentences") - stp - sfp - sfn
    flagged = [r for r in rows if r["pred_burst"] + r["pred_line"]]
    recorded = [r for r in rows if r["truth_burst"] + r["truth_line"]]
    summary = {
        "runs": len(rows), "dev": sum(r["env"] == "dev" for r in rows), "prod": sum(r["env"] == "prod" for r in rows),
        "tokens_match": sum(r["tokens_match"] for r in rows),
        "recorded": {"burst_ends": total("truth_burst"), "lines": total("truth_line"), "runs": len(recorded)},
        "predicted": {"burst_ends": total("pred_burst"), "lines": total("pred_line"), "runs": len(flagged),
                      "runs_dev": sum(r["env"] == "dev" for r in flagged),
                      "runs_prod": sum(r["env"] == "prod" for r in flagged),
                      "flag_rate": round(len(flagged) / max(1, len(rows)), 3),
                      "sentences": total("flagged_sentences")},
        "defect": {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / max(1, tp + fp), 3),
                   "recall": round(tp / max(1, tp + fn), 3)},
        "sentence": {"tp": stp, "fp": sfp, "fn": sfn, "tn": stn, "precision": round(stp / max(1, stp + sfp), 3),
                     "recall": round(stp / max(1, stp + sfn), 3), "false_positive_rate": round(sfp / max(1, sfp + stn), 4)},
        "run": {"tp": sum(1 for r in rows if r in flagged and r in recorded),
                "fp": sum(1 for r in flagged if r not in recorded),
                "fn": sum(1 for r in recorded if r not in flagged),
                "tn": sum(1 for r in rows if r not in flagged and r not in recorded)},
        "misses": [{k: r[k] for k in ("env", "run", "defect_tp", "defect_fp", "defect_fn", "sentence_fp", "sentence_fn",
                                      "tokens_match")}
                   for r in rows if r["defect_fp"] or r["defect_fn"] or r["sentence_fp"] or r["sentence_fn"]],
    }
    out = Path(__file__).with_name("measurements.json")
    out.write_bytes((json.dumps({"summary": summary, "runs": rows}, indent=1) + "\n").encode("utf-8"))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
