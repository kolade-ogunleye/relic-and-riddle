# Application service contract, version 1

The external lab launcher is an OS supervisor: it chooses the Windows identity,
sets the approved executable PATH, supervises the process tree, and records exit
status. Its implementation and workload task definitions belong to this project.
Managed-account policy, host authorization/ACLs and security telemetry remain lab
responsibilities. Host changes use approved administrative execution.

The application-owned entry point is:

```powershell
python scripts/service_runtime.py contract
python scripts/service_runtime.py pipeline --settings C:\approved\launch.json
python scripts/service_runtime.py backup-check --settings C:\approved\launch.json
python scripts/service_runtime.py backup-cycle --settings C:\approved\launch.json
python scripts/service_runtime.py backup-restore --settings C:\approved\launch.json --snapshot-id production-YYYYMMDDTHHMMSSZ-0123456789ab
```

These are separate processes/identities. `pipeline` retains `daemon --dry-run` and
does not schedule API backups, load backup credentials or change paid-provider
flags. The backup process alone reads its custody files. None of these commands
register tasks, change ACLs, select a Windows account, send operator messages or
enable live publication.

The manifest accepts the existing lab adapter's nine fields:
`app_root`, `maintenance_root`, `runtime_root`, `backup_key_file`,
`source_token_cache`, `token_cache`, `legacy_archive`, `client_id`, and
`expected_bot_id`. All path fields are absolute. Optional `contract_version`
defaults to 1 for the existing manifest; unsupported versions/unknown fields are
rejected. The manifest contains paths and public identification, never raw keys,
passwords or tokens. `maintenance_root` is accepted for migration compatibility;
the released app ships its own backup modules and does not inject the maintenance
overlay into its import path.

`RELIC_APP_ROOT` is set before application config import so the app's own loader
reads only the explicitly selected root's configuration. Already loaded config
from another root is rejected. Production bot identity is checked against the
manifest before running an operation. Configuration is changed only inside that
process; `.env` and dev settings are not rewritten.

Backup uses `runtime_root/spool/Backup` and its separate managed token cache.
Public `OneDriveAuth.export_cache()` provisions a distinct encrypted cache from
the application's enrollment custody. It locks the destination, leaves enrollment
unchanged, never replaces an existing cache, and does not require custody-folder
write permission. Launchers must not manipulate MSAL private state or print token
values. An expired/revoked grant requires explicit application enrollment.

`backup-cycle` captures at most one full snapshot per UTC day and retries queued
uploads. Its v1 JSON result includes `cycle_verified`, `state`, `pending_jobs`,
`latest_snapshot_id` and `verification`. Success requires no pending jobs, no busy
worker result and a downloaded-SHA-256-verified snapshot captured within 26 hours
(at most five minutes in the future for clock skew). This policy now lives in
`backup_runtime.cycle_summary()` rather than in a host launcher. Existing
`run_cycle()` list results remain compatible with the deployed lab adapter.

Exit 0 means the requested check/cycle/restore succeeded; exit 2 means attention
is required. For `pipeline`, the external supervisor must also observe unexpected
process exit; an exit code alone is not ongoing daemon health. SDK errors return
exception type, not raw network/credential strings. `backup-check` proves API
access only; it does not imply an upload or restore passed. Restore always fetches
from the cloud and creates a new guarded recovery directory, never overwriting
the running app.

Migration is an application-owned reviewed launcher change after artifact release,
executed through approved host administration:
call this entry point from the existing process supervisor and preserve its
identity, stop/parent monitoring, overlap prevention and startup checks. The
current deployed adapter remains compatible until that controlled promotion.
