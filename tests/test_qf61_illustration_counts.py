"""QF-61: an illustration's numbers come only from a fact's structured, checked and signed quantities (operator
decision 2026-11-12, structured counts only), and the vision check rejects a wrong count or anything drawn that the
facts do not name.

No count or measure is read out of prose for the generator: every number of the fact text is removed before it goes
into the prompt, and a fact without a structured count gives the "draw no specific number" instruction and nothing
for the count check to verify. Only an "exact" structured count is drawn; an approximate, ranged, per-unit or
comparison-set one never becomes exact. An unclear quantity in the prose still refuses the picture."""

import json
import re
from pathlib import Path

import pytest
from PIL import Image

from src import config, free_llm, image_gen, image_quarantine

ROOT = Path(__file__).resolve().parents[1]
SHIP = ("The wooden ship had three tall masts and two sails, and was 34 cm long. "
        "The hull of the wooden ship was built of oak planks.")
SHIP_COUNTS = [{"element": "mast", "count": 3, "qualifier": "exact", "source": "three tall masts"},
               {"element": "sail", "count": 2, "qualifier": "exact", "source": "two sails"}]
SHIP_MEASURES = [{"element": "ship", "measure": "34 cm", "qualifier": "exact"}]
KAILASA = "Known as Cave 16, this rock-cut stone temple is the largest temple among the 34 monuments at the site."
_NUMERAL = re.compile(r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
                      r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
                      r"seventy|eighty|ninety|hundred|thousand|million|dozen|dozens|hundreds|thousands)\b",
                      re.IGNORECASE)


# "one of the foremost achievements" is an idiom, not a count (round 6)
_ONE_IDIOM = re.compile(r"\bone\s+(?=of\s+(?:the|its|their|his|her|our|these|those|them|many|several)\b)|\bone\s+another\b",
                        re.IGNORECASE)


_MISSING = "<this tree has no such constant>"


def NO_COUNT() -> str:      # read so an older tree fails by assertion
    return getattr(image_gen, "NO_COUNT", _MISSING)


def NO_OTHER_COUNT() -> str:
    return getattr(image_gen, "NO_OTHER_COUNT", _MISSING)


def _quantities():
    try:
        from src import fact_quantities
    except ImportError:
        fact_quantities = None
    assert fact_quantities is not None, "this tree has no structured quantities (src/fact_quantities.py)"
    return fact_quantities


def fact(statement, counts=(), measures=(), quote=None, fid="F1", **changes):
    """A checked fact as the fact-sheet rules store it, with its quantities record when one is kept."""
    value = {"id": fid, "statement": statement, "kind": "number", "disputed": False, "hedge": "",
             "evidence": [{"url": "https://www.britannica.com/topic/probe", "tier": 1, "quote": quote or statement,
                           "host": "britannica.com", "fetch": "web_search"}]}
    value.update(changes)
    try:
        from src import fact_quantities
    except ImportError:        # a tree without structured quantities: the fact stays countless
        return value
    record, _ = fact_quantities.check(value, list(counts), list(measures))
    if record:
        value["quantities"] = record
    return value


def spec_of(*facts, line=None):
    line = line or facts[0]["statement"]
    return image_gen.spec_from_facts(line, {}, {"facts": list(facts)},
                                     {"sentence_trace": [{"sentence": line, "fact_ids": [f["id"] for f in facts]}]})


def spec_for(statement, counts=(), measures=()):
    return spec_of(fact(statement, counts, measures))


def counts(spec) -> dict:
    return {c["element"]: c["count"] for c in (spec or {}).get("counts") or []}


def prose_numbers(spec) -> list[str]:
    """Every number in the generator prompt that is not a checked structured count or measure: the prompt less the
    fixed template text and less the counts sentence and measures built from the structured fields."""
    text = image_gen.build_prompt(spec)
    text = text.replace(f"Viewpoint: {spec.get('viewpoint') or 'a three-quarter view'}.", " ")   # the code's own
    for name in dir(image_gen):
        value = getattr(image_gen, name)
        if name.isupper() and isinstance(value, str) and len(value) > 20:
            text = text.replace(value, " ")
    for table in (image_gen.ERA_IDIOM, image_gen.ERA_LABEL):
        for value in table.values():
            text = text.replace(value, " ")
    sentence = getattr(image_gen, "counts_sentence", None)
    if spec.get("counts") and sentence:
        text = text.replace(sentence(spec.get("counts")), " ")
    if sentence:           # a tree with structured quantities: its measures are the structured ones
        for m in spec.get("measures") or []:
            text = text.replace(m, " ", 1)
    return _NUMERAL.findall(_ONE_IDIOM.sub(" ", text))


def ship_spec():
    spec, why = spec_for(SHIP, SHIP_COUNTS, SHIP_MEASURES)
    assert spec, why
    return spec


