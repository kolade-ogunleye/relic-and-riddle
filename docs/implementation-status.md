# Implementation completion and evidence

This record covers the corrective production plan. Earlier findings and intermediate
results are preserved in [implementation-history.md](implementation-history.md).
The original audit's blanket standards certifications, revenue forecasts, and claims
of platform eligibility are not acceptance criteria.

## Requirement and evidence matrix

| Requirement | Implemented behavior | Evidence |
| --- | --- | --- |
| Baseline inventory | Measured three existing episodes before correction | `baseline-measurements.json` |
| Fail-closed quality checks | Missing masters, measurement failures, stale review hashes, missing composition inputs and unresolved rights block release | `tests/test_release_integrity.py`, `tests/test_orchestrator.py`, `src/release.py` |
| Factual grounding | Trace review plus independent raw-quote review; review binds public copy, research, rules and scale-reference data | `src/trace_check.py`, `src/fact_evidence.py`, `tests/test_empirical_claims.py`, `tests/test_fact_evidence.py` |
| Public surfaces | Narration, titles, captions, descriptions, comments, thumbnails, stories, carousels and graphic annotations enter review | `src/trace_check.py:surfaces`, final `script_metadata.json` files |
| Source identity and rights | Exact paths; missing/ambiguous sources fail; recursive transformation lineage; actual licenses and required attributions retained | `src/credits.py`, `src/lineage.py`, `tests/test_lineage.py`, `tests/test_attribution_evidence.py`, final `provenance.json` files |
| Generated graphics | Exact generator code archived; maps retain Natural Earth sources/terms; comparison references retain primary evidence | `src/graphics.py`, `src/reference_facts.py`, `data/reference_facts.json`, `graphics/source_versions/` |
| Voice, music and fonts | Narration rights bind script/audio; music records actual source files and clearance; font binaries bind OFL notices | `src/audio_provenance.py`, `fonts/license_evidence.json`, `fonts/licenses/`, corresponding regression tests |
| Measured audio | Stereo 48 kHz; final encoded loudness and true peak checked; AAC overshoots corrected with video stream copied | `src/encoded_audio.py`, `tests/test_encoded_audio.py`, final release reports |
| Captions and layout | Format-specific captions, SRT/VTT exports, all-cut frame sampling, collision/bounds checks; unapplied fixes stay blocking | `src/video_composer.py`, `src/video_qa.py`, `tests/test_quality_audit.py`, final contact sheets |
| Delivery and capabilities | Explicit Reel, documentary and extended-vertical profiles; unsupported upload profiles rejected | `src/delivery_profiles.py`, `main.py:publish_run`, `tests/test_delivery_and_visual.py` |
| Resumability | Atomic stage checkpoints bind inputs/output hashes for scripts, narration, beds, graphics, rendering and packaging | `src/checkpoints.py`, `main.py`, `tests/test_checkpoints.py`, `tests/test_graphic_cache.py` |
| Encoding and caching | Shared actual hardware probe with CPU fallback; bounded worker count; content/model-based upscale invalidation | `src/encoding.py`, `src/upscale.py`, `tests/test_encoding.py`, `output/validation/encoder_benchmark/benchmark.json` |
| Publication safety | Fresh release gate, OS-owned upload lock, atomic claim, no repeat confirmed uploads, unknown outcomes block retries | `main.py`, `src/db.py`, `src/publication_lock.py`, `tests/test_publication_recovery.py`, `tests/test_orchestrator.py` |
| Interrupted uploads | Explicit local recovery checks lock ownership, then quarantines abandoned claims for account reconciliation | `scripts/recover_publication.py`, [operating instructions](release-validation.md) |
| Representative validation | Full Derinkuyu documentary plus Roman and Kailasa Reels, packaging, sample, measured release reports and listening reviews | `output/validation/fresh_derinkuyu_v3/`, `fresh_roman/`, `fresh_kailasa/` |

> **Evidence on disk (2026-10-20):** the `output/validation/` folders cited above were deleted
> after this record was written; regenerate them with `scripts/prepare_validation.py` and
> `scripts/render_validation.py` (documentary) or `scripts/prepare_reel_validation.py` and
> `scripts/render_reel_validation.py` (Reels) before relying on them. The quality audit is
> `QualityAuditor.certify` (formerly described as "self-healing"); it reports and repairs nothing.

## Scope and operating limits

The automated gate reports the checks actually performed. It is not broadcast, WCAG,
legal, monetization, or cryptographic certification. File hashes establish recorded
identity; they do not independently establish a license or historical truth.

The configured production uploader handles Facebook/Instagram Reels. Documentary,
YouTube and extended TikTok artifacts are exports; no new uploader, automatic comment
pinning, native poll integration, or account eligibility has been claimed or enabled.
Existing editorial questions/poll-style images remain static assets.

Listening review is explicitly automated and bound to each master hash. The reviewer
receives a 192 kbps MP3 derivative; actual encoded-master loudness/peak measurements
are separate. Publication retains the existing approval workflow. No live publication
or outbound notification was performed during this validation.

Roman uses illustrative photographs of Roman concrete at the Pantheon, rather than
photographic evidence of the Cosa drill core. Diagrams marked illustrative do not claim
to be surveyed archaeological reconstructions. Source libraries remain unapproved for
unrestricted reuse where their review flag is false.

Localization, automated A/B experiments, community automation, new platform adapters,
and formal independent certification remain subsequent work, as originally scoped.
The dirty working tree predates this work and has not been reset or committed.

## Final validation

Final measured batch results and regression evidence are recorded in `completion-audit.json`.


| Final artifact | Duration | Integrated loudness | True peak | Release checks |
| --- | ---: | ---: | ---: | ---: |
| Derinkuyu documentary | 193.536 s | -14.5 LUFS | -1.9 dBTP | 20 passed |
| Roman concrete Reel | 43.267 s | -14.1 LUFS | -1.7 dBTP | 18 passed |
| Kailasa Reel | 47.033 s | -14.1 LUFS | -1.8 dBTP | 18 passed |

The final regression suite passed **358 tests in 106.52 seconds**. New tests cover
live upload-lock rejection, process-exit lock release, abandoned-claim quarantine,
packaging/graphic cache invalidation, and final AAC correction with unchanged video.
All three automated listening reviews passed and identify their exact master hashes.
The Derinkuyu inspection extract is 15 seconds; it is not a separate profile release.

The refreshed six-second mixed sample encoded in 8.294 seconds with CPU x264 slow/CRF18
and 2.918 seconds with NVENC p4/CQ21 (about 2.8x faster on this sample). SSIM against
the lossless sample was 0.942638 and 0.944372, respectively. This benchmark establishes
sample throughput/quality, not a whole-pipeline speed guarantee.

All planned corrective jobs and local validation are complete. Live publication and
human editorial approval remain separate operator actions. No active validation job
should remain after `completion-audit.json` has been written successfully.
