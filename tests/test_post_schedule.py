from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import main as main_module
from src import post_schedule as ps

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_reel_times_alternate_daily():
    days = [date(2026, 10, 12) + timedelta(days=i) for i in range(4)]
    hours = [ps.reel_time(d).hour for d in days]
    assert sorted(set(hours)) == [12, 19] and hours[0] != hours[1] and hours[0] == hours[2]


def test_next_reel_slot_skips_passed_and_booked_days():
    day = date(2026, 10, 12)
    slot_today = ps.reel_time(day)
    assert ps.next_reel_slot(slot_today - timedelta(hours=2)) == slot_today
    # Within 10 minutes of the slot, or after it: tomorrow.
    assert ps.next_reel_slot(slot_today - timedelta(minutes=5)).date() == day + timedelta(days=1)
    # Today already booked: tomorrow.
    assert ps.next_reel_slot(slot_today - timedelta(hours=2), {day}).date() == day + timedelta(days=1)


def test_story_follows_reel():
    slot = et(2026, 10, 12, 12)
    assert ps.story_time(slot) == et(2026, 10, 12, 12, 45)


def test_carousel_only_tue_to_thu_at_the_other_reel_time():
    for i in range(7):
        d = date(2026, 10, 12) + timedelta(days=i)  # Mon 12 Oct .. Sun 18 Oct
        slot = ps.carousel_time(d)
        if d.weekday() in (1, 2, 3):
            assert slot is not None and slot.hour != ps.reel_time(d).hour
        else:
            assert slot is None
    assert ps.next_carousel_slot(et(2026, 10, 12, 9)).date() == date(2026, 10, 13)


