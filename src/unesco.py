"""
src/unesco.py
=============
Automated ingestion and querying of the official UNESCO World Heritage Open Dataset.
Dataset source: https://whc.unesco.org/en/list/xml/ (1,199 inscribed sites).

Provides:
1. Local-first caching of the UNESCO World Heritage registry.
2. Fuzzy and geo-distance matching of topics to official UNESCO site records.
3. Official Tier 1 evidentiary quotes for fact_sheet.py.
4. Autonomous discovery filtering for Criterion (i) and (iv) engineering masterpieces.
"""

import difflib
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel

from . import config


def _clean_title(title: str) -> str:
    """Accent-free, punctuation-free, single-spaced lowercase, for matching site names."""
    import unicodedata
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", title or "") if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", folded.lower()).split())

log = logging.getLogger(__name__)

UNESCO_XML_URL = "https://whc.unesco.org/en/list/xml/"
CACHE_DIR = config.BASE_DIR / "cache" / "unesco"
CACHE_FILE = CACHE_DIR / "sites.xml"
CACHE_TTL_DAYS = 30
# The engineering themes the Ideas panel offers (Ideas → UNESCO marvels), each with the words that place a site in it.
THEME_KEYWORDS = {
    "subterranean": ["subterranean", "underground", "cave", "hypogeum", "catacomb", "salt", "mine", "tunnel", "quarry"],
    "hydraulic": ["aqueduct", "canal", "hydraulic", "qanat", "cistern", "dam", "reservoir", "water"],
    "megalithic": ["megalith", "monolith", "obelisk", "pyramid", "temple", "acropolis", "basalt", "stone"],
    "metallurgy": ["kiln", "metallurgy", "iron", "bronze", "copper", "forge", "smelt"],
}


# Topic country names as UNESCO's registry spells them (it uses official UN names).
COUNTRY_ALIASES = {
    "turkey": "turkiye", "usa": "united states of america", "us": "united states of america",
    "united states": "united states of america", "uk": "united kingdom", "england": "united kingdom",
    "scotland": "united kingdom", "wales": "united kingdom", "britain": "united kingdom",
    "russia": "russian federation", "iran": "iran", "syria": "syrian arab republic",
    "czech republic": "czechia", "south korea": "republic of korea", "vietnam": "viet nam",
    "netherlands": "netherlands", "bolivia": "bolivia", "tanzania": "united republic of tanzania",
}
# Words that name a kind of place rather than a particular one; they never identify a site.
GENERIC_WORDS = {
    "ancient", "historic", "historical", "centre", "center", "city", "cities", "old", "town", "temple", "temples",
    "palace", "palaces", "church", "churches", "cathedral", "monument", "monuments", "national", "park", "site",
    "sites", "area", "areas", "great", "wall", "walls", "royal", "rock", "rocks", "cave", "caves", "island",
    "islands", "lines", "sea", "bay", "desert", "mountain", "mountains", "library", "museum", "underground",
    "related", "property", "properties", "complex", "group", "groups", "remains", "ruins", "archaeological",
    "cultural", "landscape", "region", "province", "valley", "river", "lake", "structure", "mystery", "secret",
    "paradox", "anomaly", "event", "miracle", "incident", "heist", "self", "healing", "concrete", "steel",
    "monolith", "manuscript", "mechanism", "headquarters", "peninsula", "shipwreck", "plain", "plateau",
}


