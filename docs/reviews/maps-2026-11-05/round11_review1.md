VERDICT: NOT OK

The working tree has no wrong water, country or island name on any of my 98 new renders. It fails on design: 13 renders score below 9, which the brief counts as blocking. There are two regressions against HEAD (Luxembourg's name, Westminster's "34 YEARS LATER"). Two of the directive items are only partly done (route home names go unchecked; the 5 km topic-country tie-break overrides the ground the site sits on).

**Counts**
- **Graphics tests** (24 files plus round 15, `-n 2`): 1,832 passed, 0 failed, in 67 min.
- **Full suite** (`-n 2`): 6,493 passed, 13 skipped, 0 failed, in 82 min. The md5 of `src/graphics.py` and `src/geo.py` was the same at the start, middle and end.
- **Ruff**: all checks pass on `src/graphics.py`, `src/geo.py`, both fetch scripts, `tests/geo_truth.py` and the round test files.
- **Expected values**:
  - The working tree changes no test that exists on origin/main. `tests/test_graphics.py` matches HEAD.
  - Its 12 lines deleted against origin/main are the door and ventilation test, removed in an earlier commit on this branch, not in this round.
  - `test_r3`, `test_r4` and `test_timeline_hierarchy` are branch-only.
- **New renders**: 49 jobs, never used in the earlier review folders, at both sizes (98 images), each with a HEAD render beside it on a board. I looked at every one.
  - 44 map places:
    - Coasts: Mont-Saint-Michel, Cabo da Roca, Dubrovnik, Neum, Trieste, Malmö, Lamu, Port Arthur.
    - Islands and atolls: Valletta, Aitutaki, Bora Bora, Palmyra, Saint-Pierre, Bermuda, Christmas Island, Lampedusa, Aruba, Mayotte, Kwajalein, Minicoy, Niue, Guam, Funchal, Ponta Delgada, Grenada, Koror, Seychelles.
    - Enclaves, exclaves and microstates: Macau, Melilla, Oecusse, Point Roberts, Jungholz, Luxembourg, Brunei.
    - Three-country borders: Vaals, Leticia, Prespa.
    - Poles: Dome A, Cape Columbia, Arctic Ocean drift.
    - Antimeridian: Lomaloma, Adak, Kanton, Tonga.
  - 2 routes: Morocco and Vietnam.
  - 3 timelines: Pisa, Westminster Abbey, Sagrada Família.
- **My own checker** (`h/mychk.py`): raw Natural Earth 1:10m subunit, country and marine files, its own ray-cast point-in-polygon, and an 11×5 sample grid over every drawn name.
  - 6 flags, all from the 5 km topic-country tie-break: Leticia ×2, Prespa ×2, Vaals ×2. The topic's country is named although the site's point lies on the neighbour's polygon.
  - No name over the wrong land, and no wrong water name.
  - Rerun on the fixer's own renders in `r11build`, it flags the same tie-break for Büsingen, Baarle, the Golden Triangle and Treriksröset. The Zanzibar flag is my checker's mistake; TANZANIA is correct there.

**Scores (9:16 / 16:9)**
- 9 / 9: Cabo da Roca, Lamu, Bermuda, Guam, Cape Columbia (16:9; 9:16 is 8), Pisa timeline.
- 8.5: Mont-Saint-Michel, Neum, Malmö, Port Arthur, Bora Bora, Funchal, Grenada, Koror, Oecusse, Brunei, Leticia, Point Roberts, Jungholz, Dome A, Tonga, Mayotte, Prespa, Vaals 16:9, Morocco route.
- 8: Dubrovnik, Aitutaki, Christmas Island, Niue, Ponta Delgada, Lampedusa, Kanton, Arctic drift, Seychelles 16:9.
- Blocking (below 8):

| Place | Score (9:16 / 16:9) | Problem |
|---|---|---|
| Luxembourg | 7 / 7 | No home name at either size, although the 16:9 fill is about 250×420 px. HEAD named LUXEMBOURG. Regression. |
| Melilla 16:9 | 6.5 | SPAIN is set 430 px from the site with a 7 px leader to the Chafarinas islets. Melilla's own fill under the marker has no name. |
| Macau | 6.5 / 7 | Framed at 2 km on coarse data, so straight-edged China and a rectangular Hengqin. The title breaks "RUINS OF ST / PAUL'S, MACAU", splitting a proper name. Title says MACAU, home says MACAO. |
| Vietnam route | 6.5 / 7.5 | HUE IMPERIAL CITY sits over Laos directly under THAILAND, so it reads as being in Thailand. VIETNAM is jammed 6 px under the Hanoi label. Cambodia is not named. |
| Vaals 9:16 | 6.5 | NETHERLANDS is set outside over the Wadden Sea, though the empty fill holds it easily. The bottom half (Switzerland, Italy, the Mediterranean) has no names. |
| Trieste 9:16 | 7.5 | No water named, although half the frame is the Adriatic. Slovenia, 10 km away, is not named while Croatia is. |
| Minicoy 9:16 | 7.5 | OMAN, 2,000 km away, is named; the Maldives, 130 km away, and Sri Lanka are not. |
| Saint-Pierre | 7.5 | 18-point outlines at about 25 px per edge. Canada not named at 9:16. At 16:9 the globe sits on Newfoundland. |
| Kwajalein, Palmyra, Valletta, Aruba 9:16, Adak 9:16, Lomaloma | 7.5 | See problems 9, 10 and 11 below. |
| Westminster 16:9 | 8 | "34 / YEARS LATER" on two rows where HEAD fit it on one. "887 YEARS LATER" is over the spine. |
| Sagrada 16:9 | 7.5 | "THE TOWER / OF JESUS CHRIST / IS COMPLETED" splits a proper name. |

