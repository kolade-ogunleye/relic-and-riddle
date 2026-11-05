# Design work handover (2026-11-04)

Branch: `claude/intelligent-carson-ybsccd` (PR #3). The goal is every graphic at an elite 10/10. Each design
is judged by independent expert-panel reviews, and it is frozen once a fresh panel scores it 9.5 or more and can
name no further gain, two consecutive adversarial rounds find no new defect, and the operator agrees.

## Work split (avoid file conflicts)

- **Cloud session (maps):** owns `src/graphics.py` map code, `src/geo.py` and the map/timeline tests
  (`tests/test_r3_graphics_*`, `test_r4_graphics_*`, `test_timeline_hierarchy_*`, `test_round6/7/8_review_*`,
  `test_scale_bar_*`, `test_map_marker_*`, `test_geo_polylabel_*`, `test_globe_relief_*`, `test_timeline_ink_*`).
  Do **not** edit these files from another session until the maps land.
- **PC session:** everything else (the list below).

## State by design

| Design | State | Next |
|---|---|---|
| Reel covers, grid tiles, YouTube thumbnail | Round-4 work committed; freeze step 2 not passed (8 / 8 / 8.5), work kept uncommitted | Step 5 |
| Captions 9:16 / 16:9 | Freeze step 2 reviewer-passed (9.1 / 9.3 / cues 9); panel 6.5-7.5, not frozen | Step 5: panel findings as spec (`docs/reviews/freeze-panel-2026-11-04/captions/step2`) |
| IG carousel, FB gallery, Stories | Committed; freeze step 2 not passed (6.5 / 6 / 8), work kept uncommitted | Step 5, after finding why each round scored lower |
| Reel loop bridge, 16:9 end screen | Committed; freeze step 2 not passed (end screen 7.5, loop bridge 9), work kept uncommitted | Step 5; composer wiring (W1, L1, L2, L4) is a separate task |
| Then-and-now | Freeze step 2 reviewer-passed (9); panel 7-8.1, not frozen | Step 5: panel findings as spec |
| Photo callouts | Redesign committed; unattended runs get none until the accuracy audit exists | Needs real photos and an accuracy audit |
| First frame, chapter card, myth-vs-fact (`src/design_cards.py`) | Freeze step 2 reviewer-passed (9 / 9 / 9); panel 8.1-8.5, not frozen | Step 5: panel findings as spec; operator: chapter cards all-or-none |
| Quote cards | Held (`QUOTE_CARDS_ENABLED=false`) | Enable only with signed records and a real checker key; speaker, work and date come from the signed check record only |
| AI illustrations | Built | Needs a Gemini key and Tesseract to judge |
| Maps, timelines | In progress in the cloud session (round 8) | — |

## Operator decisions, freeze step 2 (approved 2026-11-05, for the PC session to implement)

- **Text layout engine: raqm everywhere.** Do not force BASIC. Done:
  - CI installs `libfribidi0` (`.github/workflows/tests.yml`).
  - Dev PC (2026-11-05): `libfribidi-0.dll` from MSYS2's UCRT64 package `mingw-w64-ucrt-x86_64-fribidi-1.0.18-1`
    (repo.msys2.org; package SHA-256 `4837d362...ac920` matches the repository database; DLL SHA-256
    `4c775e98...746c9`; it imports only the Windows CRT and KERNEL32) in `C:\Users\omokolade\bin\fribidi`, on the
    user `PATH`. Pillow 12.3.0 then reports raqm 0.10.5 and FriBiDi 1.0.18. Pillow's docs: its wheels load
    `fribidi.dll`, `fribidi-0.dll` or `libfribidi-0.dll` at run time from the DLL search path; they name no download.
  - `main.warn_missing_raqm()` warns at run start when `PIL.features.check("raqm")` is false, and
    `tests/test_raqm_layout_2026_11_05.py` fails a suite run on a build without raqm.
  - Measured test values are pinned to raqm (the CI fix below). Processes started before the `PATH` change (the dev
    bot) lay text out with BASIC until they are restarted.
- **Chapter cards: draw all of them.** A long act title (3 of 5 Richat titles are over 32 characters) wraps to two
  lines; no card is dropped. Done (2026-11-05, reviewer 16:9 9.0, 9:16 9.2): the 32-character cap is gone; on 16:9 the
  two-line ladder runs to the 96 px floor, and a size that cannot reach the numeral's ink top hangs from it (Richat act
  1 at 144, act 4 at 128, act 5 at 152). On 9:16 the existing reviewed rule takes three lines when they set at least
  16 px larger, so acts 1, 4 and 5 set on three (100, 100, 128); two lines there would be 84 px (act 1) or under the
  84 px floor (act 4). Every card that drew before is identical (1,087 cases checked). A title is refused only when it
  cannot fit at the floor (about 55-62 characters, depending on the words) or fails grounding. Open: one title size per
  video (16:9 now 128-176 px across the five acts); a colon break preference ("INTRODUCTION: / ...") on 16:9.
