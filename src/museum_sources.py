"""
src/museum_sources.py
=====================
Authoritative open-access image sources beyond Wikimedia Commons, each returning candidates in the
same shape as commons.candidates() so ranking, the vision review and the credits are shared:

  - The Metropolitan Museum of Art: objects marked isPublicDomain (CC0), with dated, attributed
    museum records. Search: /public/collection/v1.1/search (v1 was retired on 2026-10-01).
  - NASA Image and Video Library: images credited to NASA (US government works); items crediting
    another party are skipped, since NASA notes third-party material may be copyrighted.
  - Smithsonian Open Access: media whose usage is CC0. Needs a free api.data.gov key
    (SMITHSONIAN_API_KEY); without one the public DEMO_KEY allows only a few calls an hour.

The Library of Congress is not included: loc.gov answers automated clients with a bot challenge,
and getting around that would not be appropriate.

Requests are made one at a time with a pause between, and identify the bot like commons.HEADERS.
"""

import html
import logging
import time
from typing import Dict, List, Optional

import requests

from .commons import HEADERS
from .credits import normalize

log = logging.getLogger(__name__)

MET = "https://collectionapi.metmuseum.org/public/collection"
NASA = "https://images-api.nasa.gov"
SI = "https://api.si.edu/openaccess/api/v1.0/search"
PAUSE = 1.0


def _get(url: str, params: Optional[dict] = None, timeout: float = 30, headers: Optional[dict] = None) -> Optional[dict]:
    time.sleep(PAUSE)
    try:
        r = requests.get(url, params=params, headers={**HEADERS, **(headers or {})}, timeout=timeout)
        if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
            log.info("%s answered HTTP %s; skipping.", url.split("/")[2], r.status_code)
            return None
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.info("%s unavailable (%s); skipping.", url.split("/")[2], e)
        return None


def _candidate(**kw) -> Dict:
    base = {"title": "", "download_url": "", "source_url": "", "artist": "Unknown", "license": "",
            "license_url": "", "attribution_required": False, "year": None, "width": 0, "height": 0,
            "grade": 0, "description": "", "categories": [], "lat": None, "lon": None, "provider": ""}
    return {**base, **kw}


def met_search(query: str, limit: int = 12) -> List[Dict]:
    found = _get(f"{MET}/v1.1/search", {"q": query, "hasImages": "true", "limit": limit})
    out = []
    for oid in ((found or {}).get("objectIDs") or [])[:limit]:
        o = _get(f"{MET}/v1/objects/{oid}")
        if not o or not o.get("isPublicDomain") or not o.get("primaryImage"):
            continue
        desc = ", ".join(x for x in (o.get("objectDate"), o.get("culture") or o.get("country"), o.get("medium"),
                                    o.get("department")) if x)
        out.append(_candidate(
            title=f"{o.get('title', 'Object')} (The Met {oid})", download_url=o["primaryImage"],
            source_url=o.get("objectURL", ""), artist=o.get("artistDisplayName") or "The Metropolitan Museum of Art",
            license="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            description=desc, categories=[c.get("term", "") for c in (o.get("tags") or [])][:10], provider="met"))
    return out


def nasa_search(query: str, limit: int = 12) -> List[Dict]:
    found = _get(f"{NASA}/search", {"q": query, "media_type": "image"})
    out = []
    for item in ((found or {}).get("collection") or {}).get("items", [])[:limit]:
        d = (item.get("data") or [{}])[0]
        third_party = d.get("secondary_creator") or d.get("photographer") or ""
        if third_party and "nasa" not in third_party.lower():
            continue  # credited to another party: may be copyrighted
        credit = third_party or f"NASA ({d.get('center') or 'NASA'})"
        assets = _get(f"{NASA}/asset/{d['nasa_id']}") or {}
        hrefs = [a.get("href", "") for a in (assets.get("collection") or {}).get("items", [])]
        best = next((h for h in hrefs if "~orig." in h and h.lower().endswith((".jpg", ".jpeg", ".png"))), None) \
            or next((h for h in hrefs if "~large." in h), None)
        if not best:
            continue
        out.append(_candidate(
            title=f"{d.get('title', d['nasa_id'])} (NASA {d['nasa_id']})", download_url=best.replace("http://", "https://"),
            source_url=f"https://images.nasa.gov/details/{d['nasa_id']}", artist=credit, license="Public domain",
            license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
            description=html.unescape(d.get("description") or "")[:400], categories=(d.get("keywords") or [])[:10],
            year=int(d["date_created"][:4]) if (d.get("date_created") or "")[:4].isdigit() else None, provider="nasa"))
    return out


def smithsonian_search(query: str, limit: int = 12) -> List[Dict]:
    from . import config
    key = config.SMITHSONIAN_API_KEY or "DEMO_KEY"
    # The key goes in the X-Api-Key header (api.data.gov accepts it there), never in the URL or logs.
    found = _get(SI, {"q": f"{query} AND online_media_type:Images", "rows": limit}, headers={"X-Api-Key": key})
    out = []
    for row in ((found or {}).get("response") or {}).get("rows", []):
        content = row.get("content") or {}
        dnr = content.get("descriptiveNonRepeating") or {}
        for m in ((dnr.get("online_media") or {}).get("media") or []):
            if (m.get("usage") or {}).get("access") != "CC0":
                continue
            resources = m.get("resources") or []
            best = next((r.get("url") for r in resources if "High-resolution" in (r.get("label") or "")), None) \
                or m.get("content")
            if not best:
                continue
            notes = " ".join(n.get("content", "") for n in (content.get("freetext") or {}).get("notes", [])[:2])
            out.append(_candidate(
                title=f"{row.get('title', 'Object')} (Smithsonian {dnr.get('unit_code', '')})", download_url=best,
                source_url=dnr.get("record_link") or dnr.get("guid", ""), artist=dnr.get("data_source") or "Smithsonian",
                license="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                description=notes[:400], provider="smithsonian"))
            break  # one image per object
    return out


SOURCES = {"met": met_search, "nasa": nasa_search, "smithsonian": smithsonian_search}


def search_all(query: str, keywords: List[str], sources=tuple(SOURCES)) -> List[Dict]:
    """Candidates from the museum sources whose title or description names the subject."""
    kws = [normalize(k) for k in keywords]
    out = []
    for name in sources:
        for cand in SOURCES[name](query):
            text = normalize(f"{cand['title']} {cand['description']} {' '.join(cand['categories'])}")
            if not kws or any(k in text for k in kws):
                out.append(cand)
    return out
