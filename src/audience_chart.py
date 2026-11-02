"""
src/audience_chart.py
=====================
The audience chart sent to Telegram: views 7 days after posting for the last six Reels, one row per Reel
(newest on top), one marker per platform (Instagram, YouTube Shorts, Facebook) on a shared linear axis.

Design (critic round 4, 2026-10-29, C:/tmp/visual_review/critic_round4/final_chart.png): a dot plot on ink,
gold/coral/teal series that a marker shape also carries (circle, triangle, square), so the series stay apart
for a colour-blind reader (cvd_separation() measures it) and in a greyscale preview. Changed from the critic's
render, 2026-10-29: every text is at least 36 px on the 1200 px canvas, 12 px at Telegram's ~400 px preview
(the critic's footer and figures were 26-28 px, 9 px in the preview); each row carries the Reel's real title
(the critic drew "REEL 6") and its exact figures, because an axis alone cannot be read at 400 px.

The numbers are the stored 7-day snapshots in audience_metrics (src/audience.py) and nothing else: with no
rows the chart says so. This module holds no sample values; `sample=True` only watermarks a render whose
values the caller supplied (tests and review evidence), twice, as the critic's did. The headline is computed
from the rows (headline()), never written by hand.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from . import typography
from .config import FONTS_DIR

# ------------------------------------------------------------------ brand (critic round 4, brand4.py)
INK = (11, 14, 18)
BONE = (243, 237, 226)
STONE_LT = (184, 177, 163)
GOLD = (242, 182, 48)
TEAL = (79, 179, 191)
CORAL = (232, 115, 90)
GRID = (38, 42, 49)
BAR = (48, 53, 61)

# Legend and figure order follows the critic's key: Instagram, YouTube, Facebook.
PLATFORMS: Tuple[Tuple[str, str, Tuple[int, int, int], str], ...] = (
    ("instagram", "Instagram", GOLD, "circle"),
    ("youtube", "YouTube", CORAL, "triangle"),
    ("facebook", "Facebook", TEAL, "square"),
)
NAMES = {p: n for p, n, _, _ in PLATFORMS}
# audience.dashboard() channels: YouTube is the Shorts channel; the long documentary is not a Reel.
CHANNEL = {"instagram": "ig", "youtube": "yt", "facebook": "fb"}

W = 1200                 # canvas width; Telegram shows a photo at ~400 px in the chat, a third of this
PREVIEW_W = 400
MIN_TEXT_AT_PREVIEW = 12  # px at the preview width (operator gate, 2026-10-29)
TEXT = 36                # every small text: 36 px here is 12 px in the preview
MARGIN = 80
SS = 2                   # supersampling: PIL draws shapes without anti-aliasing, so draw at 2x and reduce
SNAPSHOT = "7d"
LIMIT = 6
# Each platform counts a "view" its own way (Instagram and Facebook Reels plays, YouTube Shorts views; _views() reads
# whichever field the snapshot has), so the chart says so under the figures, in words that name no definition the
# code has not checked (round-3 panel, standards editor, 2026-11-02).
FOOTNOTE = "EACH PLATFORM COUNTS A VIEW ITS OWN WAY"
NEAR = 24                 # px: markers closer than this on one row (about one marker's radius) are fanned out (_spread)
SPREAD = 11               # px each step of that spread moves a marker off the row's line
DATE_SEP = " \u00b7 "      # between a row's post date and its title

MONO_FILES = ("IBMPlexMono-SemiBold.ttf", "IBMPlexMono-Medium.ttf")


# ------------------------------------------------------------------ data
def _views(metrics: Dict[str, Any]) -> Optional[int]:
    """The view count in a stored snapshot. audience.derived() reads `views or plays`, which turns a real
    0 into None (2026-10-29); a Reel nobody watched is a 0 on the chart, not a gap."""
    if metrics.get("unavailable"):
        return None
    for key in ("views", "fb_reels_total_plays", "plays", "blue_reels_play_count"):
        v = metrics.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return None


def reels_at_7d(db, limit: int = LIMIT) -> List[Dict[str, Any]]:
    """The newest `limit` Reels that have a 7-day figure on at least one platform, newest first:
    [{"run_id", "title", "posted_at", "views": {instagram|youtube|facebook: int | None}}]. Only stored rows;
    a Reel younger than 7 days is not on the chart yet."""
    from . import audience
    videos = audience.dashboard(db)  # newest posting first, titles and formats resolved there
    with db._conn() as conn:
        rows = conn.execute("SELECT run_id, platform, metrics_json FROM audience_metrics WHERE snapshot = ?",
                            (SNAPSHOT,)).fetchall()
    stored: Dict[Tuple[str, str], Optional[int]] = {}
    for r in rows:
        try:
            stored[(r["run_id"], r["platform"])] = _views(json.loads(r["metrics_json"] or "{}"))
        except ValueError:
            continue
    out = []
    for v in videos:
        if v.get("format") != "reel":
            continue
        views = {p: stored.get((v["run_id"], p)) for p in NAMES}
        if all(x is None for x in views.values()):
            continue
        out.append({"run_id": v["run_id"], "title": v.get("title") or v["run_id"], "posted_at": v.get("posted_at"),
                    "views": views})
        if len(out) == limit:
            break
    return out


# ------------------------------------------------------------------ words
def _names(platforms: Sequence[str]) -> str:
    names = [NAMES[p] for p in platforms]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def headline(reels: Sequence[Dict[str, Any]]) -> str:
    """The chart's claim, counted from the rows. A Reel is compared only where two or more platforms have a
    figure, and a tie (or all zeros) has no leader, so "leads" is never said of a Reel nobody won."""
    if not reels:
        return "No audience data yet"
    present = [p for p in NAMES if any(r["views"].get(p) is not None for r in reels)]
    if len(present) == 1:
        return f"Only {NAMES[present[0]]} has figures yet"
    leads = {p: 0 for p in NAMES}
    compared = 0
    for r in reels:
        vals = {p: v for p, v in r["views"].items() if v is not None}
        if len(vals) < 2:
            continue
        compared += 1
        top = max(vals.values())
        winners = [p for p, v in vals.items() if v == top]
        if len(winners) == 1 and top > 0:
            leads[winners[0]] += 1
    if not compared:
        return "No Reel has two platforms to compare yet"
    best = max(leads.values())
    if best == 0:
        return "No platform leads yet"
    tops = [p for p in NAMES if leads[p] == best]
    if len(tops) > 1:
        return f"{_names(tops)} lead {best} Reel{'s' if best != 1 else ''} each"
    name = NAMES[tops[0]]
    if best == compared:
        if compared == 1:
            return f"{name} leads on the one Reel " + ("so far" if len(reels) == 1 else "compared")
        return f"{name} leads on every Reel"
    return f"{name} leads on {best} of {compared} Reels"


def _count(n: int) -> str:
    """A small count in words, so a line never wraps leaving a bare "1" at its end."""
    return ("One", "Two", "Three", "Four", "Five", "Six")[n - 1] if 1 <= n <= 6 else str(n)


def subtitle(reels: Sequence[Dict[str, Any]]) -> str:
    if not reels:
        return ("Each Reel's views are counted 7 days after posting; the chart fills in from the first one "
                "measured.")
    n = len(reels)
    text = ("Views 7 days after posting; more Reels join as they reach 7 days." if n == 1 else
            f"Views 7 days after posting, last {n} Reels, newest on top.")
    missing = [p for p in NAMES if all(r["views"].get(p) is None for r in reels)]
    if missing:
        text += f" No {_names(missing)} figures yet."
    single = sum(1 for r in reels if sum(v is not None for v in r["views"].values()) == 1)
    if single and len(missing) < len(NAMES) - 1:
        text += f" {_count(single)} Reel{'s have' if single != 1 else ' has'} one platform only, not compared."
    return text


def figure(v: Optional[float]) -> str:
    """A row's figure: 3,120; 12.4K; 125K; 1.28M; 12.5M; 2.4B; an em dash for no figure."""
    if v is None:
        return "\u2014"
    a = abs(v)
    if a < 10_000:
        return f"{int(round(v)):,}"
    if a < 999_500:
        k = v / 1000
        s = f"{k:.1f}" if a < 99_950 else f"{k:.0f}"
        return s.rstrip("0").rstrip(".") + "K" if "." in s else s + "K"
    unit, m = ("M", v / 1_000_000) if a < 999_500_000 else ("B", v / 1_000_000_000)
    s = f"{m:.2f}" if abs(m) < 9.995 else f"{m:.1f}" if abs(m) < 99.95 else f"{m:.0f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + unit


