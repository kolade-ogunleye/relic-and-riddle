"""QF-61 measure: does a number reach the illustration generator only from a fact's structured, checked count or
measure (operator decision 2026-11-12, structured counts only), and does the vision check reject a wrong count and
anything the facts do not name?

    python measure.py <repo root>

The code under test is <root>'s own src, so the same script measures any commit. Inputs are committed: the tracked
fact sheets (data/fact_sheets/*.json) and the probe sentences below. Nothing is written; no provider is called (the
vision answer is mocked: this measures the gate's boundary, not a model's accuracy). Quantities records are signed
in memory under a throwaway measurement key (config.FACT_RECORD_KEY is set in this process only).

The oracle (this script's own, never the code under test): "prose numbers" are the figures and cardinal number words
of the generator prompt (image_gen.build_prompt) once the fixed template text (the module's upper-case string
constants and its era tables) is removed and, on a tree with structured quantities, the counts sentence and the
measures built from the spec's structured counts and measures; every spec count must be an "exact" entry of a
supporting fact's quantities record ("not from a structured field" otherwise). "one of the ..." ("one of the foremost
achievements") is an idiom, not a count, and is not a prose number (round 6).

Round 6 adds two prompt checks, both by this script's own oracle. "Broken subject sentences" are the generator
subject's sentences that BROKEN finds (dangling prepositions and articles, "of below", "From the to the", "in during",
"A investigation", "since,", a measuring or dating verb or a modal left without its object, a label left without its
number, "ago" without a time, double spaces, orphan punctuation, a lower-case start). "Inventory conflicts" are
prompts whose "Draw only what the facts name: ..." list (rounds 4 and 5) leaves out an item the same prompt says it
"clearly shows", or a word of the subject that the vision check allows (its inventory terms). Part 4 compares the
generator-side options for the closed inventory on the same real specs, without generating anything.

Round 7 adds the byte comparison. "Altered subject sentences" are the generator subject's pieces that are not,
byte for byte, a sentence of the supporting facts' statements: the subject must be a sequence of whole source
sentences joined by single spaces (white-space runs inside a statement count as one space). The source sentences
are cut by this script's own splitter (after ".", "!" or "?" and white space), never by the code under test; a
subject that the code made of two of them still tiles. Every spec's prompt must carry "Subject: <the spec's
subject>". "spec_fact_ids" lists the facts that give a picture, so two commits can be compared (pictures lost).

Part 1, the 271 real facts: every physical fact (a material or form word in its statement or evidence) through
spec_from_facts with the fact as its own line. Part 2, the review-4 probes (all five blocking classes, plus plain
prose counts and measures) through spec_from_facts; the structured probes (an exact ship count, the same count with
every other qualifier, the Kailasa comparison set offered as exact). Part 3, the closed inventory and the count check
through the real vision_rubric on the spec of the three-mast ship fact (its structured counts on a tree that has them).

Prints JSON.
"""

import json
import re
import sys
from pathlib import Path

_NUMERAL = re.compile(r"\d+(?:[.,]\d+)*|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                      r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
                      r"seventy|eighty|ninety|hundred|thousand|million|dozen|dozens|hundreds|thousands)\b",
                      re.IGNORECASE)
# "one of the foremost": an idiom, not a count
_ONE_IDIOM = re.compile(r"\bone\s+(?=of\s+(?:the|its|their|his|her|our|these|those|them|many|several)\b)|\bone\s+another\b",
                        re.IGNORECASE)
