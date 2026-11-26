"""
src/figure_gate.py
==================
The drawn-figure release gate (design-freeze cycle 0 round 8, lane TRUTH; QF-147 made structural).

Before a run can reach approval (src/release.py write_release_report, check "drawn_figures"), every drawn text
element of every surface the run recorded is read from its layout record, every number in it is parsed whole
(src/figures.py: hedge, number or span, scale word, unit), and each must be a signed value of the run in its unit,
whole, with its hedge:

- the records: every still_layout.json under the run (the Reel covers and grid tile at the root, carousel/,
  carousel_fb/, stories/ and any other still), every <clip>.contrast.json (graphics, number cards, scale and
  section, map and route, timeline, hook / chapter / myth / quote cards, callouts, then-and-now, the caption file's
  bursts), the YouTube thumbnail's <thumb>.qa.json "elements" and the end card's line in the master's
  composition.json: each text element's "text" (typography.TextPlate.record and record_contrast record it now;
  graphics sidecars always did);
- the signed values: the run's signed copy (narration, title, captions, carousel and Story slides, cover copy, the
  end card, the share line, the fact sheet's statements and hedges) and its signed graphics fields (stat cards, the
  scale fact with its hedge, the cross-section, the timeline, the route);
- a match: the same number or span in the same unit (a unit's conversion to the other system within CONVERT_TOL of a
  signed value counts: a scale's "85 M" under "280 FT"); a signed span drawn as one end fails ("18 LEVELS" for "eight
  to 18"), a decimal drawn short fails ("11" for "11.5 feet"), a figure every signed statement of which is hedged
  fails without a hedge ("20,000 PEOPLE" for "up to 20,000").

Elements that draw derived or structural numbers are named and counted, not judged here: ruler and axis ticks (the
zero-datum critic judges them), a map's scale bar, the reference landmark's sourced figure and its note, the line of
meaning's ratio, a chapter numeral and its total, credits and licence lines. A timeline's gap label ("53 YEARS
LATER") is derived AND checked (SYS-R8-1): its record carries the rule and its two operands (graphics.derive_gap), and
the gate re-computes figures.elapsed from them, each operand a signed timeline year; a wrong gap fails.

Captions are judged across the spoken sequence, never card by card: the bursts joined in order, read with the
narration's own punctuation wherever the caption words are the narration's words (caption_text: a sentence's lead
hedge and its full stops survive; a word that differs is judged as drawn). A deep dive's narration is its acts'
narration_text (narration()). So a hedge the narration states as a sentence's lead is the caption's hedge too, and a
caption that drops the hedge's words, or changes a figure, fails. An element whose record carries no text
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

# Elements whose numbers are derived from signed values, from a photo's own record (then-and-now capture years:
# then_now's honesty rule) or structural (named and counted, never a claim):
DERIVED = re.compile(r"^(then_year|now_year|ruler|axis|tick|scale$|scale_secondary|scale_bar|meaning|ref_label|"
                     r"ref_note|bracket|numeral|total|chapter|header_brand|masthead|photo_credit|credit|licence|swipe|progress|counter|"
                     r"source|badge)", re.IGNORECASE)
CONVERSION = re.compile(r"^(figure_metric|secondary|conversion|figure_conv)", re.IGNORECASE)
SKIP_DIRS = ("stage_cache", "source_versions", "archive", "graphics_redrawn_tmp")
# a conversion within 2 % of a signed value in the other system: figures' one constant, which the renderers' conversion
# rule (figures.converted_number) draws within, so the two never disagree (SYS-R8-1)
CONVERT_TOL = figures.CONVERT_TOL
SIGNED_COPY_KEYS = ("narration_script", "title", "caption", "description", "carousel_caption", "carousel_slides",
                    "story_slides", "thumbnail", "thumbnail_youtube", "hook_sentence", "closing_sentence", "end_card",
                    "share_line", "first_comment", "poll", "photo_labels", "emphasis_words", "stat_cards", "location")

_FT_PER = figures.LENGTH_PER_FT
_LB_PER = figures.MASS_PER_LB


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def narration(payload: Mapping[str, Any]) -> str:
    """The run's narration as one text: the Reel's narration_script, or a deep dive's acts' own narration_text in act
    order, each act a sentence of its own (SYS-R8-1: a deep-dive payload has no narration_script, so its captions and
    its hedges were judged without the narration they speak)."""
    text = payload.get("narration_script")
    if isinstance(text, str) and text.strip():
        return text
    acts = payload.get("acts") or []
    parts = []
    for act in sorted((a for a in acts if isinstance(a, Mapping)), key=lambda a: a.get("act_number") or 0):
        t = " ".join(str(act.get("narration_text") or "").split())
        if t:
            parts.append(t if t[-1] in ".!?" else t + ".")
    return " ".join(parts)


def signed_texts(payload: Mapping[str, Any]) -> list[str]:
    """The run's signed copy and its signed graphics fields, as texts the figure parser reads (a deep dive: its acts'
    narration and titles and its YouTube title too)."""
    out: list[str] = []
    for key in SIGNED_COPY_KEYS:
        out += list(_strings(payload.get(key)))
    if not payload.get("narration_script") and payload.get("acts"):
        out.append(narration(payload))
        for act in payload.get("acts") or []:
            if isinstance(act, Mapping) and act.get("act_title"):
                out.append(str(act["act_title"]))
        out += list(_strings(payload.get("youtube_title")))
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


# Freeze cycle 3 (SYS batch 3, lander rulings on "THE 1,200-TON"): headline truth is ONE rule everywhere (this gate,
# script_engine's headline check and trace_check's writer and checker prompts). A headline's figure (the title, the
# thumbnail's and the YouTube thumbnail's lines) must equal a signed statement: its hedge, both span ends, in the run's
# unit (a bare ton is a tonne in a metric run, a US short ton in a US customary one). A floor ("1,000+ TONNES", "OVER
# 1,000 TONNES") stands only where the signed statement is itself a floor, drawn as the same kind of hedge. Covers are
# not exempt from hedges. Evidence is any signed field of the run (the verified body: narration, the acts, the stat
# cards, graphics fields, the fact sheet; the fact audits and the second check of those) and never derived copy (titles,
# thumbnail lines, comments, captions, the carousel slides and Story frames, nor an audit OF one of them). Freeze cycle 4
# (SYS-lane-c-c4-1): the slides and Stories are derived copy here as at draw time (visual_engine.EVIDENCE_KEYS, lane B
# c3 lander ruling), so a headline figure only a slide states is withheld by the draw gate AND refused by this one (the
# production sample: 2 of 34 runs' unhedged titles passed on a slide's unhedged figure while the body hedged it). The dev Aswan run 1792985912 drew 'THE 1,200-TON'
# over "nearly 1,090 tonnes to 1,168 tonnes" (source: "nearly 1,200 short tons"), in a metric run.
HEADLINE_KEYS = ("title", "thumbnail", "thumbnail_youtube", "youtube_title")
# the rule as the writer, the copy edit and the checkers are told it (script_engine, trace_check prompts carry it verbatim)
HEADLINE_FIGURE_RULE = ('A figure in a title or a cover line is a signed statement drawn whole: the hedge the facts give it ("about", "over", "nearly", "an estimated", "roughly"), both ends of a span, in the copy\'s unit (in metric copy a ton is a tonne, in US customary copy a short ton; name the ton when it matters). A floor ("1,000+", "over 1,000") only where the fact itself is a floor, with the same kind of hedge. Never one number taken from a range, never a rounded or derived figure the facts do not state; titles and covers are not exempt from hedges. A headline that cannot carry the figure whole leaves the figure out.')
# copy derived from the headline or written to promote it, never evidence for it (the Aswan first comment repeated
# "1,200 tons")
DERIVED_COPY_KEYS = ("first_comment", "emphasis_words", "poll", "share_line", "end_card", "caption", "carousel_caption",
                     "description", "carousel_slides", "story_slides")
_DERIVED_WHERE = re.compile(r"title|cover|thumbnail|caption|comment|description|poll|share", re.IGNORECASE)
_AUDIT_KEYS = ("fact_audits", "final_fact_audits")


_BARE_TON = re.compile(r"(?<!short )(?<!long )(?<!metric )\b(tons?)\b", re.IGNORECASE)


def _tons_in(text: str, system: str | None) -> str:
    """`text` with a ton that does not say which written as the run's: tonnes in a metric run (figures reads a bare
    ton as the US short ton, 2,000 lb, which a US customary run keeps)."""
    if system != "metric":
        return text
    return _BARE_TON.sub(lambda m: "tonnes" if m.group(1).lower().endswith("s") else "tonne", text)


def run_unit_system(payload: Mapping[str, Any]) -> str | None:
    """The run's unit system for an unqualified ton: the narration's mass system, else the deck lead, else its
    lengths' system."""
    from .typography import spoken_system
    spoken = narration(payload)
    return spoken_system(spoken, "mass") or figures.deck_lead(payload) or spoken_system(spoken, "length")


