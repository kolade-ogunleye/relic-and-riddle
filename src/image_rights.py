"""Recorded image-rights bases; a generic PD label is not geographic clearance."""

import re
from datetime import UTC, datetime
from functools import lru_cache


def public_domain(licence: str) -> bool:
    return bool(re.match(r"^(?:public domain|pd(?:\b|-))", licence.strip(), re.IGNORECASE))


def basis(kind: str, evidence: str, **details) -> dict:
    return {"kind": kind, "evidence": evidence, "checked_at": datetime.now(UTC).isoformat(), **details}


@lru_cache(maxsize=512)
def creator_qid_from_sdc(pageid: int | str, max_wait: int = 30) -> str | None:
    """Query Commons MediaInfo SDC (P170: creator) for an M-id."""
    mid = f"M{pageid}" if not str(pageid).startswith("M") else str(pageid)
    params = {"action": "wbgetentities", "format": "json", "props": "claims",
              "ids": mid, "maxlag": 5}
    try:
        from . import commons
        resp = commons._get("https://commons.wikimedia.org/w/api.php", params=params, timeout=30, max_wait=max_wait)
        entity = (resp.json() if hasattr(resp, "json") else resp).get("entities", {}).get(mid, {})
        # Commons MediaInfo entities answer with "statements" (Wikidata items with "claims").
        claims = (entity.get("statements") or entity.get("claims") or {}).get("P170", [])
        for claim in claims:
            val = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(val, dict):
                qid = val.get("id")
                if qid and re.fullmatch(r"Q[1-9][0-9]*", qid):
                    return qid
    except (ValueError, KeyError, OSError):
        return None
    return None


def author_qid_from_meta(meta: dict, page: dict) -> str | None:
    """Extract an author Wikidata QID from Artist/Credit extmetadata, SDC, or page data."""
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(page, dict):
        page = {}
    for key in ("author_qid", "creator_qid", "artist_qid"):
        val = str(meta.get(key) or page.get(key) or "").strip()
        if re.fullmatch(r"Q[1-9][0-9]*", val):
            return val
    # Direct SDC statements on the page object if preloaded
    for claim in page.get("statements", {}).get("P170", []):
        val = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(val, dict):
            qid = val.get("id")
            if qid and re.fullmatch(r"Q[1-9][0-9]*", qid):
                return qid
    # SDC query via pageid
    pageid = page.get("pageid")
    if pageid:
        sdc_qid = creator_qid_from_sdc(pageid)
        if sdc_qid:
            return sdc_qid
    artist_val = str((meta.get("Artist") or {}).get("value", "") if isinstance(meta.get("Artist"), dict) else "")
    credit_val = str((meta.get("Credit") or {}).get("value", "") if isinstance(meta.get("Credit"), dict) else "")
    combined = f"{artist_val} {credit_val}"
    m = re.search(r"wikidata\.org/(?:wiki|entity)/(Q[1-9][0-9]*)", combined)
    if m:
        return m.group(1)
    m = re.search(r"data-wikidata(?:-entity-id)?=[\"'](Q[1-9][0-9]*)[\"']", combined)
    if m:
        return m.group(1)
    return None


@lru_cache(maxsize=512)
def wikidata_author_death(author_qid: str, max_wait: int = 30) -> int | None:
    """Query Wikidata P570 (date of death) for an author item; returns death year (negative for BCE) or None."""
    if not author_qid or not re.fullmatch(r"Q[1-9][0-9]*", author_qid):
        return None
    params = {"action": "wbgetentities", "format": "json", "props": "claims",
              "ids": author_qid, "maxlag": 5}
    try:
        from . import commons
        resp = commons._get("https://www.wikidata.org/w/api.php", params=params, timeout=30, max_wait=max_wait)
        entity = (resp.json() if hasattr(resp, "json") else resp).get("entities", {}).get(author_qid, {})
        claims = [c for c in entity.get("claims", {}).get("P570", []) if c.get("rank") != "deprecated"]
        preferred = [c for c in claims if c.get("rank") == "preferred"]
        years = []
        for claim in preferred or claims:  # the preferred dates when there are any, else the normal ones
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            time_val = value.get("time")
            if not time_val or int(value.get("precision") or 0) < 9:
                continue  # a decade or century is no year of death (precision 9 = year)
            m = re.match(r"^([+-]?\d+)", str(time_val))
            if m:
                years.append(int(m.group(1)))
        return max(years) if years else None  # disputed dates: the latest, so the work is protected longest
    except (ValueError, KeyError, OSError):
        return None
    return None


