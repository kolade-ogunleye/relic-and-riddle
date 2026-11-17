"""Category fallback uses the same topic identity guards as beat searches."""

import importlib
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from scripts import fetch_commons_images as fetch
from src import commons

SUBJECTS = ("Kailasa Temple Ellora", "Antikythera mechanism", "Houfeng Didong Yi",
            "Voynich manuscript", "Lycurgus Cup", "Gol Gumbaz", "Richat Structure",
            "Amber Room", "Camp Century", "Colossi of Memnon")


def fallback_fixture(root, title):
    """Generated metadata adversary: an unrelated film category precedes the subject."""
    wrong, right = f"Category:{title} (unrelated film)", f"Category:{title}"
    asked = []

    def search(query, **kwargs):
        asked.append(query)
        if not query.startswith("incategory:"):
            return []
        unrelated = "unrelated film" in query
        label = title + (" unrelated film frame" if unrelated else " actual subject")
        return [{"title": f"File:{label}.jpg", "categories": [{"title": wrong if unrelated else right}],
                 "imageinfo": [{"width": 3000, "height": 2500, "size": 2_000_000, "mime": "image/jpeg",
                    "url": "https://upload.wikimedia.org/fixture.jpg",
                    "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]}]

    def download(url, dest, **kwargs):
        Image.new("RGB", (3000, 2500), (120, 100, 80)).save(dest)
        return 3000, 2500

    with ExitStack() as stack:
        for owner, name, replacement in (
            (fetch, "search", search), (fetch, "search_graded", lambda *a, **k: []),
            (fetch, "find_categories", lambda *a, **k: [wrong, right]),
            (fetch, "search_openverse", lambda *a, **k: []), (fetch, "download_jpeg", download),
            (commons, "_files", lambda categories, *a: {category: (1, 0) for category in categories}),
        ):
            stack.enter_context(patch.object(owner, name, replacement))
        stack.enter_context(patch.object(commons, "_ANCHORS", {}))
        stack.enter_context(patch("src.storyboard.generate_storyboard", lambda *a, **k: None))
        ident = title.lower().replace(" ", "_")
        fetch.fetch_topic({"id": ident, "title": title}, {
            "queries": [title], "keywords": [title.lower()], "exclude": ["unrelated restaurant"], "museums": False},
            False, target_images=1, assets_dir=root)
        images = json.loads((root / ident / "credits.json").read_text())["images"]
    return images, asked


@pytest.mark.parametrize("title", SUBJECTS)
def test_category_fallback_rejects_namesake_before_downloading(tmp_path, title):
    images, asked = fallback_fixture(tmp_path, title)
    assert len(images) == 1
    assert "actual subject" in images[0]["title"]
    assert not any("unrelated film" in query for query in asked)


def test_p373_anchor_is_bound_to_explicit_subject_identity(monkeypatch):
    monkeypatch.setattr(commons, "find_categories", lambda *a, **k: pytest.fail("unanchored category lookup"))
    anchor = commons.topic_anchor({"id": "instrument", "title": "Zhang Heng instrument"},
                                  subject_anchor={"qid": "Q15391579", "category": "Houfeng Didong Yi"})
    assert anchor == {"kind": "category", "name": "Houfeng Didong Yi", "deep": False, "qid": "Q15391579"}


def test_fetch_refuses_missing_identity_rules_before_search(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "search", lambda *a, **k: pytest.fail("search before identity check"))
    with pytest.raises(ValueError, match="subject identity"):
        fetch.fetch_topic({"id": "fixture", "title": "Temple"}, {"queries": ["Temple"], "keywords": ["temple"]},
                          False, assets_dir=tmp_path)


def test_all_curated_specs_have_exclusions_or_qid():
    module = importlib.import_module("src.photo_queries")
    specs = json.loads((Path(__file__).parents[1] / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    for key, spec in specs.items():
        if not key.startswith("_"):
            assert spec.get("exclude") or spec.get("qid"), key
            module.require_identity(spec, {})


@pytest.mark.parametrize("exclude", ["film", [""]])
def test_malformed_exclusions_do_not_count_as_identity_rules(exclude):
    module = importlib.import_module("src.photo_queries")
    with pytest.raises(ValueError, match="exclusions"):
        module.require_identity({"exclude": exclude}, {})


def _entities(qid, *, kinds=(), category="Pavlopetri"):
    return {qid: {"id": qid, "labels": {"en": {"value": "Pavlopetri"}},
                  "claims": {"P373": [{"mainsnak": {"datavalue": {"value": category}}}],
                             "P31": [{"mainsnak": {"datavalue": {"value": {"id": kind}}}} for kind in kinds]}}}


def test_a_disambiguation_page_is_not_a_subject():
    module = importlib.import_module("src.photo_queries")
    assert module.anchor_from_entities(_entities("Q1", kinds=["Q4167410"]), {}) == {}
    assert module.anchor_from_entities(_entities("Q1", kinds=["Q839954"]), {})["qid"] == "Q1"


def test_new_topics_carry_their_wikipedia_title_as_the_exact_sitelink():
    module = importlib.import_module("src.photo_queries")
    spec = module.structured_spec({"queries": ["Pavlopetri"], "keywords": ["pavlopetri"]}, wikipedia_title="Pavlopetri")
    assert spec["wikidata_title"] == "Pavlopetri"
    assert module.entity_request(spec)["titles"] == "Pavlopetri"
    curated = module.structured_spec({"queries": ["X"], "keywords": ["x"], "qid": "Q5"}, wikipedia_title="Other")
    assert "wikidata_title" not in curated  # a curated QID is never replaced by a title


def test_a_proposal_is_verified_by_the_wikipedia_page_it_cites():
    from src import topic_proposer
    from src.models import ProposedLocation, Source, TopicProposal
    proposal = TopicProposal(
        slug="pavlopetri", title="The Sunken Town of Pavlopetri", sub_theme="Ancient Engineering", hook="A hook.",
        location=ProposedLocation(name="Site", city="C", region="R", country="X"),
        key_facts=["f1", "f2", "f3", "f4", "f5"], hashtags=["#a"] * 7, image_queries=["site"],
        image_keywords=["site"], why_it_works="Visuals.",
        sources=[Source(title="s", url="https://real.org/a"), Source(title="w", url="https://en.wikipedia.org/wiki/Pavlopetri")])
    assert topic_proposer._wikipedia_title(proposal) == "Pavlopetri"
    assert topic_proposer._wikipedia_title(proposal.model_copy(update={"sources": []})) == "The Sunken Town of Pavlopetri"


def _new_topic_fetch(monkeypatch, tmp_path, answer):
    module = importlib.import_module("src.photo_queries")
    searched = []

    class Response:
        def json(self):
            return {"entities": answer}

    monkeypatch.setattr(commons, "_get", lambda *a, **k: Response())
    monkeypatch.setattr(fetch, "search", lambda q, **k: searched.append(q) or [])
    for name in ("search_graded", "find_categories", "search_openverse"):
        monkeypatch.setattr(fetch, name, lambda *a, **k: [])
    monkeypatch.setattr("src.storyboard.generate_storyboard", lambda *a, **k: None)
    spec = module.structured_spec({"queries": ["Pavlopetri"], "keywords": ["pavlopetri"], "museums": False},
                                  wikipedia_title="Pavlopetri")
    fetch.fetch_topic({"id": "pavlopetri", "title": "Pavlopetri"}, spec, False, assets_dir=tmp_path)
    return searched


def test_a_new_topic_whose_wikipedia_page_resolves_is_fetched(monkeypatch, tmp_path):
    searched = _new_topic_fetch(monkeypatch, tmp_path, _entities("Q1655457"))
    assert "haswbstatement:P180=Q1655457" in searched and "Pavlopetri" in searched


@pytest.mark.parametrize("answer", [{}, _entities("Q1", kinds=["Q4167410"])])
def test_a_new_topic_without_a_verified_subject_is_held_with_the_upload_route(monkeypatch, tmp_path, answer):
    with pytest.raises(ValueError, match="subject identity.*/photos N upload"):
        _new_topic_fetch(monkeypatch, tmp_path, answer)


def test_a_held_fetch_tells_the_automatic_path_why_and_a_verified_one_clears_it(monkeypatch, tmp_path):
    from src import asset_pipeline
    with pytest.raises(ValueError):
        _new_topic_fetch(monkeypatch, tmp_path, {})
    reason = asset_pipeline.not_ready_reason("pavlopetri", tmp_path)
    assert "held" in reason and '"Pavlopetri" is not an exact English Wikipedia page title' in reason
    assert "/photos N upload" in reason and "found nothing new" not in reason
    _new_topic_fetch(monkeypatch, tmp_path, _entities("Q1655457"))
    assert "fetch_held" not in json.loads((tmp_path / "pavlopetri" / "credits.json").read_text(encoding="utf-8"))


def test_the_command_line_fetch_goes_on_past_a_held_topic(db, monkeypatch, tmp_path, capsys):
    (tmp_path / "image_queries.json").write_text(json.dumps(
        {f"0{i}_topic": {"queries": ["q"], "keywords": ["k"]} for i in range(1, 4)}), encoding="utf-8")
    fetched = []

    def fetch_topic(topic, *args, **kwargs):
        fetched.append(topic["id"])
        if topic["id"] == "01_topic":
            raise ValueError("Photo search needs a verified subject identity")
        return 99

    monkeypatch.setattr(fetch, "TOPICS_DIR", tmp_path)
    monkeypatch.setattr(fetch, "PipelineDatabase", lambda: db)
    monkeypatch.setattr(fetch, "fetch_topic", fetch_topic)
    monkeypatch.setattr("sys.argv", ["fetch_commons_images.py"])
    assert fetch.main() == 2
    assert fetched == ["01_topic", "02_topic", "03_topic"]
    assert "01_topic: held: Photo search needs a verified subject identity" in capsys.readouterr().out


def test_a_topic_added_in_telegram_records_its_title_as_the_sitelink(db, tmp_path, monkeypatch):
    from src import topic_proposer
    from src.control import make_command_handler
    queries = tmp_path / "image_queries.json"
    queries.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(topic_proposer, "BANK", db.topic_bank_file)
    monkeypatch.setattr(topic_proposer, "QUERIES", queries)
    assert "Added" in make_command_handler(db, lambda: None, lambda: "")("add", "Pavlopetri")
    assert json.loads(queries.read_text(encoding="utf-8"))["pavlopetri"]["wikidata_title"] == "Pavlopetri"


def test_an_approved_proposal_is_added_inside_the_bank_lock_with_its_cited_page(db, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from src import topic_proposer, topic_store
    from src.models import ProposedLocation, Source, TopicProposal
    queries = tmp_path / "image_queries.json"
    queries.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(topic_proposer, "BANK", db.topic_bank_file)
    monkeypatch.setattr(topic_proposer, "QUERIES", queries)
    proposal = TopicProposal(
        slug="pavlopetri", title="The Sunken Town of Pavlopetri", sub_theme="Ancient Engineering", hook="A hook.",
        location=ProposedLocation(name="Site", city="C", region="R", country="X"),
        key_facts=["f1", "f2", "f3", "f4", "f5"], hashtags=["#a"] * 7, image_queries=["site"],
        image_keywords=["site"], why_it_works="Visuals.",
        sources=[Source(title="w", url="https://en.wikipedia.org/wiki/Pavlopetri")])
    db.set_setting(topic_proposer.PENDING_KEY, json.dumps({"pavlopetri": proposal.model_dump(mode="json")}))
    held, real_transaction, real_commit = [], topic_store.transaction, topic_store.commit

    @contextmanager
    def transaction(path):
        held.append(True)
        with real_transaction(path):
            yield
        held.pop()

    def commit(*args):
        assert held, "the bank and its image searches were written outside the topic-bank lock"
        return real_commit(*args)

    monkeypatch.setattr(topic_store, "transaction", transaction)
    monkeypatch.setattr(topic_store, "commit", commit)
    assert topic_proposer.add(db, "pavlopetri") == "pavlopetri"
    assert json.loads(queries.read_text(encoding="utf-8"))["pavlopetri"]["wikidata_title"] == "Pavlopetri"
