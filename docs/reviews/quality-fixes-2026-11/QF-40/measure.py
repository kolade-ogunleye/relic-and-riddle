"""QF-40 acceptance metric: does one Wikipedia edit plus one page it cites make a number "strong"?

Usage: python measure.py <repo root>. Prints the metric as JSON and writes measurements.json next to this file.

The real code path runs with the network mocked (requests.get answers from the fixture below; nothing leaves the
machine): free_evidence.quotes_for fetches the Wikipedia article and the page its extlinks name, fact_sheet._apply_rules
applies the evidence rules, and card_fields.check_field / verify_field sign and verify card fields with mocked
checkers under a throwaway test key (the same kind tests/conftest.py uses; no real key is read).
"""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

FIGURE = "4,700 tonnes"
WIKI_EXTRACT = (f"The vault under the old mint holds {FIGURE} of gold according to the article. "
                "The vault under the old mint was dug in the year 1871 by local miners.")
ATTACKER = "https://attacker-blog.example/vault"          # self-hosted page the edited article now cites
CITED_GOV = "https://records.example.gov/vault"            # an allowlisted host, but reached through the article's links
PAGE = f"<html><body><p>Records show that the vault under the old mint holds {FIGURE} of gold today.</p></body></html>"
TEST_KEY = "measure-only-fact-record-key-0123456789abcdef"


def fake_get(seen, extlinks):
    def get(url, params=None, headers=None, timeout=None, stream=False):
        seen.append({"url": url, "params": dict(params or {})})
        if "wikipedia.org/w/api.php" in url and (params or {}).get("list") == "search":
            return SimpleNamespace(json=lambda: {"query": {"search": [{"title": "Old Mint Vault"}]}})
        if "wikipedia.org/w/api.php" in url:
            page = {"title": "Old Mint Vault", "extract": WIKI_EXTRACT, "extlinks": [{"*": u} for u in extlinks]}
            return SimpleNamespace(json=lambda: {"query": {"pages": {"1": page}}})
        if "api.openalex.org" in url:
            return SimpleNamespace(status_code=200, json=lambda: {"results": []})
        body = PAGE.encode("utf-8")

        class Resp:
            status_code = 200
            headers: ClassVar[dict] = {"Content-Type": "text/html; charset=utf-8"}

            def iter_content(self, n):
                yield body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return Resp()
    return get


def scenario(extlink):
    """Wikipedia + one page it cites, both quoting the figure: (the kept fact, the evidence quotes, request log)."""
    import requests

    from src import fact_sheet, free_evidence
    from src.models import DraftFact
    seen = []
    old = requests.get
    requests.get = fake_get(seen, [extlink])
    try:
        quotes, _ = free_evidence.quotes_for({"id": "vault", "title": "Old Mint Vault", "location": {}},
                                              ["vault", "mint"])
    finally:
        requests.get = old
    ids = [i for i, q in enumerate(quotes, 1) if "4,700" in q["quote"]]
    draft = DraftFact(id="F1", statement=f"The vault holds {FIGURE} of gold.", kind="number", quote_ids=ids,
                      disputed=False, hedge="")
    kept, dropped = fact_sheet._apply_rules([draft], quotes, auto_hedge=True)
    return kept, quotes, seen, dropped


def card_verdict(evidence, kind, field, fact_kind="number", statement=f"The vault holds {FIGURE} of gold."):
    """verify_field on a card field of an undisputed fact with `evidence`, signed through check_field."""
    from src import card_fields as cf
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
    try:
        cf.verify_field(sheet, kind, field["id"])
        return "accepted", sheet
    except cf.FieldRefused as e:
        return f"refused ({e.code}): {e.reason}", sheet


DATE_TEXTS = ["On 30 June 1908 the sky lit up", "Carved around 9600 BC", "Vesuvius buried it in AD 79",
              "Begun about 3000 BC", "Built in 1871", "July 29, 1941, at around 7:14",
              "Built between 9,600 and 8,200 BCE", "Dug in the 8th to 7th century BC",
              "Made between 7,000 and 10,000 years ago"]
QUANTITY_ON_DATE_TEXTS = ["In 1941 it was taken apart in 36 hours", "Dated to 6500 BCE, 40 of 120 skulls"]


