"""QF-126b round 3: what the credits hold tells the operator about each real stored copy, against what writing the
credits again does (read only on the data; each run staged in a temporary folder; counts only).

Usage: python hold_probe.py <repo root> <a folder of run_* folders> <page ids JSON> [<rows JSON>]
Each run's stored copy (script_metadata.json, as stored: no source_links.json) goes through the release gate's texts
(publishable.payload_credit_texts, as release.py reads them) and ledger_credit_problems. Then its credits are written
again as a re-send does (add_provenance_attributions with the lookup, served from the page ids JSON: no network), and
the gate reads the result. Counts:
  told_fit: held with "They fit"; told_fit_but_resend_held: of those, still held after the re-send (a false "fit")
  told_over_but_resend_fits: held with "N characters over" (or cut) although the re-send posts it (a false "over")
  truly_over: held with "over" and still held after the re-send, with the figure the hold states (round 4: the
  9-digit bound) and by how much it is over with the real ids, which the re-send has recorded (source_links.json)
The per-run rows (run names) go to the optional rows JSON, which is not committed.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    os.chdir(root)
    import measure

    from src import credits, publishable
    ids = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))["page_ids"]
    runs = [r for r in measure.real_runs(sys.argv[2]) if measure.photos_asked(r) or measure.tracks_asked(r)]

    def gate(payload, ledger):
        texts, limits = publishable.payload_credit_texts(payload, publishable.is_documentary(payload))
        return credits.ledger_credit_problems(ledger, texts, limits), texts, limits

    out = {"runs": len(runs), "not_held_as_stored": 0, "told_fit": 0, "told_fit_but_resend_held": 0,
           "told_over_but_resend_fits": 0, "truly_over": 0, "truly_over_stated": [], "truly_over_real_id_margins": [],
           "other_hold": 0}
    rows = []
    tmp = Path(tempfile.mkdtemp(prefix="qf126b-hold-"))
    try:
        for n, run in enumerate(runs):
            ledger = measure.stage(tmp / str(n), run)
            problems, texts, limits = gate(copy.deepcopy(run["payload"]), ledger)
            resent = copy.deepcopy(run["payload"])
            credits.add_provenance_attributions(resent, ledger,
                                                lookup=lambda urls: {u: ids[u] for u in urls if u in ids})
            held_after = bool(gate(resent, ledger)[0])
            text = " ".join(problems)
            over = re.search(r"characters over|cut at the platform|the platform takes", text)
            if not problems:
                kind = "not_held_as_stored"
            elif "They fit" in text and not over:
                kind = "told_fit_but_resend_held" if held_after else "told_fit"
                out["told_fit"] += 1
            elif over:
                kind = "truly_over" if held_after else "told_over_but_resend_fits"
            else:
                kind = "other_hold"
            if kind != "told_fit":
                out[kind] += 1
            row = {"run": run["name"], "kind": kind}
            if kind == "truly_over":
                stated = re.search(r"([\d,]+) characters over", text)
                row["stated_over"] = int(stated.group(1).replace(",", "")) if stated else None
                out["truly_over_stated"].append(row["stated_over"])
                name = next(iter(texts))
                bound = getattr(credits, "_with_page_id_bound", lambda r: r)(
                    credits.required_credits(json.loads(ledger.read_text(encoding="utf-8")), ledger.parent))
                row["real_ids_over"] = credits._credits_over(texts[name][:limits[name]], bound, limits[name])
                out["truly_over_real_id_margins"].append(row["real_ids_over"])
            rows.append(row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if len(sys.argv) > 4:
        Path(sys.argv[4]).write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
