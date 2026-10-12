"""
src/free_evidence.py
====================
Evidence for fact sheets at no cost: the topic's Wikipedia article (free MediaWiki API, plain-text
extract) plus a few of the external pages that article cites, fetched directly. Quotes are the
article's and the pages' own sentences that carry numbers, dates or the topic's key words.

Wikipedia counts as a tier-2 source; the cited pages often add a tier-1 source or a second site,
which the fact-sheet rules need for numbers and dates.
Security: pages are untrusted data. HTTPS only, size-capped, text extracted (no scripts run), and the
text is only ever quoted as evidence, never followed as instructions.
"""

import html
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from .commons import HEADERS

log = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
MAX_PAGE_BYTES = 2_000_000
MAX_REFERENCES = 6
SKIP_HOSTS = ("wikipedia.org", "wikimedia.org", "wikidata.org", "archive.org", "doi.org", "worldcat.org",
              "google.com", "books.google", "jstor.org", "youtube.com", "facebook.com", "twitter.com", "x.com",
              "toolforge.org", "bnf.fr", "idref.fr", "viaf.org", "loc.gov/authorities", "d-nb.info", "openstreetmap.org",
              "geonames.org", "isni.org", "catalogue.", "id.loc.gov", "kb.nl", "nla.gov.au/anbd")  # catalogues, maps, IDs


def _get(url: str, params: Optional[dict] = None, timeout: float = 20) -> requests.Response:
    return requests.get(url, params=params, headers=HEADERS, timeout=timeout)


def find_article(query: str) -> Optional[str]:
    data = _get(WIKI_API, {"action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"}).json()
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def article(title: str) -> Tuple[str, str, List[str]]:
    """(url, plain text, external reference URLs) for a Wikipedia article."""
    data = _get(WIKI_API, {"action": "query", "prop": "extracts|extlinks", "explaintext": 1, "titles": title,
                           "ellimit": 100, "format": "json", "redirects": 1}).json()
    page = next(iter(data.get("query", {}).get("pages", {}).values()), {})
    url = f"https://en.wikipedia.org/wiki/{page.get('title', title).replace(' ', '_')}"
    links = [l.get("*") or l.get("url") for l in page.get("extlinks", []) if (l.get("*") or l.get("url"))]
    return url, page.get("extract", ""), links


def page_text(url: str) -> Optional[str]:
    """Visible text of a page (HTTPS only, size-capped). None if it can't be fetched as HTML."""
    if urlparse(url).scheme != "https":
        return None
    try:
        with requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20, stream=True) as r:
            if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
                return None
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    break
    except requests.RequestException:
        return None
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def sentences(text: str) -> List[str]:
    text = re.sub(r"=+ [^=]+ =+", " ", text)  # Wikipedia section headings
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text) if 30 <= len(s.strip()) <= 500]


def quotes_for(topic: Dict, keywords: List[str], max_quotes: int = 90) -> Tuple[List[dict], List[dict]]:
    """([{"url", "title", "quote"}], [{"title", "url"}]): evidence sentences and the sources they came from."""
    loc = topic.get("location", {})
    # The subject's article and the place's article (e.g. "Roman concrete" and "Cosa"), when different.
    titles = []
    for q in (topic.get("title", ""), loc.get("name", "")):
        t = find_article(q) if q else None
        if t and t not in titles:
            titles.append(t)
    if not titles:
        return [], []
    keys = [k.lower() for k in keywords if len(k) > 3]
    picked: List[dict] = []
    sources: List[dict] = []
    links: List[str] = []

    def take(src_url: str, src_title: str, body: str, limit: int) -> int:
        n = 0
        for s in sentences(body):
            low = s.lower()
            if re.search(r"\d", s) or any(k in low for k in keys):
                picked.append({"url": src_url, "title": src_title, "quote": s})
                n += 1
                if n >= limit:
                    break
        return n

    for title in titles:
        url, text, refs = article(title)
        take(url, f"Wikipedia: {title}", text, 45)
        sources.append({"title": f"Wikipedia: {title}", "url": url})
        links += refs
    fetched = 0
    for link in links:
        host = (urlparse(link).hostname or "").lower()
        if fetched >= MAX_REFERENCES or any(h in host for h in SKIP_HOSTS):
            continue
        body = page_text(link)
        if not body:
            continue
        if take(link, host, body, 8):
            sources.append({"title": host, "url": link})
            fetched += 1
    log.info("Free evidence for %s: %d quotes from Wikipedia + %d cited pages.", topic.get("id"), len(picked), fetched)
    return picked[:max_quotes], sources
