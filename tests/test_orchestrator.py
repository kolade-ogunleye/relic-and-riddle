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


def test_failed_fact_check_is_revised_then_fails_closed(db, monkeypatch, tmp_path, curated_payload):
    import main
    from src.visual_engine import VisualEngine
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord.visual_engine, "get_scene_sequence", lambda t: [tmp_path / "a.jpg"])
    monkeypatch.setattr(VisualEngine, "hero_image", staticmethod(lambda s: s[0]))
    notes_seen = []

    def gen(topic, revision_notes="", previous=None):
        notes_seen.append(revision_notes)
        return dict(curated_payload, origin="claude", topic_id=topic["id"])

    monkeypatch.setattr(coord.script_engine, "generate_script", gen)
    monkeypatch.setattr(coord.script_engine, "verify_claims",
                        lambda p, topic=None, allow_unreviewed_curated=False: (False, p, f"bad claim {len(notes_seen)}"))
    db.create_run("r9", "01_topic")
    with pytest.raises(RuntimeError, match="guardrail"):
        coord._produce_and_publish({"id": "01_topic"}, "r9", tmp_path, True, True, "queued")
    assert notes_seen == ["", "bad claim 1", "bad claim 2"]  # original + MAX_FACT_REVISIONS rewrites
