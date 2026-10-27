# Development and production deployment

The workstation checkout is development; the VM is production. Their private
configuration, Telegram identities, queues, approvals and usage ledgers are
independent. Never deploy dev's `.env` or database to production. Dev feature
flags remain unchanged; production retains its configured licensed providers and
existing caps. A dry run can still consume API credits.

Production runs only the formats in its `VIDEO_FORMATS` (default `reel`). The 16:9 documentary
stays out of it until `docs/documentary-rebuild.md`'s acceptance criteria are met and approved;
`YOUTUBE_SHORTS` stays off in production until the operator enables it.

## Responsibility and runtime

The lab repository owns managed identities, host authorization/ACLs, OS/GPU
exposure, security telemetry and whole-VM recovery. Its operating procedure is
`docs/CONTENT_VM_TASK_IDENTITY.md`. Application changes do not modify those controls.
This repository owns application launchers, workload task definitions, deployment,
the CLI/API contract, backup format, provider behavior,
Telegram UX and content acceptance checks. See [reconciliation](deployment-reconciliation.md).

The lab's managed deployment passed no-login reboot acceptance on 2026-10-22 UTC.
Its pipeline task uses `LAB\rr-pipeline$`; backup uses `LAB\rr-backup$`. Both
have the `-Unattended` task suffix. The original interactive tasks remain disabled;
retirement uses approved host administration. Old production task/account installers have
been removed from the application release. `scripts/windows_runtime.ps1` manages
development only and refuses production actions. Its task runs headless (Windows
Script Host starts the launcher hidden), so Start opens no terminal window or tab;
output is in `logs/runtime-Development.*.log`.

The external launcher supervises Windows process ownership, stop requests and
exit status. The [application service contract](runtime-contract.md) supplies
explicit-path pipeline and backup operations. The pipeline never receives backup
custody and does not schedule API cycles. The separate backup task owns those.
Current deployed adapters remain compatible until a lab-controlled promotion.

## Application release workflow

1. Reconcile code/tests against the published application branch. Keep content
   metadata changes separate from the release and preserve dirty operator work.
2. Run the offline suite and focused runtime-contract tests. Build a source
   artifact from a committed revision, including dependency pins, but excluding
   `.env`, private media, databases, caches, usage records and credentials.
3. Publish the reviewed branch and immutable artifact/checksum to origin. A
   branch push is not a production activation or a claim of a merged release.
4. Have lab deployment promote the approved artifact and manifest through its
   controlled idle-stop/start procedure. Preserve its identity/ACL/process
   supervision controls. Do not replace the running tree during generation,
   publication or backup transfer. Preserve production-only data modifications.
5. Install the pinned dependencies using the release's virtual environment. Keep
   FFmpeg 8.1 until the 9.0.2 encoded-audio compatibility problem is resolved.
6. Execute [focused application probes](production-probe-handoff.md) under the
   pipeline identity. Test backup API/cycle/restore under the backup identity.
   Ordinary-user SSH checks cannot substitute for managed-account acceptance.
7. After Telegram defects are addressed, run end-to-end content validation and
   human approval. Archive validation runs before explicit live activation,
   retaining paid usage, publication IDs, unknown outcomes and provenance.

Production remains `daemon --dry-run` until the operator explicitly authorizes
live posting. This runbook does not implement an automatic merge/deploy pipeline.

### Script library (step-1 work carried between environments)

`data/script_library/<topic>/` holds a fact sheet and, where one exists, a script that
passed the fact check against exactly that sheet (`source.json` names the run). It
ships with a release so production need not pay again for research and scripts
development already made (2026-10-27). Voice, music and clips are never carried:
production voices with ElevenLabs itself. Rules:

- A fact sheet is copied in only where the topic has none; an environment's own
  sheet is never overwritten. A sheet over 60 days old is researched again, as any is.
- A library script is used only when the topic comes up in that environment's own
  queue, its fact sheet is the same research, and it passes today's checks. Human
  review still decides. No approvals, runs or usage records are carried.
- Export: `python scripts/export_script_library.py TOPIC ...` (an existing entry is
  kept unless `--replace`). `tests/test_script_library.py` refuses a release whose
  entries no longer match their research or today's checks.
- Starting afresh: `main.py run|test --fresh-from facts|script|voice|music|clips`
  remakes that step and every later one; `--reuse-from RUN` reuses what an earlier
  run of the same topic made and passed. In Telegram: `/render fresh script`,
  `/render reuse 12`.

## Recovery and rollback

Before promotion, preserve the prior app artifact and take a verified backup.
Reinstall the previous artifact through lab automation if code rollback is needed.
A code rollback does not require restoring an old database: doing that can erase
paid usage and replay publication. Reconcile uncertain uploads before any state
restoration. Keep only one production bot poller active.

