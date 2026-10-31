#Requires -Version 5.1
<#
.SYNOPSIS
Installs new signed releases on content-client-win-01 without an administrator.
.DESCRIPTION
Starts every minute as LAB\rr-deploy$ (task RelicAndRiddle-ReleaseDeployer-Unattended) and checks
GitHub every 2 minutes, or at once after /deploy in the production bot. Its installed copy lives
in ContentRuntime\tools\deploy, which this account can read but not change.

1. Fetches release tags (v1.2.3 / v1.2.3-rcN) from GitHub over SSH with a read-only deploy key.
   Nothing connects to the VM; the key can read the repository and nothing else.
2. Takes the newest tag newer than the running one only if it carries an SSH signature from the
   pinned signer (the operator's smartcard). Unsigned or wrongly signed tags are refused.
3. Runs the offline suite for the release in a separate worktree while production keeps running.
4. Waits until production is idle (scripts/release_gate.py: no run in flight, no open review, no
   post due, no daemon job, clear of the daily run; and no backup running).
5. Holds the pipeline through its launcher (control\Pipeline.hold): the worker tree stops while
   the managed task keeps running. Checks the release out, installs changed requirements, then
   asks the launcher to run the suite under LAB\rr-pipeline$ with production's own settings.
6. On a pass it releases the hold, the launcher restarts the daemon on the new release, and the
   daemon must still be running 3 minutes later. On any failure the tree returns to the previous
   release, the hold is released and the tag is not retried.

Every outcome is written to control\release.json (the daemon reports it in Telegram) and to the
Application event log (5010 installed, 5011 refused or rolled back). It never reads .env (its
account is denied it), never changes identities, ACLs, tasks or the backup, and never changes the
pipeline's launch mode (live since 2026-10-25). A tree not at a release tag (a manual change) pauses it.
#>
[CmdletBinding()]
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'The release deployer is restricted to CONTENTCLIENTWI.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity -ine 'LAB\rr-deploy$') { throw 'Release deployer identity mismatch.' }

. (Join-Path $PSScriptRoot 'lib/ContentReleaseDeployer.ps1')
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent   # ContentRuntime\tools\deploy -> ContentRuntime
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
$settings = Get-Content -LiteralPath (Join-Path $root 'deploy.json') -Raw | ConvertFrom-Json
$app = [string]$manifest.app_root
$python = Join-Path $app '.venv\Scripts\python.exe'
$git = [string]$settings.git
$logs = Join-Path $root 'logs\Deploy'
$statePath = Join-Path $logs 'state.json'
$statusPath = Join-Path $logs 'status.json'
$rejectedPath = Join-Path $logs 'rejected.json'
$logPath = Join-Path $logs 'deployer.log'
$holdPath = Join-Path $root 'control\Pipeline.hold'
$verifyPath = Join-Path $root 'control\Pipeline.verify'
$stopPath = Join-Path $root 'control\Pipeline.stop'
$receiptPath = Join-Path $root 'control\release.json'
$holdStatePath = Join-Path $root 'logs\Pipeline\Hold.json'
$runRecordPath = Join-Path $root 'logs\Pipeline\Run.pid.json'
$backupRecordPath = Join-Path $root 'logs\Backup\Run.pid.json'
$staging = Join-Path $root 'staging'
$checkPath = Join-Path $logs 'last-check.json'
$requestPath = Join-Path $root 'logs\Pipeline\deploy.request'

function Write-Log([string]$Text) {
    if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 5MB) {
        Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
    }
    Add-Content -LiteralPath $logPath -Value ('{0:o} {1}' -f [datetime]::UtcNow, $Text) -Encoding UTF8
}
function Set-Status([string]$Text, [string]$Tag = '') {
    Write-ContentJson -Path $statusPath -Value ([ordered]@{ AtUtc = [datetime]::UtcNow.ToString('o'); Status = $Text; Tag = $Tag })
    Write-Log $Text
}
function Write-Event([int]$Id, [string]$Text) {
    $type = if ($Id -eq 5010) { 'Information' } else { 'Error' }
    try { Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId $Id -EntryType $type -Message $Text }
    catch { Write-Log "Could not write event ${Id}: $_" }
}
function Get-Rejected {
    $data = Read-ContentJson -Path $rejectedPath
    if (-not $data) { return @() }
    return @($data.PSObject.Properties.Name)
}
function Add-Rejected([string]$Tag, [string]$Reason) {
    $data = [ordered]@{}
    $existing = Read-ContentJson -Path $rejectedPath
    if ($existing) { foreach ($p in $existing.PSObject.Properties) { $data[$p.Name] = $p.Value } }
    $data[$Tag] = [ordered]@{ Reason = $Reason; AtUtc = [datetime]::UtcNow.ToString('o') }
    Write-ContentJson -Path $rejectedPath -Value $data
}
function Write-Receipt([string]$Tag, [string]$Outcome, [string]$Previous, [string]$Running, [string]$Reason = '') {
    Write-ContentJson -Path $receiptPath -Value ([ordered]@{
        tag = $Tag; outcome = $Outcome; previous = $Previous; running = $Running; reason = $Reason
        at = [datetime]::UtcNow.ToString('o')
    })
}
function Get-HoldState { Read-ContentJson -Path $holdStatePath }
function Get-TagAt([string]$Commit) {
    $r = Invoke-ContentGit -Git $git -App $app -Arguments @('describe', '--exact-match', '--tags', '--match', 'v*', $Commit)
    if ($r.ExitCode -eq 0) { return $r.Stdout.Trim() }
    return ''
}
function Request-Hold([string]$Tag, [string]$Commit) {
    # Returns once the launcher confirms the worker tree has stopped.
    $since = [datetime]::UtcNow
    Set-Content -LiteralPath $holdPath -Value "$Tag $Commit" -Encoding ASCII
    $held = Wait-ContentCondition -Minutes 6 -Condition {
        $s = Get-HoldState
        if ($s -and $s.State -in 'held', 'verifying', 'verified' -and (ConvertTo-ContentUtc $s.AtUtc) -ge $since.AddSeconds(-1)) { $s }
    }
    return [bool]$held
}
function Clear-Hold {
    Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $holdPath -Force -ErrorAction SilentlyContinue
}
function Restore-Previous($State, [string]$Reason) {
    # Back to the release that was running. If that fails too, the hold stays: a half-updated tree
    # must not run. The launcher alerts after 3 hours held; the event below alerts at once.
    Write-Log "Restoring $($State.previous_tag) ($($State.previous)): $Reason"
    try {
        if (-not (Test-Path -LiteralPath $holdPath)) { [void](Request-Hold -Tag $State.tag -Commit $State.previous) }
        $head = (Invoke-ContentGit -Git $git -App $app -Arguments @('rev-parse', 'HEAD')).Stdout.Trim()
        if ($head -ne $State.previous) { [void](Update-ContentTree -Git $git -App $app -Target $State.previous -Python $python) }
    } catch {
        Write-Event 5001 "Release $($State.tag) failed and the previous release could not be restored; the pipeline stays held. $_"
        Set-Status "Restore failed; pipeline held for manual recovery: $_" $State.tag
        Write-ContentJson -Path $statePath -Value ([ordered]@{ phase = 'stuck'; tag = $State.tag; target = $State.target;
            previous = $State.previous; previous_tag = $State.previous_tag; at = [datetime]::UtcNow.ToString('o') })
        exit 1
    }
    Add-Rejected -Tag $State.tag -Reason $Reason
    Write-Receipt -Tag $State.tag -Outcome 'rolled_back' -Previous $State.previous_tag -Running $State.previous_tag -Reason $Reason
    Clear-Hold
    Write-ContentJson -Path $statePath -Value ([ordered]@{ phase = 'done'; tag = $State.tag; at = [datetime]::UtcNow.ToString('o') })
    Write-Event 5011 "Release $($State.tag) was not installed and production is back on $($State.previous_tag): $Reason"
    Set-Status "Rolled back to $($State.previous_tag): $Reason" $State.tag
}

