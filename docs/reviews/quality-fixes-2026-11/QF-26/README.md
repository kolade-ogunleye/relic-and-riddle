# QF-26: aliased geometry gets one antialiased path

Review 2026-11-05, section 3 (visual design): "land, borders and scale silhouettes are aliased ImageDraw next to
4x-supersampled markers". Acceptance (STATUS.md): an edge aliasing measure on land, borders and scale; the freeze
inventory widens the row to the strata ground, bars and rules. Contrast and colour stay unchanged except at the
antialiased edges (FREEZE-RESUME rulings: the WCAG 1.4.11 3:1 criterion for maps and routes is not fixed here, and
nothing here makes it worse).

## Step 1: qualification (skia-python 144.1 + pybind11 3.1.0)

The operator's delegated decision (QF-26.md, 2026-11-09) approved these two pins for qualification, adopted only
if Skia beats Pillow on edge error and area bias on every target family, with deterministic Windows/Linux output.

- **Install, offline and hashed.** Both wheels were fetched once (`pip download --no-deps`) and installed into an
  isolated venv with `uv pip install --offline --no-index --find-links`; their sha256 equal the ones in QF-26.md
  (Windows cp311 `e5198124...`, Linux manylinux_2_28 cp311 `db9f2dd3...`, pybind11 `b8488090...`). `uv lock`
  (uv 0.13.7) added exactly the two packages, with hashes for every platform wheel, and nothing else changed in the
  lock; `scripts/check_lock.py` passes. The shared venv was not touched (suites were running on it); the build and
  tests ran in an isolated venv synced from this lock (`uv sync --frozen --all-groups`).
- **Licence.** skia-python: BSD-3-Clause (its wheel carries the notice in `dist-info/licenses/LICENSE`); Skia
  itself BSD-3-Clause; pybind11 BSD-3-Clause. Nothing is redistributed (the wheels are installed from PyPI by the
  pinned lock; the outputs are images), so no notice file is added to the repository. The wheel bundles ICU data
  (`icudtl.dat`, Unicode licence); we draw no text with Skia.
- **Wheels.** cp311 wheels for Windows AMD64/ARM64, manylinux_2_28 x86_64/aarch64 and macOS. Linux CI gets the
  runtime libraries skia-python documents (`libfontconfig1 libgl1 libegl1`, `.github/workflows/tests.yml`); only CPU
  raster surfaces are used (no GPU context). Import cost about 0.15 s.
