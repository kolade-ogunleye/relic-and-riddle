import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import audio_engine  # noqa: E402
from src.db import PipelineDatabase  # noqa: E402

CURATED = ROOT / "data" / "scripts" / "derinkuyu_underground_city.json"


@pytest.fixture(autouse=True)
def default_licences(monkeypatch):
    """Tests see the default licence policy (no CC BY-SA), whatever the developer's .env says."""
    from src import config
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", False)
    monkeypatch.setattr(config, "BEAT_IMAGES_ALLOW_SHARE_ALIKE", False)
    # Nor the developer's formats, Shorts switch or retry waits (a test never sleeps a real minute).
    monkeypatch.setattr(config, "VIDEO_FORMATS", ["reel"])
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", False)
    monkeypatch.setattr(config, "SCENE_PLAN_RETRY_SECONDS", 0.0)
    # Nor the deployment's model and dry-run choices: production's FREE_TIER_HEAVY=false and
    # CLAUDE_FALLBACK=true failed 8 tests in rc8's on-host suite (2026-10-23), which rolled the update back.
    monkeypatch.setattr(config, "FREE_TIER_HEAVY", True)
    monkeypatch.setattr(config, "CLAUDE_FALLBACK", False)
    monkeypatch.setattr(config, "GEMINI_SPARE_KEY_PERMITTED", False)
    monkeypatch.setattr(config, "ELEVENLABS_IN_DRY_RUNS", False)
    monkeypatch.setattr(config, "MOTION_IN_DRY_RUNS", False)
    monkeypatch.setattr(config, "BOT_RUNS_DRY", True)
    monkeypatch.setattr(config, "SPEECH_SPEEDUP_MAX", 1.08)
    from src import improve
    improve._buffer.clear()  # audit notes buffered by one test are not another test's findings


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
    # Settings a developer's .env changes must not change test outcomes; tests opt in explicitly.
    monkeypatch.setattr("src.config.VOICE_REQUIRE_LICENSED", False)
    monkeypatch.setattr("src.config.PAID_FALLBACK_LIGHT_MONTHLY_USD", 0.0)
    monkeypatch.setattr("src.config.GROQ_LIGHT_FALLBACK", False)  # a local .env never changes test behaviour
    monkeypatch.setattr("src.config.FREE_LIGHT_MODEL", "")
    monkeypatch.setattr("src.config.BACKUP_TRANSPORT", "sync-folder")
    monkeypatch.setattr("src.config.GEMINI_SPARE_KEY_PERMITTED", False)
    monkeypatch.setattr("src.upscale.ONNX_MODEL", Path(__file__).parent / "no_such_model.onnx")  # tests opt in to real inference
    monkeypatch.setattr("src.config.PAID_FALLBACK_HEAVY_MONTHLY_USD", 0.0)
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 0.0)
    monkeypatch.setattr("src.audio_engine.VOICE_PREFER", "elevenlabs")  # the code's default
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr("src.telegram_bot.TELEGRAM_APPROVER_IDS", set())
    monkeypatch.setattr("src.audio_engine.ELEVENLABS_API_KEY", "")
    monkeypatch.setattr("src.audio_engine.AZURE_SPEECH_KEY", "")
    monkeypatch.setattr("src.audio_engine.AZURE_SPEECH_REGION", "")
    monkeypatch.setattr("src.control.HEALTHCHECK_PING_URL", "")
    # Every credential the log masker knows, blanked in config too: a failing test once printed a real key.
    from src import config as _config
    from src.ops import SecretMasker
    for _name in SecretMasker.SECRET_NAMES:
        monkeypatch.setattr(_config, _name, "")
    monkeypatch.setattr("src.config.COMMONS_CONTACT", "")

    def blocked(*args, **kwargs):
        raise RuntimeError("Tests may not open real network connections; mock the call instead.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    # Scripts are measured by speaking them (edge-tts); offline that is "unmeasurable". Tests of the
    # measurement itself use the real_measure fixture.
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: None)


_REAL_MEASURE = audio_engine.measure_spoken_seconds


@pytest.fixture
def real_measure():
    return _REAL_MEASURE


@pytest.fixture(autouse=True)
def no_repo_writes(monkeypatch, tmp_path_factory):
    """Tests must never change the repository's own data: operator issue reports go to a temp
    folder, and music use counts to a temp copy of the library (orchestrator tests pick real
    tracks). An earlier version filed ~90 fake issues and inflated real track use counts."""
    from src import music
    sandbox = tmp_path_factory.mktemp("repo_writes")
    monkeypatch.setattr("src.config.ISSUES_DIR", sandbox / "issues")
    monkeypatch.setattr("src.config.LOG_DIR", sandbox / "logs")
    monkeypatch.setattr("src.audio_engine.PACE_FILE", sandbox / "voice_pace.json")
    monkeypatch.setattr("src.script_engine.WORD_PACE_FILE", sandbox / "word_pace.json")
    # A test that spends a budget to its cap must not mark the real environment's provider as shut.
    monkeypatch.setattr("src.provider_status._file", lambda: sandbox / "provider_status.json")
    # Pool preparation fetches in a child process the socket block can't reach; tests opt in.
    monkeypatch.setattr("src.asset_pipeline.ensure_pool", lambda topic, assets_dir=None: True)
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
    payload.update({"topic_id": "derinkuyu_underground_city", "origin": "curated",
                    "location": {"name": "Derinkuyu", "country": "Turkey"}})
    return payload


@pytest.fixture
def delivered_review(db, tmp_path):
    """Seed a real artifact binding; old tests must not approve an unbound card."""
    from src.telegram_state import initialize, put, review_fingerprint
    def prepare(run_id, message_id):
        initialize(db)
        directory = tmp_path / ("review_" + run_id)
        directory.mkdir(exist_ok=True)
        (directory / "master.mp4").write_bytes(b"offline video")
        (directory / "thumbnail_cover.jpg").write_bytes(b"offline cover")
        if not db.get_run(run_id):
            db.create_run(run_id, "01_topic")
        db.update_run(run_id, "pending_approval", master_video_path=directory / "master.mp4")
        db.record_approval(run_id, "01_topic", message_id)
        fingerprint = review_fingerprint(db, run_id)
        put(db, "review-media", run_id, fingerprint)
        put(db, "review-card", f"{run_id}:{message_id}", fingerprint)
        return directory
    return prepare
