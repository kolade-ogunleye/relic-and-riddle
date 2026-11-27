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
    if not free_llm.available():
        raise SystemExit("No free Gemini key in this environment. Not sent.")
    free_llm.ALLOW_PAID_FALLBACK = False            # this process only: no billed key, no Claude fallback
    config.PICTURE_SUBJECT_LOOK = True              # this process only
    counter = SCRATCH / "requests_sent.json"
    sent = json.load(open(counter)).get("sent", 0) if counter.is_file() else 0
    real_ask = free_llm._ask

    def counted(key_name, *args, **kwargs):
        nonlocal sent
        if key_name != "free":
            raise Stop(f"a request on the {key_name!r} key was about to be sent; stopped")
        if sent >= a.budget:
            raise Stop(f"the {a.budget}-request ceiling is reached")
        sent += 1
        counter.write_text(json.dumps({"sent": sent}), encoding="utf-8")
        return real_ask(key_name, *args, **kwargs)
    free_llm._ask = counted
    ctx = contexts()
    by_id = {lab["id"]: lab for lab in LABELS}
    with free_llm.free_only(), open(out, "a", encoding="utf-8", newline="\n") as f:
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
    print(f"requests sent so far: {sent} of {a.budget}")


if __name__ == "__main__":
    main()
