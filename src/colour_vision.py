"""Colour-vision-deficiency (CVD) legibility of the published graphics (QF-88).

Simulation: Machado, Oliveira & Fernandes, "A Physiologically-based Model for Simulation of Color Vision
Deficiency", IEEE TVCG 15(6), 2009, at severity 1.0 (protanopia, deuteranopia, tritanopia): the authors' 3 x 3
matrices (their Table 1), applied to linear RGB. Measures: CIEDE2000 colour difference (CIE 142-2001; the Sharma,
Wu & Dalal 2005 formulation) and the WCAG 2.x contrast ratio (relative luminance, sRGB transfer).

The gate (`check`): a graphic's colours that carry meaning are its roles (ROLES: the figure gold, the run's accent,
the chart series, the map's home fill and neighbour land). A role is present when at least ROLE_SHARE of the
picture lies within ROLE_TOLERANCE of its nominal colour (CIELAB, lightness down-weighted); its measured colour is the median of those
pixels, so relief shading and the drawn result count, not the token. Every pair of present roles whose difference
carries meaning (MEANING_PAIRS) must stay at least MIN_DE00 apart in CIEDE2000 under each simulation, or the
graphic fails: a viewer with that deficiency would see the two as one. MIN_DE00 (10) is the project's own margin,
not a published threshold: about four times the CIEDE2000 just-noticeable difference for large patches, and below
the 14 measured on the closest real pair (gold against coral, deuteranopia).

Measured and shown as a note, not a block (operator, decisions QF-88-question): WCAG 2.1 SC 1.4.11 non-text
contrast, 3:1, of each mark role against the ground around it (MARK_ROLES; the ground is the median of a band
NOTE_BAND px outside the mark's pixels), in normal vision and under each simulation. Today's maps and routes fall
short of it in normal vision; that goes to the design freeze loop as a criterion.
"""

from pathlib import Path

import numpy as np

from . import brand

MACHADO_2009 = {
    "protanopia": np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216],
                            [-0.003882, -0.048116, 1.051998]]),
    "deuteranopia": np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413],
                              [-0.011820, 0.042940, 0.968881]]),
    "tritanopia": np.array([[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602],
                            [0.004733, 0.691367, 0.303900]]),
}
VISIONS = ("normal", *MACHADO_2009)

MIN_DE00 = 10.0          # the project's margin (module docstring)
NONTEXT_MIN = 3.0        # WCAG 2.1 SC 1.4.11
ROLE_TOLERANCE = 12.0    # CIELAB distance from a role's nominal colour, lightness divided by ROLE_LIGHTNESS_WEIGHT
ROLE_SHARE = 0.0002      # of the picture's pixels for a role to be present (a thin outline, a year's figures)
# How much a role's lightness may vary as drawn: a map's home fill is shaded by the relief, a mark is anti-aliased;
# the neighbour land keeps weight 1 so the darker sea is not taken for it.
ROLE_LIGHTNESS_WEIGHT = {"home_fill": 2.5, "land": 1.0}
MARK_LIGHTNESS_WEIGHT = 1.5
NOTE_BAND = (3, 9)       # px outside a mark: the ground it is read against
SAMPLE_SIDE = 960        # the picture is read at most this many px on its long side

# Map tints are graphics.LAND_TINT and HOME_TINT (kept here so this module does not import the renderer).
LAND_TINT, HOME_TINT = (88, 92, 100), (168, 137, 81)
ROLES = {"gold": brand.GOLD, "teal": brand.TEAL, "coral": brand.CORAL, "home_fill": HOME_TINT,
         "land": LAND_TINT}
# The figure gold against the accent structure ("so figures and structure never merge": art_direction), the chart
# series against each other, and the map's home country against its neighbours.
MEANING_PAIRS = (("gold", "accent"), ("gold", "teal"), ("gold", "coral"), ("teal", "coral"), ("home_fill", "land"))
# A pair that means something only on some graphics: the home fill and the neighbour land are a map's (a locator or
# a route). Elsewhere their colours occur by chance in a photograph (a stat card's ground, a cover), and two photo
# colours are not a meaning pair: measured on real stills, they matched as home/land and fell under the margin.
PAIR_KINDS = {("home_fill", "land"): ("map", "route")}
MARK_ROLES = ("gold", "accent", "teal", "coral")


