# QF-136: the visual critic, an advisory check on design render sets

**ADVISORY: the visual critic blocks nothing. 1 of 622 planted defects passed it (0.16%: an accent added over a busy ground); a pass is not a guarantee (operator ruling, decisions/FREEZE-HARD-LIMITS-2026-11-17.md).**

`src/visual_critic.py` (the checks) and `scripts/visual_critic.py` (command line: `check`, `render`, `plant`, `score`;
`scripts/visual_critic_plant.py` plants known defects). It emits findings and pass/fail per frame and per defect
class. It never scores a design and never decides FROZEN. Its pass/fail ("gate" in report.json) is advice for the
panel, not a block (report.json carries `"advisory": true`).

Round 2 (after the round-1 adversary and review): the gate fails closed. A frame passes only when every character of
its truth is certified from the pixels; anything it cannot judge is a `coverage` finding, and every report lists
what it does not gate (`coverage.not_gated`, `coverage.not_judged`): a pass is never coverage of something unlisted.

## What it checks, and from what

The renderers' records are not evidence for the renderers (GLYPH-DROP: drawable() deleted glyphs while the records
held the signed string). The truth is what a renderer was ASKED to draw, after the documented editorial rules:

| Surface | Truth |
|---|---|
| Story frames | the recipe's slide fields; on the poll frame the body keeps its context and the poll question heads it when the slide has none (multi_format's rule) |
| Carousel slides (IG/FB) | the recipe's card fields (heading, title, body, kicker, subtitle) |
| Covers and their profile-grid tiles | the recipe's copy, after cover_design's withhold rule modelled from the recipe's figure (a range or disputed figure, or a hedged figure whose hedge does not fit, is lifted out with its qualifier and a dangling article); a line the rule leaves as a fragment (cover_design.withheld_fragment, QF-141) is not drawn and not in the truth |
| Cards (hook, chapter, myth; harness) | the signed card fields |
| Graphics | the run's lineage inputs (`<clip>.lineage.json`, or the harness's copy of them): the map's place name. Number cards and timelines have no truth reader yet (the poster shows every card, a value's qualifier set apart; timeline labels are tracked capitals): they fail as `coverage` (QF-136b), never pass on their record |
| Captions | the narration (words.json): every spoken word, in order, once, on screen while it is spoken (0.30 s); a transparent event shows nothing; the reconstruction badge's wording is video_composer's signed label |
| Masters | the captions' own glyphs (libass, the same file, at the frame's own time) against the decoded frame |

| Class | Judged on | How |
|---|---|---|
| coverage | set / frame | No truth (no recipe, a renderer or graphic kind with no truth reader), an unreadable record or picture, a frame that could not be judged, an empty set, `render_errors.json` with errors, a picture beside the set's records that no record names, a graphic without its poster frame, a frame the lander's freeze manifest lists that was not judged (`--manifest`); a field whose face the bundled faces do not reproduce closely enough to tell its letters (its placed words fit under 0.92) |
| text_identity | pixels | Every truth word as drawn (its punctuation, digits and accents; no length floor) is shaped here (raqm/HarfBuzz, bundled fonts), found in the field's face, size and tracking, in reading order (on the line after the previous word, or the next line), and certified letter by letter: the pixels must be explained better by the truth than by each of its one-edit variants (every other letter, digit, accented form or sign put in each place, each character dropped, each pair transposed), the two drawn from the same origin at sub-pixel phases with the word's own ink and ground tones fitted, confirmed on the letter's stretch; each character's core must carry ink (a dropped dash or apostrophe); numerals digit by digit in their slots. Extra: a truth word found again (a duplicated line; on a card anywhere for words of 5+ letters, else inside the truth's recorded boxes), ink between or after a line's words that is none of its words. A face the truth names must be the one in the pixels. Captions: the narration as above. Masters: no run of 12 px of the glyphs without their fill, and none of fill beside them (registered within a pixel) |
| record_mismatch | pixels / record | A found word that no record box holds; record text that is not a run of the truth's words; a record face other than the one the pixels show |
| text_cut | pixels | A found word crossing the frame edge; a word touching a frame or zone edge whose drawing puts ink past it (a column cut away), and a first or last glyph whose outer columns lack their ink there; caption glyphs (libass) at the frame edge |
| safe_zone | pixels | The found field box against the still's recorded zone, else the repo's safe-zone table, naming the platforms whose interface covers it; caption glyphs and the master's captions against the Reel box |
| overlap | pixels | Two fields' found words over each other in a still or card |
| plate_overrun | pixels (libass) | Caption and badge glyphs against the plate drawn for them (0 px) |
| glyph_fallback | pixels (libass) / record | A caption family libass never resolves; a stand-in face a still recorded |
| contrast | pixels | WCAG on the delivered pixels with each placed word's own glyph mask (typography.ring_contrast's measure), every placed word; a word too faint to tell its letters is a text_identity finding |
| mark_contrast | record | An essential mark's recorded ratio under 3:1 |
| face_overlap | pixels (GPU) | A found field over 10% or more of a face the pipeline's YuNet finds |

