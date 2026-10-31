"""A picture's archive caption is the source's label, passed to the video check as context and never drawn on screen;
a finding that only compares the picture with that label is not a defect viewers see (Nazca beat 8, dev, 2026-10-29:
Commons filed the Hands geoglyph as the "Condor" and the release was blocked over the label)."""

import pytest

from src import video_qa
from src.models import QAIssue, QAReport


def _report(*issues):
    return QAReport(score=90, certified=False, summary="s", issues=[QAIssue(**i) for i in issues])


@pytest.mark.parametrize("text", [
    "Archive caption mentions the Condor geoglyph, but the image shows the Hands/Tree geoglyphs.",
    "The photo's metadata names a different site than the one pictured.",
    "The file name says 'Hummingbird' but the picture shows the Monkey.",
])
def test_a_finding_about_the_source_label_alone_is_not_counted(text):
    report = video_qa.drop_source_label_findings(_report({"type": "video_check", "beat": 8, "severity": "minor",
                                                          "issue": text}))
    assert report.issues == [] and "the source's label, not shown on screen" in report.summary


@pytest.mark.parametrize("text, severity", [
    ("The narration names the Condor, but the image shows the Hands geoglyph.", "minor"),       # what viewers hear
    ("The on-screen caption is cut off at the right edge.", "minor"),                           # what viewers see
    ("Archive caption names the Condor, but the image shows a modern highway.", "major"),       # never by wording
])
def test_findings_against_what_viewers_hear_or_see_still_count(text, severity):
    report = video_qa.drop_source_label_findings(_report({"type": "video_check", "beat": 8, "severity": severity,
                                                          "issue": text}))
    assert len(report.issues) == 1


def test_the_check_is_told_the_label_is_not_on_screen(tmp_path, monkeypatch):
    photo = tmp_path / "02_commons.jpg"
    photo.write_bytes(b"x")
    monkeypatch.setattr(video_qa, "_photo_record", lambda p: {"description": 'Aerial view of the "Condor"'})
    tag = video_qa.beat_tag(photo)
    assert "not shown on screen" in tag and "may be wrong" in tag and "Condor" in tag
