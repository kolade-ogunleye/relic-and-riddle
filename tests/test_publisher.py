from unittest.mock import MagicMock

import pytest
import requests

from src.publisher import (
    MetaPublisher, NotConfigured, PublishFailed, PublishUnknown, UploadPostPublisher,
)


@pytest.fixture
def envelope(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    cover = tmp_path / "c.jpg"
    cover.write_bytes(b"0")
    return {
        "run_id": "r1", "topic_id": "01_topic", "platforms": ["facebook", "instagram"],
        "media": {"video_path": str(video), "thumbnail_path": str(cover), "thumbnail_1x1_path": str(cover)},
        "metadata": {"title": "T", "caption": "Cap", "first_comment": "Q?", "is_ai_generated": True},
    }


def _stub(name, exc=None, result=None, platforms=("facebook", "instagram")):
    pub = MagicMock()
    pub.name = name
    pub.platforms = platforms
    if exc:
        pub.publish.side_effect = exc
    else:
        pub.publish.return_value = result or {"status": "published"}
    return pub


@pytest.fixture
def run(db):
    db.create_run("r1", "01_topic")
    return db


def test_dry_run_is_simulated_not_published(run, envelope):
    first = _stub("a")
    res = MetaPublisher(run, [first]).publish_reel(envelope, dry_run=True)
    assert res["status"] == "simulated"
    first.publish.assert_not_called()
    assert [p["status"] for p in run.list_publications("r1")] == ["simulated"]


def test_no_configured_publisher_is_a_failure(run, envelope):
    res = MetaPublisher(run, [_stub("a", NotConfigured("x")), _stub("b", NotConfigured("y"))]).publish_reel(envelope)
    assert res["status"] == "failed"


def test_definite_failure_fails_over(run, envelope):
    second = _stub("b")
    res = MetaPublisher(run, [_stub("a", PublishFailed("400")), second]).publish_reel(envelope)
    assert res["status"] == "published" and res["channel"] == "b"


def test_unknown_outcome_stops_failover(run, envelope):
    second = _stub("b")
    res = MetaPublisher(run, [_stub("a", PublishUnknown("timeout")), second]).publish_reel(envelope)
    assert res["status"] == "unknown"
    second.publish.assert_not_called()


def _response(status, body):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.json.return_value = body
    return r


def test_upload_post_request_matches_documented_api(monkeypatch, envelope):
    captured = {}

    def fake_post(url, headers, data, files, timeout):
        captured.update(url=url, headers=headers, data=data, files=files)
        return _response(200, {"success": True, "results": {
            "instagram": {"success": True, "url": "https://ig/p/1"},
            "facebook": {"success": True, "url": "https://fb/r/1"},
        }})

    monkeypatch.setattr(requests, "post", fake_post)
    res = UploadPostPublisher(api_key="k" * 20, user="profile", base_url="https://api.upload-post.com/api").publish(envelope)

    assert res["status"] == "published"
    assert captured["url"] == "https://api.upload-post.com/api/upload"
    assert captured["headers"]["Authorization"] == "Apikey " + "k" * 20
    assert captured["headers"]["Idempotency-Key"] == "r1"
    assert ("user", "profile") in captured["data"]
    assert [v for k, v in captured["data"] if k == "platform[]"] == ["facebook", "instagram"]
    assert {"video", "cover_image"} <= set(captured["files"])


def test_upload_post_timeout_retries_with_same_key_then_is_unknown(monkeypatch, envelope):
    calls = []

    def fake_post(url, headers, **kw):
        calls.append(headers["Idempotency-Key"])
        raise requests.Timeout("read timeout")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(PublishUnknown):
        UploadPostPublisher(api_key="k" * 20, user="u").publish(envelope)
    assert calls == ["r1", "r1"]


def test_upload_post_client_error_is_definite_failure(monkeypatch, envelope):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _response(401, {"success": False, "message": "bad key"}))
    with pytest.raises(PublishFailed):
        UploadPostPublisher(api_key="k" * 20, user="u").publish(envelope)


def test_partial_publish_is_unknown():
    with pytest.raises(PublishUnknown):
        UploadPostPublisher._summarize([{"platform": "instagram", "success": True},
                                        {"platform": "facebook", "success": False}], {})


