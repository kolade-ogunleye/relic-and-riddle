"""QF-99 acceptance: a text request carries the thinking level its caller asked for.

    python measure.py <repo root>

Runs the code of <repo root> (the base too) with the transport mocked: every request is recorded, no model is called.
- requests: for each effort a caller can ask ("low", "medium", "high", none), the thinking level the Interactions
  request carried, sent through free_llm.call (the path every light text task takes) and gemini.structured directly.
- groq: the reasoning_effort the light Groq fallback sent for "low" and for none.
- direct_calls_dropping_effort: gemini.structured / groq.structured calls in src/ that pass no thinking level on
  (acceptance: 0).
- light_text_calls_recorded: real_data.json (development runs, committed): the recorded calls of the tasks that ask
  "low"/"medium" on the text path, which went out at the default level before (the "before" latency and tokens).
"""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
here = Path(__file__).resolve().parent

from pydantic import BaseModel

import src.ops
from src import config, free_llm, gemini, groq
from src.db import PipelineDatabase


class Answer(BaseModel):
    text: str


sent = []


def post(url, headers=None, json=None, timeout=None):
    sent.append(json)
    if "groq" in url:
        data = {"choices": [{"message": {"content": '{"text": "ok"}'}}], "usage": {}}
    else:
        data = {"status": "completed", "usage": {"total_input_tokens": 1, "total_output_tokens": 1},
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"text": "ok"}'}]}]}
    return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: data)


gemini.requests.post = post
groq.requests.post = post
config.SCRIPT_ENGINE, config.GEMINI_FREE_API_KEY, config.GROQ_API_KEY = "free", "free-key", "groq-key"
free_llm.enabled = lambda: True
free_llm.available = lambda: True
src.ops.record_llm_call = lambda *a, **k: None   # nothing is written to a run's call log or the database
PipelineDatabase.record_llm_usage = lambda *a, **k: None
groq.available = lambda: True


def level(body):
    return (body.get("generation_config") or {}).get("thinking_level")


requests = {}
for effort in ("low", "medium", "high", None):
    sent.clear()
    kwargs = {"effort": effort} if effort else {}
    free_llm.call("s", "p", Answer, role="writer", task_class="light", max_tokens=100, **kwargs)
    via_free = level(sent[-1])
    sent.clear()
    gemini.structured("s", "p", Answer, key="k", **({"thinking": effort} if effort else {}))
    requests[str(effort)] = {"asked": effort, "sent_via_free_llm": via_free, "sent_direct": level(sent[-1])}

groq_sent = {}
for effort in ("low", None):
    sent.clear()
    try:
        groq.structured("s", "p", Answer, **({"thinking": effort} if effort else {}))
    except TypeError:   # the base takes no effort at all
        groq.structured("s", "p", Answer)
    groq_sent[str(effort)] = sent[-1].get("reasoning_effort")

dropping = []
for path in sorted((root / "src").glob("*.py")):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "structured"
                and isinstance(node.func.value, ast.Name) and node.func.value.id in ("gemini", "groq")
                and not any(k.arg in ("thinking", None) for k in node.keywords)):
            dropping.append(f"{path.name}:{node.func.value.id}.structured")

light = [k for k, v in requests.items() if v["asked"] in ("low", "medium")]
print(json.dumps({
    "requests": requests,
    "light_requests_carrying_their_level": sum(requests[k]["sent_via_free_llm"] == requests[k]["asked"]
                                               and requests[k]["sent_direct"] == requests[k]["asked"] for k in light),
    "light_requests": len(light),
    "writer_high_sends_a_level": requests["high"]["sent_via_free_llm"] is not None or requests["high"]["sent_direct"] is not None,
    "groq_reasoning_effort": groq_sent,
    "direct_calls_dropping_effort": len(dropping),
    "direct_calls_dropping_effort_list": dropping,
    "light_text_calls_recorded": json.loads((here / "real_data.json").read_text(encoding="utf-8")),
}, indent=1))
