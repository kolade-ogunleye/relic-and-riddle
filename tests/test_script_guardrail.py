import pytest

from src.fact_checker import ClaimFactChecker
from src.models import ClaimAudit, FactCheckReport, ScriptPackage, Source
from src.script_engine import ScriptEngine, ScriptUnavailableError, validate_structure


def test_curated_script_is_structurally_valid(curated_payload):
    assert validate_structure(ScriptPackage.model_validate(curated_payload)) == []


def test_curated_script_offline_requires_human_review(curated_payload):
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed and "reviewed" in notes
    assert ScriptEngine().verify_claims(curated_payload, allow_unreviewed_curated=True)[0]
    curated_payload["human_reviewed"] = True
    assert ScriptEngine().verify_claims(curated_payload)[0]


@pytest.mark.parametrize("mutate, expected", [
    (lambda p: p.update(narration_script="Too short.", closing_sentence="Too short."), "words"),
    (lambda p: p.update(narration_script=p["narration_script"][:-1] + " and", closing_sentence="x"), "trailing word"),
    (lambda p: p.update(caption=p["caption"] + " Built by aliens."), "trope"),
    (lambda p: p.update(caption=p["caption"] + " A study reported discoveries."), "machine-sounding phrasing"),
    (lambda p: p.update(story_slides=p["story_slides"][:2]), "3 story slides"),
    (lambda p: p.update(title="x" * 61), "Title"),
])
def test_structural_rejections(curated_payload, mutate, expected):
    curated_payload["human_reviewed"] = True
    mutate(curated_payload)
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed
    assert expected in notes


def test_trope_check_uses_word_boundaries(curated_payload):
    # "alienated" must not trip the "alien" filter
    curated_payload["caption"] += " Villagers felt alienated."
    assert ScriptEngine().verify_claims(curated_payload, allow_unreviewed_curated=True)[0]


def test_uncurated_script_fails_closed_without_claude(curated_payload):
    curated_payload["origin"] = "claude"
    passed, _, notes = ScriptEngine().verify_claims(curated_payload)
    assert not passed and "failing closed" in notes


def test_topic_without_any_script_source_raises():
    with pytest.raises(ScriptUnavailableError):
        ScriptEngine().generate_script({"id": "roman_self_healing_concrete", "title": "Roman concrete"})


def _claim(verdict, hedged=False, sources=True):
    return ClaimAudit(claim="c", verdict=verdict, hedged_in_script=hedged, note="n",
                      sources=[Source(title="s", url="https://e.org")] if sources else [])


@pytest.mark.parametrize("claims, ok", [
    ([_claim("supported")], True),
    ([_claim("supported"), _claim("disputed", hedged=True)], True),
    ([_claim("supported"), _claim("disputed", hedged=False)], False),
    ([_claim("supported"), _claim("unsupported")], False),
    ([_claim("false")], False),
    ([_claim("supported", sources=False)], False),
    ([], False),
])
def test_fact_check_pass_rule(claims, ok):
    passed, confidence, audits, _ = ClaimFactChecker.evaluate(FactCheckReport(claims=claims, summary=""))
    assert passed is ok
    assert len(audits) == len(claims)


def test_supported_claim_with_hallucinated_source_fails():
    report = FactCheckReport(claims=[_claim("supported")], summary="")
    assert ClaimFactChecker.evaluate(report, retrieved_urls={"https://e.org"})[0]
    assert not ClaimFactChecker.evaluate(report, retrieved_urls={"https://other.org"})[0]


def test_verdict_casing_is_normalized():
    assert ClaimAudit(claim="c", verdict=" Supported ", hedged_in_script=False, note="", sources=[]).verdict == "supported"


def test_ai_phrasing_and_generic_hashtags_are_rejected(curated_payload):
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    curated_payload["caption"] += " A testament to ancient ingenuity."
    curated_payload["hashtags"] = curated_payload["hashtags"][:4] + ["#ReelsViral"]
    problems = " ".join(validate_structure(ScriptPackage.model_validate(curated_payload)))
    assert "testament to" in problems and "#ReelsViral" in problems


def test_clean_script_has_no_style_problems(curated_payload):
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    assert validate_structure(ScriptPackage.model_validate(curated_payload)) == []


def _pkg(curated_payload, **changes):
    from src.models import ScriptPackage
    data = {k: v for k, v in curated_payload.items() if k in ScriptPackage.model_fields}
    data.update(changes)
    return ScriptPackage.model_validate(data)


def test_caption_template_line_and_hook_repeat_are_rejected(curated_payload):
    from src.script_engine import validate_structure
    assert validate_structure(_pkg(curated_payload)) == []
    bad = _pkg(curated_payload, caption=curated_payload["caption"] + "\n\n📍 Derinkuyu, Turkey")
    assert any("📍" in p for p in validate_structure(bad))
    hooky = _pkg(curated_payload, caption=curated_payload["hook_sentence"] + "\n\nMore context here.")
    assert any("repeats the narration hook" in p for p in validate_structure(hooky))


