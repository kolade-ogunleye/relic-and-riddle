# Film grade: the print's tone, the picture's hue (2026-10-29)

## Defect

Production's Derinkuyu run (29 Oct). `src/colour_balance.py` took the lamp-lit stone door (photo 25) from mean
RGB 70/71/31 to 85/84/45. The master's film grade then put it back: `curves` + `lut3d=assets/luts/kodak_2383.cube`
took the same still to 79/76/26, and the shot read neon lime. The Kodak 2383 print LUT is built for log film scans.
On our display-referred Rec.709 stills it also:

- warms every grey: mid-grey 128/128/128 comes out 140/136/130, and black comes out blue (3/5/8);
- crushes blue in yellow and orange colours: 85/84/45 comes out 67/62/22, so the B/G ratio falls by a third;
- raises saturation by up to 2.5 times (Roman concrete photo 1), and moves hue by 5 degrees on average.

Measured over 40 stills, the old grade added more than 0.01 of cast to 25 of them. It doubled the door's cast
(0.431 to 0.936) and the Richat cast (0.475 to 0.930).

## Fix (`src/video_composer.py`: `hue_safe_print_table`, `hue_safe_print_lut`, `film_grade_filter`)

The render still uses the print LUT's tone (its contrast, black density and highlight roll-off), but none of
its colour. A derived table of the same size (33^3) is built from `kodak_2383.cube` and cached under
`cache/luts/` (the file name carries a hash of the source and the settings, it is written atomically and uses LF):

1. **Tone**: the print's grey response, with its channels averaged so the curve has no tint of its own. It is
   applied to a norm halfway between a colour's Rec.709 luminance and its brightest channel
   (`FILM_TONE_NORM_MIX = 0.5`). The whole colour is scaled by `T(N)/N`, so hue and HSV saturation are unchanged.
   A luminance-only norm (`tone100` below) washes out bright, saturated warm colours such as Richat's sand,
   because their red channel clips. A max-channel norm (`max100`) avoids that but loses contrast (luma std
   1.165 against 1.203 for the old grade). The halfway norm keeps the old contrast (1.199).
2. **Richness**: relative chroma times `FILM_TONE_CHROMA = 1.03`, reduced where needed to stay in gamut.
3. **Black** is the print's own black, neutral.

The chain the render uses is
`[video_raw]format=yuv420p,curves=master='0/0.02 0.5/0.50 1/0.98',lut3d='<cache>/kodak_2383_tone_<hash>.cube',...`
followed by the topic's bounded `colorbalance` bias, which is unchanged. If the LUT is missing or cannot be
read, the grade falls back to a neutral contrast curve with saturation 1.03. The old fallback's red and blue
curves (`blue='... 1/0.94'`) warmed the highlights, so they were removed.

## Method

The measurement script is `harness.py` (`<repo> <workdir> <variants.json> --per-topic 4`). It takes 4 stills per
topic, spread evenly across the folder, plus the Derinkuyu door. The 10 topics are Derinkuyu, Richat, Nazca,
Roman concrete, Amber Room, Odessa, Gol Gumbaz, Heracleion, Dyatlov and Camp Century: 40 stills in all. Each
still is measured as is and after `colour_balance.balanced`, with `CACHE_DIR` pointed at a temporary folder.
Each still is reduced to 720 px and run through the master's colour path: `scale=out_range=tv,format=yuv420p`,
then the grade, then `format=yuv420p`, using ffmpeg 8.1. Each grade is compared with the same path run with a
`null` grade. The metrics are:

