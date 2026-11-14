# OneDrive backup and recovery

Current production update (2026-10-22 UTC): lab automation now runs backup as
`LAB\rr-backup$` using `RelicAndRiddle-ProductionBackup-Unattended`, without a
desktop login. The original interactive backup task is disabled. The protected
runtime uses `C:\ProgramData\HNSHL\ContentRuntime\spool\Backup` and a separate
service-owned encrypted token cache. The recovery key custody and API destination
remain unchanged. A fresh snapshot `production-20261022T014910Z-5f996e76f628`
passed downloaded SHA-256 and an isolated 429-file restore under this identity.
Desktop sync fallback is **disabled** for the managed service; pending API jobs
remain in its durable local spool. Earlier enabled-desktop-fallback and
interactive-task instructions describe the superseded deployment. Use the lab's
`docs/CONTENT_VM_TASK_IDENTITY.md` for current task management and recovery.

“Desktop copy” means copying into the machine's OneDrive sync folder. That
requires the OneDrive desktop client and its signed-in session. A successful
copy proves local staging, not cloud synchronization.

The optional `onedrive-api` transport uploads directly to Microsoft Graph,
without the desktop client. It keeps encrypted snapshots locally until upload
and downloaded SHA-256 verification complete. An optional desktop-folder copy
is an additional fallback; it never clears a failed API upload or proves cloud
readiness. Existing environments default to their existing `sync-folder` mode.

Confirmed production policy: API primary, desktop OneDrive sync folder fallback.
Requested production destination: `OneDrive/Backups/Content Creation/Relic & Riddle`,
with encrypted snapshots under `production/snapshots`. This custom location needs
`ONEDRIVE_SCOPE=files` and
`ONEDRIVE_BACKUP_PATH=Backups/Content Creation/Relic & Riddle` (forward slashes,
relative to the cloud drive root; do not include `OneDrive` or a Windows drive).
Microsoft's delegated `Files.ReadWrite` permission covers the signed-in user's
files broadly; the configured path is an application behavior limit, not a
folder-scoped Microsoft permission. Review the new browser consent accordingly.
The existing app-folder authorization cannot access this custom destination.
Enrollment and the real cloud restore drill must finish before changing the
running production configuration. Development keeps its current configuration.

## Register and authorize a personal Microsoft account

1. In the Microsoft Entra admin center, open **App registrations > New
   registration**. Name it `Relic and Riddle backup`, and select support for
   **personal Microsoft accounts** (or organizational and personal accounts).
   Registration requires access to an Entra directory; a Microsoft 365 Personal
   subscription alone does not supply an organizational directory.
2. Copy the **Application (client) ID**. This is public identification, not a
   password. Under **Authentication**, enable **Allow public client flows**.
   Save the setting. **Redirect URI:** leave blank for this device-code flow.
   **Live SDK support:** not required by this MSAL/Microsoft Graph integration;
   leave disabled for this dedicated app. Do not create a client secret or enable
   implicit access-token/ID-token grants.
3. Under **API permissions**, add Microsoft Graph **delegated** permission
   `Files.ReadWrite.AppFolder`. Adding the permission is not the personal
   account's authorization; the browser consent steps below perform that.

The [desktop app configuration reference](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration)
confirms that device-code flow needs a public client and no redirect URI.