def axis_label(v: float, top: Optional[float] = None) -> str:
    """A tick's label in the unit of the axis's top tick, so one axis never mixes 7,500 with 10K."""
    top = v if top is None else top
    if top < 10_000 or v == 0:
        return f"{int(v):,}"
    if top < 1_000_000:
        return f"{v / 1000:g}K"
    if top < 1_000_000_000:
        return f"{v / 1_000_000:g}M"
    return f"{v / 1_000_000_000:g}B"


def ticks(vmax: float, steps: int = 4) -> List[float]:
    """0 and two to `steps` even steps (1, 2, 2.5 or 5 times a power of ten) reaching at least vmax, the
    lowest such top first (more ticks on a tie): 4,100 is drawn to 5,000, not 6,000 (2026-10-29)."""
    if vmax <= 0:
        return [0, 5, 10]
    mag = 10 ** math.floor(math.log10(vmax / steps))
    best = None
    for step in sorted({max(m * mag * k, 1) for m in (1, 2, 2.5, 5) for k in (1, 10)}):
        if step != int(step):  # views are whole numbers: no 2.5 step under 10
            continue
        step = int(step)
        n = math.ceil(vmax / step - 1e-9)
        if 2 <= n <= steps:
            key = (n * step, -n)
            if best is None or key < best[0]:
                best = (key, step, n)
    _, step, n = best if best else (None, 1, max(2, math.ceil(vmax)))
    return [i * step for i in range(n + 1)]


