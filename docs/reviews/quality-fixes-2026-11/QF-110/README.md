# QF-110: scheduled posting survives code updates and clean-ups, and never blocks silently

Operator: "YES", 2026-11-11 (top priority: publishing is blocked on production). Production runs older code; nothing
here reaches it before a signed release.

## The production failure (diagnosis, counts only)

One approved Reel booking never posted; post_due failed every minute for days. Four causes, each reproduced by a
test in `tests/test_qf110_posting_survives_updates.py`:

1. **Fingerprint drift.** The fact gate required the stamp's code fingerprints (the checkers' prompts, and the
   surfaces the copy is read from) to equal the current code's, so a release after approval invalidated every review.
2. **Missing parallax.** The pre-run purge (`ops.AssetLifecycleManager`) removed `parallax/` (and `motion/`) from
   every run folder older than 7 days whatever its state, a booked one included; the gate required every recorded
   clip and lineage record on disk.
3. **One blocked item aborted the job.** A release refusal raised out of `_post_due`, so every other due post waited.
4. **An alert every minute,** in internal terms (`Scheduled posting hit an error: Release blocked: ...`).

## What changed

- **Facts are content-bound** (`src/fact_evidence.py`, `src/release.py`, `main._recheck_facts`). When a stamp is stale
  only because of code (its rules or public-copy fingerprint moved, or it was made before records were signed, which
  operator decision A already sends back for a re-check), and the research file is unchanged, and the run's review
  fingerprint (its payload, master, cover, Stories, carousel) is still the one on the review card the operator
  approved, the two checkers run again on a copy of that payload under the current rules. The signed result
  (`fact_recheck`, pass or fail) is kept beside the master (`fact_recheck.json`), never in the payload (so the
  approval binding holds), and is reused, so it is paid once. The gate passes only on a signed passing re-check of
  this very content. A changed sheet or reference file, changed copy or media, a forged or unsigned-but-signed-field
  stamp, or a failing re-check blocks.
- **The approved master is the unit of trust** (`video_qa.provenance_problems`, `release`). A scene clip or lineage
  record in `parallax/` or `motion/` that is gone is counted, not refused (a present one must still match). Mandatory,
  and why: the master and its composition record (the unit of trust); photos, footage, illustrations, map data and
  their licence and credit records (photo rights); narration and music bed with their rights records (the gate
  inspects them); fonts and the font licence record (font rights); the fact sheet and reference file. The strict
  `verify_provenance_integrity` is unchanged for its other callers.
- **Repository-owned rights records by content** (operator ruling B, decisions/QF-110-question.md;
  `video_qa._repository_record_problem`). A release rewrites these for reasons unrelated to an episode, so a byte
  change is not by itself a block:
  - `fonts/license_evidence.json` (the font catalogue): only the entries of the fonts this master used are read, by
    font file name and sha256 from the master's composition record. Each must still exist with that font sha256, name
    a licence text recorded with the video, carry that text's current sha256, and the text must still be the recorded
    one (below). Entries for other fonts may be added or changed. `font_rights` still verifies every used font at
    post time (its sha256, its licence file and hash, "SIL Open Font License" and a copyright), and the font files
    themselves stay byte-checked.
  - `fonts/licenses/*.OFL.txt`: compared after line-ending normalisation only: every run of CR before LF, and every
    lone CR, becomes LF; nothing else (no whitespace, case or blank-line change). Only the old file's hash is on
    record, so the normalised current text is rendered with LF, CRLF, CR and CR CR LF line ends and one rendering
    must hash to the recorded sha256. Any other byte that differs (one word of a different licence) changes every
    rendering's hash, so it cannot pass. (Anton's text was re-saved from CR CR LF to LF on 2026-10-24; Montserrat's
    from CRLF.)
  - Per-episode records stay byte-checked: photos, `assets/<topic>/credits.json`, and the voice and music
    `.rights.json`, a rebind-repointed one included (ruling: not C).
- **post_due isolates bookings** (`main._post_due`). A refusal no retry can change sets the booking to `blocked`
  with its reasons in plain words and frees its day; one Needs-you alert per blocked booking, with ♻️ Make a new
  version (asks first), ⏹ Cancel booking (asks first), 📋 Details. An error that may pass keeps the booking and is
  said once per distinct error. The posting job's own error is said once until it changes or a pass works.
- **Post now / Retry Reel** re-run the gate after the re-check and answer a block in plain words with what can work;
  the episode view lists a blocked booking's reasons and its two actions.
