# Colour and brightness balance at photo preparation (2026-10-29, rc72 item 11)

Production's Derinkuyu run (run_derinkuyu_underground_city_1793286295) used stills that were dark and murky with a
heavy yellow-green lamp cast, so the shots looked alike. `src/colour_balance.py` now balances every prepared still
(`main.prepared_still`, `_prepare_images`): beat, gap, reserve and pre-voice photos too.

## What it does (VERSION 2, after the review below)

- **Cast, lamp-lit scenes only**: a scene is lamp-lit when it is dim (95th-percentile luma < 0.72, `DIM_P95`), has
  no daylight sky (< 5% of its upper half bright and sky blue, hue 190-250), no vegetation (< 3% of the frame green,
  hue 80-170, saturation > 0.2) and even its brightest unclipped 3% is tinted (HSV saturation >= 0.20: a white
  light shows as near-white highlights). The cast is measured only on **neutral candidates**: the least saturated
  35% of the mid-tones (luma 20-235), never sky-blue or foliage pixels. Each channel is taken 60% of the way to
  grey (`CAST_STRENGTH`), gains capped to 0.85-1.18 (`GAIN_LIMIT`), normalised so brightness is unchanged by it.
  A cast under 0.04 is left alone. Every other scene keeps its colour: a blue sky, desert sand, sea, foliage,
  red sandstone, a painted pot on a grey plinth.
- **Exposure**: only a mean luminance under 0.30 is lifted, by a gamma curve to at most half-way to 0.40, never
  stronger than gamma 0.75, applied to the luminance with every channel scaled alike, so hue and saturation are
  kept. Black stays black and white stays white: contrast (luma std) fell by at most 0.004.
- **Never touched**: black-and-white (mean chroma < 8), toned archival prints (every channel linear in luminance,
  hue in sepia 10-48 deg or cyanotype 185-235 deg), our graphics/maps, footage and clips, an already balanced copy.
- **Record**: a copy keeps its file name under `cache/colour_balance/<sha256(source)+version>/`, with a lineage
  sidecar (operation `colour_balance`, its gains, gamma, cast and mean luminance), so credits and rights resolve to
  the original. Deterministic, cached (a resume pays nothing), idempotent. VERSION 2 re-keys the cache.

## Review defect fixed (2026-10-29)

Version 1 ran grey-world over the whole frame, so any natural dominant colour read as a cast. Photo 5 (a daylight
street under a clear sky) had its sky turned grey-teal: sky saturation 0.527 -> 0.417 (-21%). Version 1 also
neutralised photo 12 (red sandstone carving), the sunlit desert of the Richat pool (whole-frame saturation down to
-49%, photo 08), Nazca's skies (-23%, photo 18) and the Pantheon's warm concrete (-52%, roman photo 24).

## Measurements (read only: 32 Derinkuyu photos plus three outdoor-heavy pools)

