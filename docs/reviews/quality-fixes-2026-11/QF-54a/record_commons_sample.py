"""Records the real Commons search results that QF-54a's measure.py replays. Run once by hand, never by tests:

    python record_commons_sample.py <repo root> <base commit>

Read-only and free: for each of the five test topics it runs, through the fetcher's own functions and at their
own limits (search_graded + search, 50 results each), every query of the base plan (the given base commit's
curated queries) and of the QF-54a plan (built from the committed Wikidata snapshot through the parser of a live
answer, so measure.py rebuilds the same plan offline). It writes commons_sample.json beside this file: each
query's result titles in order, and each file's page metadata as the API returned it (the fields candidates()
reads). No image is downloaded.
"""
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import commons, photo_queries

TOPICS = ["antikythera_mechanism", "hal_saflieni_hypogeum", "zhang_heng_seismoscope", "voynich_manuscript",
          "kailasa_temple_monolith"]


def plan(spec: dict, recorded: dict) -> list[str]:
    """The QF-54a plan from the recorded Wikidata answer, through the parser of a live answer."""
    return photo_queries.query_plan(spec, anchor=photo_queries.anchor_from_entities(recorded["entities"], spec))[0]


def main() -> None:
    # The base plan is the given commit's (the parent's) curated queries.
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout
    base_commit = git("rev-parse", "--short", sys.argv[2]).strip()
    base_specs = json.loads(git("show", f"{sys.argv[2]}:data/topics/image_queries.json"))
    specs = json.loads((ROOT / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    snapshot = json.loads((ROOT / "data/topics/wikidata_snapshot.json").read_text(encoding="utf-8"))["topics"]
    pages, topics = {}, {}
    for name in TOPICS:
        base_queries = list(base_specs[name]["queries"])
        new_queries = plan(specs[name], snapshot[name])
        results = {}
        for query in dict.fromkeys([*base_queries, *new_queries]):
            titles = []
            for page in commons.search_graded(query) + commons.search(query):
                pages.setdefault(page["title"], page)
                if page["title"] not in titles:
                    titles.append(page["title"])
            results[query] = titles
        topics[name] = {"base_queries": base_queries, "qf54a_queries": new_queries, "results": results}
    out = {"recorded_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"), "base_commit": base_commit,
           "method": "src.commons.search_graded(q) + search(q), limit 50 each, as the fetcher calls them",
           "topics": topics, "pages": pages}
    (HERE / "commons_sample.json").write_text(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
                                              encoding="utf-8", newline="\n")
    print(len(pages), "pages;", sum(len(t["results"]) for t in topics.values()), "queries")


if __name__ == "__main__":
    main()
