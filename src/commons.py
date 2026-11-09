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
import threading
import time
from pathlib import Path
from typing import Iterator, List, Optional
from urllib.parse import unquote

import requests
from PIL import Image, ImageOps

from . import config  # every setting is read once, in config (COMMONS_CONTACT came out empty when read here first)
from .credits import license_allowed, normalize, sanitise_credit_text

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
MIN_VERTICAL_CROP = 1080
NATIVE_VERTICAL_CROP = 1350


def vertical_crop_width(width: int, height: int) -> int:
    """Usable source pixels across a 9:16 crop, before any upscaling."""
    return max(0, int(min(width, height * 9 / 16)))


def crop_rank(cand: dict) -> float:
    w, h = cand.get("width", 0), cand.get("height", 0)
    aspect = w / h if h else 0
    bonus = 1.10 if any(abs(aspect - ratio) <= 0.03 for ratio in (3 / 4, 4 / 5, 1)) else 1.0
    return vertical_crop_width(w, h) * bonus


def _maxlagged(resp: requests.Response) -> bool:
    """A 'maxlag' refusal: HTTP 200 with an error body asking the client to wait."""
    if "json" not in resp.headers.get("Content-Type", ""):
        return False
    try:
        return (resp.json().get("error") or {}).get("code") == "maxlag"
    except ValueError:
        return False


def _get(url: str, params: Optional[dict] = None, timeout: float = 60, max_wait: int = 60,
         allow_redirects: bool = True, stream: bool = False) -> requests.Response:
    """GET following Wikimedia's API etiquette: one request at a time with a pause between,
    maxlag on API calls, and backoff that honours Retry-After on 429/503 and maxlag refusals.
    `allow_redirects=False` hands a redirect back unfollowed (download_jpeg checks each hop itself).
    Streamed download bodies are left unread for _capped_body, including JSON responses."""
    hops = {} if allow_redirects else {"allow_redirects": False}
    resp = None
    started = time.monotonic()
    if url == API:
        params = {**(params or {}), "maxlag": MAXLAG}
    for attempt in range(5):
        time.sleep(1.0)
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout, **hops,
                            **({"stream": True} if stream else {}))
        if resp.status_code not in (429, 503) and (stream or not _maxlagged(resp)):
            try:
                resp.raise_for_status()
            except requests.RequestException:
                if stream:
                    resp.close()
                raise
            return resp
        retry_after = resp.headers.get("Retry-After", "")
        if stream:
            resp.close()
        wait = int(retry_after) if retry_after.isdigit() else 15 * 2 ** attempt
        if wait + (time.monotonic() - started) > max_wait:
            break  # don't hang for hours; re-run later (or set COMMONS_CONTACT)
        log.info("Commons rate limited; waiting %ss as requested", wait)
        time.sleep(wait + 1)  # retrying before Retry-After expires only extends the block
    if not stream and _maxlagged(resp):
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
    data = resp.json()
    # A deepcategory: over a large tree is applied to only some of it ("Deep category query returned too many
    # categories. Only a subset of categories has been applied."): said, so a widened search is not taken as whole.
    warning = str((data.get("warnings") or {}).get("search") or "")
    _last_search.partial = "subset" in warning.lower() or "too many categories" in warning.lower()
    pages = data.get("query", {}).get("pages", {})
    return sorted(pages.values(), key=lambda p: p.get("index", 0))


_last_search = threading.local()   # .partial: the last search() on this thread applied only part of a category


# Commons search matches every word, so a precise per-beat query often finds nothing (dev Nazca, 2026-10-28:
# "Nazca lines ground level trench" 0 results, "ground" in the Nazca lines category 23). A query with fewer than
# WIDEN_BELOW results is tried again shorter, at most WIDEN_MAX_EXTRA more searches, stopping at the first that
# fails, each restricted to something the topic structurally is: its own Commons category (deepcategory:), else
# its place's category (incategory:), else its coordinates (nearcoord:). A word from the title is never the anchor:
# "Under", "Salt" or "Roman" found railway tunnels, Salt Lake City and other Roman kilns (review, 2026-10-29).
# Nothing to anchor on: no widening. The precise query, and every filter after the search (licence, keywords,
# resolution, author, the look, the line check), are as before.
WIDEN_BELOW = 8
WIDEN_MAX_EXTRA = 3
NEAR_KM = 10            # a coordinate anchor's radius (a city's 20 km held its scooters and bridges)
_FILLER = {"the", "a", "an", "of", "in", "on", "at", "and", "or", "to", "for", "with", "from", "by", "near", "not"}
_OPERATORS = {"AND", "OR", "NOT"}
_ANCHORS: dict = {}     # topic key -> (when, its anchor): looked up once, again after ANCHOR_TTL (None: sooner)
ANCHOR_TTL, NO_ANCHOR_TTL = 24 * 3600, 3600


