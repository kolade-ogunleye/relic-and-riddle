"""
tests/test_photo_orientation.py
===============================
A photo stored turned over (fetched before the fetcher applied EXIF orientation) is put upright before a run
uses it, or left out (production's Antikythera 07_commons.jpg: 4000x3000 for a 3000x4000 original, 2026-10-27).
"""

import json

from PIL import Image

from src import commons, photo_orientation


def _topic(tmp_path, stored=(4000, 3000), recorded=(3000, 4000)):
    d = tmp_path / "antikythera_mechanism"
    d.mkdir()
    Image.new("RGB", stored, "gray").save(d / "07_commons.jpg")
    Image.new("RGB", (1200, 800), "gray").save(d / "01_commons.jpg")
    (d / "credits.json").write_text(json.dumps([
        {"file": "07_commons.jpg", "width": recorded[0], "height": recorded[1], "download_url": "https://up/07.jpg"},
        {"file": "01_commons.jpg", "width": 1200, "height": 800, "download_url": "https://up/01.jpg"},
    ]), encoding="utf-8")
    return d


def test_a_photo_turned_over_against_its_record_is_found(tmp_path):
    d = _topic(tmp_path)
    assert [i["file"] for i in photo_orientation.sideways(d)] == ["07_commons.jpg"]
    assert not photo_orientation.turned(d / "01_commons.jpg", 1200, 800)
    assert not photo_orientation.turned(d / "07_commons.jpg", 500, 500)   # a square original says nothing


def test_it_is_fetched_again_upright_and_the_old_file_kept(tmp_path, monkeypatch):
    d = _topic(tmp_path)
    fetched = []
    monkeypatch.setattr(commons, "download_jpeg",
                        lambda url, dest, **k: fetched.append(url) or Image.new("RGB", (3000, 4000), "gray").save(dest))
    done, still = photo_orientation.put_upright(d)
    assert fetched == ["https://up/07.jpg"] and still == set()
    assert Image.open(d / "07_commons.jpg").size == (3000, 4000)
    assert Image.open(d / photo_orientation.BACKUP_DIR / "07_commons.jpg").size == (4000, 3000)
    assert "fetched again upright" in done[0]
    assert photo_orientation.put_upright(d) == ([], set())                 # nothing left to do


def test_one_that_cannot_be_fetched_again_is_left_out_not_used_sideways(tmp_path, monkeypatch):
    d = _topic(tmp_path)

    def offline(url, dest, **k):
        raise OSError("offline")
    monkeypatch.setattr(commons, "download_jpeg", offline)
    done, still = photo_orientation.put_upright(d)
    assert still == {"07_commons.jpg"} and "this run leaves it out" in done[0]
    assert Image.open(d / "07_commons.jpg").size == (4000, 3000)            # untouched
    assert not (d / "07_commons.upright.jpg").exists()


def test_the_run_leaves_a_still_turned_photo_out(tmp_path, monkeypatch):
    import main
    from types import SimpleNamespace
    d = _topic(tmp_path)
    monkeypatch.setattr(commons, "download_jpeg", lambda url, dest, **k: (_ for _ in ()).throw(OSError("offline")))
    alerts = []
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.visual_engine = SimpleNamespace(assets_dir=tmp_path)
    c._alert = alerts.append
    assert c._upright_photos({"id": "antikythera_mechanism", "title": "Antikythera"}) == {"07_commons.jpg"}
    assert "Photos turned over" in alerts[0]
