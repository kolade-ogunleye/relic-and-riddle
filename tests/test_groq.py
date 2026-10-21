"""The second free model: strict schema, text-only, used for light tasks only when switched on,
before (never instead of) the paid fallback's caps; a 429 moves on."""

import json

import pytest
from pydantic import BaseModel

from src import config, free_llm, gemini, groq


class _Out(BaseModel):
    answer: str


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok = status, body, 200 <= status < 300

    def json(self):
        return self._body


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free")
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free")
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: (_ for _ in ()).throw(gemini.QuotaExhausted("429")))


def test_light_text_task_uses_groq_when_switched_on(keys, monkeypatch):
    sent = []
    monkeypatch.setattr(config, "GROQ_LIGHT_FALLBACK", True)
    monkeypatch.setattr(groq.requests, "post", lambda url, json, **k: sent.append(json) or _Resp(200, {
        "choices": [{"message": {"content": '{"answer": "from groq"}'}}], "usage": {}}))
    assert free_llm.structured("s", "q", _Out, task_class="light").answer == "from groq"
    fmt = sent[0]["response_format"]
    assert fmt["json_schema"]["strict"] is True and fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_groq_is_never_used_when_off_for_heavy_tasks_or_for_images(keys, monkeypatch):
    called = []
    monkeypatch.setattr(groq.requests, "post", lambda *a, **k: called.append(1))
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", False)
    for flag, task, prompt in ((False, "light", "q"), (True, "heavy", "q"), (True, "light", [{"type": "text", "text": "q"}])):
        monkeypatch.setattr(config, "GROQ_LIGHT_FALLBACK", flag)
        with pytest.raises(free_llm.FreeQuotaUnavailable):
            free_llm.structured("s", prompt, _Out, task_class=task)
    assert called == []


def test_groq_limit_moves_on(keys, monkeypatch):
    monkeypatch.setattr(config, "GROQ_LIGHT_FALLBACK", True)
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", False)
    monkeypatch.setattr(groq.requests, "post", lambda *a, **k: _Resp(429, {"error": {"message": "rate"}}))
    with pytest.raises(free_llm.FreeQuotaUnavailable):
        free_llm.structured("s", "q", _Out, task_class="light")


def test_strict_schema_makes_optional_fields_nullable_and_required():
    class M(BaseModel):
        a: str
        b: int = 3
    s = groq.strict_schema(M)
    assert s["required"] == ["a", "b"] and s["properties"]["b"]["type"] == ["integer", "null"]
    assert "default" not in json.dumps(s) and s["additionalProperties"] is False


def test_light_text_tasks_use_their_own_free_model_and_photos_do_not(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free")
    monkeypatch.setattr(config, "SCRIPT_ENGINE", "free")
    monkeypatch.setattr(config, "FREE_LIGHT_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(gemini, "structured", lambda s, p, schema, model=None, key=None: seen.append(model) or _Out(answer="x"))
    free_llm.structured("s", "text", _Out, task_class="light")
    free_llm.structured("s", [{"type": "text", "text": "photo"}], _Out, task_class="light")   # photo review
    free_llm.structured("s", "text", _Out, task_class="heavy")
    assert seen == ["gemini-3.6-flash", config.FREE_WRITER_MODEL, config.FREE_WRITER_MODEL]
