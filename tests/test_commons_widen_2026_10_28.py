"""
Commons search matches every word, so a precise per-beat query often finds nothing (dev Nazca run
_1793251266, 2026-10-28: "Nazca lines ground level trench" 0 results; "ground" in the Nazca lines category 23).
A query with few results is tried again shorter, restricted to what the topic structurally is: its own Commons
category, else its place's, else its coordinates; nothing to anchor on, no widening. Title words are never the
anchor ("Under", "Salt", "Roman" found railway tunnels, Salt Lake City and other Roman kilns: review 2026-10-29).
Titles, places and categories below are test data shaped like the bank's; Commons is stubbed.
"""

import pytest
import requests

from src import beat_images, commons, config
from src.models import BeatQueries, BeatQuery

NAZCA = {"kind": "category", "name": "Nazca lines", "deep": True}


@pytest.fixture(autouse=True)
def fresh_anchors(monkeypatch):
    monkeypatch.setattr(commons, "_ANCHORS", {})


def _pages(prefix, n):
    return [{"pageid": f"{prefix}{i}", "title": f"File:{prefix} {i}.jpg"} for i in range(n)]


def _stub(monkeypatch, answers):
    """commons.search answering `answers[query]` pages (0 for any other query); returns the queries asked."""
    asked = []

    def search(q, limit=50, max_wait=60):
        asked.append(q)
        a = answers.get(q, 0)
        return _pages(q, a) if isinstance(a, int) else list(a)
    monkeypatch.setattr(commons, "search", search)
    return asked


def _categories(monkeypatch, found, files):
    """find_categories answering `found[query]`; each category's (files, subcategories) from `files` (a number:
    that many files, no subcategories; absent: none)."""
    asked = []
    monkeypatch.setattr(commons, "find_categories", lambda q, limit=5, max_wait=30: asked.append(q) or found.get(q, []))
    count = lambda v: v if isinstance(v, tuple) else (v, 0)  # noqa: E731
    monkeypatch.setattr(commons, "_files", lambda cats, max_wait: {c: count(files.get(c, 0)) for c in cats})
    return asked


def _filed(category, n):
    """`n` approved photo records filed in `category`."""
    return [{"title": f"File:Photo {i}.jpg", "categories": [category, "Self-published work"]} for i in range(n)]


# ------------------------------------------------------------------ the anchor
def test_the_topics_own_category_is_the_anchor(monkeypatch):
    _categories(monkeypatch, {"The Nazca Lines": ["Category:Nazca lines", "Category:Palpa lines"]},
                {"Category:Nazca lines": 600, "Category:Palpa lines": 90})
    anchor = commons.topic_anchor({"id": "nazca_lines", "title": "The Nazca Lines", "location": {"name": "Nazca Desert"}})
    assert anchor == NAZCA                                     # Palpa is not this topic's name
    assert commons.anchor_filter(anchor) == 'deepcategory:"Nazca lines"'


def test_the_category_the_topics_photos_name_wins_over_a_title_word(monkeypatch):
    """"The Mystery (Band)" matched a title word; the topic's photos are in the Pantheon's category."""
    _categories(monkeypatch, {"The Mystery": ["Category:The Mystery (Band)"],
                              "Pantheon": ["Category:Pantheon (Rome)"]},
                {"Category:The Mystery (Band)": 30, "Category:Pantheon (Rome)": 50})
    photos = [{"title": f"File:Pantheon dome {i}.jpg", "categories": ["Pantheon (Rome) - Dome interior"]} for i in range(3)]
    topic = {"id": "roman_concrete", "title": "The Mystery of Roman Concrete", "location": {"name": "Pantheon"}}
    assert commons.topic_anchor(topic, photos=photos) == {"kind": "category", "name": "Pantheon (Rome)", "deep": False}
    assert commons.anchor_filter(commons.topic_anchor(topic, photos=photos)) == 'incategory:"Pantheon (Rome)"'


