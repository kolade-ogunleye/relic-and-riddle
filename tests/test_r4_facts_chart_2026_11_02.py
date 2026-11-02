"""Round-4 panel (2026-11-02), facts and chart workstream.

Covers (reel_cover_badge): a year on the cover says what happened in it, in the sheet's own word for that year or
one of its family ("DERINKUYU · FOUND 1963" for "rediscovered in 1963"); a bare "DERINKUYU, 1963" under "A
FORGOTTEN CITY" reads as the founding date and goes back to the writer, at draft time and on the finished payload.

Audience chart (internal_audience_chart): render() draws rows newest first whatever order the caller passes; undated
rows follow in the caller's order. The sheet below is a test fixture copied from data/fact_sheets (statements only)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import audience_chart as ac
from src import script_engine, trace_check
from src.trace_check import COVER_PROBLEM

DERINKUYU = {"facts": [
    {"id": "derinkuyu_discovery_1963", "statement": "The subterranean city was accidentally rediscovered in 1963 when "
     "a local resident knocked through a basement wall during home renovations.", "disputed": False, "hedge": "",
     "evidence": []},
    {"id": "derinkuyu_capacity", "statement": "The complex was capable of sheltering up to 20,000 people alongside "
     "their livestock and food supplies.", "disputed": False, "hedge": "", "evidence": []},
    {"id": "derinkuyu_origins_phrygian", "statement": "The earliest tunnels and chambers may date back to the 8th to "
     "7th century BC, likely carved by the Phrygians.", "disputed": False, "hedge": "", "evidence": []},
    {"id": "derinkuyu_1923_abandonment", "statement": "The underground complex was continuously used for shelter "
     "until it was completely abandoned following the 1923 population exchange between Greece and Turkey.",
     "disputed": False, "hedge": "", "evidence": []}]}


def cover(eyebrow="", line1="", line2="", tagline=""):
    return {"thumbnail": {"eyebrow": eyebrow, "line1": line1, "line2": line2, "tagline": tagline}}


def year_problems(payload, sheet=DERINKUYU):
    return [p for p in trace_check.cover_copy_problems(payload, sheet) if "the year" in p]


# ------------------------------------------------------------------ a year says what happened in it
@pytest.mark.parametrize("tagline", ["DERINKUYU, 1963", "DERINKUYU 1963", "1963", "TURKEY · 1963"])
def test_a_bare_year_beside_a_place_is_refused(tagline):
    problems = year_problems(cover("A FORGOTTEN CITY", "18 LEVELS", "UNDERGROUND", tagline))
    assert len(problems) == 1
    p = problems[0]
    assert p.startswith(COVER_PROBLEM) and "the tagline" in p and "1963" in p
    assert "derinkuyu_discovery_1963" in p and "REDISCOVERED 1963" in p   # the sheet's own word as the hint


@pytest.mark.parametrize("tagline", ["DERINKUYU · FOUND 1963", "REDISCOVERED 1963", "FOUND IN 1963",
                                     "DISCOVERED 1963", "HIDDEN UNTIL 1963", "LOST UNTIL 1963"])
def test_a_year_with_the_sheets_event_word_or_its_family_passes(tagline):
    assert year_problems(cover("A FORGOTTEN CITY", "", "", tagline)) == []


@pytest.mark.parametrize("tagline", ["BUILT 1963", "FOUNDED 1963", "ABANDONED 1963"])
def test_a_year_with_another_events_word_is_refused(tagline):
    # the sheet's 1963 is a rediscovery: a building or founding word would put the wrong event in that year
    problems = year_problems(cover("", "", "", tagline))
    assert problems and f"puts '{tagline.split()[0]}' in the year 1963" in problems[0]


def test_the_event_word_is_read_from_beside_the_year_not_the_whole_sentence():
    # "knocked" is in the 1963 sentence but follows "when": the event of 1963 is still only the rediscovery family
    own, allowed = trace_check._event_words(DERINKUYU["facts"][0]["statement"], "1963")
    assert "rediscovered" in own and "found" in allowed
    assert "built" not in allowed and "founded" not in allowed
    own23, allowed23 = trace_check._event_words(DERINKUYU["facts"][3]["statement"], "1923")
    assert "abandoned" in own23 and "deserted" in allowed23


def test_the_eyebrow_and_headline_are_held_too():
    assert any("the eyebrow" in p for p in year_problems(cover("DERINKUYU, 1963", "", "", "")))
    assert any("the line1" in p for p in year_problems(cover("", "1923", "", "")))
    assert year_problems(cover("", "ABANDONED IN 1923", "", "")) == []


@pytest.mark.parametrize("line", ["20,000 PEOPLE", "SHELTER FOR 20,000", "1963 FT DEEP", "THE 1900 STEPS"])
def test_counts_measures_and_years_no_fact_holds_are_left_to_the_number_checks(line):
    assert year_problems(cover("", line, "", "")) == []


def test_no_sheet_or_no_year_raises_nothing():
    assert trace_check.cover_copy_problems(cover("", "", "", "DERINKUYU, 1963"), {"facts": []}) == []
    assert year_problems(cover("A FORGOTTEN CITY", "18 LEVELS", "UNDERGROUND", "WHO DUG IT?")) == []


def test_the_finished_payload_gate_refuses_a_bare_year_back_to_the_writer():
    problems = trace_check.headline_gate(cover("A FORGOTTEN CITY", "", "", "DERINKUYU, 1963"), DERINKUYU)
    assert any(p.startswith(COVER_PROBLEM) and "1963" in p for p in problems)
    assert trace_check.headline_gate(cover("A FORGOTTEN CITY", "", "", "DERINKUYU · FOUND 1963"),
                                     DERINKUYU) == []


def test_the_draft_gate_sends_a_bare_year_back_with_the_other_cover_problems():
    pkg = SimpleNamespace(thumbnail={"eyebrow": "A FORGOTTEN CITY", "line1": "", "line2": "",
                                     "tagline": "DERINKUYU, 1963"})
    problems = script_engine.cover_problems(pkg, DERINKUYU)
    assert problems and all(p.startswith(COVER_PROBLEM) for p in problems)
    assert script_engine.blocking_problems(problems) == problems   # a cover problem sends the draft back


def test_every_writer_prompt_states_the_year_rule():
    for text in (script_engine.COPY_EDIT_SYSTEM,):
        assert "never bare beside a place" in text
    import inspect
    src = inspect.getsource(script_engine)
    assert src.count("A year says what happened in it, in the facts' word for that year") == 2


# ------------------------------------------------------------------ audience chart: newest on top, always
def reel(day, title=None, ig=100, yt=50, fb=10):
    posted = None if day is None else datetime(2026, 10, day, 12, tzinfo=timezone.utc).isoformat()
    return {"run_id": f"r{day}", "title": title or f"Topic {day}", "posted_at": posted,
            "views": {"instagram": ig, "youtube": yt, "facebook": fb}}


TODAY = datetime(2026, 11, 2, tzinfo=timezone.utc)


def test_rows_passed_oldest_first_are_drawn_newest_first():
    rows = [reel(d, t) for d, t in zip((19, 21, 23, 25, 27, 29), ("Derinkuyu", "Richat", "Gol Gumbaz", "Kailasa",
                                                                    "Hal Saflieni", "Antikythera"))]
    _, info = ac.render(rows, sample=True, today=TODAY)
    assert info["titles"] == ["Antikythera", "Hal Saflieni", "Kailasa", "Gol Gumbaz", "Richat", "Derinkuyu"]
    assert info["dates"] == ["29 OCT", "27 OCT", "25 OCT", "23 OCT", "21 OCT", "19 OCT"]
    # the drawn date text runs top to bottom in the same order
    drawn = [t for t in info["texts"] if t["text"].endswith(ac.DATE_SEP.rstrip()) and "OCT" in t["text"]]
    assert [t["text"].split()[0] for t in sorted(drawn, key=lambda t: t["box"][1])] == ["29", "27", "25", "23",
                                                                                          "21", "19"]
    assert "newest on top" in info["subtitle"]


def test_more_than_six_rows_keep_the_newest_six():
    rows = [reel(d) for d in range(10, 18)]           # oldest first, eight rows
    _, info = ac.render(rows, sample=True, today=TODAY)
    assert info["rows"] == 6 and info["titles"][0] == "Topic 17" and info["titles"][-1] == "Topic 12"


def test_undated_rows_follow_the_dated_ones_in_the_callers_order():
    rows = [reel(None, "Undated A"), reel(20, "Older"), reel(None, "Undated B"), reel(28, "Newer")]
    assert [r["title"] for r in ac.newest_first(rows)] == ["Newer", "Older", "Undated A", "Undated B"]
    bad = dict(reel(None, "Bad date"), posted_at="not a date")
    assert [r["title"] for r in ac.newest_first([bad, reel(20, "Older")])] == ["Older", "Bad date"]


def test_the_sort_is_stable_and_already_newest_first_input_is_unchanged():
    rows = [reel(28, "A"), reel(28, "B"), reel(20, "C")]
    assert [r["title"] for r in ac.newest_first(rows)] == ["A", "B", "C"]
    assert [r["title"] for r in ac.newest_first(list(reversed(rows)))] == ["B", "A", "C"]   # tie keeps caller order
    assert ac.newest_first([]) == []


def test_mixed_naive_aware_and_epoch_dates_sort_together():
    rows = [{"title": "naive", "posted_at": "2026-10-20T12:00:00", "views": {}},
            {"title": "epoch", "posted_at": datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp(), "views": {}},
            {"title": "aware", "posted_at": "2026-10-22T12:00:00Z", "views": {}}]
    assert [r["title"] for r in ac.newest_first(rows)] == ["epoch", "aware", "naive"]


# ------------------------------------------------------------------ round-7 review: one family per event, no stray "-ed"
def one_fact(statement):
    return {"facts": [{"id": "probe_fact", "statement": statement, "disputed": False, "hedge": "", "evidence": []}]}


@pytest.mark.parametrize("statement,line", [
    ("The theatre reopened in 1985.", "SEALED 1985"),
    ("The theatre reopened in 1985.", "CLOSED 1985"),
    ("The library was destroyed by fire in 1900.", "FLOODED 1900"),
    ("The library was destroyed by fire in 1900.", "STOLEN 1900"),
    ("The library was destroyed by fire in 1900.", "COLLAPSED 1900"),
    ("The statue was stolen in 1911.", "DESTROYED 1911"),
    ("The statue was stolen in 1911.", "BURNED 1911"),
    ("The tomb was sealed in 1922.", "OPENED 1922"),
    ("The fort was built in 1650.", "FOUNDED 1650"),
    ("The fort was built in 1650.", "COMPLETED 1650"),
    ("The site was explored in 1890.", "FOUND 1890"),
])
def test_a_word_for_a_different_or_opposite_event_is_refused(statement, line):
    word = line.split()[0]
    problems = year_problems(cover("", "", "", line), one_fact(statement))
    assert len(problems) == 1 and f"puts '{word}' in the year" in problems[0]


@pytest.mark.parametrize("statement,line", [
    ("The theatre reopened in 1985.", "REOPENED 1985"),
    ("The theatre reopened in 1985.", "RESTORED 1985"),
    ("The library was destroyed by fire in 1900.", "DESTROYED 1900"),
    ("The library was destroyed by fire in 1900.", "RAZED 1900"),
    ("The statue was stolen in 1911.", "LOOTED 1911"),
    ("The tomb was sealed in 1922.", "CLOSED 1922"),
    ("The site was unearthed in 1974.", "FOUND 1974"),
    ("The site was unearthed in 1974.", "DISCOVERED 1974"),
])
def test_a_true_near_synonym_of_the_sheets_word_passes(statement, line):
    assert year_problems(cover("", "", "", line), one_fact(statement)) == []


def test_the_families_never_share_a_word_and_discovery_is_the_near_synonym_set():
    fams = trace_check._EVENT_FAMILIES
    assert sum(len(f) for f in fams) == len(trace_check._EVENT_WORDS)      # no word in two families
    assert trace_check._DISCOVERY == {"found", "discovered", "discovery", "rediscovered", "rediscovery",
                                      "uncovered", "unearthed"}
    def family(w):
        return next(f for f in fams if w in f)
    for a, b in (("reopened", "closed"), ("opened", "sealed"), ("destroyed", "burned"), ("destroyed", "stolen"),
                 ("destroyed", "flooded"), ("destroyed", "collapsed"), ("stolen", "flooded"), ("found", "identified"),
                 ("found", "explored")):
        assert family(a) != family(b), (a, b)


@pytest.mark.parametrize("statement,line", [
    ("It was estimated in 1963 that 20,000 people lived there.", "ESTIMATED 1963"),
    ("In 1963 a resident knocked down a wall.", "KNOCKED 1963"),
    ("The population was recorded in 1890 as 4,000.", "RECORDED 1890"),
    ("Archaeologists photographed the walls in 1931 for the first time.", "PHOTOGRAPHED 1931"),
])
def test_an_ed_word_that_is_not_the_sites_event_does_not_pass(statement, line):
    assert year_problems(cover("", "", "", line), one_fact(statement))


def test_an_uncurated_ed_verb_counts_only_right_before_in_year():
    own, _ = trace_check._event_words("It was estimated in 1963 that 20,000 people lived there.", "1963")
    assert own == set()
    own, _ = trace_check._event_words("In 1963 a resident knocked down a wall.", "1963")
    assert own == set()
    own, allowed = trace_check._event_words("The cathedral was consecrated in 1150.", "1150")
    assert "consecrated" in own and "dedicated" in allowed
    own, allowed = trace_check._event_words("The chapel was frescoed in 1480 by a local painter.", "1480")
    assert own == {"frescoed"} and allowed == {"frescoed"}
    assert year_problems(cover("", "", "", "FRESCOED 1480"),
                         one_fact("The chapel was frescoed in 1480 by a local painter.")) == []
    assert year_problems(cover("", "", "", "CHAPEL, 1480"),
                         one_fact("The chapel was frescoed in 1480 by a local painter."))