def linear(rgb) -> np.ndarray:
    c = np.asarray(rgb, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def simulate(lin: np.ndarray, vision: str) -> np.ndarray:
    """Linear RGB (..., 3) as seen with `vision` (Machado 2009, severity 1.0)."""
    if vision == "normal":
        return lin
    return np.clip(lin @ MACHADO_2009[vision].T, 0.0, 1.0)


def luminance(lin: np.ndarray) -> np.ndarray:
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def contrast(a: np.ndarray, b: np.ndarray) -> float:
    la, lb = float(luminance(a)), float(luminance(b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def lab(lin: np.ndarray) -> np.ndarray:
    """CIELAB (D65) of linear sRGB."""
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    d = 6 / 29
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def de00(lab1, lab2) -> float:
    """CIEDE2000 (kL = kC = kH = 1), as Sharma, Wu & Dalal 2005 give it."""
    L1, a1, b1 = (float(v) for v in lab1)
    L2, a2, b2 = (float(v) for v in lab2)
    c_mean = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_mean ** 7 / (c_mean ** 7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360 if c2p else 0.0
    dl, dc = L2 - L1, c2p - c1p
    dh = 0.0 if c1p * c2p == 0 else (h2p - h1p + 180) % 360 - 180
    d_h = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))
    l_mean, cp_mean = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_mean = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_mean = (h1p + h2p) / 2
    else:
        h_mean = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (1 - 0.17 * np.cos(np.radians(h_mean - 30)) + 0.24 * np.cos(np.radians(2 * h_mean))
         + 0.32 * np.cos(np.radians(3 * h_mean + 6)) - 0.20 * np.cos(np.radians(4 * h_mean - 63)))
    rotation = -np.sin(np.radians(60 * np.exp(-((h_mean - 275) / 25) ** 2))) * 2 * np.sqrt(
        cp_mean ** 7 / (cp_mean ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (l_mean - 50) ** 2 / np.sqrt(20 + (l_mean - 50) ** 2)
    sc, sh = 1 + 0.045 * cp_mean, 1 + 0.015 * cp_mean * t
    return float(np.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (d_h / sh) ** 2 + rotation * (dc / sc) * (d_h / sh)))


def pair_distance(a_rgb, b_rgb) -> dict:
    """CIEDE2000 between two colours (0-255 RGB) in each vision."""
    a, b = linear(a_rgb), linear(b_rgb)
    return {v: round(de00(lab(simulate(a, v)), lab(simulate(b, v))), 1) for v in VISIONS}


def roles_for(accent) -> dict:
    """The roles of a graphic drawn with this run's accent (one role per distinct colour)."""
    roles = dict(ROLES)
    accent = tuple(int(c) for c in accent) if accent is not None else None
    if accent and accent not in {tuple(v) for v in roles.values()}:
        roles["accent"] = accent
    return roles


def _picture(image) -> np.ndarray:
    from PIL import Image
    im = image if isinstance(image, Image.Image) else Image.open(image)
    im = im.convert("RGB")
    scale = SAMPLE_SIDE / max(im.size)
    if scale < 1:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.NEAREST)
    return np.asarray(im)


def kind_of(path) -> str:
    """A graphic's kind from its file name (graphic_<kind>.jpg), as graphic_match names it."""
    stem = Path(path).stem
    return stem[len("graphic_"):].split("_")[0] if stem.startswith("graphic_") else ""


def check(image, accent=None, kind=None) -> dict:
    """The CVD check of one rendered graphic (a path or PIL image; `kind`: map, route, numbers, ..., read from a
    path's name when not given; PAIR_KINDS limits a pair to its kinds): its present roles, every meaning pair's
    smallest CIEDE2000 across the simulations (`pairs`, failing under MIN_DE00), and the WCAG 1.4.11 note
    (`nontext`: each mark's smallest contrast against its ground across the visions)."""
    import cv2
    if kind is None and isinstance(image, (str, Path)):
        kind = kind_of(image)
    rgb = _picture(image)
    lin = linear(rgb)
    pixels_lab = lab(lin)
    roles = roles_for(accent)
    masks, measured = {}, {}
    for name, nominal in roles.items():
        offset = pixels_lab - lab(linear(nominal))
        offset[..., 0] /= ROLE_LIGHTNESS_WEIGHT.get(name, MARK_LIGHTNESS_WEIGHT)
        mask = np.linalg.norm(offset, axis=-1) <= ROLE_TOLERANCE
        if mask.mean() >= ROLE_SHARE:
            masks[name] = mask
            measured[name] = np.median(lin[mask], axis=0)
    pairs = []
    for a, b in MEANING_PAIRS:
        if (a, b) in PAIR_KINDS and kind not in PAIR_KINDS[(a, b)]:
            continue
        if a in measured and b in measured:
            dist = {v: round(de00(lab(simulate(measured[a], v)), lab(simulate(measured[b], v))), 1) for v in VISIONS}
            worst = min(dist, key=dist.get)
            pairs.append({"pair": [a, b], "de00": dist, "worst_vision": worst, "pass": dist[worst] >= MIN_DE00})
    nontext = []
    inner = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * NOTE_BAND[0] + 1,) * 2)
    outer = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * NOTE_BAND[1] + 1,) * 2)
    for name in MARK_ROLES:
        if name not in masks:
            continue
        m = masks[name].astype(np.uint8)
        band = (cv2.dilate(m, outer) > 0) & ~(cv2.dilate(m, inner) > 0)
        for other_name, other in masks.items():
            if other_name != name:
                band &= ~other
        if band.sum() < 8:
            continue
        ground = np.median(lin[band], axis=0)
        ratios = {v: round(contrast(simulate(measured[name], v), simulate(ground, v)), 2) for v in VISIONS}
        worst = min(ratios, key=ratios.get)
        nontext.append({"mark": name, "contrast": ratios, "worst_vision": worst, "meets": ratios[worst] >= NONTEXT_MIN})
    return {"roles": sorted(measured), "pairs": pairs, "nontext": nontext,
            "pass": all(p["pass"] for p in pairs)}


def accent_problem(accent) -> str | None:
    """Why an accent would merge with the figure gold for a colour-blind viewer, else None (the gate's own pair
    rule, on the nominal colours: art_direction rejects such an accent at the source)."""
    dist = pair_distance(accent, ROLES["gold"])
    worst = min(dist, key=dist.get)
    if dist[worst] < MIN_DE00:
        return f"{dist[worst]:.1f} dE00 from the figure gold under {worst} (needs {MIN_DE00:.0f})"
    return None


_CACHE: dict = {}


def run_check(run_dir: Path, accent=None) -> list:
    """The check of every rendered graphic in a run (graphics/graphic_*.jpg, each clip's drawn last frame). The
    review card re-runs the gate each time it is shown: a graphic already checked with the same bytes and accent is
    not read again (the key is its sha256)."""
    from .credits import sha256_file
    out = []
    for p in sorted(Path(run_dir).glob("graphics/graphic_*.jpg")):
        key = (sha256_file(p), tuple(accent) if accent is not None else None)
        if key not in _CACHE:
            _CACHE[key] = check(p, accent)
        out.append({"graphic": p.name, **_CACHE[key]})
    return out
