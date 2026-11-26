"""Lane C cycle 4: script-side copy items (cycle 3 lander rulings), no API call.

(a) The writer is held to signed or hedged figures upstream, so the draw-time figure gate (visual_engine.gate_drawn)
rarely fires: every carousel slide, Story frame and poll question is read at draft time by the gate itself over the
gate's own evidence (script_engine.drawn_figure_problems), and the headline check reads the same evidence (slides and
Stories are derived copy, never evidence). A finding gets the copy edit and never blocks a draft.
(b) No prompt exempts titles or covers from hedging (the fact-review prompt's "not statistical assertions" framing is
gone), and a headline whose figure is left out is rebuilt around another verified detail, never a bare noun.
Topic-agnostic: synthetic copy, no topic names."""
import re
import string
from types import SimpleNamespace

import pytest

from src import improve, script_engine, trace_check, visual_engine
from src.script_engine import (
    DRAWN_FIGURE_PROBLEM,
    HEADLINE_RECAST,
    blocking_problems,
    copy_problem_fields,
    drawn_figure_problems,
    headline_problems,
)

NARRATION = ("By one estimate, the siege lasted 73 days before the city fell. Surveys give the hall's length as "
             "between 40 and 42 meters. The vault held over 900 crates.")
SHEET = {"facts": [
    {"id": "f1", "statement": "The siege lasted 73 days.", "disputed": True, "hedge": "by one estimate, 73 days"},
    {"id": "f2", "statement": "The hall is between 40 and 42 meters long.", "disputed": False, "hedge": ""},
    {"id": "f3", "statement": "The vault held over 900 crates.", "disputed": False, "hedge": ""}]}


def _slide(heading, body=""):
    return SimpleNamespace(heading=heading, body=body)


def _frame(line1="", line2="", body=""):
    return SimpleNamespace(kicker="", line1=line1, line2=line2, body=body)


def _pkg(slides=(), frames=(), poll="", title="", cover=("", "", "", ""), **extra):
    return SimpleNamespace(narration_script=NARRATION, carousel_slides=list(slides), story_slides=list(frames),
                           poll=SimpleNamespace(question=poll, options=["A", "B"]), title=title, caption="",
                           thumbnail=SimpleNamespace(**dict(zip(("eyebrow", "line1", "line2", "tagline"), cover))),
                           **extra)


# ------------------------------------------------------------------------------------------- (a) drawn figures upstream
@pytest.mark.parametrize("slide, why", [
    (_slide("THE 73-DAY SIEGE"), "hedge"),                                  # the signed figure is hedged
    (_slide("THE HALL", "It runs 42 meters from end to end."), "span"),      # one end of a signed span
    (_slide("A VAST STORE", "The vault held over 1,000 crates."), "unsigned"),   # a floor no fact gives
])
def test_a_slide_figure_the_draw_gate_would_withhold_is_a_draft_finding(slide, why):
    problems = drawn_figure_problems(_pkg([slide]), SHEET)
    assert len(problems) == 1 and problems[0].startswith(DRAWN_FIGURE_PROBLEM + "carousel slide 1: "), (why, problems)
    # the finding is the gate's own: the same slide through gate_drawn over the same evidence withholds the same figure
    payload = script_engine._evidence_payload(_pkg([slide]), SHEET)
    _, rows = visual_engine.gate_drawn({"heading": slide.heading, "body": slide.body}, payload, "carousel slide 1",
                                       ["heading", "body"])
    assert rows and all(r["drawn"] in problems[0] for r in rows)


def test_a_signed_or_hedged_figure_passes_and_nothing_is_written_for_it():
    ok = [_slide("AN ESTIMATED 73-DAY SIEGE", "By one estimate, the siege lasted 73 days."),
          _slide("THE HALL", "It runs between 40 and 42 meters."), _slide("THE VAULT", "It held over 900 crates.")]
    assert drawn_figure_problems(_pkg(ok, [_frame("OVER 900 CRATES", "IN ONE VAULT")]), SHEET) == []


def test_story_frames_and_the_poll_are_read_like_the_deck():
    found = drawn_figure_problems(_pkg(frames=[_frame("A 73-DAY SIEGE", "THE CITY FELL")],
                                       poll="Could 1,000 crates fit?"), SHEET)
    assert [p.split(":")[0] for p in found] == [DRAWN_FIGURE_PROBLEM + "Story slide 1", DRAWN_FIGURE_PROBLEM + "poll"]
    assert copy_problem_fields(found[0], None) == ("story_slides",) and copy_problem_fields(found[1], None) == ("poll",)