_PREP = r"(?:of|in|on|at|by|for|from|to|since|until|during|between|among|with|including|than|as)"
BROKEN = [re.compile(p) for p in (
    r"\b" + _PREP + r"\s*(?:[,.;:!?]|$)",                                         # "in during salvage" / "since,"
    (r"\b(?:of|in|on|at|by|to|since|until|during|with)\s+(?:of|in|on|at|by|to|since|until|during|with|below|above|"
     r"including|for)\b"),                                                        # "of below", "of including"
    r"\b(?:[Tt]he|[Aa]n?|their|its)\s*(?:[,.;:!?]|$)",                            # "the,"
    r"\b(?:[Tt]he|[Aa]n?)\s+(?:of|in|on|at|by|to|from|since|during|with|the|a|an|and|or)\b",   # "From the to the"
    r"\b[Aa]\s+[aeioAEIO]\w", r"\b[Aa]n\s+[bcdfgjklmnpqrstvwxyz]\w",              # "A investigation"
    r"\b(?:and|or|but)\s*[.;!?]",                                                 # "drills and."
    r"\s{2,}", r"\s[,.;:!?]", r"[,;:]\s*[,.;:]", r"^[^A-Z]", r"[.!?]\s+[a-z]",
    r"\b(?:from|of|since|the|to|and)\s+ago\b",                                    # "dating from ago"
    r"\b(?:weighs|weighing|measures|measuring|spanning|plunging|reaching|totals)\s*[,.;:!?]",
    r"\b(?:on|in|as|at|of)\s+(?:Cave|Pillar|Chapter|Room|Tomb|Gate|Gallery|Level)\s*(?:[,.;:]|\s(?:in|of|on|at|the)\b)",
    r"\b(?:may|might|can|could|will|would|should|must)\s*[,.;:!?]",
    r"\bdat(?:e|es|ed|ing)(?:\s+back)?\s*[,.;:!?]")]
KEY = "measurement-only-key-not-a-secret-0123456789abcdef"
# round 7: a sentence that states a number is left out of the subject whole, so the structured ship fact carries a
# second, number-free sentence (its signed counts are the first sentence's own)
SHIP = ("The wooden ship had three tall masts and two sails, and was 34 cm long. "
        "The hull of the wooden ship was built of oak planks.")
SHIP_COUNTS = [{"element": "mast", "count": 3, "qualifier": "exact", "source": "three tall masts"},
               {"element": "sail", "count": 2, "qualifier": "exact", "source": "two sails"}]
SHIP_MEASURES = [{"element": "ship", "measure": "34 cm", "qualifier": "exact"}]
# round 7: the structured probes carry a number-free sentence too, so that a spec exists and the check is not vacuous
GEARS = "The bronze mechanism had 30 gears. The bronze mechanism was housed in a wooden case."
CLIFF = " The rock-cut temple was carved from the basalt cliff."
KAILASA = "Known as Cave 16, this rock-cut stone temple is the largest temple among the 34 monuments at the site."
REVIEW4 = {
    "label_number_and_verb": [
        "Stone column 7 bears 12 carved reliefs.", "Cave 16 houses a stone temple.", "Room 12 contains a bronze lamp.",
        "Tomb 5 holds a stone coffin.", "Pillar 43 shows a carved stone vulture.",
        "Gate 3 opens onto a stone courtyard.", "Cave 16 features 3 stone shrines.",
        "The stone fort had 4 towers before 1 collapsed."],
    "distributive": [
        "Each of the 4 stone towers has 3 windows.", "The 4 stone towers have 3 windows each.",
        "Every stone tower has 3 windows.", "Each stone tower has 3 windows.",
        "There are 3 windows in each stone tower.", "The stone hall has 4 columns on each side.",
        "The stone hall has 4 columns along each side.", "The stone gate has 4 columns on either side of the door.",
        "The wooden ship had 20 oars on each side.", "The wooden ship had 20 oars a side."],
    "separate_groups": [
        "The stone fort had 3 round towers and 3 square towers.",
        "The stone fort had 3 round towers; its gatehouse had 3 towers.",
        "The stone temple had 2 bronze doors and 2 stone doors.",
        "The stone fort had 4 towers in 1500 and 2 towers in 1800."],
    "membership_and_fractions": [
        "The bronze temple was one of the three largest temples.", "The bronze bell is the largest of the 5 bells.",
        "The stone temple is one of 7 temples.", "The stone temple stands among 7 temples.",
        "The stone temple is the oldest of 7 temples.", "Two-thirds of the 90 stone columns survive.",
        "About 40% of the 90 stone columns survive.", KAILASA],
    "approximations_hedges_plans": [
        "The bronze mechanism had ~30 gears.", "The bronze mechanism had ≈30 gears.",
        "The stone obelisk weighs ~300 tonnes.", "The bronze mechanism had 30 gears, or more.",
        "The bronze mechanism had 30 gears at most.", "The bronze mechanism had 30 gears at least.",
        "The bronze mechanism had 30 gears, give or take.", "Scholars estimate the bronze mechanism had 30 gears.",
        "The bronze mechanism is believed to have had 30 gears.", "The bronze mechanism reportedly had 30 gears.",
        "Scans suggest the bronze mechanism originally had 35 gears.",
        "The stone statue was designed to weigh 1,000 tonnes.", "The stone statue was to weigh 1,000 tonnes.",
        "The stone tower was meant to be 42 metres tall."],
    "plain_prose_counts_and_measures": [
        "The marble temple had two statues.", "The stone stair had 1,000 steps.", "The stone wall had 12 niches.",
        "The bronze mechanism had thirty-five gears.",
        "The marble dome measures approximately 38 meters (125 feet) across.",
        "Derinkuyu was linked to the stone city of Kaymakli across 8 to 9 kilometres of connecting tunnels.",
        "The stone tower is forty metres tall.", SHIP],
}
SMUGGLED = ["cannon mount", "flag part", "human figure detail", "lighthouse section", "wooden stand", "brass handle",
            "carved pedestal"]