def _words(text: str) -> List[str]:
    """Search words: hyphens split, quotes, brackets and the search operators (AND, OR, NOT, a leading -) dropped."""
    text = re.sub(r"['’]s\b", "", (text or "").replace("-", " "))   # "Heng's" is Heng
    return [w for w in re.findall(r"[^\W_][\w'’]*", text) if w not in _OPERATORS]


def _similar(a: str, b: str) -> bool:
    """One word for another: equal, a plural, or one letter apart in a long word (Odessa / Odesa)."""
    a, b = normalize(a), normalize(b)
    if a == b or a.rstrip("s") == b.rstrip("s"):
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _category_words(name: str) -> List[str]:
    """A category's own words: no "Category:", no disambiguation in brackets, no filler."""
    name = re.sub(r"\([^)]*\)", " ", name.split(":", 1)[-1])
    return [w for w in _words(name) if w.lower() not in _FILLER]


def _matches(category: str, words: List[str]) -> int:
    """How many of the category's words the topic's words name (0 unless all of them do)."""
    own = _category_words(category)
    return len(own) if own and all(any(_similar(c, w) for w in words) for c in own) else 0


def _files(categories: List[str], max_wait: int) -> dict:
    """{category: (files, subcategories)} from Commons' category counts."""
    if not categories:
        return {}
    resp = _get(API, params={"action": "query", "format": "json", "prop": "categoryinfo",
                             "titles": "|".join(categories)}, timeout=20, max_wait=max_wait)
    out = {}
    for page in (resp.json().get("query") or {}).get("pages", {}).values():
        info = page.get("categoryinfo") or {}
        out[page.get("title")] = (int(info.get("files") or 0), int(info.get("subcats") or 0))
    return out


def _in_order(words: List[str], sequences: List[List[str]]) -> bool:
    """`words` appear in this order in one of `sequences` ("Roman concrete" is not the typeface "Concrete Roman")."""
    for seq in sequences:
        i = 0
        for w in seq:
            if i < len(words) and _similar(words[i], w):
                i += 1
        if i == len(words):
            return True
    return False


def _qualified_elsewhere(category: str, words: List[str]) -> bool:
    """A bracketed qualifier the topic does not name: a namesake ("Zhang Heng (actress)", "Archimedes (genus)")."""
    brackets = " ".join(re.findall(r"\(([^)]*)\)", category.split(":", 1)[-1]))
    return any(not any(_similar(q, w) for w in words) for q in _words(brackets) if q.lower() not in _FILLER)


PLACE_SUBCATS_MAX = 10   # a place's category with more subcategories is a city, region or sea, not a site


def _fullness(category: str, words: List[str]) -> int:
    """How fully a category is the topic's name: its words `words` name (a bracketed one too, "Heracleion
    (Thonis)"), less the bracketed qualifiers they do not name ("Archimedes (genus)", a fossil, against "Claw of
    Archimedes"; "Zhang Heng (actress)")."""
    name = category.split(":", 1)[-1]
    brackets = " ".join(re.findall(r"\(([^)]*)\)", name))
    main = [w for w in _words(re.sub(r"\([^)]*\)", " ", name)) if w.lower() not in _FILLER]
    qualifiers = [w for w in _words(brackets) if w.lower() not in _FILLER]
    named = lambda w: any(_similar(w, x) for x in words)  # noqa: E731
    return sum(1 for w in main if named(w)) + sum(1 if named(w) else -1 for w in qualifiers)