def test_the_fuller_name_then_the_titles_proper_name_wins(monkeypatch):
    _categories(monkeypatch, {"The Great Wall of Gorgan": ["Category:The Great Wall (film)", "Category:Gorgan",
                                                           "Category:Great Wall of Gorgan"]},
                {"Category:The Great Wall (film)": 9, "Category:Gorgan": 99, "Category:Great Wall of Gorgan": 20})
    topic = {"id": "wall_of_gorgan", "title": "The Great Wall of Gorgan", "location": {"name": "Golestan"}}
    assert commons.topic_anchor(topic)["name"] == "Great Wall of Gorgan"
    # Two names as full as each other: the one found from the part after "of" (the proper name), not "Sunken City".
    _categories(monkeypatch, {"Sunken City": ["Category:Sunken City"], "Thonis-Heracleion": ["Category:Heracleion (Thonis)"],
                              "The Sunken City of Thonis-Heracleion": ["Category:Sunken City"]},
                {"Category:Sunken City": 40, "Category:Heracleion (Thonis)": 20})
    commons._ANCHORS.clear()
    topic = {"id": "sunken_city", "title": "The Sunken City of Thonis-Heracleion", "location": {"name": "Abukir Bay"}}
    assert commons.topic_anchor(topic)["name"] == "Heracleion (Thonis)"


def test_a_category_without_files_is_passed_over_and_a_spelling_variant_counts(monkeypatch):
    _categories(monkeypatch, {"Odessa Catacombs": ["Category:Odessa catacombs", "Category:Odesa catacombs"]},
                {"Category:Odessa catacombs": 0, "Category:Odesa catacombs": 39})
    anchor = commons.topic_anchor({"id": "odessa_catacombs", "title": "The 1,500-Mile Odessa Catacombs",
                                   "location": {"name": "Odessa"}})
    assert anchor["name"] == "Odesa catacombs"


def test_coordinates_when_there_is_no_category_and_nothing_without_either(monkeypatch):
    asked = _categories(monkeypatch, {}, {})
    topic = {"id": "x", "title": "Operation Quiet Cat", "location": {"name": "Somewhere", "latitude": 38.95,
                                                                   "longitude": -77.15}}
    anchor = commons.topic_anchor(topic)
    assert anchor == {"kind": "near", "lat": 38.95, "lon": -77.15}
    assert commons.anchor_filter(anchor) == f"nearcoord:{commons.NEAR_KM}km,38.9500,-77.1500"
    assert commons.topic_anchor(dict(topic, id="y", location={"latitude": 0.0, "longitude": 0.0})) is None
    # Looked up once (cached), asked again only after a while
    n = len(asked)
    commons.topic_anchor(topic)
    assert len(asked) == n


def test_a_beat_about_another_place_is_anchored_on_that_place(monkeypatch):
    _categories(monkeypatch, {"Pattadakal": ["Category:Pattadakal"]}, {"Category:Pattadakal": 300})
    topic = {"id": "kailasa", "title": "The Kailasa Temple", "location": {"name": "Ellora"}}
    assert commons.topic_anchor(topic, "Pattadakal") == {"kind": "category", "name": "Pattadakal", "deep": False}


def test_the_full_name_beats_a_namesake_with_a_qualifier(monkeypatch):
    """Review 2026-10-29: "Archimedes (genus)" (fossil bryozoans) beat "Claw of Archimedes" (6 files)."""
    _categories(monkeypatch, {"Archimedes": ["Category:Archimedes (genus)", "Category:Archimedes"],
                              "Claw": [], "The Claw of Archimedes": ["Category:Claw of Archimedes", "Category:Archimedes"]},
                {"Category:Archimedes (genus)": 40, "Category:Archimedes": (300, 20), "Category:Claw of Archimedes": 6})
    topic = {"id": "claw_of_archimedes", "title": "The Claw of Archimedes", "location": {"name": "Syracuse"}}
    assert commons.topic_anchor(topic) == {"kind": "category", "name": "Claw of Archimedes", "deep": True}


