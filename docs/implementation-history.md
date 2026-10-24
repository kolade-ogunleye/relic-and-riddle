# Pipeline completion record

The working tree contains substantial earlier uncommitted work. Preserve it and assess
the final behavior rather than assigning authorship from a diff against HEAD.

## Baseline

Before corrective implementation, all 313 tests passed. Direct runtime probes nevertheless
showed that a missing master received 100/100 certification, missing assets disappeared
from provenance, and changing the master did not invalidate its ledger. These probes
establish why a passing suite alone was insufficient.

## Corrections implemented

- Missing masters and failed audio/frame measurements cannot receive certification.
- Provenance resolves exact relative paths against explicit roots, rejects ambiguous
  matches, canonicalizes paths, and records missing files as failures.
- File verification recomputes checksums, including master, narration, bed, assets, and
  available credit files. Licensing is separate from file identity.
- Unknown licensing and privacy checks remain unverified; unconditional standards
  compliance and default successful quality certification have been removed.
- Deep-dive fact verification fails before synthesis when verification fails.
- Thumbnail copy uses neutral text instead of a fabricated numeric fallback.
- Deep-dive approval no longer records a publication that never happened.
- Scheduling and publication require a fresh release report with measured dimensions,
  duration, channel count, sample rate, loudness, true peak, packaging, provenance,
  rights coverage, factual verification, and visual validation.
- Delivery profiles now supply render dimensions and mastering targets, and reel
  production reads the profile's music looping and sample rate settings.
- No-op loudness correction and metadata-only temporal edits no longer claim remediation.

## Remaining completion work

1. Inventory and measure existing representative runs; save baseline artifacts.
2. Complete source-to-derived asset lineage, generated-asset declarations, narration and
   music rights evidence, and attribution checks. A nonempty license string alone is
   insufficient proof of clearance.
3. Bind fact verification and review evidence to input fingerprints; cover all public
   surfaces, including graphics, thumbnail copy, and localized output.
4. Complete caption/font/bounds validation and ensure skipped checks produce an explicit
   unverified state. Review heuristics for false positives before relying on them as gates.
5. Complete per-stage checkpoints and dependency invalidation for scripts, paid audio,
   generated media, rendering, packaging, and publication.
6. Finish capability-aware platform profiles, including distinct reward-program duration
   checks and truthful unsupported publishing operations.
7. Consolidate encoder selection, benchmark representative graphics, and implement
   bounded concurrency and cache invalidation.
8. Render and inspect a representative sample, one full Derinkuyu episode, and a varied
   small batch. Save measurements, preview frames, and release reports.
9. Update operations documentation and perform a requirement-by-requirement completion
   audit. Do not label the full plan complete until these artifacts substantiate it.

Localization, automated experiments, and community automation remain subsequent releases
as specified in the plan; do not present their schemas as implemented integrations.

## Validation of the first corrective pass

- Full suite: 321 passed in 59.33 seconds after release gating and negative-case fixes.
- Additional real FFmpeg integration check: stereo music with distinct left/right tones
  retained two independent channels after the narration/ducking/mixing chain.
- Focused integrity suite, including that measured audio check: 8 passed.
- Full episode and varied batch validation remain outstanding; these results do not
  certify final audiovisual quality or completion of the complete plan.

## Second corrective pass

- Saved measured baseline stream metadata, duration, loudness, and true peak for three
  existing episodes in `docs/baseline-measurements.json`.
- The 242.433-second Derinkuyu master measures -14.0 LUFS and -1.4 dBTP, exceeding the
  configured -1.5 dBTP peak limit. The Roman concrete and Kailasa shorts measure -14.1 /
  -14.0 LUFS and -1.8 dBTP. These are baseline observations, not newly validated renders.
- Added atomic checkpoints that require identical input fingerprints and matching
  output hashes. Reel script and narration reuse now consume these checkpoints.
- Verified scripts use a separate immutable snapshot so final packaging metadata does
  not inadvertently invalidate paid generation inputs.
- Added source identity sidecars for new upscaling, privacy transformation, and parallax
  outputs. Provenance records and verifies their recursive source chains.
- Derived assets can inherit source credits; disallowed or incomplete license evidence
  remains unverified. Generated graphics, music, and voice clearance still need work.
- Full suite after checkpoint integration: 324 passed in 84.92 seconds. Subsequent
  lineage changes have their own focused validation; do not treat that earlier full
  suite result as validation of later modifications.

## Third corrective pass

- Visual audit results include the master checksum; release checks reject certification
  from a different render. A regression test covers replacement of the master.
- Encoder probing now lives in `src/encoding.py` and is shared by composition and graphics.
  Tests cover hardware probe caching and CPU fallback.
- Standard Reel profiles no longer claim Creator Rewards compatibility; a separate
  extended vertical profile expresses a 62-68 second editorial duration without claiming
  account eligibility or publishing support.
