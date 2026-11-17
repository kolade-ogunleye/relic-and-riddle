"""QF-143: a YouTube thumbnail headline never ends on a mark that leads into a line not drawn (":"), nor on a dangling
fragment, and a line drawn without its partner stands alone whole (QF-23: never cut).

The dev Richat documentary (run_richat_structure_sahara_eye_1792740961_long) has thumbnail copy line1 "THE RICHAT
STRUCTURE:" and line2 "THE EYE OF THE SAHARA". Line 2 is over the three-word line budget (QF-23, option 1), so it is
left out whole; the thumbnail then ended on "THE RICHAT STRUCTURE:".
"""
import re

import pytest
from PIL import Image

from src import youtube_thumbnail

LEAD = ":;,&/+-–—"
HEADLINE = ("title", "title_1", "title_2")
# The test's own oracle, written out (not the code's predicate): words a lone headline line cannot end on
END_WORDS = {"THE", "A", "AN", "ITS", "THEIR", "OF", "TO", "FROM", "WITH", "AT", "BY", "FOR", "INTO", "THAN", "AND",
             "OR", "BUT", "NOR"}


def _photo() -> Image.Image:
    return Image.new("RGB", (1600, 1200), (196, 168, 122))


def _headline(plates) -> list[str]:
    return [p.text for n, p in plates if n in HEADLINE]


def _check(copy: dict, plates, meta: dict) -> None:
    lines = _headline(plates)
    drawn = [p.text for n, p in plates if n in HEADLINE + ("kicker",)]
    if not lines:
        assert meta.get("headline_dropped"), "no headline drawn and none recorded"
        return
    last = lines[-1]
    assert last[-1] not in LEAD, f"the headline ends on a leading mark: {last!r}"
    words = re.findall(r"[^\W_]+(?:'[^\W_]+)*", last.upper())
    if last[-1] not in "?!.…":
        assert words[-1] not in END_WORDS, f"the headline ends on a dangling word: {last!r}"
        if len(lines) == 1:
            assert words[0] not in {"OF", "THAN"}, f"a lone line opens on a joiner: {last!r}"
            assert not (words[0] in {"AND", "OR", "BUT", "NOR"} and words[1:2] in (["THE"], ["A"], ["AN"])),                 f"a lone line carries on a list: {last!r}"
    # never cut (QF-23): each drawn line is a whole verified field, at most without its trailing mark
    fields = [" ".join(str(copy.get(k) or "").upper().split()) for k in ("eyebrow", "line1", "line2", "tagline")]
    for t in drawn:
        t = t.replace("…", "...").replace("’", "'")
        assert any(t == f or (f.startswith(t) and f[len(t):].strip() and not f[len(t):].strip(LEAD + " "))
                   for f in fields if f), f"{t!r} is not a whole verified line"


def test_the_richat_documentary_headline_does_not_end_on_its_colon() -> None:
    copy = {"eyebrow": "DOCUMENTARY", "line1": "THE RICHAT STRUCTURE:", "line2": "THE EYE OF THE SAHARA",
            "tagline": "EXPLORE THE EVIDENCE"}
    meta: dict = {}
    _, plates = youtube_thumbnail.render(
        _photo(), copy, meta=meta,
        youtube_title="The Richat Structure: Geological Evidence and Scientific Origins")
    assert _headline(plates) == ["THE RICHAT STRUCTURE"]
    assert meta["headline_lines_dropped"] == ["line2"]   # over the budget: left out whole, as before
    _check(copy, plates, meta)


