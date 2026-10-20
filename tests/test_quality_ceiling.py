"""
tests/test_quality_ceiling.py
=============================
Studio-Grade Quality Ceiling Evolution Test Suite.
Validates:
1. Kodak 2383 soft-knee cinema tone curves and BT.709 broadcast tagging.
2. 3D depth-layered key-art compositing with adaptive saliency gating (15-60%).
3. Multi-template 5-archetype magazine editorial carousels with semantic intent routing.
4. Ancient history procedural graphics suite (LiDAR scan, stratigraphy, isometric cutaway).
5. Closed-loop WCAG AAA contrast gating and subject non-occlusion auditing.
"""

import subprocess
from pathlib import Path
from PIL import Image
import pytest

from src.graphics import make_lidar_scan, make_stratigraphy, make_isometric_cutaway, CLIP_SECONDS
from src.multi_format import MultiFormatGenerator
from src.video_qa import QualityAuditor
from src.visual_engine import COVER_STYLES, VisualEngine, extract_foreground_mask


def test_cinema_tone_curves_ffmpeg():
    """Verify that FFmpeg processes Kodak 2383 soft-knee curve and BT.709 tags cleanly."""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
        "-vf", "curves=master='0/0.02 0.2/0.19 0.5/0.51 0.8/0.82 1/0.98':red='0/0.01 0.5/0.50 1/0.99':blue='0/0.04 0.5/0.49 1/0.94',scale=out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"FFmpeg failed cinema curve filter: {res.stderr}"


def test_depth_layered_thumbnail_generation(tmp_path):
    """Verify 3D depth-layered key-art thumbnail generation with timestamp safe zone clearance."""
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (75, 65, 55)).save(hero)
    payload = {
        "title": "Derinkuyu Underground City",
        "thumbnail": {
            "eyebrow": "DOCUMENTARY INVESTIGATION",
            "line1": "DERINKUYU",
            "line2": "ANCIENT METROPOLIS",
            "tagline": "280 FT UNDERGROUND",
        }
    }
    ve = VisualEngine()
    out_16x9 = tmp_path / "thumb_16x9.jpg"
    res = ve.generate_depth_layered_thumbnail(hero, payload, out_16x9, aspect_ratio="16:9")
    assert res.exists()
    im = Image.open(res)
    assert im.size == (1920, 1080)

    # Verify YouTube bottom-right timestamp safe zone [1600, 1920] x [920, 1080]
    lr_box = im.crop((1600, 920, 1920, 1080)).convert("L")
    assert lr_box.getextrema()[1] < 220

    # Also test 9:16 vertical key-art
    out_9x16 = tmp_path / "thumb_9x16.jpg"
    res_916 = ve.generate_depth_layered_thumbnail(hero, payload, out_9x16, aspect_ratio="9:16")
    assert res_916.exists()
    assert Image.open(res_916).size == (1080, 1920)


def test_cover_depth_sandwich_style(tmp_path):
    """Verify depth_sandwich is in COVER_STYLES and produces safe 9:16 and 1:1 crops."""
    assert "depth_sandwich" in COVER_STYLES
    hero = tmp_path / "hero_cover.jpg"
    Image.new("RGB", (1200, 1800), (85, 75, 65)).save(hero)
    copy = {
        "thumbnail": {
            "eyebrow": "UNDERGROUND REALM",
            "line1": "18 STORIES DOWN",
            "line2": "SOLID VOLCANIC ROCK",
            "tagline": "CARVED BY HAND",
        }
    }
    ve = VisualEngine()
    c916, c11 = ve.generate_thumbnails(hero, copy, tmp_path / "ds", style="depth_sandwich")
    assert c916.exists() and c11.exists()
    assert Image.open(c916).size == (1080, 1920)
    assert Image.open(c11).size == (1080, 1080)

    # Safe crop vertical bands
    im_l = Image.open(c916).convert("L")
    for band in ((0, 0, 1080, 400), (0, 1520, 1080, 1920)):
        assert im_l.crop(band).getextrema()[1] < 200


def test_adaptive_saliency_gating():
    """Verify adaptive saliency gating accurately extracts coverage on flat vs structured imagery."""
    # Flat image has 0.0 coverage
    flat = Image.new("RGB", (600, 800), (100, 100, 100))
    _, cov_flat = extract_foreground_mask(flat)
    assert cov_flat == 0.0

    # Synthetic image with a distinct centered pillar has valid coverage in [0.15, 0.60]
    import numpy as np
    arr = np.zeros((800, 600, 3), dtype=np.uint8) + 40
    arr[:, 200:380] = 200  # Bright pillar occupies 30% width
    img_pillar = Image.fromarray(arr)
    _, cov_pillar = extract_foreground_mask(img_pillar)
    assert 0.15 <= cov_pillar <= 0.60


