# QF-94: drawn text never drops a character silently

Finding (decisions/GLYPH-DROP.md, 2026-11-09; adversarial reviewer of the social design round 3, confirmed in code):
`typography.drawable(text, font)` removed every character its face had no glyph for, tidied the spaces and returned
the shorter text, with nothing logged or recorded; about 20 call sites in `src/` use it. In Anton "ΑΓΙΑ ΣΟΦΙΑ" became
"Σ" and a Cyrillic heading became empty, so a card could show text that is not the signed field (fact-safety rule:
cards draw only checked, signed card fields).

Operator ruling (decisions/FREEZE-RESUME-rulings.md Q2, 2026-11-10): bundle Noto subsets for Greek, Cyrillic and
extended Latin (SIL OFL) as a fallback face, recorded per field; refuse the card only if no bundled face has the
glyph, with a reason the review card shows.

## What changed

- **Fallback faces** (`fonts/NotoSans-LGC.ttf`, 897 KB; `fonts/NotoSerif-LGC.ttf`, 661 KB). Built by
  `scripts/build_fallback_fonts.py` from google/fonts at a pinned commit (8b0a1d0f, Noto Sans / Serif 2.015), each
  upstream file checked against its recorded SHA-256, cut with fontTools 4.67.0 to Latin (Basic, Latin-1,
  Extended-A/B/C/D, Extended Additional: Vietnamese), Greek and Coptic plus Greek Extended (polytonic), Cyrillic plus
  its supplement and extended-C, the combining marks and general punctuation; layout features, names and variation
  kept; hinting removed; weight axis limited to 400-900 (no design sets lighter text); the serif's width axis pinned
  to normal (Cinzel, the face it stands in for, is wide). The build is deterministic (two builds, identical hashes).
  Licence evidence follows the repository's font convention (QF-90 has not built THIRD_PARTY_NOTICES.md yet; it reads
  this record): `fonts/licenses/<file>.OFL.txt` (the upstream OFL text, LF) and a `fonts/license_evidence.json` record
  per file with its SHA-256, embedded names, licence hash, the pinned upstream URL, the upstream hash, the subset
  ranges and tool version, and `"role": "fallback face (QF-94)"`. `font_evidence.font_rights` verifies both. The Noto
  licence reserves no font name, so the subsets keep theirs.
- **Mechanism** (`src/font_engine.py`, face-agnostic): `field_face(text, font)` returns `font` when its cmap maps
  every character of the field (white space and invisible format characters aside, read in composed form so a base
  letter and its mark count as the precomposed letter HarfBuzz composes), else the first face in the font's fallback
  list that maps the whole field: the whole field is set in that one face. The face is matched to the brand face's
  instance by measurement (`fallback_match`): width on `wdth` (the advance of HAMBURGEFONSTIV per cap height), colour
  on `wght` (its ink density) and size by cap height, so it holds for any brand face (QF-87 may change them). No
  bundled face maps it: `GlyphRefused` (code `no_glyph`, a reason naming the characters, their script and the field).
  `FeaturedFont`'s measures and masks, `shape` and `draw_run` all take the field's face, so every path that draws
  through a bundled font (plain `ImageDraw.text`, tracked runs, plates) draws the same face and never a missing-glyph
  box.
- **Which faces** (`src/typography.py`): `FALLBACK_FILES` (sans, serif) and `FALLBACK_STYLE` (`Cinzel: serif`; every
  other family is sans). `get_font` gives each brand font its fallback list: the Noto subset of its own style, the
  other one, then the other brand faces, a face with a weight axis first (the ruling refuses a field only when no
  bundled face has its glyphs: Cinzel and both subsets have no arrow, Montserrat has). A brand change edits these two
  tables only.
- **drawable()** keeps every letter: it still removes emoji (the art gate), records that removal, and checks the field
  (raising `GlyphRefused`). `fit_text`, `fit_lines`, `plan_text`, `graphics.fit_tracked` and the renderers that draw a
  field's lines or words one at a time (`field_font`: the site's marker label, a route stop's name, the end card's
  share line, a Story frame's credit) set the whole field in one face at every size they try.