Microsoft documents [app-folder access](https://learn.microsoft.com/en-us/graph/onedrive-sharepoint-appfolder)
and [device-code sign-in in MSAL Python](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens).
There is a [reported personal-account AppFolder 403 issue](https://github.com/OneDrive/onedrive-api-docs/issues/1930).
If encountered, investigate first. The explicit alternative `ONEDRIVE_SCOPE=files`
requires delegated `Files.ReadWrite` and a new sign-in consenting to broader
drive access. The application never widens permission automatically.

Run enrollment under the Windows account that will run the production process.
These PowerShell settings affect only the current shell and its child processes;
they do not modify either environment's `.env`:

```powershell
$env:RELIC_ENVIRONMENT = 'production'
$env:BACKUP_TRANSPORT = 'onedrive-api'
$env:BACKUP_ENABLED = 'true'
$env:ONEDRIVE_CLIENT_ID = '<application-client-id>'
$env:ONEDRIVE_SCOPE = 'appfolder'
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production keygen
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production auth
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production check
```

For the requested custom production location, set these two process settings
before running `auth` and the subsequent commands instead of the appfolder scope:

```powershell
$env:ONEDRIVE_SCOPE = 'files'
$env:ONEDRIVE_BACKUP_PATH = 'Backups/Content Creation/Relic & Riddle'
```

The permission can be added under **API permissions > Microsoft Graph > Delegated
permissions > Files.ReadWrite**. The explicit `auth` command requests consent for
that scope and saves a separate `tokens-files.enc` cache; it does not reuse or
overwrite the earlier app-folder token cache. Existing content in either folder
is retained; backup setup does not move or delete it.

The production commands require this version of the backup tooling and its
dependencies on the VM. Do not run production snapshots from the dev checkout.
Registration itself can be completed from your usual browser on any computer.

To authorize the storage owner's personal account:

1. Run `auth` above and keep its terminal open. It prints a Microsoft verification
   URL and a short-lived device code, then waits for browser sign-in.
2. Open the printed URL (normally `https://microsoft.com/devicelogin`) in a private
   browser window. Enter the code from that command.
3. Sign in with the **personal Microsoft account that owns the OneDrive**.
   If Microsoft offers work/school versus personal accounts for the same email,
   choose **Personal account**. This can differ from your Entra administrator
   account. An existing administrator browser session does not grant access to
   the personal OneDrive.
4. Check that the application name matches your new registration, review the
   requested access, and accept. Complete any Microsoft confirmation prompts.
5. Return to the terminal. The command saves an encrypted token cache and checks
   the app folder. Run `check` to confirm access without another interactive
   sign-in. If the code expires, rerun `auth` for a fresh code.

You do not enter a OneDrive folder path or link your personal storage inside the
Entra portal. The account chosen during this sign-in determines the cloud drive.
For production, authorize under the Windows identity that runs production so its
cache and file permissions belong to that process. A browser on your workstation
can complete a device-code request started on the VM.

Never send
passwords, access tokens, or refresh tokens in chat. The recovery key and encrypted
token cache default to `$HOME/.relic-and-riddle/production/backup/`, outside the
checkout. Keep an independently secured copy of `recovery.key`; losing it makes
the encrypted archives unrecoverable. Key generation refuses to replace a key.
Development uses a separate namespace; never copy its `.env` or state to production.

An independent production recovery-key copy was saved on the workstation at
`$HOME/.relic-and-riddle/production-recovery/recovery.key`, outside the development
checkout, with file access restricted to its Windows owner and SYSTEM. Its key
fingerprint matches production. It is not a dev credential and is not part of
the uploaded archive. Keep this copy available when recovering onto another host.

After enrollment and a successful restore drill, add only the backup settings to
the production configuration and restart its process. Leave dev configuration,
Telegram identity, provider flags, and spend caps unchanged. API retry scheduling
runs through the separate backup worker, scheduled by the host. The cycle captures
at most one full snapshot per UTC day, an hourly verified copy of the database
(below) and retries pending uploads; explicit `snapshot` commands can capture
additional state before deployments or after important changes. Neither cadence
is a zero-data-loss transaction journal.
For the confirmed fallback policy, also set `BACKUP_SYNC_FOLDER_FALLBACK=true`
and keep production's existing `BACKUP_ONEDRIVE_DIR` pointing to its real synced
OneDrive directory. Do not use a guessed folder on a replacement host; provision
the desktop client before enabling that optional fallback there.

Production task registration is owned by lab automation. The earlier interactive
backup installer has been retired from this application release. The application
daemon does not run API backup cycles: its account cannot read backup custody.
Use the separate backup worker through the [service contract](runtime-contract.md).

## Verify upload and recovery before activation

```powershell
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production snapshot
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production sync
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production status
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production list-cloud
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production restore '<snapshot-id>' --cloud --destination 'C:\Recovery\relic-isolated-drill'
```

The destination must not exist. `--cloud` always downloads from OneDrive and
fails if the cloud is unavailable; it does not substitute the staged local copy.
Without `--cloud`, restore tests only the local encrypted snapshot. A verified
upload alone does not prove restoration. API readiness requires verified uploads,
no pending jobs, reachable OneDrive, and a cloud-sourced restore within 30 days.

Snapshots contain an online SQLite backup, application source, persistent data,
assets, fonts, and output. They exclude actual `.env` files, credentials, virtual
environments, and downloaded tools/models. Provision those dependencies and
production secrets separately during recovery. Snapshots are full copies, not
incremental; check OneDrive quota (cloud copies are never deleted by the worker).

## Hourly database copies, retention and freshness

Each worker cycle (the backup task repeats every 10 minutes) also takes an hourly
copy of `data/pipeline.db` with SQLite's online backup API, so committed rows still
in the `-wal` file are included while the pipeline writes. The copy is an archive
of the same format (schema 1, same key, `kind: database`, only `data/pipeline.db`)
in `<spool>/sqlite-hourly`. It counts only after a test restore: decryption, every
manifest hash, SQLite integrity and foreign keys, and the restored database's
schema digest and per-table row counts equal to the staged copy's. A copy that
fails stays as `failed` evidence (only the newest one) and the next attempt waits
an hour. Hourly copies stay on this host: they narrow the database recovery point
to an hour against local damage; the daily cloud archive remains the off-machine
recovery point, and whole-VM recovery stays a lab responsibility.

```powershell
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production status --hourly
.\.venv\Scripts\python.exe scripts\backupctl.py --environment production restore '<snapshot-id>' --database-only --destination 'C:\Recovery\relic-db-drill'
```

Retention runs at the end of each cycle and never touches OneDrive:
`BACKUP_DATABASE_KEEP` (default 48, two days) verified hourly copies, re-hashed before
they count, and `BACKUP_SNAPSHOT_KEEP` (default 7) cloud-verified daily archives, plus
the one holding the latest drill receipt. Pending, failed, damaged, other-environment,
linked or unexplained content is never deleted. Restores and retention share one
lock, so an archive cannot be pruned while it is restored.

Drill one of the newest kept daily snapshots (`status` lists them). The drill's
receipt is written into that snapshot's local spool directory. A drill of a snapshot
whose local copy was already pruned still restores from the cloud, but it records
nothing locally, so "last drill" and readiness stay overdue.

The worker writes a redacted receipt (`BACKUP_STATUS_FILE`, default
`data/backup-status.json`: timestamps and counts only) after every cycle and drill.
The pipeline reads only that file. In production, `/status` and the daily heartbeat
show "Last snapshot X h / last drill N d / hourly copy M min"; the dashboard shows a
line, and Needs you gets one notice a day, while any of these holds: no
cloud-verified snapshot in 26 h ("backup stale"), no cloud restore drill in 30 days
("restore drill overdue"), no verified hourly copy in 3 h ("hourly copy stale"), or
the worker's last cycle named a failed stage ("backup worker fault (retention)" or
"(hourly copy)"). A missing or unreadable receipt alerts as unknown. Development has
no backup worker and shows nothing.

In production the receipt depends on a lab-owned grant: the backup account
(`LAB\rr-backup$`) may create files in the application's `data/` folder (CreateFiles,
with CREATOR OWNER Modify on what it creates; the grant SQLite readers already need
for their sidecars). Without that grant the worker cannot write the receipt, and the
pipeline shows "no status from the backup worker" and alerts. The application does
not change that ACL; a missing grant is raised with the lab.

