"""Card fields on the fact sheet (operator decision, 2026-11-03; src/card_fields.py): the typed schema, the
fact-sheet step proposing them and the existing fact checker judging them (its model mocked), the record a card
reads, and that the renderer never calls a model."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src import card_fields as cf
from src import design_cards as dc
from src import fact_sheet, free_llm, llm, trace_check
from src.models import (
    CardMyth,
    CardQuote,
    CardStatement,
    DraftCardFields,
    DraftCardMyth,
    DraftCardQuote,
    DraftCardStatement,
    DraftFact,
    FactSheetCards,
    FactSheetDraft,
    Source,
    Speaker,
)

ROOT = Path(__file__).resolve().parent.parent
WIKI = "https://en.wikipedia.org/wiki/Richat_Structure"
NASA = "https://www.nasa.gov/richat"


@pytest.fixture(autouse=True)
def quote_cards_on(monkeypatch):
    """Quote cards are held by default (operator decision B: config.QUOTE_CARDS_ENABLED=false refuses "held", tested
    in tests/test_signed_records_2026_11_03.py); these tests exercise the quote guard behind the hold."""
    from src import config
    monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", True)
QUOTES = [
    {"url": NASA, "tier": 1, "quote": "The Richat Structure is in the Sahara of Mauritania."},
    {"url": NASA, "tier": 1, "quote": "Erosion of an uplifted dome formed the rings of the Richat Structure."},
    {"url": WIKI, "tier": 2, "quote": "It was once thought to be an impact crater."},
    {"url": NASA, "tier": 1, "quote": 'Theodore Monod wrote: "It is not a crater."'},
    {"url": WIKI, "tier": 2, "quote": "Astronauts use the structure as a landmark."},
    {"url": WIKI, "tier": 2, "quote": "The structure is also called the Eye of the Sahara."},
    {"url": WIKI, "tier": 2, "quote": "Geologist Theodore Monod studied the structure."},
]


def _draft():
    facts = [
        DraftFact(id="F1", statement="The Richat Structure is in the Sahara of Mauritania", kind="name",
                  quote_ids=[1], disputed=False, hedge=""),
        DraftFact(id="F2", statement="Erosion of an uplifted dome formed its rings", kind="event", quote_ids=[2],
                  disputed=False, hedge=""),
        DraftFact(id="F3", statement="It is not an impact crater", kind="other", quote_ids=[3, 4], disputed=False,
                  hedge=""),
        DraftFact(id="F4", statement="Astronauts use it as a landmark", kind="other", quote_ids=[5], disputed=False,
                  hedge=""),
        DraftFact(id="F5", statement="It is also called the Eye of the Sahara", kind="name", quote_ids=[6],
                  disputed=False, hedge=""),
        DraftFact(id="F6", statement="Geologist Theodore Monod studied the structure", kind="name", quote_ids=[7, 4],
                  disputed=False, hedge=""),
        DraftFact(id="F7", statement="It has no evidence", kind="other", quote_ids=[99], disputed=False, hedge=""),
    ]
    cards = DraftCardFields(
        statements=[DraftCardStatement(id="S1", text="Erosion of an uplifted dome formed its rings", fact_id="F2"),
                    DraftCardStatement(id="S2", text="A statement on a fact the rules drop", fact_id="F7")],
        myths=[DraftCardMyth(id="M1", myth="A meteorite impact made the Richat Structure", verdict="false",
                             refuting_fact_id="F3", correction="It is not an impact crater",
                             quote_id=3)],
        quotes=[DraftCardQuote(id="Q1", quotation="It is not a crater.", speaker=Speaker(name="Theodore Monod"),
                               work="", date=None, fact_id="F6", quote_id=4),
                DraftCardQuote(id="Q2", quotation="It is not a crater.", speaker=Speaker(name="Theodore Monod"),
                               fact_id="F6", quote_id=5)])
    return FactSheetDraft(facts=facts, cards=cards)


# ------------------------------------------------------------------------------------------------ schema
def test_schema_round_trip():
    cards = FactSheetCards(
        statements=[CardStatement(id="S1", text="Erosion formed the rings", fact_id="F2")],
        myths=[CardMyth(id="M1", myth="A meteorite made it", verdict="misleading", refuting_fact_id="F3",
                        correction="Erosion formed the rings", source=Source(title="nasa", url=NASA))],
        quotes=[CardQuote(id="Q1", quotation="It is not a crater.", speaker=Speaker(name="Theodore Monod",
                                                                                   role="Geologist"),
                          work="", date="1940", source_url=NASA, evidence=QUOTES[3]["quote"], fact_id="F6")])
    blob = json.dumps(cards.model_dump())
    again = FactSheetCards(**json.loads(blob))
    assert again == cards and again.model_dump() == cards.model_dump()
    assert FactSheetCards().model_dump() == {"statements": [], "myths": [], "quotes": [], "dropped": []}
    with pytest.raises(ValidationError):
        CardStatement(id="S", text="x" * 85, fact_id="F")
    with pytest.raises(ValidationError):
        CardMyth(id="M", myth="m", verdict="true", refuting_fact_id="F", correction="c", source=Source(title="t", url="u"))
    with pytest.raises(ValidationError):
        CardMyth(id="M", myth="m", verdict="false", refuting_fact_id="F", correction="c" * 85,
                 source=Source(title="t", url="u"))
    # the writer's schema: cards optional, so a draft without them (and every older response) still parses
    assert FactSheetDraft(facts=[]).cards is None
    d = _draft()
    assert FactSheetDraft(**json.loads(d.model_dump_json())) == d
    assert "cards" in FactSheetDraft.model_json_schema()["properties"]


def test_shipped_sheets_still_load():
    """The optional section: every shipped sheet without cards loads as before; the one with them validates."""
    for p in sorted((ROOT / "data" / "fact_sheets").glob("*.json")):
        sheet = json.loads(p.read_text(encoding="utf-8"))
        FactSheetCards(**(sheet.get("cards") or {}))


# ------------------------------------------------------------------------------------------------ generation
def _mock_checkers(calls):
    """trace_check.check and evidence_check with the model mocked: a myth (it names a meteorite) is contradicted by
    the refuting fact, every other text traced to the fact whose words it uses."""
    def fid_of(text, sheet):
        if "meteorite" in text:
            return next(f["id"] for f in sheet["facts"] if "crater" in f["statement"])
        if text.startswith("“"):        # a quote card's text names its speaker: the fact about him
            return next(f["id"] for f in sheet["facts"] if "Monod" in f["statement"])
        best = max(sheet["facts"], key=lambda f: len(set(text.lower().split()) & set(f["statement"].lower().split())))
        return best["id"]

    def trace(payload, sheet):
        calls.append(("trace", payload))
        t = payload["card_copy"][0]
        fid = fid_of(t, sheet)
        v = "contradicts" if "meteorite" in t else "traced"
        return v == "traced", 100.0, [{"claim": t, "where": "graphic", "fact_ids": [fid], "claim_type": "domain_fact",
                                       "verdict": v, "note": ""}], "mock"

    def evidence(payload, sheet):
        calls.append(("evidence", payload))
        t = payload["card_copy"][0]
        fid = fid_of(t, sheet)
        v = "contradicted" if "meteorite" in t else "supported"
        return ("fail" if "meteorite" in t else "pass"), 100.0, [
            {"claim": t, "where": "graphic", "claim_type": "domain_fact", "verdict": v,
             "quote_ids": sorted(cf.evidence_numbers(sheet, fid)), "note": ""}], "mock"
    return trace, evidence


@pytest.fixture
def built(monkeypatch, tmp_path):
    calls, prompts = [], []
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    monkeypatch.setattr(free_llm, "enabled", lambda: True)
    from src import free_evidence
    monkeypatch.setattr(free_evidence, "quotes_for", lambda topic, kw: ([dict(q) for q in QUOTES],
                                                                       [{"title": "NASA", "url": NASA}]))
    monkeypatch.setattr(fact_sheet, "_unesco_evidence", lambda topic: ([], None))

    def writer(system, prompt, schema, **kw):
        prompts.append((system, prompt, schema))
        return _draft()
    monkeypatch.setattr(free_llm, "call", writer)
    trace, evidence = _mock_checkers(calls)
    monkeypatch.setattr(trace_check, "check", trace)
    monkeypatch.setattr(trace_check, "evidence_check", evidence)
    monkeypatch.setattr(trace_check, "checker_models", lambda: {"trace_check": "mock-a", "evidence_check": "mock-b"})
    sheet = fact_sheet.build({"id": "richat_test", "title": "The Richat Structure", "location": {"name": "Richat"},
                              "key_facts": []})
    return sheet, calls, prompts


def test_generation_proposes_fields_and_checks_each_text(built):
    sheet, calls, prompts = built
    system, _, schema = prompts[0]
    assert schema is FactSheetDraft and "CARD FIELDS" in system and "at most 84 characters" in system
    cards = sheet["cards"]
    # deterministic rules first: a field on a dropped fact, or citing a quote its fact does not cite, never reaches
    # the checker
    assert {d["id"] for d in cards["dropped"]} == {"S2", "Q2"}
    q = cards["quotes"][0]
    assert q["evidence"] == QUOTES[3]["quote"] and q["source_url"] == NASA       # filled from the quote, not the model
    assert cards["myths"][0]["source"]["url"] == WIKI
    # every text went through the existing checker, alone, as a "card_copy" payload: both checks per text
    texts = [p["card_copy"] for _, p in calls]
    expected = [cards["statements"][0]["text"], cards["myths"][0]["myth"], cards["myths"][0]["correction"],
                cf.texts("quotes", q)["text"]]
    assert all([t] in texts for t in expected) and len(calls) == 2 * len(expected)
    assert {k for k, _ in calls} == {"trace", "evidence"}
    for kind in cf.KINDS:
        for f in cards[kind]:
            chk = f["check"]
            assert chk["verdict"] == "supported", (kind, chk)
            assert {r["model"] for r in chk["runs"]} == {"mock-a", "mock-b"} and chk["sha256"] == cf.check_hash(chk)
            assert cf.verify_field(sheet, kind, f["id"])["id"] == f["id"]
    assert cards["myths"][0]["check"]["parts"] == {"myth": "contradicted", "correction": "supported"}
    saved = json.loads((fact_sheet.path_for("richat_test")).read_text(encoding="utf-8"))
    assert saved["cards"] == cards


def test_the_surface_is_the_checkers_own():
    """The card text reaches the checkers through trace_check.surfaces as on-screen graphic text; a payload
    without "card_copy" has exactly the surfaces (and public fingerprint) it had."""
    assert trace_check.surfaces({"card_copy": ["Erosion formed the rings"]}) == [("graphic", "Erosion formed the rings")]
    from src.fact_evidence import public_fingerprint
    p = {"title": "T", "narration_script": "N", "caption": "C", "first_comment": "F",
         "thumbnail": {"eyebrow": "", "line1": "A", "line2": "", "tagline": ""}, "story_slides": [],
         "carousel_slides": []}
    assert public_fingerprint(p) == public_fingerprint({**p, "card_copy": []})


def test_checker_unavailable_leaves_needs_check(monkeypatch):
    sheet = {"facts": [{"id": "F", "statement": "Erosion formed the rings", "kind": "event", "disputed": False,
                        "hedge": "", "evidence": [QUOTES[1]]}]}

    def down(payload, sh):
        raise llm.LLMError("no key")
    f = {"id": "S1", "text": "Erosion formed the rings", "fact_id": "F"}
    chk = cf.check_field("statements", f, sheet, trace=down, evidence=down, models={"trace_check": "a",
                                                                                   "evidence_check": "b"})
    assert chk["verdict"] == "needs_check" and not chk["runs"]
    sheet["cards"] = {"statements": [{**f, "check": chk}]}
    with pytest.raises(cf.FieldRefused) as e:
        cf.verify_field(sheet, "statements", "S1")
    assert e.value.code == "unsupported_field"
    sheet["cards"] = {"statements": [f]}
    with pytest.raises(cf.FieldRefused) as e:
        cf.verify_field(sheet, "statements", "S1")
    assert e.value.code == "unchecked"


def test_a_failed_checker_verdict_is_never_usable(monkeypatch):
    sheet = {"facts": [{"id": "F", "statement": "Erosion formed the rings", "kind": "event", "disputed": False,
                        "hedge": "", "evidence": [QUOTES[1]]}]}
    f = {"id": "S1", "text": "Aliens formed the rings", "fact_id": "F"}
    calls = []
    trace, evidence = _mock_checkers(calls)

    def untraced(payload, sh):
        _, _, a, s = trace(payload, sh)
        a[0]["verdict"] = "untraced"
        return False, 0.0, a, s
    chk = cf.check_field("statements", f, sheet, trace=untraced, evidence=evidence,
                         models={"trace_check": "a", "evidence_check": "b"})
    assert chk["verdict"] == "unsupported"
    # a myth the checkers found TRUE is "contradicted" as a field, never usable
    m = {"id": "M1", "myth": "Erosion formed the rings", "verdict": "false", "refuting_fact_id": "F",
         "correction": "Erosion formed the rings!", "source": {"title": "nasa", "url": NASA}}
    chk = cf.check_field("myths", m, sheet, trace=trace, evidence=evidence, models={"trace_check": "a",
                                                                                   "evidence_check": "b"})
    assert chk["verdict"] == "contradicted" and chk["parts"]["myth"] == "supported"


# ------------------------------------------------------------------------------------------------ the renderer
def test_renderer_never_calls_a_model(monkeypatch, tmp_path, built):
    """Cards read the stored record only: with every model path made to explode, the checked fields still plan."""
    sheet, _, _ = built

    def boom(*a, **k):
        raise AssertionError("the renderer called a model")
    for mod, name in ((free_llm, "call"), (free_llm, "structured"), (llm, "structured_call"), (llm, "research"),
                      (trace_check, "check"), (trace_check, "evidence_check")):
        monkeypatch.setattr(mod, name, boom)
    sheet = json.loads(json.dumps(sheet))
    m = sheet["cards"]["myths"][0]["id"]
    q = sheet["cards"]["quotes"][0]["id"]
    assert dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", m)), sheet).kind == "mythfact"
    assert dc.plan_quote_card(dc.QuoteCard(quote_src=dc.CardRef("quotes", q)), sheet, 1920, 1080).kind == "quote"
    s = sheet["cards"]["statements"][0]
    with pytest.raises(dc.CardRefused) as e:      # a refusal, too, is reached without any model
        dc.plan_chapter_card(dc.ChapterCard(1, 3, "Erosion of an uplifted dome", fact_ids=(s["fact_id"],),
                                            title_src=dc.CardRef("statements", s["id"])), sheet)
    assert e.value.code == "not_verbatim"


def test_cards_refuse_without_checked_fields(built):
    sheet, _, _ = built
    bare = {k: v for k, v in sheet.items() if k != "cards"}
    s = sheet["cards"]["statements"][0]
    for planner, spec in ((dc.plan_myth_fact, dc.MythFact(myth_src=dc.CardRef("myths", "M1"))),
                          (dc.plan_quote_card, dc.QuoteCard(quote_src=dc.CardRef("quotes", "Q1"))),
                          (dc.plan_chapter_card, dc.ChapterCard(1, 3, s["text"][:32], title_src=dc.CardRef(
                              "statements", s["id"])))):
        with pytest.raises(dc.CardRefused) as e:
            planner(spec, bare)
        assert e.value.code == "no_field", e.value.reason


# ------------------------------------------------------------------------------------------------ the worked example
def test_backfilled_example_sheet():
    """data/fact_sheets/hal_saflieni_hypogeum.json carries card fields made only from its own statements (its one
    statement of at most 84 characters; no evidence passage of it quotes a speaker, and it labels no myth), marked as
    needing a check (no model key in the development checkout), so no card can use them yet."""
    sheet = json.loads((ROOT / "data" / "fact_sheets" / "hal_saflieni_hypogeum.json").read_text(encoding="utf-8"))
    cards = FactSheetCards(**sheet["cards"])
    assert cards.statements and not cards.myths and not cards.quotes
    statements = {f["id"]: f["statement"] for f in sheet["facts"]}
    for s in cards.statements:
        assert s.text == statements[s.fact_id]
        assert s.check.verdict == "needs_check" and s.check.sha256 == cf.check_hash(s.check.model_dump())
        assert s.check.facts_sha256 == cf.facts_hash(sheet)
        with pytest.raises(cf.FieldRefused) as e:
            cf.verify_field(sheet, "statements", s.id)
        assert e.value.code == "unsupported_field"
    assert fact_sheet.load("hal_saflieni_hypogeum", max_age_days=10 ** 6)["cards"] == sheet["cards"]
