The full suite passes (6,519 passed, 13 skipped, 0 failed), but two review items get worse with this round's fixes and two need your ruling. The new outline gate leaves Macau 16:9 and San Marino at both sizes with no home name (logged drops). Westminster still shows "34 / YEARS LATER" and "887 YEARS LATER" over the spine; keeping the name whole forces both, I think. Several directive 6 items are still open.

Nothing was committed, pushed or stashed. I edited only `src/graphics.py`, `src/geo.py`, `tests/geo_truth.py`, `tests/test_round9_review_2026_11_04.py` (branch-only) and the new `tests/test_round16_review_2026_11_05.py`. The docs and fetch scripts did not need changes.

## What changed (by review problem)

1. **Luxembourg named again.** A home fill of at least 20,000 px that holds its name nowhere is now framed closer, 0.8× at a time, up to 4 times (`zoom` parameter on `_render_map`, constants at `src/graphics.py:2524`, retry at `:4337`). Luxembourg now reads LUXEMBOURG inside its fill at both sizes.
2. **Outside home names point at the site's own part of the home.** The part holding the site is computed at `:4299` and used for placing the name, checking it and drawing its leader. Melilla 16:9 now sets SPAIN by Melilla itself, not 430 px away at the Chafarinas.
3. **Topic-country tie-break limited.**
   - The topic's country now beats a polygon that holds the site only within `SITE_HINT_KM` = 2 km (`src/geo.py:1036`, `:1248`); off all ground the old 5 km still applies.
   - When it does win, the marker is moved onto the chosen country's ground (`snap_onto`, `src/graphics.py:2422`, `:4148`), so the marker and the highlighted fill agree.
   - Kept: Gibraltar (0.3 km), Vaals (1.75 km), Leticia (1.4 km), Büsingen (1.9 km).
   - Now decided by the ground: Baarle reads NETHERLANDS, Treriksröset NORWAY, the Golden Triangle MYANMAR.
   - Prespa sits exactly on the 2 km line and still reads GREECE.
   - **Decision for you:** with a 0 km cap Gibraltar would read SPAIN and Leticia BRAZIL, so I kept 2 km plus the marker move. If you want the strict reading of directive 1, the cap goes to 0.
4. **Route names drawn by the base map are now checked.** That includes the home's (`named_boxes` in `relief_base`, `:1448`; checks at `:10546`). A failing name is redrawn without it, and the layout's `country` field records the base-drawn home.
5. **Spans: two rows only where needed.** Only spans that one row leaves over the spine or unplaced now get two rows (`:7006`). Two examples where the gap cannot fit one row:
   - **Westminster:** keeping "WILLIAM THE CONQUEROR" whole makes the 1066 label about 530 px wide. That leaves "34" and "887" no one-row place under the spine within the 1,600 px run, so they stay as above.
   - **Sagrada:** the whole name widens the last label, which narrows the gaps, so "127 / YEARS LATER" now takes two rows.
6. **Proper names in labels.** "of" now links a name ("Tower of Jesus Christ"); a sentence's first article no longer counts as part of the name.
   - Sagrada reads "THE TOWER OF JESUS CHRIST / IS COMPLETED", with the column widened.
   - A label tries more rows inside its own column (weak rows allowed) before it widens (`name_whole_rows`, `:6595`).