def test_a_finding_goes_to_the_copy_edit_and_never_blocks_a_draft():
    found = drawn_figure_problems(_pkg([_slide("THE 73-DAY SIEGE")]), SHEET)
    assert copy_problem_fields(found[0], None) == ("carousel_slides",)
    assert blocking_problems(found) == []
    assert improve.classify(found[0]) == "script.drawn_figure"   # its own class on the findings ledger
    # the finding asks for the signed form or another verified detail, never a figure the facts do not give
    assert "with their hedge" in found[0] and HEADLINE_RECAST in found[0]


def test_without_a_verified_sheet_nothing_is_judged():
    assert drawn_figure_problems(_pkg([_slide("THE 73-DAY SIEGE")]), {"facts": []}) == []


def test_the_draft_check_reads_the_draw_gates_evidence_never_derived_copy():
    """Slides and Stories are derived copy at draw time (visual_engine.EVIDENCE_KEYS): a headline figure only a slide
    states is not signed at draft time either."""
    slides = [_slide("THE VAULT", "Its door weighed 12 tonnes.")]
    payload = script_engine._evidence_payload(_pkg(slides, [_frame("12 TONNES")]), SHEET)
    assert set(payload) <= set(visual_engine.EVIDENCE_KEYS) and "carousel_slides" not in payload
    pkg = _pkg(slides, [_frame("12 TONNES")], title="The 12-Tonne Door")
    assert any(p.startswith(script_engine.HEADLINE_PROBLEM + "title") for p in headline_problems(pkg, SHEET))
    pkg = _pkg(slides, title="A Hall 40 to 42 Meters Long")
    assert headline_problems(pkg, SHEET) == []


def test_every_drawn_deck_field_is_read():
    """The fields the draft check reads are the ones the deck and the Story gate (multi_format.gate_deck calls)."""
    import inspect

    from src import multi_format
    src = inspect.getsource(multi_format)
    assert 'gate_deck(slides_data, None, script_payload, "Story", ("line1", "line2", "body")' in src
    assert dict((k, f) for k, _l, f in script_engine.DRAWN_FIGURE_FIELDS) == {
        "carousel_slides": ("heading", "body"), "story_slides": ("line1", "line2", "body")}
    assert inspect.signature(multi_format.gate_deck).parameters["fields"].default == ("heading", "body")


# ----------------------------------------------------------------------- (b) the prompts: no exemption, no bare heads
PROMPTS = {"writer": script_engine.USER_PROMPT_TEMPLATE, "writer (verified)": script_engine.VERIFIED_USER_PROMPT_TEMPLATE,
           "copy edit": script_engine.COPY_EDIT_SYSTEM, "fact check": trace_check.SYSTEM}
# an exemption of headlines from hedging, in any wording ("not exempt" is the rule itself, never a hit)
EXEMPTION = re.compile(r"(?<!not )\bexempt(?:ed)?\b[^.]{0,80}hedg|hedg[^.]{0,80}(?<!not )\bexempt\b|"
                       r"not statistical assertions|never force[^.]{0,40}(?:estimated|hedg)", re.IGNORECASE)


def test_the_exemption_sweep_sees_the_withdrawn_wording_and_not_the_rule():
    assert EXEMPTION.search("covers are EXEMPT from mandatory statistical hedging words")
    assert EXEMPTION.search("titles are curiosity-driven headlines, not statistical assertions")
    assert not EXEMPTION.search("titles and covers are not exempt from hedges")


@pytest.mark.parametrize("name", PROMPTS)
def test_no_prompt_exempts_a_headline_from_its_hedge(name):
    assert not EXEMPTION.findall(PROMPTS[name]), name


def test_every_llm_prompt_in_the_copy_path_is_free_of_the_exemption():
    """A sweep, not a list: every module-level prompt string in the writer, the copy edit and the fact checks."""
    from src import fact_checker
    for module in (script_engine, trace_check, fact_checker):
        for name, value in vars(module).items():
            if isinstance(value, str) and len(value) > 200 and name.isupper():
                assert not EXEMPTION.findall(value), (module.__name__, name)


def test_the_writer_and_the_edit_rebuild_a_headline_whose_figure_is_left_out():
    for name in ("writer", "writer (verified)", "copy edit"):
        assert HEADLINE_RECAST in PROMPTS[name], name
    for name in ("writer", "writer (verified)"):
        assert PROMPTS[name].count("never a figure only the") == 2, name   # the deck and the Story
        fields = {f for _t, f, _s, _c in string.Formatter().parse(PROMPTS[name]) if f}
        PROMPTS[name].format(**{f: "x" for f in fields})                 # still a valid template
    # the copy edit reads the field rules from the writer's prompt: the slide rule reaches it
    rules = script_engine._field_rules(("carousel_slides", "story_slides"))
    assert rules.count("never a figure only the") == 2


def test_a_headline_finding_carries_the_recast_instruction():
    pkg = _pkg(title="The 73-Day Siege")
    found = headline_problems(pkg, SHEET)
    assert found and all(HEADLINE_RECAST in p for p in found)
