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

def test_image_gen_modern_scientific_override(tmp_path):
    """Verifies that modern scientific lines route to technical blueprint prompts rather than antiquity etchings."""
    # Isolation (2026-11-01): without a GEMINI_API_KEY in the environment is_available() is False and
    # synthesize_gap_asset returns before prompting, so this only passed where a real key was configured.
    with patch("src.image_gen.is_available", return_value=True), \
         patch("src.image_gen.generate_raw_linework") as mock_gen, \
         patch("src.substrate_compositor.composite_linework_onto_substrate") as mock_comp:
        
        mock_gen.return_value = b"fake_jpeg_bytes"
        mock_comp.return_value = Image.new("RGB", (100, 100))
        
        topic = {"title": "The Lycurgus Cup", "era": "classical_antiquity"}
        work_dir = tmp_path / "tmp_test_dir"  # was the repository root's tmp_test_dir

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


# ---------------------------------------------------------------------------
# 11. Parallax Bypass & 2D Ken Burns Optical Motion
# ---------------------------------------------------------------------------

def test_parallax_bypass_for_gap_art_and_macro_drift(tmp_path):
    """Verifies that render_scenes passes gap_gen_ reconstructions and macro drift shots through without warping."""
    from src import parallax
    
    reconstruction = tmp_path / "gap_gen_beat_2.jpg"
    reconstruction.write_bytes(b"\xff\xd8\xff\xe0mock_recon")
    hero_photo = tmp_path / "hero_photo.jpg"
    hero_photo.write_bytes(b"\xff\xd8\xff\xe0mock_hero")
    held_photo = tmp_path / "hero_photo_held.jpg"
    held_photo.write_bytes(b"\xff\xd8\xff\xe0mock_held")
    
    scenes = [hero_photo, reconstruction, held_photo]
    durations = [3.0, 4.0, 3.5]
    exempt_indices = [2]  # Index 2 is a held macro drift
    
    with patch("src.parallax.available", return_value=True), \
         patch("src.parallax._session"), \
         patch("src.checkpoints.file_inputs", return_value={"dummy": "dummy_hash"}), \
         patch("src.checkpoints.Checkpoints.reusable", return_value=False), \
         patch("src.checkpoints.Checkpoints.complete"), \
         patch("src.parallax.render_clip") as mock_render_clip:
        
        mock_render_clip.side_effect = lambda img, out, dur, preset, variant=0: out
        
        out_scenes = parallax.render_scenes(
            scenes=scenes,
            durations=durations,
            work_dir=tmp_path / "parallax",
            transition=0.5,
            exempt_indices=exempt_indices
        )
        
        # Only hero_photo (index 0) should have been rendered as a 3D parallax clip
        assert mock_render_clip.call_count == 1
        call_image = mock_render_clip.call_args[0][0]
        assert call_image == hero_photo
        
        # Output scenes must preserve index 1 (reconstruction) and index 2 (macro drift) as original stills
        assert out_scenes[1] == reconstruction
        assert out_scenes[2] == held_photo


# ---------------------------------------------------------------------------
# 12. ReconBadge Upper-Third Geometry & Lineage Derivation
# ---------------------------------------------------------------------------

def test_recon_badge_upper_third_and_lineage_derivation(tmp_path):
    """Verifies that ReconBadge renders at upper-third (\an8, MarginV: 310) and detects derived clips via lineage."""
    composer = VideoComposer(tmp_path)
    ass_file = tmp_path / "test_recon_captions.ass"
    words = [
        {"word": "Ancient", "start": 0.0, "end": 1.0},
        {"word": "craft.", "start": 1.0, "end": 2.0}
    ]
    
    composer.build_subtitles(
        words,
        ass_file,
        reconstruction_intervals=[(0.0, 2.0)],
        width=1080,
        height=1920
    )
    
    content = ass_file.read_text(encoding="utf-8")
    # The badge sits top-left on a plate inside the Meta inset (x 84, y 300) at 30px; it was a 22px centred
    # upper-third label (\an8, MarginV 310) until the operator approved the move (caption review, 2026-11-02).
    assert "Style: ReconBadge" in content
    assert "Style: ReconPlate" in content
    assert r"\an7\pos(98," in content and "}[ HISTORICAL RECONSTRUCTION ]" in content
    assert r"{\pos(84,300)\p1}" in content


# ---------------------------------------------------------------------------
# 13. Gap Asset Cache Reuse & Keyword-Driven Modality Fallback
# ---------------------------------------------------------------------------

def test_gap_asset_cache_reuse_and_keyword_override(tmp_path):
    """Verifies that synthesize_gap_asset reuses existing files without calling the API,
    and automatically detects modern scientific keywords even if modality is omitted."""
    from src import image_gen
    
    # 1. Existing file reuse
    existing = tmp_path / "gap_gen_beat_5.jpg"
    existing.write_bytes(b"\xff\xd8\xff\xe0existing_jpeg_data")
    
    with patch("src.image_gen.is_available", return_value=True), \
         patch("src.image_gen.generate_raw_linework") as mock_gen:
        
        result = image_gen.synthesize_gap_asset(
            beat_index=5,
            line_text="ancient bronze gears were cast",
            era="classical_antiquity",
            topic={"title": "Antikythera"},
            work_dir=tmp_path
        )
        assert result == existing
        assert mock_gen.call_count == 0  # Zero API spend on reuse!

    # 2. Keyword-driven modern scientific detection
    work_dir2 = tmp_path / "work2"
    with patch("src.image_gen.is_available", return_value=True), \
         patch("src.image_gen.generate_raw_linework", return_value=b"\xff\xd8\xff\xe0raw") as mock_gen, \
         patch("src.substrate_compositor.composite_linework_onto_substrate"):
        
        image_gen.synthesize_gap_asset(
            beat_index=2,
            line_text="modern x-ray tomography scans reveal hidden gear teeth",
            era="classical_antiquity",
            topic={"title": "Antikythera"},
            work_dir=work_dir2,
            modality="historical_scene"  # Intentionally omitted / default
        )
        assert mock_gen.call_count == 1
        prompt_used = mock_gen.call_args[0][0]
        # Must have auto-switched to technical blueprint despite era="classical_antiquity"
        assert "technical engineering schematic" in prompt_used