def evidence_texts(payload: Mapping[str, Any]) -> list[str]:
    """The run's signed fields a headline figure may be checked against (see above): never derived copy."""
    sheet = payload.get("fact_sheet")
    out = signed_texts({k: v for k, v in payload.items() if k not in HEADLINE_KEYS + DERIVED_COPY_KEYS + ("fact_sheet",)})
    # a disputed fact is signed with its hedge: its flat statement is no evidence for an unhedged headline figure
    for fact in (sheet.get("facts") or []) if isinstance(sheet, Mapping) else []:
        if isinstance(fact, Mapping):
            hedged = bool(fact.get("disputed")) and str(fact.get("hedge") or "").strip()
            out += [str(fact.get("hedge") or "")] if hedged else [str(fact.get("statement") or ""),
                                                                   str(fact.get("hedge") or "")]
    audits = [a for key in _AUDIT_KEYS for a in (payload.get(key) or [])]
    second = payload.get("second_check")
    if isinstance(second, Mapping):
        audits += list(second.get("audits") or [])
    for audit in audits:
        if not isinstance(audit, Mapping):
            continue
        where = str(audit.get("where") or "")
        if where and not [w for w in re.split(r"[,;/]", where.strip("[] ")) if w.strip()
                          and not _DERIVED_WHERE.search(w)]:
            continue          # an audit OF a title, cover, caption or comment is no evidence for a headline
        out.append(str(audit.get("claim") or ""))   # the audited claim; a checker's note is commentary, not signed
    return [t for t in out if t.strip()]


