"""
tests/test_self_healing.py
==========================
Comprehensive test suite verifying the Autonomous Closed-Loop Self-Healing &
Continuous Improvement Architecture across layout constraints, deterministic
audits, and targeted auto-remediation.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from src.graphics import LayoutConstraintSolver
from src.layout_rules import (
    DEFAULT_RULES,
    get_layout_rule,
    load_layout_rules,
    record_learned_adjustment,
    save_layout_rules,
)
from src.typography import get_font
from src.video_qa import ClosedLoopAuditor, verify_provenance_integrity


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
# 2. Persistent Layout Rules Ledger Tests
# -----------------------------------------------------------------------------
def test_layout_rules_ledger_io(tmp_path):
    custom_rules_file = tmp_path / "layout_rules.json"
    with patch("src.layout_rules.LAYOUT_RULES_PATH", custom_rules_file):
        rules = load_layout_rules()
        assert "shelf_anchor" in rules
        assert rules["shelf_anchor"]["min_gap"] == 10

        # Update and save
        rules["shelf_anchor"]["min_gap"] = 16
        save_layout_rules(rules)

        reloaded = load_layout_rules()
        assert reloaded["shelf_anchor"]["min_gap"] == 16

        # Record learned adjustment
        record_learned_adjustment("topic_01", "defense_shelf", {"shelf_gap": 18})
        updated = load_layout_rules()
        assert updated["learned_adjustments"]["topic_01"]["defense_shelf"]["shelf_gap"] == 18

        # Aspect ratio overrides
        assert get_layout_rule("shelf_gap", aspect_ratio="16:9") == 10
        assert get_layout_rule("shelf_gap", aspect_ratio="9:16") == 12


# -----------------------------------------------------------------------------
# 3. ClosedLoopAuditor Deterministic Audits
# -----------------------------------------------------------------------------
def test_auditor_semantic_cross_correlation_pass():
    auditor = ClosedLoopAuditor()
    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "Massive rolling stone doors sealed the corridors from defenders inside.",
            "asset": Path("output/run_1/graphic_defense.mp4"),
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


def test_auditor_semantic_cross_correlation_fail():
    auditor = ClosedLoopAuditor()
    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "They gathered at the communal dining tables to eat grain.",
            "asset": Path("output/run_1/graphic_ventilation.mp4"),
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
    auditor = ClosedLoopAuditor()
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


def test_auditor_auto_remediate_scene(tmp_path):
    auditor = ClosedLoopAuditor()
    run_dir = tmp_path / "test_run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Remediate layout overlap
    with patch("src.layout_rules.LAYOUT_RULES_PATH", tmp_path / "layout_rules.json"):
        success = auditor.auto_remediate_scene(
            run_dir=run_dir,
            scene_index=0,
            defect_type="layout_overlap",
            context={"issue": "text collision"},
        )
        assert success is False
        assert not (tmp_path / "layout_rules.json").exists()
        rules = load_layout_rules()
        assert rules["shelf_anchor"]["min_gap"] == 10

    # 2. Remediate temporal desync
    timeline = [{"scene_index": 1, "start": 10.0, "end": 16.0}]
    success_sync = auditor.auto_remediate_scene(
        run_dir=run_dir,
        scene_index=0,
        defect_type="temporal_desync",
        context={"trigger_time": 8.5, "timeline": timeline},
    )
    assert success_sync is False
    assert timeline[0]["start"] == 10.0


def test_auditor_closed_loop_cycle(tmp_path):
    auditor = ClosedLoopAuditor(max_healing_iterations=3)
    dummy_video = tmp_path / "master.mp4"
    dummy_video.write_bytes(b"dummy_mp4_bytes")

    timeline = [
        {
            "scene_index": 0,
            "start": 0.0,
            "end": 6.0,
            "text": "Deep ventilation shafts circulated fresh air throughout the city.",
            "asset": Path("graphic_ventilation.mp4"),
        }
    ]
    words = [
        {"word": "Deep", "start": 0.1, "dur": 0.3},
        {"word": "ventilation", "start": 0.5, "dur": 0.5},
        {"word": "shafts", "start": 1.1, "dur": 0.4},
        {"word": "air", "start": 1.7, "dur": 0.3},
    ]

    with patch("src.video_qa.measure_program_loudness", return_value={"integrated_lufs": -14.4, "true_peak_dbtp": -2.2}):
        cycle_result = auditor.run_self_healing_cycle(
            video_path=dummy_video,
            timeline=timeline,
            words=words,
            run_dir=tmp_path,
        )

        assert cycle_result["score"] == 100
        assert cycle_result["certified"] is False
        assert cycle_result["iterations_run"] == 1
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


def test_caption_collision_detection_and_remediation(tmp_path):
    auditor = ClosedLoopAuditor(max_healing_iterations=2)
    # Test remediation handler for caption_collision
    with patch("src.video_qa.load_layout_rules", return_value={"safe_zone": {"bottom_bound_reduction": 0}}), \
         patch("src.video_qa.save_layout_rules") as mock_save, \
         patch("src.video_qa.record_learned_adjustment") as mock_record:
        success = auditor.auto_remediate_scene(
            run_dir=tmp_path,
            scene_index=3,
            defect_type="caption_collision",
            context={"asset": "graphic_ventilation.mp4"},
        )
        assert success is False
        mock_save.assert_not_called()
        mock_record.assert_not_called()


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
    auditor = ClosedLoopAuditor()

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
