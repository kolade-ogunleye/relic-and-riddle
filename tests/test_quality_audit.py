"""
tests/test_quality_audit.py
==========================
Comprehensive test suite verifying the Autonomous Closed-Loop Self-Healing &
Continuous Improvement Architecture across layout constraints, deterministic
audits, and targeted auto-remediation.
"""

import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from src.graphics import LayoutConstraintSolver
from src.typography import get_font
from src.video_qa import QualityAuditor, verify_provenance_integrity


# -----------------------------------------------------------------------------
# 1. LayoutConstraintSolver Tests
# -----------------------------------------------------------------------------
def test_layout_solver_boxes_intersect():
    # Box A: [10, 10, 50, 50], Box B: [40, 40, 80, 80] -> Overlap
    box_a = (10, 10, 50, 50)
    box_b = (40, 40, 80, 80)
    assert LayoutConstraintSolver.boxes_intersect(box_a, box_b) is True

    # Box C: [60, 60, 100, 100] -> Disjoint from Box A
    box_c = (60, 60, 100, 100)
    assert LayoutConstraintSolver.boxes_intersect(box_a, box_c) is False

    # Clearance margin enforcement
    # Box D: [52, 10, 90, 50] -> Gap is 2px, but margin requirement is 10px
    box_d = (52, 10, 90, 50)
    assert LayoutConstraintSolver.boxes_intersect(box_a, box_d, margin=0.0) is False
    assert LayoutConstraintSolver.boxes_intersect(box_a, box_d, margin=10.0) is True


def test_layout_solver_box_intersects_line():
    # Horizontal line at y=100 from x=0 to x=200
    line_y = 100.0
    x_range = (0.0, 200.0)

    # Box crossing y=100: [50, 80, 120, 120] -> Intersects
    box_intersecting = (50, 80, 120, 120)
    assert LayoutConstraintSolver.box_intersects_line(box_intersecting, line_y, x_range) is True

    # Box completely above line: [50, 50, 120, 90] -> No intersection
    box_above = (50, 50, 120, 90)
    assert LayoutConstraintSolver.box_intersects_line(box_above, line_y, x_range) is False

    # Box completely below line: [50, 110, 120, 150] -> No intersection
    box_below = (50, 110, 120, 150)
    assert LayoutConstraintSolver.box_intersects_line(box_below, line_y, x_range) is False

    # Box horizontally outside x_range
    box_outside = (250, 80, 300, 120)
    assert LayoutConstraintSolver.box_intersects_line(box_outside, line_y, x_range) is False


def test_layout_solver_relax_vertical_stack():
    solver = LayoutConstraintSolver(min_padding=24)
    # Overlapping targets
    targets = [100.0, 110.0, 120.0]
    heights = [20.0, 20.0, 20.0]
    bounds = (50.0, 500.0)

    relaxed = solver.relax_vertical_stack(targets, heights, bounds, min_gap=24.0)

    # Invariants: each item + height + min_gap <= next item
    for i in range(len(relaxed) - 1):
        assert relaxed[i + 1] >= relaxed[i] + heights[i] + 24.0 - 0.01

    # Invariants: within bounds
    assert relaxed[0] >= bounds[0]
    assert relaxed[-1] + heights[-1] <= bounds[1]


def test_layout_solver_shelf_anchor_invariant():
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f_title = get_font("Anton", 34)
    f_sub = get_font("Montserrat", 22)

    shelf_x = 1000
    shelf_y = 500

    coords = LayoutConstraintSolver.get_shelf_coordinates(
        d=d,
        title="CARVED INSIDE THE PASSAGEWAY",
        f_title=f_title,
        subtitle="1,000 - 1,500 KG BASALT DISC",
        f_sub=f_sub,
        shelf_x=shelf_x,
        shelf_y=shelf_y,
        align="left",
        shelf_gap=10,
    )

    assert coords["is_valid"] is True
    # Title bottom must be strictly above shelf_y
    assert coords["title_bbox"][3] < shelf_y
    # Subtitle top must be strictly below shelf_y
    assert coords["sub_bbox"][1] > shelf_y


