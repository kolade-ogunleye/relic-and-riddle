# QF-111: no runtime code writes a file the release ships

## Defect

The runtime rewrote tracked files in place. data/music/library.json was rewritten on every pick until QF-84. Since
QF-84 it is still rewritten on every approval, download and removal. Production's checkout therefore drifts from the
release it runs. The release deployer (Update-ContentTree) refuses any later release that changes such a file
("Production changed <file> differently from the release; resolve it manually."), so a release that touches it cannot
install itself (QF-84 review 1).

## The class: every runtime write to a release-owned path

The audit read every write primitive in src/, main.py and scripts/ and followed the helpers to their callers. A path
is release-owned when git does not ignore it: a tracked file, or a new file beside tracked ones. Git refuses to check
out a release over an untracked file of the same name, but it replaces an ignored one.

| Family | Runtime writers (entry points) | Fix |
|---|---|---|
| assets/<topic>/credits.json | credits.save_credits: approve_set (imgok, `assets approve`), drop/keep_images (/photos), image_review.screen_unchecked and review_pool (run start, ensure_pool, `assets review`), asset_pipeline.ensure_pool (daily run, nightly job), telegram_images.add_image (upload), scripts/fetch_commons_images.py (fetchimages button; ensure_pool subprocess); photo_orientation.put_upright (its own replace) | this host's copy (src/host_state.py); every direct reader redirected |
| assets/<topic>/storyboard.json | storyboard.save_storyboard (end of every fetch) | this host's copy; its readers redirected |
| data/fact_sheets/<topic>.json | fact_sheet.save (build, card-field re-check; script stage, nightly prebuild, `facts`), script_library.seed_fact_sheet | this host's copy; fact_sheet.current_path / main.fact_sheet_path for stamps, checkpoints and the final check |
| data/geo_cache.json | geo.locate on a cache miss (graphics stage, route maps) | this host's lookups over the shipped cache |
| data/music/library.json | music.save_library: approve_track (mkeep), remove_track (mdel), register_download (mfetch, fetch_music.py) | this host's copy |
| data/topics/topic_bank.json, image_queries.json | topic_store.commit/recover: /add, UNESCO uadd, proposal ptadd/tadd, `topic add` | this host's topics after the shipped bank (a release still ships bank edits), journal in this host's copies |
| docs/issues/*, docs/improvements/queue.md, data/prompt_patches.json, data/topics/*.transaction.json, assets side files (callout_locate.json, *.quality.json, *.presentation.json, *.source, .orientation_backup/) | /issue reports and evidence, improvement queue and patches, per-photo side files | gitignored (tracked files there stay tracked) |

Out of scope, and why:

- Authoring tools run by hand, whose output is meant to be committed: export_script_library, migrate_topic_ids,
  generate_sfx_pack, build_fallback_fonts, record_font_evidence, compare_light_engines, eval_*, goldens and
  ci_shards with an explicit path.
- maintenance.apply (the `upd` button and `maintenance --apply`) writes requirements.txt and makes a git commit on
  purpose. It is refused when RELIC_ENVIRONMENT=production. It is listed as an operator question in the build note.
- Read-only tracked inputs: stage_budget.json, reference_facts.json, cover_art_direction.json, wikidata_snapshot.json,
  rotation.json, data/nlp, data/geo, data/sfx, fonts.

### How this host's copy works

A tracked file is addressed by its shipped path. A write goes to the same relative path under data/host/, which is
gitignored. A read takes that copy when it exists, and the shipped file otherwise. Two families merge instead: the
geocoding cache, and the topic bank with its photo searches. The first write starts from what the shipped path holds
on that host. A production checkout whose tracked copy was changed in place therefore keeps every review, approval
and addition in it. Writes outside the checkout (a run folder, a test's folder) stay in place. Backups already cover
data/.

## Enforcement

- tests/tracked_writes.py is installed by conftest.py for every test of the suite. An audit hook refuses any open
  for writing, rename, replace, truncate or delete of a release-owned path, and the test fails with the path and the
  code that wrote it.
- The session also compares `git status` (tracked and untracked, not ignored) at its end with its start. This catches
  a child process a test started.
- conftest.py points src.config.HOST_STATE_DIR at a sandbox, so tests never write the developer's data/host/.
- The row asks for one full dry-run pipeline. The suite stands in for it: CI runs every stage's tests, all under the
  guard. One offline end-to-end dry run does not exist and would need every provider mocked.

## Measurements

measure.py runs each runtime writer once against a stand-in app root and compares the shipped file's bytes.

| | base 6a7ef303 | this commit |
|---|---|---|
| writers that changed the shipped file | 6 of 6 | 0 of 6 |
| writers whose change the application reads back | 6 of 6 | 6 of 6 |

Real data, before the fix:

- Production sample (pulled 2026-11-08, the VM checkout at b85bf5e; counts only, before_prod.json). Of the photo pools' tracked
  records, 6 of 16 credits.json and 4 of 15 storyboard.json differ in content from the release production runs
  (line endings ignored). One more storyboard.json exists where b85bf5e has none. Any release that changes one of
  those 10 files is refused today. The sample holds no data/ files, so the music library, fact sheets, geocoding
  cache and topic bank could not be counted. Code and run counts imply they diverged too: QF-84 review 1 counted 34
  library writes.
- Development main checkout (read only): 0 tracked files changed at the time of the audit.
- Deployer: the refusal now says why and what to do: "Production changed <file> differently from the release, so the
  release was not installed. Production's copy holds this host's own state, written in place by code from before
  QF-111 (reviews, approvals, its own additions). Keep it: if data\host\<file> does not exist yet, copy <file> there;
  then restore the release's copy with git checkout -- <file> and deploy again." The same text is in
  Update-ContentReleaseTree.ps1, the elevated fallback. The installed deployer changes only through the elevated
  promotion procedure (AGENTS.md).

## Hand-off notes

- The production files that already diverged stay modified until the operator resolves them once, as the message
  says. The application keeps reading them correctly meanwhile: until data/host/ has a copy, the shipped path is the
  copy it reads.
- The tests: tests/test_qf111_runtime_never_writes_tracked.py (regression) and the guard in tests/conftest.py.

## Round 2 (review 1: B1, B2)

### B1: every pool reader goes through this host's copy

Four checks still looked for a pool's record at its shipped path only:

- visual_engine.get_scene_sequence and reserve_sequence;
- control._fetched_sets (the /photos list) and _resolve_topic_dir.

A pool fetched for the first time on a host has its record only under data/host/. /photos did not list it, and the
video took the folder's photos through the "photos you supplied" branch, with no review check.

All four now use host_state.exists. Every reader of a pool's record goes through credits.pool_record or
load_credits, including privacy, video_qa, ai_label, multi_format, scene_planner, topic_engine and the two
validation scripts.

The "photos you supplied" branch now refuses any photo that carries the fetcher's download evidence
(`.quality.json`) or an upload's name (`telegram_`). Fetched photos without their record are never used, whatever
happened to the record.

The test greps src/, main.py and scripts/ for a pool or storyboard record read at its path (`CREDITS_FILE).exists(`,
`"credits.json").is_file(` and the like) outside host_state; it must find none. It also adds a host-only pool case:
the pool is listed, refused while unreviewed, and used once reviewed.

Real data: 8 of the 39 bank topics have no shipped pool, so they are exactly the ones a first fetch creates
host-only, as is every topic added later. The production sample's 16 pools all have a shipped record today.

### B2: a host copy no longer hides what a release ships

Per kind:

- **Geocoding cache:** this host's copy holds only its own lookups. The shipped cache is read over it, so a
  corrected shipped place always wins. A copy written whole by round 1 is read the same way.
- **Fact sheets:** fact_sheet.chosen picks between the shipped sheet and this host's copy:
  - only one is usable (readable, today's RULES_VERSION, not expired): that one;
  - both are usable: the newer by built_at, with this host's copy winning a tie (its re-checked card fields);
  - neither is usable: this host's copy, as before.

  A usable shipped sheet is never researched again. Stamps, checkpoints, the final check and the QF-110 re-check
  all read the chosen sheet.
- **Photo credits (per entry):** credits.with_shipped_attribution takes every attribution field of a photo the
  shipped record also lists from the shipped record (title, artist, author, creator, credit, attribution, licence
  and its link, source and its link, attribution_required, provider, own_work), for kept and rejected photos alike.
  The review fields (reviewed, verdicts, keeps) stay this host's.
- **Whole copies (the rest of a pool record, storyboards, the music library):**
  - When a host copy is first made, the shipped file's LF digest is recorded beside it (`<copy>.shipped.json`).
  - host_state.shipped_changed() names every copy whose shipped file has changed since. A copy with no record (one
    made by hand on the VM) takes today's shipped file as its start.
  - At run start, main.warn_shipped_changes logs a warning and raises one Needs-you item, "A release changed files
    this host keeps its own copy of", which lists the files and says how to take the new version.

Measured on the real tracked data (measure.py "masking"; round 1 = 0e4480b2, measurements_parent_round1.json):

| | round 1 | round 2 |
|---|---|---|
| shipped geocoding places (25) whose correction a host copy masks | 25 | 0 |
| usable shipped fact sheets (18 of 20) researched again beside a stale host copy | 18 | 0 |
| photos read with a stale attribution after the release's credit fix (QF-42, 1 pool) | 1 | 0 |
| shipped changes under a whole host copy said at run start (the music library) | 0 | 1 |

### Also

- tests/test_qf29_golden_images.py: the stand-in repository's commits pass `-c commit.gpgsign=false`, so a host
  that signs by default never waits on a card.
- Topic bank, a known limit: when an operator copies production's whole bank into data/host/, a topic a later
  release removes stays, because this host's topics are appended by id.
- Reconciliation for the operator, once, on the VM, and only after this round: copy each diverged file to
  data/host/ and restore the shipped copy. First check that the pipeline identity can create data\host\ (the ACLs
  are lab-owned). The tracked docs/issues files that scripts/production_reset.py moved away count as diverged too.

## Round 3 (review 2: B4)

Round 2 matched a pool's records by file name: credits.with_shipped_attribution took 13 fields, source_url
included, from the shipped entry with the same `file`. But pool photos are named by position (NN_commons.jpg), so
the same name can be a different photo. In the production sample, 21 of the 196 photos that share a name with a
tracked record are a different photo.

The review's replay put production's records in as this host's copies, as the deployer message tells the operator
to do, and read them through pool_record. 21 of 239 photos came back with another photo's title, artist, licence and
source_url. A later review write would have saved them that way.

Fix (src/credits.py):

- The merge matches on the same photo, not the same name. A shipped entry counts as the same photo when:
  - its source_url equals this host's entry's source_url, and both are set;
  - every content hash both entries record (sha256, source_sha256) is equal;
  - when the two entries name different files and both files are on disk, the files have the same bytes.
- source_url is the key, so it is never taken from the shipped record (it is out of ATTRIBUTION_FIELDS). The file
  name and the review fields stay this host's too.
- A photo the release does not list under the same source keeps its own credit.
- The run-start notice's Details sentence is corrected to "A photo's credit is still taken from the release when the
  release lists the same photo (the same source)". The approved headline is unchanged.

Regression cases (tests/test_qf111_runtime_never_writes_tracked.py):

- same name, different photo: keeps its own title, artist, licence and source, also after a save;
- the same photo under another name: takes the release's credit and keeps its own file and source;
- the same source with different bytes, or a different recorded hash: not merged;
- the QF-42-style correction for the same photo still arrives.

The review's replay again, on the production sample (counts only, replay_prod.json):

| | round 2 | round 3 |
|---|---|---|
| photos given another photo's credit (source_url changed) | 21 of 239 | 0 of 239 |
| the same photo given the release's credit (source_url unchanged, the artist differs) | 0 | 7 (the release's artist line for the same photo) |

measure.py's "stale attributions after QF-42's fix" stays 0: the fix keeps the photo's source_url, so it still
arrives.
