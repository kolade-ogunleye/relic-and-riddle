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


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch):
    """
    Tests must never reach a real service. Credentials read from the developer's .env are
    blanked, and any real network connection fails the test outright. (An earlier version
    let orchestrator tests send real Telegram alerts such as "Publish outcome unknown for r1".)
    """
    import socket

    monkeypatch.setattr("src.gemini.GEMINI_API_KEY", "")
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ELEVENLABS_API_KEY", "UPLOAD_POST_API_KEY", "GEMINI_API_KEY",
                 "KLING_API_KEY", "AZURE_SPEECH_KEY",
                 "META_ACCESS_TOKEN", "META_PAGE_ACCESS_TOKEN", "HEALTHCHECK_PING_URL"):
        monkeypatch.setattr(f"src.config.{name}", "")
    monkeypatch.setattr("src.config.TELEGRAM_APPROVER_IDS", set())
    monkeypatch.setattr("src.config.GEMINI_FREE_API_KEY", "")
    monkeypatch.setattr("src.config.SCRIPT_ENGINE", "claude")  # free-engine tests opt in explicitly
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_APPROVER_IDS", set())
    monkeypatch.setattr("src.audio_engine.ELEVENLABS_API_KEY", "")
    monkeypatch.setattr("src.audio_engine.AZURE_SPEECH_KEY", "")
    monkeypatch.setattr("src.audio_engine.AZURE_SPEECH_REGION", "")
    monkeypatch.setattr("src.control.HEALTHCHECK_PING_URL", "")

    def blocked(*args, **kwargs):
        raise RuntimeError("Tests may not open real network connections; mock the call instead.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def no_repo_writes(monkeypatch, tmp_path_factory):
    """Tests must never change the repository's own data: operator issue reports go to a temp
    folder, and music use counts to a temp copy of the library (orchestrator tests pick real
    tracks). An earlier version filed ~90 fake issues and inflated real track use counts."""
    from src import music
    sandbox = tmp_path_factory.mktemp("repo_writes")
    monkeypatch.setattr("src.config.ISSUES_DIR", sandbox / "issues")
    library = sandbox / "library.json"
    if music.LIBRARY.exists():
        library.write_bytes(music.LIBRARY.read_bytes())
    monkeypatch.setattr(music, "LIBRARY", library)


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
