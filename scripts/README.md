# scripts/

Every file in this folder and in `scripts/archive/`, with its class (QF-79). `tests/test_qf79_script_inventory.py`
fails when a script is added, removed or moved without a row here, when a row names a class not defined below, or
when anything outside the archive refers to an archived script, or when an archived script starts without its
override flag.

Run every Python script from the repository root with the project's virtual environment, for example
`.venv\Scripts\python.exe scripts\release_gate.py --db data\pipeline.db`. Scripts that read settings use the
checkout's own `.env`; none copies or prints it.

## Classes

| Class | Meaning | Rule |
|---|---|---|
| `live` | Imported by the application (`main.py`, `src/`), or run by the launcher, the release deployer, the promotion procedure, the development task or CI. | Path and behaviour are a contract: never move or rename; change only as an application change with its own tests and release. |
| `operator` | A command the operator runs on an environment, documented in `docs/`. | Keep in place; follow its doc. Writes are opt-in (`--apply` or an explicit sub-command) where marked. |
| `data` | Fetches or builds gitignored or bundled data (map data, fonts, models, sound effects, licence evidence). | Run when the data is missing or its source changes; record the evidence it writes. |
| `validation` | Release validation: prepares, renders and reviews a package locally, never publishing or messaging (except `telegram_acceptance.py`, development bot only). | Development or a validation workspace; see `docs/release-validation.md`. |
| `eval` | Measures a quality, model, engine or encoder question before a decision. | Paid ones have a spend cap or a dry-run default; read the notes before running. |
| `one-off` | A completed migration or reset that is still named by a document, the ledger or a test, so it stays where the reference points. | Do not run again without reading its doc; it was written for one state of the data. |
| `archived` | A completed one-off nothing refers to any more, kept in `scripts/archive/` for history. | Not maintained; do not run. Each refuses to start, before it imports or writes anything, unless given `--i-know-this-is-archived` (see the archive notes below). |

"Production" says whether the production environment runs or relies on the script. Safety lists network access,
spend and what the script writes.

## Inventory