def topic_anchor(topic: dict, place: str = "", photos: Optional[List[dict]] = None,
                 aliases: Optional[List[str]] = None, max_wait: int = 30,
                 subject_anchor: dict | None = None, category_search=None) -> Optional[dict]:
    """What widened searches are restricted to, for `topic` (or a beat about `place`, another site): {"kind":
    "category", "name", "deep"}, a Commons category with files whose words the topic's title, id, other names
    (`aliases`, e.g. its image queries) and place name together; else {"kind": "near", "lat", "lon"} from the
    topic's coordinates; else None.
      - The topic's own (a name word in it) goes before its place's, and is searched deep (deepcategory:); a place's,
        or one known only from the photos filed in it, is searched flat (incategory:), a place's only when it is a
        site: one with more than PLACE_SUBCATS_MAX subcategories is a city, region or sea, and the coordinates are
        used instead, or nothing.
      - Its words must keep the name's order ("Concrete Roman" is a typeface), and a bracketed qualifier the topic
        does not name makes it a namesake ("Zhang Heng (actress)"). A category the topic's approved `photos` (credit
        records) are filed in also counts as its own (two photos, or one when a name search found it).
      - Then one the photos are filed in (an opus caementicium over a TV series called "Mystery!"), then the fuller
        name ("Claw of Archimedes" over "Archimedes (genus)", "Colossi of Memnon" over "Memnon"; words that are not
        the name's count against it), then the one the photos are filed in most, then the one their text names
        most, then the one found from the most specific search (the title's part after its last "of", its first
        other name), then the order found.
    Kept in this process's memory for up to a day (lost on a restart; an empty answer is asked again within the
    hour, it may have been a failed search)."""
    if subject_anchor and not place:
        qid, category = subject_anchor.get("qid", ""), subject_anchor.get("category", "")
        if re.fullmatch(r"Q[1-9][0-9]*", str(qid)) and isinstance(category, str) and category.strip():
            return {"kind": "category", "name": category.removeprefix("Category:").strip(),
                    "deep": False, "qid": qid}
    category_search = category_search or find_categories
    loc = topic.get("location") or {}
    place = (place or "").strip()
    key = (topic.get("id"), place)
    if key in _ANCHORS:
        when, cached = _ANCHORS[key]
        if time.monotonic() - when < (ANCHOR_TTL if cached else NO_ANCHOR_TTL):
            return cached
    title = re.sub(r"\S*\d\S*", " ", topic.get("title") or "")   # "1,500-Mile" says nothing of the topic
    ident = str(topic.get("id") or "").replace("_", " ")
    names = [] if place else [" ".join(str(a).split()) for a in aliases or [] if str(a).strip()]
    # The name words: the title's, the id's and the first other name's (the pool's main search: "Houfeng Didong
    # Yi" for a title naming its inventor). Later other names are context searches ("Abu Qir Bay"), never names.
    name_words = [] if place else _words(title) + _words(ident) + (_words(names[0]) if names else [])
    place_words = _words(place or loc.get("name") or "")
    queries: List[str] = []
    if not place:
        parts = [" ".join(x.split()) for x in re.split(r":|&|\bof\b", title, flags=re.I)]
        parts = [re.sub(r"^(?:the|a|an)\s+", "", x, flags=re.I) for x in parts if x]
        queries += parts[::-1] + names + [" ".join(_words(title)), ident]   # the most specific first
    queries += [" ".join(place_words)] if place_words else []
    naming = {q for q in parts[::-1] + names[:1]} if not place else set()
    sequences = [_words(title) + place_words, _words(ident) + place_words] + ([_words(names[0]) + place_words] if names else [])
    texts = [_words(" ".join([str(im.get("title") or ""), str(im.get("description") or "")]
                             + [str(c) for c in im.get("categories") or []])) for im in photos or []]
    filed = [{normalize(str(c)) for c in im.get("categories") or []} for im in photos or []]
    scored = {}
    try:
        order = 0
        for rank, q in enumerate(dict.fromkeys(q.strip() for q in queries if q.strip())):
            for cat in category_search(q, limit=5, max_wait=max_wait):
                order += 1
                if cat in scored:
                    continue
                # The topic's photos filed in it: two of them, or one when a name search found it.
                in_pool = sum(normalize(cat.split(":", 1)[-1]) in f for f in filed)
                main = _category_words(cat)
                named = (bool(name_words) and bool(_matches(cat, name_words + place_words))
                         and _fullness(cat, name_words) > 0 and not _qualified_elsewhere(cat, name_words + place_words)
                         and _in_order(main, sequences)
                         # all of it the place's words: the place ("Bothnian Sea" for a sea's anomaly), not the topic
                         and not all(any(_similar(c, w) for w in place_words) for c in main))
                own = named or (not place and (in_pool >= 2 or (in_pool and q in naming)))
                if not (own or _matches(cat, place_words)):
                    continue
                mentions = sum(all(any(_similar(c, w) for w in text) for c in main) for text in texts)
                # The name's words it names, less unnamed qualifiers and less words that are not the name's
                # ("Damascus steel" over "Ancient City of Damascus", "Gol Gumbaz" over "Interior of Gol Gumbaz").
                if own:
                    full = _fullness(cat, name_words) - sum(1 for w in main if not any(_similar(w, x) for x in name_words))
                else:
                    full = _fullness(cat, place_words)
                scored[cat] = (own, in_pool > 0, full, in_pool, mentions, -rank, -order, named, cat)
        best = sorted(scored.values(), reverse=True)[:5]
        counts = _files([c[-1] for c in best], max_wait) if best else {}
        found = None
        for own, *_, named, cat in best:
            files, subcats = counts.get(cat, (0, 0))
            if not files and not (own and subcats):
                continue
            if not own and subcats > PLACE_SUBCATS_MAX:
                log.info("Commons category %s is a whole city or region (%d subcategories): not an anchor.", cat, subcats)
                continue
            # Deep only for a category named for the topic; one known only by where its photos are filed (a
            # building's, "the CIA headquarters" for an operation run there) is searched flat, as a place is.
            found = {"kind": "category", "name": cat.split(":", 1)[-1], "deep": named}
            break
    except Exception as e:  # the anchor only widens a search: never a reason for the beat's search to fail
        log.info("Commons category of %s not looked up (%s).", topic.get("id"), e)
        return None   # not cached: asked again next time
    if found is None and not place:
        lat, lon = _float(loc.get("latitude")), _float(loc.get("longitude"))
        if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
            found = {"kind": "near", "lat": lat, "lon": lon}
    _ANCHORS[key] = (time.monotonic(), found)
    return found


