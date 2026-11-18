"""
src/figure_gate.py
==================
The drawn-figure release gate (design-freeze cycle 0 round 8, lane TRUTH; QF-147 made structural).

Before a run can reach approval (src/release.py write_release_report, check "drawn_figures"), every drawn text
element of every surface the run recorded is read from its layout record, every number in it is parsed whole
(src/figures.py: hedge, number or span, scale word, unit), and each must be a signed value of the run in its unit,
whole, with its hedge:

- the records: every still_layout.json under the run (covers and YouTube thumbnails at the root, carousel/,
  carousel_fb/, stories/ and any other still) and every <clip>.contrast.json (graphics, number cards, scale and
  section, map and route, timeline, hook / chapter / myth / quote cards, callouts, then-and-now): each text element's
  "text" (typography.TextPlate.record and record_contrast record it; graphics sidecars always did);
- the signed values: the run's signed copy (narration, title, captions, carousel and Story slides, cover copy, the
  end card, the share line, the fact sheet's statements and hedges) and its signed graphics fields (stat cards, the
  scale fact with its hedge, the cross-section, the timeline, the route);
- a match: the same number or span in the same unit (a unit's conversion to the other system within CONVERT_TOL of a
  signed value counts: a scale's "85 M" under "280 FT"); a signed span drawn as one end fails ("18 LEVELS" for "eight
  to 18"), a decimal drawn short fails ("11" for "11.5 feet"), a figure every signed statement of which is hedged
  fails without a hedge ("20,000 PEOPLE" for "up to 20,000").

Elements that draw derived or structural numbers are named and counted, not judged here: ruler and axis ticks (the
zero-datum critic judges them), a map's scale bar, the reference landmark's sourced figure and its note, the line of
meaning's ratio, a chapter numeral and its total, credits and licence lines. An element whose record carries no text
(a record written before records carried it) cannot be judged and holds the run for review: fail closed.

Topic-agnostic: no topic names; the vocabularies are src/figures.py's.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import figures

# Elements whose numbers are derived from signed values or structural (named and counted, never a claim):
DERIVED = re.compile(r"^(ruler|axis|tick|scale$|scale_secondary|scale_bar|meaning|ref_label|ref_note|bracket|numeral|"
                     r"total|chapter|header_brand|masthead|photo_credit|credit|licence|share|swipe|progress|counter|"
                     r"source|badge)", re.IGNORECASE)
CONVERSION = re.compile(r"^(figure_metric|secondary|conversion|figure_conv)", re.IGNORECASE)
SKIP_DIRS = ("stage_cache", "source_versions", "archive", "graphics_redrawn_tmp")
CONVERT_TOL = 0.02     # a conversion within 2 % of a signed value in the other system (rounded as measurement_text)
SIGNED_COPY_KEYS = ("narration_script", "title", "caption", "description", "carousel_caption", "carousel_slides",
                    "story_slides", "thumbnail", "thumbnail_youtube", "hook_sentence", "closing_sentence", "end_card",
                    "share_line", "first_comment", "poll", "photo_labels", "emphasis_words", "stat_cards", "location")

_FT_PER = {"FT": 1.0, "M": 3.28084, "KM": 3280.84, "MILES": 5280.0, "MI": 5280.0, "CM": 0.0328084, "IN": 1 / 12,
           "YD": 3.0}
_LB_PER = {"LB": 1.0, "KG": 2.20462, "TONNES": 2204.62, "TONS": 2000.0}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def signed_texts(payload: Mapping[str, Any]) -> list[str]:
    """The run's signed copy and its signed graphics fields, as texts the figure parser reads."""
    out: list[str] = []
    for key in SIGNED_COPY_KEYS:
        out += list(_strings(payload.get(key)))
    sheet = payload.get("fact_sheet") or {}
    for fact in (sheet.get("facts") or []) if isinstance(sheet, Mapping) else []:
        if isinstance(fact, Mapping):
            out += [str(fact.get("statement") or ""), str(fact.get("hedge") or "")]
    fact = payload.get("scale_fact")
    if isinstance(fact, Mapping) and fact.get("value"):
        hedge = "about " if fact.get("approximate", True) else ""
        out.append(f"{hedge}{float(fact['value']):g} {fact.get('unit') or 'ft'}")
    cs = payload.get("cross_section")
    if isinstance(cs, Mapping):
        if cs.get("total_depth_ft"):
            out.append(f"{'about ' if cs.get('approximate') else ''}{float(cs['total_depth_ft']):g} ft")
        for lv in cs.get("levels") or []:
            if isinstance(lv, Mapping) and lv.get("depth_ft") is not None:
                out.append(f"{float(lv['depth_ft']):g} ft {lv.get('label') or ''}")
    for ev in payload.get("timeline") or []:
        if isinstance(ev, Mapping):
            out.append(f"{ev.get('year', '')} {ev.get('label') or ''} {ev.get('end_year') or ''}")
    for stop in payload.get("route") or []:
        out += list(_strings(stop))
    return [t for t in out if t and t.strip()]


