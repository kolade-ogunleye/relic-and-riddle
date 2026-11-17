# QF-82: covers, final + grid only, bandit frozen

Spec (project-quality-review-2026-11-05, QF-82): variants a/b/c were rendered on every run though the selector
ignored them, and the bandit scored covers on Reel-content signals. Render final + grid only; freeze the bandit until
a cover-specific signal exists (YouTube long-form CTR or a manual Instagram test). Acceptance: no cover_a/b/c in a run
folder; bandit `choose` returns the fixed style while frozen.

## What changed

- `VisualEngine.generate_thumbnails` draws only `thumbnail_cover.jpg` and `thumbnail_profile_grid.jpg`. `variants`
  now defaults to False and is ignored (older callers keep working). Variant files that an older run already holds
  are left alone. The final cover's fit rules, focus, grid proof, grid repair and layout record are unchanged, and
  so is its encoding (q95, as on the base; the QF-13 / QF-101 cover-encoding rulings are not touched).
- `cover_selector.choose` returns `config.COVER_FIXED_STYLE` ("badge") whatever the audience numbers, neighbouring
  styles or seed, and never calls `learning.choose`. `for_run` no longer reads the audience table, the recent runs'
  styles or the hero focus, because none of them may move the choice. There is no environment switch to unfreeze
  it. `fitting`, `reward` and `evidence` stay (the renderer uses `fitting`; the other two describe history).
