"""Audience: Meta's own Graph insights first (2026-10-28).

Production, 2026-10-28: every audience_metrics row came from "upload-post" although the Meta tokens are
configured, with no Graph log line at all, and every Story snapshot failed with HTTP 400 because the first
one was taken at 1 day, after the story's 24 hours. All HTTP here is mocked; no test touches a real API.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src import audience, config

IG_TOKEN = "IGTOKEN-abcdefghijklmnop-SECRET"
PAGE_TOKEN = "PAGETOKEN-qrstuvwxyz-SECRET"
UP_KEY = "UPKEY-0123456789-SECRET"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.ok = body, status, status < 400

    def json(self):
        return self._body


@pytest.fixture
def meta(monkeypatch):
    monkeypatch.setattr(config, "META_ACCESS_TOKEN", IG_TOKEN)
    monkeypatch.setattr(config, "META_IG_ACCOUNT_ID", "17840000000000001")
    monkeypatch.setattr(config, "META_PAGE_ACCESS_TOKEN", PAGE_TOKEN)
    monkeypatch.setattr(config, "META_PAGE_ID", "100000000000001")
    monkeypatch.setattr(config, "META_GRAPH_VERSION", "v26.0")
    monkeypatch.setattr(config, "UPLOAD_POST_API_KEY", UP_KEY)
    monkeypatch.setattr(config, "UPLOAD_POST_BASE_URL", "https://api.upload-post.com/api")


def route(monkeypatch, table):
    """requests.get answered by the first (url fragment, metric or None) -> body/callable in `table`."""
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        params = params or {}
        calls.append((url, dict(params)))
        for (fragment, metric), answer in table.items():
            if fragment in url and (metric is None or params.get("metric") == metric):
                answer = answer(url, params) if callable(answer) else answer
                return answer if isinstance(answer, Resp) else Resp(answer)
        return Resp({"error": {"message": f"unmocked {url}", "code": 100}}, 400)

    monkeypatch.setattr(audience.requests, "get", get)
    return calls


def publish(db, run_id, channel, response, posted):
    if not db.get_run(run_id):
        db.create_run(run_id, "01_topic")
    db.record_publication("01_topic", run_id, channel, "published", response)
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE run_id = ? AND channel = ? AND created_at = "
                     "(SELECT MAX(created_at) FROM publications WHERE run_id = ? AND channel = ?)",
                     (posted.isoformat(), run_id, channel, run_id, channel))


def rows(db):
    with db._conn() as conn:
        return {(r["platform"], r["snapshot"]): (r["source"], json.loads(r["metrics_json"]))
                for r in conn.execute("SELECT * FROM audience_metrics")}


IG_INSIGHTS = {"data": [{"name": "views", "values": [{"value": 121}]}, {"name": "reach", "values": [{"value": 90}]},
                        {"name": "ig_reels_avg_watch_time", "values": [{"value": 7900}]}]}
FB_INSIGHTS = {"data": [{"name": "fb_reels_total_plays", "values": [{"value": 339}]},
                        {"name": "post_video_avg_time_watched", "values": [{"value": 6100}]}]}
REEL_URLS = {"instagram": "https://www.instagram.com/reel/DQabc123XYZ/",
             "facebook": "https://www.facebook.com/reel/1419040693748996"}


def test_the_media_id_is_read_from_the_stored_upload_post_answer(db, meta, monkeypatch):
    res = {"status": "published", "post_urls": {"instagram": None, "facebook": REEL_URLS["facebook"]},
           "raw": {"status": "completed", "request_id": "run_k", "results": [
               {"platform": "instagram", "success": True, "platform_post_id": "18050000000000001"},
               {"platform": "facebook", "success": True, "post_url": REEL_URLS["facebook"]}]}}
    publish(db, "run_k", "upload-post", res, NOW - timedelta(days=1, hours=1))
    calls = route(monkeypatch, {("/18050000000000001/insights", None): IG_INSIGHTS,
                                ("/1419040693748996/video_insights", None): FB_INSIGHTS})
    assert audience.collect(db, now=NOW) == 2
    got = rows(db)
    assert got[("instagram", "1d")][0] == "instagram-graph" and got[("instagram", "1d")][1]["views"] == 121
    assert got[("facebook", "1d")][0] == "facebook-graph" and got[("facebook", "1d")][1]["fb_reels_total_plays"] == 339
    assert not any("/media" in u or "uploadposts" in u for u, _ in calls)  # no search, no fallback needed
    # Kept: the next snapshot does not look the ids up again.
    with db._conn() as conn:
        assert {r["platform"]: r["how"] for r in conn.execute("SELECT * FROM audience_media_ids")} == {
            "instagram": "stored publish response", "facebook": "post URL"}


def test_without_a_stored_id_the_history_then_the_accounts_media_are_matched(db, meta, monkeypatch):
    posted = NOW - timedelta(days=1, hours=2)
    publish(db, "run_g", "upload-post", {"post_urls": REEL_URLS}, posted)
    media = {"data": [{"id": "1", "permalink": "https://www.instagram.com/reel/OTHER/", "media_product_type": "REELS",
                       "timestamp": (posted - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+0000")},
                      {"id": "18060000000000002", "permalink": "https://www.instagram.com/reel/DQabc123XYZ/",
                       "media_product_type": "REELS", "timestamp": posted.strftime("%Y-%m-%dT%H:%M:%S+0000")}]}
    route(monkeypatch, {("/uploadposts/history", None): {"history": [], "total": 0},
                        ("/17840000000000001/media", None): media,
                        ("/18060000000000002/insights", None): IG_INSIGHTS,
                        ("/1419040693748996/video_insights", None): FB_INSIGHTS})
    assert audience.collect(db, now=NOW) == 2
    assert rows(db)[("instagram", "1d")][0] == "instagram-graph"


def test_a_profile_style_url_is_matched_by_publication_time(db, meta, monkeypatch):
    """The old lookup took the URL's last segment as a shortcode: a profile URL matched nothing, silently."""
    posted = NOW - timedelta(days=1, hours=2)
    publish(db, "run_t", "upload-post", {"post_urls": {"instagram": "https://www.instagram.com/relic_and_riddle/"}}, posted)
    media = {"data": [{"id": "far", "media_product_type": "REELS", "permalink": "https://www.instagram.com/reel/A/",
                       "timestamp": (posted - timedelta(hours=5)).isoformat()},
                      {"id": "feed", "media_product_type": "FEED", "permalink": "https://www.instagram.com/p/B/",
                       "timestamp": posted.isoformat()},
                      {"id": "near", "media_product_type": "REELS", "permalink": "https://www.instagram.com/reel/C/",
                       "timestamp": (posted - timedelta(minutes=4)).isoformat()}]}
    route(monkeypatch, {("/uploadposts/history", None): {"history": []},
                        ("/17840000000000001/media", None): media,
                        ("/near/insights", None): IG_INSIGHTS,
                        ("/uploadposts/post-analytics/", None): {"platforms": {}}})
    audience.collect(db, now=NOW)
    assert rows(db)[("instagram", "1d")][1]["media_id"] == "near"


