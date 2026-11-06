"""The hook form's soft check, and YouTube's retention curve (2026-11-01)."""

from types import SimpleNamespace

import pytest

from src import audience, config, hook_learning, youtube_analytics

QUESTION_HOOK = "How did a whole city stay hidden under Cappadocia?"


def _coordinator(db, payload, retry_hook, retry_passes=True, retry_raises=False, monkeypatch=None, plain_verify=False):
    import main
    calls = []

    class FakeEngine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            calls.append(revision_notes)
            if previous is not None and revision_notes.startswith("Hook form"):
                if retry_raises:
                    raise RuntimeError("writer down")
                return dict(previous, hook_sentence=retry_hook)
            return dict(payload)

        def verify_claims(self, p, topic=None, allow_unreviewed_curated=False):
            ok = p.get("hook_sentence") != retry_hook or retry_passes
            if plain_verify:   # as the real verify_claims: it does not set guardrail_passed itself
                return ok, dict(p), "PASSED" if ok else "REJECTED: unsupported claim in the hook."
            return ok, dict(p, guardrail_passed=ok), "PASSED" if ok else "REJECTED: unsupported claim in the hook."

        def _load_curated(self, topic_id):
            return None

    db.create_run("run_x", db.list_all_topics()[0]["id"])
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.script_engine = db, FakeEngine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    monkeypatch.setattr(hook_learning, "for_run", lambda db, run_id, topic: ("question", "test"))
    monkeypatch.setattr("src.engagement_learning.for_run", lambda db, run_id: (None, "test"))
    return c, calls


def _payload(curated_payload):
    return dict(curated_payload, origin="claude", hook_sentence="Beneath a house in Cappadocia, a city lay hidden.")


def test_a_draft_in_another_form_is_revised_once_and_kept_when_it_passes(db, tmp_path, curated_payload, monkeypatch):
    c, calls = _coordinator(db, _payload(curated_payload), QUESTION_HOOK, monkeypatch=monkeypatch)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["hook_sentence"] == QUESTION_HOOK
    assert out["hook_plan"] == {"asked": "question", "written": "question", "revised": True, "why": "test"}
    assert sum(1 for n in calls if n.startswith("Hook form")) == 1


def test_a_revision_that_fails_the_fact_check_is_dropped(db, tmp_path, curated_payload, monkeypatch):
    c, _ = _coordinator(db, _payload(curated_payload), QUESTION_HOOK, retry_passes=False, monkeypatch=monkeypatch)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["hook_sentence"].startswith("Beneath a house") and out["hook_plan"]["revised"] is False


def test_a_revision_in_the_wrong_form_again_is_dropped(db, tmp_path, curated_payload, monkeypatch):
    c, _ = _coordinator(db, _payload(curated_payload), "A city lay sealed beneath the fields.", monkeypatch=monkeypatch)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["hook_plan"]["written"] == "discovery" and out["hook_plan"]["revised"] is False


def test_a_failing_writer_never_costs_the_passed_draft(db, tmp_path, curated_payload, monkeypatch):
    c, _ = _coordinator(db, _payload(curated_payload), QUESTION_HOOK, retry_raises=True, monkeypatch=monkeypatch)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["guardrail_passed"] and out["hook_plan"]["revised"] is False


def test_a_draft_already_in_form_is_not_revised(db, tmp_path, curated_payload, monkeypatch):
    c, calls = _coordinator(db, dict(_payload(curated_payload), hook_sentence=QUESTION_HOOK), QUESTION_HOOK,
                            monkeypatch=monkeypatch)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["hook_plan"]["revised"] is None and not any(n.startswith("Hook form") for n in calls)


def test_the_follow_rate_line() -> None:
    rows = [{"run_id": "a", "hook_plan": {"asked": "question", "written": "question", "revised": None}},
            {"run_id": "b", "hook_plan": {"asked": "figure", "written": "figure", "revised": True}},
            {"run_id": "c", "hook_plan": {"asked": "figure", "written": "discovery", "revised": False}}]
    line = hook_learning.follow_rate(rows)
    assert "3 run(s) asked; 1 opened in the asked form at once, 1 after one revision" in line
    assert hook_learning.follow_rate([]) is None


# ---------------------------------------------------------------- YouTube Analytics

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


@pytest.fixture
def yt_env(monkeypatch):
    for k, v in (("YOUTUBE_ANALYTICS_CLIENT_ID", "cid.apps"), ("YOUTUBE_ANALYTICS_CLIENT_SECRET", "secret-xyz"),
                 ("YOUTUBE_ANALYTICS_REFRESH_TOKEN", "refresh-abc")):
        monkeypatch.setattr(config, k, v)  # read from src/config since QF-51, not from os.environ
    youtube_analytics._token.update(value="", expires=0.0)


