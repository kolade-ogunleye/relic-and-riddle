# QF-02 16-bit grade

Before: the master's film grade ran on the yuv420p stream. `curves` and `lut3d` are RGB filters, so ffmpeg converted
the picture to 8-bit RGB and back around them. Then the grain, the lens vignette and the captions ran on 8-bit YUV.
A 0-64 shadow ramp (56 luma levels) came out of the master with 29 distinguishable levels, because neighbouring
levels merged onto one code. Three black lifts were stacked on top of each other: a still's branch curve (0/0.02),
the pre-LUT curve (0/0.02) and the print table's own black (5.4/255). Together they put RGB black at Y21-22.

After: the master's picture chain (`video_composer.picture_chain`) reads the BT.709 YUV picture once into 16-bit
planar RGB (`gbrp16le`). The grade, the print LUT, the topic bias, the grain, the lens vignette and the captions all
run in 16-bit. The picture is then converted once, at the end, to BT.709 yuv420p (swscale dithers 16-bit RGB down to
8-bit YUV). Black is set in one place, the print table, at video black.

Reproduce: `python measure.py <repo root>` (repo root is the only argument). It prints JSON and writes
`measurements.json`. It runs `VideoComposer.compose_video`'s own graph on inputs it generates: flat BT.709 frames,
black and grey stills, and a sine narration. It does not use output/, the photos or the network. The master's
encoder is pinned twice: once lossless (the graph's own output) and once to the pipeline's libx264 fallback
(`VIDEO_PRESET`, `VIDEO_CRF`). NVENC differs per GPU, so the measurement does not use it.

- `before.json` / `before.txt`: measure.py run on a worktree of the start commit ea92e22 (QF-01)
- `after.json` / `after.txt` / `measurements.json`: the fix (ffmpeg 8.1, Windows)

The grain is unseeded (`noise=alls=8:allf=t+u`, which QF-03 removes), so the last decimal changes from run to run.

## Metric

| Measure | Before (ea92e22) | After |
|---|---|---|
| 0-64 shadow ramp: levels kept, of 56 (lossless encode) | **29** | **54** |
| same, libx264 delivery encoder (preset slow, CRF 18) | 29 | 52 |
| YUV -> 8-bit RGB conversions in the ramp graph | `yuv420p->rgb24` (around the grade) | none |
| black, 9:16 still (zoompan branch, its tone curve), lossless / x264 | Y22.41 / 22.50 | **Y16.62 / 16.99** |
| black, 16:9 still (zoompan branch) | Y22.38 / 22.49 | Y16.61 / 17.00 |
| black, 16:9 portrait still (tilt branch, no branch curve) | Y21.39 / 21.51 | Y16.61 / 17.00 |
| black, 9:16 black clip (Y16 in) | Y21.41 / 21.52 | Y16.62 / 16.99 |
| right edge: last 8 columns / the 8 before (planar-RGB bar check) | 0.993 | 0.993 |
| lens vignette: darkest 64 px corner / centre, above black (9:16 grey still) | 0.546 | 0.602 |

Acceptance: at least 50 levels kept (was 29; the finding measured 31 on its own chain), and blacks at Y16 +-1 (were
Y21-22). Both are met with either encoder.

A level counts as kept when its mean centre luma (a 192x192 box with the vignette at 1, grain averaged out) is more
than 0.15 code above the level below. The 8-bit round trips put neighbouring levels on the same code (difference 0).
The 16-bit chain's dithered conversion keeps a fractional step of 0.3-0.9 code. The 2 merged steps at the very bottom
of the lossless run are the grain clipping at RGB 0 (the levels there sit 0.0-0.4 code apart). The 8-bit codes the
boxes use cannot exceed the graded span (35-45 either way), which is why levels are counted from the means.

## What changed

- `src/video_composer.py`
  - `picture_chain(grade, width, height, ...)` builds the 16-bit chain: `BT709_TO_RGB,format=gbrp16le`, the
    grade, the grain, the lens, the card split, the captions, then `RGB_TO_BT709`. The fallback grade's `eq` (a YUV
    filter) still runs before the RGB read, as QF-01's `graded_in_bt709` did. This function replaces
    `graded_in_bt709`.
  - The lens vignette: ffmpeg's `vignette` has no 16-bit path; it converted 16-bit RGB to rgb24 and back. The same
    `picture_vignette(width, height)` filter is now drawn on a white frame (`color=white`, rgb24) and multiplied into
    the 16-bit picture (`blend=all_mode=multiply`, 16-bit). `graphic_vignette_enable`'s `enable` moves to the blend,
    so the lens still lifts under graphics.
  - In `compose_video`, `film_grade` holds the whole picture chain. The pinned guard
    `[video_raw]format=yuv420p,{film_grade},format=yuv420p,` (tests/test_delivery_and_visual.py, on origin/main) is
    kept: its trailing `format=yuv420p` follows the chain's own conversion. The 8 px right-edge bar that guard was
    added against does not appear (the `right_edge` row: 0.993 before and after).
  - The end card's background frame (`v_last`) is taken from the 16-bit RGB picture straight to rgb24.
  - The stacked black lifts are gone. The still branch curve is `0/0 0.1/0.11 0.5/0.50 0.9/0.89 1/0.97` and
    `FILM_PRE_CURVE` is `0/0 0.5/0.50 1/0.98`. The fallback grade drops `brightness=0.008` and its curve's 0/0.02.
    `hue_safe_print_lut` writes the table with `black=0.0`: a new `hue_safe_print_table(..., black=)` moves the
    print's black to 0 and fades the change out linearly to white, which stays. `FILM_TONE_VERSION` is now 2, so the
    cached table is rebuilt.
  - The caption ASS header says `YCbCr Matrix: None`. The captions are now drawn on RGB. With QF-01's `TV.709`,
    libass squeezed the colours into TV range on an RGB frame: brand gold measured Y166 U72 V155 in the master
    (about RGB 223/172/56). With `None` it measures BT.709 brand gold again
    (`tests/test_qf01_colour_matrix.py::test_the_gold_caption_word_is_bt709_brand_gold_in_the_master` passes).
