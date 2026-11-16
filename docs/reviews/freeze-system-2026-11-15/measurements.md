# Cycle 0: before / after on real runs (round 2: base ba3e0141 vs head = the cycle-0 tree after the adversary round)

All inputs real (the development checkout's output/run_* and assets/, read only), through the freeze-panel fixtures
catalogue and the runs' recorded recipes. Scripts in this folder: `probe_masthead.py`, `probe_youtube.py`,
`compare_rs.py` (over QF-35a's `render_surfaces.py --keep`), `bridge_frames.py`. Contrast for the masthead is the
WORST LETTER since round 2 (src/masthead.py local_contrast); "aggregate" is the element's whole ring, as round 1 and the
base measured it.

## Carousel and Facebook gallery masthead (56 slides: Gol Gumbaz 1793278143, Nazca 1793247464 and 1793251266, Richat 1793272675; IG + FB)

| | base (aggregate) | cycle 0 round 1 (aggregate) | cycle 0 round 2 (worst letter) |
|---|---|---|---|
| masthead drawn | 56 | 56 | 51 (5 fail closed, recorded) |
| contrast, min / median, drawn slides | 5.50 / 5.77 | 6.50 / 6.53 | 5.56 / 6.51 |
| drawn slides >= 6.5 target | 5 | 56 | 43 |
| drawn slides < 5.5 gate | 0 | 0 | 0 |
| largest halo | 0.60 (Richat s1, a ruling breach) | 0.16 | 0.16 |
| scrim at the masthead | cap 0.85 | peak <= 0.60 | peak <= 0.60 |
| levels step used | - | 14 | 20 |
| mark non-text contrast, min | (edge never drawn) | (edge never drawn) | 4.37 (gate 3.0) |

The 5 not drawn: Nazca 1793247464 s04 (IG, FB) and s06 (IG), Nazca 1793251266 s04 (IG, FB): their worst letter
reaches 4.08-4.21 after the 0.60 scrim, the 0.16 halo and the levels floor (round 1 reported them at 6.19-6.31: the
aggregate hid a bright line behind single letters). With the halo cap at 0.24 all five reach 5.56-5.59 (0.48:
6.52-6.59); 0.16 is an operator ruling, so the build keeps it and fails closed.
`carousel_nazca_04_fail_closed_before_after.jpg`. Files: `carousel_masthead_base.json` (base, probed on
18049e0e; the carousel's masthead code is the same at ba3e0141), `carousel_masthead_head.json` (round 2).

## YouTube thumbnail masthead (5 covers fixtures: Wieliczka, Greek fire, Tunguska, Amber, Richat)

| | base | round 2 |
|---|---|---|
| fill | GOLD | BONE |
| planned contrast | 8.00-9.96 (aggregate) | 10.92-13.72 (worst letter) |
| delivered after 960 px q70, worst letter, min | - | 9.78 (Greek fire) |
| halo | 0.35 on every case | 0.0 |
| mark non-text contrast, min | - | 7.23 |

Files: `youtube_masthead_base.json`, `youtube_masthead_head.json`.

## Every reachable surface (render_surfaces, 158 surfaces, --per-kind 1; 0 errors on both trees)

`real_runs_compare.txt` (last frame per surface for the regions; hashes over every frame):

| surface | changed | region (last frame, real runs) |
|---|---|---|
| IG carousel + FB gallery slides | 56 of 56 (Story frames 12: identical) | y <= 490: the masthead's scrim eases out at baseline + 300 design px (by design), the levels step under the same shape, the name, the mark |
| YouTube thumbnail | 5 of 5 | x 95-805, y 9-175 (the masthead box) on these five. NOT a general bound: the ink shade's feather is fitted to the type's right edge, masthead included, so where a headline is shorter than the masthead (the adversary's short-headline fixture) the narrower masthead moves the full-height shade |
| 16:9 end screen | 4 of 4 | x 154-1513, y 130-254: masthead mark (edge over fill, em geometry) and WATCH NEXT as the system kicker |
| 9:16 end card | 5 of 5 | the masthead group (a 117 px band) |
| photo callouts (reference pair, Amber) | 1 of 1 | every frame but one of 180 (the dim and plate shadow are INK, not black): at most 9 levels (frame 73) |
| captions (incl. badge frames), hook / chapter / myth cards (27), Reel covers + grid tiles (10), maps, number cards, then-and-now, loop bridges (10) | 0 | identical |

Cards: the line-end list now refuses "into" / "onto" / "via" / "per" at a line end on the cards too (it allowed them);
0 of the 27 real card cases change; a title that breaks on one of those words (the adversary's synthetic "into"
chapter: 136 -> 128 px) steps to the next size, as it does for "of".
