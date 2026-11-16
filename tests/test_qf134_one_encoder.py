"""QF-134: delivered and judged video is encoded with the production encoder on every machine.

Production has no NVIDIA adapter and encodes with libx264 (preset slow, CRF 18; the loop bridge's body intermediate
veryfast, CRF 10). Development has an NVENC GPU; while a hardware probe chose the encoder, it delivered and reviewed
NVENC video production never makes. Every test here simulates a machine whose GPU answers the NVENC probe (the probe
is mocked: CI has no GPU) and checks that what is delivered or judged does not change with it."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PIL import Image

from src import config, encoding, graphics
from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]
PRODUCTION_INTERMEDIATE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10"]
# QF-139: each delivery encoder also carries x264's fixed thread count (the machine's cores never reach it)
DELIVERY = [*PRODUCTION, "-threads", "6"]
DELIVERY_INTERMEDIATE = [*PRODUCTION_INTERMEDIATE, "-threads", "6"]
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _machine(monkeypatch, nvenc: bool):
    """A machine whose NVENC probe succeeds (`nvenc`) or fails; every other ffmpeg call is left alone."""
    monkeypatch.setattr(encoding, "_DETECTED_ENCODER", None)
    real = subprocess.run

    def run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "h264_nvenc" in cmd and "null" in cmd:
            return SimpleNamespace(returncode=0 if nvenc else 1)
        return real(cmd, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)


class _Encode:
    """Stands in for the ffmpeg process a graphic clip is piped into; records its command."""
    commands: ClassVar[list] = []

    def __init__(self, cmd, *args, **kwargs):
        _Encode.commands.append(list(cmd))
        self.stdin = SimpleNamespace(write=lambda data: None, close=lambda: None)
        self.stderr = SimpleNamespace(read=lambda: b"")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


def _graphic_command(monkeypatch, tmp_path, nvenc: bool) -> list:
    _machine(monkeypatch, nvenc)
    _Encode.commands = []
    monkeypatch.setattr(graphics.subprocess, "Popen", _Encode)
    graphics._encode((Image.new("RGB", (256, 256)) for _ in range(2)), tmp_path / "clip.mp4", 2, 256, 256)
    (command,) = _Encode.commands
    return command


def _encoder_of(command: list) -> list:
    at = command.index("-c:v")
    return command[at:at + 6]


def test_graphic_clips_take_the_production_encoder_where_nvenc_answers(monkeypatch, tmp_path):
    """Graphic, design-card and callout clips (graphics._encode) are cut into the delivered master."""
    with_gpu = _graphic_command(monkeypatch, tmp_path, nvenc=True)
    assert _encoder_of(with_gpu) == PRODUCTION, with_gpu


def test_graphic_clip_command_is_the_same_on_both_machines(monkeypatch, tmp_path):
    with_gpu = _graphic_command(monkeypatch, tmp_path, nvenc=True)
    without = _graphic_command(monkeypatch, tmp_path, nvenc=False)
    assert with_gpu == without
    assert "h264_nvenc" not in with_gpu


def test_master_and_bridge_encoders_ignore_the_hardware_probe(monkeypatch):
    """The master of every format, the loop bridge's two passes and a micro-patched master."""
    _machine(monkeypatch, nvenc=True)
    assert vc._get_video_encoder() == DELIVERY
    assert vc._get_intermediate_video_encoder() == DELIVERY_INTERMEDIATE