def test_retention_is_read_and_the_token_is_reused(yt_env, monkeypatch) -> None:
    posts, gets = [], []
    monkeypatch.setattr(youtube_analytics.requests, "post",
                        lambda url, data, timeout: posts.append(data) or _Resp(200, {"access_token": "tok-123456",
                                                                                     "expires_in": 3600}))
    monkeypatch.setattr(youtube_analytics.requests, "get", lambda url, params, headers, timeout: gets.append(
        (params, headers)) or _Resp(200, {"rows": [[0.01, 1.0], [0.5, 0.62], [1.0, 0.41]]}))
    assert youtube_analytics.configured()
    assert youtube_analytics.retention("vid12345") == {"1": 1.0, "50": 0.62, "100": 0.41}
    youtube_analytics.retention("vid12345")
    assert len(posts) == 1 and posts[0]["grant_type"] == "refresh_token"
    params, headers = gets[0]
    assert params["filters"] == "video==vid12345" and params["dimensions"] == "elapsedVideoTimeRatio"
    assert headers["Authorization"] == "Bearer tok-123456"


def test_a_refused_report_raises(yt_env, monkeypatch) -> None:
    monkeypatch.setattr(youtube_analytics.requests, "post",
                        lambda url, data, timeout: _Resp(200, {"access_token": "tok-123456"}))
    monkeypatch.setattr(youtube_analytics.requests, "get", lambda *a, **k: _Resp(403, {"error": "forbidden"}))
    with pytest.raises(RuntimeError, match="HTTP 403"):
        youtube_analytics.retention("vid12345")


def test_video_ids_from_the_short_link() -> None:
    assert youtube_analytics.video_id({"post_urls": {"youtube": "https://youtube.com/shorts/AbC123xyz"}}) == "AbC123xyz"
    assert youtube_analytics.video_id({"post_urls": {"youtube": "https://www.youtube.com/watch?v=Zz9_-8yy"}}) == "Zz9_-8yy"
    assert youtube_analytics.video_id({}) is None


def test_not_configured_does_nothing(monkeypatch) -> None:
    for k in ("YOUTUBE_ANALYTICS_CLIENT_ID", "YOUTUBE_ANALYTICS_CLIENT_SECRET", "YOUTUBE_ANALYTICS_REFRESH_TOKEN"):
        monkeypatch.setattr(config, k, "")
    data, outcomes = {"views": 5}, []
    audience._add_youtube_retention(data, {}, {}, outcomes, "youtube", "r", "1d")
    assert data == {"views": 5} and outcomes == []


def test_the_short_gets_its_curve_and_the_hold_and_beats_read_it(yt_env, monkeypatch) -> None:
    curve = {str(k): round(1.0 - 0.006 * k, 3) for k in range(1, 101)}
    monkeypatch.setattr(youtube_analytics, "retention", lambda vid, published=None: curve)
    data, outcomes = {"views": 100}, []
    audience._add_youtube_retention(data, {"post_urls": {"youtube": "https://youtube.com/shorts/AbC123xyz"}},
                                    {"posted_at": "2026-11-01T12:00:00+00:00"}, outcomes, "youtube", "r", "1d")
    assert data["yt_retention_graph"] == curve and outcomes[0][1:3] == ("youtube-analytics", "ok")
    assert audience.hold_at(data, 50.0) == 0.964                     # 3 s of 50 s: the 6th hundredth
    beats = [{"start": 0.0, "text": "Hook."}, {"start": 10.0, "text": "Middle."}]
    assert [d["text"] for d in audience.beat_drops(data, 50.0, beats)] == ["Hook.", "Middle."]


def test_a_shorts_loops_never_count_as_more_than_everyone() -> None:
    assert audience.hold_at({"yt_retention_graph": {"1": 1.3, "6": 1.2, "100": 0.9}}, 50.0) == 1.0


def test_youtube_secrets_are_redacted(yt_env) -> None:
    assert "secret-xyz" not in audience._redact("failed with secret-xyz and refresh-abc")
    assert "refresh-abc" not in audience._redact("refresh-abc")


def test_nothing_is_steered_before_any_reel_is_measured(db) -> None:
    from src import engagement_learning
    assert hook_learning.for_run(db, "run_x", {"id": "t"})[0] is None
    assert engagement_learning.for_run(db, "run_x")[0] is None


def test_measured_reels_start_the_steer(db, monkeypatch) -> None:
    rows = [{"platform": "instagram", "run_id": "a", "hook_form": "discovery", "skip_rate": 40.0}]
    monkeypatch.setattr(audience, "table", lambda db: rows)
    form, why = hook_learning.for_run(db, "run_x", {"id": "t"})
    assert form in hook_learning.FORMS and why.startswith("exploring")


def test_an_accepted_revision_is_recorded_like_any_passed_draft(db, tmp_path, curated_payload, monkeypatch):
    """PR review (2026-11-02): the revision replaced the payload after guardrail_passed was set, so the fact
    review record raised. It now runs before the share line, credits and the guardrail record."""
    import json as _json

    from src import config as _config
    sheets = tmp_path / "data" / "fact_sheets"
    sheets.mkdir(parents=True)
    (sheets / "t.json").write_text(_json.dumps({"facts": []}), encoding="utf-8")
    monkeypatch.setattr(_config, "DATA_DIR", tmp_path / "data")
    c, _ = _coordinator(db, _payload(curated_payload), QUESTION_HOOK, monkeypatch=monkeypatch, plain_verify=True)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["hook_sentence"] == QUESTION_HOOK and out["hook_plan"]["revised"] is True
    # Operator decision (2026-11-04): the script stage no longer stamps; the stamp comes only after the automated
    # checker runs on the final copy (_verify_final_copy), so the accepted revision carries no stamp yet.
    assert out["guardrail_passed"] is True and "fact_review" not in out
