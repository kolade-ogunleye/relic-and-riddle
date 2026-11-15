"""
src/source_policy.py
====================
Sources no asset may come from, whatever licence a record states (QF-126; catalogue audit 2026-11-14, R6).

  David Rumsey Map Collection   CC BY-NC-SA; commercial use by permission only
  BBC Sound Effects (RemArc)    personal, educational and research use only
  ESA (esa.int)                 non-commercial unless the item itself is marked CC BY-SA 3.0 IGO
                                (ESA/Hubble and ESA/Webb releases are CC BY 4.0 and are accepted with that licence)

`blocked_source(record)` is asked wherever a record enters the pipeline (the Commons and museum searches, the archive
film search, an operator's upload, the music library) and again when a run's rights are decided
(credits.rights_evidence_ok), so a record ingested before the list existed is never counted as cleared.
Topic-agnostic: the list names sources, never subjects.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# name -> (hosts, phrases, why). A host matches itself and its subdomains; a phrase matches as words in the record's
# source fields (not its title or description: a photo OF a BBC building is not a BBC recording).
BLOCKED_SOURCES: dict[str, dict[str, Any]] = {
    "David Rumsey Map Collection": {
        "hosts": ("davidrumsey.com",),
        "phrases": ("david rumsey",),
        "why": "its scans are CC BY-NC-SA (commercial use by permission only)",
    },
    "BBC Sound Effects (RemArc)": {
        "hosts": ("bbcrewind.co.uk",),
        "phrases": ("bbc sound effects", "remarc", "bbc rewind"),
        "why": "the RemArc licence allows personal, educational and research use only",
    },
    "ESA": {
        "hosts": ("esa.int",),
        "phrases": ("european space agency",),
        "why": "ESA media is non-commercial unless the item is marked CC BY-SA 3.0 IGO",
    },
}

# The fields that say where a record came from. A URL anywhere in them is matched by host (a Commons credit is HTML
# that links its source); their words are matched by phrase.
_FIELDS = ("source_url", "source", "url", "download_url", "foreign_landing_url", "landing_url", "license_url",
           "licence_url", "provider", "credit", "collection", "collections", "institution", "artist", "creator",
           "author", "categories")
_AUTHOR_FIELDS = ("provider", "source", "credit", "artist", "creator", "author")
_URL = re.compile(r"(?:https?:)?//[^\s\"'<>|]+", re.IGNORECASE)


def _values(record: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    out = []
    for field in fields:
        value = record.get(field)
        out += [str(v) for v in (value if isinstance(value, (list, tuple, set)) else [value]) if v]
    return out


def _hosts(record: dict[str, Any]) -> list[str]:
    hosts = []
    for text in _values(record, _FIELDS):
        for url in _URL.findall(text):
            host = (urlparse(url if url.lower().startswith("http") else "https:" + url).hostname or "").lower()
            if host:
                hosts.append(host)
    return hosts


def _words(text: str) -> str:
    """`text` as lower-case words: tags and URLs out (a URL is matched by its host), punctuation to spaces."""
    text = _URL.sub(" ", re.sub(r"<[^>]+>", " ", text))
    return " ".join(re.sub(r"[_\W]+", " ", text.lower()).split())


# ESA (review of QF-126, round 1; ruled 2026-11-14). The agency's token is "ESA" in upper case, as a word: a
# photographer called Esa is not the agency. It is looked for anywhere in a credit ("Credit: ESA/ATG medialab",
# "DLR / ESA"), and a credit that names NASA before it ("NASA, ESA and the Hubble Heritage Team") is NASA's record.
# ESA/Hubble and ESA/Webb publish under CC BY 4.0 on their own sites (esahubble.org/copyright, esawebb.org/copyright):
# they are let through with that licence. esa.int media stay out unless marked CC BY-SA 3.0 IGO.
_ESA_TOKEN = re.compile(r"(?<![A-Za-z0-9])ESA(?![A-Za-z0-9])")
_NASA_TOKEN = re.compile(r"(?<![A-Za-z0-9])NASA(?![A-Za-z0-9])")
_ESA_TELESCOPES = re.compile(r"(?<![A-Za-z0-9])ESA\s*/\s*(?:Hubble|Webb)(?![A-Za-z0-9])")
_ESA_TELESCOPE_HOSTS = ("esahubble.org", "esawebb.org", "spacetelescope.org")
_LICENCE_URL_FIELDS = ("license_url", "licence_url")


def _licence_key(record: dict[str, Any]) -> str:
    """The record's licence name as lower-case letters and digits only ("CC-BY-SA-3.0-IGO" -> "ccbysa30igo")."""
    return re.sub(r"[^a-z0-9]", "", str(record.get("license") or record.get("licence") or "").lower())


