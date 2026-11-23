"""
src/visual_engine.py
====================
Scene sequencing and the delivered cover engine.
1. Resolves topic-specific scene media (images, plus an optional motion hook clip).
   A topic without its own media fails fast instead of borrowing another topic's imagery.
2. Renders the final cover with bold display type in two formats:
   - 1080x1920 (9:16 Reel cover)
   - 1080x1080 (1:1 Instagram profile-grid crop)
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PIL import Image, ImageOps

from .config import (
    ASSETS_DIR,
    MIN_SCENE_IMAGES,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
)
from .credits import CONTACT_SHEET, CREDITS_FILE, load_credits
from .typography import is_video, spoken_units

log = logging.getLogger(__name__)

# Brand tokens (src/brand.py, visual overhaul 2026-10-29): the one gold and the bone text every surface shares.
from . import brand as _brand
GOLD = _brand.rgba(_brand.GOLD)
WHITE = _brand.rgba(_brand.BONE)
# The styles a Reel cover can go out in (cover_selector.fitting; selection is fixed to one since QF-82). lower_third
# left the rotation on 2026-11-01 (it differed from Discovery only in its headline colour); "monument" took its place.
# Every style ever posted stays drawable, so a kept recipe can always be redrawn (RENDERABLE_STYLES).
COVER_STYLES = ("badge", "monument", "minimal", "depth_sandwich")
RENDERABLE_STYLES = COVER_STYLES + ("lower_third",)

class MissingAssetsError(FileNotFoundError):
    pass


def cover_text_zone() -> Tuple[int, int, int, int]:
    """Where a 9:16 cover's text and badges stay: the centre square both profile-grid crops keep
    (safe_zones.cover_box), less 60 px at the sides and 20 px at the top and bottom."""
    from .safe_zones import cover_box
    x0, y0, x1, y1 = cover_box(VIDEO_WIDTH, VIDEO_HEIGHT)
    return x0 + 60, y0 + 20, x1 - 60, y1 - 20


def grid_crop(cover: Image.Image) -> Image.Image:
    """The 1:1 profile-grid copy of a 9:16 cover: its centre square."""
    top = max(0, min(VIDEO_HEIGHT - VIDEO_WIDTH, VIDEO_HEIGHT // 2 - VIDEO_WIDTH // 2))
    return cover.crop((0, top, VIDEO_WIDTH, top + VIDEO_WIDTH))


# Lane B cycle 3 (QF-101 closed as "no keep_rgb", lander decision under the operator's delegation, 2026-11-19): every
# cover JPEG (Reel cover, profile grid tile, YouTube thumbnail) is a standard YCbCr JPEG with NO chroma subsampling
# (4:4:4, Pillow subsampling=0), at the highest quality on COVER_QUALITIES that keeps the file within its platform's
# limit. 4:2:0 halved the chroma of the gold type and the dimmed hero (decisions/QF-13.md); an RGB-transform JPEG is
# not used. YouTube's custom thumbnail limit is 2 MiB (decisions/QF-13.md). The Reel cover and grid tile go to
# Instagram and Facebook; the smaller verified limit is Instagram's (read 2026-11-19), "File size: 8MB maximum" for a
# Reels cover photo and "8 MB maximum" for an image post, https://developers.facebook.com/docs/instagram-platform/
# instagram-graph-api/reference/ig-user/media ; Facebook: "Files can not exceed 10MB" (https://developers.facebook.com/
# docs/graph-api/reference/page/photos/) and "Maximum thumbnail file size 10MB" for a reel cover
# (https://developers.facebook.com/docs/graph-api/reference/video/thumbnails). "MB" is read as 8,000,000 bytes (the
# stricter reading).
COVER_SUBSAMPLING = 0                    # 4:4:4
COVER_QUALITIES = tuple(range(100, 49, -5))   # highest first; never under 50
COVER_LIMITS = {"youtube": 2 * 1024 * 1024, "reel": 8_000_000, "grid": 8_000_000}


def save_cover_jpeg(im: Image.Image, path: Path, surface: str) -> Dict[str, Any]:
    """`im` written to `path` as a 4:4:4 YCbCr JPEG at the highest COVER_QUALITIES step whose file is within
    COVER_LIMITS[surface]; returns the record {"quality", "subsampling": "4:4:4", "bytes", "limit"} (the cover's
    metadata keeps it). A cover no step fits raises ValueError (never a subsampled or oversized file)."""
    import io
    rgb = im.convert("RGB")
    limit = COVER_LIMITS[surface]
    for q in COVER_QUALITIES:
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=q, subsampling=COVER_SUBSAMPLING)
        if buf.tell() <= limit:
            Path(path).write_bytes(buf.getvalue())
            return {"quality": q, "subsampling": "4:4:4", "bytes": buf.tell(), "limit": limit}
    raise ValueError(f"the {surface} cover does not fit {limit} bytes as a 4:4:4 JPEG at quality "
                     f"{COVER_QUALITIES[-1]}")


def redraw_cover(path: Path, recipe: Dict[str, Any]) -> Dict[str, Any]:
    """Draws a cover again with the tighter fit, from its kept recipe, in place (still_vision: the look
    reported its text cut off or overlapping); the profile-grid copy is cut again from the main cover."""
    from . import still_layout
    engine = VisualEngine()
    engine._text_cy = int(recipe.get("text_cy") or VIDEO_HEIGHT // 2)
    with Image.open(recipe["hero"]) as source:
        engine._hero_source = ImageOps.exif_transpose(source).convert("RGBA")
    from .image_presentation import full_image
    engine._hero_source.info["essential_full_image"] = full_image(Path(recipe["hero"]))
    from .cover_design import reviewed_focus
    from .photo_hygiene import clean_hero
    engine._hero_focus_reviewed = reviewed_focus(Path(recipe["hero"]), recipe.get("focus"))
    engine._hero_source, engine._hero_focus = clean_hero(engine._hero_source, engine._hero_focus_reviewed)
    im = ImageOps.fit(engine._hero_source, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
    render = {"lower_third": engine._cover_lower_third, "minimal": engine._cover_minimal,
              "depth_sandwich": engine._cover_depth_sandwich,
              "monument": engine._cover_monument}.get(recipe.get("style"), engine._cover_badge)
    lay = still_layout.Layout("cover", (VIDEO_WIDTH, VIDEO_HEIGHT), cover_text_zone(), tight=True)
    engine._figure = recipe.get("figure")
    engine._illustration = bool(recipe.get("illustration"))
    engine._reveal = recipe.get("reveal")
    out = render(im, dict(recipe.get("copy") or {}), lay=lay, tight=True).convert("RGB")
    path = Path(path)
    # Written aside, then swapped in: the video judge may be reading this cover while the audit redraws it
    # (review, 2026-10-28)
    tmp = path.with_name(path.stem + ".redraw" + path.suffix)
    encoding = save_cover_jpeg(out, tmp, "reel")
    import os
    os.replace(tmp, path)
    from .cover_design import delivered_contrast
    delivered_contrast(lay, path)
    if path.name == "thumbnail_cover.jpg":
        save_cover_jpeg(grid_crop(out), path.with_name("thumbnail_profile_grid.jpg"), "grid")
    prior = still_layout.load(path) or {}
    return still_layout.save(path, lay, {**recipe, "focus": engine._hero_focus_reviewed, "encoding": encoding,
                                        "composition": lay.meta.get("composition"),
                                        "grid_proof": lay.meta.get("grid_proof")},
                             int(prior.get("attempts", 1)) + 1)


def calm_center_y(im: Image.Image, candidates=(760, 960, 1160), band: int = 440) -> int:
    """Where to centre a title so it covers the least eye-catching, least busy part of the photo:
    spectral-residual saliency (Hou & Zhang, 2007) plus edge density, on a small greyscale copy.
    Candidates stay inside the centre square that the profile grid keeps; ties favour the middle."""
    import cv2
    import numpy as np
    small = np.asarray(im.convert("L").resize((128, 228)), dtype=np.float32) / 255.0
    spectrum = np.fft.fft2(small)
    log_amp = np.log(np.abs(spectrum) + 1e-6)
    blurred = cv2.blur(log_amp.astype(np.float32), (3, 3))
    residual = np.exp((log_amp - blurred) + 1j * np.angle(spectrum))
    sal = np.abs(np.fft.ifft2(residual)) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 3)
    # Busy texture hurts legibility as much as a salient subject: add edge density (Sobel magnitude).
    gx, gy = cv2.Sobel(small, cv2.CV_32F, 1, 0), cv2.Sobel(small, cv2.CV_32F, 0, 1)
    edges = np.sqrt(gx * gx + gy * gy)
    clutter = sal / (sal.mean() + 1e-6) + edges / (edges.mean() + 1e-6)
    scale = clutter.shape[0] / im.height

    def score(cy):
        a, b = int((cy - band / 2) * scale), int((cy + band / 2) * scale)
        return float(clutter[max(0, a):b].mean()) * (0.95 if cy == 960 else 1.0)
    return min(candidates, key=score)


def extract_foreground_mask(im: Image.Image) -> Tuple[Any, float]:
    """Extract foreground subject mask using edge-saliency and Otsu thresholding.
    Returns (mask_array, coverage_ratio)."""
    import cv2
    import numpy as np
    arr = np.asarray(im.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    if float(mag.mean()) < 1.0:
        return np.zeros(gray.shape, dtype=np.uint8), 0.0

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    border = np.concatenate([mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]])
    if np.mean(border) > 128:
        mask = cv2.bitwise_not(mask)
        
    coverage = float(np.count_nonzero(mask)) / mask.size
    return mask, coverage


def extract_curiosity_badge(script_payload: Dict[str, Any]) -> str:
    """Extracts a high-impact numerical or forensic curiosity badge from script facts or thumbnail."""
    copy = script_payload.get("thumbnail", {})
    if copy.get("badge"):
        return copy["badge"].upper()

    stat_cards = script_payload.get("stat_cards") or []
    for sc in stat_cards:
        val = str(sc.get("value", "")).strip()
        lbl = str(sc.get("label", "")).strip()
        # QF-23: whole copy or the next source, never cut by characters (it was [:28] / [:22] / [:24])
        if any(c.isdigit() for c in val):
            badge = f"{val.upper()} • {lbl.upper()}"
            if len(badge) <= 28:
                return badge
            if len(val) <= 22:
                return val.upper()

    scale_fact = script_payload.get("scale_fact") or {}
    if scale_fact.get("value"):
        val = scale_fact["value"]
        unit = str(scale_fact.get("unit", "")).upper()
        for badge in (f"{val} {unit} RECORD", f"{val} {unit}".strip()):
            if len(badge) <= 24:
                return badge

    if copy.get("eyebrow") and len(copy["eyebrow"]) <= 24:
        return copy["eyebrow"].upper()

    return "ARCHAEOLOGICAL RECORD"


class VisualEngine:
    def __init__(self, assets_dir: Path = ASSETS_DIR):
        self.assets_dir = assets_dir

    def get_scene_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """
        Scene media for the video, in order. Sources, first match wins:
          assets/<topic_id>/*.jpg|*.png  (sorted by name; at least MIN_SCENE_IMAGES).
              If the folder has a credits.json (fetched images), only credited files are
              used, and only after a human approved the set (`main.py assets approve`).
          the shared suite, for the topics it actually depicts
        An optional assets/<topic_id>/hook_motion.mp4 replaces the first still (Beat 1).
        """
        tid = topic["id"]
        topic_dir = self.assets_dir / tid
        resolved: List[Path] = []

        if (topic_dir / CREDITS_FILE).exists():
            credits = load_credits(topic_dir)
            if not credits["reviewed"]:
                raise MissingAssetsError(
                    f"Fetched images for '{tid}' are not reviewed. Check {topic_dir / CONTACT_SHEET}, delete "
                    f"wrong images, then run: python main.py assets approve {tid}"
                )
            # Only photos that show the topic (best first, so the cover is the best of them), those you kept,
            # and at most a couple of context shots; never one that was weak or never looked at (2026-10-25).
            # The same counting as approval and readiness (image_review.readiness, 2026-10-28), so a pool this
            # refuses is one the scheduler already reported as not ready.
            from .image_review import readiness_text, usable_photos
            from .photo_pool import refresh
            refresh(topic_dir, credits["images"])
            chosen = usable_photos(credits["images"])
            resolved = [topic_dir / c["file"] for c in chosen]
            if len(resolved) < MIN_SCENE_IMAGES:
                raise MissingAssetsError(
                    f"Only {len(resolved)} of the {len(credits['images'])} approved photos of '{tid}' are strong enough "
                    f"for the video (need {MIN_SCENE_IMAGES}): {readiness_text(credits['images'])}"
                    + ". Fetch or upload photos that show the topic itself, or keep a photo with /photos N keep from its sheet.")
        elif topic_dir.is_dir():
            resolved = sorted(
                p for p in topic_dir.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.name != CONTACT_SHEET
            )
            from .image_review import distinct_photos
            from .photo_pool import refresh
            records = [{"file": path.name} for path in resolved]
            refresh(topic_dir, records)
            resolved = [topic_dir / record["file"] for record in distinct_photos(records)]

        if len(resolved) < MIN_SCENE_IMAGES:
            raise MissingAssetsError(
                f"Topic '{tid}' has {len(resolved)} scene images; need at least {MIN_SCENE_IMAGES} "
                f"in {topic_dir}. Add licensed images that depict this topic."
            )

        hook_video = topic_dir / "hook_motion.mp4"
        if hook_video.exists():
            log.info("Motion hook clip found for Beat 1: %s", hook_video.name)
            resolved = [hook_video] + resolved[1:]
        return resolved

    def reserve_sequence(self, topic: Dict[str, Any]) -> List[Path]:
        """The approved pool's context shots beyond the video's usual cap (image_review.reserve_photos), best
        first: used only where a picture would otherwise repeat. Empty for a pool not approved or without credits."""
        topic_dir = self.assets_dir / topic["id"]
        if not (topic_dir / CREDITS_FILE).exists():
            return []
        credits = load_credits(topic_dir)
        if not credits.get("reviewed"):
            return []
        from .image_review import reserve_photos
        return [topic_dir / c["file"] for c in reserve_photos(credits["images"]) if (topic_dir / c["file"]).is_file()]

    @staticmethod
    def hero_image(scene_media: List[Path]) -> Path:
        """The cover's photograph: the sequence's first still (a motion hook clip can't be a cover), unless another
        strong still of the same topic scores clearly higher (hero_photo, 2026-11-01). The strong photos are the
        ones the topic folder's credits mark as showing the topic or kept (image_review.strong_stems)."""
        first = next(p for p in scene_media if not is_video(p))
        from .hero_photo import pick
        from .image_review import strong_stems
        try:
            stems = strong_stems(_topic_folder(Path(first)))
        except (OSError, ValueError, KeyError):  # an unreadable credits file: compare nothing, keep the first
            return first
        return pick(scene_media, stems)

    def generate_thumbnails(self, hero_image_path: Path, script_payload: Dict[str, Any], output_dir: Path,
                            style: str = "badge", variants: bool = False) -> Tuple[Path, Path]:
        """The delivered cover in `style` (one of RENDERABLE_STYLES) and its profile-grid tile, nothing else.
        Every layout keeps its text inside the centre 1080x1080 square, which survives both the
        1:1 and the 3:4 profile-grid crops, and draws every line of its copy (eyebrow, title, tagline).
        Each cover is measured and checked after drawing (still_layout); a failing one is drawn once
        more with the tighter fit. The A/B/C variant suite is no longer rendered (QF-82: the selector
        never chose from it); `variants` is accepted for older callers and ignored. Variant files an older run
        already holds are left as they are."""
        output_dir.mkdir(parents=True, exist_ok=True)
        out_916_path = output_dir / "thumbnail_cover.jpg"
        out_1x1_path = output_dir / "thumbnail_profile_grid.jpg"

        copy = dict(script_payload.get("thumbnail", {}))
        if "badge" not in copy:
            copy["badge"] = extract_curiosity_badge(script_payload)
        copy = _in_spoken_units(copy, script_payload)
        # What the fact sheet says about the hero figure (round 3): a range or disputed value never stands as one
        # number, a hedged one only with its qualifier, and the claim's dimension word travels with it.
        self._figure = cover_figure(copy, script_payload)
        # The script's reveal line (round 4): a tagline that answers it is not drawn on the cover.
        self._reveal = reveal_line(script_payload)
        # Operator ruling 7 (lane B cycle 1): the run's verified lines a tagline the withheld figure emptied falls back to
        from .cover_design import tagline_alternates
        self._tagline_alternates = tagline_alternates(script_payload)
        from .scene_planner import is_illustration
        self._illustration = is_illustration(Path(hero_image_path))

        with Image.open(hero_image_path) as source:
            self._hero_source = ImageOps.exif_transpose(source).convert("RGBA")
        from .image_presentation import full_image
        self._hero_source.info["essential_full_image"] = full_image(Path(hero_image_path))
        from .cover_design import reviewed_focus
        from .photo_hygiene import clean_hero
        self._hero_focus_reviewed = reviewed_focus(Path(hero_image_path))
        self._hero_source, self._hero_focus = clean_hero(self._hero_source, self._hero_focus_reviewed)
        im = ImageOps.fit(self._hero_source, (VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
        self._text_cy = VIDEO_HEIGHT // 2  # legacy recipe field; photo and text now have separate spaces

        def save(path: Path, chosen: str, enforce: bool = False) -> Image.Image:
            out, lay, attempts = self._render_checked(im, copy, chosen)
            grid = {}
            if enforce:
                out, lay, attempts, chosen, grid = self._grid_enforced(im, copy, chosen, out, lay, attempts)
            out = out.convert("RGB")
            encoding = save_cover_jpeg(out, path, "reel")
            from .cover_design import delivered_contrast
            delivered_contrast(lay, path)
            from . import still_layout
            still_layout.save(path, lay, {"renderer": "visual_engine.redraw_cover", "hero": str(hero_image_path),
                                          "copy": copy, "style": chosen, "text_cy": self._text_cy,
                                          "encoding": encoding,
                                          "focus": self._hero_focus_reviewed,
                                          "composition": lay.meta.get("composition"),
                                          "figure": self._figure, "illustration": self._illustration,
                                          "reveal": self._reveal, "grid_proof": lay.meta.get("grid_proof"),
                                          **({"style_refit": self._style_refit}
                                             if enforce and getattr(self, "_style_refit", None) else {}),
                                          **grid}, attempts)
            proof = lay.meta.get("grid_proof") or {}
            if proof and not proof.get("ok"):
                log.warning("Cover %s (%s) at profile-grid size: %s", path.name, chosen, "; ".join(proof["fails"]))
            return out

        from .typography import TextFitError
        chosen = self._fitted_style(copy, style if style in RENDERABLE_STYLES else "badge")
        try:
            final_916 = save(out_916_path, chosen, enforce=True)
        except TextFitError as e:
            # QF-23: the next style the copy fits whole; a cover no style holds whole stops the run (refused)
            from .cover_selector import fitting
            focus, size = self._hero_fit()
            for other in [s for s in fitting(copy, getattr(self, "_figure", None), focus, size) if s != chosen]:
                try:
                    final_916 = save(out_916_path, other, enforce=True)
                    log.warning("Cover %s does not hold its copy whole (%s); drawn as %s.", chosen, e, other)
                    break
                except TextFitError:
                    continue
            else:
                raise
        save_cover_jpeg(grid_crop(final_916), out_1x1_path, "grid")
        log.info("Covers generated (%s): %s, %s", chosen, out_916_path.name, out_1x1_path.name)
        return out_916_path, out_1x1_path

    def _grid_enforced(self, im, copy, style, out, lay, attempts):
        """(image, layout, attempts, style, record) for the delivered cover once the profile-grid proof has been
        acted on (round 4; it was only logged): a hook element (title, figure, qualifier, unit, masthead) under its
        grid floor or outside the 3:4 crop draws the cover again with the tighter fit, then in the next style
        cover_selector.fitting allows; a failure left after that stays on the cover's record ("grid_hold") for the
        review card. A fact carried only by texture stays a warning."""
        from . import still_layout
        from .cover_selector import fitting

        def hooks(lay_):
            return list(((lay_.meta.get("grid_proof") or {}).get("hook_fails")) or [])
        first = hooks(lay)
        if not first:
            return out, lay, attempts, style, {}
        from .typography import TextFitError
        render = self._renderer(style)
        tight_lay = still_layout.Layout("cover", (VIDEO_WIDTH, VIDEO_HEIGHT), cover_text_zone(), tight=True)
        try:   # QF-23: a redraw that cannot hold the copy whole is no option; the cover drawn first stands
            tight = render(im.copy(), copy, lay=tight_lay, tight=True)
            if not hooks(tight_lay) and not tight_lay.problems():
                return tight, tight_lay, attempts + 1, style, {"grid_redraw": {"tight": True, "fails": first}}
        except TextFitError as e:
            log.info("Cover %s: the tighter fit does not hold its copy whole (%s).", style, e)
        focus, size = self._hero_fit()   # r7: the paper card's fill rule applies to the fallback too
        for other in fitting(copy, getattr(self, "_figure", None), focus, size):
            if other == style:
                continue
            try:
                o_out, o_lay, o_att = self._render_checked(im, copy, other)
            except TextFitError as e:
                log.info("Cover style %s does not hold the copy whole (%s).", other, e)
                continue
            if not hooks(o_lay) and not o_lay.problems():
                log.warning("Cover %s failed the profile-grid proof (%s); drawn as %s instead.", style,
                            "; ".join(first), other)
                return o_out, o_lay, attempts + o_att, other, {"grid_fallback": {"from": style, "to": other,
                                                                                 "fails": first}}
        log.warning("Cover %s fails the profile-grid proof in every fitting style: %s", style, "; ".join(first))
        # Cycle 0 round 8 (lander ruling: no surface draws under its floor): an element under its grid floor in every
        # fitting style is a recorded refusal (refusal.LayoutError), never a cover delivered under its floor; a
        # failure of another kind (contrast on the tile, the 3:4 crop) stays on the record as before
        floors = [f for f in first if "px cap at 124" in f]
        if floors:
            from .refusal import LayoutError
            raise LayoutError("the cover's " + "; ".join(floors) + " in every fitting style")
        return out, lay, attempts, style, {"grid_hold": first}

    def _hero_fit(self):
        """(reviewed focus, pixel size) of the hero as the covers draw it, for cover_selector.fitting's paper-card
        fill rule (r7: the rule was off in the pipeline, which never passed the hero to the selector)."""
        focus = getattr(self, "_hero_focus", None)
        source = getattr(self, "_hero_source", None)
        return (focus, source.size) if (focus and source is not None) else (None, None)

    def _fitted_style(self, copy, style: str) -> str:
        """`style`, unless the hero's reviewed focus takes the paper card out (cover_selector.fitting: the feature
        fills too little of its window); then the first other style that fits, recorded in self._style_refit.
        The selector chose without the photo when its caller passed no hero; here the photo is known."""
        from .cover_selector import PAPER, fitting
        self._style_refit = None
        focus, size = self._hero_fit()
        if style != PAPER or not focus:
            return style
        options = fitting(copy, getattr(self, "_figure", None), focus, size)
        if PAPER in options:
            return style
        other = next((s for s in options if s != PAPER), "badge")
        log.info("Cover style %s refitted to %s: the reviewed feature fills too little of the paper card.", style,
                 other)
        self._style_refit = {"from": style, "to": other, "reason": "paper card fill"}
        return other

    def _renderer(self, style: str):
        return {"lower_third": self._cover_lower_third, "minimal": self._cover_minimal,
                "depth_sandwich": self._cover_depth_sandwich,
                "monument": self._cover_monument}.get(style, self._cover_badge)

    def _render_checked(self, im: Image.Image, copy: Dict[str, str], style: str):
        """(image, layout, attempts): the style drawn, checked, and drawn once more with the tighter fit if its
        measured layout fails."""
        from . import still_layout
        render = {"lower_third": self._cover_lower_third, "minimal": self._cover_minimal,
                  "depth_sandwich": self._cover_depth_sandwich,
                  "monument": self._cover_monument}.get(style, self._cover_badge)

        def draw(tight: bool):
            lay = still_layout.Layout("cover", (VIDEO_WIDTH, VIDEO_HEIGHT), cover_text_zone(), tight=tight)
            return render(im.copy(), copy, lay=lay, tight=tight), lay
        return still_layout.drawn(draw)

    def _cover_lower_third(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        return self._cover_design(im, copy, "lower_third", lay, tight)

    def _cover_monument(self, im: Image.Image, copy: dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        # One measured figure as the hero over the full-bleed photograph (cover_design._monument).
        return self._cover_design(im, copy, "monument", lay, tight)

    def _cover_minimal(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        return self._cover_design(im, copy, "minimal", lay, tight)

    def _cover_depth_sandwich(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        # Retained style ID: the evidence plate replaces the unreliable foreground mask.
        return self._cover_design(im, copy, "depth_sandwich", lay, tight)

    def _cover_badge(self, im: Image.Image, copy: Dict[str, str], lay=None, tight: bool = False) -> Image.Image:
        # Retained style ID: discovery now uses a plain kicker and a bold headline.
        return self._cover_design(im, copy, "badge", lay, tight)

    def _cover_design(self, im, copy, style, lay, tight):
        from .cover_design import render_cover
        return render_cover(im, copy, style, getattr(self, "_text_cy", VIDEO_HEIGHT // 2), lay, tight,
                            source=getattr(self, "_hero_source", None), focus=getattr(self, "_hero_focus", None),
                            figure=getattr(self, "_figure", None),
                            illustration=bool(getattr(self, "_illustration", False)),
                            reveal=getattr(self, "_reveal", None),
                            alternates=getattr(self, "_tagline_alternates", None))

    def generate_youtube_thumbnail(
        self,
        hero_image_path: Path,
        script_payload: Dict[str, Any],
        output_path: Path,
    ) -> Path:
        """16:9 thumbnail (1920x1080) for the YouTube documentary: the covers' system (youtube_thumbnail): the
        photograph full-bleed, the masthead, one figure or a two-tone Anton headline on a left ink gradient,
        YouTube's timestamp corner clear. (Cycle 0 round 7: the legacy depth-layered thumbnail, no caller, retired.)"""
        from . import youtube_thumbnail
        script_payload.pop("thumbnail_note", None)   # this render's note only (QF-23)
        from .refusal import LayoutError
        # Lane B cycle 3: the ranked headline chain (youtube_headline_chain), each candidate rendered on the hero
        # until one sets; refused only when the whole chain fails (every reason recorded)
        tried: list = []
        im = drawn_plates = None
        meta: dict[str, Any] = {}
        for cand in youtube_headline_chain(script_payload):
            tag = {"variant": cand["variant"]} if cand.get("variant") else {}
            if cand["copy"] is None:
                tried.append({"source": cand["source"], **tag, "skipped": cand["skipped"]})
                continue
            meta = {}
            try:
                im, drawn_plates = youtube_thumbnail.render(
                    meta=meta, **youtube_render_inputs(script_payload, hero_image_path, copy=cand["copy"]))
            except LayoutError as e:
                tried.append({"source": cand["source"], **tag, "refused": str(e)})
                im = None
                continue
            if not youtube_thumbnail.drew_headline(drawn_plates):   # the word budget left the line out whole
                tried.append({"source": cand["source"], **tag,
                              "refused": "no headline drawn (longer than the word budget)"})
                im = None
                continue
            faults = drawn_faults(drawn_plates, script_payload)
            if faults:   # the drawn headline (a badge figure included) held to the evidence again
                tried.append({"source": cand["source"], **tag, "refused": "truth (drawn): " + "; ".join(faults)})
                im = None
                continue
            why = specificity_fault(drawn_text(drawn_plates), script_payload)
            if why:   # the render's own fallback (QF-23 headline_from) may draw another line: held to the gate too
                tried.append({"source": cand["source"], **tag, "refused": why + " (as drawn)"})
                im = None
                continue
            if cand.get("withheld"):   # its contradicted figure lifted out by the chain (figure_free), recorded
                w, meta["headline_withheld"] = cand["withheld"][0], cand["withheld"]
                meta.setdefault("figure_withheld", {"number": w["number"], "unit": w["unit"], "reason": w["reason"],
                                                    "source": cand["source"], "figures": cand["withheld"]})
            meta["headline_source"] = cand["source"]
            # the record says what was DRAWN and the source it came from (the figure branch may draw the copy's badge
            # figure over another of its lines, lander review: pair 1)
            meta["headline_chain"] = tried + [{"source": cand["source"], "candidate": cand["text"],
                                               "drawn": drawn_text(drawn_plates),
                                               **tag,
                                               **({"drawn_from": meta["headline_from"]} if meta.get("headline_from")
                                                  else {})}]
            break
        if im is None:
            # int 3 ruling: only when no candidate passes (each whole line, then each truth-refused line with its
            # contradicted figure lifted out) does the thumbnail decline, recorded as a refusal (refusal.decline:
            # main.cover_job keeps it with its surface, cover_job_results puts it on the review card)
            from .refusal import decline
            error = decline("YouTube thumbnail", "youtube.headline_chain", "no line of the headline chain passes: "
                            + "; ".join(f"{t['source']}{' (figure withheld)' if t.get('variant') else ''}: "
                                        f"{t.get('skipped') or t.get('refused')}" for t in tried))
            error.chain = tried
            log.warning("%s", error)
            raise error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encoding = save_cover_jpeg(im, output_path, "youtube")
        # The feed-size proof on the delivered JPEG (round 3), kept beside the thumbnail for QA to read.
        with Image.open(output_path) as saved:
            qa = youtube_thumbnail.downscale_check(saved.convert("RGB"), meta)
        import json
        output_path.with_suffix(".qa.json").write_text(json.dumps(
            {**{k: meta.get(k) for k in ("downscale", "downscale_ok", "downscale_fails", "title_overlap",
                                         "figure_withheld", "figure_declined", "tension_from", "text_over_focus",
                                         "headline_fill_from", "tagline_dropped", "year_event", "ink_edge",
                                         "headline_from", "headline_dropped", "headline_lines_dropped",
                                         "headline_fragment", "headline_pair_kept", "headline_source",
                                         "headline_withheld",
                                         "headline_chain")},
             # cycle 0 round 8: the drawn text of every line, for the release gate's figure check (figure_gate)
             "encoding": encoding,
             "elements": [{"name": n, "text": getattr(pl, "text", ""), "box": [int(v) for v in pl.box]}
                          for n, pl in (drawn_plates or []) if not getattr(pl, "empty", False)]},
            indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
        # QF-23 (option 1): a headline the four-word budget holds only cut is never drawn cut; the review card says
        # what the thumbnail drew instead
        note = youtube_thumbnail.headline_note(meta)
        # lane B cycle 3: the review card names the chain source the headline was drawn from, at approval
        last = meta["headline_chain"][-1]
        source = (f"\U0001f5bc YouTube thumbnail headline from {meta['headline_source'].replace('_', ' ')}"
                  + (f" (its {last['drawn_from']} line)" if last.get("drawn_from") else "") + f": {last['drawn']}"
                  + (" (" + ", ".join(w["drawn"] for w in meta.get("headline_withheld") or [])
                     + " withheld: the run's evidence contradicts it)" if last.get("variant") else ""))
        note = f"{source}\n{note}" if note else source
        if note:
            script_payload["thumbnail_note"] = note
            log.warning("YouTube thumbnail: %s", note)
        if not qa:
            log.warning("YouTube thumbnail at feed size: %s", "; ".join(meta.get("downscale_fails") or []))
        log.info("YouTube thumbnail generated: %s", output_path.name)
        return output_path


def _topic_folder(photo: Path) -> Path:
    """The folder holding a photo's credits: its own, or, for a derived copy (a caption-strip crop under
    cache/text_crop), its original's, through its lineage record (PR review, 2026-11-02)."""
    if (photo.parent / "credits.json").is_file():
        return photo.parent
    try:
        from .lineage import source_chain
        sources = [Path(r["path"]) for r in source_chain(photo) if r.get("kind") != "lineage"]
    except (OSError, ValueError, KeyError):
        sources = []
    for src in reversed(sources):
        if (src.parent / "credits.json").is_file():
            return src.parent
    return photo.parent


def fact_sheet_for(script_payload: dict[str, Any]) -> dict[str, Any] | None:
    """The run's verified fact sheet, or None: the one the fact review used (payload["fact_review"]["sheet_path"]),
    else the topic's sheet (fact_sheet.load). Round 4: a review path that cannot be read here (a path written on
    another machine) falls back to the topic's sheet; it no longer ends the lookup, which left the figure check
    off and let a disputed figure stand as one bare number."""
    import json
    path = str(((script_payload.get("fact_review") or {}).get("sheet_path")) or "")
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError) as e:
            log.info("Fact review sheet %s unreadable here (%s); trying the topic's sheet.", path, e)
    if (path or script_payload.get("fact_sheet")) and script_payload.get("topic_id"):
        try:
            from . import fact_sheet
            return fact_sheet.load(str(script_payload["topic_id"]), max_age_days=3650)
        except (OSError, ValueError, KeyError) as e:
            log.warning("Fact sheet unreadable for the cover's figure check (%s).", e)
    return None


def fact_checked(script_payload: dict[str, Any]) -> bool:
    """True when the payload says it was checked against a fact sheet (a fact review, or written from a sheet)."""
    return bool(script_payload.get("fact_review") or script_payload.get("fact_sheet"))


def cover_figure(copy: dict[str, Any], script_payload: dict[str, Any]) -> dict[str, Any] | None:
    """cover_design.figure_check for this cover copy against the run's fact sheet (None without a sheet or a
    figure): what the covers, the selector and the YouTube thumbnail may draw of the hero figure.

    Fail-closed (round 4): a payload that says it was fact-checked but whose sheet does not load gets the check
    against an empty sheet, marked "sheet_missing": no measured figure is the monument's or the thumbnail's hero,
    and a year carries no event word; the headline stays as the copy wrote it."""
    from .cover_design import figure_check
    sheet = fact_sheet_for(script_payload)
    checked = dict(copy)
    checked = _in_spoken_units(checked, script_payload)
    if sheet is None:
        if not fact_checked(script_payload):
            return None
        out = figure_check(checked, {"facts": []}, script_payload)
        if out is not None:
            out.update(sheet_missing=True, monument_ok=False,
                       reason="the payload was fact-checked but its fact sheet did not load")
            log.warning("Cover figure check: %s.", out["reason"])
        return out
    return figure_check(checked, sheet, script_payload)


def reveal_line(script_payload: dict[str, Any]) -> str | None:
    """The script's reveal or takeaway line, as the covers read it (resolving_tagline): the closing sentence, or
    None when the payload has none (r7: an empty string read as a reveal and let the cause rule drop taglines)."""
    line = str(script_payload.get("closing_sentence") or "").strip()
    return line or None


def _in_spoken_units(copy: Dict[str, Any], script_payload: Dict[str, Any]) -> Dict[str, Any]:
    """The cover's lines with each figure in the unit the narration speaks (Richat's cover said "40+ KM" while the
    narration said "25 miles": one unit per run, 2026-10-29)."""
    from .figures import deck_lead   # cycle 0 round 8: one lead unit per run (the signed scale fact decides)
    narration = script_payload.get("narration_script") or ""
    lead = deck_lead(script_payload)
    return {k: spoken_units(v, narration, lead) if isinstance(v, str) else v for k, v in copy.items()}


def youtube_render_inputs(script_payload: Dict[str, Any], hero_image_path: Path | None = None,
                          copy: Dict[str, str] | None = None) -> Dict[str, Any]:
    """The YouTube thumbnail render's inputs (youtube_thumbnail.render keyword arguments, but meta): the one place
    they are made, read by generate_youtube_thumbnail and by the copy-stage fit check (src/cover_fit.py), so the check
    and the render are one decision (freeze lane B cycle 1: the check rendered without the figure check and the badge
    and passed copy the render refuses). `copy`: the cover copy (default youtube_cover_copy(script_payload)). Without a
    hero, `source` is None and focus / illustration are absent (the check probes the columns a hero can leave)."""
    copy = dict(copy if copy is not None else youtube_cover_copy(script_payload))
    # The badge is not drawn; it is read for the qualifier the covers' copy sets on the figure ("EST.").
    copy.setdefault("badge", _in_spoken_units({"b": extract_curiosity_badge(script_payload)}, script_payload)["b"])
    out: Dict[str, Any] = {"source": None, "copy": copy, "focus": None, "figure": cover_figure(copy, script_payload),
                           "youtube_title": script_payload.get("youtube_title") or None,
                           "reveal": reveal_line(script_payload)}
    if hero_image_path is not None:
        from .cover_design import reviewed_focus
        from .image_presentation import full_image
        from .photo_hygiene import clean_hero
        from .scene_planner import is_illustration
        with Image.open(hero_image_path) as source:
            src = ImageOps.exif_transpose(source).convert("RGBA")
        src.info["essential_full_image"] = full_image(Path(hero_image_path))
        out["source"], out["focus"] = clean_hero(src, reviewed_focus(Path(hero_image_path)))
        out["illustration"] = is_illustration(Path(hero_image_path))
    return out


def youtube_cover_copy(script_payload: Dict[str, Any]) -> Dict[str, str]:
    """The cover copy the YouTube thumbnail sets (generate_youtube_thumbnail), each figure in the narration's unit:
    the one copy its pre-render fit check reads too (src/cover_fit.py; main's deep-dive draft retry), so the check
    and the render never see different words (SYS-R8-3)."""
    copy = documentary_cover_copy(script_payload)
    signed = youtube_headline(script_payload)
    if signed:   # lane B cycle 3: the run's signed 16:9 headline first (the render walks the rest of the chain)
        copy = dict(copy, line1=signed[0], line2=signed[1])
    copy = {k: ("" if k in ("eyebrow", "tagline") and str(v or "").upper() in CODE_LABELS else v)
            for k, v in copy.items()}   # lane B cycle 3: a code label is not signed copy (as in the chain)
    return _in_spoken_units(copy, script_payload)


def youtube_headline(script_payload: Dict[str, Any]) -> tuple[str, str] | None:
    """The run's signed short YouTube headline (thumbnail.youtube_line1 / youtube_line2, from the deep dive's
    youtube_headline, held by trace_check.youtube_headline_problems), in capitals, or None."""
    th = script_payload.get("thumbnail") or {}
    l1, l2 = (" ".join(str(th.get(k) or "").split()).upper() for k in ("youtube_line1", "youtube_line2"))
    return (l1, l2) if (l1 or l2) else None


# Lane B cycle 3 (lander ruling under the operator's delegation, 2026-11-19): the YouTube headline is a RANKED CHAIN
# of the run's signed lines, from lines written as hooks to lines written for other jobs, never a free pick. Interior
# slide and Story body lines are never candidates. Each must pass the truth / figure gates, read as a headline and
# complement the video title, then set in the render (the heading gates); the next is tried otherwise, and the
# thumbnail is refused only when the whole chain fails. The source drawn is recorded (qa.json, the review card).
YOUTUBE_HEADLINE_CHAIN = ("youtube_headline", "thumbnail", "carousel_cover", "tagline", "story_hook", "eyebrow")
# (lander, 2026-11-19 review: the eyebrow is a category line more often than a hook, so it is tried last)
TITLE_OVERLAP_MAX = 0.5   # a candidate whose content words are more than half the video title's words repeats it


# The labels the code itself writes on a documentary cover (main.documentary_cover, documentary_cover_copy's
# defaults): claim-free and never signed copy, so never a headline candidate; a documentary reads its parent Reel's
# signed lines for those sources instead (payload["parent_copy"], main._inherit_graphics_data).
CODE_LABELS = frozenset({"DOCUMENTARY", "DOCUMENTARY INVESTIGATION", "EXPLORE THE EVIDENCE", "WHAT THE EVIDENCE SHOWS",
                         "THE EVIDENCE"})


def _chain_lines(script_payload: Dict[str, Any], source: str) -> tuple[str, str]:
    """(line 1, line 2) of one chain source, as signed, in capitals ("", "" when the run has none). A documentary's
    carousel / Story / tagline / eyebrow sources are its parent Reel's (parent_copy) where it has none of its own."""
    def clean(t):
        return " ".join(str(t or "").split()).upper()
    parent = script_payload.get("parent_copy") or {}
    th = dict(script_payload.get("thumbnail") or {})
    for k in ("tagline", "eyebrow"):
        if clean(th.get(k)) in CODE_LABELS or not clean(th.get(k)):
            th[k] = (parent.get("thumbnail") or {}).get(k) or ""
    if not script_payload.get("carousel_slides") and parent.get("carousel_slides"):
        script_payload = dict(script_payload, carousel_slides=parent["carousel_slides"])
    if not script_payload.get("story_slides") and parent.get("story_slides"):
        script_payload = dict(script_payload, story_slides=parent["story_slides"])
    if source == "youtube_headline":
        return youtube_headline(script_payload) or ("", "")
    if source == "thumbnail":
        base = documentary_cover_copy(script_payload)
        return base["line1"], base["line2"]
    if source == "carousel_cover":
        slides = [s for s in (script_payload.get("carousel_slides") or []) if isinstance(s, dict)]
        return (clean(slides[0].get("heading")), "") if slides else ("", "")
    if source in ("tagline", "eyebrow"):
        return clean(th.get(source)), ""
    if source == "story_hook":
        slides = [s for s in (script_payload.get("story_slides") or []) if isinstance(s, dict)]
        return (clean(slides[0].get("line1")), clean(slides[0].get("line2"))) if slides else ("", "")
    raise ValueError(source)


# The run's EVIDENCE for a headline's figures (lane B cycle 3, lander review: "7,000 RESTING SOULS" passed against
# the title's own unhedged "7,000-Body" while the narration and the stat card say "approximately 7,000"): the
# narration, the stat cards and the verified graphics data and fact sheet, never the candidate's own field or other
# derived copy (titles, captions, cover, carousel, Story, comments).
EVIDENCE_KEYS = ("narration_script", "acts", "stat_cards", "fact_sheet", "scale_fact", "cross_section", "timeline",
                 "route", "location")


def evidence_texts(script_payload: Dict[str, Any]) -> List[str]:
    """The texts a headline's figures are held to (EVIDENCE_KEYS, read by figure_gate.signed_texts)."""
    from . import figure_gate
    return figure_gate.signed_texts({k: script_payload[k] for k in EVIDENCE_KEYS if k in script_payload})


def evidence_faults(text: str, script_payload: Dict[str, Any]) -> List[str]:
    """figure_gate.figure_faults of `text` against the run's evidence (evidence_texts): [] when every figure in it is
    an evidence figure, whole, its hedge kept."""
    from . import figure_gate, figures
    return figure_gate.figure_faults(text, figures.signed_figures(evidence_texts(script_payload)))


def headline_generic_vocabulary() -> frozenset:
    """The active brand's generic vocabulary (data/headline_generic_vocabulary.json, per brand name; a brand with no
    entry has none): words that say the niche, never the subject ("ANCIENT", "MARVELS", "MYSTERY")."""
    import json
    from . import brand
    path = Path(__file__).resolve().parents[1] / "data" / "headline_generic_vocabulary.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    words = (data.get("brands") or {}).get(brand.active().name) or []
    return frozenset(str(w).upper() for w in words)


def _stem(word: str) -> str:
    w = word.upper().replace("’", "'")
    w = w[:-2] if w.endswith("'S") else w
    return w[:-1] if len(w) > 4 and w.endswith("S") and not w.endswith("SS") else w


def place_words(script_payload: Dict[str, Any]) -> set:
    """The run's place words (stemmed): its location's city, region and country. A place alone is not the subject
    (lander review: "ANCIENT ASWAN", "ANCIENT MALTA" are category labels)."""
    from .youtube_thumbnail import _content
    loc = script_payload.get("location") or {}
    if not isinstance(loc, dict):
        return set()
    return {_stem(w) for w in _content(" ".join(str(loc.get(k) or "") for k in ("city", "region", "country")))}


def subject_words(script_payload: Dict[str, Any]) -> set:
    """This run's subject nouns and entities (stemmed content words): its topic name, its title, the scale fact's
    subject and the site's own name (location.name), less its place words (place_words: a city, region or country
    alone is not the subject) and the brand's generic vocabulary."""
    from .youtube_thumbnail import _content
    loc = script_payload.get("location") or {}
    fact = script_payload.get("scale_fact") or {}
    parts = [str(script_payload.get("topic_id") or "").replace("_", " "), str(script_payload.get("title") or ""),
             str(loc.get("name") or "") if isinstance(loc, dict) else "",
             str(fact.get("subject") or "") if isinstance(fact, dict) else ""]
    generic = headline_generic_vocabulary()
    places = place_words(script_payload)
    own = {_stem(w) for w in _content(" ".join(parts)) if w not in generic and not w.isdigit()}
    return (own | evidence_nouns(tuple(evidence_texts(script_payload)))) - places - {_stem(w) for w in generic}


def evidence_nouns(texts: tuple) -> set:
    """The head nouns the run's signed evidence repeats (lane B cycle 3, lander: "FORGOTTEN GEARS" names the thing
    itself): a noun (the committed offline tagger, narrative_payoff.nouns) found in at least two separate evidence
    texts (evidence_texts: the narration, each stat card, the graphics data, each fact), stemmed. Generic and
    portable: nothing names a niche; places and the generic vocabulary are taken out by the caller."""
    return set(_evidence_nouns(texts))


_EVIDENCE_NOUNS: Dict[tuple, frozenset] = {}


def _evidence_nouns(texts: tuple) -> frozenset:
    if texts in _EVIDENCE_NOUNS:
        return _EVIDENCE_NOUNS[texts]
    from collections import Counter

    from .narrative_payoff import nouns
    seen: Counter = Counter()
    for t in dict.fromkeys(str(x).strip() for x in texts if str(x).strip()):
        seen.update({_stem(n.upper()) for n in nouns(t) if n.isalpha() and len(n) > 2})
    out = frozenset(w for w, k in seen.items() if k >= 2)
    if len(_EVIDENCE_NOUNS) > 256:
        _EVIDENCE_NOUNS.clear()
    _EVIDENCE_NOUNS[texts] = out
    return out


def specificity_fault(text: str, script_payload: Dict[str, Any]) -> str:
    """"" when the candidate is specific to this run: it carries a figure or a subject word (subject_words) and is not
    made only of the brand's generic vocabulary; else the reason (lander review: "ANCIENT MARVELS" is a category
    label, not a hook)."""
    from . import figures
    from .youtube_thumbnail import _content
    words = _content(text)
    generic = headline_generic_vocabulary()
    if words and all(w in generic for w in words):
        return "not specific: only the brand's generic vocabulary"
    if figures.parse(text) or any(_stem(w) in subject_words(script_payload) for w in words):
        return ""
    return "not specific: no figure and no word of this run's subject"


def youtube_headline_chain(script_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ranked chain (YOUTUBE_HEADLINE_CHAIN) as [{"source", "text", "copy" (the render's cover copy, in spoken
    units) or None, "skipped" (the text gate that rejected it) or ""}]. Text gates, before any render: the source
    exists; it reads as a headline (youtube_thumbnail._alone on the whole: a whole phrase, no fragment, no line ending
    on a function word or a lead mark); it complements the video title (at most TITLE_OVERLAP_MAX of its content words
    are the title's); every figure in it is an evidence figure, its hedge and both span ends kept (evidence_faults:
    never its own field or other derived copy); it is specific to this run (specificity_fault). The render then holds
    the heading gates and the drawn text is held to the evidence again (drawn_faults)."""
    base = documentary_cover_copy(script_payload)
    out, contradicted = [], []
    for source in YOUTUBE_HEADLINE_CHAIN:
        l1, l2 = _chain_lines(script_payload, source)
        rec = _chain_candidate(script_payload, base, source, l1, l2)
        out.append(rec)
        if rec["skipped"].startswith("truth: "):
            contradicted.append((source, l1, l2))
    # int 3 ruling (2026-11-23): a figure the evidence contradicts is never drawn, and the line it sat in is not lost
    # with it while it says something without it: after every whole line, each truth-refused source once more with
    # its contradicted figure groups lifted out (figure_free), held to every gate again; never a word added
    for source, l1, l2 in contradicted:
        withheld: list = []
        f1, f2 = (figure_free(t, script_payload, withheld) for t in (l1, l2))
        if not (f1 or f2):
            continue
        rec = _chain_candidate(script_payload, base, source, f1, f2, withheld)
        rec["variant"] = "figure withheld"
        out.append(rec)
    return out


def _chain_candidate(script_payload: Dict[str, Any], base: Dict[str, str], source: str, l1: str, l2: str,
                     withheld: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """One chain record (youtube_headline_chain): the text gates in order, then the render's copy. `withheld`: the
    figures figure_free lifted out of this source's lines; they are lifted out of the copy's other fields too, so the
    kicker never states one either."""
    from .youtube_thumbnail import _alone, _content
    title = str(script_payload.get("youtube_title") or "")
    text = " ".join(t for t in (l1, l2) if t)
    rec = {"source": source, "copy": None, "skipped": "", "text": text}
    if withheld:
        rec["withheld"] = withheld
    if not text or text in CODE_LABELS:
        rec["skipped"] = "none in this run" if not text else "a label the code writes, not signed copy"
        return rec
    if _alone(text) != text.rstrip():
        rec["skipped"] = "not a headline (a fragment, or a line ending on a function word)"
        return rec
    words = _content(text)
    if title and words and len(words & _content(title)) / len(words) > TITLE_OVERLAP_MAX:
        rec["skipped"] = "repeats the video title"
        return rec
    faults = evidence_faults(text, script_payload)   # every source, the base pair too: evidence only
    if faults:
        rec["skipped"] = "truth: " + "; ".join(faults)
        return rec
    why = specificity_fault(text, script_payload)
    if why:
        rec["skipped"] = why
        return rec
    copy = dict(base, line1=l1, line2=l2)
    if source != "thumbnail":
        # the other sources are the headline alone: the eyebrow and tagline are candidates of their own, so the
        # render's own fallback to them (QF-23 headline_from) never draws one past the chain's gates
        copy["eyebrow"] = copy["tagline"] = ""
    for k in ("tagline", "eyebrow"):
        if copy.get(k, "").upper() in CODE_LABELS:
            copy[k] = ""    # a code label is never a kicker fill either (it is not signed copy)
    if withheld:
        for k in ("eyebrow", "tagline"):
            copy[k] = figure_free(copy.get(k) or "", script_payload, [])
    rec["copy"] = _in_spoken_units(copy, script_payload)
    return rec


def figure_free(text: str, script_payload: Dict[str, Any], withheld: List[Dict[str, Any]]) -> str:
    """`text` with every figure group the run's evidence contradicts (evidence_faults) lifted out whole: its hedge,
    number, unit, conversion and the dimension word after it (figures.parse), tidied at the cut as the covers' own
    withholding is (cover_design._without), and "" when what is left is a fragment (cover_design.withheld_fragment:
    "ACROSS", "BUILT"). Only the copy's own words remain; none is added. Each lifted figure is appended to
    `withheld` ({"number", "unit", "drawn", "reason"}) for the record."""
    import re

    from .cover_design import _without, withheld_fragment
    from .figures import parse
    out, cut = text, False
    for f in reversed(parse(text)):
        faults = evidence_faults(text[f.start:f.end], script_payload)
        if not faults:
            continue
        end = f.end
        if f.tail:
            m = re.match(r"\s*" + re.escape(f.tail) + r"\b", out[end:], re.IGNORECASE)
            end += m.end() if m else 0
        out, cut = _without(out, (f.start, end)), True
        withheld.insert(0, {"number": f.number, "unit": f.unit, "drawn": f.drawn(),
                            "reason": "the run's evidence contradicts it: " + "; ".join(faults)})
    out = " ".join(out.split())
    return "" if cut and withheld_fragment(out) else out


def drawn_text(plates) -> str:
    """The headline as drawn: the figure row (qualifier, figure, unit) and the title rows, in drawing order."""
    return " ".join(pl.text for n, pl in plates if not pl.empty and n in (
        "figure_qualifier", "figure", "figure_unit", "title_1", "title_2", "title", "qualifier"))


def drawn_faults(plates, script_payload: Dict[str, Any]) -> List[str]:
    """The drawn headline held to the evidence (evidence_faults): the figure row as one statement, each title row on
    its own (a figure the render took from the badge is judged as drawn, not as the copy wrote it)."""
    fig = " ".join(pl.text for n, pl in plates if not pl.empty and n in ("figure_qualifier", "figure", "figure_unit"))
    rows = [pl.text for n, pl in plates if not pl.empty and n in ("title_1", "title_2", "title")]
    out = evidence_faults(fig, script_payload) if fig else []
    for r in rows:
        out += evidence_faults(r, script_payload)
    return out


def documentary_cover_copy(script_payload: Dict[str, Any]) -> Dict[str, str]:
    """The four lines of the documentary cover: the payload's thumbnail copy where given, else lines drawn
    from the topic itself (the title split on whole words, the sub-theme). Until 2026-10-24 the fallbacks
    were Derinkuyu's ("ANCIENT METROPOLIS", "UNEXPLAINED ARCHITECTURE") and appeared on any topic whose
    deep-dive copy was missing."""
    copy = script_payload.get("thumbnail") or {}
    title = (script_payload.get("title") or "").strip()
    words, line1 = title.split(), ""
    while words and (not line1 or len(f"{line1} {words[0]}") <= 24):   # whole words, the first even past 24 (QF-23)
        line1 = f"{line1} {words.pop(0)}".strip()
    rest = " ".join(words) if not copy.get("line1") else ""
    sub_theme = (script_payload.get("sub_theme") or "").strip()
    line2 = copy.get("line2") or rest or sub_theme or "THE EVIDENCE"
    tagline = copy.get("tagline") or ("" if line2 == sub_theme else sub_theme) or "WHAT THE EVIDENCE SHOWS"
    return {"eyebrow": (copy.get("eyebrow") or "DOCUMENTARY INVESTIGATION").upper(),
            "line1": (copy.get("line1") or line1).upper(), "line2": line2.upper(), "tagline": tagline.upper()}
