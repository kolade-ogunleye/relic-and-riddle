"""QF-141 round 3: an adversarial grid over withhold_figure (force=True), printed as JSON (adversarial.json).

    python adversarial.py [<code root>]

Every punctuation mark (sentence marks, separators, dashes, ellipsis, each bracket and quote pair) x the figure's
place in the field (start, middle, end; touching the mark or spaced from it) x every word class of the QF-141
table (joiners, prepositions that need an object, locatives) on either side of the figure. Each output is checked:
no output opens on a sentence mark, a separator or a closing mark; none ends on a separator or an opening mark;
none holds an empty pair or a pair with a space inside its edge; none ends on a word that needs an object or opens
on a joiner; the withheld figure is never left; and a locative phrase that reads ("BELOW KRAKOW", "HIDDEN BELOW")
is kept. The counts per check are the result (0 everywhere is the pass).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIG = "144 FT"
SINGLE = [".", "?", "!", ",", ";", ":", "…", "—", "–", "-"]
PAIRS = [("(", ")"), ("[", "]"), ("{", "}"), ("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'"),
         ("«", "»")]


def grid(classes: dict[str, list[str]], hedges: set[str]) -> list[tuple[str, str, bool | None]]:
    """(phrase, family, kept): kept True/False when the phrase's class decides it, None when only marks are judged."""
    cases: list[tuple[str, str, bool | None]] = []
    for m in SINGLE:
        for a, b in ((m, " "), (" " + m, " ")):     # touching the figure, or spaced
            cases += [(f"{FIG}{a}{b}NO WAY OUT".replace("  ", " "), "mark-start", True),
                      (f"THE SECRET {FIG}{a}{b}NO WAY OUT".replace("  ", " "), "mark-middle", True),
                      (f"THE SECRET{a}{b}{FIG} NO WAY OUT".replace("  ", " "), "mark-middle", True),
                      (f"THE SECRET{a}{b}{FIG}".replace("  ", " "), "mark-end", True),
                      (f"THE SECRET {m} {FIG} {m} NO WAY OUT", "mark-both", True),
                      (f"THE MINE {FIG} (44 M){m} BELOW KRAKOW", "conversion", True)]
        if m != ".":      # ".144" is a decimal, not a mark and a figure
            cases.append((f"{m}{FIG} BELOW KRAKOW", "mark-before-start", True))
    for o, c in PAIRS:
        cases += [(f"{o}{FIG}{c} BELOW KRAKOW", "pair-start", True),
                  (f"BELOW KRAKOW {o}{FIG}{c}", "pair-end", True),
                  (f"THE MINE {o}{FIG}{c} BELOW KRAKOW", "pair-middle", True),
                  (f"THE MINE {o}{FIG} DEEP{c}", "pair-partial", True),
                  (f"THE MINE {o}DEEP {FIG}{c}", "pair-partial", True),
                  (f"THE MINE {o}SOME {FIG} DOWN{c} BELOW KRAKOW", "pair-partial", True),
                  (f"{o}THE {FIG}{c} BELOW KRAKOW", "pair-article", True),
                  (f"THE MINE {o}{FIG} (44 M){c} BELOW KRAKOW", "pair-conversion", True)]
    for w in classes["joiner"]:
        cases += [(f"{FIG} {w} SOLID SALT", "joiner-after", False),
                  (f"{FIG}, {w} SOLID SALT", "joiner-after-mark", False),
                  (f"({FIG}) {w} SOLID SALT", "joiner-after-pair", False)]
    for w in classes["object"]:
        cases += [(f"CARVED {w} {FIG}", "object-before", False),
                  (f"CARVED {w} {FIG}.", "object-before", False),
                  (f"CARVED {w} THE {FIG}", "object-before", False),
                  (f"CARVED {w}: {FIG}", "object-before-mark", False),
                  (f"CARVED {w} ({FIG})", "object-before-pair", False)]
    for w in classes["locative"]:
        cases += [(f"{FIG} {w} KRAKOW", "locative-after", True),
                  (f"HIDDEN {FIG} {w}", "locative-end", True),
                  # a locative that is also a figure's hedge ("AROUND 144 FT") goes with the figure
                  (f"HIDDEN {w} {FIG}", "locative-before", None if w in hedges else True),
                  (f"({FIG}) {w} KRAKOW", "locative-pair", True),
                  (f"HIDDEN {w} — {FIG}", "locative-mark", True)]
    return cases


def main(argv: list[str]) -> int:
    code = Path(argv[0]).resolve() if argv else HERE.parents[3]
    sys.path.insert(0, str(code))
    from src import cover_design as cd
    classes = {"joiner": sorted(cd._JOINERS), "object": sorted(cd._OBJECT_PREPOSITIONS - cd._JOINERS),
               "locative": sorted(cd._LOCATIVES)}
    opens_bad = "".join(SINGLE) + "".join(c for _, c in PAIRS if c not in "\"'")
    ends_bad = ",;:…—–-" + "".join(o for o, _ in PAIRS if o not in "\"'")
    checks = {k: [] for k in ("opens_on_mark", "ends_on_mark", "empty_pair", "space_inside_pair", "ends_needing_object",
                             "opens_on_joiner", "figure_left", "kept_wrongly_dropped", "dropped_wrongly_kept")}
    rows = grid(classes, {q for q in cd.QUALIFIERS if " " not in q})
    for text, family, kept in rows:
        out = cd.withhold_figure({"tagline": text}, {"number": "144", "unit": "FT"}, force=True)["tagline"]
        words = re.findall(r"[^\W_]+(?:'[^\W_]+)*", out.upper())
        hit = {
            "opens_on_mark": bool(out) and out[0] in opens_bad,
            "ends_on_mark": bool(out) and out[-1] in ends_bad,
            "empty_pair": any(re.search(re.escape(o) + r"\s*" + re.escape(c), out) for o, c in PAIRS
                              if o not in "\"'") or bool(re.search(r"(?<!\w)([\"'])\s*\1(?!\w)", out)),
            "space_inside_pair": any(re.search(re.escape(o) + r"\s|\s" + re.escape(c), out) for o, c in PAIRS
                                     if o not in "\"'"),
            "ends_needing_object": bool(words) and words[-1] in cd._NEEDS_OBJECT,
            "opens_on_joiner": bool(words) and words[0] in cd._JOINERS,
            "figure_left": "144" in out or "44 M" in out,     # the figure, or the same figure in metres
            "kept_wrongly_dropped": kept is True and not out,
            "dropped_wrongly_kept": kept is False and bool(out),
        }
        for k, v in hit.items():
            if v:
                checks[k].append([text, out])
    result = {"cases": len(rows), "classes": {k: len(v) for k, v in classes.items()},
              "failures": {k: len(v) for k, v in checks.items()},
              "examples": {k: v[:5] for k, v in checks.items() if v}}
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
