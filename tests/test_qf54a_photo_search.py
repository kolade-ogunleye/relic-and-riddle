"""QF-54a: structured shot searches from a verified Wikidata item; the title rule that admits a file is unchanged."""

import json
from pathlib import Path

import pytest

from src import commons
from src import photo_queries as module

ROOT = Path(__file__).parents[1]
SPECS = json.loads((ROOT / "data/topics/image_queries.json").read_text(encoding="utf-8"))
SNAPSHOT = json.loads((ROOT / "data/topics/wikidata_snapshot.json").read_text(encoding="utf-8"))["topics"]
BEFORE = json.loads((ROOT / "docs/reviews/quality-fixes-2026-11/QF-54a/before.json").read_text(encoding="utf-8"))
TOPICS = {name: spec for name, spec in SPECS.items() if not name.startswith("_")}


def test_every_curated_topic_has_six_queries_over_four_shot_types():
    assert len(TOPICS) == 31
    for spec in TOPICS.values():
        assert len({s["query"] for s in spec["shot_queries"]}) >= 6
        assert len({s["shot_type"] for s in spec["shot_queries"]} & module.SHOT_TYPES) >= 4
        assert len(module.query_plan(spec, anchor={})[0]) >= 6


@pytest.mark.parametrize("name", sorted(TOPICS))
def test_admission_fields_are_the_parents_exactly(name):
    # QF-54a adds searches only. The keywords and exclusions that admit a file, and the topic's own searches (also
    # read by beat_images._other_names for the anchor category), are the parent's, value for value.
    for field in ("queries", "keywords", "exclude"):
        assert TOPICS[name].get(field) == BEFORE[name].get(field), field
    assert module.query_plan(TOPICS[name], anchor={"qid": "Q1", "local_label": "Λ"})[1] == BEFORE[name]["keywords"]


@pytest.mark.parametrize("name", sorted(TOPICS))
def test_curated_qid_is_the_recorded_entity_of_this_topic(name):
    spec, recorded = TOPICS[name], SNAPSHOT[name]
    assert recorded["request"]["ids"] == spec["qid"]
    anchor = module.anchor_from_entities(recorded["entities"], spec)
    assert anchor["qid"] == spec["qid"]
    # The recorded entity must name the topic: its English label, aliases or Commons category share a keyword or
    # the Wikipedia title (a wrong QID with its own true label would not).
    entity = recorded["entities"][spec["qid"]]
    english = [entity.get("labels", {}).get("en", {}).get("value", ""), anchor["category"],
               *[a["value"] for a in entity.get("aliases", {}).get("en", [])]]
    names = commons.normalize(" | ".join(english))
    title = commons.normalize(spec.get("wikidata_title", ""))
    assert any(commons.normalize(k) in names for k in spec["keywords"]) or (title and title in names)


@pytest.mark.parametrize("name", sorted(TOPICS))
def test_a_run_plans_from_the_recorded_answer_without_asking_wikidata(monkeypatch, name):
    # The plan a run uses is the measured plan: a curated QID is read from the committed answer, on every run.
    spec, recorded = TOPICS[name], SNAPSHOT[name]
    monkeypatch.setattr(commons, "_get", lambda *a, **k: pytest.fail("asked Wikidata for a recorded item"))
    assert recorded["request"] == {k: module.entity_request(spec)[k] for k in ("ids", "languages")}
    assert module.query_plan(spec) == module.query_plan(spec, anchor=module.anchor_from_entities(
        recorded["entities"], spec))


@pytest.mark.parametrize("name", sorted(TOPICS))
def test_a_live_answer_goes_through_the_same_parser(monkeypatch, name):
    spec, recorded = TOPICS[name], SNAPSHOT[name]
    monkeypatch.setattr(module, "recorded_anchor", lambda spec: None)  # an item not in the snapshot

    class Response:
        def json(self):
            return {"entities": recorded["entities"]}

    monkeypatch.setattr(commons, "_get", lambda url, **kwargs: Response())
    assert module.entity_anchor(spec) == module.anchor_from_entities(recorded["entities"], spec)


def test_wikidata_supplies_depicts_category_and_local_language_searches(monkeypatch):
    calls = []

    class Response:
        def json(self):
            return {"entities": {"Q182324": {"id": "Q182324",
                "labels": {"en": {"value": "Antikythera mechanism"}, "el": {"value": "Μηχανισμός των Αντικυθήρων"}},
                "aliases": {"el": [{"value": "Μηχανισμός Αντικυθήρων"}], "en": [{"value": "Antikythera computer"},
                                                                               {"value": "Ancient computer"}]},
                "claims": {"P373": [{"mainsnak": {"datavalue": {"value": "Antikythera mechanism"}}}]}}}}

    monkeypatch.setattr(commons, "_get", lambda url, **kwargs: calls.append(kwargs) or Response())
    monkeypatch.setattr(module, "recorded_anchor", lambda spec: None)  # the live path
    spec = module.structured_spec({"queries": ["Antikythera mechanism"], "keywords": ["antikythera"],
                                   "qid": "Q182324", "languages": ["el"]})
    planned, keywords, anchor = module.query_plan(spec)
    assert calls[0]["params"]["languages"] == "en|el" and calls[0]["params"]["ids"] == "Q182324"
    assert planned[0] == "Antikythera mechanism"  # the topic's own search keeps its place
    assert "haswbstatement:P180=Q182324" in planned
    assert 'incategory:"Antikythera mechanism"' in planned
    assert '"Μηχανισμός των Αντικυθήρων"' in planned  # the label in the topic's language, not an alias
    assert keywords == ["antikythera"]  # no alias becomes a title keyword
    assert anchor["qid"] == "Q182324"


