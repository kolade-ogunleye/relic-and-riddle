import json

import pytest
from PIL import Image

from src import footage


class FakeResp:
    def __init__(self, status=200, ctype="video/mp4", chunks=(b"x" * 10,), location=None):
        self.status_code, self.headers = status, {"Content-Type": ctype}
        if location:
            self.headers["Location"] = location
        self._chunks = chunks
        self.is_redirect = status in (301, 302)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise footage.requests.HTTPError(str(self.status_code))

    def iter_content(self, n):
        return iter(self._chunks)


@pytest.mark.parametrize("url", ["http://upload.wikimedia.org/a.webm", "https://evil.example.com/a.webm",
                                 "https://upload.wikimedia.org.evil.com/a.webm", "file:///etc/passwd"])
def test_download_refuses_other_hosts_and_plain_http(url, tmp_path):
    with pytest.raises(ValueError):
        footage.download(url, "commons", tmp_path / "f")


def test_download_refuses_redirect_off_host_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(footage.requests, "get", lambda *a, **k: FakeResp(302, location="https://evil.example.com/x"))
    with pytest.raises(ValueError, match="redirect"):
        footage.download("https://upload.wikimedia.org/a.webm", "commons", tmp_path / "f")
    monkeypatch.setattr(footage, "MAX_BYTES", 15)
    monkeypatch.setattr(footage.requests, "get", lambda *a, **k: FakeResp(chunks=(b"x" * 10, b"x" * 10)))
    with pytest.raises(ValueError, match="size cap"):
        footage.download("https://upload.wikimedia.org/a.webm", "commons", tmp_path / "f")
    monkeypatch.setattr(footage.requests, "get", lambda *a, **k: FakeResp(ctype="text/html"))
    with pytest.raises(ValueError, match="content type"):
        footage.download("https://upload.wikimedia.org/a.webm", "commons", tmp_path / "f")


def test_probe_refuses_playlists(tmp_path):
    playlist = tmp_path / "evil.mp4"
    playlist.write_text("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///C:/Windows/win.ini\n#EXT-X-ENDLIST\n")
    with pytest.raises(ValueError):
        footage.probe(playlist)


def test_credit_line_per_licence_and_idempotent():
    used = [{"provider": "commons", "artist": "Ann", "license": "CC BY 4.0"},
            {"provider": "pexels", "artist": "Bo", "license": "Pexels License"},
            {"provider": "nasa", "artist": "NASA", "license": "Public domain"}]
    line = footage.credit_line(used)
    assert line == "🎥 Footage: Ann (CC BY 4.0) via Wikimedia Commons; Bo / Pexels; NASA"
    once = footage.with_credits("Story.\n📷 Images: X", used)
    assert footage.with_credits(once, used) == once and once.endswith(line)


def test_planner_caps_footage_share(tmp_path, monkeypatch):
    from src import scene_planner
    from src.models import ScenePlan
    stills = []
    for k in range(2):
        p = tmp_path / f"s{k}.jpg"
        Image.new("RGB", (10, 10)).save(p)
        stills.append(p)
    clips = []
    for k in range(4):
        c = tmp_path / f"footage_{k:02d}.mp4"
        c.write_bytes(b"0")
        Image.new("RGB", (10, 10)).save(c.with_suffix(".jpg"))
        c.with_suffix(".json").write_text(json.dumps({"place_specific": k == 0}), encoding="utf-8")
        clips.append(c)
    beats = [{"start": i * 3.0, "end": (i + 1) * 3.0, "text": f"b{i}"} for i in range(5)]
    monkeypatch.setattr(scene_planner, "claude_available", lambda: True)
    seen_labels = []

    def fake_call(system, content, model, **k):
        seen_labels.extend(c["text"] for c in content if c.get("type") == "text")
        return ScenePlan(images=[3, 4, 5, 6, 1], notes="n")  # 4 of 5 beats footage
    monkeypatch.setattr(scene_planner, "structured_call", fake_call)
    chosen, durations, _ = scene_planner.plan_scenes(beats, stills + clips)
    assert scene_planner._footage_share(chosen, durations) <= 0.4
    assert any("actual site" in t for t in seen_labels) and any("GENERIC" in t for t in seen_labels)


def test_curated_footage_ingestion(tmp_path, monkeypatch):
    topic_id = "derinkuyu_underground_city"
    assets_dir = tmp_path / "assets"
    curated_dir = assets_dir / topic_id / "footage"
    curated_dir.mkdir(parents=True)
    fake_video = curated_dir / "drone_flyover.mp4"
    fake_video.write_bytes(b"dummy_video_bytes")
    (curated_dir / "drone_flyover.json").write_text(
        json.dumps({"title": "4K Drone Flyover", "uploader": "Boutique Aerials", "license": "Custom License"}),
        encoding="utf-8"
    )

    work_dir = tmp_path / "work"
    monkeypatch.setattr(footage.config, "ASSETS_DIR", assets_dir)
    monkeypatch.setattr(footage, "probe", lambda p: {"width": 1080, "height": 1920, "duration": 15.0})
    def fake_make_segment(src, info, out):
        out.write_bytes(b"segmented")
        Image.new("RGB", (10, 10)).save(out.with_suffix(".jpg"))
        return out
    monkeypatch.setattr(footage, "make_segment", fake_make_segment)

    clips, credits = footage.get_curated_footage(topic_id, work_dir)
    assert len(clips) == 1
    assert clips[0].name == "footage_01.mp4"
    assert len(credits) == 1
    assert credits[0]["title"] == "4K Drone Flyover"
    assert credits[0]["artist"] == "Boutique Aerials"
    assert credits[0]["provider"] == "curated"

    line = footage.credit_line(credits)
    assert "Boutique Aerials (Custom License)" in line

    # Test source() picks up curated clips
    topic = {"id": topic_id, "location": {"name": "Derinkuyu"}}
    s_clips, s_credits = footage.source([], topic, work_dir)
    assert len(s_clips) == 1
    assert s_credits[0]["artist"] == "Boutique Aerials"


def test_source_empty_when_no_curated_footage(tmp_path, monkeypatch):
    topic_id = "empty_topic"
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(footage.config, "ASSETS_DIR", assets_dir)
    topic = {"id": topic_id, "location": {"name": "Nowhere"}}
    clips, credits = footage.source([], topic, tmp_path / "work")
    assert clips == []
    assert credits == []


