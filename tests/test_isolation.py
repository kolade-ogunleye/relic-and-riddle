import pytest
import requests


def test_real_network_is_blocked():
    with pytest.raises(Exception, match="may not open real network connections"):
        requests.get("https://api.telegram.org", timeout=5)


def test_coordinator_alerts_never_reach_telegram(db):
    from main import PipelineCoordinator
    coord = PipelineCoordinator(db)
    assert not coord.telegram_bot.is_configured()
    assert coord.telegram_bot.notify("would have been a real message") is False
