import json

import pytest

from src import topic_proposer as tp
from src.models import ProposedLocation, Source, TopicProposal, TopicProposals


def _proposal(slug, url="https://real.org/a", title=None):
    return TopicProposal(
        slug=slug, title=title or slug.replace("_", " ").title(), sub_theme="Ancient Engineering",
        hook="A true hook.", location=ProposedLocation(name="Site", city="C", region="R", country="X"),
        key_facts=["f1", "f2", "f3", "f4", "f5"], hashtags=["#a"] * 7, image_queries=["site"],
        image_keywords=["Site"], why_it_works="Strong visuals.", sources=[Source(title="s", url=url)])


@pytest.fixture
def wired(db, monkeypatch, tmp_path):
    queries = tmp_path / "image_queries.json"
    queries.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tp, "BANK", db.topic_bank_file)
    monkeypatch.setattr(tp, "QUERIES", queries)
    monkeypatch.setattr(tp, "claude_available", lambda: True)
    monkeypatch.setattr(tp, "research", lambda *a, **k: ("notes", [Source(title="s", url="https://real.org/a")]))
    return db, queries


def test_propose_keeps_only_verified_new_ideas(wired, monkeypatch):
    db, _ = wired
    monkeypatch.setattr(tp, "structured_call", lambda *a, **k: TopicProposals(topics=[
        _proposal("baghdad_battery"),
        _proposal("made_up", url="https://hallucinated.example"),  # unverified source
        _proposal("topic", title="Duplicate slug of existing 01_topic"),  # collides with bank id suffix
    ]))
    fresh = tp.propose(db)
    assert [p.slug for p in fresh] == ["baghdad_battery"]
    assert set(tp.pending(db)) == {"baghdad_battery"}


def test_add_appends_to_bank_queries_and_db(wired, monkeypatch):
    db, queries = wired
    monkeypatch.setattr(tp, "structured_call", lambda *a, **k: TopicProposals(topics=[_proposal("roman_dodecahedron")]))
    tp.propose(db)
    topic_id = tp.add(db, "roman_dodecahedron")
    assert topic_id == "04_roman_dodecahedron"  # fixture bank has topics 01-03
    assert db.get_topic_by_id(topic_id)["status"] == "queued"
    assert json.loads(queries.read_text(encoding="utf-8"))[topic_id]["keywords"] == ["site"]
    assert tp.pending(db) == {}
    with pytest.raises(ValueError):
        tp.add(db, "roman_dodecahedron")


def test_skip_removes_pending(wired, monkeypatch):
    db, _ = wired
    monkeypatch.setattr(tp, "structured_call", lambda *a, **k: TopicProposals(topics=[_proposal("x_topic")]))
    tp.propose(db)
    tp.skip(db, "x_topic")
    assert tp.pending(db) == {}


def test_proposal_text_escapes():
    text = tp.proposal_text(_proposal("a", title="<b>bad</b>"))
    assert "<b>bad</b>" not in text and "Topic proposal" in text


@pytest.mark.parametrize("title, dup", [
    ("The Hidden City of Derinkuyu", True),
    ("Derinkuyu: Turkey's 18-Level Underground City", True),
    ("The Antikythera Mechanism Explained", True),
    ("The Tunnel of Eupalinos", False),
    ("Persian Yakhchal Ice Houses", False),
    ("The Roman Dodecahedron", False),           # shares only "roman" with Roman concrete
    ("Roman Self-Healing Concrete Explained", True),
])
def test_near_duplicate_titles(title, dup):
    existing = ["The Underground City of Derinkuyu", "The Antikythera Mechanism: 2,000-Year-Old Analog Computer",
                "Damascus Steel & Ancient Carbon Nanotubes", "The Mystery of Roman Self-Healing Concrete"]
    assert (tp.near_duplicate(title, existing) is not None) is dup


def test_propose_rejects_near_duplicate_of_bank(wired, monkeypatch):
    db, _ = wired
    monkeypatch.setattr(tp, "_bank", lambda: {"topics": [{"id": "01_derinkuyu", "title": "The Underground City of Derinkuyu"}]})
    monkeypatch.setattr(tp, "structured_call", lambda *a, **k: TopicProposals(topics=[
        _proposal("hidden_derinkuyu", title="The Hidden City of Derinkuyu"), _proposal("eupalinos", title="Tunnel of Eupalinos")]))
    assert [p.slug for p in tp.propose(db)] == ["eupalinos"]
