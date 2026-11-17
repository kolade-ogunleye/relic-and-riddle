"""QF-141: a withheld hero figure never leaves a fragment of a cover field.

Round 4 (2026-11-02) widened withhold_figure from a ranged or disputed figure to an estimate whose hedge cannot be
shown (r7). It lifts the figure out of every line that states it and kept whatever was left, so the Wieliczka
cover's tagline "327 METERS DOWN" (the sheet hedges 327 m, the copy has no qualifier) was drawn as "DOWN" alone,
and its badge "327.0 M RECORD" kept "RECORD". The figure stays withheld (operator rule); what is left of its phrase
is drawn only when it still reads on its own.

The oracle here is independent of cover_design's word lists: a field the withhold changed is either empty or has
at least two words, at least one of them a word that is not a function, unit, direction or hedge word, and does
not end on a function word. Invariants run over the repository's real script-library payloads and fact sheets
(the catalogue), a set of figure phrasings, and the drawn Wieliczka cover's own plates.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from src.cover_design import figure_check, must_withhold, render_cover, withhold_figure
from src.still_layout import Layout
from src.visual_engine import _in_spoken_units, cover_figure, extract_curiosity_badge

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "data" / "script_library"
FIELDS = ("eyebrow", "line1", "line2", "tagline", "badge")
FUNCTION = {"THE", "A", "AN", "OF", "IN", "ON", "AT", "TO", "FROM", "BY", "FOR", "WITH", "THAN", "AND", "OR", "AS"}
NOT_CONTENT = FUNCTION | {
    "DOWN", "UP", "DEEP", "HIGH", "TALL", "WIDE", "LONG", "ACROSS", "BELOW", "ABOVE", "OLD", "AWAY", "AROUND",
    "METERS", "METRES", "M", "FT", "FEET", "FOOT", "MILES", "MILE", "KM", "TONS", "TONNES", "ESTIMATED", "EST",
    "ABOUT", "ALMOST", "NEARLY", "RECORD", "OVER", "UNDER",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Z0-9']+", str(text or "").upper())


def _reads_alone(text: str) -> bool:
    words = _words(text)
    return (len(words) >= 2 and words[-1] not in FUNCTION
            and any(w not in NOT_CONTENT and not w.isdigit() for w in words))


def _copy(payload: dict) -> dict:
    copy = dict(payload.get("thumbnail") or {})
    copy.setdefault("badge", extract_curiosity_badge(payload))
    return _in_spoken_units(copy, payload)


def _library() -> list[tuple[str, dict]]:
    out = []
    for p in sorted(LIBRARY.glob("*/verified_script.json")):
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("thumbnail"):
            out.append((p.parent.name, payload))
    return out


def _fragments(before: dict, after: dict) -> dict:
    return {k: after.get(k) for k in FIELDS
            if str(after.get(k) or "") != str(before.get(k) or "") and str(after.get(k) or "").strip()
            and not _reads_alone(after[k])}


def test_no_catalogue_cover_field_is_left_as_a_fragment_by_the_withhold() -> None:
    cases = _library()
    assert cases, "the script library has no payload with cover copy"
    withheld = 0
    for topic, payload in cases:
        copy = _copy(payload)
        figure = cover_figure(copy, payload)
        if not must_withhold(figure):
            assert withhold_figure(copy, figure) == copy, topic          # no withheld figure: the copy as written
            continue
        withheld += 1
        out = withhold_figure(copy, figure)
        assert not _fragments(copy, out), (topic, _fragments(copy, out))
        number = str(figure["number"])
        assert not any(number in str(out.get(k) or "") for k in FIELDS), (topic, out)   # still withheld
    assert withheld, "no catalogue cover withholds a figure: the invariant checked nothing"


def test_wieliczka_keeps_its_figure_withheld_and_draws_no_stray_word() -> None:
    payload = json.loads((LIBRARY / "wieliczka_salt_mine" / "verified_script.json").read_text(encoding="utf-8"))
    copy = _copy(payload)
    figure = cover_figure(copy, payload)
    assert must_withhold(figure) and float(figure["number"]) == 327      # the rule still withholds it
    lay = Layout("cover", (1080, 1920), (60, 420, 1020, 1500))
    photo = Image.new("RGBA", (1600, 1200), (120, 110, 95, 255))
    render_cover(photo.resize((1080, 1920)), copy, "badge", lay=lay, source=photo, figure=figure)
    drawn = [p.text for plates in lay.meta.get("plates", {}).values() for p in plates if not p.empty]
    assert drawn and not any("327" in t for t in drawn), drawn
    # round 7 (F1 / F21): the title lockup is set by the heading engine, which moves line 1's closing preposition to
    # open line 2 (CARVED FROM / SOLID SALT -> CARVED / FROM SOLID SALT, the panel's ruled fix) and draws a line in
    # colour runs: the lockup is judged whole -- its words, in order, are the verified line 1 and line 2, nothing cut
    titles = [p.text for n in ("title_1", "title_2") for p in lay.meta.get("plates", {}).get(n, []) if not p.empty]
    authored = " ".join(str(copy.get(k) or "") for k in ("line1", "line2")).upper().split()
    assert not titles or " ".join(titles).split() == [w for w in authored if w], (titles, authored)
    drawn = [p.text for n, plates in lay.meta.get("plates", {}).items() if n not in ("title_1", "title_2")
             for p in plates if not p.empty]
    for text in drawn:
        if text.strip().upper() not in {v.strip().upper() for v in copy.values() if isinstance(v, str)}:
            assert _reads_alone(text), (text, drawn)                     # a changed line reads on its own
    assert "DOWN" not in [t.strip().upper() for t in drawn], drawn
    record = lay.meta["figure_withheld"]
    assert float(record["number"]) == 327 and "tagline" in record.get("dropped", []), record


def test_a_field_that_still_reads_is_kept_and_a_fragment_never_is() -> None:
    figure = {"number": "144", "unit": "FT"}
    kept = {"144 FT BELOW KRAKOW": "BELOW KRAKOW", "HIDDEN 144 FT BELOW": "HIDDEN BELOW",
            "144 FT ACROSS THE SAHARA": "ACROSS THE SAHARA", "144 FT. NO WAY OUT": "NO WAY OUT",
            "144 FT? THINK AGAIN": "THINK AGAIN", "THE SECRET 144 FT. NO WAY OUT": "THE SECRET. NO WAY OUT"}
    for text, want in kept.items():
        assert withhold_figure({"tagline": text}, figure, force=True)["tagline"] == want, text
    phrasings = {
        "144 FT DOWN": False, "DOWN 144 FT": False, "144 FT RECORD": False, "DEEPER THAN 144 FT": False,
        "144 FT OF SOLID SALT": False, "THE 144-FOOT": False, "ESTIMATED 144 FT": False, "144 FT DEEP": False,
        "THE 144-FOOT WHISPERING DOME": True, "144-FOOT WHISPERING DOME": True, "THE 144-FOOT EYE OF SAHARA": True,
        "CARVED 144 FT INTO SOLID ROCK": True,
        # round 2 (review 1, B1): a locative or adverbial preposition with its object, or after a content word,
        # still reads; a joiner or a word that needs an object does not
        "144 FT BELOW KRAKOW": True, "144 FT UNDER POLAND": True, "144 FT ACROSS THE SAHARA": True,
        "HIDDEN 144 FT BELOW": True, "144 FT ON THE MAP": True, "CARVED FROM 144 FT": False,
        "144 FT - AND STILL SINKING": False, "144 FT THAN ANY CATHEDRAL": False,
        # B2: a sentence mark the cut leaves at an edge is not drawn
        "144 FT. NO WAY OUT": True, "144 FT. STILL A MYSTERY": True, "144 FT? THINK AGAIN": True,
        "THE SECRET 144 FT. NO WAY OUT": True,
    }
    for text, keeps in phrasings.items():
        for field in FIELDS:
            copy = {k: "SOLID SALT" for k in FIELDS}
            copy[field] = text
            out = withhold_figure(copy, figure, force=True)
            assert "144" not in out[field], (text, out[field])
            assert not _fragments(copy, out), (text, field, out[field])
            assert bool(out[field]) == keeps, (text, field, out[field])
            assert not re.match(r"[\s.?!,;:…–—-]", out[field]), (text, out[field])   # no mark opens it
            assert not re.search(r"\s[.?!,;:]", out[field]), (text, out[field])   # no mark stands apart
            assert {k: v for k, v in out.items() if k != field} == {k: v for k, v in copy.items() if k != field}


def test_richat_withheld_range_stays_withheld() -> None:
    sheet_file = ROOT / "data" / "fact_sheets" / "richat_structure_sahara_eye.json"
    sheet = json.loads(sheet_file.read_text(encoding="utf-8"))
    copy = {"eyebrow": "DESERT ANOMALY", "line1": "THE 25-MILE", "line2": "SAHARA EYE", "tagline": "CARVED BY NATURE"}
    figure = figure_check(copy, sheet)
    assert must_withhold(figure)
    out = withhold_figure(copy, figure)
    assert out["line1"] == "" and out["line2"] == "SAHARA EYE" and out["tagline"] == "CARVED BY NATURE"
    assert not any("25" in str(v) for v in out.values())


# Round 3: the word classes, as the README tables them (written here independently of cover_design's lists).
JOINERS = ["OF", "THAN", "AND", "OR", "BUT", "NOR", "AS", "THAT", "WHICH", "WHO", "WHOM", "WHOSE", "IS", "ARE",
           "WAS", "WERE", "BE", "BEEN"]
NEED_OBJECT = ["OF", "TO", "FROM", "WITH", "AT", "BY", "FOR", "IN", "INTO", "ONTO", "UPON", "PER", "VIA", "AMONG",
               "AMONGST", "BETWEEN", "AMID", "AMIDST", "ATOP", "DURING", "DESPITE", "LIKE", "UNLIKE", "EXCEPT",
               "AGAINST", "TOWARD", "TOWARDS", "UNTIL", "TILL", "THAN", "UNTO", "VERSUS", "VS", "CONCERNING",
               "REGARDING", "INCLUDING"]
LOCATIVES = ["ON", "OVER", "UNDER", "BELOW", "ABOVE", "BENEATH", "UNDERNEATH", "ACROSS", "THROUGH", "AROUND",
             "BEYOND", "WITHIN", "WITHOUT", "ALONG", "PAST", "NEAR", "BEHIND", "INSIDE", "OUTSIDE", "BEFORE", "AFTER",
             "SINCE", "OPPOSITE", "ALONGSIDE", "ABOARD", "UP", "DOWN", "OFF", "OUT", "AWAY", "THROUGHOUT",
             "UNDERGROUND", "AHEAD"]
HEDGES = {"OVER", "AROUND", "ABOUT", "ALMOST", "NEARLY", "SOME", "ROUGHLY", "CIRCA"}   # lifted with the figure
PAIRS = [("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"),
         ("«", "»")]
MARKS = [".", "?", "!", ",", ";", ":", "…", "—", "–", "-"]
FIG = {"number": "144", "unit": "FT"}


def _cut(text: str) -> str:
    return withhold_figure({"tagline": text}, FIG, force=True)["tagline"]


def _clean(out: str) -> list[str]:
    """What is wrong with a remainder's marks: an opening mark, a trailing separator, an empty or loose pair."""
    bad = []
    if out[:1] and out[:1] in ".?!,;:…—–-)]}”’»":
        bad.append("opens on a mark")
    if out[-1:] and out[-1:] in ",;:…—–-([{“‘«":
        bad.append("ends on a mark")
    for o, c in PAIRS:
        if o not in "\"'" and (re.search(re.escape(o) + r"\s*" + re.escape(c), out)
                               or re.search(re.escape(o) + r"\s|\s" + re.escape(c), out)):
            bad.append("empty or loose pair")
    if re.search(r"(?<!\w)([\"'])\s*\1(?!\w)", out):
        bad.append("empty quotes")
    return bad


