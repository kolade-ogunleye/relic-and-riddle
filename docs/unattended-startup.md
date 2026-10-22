# Production startup without a desktop login

Lab automation completed no-login reboot acceptance on 2026-10-22 UTC using
separate `LAB\rr-pipeline$` and `LAB\rr-backup$` identities. The authoritative
operating procedure is `docs/CONTENT_VM_TASK_IDENTITY.md` in the lab repository.

Current tasks are `RelicAndRiddle-Production-Unattended` and
`RelicAndRiddle-ProductionBackup-Unattended`. The originals are disabled rollback
artifacts; retiring their registrations is a lab-owned configuration change.
No application release should reinstall them or create another managed account.

This application supplies the [service contract](runtime-contract.md), meaningful
exit status and the [focused probe](production-probe-handoff.md). The lab owns
managed passwords, ACLs, startup triggers, process supervision, reboot acceptance,
and security telemetry. Its gMSA password rotation does not change the content
backup encryption key; OneDrive OAuth consent is a separate application grant.

The lab evidence proves Session 0 NVENC/API canaries, cloud upload/restore,
automatic startup and watchdog health. It does not substitute for actual depth
inference, native Speech checks, provider generation or human content approval.
See [reconciliation and readiness](deployment-reconciliation.md) for that backlog.

A desktop OneDrive client is not running when signed out. The managed backup
therefore uses the API and durable local spool, with desktop sync fallback disabled.
The pipeline remains in dry-run mode. No reboot or account migration is required
for a focused application probe under the already provisioned identity.