- **cast**: `colour_balance.scene()` neutral candidates, `max|mean/channel - 1|` (the balance's own measure);
- **hue shift**: the mean absolute per-pixel hue change, weighted by saturation, over pixels with saturation > 0.12;
- **saturation ratio**: mean HSV saturation after the grade over before (black-and-white stills excluded);
- **B/G ratio change**: the blue channel against green;
- **luma std ratio**: contrast.

The variants tried, measured on the balanced stills (all in `measurements.json`, `iterations_balanced`):

| variant | worst cast added | stills cast +0.01 | sat mean / max | hue shift | luma std |
|---|---|---|---|---|---|
| old (full LUT) | +0.579 | 25/40 | 1.293 / 2.498 | 5.05 deg | 1.203 |
| LUT at 50% (blend50) | +0.210 | 20/40 | 1.131 / 1.727 | 2.84 | 1.079 |
| luminance tone, sat 1.00 (tone100) | +0.022 | 1/40 | 0.985 / 1.061 | 1.39 | 1.225 |
| luminance tone, sat 1.05 | +0.028 | 3/40 | 1.023 / 1.111 | 1.33 | 1.225 |
| tone + the LUT's own per-colour saturation, clamped 0.95-1.10 | +0.060 | 5/40 | 1.028 / 1.116 | 1.34 | 1.225 |
| max-channel norm, 1.00 / 1.05 | +0.021 / +0.026 | 1 / 5 | 0.989 / 1.028 | 1.32 / 1.27 | 1.165 |
| halfway norm, 1.00 / 1.05 | +0.021 / +0.027 | 1 / 5 | 0.990 / 1.028 | 1.36 / 1.30 | 1.199 |
| **shipped: halfway norm, 1.03** | **+0.025** | **1/40** | **1.013 / 1.096** | **1.32** | **1.199** |

A partial blend only shrinks the defect: half the LUT still adds half the cast. Any uniform saturation gain
grows a warm material's measured "cast" in proportion. Above about 1.03, the number of stills over the 0.01
epsilon climbs from 1 to 5, so 1.03 is the ceiling. The one still over the epsilon with the shipped grade, Roman
photo 10 (0.073 to 0.098), is not a hue push: the hue shift is 1.7 degrees and B/G is 0.98. Its highlights carry
more of the scene's tint than its shadows, so added contrast weights the neutral mean toward them. The old grade
did the same to this still (0.098).

## Measurements: old vs new, balanced stills (full rows in `measurements.json`)

| still | cast before | old | new | sat old / new | hue shift old / new | B/G old / new | luma std old / new |
|---|---|---|---|---|---|---|---|
| Derinkuyu 25 (door) | 0.431 | 0.936 | 0.424 | 1.23 / 1.02 | 4.5 / 1.5 | 0.665 / 1.017 | 1.14 / 1.15 |
| Derinkuyu 32 | 0.355 | 0.506 | 0.350 | 1.09 / 1.02 | 5.2 / 3.0 | 0.840 / 1.006 | 1.00 / 1.01 |
| Derinkuyu 23 | 0.058 | 0.096 | 0.064 | 1.01 / 1.00 | 5.2 / 1.9 | 0.903 / 0.958 | 1.06 / 1.12 |
| Richat 01 | 0.475 | 0.930 | 0.473 | 1.30 / 1.00 | 6.0 / 0.4 | 0.692 / 0.998 | 1.26 / 1.19 |
| Richat 14 | 0.544 | 1.123 | 0.538 | 1.43 / 1.00 | 5.9 / 1.2 | 0.657 / 1.013 | 1.04 / 1.11 |
| Nazca 14 | 0.011 | 0.021 | 0.014 | 1.20 / 1.02 | 8.3 / 1.2 | 0.948 / 1.008 | 1.52 / 1.53 |
| Roman 01 | 0.007 | 0.031 | 0.007 | 2.50 / 1.10 | 8.1 / 2.9 | 0.950 / 0.996 | 1.46 / 1.45 |
| Roman 10 | 0.073 | 0.098 | 0.098 | 1.04 / 0.96 | 3.9 / 1.7 | 0.907 / 0.981 | 1.11 / 1.11 |
| Amber Room 20 | 0.618 | 0.782 | 0.553 | 1.06 / 0.99 | 6.6 / 0.8 | 0.892 / 1.040 | 1.18 / 1.24 |
| Odessa 01 | 0.332 | 0.715 | 0.342 | 1.39 / 1.01 | 4.7 / 1.4 | 0.726 / 0.999 | 1.22 / 1.31 |
| Gol Gumbaz 30 | 0.017 | 0.023 | 0.018 | 1.78 / 1.04 | 3.7 / 2.7 | 0.925 / 1.001 | 1.34 / 1.33 |
| Heracleion 03 | 0.029 | 0.039 | 0.031 | 1.33 / 1.02 | 3.9 / 1.6 | 0.931 / 0.999 | 1.19 / 1.19 |
| Dyatlov 11 | 0.828 | 0.520 | 0.661 | 1.01 / 1.00 | 4.9 / 1.1 | 1.026 / 0.995 | 1.25 / 1.29 |
| Camp Century 03 | 0.020 | 0.023 | 0.020 | 1.18 / 1.01 | 7.2 / 1.3 | 0.966 / 1.004 | 1.32 / 1.29 |

Across all 40 balanced stills:

| | old | new |
|---|---|---|
| worst cast added | +0.579 | +0.025 |
| stills over the 0.01 epsilon | 25 | 1 |
| saturation (mean / max) | 1.29 / 2.50 | 1.01 / 1.10 |
| hue shift (mean / max) | 5.05 / 8.78 deg | 1.32 / 3.29 deg |
| smallest B/G ratio | 0.657 | 0.958 |
| contrast (luma std) | 1.203 | 1.199 |

The raw stills give the same picture (`summary.raw`). The remaining 1-3 degrees of hue shift is the cost of the
yuv420p path and 8-bit rounding, measured against a `null` grade through the same path.

## Images (old grade | new grade, half size, balanced stills; `render_evidence.py <workdir>` re-renders them)

- `derinkuyu_25_master_frame_old_new.jpg`: the door as the master frames it at 0:17 (9:16 cover crop, pre-zoom
  curve, film grade, vignette; no captions or grain).
- `derinkuyu_25_old_new.jpg`, `derinkuyu_32_old_new.jpg`: lamp-lit stone, where the old print undid the balance.
- `richat_01_old_new.jpg`, `richat_14_old_new.jpg`: desert, warm material. Its own colour is kept, with no
  orange-yellow flood.
- `nazca_14_old_new.jpg`, `roman_01_old_new.jpg`, `gol_30_old_new.jpg`, `sunken_03_old_new.jpg`: daylight
  stone, sky and sea. They keep the print's contrast.
- `odessa_01_old_new.jpg`, `amber_20_old_new.jpg`: warm lamp scenes. The new grade keeps each photograph's own
  warmth (the lamp, the amber) and does not add the print's.

## Not fixed here (other files)

- **The 0:17 frame is far brighter than the grade can make it.** The final frame's mean is 122/128/31. The
  balanced still, framed and graded as the master does it, is 52/50/15 with the old grade. So before the master,
  something lifted the shot about 2.3 times and turned it from red-over-green to green-over-red. Neither the
  grade nor parallax can do that. A parallax round trip (rgb24 to x264 yuv420p, untagged, then the master's path)
  measured 85/84/45 to 81/82/44 (yuv rounding only), and ffmpeg 8.1 converts BT.601 sources to the BT.709 tag
  correctly (a 200/60/40 patch came out 198/57/38). The suspect is the AI motion clip for that beat
  (Kling, `src/motion.py`). `pre_condition_anchor_frame` sends the prepared still, but nothing checks the clip's
  colour or exposure against it. Add a check to `_finish` that compares the clip's first frame with the anchor's
  mean RGB and luma.
- **`art_direction.grade_bias` adds a cast back on lamp-lit heroes.** It is `(r - b) / 255 * 0.25` of the
  (balanced) hero, so Derinkuyu's door gives +0.040. Through the new grade, that bias takes the door's cast from
  0.424 to 0.462 (mean 73.9/72.8/38.6 to 77.8/72.7/34.7). It should be 0 when `colour_balance.scene(hero)` is
  lamp-lit, or measured only on daylight heroes.