Outputs: `report.json` (deterministic; `gate`, `per_class`, `coverage` {findings, not_judged, not_gated}, every
frame's findings with basis, value and threshold), `timing.json` (seconds, face time, peak memory per worker and in
all), `index.html`, `sheet/`. The gate passes only with no failing frame, no coverage finding, at least one frame
judged and faces checked.

## Results (round 3, the critic's last round)

Acceptance for a gate (operator, 2026-11-17): zero false passes on the sweep-scale planted sets, controls passing,
0 false fails on the 150-frame real set, CI green, bounded memory. Measured below; where a bar is not met the critic
lands as ADVISORY (non-blocking).

### Real renders (`real_set.json`): the controls

150 frames from this tree's renderers on real run inputs: the Story frames and carousel slides of 6 runs, 5 covers
with their grid tiles, 16 cards, 12 graphics and 10 caption files.
- 0 false fails.
- 8 frames fail: the number cards and timelines, which fail as uncovered (no truth reader, QF-136b).
- Wall time 1332 s at 4 workers.
- Peak memory: 5.6 GB in all (main 0.38 GB, workers 1.24-1.37 GB).

### Planted defects at the sweep's scale (`planted_real.json`)

622 cases are planted on copies of the real frames. The text changes are made by the renderer itself (14 mutations
x 20: letters dropped, changed or transposed; short words dropped; words swapped or added; a prefix; capitals;
punctuation dropped; accents added; digits dropped or changed).

| class | planted | frame fails the gate (false passes) | found as its own class |
|---|---|---|---|
| text_identity | 280 | 279 (1) | 267 |
| coverage, contrast, overlap, mark_contrast | 40 each | 40 each (0) | 40 each |
| record_mismatch | 40 | 40 (0) | 39 |
| text_cut | 40 | 40 (0) | 35 |
| face_overlap | 34 | 34 (0) | 31 |
| safe_zone | 28 | 28 (0) | 28 |
| plate_overrun, glyph_fallback (captions) | 20 each | 20 each (0) | 20 each |

- **False-pass rate:** 1 in 622 (0.16%). The one false pass is an accent added over the photograph on a carousel
  heading ("LÍNES"). This is the stated limit: an added accent is judged on a plain plate only.
- **Found under another class:** a "NOT" prefix on a carousel heading is often found as another class, which still
  fails the gate.
- **Other cases:** the captions and masters sweep's cases are regression tests (tests/test_qf136_r3_sweep.py and
  tests/test_qf136_r2_fail_closed.py), not planted at scale.
- **Planted check:** peak 7.4 GB in all at 4 workers; wall time 7595 s.

### Real run folders, read only (`real_run_folders.json`)

Two development run folders, from older renderers:
- **Richat:** 8 of 26 frames fail. Real: the Reel box, contrast and the poll Story title. Coverage: the number card and
  its unjudged clips.
- **Gol Gumbaz:** 11 of 24 frames fail. Contrast. Coverage: unjudged clips and the YouTube thumbnail. text_identity:
  the old poll Story's titles.
- **Not checked by hand:** uncertified older faces now report as coverage, not as wrong words, but the remaining
  text_identity findings on these folders were not each opened by hand.
- **Memory:** peak at most 4.6 GB in all.

### GPU and CPU (`gpu_cpu.json`)

On the 10 planted face frames (11 real faces), the DirectML and CPU reports are byte-identical (round 2).

### Committed inputs only (`measure.py`, `measurements.json`)