def answer(spec=None, **changes):
    spec = spec or ship_spec()
    value = {"descriptors": [{"descriptor": d, "shown": True} for d in spec["required"]],
             "has_text": False, "anachronism": False, "identifiable_face": False, "subject_in_safe_zone": True,
             "could_be_mistaken_for_photograph": False,
             "elements": [{"element": "mast", "shown": True, "count_seen": 3},
                          {"element": "sail", "shown": True, "count_seen": 2}],
             "depicted_elements": ["wooden ship", "masts", "sails"],
             "measure_checks": [{"measure": "34 cm", "consistent": None, "reason": "No scale is visible."}]}
    value.update(changes)
    return value


def judge(tmp_path, monkeypatch, value, spec=None):
    image = tmp_path / "candidate.png"
    Image.new("RGB", (64, 64)).save(image)
    monkeypatch.setattr(image_quarantine, "_vision_ready", lambda: True)
    monkeypatch.setattr(free_llm, "structured", lambda system, prompt, schema, **kwargs: schema(**value))
    return image_quarantine.vision_rubric(image, spec or ship_spec())


# ------------------------------------------------------------------ review 4: no number from prose, ever
REVIEW4_PROBES = [
    # 1. a label number followed by a verb
    "Stone column 7 bears 12 carved reliefs.", "Cave 16 houses a stone temple.", "Room 12 contains a bronze lamp.",
    "Tomb 5 holds a stone coffin.", "Pillar 43 shows a carved stone vulture.", "Gate 3 opens onto a stone courtyard.",
    "Cave 16 features 3 stone shrines.", "The stone fort had 4 towers before 1 collapsed.",
    # 2. distributive counts
    "Each of the 4 stone towers has 3 windows.", "The 4 stone towers have 3 windows each.",
    "Every stone tower has 3 windows.", "Each stone tower has 3 windows.", "There are 3 windows in each stone tower.",
    "The stone hall has 4 columns on each side.", "The stone hall has 4 columns along each side.",
    "The stone gate has 4 columns on either side of the door.", "The wooden ship had 20 oars on each side.",
    "The wooden ship had 20 oars a side.",
    # 3. one element counted in separate groups
    "The stone fort had 3 round towers and 3 square towers.",
    "The stone fort had 3 round towers; its gatehouse had 3 towers.",
    "The stone temple had 2 bronze doors and 2 stone doors.", "The stone fort had 4 towers in 1500 and 2 towers in 1800.",
    # 4. membership and comparison sets, the whole of a fraction or a percentage
    "The bronze temple was one of the three largest temples.", "The bronze bell is the largest of the 5 bells.",
    "The stone temple is one of 7 temples.", "The stone temple stands among 7 temples.",
    "The stone temple is the oldest of 7 temples.", "Two-thirds of the 90 stone columns survive.",
    "About 40% of the 90 stone columns survive.",
    KAILASA,
    # 5. approximations, hedges and plans
    "The bronze mechanism had ~30 gears.", "The bronze mechanism had ≈30 gears.", "The stone obelisk weighs ~300 tonnes.",
    "The bronze mechanism had 30 gears, or more.", "The bronze mechanism had 30 gears at most.",
    "The bronze mechanism had 30 gears at least.", "The bronze mechanism had 30 gears, give or take.",
    "Scholars estimate the bronze mechanism had 30 gears.", "The bronze mechanism is believed to have had 30 gears.",
    "The bronze mechanism reportedly had 30 gears.", "Scans suggest the bronze mechanism originally had 35 gears.",
    "The stone statue was designed to weigh 1,000 tonnes.", "The stone statue was to weigh 1,000 tonnes.",
    "The stone tower was meant to be 42 metres tall.",
    # plain counts and measures in prose: no longer read either
    "The marble temple had two statues.", "The stone stair had 1,000 steps.", "The stone wall had 12 niches.",
    "The bronze mechanism had thirty-five gears.", "The marble dome measures approximately 38 meters (125 feet) across.",
    "Derinkuyu was linked to the stone city of Kaymakli across 8 to 9 kilometres of connecting tunnels.",
    "The stone tower is forty metres tall.", SHIP,
]


@pytest.mark.parametrize("statement", REVIEW4_PROBES)
def test_no_number_from_prose_reaches_the_generator_prompt(statement):
    spec, why = spec_for(statement)
    if spec is None:
        assert why
        return
    assert not spec.get("counts") and not spec.get("measures") and not spec.get("physical_measures")
    assert prose_numbers(spec) == [], image_gen.build_prompt(spec)


def test_the_kailasa_comparison_set_gives_no_count_on_the_real_sheet():
    sheet = json.loads((ROOT / "data" / "fact_sheets" / "kailasa_temple_monolith.json").read_text(encoding="utf-8"))
    by_id = {f["id"]: f for f in sheet["facts"]}
    assert "34 monuments" in by_id["kailasa-cave16-scale"]["statement"]
    lines = [by_id[i]["statement"] for i in ("kailasa-cave16-scale", "kailasa-prototype-model",
                                            "kailasa-architectural-hybrid") if i in by_id]
    assert len(lines) == 3
    specs = [spec for spec, _ in (image_gen.spec_from_facts(line, {}, sheet) for line in lines) if spec]
    assert len(specs) >= 2          # the lines review 4 saw given {monument: 34}
    for spec in specs:
        assert not spec.get("counts") and "monument" not in counts(spec)
        assert prose_numbers(spec) == [] and "34" not in image_gen.build_prompt(spec)