def test_carousel_caption_must_be_its_own_text(curated_payload):
    from src.script_engine import validate_structure
    same = _pkg(curated_payload, carousel_caption=curated_payload["caption"])
    assert any("carousel_caption is too close" in p for p in validate_structure(same))
    fresh = _pkg(curated_payload, carousel_caption="Swipe through seven facts about a city dug into rock. "
                                                    "Which level would you hide in? #Derinkuyu #Cappadocia")
    assert validate_structure(fresh) == []


def test_narrating_the_visuals_is_rejected(curated_payload):
    from src.script_engine import validate_structure
    text = curated_payload["narration_script"].replace("It led into", "As you can see, it led into", 1)
    assert any("machine-sounding" in p for p in validate_structure(_pkg(curated_payload, narration_script=text)))


def test_carousel_post_uses_own_caption_with_image_credits():
    from main import PipelineCoordinator
    payload = {"caption": "Reel text\n\n📷 Images via Wikimedia Commons: Ann (CC BY 4.0)", "carousel_caption": "Swipe."}
    assert PipelineCoordinator._carousel_caption(payload) == "Swipe.\n\n📷 Images via Wikimedia Commons: Ann (CC BY 4.0)"
    assert PipelineCoordinator._carousel_caption({"caption": "Reel text"}) == "Reel text"


def test_meta_caption_limits(curated_payload):
    from src.script_engine import validate_structure
    six = _pkg(curated_payload, hashtags=["#A", "#B", "#C", "#D", "#E", "#F"])
    assert any("at most 5" in p for p in validate_structure(six))
    tagged_comment = _pkg(curated_payload, first_comment="What do you think? #Derinkuyu")
    assert any("more than 5 hashtags" in p for p in validate_structure(tagged_comment))
    long_first = _pkg(curated_payload, caption="X" * 130 + "\n\nMore.\n\n#Derinkuyu")
    assert any("125" in p for p in validate_structure(long_first))
    spammy = _pkg(curated_payload, caption="Hidden city 🏛️🔥😱🤯✨\n\nMore.\n\n#Derinkuyu")
    assert any("emoji" in p for p in validate_structure(spammy))
    warm = _pkg(curated_payload, caption="A city hidden under a house 🏛️\n\nMore context. Would you go down? 👇\n\n"
                                         "#Derinkuyu #Cappadocia #UndergroundCity #ByzantineHistory #Archaeology")
    assert validate_structure(warm) == []


def test_invalid_graphic_is_dropped_not_fatal(curated_payload):
    from src.script_engine import prune_graphics, validate_structure
    pkg = _pkg(curated_payload, stat_cards=[{"value": "X" * 40, "label": "too long a value"}],
               timeline=[{"year": -800, "approximate": True, "label": "First chambers"},
                         {"year": 1963, "approximate": False, "label": "Rediscovered"}])
    assert validate_structure(pkg)  # the oversized card fails as is
    pruned = prune_graphics(pkg)
    assert pruned.stat_cards is None and pruned.timeline is not None  # only the bad graphic goes
    assert validate_structure(pruned) == []


def test_fact_starved_topic_error():
    from src.fact_sheet import FactStarvedTopicError, MIN_VERIFIED_FACTS
    assert MIN_VERIFIED_FACTS == 6
    import pytest
    with pytest.raises(FactStarvedTopicError):
        # Emulate build logic when facts < 6
        facts = [{"id": "fact_1"}]
        if len(facts) < MIN_VERIFIED_FACTS:
            raise FactStarvedTopicError("Topic yielded only 1 verified fact (6 required).")


def test_resilient_surgical_sentence_revision(curated_payload):
    from src.script_engine import ScriptEngine
    engine = ScriptEngine()
    fake_sheet = {
        "topic_id": "test_topic",
        "facts": [
            {"id": "fact_1", "statement": "Roman concrete stands intact.", "disputed": False, "hedge": "", "evidence": []}
        ]
    }
    prev = dict(curated_payload)
    prev["caption"] = "Ancient harbor seawalls resist ocean decay. Unverified petrographic claim was stated."
    # Test normalized core match
    class MockEdit:
        find = "Unverified petrographic claim was stated."
        replace = "Roman concrete stands intact."

    class MockResult:
        edits = [MockEdit()]

    from unittest.mock import patch
    with patch("src.script_engine._structured", return_value=MockResult()):
        revised = engine._revise_surgically(prev, "Unverified claim", fake_sheet)
        assert revised is not None
        assert "Roman concrete stands intact." in revised.caption
        assert "Unverified petrographic claim" not in revised.caption



