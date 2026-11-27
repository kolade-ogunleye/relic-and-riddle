"""The FIGURES stream's class measures (design freeze, cycle 5), each over figures_corpus.py's capture records.

Every measure returns rows (run, surface, element, measure, verdict) with verdict one of PASS, FAIL, RECORDED (the
renderer left the part out and recorded it on the omission ledger, or refused the graphic with a recorded refusal) or
NOTE (a ruled exception, named). A class script prints its table and exits non-zero when any row is FAIL.

The measures read only what the capture holds (the renderers' own plates, marks, rulers, omissions and layout
records), so the same instrument measures the base tree and the tip.
"""
from __future__ import annotations

import math
import re
from typing import Any

HEADING = ("subject", "figure", "figure_metric")
RULER_ROLE = re.compile(r"^(ruler\d*|axis\d*|tick\d*|scale|scale_secondary|scale_bar.*)$")
DIMENSION = {"DEEP", "TALL", "LONG", "HIGH", "WIDE"}
QUALIFIERS = {"ABOUT", "NEARLY", "ROUGHLY", "AROUND", "ALMOST", "APPROXIMATELY", "OVER", "UNDER", "UP", "TO", "EST.",
              "EST", "ESTIMATED", "C.", "CIRCA", "AN"}
UNIT_GAP = (0.2, 0.5)     # FIG-03: a number's ink to its unit's, in caps of the number (one thin-space token)
SPAN_CAPTION = re.compile(r"^gap\d+(_\d+)?$")      # a timeline's elapsed-time caption on the spine (derived, recorded)


def surface(rec: dict[str, Any]) -> str:
    return f"{rec['kind']}__{rec['format']}"


def _status(rec: dict[str, Any]) -> tuple | None:
    """A graphic that was refused: one RECORDED row (a recorded refusal), or FAIL for an error."""
    if rec.get("error"):
        return (rec["run"], surface(rec), "-", "error: " + rec["error"].strip().splitlines()[-1][:110], "FAIL")
    if rec.get("refused"):
        return (rec["run"], surface(rec), "-", "refused: " + str(rec["refused"].get("text"))[:110], "RECORDED")
    if not rec.get("drawn"):
        return (rec["run"], surface(rec), "-", "the renderer drew nothing (no input in range)", "NOTE")
    return None


def tokens(tree_graphics, palette) -> dict[str, str]:
    """The figure rule's faces and colours under `palette`, as this tree resolves them."""
    from src import brand
    from src.art_direction import use_palette
    g = tree_graphics
    use_palette(palette)
    try:
        hexs = lambda c: "#%02X%02X%02X" % tuple(c[:3])    # noqa: E731
        return {"figure_face": " ".join(g.figure_font(40).getname()), "figure_fill": hexs(g.figure_fill()),
                "bone": hexs(brand.BONE), "data_face": " ".join(g.data_font(20).getname())}
    finally:
        use_palette(None)


def _is_quantity_label(text: str) -> bool:
    from src import figures
    spans = figures.quantity_spans(text)
    if not any(any(ch.isdigit() for ch in text[a:b]) for a, b in spans):
        return False
    rest = text
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    return all(w.strip(",;:").upper() in QUALIFIERS | DIMENSION for w in rest.split())


def _has_hedge(text: str) -> bool:
    return any(w.strip(",;:").upper() in QUALIFIERS for w in text.split())


# --------------------------------------------------------------------------------------------- FIG-01 / FIG-03
def quantity_rows(rec: dict[str, Any], tok: dict[str, str]) -> list[tuple]:
    """Every stated quantity through the one figure rule (operator rulings 3 and 8): the figure face; number and unit
    in the figure colour, qualifiers in bone; the data face only on rulers, axes and scale bars, which carry no hedge
    and are never the figure colour."""
    st = _status(rec)
    if st:
        return [st]
    from src import figures
    rows = []
    for e in rec["elements"]:
        text, name = e["text"], e["name"]
        if not any(ch.isdigit() for ch in text):
            continue
        faces = sorted({r["face"] for r in e["runs"]})
        fills = sorted({r["fill"] for r in e["runs"]})
        where = (rec["run"], surface(rec), f"{name} {text!r}")
        if RULER_ROLE.match(name):
            if _has_hedge(text) or tok["figure_fill"] in fills:
                rows.append((*where, f"a stated quantity on the ruler path: {faces[0]} {'/'.join(fills)}", "FAIL"))
            elif faces != [tok["data_face"]] and faces != ["(drawn mask)"]:
                rows.append((*where, f"an axis figure outside the data face: {faces}", "FAIL"))
            else:
                gap, ok = "", True
                if len(e["runs"]) == 2:
                    a, b = e["runs"]
                    cap = max(1, a["box"][3] - a["box"][1])
                    g = (b["box"][0] - a["box"][2]) / cap
                    ok = UNIT_GAP[0] <= g <= UNIT_GAP[1]          # FIG-03: one unit-space token, judged
                    gap = f"; unit gap {g:.2f} cap (need {UNIT_GAP[0]}-{UNIT_GAP[1]})"
                rows.append((*where, f"axis figure in the data face{gap}", "PASS" if ok else "FAIL"))
            continue
        if not _is_quantity_label(text):
            continue
        if SPAN_CAPTION.match(name):
            rows.append((*where, f"elapsed-time span caption in the text face ({faces[0]}): a caption on the spine, "
                                 "derived and recorded; not a stated quantity label (decision, hand-back)", "NOTE"))
            continue
        bad = []
        spans = figures.quantity_spans(text)
        for r in e["runs"]:
            at = text.find(r["text"])
            gold = any(ch.isdigit() for ch in r["text"]) or any(a <= at < b for a, b in spans if at >= 0)
            if r["face"] != tok["figure_face"]:
                bad.append(f"{r['text']!r} in {r['face']}")
            elif gold and r["fill"] != tok["figure_fill"]:
                bad.append(f"{r['text']!r} figure in {r['fill']}")
            elif not gold and r["fill"] != tok["bone"]:
                bad.append(f"qualifier {r['text']!r} in {r['fill']}")
        gaps = [g for r in e["runs"] for g in [_figure_unit_gap(r)] if g is not None]
        bad += [f"unit gap {g:.2f} cap outside {UNIT_GAP[0]}-{UNIT_GAP[1]}" for g in gaps
                if not UNIT_GAP[0] <= g <= UNIT_GAP[1]]
        gap_note = f"; unit gap {min(gaps):.2f}-{max(gaps):.2f} cap" if gaps else ""
        rows.append((*where, "; ".join(bad) or "figure face, number and unit in the figure colour, qualifier bone"
                     + gap_note, "FAIL" if bad else "PASS"))
    return rows


