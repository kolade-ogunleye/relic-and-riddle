import pytest

from main import PipelineCoordinator


@pytest.fixture
def rendered_run(db, tmp_path, curated_payload):
    video = tmp_path / "m.mp4"
    video.write_bytes(b"0")
    cover = tmp_path / "c.jpg"
    cover.write_bytes(b"0")
    curated_payload["topic_id"] = "01_topic"
    curated_payload["human_reviewed"] = True
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=video,
                  thumbnail_cover_path=cover, thumbnail_grid_path=cover)
    return PipelineCoordinator(db)


@pytest.mark.parametrize("pub_status, run_status, topic_status, count", [
    ("published", "published", "published", 1),
    ("simulated", "simulated", "queued", 0),
    ("failed", "failed", "failed", 0),
    ("unknown", "publish_unknown", "publish_unknown", 0),
])
def test_only_confirmed_publishes_count(rendered_run, monkeypatch, pub_status, run_status, topic_status, count):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": pub_status})
    rendered_run.publish_run("r1")
    db = rendered_run.db
    assert db.get_run("r1")["status"] == run_status
    topic = db.get_topic_by_id("01_topic")
    assert (topic["status"], topic["times_published"]) == (topic_status, count)


def test_unknown_publish_topic_is_not_picked_again(rendered_run, monkeypatch):
    monkeypatch.setattr(rendered_run.publisher, "publish_reel", lambda env, dry_run=False: {"status": "unknown"})
    rendered_run.publish_run("r1")
    assert rendered_run.db.get_next_topic()["id"] != "01_topic"


def test_unreviewed_curated_run_cannot_be_published(rendered_run):
    run = rendered_run.db.get_run("r1")
    payload = dict(run["script_payload"], human_reviewed=False)
    rendered_run.db.update_run("r1", "pending_approval", script_payload=payload)
    with pytest.raises(ValueError, match="unreviewed"):
        rendered_run.publish_run("r1")
