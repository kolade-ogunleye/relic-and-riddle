"""
tests/test_carousel_window_2026_11_03.py
========================================
A carousel's 7-day window counts from when its Reel posts (or from the Reel's slot while it hasn't), not from when
the run was booked (operator, 2026-11-03). Counted from the booking, a run approved between about Mon 12:00 and
Tue 18:45 Eastern had a carousel that could never post and expired silently, while the approval said it would go out
in "the next carousel slot". One rule (src/post_schedule.py: carousel_reel, carousel_may_take, carousel_deadline) now
serves the daemon (main._post_carousel_if_due), Posting and Slots (posting_view.carousel_plan) and the messages
(main._book_followers, main.post_now), so all of them name the same slot. And every carousel gets its slot: it
expires 7 days after its Reel or at its first carousel slot 48 h after the Reel, whichever is later, so a Reel posted
off-slot (Post now, a retry) is covered too (Thu 12:00 to the next Tue 19:00 is 5 d 7 h, longer than 7 days less 48 h).

Every test here but the last freezes the clock: the booking's created_at (src.db), the coordinator, the plan and
the slot rules all read it. The last reads the wall clock, for the 168-hour clock plugin to move.
"""
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import post_schedule as ps

ET = ZoneInfo("America/New_York")
# Three weeks, Monday 00:00 Eastern: the Reel and carousel hours alternate by date, so consecutive weeks are in
# opposite 14-day phases; the third spans the end of daylight saving time (Sun 1 Nov 2026).
WEEKS = (datetime(2026, 11, 2, tzinfo=ET), datetime(2026, 11, 9, tzinfo=ET), datetime(2026, 10, 26, tzinfo=ET))
HOURS = [(w, h) for w in WEEKS for h in range(7 * 24)]


class _Clock:
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.astimezone(tz) if tz else _Clock.now.astimezone().replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    import main
    from src import db as db_module
    from src import posting_view
    for module in (db_module, posting_view, ps, main):
        monkeypatch.setattr(module, "datetime", _Frozen)

    def at(moment: datetime) -> datetime:
        _Clock.now = moment.astimezone(timezone.utc)
        return moment
    return at


