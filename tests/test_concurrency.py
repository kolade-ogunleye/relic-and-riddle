"""
tests/test_concurrency.py
=========================
Parallel and de-duplicated stages must produce exactly what the sequential code did.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import encoding, parallax, privacy, video_qa
from src.video_composer import VideoComposer
from src.video_qa import QualityAuditor


def test_encoder_probe_runs_once_under_concurrent_first_use(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(encoding, "_DETECTED_ENCODER", None)
    calls = []

    def slow_probe(*args, **kwargs):
        calls.append(1)
        time.sleep(0.05)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(encoding.subprocess, "run", slow_probe)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: encoding.get_video_encoder(), range(8)))
    assert len(calls) == 1
    assert all(r == results[0] for r in results) and results[0][1] == "h264_nvenc"


def test_anonymize_all_checks_each_file_once_and_keeps_order(monkeypatch, tmp_path):
    seen, lock = [], threading.Lock()

    def fake(path, output_path=None):
        with lock:
            seen.append(Path(path))
        time.sleep(0.01)
        return Path(str(path) + ".safe")
    monkeypatch.setattr(privacy, "anonymize_media", fake)
    a, b, c = tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"
    out = privacy.anonymize_all([a, b, a, c, a])  # the hero repeats a scene
    assert out == [Path(str(p) + ".safe") for p in (a, b, a, c, a)]
    assert sorted(seen) == sorted([a, b, c])


def test_parallel_face_checks_share_detectors_safely():
    # The shared YuNet/cascade objects are only used under the detector lock.
    import inspect
    src = inspect.getsource(privacy.detect_human_faces)
    assert "_DETECTOR_LOCK" in src and src.index("_DETECTOR_LOCK") < src.index("yunet.detect")


def test_vision_post_retries_rate_limits_instead_of_reporting_no_faces(monkeypatch):
    import requests
    replies = iter([SimpleNamespace(status_code=429, headers={"Retry-After": "0"}),
                    SimpleNamespace(status_code=200, headers={})])
    monkeypatch.setattr(requests, "post", lambda *a, **k: next(replies))
    monkeypatch.setattr(privacy.time, "sleep", lambda s: None)
    assert privacy._post_vision("https://example.invalid", {}, timeout=1).status_code == 200


def test_certify_audits_the_master_once(monkeypatch, tmp_path):
    video = tmp_path / "master.mp4"
    video.write_bytes(b"x")
    calls = []

    def audit(self, *args, **kwargs):
        calls.append(1)
        return {"score": 100, "certified": True, "issues": [], "loudness": {}, "remediation_targets": []}
    monkeypatch.setattr(QualityAuditor, "audit_rendered_timeline", audit)
    result = QualityAuditor().certify(video_path=video, timeline=[], words=[], run_dir=tmp_path)
    assert len(calls) == 1 and result["certified"] is True


def test_extract_frames_parallel_keeps_order_and_first_error(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(stdout='{"format": {"duration": "60"}}')
        Path(cmd[-1]).write_bytes(b"jpg")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(video_qa.subprocess, "run", fake_run)
    frames = video_qa.extract_frames(video, [1.0, 2.0, 3.0, 4.0, 5.0], tmp_path / "f")
    assert [f.name for f in frames] == [f"qa_{i:02d}.jpg" for i in range(1, 6)]

    def failing_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(stdout='{"format": {"duration": "60"}}')
        if "qa_01.jpg" not in cmd[-1]:
            Path(cmd[-1]).write_bytes(b"jpg")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(video_qa.subprocess, "run", failing_run)
    with pytest.raises(ValueError, match="1.00s"):
        video_qa.extract_frames(video, [1.0, 2.0, 3.0], tmp_path / "g")


def test_calibrated_gain_reused_only_for_identical_audio(monkeypatch, tmp_path):
    vo, bed, bed2 = tmp_path / "vo.mp3", tmp_path / "bed.wav", tmp_path / "bed2.wav"
    vo.write_bytes(b"voice")
    bed.write_bytes(b"bed")
    bed2.write_bytes(b"other bed")
    composer = VideoComposer()
    runs = []
    monkeypatch.setattr(composer, "_calibrate_gain", lambda *a: runs.append(a) or 1.5)
    assert composer.calibrate_gain(vo, bed) == 1.5
    assert composer.calibrate_gain(vo, bed) == 1.5
    assert len(runs) == 1
    composer.calibrate_gain(vo, bed2)
    composer.calibrate_gain(vo, bed, target_lufs=-16.0)
    assert len(runs) == 3


def test_parallax_clips_reused_only_while_inputs_match(monkeypatch, tmp_path):
    from src.lineage import record_derived
    monkeypatch.setattr(parallax, "available", lambda: True)
    monkeypatch.setattr(parallax, "_session", lambda: None)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr(parallax, "MODEL_PATH", model)
    still = tmp_path / "still.jpg"
    still.write_bytes(b"still")
    renders = []

    def fake_render(image, out, duration, preset):
        renders.append((duration, preset))
        out.write_bytes(f"{duration}{preset}".encode())
        record_derived(out, [image], "parallax", {"duration": duration})
        return out
    monkeypatch.setattr(parallax, "render_clip", fake_render)
    work = tmp_path / "parallax"
    first = parallax.render_scenes([still, still], [3.0, 4.0], work, 0.4)
    again = parallax.render_scenes([still, still], [3.0, 4.0], work, 0.4)
    assert first == again and len(renders) == 2
    parallax.render_scenes([still, still], [3.0, 5.0], work, 0.4)  # a re-timed beat re-renders
    assert len(renders) == 3
    (work / "scene_01.mp4").write_bytes(b"tampered")
    parallax.render_scenes([still, still], [3.0, 5.0], work, 0.4)
    assert len(renders) == 4


def _resumable_run(db, monkeypatch, tmp_path, curated_payload):
    """A failed run whose script and voiceover are reusable (as in test_orchestrator)."""
    import json
    import main
    from main import PipelineCoordinator
    from src import config as test_config
    from src.checkpoints import Checkpoints, file_inputs, topic_inputs
    from src.fact_evidence import record_fact_review
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    run_dir = tmp_path / "r5"
    run_dir.mkdir()
    sheet = tmp_path / "data" / "fact_sheets" / "01_topic.json"
    sheet.parent.mkdir(parents=True)
    sheet.write_text('{"facts": []}', encoding="utf-8")
    monkeypatch.setattr(test_config, "DATA_DIR", tmp_path / "data")
    saved = dict(curated_payload, guardrail_passed=True)
    record_fact_review(saved, sheet)
    (run_dir / "verified_script.json").write_text(json.dumps(saved), encoding="utf-8")
    (run_dir / "voiceover.mp3").write_bytes(b"x")
    (run_dir / "words.json").write_text("[]", encoding="utf-8")
    (run_dir / "voice.json").write_text(json.dumps({"provider": "edge-tts", "licensed": False}))
    db.create_run("r5", "01_topic")
    db.update_run("r5", "failed", error="RenderError")
    coord = PipelineCoordinator(db)
    monkeypatch.setattr(coord.visual_engine, "get_scene_sequence", lambda t: [tmp_path / "a.jpg"])
    monkeypatch.setattr(type(coord.visual_engine), "hero_image", staticmethod(lambda s: s[0]))
    topic = db.get_topic_by_id("01_topic")
    checkpoints = Checkpoints(run_dir)
    checkpoints.complete("script", {"topic": topic_inputs(topic), "engine": test_config.SCRIPT_ENGINE,
                                    "fact_sheet": file_inputs([sheet])}, [run_dir / "verified_script.json"])
    checkpoints.complete("voice", coord.voice_inputs(curated_payload["narration_script"], dry_run=True),
        [run_dir / "voiceover.mp3", run_dir / "words.json", run_dir / "voice.json"])
    monkeypatch.setattr(coord.audio_engine, "get_duration", lambda p: 44.0)
    monkeypatch.setattr(coord.audio_engine, "generate_ambient_bed", lambda *a, **k: None)
    return coord


def test_background_image_prep_reaches_the_render(db, monkeypatch, tmp_path, curated_payload):
    coord = _resumable_run(db, monkeypatch, tmp_path, curated_payload)
    prepared_media = ([tmp_path / "safe.jpg"], tmp_path / "safe.jpg", 0)
    monkeypatch.setattr(coord, "_prepare_images", lambda scenes, hero, run_dir=None: prepared_media)
    seen = {}

    def render(*args):
        seen["prepared"] = args[-1].result()
        return {"status": "simulated"}
    monkeypatch.setattr(coord, "_render_approve_publish", render)
    assert coord.resume_run("r5", dry_run=True)["status"] == "simulated"
    assert seen["prepared"] == prepared_media


def test_failed_bed_waits_for_image_prep_before_reporting(db, monkeypatch, tmp_path, curated_payload):
    coord = _resumable_run(db, monkeypatch, tmp_path, curated_payload)
    events = []

    def slow_prep(scenes, hero, run_dir=None):
        time.sleep(0.2)
        events.append("prep done")
        return scenes, hero, 0

    def failing_bed(*args):
        raise RuntimeError("bed failed")
    monkeypatch.setattr(coord, "_prepare_images", slow_prep)
    monkeypatch.setattr(coord, "_produce_bed_and_render", failing_bed)
    with pytest.raises(RuntimeError, match="bed failed"):
        coord.resume_run("r5", dry_run=True)
    events.append("failure reported")
    # No image writer outlives the failed run (a new /run could otherwise share its caches).
    assert events == ["prep done", "failure reported"]
    assert db.get_run("r5")["status"] == "failed"


@pytest.mark.parametrize("status", [200, 400, 403])
def test_vision_post_is_unchanged_for_non_transient_replies(monkeypatch, status):
    # Only 429/5xx (which previously fell through to "no faces found") are retried;
    # every other reply is returned after exactly one call, as before.
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or SimpleNamespace(status_code=status, headers={}))
    monkeypatch.setattr(privacy.time, "sleep", lambda s: pytest.fail("no wait expected"))
    assert privacy._post_vision("https://example.invalid", {}, timeout=1).status_code == status
    assert len(calls) == 1


def test_vision_post_gives_up_after_bounded_retries(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or SimpleNamespace(status_code=503, headers={}))
    monkeypatch.setattr(privacy.time, "sleep", lambda s: None)
    assert privacy._post_vision("https://example.invalid", {}, timeout=1).status_code == 503  # same final reply as before
    assert len(calls) == 3
