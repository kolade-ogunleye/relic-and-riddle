# Transitional Windows runtime source (record only)

This directory recorded the application-specific source handed back by the lab
project after its 2026-10-22 ownership correction. `provenance.json` records each
file's SHA-256 and the source checkout HEAD. The files came from uncommitted
working-tree changes; HEAD alone does not identify them.
Nothing was removed from the lab repository or changed on the running VM.

**The launcher copies are gone (QF-47, 2026-11-05).** The `tools/` and `tests/`
copies were obsolete: their adapter still started `daemon --dry-run`, while
production has posted live since the operator's activation on 2026-10-25, and CI
tested that copy instead of the one production installs. The maintained launcher
is `deployment/windows/runtime/` (the files `Install-ContentReleaseDeployer.ps1`
installs), with its tests in `deployment/windows/runtime/tests/`. Git history keeps
the deleted bytes; `provenance.json` still names them and the lab's hashes. Play 52
remains here as a record and references the deleted `tools/`: do not run it.

## Boundary

The application owns its launcher, workload task definitions, backup adapter,
application-health producer and release procedure. The lab supplies managed
identities, host authorization/resource ACLs, OS/GPU compatibility and approved
administrative execution. Wazuh collection/rules stay in the lab. Windows event
provider/IDs are an integration contract; do not silently rename them.

`ContentProbeProcess.ps1` is a copied shared dependency, not a transfer of lab
host-probe ownership. Play 52 mixes application deployment with host authorization
and event-source setup. Split those responsibilities before maintaining it as
application deployment. Its lab inventory, controller credentials and collections
are not supplied here. Never copy Vault material or production credentials here.

## Tests

From `deployment/windows/runtime`, as CI does:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
Invoke-Pester -Path tests/unit
```

## Production bindings (moved from lab inventory, 2026-10-22)

These non-secret paths were application settings in the lab's
`host_vars/<production-host>.yml`, used only by play 52. With the source handoff
complete (lab PR #60), they are maintained here as application deployment configuration.

| Setting | Value |
| --- | --- |
| `content_app_root` | `<operator-home>\relic-and-riddle` |
| `content_maintenance_root` | `<operator-home>\relic-ops\backup-maintenance-c293e863` |
| `content_python_base` | `<operator-home>\AppData\Local\Programs\Python\Python311` |
| `content_backup_custody` | `<operator-home>\.relic-and-riddle\production\backup` |
| `content_legacy_archive` | `<operator-home>\OneDrive\Backups\Created Contents\RelicAndRiddle` |
| `content_unattended_enabled` | `true` (API, GPU and cloud-restore gates passed; legacy tasks stopped 2026-10-22 UTC) |

`content_ffmpeg_bin` (FFmpeg 8.1) stays in lab inventory because lab play 51 uses it.
