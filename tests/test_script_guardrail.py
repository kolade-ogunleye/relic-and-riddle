import pytest

from src.fact_checker import ClaimFactChecker
from src.models import ClaimAudit, FactCheckReport, ScriptPackage, Source
from src.script_engine import ScriptEngine, ScriptUnavailableError, validate_structure


def test_curated_script_is_structurally_valid(curated_payload):
    assert validate_structure(ScriptPackage.model_validate(curated_payload)) == []


def test_curated_script_offline_requires_human_review(curated_payload):
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed and "reviewed" in notes
    assert ScriptEngine().verify_claims(curated_payload, allow_unreviewed_curated=True)[0]
    curated_payload["human_reviewed"] = True
    assert ScriptEngine().verify_claims(curated_payload)[0]


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p.update(narration_script="Too short.", closing_sentence="Too short."), "words"),
    (lambda p: p.update(narration_script=p["narration_script"][:-1] + " and", closing_sentence="x"), "trailing word"),
    (lambda p: p.update(caption=p["caption"] + " Built by aliens."), "trope"),
    (lambda p: p.update(story_slides=p["story_slides"][:2]), "3 story slides"),
    (lambda p: p.update(title="x" * 61), "Title"),
])
def test_structural_rejections(curated_payload, mutate, expected):
    curated_payload["human_reviewed"] = True
    mutate(curated_payload)
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed
    assert expected in notes


def test_trope_check_uses_word_boundaries(curated_payload):
    # "alienated" must not trip the "alien" filter
    curated_payload["caption"] += " Villagers felt alienated."
    assert ScriptEngine().verify_claims(curated_payload, allow_unreviewed_curated=True)[0]


def test_uncurated_script_fails_closed_without_claude(curated_payload):
    curated_payload["origin"] = "claude"
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed and "failing closed" in notes


def test_topic_without_any_script_source_raises():
    with pytest.raises(ScriptUnavailableError):
        ScriptEngine().generate_script({"id": "02_roman_self_healing_concrete", "title": "Roman concrete"})


def _claim(verdict, hedged=False, sources=True):
    return ClaimAudit(claim="c", verdict=verdict, hedged_in_script=hedged, note="n",
                      sources=[Source(title="s", url="https://e.org")] if sources else [])


@pytest.mark.parametrize("claims, ok", [
    ([_claim("supported")], True),
    ([_claim("supported"), _claim("disputed", hedged=True)], True),
    ([_claim("supported"), _claim("disputed", hedged=False)], False),
    ([_claim("supported"), _claim("unsupported")], False),
    ([_claim("false")], False),
    ([_claim("supported", sources=False)], False),
    ([], False),
])
def test_fact_check_pass_rule(claims, ok):
    passed, confidence, audits, _ = ClaimFactChecker.evaluate(FactCheckReport(claims=claims, summary=""))
    assert passed is ok
    assert len(audits) == len(claims)


def test_supported_claim_with_hallucinated_source_fails():
    report = FactCheckReport(claims=[_claim("supported")], summary="")
    assert ClaimFactChecker.evaluate(report, retrieved_urls={"https://e.org"})[0]
    assert not ClaimFactChecker.evaluate(report, retrieved_urls={"https://other.org"})[0]


def test_verdict_casing_is_normalized():
    assert ClaimAudit(claim="c", verdict=" Supported ", hedged_in_script=False, note="", sources=[]).verdict == "supported"
