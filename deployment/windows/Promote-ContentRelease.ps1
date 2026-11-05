<#
.SYNOPSIS
Promotes a reviewed application release on the production VM (approved host administration).
.DESCRIPTION
Run once, elevated, on CONTENTCLIENTWI. It implements the documented idle stop/start:

1. Refuses to stop while a run is producing (read-only check of the application database).
2. Stops the pipeline through its launcher's stop file and waits for the worker tree to exit.
3. Writes "stopped" to the hand-off flag, then waits until the operator's SSH session has
   updated the application tree and written "ready <commit>" to the same flag.
4. Removes the stop file, starts the managed pipeline task and confirms a new worker started.
5. Retires the disabled legacy interactive tasks, exporting each definition first.
6. Runs the focused acceptance probe under LAB\rr-pipeline$ through a one-time task and prints
   its summary (the report contains no secrets), then removes that task.

It never reads or changes .env, credentials, identities, ACLs or the backup task, and never
changes the pipeline's launch mode (live since the operator's activation on 2026-10-25).
.PARAMETER PaidVoice
Also synthesize one short licensed voice sample per configured provider (a few dozen characters).
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    # The hand-off flag in the operator's SSH profile, where Update-ContentReleaseTree.ps1 writes it
    # (its default, $HOME\relic-promote.flag): this elevated session may run under another account.
    [Parameter(Mandatory)][string]$HandoffFlag,
    [int]$WaitMinutes = 60,
    [switch]$SkipProbe,
    [switch]$PaidVoice
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Promotion is restricted to CONTENTCLIENTWI.' }
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

$root = 'C:\ProgramData\HNSHL\ContentRuntime'
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
$app = [string]$manifest.app_root
$python = Join-Path $app '.venv\Scripts\python.exe'
$task = 'RelicAndRiddle-Production-Unattended'
$stopFile = Join-Path $root 'control\Pipeline.stop'
$stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')

function Write-Step($text) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $text) }

# 1. Idle check: no run may be producing (read-only).
$check = @'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
producing = db.execute("SELECT COUNT(*) FROM runs WHERE status = 'producing'").fetchone()[0]
print(producing)
'@
# A script file, not "python -c": Windows PowerShell 5.1 strips the double quotes from a native
# command's arguments, which broke the inline code.
$checkFile = Join-Path $env:TEMP "relic-idle-check-$stamp.py"
Set-Content -LiteralPath $checkFile -Value $check -Encoding ASCII
try {
    $output = & $python $checkFile (Join-Path $app 'data\pipeline.db')
    if ($LASTEXITCODE) { throw "The idle check could not read the application database (exit $LASTEXITCODE)." }
} finally {
    Remove-Item -LiteralPath $checkFile -ErrorAction SilentlyContinue
}
$producing = [int]$output
if ($producing -gt 0) { throw "A run is producing ($producing); promote when the pipeline is idle." }
Write-Step 'Pipeline is idle (no run producing).'

# 2. Stop through the launcher's stop file.
if ($PSCmdlet.ShouldProcess($task, 'Stop the pipeline through its stop file')) {
    New-Item -ItemType File -Path $stopFile -Force | Out-Null
    $deadline = (Get-Date).AddMinutes(5)
    while ((Get-ScheduledTask -TaskName $task).State -eq 'Running' -and (Get-Date) -lt $deadline) { Start-Sleep 3 }
    if ((Get-ScheduledTask -TaskName $task).State -eq 'Running') { throw 'The pipeline did not stop within 5 minutes; the stop file is left in place.' }
    Write-Step 'Pipeline stopped.'
}