def test_skipped_platforms_do_not_block_success():
    res = UploadPostPublisher._summarize([{"platform": "instagram", "success": True, "post_url": "u"},
                                          {"platform": "facebook", "success": True, "skipped": True}], {})
    assert res["status"] == "published" and res["skipped"] == ["facebook"]


def test_direct_fallbacks_cover_each_platform_once(run, envelope):
    ig = _stub("instagram-graph", result={"status": "published", "post_ids": {"instagram": "1"}}, platforms=("instagram",))
    fb = _stub("facebook-page", result={"status": "published", "post_ids": {"facebook": "2"}}, platforms=("facebook",))
    res = MetaPublisher(run, [_stub("upload-post", PublishFailed("401")), ig, fb]).publish_reel(envelope)
    assert res["status"] == "published" and res["post_ids"] == {"instagram": "1", "facebook": "2"}
    assert ig.publish.call_args[0][0]["platforms"] == ["instagram"]
    assert fb.publish.call_args[0][0]["platforms"] == ["facebook"]
    assert "missing_platforms" not in res


def test_primary_success_skips_direct_fallbacks(run, envelope):
    ig = _stub("instagram-graph", platforms=("instagram",))
    res = MetaPublisher(run, [_stub("upload-post"), ig]).publish_reel(envelope)
    assert res["status"] == "published"
    ig.publish.assert_not_called()


def test_partial_platform_coverage_is_reported(run, envelope):
    ig = _stub("instagram-graph", result={"status": "published"}, platforms=("instagram",))
    fb = _stub("facebook-page", NotConfigured("no page token"), platforms=("facebook",))
    res = MetaPublisher(run, [_stub("upload-post", NotConfigured("x")), ig, fb]).publish_reel(envelope)
    assert res["status"] == "published" and res["missing_platforms"] == ["facebook"]


def test_unknown_on_second_platform_stops_and_reports_what_posted(run, envelope):
    ig = _stub("instagram-graph", result={"status": "published"}, platforms=("instagram",))
    fb = _stub("facebook-page", PublishUnknown("timeout"), platforms=("facebook",))
    res = MetaPublisher(run, [_stub("upload-post", PublishFailed("x")), ig, fb]).publish_reel(envelope)
    assert res["status"] == "unknown" and len(res["published"]) == 1


def _fb_api(calls, finish_exc=None):
    def request(method, url, timeout=60, **kw):
        calls.append((method, url.split("?")[0], kw.get("data") if isinstance(kw.get("data"), dict) else "<file>"))
        if "video_reels" in url and kw.get("data", {}).get("upload_phase") == "start":
            body = {"video_id": "v1", "upload_url": "https://rupload.facebook.com/video-upload/v26.0/v1"}
        elif "video_reels" in url and kw.get("data", {}).get("upload_phase") == "finish":
            if finish_exc:
                raise finish_exc
            body = {"success": True}
        elif "rupload" in url:
            body = {"success": True}
        else:
            body = {"status": {"video_status": "ready"}}
        r = MagicMock(ok=True, status_code=200)
        r.json.return_value = body
        return r
    return request


def test_facebook_page_reels_flow(monkeypatch, envelope):
    from src.publisher import FacebookPageReelsPublisher
    calls = []
    monkeypatch.setattr(requests, "request", _fb_api(calls))
    pub = FacebookPageReelsPublisher(token="t" * 20, page_id="123", version="v26.0", poll_interval=0)
    res = pub.publish(envelope)
    assert res["post_ids"] == {"facebook": "v1"}
    phases = [c[2].get("upload_phase") for c in calls if isinstance(c[2], dict) and "upload_phase" in c[2]]
    assert phases == ["start", "finish"]
    finish = [c for c in calls if isinstance(c[2], dict) and c[2].get("upload_phase") == "finish"][0]
    assert finish[2]["video_state"] == "PUBLISHED" and finish[2]["description"] == "Cap"


def test_facebook_finish_timeout_is_unknown(monkeypatch, envelope):
    from src.publisher import FacebookPageReelsPublisher
    monkeypatch.setattr(requests, "request", _fb_api([], finish_exc=requests.Timeout("slow")))
    pub = FacebookPageReelsPublisher(token="t" * 20, page_id="123", version="v26.0", poll_interval=0)
    with pytest.raises(PublishUnknown):
        pub.publish(envelope)