def anchor_filter(anchor: Optional[dict]) -> str:
    """The search keyword an anchor adds ("" for none)."""
    if not anchor:
        return ""
    if anchor["kind"] == "category":
        return f'{"deepcategory" if anchor.get("deep") else "incategory"}:"{anchor["name"]}"'
    return f"nearcoord:{NEAR_KM}km,{anchor['lat']:.4f},{anchor['lon']:.4f}"


def widen_queries(query: str, anchor: Optional[dict], keywords: Optional[List[str]] = None,
                  max_extra: int = WIDEN_MAX_EXTRA) -> List[str]:
    """Shorter forms of `query`, most specific first, each restricted to `anchor` (anchor_filter): its proper
    nouns, each title keyword, its first word; the last is the anchor alone (what the category or place has).
    [] without an anchor. A form that is the query again (filler aside) is left out."""
    restrict = anchor_filter(anchor)
    if not restrict or max_extra <= 0:
        return []
    words = [w for w in _words(query) if w.lower() not in _FILLER]
    same = {" ".join(sorted(normalize(w) for w in words))}
    forms: List[str] = []

    def add(extra: List[str]) -> None:
        key = " ".join(sorted({normalize(w) for w in extra}))
        if extra and key not in same:
            same.add(key)
            forms.append(" ".join(extra + [restrict]))
    add([w for w in words if w[:1].isupper()])
    for kw in keywords or []:
        add([w for w in _words(kw) if w.lower() not in _FILLER])
    add(words[:1])
    return forms[:max_extra - 1] + [restrict]


def _page_key(page) -> str:
    if isinstance(page, dict):
        return str(page.get("pageid") or page.get("title") or id(page))
    return repr(page)


def search_widened(query: str, anchor, keywords: Optional[List[str]] = None, limit: int = 50,
                   max_wait: int = 60, below: int = WIDEN_BELOW, max_extra: int = WIDEN_MAX_EXTRA):
    """(results, [(query, results), ...]): search(query), and when it finds fewer than `below` results, the
    widen_queries forms in order, at most `max_extra` more searches and none after one fails, until `below` are
    found; merged in order, each file once. `anchor` may be a function giving it, asked only when widening."""
    pages = list(search(query, limit=limit, max_wait=max_wait))
    tried = [(query, len(pages))]
    seen = {_page_key(p) for p in pages}
    if len(pages) >= below:
        return pages, tried
    anchor = anchor() if callable(anchor) else anchor
    for wider in widen_queries(query, anchor, keywords, max_extra):
        if len(pages) >= below:
            break
        try:
            _last_search.partial = False
            found = list(search(wider, limit=limit, max_wait=max_wait))
        except (requests.RequestException, ValueError) as e:
            log.info("Commons search %r failed (%s); no wider search after it, the results so far stand.", wider, e)
            tried.append((wider, -1))
            break
        if getattr(_last_search, "partial", False):
            # Only part of the category tree was searched: its results are not the category's, and the next form
            # would be cut the same way. Not used; no wider search with this anchor.
            log.info("Commons search %r covered only part of its category; not used, no wider search after it.", wider)
            tried.append((wider, -2))
            break
        tried.append((wider, len(found)))
        for p in found:
            if _page_key(p) not in seen:
                seen.add(_page_key(p))
                # Which wider form found it (candidates() passes it on): a form that names only the topic is the
                # whole category, whose files must name the beat's own subject to be looked at (anchor_only).
                pages.append({**p, "widened": wider} if isinstance(p, dict) else p)
    return pages, tried


