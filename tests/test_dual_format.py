"""
tests/test_dual_format.py
=========================
Unit tests for the Gold-Standard Dual-Format (Viral Reels & In-Stream Documentaries) Expansion:
1. DocumentaryAct and DeepDivePackage schemas.
2. Fact-checking act-by-act via trace_check.check_act.
3. Deep-dive eligibility gating.
4. Database parent-child run tracking and sibling queries.
5. 16:9 widescreen subtitle generation with bottom-third placement.
6. Hardware acceleration encoder detection fallback.
"""

from pathlib import Path
from src.models import DocumentaryAct, DeepDivePackage, Source
from src.trace_check import check_act
from src.script_engine import ScriptEngine
from src.video_composer import VideoComposer, _get_video_encoder
from src.db import PipelineDatabase


def test_documentary_act_and_package_models():
    act1 = DocumentaryAct(
        act_number=1,
        act_title="The Discovery",
        target_duration_sec=45,
        narration_text="In 1963, a Turkish homeowner knocked down a basement wall.",
        visual_intent="Dusty basement with breach",
        graphic_type="none",
        claim_fact_ids=["F1"],
    )
    assert act1.act_number == 1
    assert "Turkish" in act1.narration_text

    pkg = DeepDivePackage(
        topic_id="derinkuyu_underground_city",
        title="Derinkuyu Underground City",
        youtube_title="The 18-Story Subterranean Metropolis: How Derinkuyu Protected 20,000 People",
        description="Comprehensive investigative documentary with chapter timestamps.",
        tags=["derinkuyu", "ancient", "underground"],
        acts=[act1],
        closing_debate_question="Could modern civilization survive for months in subterranean shelters?",
        sources=[Source(title="Turkish Ministry", url="https://muze.gov.tr")],
    )
    assert pkg.total_words == 10
    assert "[Act 1: The Discovery]" in pkg.full_narration


def test_check_act_fact_verification(monkeypatch):
    sheet = {
        "topic_id": "derinkuyu_underground_city",
        "facts": [
            {
                "id": "F1",
                "statement": "Derinkuyu has an approximate depth of 85 metres.",
                "disputed": False,
                "hedge": "",
                "evidence": [{"quote": "depth of 85 metres"}],
            }
        ],
    }
    # Mock LLM call to return clean TraceReport with traced verdict
    from src.trace_check import TraceReport, TracedClaim
    monkeypatch.setattr(
        "src.free_llm.call",
        lambda *args, **kwargs: TraceReport(
            claims=[
                TracedClaim(
                    claim="depth of 85 metres",
                    where="narration",
                    fact_ids=["F1"],
                    verdict="traced",
                    note="Accurate",
                )
            ]
        ),
    )
    passed, findings = check_act("Derinkuyu has an approximate depth of 85 metres.", sheet, 1)
    assert passed is True
    assert len(findings) == 0


def test_is_deep_dive_eligible():
    engine = ScriptEngine()
    topic_rich = {
        "id": "test_rich",
        "key_facts": [f"Fact {i}" for i in range(12)],
    }
    topic_poor = {
        "id": "test_poor",
        "key_facts": ["Fact 1", "Fact 2"],
    }
    assert engine.is_deep_dive_eligible(topic_rich, visual_count=10) is True
    assert engine.is_deep_dive_eligible(topic_poor, visual_count=10) is False
    assert engine.is_deep_dive_eligible(topic_rich, visual_count=2) is False


def test_db_records_parent_and_child_runs(tmp_path: Path):
    db_file = tmp_path / "test_pipeline.db"
    db = PipelineDatabase(db_path=db_file)

    topic_id = "derinkuyu_underground_city"
    with db._conn() as conn:
        conn.execute("INSERT OR IGNORE INTO topics (id, title, status) VALUES (?, ?, 'queued')", (topic_id, "Derinkuyu"))
    parent_id = "run_derinkuyu_100_short"
    child_id = "run_derinkuyu_100_long"

    db.create_run(parent_id, topic_id, content_format="reel")
    db.create_run(child_id, topic_id, parent_run_id=parent_id, content_format="deep_dive")

    parent_run = db.get_run(parent_id)
    child_run = db.get_run(child_id)

    assert parent_run["content_format"] == "reel"
    assert parent_run["parent_run_id"] is None
    assert child_run["content_format"] == "deep_dive"
    assert child_run["parent_run_id"] == parent_id



def test_widescreen_subtitle_geometry(tmp_path: Path):
    composer = VideoComposer()
    words = [
        {"word": "Ancient", "start": 0.0, "dur": 0.4},
        {"word": "subterranean", "start": 0.4, "dur": 0.5},
        {"word": "chambers", "start": 0.9, "dur": 0.4},
    ]
    ass_path = tmp_path / "captions_16x9.ass"
    composer.build_subtitles(words, ass_path, width=1920, height=1080)

    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in content
    assert "PlayResY: 1080" in content
    # Center X should be 960 (1920 // 2)
    assert "\\pos(960," in content


def test_hardware_video_encoder():
    encoder = _get_video_encoder()
    assert isinstance(encoder, list)
    assert "-c:v" in encoder
    assert ("h264_nvenc" in encoder) or ("libx264" in encoder)


def test_compute_act_chapters():
    from main import compute_act_chapters
    acts = [
        DocumentaryAct(
            act_number=1,
            act_title="The Discovery",
            target_duration_sec=45,
            narration_text="In 1963, a Turkish homeowner knocked down a basement wall.",
            visual_intent="Dusty basement",
            graphic_type="none",
        ),
        DocumentaryAct(
            act_number=2,
            act_title="Subterranean Engineering",
            target_duration_sec=45,
            narration_text="Down into the dark, eighteen levels descended.",
            visual_intent="Deep shafts",
            graphic_type="cross_section",
        ),
    ]
    words = [
        {"word": "In", "start": 0.0, "dur": 0.2},
        {"word": "1963,", "start": 0.2, "dur": 0.5},
        {"word": "a", "start": 0.7, "dur": 0.1},
        {"word": "Turkish", "start": 0.8, "dur": 0.4},
        {"word": "homeowner", "start": 1.2, "dur": 0.4},
        {"word": "Down", "start": 45.5, "dur": 0.3},
        {"word": "into", "start": 45.8, "dur": 0.2},
        {"word": "the", "start": 46.0, "dur": 0.1},
        {"word": "dark,", "start": 46.1, "dur": 0.3},
    ]
    chapters, text, cues = compute_act_chapters(acts, words, 90.0)
    assert len(chapters) == 2
    assert chapters[0]["timestamp"] == "0:00"
    assert chapters[0]["title"] == "The Discovery"
    assert chapters[1]["timestamp"] == "0:45"
    assert chapters[1]["title"] == "Subterranean Engineering"
    assert cues == [45.5]
    assert "0:00 Act 1 - The Discovery" in text
    assert "0:45 Act 2 - Subterranean Engineering" in text


def test_two_tier_claim_classifier_rhetorical_framing():
    from src.trace_check import TracedClaim, TraceReport
    # Rhetorical framing claim (contrast/hook) should be accepted as traced even without a fact sheet fact_id
    claim = TracedClaim(
        claim="Modern builders erect monuments upward into the sky.",
        claim_type="rhetorical_framing",
        where="hook",
        fact_ids=[],
        verdict="traced",
        note="Rhetorical introductory contrast",
    )
    report = TraceReport(claims=[claim])
    assert report.claims[0].claim_type == "rhetorical_framing"
    assert report.claims[0].verdict == "traced"

