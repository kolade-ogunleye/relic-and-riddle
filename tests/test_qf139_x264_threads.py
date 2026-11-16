"""QF-139: every libx264 encode passes a fixed thread count, so a master is the same bytes at every core count.

x264's output depends on its thread count. Unless told, it derives the count from the machine (1.5 threads per
logical processor the process may run on), and the master pass was told os.cpu_count(): the 32-core development
PC, the 6-processor production VM and a CI runner encoded the same arguments into different files. A rate-capped
encode (the review proxy) is not deterministic with frame threads at all, so it takes one thread.
A machine is simulated here by what the code and the encoder read: os.cpu_count() (patched) and the processors the
process may run on (its affinity, which every ffmpeg it starts inherits)."""
import ast
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PIL import Image

from src import config, encoding, footage, graphics, motion, parallax, then_now
from src import video_composer as vc

THREADS = 6   # the count production's masters were already encoded with (its os.cpu_count())
ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _encoder_threads(cmd: list) -> list:
    """The -threads values that reach the output (and so the encoder): those after the last input."""
    cmd = [str(c) for c in cmd]
    last_input = max(i for i, c in enumerate(cmd) if c == "-i")
    return [cmd[i + 1] for i in range(last_input + 1, len(cmd) - 1) if cmd[i] == "-threads"]


class _Process:
    """Stands in for an ffmpeg process that frames are piped into; records its command."""
    commands: ClassVar[list] = []

    def __init__(self, cmd, *args, **kwargs):
        _Process.commands.append([str(c) for c in cmd])
        self.stdin = SimpleNamespace(write=lambda data: None, close=lambda: None)
        self.stderr = SimpleNamespace(read=lambda: b"")
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


@pytest.fixture
def machine_with_32_cores(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    _Process.commands = []


def test_the_thread_count_is_fixed_at_the_production_vm_count():
    assert getattr(config, "VIDEO_ENCODE_THREADS", None) == THREADS, "no fixed x264 thread count"
    assert encoding.delivery_video_encoder() == ["-c:v", "libx264", "-preset", "slow", "-crf", "18",
                                                 "-threads", str(THREADS)]
    assert encoding.delivery_intermediate_encoder() == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
                                                        "-threads", str(THREADS)]


def test_graphic_and_then_now_clips_pass_the_fixed_count(machine_with_32_cores, monkeypatch, tmp_path):
    monkeypatch.setattr(graphics.subprocess, "Popen", _Process)
    graphics._encode((Image.new("RGB", (64, 64)) for _ in range(2)), tmp_path / "graphic.mp4", 2, 64, 64)
    monkeypatch.setattr(subprocess, "Popen", _Process)   # then_now imports subprocess inside _encode
    then_now._encode((Image.new("RGB", (64, 64)) for _ in range(2)), tmp_path / "then_now.mp4", 2, 64, 64)
    assert [_encoder_threads(c) for c in _Process.commands] == [[str(THREADS)], [str(THREADS)]], _Process.commands


def test_parallax_clip_passes_the_fixed_count(machine_with_32_cores, monkeypatch, tmp_path):
    import numpy as np
    image = tmp_path / "photo.png"
    Image.new("RGB", (120, 200), "grey").save(image)
    monkeypatch.setattr(parallax, "depth_map", lambda rgb: np.zeros(rgb.shape[:2], np.float32))
    monkeypatch.setattr(parallax.subprocess, "Popen", _Process)
    monkeypatch.setattr("src.lineage.record_derived", lambda *a, **k: None)
    parallax.render_clip(image, tmp_path / "clip.mp4", 0.1, size=(64, 96))
    assert [_encoder_threads(c) for c in _Process.commands] == [[str(THREADS)]], _Process.commands


def test_footage_and_motion_clips_pass_the_fixed_count(machine_with_32_cores, monkeypatch, tmp_path):
    commands = []

    def run(cmd, *args, **kwargs):
        commands.append([str(c) for c in cmd])
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(footage.subprocess, "run", run)
    footage.make_segment(tmp_path / "film.mp4", {"duration": 30.0, "width": 1080, "height": 1920,
                                                 "color_space": "bt709"}, tmp_path / "segment.mp4")
    monkeypatch.setattr(motion, "probe", lambda path: {"duration": 10.0, "width": 1080, "height": 1920})
    monkeypatch.setattr(motion, "_gate", lambda *a, **k: {})
    motion._finish(tmp_path / "raw.mp4", tmp_path / "motion.mp4")
    encodes = [c for c in commands if "libx264" in c]
    assert len(encodes) == 2, commands
    assert [_encoder_threads(c) for c in encodes] == [[str(THREADS)], [str(THREADS)]], encodes