def test_a_kailasa_comparison_set_offered_as_exact_is_not_kept():
    fact_quantities = _quantities()
    statement = KAILASA
    record, dropped = fact_quantities.check(
        fact(statement), [{"element": "monument", "count": 34, "qualifier": "exact", "source": "34 monuments"}], [])
    assert record is None and dropped
    spec, _ = spec_for(statement + " The rock-cut temple was carved from the basalt cliff.",
                       [{"element": "monument", "count": 34, "qualifier": "exact", "source": "34 monuments"}])
    assert spec and not counts(spec)


# ------------------------------------------------------------------ the structured count is the only number
def test_a_structured_exact_count_yields_exactly_that_number():
    spec = ship_spec()
    assert counts(spec) == {"mast": 3, "sail": 2}
    assert spec.get("measures") == ["34 cm"] and spec.get("physical_measures") == ["34 cm"]
    prompt = image_gen.build_prompt(spec)
    assert "Exact counts, checked against the sources: 3 mast; 2 sail. Draw exactly these numbers." in prompt
    assert "34 cm" in prompt and "human figure" not in prompt
    assert prose_numbers(spec) == []
    assert "three" not in spec["subject"] and "34" not in spec["subject"]
    assert NO_OTHER_COUNT() in prompt and NO_COUNT() not in prompt


@pytest.mark.parametrize("qualifier", ["approximate", "minimum", "maximum", "range", "per_unit", "comparison_set"])
def test_a_structured_count_that_is_not_exact_never_becomes_exact(qualifier):
    statement = "The bronze mechanism had 30 gears. The bronze mechanism was housed in a wooden case."
    spec, why = spec_for(statement, [{"element": "gear", "count": 30, "qualifier": qualifier, "source": "30 gears"}])
    assert spec, why
    assert not counts(spec) and "30" not in image_gen.build_prompt(spec)
    assert NO_COUNT() in image_gen.build_prompt(spec)


@pytest.mark.parametrize("statement,entry", [
    ("Nearly 30 bronze gears survive.", {"element": "gear", "count": 30, "source": "30 bronze gears"}),
    ("The bronze mechanism reportedly had 30 gears.", {"element": "gear", "count": 30, "source": "30 gears"}),
    ("Each of the 4 stone towers has 3 windows.", {"element": "window", "count": 3, "source": "3 windows"}),
    ("Each of the 4 stone towers has 3 windows.", {"element": "window", "count": 12, "source": "3 windows"}),
    ("The wooden ship had 20 oars a side.", {"element": "oar", "count": 20, "source": "20 oars"}),
    ("The stone fort had 3 round towers and 3 square towers.", {"element": "tower", "count": 3,
                                                               "source": "3 round towers"}),
    ("The stone fort had 3 round towers and 3 square towers.", {"element": "tower", "count": 6,
                                                               "source": "3 round towers"}),
    ("Stone column 7 bears 12 carved reliefs.", {"element": "bear", "count": 7, "source": "7 bears"}),
    ("The stone fort had 4 towers before 1 collapsed.", {"element": "collapsed", "count": 1, "source": "1 collapsed"}),
    ("Cave 16 features 3 stone shrines.", {"element": "feature", "count": 16, "source": "16 features"}),
    ("The bronze temple was one of the three largest temples.", {"element": "temple", "count": 3,
                                                                 "source": "three largest temples"}),
    ("Two-thirds of the 90 stone columns survive.", {"element": "column", "count": 90, "source": "90 stone columns"}),
    ("The bronze mechanism had ~30 gears.", {"element": "gear", "count": 30, "source": "30 gears"}),
    ("The bronze mechanism had 30 gears, or more.", {"element": "gear", "count": 30, "source": "30 gears"}),
    ("The bronze mechanism had 30 gears.", {"element": "gear", "count": 31, "source": "30 gears"}),
    ("The bronze mechanism had 30 gears.", {"element": "cannon", "count": 30, "source": "30 gears"}),
    ("The bronze mechanism had 30 gears.", {"element": "gear", "count": 30, "source": "30 bronze gears"}),
])
def test_an_exact_count_the_statement_does_not_state_as_exact_is_not_kept(statement, entry):
    fact_quantities = _quantities()
    record, dropped = fact_quantities.check(fact(statement), [dict(entry, qualifier="exact")], [])
    assert record is None and len(dropped) == 1


def test_an_exact_count_needs_an_undisputed_strongly_sourced_fact_and_evidence_with_its_number():
    fact_quantities = _quantities()
    entry = [{"element": "gear", "count": 30, "qualifier": "exact", "source": "30 gears"}]
    statement = "The bronze mechanism had 30 gears."
    assert fact_quantities.check(fact(statement), entry, [])[0]
    for changed in (fact(statement, disputed=True), fact(statement, hedge="by one estimate, 30 gears"),
                    fact(statement, quote="The bronze mechanism had many gears.")):
        assert fact_quantities.check(changed, entry, [])[0] is None
    weak = fact(statement)
    weak["evidence"] = [dict(weak["evidence"][0], url="https://example-travel-blog.com/x", tier=3,
                             host="example-travel-blog.com")]
    assert fact_quantities.check(weak, entry, [])[0] is None


