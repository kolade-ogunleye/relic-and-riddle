"""
tests/test_motion_hook_advanced.py
==================================
Comprehensive test suite for the Archetype-Aware AI Motion Hook Engine,
Material Substrate Grounding, Anchor Pre-Conditioning, and Homography Anti-Hallucination Gate.
"""

import numpy as np
from PIL import Image

from src import motion
from src.video_qa import verify_motion_hook_integrity


def test_classify_archetypes():
    # Megastructures
    assert motion.classify_archetype({"title": "Kailasa Temple Monolith", "key_facts": ["carved from basalt cliff"]}) == "megastructure"
    assert motion.classify_archetype({"title": "Derinkuyu Underground City", "key_facts": ["18 levels in volcanic tuff"]}) == "megastructure"
    assert motion.classify_archetype({"title": "Great Pyramid of Giza", "key_facts": ["limestone blocks"]}) == "megastructure"

    # Relics / Artifacts
    assert motion.classify_archetype({"title": "The Antikythera Mechanism", "key_facts": ["bronze gears recovered from shipwreck"]}) == "relic_artifact"
    assert motion.classify_archetype({"title": "The Voynich Manuscript", "key_facts": ["vellum parchment mystery"]}) == "relic_artifact"
    assert motion.classify_archetype({"title": "The Baghdad Battery", "key_facts": ["terracotta jar with copper cylinder"]}) == "relic_artifact"

    # Riddles / Events
    assert motion.classify_archetype({"title": "The Bronze Age Collapse", "key_facts": ["sudden destruction of civilizations"]}) == "riddle_event"
    assert motion.classify_archetype({"title": "The Tunguska Event", "key_facts": ["mysterious blast flattened siberian forest"]}) == "riddle_event"


def test_extract_material_substrate():
    kailasa = {"title": "Kailasa Temple", "key_facts": ["Deccan basalt cliff"]}
    assert "basalt" in motion.extract_material_substrate(kailasa).lower()

    antikythera = {"title": "Antikythera Mechanism", "key_facts": ["ancient bronze gear train"]}
    assert "bronze" in motion.extract_material_substrate(antikythera).lower()

    concrete = {"title": "Roman Concrete", "key_facts": ["pozzolanic lime clasts"]}
    assert "pozzolanic" in motion.extract_material_substrate(concrete).lower()

    manuscript = {"title": "Voynich Manuscript", "key_facts": ["vellum parchment"]}
    assert "parchment" in motion.extract_material_substrate(manuscript).lower()


def test_build_motion_prompts_by_archetype():
    p_mega = motion.build_motion_prompt("megastructure", "Kailasa Temple", "tholeiitic basalt")
    assert "24mm wide-angle" in p_mega
    assert "tholeiitic basalt" in p_mega
    assert "Strict rigidity constraint" in p_mega
    assert "Zero morphological drift" in p_mega

    p_relic = motion.build_motion_prompt("relic_artifact", "Antikythera Mechanism", "corroded bronze alloy")
    assert "100mm" in p_relic
    assert "macro probe lens" in p_relic
    assert "corroded bronze alloy" in p_relic
    assert "gear tooth" in p_relic
    assert "Zero morphological warping" in p_relic

    p_riddle = motion.build_motion_prompt("riddle_event", "Bronze Age Collapse", "archival cartography")
    assert "macro sweep" in p_riddle
    assert "archival cartography" in p_riddle


def test_pre_condition_anchor_frame(tmp_path):
    # Create an arbitrary horizontal image (1920x1080)
    raw_img = tmp_path / "raw.jpg"
    Image.new("RGB", (1920, 1080), (100, 120, 140)).save(raw_img)

    out = motion.pre_condition_anchor_frame(raw_img, tmp_path / "anchor_9_16.jpg", archetype="relic_artifact")
    assert out.exists()
    im = Image.open(out)
    assert im.size == (1080, 1920)


def test_verify_motion_hook_integrity_synthetic_pairs(tmp_path):
    import cv2
    np.random.seed(42)
    img1 = cv2.GaussianBlur(np.random.randint(0, 255, (600, 600), dtype=np.uint8), (7, 7), 0)
    for i in range(30):
        x, y, r = np.random.randint(50, 550), np.random.randint(50, 550), np.random.randint(10, 40)
        cv2.circle(img1, (x, y), r, int(np.random.randint(0, 255)), -1)

    f1 = tmp_path / "f1.jpg"
    f2_rigid = tmp_path / "f2_rigid.jpg"
    f2_hallucinated = tmp_path / "f2_hallucinated.jpg"

    cv2.imwrite(str(f1), img1)

    # 1. Rigid camera push/zoom (1.08x scale)
    M = cv2.getRotationMatrix2D((300, 300), 0, 1.08)
    img2_rigid = cv2.warpAffine(img1, M, (600, 600))
    cv2.imwrite(str(f2_rigid), img2_rigid)

    res_rigid = verify_motion_hook_integrity(f1, f2_rigid, archetype="megastructure")
    assert res_rigid["pass"] is True
    assert res_rigid["inlier_ratio"] > 0.40
    assert "Passed homography" in res_rigid["reason"]

    # 2. Hallucinated / random noise frame (morphological breakdown)
    img2_random = np.random.randint(0, 256, (600, 600), dtype=np.uint8)
    cv2.imwrite(str(f2_hallucinated), img2_random)

    res_fail = verify_motion_hook_integrity(f1, f2_hallucinated, archetype="megastructure")
    assert res_fail["pass"] is False


def test_motion_fails_safe_to_fallback_on_qa_rejection(monkeypatch, tmp_path):
    # Verify that when an AI generation produces a morphing/hallucinated clip,
    # the engine discards it and returns None, triggering the 2.5D parallax fallback path.
    class DummyDB:
        def media_spend(self, month):
            return 0.0

    db = DummyDB()
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 10.0)

    # Mock provider that fails the verification check
    def mock_animate_failing(img, out, prompt=None, archetype="megastructure", on_billed=None):
        raise motion.MotionError("Anti-hallucination homography check failed: inlier ratio 0.08 < 0.25")

    monkeypatch.setattr(motion, "providers", lambda topic=None, fact_sheet=None: [
        ("kling-3.0", lambda: True, mock_animate_failing, 0.84)
    ])

    test_img = tmp_path / "photo.jpg"
    Image.new("RGB", (1080, 1920), (80, 80, 80)).save(test_img)

    result = motion.animate(db, test_img, tmp_path / "out.mp4", topic={"title": "Test Topic"})
    # Must fail closed to None so 2.5D parallax runs at zero risk
    assert result is None
