"""Round-3 panel (2026-11-02), facts and chart workstream.

Covers (reel_cover_badge, reel_cover_depth_sandwich): the cover's words are held to the verified sheet on the
finished payload as at draft time. A disputed figure never stands as one number ("THE 25-MILE SAHARA EYE"); a stated
span in the sheet's own unit may; a deck keeps the words that limit its fact ("NO METAL TENSION RINGS", never "BUILT
WITHOUT RINGS"); a size says what it measures. The gate rejects and sends the copy back to the writer, never rewrites it.

Audience chart (internal_audience_chart): rows carry their post date, a footnote says platforms count views their
own way, near-equal markers are fanned so every shape shows, and the key markers stand in one column per platform.
The sheets below are test fixtures copied from data/fact_sheets (statements only)."""
from datetime import datetime, timezone

import pytest

from src import audience_chart as ac
from src import script_engine, trace_check
from src.trace_check import COVER_PROBLEM

RICHAT = {"facts": [
    {"id": "fact-2", "statement": "The circular geological formation spans a diameter of 40 kilometres (25 mi).",
     "disputed": True, "hedge": "Geological field surveys record the feature at 40 kilometres across, though satellite "
                                "observations describe it as spanning almost 50 kilometres.", "evidence": []},
    {"id": "fact-9", "statement": "Initially considered an extraterrestrial impact crater, the impact theory was "
                                  "disproved when suspected shock-metamorphism coesite was identified in 1969 as "
                                  "mislabeled barite.", "disputed": False, "hedge": "", "evidence": []}]}
GUMBAZ = {"facts": [
    {"id": "fact-4", "statement": "The central dome has an external diameter of 44 meters (144 feet), making it the "
                                  "largest unsupported dome in India at the time of its completion.",
     "disputed": False, "hedge": "", "evidence": []},
    {"id": "fact-9", "statement": "The engineering design of the central dome achieves its structural integrity "
                                  "without the use of metal tension rings.", "disputed": False, "hedge": "",
     "evidence": []}]}


def cover(eyebrow="", line1="", line2="", tagline="", **extra):
    return {"thumbnail": {"eyebrow": eyebrow, "line1": line1, "line2": line2, "tagline": tagline}, **extra}


# ------------------------------------------------------------------ disputed figures on the cover
def test_a_disputed_figure_as_one_number_is_refused_on_the_finished_cover():
    problems = trace_check.headline_gate(cover("DESERT ANOMALY", "THE 25-MILE", "SAHARA EYE", "CARVED BY NATURE"),
                                         RICHAT)
    assert any(p.startswith("Headline figure in the thumbnail: 25") and "never one number" in p for p in problems)


@pytest.mark.parametrize("line1", ["THE SAHARA EYE", "40-50 KM ACROSS", "40 TO 50 KM ACROSS"])
def test_a_non_numeric_headline_or_the_sheets_own_span_passes(line1):
    assert trace_check.headline_gate(cover("DESERT ANOMALY", line1, "", ""), RICHAT) == []


def test_a_span_the_sheet_does_not_state_in_that_unit_is_refused():
    # 31 mi is 50 km converted: the sheet never says it in miles, so the span is not the sheet's
    problems = trace_check.headline_gate(cover("", "25-31 MILES ACROSS", "", ""), RICHAT)
    assert problems and problems[0].startswith("Headline figure in the thumbnail")


def test_a_single_estimate_span_is_still_refused():
    sheet = {"facts": [{"id": "f", "statement": "It is 40 to 50 km across.", "disputed": True,
                        "hedge": "by one estimate, it is 40 to 50 km across.", "evidence": []}]}
    assert trace_check.headline_gate(cover("", "40-50 KM ACROSS", "", ""), sheet)


def test_the_cover_gate_fails_the_fact_check_and_its_finding_names_the_cover(monkeypatch):
    monkeypatch.setattr(trace_check.free_llm, "call", lambda *a, **k: trace_check.TraceReport(claims=[
        trace_check.TracedClaim(claim="x", where="title", fact_ids=["fact-2"], verdict="traced", note="")]))
    monkeypatch.setattr("src.reference_facts.with_reference_evidence", lambda sheet, payload: sheet)
    payload = cover("DESERT ANOMALY", "THE 25-MILE", "SAHARA EYE", "", title="The eye in the desert")
    passed, _, _, msg = trace_check.check(payload, RICHAT)
    assert not passed and "(headline gate)" in msg and "thumbnail" in msg
    assert payload["thumbnail"]["line1"] == "THE 25-MILE"          # rejected, never rewritten here
    fixed = cover("DESERT ANOMALY", "THE SAHARA EYE", "", "", title="The eye in the desert")
    assert trace_check.check(fixed, RICHAT)[0]


def test_a_youtube_title_figure_is_held_to_the_same_rule():
    problems = trace_check.headline_gate(cover(youtube_title="The 25-Mile Eye of the Desert"), RICHAT)
    assert problems and problems[0].startswith("Headline figure in the YouTube title")