- **Accuracy (`qualify.py`, 32 x 32 exact reference).** Skia beats Pillow on edge RMSE and on area bias on all
  eight families (land with a hole, scale silhouette, fractional rectangle, thin sliver, small island, 3 px border
  ring, 1 px rule, 2 px diagonal): edge RMSE Pillow 0.071-0.326 -> Skia 0.0014-0.0223; area bias Pillow
  +5..+196 px (fills dilated half a pixel), -250 px (border ring) -> Skia within +-3.5 px. OpenCV's LINE_AA fill (the
  probe's alternative, no install) is worse than Pillow on every fill (RMSE 0.30-0.51, bias +9..+430 px). Skia's own
  path AA takes 4 sub-scanlines a row; `src/raster.py` draws each shape 4x tall and averages (16 sub-scanlines),
  which cuts its error 3-4x (`skia_vertical_ss_1` in qualify's output).
- **Determinism.** `qualify.py` run on Windows (win_amd64 wheel) and on Linux (WSL Ubuntu 24.04, CPython 3.11.18,
  manylinux wheel): all ten output digests (eight families plus an RGB blend and an RGBA outline composite) are
  byte-identical, and every metric equal. Repeat runs are identical.

`qualify.py <repo root>` output: `qualify_windows.json`, `qualify_linux.json`.

Result: adopted. No operator question was needed (the pins are the approved ones).

## What changed

- `src/raster.py` (new): `Draw(im, mode=None, grid="pixel")` takes ImageDraw's shape calls (polygon, rectangle,
  ellipse, line, plus `lines` and `fill_rings`) and draws them through Skia coverage, laying the shape's colour on
  the Pillow image by that coverage. A pixel a shape wholly covers gets exactly the colour ImageDraw wrote; only
  pixels a boundary crosses mix. Blending follows ImageDraw (mode "RGBA" on RGB blends the ink's alpha; otherwise
  replace, on RGBA by premultiplied interpolation, so no dark fringe). Two grids:
  - "pixel" (layout geometry): whole-pixel rectangles, ellipse boxes and axis-aligned lines of every width, in
    either direction (round 2: an even width's extra row follows the line's direction, as ImageDraw's), are
    pixel-identical to ImageDraw (tested on 64 cases), so crisp rules stay crisp; fractional geometry lies between
    pixels instead of being cut to the pixel below. As in ImageDraw, an outline in the fill's ink is not drawn.
  - "exact" (projected map geometry): the stated geometry, so a border is centred on the coast its land ends at.
  - Polygons are the ring exactly in both grids (ImageDraw dilated a fractional fill by about half a pixel).
  - `Rings`: rings filled as one shape (non-zero winding on rings oriented by role): neighbouring polygons that
    share an edge leave no seam (drawn one by one, two half-covered pixels of land would add to 3/4: a faint sea
    line along every shared edge, the antimeridian cut among them), holes are taken away, an enclave in its
    neighbour's hole is land in any order.
  - Large shapes are rasterised 512 rows a pass, and only the touched pixels are read and written.
- `src/graphics.py`, the call sites only (geometry unchanged):
  - `relief_base`: the land mask and the home fill are `Rings` filled on the exact grid; the borders are one stroke
    per style on the exact grid. The home outline's source is the home's coverage on the same grid taken at its
    half (round 2, below): a two-level mask of the exact coast, from which the GOLD_LIGHT inner edge and the INK
    line are cut by their unchanged morphology and blur. The globe inset (3x supersampled), that morphology and
    blur, and every hit-test mask (`view_masks`, `mark_mask`, `rings_fill`, the name checks) are unchanged: facts
    and placement stay on their own masks.
  - `_draw_scale_bar` (maps and routes): the casing, line and ticks; the bar grows through fractional pixels
    instead of whole-pixel steps.
  - The scale graphic, 9:16 and 16:9: the strata ground (`EARTH`), the shaft and its outline, the rulers' ticks,
    the ground lines, the landmark silhouettes, the monolith and the brackets.
  - Stat cards: the range dashes (at rest and while settling) and the kicker's rule.
  - `make_all`'s graphic cache key includes `src/raster.py`.
- Dependencies: `skia-python==144.1`, `pybind11==3.1.0` in pyproject.toml, requirements.txt and uv.lock; CI
  installs the Linux runtime libraries.

Not changed (already antialiased by supersampling, left to keep their frozen pixels): the dot, pin and ripple rings
(4x), route legs, arrows and leaders (4x), the cross-section's `_Pen` (2x), the globe (3x). Timeline spines and
callouts' rings are outside this row.

## Measurement

`python docs/reviews/quality-fixes-2026-11/QF-26/measure.py <repo root>` on the base a3946226 -> `before.json`, on
this commit -> `after.json` (FONT_SIZE=80, FriBiDi on PATH, the map data in tools/geo). Inputs: the dev runs'
topics (data/topics/topic_bank.json), the two scale facts the dev runs rendered (`inputs.json`, copied from those
runs' payloads) and two committed scripts' stat cards (data/script_library).

**Primitives** (the real geometry through each tree's drawing call; edge band = pixels within one of a partly
covered reference pixel):

| Family (real geometry) | Edge RMSE before -> after | Area bias px before -> after | Grey levels at the edge |
|---|---|---|---|
| Land, 5 dev-run places (720 px views, exact reference) | 0.205-0.307 -> 0.0041-0.0067 | +405..+1289 -> -3.1..+5.5 | 2 -> 243-256 |
| Borders, same rings, 3 px (ImageDraw 16x reference) | 0.200-0.263 -> 0.018-0.060 | -993..-97 -> +8..+320 | 2 -> 255-256 |
| Scale silhouettes (Statue of Liberty, Great Pyramid; exact reference) | 0.219-0.255 -> 0.0051-0.0057 | +698..+1206 -> -8.0..+1.3 | 2 -> 103-246 |

The border's remaining error and positive bias are mostly the reference's own (ImageDraw at 16x on thousands of
short coast segments); qualify.py's exact stroke reference gives Skia 0.0069 RMSE and +1.7 px on a 3 px ring.

**Frames** (last frames, real inputs; stair_corners = 2 x 2 three-to-one blocks of two flat tones >= 40 luma apart):

| Frame | Stair corners before -> after | Contrast minima (text drawn/graded; non-text) |
|---|---|---|
| scale, Aswan (height), 9:16 | 3069 -> 2579 | text 9.08/8.90 unchanged; ground line 16.68 unchanged; subject 10.37 -> 9.96 |
| scale, Aswan, 16:9 | 3548 -> 2641 | text unchanged; ground line 16.56 -> 16.62; subject 10.55 -> 10.37 |
| scale, Wieliczka (depth), 9:16 | 2986 -> 2647 | text unchanged; ground line 14.14 unchanged; shaft 1.66 -> 1.62 |
| scale, Wieliczka, 16:9 | 3269 -> 2711 | text unchanged; ground line and shaft unchanged |
| map, Istanbul, 9:16 | 893 -> 805 | unchanged (marker 5.70, globe 9.46) |
| map, Malta, 9:16 | 666 -> 671 | unchanged (marker 1.24, globe 9.57) |
| map, Ellora, 16:9 | 1181 -> 1232 | unchanged |
| map, Flannan Isles (UK), 9:16 | 824 -> 792 | unchanged (marker 1.99, globe 9.56) |
| map, Amber Room (Russia), 16:9 | 1196 -> 1214 | unchanged |
| map, Odessa (Ukraine), 9:16 | 1754 -> 1436 | unchanged; globe 3.27 -> 3.28 |
| map, Camp Century (polar view), 9:16 | 2251 -> 2024 | text and marker unchanged; globe 8.44 -> 7.76 |
| stat cards, Aswan and Memnon, 9:16 | 1643 -> 1641, 1436 -> 1436 | unchanged |

In every frame the most common flat colours (the drawn tokens) are the same colours before and after. On two
frames, two near-equal shares swap places (Wieliczka 16:9: 1.13 % / 1.11 % -> 1.06 % / 1.07 %), or the twelfth
entry (0.08-0.09 % of the frame) is another sea tone (Malta).

Reading the frame figures honestly:
- The stair-corner count also counts whole-pixel rectangles (four corners each, kept crisp on purpose), text and
  the photo-free grounds, so it falls by 11-26 % on the scale graphic and not to zero.
- The map's frame is a bicubic resample of a base drawn larger than the frame, which already softened the base's
  steps, so the base itself is the measure there (the primitives table). The map counts move both ways (-18 % to
  +4 %). On Ellora, 191 corners go and 242 appear, nearly all between the INK line's dark tones and the flat sea.
  The INK line is still cut from a two-level mask (round 2), so its remaining steps move with the coast's
  half-pixel shift. They are the base's steps; there are no more of them.
- Camp Century's globe minimum goes from 8.44 to 7.76 (polar view). The globe itself is untouched; its record
  measures it against the antialiased land around it. It stays 2.6 times the 3:1 target.
- Non-text minima are measured on the base's ImageDraw hit-test masks (unchanged). Where a mark's edge is now
  partly covered, those mask pixels hold the mixed edge tone, so the measured minimum moves by up to 0.41 (subject)
  and 0.04 (shaft; it was already under 3:1 on the base, a QF-88 / maps-routes freeze item, not fixed here). The
  marks' own colours are unchanged (the flat-colour lists). Text contrast is unchanged everywhere.

## Round 2: the home coast's gold edge (review 1, blocking 1)

Round 1 cut the home edge (the GOLD_LIGHT band inside the coast and the INK line on it) from the antialiased
coverage. Its morphology (erode/dilate by whole pixels) and its Gaussian blur (0.45 x base px) were made for a
two-level mask, so the coast was softened twice. On the review's frames the rim peak fell 5-28 %, and at native 4x
Flannan's Lewis and Harris lost their gold.

Fix: the home's coverage is filled on the exact grid as before, then taken at its half (`v >= 128`: the pixels
whose centre the home covers). The edge's morphology and blur get the two-level mask they were tuned on. It is cut
from the same exact coast as the antialiased fill, land and borders. A hole in the home
(a lake, an enclave) whose coverage reaches half of no pixel would vanish from that mask. Such a hole keeps the
pixels it touches, as ImageDraw drew it (`_closed_at_half`; the regression test
`test_a_loch_smaller_than_a_pixel_keeps_its_ink_and_gold`). None of the seven maps has one; the rule keeps a
hole from being lost. The narrow sea lochs show the other way round. In the base, ImageDraw's half-pixel
growth of the home closed a sea loch narrower than a pixel everywhere but at its widest points, so Lewis and
Harris showed chains of INK dots. The exact coast keeps each loch as one channel (Flannan detail board).
Two alternatives were measured and not kept:
- The base's ImageDraw mask as the edge's source (the base's ring and line, byte for byte). ImageDraw's mask lies
  about half a pixel outside the exact coast. The neighbours' 3 px borders, now centred on the exact coast, then
  reach past the INK line into the gold, and on every land border a grey band showed between the INK line and the
  gold (Ukraine-Belarus at native 4x).
- That mask, with the land and home fills grown 0.35 px (an earlier round-2 draft) to give back ImageDraw's land
  weight. It brings the area bias back (+0.35 px all round), which QF-26.md's adoption condition rules out. It
  also leaves the grey band.

Measured with `measure.py` on base a3946226 and on this commit (`before.json`, `after.json`), on seven maps: the
review's five, Istanbul, and Camp Century (polar view). Two measures:
- home_rim: the review's frame metric. The peak is the median of the 5 x 5 luma maximum over the gold within
  2 px of dark.
- home_edge: the gold edge alone, on the relief base as the viewer sees it, as its visible share of GOLD_LIGHT
  after the INK line and borders are drawn over it. It is profiled every 0.1 px along the normal at every third
  coast pixel and gives the median peak, the median full width at half maximum, the mean width, and the visible
  gold in pixels.

| Map | Frame rim peak | Edge peak (median) | FWHM median px | FWHM mean px | Visible gold |
|---|---|---|---|---|---|
| Flannan Isles (UK), 9:16 | 178.2 -> 178.6 (+0.2 %) | 0.512 -> 0.511 (-0.2 %) | 1.4 -> 1.4 | 1.541 -> 1.520 (-1.4 %) | +2.8 % |
| Hal Saflieni (Malta), 9:16 | 184.7 -> 185.0 (+0.2 %) | 0.505 -> 0.507 (+0.2 %) | 1.2 -> 1.2 | 1.344 -> 1.320 (-1.8 %) | -0.4 % |
| Amber Room (Russia), 16:9 | 183.3 -> 185.3 (+1.1 %) | 0.681 -> 0.669 (-1.8 %) | 1.9 -> 1.9 | 1.850 -> 1.932 (+4.4 %) | +4.7 % |
| Odessa (Ukraine), 9:16 | 192.6 -> 193.0 (+0.2 %) | 0.734 -> 0.723 (-1.5 %) | 1.5 -> 1.7 | 1.627 -> 1.797 (+10.4 %) | +13.8 % |
| Kailasa (India), 16:9 | 189.2 -> 188.8 (-0.2 %) | 0.733 -> 0.731 (-0.2 %) | 2.7 -> 2.7 | 2.869 -> 2.846 (-0.8 %) | -0.7 % |
| Istanbul (Turkey), 9:16 | 176.9 -> 176.6 (-0.2 %) | 0.508 -> 0.502 (-1.1 %) | 1.2 -> 1.2 | 1.384 -> 1.368 (-1.1 %) | +7.6 % |
| Camp Century (polar), 9:16 | 200.1 -> 200.0 (-0.0 %) | 0.815 -> 0.816 (+0.1 %) | 2.9 -> 2.9 | 3.444 -> 3.452 (+0.2 %) | -2.2 % |

On the same frame metric, round 1 read -28 % (Flannan) to -5 % (Kailasa). Now every peak is within +-1.8 % and
every median width equals the base's, except Odessa's, which is wider. No edge is weaker or narrower by more than
2 %. Two figures go past +2 %, both towards more gold and both on the maps with the longest land borders: Odessa's
widths (Ukraine) and Amber Room's mean width (Russia). In the base, the aliased neighbour border was drawn after
the gold edge, half a pixel off the home's ImageDraw mask, so it painted over the edge's outer pixel. Now the
border and the INK line share the exact coast, the line covers the border, and the edge shows its designed 1.5
base px. A profile across the Ukraine-Belarus border in base pixels reads INK 42, 42, then gold 145, 189, 150
before; after, INK 42, 30, 42, then gold 132, 196, 188, 150.

The edge's own layer, before anything is drawn over it (from the two masks), has the same peak, and its gold per
coast pixel is within 1 % (Istanbul +0.9 %, Odessa +0.7 %; measured in a scratch variant, not committed). The frame width proxy (`home_rim.width_px`) reads
Odessa +4.6 % and Istanbul +2.9 %, and the others within +-1.4 %. That metric also counts the neighbours' borders
and the INK line's antialiased edge as gold and dark. With the base's own mask as the source (the base's edge,
byte for byte; the same scratch variant), it still read Odessa +4.7 %, so that figure does not come from the edge.

Regression test: `test_the_home_coasts_gold_edge_keeps_its_strength`, on a synthetic home blob at base scales
1.0, 1.6 and 2.4. The mean GOLD_LIGHT share of the band's peak across the coast must be at least 98 % of the
base's (0.467 / 0.593 / 0.729). Round 1 drew 0.366 / 0.530 / 0.710 and fails; this commit draws the base's values.

## Evidence

`python docs/reviews/quality-fixes-2026-11/QF-26/evidence.py <base root> <commit root> renders` -> `evidence.json`
and `renders/`: per design a half-size before/after board (`<design>_<key>_<fmt>.jpg`) and a detail board
(`*_detail.jpg`: two 96 px squares, native pixels at 4x, before over after, because the antialiased edges are a
pixel wide and do not show at half size. On a map the first square is where the home coast's gold edge changed
most, the worst case of that edge, and the second is where the frames differ most. Elsewhere both are where the
frames differ most). Round 2 adds Flannan, Odessa and Camp Century (polar view).

| Design | Changed pixels (share of frame) | Of them within 1 px of a drawn edge | Largest change farther from an edge |
|---|---|---|---|
| map, Malta, 9:16 | 8,854 (0.43 %) | 78.8 % | 7 levels |
| map, Ellora, 16:9 | 71,075 (3.43 %) | 94.5 % | 4 levels |
| map, Flannan Isles, 9:16 | 147,957 (7.14 %) | 77.3 % | 11 levels |
| map, Odessa, 9:16 | 135,814 (6.55 %) | 96.0 % | 22 levels |
| map, Camp Century (polar), 9:16 | 190,146 (9.17 %) | 92.5 % | 8 levels |
| scale, Wieliczka, 9:16 | 4,934 (0.24 %) | 100 % | 0 |
| scale, Aswan, 16:9 | 10,026 (0.48 %) | 99.99 % | 4 levels |
| stat cards, Aswan, 9:16 | 40 (0.00 %) | 100 % | 0 |

On the maps the frame is resampled from the base, so a base edge's change spreads two to three frame pixels and
some of it lands beyond the 1 px band, in the resampled coast and border edges and not in a fill: the flat-colour
lists are unchanged. Odessa's 22 levels are the Tendra and Kinburn spits (detail board, left crop). They are
narrower than a base pixel: ImageDraw drew them a pixel wide in gold, and they now take their true coverage,
a faint gold hairline (see Known limits). The stat cards' bars sit on whole pixels at rest, so the held frame
barely changes; the AA shows while a range's dash settles.

## Known limits and effects

- Home features narrower than a base pixel (spits, islets such as the Tendra and Kinburn spits off Odessa) are drawn
  at their true coverage. ImageDraw drew them at least a pixel wide, so they read fainter than on the base. This is
  the area-true rendering the qualification adopted (the thin-sliver family). Whether such home features need a
  minimum legible width is a design question for the maps freeze panel, not decided here.

- Hairlines at fractional positions (the silhouettes' 1 px detail lines, the pyramid's courses) now spread over the
  two rows they straddle: the same ink, a lower peak contrast per row than ImageDraw's whole-pixel line. Their
  total ink is preserved, and they no longer jump a pixel as the drawing moves.
- Fills lose ImageDraw's half-pixel dilation, so a filled shape is the size its geometry says (land area bias
  +405..+1289 px on a 720 px view before, within +-6 px after). Whole-pixel boxes on the pixel grid are unchanged.
- Map base cost: a 3240 x 5760 relief base 16.6 s -> 21.1 s on the workstation (+4.5 s; the review measured
  +6.8 s min to min under load). One base per map render, which takes minutes; a pixel-identical shortcut gave no
  measurable gain, and a coarser coverage (VERTICAL_SS 2) would trade edge quality for it, so it is kept.
- The border reference in measure.py is ImageDraw at 16x (an exact stroke reference is too slow at map size); its
  own half-pixel-at-16x rounding is part of the remaining border error.
- QF-20 (maps on Shapely and pyproj) port, measured by the review (cherry-pick onto QF-20's bb332766): it is not
  mechanical. `uv.lock` conflicts (shapely against skia-python): regenerate it with `uv lock`, never hand-merge.
  Three hunks of `relief_base` conflict and must be ported by hand: (a) QF-20's neutral-territory mask
  (`neutral_m`, `dn = ImageDraw.Draw(...)`) must become a `raster.Rings` filled the same way, or the neutral overlay
  stays aliased against the antialiased land; (b) QF-20 removed the home's least-extent oval branch: keep QF-20's
  version (no `Rings.oval` at that site); (c) the holes loop must add QF-20's batched rings (`drawn_poly[1 + hi_]`,
  skipping `geo.map_geometry.resolvable_hole` failures) with `dl.add(hp, hole=True)`, not re-project `hole`.
  `_close_seams` and `_draw_scale_bar` merge cleanly. Whichever of QF-20 and QF-26 lands second ports by hand and
  re-measures the land and border primitives and the home rim (measure.py).
- Production install (review 1, item 5): the signed-tag deployer sees requirements.txt change and runs
  `pip install -r requirements.txt` inside the hold window, online from PyPI and not hash-checked (requirements.txt
  carries no hashes; the sha256 pins are enforced in CI through `uv export --locked` and `--require-hashes`), then
  runs the suite and rolls back on a failure. The win_amd64 module imports MSVCP140, VCRUNTIME140(_1) and
  OPENGL32, as onnxruntime and OpenCV already need the VC++ runtime. A rollback leaves the two packages installed
  (harmless). The release note should say that the release adds two packages, and the two wheels
  (`skia_python-144.1-cp311-cp311-win_amd64.whl`, `pybind11-3.1.0-py3-none-any.whl`, sha256 as in QF-26.md) should be
  staged for the elevated fallback (`Update-ContentReleaseTree.ps1`, offline wheels first) in case the deployer's
  PyPI install is refused.
- Hairlines (review 1, item 4): judged not worse overall by the review; if the freeze panel wants crisper static
  courses, hint axis-aligned strokes of width <= 2 to whole pixels on the held frame only (not done here).
- Non-text contrast is measured on the base's ImageDraw mark masks; their edge pixels now hold mixed tones. On the
  mark's core (the mask eroded 1 px) the review measured the shaft 1.67 -> 1.66 and the subject 10.37 -> 10.03 (the
  monolith's coursing lines touch more rows). No mark crosses 3:1 either way. Measuring marks on coverage masks is
  the better rule for the freeze loop.