def test_an_edited_unsigned_or_rebound_record_gives_no_number(monkeypatch):
    base = fact(SHIP, SHIP_COUNTS, SHIP_MEASURES)
    assert "quantities" in base, "the fact-sheet rules keep no structured record"
    assert counts(spec_of(base)[0]) == {"mast": 3, "sail": 2}
    edited = json.loads(json.dumps(base))
    edited["quantities"]["counts"][0]["count"] = 4
    rebound = json.loads(json.dumps(base))
    rebound["statement"] = SHIP.replace("three", "four")
    future = json.loads(json.dumps(base))
    future["quantities"]["version"] = 99
    for broken in (edited, rebound, future):
        spec, why = spec_of(broken)
        assert spec, why
        assert not counts(spec) and not spec.get("measures") and prose_numbers(spec) == []
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "")
    spec, _ = spec_of(base)
    assert not counts(spec) and NO_COUNT() in image_gen.build_prompt(spec)


def test_two_facts_with_different_checked_counts_refuse_the_picture():
    a = fact("The bronze mechanism had 30 gears.", [{"element": "gear", "count": 30, "qualifier": "exact",
                                                     "source": "30 gears"}])
    b = fact("The bronze mechanism had 35 gears in all.", [{"element": "gear", "count": 35, "qualifier": "exact",
                                                           "source": "35 gears"}], fid="F2")
    assert a.get("quantities") and b.get("quantities")
    spec, why = spec_of(a, b, line="The bronze mechanism had 30 gears.")
    assert spec is None and "quantity" in why


def test_the_fact_sheet_rules_write_and_sign_the_structured_counts():
    from src import fact_sheet
    fact_quantities = _quantities()
    from src.models import DraftFact
    quotes = [{"url": "https://www.britannica.com/topic/ship", "quote": SHIP}]
    draft = DraftFact(id="F1", statement=SHIP, kind="number", quote_ids=[1], disputed=False, hedge="",
                      counts=[dict(SHIP_COUNTS[0]), {"element": "sail", "count": 2, "qualifier": "approximate",
                                                     "source": "two sails"},
                              {"element": "mast", "count": 3, "qualifier": "exact", "source": "34 cm"}],
                      measures=SHIP_MEASURES)
    facts, dropped = fact_sheet._apply_rules([draft], quotes)
    assert not dropped
    record = facts[0].get("quantities") or {}
    assert record.get("signature") and record.get("version") == fact_quantities.VERSION
    assert [c["element"] for c in record.get("counts") or []] == ["mast", "sail"]
    assert fact_quantities.checked(facts[0])["counts"] == [SHIP_COUNTS[0]]
    plain, _ = fact_sheet._apply_rules([DraftFact(id="F2", statement=SHIP, kind="number", quote_ids=[1],
                                                  disputed=False, hedge="")], quotes)
    assert "quantities" not in plain[0]          # a fact without the field is valid and countless
    assert getattr(fact_sheet, "FACT_SCHEMA_VERSION", None) == 2


# ------------------------------------------------------------------ no structured count: no number to draw or check
@pytest.mark.parametrize("statement", ["The marble temple had two statues.", "The bronze mechanism had 30 gears.",
                                       SHIP])
def test_a_countless_fact_tells_the_generator_to_draw_no_specific_number(statement):
    # round 7: a sentence that states a number is left out whole, so each fact carries a number-free sentence
    statement += " The stone hall stands beside the river."
    spec, why = spec_for(statement)
    assert spec, why
    prompt = image_gen.build_prompt(spec)
    assert NO_COUNT() in prompt
    assert not spec.get("counts") and "Exact counts" not in prompt and prose_numbers(spec) == []


def test_the_count_check_has_nothing_to_verify_without_a_structured_count(tmp_path, monkeypatch):
    spec, why = spec_for(SHIP)
    assert spec, why
    value = answer(spec, elements=[], measure_checks=[])
    assert judge(tmp_path, monkeypatch, value, spec) == (True, "pass")
    two_masts = answer(spec, elements=[{"element": "mast", "shown": True, "count_seen": 2}], measure_checks=[])
    ok, why = judge(tmp_path, monkeypatch, two_masts, spec)
    assert ok and "count" not in why


# ------------------------------------------------------------------ acceptance: the vision check
def test_two_masts_cannot_pass_a_three_mast_spec(tmp_path, monkeypatch):
    value = answer()
    value["elements"][0]["count_seen"] = 2
    ok, why = judge(tmp_path, monkeypatch, value)
    assert not ok and "count mismatch" in why and "mast" in why


def test_a_cannon_absent_from_the_facts_is_rejected(tmp_path, monkeypatch):
    ok, why = judge(tmp_path, monkeypatch, answer(depicted_elements=["wooden ship", "masts", "sails", "cannon"]))
    assert not ok and "cannon" in why


