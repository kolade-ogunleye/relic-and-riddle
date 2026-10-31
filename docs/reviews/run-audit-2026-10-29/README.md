# Run audit, 22-29 Oct 2026 (production first, then development)

Read-only audit of every run since 22 Oct (29 production, 43 development): runs, approvals, findings and
proposals tables; each run's run.log, qa_report.json, release_report.json, picture_lines.json, timings.json,
cards.jsonl, llm_calls.jsonl; the .picture_text.json and picture_fit.json caches; logs/pipeline.log. Code
locations are at 47d6168 (v1.0.0-rc72). Production: MINOR_FINDINGS_BLOCK_RELEASE=False, FREE_TIER_HEAVY=False;
development: both True.

TP = a real quality problem the checks caught; FP = a check that blocked or flagged good content.

## Ranked root causes

1. **FP, blocking: scale graphic after the hook fails `semantic_desync`** (prod run_derinkuyu_underground_city_1793303149,
   rc72). The release gate blocked the review (no Approve button): "Graphic 'graphic_scale.mp4' missing narration
   keywords ['280','85','city','depth']". `lock_narrative_graphics` (src/scene_planner.py:306-307) puts a graphic only
   the hook names on the beat after the hook; the QA keyword window starts 2.5 s before the cut (src/video_qa.py:612-615),
   so "city" (0.9 s) and "280" (2.3 s) fall outside it; the hook exemption (src/video_qa.py:633) covers only the offset
   check. rc72's short hook moved the number into the hook, so this recurs for any topic that opens on its number. The
   card also lists the non-blocking minor finding among the blocked reasons (src/release.py:126 passes `found`, not
   `blocking`) and shows the raw checker string.
2. **FP plus a cache defect: picture-text verdicts cached without a box or edge.** Derinkuyu's rolling-stone-door
   photos (05, 11) are cached `{"text": true, "edge": "none", "box": []}` and set aside in every run since; new looks
   degenerate ("Well alright, let's wrap this up...", "Done well done perfectly done...", prompt echoes, "Wait, make
   sure no control tokens..."). `PictureVerdict` (src/beat_images.py:48-62) defaults edge/box/object, `text_edge` is a
   free string, reasons are unbounded; verdicts are cached at screen 3 and never re-looked (src/beat_images.py:771-786);
   rc72's `verdict_reason` filter (src/llm.py:473-492) misses these and is not applied to cached reasons.
3. **TP verdicts, wasted time: photo searches widen to the whole category.** stills_graphics_sourcing 767 s and 805 s
   on prod Derinkuyu, 2,416 s on dev Nazca. Beat 10 searched for the church exterior, widened to
   `incategory:"Underground City of Derinkuyu"`, and set aside 20 underground photos in a row (about 5 min), so the
   ending fell back to the turnstile photo. Widening can return the bare category (src/commons.py:454); candidates
   count as relevant on the topic name alone (src/beat_images.py:423); looks run serially, and one failed billed look
   costs about 250 s (two 120 s timeouts plus a pause, src/gemini.py:204-206, src/free_llm.py:122-137).
4. **FP chain: emoji rule, copy edit, number check** (prod 1793300709, rc71). The emoji count (src/script_engine.py:1796)
   counted the credit lines' 📷/🎬; the copy edit stripped them, so the lines stopped being recognised as credits
   (src/credits.py:242-245, src/trace_check.py:78-83), and `numbers_in` (src/fact_sheet.py:115-123) then read photo
   titles (9843, 9913) and Commons curids (3.10746e+07) as untraced figures.
5. **FP that cost a stat card: the number-card check reads digits only.** "number card: 8 (Recorded subterranean
   levels) is not a figure the narration says" while the narration says "eight to 18 levels" (src/trace_check.py:121-131);
   the revision dropped the card.
6. **FP, fixed (rc66/rc68): "Audio changed after rights evidence was recorded" on regenerated runs.** Confirmed on dev;
   not yet exercised on prod.
7. **FP, fixed (rc66): minor findings blocked release on prod.** Severity gate in src/release.py:121; not yet exercised
   on prod. On dev (blocking by the operator's setting) 3 of 5 were real, 2 were false and are fixed (a3a87bd, b8b7d0c).
8. **TP, fixed (rc66): Pantheon photos under "modern infrastructure" lines** (prod Roman). The line-picture check works.
9. **TP checks that ended runs: script length and poll at the last attempt.** rc70's pace-based window and d94db72's
   54.5 s ceiling confirmed; recommendation: a draft of 51.6-53 s gets one trim attempt and then passes.
10. **Partly FP: rhetorical lines forced to "untraced"** ("Centuries of everyday life hid the massive world waiting
    below."): src/trace_check.py:134-156 treats any "centur..." word as an empirical claim.
11. **Provider reliability.** Every light call hits the free 429 first and then the billed key; the billed key timed
    out 7 times on 29 Oct and once returned no output. No cooldown after a free-tier 429 (src/free_llm.py:201-214).
12. **TP handled correctly:** too few strong photos (Greek Fire, Tunguska), the second fact checker (Antikythera,
    Paracas, Damascus), dropped hedges (Derinkuyu levels, Amber Room tonnes), privacy blur, cover overlap, accession
    labels.
13. **Telegram card defects:** a one-item list says "the full list is in the reply under this card" twice; the
    progress card's estimate said about 21 min for a 56 min run.
14. **Housekeeping:** test-fixture folders from 25 Oct in prod's output/ (run_f, r_prog, review2, ...); the ledger's
    "ready" proposal for run.failed.voice_length is already fixed by d94db72.

Also found on prod run 1793303149 (fixed in the same release): the film-print LUT crushes blue so lamp-lit caves read
neon lime (docs/reviews/film-grade-2026-10-29/); the map was rendered but not chosen; the pre-voice rewording was
refused because the credit block was appended twice; the end card's share line.

## Recent fixes in real runs

| Commit | Change | Status |
|---|---|---|
| rc70 6e8e68e | length judged on the run's draft pace | working (limited data) |
| rc70 5c5f3bb | no double resume/post; media cards edited as media | not exercised |
| rc71 ae4ae4f | caption first-line break; short copy edit | break confirmed 3x; copy edit causes item 4 |
| rc72 4efeee8 | place named early, short hook, no asides, headline figures | confirmed |
| rc72 4efeee8 | map used | not working (fixed in the next release) |
| rc72 4efeee8 | pictures before the voice | exercised (2 of 5 lines; about 5 min) |
| rc72 4efeee8 | verdict_reason self-talk filter | ineffective (item 2) |
| rc72 0e7ac9e | colour balance | ran on 15 photos; spoiled by the LUT |
| rc66 d94db72 | voice kept up to 54.5 s | confirmed |
