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
| Reel covers, grid tiles, YouTube thumbnail | Round-4 work committed | Freeze panel |
| Captions 9:16 / 16:9 | 80 px committed | Proper-noun phrases are split across bursts ("RASHTRAKUTA / DYNASTY"); keep names together, shrinking if needed |
| IG carousel, FB gallery, Stories | Committed | Freeze panel |
| Reel loop bridge, 16:9 end screen | Committed; the audio seam is exact (fcb822e) | Freeze panel |
| Then-and-now | Committed | Stepped label shadow on near-white photos |
| Photo callouts | Redesign committed; unattended runs get none until the accuracy audit exists | Needs real photos and an accuracy audit |
| First frame, chapter card, myth-vs-fact (`src/design_cards.py`) | Committed, 9/10 on review (8693156) | Long 9:16 myth: keep the source line, shrink to a 76 px floor or add a line, never drop it |
| Quote cards | Held (`QUOTE_CARDS_ENABLED=false`) | Enable only with signed records and a real checker key; speaker, work and date come from the signed check record only |
| AI illustrations | Built | Needs a Gemini key and Tesseract to judge |
| Maps, timelines | In progress in the cloud session (round 8) | — |

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