def _setup(root: Path):
    sys.path.insert(0, str(root))
    from src import config
    config.FACT_RECORD_KEY = KEY
    from src import image_gen
    try:
        from src import fact_quantities
    except ImportError:
        fact_quantities = None
    return image_gen, fact_quantities


def _fact(fq, statement, counts=(), measures=(), fid="F1"):
    value = {"id": fid, "statement": statement, "kind": "number", "disputed": False, "hedge": "",
             "evidence": [{"url": "https://www.britannica.com/topic/probe", "tier": 1, "quote": statement,
                           "host": "britannica.com", "fetch": "web_search"}]}
    if fq is not None:
        record, _ = fq.check(value, list(counts), list(measures))
        if record:
            value["quantities"] = record
    return value


def _spec(image_gen, *facts):
    line = facts[0]["statement"]
    return image_gen.spec_from_facts(line, {}, {"facts": list(facts)},
                                     {"sentence_trace": [{"sentence": line, "fact_ids": [f["id"] for f in facts]}]})


def prose_numbers(image_gen, spec) -> int:
    text = image_gen.build_prompt(spec)
    text = text.replace(f"Viewpoint: {spec.get('viewpoint') or 'a three-quarter view'}.", " ")   # the code's own
    for name in dir(image_gen):
        value = getattr(image_gen, name)
        if name.isupper() and isinstance(value, str) and len(value) > 20:
            text = text.replace(value, " ")
    for table in (getattr(image_gen, "ERA_IDIOM", {}), getattr(image_gen, "ERA_LABEL", {})):
        for value in table.values():
            text = text.replace(value, " ")
    sentence = getattr(image_gen, "counts_sentence", None)
    if sentence:
        if spec.get("counts"):
            text = text.replace(sentence(spec["counts"]), " ")
        for m in spec.get("measures") or []:
            text = text.replace(m, " ", 1)
    return len(_NUMERAL.findall(_ONE_IDIOM.sub(" ", text)))


def broken_sentences(spec) -> list[str]:
    """The generator subject's sentences that BROKEN flags (this script's own grammar oracle)."""
    subject = str(spec.get("subject") or "")
    return [s for s in re.split(r"(?<=[.!?;])\s+", subject) if s and any(p.search(s) for p in BROKEN)]


def _source_sentences(statements) -> list[str]:
    return [p for s in statements for p in re.split(r"(?<=[.!?])\s+", " ".join(str(s or "").split())) if p]


def subject_tiles(spec, statements) -> tuple[int, int]:
    """(sentences of the subject, those that are not byte-identical to a source sentence). The subject is tiled with
    whole source sentences joined by single spaces; what cannot be tiled counts as one altered sentence per piece
    that the script's own splitter finds in it."""
    subject = str(spec.get("subject") or "")
    pieces = sorted(set(_source_sentences(statements)), key=len, reverse=True)
    reach = {0: 0}                          # position -> tiles used to reach it
    for pos in range(len(subject) + 1):
        if pos not in reach:
            continue
        for piece in pieces:
            end = pos + len(piece)
            if subject.startswith(piece, pos) and (end == len(subject) or subject[end] == " "):
                nxt = end + 1 if end < len(subject) else end
                reach.setdefault(nxt, reach[pos] + 1)
    if subject and len(subject) in reach:
        return reach[len(subject)], 0
    n = len(_source_sentences([subject]))
    return n, n


