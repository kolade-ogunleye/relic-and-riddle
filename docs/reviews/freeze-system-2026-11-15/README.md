# Design-freeze cycle 0: the design SYSTEM (2026-11-15)

FREEZE-PLAYBOOK.md v2 section 1. Built on QF-35a's role tokens (colours, faces, strings): this step names every size,
ratio, alpha, gap, easing and duration the designs share, as tokens read at call time, with the rule that uses each.
The written system is the docstring of `src/brand_system.py`; the tables are below. Trade-off order (binding): gates
and truth > legibility at delivered feed size on the worst hero > subject visibility > one system across surfaces >
hierarchy > rhythm > ornament.

## Where the system lives

- `src/brand_system.py`, three kinds of value (round 2):
  - `GATES`: operator rulings and WCAG / platform limits. Constants; `BrandConfig` refuses a system override naming one.
  - `SYSTEM_TOKENS`: the style values a renderer reads NOW, overridable per brand through `BrandConfig.system`, read
    with `tok()` / `colour_of()` at call time. Each one has a test that overrides it and measures the change in the
    renderer that reads it; `BrandConfig` refuses an override of anything else.
  - `SPEC`: the rest of the written system, the values each group ports its surfaces onto. Not read by a renderer yet,
    so not overridable; a value moves into SYSTEM_TOKENS (with its probe) in the commit that makes a renderer read it.
  - `TYPE` / `TYPE_FALLBACK` / `TYPE_FLOOR` / `FACE` per canvas class (the target ladder), `DEVIATIONS` (every surface
    size system still in force outside the ladder, with its owner group; a test holds each named constant to the
    declared sizes), `SPACING`, the one line-end vocabulary, the named eases and `masthead_spec`.
- `src/masthead.py`: the one masthead treatment. Its ground is solved in the ruled order (nothing; a top-edge INK
  gradient up to 0.60 at its peak; plus the name's halo up to 0.16; plus a levels ceiling under the same shape, never
  under 205; the light-ground INK variant); contrast is judged on the WORST GLYPH (round 3: every ink component
  against the ground under its own ink and its 1-4 px surround, 95th percentile, plus the element's whole and
  windowed rings); under the 5.5 gate after every step, or wider than its room, it FAILS CLOSED (not drawn; the record
  says why, with the best ratio any step reached). The diamond's INK edge is drawn over its gold fill and its
  non-text contrast is recorded (`mark_contrast`, gate 3.0).
- Round 6 (operator ruling 4): the HALO EXCEPTION. Only where no ruled step reaches the 5.5 gate, the bone name takes
  the least halo above 0.16 (0.01 steps) up to 0.24 that reaches it, on the least levelled ground at that halo; the
  record says `halo_exception`. Gate pair `brand_system.HALO_CAP` 0.16 / `HALO_EXCEPTION_CAP` 0.24 (no brand moves
  either). Search order (lander decision 2026-11-16, kept as built): the SMALLEST halo first, then the least levelling
  at it. Ruling 4 says the least halo, only where needed, and the preview panel flags halo clouds around lettering
  (F15, S2), so less halo is the better look; every glyph still meets 5.5. Real runs: the 7 Nazca slides that failed
  closed now draw at 5.50-5.52 (halo 0.17-0.21, levels 205; 7.24-8.11 after the 960 px re-encode); the other order
  (least levelling first) would have drawn them at halo 0.24, levels 210-215, 5.56-5.59. Records:
  `halo_exception_r6.json`.
- Round 6 (Codex r5-1): ONE gate. `masthead.verify` (room, name at the gate, mark at 3:1 on the pixels it changes) is
  passed by every masthead before anything of it is drawn, and `masthead.draw_mark` is the mark's only drawer (carousel,
  FB, YouTube, end card, end screen; the cover and hook paths too). A palette whose GOLD vs INK or INK vs BONE is under
  3:1 is refused by BrandConfig.
- Known limit (round 4 note, Codex r4): for text with COMBINING marks at zero tracking Pillow's raqm draws the run
  whole while `glyph_masks` rasterises each glyph alone, so mark positioning can differ slightly (IoU of the union of
  the shaped masks with the drawn ink 0.909 on the adversary's sample). The judgement partitions the DRAWN ink by those
  masks (every drawn pixel is judged, taken by the nearest glyph), so nothing drawn escapes it; only the attribution
  of a mark's pixels to its base glyph can differ. The brand masthead (RELIC & RIDDLE) has no combining marks.
- Round 5: the mark's contrast is measured on the pixels it actually changed (gold fill vs the ground's darkest,
  INK edge vs its brightest; a part needs 12 visible pixels) and a masthead whose mark falls under 3:1 is refused;
  `TOKEN_JOINT` limits interacting tokens (mark_edge <= 0.35 x mark_r). A content body never shrinks on its own: one
  that does not set in its lines takes a line.