def commons_basis(page: dict, meta: dict, source: str) -> dict | None:
    licence = str(meta.get("LicenseShortName", {}).get("value", ""))
    if licence.strip().lower().startswith("cc0"):
        return basis("cc0", source)
    if not public_domain(licence):
        return None
    names = [str(item.get("title", "")) for item in page.get("templates", []) + page.get("categories", [])]
    names += str(meta.get("Categories", {}).get("value", "")).split("|")
    names += [licence]
    text_raw = "|".join(names).lower()
    text_norm = re.sub(r"[\s_]+", "-", text_raw)
    # The same rules as basis_from_record (stored records), so a file passes or fails alike at fetch and at release.
    if re.search(r"\bcc-zero\b", text_norm):
        return basis("cc0", source, scope="CC0 waiver recorded as the file's Commons category")

    # 1. Author dedications (worldwide waiver / CC0 equivalent)
    if re.search(r"\bpd-(?:self|user|author(?:-flickrpdm)?)\b", text_norm):
        return basis("commons-pd-user", source, scope="worldwide author dedication / waiver")

    # 2. US federal works (17 U.S.C. 105), read as the stored-record path reads them (basis_from_record)
    # NASA first: its usual template is PD-USGov-NASA, and a NASA tag needs the third-party check.
    if re.search(r"\bpd-(?:usgov-)?nasa\b", text_norm):
        credit = " ".join(str((meta.get(k) or {}).get("value", "")) for k in ("Artist", "Credit", "ImageDescription")
                          if isinstance(meta.get(k), dict))
        if _THIRD_PARTY.search(credit):
            return None  # a NASA tag over an ESA/JAXA/courtesy credit is no NASA own work
        return basis("nasa-own-work", source, third_party=False, scope="NASA own work")
    if re.search(r"\bpd-(?:usgov|usgs|cia)\b", text_norm):
        credit = " ".join(str((meta.get(k) or {}).get("value", "")) for k in ("Artist", "Credit", "ImageDescription")
                          if isinstance(meta.get(k), dict))
        if _THIRD_PARTY.search(credit):
            return None  # the ruling's federal works are those without third-party claims
        return basis("commons-pd-usgov", source, scope="US federal work, 17 U.S.C. 105")

    # 3. Life-term + US basis (both required; check US basis first to avoid redundant Wikidata lookups)
    us = re.search(r"\bpd-us-expired\b|\bpd-old-(?:70|80|90|100|auto)-expired\b", text_norm)
    if not us:
        return None

    old = re.search(r"\bpd-old-(?:70|80|90|100|auto)(?:-expired)?\b(?!:-no-death-date)", text_norm) or re.search(
        r"author-died-more-than-(?:70|80|90|100)-years-ago", text_norm)
    source_life_term = old.group() if old else None
    if not source_life_term:
        author_qid = author_qid_from_meta(meta, page)
        if author_qid:
            death_year = wikidata_author_death(author_qid)
            # Life+70 rule: protected until Jan 1 of (death_year + 71).
            # death_year + 70 < current_year ensures full 70 years have elapsed.
            if death_year is not None and (death_year + 70 < datetime.now(UTC).year):
                source_life_term = f"wikidata-p570:{author_qid}:{death_year}"
    if not (source_life_term and source):
        return None
    kind = "commons-pd-art" if "pd-art" in text_norm else "commons-pd-old"
    return basis(kind, source, source_life_term=source_life_term, us_basis=us.group(),
                 scope="documented source/life-term and US bases; jurisdiction-specific exceptions require review")


