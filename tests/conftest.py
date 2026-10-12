import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import PipelineDatabase  # noqa: E402

CURATED = ROOT / "data" / "scripts" / "01_derinkuyu_underground_city.json"


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """Tests never call the real API unless they patch it in explicitly."""
    monkeypatch.setattr("src.llm.ANTHROPIC_API_KEY", "")


@pytest.fixture
def db(tmp_path):
    bank = tmp_path / "topic_bank.json"
    bank.write_text(json.dumps({"topics": [
        {"id": f"0{i}_topic", "title": f"Topic {i}", "hook": "h", "location": {"name": "X", "country": "Y"}}
        for i in range(1, 4)
    ]}), encoding="utf-8")
    return PipelineDatabase(db_path=tmp_path / "test.db", topic_bank_file=bank)


@pytest.fixture
def curated_payload():
    data = json.loads(CURATED.read_text(encoding="utf-8"))
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    payload.update({"topic_id": "01_derinkuyu_underground_city", "origin": "curated",
                    "location": {"name": "Derinkuyu", "country": "Turkey"}})
    return payload
