# QF-141: a withheld figure never leaves a fragment of a cover field

Defect (Claude review of QF-136, D2): on the tip the Wieliczka cover (catalogue topic `wieliczka_salt_mine`) drew
its tagline "327 METERS DOWN" as "DOWN" alone. The fact sheet hedges 327 m and the copy carries no qualifier, so
the figure is withheld by design (r7, operator default: an estimate whose hedge cannot be shown legibly is shown
nowhere). The withhold lifted the figure out of the line and kept the rest, whatever it was. A production run of
2026-11-01 drew the full tagline on the renderer of that day.

## Where it came in

`git log -S "def must_withhold"` / `-S hedge_fits` on `src/cover_design.py`: 9619fb76 (round 4, 2026-11-02)
widened `withhold_figure` from a ranged or disputed figure (`conflict`, round 3, 7213e7fb) to an estimate whose
hedge cannot be shown (`must_withhold`). `_without` strips only a dangling article, so a title phrase such as
"THE 25-MILE" empties, but a figure inside a longer phrase leaves its stray word; round 4 brought such phrases under
the withhold ("327 METERS DOWN", "327.0 M RECORD", "ESTIMATED 6 TONNES"). The production run (2026-11-01) predates
9619fb76.

## Fix (src/cover_design.py, src/youtube_thumbnail.py)

`withhold_figure` keeps the rule (which figures are withheld is unchanged) and, for each field it cut a figure out
of, draws the rest only when it reads on its own (`withheld_fragment`); a fragment becomes an empty field, which
every style already handles (Richat's emptied line1). No word is added: the operator's rulings document no
no-figure form for a tagline or badge, so the field is dropped (open question below). `figure_withheld` lists the fields left
empty (`dropped`): on disk in the YouTube thumbnail's qa.json; the cover's is in its layout's meta only (still_layout
does not persist the cover meta, as before). Topic-agnostic: no topic, figure
or word of a real copy is named in the code.

Fragment, measured on the remainder's words (letters, digits, apostrophes), any of:

1. fewer than two words: the word belonged to the figure's phrase and is a stray label alone ("DOWN", "RECORD",
   "ESTIMATED"); in every real case measured, every one-word remainder is of this kind;
2. no content word: every word an article, preposition, conjunction, unit (`typography._unit`), extent, direction
   or hedge word ("METERS DEEP", "UP TO THE");
3. the word that joined or governed the figure left dangling: it opens on a joiner (OF, THAN, a conjunction, a
   relative word or a copula: "OF SOLID SALT") or ends on a word that needs an object (an article, a joiner, TO,
   FROM, WITH, AT, BY, IN, FOR, INTO...: "DEEPER THAN", "CARVED FROM"; every word is tabled below).

A remainder that still reads stays: "THE 25-MILE EYE" -> "THE EYE" (over "SEEN FROM SPACE"), "144-FOOT WHISPERING
DOME" -> "WHISPERING DOME"; a locative or adverbial preposition stands at either end ("144 FT BELOW KRAKOW" ->
"BELOW KRAKOW", "25 MILES ACROSS THE SAHARA" -> "ACROSS THE SAHARA", "HIDDEN 144 FT BELOW" -> "HIDDEN BELOW"); an
opening article or possessive stands.

### Word classes (cover_design: _JOINERS, _OBJECT_PREPOSITIONS, _LOCATIVES)

