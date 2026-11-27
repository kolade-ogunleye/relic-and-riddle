"""SENDS the SUBJECT-VISION evaluation's requests. NOT RUN by the builder (2026-11-27): the development picture check
was not established as free (HANDBACK-subjectvision-c6.md, "Spend"). Run only on the operator's decision.

What it does, when run with --send in an environment the operator has chosen (its process environment; this script
never reads a .env file and never prints a key):
- the production path: src/beat_images.picture_look at TEXT_SCREEN_SIZE with PICTURE_SUBJECT_LOOK switched on in
  THIS process only, the photograph's context (sv_common.contexts) beside the question;
- FREE ONLY, enforced in this process: SCRIPT_ENGINE must be "free" (no Claude engine); free_llm.free_only() and
  ALLOW_PAID_FALLBACK=False (no billed Gemini key, no Claude fallback); every request below free_llm is counted and a
  request on any key but "free" stops the script before it is sent;
- serial (one request at a time, the client's own retry and backoff), with a hard ceiling of --budget requests
  (default 300, the operator's ceiling) counted across every pass in SV_SCRATCH/requests_sent.json;
- pass "first": every labelled photograph; pass "repeat": the 30 of sv_common.stability_ids() again.
Answers: SV_SCRATCH/answers_<pass>.jsonl, one line per photograph (resumable: a photograph already answered is
skipped). Usage: python ask.py --send first ; python ask.py --send repeat"""
import argparse
import json
import sys
import time
from pathlib import Path

from sv_common import LABELS, SCRATCH, contexts, photo_path, stability_ids

WT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(WT))


class Stop(Exception):
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pass_", choices=("first", "repeat"))
    ap.add_argument("--send", action="store_true", help="actually send (without it: print what would be sent)")
    ap.add_argument("--budget", type=int, default=300)
    ap.add_argument("--billed", action="store_true",
                    help="operator-approved Option B (2026-11-27): the billed Gemini light key only, "
                         "never the free key production shares; stops at --budget requests or --usd")
    ap.add_argument("--usd", type=float, default=1.20)
    a = ap.parse_args()
    ids = [lab["id"] for lab in LABELS] if a.pass_ == "first" else stability_ids()
    out = SCRATCH / f"answers_{a.pass_}.jsonl"
    done = set()
    if out.is_file():
        done = {json.loads(l)["id"] for l in open(out, encoding="utf-8") if l.strip()}
    todo = [i for i in ids if i not in done]
    if not a.send:
        print(f"dry: would ask {len(todo)} photographs ({len(done)} already answered) -> {out}")
        return
    from src import beat_images, config, free_llm, gemini
    if not free_llm.enabled():
        raise SystemExit("SCRIPT_ENGINE is not 'free': the picture check would run on Claude. Not sent.")
    want = "billed" if a.billed else "free"
    if a.billed:
        # This process only: the free key is blanked so no request draws on the quota production shares; the
        # client then goes straight to its billed light path, which keeps its own monthly cap and records each
        # call's cost through its normal accounting. No Claude fallback.
        if not config.is_configured(config.GEMINI_API_KEY) or config.GEMINI_API_KEY == config.GEMINI_FREE_API_KEY:
            raise SystemExit("No separate billed Gemini key in this environment. Not sent.")
        config.GEMINI_FREE_API_KEY = ""
        config.GEMINI_FREE_API_KEY_SPARE = ""
        config.CLAUDE_FALLBACK = False
    else:
        if not free_llm.available():
            raise SystemExit("No free Gemini key in this environment. Not sent.")
        free_llm.ALLOW_PAID_FALLBACK = False        # this process only: no billed key, no Claude fallback
    config.PICTURE_SUBJECT_LOOK = True              # this process only
    counter = SCRATCH / "requests_sent.json"
    state = json.load(open(counter)) if counter.is_file() else {}
    sent, usd = state.get("sent", 0), state.get("usd", 0.0)
    tok_in, tok_out = state.get("tokens_in", 0), state.get("tokens_out", 0)
    price_in, price_out = free_llm.PAID_PRICES.get(config.FREE_CHECKER_MODEL, free_llm.PAID_PRICES["gemini-3.8-flash"])

    def save():
        counter.write_text(json.dumps({"sent": sent, "key": want, "tokens_in": tok_in, "tokens_out": tok_out,
                                       "usd": round(usd, 6)}), encoding="utf-8")
    real_ask = free_llm._ask

    def counted(key_name, *args, **kwargs):
        nonlocal sent, usd, tok_in, tok_out
        if key_name != want:
            raise Stop(f"a request on the {key_name!r} key was about to be sent; stopped")
        if sent >= a.budget:
            raise Stop(f"the {a.budget}-request ceiling is reached")
        if a.billed and usd >= a.usd:
            raise Stop(f"the ${a.usd:.2f} ceiling is reached")
        sent += 1
        save()
        result = real_ask(key_name, *args, **kwargs)
        if a.billed:
            ti, to = gemini.last_usage()
            tok_in, tok_out = tok_in + (ti or 0), tok_out + (to or 0)
            usd += (ti or 0) / 1e6 * price_in + (to or 0) / 1e6 * price_out
            save()
        return result
    free_llm._ask = counted
    ctx = contexts()
    by_id = {lab["id"]: lab for lab in LABELS}
    import contextlib
    guard = contextlib.nullcontext() if a.billed else free_llm.free_only()
    with guard, open(out, "a", encoding="utf-8", newline="\n") as f:
        for i in todo:
            lab = by_id[i]
            started, before = time.perf_counter(), sent
            try:
                look = beat_images.picture_look(photo_path(lab), ctx[i]["topic"], beat_images.TEXT_SCREEN_SIZE,
                                                context=ctx[i])
            except Stop as e:
                print(e); break
            tokens_in, tokens_out = gemini.last_usage()
            f.write(json.dumps({"id": i, "look": look, "seconds": round(time.perf_counter() - started, 2),
                                "requests": sent - before, "tokens_in": tokens_in, "tokens_out": tokens_out},
                               ensure_ascii=False) + "\n")
            f.flush()
            if not look.get("checked"):
                print(f"{i}: not looked at ({look.get('reason')}); stopping (quota or outage)")
                break
    print(f"requests sent so far: {sent} of {a.budget}" + (f"; ${usd:.4f} of ${a.usd:.2f}" if a.billed else ""))


if __name__ == "__main__":
    main()
