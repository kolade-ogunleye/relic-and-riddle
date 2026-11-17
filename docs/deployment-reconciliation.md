# Application reconciliation and readiness backlog

## Ownership

| Area | Owner / source of truth | Application responsibility |
| --- | --- | --- |
| Telegram commands, cards, navigation, approvals, errors | This repository | Reproduce defects in dev, fix and test; promote a reviewed release |
| Content generation, rights, quality, provider choices and spend controls | This repository and each environment's private configuration | Validate real workflow; preserve dev/prod separation and paid usage |
| Backup format, encryption, upload/resume, restore and CLI/API contract | This repository | Release the tested implementation and keep its public interface stable |
| Managed identities, host authorization/ACLs, OS/GPU exposure | Lab repository / lab operator | Specify runtime needs and exercise application behavior under the supplied identity |
| Application launchers, workload task definitions, installation and health producer | This repository; host changes use approved execution | Maintain deployment and supervise the app; preserve the telemetry interface |
| Wazuh, security-event rules, network/domain policy | Lab repository / lab operator | Supply meaningful application exit status and safe logs; consume lab acceptance evidence |
| Whole-VM/hypervisor backup and disaster recovery | Lab repository / lab operator | Maintain application recovery artifacts and dependency requirements |

Wazuh delivery is lab infrastructure acceptance, not proof of content quality,
Telegram correctness, licensed-provider generation, or approval usability.

## Verified repository state

At the start of reconciliation, application `origin` was fetched. Dev HEAD and
`origin/remediation/purge` are both `7e972ba18656254a7d23bc9dfb9f45059a51dc3b`.
Production HEAD is the same commit, tagged `v1.0.0-rc2`. Tracked application source
on production has no changes; the differences reported by git are content/media
metadata and generated runtime state. The normalized Telegram source blob is
identical in dev, production and committed HEAD; raw file hashes differ because
of line endings.

The lab agent staged its adapters under `C:\ProgramData\HNSHL\ContentRuntime`,
not by patching the application checkout. The updated lab runbook assigns those
application-specific adapters, deployment and health producers to this project.
`deployment/windows/handoff/provenance.json` records their uncommitted source with
per-file hashes; the copies themselves were deleted as obsolete (QF-47), and the
maintained launcher and its tests are `deployment/windows/runtime`. Lab source and
live resources are unchanged. Mixed platform and application deployment still needs
separation; see the handoff README.

The `feat/production-runtime-contract` candidate collects the newer backup,
recovery, startup safety, approver validation, documentation and tests. Production
still uses rc2 plus its maintenance overlay until approved application promotion.
Publishing this candidate does not prove deployment or runtime acceptance.
The candidate removes the legacy tracked database from new artifacts; local data
is retained and prior Git history is unchanged. Unrelated fact-sheet/music edits
and local evaluation files are excluded from the candidate.

## Reconciliation work

1. Build a reviewed application change set from this worktree. Separate code and
   tests from the pre-existing fact-sheet/music edits and generated data. Do not
   commit a production database, configuration, credentials or private media.
2. Document/version the application contract consumed by the lab adapter:
   explicit roots/spool/cache paths, environment selection, backup results and
   freshness, stop behavior, and process exit codes. Avoid relying on private
   credential-provider methods or duplicating application policy in launchers.
3. Preserve split identities: pipeline cannot read backup custody; only the
   backup identity runs API backup cycles. The candidate contains no in-daemon
   API backup scheduler; it supplies a separate versioned backup entry point.
4. Retire superseded production installers from the active application deployment
   path. `windows_gmsa_prepare.ps1`, the original production task installer, and
   the old interactive backup installer are historical scaffolding, not a second
   production management plane. Preserve any still-needed dev launcher separately.
5. Publish the reviewed application changes to origin and make a tested release
   artifact. Then promote the approved artifact/configuration through approved host
   contract. Do not copy the dev `.env` or state over production.

## Legacy task retirement: deployment handoff

`RelicAndRiddle-Production` and `RelicAndRiddle-ProductionBackup` are the original
interactive tasks created during initial validation. They were retained disabled
for rollback during the managed-account cutover. The no-login reboot gate has
now passed. Retire these obsolete registrations through the lab's operating
procedure, preserving an exported definition/rollback record outside the active
task scheduler as needed. Update rollback documentation and ensure configuration
management cannot recreate the obsolete registrations. Do not delete the active
`-Unattended` tasks or deliberately retained diagnostic canaries. No tasks were
deleted by this application audit.

## Application backlog, in priority order

1. **Telegram first:** collect current symptoms and affected environment, reproduce
   each problem with expected/actual behavior, and agree acceptance checks. Both
   dev and production currently contain zero rows in `issues`; this is not proof
   of no defects. Historical Markdown has contradictory status/resolution notes
   (for example the music-fetch request), so it is not a verified current backlog.
   Do not label a defect resolved solely because the database has no open rows.
2. **Release reconciliation:** finish the application/adapter boundaries and origin
   release work above without changing lab infrastructure from this repository.
3. **Application checks under `rr-pipeline$`:** the operator's 2026-10-22 receipt
   passed actual DirectML-enabled depth inference, native Azure Speech loading
   and NVENC in Session 0. Vulkan upscaling failed; the application now upscales through
   ONNX Runtime/DirectML instead ([upscaler](upscaler.md)), pending a re-probe after promotion.
   See [runtime acceptance](runtime-acceptance.md).
4. **Assets and provider path:** address the insufficient Derinkuyu photo pool;
   validate configured licensed generation providers within existing production
   caps. Authentication/quota health is not successful paid generation.
5. **End-to-end acceptance after Telegram fixes:** a full production dry run under
   the managed identity, media/rights/release checks, usable review and human
   approval. Do not activate publication merely because startup works.
6. **Promotion/activation:** deploy the reviewed release, archive fresh validation
   data while preserving paid usage/publication evidence, verify production
   settings, and obtain explicit live-activation instruction.

FFmpeg 9.0.2 compatibility is a later upgrade item; 8.1 remains pinned. Lab-owned
whole-VM recovery and infrastructure cleanup are separate handoffs, not untracked
application defects. The managed backup intentionally disables desktop sync
fallback when signed out; its API spool is the durable retry path.
