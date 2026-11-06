"""QF-40: tier-gated corroboration and signed evidence provenance.

One Wikipedia edit plus one self-hosted page it cites made a false number "strong": any two hosts counted as two
sites, every extlinks page was fetched as evidence, and the HMAC signed the result. The network is mocked here
(requests.get answers from the fixture; nothing leaves the machine) and the checkers are mocked; records are signed
under the test key tests/conftest.py sets."""
import copy
import json
from types import SimpleNamespace
from typing import ClassVar

import pytest
import requests

from src import card_fields as cf
from src import fact_sheet, free_evidence, url_guard
from src.models import DraftFact

FIGURE = "4,700 tonnes"
EXTRACT = (f"The vault under the old mint holds {FIGURE} of gold according to the article. "
           "The vault under the old mint was dug in the year 1871 by local miners.")
PAGE = f"<html><body><p>Records show that the vault under the old mint holds {FIGURE} of gold today.</p></body></html>"


class _Page:
    status_code = 200
    headers: ClassVar[dict] = {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, n):
        yield PAGE.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _network(monkeypatch, extlinks):
    seen = []

    def get(url, params=None, headers=None, timeout=None, stream=False, allow_redirects=True):
        seen.append((url, dict(params or {}), stream))
        if url == free_evidence.WIKI_API and (params or {}).get("list") == "search":
            return SimpleNamespace(json=lambda: {"query": {"search": [{"title": "Old Mint Vault"}]}})
        if url == free_evidence.WIKI_API:
            page = {"title": "Old Mint Vault", "extract": EXTRACT, "extlinks": [{"*": u} for u in extlinks]}
            return SimpleNamespace(json=lambda: {"query": {"pages": {"1": page}}})
        if url.startswith("https://api.openalex.org/"):
            return SimpleNamespace(status_code=200, json=lambda: {"results": []})
        assert url in extlinks, url
        return _Page()
    monkeypatch.setattr(requests, "get", get)
    # QF-44: page_text asks HEAD first and resolves the cited host; both answer from the fixture.
    monkeypatch.setattr(requests, "head", lambda url, **k: SimpleNamespace(status_code=405, headers={}, is_redirect=False))
    monkeypatch.setattr(url_guard, "_addresses", lambda host: ["93.184.216.34"])
    return seen


def _fact_from_wikipedia_and(monkeypatch, link):
    seen = _network(monkeypatch, [link])
    quotes, _ = free_evidence.quotes_for({"id": "vault", "title": "Old Mint Vault", "location": {}}, ["vault", "mint"])
    ids = [i for i, q in enumerate(quotes, 1) if "4,700" in q["quote"]]
    assert len(ids) == 2       # the article's sentence and the cited page's sentence, one figure
    draft = DraftFact(id="F1", statement=f"The vault holds {FIGURE} of gold.", kind="number", quote_ids=ids,
                      disputed=False, hedge="")
    kept, dropped = fact_sheet._apply_rules([draft], quotes, auto_hedge=True)
    assert not dropped
    return kept[0], seen


@pytest.mark.parametrize("link", ["https://attacker-blog.example/vault", "https://records.example.gov/vault"])
def test_wikipedia_and_a_page_it_cites_are_one_source(monkeypatch, link):
    """The acceptance: Wikipedia + a page its extlinks name, carrying one figure, is disputed and hedged; a cited
    page inherits tier 3 even on an allowlisted host."""
    f, seen = _fact_from_wikipedia_and(monkeypatch, link)
    assert f["disputed"] is True and f["hedge"].startswith("by one estimate")
    assert [(e["host"], e["tier"], e["fetch"]) for e in f["evidence"]] == [
        ("en.wikipedia.org", 2, "wikipedia"), (link.split("/")[2], 3, "wikipedia_cited")]
    # the requests the mocked network saw: the article search and extract with its extlinks, then the cited page
    wiki = [p for u, p, _ in seen if u == free_evidence.WIKI_API]
    assert wiki[0]["srsearch"] == "Old Mint Vault" and "extlinks" in wiki[1]["prop"]
    assert (link, {}, True) in seen


