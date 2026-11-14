"""Records data/topics/wikidata_snapshot.json, which tests and measure.py replay offline. Run by hand, never by tests:

    python record_wikidata_snapshot.py <repo root>

Read-only and free: one wbgetentities read of every curated QID, with the fetcher's User-Agent. Each topic keeps
the entity as its own request (photo_queries.entity_request: English plus the topic's languages) returns it, with
one cut: of the claims, only P373 (Commons category), the one claim the parser reads. photo_queries reads the file
for a curated QID through anchor_from_entities, the same parser as a live answer.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))

import requests

from src import commons, photo_queries


def _cut(entity: dict, languages: list[str]) -> dict:
    """The entity as a request for these languages returns it; of the claims, P373 only."""
    entity = dict(entity)
    for field in ("labels", "aliases"):
        entity[field] = {k: v for k, v in entity.get(field, {}).items() if k in languages}
    entity["claims"] = {"P373": entity.get("claims", {}).get("P373", [])}
    return entity


def main() -> None:
    specs = json.loads((ROOT / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    requests_by_topic = {name: photo_queries.entity_request(spec) for name, spec in specs.items()
                         if not name.startswith("_") and spec.get("qid")}
    # One read for all items (wbgetentities takes up to 50 ids), in every language any topic asks for; each topic
    # then keeps only its own languages, which is what its own request returns.
    ids = sorted({params["ids"] for params in requests_by_topic.values()})
    languages = sorted({lang for params in requests_by_topic.values() for lang in params["languages"].split("|")})
    request = {"action": "wbgetentities", "format": "json", "props": "labels|aliases|claims",
               "ids": "|".join(ids), "languages": "|".join(languages)}
    # Sent without maxlag: on 2026-11-09 Wikidata folded its query-service lag (about 12 s, for over 40 minutes)
    # into maxlag, so the fetcher's maxlag=5 refused every read. maxlag is the courtesy the API docs ask of bots
    # that edit; this is one read, by hand, with the fetcher's own User-Agent.
    response = requests.get(photo_queries.WIKIDATA_API, params=request, headers=commons.HEADERS, timeout=60)
    response.raise_for_status()
    answer = response.json()
    if "error" in answer:
        raise SystemExit(str(answer["error"]))
    found = answer.get("entities", {})
    topics = {}
    for name, params in requests_by_topic.items():
        own = params["languages"].split("|")
        topics[name] = {"request": {"ids": params["ids"], "languages": params["languages"]},
                        "entities": {params["ids"]: _cut(found[params["ids"]], own)}}
    out = {"_doc": "wbgetentities answers for each topic's curated QID, in the topic's own request languages "
                   "(claims cut to P373), recorded by docs/reviews/quality-fixes-2026-11/QF-54a/"
                   "record_wikidata_snapshot.py. src/photo_queries.py reads it for a curated QID (live Wikidata "
                   "only for an item not recorded here) through anchor_from_entities, the parser of a live answer.",
           "recorded_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"), "topics": topics}
    (ROOT / "data/topics/wikidata_snapshot.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                                            encoding="utf-8", newline="\n")
    print(len(topics), "topics")


if __name__ == "__main__":
    main()