**Problems**
1. **Luxembourg's name is dropped** (`src/graphics.py:3129`, `src/graphics.py:4200`). Both sizes log "fits neither its own fill nor open water". The country is far larger than Lesotho, which does hold its name. Regression against HEAD.
2. **A home name can point at any speck of its country** (`country_outside_place` at `src/graphics.py:2992`, `ground_leader` at `:3874`).
   - Melilla: SPAIN points at the Chafarinas.
   - Bora Bora: FRENCH POLYNESIA points at Taha'a.
   - Adak 9:16: a 90 px leader runs to Great Sitkin.
   - The leader should go to the polygon that holds the site, or the name should sit in that fill.
3. **The topic country overrides the ground that holds the site** (`src/geo.py:1238-1243`). Within 5 km, the hint wins even when another country's polygon holds the site (Vaals, Prespa, Leticia, Büsingen, Baarle, Golden Triangle, Treriksröset). That is more than a tie-break, and it departs from directive 1. The marker then sits on a polygon other than the highlighted one.
4. **Route home names are never checked** (`src/graphics.py:10321`, `:10349`, `:10480`). When the base map has already named the home (MOROCCO), no NameCheck is recorded. The layout shows `country: None`, so neither checker sees a name that is drawn. Directive 5 asks for every drawn name to be checked.
5. **Ruling R11 regression** (`wide_span_lines`, `src/graphics.py:6195`, and the span search near `:6680`). Westminster 16:9 sets "34 / YEARS LATER" on two rows; HEAD fit it on one in the same gap. R11 says to keep one row where it fits. "887 YEARS LATER" takes the over-spine last resort.
6. **Proper name split in a timeline label** (`NAME_EPITHET_LINKS`, `src/graphics.py:4995`). Sagrada 16:9 sets "THE TOWER / OF JESUS CHRIST". Only "and" links are held together, not "of".
7. **Site title splits a proper name**: Macau 9:16 reads "RUINS OF ST / PAUL'S, MACAU".
8. **Outline gate allows crude shapes** (`ISLAND_VERTEX_PX_RAW = 28`, `src/graphics.py:2471`).
   - At up to 28 px per source edge, polygons look angular: Saint-Pierre, Kwajalein's leaf shape, São Miguel, Niue.
   - Neighbours are not gated at all: Macau's China and Hengqin, Lomaloma's octagon islets, Saint-Pierre's Newfoundland.
9. **Leader stubs read as apostrophes**: 5 to 7 px ticks after MALTA, MARSHALL ISLANDS, SEYCHELLES (16:9) and ARUBA (`ground_leader`). In Valletta 9:16, MALTA is set outside although the island holds it.
10. **Neighbours and water left unnamed** (directive 6, still open).
    - The 9:16 graphics box ends around y=1150, so big land and sea below it go unnamed: Italy and the Ionian Sea (Dubrovnik), the Adriatic (Trieste), Venezuela (Aruba), Switzerland and Italy (Vaals), Greenland (Cape Columbia 9:16), Russia (Arctic drift).
    - Neighbours are also badly chosen: Minicoy names OMAN but not the Maldives; Trieste names CROATIA but not SLOVENIA.
11. **Weak home names on small territories**:
    - Palmyra's home name repeats the title ("PALMYRA ATOLL"), and UNITED STATES appears nowhere.
    - The site title overlaps its own island at Aitutaki, Niue, Mayotte, Seychelles, Adak and Lomaloma (which also covers an islet).
12. **Smaller points**:
    - The US is "UNITED STATES" at Point Roberts 9:16 but "UNITED STATES OF AMERICA" at 16:9 and on Adak.
    - My checker's own false positives, which I corrected for: Timor-Leste, Zanzibar.

**Images and outputs** (all in `./maps_work/r11rev1/`)
- `after/` and `before/`: 98 working-tree and 98 HEAD renders, with logs and layout files.
- `boards/ba_<job>_<size>.jpg`: 98 HEAD-against-working-tree boards.
- `sheets/`: 24 contact sheets, plus `sheets/tl169.jpg` for the 16:9 timelines.
- `v/p_after/<job>.jpg`: 9:16 and 16:9 side by side. Crops: `v/valletta_leader.png`, `v/mel.png`, `v/kwa.png`, `v/ait.png`, `v/vn916.png`, `v/dub_mk169.png`, `v/tl916.jpg`.
- Checker output: `mychk_after.txt` (my renders) and `mychk_r11build.txt` (the fixer's renders).
- Test logs: `gfx.txt` and `full.txt`.
- Harnesses: `h/hr.py`, `h/mychk.py`, `h/pair.py`, `h/boards.py`, `h/gfx.sh`, `h/full.sh`.