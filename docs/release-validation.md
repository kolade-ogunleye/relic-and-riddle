# Validate an episode locally

Run this from the repository with the project's Python environment:

```powershell
.\.venv\Scripts\python.exe -m src.release "output\<run>\<master>.mp4"
```

The command reads `script_metadata.json` beside the master and writes a fresh
`release_report.json`. It does not generate content, contact a language model, or publish.
Use `--metadata`, `--cover`, and `--grid` to specify different artifact paths.
For legacy metadata without a profile, specify `--profile deep_dive_16x9` or
`--profile reel_9x16` explicitly.
Exit code 0 means all implemented checks passed; exit code 1 means fail or needs review.

## Evidence and recovery

- Factual review must match the current public copy and research file. Changed titles,
  captions, descriptions, graphics annotations, or research require a new review.
- Provenance checks recompute hashes of the master, composition assets, source lineage,
  narration, music, and available rights records. Missing composition coverage fails.
- Audio evidence must match the actual audio file. Library tracks must have complete
  allowed-license evidence and intact source files; required attribution must be present.
  Provider-generated music needs music-specific clearance evidence.
- Visual checks must identify the current master checksum. Replacing or correcting a
  render requires another visual audit.
- Measured duration, dimensions, channels, sample rate, loudness, and true peak must
  meet the delivery profile. A duration profile does not prove monetization eligibility.
- Inspect preview frames and listen to the output before rollout. The automated report
  cannot establish historical truth, artistic quality, or rights beyond its recorded evidence.

Scheduling and publication evaluate the gate again; a previously saved passing report
does not bypass it. Unknown licensing is a review state, not an assumed public-domain grant.

## Existing runs

Legacy files without matching evidence are kept, but are not grandfathered into a passing
release. Production checkpoints reuse only artifacts whose input fingerprints and output
hashes match. Unverifiable cached stages must be rebuilt or reviewed with evidence.
Never create a successful review flag merely to make an older run pass.

The implementation and remaining rollout work are tracked in `implementation-status.md`.


## Recover an interrupted upload

Stop any workers from versions predating the OS upload lock before using recovery.
Then run the local recovery command (it sends nothing and does not upload):

```powershell
.\.venv\Scripts\python.exe scripts/recover_publication.py <run-id> --older-workers-stopped
```

A live worker holding the lock prevents recovery. An abandoned `publishing` claim
moves to `publish_unknown`; it never returns directly to a retryable state. Check every
platform account for the upload, then use the existing posted/not-posted reconciliation
control. Do not requeue an unknown outcome before checking the accounts. Published
runs cannot be uploaded again through `publish_run`.

## Reproduce validation without publication

The standalone preparation, audio and render scripts under `scripts/` do not invoke
the production daemon, approval notifications or upload adapters. Audio generation
requires a current factual receipt. `generate_validation_audio.py --reuse-existing`
retains matching licensed narration; changed scripts require a new narration artifact.
`render_validation.py` and `prepare_validation.py` require both trace and raw-evidence
checks. Reel rendering requires an existing current successful review.

`review_validation_audio.py <master>` submits an audio derivative to the configured
free checking provider and saves `audio_listening_review.json`. It has no text-only
fallback and never claims human approval. Missing/failed provider review is not a pass.

The audio request follows the provider's [audio-input documentation](https://ai.google.dev/gemini-api/docs/audio).
Its findings supplement final encoded-master measurements and visual inspection.