def test_the_writer_and_the_copy_edit_are_told_the_rules():
    rules = script_engine.USER_PROMPT_TEMPLATE + script_engine.VERIFIED_USER_PROMPT_TEMPLATE
    assert rules.count("- thumbnail:") == 2 and "never as one number" in rules and "FT ACROSS" in rules
    assert "keeps every word that limits it" in rules and "never gives away the answer" in rules
    assert "own span in the same unit" in script_engine.COPY_EDIT_SYSTEM
    assert "floor at or below" in script_engine.COPY_EDIT_SYSTEM   # the earlier rule stays
    assert "- thumbnail:" in script_engine._field_rules(("thumbnail",))
    assert "ESTIMATED" in trace_check.SYSTEM and "hedge words only" in trace_check.SYSTEM


# ------------------------------------------------------------------ the deck keeps what limits its fact
def test_a_deck_that_drops_a_limiting_modifier_is_refused_with_the_sheets_wording():
    problems = trace_check.dropped_limits(cover(tagline="BUILT WITHOUT RINGS"), GUMBAZ)
    assert len(problems) == 1 and problems[0].startswith(COVER_PROBLEM)
    assert "metal, tension" in problems[0] and "NO METAL TENSION RINGS" in problems[0]
    assert trace_check.dropped_limits(cover(tagline="NO TENSION RINGS"), GUMBAZ)              # still drops "metal"
    assert trace_check.dropped_limits(cover(tagline="NO METAL TENSION RINGS"), GUMBAZ) == []
    assert trace_check.dropped_limits(cover(tagline="BUILT WITHOUT METAL TENSION RINGS"), GUMBAZ) == []


def test_a_superlative_keeps_its_modifier_place_and_time():
    assert trace_check.dropped_limits(cover(line1="INDIA'S LARGEST DOME"), GUMBAZ)
    assert trace_check.dropped_limits(cover(line1="LARGEST UNSUPPORTED DOME"), GUMBAZ)       # no place, no time
    assert trace_check.dropped_limits(cover("ONCE INDIA'S", "LARGEST UNSUPPORTED", "DOME"), GUMBAZ) == []


def test_lines_the_sheet_does_not_limit_are_left_to_the_claim_checks():
    assert trace_check.dropped_limits(cover(tagline="NO DOOR, NO NAME"), GUMBAZ) == []
    assert trace_check.dropped_limits(cover(tagline="BUILT WITHOUT RINGS"), {"facts": []}) == []


# ------------------------------------------------------------------ a size says what it measures
def test_a_bare_size_is_refused_and_one_with_its_dimension_passes():
    problems = trace_check.unmeasured_cover_figures(cover(line1="THE 144-FOOT", line2="WHISPERING DOME"), GUMBAZ)
    assert len(problems) == 1 and "'144 FT ACROSS'" in problems[0]
    for ok in ("144 FT ACROSS", "144 FEET WIDE", "A 144-FT DIAMETER"):
        assert trace_check.unmeasured_cover_figures(cover(line1=ok), GUMBAZ) == [], ok


def test_a_size_whose_fact_names_no_dimension_is_left_alone():
    sheet = {"facts": [{"id": "f", "statement": "The block weighs 40 tonnes and sits 120 m from the river.",
                        "disputed": False, "hedge": ""}]}
    assert trace_check.unmeasured_cover_figures(cover(line1="120 M FROM THE NILE"), sheet) == []


def test_the_dimension_is_read_from_the_figures_own_clause():
    sheet = {"facts": [{"id": "f", "statement": "The hall measures 135.5 feet across and 178 feet in height.",
                        "disputed": False, "hedge": ""}]}
    assert trace_check.unmeasured_cover_figures(cover(line1="178 FT TALL"), sheet) == []
    assert trace_check.unmeasured_cover_figures(cover(line1="178 FT ACROSS"), sheet)


# ------------------------------------------------------------------ at draft time: the copy edit's
def test_cover_copy_faults_go_to_the_copy_edit_of_the_thumbnail(curated_payload):
    pkg = script_engine.ScriptPackage.model_validate(dict(curated_payload, thumbnail={
        "eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
        "tagline": "BUILT WITHOUT RINGS"}))
    problems = script_engine.cover_problems(pkg, GUMBAZ)
    assert len(problems) == 2
    assert all(script_engine.copy_problem_fields(p, pkg) == ("thumbnail",) for p in problems)
    assert script_engine.cover_problems(pkg) == []            # no run sheet: nothing to hold it to
    token = script_engine._draft_sheet.set(GUMBAZ)
    try:
        assert set(problems) <= set(script_engine.draft_problems(pkg))
    finally:
        script_engine._draft_sheet.reset(token)


