# QF-01 Colour matrix

Before: every RGB-to-YUV conversion used swscale's BT.601 default, and the master was tagged BT.709. Graphics,
parallax and SD footage clips were BT.601 and had no tags. The master carried `colorspace=bt709`, but its
primaries and transfer were `unknown`. After: every RGB picture is converted with the BT.709 matrix where it enters
YUV. Every clip and the master are tagged BT.709 limited range (colorspace, primaries, transfer, range). A YUV clip
is read with the matrix it was made with.

Reproduce: `python measure.py <repo root>` (repo root is the only argument). It prints JSON and writes
`measurements.json`. It runs the pipeline's own encoders (`graphics._encode`, `parallax.render_clip`,
`footage.make_segment`, `motion._finish`) and `VideoComposer.compose_video`'s own graph on inputs it generates:
solid #FF0000 frames, solid red sources and a sine narration. It does not use output/, the photos or the network.

- `before.json` / `before.txt`: measure.py run on a worktree of the start commit 7d063bd
- `after.json` / `after.txt` / `measurements.json`: the fix (ffmpeg 8.1, Windows)

## Metric (target BT.709 limited red: Y63 U102 V240, +-2)

| Path | Before: tags (space/primaries/transfer) | Before Y U V | After: tags | After Y U V |
|---|---|---|---|---|
| graphics._encode clip | unknown/unknown/unknown | 81 90 240 | bt709/bt709/bt709, tv | 63 102 240 |
| parallax.render_clip clip | unknown/unknown/unknown | 81 90 240 | bt709/bt709/bt709, tv | 63 102 240 |
| footage segment, BT.709 HD source | bt709/bt709/bt709 | 62 102 239 | bt709/bt709/bt709, tv | 62 102 239 |
| footage segment, untagged SD BT.601 source | bt470bg/unknown/unknown | 81 90 239 | bt709/bt709/bt709, tv | 62 102 238 |
| motion._finish clip | bt709/bt709/bt709 | 62 102 239 | bt709/bt709/bt709, tv | 62 102 239 |
| master 9:16, graphic clip (grade at identity) | bt709/unknown/unknown | 62.0 102.5 237.6 | bt709/bt709/bt709, tv | 62.6 101.7 239.6 |
| master 16:9, red still, tilt branch (grade at identity) | bt709/unknown/unknown | 62.0 102.3 237.6 | bt709/bt709/bt709, tv | 62.6 101.6 239.5 |
| master 16:9, graphic clip (grade at identity) | bt709/unknown/unknown | 62.0 102.5 237.6 | bt709/bt709/bt709, tv | 62.7 101.7 239.6 |
| master 9:16, real film grade, vs the grade in RGB, BT.709-encoded (65.9 102.8 237.8) | bt709/unknown/unknown | 63.7 102.8 236.6 | bt709/bt709/bt709, tv | 65.7 102.7 237.6 |

The "grade at identity" masters hold `film_grade_filter` at `null`, so the colour conversions are measured on
their own. Each still branch's tone curve (9:16) and the film grade change red on purpose. The 16:9 portrait
still is the tilt branch, which has no curve. The real-grade row compares the master against the grade applied in
pure RGB and then encoded with BT.709.

Before, the master came out close to BT.709 red only because ffmpeg 8.1's last `scale` converted the untagged
(BT.601) picture to the BT.709 that the encoder flags asked for. That extra conversion was the fourth on a still's
path. It is a side effect of ffmpeg's colour negotiation, not something the graph asked for. Even so, V came out at
237.6 instead of 240 and the primaries and transfer stayed unknown.

## Conversions in compose_video's graph (ffmpeg -v verbose, swscale's own lines)

