# QF-23: checked copy is never truncated

Review finding (project-quality-review-2026-11-05, section 3 "Visual design"): checked copy could be silently
truncated with an ellipsis (`src/typography.py:228-290`, `fit_text`'s sentence-keeping and `_shorten`) on a channel
whose premise is verified facts. Acceptance (ledger): `fit_text` raises instead of an ellipsis; every card refuses
rather than truncating.

## What changed

- `src/typography.py`. One refusal type: `LayoutError(ValueError)` (a surface that cannot be drawn as designed
  without cutting checked copy) and its checked-copy case `TextFitError(LayoutError)`, which carries the copy, the
  whole copy wrapped at the last size tried, the font and the box. `fit_text` wraps at the design size, steps down to
  the floor and then raises; the sentence-keeping branch and `_shorten` (the ellipsis) are gone, `quiet` and
  `sentences` permit nothing. `probe_text` is the trial form for a layout choosing between its permitted settings: it
  returns the whole copy with an overflow flag instead of raising, and every caller ends on a strict `fit_text`.
  `fit_line` inherits the rule; `fit_lines` (the legacy line fitter) no longer drops sentences or runs past its block.
  Every measure is QF-21's shaped run (the kerned, featured advance `FeaturedFont` and `tracked_width` give).
- `src/graphics.py`. `fit_tracked` refuses like `fit_text`; its trial (`_probe_tracked`, the old search unchanged)
  feeds only the timelines' layout search, which is refused when its chosen layout still has a cut label (portrait
  and 16:9), so every layout that holds its labels whole is chosen exactly as before. The site label raises at its
  floor (its fallback called `fit_lines` with a missing argument). The number cards try their scale ladder, then
  refuse; the cross-section label takes three lines before it refuses (it was cut on two), and a
  stack that then no longer fits its column is refused rather than drawn past the text box (stacks of labels two
  lines hold whole are placed exactly as before). `make_all` records a
  refused graphic in `payload["layout_holds"]` and goes on without it, as it does for any failed graphic. The
  scale poster's ruler note keeps its rule ("inside the box or not at all") through `probe_text`: one line changed in
  the area QF-24 rewrites.
- Stills. `src/multi_format.py`: the kicker is refused whole instead of cut with an ellipsis (`_fit_kicker`); a
  carousel slide's text steps down its existing scale ladder before the slide is refused, and a "<SUBJECT> ·
  <CATEGORY>" kicker that no scale holds names the subject alone rather than being cut (the category is the house
  label `_get_card_category` gives the slide, "YOUR TAKE", no checked copy; recorded as shortened
  "kicker_category", which the stills audit puts on the review card as a note); the deck frame's heading probe (`_heading_lines`) takes the two-line frame where its design scale would cut;
  trial fits use `probe_text`. `src/still_layout.drawn`: copy that fits the first layout only cut gets the design's
  tighter fit (the retry a failed layout already gets), which refuses in turn. `src/cover_design.py` /
  `src/visual_engine.py`: trial fits use `probe_text` (the cover variants are gone with QF-82); the final cover falls back to the
  next style that fits, and a cover no style holds whole is refused (the run stops before review with the reason:
  a Reel needs its cover); the paper card's fill estimate and the profile-grid redraws treat a refusing style as no
  option. `src/audience_chart.py`: the headline's one-line trial uses `probe_text`; a Reel name that fits only cut
  refuses the chart.
