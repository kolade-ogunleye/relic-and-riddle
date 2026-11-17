"""QF-51 (project review 2026-11-05): config drift.

The three YOUTUBE_ANALYTICS_* credentials were read from os.environ inside src/youtube_analytics.py and were
missing from the log masker's SECRET_NAMES; the launcher's RELIC_DEPLOY_REQUEST/STATUS and
RELIC_RELEASE_RECEIPT were documented nowhere in .env.example; and the settings inventory matched only a
literal os.getenv("X"), so neither showed up. No real request is made: requests.post is mocked.
"""
import importlib
import re
from pathlib import Path

from src import config, youtube_analytics
from src.ops import SecretMasker

ROOT = Path(__file__).resolve().parent.parent
YT = ("YOUTUBE_ANALYTICS_CLIENT_ID", "YOUTUBE_ANALYTICS_CLIENT_SECRET", "YOUTUBE_ANALYTICS_REFRESH_TOKEN")


def test_youtube_analytics_settings_live_in_config_and_the_masker() -> None:
    for name in YT:
        assert hasattr(config, name), name
        assert name in SecretMasker.SECRET_NAMES, name
    source = (ROOT / "src/youtube_analytics.py").read_text(encoding="utf-8")
    assert not re.search(r"\bos\.(getenv|environ)", source)


def test_a_youtube_refresh_token_never_reaches_a_log_line(monkeypatch) -> None:
    monkeypatch.setattr(config, "YOUTUBE_ANALYTICS_CLIENT_SECRET", "GOCSPX-client-secret-qf51")
    monkeypatch.setattr(config, "YOUTUBE_ANALYTICS_REFRESH_TOKEN", "1//refresh-token-value-qf51")
    masked = SecretMasker().mask("refresh failed for 1//refresh-token-value-qf51 with GOCSPX-client-secret-qf51")
    assert "refresh-token-value-qf51" not in masked and "client-secret-qf51" not in masked


def test_youtube_analytics_uses_the_config_values(monkeypatch) -> None:
    for name in YT:
        monkeypatch.delenv(name, raising=False)   # the environment no longer decides
    monkeypatch.setattr(config, "YOUTUBE_ANALYTICS_CLIENT_ID", "cid.apps")
    monkeypatch.setattr(config, "YOUTUBE_ANALYTICS_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setattr(config, "YOUTUBE_ANALYTICS_REFRESH_TOKEN", "refresh-abc")
    youtube_analytics._token.update(value="", expires=0.0)
    sent = []

    class Resp:
        status_code, text = 200, ""

        def json(self):
            return {"access_token": "tok-123456", "expires_in": 3600}

    monkeypatch.setattr(youtube_analytics.requests, "post", lambda url, data, timeout: sent.append(data) or Resp())
    assert youtube_analytics.configured()
    assert youtube_analytics.access_token() == "tok-123456"
    assert sent == [{"client_id": "cid.apps", "client_secret": "secret-xyz", "refresh_token": "refresh-abc",
                     "grant_type": "refresh_token"}]
    assert "secret-xyz" in youtube_analytics.secrets() and "refresh-abc" in youtube_analytics.secrets()
    youtube_analytics._token.update(value="", expires=0.0)


def test_every_variable_the_launcher_sets_is_documented() -> None:
    launcher = (ROOT / "deployment/windows/runtime/Start-ContentVmWorkload.ps1").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    names = set(re.findall(r"\$env:(RELIC_[A-Z0-9_]+)\s*=", launcher)) | {"RELIC_APP_ROOT"}
    assert {"RELIC_DEPLOY_REQUEST", "RELIC_DEPLOY_STATUS", "RELIC_RELEASE_RECEIPT"} <= names
    missing = sorted(n for n in names if not re.search(rf"\b{n}\b", example))
    assert not missing, missing


def test_the_inventory_sees_helpers_constants_and_dynamic_reads() -> None:
    inventory = importlib.import_module("tests.test_settings_inventory")
    names, unresolved = inventory.reads_in(
        'DEPLOY_ENV = "RELIC_X_PATH"\n'
        'a = _env("YT_SECRET")\n'
        'b = os.environ.get(DEPLOY_ENV, "")\n'
        "c = os.environ['SUBSCRIPTED']\n"
        'os.environ["ASSIGNED_ONLY"] = "1"\n'
        "d = os.getenv(name)\n")
    assert names == {"YT_SECRET", "RELIC_X_PATH", "SUBSCRIPTED"}
    assert unresolved == {"os.getenv(name"}
