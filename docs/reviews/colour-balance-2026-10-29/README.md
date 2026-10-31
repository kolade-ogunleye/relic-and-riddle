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

## Version 4 (production review, 29 Oct)

Production's Derinkuyu review (rc72) showed the rolling stone door neon lime. Two causes, both fixed:

1. Production's photo 08 (the stone door; production's pool differs from development's) is bright: 95th-percentile
   luminance 0.79, above `DIM_P95` 0.72, so it passed as daylight and was never balanced, although its highlights
   are strongly tinted (saturation 0.52) and its neutral candidates sit at hue 62 deg. Daylight shows near-white
   highlights; these lamps tint even the brightest surfaces. A bright frame now counts as lamp-lit when its highlight
   tint is at least `BRIGHT_LAMP_TINT` 0.40 and its neutral hue is yellow-green (`LAMP_GREEN_HUE` 48-75 deg). Warm
   (10-48) and every other bright scene is unchanged: on the ten development pools above, v4 changes no photo's
   settings against v3.
2. The master's film-print LUT crushed blue (docs/reviews/film-grade-2026-10-29/).

`v4_prod_stone_door_old_new.jpg`: left, the photo as posted (unbalanced, old grade: mean 157/154/37); right, balanced
v4 through the new grade (148/148/73). Also: `art_direction.grade_bias` is 0 for a hero whose off-white lamp cast
the balance reduces (the door's was +0.040, which put the cast back); warm light keeps its bias.

## Version 5 (design critic round 4, 29 Oct): the lamp step

The critic found that version 4 leaves a yellow-green cast on lamp-lit stone. The blue gain stops at `GAIN_LIMIT`
1.18, while the door needs about 1.85. Production's door photo 08 kept a cast of 0.469 and the development door
(photo 25) kept 0.426, so both still read lime. A bigger blue gain is the wrong tool, because it amplifies the
starved blue channel's noise. In the `gain1.40` trial, blue high-frequency noise was 16-24% above version 4's.

**What version 5 does.** The step runs only on a scene where version 4's gains took out a cast whose neutral hue
is yellow-green (`LAMP_GREEN_HUE` 48-75 deg). Hues on the green side of 48 deg (`LAMP_STEP_HUE`: the step rises
from 48 to full at 58, holds to 78 and fades out by 92) keep `LAMP_STEP_KEEP` 0.4 of their saturation. They are
also pulled `LAMP_STEP_PULL` 0.5 of the way toward 48 deg, and each pixel keeps its luminance. The hue mapping is
monotonic, so nothing bands. The step is recorded in the lineage as `lamp_step`, and `VERSION` 5 re-keys the cache.

Pixels at or below 48 deg are not touched at all: candle, torch, amber, gold and ochre all sit there (the v3 rule).
Magenta and blue casts (Derinkuyu 21 and 23) are already within reach of the gains, so they keep version 4's output.

**Variants measured** on the scenes version 4 corrects (scratch harness; the shipped one is `v5_evidence.py`):

| variant | door 25 cast | prod 08 cast | warm-pixel saturation kept | warm-pixel hue moved | Derinkuyu 21 / 23 |
|---|---|---|---|---|---|
| v4 | 0.426 | 0.469 | - | - | - |
| critic's band 28-88 deg, keep 0.62, pull to 40 | 0.210 | 0.225 | 0.63-0.66 | up to 3.9 deg | 21 worse (0.135 -> 0.174); 23 hue 278 -> 317 |
| blue gain cap raised to 1.40 | 0.229 | 0.260 | 0.77-0.87 | up to 5 deg | blue noise +16-24% |
| desaturate along the cast axis, keep 0.6 | 0.207 | 0.224 | 0.66-0.72 | 6-9 deg toward red (turns orange) | - |
| green side only, keep 0.6 / 0.5 | 0.201 / 0.159 | 0.217 / 0.172 | 0.98-1.00 | under 1 deg | untouched |
| **shipped: green side only, keep 0.4, pull 0.5** | **0.121** | **0.131** | **0.96-0.99** | **under 1.3 deg** | **untouched** |
| green side only, keep 0.3 | 0.087 | 0.093 | 0.97-0.98 | under 1 deg | untouched |

The critic's band takes a third of the saturation of every warm pixel. It cannot tell the lamp from a warm
material, and it also catches the magenta and blue scenes. Desaturating along the cast axis turns warm stone
orange. Restricting the step to the green side of 48 deg removes as much cast as the critic's band, or more, and
leaves warm colours as they were. Going from keep 0.5 to 0.4 still cut the remaining cast by a quarter. Going on
to 0.3 started to grey the lamp's own warmth on the renders (compared side by side), so 0.4 is where the gain stops.

**Measurements** (`measurements_v5.json`: per photo, v4 and v5 settings, max pixel difference against v4, and the
metrics on a 256 px copy):

| pool | photos | lamp step | pixels changed vs v4 | other settings changed vs v4 |
|---|---|---|---|---|
| derinkuyu_underground_city | 32 | 6 | 6 | 0 |
| richat_structure_sahara_eye | 20 | 0 | 0 | 0 |
| nazca_lines_runway_paradox | 20 | 0 | 0 | 0 |
| roman_self_healing_concrete | 28 | 0 | 0 | 0 |
| amber_room_heist | 20 | 0 | 0 | 0 |
| lycurgus_cup_nanotech | 17 | 0 | 0 | 0 |
| odessa_catacombs | 19 | 0 | 0 | 0 |
| gobekli_tepe_enclosure_d | 11 | 0 | 0 | 0 |
| kailasa_temple_monolith | 20 | 0 | 0 | 0 |
| hal_saflieni_hypogeum | 14 | 0 | 0 | 0 |
| production Derinkuyu (photo 08, the door) | 1 | 1 | 1 | 0 |

Daylight skies, foliage, deserts, warm interiors (Amber Room, the Lycurgus cup, Odessa's lamps) and monochrome or
sepia prints are byte-identical to version 4.

| photo | cast raw / v4 / v5 | neutral hue v4 / v5 | saturation v4 / v5 | lime share v4 / v5 | hue shift | warm pixels: hue moved / saturation kept | contrast (luma std) v4 / v5 |
|---|---|---|---|---|---|---|---|
| Derinkuyu 20 | 0.377 / 0.189 / 0.098 | 55 / 52 | 0.330 / 0.283 | 0.11 / 0.00 | 1.7 | 0.5 / 0.98 | 44.0 / 44.0 |
| Derinkuyu 24 | 0.400 / 0.205 / 0.079 | 59 / 55 | 0.367 / 0.224 | 0.60 / 0.02 | 3.6 | 0.9 / 0.97 | 30.1 / 30.1 |
| Derinkuyu 25 (door) | 0.665 / 0.426 / 0.121 | 62 / 55 | 0.490 / 0.244 | 0.87 / 0.04 | 4.9 | 1.3 / 0.96 | 38.9 / 38.9 |
| Derinkuyu 26 | 0.231 / 0.096 / 0.062 | 52 / 48 | 0.254 / 0.235 | 0.01 / 0.00 | 1.4 | 0.5 / 0.99 | 22.7 / 22.7 |
| Derinkuyu 30 | 0.357 / 0.159 / 0.084 | 53 / 51 | 0.280 / 0.229 | 0.13 / 0.01 | 2.4 | 0.9 / 0.97 | 30.6 / 30.6 |
| Derinkuyu 32 | 0.585 / 0.359 / 0.187 | 53 / 52 | 0.456 / 0.385 | 0.28 / 0.04 | 2.1 | 0.9 / 0.97 | 28.0 / 28.0 |
| production 08 (door) | 0.713 / 0.469 / 0.131 | 61 / 55 | 0.515 / 0.253 | 0.88 / 0.04 | 4.9 | 0.7 / 0.98 | 46.2 / 46.2 |

Column definitions:

- **cast**: the neutral candidates' `max|mean/channel - 1|`.
- **lime share**: the share of pixels at hue 52-90 deg with saturation above 0.3.
- **hue shift**: the saturation-weighted mean change from version 4, in degrees.
- **warm pixels**: the pixels at hue 10-48 deg with saturation above 0.2 in the version 4 output.

Contrast is unchanged because luminance is kept per pixel. On these photos the cast left after the step averages
0.11, against v4's 0.27, and the lime share falls from 0.41 to 0.02.

The step adds about 1.8 s to a 12 MP photo. It runs once per photo; the result is cached.

**Renders** (half size, fitted within 1600 px):

- `v5_derinkuyu_NN_raw_v4_v5.jpg` and `v5_production_prod08_raw_v4_v5.jpg`: raw, then v4, then v5.
- `v5_amber_03_`, `v5_lycurgus_09_`, `v5_derinkuyu_19_` and `v5_derinkuyu_21_v4_v5_unchanged.jpg`: v4 and v5,
  with identical halves.

Reproduce with `v5_evidence.py <repo> <out_dir>`. The script runs version 4 from commit d333bc6 beside the working
tree, with `CACHE_DIR` in a temporary folder.

**The covers' still grade.** The re-saturation the critic measured comes from the grade in the critic's own mockup
code (`brand4.py` `grade()`: auto-expose, S-curve, split tone). Production has no such grade. In `src/visual_engine.py`,
`generate_thumbnails` and `generate_depth_layered_thumbnail` draw `cover_crop` of the balanced hero, then add only
neutral black veils, scrims and shadows. `tests/test_colour_v5_still_grade_2026_10_29.py` now holds the covers to
this: no tint, and hue within 2 deg of the balanced hero. If the overhaul ports the mockup grade, it must keep the
picture's hue and scale saturation by at most 1.03, as `video_composer.film_grade_filter` does.

**Downstream, not fixed here.** `art_direction.grade_bias` returns 0 only while the balanced hero still measures
as lamp-lit with an off-white cast. Once the step has finished the correction, the door no longer measures that
way, so a warm bias comes back: +0.031 for production 08, +0.017 for 25 and +0.020 for 32. Version 4 already did
this on 24 (+0.028), 26 and 30. Through the film grade, a bias of 0.04 added about 0.04 of cast, so the door would
end near 0.16, still far below v4's 0.42. The fix belongs in `art_direction`: decide from the hero's lineage (a
`colour_balance` record whose gains are not 1, or that has a `lamp_step`) instead of re-measuring the corrected
copy.