def test_poll_options_must_answer_the_question():
    from src.script_engine import poll_problems
    # A yes/no question answered with two choices (the Kailasa dry run) is caught...
    assert poll_problems("Is top-down carving harder than quarrying?", ["Carving top-down", "Quarrying stone"])
    # ...while either/or questions, and yes/no questions with yes/no options, pass.
    assert not poll_problems("Which took more work?", ["Carving downward", "Hauling the stone"])
    assert not poll_problems("Island tides or ancient hands?", ["Island tides", "Ancient hands"])
    assert not poll_problems("Could modern tools match this speed?", ["Yes", "No way"])
    assert poll_problems("Which took more work?", ["Carving downward from the summit", "Hauling"])  # too long to show
    assert poll_problems("Which took more work?", ["Carving", "Hauling", "Both"])                   # not exactly 2


def test_over_long_graphic_labels_are_shortened_before_any_is_dropped(curated_payload, monkeypatch):
    from src import free_llm, script_engine
    curated_payload["stat_cards"] = [
        {"value": "200,000 tons", "label": "of basalt rock removed from the hillside by hand over the years"},
        {"value": "1,200 years", "label": "standing"}]
    pkg = ScriptPackage.model_validate(curated_payload)
    monkeypatch.setattr(free_llm, "ready", lambda: True)
    monkeypatch.setattr(free_llm, "call", lambda *a, **k: script_engine._Shortened(texts=["of basalt removed by hand"]))
    out = script_engine.prune_graphics(pkg)
    assert [c.label for c in out.stat_cards] == ["of basalt removed by hand", "standing"]
    # A rewrite that still doesn't fit drops only that card; the other stays.
    monkeypatch.setattr(free_llm, "call", lambda *a, **k: script_engine._Shortened(texts=["x" * 41]))
    assert [c.label for c in script_engine.prune_graphics(pkg).stat_cards] == ["standing"]


def test_writer_failures_are_named_not_blamed_on_configuration(curated_payload, monkeypatch):
    from src import script_engine
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    engine = ScriptEngine()
    bad = dict(curated_payload, narration_script=curated_payload["narration_script"][:-1] + " that", closing_sentence="x")
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: ScriptPackage.model_validate(bad))
    monkeypatch.setattr(engine, "_load_curated", lambda topic_id: None)
    with pytest.raises(ScriptUnavailableError) as raised:
        engine.generate_script({"id": "no_curated_topic", "title": "T"})
    assert "made 3 attempts" in str(raised.value) and "trailing word 'that'" in str(raised.value)
    assert "ANTHROPIC_API_KEY" not in str(raised.value)


def test_surgical_revision_that_keeps_a_rejected_claim_falls_back_to_a_rewrite(curated_payload, monkeypatch):
    from src import script_engine
    claim = "Metallurgical analysis determined that finer pearlite spacing refined the microstructure"
    previous = dict(curated_payload, caption=curated_payload["caption"] + " " + claim + ".")
    monkeypatch.setattr(script_engine, "_structured", lambda *a, **k: script_engine.Edits(edits=[]))  # the edit misses
    monkeypatch.setattr(script_engine.fact_sheet, "as_prompt", lambda sheet: "facts")
    findings = f"1 blocking claim(s): '{claim}.' (unsupported: the source says 'likely')"
    assert ScriptEngine()._revise_surgically(previous, findings, {"facts": []}) is None


def test_the_hedge_quoted_in_a_finding_is_not_mistaken_for_the_rejected_claim(curated_payload, monkeypatch):
    from src import script_engine
    hedge = "measurements of the landmark range from 40 to nearly 50 kilometers across"
    previous = dict(curated_payload, caption=curated_payload["caption"] + " A 30-mile ring hides in West Africa.")
    fixed = curated_payload["caption"] + " The ring's " + hedge + "."
    monkeypatch.setattr(script_engine, "_structured", lambda *a, **k: script_engine.Edits(edits=[
        script_engine.Edit(find="A 30-mile ring hides in West Africa.", replace="The ring's " + hedge + ".")]))
    monkeypatch.setattr(script_engine.fact_sheet, "as_prompt", lambda sheet: "facts")
    findings = ("1 untraceable claim(s): 'A 30-mile ring hides in West Africa for good.' ([caption]; untraced: "
                f"it drops the required hedge ('{hedge}').)")
    revised = ScriptEngine()._revise_surgically(previous, findings, {"facts": []})
    assert revised is not None and revised.caption == fixed