- YouTube thumbnail (operator decision, option 1, decisions/QF-23-thumbnail-question.md). The four-word budget
  stays and now selects instead of cutting: `youtube_thumbnail._words` returns a line whole when it fits the budget,
  else nothing (it kept the line's first N words). The headline is both lines, else the first whole line that fits
  with its figure, else another verified line (eyebrow, tagline) that fits, else no headline; the outcome is in the
  thumbnail's .qa.json and on the review card (`thumbnail_note`, e.g. "drawn without its headline").
- Documentary copy (review 1). `main.documentary_cover` split the fact-checked title at 22 characters and cut the
  second line with "…" ("MECHANISM AND THE LOS…"); it now splits on whole words, the first line up to 22
  characters and the rest of the title whole on the second, and the cover's strict fit sets it or refuses.
  `visual_engine.documentary_cover_copy` (the YouTube thumbnail's copy) lost its `title[:24]` fallback the same way,
  and `extract_curiosity_badge` takes whole copy or its next source instead of `[:28]` / `[:22]` / `[:24]`.
- Holding. `main.hold_layout_error` holds a refused Story or carousel deck alone: the error is logged, the frames it
  had saved move to `<deck>/held/` (out of the posting globs), `payload["layout_holds"]` records it, and the run goes
  on; any other error propagates. The review card lists each held surface with its reason
  (`telegram_bot.layout_hold_lines`: "✋ Held, its text would not fit whole: N"). `control`: the analytics chart
  falls back to its figures as text when a Reel name would be cut. A render clears `payload["layout_holds"]` before
  it packages, so a resumed run's old payload shows only this render's holds.

### One refusal type with the parked freeze work (C:\tmp\freeze-park)

The park already raises `multi_format.LayoutError(RuntimeError)` for a body that fits none of F1's settings
(`BodyFitError(LayoutError)`) and for a missing credit (F8), and holds such a deck in `main.hold_layout_error`. QF-23
moves that class to `typography.LayoutError` (now a `ValueError`, so the existing `fit_*` callers and the approved
main test, which expect `ValueError`, are unchanged) and makes the checked-copy refusal its subclass. The park rebase:
1. `src/multi_format.py`: delete the park's own `class LayoutError(RuntimeError)`; import `LayoutError` with the other
   typography names, so `multi_format.LayoutError` (and the park tests' `M.LayoutError`) is the same class. Declare
   `class BodyFitError(TextFitError)` and raise it with the body and its box (`BodyFitError(body, lines, font, width,
   self.BODY_MAX_LINES)`, or `where=` for the share line), so its message starts "Checked copy does not fit" like
   every other refusal; the carousel's split-then-hold logic (`except BodyFitError`, `raise LayoutError(f"carousel
   slide {i + 1}: {e}") from e`) and the F8 credit refusal (`raise LayoutError(...)`) stay as they are.
2. `main.py`: keep one `hold_layout_error`. QF-23's is the park's function with the import taken from
   `src.typography`; the park's copy goes. Its callers, the record shape (`{"deck", "error", "moved"}`) and
   `LAYOUT_HELD_DIR` are the park's, so the park tests (`test_r3_a_layout_error_holds_only_that_deck`: a plain
   `ValueError` still propagates) hold unchanged.
3. Nothing in the park catches `RuntimeError` around a deck (checked), so the base-class change moves no handler.
After the rebase there is one rule, one class and one hold: a refused Story, carousel or graphic is on the review
card with its reason whichever code raised it.

## Measurements (`python measure.py <repo root> [<folder holding run_*>]`)

measurements_dev.json and before_dev.json: this commit and the base on the committed dev copy (real_inputs.json, 34
runs, extract_real_inputs.py); measurements_production_sample.json and before_production_sample.json: the same on a
copy of the production sample (34 runs, outside the repository; counts and aggregates only, the operator's rule
of 2026-11-10). Fitting primitives on impossible boxes:

| | base | this commit |
|---|---:|---:|
| impossible boxes refused (fit_text, quiet, fit_line, fit_lines, fit_tracked x 3 texts) | 0 / 15 | 15 / 15 |
| returned with an ellipsis or words lost | 11 | 0 |
| returned whole but past the box (fit_lines, fit_tracked) | 4 | 0 |
| fitting controls whole, inside the width, not shortened | 3 / 3 | 3 / 3 |

Real runs, every surface re-rendered from the run's checked copy (stills on a generated neutral photo):

| Surface | dev (34 runs): base -> this commit | production sample (34 runs): base -> this commit |
|---|---|---|
| Story frames | 34 drawn whole -> 34 drawn whole, identical | 34 -> 34, identical |
| Instagram carousel | 6 drew the closing kicker cut ("... · YOUR…"), 28 whole -> 34 whole (the 6 one scale step smaller) | 11 cut, 23 whole -> 34 whole (the 11 one step smaller) |
| Facebook carousel | 6 cut -> 34 whole (same 6) | 11 cut -> 34 whole (same 11) |
| Covers (final and grid tile) | 34 whole -> 34, identical | 34 -> 34, identical |
| Number cards 9:16 / 16:9 | 23 / 23 whole -> identical | 20 / 20 -> identical |
| Timelines 9:16 / 16:9 | 10 / 10 whole -> identical | 20 / 20 -> identical |
| Scale 9:16 / 16:9 | 2 / 2 drawn (1 not drawn: no reference) -> identical | 5 / 5 -> identical |
| Map site label (both sizes) | 34 / 34 whole -> identical | 34 / 34 -> identical |
| Analytics chart, the Reel's name | 1 cut ("... Water Could Not…") -> refused (the figures go as text) | 1 cut -> refused |
| The title as a documentary 9:16 cover (review 1) | 10 cut ("OBELISK TRAPPED IN…") -> 34 whole, 0 refused | 22 cut -> 34 whole, 0 refused |
| The title as a YouTube thumbnail (review 1) | 5 drew the cut line ("…") -> 0 (the 4-word budget still drops words: decision open) | 18 -> 0 |
| **Surfaces that drew cut copy** | **28 / 378 -> 0** | **63 / 396 -> 0** |
| **Surfaces refused** | **0 -> 1** (the chart) | **0 -> 1** (the chart) |

Base a3946226 (build 2, rebased; the same counts as on 5c7840e9). On the YouTube thumbnail, 14 dev surfaces the base
drew with a line's first words (no ellipsis, so not counted as cut above) now draw only whole verified lines (option 1). No real run is a documentary, so each run's title is made as one (main.documentary_cover)
to measure that path. "Identical": the surface drew the same strings on both trees (drawn_sha, per surface); 350 of
378 dev surfaces, the rest being exactly the cut ones above (the production sample is counted only, not compared).
A long documentary title now takes the cover's third line ("OBELISK TRAPPED / IN BEDROCK"), as cover_design's
two-line fit allows; none of the 68 is refused. No Story, cover or graphic of a real run
is refused, and no real carousel either: every cut kicker is drawn whole by the existing scale ladder. The kicker is
the closing slide's "<SUBJECT> · YOUR TAKE" (the six dev runs are Damascus steel, Roman concrete and the Unfinished
Obelisk; the production sample is cited by count only); the base drew it at the slide's full
scale with its last word cut, this commit draws the slide's stack at the next scale (0.92) with the kicker whole.
Words the real surfaces leave out by design (a cover tagline that repeats the headline, a poll frame's own
question, figures in spoken units) are the same on both trees.

## Evidence renders (renders/)

`render_evidence.py <tree> <checkout> <out> before|after` drew the surfaces the base cut, from the dev runs' real copy
and their topics' licensed photo pools, matched to the slides as the pipeline matches them (half-size JPEGs;
drawn_before.json / drawn_after.json record what each drew).