def test_every_word_class_is_judged_from_its_table_in_both_directions() -> None:
    for w in JOINERS:
        assert _cut(f"144 FT {w} SOLID SALT") == "", w                    # opens on what joined the figure
    for w in NEED_OBJECT:
        assert _cut(f"CARVED {w} 144 FT") == "", w                         # ends on a word missing its object
        assert _cut(f"CARVED {w} THE 144 FT.") == "", w
    for w in LOCATIVES:
        assert _cut(f"144 FT {w} KRAKOW") == f"{w} KRAKOW", w              # a locative with its object stands
        assert _cut(f"HIDDEN 144 FT {w}") == f"HIDDEN {w}", w              # an adverb after a content word stands
        if w not in HEDGES:
            assert _cut(f"HIDDEN {w} 144 FT") == f"HIDDEN {w}", w


def test_no_mark_is_left_at_a_cut_edge() -> None:
    expect = {
        "(144 FT) BELOW KRAKOW": "BELOW KRAKOW", "BELOW KRAKOW (144 FT)": "BELOW KRAKOW",
        "THE MINE (144 FT)": "THE MINE", "THE MINE (144 FT DEEP)": "THE MINE (DEEP)",
        "THE MINE (DEEP 144 FT)": "THE MINE (DEEP)", "“144 FT” BELOW KRAKOW": "BELOW KRAKOW",
        "'144 FT' UNDER POLAND": "UNDER POLAND", '"144 FT" UNDER POLAND': "UNDER POLAND",
        "THE DEEPEST — 144 FT": "THE DEEPEST", "THE DEEPEST – 144 FT": "THE DEEPEST",
        "THE DEEPEST… 144 FT": "THE DEEPEST", "(THE 144 FT) BELOW KRAKOW": "BELOW KRAKOW",
        "…144 FT BELOW KRAKOW": "BELOW KRAKOW", "SALT, 144 FT, DOWN THERE": "SALT, DOWN THERE",
        "THE MINE — 144 FT — BELOW KRAKOW": "THE MINE — BELOW KRAKOW",
        "THE 144-FOOT (44 M) SHAFT DOWN": "THE SHAFT DOWN", "ROME'S '144 FT' SHAFT": "ROME'S SHAFT",
        "MAURITANIA’S 144 FT RINGS": "MAURITANIA’S RINGS", "THE MINE, 144 FT.": "THE MINE.",
    }
    for text, want in expect.items():
        assert _cut(text) == want, (text, _cut(text))
    for m in MARKS:
        for text in (f"144 FT{m} NO WAY OUT", f"THE SECRET {m} 144 FT", f"THE SECRET{m} 144 FT",
                     f"THE SECRET {m} 144 FT {m} NO WAY OUT", f"THE SECRET 144 FT{m} NO WAY OUT"):
            out = _cut(text)
            assert out and not _clean(out) and "144" not in out, (text, out, _clean(out))
    for o, c in PAIRS:
        for text in (f"{o}144 FT{c} BELOW KRAKOW", f"BELOW KRAKOW {o}144 FT{c}", f"THE MINE {o}144 FT DEEP{c}",
                     f"THE MINE {o}DEEP 144 FT{c}", f"{o}THE 144 FT{c} BELOW KRAKOW"):
            out = _cut(text)
            assert out and not _clean(out) and "144" not in out, (text, out, _clean(out))
