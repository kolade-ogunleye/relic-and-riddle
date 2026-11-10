"""Structured shot searches anchored to an explicitly selected Wikidata item."""

import json
import re
from pathlib import Path

import requests

from . import commons

SHOT_TYPES = {"wide", "interior", "detail", "people-scale", "archival"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"


def quoted(value: str) -> str:
    return '"' + " ".join(str(value).replace('"', " ").replace("\\", " ").split())[:200] + '"'


def structured_spec(spec: dict) -> dict:
    """Give newly proposed topics a shot plan too. Their searches, keywords and exclusions are kept as supplied:
    the shot plan is a separate field, so nothing that reads `queries` (the anchor-category search) changes."""
    if spec.get("shot_queries"):
        return spec
    queries = list(dict.fromkeys(q.strip() for q in spec.get("queries", []) if q.strip()))
    if not queries:
        raise ValueError("A photo search plan needs a named subject")
    subject = queries[0]
    shots = [{"query": f"{subject} {suffix}", "shot_type": kind} for suffix, kind in (
        ("overview", "wide"), ("interior", "interior"), ("detail", "detail"),
        ("visitors", "people-scale"), ("historical photograph", "archival"), ("close up", "detail"))]
    return {**spec, "shot_queries": shots}


def curated_queries(spec: dict) -> list[str]:
    """The hand-written searches: the topic's own and its shot plan (the museums are asked these)."""
    return list(dict.fromkeys([*spec.get("queries", []), *[shot["query"] for shot in spec.get("shot_queries", [])]]))


def _languages(spec: dict) -> list[str]:
    languages = list(dict.fromkeys(["en", *spec.get("languages", [])]))[:5]
    if any(not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]+)?", language) for language in languages):
        raise ValueError("Invalid photo subject language")
    return languages


def entity_request(spec: dict) -> dict:
    """The wbgetentities parameters for a curated QID or exact Wikipedia sitelink ({} without either)."""
    qid = str(spec.get("qid") or "")
    title = str(spec.get("wikidata_title") or "").strip()
    if not qid and not title:
        return {}
    if qid and not re.fullmatch(r"Q[1-9][0-9]*", qid):
        raise ValueError("Invalid photo subject QID")
    return {"action": "wbgetentities", "format": "json", "props": "labels|aliases|claims",
            "languages": "|".join(_languages(spec)), "maxlag": commons.MAXLAG,
            **({"ids": qid} if qid else {"sites": "enwiki", "titles": title})}


def anchor_from_entities(entities: dict, spec: dict) -> dict:
    """The anchor in a wbgetentities answer: exactly one entity, else none (never a first hit). Live answers and
    the recorded snapshot (data/topics/wikidata_snapshot.json) both go through here."""
    found = [entity for entity in (entities or {}).values()
             if "missing" not in entity and re.fullmatch(r"Q[1-9][0-9]*", str(entity.get("id", "")))]
    if len(found) != 1:
        return {}
    entity = found[0]

    def label(language: str) -> str:
        value = (entity.get("labels", {}).get(language) or {}).get("value")
        return value.strip() if isinstance(value, str) and 1 < len(value.strip()) <= 200 else ""

    # The local-language search is the item's label in the topic's own language, when it differs from English.
    english = commons.normalize(label("en"))
    local = next((label(language) for language in _languages(spec)[1:]
                  if label(language) and commons.normalize(label(language)) != english), "")
    categories = [claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                  for claim in entity.get("claims", {}).get("P373", []) if claim.get("rank") != "deprecated"]
    category = next((value for value in categories if isinstance(value, str) and value.strip()), "")
    return {"qid": entity["id"], "category": category, "local_label": local}


SNAPSHOT_FILE = "wikidata_snapshot.json"  # in config.TOPICS_DIR, beside image_queries.json


def recorded_anchor(spec: dict) -> dict | None:
    """The anchor from the committed answer for this exact request (data/topics/wikidata_snapshot.json), or None
    when it was not recorded. A curated item is then the same on every run, with or without Wikidata reachable."""
    params = entity_request(spec)
    if not params.get("ids"):
        return None
    from . import config
    try:
        topics = json.loads((Path(config.TOPICS_DIR) / SNAPSHOT_FILE).read_text(encoding="utf-8"))["topics"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    for recorded in topics.values():
        if recorded.get("request") == {"ids": params["ids"], "languages": params["languages"]}:
            return anchor_from_entities(recorded.get("entities") or {}, spec)
    return None


def entity_anchor(spec: dict) -> dict:
    """Resolve only a curated QID or exact Wikipedia sitelink, never a first search hit: the recorded answer when
    there is one, otherwise Wikidata, asked live."""
    params = entity_request(spec)
    if not params:
        return {}
    recorded = recorded_anchor(spec)
    if recorded is not None:
        return recorded
    try:
        response = commons._get(WIKIDATA_API, params=params, timeout=30, max_wait=30)
        entities = response.json().get("entities", {})
    except (requests.RequestException, ValueError):
        return {}  # existing curated searches still work during a metadata outage
    return anchor_from_entities(entities, spec)


def query_plan(spec: dict, anchor: dict | None = None) -> tuple[list[str], list[str], dict]:
    """Six or more curated shot queries, plus exact identity and local-language searches. The title keywords are
    the topic's curated keywords, unchanged: these searches find more files, they do not admit more. `anchor`
    replays a recorded entity (anchor_from_entities); without it the curated item is asked live."""
    shots = spec.get("shot_queries")
    if not shots:
        return list(spec["queries"]), list(spec["keywords"]), {}
    if (len({shot.get("query", "").strip() for shot in shots}) < 6
            or len({shot.get("shot_type") for shot in shots} & SHOT_TYPES) < 4
            or any(not shot.get("query", "").strip() or shot.get("shot_type") not in SHOT_TYPES for shot in shots)):
        raise ValueError("Photo search specs need six distinct queries across four shot types")
    anchor = entity_anchor(spec) if anchor is None else anchor
    anchored = []
    if anchor.get("qid"):
        anchored.append(f"haswbstatement:P180={anchor['qid']}")
    if anchor.get("category"):
        anchored.append(f"incategory:{quoted(anchor['category'])}")
    if anchor.get("local_label"):
        anchored.append(quoted(anchor["local_label"]))
    # The topic's own searches keep their place first; the new ones follow.
    queries = list(dict.fromkeys([*spec["queries"], *anchored, *curated_queries(spec)]))
    return queries, list(spec["keywords"]), anchor
