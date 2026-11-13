# QF-24: 9:16 figures use the full frame (shared organic Reel safe rectangle)

Review finding (project-quality-review-2026-11-05, section 3 "Visual design"): "the lower half of every 9:16 graphic
is empty because the 35% bottom inset is Meta's *ads* guidance, not organic UI". Binding decision:
decisions/QF-24-QF-87-layout.md (approved 2026-11-09): the shared organic Reel rectangle (113, 288)-(848, 1370) at
1080 x 1920 (top 288, bottom 550, left 113, right 232), 240 px of it reserved for captions (the 20 px bottom
clearance included), the existing 40 px gap above that band, graphic text ending at y 1090; the captions keep the
reviewed 640 px ink measure, centred in the safe rectangle (x 480). The caption typography itself (true small caps,
the bounded two-line composition, NFC) is QF-87 and not in this row. Extended by the operator's ruling of
2026-11-13 (same decision file): keep the approved rectangle and lay every surface out on it, judged on real renders
(build 1b: callouts, end card, then-and-now, the myth, hook and chapter cards, timelines and route; "Surfaces").

## What changed

- `src/safe_zones.py`. The Reel profiles are the organic viewers', not the ads guides': YouTube Shorts from its
  organic "Use visual guides" diagram (65, 288)-(848, 1382); Instagram and Facebook Reels as guarded stress bounds
  from one operator-supplied viewer screenshot each (`platform-overlays.json`: the hand-read control rectangles, the
  four fit/cover transforms, 12 screenshot px of annotation uncertainty and 16 source px of chosen clearance; no
  pixels, account names or image hashes). Per platform (top, bottom, left, right): Instagram (245, 418, 113, 228),
  Facebook (244, 550, 113, 228), Shorts (288, 538, 65, 232); their union is the approved rectangle. Stories keep
  Meta's guide (unchanged: the Reel screenshots say nothing about the Story viewer). `CAPTION_BAND` 150 -> 240.
- `src/config.py`. `CAPTION_MARGIN_V` follows (672 -> 550); `CAPTION_REEL_TEXT_WIDTH = 640` names the reviewed
  measure.
- `src/video_composer.py`. Centred on the frame the organic box leaves 616 px (its right rail is wider than its
  left), which would narrow every caption to 560 px and cut new bursts; `MAX_CAPTION_WIDTH` stays 640 and the 9:16
  plate is centred in the box (`reel_caption_centre_x`: x 480; `reel_caption_span`: 734 px). The two-line guard read
  a fixed 1058 (the ads graphics floor): it now tests the plate against the reserved band
  (`safe_zones.caption_band_top`, 1130), which stands the 40 px gap under the graphics (`CAPTION_GUARD_GRAPHICS_BOTTOM`
  = `graphics_box()[3]`, 1090). The reconstruction badge stood at x 84, outside the organic box: it keeps its 19 px
  inside the box's left edge and 12 px under its top (x 132, y 300).
- `src/graphics.py`. The shared inset edge reads the box (x 100-860 -> 120-820: 7 px inside the organic left edge,
  which already holds 28 px of clearance and uncertainty; 28 px inside its right edge, as before). The design grid of
  the rebuilt vertical designs kept the design box fitted into the text box, so the narrower box would have set every
  cutaway word 11% smaller: it keeps the frame's scale, puts the design's inset edge on the shared one and gives a
  layout its right edge and foot (`_Grid.ix/iy`); the 9:16 cutaway ends its label column on the box's right edge,
  runs its depth axis and labels down into the 32 px the box adds, and moves its label column to hold each level's
  feet and metres on one line (never clipping a unit or shrinking type). Three defects the narrower box exposed:
  the number pair was fitted on its two cards' own figure sizes and then set to the smaller one, so a pair could
  step down a whole scale (kickers and labels too) where the drawn pair fitted: it is now measured at the size it
  is drawn; the height scale's landmark note ("GROUND TO TORCH TIP") was wrapped on its untracked advance and held
  to two rows, so on the organic box it was dropped whole (inside the box or not at all): it is wrapped on its
  tracked ink, three rows at most, at the same size; the number cards' text width read the 1080-wide constant.