$mutex = [Threading.Mutex]::new($false, 'Global\RelicAndRiddle-ReleaseDeployer')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    $fetchEnvironment = @{ GIT_SSH_COMMAND = ('"{0}" -i "{1}" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="{2}"' -f
        ([string]$settings.ssh).Replace('\', '/'), (Join-Path $root 'secrets\Deploy\github_ed25519').Replace('\', '/'),
        (Join-Path $PSScriptRoot 'github_known_hosts').Replace('\', '/')) }

    # An interrupted deployment is finished first: back to the release that was running.
    $state = Read-ContentJson -Path $statePath
    if ($state -and $state.phase -eq 'stuck') { Set-Status 'Held after a failed restore; waiting for manual recovery.' $state.tag; exit 1 }
    if ($state -and $state.phase -notin 'done', '') {
        Restore-Previous $state "the deployer was interrupted while $($state.phase)"
        exit 1
    }
    if (Test-Path -LiteralPath $holdPath) { Clear-Hold; Write-Log 'Removed a hold left without a deployment in progress.' }
    if (Test-Path -LiteralPath $stopPath) { Set-Status 'The pipeline is stopped for a manual procedure; not deploying.'; exit 0 }

    # The task starts every minute. GitHub is checked every check_minutes, or at once when the
    # operator sent /deploy (the daemon leaves a request in its own log folder; this account can
    # read it but not change it, so a handled request is remembered here instead).
    $last = Read-ContentJson -Path $checkPath
    $request = Read-ContentJson -Path $requestPath
    $lastRequest = if ($last) { ConvertTo-ContentUtc $last.request_at } else { [datetime]::MinValue }
    $requested = $request -and (ConvertTo-ContentUtc $request.at) -gt $lastRequest
    $due = -not $last -or (ConvertTo-ContentUtc $last.at) -lt [datetime]::UtcNow.AddMinutes(-[double]$settings.check_minutes)
    if (-not $requested -and -not $due) { exit 0 }
    $handled = if ($requested) { [string]$request.at } elseif ($last) { [string]$last.request_at } else { '' }
    Write-ContentJson -Path $checkPath -Value ([ordered]@{ at = [datetime]::UtcNow.ToString('o'); request_at = $handled })
    if ($requested) { Write-Log "Deployment check requested from Telegram at $($request.at)." }

    $fetch = Invoke-ContentGit -Git $git -App $app -Environment $fetchEnvironment -TimeoutSeconds 300 -Arguments @(
        'fetch', '--no-tags', '--quiet', [string]$settings.repository, 'refs/tags/v*:refs/tags/v*')
    if ($fetch.ExitCode -ne 0) {
        if ($fetch.Stderr -match 'would clobber existing tag') {
            Write-Event 5011 "A release tag on GitHub differs from the one already fetched; nothing installed. $($fetch.Stderr.Trim())"
        }
        Set-Status "Could not fetch release tags (exit $($fetch.ExitCode)): $($fetch.Stderr.Trim())"
        exit 1
    }

    $head = (Assert-ContentGit (Invoke-ContentGit -Git $git -App $app -Arguments @('rev-parse', 'HEAD')) 'reading HEAD').Trim()
    $current = Get-TagAt $head
    if (-not $current) { Set-Status "The tree is at $head, not at a release tag (a manual change); automatic deployment is paused."; exit 0 }
    $tags = (Assert-ContentGit (Invoke-ContentGit -Git $git -App $app -Arguments @('tag', '--list', 'v*')) 'listing tags') -split "`n" |
        ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $tag = Select-ContentReleaseCandidate -Tags $tags -Current $current -Rejected (Get-Rejected)
    if (-not $tag) { Set-Status "Up to date on $current." $current; exit 0 }
    $target = (Assert-ContentGit (Invoke-ContentGit -Git $git -App $app -Arguments @('rev-parse', "refs/tags/$tag^{commit}")) 'resolving the tag').Trim()

    $signature = Test-ContentReleaseSignature -Git $git -App $app -Tag $tag -SignersFile (Join-Path $PSScriptRoot 'allowed_signers') `
        -Signer ([string]$settings.signer)
    if (-not $signature.Valid) {
        Add-Rejected -Tag $tag -Reason $signature.Detail
        Write-Receipt -Tag $tag -Outcome 'rejected' -Previous $current -Running $current -Reason $signature.Detail
        Write-Event 5011 "Release $tag refused: $($signature.Detail)."
        Set-Status "Refused ${tag}: $($signature.Detail)" $tag
        exit 1
    }

    # The release's own suite, beside production (its settings are not readable here; the suite
    # does not need them and the in-window check repeats it with them).
    $requirementsChanged = (Invoke-ContentGit -Git $git -App $app -Arguments @('diff', '--quiet', $head, $target, '--', 'requirements.txt')).ExitCode -ne 0
    # The suite beside production runs once per candidate commit. Without this memo it ran again on every
    # check (2-3 minutes of the VM's CPU each time, beside the run it was waiting for) for as long as
    # production stayed busy, and the status read "Testing ... beside production" long after it had passed
    # (2026-10-24, rc20 behind the daily run).
    $precheckPath = Join-Path $logs "precheck-$tag.json"
    $precheck = Read-ContentJson -Path $precheckPath
    $precheckPassed = [bool]($precheck -and ([string]$precheck.commit -eq $target) -and $precheck.passed)
    if ($precheckPassed) { Write-Log "$tag passed its suite beside production at $($precheck.at); not repeated." }
    if (-not $requirementsChanged -and -not $precheckPassed) {
        $tree = Join-Path $staging $tag
        if (Test-Path -LiteralPath $tree) {
            [void](Invoke-ContentGit -Git $git -App $app -Arguments @('worktree', 'remove', '--force', $tree))
            Remove-Item -LiteralPath $tree -Recurse -Force -ErrorAction SilentlyContinue
        }
        Assert-ContentGit (Invoke-ContentGit -Git $git -App $app -Arguments @('worktree', 'add', '--detach', $tree, $target)) 'creating the check worktree' | Out-Null
        try {
            Set-Status "Testing $tag beside production." $tag
            $suite = Invoke-ContentNative -FilePath $python -ArgumentList @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-n', '4') -WorkingDirectory $tree `
                -Environment @{ PATH = (Join-Path $root 'bin') + ';' + $env:PATH } -RemoveEnvironment @('RELIC_ENVIRONMENT') -TimeoutSeconds 2400
            Set-Content -LiteralPath (Join-Path $logs "precheck-$tag.log") -Value ($suite.Stdout + $suite.Stderr) -Encoding UTF8
        } finally {
            [void](Invoke-ContentGit -Git $git -App $app -Arguments @('worktree', 'remove', '--force', $tree))
        }
        if ($suite.ExitCode -ne 0) {
            $reason = "its test suite failed beside production (exit $($suite.ExitCode)); see logs\Deploy\precheck-$tag.log"
            Add-Rejected -Tag $tag -Reason $reason
            Write-Receipt -Tag $tag -Outcome 'rejected' -Previous $current -Running $current -Reason $reason
            Write-Event 5011 "Release $tag refused: $reason."
            Set-Status "Refused ${tag}: $reason" $tag
            exit 1
        }
        Write-ContentJson -Path $precheckPath -Value ([ordered]@{ tag = $tag; commit = $target; passed = $true; at = [datetime]::UtcNow.ToString('o') })
    } elseif ($requirementsChanged) {
        Write-Log "$tag changes requirements; its suite runs after they are installed, inside the window."
    }

    # The gate comes from the running release (trusted), never from the candidate.
    $gateScript = Join-Path $app 'scripts\release_gate.py'
    if (-not (Test-Path -LiteralPath $gateScript)) {
        Set-Status "The running release $current predates automatic deployment; install $tag through the elevated procedure." $tag
        exit 0
    }
    $gate = Invoke-ContentNative -FilePath $python -ArgumentList @('-I', $gateScript, '--db', (Join-Path $app 'data\pipeline.db')) `
        -WorkingDirectory $app -TimeoutSeconds 60
    if ($gate.ExitCode -ne 0) {
        $why = $gate.Stdout.Trim()
        try { $why = (($gate.Stdout | ConvertFrom-Json).reasons) -join '; ' } catch { }
        Set-Status "$tag is ready; waiting for production to be idle: $why" $tag
        exit 0
    }
    $backup = Read-ContentJson -Path $backupRecordPath
    if ($backup -and -not $backup.Completed -and (Get-Process -Id $backup.WorkerPid -ErrorAction SilentlyContinue)) {
        Set-Status "$tag is ready; waiting for the backup to finish." $tag
        exit 0
    }
    if ($CheckOnly) { Set-Status "$tag is ready and production is idle (check only; nothing changed)." $tag; exit 0 }

    # The window: production is held from here until the hold is cleared.
    $state = [ordered]@{ phase = 'holding'; tag = $tag; target = $target; previous = $head; previous_tag = $current
                         at = [datetime]::UtcNow.ToString('o') }
    Write-ContentJson -Path $statePath -Value $state
    Set-Status "Installing $tag (was $current)." $tag
    if (-not (Request-Hold -Tag $tag -Commit $target)) {
        Clear-Hold
        Write-ContentJson -Path $statePath -Value ([ordered]@{ phase = 'done'; tag = $tag; at = [datetime]::UtcNow.ToString('o') })
        Write-Event 5011 "Release ${tag}: the pipeline launcher did not confirm the hold within 6 minutes; nothing was changed."
        Set-Status "The launcher did not confirm the hold; nothing changed. Will try again." $tag
        exit 1
    }
    $state = Read-ContentJson -Path $statePath

    $state.phase = 'updating'; Write-ContentJson -Path $statePath -Value $state
    try { $update = Update-ContentTree -Git $git -App $app -Target $target -Python $python }
    catch { Restore-Previous $state "updating the tree failed: $_"; exit 1 }

    $state.phase = 'verifying'; Write-ContentJson -Path $statePath -Value $state
    $since = [datetime]::UtcNow
    Set-Content -LiteralPath $verifyPath -Value $target -Encoding ASCII
    $verdict = Wait-ContentCondition -Minutes 45 -PollSeconds 5 -Condition {
        $s = Get-HoldState
        if ($s -and $s.State -eq 'verified' -and $s.Commit -eq $target -and (ConvertTo-ContentUtc $s.AtUtc) -ge $since) { $s }
    }
    if (-not $verdict) { Restore-Previous $state 'the test suite under the pipeline account gave no result within 45 minutes'; exit 1 }
    if (-not $verdict.Passed) { Restore-Previous $state "on this machine under the pipeline account, $($verdict.Detail)"; exit 1 }

    $state.phase = 'resuming'; Write-ContentJson -Path $statePath -Value $state
    $resumedAt = [datetime]::UtcNow
    Write-Receipt -Tag $tag -Outcome 'deployed' -Previous $current -Running $tag
    Clear-Hold
    $worker = Wait-ContentCondition -Minutes 3 -Condition {
        $r = Read-ContentJson -Path $runRecordPath
        if ($r -and -not $r.Completed -and (ConvertTo-ContentUtc $r.StartedAtUtc) -ge $resumedAt.AddSeconds(-5)) { $r }
    }
    if (-not $worker) { Restore-Previous $state 'the daemon did not start on the new release'; exit 1 }
    Start-Sleep -Seconds 180
    $after = Read-ContentJson -Path $runRecordPath
    if (-not $after -or $after.WorkerPid -ne $worker.WorkerPid -or $after.Completed -or
        -not (Get-Process -Id $worker.WorkerPid -ErrorAction SilentlyContinue)) {
        Restore-Previous $state 'the daemon stopped within 3 minutes of starting on the new release'
        exit 1
    }
    Write-ContentJson -Path $statePath -Value ([ordered]@{ phase = 'done'; tag = $tag; at = [datetime]::UtcNow.ToString('o') })
    $note = if ($update.RequirementsChanged) { ' Requirements were updated.' } else { '' }
    Write-Event 5010 "Release $tag installed (was $current); its suite passed under the pipeline account.$note"
    Set-Status "Installed $tag (was $current).$note" $tag
} catch {
    Write-Log "Deployer error: $_"
    Write-Event 5011 "The release deployer stopped with an error: $_"
    throw
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
