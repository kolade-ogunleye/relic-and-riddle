"""A provider shut by a limit or cap is said on the dashboard, with the date it comes back; one health
line says how the system has been doing (production 2026-10-24: every Claude call had failed for a day
with the log the only place that knew, and the operator had to ask for run counts, failure counts and
run lengths)."""

import json

from src import config, control, improve, provider_status


def test_a_shut_provider_is_recorded_with_its_date_and_dropped_when_it_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    message = ("You have reached your specified API usage limits. You will regain access on 2026-11-01 at 00:00 UTC")
    provider_status.note_capped("claude", "the usage limit is reached", until=provider_status.until_from(message))
    assert provider_status.until_from(message) == "2026-11-01T00:00:00+00:00"
    assert "claude" in provider_status.capped()
    provider_status.note_capped("gemini-billed", "cap spent", until="2000-01-01T00:00:00+00:00")  # long past
    assert set(provider_status.capped()) == {"claude"}
    line = provider_status.dashboard_lines()[0]
    assert line.startswith("⛔ <b>Claude is unavailable</b> until 1 Nov:") and "fall back to Gemini" in line
    provider_status.clear("claude")
    assert provider_status.capped() == {}


def test_the_dashboard_says_which_provider_is_shut(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    provider_status.note_capped("claude", "the usage limit is reached", until="2999-01-01T00:00:00+00:00")
    text, _ = control.dashboard_text_and_buttons(db, None, assets_dir=tmp_path)
    assert "<b>Needs you</b>" in text and "⛔ <b>Claude is unavailable</b> until 1 Jan" in text


def test_a_usage_limit_error_from_claude_is_noted_and_a_success_clears_it(tmp_path, monkeypatch):
    import anthropic
    import httpx
    from src import llm
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "model_chain", lambda models=None: ["claude-opus-5-5"])
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    err = anthropic.BadRequestError("You have reached your specified API usage limits. You will regain access on "
                                    "2026-11-01 at 00:00 UTC", response=response, body=None)

    def refuse(model):
        raise err
    try:
        llm._first_request(refuse)
    except llm.CreditExhausted:
        pass
    else:
        raise AssertionError("a usage limit must raise CreditExhausted")
    assert provider_status.capped()["claude"]["until"] == "2026-11-01T00:00:00+00:00"
    assert llm._first_request(lambda model: "ok") == ("ok", "claude-opus-5-5")
    assert provider_status.capped() == {}


def test_the_health_line_counts_runs_reviews_failures_lengths_and_audit_findings(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    topic = db.list_all_topics()[0]["id"]
    for run_id, status in (("run_1", "simulated"), ("run_2", "failed"), ("run_3", "rejected")):
        db.create_run(run_id, topic)
        db.update_run(run_id, status)
        (tmp_path / run_id).mkdir()
    for run_id, end in (("run_1", "10:20"), ("run_3", "10:30")):
        (tmp_path / run_id / "timings.json").write_text(json.dumps([
            {"stage": "script_and_fact_check", "seconds": 300, "at": "2026-10-24T10:05:00Z"},
            {"stage": "review_proxy", "seconds": 15, "at": f"2026-10-24T{end}:00Z"}]), encoding="utf-8")
    improve.record(db, "ui.placeholder_name", "by -", "self_audit", run_id="run_3")
    assert control.health_line(db) == "🩺 Last 7 days · 3 run(s) · 2 reached review · 1 failed · median 25 min · self-audit: 1 finding(s)"
