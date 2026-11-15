"""QF-22b (QF-22 review 2): the measure runs on production's encoder, the disk refusal names an existing folder in
GiB, and a splice's temporary camera clip carries the `<stem>.camera-` prefix the stale sweep matches."""

import fnmatch
import importlib.util
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import encoding, still_camera

ROOT = Path(__file__).resolve().parents[1]
MEASURE = ROOT / "docs/reviews/quality-fixes-2026-11/QF-22/measure.py"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")


def test_the_measure_pins_productions_encoder(monkeypatch):
    spec = importlib.util.spec_from_file_location("qf22_measure", MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "pin_encoder"), "measure.py must pin the H.264 encoder, not let an NVENC probe choose"
    monkeypatch.setattr(encoding, "_DETECTED_ENCODER", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21"])
    assert module.pin_encoder() == encoding.delivery_video_encoder()
    assert encoding.get_video_encoder() == encoding.delivery_video_encoder()   # no probe can pick NVENC after it
    assert "encoder" in MEASURE.read_text(encoding="utf-8").split("result = {", 1)[1].split("}", 1)[0]


def test_the_disk_refusal_names_the_existing_folder_in_gib(tmp_path, monkeypatch):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(still_camera.shutil, "disk_usage", lambda _p: usage(10 ** 12, 0, 3 * 1024 ** 3))
    run = tmp_path / "run_example"
    run.mkdir()
    with pytest.raises(RuntimeError) as refused:
        still_camera.ensure_disk(run / "master.camera-0123456789ab", 1800, (1080, 1920))
    message = str(refused.value)
    assert "run_example" in message and ".camera-" not in message       # the folder that exists, not the one to be
    assert message.count("GiB") == 2 and " GB" not in message            # the figures are 1024^3


@needs_ffmpeg
def test_a_splice_temporary_clip_is_one_the_stale_sweep_matches(tmp_path, monkeypatch):
    source = tmp_path / "beat.jpg"
    Image.fromarray(np.full((200, 120, 3), 128, np.uint8)).save(source)
    output = tmp_path / "master.camera-3.mkv"                 # a splice writes its clip into the run folder itself
    made = []
    real = still_camera.tempfile.mkstemp

    def recording(*args, **kwargs):
        handle, name = real(*args, **kwargs)
        made.append(Path(name))
        return handle, name
    monkeypatch.setattr(still_camera.tempfile, "mkstemp", recording)
    still_camera.render_clip(source, output, .2, (64, 112), 30, preset="splice")
    assert output.is_file() and len(made) == 1 and made[0].parent == tmp_path
    assert fnmatch.fnmatch(made[0].name, "*.camera-*"), made[0].name
    left = tmp_path / made[0].name                            # what a hard kill mid-encode would leave
    left.write_bytes(b"x")
    stale = time.time() - still_camera.STALE_SECONDS - 60
    os.utime(left, (stale, stale))
    assert left in still_camera.sweep_stale(tmp_path) and not left.exists()