def _figure_unit_gap(run: dict[str, Any]) -> float | None:
    """FIG-03: a figure-face run's ink gap between its number and its unit ("280 FT"), in caps, from the tree's
    figure face at the run's size (untracked: the plate's tracking adds at most 2 px a glyph)."""
    m = re.match(r"^(.*\d)\s+([A-Z]{1,3})$", str(run.get("text") or ""))
    if not m or not run.get("px"):
        return None
    from src import graphics
    f = graphics.figure_font(int(run["px"]))
    nb, ub, cap = f.getbbox(m.group(1)), f.getbbox(m.group(2)), f.getbbox("H")
    return (f.getlength(m.group(1) + " ") + ub[0] - nb[2]) / max(1, cap[3] - cap[1])


# --------------------------------------------------------------------------------------------- FIG-02 / FIG-04
def _group(kind: str, name: str) -> str:
    if kind == "section":
        m = re.match(r"(level\d+)_", name)
        if m:
            return m.group(1)
    if name in HEADING or re.match(r"(subject|figure)\d*$", name):
        return "heading"
    if name.startswith("ref_"):
        return "ref"
    return re.sub(r"[\d_]+$", "", name) if re.search(r"\d$", name) and not RULER_ROLE.match(name) else name


def _gap(a, b) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dy)


def collision_rows(rec: dict[str, Any]) -> list[tuple]:
    """No two label ink boxes of different groups overlap; on the drawings (scale, section) an axis figure keeps at
    least half its cap from every other label (panel 6: "300" against the floor's figure)."""
    st = _status(rec)
    if st:
        return [st]
    els = [e for e in rec["elements"] if e["box"][2] > e["box"][0]]
    rows, worst = [], None
    for i, a in enumerate(els):
        for b in els[i + 1:]:
            ga, gb = _group(rec["kind"], a["name"]), _group(rec["kind"], b["name"])
            if ga == gb:
                continue
            d = _gap(a["box"], b["box"])
            need = 0.0
            if rec["kind"] in ("scale", "section") and (RULER_ROLE.match(a["name"]) or RULER_ROLE.match(b["name"])):
                need = 0.5 * min(a["box"][3] - a["box"][1], b["box"][3] - b["box"][1])
            if d <= 0 or d < need:
                rows.append((rec["run"], surface(rec), f"{a['name']} {a['text']!r} / {b['name']} {b['text']!r}",
                             f"{d:.0f} px apart (need > {need:.0f})", "FAIL"))
            if need and (worst is None or d - need < worst[0]):
                worst = (d - need, a["name"], b["name"], d, need)
    if not rows:
        rows.append((rec["run"], surface(rec), f"{len(els)} labels",
                     "no label on another" + (f"; nearest axis pair {worst[1]}/{worst[2]} {worst[3]:.0f} px "
                                              f"(need {worst[4]:.0f})" if worst else ""), "PASS"))
    return rows


