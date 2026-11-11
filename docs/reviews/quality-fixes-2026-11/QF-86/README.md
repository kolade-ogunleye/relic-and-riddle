# QF-86: formula LUT removed, photo-only curves, no broadcaster names

Row spec (docs/reviews/project-quality-review-2026-11-05.md, QF-86; operator policy recorded in
docs/HANDOVER-design.md): grade photo layers only, in 16-bit, black at 0; delete the formula cube and its
generator; remove broadcaster names used as "standards". Acceptance: graphic-only frames within CIE76 2 of the
`brand.py` tokens; >= 250 grey levels into the colour conversion; the literal name grep returns only domain lists
(plus the one historical credit the operator approved, below).

## What changed

- `assets/luts/kodak_2383.cube` and `scripts/generate_kodak_lut.py` are deleted, with the hue-safe table code,
  its cache (`cache/luts`) and the pre-LUT curve in `src/video_composer.py`.
- `PHOTO_CURVE` (`curves=master='0/0 0.2/0.195 0.5/0.505 0.8/0.81 1/1'`) is the one photo grade, run in
  `gbrp16le` on each photographic layer before the scenes are mixed. The topic's bounded warm/cool bias is a red
  and a blue curve with both endpoints fixed. `film_grade_filter` keeps its name and its ignored `source` /
  `out_dir` arguments for callers; it reads no file.
- Bare photographic layers: the three still branches (flat camera move, portrait-on-wide tilt, essential full
  image), **parallax clips** and **AI motion clips animated from a photo** (`video_composer.is_photo_clip`: the
  clip's own lineage operation is `parallax` or `ai_motion`, its whole source chain verifies, and nothing in it is
  an illustration, graphic, hook frame or footage; fails closed on a broken or stale chain). Of the 29 dev masters
  in `output/run_*` with a composition record, 28 carry their photos as `parallax/scene_NN.mp4` (the other is the
  Richat deep dive, all stills); on real data 178/178 parallax and 4/4 motion clips classify as photos, 49/49
  graphic and 1/1 footage clips do not. Stills take the same rule (`video_composer.is_photo_still`, used by the
  master's still branch and by `photo_grade.is_photograph`): the stem check over the name and the whole chain,
  and a chain that verifies, so a broken or stale upscale chain fails closed (review r2, non-blocking 1). On dev
  data (19 master stills, 15 graphic media sources, 80 beat images) no still changes class.
- Photo layers inside graphics (review r1, blocking 1): the stat-card backdrops, the timeline ground, the
  then-and-now pair, the callout photo, the hook frame's ground and the chapter/myth card grounds are drawn through
  `src/photo_grade.py` before any token, type or plate: the master's own curve (a table made by running
  `film_grade_filter(bias)` in gbrp16le on all 256 codes) at the bias the master uses for the run. `graphics.make_all`,
  `callouts.make` and `then_now.make` set that bias (`photo_grade.using`, per worker thread); main passes the
  Reel's art-direction bias and 0 on the documentary, whose master grades at bias 0. Illustrations
  (`gap_gen_`/`gen_` and lineage) and generated hook grounds stay ungraded. The photo builders' graphic cache keys
  include photo_grade.py, video_composer.py and the bias.
- Not graded: drawn graphic marks (tokens, type, plates), generated illustrations, footage. The master's global
  grade after the mix is gone (`film_grade = "null"`), so a graphic never takes the photo curve, not even during an
  act dissolve. BT.709 conversions, the 16-bit lens and caption chain and the graphic vignette exemption are
  unchanged.
