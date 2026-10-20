import json
from pathlib import Path

import pytest

from src.credits import generate_provenance_ledger, resolve_asset_path
from src.video_qa import QualityAuditor, verify_provenance_integrity


def test_missing_video_cannot_be_certified(tmp_path):
    report = QualityAuditor().audit_rendered_timeline(tmp_path / "absent.mp4", [])
    assert report["certified"] is False
    assert report["loudness"] == {}
    assert any(i["type"] == "missing_video" for i in report["issues"])


def test_provenance_detects_tampering_and_missing_assets(tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"master")
    asset = tmp_path / "photo.jpg"
    asset.write_bytes(b"photo")
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, master, media_items=[asset])
    assert verify_provenance_integrity(ledger)
    data = json.loads(ledger.read_text())
    assert data["assets"][0]["license"] is None
    assert data["assets"][0]["rights_status"] == "unverified"
    assert not data["self_healing"]["certified"]
    asset.write_bytes(b"modified")
    assert not verify_provenance_integrity(ledger)
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, master,
                                        media_items=[tmp_path / "missing.jpg"])
    assert not verify_provenance_integrity(ledger)
    assert json.loads(ledger.read_text())["issues"]


def test_asset_resolution_preserves_directories_and_refuses_ambiguity(tmp_path):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "same.jpg").write_bytes(b"x")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_asset_path(Path("sub/same.jpg"), roots)
    with pytest.raises(ValueError, match="missing"):
        resolve_asset_path(Path("same.jpg"), roots)
    assert resolve_asset_path(roots[0] / "sub/same.jpg", roots) == (roots[0] / "sub/same.jpg").resolve()


def test_measurement_failure_is_not_success(tmp_path, monkeypatch):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"not a video")
    def fail(*args, **kwargs):
        raise ValueError("measurement failed")
    monkeypatch.setattr("src.video_qa.measure_program_loudness", fail)
    result = QualityAuditor().audit_rendered_timeline(video, [])
    assert not result["certified"]
    assert any(i["type"] == "measurement_failed" for i in result["issues"])


def test_empty_composition_manifest_is_not_verified(tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"video")
    ledger = generate_provenance_ledger(tmp_path, {"id": "test"}, {}, master)
    assert not verify_provenance_integrity(ledger)
    assert "coverage cannot be verified" in json.loads(ledger.read_text())["issues"][0]


def test_release_report_blocks_failed_facts_and_missing_provenance(tmp_path, monkeypatch):
    from src.release import require_release
    media = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920},
                         {"codec_type": "audio", "channels": 2, "sample_rate": "48000"}],
             "format": {"duration": "50"}}
    from types import SimpleNamespace
    monkeypatch.setattr("src.release.subprocess.run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(media)))
    monkeypatch.setattr("src.release.measure_program_loudness",
                        lambda p: {"integrated_lufs": -14, "true_peak_dbtp": -2})
    run = {"master_video_path": str(tmp_path / "master.mp4"),
           "script_payload": {"guardrail_passed": False}}
    with pytest.raises(ValueError, match="facts.*provenance"):
        require_release(run, dry_run=True)
    report = json.loads((tmp_path / "release_report.json").read_text())
    assert report["status"] == "fail"
    assert report["dry_run"] is True


def test_publish_cannot_bypass_release_gate(tmp_path, monkeypatch):
    from main import PipelineCoordinator
    from types import SimpleNamespace
    run = {"master_video_path": str(tmp_path / "missing.mp4"),
           "script_payload": {"origin": "deep_dive_engine", "guardrail_passed": False},
           "topic_id": "test"}
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda _: run, db_path=tmp_path / "test.db")
    def must_not_publish(*a, **k):
        pytest.fail("Publisher called before release passed")
    coordinator.publisher = SimpleNamespace(build_envelope=must_not_publish)
    with pytest.raises(ValueError, match="Release blocked"):
        coordinator.publish_run("test")


def test_real_ffmpeg_mix_preserves_stereo_bed(tmp_path):
    import shutil
    import subprocess
    import numpy as np
    import soundfile as sf
    from src.video_composer import _audio_chain
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the measured stereo integration check")
    sr = 48000
    t = np.arange(sr) / sr
    voice = np.zeros(sr, dtype=np.float32)
    bed = np.column_stack([0.1 * np.sin(2 * np.pi * 440 * t),
                           0.1 * np.sin(2 * np.pi * 880 * t)])
    vo_path, bed_path, output = (tmp_path / n for n in ("vo.wav", "bed.wav", "mix.wav"))
    sf.write(vo_path, voice, sr)
    sf.write(bed_path, bed, sr)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vo_path), "-i", str(bed_path),
                    "-filter_complex", _audio_chain(0, 1) + "[out]", "-map", "[out]",
                    str(output)], check=True, capture_output=True, timeout=30)
    mixed, measured_sr = sf.read(output)
    assert measured_sr == sr
    assert mixed.shape == (sr, 2)
    assert np.sqrt(np.mean((mixed[:, 0] - mixed[:, 1]) ** 2)) > 0.05
    assert abs(np.corrcoef(mixed.T)[0, 1]) < 0.1


def test_visual_certification_is_bound_to_render(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.release import write_release_report
    master = tmp_path / "master.mp4"
    master.write_bytes(b"replacement render")
    media = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920},
                         {"codec_type": "audio", "channels": 2, "sample_rate": "48000"}],
             "format": {"duration": "50"}}
    monkeypatch.setattr("src.release.subprocess.run", lambda *a, **k: SimpleNamespace(stdout=json.dumps(media)))
    monkeypatch.setattr("src.release.measure_program_loudness",
                        lambda p: {"integrated_lufs": -14, "true_peak_dbtp": -2})
    report = write_release_report({"master_video_path": str(master), "script_payload": {
        "guardrail_passed": True, "self_healing": {"certified": True, "master_sha256": "0" * 64}}})
    check = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert check["status"] == "fail"
