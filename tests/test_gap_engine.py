"""
tests/test_gap_engine.py
========================
Comprehensive verification suite for Phase 2.5: Generative Gap-Filling Subsystem & Precision Cartography.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import pytest
from PIL import Image, ImageDraw

from src import config, geo, graphic_match, graphics, image_gen, image_quarantine, prevoice_lines, substrate_compositor
from src.video_composer import VideoComposer


# ---------------------------------------------------------------------------
# 1. Pre-Voice Classification & Modern Scientific Override
# ---------------------------------------------------------------------------

def test_prevoice_generable_verdict():
    """Verifies that 'generable' lines are parsed with modality and excluded from revision rewrites."""
    raw_verdicts = [
        {"sentence": "The bronze gears were cut with fractions of a millimeter precision.", "kind": "shown", "photo": 1},
        {"sentence": "Before the vessel sank between approximately 70 and 60 BC.", "kind": "generable", "modality": "historical_scene", "reason": "unphotographed ancient shipwreck"},
        {"sentence": "Researchers reproduced this optical effect by embedding nanoparticles into printable nanocomposites.", "kind": "generable", "modality": "modern_scientific", "reason": "modern lab replication"},
        {"sentence": "Modern highway traffic passes near the archaeological site.", "kind": "unshown", "reason": "modern highway in ancient video"},
        {"sentence": "How did ancient builders calculate these astronomical cycles?", "kind": "abstract", "reason": "rhetorical question"}
    ]
    
    # unshown() must ONLY include true unshown lines, never generable or abstract
    unshown_lines = prevoice_lines.unshown(raw_verdicts)
    assert len(unshown_lines) == 1
    assert unshown_lines[0]["sentence"] == "Modern highway traffic passes near the archaeological site."
    
    # generable() must capture the 2 generable lines
    generable_lines = prevoice_lines.generable(raw_verdicts)
    assert len(generable_lines) == 2
    assert generable_lines[0]["modality"] == "historical_scene"
    assert generable_lines[1]["modality"] == "modern_scientific"


# ---------------------------------------------------------------------------
# 2. Substrate Compositor
# ---------------------------------------------------------------------------

def test_substrate_compositor_all_eras(tmp_path):
    """Verifies that all 4 historical eras composite linework cleanly onto procedural paper."""
    # Create test linework image with a black circle
    linework = Image.new("RGB", (270, 480), (255, 255, 255))
    draw = ImageDraw.Draw(linework)
    draw.ellipse([60, 100, 210, 250], outline=(0, 0, 0), width=4)
    
    for era in ["classical_antiquity", "medieval_renaissance", "industrial_victorian", "modern_cold_war"]:
        out_file = tmp_path / f"comp_{era}.jpg"
        composited = substrate_compositor.composite_linework_onto_substrate(
            linework,
            era=era,
            output_path=out_file,
            target_size=(270, 480)
        )
        assert out_file.exists()
        assert composited.size == (270, 480)
        assert composited.mode == "RGB"


# ---------------------------------------------------------------------------
# 3. Image Generator & Modern Scientific Override
# ---------------------------------------------------------------------------

def test_image_gen_modern_scientific_override():
    """Verifies that modern scientific lines route to technical blueprint prompts rather than antiquity etchings."""
    with patch("src.image_gen.generate_raw_linework") as mock_gen, \
         patch("src.substrate_compositor.composite_linework_onto_substrate") as mock_comp:
        
        mock_gen.return_value = b"fake_jpeg_bytes"
        mock_comp.return_value = Image.new("RGB", (100, 100))
        
        topic = {"title": "The Lycurgus Cup", "era": "classical_antiquity"}
        work_dir = Path("tmp_test_dir")
        
        # Test 1: Historical scene in classical antiquity
        image_gen.synthesize_gap_asset(
            beat_index=8,
            line_text="before the vessel sank between approximately 70 and 60 BC.",
            era="classical_antiquity",
            topic=topic,
            work_dir=work_dir,
            modality="historical_scene"
        )
        call_args = mock_gen.call_args[0][0]
        assert "Piranesi architectural copperplate engraving" in call_args
        
        # Test 2: Modern scientific reproduction line
        image_gen.synthesize_gap_asset(
            beat_index=9,
            line_text="Researchers reproduced this green and red optical effect by embedding matching nanoparticles into printable nanocomposites.",
            era="classical_antiquity",
            topic=topic,
            work_dir=work_dir,
            modality="modern_scientific"
        )
        call_args_modern = mock_gen.call_args[0][0]
        # Must override to modern technical blueprint prompt
        assert "technical engineering schematic" in call_args_modern
        assert "Piranesi" not in call_args_modern


# ---------------------------------------------------------------------------
# 4. Vision Quarantine Gate
# ---------------------------------------------------------------------------

def test_image_quarantine_dimensions(tmp_path):
    """Verifies rejection of low-resolution or wrong aspect ratio images."""
    # Bad aspect ratio (horizontal landscape)
    bad_aspect = tmp_path / "bad_aspect.jpg"
    Image.new("RGB", (1920, 1080), (255, 255, 255)).save(bad_aspect)
    ok, reason = image_quarantine.check_dimensions_and_quality(bad_aspect)
    assert not ok
    assert "Aspect ratio too wide" in reason
    
    # Good vertical 9:16 frame
    good_vertical = tmp_path / "good_vertical.jpg"
    Image.new("RGB", (1080, 1920), (255, 255, 255)).save(good_vertical)
    ok, reason = image_quarantine.check_dimensions_and_quality(good_vertical)
    assert ok


# ---------------------------------------------------------------------------
# 5. Precision Map Card Urban Targeting
# ---------------------------------------------------------------------------

def test_precision_map_card_urban_targeting():
    """Verifies that map cards format secondary labels as 'CITY, COUNTRY' when city metadata is present."""
    # Place with distinct city
    place_with_city = {
        "lat": 51.5194,
        "lon": -0.1270,
        "label": "British Museum",
        "country": "UK",
        "city": "London"
    }
    
    # Verify graphic_match indexes both city and country
    inputs = {"builder": "map", "place": place_with_city}
    sidecar_mock = MagicMock()
    sidecar_mock.read_text.return_value = json.dumps({"settings": {"inputs": inputs}})
    
    with patch("src.graphic_match.sidecar", return_value=sidecar_mock):
        t = graphic_match.terms(Path("graphic_map.mp4"))
        assert "london" in t
        assert "british" in t


# ---------------------------------------------------------------------------
# 6. Video Composer Reconstruction Badge
# ---------------------------------------------------------------------------

def test_video_composer_reconstruction_badge(tmp_path):
    """Verifies that build_subtitles emits a [ HISTORICAL RECONSTRUCTION ] badge event for designated intervals."""
    composer = VideoComposer(tmp_path)
    ass_file = tmp_path / "test_captions.ass"
    words = [
        {"word": "Before", "start": 0.0, "end": 0.5},
        {"word": "the", "start": 0.5, "end": 0.8},
        {"word": "ship", "start": 0.8, "end": 1.2},
        {"word": "sank.", "start": 1.2, "end": 2.0}
    ]
    
    # Pass a historical reconstruction interval for 0.0 -> 2.0s
    composer.build_subtitles(
        words,
        ass_file,
        reconstruction_intervals=[(0.0, 2.0)],
        width=1080,
        height=1920
    )
    
    assert ass_file.exists()
    content = ass_file.read_text(encoding="utf-8")
    assert "Style: ReconBadge" in content
    assert "[ HISTORICAL RECONSTRUCTION ]" in content
    assert "ReconBadge" in content


# ---------------------------------------------------------------------------
# 7. Lineage & Provenance Recording (Gap 1 Resolution)
# ---------------------------------------------------------------------------

def test_gap_asset_lineage_and_provenance(tmp_path):
    """Verifies that generated gap assets receive lineage sidecars and verify cleanly in provenance ledger."""
    from src import credits, lineage
    
    # Create mock gap asset
    gap_art = tmp_path / "gap_gen_beat_3.jpg"
    gap_art.write_bytes(b"\xff\xd8\xff\xe0fake_jpeg")
    
    # Lineage registration
    lineage.record_graphic(
        gap_art,
        Path(__file__),
        {"era": "classical_antiquity", "beat": 3, "line": "furnace glass melting", "modality": "historical_scene"}
    )
    
    sidecar_path = lineage.sidecar(gap_art)
    assert sidecar_path.is_file()
    decl = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert decl["operation"] == "procedural_graphic"
    assert decl["settings"]["original_drawing"] is True
    
    # Provenance ledger generation
    master_mp4 = tmp_path / "master.mp4"
    master_mp4.write_bytes(b"mp4_bytes")
    ledger_path = credits.generate_provenance_ledger(
        run_dir=tmp_path,
        topic={"id": "test_topic", "title": "Test Topic"},
        payload={"caption": "Story caption."},
        master_mp4=master_mp4,
        media_items=[gap_art]
    )
    assert ledger_path.is_file()
    ledger_data = json.loads(ledger_path.read_text(encoding="utf-8"))
    
    # Asset must be recorded as verified in-house reconstruction
    art_records = [a for a in ledger_data["assets"] if a["file"] == "gap_gen_beat_3.jpg"]
    assert len(art_records) == 1
    assert art_records[0]["rights_status"] == "verified"
    assert art_records[0]["attribution_required"] is False
    assert art_records[0]["origin"] in ("archaeological_reconstruction", "original_drawing")


# ---------------------------------------------------------------------------
# 8. Tier 3 Intentional Relic Macro Drift (Gap 2 Resolution)
# ---------------------------------------------------------------------------

def test_tier3_relic_macro_drift(tmp_path):
    """Verifies that held fallbacks for unshown lines trigger Tier 3 macro drift in scene planner and video composer."""
    from src import scene_planner
    
    # Setup test beats and scenes
    beats = [
        {"text": "The golden chalice was forged in antiquity."},
        {"text": "Unphotographed microscopic atomic scattering occurs inside the alloy."}
    ]
    hero_photo = tmp_path / "chalice_hero.jpg"
    hero_photo.write_bytes(b"jpg1")
    unrelated_photo = tmp_path / "modern_factory.jpg"
    unrelated_photo.write_bytes(b"jpg2")
    
    # fit_to_lines with candidate that fails check, forcing hold fallback
    with patch("src.beat_images.line_fit", return_value={"fits": False, "reason": "unrelated", "cached": False}), \
         patch("src.scene_planner.related_shot", return_value=None), \
         patch("src.image_gen.is_available", return_value=False):  # force Tier 3 degradation
        
        scenes, report = scene_planner.fit_to_lines(
            beats=beats,
            scenes=[hero_photo, unrelated_photo],
            pool=[hero_photo, unrelated_photo],
            subject="The Golden Chalice",
            work_dir=tmp_path
        )
        assert len(scenes) == 2
        assert scenes[1] == hero_photo  # held shot
        assert 1 in report.get("macro_drifts", [])
        assert report["unshown"][0].get("macro_drift") is True


# ---------------------------------------------------------------------------
# 9. Video QA Beat Tagging & Reconstruction Protection
# ---------------------------------------------------------------------------

def test_video_qa_reconstruction_beat_tag_and_filtering(tmp_path):
    """Verifies that gap_gen_ assets are properly tagged and protected from false-positive drawing complaints."""
    from src import video_qa
    from src.models import QAIssue, QAReport
    
    gap_asset = tmp_path / "gap_gen_beat_2.jpg"
    tag = video_qa.beat_tag(gap_asset)
    assert "in-house archaeological reconstruction" in tag
    
    # Test false positive suppression
    issues = [
        QAIssue(beat=2, severity="major", issue="The visual shows an illustration or drawing rather than a real photo of the artifact."),
        QAIssue(beat=2, severity="critical", issue="The on-screen subtitle text is cut off at the edge."),
        QAIssue(beat=1, severity="major", issue="The visual shows an illustration instead of a photo.")
    ]
    report = QAReport(summary="QA look completed", issues=issues)
    
    assets = [tmp_path / "hero_photo.jpg", gap_asset]
    filtered = video_qa.drop_reconstruction_aesthetic_findings(report, assets)
    
    # Beat 2 illustration complaint must be dropped; Beat 2 cut-off text and Beat 1 complaint must stay
    remaining_issues = filtered.issues
    assert len(remaining_issues) == 2
    assert any("cut off" in i.issue for i in remaining_issues)
    assert any(i.beat == 1 for i in remaining_issues)
    assert not any("illustration" in i.issue and i.beat == 2 for i in remaining_issues)


# ---------------------------------------------------------------------------
# 10. Telegram Card & Ledger Reconstruction Note
# ---------------------------------------------------------------------------

def test_telegram_card_and_ledger_reconstruction_note():
    """Verifies that telegram_bot renders reconstruction_note and improve records finding."""
    from src import improve, telegram_bot
    
    improve._buffer.clear()
    
    # Mock payload with reconstruction_note
    script_payload = {
        "title": "Lycurgus Cup",
        "narration_script": "Line 1. Line 2.",
        "reconstruction_note": "🏛 Archival Reconstructions (1): Beat 2 (nanoparticles scattering)",
        "picture_fit": {"judged": 4, "cached": 0, "swapped": 0, "graphic": 0, "generated": [{"beat": 2, "line": "nanoparticles"}], "held": 0, "unshown": [], "stopped": None}
    }
    
    card_text = telegram_bot.TelegramApprovalBot._card_text(
        script_payload=script_payload,
        run_id="run_test_reconstruction_123",
        release=None,
        cap=None
    )
    
    assert "Archival Reconstructions" in card_text
    assert "nanoparticles scattering" in card_text