def test_the_category_the_photos_are_filed_in_beats_a_shorter_one(monkeypatch):
    """Review 2026-10-29: with 16 records filed in "Colossi of Memnon", the anchor was "Memnon" (Greek vases)."""
    _categories(monkeypatch, {"Memnon": ["Category:Memnon"], "Singing Colossi": [],
                              "Colossi of Memnon": ["Category:Colossi of Memnon", "Category:Memnon"]},
                {"Category:Memnon": (10, 3), "Category:Colossi of Memnon": (319, 4)})
    topic = {"id": "singing_colossi_of_memnon", "title": "The Singing Colossi of Memnon",
             "location": {"name": "Colossi of Memnon"}}
    anchor = commons.topic_anchor(topic, photos=_filed("Colossi of Memnon", 16))
    assert anchor["name"] == "Colossi of Memnon"


def test_namesakes_typefaces_and_tv_series_lose_to_where_the_photos_are_filed(monkeypatch):
    """"Zhang Heng (actress)", the typeface "Concrete Roman" and a TV series "Mystery!" are not the topic: a
    qualifier it does not name, the name's words out of order, and no photo filed there."""
    _categories(monkeypatch, {"Zhang Heng seismoscope": ["Category:Houfeng Didong Yi"],
                              "Zhang Heng Earthquake Detector": ["Category:Zhang Heng (actress)"]},
                {"Category:Houfeng Didong Yi": 12, "Category:Zhang Heng (actress)": 1})
    topic = {"id": "zhang_heng_seismoscope", "title": "Zhang Heng's Earthquake Detector (AD 132)", "location": {}}
    anchor = commons.topic_anchor(topic, photos=_filed("Houfeng Didong Yi", 1), aliases=["Zhang Heng seismoscope"])
    assert anchor == {"kind": "category", "name": "Houfeng Didong Yi", "deep": False}   # known by its photos: flat
    _categories(monkeypatch, {"Roman Self-Healing Concrete": ["Category:Concrete Roman"], "Mystery": ["Category:Mystery!"],
                              "opus caementicium": ["Category:Opus caementicium"]},
                {"Category:Concrete Roman": 4, "Category:Mystery!": (0, 8), "Category:Opus caementicium": 45})
    topic = {"id": "roman_self_healing_concrete", "title": "The Mystery of Roman Self-Healing Concrete", "location": {}}
    anchor = commons.topic_anchor(topic, photos=_filed("Opus caementicium", 2), aliases=["Roman concrete", "opus caementicium"])
    assert anchor["name"] == "Opus caementicium"


def test_a_category_named_by_the_title_and_the_place_together_counts(monkeypatch):
    _categories(monkeypatch, {"Lycurgus Cup": ["Category:Lycurgus cup, British Museum", "Category:Room 2A, British Museum",
                                               "Category:Lycurgus Cup, British Museum (1958,1202.1)"],
                              "British Museum": ["Category:British Museum"]},
                {"Category:Lycurgus cup, British Museum": 42, "Category:Lycurgus Cup, British Museum (1958,1202.1)": 3,
                 "Category:Room 2A, British Museum": 50, "Category:British Museum": (92, 19)})
    topic = {"id": "lycurgus_cup", "title": "The 4th-Century Lycurgus Cup", "location": {"name": "British Museum"}}
    assert commons.topic_anchor(topic) == {"kind": "category", "name": "Lycurgus cup, British Museum", "deep": True}


def test_a_whole_city_is_no_anchor_its_coordinates_are_used_or_nothing(monkeypatch):
    _categories(monkeypatch, {"Strasbourg": ["Category:Strasbourg"]}, {"Category:Strasbourg": (221, 35)})
    topic = {"id": "x", "title": "The Quiet Year", "location": {"name": "Strasbourg"}}
    assert commons.topic_anchor(topic) is None
    commons._ANCHORS.clear()
    near = commons.topic_anchor(dict(topic, location={"name": "Strasbourg", "latitude": 48.58, "longitude": 7.75}))
    assert near == {"kind": "near", "lat": 48.58, "lon": 7.75}
    # A site's category (few subcategories) is a place anchor, searched flat
    commons._ANCHORS.clear()
    _categories(monkeypatch, {"Pantheon": ["Category:Pantheon (Rome)"]}, {"Category:Pantheon (Rome)": (43, 9)})
    assert commons.topic_anchor(dict(topic, location={"name": "Pantheon"}))["deep"] is False