def canon(word: str) -> str:
    word = word.lower()
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word


def draw_only_list(prompt: str) -> list[str] | None:
    m = re.search(r"Draw only what the facts name: (.*?)\. Add no other", prompt)
    return [x.strip() for x in m.group(1).split(",")] if m else None


def subject_words(spec) -> set:
    """The subject's words that the vision check allows a drawn item to be named with."""
    terms = {canon(t) for t in spec.get("inventory_terms") or []}
    return {canon(w) for w in re.findall(r"[a-z]+", str(spec.get("subject") or "").lower())} & terms


def inventory_conflicts(image_gen, spec) -> dict:
    """Does the generator's "Draw only" list leave out an item the prompt says it clearly shows, or a word of the
    subject that the vision check allows?"""
    listed = draw_only_list(image_gen.build_prompt(spec))
    if listed is None:
        return {"list": False, "excludes_a_clearly_shows_item": False, "excludes_a_subject_word": False}
    words = {canon(w) for x in listed for w in x.split()}
    shows = [canon(w) for x in spec.get("forms") or [] for w in x.split()]
    return {"list": True, "excludes_a_clearly_shows_item": any(x not in words for x in shows),
            "excludes_a_subject_word": bool(subject_words(spec) - words)}


def not_structured(spec, facts) -> int:
    """Spec counts and measures that are not an exact entry of a supporting fact's quantities record."""
    exact_counts = {(c.get("element"), c.get("count")) for f in facts for c in (f.get("quantities") or {}).get(
        "counts") or [] if c.get("qualifier") == "exact"}
    exact_measures = {m.get("measure") for f in facts for m in (f.get("quantities") or {}).get("measures") or []
                      if m.get("qualifier") == "exact"}
    return (sum((c.get("element"), c.get("count")) not in exact_counts for c in spec.get("counts") or [])
            + sum(m not in exact_measures for m in spec.get("measures") or []))


SPECS: list = []


def part1(root: Path, image_gen) -> dict:
    facts, sheet_of = [], {}
    for p in sorted((root / "data" / "fact_sheets").glob("*.json")):
        for f in json.loads(p.read_text(encoding="utf-8")).get("facts") or []:
            if isinstance(f, dict):
                facts.append(f)
                sheet_of[id(f)] = p.stem
    r = {"facts": len(facts), "facts_with_structured_quantities": sum("quantities" in f for f in facts),
         "physical_facts": 0, "specs": 0, "specs_refused_for_quantities": 0, "spec_counts": 0, "spec_measures": 0,
         "spec_numbers_not_from_a_structured_field": 0, "prompts_with_prose_numbers": 0, "prose_numbers": 0,
         "prompts_with_no_number_instruction": 0, "specs_refused_no_clean_subject": 0,
         "subjects_with_broken_sentences": 0, "broken_sentences": 0, "prompts_with_a_draw_only_list": 0,
         "draw_only_excludes_a_clearly_shows_item": 0, "draw_only_excludes_a_subject_word": 0,
         "subject_sentences": 0, "subject_sentences_altered": 0, "prompts_without_the_spec_subject": 0,
         "spec_fact_ids": []}
    no_count = getattr(image_gen, "NO_COUNT", None)
    for f in facts:
        statement = " ".join(str(f.get("statement") or "").split())
        quotes = " ".join(str(e.get("quote") or "") for e in f.get("evidence") or [] if isinstance(e, dict))
        text = f" {image_gen._norm(statement)} {image_gen._norm(quotes)} "
        if not any(f" {m} " in text for m in image_gen._MATERIALS + image_gen._FORMS):
            continue
        r["physical_facts"] += 1
        spec, why = image_gen.spec_from_facts(statement, {}, {"facts": [dict(f, statement=statement)]})
        if spec is None:
            r["specs_refused_for_quantities"] += "quantit" in why
            r["specs_refused_no_clean_subject"] += "without their numbers" in why
            continue
        SPECS.append(spec)
        r["specs"] += 1
        r["spec_fact_ids"].append(f"{sheet_of[id(f)]}:{f.get('id')}")
        n_sentences, altered = subject_tiles(spec, [statement])
        r["subject_sentences"] += n_sentences
        r["subject_sentences_altered"] += altered
        r["prompts_without_the_spec_subject"] += f"Subject: {spec.get('subject')}" not in image_gen.build_prompt(spec)
        broken = broken_sentences(spec)
        r["broken_sentences"] += len(broken)
        r["subjects_with_broken_sentences"] += bool(broken)
        inv = inventory_conflicts(image_gen, spec)
        r["prompts_with_a_draw_only_list"] += inv["list"]
        r["draw_only_excludes_a_clearly_shows_item"] += inv["excludes_a_clearly_shows_item"]
        r["draw_only_excludes_a_subject_word"] += inv["excludes_a_subject_word"]
        r["spec_counts"] += len(spec.get("counts") or [])
        r["spec_measures"] += len(spec.get("measures") or [])
        r["spec_numbers_not_from_a_structured_field"] += not_structured(spec, [f])
        n = prose_numbers(image_gen, spec)
        r["prose_numbers"] += n
        r["prompts_with_prose_numbers"] += bool(n)
        r["prompts_with_no_number_instruction"] += bool(no_count) and no_count in image_gen.build_prompt(spec)
    return r


