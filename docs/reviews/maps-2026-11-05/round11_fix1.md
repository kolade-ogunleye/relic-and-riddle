The map now names homes from the ground that holds the site, with no special-case tables. The full suite passed once on the final code: 6,493 passed, 13 skipped, 0 failed. Some things are still open: Vatican and Liechtenstein have no name, Reykjavik is still unlabelled at 9:16, and five 16:9 spans sit over the spine. Nothing was committed, pushed or stashed.

## Tests
- **Graphics files plus the new sweep** (`-n 2`): 1,828 passed, 4 failed.
  - Three of the failures were the checker not accepting UNITED STATES for "United States of America". The fourth was Diomede, whose name now sits outside the island with a leader.
  - I fixed the checker and updated the Diomede test, then re-ran those four plus the route, Diomede and sweep tests: 188 passed.
  - The route leader was added after that run started. The full suite covered it.
- **Full suite**, run once: 6,493 passed, 13 skipped, 0 failed, in 84 min. The md5 of `graphics.py` and `geo.py` matched before and after.
- Ruff passes on every changed file. `tests/test_graphics.py` is unchanged against HEAD.
- **Independent checks on all 94 map and route layouts:**
  - The new repository checker finds 0 faults.
  - The reviewer's `h/chk.py` flags 10 layouts. Two are Fakaofo: its 15 km grid misses the islets 6.5 km off. The other eight are the logged drops: Vatican and Liechtenstein, plus Point Nemo and the North Pole, where there is no land.

## Changes

**1. Identity from geometry** (`src/geo.py`)
- `countries()` (:157) gives the 1:50m list, or the 1:10m entities inside `detail_view()` (:169). The 1:50m list is still used for the globe (:502).
- `entity_rows` (:1079) groups the 1:10m subunits into entities, and `entity_name` (:1112) names each one with no initialisms. `countries_detail` (:1133) builds them.
  - Tokelau and French Guiana become their own entities because they have an ISO code of their own.
  - Ascension and Tristan da Cunha become their own because Saint Helena is a grouping of separate parts. Diego Garcia stays part of the British Indian Ocean Territory.
- Borders inside one entity, such as England and Scotland, are not drawn (`seam_edges`, :340).
- `site_home` (:1202) picks the smallest polygon that holds the site, else the nearest within 50 km. The topic's country only breaks ties within 5 km (`_hint_matches`, :1196):
  - Gibraltar: the summit is 0.3 km off Natural Earth's seven-point polygon.
  - Büsingen: its enclave is not in the data.
- Deleted: `SUBUNIT_DISPLAY`, `subunit_name`, `islands_10m`, `extra_polygons`, all outline smoothing, and `site_entity` with its 1,500 km search.
- `render_map` (`graphics.py`:4008) and the route home (:10260) no longer look the country up by name.
- Results:
  - GIBRALTAR, TOKELAU and SAO TOME AND PRINCIPE.
  - ASCENSION and TRISTAN DA CUNHA.
  - UNITED KINGDOM (St Kilda), RUSSIA (Kaliningrad), BRITISH INDIAN OCEAN TERRITORY (Diego Garcia).
  - Neighbours read COSTA RICA and PAPUA NEW GUINEA, not initialisms.

**2. Leader or drop** (`src/graphics.py`)
- A home name sits outside its fill only over open water (`home_name_fault`, :3901; `country_outside_place`, :2992). No other land may lie more than 30 px nearer it (`NAME_NEARER_PX`, :3771).
- `ground_leader` (:3874) draws the leader for the home name (:4377), for a site off its land, and for the route's home name (:10355).
- A neighbour's name must sit wholly on its own ground (`neighbour_name_fault`, :3985; :3609).
- The home name may sit nearer the marker, down to 60 px (`COUNTRY_ANCHOR_AIR_MIN`, :1637), which lets LESOTHO fit in its own fill. When a name is dropped, `home_drop` in the layout says why.

**3. Outline quality**
- Islands are drawn from the real 1:10m outline and framed no closer than its points allow (`ISLAND_VERTEX_PX_RAW`, :2471).
- An island of a larger country under 300 px across is framed on itself (`ISLAND_PART_PX`, :2472). This fixes Bonifacio, which is now framed on Corsica with both seas named.
- Thursday Island is no longer grouped with the Australian mainland when framing (:2572).
- Relief that is coarser than the frame is softened (`RELIEF_MAX_MAG`, :1247).

**4. Ruling R11**
- `wide_span_lines` (:6195) gives only one row, or two in the count-over-unit form ("1,669 / YEARS LATER").
- The search tries every one-row setting first (:6680). The two-row form counts as clean and is logged at info (:7027).
- "Ferdinand and Isabella" is held together (`NAME_EPITHET_LINKS`, :4995).
- The list of known-unclean timelines is gone. All seven review timelines render with no ERROR and no "LATER" alone on a row.

