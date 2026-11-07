"""Art direction for the graphics (quality step 2, operator 2026-10-24): one look per topic, derived
rather than hand-picked, and one grammar for every graphic kind.

- palette_from(hero): an accent colour sampled from the topic's hero photograph (its dominant saturated
  hue, lifted for legibility and checked for contrast against the dark backdrop), used for the structural
  strokes - spines, markers, leaders, rings. Gold stays the figure colour (numbers, years, depths), the
  brand's constant. When the sampled hue sits too close to gold, or nothing saturated is found, the accent
  is a cool complement so figures and structure never merge.
- type_scale(width, height): one hierarchy for all kinds - figure : title : label : caption : eyebrow -
  with every size at or above READABLE_FRACTION of the frame height (text must read on a phone).
- MOTION: one timing table. A count-up finishes by COUNT_DONE_BY of the clip so the resting value is on
  screen for most of it (the automated check and the viewer both see it).
- grade_bias(hero): a bounded warm/cool shift from the hero photograph, so a desert topic and a cold-sea
  topic do not share one tint; the film LUT stays.

The active palette is a context variable set per run (use_palette); renderers read accent().
"""
import colorsys
import contextvars
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

log = logging.getLogger(__name__)

from .brand import GOLD  # the brand's one gold (src/brand.py, 2026-10-29); #FFD700 until then
# The fallback accent is the brand's TEAL (src/brand.py; 2026-11-01 design review: the old #78BEFF sky blue was
# no brand token): a cool complement to gold, 8.6:1 on the ink ground.
from .brand import TEAL
DEFAULT_PALETTE: Dict[str, Any] = {"accent": TEAL, "accent_hex": "#{:02X}{:02X}{:02X}".format(*TEAL),
                                   "source": "default (cool complement to gold)"}
READABLE_FRACTION = 0.022          # the smallest text, as a fraction of the frame height
MIN_CONTRAST = 4.5                 # accent against the dark backdrop (WCAG AA for text)
GOLD_HUE_CLEARANCE = 35.0          # degrees of hue the accent keeps from gold
MOTION = {"reveal": 0.4, "count": 1.3, "stagger": 0.9, "count_done_by": 0.4, "push_in": 0.03}

_palette: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("art_palette", default=None)


def use_palette(palette: Optional[Dict[str, Any]]) -> None:
    _palette.set(palette)


def palette() -> Dict[str, Any]:
    return _palette.get() or DEFAULT_PALETTE


def accent() -> Tuple[int, int, int]:
    return tuple(palette()["accent"])  # type: ignore[return-value]


