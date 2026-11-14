"""
src/free_evidence.py
====================
Evidence for fact sheets at no cost: the topic's Wikipedia article (free MediaWiki API, plain-text
extract) plus a few of the external pages that article cites, fetched directly. Quotes are the
article's and the pages' own sentences that carry numbers, dates or the topic's key words.

Wikipedia counts as a tier-2 source. A page the article cites is evidence a quote can rest on, but it
inherits tier 3 and never corroborates (QF-40): whoever edits the article chooses its links, so the article
and a page it cites are one source. Each quote records its fetch path (fact_sheet.FETCH_*).
Security: pages are untrusted data. HTTPS only, size-capped, text extracted (no scripts run), and the
text is only ever quoted as evidence, never followed as instructions.
"""

import html
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from . import url_guard
from .commons import HEADERS

log = logging.getLogger(__name__)

# Fetch paths, as the fact sheet records them (fact_sheet.FETCH_*; repeated here so this module stays light).
FETCH_WIKIPEDIA = "wikipedia"
FETCH_WIKIPEDIA_CITED = "wikipedia_cited"
FETCH_OPENALEX = "openalex"

WIKI_API = "https://en.wikipedia.org/w/api.php"
MAX_PAGE_BYTES = 2_000_000
MAX_REFERENCES = 6
SKIP_HOSTS = ("wikipedia.org", "wikimedia.org", "archive.org", "worldcat.org",
              "google.com", "books.google", "youtube.com", "facebook.com", "twitter.com", "x.com",
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


def _html(ctype: str) -> bool:
    return "html" in (ctype or "").lower()


def page_text(url: str) -> Optional[str]:
    """Visible text of a page (HTTPS only, size-capped). None if it can't be fetched as HTML.

    The URL comes from an article's external links, i.e. from whoever edited it (QF-44): redirects are followed by
    hand and every hop must be HTTPS to a public host (src/url_guard: no private, loopback or link-local address, no
    local name). A HEAD first turns away what says it is not a page before its body is asked for."""
    headers = {"User-Agent": HEADERS["User-Agent"]}
    try:
        head = url_guard.follow(url, requests.head, headers=headers, timeout=20)
        if head.status_code == 200 and head.headers.get("Content-Type") and not _html(head.headers["Content-Type"]):
            return None
        with url_guard.follow(url, requests.get, headers=headers, timeout=20, stream=True) as r:
            if r.status_code != 200 or not _html(r.headers.get("Content-Type", "")):
                return None
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_PAGE_BYTES:
                    break
    except url_guard.RefusedURL as e:
        log.info("Cited page not fetched: %s", e)
        return None
    except requests.RequestException:
        return None
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def sentences(text: str) -> List[str]:
    text = re.sub(r"=+ [^=]+ =+", " ", text)  # Wikipedia section headings
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text) if 30 <= len(s.strip()) <= 500]


STOPWORDS = {"the", "and", "with", "from", "that", "this", "into", "over", "were", "what", "when", "which", "their"}


def _topic_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in STOPWORDS}


def fetch_openalex_scholarly_evidence(query: str, max_works: int = 3) -> Tuple[List[dict], List[dict]]:
    """Abstract sentences from scholarly works about the topic (open OpenAlex API).

    A work counts as tier-1 evidence only when OpenAlex lists it as an article or review in a
    journal; other venues (trade magazines, repositories, preprints) are tier 3 and never
    labelled peer-reviewed. Works that share no meaningful word with the topic are skipped:
    search relevance alone returns unrelated papers."""
    import urllib.parse
    url = f"https://api.openalex.org/works?search={urllib.parse.quote(query)}&per-page={max_works}"
    headers = {"User-Agent": HEADERS["User-Agent"]}
    wanted = _topic_words(query)
    quotes, sources = [], []
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            for w in r.json().get("results", []):
                title = w.get("title") or ""
                doi = w.get("doi") or ""
                abstract_inverted = w.get("abstract_inverted_index") or {}
                word_pos = sorted((p, word) for word, positions in abstract_inverted.items() for p in positions)
                abstract_text = " ".join(word for _, word in word_pos)
                if not doi or not abstract_text or not (wanted & _topic_words(title + " " + abstract_text)):
                    continue
                venue = ((w.get("primary_location") or {}).get("source") or {})
                journal = w.get("type") in ("article", "review") and venue.get("type") == "journal"
                label = (f"Peer-reviewed journal article ({venue.get('display_name')}): {title}" if journal
                         else f"Scholarly work: {title}")
                sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abstract_text) if len(s.strip()) >= 30]
                for s in sents[:3]:
                    quotes.append({"url": doi, "title": label, "quote": s, "tier": 1 if journal else 3,
                                   "fetch": FETCH_OPENALEX})
                sources.append({"title": label, "url": doi})
    except Exception as e:
        log.warning("OpenAlex scholarly evidence lookup failed: %s", e)
    return quotes, sources


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
                # reached through the article's links: tier 3 whatever its host, never a second site
                picked.append({"url": src_url, "title": src_title, "quote": s, "tier": 3,
                               "fetch": FETCH_WIKIPEDIA_CITED})
                n += 1
                if n >= limit:
                    break
        return n

    # Ingest peer-reviewed scholarly evidence from OpenAlex
    scholarly_quotes, scholarly_sources = fetch_openalex_scholarly_evidence(topic.get("title", ""), max_works=3)
    if scholarly_quotes:
        picked.extend(scholarly_quotes)
        sources.extend(scholarly_sources)

    for idx, title in enumerate(titles):
        url, text, refs = article(title)
        # The primary subject article (idx == 0) can use numbers or keywords; secondary location pages (idx > 0) MUST match topic keywords to avoid regional history pollution
        require_keywords = (idx > 0)
        n = 0
        for s in sentences(text):
            low = s.lower()
            if require_keywords:
                matched = any(k in low for k in keys)
            else:
                matched = re.search(r"\d", s) or any(k in low for k in keys)
            if matched:
                picked.append({"url": url, "title": f"Wikipedia: {title}", "quote": s, "fetch": FETCH_WIKIPEDIA})
                n += 1
                if n >= 45:
                    break
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
    log.info("Free evidence for %s: %d quotes (including %d scholarly) from Wikipedia + %d cited pages.",
             topic.get("id"), len(picked), len(scholarly_quotes), fetched)
    return picked[:max_quotes], sources