def disputed_groups(payload: Mapping[str, Any], system: str | None = None) -> list[list[figures.Figure]]:
    """The competing values of each disputed fact (the figures of one dimension its hedge gives, two or more distinct):
    a headline that draws one of them takes one number from a range (RICHAT: 40 km by survey, almost 50 km by
    satellite)."""
    sheet = payload.get("fact_sheet")
    out = []
    for fact in (sheet.get("facts") or []) if isinstance(sheet, Mapping) else []:
        if not (isinstance(fact, Mapping) and fact.get("disputed") and str(fact.get("hedge") or "").strip()):
            continue
        figs = figures.parse(_tons_in(f"{fact.get('statement') or ''} {fact.get('hedge') or ''}", system))
        by_dim: dict = {}
        for f in figs:
            base = figures.in_base(f.values[0], f.unit) if f.values and f.unit else None
            if base:
                by_dim.setdefault(base[0], {})[round(base[1], 6)] = f
        out += [list(v.values()) for v in by_dim.values() if len(v) >= 2]
    return out


def _floor(f: figures.Figure) -> bool:
    return f.lo.endswith("+") or figures.hedge_direction(f.hedge) > 0


def headline_figure_faults(drawn: str, evidence: list[figures.Figure],
                           disputed: list[list[figures.Figure]] = ()) -> list[str]:
    """figure_faults, and the hedge's kind: a floor only for a signed floor; a hedged signed figure drawn with a hedge of
    its own kind (an approximate one approximate, a bound the same bound); never one value of a disputed fact's
    competing values (`disputed`, disputed_groups)."""
    out = []
    for d in figures.parse(drawn):
        hit = next((g for g in disputed for v in g if d.unit and v.unit and len(d.values) == 1
                    and figures.within_tol(d.values[0], d.unit, v.values[0], v.unit)), None)
        if hit:
            out.append(f"{d.drawn()!r}: one number taken from a disputed fact's range "
                       f"({' / '.join(v.drawn() for v in hit)})")
            continue
        # "1,000+ TONNES" is a floor: read as the lower-bound hedge it draws ("MORE THAN 1,000 TONNES")
        as_read = (f"MORE THAN {d.drawn().replace(d.lo, d.lo[:-1], 1)}" if d.lo.endswith("+") and not d.hedge
                   else d.drawn())
        found = figure_faults(as_read, evidence)
        if found:
            out += found
            continue
        same = [s for s in evidence if s.values == tuple(v for v in d.values)
                and figures._canon(s.unit) == figures._canon(d.unit)]
        if not same:
            continue          # a conversion figure_faults accepted
        if _floor(d) and not any(_floor(s) for s in same):
            out.append(f"{d.drawn()!r}: a floor where the signed statement is not one ({same[0].drawn()!r})")
        elif d.hedge and not _floor(d) and all(s.hedge and figures.hedge_direction(s.hedge)
                                               != figures.hedge_direction(d.hedge) for s in same):
            out.append(f"{d.drawn()!r}: the hedge is not the signed one's kind ({same[0].drawn()!r})")
    return out