| class | words | rule | why |
|---|---|---|---|
| joiner | OF, THAN, AND, OR, BUT, NOR, AS, THAT, WHICH, WHO, WHOM, WHOSE, IS, ARE, WAS, WERE, BE, BEEN | a remainder may not open or end on one | each joins what came before it (the figure) to what follows; with the figure gone it joins nothing ("OF SOLID SALT", "THAT CHANGED HISTORY", "DEEPER THAN") |
| preposition that needs an object | OF, TO, FROM, WITH, AT, BY, FOR, IN, INTO, ONTO, UPON, PER, VIA, AMONG, AMONGST, BETWEEN, AMID, AMIDST, ATOP, DURING, DESPITE, LIKE, UNLIKE, EXCEPT, AGAINST, TOWARD, TOWARDS, UNTIL, TILL, THAN, UNTO, VERSUS, VS, CONCERNING, REGARDING, INCLUDING | a remainder may not end on one | none is used as a bare adverb in a headline, so at the end of a remainder its object is the cut figure ("CARVED BY", "CARVED FROM", "BURIED IN"). IN can be an adverb ("CAVED IN"), but after a cut on a cover line it has lost its object far more often (review 2): it is in this class, and a copy that ends on an adverbial IN loses that field |
| locative / adverbial preposition | ON, OVER, UNDER, BELOW, ABOVE, BENEATH, UNDERNEATH, ACROSS, THROUGH, AROUND, BEYOND, WITHIN, WITHOUT, ALONG, PAST, NEAR, BEHIND, INSIDE, OUTSIDE, BEFORE, AFTER, SINCE, OPPOSITE, ALONGSIDE, ABOARD, UP, DOWN, OFF, OUT, AWAY, THROUGHOUT, UNDERGROUND, AHEAD | stands at either end | each also stands alone as an adverb ("HIDDEN BELOW", "SEALED INSIDE") or governs the words after it ("BELOW KRAKOW"); alone in a remainder it is still a fragment by rules 1-2 ("DOWN") |
| article / possessive | THE, A, AN, ITS, THEIR | may open, may not end | "THE EYE" reads; "BELOW THE" does not |

A hedge that is also a locative (OVER, AROUND...) is the figure's qualifier when it stands right before the figure
and is lifted with it, as before ("HIDDEN AROUND 144 FT" -> "HIDDEN" -> dropped).

### Marks at the cut (`_without`, rounds 2-3)

What the cut leaves is tidied at its edge, never elsewhere in the field:
- a pair of brackets or quotes ( ) [ ] { } “ ” ‘ ’ « » " " ' ' that the cut empties goes with its inner space ("THE
  MINE (144 FT)" -> "THE MINE", "“144 FT” BELOW KRAKOW" -> "BELOW KRAKOW"); one it leaves words in closes up at the
  cut ("THE MINE (144 FT DEEP)" -> "THE MINE (DEEP)"); an article inside it that was the figure's goes too ("(THE
  144 FT)"); a quote counts as the figure's only when it touches the figure ("ROME'S", "MINERS'" stay);
- the figure opened the field: the marks around it open nothing ("144 FT. NO WAY OUT" -> "NO WAY OUT",
  "…144 FT BELOW KRAKOW" -> "BELOW KRAKOW");
- the figure ended the field: the mark that introduced it ends nothing ("THE DEEPEST — 144 FT" -> "THE DEEPEST",
  also – … : ; , -); a closing sentence mark stays ("THE MINE, 144 FT." -> "THE MINE.");