def section_rows(rec: dict[str, Any]) -> list[tuple]:
    """The cross-section's label blocks stand apart as groups (the clear air between two blocks at least 1.5 x the
    widest clear air between rows inside a block), every leader ends on its OWN figure (its mask's label end inside
    the figure's cap band, at most 16 design px left of its ink), and every word is inside the frame's text box."""
    st = _status(rec)
    if st:
        return [st]
    from src import graphics
    w, h = rec["size"]
    k = min(w / 1080, h / 1920) if h > w else min(w / 1920, h / 1080)
    rows = []
    blocks: dict[int, list] = {}
    for e in rec["elements"]:
        m = re.match(r"level(\d+)_(depth|metres|label\d+)$", e["name"])
        if m:
            blocks.setdefault(int(m.group(1)), []).append(e)
    order = sorted(blocks)

    def box_of(es):
        return [min(e["box"][0] for e in es), min(e["box"][1] for e in es), max(e["box"][2] for e in es),
                max(e["box"][3] for e in es)]

    def inner(es) -> float:
        ys = sorted({(e["box"][1], e["box"][3]) for e in es})
        air = 0.0
        for (a0, a1), (b0, b1) in zip(ys, ys[1:]):
            if b0 >= a1:                         # rows one under the other (not the figure beside its metres)
                air = max(air, b0 - a1)
        return air
    for a, b in zip(order, order[1:]):
        air = box_of(blocks[b])[1] - box_of(blocks[a])[3]
        need = 1.5 * max(inner(blocks[a]), inner(blocks[b]), 4 * k)
        ok = air >= need - 3
        rows.append((rec["run"], surface(rec), f"level {a} -> {b}", f"{air:.0f} px between the blocks (group gap "
                     f"{need:.0f})", "PASS" if ok else "FAIL"))
    for ld in rec.get("leaders") or []:
        kx = int(re.sub(r"\D", "", ld["name"]) or 0)
        fig = next((e for e in blocks.get(kx, []) if e["name"].endswith("_depth")), None)
        if fig is None:
            continue
        ex, ey = ld["end"]
        dx = fig["box"][0] - ex
        ok = -2 <= dx <= 16 * k + 3 and fig["box"][1] - 2 <= ey <= fig["box"][3] + 2
        rows.append((rec["run"], surface(rec), f"leader {kx} -> {fig['text']!r}",
                     f"ends {dx:.0f} px left of its figure, {ey - (fig['box'][1] + fig['box'][3]) / 2:+.0f} px off its "
                     f"cap centre", "PASS" if ok else "FAIL"))
    bx0, by0, bx1, by1 = graphics.get_text_box(w, h) if h <= w else graphics.map_text_box(w, h)
    for e in rec["elements"]:
        b = e["box"]
        if b[3] > by1 + 2 or b[2] > bx1 + 2 or b[0] < bx0 - 2:
            rows.append((rec["run"], surface(rec), f"{e['name']} {e['text']!r}",
                         f"outside the text box ({b} against {bx0},{by0},{bx1},{by1})", "FAIL"))
    return rows


# --------------------------------------------------------------------------------------------- FIG-07
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[str]:
    """The numbers `text` states; a feet-and-inches figure ("10 FT 8 IN") is ONE number in feet (10.667, its last
    place an inch: marked "#")."""
    ftin = re.search(r"(\d[\d,]*)\s*FT\s+(\d+)\s*IN\b", text, re.I)
    if ftin:
        v = float(ftin.group(1).replace(",", "")) + int(ftin.group(2)) / 12
        rest = text[:ftin.start()] + text[ftin.end():]
        return [f"{v:.4f}#"] + _numbers(rest)
    inch = re.search(r"(?<![\w.])(\d+)\s*IN\b", text, re.I)     # review 3: inches alone ("about 5 in"), in feet
    if inch:
        return [f"{int(inch.group(1)) / 12:.4f}#"] + _numbers(text[:inch.start()] + text[inch.end():])
    return [m.group(0).rstrip(",") for m in NUMBER.finditer(text)]


QUANT = re.compile(r"(?:\b(about|nearly|approximately|roughly|around|almost|over|under)\s+)?(?<![\w.])"
                   r"(\d[\d,]*(?:\.\d+)?)(?:\s*(m|ft|in)\b(?:\s+(\d+)\s*in\b)?)?", re.I)


def _quantities(text: str) -> list[tuple[str, bool, str, float]]:
    """(the number as _numbers keys it, hedged, unit, value in the drawing's feet / metres) of each drawn number."""
    out = []
    for m in QUANT.finditer(text):
        num, unit = m.group(2), (m.group(3) or "").lower()
        v = float(num.replace(",", ""))
        if unit == "ft" and m.group(4):
            v += int(m.group(4)) / 12
            key = f"{v:.4f}#"
        elif unit == "in":
            v /= 12
            key = f"{v:.4f}#"
        else:
            key = num.rstrip(",")
        out.append((key, bool(m.group(1)), unit, v))
    return out


def form_fault(text: str, derived_keys: list[str], hedged: bool, unit: str, rule: str) -> str:
    """Review 3 (B2): each derived number is DRAWN with its hedge (when its record is hedged) and its unit (none for a
    ratio; the drawing's unit for a difference: m, or ft / in)."""
    for key, h, u, _v in _quantities(text):
        if key not in derived_keys:
            continue
        if hedged and not h:
            return f"draws its derived figure {key.rstrip('#')} without its hedge"
        if rule == "ratio":
            if u:
                return f"draws the ratio {key} with a unit ({u})"
        elif not u:
            return f"draws its derived figure {key.rstrip('#')} without its unit"
        elif {"m": "M", "ft": "FT", "in": "FT"}[u] != unit:
            return f"draws its derived figure {key.rstrip('#')} in {u}, not {unit.lower()}"
    return ""


def row_form_fault(text: str, unit: str, subject: float, approximate: bool) -> str:
    """Review 3 (B2, N10): every figure a scale's line of meaning draws is in the drawing's unit (m; or ft / in), and
    the subject's own figure carries its hedge when the fact is approximate."""
    for key, h, u, v in _quantities(text):
        if u and {"m": "M", "ft": "FT", "in": "FT"}[u.lower()] != unit:
            return f"draws {key.rstrip('#')} {u} in a drawing measured in {unit.lower()}"
        if u and approximate and abs(v - subject) < 1e-6 and not h:
            return f"draws the subject's {key.rstrip('#')} {u} without its hedge"
    return ""