def test_the_faithful_inventory_passes(tmp_path, monkeypatch):
    assert judge(tmp_path, monkeypatch, answer()) == (True, "pass")


@pytest.mark.parametrize("item", ["cannon mount", "flag part", "human figure detail", "lighthouse section",
                                  "wooden stand", "brass handle", "carved pedestal", "sailor figure", "anchor",
                                  "dark ink background", "frame", "detail"])
def test_an_item_the_facts_do_not_name_is_rejected_whatever_its_last_word(tmp_path, monkeypatch, item):
    ok, why = judge(tmp_path, monkeypatch, answer(depicted_elements=["wooden ship", "masts", "sails", item]))
    assert not ok and "absent from the facts" in why


@pytest.mark.parametrize("item", ["background", "hatching", "linework", "tall masts", "wooden ship"])
def test_presentation_words_and_the_facts_own_words_are_allowed(tmp_path, monkeypatch, item):
    assert judge(tmp_path, monkeypatch, answer(depicted_elements=["wooden ship", "masts", "sails", item]))[0]


def test_a_shown_element_record_the_facts_do_not_name_is_rejected(tmp_path, monkeypatch):
    value = answer()
    value["elements"].append({"element": "cannon", "shown": True, "count_seen": 1})
    assert not judge(tmp_path, monkeypatch, value)[0]


@pytest.mark.parametrize("field,value", [("elements", []), ("depicted_elements", []), ("depicted_elements", None),
                                         ("measure_checks", [])])
def test_an_incomplete_inventory_or_measure_answer_is_rejected(tmp_path, monkeypatch, field, value):
    assert not judge(tmp_path, monkeypatch, answer(**{field: value}))[0]


@pytest.mark.parametrize("count", [True, 3.0, -3, None])
def test_a_count_must_be_an_explicit_non_negative_integer(tmp_path, monkeypatch, count):
    value = answer()
    value["elements"][0]["count_seen"] = count
    assert not judge(tmp_path, monkeypatch, value)[0]


def test_the_inventory_cannot_contradict_a_passing_count(tmp_path, monkeypatch):
    assert not judge(tmp_path, monkeypatch, answer(depicted_elements=["wooden ship", "sails"]))[0]


def test_an_unscaled_measure_is_unknown_and_a_contradiction_rejects(tmp_path, monkeypatch):
    assert judge(tmp_path, monkeypatch, answer())[0]
    contradicted = answer(measure_checks=[{"measure": "34 cm", "consistent": False, "reason": "Far too large."}])
    assert not judge(tmp_path, monkeypatch, contradicted)[0]
    no_reason = answer(measure_checks=[{"measure": "34 cm", "consistent": None, "reason": chr(0x200B)}])
    assert not judge(tmp_path, monkeypatch, no_reason)[0]


# ------------------------------------------------------------------ unclear prose quantities still refuse
@pytest.mark.parametrize("statement", [
    "The bronze box held 82 fragments, 7 of which were large.", "Of the 9 bronze plates, 4 of them were engraved.",
    "Nearly 30 bronze gears survive.", "An estimated 30 bronze gears survive.", "Up to 30 bronze gears survive.",
    "The ship had 3-4 masts and bronze fittings.", "The ship had three or four masts and bronze fittings.",
    "Miners carved dozens of statues into the rock.", "The stone hall had several hundred columns.",
    "The bronze case held 12 or so gears.", "The bronze case did not hold 30 gears.",
    "The stone hall received 80 visitors per day.",
])
def test_an_unclear_quantity_in_the_prose_still_refuses_the_picture(statement):
    spec, why = spec_for(statement)
    assert spec is None and "quantity" in why


# ------------------------------------------------------------------ the real fact sheets
def test_no_number_from_the_real_fact_sheets_reaches_a_generator_prompt():
    sheets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "data" / "fact_sheets").glob("*.json"))]
    facts = [f for s in sheets for f in s.get("facts") or [] if isinstance(f, dict)]
    assert len(facts) >= 200
    assert not any("quantities" in f for f in facts)      # no structured count was invented for a signed sheet
    specs = 0
    for f in facts:
        spec, _ = image_gen.spec_from_facts(f["statement"], {}, {"facts": [f]})
        if spec:
            specs += 1
            assert not spec.get("counts") and not spec.get("measures"), f["id"]
            assert prose_numbers(spec) == [], f["id"]
            assert NO_COUNT() in image_gen.build_prompt(spec)
    assert specs >= 50


