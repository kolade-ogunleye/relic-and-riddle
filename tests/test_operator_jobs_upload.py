import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from src.image_upload import ImageUpload
from src.operator_jobs import Jobs, checkpoint


def test_cancel_queued_job_never_starts_work(db):
    queued, completed, calls = [], [], []
    jobs = Jobs(db)
    key = jobs.submit(SimpleNamespace(submit=queued.append), "Fetch", lambda: calls.append(1), lambda j, r: completed.append(j.copy()))
    assert jobs.cancel(key)
    queued.pop()()
    assert calls == [] and completed[0]["state"] == "cancelled"
    assert jobs.recent()[0]["state"] == "cancelled"


def test_cancel_inflight_job_stops_next_stage_and_rejects_duplicate(db):
    entered, release = threading.Event(), threading.Event()
    completed, stages = [], []
    jobs = Jobs(db)
    def work():
        stages.append("request already sent")
        entered.set()
        assert release.wait(5)
        checkpoint()
        stages.append("next request")
    with ThreadPoolExecutor(1) as pool:
        key = jobs.submit(pool, "Fetch", work, lambda j, r: completed.append(j.copy()), resource="one")
        assert entered.wait(5)
        with pytest.raises(ValueError, match="already running"):
            jobs.submit(pool, "Duplicate", work, lambda *_: None, resource="one")
        jobs.cancel(key)
        release.set()
    assert stages == ["request already sent"]
    assert completed[0]["state"] == "cancelled"


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (40, 40)).save(stream, "PNG")
    return stream.getvalue()


def test_upload_survives_recreation_isolated_and_requires_review(db, tmp_path, monkeypatch):
    from src import telegram_images
    monkeypatch.setattr(telegram_images, "write_contact_sheet", lambda *a: None)
    wizard = ImageUpload(db, tmp_path / "staging", tmp_path / "assets")
    state = wizard.begin(555, 111, "1", 42)
    assert wizard.load(555, 222) is None
    wizard.file(555, 111, image_bytes(), state["id"])
    wizard = ImageUpload(db, tmp_path / "staging", tmp_path / "assets")
    assert wizard.load(555, 111)["stage"] == "source"
    with pytest.raises(ValueError, match="URL"):
        wizard.answer(555, 111, "not a source")
    wizard.answer(555, 111, "https://example.org/source")
    with pytest.raises(ValueError, match="licences"):
        wizard.answer(555, 111, "All rights reserved")
    wizard.answer(555, 111, "CC BY 4.0")
    wizard.answer(555, 111, "https://creativecommons.org/licenses/by/4.0/")
    result = wizard.answer(555, 111, "Photographer")
    assert result["stage"] == "complete"
    credits = json.loads((tmp_path / "assets" / result["topic"] / "credits.json").read_text())
    assert not credits["reviewed"] and credits["images"][0]["attribution_required"]
    assert not list((tmp_path / "staging").iterdir())
    assert wizard.load(555, 111) is None


def test_cancel_upload_cannot_be_resurrected_by_late_download(db, tmp_path):
    wizard = ImageUpload(db, tmp_path / "staging", tmp_path / "assets")
    old = wizard.begin(1, 2, "1", 42)
    wizard.cancel(1, 2)
    new = wizard.begin(1, 2, "2", 43)
    assert wizard.file(1, 2, image_bytes(), old["id"]) is None
    assert wizard.load(1, 2)["id"] == new["id"]
    wizard.file(1, 2, image_bytes(), new["id"])
    assert wizard.file(1, 2, image_bytes(), new["id"]) is None
    with pytest.raises(ValueError, match="stale"):
        wizard.cancel(1, 2, expected_id=old["id"])
    with pytest.raises(ValueError, match="stale"):
        wizard.answer(1, 2, "CC0", expected_id=new["id"], expected_stage="licence")
    assert wizard.load(1, 2)["stage"] == "source"