- mid-field: a mark that followed the figure closes the words before it ("THE SECRET 144 FT. NO WAY OUT" -> "THE
  SECRET. NO WAY OUT"); two separators the cut brings together are one ("A — 144 FT — B" -> "A — B", "SALT, 144 FT,
  DOWN THERE" -> "SALT, DOWN THERE");
- the same figure in another unit in brackets right after it goes with it ("THE 144-FOOT (44 M) SHAFT" -> "THE
  SHAFT"; on the base the converted figure stayed).

### Adversarial grid (adversarial.py -> adversarial.json)

582 generated fields: every mark (. ? ! , ; : … — – -) touching or spaced from the figure at the start, middle and
end, every pair around the figure (empty, partial, with an article, with a conversion), and every word of the three
classes before and after the figure (with and without a mark or pair). Outputs opening on a mark, ending on a mark,
holding an empty or loose pair, ending on a word that needs an object, opening on a joiner, or still holding the
figure (or its conversion): 0 each; locative phrases dropped: 0; joiner/object phrases kept: 0.

## Round 2 (review 1)

B1: round 1 treated every preposition as dangling at either end, so readable remainders the base drew were emptied
("144 FT BELOW KRAKOW", "144 FT UNDER POLAND", "25 MILES ACROSS THE SAHARA", "HIDDEN 144 FT BELOW", "144 FT ON THE
MAP"). Now only joiners open a fragment and only words that need an object end one (rule 3). B2: the stray leading
mark above. Every phrasing is in the test, kept and dropped. No real case changed: the measurements below are the
same as round 1's, and the renders of this tree are byte-identical to round 1's.

## Round 3 (review 2)

B3: marks at a cut edge (empty brackets and quotes, a trailing dash or ellipsis, a bracket with a space inside) are
tidied at the cut, as tabled above. B4: the object-needing class is a full audit (BY and IN added, 36 words), tabled,
and the test generates its phrasings from its own copy of the table in both directions. The grid above is the
round's own adversarial sweep. Real data: the text sweep is identical to round 2's and all 93 rendered files are
byte-identical to round 2's.

## Measurements (measure.py; base 726da5b2 -> this commit)

Text sweep, every cover copy source (77 cases: 5 catalogue fixture covers, 4 script-library payloads, 34 dev runs
with a verified script, 34 production-sample runs counted only), copy built as `generate_thumbnails` builds it:

| | before | after |
|---|---|---|
| cases with a withheld figure | 9 | 9 |
| withheld figure absent from every drawn field | 9 of 9 | 9 of 9 |
| fields the withhold changed | 14 | 14 |
| fields left as a fragment | 11 | 0 |
| of them in a drawn field (eyebrow, line1, line2, tagline) | 6 | 0 |
| of them in the badge (read for a qualifier, drawn on no surface) | 5 | 0 |
| of them catalogue / library / dev / production | 2 / 2 / 3 / 4 | 0 / 0 / 0 / 0 |
| fields left empty | 2 | 13 |

The fragment counts use the fix's own predicate (`withheld_fragment`) on both sides, so "after = 0" holds by
construction; the evidence is the list of changed fields in comparison.json, each of which reads as stated. The 11
before: Wieliczka tagline "DOWN" (5: catalogue, script library, the dev run, 2 production runs) and badge "RECORD"
(the same 5; the badge is drawn on no surface), and a dev Amber Room run's line1 "ESTIMATED" (1). Drawn-field
fragments: 6 -> 0 in the text sweep, 2 -> 0 in the renders. Richat's "THE 25-MILE" stays withheld (line1 empty, as
before; operator round-3 rule).

Renders (render_covers.py, the base and this tree, Discovery style, the run's verified hero): 31 cases (the 5
catalogue covers, 26 dev runs of the covers survey), 0 errors either side; each case the cover, its grid tile and
the YouTube thumbnail:

- covers without a withheld figure: 26 of 26 byte- and pixel-identical (all three files);
- with a withheld figure: 5; the 3 Richat covers identical; the 2 Wieliczka covers (catalogue, dev run
  1792974672) differ: drawn tagline "DOWN" -> no tagline; their YouTube thumbnails identical (the thumbnail does
  not draw the tagline);
- fields drawn as a fragment (record's grid_proof texts): 2 -> 0.

`wieliczka_cover_before_after.jpg`: the catalogue Wieliczka cover, base and fix, half size.

Not rendered: 8 dev runs whose hero is not verified (covers survey) are in the text sweep only; among them the Amber
Room run whose line1 "ESTIMATED" would have been a title line alone.

Files: `before.json`, `after.json` (= `python measure.py` on this tree), `comparison.json` (every changed field per
case, production as counts; per render case the identity of each file and the drawn fields).

## Open questions (not decided here)

1. A dropped tagline leaves Wieliczka's cover with three lines. Should a tagline the withhold empties fall back to
   another verified line (the payload's sub-theme, a carousel heading), or stay empty? Not covered by a ruling;
   nothing is invented.
2. "ESTIMATED" is a hedge word of the sheets (`_HEDGE_WORDS`) but not a copy qualifier (`QUALIFIERS`), so
   "ESTIMATED 6 TONNES" counts as unqualified and is withheld. Should the copy's own "ESTIMATED" count as the
   figure's qualifier (the figure would then stand with its hedge)?
3. Out of scope, seen while measuring: the dev Richat run 1792778206's badge "25 TO ALMOST 31 MI" keeps the range
   (the badge is read for qualifiers and never drawn on these surfaces). The monument style, when its figure stands,
   also drops only an emptied field and would keep a one-word rest of the figure's line under the drawn figure;
   that path draws the figure (nothing is withheld) and is not measured here.
