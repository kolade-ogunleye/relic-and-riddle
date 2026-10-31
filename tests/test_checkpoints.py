import pytest

from src.checkpoints import Checkpoints


def test_checkpoint_requires_same_inputs_and_intact_outputs(tmp_path):
    checkpoints = Checkpoints(tmp_path)
    output = tmp_path / "voice.wav"
    output.write_bytes(b"audio")
    inputs = {"script": "Original", "voice": "A"}
    checkpoints.complete("voice", inputs, [output])
    assert checkpoints.reusable("voice", inputs)
    assert not checkpoints.reusable("voice", dict(inputs, script="Changed"))
    assert not checkpoints.reusable("voice", dict(inputs, voice="B"))
    output.write_bytes(b"tampered")
    assert not checkpoints.reusable("voice", inputs)


def test_corrupt_or_incomplete_checkpoint_never_reuses(tmp_path):
    checkpoints = Checkpoints(tmp_path)
    checkpoints.path.write_text("{broken")
    assert not checkpoints.reusable("voice", {})
    with pytest.raises(ValueError, match="incomplete"):
        checkpoints.complete("voice", {}, [tmp_path / "missing.wav"])


def test_packaging_cache_reuses_and_invalidates_copy_and_outputs(tmp_path):
    from src.checkpoints import cached_artifacts
    calls = []
    output = tmp_path / "cover.jpg"

    def produce():
        calls.append(1)
        output.write_bytes(b"current cover")
        return [output]

    assert cached_artifacts(tmp_path, "cover", {"title": "A"}, produce) == [output]
    cached_artifacts(tmp_path, "cover", {"title": "A"}, produce)
    assert len(calls) == 1
    output.write_bytes(b"changed")
    cached_artifacts(tmp_path, "cover", {"title": "A"}, produce)
    cached_artifacts(tmp_path, "cover", {"title": "B"}, produce)
    assert len(calls) == 3

def test_topic_priority_does_not_invalidate_script_checkpoint(tmp_path):
    from src.checkpoints import topic_inputs
    topic_v1 = {
        "id": "01_monolith",
        "title": "Kailasa Monolith",
        "hook": "Carved from top to bottom.",
        "status": "queued",
        "priority": 0,
        "times_published": 0,
        "updated_at": "2026-10-20T00:00:00"
    }
    topic_v2 = dict(topic_v1, priority=5, updated_at="2026-10-20T01:30:00")
    # Both versions should produce identical topic_inputs because priority is operational state
    assert topic_inputs(topic_v1) == topic_inputs(topic_v2)

    checkpoints = Checkpoints(tmp_path)
    script_out = tmp_path / "verified_script.json"
    script_out.write_text('{"title": "Kailasa"}', encoding="utf-8")

    inputs_v1 = {"topic": topic_inputs(topic_v1), "engine": "claude"}
    checkpoints.complete("script", inputs_v1, [script_out])

    # Reusing with topic_v2 (bumped priority) must succeed without invalidation
    inputs_v2 = {"topic": topic_inputs(topic_v2), "engine": "claude"}
    assert checkpoints.reusable("script", inputs_v2)
