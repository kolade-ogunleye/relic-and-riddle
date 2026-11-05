# Operator decision 2026-11-05: measure without kerning

libass draws the burned-in captions unkerned: the ASS header in `src/video_composer.py` has no `Kerning: yes`, and
the rendered 9:16 and 16:9 headers are checked for it (`test_caption_measure_is_unkerned`). Pillow's raqm layout
applies the font's GPOS kerning, so kerned capital pairs measured narrower than they draw (WAVY VAULT 7,000: 545 px
measured, 552 px drawn). Every caption width measure now sums single-letter advances (`vc.unkerned_length`, plus the
CapText Spacing after each glyph in 9:16, as libass draws it). The 9:16 measure covers the ink measure (`_libass_line`, so
`measure_caption_phrase`, `_caption_ink_width`, the fit size, `_fits_caption_width` / `_burst` / `_bonded`, the
two-line split and the plate widths) and the round-2 type measure (`_caption_text_width`, used by the name-bond
check). The 16:9 measure covers the fit (`_fits_wide`) and the plate width in `build_subtitles`. Small capitals stay. No
`Kerning` was added to the header. Nothing is drawn differently; only measures, plates and fit decisions move.

The round-4 base (`_r4_fits_*`, `round4_burst_cuts`, HEAD 0036b18's phrasing) measures unkerned as well. Checked: with
the kerned measure and with the unkerned one, `round4_burst_cuts` reproduces `head_0036b18_cuts.json` exactly on every
gate case, in both formats (0 mismatches each way). `test_round4_base_is_head_0036b18` therefore does not need HEAD's
own kerned measure, and no kerned path is kept.

Inputs: the five fixture runs (`../../fixtures`, real `words.json` and `emphasis_words`) in both formats. Before is
738973a (a worktree, since removed). After is that commit plus this change. Raqm is on (Pillow 12.3.0 / raqm
0.10.5 / FriBiDi 1.0.18), FONT_SIZE=80, and ffmpeg's libass draws.

## Measure vs ink (every one-line burst of the five runs)

`measure_vs_ink.py` renders every burst of each run with libass in one pass per run and format: 329 one-line 9:16
bursts and 186 16:9 bursts, plus the ten `REEL_MEASURE_RAQM` samples. It measures each burst's ink inside its plate.
Raw rows are in `measure_before.json` / `measure_after.json`.

**9:16, ink less measure (px), 329 bursts** (none is two-line):

| measure | 0 | 1 | 2 | 3 | 4 | 5 | over 2 px | worst |
|---|---|---|---|---|---|---|---|---|
| kerned (before) | 6 | 140 | 128 | 36 | 14 | 5 | 55 | 5 |
| unkerned (after) | 8 | 224 | 97 | 0 | 0 | 0 | **0** | 2 |

The kerned measure was worst on these bursts, where libass drew 5 px more than it measured. They are now within 2 px:
WAS INITIALLY CONSIDERED (74 px: 619.5 measured, 624 ink), BUILT IN VARIOUS (418.3 / 423), ALTERNATIVE SPECULATION
(606.0 / 611), GRAY BASALT MAUSOLEUM (609.2 / 614) and A CATACLYSM STRUCK (545.1 / 550). Five of them are rendered
and pinned in `test_unkerned_measure_matches_libass_ink_on_real_bursts`, which also asserts that the kerned measure
was more than 2 px short on each. The new measure is never wider than the ink. The 0 to 2 px left over is libass's
anti-aliased edge (the tolerance `REEL_INK_OVER_MEASURE = (0, 2)`).

**REEL_MEASURE_RAQM samples** (fit size, measure before -> after, ink): Wait, look here! 80, 443 -> 443, 444. Beneath
Odesa 80, 401 -> 402, 403. It was 80, 170 -> 171, 172. an estimated 720 tons 80, 638 -> 639, 640. shore of the Black
Sea 80, 634 -> 634, 635. Amenhotep III was 80, 509 -> 510, 511. **WAVY Vault 7,000 80, 545 -> 550, 552**.
COUNTERREVOLUTIONARIES 77, 634 -> 634, 635. Incomprehensibilities 80, 593 -> 593, 594. the mine's rock salt 80,
558 -> 558, 560. All ten are now 1 to 2 px under the ink.

**16:9.** The plate is the text advance plus `CAPTION_PAD_X` on each side, so `wide_model.py` compares advances. Each
burst's ink is modelled at the em libass actually draws (Fontsize 81, so 51.86 px), unhinted, from a 1000 px reference.
Unkerned, that model matches the rendered ink within 1 px on all 186 bursts (-1: 13, 0: 124, +1: 49). Kerned, it was
off by -1 to +11 px (70 of 186 over 2 px). So libass draws 16:9 unkerned too. The plate measure, against the drawn
advance:

| plate measure | mean | sd | range |
|---|---|---|---|
| kerned at 52 px (before) | +0.2 | 1.7 | -8.1 .. +3.3 |
| unkerned at 52 px (after) | +2.4 | 0.6 | +0.8 .. +3.4 |

The unkerned measure no longer runs short of the drawn line (before, a burst could draw up to 8 px more than its
plate measured). What is left is a steady +0.27 %: the plate is measured at the 52 px em, and libass draws 51.86 px
from Fontsize 81. Correcting that would narrow every 16:9 plate by about 2 px. It is outside this decision and was
not changed. (In `measure_*.json`, the 16:9 `kerned` / `unkerned` fields are a rough ink estimate at 52 px. The
accurate model is `wide_model.py`.)

## Phrasing changes

Only one burst changes its phrasing, and it is in the 9:16 layout of Richat, a 16:9 run that the gate also tests as a
Reel. No other run changes its phrasing in either format, and every 16:9 phrasing is unchanged (98 + 20 + 19 + 24 + 25
bursts).

- **run_richat_structure_sahara_eye_1792740961_long, 9:16**: GEOLOGICAL HERITAGE SITES. (74 px) becomes GEOLOGICAL
  HERITAGE | SITES. (80 px each). HEAD 0036b18 kept the burst whole. Kerned, it measured 639.9 px at 74 px, within the
  640 px ink budget, but libass draws 642 px of ink. Unkerned it measures 640.6 px, so it no longer fits at 74 px
  (and it is no bond). It is split because of width (`GATE_FORCED`, kind "width"), which leaves SITES. alone
  (`LONE_FORCED`). The other cut, GEOLOGICAL | HERITAGE SITES., would leave GEOLOGICAL alone instead. HEAD's own cut,
  OFFICIAL | GEOLOGICAL, stands. Pairs: `richat_structure_sahara_eye_9x16_w429_word-429_before_after.jpg` (the burst)
  and `..._w431_word-431_...` (SITES.). Before this change, the test file's own note recorded that this cut "went"
  under raqm. The honest measure brings it back.

**One-word 9:16 bursts per run, before -> after**: richat 7 -> 8 (+ SITES.), gol_gumbaz 1 -> 1, tunguska 1 -> 1,
amber_room 3 -> 3, voynich 1 -> 1. 16:9: 0 -> 0 everywhere.

## Plate widths (no phrasing change)

The plates follow the honest measure. On unchanged 9:16 bursts most plates stay the same or grow 1 to 2 px. The
exceptions come from `quantise_plate_widths`: it rebuilds its width levels per run of back-to-back bursts, and a 1 to 2
px measure change can move a level.

- **tunguska 9:16**: LOCAL TIME 452 -> 342 and RADIAL PATTERN 454 -> 552
  (`tunguska_event_1908_9x16_w010_...`, `..._w042_...`). RADIAL PATTERN measures 399 against 397, so the level at 453
  no longer takes it. LOCAL TIME gets its own plate and RADIAL PATTERN takes the 552 level.
- **richat 9:16**: plates whose run held the 696 px GEOLOGICAL HERITAGE SITES. plate drop to 687. Most other plates
  are +2 px (`bursts_diff_richat_structure_sahara_eye_9x16.txt`; `..._w410_...` is THE INTERNATIONAL UNION OF
  GEOLOGICAL SCIENCES).
- **16:9**: plates change -2 to +10 px (6 of 306 shrink 1-2 px where raqm kerned a pair apart, e.g. colossi "the northern monument became" 961 -> 959). Kerned pairs grow most: voynich "and World War Two attempted" 900 -> 908
  (`voynich_manuscript_16x9_w104_...`), "cryptographers from both World War One" 1200 -> 1208. Richat "an
  extraterrestrial impact crater" is `richat_structure_sahara_eye_16x9_w212_...`.

Full before/after burst lists: `bursts_diff_<case>.txt`. They were rendered with
`../render_evidence.py --code-root <tree> --case TOPIC:FMT --word TOPIC:FMT:INDEX` (`--case` and `--word` are new
options: they pick the cases and the extra words to sample), with EVIDENCE_BEFORE_LABEL=738973a. Each JPEG shows the
two half-size frames side by side, labelled BEFORE (HEAD 738973a) | AFTER (working tree), and is under 400 KB.
