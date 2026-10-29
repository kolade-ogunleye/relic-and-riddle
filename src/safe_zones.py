"""Where each platform draws its own interface over our pictures, and the box our text and figures must stay
inside for every deliverable (operator, 2026-10-24: "ensure our visual contents are within each social media
platform's safe UI"). One statement, read by the caption renderer, the graphics, the Story frames, the covers
and the automated video check, so the numbers cannot drift apart again.

Reference frame 1080x1920 (9:16). Figures and their sources, read 2026-10-24:

- Meta, Instagram and Facebook Stories and Reels: "leaving roughly 14% of the top, 35% of the bottom, and 6% on
  each side of your asset free from text, logos, or other key creative elements"
  (facebook.com/business/ads-guide/video/instagram-story; the Reels guides give the same figures). At 1920 px:
  269 top, 672 bottom, 65 each side. The profile icon and caption sit in the top band; the username, caption,
  audio row and call-to-action in the bottom one.
- YouTube Shorts: Google's vertical-video guidance draws a "Safe Zone" rectangle on a 1080x1920 frame
  (support.google.com/google-ads/answer/9128498); its measurements read 288 top, 672 bottom, 48 left, 192 right.
  The right column holds like, dislike, comments, share and remix; the bottom holds the channel, title and
  sound. Google's own caveat: overlays "may appear in different places depending on the format, campaign type,
  and screen", so this is the conservative box, not a guarantee.
- Instagram profile grid: 3:4 since January 2025 (the centre 1080x1440 of a 9:16 cover). The older 1:1 crop
  (centre 1080x1080) is kept as the stricter bound for cover text, as it already was.
- Carousel 4:5 (1440x1800 since 2026-10-28, laid out in 1080-wide design px): shown whole in both feeds; the
  profile grid shows its centre 3:4 (1013x1350 at 1080), so the 80 px side margins the slides keep are enough.

A Reel goes to all three Reel platforms, so its box is the intersection of their safe areas (the largest
inset on each side); a Story goes to the two Meta surfaces. The 16:9 documentary (YouTube long-form) keeps its
own broadcast title-safe bounds in graphics.get_text_bounds and is not covered here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

REFERENCE_WIDTH, REFERENCE_HEIGHT = 1080, 1920


@dataclass(frozen=True)
class Inset:
    """Pixels of each edge a platform may cover, at the reference 1080x1920 frame."""
    top: int
    bottom: int
    left: int
    right: int

    def box(self, width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> Tuple[int, int, int, int]:
        """(x0, y0, x1, y1) of the area left free, scaled to a frame of this size."""
        sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
        return (round(self.left * sx), round(self.top * sy), width - round(self.right * sx), height - round(self.bottom * sy))

    def centred_width(self, width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> int:
        """The widest text centred on the frame that stays inside the box."""
        x0, _, x1, _ = self.box(width, height)
        cx = width / 2
        return int(2 * min(cx - x0, x1 - cx))


PLATFORM_UI: Dict[str, Inset] = {
    "instagram_reels": Inset(top=269, bottom=672, left=65, right=65),
    "facebook_reels": Inset(top=269, bottom=672, left=65, right=65),
    "youtube_shorts": Inset(top=288, bottom=672, left=48, right=192),
    "instagram_story": Inset(top=269, bottom=672, left=65, right=65),
    "facebook_story": Inset(top=269, bottom=672, left=65, right=65),
}

DELIVERABLES: Dict[str, Tuple[str, ...]] = {
    "reel": ("instagram_reels", "facebook_reels", "youtube_shorts"),
    "story": ("instagram_story", "facebook_story"),
}

SOURCES = {
    "meta": "https://www.facebook.com/business/ads-guide/video/instagram-story",
    "youtube": "https://support.google.com/google-ads/answer/9128498",
}


def union(*platforms: str) -> Inset:
    """The area free on every one of these platforms: the largest inset on each side."""
    insets = [PLATFORM_UI[p] for p in platforms]
    return Inset(top=max(i.top for i in insets), bottom=max(i.bottom for i in insets),
                 left=max(i.left for i in insets), right=max(i.right for i in insets))


REEL = union(*DELIVERABLES["reel"])      # Inset(288, 672, 65, 192): x 65..888, y 288..1248 at 1080x1920
STORY = union(*DELIVERABLES["story"])    # Inset(269, 672, 65, 65):  x 65..1015, y 269..1248


def safe_box(kind: str, width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> Tuple[int, int, int, int]:
    """The box a deliverable's text and figures must stay inside ("reel" or "story")."""
    return {"reel": REEL, "story": STORY}[kind].box(width, height)


# Captions sit at the bottom of the Reel box: the pill's lowest edge on the box's bottom line. Everything
# above them (the graphics' text) stops this far above that line, which covers the tallest pill (the base
# font's measured height plus its padding, about 110 px) and a gap so a figure never reads as a caption.
CAPTION_BAND = 150
CAPTION_CLEARANCE = 8  # px between the pill's bottom edge and the platforms' bottom band


def caption_band_top(width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> int:
    """The highest y a caption pill reaches, at this frame size."""
    return safe_box("reel", width, height)[3] - round(CAPTION_BAND * height / REFERENCE_HEIGHT)


def graphics_box(width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT, gap: int = 40) -> Tuple[int, int, int, int]:
    """Where a 9:16 graphic may put text and figures: the Reel box with the caption band taken off its bottom."""
    x0, y0, x1, y1 = safe_box("reel", width, height)
    return x0, y0, x1, caption_band_top(width, height) - round(gap * height / REFERENCE_HEIGHT)


def cover_box(width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> Tuple[int, int, int, int]:
    """Where a 9:16 cover may put text: the centre square, which both profile-grid crops (1:1 and 3:4) keep."""
    side = min(width, height)
    return 0, (height - side) // 2, width, (height + side) // 2


def inside(box: Tuple[int, int, int, int], within: Tuple[int, int, int, int]) -> bool:
    return box[0] >= within[0] and box[1] >= within[1] and box[2] <= within[2] and box[3] <= within[3]