# -----------------------------------------------------------------------------
# 3. QualityAuditor Deterministic Audits
# -----------------------------------------------------------------------------
def _declared_section(tmp_path, subject, labels):
    """A cross-section graphic with its lineage sidecar, so the audit judges it by the words it shows."""
    from src.lineage import record_graphic
    code = tmp_path / "draw.py"
    code.write_bytes(b"drawing code")
    graphic = tmp_path / "graphic_section.mp4"
    graphic.write_bytes(b"graphic")
    record_graphic(graphic, code, {"builder": "cross-section", "payload": {"cross_section": {
        "subject": subject, "total_depth_ft": 280.0, "approximate": True,
        "levels": [{"depth_ft": 15.0 * (k + 1), "label": label} for k, label in enumerate(labels)]}}})
    return graphic


def test_auditor_semantic_cross_correlation_pass(tmp_path):
    auditor = QualityAuditor()
    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "Massive rolling stone doors sealed the corridors from defenders inside.",
            "asset": _declared_section(tmp_path, "Rolling stone doors", ["Sealed corridors", "Stone disc"]),
        }
    ]
    words = [
        {"word": "Massive", "start": 0.2, "dur": 0.5},
        {"word": "rolling", "start": 0.8, "dur": 0.4},
        {"word": "stone", "start": 1.3, "dur": 0.3},
        {"word": "doors", "start": 1.7, "dur": 0.4},
    ]

    report = auditor.audit_rendered_timeline(video_path=Path("non_existent.mp4"), timeline=timeline, words=words)
    desync_issues = [i for i in report["issues"] if i["type"] in ("semantic_desync", "temporal_desync")]
    assert len(desync_issues) == 0


def test_auditor_semantic_cross_correlation_fail(tmp_path):
    auditor = QualityAuditor()
    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "They gathered at the communal dining tables to eat grain.",
            "asset": _declared_section(tmp_path, "Ventilation shafts", ["Fresh air intake", "Shaft base"]),
        }
    ]
    words = [
        {"word": "They", "start": 0.2, "dur": 0.3},
        {"word": "gathered", "start": 0.6, "dur": 0.4},
    ]

    report = auditor.audit_rendered_timeline(video_path=Path("non_existent.mp4"), timeline=timeline, words=words)
    desync_issues = [i for i in report["issues"] if i["type"] == "semantic_desync"]
    assert len(desync_issues) == 1
    assert "missing narration keywords" in desync_issues[0]["issue"]
    assert report["score"] < 100
    assert report["certified"] is False


def test_auditor_loudness_gate():
    auditor = QualityAuditor()
    dummy_video = Path("dummy.mp4")

    # Mock measure_program_loudness to return compliant audio
    with patch("src.video_qa.measure_program_loudness", return_value={"integrated_lufs": -14.3, "true_peak_dbtp": -2.1}):
        with patch.object(Path, "exists", return_value=True):
            report = auditor.audit_rendered_timeline(dummy_video, timeline=[], words=[])
            assert report["loudness"]["integrated_lufs"] == -14.3
            assert not any(i["type"] == "loudness" for i in report["issues"])

    # Mock measure_program_loudness to return out-of-spec loud audio (-10 LUFS, +0.5 dBTP)
    with patch("src.video_qa.measure_program_loudness", return_value={"integrated_lufs": -10.0, "true_peak_dbtp": 0.5}):
        with patch.object(Path, "exists", return_value=True):
            report = auditor.audit_rendered_timeline(dummy_video, timeline=[], words=[])
            loud_issues = [i for i in report["issues"] if i["type"] == "loudness"]
            assert len(loud_issues) >= 1
            assert report["score"] <= 85
            assert report["certified"] is False


def test_certify_reports_the_audit(tmp_path):
    auditor = QualityAuditor()
    dummy_video = tmp_path / "master.mp4"
    dummy_video.write_bytes(b"dummy_mp4_bytes")

    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "Deep ventilation shafts circulated fresh air throughout the city.",
            "asset": _declared_section(tmp_path, "Ventilation shafts", ["Fresh air intake", "Shaft base"]),
        }
    ]
    words = [
        {"word": "Deep", "start": 0.1, "dur": 0.3},
        {"word": "ventilation", "start": 0.5, "dur": 0.5},
        {"word": "shafts", "start": 1.1, "dur": 0.4},
        {"word": "air", "start": 1.7, "dur": 0.3},
    ]

    with patch("src.video_qa.measure_program_loudness", return_value={"integrated_lufs": -14.4, "true_peak_dbtp": -2.2}):
        cycle_result = auditor.certify(
            video_path=dummy_video,
            timeline=timeline,
            words=words,
            run_dir=tmp_path,
        )

        assert cycle_result["score"] == 100
        assert cycle_result["certified"] is False
        assert any(i["type"] == "visual_check_failed" for i in cycle_result["issues"])