CASES = [
    # (line1, line2, eyebrow, tagline): the no-figure path
    ("THE RICHAT STRUCTURE:", "THE EYE OF THE SAHARA", "DOCUMENTARY", "EXPLORE THE EVIDENCE"),
    ("SECRETS OF THE", "LOST DESERT CITY FOUND", "DESERT RUINS", "WHAT LIES BENEATH"),
    ("THE LOST CITY OF EL DORADO", "OF THE ANDES", "LOST CITY", "STILL UNFOUND"),
    ("WHO BUILT", "THE WALLS —", "STONE PUZZLE", "NO ONE KNOWS"),
    ("GUESS WHO?", "A MUCH LONGER SECOND LINE HERE", "ANCIENT RIDDLE", "THE ANSWER"),
    ("CARVED FROM", "A SINGLE MOUNTAIN OF ROCK", "CARVED FROM", "BUILT TO LAST"),
    ("SEALED FOR CENTURIES;", "", "THE TOMB", "OPENED AT LAST"),
    # the year path (a year in the tagline is the hero; the payoff line, line2, under it)
    ("A FAR TOO LONG FIRST LINE", "THE SAHARA'S EYE:", "DESERT EYE", "FOUND IN 1965"),
    ("A FAR TOO LONG FIRST LINE", "HIDDEN BENEATH THE", "BURIED CITY", "FOUND IN 1963"),
]


@pytest.mark.parametrize("line1,line2,eyebrow,tagline", CASES)
def test_a_drawn_headline_stands_alone(line1, line2, eyebrow, tagline) -> None:
    copy = {"eyebrow": eyebrow, "line1": line1, "line2": line2, "tagline": tagline}
    meta: dict = {}
    _, plates = youtube_thumbnail.render(_photo(), copy, meta=meta)
    _check(copy, plates, meta)


def test_a_line_left_out_as_a_fragment_is_on_the_review_card() -> None:
    meta: dict = {}
    copy = {"eyebrow": "DESERT RUINS", "line1": "SECRETS OF THE", "line2": "LOST DESERT CITY FOUND",
            "tagline": "WHAT LIES BENEATH"}
    _, plates = youtube_thumbnail.render(_photo(), copy, meta=meta)
    assert "SECRETS OF THE" not in _headline(plates)
    assert youtube_thumbnail.headline_note(meta)


def test_a_line_that_stands_alone_is_drawn_unchanged() -> None:
    copy = {"line1": "BEHIND ONE WALL...", "line2": "A FORGOTTEN CITY"}
    _, plates = youtube_thumbnail.render(_photo(), copy)
    assert _headline(plates) == ["BEHIND ONE WALL…", "A FORGOTTEN CITY"]
    _, plates = youtube_thumbnail.render(_photo(), {"line1": "THE RICHAT STRUCTURE:", "line2": "SAHARA EYE"})
    assert _headline(plates) == ["THE RICHAT STRUCTURE:", "SAHARA EYE"]   # the colon leads into a drawn line


# Round 2 (review 1, finding 1): whole lines a writer chose, labelled by hand (the oracle is this table, not the
# code's fragment check). Each is line 1, with a five-word line 2 the budget leaves out, so it is drawn alone.
STANDS = ["HERE IT IS", "WHAT IT WAS", "LOCKED IN", "AND THEN NONE", "BUT NOBODY KNEW", "GUESS WHO", "SEALED WITHIN",
          "THE SAHARA'S EYE", "AND IT VANISHED"]
DANGLES = ["SECRETS OF THE", "CARVED FROM", "BUILT BY", "OF THE ANDES", "AND THE SPHINX", "HIDDEN BENEATH THE",
           "FASTER THAN", "SALT AND"]


@pytest.mark.parametrize("line,stands", [(t, True) for t in STANDS] + [(t, False) for t in DANGLES])
def test_a_whole_authored_line_stands_alone_when_it_reads_whole(line, stands) -> None:
    copy = {"eyebrow": "STONE PUZZLE", "line1": line, "line2": "A MUCH LONGER SECOND LINE HERE",
            "tagline": "NO ONE KNOWS"}
    meta: dict = {}
    _, plates = youtube_thumbnail.render(_photo(), copy, meta=meta)
    texts = {n: p.text.replace("’", "'") for n, p in plates}
    if stands:
        assert texts.get("title_1") == line, f"{line!r} is a whole line and must be drawn"
        assert texts.get("kicker") == "STONE PUZZLE"   # the kicker over it is kept
        assert "headline_fragment" not in meta
    else:
        assert line not in texts.values(), f"{line!r} dangles alone and must not be drawn"
        assert meta.get("headline_fragment") == ["line1"]
        note = youtube_thumbnail.headline_note(meta)
        assert "does not stand alone" in note and "budget" not in note   # finding 2: the true reason
