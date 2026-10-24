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
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=2, query="Derinkuyu stone door", keywords=["derinkuyu"]),
        BeatQuery(beat=1, query="Derinkuyu", keywords=["derinkuyu"])]))
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: [q])
    pools = {"Derinkuyu stone door": [_cand("A"), _cand("B"), _cand("C")], "Derinkuyu": [_cand("A"), _cand("D")]}
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter(pools[pages[0]]))
    monkeypatch.setattr(beat_images.commons, "download_jpeg",
                        lambda url, dest, max_wait=30: Image.new("RGB", (10, 10)).save(dest, "JPEG"))

    paths, credits = beat_images.source(BEATS, TOPIC, tmp_path)

    assert [c["title"] for c in credits] == ["A", "B", "D"]  # 2 for beat 2; "A" not repeated for beat 1
    assert [p.name for p in paths] == ["beat02_1.jpg", "beat02_2.jpg", "beat01_1.jpg"]
    assert all(p.exists() for p in paths) and credits[2]["beat"] == 1


def test_source_is_optional_without_claude(tmp_path):
    assert beat_images.source(BEATS, TOPIC, tmp_path) == ([], [])


def test_source_survives_commons_failure(monkeypatch, tmp_path):
    import requests
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
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


def test_the_roman_concrete_mismatches_are_found_and_searched_for(tmp_path, monkeypatch):
    # Production 2026-10-23: beat 9 ("the visual displays the interior dome") matched no picture word and
    # beat 8 was "minor", so neither was repaired; and a repair could only re-choose from the same photos.
    from src import config
    from src.models import BeatQueries
    report = QAReport(summary="", issues=[
        QAIssue(beat=8, severity="minor", issue="The visual shows the intact interior of the Roman Pantheon while the narration describes modern concrete cracking."),
        QAIssue(beat=9, severity="major", issue="The narration explicitly refers to Roman aqueducts and seawalls, but the visual displays the interior dome."),
        QAIssue(beat=3, severity="minor", issue="Image slightly soft"),
    ])
    assert beat_images.mismatched_beats(report) == [8, 9]
    beats = [{"text": f"beat {i} text"} for i in range(1, 12)]
    monkeypatch.setattr(config, "BEAT_IMAGES_ENABLED", True)
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_gap_queries", lambda b, g, t, n="": BeatQueries.model_validate(
        {"items": [{"beat": 9, "query": "Roman aqueduct", "keywords": ["aqueduct"]},
                   {"beat": 4, "query": "not asked", "keywords": ["x"]}]}))
    asked = []
    monkeypatch.setattr(beat_images, "_download", lambda items, topic, work_dir, seen, prefix, wait=30: asked.append(
        ([i.beat for i in items], prefix)) or ([], []))
    beat_images.source_gaps(beats, [8, 9], {"title": "T"}, tmp_path, set())
    assert asked == [([9], "gap")]  # only the flagged beats, filed as gap pictures


def test_a_finding_that_the_video_shows_another_site_is_a_picture_mismatch():
    from src.beat_images import picture_issues
    from src.models import QAIssue, QAReport
    kailasa = QAReport(summary="", issues=[QAIssue(beat=5, severity="minor", issue=(
        "The narration and subtitle refer to the Virupaksha Temple at Pattadakal, but the video continues to show "
        "the Kailasa Temple at Ellora rather than the prototype temple mentioned."))])
    assert picture_issues(kailasa).startswith("Beat 5:")                       # production 2026-10-24: unrepaired
    caption_only = QAReport(summary="", issues=[QAIssue(beat=3, severity="minor", issue="The caption overlaps the lower edge.")])
    assert picture_issues(caption_only) == ""


def test_a_beat_about_another_place_is_ranked_by_that_place_not_by_distance_from_the_site(monkeypatch, tmp_path):
    # Production 2026-10-24: a photo of the Virupaksha Temple at Pattadakal, 430 km from Ellora, scored 3/10
    # for being where Pattadakal is, and the planner stayed on Kailasa.
    from src.models import BeatQuery
    seen = []
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: [q])
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter([]))
    monkeypatch.setattr(beat_images.commons, "best_first", lambda cands, kw, place: seen.append((kw, place)) or [])
    topic = {"id": "kailasa", "location": {"name": "Kailasa Temple, Ellora Caves", "latitude": 20.02, "longitude": 75.18}}
    items = [BeatQuery(beat=5, query="Virupaksha Temple Pattadakal", keywords=["virupaksha"], place="Pattadakal"),
             BeatQuery(beat=2, query="Kailasa Temple courtyard", keywords=["kailasa"]),
             BeatQuery(beat=3, query="Ellora cave 16", keywords=["ellora"], place="Ellora Caves")]
    beat_images._download(items, topic, tmp_path / "beat_images", set(), "beat")
    assert seen[0] == (["virupaksha", "pattadakal"], None)                       # elsewhere: its name, no distance rule
    assert seen[1] == (["kailasa"], {"lat": 20.02, "lon": 75.18})                # the site itself
    assert seen[2] == (["ellora"], {"lat": 20.02, "lon": 75.18})                 # the same place, named differently
