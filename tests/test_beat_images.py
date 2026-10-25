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


def test_source_downloads_up_to_three_per_beat_and_skips_duplicates(monkeypatch, tmp_path):
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

    assert [c["title"] for c in credits] == ["A", "B", "C", "D"]  # 3 for beat 2; "A" not repeated for beat 1
    assert [p.name for p in paths] == ["beat02_1.jpg", "beat02_2.jpg", "beat02_3.jpg", "beat01_1.jpg"]
    assert all(p.exists() for p in paths) and credits[3]["beat"] == 1


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


def test_each_beats_search_leaves_its_counts_in_the_run_log(monkeypatch, tmp_path, caplog):
    # Production 2026-10-24 (Antikythera): "Beat images: 0 candidates for 5 beats" and no way to tell a
    # licence-filtered search from a broken one. Now every beat says how many results, usable, relevant,
    # verified and kept.
    import logging
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=1, query="Antikythera mechanism fragment", keywords=["antikythera"])]))
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: ["r1", "r2", "r3"])
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter([]))   # all share-alike, excluded
    monkeypatch.setattr(beat_images.config, "BEAT_IMAGES_ALLOW_SHARE_ALIKE", False)
    with caplog.at_level(logging.DEBUG, logger="src.beat_images"):
        paths, credits = beat_images.source(BEATS, TOPIC, tmp_path)
    assert paths == [] and credits == []
    line = next(r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "Commons results" in r.getMessage())
    assert line == ("Beat 1 'Antikythera mechanism fragment': 3 Commons results, 0 usable (share-alike excluded), "
                    "0 relevant, 0 verified, 0 set aside by the look, 0 kept.")


def test_a_picture_that_does_not_show_the_subject_is_set_aside_after_a_look(monkeypatch, tmp_path, caplog):
    # Quality step 1, 2026-10-24: a photograph of the wrong temple passed the title and metadata checks; the
    # pixels are now looked at before a picture is kept. An unavailable look keeps the picture.
    import logging
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=1, query="Derinkuyu stone door", keywords=["derinkuyu"])]))   # candidates describe Derinkuyu
    monkeypatch.setattr(beat_images.commons, "search", lambda q, limit, max_wait: [q])
    monkeypatch.setattr(beat_images.commons, "candidates", lambda pages, kw, sa: iter([_cand("A"), _cand("B"), _cand("C"), _cand("D")]))
    monkeypatch.setattr(beat_images.commons, "download_jpeg",
                        lambda url, dest, max_wait=30: Image.new("RGB", (10, 10)).save(dest, "JPEG"))
    verdicts = iter([(False, "a different temple, at Pattadakal"), (True, "the monolith's courtyard")])   # A, then B; C and D unchecked
    monkeypatch.setattr(beat_images, "picture_shows", lambda path, query: next(verdicts, (True, "check unavailable")))
    with caplog.at_level(logging.DEBUG, logger="src.beat_images"):
        paths, credits = beat_images.source(BEATS, TOPIC, tmp_path)
    assert [c["title"] for c in credits] == ["B", "C", "D"]            # A was set aside; the slot was reused
    assert credits[0]["looked"] == "the monolith's courtyard" and credits[1]["looked"] == "check unavailable"
    assert not (tmp_path / "beat01_4.jpg").exists() and all(p.exists() for p in paths)
    line = next(r.getMessage() for r in caplog.records if "set aside by the look" in r.getMessage() and "Commons results" in r.getMessage())
    assert line.endswith("4 relevant, 4 verified, 1 set aside by the look, 3 kept.")
    set_aside = [r.getMessage() for r in caplog.records if "set aside, it does not show" in r.getMessage()]
    assert set_aside == ["Beat 1: 'A' set aside, it does not show the subject (a different temple, at Pattadakal)."]


def test_the_look_keeps_the_picture_when_it_cannot_run(monkeypatch, tmp_path):
    from src import config
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr("src.free_llm.claude_engine", lambda: False)
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    p = tmp_path / "x.jpg"
    Image.new("RGB", (10, 10)).save(p, "JPEG")
    assert beat_images.picture_shows(p, "a query") == (True, "check unavailable")
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: beat_images.PictureVerdict(shows_subject=False, reason="a gift shop"))
    assert beat_images.picture_shows(p, "a query") == (False, "a gift shop")
    # 2026-10-25: a satellite image with its own caption strip reached a carousel slide, cut off by the crop
    monkeypatch.setattr("src.free_llm.structured", lambda *a, **k: beat_images.PictureVerdict(shows_subject=True, burned_in_text=True, reason="a scale bar and a caption"))
    assert beat_images.picture_shows(p, "a query") == (False, "burned-in text: a scale bar and a caption")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", False)
    assert beat_images.picture_shows(p, "a query") == (True, "not checked")


def test_topic_photos_with_printed_text_are_set_aside_once_and_cached(monkeypatch, tmp_path):
    # The Tunguska development run (2026-10-25): its captions sat over a museum placard's text.
    from src import config
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    photos = []
    for i in range(6):
        p = tmp_path / f"{i:02d}_commons.jpg"
        Image.new("RGB", (40, 40), (i * 30, 20, 20)).save(p, "JPEG")
        photos.append(p)
    looks = []

    def look(path, query):
        looks.append(path.name)
        return {"checked": True, "shows": True, "burned_in_text": path.name.startswith("03"), "reason": "a placard"}
    monkeypatch.setattr(beat_images, "picture_look", look)
    kept = beat_images.without_burned_in_text(photos, "Tunguska", minimum=4)
    assert [p.name for p in kept] == [p.name for p in photos if not p.name.startswith("03")] and len(looks) == 6
    looks.clear()
    beat_images.without_burned_in_text(photos, "Tunguska", minimum=4)
    assert looks == []                                                                # cached by hash
    assert beat_images.without_burned_in_text(photos, "Tunguska", minimum=6) == photos  # never below the minimum
