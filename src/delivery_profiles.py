"""
src/delivery_profiles.py
========================
Formal delivery profiles replacing scattered ad-hoc assumptions across platforms.
Defines explicit contracts for aspect ratio, duration, audio targets, visual layout,
safe zones, packaging assets, and platform compliance.
"""

from typing import Dict, List, Literal
from pydantic import BaseModel

from .config import RELEASE_DURATION_MAX, TARGET_DURATION_MIN


class DeliveryProfile(BaseModel):
    name: str
    aspect_ratio: str
    width: int
    height: int
    duration_min: float
    duration_max: float
    target_lufs: float = -14.0
    target_true_peak: float = -1.5
    audio_channels: int = 2
    audio_sample_rate: int = 48000
    loop_seamless: bool = False
    subtitle_style: Literal["documentary_clean", "kinetic_pill", "none"] = "kinetic_pill"
    requires_youtube_thumbnail: bool = False
    requires_provenance: bool = True
    platforms: List[str]


PROFILES: Dict[str, DeliveryProfile] = {
    "deep_dive_16x9": DeliveryProfile(
        name="deep_dive_16x9",
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        duration_min=180.0,
        duration_max=360.0,
        target_lufs=-14.0,
        target_true_peak=-1.5,
        audio_channels=2,
        loop_seamless=False,
        subtitle_style="documentary_clean",
        requires_youtube_thumbnail=True,
        requires_provenance=True,
        platforms=["youtube", "facebook_in_stream", "facebook_watch"],
    ),
    "reel_9x16": DeliveryProfile(
        name="reel_9x16",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        duration_min=TARGET_DURATION_MIN,
        duration_max=RELEASE_DURATION_MAX,
        target_lufs=-14.0,
        target_true_peak=-1.5,
        audio_channels=2,
        loop_seamless=False,
        subtitle_style="kinetic_pill",
        requires_youtube_thumbnail=False,
        requires_provenance=True,
        platforms=["meta_reels_facebook", "meta_reels_instagram", "youtube_shorts"],
    ),
    "carousel_instagram": DeliveryProfile(
        name="carousel_instagram",
        aspect_ratio="1:1",
        width=1080,
        height=1080,
        duration_min=0.0,
        duration_max=0.0,
        loop_seamless=False,
        subtitle_style="none",
        requires_youtube_thumbnail=False,
        requires_provenance=True,
        platforms=["instagram_carousel", "facebook_post"],
    ),
    "story_vertical": DeliveryProfile(
        name="story_vertical",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        duration_min=0.0,
        duration_max=0.0,
        loop_seamless=False,
        subtitle_style="none",
        requires_youtube_thumbnail=False,
        requires_provenance=True,
        platforms=["instagram_story", "facebook_story"],
    ),
}

# A distinct editorial length, rather than padding the standard Reel to imply
# rewards eligibility. Account eligibility and actual publishing support are separate.
PROFILES["vertical_extended"] = PROFILES["reel_9x16"].model_copy(update={
    "name": "vertical_extended", "duration_min": 62.0, "duration_max": 68.0,
    "platforms": ["tiktok"], "loop_seamless": False,
})


def get_delivery_profile(name: str) -> DeliveryProfile:
    """Retrieve delivery profile by name or alias."""
    alias_map = {
        "deep-dive": "deep_dive_16x9",
        "deep_dive": "deep_dive_16x9",
        "documentary": "deep_dive_16x9",
        "reel": "reel_9x16",
        "short_form": "reel_9x16",
        "short-form": "reel_9x16",
    }
    canonical = alias_map.get(name.lower(), name)
    if canonical not in PROFILES:
        raise KeyError(f"Unknown delivery profile '{name}'. Available: {list(PROFILES.keys())}")
    return PROFILES[canonical]