7. **Titles.** A title never breaks after ST, MT and similar abbreviations (`:9795`, `:9826`), so Macau reads "RUINS OF / ST PAUL'S, MACAU".
8. **Outline gate covers neighbours too.** Every outline in an island or small-country close-up is gated: median drawn source edge at most 18 px for the home and 22 px for other land (`view_edge_px` `:2618`, loop `:2768`). Macau, Saint-Pierre and Kwajalein are framed wider as a result.
9. **No apostrophe-like leader stubs.** An outside name now either touches its ground (under 3 px) or sits at least 16 px off it (`:3160`, constants `:3980`). Leaders now also reach specks smaller than the half-resolution sampling grid (`:3998`); this was a hidden bug.
10. **Title repeats and spellings** (`home_title_form`, `:2455`). A home name that only repeats the title takes the sovereign's name (Palmyra reads UNITED STATES OF AMERICA). A one-letter variant takes the title's spelling (MACAO becomes MACAU).
11. **Vietnam route.** Country names keep 36 px off stop names (`:9788`, `:10475`). Far names give way to adjacent neighbours (`:10536`). Neighbour names may step down one more size (`NEIGHBOUR_PX_STEPS`, `:3544`). CAMBODIA is now named at 9:16 but not at 16:9.
12. **Checker.** `tests/geo_truth.py` accepts the sovereign's name only when the title repeats the home's own name. The map layout now records `label`.

## Tests
- **Full suite** (`-n 2`): 6,519 passed, 13 skipped, 0 failed, in 86.5 min. The md5 of both source files was the same before and after the run and matches the current files.
- **Graphics set** (the 25 review files plus round 16): one run gave 1,853 passed and 5 failed, before my last fixes. The 5 (round 7 long label, Nauru ×2, Tristan ×2) then passed in targeted runs, and the full suite covers them all.
- **New file** `tests/test_round16_review_2026_11_05.py`: 26 tests.
- **Ruff:** clean on every file I touched.

## Expected-value changes
- **No test that exists on origin/main was changed.** `tests/test_graphics.py` is unchanged; its 12 lines removed against origin/main are from an earlier commit on this branch.
- **One branch-only test changed:** `tests/test_round9_review_2026_11_04.py`, `test_nauru_is_framed_where_it_reads`. Its gate now also accepts the new close-up gate (`own_px >= VIEW_EDGE_PX_OWN`), because problem 8 deliberately frames Nauru wider. Nothing else in its assertions changed.

## Still open
- **Home names dropped with a logged reason:** San Marino at both sizes and Macau 16:9, both left too small to hold a name once the gate frames them wider. The Arctic drift has no home on the open sea, as intended.
- **Independent checker** over all 122 map renders: no faults except Oecusse TIMOR-LESTE, which is the checker false positive the reviewer already noted.
- **Not fixed (directive 6):**
  - Trieste 9:16 has no Adriatic and no Slovenia named.
  - Minicoy names OMAN, but not the Maldives or Sri Lanka.
  - Valletta 9:16 sets MALTA outside its island.
  - Several site titles overlap their own island.
  - The 9:16 frames stay empty below about y 1150. I left that area alone because the burned-in captions use it.
  - US naming is still inconsistent across sizes.
  - Cambodia is unnamed on the Vietnam route at 16:9.

## Images
All in `./maps_work/r11build/r16/`. I looked at every render.
- `after/`: 128 renders from the final code (the review's 49 jobs plus 15 fresh places, at both sizes), each with its log and layout file. The fresh places are Kotor, San Marino, Andorra, Llívia, Pitcairn, St Helena, Reine, Isla del Sol, Kazungula, Taveuni, Ny-Ålesund, Majuro, Campione, Skellig Michael and Timbuktu.
- `before/`: HEAD renders of the 15 fresh places (the review's 49 HEAD renders are reused from `r11rev1/before`).
- `boards/ba_<job>_<size>.jpg`: 128 boards of HEAD against the working tree.
- `sheets/s1.jpg` to `s16.jpg` (all jobs), plus `final_a.jpg` and `final_b.jpg`. `after/p_luxembourg.jpg`, `p_melilla.jpg`, `p_macau.jpg`, `p_route_vietnam.jpg`, `p_tl_sagrada.jpg` and `p_vaals.jpg` show 9:16 and 16:9 side by side.
- `truth_after.txt`: the independent checker's output.
- `t/full.txt` and `t/gfx.txt`: test logs. The harnesses are in `h/`.