- `src/design_cards.py` (build 1b, (d)). The 9:16 cards' literals for the ads box (x 100-888, foot 1058) become the
  one safe-zone statement: `TALL_LEFT`/`TALL_RIGHT`/`TALL_FOOT` = `graphics_box()` (x 113, 848, y 1090), and every
  myth, hook, chapter and quote measure, x and foot reads them (`MYTH_PANEL` too). Words start on the box's left edge,
  not on the graphics' inset edge (x 120): the box already holds the platforms' clearance, and at x 120 the reviewed
  long myth LONG_ET no longer fitted its floors (76 / 58 px). The hook's centred (plate) position centres on the
  box's axis (`HOOK_CENTRE_X`, x 480; `cover_design._brand` takes the axis, 540 by default, so covers are
  unchanged): centred on the frame the drawn masthead ran to x 857, past the box. A chapter title whose few W2 steps
  find only stop-word ends steps on down the ladder to its floor for the first clean break (operator decision
  2026-11-05: every chapter card is drawn). The 9:16 quote's measure runs to the box's right edge (735), as the
  myth's and the hook's do.
- `src/callouts.py` (build 1b, (a)). The 9:16 label box is the Reel box edge to edge (x 113-848, as the graphics'
  words run; the ads box's 18 px inset, x 83-870, is not taken again). A 9:16 set the reviewed grid holds no valid
  layout for is tried again before it is refused: at its full size on a finer leader-length grid (4 px), then with
  its labels stepped down together, 2 px at a time, to the existing floor (four fifths of LABEL_PX); one size for
  every label of a set. Declutter tries every set and pair at full size first, so a smaller size never replaces a
  full-size graphic. A set no size holds keeps its conflicts (declutter drops or refuses it; render never draws
  one), and the conflict set it reports is the largest attempt whose plates still stand LABEL_GAP (24 px) apart
  where one exists.
- `src/end_card.py` (build 1b, (b)). The 9:16 group (masthead, rule, share line, follow line) and the loop bridge's
  FOLLOW centre in the box (`reel_column`: x 480, its 735 px), as the captions do.
- `src/then_now.py` (build 1b, (c); round 2). Both year markers are placed against `graphics_box()` (y 288-1090),
  each in its panel's part of it. The split keeps the reviewed y 768 (`SPLIT_TALL`, 0.4 of the height: the middle of
  the ads Reel box it was derived from); the NOW block ends at 1038, above the graphics foot.
- `src/graphics.py` route names (build 1b, (e)). On 9:16 a stop name's places one size step (2 px) smaller join its
  full-size places, ranked after all of them: on the organic box rr8's customs-house name, which just missed its
  side at full size, crossed to the tomb's side and left the tomb (a far stop) unnamed.
- `tests/test_media_and_llm.py`: the approved main-test patch (SHA-256 b95f11a7..., applied byte for byte with LF).

## Measurements (`python measure.py <repo root>`; before.json = the base 54dcbe94, measurements.json = this commit)

Inputs, committed: the 34 dev runs' checked graphic copy (QF-23/real_inputs.json: 23 number pairs, 10 timelines,
3 scale facts, 1 route) and the seven 9:16 caption narrations of the freeze-panel fixtures. Fixed yardstick: the
approved rectangle, its graphics box (y 1090), its band (y 1130-1370) and each platform's approved free area.

| Metric | before | after |
|---|---|---|
| Graphics drawn (of 37; on both trees the route draws nothing offline, and render_scale returns None for the 60 ft Colossi of Memnon) | 35 | 35 |
| Text plates outside the organic graphics box | 130 of 256 (29 of 35 graphics) | 0 of 282 |
| Text plates under a platform's UI: Instagram / Facebook / Shorts | 128 / 128 / 17 | 0 / 0 / 0 |
| Words drawn, per graphic | | identical in 35 of 35 |
| Median text foot (y): numbers / timelines / scales | 972 / 1058 / 1058 | 1035 / 1090 / 1090 |
| Median ink foot of the ink-ground graphics (timelines, scales) | 1057 | 1089 |
| Median ink centroid (y), ink-ground graphics | 660.6 | 670.5 |
| Smallest text, all graphics | 24 px | 24 px |
| Number cards: smallest text (min_px) up / same / down | | 5 / 17 / 1 (the one: a 36 px kicker at 32) |
| Number cards: display figure (max_px) up / same / down | | 2 / 10 / 11 (worst 350 -> 320, 207 -> 184) |
| Timelines: year size up / same / down | | 0 / 8 / 2 (123 -> 102, 113 -> 102) |
| Caption plates outside the organic Reel box | 116 of 273 | 0 of 273 |
| Caption plates outside the 240 px band | 273 of 273 (they sat at y 1107-1228) | 0 (y 1229-1350) |
| Caption bursts, their times, plate widths and heights, words | | identical in 7 of 7 narrations |
| Caption plate centre (widest) | x 540 | x 480 |
| Two-line rungs the band holds / two-line bursts drawn | none / 0 | 80, 74, 66 / 0 |