# ------------------------------------------------------------------ round 6: no contradiction; round 7: no edits
# the test's own grammar oracle: a broken sentence (round 6's phrase removal produced them; round 7 edits nothing)
_PREP = r"(?:of|in|on|at|by|for|from|to|since|until|during|between|among|with|including|than|as)"
BROKEN = [re.compile(p) for p in (
    r"\b" + _PREP + r"\s*(?:[,.;:!?]|$)",
    (r"\b(?:of|in|on|at|by|to|since|until|during|with)\s+(?:of|in|on|at|by|to|since|until|during|with|below|above|"
     r"including|for)\b"),
    r"\b(?:[Tt]he|[Aa]n?|their|its)\s*(?:[,.;:!?]|$)",
    r"\b(?:[Tt]he|[Aa]n?)\s+(?:of|in|on|at|by|to|from|since|during|with|the|a|an|and|or)\b",
    r"\b[Aa]\s+[aeioAEIO]\w", r"\b[Aa]n\s+[bcdfgjklmnpqrstvwxyz]\w", r"\b(?:and|or|but)\s*[.;!?]",
    r"\s{2,}", r"\s[,.;:!?]", r"[,;:]\s*[,.;:]", r"^[^A-Z]", r"[.!?]\s+[a-z]",
    r"\b(?:from|of|since|the|to|and)\s+ago\b",
    r"\b(?:weighs|weighing|measures|measuring|spanning|plunging|reaching|totals)\s*[,.;:!?]",
    r"\b(?:on|in|as|at|of)\s+(?:Cave|Pillar|Chapter|Room|Tomb|Gate|Gallery|Level)\s*(?:[,.;:]|\s(?:in|of|on|at|the)\b)",
    r"\b(?:may|might|can|could|will|would|should|must)\s*[,.;:!?]",
    r"\bdat(?:e|es|ed|ing)(?:\s+back)?\s*[,.;:!?]")]