def test_a_local_label_equal_to_the_english_one_is_not_searched_twice():
    entities = {"Q755514": {"id": "Q755514", "labels": {"en": {"value": "Ħal Saflieni Hypogeum"},
                                                        "mt": {"value": "Ħal Saflieni Hypogeum"}}}}
    assert module.anchor_from_entities(entities, {"languages": ["mt"]})["local_label"] == ""


def test_ambiguous_entity_response_does_not_select_first_hit(monkeypatch):
    class Response:
        def json(self):
            return {"entities": {"Q1": {"id": "Q1"}, "Q2": {"id": "Q2"}}}

    monkeypatch.setattr(commons, "_get", lambda *a, **k: Response())
    assert module.entity_anchor({"wikidata_title": "Ambiguous name"}) == {}


def test_new_topics_receive_a_structured_plan():
    spec = module.structured_spec({"queries": ["Example temple"], "keywords": ["example"]})
    assert len(spec["shot_queries"]) >= 6
    assert len({s["shot_type"] for s in spec["shot_queries"]}) >= 4
    assert spec["queries"] == ["Example temple"]  # the shot plan is its own field
    spec["shot_queries"] = spec["shot_queries"][:2]
    with pytest.raises(ValueError, match="six distinct"):
        module.query_plan(spec)


def _page(title, categories=()):
    return {"title": title, "categories": [{"title": "Category:" + c} for c in categories], "imageinfo": [{
        "width": 3000, "height": 2500, "size": 2_000_000, "mime": "image/jpeg",
        "url": "https://upload.wikimedia.org/fixture.jpg", "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]}


def test_the_title_rule_is_unchanged_a_category_alone_admits_nothing():
    # QF-54a adds searches only; admitting by category or depicts is QF-54b.
    found = list(commons.candidates([_page("File:DSC1000.jpg", ["Antikythera mechanism"]),
                                     _page("File:Antikythera fragment A.jpg")], ["antikythera"]))
    assert [c["title"] for c in found] == ["File:Antikythera fragment A.jpg"]


def test_fetch_runs_identity_searches_on_commons_and_curated_searches_on_museums(monkeypatch, tmp_path):
    from scripts import fetch_commons_images as fetch

    class Response:
        def json(self):
            return {"entities": {"Q182324": {"id": "Q182324", "labels": {"el": {"value": "Μηχανισμός"}},
                                              "claims": {"P373": [{"mainsnak": {"datavalue": {"value": "Antikythera"}}}]}}}}

    commons_queries, museum_queries = [], []
    monkeypatch.setattr(commons, "_get", lambda *a, **k: Response())
    monkeypatch.setattr(module, "recorded_anchor", lambda spec: None)  # the live path
    monkeypatch.setattr(fetch, "search", lambda q, **k: commons_queries.append(q) or [])
    monkeypatch.setattr(fetch, "search_graded", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "find_categories", lambda *a, **k: [])
    monkeypatch.setattr(fetch, "search_openverse", lambda *a, **k: [])
    monkeypatch.setattr("src.museum_sources.search_all", lambda q, kws: museum_queries.append(q) or [])
    monkeypatch.setattr("src.storyboard.generate_storyboard", lambda *a, **k: None)
    kinds = ["wide", "interior", "detail", "people-scale", "archival", "detail"]
    spec = {"queries": ["Antikythera mechanism", *[f"Antikythera {i}" for i in range(6)]],
            "keywords": ["antikythera"], "qid": "Q182324", "languages": ["el"],
            "shot_queries": [{"query": f"Antikythera {i}", "shot_type": kind} for i, kind in enumerate(kinds)]}
    fetch.fetch_topic({"id": "fixture", "title": "Antikythera mechanism"}, spec, False, target_images=6,
                      assets_dir=tmp_path)
    assert {"haswbstatement:P180=Q182324", 'incategory:"Antikythera"', '"Μηχανισμός"'} <= set(commons_queries)
    assert set(museum_queries) == set(module.curated_queries(spec))
    assert not {q for q in museum_queries if q.startswith(("haswbstatement:", "incategory:", '"'))}