| Case (real run, real photo pool) | before (base) | after (this commit) |
|---|---|---|
| Unfinished Obelisk 1793056896, closing slide, Instagram and Facebook | "THE UNFINISHED OBELISK · YOUR…" at 45.3 px, the stack at scale 1.0 | "THE UNFINISHED OBELISK · YOUR TAKE" whole at 41.7 px, the stack at 0.92 |
| Roman concrete 1793163918, closing slide | "ROMAN CONCRETE SECRETS · YOUR…" | whole at 0.92 |
| Damascus steel 1792732472, closing slide | "DAMASCUS STEEL SECRETS · YOUR…" | whole at 0.92 |
| Unfinished Obelisk 1793056896, its title as a documentary cover | "EGYPT'S LARGEST / OBELISK TRAPPED IN…" | "EGYPT'S LARGEST / OBELISK TRAPPED / IN BEDROCK", whole (the photo window gives way) |
| Greek fire 1792735284, analytics chart | "The 10th-Century Weapon That Water Could Not…" | refused: "Checked copy does not fit 1 line(s) of 2080 px at 72 px"; the /chart command answers with the figures as text ("A Reel's name does not fit the chart whole; its figures are below.") |

Downstream, inspected: on the three closing slides the heading, the question body and the share line are 8% smaller
than on the deck's other slides (the whole stack steps down, as the ladder does for a stack too tall); every word is
there, the kicker sits on one line inside the margin (right edge 1242-1294 of the 1328 px measure), contrast
unchanged (8.81 / 5.58 / 5.51 vs 8.83 / 5.61 / 5.50). The Facebook copy draws the same slide. The size step on the
last slide is the one visible design change; a kicker-only step would keep the deck's sizes even (noted for the
freeze panel).

## Tests

`tests/test_qf23_checked_copy.py` (fails on the base a3946226: 26 of 27; the fitting control passes on both; the five
review-1 tests: the documentary copy split on whole words, a long documentary title drawn whole or refused, the closing
kicker whole one scale down, the subject-only kicker recorded, the three-line cross-section label; the first two
fail on 680f6ded, build 1): impossible
boxes refused by fit_text (quiet or not), fit_lines, fit_tracked and the site label; probe_text returns the whole
copy; the refusal hierarchy (TextFitError < LayoutError < ValueError); a refused deck held alone with its saved frames
moved out of the posting glob and recorded, any other error propagating; a refused graphic dropped and recorded by
make_all; the review card's "Held" list; the tighter fit tried before a still is refused; the kicker refused whole; a
the cover drawn in the next style (or refused); the YouTube thumbnail's headline a whole
verified line or none; the analytics chart falling back to text;
the portrait timeline refusing its cut layout without encoding.

