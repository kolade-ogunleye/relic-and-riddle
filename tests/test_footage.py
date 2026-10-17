import json
from pathlib import Path

import pytest
from PIL import Image

from src import footage
from src.footage import FootageCheck


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


def test_quality_bar_needs_4k_landscape_or_vertical():
    assert not footage._good_size(1920, 1080)   # 1080p landscape: 607 px wide after the 9:16 crop
    assert not footage._good_size(2560, 1440)   # 1440p: 810 px
    assert footage._good_size(3840, 2160)       # 4K: 1215 px
    assert footage._good_size(1080, 1920)       # vertical HD


def test_commons_search_filters_licence_and_size(monkeypatch):
    pages = {"1": {"title": "File:Derinkuyu 4K.webm", "index": 1, "imageinfo": [{
                 "width": 3840, "height": 2160, "duration": 60, "url": "https://upload.wikimedia.org/d.webm",
                 "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "Ann"}}}]},
             "2": {"title": "File:Small.webm", "index": 2, "imageinfo": [{
                 "width": 1280, "height": 720, "duration": 60, "url": "https://upload.wikimedia.org/s.webm",
                 "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}}}]},
             "3": {"title": "File:NC.webm", "index": 3, "imageinfo": [{
                 "width": 3840, "height": 2160, "duration": 60, "url": "https://upload.wikimedia.org/n.webm",
                 "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}}}]}}

    class R:
        def json(self):
            return {"query": {"pages": pages}}
    monkeypatch.setattr(footage.commons, "_get", lambda *a, **k: R())
    found = footage.search_commons("Derinkuyu", ["derinkuyu"])
    assert [c["title"] for c in found] == ["File:Derinkuyu 4K.webm"]


def test_source_labels_place_specific_only_when_named_and_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "claude_available", lambda: True)
    cands = [
        {"provider": "commons", "title": "Derinkuyu tunnels", "text": "Derinkuyu underground city tunnels", "download_url": "u1",
         "source_url": "s1", "license": "CC BY 4.0", "artist": "Ann", "attribution_required": True, "width": 3840, "height": 2160, "duration": 60},
        {"provider": "commons", "title": "Generic cave", "text": "a cave in Cappadocia", "download_url": "u2",
         "source_url": "s2", "license": "CC0", "artist": "Bob", "attribution_required": False, "width": 3840, "height": 2160, "duration": 60},
        {"provider": "commons", "title": "Watermarked", "text": "Derinkuyu", "download_url": "u3",
         "source_url": "s3", "license": "CC0", "artist": "C", "attribution_required": False, "width": 3840, "height": 2160, "duration": 60},
    ]
    monkeypatch.setattr(footage, "PROVIDERS", [("commons", lambda q, k: list(cands))])
    monkeypatch.setattr(footage, "PER_QUERY", 3)
    monkeypatch.setattr(footage, "download", lambda url, prov, dest: dest.write_bytes(b"x") or dest)
    monkeypatch.setattr(footage, "probe", lambda p: {"width": 3840, "height": 2160, "duration": 60})

    def segment(src, info, out):
        out.write_bytes(b"0")
        Image.new("RGB", (10, 10)).save(out.with_suffix(".jpg"))
        return out
    monkeypatch.setattr(footage, "make_segment", segment)
    verdicts = iter([("yes", False), ("yes", True), ("yes", False)])  # in call order: tunnels, watermarked, cave

    def check(clip, q, place):
        pm, wm = next(verdicts)
        return FootageCheck(matches_search=True, place_matches=pm, burned_in_text_or_watermark=wm,
                            brand_logo=False, identifiable_person_focus=False, note="")
    monkeypatch.setattr(footage, "verify", check)
    topic = {"location": {"name": "Derinkuyu Underground City", "country": "Turkey", "region": "Cappadocia"}}
    clips, credits = footage.source([], topic, tmp_path)
    assert [c["title"] for c in credits] == ["Derinkuyu tunnels", "Generic cave"]  # watermarked one dropped
    assert credits[0]["place_specific"] is True
    assert credits[1]["place_specific"] is False  # found via the region search; its metadata doesn't name the site
    assert credits[1]["query"] == "Cappadocia"
    assert not any(tmp_path.glob("raw_*"))  # original downloads are never kept
    assert json.loads((tmp_path / "credits.json").read_text(encoding="utf-8"))[0]["source_url"] == "s1"


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


def test_anachronism_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(footage, "claude_available", lambda: True)
    cands = [{"provider": "commons", "title": "Modern concrete mixer", "text": "Roman concrete", "download_url": "u1",
              "source_url": "s1", "license": "CC BY 4.0", "artist": "Ann", "attribution_required": True, "width": 3840, "height": 2160, "duration": 60}]
    monkeypatch.setattr(footage, "PROVIDERS", [("commons", lambda q, k: list(cands))])
    monkeypatch.setattr(footage, "PER_QUERY", 1)
    monkeypatch.setattr(footage, "download", lambda url, prov, dest: dest.write_bytes(b"x") or dest)
    monkeypatch.setattr(footage, "probe", lambda p: {"width": 3840, "height": 2160, "duration": 60})
    monkeypatch.setattr(footage, "make_segment", lambda s, i, o: o.write_bytes(b"0") or o)
    monkeypatch.setattr(footage, "verify", lambda c, q, p: FootageCheck(matches_search=True, place_matches="yes",
                                                                        has_anachronism=True, burned_in_text_or_watermark=False,
                                                                        brand_logo=False, identifiable_person_focus=False, note="modern machinery"))
    clips, credits = footage.source([], {"location": {"name": "Roman Port", "country": "Italy"}}, tmp_path)
    assert len(clips) == 0 and len(credits) == 0


def test_search_youtube_cc_and_credit_line(monkeypatch):
    # 1. Missing key returns empty list safely
    monkeypatch.setattr(footage.config, "YOUTUBE_API_KEY", "")
    assert footage.search_youtube_cc("Derinkuyu", []) == []

    # 2. Configured key parses items correctly
    monkeypatch.setattr(footage.config, "YOUTUBE_API_KEY", "fake-yt-key")
    fake_response = {
        "items": [
            {
                "id": {"videoId": "test_vid_123"},
                "snippet": {
                    "title": "Ancient Derinkuyu Tunnels",
                    "description": "Exploration of Cappadocia underground city",
                    "channelTitle": "History Channel CC",
                },
            }
        ]
    }
    monkeypatch.setattr(footage, "_cached_get", lambda url, params: fake_response)
    results = footage.search_youtube_cc("Derinkuyu", [])
    assert len(results) == 1
    assert results[0]["provider"] == "youtube"
    assert results[0]["download_url"] == "https://www.youtube.com/watch?v=test_vid_123"
    assert results[0]["artist"] == "History Channel CC"

    # 3. Credit line formatting
    line = footage.credit_line(results)
    assert "History Channel CC via YouTube (CC-BY)" in line

