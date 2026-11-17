"""QF-99: a text call sends the thinking level its caller asked for (WRITER-TIMEOUT diagnosis, defect 3).

The Interactions request (gemini.structured's text path) sent no level, so every light edit asking for effort "low"
or "medium" ran at the model's default, full thinking. Requests are recorded from a mocked transport; no model is
called.
"""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src import config, gemini

ROOT = Path(__file__).resolve().parent.parent


class _Answer(BaseModel):
    text: str


def _interaction(body):
    data = {"status": "completed", "usage": {"total_input_tokens": 10, "total_output_tokens": 5},
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({"text": "ok"})}]}]}
    return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: data)


@pytest.fixture
def sent(monkeypatch):
    bodies = []

    def post(url, headers=None, json=None, timeout=None):
        bodies.append(copy.deepcopy(json))
        return _interaction(json)
    monkeypatch.setattr(gemini.requests, "post", post)
    return bodies


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_a_light_text_call_sends_its_thinking_level(sent, effort):
    gemini.structured("s", "p", _Answer, model="gemini-3.8-flash", key="k", thinking=effort)
    assert sent[0]["generation_config"] == {"thinking_level": effort}


@pytest.mark.parametrize("effort", [None, "high", ""])
def test_the_writer_and_a_call_without_effort_send_none(sent, effort):
    gemini.structured("s", "p", _Answer, model="gemini-3.8-flash", key="k", thinking=effort)
    assert "generation_config" not in sent[0]   # "high" is the default: the writer is unchanged


def test_a_model_that_rejects_the_level_is_asked_again_without_it(monkeypatch):
    bodies = []

    def post(url, headers=None, json=None, timeout=None):
        bodies.append(copy.deepcopy(json))
        if "generation_config" in json:
            err = {"error": {"code": 400, "message": "Unknown field generation_config.thinking_level"}}
            return SimpleNamespace(status_code=400, ok=False, text="", json=lambda: err)
        return _interaction(json)
    monkeypatch.setattr(gemini.requests, "post", post)
    assert gemini.structured("s", "p", _Answer, key="k", thinking="low").text == "ok"
    assert len(bodies) == 2 and "generation_config" in bodies[0] and "generation_config" not in bodies[1]


def test_free_llm_carries_the_callers_effort_to_the_request(sent, monkeypatch):
    from src import free_llm
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free", raising=False)
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key")
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    monkeypatch.setattr(free_llm, "available", lambda: True)
    free_llm.call("s", "p", _Answer, role="writer", task_class="light", effort="low", max_tokens=100)
    assert sent[-1]["generation_config"] == {"thinking_level": "low"}


def test_the_groq_fallback_sends_the_effort_as_reasoning_effort(monkeypatch):
    from src import groq
    bodies = []

    def post(url, json=None, timeout=None, headers=None):
        bodies.append(copy.deepcopy(json))
        data = {"choices": [{"message": {"content": '{"text": "ok"}'}}], "usage": {}}
        return SimpleNamespace(status_code=200, ok=True, text="", json=lambda: data)
    monkeypatch.setattr(config, "GROQ_API_KEY", "g")
    monkeypatch.setattr(groq, "available", lambda: True)
    monkeypatch.setattr(groq.requests, "post", post)
    groq.structured("s", "p", _Answer, thinking="low")
    groq.structured("s", "p", _Answer)
    assert bodies[0]["reasoning_effort"] == "low" and "reasoning_effort" not in bodies[1]


def _calls(name: str):
    """(file, line, keywords) of every call `<module>.structured(...)` named `name` in src/."""
    for path in sorted((ROOT / "src").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "structured"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == name):
                yield path.name, node.lineno, node


def test_every_gemini_or_groq_text_call_passes_an_effort_on():
    """The class: a call that drops its caller's effort. Every direct call passes `thinking=` (or forwards **kwargs,
    as free_llm._ask does)."""
    missing = []
    for name in ("gemini", "groq"):
        for file, line, node in _calls(name):
            forwards = any(k.arg is None for k in node.keywords)
            if not forwards and not any(k.arg == "thinking" for k in node.keywords):
                missing.append(f"{file}:{line} {name}.structured")
    assert not missing, missing