def headline_faults(payload: Mapping[str, Any]) -> list[str]:
    """One line per headline figure that is not a signed statement (see above); [] when every one is."""
    system = run_unit_system(payload)
    evidence = figures.signed_figures([_tons_in(t, system) for t in evidence_texts(payload)])
    disputed = disputed_groups(payload, system)
    out = []
    for key in HEADLINE_KEYS:
        for text in _strings(payload.get(key)):
            for f in figures.parse(_tons_in(text, system)):
                if not (f.unit or f.scale):
                    continue          # a bare number (a year, "7 wonders") is not a stated quantity here
                for fault in headline_figure_faults(f.drawn(), evidence, disputed):
                    out.append(f"headline {key} {text!r}: {f.drawn()!r} is not a signed statement: "
                               f"{fault.split(': ', 1)[-1]} (rule: headline figure == signed statement)")
    return out


def _in_feet(value: float, unit: str) -> tuple[str, float] | None:
    return figures.in_base(value, unit)


def _converts(d: figures.Figure, signed: list[figures.Figure], need_hedge: bool = True) -> bool:
    """A drawn measured figure that is a signed one's conversion to the other unit (within CONVERT_TOL). A figure in
    the signed value's own unit is never a conversion of it: drawn == signed, judged exactly by figures.truth_faults
    (SYS-R8 integration: the tolerance passed "6 M" for a signed 6.1 m and "85 M" for 85.3 m, a precision loss)."""
    for s in signed:
        for v in (s, s.conv):
            if v is None or not v.unit or len(v.values) != len(d.values):
                continue
            if figures._canon(v.unit) == figures._canon(d.unit):
                continue
            if all(figures.within_tol(a, d.unit, b, v.unit) for a, b in zip(d.values, v.values)):
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


def _hedge_on_line_before(before: str, text: str, f: figures.Figure, signed: list[figures.Figure]) -> bool:
    """True when the line read just before (`before`, same surface) ends with the hedge that `f` on this line lacks,
    and the two lines read as one block draw the figure whole and signed (SYS-F-3, freeze cycle 3: the hook's "MORE
    THAN / 129 FT" was read line by line as '129 FT' with its hedge dropped; in reading order the hedge is drawn).
    Only a hedge carried over the break: the joined figure starts on the line before and its number on this line."""
    joined = f"{before} {text}"
    cut = len(before) + 1
    for g in figures.parse(joined):
        if g.values == f.values and g.start < cut <= joined.find(f.lo, g.start) and g.hedge                 and not figure_faults(g.drawn(), signed):
            return True
    return False


def _nearest(d: figures.Figure, signed: list[figures.Figure]) -> str:
    if not d.values:
        return ""
    best = min(((abs(v - d.values[0]), s) for s in signed for v in s.values), key=lambda t: t[0], default=None)
    return best[1].drawn() if best else ""


QA_FIGURE_ORDER = ("figure_qualifier", "figure", "figure_unit", "figure_dim")   # the YouTube figure row, as read


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
    for rec_file in sorted(run_dir.rglob("*.qa.json")):   # the YouTube thumbnail's drawn lines (visual_engine)
        if any(p in SKIP_DIRS for p in rec_file.relative_to(run_dir).parts):
            continue
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        surface = rec_file.relative_to(run_dir).as_posix().replace(".qa.json", "")
        elements = [e for e in ((data.get("elements") or []) if isinstance(data, dict) else []) if isinstance(e, dict)]
        # freeze SYS batch 3 (SYS-B-4 part 3): the figure row is ONE statement, read in its order (qualifier, figure,
        # unit, dimension word) whatever order the plates were drawn in, never the number and the unit plates alone
        parts = sorted((e for e in elements if str(e.get("name") or "") in QA_FIGURE_ORDER),
                       key=lambda e: QA_FIGURE_ORDER.index(str(e.get("name"))))
        if parts:
            yield surface, "figure", {"name": "figure", "text": " ".join(str(e.get("text") or "") for e in parts),
                                      "box": parts[0].get("box")}
        for e in elements:
            if str(e.get("name") or "") not in QA_FIGURE_ORDER:
                yield surface, str(e.get("name") or ""), e
    for rec_file in sorted(run_dir.glob("*.composition.json")):   # the end card's share line as drawn
        try:
            card = json.loads(rec_file.read_text(encoding="utf-8")).get("end_card") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(card, dict) and card.get("text"):
            yield "end_card", "end_card", {"name": "end_card", "text": str(card["text"])}
    for rec_file in sorted(run_dir.rglob("*.contrast.json")):
        if any(p in SKIP_DIRS for p in rec_file.relative_to(run_dir).parts):
            continue
        try:
            data = json.loads(rec_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        surface = rec_file.relative_to(run_dir).as_posix().replace(".contrast.json", "")
        if isinstance(data, dict) and data.get("clip") == "captions":
            # the caption file's bursts are cards of ONE spoken sequence: a hedge on one card ("AS MANY") and its
            # figure on the next ("AS 20,000 PEOPLE") are one figure, so the cue text is judged joined, in order
            # (production audit 2026-11-18: runs 1793314157, 1793433058); check_run reads the narration's
            # punctuation where the words are the narration's (caption_text, SYS-R8-1)
            yield surface, "captions", {"name": "captions", "text": caption_sequence(data), "_captions": True}
            continue
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


_WORD = re.compile(r"\S+")


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def caption_text(sequence: str, narration_text: str) -> str:
    """The caption sequence as the parser reads it (SYS-R8-1): a caption's words carry no punctuation, so a sentence's
    lead ("By one estimate, ...") and its full stops were lost, and its hedge with them. Where the caption words ARE
    the narration's words, in order (difflib over the words, letters and digits only), the narration's own text for
    them is read, punctuation and all; a caption word that differs from the narration (a changed figure, a dropped
    hedge word) stays as drawn and is judged as drawn. Captions are judged across the sequence, not card by card."""
    if not narration_text or not sequence:
        return sequence
    import difflib
    cap = sequence.split()
    nar = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(narration_text)]
    sm = difflib.SequenceMatcher(None, [_norm(w) for w in cap], [_norm(w) for w, _a, _b in nar], autojunk=False)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(narration_text[nar[j1][1]:nar[j2 - 1][2]])
        elif i2 > i1:
            out.append(" ".join(cap[i1:i2]))
    return " ".join(out)