def test_a_deep_category_search_cut_short_is_not_used(monkeypatch):
    """CirrusSearch: "Deep category query returned too many categories. Only a subset of categories has been
    applied." Its results are not the category's: not used, and no wider search after it."""
    asked = []

    def search(q, limit=50, max_wait=60):
        asked.append(q)
        commons._last_search.partial = "deepcategory" in q
        return _pages(q, 0 if q == "Nazca cat geoglyph" else 30)
    monkeypatch.setattr(commons, "search", search)
    pages, tried = commons.search_widened("Nazca cat geoglyph", NAZCA, ["cat"])
    assert pages == [] and len(asked) == 2 and tried[1][1] == -2


def test_search_notes_cirrussearchs_partial_category_warning(monkeypatch):
    class Reply:
        def json(self):
            return {"warnings": {"search": {"*": "Deep category query returned too many categories. Only a subset of "
                                                 "categories has been applied."}}, "query": {"pages": {}}}
    monkeypatch.setattr(commons, "_get", lambda *a, **k: Reply())
    assert commons.search('dome deepcategory:"X"') == [] and commons._last_search.partial is True
    monkeypatch.setattr(commons, "_get", lambda *a, **k: type("R", (), {"json": lambda self: {"query": {"pages": {}}}})())
    commons.search("dome")
    assert commons._last_search.partial is False


# ------------------------------------------------------------------ the wider forms
def test_wider_forms_are_restricted_to_the_anchor_and_end_on_it_alone():
    assert commons.widen_queries("Nazca lines ground level trench", NAZCA, ["trench", "ground"]) == [
        'Nazca deepcategory:"Nazca lines"', 'trench deepcategory:"Nazca lines"', 'deepcategory:"Nazca lines"']
    assert commons.widen_queries("whispering gallery dome interior", NAZCA, ["dome"], max_extra=2) == [
        'dome deepcategory:"Nazca lines"', 'deepcategory:"Nazca lines"']
    near = {"kind": "near", "lat": 1.5, "lon": 2.25}
    assert commons.widen_queries("salt chapel", near, ["chapel"])[0] == f"chapel nearcoord:{commons.NEAR_KM}km,1.5000,2.2500"


def test_no_anchor_no_widening_and_no_form_that_is_the_query_again(monkeypatch):
    assert commons.widen_queries("brick kilns rampart", None, ["brick"]) == []
    asked = _stub(monkeypatch, {})
    assert commons.search_widened("brick kilns rampart", None, ["brick"])[0] == [] and asked == ["brick kilns rampart"]
    # "the geoglyph" is "geoglyph" again once filler words are aside: never asked as a wider form of itself
    assert commons.widen_queries("the geoglyph", NAZCA, ["geoglyph"]) == ['deepcategory:"Nazca lines"']


def test_search_operators_and_quotes_in_a_query_are_words_no_more():
    wider = commons.widen_queries('"Stake" NOT -tourist OR (lines)', NAZCA, [])
    assert wider[0] == 'Stake deepcategory:"Nazca lines"'
    assert all(" NOT " not in q and " OR " not in q and "(" not in q and " -" not in q for q in wider)


# ------------------------------------------------------------------ the search
def test_an_empty_specific_query_widens_and_finds_results(monkeypatch):
    asked = _stub(monkeypatch, {'trench deepcategory:"Nazca lines"': 23})
    pages, tried = commons.search_widened("Nazca lines ground level trench", NAZCA, ["trench", "ground"])
    assert len(pages) == 23 and asked[-1] == 'trench deepcategory:"Nazca lines"'
    assert tried == [("Nazca lines ground level trench", 0), ('Nazca deepcategory:"Nazca lines"', 0),
                     ('trench deepcategory:"Nazca lines"', 23)]