def part2(image_gen, fq) -> dict:
    r = {"review4_probes": 0, "review4_specs": 0, "review4_prompts_with_prose_numbers": 0, "review4_prose_numbers": 0,
         "review4_spec_counts": 0, "by_class": {}}
    for cls, sentences in REVIEW4.items():
        row = {"probes": len(sentences), "specs": 0, "prompts_with_prose_numbers": 0}
        for s in sentences:
            spec, _ = _spec(image_gen, _fact(fq, s))
            r["review4_probes"] += 1
            if spec is None:
                continue
            n = prose_numbers(image_gen, spec)
            row["specs"] += 1
            row["prompts_with_prose_numbers"] += bool(n)
            r["review4_specs"] += 1
            r["review4_prompts_with_prose_numbers"] += bool(n)
            r["review4_prose_numbers"] += n
            r["review4_spec_counts"] += len(spec.get("counts") or [])
        r["by_class"][cls] = row
    if fq is None:
        r["structured"] = None
        return r
    ship, _ = _spec(image_gen, _fact(fq, SHIP, SHIP_COUNTS, SHIP_MEASURES))
    exact_ok = bool(ship) and {c["element"]: c["count"] for c in ship["counts"]} == {"mast": 3, "sail": 2} \
        and ship["measures"] == ["34 cm"] and prose_numbers(image_gen, ship) == 0
    others = ["approximate", "minimum", "maximum", "range", "per_unit", "comparison_set"]
    became_exact = non_exact_specs = 0
    for q in others:
        spec, _ = _spec(image_gen, _fact(fq, GEARS,
                                          [{"element": "gear", "count": 30, "qualifier": q, "source": "30 gears"}]))
        became_exact += bool(spec and spec.get("counts"))
        non_exact_specs += bool(spec)
    kailasa = _fact(fq, KAILASA + CLIFF,
                    [{"element": "monument", "count": 34, "qualifier": "exact", "source": "34 monuments"}])
    spec, _ = _spec(image_gen, kailasa)
    r["structured"] = {"exact_ship_count_drawn_exactly": exact_ok, "non_exact_qualifiers": len(others),
                       "non_exact_became_exact": became_exact, "non_exact_specs": non_exact_specs,
                       "kailasa_spec": spec is not None,
                       "kailasa_exact_offer_kept": "quantities" in kailasa,
                       "kailasa_spec_counts": len((spec or {}).get("counts") or [])}
    return r