def test_splice_and_review_proxy_ignore_the_machine(machine_with_32_cores, monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(vc, "_run_ffmpeg", lambda cmd, *a, **k: commands.append([str(c) for c in cmd]))
    monkeypatch.setattr(vc, "video_colorspace", lambda path: "bt709")
    master, clip = tmp_path / "master.mp4", tmp_path / "beat.mp4"
    master.write_bytes(b"m")
    clip.write_bytes(b"c")
    vc.VideoComposer.splice_single_beat(master, 1, clip, [1.0, 1.0, 1.0], tmp_path / "patched.mp4")
    monkeypatch.setattr(vc.subprocess, "check_output", lambda *a, **k: b"45.0")
    monkeypatch.setattr(vc.os.path, "getsize", lambda path: 1)
    vc.VideoComposer.make_review_proxy(master, tmp_path / "proxy.mp4")
    assert len(commands) == 2, commands
    # the proxy is rate-capped (-maxrate/-bufsize): one x264 thread, the only deterministic count there
    assert [_encoder_threads(c) for c in commands] == [[str(THREADS)], ["1"]], commands


# Where "libx264" may stand without a fixed count: the hardware probe's fallback, which only scratch previews and
# scripts/benchmark_encoders.py reach (QF-134: nothing delivered or judged calls it).
UNPINNED_ALLOWED = {("src/encoding.py", "get_video_encoder")}
# Where an output "-threads" other than X264_THREADS / X264_ONE_THREAD may stand: their own definitions, and the
# still camera's FFV1 intermediates (lossless: the decoded pictures do not depend on the thread count).
OUTPUT_THREADS_ALLOWED = {("src/encoding.py", None), ("src/still_camera.py", "render_clip")}
RATE_CAP = {"-maxrate", "-bufsize"}


def _x264_problems(path: Path, rel: str) -> list[str]:
    """Every place in `path` where an x264 encode could take a machine-dependent or non-deterministic thread count:
    libx264 named in code (not a docstring, not a comparison) outside an argument list carrying *X264_THREADS or
    *X264_ONE_THREAD; a rate-capped argument list (-maxrate/-bufsize) without *X264_ONE_THREAD; and an output-side
    "-threads" (no input after it in the same list) other than those two, which would override them (ffmpeg takes
    the last)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def function_of(node):
        up = parents.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return up.name
            up = parents.get(up)
        return None

    def starred(seq, name: str) -> bool:
        return any(isinstance(e, ast.Starred) and getattr(e.value, "id", getattr(e.value, "attr", None)) == name
                   for e in seq.elts)

    def text(e):
        return e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "libx264" in node.value:
            parent = parents.get(node)
            if isinstance(parent, ast.JoinedStr):
                node, parent = parent, parents.get(parent)
            if isinstance(parent, (ast.Expr, ast.Compare)):   # a docstring, or code reading a value
                continue
            function = function_of(node)
            if (rel, function) in UNPINNED_ALLOWED:
                continue
            if not (isinstance(parent, (ast.List, ast.Tuple))
                    and (starred(parent, "X264_THREADS") or starred(parent, "X264_ONE_THREAD"))):
                found.append(f"{rel}:{node.lineno} ({function or 'module'}): libx264 without a fixed thread count")
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        function = function_of(node)
        words = [text(e) for e in node.elts]
        if RATE_CAP & set(words) and not starred(node, "X264_ONE_THREAD"):
            found.append(f"{rel}:{node.lineno} ({function or 'module'}): rate-capped encode without X264_ONE_THREAD")
        for i, word in enumerate(words):
            if word != "-threads" or (rel, function) in OUTPUT_THREADS_ALLOWED:
                continue
            later = node.elts[i + 1:]
            input_side = any(text(e) == "-i" for e in later) or any(
                isinstance(e, ast.Starred) and getattr(e.value, "id", "") == "input_args" for e in later)
            if not input_side:
                found.append(f"{rel}:{node.elts[i].lineno} ({function or 'module'}): output -threads overrides the "
                             "fixed count")
    return found


def test_every_libx264_encode_in_the_code_carries_the_fixed_count():
    """Invariant: a new libx264 encode in src/ or main.py without *X264_THREADS (or *X264_ONE_THREAD), a rate-capped
    encode with frame threads, or a later "-threads" that overrides the fixed count fails here, so no path can
    quietly take the machine's thread count again. Encodes that take encoding.delivery_video_encoder() /
    delivery_intermediate_encoder() carry it through those (tested above)."""
    found = []
    for path in [ROOT / "main.py", *sorted((ROOT / "src").rglob("*.py"))]:
        found += _x264_problems(path, path.relative_to(ROOT).as_posix())
    assert found == [], f"x264 thread count not fixed (encoding.X264_THREADS / X264_ONE_THREAD): {found}"


def test_the_invariant_catches_a_path_without_the_fixed_count(tmp_path):
    """The scan itself, on small sources: each way to lose the fixed count is reported; the fixed forms are not."""
    def source(name: str, *lines: str) -> Path:
        path = tmp_path / name
        path.write_text("".join(line + chr(10) for line in lines), encoding="utf-8")
        return path

    def problems(name: str, *lines: str) -> list[str]:
        return _x264_problems(source(name, *lines), name)

    assert problems("bare.py", "def clip(out):",
                    '    return ["ffmpeg", "-i", "-", "-c:v", "libx264", "-crf", "17", str(out)]') == [
        "bare.py:2 (clip): libx264 without a fixed thread count"]
    assert problems("hidden.py", 'CODEC = "libx264"') == ["hidden.py:1 (module): libx264 without a fixed thread count"]
    # the original master bug: the encoder's arguments, then the machine's count after them
    assert problems("override.py", "def args(encoder_args):",
                    '    return [*encoder_args, "-threads", str(os.cpu_count() or 0), "-pix_fmt", "yuv420p"]') == [
        "override.py:2 (args): output -threads overrides the fixed count"]
    assert problems("capped.py", "def proxy(src, out):",
                    '    return ["ffmpeg", "-i", src, "-c:v", "libx264", *X264_THREADS, "-maxrate", "1500k", out]') == [
        "capped.py:2 (proxy): rate-capped encode without X264_ONE_THREAD"]
    assert problems("fixed.py", "def clip(out):",
                    '    """libx264 in a docstring is prose."""',
                    '    head = ["ffmpeg", "-threads", str(os.cpu_count() or 0), *input_args]',
                    '    proxy = ["ffmpeg", "-threads", "8", "-i", out, "-c:v", "libx264", *X264_ONE_THREAD,',
                    '             "-maxrate", "1500k", out]',
                    '    return head + ["-c:v", "libx264", *X264_THREADS, str(out)] + proxy') == []


def _restrict(count: int) -> None:
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, range(count))
        return
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    assert kernel.SetProcessAffinityMask(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.c_size_t((1 << count) - 1))


def _processors() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    process, system = ctypes.c_size_t(), ctypes.c_size_t()
    kernel.GetProcessAffinityMask(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(process),
                                  ctypes.byref(system))
    return process.value.bit_count()


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_a_master_is_the_same_bytes_on_machines_with_different_core_counts(monkeypatch, tmp_path):
    """A real master (the loop-bridge Reel: body intermediate and delivery pass) composed on a 2-processor machine
    and on one with all of this host's processors (os.cpu_count 32) is the same file."""
    host = _processors()
    if host < 2:
        pytest.skip("needs a host with two logical processors")
    voice, bed = tmp_path / "voice.wav", tmp_path / "bed.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-t", "2.5", str(voice)], check=True)
    from src import end_card
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=110:sample_rate=48000",
                    "-t", f"{2.5 + end_card.LOOP_SECONDS + 1:.2f}", str(bed)], check=True)
    scenes = []
    for i, colour in enumerate(("red", "blue")):
        path = tmp_path / f"scene{i}.png"
        Image.effect_noise((96, 160), 40 + 20 * i).convert("RGB").save(path)
        scenes.append(path)
    words = [{"word": "Same", "start": 0.2, "dur": 0.5}, {"word": "bytes", "start": 0.9, "dur": 0.5}]
    files, streams = {}, {}
    try:
        for name, (cores, processors) in {"small": (2, 2), "large": (32, host)}.items():
            monkeypatch.setattr(os, "cpu_count", lambda cores=cores: cores)
            _restrict(processors)
            out = tmp_path / name / "master.mp4"
            out.parent.mkdir()
            vc.VideoComposer().compose_video(scenes, voice, bed, words, 2.0, out, durations=[1.0, 1.0],
                                             width=160, height=288, end_card="Follow for more")
            data = out.read_bytes()
            files[name] = hashlib.sha256(data).hexdigest()
            streams[name] = re.search(rb"threads=(\d+) lookahead_threads", data[: 8 << 20]).group(1)
    finally:
        _restrict(host)
    assert streams["small"] == streams["large"] == str(THREADS).encode(), streams
    assert files["small"] == files["large"]