| Graph | Before | After |
|---|---|---|
| 16:9 still + graphic, grade at identity | rgb->yuv **csp:unknown** (BT.601), then yuv unknown->**bt709** at the end | one rgb->yuv to bt709, nothing else changes matrix |
| 9:16 graphic clip, grade at identity | yuv unknown->bt709 at the end | none |
| 9:16, real grade | yuv unknown->rgb, rgb->yuv unknown, yuv unknown->bt709 | yuv bt709->rgb and rgb->yuv bt709 (the grade's round trip), nothing else |

Non-BT.709 conversions went from 1 to 3 per graph before to 0 after. Matrix changes between YUV formats went from
1 per graph to 0.

**What differs from the finding's wording.** The finding asks for "exactly one colour conversion at the end of the
graph". The fix makes one RGB-to-BT.709 conversion where each RGB picture enters the graph's YUV. It does not move
that conversion to the end. The graph stays YUV from the clips to the delivery. `tests/test_delivery_and_visual.py`
(on origin/main) pins `[video_raw]format=yuv420p,{film_grade},format=yuv420p,` against the planar-RGB (gbrp)
8 px right-edge black bar, and the video clips arrive in YUV. The film grade's RGB filters (curves, lut3d) still
need a YUV->RGB->YUV round trip. That round trip is now explicit and BT.709 both ways (`graded_in_bt709`, wrapped around `film_grade_filter`'s chain) instead
of using ffmpeg's inserted BT.601 converters. Moving the single conversion to the very end would change that pinned
test, so it would need an operator decision.

## What changed

- `src/encoding.py`: `BT709_TAGS`, `BT709_FLAGS`, `RGB_TO_BT709`, `BT709_TO_RGB`, `source_matrix()` (a clip's
  own tag; untagged clips are read as BT.709 for HD and BT.601 below 720 lines, as players read them) and
  `to_bt709()`.
- `src/graphics.py`: `_encode` converts with BT.709 and tags. `graded_frame` (the contrast measurement on the
  master's chain) converts BT.709 in and out, as the master now does.
- `src/parallax.py` (`render_clip`), `src/footage.py` (`probe` also reads `color_space`; `make_segment` uses
  the new `frame_filter_bt709`, which appends the conversion to the unchanged `frame_filter` that `video_qa` compares) and `src/motion.py` (`_finish`): BT.709, tagged. A YUV source is read with its own matrix.
- `src/video_composer.py`: the caption ASS header names `YCbCr Matrix: TV.709`; still branches use `RGB_TO_BT709`. Video branches and `hook_clip_filter` use
  `to_bt709(video_colorspace(clip))`. The grade is `graded_in_bt709(film_grade_filter(grade_bias))`. The last
  filter is a tagged BT.709 pass-through. The loop-bridge frames use `RGB_TO_BT709`. `splice_single_beat` uses BT.709, with the
  delivery flags.

## Caption colours (brand gold)

libass writes an ASS file's colours with the YCbCr matrix its `[Script Info]` header names, and a header with no
`YCbCr Matrix` line is read as TV.601. Before the fix the last `scale` converted the whole untagged (BT.601)
picture, captions included, to BT.709, so the gold came out right by accident. Once the graph is BT.709 throughout,
that last pass is BT.709 to BT.709 and the caption's BT.601 values stayed in a BT.709 master: the gold key word
measured Y175 U60 V164 (about RGB 250, 180, 42) instead of brand gold. `build_subtitles` now writes
`YCbCr Matrix: TV.709` in its header (the only ASS writer that burns into a master; `video_qa`'s caption mask
copies it and only thresholds luma).

`caption_gold` in measure.py: the gold key word (brand.GOLD #F2B630) over a grey graphic, grade and lens vignette
at identity, median Y/U/V of the gold glyph cores. Target: BT.709 limited brand gold Y175.0 U63.1 V159.7 (+-2).

| Master | Before (7d063bd): header | Before Y U V | Fix without the header line | After: header | After Y U V |
|---|---|---|---|---|---|
| 9:16 | none (TV.601) | 174 63 160 (right through the final 601-to-709 scale) | 175 60 164 | TV.709 | 175 63 160 |
| 16:9 | none (TV.601) | 174 64 159 | (not measured) | TV.709 | 175 63 160 |

`tests/test_qf01_colour_matrix.py::test_the_gold_caption_word_is_bt709_brand_gold_in_the_master` measures it in
the 9:16 master: it fails with the header line removed ((175, 60, 164) against (175.0, 63.1, 159.7)) and passes with it.