def test_graphic_cache_key_names_the_production_encoder(monkeypatch, tmp_path):
    """A graphic cached by an NVENC encode on an older build must not be reused: its key names the encoder."""
    _machine(monkeypatch, nvenc=True)
    keys = []
    monkeypatch.setattr(graphics.geo, "locate", lambda topic: None)

    def render(spec, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clip")
        path.with_suffix(".jpg").write_bytes(b"poster")
        return path

    from src import checkpoints
    real = checkpoints.Checkpoints.reusable

    def reusable(self, stage, inputs):
        keys.append(inputs.get("encoder"))
        return real(self, stage, inputs)
    monkeypatch.setattr(checkpoints.Checkpoints, "reusable", reusable)
    monkeypatch.setattr(graphics, "render_timeline", render)
    graphics.make_all({"timeline": [{"year": 100, "label": "First"}]}, {}, tmp_path)
    assert keys == [DELIVERY]


def test_splice_encodes_the_patched_master_with_the_production_encoder(monkeypatch, tmp_path):
    _machine(monkeypatch, nvenc=True)
    commands = []
    monkeypatch.setattr(vc, "_run_ffmpeg", lambda cmd, *a, **k: commands.append(list(cmd)))
    monkeypatch.setattr(vc, "video_colorspace", lambda path: "bt709")
    master, clip = tmp_path / "master.mp4", tmp_path / "beat.mp4"
    master.write_bytes(b"m")
    clip.write_bytes(b"c")
    vc.VideoComposer.splice_single_beat(master, 1, clip, [1.0, 1.0, 1.0], tmp_path / "patched.mp4")
    (command,) = commands
    assert _encoder_of(command) == PRODUCTION, command


def test_no_delivery_path_reaches_the_hardware_probe():
    """Only encoding.preview_video_encoder may use the probe (get_video_encoder); no other module in src/ or main.py,
    and no design-freeze evidence script (docs/reviews/freeze-*/, reused by later reviews and freezes), names it, so
    no delivered or judged picture can follow the machine's hardware."""
    callers = []
    freeze = sorted(path for folder in (ROOT / "docs" / "reviews").glob("freeze-*") for path in folder.rglob("*.py"))
    for path in [ROOT / "main.py", *sorted((ROOT / "src").rglob("*.py")), *freeze]:
        if path == ROOT / "src" / "encoding.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bget_video_encoder\b|\bget_intermediate_video_encoder\b", text):
            callers.append(path.relative_to(ROOT).as_posix())
    assert callers == []


def test_scratch_previews_follow_the_setting(monkeypatch):
    preview = getattr(encoding, "preview_video_encoder", None)
    assert callable(preview), "no preview encoder separate from delivery"
    _machine(monkeypatch, nvenc=True)
    monkeypatch.setattr(config, "VIDEO_PREVIEW_ENCODER", "production")
    assert preview() == DELIVERY
    monkeypatch.setattr(config, "VIDEO_PREVIEW_ENCODER", "fastest")
    assert preview()[1] == "h264_nvenc"
    _machine(monkeypatch, nvenc=False)
    assert preview() == PRODUCTION


def test_preview_setting_defaults_to_the_production_encoder():
    import os
    import sys
    env = {k: v for k, v in os.environ.items() if k != "VIDEO_PREVIEW_ENCODER"}
    code = "from src import config; print(getattr(config, 'VIDEO_PREVIEW_ENCODER', None))"
    value = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=120, check=False).stdout.strip().splitlines()
    assert value and value[-1] == "production"
    assert "VIDEO_PREVIEW_ENCODER" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_manifest_records_the_encoder_and_its_arguments(monkeypatch, tmp_path):
    _machine(monkeypatch, nvenc=True)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-t", "2.5", str(voice)], check=True)
    scenes = []
    for i, colour in enumerate(("red", "blue")):
        path = tmp_path / f"scene{i}.png"
        Image.new("RGB", (64, 64), colour).save(path)
        scenes.append(path)
    out = tmp_path / "master.mp4"
    vc.VideoComposer().compose_video(scenes, voice, voice, [], 2.0, out, durations=[1.0, 1.0])
    manifest = json.loads(out.with_suffix(".composition.json").read_text(encoding="utf-8"))
    record = manifest.get("video_encoder")
    assert record, "the run's manifest does not say which encoder made the master"
    assert record["master"] == DELIVERY
    assert record["intermediate"] is None            # no loop bridge on this master
    assert record["delivery_args"][:6] == PRODUCTION
    sei = out.read_bytes()[: 8 << 20]
    assert b"x264 - core" in sei and b"crf=18.0" in sei and b"subme=8" in sei   # slow preset, CRF 18, as recorded