def _luminance(rgb: Tuple[int, int, int]) -> float:
    def channel(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(rgb: Tuple[int, int, int], against: Tuple[int, int, int] = (12, 12, 14)) -> float:
    l1, l2 = _luminance(rgb), _luminance(against)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _hue_distance(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360.0
    return min(d, 360.0 - d)


def _legible(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """The same hue, lifted until it reads against the dark backdrop."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    s = min(max(s, 0.55), 0.8)
    v = max(v, 0.8)
    out = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v))
    while contrast(out) < MIN_CONTRAST and v < 1.0:
        v = min(1.0, v + 0.05)
        s = max(0.35, s - 0.05)
        out = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, v))
    return out  # type: ignore[return-value]


def palette_from(hero: Optional[Path]) -> Dict[str, Any]:
    """The topic's accent from its hero photograph; never raises (a missing or unreadable photo gives the
    default). The record says where the colour came from."""
    if not hero or not Path(hero).is_file():
        return dict(DEFAULT_PALETTE)
    try:
        with Image.open(hero) as im:
            small = im.convert("RGB").resize((96, 96))
            quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
            counts = quantized.getcolors(96 * 96) or []
            pal = quantized.getpalette() or []
    except Exception as e:
        log.debug("Palette not sampled from %s (%s); default used.", hero, e)
        return dict(DEFAULT_PALETTE)
    gold_hue = colorsys.rgb_to_hsv(*(c / 255.0 for c in GOLD))[0] * 360.0
    best, best_score = None, 0.0          # a saturated hue clear of gold
    warm, warm_score = None, 0.0          # a saturated hue near gold (sand, bronze, fire): its complement is used
    for count, index in counts:
        rgb = tuple(pal[index * 3:index * 3 + 3])
        if len(rgb) < 3:
            continue
        h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
        if s < 0.25 or v < 0.25:
            continue  # greys and near-black carry no hue worth building on
        score = count * s
        if _hue_distance(h * 360.0, gold_hue) < GOLD_HUE_CLEARANCE:
            if score > warm_score:
                warm, warm_score = rgb, score
            continue  # too close to the figure colour to be the structure colour
        if score > best_score:
            best, best_score = rgb, score
    if best is None and warm is not None:
        h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in warm))
        complement = tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb((h + 0.5) % 1.0, s, v))
        lifted = _legible(complement)  # type: ignore[arg-type]
        return {"accent": lifted, "accent_hex": "#%02X%02X%02X" % lifted, "sampled": "#%02X%02X%02X" % tuple(warm),
                "contrast": round(contrast(lifted), 1),
                "source": f"complement of the dominant warm hue in {Path(hero).name} (too close to gold to use as is)"}
    if best is None:
        return dict(DEFAULT_PALETTE, source=f"default: no saturated hue in {Path(hero).name}")
    lifted = _legible(best)  # type: ignore[arg-type]
    return {"accent": lifted, "accent_hex": "#%02X%02X%02X" % lifted, "sampled": "#%02X%02X%02X" % tuple(best),
            "contrast": round(contrast(lifted), 1), "source": f"sampled from {Path(hero).name}"}


def type_scale(width: int, height: int) -> Dict[str, int]:
    """Sizes for one frame: figure : title : label : caption : eyebrow, from a base that is a share of the
    frame's shorter side, with the smallest size held at the readable minimum."""
    base = int(min(width, height) * (0.17 if height > width else 0.12))
    floor = int(height * READABLE_FRACTION)
    sizes = {"figure": base, "title": int(base * 0.55), "label": int(base * 0.30), "caption": int(base * 0.26), "eyebrow": int(base * 0.22)}
    return {k: max(v, floor) for k, v in sizes.items()}


def count_start(index: int) -> float:
    """When the index-th figure begins its count-up, so every count rests by count_done_by of the clip."""
    return 0.2 + MOTION["stagger"] * index


def push_in(t: float, clip_seconds: float) -> float:
    """The backdrop's scale at time t: a slow push-in so a card never sits static."""
    return 1.0 + MOTION["push_in"] * max(0.0, min(1.0, t / clip_seconds))


def grade_bias(hero: Optional[Path], limit: float = 0.05) -> float:
    """A bounded warm (+) or cool (-) shift from the hero photograph's mean colour: the difference between
    its red and blue channels, normalised and clipped. 0 when it cannot be measured."""
    if not hero or not Path(hero).is_file():
        return 0.0
    try:
        with Image.open(hero) as im:
            data = im.convert("RGB").resize((32, 32)).tobytes()   # R, G, B interleaved
    except Exception:
        return 0.0
    if len(data) < 3:
        return 0.0
    try:   # a hero whose lamp cast colour_balance reduces (an off-white one, not warm light): a bias would put the
        # cast back (29 Oct, Derinkuyu's door: +0.040). Warm light keeps its warmth, so its bias stays. A balanced
        # copy is judged by its own record: v5 corrects the door so well that it no longer measures as cast.
        import json
        from .lineage import sidecar
        record = sidecar(Path(hero))
        if record.is_file():
            made = json.loads(record.read_text(encoding="utf-8"))
            settings = made.get("settings") or {}
            if made.get("operation") == "colour_balance" and (
                    settings.get("lamp_step") or any(abs(float(g) - 1.0) > 1e-3 for g in settings.get("gains") or [])):
                return 0.0
        import numpy as np
        from . import colour_balance
        with Image.open(hero) as im:
            small = im.convert("RGB")
            small.thumbnail((colour_balance.STAT_SIDE, colour_balance.STAT_SIDE))
            sc = colour_balance.scene(np.asarray(small, dtype=np.float64))
        warm = colour_balance.WARM_CAST_HUE
        if sc["lamp_lit"] and sc["neutral"] is not None and not warm[0] <= colour_balance._hue(sc["neutral"]) <= warm[1]:
            return 0.0
    except Exception:
        pass
    n = len(data) // 3
    r = sum(data[0::3]) / n
    b = sum(data[2::3]) / n
    bias = (r - b) / 255.0 * 0.25          # a strongly warm photo (r-b of 100) gives about +0.1 before the clip
    return round(max(-limit, min(limit, bias)), 3)


def record(run_dir: Path, hero: Optional[Path]) -> Dict[str, Any]:
    """Chooses and records the run's art direction (palette.json), and makes it the active palette."""
    pal = palette_from(hero)
    pal["grade_bias"] = grade_bias(hero)
    use_palette(pal)
    try:
        (Path(run_dir) / "palette.json").write_text(json.dumps(pal, indent=1), encoding="utf-8")
    except OSError as e:
        log.debug("Palette not recorded: %s", e)
    log.info("Art direction: accent %s (%s), grade bias %+.3f.", pal["accent_hex"], pal["source"], pal["grade_bias"])
    return pal