# 3. Hand-off to the operator's SSH session for the tree update.
if ($PSCmdlet.ShouldProcess($HandoffFlag, 'Wait for the application tree update')) {
    Set-Content -LiteralPath $HandoffFlag -Value "stopped $stamp" -Encoding ASCII
    Write-Step "Waiting up to $WaitMinutes min for 'ready <commit>' in $HandoffFlag ..."
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    $ready = $null
    while ((Get-Date) -lt $deadline) {
        $line = (Get-Content -LiteralPath $HandoffFlag -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($line -match '^ready ([0-9a-f]{7,40})$') { $ready = $Matches[1]; break }
        Start-Sleep 5
    }
    if (-not $ready) { throw 'No ready signal; the pipeline stays stopped (stop file in place) for a manual decision.' }
    Write-Step "Tree updated to $ready."
}

# 4. Restart the managed pipeline and confirm a new worker.
if ($PSCmdlet.ShouldProcess($task, 'Start the pipeline')) {
    $pidFile = Join-Path $root 'logs\Pipeline\Run.pid.json'
    $before = if (Test-Path $pidFile) { (Get-Item $pidFile).LastWriteTimeUtc } else { [datetime]::MinValue }
    Remove-Item -LiteralPath $stopFile -Force
    Start-ScheduledTask -TaskName $task
    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep 5
        if ((Test-Path $pidFile) -and (Get-Item $pidFile).LastWriteTimeUtc -gt $before) { break }
    }
    $state = (Get-ScheduledTask -TaskName $task).State
    $fresh = (Test-Path $pidFile) -and (Get-Item $pidFile).LastWriteTimeUtc -gt $before
    Write-Step "Pipeline task state: $state; new worker recorded: $fresh."
    if (-not $fresh) { Write-Warning 'No new worker record yet; check the pipeline logs before relying on it.' }
}

# 5. Retire the disabled legacy interactive tasks (export first; never an enabled one).
$rollback = Join-Path $root "rollback\legacy-tasks-$stamp"
foreach ($legacy in 'RelicAndRiddle-Production', 'RelicAndRiddle-ProductionBackup') {
    $t = Get-ScheduledTask -TaskName $legacy -ErrorAction SilentlyContinue
    if (-not $t) { Write-Step "$legacy is already retired."; continue }
    if ($t.State -ne 'Disabled') { Write-Warning "$legacy is $($t.State), not Disabled; left in place."; continue }
    if ($PSCmdlet.ShouldProcess($legacy, 'Export and unregister the disabled legacy task')) {
        New-Item -ItemType Directory -Path $rollback -Force | Out-Null
        Export-ScheduledTask -TaskName $legacy | Set-Content -LiteralPath (Join-Path $rollback "$legacy.xml") -Encoding Unicode
        Unregister-ScheduledTask -TaskName $legacy -Confirm:$false
        Write-Step "$legacy retired (definition kept in $rollback)."
    }
}

# 6. Focused acceptance probe under the pipeline identity.
if (-not $SkipProbe -and $PSCmdlet.ShouldProcess('LAB\rr-pipeline$', 'Run the focused acceptance probe')) {
    $report = Join-Path $root "logs\Pipeline\app-probe-$stamp.json"
    $probeTask = "RR-AppProbe-$stamp"
    $flags = '--providers' + $(if ($PaidVoice) { ' --paid-voice' } else { '' })
    $command = "`$env:PATH = '$root\bin;' + `$env:PATH; `$env:RELIC_ENVIRONMENT = 'Production'; " +
               "Set-Location '$app'; & '$python' scripts\runtime_probe.py --app-root '$app' --report '$report' $flags"
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -Command `"$command`""
    $who = New-ScheduledTaskPrincipal -UserId 'LAB\rr-pipeline$' -LogonType Password -RunLevel Limited
    Register-ScheduledTask -TaskName $probeTask -Action $action -Principal $who | Out-Null
    try {
        Start-ScheduledTask -TaskName $probeTask
        $deadline = (Get-Date).AddMinutes(10)
        while (-not (Test-Path $report) -and (Get-Date) -lt $deadline) { Start-Sleep 5 }
        Start-Sleep 3
        if (-not (Test-Path $report)) { throw 'The probe produced no report within 10 minutes.' }
        $r = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        Write-Step "Probe: account $($r.account), session $($r.session_id)"
        foreach ($name in $r.checks.PSObject.Properties.Name) {
            $c = $r.checks.$name
            $passed = if ($null -ne $c.passed) { $c.passed } else { '(info)' }
            Write-Host ("  {0,-22} passed={1}" -f $name, $passed)
        }
        Write-Host "  report: $report"
    } finally {
        Unregister-ScheduledTask -TaskName $probeTask -Confirm:$false -ErrorAction SilentlyContinue
    }
}
Write-Step 'Promotion finished. Production runs live: every approved run is booked and posted.'