def test_results_merge_once_each_and_widening_stops_when_enough(monkeypatch):
    shared = _pages("Nazca cat geoglyph", 1)
    _stub(monkeypatch, {"Nazca cat geoglyph": shared, 'Nazca deepcategory:"Nazca lines"': shared + _pages("cat", 2),
                        'cat deepcategory:"Nazca lines"': _pages("g", 30)})
    pages, tried = commons.search_widened("Nazca cat geoglyph", NAZCA, ["cat", "geoglyph"])
    assert [q for q, _ in tried] == ["Nazca cat geoglyph", 'Nazca deepcategory:"Nazca lines"', 'cat deepcategory:"Nazca lines"']
    assert len(pages) == 1 + 2 + 30 and pages[0] == shared[0]        # the precise results first, none twice


def test_an_already_full_query_does_not_widen(monkeypatch):
    asked = _stub(monkeypatch, {"Nazca geoglyph": 30})
    pages, tried = commons.search_widened("Nazca geoglyph", NAZCA, ["geoglyph"])
    assert asked == ["Nazca geoglyph"] and len(pages) == 30 and tried == [("Nazca geoglyph", 30)]


def test_the_extra_search_budget_is_kept(monkeypatch):
    asked = _stub(monkeypatch, {})
    commons.search_widened("Nazca wooden stake Maria Reiche", NAZCA, ["reiche", "stake"])
    assert len(asked) == 1 + commons.WIDEN_MAX_EXTRA == 4 and asked[-1] == 'deepcategory:"Nazca lines"'
    asked.clear()
    commons.search_widened("Nazca wooden stake Maria Reiche", NAZCA, ["reiche"], max_extra=1)
    assert asked == ["Nazca wooden stake Maria Reiche", 'deepcategory:"Nazca lines"']


def test_no_wider_search_after_one_fails(monkeypatch):
    asked = []

    def search(q, limit=50, max_wait=60):
        asked.append(q)
        if q != "Nazca cat geoglyph":
            raise requests.HTTPError("429 Too Many Requests")
        return _pages(q, 1)
    monkeypatch.setattr(commons, "search", search)
    pages, tried = commons.search_widened("Nazca cat geoglyph", NAZCA, ["cat", "geoglyph"])
    assert len(pages) == 1 and len(asked) == 2 and tried[1][1] == -1


def test_the_beat_search_widens_within_the_topics_category_and_keeps_every_filter(monkeypatch, tmp_path, caplog):
    """The beat search asks the topic's anchor (read with its pool's records) and the widened search; the merged
    candidates go through the same candidates() filter, look and download as before; the run log names the
    queries tried."""
    import logging
    monkeypatch.setattr("src.free_llm.ready", lambda: True)
    monkeypatch.setattr(beat_images, "plan_queries", lambda b, t: BeatQueries(items=[
        BeatQuery(beat=4, query="Nazca lines ground level trench", keywords=["trench", "ground"])]))
    seen = []
    monkeypatch.setattr(commons, "topic_anchor", lambda topic, place="", photos=None, aliases=None: seen.append(place) or NAZCA)
    asked = _stub(monkeypatch, {'trench deepcategory:"Nazca lines"': 23})
    filtered = []
    monkeypatch.setattr(commons, "candidates", lambda pages, kw, sa: filtered.append((len(pages), kw)) or iter([]))
    monkeypatch.setattr(config, "BEAT_IMAGES_ALLOW_SHARE_ALIKE", True)
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    topic = {"id": "nazca", "title": "The Nazca Lines", "location": {"name": "Nazca Desert"}}
    beats = [{"text": f"Line {i}.", "start": i * 3.0, "end": i * 3.0 + 3} for i in range(5)]
    with caplog.at_level(logging.DEBUG, logger="src.beat_images"):
        assert beat_images.source(beats, topic, tmp_path / "run") == ([], [])
    assert seen == [""] and asked[-1] == 'trench deepcategory:"Nazca lines"' and filtered == [(23, ["trench", "ground"])]
    line = next(r.getMessage() for r in caplog.records if "Commons results" in r.getMessage())
    assert "'trench deepcategory:\"Nazca lines\"': 23" in line and "23 Commons results" in line