[Backup and restore](backup.md) documents encrypted snapshots, independent key
custody and isolated drills. The managed service uses the custom OneDrive API
folder, protected spool and separate encrypted cache. Desktop sync fallback is
disabled while signed out. Whole-VM recovery is a distinct lab responsibility.

## Evidence and remaining acceptance

The lab runbook records Session 0 NVENC/API checks, the no-login reboot, fresh
cloud snapshot `production-20261022T014910Z-5f996e76f628`, and isolated restoration
of 429 files with SQLite integrity confirmed. It also records independent host
watch/telemetry checks; those are infrastructure evidence, not app quality tests.

The earlier application snapshot `production-20261021T201035Z-7b9467ab94e1` also
passed downloaded SHA-256 and cloud restore. Reports remain under the isolated
recovery-drill directory, outside the active checkout. Independent recovery-key
custody is documented in the backup runbook; key values are never released.

The previous approved Kailasa VM dry run passed NVENC, DirectML inference,
quality audit and release checks under the old operator context. The managed
identity subsequently passed depth, native Speech and NVENC checks; Vulkan
upscaling failed. See [the acceptance record](runtime-acceptance.md). The
Derinkuyu image pool, actual configured-provider generation, Telegram defects and
human acceptance are separate gates. See the reconciliation register for status.

## Promoting a release (application-owned procedure)

With the lab handoff complete (lab PR #60), release promotion is the application's procedure.
`deployment/windows/Promote-ContentRelease.ps1` runs once, elevated, on the VM and performs the
idle stop/start: it refuses while a run is producing, stops the pipeline through its launcher's
stop file, waits for the operator's SSH session to update the tree (`ready <commit>` in
`C:\Users\omokolade.LAB\relic-promote.flag`), restarts the managed task, retires the disabled
legacy tasks after exporting their definitions, and runs the focused probe under
`LAB\rr-pipeline$` (`-PaidVoice` adds one short licensed voice sample per provider). During
the wait the SSH session runs `deployment/windows/Update-ContentReleaseTree.ps1 -Commit <tag>`:
it checks out the release, installs pinned requirements, places
`tools/models/realesrgan_x4plus.onnx` (verified SHA-256, see [upscaler](upscaler.md)), runs the
offline suite and writes `ready <commit>`; on any failure it returns to the previous commit and
signals that one. Stage beforehand in `C:\Users\omokolade.LAB\relic-stage`: the release git
bundle (fetched into `refs/remotes/stage/release`, which does not touch the running tree), the
model and the new dependency wheels, so the stopped window is short. Production data files
the release does not change (fact sheets, credits, music usage) keep their local changes. The
procedure never changes `.env`, identities, ACLs or the backup task, and production stays in
`--dry-run`. Rollback: the same procedure with the previous tag.

## Automatic release deployment

Once installed, production installs signed releases itself; promotion needs no administrator and
nothing connects to the VM. Signing the release tag with the operator's smartcard is the approval.

| Piece | Where | Runs as |
| --- | --- | --- |
| Release deployer `Invoke-ContentReleaseDeployer.ps1` (+ `lib/ContentReleaseDeployer.ps1`) | `ContentRuntime\tools\deploy` | `LAB\rr-deploy$`, task `RelicAndRiddle-ReleaseDeployer-Unattended`, every minute |
| Hold-aware launcher `runtime/Start-ContentVmWorkload.ps1` (+ `lib/ContentReleaseHold.ps1`) | `ContentRuntime\tools` | `LAB\rr-pipeline$` (the existing managed task) |
| Idle gate `scripts/release_gate.py` | the running release | the deployer, database read-only |
| Pinned signer `release/allowed_signers`, GitHub host keys `release/github_known_hosts` | `ContentRuntime\tools\deploy` | read-only to the deployer |

**Cycle.** The deployer checks GitHub every 2 minutes (or within a minute of `/deploy` in the
production bot) for a tag newer than the running one (`v1.2.3`, `v1.2.3-rcN`; a final release
follows its candidates). It installs it only when:
1. the annotated tag carries an SSH signature from the pinned signer (anything else is refused
   and never retried);
2. the release's offline suite passes in a worktree beside production, on 4 workers (once per
   candidate commit: the pass is remembered in `logs\Deploy\precheck-<tag>.json`, so a release
   waiting for production to be idle is not tested again every check);
3. production is idle: no run producing, awaiting review or approved, no open review, no post due
   within an hour, no daemon job holding a busy lease, clear of the daily run (15 minutes before
   to 15 after), and no backup running.