def test_two_allowlisted_sites_still_corroborate():
    quotes = [{"url": "https://en.wikipedia.org/wiki/V", "quote": f"It holds {FIGURE}.", "fetch": "wikipedia"},
              {"url": "https://www.bbc.co.uk/v", "quote": f"It holds {FIGURE}.", "fetch": "web_search"},
              {"url": "https://de.wikipedia.org/wiki/V", "quote": f"It holds {FIGURE}.", "fetch": "wikipedia"},
              {"url": "https://travel.example/v", "quote": f"It holds {FIGURE}.", "fetch": "web_search"}]
    rule = fact_sheet._apply_rules
    fact = lambda ids: DraftFact(id="F", statement=f"It holds {FIGURE}.", kind="number", quote_ids=ids,
                                 disputed=False, hedge="")
    assert rule([fact([1, 2])], quotes, auto_hedge=True)[0][0]["disputed"] is False     # Wikipedia + BBC
    assert rule([fact([1, 3])], quotes, auto_hedge=True)[0][0]["disputed"] is True      # two Wikipedias: one site
    assert rule([fact([1, 4])], quotes, auto_hedge=True)[0][0]["disputed"] is True      # a tier-3 host: no


def _card(evidence, kind, field, fact_kind="number", statement=f"The vault holds {FIGURE} of gold."):
    sheet = {"topic": "vault", "facts": [{"id": "F1", "statement": statement,
                                          "kind": fact_kind, "disputed": False, "hedge": "", "evidence": evidence}]}
    text = cf.texts(kind, field)["text"]

    def trace(payload, s):
        return True, 0.0, [{"claim": text, "where": "graphic", "fact_ids": ["F1"], "claim_type": "domain_fact",
                            "verdict": "traced", "note": ""}], "mock"

    def evid(payload, s):
        return "pass", 0.0, [{"claim": text, "where": "graphic", "claim_type": "domain_fact", "verdict": "supported",
                              "quote_ids": sorted(str(n) for n in cf.evidence_numbers(s, "F1")), "note": ""}], "mock"
    field = dict(field)
    field["check"] = cf.check_field(kind, field, sheet, trace=trace, evidence=evid,
                                    models={"trace_check": "mock-trace", "evidence_check": "mock-evidence"})
    sheet["cards"] = {"statements": [], "myths": [], "quotes": [], "dropped": [], kind: [field]}
    return sheet


WIKI = {"url": "https://en.wikipedia.org/wiki/Old_Mint_Vault", "tier": 2, "fetch": "wikipedia", "host": "en.wikipedia.org",
        "quote": f"The vault under the old mint holds {FIGURE} of gold according to the article."}
CITED = {"url": "https://attacker-blog.example/vault", "tier": 3, "fetch": "wikipedia_cited",
         "host": "attacker-blog.example", "quote": f"Records show that the vault holds {FIGURE} of gold today."}
TIER1 = {"url": "https://www.britannica.com/vault", "tier": 1, "fetch": "web_search", "host": "britannica.com",
         "quote": f"The vault holds {FIGURE} of gold."}
QUOTED = {"url": "https://en.wikipedia.org/wiki/Old_Mint_Vault", "tier": 2, "fetch": "wikipedia",
          "host": "en.wikipedia.org", "quote": 'The curator Ada Brown said: "The vault was never emptied."'}
STATEMENT = {"id": "S", "text": f"The vault holds {FIGURE} of gold", "fact_id": "F1"}
QUOTE = {"id": "Q", "quotation": "The vault was never emptied.", "speaker": {"name": "Ada Brown", "role": ""},
         "work": "", "date": None, "source_url": QUOTED["url"], "evidence": QUOTED["quote"], "fact_id": "F1"}


@pytest.mark.parametrize("kind,field,evidence", [("statements", STATEMENT, [WIKI, CITED]),
                                                 ("quotes", QUOTE, [QUOTED, CITED])])
def test_verify_field_refuses_a_number_or_quote_resting_on_wikipedia_alone(kind, field, evidence):
    sheet = _card(evidence, kind, field)
    with pytest.raises(cf.FieldRefused, match="besides Wikipedia") as e:
        cf.verify_field(sheet, kind, field["id"])
    assert e.value.code == "unsupported_field"


@pytest.mark.parametrize("kind,field,evidence", [("statements", STATEMENT, [WIKI, TIER1]),
                                                 ("quotes", QUOTE, [QUOTED, TIER1])])
def test_verify_field_accepts_them_with_a_second_allowlisted_site(kind, field, evidence):
    assert cf.verify_field(_card(evidence, kind, field), kind, field["id"])["id"] == field["id"]