def _in_feet(value: float, unit: str) -> tuple[str, float] | None:
    u = figures._canon(unit)
    if u in _FT_PER:
        return "length", value * _FT_PER[u]
    if u in _LB_PER:
        return "mass", value * _LB_PER[u]
    return None


def _converts(d: figures.Figure, signed: list[figures.Figure], need_hedge: bool = True) -> bool:
    """A drawn measured figure that is a signed one's conversion to the other unit (within CONVERT_TOL)."""
    for s in signed:
        for v in (s, s.conv):
            if v is None or not v.unit or len(v.values) != len(d.values):
                continue
            pairs = [(_in_feet(a, d.unit), _in_feet(b, v.unit)) for a, b in zip(d.values, v.values)]
            if all(x and y and x[0] == y[0] and abs(x[1] - y[1]) <= CONVERT_TOL * max(abs(y[1]), 1e-9) + 1e-9
                   for x, y in pairs):
                if need_hedge and s.hedge and not d.hedge and all(o.hedge for o in signed if o.values == s.values):
                    continue
                return True
    return False


def figure_faults(drawn: str, signed: list[figures.Figure], need_hedge: bool = True) -> list[str]:
    """figures.truth_faults, with a measured figure's conversion to the other system accepted. `need_hedge` False: a
    conversion line under its hedged lead (the lead carries the hedge: "ABOUT 280 FT DEEP" over "85 M")."""
    out = []
    for f in figures.parse(drawn):
        fault = figures.truth_faults(f.drawn(), signed)
        if not need_hedge:
            fault = [x for x in fault if "is hedged" not in x]
        if fault and f.unit and _converts(f, signed, need_hedge):
            continue
        out += fault
    return out


def _nearest(d: figures.Figure, signed: list[figures.Figure]) -> str:
    if not d.values:
        return ""
    best = min(((abs(v - d.values[0]), s) for s in signed for v in s.values), key=lambda t: t[0], default=None)
    return best[1].drawn() if best else ""


def _elements(run_dir: Path) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """(surface, element name, element) for every element of every layout record under the run."""
    run_dir = Path(run_dir)
    for rec_file in sorted(run_dir.rglob("still_layout.json")):
        if any(p in SKIP_DIRS for p in rec_file.relative_to(run_dir).parts):
            continue
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for still, entry in (data.items() if isinstance(data, dict) else []):
            card = ((entry or {}).get("recipe") or {}).get("card") or {}
            for e in (entry or {}).get("elements") or []:
                if isinstance(e, dict):
                    if str(e.get("name")) == "stat" and isinstance(card, dict) and card.get("body"):
                        e = {**e, "_own": str(card["body"])}   # a stat slide's figure: its own body states it
                    yield f"{rec_file.parent.relative_to(run_dir).as_posix().replace('.', '') or 'cover'}/{still}", \
                        str(e.get("name") or ""), e
    for rec_file in sorted(run_dir.rglob("*.contrast.json")):
        if any(p in SKIP_DIRS for p in rec_file.relative_to(run_dir).parts):
            continue
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        surface = rec_file.relative_to(run_dir).as_posix().replace(".contrast.json", "")
        joined: list[dict[str, Any]] = []
        for e in (data.get("elements") or []) if isinstance(data, dict) else []:
            if not isinstance(e, dict):
                continue
            t = str(e.get("text") or "")
            # a figure planned a character at a time (older records): its characters are one figure
            if joined and joined[-1]["name"] == e.get("name") and len(t) == 1 and len(joined[-1].get("_last", "")) == 1:
                joined[-1]["text"] += t
                joined[-1]["_last"] = t
                continue
            joined.append({**e, "_last": t})
        for e in joined:
            yield surface, str(e.get("name") or ""), e


