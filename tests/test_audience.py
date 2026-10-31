"""Audience metrics: every platform and post type is measured, shown, and fed back to the audit ledger."""
import json
from datetime import datetime, timedelta, timezone

from src import audience, config
from src.control import make_command_handler

# The documented answer of GET /api/uploadposts/post-analytics/{request_id} (docs.upload-post.com/api/get-analytics).
DOC_ANSWER = {"success": True, "post": {"request_id": "run_a_1"}, "platforms": {
    "instagram": {"success": True, "post_metrics": {"views": 900, "reach": 700, "likes": 40, "comments": 3, "shares": 7, "saves": 5}},
    "facebook": {"success": True, "post_metrics_error": "Facebook video not found"},
}}


def _publish(db, run_id, channel, response, days_ago):
    db.create_run(run_id, "01_topic")
    db.record_publication("01_topic", run_id, channel, "published", response)
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE run_id = ? AND channel = ?",
                     ((datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(), run_id, channel))


def test_the_documented_answer_is_read_for_every_platform(monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", "k" * 20)
    monkeypatch.setattr(audience, "is_configured", lambda v: True)
    monkeypatch.setattr(audience, "_get", lambda url, params, headers=None: DOC_ANSWER)
    out = audience.upload_post_analytics("run_a_1")
    assert out["instagram"]["views"] == 900 and "unavailable" in out["facebook"]


def test_each_post_type_is_measured_as_what_it_is(db):
    reel = {"run_id": "r1", "channel": "upload-post", "response": {}}
    short = {"run_id": "r1", "channel": "youtube-shorts", "response": {}}
    carousel = {"run_id": "r1", "channel": "upload-post:carousel",
                "response": {"request_id": "r1-carousel-facebook-1", "platforms": ["facebook"], "part": 2}}
    story = {"run_id": "r1", "channel": "upload-post:story", "response": {"request_id": "r1-story-3", "part": 3}}
    assert [t[0] for t in audience.targets(reel, {})] == ["instagram", "facebook"]
    assert audience.targets(short, {}) == [("youtube", "r1:youtube-short", "youtube")]
    assert audience.targets(reel, {"content_format": "deep_dive"}) == [("youtube", "r1", "youtube")]
    assert audience.targets(carousel, {}) == [("facebook_carousel", "r1-carousel-facebook-1", "facebook")]
    assert [t[0] for t in audience.targets(story, {})] == ["instagram_story3", "facebook_story3"]


def test_a_platform_that_cannot_be_measured_never_holds_back_the_next_snapshot(db, monkeypatch):
    _publish(db, "run_a_1", "upload-post", {}, days_ago=2)
    monkeypatch.setattr(audience, "_graph_metrics", lambda label, res, *a, **k: (None, "skipped: test"))
    monkeypatch.setattr(audience, "upload_post_analytics", lambda rid, platform=None: {
        "instagram": DOC_ANSWER["platforms"]["instagram"]["post_metrics"], "facebook": {"unavailable": "not found"}})
    assert audience.collect(db) == 2  # instagram measured, facebook recorded as unavailable
    assert audience.due_snapshots(db) == []  # the 1-day snapshot is complete; nothing retried forever
    text = audience.analytics_text(db)
    assert "Instagram (1d): 900 views" in text and "Facebook (1d): not measurable" in text


def test_a_weak_video_is_filed_once_against_the_channels_own_median(db, monkeypatch):
    rows = [{"run_id": f"r{i}", "platform": "instagram", "snapshot": "7d", "fetched_at": "x", "title": f"T{i}",
             "unavailable": None, "skip_rate": 30.0, "watch_ratio": 0.5, "share_rate": 0.01, "views": 100.0,
             "reach": 100.0, "save_rate": None, "likes": None, "comments": None} for i in range(5)]
    rows[4]["skip_rate"] = 60.0  # twice the channel's median
    monkeypatch.setattr(audience, "table", lambda db: rows)
    assert audience.file_findings(db) == 1
    assert audience.file_findings(db) == 0  # filed once per video
    with db._conn() as conn:
        found = conn.execute("SELECT run_id, klass, detail FROM findings WHERE klass LIKE 'audience.%'").fetchall()
    assert [(f["run_id"], f["klass"]) for f in found] == [("r4", "audience.hook")] and "60%" in found[0]["detail"]


def test_too_few_videos_file_nothing(db, monkeypatch):
    monkeypatch.setattr(audience, "table", lambda db: [])
    assert audience.file_findings(db) == 0


def test_telegram_shows_the_audience_from_status_and_by_command(db):
    h = make_command_handler(db, lambda: None, lambda: "")
    text, buttons = h("canalytics", "")
    assert "Audience" in text and "Nothing measured yet" in text
    _, status_buttons = h("cstat", "")
    assert status_buttons[0][0]["callback_data"] == "canalytics:"
    assert json.dumps(h("analytics", "")[0]) == json.dumps(text)
