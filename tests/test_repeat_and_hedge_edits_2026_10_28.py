"""Writer rounds lost to repeats and dropped hedges (development, Nazca 2026-10-28, run.log 22:28-22:38): a surgical
revision added "by one estimate" a second time, two length edits said "geoglyphs etched into" twice, each followed by
a full rewrite, and a carousel caption and a slide dropped the mandatory "by one estimate". The editors are now told
the repeat rule, the phrases already said and the mandatory hedges; a draft whose only fault is a repeated phrase is
reworded by one short edit instead of rewritten. Every check stays as strict."""
import pytest

from src import script_engine
from src.models import ScriptPackage
from src.script_engine import REPEAT_PROBLEM, ScriptEngine, mandatory_hedges_note, repeat_rules

SHEET = {"topic_id": "t1", "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}],
         "facts": [{"id": "f1", "statement": "The desert holds over 800 straight lines.", "kind": "number",
                    "disputed": True, "hedge": "by one estimate, the desert holds over 800 straight lines.",
                    "evidence": []},
                   {"id": "f2", "statement": "The lines are shallow trenches.", "kind": "fact", "disputed": False,
                    "hedge": "", "evidence": []}]}
PHRASE = "geoglyphs etched into"
PAYOFF = "Those geoglyphs marked the landscape."


def _narration(closing, words, repeat):
    opening = "Geoglyphs cover the desert."
    body = opening + " " + " ".join(f"w{i}" for i in range(words - len(PAYOFF.split()) - len(opening.split())))
    extra = f" Some {PHRASE} the plain. Other {PHRASE} the hills." if repeat else " Some geoglyphs mark the plain."
    return f"{body}.{extra} {PAYOFF} {closing}"


def _engine(monkeypatch, curated_payload):
    curated_payload["payoff_sentence"] = PAYOFF
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script, **k})
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    # The real repeat rule's wording, from the real detector; length by word count (0.4 s a word).
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [
        REPEAT_PROBLEM + ", ".join(f"'{r}'" for r in script_engine.repeated_phrases(pkg.narration_script))
        + "; say it once (one hedge can cover two related figures) or reword the second, keeping every disputed "
          "fact hedged."] if script_engine.repeated_phrases(pkg.narration_script) else [])
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [] if len(pkg.narration_script.split()) >= 100 else [
        f"Narration runs {len(pkg.narration_script.split()) * 0.4:.1f}s when spoken (minimum 42s). This draft is "
        f"{len(pkg.narration_script.split())} words; extend the narration to 105-116 words with more verified facts "
        "from the research notes, keeping the closing sentence."])
    return engine


def test_a_length_edit_that_leaves_only_a_repeat_is_reworded_not_rewritten(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    closing = curated_payload["closing_sentence"]
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        ScriptPackage.model_validate(dict(curated_payload, narration_script=_narration(closing, 60, False))))

    def revise(previous, findings, sheet):
        edits.append(findings.split(":", 1)[0])
        repeat = findings.startswith("LENGTH:")                          # the length edit says the phrase twice
        return ScriptPackage.model_validate(dict(previous, narration_script=_narration(closing, 105, repeat)))
    monkeypatch.setattr(engine, "_revise_surgically", revise)
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and edits == ["LENGTH", "REPEAT"]            # one draft, two short edits
    assert PHRASE not in out["narration_script"] and out["fact_sheet_used"] is True


def test_a_surgical_revision_that_only_repeats_a_phrase_is_reworded_not_rewritten(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    closing = curated_payload["closing_sentence"]
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1))

    def revise(previous, findings, sheet):
        edits.append(findings)
        return ScriptPackage.model_validate(dict(previous, narration_script=_narration(closing, 105, not findings.startswith("REPEAT:"))))
    monkeypatch.setattr(engine, "_revise_surgically", revise)
    previous = dict(curated_payload, fact_sheet=True)
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim here' (unsupported)", previous=previous)
    assert not written and len(edits) == 2 and edits[1].startswith(f"REPEAT: {REPEAT_PROBLEM}'{PHRASE}'")
    assert PHRASE not in out["narration_script"]


def test_a_repeat_edit_that_still_repeats_falls_back_to_the_rewrite(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    closing = curated_payload["closing_sentence"]
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        ScriptPackage.model_validate(dict(curated_payload, narration_script=_narration(closing, 105, False))))
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: ScriptPackage.model_validate(
        dict(previous, narration_script=_narration(closing, 105, True))))
    previous = dict(curated_payload, fact_sheet=True)
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim here' (unsupported)", previous=previous)
    assert written == [1] and PHRASE not in out["narration_script"]       # the check held; the rewrite as before