TEXT_NAMES = re.compile(r"^(heading|body|kicker|stat|title|eyebrow|line|tagline|badge|share|figure|label|unit|"
                        r"subject|claim|fact|myth|quote|caption|question|prefix|secondary|place|country|year|event)",
                        re.IGNORECASE)


def _is_text(e: Mapping[str, Any]) -> bool:
    """A text element: its record says so (text, face, size, contrast), or its name is a text role's (an older record
    kept only the box)."""
    return ("text" in e or "font" in e or "px" in e or "contrast" in e or "planned" in e
            or bool(TEXT_NAMES.match(str(e.get("name") or ""))))


def check_run(run_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The gate on one run: {"faults": [...], "unrecorded": [...], "judged": n, "derived": n, "surfaces": n}. Each
    fault names the surface, the element, the drawn figure, what is wrong and the nearest signed value."""
    signed = figures.signed_figures(signed_texts(payload))
    faults, unrecorded = [], []
    judged = derived = 0
    surfaces: set[str] = set()
    for surface, name, e in _elements(run_dir):
        if not _is_text(e):
            continue
        surfaces.add(surface)
        if "text" not in e:
            unrecorded.append(f"{surface} {name}: its record carries no drawn text")
            continue
        text = str(e.get("text") or "")
        if not any(ch.isdigit() for ch in text) and not any(w in text.lower().split() for w in figures.NUMBER_WORDS):
            continue
        if DERIVED.match(name):
            derived += 1
            continue
        judged += 1
        if e.get("_own"):    # a slide's figure is judged against its own signed body too (QF-147: 280 FT over levels)
            own = figures.parse(str(e["_own"]))
            for fault in figure_faults(text, own):
                faults.append(f"{surface} ({name}): drew {text!r}, which its own body does not state whole: "
                              f"{fault.split(': ', 1)[-1]}")
        for f in figures.parse(text):
            if not (f.unit or f.scale or f.hedge or f.hi) and re.fullmatch(r"(1[0-9]{3}|20[0-9]{2})", f.lo) \
                    and any(f.lo in t for t in signed_texts(payload)):
                continue   # a year the signed copy states
            # a conversion line under its hedged lead: the lead carries the hedge
            for fault in figure_faults(f.drawn(), signed, need_hedge=not CONVERSION.match(name)):
                near = _nearest(f, signed)
                faults.append(f"{surface} ({name}): drew {f.drawn()!r}: {fault.split(': ', 1)[-1]}"
                              + (f"; nearest signed value {near!r}" if near else ""))
    return {"faults": faults, "unrecorded": unrecorded, "judged": judged, "derived": derived,
            "surfaces": len(surfaces)}


def release_check(run_dir: Path, payload: Mapping[str, Any]) -> tuple[str, list[str] | str]:
    """(status, detail) for the release report: "fail" with one line per fault; "needs_review" when an element's
    record carries no text (it cannot be judged); else "pass" with the counts."""
    res = check_run(run_dir, payload)
    if res["faults"]:
        return "fail", res["faults"]
    if res["unrecorded"]:
        return "needs_review", res["unrecorded"][:12] + ([f"and {len(res['unrecorded']) - 12} more"]
                                                         if len(res["unrecorded"]) > 12 else [])
    return "pass", (f"Every drawn number is a signed value with its unit and hedge: {res['judged']} figure "
                    f"element(s) on {res['surfaces']} surface(s); {res['derived']} derived (rulers, scale bars, "
                    "conversions' references, numerals) judged by their own critics.")
