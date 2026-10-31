#Requires -Version 5.1
<#
.SYNOPSIS
Runs a content workload under its dedicated identity with explicit dependencies.
.DESCRIPTION
Defaults to an API canary. Run mode starts the pipeline live (since 2026-10-25). A stop
request terminates only this launcher's recorded worker tree. No secret values
are passed on command lines or returned by this launcher. In pipeline run mode a
release hold (control\Pipeline.hold) stops the worker tree, runs the deployer's
verification request under this account and restarts the worker once released.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Pipeline', 'Backup')][string]$Workload,
    [ValidateSet('Canary', 'Run', 'Restore')][string]$Mode = 'Canary',
    [string]$SnapshotId = '',
    [switch]$Validation
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Workload launcher is restricted to CONTENTCLIENTWI.' }
$expected = if ($Workload -eq 'Pipeline') { 'LAB\rr-pipeline$' } else { 'LAB\rr-backup$' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($identity -ine $expected) { throw 'Workload identity mismatch.' }
if ($Mode -eq 'Restore' -and ($Workload -ne 'Backup' -or $SnapshotId -notmatch '^production-\d{8}T\d{6}Z-[a-f0-9]{12}$')) {
    throw 'Restore requires a valid production snapshot identity and the backup account.'
}
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
$appRoot = [string]$manifest.app_root
$maintenance = [string]$manifest.maintenance_root
$python = if ($Workload -eq 'Pipeline') { Join-Path $appRoot '.venv/Scripts/python.exe' }
else { Join-Path $maintenance '.venv/Scripts/python.exe' }
$adapter = Join-Path $PSScriptRoot 'content_runtime.py'
foreach ($path in $python, $adapter, $appRoot) {
    if ($path.Contains('"') -or -not (Test-Path -LiteralPath $path)) { throw 'A required runtime path is invalid or unavailable.' }
}
$env:PATH = (Join-Path $root 'bin') + ';' + $env:PATH
$env:RELIC_ENVIRONMENT = 'Production'
$env:HNSHL_CONTENT_LAUNCHER_PID = [string]$PID
$logs = Join-Path $root "logs/$Workload"
$command = if ($Mode -eq 'Restore') { 'backup-restore' }
elseif ($Workload -eq 'Pipeline' -and $Mode -eq 'Canary') { 'pipeline-check' }
elseif ($Workload -eq 'Pipeline') { 'pipeline' }
elseif ($Mode -eq 'Canary') { 'backup-check' } else { 'backup-cycle' }
$arguments = "-u `"$adapter`" $command"
if ($Mode -eq 'Restore') { $arguments += " --snapshot-id $SnapshotId" }
$stdout = Join-Path $logs "$Mode.stdout.log"
$stderr = Join-Path $logs "$Mode.stderr.log"
$pidPath = Join-Path $logs "$Mode.pid.json"
$stopPath = Join-Path $root "control/$Workload.stop"
if (Test-Path -LiteralPath $stopPath) { exit 0 }
if ($Mode -eq 'Run') {
    . (Join-Path $PSScriptRoot 'lib/ContentProbeProcess.ps1')
    $preflight = if ($Workload -eq 'Pipeline') { 'pipeline-check' } else { 'backup-check' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        try {
            $apiCode = Invoke-ContentProbeProcess -FilePath $python -Arguments "-u `"$adapter`" $preflight" -TimeoutSeconds 45
            $gpuCode = if ($Workload -eq 'Pipeline') {
                Invoke-ContentProbeProcess -FilePath (Join-Path $root 'bin/ffmpeg.exe') `
                    -Arguments '-nostdin -hide_banner -loglevel error -f lavfi -i color=c=black:s=320x240:r=10 -frames:v 2 -c:v h264_nvenc -f null NUL'
            } else { 0 }
            $ready = $apiCode -eq 0 -and $gpuCode -eq 0
        } catch { $ready = $false }
        if ($ready) { break }
        if ($attempt -lt 2) { Start-Sleep -Seconds 10 }
    }
    if (-not $ready) {
        if (-not $Validation) {
            Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId 5001 -EntryType Error `
                -Message "$Workload readiness checks failed after bounded retries."
        }
        throw 'Workload readiness checks failed after bounded retries.'
    }
}
. (Join-Path $PSScriptRoot 'lib/ContentWorkloadProcess.ps1')
# Release deployment (pipeline run only): the deployer's hold stops the worker tree while this
# launcher, and so the managed task, keeps running; see lib/ContentReleaseHold.ps1.
$releaseHold = $Workload -eq 'Pipeline' -and $Mode -eq 'Run'
$holdPath = Join-Path $root 'control/Pipeline.hold'
if ($releaseHold) {
    . (Join-Path $PSScriptRoot 'lib/ContentReleaseHold.ps1')
    $env:RELIC_RELEASE_RECEIPT = Join-Path $root 'control/release.json'
    $env:RELIC_DEPLOY_REQUEST = Join-Path $logs 'deploy.request'   # /deploy in the bot; read by the deployer
    $env:RELIC_DEPLOY_STATUS = Join-Path $root 'logs/Deploy/status.json'
    $holdArguments = @{
        HoldPath = $holdPath; StopPath = $stopPath
        VerifyPath = (Join-Path $root 'control/Pipeline.verify'); StatePath = (Join-Path $logs 'Hold.json')
        Verify = { param($commit) Invoke-ContentReleaseVerification -Python $python -AppRoot $appRoot -LogDirectory $logs -Commit $commit }
        Alert = {
            if (-not $Validation) {
                Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId 5001 -EntryType Error `
                    -Message 'Pipeline held for a release deployment for more than 3 hours; inspect the deployer evidence.'
            }
        }
    }
}
while ($true) {
    if ($releaseHold -and (Test-Path -LiteralPath $holdPath)) {
        if ((Wait-ContentReleaseHold @holdArguments) -eq 'Stopped') { exit 0 }
    }
    $loggedWorker = Start-ContentLoggedProcess -FilePath $python -Arguments $arguments -WorkingDirectory $appRoot `
        -StdoutPath $stdout -StderrPath $stderr
    $worker = $loggedWorker.Process
    $record = [ordered]@{
        Identity = $identity; SessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
        WorkerPid = $worker.Id; StartedAtUtc = $worker.StartTime.ToUniversalTime().ToString('o')
        Mode = $Mode; Workload = $Workload; Completed = $false
        Validation = [bool]$Validation
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
    $started = [datetime]::UtcNow
    $heldForRelease = $false
    try {
        while (-not $worker.WaitForExit(1000)) {
            $timedOut = $Mode -eq 'Canary' -and [datetime]::UtcNow -gt $started.AddSeconds(90)
            $stopRequested = Test-Path -LiteralPath $stopPath
            $holdRequested = $releaseHold -and (Test-Path -LiteralPath $holdPath)
            if ($timedOut -or $stopRequested -or $holdRequested) {
                & "$env:SystemRoot/System32/taskkill.exe" /PID $worker.Id /T /F | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Worker tree did not stop cleanly.' }
                $worker.WaitForExit()
                if (($stopRequested -or $holdRequested) -and -not $timedOut) {
                    $record.Completed = $true
                    if ($stopRequested) { $record.StoppedByOperator = $true } else { $record.HeldForRelease = $true }
                    $record.CompletedAtUtc = [datetime]::UtcNow.ToString('o')
                    $record | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
                    if ($stopRequested) { exit 0 }
                    $heldForRelease = $true
                    break
                }
                throw 'Workload stopped or exceeded the canary deadline.'
            }
        }
        if ($heldForRelease) { continue }
        $code = Complete-ContentLoggedProcess -Worker $loggedWorker
        $record.Completed = $true
        $record.ExitCode = $code
        $record.CompletedAtUtc = [datetime]::UtcNow.ToString('o')
        $record | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
        if ($Mode -eq 'Run' -and -not $Validation) {
            if ($Workload -eq 'Pipeline' -or $code -ne 0) {
                Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId 5001 -EntryType Error `
                    -Message "$Workload worker ended unexpectedly; inspect protected runtime evidence."
                exit 1
            }
            Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId 5000 -EntryType Information `
                -Message 'Backup API cycle completed and its queued jobs are verified.'
        }
        exit $code
    } catch {
        if ($Mode -eq 'Run' -and -not $Validation) {
            Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId 5001 -EntryType Error `
                -Message "$Workload execution failed; inspect protected runtime evidence."
        }
        throw
    } finally { Close-ContentLoggedProcess -Worker $loggedWorker }
}
