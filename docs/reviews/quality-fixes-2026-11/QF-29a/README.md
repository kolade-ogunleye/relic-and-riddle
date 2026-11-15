# QF-29a: golden images for frozen designs (the freeze lock)

Row QF-29 ("fold dated tests; golden images") is split on 2026-11-15 (freeze plan, decisions
FREEZE-EFFICIENCY-2026-11-15 and FREEZE-PLAYBOOK v2): **QF-29a = golden images** (this commit); folding the dated
tests stays in QF-29, todo. Base: e49616fc (QF-134).

The playbook: "On agreement: QF-29 golden taken; any later change touching the design fails CI"; the post-freeze
change path is critic + diff boards + one lens on the changed frames. No design is frozen yet, so nothing is golden'd
here: the mechanism is proved on two proof fixtures.

## What it is

- `scripts/goldens.py` (library and CLI): `take`, `check`, `board`, `accept-toolchain`, `fingerprint`, `list`.
- `tests/goldens/designs.py`: the registry (empty). A design is added when the operator agrees it is FROZEN, then
  `scripts/goldens.py take <design> --frozen-on <date>` writes `tests/goldens/<design>/manifest.json` (design,
  surface, case, format, kind, freeze date, commit, pinned inputs with SHA-256, settings, accepted toolchain
  variants) and the reference images.
- `tests/test_goldens.py`: one test per frozen design (shard-friendly for QF-135: separate test ids, no shared
  state, no skip; with no design yet a single passing placeholder) plus a store-consistency test (every registered
  design has a golden and vice versa, manifests whole, inputs committed and unchanged, no stray files).
- `tests/golden_proof_designs.py`: the two proof fixtures, rendered by the real design code from committed inputs:
  - still: the YouTube thumbnail (`youtube_thumbnail.render`) with the Gol Gumbaz run's committed cover copy
    (`tests/fixtures/ui02_review_v2/.../script_metadata.json`) over a committed real archive-film frame
    (`docs/reviews/archive-film-frame-2026-10-28/before_fill_crop.jpg`; not that run's hero, which is not committed:
    a mechanism proof, not a design judgment);
  - video: the first half second of the 9:16 end card (`end_card.frames`, that run's share line) through
    `graphics._encode`, the production encoder of QF-134 (libx264 slow crf 18, BT.709), frames 7 and 14 sampled.

## How a check decides

1. Pinned inputs must hash as recorded and be committed, never gitignored (`input_problems`: `git check-ignore`,
   `git ls-files --error-unmatch`); a changed input is "input-changed", never compared.
2. The design renders in a clean subprocess: whitelisted environment (OS/PATH/temp only, `PYTHONHASHSEED=0`), the
   checkout's `.env` ignored (development's `.env` cannot move a golden), the design's own `settings` only.
3. Exact: the SHA-256 of every frame's full-resolution pixels (RGB for stills; the decoded yuv420p for video: H.264
   decoding is bit-exact, so no colour conversion sits in the exact path). Same toolchain fingerprint as an accepted
   variant and any pixel different -> **design-changed** (re-validate, then retake).
4. New toolchain fingerprint -> **toolchain-changed: re-validate**, always a failure, whatever the pixels. The
   message names each changed component and says whether the pixels equal an accepted variant (then
   `accept-toolchain` records the fingerprint with no new image), or how far they are under the tolerance.

The fingerprint (hard: a new value fails the check) holds the rendering libraries only: Pillow's
FreeType/raqm/FriBiDi/HarfBuzz/libjpeg-turbo/zlib/lcms2/WebP versions and the package versions (Pillow, NumPy, OpenCV,
skia-python, uharfbuzz, fontTools, SciPy; all pinned by uv.lock); for video also the ffmpeg version line and the
executable's SHA-256 (libass, libx264, FreeType, HarfBuzz and FriBidi are linked into the build; ffmpeg prints no
libass version, so the binary hash pins it) and x264's settings string read from the encoded stream (core version,
every option and the thread count; a drift message names the differing options, e.g. `threads=48 -> 6`).