def broken(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+", str(text or "")) if s and any(p.search(s) for p in BROKEN)]


def _elements():
    try:
        from src import illustration_elements
    except ImportError:
        illustration_elements = None
    assert illustration_elements is not None, "this tree has no src/illustration_elements.py"
    return illustration_elements


def _function(name):
    found = getattr(_elements(), name, None)
    assert found is not None, f"this tree has no illustration_elements.{name} (round 7)"
    return found


def number_free(text):
    return _function("number_free_sentences")(text)


def has_number(text):
    return _function("has_number")(text)


def real_facts():
    return [(p.stem, f) for p in sorted((ROOT / "data" / "fact_sheets").glob("*.json"))
            for f in json.loads(p.read_text(encoding="utf-8")).get("facts") or [] if isinstance(f, dict)]


def real_specs():
    out = []
    for _, f in real_facts():
        statement = " ".join(str(f.get("statement") or "").split())
        spec, _ = image_gen.spec_from_facts(statement, {}, {"facts": [dict(f, statement=statement)]})
        if spec:
            out.append((f, spec))
    assert len(out) >= 54        # round 7: a fact whose only sentence states a number gives no picture (was 85)
    return out


# ------------------------------------------------------------------ round 7: prose is never edited
# the test's own oracle: a statement's sentences (after ".", "!" or "?" and white space), never the code's splitter
def source_sentences(*statements) -> list[str]:
    return [p for s in statements for p in re.split(r"(?<=[.!?])\s+", " ".join(str(s or "").split())) if p]


def tiles(subject: str, *statements) -> bool:
    """Whether `subject` is whole source sentences, byte for byte, joined by single spaces (one sentence the code kept
    together, such as "Derek J. Price", is two of this oracle's pieces and still tiles)."""
    pieces = set(source_sentences(*statements))
    reach = {0}
    for pos in range(len(subject) + 1):
        if pos in reach:
            for piece in pieces:
                end = pos + len(piece)
                if subject.startswith(piece, pos) and (end == len(subject) or subject[end] == " "):
                    reach.add(end + 1 if end < len(subject) else end)
    return bool(subject) and len(subject) in reach


def test_the_phrase_removal_path_is_gone():
    elements = _elements()
    assert not hasattr(elements, "without_numbers") and not hasattr(elements, "sentence_problems")
    _function("number_free_sentences")


REVIEW6_REAL = [("richat_structure_sahara_eye", "fact-8"), ("trepanation_miracle", "fact-3"),
                ("wieliczka_salt_mine", "fact-5"), ("antikythera_mechanism", "technological-gap"),
                ("antikythera_mechanism", "price-karakalos-imaging"), ("kailasa_temple_monolith", "kailasa-ellora-dating"),
                ("hal_saflieni_hypogeum", "dates_of_use")]


@pytest.mark.parametrize("sheet, fid", REVIEW6_REAL)
def test_a_real_sentence_with_a_number_is_left_out_whole_never_rewritten(sheet, fid):
    """Review 6: phrase removal reversed or broke these real facts (Richat run sentence 3: "they cooled between 94 and
    104 million years ago," lost its verb; "five to ten percent of all cranial remains" became "all cranial remains";
    "until 1996, when extraction ceased" and "did not appear again for around 1,500 years" lost their bounds)."""
    facts = {(s, f["id"]): f for s, f in real_facts()}
    f = facts[(sheet, fid)]
    assert number_free(f["statement"]) == []
    spec, why = image_gen.spec_from_facts(f["statement"], {}, {"facts": [f]})
    assert spec is None and why
    if sheet == "richat_structure_sahara_eye":       # the dev Richat run's sentence 3 is this fact
        assert "they cooled between 94 and 104 million years ago" in f["statement"]


@pytest.mark.parametrize("statement", [
    "The king died in 1350, leaving the stone tower unfinished.",
    "The stone tower collapsed in 1902, crushing the loggia.",
    "The timber roof burned in AD 64, and the stone hall was rebuilt.",
    ("Dating of the carbonatite rocks shows they cooled between 94 and 104 million years ago, closely matching the "
     "hydrothermal event that produced the central breccia."),
    "Trephined skulls account for five to ten percent of all cranial remains recovered from the stone tombs.",
    "The salt mine operated from the thirteenth century until 1996, when extraction ceased.",
    "Machines of similar complexity did not appear again for around 1,500 years, until the 14th century in Europe.",
    "The stone temple was excavated into the cliff wall between A.D. 600 and 1000.",
])
def test_review_6_examples_give_no_sentence_or_the_original(statement):
    keep = "The stone hall stands beside the river."
    assert number_free(statement) == []
    assert number_free(f"{statement} {keep}") == [keep]
    assert number_free(f"{keep} {statement}") == [keep]
    spec, why = spec_for(statement)
    assert spec is None and why
    for text in (f"{keep} {statement}", f"{statement} {keep}"):
        spec, why = spec_for(text)
        if spec:                    # the other gates may still refuse (human remains, an unclear quantity)
            assert spec["subject"] == keep
            assert f"Subject: {keep}" in image_gen.build_prompt(spec)


def test_a_mixed_fact_like_the_review_6_examples_keeps_its_number_free_sentence():
    keep = "The stone hall stands beside the river."
    for statement in ("The quarry closed in 1350, leaving the stone tower unfinished.",
                      "The stone tower collapsed in 1902, crushing the loggia.",
                      "The timber roof burned in AD 64, and the stone hall was rebuilt."):
        spec, why = spec_for(f"{statement} {keep}")
        assert spec, why
        assert spec["subject"] == keep and statement not in image_gen.build_prompt(spec)


def test_an_era_with_stops_at_a_sentence_end_keeps_the_next_sentence_with_it():
    keep = "The stone hall stands beside the river."
    # "B.C." may end a sentence or stand inside one: when unsure, the two stay one sentence and are left out together
    assert number_free(f"The stone cemetery was used from around 4000 B.C. to 2500 B.C. {keep}") == []
    assert number_free(f"{keep} The stone cemetery was used from around 4000 B.C. to 2500 B.C.") == [keep]


@pytest.mark.parametrize("sentence", [
    # review 6, blocking 2, and the lander's round-7 list: count words and forms
    "XII bronze columns stand in the hall.", "A pair of stone lions guard the gate.", "Twin stone towers flank the gate.",
    "The stone roof carries a trio of domes.", "A couple of stone columns remain.", "Triple stone arches span the river.",
    "The double doors of the stone hall open inward.", "A half-dozen stone pillars stand in the hall.",
    "A dozen stone pillars stand in the hall.", "Fourscore stone pillars stand in the hall.",
    "A score of stone pillars stand in the hall.", "Scores of stone pillars stand in the hall.",
    "The stone slab is ½ metre wide.", "The stone hall covers a large area of m².", "Cave XVI holds a stone shrine.",
    "Only one of the stone towers survives.", "Just one of the bronze gears remains.",
    "One of the stone towers collapsed, leaving the other standing.", "One proposed origin is a stone quarry.",
    "The bronze temple was one of the three largest temples.", "The stone hall has a twofold roof.",
    # numerals, number words, ordinals, fractions and symbols
    "The stone hall had 12 niches.", "The stone hall had twelve niches.", "Thousands of stone mounds line the dikes.",
    "Dozens of stone statues line the hall.", "A spacious stone room lies on the second floor.",
    "The first stone temple was carved by hand.", "The stone wall leans 3° to the west.",
    "About 40% of the stone columns survive.", "Half of the stone columns survive.",
    "A quarter of the stone columns survive.", "Both stone towers survive.", "A single stone block forms the shrine.",
    "The stone vault was added a century later.", "Edward I built the stone castle.", "Henry V rebuilt the stone hall.",
    "The stone hall was rebuilt in the BC era.", "The stone tower doubled as a lighthouse.",
    "The stone stair climbs twice around the tower.", "The nineteenth-century stone hall was restored.",
])
def test_a_sentence_with_any_number_is_left_out_whole(sentence):
    assert has_number(sentence)
    assert number_free(sentence) == []
    keep = "The stone hall stands beside the river."
    assert number_free(f"{keep} {sentence} {keep}") == [keep, keep]


@pytest.mark.parametrize("sentence", [
    "The stone temple is one of the largest temples of the region.",
    "Carved into the edifice is a massive relief, considered one of the foremost achievements of rock sculpture.",
    "The stone temple was one of its oldest shrines.", "It is one of humanity's earliest stone monuments.",
    "No one knows who carved the stone lions.", "The stone blocks fit one another without mortar.",
    "The stone tower is the one that fell.", "From the terrace one can see the stone arches.",
    "The site was studied by Derek J. de Solla Price in a stone vault.",
    "The stone temple stands among the monuments along the basalt cliff at Ellora.",
    "The western forest hides a stone tower.", "Miners carved chapels and statues out of the rock salt.",
])
def test_idioms_and_number_free_sentences_pass_verbatim(sentence):
    assert not has_number(sentence)
    assert number_free(sentence) == [sentence]
    spec, _ = spec_for(sentence)
    if spec:
        assert spec["subject"] == sentence


def test_sentences_are_cut_without_splitting_initials_or_eras_and_kept_verbatim():
    cut = _function("sentences")
    text = ("Derek J. de Solla Price studied the bronze gears. The temple was cut c. 750 A.D. by the kings. "
            "St. Peter's dome is stone! Is it bronze? It is.")
    parts = cut(text)
    assert parts == ["Derek J. de Solla Price studied the bronze gears.", "The temple was cut c. 750 A.D. by the kings.",
                     "St. Peter's dome is stone!", "Is it bronze?", "It is."]
    assert all(p in text for p in parts)


def test_every_prompt_sentence_is_a_source_sentence_byte_for_byte():
    checked = 0
    for f, spec in real_specs():
        statement = " ".join(str(f.get("statement") or "").split())
        assert tiles(spec["subject"], statement), (f["id"], spec["subject"])
        assert not has_number(spec["subject"]) and prose_numbers(spec) == []
        assert broken(spec["subject"]) == [], (f["id"], spec["subject"])
        assert f"Subject: {spec['subject']}" in image_gen.build_prompt(spec)
        checked += 1
    assert checked >= 54


def test_a_mixed_fact_keeps_only_its_number_free_sentences_unaltered():
    statement = ("The stone hall had 12 niches. The stone hall was roofed with timber. Only one of the stone towers "
                 "survives. It is one of the largest stone halls of the region.")
    spec, why = spec_for(statement)
    assert spec, why
    assert spec["subject"] == ("The stone hall was roofed with timber. It is one of the largest stone halls of the "
                               "region.")
    assert tiles(spec["subject"], statement)
    two = [fact("The stone hall had 12 niches. The stone hall was roofed with timber."),
           fact("The stone gate was built of cut stone blocks.", fid="F2")]
    spec, why = spec_of(*two, line="The stone hall was roofed with timber.")
    assert spec, why
    assert spec["subject"] == "The stone hall was roofed with timber. The stone gate was built of cut stone blocks."
    assert tiles(spec["subject"], *(f["statement"] for f in two))


def test_the_subject_is_never_a_clipped_sentence():
    first = "The stone hall " + "with carved stone arches " * 9 + "stands by the river."
    second = "The stone gate was built of cut stone blocks."
    assert 180 < len(first) <= 300
    spec, why = spec_for(first + " " + second)
    assert spec, why
    assert spec["subject"] == first                     # whole, not clipped; the second would not fit
    spec, why = spec_for(second + " " + first)
    assert spec, why
    assert spec["subject"] == second


def test_a_title_or_place_with_a_number_is_left_out_whole():
    statement = "The stone hall was roofed with timber."
    f = fact(statement)
    spec, _ = image_gen.spec_from_facts(statement, {"title": "The 300 Spartans", "place": "Route 66"}, {"facts": [f]})
    assert spec and spec["topic"] == "" and spec["setting"] == ""
    spec, _ = image_gen.spec_from_facts(statement, {"title": "The Stone Hall", "place": "Ellora"}, {"facts": [f]})
    assert spec and spec["topic"] == "The Stone Hall" and spec["setting"] == "Ellora"


def test_the_generator_is_never_told_to_leave_out_what_it_must_clearly_show():
    for f, spec in real_specs():
        prompt = image_gen.build_prompt(spec)
        listed = re.search(r"Draw only what the facts name: (.*?)\. Add no other", prompt)
        if listed is None:
            continue
        allowed = {w.strip() for w in listed.group(1).split(",")}
        shows = re.search(r"It clearly shows: (.*?)\.", prompt)
        for item in (shows.group(1).split(", ") if shows else []):
            assert item in allowed, (f["id"], item, sorted(allowed))
        if f["id"] == "fact_1" and "Colossi" in f["statement"]:
            assert "statue" in allowed, sorted(allowed)


def test_the_closed_inventory_stays_in_the_vision_check(tmp_path, monkeypatch):
    spec = ship_spec()
    prompt = image_gen.build_prompt(spec)
    assert "Draw only what the facts name" not in prompt
    assert getattr(image_gen, "ONLY_NAMED", _MISSING) in prompt
    assert spec.get("allowed_elements") and spec.get("inventory_terms")
    ok, why = judge(tmp_path, monkeypatch, answer(spec, depicted_elements=["wooden ship", "masts", "sails", "cannon"]),
                    spec)
    assert not ok and "cannon" in why


def test_a_clipped_subject_never_ends_on_a_dangling_word():
    long = ("Sponge divers found an ancient bronze shipwreck off the rocky point of a small Greek island in deep water. "
            "The bronze mechanism was retrieved from the shipwreck during salvage operations by the navy")
    for n in range(150, 185):
        clipped = image_gen._clean(long, n)
        assert len(clipped) <= n and broken(clipped + ".") == [], (n, clipped)