- Round 4: a glyph is a SHAPED glyph (`font_engine.glyph_masks`: each glyph HarfBuzz placed, rasterised alone at its
  position and advance), never an ink component; every numeric runtime token has a range (`TOKEN_RANGES`, refused
  outside it) and the tests sweep masthead tracking over the whole of it.
- `tests/type_sites.py` + `brand_system.SIZE_SITES`: every function of a surface module that sets a type size off the
  ladder, scanned and held equal to the declaration (113 sites at round 7, each with its owner group; aliases, font_variant,
  constructors and the masthead wrapper included). Second, run-time check: `tests/size_observer.py` records every
  font made during the renders (`test_brand_system_r4`, committed declaration `tests/fixtures/type_sizes_observed.json`;
  on real runs `observe_sizes.py`); sites only it can see are in `RUNTIME_SIZE_SITES`.
- `sweep.py` + `sweep_results.json`: the builder's adversarial sweep of the glyph judgement (1,473 grounds, 0 failures).
- `src/brand.py`: `BrandConfig.system` (default `SYSTEM_TOKENS`), `derive(..., system=...)`.

## Gates (constants)

| gate | value | source |
|---|---|---|
| contrast.text | 4.5 | WCAG AA on delivered pixels (= brand.TEXT_CONTRAST) |
| contrast.place | 4.8 | placement aim (= brand.PLACE_TARGET) |
| contrast.display | 5.1 | display rows: covers, hook, YouTube |
| contrast.kicker | 5.5 | small gold capitals (960 px q70 re-encode leaves 4.83-5.01) |
| contrast.masthead_gate / _target | 5.5 / 6.5 | operator ruling round 3 (2) |
| contrast.high | 7.0 | ink figure / source line where ruled |
| contrast.mark | 3.0 | WCAG 1.4.11 (graphics.NONTEXT_MIN and callouts.NONTEXT_MIN now read it) |
| scrim.masthead_peak | 0.60 | ruling round 3 (2) |
| halo.masthead_max | 0.16 | ruling round 3 (2) |
| halo.masthead_exception_max | 0.24 | ruling 4 (2026-11-16): the exception's ceiling, only where no ruled step reaches the gate |
| halo.exception_step | 0.01 | the exception's least-halo search |
| scrim.levels_floor | 205 | park step 5 (BRIGHT_CEILING) |
| scrim.slope_max | 0.5 luma/px | end-card ruling round 3 (1) |
| masthead.grid_cap | 9 px at 124 px wide | QF-87 |
| heading.story_one_line_floor / story_floor | 106 / 120 | rulings round 4 (2) / round 3 (3)(1) |
| motion.caption_cps | 17 | config.CAPTION_MAX_CPS |

## Runtime tokens (brand-overridable; each drives a renderer, each tested)

| token | value | read by |
|---|---|---|
| halo.step | 0.08 | masthead halo search |
| masthead.fit_floor | 0.75 | the fit ladder's floor (x the canvas size; range 0.70-1.0; its exact floor the last step) |
| plate.shade | INK | masthead scrim; callouts' dim and plate shadow |
| masthead.weight / track / mark_r / gap / mark_edge | 700 / 0.0625 em / 0.30 em / 0.36 em / 0.047 em | carousel, FB gallery, YouTube, end screen, 9:16 end card |
| masthead.colour_dark / colour_light | BONE / INK | the same |
| kicker.weight / track / rule / rule_gap / rule_thick / colour | 800 / 0.15 em / 1.0 cap / 0.55 cap / 0.16 cap / GOLD | carousel, Story and FB kickers (draw_kicker, kicker_width); end screen WATCH NEXT |
| motion.back | 1.5 | callouts' pop (ease_out_back) |
| motion.rise_s | 8 px | end screen kicker rise |

## SPEC (the written system the groups port; not runtime yet)

| group | values |
|---|---|
| plates | opaque 1.0; held 0.875; panel 0.78; radius 14 px at 1080 |
| credit / badge | data 500, 0.04 em, STONE_LT / badge role 600, 0.05 em, BONE on INK at the held tier |
| leading | display 1.13 cap; body 1.36 em; label 1.12 em |
| heading | balance >= 0.55; cover >= 1.08 x the deck's largest interior heading |
| figure | en dash centre within 0.03 em of the figures' centre; ink gap >= 0.06 em each side |
| rules | 4 px at 1080 (scaled); hairline 1 px |
| motion | reveal 0.40 s; word stagger 0.055 s; rise 14 / 10 px (heading / line); hook hold 1.07 s, fade 0.16 s; act dissolve 0.5 s; loop crossfade 0.5 s; pop scale 1.4; push 0.0016 / frame, cap 1.24 |

