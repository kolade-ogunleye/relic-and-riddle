# Development and production deployment

The workstation checkout is development; the VM is production. Their private
configuration, Telegram identities, queues, approvals and usage ledgers are
independent. Never deploy dev's `.env` or database to production. Dev feature
flags remain unchanged; production retains its configured licensed providers and
existing caps. A dry run can still consume API credits.

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
development only and refuses production actions.

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
