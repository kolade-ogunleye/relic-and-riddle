"""QF-54a acceptance: python measure.py <repo root>. Prints JSON to stdout; writes nothing.

Committed inputs, beside this file:
- before.json: the parent commit's data/topics/image_queries.json (the base plan).
- commons_sample.json: real Commons results for the five test topics, recorded by record_commons_sample.py at the
  fetcher's own limits, for every base query and every QF-54a query.
- candidate_labels.json: a shows_topic label for every file either plan admits on that sample.
The QF-54a plan is rebuilt offline from data/topics/wikidata_snapshot.json through the same entity parser as a
live Wikidata answer. Candidates are admitted by src.commons.candidates() at <repo root> (the title rule) and the
topic's exclusions, as the fetcher does.
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _code(root: Path):
    sys.path.insert(0, str(root))
    from src import commons
    from src.credits import normalize
    return commons, normalize


def plan(root: Path, topic: str, spec: dict) -> tuple[list[str], list[str]]:
    """The QF-54a query plan and title keywords, from the recorded Wikidata answer (parser of a live answer)."""
    _code(root)
    from src import photo_queries
    recorded = json.loads((root / "data/topics/wikidata_snapshot.json").read_text(encoding="utf-8"))["topics"][topic]
    queries, keywords, _ = photo_queries.query_plan(
        spec, anchor=photo_queries.anchor_from_entities(recorded["entities"], spec))
    return queries, keywords


def admitted(sample: dict, recorded: dict, queries: list[str], spec: dict, allow_sa: bool) -> dict:
    commons, normalize = _code(Path(sys.argv[1]).resolve())
    exclude = [normalize(x) for x in spec.get("exclude", [])]
    found = {}
    for query in queries:
        pages = [sample["pages"][title] for title in recorded["results"][query]]
        for cand in commons.candidates(pages, spec["keywords"], allow_sa):
            text = normalize(" ".join([cand.get("title", ""), cand.get("description", "")] + cand.get("categories", [])))
            if not any(x in text for x in exclude):
                found.setdefault(cand["title"], cand)
    return found


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    _, normalize = _code(root)
    from src import museum_sources
    before = json.loads((HERE / "before.json").read_text(encoding="utf-8"))
    specs = json.loads((root / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    snapshot_meta = json.loads((root / "data/topics/wikidata_snapshot.json").read_text(encoding="utf-8"))
    snapshot = snapshot_meta["topics"]
    topics = [name for name in specs if not name.startswith("_")]
    out = {}

    # 1. Identity: every topic has a curated QID, recorded with an English label, alias or Commons category that
    # names the topic.
    from src import photo_queries

    def names_topic(n: str) -> bool:
        recorded = snapshot[n]["entities"]
        entity = recorded.get(specs[n].get("qid"), {})
        anchor = photo_queries.anchor_from_entities(recorded, specs[n])
        english = [entity.get("labels", {}).get("en", {}).get("value", ""), anchor.get("category", ""),
                   *[a["value"] for a in entity.get("aliases", {}).get("en", [])]]
        names = normalize(" | ".join(english))
        title = normalize(specs[n].get("wikidata_title", ""))
        return anchor.get("qid") == specs[n].get("qid") and (
            any(normalize(k) in names for k in specs[n]["keywords"]) or bool(title and title in names))

    named = [n for n in topics if n in snapshot and names_topic(n)]
    out["identity"] = {"topics": len(topics), "with_curated_qid": sum(bool(specs[n].get("qid")) for n in topics),
                       "qid_recorded_and_named": len(named), "snapshot_recorded_utc": snapshot_meta["recorded_utc"]}
    out["admission_fields_equal_parent"] = all(specs[n].get(f) == before[n].get(f) for n in topics
                                               for f in ("queries", "keywords", "exclude"))

    # 2. Searches per plan, and the searches each museum source receives (curated ones only).
    plans = {n: plan(root, n, specs[n]) for n in topics}
    curated = {n: photo_queries.curated_queries(specs[n]) for n in topics}
    out["searches"] = {
        "commons_base": sum(len(before[n]["queries"]) for n in topics),
        "commons_qf54a": sum(len(plans[n][0]) for n in topics),
        "identity_searches": sum(q.startswith(("haswbstatement:", "incategory:")) for n in topics for q in plans[n][0]),
        "local_label_searches": sum(q.startswith('"') for n in topics for q in plans[n][0]),
        # search_all asks BASE_SOURCES (a keyed one only with its key) plus the operator-enabled extra sources.
        "museum_sources_always": list(museum_sources.BASE_SOURCES),
        "museum_sources_when_enabled": [s for s in museum_sources.SOURCES if s not in museum_sources.BASE_SOURCES],
        "museum_searches_per_source_base": sum(len(before[n]["queries"]) for n in topics),
        "museum_searches_per_source_qf54a": sum(len(curated[n]) for n in topics),
        "every_base_search_still_runs": all(set(before[n]["queries"]) <= set(plans[n][0]) for n in topics),
        "base_searches_run_first": all(plans[n][0][:len(before[n]["queries"])] == before[n]["queries"] for n in topics),
        "every_base_search_still_sent_to_museums": all(set(before[n]["queries"]) <= set(curated[n]) for n in topics),
        # One fetch pass asks Commons twice per search (search_graded + search) and each museum source once per
        # curated search; a refill that falls short of its target runs a second, share-alike pass.
        "commons_requests_per_pass": {"base": 2 * sum(len(before[n]["queries"]) for n in topics),
                                      "qf54a": 2 * sum(len(plans[n][0]) for n in topics)},
        "commons_requests_per_pass_one_topic_max": {"base": 2 * max(len(before[n]["queries"]) for n in topics),
                                                    "qf54a": 2 * max(len(plans[n][0]) for n in topics)},
        "museum_requests_per_source_per_pass_one_topic_max": {
            "base": max(len(before[n]["queries"]) for n in topics),
            "qf54a": max(len(curated[n]) for n in topics)},
    }

    # 3. Title keywords on every real record with a vision verdict (tracked assets/*/credits.json).
    gained, lost = Counter(), Counter()
    for credits in sorted((root / "assets").glob("*/credits.json")):
        name = credits.parent.name
        if name not in plans:
            continue
        old = [normalize(k) for k in before[name]["keywords"]]
        new = [normalize(k) for k in plans[name][1]]
        data = json.loads(credits.read_text(encoding="utf-8"))
        for record in [*data.get("images", []), *data.get("rejected_by_review", []), *data.get("diversity_reserve", [])]:
            label = (record.get("vision_review") or {}).get("shows_topic")
            if not label:
                continue
            title = normalize(record.get("title", ""))
            was, now = any(k in title for k in old), any(k in title for k in new)
            if now and not was:
                gained[label] += 1
            if was and not now:
                lost[label] += 1
    out["title_keywords_on_real_records"] = {"newly_matched": dict(gained), "no_longer_matched": dict(lost)}

    # 4. Candidates on the recorded real Commons sample, base plan against QF-54a plan.
    sample = json.loads((HERE / "commons_sample.json").read_text(encoding="utf-8"))
    labels = json.loads((HERE / "candidate_labels.json").read_text(encoding="utf-8"))["labels"]
    rows, unlabelled = {}, []
    for name, recorded in sample["topics"].items():
        row = {}
        for mode, allow_sa in (("open_licence", False), ("with_share_alike", True)):
            base = admitted(sample, recorded, recorded["base_queries"], before[name], allow_sa)
            new = admitted(sample, recorded, plans[name][0], {**specs[name], "keywords": plans[name][1]}, allow_sa)
            unlabelled += [t for t in {**base, **new} if t not in labels]
            row[mode] = {"base": len(base), "qf54a": len(new), "lost": sorted(set(base) - set(new)),
                         "base_by_label": dict(Counter(labels.get(t, {}).get("shows_topic", "unlabelled") for t in base)),
                         "qf54a_by_label": dict(Counter(labels.get(t, {}).get("shows_topic", "unlabelled") for t in new))}
        rows[name] = row
    totals = {mode: {"base": sum(r[mode]["base"] for r in rows.values()),
                     "qf54a": sum(r[mode]["qf54a"] for r in rows.values()),
                     "base_no": sum(r[mode]["base_by_label"].get("no", 0) for r in rows.values()),
                     "qf54a_no": sum(r[mode]["qf54a_by_label"].get("no", 0) for r in rows.values())}
              for mode in ("open_licence", "with_share_alike")}
    for total in totals.values():
        total["ratio"] = round(total["qf54a"] / max(1, total["base"]), 2)
        total["base_no_share"] = round(total["base_no"] / max(1, total["base"]), 3)
        total["qf54a_no_share"] = round(total["qf54a_no"] / max(1, total["qf54a"]), 3)
    zero = {name: sorted(q for q in recorded["qf54a_queries"] if q not in recorded["base_queries"]
                         and not recorded["results"][q]) for name, recorded in sample["topics"].items()}
    out["real_commons_sample"] = {"recorded_utc": sample["recorded_utc"], "base_commit": sample["base_commit"],
                                  "topics": rows, "totals": totals, "new_searches_with_no_result": zero,
                                  "plan_matches_recording": all(plans[n][0] == r["qf54a_queries"]
                                                                for n, r in sample["topics"].items()),
                                  "topics_at_least_2x_open": sum(r["open_licence"]["qf54a"] >= 2 * max(1, r["open_licence"]["base"])
                                                                 for r in rows.values()),
                                  "unlabelled_candidates": unlabelled}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    if unlabelled or not out["real_commons_sample"]["plan_matches_recording"]:
        sys.exit("unlabelled candidates, or the plan differs from the recorded sample: the measurement is incomplete")


if __name__ == "__main__":
    main()