# ------------------------------------------------------------------ colour checks
def _lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: Sequence[float]) -> float:
    r, g, b = (_lin(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Sequence[float], b: Sequence[float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Machado, Oliveira and Fernandes (2009), severity 1.0, on linear RGB: the matrices the critic's cvd.json used.
CVD = {"protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
       "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
       "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900))}


def _lab(lin: Sequence[float]) -> Tuple[float, float, float]:
    r, g, b = (min(max(x, 0.0), 1.0) for x in lin)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def cvd_separation() -> Dict[str, Dict[str, float]]:
    """CIE76 delta E between each pair of series colours, as seen with normal vision and simulated protan,
    deutan and tritan vision; plus each series' contrast against ink."""
    out: Dict[str, Dict[str, float]] = {}
    ident = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    for kind, m in [("normal", ident)] + list(CVD.items()):
        labs = {}
        for p, _, c, _ in PLATFORMS:
            lin = [_lin(x) for x in c]
            labs[p] = _lab([sum(m[i][j] * lin[j] for j in range(3)) for i in range(3)])
        keys = list(labs)
        out[kind] = {f"{a}~{b}": round(math.dist(labs[a], labs[b]), 1)
                     for i, a in enumerate(keys) for b in keys[i + 1:]}
    out["contrast_vs_ink"] = {p: round(contrast(c, INK), 2) for p, _, c, _ in PLATFORMS}
    return out


# ------------------------------------------------------------------ fonts
def _mono_paths(mono_dir: Optional[Path]) -> Optional[Tuple[Path, Path]]:
    for d in [Path(mono_dir)] if mono_dir else [FONTS_DIR]:
        paths = tuple(d / f for f in MONO_FILES)
        if all(p.exists() for p in paths):
            return paths  # type: ignore[return-value]
    return None


class _Fonts:
    """IBM Plex Mono for every figure once it is in fonts/ (another change adds it, 2026-10-29); until then
    Montserrat SemiBold, the repo's label face, so the chart never fails for a missing file."""

    def __init__(self, mono_dir: Optional[Path] = None):
        self.mono_paths = _mono_paths(mono_dir)
        self.mono_name = "IBM Plex Mono" if self.mono_paths else "Montserrat (fallback)"

    def mono(self, size: int, medium: bool = False) -> ImageFont.FreeTypeFont:
        if self.mono_paths:
            return ImageFont.truetype(str(self.mono_paths[1 if medium else 0]), size)
        return typography.get_font("Montserrat", size, weight=500 if medium else 600)

    @staticmethod
    def sans(size: int, weight: int = 600) -> ImageFont.FreeTypeFont:
        return typography.get_font("Montserrat", size, weight=weight)


# ------------------------------------------------------------------ drawing
class _Canvas:
    """Draws at SS times the design size; records every text's design-pixel box, colour and size so tests
    can measure what the image actually carries."""

    def __init__(self, height: int):
        self.h = height
        self.im = Image.new("RGB", (W * SS, height * SS), INK)
        self.d = ImageDraw.Draw(self.im)
        self.texts: List[Dict[str, Any]] = []

    def text(self, xy, s: str, font, fill, anchor="ls", clear=False):
        x, y = xy[0] * SS, xy[1] * SS
        if not s:
            return None
        bb = self.d.textbbox((x, y), s, font=font, anchor=anchor)
        if clear:  # an ink ground under a title, so gridlines never run through its letters
            self.d.rectangle((bb[0] - 8 * SS, bb[1] - 6 * SS, bb[2] + 8 * SS, bb[3] + 6 * SS), fill=INK)
        self.d.text((x, y), s, font=font, fill=fill, anchor=anchor)
        box = tuple(v / SS for v in bb)
        self.texts.append({"text": s, "box": box, "fill": fill, "size": font.size / SS})
        return box

    def width(self, s: str, font) -> float:
        return typography.line_width(self.d, s, font) / SS

    def marker(self, kind: str, cx: float, cy: float, r: float, fill, outline: float = 3):
        cx, cy = cx * SS, cy * SS
        for rad, colour in ((r + outline, INK), (r, fill)) if outline else ((r, fill),):
            rad *= SS
            if kind == "circle":
                self.d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=colour)
            elif kind == "square":
                h = rad * 0.86  # same visual weight as the circle
                self.d.rectangle((cx - h, cy - h, cx + h, cy + h), fill=colour)
            else:
                t = rad * 1.18  # an equilateral triangle on its centroid, a little larger for equal weight
                self.d.polygon([(cx, cy - t), (cx + t * 0.866, cy + t / 2), (cx - t * 0.866, cy + t / 2)], fill=colour)

    def line(self, x0, y0, x1, y1, fill, width):
        self.d.line((x0 * SS, y0 * SS, x1 * SS, y1 * SS), fill=fill, width=int(width * SS))

    def rect(self, box, fill):
        self.d.rectangle(tuple(v * SS for v in box), fill=fill)

    def finish(self) -> Image.Image:
        return self.im.resize((W, self.h), Image.LANCZOS)


def row_date(posted_at: Any) -> str:
    """A Reel's post date as the row shows it ("28 OCT"), in the operator's zone; "" with no or an unreadable date."""
    if posted_at in (None, ""):
        return ""
    from . import local_time
    try:
        return local_time.local(posted_at).strftime("%d %b").upper().lstrip("0")
    except (TypeError, ValueError, OverflowError):
        return ""


def _spread(xs: Sequence[tuple[str, float]], near: float = NEAR, step: float = SPREAD) -> dict[str, float]:
    """Vertical offsets for one row's markers: a run of markers each within `near` px of the next (equal or nearly
    equal values) is fanned about the row's line, one `step` apart, so every shape in it stays visible; the shape is
    what keeps gold and coral apart for a deutan reader (round-3 panel, 2026-11-02). x never moves: it is the value."""
    out = {p: 0.0 for p, _ in xs}
    ordered = sorted(xs, key=lambda t: t[1])
    groups: list[list[str]] = []
    last = None
    for p, x in ordered:
        if last is not None and x - last < near:
            groups[-1].append(p)
        else:
            groups.append([p])
        last = x
    for g in groups:
        if len(g) > 1:
            for i, p in enumerate(g):
                out[p] = (i - (len(g) - 1) / 2) * step * (2 if len(g) == 2 else 1)
    return out


def _date(today: Optional[datetime]) -> str:
    if today is None:
        from . import local_time
        today = local_time.local()
    return today.strftime("%d %b %Y").upper().lstrip("0")


def render(reels: Sequence[Dict[str, Any]], out_path: Optional[Path] = None, *, sample: bool = False,
           today: Optional[datetime] = None, mono_dir: Optional[Path] = None) -> Tuple[Image.Image, Dict[str, Any]]:
    """The chart for `reels` (reels_at_7d's shape, newest first) as a 1200 px wide image, saved as PNG when
    `out_path` is given. Returns (image, info): info has the headline, the drawn texts with their boxes and
    colours, the fonts used and the layout figures the tests check."""
    reels = list(reels)[:LIMIT]
    fonts = _Fonts(mono_dir)
    probe = _Canvas(1)
    head = headline(reels).upper()
    # One line when the headline fits at 64 px or more; two at the full size otherwise, never a cut claim.
    head_lines, head_font, cut = typography.fit_text(probe.d, head, "Anton", 84 * SS, (W - 2 * MARGIN) * SS, 1, 64 * SS,
                                                     sentences=False, quiet=True)
    if cut:
        head_lines, head_font, _ = typography.fit_text(probe.d, head, "Anton", 84 * SS, (W - 2 * MARGIN) * SS, 2,
                                                       64 * SS, sentences=False, quiet=True)
    sub_font = fonts.sans(TEXT * SS, 500)
    sub_lines = typography.wrap_text(probe.d, subtitle(reels), sub_font, (W - 2 * MARGIN) * SS)
    present = [p for p in PLATFORMS if any(r["views"].get(p[0]) is not None for r in reels)]

    head_lh, sub_lh, row_h = head_font.size / SS * 1.08, 46, 124
    y_kicker = 76
    y_head = y_kicker + 26 + head_font.size / SS * 0.92
    y_sub = y_head + head_lh * (len(head_lines) - 1) + 56
    y_after_sub = y_sub + sub_lh * (len(sub_lines) - 1)
    y_legend = y_after_sub + 70 if reels else y_after_sub
    y_rows = y_legend + 52
    y_axis = y_rows + row_h * len(reels) + 4
    y_note = y_axis + 70 if reels else None
    y_foot = (y_axis + 70 + 50 if reels else y_after_sub + 120)
    height = int(y_foot + (50 if sample else 0) + 52)

    c = _Canvas(height)
    # kicker row: the section in gold; the watermark on the right when the values are not real
    c.rect((MARGIN, y_kicker - 30, MARGIN + 10, y_kicker), GOLD)
    c.text((MARGIN + 26, y_kicker), "AUDIENCE", fonts.sans(TEXT * SS, 600), GOLD)
    if sample:
        c.text((W - MARGIN, y_kicker), "SAMPLE DATA", fonts.mono(TEXT * SS), CORAL, anchor="rs")
    for i, ln in enumerate(head_lines):
        c.text((MARGIN, y_head + i * head_lh), ln, head_font, BONE)
    for i, ln in enumerate(sub_lines):
        c.text((MARGIN, y_sub + i * sub_lh), ln, sub_font, STONE_LT)

    info: Dict[str, Any] = {"headline": headline(reels), "subtitle": subtitle(reels), "sample": sample,
                            "mono_font": fonts.mono_name, "rows": len(reels), "platforms": [p[0] for p in present]}
    if reels:
        # the key: marker and name in the series colour, for the platforms that have figures
        x, key_font = MARGIN, fonts.sans(38 * SS, 600)
        for p, name, colour, kind in present:
            c.marker(kind, x + 14, y_legend - 13, 13, colour, outline=0)
            c.text((x + 38, y_legend), name, key_font, colour)
            x += 38 + c.width(name, key_font) + 44

        fig_font = fonts.mono(TEXT * SS)
        title_font = fonts.sans(TEXT * SS, 600)
        axis_font = fonts.mono(TEXT * SS, medium=True)
        # The figures column (2026-10-29): beside each row's dots, one slot per platform, right-aligned to the
        # widest figure in it, its marker next to its own number. The title gets the full width above, so a
        # Reel's name is cut only when it is longer than the canvas, never for the figures.
        slots = []
        right = W - MARGIN
        for p, _, colour, kind in reversed(present):
            wmax = max(c.width(figure(r["views"].get(p)), fig_font) for r in reels)
            # the key marker at one x per slot, left of the slot's widest figure: a column that never wobbles
            slots.append((p, colour, kind, right, right - wmax - 20))
            right -= wmax + 26 + 28
        x0, x1 = MARGIN + 22, right - 34
        vals = [v for r in reels for v in r["views"].values() if v is not None]
        tk = ticks(max(vals) if vals else 0)

        def label_xs(tk_):
            span = x1 - x0
            out = []
            for t in tk_:
                wdt = c.width(axis_label(t, tk_[-1]), axis_font)
                x = x0 + span * (t / tk_[-1] if tk_[-1] else 0)
                out.append((max(x, MARGIN + wdt / 2), wdt))
            return out
        xs = label_xs(tk)
        if any(b[0] - b[1] / 2 - (a[0] + a[1] / 2) < 20 for a, b in zip(xs, xs[1:])):
            tk = ticks(max(vals) if vals else 0, steps=2)  # a narrow plot or long labels: half as many ticks
            xs = label_xs(tk)
        px = lambda v: x0 + (x1 - x0) * (v / tk[-1] if tk[-1] else 0)  # noqa: E731
        for t in tk:
            c.line(px(t), y_rows + 44, px(t), y_axis - 30, GRID, 2)
        # Each row is told apart by its post date as well as its title (round-3 panel, 2026-11-02): the date in a
        # column of its own, as wide as the widest, so every title starts at one x; the title has the rest.
        dates = [row_date(r.get("posted_at")) for r in reels]
        date_font = fonts.mono(TEXT * SS)
        date_w = max((c.width(d + DATE_SEP, date_font) for d in dates if d), default=0)
        info["title_width"] = W - 2 * MARGIN - date_w
        info["title_x"] = MARGIN + date_w
        info["dates"] = dates
        info["plot"] = (x0, x1)
        titles, markers = [], []
        for i, r in enumerate(reels):
            y_t = y_rows + i * row_h + 34
            y_d = y_t + 40  # nearer its own title than the next row's, so each title reads with its dots
            if dates[i]:
                c.text((MARGIN, y_t), dates[i] + DATE_SEP.rstrip(), date_font, STONE_LT, clear=True)
            title, _, _ = typography.fit_line(probe.d, str(r.get("title") or r.get("run_id") or ""), "Montserrat",
                                              TEXT * SS, int(info["title_width"] * SS), TEXT * SS, quiet=True)
            titles.append(title)
            c.text((info["title_x"], y_t), title, title_font, BONE, clear=True)
            for p, colour, kind, rx, kx in slots:
                v = r["views"].get(p)
                s = figure(v)
                c.marker(kind, kx, y_d, 9, colour, outline=0)
                c.text((rx, y_d), s, fig_font, colour if v is not None else STONE_LT, anchor="rm")
            row = [r["views"].get(p) for p, *_ in present if r["views"].get(p) is not None]
            if len(row) > 1:
                c.line(px(min(row)), y_d, px(max(row)), y_d, BAR, 8)
            plotted = [(p, px(r["views"][p])) for p, *_ in present if r["views"].get(p) is not None]
            dy = _spread(plotted)
            at = {}
            # the largest marker first, so a triangle or square on the same value still shows on top
            for p, _, colour, kind in sorted(present, key=lambda q: {"circle": 0, "square": 1, "triangle": 2}[q[3]]):
                v = r["views"].get(p)
                if v is not None:
                    at[p] = (px(v), y_d + dy[p])
                    c.marker(kind, px(v), y_d + dy[p], 17, colour)
            markers.append(at)
        info["titles"] = titles
        info["markers"] = markers
        info["key_x"] = {p: kx for p, _, _, _, kx in slots}
        for t, (x, _) in zip(tk, xs):
            c.text((x, y_axis + 10), axis_label(t, tk[-1]), axis_font, STONE_LT, anchor="ms")
        info["ticks"] = tk
    foot_font = fonts.mono(TEXT * SS, medium=True)
    if y_note is not None:
        c.text((MARGIN, y_note), FOOTNOTE, fonts.mono(TEXT * SS), STONE_LT)
    if sample:
        c.text((MARGIN, y_foot), "SAMPLE DATA, NOT AUDIENCE FIGURES", foot_font, CORAL)
        y_foot += 50
    c.text((MARGIN, y_foot), f"RELIC & RIDDLE  /  {_date(today)}", foot_font, STONE_LT)

    im = c.finish()
    info["texts"] = c.texts
    info["size"] = im.size
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(out_path, "PNG", optimize=True)
    return im, info


def chart_for(db, out_path: Path, today: Optional[datetime] = None) -> Dict[str, Any]:
    """The chart of the stored 7-day figures, saved to `out_path`: what a Telegram call site sends. Returns
    render()'s info plus "caption", the headline as a sentence for the photo's caption."""
    reels = reels_at_7d(db)
    _, info = render(reels, out_path, today=today)
    info["caption"] = info["headline"] + "."
    info["path"] = str(out_path)
    return info