def role_fault(text: str, ref_label: str, subject: str, s_value: float, r_value: float) -> str:
    """Review 3b (N5), on the measure's own reading: a row naming the landmark draws only the landmark's figure (within
    3 % of its file value), a row led by the subject's name only the subject's own figure."""
    low = " ".join(text.lower().split())
    ref = ref_label.split(" (")[0].lower()
    sub = re.sub(r"\s+(height|depth|tall|deep|length|long)$", "", subject.lower()).strip()
    role = "landmark" if ref and ref in low else "subject" if sub and low.startswith(sub) else ""
    for key, _h, u, v in _quantities(text):
        if not u:
            continue
        if role == "landmark" and abs(v - r_value) > 0.03 * r_value:
            return f"draws {key.rstrip('#')} {u} in the landmark's row (its figure is about {r_value:.3f})"
        if role == "subject" and abs(v - s_value) > 1e-6:
            return f"draws {key.rstrip('#')} {u} in the subject's row (its figure is {s_value:g})"
    return ""


def landmark_fault(text: str, ref: dict[str, Any] | None, unit: str) -> str:
    """Review 3 (B3), on its own arithmetic: every landmark figure `text` draws (a number within 3 % of the landmark's
    file value) is a figure its statement states (hedged where the statement hedges it), or the file value correctly
    rounded at its last digit and hedged."""
    if not ref:
        return ""
    value = float(ref["feet"]) * (0.3048 if unit == "M" else 1.0)
    statement = str(ref.get("statement") or "")
    for _key, _h, u, _v in _quantities(text):         # review 3b: never another unit than the drawing's
        if u and {"m": "M", "ft": "FT", "in": "FT"}[u.lower()] != unit:
            return f"draws the landmark in {u}, in a drawing measured in {unit.lower()}"
    pat = (r"((?:approximately|about)\W+(?:\w+\W+){0,3})?(\d[\d,]*(?:\.\d+)?)\s*(?:metres|meters|m)\b" if unit == "M"
           else r"((?:approximately|about)\W+(?:\w+\W+){0,3})?(\d[\d,]*)\s*(?:feet|foot|ft)\b"
                r"(?:\s*(\d+)\s*(?:inches|inch|in)\b)?")
    stated = []
    for m in re.finditer(pat, statement, re.I):
        v = float(m.group(2).replace(",", "")) + (int(m.group(3)) / 12 if unit == "FT" and m.group(3) else 0.0)
        stated.append((v, bool(m.group(1))))
    for key, h, u, v in _quantities(text):
        if not u or abs(v - value) > 0.03 * value:
            continue
        match = [c for c in stated if abs(c[0] - v) < 1e-6]
        if any(not c[1] for c in match) or (match and h):
            continue
        num = key.rstrip("#")
        if match:
            return f"draws the landmark {num} {u} without the hedge its statement carries"
        if not h:
            return f"draws the landmark {num} {u}, which its statement does not state, unhedged"
        place = 1 / 12 if "#" in key else 10 ** -len(num.replace(",", "").partition(".")[2])
        if abs(v - value) > 0.5 * place + 1e-6:
            return f"draws the landmark about {num} {u} for its file value {value:.3f}"
    return ""


def _rounded_once(shown: str, true: float) -> bool:
    if shown.endswith("#"):                   # feet and inches: its last place is an inch
        return abs(float(shown[:-1]) - true) <= 0.5 / 12 + 0.01
    raw = shown.replace(",", "")
    return abs(float(raw) - true) <= 0.5 * 10 ** -len(raw.partition(".")[2]) + 1e-9


def _ratio_text(ratio: float) -> str:
    """The comparison's ratio rule (one decimal under 2, else the whole number, half up), written here on its own so
    the measure does not take the tree's word for it."""
    return f"{math.floor(ratio * 10 + 0.5) / 10:g}" if ratio < 2 else str(math.floor(ratio + 0.5))


LEVEL_MAX = {"M": 0.05, "FT": 1 / 24}   # "nearly level" may be said of a gap no larger than half the last digit drawn


# review 3b (N4), on the measure's own reading: the relation each comparison phrase states ("which is larger")
RELATION_PHRASES = ((r"would stick out", "reference"), (r"would vanish", "subject"),
                    (r"would reach nearly to the surface|nearly level", "level"),
                    (r"times as tall as|times the height of", "subject"),
                    (r"times as tall(?!\s+as)|times its length", "reference"),
                    (r"nearly the same height|nearly its length", "level"))
COMPARATIVES = re.compile(r"\b(shorter|smaller|taller|larger|bigger|longer|deeper|shallower|higher|lower|less|more|"
                          r"greater|fewer|above|below|beneath|exceeds?|than|stick|vanish|reach|times|same|nearly)\b",
                          re.I)


def relation_fault(text: str, shown: str, subject: float, reference: float) -> tuple[str, bool]:
    """(what is wrong with the comparison `text` draws, whether it draws one): every comparison word belongs to a known
    phrase, and each phrase states the true relation (the reference larger, the subject larger, level / nearly the
    same when the record draws no number)."""
    rest, said = text, []
    for pat, rel in RELATION_PHRASES:
        said += [rel for _m in re.finditer(pat, rest, re.I)]
        rest = re.sub(pat, " ", rest, flags=re.I)
    left = [m.group(0) for m in COMPARATIVES.finditer(rest)]
    if left:
        return f"draws a comparison the measure cannot read ({', '.join(left)})", bool(said)
    want = "level" if not shown else ("reference" if reference > subject else "subject")
    bad = [r for r in said if r != want]
    return (f"says the {bad[0]} is larger / level where it is the {want}" if bad else ""), bool(said)


