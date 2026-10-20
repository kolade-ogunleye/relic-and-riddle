"""When the free Gemini quota runs out, a capped task class may use the billed key; spend is recorded
and the cap holds."""
import pytest

from src import config, free_llm, gemini
from src.db import PipelineDatabase



@pytest.fixture
def keys(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key-123")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "paid-key-456")
    monkeypatch.setattr(config, "PAID_FALLBACK_LIGHT_MONTHLY_USD", 5.0)
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 0.0)
    db = PipelineDatabase(db_path=tmp_path / "t.db", topic_bank_file=tmp_path / "none.json")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: db)
    calls = []

    def fake(system, prompt, schema, model=None, key=None):
        calls.append(key)
        if key == "free-key-123":
            raise gemini.QuotaExhausted("Gemini quota exceeded (HTTP 429).")
        gemini._set_usage(1_000_000, 100_000)  # $0.75 + $0.375
        return "ok"
    monkeypatch.setattr(gemini, "structured", fake)
    return calls, db


def test_light_task_falls_back_and_records_spend(keys):
    calls, db = keys
    assert free_llm.structured("s", "p", object, task_class="light") == "ok"
    assert calls == ["free-key-123", "paid-key-456"]
    assert free_llm._paid_spend("light") == pytest.approx(1.125)


def test_heavy_task_without_a_cap_stays_free_only(keys):
    with pytest.raises(free_llm.FreeQuotaUnavailable):
        free_llm.structured("s", "p", object, task_class="heavy")


def test_cap_is_respected(keys, monkeypatch):
    calls, db = keys
    monkeypatch.setattr(config, "PAID_FALLBACK_LIGHT_MONTHLY_USD", 1.0)
    free_llm.structured("s", "p", object, task_class="light")   # spends $1.125, reaching the cap
    with pytest.raises(free_llm.FreeQuotaUnavailable, match="monthly cap"):
        free_llm.structured("s", "p", object, task_class="light")


def test_evals_never_spend(keys, monkeypatch):
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", False)
    with pytest.raises(free_llm.FreeQuotaUnavailable):
        free_llm.structured("s", "p", object, task_class="light")
