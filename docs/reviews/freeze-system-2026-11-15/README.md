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

### The incremental board: an iteration aid, NEVER evidence (fenced off, round 3)

**Reuse is an iteration aid, never evidence.** The second independent review (queue/REVIEW2-board-c5.md) made the
incremental board reuse stale images five new ways (R1-R5 below), each passing the gate, because the gate re-derives
from the same DEPS.json as the plan: whatever the record misses, the gate misses too. Lander ruling 2026-11-26: stop
trying to prove reuse safe in this cycle; make it impossible to use by accident.

- `board.py --reuse <dir>` exits 2 with "--reuse refused: full render only; see queue/REVIEW2-board-c5.md" unless
  `--unsafe-reuse-for-iteration` is given too. `run_palette.sh`: `BOARD_PREV` gives the shard timings only
  (`--timings`, no image reuse); the reuse base needs `BOARD_UNSAFE_REUSE_FOR_ITERATION=1` as well.
- With the flag, the board is stamped not for the panel in three places (board.json `not_for_panel`, REUSE.json
  `unsafe`, `NOT-EVIDENCE.txt` listing the copied files), whatever was reused (a NoReuse fallback too). Every tile
  of a copied surface carries a red `REUSED, NOT EVIDENCE` strip above its label, every copied `surfaces/*.png` and
  every sheet (and its half-size copy) carries one above its pixels (PNG text chunk `board_not_evidence`), and the
  changed-surfaces report's `index.md` / `index.json` say which side is an incremental board.
- Refused downstream: `board_reuse.verify` (the gate) proves nothing of a stamped board; `board_reuse.plan` refuses
  it as a reuse base (its marks would travel on); `board_reuse.refuse_not_evidence(board, who)` is the one call for
  any consumer that judges or scores a board. The panel tools live outside the repo (C:\tmp\qf-handoff\tools):
  `panel_score.py` (`--boards`) and `panel7_coverage.py` need the same check; the patch is in the BOARD hand-back.
- A full render is unchanged: no strip, no stamp, the same bytes.

#### Open specification for a future proof (what an independent gate would need)

None of these is closed; a future reuse must close every one, with a gate that does not read the record it checks.