def test_run_cancel_rejects_upload_and_acknowledges_at_boundary(db, monkeypatch):
    from main import PipelineCoordinator
    from src.operator_jobs import JobCancelled
    db.create_run("run", "01_topic")
    db.update_run("run", "publishing")
    assert not db.request_run_cancel("run")
    assert not db.request_run_cancel("missing")
    db.update_run("run", "producing")
    assert db.request_run_cancel("run")
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    statuses, progress = [], []
    coordinator.topic_engine = SimpleNamespace(record_status=lambda *a, **kw: statuses.append(a))
    coordinator._finish_progress = lambda *a: progress.append(a)
    with pytest.raises(JobCancelled):
        coordinator._check_cancel("run")
    assert db.get_run("run")["status"] == "cancelled"
    assert statuses[0][1] == "cancelled" and progress


def test_public_menu_unifies_media_and_keeps_clear():
    from src.telegram_commands import menu
    names = [c["command"] for c in menu()]
    assert len(names) == len(set(names))
    assert {"clear", "images", "music", "jobs"} <= set(names)
    assert "fetchimages" not in names and "image" not in names


def test_cancelled_progress_receipt_does_not_report_failure(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    db.create_run("cancelled", "01_topic")
    db.update_run("cancelled", "cancelled")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    edits = []
    monkeypatch.setattr(bot, "edit_message_text", lambda *args, **kwargs: edits.append(args) or True)
    bot.finish_progress_card(42, "Topic", "cancelled", success=False, error="Cancelled by operator")
    assert "Render cancelled" in edits[0][1] and "Failed" not in edits[0][1]


def test_music_download_merges_intervening_approval_and_filters_rights(tmp_path, monkeypatch):
    from scripts import fetch_music
    from src import music
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "LIBRARY", tmp_path / "library.json")
    monkeypatch.setattr(fetch_music, "MUSIC_DIR", tmp_path)
    old = {"file": "old.mp3", "title": "Old", "source_url": "https://example.org/old", "reviewed": False,
           "mood": "epic", "license": "cc0", "uses": 7}
    (tmp_path / "old.mp3").write_bytes(b"ID3old")
    music.save_library([old])
    candidate = {"title": "Ancient strings", "id": "123456789", "duration": 120000, "filetype": "mp3",
                 "license": "by", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                 "foreign_landing_url": "https://example.org/new", "url": "https://example.org/new.mp3"}
    def search(_):
        # Operator approval happens after the fetcher's initial library snapshot.
        music.approve_track(music.track_key(old))
        return [dict(candidate, license="by-nc"), candidate]
    monkeypatch.setattr(fetch_music, "search", search)
    class Audio:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_content(self, _): return [b"ID3new"]
    monkeypatch.setattr(fetch_music.requests, "get", lambda *a, **kw: Audio())
    monkeypatch.setattr(fetch_music.time, "sleep", lambda _: None)
    assert fetch_music.fetch_music_tracks(mood="epic", add_count=1) == 1
    tracks = music.load_library()
    assert tracks[0]["reviewed"] and tracks[0]["uses"] == 7
    assert len(tracks) == 2 and not tracks[1]["reviewed"] and tracks[1]["license"] == "by"


def test_queue_page_buttons_do_not_omit_topics(db, tmp_path):
    from src.control import make_command_handler
    # Use the fixture's real topic bank; extend it through its supported sync API.
    topics = [{"id": f"page_{i:02d}", "title": f"Page title {i}", "key_facts": ["Fact"]} for i in range(14)]
    db.topic_bank_file.write_text(json.dumps({"topics": topics}))
    db._seed_topics_from_json()
    handle = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    text, buttons = handle("qpage", "0")
    assert any(b["callback_data"] == "qpage:1" for b in buttons)
    second, buttons = handle("qpage", "1")
    assert second != text and "page 2/" in second
    assert any(b["callback_data"] == "qpage:0" for b in buttons)