def test_evidence_provenance_is_bound_by_the_signed_record():
    sheet = _card([WIKI, TIER1], "statements", STATEMENT)
    assert cf.verify_field(sheet, "statements", "S")
    for key, value in (("fetch", "wikipedia"), ("tier", 2), ("host", "elsewhere.example")):
        t = copy.deepcopy(sheet)
        t["facts"][0]["evidence"][1][key] = value
        with pytest.raises(cf.FieldRefused) as e:
            cf.verify_field(t, "statements", "S")
        assert e.value.code == "stale_check"


def test_review_card_says_where_the_evidence_came_from(monkeypatch, curated_payload, tmp_path):
    assert fact_sheet.sources_line({"facts": [{"evidence": [WIKI, CITED]}]}) == "sources: Wikipedia + 1 site it cites"
    assert fact_sheet.sources_line({"facts": [{"evidence": [WIKI, CITED, TIER1]}]}) == (
        "sources: Wikipedia + 1 site it cites + 1 other site")
    from src import script_engine, trace_check
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    fact_sheet.save({"topic_id": "t1", "built_at": "2099-01-01T00:00:00+00:00", "dropped": [], "sources": [],
                     "facts": [{"id": "F1", "statement": "x", "kind": "other", "disputed": False, "hedge": "",
                                "evidence": [WIKI, CITED]}]})
    monkeypatch.setattr(trace_check, "check", lambda payload, sheet: (True, 100.0, [], "ok"))
    monkeypatch.setattr(script_engine, "SECOND_CHECK_ENABLED", False)
    eng = script_engine.ScriptEngine()
    _, out, _ = eng.verify_claims(dict(curated_payload, topic_id="t1", origin="claude", fact_sheet=True))
    assert out["fact_sheet_note"].endswith("; sources: Wikipedia + 1 site it cites")


def test_a_cached_sheet_is_held_to_the_new_rule(monkeypatch, tmp_path):
    """A sheet built before the rule (two hosts, one of them tier 3) is used only with its hedge."""
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    old = [{"url": WIKI["url"], "tier": 2, "quote": WIKI["quote"]},
           {"url": CITED["url"], "tier": 3, "quote": CITED["quote"]}]
    fact_sheet.save({"topic_id": "t2", "built_at": "2099-01-01T00:00:00+00:00", "dropped": [], "sources": [],
                     "facts": [{"id": "F1", "statement": f"The vault holds {FIGURE} of gold.", "kind": "number",
                                "disputed": False, "hedge": "", "evidence": old}]})
    f = fact_sheet.load("t2")["facts"][0]
    assert f["disputed"] is True and f["hedge"].startswith("by one estimate")
    assert json.loads((tmp_path / "t2.json").read_text(encoding="utf-8"))["facts"][0]["disputed"] is False  # file kept


@pytest.mark.parametrize("text", ["Carved around 9600 BC", "On 30 June 1908 the sky lit up",
                                  "Vesuvius buried it in AD 79", "July 29, 1941, at around 7:14",
                                  "Built between 9,600 and 8,200 BCE", "Dug in the 8th to 7th century BC",
                                  "Made between 7,000 and 10,000 years ago", "Built in 1871"])
def test_a_date_fact_on_a_reference_work_stands_as_a_statement(text):
    """A date may rest on a tier-2 reference work (fact_sheet.strongly_sourced): the whole date expression (a day of
    the month, a BC/AD year of any size, a time of day, a range) is no figure on a date fact (review round 2)."""
    wiki = dict(WIKI, quote=f"{text}, according to the article.")
    field = {"id": "S", "text": text, "fact_id": "F1"}
    sheet = _card([wiki], "statements", field, fact_kind="date", statement=f"{text}.")
    assert cf.verify_field(sheet, "statements", "S")["id"] == "S"


@pytest.mark.parametrize("text", ["In 1941 it was taken apart in 36 hours", "Dated to 6500 BCE, 40 of 120 skulls",
                                  "On 30 June 1908 it flattened 80 million trees"])
def test_a_quantity_on_a_date_fact_still_needs_a_second_site(text):
    wiki = dict(WIKI, quote=f"{text}, according to the article.")
    field = {"id": "S", "text": text, "fact_id": "F1"}
    sheet = _card([wiki], "statements", field, fact_kind="date", statement=f"{text}.")
    with pytest.raises(cf.FieldRefused, match="besides Wikipedia"):
        cf.verify_field(sheet, "statements", "S")