It then writes `control\Pipeline.hold`. The launcher stops the daemon's worker tree while the
managed task keeps running, the deployer checks the release out (installing changed
requirements), and the launcher runs the suite again under `LAB\rr-pipeline$` with production's
own `.env`. On a pass the hold is cleared, the daemon restarts on the release and must still be
running 3 minutes later. Any failure returns the tree to the previous release, clears the hold and
records the tag as refused. An interrupted deployment is rolled back on the deployer's next start.

**Reporting.** Each outcome goes to `control\release.json`, which the daemon announces in
Telegram once, and to the Application log: 5010 installed, 5011 refused or rolled back (both
below the paging threshold; production is on a working release), 5001 if a failed release could
not be restored and the pipeline stays held (pages). `logs\Deploy\status.json` shows what the
deployer is doing; `/deploy` includes it in its reply.

**Boundaries.** The deployer's account is denied `.env`, has no administrator membership, cannot
change its own installed copy, the signer list or the tasks, and never changes identities, ACLs,
the database or the backup. Production keeps `daemon --dry-run`. A tree that is not at a release
tag (a manual change) pauses automatic deployment until a release is installed again.

**Releasing.** Tag a reviewed commit and sign it with the smartcard key:
`git -c gpg.format=ssh -c gpg.ssh.program=C:/Windows/System32/OpenSSH/ssh-keygen.exe -c user.signingkey="key::$(ssh-add -L)" tag -s v1.0.0-rcN -m "v1.0.0-rcN"`
(Windows OpenSSH reaches the card through its agent; Git's bundled `ssh-keygen` does not),
then push the tag. Only sign what passed the suite and the development test. To roll back, sign a
new tag on the previous good commit; for an emergency, the elevated procedure above still works.

**Installation (once).**
1. Apply lab plays 50, 51 and 54. They provide `LAB\rr-deploy$` and its rights; play 54 also grants it git, the operator's MinGit winget package.
2. During a promotion, while `Promote-ContentRelease.ps1` waits for `ready <commit>` and the pipeline is stopped, open a second **elevated** window on the VM in the release checkout and run:
   ```powershell
   .\deployment\windows\Install-ContentReleaseDeployer.ps1 -WhatIf   # review what it would change
   .\deployment\windows\Install-ContentReleaseDeployer.ps1
   ```

The installer first checks every prerequisite and changes nothing if one fails:
- the identity, its batch logon right and its ACLs;
- the `.env` deny;
- the signer;
- that the pipeline and backup tasks are stopped.

It then does the following, and is safe to re-run because it only replaces what differs:
1. backs up whatever it replaces to `rollback\deployer-install-<UTC>`;
2. installs the hold-aware launcher into `tools`;
3. installs the deployer, the pinned signer list and GitHub's host keys into `tools\deploy`, which the deployer can read but not change;
4. writes `deploy.json`;
5. creates `secrets\Deploy\github_ed25519` and prints only its public half;
6. registers `RelicAndRiddle-ReleaseDeployer-Unattended` as `LAB\rr-deploy$`.

It does not start the deployer or change the pipeline or backup tasks, identities or ACLs.

3. Add the printed key to GitHub as a read-only deploy key: repository Settings → Deploy keys, with "Allow write access" off. Then let the promotion finish.
4. The deployer's first runs report in `logs\Deploy\status.json` and through `/deploy`. It stays paused until the tree is at a release tag.

Until this is installed, releases use the elevated procedure above.

**To undo:** disable or unregister the deployer task. Restore the launcher from `rollback\deployer-install-<UTC>`, and remove the deploy key on GitHub.

**Updating the deployer or launcher.** A release that changes `Invoke-ContentReleaseDeployer.ps1`,
its library, the signer list or a launcher file installs the application tree as usual, but the
installed copies in `tools` and `tools\deploy` stay as they were: the deployer cannot change them.
Re-run the installer from the release checkout, elevated, with the pipeline stopped the way the
promotion stops it (when the release gate is idle and the deployer has nothing in progress):

```powershell
$root = 'C:\ProgramData\HNSHL\ContentRuntime'
Set-Location C:\Users\omokolade.LAB\relic-and-riddle                       # at the release tag
New-Item -ItemType File -Path "$root\control\Pipeline.stop" -Force | Out-Null
while ((Get-ScheduledTask RelicAndRiddle-Production-Unattended).State -eq 'Running') { Start-Sleep 3 }
(Get-ScheduledTask RelicAndRiddle-ProductionBackup-Unattended).State       # must not be Running
.\deployment\windows\Install-ContentReleaseDeployer.ps1 -WhatIf             # lists only what differs
.\deployment\windows\Install-ContentReleaseDeployer.ps1
Remove-Item "$root\control\Pipeline.stop" -Force
Start-ScheduledTask RelicAndRiddle-Production-Unattended
```

The deploy key is kept, so nothing changes on GitHub. Leaving the stop file in place keeps the
pipeline down.
