"""Source-backed scale references shared by rendering and factual review."""
import json
import math

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
    closest = min(references(), key=lambda r: abs(math.log(feet / r["feet"])))
    return closest if 1 / 3 <= feet / closest["feet"] <= 3 else None


def measurement_text(feet):
    metres = feet / 3.28084
    return (f"{feet:,.0f} FT ({metres:,.0f} M)" if feet >= 20 else
            f"{int(feet)} FT {round((feet % 1) * 12)} IN ({metres:.1f} M)")


def with_reference_evidence(sheet, payload):
    ref = selected_reference(payload.get("scale_fact"))
    if not ref:
        return sheet
    fact = {"id": ref["id"], "statement": ref["statement"], "disputed": False, "hedge": "",
            "evidence": [{"url": ref["source_url"], "tier": 1, "quote": ref["quote"]}]}
    return {**sheet, "facts": [f for f in sheet["facts"] if f["id"] != fact["id"]] + [fact]}
