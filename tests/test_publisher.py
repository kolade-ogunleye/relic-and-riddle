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


def _stub(name, exc=None, result=None):
    pub = MagicMock()
    pub.name = name
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