What the numbers say, plainly: the organic box is 32 px taller for graphics (1058 -> 1090) and 88 px narrower
(823 -> 735 px). Every word stays, nothing is under any platform's UI any more, the text now reaches the box's foot,
and captions keep every cut, size and time. The cost is horizontal: a figure that was bound by the width draws up
to 9-13% smaller (11 of 23 number pairs; two of ten timelines, whose labels wrap to two lines and whose years then
step down to keep four entries whole). The measured hierarchy fix gives five number pairs larger labels and kickers
(28 -> 36, 41 -> 46, 36 -> 40 px). The ink centroid moves 10 px down; the designs are still top-anchored (their
space ends at 1090, the band takes 1130-1370), which is the decision's trade-off, not a new filler.

## Surfaces (build 1b; operator 2026-11-13: keep the approved rectangle, fix the surfaces)

Build 1 moved every consumer of the Reel box onto the 88 px narrower organic box and found 122 failures on surfaces
the 2026-11-09 decision had not assessed. Build 1b lays each of them out on the approved rectangle (`measure.py`
"surfaces", committed inputs; before = 54dcbe94 on the ads box, after = this commit):

| Surface | before (ads box) | after (organic box) |
|---|---|---|
| (a) callouts, the operator's reference door/wall pair (9:16) | both labels at 42 px; the wall's plate at x 104, outside the organic box | both labels at 38 px (one size for the set, 4 px above the 34 px floor); both plates inside the box; 77 px apart |
| (b) end card, the catalogue's five 9:16 share lines | masthead 70 px, 696 px wide on x 540 (192-888); share 52 px, 4 lines | masthead 73 px, 726 px on x 480 (117-843); share 52-54 px, 3 or 4 lines; every element inside the box |
| (c) then-and-now, 9:16 | split y 768, markers at x 83 (outside the box), NOW year foot 1038 | split y 768 (kept), markers at x 131, NOW year foot 1038 (above the 1090 foot) |
| (d) myth LONG_ET (reviewed long myth) | fact 80 / myth 60 px, ink x 96-856 | fact 76 / myth 58 px (the floors), ink x 113-843, foot 1065 |
| (d) myth LONGEST916 (stress fixture) | drawn, ink to x 885 | refused: every size that holds it ends a line on a stop word (declared; listed) |
| (d) myth, the Richat sheet's fact-9 correction | fact 76, 4 lines, ink x 96-853 | fact 76, 5 lines, ink x 113-668 |
| (d) hook, the tests' h1 claim on its bright ground (plate position) | claim 136 px, ink x 204-877 (masthead to 857) | claim 144 px on x 480, ink x 124-836 |
| (d) chapter cards, the long Richat run's five act titles | 100 / 128 / 120 / 100 / 128 px, ink to x 878, foot 1057 | 92 / 136 / 128 / 92 / 128 px, ink x 113-844, foot 1089 |
| (e) timeline five_close (stress fixture) | drawn | refused (declared; listed) |

Every element of every 9:16 surface above is inside the organic box after (and inside its graphics box, y 1090,
except the end card, which follows the narration and carries no captions: its group still centres in the whole box,
foot 1102 at most, as it did on the ads box). The 16:9 plans of the same cards are identical on both trees
(`render_surfaces.py`: "cards_16x9_identical"), and every 16:9 graphic frame and caption file hashes the same
(measure.py "wide").

Why each surface is laid out as it is:

