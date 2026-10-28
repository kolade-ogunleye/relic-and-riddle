"""
tests/test_archive_film.py
==========================
Archive film (operator, 2026-10-28): taken only where the item's own record states an open licence, never from
broadcast captures, named in its title or subjects, held for the operator, and remembered once decided.
"""

import json
from pathlib import Path

import pytest

from src import archive_film, config
from src.telegram_commands import normalise_command


def _record(identifier, licence, collection="avgeeks", title="Pompeii And Vesuvius", subject="Pompeii", size=80_000_000):
    return {"metadata": {"identifier": identifier, "title": title, "licenseurl": licence, "collection": [collection, "ephemera"],
                         "year": "1950", "subject": [subject], "creator": "AV Geeks"},
            "files": [{"name": f"{identifier}.mp4", "size": str(size)}, {"name": f"{identifier}_512kb.mp4", "size": "9000000"},
                      {"name": f"{identifier}.ogv", "size": "5000000"}]}


@pytest.fixture
def archive(monkeypatch):
    records = {
        "PompeiiAndVesuvius": _record("PompeiiAndVesuvius", "http://creativecommons.org/publicdomain/mark/1.0/"),
        "NcFilm": _record("NcFilm", "http://creativecommons.org/licenses/by-nc/4.0/"),
        "TvCapture": _record("TvCapture", "http://creativecommons.org/publicdomain/mark/1.0/", collection="tvarchive"),
        "SpaceshipVenus": _record("SpaceshipVenus", "http://creativecommons.org/publicdomain/zero/1.0/",
                                  title="First Spaceship On Venus", subject="science fiction"),
        "HugeOnly": _record("HugeOnly", "http://creativecommons.org/publicdomain/mark/1.0/", size=900_000_000),
    }
    queries = []

    def get(url, params=None):
        if url == archive_film.SEARCH:
            queries.append(params["q"])
            if "NOT licenseurl" in params["q"]:  # the holders to ask
                return {"response": {"docs": [{"identifier": "FatherNile", "title": "Father Nile (Pompeii edition)", "year": "1931"}]}}
            return {"response": {"docs": [{"identifier": k} for k in records]}}
        return records.get(url.rsplit("/", 1)[-1])
    monkeypatch.setattr(archive_film, "_get", get)
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", False)
    monkeypatch.setattr(config, "ARCHIVE_FILM_MAX_MB", 250)
    return queries


def test_only_films_with_their_own_open_licence_and_named_subject_are_taken(archive):
    usable, ask = archive_film.archive_candidates("Pompeii", ["pompeii"])
    assert [c["id"] for c in usable] == ["PompeiiAndVesuvius", "HugeOnly"]   # NC, broadcast and off-subject are not
    assert usable[1]["download_url"].endswith("/HugeOnly_512kb.mp4")         # its full reel is over the cap: a smaller copy
    film = usable[0]
    assert film["license"] == "Public domain" and film["source_url"] == "https://archive.org/details/PompeiiAndVesuvius"
    assert film["download_url"].endswith("/PompeiiAndVesuvius.mp4") and film["community_upload"] is False
    assert "NOT collection:(tvarchive OR tvnews)" in archive[0] and "licenseurl:(" in archive[0]
    assert ask == [{"title": "Father Nile (Pompeii edition)", "year": "1931", "contact": "Prelinger Archives: prelingerclips@gmail.com",
                    "source_url": "https://archive.org/details/FatherNile"}]   # no licence: asked for, never taken


def test_found_films_wait_for_the_operator_then_are_used_or_dropped_for_good(archive, tmp_path, monkeypatch):
    from src import footage
    monkeypatch.setattr(archive_film, "europeana_candidates", lambda q, k: [])
    monkeypatch.setattr(footage, "download", lambda url, provider, dest, **k: dest.write_bytes(b"mp4") or dest)
    monkeypatch.setattr(footage, "probe", lambda p: {"duration": 505.7, "width": 640, "height": 480})
    monkeypatch.setattr(archive_film.subprocess, "run", lambda cmd, **k: Path(cmd[-1]).write_bytes(b"jpg"))
    topic = {"id": "pompeii", "title": "Pompeii"}
    found = archive_film.find(topic, {"queries": ["Pompeii"], "keywords": ["pompeii"]}, tmp_path)
    assert [r["id"] for r in found["saved"]] == ["PompeiiAndVesuvius", "HugeOnly"] and found["ask"]
    pending = archive_film.pending_list(tmp_path, "pompeii")
    assert len(pending) == 2 and pending[0][1]["license"] == "Public domain"
    assert archive_film.decide(tmp_path, "pompeii", [2], use=False) == ["Pompeii And Vesuvius"]   # HugeOnly, dropped
    assert len(archive_film.pending_list(tmp_path, "pompeii")) == 1
    assert not list((tmp_path / "pompeii" / "footage").glob("*.mp4"))          # nothing in use until approved
    card = archive_film.card(tmp_path, topic, found["ask"], key="7")
    assert "/footage 7 use 1 2" in card and "/footage N" not in card              # the topic's own number
    assert "Waiting for you" in card and "Pompeii And Vesuvius" in card and "prelingerclips@gmail.com" in card
    assert archive_film.decide(tmp_path, "pompeii", [1], use=True) == ["Pompeii And Vesuvius"]
    used = list((tmp_path / "pompeii" / "footage").glob("*.mp4"))
    record = json.loads(used[0].with_suffix(".json").read_text(encoding="utf-8"))
    assert record["provider"] == "archive.org" and "approved by the operator" in record["check_note"]
    assert archive_film.find(topic, {"queries": ["Pompeii"], "keywords": ["pompeii"]}, tmp_path)["saved"] == []   # never twice


def test_the_credit_names_the_archive():
    from src.footage import credit_line
    line = credit_line([{"provider": "archive.org", "artist": "AV Geeks", "license": "Public domain"}])
    assert line == "🎥 Footage: AV Geeks (Public domain) via the Internet Archive"


def test_the_commands_route_and_an_upload_caption_is_left_alone():
    assert normalise_command("footage", "4") == ("filmlist", "4")
    assert normalise_command("footage", "4 find") == ("filmfind", "4")
    assert normalise_command("footage", "4 use 1 2") == ("filmuse", "4 1 2")
    assert normalise_command("footage", "4 drop 3") == ("filmdrop", "4 3")
    assert normalise_command("footage", "4 | source: https://x | licence: CC BY 4.0")[0] == "footage"
    assert normalise_command("footage", "") == ("footage", "")


def test_downloads_stay_on_the_archives_hosts():
    from src.footage import _host_allowed
    assert _host_allowed("archive.org", "archive") and _host_allowed("ia800202.us.archive.org", "archive")
    assert not _host_allowed("archive.org.evil.com", "archive") and not _host_allowed("", "europeana")


def test_a_wide_shot_is_shown_whole_and_a_vertical_one_fills_the_frame():
    """Archive film is 4:3 at 640x480: filling the frame kept a 270 px strip blown up four times (2026-10-28)."""
    from src.footage import FILL, frame_filter
    assert frame_filter({"width": 640, "height": 480})[0] == "-filter_complex"
    assert "overlay" in frame_filter({"width": 1920, "height": 1080})[1] and "boxblur" in frame_filter({"width": 1920, "height": 1080})[1]
    assert frame_filter({"width": 1080, "height": 1920}) == ["-vf", FILL]
    assert frame_filter({"width": 0, "height": 0}) == ["-vf", FILL]