def caption_sequence(record: Mapping[str, Any]) -> str:
    """A caption record's burst texts joined in burst order, each burst once (its bone and gold elements carry the
    same text)."""
    seen: dict[int, str] = {}
    for k, e in enumerate(record.get("elements") or []):
        if not isinstance(e, dict):
            continue
        m = re.match(r"caption\s+(\d+)", str(e.get("name") or ""))
        key = int(m.group(1)) if m else 10 ** 6 + k
        seen.setdefault(key, " ".join(str(e.get("text") or "").split()))
    return " ".join(t for _k, t in sorted(seen.items()) if t)


# The drawn surfaces each run renders whose layout records carry their drawn text (still_layout.json, the graphics'
# and cards' contrast records, the thumbnail's qa record): a run with such a surface and no judged text is held,
# never passed (production audit 2026-11-18: three published runs passed with 0 elements judged).
DRAWN_SURFACES = ("carousel/carousel_slide_*.jpg", "carousel_fb/carousel_slide_*.jpg", "stories/story_*.jpg",
                  "graphics/graphic_*.mp4", "thumbnail_*.jpg")


def drawn_surfaces(run_dir: Path) -> list[str]:
    """The run's drawn surface files (DRAWN_SURFACES), relative to the run."""
    run_dir = Path(run_dir)
    return sorted({f.relative_to(run_dir).as_posix() for pat in DRAWN_SURFACES for f in run_dir.glob(pat)})


TEXT_NAMES = re.compile(r"^(heading|body|kicker|stat|title|eyebrow|line|tagline|badge|share|figure|label|unit|"
                        r"subject|claim|fact|myth|quote|caption|question|prefix|secondary|place|country|year|event)",
                        re.IGNORECASE)


def _is_text(e: Mapping[str, Any]) -> bool:
    """A text element: its record says so (text, face, size, contrast), or its name is a text role's (an older record
    kept only the box)."""
    return ("text" in e or "font" in e or "px" in e or "contrast" in e or "planned" in e
            or bool(TEXT_NAMES.match(str(e.get("name") or ""))))


# A figure drawn in parts (a cover's or Story frame's qualifier, figure, unit and dimension word; a number card's prefix,
# figure and unit) is judged as the one figure its consecutive part elements draw ("ABOUT" + "280" + "FT DEEP").
FIGURE_PARTS = re.compile(r"^(figure_qualifier|qualifier|hedge|figure|figure_unit|figure_dim|unit|prefix)$",
                          re.IGNORECASE)