| # | from | what a reuse missed | what a proof needs |
|---|---|---|---|
| R1 | review 2 | another dev checkout with the same run ids: `RR_FIXTURE_REPO` is set by render_surfaces.py itself, reads are recorded as absolute paths under the old checkout | the dev checkout (resolved) and the checkout's identity in the key; every read keyed relative to it and re-hashed under the new one |
| R2 | review 2 | the recorder's own function bodies (`board_deps` wraps os.stat, os.path.exists, os.environ, the audit hook) run in every job, keyed by skeleton only | the deps module itself, whole, in every job's key |
| R3 | review 2 | code run by a child process not named python* (`cmd /c python`, bash, uv, a console-script .exe): imports never recorded | child-process code: any child that can run tree code is "never", or its own record joins the job's |
| R4 | review 2 | an environment variable read in bulk (`os.environ.items()`) that was unset in the previous board | the environment in the key (every variable, or a fixed allow-list the render is run under) |
| R5 | review 2 | a native read (FreeType, libass, fontconfig, libsndfile, files named inside an ffmpeg concat list) of a tracked file outside the global folders | content-hash against truth renders: the copied file compared with a fresh render at this commit, not with the record |
| R6 | review 2 | the clock: `then_now.kicker_word` reads the current year (NOW / LATER flips in 2037 for the fixtures' years) | the date in the key, or a fixed clock in the render |
| R7 | review 2 | installed packages keyed by name==version: a package file edited in place or a reinstall with other native DLLs | the installed files' hashes (site-packages, the native libraries) in the environment record |
| 1 | review 1 | tracked data under data/ | (keyed today as tracked-data; keep) |
| 2 | review 1 | a tests module's value (a SECTION constant) | (module skeleton keyed today; keep) |
| 3 | review 1 | a module-level import, dunder or method | (module skeleton keyed today; keep) |
| 4 | review 1 | the builder of an on-disk cache (code that ran where the profiler cannot see it) | builders keyed today for the derived-face cache; the class stays open with R2 / R3 |

The gate condition common to all: the content hash of a reused file against a truth render of the same state, with
the environment and the checkout identity in the key, child-process code covered and the deps module itself keyed.
What follows is the record of what the key covers today: a description, NOT a proof.

Renders only the render_surfaces.py jobs whose inputs changed and takes the rest from the previous board byte for
byte (`board_reuse.py`). The rule since the review of 2026-11-26 (FIX FIRST: the first version reused stale images):
**a job's key covers everything its render can depend on, and what cannot be proven renders.**

- One render process per job (`board.py run_jobs`, on by default; `--no-deps` goes back to shards and writes no
  record). What a job reads, imports or leaves in a module is then its own: the record does not depend on which
  jobs shared a process. Cost: each process loads the tree and picks its runs again (measured under load on a
  12-job board: 70-91 s per job, +22 % on the job seconds; the queue is dynamic, longest job first).
- Precision, by design: a change inside a module renders every job that ran ANY function of it; a change to
  module-level code renders every job that loaded it; a change to tracked data outside tests/ and docs/ renders
  everything. Reused: jobs that only loaded a module whose function bodies changed, or never loaded it.
- The record (`board_deps.Recorder`, `<out>/DEPS.json`, schema board-deps/2), per job: the functions that ran (every
  thread), every tree module loaded, every import asked for, every file read (in Python, by sqlite, or named to a
  child process, inside an ffmpeg filter string too), every path probed (os.stat and its family), every folder
  listed, every environment variable looked up, every file it built that outlives it (with the build's stack).
- The key (`board_deps.job_key`, from git objects at any commit):

  | input | how the key covers it |
  |---|---|
  | code the job ran | the WHOLE module of every function that ran (not the function); render_surfaces.py alone is cut by job closure |
  | module-level state: imports, constants, tables, class bodies, method names, decorators, defaults | the module-level code of every module the job loaded, whether or not a function of it ran |
  | a tests module's constants (a spec the harness imports) | the same: the module is loaded, so its module-level code is in the key |
  | a new module that shadows one | every tree .py file an import the job asked for could resolve to |
  | tracked data: data/ (json, data/geo, the tagger), fonts/, assets/, src/ data, tracked tools/ files, uv.lock, pyproject.toml, anything at the root | every such blob, in EVERY job's key (readers outside Python exist: FreeType, ffmpeg) |
  | tests/ and docs/ files (fixtures, fixture media) | the blob of each one the job read; presence of each path it probed; the names of each folder it listed |
  | a .py file read as data (font_assets keys its cache on its own bytes) | its blob, as data |
  | the board's own scripts (render_surfaces.py, run_surfaces.py, board_speed.py, board_inputs.py, fixtures.py) | modules like any other |

- Checked on the disk by the plan (`board_reuse.inputs_now`), per job: every file read outside the tree (the dev
  runs: bytes), every path probed (a file that appeared), every folder listed (a new run), every environment
  variable read, every artefact (still the bytes it was).
- For every job at once (no reuse at all): palette; job list; the toolchain (Python, every installed distribution,
  Pillow's text libraries, numpy, the ffmpeg binary, the machine, the variables native code reads); every file
  under tools/ (sha256); a worktree that differs from HEAD anywhere.
- On-disk caches (the tree's `cache/font_instances`, the temp folder's frame cache): a file a job read or probed
  that git does not track was built by code. `board_deps.resolve` adds its builders to the key of every job that
  touched it: from the job that built it in this board, or from the builders' store
  (`<temp>/rr_board_artefacts/<sha256>.json`, written whenever a build is observed) when it was there before. No
  proof (a cache from before this tooling, or a stored builder whose module changed since): the job is "whole",
  keyed on every module of src/, the fixtures and the board tools. A board started with `BOARD_TMP=<empty folder>`
  (run_palette.sh) and a tree without `cache/` observes every build.
- A job that cannot be recorded whole renders always ("never"): a Python child process, an .env file (never read),
  an input the job rewrote, a folder of more than 5 000 files handed to a child.
- Proof at run time, independent of the key. The static net: when the diff changes a function of a module a reused
  job loaded but never ran, and the code in that job's key mentions the function's name, the job is rendered and
  compared. Plus `--reuse-sample N` (default 2) more reused jobs at random. A difference aborts with exit 3 and
  `REUSE-ABORTED.json`. `REUSE.json` "sample.power" states what this can and cannot catch.
- The gate (`board_reuse.verify`, called by the patched freeze_gate.py) does not re-derive the plan: it reads the
  diff between the two commits itself and checks by set membership that no changed path is among a reused job's
  inputs; re-derives the static net and requires its jobs rendered; proves the sample by content hash (each sampled
  file, rendered at this commit, must be the previous manifest's bytes); checks the disk inputs again; and only then
  compares the keys. tests/test_freeze_board_c5.py makes the plan's key blind and the gate still refuses.
- The composed rows (1, 2, 12, 13, every sheet) are never reused: drawn in every board from `<out>/surfaces` and
  surfaces.json. `board_reuse.composed_inputs` hashes what they are drawn from; equal to a full render's.
- Not seen, said plainly: a file read by native code from a path Python never opened, probed or passed to a child
  (covered only where the whole folder is in every key: data/, fonts/, assets/, tools/); a bulk copy of the
  environment is not a read of each name; files outside the tree read by a native library of its own accord.

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

### Requests from the other cycle-5 streams, and tiers

- SYS-photo-c5-1: `render_surfaces.py` passes each recipe deck's recorded photographs through
  `multi_format.deck_photo_guard` with the run's own photo pool (`carousel_photos.json` `key.stills`) before it
  draws, as production does; surfaces.json `photo_choice` records each slide's reason (a drawing slide keeps its
  poster). On integration 4 the guard replaces no photograph of the four recipe runs.
- SYS-plates-c5-1: a frame's masthead record is read from the tree's own `still_layout.json` `masthead` where it
  keeps one (else from the layout the draw returned); a withheld masthead is a RECORDED OMISSION on the tile and the
  deck's `layout_holds` rows are in the refusal table; surfaces.json `pointer_records` and the Story tile's label
  carry the pointer decision; row 13's masthead table adds `reach`, `margin step`, `margin short`.
- board.json `case_tiers` / `tiers`: each case's production tier (`board_evidence.tier`; T1 published or every run,
  T2 data-gated, T3 behind a flag that is off by default, T4 internal or with no production caller), so scores can
  be reported by tier.

### Review fixes (cycle 5, fix round 1): the decks and the tiers

- The carousel decks are production's own draw. `render_surfaces.py`'s social job draws both copies of every recipe
  run through `MultiFormatGenerator.generate_carousel_sequence`, called as `src/carousel_lazy.draw` calls it
  (`board_decks.production_deck`: the run's verified script, its posters, `strict_match=True`), and the surface is
  the JPEG that call delivers, decoded. The photo match is the one input production makes with a vision call: the
  board gives the run's own `carousel_photos.json` as the kept match and blocks the matcher (`board_decks.no_vision`),
  so nothing paid and nothing on the network runs. Surface names and record keys are the same as before
  (`photo_choice`, `masthead_records`, `rerouted`, `surface_omissions`); `photo_choice[<surface>].drawn_through` says
  which path drew the slide, `.archetype` the archetype it is drawn with (`rerouted` is still the recorded archetype
  against today's routing of the card; a drawing may then take the slide) and, on a Facebook slide,
  `.facebook_post` its place in the multi-photo post (`facebook_pick` over the copy as drawn; the feed collage uses
  it). A drawing's omission rows go to the deck's diagram slide in both copies; the rest stay with the job.
- The photo pool. None of the board's recipe runs has a `carousel_plan.json`, so production's `_checked_stills`
  would hold them today; the board takes the deck's photo pool from the run's `carousel_photos.json` `key.stills`
  (as it takes the match from that file), not from a carousel plan.
- A run whose saved data cannot drive that entry (no `carousel_photos.json`, a photograph gone from disk, another
  slide count, an AI-made picture in the match) is redrawn from its saved recipe as before, and every such tile says
  `REPLAY, not production's draw` with the missing input. A copy production would hold (a refusal) is a REFUSED tile.
- Story frames are still redrawn from their recorded recipe (`_render_story_frame`), and two single slides stay
  direct `_render_carousel_slide` calls on a chosen ground, not decks: row 2's cover slide (the masthead probe) and
  the near-white stand-in's two slides (labelled so). Neither has a run's photo match to drive a deck with.
- The tier is drawn. Every tile's label strip starts with its tier line (`sheet()`, `board_evidence.tile_tier`), in
  the tier's colour, and every sheet saved whole (tables, curves, specimens) gets a one-line strip above it
  (`tier_sheets`); a sheet's PNG text chunk `board_tier` marks it. The surfaces and cases (the judged pixels) are
  never touched. An evidence sheet is T4 and says which tier's surface it measures (board.json `sheet_measures`).
  Every case of every row has a `case_tiers` entry (`board_evidence.case_tiers`).
- Tiers follow the usage inventory (queue SURFACE-USAGE-2026-11-26): the 16:9 end screen is T4 (`end_card.frames`
  has no caller in `src/`), the photo callout is T3 (`CALLOUTS_ENABLED`, code default false; no run drew one).
- Round 3 (lander ruling 2026-11-26): the Facebook slides of a deck that the multi-photo post never carries
  (`facebook_pick`, `photo_choice.facebook_post` None; a replayed deck through the same pick) are never seen by
  viewers: T4 (`board.fb_unposted`, `board_evidence.FB_UNPOSTED`, board.json `fb_unposted`). A deck whose pick
  could not be made keeps its slides at T1. Map, timeline and numbers stay T1 (published).
- Offline: the board process and every compose worker (`board.block_network`, where the Aswan deck and the board's
  own social frames are drawn) refuse socket connects, as render_surfaces.py does, so `board_decks.no_vision` is
  not the only thing between a board path and the paid matcher or the network.

### Wall-clock

`run_palette.sh` takes `BOARD_PREV=<the previous integration's out root>`: its job times balance the shards
(`--timings`); the board still renders in full. Only with `BOARD_UNSAFE_REUSE_FOR_ITERATION=1` (iteration, never
evidence) is it also the `--reuse` base. The measurements
(what limits a board, how many workers, whether palettes may overlap) are in the hand-back.
