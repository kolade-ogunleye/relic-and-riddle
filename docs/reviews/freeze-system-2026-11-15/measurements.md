# Cycle 0: before / after on real runs (base 18049e0e, head = the cycle-0 tree before the rebase onto 726da5b2)

All inputs real (the development checkout's output/run_* and assets/, read only), through the freeze-panel fixtures
catalogue and the runs' recorded recipes. Scripts in this folder: `probe_masthead.py`, `probe_youtube.py`,
`compare_rs.py` (over QF-35a's `render_surfaces.py --keep`), `bridge_frames.py`.

## Carousel and Facebook gallery masthead (56 slides: Gol Gumbaz 1793278143, Nazca 1793247464 and 1793251266, Richat 1793272675; IG + FB)

| | before | after |
|---|---|---|
| planned contrast, min / median | 5.50 / 5.77 | 6.50 / 6.53 |
| delivered after 960 px q70 re-encode, min | 5.53 | 6.47 |
| slides at or above the 6.5 target | 5 of 56 | 56 of 56 |
| slides under the 5.5 gate | 0 | 0 |
| largest halo | 0.60 (Richat s1, tight: a ruling breach) | 0.16 |
| largest scrim at the masthead | 0.85 cap | 0.60 peak |
| levels step used (ceiling 205-245) | - | 14 (Nazca: flat bright desert) |
| light-ground INK variant used | - | 0 |

Files: `carousel_masthead_base.json`, `carousel_masthead_head.json`.

## YouTube thumbnail masthead (5 covers fixtures: Wieliczka, Greek fire, Tunguska, Amber, Richat)

| | before | after |
|---|---|---|
| fill | GOLD (242, 182, 48) | BONE (243, 237, 226) |
| planned contrast, min-max | 8.00-9.96 | 11.05-15.18 |
| delivered (960 q70), min | 7.57 (Greek fire) | 10.48 (Greek fire) |
| halo | 0.35 on every case (min_halo) | 0.0 (no scrim needed: the left ink shade carries it) |

Files: `youtube_masthead_base.json`, `youtube_masthead_head.json`.

## Every reachable surface (render_surfaces, 158 surfaces, --per-kind 1; 0 errors on both trees)

`real_runs_compare.txt`. Identical: captions 20 (incl. badge frames), cards 27, covers' Reel cover and grid tile 10,
maps 2, number cards 2, then-and-now 4, social Story frames 12. Differing: social carousel / FB slides 56 (masthead
band, y < 190), YouTube thumbnails 5 (masthead box only), end screens 4 (masthead mark and WATCH NEXT kicker, y 130-254),
9:16 end cards 5 (masthead group, a 117 px band), callouts 1 (the whole frame by at most 5 levels: the dim is INK, not
black), loop bridges 10 (hash only: the last frame is identical; the cause was one ease written in another arithmetic
form, 234 px by 1 level on one frame; restored, and the Aswan bridge's 30 frames are now bit-identical to the base).