def _fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("Ħ", "H").replace("ħ", "h"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _distinctive(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", _fold(text)) if len(w) > 3 and w not in GENERIC_WORDS}


def _country_key(states: str) -> str:
    folded = _fold(states).strip()
    return COUNTRY_ALIASES.get(folded, folded)


class UNESCOSite(BaseModel):
    id_number: int
    site: str
    states: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    criteria_txt: str = ""
    category: str = ""
    date_inscribed: Optional[int] = None
    short_description: str = ""
    justification: str = ""
    url: str = ""


class UNESCOEngine:
    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self._sites: Optional[Dict[int, UNESCOSite]] = None

    def _ensure_cache(self) -> Path:
        """Downloads the official UNESCO dataset if missing or older than CACHE_TTL_DAYS."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        is_stale = (
            not self.cache_file.exists()
            or (time.time() - self.cache_file.stat().st_mtime) > (CACHE_TTL_DAYS * 86400)
        )
        if is_stale:
            try:
                log.info("Downloading official UNESCO World Heritage dataset from %s...", UNESCO_XML_URL)
                resp = requests.get(
                    UNESCO_XML_URL,
                    timeout=30,
                    headers={"User-Agent": "RelicAndRiddlePipeline/2.0 (Historical Research Project)"},
                )
                resp.raise_for_status()
                # Verify that it is valid XML before writing
                ET.fromstring(resp.content)
                self.cache_file.write_bytes(resp.content)
                log.info("Saved UNESCO dataset (%d bytes) to %s.", len(resp.content), self.cache_file)
            except Exception as e:
                log.warning("Could not download UNESCO dataset (%s); using existing cache if present.", e)
                if not self.cache_file.exists():
                    # Create an empty XML structure fallback
                    self.cache_file.write_text("<query><row></row></query>", encoding="utf-8")
        return self.cache_file

    def load_sites(self) -> Dict[int, UNESCOSite]:
        """Loads and parses the UNESCO dataset into memory."""
        if self._sites is not None:
            return self._sites

        path = self._ensure_cache()
        sites: Dict[int, UNESCOSite] = {}
        try:
            tree = ET.parse(str(path))
            root = tree.getroot()
            for row in root.findall(".//row"):
                id_elem = row.find("id_number")
                if id_elem is None or not id_elem.text or not id_elem.text.strip().isdigit():
                    continue
                site_id = int(id_elem.text.strip())

                def _text(elem_name: str) -> str:
                    el = row.find(elem_name)
                    if el is not None and el.text:
                        # Clean html tags if present
                        txt = re.sub(r"<[^>]+>", "", el.text).strip()
                        return txt
                    return ""

                def _float(elem_name: str) -> Optional[float]:
                    t = _text(elem_name)
                    try:
                        return float(t) if t else None
                    except ValueError:
                        return None

                def _int(elem_name: str) -> Optional[int]:
                    t = _text(elem_name)
                    try:
                        return int(t) if t else None
                    except ValueError:
                        return None

                site = UNESCOSite(
                    id_number=site_id,
                    site=_text("site"),
                    states=_text("states"),
                    latitude=_float("latitude"),
                    longitude=_float("longitude"),
                    criteria_txt=_text("criteria_txt"),
                    category=_text("category"),
                    date_inscribed=_int("date_inscribed"),
                    short_description=_text("short_description"),
                    justification=_text("justification"),
                    url=f"https://whc.unesco.org/en/list/{site_id}",
                )
                sites[site_id] = site
        except Exception as e:
            log.warning("Failed parsing UNESCO XML cache (%s).", e)

        self._sites = sites
        return sites

    def get_site_by_id(self, site_id: int) -> Optional[UNESCOSite]:
        return self.load_sites().get(site_id)

    def find_site(
        self,
        name: str,
        country: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> Optional[UNESCOSite]:
        """The inscribed site a free-text name refers to (same rules as match_topic)."""
        location = {"name": name, "country": country, "latitude": lat, "longitude": lon}
        return self.match_topic({"title": name, "location": location})

    def match_topic(self, topic: Dict[str, Any]) -> Optional[UNESCOSite]:
        """The inscribed site a topic is actually about, or None. Evidence from the wrong site is
        worse than none, so a match needs the site's own name to share a distinctive word with the
        topic's title, or with its location name when the topic has real coordinates (a location
        name without them can be a whole province). Cities and regions never match on their own:
        they find the site's surroundings, not the site. A topic can pin the answer with
        location.unesco_id (a number, or null for "none")."""
        loc = topic.get("location") or {}
        if "unesco_id" in loc:
            return self.get_site_by_id(int(loc["unesco_id"])) if loc["unesco_id"] is not None else None
        country = _country_key(loc.get("country") or "")
        lat, lon = loc.get("latitude", loc.get("lat")), loc.get("longitude", loc.get("lon"))
        located = lat is not None and lon is not None and (abs(float(lat)) > 1e-6 or abs(float(lon)) > 1e-6)
        wanted = _distinctive(topic.get("title", ""))
        if located:
            wanted |= _distinctive(loc.get("name") or "")
        if not wanted:
            return None
        best, best_key = None, None
        for site in self.load_sites().values():
            if country and country not in _country_key(site.states):
                continue
            # Exact words only: a one-letter difference can be a transliteration (Nazca/Nasca)
            # or a different place (Zhang/Chang'an); such topics pin location.unesco_id instead.
            hits = _distinctive(site.site) & wanted
            if not hits:
                continue
            key = (len(hits), difflib.SequenceMatcher(None, _fold(topic.get("title", "")), _fold(site.site)).ratio())
            if best_key is None or key > best_key:
                best, best_key = site, key
        return best

    def as_evidence(self, site: UNESCOSite) -> List[Dict[str, Any]]:
        """
        Formats official UNESCO registry data as Tier 1 quotes for fact_sheet.py.
        """
        quotes: List[Dict[str, Any]] = []
        if site.short_description:
            quotes.append({
                "url": site.url,
                "tier": 1,
                "quote": f"UNESCO World Heritage Site #{site.id_number} ({site.site}): {site.short_description}",
            })
        if site.justification:
            quotes.append({
                "url": site.url,
                "tier": 1,
                "quote": f"UNESCO Inscription Criteria ({site.criteria_txt}): {site.justification}",
            })
        if site.date_inscribed:
            quotes.append({
                "url": site.url,
                "tier": 1,
                "quote": f"Inscribed on the UNESCO World Heritage List in {site.date_inscribed} under criteria {site.criteria_txt}.",
            })
        return quotes

    def discover_engineering_wonders(
        self,
        limit: int = 5,
        offset: int = 0,
        sub_theme: Optional[str] = None,
        exclude_titles: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Autonomously discovers unqueued ancient engineering wonders from official
        UNESCO Criterion (i) and (iv) inscriptions.
        """
        sites = self.load_sites().values()
        candidates: List[Dict[str, Any]] = []
        # Both sides normalised the same way: an accented or hyphenated name ("Pyrénées - Mont Perdu")
        # never matched its own exclusion, so an added or skipped marvel kept coming back.
        exclude = {c for c in (_clean_title(t) for t in (exclude_titles or [])) if c}

        target_keywords = THEME_KEYWORDS.get(sub_theme.lower()) if sub_theme and sub_theme.lower() in THEME_KEYWORDS else [
            kw for kws in THEME_KEYWORDS.values() for kw in kws
        ]

        matched_index = 0
        for s in sites:
            crit = s.criteria_txt or ""
            if "(i)" not in crit and "(iv)" not in crit:
                continue

            clean_title = _clean_title(s.site)
            if any(ex in clean_title or clean_title in ex for ex in exclude):
                continue

            text = (s.site + " " + s.short_description + " " + s.justification).lower()
            matched = [k for k in target_keywords if k in text]
            if not matched:
                continue

            if matched_index < offset:
                matched_index += 1
                continue

            matched_index += 1
            candidates.append({
                "id_number": s.id_number,
                "title": s.site,
                "country": s.states,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "criteria": s.criteria_txt,
                "short_description": s.short_description,
                "matched_keywords": matched,
                "url": s.url or f"https://whc.unesco.org/en/list/{s.id_number}",
            })
            if len(candidates) >= limit:
                break

        return candidates
