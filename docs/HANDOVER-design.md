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

## Open operator decisions (freeze step 2, 2026-11-04)

- Text layout engine: the cards rework forced Pillow's BASIC layout on every build ("product choice P1"). It was an
  agent's spec, not an operator decision, and it would change production text layout, so it is **not** committed.
  Options: approve BASIC everywhere, add libraqm to dev, or pin test values for both engines.
- Chapter cards: draw all of a video's chapter cards or none (3 of 5 Richat act titles are over 32 characters).
- Caption small caps: Anton has no true small caps (faux caps are 18% thinner).

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