_RESTRICT = re.compile(r'(?:deep|in)category:"[^"]*"|nearcoord:\S+')


def _name_words(names: List[str]) -> List[str]:
    """The words of names, a restrict's category name included ('incategory:"X"' gives X's words)."""
    out: List[str] = []
    for name in names:
        named = re.search(r'category:"([^"]*)"', name or "")
        text = named.group(1) if named else _RESTRICT.sub(" ", name or "")
        out += [normalize(w) for w in _words(text) if w.lower() not in _FILLER]
    return out


def anchor_only(form: str, topic_names: List[str]) -> bool:
    """Whether a widened search `form` names nothing but its anchor and the topic (`topic_names`: its title, place,
    other names): 'Derinkuyu incategory:"Underground City of Derinkuyu"' or the anchor alone, which return the whole
    category (run audit 2026-10-29: a beat about a church set aside 20 underground photos in a row)."""
    names = _name_words(list(topic_names) + _RESTRICT.findall(form))
    own = [w for w in _words(_RESTRICT.sub(" ", form)) if w.lower() not in _FILLER]
    return not [w for w in own if not any(_similar(w, n) for n in names)]


def beat_words(query: str, keywords: List[str], topic_names: List[str]) -> List[str]:
    """The beat's own words: those of its search and title keywords that are not the topic's names (nor filler, nor
    shorter than four letters). "church exterior Derinkuyu" for Derinkuyu: ["church", "exterior"]."""
    names = _name_words(list(topic_names))
    out: List[str] = []
    for w in _words(" ".join([query or ""] + list(keywords or []))):
        n = normalize(w)
        if len(n) >= 4 and n not in _FILLER and not any(_similar(n, m) for m in names) and n not in out:
            out.append(n)
    return out