def test_a_slow_voice_never_leaves_the_writer_without_a_valid_length(curated_payload, monkeypatch):
    # Production 2026-10-23: at about 0.5 s a word, 105 words ran 52.9 s, past the 51.5 s limit, while
    # the word check refused anything under 105; three drafts failed and the run with them.
    from src import audio_engine, script_engine
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 0.5 * len(text.split()) / speed)
    monkeypatch.setattr(audio_engine, "pace_ratio", lambda key: 1.0)
    closing = curated_payload["closing_sentence"]

    def draft(n):
        body = " ".join(["stone"] * (n - len(closing.split())))
        return _pkg(curated_payload, narration_script=f"{body} {closing}")

    long_problem = script_engine.spoken_length_problems(draft(116))  # 58 s: past what a faster read fits
    target = int(long_problem[0].split("narration in ")[1].split()[0].split("-")[1])
    assert target < 103  # an achievable target, not clamped to the prompt's 105
    fits = draft(target)
    assert script_engine.spoken_length_problems(fits) == []
    assert not any("words" in p for p in validate_structure(fits))  # and the word check accepts it


def test_extraterrestrial_as_impact_science_is_not_the_trope():
    # A Richat draft was rejected for "once considered an extraterrestrial impact crater".
    from src.script_engine import find_banned_tropes
    assert find_banned_tropes("The feature was once considered an extraterrestrial impact crater.") == []
    assert find_banned_tropes("Iron from an extraterrestrial meteorite.") == []
    assert find_banned_tropes("Some claim extraterrestrial visitors built it.") == ["extraterrestrial"]
    assert find_banned_tropes("Built by aliens.") == ["aliens"]


def test_draft_bookkeeping_is_fixed_without_a_rewrite(curated_payload):
    # Dev (2026-10-23): two Amber Room drafts were rewritten for a closing_sentence that didn't match the
    # narration's last sentence and a 152-character caption first line holding two sentences.
    from src.script_engine import normalize_copy
    narration = curated_payload["narration_script"]
    first = "The Amber Room vanished in 1945 after the siege. Nobody has found a single amber panel since then, despite eight decades of searching across Europe."
    pkg = _pkg(curated_payload, closing_sentence="A different closing line.", caption=first + "\nMore text.")
    fixed = normalize_copy(pkg)
    assert narration.strip().endswith(fixed.closing_sentence)
    assert fixed.caption.split("\n")[0] == "The Amber Room vanished in 1945 after the siege."
    assert fixed.caption.replace("\n", " ").split() == pkg.caption.replace("\n", " ").split()  # same words
    long_sentence = "x" * 130
    assert normalize_copy(_pkg(curated_payload, caption=long_sentence)).caption == long_sentence  # still a rewrite


def test_a_short_draft_is_extended_not_rewritten_from_scratch(curated_payload, monkeypatch):
    # Production, 2026-10-23: three fresh rewrites each ran shorter (38.8, 37.3, 35.8 s) while told
    # "add about N words". The retry now carries the draft and a word window, and converges.
    from src import audio_engine, script_engine
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(audio_engine, "pace_ratio", lambda key: 1.0)
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: len(text.split()) * 0.4 / speed)
    engine = ScriptEngine()
    monkeypatch.setattr(engine, "_load_curated", lambda topic_id: None)
    monkeypatch.setattr(engine, "_sheet", lambda topic: None)
    seen = []
    base = [f"word{i}" for i in range(400)]  # distinct words: a repeated phrase is itself sent back (2026-10-25)
    short = " ".join(base[:70])  # with the closing sentence, well under 42 s at 0.4 s a word

    def write(topic, feedback):
        seen.append(feedback)
        import re
        found = re.search(r"extend the narration to (\d+)-(\d+) words", feedback)
        words = base[: int(found.group(1)) + 1] if found else short.split()
        closing = curated_payload["closing_sentence"]
        text = " ".join(words).rstrip(".") + ". " + closing
        return ScriptPackage.model_validate(dict(curated_payload, narration_script=text))
    monkeypatch.setattr(engine, "_generate_with_claude", write)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    out = engine.generate_script({"id": "t", "title": "T"})
    assert len(seen) == 2                                   # the second draft, written to the window, passes
    assert "<previous_narration>" in seen[1] and "extend the narration to" in seen[1]
    assert len(out["narration_script"].split()) * 0.4 >= script_engine.TARGET_DURATION_MIN


def test_a_narration_that_repeats_a_phrase_is_sent_back():
    # Production 2026-10-25: two sentences in a row opened "By one estimate," and the video check caught it
    # after the voiceover was paid for. A name or a number said twice is not a repeat.
    from src.script_engine import repeated_phrases
    assert repeated_phrases("It was found in 1901. By one estimate, it had 35 gears. By one estimate, one had 223 teeth.") == ["by one estimate"]
    assert repeated_phrases("The Richat Structure is vast. Seen from orbit, the Richat Structure looks like an eye.") == []
    assert repeated_phrases("Rings are 30 metres wide. Outer rings are 70 metres wide.") == []
    assert repeated_phrases("It tracked the Moon. It predicted eclipses.") == []
