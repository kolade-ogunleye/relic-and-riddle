# Environment boundaries

- This checkout on the workstation is **development**. The checkout on
  `content-client-win-01.lab.internal` is **production**.
- Preserve each environment's existing `.env`. Never deploy the workstation's
  `.env`, database, usage counters, approvals, or posting queue to production.
- Development uses `@relicandriddle_dev_bot` (8701645452); production uses
  `@relicandriddle_bot` (8837268021). Verify effective identity before starting a
  bot. Do not stop one environment's bot to resolve another's conflict without
  establishing which token the process actually loaded.
- Keep development's existing feature flags and spending settings unchanged.
  Production uses its own configured licensed providers and existing spending
  caps. Do not equate dry-run mode with zero API cost.
- Production remains in validation/dry-run mode until explicitly activated.
  Archive validation runs before activation; retain all paid usage records,
  publication evidence, operator settings, and backups.
- See `docs/deployment.md` for provisioning, validation, promotion, and rollback.
- Production startup was migrated through the lab's controlled automation to
  `RelicAndRiddle-Production-Unattended` (`LAB\rr-pipeline$`) and
  `RelicAndRiddle-ProductionBackup-Unattended` (`LAB\rr-backup$`). The original
  interactive tasks are retained disabled. Do not re-enable or reinstall those
  legacy tasks alongside the managed tasks. See the lab repository's
  `docs/CONTENT_VM_TASK_IDENTITY.md` for authoritative runtime management.
- This repository owns the application, Telegram behavior and backup format/API.
  Lab-owned managed identities, ACLs, scheduled-task lifecycle, Wazuh/security
  telemetry, domain/network policy and whole-VM recovery are separate operating
  responsibilities. Do not treat infrastructure acceptance as application
  acceptance or change lab controls as an application fix. See
  `docs/deployment-reconciliation.md` for the current ownership and backlog.
- Address Telegram defects before an end-to-end production episode/approval run.
  Focused runtime/provider acceptance checks and release reconciliation may be
  performed independently when requested. An empty issue table or an old
  "resolved" note is not proof that current reported defects are fixed.
