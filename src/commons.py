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
from urllib.parse import unquote

import requests
from PIL import Image, ImageOps

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


# Block-level tags end a phrase: they become a space, so "<li>Gallery</li><li>Wellcome" stays two words.
_BLOCK_TAG = re.compile(r"<\s*/?\s*(?:br|p|div|li|ul|ol|dl|dd|dt|tr|td|th|table|h\d)\b[^>]*>", re.I)


def _plain(value: str) -> str:
    """Text of a Commons metadata value on one line. Newlines are collapsed: an Artist value of two lines
    put the credit's second line in the caption without the 📷 prefix, where it counted as public prose,
    changed the fact review's fingerprint and failed the release gate's facts check (development,
    Wieliczka run_wieliczka_salt_mine_1792974672, 2026-10-26; audit 2026-10-28)."""
    # Every tag a space: two spans side by side ran together ("Unknown authorUnknown author"), which passed as
    # a named author; a value repeated whole is said once (replay of dev Wieliczka photos 06, 08, 12; 2026-10-28)
    text = html.unescape(re.sub(r"<[^>]+>", " ", _BLOCK_TAG.sub(" ", value or "")))
    text = re.sub(r" ([,.;:!?)])", r"\1", re.sub(r"\s+", " ", text)).strip()  # "<b>Smith</b>, 1950": no space before the comma
    return re.sub(r"^(.+?)(?: \1)+$", r"\1", text)


# A sentence that is a notice to re-users, not a name: Commons' Artist field holds whatever the uploader
# wrote, and a Wieliczka photo's was only "I would appreciate being notified if you use my work outside
# Wikimedia. More of my work can be found in my personal gallery." which the caption printed as the author
# (development, 2026-10-26; audit 2026-10-28).
_NOTICE = re.compile(
    r"^(?:please\b|kindly\b|i would\b|i'd\b|if you\b|more of my\b|you (?:can|may|are)\b|feel free\b|"
    r"contact me\b|e-?mail me\b|notify me\b|thanks?\b|thank you\b|see (?:also|my)\b|"
    r"this (?:file|image|photo\w*|picture|work|media)\b.*\b(?:was|is|has been|were)\b|"
    r"(?:all rights|attribution) (?:reserved|required)\b|"
    r"(?:licensed|released|published) under\b)", re.I)
# Values that are a placeholder or a note on where the file came from, never who made it.
_NOT_AUTHOR = re.compile(
    r"^(?:unknown(?: author| photographer| artist)?|n/?a|none|-+|\?+|own work|self[- ]?(?:photographed|made|"
    r"published|scanned)|gallery|source|link|original|website|"
    r"(?:transferred|imported|uploaded|moved|copied|taken|scanned|extracted|derived|cropped|retrieved|"
    r"downloaded|originally|from|via|see)\b.*)$", re.I)
_URL = re.compile(r"(?:https?:)?//\S+|\bwww\.\S+", re.I)
_LICENCE_TOKEN = re.compile(r"\b(?:CC[- ]?(?:BY|0|ZERO)(?:[- ](?:SA|ND|NC))*(?:[- ]\d(?:\.\d)?)?|"
                            r"public domain|PD-\S+)\b", re.I)
# A Commons user link in the Artist HTML: the uploader's account names the author when the text does not.
_USER_LINK = re.compile(r'href="[^"]*?(?:\bUser:|Special:EmailUser/)([^"/#?&]+)"', re.I)
MAX_AUTHOR = 150  # longer than any name: an uploader's paragraph, not an author