def truth_fault(rule: str, text: str, shown: str, operands_drawn: set[str], subject: float, reference: float,
                unit: str = "M", hedged: bool = False, derived_slot: bool = False) -> str:
    """'' when every derived number `text` draws is the rule's result on the SIGNED SOURCE values (`subject`, the
    fact's own value; `reference`, the reference file's figure in the drawing's unit, unrounded) rounded once; else
    what is wrong. Numbers that restate an operand's own drawn figure are not derived numbers."""
    # review 3b (N5): in the derived slot (the comparison row, the bracket) every number is the derived figure
    drawn = [n for n in _numbers(text) if derived_slot or n not in operands_drawn]
    if shown and not drawn and re.search(r"\bnearly\b", text, re.IGNORECASE):
        return f"says {text!r}, a word form, where its record draws {shown}"
    if rule == "ratio":
        true = max(subject, reference) / min(subject, reference)
        if not shown:
            return "" if true < 1.1 and not drawn else f"states no ratio for a true ratio of {true:.3f}"
        bad = [n for n in drawn if n != _ratio_text(true)]
        if bad:
            return f"draws {bad[0]} for a true ratio of {true:.3f} ({_ratio_text(true)} by the rule)"
        return form_fault(text, drawn, hedged, unit, rule)
    true = abs(subject - reference) if rule == "difference" else subject + reference
    if rule == "difference" and not shown:     # review 2 (N2): the word form is a derived claim, checked too
        if drawn:
            return f"draws {drawn[0]} where its record states no figure (true {true:.3f})"
        return (f"says the gap is nearly level for a true difference of {true:.3f}"
                if true > LEVEL_MAX.get(unit, 0.05) + 1e-9 else "")
    if not drawn and shown and shown in _numbers(text):
        drawn = [shown]
    bad = [n for n in drawn if not _rounded_once(n, true)]
    if bad:
        return f"draws {bad[0]} for a true {rule} of {true:.3f} between the signed values"
    return form_fault(text, drawn, hedged, unit, rule)


def derived_rows(rec: dict[str, Any]) -> list[tuple]:
    """Every figure a scale drawing or a timeline states is a signed figure of its input, the reference file's
    sourced landmark figure, a conversion of one, or a DERIVED figure whose record (operands, operation, result)
    satisfies the ruling (figures.derivation_faults) with its operands labelled on the drawing, AND whose drawn number
    is the operation on the SIGNED SOURCE values rounded once (truth_fault: re-computed here from the capture's input
    and the reference file, not from the record; "ABOUT 2.0 M" for a true 1.71 m gap fails)."""
    st = _status(rec)
    if st:
        return [st]
    from src import figures, graphics
    rows = []
    withheld = [o for o in rec.get("omissions") or [] if "derived figure" in str(o.get("omitted"))]
    for o in withheld:
        rows.append((rec["run"], surface(rec), str(o.get("omitted")), "withheld: " + str(o.get("reason"))[:100],
                     "RECORDED"))
    if rec["kind"] == "scale":
        fact = rec["input"]
        feet = fact["value"] * (3.28084 if fact["unit"] == "m" else 1.0)
        ref = graphics.pick_reference(feet)
        depth = fact.get("dimension") == "depth"
        unit = graphics.lead_unit(fact)
        fig, metric = graphics.scale_figure(fact, feet, depth)
        from src.reference_facts import reference_by_label, stated_figures
        stated = stated_figures(reference_by_label(ref[0]), unit)     # the landmark as its own statement signs it
        signed = {v for t in (fig, metric, graphics._short(ref[1], unit)) for f in figures.parse(t) for v in f.values}
        signed |= {c[0] for c in stated} | {v for c in stated for f in figures.parse(c[1]) for v in f.values}
        labelled = set((rec.get("layout") or {}).get("labelled") or [])
        per = 1 / 3.28084 if unit == "M" else 1.0
        own_unit = "M" if str(fact.get("unit") or "").strip().lower() == "m" else "FT"
        s_true = float(fact["value"]) if own_unit == unit else feet * per
        r_true = float(ref[1]) * per
        op_drawn = {n for t in (fig, metric, graphics._short(ref[1], unit), *(c[1] for c in stated))
                    for n in _numbers(t)}
        for e in rec["elements"]:
            if RULER_ROLE.match(e["name"]):
                continue
            vals = [v for f in figures.parse(e["text"]) for v in f.values]
            extra = [v for v in vals if not any(abs(v - s) < 1e-6 for s in signed)]
            d = e.get("derived")
            if e["name"].startswith("ref_label") and not d:      # review 3 (B3): the landmark's own label / legend
                lf = landmark_fault(e["text"], reference_by_label(ref[0]), unit)
                rows.append((rec["run"], surface(rec), f"{e['name']} {e['text']!r}", lf or "the landmark as signed",
                             "FAIL" if lf else "PASS"))
                continue
            if not extra and not d:
                continue
            where = (rec["run"], surface(rec), f"{e['name']} {e['text']!r}")
            if not d:
                rows.append((*where, f"draws {extra} with no derivation record", "FAIL"))
                continue
            faults = figures.derivation_faults(d, labelled if rec.get("layout") else None)
            rel_wrong, comparison = relation_fault(e["text"], str(d.get("shown") or ""), s_true, r_true)
            slot = comparison or e["name"] == "bracket"
            wrong = (rel_wrong or truth_fault(str(d.get("rule")), e["text"], str(d.get("shown") or ""), op_drawn, s_true,
                                              r_true, unit, bool(d.get("hedged")), slot)
                     or (landmark_fault(e["text"], reference_by_label(ref[0]), unit) if not slot else "")
                     or (role_fault(e["text"], ref[0], str(fact.get("subject") or ""), s_true, r_true) if not slot
                         else "")
                     or row_form_fault(e["text"], unit, s_true, bool(fact.get("approximate", True))))
            if wrong:
                faults = [*faults, wrong]
            ops = " ; ".join(f"{o['text']} ({o['role']})" for o in d.get("operands") or [])
            rows.append((*where, "; ".join(faults) or f"{d['rule']} of {ops} = {d['result']:g}, shown "
                         f"{d.get('text')!r}", "FAIL" if faults else "PASS"))
    elif rec["kind"] == "timeline":
        for e in rec["elements"]:
            if SPAN_CAPTION.match(e["name"]) and any(ch.isdigit() for ch in e["text"]):
                d = e.get("derived")
                ok = isinstance(d, dict) and d.get("rule") == "elapsed_years"
                rows.append((rec["run"], surface(rec), f"{e['name']} {e['text']!r}",
                             f"elapsed years {d.get('from')} -> {d.get('to')}" if ok else "no derivation record",
                             "PASS" if ok else "FAIL"))
    return rows