def test_provenance_integrity_with_self_healing(tmp_path):
    prov_file = tmp_path / "provenance.json"
    data = {
        "master_video": {"sha256": "abcdef1234567890abcdef1234567890"},
        "assets_count": 5,
        "self_healing": {
            "score": 100,
            "certified": True,
            "iterations_run": 1,
            "remediations_applied": [],
        },
    }
    prov_file.write_text(json.dumps(data), encoding="utf-8")
    assert verify_provenance_integrity(prov_file) is False

    # Sub-standard score fails certification
    data["self_healing"]["score"] = 60
    prov_file.write_text(json.dumps(data), encoding="utf-8")
    assert verify_provenance_integrity(prov_file) is False


def test_standards_compliance_in_provenance(tmp_path):
    from src.credits import generate_provenance_ledger
    master = tmp_path / "test_master.mp4"
    master.write_bytes(b"dummy_video_bytes")
    prov_path = generate_provenance_ledger(
        run_dir=tmp_path,
        topic={"id": "01_derinkuyu", "title": "Derinkuyu"},
        payload={"topic": "Derinkuyu"},
        master_mp4=master,
    )
    assert prov_path.exists()
    prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
    assert "standards_compliance" not in prov_data
    assert prov_data["self_healing"]["certified"] is False
    assert prov_data["privacy_and_security"]["face_anonymization"] == "unverified"


def test_audit_carousel_slides(tmp_path):
    auditor = QualityAuditor()

    # 1. Missing directory/slides
    res_empty = auditor.audit_carousel_slides(tmp_path)
    assert res_empty["certified"] is False
    assert res_empty["score"] == 0
    assert any(i["type"] == "missing_slides" for i in res_empty["issues"])

    # 2. Valid slides
    carousel_dir = tmp_path / "carousel"
    carousel_dir.mkdir()
    for idx in range(1, 4):
        # Create 1080x1350 slide with varied gradient
        img = Image.new("RGB", (1080, 1350), color=(30, 30, 40))
        d = ImageDraw.Draw(img)
        d.text((100, 100), f"Valid Slide {idx}", fill=(212, 175, 55))
        d.rectangle([100, 200, 500, 400], fill=(60, 60, 80))
        img.save(carousel_dir / f"carousel_slide_{idx:02d}.jpg")

    res_valid = auditor.audit_carousel_slides(tmp_path)
    assert res_valid["certified"] is True
    assert res_valid["score"] == 100
    assert res_valid["slides_audited"] == 3
    assert len(res_valid["issues"]) == 0

    # 3. Add a blank/monochrome slide
    blank_img = Image.new("RGB", (1080, 1350), color=(0, 0, 0))
    blank_img.save(carousel_dir / "carousel_slide_04.jpg")

    res_blank = auditor.audit_carousel_slides(carousel_dir)
    assert res_blank["certified"] is False
    assert any(i["type"] == "blank_slide" for i in res_blank["issues"])


def test_audit_story_cards(tmp_path):
    auditor = QualityAuditor()

    # 1. Missing stories
    res_empty = auditor.audit_story_cards(tmp_path)
    assert res_empty["certified"] is False
    assert any(i["type"] == "missing_stories" for i in res_empty["issues"])

    # 2. Valid story frames (1080x1920)
    story_dir = tmp_path / "stories"
    story_dir.mkdir()
    for idx in range(1, 4):
        img = Image.new("RGB", (1080, 1920), color=(20, 24, 32))
        d = ImageDraw.Draw(img)
        d.rectangle([100, 200, 800, 1000], fill=(50, 50, 70))
        d.text((200, 500), f"Valid Story Card {idx}", fill=(255, 215, 0))
        img.save(story_dir / f"story_{idx}_card.jpg")

    res_valid = auditor.audit_story_cards(tmp_path)
    assert res_valid["certified"] is True
    assert res_valid["score"] == 100
    assert res_valid["cards_audited"] == 3

    # 3. Blank card
    blank = Image.new("RGB", (1080, 1920), color=(0, 0, 0))
    blank.save(story_dir / "story_4_blank.jpg")
    res_blank = auditor.audit_story_cards(story_dir)
    assert res_blank["certified"] is False
    assert any(i["type"] == "blank_card" for i in res_blank["issues"])