def _without_notices(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(s for s in sentences if s.strip() and not _NOTICE.match(s.strip())).strip()


def _name(text: str) -> str:
    """`text` as an author's name, or "" when it is a placeholder, a provenance note or too long."""
    text = re.sub(r"^[\s:;,.\-–—|]+|[\s:;,\-–—|]+$", "", text)
    text = re.sub(r"(?<=\w{4})\.$", "", text)  # a sentence's full stop, not an initial's ("Jr.")
    if not text or len(text) > MAX_AUTHOR or _NOT_AUTHOR.match(text):
        return ""
    return text


def _credit_author(credit_html: str) -> str:
    """The institution or person a Commons Credit value names. Wellcome Collection files carry no Artist,
    only a Credit of links: "<li>Gallery: https://...</li><li>Wellcome Collection gallery (2018-03-22):
    https://wellcomecollection.org/works/... CC-BY-4.0</li>" (Commons API, 2026-10-28). URLs, licence
    tokens and dates are dropped; the first phrase left that is a name is the author."""
    for block in _BLOCK_TAG.split(credit_html or ""):
        text = _LICENCE_TOKEN.sub(" ", _URL.sub(" ", _plain(block)))
        text = re.sub(r"\([^)]*\d[^)]*\)", " ", text)  # "(2018-03-22)"
        text = re.sub(r"\s+gallery\s*[:.]?\s*$", "", re.sub(r"\s+", " ", text).strip())  # the site's label for its own page
        name = _name(_without_notices(text))
        if name:
            return name
    return ""


def _author(meta: dict) -> str:
    """Who the photo is credited to: the structured Attribution line when the file has one, then the Artist
    text without re-user notices, then the Commons account the Artist links to, then the Credit's named
    institution; "Unknown" when none of them names anyone. 11 of 11 Wellcome scenes in the Trepanation run
    (run_trepanation_miracle_1792995462) and 1 of 10 for the singing colossi were recorded "Unknown" from the
    Artist field alone, so the release marked those CC BY photos unverified (development, 2026-10-26;
    audit 2026-10-28)."""
    def value(key: str) -> str:
        return str((meta.get(key) or {}).get("value") or "")

    attribution = _name(_without_notices(_plain(value("Attribution"))))
    if attribution:
        return attribution
    raw_artist = value("Artist")
    artist_text = _without_notices(_plain(raw_artist))
    artist = _name(artist_text)
    if artist:
        return artist
    for user in _USER_LINK.findall(raw_artist):
        name = _name(unquote(user).replace("_", " "))
        if name:
            return name
    credited = _credit_author(value("Credit"))
    if credited:
        return credited
    # An uploader's paragraph (on one line) is kept for files that need no credit; candidates() refuses it
    # as the author of a CC BY file.
    if artist_text and not _NOT_AUTHOR.match(artist_text):
        return artist_text
    return "Unknown"


def _needs_named_author(licence: str) -> bool:
    """The licences the release gate requires a named author for (credits.generate_provenance_ledger: CC BY
    with an "unknown" artist is unverified)."""
    return licence.strip().lower().startswith("cc by")


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
        # Credit and Attribution name the author when Artist does not (Wellcome Collection files, see _author).
        "iiextmetadatafilter": "LicenseShortName|Artist|Credit|Attribution|LicenseUrl|AttributionRequired|DateTimeOriginal|"
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

        # A CC BY file nobody can be credited for is refused here, with the reason, rather than fetched,
        # shown on the review card and then failing the release's rights check (development, 2026-10-26;
        # audit 2026-10-28).
        artist = _author(meta)
        if _needs_named_author(licence) and (artist == "Unknown" or len(artist) > MAX_AUTHOR):
            log.info("Commons: skipped %s: %s needs a named author and its Artist, Attribution and Credit "
                     "name nobody", page["title"], licence)
            continue

        yield {
            "title": page["title"],
            "download_url": target_url,
            "source_url": info.get("descriptionurl", ""),
            "artist": artist,
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
    # Sharp enough for a 1080-wide frame without upscaling counts for a little: 8 of 16 pool images had
    # to be AI-upscaled on every Kailasa run (2026-10-24). A small file is never excluded by this alone.
    width = cand.get("width") or 0
    if width:
        score += 1.0 if width >= 1080 else -1.0
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
    with Image.open(io.BytesIO(resp.content)) as raw:
        # Upright first: re-encoding drops the EXIF orientation, and a portrait photo stored sideways with a
        # rotate flag reached a video turned over (Unfinished Obelisk, beat 10, 2026-10-25).
        im = ImageOps.exif_transpose(raw)
        w, h = im.size
        if min_pixels and (w * h < min_pixels):
            raise ValueError(f"Resolution too low: {w}x{h} ({w*h:,} px < {min_pixels:,})")
        if min_height and (h < min_height and w < min_height):
            raise ValueError(f"Dimensions too small: {w}x{h} (both edges < {min_height})")
        im.convert("RGB").save(dest, "JPEG", quality=92)  # re-encode: strips metadata and odd formats