def mentions(cand: dict, words: List[str]) -> bool:
    """Whether a candidate's title, description or categories name one of `words` (a plural, a stem or a one-letter
    spelling difference counts: commons._similar)."""
    text = normalize(" ".join([cand.get("title", ""), cand.get("description", "")] + list(cand.get("categories") or [])))
    tokens = set(re.findall(r"[^\W_]+", text))

    def same(w: str, t: str) -> bool:
        return _similar(w, t) or (min(len(w), len(t)) >= 5 and (t.startswith(w) or w.startswith(t)))
    return any(same(w, t) for w in words for t in tokens)


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
               min_height: int = MIN_HEIGHT, essential_titles: dict | None = None) -> Iterator[dict]:
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
        essential = (essential_titles or {}).get(page["title"], "")
        if not isinstance(essential, str):
            essential = ""
        essential = essential.strip()
        if w <= 0 or h <= 0:
            continue
        if not essential and (w * h < min_pixels or (h < min_height and w < min_height)
                              or vertical_crop_width(w, h) < MIN_VERTICAL_CROP):
            continue

        # The original file: Wikimedia's cached 2880-px rendition measured 32-37 dB PSNR against it at
        # final frame size (its re-compression and sharpening change fine texture). Downloads stay one
        # at a time with a pause between; only files over 35 MB use the rendition.
        orig_url = info.get("url")
        target_url = orig_url if orig_url and 0 < info.get("size", 0) <= 35_000_000 else (info.get("thumburl") or orig_url)
        # A width-limited rendition can discard the height a portrait crop needs.
        # Prefer the original when it improves crop headroom and fits the byte cap.
        if target_url != orig_url:
            th = info.get("thumbheight") or min(h, 2880)  # search requests iiurlheight=2880
            tw = info.get("thumbwidth") or int(w * th / h)
            if (vertical_crop_width(tw, th) < NATIVE_VERTICAL_CROP
                    and orig_url and 0 < info.get("size", 0) <= config.COMMONS_DOWNLOAD_MAX_MB * 1024 * 1024):
                target_url = orig_url
            else:
                w, h = tw, th
        if not essential and vertical_crop_width(w, h) < MIN_VERTICAL_CROP:
            continue

        # A CC BY file nobody can be credited for is refused here, with the reason, rather than fetched,
        # shown on the review card and then failing the release's rights check (development, 2026-10-26;
        # audit 2026-10-28).
        artist = sanitise_credit_text(_author(meta)) or "Unknown"  # no e-mail, link, handle or hashtag kept (QF-42)
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
            **({"essential_reason": essential, "presentation": "full_image"} if essential else {}),
            # What the file says it shows: used to rank relevance and to caption it for the planner.
            "description": _plain(meta.get("ImageDescription", {}).get("value", ""))[:400],
            "categories": [c for c in _plain(meta.get("Categories", {}).get("value", "")).split("|") if c][:15],
            "lat": _float(meta.get("GPSLatitude", {}).get("value")),
            "lon": _float(meta.get("GPSLongitude", {}).get("value")),
            **({"widened": page["widened"]} if page.get("widened") else {}),   # search_widened's form
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
    (metadata, see relevance()), then usable 9:16 crop with a portrait/square bonus; ties keep search order."""
    from .credits import is_share_alike
    for c in cands:
        c["relevance"] = round(relevance(c, keywords or [], place), 2)
    return sorted(cands, key=lambda c: (is_share_alike(c.get("license", "")), -c["relevance"],
                                        -crop_rank(c)))


def _capped_body(resp, cap: int) -> bytes:
    """A downloaded body of at most cap bytes, read no further than cap + one 64 KiB chunk: ValueError past it (or when the
    server declares more). A response already read (no iter_content) is measured as it is."""
    declared = str((getattr(resp, "headers", None) or {}).get("Content-Length", "")).strip()
    if declared.isdigit() and int(declared) > cap:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
        raise ValueError(f"Download too large: {int(declared):,} bytes declared (cap {cap:,})")
    if not hasattr(resp, "iter_content"):
        body = resp.content
    else:
        parts, size = [], 0
        try:
            for chunk in resp.iter_content(1 << 16):
                parts.append(chunk)
                size += len(chunk)
                if size > cap:
                    break
        finally:
            resp.close()
        body = b"".join(parts)
    if len(body) > cap:
        raise ValueError(f"Download too large: over {cap:,} bytes (cap)")
    return body


def download_jpeg(
    url: str,
    dest: Path,
    max_wait: int = 30,
    min_pixels: Optional[int] = None,
    min_height: Optional[int] = None,
    provider: str | None = None,
    min_crop_width: int | None = None,
    preserve_source: bool = False,
) -> tuple[int, int]:
    """The picture at `url` saved as a JPEG at `dest`. The URL comes from a search answer (QF-44): it and every
    redirect hop must be HTTPS on `provider`'s hosts (footage.ALLOWED_HOSTS; any still-image provider's fixed hosts
    when None) and public (src/url_guard). Raises ValueError (url_guard.RefusedURL) for any other URL."""
    from . import url_guard
    from .footage import url_allowed
    resp = url_guard.follow(url, lambda u, **kw: _get(u, timeout=30, max_wait=max_wait, stream=True, **kw),
                            allowed=lambda u: url_allowed(u, provider))
    # Untrusted bodies above the cap are refused; accepted bytes keep Pillow's own pixel limit.
    data = _capped_body(resp, config.COMMONS_DOWNLOAD_MAX_MB * 1024 * 1024)
    with Image.open(io.BytesIO(data)) as im:
        im.verify()  # reject corrupt / non-image payloads
    with Image.open(io.BytesIO(data)) as raw:
        # Upright first: re-encoding drops the EXIF orientation, and a portrait photo stored sideways with a
        # rotate flag reached a video turned over (Unfinished Obelisk, beat 10, 2026-10-25).
        im = ImageOps.exif_transpose(raw)
        w, h = im.size
        if min_pixels and (w * h < min_pixels):
            raise ValueError(f"Resolution too low: {w}x{h} ({w*h:,} px < {min_pixels:,})")
        if min_height and (h < min_height and w < min_height):
            raise ValueError(f"Dimensions too small: {w}x{h} (both edges < {min_height})")
        if min_crop_width and vertical_crop_width(w, h) < min_crop_width:
            raise ValueError(f"9:16 crop too small: {vertical_crop_width(w, h)} px < {min_crop_width}")
        im.convert("RGB").save(dest, "JPEG", quality=92)  # re-encode: strips metadata and odd formats
        from .photo_quality import record_source
        record_source(dest, data)
        if preserve_source:
            # Archive accepted original bytes separately; only the upright JPEG enters the pool.
            Path(str(dest) + ".source").write_bytes(data)
        return w, h