## Type scale (canvas px; carousel = 1080 design px x 4/3 exactly)

| role (face) | tall 1080x1920 | carousel 1440x1800 | wide 1920x1080 | one fallback tier (tall / carousel / wide) |
|---|---|---|---|---|
| display_xl (display) | 150 | 200 | 190 | 120 / 184 / 150 |
| display_l (display) | 138 | 184 | 184 | 124 / 165.3 / 152 |
| display_m (display) | 112 | 149.3 | 136 | 96 / 128 / 112 |
| figure (display) | 300 | 400 | 360 | - |
| kicker (text 800) | 40 | 45.3 | 40 | - |
| body (text) | 46 | 48 | 52 | 40 / 45.3 / 46 |
| label (text) | 30 | 36 | 34 | floor 27 / 36 / 30 |
| data_label (data) | 30 | 36 | 34 | floor 27 / 36 / 30 |
| credit (data) | 24 | 32 | 24 | 22 / - / 22 (floors 22 / 29.3 / 22) |
| masthead (masthead) | 64 (grid-shown covers: absent) | 53.3 | 64 | end screen: brand moment, the largest that fits its group width |

Declared deviations (`DEVIATIONS`, owner group): hook claim ladder 192-112 (F); 16:9 chapter titles 184-120 / 176-96
(F); end card share 58 / 56 / 54 and end screen hand-off 64 / 60 / 56 (E); YouTube headline 190 -> 96 step 4 (B); Reel
cover titles 100-164, floor 96 (B); carousel content heading IG 138 -> 124, FB 150 -> 140 design px (D); Story title
150 -> 106 / 120 (D); then-and-now THEN / NOW 48 px (A).

Spacing (px): margin 84 / 112 / 96; kicker-to-heading 24 / 32 / 24; heading-to-body 36 / 48 / 40; body-to-credit
24 / 32 / 24; unit 4. Safe boxes are `src/safe_zones.py`'s, never re-declared.

## What this commit changes (default pixels)

Measured on real runs in `measurements.md` (158 surfaces, base 6a7ef303 vs head; exact regions of every differing
pixel there). Default pixels change on: carousel and Facebook gallery slides (y 0-513, above 2 levels y 0-490: the
masthead, its scrim and levels to their reach; 49 of 56 drawn at >= 5.5 on every glyph, 7 Nazca slides fail closed and
carry no masthead), the YouTube thumbnail (x 95-815, y 0-176 on the five real covers; a headline shorter than the
masthead also moves the ink shade), the 16:9 end screen (x 154-1517, y 130-259: mark and WATCH NEXT), the 9:16 end card
(x 113-845, y 556-705: the masthead group) and photo callouts (179 of 180 frames, at most 9 levels: the INK dim and
shadow). Identical: captions (incl. the badge frames), the 27 real hook / chapter /
myth card cases, Reel covers and grid tiles, maps, number cards, then-and-now, Story frames and the loop bridges.
A card title that would break on "into" / "onto" / "via" / "per" steps to its next size (none among the real cases).

## Board recipe

`board.py` (this folder) renders the 13 rows of the cycle-0 system board fresh from one tree (the lander runs it; the
builder never renders the panel board): `python board.py <tree> <dev checkout> <out> [--brand second]`. See the
build note C:/tmp/qf-handoff/queue/CYCLE0.build-1.md for the per-row render list.

Round 7 (preview panel 1, F22: the evidence the panel could not judge) adds `board_extras.py`, drawn in the board's
process by the src renderers as the pipeline calls them, on real run inputs (each tile's label says which):
- every label sits in a strip above its tile, never over it (the @246 masthead tiles hid the mark);
- row 1: each role's second line at its line step (`lead.*`), the step printed; row 13: heading/accent, figure face,
  credit, rule, plate/scrim/halo, leading and spacing per canvas, every value read from `src/brand_system.py`;
- row 2: the masthead drawn by each carrying surface's own renderer on the hero (carousel cover slide, YouTube
  thumbnail, end screen and 9:16 end card at rest, through `end_card.frames` with its dim), the renderer's own record,
  and each at 246 px wide (whole 2x, masthead region 6x);