def _merged(elements: Iterable[tuple[str, str, dict[str, Any]]]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    held: list[tuple[str, str, dict[str, Any]]] = []

    def flush():
        if not held:
            return []
        if len(held) == 1 or any("text" not in e for _s, _n, e in held):
            return list(held)
        text = " ".join(str(e.get("text") or "") for _s, _n, e in held)
        return [(held[0][0], "figure", {**held[0][2], "name": "figure", "text": text})]
    for item in elements:
        surface, name, _e = item
        if FIGURE_PARTS.match(name) and (not held or held[-1][0] == surface):
            held.append(item)
            continue
        yield from flush()
        held = []
        if FIGURE_PARTS.match(name):
            held.append(item)
            continue
        yield item
    yield from flush()


# A timeline's gap label rows ("gap2", its continuation "gap2_1"): derived from the two signed years it spans.
GAP = re.compile(r"^gap\d+(?:_\d+)?$")


def _signed_years(payload: Mapping[str, Any]) -> dict[int, bool]:
    """{year: approximate} of the run's signed timeline entries (negative years BC)."""
    out: dict[int, bool] = {}
    for ev in payload.get("timeline") or []:
        if isinstance(ev, Mapping) and ev.get("year") is not None:
            try:
                out[int(ev["year"])] = bool(ev.get("approximate"))
            except (TypeError, ValueError):
                continue
    return out


def gap_faults(text: str, derived: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> list[str]:
    """[] when a gap label's figure is the elapsed time figures.elapsed gives between two signed years; else one line
    per fault. With the renderer's `derived` record (its rule and operands: graphics.derive_gap) the arithmetic is
    re-computed from those operands, each of which must be a signed timeline year at its signed precision. A record
    without one (written before records carried operands) is checked against every ordered pair of signed years (the
    renderer draws a gap between two kept entries, oldest first): the label passes only when one pair's rule gives it."""
    figs = [f for f in figures.parse(text) if f.values]
    if not figs:
        return []                 # a continuation row ("LATER"): no figure
    if len(figs) != 1 or figs[0].hi:
        return ["a gap label states one elapsed time"]
    d = figs[0]
    shown, hedged = round(d.values[0]), bool(d.hedge)
    years = _signed_years(payload)
    if isinstance(derived, Mapping) and derived.get("rule") == "elapsed_years":
        a, b, approx = int(derived["from"]), int(derived["to"]), bool(derived.get("approximate"))
        out = [f"its operand {y} is not a signed timeline year" for y in (a, b) if y not in years]
        if not out and approx != (years[a] or years[b]):
            out.append(f"its operands' precision (approximate {approx}) is not the signed dates'")
        want = figures.elapsed(a, b, approx)
        if want is None:
            out.append(f"no gap is drawn under two years ({a} to {b})")
        elif (shown, hedged) != want:
            out.append(f"the elapsed time from {a} to {b} is {'ABOUT ' if want[1] else ''}{want[0]:,} years by the "
                       "timeline's rule")
        return out
    ordered = sorted(years)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if figures.elapsed(a, b, years[a] or years[b]) == (shown, hedged):
                return []
    return [f"no two signed timeline years are {'about ' if hedged else ''}{shown:,} years apart by the timeline's rule"]


SOURCE_TOL = 1e-4     # an operand's recorded value against its signed source: the same number, in the drawing's unit


def derived_faults(name: str, text: str, derived: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> list[str]:
    """[] when a derived comparison (a scale's bracket figure, its line of meaning: freeze cycle 5, lander ruling
    2026-11-26, figures.DERIVED_RULE) holds; else one line per fault. Its record is re-computed (figures.
    derivation_faults: an allowed operation, sourced operands, the result from their signed source values rounded
    once, the hedge) and every operand's value is checked against what it claims to be (the same number, SOURCE_TOL:
    never a figure as rounded for drawing): the run's signed scale_fact, or the reference file's sourced landmark. A bracket's
    drawn figure is the record's. A bracket that states a figure with no record fails closed."""
    if not isinstance(derived, Mapping):
        return ["a derived figure without its derivation record (figures.DERIVED_RULE)"]
    out = figures.derivation_faults(derived)
    fact = payload.get("scale_fact") if isinstance(payload.get("scale_fact"), Mapping) else None
    for o in derived.get("operands") or []:
        unit = str(o.get("unit") or "").upper()
        per_ft = _FT_PER.get(unit)
        if o.get("source") == "scale_fact":
            try:
                feet = float(fact["value"]) * _FT_PER["M" if str(fact.get("unit")).lower() == "m" else "FT"]
            except (TypeError, KeyError, ValueError):
                out.append(f"its operand {o.get('name')!r} claims the run's scale_fact, which the run does not sign")
                continue
            # the operand's VALUE is the signed value itself (the arithmetic is done on it: figures.DERIVED_RULE);
            # its drawn figure may round it within CONVERT_TOL (figures.derivation_faults)
            if per_ft is None or abs(float(o["value"]) * per_ft - feet) > SOURCE_TOL * max(feet, 1e-9):
                out.append(f"its operand {o.get('text')!r} ({float(o['value']):g}) is not the run's signed scale_fact "
                           f"({fact.get('value')} {fact.get('unit')})")
        elif o.get("role") == "reference":
            from .reference_facts import references
            ref = next((r for r in references() if r.get("id") == o.get("reference_id")
                        or r.get("label") == o.get("name")), None)
            if ref is None or not ref.get("source_url") or o.get("source") != ref.get("source_url"):
                out.append(f"its operand {o.get('name')!r} is not a sourced landmark of the reference file")
            elif per_ft is None or abs(float(o["value"]) * per_ft - float(ref["feet"])) > SOURCE_TOL * float(ref["feet"]):
                out.append(f"its operand {o.get('text')!r} ({float(o['value']):g}) is not the reference file's "
                           f"figure for {ref['label']}")
        else:
            out.append(f"its operand {o.get('name') or o.get('role')!r} is neither a signed field of the run nor a "
                       "sourced reference")
    if name == "bracket" and str(derived.get("shown") or ""):
        drawn = [f.lo for f in figures.parse(text) if f.values]
        if str(derived["shown"]) not in drawn:
            out.append(f"it draws {text!r}, not its derivation's {derived.get('text')!r}")
    return out


def check_run(run_dir: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The gate on one run: {"faults": [...], "unrecorded": [...], "judged": n, "derived": n, "surfaces": n}. Each
    fault names the surface, the element, the drawn figure, what is wrong and the nearest signed value."""
    signed = figures.signed_figures(signed_texts(payload))
    spoken = narration(payload)
    faults, unrecorded = [], []
    judged = derived = 0
    surfaces: set[str] = set()
    texted: set[str] = set()        # the surfaces whose records carry drawn text
    prev: tuple[str, str] = ("", "")   # the surface and text of the line read before this one (reading order)
    for surface, name, e in _merged(_elements(run_dir)):
        if not _is_text(e):
            continue
        surfaces.add(surface)
        if "text" not in e:
            unrecorded.append(f"{surface} {name}: its record carries no drawn text")
            continue
        texted.add(surface)
        text = str(e.get("text") or "")
        before, prev = (prev[1] if prev[0] == surface else ""), (surface, text)
        if e.get("_captions"):
            text = caption_text(text, spoken)
        if not any(ch.isdigit() for ch in text) and not any(w in text.lower().split() for w in figures.NUMBER_WORDS):
            continue
        if GAP.match(name):         # a derived gap label: its arithmetic between two signed years, never a claim
            derived += 1
            for fault in gap_faults(text, e.get("derived"), payload):
                faults.append(f"{surface} ({name}): drew {text!r}: {fault}")
            continue
        rec = e.get("derived")
        if (isinstance(rec, Mapping) and rec.get("rule") in figures.DERIVED_OPS) or name == "bracket":
            derived += 1      # a derived comparison: re-computed from its recorded operands, never a claim
            for fault in derived_faults(name, text, rec if isinstance(rec, Mapping) else None, payload):
                faults.append(f"{surface} ({name}): drew {text!r}: {fault}")
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
            found = figure_faults(f.drawn(), signed, need_hedge=not CONVERSION.match(name))
            if found and before and _hedge_on_line_before(before, text, f, signed):
                found = []
            for fault in found:
                near = _nearest(f, signed)
                faults.append(f"{surface} ({name}): drew {f.drawn()!r}: {fault.split(': ', 1)[-1]}"
                              + (f"; nearest signed value {near!r}" if near else ""))
    faults += headline_faults(payload)
    # Fail closed (production audit 2026-11-18): a drawn surface with no record carrying its text is never passed
    # unjudged. Carousel slides, Story frames and graphics record their text by construction, so each one is checked;
    # the root covers and thumbnails when no surface of the run recorded any text at all.
    def recorded(rel: str) -> bool:
        stem = rel.rsplit(".", 1)[0]
        return bool({rel, stem, f"cover/{rel}"} & texted)
    present = drawn_surfaces(run_dir)
    missing = [rel for rel in present if not recorded(rel) and ("/" in rel or not texted)]
    unrecorded += [f"{rel}: drawn, but no layout record carries its drawn text" for rel in missing]
    return {"faults": faults, "unrecorded": unrecorded, "judged": judged, "derived": derived,
            "surfaces": len(surfaces), "drawn_surfaces": len(present)}


def release_check(run_dir: Path, payload: Mapping[str, Any]) -> tuple[str, list[str] | str]:
    """(status, detail) for the release report: "fail" with one line per fault; "needs_review" when an element's
    record carries no text, or a drawn surface has no record carrying its text (it cannot be judged: never a pass
    with nothing judged); else "pass" with the counts."""
    res = check_run(run_dir, payload)
    if res["faults"]:
        return "fail", res["faults"]
    if res["unrecorded"]:
        return "needs_review", res["unrecorded"][:12] + ([f"and {len(res['unrecorded']) - 12} more"]
                                                         if len(res["unrecorded"]) > 12 else [])
    return "pass", (f"Every drawn number is a signed value with its unit and hedge: {res['judged']} figure "
                    f"element(s) on {res['surfaces']} surface(s); {res['derived']} derived (rulers, scale bars, "
                    "conversions' references, numerals) judged by their own critics." + held_note(payload))


def held_note(payload: Mapping[str, Any]) -> str:
    """The surfaces this render refused (refusal.LayoutError, payload["layout_holds"]: the cover job's per-surface
    records and the decks' holds), named in the gate's pass so it never reads as judging a surface that was not
    drawn (cycle 0 round 8 integration: the cover job holds a refused surface; its own checks block the release)."""
    holds = [h for h in (payload.get("layout_holds") or []) if isinstance(h, Mapping)]
    held = [str(h.get("surface") or h.get("deck") or "?") for h in holds if not h.get("drawn")]
    # cycle 3 (SYS-S1-3): a surface drawn with a part left out (refusal.omit's ledger rows, "drawn": True) is named
    # apart: it was judged, without the part its rule left out
    partial = [f"{h.get('surface')} ({h.get('omitted')!s}; rule {h.get('rule') or '?'})" for h in holds
               if h.get("drawn")]
    return ((f" Held, not drawn (refused, shown on the card): {', '.join(held)}." if held else "")
            + (f" Drawn without a part (recorded, shown on the card): {', '.join(partial)}." if partial else ""))


# ------------------------------------------------------------------------------- contradictory signed values (ruling B)
# Operator ruling (2026-11-18, B): a run whose signed values for the SAME quantity disagree is held for review, both
# values named (audit run 1793034688: 9,600-8,200 BCE on a carousel slide, 9500-9000 BCE in the narration, Story
# and number card). "Same quantity", conservatively: two signed spans in the same unit and era whose intervals overlap
# but whose ends differ, whose dimension words do not differ (DEEP vs LONG are two quantities), and whose own sentences
# share a content word (the subject: "enclosures", "megaliths"). Two different quantities (a founding and an
# abandonment date, a depth and a width, two counts) never match; a single value inside a span is not a contradiction.
_STOP = frozenset(("about", "above", "after", "again", "against", "along", "among", "around", "because", "before",
                   "being", "below", "between", "beyond", "could", "during", "every", "first", "known", "later",
                   "least", "might", "never", "other", "over", "since", "their", "there", "these", "those",
                   "through", "under", "until", "which", "while", "within", "without", "would", "years", "century",
                   "centuries", "built", "began", "ended", "dated", "dates", "between", "roughly", "nearly",
                   "approximately", "estimated"))


def signed_items(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(field path, text) of the run's signed copy, for naming where a value is signed."""
    out: list[tuple[str, str]] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, str):
            if v.strip():
                out.append((path, v))
        elif isinstance(v, Mapping):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else str(k))
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i + 1}]")
    for key in SIGNED_COPY_KEYS:
        walk(payload.get(key), key)
    return out


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start))
    b = min([x for x in (text.find(". ", end), text.find("; ", end)) if x >= 0] or [len(text)])
    return text[a + 1 if a >= 0 else 0:b]


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 5 and w not in _STOP}


