"""DRY: the exact requests the SUBJECT-VISION evaluation would send (no network, no key read), and their estimated
cost. For each labelled photograph: the system text and question the prototype puts (src/beat_images.picture_look with
PICTURE_SUBJECT_LOOK on, src/subject_look.py), the context it is given, the image at the pool screen's size
(beat_images.TEXT_SCREEN_SIZE, the look every pool photograph gets), and the token estimate.

Writes SV_SCRATCH/requests.jsonl and prints the per-photograph and whole-evaluation estimate.
Token basis: the dev runs' recorded picture checks (output/run_*/llm_calls.jsonl, task picture_check, model
gemini-3.7-flash): median 1,565 input and 106 output tokens per look. Added by the subject question: its system text
and the context (characters / 4) plus the schema's five fields (~60 tokens), and ~90 output tokens (name, scene, box,
one not-subject entry with its box, confidence). Prices: the repo's own table, src/free_llm.PAID_PRICES (a model not
listed is priced as the writer model: USD 0.75 / 3.75 per million input / output tokens)."""
import io
import json
import statistics
import sys

from sv_common import LABELS, RR_DEV, SCRATCH, contexts, photo_path

from pathlib import Path
WT = Path(__file__).resolve().parents[5]          # the worktree holding this script
sys.path.insert(0, str(WT))
from src import beat_images, free_llm, subject_look   # noqa: E402
from src.llm import image_block                       # noqa: E402

BASE_IN, BASE_OUT = 1565, 106          # recorded medians, picture_check on gemini-3.7-flash (62 dev calls)
# Second basis: what development's billed key actually charged per light call (data/pipeline.db llm_usage, task
# paid-fallback:light, gemini-3.7-flash, 1,011 calls 2026-10-20 .. 2026-11-18): mean 2,087 in / 447 out, USD 0.00324
BILLED_IN, BILLED_OUT, BILLED_MEAN_USD = 2087, 447, 0.00324
SCHEMA_IN, EXTRA_OUT = 60, 90
REPEATS = 30                           # the stability sample asked a second time


def main():
    price_in, price_out = free_llm.PAID_PRICES.get(free_llm.config.FREE_CHECKER_MODEL, free_llm.PAID_PRICES["gemini-3.8-flash"])
    ctx = contexts()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    rows, extra_in = [], []
    with open(SCRATCH / "requests.jsonl", "w", encoding="utf-8", newline="\n") as out:
        for lab in LABELS:
            p = photo_path(lab)
            query = ctx[lab["id"]]["topic"]
            question = f"Searched for: {query}\nDoes this picture show it?" + subject_look.context_text(ctx[lab["id"]])
            img = image_block(p, beat_images.TEXT_SCREEN_SIZE)
            add = (len(subject_look.SUBJECT_SYSTEM) + len(subject_look.context_text(ctx[lab["id"]]))) / 4 + SCHEMA_IN
            extra_in.append(add)
            r = {"id": lab["id"], "path": str(p), "size": beat_images.TEXT_SCREEN_SIZE, "query": query,
                 "question": question, "context": ctx[lab["id"]], "image_b64_chars": len(img["source"]["data"]),
                 "extra_input_tokens_est": round(add)}
            rows.append(r)
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    med_add = statistics.median(extra_in)
    extra_cost = med_add / 1e6 * price_in + EXTRA_OUT / 1e6 * price_out
    full_in, full_out = BASE_IN + med_add, BASE_OUT + EXTRA_OUT
    full_cost = full_in / 1e6 * price_in + full_out / 1e6 * price_out
    n_eval = len(LABELS) + REPEATS
    with_claim = sum(1 for r in rows if r["context"]["claim"])
    with_title = sum(1 for r in rows if r["context"]["title"])
    print(f"requests: {len(rows)} photographs (+{REPEATS} repeats = {n_eval} calls); context: title {with_title}, "
          f"slide claim {with_claim}")
    print(f"system text: check {len(beat_images.PICTURE_SYSTEM)} chars + subject {len(subject_look.SUBJECT_SYSTEM)} chars")
    print(f"added per look: ~{med_add:.0f} input tokens (median; p90 {sorted(extra_in)[int(.9*len(extra_in))]:.0f}), "
          f"~{EXTRA_OUT} output tokens")
    print(f"prices (src/free_llm.PAID_PRICES, {free_llm.config.FREE_CHECKER_MODEL} priced as listed/writer): "
          f"${price_in}/M in, ${price_out}/M out")
    print(f"EXTENSION in a run (the look is made anyway): +${extra_cost:.5f} per photograph on the billed key, $0 on the free key")
    print(f"EVALUATION as its own calls: ~{full_in:.0f} in / {full_out} out per call = ${full_cost:.5f}; "
          f"{n_eval} calls = ${n_eval * full_cost:.2f}; at the 300-call ceiling = ${300 * full_cost:.2f}")
    billed = (BILLED_IN + med_add) / 1e6 * price_in + (BILLED_OUT + EXTRA_OUT) / 1e6 * price_out
    print(f"EVALUATION on dev's measured billed light-call basis ({BILLED_IN} in / {BILLED_OUT} out, ${BILLED_MEAN_USD} "
          f"a call): ${billed:.5f} per call with the subject question; {n_eval} calls = ${n_eval * billed:.2f}; "
          f"300 = ${300 * billed:.2f}")


if __name__ == "__main__":
    main()