- **Caption small caps: none.** Anton has no true small caps, and faux caps are 18% thinner; keep full caps.
  **Withdrawn for now (operator, 2026-11-05):** built and measured, full caps make the 9:16 bursts 12-18% wider
  (one-word bursts 13 -> 71 on the five real runs; names stop fitting at the 66 px floor and end on THE; a third main
  test, `test_caption_emphasis.py::test_clause_starter_starts_new_burst`, fails). Small caps stay until a font with
  true small caps or a resized full-caps design is reviewed. The measured patch is kept outside the repo
  (`C:/tmp/fullcaps-2026-11-05.patch` on the dev PC).
- **Caption measure without kerning (operator, 2026-11-05):** libass draws the captions unkerned (the ASS header has
  no `Kerning: yes`), while Pillow's raqm measure kerns, so a kerned pair measures narrower than it draws (WAVY VAULT:
  545 measured, 552 drawn). Measure each word as the sum of its letters' advances, as libass draws it; nothing on
  screen changes. To build, review and re-pin.

## CI on PR #3 (red since 3ac24dd)

`tests/test_freeze_captions_2026_11_04.py` reads gitignored local data (`output/run_*/words.json`,
`assets/richat_structure_sahara_eye/*.jpg`), so 31 tests fail on CI; two more compare widths measured on the PC.
Commit the needed `words.json` files and one small still into `docs/reviews/freeze-panel-2026-11-04/fixtures/`, point
`fixtures.json` at them, and pin the two measurement tests to raqm (decision above). Do not skip tests. Details:
https://github.com/kolade-ogunleye/relic-and-riddle/pull/3#issuecomment-6652354441

Done (2026-11-05): the run files the tests read are committed under `fixtures/runs/<run folder>/`
(`fixtures.run_file` and `run_path` rebase recorded run paths onto them), with a 960 px Richat still under
`fixtures/stills/` (credited in `fixtures/README.md`, CC BY 2.0); `fixtures.json` points its captions `words` at the
copies. `regression/head_0036b18_cuts.json` is HEAD 0036b18's own phrasing regenerated under raqm
(`make_head_0036b18_cuts.py`): its 9:16 cuts change in two cases (edge gains index 18; colossi 38 becomes 36 and 39),
and the gate tables follow. The Reel measure test pins raqm's widths: raqm kerns, so WAVY VAULT measures 545 px against
the 552 px libass draws (the ASS header does not ask libass to kern); every other sample is within 2 px.

## Rules (from AGENTS.md and the operator)

- Never change an expected value in a test that exists on `origin/main` without the operator's explicit approval.
  In a branch-only test, change one only where a recorded decision changes that behaviour, and list each change.
- Commit only reviewer-passed work. Send before/after images per design.
- Fact safety: cards draw text only from checked, signed card fields (`src/card_fields.py`). Fact-check records are
  HMAC-signed (`src/record_signing.py`, `FACT_RECORD_KEY`). Every script goes through the automated checker (4419229).
- Settings are read only in `src/config.py` and documented in `.env.example`.

## Operator decisions on record

- The pin marks the subject on locator maps only; route stops stay dots.
- Scale bar: km first unless the narration names a unit.
- Timeline spans sit between the events ("17 YEARS LATER"), never level with a year.
- Carousel window counts from the Reel (50edb7c).
- Reels end on the loop bridge; the full end card is 16:9 only.

## Go-live checklist

- Production `.env`: set `FACT_RECORD_KEY` (32+ bytes: `python3 -c "import secrets; print(secrets.token_hex(32))"`)
  and remove `FONT_SIZE=72`.
- On the VM: `python scripts/fetch_geodata.py` and `python scripts/fetch_relief.py`.
- A Gemini key, and Tesseract (or `IMAGE_GEN_REQUIRE_OCR=false`), for illustrations and for real fact checks.
- On the VM, FriBiDi for Pillow's raqm layout (operator decision 2026-11-05): put `libfribidi-0.dll` (MSYS2 UCRT64
  `mingw-w64-ucrt-x86_64-fribidi`, as on the dev PC, its SHA-256 checked against the repository database) in a folder
  on the service account's `PATH`, restart the bot, then confirm that
  `python -c "from PIL import features; print(features.check('raqm'))"` prints True and that the run log has no
  "raqm text layout is unavailable" warning.
