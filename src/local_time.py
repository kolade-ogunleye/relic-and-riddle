"""Times shown to the operator, in DEFAULT_TIMEZONE. Stored times stay UTC; posting slots keep the
audience's POST_TIMEZONE (see post_schedule)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config


def zone() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def local(value=None) -> datetime:
    """Now, or a stored time (ISO text, epoch seconds or datetime; naive means UTC), in the operator's zone."""
    if value is None:
        return datetime.now(zone())
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, zone())
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(zone())


def show(value=None, fmt: str = "%d %b %H:%M %Z") -> str:
    """Formatted for Telegram; an unreadable stored value is shown as it is rather than hidden."""
    try:
        return local(value).strftime(fmt)
    except (TypeError, ValueError, OverflowError):
        return str(value or "")
