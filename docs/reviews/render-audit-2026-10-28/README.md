# Render and quality failure audit, 28 Oct 2026

Of the 18 production runs since 24 Oct, 3 were published. One false check blocked every regenerated run: the voice
and music bed rights evidence. Real picture problems and photo pools the video could not use stopped most of the
rest. All the causes found are fixed on dev, with tests. None of it is released yet (production is on rc65).

Sources: a read-only export of production's database, run folders and log (taken 28 Oct, about 11:20 UTC), and the
same from dev. Four auditors worked in parallel: production runs, dev runs, the quality gates, and the findings and
proposals. Seven engineers then fixed the confirmed causes. An independent reviewer and a replay of the real
failures checked the result.

## Why each production run was not posted

| Run | Outcome | What stopped it | Was the check right? |
| --- | --- | --- | --- |
| Kailasa 24 Oct | published | none | yes |
| Antikythera 1 (25 Oct) | rejected by you | Cut-off Story text and a repeated "By one estimate". The checks found both but only noted them. | missed (detected, not acted on) |
| Antikythera 2 | failed | Voiceover 52.9 s, ceiling 52 s | ceiling right; the length estimate was wrong |
| Antikythera 3 | failed | Second fact checker: "pulled specifically from the Aegean Sea" | yes: the reviser added it |
| Antikythera 4 | regenerated | Weak images; museum placard rated minor | partly missed |
| Roman concrete 1 to 3 (26 Oct) | 2 regenerated, 1 rejected | Pantheon photos under "modern concrete" lines (major) | yes. Each regeneration also kept the script, and runs 2 and 3 were blocked by the false rights check too |
| Göbekli Tepe | published | Reel upload timed out; the retry posted it | n/a (Story and carousel were re-booked automatically) |
| Damascus steel | published | Facebook captions HTTP 400 (fixed earlier) | n/a |
| Greek fire | failed at start | 4 of 6 approved photos usable (2 topic, 4 context, at most 2 context count) | the rule was right; the set should never have shown as approved |
| Richat 1 | regenerated | Minor: "extremely blurred background" on our own numbers card | **false** |
| Richat 2 and 3 | regenerated, rejected | Only the rights check | **false** |
| Amber Room 1 | regenerated | A grey oval left by the face-privacy blur | yes (our own blur's defect) |
| Amber Room 2 | rejected | Only the rights check; the same photo was reused | **false** |
| Tunguska | failed at start | 3 of 10 photos usable (1 topic, 9 context) | rule right; the pool was thin and shown as approved |
| Hal Saflieni (27 Oct) | failed | 3 drafts: the length guidance swung between too long and too short, and the last failed only on the poll rule | **false** (both) |

No run failed in the render, encode or loudness stages on production. Masters measured about -14.1 LUFS and
-1.9 dBTP, with A/V drift of 17 ms or less.

## False positives fixed

1. **Rights evidence on regenerated runs** (blocked 5 production runs; for 3 it was the only blocker).
   - A regeneration copies the voiceover and music bed together with their `.rights.json`. That record still named
     the previous run's folder, and the check compares the path as well as the hash. So "Audio changed after rights
     evidence was recorded" appeared although the bytes were identical (Amber Room: same hash `81b50335…`). On
     production, "needs review" blocks posting.
   - Fix: `audio_provenance.rebind_rights` rebinds the copy when its hash still matches. The original path is kept as
     `carried_from`. A file whose bytes differ is never rebound, so it still fails.
2. **A minor finding blocked release even with `MINOR_FINDINGS_BLOCK_RELEASE=false`.**
   - The release gate required the audit's "certified" flag, which needs a score of exactly 100, and every minor
     finding costs 5 points.
   - The gate now decides by severity: critical and major always block, minor only when the setting says so, info
     never.
3. **Our own graphics judged as blank photos** (Richat beat 8; Odessa beat 6 on dev, twice).
   - Before, only the judge's prompt excused the designed backdrop. Now the code also sets aside a minor finding that
     only says a graphic's backdrop is blank, dark or blurred.
   - A finding about the card's own content is never set aside: a missing number, a map not drawn, text cut off, a
     blank card. Neither is any major finding.
   - The judge is also told how each beat was made: our graphic, archive film fitted over a blurred copy of itself,
     AI motion, privacy blurs applied, or an archive photo with its caption.
4. **Script length guidance contradicted itself** (Hal Saflieni: 103 words gave "85-90", then 86 words was too short).
   - The ceiling and floor were judged at different paces, but the suggested range was never checked against both.
   - There is now one window that fits both limits, used for every instruction. Hal's 103-word draft would now be told
     92-96 words.
5. **The poll rule was too literal.**
   - "Was the acoustic design intentional?" with "Deliberate design / Accidental resonance" was rejected, which cost the
     last attempt.
   - The code now rewords such a poll to "Deliberate design or accidental resonance?" with no writer attempt.
     "Yes, easily / No chance" now passes.
   - Structure and length problems are reported together, so one rewrite fixes both.
6. **Photo-text screen**: museum labels and accession numbers are now named in the prompt, and the screen looks at
   1024 px (was 512). Printed text in a band at the edge is cropped away (at most 15% of a side) instead of the whole
   photo being dropped. The crop keeps its lineage, so the photo's credit and rights still resolve. Evidence:
   `docs/reviews/text-crop-2026-10-28/`.

## True positives: root causes fixed

1. **The face-privacy blur created the artefacts** (Amber Room grey oval; dev Roman "fingers blocking the lens").
   - Gemini's face boxes were accepted without verification. A head-and-body box was blurred whole, and the blur
     averaged to one flat colour.
   - Now every box is verified. A tall box keeps only its head. The blur keeps colour and texture, with a feather
     sized to the face. Close-up and side-by-side faces are still blurred.
   - A face whose check cannot run is blurred anyway, and that result is not cached.
   - Evidence: `docs/reviews/privacy-blur-2026-10-28/`. Roman photo 19's largest blurred area went from 7.1 to 1.45
     tall per unit wide. A synthetic face's detail fell 149 times.
2. **Photo readiness disagreed with the video's own rule** (Greek fire, Tunguska).
   - Approval counted "yes" plus "partly" photos, and readiness counted every photo. The video uses topic photos plus
     at most 2 context ones.
   - Now one rule decides approval, readiness, the scheduler, `/photos` and the cards, for example "4 of 5 usable".
     A short set is topped up, and the scheduler skips a topic it cannot render and says why.
3. **Regenerate started before your reason arrived** (Roman concrete: tap 14:22:32, run 14:22:45, your words
   14:22:52).
   - After "Other", it now waits for your words, at most 90 s from the tap. The card then says what will be kept and
     what redone.
4. **Approving a blocked version**: the auto-approve flag and the CLI `review approve` / `review publish` now apply
   the release gate as the Telegram card does. A blocked version is never recorded as approved. The blocked card now
   lists the blocking checks first.
5. **Photo credits** (dev Wieliczka, Trepanation).
   - A Commons artist field holding several lines, or only a user notice, broke the caption credit and the facts
     check.
   - Wellcome photos had no Artist field and were credited "Unknown".
   - Authors are now read from Attribution, Credit and the uploader link, and every credit is one line. A CC BY photo
     with no attributable author is refused when it is fetched.

## Findings and proposals

None of the 13 production proposals was acted on, and several were built from mislabelled findings.

- **One defect, five proposals.** The Roman concrete mismatch was filed under 5 classes and became 5 proposals.
  Self-healing lines are now classed by what they say, not by beat number. A repaired beat is not counted.
- **Mislabelled classes.** Second-checker rejections were filed as "unavailable". Failures were grouped by exception
  type, so voice length and a correct guardrail rejection shared one class. Both are fixed.
- **Bad regexes.** "fact" matched inside a repetition hint, and "cover" matched "covering". Both are fixed, with a
  table test built from the real ledger texts.
- **Old rows.** Dev's 63 "traced:" fact-check rows were passes, counted as problems. They are now retired: kept, but
  not counted. The same row is never filed twice.
- **Repeat proposals.** A declined class was proposed again the next night. Now only findings after your decision
  count.
- **Paid key used.** The nightly rule draft used the billed key on 27 and 28 Oct. It is now on the free quota only,
  on its own thread, so a run beside it keeps its paid fallback.
- **Clearer cards.** A release-gate proposal confirmed by another check reads "gate correct, fix upstream".
  `pictures.licence_excluded` reads as your setting at work, not a defect.

## Verification

- **Full suite.** It passes in normal and deployer mode on the combined change. The final run after the review fixes
  is recorded in the commit messages.
- **Replay of the real failures through the new code, offline.**
  - All 8 cases pass.
  - Rights: rebound copies pass; changed bytes still fail.
  - Visual gate, across 13 real failed runs: with the minor setting off, the 5 with major findings still fail and the
    8 minor-only runs pass.
  - Backdrop: the real Odessa and Richat findings are set aside; all 14 other findings are kept.
  - Hal Saflieni: the poll is repaired, and every length range fits both limits.
  - Photo readiness: Greek fire reads 4 of 5 and Tunguska 3 of 5.
  - Ledger re-filing gives the right classes and changes nothing on a second pass.
  - Privacy: the measurements reproduce byte for byte.
- **Independent code review.** It found two privacy regressions in the first version: close-up faces were dropped,
  and a failed check dropped a face. It also found a backdrop rule that was too broad, and three smaller defects. All
  are fixed and tested.

## Decisions for you

1. **Minor findings.** Should minor findings from the vision check keep blocking release
   (`MINOR_FINDINGS_BLOCK_RELEASE`, currently on in both environments)? With it off, they show on the card and you
   decide. Every minor-only block in this audit was a privacy-blur or backdrop artefact, and those are now fixed at
   the source.
2. **Voiceover ceiling.** The post-voice ceiling is 52 s, while the release limit allows a master of 58 s (about 55 s
   of voice plus the end card). All three production overruns (52.9 s, 53.7 s, 54.4 s) would have passed release.
   Keep 52 s, or raise it toward 54.5 s?
3. **Trepanation's photos** (15, all CC BY) still carry the artist "Unknown" in their saved credits. The fix applies
   when they are fetched again.

## Still open before the next render

- **Picture against narration before the render.** Roman concrete asked for pictures of modern concrete that no
  licensed source had. The planned fix is to judge each beat's photo against its line on the plan, before the render,
  and send a line with nothing to show back to the writer.
- **Repairing minor findings.** "Text cut off" on a Story or cover, and badge overlap, are found but only noted. They
  preceded 3 of 3 rejections. These need an automatic re-layout, or a block.
- **The beat-download path** still drops pictures with text at 512 px instead of cropping them.
- **Re-checks on the next run.** Each topic's photos get the 1024 px text screen once, one light-model call per photo.
  Photos already blurred are verified again once.