# --------------------------------------------------------------------------------------------- RECORDS (axis yields)
def _recorded_axis(rec: dict[str, Any]) -> set[float]:
    texts = [str(r.get("text")) for r in ((rec.get("layout") or {}).get("axis_yielded") or [])]
    texts += [str(o.get("omitted", "")).replace("the axis figure ", "") for o in rec.get("omissions") or []
              if "axis figure" in str(o.get("omitted", ""))]
    out = set()
    for t in texts:
        m = re.match(r"\s*([\d,]+(?:\.\d+)?)", t)
        if m:
            out.add(float(m.group(1).replace(",", "")))
    return out


def axis_yield_rows(rec: dict[str, Any]) -> list[tuple]:
    """Every axis figure a drawing's ruler implies is drawn or RECORDED as yielded (freeze cycle 5: the 9:16 Wieliczka
    '300' was left out with no record). Measured on the drawing itself, not the tree's tick list: the step is the drawn
    ticks' spacing, the value of a height on the ruler is the drawn ticks' own line (value against datum), and the
    ruler's extent is the drawing's (a depth's shaft, a height's tallest figure, a cross-section's ruler line); every
    multiple of the step inside that extent is expected."""
    st = _status(rec)
    if st:
        return [st] if st[4] == "FAIL" else []
    if rec["kind"] not in ("scale", "section"):
        return []
    ticks = sorted((float(r["value"]), float(r["datum"])) for r in rec.get("rulers") or []
                   if r.get("value") is not None and r.get("datum") is not None)
    where = (rec["run"], surface(rec), "ruler")
    if len(ticks) < 2:
        return [(*where, f"{len(ticks)} axis figure(s) drawn: no step to measure", "NOTE")]
    vals = [v for v, _ in ticks]
    step = min(b - a for a, b in zip(vals, vals[1:]) if b - a > 1e-6)
    (v0, y0), (v1, y1) = ticks[0], ticks[1]
    per = (y1 - y0) / (v1 - v0)                           # px per unit on the drawing's own ruler
    lay = rec.get("layout") or {}
    if rec["kind"] == "section":
        m = next((m["box"] for m in rec.get("marks") or [] if m["name"] == "ruler" and m.get("box")), None)
        end_y = m[3] if m else None
    elif lay.get("form") == "depth":
        end_y = (lay.get("drawing") or [None] * 4)[3]
    else:
        end_y = (lay.get("drawing") or [None] * 4)[1]
    if end_y is None:
        return [(*where, "no drawing extent recorded", "NOTE")]
    end_v = v0 + (float(end_y) - y0) / per
    want = [k * step for k in range(0, int(end_v / step + 1e-6) + 1)]
    drawn, kept = set(vals), _recorded_axis(rec)
    rows = []
    for v in want:
        if any(abs(v - d) < 1e-6 for d in drawn):
            continue
        label = f"{v:,.0f}"
        if any(abs(v - d) < 1e-6 for d in kept):
            rows.append((*where, f"axis figure {label} yielded, recorded with its rule", "RECORDED"))
        elif abs(v - end_v) <= 0.02 * step:
            rows.append((*where, f"axis figure {label} is the drawing's end, named by its stated figure", "NOTE"))
        else:
            rows.append((*where, f"axis figure {label} (step {step:g}, extent {end_v:,.0f}) neither drawn nor "
                                 "recorded", "FAIL"))
    if not rows:
        rows.append((*where, f"{len(vals)} axis figures, every multiple of {step:g} to {end_v:,.0f} drawn", "PASS"))
    return rows


def region_line_rows(rec: dict[str, Any], region: str) -> list[tuple]:
    """A map whose place records a region line draws it under the site's name or records that it yielded."""
    st = _status(rec)
    if st:
        return [st] if st[4] == "FAIL" else []
    if rec["kind"] != "map" or not region:
        return []
    norm = lambda t: re.sub(r"\s+", "", str(t)).upper()     # noqa: E731
    where = (rec["run"], surface(rec), f"region line {region!r}")
    if norm(region) in norm(" ".join(e["text"] for e in rec["elements"])):
        return [(*where, "drawn", "PASS")]
    if any("region line" in str(o.get("omitted", "")) for o in rec.get("omissions") or []):
        return [(*where, "yielded, recorded with its rule", "RECORDED")]
    return [(*where, "neither drawn nor recorded", "FAIL")]