- rows 3-5: the bright Aswan runs (covers: 1792985912; carousel: 1792993374 through `generate_carousel_sequence`,
  photos matched to its delivered slides because it kept no recipe) and the near-white antikythera photo with the
  Aswan copy (STAND-IN: no antikythera run); row 3 adds each Reel cover's 3:4 and 1:1 grid crops and the 124 px tile
  (`row03_reel_cover_grid_crops`); row 5 adds the stat and diagram slides and the feed 1080 and grid 3:4 views
  (`row05_carousel_feed`);
- row 6: `row06_story_safe_box`, the Story box from `safe_zones.safe_box("story")`;
- rows 7 and 11: captions (and the composer badge) burned into the run's own ground at that time through the master's
  `picture_chain` and `delivery_video_encoder` (a 0.2 s clip, decoded as BT.709); row 11 also shows then-and-now and the
  callout (the per-kind hero filter no longer drops kinds no hero has) and the cover and thumbnail disclosure badge
  (FORCED: no run has had an illustrated hero);
- row 8: the hook card at frames where its claim is held (frame 0 and the last full-alpha frame), not the clip's last
  frame, after the type has faded.
A failed extra is recorded in `board.json` `errors` and the board goes on.

Round 8 (official panel 1, consensus 8; lane BOARD; evidence only, no design change):
- `board_motion.py`: production-encoder clips in `<out>/clips` (listed in `board.json` `clips` with frames, seconds,
  sha256 and the encoder line) for rows 7 (captions), 9 and 12 (16:9 end screen, 9:16 end card, loop bridge with
  FOLLOW into the Reel's frame 0, the hook, the act dissolve). Each sequence is drawn once, encoded once by
  `delivery_video_encoder` and decoded once; the row-12 strips are labelled with real frame times and come with their
  luma / alpha curves (`row12_motion_*_curves.png`), the end card's measured rise, the hook's `_hook_alpha` and the
  dissolve's measured mix; the ease plot has real axes; row 9 adds FOLLOW where it is held and the end card at rest.
- `row13_motion_tokens.png/json`: every motion value the tree holds (brand_system `motion.*` by kind, the eases with
  `motion.back`'s overshoot, the renderers' timing constants, the act dissolve's frames, the caption ASS's word ease
  and plate fade, and every timing call in the end card, bridge and hook code, with its line).
- `row07_captions_cps.png/json`: per caption case and burst, on-screen seconds, characters, shown CPS, the spoken
  window, the word it ends on (never-end words flagged) and the composer's own `last_reading_rate`.
- `board_evidence.py`: every tile's label carries its pairing class (STAND-IN / FORCED) and a probe kind without a
  rule stops the board; the caption grounds are drawn by the tree under review (a stored graphic frame is re-drawn);
  `refusals_<brand>.png/json` is the palette's refusal table (surface, what, reason); `peer_feed_<feed>.png` is a
  feed sheet per feed surface at real pixel size (`--peers <dir>` with `<dir>/<feed>/*.jpg`; without it the slots
  hold the channel's other runs, labelled STAND-IN).
- `render_surfaces.py` passes the Story's one title size as production does (`story_title_px` over the Story) and
  records it (`story_title_px` in surfaces.json); a cover's Reel/grid and its YouTube thumbnail, and each social frame,
  are recorded on their own (one refusal no longer drops its siblings).
- Time: `board_speed.py` memoises the end card's ground inside its line gate (exact: same frames and stats, checked);
  `run_surfaces.py` starts render_surfaces.py with it; every row's time is printed and kept in `board.json` `seconds`.

## Acceptance

`python measure.py <repo root>`: the masthead on six committed grounds at the three carried sizes, all within the
rulings (peak <= 0.60; halo <= 0.16, or <= 0.24 where the record says `halo_exception` (ruling 4); the light variant's
lift <= 0.60; worst letter >= 5.5 on the unrounded value or not drawn; the fit ladder down to `masthead.fit_floor`
(0.75, its exact floor its last step)), and the system's counts.
`tests/test_brand_system.py` holds the invariants (gates constant; every runtime token overridden and its renderer's
change measured; no import-time copies; declared deviations held to their constants; one line-end vocabulary; named
eases bounded and shared; the masthead within the rulings, judged letter by letter on the adversary's grounds with
its own independent probe, failing closed and recorded by every carrying surface; the mark's edge and non-text
contrast; the system kicker on the end screen).

## The review loop (cycle 5, lane BOARD; evidence tooling only)

A cycle should measure designs, not wait for a board or guess what changed. Four tools, each proven on a subset
(the lander renders the full board; numbers and the proofs' data are in the hand-back, queue/HANDBACK-board-c5.md).

### The incremental board: `board.py ... --reuse <previous board of the same palette>`

Renders only the render_surfaces.py jobs whose inputs changed and takes the rest from the previous board byte for
byte (`board_reuse.py`; the composed rows are drawn fresh from the merged surfaces).

- What a file's input hash covered before: its path, the palette, the WHOLE tree stamp and the union of every data
  file the board read. Every file had the same inputs and any commit changed them all, so nothing could be reused,
  and the toolchain and the tree's untracked data (tools/geo, tools/models) were not in it.
- What a job's inputs are now. Code: the job's code key (`board_deps.py`). Not code, and any change means no reuse at
  all: a non-Python file under src/, tests/ (only one a render read), fonts/, assets/, the fixtures catalogue,
  uv.lock, pyproject.toml; a recorded data input (re-hashed); tools/geo, tools/models, tools/stress; the toolchain
  (Python, Pillow and its text libraries, numpy, the ffmpeg binary and build line); the palette; the job list.
- Why the code key is observed, not derived. Measured at integration 4 (`python board_deps.py <tree> --measure`): the
  import closure of every render entry is 161-164 of the 177 src modules, and a name-based symbol reach gets to
  7 312 of 9 495 symbols from any single function. A module-level or a static symbol-level map would never reuse
  anything. So each render process records, per job, the functions that ran (cProfile, started by `run_surfaces.py`
  before the tree loads; `render_surfaces.py` marks each job's window): those that ran in the job, those that ran
  outside every job (import time, the harness), and everything those ever called in that process (a memoised function
  that returned a cached value is followed to what computed it). The record is `<out>/DEPS.json`. The key hashes
  those functions' ASTs, every module-level value and class header they name (in any module, transitively) and every
  module's import-time residue, at any commit, from git objects.
- The record is on by default (`--no-deps` turns it off; such a board cannot be reused later). It changes no pixel.
- Proof at run time: `--reuse-sample N` (default 2) reused jobs are rendered anyway and must equal the previous
  board's files and records; a difference aborts with exit 3 and `REUSE-ABORTED.json`.
- Records: `REUSE.json` (the checks, each job's status and the symbols that changed, each reused file), `DEPS.json`,
  board.json `reuse`, `reuse_env`, `job_code_keys`, `tree.path`; INPUT_HASHES.json gives each surface the hash of its
  own job's inputs (equal on two boards exactly when the surface may be reused).
- The gate: the tools patch of this cycle makes freeze_manifest.py write each file's `provenance` (rendered / reused)
  and freeze_gate.py call `board_reuse.verify`, which derives every reused job's key again from git objects at both
  commits and checks each reused file against the previous board's manifest. A reused file keeps its render's mtime.
- Not covered by the key (the sample is the net): state one job leaves in a module global for another job's
  different function; an attribute fetched by a name that is in no recorded code; exec/eval.

### The changed-surfaces report: `board_diff.py <before board> <after board> <out>`

`index.md` / `index.json` per row (changed, added, removed, unchanged, reused; the changed pixels' box, their share,
the mean difference) and one `pairs/<row>/<stem>.jpg` per changed image: BEFORE | AFTER at 1:1, side by side when
both fit in `--max-width` (2400), else one above the other; a changed clip gets a strip pair of the same frames of
both clips with the per-frame difference. Row sheets are listed and paired only with `--sheets`.

### Evidence that was missing (panel 6)

- `cases/<name>.png`: every case the board draws itself at full size (bright and near-white covers, the Aswan deck,
  the hook's held frames, the frames taken from clips, the badges); board.json `case_files` maps EVERY row case to its
  file (`surfaces/...` or `cases/...`; None for a refused or raised tile). `surfaces/card__hook__*__916.png` is the
  cards job's LAST frame (after the fade): the claim is on `cases/card__hook__<ground>__916__f00/f32.png`.
- board.json `masthead_records`: the masthead record of each board-drawn social frame (the Aswan deck's seven slides,
  the near-white slides); row 13's social masthead table includes them.
- `clips/hook__<ground>__916.mp4` and `row12_motion_hook__<ground>` for every ground the hook draws on (the Amber
  ground beside the obelisk).
- Per caption fixture, the whole run: `clips/captions_run__<topic>__<tag>.mp4` (the master's picture chain and the
  delivery encoder), `row07_captions_run__<topic>__<tag>.png` (the caption band at 1:1 on the frame 0.08 s before
  each burst leaves) and `..._bursts.png/json` (per burst: seconds on screen, characters, characters per second).
- `exp_cases.py` expects cycle 5's files (`--drop-cycle5` for a tree before them).