Restore validates encryption, inventory, file hashes, SQLite integrity, and foreign
keys before promoting the directory. It preserves publication IDs, usage history,
approvals, and unknown publication outcomes. Paths are relocated with original
evidence retained under `.recovery`; those artifacts need revalidation. The
`.restore-isolated` marker blocks normal startup through a guarded `main.py`,
including snapshots of older releases. The original entry point is preserved at
`.recovery/main.original.py`. Current application publisher code also checks the
marker; older archived provider modules must be reviewed before direct use. Inspect
the restore report, validate artifacts, reconcile publication state, provision
production-only configuration, and make an explicit recovery decision before
removing that marker. Never start a restored duplicate alongside production.

The current automatic approval backup occurs before publication. Publication or
scheduling changes after that snapshot require another snapshot; this is not a
zero-data-loss journal. Take an explicit snapshot after validation and before
deployment, migration, or activation.

Development rehearsal on 2026-10-21: encrypted snapshot
`development-20261021T182158Z-ea779a5fe84a` restored 778 files (1,413,527,577-byte
encrypted archive), validated file checksums and SQLite integrity, and retained
the isolation marker. Evidence is under
`archive/recovery/development-20261021T182158Z-ea779a5fe84a/.recovery/restore-report.json`.
This was an offline workstation drill, not production or cloud verification.