- **Recorded per field, with its surface.** A plate's layout record names the face it was drawn in, `fallback_for`
  (the brand face) and `fallback_px` (the matched raster size); a plate built straight from a brand font resolves its
  face too. Every distinct string set in a fallback face, refused or stripped of emoji is logged and written once per
  surface to the run's glyph ledger `output/<run>/glyph_faces.jsonl` (`ops.RunLog` opens it for the run, as it does
  the run's log), labelled with `where` it was drawn (`font_engine.surface`: "cover", "YouTube thumbnail", "Story
  frame 2", "carousel", "graphic map", "graphic then/now", "design card chapter", "photo callouts", "end card",
  "captions"; then/now, callouts and the thumbnails on both the Reel and the documentary path). A line, word or fitting trial of the
  field being drawn (`begin_field`) is written too, marked `part_of` that field, so nothing drawn goes unrecorded and
  the card still lists the field once.
- **Refusal, the release and the review card.** A refused field costs its surface: a graphic job is skipped (as any
  failed graphic), a design card writes the reason to its `.design.json` (`CardRefused`, code `no_glyph`), a Story
  frame is not written (an earlier render of it is removed; stories/layout.json keeps the refusal with its
  characters, script and text) while the other frames go on. A refused Story frame is never a silent short Story: the
  release report adds a `story_frames` check, **needs_review**, naming the frame ("Story frame 2 not drawn: no
  bundled face draws 山 田 太 郎 (CJK) in "Photo: 山田太郎 · … / This frame: …"; the Story would post 1 of 2
  frames"; since QF-80 the Story is hook and reveal, with no poll frame), so approval
  stays the operator's decision with the facts in front of them. (Re-matching the frame to another photo before
  refusing was not done: the Story's photos are matched to its checked copy by `_match_photos`, and a substitute
  would bypass that match; the operator sees the refusal and can Regenerate.) The review card lists the ledger under
  "🔤 Lettering" (`telegram_bot.glyph_notes`), each note naming its surface and what a refusal cost: `Cover:
  "ΑΓΙΑ ΣΟΦΙΑ" set in NotoSans-LGC.ttf (Anton-Regular.ttf has no Α Γ Ι Ο Φ, Greek)`, `Story frame 2: "Photo: 山田太郎
  · CC BY-SA 3.0 / This frame: …" not drawn, so the frame is left out and the Story posts short: no bundled face draws
  山 田 太 郎 (CJK)` (a field's lines joined with " / "), `Graphic map: … not drawn, so the graphic is left out of the
  video`, `Captions: "ابن" has ا ب ن (Arabic), which no bundled face draws; libass draws them in a system face`; emoji
  removals are one count line ("Emoji removed from N field(s)").
- **Sizes.** A fallback face reports the size the field was asked for (`.size`, the brand face's) and is rasterised at
  its matched size (`_rr_px`; `font_engine.raster_px` for shaping), so a renderer that rebuilds a family's font from a
  fitted font's size (the figure settle, a refit at "the fitted size") gets the design size, not 1.2x it (review 1,
  finding 3).
- **Captions.** libass draws the captions, not this code: refusing a caption would refuse the whole video over one
  word libass can draw (review 1, finding 5: decided "measure safely"). Every caption measure takes a word in the
  face `choose_face` gives it, the caption face or the bundled fallback face that draws it; a character no bundled face
  draws (an Arabic name in the narration) is measured in the caption face itself, its missing-glyph advance and box
  (`font_engine.shape_in_face`, `font_engine.plain_face`), as on the base, and never raises: the run advances
  (`unkerned_length`) and the first and last glyph boxes of each 9:16 run (`_glyph_box`, which `_libass_line`,
  `_caption_ink_width`, `_caption_fit_size`, `_caption_ink_offset` and the burst fitters use; review 2, blocking 1:
  round 2 had fixed only the advance, so the 9:16 caption build raised). On "THE SCHOLAR ابن الهيثم WROTE" the
  measure equals the base's (`_libass_line` 891.53 / 0.74 / 889.94 at 80). The measures are cached across runs, so
  they record nothing: `build_subtitles` records each word no bundled face draws once per run under "captions"
  (`_record_caption_faces`), and the card says libass draws it in a system face.
- **Operator sheets.** The fallback lookup is on every bundled font, so two operator-only images that are not art
  would have raised too: the photo contact sheet now draws an archive title no bundled face draws as before (its
  missing letters as visible .notdef boxes), in `plain_face` directly, so no refusal is recorded on the run's
  review card during photo screening (QF-94b: round 3 drew through the fallback lookup first, which recorded a false
  "not drawn, so that render stopped" before falling back), and `/analytics chart` sends its figures as text with "The
  chart could not draw … (script); its figures are below." instead of failing.
- The fallback faces are recorded as sources: the composer's composition manifest (so the release's `font_rights`
  check and the provenance's resource evidence cover them), the stills' packaging cache inputs and the graphics jobs'
  checkpoint inputs (`bundled_font_files`).

## Arabic: refused, with evidence

Decided with evidence that a field in Arabic is refused with a reason, not drawn (measure.py section 3):
- No bundled face (the brand faces and both subsets) maps a single character of the Arabic block (0 of 9 files). The
  operator's ruling bundles Greek, Cyrillic and extended Latin only; adding Arabic is a new font asset.
- A face alone would not be enough: the tracked path shapes a field as one HarfBuzz buffer. For the mixed field
  "Ibn al-Haytham ابن الهيثم" HarfBuzz guesses one direction for the whole buffer, `ltr`, and the Arabic clusters come
  out in logical order left to right (15, 16, ... 24): the name would read backwards. Drawing Arabic needs bidi
  itemisation of each field, RTL anchors and wrapping, none of which the layouts have.
- Real data: one real field is Arabic, a Commons author's name ("ولاء", dev Singing Colossi run). Its photos are
  public domain, so no Story frame draws its credit; nothing real is refused on a surface it is drawn on. Before this
  row that author's name would have been dropped from a credit line (an attribution breach).

If Arabic (or Hebrew) sources become common, the operator can add a Noto Sans Arabic subset plus RTL layout as a row of
its own; until then every such field is refused and listed on the review card.

## Measurements (measure.py, base 4b288e84 -> this commit)

`python docs/reviews/quality-fixes-2026-11/QF-94/measure.py <repo root> [<production sample copy>]`; before.json and
after.json beside this file (both runs given a temporary copy of the production sample, outside the repository).
Committed input: `real_fields.json` (extract_real_fields.py over the dev runs: 2,628 fields, 1,332 distinct strings,
1,331 dev and 1 signed card field; every drawn copy field, the photo credits and the signed card fields). The
production sample is never committed: its fields (34 runs, 2,955 field rows, 1,357 distinct strings) are read from the
temporary copy at run time and reported as counts only. Each string is checked in all four families a card sets text
in, plus a 21-string stress list (Greek, polytonic Greek, Cyrillic, Maltese Ħ/ħ, Turkish ı/ş/ğ/İ, Vietnamese,
Polish/Hungarian/macrons, mixed Latin with Greek or Cyrillic, Arabic names, a credit line with an Arabic author).

| Metric | Base | This commit |
|---|---|---|
| Dev fields (5,328 face checks): characters removed by drawable (emoji aside) | 214 (14 fields drawn shorter) | 0 |
| Dev fields: characters lost through fit_text (the lines a card draws) | 214 | 0 |
| Dev fields: set in a fallback face / recorded | 0 / 0 | 10 / 10 |
| Dev fields: refused with a reason / recorded | 0 / 0 | 4 / 4 (one Arabic author name, in each family) |
| Production sample (counts only, 5,428 face checks): characters removed | 144 (6 fields drawn shorter) | 0 |
| Production sample: fallback / recorded; refused | 0 / 0; 0 | 6 / 6; 0 |
| Stress list: characters removed | 405 (47 of 84 face checks drawn shorter; "ΑΓΙΑ ΣΟΦΙΑ" -> "Σ" in Anton, "" elsewhere) | 0 |
| Stress list: fallback / recorded | 0 / 0 | 31 / 31 |
| Stress list: refused with a reason / recorded | 0 / 0 | 16 / 16 (the four Arabic strings, each family) |
| Fallback cap height vs the brand face at 80 px (Anton, Montserrat, Cinzel, Plex Mono) | - | 68/69, 56/56, 56/56, 56/56 px |

Matched instances: Anton -> Noto Sans wght 900, wdth 62.5 (both at their limit: Anton is narrower and heavier than any
Noto Sans instance; at the same cap height the fallback is about 42% wider on HAMBURGEFONSTIV, review 1), x1.203;
Montserrat 600 -> Noto Sans wght 569, wdth 100, x0.979; Cinzel 700 -> Noto Serif wght 521, x0.986; IBM Plex Mono 500 ->
Noto Sans wght 538, wdth 91, x0.979.

Which dev fields: the Cyrillic parts of Amber Room photo titles (in Anton and Cinzel; Montserrat and IBM Plex Mono have
Cyrillic). On the surfaces they are actually drawn on, no real field needs a fallback today: credit lines are set in
IBM Plex Mono, which has Cyrillic (the dev Amber Room author "Андрей Андреевич Зеест" draws in it), and the dev place
name Ħal Saflieni (also the one signed card field) is in Anton's and Montserrat's own coverage. The production sample
shows the same pattern in its counts: its fallbacks are in Anton and Cinzel only, and nothing in it is refused.

## Evidence renders (renders/, half size)

Real data: the dev Amber Room run's own copy and its photo 2 ("The Amber Room panoramic photo"), whose own Commons
record names the room in Russian, "Янтарная комната". No posted card has a field that needs a fallback (above), so
the cover's two title lines and the Story frame's first headline line carry that name; everything else is the run's
copy as written, drawn by each tree's own renderers (render_evidence.py).

- `before_cover.jpg`: the title is gone; Anton has no Cyrillic, so both lines were drawn empty and the cover shows
  only the eyebrow and tagline. `after_cover.jpg`: "ЯНТАРНАЯ / КОМНАТА" set whole in Noto Sans Black Condensed at
  Anton's cap height, in the title's colours and place.
- `before_story.jpg`: the first headline line is missing ("SHIPPED TO A CASTLE" alone). `after_story.jpg`: the line
  drawn whole in the matched face; being wider than Anton, it wraps to two lines and the frame's block moves down
  to make room (the credit stays where it was).

## Tests

`tests/test_qf94_glyph_fallback.py` fails on the base (it imports `GlyphRefused`; its drawable assertions fail there
too) and passes on this commit: drawable keeps every letter of the stress list in all four families; Arabic is refused
with a reason on every path (drawable and a plain `ImageDraw.text`) and recorded; Greek in Anton is drawn whole in the
matched Noto Sans (pixel-identical to the face's own drawing, cap height within 1 px); tracked and plain draws take the
same face; the serif falls back to the serif; a character only another brand face has is drawn in it; a wrapped field
is set in one face and recorded once; a plate records its face; faces that cover a field keep it (Maltese, Turkish,
Vietnamese, a decomposed accent); emoji are still removed and recorded; the match is within the axes for every face;
the fallback files are licensed, bundled and contain no Arabic; a design card's refusal carries the reason; RunLog
points the ledger at the run; a Story frame no face can draw is refused (an earlier render of it removed) while the
other frames are drawn. Round 2: a Story (hook and reveal, QF-80) whose frame 2 is credited (CC BY-SA) to a CJK author
writes frame 1 only, and the release report's `story_frames` check is needs_review naming frame 2, CJK and "1 of 2
frames"; the review card names each note's surface and what a refusal cost, and counts emoji removals in one line;
a fallback face reports the size asked for (a refit at its size gets the design size); an Arabic caption word is
measured and recorded, not fatal; a plate built straight from a brand font records the face that drew it; a field
drawn on its own, though a substring of the field before it, is recorded. Round 3: `_libass_line` and
`_caption_ink_width` on an Arabic caption word return the base's measure (they raised on round 2); `build_subtitles`
(9:16 and 16:9) records each such word under "captions" once per run, warm caches or not, with the card's "Captions:"
label; then/now,
callouts, the cover and the YouTube thumbnail are named surfaces with the right cost; the card note says "not
drawn" once and joins a credit's lines with " / "; the contact sheet and `/analytics chart` survive a title no
bundled face draws.

Branch-only test changes: `tests/test_bait_emoji_units_2026_10_29.py::test_emoji_and_characters_the_font_cannot_draw_are_removed`
expected `drawable("Café → ½", "Cinzel") == "Café ½"`, the silent drop this row removes; it now expects the field
kept ("Café → ½", drawn in Montserrat, which has the arrow). Not on origin/main.
`tests/test_story_credits_2026_10_29.py::test_a_long_name_stays_within_three_lines_and_a_missing_glyph_is_reported`
expected a Story frame whose credit author is "山田太郎" to be drawn with the name dropped and `complete` False;
no bundled face draws CJK, so the frame is now refused with `GlyphRefused` (reason names CJK). Not on origin/main.

## Known limits

- The fallback is set per field. A renderer that measures parts of one field with fonts it reloads by family name
  and size, outside the helpers above (the map's neighbour names, which come from Natural Earth's English names), is
  still drawn without a dropped letter (each part takes its own face), but two parts of such a field could take
  different faces. Real data: no map name needs a fallback.
- Noto Sans at its narrowest is wider than Anton, so an Anton field in Greek or Cyrillic takes more room (the
  fitting shrinks or wraps it, as on the Story frame above).
- Covers, the end card and the carousel have no per-card refusal path of their own: a field there that no bundled
  face can draw stops that stage with the GlyphRefused reason (the run's failure card shows it), as any other
  render error does. Their fields are the gate-passed English copy; real data has none in Arabic.
- QF-87's acceptance "fonts/ lists only FONT_FILES entries" must count `FALLBACK_FILES` too.
- **Greek and Cyrillic captions run past their plate (QF-94b, measured).** libass draws the captions, and for a
  glyph the caption face lacks it takes a system face, not the bundled Noto: ffmpeg's font-selection log shows
  `fontselect: (Anton, 700, 0) -> Arial-BoldMT` (16:9: `(Montserrat, 700, 0) -> Arial-BoldMT`) on the base and on
  this commit alike, so the drawn ink is identical on both trees, to the pixel. What changed is the plate: it is
  sized from the measure, which now takes Greek in the matched Noto (wider than Anton's .notdef boxes), so it is
  closer to Arial's ink but still short of it. Ink past the plate on each side, from real libass renders on a red
  ground (synthetic words; `caption_plate_overflow.py <repo root>`, its output for each tree in
  caption_overflow_before.json and caption_overflow_after.json):

  | 9:16 caption | base: plate w / ink past it, left / right | this commit: plate w / ink past it, left / right |
  |---|---|---|
  | Αθήνα wrote | 413 / 96 / 95 | 515 / 44 / 45 |
  | Αγία Σοφία | 379 / 123 / 121 | 503 / 60 / 60 |
  | Москва 1941 | 414 / 100 / 107 | 509 / 54 / 58 |
  | Γνώθι σαυτόν | 440 / 154 / 155 | 583 / 83 / 83 |
  | the Παρθενών temple | 646 / 148 / 148 (ink x 69-1011) | 648 / 65 / 64 (ink x 151-928) |
  | Κωνσταντινούπολη | 593 / 242 / 242 (ink x 2-1079: the frame's edge) | 688 / 107 / 108 (ink x 89-992) |
  | Counterrevolutionaries (control) | 690 / -28 / -27 (the pad, inside) | the same |
  | Ibn ابن wrote | 440 / -25 / -23 (inside) | the same |

  On 9:16 the overflow is 95-242 px a side on the base and 44-108 here, about half. In the two lines where the
  ink x range changes, the plate's size also changed the burst's fit size, so the same Arial glyphs are drawn
  smaller. On 16:9 (Montserrat, which has Cyrillic) the Greek ink runs 4-48 px past the plate a side on both
  trees, within 4 px of each other. The Arabic caption stays inside its plate on both. Real narrations have no
  character Anton lacks (review 1, finding 5), so no real Reel changes. Setting the matched face in libass (a `\fn`
  override on such runs, or measuring in the face libass selects) so that the plate covers the ink is a row of
  its own.
- The ledger follows the run through `ops.RunLog` (as run.log does): a render started from Telegram outside a run (a
  cover redraw) is logged, not written to a run's ledger.
- `graphics.marker_label_lines` slices fit_lines' `(lines, font)` tuple on its last-resort path (there on the base;
  queue/QF-102.proposed.md).
