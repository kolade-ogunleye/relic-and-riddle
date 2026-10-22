#Requires -Version 5.1
<#
.SYNOPSIS
Checks production task/process receipts as SYSTEM without application credentials.
.DESCRIPTION
Writes fixed Application events: 5002 on a missed/failed workload, 5005 on health.
Independent of the gMSA tasks; no environment, token or key files are read.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Runtime watch is restricted to CONTENTCLIENTWI.' }
. (Join-Path $PSScriptRoot 'lib/ContentRuntimeHealth.ps1')
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
foreach ($workload in 'Pipeline', 'Backup') {
    try {
        $name = if ($workload -eq 'Pipeline') { 'RelicAndRiddle-Production-Unattended' } else { 'RelicAndRiddle-ProductionBackup-Unattended' }
        $task = Get-ScheduledTask -TaskPath '\' -TaskName $name
        $info = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $name
        $receiptPath = Join-Path $root "logs/$workload/Run.pid.json"
        $receipt = if (Test-Path -LiteralPath $receiptPath) {
            if ((Get-Item -LiteralPath $receiptPath).Length -gt 64KB) { throw 'Oversized runtime receipt.' }
            Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        } else { $null }
        $matchesWorker = $false
        if ($receipt -and -not $receipt.Completed) {
            $process = Get-Process -Id ([int]$receipt.WorkerPid) -ErrorAction SilentlyContinue
            $python = if ($workload -eq 'Pipeline') { Join-Path $manifest.app_root '.venv/Scripts/python.exe' }
            else { Join-Path $manifest.maintenance_root '.venv/Scripts/python.exe' }
            if ($process -and $process.Path -ieq $python -and $process.StartTime.ToUniversalTime().ToString('o') -eq $receipt.StartedAtUtc) {
                $native = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$process.Id)
                $owner = Invoke-CimMethod -InputObject $native -MethodName GetOwnerSid
                $identity = if ($workload -eq 'Pipeline') { 'LAB\rr-pipeline$' } else { 'LAB\rr-backup$' }
                $expectedSid = [Security.Principal.NTAccount]::new($identity).Translate([Security.Principal.SecurityIdentifier]).Value
                $matchesWorker = $owner.Sid -eq $expectedSid
            }
        }
        $paused = Test-Path -LiteralPath (Join-Path $root "control/$workload.stop")
        $health = Get-ContentRuntimeHealth -Workload $workload -TaskState ([string]$task.State) -Receipt $receipt `
            -WorkerMatches $matchesWorker -PauseRequested $paused -LastRunUtc $info.LastRunTime.ToUniversalTime()
    } catch { $health = [pscustomobject]@{ Workload = $workload; Healthy = $false; Reason = 'MonitorEvaluationFailed' } }
    $eventId = if ($health.Healthy) { 5005 } else { 5002 }
    $entryType = if ($health.Healthy) { 'Information' } else { 'Error' }
    Write-EventLog -LogName Application -Source 'HNSHL-ContentRuntime' -EventId $eventId -EntryType $entryType `
        -Message ("{0}: {1}" -f $health.Workload, $health.Reason)
    $health
}
