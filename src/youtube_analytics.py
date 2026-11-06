"""YouTube's own audience-retention curve for a posted Short (2026-11-01).

Upload-Post's post analytics give YouTube's counts (views, likes, comments) but no retention. Google's YouTube
Analytics API reports it per video: audienceWatchRatio at 100 points of the video's length
(dimension elapsedVideoTimeRatio, 0.01 to 1.0); on Shorts it can pass 1, since looping and rewinding count.
It needs the channel owner's consent once (scope yt-analytics.readonly, read-only): run
scripts/youtube_analytics_auth.py on your machine and put the three values it prints in production's .env:

    YOUTUBE_ANALYTICS_CLIENT_ID, YOUTUBE_ANALYTICS_CLIENT_SECRET, YOUTUBE_ANALYTICS_REFRESH_TOKEN

Without them nothing here runs and YouTube keeps its counts only.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
TIMEOUT = 30
_token: dict[str, Any] = {"value": "", "expires": 0.0}


def _setting(name: str) -> str:
    """One of the three YOUTUBE_ANALYTICS_* settings, read from src/config at each use (never copied at import)."""
    return str(getattr(config, name, "") or "").strip()


def configured() -> bool:
    return all(_setting(n) for n in ("YOUTUBE_ANALYTICS_CLIENT_ID", "YOUTUBE_ANALYTICS_CLIENT_SECRET",
                                 "YOUTUBE_ANALYTICS_REFRESH_TOKEN"))


def secrets() -> list[str]:
    """The values a log line must never show."""
    return [v for v in (_setting("YOUTUBE_ANALYTICS_CLIENT_SECRET"), _setting("YOUTUBE_ANALYTICS_REFRESH_TOKEN"),
                        _token["value"]) if v and len(v) >= 6]


def access_token() -> str:
    """A current access token from the refresh token (kept until a minute before it expires)."""
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    r = requests.post(TOKEN_URL, data={"client_id": _setting("YOUTUBE_ANALYTICS_CLIENT_ID"),
                                       "client_secret": _setting("YOUTUBE_ANALYTICS_CLIENT_SECRET"),
                                       "refresh_token": _setting("YOUTUBE_ANALYTICS_REFRESH_TOKEN"),
                                       "grant_type": "refresh_token"}, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"Google token refresh refused (HTTP {r.status_code}): {r.text[:200]}")
    body = r.json()
    _token.update(value=body["access_token"], expires=time.time() + float(body.get("expires_in") or 3600))
    return _token["value"]


def retention(video_id: str, published: date | None = None) -> dict[str, float]:
    """{"1": ratio, ..., "100": ratio}: audienceWatchRatio at each hundredth of the video; {} when YouTube has no
    rows yet (a new or little-watched Short)."""
    today = datetime.now(UTC).date()   # YouTube's report days are UTC-dated; start from posting, end today
    start = (published or today - timedelta(days=90)).isoformat()
    params = {"ids": "channel==MINE", "startDate": start, "endDate": today.isoformat(),
              "metrics": "audienceWatchRatio", "dimensions": "elapsedVideoTimeRatio",
              "filters": f"video=={video_id}", "sort": "elapsedVideoTimeRatio"}
    r = requests.get(REPORTS_URL, params=params, headers={"Authorization": f"Bearer {access_token()}"},
                     timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"YouTube Analytics refused the retention report (HTTP {r.status_code}): {r.text[:200]}")
    out = {}
    for ratio, watch in r.json().get("rows") or []:
        out[str(round(float(ratio) * 100))] = round(float(watch), 4)
    return out


_VIDEO_ID = (re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"), re.compile(r"shorts/([A-Za-z0-9_-]{6,})"),
             re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"))


def video_id(response: dict[str, Any]) -> str | None:
    """A posted Short's video id from its publication record (its YouTube link)."""
    urls = (response or {}).get("post_urls") or {}
    url = urls.get("youtube") if isinstance(urls, dict) else ""
    for pattern in _VIDEO_ID:
        m = pattern.search(str(url or ""))
        if m:
            return m.group(1)
    return None