def _photos(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f"s{i}.jpg"
        p.write_bytes(b"0")
        paths.append(str(p))
    return paths


def test_story_frames_post_one_by_one_as_stories(monkeypatch, run, tmp_path):
    calls = []

    def fake_post(url, headers, data, files, timeout):
        calls.append((url, headers["Idempotency-Key"], dict(data), len(files)))
        return _response(200, {"success": True, "results": {"instagram": {"success": True, "url": "u"}}})

    monkeypatch.setattr(requests, "post", fake_post)
    up = UploadPostPublisher(api_key="k" * 20, user="u", base_url="https://api.upload-post.com/api")
    res = MetaPublisher(db=run, publishers=[up]).publish_photos("r1", "01_topic", "story", _photos(tmp_path, 3))
    assert res["status"] == "published" and res["posted_parts"] == 3
    assert [c[1] for c in calls] == ["r1-story-1", "r1-story-2", "r1-story-3"]
    url, _, data, nfiles = calls[0]
    assert url == "https://api.upload-post.com/api/upload_photos" and nfiles == 1
    assert data["media_type"] == "STORIES" and data["facebook_media_type"] == "STORIES"


def test_carousel_is_one_post_with_caption_and_max_ten(monkeypatch, run, tmp_path):
    calls = []
    monkeypatch.setattr(requests, "post", lambda url, headers, data, files, timeout: calls.append((dict(data), len(files)))
                        or _response(200, {"success": True, "results": {"instagram": {"success": True, "url": "u"}}}))
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    res = MetaPublisher(db=run, publishers=[up]).publish_photos("r1", "01_topic", "carousel", _photos(tmp_path, 12), caption="Cap")
    assert res["status"] == "published" and len(calls) == 1
    data, nfiles = calls[0]
    assert nfiles == 10 and data["title"] == "Cap" and "media_type" not in data


def test_story_failure_after_first_frame_is_unknown(monkeypatch, run, tmp_path):
    responses = iter([_response(200, {"success": True, "results": {"instagram": {"success": True}}}),
                      _response(400, {"success": False, "message": "bad"})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(responses))
    up = UploadPostPublisher(api_key="k" * 20, user="u")
    res = MetaPublisher(db=run, publishers=[up]).publish_photos("r1", "01_topic", "story", _photos(tmp_path, 3))
    assert res["status"] == "unknown" and res["posted_parts"] == 1


def test_photos_need_upload_post(run, tmp_path):
    res = MetaPublisher(db=run, publishers=[]).publish_photos("r1", "01_topic", "story", _photos(tmp_path, 1))
    assert res["status"] == "failed" and "Upload-Post" in res["error"]


def test_reel_fields_map_to_each_platform(monkeypatch, envelope):
    captured = {}

    def fake_post(url, headers, data, files, timeout):
        captured.update(data=dict(data))
        return _response(200, {"success": True, "results": {"instagram": {"success": True}, "facebook": {"success": True}}})
    monkeypatch.setattr(requests, "post", fake_post)
    envelope["metadata"]["caption"] = "C" * 2000
    UploadPostPublisher(api_key="k" * 20, user="u").publish(envelope)
    d = captured["data"]
    assert d["instagram_title"] == "C" * 2000 and d["facebook_description"] == "C" * 2000
    assert d["facebook_title"] == "T" and len(d["title"]) <= 255  # not the 2,000-char caption
    assert d["is_ai_generated"] == "true" and d["facebook_is_ai_generated"] == "true"


def test_unlicensed_voice_blocks_live_publication(monkeypatch, run, envelope, tmp_path):
    from src import config
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    import json
    (run_dir / "voice.json").write_text(json.dumps({"provider": "edge-tts", "licensed": False}), encoding="utf-8")
    pub = _stub("a")
    res = MetaPublisher(run, [pub]).publish_reel(envelope, dry_run=False)
    assert res["status"] == "blocked_unlicensed"
    assert "Commercial Voice License Required" in res["error"]
    pub.publish.assert_not_called()


def test_licensed_voice_allows_live_publication(monkeypatch, run, envelope, tmp_path):
    from src import config
    monkeypatch.setattr(config, "VOICE_REQUIRE_LICENSED", True)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    import json
    (run_dir / "voice.json").write_text(json.dumps({"provider": "azure", "licensed": True}), encoding="utf-8")
    pub = _stub("a")
    res = MetaPublisher(run, [pub]).publish_reel(envelope, dry_run=False)
    assert res["status"] == "published"
    pub.publish.assert_called_once()