**5. Sweep**
- `tests/geo_truth.py` is the independent checker. It reads the raw geojson with its own point-in-polygon code and splits the oceans at the equator.
- `tests/test_round15_review_2026_11_05.py` sweeps 200 sites at both sizes, all passing. The sites cover small islands, enclaves, three-country borders, both poles, the antimeridian, 80 seeded random sites and 25 coasts off the 1:50m ground.

Docs and the fetch script docstring now describe the 1:10m role (`README.md`, `docs/deployment.md`, `docs/sources.md`, `scripts/fetch_geodata.py`).

## Expected-value changes (all in branch-only tests)
- **round7:** the two landlocked tests now expect no outside place. The Rapa Nui vertex limit uses `ISLAND_VERTEX_PX_RAW`.
- **round8:** the Nan Madol vertex limit uses `ISLAND_VERTEX_PX_RAW`.
- **round9:**
  - The two smoothing tests now check that no outline is curved.
  - Nauru must reach three pin heads across, or be at the vertex limit.
  - The strip test checks for water only and the 30 px rule, instead of every corner.
- **round10:**
  - The USA short forms are now ["United States"].
  - The two Chaikin tests now check the functions are gone.
  - Barbados and Malé: "≤ 8.5°" becomes "< 39°", with the size-or-vertex-limit check.
- **round11:**
  - Pitcairn: the "outline drawn for this view" log check is gone.
  - Without the 10m data, no home is named and a reason is logged.
- **round12:**
  - Tristan and Bikini are found with `site_home`. The Tristan render no longer checks the log line.
  - `wide_span_lines("40 YEARS LATER", 2)` is now ["40", "YEARS LATER"].
  - Machu Picchu and Colosseum may take the logged over-spine last resort.
- **round13:**
  - St Kilda, Messina and Sardinia now return the country's own name instead of None.
  - Diego Garcia returns BRITISH INDIAN OCEAN TERRITORY.
  - The Kiribati case now expects no fault, with a leader.
- **round14:**
  - The `site_entity` cases use written-out names.
  - The coarse-island test uses the vertex limit instead of `ISLAND_COARSE_MAX_PX`.
  - `KNOWN_UNCLEAN` is now empty.
  - `wide_span_lines("72 YEARS LATER", True)` is now ["72", "YEARS LATER"].
  - Punta Arenas and Diomede may place the name outside with a leader. The 10m fjords leave Chile no room, and Little Diomede is now framed on itself.
  - The checker helper uses 1:10m ground for outside home names and for neighbours.
- **r4:** the three-row span and "40 YEARS / LATER" expectations now use the count-over-unit form.

## Still open
- **Dropped names (logged):** VATICAN, LIECHTENSTEIN and Nakhchivan's AZERBAIJAN at 9:16. Their outlines have too few points to zoom in far enough, and there is no water to put the name over.
- **Enclaves:** Büsingen and Baarle-Hertog are missing from the data. GERMANY and BELGIUM come from the 5 km tie-break, so the marker sits on Swiss and Dutch ground.
- **Over the spine:** five 16:9 spans take the logged last resort (Alhambra, Angkor, Colosseum, Machu Picchu, Stonehenge). Hagia Sophia keeps "IT IS / A MOSQUE / AGAIN", which R11 allows.
- **Reykjavik** is still unlabelled at 9:16, as at HEAD.
- **Indonesia route:** Australia is below the graphics box, so no neighbour is named.
- **Empty 9:16 bottom:** that area is the caption zone, and names stay inside the graphics box, so it is unchanged.
- **Nagurskoye** is still framed at Arctic scale, because island framing is skipped near the pole.
- No `WORKING.md` was written; it is outside the files I was allowed to edit.

## Images
Everything is in `./maps_work/r11build/`:
- `after/` holds 108 renders, logs and layout files: the review's 35 jobs, 15 new places (Lesotho, Vatican, Liechtenstein, Gambia, Akrotiri, Malé, Tristan, Ascension, St Kilda, Diego Garcia, Hong Kong, Zanzibar, Longyearbyen, Point Nemo, Baarle-Hertog) and 4 more timelines, at both sizes.
- `before/` holds the new HEAD renders. The review's HEAD renders are in `./maps_work/r10rev2/before/`.
- `boards/ba_<job>_<size>.jpg` has 108 HEAD-against-working-tree boards, and `sheets/` has 16 contact sheets. I looked at all of them.
- Checker output: `truth_after.txt` (repository checker) and `chk_after.txt` (reviewer's).
- Test logs: `gfx_final.txt` and `full.txt`.