def part3(image_gen, fq) -> dict:
    import tempfile
    from unittest.mock import patch

    from PIL import Image

    from src import free_llm, image_quarantine
    spec, _ = _spec(image_gen, _fact(fq, SHIP, SHIP_COUNTS, SHIP_MEASURES))
    base = {"descriptors": [{"descriptor": d, "shown": True} for d in spec["required"]], "has_text": False,
            "anachronism": False, "identifiable_face": False, "subject_in_safe_zone": True,
            "could_be_mistaken_for_photograph": False,
            "elements": [{"element": "mast", "shown": True, "count_seen": 3},
                         {"element": "sail", "shown": True, "count_seen": 2}],
            "depicted_elements": ["wooden ship", "masts", "sails"],
            "measure_checks": [{"measure": m, "consistent": None, "reason": "No scale is visible."}
                               for m in spec.get("physical_measures") or spec.get("measures") or []]}

    def verdict(value, s=spec):
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / "c.png"
            Image.new("RGB", (64, 64)).save(image)
            with patch.object(image_quarantine, "_vision_ready", lambda: True), \
                    patch.object(free_llm, "structured", lambda system, prompt, schema, **k: schema(**{
                        k2: v for k2, v in value.items() if k2 in schema.model_fields})):
                return image_quarantine.vision_rubric(image, s)[0]

    two_masts = json.loads(json.dumps(base))
    two_masts["elements"][0]["count_seen"] = 2
    r = {"faithful_control_accepted": verdict(base), "count_mismatch_accepted": verdict(two_masts),
         "cannon_absent_from_facts_accepted": verdict(dict(base, depicted_elements=base["depicted_elements"]
                                                           + ["cannon"])),
         "smuggled_items": len(SMUGGLED),
         "smuggled_items_accepted": sum(verdict(dict(base, depicted_elements=base["depicted_elements"] + [item]))
                                        for item in SMUGGLED)}
    # the countless ship (no structured record): what does the count check hold the picture to?
    countless, _ = _spec(image_gen, _fact(None, SHIP))
    r["countless_spec_counts_to_verify"] = len(countless.get("counts") or [])
    return r


def part4(image_gen) -> dict:
    """The generator-side options for the closed inventory, compared on the real specs' prompts (no generation).
    Option A: a "Draw only" list that is the union of the forms, the materials and every word of the subject that the
    vision check allows (the subject's own nouns, with no part-of-speech tagger to pick them out). Option B: no list
    in the generator prompt; the closed inventory stays in the vision check. Counted: option A's list length, and its
    words that name no object (a verb or participle form, an adverb, or an abstract noun)."""
    try:
        from src import illustration_elements as ie
    except ImportError:         # the base: no closed inventory yet
        ie = None
    abstract = getattr(ie, "_ABSTRACT", set())
    lengths, nonobject, with_nonobject = [], 0, 0
    for spec in SPECS:
        union = subject_words(spec) | {canon(w) for x in (spec.get("forms") or []) + (spec.get("materials") or [])
                                       for w in x.split()}
        bad = {w for w in union if w.endswith(("ed", "ing", "ly")) or w in abstract}
        lengths.append(len(union))
        nonobject += len(bad)
        with_nonobject += bool(bad)
    lengths.sort()
    return {"specs": len(SPECS), "option_a_list_words_median": lengths[len(lengths) // 2] if lengths else 0,
            "option_a_list_words_max": max(lengths, default=0), "option_a_nonobject_words": nonobject,
            "option_a_lists_with_a_nonobject_word": with_nonobject}


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    image_gen, fq = _setup(root)
    p1, p2, p3 = part1(root, image_gen), part2(image_gen, fq), part3(image_gen, fq)
    p4 = part4(image_gen)
    s = p2.get("structured") or {}
    acceptance = (p1["spec_numbers_not_from_a_structured_field"] == 0 and p1["prose_numbers"] == 0
                  and p2["review4_prose_numbers"] == 0 and p2["review4_spec_counts"] == 0
                  and s.get("exact_ship_count_drawn_exactly") is True and s.get("non_exact_became_exact") == 0
                  and s.get("kailasa_spec_counts") == 0 and p3["count_mismatch_accepted"] is False
                  and p3["cannon_absent_from_facts_accepted"] is False and p3["faithful_control_accepted"] is True
                  and p3["smuggled_items_accepted"] == 0 and p3["countless_spec_counts_to_verify"] == 0
                  and p1["prompts_with_no_number_instruction"] == p1["specs"]
                  and p1["broken_sentences"] == 0 and p1["draw_only_excludes_a_clearly_shows_item"] == 0
                  and p1["draw_only_excludes_a_subject_word"] == 0 and p1["subject_sentences_altered"] == 0
                  and p1["prompts_without_the_spec_subject"] == 0)
    print(json.dumps({"real_fact_sheets": p1, "probes": p2, "vision": p3, "inventory_options": p4,
                      "acceptance_met": acceptance}, indent=2))


if __name__ == "__main__":
    main()