# --------------------------------------------------------------------------------------------- FIG-09
SUBJECT_FIGURE = ("floor_figure", "block_label")      # a scale drawing's own figure for its subject (the answer)
OTHER_FIGURES = ("ref_label", "bracket")              # the landmark's figure and the derived difference


def hierarchy_rows(rec: dict[str, Any]) -> list[tuple]:
    """FIG-09: on a scale drawing the subject's own figure (the answer) is one step above every other stated quantity
    on the drawing (the landmark's figure, the derived bracket): its figure px greater than theirs."""
    st = _status(rec)
    if st:
        return [st] if st[4] == "FAIL" else []
    if rec["kind"] != "scale":
        return []

    def px(e):
        return max((r["px"] for r in e["runs"] if any(ch.isdigit() for ch in r["text"])), default=0)
    subj = [e for e in rec["elements"] if e["name"] in SUBJECT_FIGURE]
    other = [e for e in rec["elements"] if e["name"] in OTHER_FIGURES]
    where = (rec["run"], surface(rec), "answer")
    if not subj:
        dropped = any(("floor figure" in str(o.get("omitted")) or "block" in str(o.get("omitted")))
                      for o in rec.get("omissions") or [])
        return [(*where, "the subject's figure is not drawn" + (", recorded" if dropped else ""),
                 "RECORDED" if dropped else "FAIL")]
    a = px(subj[0])
    top = max((px(e) for e in other), default=0)
    names = ", ".join(f"{e['name']} {px(e)}" for e in other)
    return [(*where, f"{subj[0]['name']} {subj[0]['text']!r} {a} px against {names or 'none'}",
             "PASS" if a > top else "FAIL")]


# --------------------------------------------------------------------------------------------- FIG-11
def scale_bar_rows(rec: dict[str, Any]) -> list[tuple]:
    """FIG-11: a map's two-unit scale bar never reads as an equation ('100 KM / 30 MI'): either the two values are
    the same length (mi x 1.609 within 5 % of km) or each label ends on its OWN tick, the two label ends at least 8 px
    apart along the bar and in the order of their lengths (the shorter run's label ends nearer the zero end)."""
    st = _status(rec)
    if st:
        return [st] if st[4] == "FAIL" else []
    if rec["kind"] not in ("map", "route"):
        return []
    sc = {e["name"]: e for e in rec["elements"] if e["name"] in ("scale", "scale_secondary")}
    where = (rec["run"], surface(rec), "scale bar")
    if len(sc) < 2:
        return [(*where, "one unit (or no bar)", "PASS")] if sc else []
    num = lambda t: float(re.sub(r"[^\d.]", "", t) or 0)          # noqa: E731
    km_e = next((e for e in sc.values() if "KM" in e["text"]), None)
    mi_e = next((e for e in sc.values() if "MI" in e["text"]), None)
    if km_e is None or mi_e is None:
        return [(*where, f"units {[e['text'] for e in sc.values()]}", "NOTE")]
    km, mi = num(km_e["text"]), num(mi_e["text"]) * 1.609344
    if abs(mi - km) <= 0.05 * km:
        return [(*where, f"{km_e['text']} = {mi_e['text']} (one length)", "PASS")]
    p, s_ = sc["scale"], sc["scale_secondary"]
    mirrored = p["box"][0] < s_["box"][0] - 8 and p["box"][2] <= s_["box"][2]   # a bar drawn leftwards
    pe, se = (p["box"][0], s_["box"][0]) if mirrored else (p["box"][2], s_["box"][2])
    longer_primary = num(p["text"]) * (1.609344 if "MI" in p["text"] else 1) >=         num(s_["text"]) * (1.609344 if "MI" in s_["text"] else 1)
    order_ok = (pe > se) == longer_primary if not mirrored else (pe < se) == longer_primary
    ok = abs(pe - se) >= 8 and order_ok
    return [(*where, f"{p['text']} ends {pe}, {s_['text']} ends {se} (each on its own tick)",
             "PASS" if ok else "FAIL")]


# --------------------------------------------------------------------------------------------- FIG-12
def placement_rows(rec: dict[str, Any]) -> list[tuple]:
    """A timeline sets every event's label in ONE placement relative to its year (all below it, or all beside it);
    two placements in one column read as two systems (panel 6, the dense 9:16 stand-in)."""
    st = _status(rec)
    if st:
        return [st] if st[4] == "FAIL" else []
    if rec["kind"] != "timeline":
        return []
    years: dict[int, list] = {}
    labels: dict[int, list] = {}
    for e in rec["elements"]:
        m = re.match(r"(year|label)(\d+)$", e["name"])
        if m:
            (years if m.group(1) == "year" else labels).setdefault(int(m.group(2)), []).append(e["box"])
    seen = []
    for k in sorted(years):
        yb, lb = years[k][0], labels.get(k)
        if not lb:
            continue
        top, left = min(b[1] for b in lb), min(b[0] for b in lb)
        seen.append("below" if top >= yb[3] - 2 else "beside" if left >= yb[2] - 2 else "over")
    kinds = sorted(set(seen))
    return [(rec["run"], surface(rec), f"{len(seen)} event labels", f"placements {kinds}",
             "PASS" if len(kinds) <= 1 and "over" not in kinds else "FAIL")]


