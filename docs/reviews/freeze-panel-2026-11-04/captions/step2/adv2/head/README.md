# Burned-in captions, freeze pass round 2 (2026-11-04): after

Rendered by `render_evidence.py` from the five real runs of the freeze-panel catalogue (`fixtures.load('captions')`: each run's own words.json, emphasis_words and scene timeline) through `VideoComposer.build_subtitles`, burned over the run's own scene background at the burst's time.

Picture chain: `film_grade_filter(0.0)`, grain (`noise=alls=8`, seed 7), then the lens vignette exactly as compose_video draws it: `src.video_composer.picture_vignette(width, height)` is imported, giving `vignette=PI/6.4:aspect=1` at 1920x1080 and `vignette=PI/4.2:aspect=9/16` at 1080x1920 (lifted over graphic clips, as graphic_vignette_enable does), then libass with the bundled fonts (Anton 80 px for 9:16; static Montserrat Bold for 16:9).

Round 4 (review fixes, 2026-11-04): the phrase builder ranks the hard defects (a function-word burst, a mid-sentence end on a function word) first, then one soft cost: a lone content word costs 3, a pre-modifier, possessive or attributive end 2, a one-word lead-in 2, a proper-modifier or open noun phrase end 1 (round 3 ranked every M1 end above a lone word, so 9 lone bursts at HEAD became 38). A possessive name takes the noun it owns (UPON MOHAMMAD ADIL / SHAH'S DEATH, on two lines; round 3: UPON | MOHAMMAD ADIL SHAH'S | DEATH,), a two-line name takes its lead-in on line 1, a lower-case head after a connector names its own part (KING KRISHNA I | OF THE RASHTRAKUTA DYNASTY), and a figure's unit is a bond (ESTIMATED 200,000 TONS), so tests/test_loop_seam_and_captions_2026_11_03.py passes unchanged. Each remaining one-word, function-word-only and under-0.50 s burst is listed below; the rule that forces each is in the test file (LONE_WORDS, SPEECH_LIMITED, GATE_ALLOWED).

Round 3 (review fixes, 2026-11-04): the names column joins catalogue names that only connectors separate in the run's words (fixtures.json lists 'International Union' and 'Geological Sciences' apart; the run says 'International Union of Geological Sciences', listed once and marked `joined`). The caption code ends no burst on an attributive -ing word (AN ACTIVE HIDING | PLACE, now TUNNELS AS AN ACTIVE | HIDING PLACE). Open for the operator: V1 (d) as written (minimum corner luma > 0) is not met where the photograph itself is black (the ratio below is the proposed measure); M1 (b) OF A PROMINENT (gol) and the width-forced gate pairs; G1 (b) the two-line plate's rise above the graphics floor (y 1058).

The Kailasa case is new: the fact-sheet statement kailasa-commencement-patron with timings fitted to the Kailasa run's voice rate. Its timings are synthetic, so it is layout evidence only and carries no timing counts.

Per case (the counts the tests in tests/test_freeze_captions_2026_11_04.py check):

| case | format | bursts | names WHOLE / SPLIT | ending on an article | function-word-only | one-word | under 66 px | under 0.50 s | spanning a pause | two-line | G1 plates |
|---|---|---|---|---|---|---|---|---|---|---|---|
| richat_structure_sahara_eye_16x9 | 16:9 | 98 | 6 / 1 | 0 | 0 | n/a | n/a | 0 | 6 | 0 | 0 |
| gol_gumbaz_whispering_gallery_9x16 | 9:16 | 41 | 3 / 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| tunguska_event_1908_9x16 | 9:16 | 35 | 1 / 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| amber_room_heist_9x16 | 9:16 | 41 | 4 / 0 | 0 | 0 | 3 | 0 | 1 | 1 | 0 | 0 |
| voynich_manuscript_9x16 | 9:16 | 42 | 3 / 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 |
| kailasa_temple_monolith_NEW_9x16 (new, synthetic timings) | 9:16 | 6 | 1 / 1 | 0 | 0 | 3 | 0 | n/a | n/a | 0 | 0 |

The bursts behind each count:

- richat_structure_sahara_eye_16x9: spanning a pause: 4: 0.55s after 'landmark,' in desert landmark, the concentric, 11: 0.54s after 'across,' in across, though satellite observations, 52: 0.74s after 'timeline:' in timeline: dating of the carbonatite rocks, 73: 0.34s after 'activity,' in activity, the physical record, 78: 0.57s after 'reality,' in reality, as the feature is an ancient, 94: 0.34s after 'processes,' in terrestrial processes, what
- gol_gumbaz_whispering_gallery_9x16: one-word: 24: TRADITIONS; spanning a pause: 31: 0.60s after 'death,' in DEATH, LEAVING
- tunguska_event_1908_9x16: one-word: 25: WORLDWIDE
- amber_room_heist_9x16: one-word: 3: DISASSEMBLED, 9: EIGHTEENTH-CENTURY, 10: EXPANSIONS; under 0.50 s: 7: 0.48s BY ONE; spanning a pause: 8: 0.37s after 'estimate,' in ESTIMATE, FOLLOWING
- voynich_manuscript_9x16: one-word: 6: ESTABLISHED; spanning a pause: 21: 0.32s after 'plants,' in PLANTS, THE TEXT
- kailasa_temple_monolith_NEW_9x16: one-word: 0: EXCAVATION, 2: COMMENCED, 4: RASHTRAKUTA

16:9 corners (each 96 px corner, TL TR BL BR): the minimum luma, and the mean luma with the vignette over the same frame's without it. The real photographs hold pure-black pixels of their own (a night sky, a shadow), so a minimum of 0 there is the picture's; the ratio is the vignette's own darkening (the chain before V1 took the corners to 0).

- richat_structure_sahara_eye_16x9_w000_first: min [0, 3, 3, 118], mean ratio [0.414, 0.508, 0.513, 0.61]
- richat_structure_sahara_eye_16x9_w002_name-richat-1: min [0, 3, 3, 118], mean ratio [0.414, 0.508, 0.513, 0.61]
- richat_structure_sahara_eye_16x9_w010_name-adrar-9: min [0, 3, 3, 118], mean ratio [0.414, 0.508, 0.513, 0.61]
- richat_structure_sahara_eye_16x9_w014_name-adrar-13: min [0, 3, 3, 118], mean ratio [0.414, 0.508, 0.513, 0.61]
- richat_structure_sahara_eye_16x9_w199_name-richat-198: min [86, 110, 0, 125], mean ratio [0.606, 0.607, 0.442, 0.611]
- richat_structure_sahara_eye_16x9_w292_name-middle-289: min [0, 26, 3, 1], mean ratio [0.285, 0.587, 0.562, 0.61]
- richat_structure_sahara_eye_16x9_w387_name-richat-386: min [100, 66, 6, 0], mean ratio [0.608, 0.606, 0.533, 0.429]
- richat_structure_sahara_eye_16x9_w412_name-international-408: min [121, 0, 1, 2], mean ratio [0.609, 0.531, 0.376, 0.552]
- richat_structure_sahara_eye_16x9_w224_longword: min [0, 0, 74, 30], mean ratio [0.591, 0.577, 0.597, 0.599]
- richat_structure_sahara_eye_16x9_w213_longword: min [86, 110, 0, 125], mean ratio [0.606, 0.607, 0.442, 0.611]
- richat_structure_sahara_eye_16x9_w352_possessive: min [70, 58, 18, 0], mean ratio [0.603, 0.596, 0.559, 0.572]
- richat_structure_sahara_eye_16x9_w227_mid: min [0, 0, 74, 30], mean ratio [0.591, 0.577, 0.597, 0.599]
- richat_structure_sahara_eye_16x9_w385_late: min [100, 66, 6, 0], mean ratio [0.608, 0.606, 0.533, 0.429]

Files: `bursts_<case>.txt` / `bursts.txt` (every burst: time, size, plate, text; two-line bursts as `line 1 / line 2`; G marks a G1 opaque plate), `summary.json` (all of it as data), `<sample>_full.jpg`, `<sample>_half.jpg`, `<sample>.png` (the caption band), `sheet_<case>.jpg`, and `<first 16:9 sample>_corners.jpg` (the four corners at full resolution). Before | after pairs are in `../compare/<sample>_before_after.jpg`.
