# Transitional Windows runtime source

This directory preserves the application-specific source handed back by the lab
project after its 2026-10-22 ownership correction. `provenance.json` records each
file's SHA-256 and the source checkout HEAD. The files came from uncommitted
working-tree changes; HEAD alone does not identify them. Copies are byte-identical.
Nothing was removed from the lab repository or changed on the running VM.

This is migration/recovery source, not an automatic installer or an approved
deployment. The rc3 candidate at `2f00f7f` predates this handoff. Do not run play 52
or the registration scripts merely to collect application acceptance evidence.

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

## Tests and migration

From this directory, run the original adapter assertions unchanged:

```powershell
python -m unittest discover -s tests/python -p test_content_runtime.py -v
Invoke-Pester -Path tests/unit/tools/ContentWorkloadProcess.Tests.ps1,tests/unit/tools/ContentRuntimeHealth.Tests.ps1,tests/unit/tools/ContentLegacyStop.Tests.ps1
```

The mixed identity-probe and Ansible tests are retained intact. They still refer
to lab-owned tools and `site.yml`, so are not standalone application tests. Split
their application assertions rather than weakening them for an isolated run.

The old adapter retains maintenance-overlay imports and private MSAL coupling.
The maintained `scripts/service_runtime.py` contract replaces those policies.
Migrate the launcher while preserving identity checks, process-tree supervision,
stop semantics, overlap protection, receipts and event integration. Promote only
after offline tests and managed-account probes; do not overwrite the live launcher
from this preserved snapshot.