def contradictions(payload: Mapping[str, Any]) -> list[str]:
    """One line per pair of signed spans that state the same quantity differently (see above), naming both values
    and where each is signed; [] when the run's signed values agree."""
    spans = []
    for path, text in signed_items(payload):
        for f in figures.parse(text):
            if f.hi and len(f.values) == 2:
                lo, hi = sorted(f.years)         # an era-crossing span on one time line (BC negative)
                spans.append((path, f, lo, hi, _content_words(_sentence(text, f.start, f.end))))
    out, seen = [], set()
    for i, (pa, fa, la, ha, wa) in enumerate(spans):
        for pb, fb, lb, hb, wb in spans[i + 1:]:
            if (la, ha) == (lb, hb) or figures._canon(fa.unit) != figures._canon(fb.unit):
                continue
            if not figures._era_ok(fa.era, fb.era) or bool(fa.era) != bool(fb.era):
                continue
            if fa.tail and fb.tail and fa.tail != fb.tail:
                continue
            if max(la, lb) > min(ha, hb) or not (wa & wb):
                continue
            key = tuple(sorted((fa.drawn(), fb.drawn())))
            if key in seen:
                continue
            seen.add(key)
            out.append(f"the signed copy states one quantity two ways: {fa.drawn()!r} ({pa}) and {fb.drawn()!r} "
                       f"({pb}); shared words: {', '.join(sorted(wa & wb)[:4])}")
    return out