Information only, recorded in every variant and candidate but never compared (round 2, lander's ruling: the hard
fingerprint holds only components that can move pixels): the Python release, the platform, NumPy's active SIMD
dispatch and the CPU count. Measured: NumPy with X86_V3 disabled (`NPY_DISABLE_CPU_FEATURES=X86_V3`, dispatch
[X86_V2] instead of [X86_V2, X86_V3]) renders both proof fixtures pixel-identical (still a501d4c9...; video f7
0dfdace8..., f14 cc7f7eb3...), as does Linux vs Windows for the still. So GitHub runners of mixed CPUs do not turn
CI red; a pixel change they did cause would still fail, as design-changed.

### Accepting a toolchain (rounds 2 and 3)

Every render records its tree state: HEAD and every change except golden data (tracked or untracked), in the
candidate's `variant.json`. `accept-toolchain`:
- pixels identical to an accepted variant: accepted from any commit (nothing visual can have been re-baselined);
- pixels different: needs `--revalidated "<who checked which board>"` AND a candidate rendered on a clean tree at a
  commit whose files equal the golden's commit in everything but golden data (the commit that adds or re-takes the
  golden images, or the golden's own). Any other origin is a design change: refused ("that is a design change, not a
  toolchain"), re-validate and retake. A dirty tree, a candidate without a recorded commit and a commit not
  available locally are refused as well; `--from` a folder without `variant.json` is a clear error.
