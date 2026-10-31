import pytest
from pathlib import Path
from src.video_composer import VideoComposer


def test_splice_single_beat_command_structure(monkeypatch, tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"dummy_mp4_bytes")
    asset = tmp_path / "replacement.jpg"
    asset.write_bytes(b"dummy_jpg_bytes")
    output = tmp_path / "spliced.mp4"

    executed_cmds = []

    def mock_run_ffmpeg(cmd):
        executed_cmds.append(cmd)

    monkeypatch.setattr("src.video_composer._run_ffmpeg", mock_run_ffmpeg)

    durations = [3.0, 4.0, 5.0]

    # Test splicing middle beat (beat 1, duration 4.0, start 3.0, end 7.0)
    VideoComposer.splice_single_beat(master, 1, asset, durations, output)

    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert str(output) in cmd

    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=end=3.000" in filter_complex
    assert "trim=start=7.000" in filter_complex
    assert "concat=n=3:v=1:a=0" in filter_complex


def test_splice_first_beat(monkeypatch, tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"dummy_mp4_bytes")
    asset = tmp_path / "replacement.jpg"
    asset.write_bytes(b"dummy_jpg_bytes")
    output = tmp_path / "spliced_first.mp4"

    executed_cmds = []
    monkeypatch.setattr("src.video_composer._run_ffmpeg", lambda cmd: executed_cmds.append(cmd))

    durations = [3.0, 4.0, 5.0]

    # Test splicing first beat (beat 0)
    VideoComposer.splice_single_beat(master, 0, asset, durations, output)

    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=3.000" in filter_complex
    assert "concat=n=2:v=1:a=0" in filter_complex


def test_splice_last_beat(monkeypatch, tmp_path):
    master = tmp_path / "master.mp4"
    master.write_bytes(b"dummy_mp4_bytes")
    asset = tmp_path / "replacement.jpg"
    asset.write_bytes(b"dummy_jpg_bytes")
    output = tmp_path / "spliced_last.mp4"

    executed_cmds = []
    monkeypatch.setattr("src.video_composer._run_ffmpeg", lambda cmd: executed_cmds.append(cmd))

    durations = [3.0, 4.0, 5.0]

    # Test splicing last beat (beat 2)
    VideoComposer.splice_single_beat(master, 2, asset, durations, output)

    assert len(executed_cmds) == 1
    cmd = executed_cmds[0]
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=end=7.000" in filter_complex
    assert "concat=n=2:v=1:a=0" in filter_complex