@pytest.fixture
def coord(db, tmp_path, curated_payload, monkeypatch, clock):
    monkeypatch.setattr("src.release.require_release", lambda *a, **k: {"status": "pass"})
    from main import PipelineCoordinator
    c = PipelineCoordinator(db, schedule_posts=True)
    run_dir = tmp_path / "r1"
    (run_dir / "stories").mkdir(parents=True)
    (run_dir / "carousel").mkdir()
    (run_dir / "stories" / "story_01_hook.jpg").write_bytes(b"x")
    for n in range(1, 4):
        (run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg").write_bytes(b"x")
    (run_dir / "m.mp4").write_bytes(b"0")
    curated_payload.update(topic_id="01_topic", human_reviewed=True)
    db.create_run("r1", "01_topic")
    db.update_run("r1", "pending_approval", script_payload=curated_payload, master_video_path=run_dir / "m.mp4",
                  thumbnail_cover_path=run_dir / "m.mp4", thumbnail_grid_path=run_dir / "m.mp4")
    c.alerts = []
    c._alert = lambda text, buttons=None: c.alerts.append(text)
    c.telegram_bot.stamp_review_outcome = lambda run_id, text: c.alerts.append(text) or True
    c.posted = []
    monkeypatch.setattr(c.publisher, "publish_photos",
                        lambda run_id, tid, kind, files, caption="", **k: c.posted.append((kind, _Clock.now)) or {"status": "published"})
    return c


def _posts(coord, kind):
    statuses = ("pending", "waiting", "published", "expired", "cancelled", "failed", "unknown")
    return [p for p in coord.db.list_scheduled_posts(statuses) if p["kind"] == kind]


def _reel_posts(coord, when):
    """The daemon posted the Reel at `when` (as _post_due records it)."""
    reel = _posts(coord, "reel")[0]
    coord.db.update_scheduled_post(reel["id"], "published", detail={"posted_at": when.astimezone(timezone.utc).isoformat()})
    coord.db.update_run("r1", "published")


def _carousel_slots(start, end):
    day, out = start.astimezone(ET).date(), []
    while day <= end.astimezone(ET).date():
        slot = ps.carousel_time(day)
        if slot and start <= slot <= end:
            out.append(slot)
        day += timedelta(days=1)
    return out


@pytest.mark.parametrize("week,hour", HOURS, ids=[f"{w:%d%b}-{(w + timedelta(hours=h)):%a%H}" for w, h in HOURS])
def test_every_booking_hour_gets_a_carousel_slot_that_the_confirmation_names(coord, clock, week, hour):
    from src.posting_view import carousel_plan
    now = clock(week + timedelta(hours=hour))
    coord.schedule_run("r1", now=now)
    reel_at = datetime.fromisoformat(_posts(coord, "reel")[0]["due_at"])
    carousel = _posts(coord, "carousel")[0]

    plan = carousel_plan(coord.db, now)
    assert carousel["id"] in plan, f"booked {now:%a %H:%M %Z}: the carousel has no slot"
    slot = plan[carousel["id"]]
    assert slot - reel_at >= timedelta(hours=48)                       # Upload-Post's 48 h gap
    assert slot - reel_at <= timedelta(days=7)                         # inside its window
    assert slot.astimezone(ET).strftime("%a") in ("Tue", "Wed", "Thu")
    assert slot == ps.carousel_time(slot.astimezone(ET).date())        # a real carousel slot
    # The confirmation names that same slot (it named the next carousel slot, gap or not)
    assert html.escape(f"🖼 Carousel: {ps.fmt(slot)} ") in coord.alerts[-1]

    # The daemon agrees: the Reel posts in its slot, and the carousel goes out in that slot and no earlier one
    clock(reel_at + timedelta(minutes=1))
    _reel_posts(coord, reel_at + timedelta(minutes=1))
    for candidate in _carousel_slots(reel_at, slot):
        clock(candidate + timedelta(minutes=1))
        coord._post_carousel_if_due(candidate + timedelta(minutes=1))
        assert coord.posted == ([("carousel", candidate + timedelta(minutes=1))] if candidate == slot else [])
    assert _posts(coord, "carousel")[0]["status"] == "published"


def test_the_monday_afternoon_booking_that_used_to_expire_now_posts(coord, clock):
    """Booked Mon 2 Nov 13:00 EST: the Reel's slot is Tue 3 Nov 19:00, Thu 5 Nov 12:00 is 41 h after it (too soon)
    and Tue 10 Nov 19:00 is 7 days after it, but 7 days 6 h after the booking: counted from the booking, the carousel
    had no slot and expired unposted."""
    from src.posting_view import carousel_plan
    now = clock(datetime(2026, 11, 2, 13, 0, tzinfo=ET))
    coord.schedule_run("r1", now=now)
    reel_at = datetime.fromisoformat(_posts(coord, "reel")[0]["due_at"])
    slot = carousel_plan(coord.db, now)[_posts(coord, "carousel")[0]["id"]]
    assert slot - now > timedelta(days=7)          # outside the old booking-time window
    assert timedelta(hours=48) <= slot - reel_at <= timedelta(days=7)
    assert html.escape(ps.fmt(slot)) in coord.alerts[-1]


def test_a_reel_that_never_posts_holds_its_carousel_then_it_expires_a_week_after_the_reels_slot(coord, clock):
    from src.posting_view import carousel_plan
    now = clock(datetime(2026, 11, 2, 9, 0, tzinfo=ET))
    coord.schedule_run("r1", now=now)
    reel = _posts(coord, "reel")[0]
    reel_at = datetime.fromisoformat(reel["due_at"])
    coord.db.update_scheduled_post(reel["id"], "cancelled")     # the Reel's slot is gone; the carousel stays waiting
    carousel = _posts(coord, "carousel")[0]
    assert ps.carousel_reel(coord.db, carousel) == ("waiting", reel_at)
    assert carousel_plan(coord.db, now) == {}                   # no slot is promised for it
    for slot in _carousel_slots(reel_at, reel_at + timedelta(days=14)):
        clock(slot + timedelta(minutes=1))
        coord._post_carousel_if_due(slot + timedelta(minutes=1))
        expected = "expired" if slot > reel_at + timedelta(days=7) else "waiting"
        assert _posts(coord, "carousel")[0]["status"] == expected, slot
    assert coord.posted == []


def test_a_moved_reel_moves_its_carousels_window(coord, clock):
    """A Reel slot missed by over 3 h moves to the next free one (main._reschedule): the window moves with it."""
    from src.posting_view import carousel_plan
    now = clock(datetime(2026, 11, 2, 9, 0, tzinfo=ET))
    coord.schedule_run("r1", now=now)
    reel = _posts(coord, "reel")[0]
    moved = datetime.fromisoformat(reel["due_at"]) + timedelta(days=3)
    coord.db.update_scheduled_post(reel["id"], "pending", due_at=moved.astimezone(timezone.utc).isoformat())
    slot = carousel_plan(coord.db, now)[_posts(coord, "carousel")[0]["id"]]
    assert timedelta(hours=48) <= slot - moved <= timedelta(days=7)


def test_a_carousel_whose_reel_posted_over_a_week_ago_expires(coord, clock):
    now = clock(datetime(2026, 11, 2, 9, 0, tzinfo=ET))
    coord.schedule_run("r1", now=now)
    _reel_posts(coord, datetime(2026, 11, 2, 12, 1, tzinfo=ET))
    late = ps.carousel_time(datetime(2026, 11, 10, tzinfo=ET).date())    # Tue 10 Nov: 8 days after the Reel
    clock(late + timedelta(minutes=1))
    coord._post_carousel_if_due(late + timedelta(minutes=1))
    assert _posts(coord, "carousel")[0]["status"] == "expired" and coord.posted == []


def test_the_confirmation_says_so_when_a_carousel_has_no_slot_left(coord, clock):
    """Stories and carousel booked again (📱) for a Reel posted Mon 2 Nov 19:00, on Fri 6 Nov: its window ends
    Mon 9 Nov 19:00 and no Tue-Thu slot is left in it. The line says so instead of naming a slot it can't take."""
    from src.posting_view import carousel_plan
    clock(datetime(2026, 11, 2, 9, 0, tzinfo=ET))
    coord.schedule_run("r1", now=datetime(2026, 11, 2, 9, 0, tzinfo=ET))
    _reel_posts(coord, datetime(2026, 11, 2, 19, 0, tzinfo=ET))
    for p in _posts(coord, "story") + _posts(coord, "carousel"):
        coord.db.update_scheduled_post(p["id"], "cancelled", detail={"reason": "the Reel did not post"})
    clock(datetime(2026, 11, 6, 12, 0, tzinfo=ET))
    text = coord.rebook_followers("r1")
    assert "Carousel: its last carousel slot (7 days after the Reel, or its first slot 48 h after it) has passed" in text
    assert carousel_plan(coord.db) == {}


def test_a_reel_posted_without_a_booking_counts_from_its_carousels_booking(coord, clock):
    """Post for real (promote_run) posts the Reel at once and books only its Stories and carousel: the carousel's
    own booking time stands for the Reel's, in the plan, the daemon and the confirmation alike."""
    from src.posting_view import carousel_plan
    posted = clock(datetime(2026, 11, 2, 15, 0, tzinfo=ET))
    coord.db.update_run("r1", "published")
    lines = coord._book_followers("r1", Path(coord.db.get_run("r1")["master_video_path"]).parent, posted)
    carousel = _posts(coord, "carousel")[0]
    assert ps.carousel_reel(coord.db, carousel)[0] == "posted"
    slot = carousel_plan(coord.db, posted)[carousel["id"]]
    assert slot - posted >= timedelta(hours=48) and f"Carousel: {ps.fmt(slot)} " in "\n".join(lines)
    clock(slot + timedelta(minutes=1))
    coord._post_carousel_if_due(slot + timedelta(minutes=1))
    assert coord.posted == [("carousel", slot + timedelta(minutes=1))]


OFF_SLOT = [(w, h, (h * 17) % 60) for w, h in HOURS]   # every hour of the three weeks, at a varying minute


def _published_now(coord):
    def publish_run(rid, dry_run=False, previous_status="queued"):
        coord.db.update_run(rid, "published")
        return {"status": "published"}
    return publish_run


@pytest.mark.parametrize("week,hour,minute", OFF_SLOT,
                         ids=[f"{w:%d%b}-{(w + timedelta(hours=h, minutes=m)):%a%H%M}" for w, h, m in OFF_SLOT])
def test_a_reel_posted_off_slot_still_gets_its_carousel_slot(coord, clock, monkeypatch, week, hour, minute):
    """⚡ Post now at any hour: the carousel's window counts from then, it gets a Tue-Thu slot at least 48 h later
    (never expiring first), and the Post now message names the slot the daemon then posts it in."""
    from src.posting_view import carousel_plan
    now = clock(week + timedelta(hours=hour, minutes=minute))
    coord.schedule_run("r1", now=now)
    monkeypatch.setattr(coord, "publish_run", _published_now(coord))
    text = coord.post_now("r1")
    carousel = _posts(coord, "carousel")[0]
    assert ps.carousel_reel(coord.db, carousel)[0] == "posted"

    plan = carousel_plan(coord.db, now)
    assert carousel["id"] in plan, f"posted {now:%a %H:%M %Z}: the carousel has no slot"
    slot = plan[carousel["id"]]
    assert slot - now >= timedelta(hours=48)
    assert slot.astimezone(ET).strftime("%a") in ("Tue", "Wed", "Thu")
    assert f"the carousel {html.escape(ps.fmt(slot))}." in text

    for candidate in _carousel_slots(now, slot):
        clock(candidate + timedelta(minutes=1))
        coord._post_carousel_if_due(candidate + timedelta(minutes=1))
        assert coord.posted == ([("carousel", candidate + timedelta(minutes=1))] if candidate == slot else [])
    assert _posts(coord, "carousel")[0]["status"] == "published"


def test_the_deadline_is_the_first_slot_when_seven_days_fall_short():
    """Posted Tue 3 Nov 14:00 EST: Thu 5 Nov 12:00 is 46 h later, too soon; the next is Tue 10 Nov 19:00, 7 d 5 h
    after the Reel. That slot is its deadline, so it is not lost."""
    reel_at = datetime(2026, 11, 3, 14, 0, tzinfo=ET)
    first = datetime(2026, 11, 10, 19, 0, tzinfo=ET)
    assert ps.first_carousel_slot(reel_at, now=reel_at) == first
    assert ps.carousel_deadline(reel_at) == first and ps.carousel_may_take(first, reel_at)
    assert ps.carousel_expired(first + timedelta(days=1), reel_at)
    on_time = datetime(2026, 11, 2, 12, 0, tzinfo=ET)          # Thu 5 Nov 12:00 is 3 days later: 7 days stand
    assert ps.carousel_deadline(on_time) == on_time + timedelta(days=7)


@pytest.mark.parametrize("posted", [False, True], ids=["booked", "posted"])
def test_a_reel_an_hour_off_the_wall_clock_gets_a_carousel_slot(db, tmp_path, posted):
    """Reads the wall clock (run under the 168-hour clock plugin to cover every hour): a Reel booked an hour from
    now, or posted now outside any Reel slot, leaves its carousel a Tue-Thu slot 48 h or more after it."""
    from src.posting_view import carousel_plan
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_w", topic)
    db.update_run("run_w", "published" if posted else "scheduled", master_video_path=str(tmp_path / "m.mp4"))
    reel_at = now if posted else now + timedelta(hours=1)
    reel = db.add_scheduled_post("run_w", "reel", reel_at.isoformat(), [])
    if posted:
        db.update_scheduled_post(reel, "published", detail={"posted_at": reel_at.isoformat()})
    car = db.add_scheduled_post("run_w", "carousel", None, ["c.jpg"], status="waiting")
    slot = carousel_plan(db, now).get(car)
    assert slot is not None, f"{now:%a %H:%M %Z}: no carousel slot"
    assert slot - reel_at >= timedelta(hours=48) and slot.astimezone(ET).strftime("%a") in ("Tue", "Wed", "Thu")
    assert slot == ps.first_carousel_slot(reel_at, now=now)