def test_the_upload_post_history_gives_the_platform_post_id(db, meta, monkeypatch):
    publish(db, "run_h", "upload-post", {"post_urls": {"instagram": None}, "raw": {"request_id": "run_h"}},
            NOW - timedelta(days=1, hours=1))
    route(monkeypatch, {("/uploadposts/history", None): lambda u, p: {"history": [
                            {"platform": p["platform"], "success": True, "request_id": p["request_id"],
                             "platform_post_id": "18070000000000003" if p["platform"] == "instagram" else None}]},
                        ("/18070000000000003/insights", None): IG_INSIGHTS,
                        ("/100000000000001/video_reels", None): {"data": []},
                        ("/uploadposts/post-analytics/", None): {"platforms": {}}})
    audience.collect(db, now=NOW)
    got = rows(db)
    assert got[("instagram", "1d")][0] == "instagram-graph"
    assert got[("facebook", "1d")][1].get("unavailable")  # nothing anywhere: said, with both sources' reasons
    assert "Facebook Graph" in got[("facebook", "1d")][1]["unavailable"]


def test_graph_first_then_upload_post_with_the_apis_own_message_logged(db, meta, monkeypatch, caplog):
    res = {"post_urls": REEL_URLS, "raw": {"results": [{"platform": "instagram", "success": True, "platform_post_id": "999"}]}}
    publish(db, "run_f", "upload-post", res, NOW - timedelta(days=1, hours=1))
    expired = Resp({"error": {"message": "Error validating access token: Session has expired", "code": 190}}, 400)
    route(monkeypatch, {("/999/insights", None): expired,
                        ("/1419040693748996/video_insights", None): FB_INSIGHTS,
                        ("/uploadposts/post-analytics/run_f", None): {"platforms": {
                            "instagram": {"success": True, "post_metrics": {"views": 121, "likes": 9}}}}})
    with caplog.at_level(logging.INFO, logger="src.audience"):
        assert audience.collect(db, now=NOW) == 2
    got = rows(db)
    assert got[("instagram", "1d")] == ("upload-post", {"source": "upload-post", "views": 121, "likes": 9})
    assert got[("facebook", "1d")][0] == "facebook-graph"
    text = caplog.text
    assert "Instagram Graph failed: HTTP 400: Error validating access token: Session has expired (code 190)" in text
    assert "Upload-Post answered" in text and "Facebook Graph answered" in text
    assert IG_TOKEN not in text and PAGE_TOKEN not in text


