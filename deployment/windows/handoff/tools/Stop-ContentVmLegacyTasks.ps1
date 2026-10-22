#Requires -Version 5.1
<#
.SYNOPSIS
Validates legacy worker ownership, then optionally stops it at an idle boundary.
.DESCRIPTION
Defaults to inspection. Apply disables legacy schedules and asks the existing
launcher to stop while holding SQLite's write lock, preventing a new producing
run from being committed between the idle check and worker exit. No app provider
or secret file is opened. Original task definitions are retained for rollback.
#>
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Apply, [switch]$StopVerifiedOrphan)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'CONTENTCLIENTWI') { throw 'Legacy cutover is restricted to CONTENTCLIENTWI.' }
$root = Split-Path $PSScriptRoot -Parent
$manifest = Get-Content -LiteralPath (Join-Path $root 'launch.json') -Raw | ConvertFrom-Json
$appRoot = [string]$manifest.app_root
$names = @('RelicAndRiddle-Production', 'RelicAndRiddle-ProductionBackup')
$tasks = @(Get-ScheduledTask -TaskPath '\' -TaskName $names)
if ($tasks.Count -ne 2) { throw 'Both retained legacy tasks must exist.' }
$backup = $tasks | Where-Object TaskName -eq $names[1]
if ([string]$backup.State -eq 'Running') { throw 'Legacy backup is running; wait for it to complete.' }
$pipeline = $tasks | Where-Object TaskName -eq $names[0]
$receipt = Get-Content -LiteralPath (Join-Path $appRoot 'logs/runtime-Production.pid.json') -Raw | ConvertFrom-Json
$worker = Get-Process -Id ([int]$receipt.pid) -ErrorAction SilentlyContinue
if ($worker) {
    $owner = Invoke-CimMethod -InputObject (Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$worker.Id)) -MethodName GetOwnerSid
    $expectedSid = [Security.Principal.NTAccount]::new('LAB\omokolade').Translate([Security.Principal.SecurityIdentifier]).Value
    if ($worker.Path -ine (Join-Path $appRoot '.venv/Scripts/python.exe') -or $owner.Sid -ne $expectedSid -or
        $worker.StartTime.ToUniversalTime() -ne ([datetime]$receipt.started).ToUniversalTime()) {
        throw 'Legacy worker receipt does not match its executable, owner and start time.'
    }
    if ([string]$pipeline.State -ne 'Running' -and -not $StopVerifiedOrphan) {
        throw 'An orphan legacy worker exists; verify it, then explicitly use StopVerifiedOrphan for an idle stop.'
    }
} elseif ([string]$pipeline.State -eq 'Running') { throw 'Running legacy launcher has no matching worker; retry after it settles.' }
if (-not $Apply) {
    [pscustomobject]@{ ReadyForIdleStop = $true; WorkerPresent = [bool]$worker; Applied = $false }
    return
}
if (-not $PSCmdlet.ShouldProcess('Retained content tasks', 'Disable schedules and stop only an idle, verified legacy worker')) { return }
foreach ($task in $tasks) { Disable-ScheduledTask -TaskPath '\' -TaskName $task.TaskName | Out-Null }
if ($worker) {
    # JSON is sent through stdin, never interpolated into executable code.
    $inputJson = @{ app_root = $appRoot; worker_pid = $worker.Id; direct_stop = ([string]$pipeline.State -ne 'Running') } | ConvertTo-Json -Compress
    $program = @'
import ctypes, json, sqlite3, subprocess, sys
from ctypes import wintypes
from pathlib import Path
from contextlib import closing
settings = json.loads(INPUT_JSON)
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
handle = kernel.OpenProcess(0x00100000, False, settings['worker_pid'])
if not handle:
    raise SystemExit(2)
try:
    root = Path(settings['app_root'])
    with closing(sqlite3.connect((root / 'data' / 'pipeline.db').as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
        db.execute('BEGIN IMMEDIATE')
        active = db.execute('SELECT COUNT(*) FROM runs WHERE status=?', ('producing',)).fetchone()[0]
        if active:
            print(json.dumps({'stopped': False, 'active_producing': active}))
            raise SystemExit(3)
        (root / 'logs' / 'runtime-Production.stop').touch(exist_ok=True)
        if settings['direct_stop']:
            import os
            taskkill = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
            subprocess.run([str(taskkill), '/PID', str(settings['worker_pid']), '/T', '/F'],
                           check=True, text=True, capture_output=True, timeout=15)
        if kernel.WaitForSingleObject(handle, 30000) != 0:
            raise SystemExit(4)
        db.rollback()
    print(json.dumps({'stopped': True, 'active_producing': 0}))
finally:
    kernel.CloseHandle(handle)
'@
    # Encode input as a Python string literal using JSON, rather than quoting paths.
    $program = $program.Replace('INPUT_JSON', ($inputJson | ConvertTo-Json -Compress))
    $python = Join-Path $appRoot '.venv/Scripts/python.exe'
    $ErrorActionPreference = 'Continue'
    $result = $program | & $python - 2>$null
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { throw "Idle stop refused or incomplete (code $code); schedules remain disabled. Inspect before rollback or retry." }
    $result | ConvertFrom-Json
}
$deadline = [datetime]::UtcNow.AddSeconds(20)
do {
    $remaining = @(Get-ScheduledTask -TaskPath '\' -TaskName $names | Where-Object { [string]$_.State -eq 'Running' })
    if (-not $remaining.Count) { break }
    Start-Sleep -Seconds 1
} while ([datetime]::UtcNow -lt $deadline)
if ($remaining.Count) { throw 'Legacy launcher has not exited; production cutover refused.' }
[pscustomobject]@{ LegacySchedulesDisabled = $true; Applied = $true }
