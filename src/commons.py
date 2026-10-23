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
import re
import time
from pathlib import Path
from typing import Iterator, List, Optional

import requests
from PIL import Image

from . import config  # every setting is read once, in config (COMMONS_CONTACT came out empty when read here first)
from .credits import license_allowed, normalize

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's User-Agent policy: name the bot (with "bot" in it), give contact details and the
# library, or requests may be blocked without notice. Set COMMONS_CONTACT (a URL or email).
_CONTACT = config.COMMONS_CONTACT
HEADERS = {"User-Agent": "RelicAndRiddleBot/1.0 (" + (f"{_CONTACT}; " if _CONTACT else "")
           + f"licensed image sourcing for history documentaries) python-requests/{requests.__version__}"}
# API etiquette for non-interactive tasks: pause while the database servers lag (seconds).
MAXLAG = 5
MIN_PIXELS = 1_500_000
MIN_HEIGHT = 1000


def _maxlagged(resp: requests.Response) -> bool:
    """A 'maxlag' refusal: HTTP 200 with an error body asking the client to wait."""
    if "json" not in resp.headers.get("Content-Type", ""):
        return False
    try:
        return (resp.json().get("error") or {}).get("code") == "maxlag"
    except ValueError:
        return False


def _get(url: str, params: Optional[dict] = None, timeout: float = 60, max_wait: int = 60) -> requests.Response:
    """GET following Wikimedia's API etiquette: one request at a time with a pause between,
    maxlag on API calls, and backoff that honours Retry-After on 429/503 and maxlag refusals."""
    resp = None
    started = time.monotonic()
    if url == API:
        params = {**(params or {}), "maxlag": MAXLAG}
    for attempt in range(5):
        time.sleep(1.0)
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code not in (429, 503) and not _maxlagged(resp):
            resp.raise_for_status()
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        wait = int(retry_after) if retry_after.isdigit() else 15 * 2 ** attempt
        if wait + (time.monotonic() - started) > max_wait:
            break  # don't hang for hours; re-run later (or set COMMONS_CONTACT)
        log.info("Commons rate limited; waiting %ss as requested", wait)
        time.sleep(wait + 1)  # retrying before Retry-After expires only extends the block
    if _maxlagged(resp):
        raise requests.RequestException("Commons asked automated clients to wait (maxlag); try again later.")
    resp.raise_for_status()
    return resp


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


# Commons' own human-reviewed grades (best first): Featured pictures are judged the finest on the
# site; Quality images pass technical review (sharp, well exposed, at least 2 MP); Valued images are
# the most useful image of their subject.
GRADE_CATEGORIES = ("Category:Featured pictures on Wikimedia Commons", "Category:Quality images",
                    "Category:Valued images")


def grade(page: dict) -> int:
    """3 Featured, 2 Quality, 1 Valued, 0 ungraded."""
    titles = {c.get("title") for c in page.get("categories") or []}
    return max((3 - i for i, cat in enumerate(GRADE_CATEGORIES) if cat in titles), default=0)


def search_graded(query: str, limit: int = 50, max_wait: int = 60) -> list:
    """The query restricted to graded files, so they are considered even when they don't rank in the
    plain search's top results. (Most graded photos are CC BY-SA, which is excluded by default.)"""
    cats = "|".join(c.split(":", 1)[1].replace(" ", "_") for c in GRADE_CATEGORIES)
    return search(f"{query} incategory:{cats}", limit=limit, max_wait=max_wait)


def search(query: str, limit: int = 50, max_wait: int = 60) -> list:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo|categories", "iiprop": "url|size|mime|extmetadata", "iiurlheight": 2880,
        "clcategories": "|".join(GRADE_CATEGORIES), "cllimit": "max",
        "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|AttributionRequired|DateTimeOriginal|"
                               "ImageDescription|Categories|ObjectName|GPSLatitude|GPSLongitude",
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

        # The original file: Wikimedia's cached 2880-px rendition measured 32-37 dB PSNR against it at
        # final frame size (its re-compression and sharpening change fine texture). Downloads stay one
        # at a time with a pause between; only files over 35 MB use the rendition.
        orig_url = info.get("url")
        target_url = orig_url if orig_url and 0 < info.get("size", 0) <= 35_000_000 else (info.get("thumburl") or orig_url)

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
            "grade": grade(page),
            # What the file says it shows: used to rank relevance and to caption it for the planner.
            "description": _plain(meta.get("ImageDescription", {}).get("value", ""))[:400],
            "categories": [c for c in _plain(meta.get("Categories", {}).get("value", "")).split("|") if c][:15],
            "lat": _float(meta.get("GPSLatitude", {}).get("value")),
            "lon": _float(meta.get("GPSLongitude", {}).get("value")),
        }


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


# Words in a title, description or category that mark a picture as off-subject.
OFF_SUBJECT = ("tourist", "souvenir", "ticket", "restaurant", "hotel", "cafe", "bus stop", "parking", "gift shop",
               "selfie", "signpost", "logo", "map of", "stamp", "postage", "coin", "banknote", "poster")


def relevance(cand: dict, keywords: List[str], place: Optional[dict] = None) -> float:
    """How surely the file shows the topic, from its public metadata: keywords in the title, the
    description and the categories (a curator's classification), camera position near the site,
    and Commons' quality grade; off-subject words count against it."""
    kws = [normalize(k) for k in keywords if k]
    title, desc = normalize(cand.get("title", "")), normalize(cand.get("description", ""))
    cats = normalize(" | ".join(cand.get("categories", [])))
    score = 3.0 * any(k in title for k in kws) + 2.0 * any(k in desc for k in kws) + 2.0 * any(k in cats for k in kws)
    if place and place.get("lat") is not None and cand.get("lat") is not None and cand.get("lon") is not None:
        d = _km(place["lat"], place["lon"], cand["lat"], cand["lon"])
        score += 3.0 if d <= 2 else 1.5 if d <= 15 else -2.0 if d > 200 else 0.0
    score += cand.get("grade", 0)
    if any(w in f"{title} {desc} {cats}" for w in OFF_SUBJECT):
        score -= 4.0
    return score


def caption(cand: dict, max_len: int = 160) -> str:
    """One line saying what a picture shows, for the scene planner."""
    parts = [re.sub(r"^File:|\.(jpe?g|png|tiff?)$", "", cand.get("title", ""), flags=re.I).strip()]
    if cand.get("description"):
        parts.append(cand["description"])
    if cand.get("year"):
        parts.append(f"photographed {cand['year']}")
    return " - ".join(p for p in parts if p)[:max_len]


def best_first(cands: List[dict], keywords: Optional[List[str]] = None, place: Optional[dict] = None) -> List[dict]:
    """Openly licensed before CC BY-SA (a video using a BY-SA image must itself be BY-SA), then most relevant
    (metadata, see relevance()), then resolution; ties keep search order."""
    from .credits import is_share_alike
    for c in cands:
        c["relevance"] = round(relevance(c, keywords or [], place), 2)
    return sorted(cands, key=lambda c: (is_share_alike(c.get("license", "")), -c["relevance"],
                                        -(c.get("width", 0) * c.get("height", 0))))


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
