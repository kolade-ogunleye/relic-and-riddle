"""
src/provider_status.py
======================
Which paid providers are shut right now (an account usage limit, a monthly cap), from the error that said
so, kept in data/provider_status.json so the dashboard can say it. Production ran for a day with every
Claude call failing ("You will regain access on 2026-11-01") and the log the only place that knew
(2026-10-24); the pipeline fell back to Gemini silently.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import config

log = logging.getLogger(__name__)

NAMES = {"claude": "Claude", "gemini-billed": "the billed Gemini key"}
FALLBACKS = {"claude": "tasks fall back to Gemini", "gemini-billed": "heavy tasks wait for the free quota"}


def _file():
    return config.DATA_DIR / "provider_status.json"


def _load() -> Dict[str, dict]:
    try:
        return json.loads(_file().read_text(encoding="utf-8")) if _file().is_file() else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, dict]) -> None:
    try:
        from .atomic_files import write_json
        write_json(_file(), data)
    except (OSError, ValueError) as e:
        log.debug("Provider status not saved: %s", e)


def until_from(message: str) -> Optional[str]:
    """The date an error names ("regain access on 2026-11-01 at 00:00 UTC"), ISO, else None."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})(?: at (\d{2}:\d{2}))?\s*UTC", message)
    if not m:
        return None
    return f"{m.group(1)}T{m.group(2) or '00:00'}:00+00:00"


def next_month() -> str:
    now = datetime.now(timezone.utc)
    first = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.isoformat()


def note_capped(provider: str, message: str, until: Optional[str] = None) -> None:
    """Records that a provider refused for want of credit or quota. Never raises."""
    data = _load()
    data[provider] = {"message": message[:300], "until": until or until_from(message),
                      "at": datetime.now(timezone.utc).isoformat()}
    _save(data)


def clear(provider: str) -> None:
    data = _load()
    if provider in data:
        del data[provider]
        _save(data)


def capped() -> Dict[str, dict]:
    """Providers still shut: an entry past its date is dropped; one without a date stays until cleared."""
    data, now, live = _load(), datetime.now(timezone.utc).isoformat(), {}
    for provider, entry in data.items():
        if entry.get("until") and entry["until"] <= now:
            continue
        live[provider] = entry
    if live != data:
        _save(live)
    return live


def dashboard_lines() -> List[str]:
    """What the dashboard's "Needs you" says about a shut provider (HTML)."""
    import html
    lines = []
    for provider, entry in capped().items():
        name = NAMES.get(provider, provider)
        when = ""
        if entry.get("until"):
            try:
                when = " until " + datetime.fromisoformat(entry["until"]).strftime("%d %b").lstrip("0")
            except ValueError:
                when = ""
        name = name[0].upper() + name[1:]
        lines.append(f"⛔ <b>{html.escape(name)} is unavailable</b>{when}: {html.escape(entry.get('message', '')[:140])}"
                     f" · {FALLBACKS.get(provider, 'other providers stand in')}")
    return lines
