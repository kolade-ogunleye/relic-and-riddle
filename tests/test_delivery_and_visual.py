"""
tests/test_delivery_and_visual.py
=================================
Unit tests for delivery profiles, widescreen YouTube thumbnail generation,
and stereo audio chain formatting.
"""

from PIL import Image

from src.delivery_profiles import get_delivery_profile
from src.visual_engine import VisualEngine
from src.video_composer import _audio_chain, _master_chain, VideoComposer


def test_delivery_profiles_contracts():
    deep_dive = get_delivery_profile("deep_dive_16x9")
    assert deep_dive.width == 1920
    assert deep_dive.height == 1080
    assert deep_dive.audio_channels == 2
    assert deep_dive.target_lufs == -14.0
    assert deep_dive.requires_youtube_thumbnail is True
    assert deep_dive.subtitle_style == "documentary_clean"

    reel = get_delivery_profile("reel_9x16")
    assert reel.width == 1080
    assert reel.height == 1920
    assert reel.audio_channels == 2
    assert reel.loop_seamless is False
    assert reel.subtitle_style == "kinetic_pill"


def test_youtube_thumbnail_generation(tmp_path):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (80, 70, 60)).save(hero)
    payload = {
        "title": "Derinkuyu Underground City",
        "thumbnail": {
            "eyebrow": "DOCUMENTARY INVESTIGATION",
            "line1": "DERINKUYU",
            "line2": "ANCIENT METROPOLIS",
            "tagline": "280 FT UNDERGROUND",
        }
    }
    out_thumb = tmp_path / "yt_thumb.jpg"
    res = VisualEngine().generate_youtube_thumbnail(hero, payload, out_thumb)
    assert res.exists()
    im = Image.open(res)
    assert im.size == (1920, 1080)
    # Ensure lower right quadrant (YouTube timestamp zone) is clear of bright text overlays
    # Specifically band x in [1600, 1920], y in [920, 1080]
    lr_box = im.crop((1600, 920, 1920, 1080)).convert("L")
    assert lr_box.getextrema()[1] < 220


def test_audio_chain_stereo_preservation():
    chain = _audio_chain(0, 1)
    # Check that both inputs are explicitly formatted as stereo
    assert "aformat=channel_layouts=stereo" in chain
    assert "highpass=f=85" in chain
    assert "equalizer=f=3500" in chain
    assert "equalizer=f=7000" in chain
    # Check that master chain does not brickwall transients
    master = _master_chain(0.0)
    assert "alimiter=limit=0.80" in master
    assert "attack=2.5:release=80" in master


def test_export_srt_and_vtt(tmp_path):
    words = [
        {"word": "Derinkuyu", "start": 0.5, "dur": 0.4},
        {"word": "is", "start": 0.9, "dur": 0.2},
        {"word": "ancient.", "start": 1.1, "dur": 0.5},
        {"word": "It", "start": 1.7, "dur": 0.2},
        {"word": "descends", "start": 1.9, "dur": 0.4},
        {"word": "deep.", "start": 2.3, "dur": 0.3},
    ]
    composer = VideoComposer()
    srt_file = tmp_path / "test.srt"
    vtt_file = tmp_path / "test.vtt"

    composer.export_subtitles_srt(words, srt_file)
    composer.export_subtitles_vtt(words, vtt_file)

    assert srt_file.exists()
    assert vtt_file.exists()

    srt_content = srt_file.read_text(encoding="utf-8")
    assert "00:00:00,500 --> " in srt_content
    assert "Derinkuyu is ancient." in srt_content

    vtt_content = vtt_file.read_text(encoding="utf-8")
    assert "WEBVTT" in vtt_content
    assert "00:00:00.500 --> " in vtt_content


def test_video_composer_post_processing_pipeline_format_guards():
    """Verify that video_composer encloses film_grade within format=yuv420p to prevent

    planar RGB (gbrp) SIMD vector width alignment truncation (8px right black bar bug).
    """
    import inspect
    from src.video_composer import VideoComposer

    source = inspect.getsource(VideoComposer.compose_video)
    # Ensure format=yuv420p precedes and succeeds film_grade
    assert "[video_raw]format=yuv420p,{film_grade},format=yuv420p," in source




def test_documentary_cover_copy_comes_from_the_topic_when_the_deep_dive_gave_none():
    from src.visual_engine import documentary_cover_copy
    copy = documentary_cover_copy({"title": "Greek Fire and the Byzantine Siphon", "sub_theme": "Lost Technology"})
    assert copy == {"eyebrow": "DOCUMENTARY INVESTIGATION", "line1": "GREEK FIRE AND THE", "line2": "BYZANTINE SIPHON", "tagline": "LOST TECHNOLOGY"}
    short = documentary_cover_copy({"title": "Roman Concrete", "sub_theme": "Lost Technology"})
    assert (short["line1"], short["line2"], short["tagline"]) == ("ROMAN CONCRETE", "LOST TECHNOLOGY", "WHAT THE EVIDENCE SHOWS")
    given = documentary_cover_copy({"title": "Roman Concrete", "thumbnail": {"line1": "Self-healing", "line2": "Roman concrete", "tagline": "2,000 years on"}})
    assert (given["line1"], given["line2"], given["tagline"]) == ("SELF-HEALING", "ROMAN CONCRETE", "2,000 YEARS ON")
    for payload in ({"title": "Derinkuyu Underground City"}, {"title": "Greek Fire"}, {}):
        text = " ".join(documentary_cover_copy(payload).values())
        assert "METROPOLIS" not in text and "UNEXPLAINED" not in text
