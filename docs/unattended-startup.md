# Production startup without a desktop login

Lab automation completed no-login reboot acceptance on 2026-10-22 UTC using
separate `LAB\rr-pipeline$` and `LAB\rr-backup$` identities. The authoritative
operating procedure is `docs/CONTENT_VM_TASK_IDENTITY.md` in the lab repository.

Current tasks are `RelicAndRiddle-Production-Unattended` and
`RelicAndRiddle-ProductionBackup-Unattended`. The originals are disabled rollback
artifacts; retirement is application deployment through approved host administration.
No application release should reinstall them or create another managed account.

This application supplies the [service contract](runtime-contract.md), meaningful
exit status and the [focused probe](production-probe-handoff.md). The lab owns
managed passwords, host authorization/ACLs, platform reboot acceptance and security
telemetry. This project owns workload triggers and process supervision; preserved
deployment source is in `deployment/windows/handoff`. gMSA rotation does not change the content
backup encryption key; OneDrive OAuth consent is a separate application grant.

The lab evidence proves Session 0 NVENC/API canaries, cloud upload/restore,
automatic startup and watchdog health. It does not substitute for actual depth
inference, native Speech checks, provider generation or human content approval.
See [reconciliation and readiness](deployment-reconciliation.md) for that backlog.

A desktop OneDrive client is not running when signed out. The managed backup
therefore uses the API and durable local spool, with desktop sync fallback disabled.
The pipeline remains in dry-run mode. No reboot or account migration is required
for a focused application probe under the already provisioned identity.
