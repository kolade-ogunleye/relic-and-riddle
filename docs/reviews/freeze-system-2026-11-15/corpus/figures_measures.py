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
                gap = ""
                if len(e["runs"]) == 2:
                    a, b = e["runs"]
                    cap = max(1, a["box"][3] - a["box"][1])
                    gap = f"; unit gap {(b['box'][0] - a['box'][2]) / cap:.2f} cap"
                rows.append((*where, f"axis figure in the data face{gap}", "PASS"))
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
        rows.append((*where, "; ".join(bad) or "figure face, number and unit in the figure colour, qualifier bone",
                     "FAIL" if bad else "PASS"))
    return rows


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
    return [m.group(0).rstrip(",") for m in NUMBER.finditer(text)]


def _rounded_once(shown: str, true: float) -> bool:
    raw = shown.replace(",", "")
    return abs(float(raw) - true) <= 0.5 * 10 ** -len(raw.partition(".")[2]) + 1e-9


def _ratio_text(ratio: float) -> str:
    """The comparison's ratio rule (one decimal under 2, else the whole number, half up), written here on its own so
    the measure does not take the tree's word for it."""
    return f"{math.floor(ratio * 10 + 0.5) / 10:g}" if ratio < 2 else str(math.floor(ratio + 0.5))


def truth_fault(rule: str, text: str, shown: str, operands_drawn: set[str], subject: float, reference: float) -> str:
    """'' when every derived number `text` draws is the rule's result on the SIGNED SOURCE values (`subject`, the
    fact's own value; `reference`, the reference file's figure in the drawing's unit, unrounded) rounded once; else
    what is wrong. Numbers that restate an operand's own drawn figure are not derived numbers."""
    drawn = [n for n in _numbers(text) if n not in operands_drawn]
    if rule == "ratio":
        true = max(subject, reference) / min(subject, reference)
        if not shown:
            return "" if true < 1.1 and not drawn else f"states no ratio for a true ratio of {true:.3f}"
        bad = [n for n in drawn if n != _ratio_text(true)]
        return f"draws {bad[0]} for a true ratio of {true:.3f} ({_ratio_text(true)} by the rule)" if bad else ""
    true = abs(subject - reference) if rule == "difference" else subject + reference
    if not drawn and shown and shown in _numbers(text):
        drawn = [shown]
    bad = [n for n in drawn if not _rounded_once(n, true)]
    return f"draws {bad[0]} for a true {rule} of {true:.3f} between the signed values" if bad else ""


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
        signed = {v for t in (fig, metric, graphics._short(ref[1], unit)) for f in figures.parse(t) for v in f.values}
        labelled = set((rec.get("layout") or {}).get("labelled") or [])
        per = 1 / 3.28084 if unit == "M" else 1.0
        own_unit = "M" if str(fact.get("unit") or "").strip().lower() == "m" else "FT"
        s_true = float(fact["value"]) if own_unit == unit else feet * per
        r_true = float(ref[1]) * per
        op_drawn = {n for t in (fig, metric, graphics._short(ref[1], unit)) for n in _numbers(t)}
        for e in rec["elements"]:
            if RULER_ROLE.match(e["name"]):
                continue
            vals = [v for f in figures.parse(e["text"]) for v in f.values]
            extra = [v for v in vals if not any(abs(v - s) < 1e-6 for s in signed)]
            d = e.get("derived")
            if not extra and not d:
                continue
            where = (rec["run"], surface(rec), f"{e['name']} {e['text']!r}")
            if not d:
                rows.append((*where, f"draws {extra} with no derivation record", "FAIL"))
                continue
            faults = figures.derivation_faults(d, labelled if rec.get("layout") else None)
            wrong = truth_fault(str(d.get("rule")), e["text"], str(d.get("shown") or ""), op_drawn, s_true, r_true)
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