| Script | Class | Purpose | Run | Production | Safety |
|---|---|---|---|---|---|
| `__init__.py` | live | Makes `scripts` a package so `main.py`, `src/` and tests can import `scripts.<name>`. | Not run. | Yes (package marker). | None. |
| `service_runtime.py` | live | Stable application entry point for the host's supervision: `contract`, `pipeline`, `backup-check`, `backup-cycle`, `backup-restore` (`docs/runtime-contract.md`). | `service_runtime.py contract`; `service_runtime.py pipeline --settings <launch.json>` | Yes: the lab's managed tasks' contract. | `pipeline` runs the live daemon (posting candidates, provider spend under the configured caps); backup operations upload and restore. Errors print only state and exception type. |
| `release_gate.py` | live | Says whether production is idle enough for the release deployer to stop it, from the database alone. | `release_gate.py --db <pipeline.db>` | Yes: `deployment/windows/Invoke-ContentReleaseDeployer.ps1` runs it before every install. | Read-only, no network, imports nothing from `src`. |
| `runtime_probe.py` | live | Focused production application checks run through the lab's approved identity runner (`docs/production-probe-handoff.md`). | `runtime_probe.py --app-root <app> --report <file> [--providers] [--paid-voice]` | Yes: `deployment/windows/Promote-ContentRelease.ps1`. | Read-only provider checks with `--providers` (network); `--paid-voice` synthesizes one short paid sample per voice provider. Never starts a bot or publishes. |
| `windows_runtime.ps1` | live | Development runtime preparation and the hidden development task (`Prepare`, `InstallTask`, `Start`, `Stop`, `Status`). | `powershell -File scripts\windows_runtime.ps1 -Action Status` | No: refuses `-Environment Production` (lab automation owns production tasks). | Creates runtime folders, registers and starts the development task; never creates or copies `.env`. |
| `eval_prompts.py` | live | The only eval that measures the production writer on the configured engine; `main.py`'s nightly improvement cycle imports `run_candidates`. Base of `eval_harness.py`. | `eval_prompts.py --runs 2`; `eval_prompts.py --summary` | Yes: nightly candidate eval. | Free engine only (paid fallback switched off); network; appends to `data/evals/prompts/results.jsonl`. |
| `prompt_variants.py` | live | Candidate rewrites of the writer's prompts that `eval_prompts.py` compares with production's. | Not run (imported). | Yes, through `eval_prompts.py`. | None (data only). |
| `fetch_commons_images.py` | live | Fetches licensed scene images for topics from Wikimedia Commons with their credits; `src/telegram_images.py` imports `fetch_topic` for the Telegram photo fetch. | `fetch_commons_images.py [topic ids]` | Yes, through the Telegram photo fetch. | Network (Commons, museum sources); writes `assets/<topic>/`, `credits.json` and contact sheets. |
| `fetch_music.py` | live | Fetches CC0 / CC BY music from Openverse by mood, unreviewed until approved with `/music`; `src/control.py` imports `fetch_music_tracks`. | `fetch_music.py` | Yes, through the operator's music job. | Network (Openverse); writes `data/music/` and `library.json`. |
| `check_lock.py` | live | `requirements.txt` must agree with the hashed `uv.lock` and `pyproject.toml`; CI runs it. | `check_lock.py` | No (CI, `.github/workflows/tests.yml`). | Read-only, no network. |
| `lint_topic_bank.py` | live | Offline editorial lint of the topic bank (QF-73): sources, hooks, regions, sensitivity, no #ReelsViral; CI runs it. | `lint_topic_bank.py [path/to/topic_bank.json]` | No (CI, `.github/workflows/tests.yml`). | Read-only, no network. |
| `backupctl.py` | operator | Backup enrollment, cloud verification, retries and isolated restore (`docs/backup.md`). | `backupctl.py [--environment ...] {keygen,auth,check,snapshot,cycle,sync,status,list-cloud,restore}` | Yes: the operator's backup commands. | Network (OneDrive) for `auth`, `check`, `sync`, `list-cloud`, `restore --cloud`; writes the backup spool and restores only into a new isolated folder. |
| `env_reconcile.py` | operator | Brings the checkout's `.env` up to date with `.env.example` without showing a secret (`src/env_sync.py`). | `env_reconcile.py [--apply [--include-changes]]` | Yes: the bot suggests it when `.env` lags the template. | Report only by default; `--apply` edits `.env` with a backup. No network. |
| `archive_validation_runs.py` | operator | Archives explicitly named validation runs, keeping paid usage and unrelated state. | `archive_validation_runs.py --run-id <id> [...]`; `--apply --workers-stopped [--include-failed]` | Yes, when validation runs are retired. | Preview by default; `--apply` moves runs and database rows; stop the bot first. No network. |
| `reset_database.py` | operator | Starts the database afresh with a full backup, keeping this month's paid usage and pending proposals. | `reset_database.py [--apply] [--wipe] [--purge-output] [--purge-legacy]` | No: production uses `archive_validation_runs.py` (the deployment docs forbid whole-database resets there). | Dry run by default; `--apply` rewrites the database, `--wipe` keeps nothing. |
| `recover_publication.py` | operator | Quarantines an interrupted upload locally (`docs/release-validation.md`). | `recover_publication.py <run_id> --older-workers-stopped` | Yes, after an interrupted upload. | Database write only; never uploads or notifies. |
| `export_script_library.py` | operator | Exports checked fact sheets and scripts into the script library so a release carries them to the other environment (`docs/deployment.md`). | `export_script_library.py TOPIC_ID [...] [--replace]` | Indirectly: production reads the exported library from the release. | Writes the script library; no network, no voice, music or clips. |
| `youtube_analytics_auth.py` | operator | One-time OAuth consent for YouTube audience-retention reports. | `youtube_analytics_auth.py --client-id <id> --client-secret <secret>` | Indirectly: its printed values go into production's `.env` by hand. | Opens a browser, network (Google OAuth); prints a refresh token to the terminal, writes nothing. |
| `relocate.py` | operator | Rewrites stored absolute run paths after the project folder moves or is renamed. | `relocate.py "<old project folder>"` | No. | Rewrites paths in the database; no network. |
| `fetch_geodata.py` | data | Natural Earth country outlines (1:50m; `--10m` subunits) for the maps. | `fetch_geodata.py [--10m]` | Yes, at provisioning (`docs/deployment.md`). | Network (naciscdn); writes `tools/geo/`. |
| `fetch_marine.py` | data | Natural Earth marine polygons and Antarctic ice shelves for the maps. | `fetch_marine.py` | Yes, at provisioning. | Network; writes `tools/geo/`. |
| `fetch_relief.py` | data | Natural Earth shaded relief for the maps' hillshade. | `fetch_relief.py [--medium]` | Yes, at provisioning. | Network (large download); writes `tools/geo/relief/`. |
| `build_fallback_fonts.py` | data | Builds the bundled Noto fallback faces from pinned releases with licensing evidence (QF-94). | `build_fallback_fonts.py` | Indirectly: the faces ship in `fonts/`. | Network (pinned GitHub raw files); writes `fonts/`. |
| `record_font_evidence.py` | data | Records bundled-font licensing notices and identity evidence from primary repositories. | `record_font_evidence.py` | Indirectly: the evidence ships with the fonts. | Network (GitHub); writes font evidence files. |
| `convert_realesrgan_onnx.py` | data | Converts the official Real-ESRGAN x4plus weights to ONNX (`docs/upscaler.md`). | Once, in a separate environment with PyTorch (see its docstring). | Indirectly: the converted model. | Reads local weights (tensor-only unpickler); writes the ONNX model. |
| `generate_sfx_pack.py` | data | Generates the licensed sound-effect pack with ElevenLabs Sound Effects, with rights evidence. | `generate_sfx_pack.py` (dry run); `--apply [--force]` | Indirectly: the pack in `data/sfx/`. | Paid (about 1,000 credits for the pack) with `--apply`; network; writes `data/sfx/`. |
| `migrate_legacy_pd_records.py` | one-off | Adds the worldwide-basis evidence QF-57 requires to legacy public-domain records in `assets/*/credits.json` (named by `QF-57/README.md`). | `migrate_legacy_pd_records.py [--assets assets] [--apply]` | No. | Audit by default; `--apply` rewrites `credits.json` files. No network. |
| `migrate_topic_ids.py` | one-off | Moved topics from numbered to semantic ids, keeping production state (named by `.gitignore` and the 2026-10-25 audit). | `migrate_topic_ids.py [--apply]` | No (done). | `--apply` rewrites the database and asset folders with a local backup. |
| `production_reset.py` | one-off | The 2026-10-25 reset of production to a single posting candidate (named by `AGENTS.md` and its test). | `production_reset.py [--apply]` | Done once; kept as the archive procedure `AGENTS.md` cites. | Lists only by default; `--apply` archives runs and resets state; refuses while anything runs. |
| `prepare_validation.py` | validation | Prepares and fact-checks a fresh documentary package without messaging or publishing. | `prepare_validation.py <topic_id> <output> [--feedback <file>]` | No. | Writer and checker calls on the configured engines (network, possible spend); writes the package folder. |
| `prepare_reel_validation.py` | validation | Prepares a verified Reel package for local validation. | `prepare_reel_validation.py <topic_id> <output>` | No. | As `prepare_validation.py`. |
| `download_validation_assets.py` | validation | Resumes a bounded validation download of candidate images, keeping partial evidence. | `download_validation_assets.py <candidates.json> <output>` | No. | Network (Commons); writes the output folder. |
| `generate_validation_audio.py` | validation | Generates checkpointed validation narration and bed without messaging or publication. | `generate_validation_audio.py <package> [--reuse-existing]` | No. | Voice synthesis on the configured provider (network, possible spend); writes the package. |
| `render_validation.py` | validation | Renders and inspects a prepared documentary without publishing or sending messages. | `render_validation.py <package> <sources> --images <files>` | No. | Local render; writes the package and its release report. |
| `render_reel_validation.py` | validation | Renders a prepared Reel and its packaging locally. | `render_reel_validation.py <topic_id> <package> --images <files>` | No. | Local render; writes the package. |
| `review_validation_audio.py` | validation | Automated listening review of an encoded master; never a human sign-off. | `review_validation_audio.py <master>` | No. | Network (Gemini free key); writes a report next to the master. |
| `telegram_acceptance.py` | validation | Scripted Telegram acceptance of every read-only command and panel against the real Bot API, development bot only. | `telegram_acceptance.py [--trace] [--keep]` | No: refuses the production bot. | Network (Telegram); sends messages to the development chat and deletes them unless `--keep`; works on a temporary database copy. |
| `vulkan_diagnostics.ps1` | validation | Read-only guest inventory for the Vulkan investigation (`docs/runtime-acceptance.md`). | `powershell -File scripts\vulkan_diagnostics.ps1` | Run on a host by the lab-approved guest account. | Read-only: no task registration, registry writes, driver installs, `.env` or API calls. |
| `eval_harness.py` | eval | The evaluation harness base (QF-79): resumable draft loop over topics and variants plus the paired comparison, on `eval_prompts.py`; QF-17 extends it. | `eval_harness.py <results.jsonl> [--baseline current] [--candidate general]` (offline summary) | No. | The harness builds no engine and calls no provider; the caller's `draft` decides spend. The CLI only reads a results file. |
| `eval_models.py` | eval | Stale: blind evals of Opus against Sonnet (scripts, second check, light tasks) while production writes with Gemini. Superseded by `eval_harness.py` for writer questions. | `eval_models.py estimate`; `eval_models.py run --max-usd 30` | No. | Paid Claude calls under `--max-usd`; `estimate` makes no call. |
| `eval_facts_first.py` | eval | Stale: measures the verify-first pipeline with Claude models (builds on `eval_models.py`). | `eval_facts_first.py --max-usd 25` | No. | Paid Claude calls under `--max-usd`. |
| `compare_light_engines.py` | eval | Whether a light-task model can stand in for the current one, before `FREE_LIGHT_MODEL` or `GROQ_LIGHT_FALLBACK` change. | `compare_light_engines.py --candidate groq` | No. | Network (free keys; may use the capped light paid fallback, cents); read-only on runs. |
| `benchmark_encoders.py` | eval | Compares the configured CPU and hardware encoders on a mixed sample. | `benchmark_encoders.py <master> <output>` | No. | Local ffmpeg; writes the output folder. |
| `look_at_stills.py` | eval | Runs the still-vision check over finished runs' Story frames and carousel slides (quality step 5). | `look_at_stills.py output/run_a [...]` | No. | Vision model calls on the configured engine (network); read-only on runs. |
| `render_safe_zone_evidence.py` | eval | Safe-zone evidence frames with each platform's interface bands drawn over them. | `render_safe_zone_evidence.py output/run_<id> docs/reviews/safe-zones-<date> [--photos <folder>]` | No. | Local render; reads the run, writes the evidence folder. |
| `motion_pilot.py` | eval | A small paid pilot of photo-animation providers against the free local parallax. | `motion_pilot.py` (list and estimate); `--apply` | No. | Paid (about $5) with `--apply`, recorded in the media ledger; network; writes `staging/motion_pilot/`. |
| `archive/clean_dev_telegram.py` | archived | Swept the development chat's old messages once. | Do not run. | No. | With the flag: by path it stops at `import src`; with `-m` from the root it deletes the configured chat's messages through the Bot API (network) and resets the database's Telegram card state. |
| `archive/generate_topic_bank.py` | archived | Generated the first 30-topic bank (`data/topics/topic_bank.json` at the time). | Do not run. | No. | With the flag, either way: it writes `scripts/data/topics/topic_bank.json` (a stray bank beside the archive, not the real one). No network. |
| `archive/regenerate_queue.py` | archived | Rebuilt the SQLite topic queue from that bank. | Do not run. | No. | With the flag: by path it stops at `import src`; with `-m` from the root it deletes and rewrites the real database's topic rows. No network. |
| `archive/rename_topics.py` | archived | Renamed the development forum topics once. | Do not run. | No. | With the flag: by path it stops at `import src`; with `-m` from the root it renames the configured chat's forum topics through the Bot API (network). |
| `archive/reorder_topic_bank.py` | archived | Reordered the topic bank and the queue once. | Do not run. | No. | With the flag: by path it stops at `import src`; with `-m` from the root it rewrites the real topic bank and the database's topic rows. No network. |

### Archive notes

The archived files are the original one-offs, moved unchanged apart from a refusal at the top: without
`--i-know-this-is-archived` each exits with status 1 and a message saying what it would do, before it imports
anything from `src` or writes anything, whether started by path (`python scripts\archive\<name>.py`) or as a module
(`python -m scripts.archive.<name>` from the repository root). With the flag they run as they were written. Started
by path, their project root resolves to `scripts/`, so the four that import `src` stop at that import and
`generate_topic_bank.py` writes under `scripts/data/`. Started with `-m` from the root, `src` imports and they act
on the checkout's real database, `.env` settings and Telegram chat. None makes a paid call. Git history keeps the
removed round-1 Telegram mock poster.
