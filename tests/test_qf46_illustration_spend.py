"""QF-46: illustration requests bill GEMINI_API_KEY, so each one is asked of the monthly media cap
(MEDIA_MONTHLY_BUDGET_USD, shared with AI motion) before it is sent and ledgered in media_usage after an HTTP 200,
shown on the dashboard as its own "AI images" spend line. No real request is made: requests.post is a fake."""

import base64
import json

import pytest

from src import config, control, image_gen, media_budget
from src.db import PipelineDatabase

MODEL = "gemini-3-pro-image"


class _Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def _image_body():
    return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                                                                  "data": base64.b64encode(b"img").decode()}}]}}]}


@pytest.fixture
def tier(tmp_path, monkeypatch):
    """The illustration tier switched on against a scratch ledger; every request counted, none sent."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k-test", raising=False)
    monkeypatch.setattr(config, "GENERATIVE_GAP_FILLING", True, raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", MODEL, raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_API", "auto", raising=False)
    monkeypatch.setattr(config, "IMAGE_GEN_COST_USD", 0.14, raising=False)
    monkeypatch.setattr(image_gen, "is_configured", lambda v: bool(v))
    monkeypatch.setattr(image_gen, "list_models", lambda timeout=15.0: [MODEL])
    image_gen._STATUS.clear()
    db = PipelineDatabase(tmp_path / "pipeline.db")
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: db)
    sent = []
    state = {"status": 200}

    def post(url, headers=None, json=None, timeout=None):
        sent.append(url)
        return _Resp(_image_body() if state["status"] == 200 else {"error": "x"}, state["status"])
    monkeypatch.setattr(image_gen.requests, "post", post)
    yield {"db": db, "sent": sent, "state": state, "tmp": tmp_path}
    image_gen._STATUS.clear()


def _rows(db):
    with db._conn() as conn:
        return [dict(r) for r in conn.execute("SELECT provider, item, cost_usd FROM media_usage")]


def test_a_zero_media_budget_means_a_run_makes_no_image_request(tier, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 0.0, raising=False)
    monkeypatch.setattr(image_gen, "checkers_ready", lambda: (True, ""))
    budget = image_gen.Budget(tier["tmp"] / "run", limit=8)
    for line in ("The fort was rebuilt in stone.", "Merchants crossed the pass in winter."):
        art, _info = image_gen.illustrate_beat(line, {"era": "medieval", "required": []}, tier["tmp"] / "run",
                                               budget=budget, candidates=4)
        assert art is None
    assert image_gen.generate_raw_linework("an engraving of a harbour") is None
    assert tier["sent"] == [] and _rows(tier["db"]) == []


def test_a_fake_200_ledgers_one_row_shown_on_the_dashboard(tier, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, raising=False)
    assert image_gen.generate_image("draw") == b"img"
    assert len(tier["sent"]) == 1
    assert _rows(tier["db"]) == [{"provider": media_budget.AI_IMAGES, "item": MODEL, "cost_usd": 0.14}]
    lines = control.spend_lines(tier["db"])
    assert any(line.startswith("AI images this month: $0.14") for line in lines), lines
    assert any(line.startswith("AI motion this month: $0.00") for line in lines), lines   # not counted as motion
    assert "AI images $0.14" in control.spend_summary(tier["db"])


def test_a_failed_request_is_not_ledgered(tier, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, raising=False)
    tier["state"]["status"] = 500
    assert image_gen.generate_image("draw") is None
    assert len(tier["sent"]) == 1 and _rows(tier["db"]) == []


def test_the_cap_is_shared_with_motion_and_stops_requests(tier, monkeypatch):
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 0.30, raising=False)
    assert image_gen.generate_image("one") == b"img"
    assert image_gen.generate_image("two") == b"img"      # $0.28 spent of $0.30
    assert image_gen.generate_image("three") is None      # $0.42 would exceed the cap
    assert len(tier["sent"]) == 2
    tier["db"].record_media_usage("veo", "clip.mp4", 4.95)
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, raising=False)
    assert image_gen.generate_image("four") is None       # motion's spend leaves no room
    assert len(tier["sent"]) == 2
    assert media_budget.spent(tier["db"], media_budget.AI_IMAGES) == pytest.approx(0.28)
