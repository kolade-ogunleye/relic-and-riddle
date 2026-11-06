# Design work handover (2026-11-04)

Branch: `claude/intelligent-carson-ybsccd` (PR #3). The goal is every graphic at an elite 10/10. Each design
is judged by independent expert-panel reviews, and it is frozen once a fresh panel scores it 9.5 or more and can
name no further gain, two consecutive adversarial rounds find no new defect, and the operator agrees.

## Work split (avoid file conflicts)

- The workstation builder owns implementation and landing in isolated worktrees, including maps at QF-20.
- The independent reviewer checks each candidate in separate worktrees and exchanges review files with the builder.
- The operator's development checkout stays untouched. The cloud session audits the completed programme afterwards.

## State by design

| Design | State | Next |
|---|---|---|
| Reel covers, grid tiles, YouTube thumbnail | Freeze step 2 not passed (8 / 8 / 8.5); the work is parked in the named stash `qf-park-2026-11-05` | Freeze loop resumes after QF Phase 4b on the post-QF code (rebase the stash) |
| Captions 9:16 / 16:9 | Reviewer-passed (9.1 / 9.3 / 9); caption measure without kerning landed (5cce25e); panel 6.5-7.5, not frozen | QF-21/QF-87 (true smcp or a derived font: faux caps are forbidden), then the freeze loop after Phase 4b |
| IG carousel, FB gallery, Stories | Freeze step 2 not passed (6.5 / 6 / 8); parked in the same stash | QF-80/QF-81 (operator decisions), then the freeze loop after Phase 4b |
| Reel loop bridge, 16:9 end screen | Freeze step 2 not passed (end screen 7.5, loop bridge 9); parked in the same stash | Freeze loop after Phase 4b; composer wiring (W1, L1, L2, L4) is a separate task |
| Then-and-now | Reviewer-passed (9); panel 7-8.1, not frozen | Freeze loop after Phase 4b |
| Photo callouts | Redesign committed; unattended runs get none until the accuracy audit exists | Needs real photos and an accuracy audit |
| First frame, chapter card, myth-vs-fact (`src/design_cards.py`) | Reviewer-passed (9 / 9 / 9); every chapter card drawn (9d3e051); panel 8.1-8.5, not frozen | QF-25 wires the cards into the composer; freeze loop after Phase 4b |
| Quote cards | Held (`QUOTE_CARDS_ENABLED=false`) | Enable only with signed records and a real checker key; speaker, work and date come from the signed check record only |
| AI illustrations | Built | Needs a Gemini key and Tesseract to judge |
| Maps, timelines | Handed to the PC session (581a866, WIP); independent review of fix2 on 52 new places: NOT OK (0 wrong names, 79/104 renders < 9) | QF-20 (Shapely + pyproj): 200-site sweep and independent 30-new-place review >= 9 on every design; the design freeze criteria remain separate |

## Operator decisions, freeze step 2 (approved 2026-11-05, for the PC session to implement)

- **Text layout engine: raqm everywhere.** Do not force BASIC. Done:
  - CI installs `libfribidi0` (`.github/workflows/tests.yml`).
  - Dev PC (2026-11-05): `libfribidi-0.dll` from MSYS2's UCRT64 package `mingw-w64-ucrt-x86_64-fribidi-1.0.18-1`
    (repo.msys2.org; package SHA-256 `4837d362...ac920` matches the repository database; DLL SHA-256
    `4c775e98...746c9`; it imports only the Windows CRT and KERNEL32) in `%USERPROFILE%\bin\fribidi`, on the
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
  screen changes. Done (2026-11-05, reviewer 9.3): `video_composer.unkerned_length`; every 9:16 burst of the five
  real runs now measures 0-2 px under its drawn ink (was up to 5 px), 16:9 within 1 px (was up to 11). One phrasing
  change: Richat as a Reel, GEOLOGICAL HERITAGE SITES. (640.6 px against the 640 px budget) splits before SITES.
  Plates grow 1-4 px on 9:16 and -2..+10 px on 16:9; caption builds got faster (Richat 9:16 2.85 s -> 1.0 s).

## CI on PR #3 (red since 3ac24dd)

`tests/test_freeze_captions_2026_11_04.py` reads gitignored local data (`output/run_*/words.json`,
`assets/richat_structure_sahara_eye/*.jpg`), so 31 tests fail on CI; two more compare widths measured on the PC.
Commit the needed `words.json` files and one small still into `docs/reviews/freeze-panel-2026-11-04/fixtures/`, point
`fixtures.json` at them, and pin the two measurement tests to raqm (decision above). Do not skip tests. Details:
https://github.com/<owner>/relic-and-riddle/pull/3#issuecomment-6652354441

Done (2026-11-05): the run files the tests read are committed under `fixtures/runs/<run folder>/`
(`fixtures.run_file` and `run_path` rebase recorded run paths onto them), with a 960 px Richat still under
`fixtures/stills/` (credited in `fixtures/README.md`, CC BY 2.0); `fixtures.json` points its captions `words` at the
copies. `regression/head_0036b18_cuts.json` is HEAD 0036b18's own phrasing regenerated under raqm
(`make_head_0036b18_cuts.py`): its 9:16 cuts change in two cases (edge gains index 18; colossi 38 becomes 36 and 39),
and the gate tables follow. The Reel measure test pins raqm's widths: raqm kerns, so WAVY VAULT measures 545 px against
the 552 px libass draws (the ASS header does not ask libass to kern); every other sample is within 2 px.

## Quality-fixes programme (2026-11-05 on)

The project quality review (`docs/reviews/project-quality-review-2026-11-05.md`) is worked row by row; the ledger is
`docs/reviews/quality-fixes-2026-11/STATUS.md` (one commit per row, a regression test that fails on the old code, a
reproducible `measure.py`, an independent review, the full suite before every push). Phase 0 (stop-ship, 12 rows) is
complete: QF-49 identifier scrub, QF-46 illustration spend cap, QF-47 docs live vs dry run, QF-48 no self-update in
production, QF-40 tier-gated corroboration, QF-41 Gemini roles and the injection corpus, QF-42 credit lines, QF-43
publishable-field allowlist, QF-44 SSRF guard, QF-50 local face verification, QF-51 config drift, QF-45 learned-rule
grammar. Phase 1 (output defects) is built in part and lands next. The design freeze loop (panel >= 9.5, no further
gain, two clean adversarial rounds) resumes after Phase 4b, on the post-QF code.

## Operator decisions, 2026-11-05 and 2026-11-06

- Text: raqm layout everywhere (FriBiDi on the dev PC and CI; a go-live step for the VM). Caption small caps stay for
  now; faux caps are forbidden by the review addendum, so QF-21/QF-87 must use true OpenType `smcp` or a derived font.
  Caption widths are measured without kerning (as libass draws them).
- Chapter cards: all drawn; long titles wrap.
- Delivery: remove grain from the master; hard cuts inside a Reel, with a short dissolve only at act changes.
- Surfaces (QF-80..83): remove the Story poll frame; pause frames 1-2 until followers reach 1,000 and Story reach
  reaches 20% of Reel one-day reach for 10 episodes. Render carousels only for booked slots and collect insights;
  keep the Facebook gallery off until Facebook Reel reach reaches 50% of Instagram. Render final and grid covers
  only; freeze the cover bandit until a cover-specific signal exists. Default Shorts on, drop the dead thumbnail
  call, and provide the optional one-second FOLLOW overlay on the loop bridge.
- Voice (QF-78): use the designed narrator in `data/voice/voices.json`, validate the configured identity, and record
  the three-script blind listening review and approved sample hash. This does not authorise live provider calls.
- Grade and fonts (QF-86/QF-87): remove the formula LUT and its generator; grade photos only in 16-bit, with black
  at zero; remove broadcaster names used as standards. Remove unused Bebas and duplicate Montserrat; omit the
  grid masthead or keep its cap height at least 9 px. Use actual OpenType features with accents preserved.
- Topics (QF-73): require sources, lint hooks, remove the duplicate corrections file and viral hashtag, add
  underrepresented regions, and mark sensitive topics so they cannot run on consecutive days.
- Operations (QF-48): production self-update stays refused; dependency checks are report-only there and releases
  follow the approved signed-tag deployment path.
- Maps: SITE_HINT_KM stays 2 km (marker snaps onto the topic country); a proper name stays whole on a timeline and the
  span adapts (no over-spine placement); disputed territories get neutral styling (no name or highlight); an enclave
  missing from Natural Earth beyond 2 km gets no home name (logged); the dot-on-leader marker is allowed but each
  place uses one marker at both sizes; a home name that partly repeats the title switches to the sovereign's name.
- Dependencies approved: Shapely, pyproj, fontTools and the addendum list; ruff 0.17.3 as a dev tool. QF-09: edge-tts
  and pip-audit stay runtime dependencies; requirements.txt stays the production install source and must match a
  hashed uv.lock checked in CI; only ruff goes in a dev group; the deployer is unchanged.
- Privacy (QF-50): local face verification by default; ambiguous art crops go to the Gemini paid tier only with
  `FACE_VERIFY_CLOUD_AMBIGUOUS=true` (off until the operator confirms paid billing and Google's data-processing terms;
  fails closed); the origin/main test `test_kailasa_stone_carving_not_blurred` was changed with written approval (the
  photo's two living visitors are blurred).
- Process: landing one row at a time with the full suite (no batching), at `-n 12 --dist worksteal`; later phases may
  build while earlier rows land, landing in ledger order. No Defender exclusion (measured 2-3% of CPU).

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