- The production limiter ceiling is now 0.80 to leave additional AAC peak headroom.
- Saved a separate audio-corrected full Derinkuyu copy in
  `output/validation/derinkuyu/derinkuyu_master.mp4`. Measured encoded output: -14.4 LUFS,
  -1.8 dBTP. The original video stream was retained. This validates audio correction,
  not an end-to-end newly rendered episode.
- Inspected a contact sheet at 3, 40, 100, and 180 seconds. Text is inside the frame, but
  three sampled scenes use the same cave image. Visual variety and full package evidence
  remain unfinished; do not treat this audio-only correction as full rollout validation.

## Fourth corrective pass

- Added narration generation records bound to audio identity and script fingerprint.
- Library music selection now carries the complete selected-track metadata into the
  bed's rights record, with source hashes and required attribution. Music supplied by
  an external generator remains unverified without music-specific rights evidence.
- Release validation checks audio rights and attribution; the provenance ledger hashes
  those evidence files and the underlying music sources.
- Successful factual review now records a fingerprint of public copy and the research
  file hash. Release requires both to remain current. YouTube titles and descriptions
  are included in the reviewed surfaces, with chapter timestamps excluded as navigation.
- Final public copy is rechecked when it differs from the verified script package.
- Deep-dive script, narration, music bed, render, and proxy reuse now require checkpoints
  rather than file existence. Music attribution is restored from evidence when reusing
  a bed. Render fingerprints include source files, durations, emphasis, and encoder code.
- Focused checks: 57 passed for factual/audio evidence and affected release/script paths;
  24 passed for deep-dive, checkpoint, and coordinator checks before the final bed change.
- Full end-to-end newly rendered episode and varied batch evidence remain outstanding.

## Fifth corrective pass

- New procedural graphics record their generator, exact inputs, source media, and output
  identities. Pure original drawings and photo composites have different rights states.
- Map/route declarations retain Natural Earth dataset hashes and source-specific terms
  links. Terms were checked against the publisher's page on 2026-10-16; this covers the
  raster/vector data, not unrelated imagery or arbitrary graphics.
- Found and removed unsupported defaults in defense/ventilation annotations (weight,
  population, attack use, and absolute breach claims). Defaults now describe an
  illustrative mechanism. Explicit supplied annotation text is included in fact review.
- Regressions cover pure drawing versus photo composites and custom diagram claims
  appearing in the reviewed public surfaces.
- Full package and rollout validation remain unfinished; generated declarations do not
  establish factual accuracy or visual quality by themselves.

- Validation after the diagram-label changes: full suite 338 passed in 91.52 seconds.
  Subsequent map-source declarations passed 17 focused evidence/release tests.
- Added a local `python -m src.release` command and operational guidance in
  `docs/release-validation.md`. It inspects artifacts and writes evidence without
  production, model calls, or publication.

## Sixth corrective pass: fresh validation artifacts

- Added `scripts/prepare_validation.py`, a non-publishing package preparation command.
- The first fresh draft failed on unsupported history and missing qualifications. The
  generation prompt itself required invented story beats and discouraged source caveats;
  those conflicting instructions were removed from production.
- The second draft had one unsupported prominence claim. Removed that sentence without
  adding replacement claims, checked the edited act, then checked the entire package.
- Fixed chapter-navigation counters being treated as historical numerical claims, while
  preserving detection of real numbers inside the chapter wording.
- `output/validation/fresh_derinkuyu_v3/script_metadata.json` now records a successful
  real model review: all 17 identified claims trace to the unchanged research file.
- Generated fresh licensed Azure narration: 199.776 seconds, 456 timed words. Generated
  a bed from an approved local CC0 track and recorded its source and audio rights evidence.
- Five first-round image candidates were visually rejected: they show a surface church.
  A second search found eight CC BY candidates for the underground city. The first
  downloaded interior was inspected and accepted as relevant; remaining downloads are
  still running in exec session 52328 as of this record. Re-poll the live handle before
  deciding whether to resume or restart this work.
- Generated a 15-second sample exercising narration, bed, subtitles, map, photo, and
  door illustration. Measured -14.4 LUFS / -1.9 dBTP. Artifacts are under
  `output/validation/fresh_derinkuyu_v3/validation_sample.mp4` and `sample_contact_sheet.jpg`.
- Visual inspection found a map-country label in the subtitle band. Fixed country-label
  placement across the zoom; that map and sample need regeneration before final approval.
- Widescreen captions now use bundled Montserrat with measured fitting rather than
  relying on a system Arial installation.
- Added a shared encoding semaphore to bound concurrency across nested renderer pools.
- Full suite before the map-label fix: 339 passed. After it: 12 graphics tests passed.
  Encoding concurrency/fallback checks: 3 passed. Full episode, updated sample, varied
  batch, benchmark, and final completion audit remain outstanding.

## Seventh corrective pass: complete episode and batch validation

- Replaced the stalled batch downloader with bounded retries and incremental source
  evidence. Stopped its old live process before resuming, preventing concurrent writes.
- Downloaded four underground-city photos; four other requests returned explicit 429
  responses. Inspected the available photos and excluded one near-duplicate view.