def test_a_story_is_measured_before_it_expires_and_said_expired_after(db, meta, monkeypatch):
    story = lambda part: {"request_id": f"run_s-story-{part}", "part": part, "platforms": ["instagram", "facebook"],
                          "post_urls": {"instagram": "https://www.instagram.com/stories/relic_and_riddle/"}}
    posted = NOW - timedelta(hours=19)
    publish(db, "run_s", "upload-post:story", story(1), posted)
    publish(db, "run_s", "upload-post:story", story(2), posted + timedelta(minutes=2))

    # Not due before STORY_SNAPSHOT_HOURS; due after, as the "story" snapshot (never 1d).
    assert audience.due_snapshots(db, now=posted + timedelta(hours=10)) == []
    due = audience.due_snapshots(db, now=NOW)
    assert {d["snapshot"] for d in due} == {"story"} and len(due) == 2

    stories = {"data": [{"id": "s1", "timestamp": (posted - timedelta(seconds=40)).isoformat()},
                        {"id": "s2", "timestamp": (posted + timedelta(minutes=2, seconds=-30)).isoformat()}]}
    story_insights = lambda u, p: (
        {"data": [{"name": "views", "values": [{"value": 55 if "/s1/" in u else 41}]},
                  {"name": "reach", "values": [{"value": 50}]}]}
        if set(p["metric"].split(",")) == set(audience.IG_STORY_METRICS) else Resp({"error": {"message": "x"}}, 400))
    calls = route(monkeypatch, {("/uploadposts/history", None): {"history": []},
                                ("/17840000000000001/stories", None): stories,
                                ("/insights", None): story_insights,
                                ("/uploadposts/post-analytics/", None): lambda u, p: {"platforms": {
                                    "facebook": {"success": True, "post_metrics": {"views": 12}}}}})
    assert audience.collect(db, now=NOW) == 4
    got = rows(db)
    assert got[("instagram_story1", "story")][1]["views"] == 55 and got[("instagram_story1", "story")][1]["media_id"] == "s1"
    assert got[("instagram_story2", "story")][1]["media_id"] == "s2"  # each frame its own story
    assert got[("facebook_story1", "story")][0] == "upload-post"  # Meta documents no Page story insights
    assert not any("/media" in u for u, _ in calls)
    assert audience.due_snapshots(db, now=NOW + timedelta(days=30)) == []  # one snapshot per story, done


def test_a_story_collected_after_24_hours_is_recorded_expired_without_any_call(db, meta, monkeypatch):
    publish(db, "run_x", "upload-post:story", {"request_id": "run_x-story-1", "part": 1, "platforms": ["instagram"]},
            NOW - timedelta(hours=30))
    calls = route(monkeypatch, {})
    assert audience.collect(db, now=NOW) == 1
    source, data = rows(db)[("instagram_story1", "story")]
    assert source == "expired" and "expired" in data["unavailable"] and "HTTP 400" not in data["unavailable"]
    assert calls == []


def test_the_sources_health_says_who_answered_for_each_platform(db, meta, monkeypatch):
    res = {"post_urls": REEL_URLS, "raw": {"results": [{"platform": "instagram", "success": True, "platform_post_id": "999"}]}}
    publish(db, "run_f", "upload-post", res, NOW - timedelta(days=1, hours=1))
    route(monkeypatch, {("/999/insights", None): Resp({"error": {"message": "Unsupported get request", "code": 100}}, 400),
                        ("/1419040693748996/video_insights", None): FB_INSIGHTS,
                        ("/uploadposts/post-analytics/run_f", None): {"platforms": {
                            "instagram": {"success": True, "post_metrics": {"views": 121}}}}})
    assert audience.sources_health(db)["collected_at"] is None
    audience.collect(db, now=NOW)
    health = audience.sources_health(db)
    assert health["collected_at"] == NOW.isoformat()
    ig, fb = health["platforms"]["instagram"], health["platforms"]["facebook"]
    assert ig["instagram-graph"]["failed"] == 1 and "Unsupported get request" in ig["instagram-graph"]["last_error"]
    assert ig["upload-post"]["ok"] == 1 and fb["facebook-graph"]["ok"] == 1 and "upload-post" not in fb
    assert any(line.startswith("Instagram: instagram-graph: 1 failed") for line in health["lines"])


def test_no_token_ever_reaches_a_log_or_the_database(db, meta, monkeypatch, caplog):
    res = {"post_urls": REEL_URLS, "raw": {"results": [{"platform": "instagram", "success": True, "platform_post_id": "999"}]}}
    publish(db, "run_z", "upload-post", res, NOW - timedelta(days=1, hours=1))

    def get(url, params=None, headers=None, timeout=None):
        if "upload-post" in url:  # requests puts the whole URL in its error: the API key's header too, here
            raise requests.ConnectionError(f"Max retries exceeded with url: {url} (Authorization: Apikey {UP_KEY})")
        query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        if "video_insights" in url:  # an API message that echoes the token back
            return Resp({"error": {"message": f"Invalid OAuth access token - {PAGE_TOKEN}", "code": 190}}, 400)
        raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}?{query}")

    monkeypatch.setattr(audience.requests, "get", get)
    with caplog.at_level(logging.DEBUG, logger="src.audience"):
        assert audience.collect(db, now=NOW) == 0  # the fallback's network failure: retried next pass
    with db._conn() as conn:
        stored = json.dumps([dict(r) for r in conn.execute("SELECT * FROM audience_sources")])
    for secret in (IG_TOKEN, PAGE_TOKEN, UP_KEY):
        assert secret not in caplog.text and secret not in stored
    assert "access_token=***" in caplog.text
    assert audience.due_snapshots(db, now=NOW)  # still due: nothing stored as measured