def test_headline_problems_accept_a_stated_span_and_still_refuse_one_number(curated_payload):
    def pkg(line1):
        return script_engine.ScriptPackage.model_validate(dict(curated_payload, title="The eye", thumbnail={
            "eyebrow": "", "line1": line1, "line2": "", "tagline": ""}))
    assert script_engine.headline_problems(pkg("40-50 KM ACROSS"), RICHAT) == []
    assert script_engine.headline_problems(pkg("50 KM ACROSS"), RICHAT)
    assert script_engine.headline_problems(pkg("25-31 MILES"), RICHAT)


# ------------------------------------------------------------------ audience chart
def reel(title, ig, yt, fb, posted=None):
    return {"run_id": title, "title": title, "posted_at": posted,
            "views": {"instagram": ig, "youtube": yt, "facebook": fb}}


POSTED = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)


def test_each_row_carries_its_post_date_beside_an_unchanged_title():
    rows = [reel("Derinkuyu", 1200, 400, 700, POSTED), reel("No date", 900, 300, 200)]
    _, info = ac.render(rows, sample=True, today=datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert info["titles"] == ["Derinkuyu", "No date"]                    # the date is not merged into the title
    assert info["dates"][0] == ac.row_date(POSTED) and info["dates"][1] == ""
    assert ac.row_date(POSTED).split()[1] == "OCT" and not ac.row_date(POSTED).startswith("0")
    date = next(t for t in info["texts"] if t["text"].startswith(info["dates"][0]))
    title = next(t for t in info["texts"] if t["text"] == "Derinkuyu")
    assert date["fill"] == ac.STONE_LT and date["box"][2] < title["box"][0] == pytest.approx(info["title_x"], abs=2)
    assert info["title_width"] == ac.W - 2 * ac.MARGIN - (info["title_x"] - ac.MARGIN)


def test_long_titles_fit_beside_the_date():
    # QF-23: a name the column beside the date holds only cut refuses the chart (control then sends the figures as
    # text); it was drawn with an ellipsis. A name that fits is drawn whole beside its date.
    long = "The Sunken City of Heracleion, lost beneath the Bay of Abu Qir for twelve centuries and more"
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        ac.render([reel(long, 1, 2, 3, POSTED)], sample=True)
    whole = "The Sunken City of Heracleion"
    _, info = ac.render([reel(whole, 1, 2, 3, POSTED)], sample=True)
    probe = ac._Canvas(1)
    width = probe.width(info["titles"][0], ac._Fonts().sans(ac.TEXT * ac.SS, 600))
    assert info["titles"][0] == whole and width <= info["title_width"]


def test_without_dates_the_title_keeps_the_full_width():
    _, info = ac.render([reel("A", 1, 2, 3)], sample=True)
    assert info["title_width"] == ac.W - 2 * ac.MARGIN and ac.row_date(None) == "" and ac.row_date("garbage") == ""


def test_the_footnote_says_views_are_counted_differently_and_names_no_definition():
    _, info = ac.render([reel("A", 10, 5, 1)], sample=False)
    note = next(t for t in info["texts"] if t["text"] == ac.FOOTNOTE)
    foot = next(t for t in info["texts"] if t["text"].startswith("RELIC & RIDDLE"))
    assert note["fill"] == ac.STONE_LT and note["size"] == ac.TEXT and note["box"][3] < foot["box"][1]
    assert not any(w in ac.FOOTNOTE for w in ("SECOND", "3-", "PLAY"))
    _, empty = ac.render([], sample=False)
    assert ac.FOOTNOTE not in [t["text"] for t in empty["texts"]]        # no figures, nothing to qualify


def test_equal_values_leave_every_marker_visible():
    _, info = ac.render([reel("A", 930, 580, 580), reel("B", 750, 700, 500), reel("C", 1200, 400, 700)], sample=True)
    tie, near, apart = info["markers"]
    assert tie["youtube"][0] == tie["facebook"][0]                       # x is the value and never moves
    assert abs(tie["youtube"][1] - tie["facebook"][1]) >= 2 * ac.SPREAD
    assert near["instagram"][1] != near["youtube"][1]                    # 750 and 700 overlap at this scale
    assert len({y for _, y in apart.values()}) == 1                      # well apart: all on the row's line


def test_spread_fans_a_run_about_the_line():
    dy = ac._spread([("a", 100.0), ("b", 100.0), ("c", 105.0), ("d", 400.0)])
    assert dy["d"] == 0 and sorted([dy["a"], dy["b"], dy["c"]]) == [-ac.SPREAD, 0, ac.SPREAD]


def test_key_markers_stand_in_one_column_per_platform():
    _, info = ac.render([reel("A", 930, 580, 580), reel("B", 1200, 400, 700)], sample=True)
    widest = max(ac._Canvas(1).width(ac.figure(v), ac._Fonts().mono(ac.TEXT * ac.SS)) for v in (930, 1200))
    right_of_ig = info["key_x"]["instagram"] + 20 + widest
    assert info["key_x"]["instagram"] < info["key_x"]["youtube"] < info["key_x"]["facebook"]
    assert right_of_ig <= info["key_x"]["youtube"]                        # left of its slot's widest figure
