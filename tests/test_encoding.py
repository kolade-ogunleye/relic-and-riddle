from types import SimpleNamespace

from src import encoding


def test_encoder_falls_back_when_hardware_probe_fails(monkeypatch):
    monkeypatch.setattr(encoding, "_DETECTED_ENCODER", None)
    monkeypatch.setattr(encoding.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1))
    assert encoding.get_video_encoder()[1] == "libx264"


def test_encoder_reuses_successful_probe(monkeypatch):
    monkeypatch.setattr(encoding, "_DETECTED_ENCODER", None)
    calls = []
    def probe(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(encoding.subprocess, "run", probe)
    assert encoding.get_video_encoder()[1] == "h264_nvenc"
    assert encoding.get_video_encoder()[1] == "h264_nvenc"
    assert len(calls) == 1


def test_nested_workers_cannot_exceed_encoding_slots(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(encoding, "_ENCODING_SLOTS", threading.BoundedSemaphore(2))
    lock = threading.Lock()
    active = peak = 0
    @encoding.bounded_encode
    def encode_job(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)
            return value
        finally:
            with lock:
                active -= 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(encode_job, range(8))) == list(range(8))
    assert peak <= 2
    assert active == 0
