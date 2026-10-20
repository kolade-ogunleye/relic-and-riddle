import pytest

from src.fact_evidence import current_fact_review, record_fact_review


def test_review_expires_when_public_copy_or_research_changes(tmp_path):
    sheet = tmp_path / "facts.json"
    sheet.write_text('{"facts": []}')
    payload = {"guardrail_passed": True, "title": "Original", "narration_script": "Verified text"}
    record_fact_review(payload, sheet)
    assert current_fact_review(payload)
    payload["youtube_title"] = "A new claim"
    assert not current_fact_review(payload)
    record_fact_review(payload, sheet)
    sheet.write_text('{"facts": ["changed"]}')
    assert not current_fact_review(payload)


def test_metadata_and_attribution_do_not_invalidate_public_facts(tmp_path):
    sheet = tmp_path / "facts.json"
    sheet.write_text('{}')
    payload = {"guardrail_passed": True, "caption": "Verified caption", "title": "Title"}
    record_fact_review(payload, sheet)
    payload["caption"] += "\n\n🎵 Music: Artist"
    payload["qa_summary"] = "Completed"
    assert current_fact_review(payload)


def test_missing_research_cannot_receive_review_evidence(tmp_path):
    with pytest.raises(ValueError, match="research file"):
        record_fact_review({"guardrail_passed": True}, tmp_path / "missing.json")


def test_diagram_claims_are_part_of_public_review():
    from src.trace_check import surfaces
    payload = {"ventilation_airflow": {"labels": {"summary": "Sheltered 20,000 residents"}}}
    assert any("Sheltered 20,000 residents" in text for _, text in surfaces(payload))


def test_chapter_counters_do_not_hide_real_numerical_claims():
    from src.trace_check import untraced_numbers
    sheet = {"facts": []}
    assert untraced_numbers({"caption": "Act 2: Architecture", "description": "0:45 Chapter 3: History"}, sheet) == []
    problems = untraced_numbers({"caption": "Act 2: There are 85 levels"}, sheet)
    assert any("85" in problem for problem in problems)