def _licence_path(record: dict[str, Any]) -> str:
    """The path of the record's Creative Commons licence address ("/licenses/by-sa/3.0/igo"), "" without one."""
    for url in _values(record, _LICENCE_URL_FIELDS):
        parsed = urlparse(url.strip() if "//" in url else "https://" + url.strip())
        if (parsed.hostname or "").lower().removeprefix("www.") == "creativecommons.org":
            return parsed.path.lower().rstrip("/")
    return ""


def _is_by_sa_3_igo(record: dict[str, Any]) -> bool:
    """CC BY-SA 3.0 IGO, however it is spelled ("CC BY-SA IGO 3.0", "CC-BY-SA-3.0-IGO"), or by its address."""
    key = _licence_key(record)
    named = key.startswith("ccbysa") and key[6:] in ("30igo", "igo30", "3igo", "igo3")
    return named or _licence_path(record).startswith("/licenses/by-sa/3.0/igo")


def _is_by_4(record: dict[str, Any]) -> bool:
    """CC BY 4.0 by its name, or by its address (its deed and legal code pages too)."""
    path = _licence_path(record)
    return (_licence_key(record) in ("ccby40", "ccby40international") or path == "/licenses/by/4.0"
            or path.startswith("/licenses/by/4.0/"))


def _plain(text: str) -> str:
    """`text` without tags and URLs, its case kept."""
    return _URL.sub(" ", re.sub(r"<[^>]+>", " ", text))


def _names_esa(record: dict[str, Any]) -> bool:
    """Whether an author or credit field names ESA as the source: its token, with no NASA before it in the field."""
    if str(record.get("provider") or "").strip().lower() == "esa":
        return True
    for value in _values(record, _AUTHOR_FIELDS):
        text = _plain(value)
        found = _ESA_TOKEN.search(text)
        if found and not _NASA_TOKEN.search(text[:found.start()]):
            return True
    return False


def _esa_telescope(record: dict[str, Any], hosts: list[str]) -> bool:
    """An ESA/Hubble or ESA/Webb release: one of their hosts, or that author."""
    return (any(h == b or h.endswith("." + b) for h in hosts for b in _ESA_TELESCOPE_HOSTS)
            or any(_ESA_TELESCOPES.search(_plain(v)) for v in _values(record, _AUTHOR_FIELDS)))


def blocked_source(record: dict[str, Any] | None) -> str:
    """Why `record` may not be used ("<source>: <reason>"), "" when its source is not on the list. A URL in any of
    the record's source fields is matched by host, and its provider, credit, collection and author fields by the
    source's name. ESA: see _ESA_TOKEN above."""
    if not isinstance(record, dict):
        return ""
    hosts, text = _hosts(record), f" {' | '.join(_words(v) for v in _values(record, _FIELDS))} "
    for name, rule in BLOCKED_SOURCES.items():
        by_host = any(h == b or h.endswith("." + b) for h in hosts for b in rule["hosts"])
        by_name = any(f" {p} " in text for p in rule["phrases"])
        if name == "ESA":
            by_name = by_name or _names_esa(record)
        if not (by_host or by_name):
            continue
        if name == "ESA" and (_is_by_sa_3_igo(record) or (_is_by_4(record) and _esa_telescope(record, hosts))):
            continue
        return f"{name}: {rule['why']}"
    return ""


def refuse_blocked(record: dict[str, Any] | None) -> None:
    """Raises ValueError naming the source when `record` comes from a blocked one (an operator's upload, the music
    library: places that answer a person)."""
    reason = blocked_source(record)
    if reason:
        raise ValueError(f"Not accepted: this source is excluded ({reason}).")