- `src/graphics.py` `graded_frame` (the contrast measurement on the master's chain) runs the same `picture_chain`
  with its seeded grain and its own vignette string, as a filter_complex.

## Decisions and what differs from the finding's wording

- **"Stacked black lifts collapse to one (blacks at 16 +-1)."** A single lift of 0.01 kept in `FILM_PRE_CURVE`
  measured Y16.9 without the grain. With the master's grain it measured Y17.1-17.3, because in RGB the grain is
  clipped at 0 and adds about 0.6 code at black. That is outside Y16 +-1. So no lift is kept: black is set once, by
  the print table, at video black. With the grain the result is Y16.6 lossless and Y17.0 after x264 (x264 rounds the
  flat dark frames up).
- **Grain.** The grain stays where it was in the chain (after the grade, before the vignette), but it now works on
  16-bit RGB channels instead of the 8-bit YUV planes. With the same `alls=8`, its luma amplitude is lower: the
  standard deviation measured on a flat grey was 2.7 on YUV and 1.6 on RGB. QF-03 removes the grain from the master.
- **Vignette.** The vignette multiplies RGB, so black stays black in the corners. On YUV it multiplied the Y code
  itself, offset included, so the corners went below video black. The darkest corner of a mid-grey still is now
  0.602 of the centre (was 0.546). The filter strings `picture_vignette` returns are unchanged.

## Branch-only test changes (`tests/test_film_grade_2026_10_29.py`, not on origin/main)

- `_grade` (the test's model of the master's grade) now converts to `gbrp16le` before the chain, as the master does.
- `test_a_lamp_lit_wall_keeps_its_blue_and_gains_no_cast`: `bg(new) >= 0.97 * bg(ref)` -> `>= 0.95 * bg(ref)`.
  The row's decision (blacks at video black) removes the neutral veil the stacked lifts laid over dark colours. The
  dark lamp-lit wall now keeps 0.958 of the reference blue/green in the 16-bit chain (0.929 in 8-bit). The no-cast
  assertion (`_cast(new) <= _cast(ref) * 1.04 + 0.01`), the hue and the contrast assertions are unchanged and pass.
