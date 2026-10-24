"""The log says what matters (2026-10-24: hundreds of tracebacks for Telegram long-poll timeouts, a
"message to edit not found" warning on every Refresh Progress, and the billed-key notice on every call
buried the real errors in both environments' logs)."""

import logging

import pytest

import main
from src import free_llm, gemini, trace_check
from src.telegram_bot import TelegramApprovalBot, transport_error


def test_transport_failures_are_told_apart_from_our_own_errors():
    assert transport_error(RuntimeError("Telegram getUpdates failed: HTTP 502 Bad Gateway"))
    assert transport_error(RuntimeError("Telegram getUpdates failed: HTTPSConnectionPool(host='api.telegram.org', "
                                        "port=443): Read timed out. (read timeout=35)"))
    assert transport_error(RuntimeError("Telegram getUpdates failed: still rate-limited"))
    assert not transport_error(KeyError("chat"))
    assert not transport_error(RuntimeError("Telegram sendMessage failed: HTTP 400 Bad Request: can't parse entities"))


def test_polling_backs_off_quietly_on_transport_failures_and_still_reports_the_rest(monkeypatch, caplog):
    waits = []
    monkeypatch.setattr(main.time, "sleep", lambda s: waits.append(s))
    failures = [RuntimeError("Telegram getUpdates failed: HTTP 502 Bad Gateway")] * 3 + [KeyError("boom"), KeyboardInterrupt()]

    class Bot:
        def poll_once(self, handler):
            raise failures.pop(0)
    with caplog.at_level(logging.INFO):
        with pytest.raises(KeyboardInterrupt):
            main.poll_loop(Bot(), None)
    assert waits == [10, 20, 40, 10]  # growing for the gateway, the usual wait for our own error
    quiet = [r for r in caplog.records if "Telegram unreachable" in r.getMessage()]
    assert len(quiet) == 3 and all(r.levelno == logging.WARNING and r.exc_info is None for r in quiet)
    loud = [r for r in caplog.records if "polling error" in r.getMessage()]
    # A masking formatter may have turned exc_info into exc_text already (after setup_logging ran).
    assert len(loud) == 1 and (loud[0].exc_info is not None or loud[0].exc_text)


def _bot(db, monkeypatch, api=None):
    bot = TelegramApprovalBot(db=db, bot_token="123:offline", chat_id="555", approver_ids={"555"})
    calls = []
    monkeypatch.setattr(bot, "_api", api or (lambda method, **kw: calls.append((method, kw.get("data", {}))) or {"message_id": 300}))
    return bot, calls


def test_refresh_progress_redraws_the_card_without_editing_the_one_it_deleted(db, monkeypatch, caplog):
    bot, calls = _bot(db, monkeypatch)
    db.create_run("run_p", db.list_all_topics()[0]["id"])
    db.set_active_progress("run_p", "T", 2, 5, "d", message_id=5)
    real = db.get_active_progress
    monkeypatch.setattr(db, "get_active_progress", lambda: dict(real() or {}, worker_state="running"))
    with caplog.at_level(logging.INFO):
        assert bot.bring_progress_down()
    methods = [m for m, _ in calls]
    assert "editMessageText" not in methods and methods.count("sendMessage") == 1
    assert any(m == "deleteMessage" and d.get("message_id") == 5 for m, d in calls)
    assert bot._live_progress_card("run_p") == 300          # the worker goes on updating the new card
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_message_already_cleared_is_noted_not_warned(db, monkeypatch, caplog):
    def gone(method, **kw):
        raise RuntimeError("Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found")
    bot, _ = _bot(db, monkeypatch, gone)
    with caplog.at_level(logging.INFO):
        assert bot.edit_message_text(7, "x") is False
    assert 7 in bot._missing_messages
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("no longer in the chat" in r.getMessage() for r in caplog.records)


def test_the_billed_key_notice_is_said_once_per_reason(db, monkeypatch, caplog):
    from src import config
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key-123")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "paid-key-456")
    monkeypatch.setattr(config, "PAID_FALLBACK_HEAVY_MONTHLY_USD", 10.0)
    monkeypatch.setattr(free_llm, "ALLOW_PAID_FALLBACK", True)
    monkeypatch.setattr(free_llm, "_announced", set())
    monkeypatch.setattr("src.db.PipelineDatabase", lambda *a, **k: db)
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: trace_check.TraceReport(claims=[]))
    monkeypatch.setattr(gemini, "last_usage", lambda: (1000, 100))
    why = "heavy tasks use the billed key here (FREE_TIER_HEAVY=false)"
    with caplog.at_level(logging.DEBUG, logger="src.free_llm"):
        for _ in range(3):
            free_llm._paid_fallback("s", "p", trace_check.TraceReport, "gemini-3.8-flash", "heavy", why)
    notices = [r for r in caplog.records if "using the billed key" in r.getMessage()]
    assert [r.levelno for r in notices] == [logging.INFO, logging.DEBUG, logging.DEBUG]


def test_a_failed_improvement_cycle_is_said_in_telegram():
    from pathlib import Path
    source = Path(main.__file__).read_text(encoding="utf-8")  # the file, whatever other tests did to main.main
    cycle = source[source.index("def _improvement_cycle"):source.index("def weekly_digest")]
    assert "bot.notify(" in cycle[cycle.index("Improvement cycle failed"):]
