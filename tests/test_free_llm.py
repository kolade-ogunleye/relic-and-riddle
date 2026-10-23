"""The free model client: what it retries, and what never reaches the billed key."""
import pytest


def test_a_dropped_connection_is_sent_again_on_the_free_key_and_never_billed(monkeypatch):
    import requests
    from pydantic import BaseModel
    from src import free_llm, gemini

    class Out(BaseModel):
        ok: bool

    calls = []

    def post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed'))")
        raise requests.ReadTimeout("read timed out")
    monkeypatch.setattr(free_llm, "available", lambda: True)
    monkeypatch.setattr(gemini, "is_configured", lambda key: True)
    monkeypatch.setattr(free_llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(gemini.requests, "post", post)
    monkeypatch.setattr(free_llm, "_paid_fallback", lambda *a, **k: (_ for _ in ()).throw(AssertionError("billed")))
    with pytest.raises(free_llm.LLMError, match="timed out"):  # the drop was retried; a timeout is not
        free_llm.structured("s", "p", Out)
    assert len(calls) == 2
