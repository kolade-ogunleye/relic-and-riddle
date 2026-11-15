"""QF-65d: an older run's Stories and carousels (stills with no record) are verified against the run's provenance.json
before they are held off Facebook.

QF-65b holds a record-less still off Facebook (no AI label exists there for photo posts) unless its run's video traces
wholly to real media. Older runs' intermediate clips (parallax/, motion/) are often gone, so their video could not be
traced and real, credited photo posts were held. The run's provenance ledger records every composited clip with its
sha256, the pictures each was made from with theirs, and the photo's licence; the composition manifest's checksum, also
recorded there, binds the manifest's scene checksums to it. A scene that is gone is verified from it, bound by sha256
at every link. AI media (an AI motion clip, an AI illustration, the picture check's illustrated beat) and anything
the ledger cannot bind (no ledger, another manifest, other bytes, no photo left to check, an incomplete licence)
keep the post off Facebook, with the reason. Fixtures are built by the real lineage and ledger writers
(src/lineage.record_derived, src/credits.generate_provenance_ledger); no network: requests are recorded."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src import ai_label, config, publisher
from src.credits import generate_provenance_ledger
from src.lineage import record_derived, record_graphic
from tests.test_qf65a_ai_label import FAKE_JPEG, _capture
from tests.test_qf65b_ai_label_follow_up import _bare_stills, _platforms, _publish


@pytest.fixture
def run_db(db):
    db.create_run("run_x", "01_topic")
    return db


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _credit(**over) -> dict:
    return {"file": "01_commons.jpg", "title": "File:Stone hall.jpg", "artist": "A. Photographer",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0",
            "source_url": "https://commons.wikimedia.org/wiki/File:Stone_hall.jpg", "attribution_required": True,
            **over}


def _older_run(tmp_path: Path, monkeypatch, extra: str = "", credit: dict | None = None,
               gone: bool = True) -> tuple[Path, Path]:
    """An older run as the pipeline left it: a master whose scenes are a parallax clip of a credited Commons photo
    and our own map graphic (plus `extra`: "motion", an AI motion clip of the photo; "illustration", a parallax clip
    of an AI illustration), its composition manifest and provenance ledger written by the real writers, then its
    intermediate clips deleted (parallax/, motion/), and stills with no record. Returns (run, photo)."""
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    pool = tmp_path / "assets" / "topic"
    pool.mkdir(parents=True)
    photo = pool / "01_commons.jpg"
    photo.write_bytes(FAKE_JPEG + b"a stone hall")
    (pool / "credits.json").write_text(json.dumps({"images": [credit or _credit()]}), encoding="utf-8")
    run = (tmp_path / "output" / "run_x").resolve()
    (run / "parallax").mkdir(parents=True)
    (run / "graphics").mkdir()
    clip = run / "parallax" / "scene_01.mp4"
    clip.write_bytes(b"parallax clip of the hall")
    record_derived(clip, [photo], "parallax", {"preset": "push"})
    generator = tmp_path / "graphics_generator.py"
    generator.write_text("# our own map\n", encoding="utf-8")
    graphic = run / "graphics" / "graphic_map.mp4"
    graphic.write_bytes(b"our own map")
    record_graphic(graphic, generator, {"kind": "map"})
    scenes = [clip, graphic]
    if extra == "motion":
        (run / "motion").mkdir()
        anchor = run / "motion" / "motion_01_anchor.jpg"
        anchor.write_bytes(FAKE_JPEG + b"anchor frame")
        record_derived(anchor, [photo], "motion_anchor", {})
        motion = run / "motion" / "motion_01.mp4"
        motion.write_bytes(b"ai motion clip")
        record_derived(motion, [anchor], "ai_motion", {"provider": "kling", "ai_generated": True})
        scenes.append(motion)
    if extra == "illustration":
        art = run / "beat_images" / "gap_gen_beat04.jpg"
        art.parent.mkdir()
        art.write_bytes(FAKE_JPEG + b"an illustration")
        drawn = run / "parallax" / "scene_04.mp4"
        drawn.write_bytes(b"parallax clip of the illustration")
        record_derived(drawn, [art], "parallax", {"preset": "drift"})
        scenes.append(drawn)
    master = run / "topic_master.mp4"
    master.write_bytes(b"the master video")
    master.with_suffix(".composition.json").write_text(json.dumps(
        {"master_sha256": _sha(master), "scenes": {str(s): _sha(s) for s in scenes}}), encoding="utf-8")
    generate_provenance_ledger(run, {"id": "topic"}, {}, master, media_items=scenes)
    if gone:   # local cleanup of an older run's intermediate clips
        for folder in ("parallax", "motion"):
            shutil.rmtree(run / folder, ignore_errors=True)
        if extra == "illustration":
            shutil.rmtree(run / "beat_images")
    return run, photo


def _ledger(run: Path) -> dict:
    return json.loads((run / "provenance.json").read_text(encoding="utf-8"))


def _hold(run: Path, kind: str = "story") -> dict:
    return ai_label.assess_photos(_bare_stills(run, kind))


@pytest.mark.parametrize("kind", ["story", "carousel"])
def test_an_older_run_whose_gone_clips_the_ledger_verifies_posts_to_facebook(tmp_path, run_db, monkeypatch, kind):
    """The fix: parallax/ is gone, but the ledger binds each gone clip to a credited photo still here, by sha256.
    The post goes to Facebook; Instagram keeps its label (the stills themselves have no record)."""
    run, _ = _older_run(tmp_path, monkeypatch)
    assert not (run / "parallax").exists()
    sent = _capture(monkeypatch)
    res = _publish(run_db, kind, _bare_stills(run, kind), platforms=["instagram", "facebook"])
    assert res["status"] == "published" and "held_platforms" not in res
    assert "facebook" in _platforms(sent)
    assert all(("is_ai_generated", "true") in s["data"] for s in sent if ("platform[]", "instagram") in s["data"])


def test_the_dry_run_plans_the_facebook_request_for_a_verified_older_run(tmp_path, run_db, monkeypatch):
    run, _ = _older_run(tmp_path, monkeypatch)
    res = publisher.MetaPublisher(db=run_db, publishers=[]).publish_photos(
        "run_x", "01_topic", "carousel", _bare_stills(run, "carousel"), caption="c", dry_run=True)
    assert res["status"] == "simulated" and "held_platforms" not in res
    rows = [json.loads(p["response_payload_json"]) for p in run_db.list_publications("run_x")]
    assert any("facebook" in r["platforms"] for r in rows)


def test_an_older_run_whose_video_has_an_ai_motion_clip_stays_held_and_says_so(tmp_path, run_db, monkeypatch):
    """QF-65b's rule holds: the ledger records an AI motion clip like a photo's (source_media, the photo's credit),
    so the verification never takes it for real; the operator is told it is AI motion, not merely missing."""
    run, _ = _older_run(tmp_path, monkeypatch, "motion")
    entry = next(a for a in _ledger(run)["assets"] if a["file"] == "motion_01.mp4")
    assert entry["origin"] == "source_media" and entry["license"] == "CC BY 4.0"   # the trap the check must see
    sent = _capture(monkeypatch)
    res = _publish(run_db, "story", _bare_stills(run, "story"), platforms=["instagram", "facebook"])
    why = res.get("held_platforms", {}).get("facebook", "")
    assert _platforms(sent) == ["instagram", "instagram"]
    assert "from a video showing AI media: AI motion clip (motion_01.mp4" in why


def test_with_no_video_record_the_hold_names_the_ai_media_the_ledger_lists(tmp_path, monkeypatch):
    """No manifest: nothing binds the ledger to the video, so the post stays held as before; the operator is told the
    AI motion clip its ledger lists (an older production run's case), not only that the record is gone."""
    run, _ = _older_run(tmp_path, monkeypatch, "motion")
    (run / "topic_master.composition.json").unlink()
    why = _hold(run)["facebook_reasons"][0]
    assert why == ("a still with no record (story_01.jpg) and no video record to judge it by; "
                   "its provenance.json lists AI motion clip (motion_01.mp4)")


def test_an_ai_illustration_moved_by_a_parallax_clip_stays_held_as_ai(tmp_path, monkeypatch):
    run, _ = _older_run(tmp_path, monkeypatch, "illustration")
    label = _hold(run)
    assert label["facebook_hold"]
    assert "showing AI media: AI illustration (gap_gen_beat04.jpg" in label["facebook_reasons"][0]


def test_an_illustrated_beat_in_the_picture_check_still_holds_a_verified_run(tmp_path, monkeypatch):
    run, _ = _older_run(tmp_path, monkeypatch)
    (run / "script_metadata.json").write_text(json.dumps({"picture_fit": {"generated": [{"beat": 3}]}}),
                                              encoding="utf-8")
    label = _hold(run)
    assert label["facebook_hold"] and "AI illustration (beat 3)" in label["facebook_reasons"][0]


def test_a_photo_replaced_since_the_run_is_not_taken_for_the_recorded_one(tmp_path, monkeypatch):
    """Hash-bound, not name-based: the pool's 01_commons.jpg now holds other bytes (a refetched pool reuses the
    number), so it is not the photo the clip was made from."""
    run, photo = _older_run(tmp_path, monkeypatch)
    photo.write_bytes(FAKE_JPEG + b"another photo under the same name")
    label = _hold(run)
    assert label["facebook_hold"]
    assert "01_commons.jpg here is not the recorded photo (checksum mismatch)" in label["facebook_reasons"][0]


def test_with_no_photo_left_to_check_the_post_stays_held(tmp_path, monkeypatch):
    run, photo = _older_run(tmp_path, monkeypatch)
    photo.unlink()
    label = _hold(run)
    assert label["facebook_hold"] and "is here to check" in label["facebook_reasons"][0]


@pytest.mark.parametrize("damage", ["no ledger", "unreadable", "another manifest", "other clip bytes",
                                    "no entry", "no source checksum"])
def test_a_ledger_that_cannot_bind_the_video_keeps_the_post_held(tmp_path, monkeypatch, damage):
    run, _ = _older_run(tmp_path, monkeypatch)
    ledger_file = run / "provenance.json"
    ledger = _ledger(run)
    manifest = run / "topic_master.composition.json"
    if damage == "no ledger":
        ledger_file.unlink()
    elif damage == "unreadable":
        ledger_file.write_text("{not json", encoding="utf-8")
    elif damage == "another manifest":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["note"] = "edited after the ledger"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    else:
        if damage == "other clip bytes":
            next(a for a in ledger["assets"] if a["file"] == "scene_01.mp4")["sha256"] = "0" * 64
        elif damage == "no entry":
            ledger["assets"] = [a for a in ledger["assets"] if a["file"] != "scene_01.mp4"]
        else:
            ledger["derived_sources"] = [d for d in ledger["derived_sources"] if not d["path"].endswith("01_commons.jpg")]
        ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
    label = _hold(run)
    why = label["facebook_reasons"][0]
    assert label["facebook_hold"]
    assert "scene_01.mp4 is missing, and provenance.json cannot verify it: " in why


@pytest.mark.parametrize("credit", [{"artist": ""}, {"source_url": ""}, {"license": "All rights reserved"}])
def test_a_photo_without_a_complete_licence_record_keeps_the_post_held(tmp_path, monkeypatch, credit):
    run, _ = _older_run(tmp_path, monkeypatch, credit=_credit(**credit))
    label = _hold(run)
    assert label["facebook_hold"] and "licence record" in label["facebook_reasons"][0]


def test_a_ledger_written_on_another_machine_is_read_under_this_tree(tmp_path, monkeypatch):
    """The production sample's ledgers record the VM's paths: a recorded path is found at the same place under this
    app tree, and only its checksum decides that it is the recorded file."""
    run, _ = _older_run(tmp_path, monkeypatch)
    here = str(tmp_path.resolve())

    def move(value):   # every recorded path under this tree, as the other machine wrote it
        if isinstance(value, dict):
            return {move(k): move(v) for k, v in value.items()}
        if isinstance(value, list):
            return [move(v) for v in value]
        if isinstance(value, str) and value.startswith(here):
            return "C:\\Elsewhere\\app" + value[len(here):].replace("/", "\\")
        return value
    manifest = run / "topic_master.composition.json"
    manifest.write_text(json.dumps(move(json.loads(manifest.read_text(encoding="utf-8")))), encoding="utf-8")
    ledger = move(_ledger(run))
    ledger["composition_manifest"]["sha256"] = _sha(manifest)
    (run / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    assert "C:\\Elsewhere\\app\\output\\run_x\\parallax\\scene_01.mp4" in json.loads(manifest.read_text())["scenes"]
    label = _hold(run)
    assert not label["facebook_hold"], label["facebook_reasons"]


def test_a_run_whose_clips_are_all_here_is_judged_by_its_own_records_as_before(tmp_path, monkeypatch):
    """QF-65b otherwise unchanged: with its clips here, a wholly real video releases the stills and the ledger is
    not needed (none is read: it is removed); an AI clip still holds them."""
    for extra in ("", "motion"):
        work = tmp_path / (extra or "real")
        run, _ = _older_run(work, monkeypatch, extra, gone=False)
        (run / "provenance.json").unlink()
        label = _hold(run)
        assert label["facebook_hold"] is bool(extra), label["facebook_reasons"]
        if extra:
            assert "showing AI media: AI motion clip (motion_01.mp4, kling)" in label["facebook_reasons"][0]
