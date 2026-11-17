# Chapter cards: draw all of them (operator decision 2026-11-05)

Decision (docs/HANDOVER-design.md): "Chapter cards: draw all of them. A long act title (3 of 5 Richat titles are
over 32 characters) wraps to two lines; no card is dropped."

All measurements under raqm (`PIL.features.check("raqm")` True, FriBiDi on `PATH`), `FONT_SIZE=80`.

## What changed (`src/design_cards.py`)

- `plan_chapter_card` no longer refuses a title over 32 characters ("a chapter title is 1-32 characters"); an empty
  title is still refused. The refusals that protect facts stay: number/total out of range (1..total, total <= 12),
  a title repeating the chapter number ("Act 2: ..."), and `ground_line` (ungrounded title, record signing).
- 16:9: the two-line ladder (`CHAPTER_WIDE_TWO`, 176..96) now runs to its 96 px floor. Before, only 176..152 were
  allowed (a size had to reach the numeral's ink top at <= 1.20 P), so a title needing a smaller two-line size was
  refused. From 145 px up nothing changes. Under that, `_chapter_wide_rise` keeps the cap-top-to-numeral
  alignment: the first cap top stands on the numeral column's ink top, the step stays at most 1.20 P, and the last
  baseline stands above the numeral's baseline rather than the lines opening further. The kicker, divider, gutter
  (numeral column 325, divider 391, title x 459, measure to 1766) and W2 (no line ends on THE/OF/AN/A...) are
  unchanged.
- 9:16: no change to the ladder (136..84) or C6's two-or-three-line rule. The 32-character cap was the only reason
  the long acts were refused.
- No per-video title size existed before (act 2 152, act 3 176 on 16:9), so none was added.
- There is no pipeline caller that picks chapter cards per video: `video_composer` does not call `design_cards`.
  The only caller that dropped acts was this evidence script: `render_evidence.py` rendered an act with
  `fits_chapter` false (the catalogue's `1 <= len <= 32`, from `fixtures/build_fixtures.py`) on one ground only.
  It now renders every act on every ground. The catalogue flag in `fixtures.json` is left as it was recorded.

## Per act (Richat long run, `run_richat_structure_sahara_eye_1792740961_long`, 5 acts)

| Act | Title (chars) | Before | 16:9 after | 9:16 after |
|---|---|---|---|---|
| 1 | Introduction: A Desert Landmark from Space (42) | refused on both | 144 px: INTRODUCTION: A DESERT / LANDMARK FROM SPACE (top-aligned, last baseline 14 px above the numeral's) | 100 px: INTRODUCTION: / A DESERT LANDMARK / FROM SPACE |
| 2 | Documented Physical Architecture (32) | drawn | 152: DOCUMENTED PHYSICAL / ARCHITECTURE (unchanged) | 128: DOCUMENTED / PHYSICAL / ARCHITECTURE (unchanged) |
| 3 | Origin Debates and Rock Dating (30) | drawn | 176: ORIGIN DEBATES / AND ROCK DATING (unchanged) | 120: ORIGIN DEBATES / AND ROCK DATING (unchanged) |
| 4 | Archaeological Evidence and Refuted Claims (42) | refused on both | 128: ARCHAEOLOGICAL EVIDENCE / AND REFUTED CLAIMS (top-aligned, last baseline 47 px above the numeral's) | 100: ARCHAEOLOGICAL / EVIDENCE / AND REFUTED CLAIMS |
| 5 | International Heritage Recognition (34) | refused on both | 152: INTERNATIONAL / HERITAGE RECOGNITION (spans the numeral) | 128: INTERNATIONAL / HERITAGE / RECOGNITION |

Every 16:9 card has two lines. On 9:16, C6 chose three lines for acts 1, 4 and 5, as it already did for act 2.
Three lines set at least 16 px larger than two: act 1 two lines would be 84 px, and act 4 two lines need 76 px,
which is below the 84 px floor. No line ends on a stop word. Every size is at or above the floor (16:9 96, 9:16
84). The 9:16 blocks stay inside 288..1058 with the title's right ink at <= 888, and the 16:9 title stays at or
before x 1766.

Rendered on the run's five grounds (dark 14, bright 08, busy 05, hero 01, plain). Before: 30 of 50 cards refused
(acts 1, 4, 5 x 2 formats x 5 grounds). After: 50 of 50 drawn, and acts 2 and 3 are unchanged in size, lines and
drawn contrast. Drawn contrast minimum on the last frame: 16:9 7.43-9.94, 9:16 7.01-9.35.

## Upper bound (measured)

A title is refused only when it does not fit: two lines at 96 px on 16:9, or C6's lines at 84 px within the
9:16 box. Measured on joins of the real act-title words, the longest planned title is 62 characters on both
formats. The shortest refused join is 54 characters on 9:16 and 56 on 16:9; the exact limit depends on the words.
The 75-character join of acts 1 and 2 is refused on both formats (test). A W2 stop-word refusal can still occur
when every break ends on a stop word. A synthetic join, "Landmark from Space Documented", hit it; no real act
title does.

## Files

- `chapter_<fmt>_act<n>_hero__last.jpg`: all five acts, both formats, on the hero ground, before | after.
- `chapter_<fmt>_act{1,4,5}_dark__last.jpg`: the three formerly refused acts on the dark ground.
- `manifest-before.json`, `manifest-after.json`: render_evidence.py manifests (all 50 cards each).

Made with `render_evidence.py --only chapter` on a HEAD (ec4e9e5) worktree and on the working tree, with
`RR_FIXTURE_REPO` set to the main checkout for the run photos, then `compare.py`. The labels come from the script:
real act titles, test-gated, test-signed fields.