- Fresh Derinkuyu master completed: 199.776 seconds, 26 cuts. All 18 implemented release
  checks passed. Encoded output measured -14.1 LUFS / -1.7 dBTP. Contact-sheet inspection
  confirmed map/subtitle separation and multiple source views. Human listening review
  remains separate from these measurements; no publication or approval messages sent.
- Full evidence lives at `output/validation/fresh_derinkuyu_v3/`, including master,
  timeline, captions, source/rights records, thumbnails, review frames, and release report.
- Added mixed-content CPU/NVENC benchmark tooling. Six-second sample: configured CPU
  7.350s, SSIM 0.959472, 5,933,970 bytes; NVENC 2.373s, SSIM 0.959521, 4,508,285 bytes.
  This is about 3.1x encoder speed on this sample, not a whole-pipeline speed guarantee.
- Added atomic publication claims, confirmed-publication idempotency, and unknown-outcome
  handling on unexpected upload exceptions. Tests cover concurrent claims and retries.
- Fixed source-credit path normalization and attribution coverage in caption/description.
  CC BY evidence with unknown authors remains unverified.
- Added Reel music-bed checkpoints, selected audio-source invalidation, and raw provider
  score identity records. Seamless looping is now an explicit opt-in instead of a default
  promise for every Reel.
- Both Roman concrete and Kailasa fresh scripts passed factual and independent raw-quote
  checks. Narration durations: 49.992s and 47.016s, respectively, through Azure.
- Roman's first render correctly flagged an archival image with incomplete rights
  evidence. Re-rendering with a documented CC BY replacement. Kailasa is also rendering.
- Visual planning/review now honors free-provider mode instead of always calling a
  configured but exhausted Anthropic account. Relevant provider/QA tests passed.
- Full batch results, final regression suite, and requirement completion audit remain
  outstanding. Active render sessions at this entry: Roman 13956 completed (needs review),
  replacement Roman: use the actual tool-returned handle from the conversation.
  Kailasa 29013 was live at last observation.

## Eighth corrective pass: batch results and final-audit findings

- All three fresh episodes now pass their implemented automated release checks:
  Derinkuyu documentary, Roman concrete Reel, and Kailasa Reel. Short-form durations
  are 49.992s and 47.016s; visual contact sheets were inspected for packaging and layout.
- Fixed duplicate-credit normalization when a run snapshot and topic library referred to
  the same image with absolute versus relative filenames. Different-directory collisions
  remain rejected; regression tests cover both identity and publication attribution.
- Replaced Roman's archival photo with an existing CC BY photo after checking that the
  archival Commons page lacked complete source-country public-domain evidence.
- Batch validation exposed Derinkuyu-only semantic audit keywords. Audits now read each
  graphic's declared place and numeric values. A relevant phrase within a cut is treated
  as synchronized; unrelated topics/figures are still rejected. New tests cover these cases.
- Full current suite: 347 passed in 54.37 seconds.
- Benchmark results are at `output/validation/encoder_benchmark/benchmark.json`.
- No active validation render or download process remains from this pass.

### Outstanding final-audit items

1. Scale graphics add landmark reference heights that were not included in the fact
   review's text surfaces. The observed Kailasa comparison is the Statue of Liberty,
   305 ft / 93 m. Verified against NPS statistics, but its source and value must be
   recorded in factual review/provenance rather than remaining implicit in renderer code.
2. Reference checks gathered: NPS ground-to-torch 305 ft 1 in / 92.99 m; Parliament
   architecture page Elizabeth Tower 96.3 m / 316 ft; CDC current U.S. adult men's mean
   height 68.9 in (2021-23). Egypt's ministry supports Great Pyramid original height
   146.5 m, not the renderer's unsourced current-height comparison. Retain explicit
   qualifiers and source URLs when integrating reference facts.
3. Manual copy inspection found Roman's caption claim 'outlasts our best engineering'
   had been exempted as rhetorical framing. This is an empirical performance comparison,
   so that exemption must be tightened and the affected copy rechecked. Also verify
   that lab findings about recreated concrete are not generalized to all ancient sites.
4. Complete remaining stage checkpoints/invalidation (especially Reel rendering and
   packaging), capability-aware publishing/profile validation, font/resource evidence,
   and operator reconciliation for uploads interrupted after an atomic claim.
5. Rewrite the completion checklist as a requirement/evidence matrix, refresh affected
   artifact reports after final changes, and perform the full completion audit. Passing
   automated batch gates alone does not establish these outstanding requirements.


## Continuation checkpoint (2026-10-16)

- Previous validation jobs are terminal; Roman and Kailasa have current strict factual receipts and matching narration.
- Derinkuyu independent raw-evidence review failed on unsupported statements about 18 levels and shallower figures. Its package must be corrected, rechecked, and narration regenerated before a new render.
- Layout remediation now leaves encoded-asset defects blocking and does not mutate global calibration or report an unapplied repair. Updated regression assertions cover this behavior.
- Documentary validation now requires both trace review and independent raw-evidence review before recording a new receipt.
- Final render refresh, publication interruption recovery, and completion evidence matrix remain outstanding. The job is not complete.