# ------------------------------------------------------------ coordinator flow
@pytest.fixture
def coord(db, tmp_path, curated_payload, monkeypatch):
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    from main import PipelineCoordinator
    c = PipelineCoordinator(db, schedule_posts=True)
    run_dir = tmp_path / "r1"
    (run_dir / "stories").mkdir(parents=True)
    (run_dir / "carousel").mkdir()
    for n in ("story_01_hook.jpg", "story_02_reveal.jpg"):
        (run_dir / "stories" / n).write_bytes(b"x")
    for n in range(1, 4):
        (run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg").write_bytes(b"x")
    (run_dir / "m.mp4").write_bytes(b"0")
    curated_payload.update(topic_id="01_topic", human_reviewed=True)
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=run_dir / "m.mp4",
                  thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
    c.alerts = []
    c._alert = lambda text, buttons=None: c.alerts.append(text)
    return c


def test_approval_books_slots_instead_of_posting(coord, monkeypatch):
    monkeypatch.setattr(coord, "publish_run", lambda *a, **k: pytest.fail("posted immediately"))
    now = et(2026, 10, 13, 9, 30)  # Tuesday morning
    res = coord.schedule_run("r1", now=now)
    posts = {p["kind"]: p for p in coord.db.list_scheduled_posts()}
    reel_at = datetime.fromisoformat(posts["reel"]["due_at"])
    assert res["status"] == "scheduled" and reel_at == ps.next_reel_slot(now)
    assert datetime.fromisoformat(posts["story"]["due_at"]) == reel_at + timedelta(minutes=45)
    assert len(posts["story"]["files"]) == 2 and posts["carousel"]["status"] == "waiting"
    assert coord.db.get_run("r1")["status"] == "scheduled"
    assert coord.db.get_topic_by_id("01_topic")["status"] == "scheduled"
    assert "SCHEDULED" in coord.alerts[-1].upper()


def test_cli_and_dry_runs_still_post_immediately(coord, monkeypatch):
    seen = []
    monkeypatch.setattr(coord, "publish_run", lambda run_id, dry_run=False, previous_status="queued": seen.append(dry_run) or {"status": "simulated"})
    assert coord._after_approval("r1", dry_run=True)["status"] == "simulated"
    coord.schedule_posts = False
    coord._after_approval("r1")
    assert seen == [True, False]


def _published(coord, run_id="r1"):
    def publish_run(rid, dry_run=False, previous_status="queued"):
        coord.db.update_run(rid, "published")
        return {"status": "published"}
    return publish_run


def test_due_reel_then_story_post_in_order(coord, monkeypatch):
    now = et(2026, 10, 13, 9, 30)
    coord.schedule_run("r1", now=now)
    reel_at = ps.next_reel_slot(now)
    monkeypatch.setattr(coord, "publish_run", _published(coord))
    photos = []
    monkeypatch.setattr(coord.publisher, "publish_photos",
                        lambda run_id, tid, kind, files, caption="": photos.append((kind, len(files))) or {"status": "published"})

    coord.post_due(now=reel_at - timedelta(minutes=1))
    assert coord.db.get_run("r1")["status"] == "scheduled"  # not yet
    coord.post_due(now=reel_at + timedelta(minutes=1))
    assert coord.db.get_run("r1")["status"] == "published" and photos == []
    coord.post_due(now=reel_at + timedelta(minutes=46))
    assert photos == [("story", 2)]
    assert {p["kind"] for p in coord.db.list_scheduled_posts()} == {"carousel"}


def test_failed_reel_cancels_its_story_and_carousel(coord, monkeypatch):
    now = et(2026, 10, 13, 9, 30)
    coord.schedule_run("r1", now=now)
    monkeypatch.setattr(coord, "publish_run", lambda *a, **k: {"status": "failed", "publish_result": {"error": "x"}})
    coord.post_due(now=ps.next_reel_slot(now) + timedelta(minutes=1))
    assert coord.db.list_scheduled_posts() == []


def test_missed_reel_slot_moves_to_next_free_slot(coord, monkeypatch):
    now = et(2026, 10, 13, 9, 30)
    coord.schedule_run("r1", now=now)
    first = ps.next_reel_slot(now)
    monkeypatch.setattr(coord, "publish_run", lambda *a, **k: pytest.fail("posted at an odd hour"))
    late = first + timedelta(hours=5)
    coord.post_due(now=late)
    posts = {p["kind"]: p for p in coord.db.list_scheduled_posts()}
    new = datetime.fromisoformat(posts["reel"]["due_at"])
    assert new == ps.next_reel_slot(late) and new > late
    assert datetime.fromisoformat(posts["story"]["due_at"]) == new + timedelta(minutes=45)
    assert "Missed" in coord.alerts[-1]


def test_carousel_posts_newest_published_once_on_its_day(coord, monkeypatch):
    coord.schedule_run("r1", now=et(2026, 10, 12, 9, 30))
    coord.db.update_run("r1", "published")
    reel_posted = et(2026, 10, 12, 19, 1)
    for p in coord.db.list_scheduled_posts(("pending",)):
        coord.db.update_scheduled_post(p["id"], "published", detail={"posted_at": reel_posted.isoformat()})
    posted = []
    monkeypatch.setattr(coord.publisher, "publish_photos",
                        lambda run_id, tid, kind, files, caption="": posted.append((kind, len(files), bool(caption))) or {"status": "published"})
    thu = date(2026, 10, 15)  # >48h after the Reel
    slot = ps.carousel_time(thu)
    coord.post_due(now=slot - timedelta(minutes=1))
    assert posted == []
    coord.post_due(now=slot + timedelta(minutes=1))
    coord.post_due(now=slot + timedelta(minutes=2))
    assert posted == [("carousel", 3, True)]
    assert coord.db.list_scheduled_posts() == []


def test_carousel_from_unposted_reel_waits(coord, monkeypatch):
    coord.schedule_run("r1", now=et(2026, 10, 12, 9, 30))
    for p in coord.db.list_scheduled_posts(("pending",)):  # leave only the carousel; the Reel hasn't posted
        coord.db.update_scheduled_post(p["id"], "cancelled")
    monkeypatch.setattr(coord.publisher, "publish_photos", lambda *a, **k: pytest.fail("carousel before its Reel"))
    slot = ps.carousel_time(date(2026, 10, 13))
    coord.post_due(now=slot + timedelta(minutes=1))
    assert any(p["kind"] == "carousel" for p in coord.db.list_scheduled_posts(("waiting",)))


def test_status_lists_upcoming_posts(coord):
    from src.control import status_text
    coord.schedule_run("r1", now=et(2026, 10, 13, 9, 30))
    text = status_text(coord.db, None)
    assert "Upcoming posts:" in text and "Reel" in text and "next carousel slot" in text


def test_carousel_waits_48h_after_its_reel(coord, monkeypatch):
    coord.schedule_run("r1", now=et(2026, 10, 12, 9, 30))
    coord.db.update_run("r1", "published")
    tue_slot = ps.carousel_time(date(2026, 10, 13))
    for p in coord.db.list_scheduled_posts(("pending",)):  # Reel went out 6h before Tuesday's carousel slot
        coord.db.update_scheduled_post(p["id"], "published", detail={"posted_at": (tue_slot - timedelta(hours=6)).isoformat()})
    posted = []
    monkeypatch.setattr(coord.publisher, "publish_photos", lambda *a, **k: posted.append(a[2]) or {"status": "published"})
    coord.post_due(now=tue_slot + timedelta(minutes=1))
    assert posted == []  # too soon: Upload-Post flags similar content within 48h
    thu_slot = ps.carousel_time(date(2026, 10, 15))
    coord.post_due(now=thu_slot + timedelta(minutes=1))
    assert posted == ["carousel"]


def test_only_one_process_posts_at_a_time(coord, monkeypatch):
    # The daemon and the bot both post due items; a pass already running elsewhere must not be
    # doubled (the loser would see "not published" and cancel the run's Stories).
    from src.publication_lock import exclusive
    now = et(2026, 10, 13, 9, 30)
    coord.schedule_run("r1", now=now)
    monkeypatch.setattr(coord, "publish_run", lambda *a, **k: pytest.fail("posted while another process was posting"))
    with exclusive(coord.db.db_path, main_module.SCHEDULED_POSTING_LOCK) as mine:
        assert mine
        coord.post_due(now=ps.next_reel_slot(now) + timedelta(minutes=1))
    assert {p["kind"] for p in coord.db.list_scheduled_posts()} == {"reel", "story", "carousel"}
