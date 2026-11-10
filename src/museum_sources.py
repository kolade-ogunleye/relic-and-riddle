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

  - Europeana: Europe's museums, libraries and archives, each record carrying its holder's rights statement.
    Only what may be reused, edited and published is taken: public domain, CC0, CC BY (and CC BY-SA where
    IMAGES_ALLOW_SHARE_ALIKE allows it); in copyright, NonCommercial or NoDerivatives never (operator,
    2026-10-28). Needs EUROPEANA_API_KEY; off without one.

Further sources (2026-11-02), each off by default (config MUSEUM_*_ENABLED) because their API parameters could not
be checked against the official documentation from the development container; every one is marked UNVERIFIED where
it is called, and must be checked live before it is enabled:

  - Openverse (api.openverse.org): CC0, public-domain mark, CC BY (and CC BY-SA where allowed) only; the file must
    be on a host footage.ALLOWED_HOSTS["openverse"] lists.
  - Library of Congress (www.loc.gov JSON API): only items whose rights advisory says there are no known
    restrictions. Earlier (2026-10) loc.gov answered automated clients with a bot challenge, and getting around that
    would not be appropriate: a challenge page is not JSON, so it is simply skipped (nothing is worked around).
  - Art Institute of Chicago (api.artic.edu): is_public_domain artworks only (CC0), images from its IIIF server.
  - The US National Archives catalog is not included: its catalog API (v2) is understood to need an API key
    (not verified from its documentation here), and the brief was to skip a source that needs one.

Requests are made one at a time with a pause between, and identify the bot like commons.HEADERS.

