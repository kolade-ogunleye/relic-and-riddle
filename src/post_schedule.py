"""
src/post_schedule.py
====================
When each kind of post goes out (POST_TIMEZONE, US Eastern by default).

  Reels      one a day, alternating between REEL_POST_TIMES (12:00 / 19:00)
  Stories    STORY_DELAY_MINUTES after that day's Reel (it points viewers to the Reel)
  Carousels  CAROUSEL_DAYS (Tue-Thu) at the other Reel time, so two feed posts never land together

All times are timezone-aware; the database stores them in UTC.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

from . import config

LEAD = timedelta(minutes=10)  # don't book a slot that starts within minutes of approval
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def tz() -> ZoneInfo:
    return ZoneInfo(config.POST_TIMEZONE)


def _times() -> list:
    return [time(*(int(x) for x in t.split(":"))) for t in config.REEL_POST_TIMES]


def reel_time(day: date) -> datetime:
    times = _times()
    return datetime.combine(day, times[day.toordinal() % len(times)], tz())


def next_reel_slot(now: datetime, taken_days: Iterable[date] = ()) -> datetime:
    """The first day (from today) with no Reel booked whose slot is still ahead."""
    taken = set(taken_days)
    day = now.astimezone(tz()).date()
    while True:
        slot = reel_time(day)
        if day not in taken and slot >= now + LEAD:
            return slot
        day += timedelta(days=1)


def story_time(reel_slot: datetime) -> datetime:
    return reel_slot + timedelta(minutes=config.STORY_DELAY_MINUTES)


def carousel_time(day: date) -> Optional[datetime]:
    if WEEKDAYS[day.weekday()] not in config.CAROUSEL_DAYS:
        return None
    times = _times()
    return datetime.combine(day, times[(day.toordinal() + 1) % len(times)], tz())


def next_carousel_slot(now: datetime) -> datetime:
    day = now.astimezone(tz()).date()
    for _ in range(14):
        slot = carousel_time(day)
        if slot and slot >= now:
            return slot
        day += timedelta(days=1)
    raise ValueError("CAROUSEL_DAYS has no valid weekday")


# ------------------------------------------------------------ carousel window
# One rule for the three places that need it: the daemon posting a carousel (main._post_carousel_if_due), the
# Posting and Slots screens predicting its slot (posting_view.carousel_plan) and the confirmation sent when a run is
# booked (main._book_followers, main.post_now). A carousel may take a carousel slot from CAROUSEL_GAP_HOURS after
# its Reel until its deadline: CAROUSEL_MAX_AGE_DAYS after the Reel, or its first carousel slot CAROUSEL_GAP_HOURS
# after the Reel when that is later. Both count from when the Reel posted, or from its slot while it hasn't posted
# (operator, 2026-11-03: counted from the booking, a run approved Mon-Tue afternoon had a carousel that could never
# post; and every carousel gets its slot, a Reel posted off-slot too, though Thu 12:00 to Tue 19:00 is 5 d 7 h).

def carousel_gap() -> timedelta:
    return timedelta(hours=config.CAROUSEL_GAP_HOURS)


def carousel_max_age() -> timedelta:
    return timedelta(days=config.CAROUSEL_MAX_AGE_DAYS)


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def carousel_reel(db, post: Dict[str, Any]) -> Tuple[str, datetime]:
    """What a waiting carousel's window counts from, and where its Reel stands:
      ("posted", when it posted)   its Reel booking is published (or the run was posted without a Reel booking:
                                   Post for real, a retry, the CLI; then the carousel's own booking time)
      ("booked", its slot)         its Reel is booked and hasn't posted: the carousel's slot is a forecast
      ("waiting", its slot)        its Reel's booking failed, is unsure or was cancelled: the carousel waits for
                                   a Reel that posts and expires at the deadline counted from that Reel's slot."""
    reels = [p for p in db.list_scheduled_posts(("published", "pending", "unknown", "failed", "cancelled"),
                                                run_id=post["run_id"]) if p["kind"] == "reel"]
    published = [p for p in reels if p["status"] == "published"]
    if published:
        reel = max(published, key=lambda p: p["id"])
        return "posted", (_parse((reel.get("detail") or {}).get("posted_at")) or _parse(reel.get("updated_at"))
                          or _parse(reel.get("due_at")) or _parse(post.get("created_at")) or datetime.now(timezone.utc))
    created = _parse(post.get("created_at")) or datetime.now(timezone.utc)
    pending = [p for p in reels if p["status"] == "pending" and _parse(p.get("due_at"))]
    if pending:
        return "booked", _parse(max(pending, key=lambda p: p["id"])["due_at"])
    if reels:  # its Reel never posted (yet): its last slot, or the carousel's booking when it had none
        slots = [x for x in (_parse(p.get("due_at")) for p in reels) if x]
        return "waiting", max(slots) if slots else created
    run = db.get_run(post["run_id"]) or {}
    return ("posted" if run.get("status") == "published" else "waiting"), created


def carousel_deadline(reel_at: datetime) -> datetime:
    """The last moment a carousel whose Reel is out at reel_at may post: CAROUSEL_MAX_AGE_DAYS after the Reel, or
    its first carousel slot CAROUSEL_GAP_HOURS after the Reel when that is later, so it always has one."""
    try:
        first = next_carousel_slot(reel_at + carousel_gap())
    except ValueError:  # CAROUSEL_DAYS names no weekday: no carousel slot at all
        return reel_at + carousel_max_age()
    return max(reel_at + carousel_max_age(), first)


def carousel_may_take(slot: datetime, reel_at: datetime) -> bool:
    """Whether a carousel whose Reel is (or will be) out at reel_at may post in this carousel slot."""
    return reel_at + carousel_gap() <= slot <= carousel_deadline(reel_at)


def carousel_expired(slot: datetime, reel_at: datetime) -> bool:
    """Too old for this slot (and so for every later one)."""
    return slot > carousel_deadline(reel_at)


def first_carousel_slot(reel_at: datetime, now: Optional[datetime] = None) -> Optional[datetime]:
    """The first carousel slot not before now that a carousel whose Reel is out at reel_at may take, or None when
    its deadline has passed (only when booked late: Stories and carousel booked again days after the Reel)."""
    now = now or datetime.now(timezone.utc)
    start = max(now, reel_at + carousel_gap())
    day, last = start.astimezone(tz()).date(), carousel_deadline(reel_at).astimezone(tz()).date()
    while day <= last:
        slot = carousel_time(day)
        if slot and slot >= now and carousel_may_take(slot, reel_at):
            return slot
        day += timedelta(days=1)
    return None


def fmt(when: datetime) -> str:
    """A slot as the operator reads it: their own time first, the audience's zone in brackets when the two
    differ ("Tue 27 Oct 17:00 MDT (19:00 EDT audience)"). The chat showed the audience time first and the
    operator's in brackets, which read as two different appointments (operator, 2026-10-24)."""
    audience = when.astimezone(tz())
    if config.TIMEZONE == config.POST_TIMEZONE:
        return audience.strftime("%a %d %b %H:%M %Z")
    from .local_time import local
    mine = local(when)
    theirs = audience.strftime("%a %H:%M %Z" if audience.date() != mine.date() else "%H:%M %Z")
    return f"{mine.strftime('%a %d %b %H:%M %Z')} ({theirs} audience)"


def fmt_ahead(when: datetime, now: Optional[datetime] = None) -> str:
    """fmt() plus how far away it is ("· in 2 d 21 h", "· in 40 min", "· 3 h ago"), for the panels."""
    now = now or datetime.now(timezone.utc)
    seconds = (when - now).total_seconds()
    past, seconds = seconds < 0, abs(seconds)
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        span = f"{days} d {hours} h"
    elif hours:
        span = f"{hours} h {minutes:02d} min"
    else:
        span = f"{minutes} min"
    return f"{fmt(when)} · {span + ' ago' if past else 'in ' + span}"