Timeline stress sets (surveyed on 4b288e84, build 1; on 5c7840e9 the 137 targeted tests below hold the same list):
`timeline_refusals.py <repo root>` renders every committed timeline set of the tests (185 sets,
370 renders at 9:16 and 16:9). Base (timeline_refusals_before.json): 63 drawn with a cut label, 307 whole, 0 refused.
This commit (timeline_refusals.json): the same 63 refused, the same 307 whole, 0 cut. All 63 are synthetic stress sets
(fuzz vocabularies, XL and xv labels); no real run's timeline is among them (measure.py: 10 dev and 20 production
timelines draw the same strings on both trees). `QF23_REFUSED` in tests/test_r4_graphics_2026_11_02.py is that list.

Branch-only expectation changes (none of these files is on origin/main, checked with `git cat-file -e`), each the
QF-23 policy (refuse, never cut):
- tests/test_no_cut_text.py: the operator-approved patch (decisions/QF-23-proposed-main-tests.patch, approved
  2026-11-07; the file turned out not to be on origin/main): two impossible boxes refuse, a five-line control whole;
  its import block grouped for ruff.
- tests/test_audience_chart_2026_10_29.py, tests/test_r3_facts_chart_2026_11_02.py: names the chart holds only cut
  refuse; the fitting controls require the exact name.
- tests/test_bait_emoji_units_2026_10_29.py: the overlong number cards refuse before encoding; the layout checks run
  on fitting copy.
- tests/test_still_layout_2026_10_28.py: the overlong Story body refuses and leaves no frame.
- tests/test_carousel_1440_2026_10_28.py: the overlong card refuses its deck (new test); the deck checks run on the
  other six cards; its per-archetype cases expect the refusal (at least one, never another card's); every drawn case
  is also asserted unshortened.
- tests/test_carousel_content_2026_10_28.py: the thirty-fold bodies refuse; the alt text's 1,000-character cap is
  held on the records such bodies leave.
- tests/test_r4_graphics_2026_11_02.py: the 63 stress renders expect the refusal and draw nothing; every other set
  keeps every property check; the trial-search tests (word-boundary cut, no false cut over 1,500 labels) target the
  trial `_probe_tracked`, and fit_tracked is asserted to refuse.
- tests/test_timeline_hierarchy_2026_11_03.py: the sets in QF23_REFUSED refuse without encoding; the others keep
  every hierarchy rule.
- tests/test_safe_zones.py: the Derinkuyu section's first two labels fit only cut ("and storage…", "kitchens
  and…" on the base), so it is refused (new test); the box check runs on the same section with labels the column
  holds whole.
- tests/test_youtube_thumbnail_2026_11_01.py: the two-tone check runs on a line the budget holds whole; the
  six-word line it drew cut is asserted left out whole (operator decision, option 1).
- tests/test_covers_carousel_round2_2026_11_02.py: the figure thumbnail whose second line the budget held only cut
  asserts no headline (recorded); the budget checks run on a line that fits.
- tests/test_map_rewording_timeline_2026_10_29.py: the reveal-order timeline renders at the pipeline's 1080 x 1920
  (it rendered at 540 x 960, where "Rediscovered during home renovation" fits the label floor only cut).

## Known limits

- The stills were measured on a generated neutral photo (the real photos are not committed): the text fitting reads
  the photo only to choose the tighter fit on a busy one, which this commit now also tries before it refuses
  (still_layout.drawn). The evidence renders use the real photo pools.
- The analytics chart refuses a Reel name it cannot hold on one line (1 of 34 titles on each sample): /chart then
  answers with the figures as text. A two-line row would keep the picture; that is a chart design change, not this
  row's.
- Credit lines keep their own sanitised 60-character cap (QF-42) and alt text its 1,000-character cap
  (platform limits, not drawn copy); a route stop the layout cannot name is left unnamed (QF-20's map port); the
  map's neighbour names and the callout plates never drop words.
- The timelines' layout search still scores cut candidates (`_probe_tracked`) so that every layout that holds its
  labels whole is chosen exactly as before; a chosen layout with a cut is refused, never drawn.
- Overflow paths that cut no word stay: the end card fills more lines than its plan when no plan fits
  (end_card.layout), a callout plate widens past LABEL_MAX_W when no size fits (callouts._shapes), and fit_font
  returns its floor size still overflowing (the cover figure, the YouTube title). None fires on the real samples.
- A cover no style holds whole stops the run after voice and render spend (0 of 68 real covers); a script-stage
  pre-check of the cover copy would fail it cheaper.
- The closing slide's whole stack steps down 8% for its kicker (the body 36 x 0.92 = 33.1 design px, under
  BODY_SPEC's own floor of 34, as the existing ladder already allowed for tall stacks); a kicker-only step would keep
  the deck's sizes even, for the freeze panel.
- The V2 rich review card (TELEGRAM_CARDS_V2, off) is a fixed mock-up and shows no holds; the text card does.