- (a) Callouts. At 42 px the reference wall has no valid candidate at all on the organic box, even alone and on a 2 px
  grid of slides and leader lengths (probed: none of 4,430 candidates holds the single-label hard limits inside x
  113-848; the binding one is the plate's clearance from the door); its plate stood at x 104 on the ads box. Finer
  candidate positions alone do not hold it; the set stepped down together does, at 38 px. A set the reviewed grid
  holds at full size is laid out exactly as before (the stepped search runs only where it holds none). A set no size
  holds is still dropped or refused (QF-23 semantics; a conflict set is never drawn), and the conflict set it reports
  is, where any attempt has one, a set whose plates stand 24 px apart.
- (b) End card, chosen on the boards (`renders/endcard_*_base_stepdown_centred.jpg`, three columns: base, "masthead
  one cover-scale step down at the 616 px centred width", "the group centred in the box"). The step-down option
  sets the masthead at 64 px (636 px) and two of the five share lines a size smaller (52 -> 46 px), and keeps the
  frame's axis while the box's centre is 60 px left of it. Centred in the box the masthead keeps (and slightly
  grows to) its cover scale, 73 px, every share line keeps 52-54 px, and the card shares the captions' axis.
- (c) Then-and-now. On the Reel box the split would have moved 768 -> 829 and the NOW kicker under the graphics
  foot. Round 1 put it at the graphics box's middle (689); on the two real pairs that set the NOW kicker on the
  Roman frieze's inscription and (by the side rule) the THEN marker on a colossus's head, and cut 79 px of the old
  photo (QF-24 review 1). Round 2 keeps the split at 768 and only moves the markers onto the graphics box (x 83 ->
  131): the NOW block still ends at 1038, inside the box, and both real pairs keep the base's side (left) and the
  old photo's full 768 px.
- (d) Cards. LONG_ET fits only from x 113 (at x 120, the graphics' inset edge, it was refused at the floors).
  LONGEST916 is width-bound: it would need a 768 px measure; at 735 every break the floors hold ends on THE, so it is
  refused, never drawn with a stop-word end. The short myth sets one step smaller (fact 112 -> 104, myth 96 -> 90).
- (e) Timelines. five_close repeats one 58-character label at 1900, 1905, 1915 and 2020 (1910 is dropped for density, as
  before): its best two-row break needs 697 px at the 30 px floor and the column holds 646 (706 before); on three rows
  no layout holds the four drawn entries in the column at any year ratio. It is refused (QF-23: never drawn cut). The
  route's rr8 names its far stop again (the tomb); its near lighthouse, 172 px from its nearest stop, is left unnamed,
  as the route rules allow (`renders/route_rr8_fixture_before_after.jpg`).

Timeline refusal census (QF-23's `timeline_refusals.py`, every committed set at 9:16 and 16:9; `census_before.json`
/ `census_after.json`): 185 sets, 370 renders; refused 63 -> 66, cut 0 -> 0, whole 307 -> 304, all changes at 9:16.
Newly refused: cr/cr14, fz/fz11, lw/lw0, nz/nz9, nz/nz26, nz/nz32, nz/nz37, qz/qz26, se/se1, tl/five_close, xv/xv7,
xv/xv17, xv/xv18 (the 40 px narrower label column); now drawn whole: cr/cr9, fz/fz2, fz/fz17, fz/fz26, fz/fz37,
lw/lw11, nz/nz6, nz/nz10, qz/qz0, xv/xv11 (the 32 px taller column). Every one is a fuzz or stress set; the base's
list equals tests/test_r4_graphics_2026_11_02.py's QF23_REFUSED exactly, and the after list is its new value. The
ten real dev timelines all draw (measure.py).

Hero-photo stress (`stress_photos.py <tree> <dev checkout>`, every dev run's first hero still as the ground, 23
grounds; `stress_photos_before.json` / `stress_photos_after.json`): worst line contrast numbers 3.24 / 3.24 / 3.23
-> 3.27 / 3.24 / 3.24, timeline 4.41 -> 4.41; grounds with a line under 4.5:1 9 / 9 / 10 -> 7 / 10 / 10, timeline
1 -> 1. The line under target is the dimmed first figure on the held frame (checked on the worst ground: "36" at
3.24 on both trees), a pre-existing design issue named in the review ("the stat card never restores its dimmed
first figure"), not this row's; no other line falls under target on any ground.

Production sample: not used (nothing derived from it is committed).

## Renders (`render_evidence.py <base> <this tree> <dev checkout> <out>`; half-size JPEGs, base left, this right)

Real runs, each graphic drawn from its verified_script.json on its own hero photo, its last frame captioned with
its own words.json through libass. The thin lines are an overlay, not output: grey the ads box and its 1058
floor, gold the organic box, its band top (1130) and graphics floor (1090).

- `renders/run_trepanation_miracle_1792995462_numbers_before_after.jpg`: the pair at its measured scale (labels
  28 -> 36 px), the "7,000-10,000 YRS AGO" figure inside the box (it ran to x 871 before).
- `renders/run_richat_structure_sahara_eye_1792778206_numbers_before_after.jpg`: the largest gain (130 -> 200 px).
- `renders/run_amber_room_heist_1792790550_numbers_before_after.jpg`: a cost case (207 -> 184 px, the narrower
  measure wraps "AMBER IN COMPLETED ROOM" at the full scale).
- `renders/run_amber_room_heist_1792781224_timeline_before_after.jpg`: the worst timeline (labels on two lines,
  years 123 -> 102 px); "DOCUMENTATION ENDS AT KÖNIGSBERG" ran past x 848 before.
- `renders/run_unfinished_obelisk_aswan_1792993374_scale_before_after.jpg`: "GROUND TO TORCH TIP" kept (three rows),
  the note inside the box (it ran to x 862 before).
- `renders/run_wieliczka_salt_mine_1792974672_scale_before_after.jpg`: the depth poster down to 1090.

Surface boards (build 1b; `render_surfaces.py <base> <this tree> <dev checkout> <out>`, inputs from the freeze-panel
catalogue, read-only; the same overlay):

- (b) `renders/endcard_<k>_<run>_base_stepdown_centred.jpg` (the catalogue's five 9:16 end cards, each run's own
  share line on its own last scene): base, the step-down option, the chosen centred group. The decision is made
  on these: the centred group keeps the masthead at cover scale and every share size.
- (b) `renders/bridge_<k>_<run>_before_after.jpg`: the loop bridge's FOLLOW at its fully-up frame (what a Reel
  ships), centred on x 480. Labelled under the board: the caption plate the two Nazca boards show in both columns is
  burned into the run's stored master (the frame 0 the bridge dissolves into, from its original render, centred on
  x 540); it is not this tree's caption, which centres on x 480.
- (c) `renders/thennow_<k>_<run>_before_after.jpg` (the catalogue's two accepted pairs, each run's own photos and
  years, the split hold): split 768 on both, markers x 83 -> 131, both inside the box, checked by eye at full size
  (no marker on inscription text or a figure's head).
- (a) `renders/callouts_fixture_before_after.jpg`: the reference pair's layout geometry (plates, leaders, dots, the
  objects' boxes) on an ink ground, labelled: no run has drawn a callout.
- (d) `renders/card_<card>_<ground>_fixture_before_after.jpg`: the myth (LONG_ET; the Richat fact-9 correction), the
  chapter (Richat acts 1 and 4) and the hook cards at rest on the Richat still and the Amber Room hero (the busy,
  colourful worst case); the copy is the tests' committed fields (labelled "fixture"). Both heroes keep the hook
  in its normal (left-set) position; `renders/card_hook_obelisk_plate_fixture_before_after.jpg` (round 2) shows its
  plate position on the catalogue's bright unfinished-obelisk hero: base centred on x 540, ink x 204-877 (past the
  box's 848); this tree centred on x 480, ink x 124-836, claim 144 px.
- (e) `renders/route_rr8_fixture_before_after.jpg`: the reviewer's rr8 route (a test fixture), its last frame drawn
  on each tree with tests/test_r4_graphics_2026_11_02.py's own harness (REVIEW9_ROUTES["rr8"], route_geo,
  _render_fast; the route needs the gitignored tools/geo layers): the tomb is named again.

## Limits

- No run has ever drawn a callout and no fact sheet carries a checked card field (the freeze-panel catalogue's
  verdicts), so (a) is shown as layout geometry on the committed reference coordinates (on an ink ground) and (d)
  on the tests' committed fields (signed with the tests' throwaway key) on real hero photos; both are labelled.
- Declared refusals (QF-23 semantics, never drawn cut or with a stop-word end), all stress fixtures: the myth
  LONGEST916 on 9:16 and the census's 13 newly refused 9:16 timeline sets, five_close among them (10 others now
  draw whole). LONGEST916 and five_close are accepted 9:16 refusals on the programme decision's allowed-refusal list
  (QF-24/QF-87 layout, 2026-11-13, after QF-24 review 1). No real dev run's graphic is refused (measure.py: 35 of 35 drawn on both trees).
- The callouts' reference pair now draws at 38 px (was 42 on the ads box; inside the existing four-fifths floor,
  34 px, recorded for the operator after QF-24 review 1); the hook's plate position and the end card now
  centre on the box's axis (x 480), 60 px left of the frame's centre, as the captions do. These are layout
  choices the design-freeze loop reviews on device.
- No dev run carries a cross-section; the cutaway is checked on the reviewed fixtures (tests) only.
- The Meta bounds are engineering stress bounds from one device and view per platform (decision text), not a
  published specification; expanded descriptions, keyboards and modal sheets are covered by no static profile.
- Full QF-24 optical hierarchy per design and the independent visual review remain (decision conditions).
