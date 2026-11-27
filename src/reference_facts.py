"""Source-backed scale references shared by rendering and factual review."""
import json
import math
import re

from .config import DATA_DIR

REFERENCE_FILE = DATA_DIR / "reference_facts.json"


def references():
    return json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))["references"]


def selected_reference(scale_fact):
    if not scale_fact:
        return None
    feet = scale_fact["value"] * (3.28084 if scale_fact["unit"] == "m" else 1)
    if feet <= 0:
        return None
    # Within 1/3x-3x of the subject, a landmark at least the subject's size first (the subject is measured up against
    # it: a 107 ft temple against the 305 ft Statue of Liberty, never the 60 ft carved head, which is for the 20-100 ft
    # band it was sourced for), the closest by ratio of those; else the closest by ratio.
    inside = [r for r in references() if 1 / 3 <= feet / r["feet"] <= 3]
    if not inside:
        return None
    taller = [r for r in inside if r["feet"] >= feet]
    return min(taller or inside, key=lambda r: abs(math.log(feet / r["feet"])))


def measurement_text(feet):
    """A size in feet as drawn with its metres: "280 FT (85 M)"; under 20 ft, feet and inches ("5 FT 9 IN (1.75 M)").
    The metres are figures.converted_number's (SYS-R8-1): the fewest decimals within the drawn-figure gate's
    CONVERT_TOL of the feet ("15 FT (4.6 M)"; whole metres drew "5 M", 9 % over)."""
    from .figures import converted_number
    metres = converted_number(feet, "FT", "M")
    if feet >= 20:
        return f"{feet:,.0f} FT ({metres} M)"
    whole, inches = int(feet), round((feet % 1) * 12)
    if inches == 12:                 # review 2 (N6): 11.5 in and over round up into the next foot, never "12 IN"
        whole, inches = whole + 1, 0
    return f"{whole} FT {inches} IN ({metres} M)"


def with_reference_evidence(sheet, payload):
    ref = selected_reference(payload.get("scale_fact"))
    if not ref:
        return sheet
    fact = {"id": ref["id"], "statement": ref["statement"], "disputed": False, "hedge": "",
            "evidence": [{"url": ref["source_url"], "tier": 1, "quote": ref["quote"]}]}
    return {**sheet, "facts": [f for f in sheet["facts"] if f["id"] != fact["id"]] + [fact]}


_HEDGED_BEFORE = re.compile(r"(approximately|about|around|roughly|nearly)\W+(?:\w+\W+){0,3}$", re.IGNORECASE)


def stated_figures(ref, unit):
    """The figures the reference's own statement states in `unit` ("M" or "FT"), each (value, text, hedged) at its
    stated precision: "146.5 metres" -> (146.5, "146.5 M", False); "approximately 481 feet" -> (481.0, "481 FT", True);
    "5 feet 9 inches" -> (5.75, "5 FT 9 IN", ...). A figure is hedged when a hedge word stands just before it (review 2,
    N1: a landmark is drawn as a signed figure, at its signed precision)."""
    text = str((ref or {}).get("statement") or "")
    out = []
    if unit == "M":
        pat = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:metres|meters|metre|meter|m)\b", re.IGNORECASE)
        for m in pat.finditer(text):
            num = m.group(1).replace(",", "")
            out.append((float(num), f"{m.group(1)} M", bool(_HEDGED_BEFORE.search(text[:m.start()]))))
        return out
    pat = re.compile(r"(\d[\d,]*)\s*(?:feet|foot|ft)\b(?:\s*(\d+)\s*(?:inches|inch|in)\b)?", re.IGNORECASE)
    for m in pat.finditer(text):
        ft = float(m.group(1).replace(",", ""))
        if m.group(2):
            out.append((ft + int(m.group(2)) / 12, f"{m.group(1)} FT {m.group(2)} IN",
                        bool(_HEDGED_BEFORE.search(text[:m.start()]))))
        else:
            out.append((ft, f"{m.group(1)} FT", bool(_HEDGED_BEFORE.search(text[:m.start()]))))
    return out


def reference_is_rounded(ref):
    """Whether the reference file calls its figure rounded or approximate (its context or its statement): a gap or a
    ratio derived from it is then never exact (review 2, N4)."""
    words = (str((ref or {}).get("context") or "") + " " + str((ref or {}).get("statement") or "")).lower()
    return any(w in words for w in ("rounded", "approximately", "about ", "around "))


def reference_by_label(label):
    """The reference row whose label is `label`, or None."""
    return next((r for r in references() if r["label"] == label), None)


def reference_note(label, max_words=4):
    """A short note on what a landmark's figure measures, from its sourced context (round 4, 2026-11-02): the
    context's first clause in capitals ("Ground to torch tip, including ..." -> "GROUND TO TORCH TIP"), so the
    305 FT is not read as the 151 ft statue alone; "" when there is no context or its first clause is longer than
    `max_words` (a note is never cut mid-clause). Only the reference file's own words."""
    import re
    ref = reference_by_label(label)
    context = str((ref or {}).get("context") or "").strip()
    clause = re.split(r"[,;]|\.(?:\s|$)", context, maxsplit=1)[0].strip()
    if not 2 <= len(clause.split()) <= max_words:   # one word is a cut abbreviation ("U.S."), not a clause
        return ""
    return clause.upper()