- **Clean-up never touches a pending run** (`src/retention.py`, `ops.AssetLifecycleManager(db=...)`). Intermediates
  are freed only for runs that are published, rejected or archived (or unknown to the pipeline: nothing can review,
  book or post them); a live booking (pending, waiting, blocked, unknown) or an open review protects a run whatever
  its status. Every removal is logged by run and written to `output/retention_log.jsonl`. The supported command is
  `python -m src.retention` (lists; `--apply` frees; `--caches` adds QF-100's prepared-image caches: entries no run
  folder's provenance or lineage names, unchanged for `CACHE_RETENTION_DAYS`, 30 by default). What is freed is an
  explicit allowlist: the folders `parallax/`, `motion/`, `qa_frames/`, `stage_cache/`, `audit/` and root files whose
  name ends in `.tmp` (never one ending `<media or record suffix>.tmp`). No name pattern is matched.

## The master-deleting clean-up (live on production today)

The pre-run purge matched files by the pattern `*temp*` (`src/ops.py` `AssetLifecycleManager.SCRATCH_PATTERNS`, in
the code since the 2026-10-11 baseline, and so in production's b85bf5e). Path on production: every run start,
`PipelineCoordinator.run_cycle` (after the health checks) calls `lifecycle_manager.purge_intermediate_assets()`,
which for every `output/run_*` folder whose own modification time is over 7 days old, whatever the run's state,
deletes (a) the root files matching `*temp*` or `*.tmp`, and (b) the folders `motion/`, `parallax/`, `qa_frames/`,
`stage_cache/` and `audit/`. For a topic whose id contains "temp" (in the topic bank: `kailasa_temple_monolith`) (a)
matches the run's master `<topic>_master.mp4`, its `.composition.json`, `.srt`, `.vtt` and `_captions.ass`: the
published master and its captions are deleted. On dev, `run_kailasa_temple_monolith_1792524470` has lost exactly those
files; in the production sample, runs without a master: 1 of 34; runs whose folder name contains "temp": 1 of 34;
runs with both: 1. So the "1 missing master" counted below in both sets is this bug, not an episode defect. QF-110 replaces the pattern with the
allowlist above (`src/retention.py`, which `ops` now delegates to); the test
`test_cleanup_never_deletes_a_temple_topics_master` keeps it from coming back. Production keeps deleting until a
signed release carries this fix.

## Measurements (`measure.py`, counts only)

`python docs/reviews/quality-fixes-2026-11/QF-110/measure.py <root> --runs <dev output> --runs <copy of the
production sample's output> --db <read-only copy of the dev database>`; base 2debdf8b and this commit.

| | base | QF-110 |
|---|---|---|
| synthetic: missing parallax, master verified | blocks | posts |
| synthetic: altered master / changed source photo | blocks / blocks | blocks / blocks |
| production sample (34 runs; the sample excludes `parallax/` by construction): runs lacking `parallax/` | 34 | 34 |
| production sample: missing intermediate files (parallax/motion clips and lineage) | 596 | 596 |
| production sample: provenance passes if the files outside the sample (fonts, data, tools, cache) verify | 0 | 27 |
| production sample: runs blocked by intermediates alone | 27 | 0 |
| production sample: blocks still blocking (changed credits.json 7, changed pool photo 2, master deleted by the `*temp*` purge 1; 7 runs) | 7 | 7 |
| production sample: fact stamps / made before signing / rules fingerprint drifted | 34 / 34 / 9 | same |
| dev (47 folders, 30 with provenance): runs lacking `parallax/`, missing intermediate files | 25, 135 | 25, 135 |
| dev: the release gate's provenance check passes (30 runs) | 0 | 25 |
| dev: blocks still blocking (2 runs voice/music `.rights.json` changed, 2 runs fact sheet changed, 1 run whose master and composition record the `*temp*` purge deleted) | 5 runs | 5 runs |
| dev: fact stamps / sheet unchanged / drift alone with sheet unchanged | 33 / 30 / 1 | same |
| dev database copy: approved runs / review card recorded / binding still holds | 13 / 6 / 6 | 13 / 6 / 6 |

Every true block still blocks: no run whose master, photo, credits, rights record, font or fact file changed or went
missing passes under the new rule (the measure counts them apart from the freed clips).

The byte-level classifier in measure.py (`true_blocks`) still lists `fonts_changed` for all 30 dev runs: that is
the old rule's view; `gate_provenance_pass` and `gate_problems` are the release gate's own check. The production
sample holds no fonts, so ruling B cannot be measured there (its counts are unchanged: 27 pass if the files outside
the sample verify, 7 true blocks). Between the production approval's commit 41db6d2 and this base the font
catalogue only gained entries (+40 lines, 0 removed) and no used licence text changed, so the font records no longer
block production's stuck booking.
