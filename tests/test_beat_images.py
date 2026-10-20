from pathlib import Path

from PIL import Image

from src import beat_images
from src.models import BeatQueries, BeatQuery, QAIssue, QAReport

BEATS = [{"text": "Beneath Cappadocia lies a city.", "start": 0, "end": 3},
         {"text": "Rolling stone doors sealed each level.", "start": 3, "end": 6}]
TOPIC = {"title": "Derinkuyu", "location": {"name": "Derinkuyu", "country": "Turkey"}}


def _cand(title, licence="CC BY 4.0"):
    return {"title": title, "download_url": f"https://x/{title}", "source_url": "", "artist": "Ann",
            "license": licence, "license_url": "", "attribution_required": True,
            "description": "Derinkuyu underground city"}  # real candidates always name the subject


def test_source_downloads_up_to_two_per_beat_and_skips_duplicates(monkeypatch, tmp_path):
    monkeypatch.setattr(beat_images, "claude_available", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=2, query="Derinkuyu stone door", keywords=["derinkuyu"]),
        BeatQuery(beat=1, query="Derinkuyu", keywords=["derinkuyu"])]))
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: [q])
    pools = {"Derinkuyu stone door": [_cand("A"), _cand("B"), _cand("C")], "Derinkuyu": [_cand("A"), _cand("D")]}
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter(pools[pages[0]]))
    monkeypatch.setattr(beat_images.commons, "download_jpeg",
                        lambda url, dest: Image.new("RGB", (10, 10)).save(dest, "JPEG"))

    paths, credits = beat_images.source(BEATS, TOPIC, tmp_path)

    assert [c["title"] for c in credits] == ["A", "B", "D"]  # 2 for beat 2; "A" not repeated for beat 1
    assert [p.name for p in paths] == ["beat02_1.jpg", "beat02_2.jpg", "beat01_1.jpg"]
    assert all(p.exists() for p in paths) and credits[2]["beat"] == 1


def test_source_is_optional_without_claude(tmp_path):
    assert beat_images.source(BEATS, TOPIC, tmp_path) == ([], [])


def test_source_survives_commons_failure(monkeypatch, tmp_path):
    import requests
    monkeypatch.setattr(beat_images, "claude_available", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=1, query="q", keywords=["k"])]))

    def down(*a, **k):
        raise requests.ConnectionError("rate limited")
    monkeypatch.setattr(beat_images.commons, "search", down)
    assert beat_images.source(BEATS, TOPIC, tmp_path) == ([], [])


def test_used_credits_matches_original_or_upscaled_path():
    creds = [{"file": "a.jpg", "upscaled": "cache/a_up.jpg"}, {"file": "b.jpg", "upscaled": "b.jpg"}]
    assert beat_images.used_credits([Path("cache/a_up.jpg"), Path("x.jpg")], creds) == [creds[0]]


def test_credit_caption_replaces_line_and_is_idempotent():
    topic = {"images": [{"artist": "Ann", "license": "CC BY 4.0", "attribution_required": True}]}
    extra = [{"artist": "Bob", "license": "CC BY 2.0", "attribution_required": True}]
    caption = "Story.\n\n📷 Images via Wikimedia Commons: Ann (CC BY 4.0)"
    once = beat_images.credit_caption(caption, topic, extra)
    assert once == "Story.\n\n📷 Images via Wikimedia Commons: Ann (CC BY 4.0); Bob (CC BY 2.0)"
    assert beat_images.credit_caption(once, topic, extra) == once
    assert beat_images.credit_caption("Story.", {"images": []}, []) == "Story."


def test_picture_issues_only_reports_serious_picture_problems():
    report = QAReport(summary="", issues=[
        QAIssue(beat=2, severity="major", issue="The image shows a modern mall, not a stone door"),
        QAIssue(beat=3, severity="major", issue="Caption overlaps the edge"),
        QAIssue(beat=1, severity="minor", issue="Image slightly soft"),
        QAIssue(beat=0, severity="critical", issue="Cover image unrelated"),
    ])
    assert beat_images.picture_issues(report) == "Beat 2: The image shows a modern mall, not a stone door"
    assert beat_images.picture_issues(None) == ""
