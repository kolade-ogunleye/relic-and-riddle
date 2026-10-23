"""A traced claim is a passed claim, and a public-domain work is evidenced by its source page."""
from src import improve
from src.credits import rights_evidence_ok
from src.telegram_bot import TelegramApprovalBot


def test_traced_claims_are_not_an_integrity_alert_or_a_finding(db):
    # Production 2026-10-23: 23 traced claims showed as "Integrity Alert: 23 disputed/hedged".
    audits = [{"claim": f"claim {i}", "verdict": "traced"} for i in range(23)]
    payload = {"title": "T", "narration_script": "x", "fact_audits": audits, "consensus_confidence": 100.0}
    text = TelegramApprovalBot.format_card_text(payload, "run_x")
    assert "Integrity Alert" not in text and "Integrity Score:</b> 100.0% (Pass — 0 hedged)" in text
    assert improve.collect_run(db, "run_x", None, payload) == 0
    payload["fact_audits"].append({"claim": "bad", "verdict": "untraced"})
    assert "Integrity Alert" in TelegramApprovalBot.format_card_text(payload, "run_x")


def test_public_domain_needs_its_source_page_not_a_licence_url():
    pd = {"license": "Public domain", "source_url": "https://commons.wikimedia.org/wiki/File:Vitruvius.jpg"}
    assert rights_evidence_ok(pd)
    assert not rights_evidence_ok({**pd, "source_url": ""})  # still needs its evidence
    assert rights_evidence_ok({"license": "CC0", "source_url": "https://x"})
    by = {"license": "CC BY 4.0", "source_url": "https://x"}
    assert not rights_evidence_ok(by) and rights_evidence_ok({**by, "license_url": "https://cc/by/4.0"})
    assert not rights_evidence_ok({"license": "PDF scan", "source_url": "https://x"})


def test_a_bare_pd_licence_counts_as_public_domain():
    assert rights_evidence_ok({"license": "PD", "source_url": "https://x"})


def test_a_video_check_issue_says_where_it_is_in_the_video():
    # The card named "beat 9" only; the reviewer could not find it (the photo sheet is the pool, not beats).
    from src.video_qa import QAReport, qa_summary_html
    report = QAReport.model_validate({"issues": [{"beat": 9, "severity": "major", "issue": "Wrong picture."}], "summary": "s"})
    beats = [{"start": 4.6 * i, "end": 4.6 * (i + 1), "text": "x"} for i in range(11)]
    assert "beat 9, 0:36-0:41: Wrong picture." in qa_summary_html(report, beats)
    assert "beat 9: Wrong picture." in qa_summary_html(report)  # without beats, as before