def recorded_basis_ok(record: dict) -> bool:
    value = record.get("worldwide_basis")
    if not isinstance(value, dict) or not value.get("evidence") or not value.get("checked_at"):
        return False
    kind = value.get("kind")
    if kind in ("commons-pd-old", "commons-pd-art"):
        return bool(value.get("source_life_term") and value.get("us_basis"))
    if kind in ("commons-pd-self", "commons-pd-user", "commons-pd-usgov"):
        return True
    if kind == "nasa-own-work":
        return value.get("third_party") is False
    if kind == "loc-no-known-restrictions":
        return bool(value.get("statement")) and value.get("us_only") is False
    if kind == "archive-film-operator-approved":
        return bool(value.get("check_note") or value.get("evidence"))
    return kind == "cc0"



_THIRD_PARTY = re.compile(r"\b(?:esa|cnes|jaxa|csa|third[- ]party|courtesy)\b", re.IGNORECASE)


def _source_text(record: dict) -> str:
    names = [str(c) for c in record.get("categories") or []] + [str(record.get("license") or "")]
    return re.sub(r"[\s_]+", "-", "|".join(names).lower())


def basis_from_record(record: dict) -> dict | None:
    """The worldwide basis a stored record's own evidence supports (operator ruling decisions/QF-57.md, Option A),
    else None. Read from what the record already carries (its Commons categories and licence, or the operator's
    recorded approval of an archive film), so dev and production records pass the same way with no migration."""
    licence = str(record.get("license") or "")
    if not public_domain(licence):
        return None
    source = str(record.get("source_url") or "")
    text = _source_text(record)
    if re.search(r"\bcc-zero\b", text):  # Commons' CC0 category: a worldwide waiver, as a CC0 licence is
        return basis("cc0", source or "commons", scope="CC0 waiver recorded as the file's Commons category")
    if re.search(r"\bpd-(?:self|user|author(?:-flickrpdm)?)\b", text):
        return basis("commons-pd-user", source or "commons", scope="worldwide author dedication / waiver")
    if re.search(r"\bpd-(?:usgov-)?nasa\b", text):  # before PD-USGov (see above); a NASA tag, not a NASA category
        credit = f"{record.get('artist') or ''} {record.get('description') or ''}"
        if _THIRD_PARTY.search(credit):
            return None
        return basis("nasa-own-work", source or "commons", third_party=False, scope="NASA own work")
    if re.search(r"\bpd-(?:usgov|usgs|cia)\b", text):
        if _THIRD_PARTY.search(f"{record.get('artist') or ''} {record.get('description') or ''}"):
            return None  # the ruling's federal works are those without third-party claims
        return basis("commons-pd-usgov", source or "commons", scope="US federal work, 17 U.S.C. 105")
    old = (re.search(r"\bpd-old-(?:70|80|90|100|auto)(?:-expired)?\b(?!:-no-death-date)", text)
           or re.search(r"author-died-more-than-(?:70|80|90|100)-years-ago", text))
    us = re.search(r"\bpd-us-expired\b|\bpd-old-(?:70|80|90|100|auto)-expired\b", text)
    if old and us and source:
        kind = "commons-pd-art" if "pd-art" in text else "commons-pd-old"
        return basis(kind, source, source_life_term=old.group(), us_basis=us.group(),
                     scope="recorded source categories; jurisdiction-specific exceptions require review")
    if record.get("provider") == "archive.org" and "approved by the operator" in str(record.get("check_note") or ""):
        return basis("archive-film-operator-approved", source or str(record.get("id") or ""),
                     check_note=record["check_note"])
    return None


def effective_basis(record: dict) -> dict | None:
    """A stored, valid worldwide basis, else the one the record's own evidence supports."""
    return record.get("worldwide_basis") if recorded_basis_ok(record) else basis_from_record(record)


def rights_held(record: dict) -> bool:
    """A public-domain record with no worldwide basis: it may not be used or released (fails closed)."""
    return public_domain(str(record.get("license") or "")) and effective_basis(record) is None