- Clean set: 28 frames, 0 fails, nothing unjudged.
- Planted: every case fails the gate in every class.
- measure.py asserts these claims and exits non-zero otherwise.

## Probes from the round-1 adversary and review

Every probe is a regression test in `tests/test_qf136_r2_fail_closed.py`:
wrong short words (TNE / TBE for THE), a wrong digit (321 for 327), a dropped single digit, one- and two-letter words,
dropped en dash, accent and apostrophe, transposed letters, first / last letter dropped, swapped and added words, a
repeated line, 1-3 columns cut at a zone edge, a faint thinned word's contrast, the wrong face, a graphic whose
record and pixels agree on the wrong text, a graphic kind with no truth reader, an empty set, render errors and a
broken picture, a cover with no recipe or an unknown renderer, a picture no record names, a graphic without its
poster, a frame the manifest expects, captions shifted / left out / empty / transparent, a final event outside the
Reel box among few events, a master burned with TNE for THE; the cover withhold rule and its fragment.

## Reproduce

    python docs/reviews/quality-fixes-2026-11/QF-136/measure.py <repo root>

measure.py uses committed inputs only and exits non-zero unless its claims hold (no clean frame fails, nothing in
the clean set unjudged, every planted case fails the gate as its class).

    python scripts/visual_critic.py render <dev checkout> <set> --runs 6 --only social,covers
    python scripts/visual_critic.py render <dev checkout> <set2> --runs 2 --only graphics,captions,cards   (merged)
    python scripts/visual_critic.py check <set> <report> --workers 4 [--device cpu] [--manifest MANIFEST.json]
    python scripts/visual_critic.py plant <set> <report>/report.json <planted> --faces-from <dev checkout>
    python scripts/visual_critic.py check <planted> <planted report> --workers 4
    python scripts/visual_critic.py score <planted> <planted report>/report.json <report>/report.json

## Not gated (the same list is in every report's `coverage.not_gated`)

- graphics other than maps (place name only): number cards, timelines, scale, cross-section, then-and-now, callouts, quote cards fail as uncovered (QF-136b); a graphic's clip is judged on its poster (last) frame only, and a clip with no poster record fails as unjudged media
- map labels other than the place (seas, countries) and map label placement
- text outside the truth's fields: text burned into the photograph (a source's licence banner), the licence credit block, the masthead, a carousel's share line, Story poll option rows and the 'REPLY WITH A OR B' line (extra words are looked for inside the truth's own recorded boxes and on its own lines only; QF-144 crops photo-borne banners at source)
- the cover's badge copy and the thumbnail variants a/b/c (the harness renders covers without variants)
- end card and loop bridge, first frame, chapter / hook / myth cards in run folders (the harness renders them)
- the YouTube thumbnail (unjudged media: it fails as coverage)
- captions: reading speed (CPS) and punctuation (narration words are compared without it)
- colour-vision (CVD) contrast, lone function words, faux capitals
- an accent added over a busy ground (judged on a plain plate only)
- a glyph cut away from a frame or zone edge by under a column of ink (cuts are judged where a word meets a frame or zone edge)
- faces hidden by the text itself (only faces the detector still finds are judged)
- a word burned into a master away from every caption glyph (the master is checked where its captions are)
- captions after an end card's start, taken from the run's composition record, are not owed narration
- libass's own rendering of the caption file is trusted (the .ass is checked against the narration, the master against the .ass)

Review files not judged as deliverables: review_scene_strip.jpg, review_proxy.mp4, review_contact_sheet.jpg.

## Limits

- Letters are certified against the bundled faces: a delivered frame whose face, weight or hinting the bundled faces
  do not reproduce (older renderers, JPEG at small sizes) is reported as uncovered (`coverage`), not passed.
- A cut is found where a word meets a frame or zone edge; elsewhere a glyph cut by a column is not told from its
  face's weight (a word cut mid-frame by a plate is judged by the plate checks).
- Caption identity is the .ass against the narration and the master's pixels against the .ass; libass's own
  rendering is trusted for the caption file.
- The face model can fire on a non-face (a bronze mechanism in one production carousel): a face_overlap finding
  there fails the frame (a false fail, not a pass).
- A master is checked against its captions' glyphs where they are: a word burned into the master away from every
  caption glyph is not looked for.
- Captions after an end card's start (taken from the run's composition record) are not owed narration.