def test_audit_thumbnails(tmp_path):
    auditor = QualityAuditor()

    # Valid cover 1080x1920, valid grid 1080x1080
    cover = tmp_path / "thumbnail_cover.jpg"
    grid = tmp_path / "thumbnail_profile_grid.jpg"

    cov_im = Image.new("RGB", (1080, 1920), color=(25, 28, 36))
    d1 = ImageDraw.Draw(cov_im)
    d1.rectangle([100, 200, 800, 1000], fill=(50, 50, 70))
    d1.text((300, 800), "Ancient Mystery", fill=(255, 215, 0))
    cov_im.save(cover)

    grid_im = Image.new("RGB", (1080, 1080), color=(25, 28, 36))
    d2 = ImageDraw.Draw(grid_im)
    d2.rectangle([100, 200, 800, 800], fill=(50, 50, 70))
    d2.text((300, 500), "Ancient Mystery", fill=(255, 215, 0))
    grid_im.save(grid)

    res = auditor.audit_thumbnails(cover, grid)
    assert res["certified"] is True
    assert res["score"] == 100
    assert res["thumbnails_audited"] == 2

    # Invalid ratio cover
    bad_cover = tmp_path / "bad_cover.jpg"
    Image.new("RGB", (800, 800), color=(30, 30, 30)).save(bad_cover)
    res_bad = auditor.audit_thumbnails(bad_cover, grid)
    assert res_bad["certified"] is False
    assert any(i["type"] == "invalid_cover_ratio" for i in res_bad["issues"])


def test_audit_publishing_policy():
    auditor = QualityAuditor()

    # 1. Missing AI disclosure
    payload_no_ai = {
        "caption": "A deep dive into ancient subterranean architecture. What does the evidence suggest?",
        "is_generated_by_ai": False,
    }
    res_ai = auditor.audit_publishing_policy(payload_no_ai)
    assert res_ai["certified"] is False
    assert any(i["type"] == "missing_ai_disclosure" for i in res_ai["issues"])

    # 2. Engagement bait detected
    payload_bait = {
        "caption": "Was this built by ancient engineers? Comment YES or NO below!",
        "is_generated_by_ai": True,
    }
    res_bait = auditor.audit_publishing_policy(payload_bait)
    assert res_bait["certified"] is False
    assert any(i["type"] == "engagement_bait_detected" for i in res_bait["issues"])

    # 3. Clean compliant payload
    payload_clean = {
        "caption": "Did ancient builders design Derinkuyu as an emergency fortress, or a permanent underground city? What does the volcanic geology suggest?",
        "is_generated_by_ai": True,
        "claims": [{"claim": "Excavated in volcanic tuff", "confidence": 0.95}],
    }
    res_clean = auditor.audit_publishing_policy(payload_clean)
    assert res_clean["certified"] is True
    assert res_clean["score"] == 100
    assert len(res_clean["issues"]) == 0



def test_backup_summary_reports_only_what_was_measured(tmp_path):
    from src.backup import generate_dispute_summary
    run = tmp_path / "run"
    run.mkdir()
    (tmp_path / "archive").mkdir()
    (run / "provenance.json").write_text(json.dumps({"master_video": {"sha256": "x"}}), encoding="utf-8")
    text = generate_dispute_summary(run, tmp_path / "archive").read_text(encoding="utf-8")
    assert "Certified Clean" not in text and "not run" in text and "-14.0" not in text
    (run / "provenance.json").write_text(json.dumps({"self_healing": {"score": 70, "certified": False,
        "loudness": {"integrated_lufs": -15.2, "true_peak_dbtp": -1.9}}}), encoding="utf-8")
    text = generate_dispute_summary(run, tmp_path / "archive").read_text(encoding="utf-8")
    assert "70/100, NOT certified" in text and "-15.2 LUFS" in text


def test_repairable_scene_defects_become_planner_feedback():
    from src.video_qa import audit_scene_feedback
    audit = {"issues": [
        {"type": "semantic_desync", "scene_index": 2, "asset": "x/graphic_map.mp4"},
        {"type": "loudness", "scene_index": -1},                      # not fixable by re-planning
        {"type": "caption_collision", "scene_index": 4, "asset": "x/graphic_numbers.mp4"},
    ]}
    lines = audit_scene_feedback(audit).splitlines()
    assert lines[0].startswith("Beat 3:") and "graphic_map.mp4" in lines[0]
    assert lines[1].startswith("Beat 5:") and len(lines) == 2
    assert audit_scene_feedback(None) == ""