def test_multi_template_carousel_semantic_routing():
    """Verify Semantic Intent Router accurately classifies cards into the 5 archetypes."""
    mfg = MultiFormatGenerator()

    # Index 0 is cover
    assert mfg.route_carousel_archetype({"heading": "x", "body": "y"}, 0, 7) == "cover"
    # Last index is debate
    assert mfg.route_carousel_archetype({"heading": "x", "body": "y"}, 6, 7) == "debate"

    # Colossal stat card
    stat_card = {"heading": "COLOSSAL CAPACITY", "body": "Room for 20,000 people deep underground."}
    assert mfg.route_carousel_archetype(stat_card, 1, 7) == "colossal_stat"

    # Comparison card
    comp_card = {"heading": "SURFACE VERSUS DEPTH", "body": "Compared to the surface, underground was safe."}
    assert mfg.route_carousel_archetype(comp_card, 2, 7) == "comparison"

    # Blueprint card
    bp_card = {"heading": "VENTILATION SHAFT", "body": "A 180-foot ventilation shaft supplied air to all levels."}
    assert mfg.route_carousel_archetype(bp_card, 3, 7) == "blueprint"


def test_multi_template_carousel_generation(tmp_path):
    """Verify generation of all 7 multi-template editorial carousel slides."""
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1080, 1350), (40, 50, 60)).save(hero)
    payload = {
        "carousel_slides": [
            {"heading": "DERINKUYU", "body": "An underground city in Cappadocia."},
            {"heading": "COLOSSAL METROPOLIS", "body": "Home to 20,000 people deep inside 18 stories."},
            {"heading": "ENGINEERED SHAFTS", "body": "A deep ventilation channel carved through rock."},
            {"heading": "SURFACE VS DEPTH", "body": "Surface life was dangerous compared to subterranean bunkers."},
            {"heading": "HEAVY STONE DOORS", "body": "Massive 1,000-pound rolling stone doors guarded entrances."},
            {"heading": "ANCIENT EXCAVATION", "body": "Carved centuries before modern excavation tools existed."},
            {"heading": "WHO BUILT IT?", "body": "Which ancient civilization created this impossible marvel?"},
        ],
        "poll": {"options": ["Phrygians", "Hittites"]},
    }
    mfg = MultiFormatGenerator()
    slides = mfg.generate_carousel_sequence([hero], payload, tmp_path)
    assert len(slides) == 7
    for slide in slides:
        assert slide.exists()
        assert Image.open(slide).size == (1080, 1350)


def test_procedural_graphics_catalog(tmp_path):
    """Verify that LiDAR scan, Stratigraphy, and Isometric Cutaway generate clips with poster frames."""
    # 1. LiDAR Scan
    lidar_out = tmp_path / "graphic_lidar.mp4"
    res_lidar = make_lidar_scan({"depth_ft": 280, "title": "TEST LIDAR SCAN"}, lidar_out, width=540, height=960)
    assert res_lidar is not None and res_lidar.exists()
    assert res_lidar.with_suffix(".jpg").exists()

    # 2. Geological Stratigraphy
    strat_out = tmp_path / "graphic_stratigraphy.mp4"
    res_strat = make_stratigraphy({"total_depth_ft": 280, "title": "TEST STRATIGRAPHY"}, strat_out, width=540, height=960)
    assert res_strat is not None and res_strat.exists()
    assert res_strat.with_suffix(".jpg").exists()

    # 3. 3D Isometric Cutaway
    iso_out = tmp_path / "graphic_isometric.mp4"
    res_iso = make_isometric_cutaway({"title": "TEST ISOMETRIC CUTAWAY"}, iso_out, width=540, height=960)
    assert res_iso is not None and res_iso.exists()
    assert res_iso.with_suffix(".jpg").exists()


def test_wcag_aaa_contrast_and_occlusion_auditing():
    """Verify WCAG relative luminance contrast formula and non-occlusion auditing."""
    auditor = QualityAuditor()

    # White on black is 21:1
    assert auditor.calculate_contrast_ratio((255, 255, 255), (0, 0, 0)) > 20.0
    # Black on black is 1:1
    assert auditor.calculate_contrast_ratio((0, 0, 0), (0, 0, 0)) == 1.0
    # Gold on charcoal passes WCAG AAA >= 4.5:1
    gold = (255, 215, 0)
    charcoal = (16, 18, 22)
    assert auditor.calculate_contrast_ratio(gold, charcoal) >= 4.5

    # Audit image crop contrast
    dark_im = Image.new("RGB", (200, 200), (12, 14, 18))
    res = auditor.audit_wcag_contrast(dark_im, (20, 20, 180, 80), text_color=(255, 255, 255), min_contrast=4.5)
    assert res["passed"] is True
    assert res["contrast_ratio"] >= 4.5

    # Audit non-occlusion
    occ_res = auditor.audit_subject_non_occlusion(dark_im, (20, 20, 180, 80))
    assert occ_res["passed"] is True


def test_measure_caption_wcag_contrast(tmp_path):
    from PIL import Image, ImageDraw
    from src.video_qa import measure_caption_wcag_contrast
    # Create synthetic frame with dark pill and white text
    frame = Image.new("RGB", (540, 960), (40, 40, 45))
    draw = ImageDraw.Draw(frame)
    # Draw dark backing pill at caption band
    draw.rounded_rectangle([90, 580, 450, 700], radius=16, fill=(10, 10, 12))
    # Draw bright white text
    draw.text((120, 620), "VERIFIED INSCRIPTION", fill=(255, 255, 255))
    fpath = tmp_path / "frame_contrast.jpg"
    frame.save(fpath)

    res = measure_caption_wcag_contrast(fpath, caption_band=(90, 550, 450, 750))
    assert res["wcag_aa_passed"] is True
    assert res["contrast_ratio"] >= 4.5

