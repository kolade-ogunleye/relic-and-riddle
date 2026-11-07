# Then-and-now, step 1: ink type on paper (freeze panel 2026-11-04)

Defect (handover, "Then-and-now: stepped label shadow on near-white photos"): on a near-white paper ground the
THEN / NOW marker was bone type inside a heavy dark halo, whose outline stepped out where the short kicker sits
over the wide year (HEAD, 9:16 Odessa, NOW 1942 over a white map margin).

## What changed (src/then_now.py only)

A narrow port of the "ink on paper" rule from the larger freeze-panel rewrite; nothing else from it.

- For each marker, the box round its kicker and year (their ink boxes' union grown by `PAPER_PAD` = 24 px, kept
  to its photo) is measured on its split frame and its alone frame, unshaded (`paper_gate`). It is paper when, on
  both: mean luma >= 195, 10th percentile >= 140, Canny(50, 150) edge density <= 0.20, and at most 2% of its
  pixels darker than 0.6 x its mean (the last gate keeps line-art grounds with their own black rules and labels,
  e.g. the Aswan diagram, from getting black ink over black linework).
- A paper marker is set in brand INK (#0B0E12), no halo, no glow. If any ink line measures under PLACE_TARGET
  (4.8; planned, or drawn on the split or alone frame) it falls back to the bone/gold path unchanged.
- With one ink marker the other's kicker is bone (two neutrals, never ink + gold).
- Off paper the code path is HEAD's: no other constant, timing or label changed (THEN / NOW kept).
- `then_now.LAST_PAPER` records the last render's gate stats and type mode per marker (a record only).

Tests: `tests/test_thennow_paper_2026_11_04.py` (new). Five existing tests that use a flat white synthetic photo to
exercise the shade/glow or the gold/bone pairing now pin `INK_ON_PAPER = False` first (that ground is paper by
design now); no expected value changed.

## Evidence

Real run inputs only, from the freeze-panel fixture catalogue, rendered with
`../thennow/render_evidence.py --one <case> <format>` (frames-only path) from the HEAD tree (`before/`) and this
step's tree (`after/`); sheets by `../thennow/make_compare.py` in `compare/` (half size, THEN-alone, mid-wipe and
split moments; the sheet's "THEN ?/NOW ?" mode line is blank because the frames-only meta carries no mode; the
modes are below). `measurements.json` has every number; `after/<fmt>/<case>/paper_gate.json` the gate per marker.
Odessa, Gol Gumbaz and Aswan are labelled stress cases (rejected candidates used as grounds, years from the
topics' fact sheets, not capture years); Pantheon and Colossi are the two accepted pairs.

Gate stats (split frame; alone frame within a few units) and delivered contrast at the split (drawn / graded):

| case | fmt | marker | mean / p10 / edge / dark | mode | before (bone) drawn / graded | after drawn / graded |
|---|---|---|---|---|---|---|
| Odessa near-white map | 9:16 | THEN | 187 / 87 / 0.315 / 0.151 | unchanged | 5.21-5.45 / 6.69-7.24 | identical |
| | | NOW | 254 / 254 / 0.000 / 0.000 | **ink** | 5.17-5.63 / 6.53-7.44 | 19.18 / 16.60 |
| | 16:9 | THEN, NOW | 207 / 135 / 0.293 / 0.077; 112 / 82 / 0.167 / 0.006 | unchanged | | frames identical |
| Gol Gumbaz bright map | 9:16 | THEN | 208 / 155 / 0.119 / **0.041** | unchanged | | frames identical |
| | | NOW | 146 / 40 / 0.126 / 0.400 | unchanged | | |
| | 16:9 | THEN | 210 / 157 / 0.175 / **0.031** | unchanged | | frames identical |
| | | NOW | 222 / 220 / 0.019 / **0.049** | unchanged | | |
| Aswan line-art diagram | 9:16 | THEN (white sky of the 1922 photo) | 253 / 253 / 0.007 / 0.002 | **ink** | 5.25-5.68 / 6.75-7.58 | 19.01 / 16.61 |
| | | NOW (the diagram) | 204 / 147 / 0.076 / **0.087** | unchanged (kept off the linework) | 4.95-5.09 / 6.04-6.21 | same values |
| | 16:9 | THEN, NOW | 202 / 89 / 0.184 / 0.142; 171 / 90 / 0.046 / 0.343 | unchanged | | frames identical |
| Pantheon 1934 / 2018 | both | both | not paper (mean 83-175) | unchanged | | frames identical |
| Colossi 1876 / 2011 | 9:16 | THEN | 218 / 228 / 0.010 / **0.063** (a colossus in the box) | unchanged | | frames identical |
| | 16:9 | THEN | 229 / 228 / 0.000 / 0.000 | **ink** | 5.25-6.06 / 6.74-8.40 | 15.10-15.22 / 15.02-15.04 |
| | | NOW | 151 / 144 / 0.002 / 0.001 | unchanged (mean under 195) | 5.09-5.60 / 5.37-6.47 | same values |

Bold stats are the failing gate. Every ink line clears PLACE_TARGET 4.8 planned, drawn and graded (lowest 15.0).
Byte identity: every master frame of every case/format with no ink marker is pixel-identical to HEAD (Pantheon,
Gol Gumbaz, Odessa 16:9, Aswan 16:9, Colossi 9:16). Where a marker went ink, the other marker's plates are
unchanged (it was already bone in all three), so only frames showing the ink marker differ.

Looked at: Odessa 9:16 NOW 1942 is clean ink on the white margin, no stepped halo; Aswan 9:16 THEN 1922 is ink on
the photo's white sky while NOW 2005 over the diagram keeps its bone/halo; Colossi 16:9 THEN 1876 is ink on the
pale paper sky beside a bone NOW 2011 on the blue sky.