Used by the topic's pool fetch (scripts/fetch_commons_images.py) and, since 2026-10-29, by the search for a single
narration line when Commons has nothing for it (src/beat_images.py): the same picture checks follow (the look at
the pixels, burned-in text, credits recorded). Those searches are cached (CACHE_FILE) so a resume or a re-render
asks no museum again.
"""

import html
import logging
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from . import config
from .commons import HEADERS
from .credits import normalize
from .image_rights import basis, public_domain, recorded_basis_ok

log = logging.getLogger(__name__)

MET = "https://collectionapi.metmuseum.org/public/collection"
NASA = "https://images-api.nasa.gov"
SI = "https://api.si.edu/openaccess/api/v1.0/search"
EUROPEANA = "https://api.europeana.eu/record/v2/search.json"
OPENVERSE = "https://api.openverse.org/v1/images/"
LOC = "https://www.loc.gov/photos/"
ARTIC = "https://api.artic.edu/api/v1/artworks/search"
ARTIC_IIIF = "https://www.artic.edu/iiif/2"
PAUSE = 1.0
_last_request = 0.0
_failures = 0     # requests that did not answer: a search that saw one is not cached (asked again next time)


def _get(url: str, params: Optional[dict] = None, timeout: float = 30, headers: Optional[dict] = None) -> Optional[dict]:
    global _last_request, _failures
    # The pause is between requests (one at a time, as before); the first of a burst is not held back (2026-10-29).
    wait = PAUSE - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    try:
        r = requests.get(url, params=params, headers={**HEADERS, **(headers or {})}, timeout=timeout)
        if r.status_code != 200 or "json" not in r.headers.get("Content-Type", ""):
            log.info("%s answered HTTP %s; skipping.", url.split("/")[2], r.status_code)
            if r.status_code != 404:   # a missing object is an answer; a refusal or an outage is not
                _failures += 1
            return None
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.info("%s unavailable (%s); skipping.", url.split("/")[2], e)
        _failures += 1
        return None


def _candidate(**kw) -> Dict:
    base = {"title": "", "download_url": "", "source_url": "", "artist": "Unknown", "license": "",
            "license_url": "", "attribution_required": False, "year": None, "width": 0, "height": 0,
            "grade": 0, "description": "", "categories": [], "lat": None, "lon": None, "provider": ""}
    record = {**base, **kw}
    if str(record["license"]).lower().startswith("cc0") and not record.get("worldwide_basis"):
        record["worldwide_basis"] = basis("cc0", record["source_url"])
    return record


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
        credits = [str(d.get(field) or "") for field in ("secondary_creator", "photographer")]
        credits += re.findall(r"(?:image\s+)?credit\s*:\s*([^\n<]+)", str(d.get("description") or ""), re.IGNORECASE)
        third_party = next((value for value in credits if value), "")
        own_names = {"nasa", "jpl", "jsc", "gsfc", "ksc", "goddard", "nasa goddard", "nasa jpl"}
        if any(part.strip().lower() not in own_names for value in credits if value
               for part in re.split(r"[/&,;]", value) if part.strip()):
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
            worldwide_basis=basis("nasa-own-work", "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                                  third_party=False, credit=credit, scope="NASA factual-use permission; no endorsement"),
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


def open_licence(rights_url: str):
    """(short licence name, licence URL) for a rights statement we may reuse, edit and publish, else None:
    public domain, CC0, CC BY, CC BY-SA (the caller's licence gate decides on share-alike). In copyright,
    educational use only, NonCommercial and NoDerivatives are never reusable here (ERT's Antikythera films on
    Europeana are "In Copyright - Educational Use Permitted": operator, 2026-10-28)."""
    import re
    from urllib.parse import urlparse
    url = (rights_url or "").strip().replace("https://", "http://").rstrip("/")
    if urlparse(url).hostname != "creativecommons.org":
        return None
    if "/publicdomain/mark/" in url:
        return "Public domain", rights_url
    if "/publicdomain/zero/" in url:
        return "CC0", rights_url
    m = re.search(r"creativecommons\.org/licenses/(by|by-sa)/(\d\.\d)", url)
    if m:
        return ("CC BY-SA " if m.group(1) == "by-sa" else "CC BY ") + m.group(2), rights_url
    return None  # InC, InC-EDU, NC, ND, unknown


def europeana_search(query: str, limit: int = 12, media_type: str = "IMAGE") -> List[Dict]:
    from . import config
    from .credits import license_allowed
    if not config.EUROPEANA_API_KEY:
        return []
    # The key in the X-Api-Key header: Europeana deprecated the wskey parameter, and a URL can end up in logs
    found = _get(EUROPEANA, {"query": query, "reusability": "open", "media": "true", "qf": f"TYPE:{media_type}",
                             "rows": limit, "profile": "rich"}, headers={"X-Api-Key": config.EUROPEANA_API_KEY})
    out = []
    for it in (found or {}).get("items") or []:
        rights = open_licence((it.get("rights") or [""])[0])
        media = (it.get("edmIsShownBy") or [""])[0]
        if media.startswith("http://"):  # many holders list http; it is asked for over https or not at all
            media = "https://" + media[len("http://"):]
        if not rights or not license_allowed(rights[0]) or not media.startswith("https://"):
            continue  # a page to view, not a file, is not taken either
        if public_domain(rights[0]):
            continue  # a generic aggregated PD mark supplies no source/life-term evidence
        holder = (it.get("dataProvider") or [""])[0]
        names = [v for vals in (it.get("dcCreatorLangAware") or {}).values() for v in vals] + list(it.get("dcCreator") or [])
        creator = next((n for n in names if n and not n.startswith("http")), "")  # a name, not an authority link
        year = next((int(y[:4]) for y in it.get("year") or [] if str(y)[:4].isdigit()), None)
        out.append(_candidate(
            title=f"{(it.get('title') or ['Untitled'])[0]} (Europeana, {holder})"[:200], download_url=media,
            source_url=it.get("guid") or (it.get("edmIsShownAt") or [""])[0], artist=creator or holder or "Europeana",
            license=rights[0], license_url=rights[1], attribution_required=not rights[0].startswith(("Public", "CC0")),
            description=" ".join((it.get("dcDescription") or [])[:1])[:400], categories=(it.get("edmConceptPrefLabelLangAware") or {}).get("en", [])[:10],
            year=year, provider="europeana"))
    return out


def _file_allowed(url: str, provider: str) -> bool:
    """An https file on one of the provider's hosts (footage.ALLOWED_HOSTS)."""
    from urllib.parse import urlparse

    from .footage import _host_allowed
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and _host_allowed(parsed.hostname or "", provider)


def openverse_licence(code: str, version: str = "") -> tuple | None:
    """(licence name as the licence filter reads it, licence URL) for an Openverse licence code we may reuse, edit
    and publish; None for NoDerivatives, NonCommercial or anything unknown."""
    code = (code or "").strip().lower()
    if code == "pdm":
        return "Public domain", "https://creativecommons.org/publicdomain/mark/1.0/"
    if code == "cc0":
        return "CC0", "https://creativecommons.org/publicdomain/zero/1.0/"
    if code in ("by", "by-sa"):
        version = (version or "4.0").strip()
        return f"CC {code.upper()} {version}", f"https://creativecommons.org/licenses/{code}/{version}/"
    return None


def openverse_search(query: str, limit: int = 12) -> list[dict]:
    """UNVERIFIED (api.openverse.org could not be fetched on 2026-11-02): GET /v1/images/ with q, license (comma-
    separated codes), page_size, mature; results carry url, license, license_version, license_url, creator,
    foreign_landing_url, title, tags[].name, width, height, provider. Anonymous use is rate limited."""
    if not config.MUSEUM_OPENVERSE_ENABLED:
        return []
    found = _get(OPENVERSE, {"q": query, "license": "cc0,pdm,by,by-sa", "page_size": limit, "mature": "false"})
    out = []
    for it in (found or {}).get("results") or []:
        if not isinstance(it, dict):
            continue
        rights = openverse_licence(it.get("license", ""), it.get("license_version", ""))
        url = str(it.get("url") or "")
        if not rights or not _file_allowed(url, "openverse"):
            continue
        verified = _verify_openverse_source(it)
        if not verified:
            continue
        rights = verified["license"], verified["license_url"]
        from .credits import license_allowed
        if not license_allowed(rights[0]):
            continue
        if public_domain(rights[0]) and not recorded_basis_ok(verified):
            continue
        out.append(_candidate(
            title=f"{it.get('title') or query} (Openverse, {it.get('provider') or it.get('source') or 'unknown'})"[:200],
            download_url=url, source_url=it.get("foreign_landing_url") or url,
            artist=verified.get("artist") or it.get("creator") or "Unknown",
            license=rights[0], license_url=rights[1],
            worldwide_basis=verified.get("worldwide_basis"),
            license_verified_at=verified["license_verified_at"],
            license_verification_source=verified["license_verification_source"],
            attribution_required=not rights[0].startswith(("Public", "CC0")),
            width=int(it.get("width") or 0), height=int(it.get("height") or 0),
            categories=[t.get("name", "") for t in it.get("tags") or [] if isinstance(t, dict)][:10],
            provider="openverse"))
    return out


def _verify_openverse_source(item: dict) -> dict | None:
    """Recheck the exact Flickr photo or Commons file; other aggregations stay held."""
    from urllib.parse import unquote, urlparse

    from . import commons
    source, media = urlparse(str(item.get("foreign_landing_url") or "")), urlparse(str(item.get("url") or ""))
    if source.scheme != "https":
        return None
    if source.hostname in ("flickr.com", "www.flickr.com"):
        match = re.fullmatch(r"/photos/[^/]+/([0-9]+)/?", source.path)
        file_id = re.match(r"([0-9]+)_", media.path.rsplit("/", 1)[-1])
        if not config.FLICKR_API_KEY or not match or not file_id or match[1] != file_id[1]:
            return None
        if media.hostname != "live.staticflickr.com" and not (media.hostname or "").endswith(".staticflickr.com"):
            return None
        common = {"api_key": config.FLICKR_API_KEY, "format": "json", "nojsoncallback": 1}
        result = _get("https://www.flickr.com/services/rest/", {
            **common, "method": "flickr.photos.getInfo", "photo_id": match[1]}) or {}
        photo = result.get("photo") or {}
        if (result.get("stat") != "ok" or str(photo.get("id")) != match[1]
                or str((photo.get("visibility") or {}).get("ispublic")) != "1"):
            return None
        licenses = _get("https://www.flickr.com/services/rest/", {
            **common, "method": "flickr.photos.licenses.getInfo"}) or {}
        license_url = next((entry.get("url", "") for entry in (licenses.get("licenses") or {}).get("license", [])
                            if str(entry.get("id")) == str(photo.get("license"))), "")
        rights = open_licence(license_url)
        if not rights or public_domain(rights[0]):
            return None  # a Flickr PD mark alone supplies no worldwide basis
        owner = photo.get("owner") or {}
        record = {"license": rights[0], "license_url": rights[1], "artist": owner.get("realname") or owner.get("username")}
        if rights[0] == "CC0":
            record["worldwide_basis"] = basis("cc0", source.geturl())
    elif source.hostname == "commons.wikimedia.org" and source.path.startswith("/wiki/File:"):
        title = unquote(source.path[len("/wiki/"):])
        try:
            response = commons._get(commons.API, params={"action": "query", "format": "json", "titles": title,
                "prop": "imageinfo|categories|templates", "iiprop": "url|size|mime|extmetadata", "cllimit": "max",
                "tllimit": "max"}, timeout=30, max_wait=30)
            pages = list((response.json().get("query") or {}).get("pages", {}).values())
            records = list(commons.candidates(pages, []))
        except (requests.RequestException, ValueError):
            return None
        record = next((candidate for candidate in records if candidate.get("download_url") == media.geturl()), None)
        if record is None:
            return None
    else:
        return None
    return {**record, "license_verified_at": basis("", "")["checked_at"],
            "license_verification_source": source.geturl()}


_NO_KNOWN_RESTRICTIONS = ("no known restrictions", "no known copyright restrictions")
_LOC_US_ONLY = ("in the u.s.", "in the united states", "use elsewhere may be restricted")


def _loc_rights(result: dict) -> str:
    """The rights advisory a Library of Congress search result carries (on the result or its "item"), as text."""
    values = []
    for holder in (result, result.get("item") if isinstance(result.get("item"), dict) else {}):
        for key in ("rights_advisory", "rights"):
            v = holder.get(key)
            values += v if isinstance(v, list) else [v] if v else []
    return " ".join(str(v) for v in values)


def loc_search(query: str, limit: int = 12) -> list[dict]:
    """Verified against the live API on 2026-11-02: GET /photos/?q=..&fo=json&c=<count>; each result has title, url
    (the item page), date, image_url (sizes, smallest first) and its rights statement under item.rights. Only a
    result whose statement says "no known restrictions" without a U.S.-only limit is taken ("Rights status not
    evaluated" and "publication in the U.S. ... elsewhere may be restricted" are refused). A bot-challenge page is
    not JSON: skipped by _get."""
    found = _get(LOC, {"q": query, "fo": "json", "c": limit, "fa": "online-format:image"})
    out = []
    for r in (found or {}).get("results") or []:
        if not isinstance(r, dict):
            continue
        rights = _loc_rights(r)
        if not any(p in rights.lower() for p in _NO_KNOWN_RESTRICTIONS):
            continue
        if any(p in rights.lower() for p in _LOC_US_ONLY):
            # "No known restrictions on publication in the U.S. Use elsewhere may be restricted": the channel
            # publishes worldwide from Canada, so a U.S.-only clearance is not taken (2026-11-02).
            continue
        images = [str(u).split("#", 1)[0] for u in r.get("image_url") or [] if u]
        images = ["https:" + u if u.startswith("//") else u for u in images]
        best = images[-1] if images else ""   # the largest size; a thumbnail is never taken in its place
        if not _file_allowed(best, "loc"):
            continue
        date = str(r.get("date") or "")
        out.append(_candidate(
            title=f"{r.get('title') or 'Item'} (Library of Congress)"[:200], download_url=best,
            source_url=r.get("url") or r.get("id") or "", artist=", ".join(r.get("contributor") or [])[:120] or "Library of Congress",
            license="Public domain", license_url="https://www.loc.gov/legal/understanding-copyright/",
            worldwide_basis=basis("loc-no-known-restrictions", r.get("url") or r.get("id") or "",
                                  statement=rights, us_only=False),
            year=int(date[:4]) if date[:4].isdigit() else None,
            description=(" ".join(r.get("description") or []) if isinstance(r.get("description"), list)
                         else str(r.get("description") or ""))[:400] + f" Rights: {rights}"[:200],
            categories=[str(s) for s in r.get("subject") or []][:10], provider="loc"))
    return out


ARTIC_WIDTH = 843   # the IIIF width the Art Institute documents as always available (larger may be refused)


def artic_search(query: str, limit: int = 12) -> list[dict]:
    """UNVERIFIED (api.artic.edu could not be fetched on 2026-11-02): GET /api/v1/artworks/search with q,
    query[term][is_public_domain]=true, fields, limit; the answer's config.iiif_url and each artwork's image_id make
    <iiif_url>/<image_id>/full/843,/0/default.jpg. Public-domain artworks' images are CC0. The AIC-User-Agent header
    names the client, as the Art Institute asks."""
    fields = "id,title,image_id,artist_display,date_start,date_display,place_of_origin,medium_display,is_public_domain,term_titles"
    found = _get(ARTIC, {"q": query, "query[term][is_public_domain]": "true", "fields": fields, "limit": limit},
                 headers={"AIC-User-Agent": HEADERS.get("User-Agent", "relic-and-riddle")})
    iiif = str(((found or {}).get("config") or {}).get("iiif_url") or ARTIC_IIIF).rstrip("/")
    out = []
    for a in (found or {}).get("data") or []:
        if not isinstance(a, dict) or a.get("is_public_domain") is not True or not a.get("image_id"):
            continue
        url = f"{iiif}/{a['image_id']}/full/{ARTIC_WIDTH},/0/default.jpg"
        if not _file_allowed(url, "artic"):
            continue
        desc = ", ".join(str(x) for x in (a.get("date_display"), a.get("place_of_origin"), a.get("medium_display")) if x)
        out.append(_candidate(
            title=f"{a.get('title') or 'Artwork'} (Art Institute of Chicago {a.get('id')})"[:200], download_url=url,
            source_url=f"https://www.artic.edu/artworks/{a.get('id')}", artist=a.get("artist_display") or "Art Institute of Chicago",
            license="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            year=a.get("date_start") if isinstance(a.get("date_start"), int) else None,
            description=desc[:400], categories=[str(t) for t in a.get("term_titles") or []][:10], provider="artic"))
    return out


SOURCES = {"met": met_search, "nasa": nasa_search, "smithsonian": smithsonian_search, "europeana": europeana_search,
           "openverse": openverse_search, "loc": loc_search, "artic": artic_search}
BASE_SOURCES = ("met", "nasa", "smithsonian", "europeana")   # the topic pool's sources before 2026-11-02


def extra_sources() -> tuple:
    """The further sources the operator has enabled (config MUSEUM_*_ENABLED), in order."""
    return tuple(name for name, flag in (("openverse", "MUSEUM_OPENVERSE_ENABLED"), ("loc", "MUSEUM_LOC_ENABLED"),
                                         ("artic", "MUSEUM_ARTIC_ENABLED")) if getattr(config, flag, False))


def _names_subject(cand: Dict, kws: List[str]) -> bool:
    text = normalize(f"{cand.get('title', '')} {cand.get('description', '')} {' '.join(cand.get('categories') or [])}")
    return not kws or any(k in text for k in kws)


def search_all(query: str, keywords: list[str], sources: tuple | None = None) -> list[dict]:
    """Candidates from the museum sources whose title or description names the subject (BASE_SOURCES and the
    further sources the operator enabled, unless given)."""
    kws = [normalize(k) for k in keywords]
    out = []
    for name in sources or BASE_SOURCES + extra_sources():
        if name == "openverse" and not config.MUSEUM_OPENVERSE_ENABLED:
            continue
        for cand in SOURCES[name](query):
            if _names_subject(cand, kws):
                out.append(cand)
    return out


# ------------------------------------------------------------------ the search for one narration line
# Production Derinkuyu, 2026-10-29: three lines (livestock and food supplies; sections sealed in the Arab-Byzantine
# wars; the 1923 population exchange) had no Commons photo, and went on screen over unrelated pictures. Before a
# line is given up on, the museums and archives with free, documented open-access APIs are asked too: The Met (CC0
# objects, no key), NASA (its own works, no key), the Smithsonian (CC0 media, with an api.data.gov key only: the
# public DEMO_KEY is for trying the API, not for a pipeline) and Europeana (open rights statements only, with its
# key). Every result passes credits.license_allowed again when it is read (a licence setting changed since it was
# cached counts), and the caller looks at each picture as it looks at a Commons one.
CACHE_FILE = config.BASE_DIR / "cache" / "museum_search.json"
CACHE_DAYS = 14          # a museum's open-access set changes slowly; a fortnight-old answer is still good
LINE_LIMIT = 6           # objects asked for per source (The Met answers one request per object)
DOWN_SECONDS = 600       # a source that failed is not asked again for this long (each line would wait on it)
_down_until: Dict[str, float] = {}
_cache_lock = threading.Lock()


def line_sources() -> tuple:
    """The sources a line search asks, in order: those that need a key only with one configured."""
    names = ["met", "nasa"]
    if config.SMITHSONIAN_API_KEY:
        names.append("smithsonian")
    if config.EUROPEANA_API_KEY:
        names.append("europeana")
    return tuple(names) + extra_sources()


def _cache_key(name: str, query: str, limit: int) -> str:
    return f"{name}|{limit}|{' '.join(normalize(query).split())}"


def _load_cache() -> Dict:
    import json
    try:
        data = json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_search(query: str, keywords: List[str], sources: Optional[tuple] = None, limit: int = LINE_LIMIT,
                  deadline: Optional[float] = None) -> List[Dict]:
    """Candidates for one search from the museum sources (line_sources() unless given), each source's answer cached
    for CACHE_DAYS by (source, query); an answer that saw a failed request is not cached, and that source rests for
    DOWN_SECONDS. Only candidates whose metadata names the subject (keywords) and whose licence the project allows.
    `deadline` (time.monotonic()): no further source is asked once it has passed. Never raises."""
    global _failures
    from .atomic_files import write_json
    from .credits import license_allowed
    kws = [normalize(k) for k in keywords or []]
    now = time.time()
    with _cache_lock:
        cache = _load_cache()
    fresh, out = {}, []
    for name in sources or line_sources():
        if name not in SOURCES:
            continue
        if name == "openverse" and not config.MUSEUM_OPENVERSE_ENABLED:
            continue
        key = _cache_key(name, query, limit)
        hit = cache.get(key)
        if name != "openverse" and isinstance(hit, dict) and now - float(hit.get("at") or 0) < CACHE_DAYS * 86400:
            found = list(hit.get("found") or [])
        else:
            if (deadline is not None and time.monotonic() > deadline) or _down_until.get(name, 0) > time.monotonic():
                continue
            before = _failures
            try:
                found = SOURCES[name](query, limit=limit)
            except Exception as e:  # a museum is a saving, never a new way for a run to fail
                log.info("%s search for %r failed (%s); not asked again for now.", name, query[:60], e)
                found, _failures = [], _failures + 1
            if _failures > before:
                _down_until[name] = time.monotonic() + DOWN_SECONDS
            else:
                fresh[key] = {"at": now, "found": found}
        out += [dict(c) for c in found if isinstance(c, dict) and c.get("download_url")
                and (not public_domain(str(c.get("license") or "")) or recorded_basis_ok(c))
                and (name != "openverse" or c.get("license_verified_at"))
                and license_allowed(str(c.get("license") or "")) and _names_subject(c, kws)]
    if fresh:
        try:
            with _cache_lock:   # read again: another line's search may have written meanwhile
                write_json(Path(CACHE_FILE), {**_load_cache(), **fresh})
        except (OSError, ValueError, TypeError) as e:
            log.debug("Museum search cache not written: %s", e)
    return out
