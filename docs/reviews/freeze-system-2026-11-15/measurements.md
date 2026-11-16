# Cycle 0: before / after on real runs (round 3: base 6a7ef303 vs head = the cycle-0 tree after the second adversary round)

All inputs real (the development checkout's output/run_* and assets/, read only), through the freeze-panel fixtures
catalogue and the runs' recorded recipes. Scripts in this folder: `probe_masthead.py`, `probe_youtube.py`,
`compare_rs.py` (over QF-35a's `render_surfaces.py --keep`), `bridge_frames.py`, `halo_cap_what_if.py`. The masthead's
contrast is the WORST GLYPH since round 3 (src/masthead.py `judge`: each glyph's own ink against the ground under it
and its 1-4 px surround at the 95th percentile, and the element's whole and windowed rings); "aggregate" is the
element's whole ring, as the base measured it.

## Carousel and Facebook gallery masthead (56 slides: Gol Gumbaz 1793278143, Nazca 1793247464 and 1793251266, Richat 1793272675; IG + FB)

| | base (aggregate) | round 1 (aggregate) | round 2 (windows) | round 3 (worst glyph) |
|---|---|---|---|---|
| masthead drawn | 56 | 56 | 51 | 49 (7 fail closed, recorded) |
| contrast min / median, drawn | 5.50 / 5.77 | 6.50 / 6.53 | 5.56 / 6.51 | 5.52 / 6.51 |
| drawn at >= 6.5 target | 5 | 56 | 43 | 38 |
| drawn under the 5.5 gate | 0 | 0 | 0 | 0 |
| largest halo | 0.60 (Richat s1, a ruling breach) | 0.16 | 0.16 | 0.16 |
| levels step used | - | 14 | 20 | 19 |
| mark non-text contrast, min | (no edge drawn) | (no edge drawn) | 4.37 | 4.37 |

The 7 not drawn (all Nazca: the bright geoglyph lines behind single letters): 1793247464 s04 IG / FB, s06 IG, s05 FB;
1793251266 s04 IG / FB, s05 FB. Best any ruled step reaches: 5.26-5.49 (the full 0.60 scrim, the 0.16 halo, the 205
levels floor). What-if, measured only (`halo_cap_what_if.json`, the code keeps 0.16): a 0.24 halo cap draws all of them
at 5.56-5.59; 0.32 at 5.51-5.58 (the solver takes the least change that clears the gate). Files:
`carousel_masthead_base.json` (base; the carousel's masthead code is unchanged since 18049e0e), `carousel_masthead_head.json`
(probed just before the last edit, which makes a refusal's `contrast` equal its `best_tried`: read `best_tried` there;
nothing else in the record or the pixels moved).

## YouTube thumbnail masthead (5 covers fixtures: Wieliczka, Greek fire, Tunguska, Amber, Richat)

| | base | round 3 |
|---|---|---|
| fill | GOLD | BONE |
| contrast | 8.00-9.96 (aggregate) | 10.92-13.63 (worst glyph, 12 glyphs each) |
| delivered after 960 px q70, whole ring, min | 7.57 | 10.48 |
| halo | 0.35 on every case | 0.0 |
| mark non-text contrast, min | - | 7.23 |

## Every reachable surface (render_surfaces, 158 surfaces, --per-kind 1; 0 errors on both trees)

`real_runs_compare.txt` (exact boxes of EVERY differing pixel, `box_any`, and of differences above 2 levels, `box_gt2`,
on each surface's last frame; hashes over every frame):

| surface | changed | every differing pixel (union of boxes) | above 2 levels |
|---|---|---|---|
| IG carousel slides | 28 of 28 | x 0-1439, y 0-513 | y 0-490 |
| FB gallery slides | 28 of 28 | x 0-1439, y 0-513 | y 0-490 |
| Story frames | 0 of 12 | - | - |
| YouTube thumbnail | 5 of 5 | x 95-815, y 0-176 | x 95-805, y 9-175 |
| 16:9 end screen | 4 of 4 | x 154-1517, y 130-259 | x 154-1513, y 130-254 |
| 9:16 end card | 5 of 5 | x 113-845, y 556-705 | the same |
| photo callouts (reference pair, Amber) | 1 of 1 | the whole frame, 179 of 180 frames, at most 9 levels (frame 73) | the whole frame |
| captions (incl. badge frames), 27 hook / chapter / myth cases, Reel covers + grid tiles, maps, number cards, then-and-now, loop bridges | 0 | identical | - |

Why the carousel reaches y 513: the masthead scrim and the levels step ease out at `reach` = baseline + 300 design px
(~y 533 on 1440) and the 1-2 level tail of the smoothstep ends there. On YouTube the five real covers keep the change to
the masthead box; a headline shorter than the masthead moves the full-height ink shade (its feather is fitted to the
type's right edge, masthead included: the adversary's short-headline fixture). Cards: a card title that would break on
"into" / "onto" / "via" / "per" steps to its next size (the adversary's synthetic "into" chapter 136 -> 128); 0 of the
27 real card cases change.

## The builder's adversarial sweep (`sweep.py`, `sweep_results.json`)

1,473 grounds (stripes 1-48 px x 27 offsets x 3 angles, checkerboards 1-8 px, noise x 10, the narrow-letter name under
stripes 1-8 px x 20 offsets x 2 angles): 0 failures; 607 drawn, all passing the independent per-glyph check (min 5.50);
866 fail closed, all untouched with a reason and a best under the gate.
