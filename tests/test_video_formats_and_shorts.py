"""VIDEO_FORMATS decides which formats run; an approved Reel can also go to YouTube as a Short."""
import pytest

import main
from src import config, publisher
from src.control import make_command_handler
from src.publisher import MetaPublisher, PublishFailed, UploadPostPublisher


@pytest.mark.parametrize("formats, requested, expected", [
    (["reel"], "reel", "reel"),
    (["reel"], "dual", "reel"),            # the daily run asks for both; only the Reel is on
    (["reel"], "deep-dive", None),
    (["reel", "documentary"], "dual", "dual"),
    (["documentary"], "dual", "deep-dive"),
    (["documentary"], "reel", None),
])
def test_requested_formats_are_narrowed_to_video_formats(monkeypatch, formats, requested, expected):
    monkeypatch.setattr(config, "VIDEO_FORMATS", formats)
    assert config.effective_format(requested) == expected


def test_a_paused_documentary_spends_nothing(monkeypatch):
    monkeypatch.setattr(config, "VIDEO_FORMATS", ["reel"])
    coordinator = object.__new__(main.PipelineCoordinator)  # no health checker: reaching it would raise
    result = coordinator.run_cycle(content_format="deep-dive")
    assert result["status"] == "paused" and "VIDEO_FORMATS" in result["reason"]
    assert coordinator.produce_deep_dive({"id": "t"})["status"] == "paused"  # any other caller


def test_a_paused_documentary_has_no_button_and_an_old_button_starts_nothing(db, monkeypatch):
    monkeypatch.setattr(config, "VIDEO_FORMATS", ["reel"])
    monkeypatch.setattr(db, "get_next_topic", lambda: {"id": "01_topic", "title": "T", "status": "queued"})
    started = []
    h = make_command_handler(db, lambda: None, lambda content_format="reel": started.append(content_format) or "ok")
    text, rows = h("qdoc", "01_topic")
    assert "paused" in text and started == []
    assert not any(b.get("callback_data", "").startswith("qdoc:") for row in rows for b in row)


def _envelope(tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")
    return {"run_id": "run_t_1", "topic_id": "t", "platforms": ["facebook", "instagram"],
            "media": {"video_path": str(video), "thumbnail_path": str(video)},
            "metadata": {"title": "Greek Fire", "caption": "A caption", "hashtags": ["#history"],
                         "first_comment": "Which source do you trust?", "is_ai_generated": True}}


def test_a_short_uses_its_own_idempotency_key_and_no_thumbnail(tmp_path, monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        def json(self):
            return {"success": True, "results": {"youtube": {"success": True, "url": "https://youtube.com/shorts/x"}}}

    def post(url, headers=None, data=None, files=None, timeout=None):
        sent.update(headers=headers, data=dict(data), files=files)
        return Response()

    monkeypatch.setattr(publisher.requests, "post", post)
    up = UploadPostPublisher(api_key="k", user="u")
    res = up.publish_short(_envelope(tmp_path))
    assert res["status"] == "published"
    assert sent["headers"]["Idempotency-Key"] == "run_t_1:youtube-short"  # never the Reel's key
    assert sent["data"]["platform[]"] == "youtube" and set(sent["files"]) == {"video"}
    assert sent["data"]["youtube_first_comment"] == "Which source do you trust?"


def test_a_failed_short_is_recorded_and_never_raises(db, tmp_path):
    class Failing:
        def publish_short(self, envelope):
            raise PublishFailed("YouTube is not connected")

    meta = MetaPublisher(db=db, publishers=[Failing()])
    assert meta.publish_short(_envelope(tmp_path))["status"] == "failed"
    assert meta.publish_short(_envelope(tmp_path), dry_run=True)["status"] == "simulated"
    assert [p["status"] for p in db.list_publications("run_t_1")] == ["failed", "simulated"]
    assert {p["channel"] for p in db.list_publications("run_t_1")} == {"youtube-shorts"}


def test_a_short_carries_its_own_captions_when_the_run_has_them(tmp_path, monkeypatch):
    # Operator, 2026-10-27: Facebook's captions worked; YouTube takes our file too (youtube_subtitle_file)
    sent = {}

    class Response:
        status_code = 200
        def json(self):
            return {"success": True, "results": {"youtube": {"success": True, "url": "https://youtube.com/shorts/x"}}}

    def post(url, headers=None, data=None, files=None, timeout=None):
        sent.update(data=dict(data), files={k: (v[0], v[2]) for k, v in files.items()})
        return Response()

    monkeypatch.setattr(publisher.requests, "post", post)
    envelope = _envelope(tmp_path)
    (tmp_path / "reel.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nGreek fire\n", encoding="utf-8")
    UploadPostPublisher(api_key="k", user="u").publish_short(envelope)
    assert sent["files"]["youtube_subtitle_file"] == ("reel.srt", "text/plain")
    assert sent["data"]["youtube_subtitle_language"] == "en"