- `graphics.graded_frame` (the frame every graphic's `<clip>.contrast.json` "graded" ratio is measured on) now
  runs the graphic path: conversions and, when asked, the lens; no photo curve.
- Broadcaster names removed from `main.py`, `src/privacy.py`, `src/multi_format.py` and `src/video_composer.py`
  docstrings/logs; stale "film grade/LUT" comments corrected in `src/art_direction.py`, `src/brand.py` and
  `src/video_qa.py` (comments only).

## Measurement

`python docs/reviews/quality-fixes-2026-11/QF-86/measure.py <repo root>` (repo root the only argument; same script
on the base 5c7840e9 -> `before.json` and on the commit -> `after.json`; the row was first measured on 1ab9aded
and ported onto QF-22's shared camera, see the lander's queue note QF-86.port.md). Inputs are committed in `inputs/`: real
run frames, half size, origin and sha256 in `inputs/inputs.json` (the hook frames are the freeze-panel card render
on the Nazca run's own photos, a real fact on a test-signed field, labelled as such). Every picture goes through
that tree's real `compose_video` at native size in a temporary folder; nothing is written, no network. Renders:
`render.py <repo root> renders <before|after>` writes half-size JPEGs under 300 KB each to `renders/`
(`*_stat_card_richat`, `*_cut_aswan`, `*_then_and_now`, `*_hook_frame`, `*_photo_parallax`,
`*_end_screen_aswan`; each file's panels are described in render.py). `before_*` is base 5c7840e9 and `after_*` this
commit, both with the pinned libx264 encoder, so the stills show QF-22's shared camera on both sides.

The figures depend on the encoder, so measure.py and floor.py pin one: libx264 (`encoding.get_video_encoder`'s own
fallback; its 3 s nvenc probe can time out on a cold start and silently switch encoder), recorded as `encoder` in
before.json, after.json and floor.json. With h264_nvenc the review measured differences of about +-0.15 CIE76,
and +-0.25 on the end-screen kicker. The end screens draw each run's own share line (`inputs.json` runs, verbatim from the dev
run's `script_metadata.json`) through `end_card.card_text`, as the pipeline does.

Real run grade biases used (`palette.json`): Gol Gumbaz +0.006, Roman +0.009, Nazca +0.009, Aswan +0.036,
Wieliczka +0.041, Richat +0.050 (dev topics also at +0.041 Voynich, +0.043 Hal Saflieni, +0.044 Odessa).

### Acceptance

| Metric | Base 5c7840e9 | QF-86 |
|---|---|---|
| Worst brand-token CIE76, 9:16 master, bias 0 / +0.006 / +0.009 / -0.05 | 9.476 | 1.314 |
| Worst brand-token CIE76, 9:16 master, bias +0.036 / +0.041 | 9.476 | 1.314 |
| Worst brand-token CIE76, 9:16 and 16:9 master, bias +0.05 | 9.490 / 9.552 | 1.314 |
| 16-bit ramp levels before conversion (bias 0 / -0.05 / +0.05) | 256 / 236 / 236 | 256 / 256 / 256 |
| Black under either bias bound | one channel 2294/65535 | 0, 0, 0 |
| White, all three biases | 62747/65535 | 65535/65535 |
| Formula LUT / generator present | yes / yes | no / no |
| Name grep lines outside the approved contexts | 16 | 0 (4 lines: 3 domain contexts + the approved credit) |

### Every design in the freeze scope, real run frames (CIE76 as drawn -> delivered, median / p95; mean L* shift)

| Design (input) | Base | QF-86 |
|---|---|---|
| Stat card (Gol Gumbaz `graphic_numbers`) | 10.24 / 12.69, L* -8.89 | 1.01 / 1.60, L* -0.51 |
| Map (Gol Gumbaz `graphic_map`) | 5.86 / 12.30, L* -4.06 | 0.93 / 2.15, L* -0.56 |
| Then and now (Roman `graphic_thennow`) | 7.34 / 12.86, L* -1.76 | 1.04 / 2.29, L* -0.55 |
| Hook frame, bright ground (Nazca), lens on | 13.69 / 18.20, L* -11.69 | 6.33 / 17.52, L* -7.24 |
| Hook frame, bright ground, lens off (grade alone) | 11.76 / 13.38, L* -5.86 | 1.03 / 1.97, L* -0.65 |
| Hook frame, dark ground, lens on | 12.26 / 19.37, L* -9.75 | 4.03 / 14.68, L* -5.00 |
| Photo as parallax clip (Gol Gumbaz 04), lens on | 8.38 / 18.91, L* -7.35 | 8.28 / 20.96, L* -8.68 |
| Photo as parallax clip, lens off (grade alone) | 7.97 / 13.40, L* +1.18 | 1.08 / 2.72, L* -0.47 |
| Photo as parallax clip, bias +0.05, lens off | 7.91 / 13.38, chroma +1.27 | 3.61 / 5.94, chroma +1.99 |
| Photo still, flat move, lens off (means; crop moves) | L* +1.02, p5/p50/p95 3.07/66.67/88.17 | L* -0.52, p5/p50/p95 7.77/59.32/85.24 (drawn 8.44/59.32/84.94) |

Read with care:
- The hook frame and photo rows "lens on" are dominated by the lens vignette, which both trees draw (it is lifted
  only under `graphic_*` clips; `hook_frame.mp4` keeps it by design, design_cards measures it "vignette kept").
  The "lens off" rows (picture_vignette drawn white in both trees) isolate the grade.
- Photos: the base's hue-safe print tone lifted mid-tones (+1.2 L*), crushed the shadows (p5 8.4 -> 3.0) and held
  white at 62747; QF-86 keeps the photo within CIE76 1.2 median of the drawn file. With the lens on, the delivered
  photo is about 1.3 L* darker on average than on the base, because the print's mid-tone lift is gone. That is the
  visible change on every photo in every Reel.
- Callouts were not rendered by measure.py: no run carries them (CALLOUTS_ENABLED is off and the renderer needs
  located features). `callouts.render` draws its photo through `photo_grade.cover` (tested), so they follow the
  cut-gap rows below. Quote cards are out of the freeze set.

### The cut between a bare photo and the same photo inside a graphic, at each run's real bias

Lens off in both (the vignette is lifted under graphic clips only, on both trees), so this is the grade alone.
CIE76 median / p95 between the delivered bare photo (parallax clip) and the delivered graphic photo layer:

| Photo (bias) | Base (global grade) | Layer left ungraded (build-2 behaviour) | QF-86 (layer graded) |
|---|---|---|---|
| Gol Gumbaz 04 (+0.006) | 0.55 / 2.02 | 1.18 / 2.25 | 1.12 / 2.45 |
| Roman Pantheon (+0.009) | 0.36 / 1.80 | 1.49 / 2.37 | 1.21 / 2.51 |
| Aswan (+0.036) | 0.71 / 1.84 | 2.67 / 4.67 | 1.10 / 2.35 |
| Wieliczka (+0.041) | 0.48 / 1.92 | 1.25 / 4.79 | 1.11 / 2.85 |
| Richat (+0.050) | 0.83 / 1.83 | 5.21 / 6.53 | 1.12 / 2.21 |
| Aswan at the cool bound (-0.05) | 0.71 / 2.05 | 3.46 / 4.45 | 1.17 / 2.31 |

The bias no longer opens a cut: the remaining 1.1-1.3 median is the same at every bias, below the CIE76 JND of
about 2.3; p95 2.2-2.9 sits on fine texture. The base is lower because it graded both after the mix; it also
graded every graphic token (9.2-9.5).

Where the floor comes from (`floor.py <repo root>` -> `floor.json`; review r2 blocking 2; the actual
compose_video, lens off, CIE76 median / p95, Aswan +0.036, Richat +0.05, Aswan at 0):

| Variant | Aswan +0.036 | Richat +0.05 | Aswan 0 |
|---|---|---|---|
| As built (graded 8-bit layer, this machine's encoder: libx264 in floor.json) | 1.10 / 2.35 | 1.12 / 2.21 | 1.23 / 2.40 |
| Layer graded with the 16-bit curve + TPDF dither | 1.10 / 2.34 | 1.11 / 2.19 | 1.15 / 2.26 |
| Graphic clip encoded losslessly | 1.10 / 2.34 | 1.12 / 2.20 | 1.23 / 2.39 |
| Every encode lossless (clips and master) | 0.74 / 1.78 | 0.84 / 1.75 | 0.96 / 1.87 |
| Order only: same parallax path, curve after 4:2:0 vs layer graded before (lossless) | 0.37 / 1.68 | 0.42 / 1.63 | 0.73 / 1.75 |
| Path only: parallax vs graphic clip path, curve off (lossless) | 0.37 / 0.75 | 0.39 / 0.82 | 0.37 / 0.75 |
| Control: bare clip encoded and mastered twice | 0.00 / 0.00 | 0.00 / 0.00 | 0.00 / 0.00 |

- Not the 8-bit table: a 16-bit dithered layer moves the median by 0.00-0.08.
- Not the graphic clip's own encode: a lossless graphic encode moves it by 0.00 with libx264 (0.02-0.04 with
  nvenc, review r2/r3).
- With every encode lossless the floor is still 0.74-0.96 median. That remainder has two parts (of about the
  same size, except Aswan at bias 0, where the order part is twice the path part):
  - grading after rather than before 4:2:0 chroma subsampling: one parallax path, fed the raw photo with the
    curve on, or the pre-graded layer with the curve off: 0.37-0.73 median;
  - a path difference inside compose_video between a parallax clip and a graphic clip, there with the curve off:
    0.37-0.39 median, p95 0.75-0.82. The parallax path is uniformly darker by 0.5-0.7 code in R, G and B (its
    extra yuv420p -> 16-bit RGB -> yuv420p round trip). An explicit input range, or swscale's accurate_rnd +
    full_chroma_int on that round trip, leaves the offset where it is (`path_only_*`), so it is not swscale's
    rounding; its exact origin is not located.
- The rest of the as-built floor (0.28-0.36) is the delivery encode's noise on two differently processed
  pictures.

Only grading both in the same place and order (a masked grade in the master) would bring the floor to the base's
level; that is not done here.

### The Richat stat card, real renderer, real copy, bias +0.05

| | Base | QF-86 |
|---|---|---|
| Contrast sidecar min (drawn / graded) | 6.89 / 10.69 | 6.91 / 6.91 |
| Top band (no text) vs the same photo bare, frame 0, lens on: CIE76 median / p95 | 1.70 / 6.32 | 1.47 / 4.54 |

The base's graded minimum was read on the LUT-darkened ground; now graded equals drawn, and both stay above 4.5.

### Contrast measurements downstream

- `graphics.graded_frame` (the "graded" ratio in every graphic's `.contrast.json`, design_cards' hook refusal):
  CIE76 vs drawn, stat card 10.32 -> 0.95, hook dark 9.23 -> 0.98, hook bright 11.83 -> 0.79 median. Graded
  ratios now equal drawn ratios to within YUV rounding; graphics whose graded contrast was the binding minimum read
  higher. Cached graphic clips are re-rendered (their cache key includes src/graphics.py and src/brand.py).
- 16:9 end screen (`end_card.frames` at this tree; its last frame sampled at 0.55 fps, t 9.09 s, see measure.py)
  over the three last-scene photos the parked freeze round measured raw. The 16:9 end screen ends the
  documentary, whose master grades at bias 0 (`compose_deep_dive`); the topic's own bias is shown too:

  | Case (topic bias) | Kicker on raw photo (park's stand-in) | Base, bias 0 / topic bias | QF-86, bias 0 / topic bias |
  |---|---|---|---|
  | Wieliczka (+0.041) | 4.81 | 4.80 / 5.05 | 4.96 / 4.93 |
  | Aswan 1792985912 (+0.036) | 5.08 | 4.98 / 4.97 | 5.01 / 4.95 |
  | Roman 1793163918 (+0.009) | 4.89 | 4.83 / 4.83 | 5.06 / 5.06 |

  Each card draws its own dev run's share line (`inputs.json`, verbatim) through `end_card.card_text`, as the
  pipeline does (card_text sets the line's follow clause aside). All stay above 4.5; no case drops. The
  ground's grade-only mean L* shift vs the raw photo falls from -2.0 / +3.3 / -1.0 to +0.2 / -0.0 / -1.5: the
  park's raw-photo stand-ins are now closer to what the composer delivers. The ground is the last still through
  QF-22's camera at t 0.95, so its crop moved with that camera, not with the grade (QF-86.port.md). These are the committed end card's figures; the parked end card adds a kicker band (park:
  7.32-7.38) and must be re-measured on the rebased tree (rulings Q9).
- Covers, Stories and carousel slides carry no grade before or after: a real Story frame, carousel slide 1 and
  the thumbnail cover redrawn from the Gol Gumbaz run's recipes are pixel-identical on both trees (sha256 in
  `still_surfaces`). The park's still-surface contrasts (masthead 5.54:1, scrim, Greek fire ground) do not move.
  The loop bridge carries no text in production (LOOP_LINE off); its last frame and frame 0 come from the same
  chain on both trees.

## Branch-only test changes (none exists on origin/main 8419aa78)

- tests/test_film_grade_2026_10_29.py: the retired table/cache/fallback internals and the negative controls that
  rendered the deleted cube become checks of the curve's endpoints, neutrality and no-cache behaviour. Kept:
  lamp-wall blue >= 95 %, cast <= 1.04 x reference + 0.01, hue < 2 deg, local contrast >= reference, grey spread
  <= 3. Removed: ">= 95 % of the retired LUT's contrast" (the LUT no longer exists). The helper grades in RGB16
  before YUV, as the photo branch does.
- tests/test_qf01_colour_matrix.py: the graphic master now expects the graphic's own BT.709 red, not graded red.
- tests/test_qf02_16bit_grade.py: only the filter-string pin changes from the pre-curve + lut3d to PHOTO_CURVE.
- tests/test_freeze_thennow_2026_11_04.py: the `_sources` helper (the pair as `frame_pair` hands it to the
  renderer) applies `photo_grade.grade`, as `frame_pair` now does; every assertion is unchanged.
- The old candidate's QF-04 fixture line (`coordinator.db = None`) is not carried: the test passes on this base
  without it.

## Approved exact metadata exception

The grep's one non-domain line is the NASA/Commons Gemini IV credit's description (original film stock). The
operator approved preserving that exact field (decisions/QF-86.md). `approved-name-exceptions.json` binds its
title, source URL, field and value sha256; any other match, an altered value or added text in a domain context
fails `name_policy`.

## Parked freeze work

QF-86 x `src/multi_format.py`: one hunk, the carousel docstring ("one of 5 National Geographic / The Economist
archetypes" -> "one of five editorial layout archetypes"). Resolve by taking the park's docstring with the names
replaced. Pixel effect on the park's designs: none on still surfaces; the end screen and any video-frame ground
as in the table above.

## Operator note

Every Reel photo is about 1.3 L* darker on average with the lens on than on the base: the print tone's mid-tone
lift is gone (it also crushed shadows, p5 8.4 -> 3.0, and capped white at 62747). This follows the approved
policy and changes every Reel; worth a line in the phase report.