# --------------------------------------------------------------------------------------------- FIG-10
def contrast_rows(rec: dict[str, Any], gate: float = 4.5, place: float = 4.8) -> list[tuple]:
    """Every name a map draws is a measured, recorded label at or over the text gate on its own ground (the site's
    own name at the place target), and no name the map's own name check passed is drawn unmeasured (the base's)."""
    st = _status(rec)
    if st:
        return [st]
    rows = []
    norm = lambda t: re.sub(r"\s+", "", str(t)).upper()     # noqa: E731
    have = {norm(e["text"]) for e in rec["elements"]}
    joined = norm("".join(e["text"] for e in rec["elements"]))
    for e in rec["elements"]:
        if RULER_ROLE.match(e["name"]):
            continue
        need = place if e["name"].startswith(("place", "stop")) else gate
        c = e.get("planned")
        ok = c is not None and c >= need - 0.005
        rows.append((rec["run"], surface(rec), f"{e['name']} {e['text']!r}",
                     f"{c:.2f}:1 planned on its ground (need {need})" if c is not None else "unmeasured",
                     "PASS" if ok else "FAIL"))
    for c in rec.get("name_checks") or []:
        if c["ok"] and norm(c["text"]) not in have and norm(c["text"]) not in joined:
            rows.append((rec["run"], surface(rec), f"{c['kind']} name {c['text']!r}",
                         "drawn by the base, never measured or recorded", "FAIL"))
    return rows


# --------------------------------------------------------------------------------------------- FIG-05
def share_rows(rec: dict[str, Any], floor_h: float = 0.45) -> list[tuple]:
    """A 9:16 scale drawing (the drawing with its own labels: everything but the heading and the line of meaning)
    takes at least `floor_h` of the graphics box's height."""
    st = _status(rec)
    if st:
        return [st]
    if rec["kind"] != "scale" or rec["format"] != "916":
        return []
    from src import graphics
    w, h = rec["size"]
    bx0, by0, bx1, by1 = graphics.get_text_box(w, h)
    boxes = [e["box"] for e in rec["elements"] if e["name"] not in HEADING and not e["name"].startswith("meaning")]
    boxes += [m["box"] for m in rec["marks"] if m.get("box")]
    if not boxes:
        return []
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    hs = (y1 - y0) / (by1 - by0)
    area = (x1 - x0) * (y1 - y0) / ((bx1 - bx0) * (by1 - by0))
    return [(rec["run"], surface(rec), "drawing", f"height share {hs:.3f}, area share {area:.3f} of the graphics box "
             f"({bx1 - bx0} x {by1 - by0})", "PASS" if hs >= floor_h else "FAIL")]


# --------------------------------------------------------------------------------------------- FIG-06
DELIVERED_PT = 390.0      # the width, in points, a frame of either format is delivered at on a phone (16:9 inline)
MIN_LABEL_PT = 10.0       # RUBRIC PL2: the least label size at the delivered size


def delivered_floor_px(width: int) -> int:
    """The least font px on a frame `width` px wide that is MIN_LABEL_PT at the delivered size."""
    return math.ceil(MIN_LABEL_PT * width / DELIVERED_PT - 1e-6)


def floor_rows(rec: dict[str, Any]) -> list[tuple]:
    """Every label of a data graphic at or over the delivered-size floor of its format (one row per element role
    and size: the count of labels, their px and their size in delivered points)."""
    st = _status(rec)
    if st:
        return [st]
    import collections
    if rec["format"] == "slide":         # the carousel slide's drawing: its own floor (diagram_label_floor), not 16:9
        return []
    w = rec["size"][0]
    need = delivered_floor_px(w)
    seen: dict[tuple, int] = collections.Counter()
    for e in rec["elements"]:
        role = re.sub(r"neighbour_.*", "neighbour", re.sub(r"water_.*", "water", re.sub(r"\d+", "#", e["name"])))
        for r in e["runs"]:
            if r["px"]:
                seen[(role, r["px"])] += 1
    rows = []
    for (role, px), n in sorted(seen.items()):
        rows.append((rec["run"], surface(rec), f"{role} x{n}", f"{px} px = {px * DELIVERED_PT / w:.1f} pt delivered "
                     f"(floor {need} px)", "PASS" if px >= need else "FAIL"))
    return rows


def run(doc: str, kinds, measure, need_tokens: bool = False) -> int:
    """A class script's main: capture (cached), measure, print the table and the counts; 1 when any row FAILs."""
    import collections

    import figures_corpus as fc
    got, a = fc.cli(doc, kinds)
    from src import graphics
    pal = {j["run"]: j.get("palette") for j in fc.run_inputs(a.dev)}
    rows: list[tuple] = []
    for key in sorted(got["records"]):
        rec = got["records"][key]
        rows += measure(rec, tokens(graphics, pal.get(rec["run"]))) if need_tokens else measure(rec)
    print(fc.table(rows, ("run", "surface", "element", "measure", "verdict")))
    count = collections.Counter(r[4] for r in rows)
    runs = len({r[0] for r in rows})
    print(f"\n{doc.splitlines()[0]}\nsrc {got['src_sha']}: {len(got['records'])} graphics of {runs} runs/fixtures; "
          + ", ".join(f"{k} {count[k]}" for k in ("PASS", "FAIL", "RECORDED", "NOTE")))
    return 1 if count["FAIL"] else 0
