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
import math
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from . import config

log = logging.getLogger(__name__)

UNESCO_XML_URL = "https://whc.unesco.org/en/list/xml/"
CACHE_DIR = config.BASE_DIR / "cache" / "unesco"
CACHE_FILE = CACHE_DIR / "sites.xml"
CACHE_TTL_DAYS = 30


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

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great circle distance in kilometers."""
        r = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
        return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    def find_site(
        self,
        name: str,
        country: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        max_dist_km: float = 60.0,
    ) -> Optional[UNESCOSite]:
        """
        Matches a topic to an official UNESCO World Heritage site using:
        1. Exact or near name matching.
        2. Geo-distance matching if latitude/longitude are provided.
        """
        sites = self.load_sites().values()
        if not sites:
            return None

        clean_name = re.sub(r"[^a-z0-9 ]", "", name.lower())
        tokens = {w for w in clean_name.split() if len(w) > 3}

        best_match: Optional[UNESCOSite] = None
        best_score = 0.0

        for s in sites:
            s_name = re.sub(r"[^a-z0-9 ]", "", s.site.lower())
            s_tokens = {w for w in s_name.split() if len(w) > 3}

            # Check Country consistency if provided
            if country and country.lower() not in s.states.lower():
                continue

            # Geo-distance check
            if lat is not None and lon is not None and s.latitude is not None and s.longitude is not None:
                dist = self._haversine(lat, lon, s.latitude, s.longitude)
                if dist <= max_dist_km:
                    return s

            # Token overlap score
            overlap = len(tokens & s_tokens)
            ratio = difflib.SequenceMatcher(None, clean_name, s_name).ratio()
            score = (overlap * 0.4) + (ratio * 0.6)

            if score > best_score and (overlap >= 1 or ratio >= 0.60):
                best_score = score
                best_match = s

        return best_match if best_score >= 0.50 else None

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
        exclude = {t.lower() for t in (exclude_titles or [])}

        THEME_KEYWORDS = {
            "subterranean": ["subterranean", "underground", "cave", "hypogeum", "catacomb", "salt", "mine", "tunnel", "quarry"],
            "hydraulic": ["aqueduct", "canal", "hydraulic", "qanat", "cistern", "dam", "reservoir", "water"],
            "megalithic": ["megalith", "monolith", "obelisk", "pyramid", "temple", "acropolis", "basalt", "stone"],
            "metallurgy": ["kiln", "metallurgy", "iron", "bronze", "copper", "forge", "smelt"],
        }

        target_keywords = THEME_KEYWORDS.get(sub_theme.lower()) if sub_theme and sub_theme.lower() in THEME_KEYWORDS else [
            kw for kws in THEME_KEYWORDS.values() for kw in kws
        ]

        matched_index = 0
        for s in sites:
            crit = s.criteria_txt or ""
            if "(i)" not in crit and "(iv)" not in crit:
                continue

            clean_title = re.sub(r"[^a-z0-9 ]", "", s.site.lower()).strip()
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