Full table: `measurements.json` (per photo: settings, the lamp-lit decision and its inputs, mean luma, contrast std,
mean saturation of the whole frame / sky mask / foliage mask, and the neutral candidates' cast, before and after).
Saturation is HSV, on a 256 px copy; sky and foliage masks are taken from the photo before correction.

| pool | photos | v1 changed | v2 changed | v2 cast-corrected | worst sky sat change | worst foliage sat change | v1 worst sky / whole-frame |
|---|---|---|---|---|---|---|---|
| derinkuyu_underground_city | 32 | 23 | 10 | 9 (all lamp-lit) | 0.0% (15 with sky) | 0.0% (12) | -34% / -43% |
| richat_structure_sahara_eye | 20 | 20 | 0 | 0 | 0.0% (14) | 0.0% (6) | -2% / -49% |
| nazca_lines_runway_paradox | 20 | 10 | 0 | 0 | 0.0% (16) | 0.0% (8) | -23% / -9% |
| roman_self_healing_concrete | 28 | 15 | 1 (lift only) | 0 | 0.0% (11) | -11% (3, see note) | +13% / -52% |

Lamp-lit Derinkuyu photos (19, 20, 21, 23, 24, 25, 26, 30, 32): neutral-candidate cast 0.43 -> 0.26 on average
(photo 19 1.00 -> 0.67, gain-capped; 25 0.67 -> 0.43; 30 0.36 -> 0.16; 23 0.15 -> 0.06). Dark ones lifted
(7 photos plus 28): mean luma 0.17 -> 0.24. Photo 21 (orange lamps over a blue-lit well): its blue gets 15% more
saturated as the orange cast is taken out.

Note on roman photo 10 (Pantheon interior at dusk, exposure lift only, no cast correction): its "foliage" mask is
a few dark olive pixels on the coffers, not vegetation; their saturation moves -11% from 8-bit rounding in the
shadows, while the whole frame's saturation is unchanged (0.325 -> 0.326).

Known limit: a uniformly tinted wall whose hue is in the sepia range is indistinguishable from a toned print and is
left alone (real lamp-lit photos are not one straight line through the luminance, so they are balanced).

## Renders

Before (left) and after (right), each at half size, the pair fitted within 1600 px:

- `NN_commons_before_after.jpg`: every Derinkuyu photo that changed, plus unchanged daylight photos 03, 05, 07, 12
  (identical halves: proof they are left alone).
- `richat_NN_`, `nazca_NN_`, `roman_NN_before_after.jpg`: four representative photos per outdoor pool, all
  unchanged (richat 14, a dim ISS-window desert view, is the one v2 kept out by its vegetation share).

Made by a scratch script outside the repository with the cache pointed at a temporary folder; nothing in
`assets/` or `cache/` was written.

## Follow-ups

- Photo *selection* by colour/brightness variety (so a video does not draw five near-identical murky shots) is not
  built; this step only balances what was chosen.

## Version 3 (final rc72 review)

The final review found version 2 cooling warm materials toward grey: 8 of the 20 Amber Room photos read as lamp-lit
and got gains up to [0.87, 1.02, 1.18]; the Lycurgus cup, a Kailasa interior and gold on velvet were corrected too,
and Derinkuyu photo 19 turned greenish. Neither saturation nor brightness separates a warm material from a warm
lamp, but hue separates what viewers accept from what they do not: every warm case's neutral candidates sit at hue
20-44 deg (candle, torch, tungsten, amber, gold, ochre), while Derinkuyu's murk is yellow-green (52-63 deg), magenta
(351) or blue-violet (267). Version 3 leaves a cast in `WARM_CAST_HUE` (10-48 deg) as it is; the exposure lift still
applies. The foliage veto now ignores pixels darker than luminance 30, which were JPEG noise in shadow.

| pool | photos | v3 changed | v3 cast-corrected | warm cast left |
|---|---|---|---|---|
| derinkuyu_underground_city | 32 | 9 | 8 | 1 (photo 19) |
| richat_structure_sahara_eye | 20 | 0 | 0 | 0 |
| nazca_lines_runway_paradox | 20 | 0 | 0 | 0 |
| roman_self_healing_concrete | 28 | 1 (lift only) | 0 | 0 |
| amber_room_heist | 20 | 0 | 0 | 8 |
| lycurgus_cup_nanotech | 17 | 1 (lift only) | 0 | 1 |
| odessa_catacombs | 19 | 0 | 0 | 4 |
| gobekli_tepe_enclosure_d | 11 | 0 | 0 | 1 |
| kailasa_temple_monolith | 20 | 1 (lift only) | 0 | 1 |
| hal_saflieni_hypogeum | 14 | 0 | 0 | 2 |

Per-photo settings and neutral hue: `measurements_v3.json`. Before/after: `v3_amber_03_before_after.jpg`,
`v3_lycurgus_09_before_after.jpg` (unchanged in colour), `v3_derinkuyu_19_before_after.jpg` (lift only) and
`v3_derinkuyu_25_before_after.jpg` (yellow-green cast still taken out).
