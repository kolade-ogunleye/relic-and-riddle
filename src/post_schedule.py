"""
src/post_schedule.py
====================
When each kind of post goes out (POST_TIMEZONE, US Eastern by default).

  Reels      one a day, alternating between REEL_POST_TIMES (12:00 / 19:00)
  Stories    STORY_DELAY_MINUTES after that day's Reel (it points viewers to the Reel)
  Carousels  CAROUSEL_DAYS (Tue-Thu) at the other Reel time, so two feed posts never land together

All times are timezone-aware; the database stores them in UTC.
"""

from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional
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


def fmt(when: datetime) -> str:
    return when.astimezone(tz()).strftime("%a %d %b %H:%M %Z")
