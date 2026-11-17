# QF-20: Shapely and pyproj under the maps

Status: built for review (round 3: the operator's rulings Q1-Q3 of 2026-11-11 applied, the round-2 review's
findings fixed, after.json measured on this commit's code; ported onto QF-26 21b4c5c0 and measured again there). The 30-new-place acceptance review comes next
(JOB-QF-20-maps). No design is frozen.

## What changed

The locator map, the route map and the globe now use GEOS (Shapely 2.2.0) and PROJ (pyproj 3.7.2) in place of the
hand-written geometry (review 2026-11-05, section 3 "Cartography"; qf20_spec.md):

- `src/map_geometry.py`: point in polygon (holes kept, the boundary counts as ground), clipping into every separate
  piece, union and intersection, the pole of inaccessibility (maximum inscribed circle, minus the label and marker
  obstacles), true distance to a boundary, the WGS84 geodesic for route legs, and one site-centred azimuthal
  projection for every locator (there is no latitude-dependent flat projection any more).
- `src/map_layers.py` with `data/geo/`: Natural Earth lakes, disputed areas and 1:10m ice shelves, committed and
  hash-checked (`natural-earth-manifest.json`; the hash is taken on the LF form, so a CRLF checkout keeps its maps).
  Lakes are water. Contested ground is neutral: grey fill, dashed outline, no name, no highlight. By the
  operator's ruling Q2 (2026-11-11) a disputed-areas feature is contested where Natural Earth records a claim:
  its NOTE_BRK names one ("Claimed by", "Self admin.", "Between"), whatever its TYPE, except a Lease or an Overlay.
  78 of the 99 features: Taiwan, Crimea, Western Sahara, Kashmir, the Falklands, South Georgia and the Kurils (as
  the answer names them), and with them Gibraltar, Dokdo, the Paracels, Wake Atoll, Hans Island, Mayotte and the
  rest. The other 21 keep their ordinary country: Israel, Belize, Kosovo, Gaza, the West Bank, Ceuta, Melilla, the
  leases (Guantanamo, Baykonur), the UN and military overlays (the UNDOF zone, the Korean DMZ and islands, Diego
  Garcia's naval facility), and the features with no claim recorded (Scarborough Reef, the Cyprus buffer zone,
  ...). `map_layers.is_contested` holds the rule; `disputed-areas-q2.md` lists all 99 with TYPE, NOTE_BRK and the
  verdict.
- `src/geo.py` and `src/graphics.py`: the hand-rolled ray casting, Sutherland-Hodgman clipping, polylabel quadtree,
  azimuthal formulas, great-circle slerp and edge distances are deleted and call the module above.
- `src/timeline_dense.py`: five events fit a 9:16 timeline in a date column; the 16:9 timeline keeps proper names
  whole and never puts a span over the spine.
- `tests/geo_truth.py` (the independent checker, absent from origin/main) is extended by ruling Q1 only: the
  round-16 rule accepts the sovereign's name where one of the ground's own names is a whole-word run of the
  site's title, by the renderer's test (accents folded, words of A-Z, 0-9 and '); the earlier whole-title match
  is kept. "George Town" is no run of "Georgetown", and a title that does not hold the ground's name still
  rejects the sovereign (`tests/test_qf20_rulings.py`).

Fixes made in this round on top of the ported work:

- A site on unnamed ground (disputed, or a stated country the data does not hold there) is framed on the ground
  that holds it. Grytviken and Stanley were drawn at 200 km with their islands a speck; they are now framed on
  their islands, still neutral and unnamed (`geo.ground_at`, used for the span and the marker only).
- The closer framing that gives the home's fill room for its name now applies to views that reach polar latitudes
  too (Bamburgh 9:16 dropped UNITED KINGDOM; it is named again).
- A dot-on-leader title that lies over its own island takes its two-row form when that covers less of the island
  (Levuka 9:16 covered 20 % of Ovalau; now 0 %).
- The neutral ground's dashed outline never finished on some views (Pantheon 16:9 rendered for over an hour): a
  segment ending a hair past a dash boundary looped between dash and gap forever, and rings projected far off the
  canvas were walked dash by dash. Each segment is now walked only where it is on the canvas.
- A neighbour's name sits on a part of its country that carries the name, as the independent checker reads the
  ground: SERBIA was set on Vojvodina (whose own names are Vojvodina and Republic of Serbia) at four sweep renders
  (sites 71, 72, 137). Natural Earth's 1:10m subunits decide; only Serbia, Tanzania and Timor-Leste have parts that
  do not carry their name.
- On the route map, a far neighbour's name that gave way to an adjacent neighbour (round 13 rule) is not set
  again by the new pass that names the remaining visible neighbours: the Egypt route named IRAQ again after it
  gave way to SUDAN (tests/test_round13 `test_the_egypt_route_names_sudan_not_iraq`, failing before, passing now).
  The locator's own pass may still take such a name back once the adjacent ones are set, as finding 9 asks
  (Athos keeps TURKEY, Valga FINLAND; test_qf20_neighbour_layout). Applying the route's rule to locators too
  was tried in round 2 and dropped those two names.
- The 16:9 timeline's preference for two-row labels (finding 6, Amber) counts a block on three or more rows
  once, ahead of a label set past its 420 px column: round 1 counted every row over two, so a label the column
  holds in four rows was widened to 556 px instead (test_round7's KHUFU label). Amber and Nazca keep two rows;
  the dev runs' 16 timeline boards render pixel-identical.
- The topic's country matches the ground's own Natural Earth ADMIN and GEOUNIT too (`geo._hint_matches`,
  `admin_names`): Jarvis, Midway and Palmyra given as "United States Minor Outlying Islands", Christmas Island and
  Cocos as "Indian Ocean Territories" are their own ground, named as on the base, not a stated country that
  differs from it (`test_a_topic_country_given_as_the_grounds_admin_names_that_ground`). Across every Natural
  Earth name form find_country resolves (488, reviewer's scan), 23 still name no home: 19 on contested ground
  (ruling Q2), 3 Cote d'Ivoire forms whose test point lies in Lake Kossou (a lake is water), and the 1:50m
  abbreviation "Indian Ocean Ter." at Christmas Island (rare; a topic writes the full name).
- A site exactly on a shared border vertex (both polygons hold it, the boundary counting as ground) is the stated
  country's when that country's polygon is one of those holding it (`geo.site_home`): exact boundary points only,
  no distance; the coastal sweep's South Sudan vertex on the Central African Republic border named neither
  before.
- A sea's name is refused over an islet that the route map's coarser (1:50m) land leaves out: the Fiji antimeridian
  route 16:9 set SOUTH PACIFIC OCEAN over an islet at (179.413, -17.807).
- Speed, with identical pixels (byte-identical PNGs checked on Etretat, Hel, Rodrigues, Heligoland and Chelyuskin):
  point in polygon reuses prepared polygons per polygon list; a view's rings are projected in one PROJ call and
  their projected coordinates are kept per ring for the view's centre (framing redraws the same rings at many
  scales); seam edges are kept per ring; the country index keeps both the 1:10m and 1:50m lists; the framing span
  is reused within one render. CPU seconds for Etretat: 9:16 182 -> 128 (base 54), 16:9 140 -> 93 (base 54).
  Maps remain slower than the base, about 2.5x in total CPU over 8 locator renders (aggregate only; the
  production sample is cited by counts and aggregates): every vertex and every relief pixel now goes through PROJ,
  and every name is checked against the 1:10m ground.

## Measured

`python docs/reviews/quality-fixes-2026-11/QF-20/measure.py <repo root>` renders the fixed 200-site corpus at
1080x1920 and 1920x1080 (400 locators) and 17 route cases at both sizes (34 routes) from committed, hash-bound
inputs (`data/geo/benchmark-inputs`, `sweep-sites.json`, `route-cases.json`, and the shaded relief that
`python scripts/fetch_relief.py` saves to the gitignored tools/geo/relief, verified against its manifest SHA-256
and refused when missing or different), offline, and runs the unchanged
checker on every layout. It runs on the base too. Results: `before.json` (1ab9aded, with the base's checker) and
`after.json` (this commit's code, with the extended checker; its source_sha256 are this commit's files).
`after-amend-subset.json` is the round-2 amend's interim re-measure of 22 cases, kept for the record;
after.json supersedes it.
`remeasure_one.py <repo root> <site number> <width> <height>` runs one sweep case alone through measure.py's own
inputs, worker and checker (prints JSON, writes nothing).

The base's and round 2's sweeps read `sweep-sites.json` from a CRLF working copy, so the recorded `sweep_sha256`
first named the CRLF bytes; the committed file is the same JSON in LF. `recompute_input_hashes.py <repo root> [--write]` hashes the
committed inputs with measure.py's expression; it was run with `--write` on `before.json` (and on round 2's
after.json), which replaced only the two input hashes and kept the old values under `input_hashes_recomputed`. No
measured result changed (measure.py parses the file with json.loads, so line endings do not reach a render).
The round-3 `after.json` was measured on the committed LF file, so its hashes are direct.

Port onto QF-26 (21b4c5c0, one antialiased geometry path): `after.json` was measured again on the ported commit
(measure.py, 4 workers, 434 renders), and its source_sha256 are the ported files. Against round 3's sweep every
locator and route result is the same (faults, home_drop and frame count in all 434 cases; 400 / 400, 34 / 34, 370 /
400 homes named, 0 checker faults); only the frame hashes differ (434 of 434): the newer base draws the land, the
borders, the neutral ground and its dashes antialiased (QF-26), and its text and camera rows since 1ab9aded (QF-21
shaping, QF-22, QF-23, QF-94) reach the frames too. That is a pixel-level change, not a geographic one.

| | base 1ab9aded | this commit |
|---|---|---|
| locator renders passing the checker | 400 / 400 | 400 / 400 (round 1's sweep had 399 / 400: site 66 9:16 hit a MemoryError under machine load and passed alone, `site066-rerun.json`) |
| route renders passing the checker | 33 / 34 (Fiji antimeridian route: DecompressionBombError) | 34 / 34 |
| locator renders naming a home country | 390 / 400 | 370 / 400 (round 2: 368, Jarvis given as "United States Minor Outlying Islands" being unnamed; round 1: 362) |

The home-name drop (10 sites, 20 renders) is the operator's rulings, not a placement failure: 3 sites lie on
contested ground (Wake Atoll, Gibraltar, Siachen Glacier: sites 6, 41, 141; ruling Q2 names Ceuta, Melilla and
Scarborough Reef again), and 7 are tripoint or enclave sites whose stated country is not the ground that holds them
beyond the 2 km tie-break (Baarle, the Golden Triangle, Treriksroset, Iguazu, the Austria tripoint, Lake Chad, the
Evros tripoint: sites 53, 66, 67, 70, 72, 75, 76), where the base named the holding country and the ruling now names
none (logged). Jarvis (site 115) is named again by the ADMIN/GEOUNIT match. The base's own 10 unnamed renders
(Vatican, San Marino, Liechtenstein, Macau: the name fits neither fill nor water; the North Pole: open sea) are
unchanged. 0 checker faults in 434 renders.

The route cases: the real run's route (output/run_amber_room_heist_1792801257), the spec's Japan route, the
committed test routes (Turkey, Peru, Egypt, Greece, Morocco) and regional groups of the 200 sweep sites (polar,
antimeridian, island and strait stress). The sweep measures geography, not design: the design acceptance is the
independent 30-new-place review.

## The 15 starting findings (qf20_spec.md), rendered on known places at both sizes

Checked on the builder's diagnostic renders of the earlier review's 46 places, its Japan and Amber routes and its
five timelines (not committed; listed in the build note).

| # | Finding | Now |
|---|---|---|
| 1 | Island inside the mainland's bbox framed at country scale | Visby, Heligoland framed on their islands (containing ring first, then true ring distance) |
| 2 | Home-name leaders 9-15 px | home leaders measured 29-32 px on Rodrigues, Lord Howe; the site leader keeps its 24-48 px range |
| 3 | Ice-shelf seams and specks | Mawson, Neumayer, Rothera: shelves unioned with land, no specks or seams |
| 4 | High-latitude framing at 500 km | Neumayer, Mawson 100 km; Chelyuskin 100 km |
| 5 | Hel spit not drawn | the spit is drawn at 16:9 (the coast stroke keeps narrow ground visible); 9:16 is framed at 200 km, where the spit is a coast line |
| 6 | Amber timeline 16:9 on three rows | two rows |
| 7 | "DESIGNATED / UNESCO WORLD / HERITAGE" | "DESIGNATED / UNESCO WORLD HERITAGE" |
| 8 | Bamburgh UNITED KINGDOM outside, unlogged | both sizes name it outside on a leader to its own ground, and the in-fill refusal is now logged ("refused down to 20 px: small"): the fill inside the text box below the site is narrower than the name at its smallest size. 9:16 had dropped the name; it is named again (closer framing on polar views) |
| 9 | Unnamed neighbours | Czechia (Gorlitz), Gambia (Goree), Turkey (Athos), Greenland (Eureka), Finland (Valga), Tajikistan (Barak) named; Alaska is named UNITED STATES at Anadyr; American Samoa (Apia) and St Lucia/Grenada (Kingstown) are outside the text box; Lithuania is named on the Amber route at both sizes; Denmark lies outside the Heligoland 16:9 frame; the British Virgin Islands at Charlotte Amalie stay unnamed at both sizes: in frame, but no place on their own ground holds the whole name at the readable size (decisions/QF-20.md) |
| 10 | Globe over land | the globe treats all land as an obstacle (Saint-Denis: globe moved off Mauritius) |
| 11 | Relief blotches on close-ups | relief fades from 2x native magnification to flat at 4x |
| 12 | Title over its own island | Levuka fixed this round; Kingstown 16:9 off Bequia; Cockburn and Mata-Utu clear |
| 13 | Japan route: JAPAN under SENSO-JI | about 44 px clear of the stop name |
| 14 | Crude home outlines | 1:10m outlines; Natural Earth's own detail is the limit (Grand Cayman, Heligoland) |
| 15 | Rhodes 1523 dropped, span over the spine; lakes; specks | five events kept, no span over the spine; lakes drawn as water |

Operator rulings applied: SITE_HINT_KM 2 km; proper names whole; disputed ground neutral; a missing enclave names
no home (Madha); one marker per place at both sizes (decided over both formats); a home name that partly repeats
the title switches to the sovereign's name.

## Known limits (reported, not hidden)

- A site in a lake names no home: lakes are water and are taken out of every country's ground (Lake Kossou
  holds Cote d'Ivoire's representative point). Correct by the data, and logged as unmatched ground.
- Neumayer III: the checker reports ANTARCTICA as "not a name of the ground" because the site lies on an ice shelf,
  which has no name in the checker's data. The base does the same (checked); the 200-site sweep has no such site.
- Maps take about 2.5x the base's CPU time (above), after a 1.4-1.5x gain this round; a locator pair costs
  roughly 3-8 CPU minutes instead of 1-3. The map tests in the full suite take correspondingly longer.
- The 9:16 lower half stays empty on many locators and timelines: that is QF-24's row.

## Branch-only test changes (every file named is absent from origin/main)

- `test_globe_relief_2026_11_03.py`: the home mask of the quiet-relief check is drawn on the frame's own
  projection (the layout's anchor and span), because every close-up is now azimuthal; with the old flat mask the
  measured region included sea and neighbouring ground. The 9.0 luminance bound and the teal check are unchanged
  (fails 17.6 / 45.7 with the flat mask, passes with the frame's mask).
- `test_round11_review_2026_11_04.py`: PROJ's azimuthal equidistant view is computed on the WGS84 ellipsoid, so
  10 degrees of latitude from the pole are 10.0438 mean-sphere arc degrees (the true geodesic distance), not 10.0;
  the assertion now compares with that geodesic distance at the same 1e-9 tolerance.
- `test_r3_graphics_2026_11_02.py`: the country-lookup mock accepts the new keyword options; assertion unchanged.
- `test_r4_graphics_2026_11_02.py`: timelines keep five selected events instead of four (the Rhodes 1523 event,
  finding 15), the last event included; the fallback-span check covers every selected adjacent pair.
- `test_round15_review_2026_11_05.py`: (neutral ruling, kept by Q2) Gibraltar lies on contested ground and names
  no home; the identity test requires the recorded neutral decision (km 0). Diego Garcia keeps the base's
  BRITISH INDIAN OCEAN TERRITORY (under Q2 its "Diego Garcia NSF" overlay is ordinary ground; round 1 had made it
  neutral). (Ruling Q1) Ascension's home is UNITED KINGDOM and Fakaofo's NEW ZEALAND (each own name repeats the
  title) in the directives test and the offshore-leader test, each passing the extended checker.
- `test_timeline_hierarchy_2026_11_03.py`: five-event date columns are checked for shared label alignment,
  year/label separation and top alignment; the overflow fixture has six events because five now fit.
- `test_round12_review_2026_11_04.py` (ruling Q3): the Bikini close-up bound is 2.0 < span < 12.5 instead of
  < 12.0, because the span is now WGS84 arc degrees (measured 12.0209); expected-value change.
- `test_round13_review_2026_11_04.py` (ruling Q1): Ascension's home is UNITED KINGDOM (its own name repeats the
  title "Georgetown, Ascension"), and the layout must pass the extended checker; SAINT HELENA is still never
  drawn. Its Diego Garcia row is as on the base.
- `test_round14_review_2026_11_04.py` (ruling Q1): Torshavn's home is DENMARK and Norfolk's AUSTRALIA (each own
  name repeats its title), each layout passing the extended checker as well as the file's own one. (Ruling Q2)
  the coastal sweep draws its seeded sample from ordinary ground only: a vertex within 1e-6 degrees of a
  contested feature or a lake is skipped, judged from the committed layers (map_layers.index), not from site_home.
  All 16 COAST sites keep the base's assertion (home named, independent check). A new dedicated test requires
  three contested coastal cases (the Western Sahara coast, Siachen, a vertex on Crimea's boundary), each first
  shown on contested ground from the layer itself at 1e-6 degrees, to name no home, record a home_drop and pass
  the extended checker.
- `test_round16_review_2026_11_05.py`: (Ruling Q2) the Rock's summit, by contested Gibraltar, names no home
  (was GIBRALTAR, overriding). (Operator ruling, qf20_spec "Enclaves missing from Natural Earth" and HANDOFF
  section 7) Baarle (was NETHERLANDS), Treriksroset (was NORWAY) and the Golden Triangle (was MYANMAR) name no home
  past the 2 km tie-break, with the decision recorded (km 0). (Spec ruling R11 with decisions/QF-20.md's 4 -> 5
  capacity) the Pisa span test asserts four spans, exactly one of them two-row ("618 / YEARS LATER") and every
  other on one row (was: every span on one row). All five PISA events are drawn (the base dropped one, which is
  what gave its span the room); no spacing holds all four on one row (builder's ranking experiment: a
  byte-identical frame, `evidence/pisa_5_events_1920x1080.jpg`; reviewer's least-step experiment).
- `geo_truth.py` (ruling Q1): the checker extension above.

## Evidence

`evidence/`: before | after boards, half size, JPEG <= 300 KB, from real run data: every distinct locator place and
timeline of the dev runs (output/run_*), and the real run's route (50 boards). The 34 locator and route boards were rendered
again on round 2's code (0 checker faults in 34 renders; the 16 timeline boards are round 1's, and rulings Q1-Q3
do not touch timelines). Round 2 changes: Damascus 16:9 names ISRAEL; on Odessa 9:16 Israel, Kosovo and the Cyprus
buffer zone are drawn as ordinary ground (checked on a pixel-difference image; 0.61 % of pixels, 16:9 0.66 %); 17 other boards differ in under
0.5 % of their pixels. The boards were not rendered again on round 3's code: against round 2's sweep, this
commit's after.json has a different frame hash in 230 of 434 renders (218 locators, 12 routes; the locator's
neighbour pass, the 16:9 timeline score and the final Q2 filter came after round 2's sweep, and round 3 added the
ADMIN/GEOUNIT match and the border-vertex rule), with 0 checker faults. The read-only production sample was
also rendered before and after (36 renders: 0 checker faults in either); by the counts-only rule no render or name
from it is committed.

Port onto QF-26 (21b4c5c0): the 34 locator and route boards were rendered again on the ported commit (0 checker
faults in 34 renders), so their AFTER side shows QF-26's antialiased land, borders, neutral ground and dashes. Every
name they set equals round 2's renders except one: the Amber route 9:16 now names the BALTIC SEA at 21 px (round 2
and 6eb68d78 itself: "its own water holds no place for its name"). The route's land and leg masks are unchanged,
so this comes from the newer base's label measurement, not from geography; the checker passes it. The 16 timeline
boards are still round 1's and were not rendered again. The independent 33-place set (74 renders) on the port:
72 with 0 faults, Halley 9:16 and 16:9 as before (ANTARCTICA on an ice shelf the checker's data does not name);
the 7 places whose names differ from round 2's renders (Amalfi route, Campione, Eilat, Lindau, Peljesac, the Rhine
route, Trieste) name exactly what 6eb68d78 names, rendered for comparison.