For desktop fallback, set `BACKUP_SYNC_FOLDER_FALLBACK=true` and configure
`BACKUP_ONEDRIVE_DIR` to the actual locally synced OneDrive directory. API failure
then mirrors encrypted data into `api-fallback/<snapshot-id>`. Desktop syncing is
still dependent on that machine's OneDrive session and remains cloud-unverified.

## Production verification completed 2026-10-21

- Snapshot: `production-20261021T201035Z-7b9467ab94e1`, 429 files,
  587,481,880 encrypted bytes.
- Cloud destination: `Backups/Content Creation/Relic & Riddle/production/snapshots`.
- Uploaded file was independently downloaded and matched SHA-256
  `a7f5a81509b55c2b482895db06c03c2da048b8b13defd160b775c1dce21b0222`.
- A separate `--cloud` restore passed at 20:34:24 UTC. All 429 file checksums,
  encryption authentication, and SQLite integrity passed. The restored launcher
  refused startup before loading archived application code.
- Restore retained 62 LLM usage rows ($0.898979), seven media usage rows ($5.16),
  two settings, and the empty production run/approval/publication/posting queues.
- Reports are on the VM beneath
  `$HOME/relic-recovery-drills/production-20261021T201035Z-7b9467ab94e1/.recovery/`:
  `restore-report.json`, `operational-check.json`, `desktop-fallback-probe.json`,
  and `cloud-cleanup.json`.
- API readiness returned true. The separate hidden
  `RelicAndRiddle-ProductionBackup` task is enabled and its recurring execution
  completed with exit code 0. Daily snapshot capture and ten-minute retries use
  production's own credential namespace. This task still requires login.
- Desktop fallback is enabled at the requested local OneDrive path. A small
  encrypted write/read/hash probe passed and its probe files were removed.
  This proves local fallback writes; it does not claim desktop cloud sync.
- The two zero-byte failed-upload placeholders were removed after successful
  cloud verification and restoration. The verified snapshot and restore evidence
  were retained. The recovery copy is outside the active app checkout.
- Production's `.env` SHA-256 remains
  `1c0c218e36e6b35b8847968a5a2cd35f232bca16981973955fd199d2da0da737`.
  The production application task remained running; dev configuration was not
  changed. This backup verification does not activate live publishing.
- Validation: 651 full-suite tests passed before the download retry change;
  all 37 focused backup/auth/recovery tests passed after that change. Live cloud
  testing exposed the personal-content host and interruption cases, now covered
  by transfer tests and the successful cloud restore.
