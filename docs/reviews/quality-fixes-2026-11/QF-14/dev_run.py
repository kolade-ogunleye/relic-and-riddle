"""QF-14 development run (operator, 2026-11-10): one free-tier, dry, script-generation run on one real topic whose
current opening fails the hook rule, to measure whether the real writer reaches a passing opening in one edit.

    RELIC_APP_ROOT=<development checkout> python dev_run.py <code root> <topic id> [--check]

RELIC_APP_ROOT is the launcher's own variable (src/service_runtime.py): the app reads that checkout's .env as it
always does. This script never reads, prints or copies it, and prints no key, path or identifier. `--check` makes no
call: it prints the route and stops.

It calls only ScriptEngine.generate_script, the narrowest entry point: the writer, its opening edit and its
structural checks, with spoken-length measurement (edge-tts, free). No fact check, voice, render, Telegram or post.
For the run, in this process only:
- calls are held to the free quota (free_llm.free_only);
- the paid fallbacks are capped at 0, Claude as a last resort is off and so is the spare key;
- the topic's cached fact sheet is used as loaded, so no research is rebuilt (the run stops if it is missing or stale);
- the word-pace history is read but not written, so the development data is untouched.
It prints JSON: the route, each draft's opening and hook findings, each edit, the model calls, and the outcome.
"""

import json
import sys
import time
from pathlib import Path


def main(root: Path, topic_id: str, check: bool) -> dict:
    sys.path.insert(0, str(root))
    from src import config
    route = {"script_engine": config.SCRIPT_ENGINE, "free_tier_heavy": config.FREE_TIER_HEAVY,
             "free_key_configured": config.is_configured(config.GEMINI_FREE_API_KEY),
             "writer_model": config.FREE_WRITER_MODEL}
    out: dict = {"route": route, "topic": topic_id}
    if not (route["script_engine"] == "free" and route["free_tier_heavy"] and route["free_key_configured"]):
        out["stopped"] = "not the free tier: no call made"
        return out
    config.PAID_FALLBACK_HEAVY_MONTHLY_USD = 0.0
    config.PAID_FALLBACK_LIGHT_MONTHLY_USD = 0.0
    config.CLAUDE_FALLBACK = False
    config.GEMINI_SPARE_KEY_PERMITTED = False
    from src import fact_sheet, free_llm
    from src import script_engine as se
    bank = json.loads((config.DATA_DIR / "topics" / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    topic = next(t for t in bank if t["id"] == topic_id)
    sheet = fact_sheet.load(topic_id)
    out["fact_sheet_cached"] = bool(sheet and len(sheet.get("facts", [])) >= fact_sheet.MIN_VERIFIED_FACTS)
    out["route"]["paid_fallbacks"] = "0 in this process"
    if not out["fact_sheet_cached"] or check:
        out["stopped"] = "check only" if check else "no fresh cached fact sheet: research would run; no call made"
        return out

    calls, drafts, edits = [], [], []
    real_call = free_llm.call

    def counted_call(system, prompt, schema, role="writer", task_class="heavy", **kw):
        calls.append({"role": role, "task_class": task_class, "schema": getattr(schema, "__name__", str(schema))})
        return real_call(system, prompt, schema, role=role, task_class=task_class, **kw)

    def opening(pkg):
        first = se._first_sentence(pkg.narration_script)
        return {"first_sentence": first, "spoken_words": len(se._spoken_words(first)),
                "hook_findings": [p.split(":", 2)[1].strip() if p.startswith(se.HOOK_SIGNAL_PROBLEM) else "cap or label"
                                  for p in se.hook_problems(pkg.narration_script)],
                "signal": se.hook_signal(first)}

    writer = se.ScriptEngine()
    writer._sheets[topic_id] = sheet
    real_write, real_revise = writer._generate_with_claude, writer._revise_surgically

    def write(t, feedback):
        pkg = real_write(t, feedback)
        drafts.append(opening(pkg))
        return pkg

    def revise(previous, findings, s):
        pkg = real_revise(previous, findings, s)
        edits.append({"kind": findings.split(":", 1)[0], "result": opening(pkg) if pkg is not None else None})
        return pkg

    free_llm.call = counted_call
    se.record_word_pace = lambda *a, **k: None
    writer._generate_with_claude, writer._revise_surgically = write, revise
    started = time.time()
    try:
        with free_llm.free_only():
            payload = writer.generate_script(topic)
        out["outcome"] = "script"
        out["final"] = {"first_sentence": se._first_sentence(payload["narration_script"]),
                        "blocking_hook_faults": len(se.blocking_problems(se.hook_problems(payload["narration_script"]))),
                        "hook_signal_flag": bool(se.hook_signal_note(payload["narration_script"])),
                        "origin": payload.get("origin")}
    except se.ScriptUnavailableError as e:
        out["outcome"] = "no script"
        out["error_head"] = str(e)[:200]
    finally:
        free_llm.call = real_call
    out.update(seconds=round(time.time() - started, 1), model_calls=len(calls),
               model_calls_by_task_class={k: sum(c["task_class"] == k for c in calls) for k in ("heavy", "light")},
               writer_drafts=drafts, edits=edits)
    return out


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    print(json.dumps(main(Path(sys.argv[1]).resolve(), sys.argv[2], "--check" in sys.argv[3:]), indent=1,
                     ensure_ascii=False))
