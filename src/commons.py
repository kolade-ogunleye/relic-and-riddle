"""
src/commons.py
==============
Wikimedia Commons search and download with licence, relevance and resolution filters.
Shared by scripts/fetch_commons_images.py (topic image sets) and src/beat_images.py
(per-beat images during a run).
"""

import html
import io
import logging
import os
import re
import time
from pathlib import Path
from typing import Iterator, List, Optional

import requests
from PIL import Image

from .credits import license_allowed, normalize

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks automated clients to identify themselves with contact details and throttles
# anonymous ones hard. Set COMMONS_CONTACT (a URL or email you choose) for better rate limits.
_CONTACT = os.getenv("COMMONS_CONTACT", "").strip()
HEADERS = {"User-Agent": "RelicAndRiddle/1.0 (scene image fetcher" + (f"; {_CONTACT}" if _CONTACT else "") + ")"}
MIN_PIXELS = 1_500_000
MIN_HEIGHT = 1000


def _get(url: str, params: Optional[dict] = None, timeout: float = 60, max_wait: int = 60) -> requests.Response:
    """GET with polite pacing and Retry-After-aware backoff on 429/503."""
    resp = None
    started = time.monotonic()
    for attempt in range(5):
        time.sleep(1.0)
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code not in (429, 503):
            resp.raise_for_status()
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        wait = int(retry_after) if retry_after.isdigit() else 15 * 2 ** attempt
        if wait + (time.monotonic() - started) > max_wait:
            break  # don't hang for hours; re-run later (or set COMMONS_CONTACT)
        log.info("Commons rate limited; waiting %ss as requested", wait)
        time.sleep(wait + 1)  # retrying before Retry-After expires only extends the block
    resp.raise_for_status()
    return resp


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def search(query: str, limit: int = 50, max_wait: int = 60) -> list:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlheight": 2880,
        "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|AttributionRequired|DateTimeOriginal",
    }
    resp = _get(API, params=params, timeout=30, max_wait=max_wait)
    pages = resp.json().get("query", {}).get("pages", {})
    return sorted(pages.values(), key=lambda p: p.get("index", 0))


def find_categories(topic_text: str, limit: int = 5, max_wait: int = 30) -> List[str]:
    """Finds relevant Wikimedia Commons Category names for a topic."""
    params = {
        "action": "query", "format": "json", "list": "search",
        "srsearch": topic_text, "srnamespace": 14, "srlimit": limit,
    }
    try:
        resp = _get(API, params=params, timeout=20, max_wait=max_wait)
        results = resp.json().get("query", {}).get("search", [])
        return [r["title"] for r in results if r.get("title")]
    except Exception:
        return []


def photo_year(raw: str) -> Optional[int]:
    """The year a photo was taken, only when the record states exactly one plausible year
    ("1902", "1902-05-17 10:00"); ranges, "circa" and empty values give None."""
    text = _plain(raw).lower()
    if not text or any(w in text for w in ("circa", "c.", "ca.", "before", "after", "between", "or ")):
        return None
    years = set(re.findall(r"\b(1[89]\d\d|20\d\d)\b", text))
    return int(years.pop()) if len(years) == 1 else None


def candidates(pages: list, keywords: List[str], allow_sa: bool = False, min_pixels: int = MIN_PIXELS,
               min_height: int = MIN_HEIGHT) -> Iterator[dict]:
    """Relevant (title contains a keyword), openly licensed, high-enough-resolution images.
    Prioritizes the original full-resolution master file up to 35MB, falling back to 4K preview."""
    keywords = [normalize(k) for k in keywords]
    for page in pages:
        if keywords and not any(k in normalize(page["title"]) for k in keywords):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        if not license_allowed(licence, allow_sa):
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w * h < min_pixels or (h < min_height and w < min_height):
            continue

        # Target pristine original master file if <= 35MB; otherwise use 4K/UHD rendered preview
        orig_url = info.get("url")
        file_bytes = info.get("size", 0)
        if orig_url and 0 < file_bytes <= 35_000_000:
            target_url = orig_url
        else:
            target_url = info.get("thumburl") or orig_url

        yield {
            "title": page["title"],
            "download_url": target_url,
            "source_url": info.get("descriptionurl", ""),
            "artist": _plain(meta.get("Artist", {}).get("value", "")) or "Unknown",
            "license": licence,
            "license_url": meta.get("LicenseUrl", {}).get("value", ""),
            "attribution_required": meta.get("AttributionRequired", {}).get("value", "true") != "false",
            "year": photo_year(meta.get("DateTimeOriginal", {}).get("value", "")),
            "width": w,
            "height": h,
        }


def download_jpeg(
    url: str,
    dest: Path,
    max_wait: int = 30,
    min_pixels: Optional[int] = None,
    min_height: Optional[int] = None,
) -> None:
    resp = _get(url, timeout=30, max_wait=max_wait)
    with Image.open(io.BytesIO(resp.content)) as im:
        im.verify()  # reject corrupt / non-image payloads
    with Image.open(io.BytesIO(resp.content)) as im:
        w, h = im.size
        if min_pixels and (w * h < min_pixels):
            raise ValueError(f"Resolution too low: {w}x{h} ({w*h:,} px < {min_pixels:,})")
        if min_height and (h < min_height and w < min_height):
            raise ValueError(f"Dimensions too small: {w}x{h} (both edges < {min_height})")
        im.convert("RGB").save(dest, "JPEG", quality=92)  # re-encode: strips metadata and odd formats