def test_the_editors_prompt_carries_the_phrases_said_and_the_mandatory_hedges(monkeypatch, curated_payload):
    seen = []
    monkeypatch.setattr(script_engine, "_structured", lambda system, prompt, schema, **kw: seen.append((system, prompt))
                        or script_engine.Edits(edits=[]))
    narration = "By one estimate, the desert holds over 800 straight lines. Some geoglyphs etched into the plain stay sharp."
    ScriptEngine()._revise_surgically(dict(curated_payload, narration_script=narration), "LENGTH: extend it", SHEET)
    system, prompt = seen[0]
    assert "MANDATORY HEDGES" in prompt and "by one estimate, the desert holds over 800 straight lines." in prompt
    assert 'the hedge: "by one estimate"' in prompt and "every story and carousel slide" in prompt
    assert "the carousel caption" in prompt and "The lines are shallow trenches" not in prompt.split("MANDATORY")[1]
    assert "REPEATS:" in prompt and '"by one estimate"' in prompt and '"geoglyphs etched into"' in prompt
    assert "one hedge can cover two related figures" in prompt.lower() and "REPEAT finding" in system


def test_the_writer_and_the_story_shortener_are_told_the_mandatory_hedges(monkeypatch, curated_payload):
    seen = []

    def stop(system, prompt, schema, **kw):
        seen.append(prompt)
        raise script_engine.LLMError("stop")
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_structured", stop)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    with pytest.raises(script_engine.LLMError):
        engine._generate_with_claude({"id": "t1", "title": "T"}, "")
    assert "MANDATORY HEDGES" in seen[0] and script_engine.REPEAT_RULE in seen[0]
    shortened = []
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "structured", lambda system, prompt, schema, **kw: (
        shortened.append(prompt) or schema(texts=[])))
    slides = [dict(x) for x in curated_payload["story_slides"]]
    slides[0]["body"] = "Over 800 straight lines cross the desert floor."
    pkg = ScriptPackage.model_validate(dict(curated_payload, story_slides=slides))
    engine._shorten_story_slides(pkg, ["Story slide 1's body takes 4 lines on the frame (at most 3)"], SHEET)
    assert "MANDATORY HEDGES" in shortened[0]


def test_the_notes_are_empty_without_disputed_facts_and_list_each_said_phrase_once():
    assert mandatory_hedges_note(None) == "" and mandatory_hedges_note({"facts": SHEET["facts"][1:]}) == ""
    note = repeat_rules("The lines stay sharp. The lines stay sharp.")
    assert note.count('"the lines stay"') == 1 and note.startswith("\n\nREPEATS: ")


def test_past_the_cap_the_phrase_list_keeps_every_opening_and_the_first_and_last_runs(monkeypatch):
    monkeypatch.setattr(script_engine, "_MAX_LISTED_PHRASES", 10)
    narration = ("Ancient builders scraped pale stones aside. Quiet winds rarely disturb fragile desert ground. "
                 "Modern pilots photograph enormous figures from above. Careful surveyors mapped every winding path.")
    openings, grams = script_engine._phrase_runs(narration)
    listed = repeat_rules(narration)
    assert all(f'"{o}"' in listed for o in openings)
    rest = [g for g in dict.fromkeys(grams) if g not in openings]
    shown = listed.split("Already said (never again in a new or changed sentence): ")[1].rstrip(".")
    # every opening, then the first and the last runs (the end is where a length edit adds its sentences)
    assert shown == "; ".join(f'"{x}"' for x in list(dict.fromkeys(openings)) + rest[:3] + rest[-3:])
    assert len(rest) > 6


def test_a_rephrased_hedge_is_shown_whole_and_figures_are_never_cut():
    fact = {"statement": "Surveys record the area as ranging between 50 and 450 square kilometers.", "disputed": True,
            "hedge": "archaeological surveys describe the complex as covering anywhere from 50 to nearly 450 square kilometers"}
    assert script_engine._hedge_words(fact) == ""                                     # not a single insertion
    note = mandatory_hedges_note({"facts": [fact]})
    assert "(the hedge:" not in note and "anywhere from 50 to nearly 450" in note
    assert "never the same hedge twice in one text" in note.lower()
    long = "Among more than 70 figures, the spider measures 47 meters, the hummingbird 93 meters, and the condor 134 meters."
    clipped = script_engine._clip(long, 100)
    assert clipped.endswith(" ...") and not clipped[:-4].rstrip().endswith(("134", "condor 134 m"))
    assert script_engine._clip(long, 100)[:-4] in long


def test_the_story_shortener_is_told_only_the_hedges_its_slides_state():
    facts = [dict(SHEET["facts"][0]),
             {"id": "f3", "statement": "The condor figure measures 134 meters.", "disputed": True,
              "hedge": "by one estimate, the condor figure measures 134 meters."}]
    note = mandatory_hedges_note({"facts": facts}, texts=["Over 800 straight lines cross the desert."])
    assert "800 straight lines" in note and "condor" not in note
    assert mandatory_hedges_note({"facts": facts}, texts=["Nothing measured here at all."]) == ""
