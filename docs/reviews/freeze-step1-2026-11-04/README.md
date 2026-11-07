# Freeze pass, step 1: the handover's narrow fixes (2026-11-04)

Inputs are real runs only (`output/run_*`, the fixture catalogue in `freeze-panel-2026-11-04/fixtures`). Myth cards
use real fact-sheet facts in test-signed fields (labelled "new: real fact, test-signed field").

| Fix | What changed | Evidence |
|---|---|---|
| Caption proper names | `video_composer.name_bonds`: a name (capitalised run, particles between capitals, a generic head noun, regnal numerals, number words, a number after a month or designator) is one bond in 9:16 and 16:9 bursts and sidecar cues, with its article. A name too wide for one burst at the 66 px bond floor, or whose article cannot fit with it, is no bond, so no burst ends on THE. A sentence-opening word joins a name only if the narration capitalises it elsewhere; a possessive ends a name. | `review2/` corpus burns; 35 real runs: name splits 30 / 7 / 2 (9:16 / 16:9 / cues) at HEAD, 0 after; no new burst ends on THE/A/AN; no burst under 66 px |
| 16:9 vignette | `picture_vignette`: ffmpeg's `aspect` is the SAR, so 16/9 crushed 16:9 corners to black; 16:9 now `PI/6.4:aspect=1`, 9:16 byte-identical | `review1/captions/richat169_*` |
| Montserrat Bold | static OFL face bundled for the 16:9 burn-in; licence evidence recorded | `tests/test_font_evidence.py` |
| Myth source line | `_myth_fit` on 9:16 never drops the source: 84 px, +1 fact line, 76 px floor, then the largest size that holds it; a clean break first on every rung; refused when the source fits nowhere. HEAD dropped it on 4 of 44 lengths, now kept at 80 px; the other 40 identical. Strike bar clamped to the zone's right edge. | `review1/myth/` |
| Then-and-now | ink on paper: a marker whose ground passes the paper gate (mean >= 195, p10 >= 140, edge <= 0.20, dark <= 0.02) is set in INK with no halo or glow; everything else pixel-identical to HEAD | `thennow/` |

Reviews: review 1 NOT OK (caption regressions, font test), fixed; review 2 NOT OK (possessive joined a name: INSIDE |
RUSSIA'S CATHERINE PALACE), fixed and pinned by a test. Scores at review 2: vignette 9.5, font 9, myth 9.5,
then-and-now 9; captions 8 before the possessive fix.

Open (non-blocking, for the step-2 rounds): 5 more one-word 9:16 bursts than HEAD, each a content word beside a name too
wide to share a burst (allowed by the operator decision); figure ranges HEAD already splits (BETWEEN 94 AND | 104);
Odessa THEN 1941 keeps its halo (busy map, not paper).

Known machine-only failures, identical at HEAD: `test_design_cards_2026_11_03.py::test_exact_anchors` (Pillow
without raqm here; pins from a raqm build) and `::test_private_helpers_pinned` (NVENC cannot open 64x64; a CPU retry
belongs in `graphics._encode`, owned by the maps session).