- the accepted variant records the candidate's commit and tree state and its frame stats against the taken golden.
- `take` refuses a tree with any change but golden data, so a registry entry must be committed before its golden
  is taken (the golden's commit contains it).

What counts as design code under `tests/goldens/` (round 3; `scripts/goldens.py is_golden_data`, the same rule in
`tree_state`, `design_tree_changes` and `take`'s clean-tree check):

| file | counts as design code? |
|---|---|
| `tests/goldens/designs.py` (the registry: each design's render function, inputs, samples, settings) | **yes** |
| `tests/goldens/<design>/manifest.json` (a taken golden's record) | no: golden data |
| `tests/goldens/<design>/*.webp` (its reference images, `<label>.webp`, `<label>.v<k>.webp`) | no: golden data |
| any other file under `tests/goldens/` (a README, a `.py` in a golden folder, a nested folder) | **yes**; and `tests/test_goldens.py` fails on any such file in a golden folder or loose in the store |
| `tests/goldens/__pycache__/` | gitignored: never in a commit or in `git status` |

Today the store holds exactly one file, `tests/goldens/designs.py` (design code; the registry is empty).

Probes now refused (`test_a_design_change_is_never_accepted_as_a_toolchain`, synthetic commits in a private object
directory: nothing is written into the repository): review round 1's one-level BONE edit, in the working tree and
as a commit of `src/brand.py`; review round 2's commit that only edits the registry (`tests/goldens/designs.py`
wraps the still's render with GOLD (242, 182, 48) -> (242, 182, 49)). Still accepted: a commit that only adds a
golden's `manifest.json` and `.webp` images. `test_only_golden_data_is_exempt_from_the_design_tree` pins the
classification and shows an uncommitted registry edit makes a render dirty and stops a take.

## Measured (measure.py, before e49616fc -> after)

| | before | after |
|---|---|---|
| golden mechanism | none | take / check / board / accept-toolchain |
| planted changes caught (1-px shift, six-level colour; still and video) | 0 of 4 | **4 of 4** (exact) |
| toolchain changes caught (raqm version with identical pixels; x264 on 6 vs 48 threads) | 0 of 2 | **2 of 2** |
| unchanged-tree re-renders passing (2 per fixture) | n/a | **4 of 4** |

The regression test also proves the same on two further unchanged renders of the still (no false failure), the
tolerance verdicts, the identical-pixel toolchain acceptance, a changed input and a broken manifest.

Planted changes (stats at reference size; "block bias" = largest 16x16 mean signed difference):

| fixture / plant | changed px | peak | over-level share | block bias | exact | tolerance verdict |
|---|---|---|---|---|---|---|
| still, text 1 px right (LEFT 96 -> 97) | 12.85% | 114 | 1.14% | 7.10 | caught | beyond |
| still, GOLD blue +6 | 4.24% | 6 | 0 | 6.00 | caught | beyond |
| video, masthead 1 px right (f7 / f14) | 27.1% / 18.2% | 65 / 139 | 0.18% / 0.24% | 3.45 / 8.09 | caught | beyond |
| video, BONE blue -6 (f7 / f14) | 26.9% / 21.1% | 14 / 43 | 0 / 0.004% | 2.39 / 5.89 | caught | beyond (f14) |
| video, x264 6 threads vs 48 (another machine) | 36.2% / 23.8% | 15 / 24 | 0 / 0 | 1.62 / 2.18 | toolchain-changed | within |

The tolerance (`TOLERANCE` in scripts/goldens.py: level 24, share 0.05%, peak 96, block bias 3.0) is used only to
label a new-toolchain failure; it never makes a check pass. It sits between the encoder noise (bias <= 2.18, peak 24)
and the planted changes (bias >= 5.89 on at least one frame, or share >= 0.18%). The faint frame 7 of the video colour
plant (the masthead at ~20% opacity) is within on its own; the design is labelled by its worst frame.

A perceptual hash (dHash, 64 bits) misses the still's 1-px shift and both colour changes (measure.py
`dhash_differs`), so it is not used.

### Storage choice (bytes per golden, measured on the fixtures)

| choice | still (1 frame) | video (2 frames) |
|---|---|---|
| full PNG | 247,019 | 476,375 |
| full WebP lossless | 172,724 | 313,562 |
| half PNG | 117,486 | 247,895 |
| **half WebP lossless (chosen)** | **80,390** | **177,524** |
| dHash + 8 crops | 13,637 | 27,667 |

The exact check never reads the image: it compares the full-resolution pixel hash (so a 1-px shift is caught at
any reference size). The half-size lossless reference is for diff boards and the tolerance labels. Repository cost
per golden: the manifest (~2-3 KB) plus ~80 KB per still frame and ~90 KB per video sample; WebP is already
compressed, so git packing adds nothing. At ~60 frozen designs x ~2 frames that is ~10 MB (pack today: 434 MB).

### Toolchain findings

- Windows (FriBiDi 1.0.18) vs Linux (WSL Ubuntu 24.04, Pillow 13.0.0 wheel, FriBiDi 1.0.13): the still proof
  renders pixel-identical (SHA-256 a501d4c9...). A Linux CI variant is then accepted mechanically.
- **x264's output depends on the thread count**, which it takes from the machine's cores (48 here, 6 on a 4-core CI
  runner): same command, different bits (measured: threads 1, 3, 32 and auto all give different decoded frames;
  auto twice is identical). QF-134's encoder is deterministic per machine, not across machines. The goldens record
  the thread count (in the x264 settings) so it is reported as a toolchain change, never as a design change.
- The shared venv has fontTools 4.67.0 although the lock pins 4.68.0 (`goldens.py fingerprint`).

## CI and the post-freeze path

- `tests/test_goldens.py` runs in every shard plan like any test file (QF-135); the proof test
  (`tests/test_qf29_golden_images.py`) takes its goldens in a temporary store, so it passes on any machine.
- After a take on Windows the first CI run fails "toolchain changed: re-validate" (Linux). On GitHub Actions a
  failing design leaves `variant.json`, its reference-size frames and `board.png` in
  `$RUNNER_TEMP/shard/golden-actuals/<design>/`, which QF-135's shard artifact (`if: always()`) uploads; then
  `scripts/goldens.py accept-toolchain <design> --from <downloaded folder>` (identical pixels: no review;
  different: `--revalidated "<who checked which board>"`, only for a CI run of the golden's own design). No
  workflow change in this row. `GOLDEN_ACTUALS_DIR` overrides where a failing check leaves its actuals.
- Design change after a freeze: the test fails "design-changed"; `scripts/goldens.py board <design> --out DIR`
  draws golden | render | diff x8 | changed pixels | 4x zoom per frame for the light re-validation; after the
  operator's agreement, `take --retake-reason`.

Boards here: `board_still_shift_1px.jpg`, `board_still_colour.jpg`, `board_video_shift_1px.jpg`,
`board_video_toolchain_threads.jpg` (JPEG copies of the lossless PNG boards).

## Run time

Still render ~4.5 s, video sample ~27 s on this PC under load (take 15 s / 50 s). Regression test: ~6 min serial
(video test ~3.8 min: take, unchanged, threads, shift, colour); five test ids, so CI shards spread them.