def shipped_date_refusals(root: Path) -> dict:
    """The undisputed date facts of the shipped sheets (data/fact_sheets) whose own statement, as a card statement
    resting on Wikipedia alone, the source rule would refuse: {sheet/fact id: statement}."""
    from src import card_fields as cf
    find = getattr(cf, "_source_findings", None)
    out = {}
    wiki_only = [{"url": "https://en.wikipedia.org/wiki/X", "tier": 2, "fetch": "wikipedia", "quote": "x"}]
    for p in sorted((root / "data" / "fact_sheets").glob("*.json")):
        for f in json.loads(p.read_text(encoding="utf-8")).get("facts") or []:
            if f.get("kind") == "date" and not f.get("disputed") and find and find(
                    dict(f, evidence=wiki_only), [f.get("statement") or ""]):
                out[f"{p.stem}/{f.get('id')}"] = f.get("statement")
    return out


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from src import card_fields as cf
    from src import config, fact_sheet
    config.FACT_RECORD_KEY = TEST_KEY

    out = {}
    for name, link in (("wikipedia_plus_attacker_page", ATTACKER), ("wikipedia_plus_cited_gov_page", CITED_GOV)):
        kept, _, seen, dropped = scenario(link)
        f = kept[0] if kept else {}
        out[name] = {
            "disputed": f.get("disputed"), "hedge": f.get("hedge"), "dropped": [list(d) for d in dropped],
            "evidence_provenance": [{k: e.get(k) for k in ("host", "tier", "fetch")} for e in f.get("evidence", [])],
            "requests": [s["url"] for s in seen],
        }
    wiki = {"url": "https://en.wikipedia.org/wiki/Old_Mint_Vault", "tier": 2, "fetch": "wikipedia",
            "quote": f"The vault under the old mint holds {FIGURE} of gold according to the article."}
    attacker = {"url": ATTACKER, "tier": 3, "fetch": "wikipedia_cited",
                "quote": f"Records show that the vault under the old mint holds {FIGURE} of gold today."}
    tier1 = {"url": "https://www.britannica.com/vault", "tier": 1, "fetch": "web_search",
             "quote": f"The vault holds {FIGURE} of gold."}
    quoted = {"url": "https://en.wikipedia.org/wiki/Old_Mint_Vault", "tier": 2, "fetch": "wikipedia",
              "quote": 'The curator Ada Brown said: "The vault was never emptied."'}
    stmt = {"id": "S", "text": f"The vault holds {FIGURE} of gold", "fact_id": "F1"}
    quote = {"id": "Q", "quotation": "The vault was never emptied.", "speaker": {"name": "Ada Brown", "role": ""},
             "work": "", "date": None, "source_url": quoted["url"], "evidence": quoted["quote"], "fact_id": "F1"}
    out["verify_field"] = {
        "number_statement_wikipedia_plus_cited_page": card_verdict([wiki, attacker], "statements", stmt)[0],
        "quote_wikipedia_only": card_verdict([quoted, attacker], "quotes", quote)[0],
        "control_number_statement_with_tier1": card_verdict([wiki, tier1], "statements", stmt)[0],
        "control_quote_with_tier1": card_verdict([quoted, tier1], "quotes", quote)[0],
    }
    # the provenance is signed: edit a quote's fetch path on disk after the check and the card refuses it
    _, sheet = card_verdict([wiki, tier1], "statements", stmt)
    tampered = copy.deepcopy(sheet)
    tampered["facts"][0]["evidence"][1]["fetch"] = "wikipedia"
    try:
        cf.verify_field(tampered, "statements", "S")
        out["verify_field"]["provenance_edited_after_signing"] = "accepted"
    except cf.FieldRefused as e:
        out["verify_field"]["provenance_edited_after_signing"] = f"refused ({e.code})"
    # a date may rest on a reference work (fact_sheet.strongly_sourced): a statement on a date fact whose only
    # evidence is Wikipedia stands, the whole date expression being no figure; a quantity on it still needs a site
    dates = {}
    for text in DATE_TEXTS + QUANTITY_ON_DATE_TEXTS:
        ev = dict(wiki, quote=f"{text}, according to the article.")
        dates[text] = card_verdict([ev], "statements", {"id": "S", "text": text, "fact_id": "F1"},
                                   fact_kind="date", statement=f"{text}.")[0]
    out["date_fact_statements_on_wikipedia_alone"] = dates
    out["shipped_date_facts_refused_as_statements_on_wikipedia_alone"] = shipped_date_refusals(root)
    line = getattr(fact_sheet, "sources_line", None)
    out["review_card_sources_line"] = line({"facts": [{"evidence": [wiki, attacker]}]}) if line else None

    a, g, v = out["wikipedia_plus_attacker_page"], out["wikipedia_plus_cited_gov_page"], out["verify_field"]
    out["acceptance_met"] = bool(
        a["disputed"] is True and a["hedge"] and g["disputed"] is True and g["hedge"]
        and all(p["fetch"] for p in a["evidence_provenance"])
        and v["number_statement_wikipedia_plus_cited_page"].startswith("refused")
        and v["quote_wikipedia_only"].startswith("refused")
        and v["control_number_statement_with_tier1"] == "accepted" and v["control_quote_with_tier1"] == "accepted"
        and v["provenance_edited_after_signing"].startswith("refused")
        and all(dates[t] == "accepted" for t in DATE_TEXTS)
        and all(dates[t].startswith("refused") for t in QUANTITY_ON_DATE_TEXTS)
        and out["review_card_sources_line"] == "sources: Wikipedia + 1 site it cites")
    return out


if __name__ == "__main__":
    result = main(Path(sys.argv[1]).resolve())
    text = json.dumps(result, indent=1, ensure_ascii=False)
    print(text)
    (Path(__file__).resolve().parent / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