- The Covers panel (`/covers`, the review card's Covers button) recognises a run that holds only the final cover.
  It shows the final cover with a Final cover / Profile-grid tile toggle (👁 on the one shown) and the usual back
  buttons. The Telegram cache key is bound to the file's bytes, so a redrawn cover is never served from an older
  upload. Runs that still hold A/B/C variants keep the old Lock screen unchanged. Before this change, a final-only
  run had "no covers yet": the panel skipped it and opened an older variant run instead.
- Documentaries stay out of the Covers panel (review 1). A documentary has always held only a 9:16 cover and grid
  tile, but it posts its 16:9 thumbnail, so the final-only rule would have shown an image that is never posted.
  `control.is_documentary_run` recognises one by its delivery profile, its content format, a recorded cover path
  naming another file, or (when the folder has no run row) its documentary master. `/covers <documentary>` says
  that it posts its 16:9 thumbnail, which its review card shows. This is chosen over showing the recorded 16:9
  thumbnail in the panel, because that picture has no grid tile and nothing to choose, and it is already on the
  documentary's review card, which deliberately has no Covers button. Real data: 1 dev folder is a documentary.
  On base it opened the Lock screen with unposted 9:16 variants; now it is excluded. The production sample holds 0.
- The digest's cover-style line now says what it is: Reel-content medians grouped by cover style, not cover
  performance.
- The `/covers` description changes to "Preview a video's cover and profile-grid tile".

## Measurements

`measure.py <repo root>` (offline; generated geometric hero; real renderer and selector):

| | base 5c7840e9 | this commit |
|---|---|---|
| cover JPEGs written to a new run folder | 5 | 2 |
| unused variant files | 3 | 0 |
| fixed choices out of 24 against misleading Reel rewards | 0 (all depth_sandwich) | 24 (badge) |
| final cover and grid tile | 1080x1920, 1080x1080 | byte-identical to base |

`before.json` and `after.json` hold the two outputs.

`measure_real.py <repo root> <output dir> [--assets DIR] --against <base root>` re-renders every real cover that has a
recorded recipe, with both commits, and counts what the run folders hold. It prints counts only, so it can run on a
copy of the production sample.

| | dev (output/, 47 runs) | production-sample copy (34 runs) |
|---|---|---|
| runs with a cover / with A/B/C variants | 32 / 32 | 34 / 34 |
| variant files on disk (total, median per run) | 96 (71.4 MB, 2.38 MB) | 102 (80.0 MB, 2.35 MB) |
| covers re-rendered (recorded recipe and script; skipped: no record / no hero) | 7 (25 / 0) | 18 (15 / 1) |
| cover JPEGs per re-render, base -> this commit | 5 -> 2 | 5 -> 2 |
| variant files written, base -> this commit | 21 -> 0 | 54 -> 0 |
| delivered cover / grid tile / recorded style identical to base | 7 / 7 / 7 of 7 | 18 / 18 / 18 of 18 |
| median cover render time, base -> this commit | 12.2 -> 3.8 s | 15.0 -> 5.4 s |
| delivered cover byte-identical to one of its variants | 24 of 32 | 26 of 34 |
| ... to a variant of another style than the recorded one (a manual Lock) | 0 | 2 |
| recorded delivered style: badge / depth_sandwich / lower_third / minimal / no record | 1 / 1 / 2 / 3 / 25 | 5 / 4 / 5 / 5 / 15 |

`real_dev.json` and `real_production_sample_counts.json` hold the outputs (counts only).

Evidence sheets (half-size, a real development run's cover drawn again by each commit, in its recorded style):
`before_run_folder.jpg` (base: five cover files) and `after_run_folder.jpg` (this commit: the final cover and its
grid tile, the same pixels). Rendered by `render_evidence.py <repo root> <run dir> <out.jpg>`.

## Downstream effects (every consumer of the cover files)

- Review card and posting: both use `thumbnail_cover_path` / `thumbnail_cover.jpg`. Unchanged, and byte-identical
  on every re-rendered real run.
- Profile grid: cut from the final cover as before. Byte-identical.
- Covers panel: see above. The legacy `cov:` / `covlock:` / `flip_cover` paths stay for runs that already hold
  variants. For a new run there is nothing to lock, so the operator can no longer swap a run's cover for another
  style from Telegram. The spec intends this, but it is a real loss of a manual control: in the production sample
  2 covers are byte-identical to a variant of a style other than the recorded one, i.e. the operator locked a
  different variant. A follow-up could offer an on-demand redraw in another style from the review card (not in
  this row).
- Backup: the GOLD_FILES list still names the variant files and copies them when an older run has them. For a new
  run the glob finds nothing. No change is needed.
- Analytics (`audience.cover_style`): reads the style recorded beside the delivered cover, which is "badge" (or the
  grid-repair fallback) from now on. The digest line is relabelled.
- Bandit: frozen (above).
- The grid as a set: every new cover will be badge, apart from render-time fit/grid fallbacks. The neighbour and
  paper-card rhythm rules no longer vary the grid. This is the intended freeze, but it makes the profile grid more
  uniform than it was. Of the recorded delivered covers, 1 of 7 (dev) and 5 of 19 (production sample) were badge; a fixed
  style would have changed the rest.
- Parked design work (`freeze-park`): its V1 (variant dedupe in the variant loop, buttons only for existing
  variants) and C9 (delivered_style credit in `evidence`) are superseded. When the park is rebased, the V1 hunks
  conflict with the removed variant loop and should be dropped, together with the park tests that render variants
  (`variants=True` cases in its freeze-covers tests). C9's record of the drawn style is still useful for analytics,
  but the bandit no longer reads it.

## Tests

- Regression: `tests/test_qf82_cover_freeze.py`. Base: 12 failed, 2 passed (review 2 count). This commit: all 14 pass. The 3 documentary cases (review 1) also fail on build 1's a500959e.
- origin/main test changed with written approval (decisions/QF-82, patch SHA256 816d3255…): `tests/test_covers.py`.
  The three variant existence/size checks become absence checks, and the test is renamed. Final and grid checks are
  unchanged.
- Branch-only expectation changes (the freeze replaces sampling and rotation):
  `test_cover_selector_2026_11_01.py` (3 tests), `test_covers_carousel_round2_2026_11_02.py` (the neighbour-rotation
  test, and `for_run` now reads no history).

## Limits

- No cover-specific signal exists yet. This row does not define the unfreeze path; it needs YouTube long-form CTR
  or a manual Instagram test, and a reviewed change.
- Covers still go out at q95 4:2:0 (cover hue belongs to QF-101).
