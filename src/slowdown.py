"""
src/slowdown.py
===============
Why a run is slower than usual right now, in plain words, for its progress card: the free model retrying, the
billed key standing in, Telegram's flood control. The self-audit found a run promised ~16 min that took 50 (the
free model timed out for half an hour, Telegram rate-limited the bot; production, 2026-10-26) with nothing on the
card saying why. A note is fresh for FRESH_SECONDS; the card shows the latest fresh one.
"""

import threading
import time
from typing import Optional

FRESH_SECONDS = 180
_lock = threading.Lock()
_latest: dict = {}


def note(why: str) -> None:
    with _lock:
        _latest.update(why=why, at=time.time())


def current(now: Optional[float] = None) -> str:
    with _lock:
        if _latest and (now or time.time()) - _latest["at"] <= FRESH_SECONDS:
            return _latest["why"]
    return ""
